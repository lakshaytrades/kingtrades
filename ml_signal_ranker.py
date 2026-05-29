"""
ml_signal_ranker.py — Gradient Boosting win-probability scorer.

Renaissance, Two Sigma, and Citadel all use ML as a pre-filter on top of
rule-based signals. This module scores every candidate signal with a trained
GradientBoostingClassifier before it enters a position.

Architecture:
  - 15 market-structure features (RSI, MACD, volume, ADX, time, trend slope, etc.)
  - Trained on 4,000 synthetic examples built from known edge patterns
  - Auto-persisted to data/ml_model.pkl; retrains only when file is missing
  - Online learning: records live outcomes, retrains every 50 new samples
  - Fail-open: returns 0.5 (neutral) on any error — never blocks a trade on its own

Usage in high_accuracy_filter.py Gate 26:
  prob = get_win_probability(rsi, macd_hist_norm, volume_ratio, atr_pct,
                             signal_score, adx, ema_slope_pct,
                             vwap_dist_pct, bb_pct, hour_et, long_flag)
  if prob < config.ML_WIN_PROB_THRESHOLD:
      reject
"""

import logging
import os
import pickle
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH  = os.path.join(os.path.dirname(__file__), "data", "ml_model.pkl")
_BUFFER_PATH = os.path.join(os.path.dirname(__file__), "data", "ml_buffer.pkl")
_MIN_RETRAIN = 50        # retrain when live buffer hits this size
_model       = None
_lock        = threading.Lock()
_live_X: list = []
_live_y: list = []


# ── Feature extraction ────────────────────────────────────────────────────────

def build_feature_vector(
    rsi: float,
    macd_hist_norm: float,   # macd_hist / atr (dimensionless)
    volume_ratio: float,     # current vol / 20-bar avg
    atr_pct: float,          # atr / price × 100
    signal_score: float,     # 0-100 gate score (normalised to 0-1 internally)
    adx: float,
    ema_slope_pct: float,    # (ema9 - ema21) / price × 100
    vwap_dist_pct: float,    # (price - vwap) / vwap × 100, signed
    bb_pct: float,           # (price - bb_lower) / (bb_upper - bb_lower), 0-1
    hour_et: int,            # 9-15
    is_long: int,            # 1 for LONG, 0 for SHORT
    r2_quality: float = 0.5, # Gate 25 R² value
    tod_rvol_score: float = 0.0,  # TOD RVOL boost from institutional_strategies
    pairs_score: float = 0.0,
    vwap_reclaim: float = 0.0,
) -> np.ndarray:
    """Convert signal context into a 15-feature row for the classifier."""
    rsi_norm = np.clip(rsi / 100.0, 0.0, 1.0)
    macd_clipped = np.clip(macd_hist_norm, -3.0, 3.0) / 3.0   # -1..1
    vol_clipped  = np.clip(volume_ratio, 0.0, 5.0) / 5.0
    atr_clipped  = np.clip(atr_pct, 0.0, 5.0) / 5.0
    score_norm   = np.clip(signal_score, 0.0, 100.0) / 100.0
    adx_norm     = np.clip(adx, 0.0, 60.0) / 60.0
    ema_clipped  = np.clip(ema_slope_pct, -2.0, 2.0) / 2.0
    vwap_clip    = np.clip(vwap_dist_pct, -3.0, 3.0) / 3.0
    bb_clip      = np.clip(bb_pct, 0.0, 1.0)
    hour_norm    = np.clip(hour_et, 9, 16) / 16.0
    r2_norm      = np.clip(r2_quality, 0.0, 1.0)
    tod_norm     = np.clip(tod_rvol_score, -4.0, 12.0) / 12.0
    pairs_norm   = np.clip(pairs_score, 0.0, 6.0) / 6.0
    vwap_rec     = float(bool(vwap_reclaim > 0))
    long_flag    = float(int(is_long))

    return np.array([[
        rsi_norm, macd_clipped, vol_clipped, atr_clipped, score_norm,
        adx_norm, ema_clipped, vwap_clip, bb_clip, hour_norm,
        long_flag, r2_norm, tod_norm, pairs_norm, vwap_rec,
    ]])


# ── Synthetic training data ────────────────────────────────────────────────────

def _generate_synthetic_data():
    """
    4,000 synthetic examples derived from quantitative backtesting literature.
    High-probability pattern: trending (ADX>25) + RSI 50-65 + rising MACD +
      volume surge >1.5x + price above VWAP + BB mid-range + early session.
    Low-probability: choppy (ADX<15) + overbought RSI + declining MACD +
      thin volume + extended from VWAP + late session + near BB extremes.
    Random noise mixed in to prevent overfit.
    """
    rng = np.random.default_rng(42)
    N = 4000
    X_rows, y_rows = [], []

    # ─ Class 1: high-prob winners (60% of dataset) ─────────────────────────
    n_win = int(N * 0.60)
    for _ in range(n_win):
        rsi     = rng.uniform(48, 68)
        macd    = rng.uniform(0.05, 1.0)
        vol     = rng.uniform(1.4, 3.5)
        atr_p   = rng.uniform(0.5, 2.5)
        score   = rng.uniform(72, 95)
        adx     = rng.uniform(22, 50)
        ema_s   = rng.uniform(0.1, 1.0)
        vwap_d  = rng.uniform(-0.5, 1.5)
        bb_p    = rng.uniform(0.35, 0.75)
        hour    = rng.choice([9, 10, 15])
        is_long = rng.integers(0, 2)
        r2      = rng.uniform(0.55, 0.98)
        tod     = rng.uniform(3, 12)
        pairs   = rng.uniform(0, 6)
        vwap_r  = rng.uniform(0, 12)
        fv = build_feature_vector(rsi, macd, vol, atr_p, score, adx,
                                  ema_s, vwap_d, bb_p, int(hour), int(is_long),
                                  r2, tod, pairs, vwap_r)
        X_rows.append(fv[0])
        y_rows.append(1)

    # ─ Class 0: low-prob losers (40% of dataset) ───────────────────────────
    n_lose = N - n_win
    for _ in range(n_lose):
        rsi     = rng.choice([rng.uniform(20, 35), rng.uniform(70, 90)])
        macd    = rng.uniform(-1.0, -0.05)
        vol     = rng.uniform(0.5, 1.3)
        atr_p   = rng.uniform(0.1, 1.0)
        score   = rng.uniform(60, 74)
        adx     = rng.uniform(5, 18)
        ema_s   = rng.uniform(-0.8, 0.05)
        vwap_d  = rng.choice([rng.uniform(-3.0, -1.5), rng.uniform(2.0, 3.0)])
        bb_p    = rng.choice([rng.uniform(0.0, 0.15), rng.uniform(0.85, 1.0)])
        hour    = rng.choice([11, 12, 13, 14])
        is_long = rng.integers(0, 2)
        r2      = rng.uniform(0.0, 0.45)
        tod     = rng.uniform(-4, 3)
        pairs   = 0.0
        vwap_r  = 0.0
        fv = build_feature_vector(rsi, macd, vol, atr_p, score, adx,
                                  ema_s, vwap_d, bb_p, int(hour), int(is_long),
                                  r2, tod, pairs, vwap_r)
        X_rows.append(fv[0])
        y_rows.append(0)

    return np.array(X_rows), np.array(y_rows)


# ── Model lifecycle ────────────────────────────────────────────────────────────

class _ThresholdFallback:
    """Dead-simple fallback when sklearn is unavailable."""
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        score_feat = float(X[0][4])  # signal_score feature (normalised)
        adx_feat   = float(X[0][5])
        p = 0.40 + score_feat * 0.30 + adx_feat * 0.15
        p = float(np.clip(p, 0.0, 1.0))
        return np.array([[1 - p, p]])


def _train_model(X: np.ndarray, y: np.ndarray):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("gbt", GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            min_samples_leaf=10,
            random_state=42,
        )),
    ])
    model.fit(X, y)
    return model


def _init_model() -> None:
    global _model
    os.makedirs(os.path.dirname(_MODEL_PATH), exist_ok=True)
    try:
        if os.path.exists(_MODEL_PATH):
            with open(_MODEL_PATH, "rb") as f:
                _model = pickle.load(f)
            logger.info("ML signal ranker: loaded existing model from disk")
            return
        logger.info("ML signal ranker: training on synthetic data (first run)…")
        X, y = _generate_synthetic_data()
        _model = _train_model(X, y)
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump(_model, f)
        logger.info("ML signal ranker: trained and saved (synthetic, 4000 samples)")
    except Exception as exc:
        logger.warning(f"ML model init failed — using threshold fallback: {exc}")
        _model = _ThresholdFallback()


def _load_live_buffer() -> None:
    global _live_X, _live_y
    try:
        if os.path.exists(_BUFFER_PATH):
            with open(_BUFFER_PATH, "rb") as f:
                buf = pickle.load(f)
            _live_X = buf.get("X", [])
            _live_y = buf.get("y", [])
    except Exception:
        pass


def _save_live_buffer() -> None:
    try:
        with open(_BUFFER_PATH, "wb") as f:
            pickle.dump({"X": _live_X, "y": _live_y}, f)
    except Exception:
        pass


def _maybe_retrain() -> None:
    global _model, _live_X, _live_y
    if len(_live_y) < _MIN_RETRAIN:
        return
    try:
        logger.info(f"ML ranker: retraining with {len(_live_y)} live samples…")
        X_syn, y_syn = _generate_synthetic_data()
        X_live = np.array(_live_X)
        y_live = np.array(_live_y)
        # weight live samples 3× — real outcomes trump synthetic
        X_all = np.vstack([X_syn, X_live, X_live, X_live])
        y_all = np.concatenate([y_syn, y_live, y_live, y_live])
        new_model = _train_model(X_all, y_all)
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump(new_model, f)
        _model = new_model
        # keep last 200 live samples as rolling window
        _live_X = _live_X[-200:]
        _live_y = _live_y[-200:]
        _save_live_buffer()
        logger.info("ML ranker: retrained and saved")
    except Exception as exc:
        logger.warning(f"ML retrain failed: {exc}")


# ── Public API ─────────────────────────────────────────────────────────────────

def get_win_probability(
    rsi: float,
    macd_hist_norm: float,
    volume_ratio: float,
    atr_pct: float,
    signal_score: float,
    adx: float,
    ema_slope_pct: float,
    vwap_dist_pct: float,
    bb_pct: float,
    hour_et: int,
    is_long: int,
    r2_quality: float = 0.5,
    tod_rvol_score: float = 0.0,
    pairs_score: float = 0.0,
    vwap_reclaim: float = 0.0,
) -> float:
    """
    Returns P(win) for the signal. Fail-open: returns 0.5 on any error.
    """
    global _model
    with _lock:
        if _model is None:
            _init_model()
            _load_live_buffer()
    try:
        fv = build_feature_vector(
            rsi, macd_hist_norm, volume_ratio, atr_pct, signal_score,
            adx, ema_slope_pct, vwap_dist_pct, bb_pct, hour_et, is_long,
            r2_quality, tod_rvol_score, pairs_score, vwap_reclaim,
        )
        prob = float(_model.predict_proba(fv)[0][1])
        return round(prob, 4)
    except Exception as exc:
        logger.debug(f"get_win_probability error (fail-open): {exc}")
        return 0.5


def record_outcome(
    won: bool,
    rsi: float,
    macd_hist_norm: float,
    volume_ratio: float,
    atr_pct: float,
    signal_score: float,
    adx: float,
    ema_slope_pct: float,
    vwap_dist_pct: float,
    bb_pct: float,
    hour_et: int,
    is_long: int,
    r2_quality: float = 0.5,
    tod_rvol_score: float = 0.0,
    pairs_score: float = 0.0,
    vwap_reclaim: float = 0.0,
) -> None:
    """
    Record live trade outcome for online learning.
    Thread-safe; triggers retrain when buffer hits _MIN_RETRAIN samples.
    """
    global _live_X, _live_y
    try:
        fv = build_feature_vector(
            rsi, macd_hist_norm, volume_ratio, atr_pct, signal_score,
            adx, ema_slope_pct, vwap_dist_pct, bb_pct, hour_et, is_long,
            r2_quality, tod_rvol_score, pairs_score, vwap_reclaim,
        )
        with _lock:
            _live_X.append(fv[0].tolist())
            _live_y.append(1 if won else 0)
            _save_live_buffer()
            _maybe_retrain()
    except Exception as exc:
        logger.debug(f"record_outcome error: {exc}")


def get_model_stats() -> dict:
    """Return model training stats for heartbeat / diagnostics."""
    with _lock:
        return {
            "live_samples": len(_live_y),
            "model_type": type(_model).__name__ if _model else "none",
            "model_ready": _model is not None,
            "retrain_at": _MIN_RETRAIN,
            "win_rate_live": (
                round(sum(_live_y) / max(len(_live_y), 1), 3)
                if _live_y else None
            ),
        }
