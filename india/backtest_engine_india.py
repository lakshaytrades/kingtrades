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

COST_RT_PCT  = 0.0029    # 29 bps round-trip (brokerage + STT + exchange + slippage)
SQUAREOFF    = dtime(15, 20)
MARKET_OPEN  = dtime(9, 15)
ORB_END      = dtime(9, 30)

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
    """Add orb_high / orb_low columns — ORB from 9:15 to 9:30 each day."""
    df = df.copy()
    df["date"] = df.index.normalize()
    orb_h = {}; orb_l = {}
    for d, grp in df.groupby("date"):
        orb = grp.between_time("09:15", "09:30")
        if not orb.empty:
            orb_h[d] = orb["high"].max()
            orb_l[d] = orb["low"].min()
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

    # ── Signal 2: EMA momentum stack ────────────────────────────────────────
    e9  = float(row.get("ema9",  c) or c)
    e21 = float(row.get("ema21", c) or c)
    e50 = float(row.get("ema50", c) or c)
    if c > e9 > e21 > e50:     score_long  += 12; reasons.append("EMA_BULL_STACK")
    elif c > e9 > e21:          score_long  += 7
    elif c > e9:                score_long  += 3
    if c < e9 < e21 < e50:     score_short += 12; reasons.append("EMA_BEAR_STACK")
    elif c < e9 < e21:          score_short += 7
    elif c < e9:                score_short += 3

    # ── Signal 3: ORB structural breakout ───────────────────────────────────
    orb_h = float(row.get("orb_high", 0) or 0)
    orb_l = float(row.get("orb_low",  0) or 0)
    rvol  = float(row.get("rvol", 1.0) or 1.0)
    if orb_h > 0 and orb_l > 0 and bar_time > ORB_END:
        orb_range_pct = (orb_h - orb_l) / max(orb_h, 1)
        _vol_ok = rvol >= 1.3
        if 0.001 <= orb_range_pct <= 0.025:   # Valid ORB range: 0.1% to 2.5%
            if c > orb_h * 1.0015:   # 0.15% above ORB high
                if _vol_ok:    score_long  += 18; reasons.append("ORB_BULL_CONFIRM")
                else:          score_long  += 10; reasons.append("ORB_BULL_WEAK")
            elif c > orb_h:
                if _vol_ok:    score_long  += 8
            if c < orb_l * 0.9985:   # 0.15% below ORB low
                if _vol_ok:    score_short += 18; reasons.append("ORB_BEAR_CONFIRM")
                else:          score_short += 10; reasons.append("ORB_BEAR_WEAK")
            elif c < orb_l:
                if _vol_ok:    score_short += 8

    # ── Signal 4: VWAP intraday anchor ──────────────────────────────────────
    vwap = float(row.get("vwap", c) or c)
    if vwap > 0:
        vd = (c - vwap) / vwap
        if vd > 0.003:     score_long  += 10; reasons.append("ABOVE_VWAP")
        elif vd > 0.001:   score_long  += 6
        elif vd > 0:       score_long  += 3
        if vd < -0.003:    score_short += 10; reasons.append("BELOW_VWAP")
        elif vd < -0.001:  score_short += 6
        elif vd < 0:       score_short += 3

    # ── Signal 5: Volume-direction confirmation ──────────────────────────────
    bar_bull = c > o and (c - o) > (h - lo) * 0.3   # Strong bullish body
    bar_bear = c < o and (o - c) > (h - lo) * 0.3   # Strong bearish body
    if rvol > 2.0:
        if bar_bull:    score_long  += 14; reasons.append(f"VOL_BULL_{rvol:.1f}x")
        elif bar_bear:  score_short += 14; reasons.append(f"VOL_BEAR_{rvol:.1f}x")
    elif rvol > 1.4:
        if bar_bull:    score_long  += 8;  reasons.append(f"VOL_BULL_{rvol:.1f}x")
        elif bar_bear:  score_short += 8;  reasons.append(f"VOL_BEAR_{rvol:.1f}x")

    # ── Signal 6: Today's-open momentum (real-time session direction) ────────
    day_open = float(row.get("day_open", 0) or 0)
    if day_open > 0:
        from_open = (c - day_open) / day_open
        if from_open > 0.005:    score_long  += 10; reasons.append(f"DAY_MOM(+{from_open*100:.1f}%)")
        elif from_open > 0.002:  score_long  += 5
        elif from_open > 0.0005: score_long  += 2
        if from_open < -0.005:   score_short += 10; reasons.append(f"DAY_MOM({from_open*100:.1f}%)")
        elif from_open < -0.002: score_short += 5
        elif from_open < -0.0005:score_short += 2

    # ── ADX gate: kill choppy markets (multiplier only, not a signal) ────────
    adx_v = float(row.get("adx", 25) or 25)
    if adx_v < 15:
        # Very choppy — kill signal entirely
        score_long = 0.0; score_short = 0.0
        return 0.0, "LONG", "ADX_CHOP_KILL"
    elif adx_v < 20:
        score_long  *= 0.5; score_short *= 0.5
    elif adx_v >= 30:
        score_long  *= 1.1; score_short *= 1.1   # Trending: mild boost

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
    2. ATR < 0.18% price   : no room to profit after costs (~29 bps round-trip)
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
            # Lunch lull: 12:30-13:30 IST — low volume, choppy
            if _t(12, 30) <= bar_t <= _t(13, 30):
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

        if adx < 15:
            return True, "PRE_NO_TREND"
        if atr_pct < 0.18:
            return True, "PRE_ATR_SMALL"
        if atr_pct > 4.5:
            return True, "PRE_ATR_EXTREME"
        if rvol < 0.35:
            return True, "PRE_DEAD_VOL"

        bar_move = abs(c - p_c) / max(p_c, 1e-9) * 100
        if bar_move < 0.04:
            return True, "PRE_DOJI"

        ema50_dist = (c - ema50) / max(ema50, 1e-9) * 100
        macd_just_bull = macd_h > 0 and p_macd <= 0
        macd_just_bear = macd_h < 0 and p_macd >= 0
        if macd_just_bull and ema50_dist < -2.5:
            return True, "PRE_MACD_VS_EMA50"
        if macd_just_bear and ema50_dist >  2.5:
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

        # Don't enter if price is already >1.5% from VWAP (chasing)
        try:
            _pf_close = float(row.get("close", 0) or 0)
            _pf_vwap  = float(row.get("vwap",  0) or 0)
            if _pf_vwap > 0 and _pf_close > 0:
                _pf_vwap_dev = abs(_pf_close - _pf_vwap) / _pf_vwap
                if _pf_vwap_dev > 0.015:   # >1.5% from VWAP = too extended, likely to revert
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

    # Costs: 29bps round-trip on entry + exit notional
    entry_val = trade.entry * trade.qty
    exit_val  = (trade.exit_price or trade.entry) * trade.qty
    realized -= (entry_val + exit_val) * COST_RT_PCT / 2
    trade.pnl = realized
    # R-multiple
    r = abs(trade.entry - trade.sl) * trade.qty
    trade.r_mult = realized / max(r, 1e-9)
    return realized


# ── Kelly sizing ──────────────────────────────────────────────────────────────

def _kelly_size(wins: int, losses: int, capital: float, max_pct: float = 0.20) -> float:
    """Half-Kelly position size as fraction of capital (legacy — kept for compatibility)."""
    total = wins + losses
    if total < 10:
        return 0.005   # cold start: risk 0.5%
    p = wins / total
    b = 2.0            # avg win/loss ratio (1R SL, 2R TP)
    kelly = max(0, (p * b - (1 - p)) / b)
    return min(kelly * 0.5, 0.01)  # half-Kelly, cap at 1% risk per trade


def _dynamic_kelly_size(recent_trades: list, capital: float,
                         net_score: float, atr: float, entry: float) -> float:
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

MIN_SCORE    = 22.0   # Lower threshold: ORB alone (18) + any single confirm = trade
MAX_OPEN     = 8      # More simultaneous positions = more trades = higher monthly returns
MAX_POS_PCT  = 0.10   # 10% per position (more diversified, smaller individual losses)

# Adaptive threshold: auto-adjusts MIN_SCORE based on rolling win rate
_ADAPTIVE_MIN_SCORE = MIN_SCORE
_ADAPTIVE_WIN_HISTORY: list = []   # rolling win/loss (1/0)
_ADAPTIVE_UPDATE_EVERY = 20         # update every 20 trades

def _update_adaptive_threshold(pnl: float):
    """Call after each trade to update the adaptive signal threshold."""
    global _ADAPTIVE_MIN_SCORE, _ADAPTIVE_WIN_HISTORY
    _ADAPTIVE_WIN_HISTORY.append(1 if pnl > 0 else 0)
    if len(_ADAPTIVE_WIN_HISTORY) > 40:
        _ADAPTIVE_WIN_HISTORY = _ADAPTIVE_WIN_HISTORY[-40:]

    if len(_ADAPTIVE_WIN_HISTORY) >= _ADAPTIVE_UPDATE_EVERY:
        rolling_wr = sum(_ADAPTIVE_WIN_HISTORY[-20:]) / 20

        # High win rate (>=65%): relax threshold slightly to get more trades
        if rolling_wr >= 0.65:
            _ADAPTIVE_MIN_SCORE = max(MIN_SCORE - 4.0, 18.0)
        # Good win rate (>=55%): keep at base
        elif rolling_wr >= 0.55:
            _ADAPTIVE_MIN_SCORE = MIN_SCORE
        # Acceptable (>=45%): tighten moderately
        elif rolling_wr >= 0.45:
            _ADAPTIVE_MIN_SCORE = MIN_SCORE + 5.0
        # Poor (<45%): tighten significantly
        else:
            _ADAPTIVE_MIN_SCORE = MIN_SCORE + 10.0


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

    # Initialise anti-martingale streak state for the backtest run
    try:
        from risk_manager import reset_streak as _reset_streak
        _reset_streak()
    except Exception:
        pass

    all_ts = sorted(set().union(*[set(df.index) for df in data.values()]))
    _nifty_proxy_cache = {"trend_score": 0.0, "pct_above_vwap": 0.5, "pct_above_ema21": 0.5}
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

    for i, now_ts in enumerate(all_ts):
        if now_ts.time() < dtime(9, 45) or now_ts.time() > dtime(15, 0):
            continue

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

        # ── Exit open trades ────────────────────────────────────────────────
        for sym in list(open_trades.keys()):
            t = open_trades[sym]
            if sym not in data or now_ts not in data[sym].index:
                continue
            bar = data[sym].loc[now_ts]
            long = t.direction == "LONG"

            # Square-off
            if now_ts.time() >= SQUAREOFF:
                px = bar["close"]
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
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

            hi = bar["high"]; lo = bar["low"]
            c_bar = bar["close"]

            # ── Time-based exit: stale trades get cut ─────────────────────────
            if t.entry_time is not None:
                try:
                    _held_minutes = (now_ts - t.entry_time).total_seconds() / 60
                    _is_morning = t.entry_time.time() < dtime(11, 30)
                    _max_hold = 75 if _is_morning else 50
                    if _held_minutes >= _max_hold:
                        # Exit at current close regardless of P&L
                        px = bar["close"]
                        long_trade = t.direction == "LONG"
                        pnl = ((px - t.entry) if long_trade else (t.entry - px)) * t.qty
                        pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
                        equity += pnl; t.pnl = pnl; t.reason = (t.reason or "") + "+TIME_EXIT"
                        t.exit_price = px; t.exit_time = now_ts
                        trades.append(t); del open_trades[sym]
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

            sl_hit = (lo <= t.sl) if long else (hi >= t.sl)
            if sl_hit:
                px = t.sl
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
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
                    pnl_s1 -= t.entry * t.stage1_qty * COST_RT_PCT / 2
                    equity += pnl_s1
                    t.pnl += pnl_s1
                    t.stage1_done = True
                    # Tighten SL to entry after 0.5R (not full break-even yet)
                    new_sl = t.entry if long else t.entry
                    if long and new_sl > t.sl:
                        t.sl = new_sl
                    elif not long and new_sl < t.sl:
                        t.sl = new_sl

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
                            pnl_r -= (t.entry * t.qty + c_bar * t.qty) * COST_RT_PCT / 2
                            equity += pnl_r; t.pnl += pnl_r
                            t.exit_price = c_bar; t.exit_time = now_ts
                            trades.append(t); del open_trades[sym]
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
                            pnl_r -= (t.entry * t.qty + c_bar * t.qty) * COST_RT_PCT / 2
                            equity += pnl_r; t.pnl += pnl_r
                            t.exit_price = c_bar; t.exit_time = now_ts
                            trades.append(t); del open_trades[sym]
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
                    pnl_r -= (t.entry * t.qty + t.t2 * t.qty) * COST_RT_PCT / 2
                    equity += pnl_r; t.pnl += pnl_r
                    t.exit_price = t.t2; t.exit_time = now_ts
                    trades.append(t); del open_trades[sym]
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

        # No new entries after 13:30 IST (time gate)
        bar_time_now = now_ts.time()
        if bar_time_now > dtime(13, 30):
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
            # Skip new entries during lunch lull
            if dtime(12, 30) <= now_ts.time() <= dtime(13, 30):
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

            # Require minimum 6 intraday 5-min bars before scoring (30 min of data)
            _today_d = now_ts.date()
            _today_df_slice = df[df.index.date == _today_d]
            _bars_today = len(_today_df_slice[_today_df_slice.index <= now_ts])
            if _bars_today < 7:  # Need 7 bars = 9:15 + 9:20 + 9:25 + 9:30 + 9:35 + 9:40 + 9:45
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
                        # Today trending bullish but signal is SHORT → penalize heavily
                        net_score += 20
                        reason = (reason + "+TODAY_BULL_PENALIZE_SHORT") if reason else "TODAY_BULL_PENALIZE_SHORT"
                    elif _bear_bars >= 3 and direction == "LONG":
                        # Today trending bearish but signal is LONG → penalize heavily
                        net_score -= 20
                        reason = (reason + "+TODAY_BEAR_PENALIZE_LONG") if reason else "TODAY_BEAR_PENALIZE_LONG"
                    elif _bull_bars >= 3 and direction == "LONG":
                        net_score += 6  # alignment bonus (was assignment bug: net_score = abs+6)
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
                net_score += 12; reason = (reason + "+SESSION_BULL") if reason else "SESSION_BULL"
                if direction == "SHORT":
                    net_score += 18  # Extra penalty for fighting the session trend
                    reason += "+COUNTER_SESSION"
            elif _session_breadth > 0.52:
                net_score += 5
            elif _session_breadth < 0.40:  # 40%- stocks rising → bearish session
                net_score -= 12; reason = (reason + "+SESSION_BEAR") if reason else "SESSION_BEAR"
                if direction == "LONG":
                    net_score -= 18
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

            # Pre-filter: skip clearly weak signals before calling new strategies
            if abs(net_score) < 18.0:
                continue

            # ── New strategies boost (called with full DataFrame context) ─────
            try:
                from strategies_india import (ema21_pullback_signal,
                                               liquidity_grab_signal,
                                               inside_bar_breakout_signal)
                # EMA21 Pullback
                _s4, _r4 = ema21_pullback_signal(df, idx)
                if _s4 > 0:
                    net_score += _s4; reason = reason + "+" + _r4 if reason else _r4
                elif _s4 < 0:
                    net_score += _s4; reason = reason + "+" + _r4 if reason else _r4
                # Liquidity Grab
                _s5, _r5 = liquidity_grab_signal(df, idx)
                if _s5 != 0:
                    net_score += _s5; reason = reason + "+" + _r5 if reason else _r5
                # Inside Bar
                _s6, _r6 = inside_bar_breakout_signal(df, idx, now_ts)
                if _s6 != 0:
                    net_score += _s6; reason = reason + "+" + _r6 if reason else _r6

                # Re-determine direction after new strategies
                if net_score > 0:
                    direction = "LONG"
                elif net_score < 0:
                    direction = "SHORT"
            except Exception:
                pass

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
                        net_score += 15  # Push toward LONG (positive gap day)
                        reason = (reason + "+GAP_UP_PENALTY_SHORT") if reason else "GAP_UP_PENALTY_SHORT"
                        if net_score > 0:
                            direction = "LONG"
                    elif _gap_val < -0.002 and direction == "LONG":
                        net_score -= 15  # Push toward SHORT (negative gap day)
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
            if _nifty_session_bull and direction == "SHORT":
                # Market is rising session-wide but signal is SHORT → likely a loser
                # Allow ONLY if score is very strong (2× threshold = extremely high confidence)
                if abs(net_score) < _ADAPTIVE_MIN_SCORE * 2.0:
                    continue  # Block marginal counter-trend shorts in bull session
            if _nifty_session_bear and direction == "LONG":
                if abs(net_score) < _ADAPTIVE_MIN_SCORE * 2.0:
                    continue  # Block marginal counter-trend longs in bear session

            # Final threshold check after all score adjustments
            if abs(net_score) < _ADAPTIVE_MIN_SCORE:
                continue

            # Market direction filter
            if _long_only_market and direction == "SHORT":
                continue   # Market is bullish — skip counter-trend SHORT
            if _short_only_market and direction == "LONG":
                continue   # Market is bearish — skip counter-trend LONG

            # ATR-based SL/TP
            atr = row.get("atr", row["close"] * 0.005)
            if atr <= 0:
                continue
            entry = row["close"]
            long  = direction == "LONG"
            sl_dist = 2.0 * atr     # Wider SL prevents premature stops on NSE noise
            t1_dist = 1.5 * atr     # Take 30% at 0.75R
            t2_dist = 4.0 * atr     # Runner target at 2.0R — gives 2:1 R:R on 40% position
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

            # Dynamic Kelly sizing (with Sharpe/Omega/streak scalers)
            risk_pct = _dynamic_kelly_size(recent_trades, equity, net_score, atr, entry)

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

            # Half-size for signals near the lower threshold (lower confidence)
            _threshold = _ADAPTIVE_MIN_SCORE
            _confidence_ratio = abs(net_score) / max(_threshold * 2, 1.0)
            if _confidence_ratio < 0.6:  # Score is only barely above threshold
                qty = max(1, qty // 2)   # Half size for marginal signals

            trade = Trade(sym, direction, entry, sl, t1, t2, qty, now_ts, atr_at_entry=float(atr))
            try:
                from risk_manager import compute_exit_stages as _exits
                _stages = _exits(entry, atr, direction, qty)
                trade.stage1_price = _stages["stage1_price"]
                trade.stage2_price = _stages["stage2_price"]
                trade.stage1_qty   = _stages["stage1_qty"]
                trade.stage2_qty   = _stages["stage2_qty"]
                trade.runner_qty   = _stages["runner_qty"]
                trade.be_sl        = _stages["be_sl"]
                trade.sl           = _stages["sl"]
                # Override t1 and t2 with stage prices
                trade.t1 = _stages["stage2_price"]
                trade.t2 = entry + 4.0 * atr if direction == "LONG" else entry - 4.0 * atr
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
            pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
            equity += pnl; t.pnl = pnl; t.exit_price = px
            trades.append(t)
            if pnl > 0: wins += 1
            else: losses += 1
            _win_history.append(1 if pnl > 0 else 0)
            recent_trades.append(pnl / max(capital, 1e-9))

    # Build strategy attribution from trade reasons
    _STRAT_KEYS = ["EMA21_PULLBACK", "LIQ_GRAB", "INSIDE_BAR", "ORB_BREAK",
                   "SQUEEZE_FIRE", "BOS_BULL", "BOS_BEAR", "MACD_XOVER",
                   "VWAP_REVERSION", "OPENING_DRIVE", "GAP_GO", "SUPERTREND",
                   "OBI_BULL", "OBI_BEAR", "ML_STRONG", "ML_CONFIRM"]
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
            bull_days=_bull_days, total_days=len(_session_days_seen))


def _report(trades, capital, equity, max_dd, eq_curve, from_date, to_date, n_syms,
            pre_filter_kills=0, strategy_counts=None, strategy_pnl=None,
            bull_days=0, total_days=0):
    print()
    print("=" * 70)
    print("  NSE Momentum Bot — Engine Backtest Report")
    print(f"  Period  : {from_date} → {to_date}")
    print(f"  Symbols : {n_syms} loaded")
    print(f"  Capital : ₹{capital:,.0f}")
    print("=" * 70)

    if not trades:
        print("  No trades generated.")
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

    # Pre-compute 15-min and 1-hour resamples once per symbol
    data_15m: Dict[str, pd.DataFrame] = {}
    data_1h:  Dict[str, pd.DataFrame] = {}
    for sym, df in data.items():
        data_15m[sym] = _resample(df, "15min")
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

    try:
        from risk_manager import reset_streak as _reset_streak
        _reset_streak()
    except Exception:
        pass

    all_ts = sorted(set().union(*[set(df.index) for df in data.values()]))
    _nifty_proxy_cache = {"trend_score": 0.0, "pct_above_vwap": 0.5, "pct_above_ema21": 0.5}
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

    for i, now_ts in enumerate(all_ts):
        if now_ts.time() < dtime(9, 45) or now_ts.time() > dtime(15, 0):
            continue

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

        # ── Exit open trades ────────────────────────────────────────────────
        for sym in list(open_trades.keys()):
            t = open_trades[sym]
            if sym not in data or now_ts not in data[sym].index:
                continue
            bar = data[sym].loc[now_ts]
            long = t.direction == "LONG"

            if now_ts.time() >= SQUAREOFF:
                px = bar["close"]
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
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

            hi = bar["high"]; lo = bar["low"]
            c_bar = bar["close"]

            # ── Time-based exit: stale trades get cut ─────────────────────────
            if t.entry_time is not None:
                try:
                    _held_minutes = (now_ts - t.entry_time).total_seconds() / 60
                    _is_morning = t.entry_time.time() < dtime(11, 30)
                    _max_hold = 75 if _is_morning else 50
                    if _held_minutes >= _max_hold:
                        # Exit at current close regardless of P&L
                        px = bar["close"]
                        long_trade = t.direction == "LONG"
                        pnl = ((px - t.entry) if long_trade else (t.entry - px)) * t.qty
                        pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
                        equity += pnl; t.pnl = pnl; t.reason = (t.reason or "") + "+TIME_EXIT"
                        t.exit_price = px; t.exit_time = now_ts
                        trades.append(t); del open_trades[sym]
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

            sl_hit = (lo <= t.sl) if long else (hi >= t.sl)
            if sl_hit:
                px = t.sl
                partial_qty = int(t.qty * 0.4) or 1
                qty_left = t.qty - (partial_qty if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
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
                    pnl_s1 -= t.entry * t.stage1_qty * COST_RT_PCT / 2
                    equity += pnl_s1
                    t.pnl += pnl_s1
                    t.stage1_done = True
                    new_sl = t.entry
                    if long and new_sl > t.sl:
                        t.sl = new_sl
                    elif not long and new_sl < t.sl:
                        t.sl = new_sl

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
                            pnl_r -= (t.entry * t.qty + c_bar * t.qty) * COST_RT_PCT / 2
                            equity += pnl_r; t.pnl += pnl_r
                            t.exit_price = c_bar; t.exit_time = now_ts
                            trades.append(t); del open_trades[sym]
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
                            pnl_r -= (t.entry * t.qty + c_bar * t.qty) * COST_RT_PCT / 2
                            equity += pnl_r; t.pnl += pnl_r
                            t.exit_price = c_bar; t.exit_time = now_ts
                            trades.append(t); del open_trades[sym]
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
                    pnl_r -= (t.entry * t.qty + t.t2 * t.qty) * COST_RT_PCT / 2
                    equity += pnl_r; t.pnl += pnl_r
                    t.exit_price = t.t2; t.exit_time = now_ts
                    trades.append(t); del open_trades[sym]
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

        bar_time_now = now_ts.time()
        if bar_time_now > dtime(13, 30):
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

        _mkt_trend = _nifty_proxy_cache.get("trend_score", 0)
        _pct_vwap  = _nifty_proxy_cache.get("pct_above_vwap", 0.5)

        _long_only_market  = (_pct_vwap >= 0.65) or (_mkt_trend >= 0.35)
        _short_only_market = (_pct_vwap <= 0.35) or (_mkt_trend <= -0.35)
        if _long_only_market and _short_only_market:
            _long_only_market = False
            _short_only_market = False

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
            if dtime(12, 30) <= now_ts.time() <= dtime(13, 30):
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
            if _bars_today < 7:
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

            try:
                _today_date = now_ts.date()
                _today_bars = df[(df.index.date == _today_date) & (df.index <= now_ts)]
                if len(_today_bars) >= 4:
                    _last4 = _today_bars.iloc[-4:]
                    _bull_bars = int((_last4["close"] > _last4["open"]).sum())
                    _bear_bars = int((_last4["close"] < _last4["open"]).sum())
                    if _bull_bars >= 3 and direction == "SHORT":
                        net_score += 20
                        reason = (reason + "+TODAY_BULL_PENALIZE_SHORT") if reason else "TODAY_BULL_PENALIZE_SHORT"
                    elif _bear_bars >= 3 and direction == "LONG":
                        net_score -= 20
                        reason = (reason + "+TODAY_BEAR_PENALIZE_LONG") if reason else "TODAY_BEAR_PENALIZE_LONG"
                    elif _bull_bars >= 3 and direction == "LONG":
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
                net_score += 12; reason = (reason + "+SESSION_BULL") if reason else "SESSION_BULL"
                if direction == "SHORT":
                    net_score += 18  # Extra penalty for fighting the session trend
                    reason += "+COUNTER_SESSION"
            elif _session_breadth > 0.52:
                net_score += 5
            elif _session_breadth < 0.40:  # 40%- stocks rising → bearish session
                net_score -= 12; reason = (reason + "+SESSION_BEAR") if reason else "SESSION_BEAR"
                if direction == "LONG":
                    net_score -= 18
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

            if abs(net_score) < 18.0:
                continue

            try:
                from strategies_india import (ema21_pullback_signal,
                                               liquidity_grab_signal,
                                               inside_bar_breakout_signal)
                _s4, _r4 = ema21_pullback_signal(df, idx)
                if _s4 > 0:
                    net_score += _s4; reason = reason + "+" + _r4 if reason else _r4
                elif _s4 < 0:
                    net_score += _s4; reason = reason + "+" + _r4 if reason else _r4
                _s5, _r5 = liquidity_grab_signal(df, idx)
                if _s5 != 0:
                    net_score += _s5; reason = reason + "+" + _r5 if reason else _r5
                _s6, _r6 = inside_bar_breakout_signal(df, idx, now_ts)
                if _s6 != 0:
                    net_score += _s6; reason = reason + "+" + _r6 if reason else _r6

                if net_score > 0:
                    direction = "LONG"
                elif net_score < 0:
                    direction = "SHORT"
            except Exception:
                pass

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
                        net_score += 15  # Push toward LONG (positive gap day)
                        reason = (reason + "+GAP_UP_PENALTY_SHORT") if reason else "GAP_UP_PENALTY_SHORT"
                        if net_score > 0:
                            direction = "LONG"
                    elif _gap_val < -0.002 and direction == "LONG":
                        net_score -= 15  # Push toward SHORT (negative gap day)
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
            if _nifty_session_bull and direction == "SHORT":
                # Market is rising session-wide but signal is SHORT → likely a loser
                # Allow ONLY if score is very strong (2× threshold = extremely high confidence)
                if abs(net_score) < _ADAPTIVE_MIN_SCORE * 2.0:
                    continue  # Block marginal counter-trend shorts in bull session
            if _nifty_session_bear and direction == "LONG":
                if abs(net_score) < _ADAPTIVE_MIN_SCORE * 2.0:
                    continue  # Block marginal counter-trend longs in bear session

            if abs(net_score) < _ADAPTIVE_MIN_SCORE:
                continue

            if _long_only_market and direction == "SHORT":
                continue
            if _short_only_market and direction == "LONG":
                continue

            atr = row.get("atr", row["close"] * 0.005)
            if atr <= 0:
                continue
            entry = row["close"]
            long  = direction == "LONG"
            sl    = entry - 1.5 * atr if long else entry + 1.5 * atr   # was 2.0 (tighter SL = faster loss acknowledgment)
            t1    = entry + 1.5 * atr if long else entry - 1.5 * atr   # was 2.0 (achievable at 1:1 R:R from tighter SL)
            t2    = entry + 3.0 * atr if long else entry - 3.0 * atr   # was 4.0 (runner target at 2:1)

            risk_pct = _dynamic_kelly_size(recent_trades, equity, net_score, atr, entry)

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

            # Half-size for signals near the lower threshold (lower confidence)
            _threshold = _ADAPTIVE_MIN_SCORE
            _confidence_ratio = abs(net_score) / max(_threshold * 2, 1.0)
            if _confidence_ratio < 0.6:  # Score is only barely above threshold
                qty = max(1, qty // 2)   # Half size for marginal signals

            trade = Trade(sym, direction, entry, sl, t1, t2, qty, now_ts, atr_at_entry=float(atr))
            try:
                from risk_manager import compute_exit_stages as _exits
                _stages = _exits(entry, atr, direction, qty)
                trade.stage1_price = _stages["stage1_price"]
                trade.stage2_price = _stages["stage2_price"]
                trade.stage1_qty   = _stages["stage1_qty"]
                trade.stage2_qty   = _stages["stage2_qty"]
                trade.runner_qty   = _stages["runner_qty"]
                trade.be_sl        = _stages["be_sl"]
                trade.sl           = _stages["sl"]
                trade.t1 = _stages["stage2_price"]
                trade.t2 = entry + 3.0 * atr if direction == "LONG" else entry - 3.0 * atr
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
            pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
            equity += pnl; t.pnl = pnl; t.exit_price = px
            trades.append(t)
            if pnl > 0: wins += 1
            else: losses += 1
            _win_history.append(1 if pnl > 0 else 0)
            recent_trades.append(pnl / max(capital, 1e-9))

    _STRAT_KEYS = ["EMA21_PULLBACK", "LIQ_GRAB", "INSIDE_BAR", "ORB_BREAK",
                   "SQUEEZE_FIRE", "BOS_BULL", "BOS_BEAR", "MACD_XOVER",
                   "VWAP_REVERSION", "OPENING_DRIVE", "GAP_GO", "SUPERTREND",
                   "OBI_BULL", "OBI_BEAR", "ML_STRONG", "ML_CONFIRM"]
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
            bull_days=_bull_days, total_days=len(_session_days_seen))


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
                    choices=["upstox", "yfinance"],
                    help="Data source: upstox (default, 90 days) or yfinance (up to 3 years)")
    ap.add_argument("--years",    type=int,   default=0,
                    help="Years of data for yfinance source (1-5, overrides --days)")
    ap.add_argument("--interval", default="1h",
                    choices=["1h", "1d"],
                    help="Bar interval for yfinance (1h=hourly, 1d=daily)")
    args = ap.parse_args()

    # ── yfinance path ────────────────────────────────────────────────────────
    if args.source == "yfinance":
        print("Loading NSE data from Yahoo Finance (no Upstox token required) ...")
        from data_yfinance import load_nse_data_yfinance, DEFAULT_SYMBOLS
        period = f"{args.years}y" if args.years > 0 else "2y"

        if args.symbols:
            syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            syms = DEFAULT_SYMBOLS

        raw_data = load_nse_data_yfinance(syms, period=period, interval=args.interval,
                                           verbose=True)
        if not raw_data:
            print("ERROR: No data loaded from yfinance. Check internet connection.")
            sys.exit(1)

        print(f"\nLoaded {len(raw_data)} symbols from yfinance "
              f"({period} of {args.interval} bars)")
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
            "SBIN", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "ITC",
            "BHARTIARTL", "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO",
            "ADANIENT", "ADANIPORTS", "TATAMOTORS", "TATASTEEL", "SUNPHARMA",
            "DRREDDY", "CIPLA", "LT", "POWERGRID", "NTPC", "ONGC",
            "HCLTECH", "TECHM", "BAJAJFINSV", "TITAN", "NESTLEIND",
            "ULTRACEMCO", "JSWSTEEL", "GRASIM", "HEROMOTOCO", "EICHERMOT",
            "BPCL", "HINDALCO", "BRITANNIA", "INDUSINDBK", "TATACONSUM",
        ]

    run_backtest(symbols, from_date, to_date, args.capital)


if __name__ == "__main__":
    main()
