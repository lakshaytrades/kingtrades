"""
strategy_knowledge_base.py — KING Framework Library

8 legendary trading systems encoded as quantitative scoring functions.
Each framework scores the current setup independently. The master scorer
combines them into a single delta applied to the signal score.

BOOK SOURCES:
  Livermore  : Reminiscences of a Stock Operator (Edwin Lefevre, 1923)
  Minervini  : Trade Like a Stock Market Wizard (2013) + Think & Trade Like a Champion (2017)
  O'Neil     : How to Make Money in Stocks (1988, 4th ed 2009)
  Darvas     : How I Made $2,000,000 in the Stock Market (1960)
  Wyckoff    : The Richard D. Wyckoff Method of Trading (1931)
  Weinstein  : Secrets for Profiting in Bull and Bear Markets (1988)
  Turtle     : The Original Turtle Trading Rules (Curtis Faith, 2003)
  Soros      : The Alchemy of Finance (1987)

All functions:
  - Accept OHLCV DataFrames (5m or daily depending on framework)
  - Return (score_delta: float, reason: str)
  - Fail-open: return (0.0, "") on any error
  - Are pure functions (no side effects, no global state)

Master scorer returns total_delta ∈ [-20, +40] and a list of reasons.
"""

import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── shared daily data cache ──────────────────────────────────────────────────
_daily_cache: Dict[str, Dict] = {}   # {symbol: {"df": df, "ts": float}}
_DAILY_TTL = 3600.0                  # refresh daily bars hourly


def _get_daily_df(symbol: str):
    """Fetch/cache 1-year daily OHLCV via yfinance. Returns None on failure."""
    now = _time.monotonic()
    entry = _daily_cache.get(symbol)
    if entry and now - entry["ts"] < _DAILY_TTL and entry["df"] is not None:
        return entry["df"]
    try:
        import yfinance as yf
        df = yf.download(symbol, period="1y", interval="1d",
                         auto_adjust=True, progress=False, timeout=10)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        _daily_cache[symbol] = {"df": df, "ts": now}
        return df
    except Exception as exc:
        logger.debug(f"_get_daily_df({symbol}): {exc}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# FRAMEWORK 1 — JESSE LIVERMORE  (Reminiscences of a Stock Operator, 1923)
#
# Core rules encoded:
#  "Never buy a stock because it looks cheap — buy it at a PIVOTAL POINT."
#  "Markets are never wrong, opinions often are."
#  "Trade with the line of least resistance."
#  "Cut your losses quickly; let your profits run."
#
# Pivotal point = breakout above a clearly-defined resistance level that
# held for at least 3 sessions. Confirmed by volume 50%+ above average.
# ════════════════════════════════════════════════════════════════════════════

def livermore_pivotal_point(symbol: str, direction: str,
                             df_5m=None, volume_ratio: float = 1.0) -> Tuple[float, str]:
    """
    Livermore: are we at a pivotal breakout point?
    Score +10 for textbook pivot. +6 for partial pivot. -5 for chasing.
    """
    try:
        df = _get_daily_df(symbol)
        if df is None or len(df) < 20:
            return 0.0, ""

        closes  = df["close"].values
        highs   = df["high"].values
        volumes = df["volume"].values

        # Resistance = highest close in prior 10–20 bars (before last 2)
        recent_resistance = float(np.max(highs[-20:-2]))
        current_price     = float(closes[-1])
        avg_vol           = float(np.mean(volumes[-20:-2]))
        cur_vol           = float(volumes[-1])

        # Has price been consolidating (range < 8%) for at least 5 sessions?
        box_range_pct = (np.max(highs[-10:-2]) - np.min(df["low"].values[-10:-2])) / recent_resistance * 100
        consolidating = box_range_pct < 8.0

        if direction in ("LONG", "BUY"):
            just_broke = current_price > recent_resistance * 1.002   # 0.2% above resistance
            vol_surge  = cur_vol >= avg_vol * 1.5
            if just_broke and vol_surge and consolidating:
                return 10.0, f"LIVERMORE_PIVOT: {symbol} broke ${recent_resistance:.2f} pivot w/ {cur_vol/avg_vol:.1f}x vol"
            if just_broke and consolidating:
                return 6.0, f"LIVERMORE_PIVOT(weak vol): broke ${recent_resistance:.2f}"
            if current_price > recent_resistance * 1.05:
                return -5.0, f"LIVERMORE: chasing — {(current_price/recent_resistance-1)*100:.1f}% above pivot"
        else:  # SHORT
            lows = df["low"].values
            recent_support = float(np.min(lows[-20:-2]))
            just_broke     = current_price < recent_support * 0.998
            vol_surge      = cur_vol >= avg_vol * 1.5
            if just_broke and vol_surge and consolidating:
                return 10.0, f"LIVERMORE_PIVOT(SHORT): broke support ${recent_support:.2f}"
            if just_broke:
                return 6.0, f"LIVERMORE_PIVOT(SHORT): below ${recent_support:.2f}"

        return 0.0, ""
    except Exception as exc:
        logger.debug(f"livermore_pivotal_point({symbol}): {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════════════════════════════
# FRAMEWORK 2 — MARK MINERVINI  (Trade Like a Stock Market Wizard, 2013)
#
# Core rules:
#  "Only buy in a Stage 2 uptrend. Never buy Stage 1, 3, or 4."
#  "VCP (Volatility Contraction Pattern) = the highest-probability setup."
#  "The trend template: price above 50d MA, 50d above 150d, 150d above 200d."
#  "Volume dries up during the handle, surges on the breakout."
#  "Relative strength line must confirm."
# ════════════════════════════════════════════════════════════════════════════

def minervini_sepa(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Minervini SEPA (Specific Entry Point Analysis):
      - Trend template (MA structure)
      - VCP detection (volatility contraction)
    Score range: -8 to +14.
    """
    try:
        df = _get_daily_df(symbol)
        if df is None or len(df) < 60:
            return 0.0, ""

        closes = df["close"].values
        current = float(closes[-1])

        # ── Trend template ──────────────────────────────────────────────
        ma50  = float(np.mean(closes[-50:]))
        ma150 = float(np.mean(closes[-150:])) if len(closes) >= 150 else ma50
        ma200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else ma150

        # 200-day MA trending up (compare now vs 20 sessions ago)
        ma200_20 = float(np.mean(closes[-220:-20])) if len(closes) >= 220 else 0.0
        ma200_trending = ma200 > ma200_20 * 0.998 if ma200_20 > 0 else True

        template_ok = (current > ma50 > ma150 > ma200 and ma200_trending)

        if direction in ("LONG", "BUY") and not template_ok:
            return -8.0, f"MINERVINI: trend template FAIL (price<MA50 or MAs inverted)"

        # ── VCP: volatility contraction over last 3 consolidations ─────
        # Measure: rolling 5-day range pct, must contract across 3 periods
        ranges = []
        for i in range(3):
            start = -(15 - i*5)
            end   = -(10 - i*5) if i < 2 else -1
            h = float(np.max(df["high"].values[start:end]))
            l = float(np.min(df["low"].values[start:end]))
            ranges.append((h - l) / max(l, 0.01) * 100.0)

        vcp = (len(ranges) == 3 and
               ranges[0] > ranges[1] > ranges[2] and
               ranges[2] < 3.0)   # final contraction < 3% range = tight

        score = 0.0
        reasons = []

        if direction in ("LONG", "BUY"):
            if template_ok:
                score += 6.0
                reasons.append(f"SEPA_TEMPLATE({current:.0f}>MA50={ma50:.0f}>MA200={ma200:.0f})")
            if vcp:
                score += 8.0
                reasons.append(f"VCP(ranges:{ranges[0]:.1f}%→{ranges[1]:.1f}%→{ranges[2]:.1f}%)")
            elif ranges[-1] < 5.0:
                score += 3.0
                reasons.append(f"VCP_PARTIAL(range={ranges[-1]:.1f}%)")
        else:  # SHORT — inverted template
            inverted = (current < ma50 < ma150)
            if inverted:
                score += 6.0
                reasons.append(f"SEPA_SHORT(price<MA50<MA150)")

        return score, " | ".join(reasons)
    except Exception as exc:
        logger.debug(f"minervini_sepa({symbol}): {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════════════════════════════
# FRAMEWORK 3 — WILLIAM O'NEIL  (How to Make Money in Stocks, 1988)
#
# CANSLIM encoded as price/volume analysis (no fundamental data needed):
#  N: New highs — stock at or near 52-week high (price discovery)
#  S: Supply/demand — volume demand > supply on up days
#  L: Leader — RS in top 20% vs S&P 500
#  I: Institutional — large bars with volume (accumulation)
#  M: Market — SPY trend (handled elsewhere)
#
# Chart patterns (O'Neil's highest-probability):
#  Cup & Handle: U-shape over 6-65 weeks, handle ≤ 15% correction
#  Flat Base: tight 5-15% consolidation for 5+ weeks
#  Double Bottom: two lows within 5%, second slightly higher
# ════════════════════════════════════════════════════════════════════════════

def oneil_canslim(symbol: str, direction: str,
                  volume_ratio: float = 1.0) -> Tuple[float, str]:
    """
    O'Neil CANSLIM + chart pattern scoring.
    Score range: 0 to +12.
    """
    try:
        df = _get_daily_df(symbol)
        if df is None or len(df) < 30:
            return 0.0, ""

        closes = df["close"].values
        highs  = df["high"].values
        volumes = df["volume"].values
        current = float(closes[-1])

        score = 0.0
        reasons = []

        # ── N: near 52-week high ────────────────────────────────────────
        high_52w = float(np.max(highs[-252:])) if len(highs) >= 252 else float(np.max(highs))
        pct_from_high = (high_52w - current) / high_52w * 100.0
        if direction in ("LONG", "BUY"):
            if pct_from_high <= 5.0:    # within 5% of 52-week high
                score += 5.0
                reasons.append(f"ONEIL_N: {pct_from_high:.1f}% from 52wk high")
            elif pct_from_high <= 15.0:
                score += 2.0
                reasons.append(f"ONEIL_N: {pct_from_high:.1f}% from 52wk high")

        # ── S: volume demand — up-day vol vs down-day vol ───────────────
        up_vols   = [v for c, pc, v in zip(closes[-20:], closes[-21:-1], volumes[-20:]) if c > pc]
        down_vols = [v for c, pc, v in zip(closes[-20:], closes[-21:-1], volumes[-20:]) if c <= pc]
        if up_vols and down_vols:
            demand_ratio = np.mean(up_vols) / max(np.mean(down_vols), 1)
            if demand_ratio >= 1.4:
                score += 4.0
                reasons.append(f"ONEIL_S: demand/supply={demand_ratio:.1f}x")
            elif demand_ratio >= 1.15:
                score += 2.0

        # ── Cup & Handle detection (simplified) ──────────────────────────
        if len(closes) >= 40:
            mid_low  = float(np.min(closes[-30:-10]))
            cup_high = max(float(closes[-40]), float(closes[-10]))
            cup_depth = (cup_high - mid_low) / cup_high * 100.0
            # Handle correction (last 10 sessions)
            handle_high = float(np.max(highs[-10:]))
            handle_low  = float(np.min(df["low"].values[-10:]))
            handle_correction = (handle_high - handle_low) / handle_high * 100.0
            if 10.0 <= cup_depth <= 40.0 and handle_correction <= 15.0:
                score += 3.0
                reasons.append(f"ONEIL_CUP: depth={cup_depth:.0f}% handle={handle_correction:.1f}%")

        return score, " | ".join(reasons)
    except Exception as exc:
        logger.debug(f"oneil_canslim({symbol}): {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════════════════════════════
# FRAMEWORK 4 — NICOLAS DARVAS  (How I Made $2,000,000, 1960)
#
# Darvas Box Theory:
#  "A stock makes a new 52-week high, then consolidates in a box."
#  "When price breaks above the box top with volume → buy."
#  "Never buy a stock below its box. Never sell inside a box."
#  "The box ceiling becomes the stop-loss floor."
# ════════════════════════════════════════════════════════════════════════════

def darvas_box(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Darvas box breakout detection.
    Score +10 for clean Darvas breakout with volume. +5 for partial setup.
    """
    try:
        df = _get_daily_df(symbol)
        if df is None or len(df) < 25:
            return 0.0, ""

        closes  = df["close"].values
        highs   = df["high"].values
        lows    = df["low"].values
        volumes = df["volume"].values
        current = float(closes[-1])

        # New 52-week high in last 20 sessions (the catalyst that starts a Darvas setup)
        high_52w   = float(np.max(highs[-252:])) if len(highs) >= 252 else float(np.max(highs))
        new_52w_hi = float(np.max(highs[-20:])) >= high_52w * 0.98

        # Box: last 10 sessions had narrow range (< 8%)
        box_hi = float(np.max(highs[-12:-2]))
        box_lo = float(np.min(lows[-12:-2]))
        box_range_pct = (box_hi - box_lo) / box_hi * 100.0
        tight_box = box_range_pct < 8.0

        # Breakout: today above the box ceiling
        avg_vol    = float(np.mean(volumes[-20:-2]))
        today_vol  = float(volumes[-1])
        vol_surge  = today_vol >= avg_vol * 1.3

        if direction in ("LONG", "BUY"):
            if new_52w_hi and tight_box and current > box_hi * 1.001:
                if vol_surge:
                    return 10.0, f"DARVAS_BOX: ${box_lo:.2f}–${box_hi:.2f} breakout w/ vol {today_vol/avg_vol:.1f}x"
                return 5.0, f"DARVAS_BOX(low vol): ${box_lo:.2f}–${box_hi:.2f} breakout"

        return 0.0, ""
    except Exception as exc:
        logger.debug(f"darvas_box({symbol}): {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════════════════════════════
# FRAMEWORK 5 — RICHARD WYCKOFF  (The Wyckoff Method, 1931)
#
# Wyckoff's three laws:
#  Law 1: Supply & Demand — price rises when demand > supply
#  Law 2: Cause & Effect — accumulation → markup
#  Law 3: Effort vs Result — volume tells the true story
#
# Key setups:
#  Spring: shakeout below support → recapture → volume expansion (ultra-bullish)
#  UTAD: upthrust above resistance → reverse → volume (ultra-bearish)
#  Accumulation: volume surges on down days slow down → institutions accumulating
# ════════════════════════════════════════════════════════════════════════════

def wyckoff_analysis(symbol: str, direction: str,
                     df_5m=None, atr: float = 0.0) -> Tuple[float, str]:
    """
    Wyckoff: detect spring (ultra-bullish) or UTAD (ultra-bearish) + accumulation.
    Score range: -5 to +14.
    """
    try:
        df = df_5m  # use 5m data for intraday spring detection
        if df is None or len(df) < 20:
            df = _get_daily_df(symbol)
            if df is None or len(df) < 20:
                return 0.0, ""

        closes  = df["close"].values
        highs   = df["high"].values
        lows    = df["low"].values
        volumes = df["volume"].values

        score   = 0.0
        reasons = []

        # ── Spring detection: tested below recent support, now recaptured ─
        support = float(np.min(lows[-15:-3]))
        spring_test  = float(np.min(lows[-3:])) < support   # dipped below
        recaptured   = float(closes[-1]) > support           # now back above
        spring_vol   = float(volumes[-1]) > float(np.mean(volumes[-15:])) * 1.2

        if direction in ("LONG", "BUY") and spring_test and recaptured:
            if spring_vol:
                score += 14.0
                reasons.append(f"WYCKOFF_SPRING: dipped ${float(np.min(lows[-3:])):.2f} < support ${support:.2f}, recaptured w/ vol")
            else:
                score += 8.0
                reasons.append(f"WYCKOFF_SPRING(low vol): support recapture")

        # ── UTAD (Upthrust After Distribution) for SHORT ──────────────
        resistance = float(np.max(highs[-15:-3]))
        if direction == "SHORT":
            utad = (float(np.max(highs[-3:])) > resistance and
                    float(closes[-1]) < resistance)
            if utad and float(volumes[-1]) > float(np.mean(volumes[-15:])) * 1.2:
                score += 12.0
                reasons.append(f"WYCKOFF_UTAD: spike above ${resistance:.2f} then rejected")

        # ── Accumulation: volume drying up on down days ───────────────
        down_vols = [v for c, pc, v in zip(closes[-10:], closes[-11:-1], volumes[-10:]) if c < pc]
        up_vols   = [v for c, pc, v in zip(closes[-10:], closes[-11:-1], volumes[-10:]) if c >= pc]
        if down_vols and up_vols:
            avg_down = float(np.mean(down_vols))
            avg_up   = float(np.mean(up_vols))
            if direction in ("LONG", "BUY") and avg_up > avg_down * 1.4:
                score += 4.0
                reasons.append(f"WYCKOFF_ACCUM: up-vol {avg_up:.0f} >> down-vol {avg_down:.0f}")

        return score, " | ".join(reasons)
    except Exception as exc:
        logger.debug(f"wyckoff_analysis({symbol}): {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════════════════════════════
# FRAMEWORK 6 — STAN WEINSTEIN  (Secrets for Profiting, 1988)
#
# Stage Analysis (the most important concept in Weinstein):
#  Stage 1: Basing — stock flat. NEVER buy. Patient wait.
#  Stage 2: Advancing — price above rising 30-week MA. BUY here.
#  Stage 3: Topping — price churning at resistance. Reduce / exit.
#  Stage 4: Declining — below falling 30-week MA. SHORT here.
#
# "The key is not just where the price is, but what stage it's in."
# 30-week MA (150-day) slope is the single most important indicator.
# ════════════════════════════════════════════════════════════════════════════

def weinstein_stage(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Weinstein stage analysis. Only Stage 2 LONG or Stage 4 SHORT gets bonus.
    Score: +8 if correct stage, -10 if wrong stage.
    """
    try:
        df = _get_daily_df(symbol)
        if df is None or len(df) < 60:
            return 0.0, ""

        closes = df["close"].values
        current = float(closes[-1])
        ma150   = float(np.mean(closes[-150:])) if len(closes) >= 150 else float(np.mean(closes[-60:]))
        ma150_4w= float(np.mean(closes[-170:-20])) if len(closes) >= 170 else 0.0

        ma_rising  = ma150 > ma150_4w * 0.995 if ma150_4w > 0 else True
        ma_falling = ma150 < ma150_4w * 1.005 if ma150_4w > 0 else False

        if direction in ("LONG", "BUY"):
            if current > ma150 and ma_rising:
                return 8.0, f"WEINSTEIN_STAGE2: price({current:.0f}) > rising MA150({ma150:.0f})"
            if current < ma150 and ma_falling:
                return -10.0, f"WEINSTEIN: STAGE 4 declining — no LONG in Stage 4"
            if current < ma150:
                return -4.0, f"WEINSTEIN: below MA150 — not in Stage 2"
        else:  # SHORT
            if current < ma150 and ma_falling:
                return 8.0, f"WEINSTEIN_STAGE4: price({current:.0f}) < falling MA150({ma150:.0f})"
            if current > ma150 and ma_rising:
                return -10.0, f"WEINSTEIN: STAGE 2 rising — no SHORT in Stage 2"

        return 0.0, ""
    except Exception as exc:
        logger.debug(f"weinstein_stage({symbol}): {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════════════════════════════
# FRAMEWORK 7 — TURTLE TRADING RULES  (Dennis/Eckhardt, 1983)
#
# Original rules encoded:
#  System 1: 20-day breakout entry, 10-day exit
#  System 2: 55-day breakout entry, 20-day exit
#  Sizing: 1% risk per 1N (1 ATR) move
#  Adding on: pyramid up to 4 units in trending markets
#  Skip: if the last signal was a winner (avoid double-dip in same trend)
#
# "The trend is your friend until it bends."
# ════════════════════════════════════════════════════════════════════════════

def turtle_breakout(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Turtle 20-day and 55-day breakout detection.
    Score: +8 for 20d breakout, +12 for 55d breakout, -6 if false breakout territory.
    """
    try:
        df = _get_daily_df(symbol)
        if df is None or len(df) < 60:
            return 0.0, ""

        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        current = float(closes[-1])

        # 20-day and 55-day highs (exclude today)
        high_20 = float(np.max(highs[-21:-1]))
        high_55 = float(np.max(highs[-56:-1])) if len(highs) >= 56 else high_20
        low_10  = float(np.min(lows[-11:-1]))

        if direction in ("LONG", "BUY"):
            if current > high_55:
                return 12.0, f"TURTLE_SYS2: 55-day breakout ${high_55:.2f}→${current:.2f}"
            if current > high_20:
                return 8.0, f"TURTLE_SYS1: 20-day breakout ${high_20:.2f}→${current:.2f}"
            if current < low_10:
                return -6.0, f"TURTLE: price at 10-day low — exit signal, not entry"
        else:  # SHORT
            low_20  = float(np.min(lows[-21:-1]))
            low_55  = float(np.min(lows[-56:-1])) if len(lows) >= 56 else low_20
            high_10 = float(np.max(highs[-11:-1]))
            if current < low_55:
                return 12.0, f"TURTLE_SYS2(SHORT): 55-day breakdown ${low_55:.2f}"
            if current < low_20:
                return 8.0, f"TURTLE_SYS1(SHORT): 20-day breakdown ${low_20:.2f}"
            if current > high_10:
                return -6.0, f"TURTLE(SHORT): at 10-day high — exit, not entry"

        return 0.0, ""
    except Exception as exc:
        logger.debug(f"turtle_breakout({symbol}): {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════════════════════════════
# FRAMEWORK 8 — GEORGE SOROS  (The Alchemy of Finance, 1987)
#
# Theory of Reflexivity:
#  "Market prices influence the fundamentals they are supposed to reflect."
#  Boom: rising prices → positive narrative → more buying → higher prices (self-reinforcing)
#  Bust: falling prices → negative narrative → more selling → lower prices
#
# Operational translation:
#  A reflexive setup = trend + volume acceleration + narrative momentum
#  When all three align: the move will go further and faster than expected
#  Entry signal: trend acceleration + volume surge (institutions adding)
# ════════════════════════════════════════════════════════════════════════════

def soros_reflexivity(symbol: str, direction: str,
                      df_5m=None, volume_ratio: float = 1.0,
                      rsi: float = 50.0) -> Tuple[float, str]:
    """
    Soros reflexivity: accelerating trend + volume surge = self-reinforcing move.
    Score: +10 for full reflexive setup, +5 partial.
    """
    try:
        df = df_5m
        if df is None or len(df) < 20:
            df = _get_daily_df(symbol)
            if df is None or len(df) < 20:
                return 0.0, ""

        closes  = df["close"].values
        volumes = df["volume"].values

        # ── Trend acceleration: is momentum increasing? ──────────────
        # Compare 5-period return of recent 5 bars vs prior 5 bars
        recent_ret = float(closes[-1] - closes[-6]) / max(float(closes[-6]), 0.01)
        prior_ret  = float(closes[-6] - closes[-11]) / max(float(closes[-11]), 0.01) if len(closes) >= 11 else 0.0
        accelerating = (
            (direction in ("LONG","BUY") and recent_ret > prior_ret and recent_ret > 0.003)
            or
            (direction == "SHORT" and recent_ret < prior_ret and recent_ret < -0.003)
        )

        # ── Volume surging (institutional entry) ────────────────────
        avg_vol = float(np.mean(volumes[-20:-5]))
        recent_vol = float(np.mean(volumes[-5:]))
        vol_acc = recent_vol >= avg_vol * 1.5

        # ── RSI in momentum zone (not overbought/oversold) ──────────
        rsi_momentum = (50 <= rsi <= 72) if direction in ("LONG","BUY") else (28 <= rsi <= 50)

        if accelerating and vol_acc and rsi_momentum:
            return 10.0, f"SOROS_REFLEXIVITY: trend accel ({recent_ret*100:+.1f}% vs {prior_ret*100:+.1f}%) + vol {recent_vol/avg_vol:.1f}x"
        if accelerating and vol_acc:
            return 5.0, f"SOROS: accel + vol surge ({recent_vol/avg_vol:.1f}x)"
        if accelerating and rsi_momentum:
            return 3.0, f"SOROS: trend acceleration in RSI momentum zone"

        return 0.0, ""
    except Exception as exc:
        logger.debug(f"soros_reflexivity({symbol}): {exc}")
        return 0.0, ""


# ════════════════════════════════════════════════════════════════════════════
# MASTER KNOWLEDGE SCORER
# Combines all 8 frameworks into a single score delta.
# Called once per signal in signal_generator.generate_signal().
# ════════════════════════════════════════════════════════════════════════════

def compute_master_knowledge_score(
    symbol:       str,
    direction:    str,
    df_5m=None,
    volume_ratio: float = 1.0,
    rsi:          float = 50.0,
    atr:          float = 0.0,
) -> Tuple[float, List[str]]:
    """
    Run all 8 frameworks and return (total_delta, [reasons]).

    Total delta range: approximately -30 to +66.
    In practice, strong setups score +20 to +35.
    Weak/wrong-side setups score -10 to -20.

    Capped at +40 / -20 to prevent any single day's framework from
    dominating the 100-pt signal score.
    """
    total  = 0.0
    active = []

    # Ordered by reliability / execution priority
    frameworks = [
        ("WEINSTEIN",   weinstein_stage,          (symbol, direction)),
        ("MINERVINI",   minervini_sepa,            (symbol, direction)),
        ("LIVERMORE",   livermore_pivotal_point,   (symbol, direction, df_5m, volume_ratio)),
        ("TURTLE",      turtle_breakout,           (symbol, direction)),
        ("DARVAS",      darvas_box,                (symbol, direction)),
        ("ONEIL",       oneil_canslim,             (symbol, direction, volume_ratio)),
        ("WYCKOFF",     wyckoff_analysis,          (symbol, direction, df_5m, atr)),
        ("SOROS",       soros_reflexivity,         (symbol, direction, df_5m, volume_ratio, rsi)),
    ]

    for name, fn, args in frameworks:
        try:
            delta, reason = fn(*args)
            if delta != 0.0:
                total += delta
                if reason:
                    active.append(reason)
        except Exception as exc:
            logger.debug(f"[suppressed] {name}({symbol}): {exc}")

    # Cap to avoid runaway scores
    total = max(-20.0, min(40.0, total))
    return round(total, 1), active
