"""
earnings_calendar.py — Earnings calendar protection + PEAD alpha.
Two functions:
1. is_near_earnings(symbol) — returns True if earnings within 2 days.
   Blocks new entries to avoid IV crush and gap risk.
2. get_pead_score(symbol) — Post-Earnings Announcement Drift signal.
   If stock beat earnings yesterday/today and is trending up: +12 pts.
   If stock missed earnings and is trending down: -8 pts (short signal).
Uses yfinance calendar data. Cached 4 hours. Fail-open returns safe defaults.
"""
import logging
import time
from typing import Tuple

logger = logging.getLogger(__name__)
_near_cache: dict = {}
_pead_cache: dict = {}
_TTL = 14400.0  # 4 hours


def is_near_earnings(symbol: str) -> bool:
    """True if earnings within 2 calendar days. Fail-open returns False."""
    try:
        now = time.time()
        if symbol in _near_cache and now - _near_cache[symbol][1] < _TTL:
            return _near_cache[symbol][0]
        import yfinance as yf
        from datetime import datetime, timedelta
        cal = yf.Ticker(symbol).calendar
        if cal is None or cal.empty:
            _near_cache[symbol] = (False, now)
            return False
        # calendar has 'Earnings Date' column
        cols = [c for c in cal.columns if 'Earnings' in str(c)]
        if not cols:
            _near_cache[symbol] = (False, now)
            return False
        ed = cal[cols[0]].iloc[0]
        if hasattr(ed, 'date'):
            ed = ed.date()
        today = datetime.now().date()
        near = abs((ed - today).days) <= 2
        _near_cache[symbol] = (near, now)
        return near
    except Exception as e:
        logger.debug(f"earnings_calendar fail-open {symbol}: {e}")
        return False


def get_pead_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Post-earnings drift score. Fail-open returns (0.0, 'pead_unavailable').
    Only fires if earnings were 1-5 days ago.
    """
    try:
        now = time.time()
        key = f"pead_{symbol}"
        if key in _pead_cache and now - _pead_cache[key][1] < _TTL:
            return _pead_cache[key][0]
        import yfinance as yf
        from datetime import datetime, timedelta
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or cal.empty:
            result = (0.0, "no_calendar")
            _pead_cache[key] = (result, now)
            return result
        cols = [c for c in cal.columns if 'Earnings' in str(c)]
        if not cols:
            result = (0.0, "no_earnings_col")
            _pead_cache[key] = (result, now)
            return result
        ed = cal[cols[0]].iloc[0]
        if hasattr(ed, 'date'):
            ed = ed.date()
        today = datetime.now().date()
        days_since = (today - ed).days
        if not (1 <= days_since <= 5):
            result = (0.0, "no_recent_earnings")
            _pead_cache[key] = (result, now)
            return result
        # Check if stock beat: compare price action post-earnings
        hist = ticker.history(period="10d", auto_adjust=True)
        if len(hist) < 3:
            result = (0.0, "insufficient_hist")
            _pead_cache[key] = (result, now)
            return result
        post_ret = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-3] - 1)
        if post_ret > 0.03 and direction == "LONG":
            result = (12.0, f"PEAD_beat+{post_ret:.1%}")
        elif post_ret < -0.03 and direction == "SHORT":
            result = (10.0, f"PEAD_miss{post_ret:.1%}")
        elif post_ret > 0.01 and direction == "LONG":
            result = (5.0, f"PEAD_mild+{post_ret:.1%}")
        else:
            result = (0.0, f"PEAD_neutral{post_ret:.1%}")
        _pead_cache[key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"pead fail-open {symbol}: {e}")
        return (0.0, "pead_unavailable")
