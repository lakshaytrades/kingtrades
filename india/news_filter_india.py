"""
news_filter_india.py — Indian economic calendar & news blackout
India specialist: RBI MPC, Union Budget, SEBI events, NSE expiry.

Blackout rules:
  - 30 min before + 60 min after RBI rate decision (10:00 AM IST announcement)
  - All day on Budget day (Feb 1) — extreme volatility
  - 15 min before NSE monthly F&O expiry (last Thursday of month) close
  - 30 min blackout for circuit-breaker stock events
"""
import logging
from datetime import date, datetime, time, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger("news_filter_india")
IST = ZoneInfo("Asia/Kolkata")

# ── RBI MPC rate decision dates (announcement at ~10:00 AM IST day 3) ────────
# Format: (year, month, day) = announcement day
_RBI_DECISION_DATES: List[date] = [
    date(2024, 10, 9),
    date(2024, 12, 6),
    date(2025, 2, 7),
    date(2025, 4, 9),
    date(2025, 6, 6),
    date(2025, 8, 7),
    date(2025, 10, 3),
    date(2025, 12, 5),
    date(2026, 2, 6),
    date(2026, 4, 8),
    date(2026, 6, 5),
    date(2026, 8, 7),
]

_RBI_ANNOUNCE_TIME = time(10, 0)   # RBI announces at ~10 AM IST
_RBI_PRE_BLACKOUT  = timedelta(minutes=30)
_RBI_POST_BLACKOUT = timedelta(minutes=60)

# ── NSE F&O expiry (last Thursday of each month — 3:15 PM IST) ───────────────
_FNO_EXPIRY_WARN_TIME = time(15, 10)  # 15 min before 3:25 PM cut-off

# ── Market-wide circuit breakers ──────────────────────────────────────────────
_NIFTY_CIRCUIT_PCT = 0.02   # pause new entries if Nifty moves >2% from open


def is_rbi_blackout() -> bool:
    """True during RBI rate decision window (30 min before → 60 min after)."""
    now = datetime.now(IST)
    today = now.date()
    if today not in _RBI_DECISION_DATES:
        return False

    decision_dt = datetime(today.year, today.month, today.day,
                           _RBI_ANNOUNCE_TIME.hour, _RBI_ANNOUNCE_TIME.minute,
                           tzinfo=IST)
    window_start = decision_dt - _RBI_PRE_BLACKOUT
    window_end   = decision_dt + _RBI_POST_BLACKOUT
    return window_start <= now <= window_end


def is_budget_day() -> bool:
    """True on Union Budget day (Feb 1). Block all trades — extreme moves."""
    today = datetime.now(IST).date()
    return today.month == 2 and today.day == 1


def is_fno_expiry_caution() -> bool:
    """True in last 15 min of F&O expiry Thursday — reduce exposure."""
    now = datetime.now(IST)
    # Last Thursday of month check
    today = now.date()
    if today.weekday() != 3:   # 3 = Thursday
        return False
    # Is this the last Thursday?
    next_thursday = today + timedelta(days=7)
    if next_thursday.month != today.month:
        # Last Thursday of month
        return now.time() >= _FNO_EXPIRY_WARN_TIME
    return False


def is_nifty_circuit(nifty_open: float, nifty_current: float) -> bool:
    """True if Nifty has moved >2% from open — pause new entries."""
    if nifty_open <= 0:
        return False
    return abs(nifty_current - nifty_open) / nifty_open > _NIFTY_CIRCUIT_PCT


def get_nifty_open() -> float:
    """
    Fetch Nifty 50 open price for TODAY's session using intraday bars.
    Uses 5m bars so we always get today's actual open (first bar of the day),
    not yesterday's daily open that period='2d' + interval='1d' returns
    before the market has opened.
    Fail-open: returns 0.
    """
    try:
        from data_fetch_upstox import get_nifty_level as _nifty
        info = _nifty()
        if info.get("open", 0) > 0:
            return info["open"]
    except Exception as e:
        logger.debug(f"Nifty open fetch: {e}")
    return 0.0


def get_nifty_current() -> float:
    """Fetch current Nifty level. Fail-open: returns 0."""
    try:
        from data_fetch_upstox import get_nifty_level as _nifty
        return _nifty().get("level", 0.0)
    except Exception as e:
        logger.debug(f"Nifty current fetch: {e}")
    return 0.0


def is_news_safe(nifty_open: float = 0.0, nifty_current: float = 0.0) -> bool:
    """
    Master check — returns False if any blackout condition is active.
    True = safe to trade. Fail-open (returns True on errors).
    """
    try:
        if is_budget_day():
            logger.info("NEWS BLACKOUT: Union Budget day — all trades paused")
            return False

        if is_rbi_blackout():
            logger.info("NEWS BLACKOUT: RBI rate decision window")
            return False

        if is_fno_expiry_caution():
            logger.info("NEWS CAUTION: F&O expiry Thursday — reduced exposure")
            # Not a full blackout — let signals through but log warning

        if nifty_open > 0 and nifty_current > 0:
            if is_nifty_circuit(nifty_open, nifty_current):
                pct = (nifty_current - nifty_open) / nifty_open * 100
                logger.info(f"NIFTY CIRCUIT: {pct:+.1f}% from open — pausing new entries")
                return False

    except Exception as e:
        logger.debug(f"news_filter_india: {e}")

    return True


def next_blackout_event() -> Optional[str]:
    """Return description of next upcoming blackout event, for Telegram briefing."""
    now = datetime.now(IST)
    today = now.date()

    if is_budget_day():
        return "Union Budget day — NO trading today"

    # Next RBI date
    upcoming_rbi = [d for d in _RBI_DECISION_DATES if d >= today]
    if upcoming_rbi:
        next_rbi = upcoming_rbi[0]
        days_away = (next_rbi - today).days
        if days_away == 0:
            return f"RBI decision TODAY at 10:00 AM IST"
        if days_away <= 3:
            return f"RBI decision in {days_away} days ({next_rbi.strftime('%d %b')})"

    return None
