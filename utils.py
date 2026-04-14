"""
utils.py — NSE Momentum Groww AI Bot
IST Timezone Utilities + Helper Functions

⚠️ CRITICAL: Server runs in UK (UTC). ALL market logic uses IST.
Never use datetime.now() without timezone. Always use get_current_ist_time().

Timezone: Asia/Kolkata (IST = UTC+5:30)
"""

import logging
from datetime import datetime, time, timedelta, date
from zoneinfo import ZoneInfo
from typing import Optional, Union
import pandas as pd

# IST and UTC zone objects
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Market hours in IST
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
MARKET_SQUAREOFF = time(15, 20)
MARKET_SQUAREOFF_WARN = time(15, 15)
PRE_MARKET_START = time(9, 0)
TOKEN_REFRESH_TIME = time(5, 50)   # Groww tokens expire at 6:00 AM IST — refresh 10 min BEFORE

logger = logging.getLogger(__name__)


# ============================================================
# CORE IST TIME FUNCTIONS
# ============================================================

def get_current_ist_time() -> datetime:
    """
    Get current datetime in IST (Asia/Kolkata).
    Use this EVERYWHERE instead of datetime.now() or datetime.utcnow().
    Server may be in UK (UTC) — this always returns IST regardless.
    """
    return datetime.now(tz=IST)


def get_current_ist_date() -> date:
    """Get current date in IST."""
    return get_current_ist_time().date()


def convert_to_ist(dt: Union[datetime, pd.Timestamp]) -> datetime:
    """
    Convert any datetime (UTC, naive, or other tz) to IST.

    Args:
        dt: datetime or pd.Timestamp to convert

    Returns:
        datetime in IST timezone
    """
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()

    if dt.tzinfo is None:
        # Assume UTC if no timezone info (common with exchange data)
        dt = dt.replace(tzinfo=UTC)
        logger.debug(f"Naive datetime assumed UTC, converted to IST: {dt}")

    return dt.astimezone(IST)


def convert_utc_to_ist(utc_dt: datetime) -> datetime:
    """Convert UTC datetime to IST."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=UTC)
    return utc_dt.astimezone(IST)


def ist_to_utc(ist_dt: datetime) -> datetime:
    """Convert IST datetime to UTC."""
    if ist_dt.tzinfo is None:
        ist_dt = ist_dt.replace(tzinfo=IST)
    return ist_dt.astimezone(UTC)


def format_ist_timestamp(dt: Optional[datetime] = None) -> str:
    """
    Format datetime as IST timestamp string for logs and alerts.
    Always shows IST regardless of server timezone.

    Returns: "2024-01-15 09:15:00 IST"
    """
    if dt is None:
        dt = get_current_ist_time()
    elif dt.tzinfo is None or dt.tzinfo != IST:
        dt = convert_to_ist(dt)
    return dt.strftime("%Y-%m-%d %H:%M:%S IST")


def format_ist_time_only(dt: Optional[datetime] = None) -> str:
    """Format as time only: "09:15:00 IST" """
    if dt is None:
        dt = get_current_ist_time()
    elif dt.tzinfo is None or dt.tzinfo != IST:
        dt = convert_to_ist(dt)
    return dt.strftime("%H:%M:%S IST")


# ============================================================
# MARKET HOURS CHECKS
# ============================================================

# NSE trading holidays 2026 (BSE/NSE official calendar)
NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 19),   # Chhatrapati Shivaji Maharaj Jayanti
    date(2026, 3, 14),   # Holi (Dhuleti)
    date(2026, 3, 31),   # Id-Ul-Fitr (Ramzan Eid)
    date(2026, 4, 2),    # Shri Ram Navami
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 6, 28),   # Eid ul Adha
    date(2026, 8, 15),   # Independence Day
    date(2026, 8, 27),   # Ganesh Chaturthi
    date(2026, 9, 16),   # Milad-un-Nabi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 22),  # Dussehra (Vijaya Dashami)
    date(2026, 11, 11),  # Diwali Laxmi Puja (Muhurat Trading only)
    date(2026, 11, 12),  # Diwali Balipratipada
    date(2026, 11, 25),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
}


def is_nse_holiday(d: date = None) -> bool:
    """Return True if the given date (default: today IST) is an NSE trading holiday."""
    if d is None:
        d = get_current_ist_date()
    return d in NSE_HOLIDAYS_2026


def is_market_open_ist() -> bool:
    """
    Check if NSE market is currently open.
    Returns True if 9:15–3:30 PM IST on a weekday that is not an NSE holiday.
    """
    now_ist = get_current_ist_time()

    if now_ist.weekday() >= 5:          # Sat/Sun
        return False
    if is_nse_holiday(now_ist.date()):  # NSE holiday
        return False

    return MARKET_OPEN <= now_ist.time() < MARKET_CLOSE


def is_pre_market_ist() -> bool:
    """Check if it's pre-market time (9:00 AM to 9:15 AM IST)."""
    now_ist = get_current_ist_time()
    current_time = now_ist.time()
    if now_ist.weekday() >= 5:
        return False
    return PRE_MARKET_START <= current_time < MARKET_OPEN


def is_squareoff_time_ist() -> bool:
    """Check if it's time to begin squaring off (after 3:15 PM IST)."""
    now_ist = get_current_ist_time()
    return now_ist.time() >= MARKET_SQUAREOFF_WARN


def should_force_squareoff_ist() -> bool:
    """Check if forced square-off should happen now (after 3:20 PM IST)."""
    now_ist = get_current_ist_time()
    return now_ist.time() >= MARKET_SQUAREOFF


def is_market_day_ist() -> bool:
    """Check if today is a trading day (weekday + not an NSE holiday) in IST."""
    now_ist = get_current_ist_time()
    if now_ist.weekday() >= 5:
        return False
    return not is_nse_holiday(now_ist.date())


def minutes_until_market_open() -> float:
    """
    Calculate minutes until market opens (9:15 AM IST).
    Returns negative if market already open or closed.
    """
    now_ist = get_current_ist_time()
    today = now_ist.date()
    open_dt = datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)
    delta = (open_dt - now_ist).total_seconds() / 60
    return delta


def minutes_until_market_close() -> float:
    """
    Calculate minutes until market closes (3:30 PM IST).
    Returns negative if already closed.
    """
    now_ist = get_current_ist_time()
    today = now_ist.date()
    close_dt = datetime(today.year, today.month, today.day, 15, 30, 0, tzinfo=IST)
    delta = (close_dt - now_ist).total_seconds() / 60
    return delta


def get_market_open_datetime_ist() -> datetime:
    """Get today's market open datetime in IST."""
    now_ist = get_current_ist_time()
    today = now_ist.date()
    return datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)


def get_market_close_datetime_ist() -> datetime:
    """Get today's market close datetime in IST."""
    now_ist = get_current_ist_time()
    today = now_ist.date()
    return datetime(today.year, today.month, today.day, 15, 30, 0, tzinfo=IST)


def get_next_market_open_ist() -> datetime:
    """Get the next market open datetime (skips weekends)."""
    now_ist = get_current_ist_time()
    today = now_ist.date()
    check_date = today

    if now_ist.time() < MARKET_OPEN and now_ist.weekday() < 5:
        # Today's market hasn't opened yet
        return datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)

    # Move to next day
    check_date += timedelta(days=1)
    while check_date.weekday() >= 5:
        check_date += timedelta(days=1)

    return datetime(check_date.year, check_date.month, check_date.day, 9, 15, 0, tzinfo=IST)


def is_token_refresh_time() -> bool:
    """
    Check if it's time for daily Groww token refresh.

    Groww invalidates ALL tokens at 6:00 AM IST every day.
    We refresh at 5:50 AM — 10 minutes BEFORE expiry — so the current
    valid token is used to generate the next one successfully.
    Window: 5:50–5:55 AM IST (5-minute window, any day).
    """
    now_ist = get_current_ist_time()
    current = now_ist.time()
    return time(5, 50) <= current < time(5, 55)


# ============================================================
# CANDLE TIMESTAMP UTILITIES
# ============================================================

def convert_candle_timestamps_to_ist(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert all candle timestamps to IST in a DataFrame.
    Input df must have datetime index or 'timestamp'/'datetime' column.

    Returns:
        DataFrame with IST timestamps
    """
    df = df.copy()

    if isinstance(df.index, pd.DatetimeIndex):
        if df.index.tz is None:
            df.index = df.index.tz_localize(UTC)
        df.index = df.index.tz_convert(IST)
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize(UTC)
        df["timestamp"] = df["timestamp"].dt.tz_convert(IST)
    elif "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        if df["datetime"].dt.tz is None:
            df["datetime"] = df["datetime"].dt.tz_localize(UTC)
        df["datetime"] = df["datetime"].dt.tz_convert(IST)

    return df


def filter_market_hours(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter DataFrame to only include candles within market hours (9:15-15:30 IST).
    Expects IST-aware datetime index.
    """
    df = convert_candle_timestamps_to_ist(df)
    if isinstance(df.index, pd.DatetimeIndex):
        market_mask = (
            (df.index.time >= MARKET_OPEN) &
            (df.index.time <= MARKET_CLOSE) &
            (df.index.weekday < 5)
        )
        return df[market_mask]
    return df


# ============================================================
# TRADING UTILITIES
# ============================================================

def round_to_tick_size(price: float, tick_size: float = 0.05) -> float:
    """Round price to NSE tick size (default 0.05 paisa)."""
    return round(round(price / tick_size) * tick_size, 2)


def calculate_quantity(capital: float, price: float, risk_pct: float,
                       sl_distance: float) -> int:
    """
    Calculate position quantity based on risk percentage.

    Args:
        capital: Available capital in INR
        price: Current stock price
        risk_pct: Risk per trade as percentage (e.g., 0.5 for 0.5%)
        sl_distance: Stop loss distance from entry in INR

    Returns:
        Number of shares to buy
    """
    if sl_distance <= 0 or price <= 0:
        return 0
    risk_amount = capital * (risk_pct / 100)
    quantity = int(risk_amount / sl_distance)
    # Ensure quantity doesn't use more than 15% of capital
    max_qty = int((capital * 0.15) / price)
    return min(quantity, max_qty)


def calculate_pnl(entry: float, current: float, quantity: int,
                  direction: str = "BUY") -> dict:
    """
    Calculate P&L for a position.

    Args:
        entry: Entry price
        current: Current/exit price
        quantity: Number of shares
        direction: "BUY" or "SELL"

    Returns:
        dict with pnl, pnl_pct, status
    """
    if direction == "BUY":
        pnl = (current - entry) * quantity
        pnl_pct = ((current - entry) / entry) * 100
    else:  # SELL/SHORT
        pnl = (entry - current) * quantity
        pnl_pct = ((entry - current) / entry) * 100

    return {
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "status": "PROFIT" if pnl > 0 else "LOSS",
        "entry": entry,
        "current": current,
        "quantity": quantity
    }


def format_currency(amount: float) -> str:
    """Format amount as Indian currency: ₹1,23,456.78"""
    if abs(amount) >= 10000000:  # 1 crore
        return f"₹{amount/10000000:.2f}Cr"
    elif abs(amount) >= 100000:  # 1 lakh
        return f"₹{amount/100000:.2f}L"
    else:
        return f"₹{amount:,.2f}"


# ============================================================
# RETRY DECORATOR
# ============================================================

import time as time_module
import functools
from typing import Callable, Any


def retry_with_backoff(max_retries: int = 4, delays: list = None):
    """
    Decorator for API calls with exponential backoff retry.
    Delays: [2, 4, 8, 16] seconds by default.
    """
    if delays is None:
        delays = [2, 4, 8, 16]

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = delays[min(attempt, len(delays) - 1)]
                        logger.warning(
                            f"[{format_ist_timestamp()}] {func.__name__} failed "
                            f"(attempt {attempt+1}/{max_retries}): {e}. "
                            f"Retrying in {delay}s..."
                        )
                        time_module.sleep(delay)
                    else:
                        logger.error(
                            f"[{format_ist_timestamp()}] {func.__name__} failed "
                            f"after {max_retries} retries: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator


# ============================================================
# LOGGING SETUP (IST timestamps)
# ============================================================

class ISTFormatter(logging.Formatter):
    """Custom log formatter that uses IST timestamps."""

    def formatTime(self, record, datefmt=None):
        # Convert log record UTC time to IST
        ct = datetime.fromtimestamp(record.created, tz=UTC)
        ist_ct = ct.astimezone(IST)
        if datefmt:
            return ist_ct.strftime(datefmt)
        return ist_ct.strftime("%Y-%m-%d %H:%M:%S IST")


def setup_logging(log_dir: str = "logs", level: str = "INFO",
                  module_name: str = "kingtrades") -> logging.Logger:
    """
    Set up logging with IST timestamps.
    All log entries show IST time regardless of server timezone.
    """
    from pathlib import Path
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    today_ist = get_current_ist_date()
    log_file = f"{log_dir}/trading_{today_ist}.log"

    formatter = ISTFormatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    )

    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logger.info(f"Logging initialized. IST time: {format_ist_timestamp()}")
    logger.info(f"Log file: {log_file}")
    return root_logger


# ============================================================
# QUICK TEST
# ============================================================

if __name__ == "__main__":
    print("=== IST Timezone Utilities Test ===")
    print(f"Current IST time: {format_ist_timestamp()}")
    print(f"Current IST date: {get_current_ist_date()}")
    print(f"Market open? {is_market_open_ist()}")
    print(f"Pre-market? {is_pre_market_ist()}")
    print(f"Market day? {is_market_day_ist()}")
    mins = minutes_until_market_open()
    if mins > 0:
        print(f"Market opens in {mins:.1f} minutes (IST)")
    else:
        mins_close = minutes_until_market_close()
        if mins_close > 0:
            print(f"Market closes in {mins_close:.1f} minutes (IST)")
        else:
            print("Market is closed for today (IST)")
    print(f"Next market open: {get_next_market_open_ist()}")
    print(f"₹123456 formatted: {format_currency(123456)}")
    print(f"₹1234567 formatted: {format_currency(1234567)}")
