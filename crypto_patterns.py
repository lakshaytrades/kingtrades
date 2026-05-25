"""
crypto_patterns.py — World-Class Crypto Pattern Detection Library

Comprehensive pattern detection covering EVERY major approach used by top 1%
professional crypto traders:

  - 20 Candlestick patterns (single, double, triple candle)
  - 12 Chart patterns (requires 20+ bars)
  - 5 Smart Money Concepts (FVG, Order Block, Liquidity Sweep, BOS, CHoCH)
  - 4 Wyckoff Method signals (Spring, Upthrust, Accumulation, Distribution)
  - Fibonacci retracement + extension zones
  - 4 RSI Divergence types + 2 MACD Divergence types
  - Volume Profile (POC, Value Area, HVN, LVN)
  - Elliott Wave simplified detection (Wave 3, Wave 5 exhaustion)
  - Harmonic patterns (ABCD, Gartley, Butterfly)

All functions accept numpy/pandas inputs and return PatternResult objects.
Scoring guide:
  90-95 : Spring/Upthrust, Cup&Handle, Morning/Evening Star, Liquidity Sweep
  85-89 : Engulfing, FVG confirmed, BOS, CHoCH, Fibonacci 61.8%
  80-84 : H&S confirmed, 3 Soldiers/Crows, Double Top/Bottom, Wyckoff
  75-79 : Hammer at support, Flag breakout, Order Block, Divergence
  70-74 : Most candlesticks, Fibonacci 38.2%/50%, Harami, FVG unconfirmed
  60-69 : Contextual patterns (Doji, Ascending Triangle building)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core data structure
# ---------------------------------------------------------------------------

@dataclass
class PatternResult:
    """Single detected pattern with direction, strength, and optional key level."""
    name:      str
    direction: str          # "LONG", "SHORT", "NEUTRAL"
    strength:  float        # 0-100 confidence score
    key_level: Optional[float] = None
    description: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _candle_body(o: float, c: float) -> float:
    return abs(c - o)


def _candle_range(h: float, l: float) -> float:
    return h - l


def _upper_wick(o: float, h: float, c: float) -> float:
    return h - max(o, c)


def _lower_wick(o: float, l: float, c: float) -> float:
    return min(o, c) - l


def _is_bullish(o: float, c: float) -> bool:
    return c > o


def _is_bearish(o: float, c: float) -> bool:
    return c < o


def _swing_highs_lows(high: np.ndarray, low: np.ndarray,
                       lookback: int = 5) -> Tuple[List[int], List[int]]:
    """Return indices of swing highs and swing lows."""
    n = len(high)
    sh, sl = [], []
    for i in range(lookback, n - lookback):
        if high[i] == max(high[i - lookback: i + lookback + 1]):
            sh.append(i)
        if low[i] == min(low[i - lookback: i + lookback + 1]):
            sl.append(i)
    return sh, sl


def _compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute RSI array (same length as close, NaN where undefined)."""
    n = len(close)
    rsi = np.full(n, np.nan)
    if n <= period:
        return rsi
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = np.zeros(n)
    avg_l = np.zeros(n)
    avg_g[period] = np.mean(gain[:period])
    avg_l[period] = np.mean(loss[:period])
    for i in range(period + 1, n):
        avg_g[i] = (avg_g[i - 1] * (period - 1) + gain[i - 1]) / period
        avg_l[i] = (avg_l[i - 1] * (period - 1) + loss[i - 1]) / period
    rs = np.where(avg_l > 0, avg_g / avg_l, 100.0)
    rsi[period:] = 100.0 - (100.0 / (1.0 + rs[period:]))
    return rsi


def _compute_macd_hist(close: np.ndarray,
                        fast: int = 12, slow: int = 26,
                        signal: int = 9) -> np.ndarray:
    """Return MACD histogram array."""
    def ema(arr, p):
        e = np.full(len(arr), np.nan)
        if len(arr) < p:
            return e
        k = 2.0 / (p + 1)
        e[p - 1] = np.mean(arr[:p])
        for i in range(p, len(arr)):
            e[i] = arr[i] * k + e[i - 1] * (1 - k)
        return e

    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    mask = ~np.isnan(macd_line)
    sig_line = np.full(len(close), np.nan)
    idx = np.where(mask)[0]
    if len(idx) >= signal:
        partial = macd_line[idx]
        sig_partial = ema(partial, signal)
        sig_line[idx] = sig_partial
    return macd_line - sig_line


def _compute_atr(high: np.ndarray, low: np.ndarray,
                  close: np.ndarray, period: int = 14) -> float:
    """Return ATR scalar for the last `period` bars."""
    n = len(close)
    if n < 2:
        return 0.0
    tr = np.array([
        max(high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]))
        for i in range(1, n)
    ])
    if len(tr) < period:
        return float(np.mean(tr))
    return float(np.mean(tr[-period:]))


# ===========================================================================
# 1. CANDLESTICK PATTERNS (20 patterns)
# ===========================================================================

def detect_candlestick_patterns(df: pd.DataFrame,
                                  ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Detect all 20 single/double/triple candlestick patterns on the last 3 bars of df.
    ind: optional indicator dict from crypto_signals._compute_indicators() for context.
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 3:
        return results

    o  = df["open"].values.astype(float)
    h  = df["high"].values.astype(float)
    l  = df["low"].values.astype(float)
    c  = df["close"].values.astype(float)

    # Last 3 candles (i0 = oldest of the 3, i2 = current)
    i0, i1, i2 = -3, -2, -1

    o0, h0, l0, c0 = o[i0], h[i0], l[i0], c[i0]
    o1, h1, l1, c1 = o[i1], h[i1], l[i1], c[i1]
    o2, h2, l2, c2 = o[i2], h[i2], l[i2], c[i2]

    body0 = _candle_body(o0, c0)
    body1 = _candle_body(o1, c1)
    body2 = _candle_body(o2, c2)
    rng2  = _candle_range(h2, l2)
    uw2   = _upper_wick(o2, h2, c2)
    lw2   = _lower_wick(o2, l2, c2)
    rng1  = _candle_range(h1, l1)
    uw1   = _upper_wick(o1, h1, c1)
    lw1   = _lower_wick(o1, l1, c1)

    atr = _compute_atr(h, l, c, 14) if len(df) >= 15 else (rng2 if rng2 > 0 else 1.0)
    if atr <= 0:
        atr = 1.0

    # VWAP / EMA support context from ind (boosts relevance)
    near_support = False
    near_resistance = False
    if ind:
        vwap = ind.get("vwap", c2)
        bb_lo = ind.get("bb_lower", c2 * 0.97)
        bb_up = ind.get("bb_upper", c2 * 1.03)
        near_support    = (c2 < vwap * 1.005) or (c2 < bb_lo * 1.01)
        near_resistance = (c2 > vwap * 0.995) or (c2 > bb_up * 0.99)

    # ── SINGLE-CANDLE PATTERNS ──────────────────────────────────────────────

    # 1. Hammer (bullish reversal at bottom)
    #    Small body near top, long lower wick >= 2x body, tiny upper wick
    if (body2 > 0 and lw2 >= 2.0 * body2 and uw2 <= 0.3 * body2
            and rng2 >= 0.5 * atr):
        strength = 72.0
        if _is_bullish(o2, c2):
            strength = 75.0
        if near_support:
            strength = min(strength + 5.0, 80.0)
        results.append(PatternResult(
            name="Hammer", direction="LONG", strength=strength,
            key_level=l2,
            description="Long lower wick shows buyers rejected lower prices"
        ))

    # 2. Inverted Hammer (bullish reversal at bottom)
    #    Small body near bottom, long upper wick >= 2x body
    if (body2 > 0 and uw2 >= 2.0 * body2 and lw2 <= 0.3 * body2
            and rng2 >= 0.5 * atr and _is_bearish(o1, c1)):
        strength = 70.0
        if near_support:
            strength = 74.0
        results.append(PatternResult(
            name="Inverted Hammer", direction="LONG", strength=strength,
            key_level=h2,
            description="Long upper wick probed higher; buyers entering"
        ))

    # 3. Shooting Star (bearish reversal at top)
    #    Small body near bottom, long upper wick >= 2x body
    if (body2 > 0 and uw2 >= 2.0 * body2 and lw2 <= 0.3 * body2
            and rng2 >= 0.5 * atr and _is_bullish(o1, c1)):
        strength = 72.0
        if near_resistance:
            strength = 76.0
        results.append(PatternResult(
            name="Shooting Star", direction="SHORT", strength=strength,
            key_level=h2,
            description="Long upper wick rejected by sellers at high"
        ))

    # 4. Hanging Man (bearish at top)
    #    Same shape as Hammer but appears in uptrend
    if (body2 > 0 and lw2 >= 2.0 * body2 and uw2 <= 0.3 * body2
            and rng2 >= 0.5 * atr and _is_bullish(o1, c1) and c1 > c0
            and near_resistance):
        strength = 71.0
        results.append(PatternResult(
            name="Hanging Man", direction="SHORT", strength=strength,
            key_level=l2,
            description="Hammer shape at resistance = distribution signal"
        ))

    # 5. Bull Marubozu (full-body bullish candle, no/tiny wicks)
    if (body2 > 0 and uw2 <= 0.05 * body2 and lw2 <= 0.05 * body2
            and _is_bullish(o2, c2) and body2 >= 1.0 * atr):
        strength = 74.0
        if ind and ind.get("volume_ratio", 1.0) >= 2.0:
            strength = 78.0
        results.append(PatternResult(
            name="Bull Marubozu", direction="LONG", strength=strength,
            key_level=c2,
            description="Full-body bull candle — institutions buying aggressively"
        ))

    # 6. Bear Marubozu (full-body bearish candle)
    if (body2 > 0 and uw2 <= 0.05 * body2 and lw2 <= 0.05 * body2
            and _is_bearish(o2, c2) and body2 >= 1.0 * atr):
        strength = 74.0
        if ind and ind.get("volume_ratio", 1.0) >= 2.0:
            strength = 78.0
        results.append(PatternResult(
            name="Bear Marubozu", direction="SHORT", strength=strength,
            key_level=c2,
            description="Full-body bear candle — institutions selling aggressively"
        ))

    # 7. Doji (open ≈ close, indecision)
    if rng2 > 0 and body2 / rng2 < 0.1 and rng2 >= 0.4 * atr:
        # Context determines direction: after uptrend = SHORT, after downtrend = LONG
        if _is_bullish(o1, c1) and c1 > c0:
            dir_, strength = "SHORT", 65.0
        elif _is_bearish(o1, c1) and c1 < c0:
            dir_, strength = "LONG", 65.0
        else:
            dir_, strength = "NEUTRAL", 62.0
        results.append(PatternResult(
            name="Doji", direction=dir_, strength=strength,
            key_level=c2,
            description="Open equals close — market indecision / potential reversal"
        ))

    # 8. Dragonfly Doji (open=close=high, long lower wick — bullish)
    if (rng2 > 0 and body2 / rng2 < 0.1 and lw2 >= 0.7 * rng2 and uw2 < 0.1 * rng2):
        strength = 72.0
        if near_support:
            strength = 76.0
        results.append(PatternResult(
            name="Dragonfly Doji", direction="LONG", strength=strength,
            key_level=l2,
            description="Long lower wick on Doji = strong buyer rejection at lows"
        ))

    # 9. Gravestone Doji (open=close=low, long upper wick — bearish)
    if (rng2 > 0 and body2 / rng2 < 0.1 and uw2 >= 0.7 * rng2 and lw2 < 0.1 * rng2):
        strength = 72.0
        if near_resistance:
            strength = 76.0
        results.append(PatternResult(
            name="Gravestone Doji", direction="SHORT", strength=strength,
            key_level=h2,
            description="Long upper wick on Doji = strong seller rejection at highs"
        ))

    # ── TWO-CANDLE PATTERNS ────────────────────────────────────────────────

    # 10. Bullish Engulfing (most powerful reversal signal)
    if (_is_bearish(o1, c1) and _is_bullish(o2, c2)
            and o2 <= c1 and c2 >= o1
            and body2 > body1 * 1.1):
        strength = 86.0
        if near_support:
            strength = 88.0
        if ind and ind.get("volume_ratio", 1.0) >= 2.0:
            strength = min(strength + 3.0, 92.0)
        results.append(PatternResult(
            name="Bullish Engulfing", direction="LONG", strength=strength,
            key_level=l2,
            description="Large bull candle fully engulfs prior bear candle — buyers overwhelm sellers"
        ))

    # 11. Bearish Engulfing (most powerful reversal signal)
    if (_is_bullish(o1, c1) and _is_bearish(o2, c2)
            and o2 >= c1 and c2 <= o1
            and body2 > body1 * 1.1):
        strength = 86.0
        if near_resistance:
            strength = 88.0
        if ind and ind.get("volume_ratio", 1.0) >= 2.0:
            strength = min(strength + 3.0, 92.0)
        results.append(PatternResult(
            name="Bearish Engulfing", direction="SHORT", strength=strength,
            key_level=h2,
            description="Large bear candle fully engulfs prior bull candle — sellers overwhelm buyers"
        ))

    # 12. Bullish Harami (small bull inside large bear)
    if (_is_bearish(o1, c1) and _is_bullish(o2, c2)
            and o2 > c1 and c2 < o1
            and body2 < body1 * 0.6):
        strength = 70.0
        results.append(PatternResult(
            name="Bullish Harami", direction="LONG", strength=strength,
            key_level=c2,
            description="Small bull candle inside prior bear — selling momentum fading"
        ))

    # 13. Bearish Harami (small bear inside large bull)
    if (_is_bullish(o1, c1) and _is_bearish(o2, c2)
            and o2 < c1 and c2 > o1
            and body2 < body1 * 0.6):
        strength = 70.0
        results.append(PatternResult(
            name="Bearish Harami", direction="SHORT", strength=strength,
            key_level=c2,
            description="Small bear candle inside prior bull — buying momentum fading"
        ))

    # 14. Piercing Line (bullish reversal)
    #     Day 1 = large bear, Day 2 = bull opening below close[1] and closing past midpoint of day1
    mid1 = (o1 + c1) / 2
    if (_is_bearish(o1, c1) and _is_bullish(o2, c2)
            and o2 < c1 and c2 > mid1
            and body1 >= 0.8 * atr):
        strength = 74.0
        results.append(PatternResult(
            name="Piercing Line", direction="LONG", strength=strength,
            key_level=c2,
            description="Bull candle pierces more than 50% of prior bear body — buyers stepping in"
        ))

    # 15. Dark Cloud Cover (bearish reversal)
    mid1_bull = (o1 + c1) / 2
    if (_is_bullish(o1, c1) and _is_bearish(o2, c2)
            and o2 > c1 and c2 < mid1_bull
            and body1 >= 0.8 * atr):
        strength = 74.0
        results.append(PatternResult(
            name="Dark Cloud Cover", direction="SHORT", strength=strength,
            key_level=c2,
            description="Bear candle closes below 50% of prior bull — sellers taking control"
        ))

    # 16. Tweezer Bottom (two candles with matching lows — bullish)
    if (abs(l2 - l1) / max(atr, 0.001) < 0.15
            and _is_bearish(o1, c1) and _is_bullish(o2, c2)
            and rng1 >= 0.5 * atr and rng2 >= 0.5 * atr):
        strength = 73.0
        if near_support:
            strength = 77.0
        results.append(PatternResult(
            name="Tweezer Bottom", direction="LONG", strength=strength,
            key_level=min(l1, l2),
            description="Two candles share same low — strong support zone identified"
        ))

    # 17. Tweezer Top (two candles with matching highs — bearish)
    if (abs(h2 - h1) / max(atr, 0.001) < 0.15
            and _is_bullish(o1, c1) and _is_bearish(o2, c2)
            and rng1 >= 0.5 * atr and rng2 >= 0.5 * atr):
        strength = 73.0
        if near_resistance:
            strength = 77.0
        results.append(PatternResult(
            name="Tweezer Top", direction="SHORT", strength=strength,
            key_level=max(h1, h2),
            description="Two candles share same high — strong resistance zone identified"
        ))

    # ── THREE-CANDLE PATTERNS ─────────────────────────────────────────────

    # 18. Morning Star (bullish, 3-candle)
    #     Large bear → small body (star) → large bull
    if (len(df) >= 3
            and _is_bearish(o0, c0) and body0 >= 0.8 * atr
            and body1 <= 0.4 * body0
            and _is_bullish(o2, c2) and body2 >= 0.8 * atr
            and c2 > (o0 + c0) / 2):
        strength = 91.0
        if ind and ind.get("volume_ratio", 1.0) >= 1.5:
            strength = min(strength + 2.0, 95.0)
        results.append(PatternResult(
            name="Morning Star", direction="LONG", strength=strength,
            key_level=min(l0, l1, l2),
            description="3-candle bullish reversal: bear exhaustion + doji + bull impulse"
        ))

    # 19. Evening Star (bearish, 3-candle)
    if (len(df) >= 3
            and _is_bullish(o0, c0) and body0 >= 0.8 * atr
            and body1 <= 0.4 * body0
            and _is_bearish(o2, c2) and body2 >= 0.8 * atr
            and c2 < (o0 + c0) / 2):
        strength = 91.0
        if ind and ind.get("volume_ratio", 1.0) >= 1.5:
            strength = min(strength + 2.0, 95.0)
        results.append(PatternResult(
            name="Evening Star", direction="SHORT", strength=strength,
            key_level=max(h0, h1, h2),
            description="3-candle bearish reversal: bull exhaustion + doji + bear impulse"
        ))

    # 20. Three White Soldiers (3 consecutive strong bull candles)
    if (len(df) >= 3
            and all(_is_bullish(o[i], c[i]) for i in [i0, i1, i2])
            and all(_candle_body(o[i], c[i]) >= 0.7 * atr for i in [i0, i1, i2])
            and c2 > c1 > c0
            and _lower_wick(o2, l2, c2) <= 0.3 * body2
            and _lower_wick(o1, l1, c1) <= 0.3 * body1):
        strength = 83.0
        if ind and ind.get("volume_ratio", 1.0) >= 1.5:
            strength = 86.0
        results.append(PatternResult(
            name="Three White Soldiers", direction="LONG", strength=strength,
            key_level=c2,
            description="3 consecutive strong bull candles — sustained institutional buying"
        ))

    # 21. Three Black Crows (3 consecutive strong bear candles)
    if (len(df) >= 3
            and all(_is_bearish(o[i], c[i]) for i in [i0, i1, i2])
            and all(_candle_body(o[i], c[i]) >= 0.7 * atr for i in [i0, i1, i2])
            and c2 < c1 < c0
            and _upper_wick(o2, h2, c2) <= 0.3 * body2
            and _upper_wick(o1, h1, c1) <= 0.3 * body1):
        strength = 83.0
        if ind and ind.get("volume_ratio", 1.0) >= 1.5:
            strength = 86.0
        results.append(PatternResult(
            name="Three Black Crows", direction="SHORT", strength=strength,
            key_level=c2,
            description="3 consecutive strong bear candles — sustained institutional selling"
        ))

    return results


# ===========================================================================
# 2. CHART PATTERNS (requires 20+ bars)
# ===========================================================================

def detect_chart_patterns(df: pd.DataFrame,
                           ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Detect major chart patterns on the given DataFrame (needs 20+ bars).
    Uses swing high/low detection across the full window.
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 20:
        return results

    o  = df["open"].values.astype(float)
    h  = df["high"].values.astype(float)
    l  = df["low"].values.astype(float)
    c  = df["close"].values.astype(float)
    v  = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(c))
    n  = len(c)
    atr = _compute_atr(h, l, c, 14)
    if atr <= 0:
        atr = c[-1] * 0.01

    cur_price = c[-1]
    vol_ratio = ind.get("volume_ratio", 1.0) if ind else 1.0

    lb = 3
    sh_idx, sl_idx = _swing_highs_lows(h, l, lookback=lb)

    # ── 1. Double Bottom (W pattern) ────────────────────────────────────────
    if len(sl_idx) >= 2:
        b1_i, b2_i = sl_idx[-2], sl_idx[-1]
        b1, b2 = l[b1_i], l[b2_i]
        if (abs(b1 - b2) / max(atr, 0.001) < 2.0     # similar lows
                and b2_i > b1_i + 5                    # separated by bars
                and cur_price > max(h[b1_i:b2_i + 1])  # neckline break
                and b1 < c[0] * 1.5):                  # not at an extreme
            neck = max(h[b1_i:b2_i + 1])
            strength = 82.0
            if vol_ratio >= 1.5:
                strength = 86.0
            results.append(PatternResult(
                name="Double Bottom (W)", direction="LONG", strength=strength,
                key_level=neck,
                description="Two matching lows with neckline break — most reliable reversal"
            ))

    # ── 2. Double Top (M pattern) ────────────────────────────────────────────
    if len(sh_idx) >= 2:
        t1_i, t2_i = sh_idx[-2], sh_idx[-1]
        t1, t2 = h[t1_i], h[t2_i]
        if (abs(t1 - t2) / max(atr, 0.001) < 2.0
                and t2_i > t1_i + 5
                and cur_price < min(l[t1_i:t2_i + 1])
                and t1 > c[-1] * 0.5):
            neck = min(l[t1_i:t2_i + 1])
            strength = 82.0
            if vol_ratio >= 1.5:
                strength = 86.0
            results.append(PatternResult(
                name="Double Top (M)", direction="SHORT", strength=strength,
                key_level=neck,
                description="Two matching highs with neckline break — bearish reversal"
            ))

    # ── 3. Head & Shoulders (bearish) ───────────────────────────────────────
    if len(sh_idx) >= 3:
        ls_i, hd_i, rs_i = sh_idx[-3], sh_idx[-2], sh_idx[-1]
        ls, hd, rs = h[ls_i], h[hd_i], h[rs_i]
        if (hd > ls * 1.02 and hd > rs * 1.02          # head is tallest
                and abs(ls - rs) / max(atr, 0.001) < 3.0  # shoulders roughly equal
                and cur_price < min(l[ls_i:rs_i + 1])):   # neckline break
            neck = min(l[ls_i:rs_i + 1])
            strength = 81.0
            if vol_ratio >= 1.5:
                strength = 84.0
            results.append(PatternResult(
                name="Head & Shoulders", direction="SHORT", strength=strength,
                key_level=neck,
                description="Classic H&S with neckline break — high-reliability bearish reversal"
            ))

    # ── 4. Inverse Head & Shoulders (bullish) ───────────────────────────────
    if len(sl_idx) >= 3:
        ls_i, hd_i, rs_i = sl_idx[-3], sl_idx[-2], sl_idx[-1]
        ls, hd, rs = l[ls_i], l[hd_i], l[rs_i]
        if (hd < ls * 0.98 and hd < rs * 0.98
                and abs(ls - rs) / max(atr, 0.001) < 3.0
                and cur_price > max(h[ls_i:rs_i + 1])):
            neck = max(h[ls_i:rs_i + 1])
            strength = 81.0
            if vol_ratio >= 1.5:
                strength = 84.0
            results.append(PatternResult(
                name="Inverse Head & Shoulders", direction="LONG", strength=strength,
                key_level=neck,
                description="Inverse H&S with neckline break — high-reliability bullish reversal"
            ))

    # ── 5. Ascending Triangle (flat top, rising bottom) ─────────────────────
    if n >= 30 and len(sh_idx) >= 3 and len(sl_idx) >= 3:
        top_highs = h[sh_idx[-3:]]
        bot_lows  = l[sl_idx[-3:]]
        flat_top = np.std(top_highs) / max(np.mean(top_highs), 0.001) < 0.005
        rising_bot = bot_lows[-1] > bot_lows[0] * 1.003
        if flat_top and rising_bot:
            resistance = np.mean(top_highs)
            if cur_price > resistance * 1.001:
                strength = 78.0
                if vol_ratio >= 1.5:
                    strength = 82.0
                results.append(PatternResult(
                    name="Ascending Triangle Breakout", direction="LONG",
                    strength=strength, key_level=resistance,
                    description="Flat resistance + rising support — bullish breakout"
                ))
            else:
                results.append(PatternResult(
                    name="Ascending Triangle (Building)", direction="LONG",
                    strength=65.0, key_level=resistance,
                    description="Ascending triangle forming — watch for breakout above resistance"
                ))

    # ── 6. Descending Triangle (flat bottom, falling top) ───────────────────
    if n >= 30 and len(sh_idx) >= 3 and len(sl_idx) >= 3:
        top_highs2 = h[sh_idx[-3:]]
        bot_lows2  = l[sl_idx[-3:]]
        flat_bot = np.std(bot_lows2) / max(np.mean(bot_lows2), 0.001) < 0.005
        falling_top = top_highs2[-1] < top_highs2[0] * 0.997
        if flat_bot and falling_top:
            support = np.mean(bot_lows2)
            if cur_price < support * 0.999:
                strength = 78.0
                if vol_ratio >= 1.5:
                    strength = 82.0
                results.append(PatternResult(
                    name="Descending Triangle Breakdown", direction="SHORT",
                    strength=strength, key_level=support,
                    description="Flat support + falling resistance — bearish breakdown"
                ))
            else:
                results.append(PatternResult(
                    name="Descending Triangle (Building)", direction="SHORT",
                    strength=65.0, key_level=support,
                    description="Descending triangle forming — watch for breakdown below support"
                ))

    # ── 7. Symmetrical Triangle ──────────────────────────────────────────────
    if n >= 30 and len(sh_idx) >= 3 and len(sl_idx) >= 3:
        top_h = h[sh_idx[-3:]]
        bot_l = l[sl_idx[-3:]]
        falling_top2  = top_h[-1] < top_h[0] * 0.998
        rising_bot2   = bot_l[-1] > bot_l[0] * 1.002
        if falling_top2 and rising_bot2:
            apex_top = np.mean(top_h[-2:])
            apex_bot = np.mean(bot_l[-2:])
            mid_apex = (apex_top + apex_bot) / 2
            if cur_price > apex_top:
                results.append(PatternResult(
                    name="Symmetrical Triangle Breakout (Bull)",
                    direction="LONG", strength=76.0, key_level=apex_top,
                    description="Symmetrical triangle resolved bullishly"
                ))
            elif cur_price < apex_bot:
                results.append(PatternResult(
                    name="Symmetrical Triangle Breakdown (Bear)",
                    direction="SHORT", strength=76.0, key_level=apex_bot,
                    description="Symmetrical triangle resolved bearishly"
                ))
            else:
                results.append(PatternResult(
                    name="Symmetrical Triangle (Building)",
                    direction="NEUTRAL", strength=62.0, key_level=mid_apex,
                    description="Coiling — breakout imminent; direction not yet determined"
                ))

    # ── 8. Bull Flag (pole + tight consolidation) ────────────────────────────
    if n >= 25:
        # Pole: strong move up in last 8-15 bars followed by tight channel
        pole_end = max(20, n - 20)
        pole_slice = c[n - 20: n - 10]
        cons_slice = c[n - 10:]
        if len(pole_slice) >= 5 and len(cons_slice) >= 5:
            pole_move = (pole_slice[-1] - pole_slice[0]) / max(pole_slice[0], 0.001)
            cons_std  = np.std(cons_slice) / max(np.mean(cons_slice), 0.001)
            if pole_move > 0.03 and cons_std < 0.01:
                strength = 78.0
                if cur_price > max(c[n - 10: n - 1]):
                    strength = 82.0
                results.append(PatternResult(
                    name="Bull Flag", direction="LONG", strength=strength,
                    key_level=max(c[n - 10: n - 1]),
                    description="Strong pole up + tight consolidation = continuation long"
                ))

    # ── 9. Bear Flag ─────────────────────────────────────────────────────────
    if n >= 25:
        pole_slice2 = c[n - 20: n - 10]
        cons_slice2 = c[n - 10:]
        if len(pole_slice2) >= 5 and len(cons_slice2) >= 5:
            pole_move2 = (pole_slice2[0] - pole_slice2[-1]) / max(pole_slice2[0], 0.001)
            cons_std2  = np.std(cons_slice2) / max(np.mean(cons_slice2), 0.001)
            if pole_move2 > 0.03 and cons_std2 < 0.01:
                strength = 78.0
                if cur_price < min(c[n - 10: n - 1]):
                    strength = 82.0
                results.append(PatternResult(
                    name="Bear Flag", direction="SHORT", strength=strength,
                    key_level=min(c[n - 10: n - 1]),
                    description="Strong pole down + tight consolidation = continuation short"
                ))

    # ── 10. Cup & Handle ─────────────────────────────────────────────────────
    if n >= 40:
        # Cup: broad rounded bottom in first 2/3, handle in last 1/3
        cup_slice = c[:int(n * 2 / 3)]
        handle_slice = c[int(n * 2 / 3):]
        cup_left  = float(np.mean(cup_slice[:5]))
        cup_right = float(np.mean(cup_slice[-5:]))
        cup_bot   = float(np.min(cup_slice))
        handle_high = float(np.max(handle_slice))
        handle_low  = float(np.min(handle_slice))
        is_cup  = (abs(cup_left - cup_right) / max(cup_right, 0.001) < 0.05
                   and cup_bot < cup_left * 0.92)
        is_handle = (handle_high < cup_right * 1.02
                     and handle_low > cup_right * 0.96)
        if is_cup and is_handle and cur_price > cup_right * 1.005:
            strength = 92.0
            if vol_ratio >= 2.0:
                strength = 95.0
            results.append(PatternResult(
                name="Cup & Handle", direction="LONG", strength=strength,
                key_level=cup_right,
                description="Bullish continuation: rounded cup + handle breakout"
            ))

    # ── 11. Rising Wedge (bearish) ───────────────────────────────────────────
    if n >= 25 and len(sh_idx) >= 3 and len(sl_idx) >= 3:
        wedge_highs = h[sh_idx[-3:]]
        wedge_lows  = l[sl_idx[-3:]]
        both_rising = wedge_highs[-1] > wedge_highs[0] and wedge_lows[-1] > wedge_lows[0]
        narrowing   = ((wedge_highs[-1] - wedge_lows[-1]) <
                       (wedge_highs[0] - wedge_lows[0]) * 0.85)
        low_faster  = ((wedge_lows[-1] - wedge_lows[0]) >
                       (wedge_highs[-1] - wedge_highs[0]) * 0.9)
        if both_rising and narrowing and low_faster:
            support_line = wedge_lows[-1]
            if cur_price < support_line * 0.999:
                results.append(PatternResult(
                    name="Rising Wedge Breakdown", direction="SHORT",
                    strength=79.0, key_level=support_line,
                    description="Both highs and lows rising but converging — bearish breakdown"
                ))
            else:
                results.append(PatternResult(
                    name="Rising Wedge (Warning)", direction="SHORT",
                    strength=66.0, key_level=support_line,
                    description="Rising wedge forming — watch for breakdown"
                ))

    # ── 12. Falling Wedge (bullish) ──────────────────────────────────────────
    if n >= 25 and len(sh_idx) >= 3 and len(sl_idx) >= 3:
        wedge_highs2 = h[sh_idx[-3:]]
        wedge_lows2  = l[sl_idx[-3:]]
        both_falling = wedge_highs2[-1] < wedge_highs2[0] and wedge_lows2[-1] < wedge_lows2[0]
        narrowing2   = ((wedge_highs2[-1] - wedge_lows2[-1]) <
                        (wedge_highs2[0] - wedge_lows2[0]) * 0.85)
        high_faster  = ((wedge_highs2[0] - wedge_highs2[-1]) >
                        (wedge_lows2[0] - wedge_lows2[-1]) * 0.9)
        if both_falling and narrowing2 and high_faster:
            resist_line = wedge_highs2[-1]
            if cur_price > resist_line * 1.001:
                results.append(PatternResult(
                    name="Falling Wedge Breakout", direction="LONG",
                    strength=79.0, key_level=resist_line,
                    description="Both highs and lows falling but converging — bullish breakout"
                ))
            else:
                results.append(PatternResult(
                    name="Falling Wedge (Building)", direction="LONG",
                    strength=66.0, key_level=resist_line,
                    description="Falling wedge forming — watch for upside breakout"
                ))

    return results


# ===========================================================================
# 3. SMART MONEY CONCEPTS (SMC)
# ===========================================================================

def detect_smc_patterns(df: pd.DataFrame,
                         ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Smart Money Concepts: FVG, Order Block, Liquidity Sweep, BOS, CHoCH.
    These are the most powerful modern institutional trading signals.
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 10:
        return results

    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(c))
    n = len(c)
    atr = _compute_atr(h, l, c, 14)
    if atr <= 0:
        atr = c[-1] * 0.01

    cur_price = c[-1]
    vol_ratio = ind.get("volume_ratio", 1.0) if ind else 1.0

    # ── 1. Fair Value Gap (FVG) ───────────────────────────────────────────────
    # 3-candle imbalance: Bullish FVG = low[i] > high[i-2]
    # Price left a gap between candle i-2 high and candle i low (institutions moved it fast)
    for i in range(2, min(n, 10)):
        idx = n - 1 - i
        if idx < 2:
            break
        # Bullish FVG: gap between [idx-2].high and [idx].low
        if l[idx] > h[idx - 2]:
            gap_size = l[idx] - h[idx - 2]
            gap_mid  = (l[idx] + h[idx - 2]) / 2
            if gap_size >= 0.3 * atr:
                # Is current price filling the FVG (retest)?
                in_fvg = h[idx - 2] <= cur_price <= l[idx]
                confirmed = in_fvg and c[-1] > c[-2]
                strength = 87.0 if confirmed else 71.0
                results.append(PatternResult(
                    name=f"Bullish FVG {'(Confirmed)' if confirmed else '(Unconfirmed)'}",
                    direction="LONG", strength=strength,
                    key_level=gap_mid,
                    description=f"3-candle imbalance zone at {gap_mid:.2f} — institutions left gap, price tends to fill"
                ))
                break  # report most recent only

        # Bearish FVG: gap between [idx-2].low and [idx].high
        if h[idx] < l[idx - 2]:
            gap_size = l[idx - 2] - h[idx]
            gap_mid  = (l[idx - 2] + h[idx]) / 2
            if gap_size >= 0.3 * atr:
                in_fvg = h[idx] <= cur_price <= l[idx - 2]
                confirmed = in_fvg and c[-1] < c[-2]
                strength = 87.0 if confirmed else 71.0
                results.append(PatternResult(
                    name=f"Bearish FVG {'(Confirmed)' if confirmed else '(Unconfirmed)'}",
                    direction="SHORT", strength=strength,
                    key_level=gap_mid,
                    description=f"3-candle bearish imbalance at {gap_mid:.2f} — price tends to fill gap then continue down"
                ))
                break

    # ── 2. Order Block (institutional accumulation zone) ─────────────────────
    # Bullish OB: last bearish candle before a strong bullish impulse
    # Bearish OB: last bullish candle before a strong bearish impulse
    impulse_thresh = 2.0 * atr
    for i in range(3, min(n - 2, 20)):
        idx = n - i
        if idx < 1:
            break
        # Bullish OB: candle[idx] is bearish, candle[idx+1..] show bullish impulse
        impulse = c[idx + 1] - c[idx] if idx + 1 < n else 0
        if (_is_bearish(o[idx], c[idx])
                and impulse >= impulse_thresh
                and o[idx] <= cur_price <= h[idx]):   # price retesting OB zone
            strength = 76.0
            if vol_ratio >= 2.0:
                strength = 80.0
            results.append(PatternResult(
                name="Bullish Order Block Retest",
                direction="LONG", strength=strength,
                key_level=(o[idx] + h[idx]) / 2,
                description=f"Price retesting institutional accumulation zone at {(o[idx]+h[idx])/2:.2f}"
            ))
            break

        # Bearish OB: candle[idx] is bullish, followed by bearish impulse
        bearish_impulse = c[idx] - c[idx + 1] if idx + 1 < n else 0
        if (_is_bullish(o[idx], c[idx])
                and bearish_impulse >= impulse_thresh
                and l[idx] <= cur_price <= c[idx]):   # price retesting OB zone
            strength = 76.0
            if vol_ratio >= 2.0:
                strength = 80.0
            results.append(PatternResult(
                name="Bearish Order Block Retest",
                direction="SHORT", strength=strength,
                key_level=(l[idx] + c[idx]) / 2,
                description=f"Price retesting institutional distribution zone at {(l[idx]+c[idx])/2:.2f}"
            ))
            break

    # ── 3. Liquidity Sweep (most powerful reversal signal) ───────────────────
    # Bull sweep: price wick below recent swing low then reverses ABOVE it
    # Bear sweep: price wick above recent swing high then reverses BELOW it
    if n >= 10:
        recent_low  = min(l[-10:-1])
        recent_high = max(h[-10:-1])

        # Bull liquidity sweep: current candle wicked below recent low, closed above it
        if (l[-1] < recent_low           # wick below
                and c[-1] > recent_low   # close back above
                and (recent_low - l[-1]) >= 0.3 * atr):  # meaningful wick
            strength = 90.0
            if c[-1] > c[-2]:
                strength = 93.0
            results.append(PatternResult(
                name="Bullish Liquidity Sweep",
                direction="LONG", strength=strength,
                key_level=recent_low,
                description=f"Price swept stops below {recent_low:.2f} then reversed — institutions trapped shorts and reversed long"
            ))

        # Bear liquidity sweep: current candle wicked above recent high, closed below it
        if (h[-1] > recent_high          # wick above
                and c[-1] < recent_high  # close back below
                and (h[-1] - recent_high) >= 0.3 * atr):
            strength = 90.0
            if c[-1] < c[-2]:
                strength = 93.0
            results.append(PatternResult(
                name="Bearish Liquidity Sweep",
                direction="SHORT", strength=strength,
                key_level=recent_high,
                description=f"Price swept stops above {recent_high:.2f} then reversed — institutions trapped longs and reversed short"
            ))

    # ── 4. Break of Structure (BOS) ──────────────────────────────────────────
    # Bullish BOS: price cleanly breaks above last confirmed swing high
    # Bearish BOS: price cleanly breaks below last confirmed swing low
    sh_idx, sl_idx = _swing_highs_lows(h, l, lookback=4)

    if sh_idx:
        last_sh = h[sh_idx[-1]]
        if cur_price > last_sh * 1.001 and vol_ratio >= 1.2:
            results.append(PatternResult(
                name="Bullish BOS (Break of Structure)",
                direction="LONG", strength=87.0,
                key_level=last_sh,
                description=f"Price broke above swing high {last_sh:.2f} — trend confirmed bullish"
            ))

    if sl_idx:
        last_sl = l[sl_idx[-1]]
        if cur_price < last_sl * 0.999 and vol_ratio >= 1.2:
            results.append(PatternResult(
                name="Bearish BOS (Break of Structure)",
                direction="SHORT", strength=87.0,
                key_level=last_sl,
                description=f"Price broke below swing low {last_sl:.2f} — trend confirmed bearish"
            ))

    # ── 5. Change of Character (CHoCH) ───────────────────────────────────────
    # In uptrend (HH, HL): first lower low = CHoCH bearish reversal
    # In downtrend (LL, LH): first higher high = CHoCH bullish reversal
    if len(sl_idx) >= 3:
        ll_prev, ll_mid, ll_last = l[sl_idx[-3]], l[sl_idx[-2]], l[sl_idx[-1]]
        in_uptrend = ll_mid > ll_prev and ll_mid > ll_prev  # higher lows pattern
        if in_uptrend and ll_last < ll_mid:  # first lower low = CHoCH
            results.append(PatternResult(
                name="CHoCH Bearish (Change of Character)",
                direction="SHORT", strength=87.0,
                key_level=ll_last,
                description="Uptrend higher-lows pattern broken — first lower low signals trend reversal"
            ))

    if len(sh_idx) >= 3:
        hh_prev, hh_mid, hh_last = h[sh_idx[-3]], h[sh_idx[-2]], h[sh_idx[-1]]
        in_downtrend = hh_mid < hh_prev
        if in_downtrend and hh_last > hh_mid:  # first higher high = CHoCH
            results.append(PatternResult(
                name="CHoCH Bullish (Change of Character)",
                direction="LONG", strength=87.0,
                key_level=hh_last,
                description="Downtrend lower-highs pattern broken — first higher high signals trend reversal"
            ))

    return results


# ===========================================================================
# 4. WYCKOFF METHOD
# ===========================================================================

def detect_wyckoff(df: pd.DataFrame,
                   ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Wyckoff accumulation/distribution signals.
    Spring, Upthrust, Accumulation Phase, Distribution Phase.
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 30:
        return results

    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(c))
    n = len(c)
    atr = _compute_atr(h, l, c, 14)
    if atr <= 0:
        atr = c[-1] * 0.01

    cur_price = c[-1]
    vol_ratio = ind.get("volume_ratio", 1.0) if ind else 1.0

    # Define a "range" as the middle 20-40 bars
    range_start = max(0, n - 40)
    range_end   = max(10, n - 5)
    range_h = h[range_start:range_end]
    range_l = l[range_start:range_end]
    range_v = v[range_start:range_end]

    support    = float(np.percentile(range_l, 15))
    resistance = float(np.percentile(range_h, 85))
    range_size = resistance - support

    if range_size <= 0:
        return results

    # Measure range volume vs overall volume
    range_avg_vol = float(np.mean(range_v))
    overall_avg_v = float(np.mean(v))
    vol_declining = range_avg_vol < overall_avg_v * 0.85

    # ── 1. Wyckoff Spring (STRONGEST buy signal) ─────────────────────────────
    # Price briefly drops below support on high volume, then snaps back above
    recent_lows  = l[-5:]
    recent_close = c[-3:]
    if (min(recent_lows) < support - 0.1 * atr  # wick below support
            and cur_price > support              # close back above
            and vol_ratio >= 1.5):               # volume spike confirms
        strength = 93.0
        if vol_ratio >= 2.5:
            strength = 95.0
        results.append(PatternResult(
            name="Wyckoff Spring",
            direction="LONG", strength=strength,
            key_level=support,
            description=f"Price briefly dipped below range support {support:.2f} on high volume then snapped back — institutional accumulation complete, markup starting"
        ))

    # ── 2. Wyckoff Upthrust (STRONGEST sell signal) ──────────────────────────
    # Price briefly breaks above resistance on high volume, then reverses
    recent_highs = h[-5:]
    if (max(recent_highs) > resistance + 0.1 * atr  # wick above resistance
            and cur_price < resistance               # close back below
            and vol_ratio >= 1.5):
        strength = 93.0
        if vol_ratio >= 2.5:
            strength = 95.0
        results.append(PatternResult(
            name="Wyckoff Upthrust",
            direction="SHORT", strength=strength,
            key_level=resistance,
            description=f"Price briefly pushed above range resistance {resistance:.2f} on high volume then reversed — distribution complete, markdown starting"
        ))

    # ── 3. Wyckoff Accumulation Phase ────────────────────────────────────────
    # Tight range near lows with declining volume
    in_lower_range = cur_price < support + 0.3 * range_size
    tight_range_bars = c[-15:]
    range_pct = (max(tight_range_bars) - min(tight_range_bars)) / max(np.mean(tight_range_bars), 0.001) * 100
    if in_lower_range and vol_declining and range_pct < 3.0:
        results.append(PatternResult(
            name="Wyckoff Accumulation Phase",
            direction="LONG", strength=80.0,
            key_level=support,
            description=f"Tight {range_pct:.1f}% range near lows with declining volume — institutions quietly accumulating"
        ))

    # ── 4. Wyckoff Distribution Phase ────────────────────────────────────────
    # Tight range near highs with declining volume
    in_upper_range = cur_price > resistance - 0.3 * range_size
    if in_upper_range and vol_declining and range_pct < 3.0:
        results.append(PatternResult(
            name="Wyckoff Distribution Phase",
            direction="SHORT", strength=80.0,
            key_level=resistance,
            description=f"Tight {range_pct:.1f}% range near highs with declining volume — institutions quietly distributing"
        ))

    return results


# ===========================================================================
# 5. FIBONACCI ANALYSIS
# ===========================================================================

def detect_fibonacci(df: pd.DataFrame,
                     ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Fibonacci retracement and extension levels.
    Detects when current price is near key Fibonacci levels (within 0.5 ATR).
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 20:
        return results

    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(c)
    atr = _compute_atr(h, l, c, 14)
    if atr <= 0:
        atr = c[-1] * 0.01

    cur_price = c[-1]
    tol = 0.5 * atr  # within 0.5 ATR of level

    # Determine swing for fib calculation (last 20-50 bars)
    lookback = min(50, n)
    swing_high = float(np.max(h[-lookback:]))
    swing_low  = float(np.min(l[-lookback:]))
    swing_range = swing_high - swing_low
    if swing_range <= 0:
        return results

    # Identify trend direction based on where current price is
    mid_range = (swing_high + swing_low) / 2
    in_uptrend = cur_price > mid_range   # fib = retracement of upswing
    in_downtrend = not in_uptrend

    # Retracement levels (from swing high in downtrend, swing low in uptrend)
    FIB_RETRACE = {
        "38.2%": 0.382,
        "50.0%": 0.500,
        "61.8%": 0.618,
        "78.6%": 0.786,
    }

    FIB_EXTENSION = {
        "127.2%": 1.272,
        "161.8%": 1.618,
        "261.8%": 2.618,
    }

    if in_uptrend:
        # In uptrend: retracement levels are below swing_high, above swing_low
        for label, ratio in FIB_RETRACE.items():
            level = swing_high - ratio * swing_range
            if abs(cur_price - level) <= tol:
                strength = 87.0 if ratio == 0.618 else (79.0 if ratio in (0.382, 0.500) else 73.0)
                results.append(PatternResult(
                    name=f"Fibonacci Retracement {label} (Uptrend)",
                    direction="LONG", strength=strength,
                    key_level=level,
                    description=f"Price at {label} retracement ({level:.2f}) of upswing — high-probability buy zone"
                ))

        # Extension levels (targets above swing high)
        for label, ratio in FIB_EXTENSION.items():
            level = swing_low + ratio * swing_range
            if abs(cur_price - level) <= tol:
                results.append(PatternResult(
                    name=f"Fibonacci Extension {label} (Target)",
                    direction="NEUTRAL", strength=70.0,
                    key_level=level,
                    description=f"Price reached {label} extension target {level:.2f} — consider partial profit taking"
                ))

    else:
        # In downtrend: retracement levels are above swing_low, below swing_high
        for label, ratio in FIB_RETRACE.items():
            level = swing_low + ratio * swing_range
            if abs(cur_price - level) <= tol:
                strength = 87.0 if ratio == 0.618 else (79.0 if ratio in (0.382, 0.500) else 73.0)
                results.append(PatternResult(
                    name=f"Fibonacci Retracement {label} (Downtrend)",
                    direction="SHORT", strength=strength,
                    key_level=level,
                    description=f"Price at {label} retracement ({level:.2f}) of downswing — high-probability sell zone"
                ))

        # Extension levels (targets below swing low)
        for label, ratio in FIB_EXTENSION.items():
            level = swing_high - ratio * swing_range
            if abs(cur_price - level) <= tol:
                results.append(PatternResult(
                    name=f"Fibonacci Extension {label} (Short Target)",
                    direction="NEUTRAL", strength=70.0,
                    key_level=level,
                    description=f"Price reached {label} extension target {level:.2f} — consider covering shorts"
                ))

    return results


# ===========================================================================
# 6. RSI DIVERGENCE (4 types) + MACD DIVERGENCE (2 types)
# ===========================================================================

def detect_divergence(df: pd.DataFrame,
                       ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Detect RSI and MACD divergence — critical skill for catching reversals.

    RSI types:
      - Bullish Regular: price LL, RSI HL → reversal up
      - Bearish Regular: price HH, RSI LH → reversal down
      - Bullish Hidden:  price HL, RSI LL → uptrend continuation
      - Bearish Hidden:  price LH, RSI HH → downtrend continuation

    MACD types:
      - Bullish: price LL, MACD histogram HL
      - Bearish: price HH, MACD histogram LH
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 30:
        return results

    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(c)

    rsi_arr  = _compute_rsi(c, 14)
    macd_arr = _compute_macd_hist(c)

    # We need at least 2 pivot points, look at last 30 bars
    lookback = min(30, n)
    sh_idx, sl_idx = _swing_highs_lows(
        h[-lookback:], l[-lookback:], lookback=4
    )
    # Adjust indices to be relative to full array
    sh_idx = [i + (n - lookback) for i in sh_idx]
    sl_idx = [i + (n - lookback) for i in sl_idx]

    # Need at least 2 swing lows and 2 swing highs for divergence
    if len(sl_idx) >= 2 and not np.isnan(rsi_arr[sl_idx[-1]]):
        p1_i, p2_i = sl_idx[-2], sl_idx[-1]
        price_p1   = l[p1_i]
        price_p2   = l[p2_i]
        rsi_p1     = rsi_arr[p1_i]
        rsi_p2     = rsi_arr[p2_i]
        if not np.isnan(rsi_p1) and not np.isnan(rsi_p2):
            # Bullish Regular Divergence: price LL, RSI HL
            if price_p2 < price_p1 * 0.998 and rsi_p2 > rsi_p1 + 2:
                results.append(PatternResult(
                    name="RSI Bullish Regular Divergence",
                    direction="LONG", strength=77.0,
                    key_level=l[p2_i],
                    description=f"Price made lower low ({price_p2:.2f} < {price_p1:.2f}) but RSI made higher low ({rsi_p2:.1f} > {rsi_p1:.1f}) — reversal up expected"
                ))

            # Bullish Hidden Divergence: price HL, RSI LL
            if price_p2 > price_p1 * 1.002 and rsi_p2 < rsi_p1 - 2:
                results.append(PatternResult(
                    name="RSI Bullish Hidden Divergence",
                    direction="LONG", strength=76.0,
                    key_level=l[p2_i],
                    description=f"Price made higher low (uptrend) but RSI made lower low — uptrend continuation signal"
                ))

    if len(sh_idx) >= 2 and not np.isnan(rsi_arr[sh_idx[-1]]):
        p1_i, p2_i = sh_idx[-2], sh_idx[-1]
        price_p1   = h[p1_i]
        price_p2   = h[p2_i]
        rsi_p1     = rsi_arr[p1_i]
        rsi_p2     = rsi_arr[p2_i]
        if not np.isnan(rsi_p1) and not np.isnan(rsi_p2):
            # Bearish Regular Divergence: price HH, RSI LH
            if price_p2 > price_p1 * 1.002 and rsi_p2 < rsi_p1 - 2:
                results.append(PatternResult(
                    name="RSI Bearish Regular Divergence",
                    direction="SHORT", strength=77.0,
                    key_level=h[p2_i],
                    description=f"Price made higher high ({price_p2:.2f} > {price_p1:.2f}) but RSI made lower high ({rsi_p2:.1f} < {rsi_p1:.1f}) — reversal down expected"
                ))

            # Bearish Hidden Divergence: price LH, RSI HH
            if price_p2 < price_p1 * 0.998 and rsi_p2 > rsi_p1 + 2:
                results.append(PatternResult(
                    name="RSI Bearish Hidden Divergence",
                    direction="SHORT", strength=76.0,
                    key_level=h[p2_i],
                    description=f"Price made lower high (downtrend) but RSI made higher high — downtrend continuation signal"
                ))

    # MACD Divergence
    if len(sl_idx) >= 2:
        p1_i, p2_i = sl_idx[-2], sl_idx[-1]
        if not np.isnan(macd_arr[p1_i]) and not np.isnan(macd_arr[p2_i]):
            price_p1 = l[p1_i]
            price_p2 = l[p2_i]
            macd_p1  = macd_arr[p1_i]
            macd_p2  = macd_arr[p2_i]
            # Bullish MACD divergence: price LL, histogram HL
            if price_p2 < price_p1 * 0.998 and macd_p2 > macd_p1 + 1e-8:
                results.append(PatternResult(
                    name="MACD Bullish Divergence",
                    direction="LONG", strength=76.0,
                    key_level=l[p2_i],
                    description="Price lower low but MACD histogram higher low — bullish momentum diverging from price"
                ))

    if len(sh_idx) >= 2:
        p1_i, p2_i = sh_idx[-2], sh_idx[-1]
        if not np.isnan(macd_arr[p1_i]) and not np.isnan(macd_arr[p2_i]):
            price_p1 = h[p1_i]
            price_p2 = h[p2_i]
            macd_p1  = macd_arr[p1_i]
            macd_p2  = macd_arr[p2_i]
            # Bearish MACD divergence: price HH, histogram LH
            if price_p2 > price_p1 * 1.002 and macd_p2 < macd_p1 - 1e-8:
                results.append(PatternResult(
                    name="MACD Bearish Divergence",
                    direction="SHORT", strength=76.0,
                    key_level=h[p2_i],
                    description="Price higher high but MACD histogram lower high — bearish momentum diverging from price"
                ))

    return results


# ===========================================================================
# 7. VOLUME PROFILE
# ===========================================================================

def detect_volume_profile(df: pd.DataFrame,
                           ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Volume Profile: POC, Value Area, HVN, LVN.
    Uses price binning over the last N bars to build the volume distribution.
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 20:
        return results

    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(c))
    n = len(c)
    atr = _compute_atr(h, l, c, 14)
    if atr <= 0:
        atr = c[-1] * 0.01

    cur_price = c[-1]

    # Build volume profile: bin the price range into 20 buckets
    lookback = min(50, n)
    price_arr = c[-lookback:]
    vol_arr   = v[-lookback:]
    high_arr  = h[-lookback:]
    low_arr   = l[-lookback:]

    price_min = float(np.min(low_arr))
    price_max = float(np.max(high_arr))
    if price_max <= price_min:
        return results

    num_bins = 20
    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_vol   = np.zeros(num_bins)

    for i in range(len(price_arr)):
        # Distribute each bar's volume proportionally across price range
        bar_lo = low_arr[i]
        bar_hi = high_arr[i]
        bar_v  = vol_arr[i]
        bar_range = bar_hi - bar_lo
        if bar_range <= 0:
            bin_idx = np.searchsorted(bin_edges, price_arr[i], side='right') - 1
            bin_idx = min(bin_idx, num_bins - 1)
            bin_vol[bin_idx] += bar_v
        else:
            for b in range(num_bins):
                overlap_lo = max(bin_edges[b], bar_lo)
                overlap_hi = min(bin_edges[b + 1], bar_hi)
                if overlap_hi > overlap_lo:
                    frac = (overlap_hi - overlap_lo) / bar_range
                    bin_vol[b] += bar_v * frac

    total_vol = np.sum(bin_vol)
    if total_vol <= 0:
        return results

    # Point of Control (POC) — highest volume bin
    poc_bin = int(np.argmax(bin_vol))
    poc_price = (bin_edges[poc_bin] + bin_edges[poc_bin + 1]) / 2

    # Value Area (70% of volume): expand from POC outward
    sorted_vol  = np.sort(bin_vol)[::-1]
    cumvol = 0
    va_bins = []
    vol_target = total_vol * 0.70
    for i in np.argsort(bin_vol)[::-1]:
        cumvol += bin_vol[i]
        va_bins.append(i)
        if cumvol >= vol_target:
            break
    if va_bins:
        val = bin_edges[min(va_bins)]
        vah = bin_edges[max(va_bins) + 1] if max(va_bins) + 1 <= num_bins else bin_edges[-1]
    else:
        val = price_min
        vah = price_max

    # Is current price near POC (strong S/R)
    near_poc = abs(cur_price - poc_price) <= 0.5 * atr
    if near_poc:
        # Direction: if price is above POC → support (LONG), below → resistance (SHORT)
        if cur_price >= poc_price:
            results.append(PatternResult(
                name="Volume POC Support",
                direction="LONG", strength=74.0,
                key_level=poc_price,
                description=f"Price near Point of Control {poc_price:.2f} from above — highest-volume level acts as strong support"
            ))
        else:
            results.append(PatternResult(
                name="Volume POC Resistance",
                direction="SHORT", strength=74.0,
                key_level=poc_price,
                description=f"Price near Point of Control {poc_price:.2f} from below — highest-volume level acts as strong resistance"
            ))

    # Value Area High/Low as S/R
    near_vah = abs(cur_price - vah) <= 0.5 * atr
    near_val = abs(cur_price - val) <= 0.5 * atr
    if near_vah:
        results.append(PatternResult(
            name="Value Area High (VAH) Resistance",
            direction="SHORT", strength=68.0,
            key_level=vah,
            description=f"Price at Value Area High {vah:.2f} — 70% of volume was traded below here"
        ))
    if near_val:
        results.append(PatternResult(
            name="Value Area Low (VAL) Support",
            direction="LONG", strength=68.0,
            key_level=val,
            description=f"Price at Value Area Low {val:.2f} — 70% of volume was traded above here"
        ))

    # High Volume Node (HVN) — strong S/R above/below current price
    hvn_threshold = np.percentile(bin_vol, 75)
    for b in range(num_bins):
        if bin_vol[b] >= hvn_threshold:
            node_price = (bin_edges[b] + bin_edges[b + 1]) / 2
            if abs(cur_price - node_price) > atr and abs(cur_price - node_price) < 3 * atr:
                direction = "LONG" if node_price < cur_price else "SHORT"
                label     = "Support" if node_price < cur_price else "Resistance"
                results.append(PatternResult(
                    name=f"High Volume Node ({label})",
                    direction=direction, strength=67.0,
                    key_level=node_price,
                    description=f"HVN at {node_price:.2f} — high volume = strong {label.lower()}"
                ))
                break  # report only nearest HVN

    # Low Volume Node (LVN) — price moves fast through thin areas
    lvn_threshold = np.percentile(bin_vol, 25)
    for b in range(num_bins):
        if bin_vol[b] <= lvn_threshold:
            node_price = (bin_edges[b] + bin_edges[b + 1]) / 2
            if 0.5 * atr < abs(cur_price - node_price) < 2 * atr:
                results.append(PatternResult(
                    name="Low Volume Node (Fast Move Zone)",
                    direction="NEUTRAL", strength=64.0,
                    key_level=node_price,
                    description=f"LVN at {node_price:.2f} — thin liquidity area, price will accelerate through here"
                ))
                break

    return results


# ===========================================================================
# 8. ELLIOTT WAVE (simplified)
# ===========================================================================

def detect_elliott_wave(df: pd.DataFrame,
                         ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Simplified Elliott Wave: detect Wave 3 (strongest) and Wave 5 exhaustion.
    Wave 3: expanding volume + MACD histogram at new high + price > 161.8% of Wave 1.
    Wave 5 exhaustion: bearish divergence + decreasing volume at new price high.
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 40:
        return results

    h  = df["high"].values.astype(float)
    l  = df["low"].values.astype(float)
    c  = df["close"].values.astype(float)
    v  = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(c))
    n  = len(c)
    atr = _compute_atr(h, l, c, 14)
    macd_arr = _compute_macd_hist(c)
    rsi_arr  = _compute_rsi(c, 14)

    cur_price = c[-1]
    vol_ratio = ind.get("volume_ratio", 1.0) if ind else 1.0

    sh_idx, sl_idx = _swing_highs_lows(h, l, lookback=5)

    # ── Wave 3 detection (long) ───────────────────────────────────────────────
    if len(sl_idx) >= 2 and len(sh_idx) >= 1:
        w1_low  = l[sl_idx[-2]]  # Wave 1 starts at swing low
        w1_high = h[sh_idx[-1]] if sh_idx[-1] > sl_idx[-2] else h[sh_idx[-1]]
        w1_range = w1_high - w1_low
        # Wave 3 target: > 161.8% of Wave 1 from wave 2 low
        w2_low  = l[sl_idx[-1]]  # Wave 2 retraced
        w3_target = w2_low + 1.618 * w1_range
        vol_expanding = vol_ratio >= 1.5
        macd_hi = float(macd_arr[-1]) if not np.isnan(macd_arr[-1]) else 0
        macd_hist_expanding = macd_hi > 0 and macd_hi >= max(
            [x for x in macd_arr[-10:] if not np.isnan(x)] or [0]
        ) * 0.85
        if (cur_price >= w3_target * 0.99
                and vol_expanding
                and macd_hist_expanding
                and w1_range > 2 * atr):
            results.append(PatternResult(
                name="Elliott Wave 3 (Strongest Wave)",
                direction="LONG", strength=84.0,
                key_level=w3_target,
                description=f"Wave 3 impulse detected: price at {cur_price:.2f} > 161.8% W1 target {w3_target:.2f}, expanding volume + MACD momentum"
            ))

    # ── Wave 5 exhaustion (reversal warning) ──────────────────────────────────
    if len(sh_idx) >= 2:
        prev_high_i = sh_idx[-2]
        last_high_i = sh_idx[-1]
        prev_high = h[prev_high_i]
        last_high = h[last_high_i]
        # Wave 5: new price high but volume decreasing and RSI diverging
        vol_declining  = v[-5:].mean() < v[-15:-5].mean() * 0.85
        rsi_at_prev = rsi_arr[prev_high_i] if not np.isnan(rsi_arr[prev_high_i]) else 50
        rsi_at_last = rsi_arr[last_high_i] if not np.isnan(rsi_arr[last_high_i]) else 50
        rsi_diverging  = rsi_at_last < rsi_at_prev - 3 and last_high > prev_high
        if rsi_diverging and vol_declining:
            results.append(PatternResult(
                name="Elliott Wave 5 Exhaustion",
                direction="SHORT", strength=80.0,
                key_level=last_high,
                description=f"New price high at {last_high:.2f} with declining volume and RSI divergence — Wave 5 exhaustion, reversal imminent"
            ))

    return results


# ===========================================================================
# 9. HARMONIC PATTERNS
# ===========================================================================

def detect_harmonic_patterns(df: pd.DataFrame,
                              ind: Optional[Dict] = None) -> List[PatternResult]:
    """
    Harmonic patterns: ABCD, Gartley, Butterfly.
    Uses Fibonacci ratios between swing pivot points.
    """
    results: List[PatternResult] = []
    if df is None or len(df) < 30:
        return results

    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    n = len(c)
    atr = _compute_atr(h, l, c, 14)
    if atr <= 0:
        atr = c[-1] * 0.01

    cur_price = c[-1]
    tol = 0.05   # 5% tolerance on Fibonacci ratios

    sh_idx, sl_idx = _swing_highs_lows(h, l, lookback=4)

    def fib_check(ratio: float, target: float, actual: float) -> bool:
        return abs(actual / max(target, 1e-8) - ratio) <= tol

    # Need 4 alternating pivot points for ABCD / Gartley / Butterfly
    # Build alternating pivots (X, A, B, C, D) from swing highs/lows
    all_pivots = sorted(
        [(i, h[i], "H") for i in sh_idx] + [(i, l[i], "L") for i in sl_idx],
        key=lambda x: x[0]
    )[-8:]  # last 8 pivots

    if len(all_pivots) < 4:
        return results

    # ── ABCD Pattern ──────────────────────────────────────────────────────────
    # Equal BC and CD legs: BC/AB ~ CD/BC ~ 0.618 or 0.786
    if len(all_pivots) >= 4:
        A_i, A_p, A_t = all_pivots[-4]
        B_i, B_p, B_t = all_pivots[-3]
        C_i, C_p, C_t = all_pivots[-2]
        D_i, D_p, D_t = all_pivots[-1]

        AB = abs(B_p - A_p)
        BC = abs(C_p - B_p)
        CD = abs(D_p - C_p)

        if AB > atr and BC > 0 and CD > 0:
            bc_ab_ratio = BC / AB
            cd_bc_ratio = CD / BC
            # Classic ABCD: BC ~ 0.618 of AB, CD ~ 1.272 of BC (or 0.618/1.272 extensions)
            abcd_bull = (A_t == "L" and fib_check(0.618, AB, BC)
                         and fib_check(1.272, BC, CD)
                         and cur_price <= D_p * 1.01)
            abcd_bear = (A_t == "H" and fib_check(0.618, AB, BC)
                         and fib_check(1.272, BC, CD)
                         and cur_price >= D_p * 0.99)
            if abcd_bull:
                results.append(PatternResult(
                    name="ABCD Bullish Harmonic",
                    direction="LONG", strength=75.0,
                    key_level=D_p,
                    description=f"ABCD harmonic at D={D_p:.2f} with BC/AB≈61.8% and CD/BC≈127.2%"
                ))
            elif abcd_bear:
                results.append(PatternResult(
                    name="ABCD Bearish Harmonic",
                    direction="SHORT", strength=75.0,
                    key_level=D_p,
                    description=f"ABCD bearish harmonic at D={D_p:.2f}"
                ))

    # ── Gartley Pattern ────────────────────────────────────────────────────────
    # X→A→B→C→D: B=61.8% XA, D=78.6% XA
    if len(all_pivots) >= 5:
        X_i, X_p, X_t = all_pivots[-5]
        A_i, A_p, A_t = all_pivots[-4]
        B_i, B_p, B_t = all_pivots[-3]
        C_i, C_p, C_t = all_pivots[-2]
        D_i, D_p, D_t = all_pivots[-1]

        XA = abs(A_p - X_p)
        AB = abs(B_p - A_p)
        BC = abs(C_p - B_p)
        CD = abs(D_p - C_p)

        if XA > atr * 2:
            B_retrace = AB / XA   # should be ~61.8%
            D_retrace = abs(D_p - X_p) / XA  # should be ~78.6%

            gartley_bull = (X_t == "L" and A_t == "H"
                            and fib_check(0.618, 1, B_retrace)
                            and fib_check(0.786, 1, D_retrace)
                            and cur_price <= D_p * 1.01)
            gartley_bear = (X_t == "H" and A_t == "L"
                            and fib_check(0.618, 1, B_retrace)
                            and fib_check(0.786, 1, D_retrace)
                            and cur_price >= D_p * 0.99)

            if gartley_bull:
                results.append(PatternResult(
                    name="Gartley Bullish Harmonic",
                    direction="LONG", strength=78.0,
                    key_level=D_p,
                    description=f"Gartley bull at D={D_p:.2f}: B=61.8% XA, D=78.6% XA — high-probability reversal zone"
                ))
            elif gartley_bear:
                results.append(PatternResult(
                    name="Gartley Bearish Harmonic",
                    direction="SHORT", strength=78.0,
                    key_level=D_p,
                    description=f"Gartley bear at D={D_p:.2f}"
                ))

    # ── Butterfly Pattern ──────────────────────────────────────────────────────
    # D extends beyond X: 127.2% or 161.8% of XA
    if len(all_pivots) >= 5:
        X_i, X_p, X_t = all_pivots[-5]
        A_i, A_p, A_t = all_pivots[-4]
        B_i, B_p, B_t = all_pivots[-3]
        C_i, C_p, C_t = all_pivots[-2]
        D_i, D_p, D_t = all_pivots[-1]

        XA = abs(A_p - X_p)
        if XA > atr * 2:
            D_ext = abs(D_p - X_p) / XA  # should be 127.2% or 161.8%
            butterfly_bull = (X_t == "H" and  # D extends beyond X low
                              (fib_check(1.272, 1, D_ext) or fib_check(1.618, 1, D_ext))
                              and D_p < X_p
                              and cur_price <= D_p * 1.01)
            butterfly_bear = (X_t == "L" and  # D extends beyond X high
                              (fib_check(1.272, 1, D_ext) or fib_check(1.618, 1, D_ext))
                              and D_p > X_p
                              and cur_price >= D_p * 0.99)

            if butterfly_bull:
                results.append(PatternResult(
                    name="Butterfly Bullish Harmonic",
                    direction="LONG", strength=79.0,
                    key_level=D_p,
                    description=f"Butterfly bull at D={D_p:.2f}: D extends {D_ext*100:.0f}% of XA beyond X"
                ))
            elif butterfly_bear:
                results.append(PatternResult(
                    name="Butterfly Bearish Harmonic",
                    direction="SHORT", strength=79.0,
                    key_level=D_p,
                    description=f"Butterfly bear at D={D_p:.2f}"
                ))

    return results


# ===========================================================================
# MASTER AGGREGATION
# ===========================================================================

def score_all_patterns(
    results: List[PatternResult],
    long_pts: float = 0.0,
    short_pts: float = 0.0,
) -> Tuple[float, str, List[str]]:
    """
    Aggregate all PatternResult objects into a final (score, direction, top_patterns) tuple.

    Logic:
      1. Tally long_pts from LONG patterns, short_pts from SHORT patterns
         (adds to any pre-existing indicator-based long_pts / short_pts)
      2. Use 1.25x threshold: long_pts > short_pts * 1.25 for LONG
      3. Convert to base score: min(55.0 + total_dominant * 0.18, 95.0)
      4. Return (base_score, direction, top_5_pattern_names)

    Args:
        results:    List of PatternResult from all detectors
        long_pts:   Pre-existing LONG indicator points (from crypto_signals legacy code)
        short_pts:  Pre-existing SHORT indicator points (from crypto_signals legacy code)

    Returns:
        (score, direction, top_pattern_names)
    """
    for r in results:
        if r.direction == "LONG":
            long_pts  += r.strength
        elif r.direction == "SHORT":
            short_pts += r.strength
        # NEUTRAL patterns don't bias either direction

    total = long_pts + short_pts
    if total < 10.0:
        return 0.0, "NEUTRAL", []

    if long_pts > short_pts * 1.25:
        direction     = "LONG"
        dominant_pts  = long_pts
    elif short_pts > long_pts * 1.25:
        direction     = "SHORT"
        dominant_pts  = short_pts
    else:
        direction     = "NEUTRAL"
        dominant_pts  = max(long_pts, short_pts)

    base_score = min(55.0 + dominant_pts * 0.18, 95.0)
    base_score = round(base_score, 1)

    # Top 5 patterns by strength (matching direction or NEUTRAL)
    relevant = [
        r for r in results
        if r.direction == direction or r.direction == "NEUTRAL"
    ]
    relevant.sort(key=lambda r: r.strength, reverse=True)
    top_names = [r.name for r in relevant[:5]]

    return base_score, direction, top_names


# ===========================================================================
# CONVENIENCE: run ALL detectors in one call
# ===========================================================================

def run_all_detectors(
    df15: pd.DataFrame,
    ind15: Optional[Dict] = None,
    ind1h: Optional[Dict] = None,
    ind4h: Optional[Dict] = None,
) -> List[PatternResult]:
    """
    Run every pattern detector and return combined list of PatternResult objects.
    Pass df15 (15-min bars) as the primary DataFrame.
    ind15/ind1h/ind4h are indicator dicts from _compute_indicators().
    """
    all_results: List[PatternResult] = []

    try:
        all_results.extend(detect_candlestick_patterns(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: candlestick error: {e}")

    try:
        all_results.extend(detect_chart_patterns(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: chart patterns error: {e}")

    try:
        all_results.extend(detect_smc_patterns(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: SMC error: {e}")

    try:
        all_results.extend(detect_wyckoff(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: wyckoff error: {e}")

    try:
        all_results.extend(detect_fibonacci(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: fibonacci error: {e}")

    try:
        all_results.extend(detect_divergence(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: divergence error: {e}")

    try:
        all_results.extend(detect_volume_profile(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: volume profile error: {e}")

    try:
        all_results.extend(detect_elliott_wave(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: elliott wave error: {e}")

    try:
        all_results.extend(detect_harmonic_patterns(df15, ind15))
    except Exception as e:
        logger.debug(f"crypto_patterns: harmonic error: {e}")

    return all_results
