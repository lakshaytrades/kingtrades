"""
crypto_regime.py — Market Regime Detection

Determines market state to dynamically adjust:
- Signal score thresholds (harder gates in choppy/volatile markets)
- Position size multipliers (reduce in volatile expansion)
- Max concurrent positions (0 = emergency: no new entries)
- Minimum pattern categories required

Regime types:
  TRENDING_BULL  — ADX>25, EMA bullish — full momentum mode
  TRENDING_BEAR  — ADX>25, EMA bearish — no longs (longs blocked)
  RANGING        — ADX<20, sideways — mean reversion, need more confirmation
  VOLATILE_BULL  — ATR 1.5x+ above avg, price rising — half size, fast exits
  VOLATILE_BEAR  — ATR 1.5x+ above avg, price falling — EMERGENCY: close longs
  QUIET          — ATR below 0.6x avg — squeeze brewing, good for breakouts
  UNKNOWN        — insufficient data — conservative defaults
"""

import logging
from enum import Enum
from typing import Dict, Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class Regime(str, Enum):
    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGING       = "RANGING"
    VOLATILE_BULL = "VOLATILE_BULL"
    VOLATILE_BEAR = "VOLATILE_BEAR"
    QUIET         = "QUIET"
    UNKNOWN       = "UNKNOWN"


def _compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                  period: int = 14) -> float:
    """
    Compute ADX using proper Wilder smoothing.
    Returns the most recent ADX value (0-100).
    Returns 0.0 if insufficient data.
    """
    n = len(close)
    if n < period * 2 + 5:
        return 0.0

    # True Range
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1]),
        )

    # Directional Movement
    plus_dm  = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up   = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        if up > down and up > 0:
            plus_dm[i]  = up
        elif down > up and down > 0:
            minus_dm[i] = down

    # Wilder smoothing — initialise with sum of first `period` values
    atr_s    = np.sum(tr[1:period + 1])
    pdm_s    = np.sum(plus_dm[1:period + 1])
    mdm_s    = np.sum(minus_dm[1:period + 1])

    dx_list = []
    for i in range(period + 1, n):
        atr_s = atr_s - (atr_s / period) + tr[i]
        pdm_s = pdm_s - (pdm_s / period) + plus_dm[i]
        mdm_s = mdm_s - (mdm_s / period) + minus_dm[i]

        if atr_s <= 0:
            dx_list.append(0.0)
            continue

        pdi = 100.0 * pdm_s / atr_s
        mdi = 100.0 * mdm_s / atr_s
        denom = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dx_list.append(dx)

    if len(dx_list) < period:
        return 0.0

    # Smooth DX values to get ADX
    adx = np.mean(dx_list[-period:])
    return float(adx)


def detect_regime(df: pd.DataFrame, ind: Dict) -> Regime:
    """
    Detect market regime from OHLCV DataFrame and pre-computed indicators dict.

    Parameters
    ----------
    df  : OHLCV DataFrame (columns: open, high, low, close, volume)
    ind : pre-computed indicators dict (from _compute_indicators in crypto_signals)

    Returns
    -------
    Regime enum value
    """
    if df is None or ind is None or len(df) < 30:
        return Regime.UNKNOWN

    try:
        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        n     = len(close)

        # ── ATR ratio ────────────────────────────────────────────────────────
        tr_list = []
        for i in range(1, n):
            tr_list.append(max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i]  - close[i - 1]),
            ))
        tr_arr = np.array(tr_list)

        if len(tr_arr) < 14:
            return Regime.UNKNOWN

        current_atr  = float(np.mean(tr_arr[-14:]))
        avg_atr_60   = float(np.mean(tr_arr[-min(60, len(tr_arr)):]))
        atr_ratio    = current_atr / avg_atr_60 if avg_atr_60 > 0 else 1.0

        # ── Price change over last 5 bars ─────────────────────────────────────
        price_change_pct = 0.0
        if n >= 6:
            ref = close[-6]
            price_change_pct = (close[-1] - ref) / ref * 100 if ref > 0 else 0.0

        # ── ATR extreme → VOLATILE ────────────────────────────────────────────
        if atr_ratio > 2.0:
            if price_change_pct >= 0:
                return Regime.VOLATILE_BULL
            else:
                return Regime.VOLATILE_BEAR

        if atr_ratio > 1.5:
            if price_change_pct >= 0:
                return Regime.VOLATILE_BULL
            else:
                return Regime.VOLATILE_BEAR

        # ── ATR very low → QUIET ──────────────────────────────────────────────
        if atr_ratio < 0.6:
            return Regime.QUIET

        # ── ADX-based trending vs ranging ─────────────────────────────────────
        adx = _compute_adx(high, low, close, period=14)

        if adx > 25:
            ema9  = ind.get("ema9",  close[-1])
            ema21 = ind.get("ema21", close[-1])
            ema50 = ind.get("ema50", close[-1])
            if ema9 > ema21 > ema50:
                return Regime.TRENDING_BULL
            else:
                return Regime.TRENDING_BEAR

        # ADX < 25 (or between 20-25 if we wanted a border) → RANGING
        return Regime.RANGING

    except Exception as e:
        logger.debug(f"detect_regime error: {e}")
        return Regime.UNKNOWN


def get_regime_adjustments(regime: Regime) -> Dict:
    """
    Return a dict of trading parameter adjustments for the given regime.

    Keys
    ----
    regime           : Regime enum value
    score_floor_adj  : added to CRYPTO_MIN_SIGNAL_SCORE (positive = harder gate)
    size_mult        : multiply notional size (0.0 = no new trades)
    min_pattern_cats : minimum distinct pattern category types required
    max_positions    : override max concurrent positions (0 = emergency block)
    description      : human-readable summary
    """
    table = {
        Regime.TRENDING_BULL: dict(
            regime           = Regime.TRENDING_BULL,
            score_floor_adj  = 0,
            size_mult        = 1.0,
            min_pattern_cats = 1,
            max_positions    = 2,
            description      = "Trending bull — full momentum mode",
        ),
        Regime.TRENDING_BEAR: dict(
            regime           = Regime.TRENDING_BEAR,
            score_floor_adj  = +8,
            size_mult        = 0.5,
            min_pattern_cats = 2,
            max_positions    = 1,
            description      = "Trending bear — shorts only, longs blocked",
        ),
        Regime.RANGING: dict(
            regime           = Regime.RANGING,
            score_floor_adj  = +8,
            size_mult        = 0.7,
            min_pattern_cats = 2,
            max_positions    = 2,
            description      = "Ranging market — need stronger confirmation",
        ),
        Regime.VOLATILE_BULL: dict(
            regime           = Regime.VOLATILE_BULL,
            score_floor_adj  = +5,
            size_mult        = 0.5,
            min_pattern_cats = 2,
            max_positions    = 1,
            description      = "Volatile bull — half size, fast exits",
        ),
        Regime.VOLATILE_BEAR: dict(
            regime           = Regime.VOLATILE_BEAR,
            score_floor_adj  = +20,
            size_mult        = 0.0,
            min_pattern_cats = 3,
            max_positions    = 0,
            description      = "VOLATILE BEAR — EMERGENCY: close longs, no new entries",
        ),
        Regime.QUIET: dict(
            regime           = Regime.QUIET,
            score_floor_adj  = -3,
            size_mult        = 1.0,
            min_pattern_cats = 1,
            max_positions    = 2,
            description      = "Quiet/squeeze — good for breakout setups",
        ),
        Regime.UNKNOWN: dict(
            regime           = Regime.UNKNOWN,
            score_floor_adj  = +5,
            size_mult        = 0.8,
            min_pattern_cats = 1,
            max_positions    = 2,
            description      = "Unknown regime — conservative defaults",
        ),
    }
    return table.get(regime, table[Regime.UNKNOWN])


def detect_btc_macro_regime(btc_df4h: pd.DataFrame, btc_ind4h: Dict) -> Regime:
    """
    Detect macro regime using BTC 4H data.
    Calls detect_regime() — kept as a named entry-point for clarity.
    """
    return detect_regime(btc_df4h, btc_ind4h)
