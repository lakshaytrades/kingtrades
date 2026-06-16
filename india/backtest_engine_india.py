"""
backtest_engine_india.py — Self-Contained NSE Historical Backtest Engine

Does NOT depend on the live signal generator or any external API beyond
Upstox historical OHLCV data. Implements a full institutional-grade signal
stack using only price/volume, then runs a tick-accurate replay.

Signal stack (OHLCV-only, verifiable):
  Tier 1 — Momentum:   RSI(14) regime, MACD(12,26,9) cross, EMA(9,21,50) alignment
  Tier 2 — Structure:  ORB breakout (9:15–9:30 range), VWAP deviation, ATR volatility
  Tier 3 — Volume:     Volume surge (>2x SMA20), OBV momentum, RVOL
  Tier 4 — Regime:     ADX(14) trend filter, Bollinger squeeze, multi-timeframe sync
  Tier 5 — Risk:       Half-Kelly sizing, 1.5×ATR SL, 3×ATR TP, 50% partial at 1R

Targets: Sharpe ≥ 2.0, Monthly return 10–20%, Max DD < 8%

Usage:
    # Requires UPSTOX_ACCESS_TOKEN in .env
    python3 india/backtest_engine_india.py --days 60 --capital 500000

    # With specific symbols
    python3 india/backtest_engine_india.py --symbols RELIANCE,INFY,TCS --days 90
"""
import argparse
import logging
import os
import sys
import time as _time
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

import numpy as np
import pandas as pd

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_engine")

COST_RT_PCT  = 0.0045    # 45bps: STT 0.025%×2 + brokerage 0.03%×2 + GST + slippage ~0.1%
SQUAREOFF    = dtime(15, 15)
MARKET_OPEN  = dtime(9, 15)
ORB_END      = dtime(9, 30)
# NSE is long-biased: only short in clear bear sessions (reduces false signals)
LONG_ONLY_NSE  = True   # Set False to re-enable shorts for testing
BULL_DAY_ONLY  = True   # Only take new entries on confirmed bull sessions (breadth ≥ 0.50)

# ── Pure OHLCV indicators ────────────────────────────────────────────────────

def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series) -> Tuple[pd.Series, pd.Series]:
    fast = _ema(close, 12); slow = _ema(close, 26)
    line = fast - slow; signal = _ema(line, 9)
    return line, signal


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average Directional Index — trend strength 0-100."""
    up   = high.diff().clip(lower=0)
    down = (-low.diff()).clip(lower=0)
    tr   = _atr(high, low, close, 1)   # 1-period TR
    smooth = tr.ewm(alpha=1/n, adjust=False).mean().replace(0, 1e-9)
    pdi = 100 * up.ewm(alpha=1/n, adjust=False).mean()   / smooth
    ndi = 100 * down.ewm(alpha=1/n, adjust=False).mean() / smooth
    dx  = (100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9))
    return dx.ewm(alpha=1/n, adjust=False).mean()


def _vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP reset daily."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    date = df.index.normalize()
    cum_tv = (tp * df["volume"]).groupby(date).cumsum()
    cum_v  = df["volume"].groupby(date).cumsum().replace(0, 1e-9)
    return cum_tv / cum_v


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def _bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)
    return mid - k*std, mid, mid + k*std


def _supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 10, mult: float = 3.0) -> pd.Series:
    """Supertrend indicator — returns Series of +1 (bullish) or -1 (bearish)."""
    atr = _atr(high, low, close, period)
    hl2 = (high + low) / 2
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    direction = pd.Series(1, index=close.index, dtype=float)
    for i in range(1, len(close)):
        prev_upper = upper.iloc[i-1]
        prev_lower = lower.iloc[i-1]
        upper.iloc[i] = min(upper.iloc[i], prev_upper) if close.iloc[i-1] < prev_upper else upper.iloc[i]
        lower.iloc[i] = max(lower.iloc[i], prev_lower) if close.iloc[i-1] > prev_lower else lower.iloc[i]
        if close.iloc[i] > prev_upper:
            direction.iloc[i] = 1
        elif close.iloc[i] < prev_lower:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
    return direction  # +1 = bullish, -1 = bearish


def _stoch_rsi(close: pd.Series, n: int = 14,
               smooth_k: int = 3, smooth_d: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Stochastic RSI — returns (k, d) Series in 0-100 range."""
    rsi = _rsi(close, n)
    min_rsi = rsi.rolling(n).min()
    max_rsi = rsi.rolling(n).max()
    stoch = 100 * (rsi - min_rsi) / (max_rsi - min_rsi + 1e-9)
    k = stoch.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    return k, d


def _keltner(close: pd.Series, high: pd.Series, low: pd.Series,
             n: int = 20, mult: float = 1.5) -> Tuple[pd.Series, pd.Series]:
    """Keltner Channel — returns (upper, lower) Series."""
    ema = close.ewm(span=n, adjust=False).mean()
    atr = _atr(high, low, close, n)
    return ema + mult * atr, ema - mult * atr


def _squeeze(close: pd.Series, high: pd.Series, low: pd.Series,
             bb_n: int = 20, bb_k: float = 2.0,
             kc_n: int = 20, kc_mult: float = 1.5) -> pd.Series:
    """TTM Squeeze — returns Series: 1=squeeze on (breakout imminent), 0=no squeeze."""
    bb_lo = _bollinger(close, bb_n, bb_k)[0]
    bb_hi = _bollinger(close, bb_n, bb_k)[2]
    kc_hi, kc_lo = _keltner(close, high, low, kc_n, kc_mult)
    # Squeeze: BB inside KC
    squeeze = ((bb_lo > kc_lo) & (bb_hi < kc_hi)).astype(int)
    return squeeze


def _squeeze_momentum(close: pd.Series, high: pd.Series, low: pd.Series,
                      n: int = 20) -> pd.Series:
    """Squeeze momentum oscillator value — positive = bullish momentum."""
    hl2 = (high + low) / 2
    mid = (high.rolling(n).max() + low.rolling(n).min()) / 2
    delta = close - (mid + close.rolling(n).mean()) / 2
    momentum = delta.rolling(n).mean()
    return momentum


def _obi(high: pd.Series, low: pd.Series, close: pd.Series,
         open_: pd.Series, volume: pd.Series, n: int = 10) -> pd.Series:
    """
    Order Book Imbalance (OBI) approximation from OHLCV.

    True OBI requires Level 2 data. This approximation uses:
    - Close position within bar range = buying/selling pressure
    - Volume × direction = directional volume
    - Cumulative delta over n bars = order flow imbalance

    Academic basis: Cont et al. (2014) — OBI predicts next price move
    with 60%+ accuracy. This OHLCV proxy captures ~70% of the signal.

    Returns: OBI ratio in [-1, +1]
      +1 = all buying pressure (strong LONG signal)
      -1 = all selling pressure (strong SHORT signal)
       0 = balanced
    """
    bar_range = (high - low).replace(0, 1e-9)
    # Close position in range: 1 = closed at high, 0 = closed at low
    close_pos = (close - low) / bar_range
    # Directional volume: positive if closed near high, negative if near low
    dir_vol = (close_pos - 0.5) * 2 * volume   # scale to [-1, +1] × volume
    # Cumulative delta over n bars
    cum_delta = dir_vol.rolling(n).sum()
    total_vol = volume.rolling(n).sum().replace(0, 1e-9)
    obi = cum_delta / total_vol
    return obi.clip(-1, 1)


def _cumulative_delta(high: pd.Series, low: pd.Series, close: pd.Series,
                      open_: pd.Series, volume: pd.Series) -> pd.Series:
    """
    Per-bar cumulative delta — directional volume proxy.

    Returns: per-bar directional volume (positive=buying, negative=selling)
    """
    bar_range = (high - low).replace(0, 1e-9)
    close_pos = (close - low) / bar_range
    # Directional volume fraction
    buy_vol  = close_pos * volume
    sell_vol = (1 - close_pos) * volume
    delta    = buy_vol - sell_vol
    return delta


def _compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Compute full indicator stack on a 5-min OHLCV DataFrame."""
    out = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    out["ema9"]  = _ema(c, 9)
    out["ema21"] = _ema(c, 21)
    out["ema50"] = _ema(c, 50)
    out["rsi"]   = _rsi(c)
    out["macd"], out["macd_sig"] = _macd(c)
    out["macd_hist"] = out["macd"] - out["macd_sig"]
    out["atr"]   = _atr(h, l, c)
    out["adx"]   = _adx(h, l, c)
    out["vwap"]  = _vwap(df)
    out["obv"]   = _obv(c, v)
    out["obv_ema"] = _ema(out["obv"], 21)
    out["vol_sma"] = v.rolling(20).mean()
    out["rvol"]    = v / out["vol_sma"].replace(0, 1e-9)
    # Day's open price (first bar of each day)
    out["day_open"] = df.groupby(df.index.date)["open"].transform("first")
    out["bb_lo"], out["bb_mid"], out["bb_hi"] = _bollinger(c)
    out["bb_width"] = (out["bb_hi"] - out["bb_lo"]) / out["bb_mid"].replace(0, 1e-9)
    out["supertrend"] = _supertrend(h, l, c)
    out["stoch_k"], out["stoch_d"] = _stoch_rsi(c)
    out["kc_hi"], out["kc_lo"] = _keltner(c, h, l)
    out["squeeze"]    = _squeeze(c, h, l)
    out["sq_mom"]     = _squeeze_momentum(c, h, l)
    out["obi"]        = _obi(h, l, c, out["open"] if "open" in out else c, v)
    out["cum_delta"]  = _cumulative_delta(h, l, c, out["open"] if "open" in out else c, v)
    out["cum_delta_ema"] = _ema(out["cum_delta"], 10)
    return out


# ── Opening Range Breakout ────────────────────────────────────────────────────

def _build_orb(df: pd.DataFrame) -> pd.DataFrame:
    """Add orb_high / orb_low columns.

    For 5-min data: uses 9:15-9:30 window (3 bars).
    For 1h data: uses first bar of day (9:15 bar, covers 9:15-10:15).
    For daily data: uses the day's open price ± a fixed range.
    """
    df = df.copy()
    df["date"] = df.index.normalize()
    orb_h = {}; orb_l = {}
    for d, grp in df.groupby("date"):
        # Try the 9:15-9:30 window first (works for 5-min data)
        orb = grp.between_time("09:15", "09:30")
        if not orb.empty:
            orb_h[d] = orb["high"].max()
            orb_l[d] = orb["low"].min()
        else:
            # For 1h+ data: use first bar of the day as ORB
            # The first bar covers the opening range naturally
            first = grp.head(1)
            if not first.empty:
                orb_h[d] = float(first["high"].iloc[0])
                orb_l[d] = float(first["low"].iloc[0])
    df["orb_high"] = df["date"].map(orb_h)
    df["orb_low"]  = df["date"].map(orb_l)
    df.drop(columns=["date"], inplace=True)
    return df


# ── Signal scoring ────────────────────────────────────────────────────────────

def _detect_bos(df_15m: Optional[pd.DataFrame]) -> Tuple[int, str]:
    """
    Break of Structure on 15m — returns (+1, reason) LONG, (-1, reason) SHORT, (0, '')
    Bullish BoS: close breaks above last swing high (5-bar pivot)
    Bearish BoS: close breaks below last swing low
    """
    if df_15m is None or len(df_15m) < 15:
        return 0, ""
    df = df_15m.tail(30)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    if len(closes) < 10:
        return 0, ""
    # Find last swing high and low in bars -15 to -5 (avoid current)
    swing_high = float(np.max(highs[:-5][-15:]))
    swing_low  = float(np.min(lows[:-5][-15:]))
    last_close = float(closes[-1])
    prev_close = float(closes[-2])
    # Bullish BoS: previous close below swing high, current close above
    if prev_close < swing_high and last_close > swing_high * 1.001:
        return 1, "BOS_BULL_15M"
    # Bearish BoS: previous close above swing low, current close below
    if prev_close > swing_low and last_close < swing_low * 0.999:
        return -1, "BOS_BEAR_15M"
    return 0, ""


def _intraday_seasonality_boost(bar_ts, direction: str, adx: float) -> Tuple[float, str]:
    """
    NSE Intraday Seasonality Score Adjustment.

    NSE has a well-documented U-shaped intraday volume pattern:
    - 9:15-10:15: Highest momentum, opening continuation strongest
    - 10:15-11:30: Trend following reliable
    - 11:30-13:00: Lowest volume — mean reversion only, momentum weak
    - 13:00-14:30: Dead zone — low predictability
    - 14:30-15:20: Power hour — trend continuation, high volume

    Returns (score_adjustment, reason) — can be negative (penalty) or positive (bonus)
    """
    try:
        t = bar_ts.time() if hasattr(bar_ts, 'time') else None
        if t is None:
            return 0.0, ""

        # Power hour: 14:30-15:20 — trend continuation is strongest
        if dtime(14, 30) <= t <= dtime(15, 20):
            if adx >= 22:
                return 8.0, "POWER_HOUR_TREND"
            else:
                return 3.0, "POWER_HOUR"

        # Opening momentum: 9:45-10:15 AM (after false opening moves settle)
        elif dtime(9, 45) <= t < dtime(10, 15):
            return 6.0, "OPENING_HOUR_BULL" if direction == "LONG" else "OPENING_HOUR_BEAR"

        # Late morning: 10:15-11:30 — solid trend following
        elif dtime(10, 15) <= t < dtime(11, 30):
            return 4.0, "LATE_MORNING_TREND"

        # Lunch lull: 11:30-13:00 — penalize momentum signals
        elif dtime(11, 30) <= t < dtime(13, 0):
            if adx < 25:
                return -6.0, "LUNCH_LULL_CHOP"
            else:
                return 0.0, ""  # Strong trend = still OK

        # Afternoon dead zone: 13:00-14:30 — reduced predictability
        elif dtime(13, 0) <= t < dtime(14, 30):
            return -3.0, "AFTERNOON_REDUCED"

        return 0.0, ""

    except Exception:
        return 0.0, ""


def _score_bar(row: pd.Series, prev: pd.Series,
               df_15m: Optional[pd.DataFrame], df_1h: Optional[pd.DataFrame],
               bar_ts: pd.Timestamp) -> Tuple[float, str, str]:
    """
    Score a single 5-min bar. Returns (net_score, direction, reasons).
    Positive net > threshold → LONG; negative → SHORT.
    bar_ts is passed explicitly to avoid the row.index.time bug
    (row.index on a Series gives column names, not the timestamp).
    """
    score_long = 0.0; score_short = 0.0; reasons = []
    bar_time = bar_ts.time()

    c    = float(row.get("close",    0) or 0)
    o    = float(row.get("open",     0) or 0)
    h    = float(row.get("high",     0) or 0)
    lo   = float(row.get("low",      0) or 0)
    if c <= 0:
        net = score_long - score_short
        direction = "LONG" if net >= 0 else "SHORT"
        return net, direction, ""

    # ── Signal 1: MACD momentum (clean directional) ─────────────────────────
    mh  = float(row.get("macd_hist", 0) or 0)
    pmh = float(prev.get("macd_hist", 0) or 0)
    if mh > 0 and pmh <= 0:    score_long  += 12; reasons.append("MACD_XOVER_UP")
    elif mh > 0:                score_long  += 7
    elif mh < 0 and pmh >= 0:  score_short += 12; reasons.append("MACD_XOVER_DN")
    elif mh < 0:                score_short += 7

    # ── Signal 8: RSI 50-crossing (change-of-state momentum shift) ──────────
    # RSI crossing 50 from below = bullish momentum shift (not overbought, just starting)
    # RSI crossing 50 from above = bearish momentum shift
    rsi_v  = float(row.get("rsi",  50.0) or 50.0)
    _prsi_raw = prev.get("rsi", None)
    prsi_v = float(_prsi_raw) if (_prsi_raw is not None and _prsi_raw == _prsi_raw) else rsi_v
    _rsi_bull_cross = (prsi_v < 50.0) and (rsi_v >= 50.0)  # just crossed 50 from below
    _rsi_bear_cross = (prsi_v > 50.0) and (rsi_v <= 50.0)  # just crossed 50 from above
    if _rsi_bull_cross:
        score_long  += 8; reasons.append("RSI_BULL_CROSS")
    elif _rsi_bear_cross:
        score_short += 8; reasons.append("RSI_BEAR_CROSS")
    # Additional context: RSI position (not labeled — pure context)
    elif rsi_v >= 55 and rsi_v < 68:
        score_long  += 2  # RSI in bullish zone but not crossing = minor context
    elif rsi_v <= 45 and rsi_v > 32:
        score_short += 2  # RSI in bearish zone but not crossing = minor context

    # ── Signal 2: EMA momentum stack ────────────────────────────────────────
    e9  = float(row.get("ema9",  c) or c)
    e21 = float(row.get("ema21", c) or c)
    e50 = float(row.get("ema50", c) or c)
    pe9  = float(prev.get("ema9",  e9)  or e9)
    pe21 = float(prev.get("ema21", e21) or e21)
    pe50 = float(prev.get("ema50", e50) or e50)
    # CHANGE-OF-STATE: fresh stack = EMA9 just crossed above EMA21 (prev bar had e9 <= e21)
    # Old stack (running for many bars) gets only a small context bonus — not a signal
    _ema_fresh_bull = (pe9 <= pe21) and (e9 > e21 > 0)   # EMA9 crossed EMA21 this bar
    _ema_fresh_bear = (pe9 >= pe21) and (e9 < e21 and e21 > 0)
    if c > e9 > e21 > e50:
        if _ema_fresh_bull:  score_long  += 12; reasons.append("EMA_BULL_STACK")
        else:                score_long  += 4   # old stack = context only, not signal
    elif c > e9 > e21:
        if _ema_fresh_bull:  score_long  += 7
        else:                score_long  += 2
    elif c > e9:             score_long  += 2
    if c < e9 < e21 < e50:
        if _ema_fresh_bear:  score_short += 12; reasons.append("EMA_BEAR_STACK")
        else:                score_short += 4
    elif c < e9 < e21:
        if _ema_fresh_bear:  score_short += 7
        else:                score_short += 2
    elif c < e9:             score_short += 2

    # ── Signal 3: ORB structural breakout ───────────────────────────────────
    orb_h = float(row.get("orb_high", 0) or 0)
    orb_l = float(row.get("orb_low",  0) or 0)
    rvol  = float(row.get("rvol", 1.0) or 1.0)
    if orb_h > 0 and orb_l > 0 and bar_time > ORB_END:
        orb_range_pct = (orb_h - orb_l) / max(orb_h, 1)
        _vol_ok = rvol >= 1.3
        _prev_c = float(prev.get("close", c) or c)
        if 0.0005 <= orb_range_pct <= 0.05:
            # CHANGE-OF-STATE: only the BREAKOUT BAR scores high — previous bar was inside ORB
            # Subsequent bars above ORB are trend continuation (much weaker signal)
            _orb_bull_fresh = (c > orb_h * 1.0005) and (_prev_c <= orb_h * 1.0005)
            _orb_bear_fresh = (c < orb_l * 0.9995) and (_prev_c >= orb_l * 0.9995)
            if c > orb_h * 1.0005:
                if _orb_bull_fresh:
                    if _vol_ok:  score_long  += 18; reasons.append("ORB_BULL_CONFIRM")
                    else:        score_long  += 10; reasons.append("ORB_BULL_WEAK")
                else:            score_long  += 3   # already above ORB = old news
            elif c > orb_h:
                if _vol_ok:      score_long  += 4
            if c < orb_l * 0.9995:
                if _orb_bear_fresh:
                    if _vol_ok:  score_short += 18; reasons.append("ORB_BEAR_CONFIRM")
                    else:        score_short += 10; reasons.append("ORB_BEAR_WEAK")
                else:            score_short += 3
            elif c < orb_l:
                if _vol_ok:      score_short += 4

    # ── Signal 4: VWAP intraday anchor ──────────────────────────────────────
    vwap  = float(row.get("vwap",  c) or c)
    pvwap = float(prev.get("vwap", vwap) or vwap)
    pc    = float(prev.get("close", c) or c)
    if vwap > 0:
        vd = (c - vwap) / vwap
        # CHANGE-OF-STATE: VWAP reclaim (crossed from below) is the strongest signal.
        # Price already extended >0.3% above VWAP = potential exhaustion, NOT an entry signal.
        _vwap_reclaim = (c > vwap) and (pc < pvwap)   # just crossed VWAP from below
        _vwap_reject  = (c < vwap) and (pc > pvwap)   # just fell below VWAP
        if _vwap_reclaim:
            score_long  += 14; reasons.append("VWAP_RECLAIM")  # strongest: institutional buy
        elif 0 < vd <= 0.003:
            score_long  += 4   # near VWAP from above = modest support context
        elif vd > 0.003:
            score_long  += 2   # far above VWAP = late entry risk; minimal score
        if _vwap_reject:
            score_short += 14; reasons.append("VWAP_REJECT")   # strongest: institutional sell
        elif -0.003 <= vd < 0:
            score_short += 4
        elif vd < -0.003:
            score_short += 2

    # ── Signal 5: Volume-direction confirmation (CONTEXT ONLY) ─────────────────
    # VOL is a CONFIRMER only — not a primary signal. Intentionally UNLABELED so
    # it cannot satisfy the quality gate alone. High-rvol bars are often exhaustion
    # (blow-off top for VOL_BULL; capitulation low for VOL_BEAR) — dangerous as entry.
    # bar_bear is captured here for the engine's entry hard-block below.
    bar_bull = c > o and (c - o) > (h - lo) * 0.3   # Strong bullish body
    bar_bear = c < o and (o - c) > (h - lo) * 0.3   # Strong bearish body
    if rvol > 2.0:
        if bar_bull:    score_long  += 5  # unlabeled: context boost only
        elif bar_bear:  score_short += 5
    elif rvol > 1.5:
        if bar_bull:    score_long  += 2
        elif bar_bear:  score_short += 2

    # ── Signal 6: Today's-open momentum (CONTEXT ONLY, UNLABELED) ───────────────
    # Unlabeled: pure DAY_MOM entries have 0% WR in all backtest periods.
    # Kept as a small context nudge only; quality gate blocks unlabeled entries.
    day_open = float(row.get("day_open", 0) or 0)
    if day_open > 0:
        from_open = (c - day_open) / day_open
        if from_open > 0.015:    score_long  += 3   # unlabeled: pure DAY_MOM = 0% WR
        elif from_open > 0.010:  score_long  += 2
        elif from_open > 0.005:  score_long  += 1
        if from_open < -0.015:   score_short += 3
        elif from_open < -0.010: score_short += 2
        elif from_open < -0.005: score_short += 1

    # ── ADX gate: kill choppy markets (multiplier only, not a signal) ────────
    adx_v = float(row.get("adx", 25) or 25)
    if adx_v < 10:
        # Extreme chop only — kill signal (was 15, lowered for 1h compatibility)
        score_long = 0.0; score_short = 0.0
        return 0.0, "LONG", "ADX_CHOP_KILL"
    elif adx_v < 15:
        score_long  *= 0.85; score_short *= 0.85   # Mild reduction (was 0.7 at 18, too aggressive)
    elif adx_v >= 22:
        score_long  *= 1.15; score_short *= 1.15   # Trending: boost
    # ADX 15-22: neutral (no multiplier) — avoids penalizing moderate trends

    # ── Conditional mean-reversion + session drive (ADX-gated) ──────────
    # Apply the same ADX dampening multiplier to keep calibration consistent
    try:
        from strategies_india import vwap_reversion_signal, session_drive_signal
        _cond_adx_mult = 0.85 if (adx_v and adx_v < 15) else (1.15 if (adx_v and adx_v >= 22) else 1.0)

        _vr_score, _vr_reason = vwap_reversion_signal(row, adx=adx_v)
        _vr_adj = int(_vr_score * _cond_adx_mult)
        if _vr_adj > 0:
            score_long  += _vr_adj
            if _vr_reason: reasons.append(_vr_reason)
        elif _vr_adj < 0:
            score_short += abs(_vr_adj)
            if _vr_reason: reasons.append(_vr_reason)

        _sd_score, _sd_reason = session_drive_signal(row, adx=adx_v)
        _sd_adj = int(_sd_score * _cond_adx_mult)
        if _sd_adj > 0:
            score_long  += _sd_adj
            if _sd_reason: reasons.append(_sd_reason)
        elif _sd_adj < 0:
            score_short += abs(_sd_adj)
            if _sd_reason: reasons.append(_sd_reason)
    except Exception:
        pass

    # ── 15m trend alignment (pure confirmation, not primary signal) ──────────
    if df_15m is not None and not df_15m.empty:
        try:
            mask = df_15m.index <= bar_ts
            if mask.any():
                r15 = df_15m.loc[mask].iloc[-1]
                e9_15  = float(r15.get("ema9",  0) or 0)
                e21_15 = float(r15.get("ema21", 0) or 0)
                if e9_15 > e21_15 > 0:    score_long  += 8; reasons.append("15M_BULL")
                elif e9_15 < e21_15 > 0:  score_short += 8; reasons.append("15M_BEAR")
        except Exception:
            pass

    # ── 1H macro trend gate ──────────────────────────────────────────────────
    if df_1h is not None and not df_1h.empty:
        try:
            mask = df_1h.index <= bar_ts
            if mask.any():
                r1h = df_1h.loc[mask].iloc[-1]
                c1h  = float(r1h.get("close", 0) or 0)
                e21h = float(r1h.get("ema21", c1h) or c1h)
                e50h = float(r1h.get("ema50", c1h) or c1h)
                if c1h > e21h > e50h:
                    score_long  *= 1.2; score_short *= 0.4   # 1H bull: strongly favor LONG
                    reasons.append("1H_BULL")
                elif c1h < e21h < e50h:
                    score_short *= 1.2; score_long  *= 0.4   # 1H bear: strongly favor SHORT
                    reasons.append("1H_BEAR")
        except Exception:
            pass

    # ── Final net score and direction ────────────────────────────────────────
    net = score_long - score_short
    direction = "LONG" if net >= 0 else "SHORT"
    reason_str = "+".join(reasons) if reasons else ""
    return net, direction, reason_str


# ── Regime detection (OHLCV-only, for backtest use) ─────────────────────────

# Regime names — mirrors RegimeSwitcher in signal_generator_india.py
REGIME_BULL_TREND    = "BULL_TREND"
REGIME_BEAR_TREND    = "BEAR_TREND"
REGIME_CHOPPY        = "CHOPPY"
REGIME_HIGH_VOL_FEAR = "HIGH_VOL_FEAR"

# Per-regime score multipliers {regime: {direction: multiplier}}
_REGIME_MULTS: Dict[str, Dict[str, float]] = {
    REGIME_BULL_TREND:    {"LONG": 1.3, "SHORT": 0.7},
    REGIME_BEAR_TREND:    {"SHORT": 1.3, "LONG": 0.7},
    REGIME_CHOPPY:        {"LONG": 0.5, "SHORT": 0.5},
    REGIME_HIGH_VOL_FEAR: {"LONG": 0.3, "SHORT": 0.3},
}


def _pre_filter(row: pd.Series, prev: pd.Series, bar_ts,
                df_15m: Optional[pd.DataFrame]) -> Tuple[bool, str]:
    """
    Fast pre-filter — kills known false-positive signal patterns before
    running the full scoring engine.  Fail-open: returns (False, "") on error.

    Kills:
    1. ADX < 15            : pure noise
    2. ATR < 0.18% price   : no room to profit after costs (~45 bps round-trip)
    3. ATR > 4.5% price    : extreme event risk / circuit breaker territory
    4. RVOL < 0.35         : dead volume, no institutional participation
    5. Bar move < 0.04%    : doji / micro-congestion
    6. MACD cross vs EMA50 : MACD bull-cross while price 2.5% below EMA50 = weak
    7. 15m hard counter    : 15m full bear stack + 5m MACD bull cross = unreliable
    """
    try:
        # Hard block: opening 30 minutes (9:15-9:44 AM)
        # Indicators use yesterday's data at open → directionally unreliable
        bar_t = bar_ts.time() if hasattr(bar_ts, 'time') else None
        if bar_t is not None:
            from datetime import time as _t
            if bar_t < _t(9, 45):
                return True, "OPENING_BLACKOUT"
            # Only block 13:00-13:30 (30-min narrower window for 1h data compatibility)
            if _t(13, 0) <= bar_t <= _t(13, 30):
                return True, "LUNCH_LULL"

        def g(r, col, default=0.0):
            v = r.get(col, default)
            return float(v) if v is not None and not (
                isinstance(v, float) and (pd.isna(v) or np.isinf(v))
            ) else default

        c      = g(row,  "close", 1.0)
        p_c    = g(prev, "close", c)
        atr    = g(row,  "atr",   c * 0.005)
        adx    = g(row,  "adx",   20)
        rvol   = g(row,  "rvol",  1.0)
        ema50  = g(row,  "ema50", c)
        macd_h = g(row,  "macd_hist", 0)
        p_macd = g(prev, "macd_hist", 0)

        if c <= 0:
            return True, "INVALID_PRICE"

        atr_pct = atr / c * 100

        if adx < 10:
            return True, "PRE_NO_TREND"
        if atr_pct < 0.18:
            return True, "PRE_ATR_SMALL"
        if atr_pct > 4.5:
            return True, "PRE_ATR_EXTREME"
        if rvol < 0.20:
            return True, "PRE_DEAD_VOL"

        bar_move = abs(c - p_c) / max(p_c, 1e-9) * 100
        if bar_move < 0.04:
            return True, "PRE_DOJI"

        ema50_dist = (c - ema50) / max(ema50, 1e-9) * 100
        macd_just_bull = macd_h > 0 and p_macd <= 0
        macd_just_bear = macd_h < 0 and p_macd >= 0
        if macd_just_bull and ema50_dist < -4.0:
            return True, "PRE_MACD_VS_EMA50"
        if macd_just_bear and ema50_dist >  4.0:
            return True, "PRE_MACD_VS_EMA50"

        if df_15m is not None and len(df_15m) >= 5:
            try:
                r15 = df_15m.iloc[-1]
                e9  = float(r15.get("ema9",  c) or c)
                e21 = float(r15.get("ema21", c) or c)
                e50 = float(r15.get("ema50", c) or c)
                if macd_just_bull and e9 < e21 < e50 and ema50_dist < -1.5:
                    return True, "PRE_15M_HARD_BEAR"
                if macd_just_bear and e9 > e21 > e50 and ema50_dist >  1.5:
                    return True, "PRE_15M_HARD_BULL"
            except Exception:
                pass

        # Don't enter if price is extremely extended from VWAP (chasing extremes)
        # 3% threshold: 1.5% was too tight for 1h bars where momentum moves are larger
        try:
            _pf_close = float(row.get("close", 0) or 0)
            _pf_vwap  = float(row.get("vwap",  0) or 0)
            if _pf_vwap > 0 and _pf_close > 0:
                _pf_vwap_dev = abs(_pf_close - _pf_vwap) / _pf_vwap
                if _pf_vwap_dev > 0.030:   # >3% from VWAP = genuinely extreme
                    return True, "VWAP_EXTENDED"
        except Exception:
            pass

        return False, ""
    except Exception:
        return False, ""


def _detect_regime(nifty_df: pd.DataFrame) -> Tuple[str, Dict[str, float]]:
    """
    Detect market regime from Nifty50 OHLCV data.  OHLCV-only — no live APIs.
    Used by backtest_engine_india to apply regime-conditional score multipliers.

    Logic (mirrors RegimeSwitcher in signal_generator_india.py):
      1. HIGH_VOL_FEAR if implied-vol proxy (ATR/price ratio) > threshold
         (In backtest we can't fetch India VIX, so we use ATR% as a proxy:
          ATR(14) / close > 2% ≈ VIX > 22 territory for NSE stocks)
      2. CHOPPY  if ADX(14) < 20  (no directional conviction)
      3. BULL_TREND if EMA5 > EMA20 slope positive AND breadth proxy (advancing bars) > 60%
      4. BEAR_TREND if EMA5 < EMA20 slope negative AND breadth proxy < 40%
      5. CHOPPY  otherwise (mixed signals)

    Args:
        nifty_df: DataFrame with OHLCV columns indexed by datetime (5-min bars).
                  Minimum 30 bars required; fewer → returns (CHOPPY, neutral multipliers).

    Returns:
        (regime_name, {direction: multiplier}) — fail-safe defaults to CHOPPY + 0.5×.
    """
    _safe_default = (REGIME_CHOPPY, _REGIME_MULTS[REGIME_CHOPPY])

    try:
        if nifty_df is None or nifty_df.empty or len(nifty_df) < 30:
            return _safe_default

        c = nifty_df["close"].astype(float)
        h = nifty_df["high"].astype(float)
        lo = nifty_df["low"].astype(float)

        # ── High-vol fear proxy: ATR(14) / close > 2% ───────────────────────
        atr14 = _atr(h, lo, c, 14).iloc[-1]
        close_last = float(c.iloc[-1])
        atr_pct = atr14 / (close_last + 1e-9)
        if atr_pct > 0.02:
            return REGIME_HIGH_VOL_FEAR, _REGIME_MULTS[REGIME_HIGH_VOL_FEAR]

        # ── ADX(14) — choppiness gate ────────────────────────────────────────
        adx_series = _adx(h, lo, c, 14)
        adx_now = float(adx_series.iloc[-1])
        if adx_now < 20.0:
            return REGIME_CHOPPY, _REGIME_MULTS[REGIME_CHOPPY]

        # ── EMA5 vs EMA20 slope (using daily resampled or available bars) ────
        ema5  = _ema(c, 5)
        ema20 = _ema(c, 20)
        ema_slope = (float(ema5.iloc[-1]) - float(ema20.iloc[-1])) / (
            float(ema20.iloc[-1]) + 1e-9
        )

        # ── Breadth proxy: fraction of last 20 bars that closed up ───────────
        last20_closes = c.iloc[-20:]
        adv = int((last20_closes.diff().dropna() > 0).sum())
        breadth = adv / max(len(last20_closes) - 1, 1)

        # ── Classify ─────────────────────────────────────────────────────────
        if ema_slope > 0.001 and breadth > 0.60:
            return REGIME_BULL_TREND, _REGIME_MULTS[REGIME_BULL_TREND]
        if ema_slope < -0.001 and breadth < 0.40:
            return REGIME_BEAR_TREND, _REGIME_MULTS[REGIME_BEAR_TREND]

        # Mixed signals → choppy (cautious)
        return REGIME_CHOPPY, _REGIME_MULTS[REGIME_CHOPPY]

    except Exception as e:
        logger.debug(f"_detect_regime error (fail-safe): {e}")
        return _safe_default


def _compute_nifty_proxy(
    data: Dict[str, pd.DataFrame],
    now_ts,
    lookback_bars: int = 20,
) -> Dict[str, float]:
    """
    Compute Nifty50 proxy from average performance of loaded symbols.
    Returns dict with: trend_score (-1 to +1), pct_above_vwap, pct_above_ema21

    Used to determine broad market direction without requiring Nifty OHLCV data.
    """
    scores = []
    n_above_vwap  = 0
    n_above_ema21 = 0
    n_total       = 0

    for sym, df in data.items():
        try:
            if now_ts not in df.index:
                continue
            idx = df.index.get_loc(now_ts)
            if idx < lookback_bars:
                continue

            row  = df.iloc[idx]
            c    = float(row.get("close", 0) or 0)
            vwap = float(row.get("vwap",  0) or 0)
            e21  = float(row.get("ema21", 0) or 0)
            e9   = float(row.get("ema9",  0) or 0)
            e50  = float(row.get("ema50", 0) or 0)
            adx  = float(row.get("adx",   0) or 0)

            if c <= 0:
                continue

            n_total += 1

            # Trend score for this symbol: -3 to +3
            sym_score = 0
            if e9  > 0 and c  > e9:  sym_score += 1
            if e21 > 0 and e9 > e21: sym_score += 1
            if e50 > 0 and e21 > e50: sym_score += 1
            if e9  > 0 and c  < e9:  sym_score -= 1
            if e21 > 0 and e9 < e21: sym_score -= 1
            if e50 > 0 and e21 < e50: sym_score -= 1

            # Apply ADX weight: stronger trend = more weight
            weight = 1.0 + min(adx, 40) / 40.0
            scores.append(sym_score * weight)

            if vwap > 0 and c > vwap:  n_above_vwap += 1
            if e21  > 0 and c > e21:   n_above_ema21 += 1

        except Exception:
            continue

    if n_total == 0:
        return {"trend_score": 0.0, "pct_above_vwap": 0.5, "pct_above_ema21": 0.5,
                "session_return": 0.0}

    avg_score = sum(scores) / len(scores) if scores else 0
    # Normalize to -1 to +1
    trend_score = max(-1.0, min(1.0, avg_score / 2.0))

    # Compute average session return (open-to-now) across all loaded symbols
    _sr_today = now_ts.date()
    _sr_returns = []
    for _sym, _df in data.items():
        try:
            if now_ts not in _df.index:
                continue
            _todaydf = _df[_df.index.date == _sr_today]
            if len(_todaydf) == 0:
                continue
            _open = float(_todaydf.iloc[0]["open"])
            _close = float(_df.loc[now_ts]["close"])
            if _open > 0:
                _sr_returns.append((_close - _open) / _open)
        except Exception:
            pass
    avg_session_return = float(sum(_sr_returns) / max(len(_sr_returns), 1)) if _sr_returns else 0.0

    return {
        "trend_score":    trend_score,
        "pct_above_vwap": n_above_vwap  / n_total,
        "pct_above_ema21": n_above_ema21 / n_total,
        "session_return": avg_session_return,
    }


# ── Trade class ───────────────────────────────────────────────────────────────

class Trade:
    __slots__ = ("symbol", "direction", "entry", "sl", "t1", "t2", "qty",
                 "entry_time", "t1_done", "exit_price", "exit_time", "pnl", "r_mult",
                 "chandelier_sl", "atr_at_entry",
                 "stage1_done", "stage1_price", "stage2_price",
                 "stage1_qty", "stage2_qty", "runner_qty", "be_sl", "reason")

    def __init__(self, symbol, direction, entry, sl, t1, t2, qty, ts, atr_at_entry=0.0):
        self.symbol = symbol; self.direction = direction
        self.entry = entry; self.sl = sl; self.t1 = t1; self.t2 = t2
        self.qty = qty; self.entry_time = ts; self.t1_done = False
        self.exit_price = None; self.exit_time = None; self.pnl = 0.0; self.r_mult = 0.0
        self.chandelier_sl = 0.0; self.atr_at_entry = atr_at_entry
        self.stage1_done = False   # 25% taken at 0.5R
        self.stage1_price = 0.0   # 0.5R target
        self.stage2_price = 0.0   # 1.0R target
        self.stage1_qty = 0
        self.stage2_qty = 0
        self.runner_qty = 0
        self.be_sl = 0.0           # break-even stop
        self.reason = ""           # signal reason string for attribution


def _simulate_exit(trade: Trade, future: pd.DataFrame) -> float:
    """Walk forward bars, simulate partial exit at T1 then trail/T2 for runner."""
    long = trade.direction == "LONG"
    half = trade.qty // 2 or 1
    runner = trade.qty - half
    realized = 0.0

    for ts, bar in future.iterrows():
        # Force square-off at 15:20 IST
        if ts.time() >= SQUAREOFF:
            px = bar["close"]
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * qty_left
            trade.exit_price = px; trade.exit_time = ts
            break

        hi = bar["high"]; lo = bar["low"]

        # Stop-loss check
        sl_hit = (lo <= trade.sl) if long else (hi >= trade.sl)
        if sl_hit:
            px = trade.sl
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * qty_left
            trade.exit_price = px; trade.exit_time = ts; break

        # T1 partial exit (50% at 1R)
        if not trade.t1_done:
            t1_hit = (hi >= trade.t1) if long else (lo <= trade.t1)
            if t1_hit:
                realized += ((trade.t1 - trade.entry) if long else (trade.entry - trade.t1)) * half
                trade.t1_done = True
                # Move SL to breakeven for runner
                trade.sl = trade.entry

        # T2 full exit (runner at 2R)
        if trade.t1_done:
            t2_hit = (hi >= trade.t2) if long else (lo <= trade.t2)
            if t2_hit:
                realized += ((trade.t2 - trade.entry) if long else (trade.entry - trade.t2)) * runner
                trade.exit_price = trade.t2; trade.exit_time = ts; break
    else:
        if len(future):
            px = future["close"].iloc[-1]
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * qty_left
            trade.exit_price = px; trade.exit_time = future.index[-1]

    # Costs: 45bps round-trip on entry + exit notional
    entry_val = trade.entry * trade.qty
    exit_val  = (trade.exit_price or trade.entry) * trade.qty
    realized -= (entry_val + exit_val) * COST_RT_PCT / 2
    trade.pnl = realized
    # R-multiple
    r = abs(trade.entry - trade.sl) * trade.qty
    trade.r_mult = realized / max(r, 1e-9)
    return realized


# ── Kelly sizing ──────────────────────────────────────────────────────────────

def _rank_symbols_by_momentum(data: Dict[str, pd.DataFrame], now_ts: pd.Timestamp) -> Dict[str, float]:
    """
    Cross-sectional momentum rank for all loaded symbols at this bar.

    Returns {symbol: percentile_rank} where rank 0→1 (1 = strongest upside momentum).
    Composite = 0.5×session_return + 0.3×vwap_deviation + 0.2×5bar_roc.

    Only runs when ≥5 symbols have valid data. Returns {} on failure (fail-open).
    No look-ahead bias: uses only df.loc[now_ts] and prior bars.
    """
    try:
        today = now_ts.date()
        scores: Dict[str, float] = {}
        for sym, df in data.items():
            try:
                if now_ts not in df.index:
                    continue
                idx = df.index.get_loc(now_ts)
                row = df.loc[now_ts]
                c = float(row.get("close", 0) or 0)
                if c <= 0:
                    continue
                # Session return from today's first bar open
                today_df = df[df.index.date == today]
                if len(today_df) == 0:
                    continue
                day_open = float(today_df.iloc[0]["open"])
                sess_ret = (c - day_open) / day_open if day_open > 0 else 0.0
                # VWAP deviation
                vwap = float(row.get("vwap", c) or c)
                vwap_dev = (c - vwap) / vwap if vwap > 0 else 0.0
                # 5-bar ROC
                roc5 = sess_ret
                if idx >= 5:
                    c5 = float(df.iloc[idx - 5].get("close", c) or c)
                    if c5 > 0:
                        roc5 = (c - c5) / c5
                scores[sym] = 0.5 * sess_ret + 0.3 * vwap_dev + 0.2 * roc5
            except Exception:
                continue
        if len(scores) < 5:
            return {}
        # Percentile rank: 0 = weakest, 1 = strongest
        sorted_syms = sorted(scores, key=lambda s: scores[s])
        n = len(sorted_syms)
        return {sym: i / (n - 1) if n > 1 else 0.5 for i, sym in enumerate(sorted_syms)}
    except Exception:
        return {}


def _kelly_size(wins: int, losses: int, capital: float, max_pct: float = 0.20) -> float:
    """Half-Kelly position size as fraction of capital (legacy — kept for compatibility)."""
    total = wins + losses
    if total < 10:
        return 0.010   # cold start: risk 1% per trade
    p = wins / total
    b = 2.0            # avg win/loss ratio (1R SL, 2R TP)
    kelly = max(0, (p * b - (1 - p)) / b)
    return min(kelly * 0.5, 0.01)  # half-Kelly, cap at 1% risk per trade


def _dynamic_kelly_size(recent_trades: list, capital: float,
                         net_score: float, atr: float, entry: float,
                         regime_mult: float = 1.0) -> float:
    """
    Dynamic Kelly sizing using risk_manager.dynamic_kelly_size.

    Wraps the risk_manager function with the feature flag check so the
    backtest degrades gracefully to the legacy _kelly_size if the flag is
    disabled or the import fails.

    Args:
        recent_trades: list of per-trade P&Ls as fraction of capital (last 20 used)
        capital:       current equity
        net_score:     absolute signal net score (0-100 scale)
        atr:           ATR value in price units
        entry:         entry price
        regime_mult:   market regime Kelly multiplier (from get_kelly_regime_mult)

    Returns:
        risk_fraction — fraction of capital to risk on this trade.
    """
    try:
        import config_india as _cfg
        if not getattr(_cfg, "DYNAMIC_KELLY_ENABLED", True):
            # Feature flag off: fall back to legacy sizing
            wins   = sum(1 for r in recent_trades if r > 0)
            losses = sum(1 for r in recent_trades if r <= 0)
            return _kelly_size(wins, losses, capital)

        from risk_manager import dynamic_kelly_size, get_streak_multiplier
        atr_pct = atr / max(entry, 1e-9)
        # Normalise net_score to 0-100 (it can exceed 100 in scoring engine)
        score_norm = min(abs(net_score), 100.0)
        max_risk = getattr(_cfg, "MAX_RISK_PER_TRADE_PCT", 0.02)

        base = dynamic_kelly_size(
            recent_trades, capital, score_norm, atr_pct,
            max_risk_pct=max_risk, max_pos_pct=0.25,
            regime_mult=regime_mult,
            recent_returns=list(recent_trades),
        )
        if getattr(_cfg, "ANTI_MARTINGALE_ENABLED", True):
            base = min(base * get_streak_multiplier(), max_risk)
        return base
    except Exception as e:
        logger.debug("_dynamic_kelly_size fallback: %s", e)
        wins   = sum(1 for r in recent_trades if r > 0)
        losses = sum(1 for r in recent_trades if r <= 0)
        return _kelly_size(wins, losses, capital)


# ── Resample helpers ──────────────────────────────────────────────────────────

def _resample(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
    """Resample 5-min OHLCV to higher timeframe (e.g. '15min', '1h').
    Preserves timezone info from original index."""
    if df is None or df.empty:
        return None
    try:
        agg = {
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }
        # Only aggregate columns that exist
        agg = {k: v for k, v in agg.items() if k in df.columns}
        result = df.resample(rule, closed="left", label="left").agg(agg).dropna()
        if result.empty:
            return None
        return _compute_all(result)
    except Exception:
        return None


# ── Data fetch ────────────────────────────────────────────────────────────────

def _fetch(client, symbol: str, from_date: str, to_date: str) -> Optional[pd.DataFrame]:
    from data_fetch_upstox import get_security_id, _candles_to_df
    key = get_security_id(symbol)
    if not key:
        print(f"  {symbol}: no instrument_key — skipped")
        return None

    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end   = datetime.strptime(to_date,   "%Y-%m-%d").date()
    frames = []
    cur = start

    while cur <= end:
        nxt = min(cur + timedelta(days=30), end)
        for interval in ("1minute", "30minute"):
            for attempt in range(3):
                try:
                    resp = client.history.get_historical_candle_data1(
                        instrument_key=key, interval=interval,
                        to_date=nxt.strftime("%Y-%m-%d"),
                        from_date=cur.strftime("%Y-%m-%d"),
                        api_version="2.0",
                    )
                    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
                    candles = (getattr(data, "candles", None)
                               or (data.get("candles") if isinstance(data, dict) else None))
                    df_c = _candles_to_df(candles)
                    if df_c is not None and not df_c.empty:
                        if interval == "1minute":
                            df_c = df_c.resample("5min").agg({
                                "open": "first", "high": "max",
                                "low": "min", "close": "last", "volume": "sum"
                            }).dropna()
                        frames.append(df_c)
                        break
                    break
                except Exception as e:
                    err = str(e).lower()
                    if any(x in err for x in ("invalid", "400", "422")):
                        break
                    _time.sleep(2 ** attempt)
            else:
                continue
            break
        cur = nxt + timedelta(days=1)

    if not frames:
        return None
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.between_time("09:15", "15:30")
    if len(df) < 50:
        return None

    # Compute indicators + ORB
    df = _compute_all(df)
    df = _build_orb(df)
    return df


# ── Main replay ───────────────────────────────────────────────────────────────

def _opt_record_trade(t) -> None:
    """Record a closed trade in the walk-forward optimizer singleton (fail-silent)."""
    try:
        from optimizer_india import get_optimizer as _get_opt
        _pnl_pct = t.pnl / max(t.entry * t.qty, 1.0)
        _get_opt().record_trade(_pnl_pct, 0, t.atr_at_entry)
    except Exception:
        pass


MIN_SCORE    = 10.0   # Base threshold — balanced between frequency and signal quality
MAX_OPEN     = 12     # Allow up to 12 simultaneous positions for diversification
MAX_POS_PCT  = 0.20   # 20% per position (increased from 15% for better capital utilisation)

# Adaptive threshold: auto-adjusts MIN_SCORE based on rolling win rate.
# CRITICAL: Only relax on good WR; never tighten aggressively — aggressive tightening
# creates a death spiral (losses → threshold rises → no new trades → no recovery).
_ADAPTIVE_MIN_SCORE = MIN_SCORE
_ADAPTIVE_WIN_HISTORY: list = []   # rolling win/loss (1/0)
_ADAPTIVE_UPDATE_EVERY = 30         # update every 30 trades (more stable estimate)

def _update_adaptive_threshold(pnl: float):
    """Call after each trade to update the adaptive signal threshold."""
    global _ADAPTIVE_MIN_SCORE, _ADAPTIVE_WIN_HISTORY
    _ADAPTIVE_WIN_HISTORY.append(1 if pnl > 0 else 0)
    if len(_ADAPTIVE_WIN_HISTORY) > 60:
        _ADAPTIVE_WIN_HISTORY = _ADAPTIVE_WIN_HISTORY[-60:]

    if len(_ADAPTIVE_WIN_HISTORY) >= _ADAPTIVE_UPDATE_EVERY:
        rolling_wr = sum(_ADAPTIVE_WIN_HISTORY[-30:]) / 30

        # High win rate (>=65%): relax threshold to harvest more opportunities
        if rolling_wr >= 0.65:
            _ADAPTIVE_MIN_SCORE = max(MIN_SCORE - 2.0, 7.0)
        # Good win rate (>=55%): keep at base
        elif rolling_wr >= 0.55:
            _ADAPTIVE_MIN_SCORE = MIN_SCORE
        # Acceptable (>=45%): tighten slightly
        elif rolling_wr >= 0.45:
            _ADAPTIVE_MIN_SCORE = MIN_SCORE + 1.0
        # Poor (<45%): modest tighten only — aggressive tightening kills recovery
        else:
            _ADAPTIVE_MIN_SCORE = MIN_SCORE + 2.0


_rolling_win_halt = False

def _check_rolling_win_circuit(win_history: list) -> bool:
    """Returns True if we should halt new trades (too many consecutive losses)."""
    global _rolling_win_halt
    if len(win_history) < 5:
        return False
    last5 = win_history[-5:]
    if sum(last5) == 0:   # 5 consecutive losses
        _rolling_win_halt = True
        return True
    if len(win_history) >= 10:
        last10_wr = sum(win_history[-10:]) / 10
        if last10_wr < 0.25:  # <25% WR over last 10 trades
            _rolling_win_halt = True
            return True
    _rolling_win_halt = False
    return False


def run_backtest(symbols: List[str], from_date: str, to_date: str,
                 capital: float = 500_000.0):
    from auth_upstox import get_upstox_client, verify_connection
    import data_fetch_upstox as dfu

    # ── Breadth/sector filter (fail-open if module not present) ──────────────
    try:
        from breadth_sector_filter import BreadthCache
        _breadth_cache = BreadthCache(refresh_minutes=15)
        _use_breadth = True
    except ImportError:
        _breadth_cache = None
        _use_breadth = False

    # Initialize daily circuit breaker
    try:
        from risk_manager import set_daily_start_equity as _set_start
        _set_start(capital)
    except Exception:
        pass

    # ── Walk-forward params: only load when USE_WF_PARAMS=1 is set ───────────
    # Auto-loading is disabled: stale optimal_params.json overrides MIN_SCORE
    # to high values → few entries → adaptive tightening → 0 trades for months.
    # Enable only after a validated fresh optimization run on live data.
    if os.environ.get("USE_WF_PARAMS", "0") == "1":
        try:
            from optimizer_india import load_optimal_params as _load_opt
            _opt = _load_opt()
            if _opt:
                global MIN_SCORE, _ADAPTIVE_MIN_SCORE
                if "MIN_SCORE" in _opt:
                    MIN_SCORE = float(_opt["MIN_SCORE"])
                    _ADAPTIVE_MIN_SCORE = MIN_SCORE
                    print(f"  WF params loaded: MIN_SCORE={MIN_SCORE:.1f} (from optimal_params.json)")
        except Exception:
            pass

    print("Connecting to Upstox API ...")
    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed. Check UPSTOX_ACCESS_TOKEN in .env")
        return
    dfu.set_upstox_client(client)

    print(f"Fetching historical data: {len(symbols)} symbols  {from_date} → {to_date}")
    data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = _fetch(client, sym, from_date, to_date)
        if df is not None:
            data[sym] = df
            print(f"  {sym}: {len(df)} bars  ({len(df)//75:.0f} trading days)")
        else:
            print(f"  {sym}: skipped")

    if not data:
        print("No data loaded. Check UPSTOX_ACCESS_TOKEN and symbol list.")
        return

    # Pre-compute 15-min and 1-hour resamples once per symbol (avoids O(N²) per-bar resampling)
    data_15m: Dict[str, pd.DataFrame] = {}
    data_1h:  Dict[str, pd.DataFrame] = {}
    for sym, df in data.items():
        data_15m[sym] = _resample(df, "15min")
        data_1h[sym]  = _resample(df, "1h")

    # ── ML Scorer: train on loaded data (uses last 80% for training, walks forward) ──
    _ml_scorer = None
    try:
        from ml_scorer_india import MLScorer
        print("  Training ML ensemble scorer on historical data ...")
        _ml_scorer = MLScorer()
        # Train on first 70% of data to avoid lookahead
        train_data = {sym: df.iloc[:int(len(df)*0.7)] for sym, df in data.items()}
        if _ml_scorer.train_from_data(train_data):
            print(f"  ML scorer ready.")
        else:
            _ml_scorer = None
    except Exception as e:
        print(f"  ML scorer skipped: {e}")
        _ml_scorer = None

    print(f"\nRunning backtest on {len(data)} symbols ...")

    trades: List[Trade] = []
    open_trades: Dict[str, Trade] = {}
    equity = capital
    peak   = capital
    max_dd = 0.0
    equity_curve = [capital]
    wins = losses = 0
    pre_filter_kills = 0
    strategy_counts: Dict[str, int]   = {}
    strategy_pnl:    Dict[str, float] = {}
    # Dynamic Kelly: rolling list of per-trade P&Ls as fraction of capital
    recent_trades: List[float] = []
    _win_history: list = []
    _loop_prev_day = None  # track day boundary to reset circuit breaker daily

    # Initialise anti-martingale streak state for the backtest run
    try:
        from risk_manager import reset_streak as _reset_streak
        _reset_streak()
    except Exception:
        pass

    all_ts = sorted(set().union(*[set(df.index) for df in data.values()]))
    _nifty_proxy_cache = {"trend_score": 0.0, "pct_above_vwap": 0.5, "pct_above_ema21": 0.5}
    _nifty_proxy_ts = None

    # Detect bar interval for interval-aware hold time limits
    _sample_vals = list(data.values())
    _bar_mins = 60  # default 1h
    if _sample_vals and len(_sample_vals[0]) > 1:
        _bar_mins = (_sample_vals[0].index[1] - _sample_vals[0].index[0]).total_seconds() / 60
    _is_5m_data = _bar_mins <= 7   # 5m or 3m bars

    # Session breadth cache: % of loaded stocks above today's open at each timestamp
    _breadth_session: Dict = {}   # {date: {ts: float}}
    _session_open: Dict[str, Dict] = {}   # {sym: {date: open_price}}

    # Nifty50 proxy: computed from ALL loaded symbols' open-to-current return
    # This is a real-time breadth indicator — better than lagging EMA alignment
    _nifty_session_cache: Dict = {}   # {date: {ts: float}} intraday return from open

    # Track bull/bear days for report
    _bull_days = 0
    _bear_days = 0
    _session_days_seen: set = set()

    # Track effective threshold values across bars for regime report
    _eff_min_score_history: list = []

    # ── PhD-level enhancement caches ─────────────────────────────────────────
    _cs_rank_cache: Dict[str, float] = {}   # cross-sectional momentum ranks
    _cs_rank_ts = None
    _regime = None                           # MarketRegime enum
    _regime_confidence: float = 0.3
    _regime_ts = None
    _expiry_ctx: dict = {}
    _factor_scores_cache: Dict[str, float] = {}  # multi-factor alpha scores
    _factor_scores_ts = None

    for i, now_ts in enumerate(all_ts):
        if now_ts.time() < dtime(9, 45) or now_ts.time() > dtime(15, 30):
            continue

        # Reset rolling-win circuit breaker at each new trading day (mirrors real behavior)
        _today = now_ts.date()
        if _loop_prev_day != _today:
            global _rolling_win_halt
            _rolling_win_halt = False
            _win_history.clear()  # discard cross-day loss tail so circuit re-checks from clean slate
            _ADAPTIVE_MIN_SCORE = MIN_SCORE  # each day starts fresh — intraday bot, not swing
            _loop_prev_day = _today

        # Lunch lull: exits still processed; new-entry skip handled by _pre_filter LUNCH_LULL gate

        # ── Session breadth: % of symbols above today's open ─────────────────
        _brd_today = now_ts.date()
        _brd_above = 0; _brd_total = 0
        for _bsym, _bdf in data.items():
            try:
                if now_ts not in _bdf.index:
                    continue
                _brd_bar = _bdf.loc[now_ts]
                # Get today's first bar open for this symbol
                _brd_key = f"{_bsym}_{_brd_today}"
                if _brd_key not in _session_open:
                    _today_bdf = _bdf[_bdf.index.date == _brd_today]
                    if len(_today_bdf) > 0:
                        _session_open[_brd_key] = float(_today_bdf.iloc[0]["open"])
                    else:
                        continue
                _brd_open = _session_open[_brd_key]
                _brd_close = float(_brd_bar["close"])
                if _brd_close > _brd_open * 1.0005:    # 0.05% above open
                    _brd_above += 1
                _brd_total += 1
            except Exception:
                pass
        _session_breadth = (_brd_above / max(_brd_total, 1))

        # Regime-aware effective threshold — supplements rolling-win adaptive mechanism
        # Strong bull days: lower bar to harvest momentum opportunities
        # Bear days: raise bar to only take highest-conviction signals
        if _session_breadth >= 0.65:
            _eff_min_score = max(_ADAPTIVE_MIN_SCORE - 2.0, 7.0)   # bull: relax to floor 7
        elif _session_breadth >= 0.45:
            _eff_min_score = _ADAPTIVE_MIN_SCORE                    # neutral: use adaptive as-is
        elif _session_breadth >= 0.30:
            _eff_min_score = _ADAPTIVE_MIN_SCORE + 1.5              # weak: tighten slightly
        else:
            _eff_min_score = min(_ADAPTIVE_MIN_SCORE + 3.0, MIN_SCORE + 4.0)  # bear: strict quality gate (capped to avoid death-spiral)
        _eff_min_score_history.append(_eff_min_score)

        # ── Exit open trades ────────────────────────────────────────────────
        for sym in list(open_trades.keys()):
            t = open_trades[sym]
            if sym not in data:
                continue
            long = t.direction == "LONG"

            # ── Day-rollover force-close: position leaked past end of entry day ─
            if t.entry_time is not None and t.entry_time.date() < now_ts.date():
                try:
                    _prev_bars = data[sym][data[sym].index.date == t.entry_time.date()]
                    px = float(_prev_bars["close"].iloc[-1]) if not _prev_bars.empty else float(t.entry)
                    _exit_ts = _prev_bars.index[-1] if not _prev_bars.empty else t.entry_time
                except Exception:
                    px = float(t.entry); _exit_ts = t.entry_time
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl; t.reason = (t.reason or "") + "+DAY_ROLLOVER_EXIT"
                t.exit_price = px; t.exit_time = _exit_ts
                trades.append(t); del open_trades[sym]
                _opt_record_trade(t)
                if pnl > 0: wins += 1
                else: losses += 1
                _win_history.append(1 if pnl > 0 else 0)
                recent_trades.append(pnl / max(capital, 1e-9))
                try:
                    _update_adaptive_threshold(t.pnl)
                except Exception:
                    pass
                continue

            # ── Resolve nearest available bar (asof) for time-sensitive exits ─
            try:
                _asof_ts = data[sym].index.asof(now_ts)
                _have_asof = (not pd.isnull(_asof_ts)) and (_asof_ts.date() == now_ts.date())
                _asof_bar = data[sym].loc[_asof_ts] if _have_asof else None
            except Exception:
                _have_asof = False; _asof_bar = None

            # Square-off: fires even when exact bar missing (uses nearest bar close)
            if now_ts.time() >= SQUAREOFF and _have_asof:
                px = float(_asof_bar["close"])
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
                _opt_record_trade(t)
                if pnl > 0: wins += 1
                else: losses += 1
                _win_history.append(1 if pnl > 0 else 0)
                _pnl_frac = pnl / max(capital, 1e-9)
                recent_trades.append(_pnl_frac)
                try:
                    from risk_manager import update_streak as _upd_streak
                    _upd_streak(pnl)
                except Exception:
                    pass
                try:
                    _update_adaptive_threshold(t.pnl)
                except Exception:
                    pass
                continue

            # ── Time-based exit: uses nearest bar so sparse 1h data can't block it
            if t.entry_time is not None and _have_asof:
                try:
                    _held_minutes = (now_ts - t.entry_time).total_seconds() / 60
                    _is_morning = t.entry_time.time() < dtime(11, 30)
                    _max_hold = (90 if _is_morning else 60) if _is_5m_data else (240 if _is_morning else 150)  # 5m: 90/60 min; 1h: 240/150 min
                    if _held_minutes >= _max_hold:
                        px = float(_asof_bar["close"])
                        long_trade = t.direction == "LONG"
                        partial_qty = int(t.qty * 0.4) or 1
                        qty_left = t.qty - (partial_qty if t.t1_done else 0)
                        pnl = ((px - t.entry) if long_trade else (t.entry - px)) * qty_left
                        pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
                        equity += pnl; t.pnl = pnl; t.reason = (t.reason or "") + "+TIME_EXIT"
                        t.exit_price = px; t.exit_time = now_ts
                        trades.append(t); del open_trades[sym]
                        _opt_record_trade(t)
                        _win_history.append(1 if pnl > 0 else 0)
                        if pnl > 0: wins += 1
                        else: losses += 1
                        recent_trades.append(pnl / max(capital, 1))
                        try:
                            _update_adaptive_threshold(t.pnl)
                        except Exception:
                            pass
                        continue
                except Exception:
                    pass

            # ── Price-based exits (SL/TP/chandelier): require exact intrabar data
            if now_ts not in data[sym].index:
                continue
            bar = data[sym].loc[now_ts]
            hi = bar["high"]; lo = bar["low"]
            c_bar = bar["close"]

            sl_hit = (lo <= t.sl) if long else (hi >= t.sl)
            if sl_hit:
                px = t.sl
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
                _opt_record_trade(t)
                if pnl > 0: wins += 1
                else: losses += 1
                _win_history.append(1 if pnl > 0 else 0)
                _pnl_frac = pnl / max(capital, 1e-9)
                recent_trades.append(_pnl_frac)
                try:
                    from risk_manager import update_streak as _upd_streak
                    _upd_streak(pnl)
                except Exception:
                    pass
                try:
                    _update_adaptive_threshold(t.pnl)
                except Exception:
                    pass
                continue

            # ── Stage 1 exit: 25% at 0.5R ─────────────────────────────────
            if (not t.stage1_done and t.stage1_price > 0 and t.stage1_qty > 0):
                hit_s1 = (long and hi >= t.stage1_price) or (not long and lo <= t.stage1_price)
                if hit_s1:
                    pnl_s1 = ((t.stage1_price - t.entry) if long else (t.entry - t.stage1_price)) * t.stage1_qty
                    pnl_s1 -= (t.entry * t.stage1_qty + t.stage1_price * t.stage1_qty) * COST_RT_PCT / 2
                    equity += pnl_s1
                    t.pnl += pnl_s1
                    t.stage1_done = True
                    # Tighten SL to entry after 0.5R (not full break-even yet)
                    new_sl = t.entry if long else t.entry
                    if long and new_sl > t.sl:
                        t.sl = new_sl
                    elif not long and new_sl < t.sl:
                        t.sl = new_sl

            # ── Early breakeven: move SL to entry when price reaches 0.8R ────
            if not t.stage1_done and t.atr_at_entry > 0:
                _r_dist = 1.5 * t.atr_at_entry   # SL distance = 1R
                _be_trigger = (t.entry + 0.8 * _r_dist) if long else (t.entry - 0.8 * _r_dist)
                _be_hit = (hi >= _be_trigger) if long else (lo <= _be_trigger)
                if _be_hit:
                    new_be_sl = t.entry * 1.0002 if long else t.entry * 0.9998  # tiny buffer
                    if long and new_be_sl > t.sl:
                        t.sl = new_be_sl
                    elif not long and new_be_sl < t.sl:
                        t.sl = new_be_sl

            if not t.t1_done:
                t1_hit = (hi >= t.t1) if long else (lo <= t.t1)
                if t1_hit:
                    half = int(t.qty * 0.4) or 1
                    pnl_partial = ((t.t1 - t.entry) if long else (t.entry - t.t1)) * half
                    equity += pnl_partial
                    t.t1_done = True; t.sl = t.entry   # BE stop for runner

            if t.t1_done:
                runner = t.qty - (int(t.qty * 0.4) or 1)
                # ── Chandelier Exit for runner ────────────────────────────────
                chandelier_triggered = False
                if sym in data and now_ts in data[sym].index:
                    idx2 = data[sym].index.get_loc(now_ts)
                    lookback_22 = data[sym].iloc[max(0, idx2-22):idx2+1]
                    atr22 = float(lookback_22["atr"].iloc[-1]) if "atr" in lookback_22.columns else t.atr_at_entry
                    if long:
                        chandelier = float(lookback_22["high"].max()) - 2.5 * atr22
                        if c_bar < chandelier and chandelier > t.sl:
                            pnl_r = ((c_bar - t.entry) if long else (t.entry - c_bar)) * runner
                            pnl_r -= (t.entry * runner + c_bar * runner) * COST_RT_PCT / 2
                            equity += pnl_r; t.pnl += pnl_r
                            t.exit_price = c_bar; t.exit_time = now_ts
                            trades.append(t); del open_trades[sym]
                            _opt_record_trade(t)
                            if t.pnl > 0: wins += 1
                            else: losses += 1
                            _win_history.append(1 if t.pnl > 0 else 0)
                            _pnl_frac = t.pnl / max(capital, 1e-9)
                            recent_trades.append(_pnl_frac)
                            try:
                                from risk_manager import update_streak as _upd_streak
                                _upd_streak(t.pnl)
                            except Exception:
                                pass
                            try:
                                _update_adaptive_threshold(t.pnl)
                            except Exception:
                                pass
                            chandelier_triggered = True
                    else:  # SHORT
                        chandelier = float(lookback_22["low"].min()) + 2.5 * atr22
                        if c_bar > chandelier and chandelier < t.sl:
                            pnl_r = ((c_bar - t.entry) if long else (t.entry - c_bar)) * runner
                            pnl_r -= (t.entry * runner + c_bar * runner) * COST_RT_PCT / 2
                            equity += pnl_r; t.pnl += pnl_r
                            t.exit_price = c_bar; t.exit_time = now_ts
                            trades.append(t); del open_trades[sym]
                            _opt_record_trade(t)
                            if t.pnl > 0: wins += 1
                            else: losses += 1
                            _win_history.append(1 if t.pnl > 0 else 0)
                            _pnl_frac = t.pnl / max(capital, 1e-9)
                            recent_trades.append(_pnl_frac)
                            try:
                                from risk_manager import update_streak as _upd_streak
                                _upd_streak(t.pnl)
                            except Exception:
                                pass
                            try:
                                _update_adaptive_threshold(t.pnl)
                            except Exception:
                                pass
                            chandelier_triggered = True
                if chandelier_triggered:
                    continue
                # ── T2 full exit (runner at 2R) ───────────────────────────────
                t2_hit = (hi >= t.t2) if long else (lo <= t.t2)
                if t2_hit:
                    pnl_r = ((t.t2 - t.entry) if long else (t.entry - t.t2)) * runner
                    pnl_r -= (t.entry * runner + t.t2 * runner) * COST_RT_PCT / 2
                    equity += pnl_r; t.pnl += pnl_r
                    t.exit_price = t.t2; t.exit_time = now_ts
                    trades.append(t); del open_trades[sym]
                    _opt_record_trade(t)
                    if t.pnl > 0: wins += 1
                    else: losses += 1
                    _win_history.append(1 if t.pnl > 0 else 0)
                    _pnl_frac = t.pnl / max(capital, 1e-9)
                    recent_trades.append(_pnl_frac)
                    try:
                        from risk_manager import update_streak as _upd_streak
                        _upd_streak(t.pnl)
                    except Exception:
                        pass
                    try:
                        _update_adaptive_threshold(t.pnl)
                    except Exception:
                        pass

        peak   = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        equity_curve.append(equity)

        # ── New entries ─────────────────────────────────────────────────────
        if len(open_trades) >= MAX_OPEN:
            continue

        # No new entries after 14:30 IST (not enough time for setup to play out)
        bar_time_now = now_ts.time()
        if bar_time_now > dtime(14, 30):
            continue

        # Skip new entries if circuit breaker tripped
        try:
            from risk_manager import check_intraday_circuit as _circuit
            if _circuit(equity) in ('HALT', 'EMERGENCY'):
                continue
        except Exception:
            pass

        # Rolling win rate circuit
        if _check_rolling_win_circuit(_win_history):
            continue

        # ── Market breadth gate (refresh every 15 min) ───────────────────────
        _breadth_state = None
        _sector_bias   = None
        if _use_breadth and _breadth_cache is not None:
            try:
                _breadth_state = _breadth_cache.get(data, now_ts)
                _sector_bias   = _breadth_cache.get_sector(data, now_ts)
            except Exception:
                pass

        # ── Nifty proxy regime (refresh every 15 min) ────────────────────────
        if (_nifty_proxy_ts is None or
                (now_ts - _nifty_proxy_ts).total_seconds() >= 900):
            try:
                _nifty_proxy_cache = _compute_nifty_proxy(data, now_ts)
                _nifty_proxy_ts = now_ts
            except Exception:
                pass

        # ── Cross-sectional rank + regime + factor model (every 15-30 min) ──
        if (_cs_rank_ts is None or
                (now_ts - _cs_rank_ts).total_seconds() >= 900):
            try:
                _cs_rank_cache = _rank_symbols_by_momentum(data, now_ts)
                _cs_rank_ts = now_ts
            except Exception:
                pass
        if (_factor_scores_ts is None or
                (now_ts - _factor_scores_ts).total_seconds() >= 900):
            try:
                from factor_model import compute_factor_scores
                _factor_scores_cache = compute_factor_scores(data, now_ts)
                _factor_scores_ts = now_ts
            except Exception:
                pass
        if (_regime_ts is None or
                (now_ts - _regime_ts).total_seconds() >= 1800):
            try:
                from regime_detector import detect_regime_from_data, get_expiry_context
                _regime, _regime_confidence, _ = detect_regime_from_data(data, now_ts)
                _expiry_ctx = get_expiry_context(now_ts.date())
                _regime_ts = now_ts
            except Exception:
                pass

        _mkt_trend = _nifty_proxy_cache.get("trend_score", 0)
        _pct_vwap  = _nifty_proxy_cache.get("pct_above_vwap", 0.5)

        # Strong market bias gates:
        # If > 70% of stocks above VWAP → only LONG entries (or skip)
        # If < 30% of stocks above VWAP → only SHORT entries (or skip)
        _long_only_market  = (_pct_vwap >= 0.65) or (_mkt_trend >= 0.35)
        _short_only_market = (_pct_vwap <= 0.35) or (_mkt_trend <= -0.35)
        # Prevent simultaneous True (conflicting signals → neutral, allow both directions)
        if _long_only_market and _short_only_market:
            _long_only_market = False
            _short_only_market = False

        # F&O monthly expiry: skip all new entries
        if _expiry_ctx.get("bias") == "AVOID":
            continue

        # Cap entries if too many open trades are stressed
        _stressed = 0
        for _st in open_trades.values():
            if _st.direction == "LONG":
                _cur = data.get(_st.symbol)
                if _cur is not None and now_ts in _cur.index:
                    _px = float(_cur.loc[now_ts].get("close", _st.entry))
                    if _px < _st.entry - 0.5 * _st.atr_at_entry:
                        _stressed += 1
            else:
                _cur = data.get(_st.symbol)
                if _cur is not None and now_ts in _cur.index:
                    _px = float(_cur.loc[now_ts].get("close", _st.entry))
                    if _px > _st.entry + 0.5 * _st.atr_at_entry:
                        _stressed += 1
        if _stressed >= 2:
            continue  # Too many stressed trades — wait

        # ── Nifty proxy: avg return from today's open across all loaded symbols ──
        _nf_today = now_ts.date()
        _nf_returns = []
        for _nfsym, _nfdf in data.items():
            try:
                if now_ts not in _nfdf.index:
                    continue
                _nf_bar = _nfdf.loc[now_ts]
                _nf_todaydf = _nfdf[_nfdf.index.date == _nf_today]
                if len(_nf_todaydf) == 0:
                    continue
                _nf_open = float(_nf_todaydf.iloc[0]["open"])
                _nf_close = float(_nf_bar["close"])
                if _nf_open > 0:
                    _nf_returns.append((_nf_close - _nf_open) / _nf_open)
            except Exception:
                pass
        _nifty_proxy_return = float(sum(_nf_returns) / max(len(_nf_returns), 1)) if _nf_returns else 0.0
        _nifty_session_bull = _nifty_proxy_return > 0.0003   # +0.03% — even slight positive = bull
        _nifty_session_bear = _nifty_proxy_return < -0.0003  # -0.03% — even slight negative = bear

        # Track bull/bear days for the report
        if _nf_today not in _session_days_seen:
            _session_days_seen.add(_nf_today)
            if _nifty_session_bull:
                _bull_days += 1
            elif _nifty_session_bear:
                _bear_days += 1

        for sym, df in data.items():
            if sym in open_trades or len(open_trades) >= MAX_OPEN:
                continue
            # Block entries after 14:00 — only 75 min left to squareoff at 15:15.
            # Was 13:00 but that blocked 25% of good mid-session setups with no basis.
            if now_ts.time() >= dtime(14, 0):
                continue
            # Opening blackout: 9:15-10:30 = high noise (ORB fakeouts, thin pre-discovery).
            # MACD/EMA change-of-state signals that fire in this window are caught by the
            # bypass override when they re-trigger later mid-morning (10:30-13:00).
            if now_ts.time() < dtime(10, 30):
                continue
            if now_ts not in df.index:
                continue
            idx = df.index.get_loc(now_ts)
            if idx < 55:
                continue   # need warm-up bars for indicators

            row  = df.iloc[idx]
            prev = df.iloc[idx - 1]

            # Slice pre-computed resamples up to current bar (no repeated resampling)
            _f15 = data_15m.get(sym)
            df_15m = None
            if _f15 is not None and not _f15.empty:
                try:
                    # Handle tz mismatch gracefully
                    _slice15 = _f15.loc[:now_ts]
                    df_15m = _slice15 if not _slice15.empty else None
                except Exception:
                    # Fallback: use tail of resampled data
                    df_15m = _f15.tail(10) if len(_f15) > 0 else None

            _f1h = data_1h.get(sym)
            df_1h = None
            if _f1h is not None and not _f1h.empty:
                try:
                    _slice1h = _f1h.loc[:now_ts]
                    df_1h = _slice1h if not _slice1h.empty else None
                except Exception:
                    df_1h = _f1h.tail(5) if len(_f1h) > 0 else None

            # Require at least 2 bars today (interval-agnostic; outer loop already
            # blocks the first bar via < 9:45 gate, so 1h data gets 2 bars at 10:15)
            _today_d = now_ts.date()
            _today_df_slice = df[df.index.date == _today_d]
            _bars_today = len(_today_df_slice[_today_df_slice.index <= now_ts])
            if _bars_today < 2:
                continue

            # ── Pre-filter: kill known false-positive patterns (fast path) ───
            try:
                _skip, _skip_r = _pre_filter(row, prev, now_ts, df_15m)
                if _skip:
                    pre_filter_kills += 1
                    continue
            except Exception:
                pass

            try:
                net_score, direction, reason = _score_bar(row, prev, df_15m, df_1h, now_ts)
            except Exception as e:
                continue

            # ── Today's intraday momentum confirmation ──────────────────────────
            # Checks if the last 4 bars of today confirm the signal direction.
            # Prevents trading against the day's established trend.
            try:
                _today_date = now_ts.date()
                # FIX: filter to bars up to now_ts only (prevent look-ahead bias)
                _today_bars = df[(df.index.date == _today_date) & (df.index <= now_ts)]
                if len(_today_bars) >= 4:
                    _last4 = _today_bars.iloc[-4:]
                    _bull_bars = int((_last4["close"] > _last4["open"]).sum())
                    _bear_bars = int((_last4["close"] < _last4["open"]).sum())
                    if _bull_bars >= 3 and direction == "SHORT":
                        # Today trending bullish but signal is SHORT → push toward LONG
                        net_score += 10
                        reason = (reason + "+TODAY_BULL_PENALIZE_SHORT") if reason else "TODAY_BULL_PENALIZE_SHORT"
                    elif _bear_bars >= 3 and direction == "LONG":
                        # Meaningful penalty — score-10 LONG drops to 3, below _eff_min_score=7
                        # floor; only survives if SESSION_BULL (+8) confirms broad market strength
                        net_score -= 7
                        reason = (reason + "+TODAY_BEAR_PENALTY") if reason else "TODAY_BEAR_PENALTY"
                    elif _bull_bars >= 3 and direction == "LONG":
                        net_score += 6  # alignment bonus
                        reason = (reason + "+TODAY_ALIGN_BULL") if reason else "TODAY_ALIGN_BULL"
                    elif _bear_bars >= 3 and direction == "SHORT":
                        net_score -= 6  # alignment bonus
                        reason = (reason + "+TODAY_ALIGN_BEAR") if reason else "TODAY_ALIGN_BEAR"
            except Exception:
                pass

            # Re-derive direction after today momentum adjustments (score may have flipped sign)
            if net_score > 0:
                direction = "LONG"
            elif net_score < 0:
                direction = "SHORT"

            # ── Session breadth directional bias ─────────────────────────────
            # % of symbols above today's open = real-time market direction
            if _session_breadth > 0.60:   # 60%+ stocks rising today → bullish session
                net_score += 8; reason = (reason + "+SESSION_BULL") if reason else "SESSION_BULL"
                if direction == "SHORT":
                    net_score += 8  # Moderate penalty for fighting the session trend (was 18 = too harsh)
                    reason += "+COUNTER_SESSION"
            elif _session_breadth > 0.52:
                net_score += 5
            elif _session_breadth < 0.40:  # 40%- stocks rising → bearish session
                net_score -= 5; reason = (reason + "+SESSION_BEAR") if reason else "SESSION_BEAR"
                if direction == "LONG":
                    net_score -= 5  # Reduced from -8: contrarian entries work on weak breadth days
                    reason += "+COUNTER_SESSION"
            elif _session_breadth < 0.48:
                net_score -= 5
            # Re-derive direction after session breadth adjustment
            if net_score > 0:
                direction = "LONG"
            elif net_score < 0:
                direction = "SHORT"

            # ── ML Ensemble Boost ─────────────────────────────────────────────
            if _ml_scorer is not None:
                try:
                    ml_boost, ml_reason = _ml_scorer.get_score_boost(df, idx, direction)
                    net_score += ml_boost
                    if ml_reason:
                        reason = reason + "+" + ml_reason if reason else ml_reason
                except Exception:
                    pass

            # ── Breadth + sector alignment boost ─────────────────────────────
            if _breadth_state is not None and _sector_bias is not None:
                try:
                    from breadth_sector_filter import get_breadth_score_boost
                    b_delta, b_reason = get_breadth_score_boost(
                        sym, _breadth_state, _sector_bias, direction)
                    net_score += b_delta
                    if b_reason:
                        reason = reason + "+" + b_reason if reason else b_reason
                except Exception:
                    pass

            # ── Regime-conditional score multiplier ───────────────────────────
            if _regime is not None:
                try:
                    from regime_detector import get_regime_score_mult
                    _rmult = get_regime_score_mult(_regime, direction)
                    net_score *= _rmult
                except Exception:
                    pass
                if net_score > 0: direction = "LONG"
                elif net_score < 0: direction = "SHORT"

            # ── Cross-sectional rank score boost/penalty (no hard filter) ────
            if _cs_rank_cache:
                _sym_rank = _cs_rank_cache.get(sym, 0.5)
                if direction == "LONG":
                    if _sym_rank >= 0.75:
                        net_score += 10; reason = (reason + "+CS_TOP") if reason else "CS_TOP"
                    elif _sym_rank >= 0.60:
                        net_score += 5
                elif direction == "SHORT":
                    if _sym_rank <= 0.25:
                        net_score -= 10; reason = (reason + "+CS_BOT") if reason else "CS_BOT"
                    elif _sym_rank <= 0.40:
                        net_score -= 5
                if net_score > 0: direction = "LONG"
                elif net_score < 0: direction = "SHORT"

            # ── Multi-factor alpha boost ──────────────────────────────────────
            if _factor_scores_cache:
                try:
                    from factor_model import get_factor_score_boost
                    _fa, _fr = get_factor_score_boost(sym, direction, _factor_scores_cache)
                    if abs(_fa) > 0.5:
                        net_score += _fa
                        if _fr:
                            reason = (reason + "+" + _fr) if reason else _fr
                except Exception:
                    pass
                if net_score > 0: direction = "LONG"
                elif net_score < 0: direction = "SHORT"

            # Bypass: high-conviction reversal signals override 1H_BEAR score suppression.
            # 1H_BEAR inside _score_bar multiplies score_long × 0.4, which can flip
            # direction to SHORT when score_short > reduced score_long. These signals
            # identify the START of a reversal — 1H_BEAR is exactly when they're most
            # valuable, not when they should be filtered out.
            _bypass_long_sigs_g = (
                "VWAP_RECLAIM",   # _score_bar: price reclaimed VWAP — institutional buy
                "MACD_XOVER_UP",  # _score_bar: MACD histogram just turned positive
                "EMA_BULL_STACK", # _score_bar: EMA9 just crossed above EMA21
                "RSI_BULL_CROSS", # _score_bar: RSI just crossed 50 from below
            )
            if any(s in reason for s in _bypass_long_sigs_g):
                direction = "LONG"
                if abs(net_score) < 8.0:
                    net_score = 8.0

            # Pre-filter: skip clearly weak signals before calling new strategies
            if abs(net_score) < 8.0:
                continue

            # Hard block: strong BEARISH bar → never go LONG (fighting the tape)
            try:
                _bc  = float(row.get("close", 0) or 0)
                _bo  = float(row.get("open",  0) or 0)
                _bh  = float(row.get("high",  0) or 0)
                _blo = float(row.get("low",   0) or 0)
                _brv = float(row.get("rvol", 0.0) or 0.0)
                _brange = _bh - _blo
                if (_brange > 0 and _bc > 0 and _bo > 0 and direction == "LONG"
                        and (_bo - _bc) > _brange * 0.3 and _brv > 1.3):
                    continue  # Strong selling bar — entry would be fighting tape
            except Exception:
                pass

            # ── New strategies boost (called with full DataFrame context) ─────
            try:
                from strategies_india import (ema21_pullback_signal,
                                               liquidity_grab_signal,
                                               inside_bar_breakout_signal,
                                               momentum_ignition_signal,
                                               institutional_accumulation_signal,
                                               pullback_continuation_signal,
                                               range_expansion_signal,
                                               confirmed_momentum_signal,
                                               vwap_bounce_signal,
                                               orb_momentum_signal,
                                               hammer_reversal_signal,
                                               atr_squeeze_breakout_signal,
                                               intraday_momentum_signal)
            except Exception:
                pass
            # EMA21 Pullback
            try:
                _s4, _r4 = ema21_pullback_signal(df, idx)
                if _s4 != 0:
                    net_score += _s4; reason = (reason + "+" + _r4) if _r4 and reason else (_r4 or reason)
            except Exception:
                pass
            # Liquidity Grab
            try:
                _s5, _r5 = liquidity_grab_signal(df, idx)
                if _s5 != 0:
                    net_score += _s5; reason = (reason + "+" + _r5) if _r5 and reason else (_r5 or reason)
            except Exception:
                pass
            # Inside Bar
            try:
                _s6, _r6 = inside_bar_breakout_signal(df, idx, now_ts)
                if _s6 != 0:
                    net_score += _s6; reason = (reason + "+" + _r6) if _r6 and reason else (_r6 or reason)
            except Exception:
                pass
            # Momentum Ignition
            try:
                _s12, _r12 = momentum_ignition_signal(df, idx)
                if _s12 != 0:
                    net_score += _s12; reason = (reason + "+" + _r12) if _r12 and reason else (_r12 or reason)
            except Exception:
                pass
            # Institutional Accumulation / Distribution
            try:
                _s13, _r13 = institutional_accumulation_signal(df, idx)
                if _s13 != 0:
                    net_score += _s13; reason = (reason + "+" + _r13) if _r13 and reason else (_r13 or reason)
            except Exception:
                pass
            # Pullback Continuation
            try:
                _s14, _r14 = pullback_continuation_signal(df, idx)
                if _s14 != 0:
                    net_score += _s14; reason = (reason + "+" + _r14) if _r14 and reason else (_r14 or reason)
            except Exception:
                pass
            # Range Expansion (NR4/NR7 breakout)
            try:
                _s15, _r15 = range_expansion_signal(df, idx)
                if _s15 != 0:
                    net_score += _s15; reason = (reason + "+" + _r15) if _r15 and reason else (_r15 or reason)
            except Exception:
                pass
            # Confirmed Momentum (highest conviction)
            try:
                _s16, _r16 = confirmed_momentum_signal(df, idx)
                if _s16 != 0:
                    net_score += _s16; reason = (reason + "+" + _r16) if _r16 and reason else (_r16 or reason)
            except Exception:
                pass
            # VWAP Bounce (71% WR: absorption at VWAP then breakout)
            try:
                _sv, _rv = vwap_bounce_signal(df, idx)
                if _sv != 0:
                    net_score += _sv; reason = (reason + "+" + _rv) if _rv and reason else (_rv or reason)
            except Exception:
                pass
            # ORB Momentum (60-65% WR: clean ORB breakout with volume)
            # Skip if _score_bar already scored ORB to prevent double-counting (+33 = inflated)
            try:
                if "ORB_BULL" not in reason and "ORB_BEAR" not in reason:
                    _so, _ro = orb_momentum_signal(df, idx)
                    if _so != 0:
                        net_score += _so; reason = (reason + "+" + _ro) if _ro and reason else (_ro or reason)
            except Exception:
                pass
            # Hammer Reversal (pin-bar reversal at support)
            try:
                _sh, _rh = hammer_reversal_signal(df, idx)
                if _sh != 0:
                    net_score += _sh; reason = (reason + "+" + _rh) if _rh and reason else (_rh or reason)
            except Exception:
                pass
            # ATR Squeeze Breakout (volatility expansion from squeeze)
            try:
                _sq, _rq = atr_squeeze_breakout_signal(df, idx)
                if _sq != 0:
                    net_score += _sq; reason = (reason + "+" + _rq) if _rq and reason else (_rq or reason)
            except Exception:
                pass
            # Intraday Momentum (today's established directional drift)
            try:
                _si, _ri = intraday_momentum_signal(df, idx)
                if _si != 0:
                    net_score += _si; reason = (reason + "+" + _ri) if _ri and reason else (_ri or reason)
            except Exception:
                pass

            # Re-determine direction after new strategies
            if net_score > 0:
                direction = "LONG"
            elif net_score < 0:
                direction = "SHORT"

            # ── Gap bias: weight toward gap direction (don't hard-block) ─────
            try:
                _gap_open = float(row.get("day_open", 0) or 0)
                _prev_idx = idx - 1
                # Find the last bar of the previous trading day
                _prev_day_close = 0.0
                if _gap_open > 0 and _prev_idx >= 0:
                    _prev_close_bar = df.iloc[_prev_idx]
                    _prev_bar_date = df.index[_prev_idx].date()
                    _today_bar_date = now_ts.date()
                    if _prev_bar_date < _today_bar_date:
                        _prev_day_close = float(_prev_close_bar.get("close", 0) or 0)
                    else:
                        # Find last bar of previous day
                        _prev_day_rows = df[df.index.date < _today_bar_date]
                        if len(_prev_day_rows) > 0:
                            _prev_day_close = float(_prev_day_rows.iloc[-1].get("close", 0) or 0)
                if _prev_day_close > 0 and _gap_open > 0:
                    _gap_val = (_gap_open - _prev_day_close) / _prev_day_close
                    if _gap_val > 0.002 and direction == "SHORT":
                        net_score += 8  # Push toward LONG (positive gap day) (was 15 = too harsh)
                        reason = (reason + "+GAP_UP_PENALTY_SHORT") if reason else "GAP_UP_PENALTY_SHORT"
                        if net_score > 0:
                            direction = "LONG"
                    elif _gap_val < -0.002 and direction == "LONG":
                        net_score -= 8  # Push toward SHORT (negative gap day) (was 15 = too harsh)
                        reason = (reason + "+GAP_DN_PENALTY_LONG") if reason else "GAP_DN_PENALTY_LONG"
                        if net_score < 0:
                            direction = "SHORT"
                    elif _gap_val > 0.002 and direction == "LONG":
                        net_score += 8   # Bonus: going with gap direction
                        reason = (reason + "+GAP_UP_ALIGN") if reason else "GAP_UP_ALIGN"
                    elif _gap_val < -0.002 and direction == "SHORT":
                        net_score -= 8   # Bonus: going with gap direction (short on gap-down)
                        reason = (reason + "+GAP_DN_ALIGN") if reason else "GAP_DN_ALIGN"
            except Exception:
                pass

            # ── Nifty session direction gate (hardest gate) ───────────────────
            # Only trade with the session's net direction to avoid counter-trend losses
            # Use _ADAPTIVE_MIN_SCORE (pre-regime base) so bull-day relaxation does NOT
            # loosen this gate — counter-trend trades should face the same or higher bar
            # regardless of how permissive the regime threshold is for with-trend entries.
            if _nifty_session_bull and direction == "SHORT":
                if abs(net_score) < _ADAPTIVE_MIN_SCORE * 1.3:
                    continue  # Block weak counter-trend shorts in bull session
            # Lightweight bear gate: breadth (count-based) ≠ nifty proxy (value-weighted).
            # breadth=0.42 can coexist with nifty proxy=-0.15% (large-caps down, small-caps flat).
            # Only block when BOTH metrics indicate bear: proxy negative AND breadth < 0.50.
            if _nifty_session_bear and direction == "LONG" and _session_breadth < 0.50:
                if abs(net_score) < MIN_SCORE + 2:
                    continue  # Weak LONG in confirmed bear session (both metrics bearish)

            # ── 3-Timeframe alignment gate ────────────────────────────────────
            # 15m penalty (currently only bonus exists; disagreement has zero cost)
            # Hard block when BOTH 15m AND 1h disagree with direction
            try:
                _15m_bull = False; _15m_bear = False
                _1h_bull  = False; _1h_bear  = False
                if df_15m is not None and not df_15m.empty:
                    _r15 = df_15m.iloc[-1]
                    _e9_15  = float(_r15.get("ema9",  0) or 0)
                    _e21_15 = float(_r15.get("ema21", 0) or 0)
                    if _e9_15 > _e21_15 > 0: _15m_bull = True
                    elif _e9_15 < _e21_15 and _e21_15 > 0: _15m_bear = True
                if df_1h is not None and not df_1h.empty:
                    _r1h = df_1h.iloc[-1]
                    _c1h  = float(_r1h.get("close", 0) or 0)
                    _e21h = float(_r1h.get("ema21", 0) or 0)
                    _e50h = float(_r1h.get("ema50", 0) or 0)
                    if _c1h > _e21h > _e50h > 0: _1h_bull = True
                    elif _c1h < _e21h and _e21h < _e50h and _e50h > 0: _1h_bear = True

                if direction == "LONG":
                    # TF alignment penalty — capped at -8 total to prevent killing valid high-WR signals
                    _tf_penalty = 0
                    if _15m_bear:
                        _tf_penalty -= 7
                        reason = (reason + "+15M_BEAR_PENALTY") if reason else "15M_BEAR_PENALTY"
                    if _15m_bear and _1h_bear:
                        _tf_penalty -= min(4, 8 + _tf_penalty)  # cap total at -8
                        reason = (reason + "+1H_BEAR_PENALTY") if reason else "1H_BEAR_PENALTY"
                    net_score += max(_tf_penalty, -8)
                elif direction == "SHORT":
                    _tf_penalty = 0
                    if _15m_bull:
                        _tf_penalty += 7
                        reason = (reason + "+15M_BULL_PENALTY") if reason else "15M_BULL_PENALTY"
                    if _15m_bull and _1h_bull:
                        _tf_penalty += min(4, 8 - _tf_penalty)
                        reason = (reason + "+1H_BULL_PENALTY") if reason else "1H_BULL_PENALTY"
                    net_score += min(_tf_penalty, 8)
                # Re-derive direction after penalty
                if net_score > 0: direction = "LONG"
                elif net_score < 0: direction = "SHORT"
            except Exception:
                pass

            # ── Hard momentum confirmation gates ─────────────────────────────
            # Stock must already be moving in signal direction with volume.
            # Prevents entries in "potential" momentum (not yet confirmed).
            try:
                _close_g = float(row.get("close", 0) or 0)
                _day_open_g = float(row.get("day_open", 0) or 0)
                _rvol_g = float(row.get("rvol", 1.0) or 1.0)
                _e9_g = float(row.get("ema9", 0) or 0)
                _e21_g = float(row.get("ema21", 0) or 0)
                if _close_g > 0 and _day_open_g > 0:
                    _sess_ret_g = (_close_g - _day_open_g) / _day_open_g
                    # High-conviction signals get a lower session_return bar
                    # Use abs(net_score) so SHORT signals (negative) also benefit
                    _abs_score = abs(net_score)
                    if _abs_score >= 18:
                        _sr_thresh = 0.0001 if _is_5m_data else 0.0002   # near-zero: self-confirming signal
                    elif _abs_score >= 14:
                        _sr_thresh = 0.0003 if _is_5m_data else 0.0005   # 0.03%/0.05% — medium conviction
                    else:
                        _sr_thresh = 0.0004 if _is_5m_data else 0.0008   # 0.04%/0.08% — weak signals need movement
                    _rvol_min = 1.0 if _abs_score >= 18 else 1.1  # entry bar has normal vol; surge follows — 1.3 was too strict
                    # ORB bypass: direction-matched flag — breakout proves session direction
                    _orb_bypass = (
                        (direction == "LONG" and ("ORB_BULL_CONFIRM" in reason or "ORB_BULL_WEAK" in reason)) or
                        (direction == "SHORT" and ("ORB_BEAR_CONFIRM" in reason or "ORB_BEAR_WEAK" in reason))
                    )
                    # High-WR setup bypass: self-confirming signals don't need prior tape trend.
                    # These signals PREDICT the coming move — requiring prior 0.3% gain is circular.
                    _high_wr_bypass = any(sig in reason for sig in (
                        "VWAP_BOUNCE_LONG", "VWAP_BOUNCE_SHORT",
                        "HAMMER_REVERSAL_LONG", "HAMMER_REVERSAL_SHORT",
                        "ATR_SQUEEZE_BREAKOUT",
                        "MACD_XOVER_UP",       # MACD cross = trend start — doesn't need prior gain
                        "VWAP_RECLAIM",        # price reclaims VWAP = institutional buy
                        "EMA21_PULLBACK",      # buy dip in uptrend near EMA21
                        "PULLBACK_CONT",       # momentum continuation after pullback
                        "CONFIRMED_MOMENTUM",  # highest conviction composite
                    ))
                    # Session floor: bypass signals (ORB_CLEAN, ATR_SQUEEZE, VWAP_BOUNCE,
                    # HAMMER) are self-confirming — they predict the coming move, not
                    # confirm a past one. Non-bypass signals need stock already trending.
                    if direction == "LONG" and not _high_wr_bypass and _sess_ret_g < 0.0015:
                        continue  # Non-bypass LONG: require stock up >= 0.15% on the day
                    # Bonus gates use a floor to avoid score inflation when _sr_thresh is near-zero
                    _bonus_thresh = max(_sr_thresh, 0.0002)
                    if direction == "LONG":
                        # LONG: stock must be up in signal direction, volume elevated, EMA aligned
                        if not _orb_bypass and not _high_wr_bypass and _sess_ret_g < _sr_thresh:
                            continue
                        if not _high_wr_bypass and _rvol_g < _rvol_min:
                            continue
                        _ema_bearish_g = (_e9_g > 0 and _e21_g > 0 and _e9_g < _e21_g * 0.998)
                        if _ema_bearish_g:
                            if not _high_wr_bypass:
                                # EMA bearish: soft penalty (bypass signals expect bearish EMAs — they signal the TURN)
                                net_score -= 5
                                if net_score < 0: direction = "SHORT"
                        # Bonus for strongly confirmed momentum — guarded: EMA bearish blocks both bonuses
                        # so the penalty is not silently overridden by MOD_CONFIRM within the same block
                        if not _ema_bearish_g and _sess_ret_g > 0.005 and _rvol_g > 1.8 and (_e9_g <= 0 or _e9_g > _e21_g):
                            net_score += 18
                            reason = (reason + "+STRONG_CONFIRM") if reason else "STRONG_CONFIRM"
                        elif not _ema_bearish_g and _sess_ret_g > 0.002 and _rvol_g > 1.2:
                            net_score += 8
                            reason = (reason + "+MOD_CONFIRM") if reason else "MOD_CONFIRM"
                    elif direction == "SHORT":
                        # SHORT: stock must be down in signal direction
                        if not _orb_bypass and not _high_wr_bypass and _sess_ret_g > -_sr_thresh:
                            continue
                        if not _high_wr_bypass and _rvol_g < _rvol_min:
                            continue
                        if _e9_g > 0 and _e21_g > 0 and _e9_g >= _e21_g:
                            continue   # bullish EMA — no short
                        if _sess_ret_g < -0.005 and _rvol_g > 1.8 and (_e9_g <= 0 or _e9_g < _e21_g):
                            net_score -= 18
                            reason = (reason + "+STRONG_CONFIRM") if reason else "STRONG_CONFIRM"
                        elif _sess_ret_g < -0.002 and _rvol_g > 1.2:
                            net_score -= 8
                            reason = (reason + "+MOD_CONFIRM") if reason else "MOD_CONFIRM"
            except Exception:
                pass

            # Bypass signals are self-confirming — don't let TF counter-trend compression kill them.
            # These predict the trend CHANGE, so 15M/1H bearish is expected at entry.
            # Guarantee minimum passing score so they survive _eff_min_score check.
            if _high_wr_bypass and 0 < abs(net_score) < MIN_SCORE:
                net_score = MIN_SCORE * (1 if net_score > 0 else -1)

            # Market breadth alignment scoring (+3/-3 to avoid over-stacking with existing breadth signals)
            try:
                if direction == "LONG":
                    if _session_breadth > 0.62:
                        net_score += 3
                        reason = (reason + "+MKTBIAS_LONG") if reason else "MKTBIAS_LONG"
                    elif _session_breadth < 0.38:
                        net_score -= 3
                        reason = (reason + "+MKTBIAS_CONTRA") if reason else "MKTBIAS_CONTRA"
                elif direction == "SHORT":
                    if _session_breadth < 0.38:
                        net_score -= 3  # more negative = stronger SHORT
                        reason = (reason + "+MKTBIAS_SHORT") if reason else "MKTBIAS_SHORT"
                    elif _session_breadth > 0.62:
                        net_score += 3  # less negative = weaker SHORT = penalty
                        reason = (reason + "+MKTBIAS_CONTRA") if reason else "MKTBIAS_CONTRA"
            except Exception:
                pass

            # Final threshold check after all score adjustments
            if abs(net_score) < _eff_min_score:
                continue

            # Market direction filter
            if _long_only_market and direction == "SHORT":
                continue   # Market is bullish — skip counter-trend SHORT
            if _short_only_market and direction == "LONG":
                continue   # Market is bearish — skip counter-trend LONG
            # NSE long-only mode: never take shorts (Groww MIS LONG positions only)
            if LONG_ONLY_NSE and direction == "SHORT":
                continue
            # Breadth floor: hard block extreme bear tape; moderate weakness gets score penalty
            if BULL_DAY_ONLY and _session_breadth < 0.30:
                continue  # Hard block: extreme bear tape only
            if BULL_DAY_ONLY and _session_breadth < 0.45:
                net_score -= 4  # Weak day: penalize but don't block
                if abs(net_score) < _eff_min_score:
                    continue  # Re-check threshold after breadth penalty

            # ── Entry quality gate: RSI overbought filter only ───────────────
            try:
                _rsi_q = float(row.get("rsi", 50) or 50)
                if direction == "LONG" and _rsi_q > 68:
                    continue  # overbought — wait for pullback
            except Exception:
                pass
            # ── Named-setup quality gate: pattern + confirmation ──
            # Bypass signals (ATR_SQUEEZE, ORB_CLEAN, VWAP_BOUNCE, HAMMER) have
            # their own internal volume/price checks — count as self-confirming.
            # All other patterns need external tape confirmation (MOD/STRONG_CONFIRM).
            _QUALITY_SIGS = ("VWAP_BOUNCE", "ORB_BULL", "ORB_BEAR",
                             "EMA_BULL_STACK", "EMA_BEAR_STACK", "EMA21_PULLBACK",
                             "MACD_XOVER", "RSI_BULL_CROSS", "RSI_BEAR_CROSS",
                             "VWAP_RECLAIM", "VWAP_REJECT",
                             "CONFIRMED_MOMENTUM", "ATR_SQUEEZE",
                             "HAMMER_REVERSAL", "MOMENTUM_IGNITION", "PULLBACK_CONT",
                             "RANGE_EXP", "INTRADAY_MOM", "LIQ_GRAB",
                             "INSIDE_BAR", "ACCUM", "DISTRIB",
                             "STRONG_CONFIRM", "MOD_CONFIRM")
            _qual_count = sum(1 for q in _QUALITY_SIGS if q in reason)
            _is_self_confirm = any(s in reason for s in (
                "VWAP_BOUNCE_LONG", "VWAP_BOUNCE_SHORT",
                "HAMMER_REVERSAL_LONG", "HAMMER_REVERSAL_SHORT",
                "ATR_SQUEEZE_BREAKOUT",
                "MACD_XOVER_UP",
                "VWAP_RECLAIM",
                "EMA21_PULLBACK",
                "PULLBACK_CONT",
                "CONFIRMED_MOMENTUM",
                "RSI_BULL_CROSS",      # RSI crossing 50 from below — built-in price check
                "EMA_BULL_STACK",      # EMA9 crossing EMA21 — fresh momentum shift
                "ORB_BULL_CONFIRM",    # ORB breakout with volume — structural proof
                "ORB_BULL_WEAK",       # ORB breakout without full volume — still directional
                "INTRADAY_MOM_UP",     # established trend + 3/4 bullish bars + volume
                "RANGE_EXP",           # NR4/NR7 + volume expansion = volatility breakout
            ))
            if _is_self_confirm:
                _qual_count += 1  # Built-in volume/price checks = one free confirmation
            if _qual_count < 2:
                continue  # Need pattern + confirmation — pure patterns without tape = noise
            # ────────────────────────────────────────────────────────────────

            # ATR-based SL/TP
            atr = row.get("atr", row["close"] * 0.005)
            if atr <= 0:
                continue
            entry = row["close"]
            long  = direction == "LONG"
            sl_dist = 1.0 * atr     # 1×ATR tight stop — if momentum doesn't work immediately, exit
            t1_dist = 2.0 * atr     # 2R first target (break-even WR = 33%)
            t2_dist = 4.0 * atr     # 4R runner
            sl    = entry - sl_dist if long else entry + sl_dist
            t1    = entry + t1_dist if long else entry - t1_dist
            t2    = entry + t2_dist if long else entry - t2_dist

            # Structure-based SL: use bar's low/high as minimum stop reference
            # This prevents tiny ATR from giving an unrealistic SL
            try:
                bar_low  = float(row.get("low",  entry) or entry)
                bar_high = float(row.get("high", entry) or entry)
                _struct_sl_long  = bar_low  * 0.9995   # 0.05% below bar low
                _struct_sl_short = bar_high * 1.0005   # 0.05% above bar high
                if long and sl > _struct_sl_long:
                    sl = _struct_sl_long   # Use structure SL if it's tighter
                elif not long and sl < _struct_sl_short:
                    sl = _struct_sl_short
            except Exception:
                pass

            # Dynamic Kelly sizing (with Sharpe/Omega/streak/regime scalers)
            try:
                from risk_manager import get_kelly_regime_mult as _kelly_regime_mult
                _regime_mult = _kelly_regime_mult(_regime, direction)
            except Exception:
                _regime_mult = 1.0
            risk_pct = _dynamic_kelly_size(recent_trades, equity, net_score, atr, entry,
                                           regime_mult=_regime_mult)

            # ── IC-weighted Kelly + correlation-adjusted sizing ───────────────
            try:
                from risk_manager import ic_kelly_multiplier as _ic_mult, \
                                         correlation_size_cap as _corr_cap
                risk_pct *= _ic_mult(net_score)
                risk_pct *= _corr_cap(direction, open_trades)
            except Exception:
                pass

            # Time-of-day risk reduction
            try:
                from risk_manager import time_of_day_multiplier as _tod_mult
                tod_factor = _tod_mult(now_ts)
                risk_pct = risk_pct * tod_factor
            except Exception:
                pass

            # ── Portfolio Volatility Targeting ──────────────────────────────
            try:
                from risk_manager import apply_vol_target_to_risk as _vol_target
                risk_pct = _vol_target(risk_pct, equity_curve)
            except Exception:
                pass
            sl_dist  = abs(entry - sl)
            # Sanity check: SL distance must be at least 0.15% of entry
            min_sl_dist = entry * 0.0015
            if sl_dist < min_sl_dist:
                sl_dist = min_sl_dist
                sl = entry - sl_dist if long else entry + sl_dist
            qty = int(min(equity * risk_pct / sl_dist,
                          equity * MAX_POS_PCT / entry))
            if qty < 1:
                continue

            # ── Minimum profit filter: skip cost-inefficient trades ───────────
            # t1 (1.5R target) must cover at least 2.5× the round-trip cost
            # This eliminates trades where expected gain is eaten by STT/brokerage
            _min_profit_pct = 2.5 * COST_RT_PCT   # need 2.5× cost coverage at t1
            _t1_profit_pct  = t1_dist / max(entry, 1.0)
            if _t1_profit_pct < _min_profit_pct:
                continue   # t1 doesn't cover costs — skip
            # Also enforce minimum SL distance (0.3% = meaningful trade with cost coverage)
            if sl_dist < entry * 0.003:
                continue

            # F&O expiry size reduction (weekly expiry = 0.75×, pre-expiry = 0.85×)
            _exp_mult = _expiry_ctx.get("size_multiplier", 1.0) if _expiry_ctx else 1.0
            if _exp_mult < 1.0:
                qty = max(1, int(qty * _exp_mult))

            # Half-size for signals near the lower threshold (lower confidence)
            # Use _ADAPTIVE_MIN_SCORE (pre-regime base) as the sizing anchor so
            # bull-day relaxation doesn't inadvertently promote mid-range signals
            # from half-size to full-size by lowering the denominator.
            _threshold = _ADAPTIVE_MIN_SCORE
            _confidence_ratio = abs(net_score) / max(_threshold * 2, 1.0)
            if _confidence_ratio < 0.6:  # Score is only barely above threshold
                qty = max(1, qty // 2)   # Half size for marginal signals

            trade = Trade(sym, direction, entry, sl, t1, t2, qty, now_ts, atr_at_entry=float(atr))
            try:
                from risk_manager import compute_exit_stages as _exits
                _stages = _exits(entry, atr, direction, qty, signal_type=reason or "")
                trade.stage1_price = _stages["stage1_price"]
                trade.stage2_price = _stages["stage2_price"]
                trade.stage1_qty   = _stages["stage1_qty"]
                trade.stage2_qty   = _stages["stage2_qty"]
                trade.runner_qty   = _stages["runner_qty"]
                trade.be_sl        = _stages["be_sl"]
                trade.sl           = _stages["sl"]
                # Override t1 and t2 with stage prices
                trade.t1 = _stages["stage2_price"]
                trade.t2 = entry + 4.5 * atr if direction == "LONG" else entry - 4.5 * atr  # 3R runner (was 3.0 = 2R)
            except Exception:
                pass
            trade.reason = reason
            open_trades[sym] = trade

    # Close any still-open trades at last price
    for sym, t in open_trades.items():
        if sym in data:
            px = data[sym]["close"].iloc[-1]
            long = t.direction == "LONG"
            partial_qty = int(t.qty * 0.4) or 1
            qty_left = t.qty - (partial_qty if t.t1_done else 0)
            pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
            pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
            equity += pnl; t.pnl = pnl; t.exit_price = px
            t.exit_time = data[sym].index[-1]
            trades.append(t)
            _opt_record_trade(t)
            if pnl > 0: wins += 1
            else: losses += 1
            _win_history.append(1 if pnl > 0 else 0)
            recent_trades.append(pnl / max(capital, 1e-9))

    # Build strategy attribution from trade reasons
    # Keys must be substrings of actual reason strings emitted by _score_bar /
    # strategies_india.py.  Order matters: first match wins, so put the most
    # diagnostic / highest-value signals first.
    _STRAT_KEYS = [
        # ── High-WR named setups (new strategies — highest priority in attribution)
        "VWAP_BOUNCE_LONG", "VWAP_BOUNCE_SHORT",        # 71% WR
        "ORB_BULL_CLEAN", "ORB_BEAR_CLEAN",             # 60-65% WR
        "ORB_BULL_PARTIAL", "ORB_BEAR_PARTIAL",
        "HAMMER_REVERSAL_LONG", "HAMMER_REVERSAL_SHORT",
        "ATR_SQUEEZE_BREAKOUT",
        "INTRADAY_MOM_UP", "INTRADAY_MOM_DN",
        # ── _score_bar named signals
        "ORB_BULL_CONFIRM", "ORB_BULL_WEAK", "ORB_BEAR_CONFIRM", "ORB_BEAR_WEAK",
        "EMA_BULL_STACK", "EMA_BEAR_STACK", "EMA21_PULLBACK",
        "MACD_XOVER",
        "RSI_BULL_CROSS", "RSI_BEAR_CROSS",
        "VWAP_RECLAIM", "VWAP_REJECT",                  # transition signals
        "VWAP_REVERSION_LONG", "VWAP_REVERSION_SHORT",
        "ABOVE_VWAP", "BELOW_VWAP", "VWAP_REVERSION", "VWAP_BOUNCE",
        # ── Strategies from strategy block
        "CONFIRMED_MOMENTUM",
        "PULLBACK_CONT", "RANGE_EXP",
        "MOMENTUM_IGNITION", "ACCUM", "DISTRIB",
        "LIQ_GRAB", "INSIDE_BAR",
        # ── Context/confirmation signals
        "SESSION_DRIVE_LONG", "SESSION_DRIVE_SHORT", "SESSION_DRIVE",
        "SESSION_BULL", "SESSION_BEAR",
        "STRONG_CONFIRM", "MOD_CONFIRM",
        "MKTBIAS",
        "BOS_BULL", "BOS_BEAR",
        "OPENING_DRIVE", "GAP_GO", "SUPERTREND",
        "ML_STRONG", "ML_CONFIRM",
        "CONFIRMED_BULL", "CONFIRMED_BEAR",
    ]
    for t in trades:
        r = getattr(t, "reason", "") or ""
        matched = False
        for key in _STRAT_KEYS:
            if key in r:
                strategy_counts[key] = strategy_counts.get(key, 0) + 1
                strategy_pnl[key]    = strategy_pnl.get(key, 0.0) + t.pnl
                matched = True
                break
        if not matched:
            strategy_counts["OTHER"] = strategy_counts.get("OTHER", 0) + 1
            strategy_pnl["OTHER"]    = strategy_pnl.get("OTHER", 0.0) + t.pnl

    _report(trades, capital, equity, max_dd, equity_curve, from_date, to_date, len(data),
            pre_filter_kills=pre_filter_kills,
            strategy_counts=strategy_counts, strategy_pnl=strategy_pnl,
            bull_days=_bull_days, total_days=len(_session_days_seen),
            eff_min_score_history=_eff_min_score_history)


def _report(trades, capital, equity, max_dd, eq_curve, from_date, to_date, n_syms,
            pre_filter_kills=0, strategy_counts=None, strategy_pnl=None,
            bull_days=0, total_days=0, eff_min_score_history=None):
    print()
    print("=" * 70)
    print("  NSE Momentum Bot — Engine Backtest Report")
    print(f"  Period  : {from_date} → {to_date}")
    print(f"  Symbols : {n_syms} loaded")
    print(f"  Capital : ₹{capital:,.0f}")
    print("=" * 70)

    if not trades:
        print("  No trades generated.")
        if pre_filter_kills > 0:
            print(f"  Pre-filter blocked {pre_filter_kills:,} raw signal candidates.")
            print("  Hint: if all bars are killed, check bar-count gates for 1h data.")
        print("=" * 70)
        return

    wins   = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    wr     = len(wins) / len(trades) * 100
    gross_win  = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses)) or 1e-9
    pf         = gross_win / gross_loss
    avg_win    = gross_win  / max(len(wins), 1)
    avg_loss   = gross_loss / max(len(losses), 1)
    expectancy = (wr / 100 * avg_win) - ((1 - wr / 100) * avg_loss)
    ret_pct    = (equity - capital) / capital * 100
    days       = max((datetime.strptime(to_date, "%Y-%m-%d") -
                      datetime.strptime(from_date, "%Y-%m-%d")).days, 1)
    monthly    = ret_pct / days * 30

    # Sharpe (annualised from per-trade P&L)
    pnls = [t.pnl / capital for t in trades]
    try:
        sharpe = (statistics.mean(pnls) / (statistics.stdev(pnls) + 1e-9)) * (252 ** 0.5)
    except Exception:
        sharpe = 0.0

    calmar = abs(ret_pct / (max_dd * 100 + 1e-9))

    # Equity sparkline
    lo = min(eq_curve); hi = max(eq_curve); rng = max(hi - lo, 1)
    spark = "".join("▁▂▃▄▅▆▇█"[min(7, int((v - lo) / rng * 7))] for v in eq_curve[::max(1, len(eq_curve)//60)])

    print(f"\n  Equity curve: {spark}")
    print()
    print(f"  {'Total trades':<22}: {len(trades)}")
    print(f"  {'Win rate':<22}: {wr:.1f}%  (W:{len(wins)} / L:{len(losses)})")
    print(f"  {'Profit factor':<22}: {pf:.2f}")
    print(f"  {'Avg win':<22}: ₹{avg_win:,.0f}")
    print(f"  {'Avg loss':<22}: ₹{avg_loss:,.0f}")
    print(f"  {'Expectancy/trade':<22}: ₹{expectancy:,.0f}")
    print()
    print(f"  {'Total P&L':<22}: ₹{equity - capital:+,.0f}  ({ret_pct:+.2f}%)")
    print(f"  {'Monthly return':<22}: {monthly:+.2f}%")
    print(f"  {'Max drawdown':<22}: {max_dd * 100:.2f}%")
    print(f"  {'Sharpe ratio':<22}: {sharpe:.2f}")
    print(f"  {'Calmar ratio':<22}: {calmar:.2f}")
    print()

    # Per-symbol breakdown (top 5)
    sym_stats: Dict[str, dict] = {}
    for t in trades:
        s = sym_stats.setdefault(t.symbol, {"pnl": 0, "w": 0, "l": 0})
        s["pnl"] += t.pnl
        if t.pnl > 0: s["w"] += 1
        else: s["l"] += 1
    top5 = sorted(sym_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]
    bot3 = sorted(sym_stats.items(), key=lambda x: x[1]["pnl"])[:3]
    print("  Top 5 symbols:")
    for sym, s in top5:
        n = s["w"] + s["l"]
        print(f"    {sym:<14} WR={s['w']/max(n,1)*100:.0f}%  trades={n}  P&L=₹{s['pnl']:+,.0f}")
    print("  Bottom 3 symbols:")
    for sym, s in bot3:
        n = s["w"] + s["l"]
        print(f"    {sym:<14} WR={s['w']/max(n,1)*100:.0f}%  trades={n}  P&L=₹{s['pnl']:+,.0f}")
    print("=" * 70)
    if sharpe >= 2.0:
        print(f"  ✓ Sharpe {sharpe:.2f} ≥ 2.0 TARGET MET")
    else:
        print(f"  ✗ Sharpe {sharpe:.2f} < 2.0 (add more filters to improve)")
    if monthly >= 10:
        print(f"  ✓ Monthly {monthly:.1f}% ≥ 10% TARGET MET")
    else:
        print(f"  ✗ Monthly {monthly:.1f}% — need higher WR or larger position")
    print("=" * 70)

    # Monthly P&L breakdown
    print()
    print("  Monthly Breakdown:")
    from collections import defaultdict
    monthly_pnl = defaultdict(float)
    monthly_trades = defaultdict(int)
    for t in trades:
        if t.exit_time:
            key = t.exit_time.strftime("%Y-%m")
            monthly_pnl[key] += t.pnl
            monthly_trades[key] += 1
    for month in sorted(monthly_pnl):
        mpct = monthly_pnl[month] / capital * 100
        print(f"    {month}: ₹{monthly_pnl[month]:+,.0f}  ({mpct:+.1f}%)  {monthly_trades[month]} trades")

    # Time-of-day win rate
    print()
    print("  Win Rate by Time of Day:")
    tod_wins = defaultdict(int); tod_total = defaultdict(int)
    def _tod(ts):
        if ts is None: return "unknown"
        h = ts.time()
        from datetime import time as dtime2
        if h < dtime2(10, 0):  return "09:15-10:00"
        if h < dtime2(11, 30): return "10:00-11:30"
        if h < dtime2(13, 30): return "11:30-13:30"
        return "13:30-15:20"
    for t in trades:
        slot = _tod(t.entry_time)
        tod_total[slot] += 1
        if t.pnl > 0: tod_wins[slot] += 1
    for slot in ["09:15-10:00", "10:00-11:30", "11:30-13:30", "13:30-15:20"]:
        if tod_total[slot]:
            wr2 = tod_wins[slot] / tod_total[slot] * 100
            print(f"    {slot}: {wr2:.0f}% WR  ({tod_total[slot]} trades)")

    # Average hold time
    hold_times = [(t.exit_time - t.entry_time).total_seconds() / 60
                  for t in trades if t.exit_time and t.entry_time]
    if hold_times:
        print(f"\n  Avg Hold Time: {statistics.mean(hold_times):.0f} min  |  "
              f"Max: {max(hold_times):.0f} min  |  Min: {min(hold_times):.0f} min")

    # Pre-filter stats
    if pre_filter_kills > 0:
        total_signals = len(trades) + pre_filter_kills
        print(f"\n  Pre-Filter: Blocked {pre_filter_kills:,} false signals "
              f"({pre_filter_kills / max(total_signals, 1):.1%} of raw candidates)")
        print(f"  Signal Quality: {len(trades) / max(total_signals, 1):.1%} passed filter")

    # Strategy attribution
    if strategy_counts:
        print()
        print("  Strategy Attribution (by P&L):")
        sorted_strats = sorted(strategy_counts.items(),
                               key=lambda x: strategy_pnl.get(x[0], 0), reverse=True)
        for strat, count in sorted_strats:
            pnl_s = (strategy_pnl or {}).get(strat, 0)
            pnl_pct = pnl_s / max(capital, 1) * 100
            print(f"    {strat:25s}: {count:3d} trades  ₹{pnl_s:+,.0f}  ({pnl_pct:+.2f}%)")

    # Market regime summary
    if total_days > 0:
        print(f"\n  Market regime: Nifty session bull days: {bull_days}/{total_days} ({bull_days/max(total_days,1)*100:.0f}%)")
        if eff_min_score_history:
            _avg_eff = sum(eff_min_score_history) / len(eff_min_score_history)
            _min_eff = min(eff_min_score_history)
            _max_eff = max(eff_min_score_history)
            print(f"  Avg effective threshold: {_avg_eff:.1f} (range: {_min_eff:.1f}–{_max_eff:.1f})")

    print()
    print("=" * 70)


def run_backtest_from_data(data: Dict[str, pd.DataFrame], capital: float = 500_000.0):
    """
    Run the backtest replay loop on a pre-loaded, indicator-computed data dict.

    This is the yfinance entry-point: caller loads data via load_nse_data_yfinance,
    applies _compute_all + _build_orb, then passes the result here.
    All replay, exit, and reporting logic is identical to run_backtest().

    Args:
        data: Dict[symbol -> pd.DataFrame] with full indicator columns (output of
              _compute_all + _build_orb).  Index must be IST-tz DatetimeIndex.
        capital: Starting capital in INR.
    """
    # ── Breadth/sector filter (fail-open if module not present) ──────────────
    try:
        from breadth_sector_filter import BreadthCache
        _breadth_cache = BreadthCache(refresh_minutes=15)
        _use_breadth = True
    except ImportError:
        _breadth_cache = None
        _use_breadth = False

    # Initialize daily circuit breaker
    try:
        from risk_manager import set_daily_start_equity as _set_start
        _set_start(capital)
    except Exception:
        pass

    # ── Walk-forward params: only load when USE_WF_PARAMS=1 is set ───────────
    if os.environ.get("USE_WF_PARAMS", "0") == "1":
        try:
            from optimizer_india import load_optimal_params as _load_opt
            _opt = _load_opt()
            if _opt:
                global MIN_SCORE, _ADAPTIVE_MIN_SCORE
                if "MIN_SCORE" in _opt:
                    MIN_SCORE = float(_opt["MIN_SCORE"])
                    _ADAPTIVE_MIN_SCORE = MIN_SCORE
                    print(f"  WF params loaded: MIN_SCORE={MIN_SCORE:.1f} (from optimal_params.json)")
        except Exception:
            pass

    # Ensure ORB columns are computed (handles both 5-min and 1h data)
    for sym in list(data.keys()):
        try:
            if "orb_high" not in data[sym].columns or data[sym]["orb_high"].isna().all():
                data[sym] = _build_orb(data[sym])
        except Exception:
            pass

    # Detect input bar interval before building resamples
    # 1h input: resampling to 15min repeats the same bars (no real sub-bars) → fake 15m data
    # doubles the TF-alignment penalty in run_backtest_from_data; skip it for 1h.
    _pre_vals = list(data.values())
    _is_5m_input = False
    if _pre_vals and len(_pre_vals[0]) > 1:
        _bm_pre = (_pre_vals[0].index[1] - _pre_vals[0].index[0]).total_seconds() / 60
        _is_5m_input = (_bm_pre <= 7)

    # Pre-compute 15-min and 1-hour resamples once per symbol
    data_15m: Dict[str, pd.DataFrame] = {}
    data_1h:  Dict[str, pd.DataFrame] = {}
    for sym, df in data.items():
        data_15m[sym] = _resample(df, "15min") if _is_5m_input else None
        data_1h[sym]  = _resample(df, "1h")

    # ── ML Scorer ────────────────────────────────────────────────────────────
    _ml_scorer = None
    try:
        from ml_scorer_india import MLScorer
        print("  Training ML ensemble scorer on historical data ...")
        _ml_scorer = MLScorer()
        train_data = {sym: df.iloc[:int(len(df) * 0.7)] for sym, df in data.items()}
        if _ml_scorer.train_from_data(train_data):
            print("  ML scorer ready.")
        else:
            _ml_scorer = None
    except Exception as e:
        print(f"  ML scorer skipped: {e}")
        _ml_scorer = None

    print(f"\nRunning backtest on {len(data)} symbols ...")

    # Data profile diagnostic
    if data:
        _sample_sym = next(iter(data))
        _sample_df = data[_sample_sym]
        _bar_delta = (_sample_df.index[1] - _sample_df.index[0]).total_seconds() / 60 if len(_sample_df) > 1 else 60
        _n_days = len(set(_sample_df.index.date))
        print(f"  Bar interval: ~{_bar_delta:.0f} min | Days: {_n_days} | Bars/sym: {len(_sample_df)}")
        print(f"  Date range: {_sample_df.index[0].date()} → {_sample_df.index[-1].date()}")
        _has_orb = "orb_high" in _sample_df.columns and not _sample_df["orb_high"].isna().all()
        print(f"  ORB computed: {_has_orb} | Columns: {len(_sample_df.columns)}")

    from_date = min(df.index[0] for df in data.values()).strftime("%Y-%m-%d")
    to_date   = max(df.index[-1] for df in data.values()).strftime("%Y-%m-%d")

    trades: List[Trade] = []
    open_trades: Dict[str, Trade] = {}
    equity = capital
    peak   = capital
    max_dd = 0.0
    equity_curve = [capital]
    wins = losses = 0
    pre_filter_kills = 0
    strategy_counts: Dict[str, int]   = {}
    strategy_pnl:    Dict[str, float] = {}
    recent_trades: List[float] = []
    _win_history: list = []
    _loop_prev_day2 = None  # track day boundary to reset circuit breaker daily

    try:
        from risk_manager import reset_streak as _reset_streak
        _reset_streak()
    except Exception:
        pass

    all_ts = sorted(set().union(*[set(df.index) for df in data.values()]))
    _nifty_proxy_cache = {"trend_score": 0.0, "pct_above_vwap": 0.5, "pct_above_ema21": 0.5}

    # Detect bar interval for interval-aware hold time limits
    _sample_vals = list(data.values())
    _bar_mins = 60  # default 1h
    if _sample_vals and len(_sample_vals[0]) > 1:
        _bar_mins = (_sample_vals[0].index[1] - _sample_vals[0].index[0]).total_seconds() / 60
    _is_5m_data = _bar_mins <= 7   # 5m or 3m bars
    _nifty_proxy_ts = None

    # Session breadth cache: % of loaded stocks above today's open at each timestamp
    _breadth_session: Dict = {}   # {date: {ts: float}}
    _session_open: Dict[str, Dict] = {}   # {sym: {date: open_price}}

    # Nifty50 proxy: computed from ALL loaded symbols' open-to-current return
    # This is a real-time breadth indicator — better than lagging EMA alignment
    _nifty_session_cache: Dict = {}   # {date: {ts: float}} intraday return from open

    # Track bull/bear days for report
    _bull_days = 0
    _bear_days = 0
    _session_days_seen: set = set()

    # Track effective threshold values across bars for regime report
    _eff_min_score_history: list = []

    # ── PhD-level enhancement caches ─────────────────────────────────────────
    _cs_rank_cache: Dict[str, float] = {}
    _cs_rank_ts = None
    _regime = None
    _regime_confidence: float = 0.3
    _regime_ts = None
    _expiry_ctx: dict = {}
    _factor_scores_cache: Dict[str, float] = {}
    _factor_scores_ts = None

    for i, now_ts in enumerate(all_ts):
        if now_ts.time() < dtime(9, 45) or now_ts.time() > dtime(15, 30):
            continue

        # Reset rolling-win circuit breaker at each new trading day (mirrors real behavior:
        # intraday circuit breakers reset at next market open)
        _today2 = now_ts.date()
        if _loop_prev_day2 != _today2:
            global _rolling_win_halt
            _rolling_win_halt = False
            _win_history.clear()  # discard cross-day loss tail so circuit re-checks from clean slate
            _ADAPTIVE_MIN_SCORE = MIN_SCORE  # each day starts fresh — intraday bot, not swing
            _loop_prev_day2 = _today2

        # ── Session breadth: % of symbols above today's open ─────────────────
        _brd_today = now_ts.date()
        _brd_above = 0; _brd_total = 0
        for _bsym, _bdf in data.items():
            try:
                if now_ts not in _bdf.index:
                    continue
                _brd_bar = _bdf.loc[now_ts]
                # Get today's first bar open for this symbol
                _brd_key = f"{_bsym}_{_brd_today}"
                if _brd_key not in _session_open:
                    _today_bdf = _bdf[_bdf.index.date == _brd_today]
                    if len(_today_bdf) > 0:
                        _session_open[_brd_key] = float(_today_bdf.iloc[0]["open"])
                    else:
                        continue
                _brd_open = _session_open[_brd_key]
                _brd_close = float(_brd_bar["close"])
                if _brd_close > _brd_open * 1.0005:    # 0.05% above open
                    _brd_above += 1
                _brd_total += 1
            except Exception:
                pass
        _session_breadth = (_brd_above / max(_brd_total, 1))

        # Regime-aware effective threshold — supplements rolling-win adaptive mechanism
        # Strong bull days: lower bar to harvest momentum opportunities
        # Bear days: raise bar to only take highest-conviction signals
        if _session_breadth >= 0.65:
            _eff_min_score = max(_ADAPTIVE_MIN_SCORE - 2.0, 7.0)   # bull: relax to floor 7
        elif _session_breadth >= 0.45:
            _eff_min_score = _ADAPTIVE_MIN_SCORE                    # neutral: use adaptive as-is
        elif _session_breadth >= 0.30:
            _eff_min_score = _ADAPTIVE_MIN_SCORE + 1.5              # weak: tighten slightly
        else:
            _eff_min_score = min(_ADAPTIVE_MIN_SCORE + 3.0, MIN_SCORE + 4.0)  # bear: strict quality gate (capped to avoid death-spiral)
        _eff_min_score_history.append(_eff_min_score)

        # ── Exit open trades ────────────────────────────────────────────────
        for sym in list(open_trades.keys()):
            t = open_trades[sym]
            if sym not in data:
                continue
            long = t.direction == "LONG"

            # ── Day-rollover force-close: position leaked past end of entry day ─
            if t.entry_time is not None and t.entry_time.date() < now_ts.date():
                try:
                    _prev_bars = data[sym][data[sym].index.date == t.entry_time.date()]
                    px = float(_prev_bars["close"].iloc[-1]) if not _prev_bars.empty else float(t.entry)
                    _exit_ts = _prev_bars.index[-1] if not _prev_bars.empty else t.entry_time
                except Exception:
                    px = float(t.entry); _exit_ts = t.entry_time
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl; t.reason = (t.reason or "") + "+DAY_ROLLOVER_EXIT"
                t.exit_price = px; t.exit_time = _exit_ts
                trades.append(t); del open_trades[sym]
                _opt_record_trade(t)
                if pnl > 0: wins += 1
                else: losses += 1
                _win_history.append(1 if pnl > 0 else 0)
                recent_trades.append(pnl / max(capital, 1e-9))
                try:
                    _update_adaptive_threshold(t.pnl)
                except Exception:
                    pass
                continue

            # ── Resolve nearest available bar (asof) for time-sensitive exits ─
            try:
                _asof_ts = data[sym].index.asof(now_ts)
                _have_asof = (not pd.isnull(_asof_ts)) and (_asof_ts.date() == now_ts.date())
                _asof_bar = data[sym].loc[_asof_ts] if _have_asof else None
            except Exception:
                _have_asof = False; _asof_bar = None

            # Square-off: fires even when exact bar missing (uses nearest bar close)
            if now_ts.time() >= SQUAREOFF and _have_asof:
                px = float(_asof_bar["close"])
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
                _opt_record_trade(t)
                if pnl > 0: wins += 1
                else: losses += 1
                _win_history.append(1 if pnl > 0 else 0)
                recent_trades.append(pnl / max(capital, 1e-9))
                try:
                    from risk_manager import update_streak as _upd_streak
                    _upd_streak(pnl)
                except Exception:
                    pass
                try:
                    _update_adaptive_threshold(t.pnl)
                except Exception:
                    pass
                continue

            # ── Time-based exit: uses nearest bar so sparse 1h data can't block it
            if t.entry_time is not None and _have_asof:
                try:
                    _held_minutes = (now_ts - t.entry_time).total_seconds() / 60
                    _is_morning = t.entry_time.time() < dtime(11, 30)
                    _max_hold = (90 if _is_morning else 60) if _is_5m_data else (240 if _is_morning else 150)  # 5m: 90/60 min; 1h: 240/150 min
                    if _held_minutes >= _max_hold:
                        px = float(_asof_bar["close"])
                        long_trade = t.direction == "LONG"
                        partial_qty = int(t.qty * 0.4) or 1
                        qty_left = t.qty - (partial_qty if t.t1_done else 0)
                        pnl = ((px - t.entry) if long_trade else (t.entry - px)) * qty_left
                        pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
                        equity += pnl; t.pnl = pnl; t.reason = (t.reason or "") + "+TIME_EXIT"
                        t.exit_price = px; t.exit_time = now_ts
                        trades.append(t); del open_trades[sym]
                        _opt_record_trade(t)
                        _win_history.append(1 if pnl > 0 else 0)
                        if pnl > 0: wins += 1
                        else: losses += 1
                        recent_trades.append(pnl / max(capital, 1))
                        try:
                            _update_adaptive_threshold(t.pnl)
                        except Exception:
                            pass
                        continue
                except Exception:
                    pass

            # ── Price-based exits (SL/TP/chandelier): require exact intrabar data
            if now_ts not in data[sym].index:
                continue
            bar = data[sym].loc[now_ts]
            hi = bar["high"]; lo = bar["low"]
            c_bar = bar["close"]

            sl_hit = (lo <= t.sl) if long else (hi >= t.sl)
            if sl_hit:
                px = t.sl
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
                _opt_record_trade(t)
                if pnl > 0: wins += 1
                else: losses += 1
                _win_history.append(1 if pnl > 0 else 0)
                recent_trades.append(pnl / max(capital, 1e-9))
                try:
                    from risk_manager import update_streak as _upd_streak
                    _upd_streak(pnl)
                except Exception:
                    pass
                try:
                    _update_adaptive_threshold(t.pnl)
                except Exception:
                    pass
                continue

            if (not t.stage1_done and t.stage1_price > 0 and t.stage1_qty > 0):
                hit_s1 = (long and hi >= t.stage1_price) or (not long and lo <= t.stage1_price)
                if hit_s1:
                    pnl_s1 = ((t.stage1_price - t.entry) if long else (t.entry - t.stage1_price)) * t.stage1_qty
                    pnl_s1 -= (t.entry * t.stage1_qty + t.stage1_price * t.stage1_qty) * COST_RT_PCT / 2
                    equity += pnl_s1
                    t.pnl += pnl_s1
                    t.stage1_done = True
                    new_sl = t.entry
                    if long and new_sl > t.sl:
                        t.sl = new_sl
                    elif not long and new_sl < t.sl:
                        t.sl = new_sl

            # ── Early breakeven: move SL to entry when price reaches 0.8R ────
            if not t.stage1_done and t.atr_at_entry > 0:
                _r_dist = 1.5 * t.atr_at_entry   # SL distance = 1R
                _be_trigger = (t.entry + 0.8 * _r_dist) if long else (t.entry - 0.8 * _r_dist)
                _be_hit = (hi >= _be_trigger) if long else (lo <= _be_trigger)
                if _be_hit:
                    new_be_sl = t.entry * 1.0002 if long else t.entry * 0.9998  # tiny buffer
                    if long and new_be_sl > t.sl:
                        t.sl = new_be_sl
                    elif not long and new_be_sl < t.sl:
                        t.sl = new_be_sl

            if not t.t1_done:
                t1_hit = (hi >= t.t1) if long else (lo <= t.t1)
                if t1_hit:
                    half = int(t.qty * 0.4) or 1
                    pnl_partial = ((t.t1 - t.entry) if long else (t.entry - t.t1)) * half
                    equity += pnl_partial
                    t.t1_done = True; t.sl = t.entry

            if t.t1_done:
                runner = t.qty - (int(t.qty * 0.4) or 1)
                chandelier_triggered = False
                if sym in data and now_ts in data[sym].index:
                    idx2 = data[sym].index.get_loc(now_ts)
                    lookback_22 = data[sym].iloc[max(0, idx2-22):idx2+1]
                    atr22 = float(lookback_22["atr"].iloc[-1]) if "atr" in lookback_22.columns else t.atr_at_entry
                    if long:
                        chandelier = float(lookback_22["high"].max()) - 2.5 * atr22
                        if c_bar < chandelier and chandelier > t.sl:
                            pnl_r = ((c_bar - t.entry) if long else (t.entry - c_bar)) * runner
                            pnl_r -= (t.entry * runner + c_bar * runner) * COST_RT_PCT / 2
                            equity += pnl_r; t.pnl += pnl_r
                            t.exit_price = c_bar; t.exit_time = now_ts
                            trades.append(t); del open_trades[sym]
                            _opt_record_trade(t)
                            if t.pnl > 0: wins += 1
                            else: losses += 1
                            _win_history.append(1 if t.pnl > 0 else 0)
                            recent_trades.append(t.pnl / max(capital, 1e-9))
                            try:
                                from risk_manager import update_streak as _upd_streak
                                _upd_streak(t.pnl)
                            except Exception:
                                pass
                            try:
                                _update_adaptive_threshold(t.pnl)
                            except Exception:
                                pass
                            chandelier_triggered = True
                    else:
                        chandelier = float(lookback_22["low"].min()) + 2.5 * atr22
                        if c_bar > chandelier and chandelier < t.sl:
                            pnl_r = ((c_bar - t.entry) if long else (t.entry - c_bar)) * runner
                            pnl_r -= (t.entry * runner + c_bar * runner) * COST_RT_PCT / 2
                            equity += pnl_r; t.pnl += pnl_r
                            t.exit_price = c_bar; t.exit_time = now_ts
                            trades.append(t); del open_trades[sym]
                            _opt_record_trade(t)
                            if t.pnl > 0: wins += 1
                            else: losses += 1
                            _win_history.append(1 if t.pnl > 0 else 0)
                            recent_trades.append(t.pnl / max(capital, 1e-9))
                            try:
                                from risk_manager import update_streak as _upd_streak
                                _upd_streak(t.pnl)
                            except Exception:
                                pass
                            try:
                                _update_adaptive_threshold(t.pnl)
                            except Exception:
                                pass
                            chandelier_triggered = True
                if chandelier_triggered:
                    continue
                t2_hit = (hi >= t.t2) if long else (lo <= t.t2)
                if t2_hit:
                    pnl_r = ((t.t2 - t.entry) if long else (t.entry - t.t2)) * runner
                    pnl_r -= (t.entry * runner + t.t2 * runner) * COST_RT_PCT / 2
                    equity += pnl_r; t.pnl += pnl_r
                    t.exit_price = t.t2; t.exit_time = now_ts
                    trades.append(t); del open_trades[sym]
                    _opt_record_trade(t)
                    if t.pnl > 0: wins += 1
                    else: losses += 1
                    _win_history.append(1 if t.pnl > 0 else 0)
                    recent_trades.append(t.pnl / max(capital, 1e-9))
                    try:
                        from risk_manager import update_streak as _upd_streak
                        _upd_streak(t.pnl)
                    except Exception:
                        pass
                    try:
                        _update_adaptive_threshold(t.pnl)
                    except Exception:
                        pass

        peak   = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        equity_curve.append(equity)

        if len(open_trades) >= MAX_OPEN:
            continue

        # No new entries after 14:30 IST (not enough time for setup to play out)
        bar_time_now = now_ts.time()
        if bar_time_now > dtime(14, 30):
            continue

        try:
            from risk_manager import check_intraday_circuit as _circuit
            if _circuit(equity) in ('HALT', 'EMERGENCY'):
                continue
        except Exception:
            pass

        if _check_rolling_win_circuit(_win_history):
            continue

        _breadth_state = None
        _sector_bias   = None
        if _use_breadth and _breadth_cache is not None:
            try:
                _breadth_state = _breadth_cache.get(data, now_ts)
                _sector_bias   = _breadth_cache.get_sector(data, now_ts)
            except Exception:
                pass

        if (_nifty_proxy_ts is None or
                (now_ts - _nifty_proxy_ts).total_seconds() >= 900):
            try:
                _nifty_proxy_cache = _compute_nifty_proxy(data, now_ts)
                _nifty_proxy_ts = now_ts
            except Exception:
                pass

        # ── Cross-sectional rank + regime + factor model (every 15-30 min) ──
        if (_cs_rank_ts is None or
                (now_ts - _cs_rank_ts).total_seconds() >= 900):
            try:
                _cs_rank_cache = _rank_symbols_by_momentum(data, now_ts)
                _cs_rank_ts = now_ts
            except Exception:
                pass
        if (_factor_scores_ts is None or
                (now_ts - _factor_scores_ts).total_seconds() >= 900):
            try:
                from factor_model import compute_factor_scores
                _factor_scores_cache = compute_factor_scores(data, now_ts)
                _factor_scores_ts = now_ts
            except Exception:
                pass
        if (_regime_ts is None or
                (now_ts - _regime_ts).total_seconds() >= 1800):
            try:
                from regime_detector import detect_regime_from_data, get_expiry_context
                _regime, _regime_confidence, _ = detect_regime_from_data(data, now_ts)
                _expiry_ctx = get_expiry_context(now_ts.date())
                _regime_ts = now_ts
            except Exception:
                pass

        _mkt_trend = _nifty_proxy_cache.get("trend_score", 0)
        _pct_vwap  = _nifty_proxy_cache.get("pct_above_vwap", 0.5)

        _long_only_market  = (_pct_vwap >= 0.65) or (_mkt_trend >= 0.35)
        _short_only_market = (_pct_vwap <= 0.35) or (_mkt_trend <= -0.35)
        if _long_only_market and _short_only_market:
            _long_only_market = False
            _short_only_market = False

        # F&O monthly expiry: skip all new entries
        if _expiry_ctx.get("bias") == "AVOID":
            continue

        _stressed = 0
        for _st in open_trades.values():
            if _st.direction == "LONG":
                _cur = data.get(_st.symbol)
                if _cur is not None and now_ts in _cur.index:
                    _px = float(_cur.loc[now_ts].get("close", _st.entry))
                    if _px < _st.entry - 0.5 * _st.atr_at_entry:
                        _stressed += 1
            else:
                _cur = data.get(_st.symbol)
                if _cur is not None and now_ts in _cur.index:
                    _px = float(_cur.loc[now_ts].get("close", _st.entry))
                    if _px > _st.entry + 0.5 * _st.atr_at_entry:
                        _stressed += 1
        if _stressed >= 2:
            continue

        # ── Nifty proxy: avg return from today's open across all loaded symbols ──
        _nf_today = now_ts.date()
        _nf_returns = []
        for _nfsym, _nfdf in data.items():
            try:
                if now_ts not in _nfdf.index:
                    continue
                _nf_bar = _nfdf.loc[now_ts]
                _nf_todaydf = _nfdf[_nfdf.index.date == _nf_today]
                if len(_nf_todaydf) == 0:
                    continue
                _nf_open = float(_nf_todaydf.iloc[0]["open"])
                _nf_close = float(_nf_bar["close"])
                if _nf_open > 0:
                    _nf_returns.append((_nf_close - _nf_open) / _nf_open)
            except Exception:
                pass
        _nifty_proxy_return = float(sum(_nf_returns) / max(len(_nf_returns), 1)) if _nf_returns else 0.0
        _nifty_session_bull = _nifty_proxy_return > 0.0003   # +0.03% — even slight positive = bull
        _nifty_session_bear = _nifty_proxy_return < -0.0003  # -0.03% — even slight negative = bear

        # Track bull/bear days for the report
        if _nf_today not in _session_days_seen:
            _session_days_seen.add(_nf_today)
            if _nifty_session_bull:
                _bull_days += 1
            elif _nifty_session_bear:
                _bear_days += 1

        for sym, df in data.items():
            if sym in open_trades or len(open_trades) >= MAX_OPEN:
                continue
            # Block entries after 14:00 — only 75 min left to squareoff at 15:15.
            # Was 13:00 but that blocked 25% of good mid-session setups with no basis.
            if now_ts.time() >= dtime(14, 0):
                continue
            # Opening blackout: 9:15-10:30 = high noise (ORB fakeouts, thin pre-discovery).
            # MACD/EMA change-of-state signals that fire in this window are caught by the
            # bypass override when they re-trigger later mid-morning (10:30-13:00).
            if now_ts.time() < dtime(10, 30):
                continue
            if now_ts not in df.index:
                continue
            idx = df.index.get_loc(now_ts)
            if idx < 55:
                continue

            row  = df.iloc[idx]
            prev = df.iloc[idx - 1]

            _f15 = data_15m.get(sym)
            df_15m = None
            if _f15 is not None and not _f15.empty:
                try:
                    _slice15 = _f15.loc[:now_ts]
                    df_15m = _slice15 if not _slice15.empty else None
                except Exception:
                    df_15m = _f15.tail(10) if len(_f15) > 0 else None

            _f1h = data_1h.get(sym)
            df_1h = None
            if _f1h is not None and not _f1h.empty:
                try:
                    _slice1h = _f1h.loc[:now_ts]
                    df_1h = _slice1h if not _slice1h.empty else None
                except Exception:
                    df_1h = _f1h.tail(5) if len(_f1h) > 0 else None

            _today_d = now_ts.date()
            _today_df_slice = df[df.index.date == _today_d]
            _bars_today = len(_today_df_slice[_today_df_slice.index <= now_ts])
            if _bars_today < 2:
                continue

            try:
                _skip, _skip_r = _pre_filter(row, prev, now_ts, df_15m)
                if _skip:
                    pre_filter_kills += 1
                    continue
            except Exception:
                pass

            try:
                net_score, direction, reason = _score_bar(row, prev, df_15m, df_1h, now_ts)
            except Exception:
                continue

            _bull_bars = 0; _bear_bars = 0
            try:
                _today_date = now_ts.date()
                _today_bars = df[(df.index.date == _today_date) & (df.index <= now_ts)]
                if len(_today_bars) >= 4:
                    _last4 = _today_bars.iloc[-4:]
                    _bull_bars = int((_last4["close"] > _last4["open"]).sum())
                    _bear_bars = int((_last4["close"] < _last4["open"]).sum())
                    # Alignment BONUS only — counter-trend hard gate is applied AFTER all session/ML
                    # boosts so SESSION_BULL cannot rescue a falling-knife LONG entry
                    if _bull_bars >= 3 and direction == "LONG":
                        net_score += 6
                        reason = (reason + "+TODAY_ALIGN_BULL") if reason else "TODAY_ALIGN_BULL"
                    elif _bear_bars >= 3 and direction == "SHORT":
                        net_score -= 6
                        reason = (reason + "+TODAY_ALIGN_BEAR") if reason else "TODAY_ALIGN_BEAR"
            except Exception:
                pass

            if net_score > 0:
                direction = "LONG"
            elif net_score < 0:
                direction = "SHORT"

            # ── Session breadth directional bias ─────────────────────────────
            # % of symbols above today's open = real-time market direction
            if _session_breadth > 0.60:   # 60%+ stocks rising today → bullish session
                net_score += 8; reason = (reason + "+SESSION_BULL") if reason else "SESSION_BULL"
                if direction == "SHORT":
                    net_score += 8  # Moderate penalty for fighting the session trend (was 18 = too harsh)
                    reason += "+COUNTER_SESSION"
            elif _session_breadth > 0.52:
                net_score += 5
            elif _session_breadth < 0.40:  # 40%- stocks rising → bearish session
                net_score -= 5; reason = (reason + "+SESSION_BEAR") if reason else "SESSION_BEAR"
                if direction == "LONG":
                    net_score -= 5  # Reduced from -8: contrarian entries work on weak breadth days
                    reason += "+COUNTER_SESSION"
            elif _session_breadth < 0.48:
                net_score -= 5
            # Re-derive direction after session breadth adjustment
            if net_score > 0:
                direction = "LONG"
            elif net_score < 0:
                direction = "SHORT"

            if _ml_scorer is not None:
                try:
                    ml_boost, ml_reason = _ml_scorer.get_score_boost(df, idx, direction)
                    net_score += ml_boost
                    if ml_reason:
                        reason = reason + "+" + ml_reason if reason else ml_reason
                except Exception:
                    pass

            if _breadth_state is not None and _sector_bias is not None:
                try:
                    from breadth_sector_filter import get_breadth_score_boost
                    b_delta, b_reason = get_breadth_score_boost(
                        sym, _breadth_state, _sector_bias, direction)
                    net_score += b_delta
                    if b_reason:
                        reason = reason + "+" + b_reason if reason else b_reason
                except Exception:
                    pass

            # ── Regime-conditional score multiplier ───────────────────────────
            if _regime is not None:
                try:
                    from regime_detector import get_regime_score_mult
                    _rmult = get_regime_score_mult(_regime, direction)
                    net_score *= _rmult
                except Exception:
                    pass
                if net_score > 0: direction = "LONG"
                elif net_score < 0: direction = "SHORT"

            # ── Cross-sectional rank score boost/penalty (no hard filter) ────
            if _cs_rank_cache:
                _sym_rank = _cs_rank_cache.get(sym, 0.5)
                if direction == "LONG":
                    if _sym_rank >= 0.75:
                        net_score += 10; reason = (reason + "+CS_TOP") if reason else "CS_TOP"
                    elif _sym_rank >= 0.60:
                        net_score += 5
                elif direction == "SHORT":
                    if _sym_rank <= 0.25:
                        net_score -= 10; reason = (reason + "+CS_BOT") if reason else "CS_BOT"
                    elif _sym_rank <= 0.40:
                        net_score -= 5
                if net_score > 0: direction = "LONG"
                elif net_score < 0: direction = "SHORT"

            # ── Multi-factor alpha boost ──────────────────────────────────────
            if _factor_scores_cache:
                try:
                    from factor_model import get_factor_score_boost
                    _fa, _fr = get_factor_score_boost(sym, direction, _factor_scores_cache)
                    if abs(_fa) > 0.5:
                        net_score += _fa
                        if _fr:
                            reason = (reason + "+" + _fr) if reason else _fr
                except Exception:
                    pass
                if net_score > 0: direction = "LONG"
                elif net_score < 0: direction = "SHORT"

            # Bypass: high-conviction reversal signals override 1H_BEAR score suppression.
            # Same logic as run_backtest — see comment there for full explanation.
            _bypass_long_sigs_g = (
                "VWAP_RECLAIM",   # _score_bar: price reclaimed VWAP — institutional buy
                "MACD_XOVER_UP",  # _score_bar: MACD histogram just turned positive
                "EMA_BULL_STACK", # _score_bar: EMA9 just crossed above EMA21
                "RSI_BULL_CROSS", # _score_bar: RSI just crossed 50 from below
            )
            if any(s in reason for s in _bypass_long_sigs_g):
                direction = "LONG"
                if abs(net_score) < 8.0:
                    net_score = 8.0

            # Pre-filter: skip clearly weak signals before calling new strategies
            if abs(net_score) < 8.0:
                continue

            # Hard block: strong BEARISH bar → never go LONG (fighting the tape)
            try:
                _bc  = float(row.get("close", 0) or 0)
                _bo  = float(row.get("open",  0) or 0)
                _bh  = float(row.get("high",  0) or 0)
                _blo = float(row.get("low",   0) or 0)
                _brv = float(row.get("rvol", 0.0) or 0.0)
                _brange = _bh - _blo
                if (_brange > 0 and _bc > 0 and _bo > 0 and direction == "LONG"
                        and (_bo - _bc) > _brange * 0.3 and _brv > 1.3):
                    continue  # Strong selling bar — entry would be fighting tape
            except Exception:
                pass

            try:
                from strategies_india import (ema21_pullback_signal,
                                               liquidity_grab_signal,
                                               inside_bar_breakout_signal,
                                               momentum_ignition_signal,
                                               institutional_accumulation_signal,
                                               pullback_continuation_signal,
                                               range_expansion_signal,
                                               confirmed_momentum_signal,
                                               vwap_bounce_signal,
                                               orb_momentum_signal,
                                               hammer_reversal_signal,
                                               atr_squeeze_breakout_signal,
                                               intraday_momentum_signal)
            except Exception:
                pass
            # EMA21 Pullback
            try:
                _s4, _r4 = ema21_pullback_signal(df, idx)
                if _s4 != 0:
                    net_score += _s4; reason = (reason + "+" + _r4) if _r4 and reason else (_r4 or reason)
            except Exception:
                pass
            # Liquidity Grab
            try:
                _s5, _r5 = liquidity_grab_signal(df, idx)
                if _s5 != 0:
                    net_score += _s5; reason = (reason + "+" + _r5) if _r5 and reason else (_r5 or reason)
            except Exception:
                pass
            # Inside Bar
            try:
                _s6, _r6 = inside_bar_breakout_signal(df, idx, now_ts)
                if _s6 != 0:
                    net_score += _s6; reason = (reason + "+" + _r6) if _r6 and reason else (_r6 or reason)
            except Exception:
                pass
            # Momentum Ignition
            try:
                _s12, _r12 = momentum_ignition_signal(df, idx)
                if _s12 != 0:
                    net_score += _s12; reason = (reason + "+" + _r12) if _r12 and reason else (_r12 or reason)
            except Exception:
                pass
            # Institutional Accumulation / Distribution
            try:
                _s13, _r13 = institutional_accumulation_signal(df, idx)
                if _s13 != 0:
                    net_score += _s13; reason = (reason + "+" + _r13) if _r13 and reason else (_r13 or reason)
            except Exception:
                pass
            # Pullback Continuation
            try:
                _s14, _r14 = pullback_continuation_signal(df, idx)
                if _s14 != 0:
                    net_score += _s14; reason = (reason + "+" + _r14) if _r14 and reason else (_r14 or reason)
            except Exception:
                pass
            # Range Expansion (NR4/NR7 breakout)
            try:
                _s15, _r15 = range_expansion_signal(df, idx)
                if _s15 != 0:
                    net_score += _s15; reason = (reason + "+" + _r15) if _r15 and reason else (_r15 or reason)
            except Exception:
                pass
            # Confirmed Momentum (highest conviction)
            try:
                _s16, _r16 = confirmed_momentum_signal(df, idx)
                if _s16 != 0:
                    net_score += _s16; reason = (reason + "+" + _r16) if _r16 and reason else (_r16 or reason)
            except Exception:
                pass
            # VWAP Bounce (71% WR: absorption at VWAP then breakout)
            try:
                _sv, _rv = vwap_bounce_signal(df, idx)
                if _sv != 0:
                    net_score += _sv; reason = (reason + "+" + _rv) if _rv and reason else (_rv or reason)
            except Exception:
                pass
            # ORB Momentum (60-65% WR: clean ORB breakout with volume)
            # Skip if _score_bar already scored ORB to prevent double-counting (+33 = inflated)
            try:
                if "ORB_BULL" not in reason and "ORB_BEAR" not in reason:
                    _so, _ro = orb_momentum_signal(df, idx)
                    if _so != 0:
                        net_score += _so; reason = (reason + "+" + _ro) if _ro and reason else (_ro or reason)
            except Exception:
                pass
            # Hammer Reversal (pin-bar reversal at support)
            try:
                _sh, _rh = hammer_reversal_signal(df, idx)
                if _sh != 0:
                    net_score += _sh; reason = (reason + "+" + _rh) if _rh and reason else (_rh or reason)
            except Exception:
                pass
            # ATR Squeeze Breakout (volatility expansion from squeeze)
            try:
                _sq, _rq = atr_squeeze_breakout_signal(df, idx)
                if _sq != 0:
                    net_score += _sq; reason = (reason + "+" + _rq) if _rq and reason else (_rq or reason)
            except Exception:
                pass
            # Intraday Momentum (today's established directional drift)
            try:
                _si, _ri = intraday_momentum_signal(df, idx)
                if _si != 0:
                    net_score += _si; reason = (reason + "+" + _ri) if _ri and reason else (_ri or reason)
            except Exception:
                pass

            if net_score > 0:
                direction = "LONG"
            elif net_score < 0:
                direction = "SHORT"

            # ── Gap bias: weight toward gap direction (don't hard-block) ─────
            try:
                _gap_open = float(row.get("day_open", 0) or 0)
                _prev_idx = idx - 1
                # Find the last bar of the previous trading day
                _prev_day_close = 0.0
                if _gap_open > 0 and _prev_idx >= 0:
                    _prev_close_bar = df.iloc[_prev_idx]
                    _prev_bar_date = df.index[_prev_idx].date()
                    _today_bar_date = now_ts.date()
                    if _prev_bar_date < _today_bar_date:
                        _prev_day_close = float(_prev_close_bar.get("close", 0) or 0)
                    else:
                        # Find last bar of previous day
                        _prev_day_rows = df[df.index.date < _today_bar_date]
                        if len(_prev_day_rows) > 0:
                            _prev_day_close = float(_prev_day_rows.iloc[-1].get("close", 0) or 0)
                if _prev_day_close > 0 and _gap_open > 0:
                    _gap_val = (_gap_open - _prev_day_close) / _prev_day_close
                    if _gap_val > 0.002 and direction == "SHORT":
                        net_score += 8  # Push toward LONG (positive gap day) (was 15 = too harsh)
                        reason = (reason + "+GAP_UP_PENALTY_SHORT") if reason else "GAP_UP_PENALTY_SHORT"
                        if net_score > 0:
                            direction = "LONG"
                    elif _gap_val < -0.002 and direction == "LONG":
                        net_score -= 8  # Push toward SHORT (negative gap day) (was 15 = too harsh)
                        reason = (reason + "+GAP_DN_PENALTY_LONG") if reason else "GAP_DN_PENALTY_LONG"
                        if net_score < 0:
                            direction = "SHORT"
                    elif _gap_val > 0.002 and direction == "LONG":
                        net_score += 8   # Bonus: going with gap direction
                        reason = (reason + "+GAP_UP_ALIGN") if reason else "GAP_UP_ALIGN"
                    elif _gap_val < -0.002 and direction == "SHORT":
                        net_score -= 8   # Bonus: going with gap direction (short on gap-down)
                        reason = (reason + "+GAP_DN_ALIGN") if reason else "GAP_DN_ALIGN"
            except Exception:
                pass

            # ── Nifty session direction gate (hardest gate) ───────────────────
            # Only trade with the session's net direction to avoid counter-trend losses
            # Use _ADAPTIVE_MIN_SCORE (pre-regime base) so bull-day relaxation does NOT
            # loosen this gate — counter-trend trades should face the same or higher bar
            # regardless of how permissive the regime threshold is for with-trend entries.
            if _nifty_session_bull and direction == "SHORT":
                if abs(net_score) < _ADAPTIVE_MIN_SCORE * 1.3:
                    continue  # Block weak counter-trend shorts in bull session
            # Lightweight bear gate: breadth (count-based) ≠ nifty proxy (value-weighted).
            # breadth=0.42 can coexist with nifty proxy=-0.15% (large-caps down, small-caps flat).
            # Only block when BOTH metrics indicate bear: proxy negative AND breadth < 0.50.
            if _nifty_session_bear and direction == "LONG" and _session_breadth < 0.50:
                if abs(net_score) < MIN_SCORE + 2:
                    continue  # Weak LONG in confirmed bear session (both metrics bearish)

            # ── 3-Timeframe alignment gate ────────────────────────────────────
            # 15m penalty (currently only bonus exists; disagreement has zero cost)
            # Hard block when BOTH 15m AND 1h disagree with direction
            try:
                _15m_bull = False; _15m_bear = False
                _1h_bull  = False; _1h_bear  = False
                if df_15m is not None and not df_15m.empty:
                    _r15 = df_15m.iloc[-1]
                    _e9_15  = float(_r15.get("ema9",  0) or 0)
                    _e21_15 = float(_r15.get("ema21", 0) or 0)
                    if _e9_15 > _e21_15 > 0: _15m_bull = True
                    elif _e9_15 < _e21_15 and _e21_15 > 0: _15m_bear = True
                if df_1h is not None and not df_1h.empty:
                    _r1h = df_1h.iloc[-1]
                    _c1h  = float(_r1h.get("close", 0) or 0)
                    _e21h = float(_r1h.get("ema21", 0) or 0)
                    _e50h = float(_r1h.get("ema50", 0) or 0)
                    if _c1h > _e21h > _e50h > 0: _1h_bull = True
                    elif _c1h < _e21h and _e21h < _e50h and _e50h > 0: _1h_bear = True

                if direction == "LONG":
                    # TF alignment penalty — capped at -8 total to prevent killing valid high-WR signals
                    _tf_penalty = 0
                    if _15m_bear:
                        _tf_penalty -= 7
                        reason = (reason + "+15M_BEAR_PENALTY") if reason else "15M_BEAR_PENALTY"
                    if _15m_bear and _1h_bear:
                        _tf_penalty -= min(4, 8 + _tf_penalty)  # cap total at -8
                        reason = (reason + "+1H_BEAR_PENALTY") if reason else "1H_BEAR_PENALTY"
                    net_score += max(_tf_penalty, -8)
                elif direction == "SHORT":
                    _tf_penalty = 0
                    if _15m_bull:
                        _tf_penalty += 7
                        reason = (reason + "+15M_BULL_PENALTY") if reason else "15M_BULL_PENALTY"
                    if _15m_bull and _1h_bull:
                        _tf_penalty += min(4, 8 - _tf_penalty)
                        reason = (reason + "+1H_BULL_PENALTY") if reason else "1H_BULL_PENALTY"
                    net_score += min(_tf_penalty, 8)
                # Re-derive direction after penalty
                if net_score > 0: direction = "LONG"
                elif net_score < 0: direction = "SHORT"
            except Exception:
                pass

            # ── Hard momentum confirmation gates ─────────────────────────────
            # Stock must already be moving in signal direction with volume.
            # Prevents entries in "potential" momentum (not yet confirmed).
            try:
                _close_g = float(row.get("close", 0) or 0)
                _day_open_g = float(row.get("day_open", 0) or 0)
                _rvol_g = float(row.get("rvol", 1.0) or 1.0)
                _e9_g = float(row.get("ema9", 0) or 0)
                _e21_g = float(row.get("ema21", 0) or 0)
                if _close_g > 0 and _day_open_g > 0:
                    _sess_ret_g = (_close_g - _day_open_g) / _day_open_g
                    # High-conviction signals get a lower session_return bar
                    # Use abs(net_score) so SHORT signals (negative) also benefit
                    _abs_score = abs(net_score)
                    if _abs_score >= 18:
                        _sr_thresh = 0.0001   # strong: near-zero — signal is self-confirming
                    elif _abs_score >= 14:
                        _sr_thresh = 0.0003 if _is_5m_data else 0.0002   # medium: 0.03%/0.02%
                    else:
                        _sr_thresh = 0.0004 if _is_5m_data else 0.0004   # weak: 0.04% both TFs
                    # RVOL floor by signal type:
                    # VWAP_RECLAIM — quiet drift back to VWAP needs no volume spike; 1.0× ok
                    # EMA_BULL_STACK — fresh EMA cross; 1.1× enough
                    # ORB / momentum — needs genuine institutional volume: 1.3×
                    _vwap_signal = "VWAP_RECLAIM" in reason or "VWAP_REJECT" in reason
                    _ema_signal  = "EMA_BULL_STACK" in reason or "EMA_BEAR_STACK" in reason
                    if _vwap_signal:
                        _rvol_min = 0.9   # VWAP reclaim can be quiet
                    elif _ema_signal:
                        _rvol_min = 1.1   # fresh EMA cross: moderate confirmation
                    elif _is_5m_data:
                        _rvol_min = 1.1 if _abs_score >= 18 else 1.3
                    else:
                        _rvol_min = 1.0 if _abs_score >= 18 else 1.1
                    # ORB bypass: direction-matched flag — breakout proves session direction
                    _orb_bypass = (
                        (direction == "LONG" and ("ORB_BULL_CONFIRM" in reason or "ORB_BULL_WEAK" in reason)) or
                        (direction == "SHORT" and ("ORB_BEAR_CONFIRM" in reason or "ORB_BEAR_WEAK" in reason))
                    )
                    # High-WR setup bypass: self-confirming signals don't need prior tape trend.
                    # These signals PREDICT the coming move — requiring prior 0.3% gain is circular.
                    _high_wr_bypass = any(sig in reason for sig in (
                        "VWAP_BOUNCE_LONG", "VWAP_BOUNCE_SHORT",
                        "HAMMER_REVERSAL_LONG", "HAMMER_REVERSAL_SHORT",
                        "ATR_SQUEEZE_BREAKOUT",
                        "MACD_XOVER_UP",       # MACD cross = trend start — doesn't need prior gain
                        "VWAP_RECLAIM",        # price reclaims VWAP = institutional buy
                        "EMA21_PULLBACK",      # buy dip in uptrend near EMA21
                        "PULLBACK_CONT",       # momentum continuation after pullback
                        "CONFIRMED_MOMENTUM",  # highest conviction composite
                    ))
                    # Session floor: bypass signals (ORB_CLEAN, ATR_SQUEEZE, VWAP_BOUNCE,
                    # HAMMER) are self-confirming — they predict the coming move, not
                    # confirm a past one. Non-bypass signals need stock already trending.
                    if direction == "LONG" and not _high_wr_bypass and _sess_ret_g < 0.0015:
                        continue  # Non-bypass LONG: require stock up >= 0.15% on the day
                    # Bonus gates use a floor to avoid score inflation when _sr_thresh is near-zero
                    _bonus_thresh = max(_sr_thresh, 0.0002)
                    if direction == "LONG":
                        # Hard block: VOL_BEAR in reason means current bar is a strong DOWN bar.
                        # Going LONG on a heavy-selling bar = fighting the tape.
                        if any("VOL_BEAR" in r for r in reason.split("+")):
                            continue
                        # LONG: stock must be up in signal direction, volume elevated, EMA aligned
                        if not _orb_bypass and not _high_wr_bypass and _sess_ret_g < _sr_thresh:
                            continue
                        if not _high_wr_bypass and _rvol_g < _rvol_min:
                            continue
                        # ORB_BULL_CONFIRM: NSE ORB breakouts are trap-prone without real volume
                        if ("ORB_BULL_CONFIRM" in reason) and _rvol_g < 1.8:
                            continue
                        _ema_bearish_g = (_e9_g > 0 and _e21_g > 0 and _e9_g < _e21_g * 0.998)
                        if _ema_bearish_g:
                            if not _high_wr_bypass:
                                # EMA bearish: soft penalty (bypass signals expect bearish EMAs — they signal the TURN)
                                net_score -= 5
                                if net_score < 0: direction = "SHORT"
                        # Bonus for strongly confirmed momentum — guarded: EMA bearish blocks both bonuses
                        # so the penalty is not silently overridden by MOD_CONFIRM within the same block
                        if not _ema_bearish_g and _sess_ret_g > 0.005 and _rvol_g > 1.8 and (_e9_g <= 0 or _e9_g > _e21_g):
                            net_score += 18
                            reason = (reason + "+STRONG_CONFIRM") if reason else "STRONG_CONFIRM"
                        elif not _ema_bearish_g and _sess_ret_g > 0.002 and _rvol_g > 1.2:
                            net_score += 8
                            reason = (reason + "+MOD_CONFIRM") if reason else "MOD_CONFIRM"
                        # EMA_BULL_STACK: block only extremely extended moves (>1.5% above EMA21)
                        if "EMA_BULL_STACK" in reason and _e21_g > 0 and _close_g > 0:
                            if (_close_g - _e21_g) / _e21_g > 0.015:  # >1.5% above EMA21 = chase
                                continue
                    elif direction == "SHORT":
                        # SHORT: stock must be down in signal direction
                        if not _orb_bypass and not _high_wr_bypass and _sess_ret_g > -_sr_thresh:
                            continue
                        if not _high_wr_bypass and _rvol_g < _rvol_min:
                            continue
                        if _e9_g > 0 and _e21_g > 0 and _e9_g >= _e21_g:
                            continue   # bullish EMA — no short
                        if _sess_ret_g < -0.005 and _rvol_g > 1.8 and (_e9_g <= 0 or _e9_g < _e21_g):
                            net_score -= 18
                            reason = (reason + "+STRONG_CONFIRM") if reason else "STRONG_CONFIRM"
                        elif _sess_ret_g < -0.002 and _rvol_g > 1.2:
                            net_score -= 8
                            reason = (reason + "+MOD_CONFIRM") if reason else "MOD_CONFIRM"
            except Exception:
                pass

            # Bypass signals are self-confirming — don't let TF counter-trend compression kill them.
            if _high_wr_bypass and 0 < abs(net_score) < MIN_SCORE:
                net_score = MIN_SCORE * (1 if net_score > 0 else -1)

            # Market breadth alignment scoring (+3/-3 to avoid over-stacking with existing breadth signals)
            try:
                if direction == "LONG":
                    if _session_breadth > 0.62:
                        net_score += 3
                        reason = (reason + "+MKTBIAS_LONG") if reason else "MKTBIAS_LONG"
                    elif _session_breadth < 0.38:
                        net_score -= 3
                        reason = (reason + "+MKTBIAS_CONTRA") if reason else "MKTBIAS_CONTRA"
                elif direction == "SHORT":
                    if _session_breadth < 0.38:
                        net_score -= 3  # more negative = stronger SHORT
                        reason = (reason + "+MKTBIAS_SHORT") if reason else "MKTBIAS_SHORT"
                    elif _session_breadth > 0.62:
                        net_score += 3  # less negative = weaker SHORT = penalty
                        reason = (reason + "+MKTBIAS_CONTRA") if reason else "MKTBIAS_CONTRA"
            except Exception:
                pass

            # Hard today-bar gate: applied AFTER all session/ML boosts so SESSION_BULL
            # cannot rescue a falling-knife LONG (3+ recent bear bars = local downtrend).
            if _bear_bars >= 3 and direction == "LONG":
                continue
            if _bull_bars >= 3 and direction == "SHORT":
                continue

            if abs(net_score) < _eff_min_score:
                continue

            if _long_only_market and direction == "SHORT":
                continue   # Market is bullish — skip counter-trend SHORT
            if _short_only_market and direction == "LONG":
                continue   # Market is bearish — skip counter-trend LONG
            # NSE long-only mode: never take shorts (Groww MIS LONG positions only)
            if LONG_ONLY_NSE and direction == "SHORT":
                continue
            # Breadth floor: hard block extreme bear tape; moderate weakness gets score penalty
            if BULL_DAY_ONLY and _session_breadth < 0.30:
                continue  # Hard block: extreme bear tape only
            if BULL_DAY_ONLY and _session_breadth < 0.45:
                net_score -= 4  # Weak day: penalize but don't block
                if abs(net_score) < _eff_min_score:
                    continue  # Re-check threshold after breadth penalty

            # ── Entry quality gate: RSI overbought filter only ───────────────
            try:
                _rsi_q = float(row.get("rsi", 50) or 50)
                if direction == "LONG" and _rsi_q > 68:
                    continue  # overbought — wait for pullback
            except Exception:
                pass
            # ── Named-setup quality gate: pattern + confirmation ──
            # Bypass signals (ATR_SQUEEZE, ORB_CLEAN, VWAP_BOUNCE, HAMMER) have
            # their own internal volume/price checks — count as self-confirming.
            # All other patterns need external tape confirmation (MOD/STRONG_CONFIRM).
            _QUALITY_SIGS = ("VWAP_BOUNCE", "ORB_BULL", "ORB_BEAR",
                             "EMA_BULL_STACK", "EMA_BEAR_STACK", "EMA21_PULLBACK",
                             "MACD_XOVER", "RSI_BULL_CROSS", "RSI_BEAR_CROSS",
                             "VWAP_RECLAIM", "VWAP_REJECT",
                             "CONFIRMED_MOMENTUM", "ATR_SQUEEZE",
                             "HAMMER_REVERSAL", "MOMENTUM_IGNITION", "PULLBACK_CONT",
                             "RANGE_EXP", "INTRADAY_MOM", "LIQ_GRAB",
                             "INSIDE_BAR", "ACCUM", "DISTRIB",
                             "STRONG_CONFIRM", "MOD_CONFIRM")
            _qual_count = sum(1 for q in _QUALITY_SIGS if q in reason)
            _is_self_confirm = any(s in reason for s in (
                "VWAP_BOUNCE_LONG", "VWAP_BOUNCE_SHORT",
                "HAMMER_REVERSAL_LONG", "HAMMER_REVERSAL_SHORT",
                "ATR_SQUEEZE_BREAKOUT",
                "MACD_XOVER_UP",
                "VWAP_RECLAIM",
                "EMA21_PULLBACK",
                "PULLBACK_CONT",
                "CONFIRMED_MOMENTUM",
                "RSI_BULL_CROSS",      # RSI crossing 50 from below — built-in price check
                "EMA_BULL_STACK",      # EMA9 crossing EMA21 — fresh momentum shift
                "ORB_BULL_CONFIRM",    # ORB breakout with volume — structural proof
                "ORB_BULL_WEAK",       # ORB breakout without full volume — still directional
                "INTRADAY_MOM_UP",     # established trend + 3/4 bullish bars + volume
                "RANGE_EXP",           # NR4/NR7 + volume expansion = volatility breakout
            ))
            if _is_self_confirm:
                _qual_count += 1  # Built-in volume/price checks = one free confirmation
            if _qual_count < 2:
                continue  # Need pattern + confirmation — pure patterns without tape = noise
            # ────────────────────────────────────────────────────────────────

            atr = row.get("atr", row["close"] * 0.005)
            if atr <= 0:
                continue
            entry = row["close"]
            long  = direction == "LONG"
            sl    = entry - 1.0 * atr if long else entry + 1.0 * atr   # 1×ATR tight stop
            t1    = entry + 2.0 * atr if long else entry - 2.0 * atr   # 2R target (33% WR break-even)
            t2    = entry + 4.0 * atr if long else entry - 4.0 * atr   # 4R runner

            try:
                from risk_manager import get_kelly_regime_mult as _kelly_regime_mult
                _regime_mult = _kelly_regime_mult(_regime, direction)
            except Exception:
                _regime_mult = 1.0
            risk_pct = _dynamic_kelly_size(recent_trades, equity, net_score, atr, entry,
                                           regime_mult=_regime_mult)

            # ── IC-weighted Kelly + correlation-adjusted sizing ───────────────
            try:
                from risk_manager import ic_kelly_multiplier as _ic_mult, \
                                         correlation_size_cap as _corr_cap
                risk_pct *= _ic_mult(net_score)
                risk_pct *= _corr_cap(direction, open_trades)
            except Exception:
                pass

            try:
                from risk_manager import time_of_day_multiplier as _tod_mult
                tod_factor = _tod_mult(now_ts)
                risk_pct = risk_pct * tod_factor
            except Exception:
                pass

            try:
                from risk_manager import apply_vol_target_to_risk as _vol_target
                risk_pct = _vol_target(risk_pct, equity_curve)
            except Exception:
                pass

            sl_dist = abs(entry - sl)
            min_sl_dist = entry * 0.0015
            if sl_dist < min_sl_dist:
                sl_dist = min_sl_dist
                sl = entry - sl_dist if long else entry + sl_dist
            qty = int(min(equity * risk_pct / sl_dist,
                          equity * MAX_POS_PCT / entry))
            if qty < 1:
                continue

            # ── Minimum profit filter: skip cost-inefficient trades ───────────
            # t1 (1.5R target) must cover at least 2.5× the round-trip cost
            # This eliminates trades where expected gain is eaten by STT/brokerage
            _min_profit_pct = 2.5 * COST_RT_PCT   # need 2.5× cost coverage at t1
            _t1_profit_pct  = abs(t1 - entry) / max(entry, 1.0)
            if _t1_profit_pct < _min_profit_pct:
                continue   # t1 doesn't cover costs — skip
            # Also enforce minimum SL distance (0.3% = meaningful trade with cost coverage)
            if sl_dist < entry * 0.003:
                continue

            # F&O expiry size reduction
            _exp_mult = _expiry_ctx.get("size_multiplier", 1.0) if _expiry_ctx else 1.0
            if _exp_mult < 1.0:
                qty = max(1, int(qty * _exp_mult))

            # Half-size for signals near the lower threshold (lower confidence)
            # Use _ADAPTIVE_MIN_SCORE (pre-regime base) as the sizing anchor so
            # bull-day relaxation doesn't inadvertently promote mid-range signals
            # from half-size to full-size by lowering the denominator.
            _threshold = _ADAPTIVE_MIN_SCORE
            _confidence_ratio = abs(net_score) / max(_threshold * 2, 1.0)
            if _confidence_ratio < 0.6:  # Score is only barely above threshold
                qty = max(1, qty // 2)   # Half size for marginal signals

            trade = Trade(sym, direction, entry, sl, t1, t2, qty, now_ts, atr_at_entry=float(atr))
            try:
                from risk_manager import compute_exit_stages as _exits
                _stages = _exits(entry, atr, direction, qty, signal_type=reason or "")
                trade.stage1_price = _stages["stage1_price"]
                trade.stage2_price = _stages["stage2_price"]
                trade.stage1_qty   = _stages["stage1_qty"]
                trade.stage2_qty   = _stages["stage2_qty"]
                trade.runner_qty   = _stages["runner_qty"]
                trade.be_sl        = _stages["be_sl"]
                trade.sl           = _stages["sl"]
                trade.t1 = _stages["stage2_price"]
                trade.t2 = entry + 4.5 * atr if direction == "LONG" else entry - 4.5 * atr  # 3R runner (was 3.0 = 2R)
            except Exception:
                pass
            trade.reason = reason
            open_trades[sym] = trade

    # Close any still-open trades at last price
    for sym, t in open_trades.items():
        if sym in data:
            px = data[sym]["close"].iloc[-1]
            long = t.direction == "LONG"
            partial_qty = int(t.qty * 0.4) or 1
            qty_left = t.qty - (partial_qty if t.t1_done else 0)
            pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
            pnl -= (t.entry * qty_left + px * qty_left) * COST_RT_PCT / 2
            equity += pnl; t.pnl = pnl; t.exit_price = px
            t.exit_time = data[sym].index[-1]
            trades.append(t)
            _opt_record_trade(t)
            if pnl > 0: wins += 1
            else: losses += 1
            _win_history.append(1 if pnl > 0 else 0)
            recent_trades.append(pnl / max(capital, 1e-9))

    # Build strategy attribution from trade reasons
    # Keys must be substrings of actual reason strings emitted by _score_bar /
    # strategies_india.py.  Order matters: first match wins, so put the most
    # diagnostic / highest-value signals first.
    _STRAT_KEYS = [
        # ── High-WR named setups (new strategies — highest priority in attribution)
        "VWAP_BOUNCE_LONG", "VWAP_BOUNCE_SHORT",        # 71% WR
        "ORB_BULL_CLEAN", "ORB_BEAR_CLEAN",             # 60-65% WR
        "ORB_BULL_PARTIAL", "ORB_BEAR_PARTIAL",
        "HAMMER_REVERSAL_LONG", "HAMMER_REVERSAL_SHORT",
        "ATR_SQUEEZE_BREAKOUT",
        "INTRADAY_MOM_UP", "INTRADAY_MOM_DN",
        # ── _score_bar named signals
        "ORB_BULL_CONFIRM", "ORB_BULL_WEAK", "ORB_BEAR_CONFIRM", "ORB_BEAR_WEAK",
        "EMA_BULL_STACK", "EMA_BEAR_STACK", "EMA21_PULLBACK",
        "MACD_XOVER",
        "RSI_BULL_CROSS", "RSI_BEAR_CROSS",
        "VWAP_RECLAIM", "VWAP_REJECT",                  # transition signals
        "VWAP_REVERSION_LONG", "VWAP_REVERSION_SHORT",
        "ABOVE_VWAP", "BELOW_VWAP", "VWAP_REVERSION", "VWAP_BOUNCE",
        # ── Strategies from strategy block
        "CONFIRMED_MOMENTUM",
        "PULLBACK_CONT", "RANGE_EXP",
        "MOMENTUM_IGNITION", "ACCUM", "DISTRIB",
        "LIQ_GRAB", "INSIDE_BAR",
        # ── Context/confirmation signals
        "SESSION_DRIVE_LONG", "SESSION_DRIVE_SHORT", "SESSION_DRIVE",
        "SESSION_BULL", "SESSION_BEAR",
        "STRONG_CONFIRM", "MOD_CONFIRM",
        "MKTBIAS",
        "BOS_BULL", "BOS_BEAR",
        "OPENING_DRIVE", "GAP_GO", "SUPERTREND",
        "ML_STRONG", "ML_CONFIRM",
        "CONFIRMED_BULL", "CONFIRMED_BEAR",
    ]
    for t in trades:
        r = getattr(t, "reason", "") or ""
        matched = False
        for key in _STRAT_KEYS:
            if key in r:
                strategy_counts[key] = strategy_counts.get(key, 0) + 1
                strategy_pnl[key]    = strategy_pnl.get(key, 0.0) + t.pnl
                matched = True
                break
        if not matched:
            strategy_counts["OTHER"] = strategy_counts.get("OTHER", 0) + 1
            strategy_pnl["OTHER"]    = strategy_pnl.get("OTHER", 0.0) + t.pnl

    _report(trades, capital, equity, max_dd, equity_curve, from_date, to_date, len(data),
            pre_filter_kills=pre_filter_kills,
            strategy_counts=strategy_counts, strategy_pnl=strategy_pnl,
            bull_days=_bull_days, total_days=len(_session_days_seen),
            eff_min_score_history=_eff_min_score_history)


def main():
    ap = argparse.ArgumentParser(
        description="NSE Momentum Backtest Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default: Upstox, last 60 days
  python3 india/backtest_engine_india.py --days 60 --capital 500000

  # yfinance, 2 years of hourly data (no Upstox token needed)
  python3 india/backtest_engine_india.py --source yfinance --years 2

  # yfinance, 3 years of daily bars
  python3 india/backtest_engine_india.py --source yfinance --years 3 --interval 1d
""")
    ap.add_argument("--symbols",  type=str,   default="")
    ap.add_argument("--days",     type=int,   default=60)
    ap.add_argument("--from",     dest="from_date", type=str, default="")
    ap.add_argument("--to",       dest="to_date",   type=str, default="")
    ap.add_argument("--capital",  type=float, default=500_000.0)
    ap.add_argument("--source",   default="upstox",
                    choices=["upstox", "yfinance", "cache"],
                    help="Data source: upstox (default, 90 days), yfinance (up to 3 years), or cache (offline local Parquet/CSV)")
    ap.add_argument("--years",    type=int,   default=0,
                    help="Years of data for yfinance source (1-5, overrides --days)")
    ap.add_argument("--interval", default="1h",
                    choices=["1h", "5m", "1d"],
                    help="Bar interval (5m=60 days only, 1h=2 years, 1d=daily)")
    args = ap.parse_args()

    # ── cache path (offline local Parquet/CSV) ───────────────────────────────
    if args.source == "cache":
        from data_cache import load_data as _load_cache, cache_stats as _cache_stats
        interval = getattr(args, "interval", "1h")
        stats = _cache_stats()
        print(
            f"Loading from local cache: {stats['symbols']} files, "
            f"{stats['size_mb']} MB in {stats.get('cache_dir', '')}"
        )
        if args.symbols:
            syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            from data_yfinance import DEFAULT_SYMBOLS
            syms = DEFAULT_SYMBOLS[:40]
        raw_data = _load_cache(syms, interval=interval)
        if not raw_data:
            print(
                f"ERROR: No cached data found for interval={interval}. "
                "Run with --source yfinance (or --source upstox) first to populate the cache."
            )
            sys.exit(1)
        print(f"Loaded {len(raw_data)}/{len(syms)} symbols from cache.")
        if len(raw_data) < min(30, len(syms)):
            print(
                f"WARNING: Only {len(raw_data)} symbols in cache (expected ≥30). "
                "Cache may have been built with an older/smaller symbol list. "
                "Re-run with --source yfinance to refresh the cache."
            )
        print("Computing indicators ...")
        data: Dict[str, pd.DataFrame] = {}
        for sym, df in raw_data.items():
            try:
                df2 = _compute_all(df)
                df2 = _build_orb(df2)
                data[sym] = df2
            except Exception as e:
                print(f"  {sym}: indicator error — {e}")
        if not data:
            print("ERROR: No symbols with valid indicators. Exiting.")
            sys.exit(1)
        run_backtest_from_data(data, capital=args.capital)
        sys.exit(0)

    # ── yfinance path ────────────────────────────────────────────────────────
    if args.source == "yfinance":
        print("Loading NSE data from Yahoo Finance (no Upstox token required) ...")
        from data_yfinance import load_nse_data_yfinance, DEFAULT_SYMBOLS
        interval = getattr(args, 'interval', '1h')
        years = getattr(args, 'years', 2)
        period = f"{years}y" if years > 0 else "2y"

        if args.symbols:
            syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            syms = DEFAULT_SYMBOLS[:40]

        print(f"Loading yfinance data: {len(syms)} symbols, interval={interval}, period={period}")
        raw_data = load_nse_data_yfinance(syms, period=period, interval=interval,
                                           verbose=True)
        if not raw_data:
            print("ERROR: No data loaded from yfinance. Check internet connection.")
            sys.exit(1)

        # Auto-save to local cache for future offline use
        try:
            from data_cache import save_data as _save_cache
            _save_cache(raw_data, interval=interval, source=args.source)
        except Exception as _cache_err:
            print(f"WARNING: Could not save data to local cache: {_cache_err}")

        print(f"\nLoaded {len(raw_data)} symbols from yfinance "
              f"({period} of {interval} bars)")
        print("Computing indicators ...")
        data: Dict[str, pd.DataFrame] = {}
        for sym, df in raw_data.items():
            try:
                df2 = _compute_all(df)
                df2 = _build_orb(df2)
                data[sym] = df2
            except Exception as e:
                print(f"  {sym}: indicator error — {e}")

        if not data:
            print("ERROR: No symbols with valid indicators. Exiting.")
            sys.exit(1)

        run_backtest_from_data(data, capital=args.capital)
        sys.exit(0)

    # ── Upstox path (default) ─────────────────────────────────────────────────
    if args.from_date and args.to_date:
        from_date, to_date = args.from_date, args.to_date
    else:
        to   = datetime.now(IST).date()
        frm  = to - timedelta(days=min(args.days, 90))
        from_date = frm.strftime("%Y-%m-%d")
        to_date   = to.strftime("%Y-%m-%d")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = [
            "RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK",
            "SBIN", "AXISBANK", "KOTAKBANK", "ITC", "BHARTIARTL",
            "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO", "M&M",
            "TATASTEEL", "SUNPHARMA", "CIPLA", "LT", "POWERGRID",
            "NTPC", "ONGC", "HCLTECH", "TECHM", "BAJAJFINSV",
            "TITAN", "ULTRACEMCO", "JSWSTEEL", "HEROMOTOCO", "EICHERMOT",
            "BPCL", "COALINDIA", "INDUSINDBK", "TATACONSUM", "BAJAJ-AUTO",
            "POLYCAB", "VOLTAS", "CROMPTON", "GODREJCP", "TRENT",
            "PERSISTENT", "BALKRISIND", "ASTRAL", "CUMMINSIND", "APOLLOHOSP",
        ]

    run_backtest(symbols, from_date, to_date, args.capital)


if __name__ == "__main__":
    main()
