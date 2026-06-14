"""
factor_model.py — Cross-Sectional Multi-Factor Alpha Model for NSE India

Academic basis:
  Jegadeesh & Titman (1993): momentum returns 1-3%/month
  IIM-Ahmedabad NSE study: momentum factor 21.9% annually (higher than US)
  Fama-French: combined factors explain 90%+ of cross-sectional returns
  AQR (Asness 2013): combined factors outperform individual factors

Factors (cross-sectional z-score normalized):
  F1 price_momentum  0.35 — session return from today's open
  F2 volume_momentum 0.25 — signed RVOL (volume × direction)
  F3 relative_strength 0.20 — 5-bar ROC vs peers (de-meaned by z-score)
  F4 trend_quality   0.12 — EMA stack alignment × ADX strength
  F5 vol_rank        0.08 — lower realized vol = better rank

Output: float ∈ [-15, +15] added to existing signal net_score.
"""
import logging
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

logger = logging.getLogger("factor_model")
IST = ZoneInfo("Asia/Kolkata")

# IC weights — must sum to 1.0
_FACTOR_WEIGHTS = {
    "price_momentum":    0.35,
    "volume_momentum":   0.25,
    "relative_strength": 0.20,
    "trend_quality":     0.12,
    "vol_rank":          0.08,
}
assert abs(sum(_FACTOR_WEIGHTS.values()) - 1.0) < 1e-9, "weights must sum to 1.0"

_SCORE_SCALE = 8.0    # 1 std dev above cross-section → +8 points
_MAX_BOOST   = 15.0   # hard cap per bar


def _safe_zscore(arr: np.ndarray) -> np.ndarray:
    """Cross-sectional z-score, clipped to [-3, +3] to handle outliers."""
    if len(arr) < 3:
        return np.zeros(len(arr))
    mean = np.nanmean(arr)
    std  = np.nanstd(arr)
    if std < 1e-10:
        return np.zeros(len(arr))
    return np.clip((arr - mean) / std, -3.0, 3.0)


def compute_factor_scores(
    data: Dict[str, pd.DataFrame],
    now_ts: pd.Timestamp,
) -> Dict[str, float]:
    """
    Compute cross-sectional multi-factor alpha scores for all symbols at now_ts.

    No look-ahead bias: only uses data with index <= now_ts.

    Returns:
        {symbol: alpha_score}  where positive = LONG-biased, negative = SHORT-biased.
        Range approximately [-15, +15].  Empty dict on failure.
    """
    try:
        today = now_ts.date()
        symbols: list = []
        raw: Dict[str, Dict[str, float]] = {}

        for sym, df in data.items():
            try:
                if now_ts not in df.index:
                    continue
                idx = df.index.get_loc(now_ts)
                if idx < 5:
                    continue
                row = df.loc[now_ts]
                c = float(row.get("close", 0) or 0)
                if c <= 0:
                    continue

                f: Dict[str, float] = {}

                # F1: Price momentum — session return from today's first bar open
                today_df = df[df.index.date == today]
                if len(today_df) >= 1:
                    day_open = float(today_df.iloc[0]["open"])
                    f["price_momentum"] = (c - day_open) / max(day_open, 1e-9)
                else:
                    f["price_momentum"] = 0.0

                # F2: Volume momentum — RVOL × bar direction (signed)
                rvol = float(row.get("rvol", 1.0) or 1.0)
                bar_dir = 1.0 if c > float(row.get("open", c) or c) else -1.0
                f["volume_momentum"] = (rvol - 1.0) * bar_dir

                # F3: Relative strength — 5-bar ROC (z-scoring makes it cross-sectional)
                c5 = float(df.iloc[idx - 5].get("close", c) or c)
                f["relative_strength"] = (c - c5) / max(c5, 1e-9) if c5 > 0 else f["price_momentum"]

                # F4: Trend quality — EMA stack alignment × normalized ADX
                e9  = float(row.get("ema9",  c) or c)
                e21 = float(row.get("ema21", c) or c)
                e50 = float(row.get("ema50", c) or c)
                adx = float(row.get("adx", 20) or 20)
                adx_norm = (adx - 20) / 30.0  # 0 at ADX=20, 1 at ADX=50
                if   e9 > e21 > e50: ema_dir =  1.0
                elif e9 < e21 < e50: ema_dir = -1.0
                elif e9 > e21:       ema_dir =  0.5
                elif e9 < e21:       ema_dir = -0.5
                else:                ema_dir =  0.0
                f["trend_quality"] = ema_dir * max(adx_norm, 0.1)

                # F5: Volatility rank — lower ATR% = higher rank = better momentum sustainability
                atr_val = float(row.get("atr", c * 0.005) or c * 0.005)
                atr_pct = atr_val / max(c, 1e-9)
                f["vol_rank"] = -atr_pct * bar_dir   # negate + direction

                raw[sym] = f
                symbols.append(sym)
            except Exception:
                continue

        if len(symbols) < 3:
            return {}

        # Cross-sectional z-score normalization per factor
        factor_arrs: Dict[str, np.ndarray] = {}
        for fname in _FACTOR_WEIGHTS:
            vals = np.array([raw[s].get(fname, 0.0) for s in symbols], dtype=float)
            factor_arrs[fname] = _safe_zscore(vals)

        # IC-weighted combination
        combined = np.zeros(len(symbols))
        for fname, weight in _FACTOR_WEIGHTS.items():
            combined += weight * factor_arrs[fname]

        # Scale and cap
        alpha_scores: Dict[str, float] = {}
        for i, sym in enumerate(symbols):
            alpha_scores[sym] = float(np.clip(combined[i] * _SCORE_SCALE, -_MAX_BOOST, _MAX_BOOST))

        return alpha_scores

    except Exception as e:
        logger.debug("factor_model.compute_factor_scores: %s", e)
        return {}


def get_factor_score_boost(
    symbol: str,
    direction: str,
    cached_scores: Dict[str, float],
) -> Tuple[float, str]:
    """
    Get signal score boost for a symbol from pre-computed factor scores.

    direction="LONG"  → positive alpha = boost, negative = penalty
    direction="SHORT" → inverted (short the weak stocks)

    Returns (score_delta, reason_string).  Fails open → (0.0, "").
    """
    try:
        alpha = cached_scores.get(symbol, 0.0)
        if direction == "SHORT":
            alpha = -alpha
        if alpha > 8.0:
            return alpha, f"ALPHA_STRONG(+{alpha:.1f})"
        if alpha > 4.0:
            return alpha, f"ALPHA_MOD(+{alpha:.1f})"
        if alpha > 0:
            return alpha, ""
        if alpha < -4.0:
            return alpha, f"ALPHA_WEAK({alpha:.1f})"
        return alpha, ""
    except Exception:
        return 0.0, ""
