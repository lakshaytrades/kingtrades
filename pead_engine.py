"""
pead_engine.py — Post-Earnings Announcement Drift Engine (v29.0)

PEAD (Post-Earnings Announcement Drift) is one of the most durable anomalies
in academic finance (Ball & Brown 1968, Bernard & Thomas 1989).

After an earnings surprise:
  - BEAT (EPS > estimate): stock drifts UP for 2-5 more trading days
  - MISS (EPS < estimate): stock drifts DOWN for 2-5 more trading days

The market systematically under-reacts to earnings news.
This is the only anomaly documented in every decade since 1968.

Implementation:
  - Use yfinance earnings calendar (free) to detect recent surprises
  - Compute earnings surprise magnitude (actual vs estimated EPS)
  - Score based on direction + recency + magnitude

PEAD entry rules:
  - Day 2-3 after earnings BEAT: enter LONG (institutional buying catches up)
  - Day 2-3 after earnings MISS: enter SHORT
  - Days 1 and 5+ have weaker drift (already priced or faded)
  - Large surprise (>10% beat) → stronger signal (+10), small (<3%) → weaker (+4)

We already have pead_scorer.py but this is a complete rewrite with
proper drift timing and magnitude scoring.
"""

import logging
import time as _time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_cache: Dict[str, Tuple[float, str, float]] = {}
_CACHE_TTL = 14400.0   # 4 hours (earnings don't change intraday)


def get_pead_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Check if symbol is in optimal PEAD drift window.
    Returns (score_delta, reason). Fail-open.
    """
    now = _time.monotonic()
    cached = _cache.get(symbol)
    if cached and (now - cached[2]) < _CACHE_TTL:
        raw, reason, _ = cached
        return _directional(raw, direction), reason

    score, reason = _compute_pead(symbol)
    _cache[symbol] = (score, reason, now)
    return _directional(score, direction), reason


def _compute_pead(symbol: str) -> Tuple[float, str]:
    """Core PEAD computation using yfinance earnings data."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        # Get earnings history
        earnings = ticker.earnings_history
        if earnings is None or earnings.empty:
            # Try quarterly earnings
            quarterly = ticker.quarterly_earnings
            if quarterly is None or quarterly.empty:
                return 0.0, "pead:no-earnings-data"

            # Use most recent quarter
            recent_eps = float(quarterly.iloc[-1]["Earnings"])
            prev_eps   = float(quarterly.iloc[-2]["Earnings"]) if len(quarterly) >= 2 else recent_eps
            if prev_eps != 0:
                growth = (recent_eps - prev_eps) / abs(prev_eps)
            else:
                growth = 0.0

            # Can't compute surprise without estimate, but use trend
            if growth > 0.15:
                return 5.0, f"pead:strong-growth({growth*100:.0f}%YoY)"
            elif growth > 0.05:
                return 3.0, f"pead:positive-growth"
            elif growth < -0.15:
                return -5.0, f"pead:earnings-decline({growth*100:.0f}%)"
            return 0.0, "pead:flat-earnings"

        # earnings_history has: Surprise(%), EPS Estimate, Reported EPS, Date
        today = datetime.now().date()

        # Find most recent earnings event
        for col in ["Date", "date", "Earnings Date"]:
            if col in earnings.columns:
                date_col = col
                break
        else:
            date_col = earnings.index.name or None

        # Try to get dates from index
        if hasattr(earnings.index, 'date'):
            dates = [(i, earnings.index[i]) for i in range(len(earnings))]
        else:
            return _check_earnings_calendar(symbol)

        if not dates:
            return 0.0, "pead:no-dates"

        # Find most recent earnings (last one)
        # Sort by date descending
        valid_dates = []
        for idx, d in dates:
            try:
                if hasattr(d, 'date'):
                    d = d.date()
                elif isinstance(d, str):
                    d = datetime.strptime(d[:10], "%Y-%m-%d").date()
                valid_dates.append((d, idx))
            except Exception:
                continue

        if not valid_dates:
            return _check_earnings_calendar(symbol)

        valid_dates.sort(key=lambda x: x[0], reverse=True)
        most_recent_date, most_recent_idx = valid_dates[0]
        days_since = (today - most_recent_date).days

        # Only score if in PEAD window (2-5 days after earnings)
        if days_since < 1:
            return 0.0, f"pead:same-day-earnings (wait for day 2)"
        if days_since > 7:
            return 0.0, f"pead:outside-window({days_since}d)"

        # Get surprise magnitude
        row = earnings.iloc[most_recent_idx]
        surprise_pct = 0.0
        for col in ["Surprise(%)", "Surprise", "surprise_pct", "EPS Surprise %"]:
            if col in earnings.columns:
                try:
                    surprise_pct = float(row[col])
                    break
                except Exception:
                    pass

        # Score based on window + magnitude
        direction_sign = 1.0 if surprise_pct > 0 else -1.0

        # Day 2-3 = optimal window
        if 2 <= days_since <= 3:
            window_mult = 1.0
        elif days_since == 1:
            window_mult = 0.5   # reaction day = less predictable
        else:
            window_mult = 0.7   # days 4-7 = drift fading

        abs_surp = abs(surprise_pct)
        if abs_surp >= 15:
            base_score = 10.0
        elif abs_surp >= 8:
            base_score = 7.0
        elif abs_surp >= 3:
            base_score = 5.0
        elif abs_surp >= 1:
            base_score = 3.0
        else:
            base_score = 0.0   # tiny surprise → noise

        score = direction_sign * base_score * window_mult

        return score, (
            f"pead:day{days_since}-surprise={surprise_pct:+.1f}% → {score:+.1f}"
        )

    except Exception as e:
        logger.debug(f"[pead_engine] {symbol}: {e}")
        return _check_earnings_calendar(symbol)


def _check_earnings_calendar(symbol: str) -> Tuple[float, str]:
    """Fallback: check earnings calendar for upcoming/recent events."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar

        if cal is None:
            return 0.0, "pead:no-calendar"

        # Look for earnings date
        earnings_date = None
        if isinstance(cal, dict):
            for key in ["Earnings Date", "earnings_date"]:
                if key in cal:
                    earnings_date = cal[key]
                    break
        elif hasattr(cal, "get"):
            earnings_date = cal.get("Earnings Date")

        if earnings_date is None:
            return 0.0, "pead:no-earnings-date"

        today = datetime.now().date()
        if hasattr(earnings_date, 'date'):
            earnings_date = earnings_date.date()

        days_since = (today - earnings_date).days
        if 2 <= days_since <= 5:
            return 4.0, f"pead:post-earnings-window(day {days_since})"
        elif days_since == 1:
            return 2.0, "pead:day-after-earnings"

        return 0.0, f"pead:not-in-window({days_since}d)"

    except Exception as e:
        logger.debug(f"[pead_engine] _check_calendar {symbol}: {e}")
        return 0.0, "pead:error"


def _directional(raw_score: float, direction: str) -> float:
    if direction == "SHORT":
        return -raw_score
    return raw_score
