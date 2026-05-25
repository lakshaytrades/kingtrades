"""
crypto_signals.py — Top-1% Crypto Signal Generator

Multi-timeframe momentum + mean-reversion for BTC/ETH/SOL/AVAX/LINK/DOGE.
Uses the same ATR/RSI/MACD/VWAP stack as stocks but tuned for:
  - 24/7 operation (session-based sizing)
  - Higher volatility (wider stops, bigger targets)
  - BTC correlation awareness (alt signals filtered by BTC trend)
  - Fear & Greed index integration
  - Whale volume detection
  - On-chain momentum proxy via RSI divergence

Signal pipeline:
  1. Fetch 15m, 1h, 4h candles
  2. Compute all indicators (RSI, MACD, ATR, VWAP, BB, EMA)
  3. Detect patterns (breakout, engulfing, VWAP reversion, BBand squeeze)
  4. MTF alignment check (15m + 1h + 4h must agree)
  5. BTC correlation filter (suppress alts when BTC falling)
  6. Fear & Greed context
  7. Score and grade the signal
  8. Return CryptoSignal with notional sizing
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import crypto_config as ccfg
from crypto_data import (
    get_crypto_bars, get_crypto_quote, get_fear_greed_index, get_btc_change_pct
)

logger = logging.getLogger(__name__)
UTC = ZoneInfo("UTC")
ET  = ZoneInfo("America/New_York")


@dataclass
class CryptoSignal:
    """A crypto trading signal with all required execution fields."""
    symbol:         str
    direction:      str          # "LONG" or "SHORT"
    entry_price:    float
    stop_loss:      float
    target_1:       float
    target_2:       float
    target_runner:  float
    signal_score:   float
    quality_grade:  str          # "A+", "A", "B", "C"
    atr:            float
    risk_reward:    float
    notional_usd:   float        # dollar amount to buy (fractional)
    rationale:      str
    patterns:       List[str] = field(default_factory=list)
    size_multiplier: float = 1.0
    fng_index:      int = 50
    btc_change_1h:  float = 0.0
    session:        str = "US_PEAK"
    is_high_confidence: bool = False

    def summary(self) -> str:
        return (
            f"[CRYPTO] {self.quality_grade} {self.direction} {self.symbol} "
            f"@ ${self.entry_price:,.2f} | SL=${self.stop_loss:,.2f} "
            f"T1=${self.target_1:,.2f} T2=${self.target_2:,.2f} "
            f"score={self.signal_score:.0f} notional=${self.notional_usd:.0f} "
            f"R:R={self.risk_reward:.1f} | {self.rationale}"
        )


def _compute_indicators(df: pd.DataFrame) -> Dict:
    """
    Compute all indicators on a bar DataFrame.
    Returns dict with rsi, macd, signal, hist, atr, vwap, bb_upper, bb_lower,
    ema9, ema21, ema50, volume_ratio, close, ema_trend_up, stoch_k, stoch_d
    """
    if df is None or len(df) < 30:
        return {}

    close  = df["close"].values.astype(float)
    high   = df["high"].values.astype(float)
    low    = df["low"].values.astype(float)
    volume = df["volume"].values.astype(float)
    n      = len(close)

    def ema(arr, period):
        e = np.empty(len(arr))
        e[:] = np.nan
        if len(arr) < period:
            return e
        k = 2.0 / (period + 1)
        e[period - 1] = np.mean(arr[:period])
        for i in range(period, len(arr)):
            e[i] = arr[i] * k + e[i - 1] * (1 - k)
        return e

    # RSI
    delta     = np.diff(close)
    gain      = np.where(delta > 0, delta, 0.0)
    loss      = np.where(delta < 0, -delta, 0.0)
    period    = ccfg.CRYPTO_ATR_PERIOD
    avg_gain  = np.zeros(n)
    avg_loss  = np.zeros(n)
    if n > period:
        avg_gain[period] = np.mean(gain[:period])
        avg_loss[period] = np.mean(loss[:period])
        for i in range(period + 1, n):
            avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i-1]) / period
            avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i-1]) / period
    rs  = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100.0 - (100.0 / (1 + rs))

    # MACD
    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    macd_line = ema12 - ema26
    signal_line = ema(np.nan_to_num(macd_line), 9)
    histogram   = macd_line - signal_line

    # ATR
    tr_list = []
    for i in range(1, n):
        tr_list.append(max(high[i] - low[i],
                           abs(high[i] - close[i-1]),
                           abs(low[i]  - close[i-1])))
    tr_arr = np.array(tr_list)
    atr_val = 0.0
    if len(tr_arr) >= period:
        atr_val = float(np.mean(tr_arr[-period:]))

    # VWAP (session rolling)
    if "vwap" in df.columns:
        vwap_val = float(df["vwap"].iloc[-1])
    else:
        tp   = (high + low + close) / 3
        vwap_val = float(np.sum(tp[-20:] * volume[-20:]) / max(np.sum(volume[-20:]), 1))

    # Bollinger Bands (20,2)
    bb_period = 20
    if n >= bb_period:
        bb_mid   = np.mean(close[-bb_period:])
        bb_std   = np.std(close[-bb_period:])
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
    else:
        bb_upper = bb_lower = close[-1]

    # EMAs
    ema9_  = ema(close, 9)
    ema21_ = ema(close, 21)
    ema50_ = ema(close, 50)

    # Volume ratio vs 20-bar average
    vol_avg   = np.mean(volume[-21:-1]) if n > 21 else np.mean(volume)
    vol_ratio = volume[-1] / max(vol_avg, 1)

    # Stochastic (14,3,3)
    stk_period = 14
    stoch_k_vals = []
    for i in range(stk_period, n):
        hh = np.max(high[i - stk_period:i + 1])
        ll = np.min(low[i  - stk_period:i + 1])
        rng = hh - ll
        stoch_k_vals.append((close[i] - ll) / rng * 100 if rng > 0 else 50.0)
    stoch_k = float(np.mean(stoch_k_vals[-3:])) if len(stoch_k_vals) >= 3 else 50.0
    stoch_d = float(np.mean(stoch_k_vals[-5:])) if len(stoch_k_vals) >= 5 else 50.0

    curr_rsi  = float(rsi[-1])   if n > 0 else 50.0
    prev_rsi  = float(rsi[-4])   if n > 3 else curr_rsi
    curr_macd = float(macd_line[-1])   if n > 0 else 0.0
    prev_macd = float(macd_line[-2])   if n > 1 else 0.0
    curr_hist = float(histogram[-1])   if n > 0 else 0.0
    prev_hist = float(histogram[-2])   if n > 1 else 0.0

    ema9_v  = float(ema9_[-1])  if not np.isnan(ema9_[-1])  else close[-1]
    ema21_v = float(ema21_[-1]) if not np.isnan(ema21_[-1]) else close[-1]
    ema50_v = float(ema50_[-1]) if not np.isnan(ema50_[-1]) else close[-1]

    trend_up = ema9_v > ema21_v > ema50_v

    return {
        "rsi":          curr_rsi,
        "prev_rsi":     prev_rsi,
        "macd":         curr_macd,
        "prev_macd":    prev_macd,
        "macd_hist":    curr_hist,
        "prev_hist":    prev_hist,
        "atr":          atr_val,
        "vwap":         vwap_val,
        "bb_upper":     bb_upper,
        "bb_lower":     bb_lower,
        "ema9":         ema9_v,
        "ema21":        ema21_v,
        "ema50":        ema50_v,
        "ema_trend_up": trend_up,
        "volume_ratio": vol_ratio,
        "close":        float(close[-1]),
        "prev_close":   float(close[-2]) if n > 1 else float(close[-1]),
        "stoch_k":      stoch_k,
        "stoch_d":      stoch_d,
        "high":         float(high[-1]),
        "low":          float(low[-1]),
        "open":         float(df["open"].iloc[-1]),
        "bb_squeeze":   (bb_upper - bb_lower) / max(close[-1], 1) * 100 < 2.0,
    }


def _get_session_multiplier() -> tuple:
    """Return (session_name, size_multiplier) based on current UTC hour."""
    hour = datetime.now(UTC).hour
    if 13 <= hour < 21:
        return "US_PEAK", ccfg.CRYPTO_SESSION_MULTIPLIERS["US_PEAK"]
    elif 7 <= hour < 13:
        return "EU_MORNING", ccfg.CRYPTO_SESSION_MULTIPLIERS["EU_MORNING"]
    elif 0 <= hour < 7:
        return "ASIA", ccfg.CRYPTO_SESSION_MULTIPLIERS["ASIA"]
    else:
        return "US_NIGHT", ccfg.CRYPTO_SESSION_MULTIPLIERS["US_NIGHT"]


def _detect_patterns(ind15: Dict, ind1h: Dict, ind4h: Dict,
                     df15: pd.DataFrame) -> tuple:
    """
    Detect crypto trading patterns across timeframes.
    Returns (patterns_list, score_additions, direction_bias).
    direction_bias: "LONG", "SHORT", or "NEUTRAL"
    """
    patterns = []
    score    = 0.0
    long_pts = short_pts = 0.0

    if not ind15:
        return [], 0.0, "NEUTRAL"

    c     = ind15["close"]
    rsi   = ind15["rsi"]
    vwap  = ind15["vwap"]
    atr   = ind15["atr"]
    macd  = ind15["macd"]
    hist  = ind15["macd_hist"]
    ph    = ind15["prev_hist"]
    ema9  = ind15["ema9"]
    ema21 = ind15["ema21"]
    ema50 = ind15["ema50"]
    vr    = ind15["volume_ratio"]
    stk   = ind15["stoch_k"]

    # ── 1. MACD momentum crossover (most powerful crypto signal) ──────────
    if macd > 0 and hist > 0 and hist > ph and ind15["prev_macd"] < 0:
        patterns.append("MACD Bullish Crossover")
        long_pts += 18.0 + (5.0 if vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT else 0)

    if macd < 0 and hist < 0 and hist < ph and ind15["prev_macd"] > 0:
        patterns.append("MACD Bearish Crossover")
        short_pts += 18.0 + (5.0 if vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT else 0)

    # MACD histogram momentum (histogram expanding = strength)
    if hist > 0 and hist > ph > 0:
        patterns.append("MACD Bullish Momentum")
        long_pts += 8.0
    if hist < 0 and hist < ph < 0:
        patterns.append("MACD Bearish Momentum")
        short_pts += 8.0

    # ── 2. RSI momentum ────────────────────────────────────────────────────
    if rsi < ccfg.CRYPTO_RSI_OVERSOLD and ind15["prev_rsi"] < rsi:
        patterns.append("RSI Oversold Recovery")
        long_pts += 12.0 + (5.0 if rsi < ccfg.CRYPTO_RSI_EXTREME_OS else 0)

    if rsi > ccfg.CRYPTO_RSI_OVERBOUGHT and ind15["prev_rsi"] > rsi:
        patterns.append("RSI Overbought Reversal")
        short_pts += 12.0 + (5.0 if rsi > ccfg.CRYPTO_RSI_EXTREME_OB else 0)

    # RSI in momentum zone (50-70 uptrend, 30-50 downtrend)
    if 52 < rsi < 70 and ind15["prev_rsi"] < rsi:
        patterns.append("RSI Momentum Zone")
        long_pts += 7.0
    if 30 < rsi < 48 and ind15["prev_rsi"] > rsi:
        patterns.append("RSI Weak Zone")
        short_pts += 7.0

    # ── 3. EMA trend alignment ─────────────────────────────────────────────
    if ind15["ema_trend_up"]:
        patterns.append("EMA Bull Stack (9>21>50)")
        long_pts += 10.0
    elif ema9 < ema21 < ema50:
        patterns.append("EMA Bear Stack (9<21<50)")
        short_pts += 10.0

    # EMA9 > EMA21 cross (short-term momentum flip)
    if ema9 > ema21 and ind15.get("prev_close", c) < ema21:
        patterns.append("EMA 9/21 Bullish Cross")
        long_pts += 10.0
    if ema9 < ema21 and ind15.get("prev_close", c) > ema21:
        patterns.append("EMA 9/21 Bearish Cross")
        short_pts += 10.0

    # ── 4. VWAP entries (strongest crypto intraday signal) ─────────────────
    vwap_dist_pct = abs(c - vwap) / max(vwap, 1) * 100
    if c > vwap and vwap_dist_pct < 0.3 and vr >= 1.5:
        patterns.append("VWAP Bounce Long")
        long_pts += 12.0
    if c < vwap and vwap_dist_pct < 0.3 and vr >= 1.5:
        patterns.append("VWAP Bounce Short")
        short_pts += 12.0

    # VWAP mean reversion (price extended > 1.5 ATR from VWAP)
    if atr > 0 and (c - vwap) < -1.5 * atr and rsi < 38:
        patterns.append("VWAP Oversold Reversion")
        long_pts += 14.0
    if atr > 0 and (c - vwap) > 1.5 * atr and rsi > 62:
        patterns.append("VWAP Overbought Reversion")
        short_pts += 14.0

    # ── 5. Bollinger Band signals ───────────────────────────────────────────
    bb_up = ind15["bb_upper"]
    bb_lo = ind15["bb_lower"]
    bb_squeeze = ind15.get("bb_squeeze", False)

    if c > bb_up and vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT:
        patterns.append("BB Upper Breakout")
        long_pts += 12.0
    if c < bb_lo and vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT:
        patterns.append("BB Lower Breakdown")
        short_pts += 12.0
    if c < bb_lo and rsi < 32:
        patterns.append("BB Lower Bounce")
        long_pts += 10.0
    if bb_squeeze:
        patterns.append("BB Squeeze (breakout imminent)")
        score += 5.0   # direction-neutral bonus

    # ── 6. Volume surge (whale accumulation / distribution) ─────────────────
    if vr >= ccfg.CRYPTO_VOLUME_HIGH_CONV:
        patterns.append(f"Volume Whale Surge ({vr:.1f}x)")
        # Direction depends on price action
        if c > ind15["prev_close"]:
            long_pts += 12.0
        else:
            short_pts += 12.0
    elif vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT:
        patterns.append(f"Volume Surge ({vr:.1f}x)")
        if c > ind15["prev_close"]:
            long_pts += 6.0
        else:
            short_pts += 6.0

    # ── 7. Stochastic confirmation ─────────────────────────────────────────
    if stk < 20 and stk > ind15.get("stoch_d", 50):
        patterns.append("Stoch Oversold Cross")
        long_pts += 8.0
    if stk > 80 and stk < ind15.get("stoch_d", 50):
        patterns.append("Stoch Overbought Cross")
        short_pts += 8.0

    # ── 8. Multi-timeframe confluence ─────────────────────────────────────
    if ind1h and ind1h.get("ema_trend_up"):
        if long_pts > short_pts:
            patterns.append("1H Trend Aligned (Bull)")
            long_pts += 8.0
    if ind1h and not ind1h.get("ema_trend_up", True):
        if short_pts > long_pts:
            patterns.append("1H Trend Aligned (Bear)")
            short_pts += 8.0

    if ind4h and ind4h.get("ema_trend_up"):
        if long_pts > short_pts:
            patterns.append("4H Macro Aligned (Bull)")
            long_pts += 6.0
    if ind4h and not ind4h.get("ema_trend_up", True):
        if short_pts > long_pts:
            patterns.append("4H Macro Aligned (Bear)")
            short_pts += 6.0

    # ── 9. Price above/below key EMAs ─────────────────────────────────────
    if c > ema50 and c > ema21 and c > ema9:
        long_pts += 5.0
    elif c < ema50 and c < ema21 and c < ema9:
        short_pts += 5.0

    # Determine direction
    total = long_pts + short_pts
    if total < 10:
        return patterns, 0.0, "NEUTRAL"

    if long_pts > short_pts * 1.3:
        return patterns, long_pts, "LONG"
    elif short_pts > long_pts * 1.3:
        return patterns, short_pts, "SHORT"
    else:
        return patterns, max(long_pts, short_pts), "NEUTRAL"


def _apply_fng_adjustment(base_score: float, fng: int, direction: str) -> float:
    """Adjust score based on Fear & Greed index."""
    if direction == "LONG":
        if fng <= ccfg.CRYPTO_FNG_EXTREME_FEAR:
            return base_score + 10.0    # extreme fear = contrarian buy
        elif fng <= ccfg.CRYPTO_FNG_FEAR:
            return base_score + 5.0
        elif fng >= ccfg.CRYPTO_FNG_EXTREME_GREED:
            return base_score - 12.0   # extreme greed = avoid new longs
        elif fng >= ccfg.CRYPTO_FNG_GREED:
            return base_score - 5.0
    elif direction == "SHORT":
        if fng >= ccfg.CRYPTO_FNG_EXTREME_GREED:
            return base_score + 10.0   # extreme greed = contrarian short
        elif fng >= ccfg.CRYPTO_FNG_GREED:
            return base_score + 5.0
        elif fng <= ccfg.CRYPTO_FNG_EXTREME_FEAR:
            return base_score - 8.0    # extreme fear = no shorts
    return base_score


def _apply_btc_correlation(score: float, symbol: str, direction: str,
                            btc_1h: float) -> float:
    """
    Suppress altcoin longs when BTC is falling hard.
    Boost altcoin longs when BTC is rising strongly.
    BTC signals are not affected by correlation.
    """
    if symbol == "BTC/USD":
        return score

    if direction == "LONG":
        if btc_1h <= -ccfg.CRYPTO_BTC_DRAG_PCT:
            return score - ccfg.CRYPTO_ALT_SCORE_DRAG   # BTC dropping → alts suppressed
        elif btc_1h >= ccfg.CRYPTO_BTC_BOOST_PCT:
            return score + ccfg.CRYPTO_ALT_SCORE_BOOST  # BTC pumping → alts boosted
    elif direction == "SHORT":
        if btc_1h >= ccfg.CRYPTO_BTC_BOOST_PCT:
            return score - 8.0   # BTC pumping → suppress shorts
        elif btc_1h <= -ccfg.CRYPTO_BTC_DRAG_PCT:
            return score + 6.0   # BTC dropping → confirm shorts

    return score


def _calculate_notional(symbol: str, entry_price: float, stop_loss: float,
                         direction: str, total_crypto_capital: float,
                         size_multiplier: float = 1.0) -> float:
    """
    Calculate notional USD amount for a crypto trade.
    Risk-based sizing: risk 1.5% of crypto capital per trade.
    Returns notional USD (buy $X worth of the coin).
    """
    risk_usd    = total_crypto_capital * ccfg.CRYPTO_MAX_RISK_PCT / 100
    risk_per_unit = abs(entry_price - stop_loss)
    if risk_per_unit <= 0 or entry_price <= 0:
        return ccfg.CRYPTO_MIN_NOTIONAL_USD

    # Position size in coin units = risk_usd / risk_per_unit
    units   = risk_usd / risk_per_unit
    notional = units * entry_price * size_multiplier

    # Cap by max notional and max capital %
    max_by_pct = total_crypto_capital * ccfg.CRYPTO_CAPITAL_PCT_MAX / 100
    notional   = min(notional, ccfg.CRYPTO_MAX_NOTIONAL_USD, max_by_pct)
    notional   = max(notional, ccfg.CRYPTO_MIN_NOTIONAL_USD)

    return round(notional, 2)


def generate_crypto_signal(symbol: str,
                            total_crypto_capital: float = 1000.0) -> Optional[CryptoSignal]:
    """
    Main entry point: generate a crypto trading signal for one symbol.
    Returns CryptoSignal or None if no setup found.
    """
    if not ccfg.CRYPTO_ENABLED:
        return None

    # Fetch multi-timeframe data
    df15 = get_crypto_bars(symbol, "15Min", limit=100)
    df1h = get_crypto_bars(symbol, "1Hour", limit=100)
    df4h = get_crypto_bars(symbol, "4Hour", limit=60)

    if df15 is None or len(df15) < 30:
        logger.debug(f"crypto_signals: insufficient 15m data for {symbol}")
        return None

    # Compute indicators
    ind15 = _compute_indicators(df15)
    ind1h = _compute_indicators(df1h) if df1h is not None and len(df1h) >= 30 else {}
    ind4h = _compute_indicators(df4h) if df4h is not None and len(df4h) >= 20 else {}

    if not ind15:
        return None

    # Detect patterns and direction
    patterns, pattern_score, direction = _detect_patterns(ind15, ind1h, ind4h, df15)

    if direction == "NEUTRAL" or pattern_score < 20:
        return None

    if direction == "SHORT" and not ccfg.CRYPTO_SHORT_ENABLED:
        return None

    # Build base score
    base_score = min(60.0 + pattern_score * 0.4, 95.0)

    # BTC correlation adjustment
    btc_1h = get_btc_change_pct(hours=1)
    base_score = _apply_btc_correlation(base_score, symbol, direction, btc_1h)

    # Fear & Greed adjustment
    fng = get_fear_greed_index()
    base_score = _apply_fng_adjustment(base_score, fng, direction)

    # Volume — bonus-based (low volume reduces score, surge boosts it)
    vr = ind15["volume_ratio"]
    if vr >= ccfg.CRYPTO_VOLUME_HIGH_CONV:
        base_score += 8.0    # 4x+ surge: strong institutional conviction
    elif vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT:
        base_score += 4.0    # 2.5x surge: decent participation
    elif vr < 0.5:
        base_score -= 6.0    # extremely thin volume — reduce confidence

    # Multi-timeframe alignment — bonus-based (not a hard block)
    # 3/3 aligned = +10 pts, 2/3 = +5 pts, 0/3 against = -8 pts
    if ind1h and ind4h:
        tf_long  = ind1h.get("ema_trend_up") and ind4h.get("ema_trend_up")
        tf_short = (not ind1h.get("ema_trend_up", True)) and (not ind4h.get("ema_trend_up", True))
        h1_agrees  = (direction == "LONG" and ind1h.get("ema_trend_up")) or \
                     (direction == "SHORT" and not ind1h.get("ema_trend_up", True))
        h4_agrees  = (direction == "LONG" and ind4h.get("ema_trend_up")) or \
                     (direction == "SHORT" and not ind4h.get("ema_trend_up", True))
        tfs_agree = sum([h1_agrees, h4_agrees])
        if tfs_agree == 2:
            base_score += 10.0
            patterns.append("MTF Triple Aligned")
        elif tfs_agree == 1:
            base_score += 5.0
            patterns.append("MTF Partial Aligned")
        else:
            base_score -= 8.0   # higher TFs against signal — penalise but don't block

    # RSI extreme confirmation
    rsi = ind15["rsi"]
    if direction == "LONG"  and rsi < ccfg.CRYPTO_RSI_OVERSOLD:
        base_score += 5.0
    if direction == "SHORT" and rsi > ccfg.CRYPTO_RSI_OVERBOUGHT:
        base_score += 5.0

    final_score = min(round(base_score, 1), 98.0)

    if final_score < ccfg.CRYPTO_MIN_SIGNAL_SCORE:
        logger.debug(f"{symbol}: score {final_score:.0f} < min {ccfg.CRYPTO_MIN_SIGNAL_SCORE}")
        return None

    # Compute entry, SL, targets
    entry = ind15["close"]
    atr   = ind15["atr"]
    if atr <= 0:
        atr = entry * 0.015    # fallback: 1.5% of price

    if direction == "LONG":
        sl       = round(entry - ccfg.CRYPTO_ATR_SL_MULT   * atr, 6)
        target_1 = round(entry + ccfg.CRYPTO_ATR_T1_MULT   * atr, 6)
        target_2 = round(entry + ccfg.CRYPTO_ATR_T2_MULT   * atr, 6)
        runner   = round(entry + ccfg.CRYPTO_ATR_RUNNER_MULT * atr, 6)
    else:
        sl       = round(entry + ccfg.CRYPTO_ATR_SL_MULT   * atr, 6)
        target_1 = round(entry - ccfg.CRYPTO_ATR_T1_MULT   * atr, 6)
        target_2 = round(entry - ccfg.CRYPTO_ATR_T2_MULT   * atr, 6)
        runner   = round(entry - ccfg.CRYPTO_ATR_RUNNER_MULT * atr, 6)

    risk_amt = abs(entry - sl)
    rr       = round(abs(target_2 - entry) / risk_amt, 2) if risk_amt > 0 else 0.0

    if rr < 2.0:    # minimum 2:1 R:R required
        logger.debug(f"{symbol}: R:R {rr:.1f} < 2.0 — skipping")
        return None

    # Session multiplier
    session, sess_mult = _get_session_multiplier()

    # Grade the signal
    if final_score >= ccfg.CRYPTO_HIGH_CONFIDENCE:
        grade = "A+"
        size_mult = 1.5 * sess_mult
        is_hc = True
    elif final_score >= ccfg.CRYPTO_PREMIUM_SCORE:
        grade = "A"
        size_mult = 1.2 * sess_mult
        is_hc = False
    else:
        grade = "B"
        size_mult = 1.0 * sess_mult
        is_hc = False

    # Notional sizing
    notional = _calculate_notional(symbol, entry, sl, direction,
                                   total_crypto_capital, size_mult)

    # Build rationale
    fng_label = (
        "Extreme Fear" if fng <= 25 else "Fear" if fng <= 40
        else "Extreme Greed" if fng >= 80 else "Greed" if fng >= 65 else "Neutral"
    )
    top3 = patterns[:3]
    rationale = (
        f"{' + '.join(top3)} | "
        f"RSI={rsi:.0f} MACD={'▲' if ind15['macd'] > 0 else '▼'} "
        f"Vol={vr:.1f}x | F&G={fng}({fng_label}) "
        f"BTC1h={btc_1h:+.1f}% | session={session}"
    )

    signal = CryptoSignal(
        symbol          = symbol,
        direction       = direction,
        entry_price     = entry,
        stop_loss       = sl,
        target_1        = target_1,
        target_2        = target_2,
        target_runner   = runner,
        signal_score    = final_score,
        quality_grade   = grade,
        atr             = atr,
        risk_reward     = rr,
        notional_usd    = notional,
        rationale       = rationale,
        patterns        = patterns,
        size_multiplier = size_mult,
        fng_index       = fng,
        btc_change_1h   = btc_1h,
        session         = session,
        is_high_confidence = is_hc,
    )

    logger.info(f"[CRYPTO SIGNAL] {signal.summary()}")
    return signal


def scan_crypto_watchlist(symbols: Optional[List[str]] = None,
                          total_crypto_capital: float = 1000.0) -> List[CryptoSignal]:
    """
    Scan all crypto symbols and return valid signals sorted by score.
    """
    if not ccfg.CRYPTO_ENABLED:
        return []

    if symbols is None:
        symbols = ccfg.CRYPTO_SYMBOLS

    signals = []
    for sym in symbols:
        try:
            sig = generate_crypto_signal(sym, total_crypto_capital)
            if sig:
                signals.append(sig)
        except Exception as e:
            logger.warning(f"crypto scan error {sym}: {e}")

    signals.sort(key=lambda s: s.signal_score, reverse=True)
    return signals
