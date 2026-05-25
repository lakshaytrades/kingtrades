"""
pead_scorer.py — Post-Earnings Announcement Drift (PEAD) Scorer

PEAD is one of the most studied and persistent market anomalies:
  - After a POSITIVE earnings surprise → stock drifts UP for 3-60 days
  - After a NEGATIVE earnings surprise → stock drifts DOWN for 3-60 days
  - Largest drift in days 3-21 post-earnings (initial overreaction settles)
  - 80%+ hit rate on direction when EPS surprise > 5%

Research basis:
  Ball & Brown (1968), Fama et al., Bernard & Thomas (1989)
  "SUE (Standardized Unexpected Earnings) effect" — still active in 2024-2025

Why it boosts top-1% returns:
  - Post-earnings momentum stocks trend for WEEKS, not hours
  - Intraday momentum aligns with 3-week drift = compounding edge
  - Most retail traders don't know this — they think earnings is "over"

Score logic:
  - Check if stock reported earnings 3-21 days ago
  - Get EPS surprise % from yfinance
  - Compute PEAD boost based on:
      * Surprise magnitude (>10% = maximum boost)
      * Recency (days 3-7 post = max, days 14-21 = fading)
      * Direction alignment (LONG + beat or SHORT + miss)
      * Guidance sentiment (if available)

Score range:
  +12 max boost: 3-7 days post, >10% EPS beat, LONG signal
  +8  moderate:  8-14 days post, 5-10% beat
  +4  light:     14-21 days post, any beat
   0  neutral:   >21 days or within 3 days (initial gap volatility)
  -8  penalty:   LONG signal but MISSED earnings (drift down expected)

Cache: 6 hours (earnings don't change intraday)
"""

import logging
import time
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_PEAD_CACHE: Dict[str, dict] = {}
_PEAD_CACHE_TTL = 21600   # 6 hours


def _get_earnings_data(symbol: str) -> dict:
    """
    Fetch most recent earnings result from yfinance.
    Returns:
      {
        "earnings_date": date or None,
        "eps_surprise_pct": float (>0 = beat, <0 = miss),
        "beat": bool,
        "days_since": int,
      }
    """
    cached = _PEAD_CACHE.get(symbol)
    if cached and (time.monotonic() - cached.get("_ts", 0)) < _PEAD_CACHE_TTL:
        return cached

    result = {
        "earnings_date": None,
        "eps_surprise_pct": 0.0,
        "beat": False,
        "days_since": 999,
        "_ts": time.monotonic(),
    }

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        # Method 1: earnings_history (quarterly)
        hist = ticker.earnings_history
        if hist is not None and not hist.empty:
            # Most recent quarter
            recent = hist.sort_index(ascending=False).iloc[0]
            eps_est    = float(recent.get("epsEstimate", 0) or 0)
            eps_actual = float(recent.get("epsActual", 0) or 0)

            if eps_est != 0:
                surprise_pct = (eps_actual - eps_est) / abs(eps_est) * 100
            elif eps_actual > 0:
                surprise_pct = 10.0   # beat but no estimate available
            else:
                surprise_pct = 0.0

            # Get earnings date from the index
            try:
                if hasattr(hist.index, 'date'):
                    e_date = hist.index.sort_values(ascending=False)[0].date()
                else:
                    e_date = date.today() - timedelta(days=30)
            except Exception:
                e_date = date.today() - timedelta(days=30)

            days_since = (date.today() - e_date).days if e_date else 999

            result.update({
                "earnings_date":    e_date,
                "eps_surprise_pct": round(surprise_pct, 1),
                "beat":             surprise_pct > 0,
                "days_since":       days_since,
                "_ts":              time.monotonic(),
            })

        # Method 2: calendar (next earnings) — look at recent quarter data
        if result["days_since"] == 999:
            cal = ticker.calendar
            if cal is not None:
                try:
                    # Calendar shows next earnings — estimate last one
                    next_e = cal.get("Earnings Date", [None])[0] if isinstance(cal, dict) else None
                    if next_e:
                        # Approximate last earnings as ~91 days before next
                        from pandas import Timestamp
                        if isinstance(next_e, Timestamp):
                            next_date  = next_e.date()
                            last_date  = next_date - timedelta(days=91)
                            days_since = (date.today() - last_date).days
                            if 0 <= days_since <= 60:
                                result["days_since"] = days_since
                except Exception:
                    pass

    except Exception as e:
        logger.debug(f"pead_scorer._get_earnings_data({symbol}): {e}")

    _PEAD_CACHE[symbol] = result
    return result


def get_pead_score_delta(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Compute PEAD score adjustment for a signal.

    Args:
        symbol:    Stock symbol
        direction: "LONG" or "SHORT"

    Returns:
        (score_delta, reason)
        score_delta: [-8, +12] — add to filter_result.final_score
    """
    try:
        data = _get_earnings_data(symbol)
        days    = data["days_since"]
        surprise = data["eps_surprise_pct"]
        beat    = data["beat"]

        # PEAD only active days 3-21 post-earnings
        if days < 3 or days > 21:
            return 0.0, ""

        # Days-based recency multiplier: peak at days 3-7, fades to 14-21
        if 3 <= days <= 7:
            recency = 1.0
        elif 8 <= days <= 14:
            recency = 0.65
        else:
            recency = 0.35

        # Surprise magnitude multiplier
        abs_surprise = abs(surprise)
        if abs_surprise >= 10.0:
            surprise_mult = 1.0
        elif abs_surprise >= 5.0:
            surprise_mult = 0.7
        elif abs_surprise >= 2.0:
            surprise_mult = 0.4
        else:
            surprise_mult = 0.15

        base_boost = 12.0   # max boost in pts

        if direction == "LONG" and beat:
            delta  = round(base_boost * recency * surprise_mult, 1)
            reason = (
                f"PEAD TAILWIND (day {days} post-earnings, "
                f"+{surprise:.0f}% EPS beat): drift UP expected"
            )
        elif direction == "SHORT" and not beat:
            # Miss + SHORT = drift down → confirms short
            delta  = round(base_boost * recency * surprise_mult * 0.8, 1)
            reason = (
                f"PEAD CONFIRMS SHORT (day {days} post-miss, "
                f"{surprise:.0f}% EPS miss): drift DOWN expected"
            )
        elif direction == "LONG" and not beat:
            # Beat required for LONG momentum, but missed → penalty
            delta  = round(-8.0 * recency * surprise_mult, 1)
            reason = (
                f"PEAD HEADWIND (day {days} post-miss, "
                f"{surprise:.0f}% EPS miss): drift down, avoid LONG"
            )
        elif direction == "SHORT" and beat:
            # Beaten earnings → drift up → avoid short
            delta  = round(-6.0 * recency * surprise_mult, 1)
            reason = (
                f"PEAD WARNING (day {days} post-beat, "
                f"+{surprise:.0f}% EPS beat): drift up, risky SHORT"
            )
        else:
            return 0.0, ""

        if abs(delta) >= 2.0:
            logger.debug(
                f"pead_scorer: {symbol} ({direction}) days_since={days} "
                f"surprise={surprise:+.0f}% → Δscore={delta:+.1f}"
            )
        return delta, reason

    except Exception as e:
        logger.debug(f"pead_scorer.get_pead_score_delta({symbol}): {e}")
        return 0.0, ""
