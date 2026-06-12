"""
ml_signal_india.py — ML Ensemble Signal Scorer (God Mode)
Tries LightGBM → XGBoost → LogisticRegression (in order).
Trains on labeled trade outcomes. Returns (score_delta, reason).
Score: -10 to +15. Fail-open: (0.0, "") until 30 trades collected.
Online learning: retrains every 10 new samples.
"""
import json
import logging
import pickle
import time as _time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("ml_signal_india")

_MODEL_PATH   = Path(__file__).parent.parent / "logs" / "india_ml_model.pkl"
_DATA_PATH    = Path(__file__).parent.parent / "logs" / "india_ml_training.jsonl"
_MIN_SAMPLES  = 15
_model        = None
_model_loaded = False
_vix_history: list = []
_trade_count_since_fi_log = 0


# ── Feature extraction ────────────────────────────────────────────────────────

FEATURE_NAMES = [
    "rsi", "macd_hist", "adx", "atr_pct", "vwap_gap_pct",
    "bb_width_pct", "ema_gap_pct", "vol_ratio",
    "tod_hour", "tod_minute_bin", "direction_enc", "is_power_hour",
    "close_vs_open_pct", "session_wins_capped", "session_losses_capped", "vix_level",
    # New features (God Mode):
    "time_of_day_slot", "breadth_pct", "sector_momentum_score",
    "vix_percentile", "ema_ribbon_alignment", "vol_profile_position",
    "global_bias_score", "consecutive_bars_direction",
]


def _extract_features(
    symbol: str,
    ind,
    df_5m,
    direction: str,
    session_wins: int = 0,
    session_losses: int = 0,
    breadth_pct: float = 0.5,
    sector_momentum: float = 0.0,
    global_bias_score: float = 0.0,
    vol_profile_position: float = 0.0,
) -> Optional[np.ndarray]:
    """
    Return 24-element float32 feature vector (16 original + 8 new God Mode features).
    Handles ind=None and df_5m=None gracefully — uses safe defaults.
    """
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        now_ist = datetime.now(IST)

        # ── Indicator-based features ──────────────────────────────────────────
        rsi       = 50.0
        macd_hist = 0.0
        adx       = 20.0
        atr_pct   = 0.01
        vwap_gap  = 0.0
        bb_width  = 0.02
        ema_gap   = 0.0
        vol_ratio = 1.0
        close_vs_open = 0.0

        if ind is not None:
            try:
                rsi = float(getattr(ind, "rsi", 50) or 50)
            except Exception:
                pass
            try:
                mh = getattr(ind, "macd_hist", None)
                if mh is not None:
                    macd_hist = float(mh)
            except Exception:
                pass
            try:
                a = getattr(ind, "adx", None)
                if a is not None:
                    adx = float(a)
            except Exception:
                pass

        if df_5m is not None and not df_5m.empty and len(df_5m) >= 2:
            try:
                close = float(df_5m["close"].iloc[-1])
                if close > 0:
                    if ind is not None:
                        atr_val = getattr(ind, "atr", None)
                        if atr_val and float(atr_val) > 0:
                            atr_pct = float(atr_val) / close

                        vwap_val = getattr(ind, "vwap", None)
                        if vwap_val and float(vwap_val) > 0:
                            vwap_gap = (close - float(vwap_val)) / float(vwap_val)

                        bbu = getattr(ind, "bb_upper", None)
                        bbl = getattr(ind, "bb_lower", None)
                        if bbu and bbl and float(bbu) > 0:
                            bb_width = (float(bbu) - float(bbl)) / close

                        e9  = getattr(ind, "ema9",  None)
                        e21 = getattr(ind, "ema21", None)
                        if e9 and e21 and float(e21) > 0:
                            ema_gap = (float(e9) - float(e21)) / float(e21)

                    # Volume ratio
                    if "volume" in df_5m.columns and len(df_5m) >= 20:
                        vol_avg = float(df_5m["volume"].rolling(20).mean().iloc[-1])
                        vol_now = float(df_5m["volume"].iloc[-1])
                        if vol_avg > 0:
                            vol_ratio = vol_now / vol_avg

                    # Close vs open of last bar
                    o = float(df_5m["open"].iloc[-1])
                    rng = float(df_5m["high"].iloc[-1]) - float(df_5m["low"].iloc[-1])
                    if rng > 0:
                        close_vs_open = (close - o) / rng

            except Exception:
                pass

        # ── Time-based features ───────────────────────────────────────────────
        tod_hour       = float(now_ist.hour)
        tod_minute_bin = float(now_ist.minute // 15)  # 0-3 bins per hour

        # Power hour: 9:15-10:00 IST or 14:30-15:20 IST
        h, m = now_ist.hour, now_ist.minute
        is_power_hour = float(
            (h == 9 and m >= 15) or (h == 10 and m == 0) or
            (h == 14 and m >= 30) or (h == 15 and m <= 20)
        )

        # ── Direction encoding ─────────────────────────────────────────────────
        direction_enc = 1.0 if direction == "LONG" else -1.0

        # ── Session stats ─────────────────────────────────────────────────────
        session_wins_capped   = float(min(session_wins,   10))
        session_losses_capped = float(min(session_losses, 10))

        # ── VIX level ─────────────────────────────────────────────────────────
        vix_level = 0.0
        try:
            from data_fetch_dhan import get_india_vix
            v = get_india_vix()
            if v:
                vix_level = float(v)
        except Exception:
            pass

        # ── God Mode extra features ───────────────────────────────────────────
        # time_of_day_slot: 0=ORB, 1=morning, 2=midday, 3=afternoon, 4=power_close
        h, m = now_ist.hour, now_ist.minute
        if h == 9 and m < 45:
            tod_slot = 0.0
        elif (h == 9 and m >= 45) or h == 10 or h == 11:
            tod_slot = 1.0
        elif (h == 11 and m >= 30) or h == 12 or (h == 13 and m < 30):
            tod_slot = 2.0
        elif (h == 13 and m >= 30) or h == 14:
            tod_slot = 3.0
        else:
            tod_slot = 4.0

        # EMA ribbon alignment
        ema_ribbon = 0.0
        if ind is not None:
            try:
                e9  = float(getattr(ind, "ema9",  0) or 0)
                e21 = float(getattr(ind, "ema21", 0) or 0)
                e50 = float(getattr(ind, "ema50", 0) or 0)
                if e9 > 0 and e21 > 0 and e50 > 0:
                    if e9 > e21 > e50:
                        ema_ribbon = 1.0
                    elif e9 < e21 < e50:
                        ema_ribbon = -1.0
            except Exception:
                pass

        # VIX percentile (simple cache — uses module-level list)
        vix_pct = 50.0
        try:
            if vix_level > 0:
                _vix_history.append(vix_level)
                if len(_vix_history) > 30:
                    _vix_history.pop(0)
                if len(_vix_history) >= 3:
                    vix_pct = float(
                        sum(1 for v in _vix_history if v <= vix_level) / len(_vix_history) * 100
                    )
        except Exception:
            pass

        # Consecutive bars direction
        consec = 0.0
        if df_5m is not None and len(df_5m) >= 3:
            try:
                closes = list(df_5m["close"].tail(5))
                streak = 0
                for i in range(len(closes) - 1, 0, -1):
                    if closes[i] > closes[i - 1]:
                        if streak >= 0:
                            streak += 1
                        else:
                            break
                    elif closes[i] < closes[i - 1]:
                        if streak <= 0:
                            streak -= 1
                        else:
                            break
                    else:
                        break
                consec = float(streak)
            except Exception:
                pass

        # ── Assemble feature vector ───────────────────────────────────────────
        features = np.array([
            rsi,
            macd_hist,
            adx,
            atr_pct,
            vwap_gap,
            bb_width,
            ema_gap,
            vol_ratio,
            tod_hour,
            tod_minute_bin,
            direction_enc,
            is_power_hour,
            close_vs_open,
            session_wins_capped,
            session_losses_capped,
            vix_level,
            # God Mode features
            tod_slot,
            float(breadth_pct),
            float(sector_momentum),
            vix_pct,
            ema_ribbon,
            float(vol_profile_position),
            float(global_bias_score),
            consec,
        ], dtype=np.float32)

        # Replace any NaN/Inf with 0
        features = np.where(np.isfinite(features), features, 0.0).astype(np.float32)
        return features

    except Exception as e:
        logger.debug(f"_extract_features({symbol}): {e}")
        return None


# ── Model loading ─────────────────────────────────────────────────────────────

def _get_model():
    """Load model from pickle if exists; cache in module-level _model. Returns None if unavailable."""
    global _model, _model_loaded
    if _model_loaded:
        return _model
    _model_loaded = True
    try:
        if _MODEL_PATH.exists():
            with open(_MODEL_PATH, "rb") as f:
                _model = pickle.load(f)
            logger.info(f"ML model loaded from {_MODEL_PATH} ({type(_model).__name__})")
        else:
            _model = None
    except Exception as e:
        logger.debug(f"_get_model: {e}")
        _model = None
    return _model


# ── Training ──────────────────────────────────────────────────────────────────

def _retrain_if_ready():
    """Load all JSONL training records. If >= _MIN_SAMPLES, train cascade and save model."""
    global _model, _model_loaded
    try:
        if not _DATA_PATH.exists():
            return

        records = []
        with open(_DATA_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue

        if len(records) < _MIN_SAMPLES:
            logger.debug(f"ML: {len(records)}/{_MIN_SAMPLES} samples — not training yet")
            return

        X = np.array([r["features"] for r in records], dtype=np.float32)
        y = np.array([int(r["label"]) for r in records], dtype=np.int32)

        # Validate shapes — pad/trim features if vector length changed
        if X.shape[0] != y.shape[0] or X.shape[0] < _MIN_SAMPLES:
            return
        expected_features = len(FEATURE_NAMES)
        if X.shape[1] < expected_features:
            pad = np.zeros((X.shape[0], expected_features - X.shape[1]), dtype=np.float32)
            X = np.hstack([X, pad])
        elif X.shape[1] > expected_features:
            X = X[:, :expected_features]

        # Recent 20 trades get 2x weight
        n = len(records)
        weights = np.ones(n, dtype=np.float32)
        if n > 20:
            weights[n - 20:] = 2.0

        trained_model = None

        # ── Cascade: LightGBM → XGBoost → LogisticRegression ─────────────────
        try:
            import lightgbm as lgb
            trained_model = lgb.LGBMClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.05,
                random_state=42,
                verbose=-1,
            )
            trained_model.fit(X, y, sample_weight=weights)
            logger.info(f"ML: trained LightGBM on {len(records)} samples")
        except Exception as lgb_err:
            logger.debug(f"LightGBM failed ({lgb_err}), trying XGBoost")
            try:
                from xgboost import XGBClassifier
                trained_model = XGBClassifier(
                    n_estimators=100,
                    max_depth=4,
                    learning_rate=0.05,
                    use_label_encoder=False,
                    eval_metric="logloss",
                    random_state=42,
                    verbosity=0,
                )
                trained_model.fit(X, y, sample_weight=weights)
                logger.info(f"ML: trained XGBoost on {len(records)} samples")
            except Exception as xgb_err:
                logger.debug(f"XGBoost failed ({xgb_err}), falling back to LogisticRegression")
                try:
                    from sklearn.pipeline import Pipeline
                    from sklearn.preprocessing import StandardScaler
                    from sklearn.linear_model import LogisticRegression
                    trained_model = Pipeline([
                        ("sc", StandardScaler()),
                        ("lr", LogisticRegression(max_iter=500, random_state=42)),
                    ])
                    trained_model.fit(X, y)
                    logger.info(f"ML: trained LogisticRegression on {len(records)} samples")
                except Exception as lr_err:
                    logger.debug(f"LogisticRegression failed: {lr_err}")
                    return

        if trained_model is None:
            return

        # Save model
        _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump(trained_model, f)

        _model = trained_model
        _model_loaded = True
        logger.info(f"ML model saved to {_MODEL_PATH}")

        # Feature importance logging every 50 trades
        try:
            fi = getattr(trained_model, "feature_importances_", None)
            if fi is not None and len(fi) == len(FEATURE_NAMES):
                ranked = sorted(zip(FEATURE_NAMES, fi), key=lambda x: x[1], reverse=True)[:5]
                logger.info("ML top features: " + ", ".join(f"{n}={v:.3f}" for n, v in ranked))
        except Exception:
            pass

    except Exception as e:
        logger.debug(f"_retrain_if_ready: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def get_ml_score_delta(
    symbol: str,
    ind,
    df_5m,
    direction: str,
    session_wins: int = 0,
    session_losses: int = 0,
) -> Tuple[float, str]:
    """
    Return (score_delta, reason_string).
    Fail-open: returns (0.0, "") if model not ready or features unavailable.

    Probability thresholds:
        >= 0.72 → +15 (strong bullish ML signal)
        >= 0.62 → +8
        >= 0.55 → +3
        <= 0.35 → -10 (strong bearish ML signal)
        <= 0.45 → -4
        else    →  0
    """
    try:
        model = _get_model()
        if model is None:
            return 0.0, ""

        features = _extract_features(symbol, ind, df_5m, direction, session_wins, session_losses)
        if features is None:
            return 0.0, ""

        proba = model.predict_proba(features.reshape(1, -1))[0][1]

        if proba >= 0.72:
            return 15.0, f"ml_proba={proba:.2f}"
        if proba >= 0.62:
            return 8.0, f"ml_proba={proba:.2f}"
        if proba >= 0.55:
            return 3.0, f"ml_proba={proba:.2f}"
        if proba <= 0.35:
            return -10.0, f"ml_proba={proba:.2f}"
        if proba <= 0.45:
            return -4.0, f"ml_proba={proba:.2f}"
        return 0.0, f"ml_proba={proba:.2f}"

    except Exception as e:
        logger.debug(f"get_ml_score_delta({symbol}): {e}")
        return 0.0, ""


def record_trade_outcome(
    symbol: str,
    ind,
    df_5m,
    direction: str,
    won: bool,
    pnl_pct: float,
    session_wins: int = 0,
    session_losses: int = 0,
):
    """
    Save a trade outcome record to the JSONL training file.
    Triggers retraining every 10 new samples (modulo check).
    Handles ind=None and df_5m=None gracefully.
    """
    try:
        features = _extract_features(symbol, ind, df_5m, direction, session_wins, session_losses)
        if features is None:
            features = np.zeros(len(FEATURE_NAMES), dtype=np.float32)
        # Ensure consistent vector length
        if len(features) < len(FEATURE_NAMES):
            pad = np.zeros(len(FEATURE_NAMES) - len(features), dtype=np.float32)
            features = np.concatenate([features, pad])

        record = {
            "symbol":   symbol,
            "features": features.tolist(),
            "label":    1 if won else 0,
            "pnl_pct":  float(pnl_pct),
            "direction": direction,
            "ts":       _time.time(),
        }

        _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_DATA_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")

        # Count lines and retrain every 10 new samples
        try:
            with open(_DATA_PATH, "r") as f:
                n_records = sum(1 for _ in f)
            if n_records >= _MIN_SAMPLES and n_records % 10 == 0:
                _retrain_if_ready()
        except Exception:
            pass

    except Exception as e:
        logger.debug(f"record_trade_outcome({symbol}): {e}")
