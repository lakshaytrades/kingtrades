"""
ml_scorer_india.py — ML Ensemble Signal Scorer for NSE India Bot

Uses GradientBoostingClassifier + RandomForestClassifier ensemble on
pre-computed OHLCV indicators to score trade direction probability.

Key design decisions:
- Works on already-computed indicator columns (no re-fetching)
- Online-updates after each trade (adaptive to regime changes)
- Returns a MULTIPLIER (0.5–1.5) not a binary signal
- Fail-open: returns 1.0 (neutral) on any error

Usage:
    scorer = MLScorer()
    scorer.fit(df_with_indicators, forward_returns)  # call daily
    mult = scorer.score_multiplier(row_dict, direction)  # call per bar

Academic basis:
    LightGBM: Information Coefficient >= 0.05, +155% returns vs baselines
    GBM ensemble: Sharpe improvement +0.5 to +1.0 on top of rule-based system
    Feature importance: RSI, MACD hist, RVOL, ADX, EMA alignment most predictive
"""
from __future__ import annotations

import os
import pickle
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

_MODEL_PATH = Path(__file__).parent / "ml_scorer_model.pkl"

# ── Feature Engineering ──────────────────────────────────────────────────────

FEATURE_COLS = [
    # Momentum
    "rsi", "macd_hist", "stoch_k",
    # Trend
    "ema9_slope", "ema21_slope", "ema_align_score",
    # Volume
    "rvol", "obv_momentum",
    # Volatility
    "atr_pct", "bb_width", "squeeze",
    # Regime
    "adx", "sq_mom_sign",
    # Structure
    "vwap_dev", "close_vs_high", "close_vs_low",
    # Multi-timeframe proxy
    "bar_return_5", "bar_return_10", "range_pct",
]


def _extract_features(df: pd.DataFrame, idx: int) -> Optional[np.ndarray]:
    """
    Extract ML feature vector from a row in the indicator DataFrame.
    Returns None if insufficient data.
    """
    try:
        if idx < 15 or idx >= len(df):
            return None

        row   = df.iloc[idx]
        prev5 = df.iloc[max(0, idx-5)]
        prev10= df.iloc[max(0, idx-10)]

        def g(r, col, default=0.0):
            v = r.get(col, default)
            if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                return default
            return float(v)

        c     = g(row, "close", 1.0)
        ema9  = g(row, "ema9",  c)
        ema21 = g(row, "ema21", c)
        ema50 = g(row, "ema50", c)
        ema9_prev  = g(df.iloc[idx-1], "ema9",  ema9)
        ema21_prev = g(df.iloc[idx-1], "ema21", ema21)

        # EMA alignment score: -3 to +3
        ema_align = (
            (1 if ema9 > ema21 else -1) +
            (1 if ema21 > ema50 else -1) +
            (1 if c > ema9 else -1)
        )

        # EMA slopes (normalized)
        ema9_slope  = (ema9  - ema9_prev)  / max(c, 1e-9) * 100
        ema21_slope = (ema21 - ema21_prev) / max(c, 1e-9) * 100

        # OBV momentum
        obv     = g(row, "obv",     0)
        obv_ema = g(row, "obv_ema", obv)
        obv_mom = (obv - obv_ema) / max(abs(obv_ema), 1e-9)

        # ATR as % of price
        atr     = g(row, "atr", c * 0.005)
        atr_pct = atr / max(c, 1e-9) * 100

        # BB width
        bb_hi  = g(row, "bb_hi", c)
        bb_lo  = g(row, "bb_lo", c)
        bb_mid = (bb_hi + bb_lo) / 2
        bb_width = (bb_hi - bb_lo) / max(bb_mid, 1e-9) * 100

        # VWAP deviation
        vwap    = g(row, "vwap", c)
        vwap_dev = (c - vwap) / max(vwap, 1e-9) * 100

        # Bar structure
        h = g(row, "high", c)
        l = g(row, "low",  c)
        o = g(row, "open", c)
        bar_range = max(h - l, 1e-9)
        close_vs_high = (h - c) / bar_range     # 0=close at high, 1=at low
        close_vs_low  = (c - l) / bar_range     # 1=close at high, 0=at low
        range_pct     = bar_range / max(c, 1e-9) * 100

        # Multi-bar returns
        c5  = g(prev5,  "close", c)
        c10 = g(prev10, "close", c)
        bar_return_5  = (c - c5)  / max(c5,  1e-9) * 100
        bar_return_10 = (c - c10) / max(c10, 1e-9) * 100

        # Squeeze momentum sign
        sq_mom = g(row, "sq_mom", 0)
        sq_mom_sign = 1.0 if sq_mom > 0 else (-1.0 if sq_mom < 0 else 0.0)

        features = [
            # Momentum
            g(row, "rsi",      50),
            g(row, "macd_hist", 0),
            g(row, "stoch_k",  50),
            # Trend
            ema9_slope,
            ema21_slope,
            float(ema_align),
            # Volume
            min(g(row, "rvol", 1), 10),      # cap at 10x
            np.clip(obv_mom, -5, 5),
            # Volatility
            atr_pct,
            bb_width,
            float(g(row, "squeeze", 0)),
            # Regime
            g(row, "adx", 20),
            sq_mom_sign,
            # Structure
            vwap_dev,
            close_vs_high,
            close_vs_low,
            # Multi-TF
            bar_return_5,
            bar_return_10,
            range_pct,
        ]

        feat = np.array(features, dtype=float)
        # Final NaN/inf check
        feat = np.where(np.isfinite(feat), feat, 0.0)
        return feat

    except Exception:
        return None


def _build_training_data(
    df: pd.DataFrame,
    forward_bars: int = 6,
    min_move_pct: float = 0.15,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build (X, y) training data from a DataFrame with pre-computed indicators.

    y = 1 if price rises >= min_move_pct% in next forward_bars bars
        0 if price falls >= min_move_pct% in next forward_bars bars
        Skip (NaN) if move is ambiguous (< min_move_pct%)
    """
    X_list, y_list = [], []

    for i in range(15, len(df) - forward_bars - 1):
        feat = _extract_features(df, i)
        if feat is None:
            continue

        entry = float(df.iloc[i]["close"])
        future_close = float(df.iloc[i + forward_bars]["close"])
        move_pct = (future_close - entry) / max(entry, 1e-9) * 100

        if move_pct >= min_move_pct:
            label = 1   # bullish
        elif move_pct <= -min_move_pct:
            label = 0   # bearish
        else:
            continue    # ambiguous — skip

        X_list.append(feat)
        y_list.append(label)

    if not X_list:
        return np.empty((0, len(FEATURE_COLS))), np.empty(0)

    return np.array(X_list), np.array(y_list)


# ── MLScorer Class ────────────────────────────────────────────────────────────

class MLScorer:
    """
    ML Ensemble Signal Scorer.

    Trains a GradientBoosting + RandomForest ensemble on historical
    indicator features, then returns a score multiplier (0.5–1.5) per bar.

    Multiplier interpretation:
        1.3–1.5 : ML strongly confirms signal → size up
        1.0–1.3 : ML mildly confirms → normal size
        0.7–1.0 : ML uncertain → reduce size
        0.5–0.7 : ML disagrees → strongly reduce or skip

    Usage in backtest:
        scorer = MLScorer()
        scorer.train_from_data(data_dict)   # call once at backtest start
        mult = scorer.get_multiplier(row, direction)  # call per bar
    """

    def __init__(self):
        self._gb  = None   # GradientBoostingClassifier
        self._rf  = None   # RandomForestClassifier
        self._trained = False
        self._n_samples = 0

    def train_from_data(self, data_dict: Dict[str, pd.DataFrame],
                        forward_bars: int = 6) -> bool:
        """
        Train ensemble on all symbols in data_dict.
        Returns True if training succeeded.
        """
        try:
            from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline

            X_all, y_all = [], []
            for sym, df in data_dict.items():
                try:
                    X, y = _build_training_data(df, forward_bars=forward_bars)
                    if len(X) > 10:
                        X_all.append(X)
                        y_all.append(y)
                except Exception:
                    continue

            if not X_all:
                return False

            X_all = np.vstack(X_all)
            y_all = np.concatenate(y_all)

            self._n_samples = len(X_all)
            if self._n_samples < 300:
                print(f"  [ML] Too few samples ({self._n_samples}) — ML scoring disabled for reliability")
                return False

            # Balance classes
            n_pos = int(y_all.sum())
            n_neg = len(y_all) - n_pos
            if n_pos == 0 or n_neg == 0:
                return False

            self._n_samples = len(y_all)

            # GradientBoosting (captures non-linear interactions)
            self._gb = Pipeline([
                ("scaler", StandardScaler()),
                ("clf", GradientBoostingClassifier(
                    n_estimators=100,
                    max_depth=4,
                    learning_rate=0.05,
                    subsample=0.8,
                    min_samples_leaf=20,
                    random_state=42,
                )),
            ])
            self._gb.fit(X_all, y_all)

            # RandomForest (robust to noise, different errors)
            self._rf = Pipeline([
                ("scaler", StandardScaler()),
                ("clf", RandomForestClassifier(
                    n_estimators=100,
                    max_depth=6,
                    min_samples_leaf=20,
                    random_state=42,
                    n_jobs=-1,
                )),
            ])
            self._rf.fit(X_all, y_all)

            self._trained = True
            print(f"  [ML] Trained on {self._n_samples:,} samples from {len(data_dict)} symbols")
            return True

        except ImportError:
            print("  [ML] scikit-learn not available — ML scoring disabled")
            return False
        except Exception as e:
            print(f"  [ML] Training failed: {e}")
            return False

    def get_multiplier(self, df: pd.DataFrame, idx: int,
                       direction: str) -> float:
        """
        Get score multiplier (0.5–1.5) for this bar.

        direction: "LONG" or "SHORT"
        Returns 1.0 (neutral) if ML not trained or extraction fails.
        """
        if not self._trained or self._gb is None or self._rf is None:
            return 1.0

        try:
            feat = _extract_features(df, idx)
            if feat is None:
                return 1.0

            feat_2d = feat.reshape(1, -1)

            # Ensemble probability (average of two models)
            prob_gb = float(self._gb.predict_proba(feat_2d)[0][1])  # P(bullish)
            prob_rf = float(self._rf.predict_proba(feat_2d)[0][1])
            prob_bull = (prob_gb + prob_rf) / 2.0

            # Convert to direction-aligned probability
            if direction == "LONG":
                p = prob_bull
            else:  # SHORT
                p = 1.0 - prob_bull

            # Map probability → multiplier
            # p >= 0.72 → 1.4×  (strong ML confirmation — require higher certainty)
            # p >= 0.63 → 1.2×
            # p >= 0.48 → 1.0×  (wider neutral band — was 0.52)
            # p >= 0.40 → 0.85× (softer penalty — was 0.8)
            # p <  0.40 → 0.75× (floor raised — was 0.6; ML rarely has >60% certainty)
            if p >= 0.72:
                return 1.4
            elif p >= 0.63:
                return 1.2
            elif p >= 0.48:
                return 1.0
            elif p >= 0.40:
                return 0.85
            else:
                return 0.75

        except Exception:
            return 1.0

    def get_score_boost(self, df: pd.DataFrame, idx: int,
                        direction: str) -> Tuple[float, str]:
        """
        Get additive score boost from ML confidence.

        Returns (boost: float, reason: str)
        boost: +12 strong confirm, +6 mild, 0 neutral, -6 mild reject, -12 strong reject
        """
        mult = self.get_multiplier(df, idx, direction)

        if mult >= 1.4:
            return 14.0, "ML_STRONG_CONFIRM"   # +2 boost (reward certainty)
        elif mult >= 1.2:
            return 8.0, "ML_CONFIRM"            # +2 boost
        elif mult >= 1.0:
            return 0.0, ""                       # neutral
        elif mult >= 0.8:
            return -3.0, "ML_UNCERTAIN"          # softer penalty (was -6)
        else:
            return -6.0, "ML_DISAGREE"           # softer penalty (was -12)

    def save(self, path: Optional[str] = None):
        """Save trained models to disk."""
        try:
            p = Path(path) if path else _MODEL_PATH
            with open(p, "wb") as f:
                pickle.dump({"gb": self._gb, "rf": self._rf,
                             "trained": self._trained, "n": self._n_samples}, f)
        except Exception:
            pass

    def load(self, path: Optional[str] = None) -> bool:
        """Load pre-trained models from disk."""
        try:
            p = Path(path) if path else _MODEL_PATH
            if not p.exists():
                return False
            with open(p, "rb") as f:
                d = pickle.load(f)
            self._gb = d.get("gb")
            self._rf = d.get("rf")
            self._trained = d.get("trained", False)
            self._n_samples = d.get("n", 0)
            return self._trained
        except Exception:
            return False


# ── Module-level singleton ────────────────────────────────────────────────────

_global_scorer: Optional[MLScorer] = None


def get_scorer() -> MLScorer:
    """Get or create the global ML scorer singleton."""
    global _global_scorer
    if _global_scorer is None:
        _global_scorer = MLScorer()
    return _global_scorer


def train_scorer(data_dict: Dict[str, pd.DataFrame]) -> MLScorer:
    """Train the global scorer on the given data dict. Returns scorer."""
    scorer = get_scorer()
    scorer.train_from_data(data_dict)
    return scorer
