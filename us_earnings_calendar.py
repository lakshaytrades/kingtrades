"""
us_earnings_calendar.py — US Earnings & Economic Calendar (God Mode)
Avoids entering positions right before earnings releases (gap risk).
Also tracks FOMC dates — no new positions on FOMC announcement days.
Data: yfinance earnings calendar + hardcoded FOMC 2025/2026 dates + Economic data RSS.

Earnings gap risk:
  Stock reports earnings within 24h: avoid entry (unpredictable gap risk)
  Stock reported earnings yesterday (post-earnings drift opportunity): +5 if moved favorably
"""
import logging
import time as _time
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("us_earnings")

ET_TZ = ZoneInfo("America/New_York")

_cache: dict = {}
_TTL = 1800.0  # 30 min

# ── FOMC Meeting Dates ────────────────────────────────────────────────────────
# Federal Open Market Committee rate decision announcement dates (market-moving)
FOMC_DATES = {
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
}

# ── Major US Economic Release Dates 2025–2026 ────────────────────────────────
# CPI, NFP (Non-Farm Payrolls), GDP — reduce position sizing on these days
ECONOMIC_RELEASE_DATES = {
    # CPI releases 2025
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-10", "2025-10-15", "2025-11-12", "2025-12-10",
    # NFP (Non-Farm Payrolls) 2025 — first Friday of each month
    "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04",
    "2025-05-02", "2025-06-06", "2025-07-03", "2025-08-01",
    "2025-09-05", "2025-10-03", "2025-11-07", "2025-12-05",
    # GDP Advance releases 2025
    "2025-01-30", "2025-04-30", "2025-07-30", "2025-10-30",
    # CPI releases 2026
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-10",
    "2026-05-13", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-09", "2026-10-14", "2026-11-11", "2026-12-09",
    # NFP 2026
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-08", "2026-06-05", "2026-07-02", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
    # GDP Advance releases 2026
    "2026-01-29", "2026-04-29", "2026-07-29", "2026-10-29",
}


def is_fomc_day() -> bool:
    """Check if today is a Federal Reserve FOMC announcement day (ET timezone)."""
    try:
        today_et = datetime.now(tz=ET_TZ).strftime("%Y-%m-%d")
        return today_et in FOMC_DATES
    except Exception as e:
        logger.debug(f"is_fomc_day error: {e}")
        return False


def is_economic_data_day() -> bool:
    """
    Return True on major US economic release days (CPI, NFP, GDP).
    Reduces position sizing to manage gap risk from macro surprises.
    """
    try:
        today_et = datetime.now(tz=ET_TZ).strftime("%Y-%m-%d")
        return today_et in ECONOMIC_RELEASE_DATES
    except Exception as e:
        logger.debug(f"is_economic_data_day error: {e}")
        return False


def get_earnings_date(symbol: str) -> Optional[date]:
    """
    Get next earnings date for symbol using yfinance calendar.
    Returns None if unavailable. Cached 30 minutes.
    """
    now = _time.time()
    cache_key = f"earnings_date:{symbol}"
    if cache_key in _cache and now - _cache[cache_key][1] < _TTL:
        return _cache[cache_key][0]

    result: Optional[date] = None
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(symbol)
        cal = ticker_obj.calendar

        if cal is not None:
            # yfinance calendar is a dict with 'Earnings Date' key (list of dates)
            if isinstance(cal, dict):
                earnings_dates = cal.get("Earnings Date", [])
                if isinstance(earnings_dates, list) and earnings_dates:
                    raw = earnings_dates[0]
                elif isinstance(earnings_dates, (datetime, date)):
                    raw = earnings_dates
                else:
                    raw = None

                if raw is not None:
                    if hasattr(raw, "date"):
                        result = raw.date()
                    elif isinstance(raw, date) and not isinstance(raw, datetime):
                        result = raw
                    elif isinstance(raw, str):
                        result = datetime.strptime(raw[:10], "%Y-%m-%d").date()
            elif hasattr(cal, "iloc"):
                # DataFrame format (older yfinance)
                try:
                    earnings_row = cal.loc["Earnings Date"] if "Earnings Date" in cal.index else None
                    if earnings_row is not None:
                        raw_val = earnings_row.iloc[0] if hasattr(earnings_row, "iloc") else earnings_row
                        if hasattr(raw_val, "date"):
                            result = raw_val.date()
                except Exception:
                    pass

    except Exception as e:
        logger.debug(f"get_earnings_date fail-open {symbol}: {e}")

    _cache[cache_key] = (result, now)
    return result


def should_avoid_earnings(symbol: str) -> Tuple[bool, str]:
    """
    Check if we should avoid entering a position due to upcoming earnings.
    Returns (should_avoid, reason).
    - Earnings today: (True, "earnings_today")
    - Earnings within 1 day: (True, "earnings_tomorrow")
    - Otherwise: (False, "")
    """
    try:
        earnings_dt = get_earnings_date(symbol)
        if earnings_dt is None:
            return (False, "")

        today_et = datetime.now(tz=ET_TZ).date()
        days_until = (earnings_dt - today_et).days

        if days_until == 0:
            return (True, "earnings_today")
        elif days_until == 1:
            return (True, "earnings_tomorrow")
        elif days_until < 0 and days_until >= -1:
            # Earnings was yesterday — post-earnings drift window
            return (False, "post_earnings_window")
        else:
            return (False, "")

    except Exception as e:
        logger.debug(f"should_avoid_earnings fail-open {symbol}: {e}")
        return (False, "")


def get_earnings_drift_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Post-earnings announcement drift (PEAD) signal.
    If earnings were yesterday AND stock moved favorably (>2%):
      LONG: stock up >2% → +5 (momentum continuation)
      SHORT: stock down >2% → +5 (negative PEAD continuation)
    Returns (score_delta, reason). Fail-open returns (0.0, "").
    """
    try:
        earnings_dt = get_earnings_date(symbol)
        if earnings_dt is None:
            return (0.0, "")

        today_et = datetime.now(tz=ET_TZ).date()
        days_since = (today_et - earnings_dt).days

        # Only apply drift score if earnings was yesterday (day after = drift window)
        if days_since != 1:
            return (0.0, "")

        # Fetch yesterday and day-before price to measure post-earnings move
        now = _time.time()
        cache_key = f"drift:{symbol}"
        if cache_key in _cache and now - _cache[cache_key][1] < _TTL:
            return _cache[cache_key][0]

        import yfinance as yf
        hist = yf.download(symbol, period="5d", interval="1d", progress=False, auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 2:
            _cache[cache_key] = ((0.0, ""), now)
            return (0.0, "")

        # Get the last two closes
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            _cache[cache_key] = ((0.0, ""), now)
            return (0.0, "")

        yesterday_close = float(closes.iloc[-1])
        day_before_close = float(closes.iloc[-2])
        if day_before_close == 0:
            _cache[cache_key] = ((0.0, ""), now)
            return (0.0, "")

        pct_move = (yesterday_close - day_before_close) / day_before_close * 100.0

        result: Tuple[float, str]
        if direction == "LONG" and pct_move > 2.0:
            result = (5.0, f"post_earnings_drift_long:{pct_move:+.1f}%")
        elif direction == "SHORT" and pct_move < -2.0:
            result = (5.0, f"post_earnings_drift_short:{pct_move:+.1f}%")
        else:
            result = (0.0, "")

        _cache[cache_key] = (result, now)
        return result

    except Exception as e:
        logger.debug(f"get_earnings_drift_score fail-open {symbol}: {e}")
        return (0.0, "")
