"""
wyckoff_vsa_india.py — Wyckoff Volume Spread Analysis Engine
Richard Wyckoff's methodology: price spread × volume tells you who is in control.

The four Wyckoff phases:
  ACCUMULATION → Spring → Markup (LONG — smart money accumulating)
  DISTRIBUTION → UTAD  → Markdown (SHORT — smart money distributing)

VSA signals mapped to NSE 5-min bars:
  Stopping Volume  : wide spread DOWN bar with very high volume + close in upper 30%
  No Supply        : narrow spread DOWN bar with low volume (supply drying up)
  Effort vs Result : high volume with minimal price movement (absorption)
  Sign of Strength : wide spread UP bar + high volume + close near high
  Sign of Weakness : wide spread DOWN bar + high volume + close near low
  Spring           : dip below support then close above = manipulation (very bullish)
  Upthrust         : push above resistance then close below = distribution (very bearish)

Score impact: -18 to +20 (most powerful signal in the stack)
"""
import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("wyckoff_vsa")


def _percentile_rank(series: pd.Series, value: float) -> float:
    """Return percentile rank (0-100) of value within series."""
    if len(series) == 0:
        return 50.0
    return float((series < value).sum() / len(series) * 100)


def analyze_wyckoff(df: pd.DataFrame, lookback: int = 30) -> dict:
    """
    Analyze last N bars for Wyckoff VSA signals.
    Returns:
      {
        "phase": "ACCUMULATION"/"DISTRIBUTION"/"NEUTRAL",
        "signal": str,
        "score_adj": int,
        "strength": "STRONG"/"MODERATE"/"WEAK",
        "details": str,
      }
    Fail-open: returns NEUTRAL, score_adj=0.
    """
    _empty = {"phase": "NEUTRAL", "signal": "NONE", "score_adj": 0,
              "strength": "WEAK", "details": ""}
    try:
        if df is None or len(df) < 20:
            return _empty

        df = df.tail(lookback).copy()
        df = df.reset_index(drop=True)

        close  = df["close"].values.astype(float)
        high   = df["high"].values.astype(float)
        low    = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float)
        open_  = df["open"].values.astype(float)

        # Spread (range) of each bar
        spread = high - low
        vol_series = pd.Series(volume)
        spread_series = pd.Series(spread)
        avg_vol    = vol_series.rolling(20).mean().iloc[-1]
        avg_spread = spread_series.rolling(20).mean().iloc[-1]

        if avg_vol <= 0 or avg_spread <= 0:
            return _empty

        # Focus on last 5 bars for signal
        n = len(df)
        last_3 = slice(max(0, n - 3), n)
        last_5 = slice(max(0, n - 5), n)

        signals = []
        score_adj = 0

        # ── Bar-by-bar VSA analysis (last 5 bars) ────────────────────────────

        for i in range(max(0, n - 5), n):
            bar_vol  = volume[i]
            bar_spr  = spread[i]
            bar_cls  = close[i]
            bar_low  = low[i]
            bar_high = high[i]
            bar_opn  = open_[i]

            vol_rank    = _percentile_rank(vol_series, bar_vol)
            spread_rank = _percentile_rank(spread_series, bar_spr)
            close_pos   = (bar_cls - bar_low) / bar_spr if bar_spr > 0 else 0.5
            is_up_bar   = bar_cls > bar_opn
            is_down_bar = bar_cls < bar_opn

            # 1. Stopping Volume (strong LONG signal)
            # Wide down bar + ultra high volume + closes in upper 30%
            if (is_down_bar and vol_rank >= 85 and spread_rank >= 60
                    and close_pos >= 0.70):
                signals.append("STOPPING_VOLUME")
                score_adj += 15

            # 2. Sign of Strength (strong LONG continuation)
            # Wide up bar + high volume + close near high
            elif (is_up_bar and vol_rank >= 75 and spread_rank >= 55
                  and close_pos >= 0.75):
                signals.append("SIGN_OF_STRENGTH")
                score_adj += 12

            # 3. No Supply (moderate LONG — shorts giving up)
            # Narrow down bar + low volume + close not near low
            elif (is_down_bar and vol_rank <= 30 and spread_rank <= 35
                  and close_pos >= 0.55):
                signals.append("NO_SUPPLY")
                score_adj += 8

            # 4. Sign of Weakness (strong SHORT signal)
            # Wide down bar + high volume + close near low
            elif (is_down_bar and vol_rank >= 75 and spread_rank >= 55
                  and close_pos <= 0.30):
                signals.append("SIGN_OF_WEAKNESS")
                score_adj -= 12

            # 5. No Demand (moderate SHORT — buyers not committing)
            # Narrow up bar + low volume + close in upper half but weak
            elif (is_up_bar and vol_rank <= 30 and spread_rank <= 35
                  and close_pos <= 0.65):
                signals.append("NO_DEMAND")
                score_adj -= 6

            # 6. Effort vs Result — absorption (market maker absorbing)
            # High volume + minimal price movement
            elif (vol_rank >= 80 and spread_rank <= 25):
                signals.append("ABSORPTION")
                # Neutral but signals potential reversal
                score_adj += 2 if is_up_bar else -2

        # ── Spring / Upthrust detection (multi-bar patterns) ─────────────────

        if n >= 10:
            # Spring: 3 bars ago made a new low below prior support,
            # then recovered and closed above the prior support level
            recent_lows = low[max(0, n - 10):n - 2]
            if len(recent_lows) > 0:
                support_level = np.percentile(recent_lows, 15)
                # Check if price recently dipped below support then recovered
                two_bars_ago_low  = low[n - 3] if n >= 3 else close[-1]
                yesterday_close   = close[n - 2] if n >= 2 else close[-1]
                today_close       = close[n - 1]

                if (two_bars_ago_low < support_level
                        and yesterday_close > support_level
                        and today_close > support_level
                        and volume[n - 3] > avg_vol * 1.3):
                    signals.append("SPRING")
                    score_adj += 20   # highest conviction Wyckoff signal

                # Upthrust: price briefly above resistance then fails back
                recent_highs = high[max(0, n - 10):n - 2]
                if len(recent_highs) > 0:
                    resist_level = np.percentile(recent_highs, 85)
                    two_bars_ago_high = high[n - 3] if n >= 3 else close[-1]
                    if (two_bars_ago_high > resist_level
                            and yesterday_close < resist_level
                            and today_close < resist_level
                            and volume[n - 3] > avg_vol * 1.3):
                        signals.append("UPTHRUST")
                        score_adj -= 18   # strongest distribution signal

        # ── Composite phase determination ─────────────────────────────────────

        accum_signals = {"STOPPING_VOLUME", "SIGN_OF_STRENGTH", "NO_SUPPLY", "SPRING"}
        dist_signals  = {"SIGN_OF_WEAKNESS", "NO_DEMAND", "UPTHRUST"}

        accum_count = sum(1 for s in signals if s in accum_signals)
        dist_count  = sum(1 for s in signals if s in dist_signals)

        if accum_count > dist_count:
            phase = "ACCUMULATION"
        elif dist_count > accum_count:
            phase = "DISTRIBUTION"
        else:
            phase = "NEUTRAL"

        strength = ("STRONG" if abs(score_adj) >= 15
                    else "MODERATE" if abs(score_adj) >= 8
                    else "WEAK")

        # Cap at reasonable bounds
        score_adj = max(-18, min(20, score_adj))

        return {
            "phase": phase,
            "signal": signals[-1] if signals else "NONE",
            "all_signals": signals,
            "score_adj": score_adj,
            "strength": strength,
            "details": ", ".join(signals[-3:]) if signals else "",
        }

    except Exception as e:
        logger.debug(f"analyze_wyckoff: {e}")
        return _empty


def get_wyckoff_score(df: pd.DataFrame, direction: str) -> Tuple[int, str]:
    """
    Return (score_adj, reason) for the given direction based on Wyckoff VSA.
    LONG: positive for ACCUMULATION, negative for DISTRIBUTION
    SHORT: inverse
    """
    try:
        vsa = analyze_wyckoff(df)
        adj = vsa["score_adj"]
        sig = vsa["signal"]
        phase = vsa["phase"]

        # Invert for SHORT
        if direction == "SHORT":
            adj = -adj

        if adj == 0:
            return (0, "")

        reason = f"VSA_{sig}({phase})"
        return (adj, reason)

    except Exception as e:
        logger.debug(f"get_wyckoff_score: {e}")
        return (0, "")
