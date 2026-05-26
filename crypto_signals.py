"""
crypto_signals.py — Top-1% Crypto Signal Generator (PROFIT-FIRST after -$4K loss)

Multi-timeframe momentum + mean-reversion for BTC/ETH/SOL only.
Hard gates that MUST pass before any signal is generated:
  1. Session not in DISABLED_SESSIONS (US_NIGHT = 21:00-00:00 UTC is OFF)
  2. Volume >= 1.0x average (no thin-market noise)
  3. 1H EMA must agree with signal direction
  4. 4H EMA must not be opposing direction
  5. For ETH/SOL longs: BTC 4H must be in uptrend (EMA 9>21>50)
  6. Score >= 75 (raised from 68 after loss event)
  7. R:R >= 2.0

Signal scoring:
  base  = 60 + pattern_score * 0.4 (capped at 95)
  bonus = BTC correlation (±8-15) + F&G (±10) + volume (+5/+10) + MTF (+5/+12)
  final = base + bonus (max 98)
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
from crypto_patterns import (
    detect_candlestick_patterns,
    detect_chart_patterns,
    detect_smc_patterns,
    detect_wyckoff,
    detect_fibonacci,
    detect_divergence,
    detect_volume_profile,
    detect_elliott_wave,
    detect_harmonic_patterns,
    run_all_detectors,
    score_all_patterns,
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
    regime:         str = "UNKNOWN"

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
    Detect crypto trading patterns using world-class pattern library.

    Stage 1 — Indicator-based scoring (MACD/RSI/VWAP/BB/Stoch):
      Legacy indicator signals → long_pts / short_pts

    Stage 2 — Full pattern library (crypto_patterns.py):
      30+ patterns: Candlestick, Chart, SMC, Wyckoff, Fibonacci,
      Divergence, Elliott Wave, Volume Profile, Harmonics

    Stage 3 — Aggregation via score_all_patterns()
      Combines both stages into final (score, direction) with 1.25× threshold

    Returns (patterns_list, score_additions, direction_bias).
    direction_bias: "LONG", "SHORT", or "NEUTRAL"
    """
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

    legacy_patterns = []
    long_pts = short_pts = 0.0

    # ── STAGE 1: indicator-based signals (kept for speed, no df required) ──

    # MACD crossover + momentum
    if macd > 0 and hist > 0 and hist > ph and ind15["prev_macd"] < 0:
        legacy_patterns.append("MACD Bullish Crossover")
        long_pts += 18.0 + (5.0 if vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT else 0)
    if macd < 0 and hist < 0 and hist < ph and ind15["prev_macd"] > 0:
        legacy_patterns.append("MACD Bearish Crossover")
        short_pts += 18.0 + (5.0 if vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT else 0)
    if hist > 0 and hist > ph > 0:
        legacy_patterns.append("MACD Bullish Momentum")
        long_pts += 8.0
    if hist < 0 and hist < ph < 0:
        legacy_patterns.append("MACD Bearish Momentum")
        short_pts += 8.0

    # RSI
    if rsi < ccfg.CRYPTO_RSI_OVERSOLD and ind15["prev_rsi"] < rsi:
        legacy_patterns.append("RSI Oversold Recovery")
        long_pts += 12.0 + (5.0 if rsi < ccfg.CRYPTO_RSI_EXTREME_OS else 0)
    if rsi > ccfg.CRYPTO_RSI_OVERBOUGHT and ind15["prev_rsi"] > rsi:
        legacy_patterns.append("RSI Overbought Reversal")
        short_pts += 12.0 + (5.0 if rsi > ccfg.CRYPTO_RSI_EXTREME_OB else 0)
    if 52 < rsi < 70 and ind15["prev_rsi"] < rsi:
        long_pts += 7.0
    if 30 < rsi < 48 and ind15["prev_rsi"] > rsi:
        short_pts += 7.0

    # EMA stack
    if ind15["ema_trend_up"]:
        legacy_patterns.append("EMA Bull Stack (9>21>50)")
        long_pts += 10.0
    elif ema9 < ema21 < ema50:
        legacy_patterns.append("EMA Bear Stack (9<21<50)")
        short_pts += 10.0

    # VWAP
    vwap_dist_pct = abs(c - vwap) / max(vwap, 1) * 100
    if c > vwap and vwap_dist_pct < 0.3 and vr >= 1.5:
        legacy_patterns.append("VWAP Bounce Long")
        long_pts += 12.0
    if c < vwap and vwap_dist_pct < 0.3 and vr >= 1.5:
        legacy_patterns.append("VWAP Bounce Short")
        short_pts += 12.0
    if atr > 0 and (c - vwap) < -1.5 * atr and rsi < 38:
        legacy_patterns.append("VWAP Oversold Reversion")
        long_pts += 14.0
    if atr > 0 and (c - vwap) > 1.5 * atr and rsi > 62:
        legacy_patterns.append("VWAP Overbought Reversion")
        short_pts += 14.0

    # Bollinger Bands
    bb_up = ind15["bb_upper"]
    bb_lo = ind15["bb_lower"]
    if c > bb_up and vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT:
        legacy_patterns.append("BB Upper Breakout")
        long_pts += 12.0
    if c < bb_lo and vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT:
        legacy_patterns.append("BB Lower Breakdown")
        short_pts += 12.0
    if c < bb_lo and rsi < 32:
        legacy_patterns.append("BB Lower Bounce")
        long_pts += 10.0

    # Volume
    if vr >= ccfg.CRYPTO_VOLUME_HIGH_CONV:
        legacy_patterns.append(f"Volume Whale Surge ({vr:.1f}x)")
        if c > ind15.get("prev_close", c):
            long_pts += 12.0
        else:
            short_pts += 12.0
    elif vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT:
        legacy_patterns.append(f"Volume Surge ({vr:.1f}x)")
        if c > ind15.get("prev_close", c):
            long_pts += 6.0
        else:
            short_pts += 6.0

    # Stochastic
    stk_d = ind15.get("stoch_d", 50)
    if stk < 20 and stk > stk_d:
        legacy_patterns.append("Stoch Oversold Cross")
        long_pts += 8.0
    if stk > 80 and stk < stk_d:
        legacy_patterns.append("Stoch Overbought Cross")
        short_pts += 8.0

    # MTF (1H/4H context)
    if ind1h:
        if ind1h.get("ema_trend_up") and long_pts > short_pts:
            legacy_patterns.append("1H Trend Aligned (Bull)")
            long_pts += 8.0
        elif not ind1h.get("ema_trend_up", True) and short_pts > long_pts:
            legacy_patterns.append("1H Trend Aligned (Bear)")
            short_pts += 8.0
    if ind4h:
        if ind4h.get("ema_trend_up") and long_pts > short_pts:
            legacy_patterns.append("4H Macro Aligned (Bull)")
            long_pts += 6.0
        elif not ind4h.get("ema_trend_up", True) and short_pts > long_pts:
            legacy_patterns.append("4H Macro Aligned (Bear)")
            short_pts += 6.0

    # EMA stack price location
    if c > ema50 and c > ema21 and c > ema9:
        long_pts += 5.0
    elif c < ema50 and c < ema21 and c < ema9:
        short_pts += 5.0

    # ── STAGE 2: world-class pattern library ──────────────────────────────
    try:
        pattern_results = run_all_detectors(df15, ind15, ind1h or {}, ind4h or {})
        adv_score, adv_direction, adv_names = score_all_patterns(
            pattern_results, long_pts, short_pts
        )
        all_patterns = legacy_patterns + adv_names
        if adv_direction != "NEUTRAL" and adv_score > 0:
            return all_patterns, adv_score, adv_direction
    except Exception as _e:
        logger.debug(f"_detect_patterns advanced: {_e}")

    # ── STAGE 3 fallback: legacy indicator-only scoring ───────────────────
    total = long_pts + short_pts
    if total < 10:
        return legacy_patterns, 0.0, "NEUTRAL"
    if long_pts > short_pts * 1.3:
        return legacy_patterns, long_pts, "LONG"
    elif short_pts > long_pts * 1.3:
        return legacy_patterns, short_pts, "SHORT"
    return legacy_patterns, max(long_pts, short_pts), "NEUTRAL"


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


def _count_pattern_categories(patterns: list) -> int:
    """Count distinct pattern category types present."""
    categories = {
        "TREND":   {"MACD", "EMA Bull", "EMA Bear", "MTF"},
        "OSC":     {"RSI", "Stoch", "Divergence"},
        "STRUCT":  {"VWAP", "BB", "Bollinger"},
        "SMC":     {"FVG", "Order Block", "Liquidity", "BOS", "CHoCH", "Premium", "Discount"},
        "WYCKOFF": {"Spring", "Upthrust", "Accumulation", "Distribution", "Wyckoff"},
        "FIBS":    {"Fibonacci", "61.8", "Golden", "78.6%", "Extension"},
        "CANDLE":  {"Hammer", "Engulfing", "Doji", "Star", "Harami", "Pinbar", "Tweezer"},
        "CHART":   {"Double", "Head", "Flag", "Triangle", "Wedge", "Cup", "Channel"},
        "ELLIOTT": {"Elliott", "Wave 3", "Wave 5"},
        "VOLUME":  {"Volume", "POC", "VAH", "VAL", "Whale"},
    }
    found = set()
    for pat in patterns:
        for cat, keywords in categories.items():
            if any(kw.lower() in pat.lower() for kw in keywords):
                found.add(cat)
                break
    return len(found)


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

    # ── SESSION GATE — block disabled sessions entirely ────────────────────────
    session, sess_mult = _get_session_multiplier()
    if session in ccfg.CRYPTO_DISABLED_SESSIONS or sess_mult == 0.0:
        logger.debug(f"{symbol}: session {session} disabled — no new entries")
        return None

    # ── ASIA SESSION: BTC-only + higher score floor ─────────────────────────────
    # Asia (00:00-07:00 UTC) has thin liquidity and whale manipulation.
    # Only allow BTC signals during Asia; require higher score for any entry.
    if session == "ASIA":
        if symbol != "BTC/USD":
            logger.debug(f"{symbol}: ASIA session — BTC-only, skipping {symbol}")
            return None

    # ── VOLUME HARD GATE — reject below minimum ─────────────────────────────────
    vr = ind15["volume_ratio"]
    vol_gate = getattr(ccfg, "CRYPTO_VOLUME_MIN_GATE", 1.0)
    if vr < vol_gate:
        logger.debug(f"{symbol}: volume {vr:.1f}x < {vol_gate}x minimum — skipping")
        return None

    # Detect patterns and direction
    patterns, pattern_score, direction = _detect_patterns(ind15, ind1h, ind4h, df15)

    if direction == "NEUTRAL" or pattern_score < 20:
        return None

    if direction == "SHORT" and not ccfg.CRYPTO_SHORT_ENABLED:
        return None

    # Build base score:
    # New library returns 55-95 range score directly.
    # Legacy fallback returns raw points (typically 20-150) — convert with old formula.
    if pattern_score <= 95.0:
        base_score = pattern_score   # already normalised by score_all_patterns()
    else:
        base_score = min(60.0 + pattern_score * 0.4, 95.0)  # legacy fallback

    # BTC correlation adjustment
    btc_1h = get_btc_change_pct(hours=1)
    base_score = _apply_btc_correlation(base_score, symbol, direction, btc_1h)

    # Fear & Greed adjustment
    fng = get_fear_greed_index()
    base_score = _apply_fng_adjustment(base_score, fng, direction)

    # Funding rate adjustment
    try:
        from crypto_data import get_funding_rate
        funding = get_funding_rate(symbol)
        if direction == "LONG":
            if funding["sentiment"] == "LONG_HEAVY":
                base_score -= 8.0   # longs overextended in perps = risk of liquidation cascade
                patterns.append("⚠️ Funding Long Heavy")
            elif funding["sentiment"] == "SHORT_HEAVY":
                base_score += 6.0   # short squeeze potential = supports longs
                patterns.append("🚀 Funding Short Squeeze Risk")
        elif direction == "SHORT":
            if funding["sentiment"] == "SHORT_HEAVY":
                base_score -= 8.0   # shorts overextended = risk of squeeze
            elif funding["sentiment"] == "LONG_HEAVY":
                base_score += 6.0   # longs will get liquidated = supports shorts
    except Exception:
        pass

    # Volume — bonus scoring (already passed hard gate above)
    if vr >= ccfg.CRYPTO_VOLUME_HIGH_CONV:
        base_score += 10.0   # strong institutional surge
    elif vr >= ccfg.CRYPTO_VOLUME_SURGE_MULT:
        base_score += 5.0    # decent participation
    elif vr < 1.5:
        base_score -= 4.0    # low volume = lower confidence (already above min gate)

    # ── MTF ALIGNMENT — hard gates + bonus scoring ─────────────────────────────
    if ind1h and ind4h:
        h1_agrees = (direction == "LONG"  and ind1h.get("ema_trend_up")) or \
                    (direction == "SHORT" and not ind1h.get("ema_trend_up", True))
        h4_against = (direction == "LONG"  and not ind4h.get("ema_trend_up", True)) or \
                     (direction == "SHORT" and ind4h.get("ema_trend_up", False))

        # Hard gate 1: 1H must agree with 15m signal direction
        if getattr(ccfg, "CRYPTO_REQUIRE_1H_ALIGNMENT", True) and not h1_agrees:
            logger.debug(f"{symbol}: 1H EMA conflicts with {direction} signal — skip")
            return None

        # Hard gate 2: 4H must not be directly opposed
        if getattr(ccfg, "CRYPTO_REQUIRE_4H_NO_CONFLICT", True) and h4_against:
            logger.debug(f"{symbol}: 4H EMA opposes {direction} signal — skip")
            return None

        h4_agrees = (direction == "LONG"  and ind4h.get("ema_trend_up")) or \
                    (direction == "SHORT" and not ind4h.get("ema_trend_up", True))
        if h1_agrees and h4_agrees:
            base_score += 12.0
            patterns.append("MTF Triple Aligned")
        elif h1_agrees:
            base_score += 5.0
            patterns.append("MTF 1H Aligned")
    elif not ind1h:
        # No 1H data — cannot verify trend, skip
        logger.debug(f"{symbol}: no 1H data — cannot verify MTF alignment, skip")
        return None

    # ── BTC UPTREND GATE for altcoins ───────────────────────────────────────────
    if symbol != "BTC/USD" and direction == "LONG":
        if getattr(ccfg, "CRYPTO_REQUIRE_BTC_UPTREND_FOR_ALTS", True):
            btc_bars_4h = get_crypto_bars("BTC/USD", "4Hour", limit=60)
            if btc_bars_4h is not None and len(btc_bars_4h) >= 50:
                btc_ind = _compute_indicators(btc_bars_4h)
                if btc_ind and not btc_ind.get("ema_trend_up", True):
                    logger.debug(f"{symbol}: BTC 4H downtrend — no alt longs allowed")
                    return None

    # RSI extreme confirmation
    rsi = ind15["rsi"]
    if direction == "LONG"  and rsi < ccfg.CRYPTO_RSI_OVERSOLD:
        base_score += 5.0
    if direction == "SHORT" and rsi > ccfg.CRYPTO_RSI_OVERBOUGHT:
        base_score += 5.0

    # ── REGIME-BASED ADJUSTMENT ────────────────────────────────────────────────
    adjusted_min_score = ccfg.CRYPTO_MIN_SIGNAL_SCORE
    regime_size_mult   = 1.0
    regime_str         = "UNKNOWN"
    try:
        from crypto_regime import detect_regime, get_regime_adjustments
        regime_obj = detect_regime(df15, ind15)
        regime_adj = get_regime_adjustments(regime_obj)
        regime_str = regime_obj.value

        # Block longs in VOLATILE_BEAR and TRENDING_BEAR
        if direction == "LONG" and regime_str in ("VOLATILE_BEAR", "TRENDING_BEAR"):
            logger.debug(f"{symbol}: regime {regime_str} blocks LONG signals")
            return None

        # Adjust score floor
        adjusted_min_score = ccfg.CRYPTO_MIN_SIGNAL_SCORE + regime_adj["score_floor_adj"]

        # Adjust size multiplier
        regime_size_mult = regime_adj["size_mult"]

        patterns.append(f"Regime:{regime_str}")
    except Exception as _re:
        logger.debug(f"regime detection: {_re}")

    # ── MULTI-CATEGORY PATTERN GATE ────────────────────────────────────────────
    n_cats = _count_pattern_categories(patterns)
    try:
        min_cats = regime_adj["min_pattern_cats"]
    except Exception:
        min_cats = 1

    final_score = min(round(base_score, 1), 98.0)

    if n_cats < min_cats and final_score < 88.0:
        logger.debug(
            f"{symbol}: only {n_cats} pattern category, need {min_cats} "
            f"— score {final_score:.0f} insufficient"
        )
        return None

    # Asia session requires higher quality — raise floor by 8 points
    if session == "ASIA":
        adjusted_min_score = max(adjusted_min_score, ccfg.CRYPTO_MIN_SIGNAL_SCORE + 8)

    if final_score < adjusted_min_score:
        logger.debug(
            f"{symbol}: score {final_score:.0f} < adjusted min {adjusted_min_score} "
            f"(regime={regime_str}, session={session})"
        )
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

    # session and sess_mult already set at top of function (from session gate check)

    # Grade the signal
    if final_score >= ccfg.CRYPTO_HIGH_CONFIDENCE:
        grade = "A+"
        size_mult = 1.5 * sess_mult * regime_size_mult
        is_hc = True
    elif final_score >= ccfg.CRYPTO_PREMIUM_SCORE:
        grade = "A"
        size_mult = 1.2 * sess_mult * regime_size_mult
        is_hc = False
    else:
        grade = "B"
        size_mult = 1.0 * sess_mult * regime_size_mult
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
        regime          = regime_str,
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
