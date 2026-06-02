"""
random_forest_ranker.py — Random Forest Ensemble (v17.0)

A Random Forest classifier that provides a second ML opinion alongside the
GradientBoosting model in ml_signal_ranker.py.

Architecture:
  - Same 11 core features as ml_signal_ranker (subset for compatibility)
  - n_estimators=100, max_depth=6, min_samples_split=10
  - Trained on 4000 synthetic samples using same patterns as GBT
  - Persisted to data/rf_model.pkl
  - Fail-open: returns 0.5 (neutral) on any error

Usage:
  prob = get_rf_win_probability(rsi, macd_hist_norm, volume_ratio, atr_pct,
                                signal_score, adx, ema_slope_pct,
                                vwap_dist_pct, bb_pct, hour_et, long_flag)
"""

import logging
import os
import pickle
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "data", "rf_model.pkl")
_model      = None
_lock       = threading.Lock()


# ── Feature building ──────────────────────────────────────────────────────────

def _build_feature_vector(
    rsi: float,
    macd_hist_norm: float,
    volume_ratio: float,
    atr_pct: float,
    signal_score: float,
    adx: float,
    ema_slope_pct: float,
    vwap_dist_pct: float,
    bb_pct: float,
    hour_et: float,
    long_flag: int,
) -> np.ndarray:
    """Normalise 11 features into a [0,1] or [-1,1] float array."""
    rsi_n    = float(np.clip(rsi, 0, 100)) / 100.0
    macd_n   = float(np.clip(macd_hist_norm, -3.0, 3.0)) / 3.0
    vol_n    = float(np.clip(volume_ratio, 0.0, 5.0)) / 5.0
    atr_n    = float(np.clip(atr_pct, 0.0, 5.0)) / 5.0
    score_n  = float(np.clip(signal_score, 0.0, 100.0)) / 100.0
    adx_n    = float(np.clip(adx, 0.0, 60.0)) / 60.0
    ema_n    = float(np.clip(ema_slope_pct, -2.0, 2.0)) / 2.0
    vwap_n   = float(np.clip(vwap_dist_pct, -3.0, 3.0)) / 3.0
    bb_n     = float(np.clip(bb_pct, 0.0, 1.0))
    hour_n   = float(np.clip(hour_et, 9.0, 16.0)) / 16.0
    lf       = float(int(long_flag))
    return np.array([[rsi_n, macd_n, vol_n, atr_n, score_n,
                      adx_n, ema_n, vwap_n, bb_n, hour_n, lf]])


# ── Synthetic training data ───────────────────────────────────────────────────

def _generate_synthetic_data():
    """
    4,000 synthetic examples matching ml_signal_ranker's dataset structure.
    60% winners / 40% losers.
    """
    rng = np.random.default_rng(seed=137)   # different seed from GBT for diversity
    N = 4000
    X_rows, y_rows = [], []

    # ── Winners (60%) ──────────────────────────────────────────────────────
    n_win = int(N * 0.60)
    for _ in range(n_win):
        rsi    = rng.uniform(48, 68)
        macd   = rng.uniform(0.05, 1.0)
        vol    = rng.uniform(1.4, 3.5)
        atr_p  = rng.uniform(0.5, 2.5)
        score  = rng.uniform(72, 95)
        adx    = rng.uniform(22, 50)
        ema_s  = rng.uniform(0.1, 1.0)
        vwap_d = rng.uniform(-0.5, 1.5)
        bb_p   = rng.uniform(0.35, 0.75)
        hour   = rng.choice([9.0, 10.0, 15.0])
        lf     = int(rng.integers(0, 2))
        fv = _build_feature_vector(rsi, macd, vol, atr_p, score, adx,
                                   ema_s, vwap_d, bb_p, hour, lf)
        X_rows.append(fv[0])
        y_rows.append(1)

    # ── Losers (40%) ───────────────────────────────────────────────────────
    n_lose = N - n_win
    for _ in range(n_lose):
        rsi    = float(rng.choice([rng.uniform(20, 35), rng.uniform(70, 90)]))
        macd   = rng.uniform(-1.0, -0.05)
        vol    = rng.uniform(0.5, 1.3)
        atr_p  = rng.uniform(0.1, 1.0)
        score  = rng.uniform(60, 74)
        adx    = rng.uniform(5, 18)
        ema_s  = rng.uniform(-0.8, 0.05)
        vwap_d = float(rng.choice([rng.uniform(-3.0, -1.5), rng.uniform(2.0, 3.0)]))
        bb_p   = float(rng.choice([rng.uniform(0.0, 0.15), rng.uniform(0.85, 1.0)]))
        hour   = rng.choice([11.0, 12.0, 13.0, 14.0])
        lf     = int(rng.integers(0, 2))
        fv = _build_feature_vector(rsi, macd, vol, atr_p, score, adx,
                                   ema_s, vwap_d, bb_p, hour, lf)
        X_rows.append(fv[0])
        y_rows.append(0)

    return np.array(X_rows), np.array(y_rows)


# ── Model lifecycle ───────────────────────────────────────────────────────────

class _ThresholdFallback:
    """Dead-simple fallback when sklearn is unavailable."""
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        score_feat = float(X[0][4])   # signal_score normalised
        adx_feat   = float(X[0][5])
        p = 0.40 + score_feat * 0.28 + adx_feat * 0.12
        p = float(np.clip(p, 0.0, 1.0))
        return np.array([[1 - p, p]])


def _train_model(X: np.ndarray, y: np.ndarray):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("rf", RandomForestClassifier(
            n_estimators=100,
            max_depth=6,
            min_samples_split=10,
            min_samples_leaf=5,
            max_features="sqrt",
            random_state=137,
            n_jobs=-1,
        )),
    ])
    model.fit(X, y)
    return model


def _init_model() -> None:
    global _model
    try:
        os.makedirs(os.path.dirname(_MODEL_PATH), exist_ok=True)
    except Exception:
        pass

    try:
        if os.path.exists(_MODEL_PATH):
            with open(_MODEL_PATH, "rb") as f:
                _model = pickle.load(f)
            logger.info("RF ranker: loaded existing model from disk")
            return
        logger.info("RF ranker: training on synthetic data (first run)...")
        X, y = _generate_synthetic_data()
        _model = _train_model(X, y)
        try:
            with open(_MODEL_PATH, "wb") as f:
                pickle.dump(_model, f)
            logger.info("RF ranker: trained and saved (synthetic, 4000 samples)")
        except Exception as save_exc:
            logger.warning(f"RF ranker: could not save model: {save_exc}")
    except ImportError:
        logger.info("RF ranker: sklearn not available — using threshold fallback")
        _model = _ThresholdFallback()
    except Exception as exc:
        logger.warning(f"RF ranker: init failed — using threshold fallback: {exc}")
        _model = _ThresholdFallback()


# ── Public API ────────────────────────────────────────────────────────────────

def get_rf_win_probability(
    rsi: float,
    macd_hist_norm: float,
    volume_ratio: float,
    atr_pct: float,
    signal_score: float,
    adx: float,
    ema_slope_pct: float,
    vwap_dist_pct: float,
    bb_pct: float,
    hour_et: float,
    long_flag: int,
) -> float:
    """
    Random Forest win-probability scorer.

    Returns probability [0.0, 1.0] of a winning trade.
    Fail-open: returns 0.5 (neutral) on any error.

    Score adjustments (applied in signal_generator.py):
      prob >= 0.70 : +6 pts
      prob >= 0.60 : +3 pts
      prob <  0.40 : -6 pts
    """
    global _model

    try:
        with _lock:
            if _model is None:
                _init_model()
            if _model is None:
                _model = _ThresholdFallback()

        fv = _build_feature_vector(
            rsi, macd_hist_norm, volume_ratio, atr_pct,
            signal_score, adx, ema_slope_pct, vwap_dist_pct,
            bb_pct, hour_et, long_flag,
        )
        proba = _model.predict_proba(fv)
        return float(proba[0][1])   # probability of class 1 (winner)
    except Exception as exc:
        logger.debug(f"get_rf_win_probability error (fail-open): {exc}")
        return 0.5
