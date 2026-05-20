"""
utils.py — US Momentum Alpaca AI Bot
ET Timezone Utilities + Helper Functions

All market logic uses ET (America/New_York). Server may be UTC — always
use get_current_et_time() instead of datetime.now().
"""

import logging
import functools
import time as time_module
from datetime import datetime, time, timedelta, date
from zoneinfo import ZoneInfo
from typing import Optional, Union, Callable, Any
import pandas as pd

ET  = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# US market hours in ET
US_MARKET_OPEN  = time(9, 30)
US_MARKET_CLOSE = time(16, 0)
US_SQUAREOFF    = time(15, 50)   # close positions 10 min before close
US_SQUAREOFF_WARN = time(15, 45)
US_PRE_MARKET_START = time(8, 30)

logger = logging.getLogger(__name__)


# ============================================================
# CORE ET TIME FUNCTIONS
# ============================================================

def get_current_et_time() -> datetime:
    """Get current datetime in US Eastern Time (ET). Handles DST automatically."""
    return datetime.now(ET)


def get_current_et_date() -> date:
    """Get current date in ET."""
    return get_current_et_time().date()


def format_et_timestamp(dt: Optional[datetime] = None) -> str:
    """Format datetime as ET timestamp string for logs."""
    if dt is None:
        dt = get_current_et_time()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC).astimezone(ET)
    else:
        dt = dt.astimezone(ET)
    return dt.strftime("%Y-%m-%d %H:%M:%S ET")


def format_et_time_only(dt: Optional[datetime] = None) -> str:
    """Format as time only: '09:30:00 ET'"""
    if dt is None:
        dt = get_current_et_time()
    return dt.astimezone(ET).strftime("%H:%M:%S ET")


def convert_to_et(dt: Union[datetime, "pd.Timestamp"]) -> datetime:
    """Convert any datetime to ET."""
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET)


# ============================================================
# MARKET HOURS CHECKS
# ============================================================

def is_market_open_et() -> bool:
    """
    Check if US market (NYSE/NASDAQ) is currently open.
    Returns True if 9:30 AM – 4:00 PM ET on a weekday.
    """
    now = get_current_et_time()
    if now.weekday() >= 5:
        return False
    return US_MARKET_OPEN <= now.time() < US_MARKET_CLOSE


def is_pre_market_et() -> bool:
    """Check if it's US pre-market (8:30–9:30 AM ET)."""
    now = get_current_et_time()
    if now.weekday() >= 5:
        return False
    return US_PRE_MARKET_START <= now.time() < US_MARKET_OPEN


def is_us_squareoff_time() -> bool:
    """Check if it's time to square off US positions (after 3:50 PM ET)."""
    return get_current_et_time().time() >= US_SQUAREOFF


def is_us_squareoff_warn_time() -> bool:
    """Check if it's the 15-minute warning window (3:45–3:50 PM ET)."""
    t = get_current_et_time().time()
    return US_SQUAREOFF_WARN <= t < US_SQUAREOFF


def is_market_day_et() -> bool:
    """Check if today is a US trading weekday."""
    return get_current_et_time().weekday() < 5


def minutes_until_market_open() -> float:
    """Minutes until 9:30 AM ET open. Negative if already open or closed."""
    now = get_current_et_time()
    today = now.date()
    open_dt = datetime(today.year, today.month, today.day, 9, 30, 0, tzinfo=ET)
    return (open_dt - now).total_seconds() / 60


def minutes_until_market_close() -> float:
    """Minutes until 4:00 PM ET close. Negative if already closed."""
    now = get_current_et_time()
    today = now.date()
    close_dt = datetime(today.year, today.month, today.day, 16, 0, 0, tzinfo=ET)
    return (close_dt - now).total_seconds() / 60


def get_market_open_datetime_et() -> datetime:
    """Today's 9:30 AM ET."""
    now = get_current_et_time()
    d = now.date()
    return datetime(d.year, d.month, d.day, 9, 30, 0, tzinfo=ET)


def get_market_close_datetime_et() -> datetime:
    """Today's 4:00 PM ET."""
    now = get_current_et_time()
    d = now.date()
    return datetime(d.year, d.month, d.day, 16, 0, 0, tzinfo=ET)


def get_next_market_open_et() -> datetime:
    """Next 9:30 AM ET open (skips weekends)."""
    now = get_current_et_time()
    today = now.date()
    if now.time() < US_MARKET_OPEN and now.weekday() < 5:
        return datetime(today.year, today.month, today.day, 9, 30, 0, tzinfo=ET)
    check = today + timedelta(days=1)
    while check.weekday() >= 5:
        check += timedelta(days=1)
    return datetime(check.year, check.month, check.day, 9, 30, 0, tzinfo=ET)


# ============================================================
# CANDLE TIMESTAMP UTILITIES
# ============================================================

def convert_candle_timestamps_to_et(df: pd.DataFrame) -> pd.DataFrame:
    """Convert candle DataFrame timestamps to ET."""
    df = df.copy()
    if isinstance(df.index, pd.DatetimeIndex):
        if df.index.tz is None:
            df.index = df.index.tz_localize(UTC)
        df.index = df.index.tz_convert(ET)
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize(UTC)
        df["timestamp"] = df["timestamp"].dt.tz_convert(ET)
    elif "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        if df["datetime"].dt.tz is None:
            df["datetime"] = df["datetime"].dt.tz_localize(UTC)
        df["datetime"] = df["datetime"].dt.tz_convert(ET)
    return df


def filter_market_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Filter DataFrame to US market hours (9:30–16:00 ET)."""
    df = convert_candle_timestamps_to_et(df)
    if isinstance(df.index, pd.DatetimeIndex):
        mask = (
            (df.index.time >= US_MARKET_OPEN) &
            (df.index.time <= US_MARKET_CLOSE) &
            (df.index.weekday < 5)
        )
        return df[mask]
    return df


# ============================================================
# IST ALIASES — kept so existing imports don't break
# All IST calls now delegate to their ET equivalents.
# ============================================================

IST = ET   # alias — everything is ET now

get_current_ist_time   = get_current_et_time
get_current_ist_date   = get_current_et_date
format_ist_timestamp   = format_et_timestamp
format_ist_time_only   = format_et_time_only
convert_to_ist         = convert_to_et
convert_utc_to_ist     = convert_to_et
ist_to_utc             = lambda dt: dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=ET).astimezone(UTC)
is_market_open_ist     = is_market_open_et
is_pre_market_ist      = is_pre_market_et
is_squareoff_time_ist  = is_us_squareoff_time
should_force_squareoff_ist = is_us_squareoff_time
is_market_day_ist      = is_market_day_et
convert_candle_timestamps_to_ist = convert_candle_timestamps_to_et
get_market_open_datetime_ist  = get_market_open_datetime_et
get_market_close_datetime_ist = get_market_close_datetime_et
get_next_market_open_ist      = get_next_market_open_et

def is_token_refresh_time() -> bool:
    """No-op for Alpaca (no daily token refresh needed)."""
    return False


# ============================================================
# TRADING UTILITIES
# ============================================================

def round_to_tick_size(price: float, tick_size: float = 0.01) -> float:
    """Round price to tick size (default $0.01 for US stocks)."""
    return round(round(price / tick_size) * tick_size, 2)


def calculate_quantity(capital: float, price: float, risk_pct: float,
                       sl_distance: float) -> int:
    """
    Calculate position quantity based on risk percentage.
    capital: Available capital in USD.
    sl_distance: Stop loss distance from entry in USD.
    """
    if sl_distance <= 0 or price <= 0:
        return 0
    risk_amount = capital * (risk_pct / 100)
    quantity = int(risk_amount / sl_distance)
    max_qty = int((capital * 0.15) / price)
    return min(quantity, max_qty)


def calculate_pnl(entry: float, current: float, quantity: int,
                  direction: str = "BUY") -> dict:
    """Calculate P&L for a position."""
    if direction == "BUY":
        pnl = (current - entry) * quantity
        pnl_pct = ((current - entry) / entry) * 100
    else:
        pnl = (entry - current) * quantity
        pnl_pct = ((entry - current) / entry) * 100
    return {
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "status": "PROFIT" if pnl > 0 else "LOSS",
        "entry": entry,
        "current": current,
        "quantity": quantity,
    }


def format_currency(amount: float) -> str:
    """Format as USD."""
    if abs(amount) >= 1_000_000:
        return f"${amount/1_000_000:.2f}M"
    elif abs(amount) >= 1_000:
        return f"${amount/1_000:.1f}k"
    else:
        return f"${amount:,.2f}"


# ============================================================
# RETRY DECORATOR
# ============================================================

def retry_with_backoff(max_retries: int = 4, delays: list = None):
    """Decorator for API calls with exponential backoff retry."""
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
                            f"[{format_et_timestamp()}] {func.__name__} failed "
                            f"(attempt {attempt+1}/{max_retries}): {e}. "
                            f"Retrying in {delay}s..."
                        )
                        time_module.sleep(delay)
                    else:
                        logger.error(
                            f"[{format_et_timestamp()}] {func.__name__} failed "
                            f"after {max_retries} retries: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator


# ============================================================
# LOGGING SETUP (ET timestamps)
# ============================================================

class ETFormatter(logging.Formatter):
    """Custom log formatter that uses ET timestamps."""

    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created, tz=UTC).astimezone(ET)
        if datefmt:
            return ct.strftime(datefmt)
        return ct.strftime("%Y-%m-%d %H:%M:%S ET")


# Keep old name as alias
ISTFormatter = ETFormatter


def setup_logging(log_dir: str = "logs", level: str = "INFO",
                  module_name: str = "kingtrades") -> logging.Logger:
    """Set up logging with ET timestamps."""
    from pathlib import Path
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    today_et = get_current_et_date()
    log_file = f"{log_dir}/trading_{today_et}.log"

    formatter = ETFormatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logger.info(f"Logging initialized. ET time: {format_et_timestamp()}")
    logger.info(f"Log file: {log_file}")
    return root_logger


# ============================================================
# QUICK TEST
# ============================================================

if __name__ == "__main__":
    print("=== ET Timezone Utilities Test ===")
    print(f"Current ET time: {format_et_timestamp()}")
    print(f"Current ET date: {get_current_et_date()}")
    print(f"Market open? {is_market_open_et()}")
    print(f"Pre-market? {is_pre_market_et()}")
    mins = minutes_until_market_open()
    if mins > 0:
        print(f"Market opens in {mins:.1f} minutes (ET)")
    else:
        mins_close = minutes_until_market_close()
        if mins_close > 0:
            print(f"Market closes in {mins_close:.1f} minutes (ET)")
        else:
            print("Market is closed for today (ET)")
    print(f"$123456 formatted: {format_currency(123456)}")
