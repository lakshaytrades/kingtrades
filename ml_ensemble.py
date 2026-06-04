"""
ml_ensemble.py — 4-Model ML Ensemble with Dynamic Online Learning v2.0

Fuses GBM + RandomForest + ExtraTrees + LogisticRegression into a single
weighted prediction. Model weights update each time a live outcome arrives —
models that have been right recently vote louder.

v22.0 upgrades:
  - Regime-aware model selection: loads bull_models or bear_models based on
    current SPY vs 200-day SMA regime (detected via yfinance fast_info).
  - 8 pretrained models: gbm_bull/bear, rf_bull/bear, et_bull/bear, lr_bull/bear.
  - Regime re-checked every 60 minutes, models swapped automatically if regime flips.
  - Falls back to single model set (legacy .pkl) if regime detection fails.

Why 4 models beat 1:
  - GBM excels at tabular momentum patterns (strong trends)
  - RF excels at noisy environments (ranging markets)
  - ExtraTrees adds orthogonal randomisation (captures outlier setups)
  - LR adds linear baseline (prevents overfitting in thin data)

Online learning: buffer 10 live outcomes → partial-fit LR, retrain others.
Fail-open: returns neutral prediction (0.5, 0.0) on any exception.
"""

import logging
import os
import pickle
import threading
import time as _time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH  = os.path.join(os.path.dirname(__file__), "data", "ml_ensemble_v1.pkl")
_PRETRAINED_DIR = os.path.join(os.path.dirname(__file__), "data", "ml_pretrained")
_RETRAIN_AT  = 10   # retrain when live buffer reaches this size
_ROLL_WINDOW = 20   # rolling window for weight updates
_REGIME_CHECK_INTERVAL = 3600.0  # seconds between regime re-checks (60 min)

FEATURE_NAMES = [
    "rsi", "macd_hist_norm", "volume_ratio", "atr_pct", "signal_score",
    "adx", "ema_slope_pct", "vwap_dist_pct", "bb_pct", "hour_et",
    "is_long", "r2_quality", "mfi_norm", "ofi_5bar", "vix_level_norm",
    "short_ratio_norm", "consecutive_wins",
]

N_FEATURES = len(FEATURE_NAMES)


@dataclass
class EnsemblePrediction:
    win_prob: float        # 0.0-1.0 ensemble win probability
    confidence: float      # 1-std(model_probs); higher = more agreement
    model_agreement: int   # how many of 4 models agree with ensemble direction
    score_delta: float     # score adjustment (+12 strong bull, -5 strong bear, etc.)
    reason: str


def _safe_float(v, default=0.0) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except Exception:
        return default


def _build_x(feat: dict) -> np.ndarray:
    """Extract feature vector in canonical order. Missing keys → 0.0."""
    return np.array([_safe_float(feat.get(k, 0.0)) for k in FEATURE_NAMES],
                    dtype=np.float32).reshape(1, -1)


def _generate_synthetic_data() -> Tuple[np.ndarray, np.ndarray]:
    """
    2200 synthetic training samples built from known edge anchor points.
    Win-probability for each anchor matches documented statistical edges.
    """
    rng = np.random.default_rng(42)
    rows: List[np.ndarray] = []
    labels: List[int] = []

    def add_anchor(base: dict, n: int, win_rate: float, noise: float = 0.08):
        vec = np.array([_safe_float(base.get(k, 0.0)) for k in FEATURE_NAMES], dtype=np.float32)
        for _ in range(n):
            x = vec + rng.normal(0, noise, size=N_FEATURES).astype(np.float32)
            rows.append(x)
            labels.append(1 if rng.random() < win_rate else 0)

    # Anchor 1 — strong bull momentum (RSI mid, high volume, trend aligned)
    add_anchor({"rsi": 54, "macd_hist_norm": 0.35, "volume_ratio": 2.3, "atr_pct": 1.1,
                "signal_score": 78, "adx": 31, "ema_slope_pct": 0.08, "vwap_dist_pct": 0.3,
                "bb_pct": 0.70, "hour_et": 10, "is_long": 1, "r2_quality": 0.68,
                "mfi_norm": 0.65, "ofi_5bar": 0.35, "vix_level_norm": 0.5,
                "short_ratio_norm": 0.3, "consecutive_wins": 2}, 250, 0.68)

    # Anchor 2 — overbought, low conviction long (likely loss)
    add_anchor({"rsi": 79, "macd_hist_norm": 0.05, "volume_ratio": 1.1, "atr_pct": 0.9,
                "signal_score": 65, "adx": 18, "ema_slope_pct": 0.02, "vwap_dist_pct": 1.1,
                "bb_pct": 0.92, "hour_et": 12, "is_long": 1, "r2_quality": 0.28,
                "mfi_norm": 0.80, "ofi_5bar": -0.10, "vix_level_norm": 0.5,
                "short_ratio_norm": 0.2, "consecutive_wins": 0}, 250, 0.32)

    # Anchor 3 — strong trend + high ADX (high conviction)
    add_anchor({"rsi": 58, "macd_hist_norm": 0.45, "volume_ratio": 2.0, "atr_pct": 1.5,
                "signal_score": 80, "adx": 38, "ema_slope_pct": 0.12, "vwap_dist_pct": 0.4,
                "bb_pct": 0.75, "hour_et": 10, "is_long": 1, "r2_quality": 0.78,
                "mfi_norm": 0.70, "ofi_5bar": 0.40, "vix_level_norm": 0.45,
                "short_ratio_norm": 0.4, "consecutive_wins": 3}, 250, 0.72)

    # Anchor 4 — midday chop (high noise)
    add_anchor({"rsi": 52, "macd_hist_norm": 0.02, "volume_ratio": 0.8, "atr_pct": 0.6,
                "signal_score": 64, "adx": 14, "ema_slope_pct": 0.01, "vwap_dist_pct": 0.05,
                "bb_pct": 0.52, "hour_et": 12, "is_long": 1, "r2_quality": 0.22,
                "mfi_norm": 0.50, "ofi_5bar": 0.02, "vix_level_norm": 0.55,
                "short_ratio_norm": 0.2, "consecutive_wins": 0}, 200, 0.38)

    # Anchor 5 — power hour breakout (3-3:30 PM ET)
    add_anchor({"rsi": 61, "macd_hist_norm": 0.30, "volume_ratio": 2.8, "atr_pct": 1.4,
                "signal_score": 76, "adx": 30, "ema_slope_pct": 0.09, "vwap_dist_pct": 0.5,
                "bb_pct": 0.80, "hour_et": 15, "is_long": 1, "r2_quality": 0.62,
                "mfi_norm": 0.68, "ofi_5bar": 0.45, "vix_level_norm": 0.5,
                "short_ratio_norm": 0.3, "consecutive_wins": 1}, 200, 0.65)

    # Anchor 6 — short squeeze candidates
    add_anchor({"rsi": 65, "macd_hist_norm": 0.40, "volume_ratio": 3.2, "atr_pct": 2.1,
                "signal_score": 82, "adx": 28, "ema_slope_pct": 0.15, "vwap_dist_pct": 0.8,
                "bb_pct": 0.85, "hour_et": 10, "is_long": 1, "r2_quality": 0.60,
                "mfi_norm": 0.72, "ofi_5bar": 0.55, "vix_level_norm": 0.45,
                "short_ratio_norm": 0.85, "consecutive_wins": 2}, 150, 0.70)

    # Anchor 7 — crisis/high VIX (avoid)
    add_anchor({"rsi": 38, "macd_hist_norm": -0.40, "volume_ratio": 2.5, "atr_pct": 3.0,
                "signal_score": 64, "adx": 32, "ema_slope_pct": -0.18, "vwap_dist_pct": -1.5,
                "bb_pct": 0.10, "hour_et": 10, "is_long": 1, "r2_quality": 0.60,
                "mfi_norm": 0.25, "ofi_5bar": -0.50, "vix_level_norm": 0.90,
                "short_ratio_norm": 0.3, "consecutive_wins": 0}, 150, 0.28)

    # Anchor 8 — MFI accumulation + OFI aligned
    add_anchor({"rsi": 56, "macd_hist_norm": 0.25, "volume_ratio": 2.0, "atr_pct": 1.2,
                "signal_score": 75, "adx": 26, "ema_slope_pct": 0.07, "vwap_dist_pct": 0.2,
                "bb_pct": 0.65, "hour_et": 11, "is_long": 1, "r2_quality": 0.58,
                "mfi_norm": 0.78, "ofi_5bar": 0.40, "vix_level_norm": 0.48,
                "short_ratio_norm": 0.3, "consecutive_wins": 1}, 200, 0.65)

    # Anchor 9 — opening drive, high conviction
    add_anchor({"rsi": 60, "macd_hist_norm": 0.50, "volume_ratio": 3.5, "atr_pct": 1.8,
                "signal_score": 85, "adx": 35, "ema_slope_pct": 0.20, "vwap_dist_pct": 0.6,
                "bb_pct": 0.82, "hour_et": 9, "is_long": 1, "r2_quality": 0.72,
                "mfi_norm": 0.75, "ofi_5bar": 0.60, "vix_level_norm": 0.42,
                "short_ratio_norm": 0.4, "consecutive_wins": 3}, 200, 0.73)

    # Anchor 10 — ranging market (random)
    add_anchor({"rsi": 50, "macd_hist_norm": 0.00, "volume_ratio": 1.0, "atr_pct": 0.5,
                "signal_score": 63, "adx": 12, "ema_slope_pct": 0.00, "vwap_dist_pct": 0.0,
                "bb_pct": 0.50, "hour_et": 13, "is_long": 1, "r2_quality": 0.15,
                "mfi_norm": 0.50, "ofi_5bar": 0.00, "vix_level_norm": 0.52,
                "short_ratio_norm": 0.2, "consecutive_wins": 0}, 150, 0.43)

    X = np.vstack(rows)
    y = np.array(labels, dtype=np.int32)
    # Shuffle
    idx = rng.permutation(len(y))
    return X[idx], y[idx]


def _detect_spy_regime() -> str:
    """
    Detect current market regime by comparing SPY price to its 200-day SMA.
    Returns "BULL" or "BEAR". Fail-open returns "BULL".
    Uses yfinance fast_info for current price and history for SMA.
    """
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        # Get current price via fast_info
        current_price = float(spy.fast_info.get("lastPrice", 0) or 0)
        if current_price <= 0:
            # Fallback: use recent history
            hist = spy.history(period="5d", auto_adjust=True)
            if not hist.empty:
                current_price = float(hist["Close"].iloc[-1])
        if current_price <= 0:
            logger.debug("ml_ensemble: SPY price unavailable — defaulting to BULL regime")
            return "BULL"
        # Get 200-day history for SMA
        hist200 = spy.history(period="300d", auto_adjust=True)
        if len(hist200) < 200:
            logger.debug("ml_ensemble: insufficient SPY history for SMA200 — defaulting to BULL")
            return "BULL"
        sma200 = float(hist200["Close"].tail(200).mean())
        regime = "BULL" if current_price >= sma200 else "BEAR"
        logger.info(
            f"ml_ensemble: SPY regime={regime} "
            f"(price=${current_price:.2f} vs SMA200=${sma200:.2f})"
        )
        return regime
    except Exception as e:
        logger.debug(f"ml_ensemble: regime detection fail-open ({e}) → BULL")
        return "BULL"


class MLEnsemble:
    """4-model weighted ensemble with online weight adaptation and regime-aware model selection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._models: dict = {}
        self._weights: Dict[str, float] = {}
        self._model_history: Dict[str, List[int]] = {}  # 1=correct, 0=wrong
        self._live_X: List[np.ndarray] = []
        self._live_y: List[int] = []
        self._n_live: int = 0
        self._loaded = False
        # Regime tracking
        self._current_regime: str = "BULL"
        self._last_regime_check: float = 0.0
        self._load_or_init()

    # ── model setup ──────────────────────────────────────────────────────────

    def _build_models(self):
        from sklearn.ensemble import (
            GradientBoostingClassifier, RandomForestClassifier,
            ExtraTreesClassifier,
        )
        from sklearn.linear_model import LogisticRegression
        self._models = {
            "gbm": GradientBoostingClassifier(
                n_estimators=120, max_depth=4, learning_rate=0.05,
                subsample=0.8, random_state=42,
            ),
            "rf": RandomForestClassifier(
                n_estimators=120, max_depth=7, min_samples_split=8,
                random_state=42, n_jobs=-1,
            ),
            "et": ExtraTreesClassifier(
                n_estimators=120, max_depth=8, min_samples_split=6,
                random_state=43, n_jobs=-1,
            ),
            "lr": LogisticRegression(
                max_iter=600, C=1.0, random_state=42,
            ),
        }
        self._weights = {k: 1.0 for k in self._models}
        self._model_history = {k: [] for k in self._models}

    def _train_all(self, X: np.ndarray, y: np.ndarray):
        for name, model in self._models.items():
            try:
                model.fit(X, y)
            except Exception as e:
                logger.warning(f"ml_ensemble: failed to train {name}: {e}")

    def _load_pretrained_regime(self, regime: str) -> bool:
        """
        Load regime-specific models (bull or bear) from data/ml_pretrained/.
        Tries gbm_{regime}.pkl, rf_{regime}.pkl, et_{regime}.pkl, lr_{regime}.pkl.
        Returns True if all 4 regime model files were loaded successfully.
        """
        regime_lower = regime.lower()
        required = {"gbm", "rf", "et", "lr"}
        try:
            if not os.path.isdir(_PRETRAINED_DIR):
                return False
            loaded: dict = {}
            for name in required:
                path = os.path.join(_PRETRAINED_DIR, f"{name}_{regime_lower}.pkl")
                if not os.path.exists(path):
                    logger.debug(f"ml_ensemble: pretrained/{name}_{regime_lower}.pkl not found")
                    return False
                with open(path, "rb") as f:
                    payload = pickle.load(f)
                loaded[f"{name}_{regime_lower}"] = payload["model"]
            self._models = loaded
            self._weights = {k: 1.0 for k in self._models}
            self._model_history = {k: [] for k in self._models}
            self._current_regime = regime
            logger.info(
                f"ml_ensemble: loaded 4 {regime} regime models from {_PRETRAINED_DIR} "
                f"(10yr history, regime-aware v22.0)"
            )
            return True
        except Exception as e:
            logger.warning(f"ml_ensemble: {regime} regime model load failed ({e})")
            return False

    def _load_pretrained(self) -> bool:
        """
        Load models pre-trained by pretrain_ml.py from data/ml_pretrained/.
        v22.0: First tries regime-aware models (bull/bear), then falls back to
        legacy single-set models. Returns True if any models were loaded.
        The pre-trained models use real 10yr OHLCV history — far superior to
        the synthetic anchor-point fallback used on a cold start.
        """
        required = {"gbm", "rf", "et", "lr"}
        try:
            if not os.path.isdir(_PRETRAINED_DIR):
                return False

            # Detect current regime for initial model selection
            detected_regime = _detect_spy_regime()
            self._current_regime = detected_regime
            self._last_regime_check = _time.time()

            # Priority 1: Load regime-specific models
            if self._load_pretrained_regime(detected_regime):
                return True

            # Priority 2: Fall back to legacy (non-regime) single model set
            loaded: dict = {}
            for name in required:
                path = os.path.join(_PRETRAINED_DIR, f"{name}.pkl")
                if not os.path.exists(path):
                    logger.debug(f"ml_ensemble: pretrained/{name}.pkl not found")
                    return False
                with open(path, "rb") as f:
                    payload = pickle.load(f)
                loaded[name] = payload["model"]
            self._models = loaded
            self._weights = {k: 1.0 for k in self._models}
            self._model_history = {k: [] for k in self._models}
            logger.info(
                f"ml_ensemble: loaded 4 legacy pre-trained models from {_PRETRAINED_DIR} "
                "(regime-specific models not found — run pretrain_ml.py to generate them)"
            )
            return True
        except Exception as e:
            logger.warning(f"ml_ensemble: pretrained load failed ({e}), falling back")
            return False

    def _check_and_swap_regime(self) -> None:
        """
        Check if regime has changed (every 60 min).
        If SPY flipped from BULL→BEAR or BEAR→BULL, swap to corresponding model set.
        Fail-open: keeps current models on any error.
        """
        try:
            now = _time.time()
            if now - self._last_regime_check < _REGIME_CHECK_INTERVAL:
                return
            self._last_regime_check = now
            new_regime = _detect_spy_regime()
            if new_regime == self._current_regime:
                logger.debug(f"ml_ensemble: regime check — still {self._current_regime}")
                return
            # Regime has flipped — try to swap models
            logger.info(
                f"ml_ensemble: REGIME FLIP {self._current_regime} → {new_regime} "
                "— swapping model set"
            )
            if self._load_pretrained_regime(new_regime):
                logger.info(f"ml_ensemble: now using {new_regime} models")
            else:
                logger.warning(
                    f"ml_ensemble: {new_regime} models not found — continuing with "
                    f"{self._current_regime} models (run pretrain_ml.py to fix)"
                )
        except Exception as e:
            logger.debug(f"ml_ensemble: regime swap suppressed: {e}")

    def _load_or_init(self):
        os.makedirs("data", exist_ok=True)
        # Priority 1: load live-adapted ensemble (has online learning history)
        try:
            if os.path.exists(_MODEL_PATH):
                with open(_MODEL_PATH, "rb") as f:
                    state = pickle.load(f)
                self._models   = state["models"]
                self._weights  = state.get("weights", {k: 1.0 for k in self._models})
                self._model_history = state.get("history", {k: [] for k in self._models})
                self._loaded   = True
                logger.info("ml_ensemble: loaded existing ensemble from disk")
                return
        except Exception as e:
            logger.warning(f"ml_ensemble: could not load model ({e}), retraining")
        # Priority 2: load pre-trained models from pretrain_ml.py output
        if self._load_pretrained():
            self._save()   # persist as live ensemble so next restart is fast
            self._loaded = True
            return
        # Priority 3: fall back to synthetic anchor-point training
        self._build_models()
        X, y = _generate_synthetic_data()
        self._train_all(X, y)
        self._save()
        self._loaded = True
        logger.info(f"ml_ensemble: trained on {len(y)} synthetic samples")

    def _save(self):
        try:
            state = {
                "models": self._models,
                "weights": self._weights,
                "history": self._model_history,
            }
            with open(_MODEL_PATH, "wb") as f:
                pickle.dump(state, f)
        except Exception as e:
            logger.debug(f"ml_ensemble: save failed: {e}")

    # ── prediction ────────────────────────────────────────────────────────────

    def predict(self, features: dict) -> EnsemblePrediction:
        try:
            # Regime re-check every 60 min (non-blocking — swaps model set in-place)
            self._check_and_swap_regime()
            x = _build_x(features)
            probs: Dict[str, float] = {}
            for name, model in self._models.items():
                try:
                    p = float(model.predict_proba(x)[0][1])
                    probs[name] = p
                except Exception:
                    probs[name] = 0.5

            # Weighted average
            total_w = sum(self._weights.get(k, 1.0) for k in probs)
            if total_w == 0:
                total_w = 1.0
            ensemble_prob = sum(
                probs[k] * self._weights.get(k, 1.0) for k in probs
            ) / total_w

            prob_values = list(probs.values())
            confidence = 1.0 - float(np.std(prob_values))
            model_agreement = sum(
                1 for p in prob_values
                if (p > 0.5) == (ensemble_prob > 0.5)
            )

            # Score delta
            _regime_tag = f"[{self._current_regime}]"
            if ensemble_prob >= 0.75 and model_agreement >= 3:
                delta = 12.0
                reason = f"ensemble:bull_consensus p={ensemble_prob:.2f} agree={model_agreement}/4 {_regime_tag}"
            elif ensemble_prob >= 0.68 and model_agreement >= 2:
                delta = 7.0
                reason = f"ensemble:bull_majority p={ensemble_prob:.2f} {_regime_tag}"
            elif ensemble_prob >= 0.58:
                delta = 3.0
                reason = f"ensemble:slight_bull p={ensemble_prob:.2f} {_regime_tag}"
            elif ensemble_prob <= 0.30 and model_agreement >= 3:
                delta = -8.0
                reason = f"ensemble:bear_consensus p={ensemble_prob:.2f} {_regime_tag}"
            elif ensemble_prob <= 0.40:
                delta = -5.0
                reason = f"ensemble:bear_lean p={ensemble_prob:.2f} {_regime_tag}"
            else:
                delta = 0.0
                reason = f"ensemble:neutral p={ensemble_prob:.2f} {_regime_tag}"

            return EnsemblePrediction(
                win_prob=ensemble_prob,
                confidence=max(0.0, confidence),
                model_agreement=model_agreement,
                score_delta=delta,
                reason=reason,
            )
        except Exception as e:
            logger.debug(f"ml_ensemble.predict suppressed: {e}")
            return EnsemblePrediction(0.5, 0.0, 0, 0.0, "error")

    # ── online learning ───────────────────────────────────────────────────────

    def record_outcome(self, features: dict, was_win: bool) -> None:
        """Record live trade outcome, update model weights, retrain when buffer full."""
        try:
            with self._lock:
                x_vec = _build_x(features).flatten()
                label  = 1 if was_win else 0
                self._live_X.append(x_vec)
                self._live_y.append(label)
                self._n_live += 1

                # Update per-model rolling accuracy
                for name, model in self._models.items():
                    try:
                        pred = int(model.predict(_build_x(features))[0])
                        correct = int(pred == label)
                        hist = self._model_history.setdefault(name, [])
                        hist.append(correct)
                        if len(hist) > _ROLL_WINDOW:
                            hist.pop(0)
                        # Update weight: rolling accuracy (min 0.5)
                        if len(hist) >= 3:
                            acc = sum(hist) / len(hist)
                            self._weights[name] = max(0.5, acc * 2.0)
                    except Exception:
                        pass

                if self._n_live >= _RETRAIN_AT:
                    self._retrain_live()
        except Exception as e:
            logger.debug(f"ml_ensemble.record_outcome suppressed: {e}")

    def _retrain_live(self):
        try:
            X_syn, y_syn = _generate_synthetic_data()
            X_live = np.vstack(self._live_X)
            y_live = np.array(self._live_y, dtype=np.int32)
            # Oversample live 5× to give it more weight
            X_combined = np.vstack([X_syn] + [X_live] * 5)
            y_combined = np.concatenate([y_syn] + [y_live] * 5)
            idx = np.random.permutation(len(y_combined))
            self._train_all(X_combined[idx], y_combined[idx])
            # Keep last 5 live samples as seed for next cycle
            self._live_X  = self._live_X[-5:]
            self._live_y  = self._live_y[-5:]
            self._n_live  = 0
            self._save()
            logger.info(f"ml_ensemble: retrained on {len(y_combined)} samples (live+synthetic)")
        except Exception as e:
            logger.warning(f"ml_ensemble._retrain_live: {e}")


# ── Module singleton ──────────────────────────────────────────────────────────

_ensemble: Optional[MLEnsemble] = None
_ensemble_lock = threading.Lock()


def _get_ensemble() -> MLEnsemble:
    global _ensemble
    if _ensemble is None:
        with _ensemble_lock:
            if _ensemble is None:
                _ensemble = MLEnsemble()
    return _ensemble


def get_ensemble_prediction(features: dict) -> EnsemblePrediction:
    """Public API — safe wrapper around the singleton."""
    try:
        return _get_ensemble().predict(features)
    except Exception as e:
        logger.debug(f"get_ensemble_prediction suppressed: {e}")
        return EnsemblePrediction(0.5, 0.0, 0, 0.0, "error")


def record_trade_outcome_ensemble(features: dict, was_win: bool) -> None:
    """Call after every closed trade to update model weights."""
    try:
        _get_ensemble().record_outcome(features, was_win)
    except Exception as e:
        logger.debug(f"record_trade_outcome_ensemble suppressed: {e}")
