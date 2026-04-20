# ============================================================
# KINGTRADES — NSE MOMENTUM GROWW AI BOT
# ALL MODULES COMBINED INTO ONE FILE
# ============================================================

# ============================================================
# MODULE: config.py
# ============================================================
"""
config.py — NSE Momentum Groww AI Bot
Central configuration with IST timezone settings.

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) but ALL trading logic uses IST (Asia/Kolkata).
"""

import os
from datetime import time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env
load_dotenv()

# ============================================================
# TIMEZONE — CRITICAL
# Server: UK (UTC) | Markets: India (IST = UTC+5:30)
# ============================================================
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Market hours in IST
MARKET_OPEN_IST = time(9, 15)
MARKET_CLOSE_IST = time(15, 30)
MARKET_SQUAREOFF_IST = time(15, 20)   # Force square-off before this
MARKET_SQUAREOFF_WARN = time(15, 15)  # Warn at this time
PRE_MARKET_START_IST = time(9, 0)
TOKEN_REFRESH_TIME_IST = time(5, 50)  # Groww tokens expire at 6:00 AM IST — refresh 10 min BEFORE

# Trading days: Monday(0) to Friday(4)
TRADING_DAYS = {0, 1, 2, 3, 4}

# ============================================================
# GROWW API CREDENTIALS (from trades secret env group)
# ============================================================
GROWW_AUTH_TOKEN = os.getenv("GROWW_AUTH_TOKEN", "")
GROWW_CLIENT_ID = os.getenv("GROWW_CLIENT_ID", "")
GROWW_CLIENT_SECRET = os.getenv("GROWW_CLIENT_SECRET", "")
GROWW_EMAIL = os.getenv("GROWW_EMAIL", "")
GROWW_PASSWORD = os.getenv("GROWW_PASSWORD", "")
GROWW_TOTP_SECRET = os.getenv("GROWW_TOTP_SECRET", "")

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# NEWS / SENTIMENT
# ============================================================
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ============================================================
# TRADING CONFIGURATION
# ============================================================

# LIVE TRADING SWITCH — Default False for safety
# Set LIVE_TRADING_ENABLED=True in .env ONLY when ready
LIVE_TRADING_ENABLED: bool = True

# Capital
MAX_DAILY_CAPITAL: float = float(os.getenv("MAX_DAILY_CAPITAL", "50000"))

# Risk per trade (0.5% default, max 1%)
MAX_RISK_PER_TRADE_PCT: float = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.5"))
MAX_RISK_PER_TRADE_PCT = min(MAX_RISK_PER_TRADE_PCT, 1.0)  # Hard cap at 1%

# Daily loss limit (2%)
DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "2.0"))
DAILY_LOSS_LIMIT_PCT = min(DAILY_LOSS_LIMIT_PCT, 3.0)  # Hard cap at 3%

# Position limits
MAX_POSITIONS: int = 10        # Max simultaneous open positions
MIN_POSITIONS: int = 1
MAX_CAPITAL_PER_TRADE_PCT: float = 15.0  # Max 15% of capital in single trade

# ============================================================
# STRATEGY PARAMETERS
# ============================================================

# RSI Settings
RSI_PERIOD: int = 14
RSI_OVERSOLD: float = 35.0       # Buy signal zone (trainer may adjust)
RSI_OVERBOUGHT: float = 65.0     # Sell signal zone (trainer may adjust)
RSI_EXTREME_OVERSOLD: float = 25.0
RSI_EXTREME_OVERBOUGHT: float = 75.0

# MACD Settings
MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL: int = 9

# ATR Settings (for SL/TP)
ATR_PERIOD: int = 14
ATR_SL_MULTIPLIER: float = 1.4   # Tighter SL for higher accuracy (was 1.5)
ATR_TP_MULTIPLIER: float = 2.8   # T1 = 2:1 R:R, T2 = 4:1 R:R
ATR_TRAIL_MULTIPLIER: float = 0.8 # Trail activates earlier — protect profits faster
# Partial exit strategy (higher accuracy = take profits)
PARTIAL_EXIT_T1_PCT: float = 50.0   # Exit 50% at T1 (was 40%)
PARTIAL_EXIT_T2_PCT: float = 30.0   # Exit 30% at T2
RUNNER_PCT: float = 20.0            # 20% runner with trailing stop

# EMA Settings
EMA_FAST: int = 9
EMA_MID: int = 21
EMA_SLOW: int = 50
EMA_TREND: int = 200

# Volume Settings
VOLUME_SURGE_MULTIPLIER: float = 1.8  # Entry gate: 1.8x (filter uses this)
VOLUME_HIGH_CONVICTION: float = 2.5  # 2.5x = high conviction → full size bonus
VOLUME_SMA_PERIOD: int = 20

# Bollinger Bands
BB_PERIOD: int = 20
BB_STD: float = 2.0

# Stochastic
STOCH_K_PERIOD: int = 14
STOCH_D_PERIOD: int = 3
STOCH_SMOOTH: int = 3

# VWAP
VWAP_DEVIATION_THRESHOLD: float = 0.5  # % deviation from VWAP to signal

# Multi-Timeframe
PRIMARY_TIMEFRAME: str = "5m"
CONFIRMATION_TIMEFRAME: str = "15m"
TREND_TIMEFRAME: str = "1h"

# ── HIGH-ACCURACY MODE (targets 70-80% win rate) ───────────
# Raised from 65 → 72. Fewer trades, higher quality.
# 18yr rule: "It's not the number of trades, it's the quality."
MIN_SIGNAL_SCORE: float = 72.0          # Hard minimum — no trade below this
HIGH_CONFIDENCE_SCORE: float = 82.0     # Full size above this
PREMIUM_SCORE: float = 90.0             # 1.2x size — A+ grade setups only
MIN_VOLUME_RATIO: float = 1.8           # Min volume surge for entry (was 1.5)
REQUIRE_MTF_ALIGNMENT: bool = True      # Always require ≥2 TF alignment
REQUIRE_POWER_HOUR: bool = True         # Only trade during power windows
HEIKIN_ASHI_CONFIRM: bool = True        # Require HA confirmation
MAX_TRADES_PER_DAY: int = 6             # Quality > quantity. Max 6 per day.
MAX_TRADES_PER_STOCK: int = 2           # Max 2 trades per stock per day

# ============================================================
# CIRCUIT BREAKERS
# ============================================================
NIFTY_CIRCUIT_PCT: float = 2.0        # Pause if Nifty moves >2%
CONSECUTIVE_LOSS_LIMIT: int = 3       # Pause after 3 consecutive losses
PAUSE_AFTER_LOSSES_MINUTES: int = 30  # Pause duration after consecutive losses

# ============================================================
# WATCHLIST
# ============================================================
DEFAULT_WATCHLIST = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT",
    "WIPRO", "HCLTECH", "AXISBANK", "MARUTI", "SUNPHARMA",
    "TATAMOTORS", "BAJFINANCE", "ADANIENT", "ULTRACEMCO", "TITAN"
]

CUSTOM_WATCHLIST_STR = os.getenv("CUSTOM_WATCHLIST", "")
WATCHLIST = (
    [s.strip() for s in CUSTOM_WATCHLIST_STR.split(",") if s.strip()]
    if CUSTOM_WATCHLIST_STR
    else DEFAULT_WATCHLIST
)

# Nifty50 index symbol for relative strength
NIFTY_SYMBOL = "NIFTY 50"
NIFTY_GROWW_SYMBOL = "^NSEI"  # For data fetch

# ============================================================
# LOGGING
# ============================================================
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR: str = os.getenv("LOG_DIR", "logs")
TRADE_LOG_DIR: str = f"{LOG_DIR}/trades"
PERFORMANCE_LOG_DIR: str = f"{LOG_DIR}/performance"
CHART_DIR: str = "charts"

# Create log directories
for d in [LOG_DIR, TRADE_LOG_DIR, PERFORMANCE_LOG_DIR, CHART_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

# ============================================================
# API RETRY SETTINGS
# ============================================================
MAX_RETRIES: int = 4
RETRY_DELAYS: list = [2, 4, 8, 16]  # Exponential backoff (seconds)
API_TIMEOUT: int = 10  # seconds

# ============================================================
# NEWS FILTER
# ============================================================
NEWS_BLACKOUT_MINUTES: int = 30  # Skip signals 30 min before/after events
HIGH_IMPACT_KEYWORDS = [
    "RBI", "monetary policy", "repo rate", "CPI", "GDP", "inflation",
    "election", "budget", "SEBI", "circuit breaker", "market halt"
]

# ============================================================
# TRAINER
# ============================================================
TRAINER_DATA_DIR: str = "data"
TRAINER_RESULTS_DIR: str = "trainer_results"
BACKTEST_SLIPPAGE_PCT: float = 0.1    # 0.1% slippage per trade
BACKTEST_BROKERAGE_PCT: float = 0.03  # ~0.03% Groww brokerage
BACKTEST_TAX_PCT: float = 0.1         # STT + other taxes estimate
TARGET_MONTHLY_RETURN_PCT: float = 5.0
TARGET_WIN_RATE: float = 55.0
TARGET_SHARPE: float = 1.5
MAX_DRAWDOWN_LIMIT: float = 8.0

# Create trainer dirs
for d in [TRAINER_DATA_DIR, TRAINER_RESULTS_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

# ============================================================
# OPTION CHAIN ANALYSIS
# ============================================================
# Uses NSE public API — no API key required, just proper headers
OPTION_CHAIN_ENABLED:  bool  = True
OC_SYMBOLS:           list  = ["NIFTY", "BANKNIFTY"]  # Indices to read
OC_CACHE_TTL_SECONDS: int   = 300          # Refresh every 5 min
OC_PCR_BULLISH_THRESHOLD:  float = 1.2     # PCR > this → bullish
OC_PCR_BEARISH_THRESHOLD:  float = 0.7     # PCR < this → bearish
OC_MAX_PAIN_INFLUENCE_PCT: float = 1.5     # Max pain gravity within this % range

# ============================================================
# FII / DII FLOW TRACKER
# ============================================================
FII_DII_ENABLED:        bool  = True
FII_BULLISH_THRESHOLD:  float = 500.0      # FII net > ₹500cr = bullish
FII_BEARISH_THRESHOLD:  float = -500.0     # FII net < -₹500cr = bearish
FII_STRONG_BUY:         float = 2000.0     # Strong buy signal
FII_CACHE_MINUTES:      int   = 30         # How often to refresh flow data

# ============================================================
# VOLUME PROFILE
# ============================================================
VP_ENABLED:             bool  = True
VP_NUM_BINS:            int   = 100        # Price level resolution
VP_VALUE_AREA_PCT:      float = 70.0       # Standard 70% value area
VP_COMPOSITE_DAYS:      int   = 5          # Multi-day composite lookback
VP_HVN_STD_THRESHOLD:  float = 0.5        # HVN: above mean + 0.5 std
VP_LVN_STD_THRESHOLD:  float = 1.0        # LVN: below mean - 1.0 std

# ============================================================
# ADVANCED RISK MANAGEMENT
# ============================================================

# Portfolio heat = total SL risk across all open positions as % of capital
# 3% = 6 positions × 0.5% each — hard ceiling, no new entries above this
MAX_PORTFOLIO_HEAT_PCT: float = 3.0

# Maximum positions per sector (correlation guard)
# Prevents 3 bank stocks from tripling a sector-wide loss
MAX_POSITIONS_PER_SECTOR: int = 2

# Session-based size multipliers (auto-applied in risk_manager)
# Override here if you want different behaviour per session
SESSION_SIZE_MULTIPLIERS = {
    "OPENING_DRIVE": 1.00,   # 09:15-10:00 — peak momentum, full size
    "MORNING":       0.80,   # 10:00-11:00 — good but fading momentum
    "MIDDAY_CHOP":   0.50,   # 11:00-13:30 — chop zone, half size
    "AFTERNOON":     0.80,   # 13:30-15:00 — institutional resumption
    "CLOSING":       0.30,   # 15:00-15:20 — only high-conviction exits
}

# ============================================================
# NSE SUPPLEMENTARY DATA
# ============================================================
NSE_DATA_ENABLED: bool = True       # Bulk/block deals, delivery %, FII futures
NSE_DATA_CACHE_TTL: int = 3600      # Cache timeout seconds
NSE_BULK_DEAL_MIN_VALUE_CR: float = 1.0  # Ignore deals < ₹1 crore (noise)
NSE_DELIVERY_STRONG_PCT: float = 60.0    # Above this = strong conviction
NSE_DELIVERY_WEAK_PCT:   float = 30.0    # Below this = speculative only
NSE_FII_FUTURES_BULLISH_PCT: float = 55.0  # FII long % above this = bullish
NSE_FII_FUTURES_BEARISH_PCT: float = 45.0  # FII long % below this = bearish

# ============================================================
# CONCURRENT SIGNAL SCANNING
# ============================================================
SCAN_MAX_WORKERS: int = 6          # ThreadPoolExecutor workers for watchlist scan
SCAN_SYMBOL_TIMEOUT: int = 20      # Seconds before a single symbol scan times out
SCAN_TOTAL_TIMEOUT:  int = 90      # Seconds before full scan cycle aborts

# ============================================================
# TRAINER — ADVANCED
# ============================================================
MONTE_CARLO_SIMULATIONS:   int   = 1000
MONTE_CARLO_RUIN_THRESHOLD: float = 40.0  # % drawdown = "ruin"
MONTE_CARLO_MAX_RUIN_PCT:   float = 5.0   # Deployable only if ruin prob < 5%
SENSITIVITY_ROBUSTNESS_MIN: float = 0.70  # Min robustness score to deploy
BENCHMARK_SYMBOL:           str   = "^NSEI"  # Nifty50 for benchmark comparison

# ============================================================
# DAY-OF-WEEK PROFIT OPTIMIZER  (4 profitable days per week)
# ============================================================
# 18yr NSE observation:
#   Monday   — Gap-and-trap. Institutions test retail. Lower accuracy.
#   Tuesday  — Best trend day. Institutions deploy capital. Full size.
#   Wednesday— Best continuation day. Trend confirmed. Full size.
#   Thursday — F&O expiry games + afternoon volatility. Slightly cautious.
#   Friday   — Profit booking. Institutions exit positions. Reduce size.
#
# Goal: Win 4 out of 5 days/week by adjusting aggressiveness per day.

DOW_SIZE_MULTIPLIERS: dict = {
    0: 0.65,   # Monday   — volatile open, gap traps, 65% size
    1: 1.00,   # Tuesday  — best trend day, full size
    2: 1.00,   # Wednesday— trend continuation, full size
    3: 0.80,   # Thursday — F&O expiry effect, 80% size
    4: 0.65,   # Friday   — profit booking / reversals, 65% size
}

# Minimum signal score by day (higher bar on volatile days)
DOW_MIN_SCORE: dict = {
    0: 78.0,   # Monday   — A-grade only
    1: 72.0,   # Tuesday  — standard
    2: 72.0,   # Wednesday— standard
    3: 75.0,   # Thursday — slightly tighter
    4: 78.0,   # Friday   — A-grade only
}

# Max trades per day by day-of-week
DOW_MAX_TRADES: dict = {
    0: 3,   # Monday   — 3 max
    1: 6,   # Tuesday  — 6 max
    2: 6,   # Wednesday— 6 max
    3: 5,   # Thursday — 5 max
    4: 3,   # Friday   — 3 max
}

# Weekly P&L management
WEEKLY_PROFIT_TARGET_PCT: float = 2.0    # At 2% weekly profit → only A+ trades
WEEKLY_PROFIT_LOCK_PCT:   float = 1.5    # At 1.5% weekly → 50% size, A/A+ only
WEEKLY_LOSS_STOP_PCT:     float = 2.5    # -2.5% weekly loss → halt new entries
WEEKLY_DATA_FILE:         str   = "data/weekly_pnl.json"

# ============================================================
# NSE F&O / GAP / LIQUIDITY / CIRCUIT BREAKER  (11/10 gates)
# ============================================================

# Minimum daily traded volume (shares) required for entry
# Stocks below this are too illiquid — wide spreads, poor fills
MIN_DAILY_VOLUME: int = 500_000       # 5 lakh shares/day minimum

# Gap filter — skip gapped stocks during price discovery window
# Applied only to first N minutes after market open
MAX_GAP_PCT: float = 2.0              # MEDIUM gap threshold (%)
LARGE_GAP_PCT: float = 3.5            # LARGE gap threshold (%)
EXTREME_GAP_PCT: float = 5.0          # EXTREME gap — avoid session (%)

# Circuit breaker proximity — don't trade near circuit limits
# NSE circuit bands: 5%, 10%, 20% from previous close
CIRCUIT_BUFFER_PCT: float = 0.5       # Avoid within 0.5% of any circuit band
CIRCUIT_BANDS: list = [5.0, 10.0, 20.0]   # NSE circuit levels

# Corporate actions buffer — skip stocks within N days of ex-date
CORP_ACTION_BUFFER_DAYS: int = 2      # Avoid 2 days before/on ex-date

# F&O eligibility — cache refresh
FO_LIST_CACHE_HOURS: int = 24         # Refresh F&O list once per day

# API health check at session start
API_HEALTH_CHECK_ENABLED: bool = True

# ============================================================
# VALIDATION
# ============================================================
def validate_config() -> list:
    """Validate critical configuration. Returns list of issues."""
    issues = []
    if not GROWW_AUTH_TOKEN:
        issues.append("GROWW_AUTH_TOKEN not set")
    if not GROWW_TOTP_SECRET:
        issues.append("GROWW_TOTP_SECRET not set — TOTP auto-login disabled")
    if not TELEGRAM_BOT_TOKEN:
        issues.append("TELEGRAM_BOT_TOKEN not set — alerts disabled")
    if not TELEGRAM_CHAT_ID:
        issues.append("TELEGRAM_CHAT_ID not set — alerts disabled")
    if LIVE_TRADING_ENABLED and not GROWW_AUTH_TOKEN:
        issues.append("CRITICAL: LIVE_TRADING_ENABLED=True but no Groww token!")
    return issues


if __name__ == "__main__":
    issues = validate_config()
    if issues:
        print("Configuration issues:")
        for i in issues:
            print(f"  ⚠️  {i}")
    else:
        print("✅ Configuration valid")
    print(f"Live trading: {'⚡ ENABLED — REAL MONEY' if LIVE_TRADING_ENABLED else '🔒 DISABLED (safe mode)'}")
    print(f"Capital: ₹{MAX_DAILY_CAPITAL:,.0f}")
    print(f"Timezone: IST (Asia/Kolkata) — Server: UK UTC")


# ============================================================
# MODULE: utils.py
# ============================================================
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


# ============================================================
# MODULE: auth_groww.py
# ============================================================
"""
auth_groww.py — NSE Momentum Groww AI Bot
Fully Automatic TOTP Token Refresh — Zero Manual Intervention

─── HOW GROWW TOKENS WORK ───────────────────────────────────────────────────
Groww issues two kinds of credentials from developer.groww.in:

  1. GROWW_AUTH_TOKEN  — The daily trading JWT. Groww invalidates ALL JWTs
                         at exactly 6:00 AM IST every morning (their day reset).
                         You manually copy this once from developer.groww.in.

  2. GROWW_TOTP_SECRET — Your permanent TOTP base32 secret (never expires).
                         Used to generate 6-digit TOTP codes.

─── WHAT THE BOT DOES AUTOMATICALLY ────────────────────────────────────────
  At 5:50 AM IST every morning (10 min BEFORE the 6 AM expiry):
    totp_code    = pyotp.TOTP(GROWW_TOTP_SECRET).now()
    access_token = GrowwAPI.get_access_token(api_key=CURRENT_JWT, totp=totp_code)
    → fresh JWT valid until next 6:00 AM IST

  The bot uses the STILL-VALID current JWT to authenticate, then swaps it
  for the new one. This is why 5:50 AM is critical — at 6:05 AM the old
  JWT is already dead and the exchange fails.

  Fallback: If the SDK call fails, a direct HTTPS request to api.groww.in
  is tried (same endpoint, no Cloudflare block — api.groww.in is the API
  domain, not groww.in which is the web UI).

─── SETUP (ONE TIME ONLY) ───────────────────────────────────────────────────
  Set in Render → kingtrades-bot → Environment Variables:
    GROWW_AUTH_TOKEN  = current JWT from developer.groww.in → API Keys
    GROWW_TOTP_SECRET = TOTP secret from developer.groww.in → API Keys

  After this, the bot refreshes automatically forever. You never manually
  update GROWW_AUTH_TOKEN again — the bot keeps it fresh at 5:50 AM IST daily.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import requests

import pyotp

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_TOKEN_CACHE_FILE = Path("data/.token_cache.json")

# ────────────────────────────────────────────────────────────────────────────
# Token cache — stores api_key and access_token SEPARATELY
# ────────────────────────────────────────────────────────────────────────────

def _save_token_cache(api_key: str, access_token: str, timestamp: datetime) -> None:
    """
    Save both the permanent api_key and today's access_token.
    api_key is stored so the bot can survive a Render restart without re-reading env.
    access_token is stored to avoid unnecessary re-auth on restarts within the same day.
    """
    try:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE_FILE.write_text(json.dumps({
            "api_key":      api_key,
            "access_token": access_token,
            "timestamp":    timestamp.isoformat(),
        }))
    except Exception as e:
        logger.debug(f"Token cache write failed: {e}")


def _load_token_cache() -> Tuple[Optional[str], Optional[str], Optional[datetime]]:
    """
    Returns (api_key, access_token, timestamp).
    access_token is only returned if it's <12h old (Groww access tokens last ~24h but we
    refresh at 6h to be safe). api_key is always returned if present.
    """
    try:
        if not _TOKEN_CACHE_FILE.exists():
            return None, None, None
        data = json.loads(_TOKEN_CACHE_FILE.read_text())

        api_key      = data.get("api_key", "")
        access_token = data.get("access_token", "")
        ts_str       = data.get("timestamp", "")

        if not ts_str:
            return api_key or None, None, None

        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)

        age_h = (datetime.now(IST) - ts).total_seconds() / 3600

        if age_h < 20 and access_token and len(access_token) > 20:
            logger.info(
                f"[{format_ist_timestamp()}] Cache hit: access_token age {age_h:.1f}h "
                f"(valid for {20 - age_h:.1f}h more)"
            )
            return api_key or None, access_token, ts

        logger.info(
            f"[{format_ist_timestamp()}] Cache: access_token {age_h:.1f}h old — "
            "will refresh (api_key retained)"
        )
        return api_key or None, None, ts

    except Exception as e:
        logger.debug(f"Token cache read failed: {e}")
    return None, None, None


# ────────────────────────────────────────────────────────────────────────────
# Core refresh — uses permanent api_key, generates fresh access_token via TOTP
# ────────────────────────────────────────────────────────────────────────────

def _extract_token_from_response(body: dict) -> Optional[str]:
    """Pull the JWT from any known key in a Groww API response dict."""
    for key in ("token", "authToken", "auth_token", "access_token",
                "jwtToken", "jwt_token", "userToken", "user_token"):
        val = body.get(key) or (body.get("data") or {}).get(key)
        if val and isinstance(val, str) and len(val) > 20:
            return val
    return None


def _refresh_via_direct_http(api_key: str, totp_code: str) -> Optional[str]:
    """
    Direct HTTPS call to api.groww.in — used when the SDK is not installed
    or when the SDK call fails.  api.groww.in is NOT behind Cloudflare so it
    works from Render (unlike groww.in/v1/api/... which is blocked).

    Tries every known token-generation endpoint pattern.
    """
    headers = {
        "Authorization":  f"Bearer {api_key}",
        "Content-Type":   "application/json",
        "Accept":         "application/json",
        "User-Agent":     "growwapi-python/1.0",
        "X-Api-Version":  "1",
    }
    endpoints = [
        # Official SDK endpoint (most likely)
        ("POST", "https://api.groww.in/v1/user/generate_token",
         {"api_key": api_key, "totp": totp_code}),
        # Alternate patterns used by various SDK versions
        ("POST", "https://api.groww.in/v1/user/session/generate_token",
         {"api_key": api_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/auth/access-token",
         {"api_key": api_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/auth/token",
         {"apiKey": api_key, "totp": totp_code}),
        # With current token in Authorization header only
        ("POST", "https://api.groww.in/v1/user/token/refresh",
         {"totp": totp_code}),
    ]

    for method, url, body in endpoints:
        try:
            resp = requests.request(
                method, url, json=body, headers=headers, timeout=20
            )
            label = url.split("/")[-1]
            if resp.status_code in (200, 201):
                try:
                    tok = _extract_token_from_response(resp.json())
                    if tok:
                        logger.info(
                            f"[{format_ist_timestamp()}] ✅ Direct HTTP token via {label} "
                            f"(HTTP {resp.status_code})"
                        )
                        return tok
                    logger.debug(f"  [{label}] HTTP 200 but no token — keys: {list(resp.json().keys())}")
                except Exception:
                    pass
            elif resp.status_code == 401:
                logger.debug(f"  [{label}] HTTP 401 — api_key rejected")
            elif resp.status_code == 404:
                logger.debug(f"  [{label}] HTTP 404 — endpoint not found (trying next)")
            else:
                logger.debug(f"  [{label}] HTTP {resp.status_code}")
        except requests.exceptions.ConnectionError:
            logger.debug(f"  [{url.split('/')[2]}] Connection error — not reachable")
        except Exception as e:
            logger.debug(f"  Direct HTTP {url.split('/')[-1]}: {e}")
    return None


def _refresh_access_token(api_key: str, totp_secret: str, attempt: int = 1) -> Optional[str]:
    """
    Refresh the Groww access token using the current JWT + TOTP.

    TIMING IS CRITICAL:
      - Call this at 5:50 AM IST — the current JWT is still valid (expires at 6:00 AM)
      - The fresh JWT is valid until next 6:00 AM IST
      - After 6:00 AM the old JWT is dead and this call fails

    Try order:
      1. growwapi SDK class method (GrowwAPI.get_access_token)
      2. growwapi SDK instance method (older SDK versions)
      3. Direct HTTPS to api.groww.in (no SDK needed, not Cloudflare-blocked)

    api_key     = GROWW_AUTH_TOKEN (the current valid JWT — refreshed daily)
    totp_secret = GROWW_TOTP_SECRET (permanent base32 secret — never changes)
    """
    if not api_key or not totp_secret:
        logger.error(
            f"[{format_ist_timestamp()}] Cannot refresh: "
            f"api_key={'set' if api_key else 'MISSING'}, "
            f"totp_secret={'set' if totp_secret else 'MISSING'}\n"
            "  → Set GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET in Render Environment."
        )
        return None

    # Wait for a TOTP window with ≥ 5s of life left (avoids code expiring mid-call)
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 5:
        wait = remaining + 1
        logger.info(f"[{format_ist_timestamp()}] Waiting {wait}s for fresh TOTP window...")
        time.sleep(wait)

    totp_code = pyotp.TOTP(totp_secret).now()
    window_left = 30 - (int(time.time()) % 30)
    logger.info(
        f"[{format_ist_timestamp()}] Token refresh attempt {attempt}/3 | "
        f"TOTP={totp_code} | window={window_left}s"
    )

    # ── Path 1: growwapi SDK class method ────────────────────────────────
    try:
        from growwapi import GrowwAPI
        access_token = GrowwAPI.get_access_token(api_key=api_key, totp=totp_code)
        if isinstance(access_token, str) and len(access_token) > 20:
            logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.get_access_token()")
            return access_token
        if isinstance(access_token, dict):
            tok = _extract_token_from_response(access_token)
            if tok:
                logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.get_access_token() [dict]")
                return tok
        logger.debug(f"SDK returned: {str(access_token)[:100]}")

    except AttributeError:
        # Older SDK — try instance method
        try:
            from growwapi import GrowwAPI
            tmp = GrowwAPI(api_key)
            for mname in ("get_access_token", "refresh_token", "generate_session"):
                fn = getattr(tmp, mname, None)
                if not fn:
                    continue
                try:
                    result = fn(totp=totp_code)
                    if result and isinstance(result, str) and len(result) > 20:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK instance.{mname}()")
                        return result
                except TypeError:
                    pass
        except Exception as e2:
            logger.debug(f"SDK instance fallback: {e2}")

    except ImportError:
        logger.debug("growwapi SDK not installed — using direct HTTP")

    except Exception as e:
        logger.warning(f"[{format_ist_timestamp()}] SDK error: {e}")

    # ── Path 2: Direct HTTPS to api.groww.in (no SDK needed) ─────────────
    logger.info(f"[{format_ist_timestamp()}] Trying direct HTTPS to api.groww.in...")
    tok = _refresh_via_direct_http(api_key, totp_code)
    if tok:
        return tok

    logger.warning(
        f"[{format_ist_timestamp()}] Attempt {attempt}/3 failed.\n"
        "  Cause: Either the JWT in GROWW_AUTH_TOKEN expired before 5:50 AM refresh,\n"
        "  or api.groww.in is temporarily unreachable from Render."
    )
    return None


# ────────────────────────────────────────────────────────────────────────────
# Auth Manager
# ────────────────────────────────────────────────────────────────────────────

class GrowwAuthManager:
    """
    Fully automatic Groww authentication manager.

    Two permanent env vars (set ONCE in Render, never touch again):
      GROWW_AUTH_TOKEN  = permanent API key JWT from developer.groww.in
      GROWW_TOTP_SECRET = base32 TOTP secret from the same page

    The manager:
      - Loads api_key from env (permanent — never overwritten)
      - Generates a fresh access_token via TOTP every morning at 6:05 AM IST
      - Auto-retries 3x with 30s backoff on transient failures
      - Caches access_token on disk (survives bot restarts within the same day)
      - Notifies via Telegram if TOTP secret or api_key is missing
    """

    def __init__(self):
        # ── Permanent credentials (set ONCE, env vars never change) ───────
        self._api_key    = os.getenv("GROWW_AUTH_TOKEN", "")   # permanent JWT
        self.totp_secret = os.getenv("GROWW_TOTP_SECRET", "")  # TOTP base32 secret

        # Kept for possible future use / web login fallback
        self.email    = os.getenv("GROWW_EMAIL", "")
        self.password = os.getenv("GROWW_PASSWORD", "")

        # ── Runtime state ─────────────────────────────────────────────────
        self._token:           Optional[str]      = None   # daily access token
        self._token_timestamp: Optional[datetime] = None

        # ── Load cache first ──────────────────────────────────────────────
        cached_api_key, cached_access, cached_ts = _load_token_cache()

        # If cache has a valid api_key and env has none, use cache's api_key
        if not self._api_key and cached_api_key:
            self._api_key = cached_api_key
            logger.info(f"[{format_ist_timestamp()}] api_key restored from cache")

        # If cache has a fresh access_token, use it (skip refresh until it's old)
        if cached_access:
            self._token           = cached_access
            self._token_timestamp = cached_ts
            logger.info(f"[{format_ist_timestamp()}] Using cached access_token")

        # ── Validate config ───────────────────────────────────────────────
        if not self._api_key:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_AUTH_TOKEN not set!\n"
                "  1. Go to developer.groww.in → Manage → API Keys\n"
                "  2. Copy your permanent JWT\n"
                "  3. Render → kingtrades-bot → Environment → GROWW_AUTH_TOKEN → paste → Save"
            )
        if not self.totp_secret:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_TOTP_SECRET not set!\n"
                "  1. Go to developer.groww.in → Manage → API Keys\n"
                "  2. Find 'TOTP Secret' (base32 string) → copy it\n"
                "  3. Render → kingtrades-bot → Environment → GROWW_TOTP_SECRET → paste → Save"
            )

        if self._api_key and self.totp_secret:
            logger.info(f"[{format_ist_timestamp()}] ✅ Auth manager ready (api_key + TOTP set)")

    # ──────────────────────────────────────────────────────────────────────
    # Token refresh with 3-attempt retry
    # ──────────────────────────────────────────────────────────────────────

    def _do_refresh_with_retry(self) -> Optional[str]:
        """
        Get a fresh access_token using the permanent api_key + live TOTP code.
        Matches exactly what developer.groww.in docs show:

            totp_code    = pyotp.TOTP(totp_secret).now()
            access_token = GrowwAPI.get_access_token(api_key=api_key, totp=totp_code)
            groww        = GrowwAPI(access_token)

        api_key is PERMANENT — it never changes, never expires.
        access_token is DAILY — refreshed automatically at 5:50 AM IST.
        Retries 3 times with 30s spacing on transient failures.
        """
        for attempt in range(1, 4):
            token = _refresh_access_token(self._api_key, self.totp_secret, attempt)
            if token:
                self._token           = token
                self._token_timestamp = get_current_ist_time()
                # Save api_key (permanent) + access_token (daily) separately.
                # ⚠️  DO NOT update self._api_key here — it is permanent.
                _save_token_cache(self._api_key, token, self._token_timestamp)
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ Fresh access_token saved "
                    f"(attempt {attempt}/3) — valid until next 6:00 AM IST"
                )
                return token

            if attempt < 3:
                logger.warning(
                    f"[{format_ist_timestamp()}] Attempt {attempt}/3 failed — "
                    "retrying in 30s..."
                )
                time.sleep(30)

        # All 3 attempts failed
        logger.error(
            f"[{format_ist_timestamp()}] ❌ All 3 refresh attempts failed.\n"
            "  Bot will continue with existing token until it expires.\n"
            "  Check: GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET are correct in Render env vars."
        )
        self._send_refresh_failed_alert()
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def get_valid_token(self) -> Optional[str]:
        """
        Return a valid access_token, refreshing if needed.

        Because api_key is PERMANENT, we can call get_access_token() at any time
        of day — not just at 5:50 AM. So we refresh whenever the token is stale
        (>20h old) rather than only in a pre-expiry window.

        Called before every GrowwAPI() instantiation.
        """
        if self._token and self._token_timestamp:
            age_h = (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600
            if age_h < 20:
                return self._token
            logger.info(
                f"[{format_ist_timestamp()}] access_token {age_h:.1f}h old — refreshing..."
            )
        elif not self._token:
            logger.info(f"[{format_ist_timestamp()}] No access_token yet — fetching now...")

        # Fallback: if refresh fails, return old token (may still work for a bit)
        return self._do_refresh_with_retry() or self._token

    def refresh_token_if_needed(self) -> bool:
        """
        Force a fresh access_token via TOTP.
        Called at 5:50 AM IST daily and on demand.
        """
        logger.info(
            f"[{format_ist_timestamp()}] TOTP refresh: "
            f"pyotp.TOTP(secret).now() → GrowwAPI.get_access_token(api_key, totp)..."
        )
        token = self._do_refresh_with_retry()
        if token:
            logger.info(f"[{format_ist_timestamp()}] ✅ access_token refreshed — bot ready")
            return True
        logger.error(
            f"[{format_ist_timestamp()}] ❌ Refresh failed.\n"
            "  Verify in Render env vars:\n"
            "  • GROWW_AUTH_TOKEN  = TOTP Token from developer.groww.in → API Keys\n"
            "  • GROWW_TOTP_SECRET = TOTP Secret from developer.groww.in → API Keys"
        )
        return False

    def force_refresh(self) -> bool:
        """Force an immediate refresh (called by /refresh Telegram command or watchdog)."""
        logger.info(f"[{format_ist_timestamp()}] Forced token refresh requested...")
        token = self._do_refresh_with_retry()
        return bool(token)

    def login_and_get_token(self) -> Optional[str]:
        """Compatibility shim — calls get_valid_token()."""
        return self.get_valid_token()

    @property
    def token(self) -> Optional[str]:
        return self._token

    @property
    def token_age_hours(self) -> float:
        if not self._token_timestamp:
            return float("inf")
        return (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600

    # ──────────────────────────────────────────────────────────────────────
    # Telegram alerts
    # ──────────────────────────────────────────────────────────────────────

    def _send_refresh_failed_alert(self) -> None:
        """Telegram alert when all 3 refresh attempts fail."""
        try:
            import config, requests as req
            if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
                return
            req.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id":    config.TELEGRAM_CHAT_ID,
                    "parse_mode": "HTML",
                    "text": (
                        "🔴 <b>Groww Token Refresh Failed</b>\n\n"
                        "All 3 TOTP refresh attempts failed.\n\n"
                        "<b>Check these in Render env vars:</b>\n"
                        "• <code>GROWW_AUTH_TOKEN</code> — permanent JWT from "
                        "developer.groww.in → API Keys\n"
                        "• <code>GROWW_TOTP_SECRET</code> — base32 TOTP secret "
                        "from same page\n\n"
                        "Bot is running on old token — may fail soon.\n"
                        "<i>Once you fix env vars, bot auto-retries next morning.</i>"
                    ),
                },
                timeout=10,
            )
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────────
# Singleton
# ────────────────────────────────────────────────────────────────────────────

_auth_manager: Optional[GrowwAuthManager] = None


def get_auth_manager() -> GrowwAuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GrowwAuthManager()
    return _auth_manager


def get_groww_token() -> Optional[str]:
    """Get current valid access_token (auto-refreshes if needed)."""
    return get_auth_manager().get_valid_token()


def initialize_auth() -> bool:
    """
    Called at bot startup and by watchdog before each daily launch.

    Refresh policy:
    • Pre-expiry window (5:45–5:59 AM IST): ALWAYS refresh — this is the primary window
    • Token stale (>10h): refresh even mid-day (handles bot restarts after 6 AM)
    • Otherwise: use existing token as-is
    """
    manager = get_auth_manager()
    now_ist = get_current_ist_time()
    h, m = now_ist.hour, now_ist.minute

    token_age_h      = manager.token_age_hours
    pre_expiry_window = (h == 5 and m >= 45) or (h == 5 and m < 60)  # 5:45–5:59 AM
    token_stale       = token_age_h > 10   # refresh if >10h old (safe margin)

    if pre_expiry_window:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: pre-expiry window "
            f"(5:45–5:59 AM IST) — refreshing now..."
        )
        manager.refresh_token_if_needed()
    elif token_stale:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: token {token_age_h:.1f}h old — refreshing..."
        )
        manager.refresh_token_if_needed()
    else:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: token fresh "
            f"({token_age_h:.1f}h old) — no refresh needed"
        )

    return bool(manager._token or manager._api_key)


# ────────────────────────────────────────────────────────────────────────────
# Standalone test — run: python auth_groww.py
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Testing Groww TOTP auth...")
    manager = GrowwAuthManager()
    print(f"api_key set: {bool(manager._api_key)}")
    print(f"totp_secret set: {bool(manager.totp_secret)}")
    token = manager.get_valid_token()
    if token:
        print(f"✅ Access token obtained: {token[:30]}...{token[-10:]}")
    else:
        print("❌ Failed to get token — check GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET")


# ============================================================
# MODULE: data_fetch_groww.py
# ============================================================
"""
data_fetch_groww.py — NSE Momentum Groww AI Bot
Fixed: Missing positional arguments & AttributeError
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

from utils import (
    format_ist_timestamp, get_current_ist_time, convert_to_ist,
    convert_candle_timestamps_to_ist, filter_market_hours,
    retry_with_backoff, get_market_open_datetime_ist
)
from auth_groww import get_groww_token, get_auth_manager

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Candle interval mapping for Groww API
INTERVAL_MAP = {
    "1m":  "1minute",
    "5m":  "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h":  "60minute",
    "1d":  "1day",
}

class GrowwDataFetcher:
    def __init__(self):
        self._api = None
        self._cache: Dict[str, Tuple[pd.DataFrame, datetime]] = {}
        self._cache_ttl_seconds = 30
        self._balance_cache: Dict = {}        # last successful balance response
        self._balance_cache_time: Optional[datetime] = None
        self._last_token_refresh_time: Optional[datetime] = None  # debounce refreshes
        self._TOKEN_REFRESH_COOLDOWN_MIN = 15  # never refresh token more often than this
        self._init_api()

    def _init_api(self):
        try:
            from growwapi import GrowwAPI
            token = get_groww_token()
            if token:
                self._api = GrowwAPI(token)
                logger.info(f"[{format_ist_timestamp()}] GrowwAPI initialized")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Init failed: {e}")

    def _reinit_with_fresh_token(self, reason: str = "auth error") -> bool:
        """
        Force a fresh TOTP access_token and rebuild the API client.
        Debounced: will not refresh more than once every TOKEN_REFRESH_COOLDOWN_MIN minutes
        to prevent log-spam refresh loops when Groww API is returning empty responses.
        """
        now = get_current_ist_time()
        if self._last_token_refresh_time is not None:
            elapsed_min = (now - self._last_token_refresh_time).total_seconds() / 60
            if elapsed_min < self._TOKEN_REFRESH_COOLDOWN_MIN:
                logger.debug(
                    f"Token refresh skipped ({elapsed_min:.0f}m < {self._TOKEN_REFRESH_COOLDOWN_MIN}m cooldown)"
                )
                return False

        logger.warning(f"[{format_ist_timestamp()}] {reason} — forcing token refresh...")
        self._last_token_refresh_time = now
        try:
            manager = get_auth_manager()
            ok = manager.force_refresh()
            new_token = manager.token
            if ok and new_token:
                from growwapi import GrowwAPI
                self._api = GrowwAPI(new_token)
                logger.info(f"[{format_ist_timestamp()}] ✅ API client refreshed with new access_token")
                return True
            logger.error(
                f"[{format_ist_timestamp()}] ❌ Token refresh failed — "
                "check GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET in Render env vars"
            )
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Token refresh error: {e}")
        return False

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_quote(self, symbol: str) -> Optional[Dict]:
        if not self._api: self._init_api()
        if not self._api: return None

        try:
            raw = self._api.get_quote(trading_symbol=symbol, exchange="NSE", segment="CASH")
            if not raw: return None

            return {
                "symbol": symbol,
                "ltp": float(raw.get("ltp") or 0),
                "open": float(raw.get("open") or 0),
                "high": float(raw.get("high") or 0),
                "low": float(raw.get("low") or 0),
                "close": float(raw.get("close") or 0),
                "volume": int(raw.get("volume") or 0),
                "change_pct": float(raw.get("change_percent") or 0),
                "product": "MIS",
                "timestamp": format_ist_timestamp(),
            }
        except Exception as e:
            err = str(e).lower()
            if "authentication" in err or "expired" in err or "401" in err or "403" in err:
                logger.warning(f"[{format_ist_timestamp()}] Auth error on get_quote — refreshing token...")
                if self._reinit_with_fresh_token("Auth error on get_quote"):
                    raise  # Retry with new token via @retry_with_backoff
                return None
            # 400 Bad Request = wrong symbol/segment — no point retrying
            if "bad request" in err or "400" in err or "invalid symbol" in err or "not found" in err:
                logger.debug(f"get_quote({symbol}): bad request — unsupported symbol, skipping")
                return None  # Return None (not raise) so retry decorator is NOT triggered
            # Rate limit — wait 3s before the decorator retries (not 0s)
            if "rate limit" in err or "too many" in err or "429" in err:
                logger.debug(f"get_quote({symbol}): rate limited — pausing 3s")
                time.sleep(3)
                raise  # Let retry decorator handle it after the pause
            logger.error(f"get_quote({symbol}) failed: {e}")
            raise

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_candles(self, symbol: str, interval: str = "5m", days: int = 5, from_dt=None, to_dt=None) -> Optional[pd.DataFrame]:
        if not self._api: return None
        now_ist = get_current_ist_time()
        to_dt = to_dt or now_ist
        from_dt = from_dt or (to_dt - timedelta(days=days))

        from_ms = int(from_dt.timestamp() * 1000)
        to_ms   = int(to_dt.timestamp() * 1000)
        interval_str = INTERVAL_MAP.get(interval, "5minute")

        raw = self._call_candles_sdk(symbol, interval_str, from_ms, to_ms)
        if raw is None:
            raw = self._fetch_candles_http(symbol, interval_str, from_ms, to_ms)
        if raw is None:
            raw = self._fetch_candles_yfinance(symbol, interval_str, from_ms, to_ms)
        if raw is None:
            raise RuntimeError(f"All candle sources failed for {symbol} — check Groww API access")
        return self._parse_candles(raw, symbol, interval)

    def _call_candles_sdk(self, symbol: str, interval_str: str, from_ms: int, to_ms: int):
        """
        Try every known GrowwAPI method name for historical OHLCV data.
        Different SDK versions ship different method names — we try them all.
        Returns raw list-of-lists or None.
        """
        if not self._api:
            return None

        # Candidate method names across SDK versions
        candidates = [
            "get_historical_data",
            "get_historical_candle_data",
            "get_candle_data",
            "get_ohlcv",
            "historical_data",
            "candles",
        ]
        kwargs_sets = [
            # v1 SDK style
            dict(symbol=symbol, exchange="NSE", segment="CASH",
                 interval=interval_str, from_timestamp=from_ms, to_timestamp=to_ms),
            # v2 SDK style (different key names)
            dict(trading_symbol=symbol, exchange="NSE", segment="CASH",
                 interval=interval_str, from_timestamp=from_ms, to_timestamp=to_ms),
            # Positional fallback
            None,
        ]

        for method_name in candidates:
            fn = getattr(self._api, method_name, None)
            if fn is None:
                continue
            for kwargs in kwargs_sets:
                try:
                    if kwargs is None:
                        raw = fn(symbol, "NSE", "CASH", interval_str, from_ms, to_ms)
                    else:
                        raw = fn(**kwargs)
                    if raw:
                        logger.debug(f"Candle SDK hit: {method_name}")
                        return raw
                except TypeError:
                    continue  # wrong kwargs — try next set
                except Exception as e:
                    logger.debug(f"SDK candles {method_name}: {e}")
                    break  # method exists but failed — don't try other kwarg sets

        logger.debug(f"No SDK candle method worked for {symbol} — trying HTTP")
        return None

    def _fetch_candles_http(self, symbol: str, interval_str: str, from_ms: int, to_ms: int):
        """
        Direct REST fallback when SDK has no historical-data method.
        Tries known Groww API endpoint patterns.
        Returns raw list-of-lists or None.
        """
        import requests as req
        token = get_groww_token()
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "X-Api-Version": "1",
        }

        # Known Groww historical-data endpoint patterns
        endpoints = [
            ("GET", "https://api.groww.in/v1/historical-data", {
                "trading_symbol": symbol, "exchange": "NSE", "segment": "CASH",
                "interval": interval_str, "from": from_ms, "to": to_ms,
            }),
            ("GET", "https://api.groww.in/v2/historical-data", {
                "trading_symbol": symbol, "exchange": "NSE", "segment": "CASH",
                "interval": interval_str, "from_timestamp": from_ms, "to_timestamp": to_ms,
            }),
            ("GET", "https://api.groww.in/v1/charting_service/chart/historical", {
                "exchange": "NSE", "tradingsymbol": symbol,
                "timeperiod": interval_str, "starttime": from_ms, "endtime": to_ms,
            }),
        ]

        for method, url, params in endpoints:
            try:
                resp = req.request(method, url, params=params, headers=headers, timeout=20)
                if resp.status_code == 200:
                    body = resp.json()
                    # Handle envelope formats
                    candles = (body.get("candles") or body.get("data") or
                               body.get("ohlcv") or body.get("result") or body)
                    if isinstance(candles, list) and len(candles) > 0:
                        logger.debug(f"Candle HTTP hit: {url.split('/')[-1]} for {symbol}")
                        return candles
                elif resp.status_code == 400:
                    logger.debug(f"HTTP candles 400 from {url.split('/')[-1]} — wrong params")
                    continue
                elif resp.status_code in (401, 403):
                    logger.warning(f"HTTP candles auth error — refreshing token")
                    self._reinit_with_fresh_token()
                    break
            except Exception as e:
                logger.debug(f"HTTP candles {url.split('/')[-1]}: {e}")

        logger.debug(f"get_candles({symbol}): all Groww HTTP endpoints failed — trying yfinance")
        return None

    def _fetch_candles_yfinance(self, symbol: str, interval_str: str, from_ms: int, to_ms: int):
        """
        yfinance fallback for NSE historical OHLCV.
        NSE symbols need .NS suffix (e.g. RELIANCE → RELIANCE.NS).
        yf interval map: 1minute→1m, 5minute→5m, 15minute→15m, 30minute→30m, 60minute→60m, 1day→1d
        """
        try:
            import yfinance as yf
            from datetime import datetime as _dt

            yf_sym = f"{symbol}.NS"
            yf_interval_map = {
                "1minute": "1m", "5minute": "5m", "15minute": "15m",
                "30minute": "30m", "60minute": "60m", "1day": "1d",
            }
            yf_interval = yf_interval_map.get(interval_str, "5m")

            start_dt = _dt.fromtimestamp(from_ms / 1000, tz=IST)
            end_dt   = _dt.fromtimestamp(to_ms   / 1000, tz=IST)

            df = yf.download(
                yf_sym, start=start_dt, end=end_dt,
                interval=yf_interval, progress=False, auto_adjust=True,
            )
            if df is None or df.empty:
                logger.debug(f"yfinance: no data for {yf_sym}")
                return None

            # Flatten MultiIndex columns (newer yfinance returns (field, ticker) tuples)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Convert to standard list-of-lists [ts_ms, o, h, l, c, vol]
            result = []
            for ts, row in df.iterrows():
                try:
                    ts_ms = int(ts.timestamp() * 1000)
                    result.append([
                        ts_ms,
                        float(row["Open"].iloc[0])   if hasattr(row["Open"],   "iloc") else float(row["Open"]),
                        float(row["High"].iloc[0])   if hasattr(row["High"],   "iloc") else float(row["High"]),
                        float(row["Low"].iloc[0])    if hasattr(row["Low"],    "iloc") else float(row["Low"]),
                        float(row["Close"].iloc[0])  if hasattr(row["Close"],  "iloc") else float(row["Close"]),
                        int(  row["Volume"].iloc[0]) if hasattr(row["Volume"], "iloc") else int(row["Volume"]),
                    ])
                except Exception:
                    continue
            if result:
                logger.info(f"[{format_ist_timestamp()}] yfinance candles: {symbol} "
                            f"{len(result)} bars ({yf_interval})")
            return result or None

        except ImportError:
            logger.debug("yfinance not installed — skipping")
            return None
        except Exception as e:
            err = str(e).lower()
            if "ratelimit" in err or "rate limit" in err or "too many" in err or "429" in err:
                logger.warning(f"[{format_ist_timestamp()}] yfinance rate limited for {symbol} — "
                               "will retry on next scan cycle")
            else:
                logger.debug(f"yfinance candles {symbol}: {e}")
            return None

    def _parse_candles(self, raw, symbol, interval):
        if not raw: return None
        records = []
        for c in raw:
            try:
                dt = datetime.fromtimestamp(c[0]/1000, tz=UTC).astimezone(IST)
                records.append({
                    "datetime": dt, "open": float(c[1]), "high": float(c[2]), 
                    "low": float(c[3]), "close": float(c[4]), "volume": int(c[5])
                })
            except: continue
        df = pd.DataFrame(records).sort_values("datetime").set_index("datetime")
        return df if not df.empty else None

    # --------------------------------------------------------
    # ACCOUNT & LEVERAGE (FIXED Attribute Error)
    # --------------------------------------------------------

    def _refresh_api_if_needed(self):
        """
        Re-initialize the GrowwAPI client with the latest token.
        Called by main.py after a TOTP token refresh at 8:45 AM IST.
        """
        self._init_api()

    # --------------------------------------------------------

    def get_account_balance(self) -> Dict:
        """
        Fetch live account balance from Groww.

        Behaviour:
        - Tries get_balance() / get_funds() / get_margins() in order
        - Handles dict, object, and nested envelope (data/payload/result) responses
        - On empty/None response: retries once with a fresh token
        - If still empty (Groww API offline or market closed): returns last cached
          balance with a '_from_cache' + '_cache_age_min' flag for the caller to display
        - Every successful fetch is stored in self._balance_cache
        """
        _empty = {
            "available": 0, "used_margin": 0, "total": 0,
            "collateral": 0, "opening": 0,
            "_from_cache": False, "_cache_age_min": 0,
        }

        def _call_api() -> Optional[Dict]:
            """Call Groww balance endpoint, return raw dict or None."""
            if not self._api:
                return None
            for method_name in ("get_balance", "get_funds", "get_margins"):
                if hasattr(self._api, method_name):
                    try:
                        raw = getattr(self._api, method_name)()
                        logger.info(f"[balance_raw:{method_name}] {raw}")
                        return raw
                    except Exception as e:
                        logger.debug(f"balance {method_name} error: {e}")
            return None

        def _parse(res) -> Optional[Dict]:
            """Parse raw response → normalised balance dict. Returns None if empty."""
            if res is None:
                return None
            # Convert SDK object → dict
            if not isinstance(res, dict):
                try:
                    res = vars(res)
                except TypeError:
                    try:
                        res = dict(res)
                    except Exception:
                        return None
            if not res:
                return None

            # Unwrap envelope keys
            for key in ("data", "payload", "result", "response"):
                if key in res and isinstance(res[key], dict) and res[key]:
                    res = res[key]
                    break

            logger.info(f"[balance_fields] {list(res.keys())}")

            def _get(*keys) -> float:
                for k in keys:
                    v = res.get(k)
                    if v is not None:
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            pass
                return 0.0

            available = _get(
                "available_cash", "available_margin", "available",
                "available_amount", "available_limit", "cash",
                "net_available", "free_cash", "liquid_cash",
                "trading_power",
            )
            used_margin = _get(
                "used_margin", "utilised_margin", "margin_used",
                "used_amount", "blocked_amount", "used",
            )
            collateral = _get(
                "collateral", "collateral_margin", "collateral_amount",
            )
            total = _get(
                "net", "net_value", "total", "net_amount",
                "opening_balance", "total_balance",
            ) or (available + used_margin)
            opening = _get(
                "opening_balance", "start_of_day_limit", "sod_balance",
            ) or total

            # Treat as empty if everything is 0 (API returned zeroes, not real data)
            if available == 0 and used_margin == 0 and total == 0:
                return None

            return {
                "available":   available,
                "used_margin": used_margin,
                "collateral":  collateral,
                "total":       total,
                "opening":     opening,
                "_raw":        res,
                "_from_cache": False,
                "_cache_age_min": 0,
            }

        # ── Attempt 1: use current API instance ───────────────────────────
        result = _parse(_call_api())

        # ── Attempt 2: token refresh ONLY if genuinely stale (debounced) ──────
        # Empty balance ≠ auth error. The Groww balance API also returns empty
        # when the server IP is not whitelisted, or when Groww's backend is slow.
        # Refreshing the token on every empty response creates a noisy refresh
        # loop that wastes TOTP attempts and obscures the real issue.
        # We only refresh if:
        #   (a) we're inside market hours (outside, empty is expected)
        #   (b) the cooldown has elapsed (max once per 15 min)
        if result is None:
            from utils import is_market_open_ist
            if is_market_open_ist():
                now = get_current_ist_time()
                last_refresh = self._last_token_refresh_time
                elapsed_min = (
                    (now - last_refresh).total_seconds() / 60
                    if last_refresh else float("inf")
                )
                if elapsed_min >= self._TOKEN_REFRESH_COOLDOWN_MIN:
                    logger.info(
                        f"[{format_ist_timestamp()}] Balance empty during market hours — "
                        "attempting token refresh (once per 15 min). "
                        "If this persists, check Groww API Settings → add Render IP."
                    )
                    try:
                        if self._reinit_with_fresh_token("Balance empty during market hours"):
                            result = _parse(_call_api())
                    except Exception as e:
                        logger.debug(f"Balance retry error: {e}")
                else:
                    logger.debug(
                        f"Balance empty — using cache (refresh cooldown: "
                        f"{self._TOKEN_REFRESH_COOLDOWN_MIN - elapsed_min:.0f}m remaining)"
                    )
            else:
                logger.debug("Balance empty outside market hours — using cache (API offline by design)")

        # ── Success: update cache ─────────────────────────────────────────
        if result is not None:
            self._balance_cache = result.copy()
            self._balance_cache_time = get_current_ist_time()
            return result

        # ── Fallback: return last cached balance ──────────────────────────
        if self._balance_cache:
            age_min = 0
            if self._balance_cache_time:
                age_min = (get_current_ist_time() - self._balance_cache_time).total_seconds() / 60
            cached = self._balance_cache.copy()
            cached["_from_cache"]     = True
            cached["_cache_age_min"]  = round(age_min, 0)
            logger.info(f"Returning cached balance (age: {age_min:.0f} min)")
            return cached

        # ── Nothing available ─────────────────────────────────────────────
        logger.warning("Groww balance API returned empty — market may be closed")
        return _empty

    def get_positions(self) -> List[Dict]:
        """Fetch MIS leverage positions."""
        if not self._api: return []
        try:
            raw = self._api.get_positions()
            return [{
                "symbol": p.get("symbol"),
                "quantity": int(p.get("quantity", 0)),
                "product": "MIS", # Ensures leverage is tracked correctly
                "avg_price": float(p.get("average_price", 0))
            } for p in (raw or []) if int(p.get("quantity", 0)) != 0]
        except: return []

    # --------------------------------------------------------
    # PREVIOUS FEATURES: MTF, ORB, TODAY
    # --------------------------------------------------------

    def get_multi_timeframe_data(self, symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
        data = {}
        for interval, days in [("5m", 5), ("15m", 10), ("1h", 30)]:
            try: data[interval] = self.get_candles(symbol, interval=interval, days=days)
            except: data[interval] = None
        return data

    def get_today_candles(self, symbol: str, interval: str = "5m") -> Optional[pd.DataFrame]:
        return self.get_candles(symbol, interval=interval, from_dt=get_market_open_datetime_ist())

    def get_nifty_quote(self) -> Optional[Dict]:
        """Fetch Nifty 50 index quote. Tries INDEX segment first, falls back to CASH."""
        if not self._api:
            return None
        # Try INDEX segment (correct for index symbols)
        for sym in ["NIFTY 50", "NIFTY"]:
            try:
                raw = self._api.get_quote(trading_symbol=sym, exchange="NSE", segment="INDEX")
                if raw and raw.get("ltp"):
                    return {
                        "symbol": sym,
                        "ltp": float(raw.get("ltp") or 0),
                        "open": float(raw.get("open") or 0),
                        "high": float(raw.get("high") or 0),
                        "low": float(raw.get("low") or 0),
                        "close": float(raw.get("close") or 0),
                        "volume": int(raw.get("volume") or 0),
                        "change_pct": float(raw.get("change_percent") or 0),
                        "timestamp": format_ist_timestamp(),
                    }
            except Exception as e:
                logger.debug(f"Nifty quote ({sym}/INDEX) failed: {e}")
        # Fallback: try CASH segment (some SDK versions use this)
        for sym in ["NIFTY 50", "NIFTY"]:
            try:
                q = self.get_quote(sym)
                if q:
                    return q
            except Exception:
                continue
        # Final fallback: yfinance ^NSEI (always works)
        try:
            import yfinance as yf
            ticker = yf.Ticker("^NSEI")
            info   = ticker.fast_info
            ltp    = float(getattr(info, "last_price", 0) or 0)
            if ltp > 0:
                prev_close = float(getattr(info, "previous_close", ltp) or ltp)
                change_pct = round((ltp - prev_close) / prev_close * 100, 2) if prev_close else 0
                logger.debug(f"Nifty from yfinance: ₹{ltp:,.0f}")
                return {
                    "symbol": "NIFTY",
                    "ltp": ltp,
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": prev_close,
                    "volume": 0,
                    "change_pct": change_pct,
                    "timestamp": format_ist_timestamp(),
                }
        except Exception as e:
            logger.debug(f"Nifty yfinance fallback failed: {e}")
        return None

_fetcher = None
def get_data_fetcher() -> GrowwDataFetcher:
    global _fetcher
    if _fetcher is None: _fetcher = GrowwDataFetcher()
    return _fetcher


# ============================================================
# MODULE: risk_manager.py
# ============================================================
"""
risk_manager.py — NSE Momentum Groww AI Bot
ATR-Based Risk Management Engine

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
All risk parameters from config.py — never hardcode limits.

Features:
- Per-trade risk: 0.5–1% of capital (ATR-based SL)
- Trailing stop: activates at 1x ATR profit, trails 0.5x ATR
- Daily loss limit circuit breaker (2% of capital)
- Nifty circuit breaker (pause if Nifty moves >2%)
- Consecutive loss pause (3 losses → 30min pause)
- Position sizing via Kelly Criterion (capped at 10%)
- Auto balance check before every trade
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from zoneinfo import ZoneInfo

import numpy as np

from utils import format_ist_timestamp, get_current_ist_time, format_currency

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class Position:
    """Tracks an open intraday position."""
    symbol: str
    direction: str         # "LONG" or "SHORT"
    entry_price: float
    quantity: int
    stop_loss: float
    target_1: float
    target_2: float
    atr: float
    entry_time: str = ""
    trailing_active: bool = False
    trailing_stop: float = 0.0
    current_price: float = 0.0
    max_price: float = 0.0   # For LONG trailing
    min_price: float = 0.0   # For SHORT trailing
    order_id: str = ""
    sl_order_id: str = ""
    partial_exit_done: bool = False  # Legacy — kept for compatibility
    # 50/30/20 Partial exit tracking
    quality_grade: str = "B"         # A+, A, B, C — affects trail aggressiveness
    t1_qty: int = 0                  # Qty to exit at T1 (50%)
    t2_qty: int = 0                  # Qty to exit at T2 (30%)
    runner_qty: int = 0              # Runner qty (20%) with tight trailing
    t1_done: bool = False
    t2_done: bool = False
    size_multiplier: float = 1.0     # From HighAccuracyFilter

    def __post_init__(self):
        if not self.entry_time:
            self.entry_time = format_ist_timestamp()
        if self.current_price == 0:
            self.current_price = self.entry_price
        if self.max_price == 0:
            self.max_price = self.entry_price
        if self.min_price == 0:
            self.min_price = self.entry_price

    @property
    def pnl(self) -> float:
        if self.direction == "LONG":
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0
        return (self.pnl / (self.entry_price * self.quantity)) * 100

    @property
    def risk_amount(self) -> float:
        return abs(self.entry_price - self.stop_loss) * self.quantity

    @property
    def active_sl(self) -> float:
        """Current effective stop loss (trailing or original)."""
        return self.trailing_stop if self.trailing_active else self.stop_loss


@dataclass
class RiskState:
    """Daily risk tracking state — resets each morning."""
    date: str = ""
    daily_capital: float = 0.0
    available_capital: float = 0.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    trading_paused: bool = False
    pause_reason: str = ""
    pause_until: Optional[datetime] = None
    circuit_breaker_active: bool = False
    nifty_open: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    closed_trades: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.date:
            self.date = get_current_ist_time().strftime("%Y-%m-%d")
        if not self.available_capital:
            self.available_capital = self.daily_capital

    @property
    def win_rate(self) -> float:
        total = self.winning_trades + self.losing_trades
        return (self.winning_trades / total * 100) if total > 0 else 0.0

    @property
    def daily_loss_pct(self) -> float:
        if self.daily_capital == 0:
            return 0.0
        return (abs(min(self.daily_pnl, 0)) / self.daily_capital) * 100

    @property
    def portfolio_heat(self) -> float:
        """Total risk across all open positions as % of capital."""
        total_risk = sum(p.risk_amount for p in self.positions.values())
        return (total_risk / max(self.daily_capital, 1)) * 100


class RiskManager:
    """
    Central risk management for the trading bot.
    Controls position sizing, stop management, and circuit breakers.
    """

    def __init__(
        self,
        max_daily_capital: float = 50000,
        max_risk_pct: float = 0.5,
        daily_loss_limit_pct: float = 2.0,
        max_positions: int = 10,
        nifty_circuit_pct: float = 2.0,
        consecutive_loss_limit: int = 3,
        pause_minutes: int = 30,
    ):
        self.max_daily_capital = max_daily_capital
        self.max_risk_pct = min(max_risk_pct, 1.0)   # Hard cap at 1%
        self.daily_loss_limit_pct = min(daily_loss_limit_pct, 3.0)
        self.max_positions = max_positions
        self.nifty_circuit_pct = nifty_circuit_pct
        self.consecutive_loss_limit = consecutive_loss_limit
        self.pause_minutes = pause_minutes

        self.state = RiskState()
        self._available_balance: float = 0.0

        # Institutional intelligence multipliers (set by main.py each morning)
        self._fii_mult:  float = 1.0   # FII/DII flow: 0.5–1.5×
        self._oc_mult:   float = 1.0   # Option chain bias: 0.9–1.1×
        self._inst_mult: float = 1.0   # Combined: fii_mult × oc_mult

        logger.info(
            f"[{format_ist_timestamp()}] RiskManager initialized | "
            f"Capital: {format_currency(max_daily_capital)} | "
            f"Max risk/trade: {max_risk_pct}% | "
            f"Daily loss limit: {daily_loss_limit_pct}%"
        )

    # --------------------------------------------------------
    # DAILY INITIALIZATION
    # --------------------------------------------------------

    def initialize_day(self, available_balance: float, nifty_open: float = 0):
        """Call this at market open (9:15 AM IST) each day."""
        now_ist = get_current_ist_time()
        cap = min(available_balance, self.max_daily_capital)
        self.state = RiskState(
            date=now_ist.strftime("%Y-%m-%d"),
            daily_capital=cap,
            available_capital=cap,
            nifty_open=nifty_open,
        )
        self._available_balance = available_balance
        logger.info(
            f"[{format_ist_timestamp()}] Day initialized | "
            f"Capital: {format_currency(cap)} | "
            f"Nifty open: {nifty_open:.2f}"
        )

    def update_balance(self, balance: float):
        """Update available balance (called before each trade).
        Guards against 0 / negative: if balance API is offline or returns an
        empty response, keep the last known valid capital so position sizing
        and can_take_trade() continue using the initialised day capital.
        """
        if balance <= 0:
            logger.debug(
                f"[{format_ist_timestamp()}] update_balance: ignoring 0 — "
                f"keeping ₹{self._available_balance:,.0f} (API may be offline)"
            )
            return
        self._available_balance = balance
        effective_capital = min(balance, self.max_daily_capital)
        self.state.available_capital = effective_capital
        if effective_capital != self.state.daily_capital:
            self.state.daily_capital = effective_capital

    def set_institutional_multiplier(
        self, fii_mult: float = 1.0, oc_mult: float = 1.0
    ) -> None:
        """
        Set institutional flow multipliers.
        Called by main.py each morning after FII/DII and option chain analysis.

        fii_mult: from FIIDIITracker.get_position_size_multiplier() — 0.5 to 1.5
        oc_mult:  from option chain bias — 0.9 (bearish) / 1.0 (neutral) / 1.1 (bullish)

        Combined effect on position size: fii_mult × oc_mult (capped 0.5–1.5)
        """
        self._fii_mult  = max(0.5, min(fii_mult, 1.5))
        self._oc_mult   = max(0.8, min(oc_mult, 1.2))
        self._inst_mult = max(0.5, min(self._fii_mult * self._oc_mult, 1.5))
        logger.info(
            f"[{format_ist_timestamp()}] Institutional multiplier set: "
            f"FII={self._fii_mult:.2f}× OC={self._oc_mult:.2f}× "
            f"→ Combined={self._inst_mult:.2f}×"
        )

    # --------------------------------------------------------
    # SESSION MULTIPLIER
    # --------------------------------------------------------

    def _get_session_multiplier(self) -> Tuple[float, str]:
        """
        Session-based position size multiplier.
        18yr Rule: 60% of intraday P&L comes from the opening drive (9:15-10:00).
        Midday (11:00-13:30) is a trap — algos chop retail to death.

        Returns (multiplier, session_name)
        """
        now = get_current_ist_time()
        h, m = now.hour, now.minute
        total_min = h * 60 + m

        if 555 <= total_min < 600:    # 09:15-10:00 Opening drive
            return 1.0, "OPENING_DRIVE"
        elif 600 <= total_min < 660:  # 10:00-11:00 Morning session
            return 0.80, "MORNING"
        elif 660 <= total_min < 810:  # 11:00-13:30 Midday chop
            return 0.50, "MIDDAY_CHOP"
        elif 810 <= total_min < 900:  # 13:30-15:00 Afternoon trend
            return 0.80, "AFTERNOON"
        elif 900 <= total_min < 920:  # 15:00-15:20 Closing risk
            return 0.30, "CLOSING"
        else:
            return 0.0, "AFTER_HOURS"

    # --------------------------------------------------------
    # DYNAMIC KELLY CRITERION
    # --------------------------------------------------------

    def _dynamic_kelly_fraction(self) -> float:
        """
        Dynamic Kelly using the last 20 closed trades (updates intraday).
        Falls back to 0.55 win-rate estimate if fewer than 5 trades.

        Kelly% = W - (1-W)/R  where R = avg_win / avg_loss
        Capped at 25% to prevent overbetting.
        """
        recent = self.state.closed_trades[-20:]
        if len(recent) < 5:
            return 0.55   # Fallback

        wins   = [t for t in recent if t.get("pnl", 0) > 0]
        losses = [t for t in recent if t.get("pnl", 0) <= 0]
        if not wins or not losses:
            return 0.55

        wr      = len(wins) / len(recent)
        avg_win = abs(sum(t["pnl"] for t in wins)   / len(wins))
        avg_los = abs(sum(t["pnl"] for t in losses) / len(losses))
        if avg_los < 1:
            return 0.55

        kelly = wr - (1 - wr) / (avg_win / avg_los)
        return max(0.10, min(kelly, 0.25))   # Clamp 10-25%

    # --------------------------------------------------------
    # SECTOR CORRELATION GUARD
    # --------------------------------------------------------

    def _check_sector_correlation(self, symbol: str) -> Dict:
        """
        Prevent more than 2 open positions in the same sector.
        18yr Rule: "Sector correlation kills diversification. 3 bank stocks
        in a bank rout = 3× the loss."
        """
        try:
            from nse_data import get_sector
            sector = get_sector(symbol)
            if sector == "OTHER":
                return {"allowed": True, "reason": "Unknown sector — allowing"}

            same_sector = [
                s for s in self.state.positions
                if get_sector(s) == sector
            ]
            max_per_sector = getattr(config, "MAX_POSITIONS_PER_SECTOR", 2)
            if len(same_sector) >= max_per_sector:
                return {
                    "allowed": False,
                    "reason": (
                        f"Sector limit: already {len(same_sector)} open in "
                        f"{sector} ({', '.join(same_sector)})"
                    ),
                }
        except Exception:
            pass
        return {"allowed": True, "reason": "Sector check passed"}

    # --------------------------------------------------------
    # PORTFOLIO VAR
    # --------------------------------------------------------

    def calculate_portfolio_var(self, confidence: float = 0.95) -> Dict:
        """
        Calculate Value at Risk across all open positions.
        Uses ATR as a 1-day volatility proxy for each position.

        Returns:
          var_95:  95% 1-day Value at Risk in ₹
          cvar_95: Conditional VaR (expected loss beyond VaR)
          heat_pct: Portfolio heat (% of capital at risk via SL)
        """
        if not self.state.positions:
            return {"var_95": 0, "cvar_95": 0, "heat_pct": 0}

        losses = []
        for pos in self.state.positions.values():
            # Max loss = SL distance × quantity
            sl_loss = abs(pos.entry_price - pos.active_sl) * pos.quantity
            # Simulate 100 scenarios using ATR
            atr = pos.atr if pos.atr > 0 else pos.entry_price * 0.005
            scenarios = np.random.normal(-atr * pos.quantity, atr * pos.quantity * 0.5, 1000)
            losses.extend(scenarios.tolist())

        if not losses:
            return {"var_95": 0, "cvar_95": 0, "heat_pct": 0}

        loss_arr = np.array(losses)
        var_95   = float(np.percentile(-loss_arr, 95))
        cvar_95  = float(np.mean(-loss_arr[-loss_arr >= var_95])) if var_95 > 0 else 0

        return {
            "var_95":   round(max(var_95, 0), 2),
            "cvar_95":  round(max(cvar_95, 0), 2),
            "heat_pct": round(self.state.portfolio_heat, 2),
        }

    # --------------------------------------------------------
    # POSITION SIZING
    # --------------------------------------------------------

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        direction: str = "LONG",
        win_rate_estimate: float = 0.55,
    ) -> Dict:
        """
        Multi-layer position sizing:
          1. Risk-based (primary): risk_pct × capital / SL_distance
          2. Dynamic Kelly (secondary): uses last 20 trades
          3. Session multiplier: reduce size during midday chop
          4. Institutional multiplier: FII/DII + Option Chain
          5. Portfolio heat cap: no trade if total heat > MAX_PORTFOLIO_HEAT
          6. Capital cap: max 15% per position
        """
        import config as _cfg
        capital = self.state.daily_capital
        if capital <= 0 or entry_price <= 0:
            return {"quantity": 0, "reason": "Insufficient capital"}

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return {"quantity": 0, "reason": "Invalid SL distance"}

        # 1. Risk-based sizing
        risk_amount = capital * (self.max_risk_pct / 100)
        risk_qty    = int(risk_amount / sl_distance)

        # 2. Dynamic Kelly
        kelly_frac  = self._dynamic_kelly_fraction()
        kelly_qty   = int((capital * kelly_frac) / entry_price)

        # More conservative of the two
        quantity = min(risk_qty, kelly_qty) if kelly_qty > 0 else risk_qty

        # 3. Session multiplier (reduce during midday, closing)
        sess_mult, session = self._get_session_multiplier()
        quantity = max(1, int(quantity * sess_mult))

        # 4a. Day-of-week multiplier (4-day profit optimizer)
        now_ist    = get_current_ist_time()
        dow        = now_ist.weekday()   # 0=Mon … 4=Fri
        dow_mults  = getattr(_cfg, "DOW_SIZE_MULTIPLIERS", {})
        dow_mult   = dow_mults.get(dow, 1.0)
        quantity   = max(1, int(quantity * dow_mult))

        # 4b. Institutional multiplier (FII/DII + Option Chain)
        inst_mult = getattr(self, "_inst_mult", 1.0)
        quantity  = max(1, int(quantity * inst_mult))

        # 5. Portfolio heat cap
        max_portfolio_heat = getattr(_cfg, "MAX_PORTFOLIO_HEAT_PCT", 3.0)
        current_heat       = self.state.portfolio_heat
        if current_heat >= max_portfolio_heat:
            return {
                "quantity": 0,
                "reason": (
                    f"Portfolio heat {current_heat:.1f}% ≥ "
                    f"max {max_portfolio_heat}% — no new entries"
                ),
            }

        # 6. Capital cap: max 15% per position
        max_by_capital = int((capital * 0.15) / entry_price)
        quantity = min(quantity, max_by_capital)
        quantity = max(quantity, 1)

        capital_used = entry_price * quantity
        actual_risk  = sl_distance * quantity

        return {
            "quantity":    quantity,
            "risk_amount": round(actual_risk, 2),
            "capital_used":round(capital_used, 2),
            "capital_pct": round(capital_used / capital * 100, 1),
            "risk_pct":    round(actual_risk  / capital * 100, 2),
            "risk_qty":    risk_qty,
            "kelly_qty":   kelly_qty,
            "kelly_frac":  round(kelly_frac, 3),
            "sl_distance": round(sl_distance, 2),
            "sess_mult":   round(sess_mult, 2),
            "session":     session,
            "dow_mult":    round(dow_mult, 2),
            "inst_mult":   round(inst_mult, 2),
            "heat_pct":    round(self.state.portfolio_heat, 2),
        }

    # --------------------------------------------------------
    # PRE-TRADE CHECKS
    # --------------------------------------------------------

    def can_take_trade(self, symbol: str, direction: str = "LONG") -> Dict:
        """
        Check all risk conditions before entering a trade.
        Returns {"allowed": bool, "reason": str}
        """
        # 1. Trading paused?
        if self.state.trading_paused:
            if self.state.pause_until:
                now = get_current_ist_time()
                if now < self.state.pause_until:
                    remaining = (self.state.pause_until - now).seconds // 60
                    return {"allowed": False, "reason": f"Trading paused — {remaining}min remaining ({self.state.pause_reason})"}
                else:
                    # Pause expired
                    self.resume_trading(auto=True)
            else:
                return {"allowed": False, "reason": f"Trading paused: {self.state.pause_reason}"}

        # 2. Circuit breaker active?
        if self.state.circuit_breaker_active:
            return {"allowed": False, "reason": "Circuit breaker active — no new entries"}

        # 3. Daily loss limit?
        if self.state.daily_loss_pct >= self.daily_loss_limit_pct:
            self._trigger_circuit_breaker(f"Daily loss limit {self.daily_loss_limit_pct}% hit")
            return {"allowed": False, "reason": f"Daily loss limit {self.daily_loss_limit_pct}% reached"}

        # 4. Max positions?
        open_positions = len(self.state.positions)
        if open_positions >= self.max_positions:
            return {"allowed": False, "reason": f"Max positions ({self.max_positions}) reached"}

        # 5. Already have position in this symbol?
        if symbol in self.state.positions:
            return {"allowed": False, "reason": f"Already have open position in {symbol}"}

        # 6. Sufficient capital?
        if self._available_balance < 1000:
            return {"allowed": False, "reason": "Insufficient balance"}

        # 7. Portfolio heat limit
        import config as _cfg
        max_heat = getattr(_cfg, "MAX_PORTFOLIO_HEAT_PCT", 3.0)
        current_heat = self.state.portfolio_heat
        if current_heat >= max_heat:
            return {
                "allowed": False,
                "reason": f"Portfolio heat {current_heat:.1f}% ≥ limit {max_heat}%",
            }

        # 8. Sector correlation guard (max N positions per sector)
        sector_check = self._check_sector_correlation(symbol)
        if not sector_check["allowed"]:
            return sector_check

        # 9. Session gate — no new entries after 3:00 PM IST
        sess_mult, session = self._get_session_multiplier()
        if sess_mult == 0.0:
            return {"allowed": False, "reason": f"Session gate: {session}"}

        return {"allowed": True, "reason": f"All checks passed | Session={session}"}

    # --------------------------------------------------------
    # TRAILING STOP MANAGEMENT
    # --------------------------------------------------------

    def update_trailing_stop(self, position: Position, current_price: float) -> Dict:
        """
        Update trailing stop with 50/30/20 partial exit strategy.

        Exit sequence:
          T1 hit → exit 50% (t1_qty), move SL to entry (breakeven)
          T2 hit → exit 30% (t2_qty), activate tight runner trail
          Runner → 20% rides with grade-adaptive trailing stop
          T2 full → exit remaining if T2 not done (grade C: exit all)

        Trail aggressiveness by grade:
          A+ / A: trail at 0.5x ATR (let winner run)
          B:      trail at 0.8x ATR
          C:      trail at 1.0x ATR (tighter — lower conviction)

        Returns:
            {action, new_sl, reason, exit_qty}
        """
        from config import ATR_TRAIL_MULTIPLIER
        position.current_price = current_price
        atr = position.atr

        # Grade-adaptive trail distance
        grade_trail = {
            "A+": ATR_TRAIL_MULTIPLIER * 0.6,   # Tightest — best setups deserve room
            "A":  ATR_TRAIL_MULTIPLIER * 0.8,
            "B":  ATR_TRAIL_MULTIPLIER,
            "C":  ATR_TRAIL_MULTIPLIER * 1.3,   # Widest — low conviction, protect early
        }
        trail_dist = grade_trail.get(position.quality_grade, ATR_TRAIL_MULTIPLIER) * atr

        if position.direction == "LONG":
            position.max_price = max(position.max_price, current_price)
            profit = current_price - position.entry_price

            # Hard SL check (original or trailing)
            active_sl = position.trailing_stop if position.trailing_active else position.stop_loss
            if current_price <= active_sl:
                remaining = position.quantity  # Exit whatever's left
                return {
                    "action": "EXIT", "new_sl": active_sl, "exit_qty": remaining,
                    "reason": f"{'Trailing' if position.trailing_active else 'Original'} SL hit ₹{current_price:.2f}"
                }

            # ── T1: 50% exit at Target 1 ──────────────────────
            if not position.t1_done and current_price >= position.target_1:
                position.t1_done = True
                position.partial_exit_done = True
                # Move SL to breakeven after T1
                if position.direction == "LONG":
                    position.stop_loss = max(position.stop_loss, position.entry_price)
                return {
                    "action": "PARTIAL_EXIT_T1",
                    "new_sl": position.entry_price,
                    "exit_qty": position.t1_qty,
                    "reason": f"T1 hit ₹{position.target_1:.2f} — exit {position.t1_qty} qty (50%), SL→breakeven"
                }

            # ── T2: 30% exit at Target 2 ──────────────────────
            if position.t1_done and not position.t2_done and current_price >= position.target_2:
                position.t2_done = True
                # Grade C: exit all at T2 (no runner)
                if position.quality_grade == "C" or position.runner_qty == 0:
                    return {
                        "action": "EXIT", "new_sl": current_price,
                        "exit_qty": position.t2_qty + position.runner_qty,
                        "reason": f"T2 hit ₹{position.target_2:.2f} — exit all remaining"
                    }
                # Grade A+/A/B: activate runner trailing
                position.trailing_active = True
                position.trailing_stop   = current_price - trail_dist
                logger.info(
                    f"[{format_ist_timestamp()}] {position.symbol}: "
                    f"T2 hit — runner trail activated ₹{position.trailing_stop:.2f}"
                )
                return {
                    "action": "PARTIAL_EXIT_T2",
                    "new_sl": position.trailing_stop,
                    "exit_qty": position.t2_qty,
                    "reason": f"T2 hit ₹{position.target_2:.2f} — exit {position.t2_qty} qty (30%), runner active"
                }

            # ── Runner trailing stop (post-T2) ────────────────
            if position.trailing_active and position.t2_done:
                new_trail = position.max_price - trail_dist
                if new_trail > position.trailing_stop:
                    position.trailing_stop = new_trail
                    return {
                        "action": "UPDATE_SL", "new_sl": new_trail, "exit_qty": 0,
                        "reason": f"Runner trail ₹{new_trail:.2f} (grade {position.quality_grade})"
                    }
                if current_price <= position.trailing_stop:
                    return {
                        "action": "EXIT", "new_sl": position.trailing_stop,
                        "exit_qty": position.runner_qty,
                        "reason": f"Runner trail hit ₹{current_price:.2f} — grade {position.quality_grade}"
                    }

            # ── Pre-T1: activate trailing if 1x ATR in profit ─
            if not position.t1_done and not position.trailing_active and profit >= atr:
                position.trailing_active = True
                position.trailing_stop   = current_price - trail_dist
                logger.info(
                    f"[{format_ist_timestamp()}] {position.symbol}: "
                    f"Early trail activated at ₹{position.trailing_stop:.2f}"
                )

        else:  # SHORT
            position.min_price = min(position.min_price, current_price)
            profit = position.entry_price - current_price

            active_sl = position.trailing_stop if position.trailing_active else position.stop_loss
            if current_price >= active_sl:
                return {
                    "action": "EXIT", "new_sl": active_sl, "exit_qty": position.quantity,
                    "reason": f"Short {'trailing' if position.trailing_active else 'original'} SL hit ₹{current_price:.2f}"
                }

            # T1: 50%
            if not position.t1_done and current_price <= position.target_1:
                position.t1_done = True
                position.partial_exit_done = True
                position.stop_loss = min(position.stop_loss, position.entry_price)
                return {
                    "action": "PARTIAL_EXIT_T1",
                    "new_sl": position.entry_price,
                    "exit_qty": position.t1_qty,
                    "reason": f"Short T1 hit ₹{position.target_1:.2f} — 50% exit, SL→breakeven"
                }

            # T2: 30%
            if position.t1_done and not position.t2_done and current_price <= position.target_2:
                position.t2_done = True
                if position.quality_grade == "C" or position.runner_qty == 0:
                    return {
                        "action": "EXIT", "new_sl": current_price,
                        "exit_qty": position.t2_qty + position.runner_qty,
                        "reason": f"Short T2 hit ₹{position.target_2:.2f} — full exit (grade C)"
                    }
                position.trailing_active = True
                position.trailing_stop   = current_price + trail_dist
                return {
                    "action": "PARTIAL_EXIT_T2",
                    "new_sl": position.trailing_stop,
                    "exit_qty": position.t2_qty,
                    "reason": f"Short T2 hit ₹{position.target_2:.2f} — 30% exit, runner active"
                }

            # Runner
            if position.trailing_active and position.t2_done:
                new_trail = position.min_price + trail_dist
                if new_trail < position.trailing_stop:
                    position.trailing_stop = new_trail
                    return {
                        "action": "UPDATE_SL", "new_sl": new_trail, "exit_qty": 0,
                        "reason": f"Short runner trail ₹{new_trail:.2f}"
                    }
                if current_price >= position.trailing_stop:
                    return {
                        "action": "EXIT", "new_sl": position.trailing_stop,
                        "exit_qty": position.runner_qty,
                        "reason": f"Short runner trail hit ₹{current_price:.2f}"
                    }

            if not position.t1_done and not position.trailing_active and profit >= atr:
                position.trailing_active = True
                position.trailing_stop   = current_price + trail_dist

        return {"action": "HOLD", "new_sl": position.active_sl, "exit_qty": 0, "reason": "Hold"}

    # --------------------------------------------------------
    # POSITION TRACKING
    # --------------------------------------------------------

    def setup_partial_exits(self, position: Position) -> Position:
        """
        Calculate 50/30/20 partial exit quantities.
        18yr rule: Take half off at T1 to guarantee profit, let runner work.
        A+ grade: runner stays alive longer with tighter trail.
        """
        from config import PARTIAL_EXIT_T1_PCT, PARTIAL_EXIT_T2_PCT, RUNNER_PCT
        qty = position.quantity
        t1_qty = max(1, round(qty * PARTIAL_EXIT_T1_PCT / 100))
        t2_qty = max(1, round(qty * PARTIAL_EXIT_T2_PCT / 100))
        runner_qty = max(0, qty - t1_qty - t2_qty)
        position.t1_qty     = t1_qty
        position.t2_qty     = t2_qty
        position.runner_qty = runner_qty
        logger.info(
            f"[{format_ist_timestamp()}] {position.symbol} partial exits: "
            f"T1={t1_qty}qty({PARTIAL_EXIT_T1_PCT:.0f}%) "
            f"T2={t2_qty}qty({PARTIAL_EXIT_T2_PCT:.0f}%) "
            f"Runner={runner_qty}qty | Grade={position.quality_grade}"
        )
        return position

    # --------------------------------------------------------
    # SMART TRADE HEALTH MONITOR
    # --------------------------------------------------------

    def _position_age_minutes(self, pos: Position) -> float:
        """Return how many minutes this position has been open."""
        try:
            now = get_current_ist_time()
            entry_str = pos.entry_time[:19]   # trim trailing " IST" or zone suffix
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    entry_dt = datetime.strptime(entry_str, fmt).replace(tzinfo=IST)
                    return max(0.0, (now - entry_dt).total_seconds() / 60)
                except ValueError:
                    continue
            # ISO fallback
            entry_dt = datetime.fromisoformat(pos.entry_time)
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=IST)
            return max(0.0, (now - entry_dt).total_seconds() / 60)
        except Exception:
            return 0.0

    def check_position_health(
        self,
        pos: Position,
        ltp: float,
        recent_candles: Optional[list] = None,
    ) -> Dict:
        """
        Smart trade health monitor — exit BEFORE stop-loss if trade shows weakness.
        18yr Rule: "A small profit is better than a break-even; a break-even is better
        than a loss. Exit ugly trades early."

        Rules (checked in priority order):
          1. Peak reversal  — was +1x ATR positive, now ≤0             → EXIT_NOW
          2. Early adverse  — moved 0.6× SL-distance against us        → EXIT_NOW
          3. Stalled trade  — 20+ min with <0.3x ATR progress          → EXIT_NOW
          4. Time gate      — 35+ min old, <30% toward T1              → EXIT_NOW
          5. Break-even     — up 0.8x ATR → move SL to entry           → BREAK_EVEN
          6. Candle reversal— 2 consecutive opposing candles (pre-T1)   → EXIT_NOW

        Returns: {"action": "HOLD"|"EXIT_NOW"|"BREAK_EVEN", "reason": str,
                  "new_sl": float (only for BREAK_EVEN)}
        """
        atr = pos.atr if pos.atr > 0 else pos.entry_price * 0.005

        if pos.direction == "LONG":
            move      = ltp - pos.entry_price           # +ve = profit
            t1_dist   = pos.target_1 - pos.entry_price
            peak_move = pos.max_price - pos.entry_price
            sl_dist   = pos.entry_price - pos.stop_loss
        else:
            move      = pos.entry_price - ltp
            t1_dist   = pos.entry_price - pos.target_1
            peak_move = pos.entry_price - pos.min_price
            sl_dist   = pos.stop_loss - pos.entry_price

        sl_dist   = max(sl_dist, atr * 0.1)   # guard against zero
        age_min   = self._position_age_minutes(pos)

        # ── Rule 1: Peak reversal ─────────────────────────────────────────
        # Was strongly positive, now flat or losing — momentum has reversed.
        if peak_move >= atr and move <= 0 and not pos.t1_done:
            return {
                "action": "EXIT_NOW",
                "reason": (
                    f"Peak reversal: peaked +{peak_move:.2f} "
                    f"({peak_move / atr:.1f}× ATR), now {move:+.2f} — exit before loss"
                ),
            }

        # ── Rule 2: Early adverse move ────────────────────────────────────
        # Moved 60% of SL distance against us without trailing active.
        if move < -(0.6 * sl_dist) and not pos.trailing_active and not pos.t1_done:
            return {
                "action": "EXIT_NOW",
                "reason": (
                    f"Early exit: {abs(move):.2f} adverse "
                    f"({abs(move) / atr:.1f}× ATR) before SL hit"
                ),
            }

        # ── Rule 3: Stalled trade ─────────────────────────────────────────
        # 20+ minutes open, barely moved — capital is better deployed elsewhere.
        if age_min >= 20 and 0 <= move < (0.3 * atr) and not pos.t1_done:
            return {
                "action": "EXIT_NOW",
                "reason": (
                    f"Stalled {age_min:.0f}min: only {move:.2f} progress "
                    f"({move / atr:.2f}× ATR) — releasing capital"
                ),
            }

        # ── Rule 4: Time gate ─────────────────────────────────────────────
        # 35 minutes in with less than 30% progress toward first target.
        if age_min >= 35 and t1_dist > 0 and (move / t1_dist) < 0.30 and not pos.t1_done:
            return {
                "action": "EXIT_NOW",
                "reason": (
                    f"Time exit: {age_min:.0f}min, "
                    f"only {move / t1_dist * 100:.0f}% toward T1 ₹{pos.target_1:.2f}"
                ),
            }

        # ── Rule 5: Break-even protection ────────────────────────────────
        # Up 0.8× ATR — lock in no-loss with break-even SL.
        if move >= (0.8 * atr) and not pos.trailing_active:
            be = pos.entry_price
            current_sl = pos.stop_loss
            if pos.direction == "LONG" and current_sl < be:
                return {
                    "action": "BREAK_EVEN",
                    "new_sl": be,
                    "reason": (
                        f"Break-even: up {move:.2f} ({move / atr:.1f}× ATR) "
                        f"→ SL moved to entry ₹{be:.2f}"
                    ),
                }
            elif pos.direction == "SHORT" and current_sl > be:
                return {
                    "action": "BREAK_EVEN",
                    "new_sl": be,
                    "reason": (
                        f"Break-even: up {move:.2f} ({move / atr:.1f}× ATR) "
                        f"→ SL moved to entry ₹{be:.2f}"
                    ),
                }

        # ── Rule 6: Two consecutive opposing candles ──────────────────────
        # Momentum fading before T1 — close while still in marginal profit.
        if recent_candles and len(recent_candles) >= 2 and not pos.t1_done:
            try:
                c1 = recent_candles[-2]
                c2 = recent_candles[-1]
                if pos.direction == "LONG":
                    bear1 = float(c1["close"]) < float(c1["open"])
                    bear2 = float(c2["close"]) < float(c2["open"])
                    if bear1 and bear2 and move < atr:
                        return {
                            "action": "EXIT_NOW",
                            "reason": "2 consecutive bearish candles — long momentum fading",
                        }
                else:
                    bull1 = float(c1["close"]) > float(c1["open"])
                    bull2 = float(c2["close"]) > float(c2["open"])
                    if bull1 and bull2 and move < atr:
                        return {
                            "action": "EXIT_NOW",
                            "reason": "2 consecutive bullish candles — short momentum fading",
                        }
            except Exception:
                pass

        return {"action": "HOLD", "reason": "Trade health OK"}

    def add_position(self, position: Position):
        """Register a new open position."""
        position = self.setup_partial_exits(position)
        self.state.positions[position.symbol] = position
        logger.info(
            f"[{format_ist_timestamp()}] Position opened: "
            f"{position.direction} {position.symbol} x{position.quantity} "
            f"@ ₹{position.entry_price:.2f} | "
            f"SL: ₹{position.stop_loss:.2f} | "
            f"T1: ₹{position.target_1:.2f}"
        )

    def close_position(self, symbol: str, exit_price: float, reason: str = ""):
        """Close and record a position."""
        if symbol not in self.state.positions:
            return None
        pos = self.state.positions.pop(symbol)
        pos.current_price = exit_price
        pnl = pos.pnl
        self.state.daily_pnl += pnl
        self.state.daily_trades += 1

        if pnl > 0:
            self.state.winning_trades += 1
            self.state.consecutive_losses = 0
        else:
            self.state.losing_trades += 1
            self.state.consecutive_losses += 1
            self.state.max_consecutive_losses = max(
                self.state.max_consecutive_losses,
                self.state.consecutive_losses
            )
            # Consecutive loss pause
            if self.state.consecutive_losses >= self.consecutive_loss_limit:
                self._pause_trading(
                    f"{self.consecutive_loss_limit} consecutive losses",
                    minutes=self.pause_minutes
                )

        # Update peak and drawdown
        self.state.peak_pnl = max(self.state.peak_pnl, self.state.daily_pnl)
        drawdown = self.state.peak_pnl - self.state.daily_pnl
        self.state.max_drawdown = max(self.state.max_drawdown, drawdown)

        trade_record = {
            "symbol": symbol,
            "direction": pos.direction,
            "entry": pos.entry_price,
            "exit": exit_price,
            "quantity": pos.quantity,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pos.pnl_pct, 2),
            "entry_time": pos.entry_time,
            "exit_time": format_ist_timestamp(),
            "reason": reason,
        }
        self.state.closed_trades.append(trade_record)

        logger.info(
            f"[{format_ist_timestamp()}] Position closed: {symbol} | "
            f"P&L: {format_currency(pnl)} ({pos.pnl_pct:+.2f}%) | "
            f"Reason: {reason} | Daily P&L: {format_currency(self.state.daily_pnl)}"
        )
        return trade_record

    # --------------------------------------------------------
    # CIRCUIT BREAKERS
    # --------------------------------------------------------

    def check_nifty_circuit(self, nifty_current: float):
        """Pause new entries if Nifty moves >2% from open."""
        if self.state.nifty_open == 0:
            return
        nifty_move = abs((nifty_current - self.state.nifty_open) / self.state.nifty_open) * 100
        if nifty_move >= self.nifty_circuit_pct:
            direction = "UP" if nifty_current > self.state.nifty_open else "DOWN"
            self._trigger_circuit_breaker(
                f"Nifty moved {nifty_move:.1f}% {direction} from open"
            )

    def _trigger_circuit_breaker(self, reason: str):
        if not self.state.circuit_breaker_active:
            self.state.circuit_breaker_active = True
            self.state.trading_paused = True
            self.state.pause_reason = f"CIRCUIT BREAKER: {reason}"
            logger.warning(
                f"[{format_ist_timestamp()}] 🚨 CIRCUIT BREAKER ACTIVATED: {reason}"
            )

    def _pause_trading(self, reason: str, minutes: int = 30):
        now = get_current_ist_time()
        self.state.trading_paused = True
        self.state.pause_reason = reason
        self.state.pause_until = now + timedelta(minutes=minutes)
        logger.warning(
            f"[{format_ist_timestamp()}] ⏸ Trading PAUSED for {minutes}min: {reason}"
        )

    def resume_trading(self, auto: bool = False):
        self.state.trading_paused = False
        self.state.pause_reason = ""
        self.state.pause_until = None
        if not self.state.circuit_breaker_active:
            msg = "auto-resumed" if auto else "manually resumed"
            logger.info(f"[{format_ist_timestamp()}] ▶️ Trading {msg}")

    def manual_resume(self):
        """Manual resume via Telegram /resume command."""
        self.state.circuit_breaker_active = False
        self.resume_trading()
        logger.info(f"[{format_ist_timestamp()}] ▶️ Trading manually resumed via Telegram")

    def emergency_stop(self):
        """Kill switch — called by /kill Telegram command."""
        self._trigger_circuit_breaker("MANUAL KILL SWITCH")
        logger.critical(f"[{format_ist_timestamp()}] 🛑 EMERGENCY STOP ACTIVATED")

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    def get_daily_summary(self) -> Dict:
        """Get today's trading summary."""
        return {
            "date": self.state.date,
            "daily_pnl": round(self.state.daily_pnl, 2),
            "daily_pnl_pct": round((self.state.daily_pnl / max(self.state.daily_capital, 1)) * 100, 2),
            "total_trades": self.state.daily_trades,
            "wins": self.state.winning_trades,
            "losses": self.state.losing_trades,
            "win_rate": round(self.state.win_rate, 1),
            "open_positions": len(self.state.positions),
            "max_drawdown": round(self.state.max_drawdown, 2),
            "consecutive_losses": self.state.consecutive_losses,
            "paused": self.state.trading_paused,
            "circuit_breaker": self.state.circuit_breaker_active,
        }


# ============================================================
# MODULE: pattern_recognition.py
# ============================================================
"""
pattern_recognition.py — NSE Momentum Groww AI Bot
20+ Chart Patterns + Full Indicator Stack

Based on 18+ years of NSE intraday trading experience.
Detects: engulfing, hammer, doji, breakouts, flags, pennants,
         VWAP deviations, divergences, ORB, and more.

All patterns return a confidence score (0–100) and direction (LONG/SHORT/NEUTRAL).
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

try:
    import pandas_ta as ta
except ImportError:
    ta = None
    logging.warning("pandas_ta not installed. Run: pip install pandas-ta")

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)


@dataclass
class PatternResult:
    name: str
    direction: str        # "LONG", "SHORT", "NEUTRAL"
    confidence: float     # 0–100
    description: str
    candle_index: int = -1


@dataclass
class IndicatorSet:
    """Complete indicator values for a single bar."""
    rsi: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    atr: float = 0.0
    vwap: float = 0.0
    bb_upper: float = 0.0
    bb_mid: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    ema9: float = 0.0
    ema21: float = 0.0
    ema50: float = 0.0
    ema200: float = 0.0
    volume_sma: float = 0.0
    volume_ratio: float = 1.0  # current / SMA
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    obv: float = 0.0
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    supertrend: float = 0.0
    supertrend_dir: int = 1   # 1 = bullish, -1 = bearish


class TechnicalIndicators:
    """Computes full indicator stack using pandas_ta."""

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add all technical indicators to a candles DataFrame.
        Input: OHLCV DataFrame. Returns augmented DataFrame.
        """
        if ta is None:
            logger.error("pandas_ta not available — cannot compute indicators")
            return df

        df = df.copy()

        try:
            # RSI
            df["rsi"] = ta.rsi(df["close"], length=14)

            # MACD
            macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
            if macd is not None:
                df["macd"] = macd.iloc[:, 0]
                df["macd_hist"] = macd.iloc[:, 1]
                df["macd_signal"] = macd.iloc[:, 2]

            # ATR
            df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

            # Bollinger Bands
            bb = ta.bbands(df["close"], length=20, std=2)
            if bb is not None:
                df["bb_lower"] = bb.iloc[:, 0]
                df["bb_mid"] = bb.iloc[:, 1]
                df["bb_upper"] = bb.iloc[:, 2]
                df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

            # EMAs
            df["ema9"]  = ta.ema(df["close"], length=9)
            df["ema21"] = ta.ema(df["close"], length=21)
            df["ema50"] = ta.ema(df["close"], length=50)
            df["ema200"]= ta.ema(df["close"], length=200)

            # Volume SMA and ratio
            df["volume_sma"] = ta.sma(df["volume"].astype(float), length=20)
            df["volume_ratio"] = df["volume"] / df["volume_sma"].replace(0, np.nan)

            # Stochastic
            stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3, smooth_k=3)
            if stoch is not None:
                df["stoch_k"] = stoch.iloc[:, 0]
                df["stoch_d"] = stoch.iloc[:, 1]

            # OBV
            df["obv"] = ta.obv(df["close"], df["volume"])

            # ADX / DI
            adx = ta.adx(df["high"], df["low"], df["close"], length=14)
            if adx is not None:
                df["adx"]      = adx.iloc[:, 0]
                df["plus_di"]  = adx.iloc[:, 1]
                df["minus_di"] = adx.iloc[:, 2]

            # VWAP (only meaningful for intraday — resets each day)
            try:
                df["vwap"] = self._calculate_vwap(df)
            except Exception:
                df["vwap"] = df["close"]

            # Supertrend
            try:
                st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3)
                if st is not None:
                    df["supertrend"]     = st.iloc[:, 0]
                    df["supertrend_dir"] = st.iloc[:, 3].apply(lambda x: 1 if x > 0 else -1)
            except Exception:
                df["supertrend"] = df["close"]
                df["supertrend_dir"] = 1

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Indicator compute error: {e}")

        return df.ffill().infer_objects(copy=False).fillna(0)

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate VWAP resetting each trading day (9:15 AM IST).
        VWAP = cumsum(typical_price * volume) / cumsum(volume)
        """
        df = df.copy()
        tp = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"].astype(float)

        # Group by date for daily reset
        if hasattr(df.index, 'date'):
            dates = pd.Series(df.index.date, index=df.index)
            vwap = pd.Series(index=df.index, dtype=float)
            for day in dates.unique():
                mask = dates == day
                day_tp = tp[mask]
                day_vol = vol[mask]
                cum_tpv = (day_tp * day_vol).cumsum()
                cum_vol = day_vol.cumsum()
                vwap[mask] = cum_tpv / cum_vol.replace(0, np.nan)
        else:
            cum_tpv = (tp * vol).cumsum()
            cum_vol = vol.cumsum()
            vwap = cum_tpv / cum_vol.replace(0, np.nan)

        return vwap

    def get_latest_indicators(self, df: pd.DataFrame) -> IndicatorSet:
        """Extract the latest bar's indicator values as IndicatorSet."""
        if df.empty:
            return IndicatorSet()
        row = df.iloc[-1]
        return IndicatorSet(
            rsi=float(row.get("rsi", 50)),
            macd=float(row.get("macd", 0)),
            macd_signal=float(row.get("macd_signal", 0)),
            macd_hist=float(row.get("macd_hist", 0)),
            atr=float(row.get("atr", 0)),
            vwap=float(row.get("vwap", row.get("close", 0))),
            bb_upper=float(row.get("bb_upper", 0)),
            bb_mid=float(row.get("bb_mid", 0)),
            bb_lower=float(row.get("bb_lower", 0)),
            bb_width=float(row.get("bb_width", 0)),
            ema9=float(row.get("ema9", 0)),
            ema21=float(row.get("ema21", 0)),
            ema50=float(row.get("ema50", 0)),
            ema200=float(row.get("ema200", 0)),
            volume_sma=float(row.get("volume_sma", 0)),
            volume_ratio=float(row.get("volume_ratio", 1)),
            stoch_k=float(row.get("stoch_k", 50)),
            stoch_d=float(row.get("stoch_d", 50)),
            obv=float(row.get("obv", 0)),
            adx=float(row.get("adx", 0)),
            plus_di=float(row.get("plus_di", 0)),
            minus_di=float(row.get("minus_di", 0)),
            supertrend=float(row.get("supertrend", row.get("close", 0))),
            supertrend_dir=int(row.get("supertrend_dir", 1)),
        )


class PatternRecognizer:
    """
    Detects 20+ candlestick and chart patterns.
    Each detector returns a PatternResult with confidence 0–100.
    """

    def __init__(self):
        self.indicators = TechnicalIndicators()

    def analyze(self, df: pd.DataFrame) -> Dict:
        """
        Run full pattern analysis on a DataFrame.
        Returns dict with all detected patterns and indicator values.
        """
        if df is None or len(df) < 50:
            return {"patterns": [], "indicators": IndicatorSet(), "score": 0}

        # Add indicators
        df = self.indicators.compute(df)
        ind = self.indicators.get_latest_indicators(df)

        # Detect all patterns
        patterns = []
        # Standard detectors (df, ind) signature
        detectors = [
            # ── Candlestick reversals ──────────────────────────────────────
            self.detect_bullish_engulfing,
            self.detect_bearish_engulfing,
            self.detect_hammer,
            self.detect_shooting_star,
            self.detect_doji,
            self.detect_morning_star,
            self.detect_evening_star,
            self.detect_three_white_soldiers,
            self.detect_three_black_crows,
            self.detect_bullish_harami,
            self.detect_bearish_harami,
            self.detect_piercing_line,
            self.detect_dark_cloud_cover,
            # ── Chart patterns ────────────────────────────────────────────
            self.detect_breakout,
            self.detect_breakdown,
            self.detect_flag_pattern,
            self.detect_pennant_pattern,
            self.detect_bb_squeeze_breakout,
            self.detect_orb_breakout,
            # ── Indicator crossovers ──────────────────────────────────────
            self.detect_vwap_bounce,
            self.detect_vwap_breakdown,
            self.detect_volume_surge_breakout,
            self.detect_rsi_divergence,
            self.detect_macd_crossover,
            self.detect_ema_crossover,
            self.detect_supertrend_signal,
            # ── Elite 18-year patterns (Smart Money / NSE-specific) ───────
            self.detect_fair_value_gap,
            self.detect_order_block,
            self.detect_heikin_ashi_trend,
            self.detect_inside_bar,
            self.detect_double_bottom_top,
            self.detect_market_structure_break,
            self.detect_ema_stack,
        ]

        for detector in detectors:
            try:
                result = detector(df, ind)
                if result and result.confidence >= 40:
                    patterns.append(result)
            except Exception as e:
                logger.debug(f"Pattern detector {detector.__name__} error: {e}")

        # Compute composite score
        score = self._compute_composite_score(patterns, ind)

        return {
            "patterns": patterns,
            "indicators": ind,
            "score": score,
            "long_patterns": [p for p in patterns if p.direction == "LONG"],
            "short_patterns": [p for p in patterns if p.direction == "SHORT"],
        }

    # --------------------------------------------------------
    # CANDLESTICK PATTERNS
    # --------------------------------------------------------

    def detect_bullish_engulfing(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        prev_bearish = prev["close"] < prev["open"]
        curr_bullish = curr["close"] > curr["open"]
        engulfs = curr["open"] < prev["close"] and curr["close"] > prev["open"]
        if not (prev_bearish and curr_bullish and engulfs):
            return None
        body_ratio = abs(curr["close"] - curr["open"]) / max(abs(prev["close"] - prev["open"]), 0.01)
        confidence = min(50 + body_ratio * 10 + (ind.volume_ratio * 5), 90)
        # Stronger in oversold
        if ind.rsi < 40:
            confidence = min(confidence + 10, 95)
        return PatternResult("Bullish Engulfing", "LONG", confidence,
                             f"Bullish engulfing with {body_ratio:.1f}x body ratio")

    def detect_bearish_engulfing(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        prev_bullish = prev["close"] > prev["open"]
        curr_bearish = curr["close"] < curr["open"]
        engulfs = curr["open"] > prev["close"] and curr["close"] < prev["open"]
        if not (prev_bullish and curr_bearish and engulfs):
            return None
        body_ratio = abs(curr["close"] - curr["open"]) / max(abs(prev["close"] - prev["open"]), 0.01)
        confidence = min(50 + body_ratio * 10 + (ind.volume_ratio * 5), 90)
        if ind.rsi > 60:
            confidence = min(confidence + 10, 95)
        return PatternResult("Bearish Engulfing", "SHORT", confidence,
                             f"Bearish engulfing with {body_ratio:.1f}x body ratio")

    def detect_hammer(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        upper_shadow = c["high"] - max(c["close"], c["open"])
        if body / total_range > 0.35:
            return None
        if lower_shadow < 2 * body:
            return None
        if upper_shadow > body:
            return None
        confidence = 65 + (lower_shadow / total_range) * 20
        if ind.rsi < 40:
            confidence = min(confidence + 10, 92)
        return PatternResult("Hammer", "LONG", confidence,
                             f"Hammer — strong rejection at lows, RSI={ind.rsi:.0f}")

    def detect_shooting_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        upper_shadow = c["high"] - max(c["close"], c["open"])
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        if body / total_range > 0.35:
            return None
        if upper_shadow < 2 * body:
            return None
        if lower_shadow > body:
            return None
        confidence = 65 + (upper_shadow / total_range) * 20
        if ind.rsi > 60:
            confidence = min(confidence + 10, 92)
        return PatternResult("Shooting Star", "SHORT", confidence,
                             f"Shooting Star — rejection at highs, RSI={ind.rsi:.0f}")

    def detect_doji(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        if body / total_range > 0.1:
            return None
        return PatternResult("Doji", "NEUTRAL", 60,
                             "Doji — market indecision, watch for breakout")

    def detect_morning_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] < c1["open"] and
                abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3 and
                c3["close"] > c3["open"] and
                c3["close"] > (c1["open"] + c1["close"]) / 2):
            confidence = 75 + (ind.volume_ratio - 1) * 5
            return PatternResult("Morning Star", "LONG", min(confidence, 92),
                                 "3-candle morning star reversal at bottom")
        return None

    def detect_evening_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] > c1["open"] and
                abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3 and
                c3["close"] < c3["open"] and
                c3["close"] < (c1["open"] + c1["close"]) / 2):
            confidence = 75 + (ind.volume_ratio - 1) * 5
            return PatternResult("Evening Star", "SHORT", min(confidence, 92),
                                 "3-candle evening star reversal at top")
        return None

    def detect_three_white_soldiers(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] > c1["open"] and c2["close"] > c2["open"] and c3["close"] > c3["open"] and
                c2["open"] > c1["open"] and c3["open"] > c2["open"] and
                c2["close"] > c1["close"] and c3["close"] > c2["close"]):
            return PatternResult("Three White Soldiers", "LONG", 80,
                                 "Strong 3-candle bullish momentum continuation")
        return None

    def detect_three_black_crows(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] < c1["open"] and c2["close"] < c2["open"] and c3["close"] < c3["open"] and
                c2["open"] < c1["open"] and c3["open"] < c2["open"] and
                c2["close"] < c1["close"] and c3["close"] < c2["close"]):
            return PatternResult("Three Black Crows", "SHORT", 80,
                                 "Strong 3-candle bearish momentum continuation")
        return None

    def detect_bullish_harami(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        if (prev["close"] < prev["open"] and
                curr["close"] > curr["open"] and
                curr["open"] > prev["close"] and curr["close"] < prev["open"]):
            return PatternResult("Bullish Harami", "LONG", 62,
                                 "Bullish harami — potential reversal inside prior bearish candle")
        return None

    def detect_bearish_harami(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        if (prev["close"] > prev["open"] and
                curr["close"] < curr["open"] and
                curr["open"] < prev["close"] and curr["close"] > prev["open"]):
            return PatternResult("Bearish Harami", "SHORT", 62,
                                 "Bearish harami — potential reversal inside prior bullish candle")
        return None

    def detect_piercing_line(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        mid_prev = (prev["open"] + prev["close"]) / 2
        if (prev["close"] < prev["open"] and
                curr["close"] > curr["open"] and
                curr["open"] < prev["close"] and
                curr["close"] > mid_prev and curr["close"] < prev["open"]):
            return PatternResult("Piercing Line", "LONG", 68,
                                 "Piercing line — bullish reversal, price penetrates >50% of prior bearish bar")
        return None

    def detect_dark_cloud_cover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        mid_prev = (prev["open"] + prev["close"]) / 2
        if (prev["close"] > prev["open"] and
                curr["close"] < curr["open"] and
                curr["open"] > prev["close"] and
                curr["close"] < mid_prev and curr["close"] > prev["open"]):
            return PatternResult("Dark Cloud Cover", "SHORT", 68,
                                 "Dark cloud cover — bearish reversal, penetrates >50% of prior bullish bar")
        return None

    # --------------------------------------------------------
    # CHART PATTERNS
    # --------------------------------------------------------

    def detect_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Breakout above recent resistance with volume."""
        if len(df) < 20:
            return None
        recent_high = df["high"].iloc[-20:-1].max()
        curr_close = df["close"].iloc[-1]
        if curr_close > recent_high * 1.001 and ind.volume_ratio >= 1.5:
            breakout_pct = ((curr_close - recent_high) / recent_high) * 100
            confidence = min(60 + breakout_pct * 10 + (ind.volume_ratio - 1) * 15, 92)
            return PatternResult("Resistance Breakout", "LONG", confidence,
                                 f"Breakout above ₹{recent_high:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    def detect_breakdown(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Breakdown below recent support with volume."""
        if len(df) < 20:
            return None
        recent_low = df["low"].iloc[-20:-1].min()
        curr_close = df["close"].iloc[-1]
        if curr_close < recent_low * 0.999 and ind.volume_ratio >= 1.5:
            breakdown_pct = ((recent_low - curr_close) / recent_low) * 100
            confidence = min(60 + breakdown_pct * 10 + (ind.volume_ratio - 1) * 15, 92)
            return PatternResult("Support Breakdown", "SHORT", confidence,
                                 f"Breakdown below ₹{recent_low:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    def detect_flag_pattern(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Bull/bear flag: sharp move then tight consolidation."""
        if len(df) < 15:
            return None
        # Strong move in last 5 bars
        move = df["close"].iloc[-10] - df["close"].iloc[-15]
        atr = ind.atr if ind.atr > 0 else 1
        if abs(move) < 3 * atr:
            return None
        # Tight range in last 5 bars (flag)
        recent = df.iloc[-5:]
        flag_range = recent["high"].max() - recent["low"].min()
        if flag_range > 1.5 * atr:
            return None
        if move > 0:
            return PatternResult("Bull Flag", "LONG", 72,
                                 f"Bull flag: {move:.2f} pole, tight {flag_range:.2f} consolidation")
        else:
            return PatternResult("Bear Flag", "SHORT", 72,
                                 f"Bear flag: {abs(move):.2f} pole, tight {flag_range:.2f} consolidation")

    def detect_pennant_pattern(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Pennant: sharp move then converging highs/lows."""
        if len(df) < 15:
            return None
        # Strong initial move
        move = df["close"].iloc[-10] - df["close"].iloc[-15]
        atr = ind.atr if ind.atr > 0 else 1
        if abs(move) < 3 * atr:
            return None
        # Converging in last 5 bars
        recent = df.iloc[-5:]
        highs = recent["high"].values
        lows = recent["low"].values
        if len(highs) < 3:
            return None
        high_slope = np.polyfit(range(len(highs)), highs, 1)[0]
        low_slope = np.polyfit(range(len(lows)), lows, 1)[0]
        if move > 0 and high_slope < 0 and low_slope > 0:
            return PatternResult("Bull Pennant", "LONG", 74,
                                 "Bull pennant — converging consolidation after strong move up")
        elif move < 0 and high_slope < 0 and low_slope > 0:
            return PatternResult("Bear Pennant", "SHORT", 74,
                                 "Bear pennant — converging consolidation after strong move down")
        return None

    def detect_vwap_bounce(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Price bouncing off VWAP from below (bullish)."""
        if ind.vwap == 0 or len(df) < 3:
            return None
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        # Was below VWAP, now crossing above
        if (prev["low"] <= ind.vwap and curr["close"] > ind.vwap * 1.001 and
                curr["close"] > curr["open"]):
            dev_pct = ((curr["close"] - ind.vwap) / ind.vwap) * 100
            if dev_pct < 0.5:  # Not too far above VWAP yet
                confidence = 70 + (ind.volume_ratio - 1) * 10
                return PatternResult("VWAP Bounce", "LONG", min(confidence, 85),
                                     f"Price bouncing above VWAP ₹{ind.vwap:.2f}")
        return None

    def detect_vwap_breakdown(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Price breaking below VWAP (bearish)."""
        if ind.vwap == 0 or len(df) < 3:
            return None
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        if (prev["high"] >= ind.vwap and curr["close"] < ind.vwap * 0.999 and
                curr["close"] < curr["open"]):
            confidence = 70 + (ind.volume_ratio - 1) * 10
            return PatternResult("VWAP Breakdown", "SHORT", min(confidence, 85),
                                 f"Price broke below VWAP ₹{ind.vwap:.2f}")
        return None

    def detect_volume_surge_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Strong directional move with 2x+ volume surge."""
        if ind.volume_ratio < 2.0 or len(df) < 5:
            return None
        curr = df.iloc[-1]
        body = abs(curr["close"] - curr["open"])
        atr = ind.atr if ind.atr > 0 else 1
        if body < 0.8 * atr:
            return None
        confidence = min(55 + (ind.volume_ratio - 2) * 15 + (body / atr) * 5, 90)
        direction = "LONG" if curr["close"] > curr["open"] else "SHORT"
        return PatternResult("Volume Surge", direction, confidence,
                             f"{ind.volume_ratio:.1f}x volume surge with {body/atr:.1f}x ATR move")

    def detect_rsi_divergence(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """RSI divergence: price makes new high/low but RSI doesn't."""
        if len(df) < 20 or "rsi" not in df.columns:
            return None
        # Bullish divergence: price lower low, RSI higher low
        prices = df["close"].iloc[-20:]
        rsiv = df["rsi"].iloc[-20:]
        if rsiv.isna().all():
            return None
        # Find recent swing lows
        price_min_idx = prices.idxmin()
        prev_prices = df["close"].iloc[-40:-20] if len(df) >= 40 else None
        if prev_prices is not None and not prev_prices.empty:
            prev_min = prev_prices.min()
            curr_min = prices.min()
            prev_rsi = df["rsi"].iloc[-40:-20].min() if len(df) >= 40 else 50
            curr_rsi = rsiv.min()
            if curr_min < prev_min and curr_rsi > prev_rsi and ind.rsi < 45:
                return PatternResult("Bullish RSI Divergence", "LONG", 75,
                                     f"Bullish divergence: price lower low, RSI higher low ({ind.rsi:.0f})")
            if curr_min > prev_min and curr_rsi < prev_rsi and ind.rsi > 55:
                return PatternResult("Bearish RSI Divergence", "SHORT", 75,
                                     f"Bearish divergence: price higher high, RSI lower high ({ind.rsi:.0f})")
        return None

    def detect_macd_crossover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """MACD line crossing signal line."""
        if len(df) < 3 or "macd" not in df.columns:
            return None
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_diff = prev.get("macd", 0) - prev.get("macd_signal", 0)
        curr_diff = curr.get("macd", 0) - curr.get("macd_signal", 0)
        if prev_diff < 0 and curr_diff > 0:
            hist_strength = abs(ind.macd_hist)
            confidence = min(62 + hist_strength * 2, 85)
            return PatternResult("MACD Bullish Crossover", "LONG", confidence,
                                 f"MACD crossed above signal, hist={ind.macd_hist:.4f}")
        if prev_diff > 0 and curr_diff < 0:
            hist_strength = abs(ind.macd_hist)
            confidence = min(62 + hist_strength * 2, 85)
            return PatternResult("MACD Bearish Crossover", "SHORT", confidence,
                                 f"MACD crossed below signal, hist={ind.macd_hist:.4f}")
        return None

    def detect_bb_squeeze_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Bollinger Band squeeze followed by expansion breakout."""
        if len(df) < 25 or "bb_width" not in df.columns:
            return None
        # Squeeze: BB width at recent minimum
        recent_width = df["bb_width"].iloc[-25:]
        if recent_width.isna().all():
            return None
        curr_width = ind.bb_width
        avg_width = recent_width.mean()
        # Was squeezed, now expanding
        if curr_width > avg_width * 1.2:
            prev_width = df["bb_width"].iloc[-5:-1].mean()
            if prev_width < avg_width * 0.8:  # Was in squeeze
                curr = df.iloc[-1]
                if curr["close"] > ind.bb_upper:
                    return PatternResult("BB Squeeze Breakout Long", "LONG", 78,
                                        f"Bollinger Band squeeze explosion upward")
                elif curr["close"] < ind.bb_lower:
                    return PatternResult("BB Squeeze Breakout Short", "SHORT", 78,
                                        f"Bollinger Band squeeze explosion downward")
        return None

    def detect_ema_crossover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """EMA9 crossing EMA21 (fast momentum signal)."""
        if len(df) < 3 or "ema9" not in df.columns:
            return None
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_diff = prev.get("ema9", 0) - prev.get("ema21", 0)
        curr_diff = curr.get("ema9", 0) - curr.get("ema21", 0)
        close = curr["close"]
        if prev_diff < 0 and curr_diff > 0 and close > ind.ema50:
            return PatternResult("EMA9 x EMA21 Bullish", "LONG", 68,
                                 f"EMA9 crossed above EMA21, price above EMA50")
        if prev_diff > 0 and curr_diff < 0 and close < ind.ema50:
            return PatternResult("EMA9 x EMA21 Bearish", "SHORT", 68,
                                 f"EMA9 crossed below EMA21, price below EMA50")
        return None

    def detect_supertrend_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Supertrend direction change signal."""
        if len(df) < 3 or "supertrend_dir" not in df.columns:
            return None
        prev_dir = int(df["supertrend_dir"].iloc[-2])
        curr_dir = int(df["supertrend_dir"].iloc[-1])
        if prev_dir == -1 and curr_dir == 1:
            return PatternResult("Supertrend Flip Bullish", "LONG", 76,
                                 f"Supertrend flipped bullish — trend change confirmed")
        if prev_dir == 1 and curr_dir == -1:
            return PatternResult("Supertrend Flip Bearish", "SHORT", 76,
                                 f"Supertrend flipped bearish — trend change confirmed")
        return None

    def detect_orb_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Opening Range Breakout (9:15–9:30 AM IST)."""
        if len(df) < 4:
            return None
        # First 3 candles form the ORB
        orb_candles = df.iloc[:3]
        orb_high = orb_candles["high"].max()
        orb_low = orb_candles["low"].min()
        curr = df.iloc[-1]
        if curr["close"] > orb_high * 1.001 and ind.volume_ratio >= 1.5:
            return PatternResult("ORB Bullish Breakout", "LONG", 80,
                                 f"ORB breakout above ₹{orb_high:.2f} with {ind.volume_ratio:.1f}x volume")
        if curr["close"] < orb_low * 0.999 and ind.volume_ratio >= 1.5:
            return PatternResult("ORB Bearish Breakdown", "SHORT", 80,
                                 f"ORB breakdown below ₹{orb_low:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    # --------------------------------------------------------
    # ELITE PATTERNS  (18-year NSE professional stack)
    # --------------------------------------------------------

    def detect_fair_value_gap(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Fair Value Gap (FVG) / Imbalance — ICT / Smart Money concept.
        Gap = candle 3's low > candle 1's high (bullish) or
              candle 3's high < candle 1's low (bearish).
        Price will often return to fill the imbalance before continuing.
        High-probability entry when price re-enters the gap with volume.
        """
        if len(df) < 4:
            return None
        c1, c2, c3 = df.iloc[-4], df.iloc[-3], df.iloc[-2]
        curr        = df.iloc[-1]

        # Bullish FVG: candle 3 low > candle 1 high (upside imbalance)
        if c3["low"] > c1["high"]:
            gap_size = c3["low"] - c1["high"]
            atr = ind.atr if ind.atr > 0 else 1
            if gap_size < 0.3 * atr:       # Too small — noise
                return None
            # Best entry: price pulling back INTO the gap from above
            in_gap = c1["high"] <= curr["close"] <= c3["low"]
            confidence = 75 if in_gap else 60
            if ind.ema9 > ind.ema21 and ind.supertrend_dir == 1:
                confidence = min(confidence + 10, 90)
            return PatternResult("Bullish FVG", "LONG", confidence,
                                 f"Bullish Fair Value Gap: ₹{c1['high']:.2f}–₹{c3['low']:.2f} "
                                 f"({'price in gap' if in_gap else 'approaching gap'})")

        # Bearish FVG: candle 3 high < candle 1 low (downside imbalance)
        if c3["high"] < c1["low"]:
            gap_size = c1["low"] - c3["high"]
            atr = ind.atr if ind.atr > 0 else 1
            if gap_size < 0.3 * atr:
                return None
            in_gap = c3["high"] <= curr["close"] <= c1["low"]
            confidence = 75 if in_gap else 60
            if ind.ema9 < ind.ema21 and ind.supertrend_dir == -1:
                confidence = min(confidence + 10, 90)
            return PatternResult("Bearish FVG", "SHORT", confidence,
                                 f"Bearish Fair Value Gap: ₹{c3['high']:.2f}–₹{c1['low']:.2f} "
                                 f"({'price in gap' if in_gap else 'approaching gap'})")
        return None

    def detect_order_block(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Order Block (OB) — Institutional accumulation/distribution zones.
        Bullish OB: the last bearish candle BEFORE a strong bullish impulse.
        Bearish OB: the last bullish candle BEFORE a strong bearish impulse.
        These zones act as high-probability support/resistance areas.
        18yr rule: "Institutions leave their footprint in order blocks."
        """
        if len(df) < 10:
            return None
        atr = ind.atr if ind.atr > 0 else 1

        # Scan for bullish OB: last bearish candle before 3+ candle bull move
        for i in range(-6, -2):
            ob_candle = df.iloc[i]
            if ob_candle["close"] >= ob_candle["open"]:
                continue   # Not bearish
            # Check if followed by 3 consecutive bullish candles
            subsequent = df.iloc[i+1:i+4]
            if len(subsequent) < 3:
                continue
            if not all(subsequent["close"] > subsequent["open"]):
                continue
            # Strong impulse: total move > 1.5 ATR
            impulse = subsequent["close"].iloc[-1] - ob_candle["low"]
            if impulse < 1.5 * atr:
                continue
            # Price currently returning to OB zone
            ob_high = ob_candle["high"]
            ob_low  = ob_candle["low"]
            curr_close = df.iloc[-1]["close"]
            if ob_low * 0.997 <= curr_close <= ob_high * 1.003:
                confidence = 80 + min((ind.volume_ratio - 1) * 5, 10)
                return PatternResult("Bullish Order Block", "LONG", min(confidence, 92),
                                     f"Institutional OB zone ₹{ob_low:.2f}–₹{ob_high:.2f} "
                                     f"— price returning to buy zone")

        # Scan for bearish OB: last bullish candle before 3+ candle bear move
        for i in range(-6, -2):
            ob_candle = df.iloc[i]
            if ob_candle["close"] <= ob_candle["open"]:
                continue
            subsequent = df.iloc[i+1:i+4]
            if len(subsequent) < 3:
                continue
            if not all(subsequent["close"] < subsequent["open"]):
                continue
            impulse = ob_candle["high"] - subsequent["close"].iloc[-1]
            if impulse < 1.5 * atr:
                continue
            ob_high = ob_candle["high"]
            ob_low  = ob_candle["low"]
            curr_close = df.iloc[-1]["close"]
            if ob_low * 0.997 <= curr_close <= ob_high * 1.003:
                confidence = 80 + min((ind.volume_ratio - 1) * 5, 10)
                return PatternResult("Bearish Order Block", "SHORT", min(confidence, 92),
                                     f"Institutional OB zone ₹{ob_low:.2f}–₹{ob_high:.2f} "
                                     f"— price returning to sell zone")
        return None

    def detect_heikin_ashi_trend(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Heikin Ashi trend signal — smoothed candles remove noise.
        Rules:
          Strong bull: 3+ consecutive HA bullish candles with no lower shadows
          Strong bear: 3+ consecutive HA bearish candles with no upper shadows
        18yr rule: "HA candles tell you the trend clearly — no guessing."
        """
        if len(df) < 5:
            return None

        # Calculate last 5 HA candles
        ha = pd.DataFrame(index=df.index[-5:])
        raw = df.iloc[-5:].copy()
        ha["close"] = (raw["open"] + raw["high"] + raw["low"] + raw["close"]) / 4
        ha_open = pd.Series(dtype=float, index=raw.index)
        ha_open.iloc[0] = (raw["open"].iloc[0] + raw["close"].iloc[0]) / 2
        for j in range(1, len(raw)):
            ha_open.iloc[j] = (ha_open.iloc[j-1] + ha["close"].iloc[j-1]) / 2
        ha["open"]  = ha_open
        ha["high"]  = pd.concat([raw["high"], ha["open"], ha["close"]], axis=1).max(axis=1)
        ha["low"]   = pd.concat([raw["low"],  ha["open"], ha["close"]], axis=1).min(axis=1)
        ha["bullish"] = ha["close"] > ha["open"]
        ha["lower_shadow"] = ha[["open", "close"]].min(axis=1) - ha["low"]

        last3 = ha.iloc[-3:]
        if last3["bullish"].all():
            no_lower = (last3["lower_shadow"] < (ha["close"] - ha["open"]).abs().mean() * 0.1).all()
            confidence = 78 if no_lower else 68
            if ind.supertrend_dir == 1 and ind.ema9 > ind.ema21:
                confidence = min(confidence + 8, 90)
            return PatternResult("Heikin Ashi Bull Trend", "LONG", confidence,
                                 f"3+ consecutive HA bull candles "
                                 f"{'(no lower shadows — strong trend)' if no_lower else ''}")

        if not last3["bullish"].any():
            upper_shadow = ha["high"] - ha[["open", "close"]].max(axis=1)
            no_upper = (upper_shadow.iloc[-3:] < (ha["close"] - ha["open"]).abs().mean() * 0.1).all()
            confidence = 78 if no_upper else 68
            if ind.supertrend_dir == -1 and ind.ema9 < ind.ema21:
                confidence = min(confidence + 8, 90)
            return PatternResult("Heikin Ashi Bear Trend", "SHORT", confidence,
                                 f"3+ consecutive HA bear candles "
                                 f"{'(no upper shadows — strong trend)' if no_upper else ''}")
        return None

    def detect_inside_bar(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Inside Bar — entire candle within previous candle's range.
        Signals consolidation before breakout continuation.
        Trade direction = direction of the preceding trend.
        18yr rule: "Inside bars are the market pausing to reload."
        """
        if len(df) < 5:
            return None
        mother = df.iloc[-2]   # Previous candle
        inside = df.iloc[-1]   # Current candle

        if not (inside["high"] <= mother["high"] and inside["low"] >= mother["low"]):
            return None

        # Body of mother candle should be meaningful (at least 0.5x ATR)
        mother_body = abs(mother["close"] - mother["open"])
        atr = ind.atr if ind.atr > 0 else 1
        if mother_body < 0.5 * atr:
            return None   # Doji mother — not a clean setup

        # Direction = preceding 5-bar trend
        trend_move = df["close"].iloc[-6] - df["close"].iloc[-10] if len(df) >= 10 else 0
        direction = "LONG" if trend_move > 0 else "SHORT"

        compression = (mother["high"] - mother["low"]) / max(inside["high"] - inside["low"], 0.01)
        confidence = min(62 + compression * 3, 80)

        return PatternResult(
            f"Inside Bar ({'Bull' if direction == 'LONG' else 'Bear'} Continuation)",
            direction, confidence,
            f"Inside bar after {'bull' if direction == 'LONG' else 'bear'} trend — "
            f"compression {compression:.1f}x. Breakout likely."
        )

    def detect_double_bottom_top(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Double Bottom (W-pattern) — bullish reversal at support.
        Double Top (M-pattern) — bearish reversal at resistance.
        Both bottoms/tops within 0.5% of each other = valid.
        18yr rule: "Double tops/bottoms are the most reliable reversal patterns in NSE."
        """
        if len(df) < 30:
            return None
        atr = ind.atr if ind.atr > 0 else 1

        # Double Bottom: two lows at similar level, second followed by break above midpoint
        lows = df["low"].iloc[-30:]
        sorted_low_idx = lows.nsmallest(3).index
        if len(sorted_low_idx) < 2:
            return None

        low1_idx = sorted_low_idx[0]
        low2_idx = sorted_low_idx[1]
        low1 = lows[low1_idx]
        low2 = lows[low2_idx]

        # Two bottoms within 0.5% and separated by at least 5 candles
        if abs(low1 - low2) / max(low1, 0.01) > 0.005:
            pass  # Too far apart
        elif abs(df.index.get_loc(low1_idx) - df.index.get_loc(low2_idx)) >= 5:
            # Find midpoint (the "neckline")
            between = df["high"].iloc[
                min(df.index.get_loc(low1_idx), df.index.get_loc(low2_idx)):
                max(df.index.get_loc(low1_idx), df.index.get_loc(low2_idx)) + 1
            ]
            neckline = between.max()
            curr_close = df.iloc[-1]["close"]
            if curr_close > neckline * 0.999:
                confidence = 78 + (ind.volume_ratio - 1) * 5
                confidence = min(confidence + (5 if ind.rsi < 50 else 0), 90)
                return PatternResult("Double Bottom", "LONG", confidence,
                                     f"W-pattern: two bottoms near ₹{min(low1,low2):.2f}, "
                                     f"breakout above neckline ₹{neckline:.2f}")

        # Double Top
        highs = df["high"].iloc[-30:]
        sorted_high_idx = highs.nlargest(3).index
        if len(sorted_high_idx) < 2:
            return None

        high1_idx = sorted_high_idx[0]
        high2_idx = sorted_high_idx[1]
        high1 = highs[high1_idx]
        high2 = highs[high2_idx]

        if abs(high1 - high2) / max(high1, 0.01) <= 0.005:
            if abs(df.index.get_loc(high1_idx) - df.index.get_loc(high2_idx)) >= 5:
                between = df["low"].iloc[
                    min(df.index.get_loc(high1_idx), df.index.get_loc(high2_idx)):
                    max(df.index.get_loc(high1_idx), df.index.get_loc(high2_idx)) + 1
                ]
                neckline = between.min()
                curr_close = df.iloc[-1]["close"]
                if curr_close < neckline * 1.001:
                    confidence = 78 + (ind.volume_ratio - 1) * 5
                    confidence = min(confidence + (5 if ind.rsi > 50 else 0), 90)
                    return PatternResult("Double Top", "SHORT", confidence,
                                        f"M-pattern: two tops near ₹{max(high1,high2):.2f}, "
                                        f"break below neckline ₹{neckline:.2f}")
        return None

    def detect_market_structure_break(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Break of Structure (BOS) / Change of Character (ChoCH).
        BOS = trend continuation: price breaks the last significant swing high/low.
        ChoCH = trend reversal: downtrend breaks a previous lower-high (bearish→bullish).
        18yr rule: "Structure breaks tell you WHO is in control — institutions or retail."
        """
        if len(df) < 20:
            return None
        atr = ind.atr if ind.atr > 0 else 1

        # Find last swing high and swing low using simple peak/valley detection
        highs = df["high"].values
        lows  = df["low"].values
        closes = df["close"].values

        # Swing high: local max in a 5-bar window
        swing_highs = []
        swing_lows  = []
        for j in range(2, len(df) - 2):
            if highs[j] > highs[j-1] and highs[j] > highs[j-2] and \
               highs[j] > highs[j+1] and highs[j] > highs[j+2]:
                swing_highs.append((j, highs[j]))
            if lows[j] < lows[j-1] and lows[j] < lows[j-2] and \
               lows[j] < lows[j+1] and lows[j] < lows[j+2]:
                swing_lows.append((j, lows[j]))

        curr_close = closes[-1]

        if len(swing_highs) >= 2:
            prev_swing_high = swing_highs[-2][1]  # Second-to-last swing high
            last_swing_high = swing_highs[-1][1]  # Most recent swing high
            # BOS Long: current price breaks ABOVE the last swing high
            if curr_close > last_swing_high * 1.001 and ind.volume_ratio >= 1.5:
                confidence = 76 + min((ind.volume_ratio - 1.5) * 8, 12)
                # Bonus if this creates HH (Higher High) = trend continuation
                if last_swing_high > prev_swing_high:
                    confidence = min(confidence + 7, 92)
                    return PatternResult("BOS — Higher High (Trend Continues)", "LONG",
                                        confidence,
                                        f"Break of Structure: new HH above ₹{last_swing_high:.2f} "
                                        f"with {ind.volume_ratio:.1f}x volume")
                else:
                    return PatternResult("ChoCH — Bullish Reversal", "LONG",
                                        confidence,
                                        f"Change of Character: broke above ₹{last_swing_high:.2f} "
                                        f"— downtrend reversing")

        if len(swing_lows) >= 2:
            prev_swing_low = swing_lows[-2][1]
            last_swing_low = swing_lows[-1][1]
            if curr_close < last_swing_low * 0.999 and ind.volume_ratio >= 1.5:
                confidence = 76 + min((ind.volume_ratio - 1.5) * 8, 12)
                if last_swing_low < prev_swing_low:
                    confidence = min(confidence + 7, 92)
                    return PatternResult("BOS — Lower Low (Trend Continues)", "SHORT",
                                        confidence,
                                        f"Break of Structure: new LL below ₹{last_swing_low:.2f} "
                                        f"with {ind.volume_ratio:.1f}x volume")
                else:
                    return PatternResult("ChoCH — Bearish Reversal", "SHORT",
                                        confidence,
                                        f"Change of Character: broke below ₹{last_swing_low:.2f} "
                                        f"— uptrend reversing")
        return None

    def detect_ema_stack(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        EMA Stack — full alignment of 9 > 21 > 50 > 200 (bullish) or reverse (bearish).
        Price above/below all EMAs = maximum trend conviction.
        18yr rule: "When all 4 EMAs are stacked, ride it — don't fight it."
        """
        if ind.ema9 == 0 or ind.ema200 == 0:
            return None
        curr_close = df.iloc[-1]["close"]

        # Full bullish stack
        if (ind.ema9 > ind.ema21 > ind.ema50 > ind.ema200 and
                curr_close > ind.ema9):
            # Measure momentum: price distance above EMA200 as % of ATR
            atr = ind.atr if ind.atr > 0 else 1
            dist_from_200 = (curr_close - ind.ema200) / atr
            confidence = 72 + min(dist_from_200 * 2, 15)
            # Only trade if price pulled back to EMA9 or EMA21 recently
            near_ema = min(abs(curr_close - ind.ema9), abs(curr_close - ind.ema21)) < 2 * atr
            if near_ema:
                confidence = min(confidence + 8, 92)
            return PatternResult("Full EMA Stack Bullish", "LONG", confidence,
                                 f"EMA9({ind.ema9:.0f}) > EMA21({ind.ema21:.0f}) > "
                                 f"EMA50({ind.ema50:.0f}) > EMA200({ind.ema200:.0f}) — "
                                 f"maximum bullish trend alignment")

        # Full bearish stack
        if (ind.ema9 < ind.ema21 < ind.ema50 < ind.ema200 and
                curr_close < ind.ema9):
            atr = ind.atr if ind.atr > 0 else 1
            dist_from_200 = (ind.ema200 - curr_close) / atr
            confidence = 72 + min(dist_from_200 * 2, 15)
            near_ema = min(abs(curr_close - ind.ema9), abs(curr_close - ind.ema21)) < 2 * atr
            if near_ema:
                confidence = min(confidence + 8, 92)
            return PatternResult("Full EMA Stack Bearish", "SHORT", confidence,
                                 f"EMA9({ind.ema9:.0f}) < EMA21({ind.ema21:.0f}) < "
                                 f"EMA50({ind.ema50:.0f}) < EMA200({ind.ema200:.0f}) — "
                                 f"maximum bearish trend alignment")
        return None

    def detect_relative_strength_vs_nifty(
        self, df: pd.DataFrame, ind: IndicatorSet,
        nifty_df: Optional[pd.DataFrame] = None
    ) -> Optional[PatternResult]:
        """
        Relative Strength vs Nifty50 — NSE-specific elite filter.
        Long only stocks stronger than Nifty. Short only stocks weaker.
        Measured as: stock % change vs Nifty % change over last 5 candles.
        18yr rule: "Never go long a weak stock in a strong market.
                    The best trades are in stocks LEADING Nifty."
        """
        if nifty_df is None or len(df) < 6 or len(nifty_df) < 6:
            return None

        # 5-bar return for stock and Nifty
        stock_ret  = (df["close"].iloc[-1] - df["close"].iloc[-6]) / max(df["close"].iloc[-6], 0.01) * 100
        nifty_ret  = (nifty_df["close"].iloc[-1] - nifty_df["close"].iloc[-6]) / max(nifty_df["close"].iloc[-6], 0.01) * 100
        rs_delta   = stock_ret - nifty_ret

        if rs_delta > 1.0:   # Stock outperforming Nifty by >1% over 5 bars
            confidence = min(62 + rs_delta * 5, 85)
            return PatternResult("RS+ vs Nifty", "LONG", confidence,
                                 f"Stock +{stock_ret:.1f}% vs Nifty +{nifty_ret:.1f}% "
                                 f"(RS delta: +{rs_delta:.1f}%) — leading the market")
        if rs_delta < -1.0:  # Stock underperforming Nifty by >1%
            confidence = min(62 + abs(rs_delta) * 5, 85)
            return PatternResult("RS- vs Nifty", "SHORT", confidence,
                                 f"Stock {stock_ret:.1f}% vs Nifty {nifty_ret:.1f}% "
                                 f"(RS delta: {rs_delta:.1f}%) — lagging the market")
        return None

    # --------------------------------------------------------
    # COMPOSITE SCORE
    # --------------------------------------------------------

    def _compute_composite_score(
        self, patterns: List[PatternResult], ind: IndicatorSet
    ) -> Dict:
        """
        Compute composite bullish/bearish score from all patterns + indicators.
        Returns {"long": 0-100, "short": 0-100, "direction": "LONG"/"SHORT"/"NEUTRAL"}
        """
        long_score = 0.0
        short_score = 0.0

        # Elite patterns get higher weight (these are the money-makers)
        ELITE_PATTERNS = {
            "Bullish FVG", "Bearish FVG",
            "Bullish Order Block", "Bearish Order Block",
            "BOS — Higher High (Trend Continues)", "BOS — Lower Low (Trend Continues)",
            "ChoCH — Bullish Reversal", "ChoCH — Bearish Reversal",
            "Full EMA Stack Bullish", "Full EMA Stack Bearish",
            "Double Bottom", "Double Top",
            "Heikin Ashi Bull Trend", "Heikin Ashi Bear Trend",
        }

        # Pattern scores
        for p in patterns:
            weight = 0.8 if p.name in ELITE_PATTERNS else 0.6  # Elite patterns weighted more
            if p.direction == "LONG":
                long_score += p.confidence * weight
            elif p.direction == "SHORT":
                short_score += p.confidence * weight

        # Indicator confluence
        # RSI
        if ind.rsi < 35:
            long_score += 15
        elif ind.rsi > 65:
            short_score += 15

        # MACD histogram direction
        if ind.macd_hist > 0:
            long_score += 10
        elif ind.macd_hist < 0:
            short_score += 10

        # EMA alignment
        if ind.ema9 > ind.ema21 > ind.ema50:
            long_score += 12
        elif ind.ema9 < ind.ema21 < ind.ema50:
            short_score += 12

        # Volume confirmation
        if ind.volume_ratio >= 2.0:
            if long_score > short_score:
                long_score += 10
            else:
                short_score += 10

        # Supertrend
        if ind.supertrend_dir == 1:
            long_score += 8
        else:
            short_score += 8

        # ADX trend strength
        if ind.adx > 25:
            if ind.plus_di > ind.minus_di:
                long_score += 8
            else:
                short_score += 8

        # Normalize to 0-100
        max_score = max(long_score, short_score, 1)
        long_norm = min((long_score / max_score) * 100 * (max_score / 150), 100)
        short_norm = min((short_score / max_score) * 100 * (max_score / 150), 100)

        direction = "NEUTRAL"
        if long_norm > short_norm and long_norm >= 55:
            direction = "LONG"
        elif short_norm > long_norm and short_norm >= 55:
            direction = "SHORT"

        return {
            "long": round(long_norm, 1),
            "short": round(short_norm, 1),
            "direction": direction,
            "dominant": max(long_norm, short_norm),
        }


# ============================================================
# MODULE: multi_timeframe.py
# ============================================================
"""
multi_timeframe.py — NSE Momentum Groww AI Bot
5-min / 15-min / 1-hour Alignment Engine + Swing Structure + Fibonacci

18yr Pro Rule: "Never trade against the higher timeframe. 
The 1h sets the map. The 15m sets the direction. The 5m pulls the trigger."

Advanced features:
- Swing high/low detection (pivot-based)
- Break of Structure (BoS) — confirms trend change
- Supply/Demand zones from institutional order blocks
- Fibonacci retracement levels (38.2%, 50%, 61.8%, 78.6%)
- Fair Value Gaps (FVG) — imbalance zones that price revisits
- Dynamic Support/Resistance clustering
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

TREND_BULLISH = "BULLISH"
TREND_BEARISH = "BEARISH"
TREND_NEUTRAL  = "NEUTRAL"


@dataclass
class SwingPoint:
    price: float
    index: int
    kind: str        # "HIGH" or "LOW"
    strength: int    # number of candles it spans


@dataclass
class Zone:
    top: float
    bottom: float
    kind: str        # "SUPPLY" or "DEMAND"
    strength: int    # touches / confirmations
    origin_index: int


@dataclass
class FibLevel:
    ratio: float
    price: float
    label: str


@dataclass
class FairValueGap:
    top: float
    bottom: float
    direction: str   # "BULLISH" or "BEARISH"
    index: int
    filled: bool = False


@dataclass
class MTFAnalysis:
    # Trend per timeframe
    trend_5m:  str = TREND_NEUTRAL
    trend_15m: str = TREND_NEUTRAL
    trend_1h:  str = TREND_NEUTRAL

    # Scores
    score_5m:  int = 0
    score_15m: int = 0
    score_1h:  int = 0

    # Alignment
    aligned:           bool = False
    alignment_score:   int  = 0
    entry_direction:   str  = "SKIP"
    htf_bias:          str  = TREND_NEUTRAL

    # Structure
    swing_highs: List[SwingPoint] = field(default_factory=list)
    swing_lows:  List[SwingPoint] = field(default_factory=list)
    bos_detected:  bool  = False
    bos_direction: str   = ""
    bos_level:     float = 0.0

    # Zones & levels
    supply_zones:  List[Zone]      = field(default_factory=list)
    demand_zones:  List[Zone]      = field(default_factory=list)
    fib_levels:    List[FibLevel]  = field(default_factory=list)
    fvg_list:      List[FairValueGap] = field(default_factory=list)
    key_resistance: float = 0.0
    key_support:    float = 0.0
    pivot:          float = 0.0
    r1: float = 0.0
    r2: float = 0.0
    s1: float = 0.0
    s2: float = 0.0

    description: str = ""

    @property
    def is_long_setup(self) -> bool:
        return self.entry_direction == "LONG" and self.aligned

    @property
    def is_short_setup(self) -> bool:
        return self.entry_direction == "SHORT" and self.aligned


class MultiTimeframeAnalyzer:
    """
    Full multi-timeframe analysis engine.
    Combines trend, structure, zones, and Fibonacci for high-probability setups.
    """

    # -------------------------------------------------------
    # MAIN ANALYSIS ENTRY POINT
    # -------------------------------------------------------

    def analyze(
        self,
        df_5m:  Optional[pd.DataFrame],
        df_15m: Optional[pd.DataFrame],
        df_1h:  Optional[pd.DataFrame],
    ) -> MTFAnalysis:
        result = MTFAnalysis()

        # Compute trend on each timeframe
        t5  = self._get_trend(df_5m,  "5m")
        t15 = self._get_trend(df_15m, "15m")
        t1h = self._get_trend(df_1h,  "1h")

        result.trend_5m,  result.score_5m  = t5["trend"],  t5["score"]
        result.trend_15m, result.score_15m = t15["trend"], t15["score"]
        result.trend_1h,  result.score_1h  = t1h["trend"], t1h["score"]

        # Higher timeframe bias (1h + 15m) — most important
        result.htf_bias = self._htf_bias(t15, t1h)

        # Alignment check
        align = self._check_alignment(t5, t15, t1h)
        result.aligned         = align["aligned"]
        result.alignment_score = align["score"]
        result.entry_direction = align["direction"]
        result.description     = align["reason"]

        # Structure: swing points, BoS
        if df_5m is not None and len(df_5m) >= 20:
            result.swing_highs, result.swing_lows = self.find_swing_points(df_5m)
            bos = self.check_break_of_structure(df_5m, df_15m)
            result.bos_detected  = bos["detected"]
            result.bos_direction = bos.get("direction", "")
            result.bos_level     = bos.get("level", 0.0)

        # Supply/Demand zones from 15m
        if df_15m is not None and len(df_15m) >= 20:
            result.supply_zones, result.demand_zones = self.find_supply_demand_zones(df_15m)
            result.fvg_list = self.find_fair_value_gaps(df_15m)

        # Fibonacci levels
        if result.swing_highs and result.swing_lows:
            result.fib_levels = self.calculate_fibonacci(result.swing_highs, result.swing_lows)

        # Classic pivot / S&R
        if df_5m is not None and not df_5m.empty:
            sr = self.get_support_resistance(df_5m)
            result.key_resistance = sr.get("resistance", 0.0)
            result.key_support    = sr.get("support", 0.0)
            result.pivot = sr.get("pivot", 0.0)
            result.r1, result.r2 = sr.get("r1", 0.0), sr.get("r2", 0.0)
            result.s1, result.s2 = sr.get("s1", 0.0), sr.get("s2", 0.0)

        logger.debug(
            f"[{format_ist_timestamp()}] MTF: 5m={result.trend_5m} "
            f"15m={result.trend_15m} 1h={result.trend_1h} "
            f"aligned={result.aligned} dir={result.entry_direction}"
        )
        return result

    # -------------------------------------------------------
    # TREND DETECTION PER TIMEFRAME
    # -------------------------------------------------------

    def _get_trend(self, df: Optional[pd.DataFrame], label: str) -> Dict:
        if df is None or len(df) < 15:
            return {"trend": TREND_NEUTRAL, "score": 0}

        row = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 2 else row
        score = 0

        # EMA hierarchy
        ema9  = float(row.get("ema9",  row.get("close", 0)))
        ema21 = float(row.get("ema21", row.get("close", 0)))
        ema50 = float(row.get("ema50", row.get("close", 0)))
        close = float(row.get("close", 0))

        if ema9 > ema21 > ema50 and close > ema9:
            score += 30
        elif ema9 < ema21 < ema50 and close < ema9:
            score -= 30
        elif ema9 > ema21:
            score += 15
        elif ema9 < ema21:
            score -= 15

        # MACD histogram momentum
        hist      = float(row.get("macd_hist", row.get("MACDh_12_26_9", 0)) or 0)
        prev_hist = float(prev.get("macd_hist", prev.get("MACDh_12_26_9", 0)) or 0)
        if hist > 0 and hist > prev_hist:
            score += 20   # Growing bullish momentum
        elif hist > 0:
            score += 10
        elif hist < 0 and hist < prev_hist:
            score -= 20
        elif hist < 0:
            score -= 10

        # RSI
        rsi = float(row.get("rsi", row.get("RSI_14", 50)) or 50)
        if rsi > 55:
            score += 10
        elif rsi < 45:
            score -= 10

        # ADX + DI
        adx      = float(row.get("adx", row.get("ADX_14", 20)) or 20)
        plus_di  = float(row.get("plus_di", row.get("DMP_14", 25)) or 25)
        minus_di = float(row.get("minus_di", row.get("DMN_14", 25)) or 25)
        if adx > 25:
            if plus_di > minus_di:
                score += 15
            else:
                score -= 15

        # Price relative to VWAP
        vwap = float(row.get("vwap", 0) or 0)
        if vwap > 0:
            if close > vwap * 1.002:
                score += 10
            elif close < vwap * 0.998:
                score -= 10

        # Supertrend
        st_dir = int(row.get("supertrend_dir", row.get("SUPERTd_7_3.0", 1)) or 1)
        if st_dir == 1:
            score += 15
        elif st_dir == -1:
            score -= 15

        # Volume confirmation
        vol_ratio = float(row.get("volume_ratio", 1.0) or 1.0)
        if vol_ratio > 1.5 and score > 0:
            score += 10
        elif vol_ratio > 1.5 and score < 0:
            score -= 10

        if score >= 35:
            trend = TREND_BULLISH
        elif score <= -35:
            trend = TREND_BEARISH
        else:
            trend = TREND_NEUTRAL

        return {
            "trend": trend, "score": score,
            "ema9": ema9, "ema21": ema21, "ema50": ema50,
            "rsi": rsi, "adx": adx, "macd_hist": hist,
        }

    def _htf_bias(self, t15: Dict, t1h: Dict) -> str:
        """1h is the MAP. 15m is the DIRECTION. Never fight both."""
        if t1h["trend"] == TREND_BULLISH and t15["trend"] == TREND_BULLISH:
            return TREND_BULLISH
        if t1h["trend"] == TREND_BEARISH and t15["trend"] == TREND_BEARISH:
            return TREND_BEARISH
        return TREND_NEUTRAL

    def _check_alignment(self, t5: Dict, t15: Dict, t1h: Dict) -> Dict:
        """
        Full 3-timeframe alignment check.
        18yr rule: "The more timeframes agree, the higher the win rate."
        """
        trends = [t5["trend"], t15["trend"], t1h["trend"]]
        bulls = trends.count(TREND_BULLISH)
        bears = trends.count(TREND_BEARISH)

        # All 3 aligned — max confidence
        if bulls == 3:
            return {"aligned": True, "score": 100, "direction": "LONG",
                    "reason": "All 3 TF bullish (5m+15m+1h) — HIGHEST confidence LONG"}
        if bears == 3:
            return {"aligned": True, "score": 100, "direction": "SHORT",
                    "reason": "All 3 TF bearish (5m+15m+1h) — HIGHEST confidence SHORT"}

        # 1h + 15m agree (most important pair)
        if t1h["trend"] == TREND_BULLISH and t15["trend"] == TREND_BULLISH:
            return {"aligned": True, "score": 80, "direction": "LONG",
                    "reason": "1h+15m bullish (HTF aligned). 5m lagging — still LONG"}
        if t1h["trend"] == TREND_BEARISH and t15["trend"] == TREND_BEARISH:
            return {"aligned": True, "score": 80, "direction": "SHORT",
                    "reason": "1h+15m bearish (HTF aligned). 5m lagging — still SHORT"}

        # 5m + 15m agree, 1h neutral/opposite — medium confidence
        if t5["trend"] == TREND_BULLISH and t15["trend"] == TREND_BULLISH:
            score = 60 if t1h["trend"] != TREND_BEARISH else 40
            return {"aligned": score >= 60, "score": score, "direction": "LONG",
                    "reason": f"5m+15m bullish. 1h={t1h['trend']}. Moderate confidence."}
        if t5["trend"] == TREND_BEARISH and t15["trend"] == TREND_BEARISH:
            score = 60 if t1h["trend"] != TREND_BULLISH else 40
            return {"aligned": score >= 60, "score": score, "direction": "SHORT",
                    "reason": f"5m+15m bearish. 1h={t1h['trend']}. Moderate confidence."}

        # Conflict — skip trade
        return {
            "aligned": False, "score": 20, "direction": "SKIP",
            "reason": f"MTF conflict: 5m={t5['trend']} 15m={t15['trend']} 1h={t1h['trend']}. Skip."
        }

    # -------------------------------------------------------
    # SWING POINT DETECTION
    # -------------------------------------------------------

    def find_swing_points(
        self, df: pd.DataFrame, strength: int = 5
    ) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Detect pivot highs and lows.
        strength = number of candles on each side that must be lower/higher.
        18yr rule: "Swing points are the skeleton of price action."
        """
        highs: List[SwingPoint] = []
        lows:  List[SwingPoint] = []
        n = len(df)

        for i in range(strength, n - strength):
            window_h = df["high"].iloc[i - strength: i + strength + 1]
            window_l = df["low"].iloc[i - strength: i + strength + 1]

            # Pivot high: highest in the window
            if df["high"].iloc[i] == window_h.max():
                highs.append(SwingPoint(
                    price=float(df["high"].iloc[i]),
                    index=i,
                    kind="HIGH",
                    strength=strength
                ))

            # Pivot low: lowest in the window
            if df["low"].iloc[i] == window_l.min():
                lows.append(SwingPoint(
                    price=float(df["low"].iloc[i]),
                    index=i,
                    kind="LOW",
                    strength=strength
                ))

        # Return only last 5 significant swings each
        return highs[-5:], lows[-5:]

    # -------------------------------------------------------
    # BREAK OF STRUCTURE
    # -------------------------------------------------------

    def check_break_of_structure(
        self,
        df_5m: pd.DataFrame,
        df_15m: Optional[pd.DataFrame]
    ) -> Dict:
        """
        Break of Structure (BoS) — price breaks above last swing high (bullish BoS)
        or below last swing low (bearish BoS), confirmed on close.

        18yr rule: "BoS is the signal that the trend has changed.
        Don't fade it — join it immediately on the first pullback."
        """
        result = {"detected": False, "direction": "", "level": 0.0, "strength": 0.0}
        if df_5m is None or len(df_5m) < 20:
            return result

        highs, lows = self.find_swing_points(df_5m, strength=4)
        if not highs or not lows:
            return result

        last_close = float(df_5m["close"].iloc[-1])
        last_swing_high = max(highs, key=lambda x: x.index).price
        last_swing_low  = min(lows,  key=lambda x: x.index).price

        # Bullish BoS: close above last significant swing high
        if last_close > last_swing_high * 1.001:
            # Confirm on 15m if available
            confirmed = True
            if df_15m is not None and len(df_15m) >= 3:
                close_15m = float(df_15m["close"].iloc[-1])
                confirmed = close_15m > last_swing_high
            result = {
                "detected": confirmed,
                "direction": "BULLISH",
                "level": last_swing_high,
                "strength": (last_close - last_swing_high) / last_swing_high * 100,
            }

        # Bearish BoS: close below last significant swing low
        elif last_close < last_swing_low * 0.999:
            confirmed = True
            if df_15m is not None and len(df_15m) >= 3:
                close_15m = float(df_15m["close"].iloc[-1])
                confirmed = close_15m < last_swing_low
            result = {
                "detected": confirmed,
                "direction": "BEARISH",
                "level": last_swing_low,
                "strength": (last_swing_low - last_close) / last_swing_low * 100,
            }

        return result

    # -------------------------------------------------------
    # SUPPLY / DEMAND ZONES
    # -------------------------------------------------------

    def find_supply_demand_zones(
        self, df: pd.DataFrame, lookback: int = 50
    ) -> Tuple[List[Zone], List[Zone]]:
        """
        Identify institutional supply (resistance) and demand (support) zones.
        A zone = area where price reversed sharply with big volume.

        18yr rule: "Supply/Demand zones are where institutions placed orders.
        Price ALWAYS revisits these — trade the retest, not the initial move."
        """
        supply: List[Zone] = []
        demand: List[Zone] = []
        df_r = df.tail(lookback).copy()
        n = len(df_r)

        avg_vol = df_r["volume"].mean()

        for i in range(2, n - 1):
            candle = df_r.iloc[i]
            prev   = df_r.iloc[i - 1]
            body   = abs(float(candle["close"]) - float(candle["open"]))
            prev_body = abs(float(prev["close"]) - float(prev["open"]))
            vol    = float(candle.get("volume", avg_vol))

            # Supply zone: strong bearish candle after rally, high volume
            if (float(candle["close"]) < float(candle["open"]) and   # bearish
                body > prev_body * 1.5 and                            # bigger than previous
                vol > avg_vol * 1.3):                                 # above avg volume
                supply.append(Zone(
                    top=float(candle["open"]),
                    bottom=float(candle["close"]),
                    kind="SUPPLY",
                    strength=int(vol / avg_vol * 10),
                    origin_index=i,
                ))

            # Demand zone: strong bullish candle after decline, high volume
            if (float(candle["close"]) > float(candle["open"]) and   # bullish
                body > prev_body * 1.5 and
                vol > avg_vol * 1.3):
                demand.append(Zone(
                    top=float(candle["close"]),
                    bottom=float(candle["open"]),
                    kind="DEMAND",
                    strength=int(vol / avg_vol * 10),
                    origin_index=i,
                ))

        # Return most recent + strongest zones
        supply.sort(key=lambda z: z.strength, reverse=True)
        demand.sort(key=lambda z: z.strength, reverse=True)
        return supply[:5], demand[:5]

    # -------------------------------------------------------
    # FAIR VALUE GAPS (FVG)
    # -------------------------------------------------------

    def find_fair_value_gaps(self, df: pd.DataFrame) -> List[FairValueGap]:
        """
        Fair Value Gaps (FVG) / Imbalances — 3-candle pattern.
        Candle[i-2] high < Candle[i] low → bullish FVG (gap up, price will return)
        Candle[i-2] low  > Candle[i] high → bearish FVG (gap down, price will return)

        18yr rule: "Price is a magnet for inefficiency.
        FVGs get filled 80%+ of the time in intraday — trade the fill."
        """
        fvgs: List[FairValueGap] = []
        n = len(df)
        if n < 3:
            return fvgs

        current_price = float(df["close"].iloc[-1])

        for i in range(2, n):
            c0 = df.iloc[i - 2]
            c2 = df.iloc[i]

            # Bullish FVG
            if float(c0["high"]) < float(c2["low"]):
                fvg = FairValueGap(
                    top=float(c2["low"]),
                    bottom=float(c0["high"]),
                    direction="BULLISH",
                    index=i,
                    filled=(current_price <= float(c0["high"])),
                )
                fvgs.append(fvg)

            # Bearish FVG
            elif float(c0["low"]) > float(c2["high"]):
                fvg = FairValueGap(
                    top=float(c0["low"]),
                    bottom=float(c2["high"]),
                    direction="BEARISH",
                    index=i,
                    filled=(current_price >= float(c0["low"])),
                )
                fvgs.append(fvg)

        # Last 10 unfilled FVGs only
        unfilled = [f for f in fvgs if not f.filled]
        return unfilled[-10:]

    # -------------------------------------------------------
    # FIBONACCI RETRACEMENTS
    # -------------------------------------------------------

    def calculate_fibonacci(
        self,
        swing_highs: List[SwingPoint],
        swing_lows:  List[SwingPoint],
    ) -> List[FibLevel]:
        """
        Calculate Fibonacci retracement levels from last major swing.
        Key levels: 23.6%, 38.2%, 50%, 61.8%, 78.6%

        18yr rule: "61.8% and 50% are the golden entry zones.
        Price bounces from these levels with uncanny precision."
        """
        if not swing_highs or not swing_lows:
            return []

        last_high = max(swing_highs, key=lambda x: x.index)
        last_low  = max(swing_lows,  key=lambda x: x.index)
        levels: List[FibLevel] = []

        if last_high.index > last_low.index:
            # Downswing: high → low, fib from bottom up
            swing_range = last_high.price - last_low.price
            for ratio, label in [
                (0.236, "23.6%"), (0.382, "38.2%"),
                (0.500, "50.0%"), (0.618, "61.8%"), (0.786, "78.6%")
            ]:
                levels.append(FibLevel(
                    ratio=ratio,
                    price=round(last_low.price + swing_range * ratio, 2),
                    label=label,
                ))
        else:
            # Upswing: low → high, fib from top down
            swing_range = last_high.price - last_low.price
            for ratio, label in [
                (0.236, "23.6%"), (0.382, "38.2%"),
                (0.500, "50.0%"), (0.618, "61.8%"), (0.786, "78.6%")
            ]:
                levels.append(FibLevel(
                    ratio=ratio,
                    price=round(last_high.price - swing_range * ratio, 2),
                    label=label,
                ))
        return levels

    def nearest_fib_level(self, price: float, fibs: List[FibLevel], tolerance_pct: float = 0.3) -> Optional[FibLevel]:
        """Return closest Fibonacci level if price is within tolerance."""
        for fib in fibs:
            dist = abs(price - fib.price) / fib.price * 100
            if dist <= tolerance_pct:
                return fib
        return None

    # -------------------------------------------------------
    # SUPPORT / RESISTANCE
    # -------------------------------------------------------

    def get_support_resistance(self, df: pd.DataFrame, lookback: int = 30) -> Dict:
        """Classic pivot-based S/R levels."""
        if df is None or len(df) < 3:
            return {}
        recent = df.tail(lookback)
        h = recent["high"].max()
        l = recent["low"].min()
        c = float(df["close"].iloc[-1])
        pivot = (h + l + c) / 3
        r1 = 2 * pivot - l
        r2 = pivot + (h - l)
        s1 = 2 * pivot - h
        s2 = pivot - (h - l)
        return {
            "resistance": round(h, 2), "support": round(l, 2),
            "pivot": round(pivot, 2),
            "r1": round(r1, 2), "r2": round(r2, 2),
            "s1": round(s1, 2), "s2": round(s2, 2),
        }

    # -------------------------------------------------------
    # ENTRY QUALITY NEAR ZONES
    # -------------------------------------------------------

    def is_near_demand_zone(self, price: float, zones: List[Zone], tolerance_pct: float = 0.5) -> Tuple[bool, Optional[Zone]]:
        """Check if price is near a demand zone (ideal BUY area)."""
        for zone in zones:
            mid = (zone.top + zone.bottom) / 2
            if abs(price - mid) / mid * 100 <= tolerance_pct:
                return True, zone
        return False, None

    def is_near_supply_zone(self, price: float, zones: List[Zone], tolerance_pct: float = 0.5) -> Tuple[bool, Optional[Zone]]:
        """Check if price is near a supply zone (ideal SELL area)."""
        for zone in zones:
            mid = (zone.top + zone.bottom) / 2
            if abs(price - mid) / mid * 100 <= tolerance_pct:
                return True, zone
        return False, None

    def get_nearest_fvg(self, price: float, fvgs: List[FairValueGap]) -> Optional[FairValueGap]:
        """Find nearest unfilled FVG to current price."""
        if not fvgs:
            return None
        return min(fvgs, key=lambda f: abs(((f.top + f.bottom) / 2) - price))


# ============================================================
# MODULE: signal_generator.py
# ============================================================
"""
signal_generator.py — NSE Momentum Groww AI Bot
AI-Enhanced Multi-Timeframe Momentum Signal Engine

Based on 18+ years of NSE intraday trading experience.
Combines pattern recognition, multi-timeframe alignment,
news filter, relative strength vs Nifty, and AI scoring
to produce high-conviction LONG/SHORT/SKIP signals.

Signal pipeline:
  1. Fetch 5m, 15m, 1h data
  2. Detect patterns on each timeframe
  3. Check MTF alignment (5m must align with 15m and 1h trend)
  4. Apply news blackout filter
  5. Check relative strength vs Nifty
  6. [NEW] Option Chain context (PCR, Max Pain, OI walls) → ±10 pts
  7. [NEW] FII/DII flow adjustment → ±10 pts
  8. [NEW] Volume Profile context (VPOC/VAH/VAL) → ±15 pts
  9. Compute AI composite score
  10. Apply minimum confidence gate (default: 65/100)
  11. Return TradeSignal with entry, SL, TP, and full rationale
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

from utils import (
    format_ist_timestamp, get_current_ist_time,
    is_market_open_ist, round_to_tick_size
)
from pattern_recognition import PatternRecognizer, IndicatorSet
from data_fetch_groww import GrowwDataFetcher
from high_accuracy_filter import HighAccuracyFilter, FilterResult

# ── Institutional intelligence modules (new) ────────────────
try:
    from option_chain import OptionChainAnalyzer, get_option_chain_analyzer
    _OC_AVAILABLE = True
except ImportError:
    _OC_AVAILABLE = False

try:
    from fii_dii_tracker import FIIDIITracker, get_fii_dii_tracker
    _FII_AVAILABLE = True
except ImportError:
    _FII_AVAILABLE = False

try:
    from volume_profile import VolumeProfileAnalyzer, get_vp_analyzer
    _VP_AVAILABLE = True
except ImportError:
    _VP_AVAILABLE = False

try:
    from nse_data import NSEDataFetcher, get_nse_data_fetcher
    _NSE_DATA_AVAILABLE = True
except ImportError:
    _NSE_DATA_AVAILABLE = False

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class TradeSignal:
    """Complete trade signal with entry, SL, TP, and full rationale."""
    symbol: str
    direction: str            # "LONG" or "SHORT"
    signal_score: float       # 0–100 composite confidence
    entry_price: float
    stop_loss: float
    target_1: float           # 2:1 R:R
    target_2: float           # 3:1 R:R
    risk_reward: float
    atr: float
    quantity: int = 0
    patterns: List[str] = field(default_factory=list)
    timeframe_alignment: Dict = field(default_factory=dict)
    indicators: Optional[IndicatorSet] = None
    news_clear: bool = True
    relative_strength: float = 0.0
    signal_time: str = ""
    rationale: str = ""
    is_high_confidence: bool = False  # Score >= 80
    quality_grade: str = "B"          # A+, A, B, C from HighAccuracyFilter
    size_multiplier: float = 1.0      # From HighAccuracyFilter (0.5–1.5x)

    def __post_init__(self):
        if not self.signal_time:
            self.signal_time = format_ist_timestamp()
        self.is_high_confidence = self.signal_score >= 80

    @property
    def grade_emoji(self) -> str:
        return {"A+": "💎", "A": "⭐", "B": "✅", "C": "⚠️"}.get(self.quality_grade, "📊")

    def summary(self) -> str:
        rr = f"{self.risk_reward:.1f}:1"
        direction_emoji = "🟢" if self.direction == "LONG" else "🔴"
        return (
            f"{direction_emoji} {self.direction} {self.symbol} | "
            f"Grade: {self.grade_emoji}{self.quality_grade} | "
            f"Score: {self.signal_score:.0f}/100 | Size: {self.size_multiplier:.1f}x\n"
            f"Entry: ₹{self.entry_price:.2f} | SL: ₹{self.stop_loss:.2f} | "
            f"T1: ₹{self.target_1:.2f} | T2: ₹{self.target_2:.2f} | R:R {rr}\n"
            f"Patterns: {', '.join(self.patterns[:3])}\n"
            f"{self.rationale}"
        )


class SignalGenerator:
    """
    Generates high-conviction intraday momentum signals.
    Uses AI scoring to rank and filter trade opportunities.
    """

    def __init__(
        self,
        data_fetcher: GrowwDataFetcher,
        news_filter=None,
        min_signal_score: float = 65.0,
        high_confidence_score: float = 80.0,
    ):
        self.fetcher = data_fetcher
        self.news_filter = news_filter
        self.recognizer = PatternRecognizer()
        self.min_score = min_signal_score
        self.high_conf_score = high_confidence_score
        self._nifty_open: Optional[float] = None
        self._nifty_current: Optional[float] = None
        self.ha_filter = HighAccuracyFilter()
        self._learner = None          # Set by main.py: generator.set_learner(learner)
        self._orb_direction: str = "" # Set by main.py after ORB is established
        self._nifty_change_pct: float = 0.0

        # ── Institutional intelligence (auto-init) ──────────────
        self._oc: Optional["OptionChainAnalyzer"] = (
            get_option_chain_analyzer() if _OC_AVAILABLE else None
        )
        self._fii_dii: Optional["FIIDIITracker"] = (
            get_fii_dii_tracker() if _FII_AVAILABLE else None
        )
        self._vp: Optional["VolumeProfileAnalyzer"] = (
            get_vp_analyzer() if _VP_AVAILABLE else None
        )
        # Cached FII adjustment (refreshed every scan cycle, not per-symbol)
        self._fii_adjustment: float = 0.0
        self._fii_size_mult:  float = 1.0

        # NSE supplementary data (bulk/block deals, delivery %, FII futures)
        self._nse_data: Optional["NSEDataFetcher"] = (
            get_nse_data_fetcher() if _NSE_DATA_AVAILABLE else None
        )

        # Concurrent scanning config
        self._max_workers = 6   # Parallel symbol scans (Groww rate-limit safe)

    def set_learner(self, learner) -> None:
        """Inject self-learning engine for adaptive pattern weights."""
        self._learner = learner

    def set_orb_direction(self, direction: str) -> None:
        """Set opening range breakout direction ('UP'/'DOWN'/'') at 9:30 AM IST."""
        self._orb_direction = direction

    def update_nifty_change(self, nifty_change_pct: float) -> None:
        """Update Nifty % change vs previous close (called each scan cycle)."""
        self._nifty_change_pct = nifty_change_pct

    def refresh_institutional_context(self) -> None:
        """
        Refresh FII/DII flow and option chain before each scan cycle.
        Called once per scan — not per symbol — for efficiency.
        """
        if self._fii_dii:
            try:
                self._fii_adjustment = self._fii_dii.get_signal_adjustment()
                self._fii_size_mult  = self._fii_dii.get_position_size_multiplier()
                logger.debug(
                    f"[{format_ist_timestamp()}] FII adj={self._fii_adjustment:+.1f} "
                    f"size_mult={self._fii_size_mult:.2f}"
                )
            except Exception as e:
                logger.warning(f"FII/DII refresh failed: {e}")
                self._fii_adjustment = 0.0
                self._fii_size_mult  = 1.0

    # --------------------------------------------------------
    # MAIN SIGNAL GENERATION
    # --------------------------------------------------------

    def generate_signal(self, symbol: str) -> Optional[TradeSignal]:
        """
        Generate a trade signal for a single symbol.
        Returns TradeSignal if confidence >= min_score, else None.
        """
        try:
            logger.info(f"[{format_ist_timestamp()}] Analyzing {symbol}...")

            # 1. Fetch multi-timeframe data
            mtf_data = self.fetcher.get_multi_timeframe_data(symbol)
            df_5m  = mtf_data.get("5m")
            df_15m = mtf_data.get("15m")
            df_1h  = mtf_data.get("1h")

            if df_5m is None or len(df_5m) < 30:
                logger.debug(f"{symbol}: insufficient 5m data")
                return None

            # 2. Pattern analysis on each timeframe
            analysis_5m  = self.recognizer.analyze(df_5m)
            analysis_15m = self.recognizer.analyze(df_15m) if df_15m is not None else {}
            analysis_1h  = self.recognizer.analyze(df_1h)  if df_1h is not None else {}

            score_5m  = analysis_5m.get("score", {})
            score_15m = analysis_15m.get("score", {}) if analysis_15m else {}
            score_1h  = analysis_1h.get("score", {})  if analysis_1h else {}

            ind = analysis_5m.get("indicators", IndicatorSet())

            # 3. Multi-timeframe alignment
            alignment = self._check_mtf_alignment(score_5m, score_15m, score_1h)
            if not alignment["aligned"]:
                logger.debug(f"{symbol}: MTF not aligned — skipping")
                return None

            direction = alignment["direction"]

            # 4. News filter
            news_clear = True
            if self.news_filter:
                try:
                    news_clear = self.news_filter.is_safe_to_trade(symbol)
                    if not news_clear:
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: news blackout — skipping")
                        return None
                except Exception:
                    pass

            # 5. Relative strength vs Nifty
            rs = self._get_relative_strength(symbol)

            # 5b. Institutional intelligence context (Option Chain + Volume Profile)
            inst_ctx = self._get_institutional_context(symbol, df_5m)

            # 6. Composite AI score
            ai_score = self._compute_ai_score(
                direction=direction,
                score_5m=score_5m,
                score_15m=score_15m,
                score_1h=score_1h,
                ind=ind,
                relative_strength=rs,
                alignment=alignment,
                institutional_ctx=inst_ctx,
            )

            if ai_score < self.min_score:
                logger.debug(f"{symbol}: score {ai_score:.1f} below threshold {self.min_score}")
                return None

            # 7. High-accuracy filter — 5-gate confluence check
            pattern_objs   = analysis_5m.get("patterns", [])
            pattern_names  = [p.name for p in pattern_objs if hasattr(p, "name")]
            pattern_scores = [getattr(p, "confidence", 70.0) for p in pattern_objs]

            ltp_now   = float(df_5m.iloc[-1]["close"])
            above_vwap = ltp_now >= ind.vwap if ind.vwap and ind.vwap > 0 else True

            stock_quote      = self.fetcher.get_quote(symbol) or {}
            stock_change_pct = stock_quote.get("change_pct", 0.0)

            filter_result = self.ha_filter.evaluate(
                signal_score     = ai_score,
                direction        = "BUY" if direction == "LONG" else "SELL",
                regime           = alignment.get("regime", "UNKNOWN"),
                mtf_alignment    = alignment,
                volume_ratio     = ind.volume_ratio,
                pattern_names    = pattern_names,
                pattern_scores   = pattern_scores,
                df_5m            = df_5m,
                rsi              = ind.rsi,
                above_vwap       = above_vwap,
                nifty_change_pct = self._nifty_change_pct,
                stock_change_pct = stock_change_pct,
                news_clear       = news_clear,
                orb_direction    = self._orb_direction,
                learner          = self._learner,
                # ── Gates 6-10 parameters ─────────────────────────────
                symbol           = symbol,
                daily_volume     = float(stock_quote.get("volume", 0) or
                                         stock_quote.get("vol", 0) or
                                         stock_quote.get("traded_volume", 0) or 0),
                ltp              = ltp_now,
                prev_close       = float(stock_quote.get("prev_close", 0) or
                                         stock_quote.get("previous_close", 0) or
                                         stock_quote.get("close", 0) or 0),
                gap_pct          = self._get_gap_pct(symbol),
                minutes_since_open = self._minutes_since_open(),
            )

            if not filter_result.passed:
                logger.debug(
                    f"[{format_ist_timestamp()}] {symbol}: FILTERED — {filter_result.rejection_reason}"
                )
                return None

            # 8. Build signal using filter's final score and size
            signal = self._build_signal(
                symbol=symbol,
                direction=direction,
                df_5m=df_5m,
                ind=ind,
                ai_score=filter_result.final_score,
                patterns=pattern_objs,
                alignment=alignment,
                rs=rs,
                news_clear=news_clear,
                quality_grade=filter_result.quality_grade,
                size_multiplier=filter_result.size_multiplier,
                filter_bonuses=filter_result.bonuses,
            )

            logger.info(
                f"[{format_ist_timestamp()}] ✅ SIGNAL: {direction} {symbol} "
                f"| Score: {filter_result.final_score:.0f} | Entry: ₹{signal.entry_price:.2f}"
            )
            return signal

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] signal_generator error for {symbol}: {e}")
            return None

    def scan_watchlist(
        self, symbols: List[str], max_signals: int = 5
    ) -> List[TradeSignal]:
        """
        Concurrent watchlist scan using ThreadPoolExecutor.

        Performance: 30 symbols × ~4s each → 120s sequential vs ~20s concurrent.
        Rate-limit safe: max 6 workers (Groww allows ~10 req/s).
        """
        # Refresh FII/DII + OC + FII futures once per cycle (not per symbol)
        self.refresh_institutional_context()

        signals: List[TradeSignal] = []
        errors  = 0

        with ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="sig") as pool:
            futures = {pool.submit(self._scan_one, sym): sym for sym in symbols}
            for fut in as_completed(futures, timeout=90):
                sym = futures[fut]
                try:
                    sig = fut.result(timeout=20)
                    if sig:
                        signals.append(sig)
                except FuturesTimeout:
                    logger.debug(f"Scan timeout: {sym}")
                    errors += 1
                except Exception as e:
                    logger.debug(f"Scan error {sym}: {e}")
                    errors += 1

        # Sort: grade first (A+ > A > B > C), then score
        grade_rank = {"A+": 4, "A": 3, "B": 2, "C": 1}
        signals.sort(
            key=lambda s: (grade_rank.get(s.quality_grade, 0), s.signal_score),
            reverse=True,
        )

        filter_stats = self.ha_filter.get_stats()
        logger.info(
            f"[{format_ist_timestamp()}] Scan: {len(signals)} signals "
            f"/ {len(symbols)} symbols | "
            f"Pass rate {filter_stats['pass_rate']:.0f}% | Errors={errors}"
        )
        return signals[:max_signals]

    def _scan_one(self, symbol: str) -> Optional[TradeSignal]:
        """Wrapper for generate_signal — safe for ThreadPoolExecutor."""
        try:
            return self.generate_signal(symbol)
        except Exception as e:
            logger.debug(f"_scan_one({symbol}): {e}")
            return None

    # --------------------------------------------------------
    # INSTITUTIONAL INTELLIGENCE CONTEXT
    # --------------------------------------------------------

    def _get_institutional_context(
        self, symbol: str, df_5m=None
    ) -> Dict:
        """
        Gather Option Chain + Volume Profile context for this symbol.
        FII/DII is market-wide (cached in self._fii_adjustment).

        Returns dict with:
          oc_score:      float (-10 to +10)
          oc_signals:    List[str]
          vp_score:      float (-15 to +15)
          vp_notes:      List[str]
          vp_levels:     Dict (vpoc, vah, val)
          fii_score:     float (-10 to +10) — pre-cached
          fii_size_mult: float
        """
        ctx = {
            "oc_score":        0.0,
            "oc_signals":      [],
            "vp_score":        0.0,
            "vp_notes":        [],
            "vp_levels":       {},
            "fii_score":       self._fii_adjustment,
            "fii_size_mult":   self._fii_size_mult,
            "nse_score":       0,
            "nse_reason":      "",
        }

        # ── Option Chain ─────────────────────────────────
        if self._oc:
            try:
                oc_sym   = "NIFTY"  # Use Nifty chain for market bias
                ctx["oc_score"] = self._oc.get_direction_score(oc_sym)
                oc_result = self._oc.analyze(oc_sym)
                if oc_result:
                    ctx["oc_signals"] = oc_result.signals[:3]
            except Exception as e:
                logger.debug(f"OC context error: {e}")

        # ── Volume Profile ────────────────────────────────
        if self._vp and df_5m is not None and not df_5m.empty:
            try:
                from datetime import datetime as _dt
                session_date = str(get_current_ist_time().date())
                vp_result = self._vp.analyze(df_5m, symbol=symbol, session_date=session_date)
                if vp_result:
                    ltp = float(df_5m["close"].iloc[-1]) if "close" in df_5m.columns else 0
                    if ltp > 0:
                        # We'll pass direction="LONG" to get generic score; caller adjusts
                        vp_ctx = self._vp.get_signal_context(vp_result, ltp, "LONG")
                        ctx["vp_score"]  = vp_ctx.get("score_adjustment", 0.0)
                        ctx["vp_notes"]  = vp_ctx.get("notes", [])
                        ctx["vp_levels"] = {
                            "vpoc": vp_result.vpoc,
                            "vah":  vp_result.vah,
                            "val":  vp_result.val,
                        }
            except Exception as e:
                logger.debug(f"VP context error for {symbol}: {e}")

        # ── NSE supplementary data (bulk/block, delivery, 52wk) ──
        if self._nse_data:
            try:
                # direction placeholder — adjusted in _compute_ai_score
                nse_score, nse_reason = self._nse_data.get_composite_score(
                    symbol, "LONG"
                )
                ctx["nse_score"]  = nse_score
                ctx["nse_reason"] = nse_reason
            except Exception as e:
                logger.debug(f"NSE data context error for {symbol}: {e}")

        return ctx

    # --------------------------------------------------------
    # MULTI-TIMEFRAME ALIGNMENT
    # --------------------------------------------------------

    def _check_mtf_alignment(
        self,
        score_5m: Dict,
        score_15m: Dict,
        score_1h: Dict,
    ) -> Dict:
        """
        Check if 5m signal aligns with 15m and 1h trend.
        Rules (18yr experience):
          - 5m must show clear direction (LONG or SHORT, score >= 55)
          - 15m must not be strongly opposing
          - 1h trend must confirm (or at least not contradict)
        """
        dir_5m  = score_5m.get("direction", "NEUTRAL")
        dir_15m = score_15m.get("direction", "NEUTRAL") if score_15m else "NEUTRAL"
        dir_1h  = score_1h.get("direction", "NEUTRAL") if score_1h else "NEUTRAL"

        dom_5m  = score_5m.get("dominant", 0)
        dom_15m = score_15m.get("dominant", 0) if score_15m else 0
        dom_1h  = score_1h.get("dominant", 0) if score_1h else 0

        # Must have clear 5m signal
        if dir_5m == "NEUTRAL" or dom_5m < 50:
            return {"aligned": False, "direction": "NEUTRAL", "score": 0,
                    "5m": dir_5m, "15m": dir_15m, "1h": dir_1h}

        direction = dir_5m

        # Alignment scoring
        alignment_score = 0.0
        if dir_5m == direction:
            alignment_score += 35
        if dir_15m == direction:
            alignment_score += 35
        elif dir_15m == "NEUTRAL":
            alignment_score += 15  # Neutral is acceptable
        else:
            alignment_score -= 20  # Opposing 15m is bad

        if dir_1h == direction:
            alignment_score += 30
        elif dir_1h == "NEUTRAL":
            alignment_score += 10
        else:
            alignment_score -= 15  # 1h opposing means counter-trend — risky

        aligned = alignment_score >= 50

        return {
            "aligned": aligned,
            "direction": direction,
            "score": alignment_score,
            "5m": dir_5m,
            "15m": dir_15m,
            "1h": dir_1h,
            "full_alignment": (dir_5m == dir_15m == dir_1h == direction),
        }

    # --------------------------------------------------------
    # RELATIVE STRENGTH
    # --------------------------------------------------------

    def _get_relative_strength(self, symbol: str) -> float:
        """
        Calculate relative strength of stock vs Nifty50.
        RS > 0: stock outperforming (bullish edge)
        RS < 0: stock underperforming (bearish edge)
        """
        try:
            stock_quote = self.fetcher.get_quote(symbol)
            nifty_quote = self.fetcher.get_nifty_quote()
            if not stock_quote or not nifty_quote:
                return 0.0
            stock_chg = stock_quote.get("change_pct", 0)
            nifty_chg = nifty_quote.get("change_pct", 0)
            return round(stock_chg - nifty_chg, 2)
        except Exception:
            return 0.0

    # --------------------------------------------------------
    # GAP & TIME HELPERS (used by Gates 6-10)
    # --------------------------------------------------------

    def _get_gap_pct(self, symbol: str) -> float:
        """Get today's opening gap % for symbol (0.0 if not available)."""
        try:
            from gap_analyzer import get_gap_analyzer
            return get_gap_analyzer().get_gap_pct(symbol)
        except Exception:
            return 0.0

    def _minutes_since_open(self) -> float:
        """Minutes elapsed since 9:15 AM IST market open (0.0 before open)."""
        from datetime import datetime as _dt
        now_ist = get_current_ist_time()
        market_open = _dt(now_ist.year, now_ist.month, now_ist.day, 9, 15, 0, tzinfo=IST)
        return max(0.0, (now_ist - market_open).total_seconds() / 60)

    # --------------------------------------------------------
    # AI COMPOSITE SCORE
    # --------------------------------------------------------

    def _compute_ai_score(
        self,
        direction: str,
        score_5m: Dict,
        score_15m: Dict,
        score_1h: Dict,
        ind: IndicatorSet,
        relative_strength: float,
        alignment: Dict,
        institutional_ctx: Optional[Dict] = None,
    ) -> float:
        """
        AI composite score (0–100) incorporating:
        - Pattern strength on each timeframe (weighted)
        - MTF alignment quality
        - Indicator confluence (RSI, MACD, Volume, ADX, SuperTrend)
        - Relative strength vs Nifty
        - Time of day (best momentum windows from 18yr experience)
        - [NEW] Option Chain direction bias (PCR, Max Pain, OI walls)
        - [NEW] FII/DII institutional flow adjustment
        - [NEW] Volume Profile (VPOC/VAH/VAL location)
        """
        score = 0.0
        ctx   = institutional_ctx or {}

        # ── Timeframe pattern scores ──────────────────────
        key = "long" if direction == "LONG" else "short"
        score += score_5m.get(key, 0) * 0.40
        score += score_15m.get(key, 0) * 0.30 if score_15m else 0
        score += score_1h.get(key, 0) * 0.20 if score_1h else 0

        # ── MTF alignment bonus ───────────────────────────
        if alignment.get("full_alignment"):
            score += 12
        elif alignment.get("score", 0) >= 70:
            score += 8
        elif alignment.get("score", 0) >= 50:
            score += 4

        # ── Indicator confluence ──────────────────────────
        if direction == "LONG":
            if 30 < ind.rsi < 50:
                score += 5  # RSI in buy zone but not extreme
            if ind.macd_hist > 0:
                score += 4
            if ind.ema9 > ind.ema21:
                score += 3
            if ind.supertrend_dir == 1:
                score += 4
            if ind.adx > 25 and ind.plus_di > ind.minus_di:
                score += 5
        else:  # SHORT
            if 50 < ind.rsi < 70:
                score += 5
            if ind.macd_hist < 0:
                score += 4
            if ind.ema9 < ind.ema21:
                score += 3
            if ind.supertrend_dir == -1:
                score += 4
            if ind.adx > 25 and ind.minus_di > ind.plus_di:
                score += 5

        # ── Volume confirmation ───────────────────────────
        if ind.volume_ratio >= 2.0:
            score += 8
        elif ind.volume_ratio >= 1.5:
            score += 4

        # ── Relative strength vs Nifty ────────────────────
        if direction == "LONG" and relative_strength > 0.5:
            score += min(relative_strength * 2, 8)
        elif direction == "SHORT" and relative_strength < -0.5:
            score += min(abs(relative_strength) * 2, 8)

        # ── Time of day bonus ─────────────────────────────
        now_ist  = get_current_ist_time()
        time_val = now_ist.hour + now_ist.minute / 60
        if 9.25 <= time_val <= 10.5:  # 9:15–10:30: morning momentum power hour
            score += 6
        elif 13.5 <= time_val <= 14.5:  # 1:30–2:30 PM: afternoon institutional
            score += 4
        elif time_val >= 14.75:  # After 2:45 PM: avoid new positions
            score -= 8

        # ── [NEW] Option Chain direction bias ─────────────
        oc_score = ctx.get("oc_score", 0.0)
        if direction == "LONG":
            score += oc_score   # +ve oc_score = bullish OC = good for LONG
        else:
            score -= oc_score   # -ve oc_score = bearish OC = good for SHORT

        # ── [NEW] FII/DII flow adjustment ────────────────
        fii_adj = ctx.get("fii_score", 0.0)
        if direction == "LONG":
            score += fii_adj   # FII buying = boost LONG
        else:
            score -= fii_adj   # FII selling = boost SHORT

        # ── [NEW] Volume Profile context ──────────────────
        vp_score = ctx.get("vp_score", 0.0)
        if direction == "LONG":
            score += vp_score
        else:
            score += vp_score  # Already direction-aligned by get_signal_context

        # ── [NEW] NSE bulk/block deal + delivery + 52wk ───
        nse_raw = ctx.get("nse_score", 0)
        if nse_raw != 0:
            # Flip sign for SHORT (buy signal = bad for short)
            nse_adj = nse_raw if direction == "LONG" else -nse_raw
            score += nse_adj
            if abs(nse_adj) >= 5:
                logger.debug(
                    f"NSE data adj={nse_adj:+d} | {ctx.get('nse_reason', '')}"
                )

        return min(round(score, 1), 100)

    # --------------------------------------------------------
    # SIGNAL BUILDER
    # --------------------------------------------------------

    def _build_signal(
        self,
        symbol: str,
        direction: str,
        df_5m,
        ind: IndicatorSet,
        ai_score: float,
        patterns: list,
        alignment: Dict,
        rs: float,
        news_clear: bool,
        quality_grade: str = "B",
        size_multiplier: float = 1.0,
        filter_bonuses: Optional[List[str]] = None,
    ) -> TradeSignal:
        """Build complete TradeSignal with entry, SL, TP levels."""
        from config import ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER
        curr = df_5m.iloc[-1]
        ltp = float(curr["close"])
        atr = max(ind.atr, ltp * 0.003)  # Minimum 0.3% ATR

        if direction == "LONG":
            entry     = round_to_tick_size(ltp)
            stop_loss = round_to_tick_size(entry - ATR_SL_MULTIPLIER * atr)
            target_1  = round_to_tick_size(entry + 2.0 * (entry - stop_loss))
            target_2  = round_to_tick_size(entry + ATR_TP_MULTIPLIER * (entry - stop_loss))
        else:  # SHORT
            entry     = round_to_tick_size(ltp)
            stop_loss = round_to_tick_size(entry + ATR_SL_MULTIPLIER * atr)
            target_1  = round_to_tick_size(entry - 2.0 * (stop_loss - entry))
            target_2  = round_to_tick_size(entry - ATR_TP_MULTIPLIER * (stop_loss - entry))

        sl_distance = abs(entry - stop_loss)
        risk_reward = abs(target_1 - entry) / sl_distance if sl_distance > 0 else 2.0

        # Rationale text
        mtf_str      = f"5m:{alignment.get('5m','?')} / 15m:{alignment.get('15m','?')} / 1h:{alignment.get('1h','?')}"
        pattern_names = [p.name for p in patterns if hasattr(p, "name") and
                         getattr(p, "direction", direction) == direction][:3]
        bonus_str    = " | " + ", ".join((filter_bonuses or [])[:4]) if filter_bonuses else ""

        rationale = (
            f"Grade {quality_grade} | MTF: {mtf_str} | "
            f"RS vs Nifty: {rs:+.1f}% | "
            f"Volume: {ind.volume_ratio:.1f}x | "
            f"RSI: {ind.rsi:.0f} | "
            f"MACD: {'▲' if ind.macd_hist > 0 else '▼'} | "
            f"Supertrend: {'▲' if ind.supertrend_dir == 1 else '▼'}"
            f"{bonus_str}"
        )

        return TradeSignal(
            symbol=symbol,
            direction=direction,
            signal_score=ai_score,
            entry_price=entry,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            risk_reward=round(risk_reward, 2),
            atr=round(atr, 2),
            patterns=pattern_names,
            timeframe_alignment=alignment,
            indicators=ind,
            news_clear=news_clear,
            relative_strength=rs,
            signal_time=format_ist_timestamp(),
            rationale=rationale,
            quality_grade=quality_grade,
            size_multiplier=size_multiplier,
        )


# ============================================================
# MODULE: execution_groww.py
# ============================================================
"""
execution_groww.py — NSE Momentum Groww AI Bot
Live Order Placement, Modification, Cancellation via growwapi

⚠️ WARNING: THIS MODULE PLACES REAL ORDERS WITH REAL MONEY ON GROWW.
⚠️ ALL orders use MIS (Margin Intraday Square-off) product type.
⚠️ LIVE_TRADING_ENABLED must be True in .env for real order placement.
⚠️ Start with very small capital. Monitor manually at first.

Server runs in UK (UTC) — all timestamps in IST.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, round_to_tick_size
from auth_groww import get_groww_token
from risk_manager import Position, RiskManager
from signal_generator import TradeSignal

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class GrowwIPBlockedError(Exception):
    """Raised when Groww rejects order placement due to unregistered server IP."""

# SEBI-compliant trade journal DB
TRADE_DB_PATH = "logs/trades/trade_journal.db"


class OrderResult:
    def __init__(self, success: bool, order_id: str = "", message: str = "",
                 raw: dict = None):
        self.success = success
        self.order_id = order_id
        self.message = message
        self.raw = raw or {}
        self.timestamp = format_ist_timestamp()

    def __repr__(self):
        status = "✅" if self.success else "❌"
        return f"{status} OrderResult(id={self.order_id}, msg={self.message})"


class GrowwExecutor:
    """
    Live order execution engine for Groww.
    All orders are MIS (intraday). No delivery orders.
    """

    def __init__(self, risk_manager: RiskManager, live_enabled: bool = False):
        self.risk_manager = risk_manager
        self.live_enabled = live_enabled
        self._api = None
        self._init_api()
        self._init_db()

        if self.live_enabled:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚡ LIVE TRADING ENABLED — "
                "REAL ORDERS WILL BE PLACED ON GROWW"
            )
        else:
            logger.info(
                f"[{format_ist_timestamp()}] 🔒 DRY RUN MODE — "
                "No real orders will be placed"
            )

    def _init_api(self):
        """Initialize Groww API client."""
        try:
            from growwapi import GrowwAPI
            token = get_groww_token()
            if token:
                self._api = GrowwAPI(token)
                # Log available order-placement methods for diagnostics
                order_methods = [m for m in dir(self._api)
                                 if not m.startswith("_") and
                                 any(k in m.lower() for k in ("order", "place", "trade", "buy", "sell"))]
                logger.info(
                    f"[{format_ist_timestamp()}] GrowwAPI executor initialized | "
                    f"Order methods: {order_methods}"
                )
        except ImportError:
            logger.error("growwapi not installed — order execution disabled")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Executor API init error: {e}")

    # --------------------------------------------------------
    # ROBUST ORDER PLACEMENT (method-name discovery)
    # --------------------------------------------------------

    _ORDER_METHOD_NAMES = [
        "place_order",
        "place_equity_order",
        "place_mis_order",
        "create_order",
        "new_order",
        "order",
        "place_new_order",
    ]

    def _call_place_order(self, order_params: dict) -> Optional[dict]:
        """
        Call Groww place_order with the given params.
        Falls back through a priority list of method name variants in case the
        installed SDK version differs. On any successful call (even None response)
        returns the response; only logs an error if NO method name matches at all.
        """
        if not self._api:
            return None

        # Build alternate params in case SDK uses different key names
        alt_params = dict(order_params)
        # Some SDK versions use "symbol" instead of "trading_symbol"
        if "trading_symbol" in alt_params:
            alt_params["symbol"] = alt_params.pop("trading_symbol")
        # Some SDK versions use "product_type" instead of "product"
        if "product" in alt_params:
            alt_params["product_type"] = alt_params.pop("product")
        # Some SDK versions omit "segment"
        alt_params_noseg = {k: v for k, v in order_params.items() if k != "segment"}

        tried = []
        for method_name in self._ORDER_METHOD_NAMES:
            if not hasattr(self._api, method_name):
                continue
            fn = getattr(self._api, method_name)
            tried.append(method_name)

            for label, params in (
                ("primary", order_params),
                ("alt",     alt_params),
                ("noseg",   alt_params_noseg),
            ):
                try:
                    resp = fn(**params)
                    logger.info(
                        f"[{format_ist_timestamp()}] place_order called via "
                        f"'{method_name}' ({label} params) → {resp}"
                    )
                    # Return whatever the SDK gives (None = order rejected at API level)
                    return resp
                except TypeError as te:
                    logger.debug(
                        f"[{format_ist_timestamp()}] '{method_name}' ({label}) "
                        f"TypeError: {te} — trying next param set"
                    )
                except Exception as e:
                    err_str = str(e).lower()
                    # IP whitelist errors affect ALL methods — bail immediately
                    if any(kw in err_str for kw in (
                        "ip", "inactive", "registered ip", "not whitelisted",
                        "ip not", "ip address", "allowed ip",
                    )):
                        raise GrowwIPBlockedError(str(e))
                    logger.warning(
                        f"[{format_ist_timestamp()}] '{method_name}' ({label}) error: {e}"
                    )
                    break  # Non-TypeError: method exists but call rejected → next method

        if not tried:
            # Log all API methods so we know what to add next time
            all_methods = [m for m in dir(self._api) if not m.startswith("_")]
            logger.error(
                f"[{format_ist_timestamp()}] ❌ No order method found in SDK. "
                f"Available API methods: {all_methods}"
            )
        return None

    def _init_db(self):
        """Initialize SEBI-compliant trade journal SQLite DB."""
        Path(TRADE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(TRADE_DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                symbol TEXT,
                direction TEXT,
                quantity INTEGER,
                entry_price REAL,
                exit_price REAL,
                stop_loss REAL,
                target_1 REAL,
                target_2 REAL,
                pnl REAL,
                pnl_pct REAL,
                product TEXT DEFAULT 'MIS',
                signal_score REAL,
                patterns TEXT,
                entry_time TEXT,
                exit_time TEXT,
                exit_reason TEXT,
                atr REAL,
                live_trade INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    # --------------------------------------------------------
    # ENTRY ORDERS
    # --------------------------------------------------------

    def place_entry_order(self, signal: TradeSignal) -> OrderResult:
        """
        Place entry order based on a TradeSignal.
        Uses LIMIT order near current price, falls back to MARKET.

        Args:
            signal: TradeSignal from signal_generator

        Returns:
            OrderResult with order_id on success
        """
        # ── F&O ELIGIBILITY CHECK (SELL/SHORT orders only) ──────────────
        # NSE only allows intraday short-selling on F&O segment stocks.
        # Attempting to short an equity-only stock → Groww REJECTS the order.
        # Rejection after position tracking = phantom position risk.
        if signal.direction in ("SHORT", "SELL"):
            try:
                from nse_fo_list import is_fo_eligible
                if not is_fo_eligible(signal.symbol):
                    logger.warning(
                        f"[{format_ist_timestamp()}] ⛔ SHORT BLOCKED: {signal.symbol} "
                        "is NOT F&O eligible — intraday short-selling not allowed "
                        "on equity-only stocks. Order would be rejected by Groww."
                    )
                    return OrderResult(
                        False,
                        message=(
                            f"{signal.symbol} not F&O eligible — "
                            "cannot short equity-only stock on NSE"
                        ),
                    )
            except Exception as e:
                logger.debug(f"F&O check error (allowing trade): {e}")

        # ── Fetch live balance BEFORE risk check so capital is current ──────
        from data_fetch_groww import get_data_fetcher
        fetcher = get_data_fetcher()
        balance = fetcher.get_account_balance()
        available = balance.get("available", 0)
        # update_balance() guards against 0 — keeps last known good capital
        self.risk_manager.update_balance(available)

        logger.info(
            f"[{format_ist_timestamp()}] PRE-TRADE: {signal.symbol} | "
            f"API balance: ₹{available:,.0f} | "
            f"Risk capital: ₹{self.risk_manager.state.daily_capital:,.0f} | "
            f"Score: {signal.signal_score:.0f} | "
            f"Live: {self.live_enabled}"
        )

        # Pre-trade risk check
        can_trade = self.risk_manager.can_take_trade(signal.symbol, signal.direction)
        if not can_trade["allowed"]:
            logger.warning(
                f"[{format_ist_timestamp()}] Trade BLOCKED: {signal.symbol} — "
                f"{can_trade['reason']}"
            )
            return OrderResult(False, message=can_trade["reason"])

        # Recalculate quantity with live balance, apply filter size multiplier
        sizing = self.risk_manager.calculate_position_size(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
        )
        quantity = sizing.get("quantity", 0)
        logger.info(
            f"[{format_ist_timestamp()}] SIZING: {signal.symbol} qty={quantity} | "
            f"{sizing.get('reason', 'OK')} | "
            f"capital=₹{sizing.get('capital_used', 0):,.0f} | "
            f"session={sizing.get('session', '?')}"
        )
        if quantity <= 0:
            return OrderResult(
                False,
                message=(
                    f"Position size = 0 — {sizing.get('reason', 'insufficient capital')} "
                    f"(capital=₹{self.risk_manager.state.daily_capital:,.0f})"
                )
            )
        # Apply grade-based size multiplier from HighAccuracyFilter
        size_mult = getattr(signal, "size_multiplier", 1.0)
        if size_mult != 1.0:
            quantity = max(1, int(quantity * size_mult))
            logger.info(
                f"[{format_ist_timestamp()}] Size adjusted by {size_mult:.1f}x "
                f"(Grade {getattr(signal, 'quality_grade', 'B')}) → {quantity} qty"
            )

        entry_price = round_to_tick_size(signal.entry_price)
        transaction_type = "BUY" if signal.direction == "LONG" else "SELL"

        logger.info(
            f"[{format_ist_timestamp()}] {'[LIVE]' if self.live_enabled else '[DRY]'} "
            f"Placing {transaction_type} {signal.symbol} x{quantity} @ ₹{entry_price:.2f} "
            f"| SL: ₹{signal.stop_loss:.2f} | Score: {signal.signal_score:.0f}"
        )

        if not self.live_enabled:
            # Dry run — simulate order
            fake_id = f"DRY_{signal.symbol}_{get_current_ist_time().strftime('%H%M%S')}"
            position = self._create_position(signal, quantity, entry_price, fake_id)
            self.risk_manager.add_position(position)
            self._log_to_db(signal, quantity, entry_price, fake_id, live=False)
            logger.info(
                f"[{format_ist_timestamp()}] [DRY RUN] Order simulated: {fake_id}"
            )
            return OrderResult(True, order_id=fake_id,
                               message=f"Dry run — {transaction_type} {quantity}x {signal.symbol}")

        # LIVE ORDER
        if not self._api:
            self._init_api()
            if not self._api:
                return OrderResult(False, message="Groww API not initialized")

        try:
            # ── MARKET order: fills immediately at current price.
            # LIMIT orders at a stale signal price risk never filling within the
            # NSE session window, forcing cancellation and missed momentum moves.
            # For liquid NSE stocks the slippage on MARKET is negligible (<0.05%).
            order_params = {
                "trading_symbol": signal.symbol,
                "exchange": "NSE",
                "segment": "CASH",         # Equity cash segment
                "transaction_type": transaction_type,
                "quantity": quantity,
                "product": "MIS",          # Intraday only — never delivery
                "order_type": "MARKET",    # MARKET: fills instantly, no stale-price risk
                "validity": "DAY",
            }

            response = self._call_place_order(order_params)
            logger.info(f"[{format_ist_timestamp()}] place_order response: {response}")

            if response and (response.get("order_id") or response.get("id")):
                order_id = str(response.get("order_id") or response.get("id"))

                # ── Wait for fill confirmation (MARKET fills in <5s normally) ──
                # We do NOT cancel on timeout: a MARKET order is already sent to
                # the exchange and will fill at whatever the current price is.
                # Cancelling would create a phantom unclosed position.
                filled_price = self._wait_for_fill(
                    order_id, entry_price, timeout=60, is_market_order=True
                )

                # For MARKET orders: if status API is slow, trust the order went
                # through and use the live quote as a proxy for fill price.
                if filled_price is None:
                    from data_fetch_groww import get_data_fetcher
                    q = get_data_fetcher().get_quote(signal.symbol)
                    filled_price = q.get("ltp", entry_price) if q else entry_price
                    logger.warning(
                        f"[{format_ist_timestamp()}] ⚠️ {order_id}: fill status not "
                        f"confirmed in 60s — using live LTP ₹{filled_price:.2f} as fill price. "
                        f"CHECK GROWW APP to verify position."
                    )

                position = self._create_position(signal, quantity, filled_price, order_id)
                self.risk_manager.add_position(position)
                self._log_to_db(signal, quantity, filled_price, order_id, live=True)

                logger.info(
                    f"[{format_ist_timestamp()}] ✅ ORDER PLACED: {order_id} | "
                    f"{transaction_type} {signal.symbol} x{quantity} "
                    f"@ ₹{filled_price:.2f} (signal ₹{entry_price:.2f})"
                )
                return OrderResult(True, order_id=order_id,
                                   message=f"Filled: {order_id}", raw=response)
            else:
                logger.error(
                    f"[{format_ist_timestamp()}] Order placement failed: {response}"
                )
                return OrderResult(False, message=f"API error: {response}")

        except GrowwIPBlockedError as ip_err:
            msg = (
                "🚨 GROWW IP BLOCKED — Orders cannot be placed!\n\n"
                "Your Render server IP is not whitelisted in Groww.\n\n"
                "Fix:\n"
                "1. Go to Groww → Profile → Developer API Settings\n"
                "2. Add your Render server's outbound IP to the allowed list\n"
                "3. Render IPs: check Dashboard → Service → Outbound IPs\n\n"
                f"Raw error: {ip_err}"
            )
            logger.error(f"[{format_ist_timestamp()}] {msg}")
            # Send Telegram alert so user can act immediately
            try:
                from alerts_telegram import TelegramAlerter
                import os
                alerter = TelegramAlerter(
                    os.getenv("TELEGRAM_BOT_TOKEN", ""),
                    os.getenv("TELEGRAM_CHAT_ID", ""),
                )
                alerter.send_text(msg)
            except Exception:
                pass
            return OrderResult(False, message="IP not whitelisted on Groww")

        except Exception as e:
            logger.error(
                f"[{format_ist_timestamp()}] place_entry_order exception: {e}"
            )
            return OrderResult(False, message=str(e))

    # --------------------------------------------------------
    # EXIT ORDERS
    # --------------------------------------------------------

    def place_exit_order(
        self,
        symbol: str,
        quantity: int,
        direction: str,
        reason: str = "Signal exit",
        use_market_order: bool = False,
    ) -> OrderResult:
        """
        Place exit (square-off) order for an open position.
        Uses MARKET order for urgent exits (SL hit, kill switch, EOD).
        """
        exit_type = "SELL" if direction == "LONG" else "BUY"
        order_type = "MARKET" if use_market_order else "LIMIT"

        if not self.live_enabled:
            from data_fetch_groww import get_data_fetcher
            fetcher = get_data_fetcher()
            quote = fetcher.get_quote(symbol)
            exit_price = quote.get("ltp", 0) if quote else 0
            trade = self.risk_manager.close_position(symbol, exit_price, reason)
            if trade:
                self._update_db_exit(symbol, exit_price, reason)
            fake_id = f"DRY_EXIT_{symbol}_{get_current_ist_time().strftime('%H%M%S')}"
            logger.info(f"[{format_ist_timestamp()}] [DRY RUN] Exit simulated: {symbol}")
            return OrderResult(True, order_id=fake_id,
                               message=f"Dry exit {symbol} — {reason}")

        if not self._api:
            self._init_api()

        try:
            from data_fetch_groww import get_data_fetcher
            fetcher = get_data_fetcher()
            quote = fetcher.get_quote(symbol)
            exit_price = round_to_tick_size(quote.get("ltp", 0)) if quote else 0

            params = {
                "trading_symbol": symbol,  # Groww SDK uses trading_symbol, not symbol
                "exchange": "NSE",
                "segment": "CASH",         # Required by Groww SDK for equities
                "transaction_type": exit_type,
                "quantity": quantity,
                "product": "MIS",
                "order_type": order_type,
                "validity": "DAY",
            }
            if order_type == "LIMIT" and exit_price:
                params["price"] = exit_price

            response = self._call_place_order(params)

            if response and (response.get("order_id") or response.get("id")):
                order_id = str(response.get("order_id") or response.get("id"))
                trade = self.risk_manager.close_position(symbol, exit_price, reason)
                if trade:
                    self._update_db_exit(symbol, exit_price, reason)
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ EXIT ORDER: {symbol} x{quantity} | {reason}"
                )
                return OrderResult(True, order_id=order_id, raw=response)
            else:
                # Try market order fallback
                params["order_type"] = "MARKET"
                params.pop("price", None)
                response2 = self._call_place_order(params)
                if response2 and (response2.get("order_id") or response2.get("id")):
                    order_id = str(response2.get("order_id") or response2.get("id"))
                    self.risk_manager.close_position(symbol, exit_price, reason + " (MARKET fallback)")
                    return OrderResult(True, order_id=order_id, raw=response2)
                return OrderResult(False, message=f"Exit failed: {response}")

        except GrowwIPBlockedError as ip_err:
            logger.error(
                f"[{format_ist_timestamp()}] EXIT BLOCKED — IP not whitelisted: {ip_err}. "
                "Add Render outbound IP to Groww API Settings."
            )
            return OrderResult(False, message="IP not whitelisted — exit blocked")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] place_exit_order error: {e}")
            return OrderResult(False, message=str(e))

    # --------------------------------------------------------
    # MODIFY STOP LOSS
    # --------------------------------------------------------

    def modify_stop_loss(self, symbol: str, new_sl: float) -> OrderResult:
        """Modify the SL for an open position's SL order."""
        if symbol not in self.risk_manager.state.positions:
            return OrderResult(False, message=f"No position found for {symbol}")

        pos = self.risk_manager.state.positions[symbol]
        pos.stop_loss = new_sl
        if pos.trailing_active:
            pos.trailing_stop = new_sl

        if not self.live_enabled:
            logger.info(f"[{format_ist_timestamp()}] [DRY] SL modified: {symbol} → ₹{new_sl:.2f}")
            return OrderResult(True, message=f"Dry: SL updated to ₹{new_sl:.2f}")

        # Modify SL order on Groww if sl_order_id exists
        if pos.sl_order_id and self._api:
            try:
                response = self._api.modify_order(
                    order_id=pos.sl_order_id,
                    price=round_to_tick_size(new_sl),
                )
                if response:
                    logger.info(
                        f"[{format_ist_timestamp()}] SL modified: {symbol} → ₹{new_sl:.2f}"
                    )
                    return OrderResult(True, order_id=pos.sl_order_id)
            except Exception as e:
                logger.error(f"Modify SL error: {e}")

        return OrderResult(True, message=f"SL updated locally to ₹{new_sl:.2f}")

    # --------------------------------------------------------
    # SQUARE-OFF ALL (EOD / KILL SWITCH)
    # --------------------------------------------------------

    def square_off_all(self, reason: str = "EOD square-off") -> List[OrderResult]:
        """
        Square off ALL open positions immediately.
        Used for EOD (3:20 PM IST) and kill switch.
        Always uses MARKET orders for guaranteed execution.
        """
        logger.warning(
            f"[{format_ist_timestamp()}] 🔴 SQUARE OFF ALL POSITIONS: {reason}"
        )
        results = []
        positions = list(self.risk_manager.state.positions.values())

        for pos in positions:
            result = self.place_exit_order(
                symbol=pos.symbol,
                quantity=pos.quantity,
                direction=pos.direction,
                reason=reason,
                use_market_order=True,  # MARKET for guaranteed fill
            )
            results.append(result)
            if not result.success:
                logger.error(
                    f"[{format_ist_timestamp()}] ❌ Failed to exit {pos.symbol}: "
                    f"{result.message}"
                )
        logger.warning(
            f"[{format_ist_timestamp()}] Square-off complete: "
            f"{sum(1 for r in results if r.success)}/{len(results)} successful"
        )
        return results

    # --------------------------------------------------------
    # HELPERS
    # --------------------------------------------------------
    # ORDER FILL CONFIRMATION
    # --------------------------------------------------------

    def _wait_for_fill(self, order_id: str, expected_price: float,
                       timeout: int = 60,
                       is_market_order: bool = False) -> Optional[float]:
        """
        Poll Groww order status until FILLED or timeout.
        Returns actual fill price on success, None if status not confirmed.

        MARKET orders fill in <5s. LIMIT orders may take longer.
        For MARKET orders we do NOT cancel on timeout — the order is already at
        the exchange and will fill. Caller handles the None case by using LTP.

        Groww order statuses:
          Pending : PLACED, OPEN, PENDING, TRANSIT, TRIGGER_PENDING, OPEN_PENDING
          Filled  : COMPLETE, FILLED, TRADED, EXECUTED, PARTIAL_EXECUTED
          Terminal: CANCELLED, REJECTED, EXPIRED, FAILED
        """
        import time as _time
        deadline = _time.time() + timeout
        poll_interval = 2  # Check every 2 seconds

        # Status sets
        FILL_STATUSES     = {"COMPLETE", "FILLED", "TRADED", "EXECUTED",
                             "PARTIAL_EXECUTED", "FULL", "DONE", "SUCCESS"}
        TERMINAL_STATUSES = {"CANCELLED", "REJECTED", "EXPIRED", "FAILED",
                             "CANCEL", "REJECT"}

        while _time.time() < deadline:
            try:
                status_resp = self._api.get_order(order_id=order_id)
                if not status_resp:
                    _time.sleep(poll_interval)
                    continue

                # Unwrap envelope if needed
                data = status_resp
                for env_key in ("data", "payload", "result"):
                    if env_key in status_resp and isinstance(status_resp[env_key], dict):
                        data = status_resp[env_key]
                        break

                raw_status = (
                    data.get("status") or
                    data.get("order_status") or
                    data.get("orderStatus") or
                    status_resp.get("status") or
                    ""
                )
                status = str(raw_status).upper().strip()

                logger.debug(f"Order {order_id} status: {status!r}")

                if status in FILL_STATUSES:
                    fill_price = (
                        data.get("average_price") or
                        data.get("avg_price") or
                        data.get("averagePrice") or
                        data.get("filled_price") or
                        status_resp.get("average_price") or
                        expected_price
                    )
                    return float(fill_price)

                if status in TERMINAL_STATUSES:
                    reason = (
                        data.get("message") or data.get("reason") or
                        data.get("errorMessage") or data.get("reject_reason") or
                        "No reason provided"
                    )
                    logger.warning(
                        f"[{format_ist_timestamp()}] Order {order_id} {status}: {reason}"
                    )
                    r = str(reason).lower()
                    if "circuit"  in r:
                        logger.error(f"⛔ CIRCUIT LIMIT hit: {order_id}")
                    elif "margin" in r or "fund" in r:
                        logger.error(f"⛔ INSUFFICIENT MARGIN: {order_id} — reduce qty")
                    elif "short"  in r or "sell" in r:
                        logger.error(f"⛔ SHORT REJECTED: {order_id} — check F&O eligibility")
                    return None

                # Still pending (PLACED / OPEN / TRANSIT / etc.) — keep polling
                _time.sleep(poll_interval)

            except Exception as e:
                logger.debug(f"Order status check error: {e}")
                _time.sleep(poll_interval)

        # Timeout
        order_type_label = "MARKET" if is_market_order else "LIMIT"
        logger.warning(
            f"[{format_ist_timestamp()}] {order_type_label} order {order_id} "
            f"fill not confirmed in {timeout}s"
        )
        return None  # Caller decides whether to cancel or trust the fill

    def _cancel_order(self, order_id: str) -> bool:
        """Cancel an unfilled order."""
        try:
            resp = self._api.cancel_order(order_id=order_id)
            logger.info(f"[{format_ist_timestamp()}] Order {order_id} cancelled")
            return bool(resp)
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Cancel order {order_id} failed: {e}")
            return False

    # --------------------------------------------------------

    def _create_position(
        self, signal: TradeSignal, quantity: int,
        entry_price: float, order_id: str
    ) -> Position:
        return Position(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=signal.stop_loss,
            target_1=signal.target_1,
            target_2=signal.target_2,
            atr=signal.atr,
            order_id=order_id,
            quality_grade=getattr(signal, "quality_grade", "B"),
            size_multiplier=getattr(signal, "size_multiplier", 1.0),
        )

    def _log_to_db(
        self, signal: TradeSignal, quantity: int,
        entry_price: float, order_id: str, live: bool
    ):
        """Log trade entry to SEBI-compliant SQLite journal."""
        try:
            conn = sqlite3.connect(TRADE_DB_PATH)
            conn.execute("""
                INSERT INTO execution_trades
                (order_id, symbol, direction, quantity, entry_price,
                 stop_loss, target_1, target_2, product, signal_score,
                 patterns, entry_time, live_trade, created_at, atr)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                order_id, signal.symbol, signal.direction, quantity, entry_price,
                signal.stop_loss, signal.target_1, signal.target_2, "MIS",
                signal.signal_score, str(signal.patterns),
                format_ist_timestamp(), int(live),
                format_ist_timestamp(), signal.atr,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB log error: {e}")

    def _update_db_exit(self, symbol: str, exit_price: float, reason: str):
        """Update trade journal with exit details."""
        try:
            pos_closed = None
            for t in self.risk_manager.state.closed_trades:
                if t.get("symbol") == symbol:
                    pos_closed = t
                    break
            if not pos_closed:
                return
            pnl = pos_closed.get("pnl", 0)
            conn = sqlite3.connect(TRADE_DB_PATH)
            conn.execute("""
                UPDATE execution_trades SET exit_price=?, pnl=?, pnl_pct=?,
                exit_time=?, exit_reason=?
                WHERE symbol=? AND exit_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (exit_price, pnl, pos_closed.get("pnl_pct", 0),
                  format_ist_timestamp(), reason, symbol))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB exit update error: {e}")


# ============================================================
# MODULE: alerts_telegram.py
# ============================================================
"""
alerts_telegram.py — NSE Momentum Groww AI Bot
Rich Telegram Alert Engine with Charts, Gemini AI Analysis, and EOD Reports

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).

Features:
  • Signal alerts with full rationale, R:R, grade, and projected P&L
  • Candlestick chart images (mplfinance) with entry arrow + indicators
  • Gemini AI signal explanation (plain English "why this trade")
  • Trade fill / exit / SL-hit alerts with P&L in green/red
  • Circuit breaker / kill-switch alerts
  • Status report with live positions and daily P&L
  • EOD performance report with equity curve chart
  • Morning brief (overnight analysis summary)
  • IST timestamps on every message

Usage:
  from alerts_telegram import TelegramAlerter
  alerter = TelegramAlerter(token, chat_id)
  alerter.send_signal(signal, candles_df)
"""

import io
import logging
import os
import textwrap
from datetime import datetime
from typing import Optional, Dict, List, Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests as _requests

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ── emoji palette ───────────────────────────────────────────
E = {
    "long":   "🟢",
    "short":  "🔴",
    "profit": "✅",
    "loss":   "❌",
    "warn":   "⚠️",
    "kill":   "🚨",
    "pause":  "⏸",
    "resume": "▶️",
    "chart":  "📊",
    "clock":  "🕐",
    "money":  "💰",
    "brain":  "🧠",
    "rocket": "🚀",
    "fire":   "🔥",
    "target": "🎯",
    "shield": "🛡",
    "trophy": "🏆",
    "pin":    "📌",
}


# ============================================================
# CHART BUILDER
# ============================================================

def _build_candle_chart(
    candles: pd.DataFrame,
    signal_price: float,
    stop_loss: float,
    target_1: float,
    target_2: float,
    direction: str,
    symbol: str,
    grade: str = "B",
) -> Optional[io.BytesIO]:
    """
    Build a candlestick chart with entry/SL/TP lines and a signal arrow.
    Returns a BytesIO PNG buffer, or None on failure.
    """
    try:
        import mplfinance as mpf
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        # Use last 60 candles for clarity
        df = candles.copy().tail(60)
        df.index = pd.DatetimeIndex(df.index)
        df.columns = [c.capitalize() for c in df.columns]

        # Ensure required columns
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col not in df.columns:
                return None

        entry_line = [signal_price] * len(df)
        sl_line    = [stop_loss]    * len(df)
        t1_line    = [target_1]     * len(df)
        t2_line    = [target_2]     * len(df)

        ap = [
            mpf.make_addplot(entry_line, color="#FFD700", width=1.5, linestyle="--"),
            mpf.make_addplot(sl_line,    color="#FF4444", width=1.2, linestyle=":"),
            mpf.make_addplot(t1_line,    color="#44FF88", width=1.0, linestyle="-."),
            mpf.make_addplot(t2_line,    color="#00FFCC", width=0.8, linestyle="-."),
        ]

        mc = mpf.make_marketcolors(
            up="#00C853", down="#FF1744",
            wick={"up": "#00C853", "down": "#FF1744"},
            edge={"up": "#00C853", "down": "#FF1744"},
            volume={"up": "#00C85380", "down": "#FF174480"},
        )
        s = mpf.make_mpf_style(
            base_mpl_style="dark_background",
            marketcolors=mc,
            gridstyle=":",
            gridcolor="#333333",
            facecolor="#0D1117",
            figcolor="#0D1117",
            rc={
                "axes.labelcolor": "#CCCCCC",
                "xtick.color": "#AAAAAA",
                "ytick.color": "#AAAAAA",
            },
        )

        fig, axes = mpf.plot(
            df,
            type="candle",
            style=s,
            addplot=ap,
            volume=True,
            figratio=(14, 7),
            figscale=1.2,
            title=f"\n{symbol}  |  {direction}  |  Grade: {grade}  |  {format_ist_timestamp()}",
            returnfig=True,
        )

        # Entry arrow annotation on last candle
        ax = axes[0]
        price_range = float(df["High"].max()) - float(df["Low"].min())
        offset = price_range * 0.05
        x_pos  = len(df) - 1
        color  = "#00C853" if direction == "LONG" else "#FF1744"
        if direction == "LONG":
            ax.annotate(
                "▲ ENTRY",
                xy=(x_pos, signal_price),
                xytext=(max(x_pos - 4, 0), signal_price - offset * 2),
                fontsize=8, color=color, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=color, lw=1.5),
            )
        else:
            ax.annotate(
                "▼ ENTRY",
                xy=(x_pos, signal_price),
                xytext=(max(x_pos - 4, 0), signal_price + offset * 2),
                fontsize=8, color=color, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=color, lw=1.5),
            )

        # Legend
        patches = [
            mpatches.Patch(color="#FFD700", label=f"Entry ₹{signal_price:.2f}"),
            mpatches.Patch(color="#FF4444", label=f"SL ₹{stop_loss:.2f}"),
            mpatches.Patch(color="#44FF88", label=f"T1 ₹{target_1:.2f}"),
            mpatches.Patch(color="#00FFCC", label=f"T2 ₹{target_2:.2f}"),
        ]
        ax.legend(
            handles=patches, loc="upper left", fontsize=7,
            facecolor="#1A1A2E", edgecolor="#444", labelcolor="white",
        )

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                    facecolor="#0D1117")
        plt.close(fig)
        buf.seek(0)
        return buf

    except Exception as e:
        logger.warning(f"Chart generation failed: {e}")
        return None


def _build_equity_curve(trades: List[Dict], capital: float) -> Optional[io.BytesIO]:
    """Build a daily equity curve PNG from completed trades list."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if not trades:
            return None

        pnls       = [t.get("pnl", 0) for t in trades]
        labels     = [t.get("symbol", f"T{i+1}")[:6] for i, t in enumerate(trades)]
        cumulative = list(pd.Series(pnls).cumsum())

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(12, 7),
            facecolor="#0D1117",
            gridspec_kw={"height_ratios": [2, 1]},
        )
        fig.patch.set_facecolor("#0D1117")

        # Equity curve
        x      = list(range(len(cumulative)))
        color  = "#00C853" if cumulative[-1] >= 0 else "#FF1744"
        ax1.plot(x, cumulative, color=color, linewidth=2.5, zorder=3)
        ax1.fill_between(x, cumulative, alpha=0.15, color=color)
        ax1.axhline(0, color="#555555", linestyle="--", lw=1)
        ax1.set_facecolor("#0D1117")
        ax1.set_title("Equity Curve — Today's Trades", color="white", fontsize=11)
        ax1.set_ylabel("Cumulative P&L (₹)", color="#CCCCCC", fontsize=9)
        ax1.tick_params(colors="#AAAAAA")
        ax1.grid(True, color="#333333", linestyle=":", alpha=0.5)

        # Per-trade bar
        bar_colors = ["#00C853" if p >= 0 else "#FF1744" for p in pnls]
        ax2.bar(x, pnls, color=bar_colors, width=0.7)
        ax2.axhline(0, color="#555555", linestyle="--", lw=1)
        ax2.set_facecolor("#0D1117")
        ax2.set_ylabel("Trade P&L (₹)", color="#CCCCCC", fontsize=9)
        ax2.tick_params(colors="#AAAAAA")
        ax2.set_xticks(x)
        ax2.set_xticklabels(labels, rotation=35, color="#AAAAAA", fontsize=7)
        ax2.grid(True, color="#333333", linestyle=":", alpha=0.5)

        plt.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor="#0D1117")
        plt.close(fig)
        buf.seek(0)
        return buf

    except Exception as e:
        logger.warning(f"Equity curve chart failed: {e}")
        return None


# ============================================================
# TELEGRAM ALERTER
# ============================================================

class TelegramAlerter:
    """
    Rich Telegram alert engine with full IST timestamps.

    All public methods are synchronous — async sending is handled internally.
    Gracefully degrades if telegram or matplotlib are not installed.
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id   = str(chat_id)
        self._bot      = None
        self._ready    = False
        self._init_bot()

    # --------------------------------------------------------
    # INIT
    # --------------------------------------------------------

    def _init_bot(self):
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured (missing token/chat_id) — alerts disabled")
            return
        # Use direct HTTP instead of python-telegram-bot to avoid asyncio event-loop issues
        self._api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self._ready = True
        logger.info(f"[{format_ist_timestamp()}] Telegram alerter ready (direct HTTP)")

    def initialize(self) -> bool:
        return self._ready

    def _is_ready(self) -> bool:
        return self._ready

    # --------------------------------------------------------
    # INTERNAL SEND
    # --------------------------------------------------------

    def _send(self, text: str, image_buf: Optional[io.BytesIO] = None,
              parse_mode: str = "Markdown") -> bool:
        """
        Send message via direct Telegram Bot HTTP API.
        No asyncio — pure requests, works from any thread/context.
        """
        if not self._ready:
            logger.debug("Telegram not ready — alert suppressed")
            return False
        try:
            if image_buf:
                image_buf.seek(0)
                resp = _requests.post(
                    f"{self._api_base}/sendPhoto",
                    data={
                        "chat_id":    self.chat_id,
                        "caption":    text[:1024],
                        "parse_mode": parse_mode,
                    },
                    files={"photo": ("chart.png", image_buf, "image/png")},
                    timeout=15,
                )
            else:
                resp = _requests.post(
                    f"{self._api_base}/sendMessage",
                    json={
                        "chat_id":    self.chat_id,
                        "text":       text[:4096],
                        "parse_mode": parse_mode,
                    },
                    timeout=15,
                )
            if not resp.ok:
                logger.warning(f"Telegram API {resp.status_code}: {resp.text[:200]}")
                return False
            return True
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    def send_text(self, text: str) -> bool:
        return self._send(text)

    def send_html(self, text: str) -> bool:
        """Send message with HTML parse mode (supports <b>, <i>, <code> tags)."""
        return self._send(text, parse_mode="HTML")

    # --------------------------------------------------------
    # SIGNAL ALERT  (core alert with chart)
    # --------------------------------------------------------

    def send_signal(self, signal, candles_df: Optional[pd.DataFrame] = None) -> bool:
        """
        Send a rich trade signal alert.
        `signal` is a TradeSignal dataclass from signal_generator.py
        """
        direction_emoji = E["long"] if signal.direction == "LONG" else E["short"]
        grade_emoji     = getattr(signal, "grade_emoji", E["target"])
        grade           = getattr(signal, "quality_grade", "B")
        size_mult       = getattr(signal, "size_multiplier", 1.0)
        qty             = getattr(signal, "quantity", 0)

        risk_amount = abs(signal.entry_price - signal.stop_loss) * qty
        proj_t1     = abs(signal.target_1 - signal.entry_price) * qty
        proj_t2     = abs(signal.target_2 - signal.entry_price) * qty
        patterns_str = ", ".join(signal.patterns[:5]) if signal.patterns else "—"
        mtf          = signal.timeframe_alignment or {}
        mtf_str      = " | ".join(f"{tf}: {v}" for tf, v in list(mtf.items())[:3]) if mtf else "—"
        rationale    = textwrap.shorten(
            signal.rationale or "Signal confirmed by multi-timeframe momentum",
            350, placeholder="...",
        )

        text = (
            f"{direction_emoji} *{signal.direction} SIGNAL — {signal.symbol}*\n"
            f"{grade_emoji} Grade: `{grade}` | Score: `{signal.signal_score:.0f}/100` | Size: `{size_mult:.1f}x`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['target']} *Entry:* `₹{signal.entry_price:.2f}`\n"
            f"🛑 *Stop Loss:* `₹{signal.stop_loss:.2f}`\n"
            f"🎯 *Target 1:* `₹{signal.target_1:.2f}`\n"
            f"🎯 *Target 2:* `₹{signal.target_2:.2f}`\n"
            f"📐 *R:R:* `{signal.risk_reward:.1f}:1` | Qty: `{qty}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['money']} Risk: `₹{risk_amount:,.0f}` | T1 P&L: `₹{proj_t1:,.0f}` | T2: `₹{proj_t2:,.0f}`\n"
            f"📈 Patterns: `{patterns_str}`\n"
            f"⏱ MTF: `{mtf_str}`\n"
            f"📰 News: `{'Clear' if signal.news_clear else 'BLOCKED — event nearby'}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['brain']} *AI Rationale:*\n_{rationale}_\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['clock']} `{signal.signal_time or format_ist_timestamp()}`"
        )

        chart = None
        if candles_df is not None and not candles_df.empty:
            chart = _build_candle_chart(
                candles_df, signal.entry_price, signal.stop_loss,
                signal.target_1, signal.target_2,
                signal.direction, signal.symbol, grade,
            )

        return self._send(text, chart)

    # --------------------------------------------------------
    # TRADE FILL
    # --------------------------------------------------------

    def send_trade_fill(self, symbol: str, direction: str, qty: int,
                        price: float, order_id: str = "") -> bool:
        emoji = E["long"] if direction == "LONG" else E["short"]
        text = (
            f"{emoji} *ORDER FILLED — {symbol}*\n"
            f"Direction: `{direction}` | Qty: `{qty}` | Price: `₹{price:.2f}`\n"
            f"Order ID: `{order_id}`\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # EXIT ALERT (Profit / Loss)
    # --------------------------------------------------------

    def send_exit(self, symbol: str, direction: str, qty: int,
                  entry: float, exit_price: float, pnl: float,
                  reason: str = "Target", order_id: str = "") -> bool:
        pnl_emoji = E["profit"] if pnl >= 0 else E["loss"]
        pnl_str   = f"+₹{pnl:,.0f}" if pnl >= 0 else f"-₹{abs(pnl):,.0f}"
        pct = ((exit_price - entry) / entry * 100) if direction == "LONG" else ((entry - exit_price) / entry * 100)
        text = (
            f"{pnl_emoji} *EXIT — {symbol}*\n"
            f"Direction: `{direction}` | Qty: `{qty}`\n"
            f"Entry: `₹{entry:.2f}` → Exit: `₹{exit_price:.2f}` (`{pct:+.2f}%`)\n"
            f"P&L: *{pnl_str}*\n"
            f"Reason: `{reason}`\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # STOP-LOSS HIT
    # --------------------------------------------------------

    def send_sl_hit(self, symbol: str, direction: str, entry: float,
                    sl_price: float, loss: float) -> bool:
        text = (
            f"{E['loss']} *STOP-LOSS HIT — {symbol}*\n"
            f"Direction: `{direction}` | Entry: `₹{entry:.2f}` | SL: `₹{sl_price:.2f}`\n"
            f"Loss: `₹{abs(loss):,.0f}`\n"
            f"{E['warn']} Reviewing consecutive losses...\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # CIRCUIT BREAKER / KILL
    # --------------------------------------------------------

    def send_circuit_breaker(self, reason: str) -> bool:
        text = (
            f"{E['kill']} *CIRCUIT BREAKER TRIGGERED*\n"
            f"Reason: `{reason}`\n"
            f"All new entries PAUSED. Existing positions being monitored.\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    def send_kill_alert(self) -> bool:
        text = (
            f"{E['kill']} *KILL SWITCH ACTIVATED*\n"
            f"Emergency stop received. ALL positions being squared off.\n"
            f"New trading HALTED for the day.\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # STATUS REPORT
    # --------------------------------------------------------

    def send_status(self, risk_manager=None) -> bool:
        if risk_manager is None:
            return self._send(
                f"{E['chart']} *Bot Status*\n"
                f"`{format_ist_timestamp()}`\nNo risk data available."
            )

        state     = getattr(risk_manager, "state", None)
        positions = state.positions      if state else {}   # positions live on state, not risk_manager
        daily_pnl = state.daily_pnl     if state else 0
        capital   = state.daily_capital if state else 0
        n_trades  = state.daily_trades  if state else 0
        wins      = state.winning_trades if state else 0
        wr_pct    = (wins / n_trades * 100) if n_trades > 0 else 0.0
        paused    = state.trading_paused if state else False

        pnl_emoji = E["profit"] if daily_pnl >= 0 else E["loss"]
        pnl_str   = f"+₹{daily_pnl:,.0f}" if daily_pnl >= 0 else f"-₹{abs(daily_pnl):,.0f}"

        lines = [
            f"{E['chart']} *Bot Status — {format_ist_timestamp()}*",
            f"Daily P&L: {pnl_emoji} *{pnl_str}*",
            f"Capital: `₹{capital:,.0f}` | Trades: `{n_trades}` | Win Rate: `{wr_pct:.1f}%`",
            f"Open Positions: `{len(positions)}` | State: `{'PAUSED' if paused else 'ACTIVE'}`",
        ]
        if positions:
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            for sym, pos in list(positions.items())[:8]:
                p_pnl  = getattr(pos, "pnl", 0)
                p_emj  = "🟢" if p_pnl >= 0 else "🔴"
                cur    = getattr(pos, "current_price", pos.entry_price)
                lines.append(
                    f"{p_emj} `{sym}` {pos.direction} "
                    f"₹{pos.entry_price:.2f}→₹{cur:.2f} | "
                    f"P&L: `{'+' if p_pnl>=0 else ''}{p_pnl:,.0f}`"
                )

        return self._send("\n".join(lines))

    # --------------------------------------------------------
    # PAUSE / RESUME
    # --------------------------------------------------------

    def send_pause(self, reason: str = "") -> bool:
        text = (
            f"{E['pause']} *Trading PAUSED*\n"
            f"{'Reason: ' + reason if reason else 'Manual pause.'}\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    def send_resume(self) -> bool:
        return self._send(
            f"{E['resume']} *Trading RESUMED*\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )

    # --------------------------------------------------------
    # SQUARE-OFF WARNING
    # --------------------------------------------------------

    def send_squareoff_warning(self, positions) -> bool:
        n = len(positions) if hasattr(positions, "__len__") else 0
        text = (
            f"{E['warn']} *SQUARE-OFF WARNING — 3:20 PM IST*\n"
            f"`{n}` open position(s) will be force-closed at market price.\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # MORNING BRIEF
    # --------------------------------------------------------

    def send_morning_brief(
        self,
        brief_or_watchlist=None,
        available: float = 0,
        nifty_open: float = 0,
        oc_summary: str = "",
        fii_summary: str = "",
    ) -> bool:
        """
        Backward-compatible morning brief.

        Can be called two ways:
          1. send_morning_brief(brief_dict)             — new style (dict from overnight_analyzer)
          2. send_morning_brief(watchlist, avail, nifty) — legacy style from main.py
        """
        # Detect calling style
        if isinstance(brief_or_watchlist, dict):
            brief      = brief_or_watchlist
            bias       = brief.get("day_bias", "NEUTRAL")
            bias_score = brief.get("bias_score", 0)
            vix_data   = brief.get("vix", {})
            vix        = vix_data.get("vix", 0) if isinstance(vix_data, dict) else 0
            gift_data  = brief.get("gift_nifty", {})
            gap_pct    = gift_data.get("gap_pct", 0) if isinstance(gift_data, dict) else 0
            risks      = brief.get("key_risks", [])
            ai_thesis  = brief.get("ai_thesis", "")
            watchlist  = brief.get("top_watchlist", [])
            avail_cap  = available or brief.get("available_capital", 0)
            nifty_ltp  = nifty_open or brief.get("nifty_open", 0)
        else:
            # Legacy: send_morning_brief(watchlist_list, available_float, nifty_open_float)
            watchlist  = brief_or_watchlist or []
            avail_cap  = available
            nifty_ltp  = nifty_open
            bias, bias_score, vix, gap_pct = "NEUTRAL", 0, 15.0, 0.0
            risks, ai_thesis = [], ""

        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")
        wl_str     = ", ".join(f"`{s}`" for s in watchlist[:10]) if watchlist else "—"
        risks_str  = "\n".join(f"  • {r}" for r in risks[:4]) if risks else "  No high-impact events"
        thesis     = textwrap.shorten(ai_thesis or "Scanning for momentum setups...", 350, placeholder="...")

        text = (
            f"{E['rocket']} *MORNING BRIEF — {format_ist_timestamp()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{bias_emoji} Day Bias: *{bias}* (score: `{bias_score:+d}`)\n"
            f"VIX: `{vix:.1f}` | Gift Nifty: `{gap_pct:+.2f}%` | "
            f"Nifty: `₹{nifty_ltp:,.0f}` | Capital: `₹{avail_cap:,.0f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"*Key Risks:*\n{risks_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"*Watchlist ({len(watchlist)} stocks):* {wl_str}\n"
        )
        if oc_summary:
            text += f"━━━━━━━━━━━━━━━━━━━━\n{oc_summary}\n"
        if fii_summary:
            text += f"━━━━━━━━━━━━━━━━━━━━\n{fii_summary}\n"
        if ai_thesis:
            text += f"━━━━━━━━━━━━━━━━━━━━\n{E['brain']} *AI Thesis:* _{thesis}_"
        return self._send(text)

    # --------------------------------------------------------
    # ENTRY ALERT (alias for send_signal — used by main.py)
    # --------------------------------------------------------

    def send_entry_alert(self, signal, candles_df=None) -> bool:
        """Alias for send_signal() — called by main.py after order placement."""
        return self.send_signal(signal, candles_df)

    # --------------------------------------------------------
    # EXIT ALERT — positional signature used by main.py
    # --------------------------------------------------------

    def send_exit_alert(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        reason: str = "Target",
        order_id: str = "",
    ) -> bool:
        """
        Exit alert called by main.py after a position is closed.
        Signature: (symbol, direction, entry_price, exit_price, qty, pnl, reason)
        Delegates to send_exit() which has qty before entry in its signature.
        """
        return self.send_exit(
            symbol=symbol,
            direction=direction,
            qty=quantity,
            entry=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            reason=reason,
            order_id=order_id,
        )

    # --------------------------------------------------------
    # EOD PERFORMANCE REPORT
    # --------------------------------------------------------

    def send_eod_report(self, risk_manager=None,
                        trades: Optional[List[Dict]] = None,
                        capital: float = 0) -> bool:
        state      = getattr(risk_manager, "state", None) if risk_manager else None
        all_trades = trades or []
        daily_pnl  = state.daily_pnl      if state else sum(t.get("pnl", 0) for t in all_trades)
        n_trades   = state.daily_trades   if state else len(all_trades)
        wins       = state.winning_trades if state else sum(1 for t in all_trades if t.get("pnl", 0) > 0)
        losses     = state.losing_trades  if state else sum(1 for t in all_trades if t.get("pnl", 0) < 0)
        wr_pct     = (wins / n_trades * 100) if n_trades > 0 else 0.0
        pnl_pct    = (daily_pnl / capital * 100) if capital > 0 else 0.0

        pnl_emoji  = E["trophy"] if daily_pnl > 0 else (E["loss"] if daily_pnl < 0 else E["chart"])
        pnl_str    = (f"+₹{daily_pnl:,.0f} (+{pnl_pct:.2f}%)"
                      if daily_pnl >= 0
                      else f"-₹{abs(daily_pnl):,.0f} ({pnl_pct:.2f}%)")

        best  = max(all_trades, key=lambda t: t.get("pnl", 0), default=None)
        worst = min(all_trades, key=lambda t: t.get("pnl", 0), default=None)
        best_str  = f"{best.get('symbol','?')} `+₹{best.get('pnl',0):,.0f}`"   if best  else "—"
        worst_str = f"{worst.get('symbol','?')} `-₹{abs(worst.get('pnl',0)):,.0f}`" if worst else "—"

        # Daily target slice = 5% monthly ÷ ~22 trading days
        target_hit = (daily_pnl >= capital * 0.05 / 22) if capital > 0 else False

        text = (
            f"{pnl_emoji} *END-OF-DAY REPORT — {format_ist_timestamp()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Net P&L: *{pnl_str}*\n"
            f"Trades: `{n_trades}` | Wins: `{wins}` | Losses: `{losses}`\n"
            f"Win Rate: `{wr_pct:.1f}%`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{E['fire']} Best Trade:  {best_str}\n"
            f"{E['loss']} Worst Trade: {worst_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{'Daily target achieved!' if target_hit else 'Below daily target slice (5%/mo)'}\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )

        chart = _build_equity_curve(all_trades, capital) if all_trades else None
        return self._send(text, chart)

    # --------------------------------------------------------
    # TOKEN REFRESH
    # --------------------------------------------------------

    def send_token_refresh(self, success: bool, method: str = "TOTP") -> bool:
        emoji  = E["profit"] if success else E["loss"]
        status = "succeeded" if success else "FAILED — using previous token"
        text = (
            f"{emoji} *Groww Token Refresh {status.split()[0].capitalize()}*\n"
            f"Method: `{method}` | Status: `{status}`\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # WATCHLIST UPDATE
    # --------------------------------------------------------

    def send_watchlist(self, symbols: List[str]) -> bool:
        wl = ", ".join(f"`{s}`" for s in symbols[:20])
        text = (
            f"{E['pin']} *Watchlist Updated*\n"
            f"Scanning `{len(symbols)}` stocks:\n{wl}\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)

    # --------------------------------------------------------
    # AI INSIGHT
    # --------------------------------------------------------

    def send_ai_insight(self, symbol: str, insight: str,
                        trade_review: str = "") -> bool:
        text = (
            f"{E['brain']} *AI Insight — {symbol}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_{textwrap.shorten(insight, 600, placeholder='...')}_\n"
        )
        if trade_review:
            text += (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"*Trade Review:*\n"
                f"_{textwrap.shorten(trade_review, 400, placeholder='...')}_\n"
            )
        text += f"{E['clock']} `{format_ist_timestamp()}`"
        return self._send(text)

    # --------------------------------------------------------
    # GENERIC ALERT
    # --------------------------------------------------------

    def send_alert(self, title: str, body: str, level: str = "INFO") -> bool:
        level_emoji = {
            "INFO":    "ℹ️",
            "WARNING": E["warn"],
            "ERROR":   "🚨",
            "SUCCESS": E["profit"],
        }.get(level, "ℹ️")
        text = (
            f"{level_emoji} *{title}*\n"
            f"{body}\n"
            f"{E['clock']} `{format_ist_timestamp()}`"
        )
        return self._send(text)


# ============================================================
# MODULE: watchlist_manager.py
# ============================================================
"""
watchlist_manager.py — NSE Momentum Groww AI Bot
Dynamic NSE Stock Selection — Scans for Best Momentum Candidates

18yr Rule: "Don't trade 100 stocks. Find the 5-10 that are ALIVE today.
Momentum clusters. Where money flows, more money follows."

Features:
- 200+ stock universe (Nifty50 + Nifty100 + select mid-caps)
- Pre-market momentum scan at 9:00 AM IST
- Sector strength ranking — trade leaders not laggards
- ADR filter (min 1.5% daily range for intraday viability)
- Volume filter (today's vol vs 20-day average)
- Relative strength vs Nifty50
- Auto-removes symbols from blacklist (from self-learning)
"""

import logging
from datetime import timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date
import config

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# -----------------------------------------------------------
# FULL LIQUID UNIVERSE (NSE — high volume / intraday friendly)
# -----------------------------------------------------------
LIQUID_UNIVERSE = [
    # Nifty 50 core
    "RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN","BHARTIARTL",
    "ITC","KOTAKBANK","LT","WIPRO","HCLTECH","AXISBANK","MARUTI","SUNPHARMA",
    "TATAMOTORS","BAJFINANCE","ADANIENT","ULTRACEMCO","TITAN","ASIANPAINT",
    "POWERGRID","NTPC","HINDUNILVR","ONGC","JSWSTEEL","TATASTEEL",
    "INDUSINDBK","BAJAJFINSV","TECHM","NESTLEIND","CIPLA","DIVISLAB",
    "DRREDDY","EICHERMOT","APOLLOHOSP","COALINDIA","BPCL","HEROMOTOCO",
    "BRITANNIA","GRASIM","TATACONSUM","HINDALCO","UPL","ADANIPORTS",
    "SHREECEM","LTIM","HDFCLIFE","SBILIFE","PIDILITIND",
    # High-momentum mid-caps
    "ZOMATO","DMART","NYKAA","POLICYBZR","PAYTM","IRCTC","HAL",
    "BEL","BHEL","SAIL","IDBI","BANDHANBNK","MUTHOOTFIN","MANAPPURAM",
    "RECLTD","PFC","IRFC","NMDC","NATIONALUM","HINDCOPPER",
    "TATAPOWER","ADANIGREEN","ADANITRANS","TORNTPOWER","CESC",
    "VOLTAS","HAVELLS","CROMPTON","DIXON","AMBER","POLYCAB",
    "MINDTREE","MPHASIS","COFORGE","PERSISTENT","SONACOMS",
    "JUBLFOOD","WESTLIFE","DEVYANI","SAPPHIRE",
    "BALKRISIND","APOLLOTYRE","MRF","CEATLTD",
    "TRENT","SHOPERSTOP","ABFRL","MANYAVAR",
    "SRF","DEEPAKNTR","TATACHEM","GNFC",
    "BANKBARODA","UNIONBANK","PNB","CANBK","FEDERALBNK",
]

# Sector mapping (simplified)
SECTOR_MAP = {
    "IT":        ["TCS","INFY","WIPRO","HCLTECH","TECHM","LTIM","MPHASIS","COFORGE","PERSISTENT"],
    "BANKING":   ["HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK","INDUSINDBK","BANDHANBNK","FEDERALBNK"],
    "PHARMA":    ["SUNPHARMA","CIPLA","DRREDDY","DIVISLAB","APOLLOHOSP"],
    "AUTO":      ["MARUTI","TATAMOTORS","EICHERMOT","HEROMOTOCO","BAJAJFINSV","BALKRISIND","MRF"],
    "ENERGY":    ["RELIANCE","ONGC","BPCL","TATAPOWER","ADANIGREEN","NTPC","POWERGRID","COALINDIA"],
    "METALS":    ["TATASTEEL","JSWSTEEL","HINDALCO","SAIL","NMDC","NATIONALUM","HINDCOPPER"],
    "FMCG":      ["ITC","HINDUNILVR","NESTLEIND","BRITANNIA","TATACONSUM","DABUR"],
    "REALTY":    ["DLF","GODREJPROP","PRESTIGE","OBEROIRLTY"],
    "FINANCE":   ["BAJFINANCE","HDFCLIFE","SBILIFE","MUTHOOTFIN","MANAPPURAM","PFC","RECLTD"],
    "INFRA":     ["LT","ADANIPORTS","HAL","BEL","BHEL","IRFC"],
}


class WatchlistManager:
    """Dynamic watchlist engine - finds best momentum stocks each morning."""

    def __init__(self, name="momentum_watchlist", symbols=None):
        self.name = name
        self.symbols = symbols if symbols is not None else []
        self.last_updated = None

        # Scan state — initialised here so get_watchlist() never hits AttributeError
        self._watchlist: List[str] = []
        self._scored_stocks: List[Dict] = []
        self._sector_leaders: Dict = {}
        self._last_scan_time: Optional[float] = None
        self._scan_ttl: int = 15 * 60  # Rescan every 15 min

    # -------------------------------------------------------------------------
    # MAIN WATCHLIST GETTER
    # -------------------------------------------------------------------------

    def get_watchlist(self, data_fetcher=None, learner=None) -> List[str]:
        """
        Return today's active watchlist.
        Order: user-defined > momentum-scanned > default.
        """
        # User-defined list takes priority
        if config.CUSTOM_WATCHLIST_STR:
            wl = [s.strip().upper() for s in config.CUSTOM_WATCHLIST_STR.split(",") if s.strip()]
            if learner:
                wl = [s for s in wl if not learner.is_symbol_blacklisted(s)]
            return wl

        # Check if scan is fresh
        if (data_fetcher and
                (self._last_scan_time is None or
                 (get_current_ist_time().timestamp() - self._last_scan_time) > self._scan_ttl)):
            self._run_momentum_scan(data_fetcher, learner)

        if self._watchlist:
            return self._watchlist

        # Fallback to default
        return config.DEFAULT_WATCHLIST[:15]

    # -------------------------------------------------------
    # MOMENTUM SCAN
    # -------------------------------------------------------

    def _run_momentum_scan(self, data_fetcher, learner=None, top_n: int = 15):
        """
        Score all liquid stocks by momentum.
        18yr rule: "Trade what's moving RIGHT NOW, not what moved yesterday."
        """
        logger.info(f"[{format_ist_timestamp()}] Running momentum scan on {len(LIQUID_UNIVERSE)} stocks...")
        scores = []

        import time as _time
        for i, symbol in enumerate(LIQUID_UNIVERSE):
            # Skip blacklisted symbols
            if learner and learner.is_symbol_blacklisted(symbol):
                continue
            try:
                score = self._score_stock(symbol, data_fetcher)
                if score and score["tradeable"]:
                    scores.append(score)
            except Exception as e:
                logger.debug(f"Score failed {symbol}: {e}")
            # Throttle: 5 requests per 2s to stay within Groww rate limits
            if i % 5 == 4:
                _time.sleep(2.0)

        if not scores:
            logger.warning(f"[{format_ist_timestamp()}] Momentum scan returned no results")
            self._watchlist = config.DEFAULT_WATCHLIST[:top_n]
            return

        # Sort by composite momentum score
        scores.sort(key=lambda x: x["momentum_score"], reverse=True)
        self._scored_stocks = scores

        # Take top N
        self._watchlist = [s["symbol"] for s in scores[:top_n]]
        self._last_scan_time = get_current_ist_time().timestamp()

        # Update sector leaders
        self._sector_leaders = self._rank_sector_leaders(scores)

        logger.info(
            f"[{format_ist_timestamp()}] Watchlist updated: {self._watchlist[:8]}... "
            f"({len(self._watchlist)} stocks)"
        )

    def _score_stock(self, symbol: str, data_fetcher) -> Optional[Dict]:
        """Score a single stock for momentum potential."""
        try:
            quote = data_fetcher.get_quote(symbol)
            if not quote or quote["ltp"] <= 0:
                return None

            ltp         = float(quote["ltp"])
            change_pct  = float(quote.get("change_pct", 0))
            volume      = int(quote.get("volume", 0))
            high        = float(quote.get("high", ltp))
            low         = float(quote.get("low", ltp))

            # ADR check — must be above 1.5% for intraday viability
            intraday_range_pct = (high - low) / low * 100 if low > 0 else 0

            # Price filter: ₹50–₹5000 (outside this is difficult to trade)
            if ltp < 50 or ltp > 8000:
                return None

            # Momentum score
            score = 0.0

            # 1. Price change momentum (most important)
            score += abs(change_pct) * 10
            if change_pct > 0:
                score += 5   # Bullish bias bonus

            # 2. Intraday range (are we getting movement today?)
            score += intraday_range_pct * 5

            # 3. Volume factor — above 1M shares = liquid
            if volume > 1_000_000:
                score += 15
            elif volume > 500_000:
                score += 8
            elif volume < 100_000:
                score -= 20  # Too illiquid

            # 4. Price momentum strength
            if abs(change_pct) > 2:
                score += 20  # Strong mover
            elif abs(change_pct) > 1:
                score += 10

            tradeable = (
                intraday_range_pct >= 0.8 and   # Some movement today
                volume >= 100_000 and             # Minimum liquidity
                ltp >= 50                         # Price filter
            )

            return {
                "symbol":        symbol,
                "ltp":           ltp,
                "change_pct":    change_pct,
                "volume":        volume,
                "range_pct":     round(intraday_range_pct, 2),
                "momentum_score": round(score, 1),
                "tradeable":     tradeable,
            }
        except Exception as e:
            logger.debug(f"[{format_ist_timestamp()}] Score error {symbol}: {e}")
            return None

    # -------------------------------------------------------
    # SECTOR LEADERSHIP
    # -------------------------------------------------------

    def _rank_sector_leaders(self, scores: List[Dict]) -> Dict[str, List[str]]:
        """
        Find top 2 stocks per sector by momentum score.
        18yr rule: "Trade sector leaders — they move first and furthest."
        """
        score_map = {s["symbol"]: s["momentum_score"] for s in scores}
        sector_leaders = {}

        for sector, symbols in SECTOR_MAP.items():
            sector_scores = [
                (sym, score_map.get(sym, 0))
                for sym in symbols if sym in score_map
            ]
            sector_scores.sort(key=lambda x: x[1], reverse=True)
            leaders = [sym for sym, _ in sector_scores[:2]]
            if leaders:
                sector_leaders[sector] = leaders

        return sector_leaders

    def get_sector_leaders(self) -> Dict[str, List[str]]:
        return self._sector_leaders

    def get_top_movers(self, n: int = 5) -> List[Dict]:
        """Return top N momentum stocks with their scores."""
        return self._scored_stocks[:n]

    def get_sector_for_symbol(self, symbol: str) -> str:
        for sector, syms in SECTOR_MAP.items():
            if symbol in syms:
                return sector
        return "OTHER"

    # -------------------------------------------------------
    # RELATIVE STRENGTH
    # -------------------------------------------------------

    def calculate_relative_strength(
        self,
        stock_change_pct: float,
        nifty_change_pct: float,
    ) -> float:
        """
        Relative strength = stock return - Nifty return.
        Positive = outperforming Nifty (strong stock).
        18yr rule: "Buy the strongest stock in the strongest sector.
        Never buy a stock weaker than Nifty in a weak market."
        """
        return stock_change_pct - nifty_change_pct

    def filter_by_relative_strength(
        self,
        symbols: List[str],
        data_fetcher,
        nifty_change_pct: float,
        min_rs: float = 0.3,
    ) -> List[str]:
        """Keep only stocks outperforming Nifty by at least min_rs%."""
        strong = []
        for sym in symbols:
            try:
                q = data_fetcher.get_quote(sym)
                if q:
                    rs = self.calculate_relative_strength(q.get("change_pct", 0), nifty_change_pct)
                    if rs >= min_rs:
                        strong.append(sym)
            except Exception:
                pass
        return strong if strong else symbols[:10]

    def format_watchlist_message(self) -> str:
        """Telegram-friendly watchlist summary."""
        if not self._scored_stocks:
            wl = config.DEFAULT_WATCHLIST[:10]
            return "📋 Watchlist (default):\n" + "\n".join(f"  • {s}" for s in wl)

        lines = ["📋 Today's Momentum Watchlist\n"]
        for i, s in enumerate(self._scored_stocks[:10], 1):
            arrow = "🟢" if s["change_pct"] >= 0 else "🔴"
            lines.append(
                f"{i}. {arrow} {s['symbol']:12s} "
                f"₹{s['ltp']:,.0f}  {s['change_pct']:+.1f}%  "
                f"Vol:{s['volume']//1000:.0f}K  "
                f"Score:{s['momentum_score']:.0f}"
            )
        return "\n".join(lines)


# ============================================================
# MODULE: news_filter.py
# ============================================================
"""
news_filter.py — NSE Momentum Groww AI Bot
News/Sentiment Filter — Economic Calendar + NewsAPI

Skips signals 30 minutes before/after:
- RBI policy decisions
- GDP / CPI releases
- Nifty50 component earnings
- Any high-impact market events

Sources: NewsAPI, economic calendar RSS feeds
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

import requests
import feedparser

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# High-impact keywords that trigger blackout
HIGH_IMPACT_KEYWORDS = [
    "rbi", "monetary policy", "repo rate", "cpi", "gdp", "inflation",
    "election", "union budget", "sebi", "circuit breaker", "market halt",
    "fed rate", "federal reserve", "us jobs", "nonfarm", "quantitative",
    "credit policy", "msci", "ftse rebalance", "index rebalance",
    "earnings results", "quarterly results", "q1 results", "q2 results",
    "q3 results", "q4 results", "board meeting dividend",
]

# Economic calendar RSS feeds (free sources)
CALENDAR_FEEDS = [
    "https://www.goodreturns.in/rss/news.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
]


class NewsFilter:
    """
    Filters out trading signals near high-impact news events.
    Uses NewsAPI for real-time sentiment and RSS for calendar.
    """

    def __init__(self, news_api_key: str = "", blackout_minutes: int = 30):
        self.api_key = news_api_key
        self.blackout_minutes = blackout_minutes
        self._event_cache: List[Dict] = []
        self._cache_refreshed: Optional[datetime] = None
        self._sentiment_cache: Dict[str, Dict] = {}
        self._last_refresh_ist: Optional[datetime] = None

    def is_safe_to_trade(self, symbol: str = "") -> bool:
        """
        Check if it's safe to trade right now.
        Returns False if within blackout period of any high-impact event.
        """
        now = get_current_ist_time()

        # Refresh events every 30 minutes
        if (self._last_refresh_ist is None or
                (now - self._last_refresh_ist).total_seconds() > 1800):
            self._refresh_events()

        # Check if any event is within blackout window
        for event in self._event_cache:
            event_time = event.get("time")
            if not event_time:
                continue
            time_diff = abs((now - event_time).total_seconds() / 60)
            if time_diff <= self.blackout_minutes:
                logger.warning(
                    f"[{format_ist_timestamp()}] 🚫 NEWS BLACKOUT: "
                    f"'{event['title'][:60]}' in {time_diff:.0f} min window"
                )
                return False

        # Check symbol-specific sentiment
        if symbol:
            sentiment = self.get_symbol_sentiment(symbol)
            if sentiment.get("strong_negative"):
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: strong negative news — caution"
                )
                # Don't block but warn (return True but log)

        return True

    def _refresh_events(self):
        """Refresh event cache from RSS feeds and NewsAPI."""
        self._event_cache = []
        self._last_refresh_ist = get_current_ist_time()

        # 1. Parse RSS economic calendars
        for feed_url in CALENDAR_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "").lower()
                    if any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
                        # Try to parse published time
                        pub = entry.get("published_parsed")
                        if pub:
                            from time import mktime
                            dt = datetime.fromtimestamp(mktime(pub), tz=IST)
                        else:
                            dt = get_current_ist_time()
                        self._event_cache.append({
                            "title": entry.get("title", ""),
                            "time": dt,
                            "source": "RSS",
                        })
            except Exception as e:
                logger.debug(f"RSS feed error ({feed_url}): {e}")

        # 2. NewsAPI for recent high-impact financial news
        if self.api_key:
            try:
                url = "https://newsapi.org/v2/everything"
                params = {
                    "q": "RBI OR budget OR GDP OR CPI OR NSE OR Sensex",
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "apiKey": self.api_key,
                }
                resp = requests.get(url, params=params, timeout=5)
                if resp.status_code == 200:
                    articles = resp.json().get("articles", [])
                    for art in articles:
                        title = (art.get("title") or "").lower()
                        if any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
                            pub_str = art.get("publishedAt", "")
                            try:
                                pub_dt = datetime.fromisoformat(
                                    pub_str.replace("Z", "+00:00")
                                ).astimezone(IST)
                            except Exception:
                                pub_dt = get_current_ist_time()
                            self._event_cache.append({
                                "title": art.get("title", ""),
                                "time": pub_dt,
                                "source": "NewsAPI",
                            })
            except Exception as e:
                logger.debug(f"NewsAPI error: {e}")

        if self._event_cache:
            logger.info(
                f"[{format_ist_timestamp()}] News filter: "
                f"{len(self._event_cache)} high-impact events loaded"
            )

    def get_symbol_sentiment(self, symbol: str) -> Dict:
        """
        Get basic sentiment score for a symbol from recent news.
        Returns: {"score": float, "positive": bool, "strong_negative": bool}
        """
        if not self.api_key:
            return {"score": 0, "positive": False, "strong_negative": False}

        # Check cache (5 minute TTL)
        if symbol in self._sentiment_cache:
            cached = self._sentiment_cache[symbol]
            age = (get_current_ist_time() - cached["cached_at"]).total_seconds()
            if age < 300:
                return cached

        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": symbol,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 10,
                "apiKey": self.api_key,
            }
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return {"score": 0, "positive": False, "strong_negative": False}

            articles = resp.json().get("articles", [])
            if not articles:
                return {"score": 0, "positive": False, "strong_negative": False}

            # Simple keyword-based sentiment
            positive_words = ["surge", "rally", "gain", "buy", "bullish", "upgrade",
                              "target raised", "beat", "outperform", "strong", "profit"]
            negative_words = ["fall", "drop", "loss", "sell", "bearish", "downgrade",
                              "target cut", "miss", "underperform", "weak", "fraud",
                              "penalty", "fine", "scam", "investigation"]

            pos_count = neg_count = 0
            for art in articles:
                text = (
                    (art.get("title") or "") + " " +
                    (art.get("description") or "")
                ).lower()
                pos_count += sum(1 for w in positive_words if w in text)
                neg_count += sum(1 for w in negative_words if w in text)

            score = (pos_count - neg_count) / max(len(articles), 1)
            result = {
                "score": round(score, 2),
                "positive": score > 0.3,
                "strong_negative": neg_count > pos_count * 2 and neg_count >= 3,
                "cached_at": get_current_ist_time(),
            }
            self._sentiment_cache[symbol] = result
            return result

        except Exception as e:
            logger.debug(f"Sentiment fetch error for {symbol}: {e}")
            return {"score": 0, "positive": False, "strong_negative": False}

    def get_nifty_sentiment_score(self) -> float:
        """Get overall market sentiment score (-1 to +1)."""
        try:
            if not self.api_key:
                return 0.0
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": "Nifty50 Sensex India stock market",
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 15,
                "apiKey": self.api_key,
            }
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return 0.0
            articles = resp.json().get("articles", [])
            positive_words = ["surge", "rally", "gain", "bullish", "up", "rise",
                              "breakout", "all-time high", "record"]
            negative_words = ["fall", "drop", "crash", "bearish", "down", "decline",
                              "selloff", "fear", "uncertainty"]
            pos = neg = 0
            for art in articles:
                text = ((art.get("title") or "") + " " +
                        (art.get("description") or "")).lower()
                pos += sum(1 for w in positive_words if w in text)
                neg += sum(1 for w in negative_words if w in text)
            total = pos + neg
            if total == 0:
                return 0.0
            return round((pos - neg) / total, 2)
        except Exception:
            return 0.0


# ============================================================
# MODULE: fii_dii_tracker.py
# ============================================================
"""
fii_dii_tracker.py — NSE Momentum Groww AI Bot
FII / DII Daily Flow Tracker

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).

18 years of experience principle:
"FIIs (Foreign Institutional Investors) dominate NSE direction.
 When FIIs are net buyers, Nifty goes up. When they sell, Nifty falls.
 DII (Domestic) buying on dips is the floor. FII buying is the engine.
 Never fight FII trend. Trade WITH institutional flow."

What this module provides:
  • FII net buy/sell (cash + futures) from NSE provisional data
  • DII net activity
  • 5-day rolling flow trend (momentum of FII buying/selling)
  • Derivative participant data (FII index futures positioning)
  • Combined flow score for signal adjustment
  • Historical flow database for trend analysis

Data Sources:
  1. NSE FII/DII Trade React: https://www.nseindia.com/api/fiidiiTradeReact
  2. NSE Participant-wise derivatives: https://www.nseindia.com/api/historicaloptionchain?...
  3. Fallback: yfinance ETF proxy (NIFTYBEES, GOLDBEES flows)

Usage:
  from fii_dii_tracker import FIIDIITracker
  tracker = FIIDIITracker()
  flow = tracker.get_today_flow()
  bias = tracker.get_flow_bias()  # "BULLISH", "BEARISH", "NEUTRAL"
  score = tracker.get_signal_adjustment()  # -10 to +10
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import pandas as pd
import numpy as np

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)

# NSE requires browser-like headers + a valid session cookie from the homepage.
# Without the cookie NSE returns 403. The session must visit / first, then /api/*.
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "Origin":          "https://www.nseindia.com",
    "DNT":             "1",
    "Connection":      "keep-alive",
    "Sec-Fetch-Site":  "same-origin",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Dest":  "empty",
    "Cache-Control":   "no-cache",
    "Pragma":          "no-cache",
}

NSE_FII_DII_URL       = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_FII_DERIV_URL     = "https://www.nseindia.com/api/historicaloptionchain"
NSE_PARTICIPANT_URL   = "https://www.nseindia.com/api/market-participants-turnover"

DB_PATH = Path("logs/fii_dii_flow.db")
CACHE_TTL_MINUTES = 30  # NSE updates provisional data ~3:30 PM IST


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class FIIDIIFlow:
    """Daily FII/DII flow data."""
    date:         str
    fii_buy:      float = 0.0      # ₹ crore
    fii_sell:     float = 0.0
    fii_net:      float = 0.0      # + = net buyer, - = net seller
    dii_buy:      float = 0.0
    dii_sell:     float = 0.0
    dii_net:      float = 0.0
    fii_fut_net:  float = 0.0     # FII index futures net (more predictive)
    fii_opt_net:  float = 0.0     # FII options net
    data_source:  str  = "NSE"
    fetched_at:   str  = ""

    def __post_init__(self):
        if not self.fetched_at:
            self.fetched_at = format_ist_timestamp()
        if self.fii_net == 0.0 and (self.fii_buy or self.fii_sell):
            self.fii_net = self.fii_buy - self.fii_sell
        if self.dii_net == 0.0 and (self.dii_buy or self.dii_sell):
            self.dii_net = self.dii_buy - self.dii_sell

    @property
    def combined_net(self) -> float:
        """Combined institutional flow. Positive = institutions net buying."""
        return self.fii_net + self.dii_net

    @property
    def is_bullish(self) -> bool:
        return self.fii_net > 500  # ₹500cr+ FII buying = bullish

    @property
    def is_bearish(self) -> bool:
        return self.fii_net < -500  # ₹500cr+ FII selling = bearish

    def summary(self) -> str:
        fii_emoji = "🟢" if self.fii_net >= 0 else "🔴"
        dii_emoji = "🟢" if self.dii_net >= 0 else "🔴"
        return (
            f"FII/DII Flow ({self.date}): "
            f"{fii_emoji} FII: ₹{self.fii_net:+,.0f}Cr | "
            f"{dii_emoji} DII: ₹{self.dii_net:+,.0f}Cr | "
            f"Combined: ₹{self.combined_net:+,.0f}Cr"
        )


@dataclass
class FlowBias:
    """Multi-day flow momentum analysis result."""
    bias:          str           # "BULLISH", "BEARISH", "NEUTRAL"
    score:         float         # -100 to +100
    today_flow:    Optional[FIIDIIFlow]
    rolling_5d_net: float        # 5-day sum of FII net
    rolling_trend: str           # "ACCELERATING", "DECELERATING", "STEADY", "REVERSING"
    signals:       List[str]     = field(default_factory=list)
    timestamp:     str           = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_ist_timestamp()

    def as_signal_adjustment(self) -> float:
        """
        Returns adjustment for signal_generator AI score: -10 to +10.
        Strong FII buying → +10 added to every LONG signal score.
        Strong FII selling → -10 deducted from every LONG score.
        """
        return max(-10.0, min(10.0, self.score / 10.0))


# ============================================================
# TRACKER
# ============================================================

class FIIDIITracker:
    """
    Tracks FII/DII institutional flows from NSE India.

    The #1 edge in NSE intraday trading is knowing who's buying and selling.
    FII sell-off days → avoid LONG trades entirely.
    FII buy days → lean toward LONG, give MORE margin to signals.
    DII buying on dips → stronger support (don't aggressively short).
    """

    def __init__(self):
        self._session    = requests.Session()
        self._session.headers.update(NSE_HEADERS)
        self._session_ok = False
        self._cache:     Dict[str, FIIDIIFlow] = {}
        self._last_fetch: Optional[datetime]   = None
        self._db_path    = DB_PATH
        self._init_db()
        self._init_session()

    # ──────────────────────────────────────────────────────
    # SETUP
    # ──────────────────────────────────────────────────────

    def _init_db(self):
        """Create SQLite DB for historical flow storage."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fii_dii_flow (
                    date TEXT PRIMARY KEY,
                    fii_buy REAL, fii_sell REAL, fii_net REAL,
                    dii_buy REAL, dii_sell REAL, dii_net REAL,
                    fii_fut_net REAL, fii_opt_net REAL,
                    data_source TEXT, fetched_at TEXT
                )
            """)
            conn.commit()

    def _init_session(self):
        """
        Establish NSE session cookie by visiting homepage + market-data page.
        NSE requires a valid nsit/nseappid cookie; without it all /api/* return 403.
        """
        try:
            self._session = requests.Session()
            self._session.headers.update(NSE_HEADERS)
            # Step 1: visit homepage to get initial cookies
            r1 = self._session.get(
                "https://www.nseindia.com/",
                timeout=15, allow_redirects=True,
            )
            if r1.status_code != 200:
                logger.warning(f"[{format_ist_timestamp()}] NSE homepage {r1.status_code}")
                return
            time.sleep(1)  # brief pause — mimic browser behaviour
            # Step 2: visit a market-data page to get additional cookies (nsit, nseappid)
            self._session.get(
                "https://www.nseindia.com/market-data/live-equity-market",
                timeout=15, allow_redirects=True,
            )
            time.sleep(0.5)
            self._session_ok = True
            logger.info(
                f"[{format_ist_timestamp()}] FII/DII tracker NSE session ready "
                f"(cookies: {list(self._session.cookies.keys())})"
            )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] NSE session failed: {e}")

    # ──────────────────────────────────────────────────────
    # FETCH
    # ──────────────────────────────────────────────────────

    def _should_fetch(self) -> bool:
        """Only re-fetch if cache is stale (30 min TTL)."""
        if not self._last_fetch:
            return True
        elapsed = (datetime.now() - self._last_fetch).total_seconds() / 60
        return elapsed >= CACHE_TTL_MINUTES

    def get_today_flow(self, force_refresh: bool = False) -> Optional[FIIDIIFlow]:
        """
        Fetch today's FII/DII provisional data from NSE.
        NSE publishes provisional data during market hours (~11 AM, 3:30 PM IST).
        """
        today_str = str(get_current_ist_date())

        # Return cache if fresh
        if not force_refresh and today_str in self._cache and not self._should_fetch():
            return self._cache[today_str]

        # Try NSE API first
        flow = self._fetch_from_nse()
        if flow:
            self._cache[today_str] = flow
            self._last_fetch = datetime.now()
            self._save_to_db(flow)
            logger.info(f"[{format_ist_timestamp()}] {flow.summary()}")
            return flow

        # Fallback: last stored value
        stored = self._load_from_db(today_str)
        if stored:
            logger.info(f"[{format_ist_timestamp()}] FII/DII: using stored data for {today_str}")
            return stored

        logger.warning(f"[{format_ist_timestamp()}] FII/DII: no data available for {today_str}")
        return None

    def _fetch_from_nse(self) -> Optional[FIIDIIFlow]:
        """
        Fetch provisional FII/DII data from NSE API.
        On 403: reinit session and retry with exponential backoff (max 2 retries).
        NSE 403s are common — the session cookie expires every ~10 minutes.
        """
        if not self._session_ok:
            self._init_session()

        for attempt, wait in enumerate([0, 3, 8]):
            try:
                if wait:
                    time.sleep(wait)
                    self._init_session()  # fresh cookies before each retry

                resp = self._session.get(NSE_FII_DII_URL, timeout=20)

                if resp.status_code == 403:
                    logger.warning(
                        f"[{format_ist_timestamp()}] NSE FII 403 — "
                        f"session expired (attempt {attempt + 1}/3)"
                    )
                    self._session_ok = False
                    if attempt < 2:
                        continue
                    # All retries exhausted — fall through to DB fallback
                    return None

                resp.raise_for_status()
                data = resp.json()
                return self._parse_nse_fii_dii(data)

            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] NSE FII/DII fetch error (attempt {attempt + 1}): {e}")
                if attempt >= 2:
                    return None

        return None

    def _parse_nse_fii_dii(self, data) -> Optional[FIIDIIFlow]:
        """Parse NSE FII/DII API response."""
        try:
            # NSE returns a list of participant data
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                entries = data.get("data", data.get("entries", [data]))
            else:
                return None

            fii_buy = fii_sell = dii_buy = dii_sell = 0.0
            today_str = str(get_current_ist_date())

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # Look for FII/FPI entries
                category = str(entry.get("category", entry.get("clientType", ""))).upper()
                buy_val  = float(entry.get("buyValue",  entry.get("grossPurchase", 0)) or 0)
                sell_val = float(entry.get("sellValue", entry.get("grossSale", 0))    or 0)

                if any(k in category for k in ["FII", "FPI", "FOREIGN"]):
                    fii_buy  += buy_val
                    fii_sell += sell_val
                elif any(k in category for k in ["DII", "DOMESTIC", "MF", "MUTUAL"]):
                    dii_buy  += buy_val
                    dii_sell += sell_val

            if fii_buy == 0 and fii_sell == 0:
                logger.warning(f"[{format_ist_timestamp()}] FII/DII: could not parse NSE response")
                return None

            return FIIDIIFlow(
                date=today_str,
                fii_buy=round(fii_buy / 1e7, 2),    # Convert to ₹ crore
                fii_sell=round(fii_sell / 1e7, 2),
                dii_buy=round(dii_buy / 1e7, 2),
                dii_sell=round(dii_sell / 1e7, 2),
                data_source="NSE",
            )

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] FII/DII parse error: {e}")
            return None

    # ──────────────────────────────────────────────────────
    # HISTORICAL / DB
    # ──────────────────────────────────────────────────────

    def _save_to_db(self, flow: FIIDIIFlow):
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO fii_dii_flow VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    flow.date, flow.fii_buy, flow.fii_sell, flow.fii_net,
                    flow.dii_buy, flow.dii_sell, flow.dii_net,
                    flow.fii_fut_net, flow.fii_opt_net,
                    flow.data_source, flow.fetched_at,
                ))
        except Exception as e:
            logger.error(f"FII/DII DB save error: {e}")

    def _load_from_db(self, date_str: str) -> Optional[FIIDIIFlow]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM fii_dii_flow WHERE date=?", (date_str,)
                ).fetchone()
            if row:
                return FIIDIIFlow(
                    date=row[0], fii_buy=row[1], fii_sell=row[2], fii_net=row[3],
                    dii_buy=row[4], dii_sell=row[5], dii_net=row[6],
                    fii_fut_net=row[7], fii_opt_net=row[8],
                    data_source=row[9], fetched_at=row[10],
                )
        except Exception as e:
            logger.error(f"FII/DII DB load error: {e}")
        return None

    def get_historical_flows(self, days: int = 10) -> List[FIIDIIFlow]:
        """Load last N days of FII/DII flow from DB."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM fii_dii_flow ORDER BY date DESC LIMIT ?", (days,)
                ).fetchall()
            flows = []
            for row in rows:
                flows.append(FIIDIIFlow(
                    date=row[0], fii_buy=row[1], fii_sell=row[2], fii_net=row[3],
                    dii_buy=row[4], dii_sell=row[5], dii_net=row[6],
                    fii_fut_net=row[7], fii_opt_net=row[8],
                    data_source=row[9], fetched_at=row[10],
                ))
            return flows
        except Exception as e:
            logger.error(f"FII/DII historical load error: {e}")
            return []

    # ──────────────────────────────────────────────────────
    # BIAS ENGINE
    # ──────────────────────────────────────────────────────

    def get_flow_bias(self) -> FlowBias:
        """
        Compute multi-day FII/DII flow momentum bias.

        18yr rule: Don't just look at today's flow.
        3 consecutive days of FII selling = trend. 1 day = noise.
        5-day rolling FII net > ₹5000cr = strong bull wave.
        5-day rolling FII net < -₹5000cr = distribution phase.
        """
        today_flow = self.get_today_flow()
        historical = self.get_historical_flows(days=10)

        # Rolling 5-day FII net
        recent_5  = historical[:5]
        rolling_5d = sum(f.fii_net for f in recent_5)

        # Trend direction
        if len(recent_5) >= 3:
            nets    = [f.fii_net for f in recent_5[:3]]
            trend_d = sum(1 if n > 0 else -1 for n in nets)
            if all(n > 0 for n in nets):
                trend = "ACCELERATING" if nets[0] > nets[1] > nets[2] else "STEADY"
            elif all(n < 0 for n in nets):
                trend = "ACCELERATING" if nets[0] < nets[1] < nets[2] else "STEADY"
            else:
                trend = "REVERSING" if (nets[0] > 0) != (nets[1] > 0) else "DECELERATING"
        else:
            trend = "UNKNOWN"

        score, signals = self._score_flow(today_flow, rolling_5d, trend)

        if score >= 20:
            bias = "BULLISH"
        elif score <= -20:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return FlowBias(
            bias=bias,
            score=score,
            today_flow=today_flow,
            rolling_5d_net=rolling_5d,
            rolling_trend=trend,
            signals=signals,
        )

    def _score_flow(
        self, today: Optional[FIIDIIFlow], rolling_5d: float, trend: str
    ) -> Tuple[float, List[str]]:
        score   = 0.0
        signals = []

        # Today's flow
        if today:
            # FII dominates (2x DII weight)
            if today.fii_net > 2000:
                score += 30
                signals.append(f"FII strong buy today: ₹{today.fii_net:+,.0f}Cr — VERY BULLISH")
            elif today.fii_net > 500:
                score += 15
                signals.append(f"FII net buy today: ₹{today.fii_net:+,.0f}Cr — BULLISH")
            elif today.fii_net > -500:
                score += 5
                signals.append(f"FII neutral today: ₹{today.fii_net:+,.0f}Cr")
            elif today.fii_net > -2000:
                score -= 15
                signals.append(f"FII net sell today: ₹{today.fii_net:+,.0f}Cr — BEARISH")
            else:
                score -= 30
                signals.append(f"FII heavy sell today: ₹{today.fii_net:+,.0f}Cr — VERY BEARISH")

            # DII (half weight — often contrarian buyers at dips)
            if today.dii_net > 1000:
                score += 10
                signals.append(f"DII heavy buy: ₹{today.dii_net:+,.0f}Cr — strong support floor")
            elif today.dii_net > 0:
                score += 5
                signals.append(f"DII net buy: ₹{today.dii_net:+,.0f}Cr")
            elif today.dii_net < -1000:
                score -= 10
                signals.append(f"DII net sell: ₹{today.dii_net:+,.0f}Cr — no support")

        # 5-day rolling momentum
        if rolling_5d > 10000:
            score += 20
            signals.append(f"5-day FII rolling: ₹{rolling_5d:+,.0f}Cr — strong BULL wave")
        elif rolling_5d > 3000:
            score += 10
            signals.append(f"5-day FII rolling: ₹{rolling_5d:+,.0f}Cr — moderate bull")
        elif rolling_5d < -10000:
            score -= 20
            signals.append(f"5-day FII rolling: ₹{rolling_5d:+,.0f}Cr — distribution phase")
        elif rolling_5d < -3000:
            score -= 10
            signals.append(f"5-day FII rolling: ₹{rolling_5d:+,.0f}Cr — moderate sell-off")

        # Trend momentum
        if trend == "ACCELERATING" and score > 0:
            score += 10
            signals.append("FII buying ACCELERATING — momentum building")
        elif trend == "ACCELERATING" and score < 0:
            score -= 10
            signals.append("FII selling ACCELERATING — distribution intensifying")
        elif trend == "REVERSING":
            signals.append(f"FII trend REVERSING — watch for direction change")

        return score, signals

    # ──────────────────────────────────────────────────────
    # SIGNAL ADJUSTMENT (for signal_generator)
    # ──────────────────────────────────────────────────────

    def get_signal_adjustment(self) -> float:
        """
        Returns score adjustment for signal_generator: -10 to +10.
        This is added to every AI composite score.
        Strong FII buy day → boost LONG signals by up to +10 pts.
        Strong FII sell day → reduce LONG signals by up to 10 pts.
        """
        bias = self.get_flow_bias()
        return bias.as_signal_adjustment()

    def get_position_size_multiplier(self) -> float:
        """
        Returns position size multiplier: 0.5 to 1.5.
        Strong FII tailwind → trade bigger.
        FII headwind → trade smaller or avoid.
        """
        bias = self.get_flow_bias()
        score = bias.score
        if score >= 40:
            return 1.5
        if score >= 20:
            return 1.2
        if score >= 0:
            return 1.0
        if score >= -20:
            return 0.75
        return 0.5

    # ──────────────────────────────────────────────────────
    # TELEGRAM FORMAT
    # ──────────────────────────────────────────────────────

    def format_telegram(self) -> str:
        """Format FII/DII summary for Telegram morning brief."""
        bias = self.get_flow_bias()
        today = bias.today_flow

        emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias.bias, "🟡")

        if today:
            fii_str = f"₹{today.fii_net:+,.0f}Cr"
            dii_str = f"₹{today.dii_net:+,.0f}Cr"
        else:
            fii_str = dii_str = "N/A"

        signals_str = "\n".join(f"  • {s}" for s in bias.signals[:4])

        return (
            f"💰 *FII/DII Flow*\n"
            f"FII Today: `{fii_str}` | DII: `{dii_str}`\n"
            f"5-Day FII: `₹{bias.rolling_5d_net:+,.0f}Cr` | Trend: `{bias.rolling_trend}`\n"
            f"{emoji} Bias: *{bias.bias}* | Score: `{bias.score:+.0f}`\n"
            f"*Signals:*\n{signals_str}"
        )


# ──────────────────────────────────────────────────────────────
# SINGLETON
# ──────────────────────────────────────────────────────────────

_tracker: Optional[FIIDIITracker] = None

def get_fii_dii_tracker() -> FIIDIITracker:
    global _tracker
    if _tracker is None:
        _tracker = FIIDIITracker()
    return _tracker


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tracker = FIIDIITracker()
    bias = tracker.get_flow_bias()
    print(f"\nFII/DII Bias: {bias.bias} (score: {bias.score:+.0f})")
    print(f"Rolling 5-day FII: ₹{bias.rolling_5d_net:+,.0f}Cr | Trend: {bias.rolling_trend}")
    if bias.today_flow:
        print(bias.today_flow.summary())
    print("\nSignals:")
    for s in bias.signals:
        print(f"  → {s}")
    print(f"\nSignal adjustment: {bias.as_signal_adjustment():+.1f} pts")
    print(f"Position size multiplier: {tracker.get_position_size_multiplier():.2f}x")


# ============================================================
# MODULE: trade_journal.py
# ============================================================
"""
trade_journal.py — NSE Momentum Groww AI Bot
SQLite Trade Journal + Pattern Performance Tracker

Logs every trade with full SEBI-compliant fields.
Powers the self-learning module by tracking which patterns/times/stocks perform best.

18yr rule: "Keep a journal. Review it EVERY day. 
The market is a teacher — it only teaches those who pay attention."
"""

import logging
import sqlite3
import json
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)

DB_PATH = "logs/trades/trade_journal.db"


@dataclass
class TradeRecord:
    """One complete trade from entry to exit."""
    # Identifiers
    trade_id:       str  = ""
    order_id:       str  = ""
    symbol:         str  = ""
    date_ist:       str  = ""

    # Entry
    direction:      str   = ""      # "LONG" or "SHORT"
    entry_price:    float = 0.0
    entry_qty:      int   = 0
    entry_time_ist: str   = ""
    entry_pattern:  str   = ""      # e.g. "VWAP_BOUNCE+ORB_BREAKOUT"
    signal_score:   float = 0.0
    regime:         str   = ""
    session:        str   = ""
    mtf_aligned:    bool  = False
    volume_ratio:   float = 1.0
    rsi_at_entry:   float = 50.0
    atr_at_entry:   float = 0.0

    # Risk levels
    stop_loss:      float = 0.0
    target_1:       float = 0.0
    target_2:       float = 0.0
    risk_pct:       float = 0.0
    rr_ratio:       float = 0.0

    # Exit
    exit_price:     float = 0.0
    exit_qty:       int   = 0
    exit_time_ist:  str   = ""
    exit_reason:    str   = ""      # "HIT_T1","HIT_T2","HIT_SL","FORCE_EXIT","KILL"

    # Result
    pnl:            float = 0.0
    pnl_pct:        float = 0.0
    brokerage:      float = 0.0
    net_pnl:        float = 0.0
    outcome:        str   = ""      # "WIN" or "LOSS"
    hold_minutes:   float = 0.0

    # Metadata
    notes:          str   = ""


class TradeJournal:
    """
    SQLite-based trade journal with pattern and timing analytics.
    Self-learning module reads from here to adapt strategy parameters.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id       TEXT PRIMARY KEY,
                    order_id       TEXT,
                    symbol         TEXT,
                    date_ist       TEXT,
                    direction      TEXT,
                    entry_price    REAL,
                    entry_qty      INTEGER,
                    entry_time_ist TEXT,
                    entry_pattern  TEXT,
                    signal_score   REAL,
                    regime         TEXT,
                    session        TEXT,
                    mtf_aligned    INTEGER,
                    volume_ratio   REAL,
                    rsi_at_entry   REAL,
                    atr_at_entry   REAL,
                    stop_loss      REAL,
                    target_1       REAL,
                    target_2       REAL,
                    risk_pct       REAL,
                    rr_ratio       REAL,
                    exit_price     REAL,
                    exit_qty       INTEGER,
                    exit_time_ist  TEXT,
                    exit_reason    TEXT,
                    pnl            REAL,
                    pnl_pct        REAL,
                    brokerage      REAL,
                    net_pnl        REAL,
                    outcome        TEXT,
                    hold_minutes   REAL,
                    notes          TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    date_ist          TEXT PRIMARY KEY,
                    total_trades      INTEGER,
                    wins              INTEGER,
                    losses            INTEGER,
                    gross_pnl         REAL,
                    net_pnl           REAL,
                    win_rate          REAL,
                    best_trade_pnl    REAL,
                    worst_trade_pnl   REAL,
                    max_drawdown      REAL,
                    capital_deployed  REAL,
                    return_pct        REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pattern_stats (
                    pattern_name    TEXT PRIMARY KEY,
                    total_trades    INTEGER DEFAULT 0,
                    wins            INTEGER DEFAULT 0,
                    losses          INTEGER DEFAULT 0,
                    total_pnl       REAL DEFAULT 0,
                    avg_pnl         REAL DEFAULT 0,
                    win_rate        REAL DEFAULT 0,
                    avg_hold_min    REAL DEFAULT 0,
                    last_updated    TEXT
                )
            """)
            conn.commit()
        logger.info(f"[{format_ist_timestamp()}] Trade journal DB ready: {self.db_path}")

    # -------------------------------------------------------
    # WRITE
    # -------------------------------------------------------

    def log_trade(self, trade: TradeRecord):
        """Insert or update a trade record."""
        if not trade.trade_id:
            trade.trade_id = f"{trade.symbol}_{trade.entry_time_ist.replace(' ','_').replace(':','')}"
        if not trade.date_ist:
            trade.date_ist = str(get_current_ist_date())

        d = asdict(trade)
        d["mtf_aligned"] = int(trade.mtf_aligned)

        cols = ", ".join(d.keys())
        placeholders = ", ".join("?" * len(d))
        updates = ", ".join(f"{k}=excluded.{k}" for k in d if k != "trade_id")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"INSERT INTO trades ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(trade_id) DO UPDATE SET {updates}",
                list(d.values())
            )
            conn.commit()

        logger.info(
            f"[{format_ist_timestamp()}] Journal: {trade.symbol} {trade.direction} "
            f"{trade.outcome} ₹{trade.net_pnl:+.0f}"
        )
        self._update_pattern_stats(trade)

    def _update_pattern_stats(self, trade: TradeRecord):
        """Update per-pattern win rate statistics."""
        if not trade.entry_pattern or not trade.outcome:
            return
        patterns = trade.entry_pattern.split("+")
        for pattern in patterns:
            pattern = pattern.strip()
            if not pattern:
                continue
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO pattern_stats (pattern_name, total_trades, wins, losses, total_pnl, last_updated)
                    VALUES (?, 1, ?, ?, ?, ?)
                    ON CONFLICT(pattern_name) DO UPDATE SET
                        total_trades = total_trades + 1,
                        wins   = wins   + excluded.wins,
                        losses = losses + excluded.losses,
                        total_pnl = total_pnl + excluded.total_pnl,
                        last_updated = excluded.last_updated
                """, (
                    pattern,
                    1 if trade.outcome == "WIN" else 0,
                    1 if trade.outcome == "LOSS" else 0,
                    trade.net_pnl,
                    format_ist_timestamp(),
                ))
                conn.execute("""
                    UPDATE pattern_stats SET
                        avg_pnl  = total_pnl / total_trades,
                        win_rate = CAST(wins AS REAL) / total_trades * 100
                    WHERE pattern_name = ?
                """, (pattern,))
                conn.commit()

    def save_daily_summary(self, summary: Dict):
        """Persist end-of-day summary."""
        date_str = summary.get("date_ist", str(get_current_ist_date()))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO daily_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(date_ist) DO UPDATE SET
                    total_trades=excluded.total_trades,
                    wins=excluded.wins,
                    losses=excluded.losses,
                    gross_pnl=excluded.gross_pnl,
                    net_pnl=excluded.net_pnl,
                    win_rate=excluded.win_rate,
                    best_trade_pnl=excluded.best_trade_pnl,
                    worst_trade_pnl=excluded.worst_trade_pnl,
                    max_drawdown=excluded.max_drawdown,
                    capital_deployed=excluded.capital_deployed,
                    return_pct=excluded.return_pct
            """, (
                date_str,
                summary.get("total_trades", 0),
                summary.get("wins", 0),
                summary.get("losses", 0),
                summary.get("gross_pnl", 0),
                summary.get("net_pnl", 0),
                summary.get("win_rate", 0),
                summary.get("best_trade_pnl", 0),
                summary.get("worst_trade_pnl", 0),
                summary.get("max_drawdown", 0),
                summary.get("capital_deployed", 0),
                summary.get("return_pct", 0),
            ))
            conn.commit()

    # -------------------------------------------------------
    # READ / ANALYTICS
    # -------------------------------------------------------

    def get_today_trades(self) -> List[TradeRecord]:
        today = str(get_current_ist_date())
        return self._fetch_trades("WHERE date_ist = ?", (today,))

    def get_trades_last_n_days(self, n: int = 30) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM trades ORDER BY entry_time_ist DESC LIMIT ?",
                conn, params=(n * 20,)
            )
        return df

    def get_pattern_stats(self) -> pd.DataFrame:
        """Return pattern performance table sorted by win rate."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM pattern_stats WHERE total_trades >= 3 ORDER BY win_rate DESC",
                conn
            )
        return df

    def get_best_patterns(self, top_n: int = 5, min_trades: int = 5) -> List[Dict]:
        """Return top N patterns by win rate (min_trades filter)."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT pattern_name, total_trades, win_rate, avg_pnl
                FROM pattern_stats
                WHERE total_trades >= ?
                ORDER BY win_rate DESC, avg_pnl DESC
                LIMIT ?
            """, (min_trades, top_n)).fetchall()
        return [{"pattern": r[0], "trades": r[1], "win_rate": r[2], "avg_pnl": r[3]} for r in rows]

    def get_worst_patterns(self, bottom_n: int = 5, min_trades: int = 5) -> List[Dict]:
        """Return bottom N patterns — candidates for disabling."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT pattern_name, total_trades, win_rate, avg_pnl
                FROM pattern_stats
                WHERE total_trades >= ?
                ORDER BY win_rate ASC
                LIMIT ?
            """, (min_trades, bottom_n)).fetchall()
        return [{"pattern": r[0], "trades": r[1], "win_rate": r[2], "avg_pnl": r[3]} for r in rows]

    def get_session_stats(self) -> pd.DataFrame:
        """P&L breakdown by session (OPENING_DRIVE / MORNING / MIDDAY / AFTERNOON)."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql("""
                SELECT session,
                       COUNT(*) as trades,
                       SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                       ROUND(AVG(pnl_pct),2) as avg_pnl_pct,
                       ROUND(SUM(net_pnl),2) as total_pnl
                FROM trades GROUP BY session ORDER BY total_pnl DESC
            """, conn)
        return df

    def get_daily_equity_curve(self) -> pd.DataFrame:
        """Cumulative P&L by day for equity curve chart."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql("""
                SELECT date_ist, SUM(net_pnl) as daily_pnl
                FROM trades GROUP BY date_ist ORDER BY date_ist
            """, conn)
        if not df.empty:
            df["cumulative_pnl"] = df["daily_pnl"].cumsum()
        return df

    def get_today_summary(self) -> Dict:
        trades = self.get_today_trades()
        if not trades:
            return {"total_trades": 0, "wins": 0, "losses": 0, "net_pnl": 0, "win_rate": 0}
        wins   = sum(1 for t in trades if t.outcome == "WIN")
        losses = sum(1 for t in trades if t.outcome == "LOSS")
        net    = sum(t.net_pnl for t in trades)
        return {
            "date_ist":      str(get_current_ist_date()),
            "total_trades":  len(trades),
            "wins":          wins,
            "losses":        losses,
            "net_pnl":       round(net, 2),
            "win_rate":      round(wins / len(trades) * 100, 1) if trades else 0,
            "best_trade":    max((t.net_pnl for t in trades), default=0),
            "worst_trade":   min((t.net_pnl for t in trades), default=0),
        }

    def _fetch_trades(self, where_clause: str = "", params: tuple = ()) -> List[TradeRecord]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"SELECT * FROM trades {where_clause} ORDER BY entry_time_ist DESC", params).fetchall()
        records = []
        for row in rows:
            d = dict(row)
            d["mtf_aligned"] = bool(d.get("mtf_aligned", 0))
            try:
                records.append(TradeRecord(**d))
            except Exception:
                pass
        return records


# Singleton
_journal: Optional[TradeJournal] = None

def get_journal() -> TradeJournal:
    global _journal
    if _journal is None:
        _journal = TradeJournal()
    return _journal


# ============================================================
# MODULE: dashboard.py
# ============================================================
"""
dashboard.py — NSE Momentum Groww AI Bot
Real-Time Performance Dashboard + EOD Reports

Features:
- Live terminal dashboard (refreshes every 30s during market hours)
- Equity curve chart (matplotlib)
- Per-trade P&L waterfall
- EOD summary report → sent via Telegram
- Session breakdown table
- Pattern performance heatmap

All timestamps in IST. Server runs in UK (UTC).
"""

import logging
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from utils import (
    format_ist_timestamp, format_currency,
    get_current_ist_time, get_current_ist_date
)

logger = logging.getLogger(__name__)
CHART_DIR = Path("charts")
CHART_DIR.mkdir(exist_ok=True)


class PerformanceDashboard:
    """Live performance tracker + EOD report generator."""

    def __init__(self, journal=None, alerter=None):
        self.journal = journal
        self.alerter = alerter

    # -------------------------------------------------------
    # TERMINAL DASHBOARD (Rich text)
    # -------------------------------------------------------

    def print_live_dashboard(self, risk_mgr=None):
        """Print compact live dashboard to terminal."""
        now = format_ist_timestamp()
        summary = self.journal.get_today_summary() if self.journal else {}

        pnl      = summary.get("net_pnl", 0)
        trades   = summary.get("total_trades", 0)
        wins     = summary.get("wins", 0)
        losses   = summary.get("losses", 0)
        wr       = summary.get("win_rate", 0)
        positions = 0

        if risk_mgr:
            positions = len(getattr(getattr(risk_mgr, "state", None), "positions", {}))
            pnl       = getattr(getattr(risk_mgr, "state", None), "daily_pnl", 0)

        pnl_sign = "+" if pnl >= 0 else ""
        status   = "🟢 RUNNING" if risk_mgr and getattr(risk_mgr, "running", True) else "🔴 STOPPED"

        print(f"""
╔══════════════════════════════════════════════╗
║   NSE MOMENTUM BOT  [{now}]
╠══════════════════════════════════════════════╣
║  Status   : {status}
║  P&L      : {pnl_sign}{format_currency(pnl)}
║  Positions: {positions} open
║  Trades   : {trades} ({wins}W / {losses}L)
║  Win Rate : {wr:.1f}%
╚══════════════════════════════════════════════╝""")

    # -------------------------------------------------------
    # EQUITY CURVE CHART
    # -------------------------------------------------------

    def generate_equity_curve(self, save_path: Optional[str] = None) -> Optional[str]:
        """Generate cumulative equity curve chart. Returns file path."""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates

            if not self.journal:
                return None
            df = self.journal.get_daily_equity_curve()
            if df.empty:
                return None

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), facecolor="#0d1117")
            for ax in (ax1, ax2):
                ax.set_facecolor("#0d1117")
                ax.tick_params(colors="white")
                ax.spines[:].set_color("#30363d")

            dates = pd.to_datetime(df["date_ist"])
            cum_pnl = df["cumulative_pnl"]

            # Equity curve
            color = "#2ea043" if cum_pnl.iloc[-1] >= 0 else "#f85149"
            ax1.plot(dates, cum_pnl, color=color, linewidth=2)
            ax1.fill_between(dates, cum_pnl, alpha=0.15, color=color)
            ax1.axhline(0, color="#8b949e", linewidth=0.8, linestyle="--")
            ax1.set_title("Cumulative P&L Equity Curve", color="white", fontsize=13, pad=10)
            ax1.set_ylabel("₹ P&L", color="white")
            ax1.yaxis.set_major_formatter(lambda x, p: f"₹{x:,.0f}")

            # Daily bars
            daily = df["daily_pnl"]
            colors_bar = ["#2ea043" if v >= 0 else "#f85149" for v in daily]
            ax2.bar(dates, daily, color=colors_bar, width=0.6)
            ax2.axhline(0, color="#8b949e", linewidth=0.8, linestyle="--")
            ax2.set_title("Daily P&L", color="white", fontsize=11, pad=8)
            ax2.set_ylabel("₹ P&L", color="white")

            plt.tight_layout(pad=2)
            path = save_path or str(CHART_DIR / f"equity_{get_current_ist_date()}.png")
            plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            return path

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Equity curve error: {e}")
            return None

    # -------------------------------------------------------
    # EOD REPORT (sent via Telegram)
    # -------------------------------------------------------

    def generate_eod_report(self, capital: float = 50000, learner=None) -> Dict:
        """
        Full end-of-day performance report.
        Returns dict with stats + sends Telegram message with chart.
        """
        if not self.journal:
            return {}

        summary    = self.journal.get_today_summary()
        trades_df  = self.journal.get_trades_last_n_days(1)
        pat_df     = self.journal.get_pattern_stats()
        sess_df    = self.journal.get_session_stats()

        net_pnl    = summary.get("net_pnl", 0)
        ret_pct    = (net_pnl / capital * 100) if capital > 0 else 0
        trades     = summary.get("total_trades", 0)
        wr         = summary.get("win_rate", 0)

        # Sharpe (simplified daily)
        if not trades_df.empty and "net_pnl" in trades_df.columns:
            pnl_vals  = trades_df["net_pnl"].values
            sharpe    = (np.mean(pnl_vals) / (np.std(pnl_vals) + 1e-9)) * np.sqrt(252)
        else:
            sharpe = 0.0

        # Build report text
        sign = "+" if net_pnl >= 0 else ""
        emoji = "🟢" if net_pnl >= 0 else "🔴"

        report = {
            "date_ist":      str(get_current_ist_date()),
            "net_pnl":       net_pnl,
            "return_pct":    round(ret_pct, 2),
            "total_trades":  trades,
            "wins":          summary.get("wins", 0),
            "losses":        summary.get("losses", 0),
            "win_rate":      wr,
            "sharpe":        round(sharpe, 2),
            "best_trade":    summary.get("best_trade", 0),
            "worst_trade":   summary.get("worst_trade", 0),
        }

        msg = (
            f"{emoji} EOD Report — {get_current_ist_date()}\n"
            f"P&L:   {sign}{format_currency(net_pnl)} ({sign}{ret_pct:.2f}%)\n"
            f"Trades: {trades}  |  W/L: {summary.get('wins',0)}/{summary.get('losses',0)}\n"
            f"Win Rate: {wr:.1f}%  |  Sharpe: {sharpe:.2f}\n"
            f"Best: {format_currency(summary.get('best_trade',0))}"
            f"  Worst: {format_currency(summary.get('worst_trade',0))}\n"
        )

        if not pat_df.empty:
            top3 = pat_df.head(3)
            msg += "\nTop Patterns:\n"
            for _, row in top3.iterrows():
                msg += f"  • {row['pattern_name']}: {row['win_rate']:.0f}% WR ({row['total_trades']} trades)\n"

        # Learner insights
        if learner:
            msg += f"\n{learner.get_learning_summary()}\n"

        logger.info(f"[{format_ist_timestamp()}] EOD Report:\n{msg}")

        # Send via Telegram
        if self.alerter:
            try:
                import asyncio
                chart_path = self.generate_equity_curve()
                asyncio.run(self.alerter.send_eod_report(report, chart_path))
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] EOD Telegram send failed: {e}")

        # Save daily summary to journal
        if self.journal:
            self.journal.save_daily_summary({
                **report,
                "capital_deployed": capital,
                "gross_pnl": net_pnl,
            })

        return report

    # -------------------------------------------------------
    # PATTERN PERFORMANCE TABLE
    # -------------------------------------------------------

    def print_pattern_table(self):
        """Print pattern win rate table to console."""
        if not self.journal:
            return
        df = self.journal.get_pattern_stats()
        if df.empty:
            print("No pattern data yet.")
            return
        print("\n=== Pattern Performance ===")
        print(f"{'Pattern':<30} {'Trades':>6} {'WR%':>6} {'Avg P&L':>10}")
        print("-" * 56)
        for _, row in df.iterrows():
            print(
                f"{row['pattern_name']:<30} "
                f"{row['total_trades']:>6} "
                f"{row['win_rate']:>5.1f}% "
                f"₹{row['avg_pnl']:>9.0f}"
            )

    # -------------------------------------------------------
    # OPEN POSITIONS TABLE
    # -------------------------------------------------------

    def print_positions(self, risk_mgr):
        """Print live positions table."""
        if not risk_mgr:
            return
        positions = getattr(risk_mgr, "open_positions", [])
        if not positions:
            print("No open positions.")
            return
        print(f"\n=== Open Positions [{format_ist_timestamp()}] ===")
        print(f"{'Symbol':<12} {'Dir':>5} {'Qty':>5} {'Entry':>8} {'CMP':>8} {'P&L':>10} {'SL':>8}")
        print("-" * 65)
        for pos in positions:
            pnl_str = f"₹{pos.pnl:+.0f}"
            print(
                f"{pos.symbol:<12} {pos.direction:>5} {pos.quantity:>5} "
                f"₹{pos.entry_price:>7.1f} ₹{pos.current_price:>7.1f} "
                f"{pnl_str:>10} ₹{pos.active_sl:>7.1f}"
            )
        total_pnl = sum(p.pnl for p in positions)
        print(f"\nTotal Open P&L: ₹{total_pnl:+.0f}")


# ============================================================
# MODULE: trainer.py
# ============================================================
"""
trainer.py — NSE Momentum Groww AI Bot
Robust Backtesting Engine + Walk-Forward Optimization + Statistical Validation

⚠️ OFFLINE ONLY — Trainer never places live orders.
Run this BEFORE going live to validate and optimize strategy parameters.

18yr Rule: "Backtest is NOT a crystal ball. It's a confidence builder.
Walk-forward validation is the ONLY honest way to test a strategy."

Includes:
  BacktestEngine          — Event-driven 5m candle simulation with 50/30/20 partial exits
  WalkForwardOptimizer    — Optuna-powered walk-forward parameter search
  MonteCarloSimulator     — 1000-run trade shuffle stress test + ruin probability
  BenchmarkComparator     — Alpha/Beta/Info ratio vs Nifty50 buy-and-hold
  SensitivityAnalyzer     — Robustness score: how much does Sharpe degrade per ±10% param shift
  PortfolioStressTest     — Combined stress: crash scenario + fat tail simulation

Usage:
  python trainer.py --symbols RELIANCE,TCS --days 365
  python trainer.py --full-optimization
  python trainer.py --monte-carlo
  python trainer.py --benchmark
  python trainer.py --sensitivity
  python trainer.py --quick-test           # All analyses, 30d data

Targets:
  Win rate > 55% | Sharpe > 1.5 | Max drawdown < 8% | Net return > 5%/month
  Monte Carlo ruin prob < 5% | Benchmark alpha > 5%/yr | Sensitivity score > 0.7
"""

import argparse
import json
import logging
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class _NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy scalars and booleans."""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

from utils import (
    format_ist_timestamp, get_current_ist_time,
    filter_market_hours, setup_logging
)
import config

logger = logging.getLogger(__name__)
RESULTS_DIR = Path("trainer_results")
RESULTS_DIR.mkdir(exist_ok=True)

# -----------------------------------------------------------
# COST MODEL (realistic NSE intraday costs)
# -----------------------------------------------------------
SLIPPAGE_PCT   = 0.10   # 0.10% per trade (market impact)
BROKERAGE_PCT  = 0.03   # ~₹20 flat → ~0.03% on ₹70k trade
STT_PCT        = 0.025  # Securities Transaction Tax (sell side)
TOTAL_COST_PCT = SLIPPAGE_PCT + BROKERAGE_PCT + STT_PCT  # ~0.155%


# -----------------------------------------------------------
# BACKTEST ENGINE
# -----------------------------------------------------------

class BacktestEngine:
    """
    Event-driven vectorised backtester for 5-min NSE data.
    Simulates: entry, SL, T1 (40% exit), T2 (40% exit), T3 trailing runner.
    """

    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.capital         = initial_capital
        self.trades: List[Dict] = []

    def run(
        self,
        df:          pd.DataFrame,
        signals_df:  pd.DataFrame,
        atr_sl_mult: float = 1.5,
        atr_t1_mult: float = 2.5,
        atr_t2_mult: float = 4.0,
        max_risk_pct: float = 0.5,
    ) -> Dict:
        """
        Run backtest on signal list.

        df:         Raw OHLCV + indicators (5-min, IST index)
        signals_df: DataFrame with columns: timestamp, direction, entry, atr, score
        """
        self.capital = self.initial_capital
        self.trades  = []
        equity_curve = [self.initial_capital]
        peak_equity  = self.initial_capital

        for _, sig in signals_df.iterrows():
            # Find entry candle in df
            try:
                entry_idx = df.index.searchsorted(sig["timestamp"])
            except Exception:
                continue

            if entry_idx >= len(df) - 5:
                continue

            entry_price = float(sig["entry"])
            direction   = str(sig["direction"])
            atr         = float(sig.get("atr", entry_price * 0.005))
            score       = float(sig.get("score", 65))

            # Position sizing
            sl_dist    = atr * atr_sl_mult
            risk_amt   = self.capital * (max_risk_pct / 100)
            qty        = max(1, int(risk_amt / sl_dist))
            max_qty    = int(self.capital * 0.15 / entry_price)
            qty        = min(qty, max_qty)

            if qty <= 0:
                continue

            sl     = entry_price - sl_dist if direction == "LONG" else entry_price + sl_dist
            t1     = entry_price + atr * atr_t1_mult if direction == "LONG" else entry_price - atr * atr_t1_mult
            t2     = entry_price + atr * atr_t2_mult if direction == "LONG" else entry_price - atr * atr_t2_mult

            # Simulate trade through subsequent candles
            result = self._simulate_trade(
                df, entry_idx, direction, entry_price, sl, t1, t2, qty
            )

            if result:
                self.trades.append(result)
                self.capital += result["net_pnl"]
                equity_curve.append(self.capital)
                peak_equity = max(peak_equity, self.capital)

        return self._compute_stats(equity_curve, peak_equity)

    def _simulate_trade(
        self, df, entry_idx, direction, entry, sl, t1, t2, qty
    ) -> Optional[Dict]:
        """Walk forward candle-by-candle to find exit."""
        qty_remaining = qty
        t1_done       = False
        realized_pnl  = 0.0
        exit_price    = entry
        exit_reason   = "TIMEOUT"
        entry_cost    = entry * qty * TOTAL_COST_PCT / 100

        for i in range(entry_idx + 1, min(entry_idx + 80, len(df))):
            c = df.iloc[i]
            h, l = float(c["high"]), float(c["low"])

            if direction == "LONG":
                # Check SL
                if l <= sl:
                    exit_price  = sl
                    exit_reason = "HIT_SL"
                    realized_pnl += (exit_price - entry) * qty_remaining
                    qty_remaining = 0
                    break
                # Check T1
                if not t1_done and h >= t1:
                    t1_qty = max(1, int(qty * 0.4))
                    realized_pnl += (t1 - entry) * t1_qty
                    qty_remaining -= t1_qty
                    sl = entry   # Move SL to breakeven
                    t1_done = True
                    exit_reason = "HIT_T1"
                # Check T2
                if t1_done and h >= t2:
                    realized_pnl += (t2 - entry) * qty_remaining
                    qty_remaining = 0
                    exit_reason   = "HIT_T2"
                    break
            else:  # SHORT
                if h >= sl:
                    exit_price  = sl
                    exit_reason = "HIT_SL"
                    realized_pnl += (entry - exit_price) * qty_remaining
                    qty_remaining = 0
                    break
                if not t1_done and l <= t1:
                    t1_qty = max(1, int(qty * 0.4))
                    realized_pnl += (entry - t1) * t1_qty
                    qty_remaining -= t1_qty
                    sl = entry
                    t1_done      = True
                    exit_reason  = "HIT_T1"
                if t1_done and l <= t2:
                    realized_pnl += (entry - t2) * qty_remaining
                    qty_remaining = 0
                    exit_reason   = "HIT_T2"
                    break

        # Time exit (remaining qty closed at last candle)
        if qty_remaining > 0:
            last_close = float(df.iloc[min(entry_idx + 79, len(df)-1)]["close"])
            if direction == "LONG":
                realized_pnl += (last_close - entry) * qty_remaining
            else:
                realized_pnl += (entry - last_close) * qty_remaining
            if exit_reason not in ("HIT_T1", "HIT_T2"):
                exit_reason = "TIMEOUT"

        gross_pnl = realized_pnl
        cost      = entry * qty * TOTAL_COST_PCT / 100
        net_pnl   = gross_pnl - cost

        return {
            "direction":   direction,
            "entry":       entry,
            "qty":         qty,
            "exit_reason": exit_reason,
            "gross_pnl":   round(gross_pnl, 2),
            "cost":        round(cost, 2),
            "net_pnl":     round(net_pnl, 2),
            "outcome":     "WIN" if net_pnl > 0 else "LOSS",
        }

    def _compute_stats(self, equity: List[float], peak: float) -> Dict:
        if not self.trades:
            return {"error": "No trades"}

        net_pnls = [t["net_pnl"] for t in self.trades]
        wins     = [p for p in net_pnls if p > 0]
        losses   = [p for p in net_pnls if p <= 0]
        total_pnl = sum(net_pnls)
        win_rate  = len(wins) / len(net_pnls) * 100

        pnl_arr = np.array(net_pnls)

        # ── Sharpe (annualised using trade-level returns) ──
        # Guard: need ≥2 trades with meaningful variance; 1e-9 epsilon causes
        # billions when std≈0 (single trade or all trades identical P&L).
        _std = float(np.std(pnl_arr))
        if len(pnl_arr) < 2 or _std < 1e-6:
            sharpe = 0.0
        else:
            sharpe = (float(np.mean(pnl_arr)) / _std) * float(np.sqrt(252))
            sharpe = max(-30.0, min(sharpe, 30.0))  # Hard cap ±30

        # ── Sortino (penalise only downside deviation) ──────
        downside = pnl_arr[pnl_arr < 0]
        _sort_std = float(np.std(downside)) if len(downside) > 1 else 0.0
        if _sort_std < 1e-6:
            sortino = 0.0
        else:
            sortino = (float(np.mean(pnl_arr)) / _sort_std) * float(np.sqrt(252))
            sortino = max(-30.0, min(sortino, 30.0))

        # ── Max drawdown ────────────────────────────────────
        eq_arr   = np.array(equity)
        peak_arr = np.maximum.accumulate(eq_arr)
        dd_arr   = (eq_arr - peak_arr) / (peak_arr + 1e-9) * 100
        max_dd   = float(abs(dd_arr.min()))

        # ── Calmar = annualised return / max drawdown ───────
        total_return_pct = (self.capital - self.initial_capital) / self.initial_capital * 100
        n_days           = max(len(equity), 1)
        annualised_ret   = total_return_pct / (n_days / 252)
        calmar           = annualised_ret / max(max_dd, 0.01)

        # ── Monthly return ───────────────────────────────────
        monthly_return = total_return_pct / max(n_days / 22, 1)

        # ── Profit factor, payoff ratio ─────────────────────
        profit_factor = abs(sum(wins)) / (abs(sum(losses)) + 1e-9)
        avg_win       = np.mean(wins)  if wins   else 0.0
        avg_loss      = np.mean(losses) if losses else 0.0
        payoff_ratio  = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

        # ── Consecutive streaks ─────────────────────────────
        max_cons_wins = max_cons_losses = cur_w = cur_l = 0
        for p in net_pnls:
            if p > 0:
                cur_w += 1; cur_l = 0
                max_cons_wins = max(max_cons_wins, cur_w)
            else:
                cur_l += 1; cur_w = 0
                max_cons_losses = max(max_cons_losses, cur_l)

        # ── Recovery factor ─────────────────────────────────
        recovery_factor = total_pnl / (max_dd / 100 * self.initial_capital + 1e-9)

        # ── Session breakdown ───────────────────────────────
        session_stats: Dict[str, Dict] = {
            "opening_drive": {"trades": [], "label": "09:15-10:00"},
            "morning":       {"trades": [], "label": "10:00-11:00"},
            "midday":        {"trades": [], "label": "11:00-13:30"},
            "afternoon":     {"trades": [], "label": "13:30-15:20"},
        }
        for t in self.trades:
            ts = t.get("entry_time")
            if ts:
                try:
                    h = ts.hour if hasattr(ts, "hour") else int(str(ts)[11:13])
                    m = ts.minute if hasattr(ts, "minute") else int(str(ts)[14:16])
                    tot = h * 60 + m
                    if 555 <= tot < 600:          # 09:15-10:00
                        session_stats["opening_drive"]["trades"].append(t["net_pnl"])
                    elif 600 <= tot < 660:         # 10:00-11:00
                        session_stats["morning"]["trades"].append(t["net_pnl"])
                    elif 660 <= tot < 810:         # 11:00-13:30
                        session_stats["midday"]["trades"].append(t["net_pnl"])
                    elif 810 <= tot < 920:         # 13:30-15:20
                        session_stats["afternoon"]["trades"].append(t["net_pnl"])
                except Exception:
                    pass

        sessions: Dict[str, Dict] = {}
        for name, info in session_stats.items():
            pts = info["trades"]
            if pts:
                sessions[name] = {
                    "label":     info["label"],
                    "trades":    len(pts),
                    "win_rate":  round(sum(1 for p in pts if p > 0) / len(pts) * 100, 1),
                    "total_pnl": round(sum(pts), 2),
                    "avg_pnl":   round(np.mean(pts), 2),
                }

        passes_targets = bool(
            win_rate       >= config.TARGET_WIN_RATE and
            sharpe         >= config.TARGET_SHARPE and
            max_dd         <= config.MAX_DRAWDOWN_LIMIT and
            monthly_return >= config.TARGET_MONTHLY_RETURN_PCT
        )

        return {
            "total_trades":      len(self.trades),
            "wins":              len(wins),
            "losses":            len(losses),
            "win_rate":          round(win_rate, 2),
            "avg_win":           round(avg_win, 2),
            "avg_loss":          round(avg_loss, 2),
            "payoff_ratio":      round(payoff_ratio, 2),
            "profit_factor":     round(profit_factor, 2),
            "total_pnl":         round(total_pnl, 2),
            "total_return_pct":  round(total_return_pct, 2),
            "monthly_return":    round(monthly_return, 2),
            "annualised_return": round(annualised_ret, 2),
            "sharpe":            round(sharpe, 2),
            "sortino":           round(sortino, 2),
            "calmar":            round(calmar, 2),
            "max_drawdown":      round(max_dd, 2),
            "recovery_factor":   round(recovery_factor, 2),
            "max_cons_wins":     max_cons_wins,
            "max_cons_losses":   max_cons_losses,
            "final_capital":     round(self.capital, 2),
            "passes_targets":    passes_targets,
            "sessions":          sessions,
        }


# -----------------------------------------------------------
# WALK-FORWARD OPTIMIZER (Optuna)
# -----------------------------------------------------------

class WalkForwardOptimizer:
    """
    Optuna-powered walk-forward parameter optimization.
    Uses in-sample training window + out-of-sample validation.
    Prevents look-ahead bias.
    """

    def __init__(self, df: pd.DataFrame, n_splits: int = 5):
        self.df       = df
        self.n_splits = n_splits

    def optimize(self, n_trials: int = 100) -> Dict:
        """Run Optuna optimization over walk-forward windows."""
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            logger.error("optuna not installed: pip install optuna")
            return {}

        all_results = []
        window_size = len(self.df) // (self.n_splits + 1)

        for split in range(self.n_splits):
            train_start = split * window_size
            train_end   = train_start + window_size
            test_start  = train_end
            test_end    = min(test_start + window_size // 2, len(self.df))

            df_train = self.df.iloc[train_start:train_end]
            df_test  = self.df.iloc[test_start:test_end]

            if len(df_train) < 100 or len(df_test) < 20:
                continue

            logger.info(f"[{format_ist_timestamp()}] Walk-forward split {split+1}/{self.n_splits}...")

            def objective(trial):
                atr_sl   = trial.suggest_float("atr_sl",  1.0, 2.5, step=0.1)
                atr_t1   = trial.suggest_float("atr_t1",  1.5, 3.5, step=0.1)
                atr_t2   = trial.suggest_float("atr_t2",  3.0, 6.0, step=0.5)
                risk_pct = trial.suggest_float("risk_pct", 0.3, 1.0, step=0.1)

                signals = self._generate_signals(df_train, atr_sl)
                if signals.empty:
                    return -1000.0

                engine = BacktestEngine(initial_capital=100000)
                stats  = engine.run(df_train, signals, atr_sl, atr_t1, atr_t2, risk_pct)
                if "error" in stats:
                    return -1000.0

                # Objective: maximise Sharpe, penalise drawdown
                score = stats["sharpe"] * 10 - max(0, stats["max_drawdown"] - 5) * 2
                return score

            study = optuna.create_study(direction="maximize")
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
            best = study.best_params

            # Validate on out-of-sample test window
            signals_test = self._generate_signals(df_test, best.get("atr_sl", 1.5))
            if not signals_test.empty:
                engine = BacktestEngine(100000)
                oos_stats = engine.run(
                    df_test, signals_test,
                    best["atr_sl"], best["atr_t1"], best["atr_t2"], best["risk_pct"]
                )
                all_results.append({"split": split, "params": best, "oos": oos_stats})
                logger.info(
                    f"[{format_ist_timestamp()}] Split {split+1} OOS: "
                    f"WR={oos_stats.get('win_rate',0):.1f}% "
                    f"Sharpe={oos_stats.get('sharpe',0):.2f} "
                    f"DD={oos_stats.get('max_drawdown',0):.1f}%"
                )

        if not all_results:
            return {}

        # Average best parameters across splits
        param_keys = ["atr_sl", "atr_t1", "atr_t2", "risk_pct"]
        avg_params = {}
        for k in param_keys:
            vals = [r["params"].get(k, 0) for r in all_results]
            avg_params[k] = round(np.mean(vals), 2)

        self._save_optimized_params(avg_params, all_results)
        return {"best_params": avg_params, "split_results": all_results}

    def _generate_signals(self, df: pd.DataFrame, atr_sl_mult: float) -> pd.DataFrame:
        """Generate mock signals from indicators for backtesting."""
        signals = []
        if len(df) < 30:
            return pd.DataFrame()
        for i in range(30, len(df)):
            row = df.iloc[i]
            rsi  = float(row.get("rsi", 50) or 50)
            hist = float(row.get("macd_hist", 0) or 0)
            close = float(row.get("close", 0))
            vol_ratio = float(row.get("volume_ratio", 1) or 1)
            atr  = float(row.get("atr", close * 0.005) or close * 0.005)

            score = 0
            direction = None

            if rsi < 40 and hist > 0 and vol_ratio > 1.5:
                score     = 70
                direction = "LONG"
            elif rsi > 60 and hist < 0 and vol_ratio > 1.5:
                score     = 70
                direction = "SHORT"

            if direction and score >= 65:
                signals.append({
                    "timestamp": df.index[i],
                    "direction": direction,
                    "entry":     close,
                    "atr":       atr,
                    "score":     score,
                })
        return pd.DataFrame(signals)

    def _save_optimized_params(self, params: Dict, results: List):
        out = RESULTS_DIR / f"optimized_params_{get_current_ist_time().strftime('%Y%m%d')}.json"
        with open(out, "w") as f:
            json.dump({"params": params, "results": [r["oos"] for r in results]}, f, indent=2, cls=_NumpyEncoder)
        logger.info(f"[{format_ist_timestamp()}] Optimized params saved → {out}")


# -----------------------------------------------------------
# EOD SELF-TRAINER  (runs automatically at 4:30 PM IST daily)
# -----------------------------------------------------------

ADAPTIVE_PARAMS_FILE = Path("data/adaptive_params.json")
ADAPTIVE_PARAMS_FILE.parent.mkdir(exist_ok=True)


class EODSelfTrainer:
    """
    Automatic end-of-day strategy optimizer.

    Runs at 4:30 PM IST after every market close:
      1. Downloads last 30 days of 5m/15m data for top 10 watchlist stocks
      2. Walk-forward optimizes ATR_SL, ATR_T1, ATR_T2, risk_pct (20 trials × 3 splits)
      3. Averages best params across all symbols
      4. Safety gate: only adopts new params if Sharpe improved ≥ 5%
      5. Saves to data/adaptive_params.json (read by main.py each morning)
      6. Sends results summary to Telegram

    18yr Rule: "Your strategy should get smarter every single day.
    Markets evolve — a strategy that never adapts will eventually die."

    Param file format:
      {
        "date":         "2026-04-09",
        "atr_sl":       1.4,
        "atr_t1":       2.6,
        "atr_t2":       4.2,
        "risk_pct":     0.5,
        "min_score":    72.0,
        "sharpe":       1.87,
        "win_rate":     61.2,
        "monthly_ret":  6.4,
        "symbols_used": ["RELIANCE", "TCS", ...],
        "improved":     true
      }
    """

    # Safe bounds — trainer can never drift params outside these
    PARAM_BOUNDS = {
        "atr_sl":   (1.0, 2.5),
        "atr_t1":   (1.5, 4.0),
        "atr_t2":   (3.0, 7.0),
        "risk_pct": (0.3, 1.0),
    }

    def run(
        self,
        fetcher,
        symbols:   List[str] = None,
        lookback:  int        = 30,
        n_trials:  int        = 20,
        n_splits:  int        = 3,
        alerter                = None,
    ) -> Dict:
        """
        Full EOD training cycle.
        Returns summary dict (also sent to Telegram).
        """
        import config as _cfg

        if symbols is None:
            symbols = _cfg.DEFAULT_WATCHLIST[:10]

        logger.info(
            f"[{format_ist_timestamp()}] EOD Self-Trainer starting — "
            f"{len(symbols)} symbols, {lookback}d lookback, {n_trials} trials"
        )

        # Load current (yesterday's) params as baseline
        baseline = self.load_params()
        baseline_sharpe = baseline.get("sharpe", 0.0)

        per_symbol_params: List[Dict] = []
        per_symbol_stats:  List[Dict] = []

        for sym in symbols:
            try:
                df = fetcher.get_candles(sym, interval="5m", days=lookback)
                if df is None or len(df) < 100:
                    continue

                # Add indicators
                try:
                    from pattern_recognition import TechnicalIndicators
                    df = TechnicalIndicators().compute(df)
                except Exception:
                    continue

                # Walk-forward optimization
                opt    = WalkForwardOptimizer(df, n_splits=n_splits)
                result = opt.optimize(n_trials=n_trials)

                if result.get("best_params") and result.get("split_results"):
                    best_p = result["best_params"]
                    oos_stats = [r["oos"] for r in result["split_results"] if "oos" in r]

                    if oos_stats:
                        avg_sharpe = float(np.mean([s.get("sharpe", 0) for s in oos_stats]))
                        avg_wr     = float(np.mean([s.get("win_rate", 0) for s in oos_stats]))
                        avg_ret    = float(np.mean([s.get("monthly_return", 0) for s in oos_stats]))

                        per_symbol_params.append(best_p)
                        per_symbol_stats.append({
                            "symbol":  sym,
                            "sharpe":  round(avg_sharpe, 3),
                            "win_rate":round(avg_wr, 1),
                            "monthly": round(avg_ret, 2),
                        })
                        logger.info(
                            f"[{format_ist_timestamp()}] {sym}: "
                            f"Sharpe={avg_sharpe:.2f} WR={avg_wr:.1f}% "
                            f"Monthly={avg_ret:.1f}%"
                        )
            except Exception as e:
                logger.warning(f"EOD train failed for {sym}: {e}")

        if not per_symbol_params:
            logger.warning(f"[{format_ist_timestamp()}] EOD Self-Trainer: no usable results")
            return {"improved": False, "reason": "No usable optimization results"}

        # Average params across all symbols (ensemble approach)
        param_keys  = ["atr_sl", "atr_t1", "atr_t2", "risk_pct"]
        avg_params  = {}
        for k in param_keys:
            vals = [p[k] for p in per_symbol_params if k in p]
            if vals:
                raw = float(np.mean(vals))
                lo, hi = self.PARAM_BOUNDS[k]
                avg_params[k] = round(max(lo, min(raw, hi)), 3)

        # Overall performance summary
        avg_sharpe  = float(np.mean([s["sharpe"]   for s in per_symbol_stats]))
        avg_wr      = float(np.mean([s["win_rate"]  for s in per_symbol_stats]))
        avg_monthly = float(np.mean([s["monthly"]   for s in per_symbol_stats]))

        # Safety gate: only adopt if Sharpe improved ≥ 5% over yesterday's
        improvement = (avg_sharpe - baseline_sharpe) / max(abs(baseline_sharpe), 0.1)
        improved    = bool(improvement >= 0.05 or baseline_sharpe == 0.0)

        # Adaptive min_score: tighten if win rate is high, loosen if low
        current_min = baseline.get("min_score", _cfg.MIN_SIGNAL_SCORE)
        if avg_wr >= 65:
            new_min_score = min(current_min + 1.0, 82.0)   # Getting better → tighten
        elif avg_wr <= 50:
            new_min_score = max(current_min - 1.0, 65.0)   # Getting worse → loosen
        else:
            new_min_score = current_min

        new_params = {
            "date":         get_current_ist_time().strftime("%Y-%m-%d"),
            **avg_params,
            "min_score":    round(new_min_score, 1),
            "sharpe":       round(avg_sharpe, 3),
            "win_rate":     round(avg_wr, 1),
            "monthly_ret":  round(avg_monthly, 2),
            "symbols_used": [s["symbol"] for s in per_symbol_stats],
            "improved":     improved,
            "improvement_pct": round(improvement * 100, 1),
            "baseline_sharpe": round(baseline_sharpe, 3),
        }

        if improved:
            self._save_params(new_params)
            logger.info(
                f"[{format_ist_timestamp()}] ✅ EOD Self-Trainer: params UPDATED — "
                f"Sharpe {baseline_sharpe:.2f}→{avg_sharpe:.2f} "
                f"(+{improvement*100:.1f}%)"
            )
        else:
            # Still save for reference but keep yesterday's params active
            new_params["active"] = False
            logger.info(
                f"[{format_ist_timestamp()}] ℹ️ EOD Self-Trainer: no improvement "
                f"({improvement*100:+.1f}%) — keeping yesterday's params"
            )

        # Send Telegram summary
        if alerter:
            self._send_telegram_summary(alerter, new_params, per_symbol_stats, improved)

        return new_params

    def _save_params(self, params: Dict) -> None:
        """Save adaptive params to disk — read by main.py each morning."""
        try:
            ADAPTIVE_PARAMS_FILE.write_text(json.dumps(params, indent=2, cls=_NumpyEncoder))
            # Also archive daily copy
            archive = RESULTS_DIR / f"params_{params['date']}.json"
            archive.write_text(json.dumps(params, indent=2, cls=_NumpyEncoder))
        except Exception as e:
            logger.error(f"Failed to save adaptive params: {e}")

    @staticmethod
    def load_params() -> Dict:
        """
        Load yesterday's trained params.
        Called by main.py at 9:00 AM IST to apply for today's trading.
        Returns empty dict if no params file exists (use config defaults).
        """
        try:
            if ADAPTIVE_PARAMS_FILE.exists():
                data = json.loads(ADAPTIVE_PARAMS_FILE.read_text())
                # Only use if trained today or yesterday (not stale)
                from datetime import date, timedelta
                today     = get_current_ist_time().date()
                train_date = date.fromisoformat(data.get("date", "2000-01-01"))
                if (today - train_date).days <= 3:   # Accept params up to 3 days old
                    return data
        except Exception as e:
            logger.warning(f"Load adaptive params failed: {e}")
        return {}

    def _send_telegram_summary(
        self, alerter, params: Dict, sym_stats: List[Dict], improved: bool
    ) -> None:
        try:
            emoji  = "✅" if improved else "ℹ️"
            lines  = [
                f"{emoji} <b>EOD Self-Training Complete</b> — "
                f"{params.get('date', '')}",
                "",
                f"📊 <b>Optimized Parameters:</b>",
                f"  ATR SL:   {params.get('atr_sl', '-')}×",
                f"  ATR T1:   {params.get('atr_t1', '-')}×",
                f"  ATR T2:   {params.get('atr_t2', '-')}×",
                f"  Risk/trade: {params.get('risk_pct', '-')}%",
                f"  Min score:  {params.get('min_score', '-')}",
                "",
                f"📈 <b>Backtest Performance (OOS avg):</b>",
                f"  Sharpe: {params.get('baseline_sharpe',0):.2f} → "
                f"{params.get('sharpe',0):.2f} "
                f"({params.get('improvement_pct',0):+.1f}%)",
                f"  Win rate:    {params.get('win_rate',0):.1f}%",
                f"  Monthly est: {params.get('monthly_ret',0):.1f}%",
                "",
            ]
            if sym_stats:
                lines.append("🔬 <b>Symbol Results:</b>")
                for s in sym_stats[:5]:
                    lines.append(
                        f"  {s['symbol']:12s} WR={s['win_rate']:.0f}%  "
                        f"Sharpe={s['sharpe']:.2f}"
                    )
            if improved:
                lines.append("\n✅ Params adopted — trading tomorrow with updated strategy")
            else:
                lines.append("\nℹ️ No improvement — keeping yesterday's params")

            alerter.send_text("\n".join(lines))
        except Exception as e:
            logger.warning(f"EOD training Telegram summary failed: {e}")


# -----------------------------------------------------------
# MONTE CARLO SIMULATOR
# -----------------------------------------------------------

class MonteCarloSimulator:
    """
    Stress-tests the strategy by shuffling trade order 1000× times.

    18yr Rule: "If your strategy only works in one specific sequence of trades,
    it's not a strategy — it's luck. MC simulation exposes the difference."

    Key outputs:
      P10/P50/P90 final equity   → confidence band on expected outcome
      Ruin probability           → % of sims with drawdown > 40%
      Max drawdown distribution  → 90th percentile worst-case drawdown
    """

    def simulate(
        self,
        trades:       List[Dict],
        initial_capital: float = 100000,
        n_simulations:   int   = 1000,
        ruin_threshold:  float = 40.0,   # % drawdown = "ruin"
    ) -> Dict:
        if not trades or len(trades) < 5:
            return {"error": "Need at least 5 trades for MC simulation"}

        net_pnls     = [t["net_pnl"] for t in trades]
        final_equities: List[float] = []
        max_drawdowns:  List[float] = []
        ruin_count   = 0

        rng = random.Random(42)   # Reproducible

        for _ in range(n_simulations):
            shuffled = net_pnls[:]
            rng.shuffle(shuffled)

            capital  = initial_capital
            peak     = initial_capital
            max_dd   = 0.0

            for pnl in shuffled:
                capital += pnl
                peak     = max(peak, capital)
                dd       = (peak - capital) / peak * 100
                max_dd   = max(max_dd, dd)

            final_equities.append(capital)
            max_drawdowns.append(max_dd)
            if max_dd >= ruin_threshold:
                ruin_count += 1

        eq_arr  = sorted(final_equities)
        dd_arr  = sorted(max_drawdowns)
        n       = len(eq_arr)
        ruin_pct = ruin_count / n_simulations * 100

        def percentile(arr: list, p: float) -> float:
            idx = int(len(arr) * p / 100)
            return round(arr[min(idx, len(arr) - 1)], 2)

        result = {
            "simulations":         n_simulations,
            "ruin_probability_pct": round(ruin_pct, 2),
            "final_equity": {
                "p10": percentile(eq_arr, 10),
                "p25": percentile(eq_arr, 25),
                "p50": percentile(eq_arr, 50),
                "p75": percentile(eq_arr, 75),
                "p90": percentile(eq_arr, 90),
            },
            "max_drawdown": {
                "p50": percentile(dd_arr, 50),
                "p75": percentile(dd_arr, 75),
                "p90": percentile(dd_arr, 90),
                "p95": percentile(dd_arr, 95),
            },
            "expected_return_pct": round(
                (np.mean(final_equities) - initial_capital) / initial_capital * 100, 2
            ),
            "passes_mc": ruin_pct < 5.0,   # Must be < 5% ruin prob to be deployable
        }

        logger.info(
            f"[{format_ist_timestamp()}] MC ({n_simulations} sims): "
            f"P50 equity=₹{result['final_equity']['p50']:,.0f} | "
            f"Ruin={ruin_pct:.1f}% | "
            f"P90 drawdown={result['max_drawdown']['p90']:.1f}%"
        )
        return result

    def format_report(self, mc: Dict) -> str:
        if "error" in mc:
            return f"MC Error: {mc['error']}"
        p = mc["passes_mc"]
        lines = [
            f"\n{'✅' if p else '❌'} Monte Carlo ({mc['simulations']} simulations)",
            f"  Ruin probability:  {mc['ruin_probability_pct']:.1f}%  ({'SAFE' if p else 'RISKY'})",
            f"  Expected return:   {mc['expected_return_pct']:+.1f}%",
            f"  Final equity (₹):  P10={mc['final_equity']['p10']:,.0f}  "
            f"P50={mc['final_equity']['p50']:,.0f}  P90={mc['final_equity']['p90']:,.0f}",
            f"  Max drawdown (%):  P50={mc['max_drawdown']['p50']:.1f}  "
            f"P90={mc['max_drawdown']['p90']:.1f}  P95={mc['max_drawdown']['p95']:.1f}",
        ]
        return "\n".join(lines)


# -----------------------------------------------------------
# BENCHMARK COMPARATOR
# -----------------------------------------------------------

class BenchmarkComparator:
    """
    Compares strategy returns vs Nifty50 buy-and-hold.

    Key metrics:
      Alpha       = strategy return - benchmark return (excess return)
      Beta        = correlation of strategy returns to Nifty daily moves
      Info Ratio  = alpha / tracking_error (consistency of outperformance)
      Sharpe vs   = strategy Sharpe vs Nifty Sharpe
    """

    def compare(
        self,
        strategy_trades: List[Dict],
        initial_capital:  float = 100000,
        period_days:      int   = 180,
    ) -> Dict:
        try:
            import yfinance as yf
        except ImportError:
            return {"error": "yfinance not installed"}

        if not strategy_trades:
            return {"error": "No trades to compare"}

        # Fetch Nifty50 data for same period
        try:
            end_date   = get_current_ist_time()
            start_date = end_date - timedelta(days=period_days + 10)
            nifty = yf.download(
                "^NSEI", start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                progress=False, auto_adjust=True,
            )
            if nifty.empty:
                return {"error": "Could not fetch Nifty data"}
        except Exception as e:
            return {"error": f"Nifty fetch failed: {e}"}

        # Benchmark: buy-and-hold Nifty
        nifty_start = float(nifty["Close"].iloc[0])
        nifty_end   = float(nifty["Close"].iloc[-1])
        bm_return   = (nifty_end - nifty_start) / nifty_start * 100

        # Strategy equity curve
        capital     = initial_capital
        strat_pnls  = [t["net_pnl"] for t in strategy_trades]
        strat_return = sum(strat_pnls) / initial_capital * 100

        # Nifty daily returns
        nifty_daily = nifty["Close"].pct_change().dropna().values
        # Strategy daily P&L approximation
        strat_daily = np.array(strat_pnls)

        # Alpha & Beta (simple regression)
        if len(nifty_daily) >= 5 and len(strat_daily) >= 5:
            n = min(len(nifty_daily), len(strat_daily))
            x = nifty_daily[:n]
            y = strat_daily[:n] / initial_capital  # Convert to returns

            beta    = float(np.cov(x, y)[0][1] / (np.var(x) + 1e-9))
            alpha_d = float(np.mean(y) - beta * np.mean(x))
            alpha   = alpha_d * 252 * 100  # Annualised %

            # Information ratio
            excess_returns   = y - x * beta
            tracking_error   = float(np.std(excess_returns) * np.sqrt(252))
            info_ratio       = float(alpha / (tracking_error * 100 + 1e-9))

            # Sharpe comparison
            strat_sharpe = (np.mean(y) / (np.std(y) + 1e-9)) * np.sqrt(252)
            nifty_sharpe = (np.mean(x) / (np.std(x) + 1e-9)) * np.sqrt(252)
        else:
            beta = alpha = info_ratio = 0.0
            strat_sharpe = nifty_sharpe = 0.0

        result = {
            "strategy_return_pct":   round(strat_return, 2),
            "benchmark_return_pct":  round(bm_return, 2),
            "alpha_annualised_pct":  round(alpha, 2),
            "beta":                  round(beta, 3),
            "info_ratio":            round(info_ratio, 3),
            "strategy_sharpe":       round(float(strat_sharpe), 2),
            "benchmark_sharpe":      round(float(nifty_sharpe), 2),
            "outperformance_pct":    round(strat_return - bm_return, 2),
            "passes_benchmark":      strat_return > bm_return and alpha > 0,
        }

        logger.info(
            f"[{format_ist_timestamp()}] Benchmark: "
            f"Strategy {strat_return:+.1f}% vs Nifty {bm_return:+.1f}% | "
            f"Alpha={alpha:+.1f}% | Beta={beta:.2f} | IR={info_ratio:.2f}"
        )
        return result

    def format_report(self, bm: Dict) -> str:
        if "error" in bm:
            return f"Benchmark Error: {bm['error']}"
        p = bm.get("passes_benchmark", False)
        lines = [
            f"\n{'✅' if p else '❌'} Benchmark vs Nifty50",
            f"  Strategy return:  {bm['strategy_return_pct']:+.1f}%",
            f"  Nifty return:     {bm['benchmark_return_pct']:+.1f}%",
            f"  Outperformance:   {bm['outperformance_pct']:+.1f}%",
            f"  Alpha (ann):      {bm['alpha_annualised_pct']:+.1f}%",
            f"  Beta:             {bm['beta']:.3f}",
            f"  Info Ratio:       {bm['info_ratio']:.3f}",
            f"  Sharpe: strategy={bm['strategy_sharpe']:.2f} vs Nifty={bm['benchmark_sharpe']:.2f}",
        ]
        return "\n".join(lines)


# -----------------------------------------------------------
# SENSITIVITY ANALYZER
# -----------------------------------------------------------

class SensitivityAnalyzer:
    """
    Tests how strategy performance degrades when parameters shift ±10/20/30%.

    18yr Rule: "A robust strategy is one whose edge survives slightly wrong
    parameters. If it only works at one exact setting, it's curve-fitted."

    Robustness score 0-1:
      > 0.7 = robust (Sharpe stays within 30% across ±20% param range)
      0.4-0.7 = moderate (use with caution)
      < 0.4 = fragile (likely overfit — do NOT deploy)
    """

    def analyze(
        self,
        df:          pd.DataFrame,
        base_params: Dict,
        variations:  List[float] = None,
    ) -> Dict:
        if variations is None:
            variations = [-0.30, -0.20, -0.10, 0.0, 0.10, 0.20, 0.30]

        params_to_test = ["atr_sl", "atr_t1", "atr_t2", "risk_pct"]
        results: Dict[str, List] = {p: [] for p in params_to_test}

        base_sharpe = self._run_single(df, base_params)

        for param in params_to_test:
            base_val = base_params.get(param, 1.0)
            for var in variations:
                test_params = base_params.copy()
                test_params[param] = round(base_val * (1 + var), 3)
                sharpe = self._run_single(df, test_params)
                results[param].append({
                    "variation_pct": round(var * 100, 0),
                    "param_value":   test_params[param],
                    "sharpe":        round(sharpe, 3),
                    "sharpe_delta":  round(sharpe - base_sharpe, 3),
                })

        # Robustness score: average Sharpe retention at ±20% variation
        robustness_scores = []
        for param, param_results in results.items():
            vals_at_20 = [
                r["sharpe"] for r in param_results
                if abs(r["variation_pct"]) <= 20
            ]
            if vals_at_20 and base_sharpe > 0:
                avg_retention = np.mean(vals_at_20) / max(base_sharpe, 0.01)
                robustness_scores.append(avg_retention)

        robustness = round(float(np.mean(robustness_scores)) if robustness_scores else 0, 3)

        result = {
            "base_sharpe":    round(base_sharpe, 3),
            "robustness_score": robustness,
            "grade": ("ROBUST" if robustness > 0.7 else
                      "MODERATE" if robustness > 0.4 else "FRAGILE"),
            "param_sensitivity": results,
            "passes_sensitivity": robustness > 0.7,
        }

        logger.info(
            f"[{format_ist_timestamp()}] Sensitivity: "
            f"Robustness={robustness:.3f} ({result['grade']}) | "
            f"Base Sharpe={base_sharpe:.3f}"
        )
        return result

    def _run_single(self, df: pd.DataFrame, params: Dict) -> float:
        """Run a quick backtest and return Sharpe ratio."""
        try:
            signals = WalkForwardOptimizer(df)._generate_signals(
                df, params.get("atr_sl", 1.5)
            )
            if signals.empty:
                return 0.0
            engine = BacktestEngine(100000)
            stats  = engine.run(
                df, signals,
                atr_sl_mult  = params.get("atr_sl",   1.5),
                atr_t1_mult  = params.get("atr_t1",   2.5),
                atr_t2_mult  = params.get("atr_t2",   4.0),
                max_risk_pct = params.get("risk_pct",  0.5),
            )
            return float(stats.get("sharpe", 0.0))
        except Exception:
            return 0.0

    def format_report(self, sens: Dict) -> str:
        if "error" in sens:
            return f"Sensitivity Error: {sens['error']}"
        p = sens.get("passes_sensitivity", False)
        lines = [
            f"\n{'✅' if p else '❌'} Sensitivity Analysis — {sens['grade']}",
            f"  Base Sharpe:       {sens['base_sharpe']:.3f}",
            f"  Robustness score:  {sens['robustness_score']:.3f}  "
            f"(>0.7 = robust, 0.4-0.7 = moderate, <0.4 = fragile)",
        ]
        # Show worst param
        worst_param = ""
        worst_score = 9999
        for param, vals in sens.get("param_sensitivity", {}).items():
            sharpes = [v["sharpe"] for v in vals if v["sharpe"] > 0]
            if sharpes:
                avg = np.mean(sharpes)
                if avg < worst_score:
                    worst_score = avg
                    worst_param = param
        if worst_param:
            lines.append(f"  Most sensitive param: {worst_param}")
        return "\n".join(lines)


# -----------------------------------------------------------
# CLI ENTRY POINT
# -----------------------------------------------------------

def main():
    setup_logging(config.LOG_DIR, config.LOG_LEVEL)
    parser = argparse.ArgumentParser(
        description="NSE Bot Trainer — Backtesting + Walk-Forward + MC + Benchmark + Sensitivity"
    )
    parser.add_argument("--symbols",          default="RELIANCE,TCS,INFY")
    parser.add_argument("--days",             type=int,  default=180)
    parser.add_argument("--trials",           type=int,  default=50)
    parser.add_argument("--capital",          type=float, default=100000)
    parser.add_argument("--quick-test",       action="store_true", help="30d, all analyses fast")
    parser.add_argument("--full-optimization",action="store_true", help="Full walk-forward optimization")
    parser.add_argument("--monte-carlo",      action="store_true", help="Run MC simulation")
    parser.add_argument("--benchmark",        action="store_true", help="Benchmark vs Nifty50")
    parser.add_argument("--sensitivity",      action="store_true", help="Parameter sensitivity")
    parser.add_argument("--all",              action="store_true", help="Run all analyses")
    args = parser.parse_args()

    if args.quick_test or args.all:
        if args.quick_test:
            args.days   = 30
            args.trials = 20
        args.monte_carlo = args.benchmark = args.sensitivity = True

    symbols = [s.strip() for s in args.symbols.split(",")]
    logger.info(f"[{format_ist_timestamp()}] Trainer. Symbols: {symbols}, Days: {args.days}")

    try:
        from data_fetch_groww import get_data_fetcher
        fetcher = get_data_fetcher()
    except Exception as e:
        logger.error(f"Cannot initialize data fetcher: {e}")
        sys.exit(1)

    all_results   = {}
    mc_runner     = MonteCarloSimulator()
    bm_runner     = BenchmarkComparator()
    sens_runner   = SensitivityAnalyzer()

    for symbol in symbols:
        logger.info(f"[{format_ist_timestamp()}] ── Processing {symbol} ──")
        try:
            df = fetcher.get_candles(symbol, interval="5m", days=args.days)
            if df is None or len(df) < 100:
                logger.warning(f"Insufficient data for {symbol}")
                continue

            try:
                from pattern_recognition import TechnicalIndicators
                df = TechnicalIndicators().compute(df)
            except Exception as e:
                logger.warning(f"Indicator compute failed {symbol}: {e}")
                continue

            sym_result: Dict = {}

            # ── Core backtest ──────────────────────────────────
            engine  = BacktestEngine(args.capital)
            signals = WalkForwardOptimizer(df)._generate_signals(df, 1.5)
            bt      = engine.run(df, signals) if not signals.empty else {"error": "no signals"}
            sym_result["backtest"] = bt

            if "win_rate" in bt:
                p = "✅" if bt.get("passes_targets") else "❌"
                print(
                    f"\n{p} {symbol} Backtest: "
                    f"WR={bt['win_rate']:.1f}%  Sharpe={bt['sharpe']:.2f}  "
                    f"Sortino={bt['sortino']:.2f}  Calmar={bt['calmar']:.2f}  "
                    f"DD={bt['max_drawdown']:.1f}%  Monthly={bt['monthly_return']:.1f}%  "
                    f"PF={bt['profit_factor']:.2f}"
                )
                # Session breakdown
                if bt.get("sessions"):
                    print("  Sessions:")
                    for sname, sdata in bt["sessions"].items():
                        print(
                            f"    {sname:15s} ({sdata['label']}): "
                            f"{sdata['trades']} trades | "
                            f"WR={sdata['win_rate']:.0f}% | "
                            f"P&L=₹{sdata['total_pnl']:+,.0f}"
                        )

            # ── Walk-forward optimization ──────────────────────
            if args.full_optimization:
                optimizer = WalkForwardOptimizer(df, n_splits=5)
                wf = optimizer.optimize(n_trials=args.trials)
                sym_result["walk_forward"] = wf
                if wf.get("best_params"):
                    print(f"  WF best params: {wf['best_params']}")

            # ── Monte Carlo ────────────────────────────────────
            if getattr(args, "monte_carlo", False) and engine.trades:
                mc = mc_runner.simulate(engine.trades, args.capital)
                sym_result["monte_carlo"] = mc
                print(mc_runner.format_report(mc))

            # ── Benchmark vs Nifty ─────────────────────────────
            if getattr(args, "benchmark", False) and engine.trades:
                bm = bm_runner.compare(engine.trades, args.capital, args.days)
                sym_result["benchmark"] = bm
                print(bm_runner.format_report(bm))

            # ── Sensitivity analysis ───────────────────────────
            if getattr(args, "sensitivity", False):
                base_params = {"atr_sl": 1.5, "atr_t1": 2.5, "atr_t2": 4.0, "risk_pct": 0.5}
                sens = sens_runner.analyze(df, base_params)
                sym_result["sensitivity"] = sens
                print(sens_runner.format_report(sens))

            all_results[symbol] = sym_result

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Trainer failed for {symbol}: {e}")

    out_file = RESULTS_DIR / f"trainer_{get_current_ist_time().strftime('%Y%m%d_%H%M')}.json"
    with open(out_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"[{format_ist_timestamp()}] Results → {out_file}")
    print(f"\n✅ Trainer complete. Results saved → {out_file}")


if __name__ == "__main__":
    main()


# ============================================================
# MODULE: continuous_learner.py
# ============================================================
"""
continuous_learner.py — NSE Momentum Groww AI Bot
24/7 Continuous Learning Scheduler

The bot NEVER stops learning — even when the market is closed.
This is what gives it the "18 years of experience" that compounds over time.

Schedule:
  03:30 PM IST  Market closes → EOD data download begins
  04:00 PM IST  Self-learning cycle (trade analysis + parameter adaptation)
  04:30 PM IST  AI brain EOD trade review (lessons extracted)
  05:00 PM IST  Walk-forward trainer runs on today's fresh data
  07:00 PM IST  Weekly review (Sundays only)
  08:00 PM IST  Economic calendar refresh
  10:00 PM IST  Global market snapshot (US markets open at ~9:30 PM IST)
  01:00 AM IST  US markets close → overnight analysis starts
  06:00 AM IST  Asian markets data fetch
  08:00 AM IST  Full pre-market intelligence report generated
  08:30 AM IST  Morning brief sent to Telegram
  08:45 AM IST  TOTP login to Groww → token refresh

Daily learning targets:
  - Download fresh 5m/15m/1h/daily candles for all watchlist stocks
  - Update pattern win rates from today's trades
  - Adjust RSI thresholds, min score, session multipliers
  - Disable chronic losing patterns
  - Update seasonal/calendar intelligence
  - Generate tomorrow's trading plan with AI
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from utils import (
    format_ist_timestamp, get_current_ist_time, get_current_ist_date,
    is_market_open_ist, is_market_day_ist, setup_logging
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class ScheduledTask:
    """A task that runs at a specific IST time daily (or on specific weekdays)."""
    def __init__(self, name: str, hour_ist: int, minute_ist: int,
                 func: Callable, weekdays: Optional[List[int]] = None,
                 run_on_holidays: bool = False):
        self.name          = name
        self.hour_ist      = hour_ist
        self.minute_ist    = minute_ist
        self.func          = func
        self.weekdays      = weekdays    # None = every day, [0,1,2,3,4] = Mon-Fri
        self.run_on_holidays = run_on_holidays
        self.last_run_date: Optional[str] = None
        self.run_count     = 0
        self.last_error:   Optional[str] = None

    def is_due(self, now_ist: datetime) -> bool:
        today_str = str(now_ist.date())
        if self.last_run_date == today_str:
            return False
        if self.weekdays and now_ist.weekday() not in self.weekdays:
            return False
        if now_ist.hour == self.hour_ist and now_ist.minute == self.minute_ist:
            return True
        # Allow 2-minute late window (in case scheduler wakes up late)
        target = now_ist.replace(hour=self.hour_ist, minute=self.minute_ist, second=0)
        diff   = (now_ist - target).total_seconds()
        return 0 <= diff <= 120

    def run(self):
        logger.info(f"[{format_ist_timestamp()}] ⏰ Task: {self.name}")
        try:
            self.func()
            self.run_count    += 1
            self.last_run_date = str(get_current_ist_date())
            logger.info(f"[{format_ist_timestamp()}] ✅ Task done: {self.name}")
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[{format_ist_timestamp()}] ❌ Task failed: {self.name} — {e}")


class ContinuousLearner:
    """
    Runs as a background process (or standalone) 24/7.
    Learns from every market day and adapts the strategy permanently.
    """

    def __init__(self):
        self.running  = False
        self.tasks:   List[ScheduledTask] = []
        self._modules: Dict = {}
        self._token_refreshed_date: str = ""  # YYYY-MM-DD — skip cascade once refreshed

    # ── INITIALISE ALL MODULES ─────────────────────────────

    def _load_modules(self):
        """Lazy-load all bot modules. Avoids import errors at startup."""
        try:
            from data_fetch_groww import get_data_fetcher
            self._modules["fetcher"] = get_data_fetcher()
        except Exception as e:
            logger.warning(f"Fetcher not available: {e}")

        try:
            from market_data_store import get_data_store
            self._modules["store"] = get_data_store()
        except Exception as e:
            logger.warning(f"Data store not available: {e}")

        try:
            from self_learning import get_learner
            self._modules["learner"] = get_learner()
        except Exception as e:
            logger.warning(f"Learner not available: {e}")

        try:
            from ai_brain import get_ai_brain
            self._modules["ai"] = get_ai_brain()
        except Exception as e:
            logger.warning(f"AI Brain not available: {e}")

        try:
            from trade_journal import get_journal
            self._modules["journal"] = get_journal()
        except Exception as e:
            logger.warning(f"Journal not available: {e}")

        try:
            from overnight_analyzer import get_overnight_analyzer
            self._modules["overnight"] = get_overnight_analyzer()
        except Exception as e:
            logger.warning(f"Overnight analyzer not available: {e}")

        try:
            from economic_calendar import get_calendar
            self._modules["calendar"] = get_calendar()
        except Exception as e:
            logger.warning(f"Calendar not available: {e}")

        try:
            from alerts_telegram import TelegramAlerter
            import config
            if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
                self._modules["alerter"] = TelegramAlerter(
                    config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID
                )
        except Exception as e:
            logger.warning(f"Alerter not available: {e}")

        try:
            from watchlist_manager import WatchlistManager
            self._modules["watchlist"] = WatchlistManager()
        except Exception as e:
            logger.warning(f"Watchlist not available: {e}")

        logger.info(f"[{format_ist_timestamp()}] Modules loaded: {list(self._modules.keys())}")

    # ── REGISTER ALL SCHEDULED TASKS ──────────────────────

    def _register_tasks(self):
        """Register all scheduled learning tasks."""

        # EOD data download (Mon-Fri, after market close)
        self.tasks.append(ScheduledTask(
            "EOD Data Download", 15, 45,
            self._task_eod_data_download,
            weekdays=[0,1,2,3,4]
        ))

        # Self-learning cycle (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Self-Learning Cycle", 16, 0,
            self._task_self_learning,
            weekdays=[0,1,2,3,4]
        ))

        # AI EOD trade review (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "AI EOD Trade Review", 16, 30,
            self._task_ai_eod_review,
            weekdays=[0,1,2,3,4]
        ))

        # EOD Self-Training (Mon-Fri at 4:30 PM IST)
        # Runs after market close, optimizes tomorrow's params from last 30 days
        self.tasks.append(ScheduledTask(
            "EOD Self-Training", 16, 30,
            self._task_eod_self_training,
            weekdays=[0,1,2,3,4]
        ))

        # Weekly deep review (Sunday evening)
        self.tasks.append(ScheduledTask(
            "Weekly Strategy Review", 19, 0,
            self._task_weekly_review,
            weekdays=[6]   # Sunday
        ))

        # Economic calendar refresh (daily)
        self.tasks.append(ScheduledTask(
            "Calendar Refresh", 20, 0,
            self._task_calendar_refresh
        ))

        # US market snapshot (US markets open ~9:30 PM IST)
        self.tasks.append(ScheduledTask(
            "US Market Snapshot", 22, 0,
            self._task_us_snapshot
        ))

        # Overnight analysis — after US markets close (~2 AM IST)
        self.tasks.append(ScheduledTask(
            "Overnight Analysis", 1, 30,
            self._task_overnight_analysis
        ))

        # ── Groww Cloud token refresh cascade (keys reset at 6 AM IST) ──────
        # Try at 6:05, 6:20, 6:40, 7:00, 8:00 — stop as soon as one succeeds
        for h, m in [(6, 5), (6, 20), (6, 40), (7, 0), (8, 0)]:
            self.tasks.append(ScheduledTask(
                f"Token Refresh {h:02d}:{m:02d}",
                h, m,
                self._task_token_refresh,
                weekdays=[0, 1, 2, 3, 4],
                run_on_holidays=True,   # Refresh even on holidays — token still expires
            ))

        # Asian markets + Gift Nifty fetch (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Asian Markets Fetch", 6, 30,
            self._task_asian_markets,
            weekdays=[0,1,2,3,4]
        ))

        # Pre-market intelligence (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Pre-Market Intelligence", 8, 0,
            self._task_premarket_intel,
            weekdays=[0,1,2,3,4]
        ))

        # Morning brief to Telegram (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Morning Brief", 8, 30,
            self._task_morning_brief,
            weekdays=[0,1,2,3,4]
        ))

        logger.info(f"[{format_ist_timestamp()}] {len(self.tasks)} tasks scheduled")

    # ── TASK IMPLEMENTATIONS ───────────────────────────────

    def _task_eod_data_download(self):
        """Download fresh candle data for all watchlist stocks after market close."""
        store   = self._modules.get("store")
        fetcher = self._modules.get("fetcher")
        wl_mgr  = self._modules.get("watchlist")
        if not store or not fetcher:
            return

        import config
        symbols = (
            wl_mgr.get_watchlist() if wl_mgr
            else config.DEFAULT_WATCHLIST
        )
        logger.info(f"[{format_ist_timestamp()}] Downloading data for {len(symbols)} symbols...")
        result = store.download_all(symbols, fetcher, intervals=["5m","15m","1h","1d"])
        logger.info(f"[{format_ist_timestamp()}] {store.get_store_summary()}")

        # Also update market calendar with today's Nifty data
        try:
            nifty_q = fetcher.get_nifty_quote()
            if nifty_q:
                from economic_calendar import get_calendar
                cal = get_calendar()
                today_events = [ev["event"] for ev in cal.get_today_events()
                                if ev["impact"] != "HOLIDAY"]
                store.log_market_day(
                    nifty_open  = float(nifty_q.get("open", 0)),
                    nifty_close = float(nifty_q.get("ltp",  0)),
                    events      = today_events,
                )
        except Exception as e:
            logger.warning(f"Market calendar update failed: {e}")

    def _task_self_learning(self):
        """Run adaptive self-learning on today's trades."""
        learner = self._modules.get("learner")
        if not learner:
            return
        updated_cfg = learner.run_learning_cycle(lookback_days=20)
        logger.info(f"[{format_ist_timestamp()}] {learner.get_learning_summary()}")

        # Send learning report to Telegram
        alerter = self._modules.get("alerter")
        if alerter:
            try:
                alerter.send_text(learner.get_learning_summary())
            except Exception:
                pass

    def _task_ai_eod_review(self):
        """Ask AI to review today's trades and extract lessons."""
        ai      = self._modules.get("ai")
        journal = self._modules.get("journal")
        if not ai or not journal:
            return

        today_trades = journal.get_today_trades()
        if not today_trades:
            logger.info(f"[{format_ist_timestamp()}] No trades today — skipping AI review")
            return

        summary  = journal.get_today_summary()
        daily_pnl = summary.get("net_pnl", 0)

        trade_dicts = []
        for t in today_trades:
            trade_dicts.append({
                "symbol":        t.symbol,
                "direction":     t.direction,
                "entry_pattern": t.entry_pattern,
                "signal_score":  t.signal_score,
                "net_pnl":       t.net_pnl,
                "outcome":       t.outcome,
                "exit_reason":   t.exit_reason,
                "session":       t.session,
                "regime":        t.regime,
            })

        analysis = ai.analyse_day_trades(
            trades          = trade_dicts,
            daily_pnl       = daily_pnl,
            market_context  = f"Date: {get_current_ist_date()}"
        )

        # Apply pattern feedback from AI to self-learner
        learner = self._modules.get("learner")
        if learner and analysis.get("pattern_feedback"):
            for pattern, score in analysis["pattern_feedback"].items():
                # AI score 0-100 maps to weight 0-1.5
                weight = max(0.0, min(score / 100 * 1.5, 1.5))
                if weight < 0.3 and score < 30:
                    weight = 0.0   # Disable if AI rates very low
                learner.config.pattern_weights[pattern] = round(weight, 2)
            learner._save_config()

        # Log key lesson to Telegram
        alerter = self._modules.get("alerter")
        if alerter and analysis.get("lessons"):
            lesson_text = (
                f"🧠 AI EOD Lesson — {get_current_ist_date()}\n"
                + "\n".join(f"• {l}" for l in analysis["lessons"][:3])
                + f"\n\n📌 Tomorrow: {analysis.get('tomorrow_focus','')}"
            )
            try:
                alerter.send_text(lesson_text)
            except Exception:
                pass

    def _task_eod_self_training(self):
        """
        EOD Self-Training: runs after market close at 4:30 PM IST Mon-Fri.

        Uses last 30 days of 5m data to walk-forward optimize ATR SL/TP,
        risk%, and minimum signal score. Saves to data/adaptive_params.json.
        Main.py reads this file every morning at 9:00 AM IST and applies
        the updated parameters for that day's live trading.
        """
        fetcher = self._modules.get("fetcher")
        alerter = self._modules.get("alerter")
        if not fetcher:
            logger.warning(f"[{format_ist_timestamp()}] EOD Self-Train: no fetcher available")
            return

        import config
        try:
            from trainer import EODSelfTrainer
            trainer = EODSelfTrainer()
            symbols = config.DEFAULT_WATCHLIST[:10]
            result  = trainer.run(
                fetcher   = fetcher,
                symbols   = symbols,
                lookback  = 30,
                n_trials  = 20,   # Fast but meaningful (Render CPU-safe)
                n_splits  = 3,
                alerter   = alerter,
            )
            logger.info(
                f"[{format_ist_timestamp()}] EOD Self-Training done: "
                f"improved={result.get('improved')} | "
                f"Sharpe={result.get('sharpe',0):.2f} | "
                f"WR={result.get('win_rate',0):.1f}%"
            )
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] EOD Self-Training failed: {e}")

    def _task_weekly_review(self):
        """Full weekly strategy review using AI (Sundays)."""
        ai      = self._modules.get("ai")
        journal = self._modules.get("journal")
        if not ai or not journal:
            return

        stats_df = journal.get_trades_last_n_days(7)
        if stats_df.empty:
            logger.info("No trades this week for review")
            return

        wins   = stats_df[stats_df["outcome"] == "WIN"]
        losses = stats_df[stats_df["outcome"] == "LOSS"]
        wr     = len(wins) / max(len(stats_df), 1) * 100
        net    = stats_df["net_pnl"].sum() if "net_pnl" in stats_df else 0

        weekly_stats = {
            "total_trades": len(stats_df),
            "win_rate":     round(wr, 1),
            "net_pnl":      round(net, 2),
            "sharpe":       0,
            "max_drawdown": 0,
        }

        pat_stats = journal.get_best_patterns(top_n=10, min_trades=3)
        review    = ai.weekly_strategy_review(weekly_stats, pat_stats)

        logger.info(f"[{format_ist_timestamp()}] Weekly Review:\n{review[:500]}")

        alerter = self._modules.get("alerter")
        if alerter:
            try:
                alerter.send_text(f"📊 Weekly Review\n{review[:1000]}")
            except Exception:
                pass

    def _task_calendar_refresh(self):
        """Refresh economic calendar from web."""
        cal = self._modules.get("calendar")
        if cal:
            cal.refresh_from_web()
        upcoming = (self._modules.get("calendar") or
                    __import__("economic_calendar", fromlist=["get_calendar"]).get_calendar())
        logger.info(f"[{format_ist_timestamp()}] {upcoming.format_upcoming_events()}")

    def _task_us_snapshot(self):
        """Log US market opening direction (9:30 PM IST = 9:00 AM ET)."""
        try:
            import yfinance as yf
            spy = yf.download("SPY", period="1d", interval="5m", progress=False, auto_adjust=True)
            if not spy.empty:
                latest = float(spy["Close"].iloc[-1])
                first  = float(spy["Close"].iloc[0])
                chg    = (latest - first) / first * 100
                logger.info(
                    f"[{format_ist_timestamp()}] US snapshot: "
                    f"SPY {chg:+.2f}% intraday"
                )
        except Exception as e:
            logger.debug(f"US snapshot failed: {e}")

    def _task_overnight_analysis(self):
        """Full overnight analysis after US markets close."""
        overnight = self._modules.get("overnight")
        ai        = self._modules.get("ai")
        if overnight:
            result = overnight.run(ai_brain=ai)
            logger.info(
                f"[{format_ist_timestamp()}] Overnight done: "
                f"bias={result.get('day_bias','?')} "
                f"score={result.get('bias_score',0):+d}"
            )

    def _task_asian_markets(self):
        """Refresh Asian market data (runs at 6:30 AM IST after Asian open)."""
        overnight = self._modules.get("overnight")
        if overnight:
            overnight._fetch_gift_nifty()
            logger.info(f"[{format_ist_timestamp()}] Asian markets fetched")

    def _task_premarket_intel(self):
        """Full pre-market intelligence combining all overnight data."""
        overnight = self._modules.get("overnight")
        ai        = self._modules.get("ai")
        if not overnight:
            return
        # Re-run to get latest Gift Nifty (if not run overnight)
        if not overnight._today_analysis:
            overnight.run(ai_brain=ai)
        logger.info(
            f"[{format_ist_timestamp()}] Pre-market intel ready: "
            f"bias={overnight.get_day_bias()} "
            f"gap={overnight.get_gap_pct():+.2f}%"
        )

    def _task_morning_brief(self):
        """Send morning brief to Telegram with full day plan."""
        overnight = self._modules.get("overnight")
        alerter   = self._modules.get("alerter")
        calendar  = self._modules.get("calendar")

        if not overnight or not alerter:
            return

        brief = overnight.format_morning_brief()

        if calendar:
            events_text = calendar.format_upcoming_events()
            brief      += f"\n\n{events_text}"

        # Check for today's high-impact events
        if calendar:
            today_events = calendar.get_today_events()
            high_impact  = [e for e in today_events if e["impact"] in ("HIGH", "EXTREME")]
            if high_impact:
                brief += (
                    f"\n\n🚨 HIGH IMPACT EVENTS TODAY:\n"
                    + "\n".join(f"  • {e['event']} @ {e['time_ist']} IST" for e in high_impact)
                )
                brief += "\n⚠️ REDUCE POSITION SIZES 50% near these events!"

        try:
            alerter.send_text(brief)
        except Exception as e:
            logger.warning(f"Morning brief send failed: {e}")

        logger.info(f"[{format_ist_timestamp()}] Morning brief sent")

    def _task_token_refresh(self):
        """
        Groww Cloud token refresh cascade — runs at 6:05, 6:20, 6:40, 7:00, 8:00 AM IST.
        Groww Cloud API keys reset at 6 AM IST daily.
        As soon as one attempt succeeds, all later cascade attempts are skipped.
        """
        today = str(get_current_ist_date())
        if self._token_refreshed_date == today:
            logger.info(f"[{format_ist_timestamp()}] Token already refreshed today — cascade skip")
            return

        alerter = self._modules.get("alerter")

        try:
            from auth_groww import get_auth_manager
            mgr = get_auth_manager()
            old_token = mgr.token or ""

            logger.info(f"[{format_ist_timestamp()}] Token refresh cascade attempt...")
            success = mgr.refresh_token_if_needed()
            new_token = mgr.token or ""

            if success and new_token and new_token != old_token:
                # Token genuinely changed — propagate to fetcher + executor
                self._token_refreshed_date = today
                logger.info(f"[{format_ist_timestamp()}] ✅ Groww token refreshed (cascade success)")

                # Kick the data-fetcher singleton so it picks up the new token
                try:
                    from data_fetch_groww import get_data_fetcher
                    fetcher = get_data_fetcher()
                    if hasattr(fetcher, "_refresh_api_if_needed"):
                        fetcher._refresh_api_if_needed()
                except Exception as e:
                    logger.debug(f"Fetcher token propagation: {e}")

                if alerter:
                    try:
                        alerter.send_html(
                            f"🔑 <b>Groww Token Refreshed</b> — {format_ist_timestamp()}\n"
                            f"Ready for today's trading session."
                        )
                    except Exception:
                        pass

            elif success and new_token == old_token:
                # Same token returned — may be using cached env token; still mark success
                self._token_refreshed_date = today
                logger.info(
                    f"[{format_ist_timestamp()}] Token refresh returned same token "
                    f"(env fallback). Cascade will not retry."
                )
            else:
                logger.warning(
                    f"[{format_ist_timestamp()}] Token refresh attempt failed — "
                    f"next cascade attempt will retry."
                )
                if alerter:
                    try:
                        alerter.send_html(
                            f"⚠️ <b>Token Refresh Failed</b> — {format_ist_timestamp()}\n"
                            f"Will retry. Check GROWW_TOTP_SECRET in Render env."
                        )
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Token refresh task error: {e}")

    # ── MAIN SCHEDULER LOOP ────────────────────────────────

    def start(self, blocking: bool = True):
        """
        Start the continuous learning scheduler.
        blocking=True: runs in foreground (standalone mode)
        blocking=False: runs in background thread
        """
        self._load_modules()
        self._register_tasks()
        self.running = True

        logger.info(
            f"[{format_ist_timestamp()}] Continuous Learner started. "
            f"{len(self.tasks)} tasks registered. "
            f"Checking every 60s."
        )

        if not blocking:
            t = threading.Thread(target=self._loop, daemon=True, name="ContinuousLearner")
            t.start()
            return t

        try:
            self._loop()
        except KeyboardInterrupt:
            logger.info(f"[{format_ist_timestamp()}] Continuous Learner stopped")
        finally:
            self.running = False

    def _loop(self):
        """Main scheduler loop — checks tasks every 60 seconds."""
        while self.running:
            now_ist = get_current_ist_time()
            for task in self.tasks:
                if task.is_due(now_ist):
                    task.run()
            time.sleep(60)

    def stop(self):
        self.running = False

    def get_status(self) -> str:
        lines = [f"🤖 Continuous Learner Status — {format_ist_timestamp()}"]
        for task in self.tasks:
            status = f"Last: {task.last_run_date or 'never'} | Runs: {task.run_count}"
            if task.last_error:
                status += f" | ⚠️ {task.last_error[:40]}"
            lines.append(f"  • {task.name:30s} {status}")
        return "\n".join(lines)

    def run_task_now(self, task_name: str) -> bool:
        """Manually trigger a specific task by name."""
        for task in self.tasks:
            if task.name.lower() == task_name.lower():
                task.run()
                return True
        return False


# ── STANDALONE ENTRY POINT ─────────────────────────────────────────────────

def main():
    import config
    setup_logging(config.LOG_DIR, config.LOG_LEVEL)
    logger.info("=" * 55)
    logger.info("  NSE BOT — Continuous Learner (24/7 Mode)")
    logger.info(f"  IST: {format_ist_timestamp()}")
    logger.info("=" * 55)

    learner = ContinuousLearner()

    def handle_sig(sig, _frame):
        logger.info(f"Signal {sig} — shutting down learner")
        learner.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT,  handle_sig)

    learner.start(blocking=True)


if __name__ == "__main__":
    main()


# ============================================================
# MODULE: ai_brain.py
# ============================================================
"""
ai_brain.py — NSE Momentum Groww AI Bot
Central AI Intelligence Engine — Powered by Gemini API

This is the "thinking" layer of the bot. It uses Gemini AI to:
1. Analyse market patterns and generate human-like trading insight
2. Review each day's trades and extract lessons
3. Synthesise news + price action + indicators into a daily market thesis
4. Suggest strategy improvements based on changing market conditions
5. Explain EVERY signal in plain English (why this trade, why now)
6. Learn from 18 years of encoded trading rules + live performance data
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import google.genai as genai
from google.genai import types as genai_types
from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)

AI_INSIGHTS_DIR = Path("logs/ai_insights")
AI_INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)

# 18 years of trading wisdom — the permanent knowledge base
TRADING_WISDOM = """
You are an expert NSE intraday momentum trader with 18+ years of experience since 2008.
You have traded through: 2008 financial crisis, 2009 recovery, 2011 correction,
2016 demonetization, 2020 COVID crash and recovery, 2022 bear market, 2024 bull run.

Your core principles (never compromise these):
1. CAPITAL PRESERVATION FIRST. A 10% loss needs 11% gain to recover. A 50% loss needs 100%.
2. Trade WITH the trend. The trend is your best friend. Never fight it.
3. Volume confirms everything. Price without volume is a lie.
4. VWAP is the institutional anchor. Above = bullish bias. Below = bearish bias.
5. The opening 45 minutes (9:15-10:00 AM IST) has 40% of daily volume and best momentum.
6. NEVER trade during 11 AM - 1 PM IST. This is the chop zone. Professionals take lunch.
7. After 3 consecutive losses, stop trading and review. The market is telling you something.
8. Position sizing is more important than entry price. A great entry with 10% size = mediocre return.
9. Take partial profits at T1 (50%). Let winners run with trailing stops.
10. Every losing trade is a tuition fee. Learn from it. Don't repeat the same mistake.

NSE-specific knowledge:
- FII (Foreign Institutional Investors) dominate price direction. Watch their flows daily.
- DIIs (Domestic) often buy on dips. Their buying = support.
- Nifty50 direction sets the tone for 80% of stocks.
- Earnings season (Apr, Jul, Oct, Jan) = higher volatility. Widen stops.
- RBI policy days: DO NOT TRADE 30 min before announcement.
- Budget day: THE most volatile day of the year. Only trade after 12 PM post clarity.
- F&O expiry (last Thursday of month): high manipulation. Avoid if unsure.
- SGX Nifty (now Gift Nifty) pre-market futures indicate opening direction.
- Global cues: US market close, Asian markets open — both matter for NSE opening.
"""


class AIBrain:
    """
    Gemini-powered AI brain for market analysis and continuous learning.
    Runs during off-market hours and provides insights before market opens.
    """

    def __init__(self):
        self._client = None
        self._model  = "gemini-2.0-flash"
        self._enabled = bool(os.getenv("GEMINI_API_KEY"))
        if not self._enabled:
            logger.warning(
                f"[{format_ist_timestamp()}] GEMINI_API_KEY not set — "
                "AI brain running in rule-based mode only"
            )
        else:
            self._init_client()

    def _init_client(self):
        try:
            self._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            logger.info(f"[{format_ist_timestamp()}] AI Brain (Gemini) initialized")
        except Exception as e:
            logger.error(f"AI Brain init failed: {e}")

    def _ask(self, prompt: str, max_tokens: int = 1024) -> str:
        """Send a prompt to Gemini and get response."""
        if not self._client:
            return "[AI Brain offline — set GEMINI_API_KEY]"
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=TRADING_WISDOM,
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                )
            )
            return response.text
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] AI Brain query failed: {e}")
            return f"[AI query failed: {e}]"

    # --------------------------------------------------------
    # DAILY MARKET THESIS (runs at 8:30 AM IST)
    # --------------------------------------------------------

    def generate_morning_thesis(
        self,
        nifty_prev_close: float,
        gift_nifty:       float,
        us_market_change: float,
        asian_markets:    Dict[str, float],
        top_news:         List[str],
        economic_events:  List[str],
    ) -> Dict:
        """
        Generate today's trading thesis before market opens.
        Called at 8:30 AM IST — before the TOTP login.

        Returns: {bias, key_levels, sectors_to_watch, avoid_list, thesis_text}
        """
        gap_pct = ((gift_nifty - nifty_prev_close) / nifty_prev_close * 100) if nifty_prev_close else 0
        asian_summary = ", ".join(f"{k}: {v:+.1f}%" for k, v in asian_markets.items())
        news_text     = "\n".join(f"- {n}" for n in top_news[:5])
        events_text   = "\n".join(f"- {e}" for e in economic_events[:3]) or "None today"

        prompt = f"""
Today is {get_current_ist_date()} (IST). NSE opens in ~45 minutes.

OVERNIGHT DATA:
- Nifty50 prev close: {nifty_prev_close:.0f}
- Gift Nifty futures: {gift_nifty:.0f} (implied gap: {gap_pct:+.1f}%)
- US markets last close: {us_market_change:+.1f}%
- Asian markets: {asian_summary}

TOP NEWS:
{news_text}

ECONOMIC EVENTS TODAY:
{events_text}

Based on your 18 years of NSE trading experience, provide:
1. MARKET BIAS (BULLISH/BEARISH/NEUTRAL) and confidence (0-100)
2. NIFTY KEY LEVELS to watch (support and resistance)
3. TOP 3 SECTORS likely to outperform today
4. TOP 3 SECTORS to avoid today
5. OPENING STRATEGY (what to do in first 15 minutes)
6. ANY RED FLAGS (reasons to trade cautiously today)
7. ONE-LINE THESIS for the day

Format as JSON with keys: bias, confidence, support, resistance,
sectors_buy, sectors_avoid, opening_strategy, red_flags, thesis
"""
        response = self._ask(prompt, max_tokens=800)

        # Try to parse JSON, fall back to text
        try:
            # Extract JSON block if wrapped
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]
            result = json.loads(response.strip())
        except Exception:
            result = {
                "bias":             "NEUTRAL",
                "confidence":       50,
                "thesis":           response[:300],
                "opening_strategy": "Wait for direction after first 15 min",
                "red_flags":        [],
                "sectors_buy":      [],
                "sectors_avoid":    [],
            }

        result["generated_at"] = format_ist_timestamp()
        result["gap_pct"]      = round(gap_pct, 2)
        self._save_insight("morning_thesis", result)
        logger.info(f"[{format_ist_timestamp()}] Morning thesis: {result.get('bias','?')} | {result.get('thesis','')[:100]}")
        return result

    # --------------------------------------------------------
    # SIGNAL EXPLANATION (runs before every trade)
    # --------------------------------------------------------

    def explain_signal(
        self,
        symbol:    str,
        direction: str,
        patterns:  List[str],
        score:     float,
        regime:    str,
        mtf_info:  str,
        rsi:       float,
        vwap_pos:  str,
        volume_ratio: float,
    ) -> str:
        """
        Generate a plain-English explanation of WHY this trade.
        Sent with every Telegram alert so you understand each trade.
        """
        prompt = f"""
A trading signal has been generated. Explain it like a professional trader would:

Symbol: {symbol}
Direction: {direction}
Patterns detected: {', '.join(patterns)}
Signal confidence: {score:.0f}/100
Market regime: {regime}
Multi-timeframe: {mtf_info}
RSI: {rsi:.0f}
Price vs VWAP: {vwap_pos}
Volume ratio: {volume_ratio:.1f}x average

In 3-4 sentences, explain:
1. WHY this is a good setup (the edge)
2. WHAT could go wrong (the risk)
3. ONE key thing to watch during this trade

Be direct and specific. Use the mindset of an 18-year NSE veteran.
"""
        return self._ask(prompt, max_tokens=300)

    # --------------------------------------------------------
    # EOD LEARNING (runs after market close)
    # --------------------------------------------------------

    def analyse_day_trades(self, trades: List[Dict], daily_pnl: float, market_context: str) -> Dict:
        """
        Review the day's trades and extract lessons.
        Called at EOD, results fed into self_learning.py.
        """
        if not trades:
            return {"lessons": [], "pattern_feedback": {}, "summary": "No trades today."}

        trade_summary = "\n".join([
            f"- {t.get('symbol','?')} {t.get('direction','?')}: "
            f"Pattern={t.get('entry_pattern','?')} "
            f"Score={t.get('signal_score',0):.0f} "
            f"PnL=₹{t.get('net_pnl',0):+.0f} "
            f"({t.get('outcome','?')}) "
            f"Exit={t.get('exit_reason','?')}"
            for t in trades[:20]
        ])

        prompt = f"""
Today's NSE intraday trading session is complete.
Date: {get_current_ist_date()} (IST)
Total P&L: ₹{daily_pnl:+.0f}
Market context: {market_context}

TRADES TODAY:
{trade_summary}

As an 18-year NSE veteran, analyse these trades and provide:
1. WHAT WORKED: Which patterns/setups produced profits? Why?
2. WHAT FAILED: Which losses were avoidable? What was the mistake?
3. MARKET LESSON: What did the market teach today?
4. TOMORROW'S EDGE: Based on today, what to focus on tomorrow?
5. PATTERN_SCORES: Rate each pattern that appeared (0-100 confidence for tomorrow)
6. ONE RULE: One specific rule to add/change based on today

Format as JSON: lessons (list), pattern_feedback (dict name->score), 
tomorrow_focus (string), rule_change (string), summary (string)
"""
        response = self._ask(prompt, max_tokens=1000)
        try:
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]
            result = json.loads(response.strip())
        except Exception:
            result = {
                "lessons":         [response[:400]],
                "pattern_feedback": {},
                "tomorrow_focus":   "Review failed signals",
                "rule_change":      "None identified",
                "summary":          response[:200],
            }

        result["date_ist"]     = str(get_current_ist_date())
        result["daily_pnl"]    = daily_pnl
        result["trade_count"]  = len(trades)
        self._save_insight("eod_analysis", result)
        return result

    # --------------------------------------------------------
    # WEEKLY STRATEGY REVIEW (runs every Sunday)
    # --------------------------------------------------------

    def weekly_strategy_review(self, weekly_stats: Dict, pattern_stats: List[Dict]) -> str:
        """
        Deep weekly review of strategy performance.
        Generates actionable improvements for next week.
        """
        pat_text = "\n".join([
            f"  {p['pattern']}: {p['win_rate']:.0f}% WR, {p['trades']} trades, avg ₹{p['avg_pnl']:+.0f}"
            for p in pattern_stats[:10]
        ])

        prompt = f"""
Weekly NSE trading strategy review — Week ending {get_current_ist_date()}

WEEKLY STATS:
- Total trades: {weekly_stats.get('total_trades', 0)}
- Win rate: {weekly_stats.get('win_rate', 0):.1f}%
- Net P&L: ₹{weekly_stats.get('net_pnl', 0):+.0f}
- Sharpe: {weekly_stats.get('sharpe', 0):.2f}
- Max drawdown: {weekly_stats.get('max_drawdown', 0):.1f}%

PATTERN PERFORMANCE:
{pat_text}

As an 18-year NSE veteran, provide a weekly review:
1. STRATEGY HEALTH (is it working? Why/why not?)
2. TOP 3 IMPROVEMENTS for next week
3. PATTERNS TO INCREASE SIZE on (high performers)
4. PATTERNS TO STOP TRADING (chronic losers)
5. MARKET CONDITION ASSESSMENT (trending/ranging? What's next week likely to bring?)
6. RISK ADJUSTMENT (should we increase/decrease position size next week?)

Be specific and actionable. This is real money.
"""
        review = self._ask(prompt, max_tokens=1200)
        self._save_insight("weekly_review", {"review": review, "stats": weekly_stats})
        return review

    # --------------------------------------------------------
    # MARKET PATTERN LEARNING (runs nightly)
    # --------------------------------------------------------

    def learn_new_patterns(self, recent_data_summary: str) -> List[Dict]:
        """
        Ask AI to identify any new patterns or market behaviours from recent data.
        This is how the bot stays up-to-date with evolving market conditions.
        """
        prompt = f"""
Analyse recent NSE market data and identify any patterns or behaviours that
a momentum intraday trader should know about:

RECENT MARKET DATA SUMMARY:
{recent_data_summary}

Identify:
1. Any NEW patterns emerging (not in standard playbooks)
2. TIME-OF-DAY anomalies (e.g., is 2 PM rally happening consistently?)
3. SECTOR ROTATION patterns
4. VOLUME PROFILE changes (is morning or afternoon volume dominant?)
5. Any GLOBAL CORRELATION shifts (is US correlation higher/lower than usual?)

For each finding, rate confidence (0-100) and tradeable impact (HIGH/MEDIUM/LOW).
Format as JSON list of: {{finding, type, confidence, impact, action}}
"""
        response = self._ask(prompt, max_tokens=800)
        try:
            if "```" in response:
                response = response.split("```")[1].split("```")[0]
                if response.startswith("json"):
                    response = response[4:]
            findings = json.loads(response.strip())
            if isinstance(findings, list):
                self._save_insight("pattern_discoveries", {"findings": findings})
                return findings
        except Exception:
            pass
        return []

    # --------------------------------------------------------
    # STOCK-SPECIFIC ANALYSIS
    # --------------------------------------------------------

    def analyse_stock(self, symbol: str, technicals: Dict, news: List[str], sector_trend: str) -> str:
        """Quick AI assessment of a stock before adding to watchlist."""
        news_text = "\n".join(f"- {n}" for n in news[:3]) or "No recent news"
        prompt = f"""
Quick pre-trade assessment for NSE intraday trading:

Stock: {symbol}
Sector trend: {sector_trend}
Technical snapshot:
- RSI: {technicals.get('rsi', 50):.0f}
- MACD: {'bullish' if technicals.get('macd_hist', 0) > 0 else 'bearish'}
- Volume ratio: {technicals.get('volume_ratio', 1):.1f}x
- vs VWAP: {'above' if technicals.get('above_vwap', False) else 'below'}
- ATR%: {technicals.get('atr_pct', 0.3):.2f}%

Recent news:
{news_text}

In 2 sentences: Is this stock worth watching today for momentum trading? 
Flag any red flags. Be direct.
"""
        return self._ask(prompt, max_tokens=150)

    # --------------------------------------------------------
    # PERSISTENCE
    # --------------------------------------------------------

    def _save_insight(self, category: str, data: Dict):
        """Save AI insight to disk for audit and future learning."""
        today = get_current_ist_date()
        path  = AI_INSIGHTS_DIR / f"{category}_{today}.json"
        try:
            existing = []
            if path.exists():
                with open(path) as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
            existing.append(data)
            with open(path, "w") as f:
                json.dump(existing, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Could not save insight: {e}")

    def get_today_thesis(self) -> Optional[Dict]:
        """Load today's morning thesis if it exists."""
        today = get_current_ist_date()
        path  = AI_INSIGHTS_DIR / f"morning_thesis_{today}.json"
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                return data[-1] if isinstance(data, list) else data
            except Exception:
                pass
        return None


# Singleton
_brain: Optional["AIBrain"] = None

def get_ai_brain() -> "AIBrain":
    global _brain
    if _brain is None:
        _brain = AIBrain()
    return _brain


# ============================================================
# MODULE: market_regime.py
# ============================================================
"""
market_regime.py — NSE Momentum Groww AI Bot
Market Regime Detection Engine — 18+ Years of Trading Experience

⚠️ MOST IMPORTANT LESSON FROM 18 YEARS:
"The strategy that works in trending markets DESTROYS capital in ranging markets.
 Detect the regime FIRST, then apply the right strategy."

Regimes:
  STRONG_TREND_UP   — Strong directional move, ride momentum hard
  STRONG_TREND_DOWN — Strong directional move, short heavily
  WEAK_TREND_UP     — Mild uptrend, lighter positions
  WEAK_TREND_DOWN   — Mild downtrend, lighter positions
  RANGING           — Chop/consolidation — AVOID breakouts, fade extremes
  HIGH_VOLATILITY   — Explosive moves, wider SL, smaller size
  LOW_VOLATILITY    — Squeeze building — wait for breakout
  OPENING_DRIVE     — 9:15–10:00 AM: highest momentum window of day
  MIDDAY_CHOP       — 11:00 AM–1:00 PM: lowest signal quality — reduce size
  AFTERNOON_TREND   — 1:30–3:00 PM: institutional activity resumes

Each regime has:
  - Optimal strategy (momentum / mean-reversion / avoid)
  - Position size multiplier (scale up in ideal conditions)
  - Preferred patterns
  - Risk multiplier
"""

import logging
from dataclasses import dataclass
from datetime import time
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class RegimeState:
    """Complete market regime description with trading guidance."""
    regime: str                    # e.g., "STRONG_TREND_UP"
    confidence: float              # 0–100
    strategy: str                  # "MOMENTUM" / "MEAN_REVERSION" / "AVOID"
    size_multiplier: float         # Scale position size (0.0–1.5)
    risk_multiplier: float         # Scale SL width
    preferred_direction: str       # "LONG" / "SHORT" / "BOTH" / "NONE"
    description: str
    adx: float = 0.0
    bb_width: float = 0.0
    atr_pct: float = 0.0
    trend_strength: float = 0.0
    session: str = ""              # Current trading session

    @property
    def is_tradeable(self) -> bool:
        return self.strategy != "AVOID" and self.size_multiplier > 0

    @property
    def emoji(self) -> str:
        mapping = {
            "STRONG_TREND_UP":   "🚀",
            "STRONG_TREND_DOWN": "💣",
            "WEAK_TREND_UP":     "📈",
            "WEAK_TREND_DOWN":   "📉",
            "RANGING":           "↔️",
            "HIGH_VOLATILITY":   "⚡",
            "LOW_VOLATILITY":    "😴",
            "OPENING_DRIVE":     "🔥",
            "MIDDAY_CHOP":       "⚠️",
            "AFTERNOON_TREND":   "🌅",
        }
        return mapping.get(self.regime, "📊")


# Session windows (IST)
OPENING_DRIVE_START = time(9, 15)
OPENING_DRIVE_END   = time(10, 0)
MORNING_END         = time(11, 0)
MIDDAY_START        = time(11, 0)
MIDDAY_END          = time(13, 0)
AFTERNOON_START     = time(13, 30)
AFTERNOON_END       = time(15, 0)
EOD_START           = time(15, 0)


class MarketRegimeDetector:
    """
    Detects current market regime from multiple data sources.

    18-year rule: "Trade WITH the regime, not against it.
    A good trader doesn't fight the market — they read it."
    """

    def __init__(self):
        self._nifty_regime: Optional[RegimeState] = None
        self._last_regime_time: Optional[float] = None
        self._regime_cache_ttl = 300  # Refresh every 5 minutes

    # --------------------------------------------------------
    # MAIN DETECTION
    # --------------------------------------------------------

    def detect(self, df: pd.DataFrame, nifty_df: Optional[pd.DataFrame] = None) -> RegimeState:
        """
        Detect the current market regime from price data.

        Args:
            df: 5-min OHLCV DataFrame (with indicators already computed)
            nifty_df: Nifty50 DataFrame for macro context

        Returns:
            RegimeState with complete trading guidance
        """
        if df is None or len(df) < 30:
            return self._default_regime()

        session = self._get_current_session()

        # Extract key metrics
        adx         = self._get_adx(df)
        bb_width    = self._get_bb_width(df)
        atr_pct     = self._get_atr_pct(df)
        trend_str   = self._get_trend_strength(df)
        trend_dir   = self._get_trend_direction(df)
        volatility  = self._classify_volatility(df, atr_pct)

        # Opening drive (9:15–10:00 AM) — highest momentum session
        if session == "OPENING_DRIVE":
            return RegimeState(
                regime="OPENING_DRIVE",
                confidence=85,
                strategy="MOMENTUM",
                size_multiplier=1.2,       # Bigger size in opening drive
                risk_multiplier=1.0,
                preferred_direction=trend_dir,
                description="Opening drive — highest momentum window. Trade breakouts aggressively.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # Midday chop (11 AM–1 PM) — avoid or reduce heavily
        if session == "MIDDAY_CHOP":
            return RegimeState(
                regime="MIDDAY_CHOP",
                confidence=80,
                strategy="AVOID",
                size_multiplier=0.3,       # Very small size or skip
                risk_multiplier=0.8,
                preferred_direction="NONE",
                description="Midday chop (11 AM–1 PM IST). Low signal quality. Reduce size 70% or skip.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # EOD — reduce risk
        if session == "EOD":
            return RegimeState(
                regime="RANGING",
                confidence=70,
                strategy="AVOID",
                size_multiplier=0.0,
                risk_multiplier=0.5,
                preferred_direction="NONE",
                description="EOD (3 PM+). No new entries. Close all positions by 3:20 PM IST.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # High volatility — explosive moves (earnings, news, events)
        if atr_pct > 0.8:
            return RegimeState(
                regime="HIGH_VOLATILITY",
                confidence=75,
                strategy="MOMENTUM",
                size_multiplier=0.6,       # Smaller size with wider SL
                risk_multiplier=1.5,       # Wider stops needed
                preferred_direction=trend_dir,
                description=f"High volatility regime (ATR {atr_pct:.2f}%). Wider SL, smaller size.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # Low volatility / squeeze — wait for breakout
        if bb_width < 0.02 and atr_pct < 0.2:
            return RegimeState(
                regime="LOW_VOLATILITY",
                confidence=70,
                strategy="AVOID",
                size_multiplier=0.4,
                risk_multiplier=0.8,
                preferred_direction="BOTH",
                description="Volatility squeeze. BB tight. Wait for explosive breakout then enter.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # Strong trend (ADX > 30)
        if adx >= 30:
            if trend_dir == "LONG":
                return RegimeState(
                    regime="STRONG_TREND_UP",
                    confidence=min(50 + adx, 95),
                    strategy="MOMENTUM",
                    size_multiplier=1.2,
                    risk_multiplier=1.0,
                    preferred_direction="LONG",
                    description=f"Strong uptrend. ADX={adx:.0f}. Trade LONGs with momentum. Avoid shorts.",
                    adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                    trend_strength=trend_str, session=session
                )
            else:
                return RegimeState(
                    regime="STRONG_TREND_DOWN",
                    confidence=min(50 + adx, 95),
                    strategy="MOMENTUM",
                    size_multiplier=1.2,
                    risk_multiplier=1.0,
                    preferred_direction="SHORT",
                    description=f"Strong downtrend. ADX={adx:.0f}. Trade SHORTs with momentum. Avoid longs.",
                    adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                    trend_strength=trend_str, session=session
                )

        # Weak trend (ADX 20–30)
        if 20 <= adx < 30:
            if trend_dir == "LONG":
                return RegimeState(
                    regime="WEAK_TREND_UP",
                    confidence=60,
                    strategy="MOMENTUM",
                    size_multiplier=0.8,
                    risk_multiplier=1.0,
                    preferred_direction="LONG",
                    description=f"Mild uptrend. ADX={adx:.0f}. Prefer longs but tighten targets.",
                    adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                    trend_strength=trend_str, session=session
                )
            else:
                return RegimeState(
                    regime="WEAK_TREND_DOWN",
                    confidence=60,
                    strategy="MOMENTUM",
                    size_multiplier=0.8,
                    risk_multiplier=1.0,
                    preferred_direction="SHORT",
                    description=f"Mild downtrend. ADX={adx:.0f}. Prefer shorts.",
                    adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                    trend_strength=trend_str, session=session
                )

        # Ranging (ADX < 20)
        return RegimeState(
            regime="RANGING",
            confidence=65,
            strategy="AVOID",
            size_multiplier=0.4,
            risk_multiplier=0.7,
            preferred_direction="BOTH",
            description=f"Ranging/choppy market. ADX={adx:.0f}. Skip momentum signals. Fade extremes only.",
            adx=adx, bb_width=bb_width, atr_pct=atr_pct,
            trend_strength=trend_str, session=session
        )

    def detect_nifty_regime(self, nifty_df: pd.DataFrame) -> RegimeState:
        """Detect Nifty50 macro regime — used to filter stock signals."""
        return self.detect(nifty_df)

    # --------------------------------------------------------
    # SESSION DETECTION (IST)
    # --------------------------------------------------------

    def _get_current_session(self) -> str:
        now_ist = get_current_ist_time()
        t = now_ist.time()
        if OPENING_DRIVE_START <= t < OPENING_DRIVE_END:
            return "OPENING_DRIVE"
        elif OPENING_DRIVE_END <= t < MIDDAY_START:
            return "MORNING"
        elif MIDDAY_START <= t < MIDDAY_END:
            return "MIDDAY_CHOP"
        elif AFTERNOON_START <= t < AFTERNOON_END:
            return "AFTERNOON_TREND"
        elif t >= EOD_START:
            return "EOD"
        return "MORNING"

    # --------------------------------------------------------
    # METRIC EXTRACTORS
    # --------------------------------------------------------

    def _get_adx(self, df: pd.DataFrame) -> float:
        if "adx" in df.columns:
            val = df["adx"].iloc[-1]
            return float(val) if not pd.isna(val) else 20.0
        return 20.0

    def _get_bb_width(self, df: pd.DataFrame) -> float:
        if "bb_width" in df.columns:
            val = df["bb_width"].iloc[-1]
            return float(val) if not pd.isna(val) else 0.03
        return 0.03

    def _get_atr_pct(self, df: pd.DataFrame) -> float:
        """ATR as percentage of price."""
        if "atr" in df.columns:
            atr = df["atr"].iloc[-1]
            close = df["close"].iloc[-1]
            if close > 0:
                return float(atr / close * 100)
        return 0.3

    def _get_trend_strength(self, df: pd.DataFrame) -> float:
        """0–100 trend strength score."""
        adx = self._get_adx(df)
        return min(adx * 2, 100)

    def _get_trend_direction(self, df: pd.DataFrame) -> str:
        """Determine trend direction from EMAs and price."""
        try:
            row = df.iloc[-1]
            ema9  = float(row.get("ema9", 0))
            ema21 = float(row.get("ema21", 0))
            ema50 = float(row.get("ema50", 0))
            close = float(row.get("close", 0))
            plus_di  = float(row.get("plus_di", 0))
            minus_di = float(row.get("minus_di", 0))

            bullish_points = 0
            if ema9 > ema21:
                bullish_points += 1
            if ema21 > ema50:
                bullish_points += 1
            if close > ema9:
                bullish_points += 1
            if plus_di > minus_di:
                bullish_points += 1

            return "LONG" if bullish_points >= 3 else "SHORT"
        except Exception:
            return "LONG"

    def _classify_volatility(self, df: pd.DataFrame, atr_pct: float) -> str:
        if atr_pct > 0.8:
            return "HIGH"
        elif atr_pct < 0.2:
            return "LOW"
        return "NORMAL"

    def _default_regime(self) -> RegimeState:
        return RegimeState(
            regime="RANGING",
            confidence=30,
            strategy="AVOID",
            size_multiplier=0.5,
            risk_multiplier=1.0,
            preferred_direction="BOTH",
            description="Insufficient data for regime detection.",
            session=self._get_current_session()
        )

    # --------------------------------------------------------
    # GAP ANALYSIS (at 9:15 AM IST open)
    # --------------------------------------------------------

    def analyze_gap(self, prev_close: float, today_open: float) -> Dict:
        """
        Analyze opening gap vs previous close.
        18yr rule: "Gap direction sets the day's bias."

        Gap > 0.5%: Bullish gap — buy dips in first hour
        Gap < -0.5%: Bearish gap — sell rallies in first hour
        Gap > 2%: Huge gap — fade after 30-min equilibrium
        """
        if prev_close <= 0:
            return {"type": "NO_GAP", "pct": 0, "bias": "NEUTRAL"}

        gap_pct = ((today_open - prev_close) / prev_close) * 100

        if gap_pct >= 2.0:
            return {
                "type": "HUGE_GAP_UP", "pct": gap_pct,
                "bias": "FADE_AFTER_BALANCE",
                "note": "Gap >2%: Wait 30min, then fade if market reverses. Don't chase."
            }
        elif gap_pct >= 0.5:
            return {
                "type": "GAP_UP", "pct": gap_pct,
                "bias": "BULLISH",
                "note": f"Gap up {gap_pct:.1f}%: Buy pullbacks toward VWAP in first hour."
            }
        elif gap_pct <= -2.0:
            return {
                "type": "HUGE_GAP_DOWN", "pct": gap_pct,
                "bias": "FADE_AFTER_BALANCE",
                "note": "Gap down >2%: Wait 30min equilibrium, then trade direction."
            }
        elif gap_pct <= -0.5:
            return {
                "type": "GAP_DOWN", "pct": gap_pct,
                "bias": "BEARISH",
                "note": f"Gap down {gap_pct:.1f}%: Short rallies toward VWAP in first hour."
            }
        else:
            return {
                "type": "FLAT_OPEN", "pct": gap_pct,
                "bias": "NEUTRAL",
                "note": "Flat open: Wait for breakout direction above/below ORB."
            }

    # --------------------------------------------------------
    # ADR (Average Daily Range) FILTER
    # --------------------------------------------------------

    def get_adr_filter(self, df_daily: pd.DataFrame, adr_days: int = 20) -> Dict:
        """
        Calculate Average Daily Range.
        18yr rule: "Only trade stocks that MOVE. Min 1.5% ADR for intraday."
        """
        if df_daily is None or len(df_daily) < adr_days:
            return {"adr_pct": 0, "tradeable": True}

        recent = df_daily.tail(adr_days)
        daily_ranges = (recent["high"] - recent["low"]) / recent["close"] * 100
        adr_pct = daily_ranges.mean()

        return {
            "adr_pct": round(adr_pct, 2),
            "tradeable": adr_pct >= 1.5,
            "note": f"ADR={adr_pct:.1f}% {'✅ sufficient' if adr_pct >= 1.5 else '❌ too low for intraday'}"
        }


# ============================================================
# MODULE: high_accuracy_filter.py
# ============================================================
"""
high_accuracy_filter.py — NSE Momentum Groww AI Bot
High-Accuracy Signal Gate — Targets 70-80% Win Rate

18yr Truth: "Most traders fail because they take EVERY signal.
The 70%+ win rate comes from taking ONLY THE BEST 20% of signals.
Patience is your edge. Waiting IS the strategy."

This filter sits BETWEEN signal_generator and execution.
A signal must PASS ALL 10 gates to become a trade.

THE 10 CONFLUENCE GATES:
  Gate 1:  POWER HOURS ONLY    — Trade only in high-probability time windows
  Gate 2:  REGIME ALIGNMENT    — Market regime must be MOMENTUM (not RANGING)
  Gate 3:  MULTI-TF ALIGNMENT  — At least 2 of 3 timeframes must agree
  Gate 4:  VOLUME SURGE        — Current volume must be ≥ 1.8x 20-period SMA
  Gate 5:  PATTERN QUALITY     — Pattern confidence score ≥ 72/100
  Gate 6:  LIQUIDITY           — Min daily volume ≥ 5 lakh shares (no illiquid stocks)
  Gate 7:  CIRCUIT BREAKER     — Stock not near 5/10/20% NSE circuit bands
  Gate 8:  GAP RISK            — No extreme gap open; wait window respected
  Gate 9:  CORP ACTIONS        — No ex-dividend/bonus/split within 2 days
  Gate 10: F&O SHORT ELIGIBLE  — SELL signals only on F&O-eligible stocks

BONUS GATES (increase score further):
  + Heikin Ashi confirmation  (trend candle in signal direction)
  + VWAP position alignment   (BUY above VWAP, SELL below VWAP)
  + RSI in momentum zone      (35-55 for BUY, 45-65 for SELL)
  + Relative strength vs Nifty (stock stronger than index)
  + Opening range aligned     (ORB direction matches signal)
  + No news blackout          (30 min buffer around events)
  + Nifty same direction      (index confirms stock direction)

REJECTION REASONS LOGGED:
Every rejection is stored so the self-learner can see what's being filtered.
"""

import logging
from dataclasses import dataclass, field
from datetime import time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ── POWER HOUR WINDOWS (IST) ───────────────────────────────
# Only trade during highest-probability intraday windows.
# Based on 18yr observation: 70%+ of profitable moves start here.
POWER_WINDOWS = [
    (time(9, 15), time(10, 30)),   # Opening drive — strongest momentum
    (time(14, 0), time(15, 0)),    # Afternoon institutional — second best
]

# Reduced-size window (can trade but 60% size)
CAUTION_WINDOWS = [
    (time(10, 30), time(11, 0)),   # Post-opening fade
    (time(13, 0),  time(14, 0)),   # Early afternoon pickup
]

# NO TRADE windows
AVOID_WINDOWS = [
    (time(11, 0),  time(13, 0)),   # Midday chop — 18yr rule: ALWAYS avoid
    (time(15, 0),  time(15, 30)),  # EOD — no new entries
]


@dataclass
class FilterResult:
    """Result of high-accuracy filter with full reasoning."""
    passed:          bool    = False
    final_score:     float   = 0.0
    size_multiplier: float   = 1.0   # 0 = no trade, 0.5 = half, 1.0 = full, 1.2 = premium
    gates_passed:    List[str] = field(default_factory=list)
    gates_failed:    List[str] = field(default_factory=list)
    bonuses:         List[str] = field(default_factory=list)
    rejection_reason: str = ""
    time_window:     str = ""
    quality_grade:   str = ""   # "A+", "A", "B", "C", "REJECT"

    @property
    def summary(self) -> str:
        grade = self.quality_grade
        if self.passed:
            return (f"✅ GRADE {grade} | Score {self.final_score:.0f} | "
                    f"Size {self.size_multiplier:.1f}x | "
                    f"Gates: {', '.join(self.gates_passed)}")
        return f"❌ REJECTED: {self.rejection_reason}"


class HighAccuracyFilter:
    """
    The gatekeeper. Only the best setups get through.
    18yr rule: 'Miss a trade → lose opportunity. Take a bad trade → lose money.
    Opportunity loss is recoverable. Capital loss may not be.'
    """

    def __init__(self):
        self._rejection_log: List[Dict] = []
        self._pass_count    = 0
        self._reject_count  = 0

    # ─────────────────────────────────────────────────────────
    # MAIN FILTER — call this before every trade
    # ─────────────────────────────────────────────────────────

    def evaluate(
        self,
        signal_score:        float,
        direction:           str,             # "BUY" or "SELL"
        regime:              str,
        mtf_alignment:       Dict,            # from MultiTimeframeAnalyzer
        volume_ratio:        float,
        pattern_names:       List[str],
        pattern_scores:      List[float],
        df_5m:               Optional[pd.DataFrame],
        rsi:                 float,
        above_vwap:          bool,
        nifty_change_pct:    float,
        stock_change_pct:    float,
        news_clear:          bool,
        orb_direction:       str = "",        # "UP", "DOWN", or ""
        learner=None,
        # ── NEW: Gates 6-10 parameters ──────────────────────────
        symbol:              str   = "",      # Gate 6-10: symbol for checks
        daily_volume:        float = 0.0,     # Gate 6: today's volume (shares)
        prev_close:          float = 0.0,     # Gate 7: yesterday's close (circuit)
        ltp:                 float = 0.0,     # Gate 7: current price (circuit calc)
        minutes_since_open:  float = 0.0,     # Gate 8: for gap timing
        gap_pct:             float = 0.0,     # Gate 8: gap % (set by gap_analyzer)
    ) -> FilterResult:

        result = FilterResult()
        now_ist = get_current_ist_time()

        # ── GATE 1: POWER HOURS ───────────────────────────
        window, size_mult = self._check_time_window(now_ist.time())
        result.time_window = window

        if window == "AVOID":
            result.rejection_reason = (
                f"AVOID window ({now_ist.strftime('%H:%M')} IST). "
                "No trades during midday chop (11:00–13:00) or EOD."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.size_multiplier = size_mult
        result.gates_passed.append(f"POWER_HOUR({window})")

        # ── GATE 2: REGIME ALIGNMENT ─────────────────────
        regime_ok, regime_size = self._check_regime(regime, direction)
        if not regime_ok:
            result.gates_failed.append(f"REGIME({regime})")
            result.rejection_reason = (
                f"Regime '{regime}' does not support {direction} momentum trades. "
                "Only STRONG_TREND or OPENING_DRIVE for full size."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.size_multiplier *= regime_size
        result.gates_passed.append(f"REGIME({regime})")

        # ── GATE 3: MULTI-TIMEFRAME ALIGNMENT ────────────
        mtf_ok, mtf_score = self._check_mtf(mtf_alignment, direction)
        if not mtf_ok:
            result.gates_failed.append("MTF_ALIGNMENT")
            result.rejection_reason = (
                f"MTF conflict: {mtf_alignment.get('description','no alignment')}. "
                "Need ≥2 timeframes aligned."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.gates_passed.append(f"MTF(score={mtf_score})")

        # ── GATE 4: VOLUME SURGE ─────────────────────────
        vol_ok, vol_bonus = self._check_volume(volume_ratio)
        if not vol_ok:
            result.gates_failed.append(f"VOLUME(ratio={volume_ratio:.1f})")
            result.rejection_reason = (
                f"Volume ratio {volume_ratio:.1f}x < 1.8x required. "
                "No volume = no conviction = no trade."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.gates_passed.append(f"VOLUME({volume_ratio:.1f}x)")

        # ── GATE 5: PATTERN QUALITY SCORE ────────────────
        pat_ok, pat_score, best_pattern = self._check_pattern_quality(
            signal_score, pattern_scores, pattern_names, learner
        )
        if not pat_ok:
            result.gates_failed.append(f"PATTERN_SCORE({signal_score:.0f})")
            result.rejection_reason = (
                f"Signal score {signal_score:.0f} < 72 required. "
                f"Best pattern: {best_pattern}. Wait for stronger setup."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.gates_passed.append(f"PATTERN_SCORE({signal_score:.0f})")

        # ── NEWS BLACKOUT ────────────────────────────────
        if not news_clear:
            result.rejection_reason = "News blackout active — RBI/FOMC/event window"
            self._log_rejection(result, signal_score, direction)
            return result

        # ── GATE 6: LIQUIDITY ─────────────────────────────
        if daily_volume > 0:
            liq_ok, liq_reason = self._check_liquidity(daily_volume)
            if not liq_ok:
                result.gates_failed.append(f"LIQUIDITY({daily_volume/1e5:.1f}L)")
                result.rejection_reason = liq_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append(f"LIQUIDITY({daily_volume/1e5:.0f}L)")

        # ── GATE 7: CIRCUIT BREAKER PROXIMITY ────────────
        if prev_close > 0 and ltp > 0:
            circ_ok, circ_reason = self._check_circuit_proximity(prev_close, ltp)
            if not circ_ok:
                result.gates_failed.append("CIRCUIT_BREAKER")
                result.rejection_reason = circ_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append("CIRCUIT_OK")

        # ── GATE 8: GAP RISK ──────────────────────────────
        abs_gap = abs(gap_pct)
        if abs_gap > 0:
            gap_ok, gap_reason = self._check_gap_risk(gap_pct, minutes_since_open)
            if not gap_ok:
                result.gates_failed.append(f"GAP_RISK({gap_pct:+.1f}%)")
                result.rejection_reason = gap_reason
                self._log_rejection(result, signal_score, direction)
                return result
            if abs_gap >= 2.0:
                result.gates_passed.append(f"GAP_CLEARED({gap_pct:+.1f}%)")

        # ── GATE 9: CORPORATE ACTIONS ─────────────────────
        if symbol:
            corp_ok, corp_reason = self._check_corp_actions(symbol)
            if not corp_ok:
                result.gates_failed.append("CORP_ACTION")
                result.rejection_reason = corp_reason
                self._log_rejection(result, signal_score, direction)
                return result

        # ── GATE 10: F&O ELIGIBILITY (SELL/SHORT only) ───
        if direction == "SELL" and symbol:
            fo_ok, fo_reason = self._check_fo_eligibility(symbol)
            if not fo_ok:
                result.gates_failed.append("FO_INELIGIBLE")
                result.rejection_reason = fo_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append("FO_ELIGIBLE")

        # ─────────────────────────────────────────────────
        # ALL 10 GATES PASSED — now calculate bonus score
        # ─────────────────────────────────────────────────
        result.passed   = True
        bonus_score     = 0.0

        # Bonus 1: Heikin Ashi confirmation
        if df_5m is not None and len(df_5m) >= 3:
            ha_ok, ha_note = self._check_heikin_ashi(df_5m, direction)
            if ha_ok:
                bonus_score += 5
                result.bonuses.append(f"HA({ha_note})")
            else:
                bonus_score -= 3
                result.bonuses.append(f"HA_WEAK({ha_note})")

        # Bonus 2: VWAP position
        if direction == "BUY" and above_vwap:
            bonus_score += 6
            result.bonuses.append("ABOVE_VWAP")
        elif direction == "SELL" and not above_vwap:
            bonus_score += 6
            result.bonuses.append("BELOW_VWAP")
        elif direction == "BUY" and not above_vwap:
            bonus_score -= 5
            result.size_multiplier *= 0.8   # Below VWAP buy = weaker
            result.bonuses.append("BELOW_VWAP(weak_buy)")

        # Bonus 3: RSI momentum zone
        rsi_bonus = self._rsi_bonus(rsi, direction)
        bonus_score += rsi_bonus
        if rsi_bonus > 0:
            result.bonuses.append(f"RSI_OPTIMAL({rsi:.0f})")
        elif rsi_bonus < 0:
            result.bonuses.append(f"RSI_EXTREME({rsi:.0f})")

        # Bonus 4: Relative strength vs Nifty
        rs = stock_change_pct - nifty_change_pct
        if direction == "BUY" and rs > 0.3:
            bonus_score += 5
            result.bonuses.append(f"RS_STRONG(+{rs:.1f}%)")
        elif direction == "SELL" and rs < -0.3:
            bonus_score += 5
            result.bonuses.append(f"RS_WEAK({rs:.1f}%)")
        elif (direction == "BUY" and rs < -0.5) or (direction == "SELL" and rs > 0.5):
            bonus_score -= 8
            result.size_multiplier *= 0.7
            result.bonuses.append(f"RS_AGAINST({rs:.1f}%)")

        # Bonus 5: Nifty alignment
        if direction == "BUY" and nifty_change_pct > 0.2:
            bonus_score += 5
            result.bonuses.append("NIFTY_ALIGNED")
        elif direction == "SELL" and nifty_change_pct < -0.2:
            bonus_score += 5
            result.bonuses.append("NIFTY_ALIGNED")
        elif (direction == "BUY" and nifty_change_pct < -0.5) or \
             (direction == "SELL" and nifty_change_pct > 0.5):
            bonus_score -= 10
            result.bonuses.append("NIFTY_AGAINST")

        # Bonus 6: ORB direction match
        if orb_direction and orb_direction == ("UP" if direction=="BUY" else "DOWN"):
            bonus_score += 8
            result.bonuses.append("ORB_ALIGNED")
        elif orb_direction and orb_direction != ("UP" if direction=="BUY" else "DOWN"):
            bonus_score -= 6
            result.bonuses.append("ORB_CONFLICT")

        # Bonus 7: Premium patterns
        premium_patterns = {
            "ORB_BREAKOUT", "VWAP_RECLAIM", "BULLISH_ENGULFING",
            "BEARISH_ENGULFING", "VOLUME_SURGE_BREAKOUT", "FLAG_BREAKOUT",
            "BOS_BULLISH", "BOS_BEARISH"
        }
        if any(p in premium_patterns for p in pattern_names):
            bonus_score += 6
            matched = [p for p in pattern_names if p in premium_patterns]
            result.bonuses.append(f"PREMIUM({','.join(matched[:2])})")

        # ── FINAL SCORE & GRADE ───────────────────────────
        result.final_score = signal_score + bonus_score

        # Reduce size if bonus brought score below threshold
        if result.final_score < 72:
            result.passed = False
            result.rejection_reason = (
                f"Post-bonus score {result.final_score:.0f} < 72. "
                f"Bonuses: {bonus_score:+.0f}. Too many counter-indicators."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        # Grade the setup
        if result.final_score >= 90:
            result.quality_grade   = "A+"
            result.size_multiplier = min(result.size_multiplier * 1.2, 1.5)
        elif result.final_score >= 82:
            result.quality_grade   = "A"
            result.size_multiplier = min(result.size_multiplier * 1.1, 1.3)
        elif result.final_score >= 75:
            result.quality_grade   = "B"
        else:
            result.quality_grade   = "C"
            result.size_multiplier *= 0.75   # Low-conviction C grade = smaller size

        # Hard cap size multiplier
        result.size_multiplier = round(min(result.size_multiplier, 1.5), 2)

        self._pass_count += 1
        logger.info(
            f"[{format_ist_timestamp()}] FILTER PASSED: "
            f"Grade={result.quality_grade} "
            f"Score={result.final_score:.0f} "
            f"Size={result.size_multiplier}x "
            f"Gates={result.gates_passed} "
            f"Bonuses={result.bonuses}"
        )
        return result

    # ─────────────────────────────────────────────────────────
    # GATE IMPLEMENTATIONS
    # ─────────────────────────────────────────────────────────

    def _check_time_window(self, t: time) -> Tuple[str, float]:
        for start, end in POWER_WINDOWS:
            if start <= t < end:
                return "POWER", 1.0
        for start, end in CAUTION_WINDOWS:
            if start <= t < end:
                return "CAUTION", 0.6
        for start, end in AVOID_WINDOWS:
            if start <= t < end:
                return "AVOID", 0.0
        return "POWER", 1.0   # Outside all windows (e.g., at exact boundaries)

    def _check_regime(self, regime: str, direction: str) -> Tuple[bool, float]:
        """
        Only trade momentum-friendly regimes.
        18yr rule: A ranging market kills momentum strategies.
        """
        full_size = {
            "STRONG_TREND_UP":   ("BUY",  1.2),
            "STRONG_TREND_DOWN": ("SELL", 1.2),
            "OPENING_DRIVE":     ("BOTH", 1.1),
            "AFTERNOON_TREND":   ("BOTH", 1.0),
        }
        reduced_size = {
            "WEAK_TREND_UP":     ("BUY",  0.8),
            "WEAK_TREND_DOWN":   ("SELL", 0.8),
            "HIGH_VOLATILITY":   ("BOTH", 0.6),
        }
        blocked = {"RANGING", "LOW_VOLATILITY", "MIDDAY_CHOP"}

        if regime in blocked:
            return False, 0.0

        if regime in full_size:
            pref_dir, size = full_size[regime]
            if pref_dir == "BOTH" or pref_dir == direction:
                return True, size
            return False, 0.0  # Wrong direction for regime

        if regime in reduced_size:
            pref_dir, size = reduced_size[regime]
            if pref_dir == "BOTH" or pref_dir == direction:
                return True, size
            return False, 0.0

        return True, 0.9   # Unknown regime — allow but cautiously

    def _check_mtf(self, mtf: Dict, direction: str) -> Tuple[bool, int]:
        alignment_score = mtf.get("alignment_score", mtf.get("score", 0))
        entry_dir       = mtf.get("entry_direction", mtf.get("direction", "SKIP"))

        if entry_dir == "SKIP":
            return False, 0
        if alignment_score < 60:
            return False, alignment_score
        # Check direction match
        signal_dir = "LONG" if direction == "BUY" else "SHORT"
        if entry_dir != signal_dir:
            return False, alignment_score
        return True, alignment_score

    def _check_volume(self, volume_ratio: float) -> Tuple[bool, float]:
        """Require 1.8x volume. No volume = no institutional participation."""
        if volume_ratio < 1.8:
            return False, 0
        bonus = min((volume_ratio - 1.8) * 5, 10)   # Up to +10 for very high volume
        return True, bonus

    def _check_pattern_quality(
        self,
        signal_score:  float,
        pat_scores:    List[float],
        pat_names:     List[str],
        learner=None,
    ) -> Tuple[bool, float, str]:
        """Require minimum 72 base score. Apply learner weights."""
        best_score   = max(pat_scores) if pat_scores else signal_score
        best_pattern = pat_names[pat_scores.index(best_score)] if pat_scores and pat_names else "?"

        # Apply learner weight to best pattern
        effective_score = signal_score
        if learner and best_pattern:
            weight = learner.get_pattern_weight(best_pattern)
            if weight == 0.0:
                return False, 0, f"{best_pattern}(DISABLED)"
            effective_score = signal_score * weight

        return effective_score >= 72, effective_score, best_pattern

    def _check_heikin_ashi(self, df: pd.DataFrame, direction: str) -> Tuple[bool, str]:
        """
        Heikin Ashi candles smooth noise and confirm trend direction.
        18yr rule: HA is NOT for entry timing — it's for TREND CONFIRMATION.
        """
        try:
            close  = df["close"]
            open_  = df["open"]
            high   = df["high"]
            low    = df["low"]

            ha_close = (open_ + high + low + close) / 4
            ha_open  = pd.Series(index=df.index, dtype=float)
            ha_open.iloc[0] = (open_.iloc[0] + close.iloc[0]) / 2
            for i in range(1, len(df)):
                ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2

            last_ha_close = float(ha_close.iloc[-1])
            last_ha_open  = float(ha_open.iloc[-1])
            prev_ha_close = float(ha_close.iloc[-2])
            prev_ha_open  = float(ha_open.iloc[-2])

            # Bullish HA: close > open, increasing
            if direction == "BUY":
                bullish = (
                    last_ha_close > last_ha_open and        # Green HA candle
                    last_ha_close > prev_ha_close and       # Rising
                    abs(last_ha_close - last_ha_open) >     # Solid body
                    abs(prev_ha_close - prev_ha_open) * 0.5
                )
                return bullish, "BULLISH_HA" if bullish else "WEAK_HA"

            else:  # SELL
                bearish = (
                    last_ha_close < last_ha_open and
                    last_ha_close < prev_ha_close and
                    abs(last_ha_close - last_ha_open) >
                    abs(prev_ha_close - prev_ha_open) * 0.5
                )
                return bearish, "BEARISH_HA" if bearish else "WEAK_HA"

        except Exception as e:
            logger.debug(f"HA check failed: {e}")
            return True, "HA_UNAVAILABLE"

    def _check_liquidity(self, daily_volume: float) -> Tuple[bool, str]:
        """
        Gate 6: Minimum daily volume check.
        18yr rule: Illiquid stocks have wide spreads — you pay to enter AND exit.
        At 5 lakh shares/day, spread impact is manageable for our position sizes.
        """
        from config import MIN_DAILY_VOLUME
        min_vol = MIN_DAILY_VOLUME

        if daily_volume < min_vol:
            return False, (
                f"Volume too low: {daily_volume/1e5:.1f}L shares/day "
                f"< {min_vol/1e5:.0f}L minimum. "
                "Low liquidity = wide spread = guaranteed slippage loss."
            )
        return True, ""

    def _check_circuit_proximity(
        self, prev_close: float, ltp: float
    ) -> Tuple[bool, str]:
        """
        Gate 7: Don't trade near NSE circuit breaker bands.
        NSE applies 5%, 10%, 20% upper/lower circuits from previous close.
        Near the circuit → stock may freeze → trapped position.
        """
        from config import CIRCUIT_BANDS, CIRCUIT_BUFFER_PCT

        price_change_pct = ((ltp - prev_close) / prev_close) * 100

        for band_pct in CIRCUIT_BANDS:
            # Check upper circuit proximity
            upper_circuit_pct = band_pct
            if price_change_pct >= (upper_circuit_pct - CIRCUIT_BUFFER_PCT):
                return False, (
                    f"Near upper {band_pct:.0f}% circuit "
                    f"(price +{price_change_pct:.1f}% vs prev close). "
                    "Trading near circuit = risk of freeze — avoid."
                )
            # Check lower circuit proximity
            lower_circuit_pct = -band_pct
            if price_change_pct <= (lower_circuit_pct + CIRCUIT_BUFFER_PCT):
                return False, (
                    f"Near lower {band_pct:.0f}% circuit "
                    f"(price {price_change_pct:.1f}% vs prev close). "
                    "Trading near circuit = risk of freeze — avoid."
                )

        return True, ""

    def _check_gap_risk(
        self, gap_pct: float, minutes_since_open: float
    ) -> Tuple[bool, str]:
        """
        Gate 8: Pre-market gap price discovery window.
        18yr rule: >2% gap = unpredictable first 5-15 min. Wait it out.
        Extreme gaps >5% = avoid whole session.
        """
        from config import MAX_GAP_PCT, LARGE_GAP_PCT, EXTREME_GAP_PCT
        abs_gap = abs(gap_pct)

        if abs_gap >= EXTREME_GAP_PCT:
            return False, (
                f"EXTREME gap {gap_pct:+.1f}% — avoid entire session. "
                "Price discovery takes full day on 5%+ gaps."
            )

        wait_min = 0
        if abs_gap >= LARGE_GAP_PCT:
            wait_min = 15
        elif abs_gap >= MAX_GAP_PCT:
            wait_min = 5

        if wait_min > 0 and minutes_since_open < wait_min:
            remaining = wait_min - minutes_since_open
            return False, (
                f"Gap {gap_pct:+.1f}% — price discovery window. "
                f"Wait {remaining:.0f} more min (total {wait_min}min after open)."
            )

        return True, ""

    def _check_corp_actions(self, symbol: str) -> Tuple[bool, str]:
        """
        Gate 9: Corporate actions proximity check.
        Ex-dividend, bonus, split create artificial price moves.
        Skip stocks within 2 days of any ex-date.
        """
        try:
            from corporate_actions import is_safe_from_corp_actions
            safe, reason = is_safe_from_corp_actions(symbol)
            if not safe:
                return False, (
                    f"{symbol} has upcoming corporate action: {reason}. "
                    "Price adjustment distorts all technical signals."
                )
            return True, ""
        except ImportError:
            # Module not yet available — allow trade (no false positives)
            return True, ""
        except Exception as e:
            logger.debug(f"Corp action check error for {symbol}: {e}")
            return True, ""   # Fail open — don't block on data errors

    def _check_fo_eligibility(self, symbol: str) -> Tuple[bool, str]:
        """
        Gate 10: F&O eligibility for SELL (SHORT) signals.
        Intraday shorting on NSE is ONLY allowed for F&O segment stocks.
        Short-selling a non-F&O equity stock → Groww REJECTS the order
        → phantom position risk + wasted order slot.
        """
        try:
            from nse_fo_list import is_fo_eligible
            if not is_fo_eligible(symbol):
                return False, (
                    f"{symbol} is NOT F&O eligible — cannot short intraday. "
                    "NSE equity-only stocks: BUY-only. "
                    "Signal converted to SKIP (not a BUY opportunity)."
                )
            return True, ""
        except ImportError:
            return True, ""   # Module unavailable — allow (no false blocks)
        except Exception as e:
            logger.debug(f"F&O eligibility check error for {symbol}: {e}")
            return True, ""   # Fail open

    def _rsi_bonus(self, rsi: float, direction: str) -> float:
        """
        Ideal RSI zones for momentum entries.
        18yr rule: The best BUY entries are RSI 38-52 (momentum building, not overbought).
        Extreme RSI = fading momentum = dangerous entry.
        """
        if direction == "BUY":
            if 35 <= rsi <= 52:   return 8    # Golden zone — momentum building
            if 52 < rsi <= 60:    return 3    # Slightly extended but ok
            if rsi > 70:          return -12  # Overbought — high failure risk
            if rsi < 30:          return -5   # Extreme — trend may continue down
        else:  # SELL
            if 48 <= rsi <= 65:   return 8
            if 40 <= rsi < 48:    return 3
            if rsi < 30:          return -12
            if rsi > 75:          return -5
        return 0

    # ─────────────────────────────────────────────────────────
    # STATISTICS
    # ─────────────────────────────────────────────────────────

    def _log_rejection(self, result: FilterResult, score: float, direction: str):
        self._reject_count += 1
        self._rejection_log.append({
            "time_ist":  format_ist_timestamp(),
            "score":     score,
            "direction": direction,
            "reason":    result.rejection_reason,
            "window":    result.time_window,
        })
        logger.debug(
            f"[{format_ist_timestamp()}] FILTERED OUT: {result.rejection_reason}"
        )

    def get_stats(self) -> Dict:
        total = self._pass_count + self._reject_count
        return {
            "total_evaluated": total,
            "passed":    self._pass_count,
            "rejected":  self._reject_count,
            "pass_rate": round(self._pass_count / max(total, 1) * 100, 1),
            "top_rejection_reasons": self._top_rejections(),
        }

    def _top_rejections(self) -> List[str]:
        from collections import Counter
        if not self._rejection_log:
            return []
        reasons = [r["reason"][:50] for r in self._rejection_log]
        return [r for r, _ in Counter(reasons).most_common(5)]

    def print_stats(self):
        s = self.get_stats()
        print(
            f"\nHigh-Accuracy Filter Stats:\n"
            f"  Evaluated: {s['total_evaluated']} | "
            f"Passed: {s['passed']} ({s['pass_rate']:.0f}%) | "
            f"Rejected: {s['rejected']}\n"
            f"  Top rejections:\n"
            + "\n".join(f"    • {r}" for r in s["top_rejection_reasons"])
        )


# ============================================================
# MODULE: volume_profile.py
# ============================================================
"""
volume_profile.py — NSE Momentum Groww AI Bot
Volume Profile Analysis: VPOC, VAH, VAL, HVN, LVN

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).

Volume Profile is the institutional edge. Every major market maker,
prop desk, and hedge fund uses volume profile to identify:
  • VPOC (Volume Point of Control): Price with highest traded volume = magnetic level
  • VAH (Value Area High): Top of the 70% volume zone = resistance
  • VAL (Value Area Low): Bottom of the 70% volume zone = support
  • HVN (High Volume Node): Price levels where institutions transact heavily = S/R
  • LVN (Low Volume Node): Price gaps in volume = fast travel zones (breakouts)

18 years of experience: "Volume profile doesn't lie. Price returns to VPOC.
Price breaks through LVN quickly. Price stalls at HVN."

What this module provides:
  1. Session Volume Profile (today's 5m candles → intraday VPOC/VAH/VAL)
  2. Multi-day Volume Profile (5-20 day composite → key institutional levels)
  3. Level-based trade bias (is price above/below VPOC? Near VAH/VAL?)
  4. HVN/LVN detection for entry quality assessment
  5. Volume-weighted price targets

Usage:
  from volume_profile import VolumeProfileAnalyzer
  vp  = VolumeProfileAnalyzer()
  res = vp.analyze(candles_df)  # pass 5m candle DataFrame
  print(res.vpoc, res.vah, res.val)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

# Volume area covers 70% of total volume (standard profile definition)
VALUE_AREA_VOLUME_PCT = 0.70
DEFAULT_NUM_BINS      = 100   # Price levels to slice into


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class VolumeNode:
    """A single price level in the volume profile."""
    price:       float
    volume:      int
    volume_pct:  float    # % of total session volume
    node_type:   str      # "HVN", "LVN", "NEUTRAL"

    @property
    def is_hvn(self) -> bool:
        return self.node_type == "HVN"

    @property
    def is_lvn(self) -> bool:
        return self.node_type == "LVN"


@dataclass
class VolumeProfileResult:
    """Complete volume profile analysis for a session or date range."""
    symbol:         str
    session_date:   str
    vpoc:           float          # Highest volume price level
    vah:            float          # Value Area High (70% volume top boundary)
    val:            float          # Value Area Low (70% volume bottom boundary)
    value_area_pct: float          # Actual % of volume within VAH-VAL (should be ~70%)
    session_high:   float
    session_low:    float
    hvn_levels:     List[float]    # High Volume Nodes (strong S/R)
    lvn_levels:     List[float]    # Low Volume Nodes (fast travel zones)
    total_volume:   int
    profile:        List[VolumeNode]  # Full price-volume distribution
    num_candles:    int
    timestamp:      str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_ist_timestamp()

    @property
    def value_area_width(self) -> float:
        return self.vah - self.val

    @property
    def value_area_width_pct(self) -> float:
        return (self.value_area_width / self.vpoc * 100) if self.vpoc > 0 else 0

    def price_location(self, price: float) -> str:
        """Describe where `price` sits relative to the value area."""
        if price > self.vah:
            return "ABOVE_VALUE_AREA"
        if price < self.val:
            return "BELOW_VALUE_AREA"
        if abs(price - self.vpoc) / self.vpoc < 0.002:
            return "AT_VPOC"
        if abs(price - self.vah) / self.vah < 0.003:
            return "NEAR_VAH"
        if abs(price - self.val) / self.val < 0.003:
            return "NEAR_VAL"
        return "INSIDE_VALUE_AREA"

    def get_bias(self, current_price: float) -> str:
        """
        Simple bias based on price vs VPOC:
        Above VPOC → bullish (buying side controls)
        Below VPOC → bearish (selling side controls)
        At VPOC → neutral/balanced
        """
        if current_price > self.vpoc * 1.003:
            return "BULLISH"
        if current_price < self.vpoc * 0.997:
            return "BEARISH"
        return "NEUTRAL"

    def nearest_hvn(self, price: float, max_dist_pct: float = 1.0) -> Optional[float]:
        """Find the nearest High Volume Node within max_dist_pct % of price."""
        best, best_dist = None, float("inf")
        for lvl in self.hvn_levels:
            dist = abs(price - lvl) / price * 100
            if dist <= max_dist_pct and dist < best_dist:
                best, best_dist = lvl, dist
        return best

    def nearest_lvn(self, price: float, max_dist_pct: float = 1.5) -> Optional[float]:
        """Find the nearest Low Volume Node (expect fast move through)."""
        best, best_dist = None, float("inf")
        for lvl in self.lvn_levels:
            dist = abs(price - lvl) / price * 100
            if dist <= max_dist_pct and dist < best_dist:
                best, best_dist = lvl, dist
        return best

    def summary(self) -> str:
        return (
            f"VolumeProfile {self.symbol} ({self.session_date}) | "
            f"VPOC=₹{self.vpoc:.2f} | VAH=₹{self.vah:.2f} | VAL=₹{self.val:.2f} | "
            f"VA Width={self.value_area_width_pct:.1f}% | "
            f"HVNs={len(self.hvn_levels)} LVNs={len(self.lvn_levels)}"
        )


# ============================================================
# MAIN ANALYZER
# ============================================================

class VolumeProfileAnalyzer:
    """
    Volume Profile Analysis Engine.

    Converts raw OHLCV candle data into institutional price-volume maps.
    Used for identifying key support/resistance and trade quality assessment.
    """

    def __init__(self, num_bins: int = DEFAULT_NUM_BINS):
        self.num_bins = num_bins
        self._cache: Dict[str, VolumeProfileResult] = {}

    # ──────────────────────────────────────────────────────
    # MAIN ANALYSIS
    # ──────────────────────────────────────────────────────

    def analyze(
        self,
        candles: pd.DataFrame,
        symbol: str = "UNKNOWN",
        session_date: Optional[str] = None,
    ) -> Optional[VolumeProfileResult]:
        """
        Build complete volume profile from OHLCV candle DataFrame.

        Args:
            candles: DataFrame with columns [open, high, low, close, volume]
                     indexed by datetime (IST-aware)
            symbol:  Stock/index symbol for labelling
            session_date: Date string (defaults to today IST)

        Returns:
            VolumeProfileResult with VPOC, VAH, VAL, HVN/LVN levels
        """
        if candles is None or candles.empty:
            return None

        session_date = session_date or str(get_current_ist_time().date())
        cache_key    = f"{symbol}_{session_date}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            result = self._build_profile(candles, symbol, session_date)
            if result:
                self._cache[cache_key] = result
                logger.debug(f"[{format_ist_timestamp()}] {result.summary()}")
            return result
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] VP analysis error for {symbol}: {e}")
            return None

    def _build_profile(
        self, df: pd.DataFrame, symbol: str, session_date: str
    ) -> Optional[VolumeProfileResult]:
        """Core volume profile computation."""
        # Normalise column names
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        required = {"high", "low", "close", "volume"}
        if not required.issubset(set(df.columns)):
            logger.warning(f"Volume profile: missing columns. Have: {df.columns.tolist()}")
            return None

        df = df.dropna(subset=["high", "low", "close", "volume"])
        if len(df) < 5:
            return None

        # Build price range
        session_high = float(df["high"].max())
        session_low  = float(df["low"].min())
        price_range  = session_high - session_low

        if price_range < 0.01:
            return None

        # Create price bins
        price_levels = np.linspace(session_low, session_high, self.num_bins + 1)
        bin_volume   = np.zeros(self.num_bins)

        # Distribute each candle's volume across its high-low range
        # (Typical Price method: each candle votes proportionally)
        for _, row in df.iterrows():
            try:
                h = float(row["high"])
                l = float(row["low"])
                v = float(row["volume"])
                if h <= l or v == 0:
                    continue
                # Find bins covered by this candle's range
                lo_bin = max(0, int((l - session_low) / price_range * self.num_bins))
                hi_bin = min(self.num_bins - 1, int((h - session_low) / price_range * self.num_bins))
                n_bins_covered = max(1, hi_bin - lo_bin + 1)
                vol_per_bin    = v / n_bins_covered
                for b in range(lo_bin, hi_bin + 1):
                    bin_volume[b] += vol_per_bin
            except Exception:
                continue

        total_volume = float(bin_volume.sum())
        if total_volume == 0:
            return None

        # VPOC: bin with max volume
        vpoc_idx  = int(np.argmax(bin_volume))
        vpoc_price = float((price_levels[vpoc_idx] + price_levels[vpoc_idx + 1]) / 2)

        # Value Area: expand from VPOC until 70% of volume is captured
        vah_idx, val_idx = self._calc_value_area(bin_volume, vpoc_idx, total_volume)
        vah_price = float((price_levels[vah_idx] + price_levels[min(vah_idx + 1, self.num_bins)]) / 2)
        val_price = float((price_levels[val_idx] + price_levels[val_idx + 1]) / 2)
        va_volume_pct = float(bin_volume[val_idx:vah_idx + 1].sum() / total_volume * 100)

        # Build VolumeNode list with HVN/LVN classification
        mean_vol = float(np.mean(bin_volume[bin_volume > 0]))
        std_vol  = float(np.std(bin_volume[bin_volume > 0]))
        hvn_threshold = mean_vol + std_vol * 0.5   # HVN: above 0.5 std
        lvn_threshold = max(0, mean_vol - std_vol * 1.0)  # LVN: below 1 std

        nodes   = []
        hvn_lvl = []
        lvn_lvl = []
        for i in range(self.num_bins):
            p    = float((price_levels[i] + price_levels[i + 1]) / 2)
            v    = int(bin_volume[i])
            pct  = v / total_volume * 100
            if bin_volume[i] >= hvn_threshold:
                ntype = "HVN"
                hvn_lvl.append(p)
            elif bin_volume[i] <= lvn_threshold and bin_volume[i] > 0:
                ntype = "LVN"
                lvn_lvl.append(p)
            else:
                ntype = "NEUTRAL"
            nodes.append(VolumeNode(price=p, volume=v, volume_pct=pct, node_type=ntype))

        # Consolidate nearby HVN/LVN levels (merge within 0.3% of each other)
        hvn_lvl = self._consolidate_levels(hvn_lvl, tolerance_pct=0.3)
        lvn_lvl = self._consolidate_levels(lvn_lvl, tolerance_pct=0.3)

        return VolumeProfileResult(
            symbol=symbol,
            session_date=session_date,
            vpoc=round(vpoc_price, 2),
            vah=round(vah_price, 2),
            val=round(val_price, 2),
            value_area_pct=round(va_volume_pct, 1),
            session_high=round(session_high, 2),
            session_low=round(session_low, 2),
            hvn_levels=[round(l, 2) for l in hvn_lvl],
            lvn_levels=[round(l, 2) for l in lvn_lvl],
            total_volume=int(total_volume),
            profile=nodes,
            num_candles=len(df),
        )

    def _calc_value_area(
        self, bin_volume: np.ndarray, vpoc_idx: int, total_volume: float
    ) -> Tuple[int, int]:
        """
        Expand from VPOC outward (adding highest adjacent bin each step)
        until 70% of total volume is captured.
        Returns (vah_idx, val_idx).
        """
        target     = total_volume * VALUE_AREA_VOLUME_PCT
        accumulated = bin_volume[vpoc_idx]
        hi_idx     = vpoc_idx
        lo_idx     = vpoc_idx
        n          = len(bin_volume)

        while accumulated < target:
            can_up   = hi_idx + 1 < n
            can_down = lo_idx - 1 >= 0

            if not can_up and not can_down:
                break

            add_up   = bin_volume[hi_idx + 1] if can_up   else -1
            add_down = bin_volume[lo_idx - 1] if can_down else -1

            if add_up >= add_down:
                hi_idx    += 1
                accumulated += bin_volume[hi_idx]
            else:
                lo_idx    -= 1
                accumulated += bin_volume[lo_idx]

        return hi_idx, lo_idx

    def _consolidate_levels(self, levels: List[float], tolerance_pct: float = 0.3) -> List[float]:
        """Merge nearby levels within tolerance_pct% of each other."""
        if not levels:
            return []
        levels = sorted(levels)
        merged = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl - merged[-1]) / merged[-1] * 100 <= tolerance_pct:
                merged[-1] = (merged[-1] + lvl) / 2  # Average
            else:
                merged.append(lvl)
        return merged

    # ──────────────────────────────────────────────────────
    # SIGNAL HELPERS
    # ──────────────────────────────────────────────────────

    def get_signal_context(
        self, result: VolumeProfileResult, current_price: float, direction: str
    ) -> Dict:
        """
        Evaluate how the volume profile supports or opposes a trade signal.

        Returns dict with:
          - location: where price is relative to value area
          - bias: BULLISH/BEARISH/NEUTRAL
          - score_adjustment: -15 to +15 for signal_generator
          - notes: list of observations
        """
        if not result:
            return {"score_adjustment": 0, "notes": []}

        location     = result.price_location(current_price)
        vp_bias      = result.get_bias(current_price)
        adjustment   = 0.0
        notes        = []
        nearby_hvn   = result.nearest_hvn(current_price, max_dist_pct=0.5)
        nearby_lvn   = result.nearest_lvn(current_price, max_dist_pct=1.0)

        if direction == "LONG":
            if location == "ABOVE_VALUE_AREA":
                adjustment += 8
                notes.append(f"Price ABOVE value area ({current_price:.2f}>{result.vah:.2f}) — LONG in acceptance zone")
            elif location == "AT_VPOC":
                adjustment += 5
                notes.append(f"Price at VPOC ₹{result.vpoc:.2f} — balanced, slight LONG edge")
            elif location == "NEAR_VAL":
                adjustment += 10
                notes.append(f"LONG from VAL ₹{result.val:.2f} — value area support, high R:R")
            elif location == "BELOW_VALUE_AREA":
                adjustment -= 10
                notes.append(f"Price BELOW value area ₹{result.val:.2f} — risk of continuation down, avoid LONG")
            elif location == "NEAR_VAH":
                adjustment -= 5
                notes.append(f"LONG near VAH ₹{result.vah:.2f} — resistance overhead, tight stop needed")

            if nearby_hvn:
                dist = abs(current_price - nearby_hvn) / current_price * 100
                if nearby_hvn > current_price:
                    adjustment += 3
                    notes.append(f"HVN above at ₹{nearby_hvn:.2f} ({dist:.1f}% away) — possible target")
                else:
                    adjustment += 5
                    notes.append(f"HVN below at ₹{nearby_hvn:.2f} — strong support under trade")

            if nearby_lvn and nearby_lvn > current_price:
                notes.append(f"LVN at ₹{nearby_lvn:.2f} — price may travel fast through this zone")
                adjustment += 4

        else:  # SHORT
            if location == "BELOW_VALUE_AREA":
                adjustment += 8
                notes.append(f"Price BELOW value area ₹{result.val:.2f} — SHORT in distribution")
            elif location == "AT_VPOC":
                adjustment += 5
                notes.append(f"Price at VPOC ₹{result.vpoc:.2f} — SHORT balanced entry")
            elif location == "NEAR_VAH":
                adjustment += 10
                notes.append(f"SHORT from VAH ₹{result.vah:.2f} — value area resistance, high R:R")
            elif location == "ABOVE_VALUE_AREA":
                adjustment -= 10
                notes.append(f"Price ABOVE value area ₹{result.vah:.2f} — breakout risk, avoid SHORT")
            elif location == "NEAR_VAL":
                adjustment -= 5
                notes.append(f"SHORT near VAL ₹{result.val:.2f} — support below, tight stop needed")

            if nearby_hvn and nearby_hvn < current_price:
                adjustment += 5
                notes.append(f"HVN below at ₹{nearby_hvn:.2f} — strong overhead supply for SHORT")

        # VPOC magnet effect
        dist_to_vpoc = abs(current_price - result.vpoc) / result.vpoc * 100
        if dist_to_vpoc > 1.5:
            notes.append(f"VPOC magnet at ₹{result.vpoc:.2f} ({dist_to_vpoc:.1f}% away) — expect reversion")

        return {
            "location":        location,
            "bias":            vp_bias,
            "score_adjustment": round(min(max(adjustment, -15), 15), 1),
            "vpoc":            result.vpoc,
            "vah":             result.vah,
            "val":             result.val,
            "notes":           notes,
        }

    # ──────────────────────────────────────────────────────
    # MULTI-DAY COMPOSITE
    # ──────────────────────────────────────────────────────

    def analyze_composite(
        self,
        candles_list: List[pd.DataFrame],
        symbol: str = "UNKNOWN",
        label: str = "5D",
    ) -> Optional[VolumeProfileResult]:
        """
        Build a composite volume profile across multiple sessions.
        Concatenates all candle data and runs profile on the merged set.
        Useful for identifying institutional S/R across multiple days.
        """
        if not candles_list:
            return None
        try:
            combined = pd.concat(
                [df for df in candles_list if df is not None and not df.empty],
                axis=0,
            ).sort_index()
            return self.analyze(combined, symbol=symbol, session_date=label)
        except Exception as e:
            logger.error(f"VP composite error: {e}")
            return None

    # ──────────────────────────────────────────────────────
    # TELEGRAM FORMAT
    # ──────────────────────────────────────────────────────

    def format_telegram(
        self, result: VolumeProfileResult, current_price: float
    ) -> str:
        """Format volume profile for Telegram signal alert."""
        if not result:
            return "Volume Profile: N/A"
        location = result.price_location(current_price)
        bias     = result.get_bias(current_price)
        bias_emj = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")
        return (
            f"📊 *Volume Profile*\n"
            f"VPOC: `₹{result.vpoc:,.2f}` | VAH: `₹{result.vah:,.2f}` | VAL: `₹{result.val:,.2f}`\n"
            f"Width: `{result.value_area_width_pct:.1f}%` | HVNs: `{len(result.hvn_levels)}` | LVNs: `{len(result.lvn_levels)}`\n"
            f"Price Location: `{location}` | {bias_emj} Bias: *{bias}*"
        )


# ──────────────────────────────────────────────────────────────
# SINGLETON
# ──────────────────────────────────────────────────────────────

_vp_analyzer: Optional[VolumeProfileAnalyzer] = None

def get_vp_analyzer() -> VolumeProfileAnalyzer:
    global _vp_analyzer
    if _vp_analyzer is None:
        _vp_analyzer = VolumeProfileAnalyzer()
    return _vp_analyzer


if __name__ == "__main__":
    import pandas as pd, numpy as np, logging
    logging.basicConfig(level=logging.INFO)

    # Generate synthetic 5-min candle data for testing
    np.random.seed(42)
    n = 75  # 6.25 hours of 5m candles
    base = 22000.0
    prices = base + np.cumsum(np.random.randn(n) * 50)
    df = pd.DataFrame({
        "open":   prices,
        "high":   prices + abs(np.random.randn(n) * 30),
        "low":    prices - abs(np.random.randn(n) * 30),
        "close":  prices + np.random.randn(n) * 10,
        "volume": np.random.randint(100000, 500000, n),
    }, index=pd.date_range("2026-04-09 09:15", periods=n, freq="5min"))

    vp = VolumeProfileAnalyzer()
    result = vp.analyze(df, symbol="NIFTY_TEST")
    if result:
        print(result.summary())
        print(f"\nHVN levels: {result.hvn_levels[:5]}")
        print(f"LVN levels: {result.lvn_levels[:5]}")
        current = float(df["close"].iloc[-1])
        ctx = vp.get_signal_context(result, current, "LONG")
        print(f"\nSignal context for LONG @ ₹{current:.2f}:")
        print(f"  Location: {ctx['location']}")
        print(f"  Score adjustment: {ctx['score_adjustment']:+.1f}")
        for note in ctx["notes"]:
            print(f"  → {note}")


# ============================================================
# MODULE: gap_analyzer.py
# ============================================================
"""
gap_analyzer.py — NSE Momentum Groww AI Bot
Pre-Market Gap Analysis — Today Open vs Yesterday Close

Purpose: Detect and manage gap opens that create unreliable signals.

18yr RULE — "Gaps are NOT your friend at open":
  - Stocks with >2% gap up or down have UNPREDICTABLE price discovery for 5-10 min.
  - The first 1-3 candles are often noise — institutions testing retail.
  - Chasing a gap open is a 50/50 gamble, NOT momentum trading.
  - Wait for the gap to stabilize, then trade the direction after confirmation.

GAP CATEGORIES:
  SMALL  (<2%)   : Normal — trade immediately
  MEDIUM (2-3.5%): Wait 5 min for price discovery
  LARGE  (3.5-5%): Wait 15 min
  EXTREME (>5%)  : Avoid the stock for entire session

Special case — Gap Reclaim:
  If price FILLS the gap within the first 15 min → potential strong reversal signal.
  If price EXTENDS the gap beyond 15 min → trend continuation (trade with gap).
  Both are handled by the wait window: after the wait, signal direction is reliable.
"""

import logging
from datetime import date
from typing import Dict, Optional, Tuple

from utils import get_current_ist_date, get_current_ist_time, MARKET_OPEN

logger = logging.getLogger(__name__)


# ── GAP THRESHOLDS ─────────────────────────────────────────────────────────

class GapCategory:
    SMALL   = "SMALL"     # < 2% — no restriction
    MEDIUM  = "MEDIUM"    # 2.0% – 3.5% — wait 5 min
    LARGE   = "LARGE"     # 3.5% – 5.0% — wait 15 min
    EXTREME = "EXTREME"   # > 5% — avoid session


THRESHOLDS = {
    GapCategory.EXTREME: 5.0,
    GapCategory.LARGE:   3.5,
    GapCategory.MEDIUM:  2.0,
    GapCategory.SMALL:   0.0,
}

# Minutes to wait after market open before trading a gapped stock
GAP_WAIT_MINUTES: Dict[str, int] = {
    GapCategory.SMALL:   0,
    GapCategory.MEDIUM:  5,
    GapCategory.LARGE:   15,
    GapCategory.EXTREME: 9999,   # Session-long avoid
}


class GapInfo:
    """Complete gap analysis result for one symbol."""

    def __init__(
        self,
        symbol:      str,
        prev_close:  float,
        today_open:  float,
    ):
        self.symbol     = symbol
        self.prev_close = prev_close
        self.today_open = today_open

        if prev_close > 0 and today_open > 0:
            self.gap_pct = round((today_open - prev_close) / prev_close * 100, 2)
        else:
            self.gap_pct = 0.0

        self.abs_gap   = abs(self.gap_pct)
        self.direction = "UP" if self.gap_pct > 0.1 else ("DOWN" if self.gap_pct < -0.1 else "FLAT")
        self.category  = self._categorize()
        self.wait_min  = GAP_WAIT_MINUTES[self.category]

    def _categorize(self) -> str:
        if self.abs_gap >= THRESHOLDS[GapCategory.EXTREME]:
            return GapCategory.EXTREME
        if self.abs_gap >= THRESHOLDS[GapCategory.LARGE]:
            return GapCategory.LARGE
        if self.abs_gap >= THRESHOLDS[GapCategory.MEDIUM]:
            return GapCategory.MEDIUM
        return GapCategory.SMALL

    def is_safe(self, minutes_since_open: float = 0) -> Tuple[bool, str]:
        """
        Is it safe to trade this symbol right now given the gap?

        Args:
            minutes_since_open: Minutes elapsed since 9:15 AM IST open

        Returns:
            (safe: bool, reason: str)
        """
        if self.category == GapCategory.SMALL:
            return True, ""

        if self.category == GapCategory.EXTREME:
            return False, (
                f"EXTREME gap {self.gap_pct:+.1f}% — avoid entire session "
                f"(unresolvable price discovery risk)"
            )

        if minutes_since_open < self.wait_min:
            remaining = self.wait_min - minutes_since_open
            return False, (
                f"{self.category} gap {self.gap_pct:+.1f}% — "
                f"wait {remaining:.0f} more min for price discovery"
            )

        return True, ""

    def __repr__(self):
        return (
            f"GapInfo({self.symbol}: {self.gap_pct:+.2f}% "
            f"[{self.category}] wait={self.wait_min}min)"
        )


class GapAnalyzer:
    """
    Analyzes pre-market gaps for the watchlist.

    Usage:
        analyzer = GapAnalyzer()
        # At market open — pre-load all gaps
        analyzer.load_gaps_for_watchlist(fetcher, watchlist)

        # Before each signal
        safe, reason = analyzer.is_safe_to_trade("RELIANCE")
    """

    def __init__(self):
        self._gaps: Dict[str, GapInfo] = {}
        self._analysis_date: Optional[date] = None

    def set_gap(self, symbol: str, prev_close: float, today_open: float) -> GapInfo:
        """Manually set gap data for a symbol (from candle data)."""
        info = GapInfo(symbol, prev_close, today_open)
        self._gaps[symbol] = info
        if info.category != GapCategory.SMALL:
            logger.info(
                f"Gap detected: {symbol} {info.direction} {info.gap_pct:+.2f}% "
                f"[{info.category}] — wait {info.wait_min} min"
            )
        return info

    def is_safe_to_trade(self, symbol: str) -> Tuple[bool, str]:
        """
        Check if a symbol is safe to trade given its gap.
        Automatically calculates minutes since open.

        Returns:
            (safe: bool, reason: str)
        """
        gap_info = self._gaps.get(symbol.upper())
        if gap_info is None:
            return True, ""   # No gap data → assume safe (no false positives)

        # Calculate minutes since market open (9:15 AM IST)
        now_ist = get_current_ist_time()
        from datetime import datetime, time as dtime
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        today = now_ist.date()
        market_open_dt = datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)
        minutes_since_open = max(0.0, (now_ist - market_open_dt).total_seconds() / 60)

        return gap_info.is_safe(minutes_since_open)

    def get_gap_pct(self, symbol: str) -> float:
        """Get gap percentage for a symbol (0.0 if not analyzed)."""
        info = self._gaps.get(symbol.upper())
        return info.gap_pct if info else 0.0

    def get_gap_info(self, symbol: str) -> Optional[GapInfo]:
        """Get full GapInfo for a symbol."""
        return self._gaps.get(symbol.upper())

    def load_gaps_for_watchlist(self, data_fetcher, watchlist) -> Dict[str, GapInfo]:
        """
        Pre-load gap data for all watchlist symbols at market open.
        Uses 1-day OHLCV to get yesterday's close and today's open.

        Args:
            data_fetcher: GrowwDataFetcher instance
            watchlist:    list of NSE symbols

        Returns:
            dict of symbol → GapInfo
        """
        today = get_current_ist_date()
        if self._analysis_date == today and self._gaps:
            logger.debug("Gap analysis already done today — using cached data")
            return self._gaps

        loaded = 0
        errors = 0

        for symbol in watchlist:
            try:
                # Get last 3 daily candles: yesterday close + today open
                # get_candles() signature: (symbol, interval, days, from_dt, to_dt)
                df = data_fetcher.get_candles(symbol=symbol, interval="1d", days=3)
                if df is None or len(df) < 2:
                    continue

                prev_close  = float(df["close"].iloc[-2])
                today_open  = float(df["open"].iloc[-1])

                self.set_gap(symbol, prev_close, today_open)
                loaded += 1

            except Exception as e:
                logger.debug(f"Gap load error for {symbol}: {e}")
                errors += 1

        self._analysis_date = today
        extreme_count = sum(1 for g in self._gaps.values() if g.category == GapCategory.EXTREME)
        gapped_count  = sum(1 for g in self._gaps.values() if g.category != GapCategory.SMALL)

        logger.info(
            f"Gap analysis complete: {loaded}/{len(watchlist)} symbols | "
            f"Gapped: {gapped_count} | Extreme: {extreme_count} | Errors: {errors}"
        )
        return self._gaps

    def get_gapped_stocks_summary(self) -> str:
        """Format summary of gapped stocks for Telegram morning brief."""
        gapped = [g for g in self._gaps.values() if g.category != GapCategory.SMALL]
        if not gapped:
            return "No significant gaps today."

        lines = [f"📊 Gap opens ({len(gapped)} stocks):"]
        for g in sorted(gapped, key=lambda x: abs(x.gap_pct), reverse=True)[:10]:
            icon = "🔴" if g.direction == "DOWN" else "🟢"
            lines.append(
                f"  {icon} {g.symbol}: {g.gap_pct:+.1f}% [{g.category}] "
                f"— wait {g.wait_min}min"
            )
        return "\n".join(lines)

    def clear(self):
        """Clear all gap data (call at start of new trading day)."""
        self._gaps.clear()
        self._analysis_date = None


# ── SINGLETON ──────────────────────────────────────────────────────────────

_gap_analyzer_instance: Optional[GapAnalyzer] = None


def get_gap_analyzer() -> GapAnalyzer:
    """Return singleton GapAnalyzer instance."""
    global _gap_analyzer_instance
    if _gap_analyzer_instance is None:
        _gap_analyzer_instance = GapAnalyzer()
    return _gap_analyzer_instance


def is_gap_safe(symbol: str) -> Tuple[bool, str]:
    """Quick helper: (safe, reason) for gap check."""
    return get_gap_analyzer().is_safe_to_trade(symbol)


# ── SELF-TEST ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Gap Analyzer Test ===")
    analyzer = GapAnalyzer()

    # Simulate various gap scenarios
    test_cases = [
        ("RELIANCE",  1500.00, 1505.00),   # +0.33% — SMALL
        ("HDFCBANK",  1700.00, 1738.00),   # +2.24% — MEDIUM
        ("TATAMOTORS", 800.00,  830.00),   # +3.75% — LARGE
        ("ZOMATO",     220.00,  235.00),   # +6.82% — EXTREME
        ("TCS",       3500.00, 3430.00),   # -2.00% — MEDIUM down
    ]

    for sym, prev, today in test_cases:
        info = analyzer.set_gap(sym, prev, today)
        safe_now, reason = info.is_safe(minutes_since_open=0)
        safe_later, reason2 = info.is_safe(minutes_since_open=20)
        print(
            f"\n{sym}: {info.gap_pct:+.2f}% [{info.category}]"
            f"\n  At open (0 min): {'✅ Safe' if safe_now else f'⛔ {reason}'}"
            f"\n  After 20 min:   {'✅ Safe' if safe_later else f'⛔ {reason2}'}"
        )

    print("\n" + analyzer.get_gapped_stocks_summary())


# ============================================================
# MODULE: overnight_analyzer.py
# ============================================================
"""
overnight_analyzer.py — NSE Momentum Groww AI Bot
Overnight & Pre-Market Intelligence Engine

Runs at 8:00–8:45 AM IST BEFORE market opens.
Fetches global market cues and builds today's trading bias.

What it checks:
1. US markets close (S&P500, Nasdaq, Dow) — biggest influence on NSE
2. Asian markets (Nikkei, Hang Seng, SGX/Gift Nifty) — morning cues
3. Crude oil price — impacts ONGC, BPCL, RELIANCE, aviation stocks
4. Gold price — risk-off indicator
5. USD/INR — FII flows indicator
6. Gift Nifty futures — direct NSE open predictor
7. VIX India — fear gauge (high VIX = volatile, dangerous day)
8. Global news headlines from Reuters/Bloomberg

18yr Rule: "The night before a trade is as important as the trade itself.
Know what happened globally BEFORE you place a single order."
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

OVERNIGHT_DIR = Path("logs/overnight")
OVERNIGHT_DIR.mkdir(parents=True, exist_ok=True)


class OvernightAnalyzer:
    """
    Fetches and analyses global overnight data to build NSE morning bias.
    Runs daily at 8:00 AM IST (before Groww login at 8:45 AM IST).
    """

    def __init__(self):
        self._today_analysis: Optional[Dict] = None
        self._cache_path = OVERNIGHT_DIR / f"analysis_{get_current_ist_date()}.json"
        # Try loading existing analysis for today
        if self._cache_path.exists():
            try:
                with open(self._cache_path) as f:
                    self._today_analysis = json.load(f)
                logger.info(f"[{format_ist_timestamp()}] Loaded cached overnight analysis")
            except Exception:
                pass

    # --------------------------------------------------------
    # MAIN ANALYSIS RUNNER
    # --------------------------------------------------------

    def run(self, ai_brain=None) -> Dict:
        """
        Full overnight analysis. Returns comprehensive bias dict.
        Call at 8:00–8:30 AM IST. Results cached for the day.
        """
        logger.info(f"[{format_ist_timestamp()}] Running overnight analysis...")

        result = {
            "date_ist":       str(get_current_ist_date()),
            "generated_at":   format_ist_timestamp(),
            "global_markets": {},
            "gift_nifty":     {},
            "commodities":    {},
            "fx":             {},
            "vix":            {},
            "gap_analysis":   {},
            "day_bias":       "NEUTRAL",
            "bias_score":     0,      # -100 to +100 (negative=bearish, positive=bullish)
            "confidence":     50,
            "key_risks":      [],
            "sectors_favour": [],
            "sectors_avoid":  [],
            "ai_thesis":      "",
            "trade_advice":   "",
        }

        bias_score = 0

        # 1. Fetch global market data
        global_data = self._fetch_global_markets()
        result["global_markets"] = global_data

        # Score global markets
        sp500  = global_data.get("sp500_change",   0)
        nasdaq = global_data.get("nasdaq_change",  0)
        nikkei = global_data.get("nikkei_change",  0)
        hsi    = global_data.get("hangseng_change",0)

        if sp500 > 0.5:    bias_score += 15
        elif sp500 < -0.5: bias_score -= 20   # US fall hits NSE harder than US rise helps
        if nasdaq > 0.5:   bias_score += 10
        elif nasdaq < -1:  bias_score -= 15
        if nikkei > 0.5:   bias_score += 8
        elif nikkei < -1:  bias_score -= 10
        if hsi > 0:        bias_score += 5
        elif hsi < -1:     bias_score -= 8

        # 2. Gift Nifty (most direct predictor)
        gift = self._fetch_gift_nifty()
        result["gift_nifty"] = gift
        gap_pct = gift.get("gap_pct", 0)
        if gap_pct > 0.5:    bias_score += 20
        elif gap_pct > 0.2:  bias_score += 10
        elif gap_pct < -0.5: bias_score -= 25
        elif gap_pct < -0.2: bias_score -= 12

        # Gap analysis (extreme gaps often fade)
        if abs(gap_pct) > 1.5:
            result["gap_analysis"] = {
                "type":   "LARGE_GAP",
                "pct":    gap_pct,
                "advice": (
                    "Gap >1.5%: Wait 15-20 min for equilibrium. "
                    "Trade WITH gap after pullback, not immediately at open."
                    if gap_pct > 0 else
                    "Gap down >1.5%: Don't short immediately at open — wait for bounce/rejection first."
                )
            }
            result["key_risks"].append(f"Large gap {'up' if gap_pct > 0 else 'down'} {gap_pct:+.1f}% — choppy open likely")
        else:
            result["gap_analysis"] = {
                "type":   "NORMAL_OPEN",
                "pct":    gap_pct,
                "advice": "Normal open. Wait for ORB (9:15-9:30 AM) to set direction."
            }

        # 3. Commodities
        comms = self._fetch_commodities()
        result["commodities"] = comms
        crude = comms.get("crude_change", 0)
        gold  = comms.get("gold_change", 0)

        if crude > 2:
            bias_score -= 5   # High crude = bad for import-heavy India
            result["sectors_avoid"].append("AVIATION")
            result["sectors_favour"].append("ENERGY")
            result["key_risks"].append(f"Crude up {crude:+.1f}% — inflation risk, avoid aviation/paints")
        elif crude < -2:
            bias_score += 5
            result["sectors_favour"].append("AVIATION")
            result["sectors_avoid"].append("ENERGY")

        if gold > 1:
            result["key_risks"].append("Gold up — risk-off signal. Reduce position sizes.")
            bias_score -= 5

        # 4. USD/INR (Rupee strength)
        fx = self._fetch_fx()
        result["fx"] = fx
        usdinr_change = fx.get("usdinr_change", 0)
        if usdinr_change > 0.3:   # Rupee weakening (bad for FII inflows)
            bias_score -= 8
            result["key_risks"].append(f"Rupee weakening ({usdinr_change:+.2f}%) — FII may sell")
        elif usdinr_change < -0.3:  # Rupee strengthening (good)
            bias_score += 5

        # 5. India VIX
        vix_data = self._fetch_india_vix()
        result["vix"] = vix_data
        vix = vix_data.get("vix", 15)
        if vix > 22:
            result["key_risks"].append(f"India VIX = {vix:.1f} (HIGH) — use 50% position size today")
            bias_score -= 10
        elif vix > 18:
            result["key_risks"].append(f"India VIX = {vix:.1f} (ELEVATED) — widen stops")
            bias_score -= 5
        elif vix < 12:
            result["key_risks"].append(f"India VIX = {vix:.1f} (LOW) — low volatility, fewer signals")

        # 6. Final bias
        result["bias_score"] = bias_score
        if bias_score >= 20:
            result["day_bias"]    = "BULLISH"
            result["confidence"]  = min(50 + bias_score, 90)
            result["trade_advice"] = "Prefer LONG setups. Buy dips toward VWAP. Avoid shorts."
        elif bias_score <= -20:
            result["day_bias"]    = "BEARISH"
            result["confidence"]  = min(50 + abs(bias_score), 90)
            result["trade_advice"] = "Prefer SHORT setups. Sell rallies. Reduce position sizes."
        else:
            result["day_bias"]    = "NEUTRAL"
            result["confidence"]  = 50
            result["trade_advice"] = "No clear bias. Wait for ORB. Trade only high-confidence setups (score > 75)."

        # 7. AI thesis (if available)
        if ai_brain:
            try:
                asian = {
                    "Nikkei": nikkei,
                    "HangSeng": hsi,
                    "SGX/Gift": gap_pct,
                }
                thesis = ai_brain.generate_morning_thesis(
                    nifty_prev_close = gift.get("prev_close", 0),
                    gift_nifty       = gift.get("gift_nifty", 0),
                    us_market_change = sp500,
                    asian_markets    = asian,
                    top_news         = self._fetch_top_headlines(),
                    economic_events  = [],
                )
                result["ai_thesis"]   = thesis.get("thesis", "")
                result["day_bias"]    = thesis.get("bias", result["day_bias"])
                result["confidence"]  = thesis.get("confidence", result["confidence"])
                # Merge AI sector recommendations
                for s in thesis.get("sectors_buy", []):
                    if s not in result["sectors_favour"]:
                        result["sectors_favour"].append(s)
                for s in thesis.get("sectors_avoid", []):
                    if s not in result["sectors_avoid"]:
                        result["sectors_avoid"].append(s)
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] AI thesis failed: {e}")

        # Cache result
        self._today_analysis = result
        try:
            with open(self._cache_path, "w") as f:
                json.dump(result, f, indent=2, default=str)
        except Exception:
            pass

        self._log_summary(result)
        return result

    # --------------------------------------------------------
    # DATA FETCHERS (using yfinance as free data source)
    # --------------------------------------------------------

    def _fetch_global_markets(self) -> Dict:
        """Fetch US + Asian market data via yfinance."""
        result = {}
        try:
            import yfinance as yf
            tickers = {
                "sp500":   "^GSPC",
                "nasdaq":  "^IXIC",
                "dow":     "^DJI",
                "nikkei":  "^N225",
                "hangseng":"^HSI",
                "ftse":    "^FTSE",
            }
            for name, ticker in tickers.items():
                try:
                    data = yf.download(ticker, period="2d", interval="1d",
                                       progress=False, auto_adjust=True)
                    if len(data) >= 2:
                        prev  = float(data["Close"].iloc[-2])
                        last  = float(data["Close"].iloc[-1])
                        chg   = (last - prev) / prev * 100
                        result[f"{name}_close"]  = round(last, 2)
                        result[f"{name}_change"] = round(chg, 2)
                except Exception:
                    result[f"{name}_change"] = 0
        except ImportError:
            logger.warning("yfinance not installed. pip install yfinance")
            result = {"sp500_change": 0, "nasdaq_change": 0, "nikkei_change": 0, "hangseng_change": 0}
        return result

    def _fetch_gift_nifty(self) -> Dict:
        """
        Gift Nifty (formerly SGX Nifty) — best pre-market Nifty predictor.
        Available from ~6 AM IST. Uses yfinance as proxy (Nifty futures).
        """
        result = {"gift_nifty": 0, "prev_close": 0, "gap_pct": 0}
        try:
            import yfinance as yf
            # Use Nifty50 index as proxy since Gift Nifty isn't on yfinance
            nifty = yf.download("^NSEI", period="5d", interval="1d",
                                 progress=False, auto_adjust=True)
            if len(nifty) >= 1:
                prev_close = float(nifty["Close"].iloc[-1])
                result["prev_close"] = round(prev_close, 2)
                result["gift_nifty"] = round(prev_close, 2)  # Placeholder until Gift Nifty API

                # Try to get Gift Nifty from investing.com via requests
                try:
                    import requests
                    resp = requests.get(
                        "https://query1.finance.yahoo.com/v8/finance/chart/NIFTY50.NS",
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=5
                    )
                    if resp.status_code == 200:
                        data  = resp.json()
                        price = data["chart"]["result"][0]["meta"].get("regularMarketPrice", prev_close)
                        result["gift_nifty"] = round(float(price), 2)
                        gap   = (float(price) - prev_close) / prev_close * 100
                        result["gap_pct"] = round(gap, 2)
                except Exception:
                    result["gap_pct"] = 0
        except Exception as e:
            logger.debug(f"Gift Nifty fetch failed: {e}")
        return result

    def _fetch_commodities(self) -> Dict:
        """Fetch crude oil (Brent) and gold prices."""
        result = {"crude_change": 0, "gold_change": 0, "crude_price": 0, "gold_price": 0}
        try:
            import yfinance as yf
            for name, ticker in [("crude", "BZ=F"), ("gold", "GC=F")]:
                try:
                    data = yf.download(ticker, period="2d", interval="1d",
                                       progress=False, auto_adjust=True)
                    if len(data) >= 2:
                        prev = float(data["Close"].iloc[-2])
                        last = float(data["Close"].iloc[-1])
                        result[f"{name}_price"]  = round(last, 2)
                        result[f"{name}_change"] = round((last - prev) / prev * 100, 2)
                except Exception:
                    pass
        except ImportError:
            pass
        return result

    def _fetch_fx(self) -> Dict:
        """Fetch USD/INR exchange rate."""
        result = {"usdinr": 0, "usdinr_change": 0}
        try:
            import yfinance as yf
            data = yf.download("INR=X", period="2d", interval="1d",
                               progress=False, auto_adjust=True)
            if len(data) >= 2:
                prev = float(data["Close"].iloc[-2])
                last = float(data["Close"].iloc[-1])
                result["usdinr"]        = round(last, 4)
                result["usdinr_change"] = round((last - prev) / prev * 100, 3)
        except Exception as e:
            logger.debug(f"FX fetch failed: {e}")
        return result

    def _fetch_india_vix(self) -> Dict:
        """Fetch India VIX — volatility fear gauge."""
        result = {"vix": 15, "vix_change": 0}
        try:
            import yfinance as yf
            data = yf.download("^INDIAVIX", period="2d", interval="1d",
                               progress=False, auto_adjust=True)
            if len(data) >= 2:
                prev = float(data["Close"].iloc[-2])
                last = float(data["Close"].iloc[-1])
                result["vix"]        = round(last, 2)
                result["vix_change"] = round(last - prev, 2)
            elif len(data) == 1:
                result["vix"] = round(float(data["Close"].iloc[-1]), 2)
        except Exception as e:
            logger.debug(f"VIX fetch failed: {e}")
        return result

    def _fetch_top_headlines(self) -> List[str]:
        """Fetch top market headlines from NewsAPI."""
        headlines = []
        api_key = os.getenv("NEWS_API_KEY", "")
        if not api_key:
            return ["No news API key configured"]
        try:
            import requests
            resp = requests.get(
                "https://newsapi.org/v2/top-headlines",
                params={
                    "q": "NSE OR Nifty OR RBI OR India stock market",
                    "language": "en",
                    "pageSize": 5,
                    "apiKey": api_key,
                },
                timeout=8
            )
            if resp.status_code == 200:
                articles = resp.json().get("articles", [])
                headlines = [a.get("title", "") for a in articles[:5]]
        except Exception as e:
            logger.debug(f"Headlines fetch failed: {e}")
        return headlines

    # --------------------------------------------------------
    # QUERY HELPERS (used during trading day)
    # --------------------------------------------------------

    def get_day_bias(self) -> str:
        """Current day's bias: BULLISH / BEARISH / NEUTRAL."""
        if self._today_analysis:
            return self._today_analysis.get("day_bias", "NEUTRAL")
        return "NEUTRAL"

    def get_bias_score(self) -> int:
        """Raw bias score from -100 to +100."""
        if self._today_analysis:
            return self._today_analysis.get("bias_score", 0)
        return 0

    def get_vix(self) -> float:
        if self._today_analysis:
            return self._today_analysis.get("vix", {}).get("vix", 15)
        return 15

    def get_gap_pct(self) -> float:
        if self._today_analysis:
            return self._today_analysis.get("gift_nifty", {}).get("gap_pct", 0)
        return 0

    def get_size_multiplier(self) -> float:
        """
        Position size multiplier based on overnight risk.
        High VIX or large gap = smaller size.
        """
        vix  = self.get_vix()
        bias = abs(self.get_bias_score())

        if vix > 22:
            return 0.5   # Very dangerous — half size
        elif vix > 18:
            return 0.7
        elif bias > 30:
            return 1.1   # Strong overnight signal — slight boost
        return 1.0

    def get_sectors_to_favour(self) -> List[str]:
        if self._today_analysis:
            return self._today_analysis.get("sectors_favour", [])
        return []

    def get_sectors_to_avoid(self) -> List[str]:
        if self._today_analysis:
            return self._today_analysis.get("sectors_avoid", [])
        return []

    def get_key_risks(self) -> List[str]:
        if self._today_analysis:
            return self._today_analysis.get("key_risks", [])
        return []

    def format_morning_brief(self) -> str:
        """Telegram-ready morning brief message."""
        if not self._today_analysis:
            return "📊 Overnight analysis not yet available."
        a = self._today_analysis
        bias   = a.get("day_bias", "NEUTRAL")
        score  = a.get("bias_score", 0)
        conf   = a.get("confidence", 50)
        gm     = a.get("global_markets", {})
        vix    = a.get("vix", {}).get("vix", 15)
        gap    = a.get("gift_nifty", {}).get("gap_pct", 0)
        crude  = a.get("commodities", {}).get("crude_change", 0)
        usdinr = a.get("fx", {}).get("usdinr_change", 0)
        risks  = a.get("key_risks", [])
        advice = a.get("trade_advice", "")
        thesis = a.get("ai_thesis", "")

        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")

        msg = (
            f"🌅 MORNING BRIEF — {get_current_ist_date()}\n"
            f"{'─'*38}\n"
            f"{bias_emoji} Bias: {bias} (Score: {score:+d}, Conf: {conf}%)\n"
            f"🎯 Gift Nifty Gap: {gap:+.2f}%\n"
            f"🌍 US: S&P {gm.get('sp500_change',0):+.1f}%  Nasdaq {gm.get('nasdaq_change',0):+.1f}%\n"
            f"🗾 Asia: Nikkei {gm.get('nikkei_change',0):+.1f}%  HangSeng {gm.get('hangseng_change',0):+.1f}%\n"
            f"🛢 Crude: {crude:+.1f}%  💵 USD/INR: {usdinr:+.3f}%  😨 VIX: {vix:.1f}\n"
        )
        if risks:
            msg += f"⚠️ Risks:\n" + "\n".join(f"  • {r}" for r in risks[:3]) + "\n"
        msg += f"💡 {advice}\n"
        if thesis:
            msg += f"\n🧠 AI: {thesis[:200]}"
        return msg

    def _log_summary(self, result: Dict):
        logger.info(
            f"[{format_ist_timestamp()}] Overnight: "
            f"bias={result['day_bias']}({result['bias_score']:+d}) "
            f"gap={result.get('gift_nifty',{}).get('gap_pct',0):+.1f}% "
            f"VIX={result.get('vix',{}).get('vix',15):.1f} "
            f"US={result.get('global_markets',{}).get('sp500_change',0):+.1f}%"
        )


# Singleton
_analyzer: Optional[OvernightAnalyzer] = None

def get_overnight_analyzer() -> OvernightAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = OvernightAnalyzer()
    return _analyzer


# ============================================================
# MODULE: economic_calendar.py
# ============================================================
"""
economic_calendar.py — NSE Momentum Groww AI Bot
NSE Economic Event Calendar + Impact Learning Engine

Tracks high-impact events and learns their historical market impact.
18yr Rule: "Know the calendar before the market opens.
One RBI announcement can wipe out a week of gains in 30 minutes."

Events tracked:
- RBI Monetary Policy (6x per year) — BIGGEST mover
- Union Budget (Feb 1) — most volatile day of year
- GDP/CPI/WPI releases
- NSE F&O expiry (last Thursday monthly) — manipulation zone
- Nifty50 earnings (Apr/Jul/Oct/Jan quarters)
- US Fed FOMC (8x per year) — impacts FII flows
- SGX/Gift Nifty settlement days
- NSE/BSE circuit breaker history
"""

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

CALENDAR_DB = Path("data/economic_calendar.db")
CALENDAR_DB.parent.mkdir(exist_ok=True)

# ── Hard-coded high-impact NSE events 2025-2026 ────────────────────────────
HARDCODED_EVENTS = [
    # RBI MPC (Monetary Policy Committee) dates 2025
    {"date": "2025-04-09", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2025-06-06", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2025-08-08", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2025-10-08", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2025-12-05", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2026-02-06", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2026-04-09", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    # Budget
    {"date": "2026-02-01", "event": "Union Budget 2026-27",    "impact": "EXTREME","avoid_minutes": 120},
    # US Fed FOMC 2025 (approximate)
    {"date": "2025-05-07", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-06-18", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-07-30", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-09-17", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-11-05", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-12-17", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    # India GDP/CPI
    {"date": "2025-05-30", "event": "India GDP Q4 FY25",       "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-08-29", "event": "India GDP Q1 FY26",       "impact": "MEDIUM","avoid_minutes": 30},
    # NSE holidays 2025
    {"date": "2025-04-14", "event": "NSE Holiday - Ambedkar Jayanti", "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-04-18", "event": "NSE Holiday - Good Friday",      "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-05-01", "event": "NSE Holiday - Maharashtra Day",  "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-08-15", "event": "NSE Holiday - Independence Day", "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-10-02", "event": "NSE Holiday - Gandhi Jayanti",   "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-10-24", "event": "NSE Holiday - Dussehra",         "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-11-05", "event": "NSE Holiday - Diwali Laxmi Puja","impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-12-25", "event": "NSE Holiday - Christmas",        "impact": "HOLIDAY", "avoid_minutes": 0},
]

# F&O expiry is always last Thursday of each month
def _get_monthly_expiry_dates(start_year: int = 2025, months: int = 24) -> List[Dict]:
    events = []
    from calendar import monthrange
    today = date.today()
    for m in range(months):
        year  = start_year + (m // 12)
        month = (m % 12) + 1
        # Find last Thursday (weekday=3)
        last_day = monthrange(year, month)[1]
        for d in range(last_day, 0, -1):
            if date(year, month, d).weekday() == 3:
                events.append({
                    "date":           str(date(year, month, d)),
                    "event":          "NSE F&O Monthly Expiry",
                    "impact":         "MEDIUM",
                    "avoid_minutes":  45,
                })
                break
    return events


class EconomicCalendar:
    """
    Manages the economic event calendar with impact learning.
    Learns how each event type historically affected NSE.
    """

    def __init__(self):
        self._init_db()
        self._seed_events()

    def _init_db(self):
        with sqlite3.connect(CALENDAR_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_ist       TEXT NOT NULL,
                    event_name     TEXT NOT NULL,
                    impact_level   TEXT DEFAULT 'MEDIUM',
                    avoid_minutes  INTEGER DEFAULT 30,
                    time_ist       TEXT DEFAULT '10:00',
                    source         TEXT DEFAULT 'hardcoded'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_outcomes (
                    event_name     TEXT NOT NULL,
                    date_ist       TEXT NOT NULL,
                    nifty_change   REAL,
                    vix_change     REAL,
                    intraday_range REAL,
                    outcome_note   TEXT,
                    recorded_at    TEXT,
                    PRIMARY KEY (event_name, date_ist)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_impact_stats (
                    event_name     TEXT PRIMARY KEY,
                    avg_nifty_move REAL,
                    avg_range      REAL,
                    bearish_count  INTEGER DEFAULT 0,
                    bullish_count  INTEGER DEFAULT 0,
                    total_count    INTEGER DEFAULT 0,
                    last_updated   TEXT
                )
            """)
            conn.commit()

    def _seed_events(self):
        """Populate DB with hardcoded events + F&O expiry dates."""
        all_events = HARDCODED_EVENTS + _get_monthly_expiry_dates()
        with sqlite3.connect(CALENDAR_DB) as conn:
            for ev in all_events:
                conn.execute(
                    "INSERT OR IGNORE INTO events (date_ist, event_name, impact_level, avoid_minutes) "
                    "VALUES (?,?,?,?)",
                    (ev["date"], ev["event"], ev["impact"], ev.get("avoid_minutes", 30))
                )
            conn.commit()

    # ── QUERY ──────────────────────────────────────────────

    def get_today_events(self) -> List[Dict]:
        today = str(get_current_ist_date())
        with sqlite3.connect(CALENDAR_DB) as conn:
            rows = conn.execute(
                "SELECT date_ist, event_name, impact_level, avoid_minutes, time_ist "
                "FROM events WHERE date_ist=? ORDER BY time_ist",
                (today,)
            ).fetchall()
        return [{"date": r[0], "event": r[1], "impact": r[2],
                 "avoid_minutes": r[3], "time_ist": r[4]} for r in rows]

    def get_upcoming_events(self, days: int = 7) -> List[Dict]:
        today  = str(get_current_ist_date())
        future = str(get_current_ist_date() + timedelta(days=days))
        with sqlite3.connect(CALENDAR_DB) as conn:
            rows = conn.execute(
                "SELECT date_ist, event_name, impact_level, avoid_minutes "
                "FROM events WHERE date_ist BETWEEN ? AND ? ORDER BY date_ist",
                (today, future)
            ).fetchall()
        return [{"date": r[0], "event": r[1], "impact": r[2], "avoid_minutes": r[3]} for r in rows]

    def is_blackout_now(self) -> Tuple[bool, str]:
        """
        Check if current IST time is within blackout window of any event.
        Returns (True, reason) or (False, "").
        """
        events = self.get_today_events()
        if not events:
            return False, ""
        now_ist = get_current_ist_time()
        for ev in events:
            if ev["impact"] == "HOLIDAY":
                return True, f"NSE Holiday: {ev['event']}"
            try:
                h, m    = map(int, ev["time_ist"].split(":"))
                ev_time = now_ist.replace(hour=h, minute=m, second=0)
                window  = ev.get("avoid_minutes", 30)
                start   = ev_time - timedelta(minutes=window)
                end_t   = ev_time + timedelta(minutes=window)
                if start <= now_ist <= end_t:
                    return True, (
                        f"Event blackout: {ev['event']} "
                        f"({window}min window around {ev['time_ist']} IST)"
                    )
            except Exception:
                pass
        return False, ""

    def is_fno_expiry_today(self) -> bool:
        events = self.get_today_events()
        return any("Expiry" in ev["event"] for ev in events)

    def is_holiday_today(self) -> bool:
        events = self.get_today_events()
        return any(ev["impact"] == "HOLIDAY" for ev in events)

    # ── LEARNING: record what actually happened ─────────────

    def record_event_outcome(self, event_name: str, nifty_change: float,
                              intraday_range: float, note: str = ""):
        today = str(get_current_ist_date())
        with sqlite3.connect(CALENDAR_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO event_outcomes VALUES (?,?,?,NULL,?,?,?)",
                (event_name, today, nifty_change, intraday_range, note, format_ist_timestamp())
            )
            # Update rolling stats
            conn.execute("""
                INSERT INTO event_impact_stats
                    (event_name, avg_nifty_move, avg_range,
                     bearish_count, bullish_count, total_count, last_updated)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(event_name) DO UPDATE SET
                    avg_nifty_move = (avg_nifty_move * total_count + excluded.avg_nifty_move)
                                     / (total_count + 1),
                    avg_range      = (avg_range * total_count + excluded.avg_range)
                                     / (total_count + 1),
                    bearish_count  = bearish_count + excluded.bearish_count,
                    bullish_count  = bullish_count + excluded.bullish_count,
                    total_count    = total_count + 1,
                    last_updated   = excluded.last_updated
            """, (
                event_name,
                abs(nifty_change), intraday_range,
                1 if nifty_change < 0 else 0,
                1 if nifty_change > 0 else 0,
                format_ist_timestamp(),
            ))
            conn.commit()

    def get_event_impact_stats(self, event_name: str) -> Optional[Dict]:
        with sqlite3.connect(CALENDAR_DB) as conn:
            row = conn.execute(
                "SELECT * FROM event_impact_stats WHERE event_name=?",
                (event_name,)
            ).fetchone()
        if row:
            return {
                "event":        row[0], "avg_move":   row[1],
                "avg_range":    row[2], "bearish_pct": row[3] / max(row[5], 1) * 100,
                "total_events": row[5],
            }
        return None

    def format_upcoming_events(self) -> str:
        events = self.get_upcoming_events(days=7)
        if not events:
            return "📅 No major events in next 7 days"
        lines = ["📅 Upcoming Events (7 days):"]
        for ev in events:
            icon = {"HIGH": "🔴", "EXTREME": "🚨", "MEDIUM": "🟡", "HOLIDAY": "⛔"}.get(ev["impact"], "📌")
            lines.append(f"  {icon} {ev['date']}: {ev['event']} ({ev['impact']})")
        return "\n".join(lines)

    # ── FETCH LIVE EVENTS FROM INVESTING.COM RSS ────────────

    def refresh_from_web(self):
        """Pull upcoming economic events from free RSS feeds."""
        try:
            import feedparser
            feed = feedparser.parse("https://in.investing.com/rss/market_overview_Fundamental_Analysis.rss")
            today = get_current_ist_date()
            with sqlite3.connect(CALENDAR_DB) as conn:
                for entry in feed.entries[:20]:
                    title = entry.get("title", "")
                    if any(kw in title.upper() for kw in ["RBI", "CPI", "GDP", "INFLATION", "BUDGET", "FOMC"]):
                        impact = "HIGH" if any(k in title.upper() for k in ["RBI", "BUDGET", "FOMC"]) else "MEDIUM"
                        conn.execute(
                            "INSERT OR IGNORE INTO events (date_ist, event_name, impact_level, source) "
                            "VALUES (?,?,?,'rss')",
                            (str(today), title[:100], impact)
                        )
                conn.commit()
            logger.info(f"[{format_ist_timestamp()}] Calendar refreshed from RSS")
        except Exception as e:
            logger.debug(f"Calendar RSS refresh failed: {e}")


# Singleton
_calendar: Optional[EconomicCalendar] = None

def get_calendar() -> EconomicCalendar:
    global _calendar
    if _calendar is None:
        _calendar = EconomicCalendar()
    return _calendar


# ============================================================
# MODULE: corporate_actions.py
# ============================================================
"""
corporate_actions.py — NSE Momentum Groww AI Bot
Corporate Actions Filter — Ex-Dividend, Bonus, Split Detection

Purpose: Avoid trading stocks near corporate action ex-dates.

WHY THIS MATTERS (18yr lesson):
  - Ex-dividend: Stock drops by dividend amount on ex-date → false bearish signal
  - Bonus issue: Price adjusts sharply → invalid technical levels
  - Stock split: All price-based indicators reset → unreliable signals
  - Rights issue: Uncertainty / volatility before ex-date
  Buffer rule: Skip stocks within 2 trading days of any ex-date.

Source: NSE public corporate actions API (no auth required, browser headers)
Cache: Refreshed once daily, stored in data/corp_actions_cache.json
Fallback: Empty actions (safe — no false positives)
"""

import json
import logging
import requests
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils import get_current_ist_date

logger = logging.getLogger(__name__)

CORP_CACHE_FILE = Path("data/corp_actions_cache.json")

# NSE API — corporate actions for equities (public, no login)
NSE_CORP_API = (
    "https://www.nseindia.com/api/corporates-corporateActions"
    "?index=equities&from_date={from_date}&to_date={to_date}"
)

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
}

# Action types that affect price significantly
PRICE_IMPACT_ACTIONS = {
    "DIVIDEND", "INTERIM DIVIDEND", "FINAL DIVIDEND",
    "BONUS", "SPLIT", "RIGHTS", "BUYBACK",
    "AMALGAMATION", "DEMERGER", "FACE VALUE CHANGE",
}

# Buffer: skip stocks within this many days of ex-date
CORP_ACTION_BUFFER_DAYS = 2


class CorporateActionsFilter:
    """
    Tracks NSE corporate actions and blocks trades near ex-dates.

    Prevents:
    - False bearish signals on ex-dividend days (price drops by dividend)
    - Misleading technical levels around bonus/split price adjustments
    - Unexpected volatility from rights/buyback corporate events
    """

    def __init__(self, buffer_days: int = CORP_ACTION_BUFFER_DAYS):
        self.buffer_days = buffer_days
        self._actions: Dict[str, List[dict]] = {}   # symbol → [action, ...]
        self._cache_date: Optional[date] = None
        self._load_cache()

    # ── CACHE ──────────────────────────────────────────────────

    def _load_cache(self):
        """Load corporate actions from disk if today's cache exists."""
        try:
            if CORP_CACHE_FILE.exists():
                with open(CORP_CACHE_FILE) as f:
                    data = json.load(f)
                cached_date = date.fromisoformat(data.get("date", "2000-01-01"))
                if cached_date == get_current_ist_date():
                    self._actions = data.get("actions", {})
                    self._cache_date = cached_date
                    stocks_with_actions = len(self._actions)
                    logger.info(
                        f"Corp actions loaded from cache: "
                        f"{stocks_with_actions} stocks with upcoming events"
                    )
                    return
        except Exception as e:
            logger.debug(f"Corp actions cache load error: {e}")

        self._refresh()

    # ── NSE FETCH ──────────────────────────────────────────────

    def _refresh(self) -> bool:
        """
        Fetch corporate actions from NSE for the next 7 days.
        Returns True on success, False on failure (uses empty dict fallback).
        """
        try:
            today = get_current_ist_date()
            # Include yesterday (ex-date may already be today's event)
            from_date = (today - timedelta(days=1)).strftime("%d-%m-%Y")
            to_date   = (today + timedelta(days=7)).strftime("%d-%m-%Y")

            session = requests.Session()
            # NSE requires session cookie from homepage first
            session.get(
                "https://www.nseindia.com",
                headers=NSE_HEADERS,
                timeout=10,
            )

            url  = NSE_CORP_API.format(from_date=from_date, to_date=to_date)
            resp = session.get(url, headers=NSE_HEADERS, timeout=15)
            resp.raise_for_status()

            raw_data = resp.json()
            self._parse_actions(raw_data)
            self._cache_date = today
            self._save_cache()

            logger.info(
                f"Corp actions refreshed: {len(self._actions)} stocks "
                f"have events in next 7 days"
            )
            return True

        except Exception as e:
            logger.warning(
                f"Corp actions fetch failed: {e} — "
                "assuming no corporate events (safe fallback)"
            )
            self._actions = {}
            self._cache_date = get_current_ist_date()
            self._save_cache()
            return False

    def _parse_actions(self, raw_data):
        """
        Parse NSE corporate actions JSON response.
        NSE returns a list or dict with 'data' key.
        """
        self._actions = {}
        records = raw_data if isinstance(raw_data, list) else raw_data.get("data", [])

        for record in records:
            symbol = (
                record.get("symbol") or record.get("sym") or ""
            ).upper().strip()
            if not symbol:
                continue

            purpose = (
                record.get("purpose") or record.get("subject") or ""
            ).upper().strip()

            # Check if this action type impacts price
            is_price_impact = any(
                keyword in purpose for keyword in PRICE_IMPACT_ACTIONS
            )
            if not is_price_impact:
                continue

            # Parse ex-date (NSE uses multiple formats)
            ex_date_str = (
                record.get("exDate") or record.get("ex_date") or
                record.get("exdate") or ""
            ).strip()

            if not ex_date_str:
                continue

            ex_date = self._parse_date(ex_date_str)
            if ex_date is None:
                continue

            if symbol not in self._actions:
                self._actions[symbol] = []

            self._actions[symbol].append({
                "ex_date":    str(ex_date),
                "purpose":    purpose,
                "series":     record.get("series", "EQ"),
                "face_value": record.get("faceVal", ""),
            })

    @staticmethod
    def _parse_date(date_str: str) -> Optional[date]:
        """Try multiple date formats used by NSE."""
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%b %d, %Y"):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _save_cache(self):
        """Persist corporate actions to disk cache."""
        try:
            CORP_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CORP_CACHE_FILE, "w") as f:
                json.dump(
                    {
                        "date":    str(self._cache_date),
                        "actions": self._actions,
                        "count":   len(self._actions),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.debug(f"Corp actions cache save error: {e}")

    # ── PUBLIC API ─────────────────────────────────────────────

    def has_upcoming_action(
        self, symbol: str, buffer_days: Optional[int] = None
    ) -> Tuple[bool, str]:
        """
        Check if a stock has a price-impacting corporate action within buffer_days.

        Args:
            symbol:      NSE symbol to check
            buffer_days: Days around ex-date to block (default: self.buffer_days)

        Returns:
            (has_action: bool, reason: str)
            has_action=True means DO NOT TRADE this stock.
        """
        today = get_current_ist_date()
        if self._cache_date != today:
            self._refresh()

        buf = buffer_days if buffer_days is not None else self.buffer_days
        actions = self._actions.get(symbol.upper(), [])

        if not actions:
            return False, ""

        for action in actions:
            ex_date = self._parse_date(action["ex_date"])
            if ex_date is None:
                continue
            days_diff = (ex_date - today).days
            # Block if within buffer (before OR on the ex-date)
            if -1 <= days_diff <= buf:
                purpose = action.get("purpose", "CORPORATE ACTION")
                return True, (
                    f"{purpose} ex-date {action['ex_date']} "
                    f"({'+' if days_diff >= 0 else ''}{days_diff} days)"
                )

        return False, ""

    def is_safe_to_trade(self, symbol: str) -> Tuple[bool, str]:
        """
        Convenience wrapper.

        Returns:
            (safe: bool, reason: str)
            safe=True → no corporate event blocking this stock.
        """
        has_action, reason = self.has_upcoming_action(symbol)
        return (not has_action), reason

    def get_upcoming_events_today(self) -> List[dict]:
        """Return all stocks with ex-dates today (for morning brief)."""
        today = get_current_ist_date()
        if self._cache_date != today:
            self._refresh()

        events = []
        for symbol, actions in self._actions.items():
            for action in actions:
                ex_date = self._parse_date(action["ex_date"])
                if ex_date == today:
                    events.append({
                        "symbol":  symbol,
                        "purpose": action["purpose"],
                        "ex_date": action["ex_date"],
                    })
        return events

    def force_refresh(self) -> bool:
        """Force refresh from NSE (ignore cache)."""
        return self._refresh()


# ── SINGLETON ──────────────────────────────────────────────────────────────

_corp_filter_instance: Optional[CorporateActionsFilter] = None


def get_corp_filter() -> CorporateActionsFilter:
    """Return singleton CorporateActionsFilter instance."""
    global _corp_filter_instance
    if _corp_filter_instance is None:
        _corp_filter_instance = CorporateActionsFilter()
    return _corp_filter_instance


def is_safe_from_corp_actions(symbol: str) -> Tuple[bool, str]:
    """Quick helper: (safe, reason) for corporate action check."""
    return get_corp_filter().is_safe_to_trade(symbol)


# ── SELF-TEST ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Corporate Actions Filter Test ===")
    cf = CorporateActionsFilter()
    print(f"Stocks with upcoming events: {len(cf._actions)}")

    # Show today's ex-dates
    today_events = cf.get_upcoming_events_today()
    if today_events:
        print(f"\nToday's ex-dates ({len(today_events)} stocks):")
        for ev in today_events[:10]:
            print(f"  {ev['symbol']:20s}: {ev['purpose']}")
    else:
        print("\nNo ex-dates today.")

    # Test specific stocks
    test_stocks = ["RELIANCE", "TCS", "INFY", "HDFCBANK"]
    print("\nSpot checks:")
    for sym in test_stocks:
        safe, reason = cf.is_safe_to_trade(sym)
        status = "✅ Safe" if safe else f"⚠️ SKIP — {reason}"
        print(f"  {sym:20s}: {status}")


# ============================================================
# MODULE: option_chain.py
# ============================================================
"""
option_chain.py — NSE Momentum Groww AI Bot
NSE F&O Option Chain Analysis Engine

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).

The option chain is THE most powerful edge for NSE intraday trading.
18+ years of experience: institutions hedge via options BEFORE they move spot.
Reading the chain = reading institutional positioning BEFORE the move.

What this module provides:
  • Put-Call Ratio (PCR) — market sentiment gauge
  • Max Pain level — where most options expire worthless (magnetic price)
  • Open Interest (OI) change — fresh positioning vs covering
  • Implied Volatility (IV) skew — direction bias
  • Support/Resistance from highest OI strikes
  • Gamma Exposure (GEX) — market stability or fragility
  • IV Percentile — whether to widen/tighten stops

Data Source: NSE India public API (no auth required, proper headers needed)
  Nifty:     https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY
  BankNifty: https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY
  Stocks:    https://www.nseindia.com/api/option-chain-equities?symbol=SYMBOL

Usage:
  from option_chain import OptionChainAnalyzer
  oc  = OptionChainAnalyzer()
  res = oc.analyze("NIFTY")  # or "BANKNIFTY" or equity symbol
  bias, score = res.direction_bias, res.confidence_score
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import numpy as np

from utils import format_ist_timestamp, get_current_ist_time, retry_with_backoff

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# NSE API endpoints
NSE_INDEX_OC = "https://www.nseindia.com/api/option-chain-indices"
NSE_EQUITY_OC = "https://www.nseindia.com/api/option-chain-equities"
NSE_EXPIRY    = "https://www.nseindia.com/api/option-chain-indices"

# NSE requires browser-like headers to avoid 403
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Origin": "https://www.nseindia.com",
    "Connection": "keep-alive",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

INDEX_SYMBOLS  = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
CACHE_TTL_SECS = 300  # Refresh option chain every 5 minutes


# ============================================================
# RESULT DATACLASS
# ============================================================

@dataclass
class OptionChainResult:
    """Complete option chain analysis result for one symbol."""
    symbol:          str
    spot_price:      float
    expiry:          str           # Nearest weekly/monthly expiry
    pcr:             float         # Put-Call Ratio (OI-based)
    pcr_volume:      float         # PCR by volume
    max_pain:        float         # Strike where most options expire worthless
    resistance_1:    float         # Highest call OI strike (resistance)
    resistance_2:    float         # Second highest call OI strike
    support_1:       float         # Highest put OI strike (support)
    support_2:       float         # Second highest put OI strike
    call_oi_total:   int
    put_oi_total:    int
    call_oi_change:  int           # OI change in calls (fresh shorts = bearish)
    put_oi_change:   int           # OI change in puts (fresh shorts = bullish)
    atm_iv:          float         # At-the-money implied volatility
    iv_skew:         float         # Call IV - Put IV (positive = put demand = bearish hedge)
    gex:             float         # Gamma exposure (positive = stabilising, negative = amplifying)
    direction_bias:  str           # "BULLISH" / "BEARISH" / "NEUTRAL"
    confidence_score: float        # 0-100
    signals:         List[str]     = field(default_factory=list)
    timestamp:       str           = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_ist_timestamp()

    @property
    def pcr_interpretation(self) -> str:
        if self.pcr > 1.5:
            return "EXTREME BULLISH (contrarian)"
        if self.pcr > 1.2:
            return "BULLISH"
        if self.pcr > 0.8:
            return "NEUTRAL"
        if self.pcr > 0.5:
            return "BEARISH"
        return "EXTREME BEARISH (contrarian)"

    def summary(self) -> str:
        return (
            f"OC {self.symbol} | Spot: ₹{self.spot_price:.0f} | "
            f"PCR: {self.pcr:.2f} ({self.pcr_interpretation}) | "
            f"MaxPain: ₹{self.max_pain:.0f} | "
            f"Support: ₹{self.support_1:.0f} | "
            f"Resist: ₹{self.resistance_1:.0f} | "
            f"Bias: {self.direction_bias} ({self.confidence_score:.0f}/100)"
        )


# ============================================================
# MAIN ANALYZER
# ============================================================

class OptionChainAnalyzer:
    """
    NSE Option Chain Analysis Engine.

    Reads the live NSE option chain for NIFTY/BANKNIFTY/equities and
    extracts institutional positioning signals:
      - PCR (Put-Call Ratio): <0.7 bearish, >1.3 bullish, >1.5 extreme bullish
      - Max Pain: price magnet at expiry
      - OI-based support/resistance levels
      - IV skew (put demand > call demand = bearish hedge by institutions)
      - Gamma Exposure (GEX): negative = volatile/trending moves amplified

    Note: NSE blocks non-Indian IPs (e.g., Render Singapore). When blocked,
    the analyzer gracefully returns None and the bot runs without OC data.
    """

    _MAX_SESSION_FAILURES = 3          # Stop retrying after this many 403s
    _SESSION_RETRY_COOLDOWN = 1800     # Seconds between retry attempts (30 min)

    def __init__(self):
        self._session          = requests.Session()
        self._cache:           Dict[str, Tuple[OptionChainResult, datetime]] = {}
        self._session_ok       = False
        self._session_failures = 0
        self._last_init_time   = 0.0   # epoch seconds
        self._permanently_down = False
        self._init_session()

    # ──────────────────────────────────────────────────────
    # SESSION MANAGEMENT
    # ──────────────────────────────────────────────────────

    def _init_session(self):
        """Establish NSE session (required before API calls)."""
        if self._permanently_down:
            return

        now = time.time()
        if now - self._last_init_time < 60:  # Don't hammer NSE faster than 1/min
            return
        self._last_init_time = now

        try:
            self._session.headers.update(NSE_HEADERS)
            # Must visit homepage first — NSE sets required cookies here
            resp = self._session.get(
                "https://www.nseindia.com/",
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                self._session_ok       = True
                self._session_failures = 0
                logger.info(f"[{format_ist_timestamp()}] NSE session established")
            else:
                self._session_failures += 1
                logger.warning(
                    f"[{format_ist_timestamp()}] NSE session status: {resp.status_code} "
                    f"(failure {self._session_failures}/{self._MAX_SESSION_FAILURES})"
                )
                if resp.status_code == 403:
                    # 403 = Render's IP is blocked by NSE. This is permanent — no point retrying.
                    self._permanently_down = True
                    logger.warning(
                        f"[{format_ist_timestamp()}] NSE blocked this server's IP (403). "
                        f"Option chain disabled for session — bot continues without it."
                    )
                else:
                    logger.warning(
                        f"[{format_ist_timestamp()}] NSE session status: {resp.status_code} "
                        f"(failure {self._session_failures}/{self._MAX_SESSION_FAILURES})"
                    )
                    if self._session_failures >= self._MAX_SESSION_FAILURES:
                        self._permanently_down = True
                        logger.warning(
                            f"[{format_ist_timestamp()}] NSE option chain permanently disabled "
                            f"for this session. All other features unaffected."
                        )
        except Exception as e:
            self._session_failures += 1
            logger.warning(f"[{format_ist_timestamp()}] NSE session init failed: {e}")
            self._session_ok = False
            if self._session_failures >= self._MAX_SESSION_FAILURES:
                self._permanently_down = True

    def _refresh_session_if_needed(self) -> bool:
        """Returns True if session is ready, False if unavailable (no exception = no retry spam)."""
        if self._permanently_down:
            return False
        if not self._session_ok:
            if time.time() - self._last_init_time > self._SESSION_RETRY_COOLDOWN:
                self._session_failures = 0
                self._init_session()
        return self._session_ok

    # ──────────────────────────────────────────────────────
    # DATA FETCH
    # ──────────────────────────────────────────────────────

    @retry_with_backoff(max_retries=3, delays=[2, 5, 10])
    def _fetch_raw(self, symbol: str) -> Optional[Dict]:
        """Fetch raw option chain JSON from NSE."""
        if not self._refresh_session_if_needed():
            return None  # Session unavailable — no exception = no retry loop
        is_index = symbol.upper() in INDEX_SYMBOLS
        url      = NSE_INDEX_OC if is_index else NSE_EQUITY_OC
        try:
            resp = self._session.get(
                url,
                params={"symbol": symbol.upper()},
                timeout=20,
            )
            if resp.status_code == 403:
                logger.warning(f"[{format_ist_timestamp()}] NSE 403 — refreshing session")
                self._session_ok = False
                self._init_session()
                time.sleep(2)
                raise Exception("NSE 403 — retry after session refresh")

            resp.raise_for_status()
            data = resp.json()
            return data.get("records", data)
        except Exception as e:
            if self._permanently_down:
                logger.debug(f"OC fetch {symbol}: {e}")
            else:
                logger.warning(f"[{format_ist_timestamp()}] OC fetch {symbol}: {e}")
            raise

    # ──────────────────────────────────────────────────────
    # PARSE + ANALYSE
    # ──────────────────────────────────────────────────────

    def analyze(self, symbol: str = "NIFTY") -> Optional[OptionChainResult]:
        """
        Full option chain analysis for symbol.
        Returns cached result if < 5 minutes old.
        """
        symbol = symbol.upper()

        # Cache check
        if symbol in self._cache:
            result, ts = self._cache[symbol]
            if (datetime.now(IST) - ts).total_seconds() < CACHE_TTL_SECS:
                return result

        try:
            raw = self._fetch_raw(symbol)
            if not raw:
                return None
            result = self._parse_chain(symbol, raw)
            if result:
                self._cache[symbol] = (result, datetime.now(IST))
                logger.info(f"[{format_ist_timestamp()}] {result.summary()}")
            return result
        except Exception as e:
            # Silence repetitive errors when NSE is permanently blocked (UK server IP)
            if self._permanently_down:
                logger.debug(f"OC analyze {symbol}: {e}")
            else:
                logger.warning(f"[{format_ist_timestamp()}] OC analyze {symbol}: {e}")
            return None

    def _parse_chain(self, symbol: str, raw: Dict) -> Optional[OptionChainResult]:
        """Parse NSE option chain response into OptionChainResult."""
        try:
            data          = raw.get("data", [])
            expiry_dates  = raw.get("expiryDates", [])
            spot_price    = float(raw.get("underlyingValue", 0))

            if not data or spot_price == 0:
                return None

            # Use nearest expiry
            nearest_expiry = expiry_dates[0] if expiry_dates else "unknown"

            # Filter to nearest expiry only
            chain_data = [
                d for d in data
                if d.get("expiryDate", "") == nearest_expiry
            ]
            if not chain_data:
                chain_data = data  # fallback: all expiries

            # Build structured rows
            rows = []
            for item in chain_data:
                strike = float(item.get("strikePrice", 0))
                ce     = item.get("CE", {}) or {}
                pe     = item.get("PE", {}) or {}
                if strike == 0:
                    continue
                rows.append({
                    "strike":       strike,
                    "ce_oi":        int(ce.get("openInterest", 0)),
                    "pe_oi":        int(pe.get("openInterest", 0)),
                    "ce_oi_chg":    int(ce.get("changeinOpenInterest", 0)),
                    "pe_oi_chg":    int(pe.get("changeinOpenInterest", 0)),
                    "ce_vol":       int(ce.get("totalTradedVolume", 0)),
                    "pe_vol":       int(pe.get("totalTradedVolume", 0)),
                    "ce_iv":        float(ce.get("impliedVolatility", 0)),
                    "pe_iv":        float(pe.get("impliedVolatility", 0)),
                    "ce_ltp":       float(ce.get("lastPrice", 0)),
                    "pe_ltp":       float(pe.get("lastPrice", 0)),
                    "ce_gamma":     float(ce.get("delta", 0)),  # using delta as proxy
                    "pe_gamma":     float(pe.get("delta", 0)),
                })

            if not rows:
                return None

            df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)

            # ── Core metrics ─────────────────────────────
            total_call_oi = df["ce_oi"].sum()
            total_put_oi  = df["pe_oi"].sum()
            total_call_vol = df["ce_vol"].sum()
            total_put_vol  = df["pe_vol"].sum()

            pcr_oi  = round(total_put_oi  / max(total_call_oi,  1), 3)
            pcr_vol = round(total_put_vol / max(total_call_vol, 1), 3)

            # Max Pain: strike where total option loss is maximum for buyers
            max_pain = self._calc_max_pain(df)

            # Key OI levels
            call_sorted = df.nlargest(5, "ce_oi")
            put_sorted  = df.nlargest(5, "pe_oi")
            resistance_1 = float(call_sorted.iloc[0]["strike"]) if len(call_sorted) > 0 else spot_price
            resistance_2 = float(call_sorted.iloc[1]["strike"]) if len(call_sorted) > 1 else spot_price
            support_1    = float(put_sorted.iloc[0]["strike"])  if len(put_sorted) > 0 else spot_price
            support_2    = float(put_sorted.iloc[1]["strike"])  if len(put_sorted) > 1 else spot_price

            # ATM IV
            df["dist_to_spot"] = abs(df["strike"] - spot_price)
            atm_row = df.loc[df["dist_to_spot"].idxmin()]
            atm_ce_iv = float(atm_row["ce_iv"])
            atm_pe_iv = float(atm_row["pe_iv"])
            atm_iv    = (atm_ce_iv + atm_pe_iv) / 2 if (atm_ce_iv + atm_pe_iv) > 0 else 0.0
            iv_skew   = round(atm_ce_iv - atm_pe_iv, 2)  # +ve = put demand = institutions hedging down

            # OI change
            call_oi_chg = int(df["ce_oi_chg"].sum())
            put_oi_chg  = int(df["pe_oi_chg"].sum())

            # Gamma Exposure (simplified: positive near ATM strikes = pinning force)
            gex = self._calc_gex(df, spot_price)

            # ── Direction bias ────────────────────────────
            bias, confidence, signals = self._determine_bias(
                pcr_oi, pcr_vol, max_pain, spot_price,
                resistance_1, support_1, call_oi_chg, put_oi_chg,
                iv_skew, gex,
            )

            return OptionChainResult(
                symbol=symbol,
                spot_price=spot_price,
                expiry=nearest_expiry,
                pcr=pcr_oi,
                pcr_volume=pcr_vol,
                max_pain=max_pain,
                resistance_1=resistance_1,
                resistance_2=resistance_2,
                support_1=support_1,
                support_2=support_2,
                call_oi_total=int(total_call_oi),
                put_oi_total=int(total_put_oi),
                call_oi_change=call_oi_chg,
                put_oi_change=put_oi_chg,
                atm_iv=round(atm_iv, 2),
                iv_skew=iv_skew,
                gex=round(gex, 2),
                direction_bias=bias,
                confidence_score=round(confidence, 1),
                signals=signals,
            )

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] OC parse error: {e}", exc_info=True)
            return None

    # ──────────────────────────────────────────────────────
    # MAX PAIN CALCULATION
    # ──────────────────────────────────────────────────────

    def _calc_max_pain(self, df: pd.DataFrame) -> float:
        """
        Max Pain: strike where total option premium paid by buyers is maximised
        (i.e., where sellers — market makers — profit most).
        Spot tends to drift toward max pain near expiry.
        """
        strikes   = df["strike"].values
        losses    = []
        for target in strikes:
            # Total loss to call buyers if expires at `target`
            call_loss = sum(max(0, target - s) * df.loc[df["strike"] == s, "ce_oi"].iloc[0]
                            for s in strikes if df.loc[df["strike"] == s, "ce_oi"].iloc[0] > 0)
            # Total loss to put buyers if expires at `target`
            put_loss  = sum(max(0, s - target) * df.loc[df["strike"] == s, "pe_oi"].iloc[0]
                            for s in strikes if df.loc[df["strike"] == s, "pe_oi"].iloc[0] > 0)
            losses.append(call_loss + put_loss)

        max_pain_idx = int(pd.Series(losses).idxmin())
        return float(strikes[max_pain_idx])

    # ──────────────────────────────────────────────────────
    # GAMMA EXPOSURE
    # ──────────────────────────────────────────────────────

    def _calc_gex(self, df: pd.DataFrame, spot: float) -> float:
        """
        Simplified Gamma Exposure.
        Positive GEX = market makers are long gamma → they sell rallies/buy dips → dampening.
        Negative GEX = market makers are short gamma → they buy rallies/sell dips → amplifying.
        For trending moves, negative GEX is better.
        """
        # Only consider strikes ±5% from spot
        near = df[(df["strike"] >= spot * 0.95) & (df["strike"] <= spot * 1.05)].copy()
        if near.empty:
            return 0.0
        # GEX proxy: net call OI - net put OI near ATM (simplified)
        gex = float((near["ce_oi"].sum() - near["pe_oi"].sum()) / 1e5)
        return round(gex, 2)

    # ──────────────────────────────────────────────────────
    # DIRECTION BIAS ENGINE
    # ──────────────────────────────────────────────────────

    def _determine_bias(
        self, pcr: float, pcr_vol: float, max_pain: float, spot: float,
        resistance: float, support: float, call_oi_chg: int, put_oi_chg: int,
        iv_skew: float, gex: float,
    ) -> Tuple[str, float, List[str]]:
        """
        18-year option chain reading methodology:

        BULLISH signals:
        - PCR > 1.2: More puts than calls = contrarian bullish (bears are hedged = bottom)
        - Spot above max pain: Spot will rise toward max pain
        - Fresh put writing (put OI change < 0): Institutions selling puts = floor
        - Positive IV skew: Calls expensive = bull sentiment
        - Spot near put support wall (call support holding)

        BEARISH signals:
        - PCR < 0.7: More calls than puts = retail euphoria = top
        - Spot below max pain: Spot will fall toward max pain
        - Fresh call writing (call OI change < 0): Institutions selling calls = ceiling
        - Negative IV skew: Puts expensive = institutions hedging downside
        - Spot near call resistance wall
        """
        score  = 0.0
        signals: List[str] = []

        # ── PCR analysis ─────────────────────────────────
        if pcr > 1.5:
            score += 20
            signals.append(f"PCR={pcr:.2f} (extreme put writing — contrarian BULLISH)")
        elif pcr > 1.2:
            score += 12
            signals.append(f"PCR={pcr:.2f} (put-heavy — BULLISH bias)")
        elif pcr > 0.9:
            score += 4
            signals.append(f"PCR={pcr:.2f} (balanced — NEUTRAL)")
        elif pcr > 0.6:
            score -= 12
            signals.append(f"PCR={pcr:.2f} (call-heavy — BEARISH bias)")
        else:
            score -= 20
            signals.append(f"PCR={pcr:.2f} (extreme call writing — contrarian BEARISH)")

        # ── Max Pain magnetic pull ────────────────────────
        pain_dist_pct = (spot - max_pain) / spot * 100
        if abs(pain_dist_pct) < 0.3:
            signals.append(f"Spot near MaxPain ₹{max_pain:.0f} — pinning expected")
        elif pain_dist_pct > 1.0:
            score -= 8
            signals.append(f"Spot ABOVE MaxPain ₹{max_pain:.0f} — gravity pull DOWN")
        elif pain_dist_pct < -1.0:
            score += 8
            signals.append(f"Spot BELOW MaxPain ₹{max_pain:.0f} — gravity pull UP")

        # ── OI Change: fresh writing vs covering ─────────
        if call_oi_chg < -100000:  # Fresh call covering → bullish
            score += 8
            signals.append(f"Call OI unwinding ({call_oi_chg:,}) — bears covering → BULLISH")
        elif call_oi_chg > 100000:  # Fresh call writing → bearish ceiling
            score -= 10
            signals.append(f"Fresh call writing ({call_oi_chg:,}) → ceiling at ₹{resistance:.0f}")

        if put_oi_chg < -100000:  # Fresh put covering → bearish
            score -= 8
            signals.append(f"Put OI unwinding ({put_oi_chg:,}) — bulls covering → BEARISH")
        elif put_oi_chg > 100000:  # Fresh put writing → bullish floor
            score += 10
            signals.append(f"Fresh put writing ({put_oi_chg:,}) → floor at ₹{support:.0f}")

        # ── IV Skew ───────────────────────────────────────
        if iv_skew > 3:
            score += 8
            signals.append(f"IV skew +{iv_skew:.1f} — call premium demand → BULLISH")
        elif iv_skew < -3:
            score -= 8
            signals.append(f"IV skew {iv_skew:.1f} — put premium demand → bearish hedge by institutions")

        # ── GEX ───────────────────────────────────────────
        if gex < -5:
            signals.append(f"Negative GEX={gex:.1f} — MMs short gamma → TRENDING moves amplified")
        elif gex > 5:
            signals.append(f"Positive GEX={gex:.1f} — MMs long gamma → dampening/pinning expected")

        # ── Spot position relative to walls ──────────────
        if spot > resistance * 0.99:
            score -= 6
            signals.append(f"Spot near call wall ₹{resistance:.0f} — heavy resistance")
        if spot < support * 1.01:
            score += 6
            signals.append(f"Spot near put wall ₹{support:.0f} — strong support")

        # ── Determine bias ────────────────────────────────
        if score >= 15:
            bias = "BULLISH"
        elif score <= -15:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        confidence = min(abs(score) * 2.5, 100)
        return bias, confidence, signals

    # ──────────────────────────────────────────────────────
    # CONVENIENCE: SCORE FOR SIGNAL GENERATOR
    # ──────────────────────────────────────────────────────

    def get_direction_score(self, symbol: str = "NIFTY") -> float:
        """
        Returns a score from -10 to +10 for use in signal_generator.
        Positive = bullish, negative = bearish.
        """
        result = self.analyze(symbol)
        if not result:
            return 0.0
        if result.direction_bias == "BULLISH":
            return min(result.confidence_score / 10, 10.0)
        if result.direction_bias == "BEARISH":
            return max(-result.confidence_score / 10, -10.0)
        return 0.0

    def get_key_levels(self, symbol: str = "NIFTY") -> Dict[str, float]:
        """
        Returns key option chain levels for a symbol.
        Used by signal_generator for support/resistance context.
        """
        result = self.analyze(symbol)
        if not result:
            return {}
        return {
            "max_pain":    result.max_pain,
            "resistance_1": result.resistance_1,
            "resistance_2": result.resistance_2,
            "support_1":   result.support_1,
            "support_2":   result.support_2,
            "pcr":         result.pcr,
            "atm_iv":      result.atm_iv,
        }

    def is_near_key_level(
        self, price: float, symbol: str = "NIFTY", tolerance_pct: float = 0.5
    ) -> Dict[str, bool]:
        """Check if a price is near a key option chain level."""
        levels = self.get_key_levels(symbol)
        result = {}
        for name, level in levels.items():
            if isinstance(level, float) and level > 0:
                dist_pct = abs(price - level) / level * 100
                result[f"near_{name}"] = dist_pct <= tolerance_pct
        return result

    def format_telegram(self, symbol: str = "NIFTY") -> str:
        """Format option chain summary for Telegram morning brief."""
        result = self.analyze(symbol)
        if not result:
            return f"Option chain for {symbol}: unavailable"

        bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(
            result.direction_bias, "🟡"
        )
        signals_str = "\n".join(f"  • {s}" for s in result.signals[:5])

        return (
            f"📊 *Option Chain — {symbol}* ({result.expiry})\n"
            f"Spot: `₹{result.spot_price:,.0f}` | "
            f"PCR: `{result.pcr:.2f}` | "
            f"ATM IV: `{result.atm_iv:.1f}%`\n"
            f"MaxPain: `₹{result.max_pain:,.0f}` | "
            f"Support: `₹{result.support_1:,.0f}` | "
            f"Resist: `₹{result.resistance_1:,.0f}`\n"
            f"{bias_emoji} Bias: *{result.direction_bias}* ({result.confidence_score:.0f}/100)\n"
            f"*Signals:*\n{signals_str}"
        )


# ──────────────────────────────────────────────────────────────
# SINGLETON
# ──────────────────────────────────────────────────────────────

_oc_analyzer: Optional[OptionChainAnalyzer] = None

def get_option_chain_analyzer() -> OptionChainAnalyzer:
    global _oc_analyzer
    if _oc_analyzer is None:
        _oc_analyzer = OptionChainAnalyzer()
    return _oc_analyzer


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    oc = OptionChainAnalyzer()
    for sym in ["NIFTY", "BANKNIFTY"]:
        print(f"\n{'='*60}")
        result = oc.analyze(sym)
        if result:
            print(result.summary())
            for sig in result.signals:
                print(f"  → {sig}")
        else:
            print(f"{sym}: No data (check NSE connectivity)")


# ============================================================
# MODULE: nse_fo_list.py
# ============================================================
"""
nse_fo_list.py — NSE Momentum Groww AI Bot
NSE F&O Eligible Stocks List — Auto-fetching with Daily Cache

Purpose: Only NSE F&O segment stocks can be intraday shorted.
Equity-only stocks can ONLY be bought (no short selling intraday).
Attempting to short a non-F&O stock on Groww → order REJECTED.

Source: NSE public CSV at https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv
Cache: Refreshed daily, stored in data/fo_list_cache.json
Fallback: Built-in list covering ~95% of Nifty 500 F&O stocks
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional, Set

import requests

from utils import get_current_ist_date

logger = logging.getLogger(__name__)

# Cache location
FO_CACHE_FILE = Path("data/fo_list_cache.json")

# NSE public F&O lot-size CSV (no auth required — use browser headers)
NSE_FO_CSV_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
NSE_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# ── BUILT-IN FALLBACK LIST ─────────────────────────────────────────────────
# Covers NSE F&O eligible stocks (Nifty 50 + Nifty 100 + popular F&O names).
# Updated periodically. Refreshed from NSE API on successful fetch.
FALLBACK_FO_LIST: Set[str] = {
    # Nifty 50
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK",
    "ASIANPAINT", "MARUTI", "TITAN", "SUNPHARMA", "NESTLEIND",
    "ULTRACEMCO", "WIPRO", "HCLTECH", "BAJFINANCE", "TATAMOTORS",
    "BAJAJFINSV", "ONGC", "NTPC", "POWERGRID", "M&M", "COALINDIA",
    "ADANIENT", "ADANIPORTS", "JSWSTEEL", "TATASTEEL", "HINDALCO",
    "GRASIM", "EICHERMOT", "HEROMOTOCO", "BPCL", "CIPLA", "DRREDDY",
    "DIVISLAB", "HDFCLIFE", "SBICARD", "TECHM", "TATACONSUM",
    "BAJAJ-AUTO", "INDUSINDBK", "UPL", "SHREECEM", "DMART", "VEDL",
    # Nifty Next 50 / popular F&O
    "ZOMATO", "NYKAA", "PAYTM", "POLICYBZR",
    "BANKBARODA", "CANFINHOME", "CHOLAFIN", "CUB", "DLF", "FEDERALBNK",
    "GODREJCP", "GODREJPROP", "GRANULES", "HAL", "HAVELLS",
    "IDFCFIRSTB", "INDUSTOWER", "IRCTC", "JINDALSTEL", "JUBLFOOD",
    "LICI", "LUPIN", "MANAPPURAM", "MCDOWELL-N", "METROPOLIS",
    "MOTHERSON", "MPHASIS", "MRF", "MUTHOOTFIN", "NAUKRI",
    "OBEROIRLTY", "OFSS", "PEL", "PETRONET", "PFC", "PNB",
    "RECLTD", "SAIL", "SBILIFE", "SRF", "TRENT", "TVSMOTOR",
    "UBL", "UNIONBANK", "VBL", "VOLTAS", "ZYDUSLIFE",
    "ABB", "AMBUJACEM", "APOLLOHOSP", "AUROPHARMA", "BALKRISIND",
    "BANDHANBNK", "BERGEPAINT", "BEL", "BHEL", "BIOCON",
    "BOSCHLTD", "CANBK", "CONCOR", "DABUR", "DEEPAKNTR",
    "ESCORTS", "EXIDEIND", "GAIL", "GMRINFRA", "GNFC",
    "HDFCAMC", "HINDPETRO", "IBULHSGFIN", "ICICIPRULI",
    "IGL", "INDHOTEL", "IOC", "ITC", "JINDALSTEL",
    "LALPATHLAB", "LICHSGFIN", "LTIM", "LTTS",
    "MINDTREE", "MARICO", "NIACL", "NMDC", "PAGEIND",
    "PERSISTENT", "PIDILITIND", "PIIND", "RBLBANK",
    "SIEMENS", "STAR", "TATACHEM", "TATAPOWER",
    "TORNTPHARM", "TORNTPOWER", "WHIRLPOOL", "YESBANK",
    # Nifty Bank additional
    "AUBANK", "DCBBANK", "KARURVYSYA", "LAKSHVILAS",
}


class NSEFOList:
    """
    Manages NSE F&O eligible stocks list with auto daily refresh.

    On first use: fetches from NSE website → parses → caches to disk.
    Daily: checks cache date; re-fetches if stale.
    On failure: uses built-in fallback list (covers major F&O stocks).
    """

    def __init__(self):
        self._fo_symbols: Set[str] = set()
        self._cache_date: Optional[date] = None
        self._load_cache()

    # ── CACHE LOAD ─────────────────────────────────────────────

    def _load_cache(self):
        """Load F&O list from disk if today's cache exists."""
        try:
            if FO_CACHE_FILE.exists():
                with open(FO_CACHE_FILE) as f:
                    data = json.load(f)
                cached_date = date.fromisoformat(data.get("date", "2000-01-01"))
                if cached_date == get_current_ist_date():
                    self._fo_symbols = set(data.get("symbols", []))
                    self._cache_date = cached_date
                    logger.info(
                        f"F&O list loaded from cache: {len(self._fo_symbols)} stocks "
                        f"(date: {cached_date})"
                    )
                    return
        except Exception as e:
            logger.debug(f"F&O cache load error: {e}")

        # No valid cache → fetch fresh
        self._refresh()

    # ── NSE FETCH ──────────────────────────────────────────────

    def _refresh(self) -> bool:
        """
        Fetch fresh F&O list from NSE public CSV.
        Returns True on success, False if fallback used.
        """
        try:
            # NSE requires a session cookie from a preliminary GET
            session = requests.Session()
            session.get(
                "https://www.nseindia.com",
                headers=NSE_BASE_HEADERS,
                timeout=10,
            )

            resp = session.get(
                NSE_FO_CSV_URL,
                headers=NSE_BASE_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()

            symbols = self._parse_fo_csv(resp.text)
            if len(symbols) < 50:
                raise ValueError(
                    f"NSE CSV parsed only {len(symbols)} symbols — likely malformed"
                )

            self._fo_symbols = symbols
            self._cache_date = get_current_ist_date()
            self._save_cache()
            logger.info(
                f"F&O list refreshed from NSE: {len(self._fo_symbols)} eligible stocks"
            )
            return True

        except Exception as e:
            logger.warning(
                f"F&O list NSE fetch failed: {e} — using built-in fallback "
                f"({len(FALLBACK_FO_LIST)} stocks)"
            )
            self._fo_symbols = FALLBACK_FO_LIST.copy()
            self._cache_date = get_current_ist_date()
            # Save fallback to cache so we don't retry every call
            self._save_cache()
            return False

    def _parse_fo_csv(self, csv_text: str) -> Set[str]:
        """
        Parse NSE fo_mktlots.csv to extract stock symbols.
        CSV format: UNDERLYING,SYMBOL,... (header on row 1, data from row 2)
        """
        symbols: Set[str] = set()
        lines = csv_text.strip().split("\n")
        for line in lines[1:]:   # skip header
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            symbol = parts[1].strip().upper().replace('"', "")
            # Validate: non-empty, reasonable length, no spaces
            if symbol and symbol != "SYMBOL" and 1 <= len(symbol) <= 20 and " " not in symbol:
                symbols.add(symbol)
        return symbols

    def _save_cache(self):
        """Persist F&O list to disk cache."""
        try:
            FO_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(FO_CACHE_FILE, "w") as f:
                json.dump(
                    {
                        "date": str(self._cache_date),
                        "symbols": sorted(self._fo_symbols),
                        "count": len(self._fo_symbols),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.debug(f"F&O cache save error: {e}")

    # ── PUBLIC API ─────────────────────────────────────────────

    def is_fo_eligible(self, symbol: str) -> bool:
        """
        Check if a stock is F&O eligible (can be shorted intraday).

        Auto-refreshes if cache is stale (new trading day).
        Falls back to built-in list if NSE fetch fails.
        """
        today = get_current_ist_date()
        if self._cache_date != today:
            logger.info(f"F&O list stale ({self._cache_date}) — refreshing...")
            self._refresh()
        return symbol.upper() in self._fo_symbols

    def get_fo_symbols(self) -> Set[str]:
        """Return full set of F&O eligible symbols."""
        today = get_current_ist_date()
        if self._cache_date != today:
            self._refresh()
        return self._fo_symbols.copy()

    def force_refresh(self) -> bool:
        """Force refresh from NSE (ignore cache)."""
        return self._refresh()

    @property
    def count(self) -> int:
        return len(self._fo_symbols)


# ── SINGLETON ──────────────────────────────────────────────────────────────

_fo_list_instance: Optional[NSEFOList] = None


def get_fo_list() -> NSEFOList:
    """Return singleton NSEFOList instance."""
    global _fo_list_instance
    if _fo_list_instance is None:
        _fo_list_instance = NSEFOList()
    return _fo_list_instance


def is_fo_eligible(symbol: str) -> bool:
    """Quick helper: is this symbol F&O eligible (shortable)?"""
    return get_fo_list().is_fo_eligible(symbol)


# ── SELF-TEST ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== NSE F&O Eligibility Test ===")
    fo = NSEFOList()
    print(f"Total F&O eligible stocks: {fo.count}")
    test = ["RELIANCE", "HDFCBANK", "TCS", "RANDOMSTOCK123", "PAYTM", "ZOMATO"]
    for sym in test:
        eligible = fo.is_fo_eligible(sym)
        icon = "✅ F&O ELIGIBLE (can short)" if eligible else "❌ Equity only (BUY only)"
        print(f"  {sym:20s}: {icon}")


# ============================================================
# MODULE: nse_data.py
# ============================================================
"""
nse_data.py — NSE Momentum Groww AI Bot
Supplementary Institutional & Microstructure Data from NSE Public APIs

No API key required — uses NSE public website endpoints with session cookie.

Data sources:
  1. Bulk Deals     — orders >0.5% of listed equity (smart-money footprint)
  2. Block Deals    — negotiated institutional trades >₹5cr or 500k shares
  3. Delivery %     — delivered qty / traded qty (high % = conviction, not speculation)
  4. FII Futures    — real-time FII long/short in index futures (ahead of equity FII)
  5. 52-Week Range  — breakout momentum context
  6. Short Interest — stock-level F&O OI changes as proxy for short buildup

18yr Rule: "Volume tells you what happened. Delivery tells you who is serious."
"""

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from utils import format_ist_timestamp, get_current_ist_time, retry_with_backoff

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

NSE_BASE = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/",
}


class NSEDataFetcher:
    """
    Fetches supplementary institutional and microstructure data from NSE.
    All data is from NSE public endpoints — no API key needed.

    Cache strategy:
      - Bulk/Block deals:   60 min (updated once per day)
      - Delivery data:      60 min
      - FII futures OI:     30 min (more dynamic)
      - 52wk range:         60 min
      - Short interest:     30 min
    """

    # Cache TTLs in seconds
    _CACHE_TTL: Dict[str, int] = {
        "bulk_deals":    3600,
        "block_deals":   3600,
        "fii_futures":   1800,
        "52wk":          3600,
        "delivery":      3600,
        "short_oi":      1800,
    }

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._session_valid = False
        self._cache:       Dict[str, object]  = {}
        self._cache_times: Dict[str, float]   = {}
        self._refresh_session()

    # ── SESSION MANAGEMENT ─────────────────────────────────────

    def _refresh_session(self) -> bool:
        """Warm the NSE session cookie — required before API calls."""
        try:
            r = self._session.get(f"{NSE_BASE}/", timeout=12)
            if r.status_code == 200:
                self._session_valid = True
                return True
        except Exception as e:
            logger.debug(f"NSE session refresh failed: {e}")
        self._session_valid = False
        return False

    def _get_cached(self, key: str) -> Optional[object]:
        ttl = self._CACHE_TTL.get(key, 1800)
        if key in self._cache:
            age = time.time() - self._cache_times.get(key, 0)
            if age < ttl:
                return self._cache[key]
        return None

    def _set_cache(self, key: str, value: object) -> None:
        self._cache[key] = value
        self._cache_times[key] = time.time()

    def _get(self, url: str, cache_key: str = "") -> Optional[dict]:
        """HTTP GET with cache, session auto-refresh, and retry."""
        if cache_key:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        if not self._session_valid:
            self._refresh_session()

        try:
            r = self._session.get(url, timeout=15)
            if r.status_code in (401, 403):
                self._refresh_session()
                r = self._session.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if cache_key:
                    self._set_cache(cache_key, data)
                return data
        except Exception as e:
            logger.debug(f"NSE GET failed [{url}]: {e}")
        return None

    # ── BULK DEALS ─────────────────────────────────────────────

    def get_bulk_deals(self, date_str: str = "") -> List[Dict]:
        """
        Bulk Deals: single orders > 0.5% of listed equity.
        Published by NSE by ~6 PM each day.
        Strong signal when same direction (BUY/SELL) repeats across multiple clients.

        Returns list of {symbol, client, buy_sell, quantity, price, value_cr}
        """
        if not date_str:
            date_str = get_current_ist_time().strftime("%d-%m-%Y")

        url = f"{NSE_BASE}/api/bulk-deals?from={date_str}&to={date_str}"
        data = self._get(url, "bulk_deals")
        if not data:
            return []

        raw = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        result = []
        for d in raw:
            try:
                qty   = int(float(d.get("QUANTITY_TRADED", 0) or 0))
                price = float(d.get("TRADE_PRICE", 0) or 0)
                result.append({
                    "symbol":    str(d.get("SYMBOL", "")).strip().upper(),
                    "client":    str(d.get("CLIENT_NAME", "")),
                    "buy_sell":  str(d.get("BUY_SELL", "")).upper(),
                    "quantity":  qty,
                    "price":     price,
                    "value_cr":  round(qty * price / 1e7, 2),
                    "date":      str(d.get("TRADE_DATE", date_str)),
                })
            except Exception:
                continue
        return result

    # ── BLOCK DEALS ────────────────────────────────────────────

    def get_block_deals(self, date_str: str = "") -> List[Dict]:
        """
        Block Deals: negotiated large trades between institutional parties.
        Threshold: > 500,000 shares OR > ₹5 crore.
        More reliable signal than bulk deals — prearranged between smart money.
        """
        if not date_str:
            date_str = get_current_ist_time().strftime("%d-%m-%Y")

        url = f"{NSE_BASE}/api/block-deals?from={date_str}&to={date_str}"
        data = self._get(url, "block_deals")
        if not data:
            return []

        raw = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        result = []
        for d in raw:
            try:
                qty   = int(float(d.get("QUANTITY_TRADED", 0) or 0))
                price = float(d.get("TRADE_PRICE", 0) or 0)
                result.append({
                    "symbol":   str(d.get("SYMBOL", "")).strip().upper(),
                    "client":   str(d.get("CLIENT_NAME", "")),
                    "buy_sell": str(d.get("BUY_SELL", "")).upper(),
                    "quantity": qty,
                    "price":    price,
                    "value_cr": round(qty * price / 1e7, 2),
                })
            except Exception:
                continue
        return result

    # ── DELIVERY % ─────────────────────────────────────────────

    def get_delivery_data(self, symbol: str) -> Optional[Dict]:
        """
        Delivery % = delivered shares / total traded shares.

        Interpretation:
          > 60%  → STRONG  — institutions taking delivery, real conviction
          30-60% → NEUTRAL — mix of speculators and investors
          < 30%  → WEAK    — mostly intraday speculation, avoid for momentum

        High delivery on a breakout day = institutional accumulation signal.
        Low delivery on a rally = short covering / speculative froth.
        """
        url = f"{NSE_BASE}/api/quote-equity?symbol={symbol}&section=trade_info"
        data = self._get(url, f"delivery_{symbol}")
        if not data:
            return None

        try:
            # NSE returns different structures depending on endpoint
            ti = (data.get("tradeInfo") or
                  data.get("marketDeptOrderBook", {}).get("tradeInfo") or {})

            total_vol = float(ti.get("totalTradedVolume", 0) or
                              data.get("totalTradedVolume", 0) or 0)
            delivered = float(ti.get("deliveryQuantity", 0) or
                              data.get("deliveryQuantity", 0) or 0)
            delivery_pct = (delivered / total_vol * 100) if total_vol > 0 else 0.0

            signal = (
                "STRONG"  if delivery_pct > 60 else
                "NEUTRAL" if delivery_pct > 30 else
                "WEAK"
            )
            return {
                "symbol":        symbol,
                "delivery_pct":  round(delivery_pct, 1),
                "total_volume":  int(total_vol),
                "delivered_qty": int(delivered),
                "signal":        signal,
            }
        except Exception as e:
            logger.debug(f"Delivery data parse error {symbol}: {e}")
        return None

    # ── FII INDEX FUTURES POSITIONING ──────────────────────────

    def get_fii_futures_positioning(self) -> Optional[Dict]:
        """
        FII index futures positioning — REAL-TIME (updated intraday).
        This is more current than equity FII/DII (which is end-of-day).

        When FII long % > 55% → Institutional bias BULLISH
        When FII long % < 45% → Institutional bias BEARISH

        Returns score -10 to +10 for signal_generator integration.
        """
        url = f"{NSE_BASE}/api/participant-wise-open-interest"
        data = self._get(url, "fii_futures")
        if not data:
            return None

        try:
            rows = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            fii_row = next(
                (r for r in rows if "FII" in str(r.get("clientType", "")).upper()),
                None
            )
            if not fii_row:
                return None

            long_idx  = int(fii_row.get("futureIndexLong",  0) or 0)
            short_idx = int(fii_row.get("futureIndexShort", 0) or 0)
            total     = long_idx + short_idx
            if total == 0:
                return None

            long_pct = long_idx / total * 100
            net      = long_idx - short_idx

            # Score: every 1% above/below 50% = 1 point, capped ±10
            raw_score = (long_pct - 50) / 1.0
            score     = max(-10, min(10, int(raw_score)))

            return {
                "fii_future_long":  long_idx,
                "fii_future_short": short_idx,
                "fii_future_net":   net,
                "fii_long_pct":     round(long_pct, 1),
                "bias":             ("BULLISH" if long_pct > 55 else
                                     "BEARISH" if long_pct < 45 else "NEUTRAL"),
                "score":            score,
                "formatted": (
                    f"FII Futures: {'🟢' if long_pct > 55 else ('🔴' if long_pct < 45 else '⚪')} "
                    f"Long {long_pct:.1f}% ({net:+,} net contracts)"
                ),
            }
        except Exception as e:
            logger.debug(f"FII futures parse error: {e}")
        return None

    # ── 52-WEEK RANGE CONTEXT ───────────────────────────────────

    def get_52wk_context(self, symbol: str) -> Optional[Dict]:
        """
        52-week high/low context for momentum classification.

        Near 52wk high (within 2%):  momentum breakout candidate → +3 to +5 pts
        AT 52wk high (within 0.5%):  breakout in progress → +5 pts
        Near 52wk low (within 5%):   avoid for LONG momentum trades
        """
        url = f"{NSE_BASE}/api/quote-equity?symbol={symbol}"
        data = self._get(url, f"52wk_{symbol}")
        if not data:
            return None

        try:
            pi     = data.get("priceInfo", {}) or {}
            whl    = pi.get("weekHighLow", {}) or {}
            high52 = float(whl.get("max", 0) or 0)
            low52  = float(whl.get("min", 0) or 0)
            ltp    = float(pi.get("lastPrice", 0) or 0)

            if not ltp or not high52 or not low52:
                return None

            pct_from_high = (high52 - ltp) / high52 * 100
            pct_from_low  = (ltp - low52)  / low52  * 100

            return {
                "symbol":           symbol,
                "high_52w":         high52,
                "low_52w":          low52,
                "ltp":              ltp,
                "pct_from_high":    round(pct_from_high, 2),
                "pct_from_low":     round(pct_from_low, 2),
                "at_52wk_high":     pct_from_high <= 0.5,
                "near_52wk_high":   pct_from_high <= 2.0,
                "near_52wk_low":    pct_from_low  <= 5.0,
            }
        except Exception as e:
            logger.debug(f"52wk parse error {symbol}: {e}")
        return None

    # ── STOCK F&O SHORT INTEREST ────────────────────────────────

    def get_short_interest(self, symbol: str) -> Optional[Dict]:
        """
        Stock F&O OI analysis as proxy for short buildup.
        Rising OI + falling price → short buildup (bearish)
        Rising OI + rising price → long buildup (bullish)
        """
        url = f"{NSE_BASE}/api/quote-derivative?symbol={symbol}"
        data = self._get(url, f"short_oi_{symbol}")
        if not data:
            return None

        try:
            stocks = data.get("stocks", []) or []
            # Get near-month futures
            near_futures = [
                s for s in stocks
                if s.get("metadata", {}).get("instrumentType") == "Stock Futures"
            ]
            if not near_futures:
                return None

            nf = near_futures[0].get("marketDeptOrderBook", {}) or {}
            oi_change_pct = float(nf.get("otherInfo", {}).get("changeinOpenInterest", 0) or 0)
            price_change  = float(data.get("info", {}).get("change", 0) or 0)

            # Classify OI + price combination
            if oi_change_pct > 5 and price_change > 0:
                signal = "LONG_BUILDUP"
                score  = 5
            elif oi_change_pct > 5 and price_change < 0:
                signal = "SHORT_BUILDUP"
                score  = -5
            elif oi_change_pct < -5 and price_change > 0:
                signal = "SHORT_COVERING"
                score  = 3
            elif oi_change_pct < -5 and price_change < 0:
                signal = "LONG_UNWINDING"
                score  = -3
            else:
                signal = "NEUTRAL"
                score  = 0

            return {
                "symbol":       symbol,
                "oi_change_pct": round(oi_change_pct, 2),
                "price_change":  round(price_change, 2),
                "signal":        signal,
                "score":         score,
            }
        except Exception as e:
            logger.debug(f"Short interest parse error {symbol}: {e}")
        return None

    # ── COMPOSITE SIGNAL SCORE ─────────────────────────────────

    def get_composite_score(self, symbol: str, direction: str) -> Tuple[int, str]:
        """
        Combine all NSE data sources into a single score adjustment.
        Returns (score_adjustment, reason_string) where score is -20 to +20.

        Breakdown:
          Bulk/Block deals:   ±10 pts  (smart money footprint)
          Delivery %:         ±5  pts  (conviction vs speculation)
          52wk context:       ±5  pts  (momentum context)
          Short interest:     ±5  pts  (F&O buildup signal)
        """
        total_score = 0
        reasons     = []

        # ── 1. Bulk + Block deals ──────────────────────────
        try:
            all_deals  = self.get_bulk_deals() + self.get_block_deals()
            sym_deals  = [d for d in all_deals if d.get("symbol") == symbol]
            if sym_deals:
                buy_val  = sum(d["value_cr"] for d in sym_deals if d["buy_sell"] == "BUY")
                sell_val = sum(d["value_cr"] for d in sym_deals if d["buy_sell"] == "SELL")
                total_val = buy_val + sell_val
                if total_val > 0:
                    net_bias = (buy_val - sell_val) / total_val * 100
                    if direction == "LONG":
                        deal_pts = 10 if net_bias > 60 else (5 if net_bias > 20 else
                                   (-10 if net_bias < -60 else (-5 if net_bias < -20 else 0)))
                    else:
                        deal_pts = -10 if net_bias > 60 else (-5 if net_bias > 20 else
                                    (10 if net_bias < -60 else (5 if net_bias < -20 else 0)))
                    total_score += deal_pts
                    if abs(deal_pts) >= 5:
                        emoji = "🟢" if deal_pts > 0 else "🔴"
                        reasons.append(
                            f"{emoji}Bulk/Block ₹{total_val:.1f}Cr "
                            f"({'Buy' if net_bias > 0 else 'Sell'} net)"
                        )
        except Exception:
            pass

        # ── 2. Delivery % ──────────────────────────────────
        try:
            delivery = self.get_delivery_data(symbol)
            if delivery:
                dp = delivery["delivery_pct"]
                if dp > 60:
                    total_score += 5
                    reasons.append(f"📦Delivery {dp:.0f}% (conviction)")
                elif dp < 30:
                    total_score -= 3
                    reasons.append(f"⚠️Delivery {dp:.0f}% (speculative)")
        except Exception:
            pass

        # ── 3. 52-week context ──────────────────────────────
        try:
            w52 = self.get_52wk_context(symbol)
            if w52:
                if direction == "LONG":
                    if w52["at_52wk_high"]:
                        total_score += 5
                        reasons.append("🚀At 52wk high (breakout)")
                    elif w52["near_52wk_high"]:
                        total_score += 3
                        reasons.append(f"📈Near 52wk high (-{w52['pct_from_high']:.1f}%)")
                    elif w52["near_52wk_low"]:
                        total_score -= 5
                        reasons.append("⚠️Near 52wk low — avoid LONG")
                elif direction == "SHORT" and w52["near_52wk_low"]:
                    total_score += 3
                    reasons.append("📉Near 52wk low (SHORT context)")
        except Exception:
            pass

        # ── 4. F&O short interest ───────────────────────────
        try:
            si = self.get_short_interest(symbol)
            if si and si["signal"] != "NEUTRAL":
                s = si["score"]
                adjusted = s if direction == "LONG" else -s
                total_score += adjusted
                if abs(adjusted) >= 3:
                    reasons.append(f"📊F&O:{si['signal']}")
        except Exception:
            pass

        total_score = max(-20, min(total_score, 20))
        reason_str  = " | ".join(reasons) if reasons else ""
        return total_score, reason_str

    # ── MORNING BRIEF SUMMARY ──────────────────────────────────

    def get_morning_summary(self) -> str:
        """
        Format NSE institutional data for Telegram morning brief.
        Called by continuous_learner at 8:30 AM IST.
        """
        lines = ["📊 <b>NSE Institutional Data</b>"]

        # FII futures positioning
        fii_fut = self.get_fii_futures_positioning()
        if fii_fut:
            lines.append(fii_fut["formatted"])

        # Bulk/Block deal activity
        bulk   = self.get_bulk_deals()
        blocks = self.get_block_deals()
        if bulk or blocks:
            total_deals = len(bulk) + len(blocks)
            buy_deals   = sum(1 for d in bulk + blocks if d["buy_sell"] == "BUY")
            sell_deals  = total_deals - buy_deals
            lines.append(
                f"💼 Deals today: {total_deals} total | "
                f"Buy:{buy_deals} / Sell:{sell_deals}"
            )
            # Top 3 by value
            all_deals = sorted(bulk + blocks, key=lambda x: x["value_cr"], reverse=True)
            for d in all_deals[:3]:
                arrow = "🟢" if d["buy_sell"] == "BUY" else "🔴"
                lines.append(
                    f"  {arrow} {d['symbol']} ₹{d['value_cr']:.1f}Cr {d['buy_sell']}"
                )
        else:
            lines.append("💼 No bulk/block deals data yet")

        return "\n".join(lines)


# ── SECTOR MAP ─────────────────────────────────────────────────
# Used by risk_manager for correlation guard
SECTOR_MAP: Dict[str, str] = {
    # Banking & Finance
    "HDFCBANK": "BANKING",   "ICICIBANK": "BANKING",  "SBIN":      "BANKING",
    "KOTAKBANK":"BANKING",   "AXISBANK":  "BANKING",  "INDUSINDBK":"BANKING",
    "BANKBARODA":"BANKING",  "PNB":       "BANKING",  "FEDERALBNK":"BANKING",
    "BAJFINANCE":"FINANCE",  "BAJAJFINSV":"FINANCE",  "HDFCAMC":   "FINANCE",
    "MUTHOOTFIN":"FINANCE",  "CHOLAFIN":  "FINANCE",

    # IT / Technology
    "TCS":     "IT",  "INFY":    "IT",  "WIPRO":   "IT",
    "HCLTECH": "IT",  "TECHM":   "IT",  "LTIM":    "IT",
    "MPHASIS": "IT",  "COFORGE": "IT",  "PERSISTENT":"IT",

    # Oil & Energy
    "RELIANCE": "ENERGY",  "ONGC":  "ENERGY",  "IOC":    "ENERGY",
    "BPCL":     "ENERGY",  "NTPC":  "ENERGY",  "POWERGRID":"ENERGY",
    "TATAPOWER":"ENERGY",  "ADANIGREEN":"ENERGY",

    # Auto
    "MARUTI":    "AUTO",  "TATAMOTORS": "AUTO",  "M&M":       "AUTO",
    "BAJAJ-AUTO":"AUTO",  "EICHERMOT":  "AUTO",  "HEROMOTOCO":"AUTO",
    "ASHOKLEY":  "AUTO",  "TVSMOTOR":   "AUTO",

    # Pharma
    "SUNPHARMA": "PHARMA",  "DRREDDY":   "PHARMA",  "CIPLA":    "PHARMA",
    "DIVISLAB":  "PHARMA",  "AUROPHARMA":"PHARMA",  "BIOCON":   "PHARMA",
    "ALKEM":     "PHARMA",  "TORNTPHARM":"PHARMA",

    # FMCG
    "ITC":       "FMCG",  "HINDUNILVR":"FMCG",  "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG",  "DABUR":     "FMCG",  "MARICO":    "FMCG",
    "GODREJCP":  "FMCG",

    # Metals & Mining
    "TATASTEEL": "METALS",  "HINDALCO": "METALS",  "JSWSTEEL":   "METALS",
    "SAIL":      "METALS",  "VEDL":     "METALS",  "NATIONALUM":  "METALS",
    "COALINDIA": "METALS",

    # Cement & Infrastructure
    "ULTRACEMCO":"CEMENT",  "SHREECEM": "CEMENT",  "AMBUJACEMENT":"CEMENT",
    "LT":        "INFRA",   "ADANIPORTS":"INFRA",  "GMRINFRA":    "INFRA",

    # Telecom
    "BHARTIARTL":"TELECOM",  "IDEA": "TELECOM",

    # Consumer / Retail
    "TITAN":   "CONSUMER",  "DMART":   "CONSUMER",  "ASIANPAINT":"CONSUMER",
    "PIDILITIND":"CONSUMER", "VOLTAS":  "CONSUMER",

    # Diversified
    "ADANIENT": "DIVERSIFIED",  "ADANIENTERPRISES":"DIVERSIFIED",
}


def get_sector(symbol: str) -> str:
    """Get sector for a symbol. Returns 'OTHER' if not in map."""
    return SECTOR_MAP.get(symbol.upper(), "OTHER")


# ── SINGLETON ──────────────────────────────────────────────────

_fetcher: Optional[NSEDataFetcher] = None


def get_nse_data_fetcher() -> NSEDataFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = NSEDataFetcher()
    return _fetcher


# ============================================================
# MODULE: market_data_store.py
# ============================================================
"""
market_data_store.py — NSE Momentum Groww AI Bot
Historical Market Data Store — Grows Daily Automatically

Stores ALL fetched candle data locally in SQLite.
After market close, downloads fresh data for every watchlist stock.
This gives the bot an ever-growing dataset to learn from.

Benefits:
- Faster signals (cached data, no API call delays)
- Offline trainer has fresh data every day
- Detects market regime changes over time
- Tracks seasonal patterns (expiry weeks, budget months, etc.)
- Never depends solely on live API during market hours
"""

import logging
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from utils import (
    format_ist_timestamp, get_current_ist_time, get_current_ist_date,
    convert_to_ist, IST, UTC
)

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CANDLE_DB = DATA_DIR / "candle_store.db"
STATS_DB  = DATA_DIR / "market_stats.db"


class MarketDataStore:
    """
    Local growing database of NSE candle data.
    Auto-downloads after each market session.
    """

    def __init__(self):
        self._init_candle_db()
        self._init_stats_db()

    # --------------------------------------------------------
    # DATABASE INIT
    # --------------------------------------------------------

    def _init_candle_db(self):
        with sqlite3.connect(CANDLE_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    symbol    TEXT    NOT NULL,
                    interval  TEXT    NOT NULL,
                    ts_ist    TEXT    NOT NULL,
                    open      REAL,
                    high      REAL,
                    low       REAL,
                    close     REAL,
                    volume    INTEGER,
                    PRIMARY KEY (symbol, interval, ts_ist)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_int ON candles(symbol, interval)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS download_log (
                    symbol      TEXT,
                    interval    TEXT,
                    last_ts_ist TEXT,
                    downloaded_at TEXT,
                    candle_count  INTEGER,
                    PRIMARY KEY (symbol, interval)
                )
            """)
            conn.commit()
        logger.debug(f"[{format_ist_timestamp()}] Candle DB ready: {CANDLE_DB}")

    def _init_stats_db(self):
        with sqlite3.connect(STATS_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    symbol       TEXT    NOT NULL,
                    date_ist     TEXT    NOT NULL,
                    open         REAL,
                    high         REAL,
                    low          REAL,
                    close        REAL,
                    volume       INTEGER,
                    adr_pct      REAL,
                    gap_pct      REAL,
                    return_pct   REAL,
                    relative_str REAL,
                    nifty_return REAL,
                    PRIMARY KEY (symbol, date_ist)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbol_meta (
                    symbol       TEXT PRIMARY KEY,
                    sector       TEXT,
                    avg_adr_pct  REAL,
                    avg_volume   INTEGER,
                    last_updated TEXT,
                    total_days   INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_calendar (
                    date_ist    TEXT PRIMARY KEY,
                    is_holiday  INTEGER DEFAULT 0,
                    event_name  TEXT,
                    nifty_open  REAL,
                    nifty_close REAL,
                    nifty_ret   REAL,
                    market_mood TEXT
                )
            """)
            conn.commit()

    # --------------------------------------------------------
    # SAVE CANDLES
    # --------------------------------------------------------

    def save_candles(self, symbol: str, interval: str, df: pd.DataFrame) -> int:
        """
        Save a DataFrame of OHLCV candles to the store.
        Ignores duplicates (upsert). Returns count saved.
        """
        if df is None or df.empty:
            return 0
        rows = []
        for ts, row in df.iterrows():
            ts_str = str(ts) if hasattr(ts, '__str__') else str(ts)
            rows.append((
                symbol, interval, ts_str,
                float(row.get("open",  0)),
                float(row.get("high",  0)),
                float(row.get("low",   0)),
                float(row.get("close", 0)),
                int(row.get("volume",  0)),
            ))
        with sqlite3.connect(CANDLE_DB) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?)",
                rows
            )
            conn.execute(
                "INSERT INTO download_log VALUES (?,?,?,?,?) "
                "ON CONFLICT(symbol,interval) DO UPDATE SET "
                "last_ts_ist=excluded.last_ts_ist, "
                "downloaded_at=excluded.downloaded_at, "
                "candle_count=candle_count+excluded.candle_count",
                (symbol, interval, rows[-1][2] if rows else "",
                 format_ist_timestamp(), len(rows))
            )
            conn.commit()
        logger.debug(f"[{format_ist_timestamp()}] Saved {len(rows)} {interval} candles for {symbol}")
        return len(rows)

    # --------------------------------------------------------
    # LOAD CANDLES
    # --------------------------------------------------------

    def load_candles(
        self,
        symbol:   str,
        interval: str,
        days:     int = 30,
        from_date: Optional[date] = None,
    ) -> Optional[pd.DataFrame]:
        """Load candles from local store. Falls back to None if not available."""
        cutoff = str(
            from_date or (get_current_ist_date() - timedelta(days=days))
        )
        with sqlite3.connect(CANDLE_DB) as conn:
            df = pd.read_sql(
                "SELECT ts_ist, open, high, low, close, volume FROM candles "
                "WHERE symbol=? AND interval=? AND ts_ist >= ? "
                "ORDER BY ts_ist",
                conn, params=(symbol, interval, cutoff)
            )
        if df.empty:
            return None
        df["ts_ist"] = pd.to_datetime(df["ts_ist"])
        df = df.set_index("ts_ist")
        df.index.name = "datetime"
        return df

    def get_candle_count(self, symbol: str, interval: str) -> int:
        with sqlite3.connect(CANDLE_DB) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM candles WHERE symbol=? AND interval=?",
                (symbol, interval)
            ).fetchone()
        return row[0] if row else 0

    def get_last_download_info(self, symbol: str, interval: str) -> Dict:
        with sqlite3.connect(CANDLE_DB) as conn:
            row = conn.execute(
                "SELECT last_ts_ist, downloaded_at, candle_count FROM download_log "
                "WHERE symbol=? AND interval=?",
                (symbol, interval)
            ).fetchone()
        if row:
            return {"last_ts": row[0], "downloaded_at": row[1], "count": row[2]}
        return {}

    # --------------------------------------------------------
    # DAILY STATS (per-stock performance tracking)
    # --------------------------------------------------------

    def save_daily_stat(self, symbol: str, stat: Dict):
        today = str(get_current_ist_date())
        with sqlite3.connect(STATS_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol, today,
                    stat.get("open", 0), stat.get("high", 0),
                    stat.get("low", 0),  stat.get("close", 0),
                    stat.get("volume", 0),
                    stat.get("adr_pct", 0), stat.get("gap_pct", 0),
                    stat.get("return_pct", 0), stat.get("relative_str", 0),
                    stat.get("nifty_return", 0),
                )
            )
            conn.commit()

    def get_daily_stats(self, symbol: str, days: int = 30) -> pd.DataFrame:
        cutoff = str(get_current_ist_date() - timedelta(days=days))
        with sqlite3.connect(STATS_DB) as conn:
            df = pd.read_sql(
                "SELECT * FROM daily_stats WHERE symbol=? AND date_ist >= ? ORDER BY date_ist",
                conn, params=(symbol, cutoff)
            )
        return df

    def get_avg_adr(self, symbol: str, days: int = 20) -> float:
        """Average daily range % over last N days."""
        df = self.get_daily_stats(symbol, days)
        if df.empty or "adr_pct" not in df.columns:
            return 0.0
        return float(df["adr_pct"].mean())

    # --------------------------------------------------------
    # MARKET CALENDAR
    # --------------------------------------------------------

    def log_market_day(self, nifty_open: float, nifty_close: float, events: List[str]):
        today = str(get_current_ist_date())
        ret   = ((nifty_close - nifty_open) / nifty_open * 100) if nifty_open else 0
        mood  = "BULLISH" if ret > 0.5 else "BEARISH" if ret < -0.5 else "FLAT"
        with sqlite3.connect(STATS_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO market_calendar VALUES (?,0,?,?,?,?,?)",
                (today, ", ".join(events), nifty_open, nifty_close, round(ret, 2), mood)
            )
            conn.commit()

    def get_market_calendar(self, days: int = 60) -> pd.DataFrame:
        cutoff = str(get_current_ist_date() - timedelta(days=days))
        with sqlite3.connect(STATS_DB) as conn:
            df = pd.read_sql(
                "SELECT * FROM market_calendar WHERE date_ist >= ? ORDER BY date_ist",
                conn, params=(cutoff,)
            )
        return df

    # --------------------------------------------------------
    # SEASONAL / STATISTICAL ANALYSIS
    # --------------------------------------------------------

    def get_day_of_week_stats(self) -> pd.DataFrame:
        """
        Returns P&L statistics by weekday.
        18yr rule: "Monday opens are often choppy. Friday has profit-taking."
        """
        df = self.get_market_calendar(days=365)
        if df.empty:
            return pd.DataFrame()
        df["dow"] = pd.to_datetime(df["date_ist"]).dt.day_name()
        return df.groupby("dow")["nifty_ret"].agg(["mean", "std", "count"]).round(3)

    def get_monthly_seasonality(self) -> pd.DataFrame:
        """Returns average Nifty return by month over stored history."""
        df = self.get_market_calendar(days=730)
        if df.empty:
            return pd.DataFrame()
        df["month"] = pd.to_datetime(df["date_ist"]).dt.month_name()
        return df.groupby("month")["nifty_ret"].agg(["mean", "count"]).round(3)

    def get_top_correlated_stocks(self, nifty_rets: pd.Series, symbols: List[str], top_n: int = 10) -> List[str]:
        """
        Find stocks most correlated to Nifty.
        High correlation = beta play on Nifty direction.
        """
        correlations = []
        for sym in symbols:
            df = self.get_daily_stats(sym, days=30)
            if len(df) < 10:
                continue
            try:
                corr = df["return_pct"].corr(nifty_rets.tail(len(df)))
                correlations.append((sym, corr))
            except Exception:
                pass
        correlations.sort(key=lambda x: abs(x[1]), reverse=True)
        return [sym for sym, _ in correlations[:top_n]]

    # --------------------------------------------------------
    # BULK DOWNLOAD (post-market)
    # --------------------------------------------------------

    def download_all(self, symbols: List[str], fetcher, intervals: List[str] = None) -> Dict:
        """
        Download latest candle data for all symbols.
        Called at EOD (after 3:30 PM IST) to keep store fresh.
        """
        if intervals is None:
            intervals = ["5m", "15m", "1h", "1d"]

        results = {"downloaded": 0, "failed": 0, "symbols": []}

        for symbol in symbols:
            for interval in intervals:
                try:
                    days = {"5m": 10, "15m": 30, "1h": 90, "1d": 365}.get(interval, 30)
                    df   = fetcher.get_candles(symbol, interval=interval, days=days)
                    if df is not None and not df.empty:
                        saved = self.save_candles(symbol, interval, df)
                        results["downloaded"] += saved
                except Exception as e:
                    results["failed"] += 1
                    logger.debug(f"Download failed {symbol} {interval}: {e}")

            # Save daily stat
            try:
                q = fetcher.get_quote(symbol)
                if q:
                    df_d = self.load_candles(symbol, "1d", days=21)
                    adr  = 0.0
                    if df_d is not None and len(df_d) > 1:
                        recent = df_d.tail(20)
                        adr    = float(((recent["high"] - recent["low"]) / recent["close"]).mean() * 100)
                    self.save_daily_stat(symbol, {
                        "open":        q.get("open", 0),
                        "high":        q.get("high", 0),
                        "low":         q.get("low",  0),
                        "close":       q.get("ltp",  0),
                        "volume":      q.get("volume", 0),
                        "adr_pct":     round(adr, 2),
                        "return_pct":  q.get("change_pct", 0),
                    })
                    results["symbols"].append(symbol)
            except Exception:
                pass

        logger.info(
            f"[{format_ist_timestamp()}] Data store updated: "
            f"{results['downloaded']} candles, "
            f"{len(results['symbols'])} symbols, "
            f"{results['failed']} failures"
        )
        return results

    def get_store_summary(self) -> str:
        """Human-readable summary of what's in the store."""
        with sqlite3.connect(CANDLE_DB) as conn:
            total = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
            symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM candles").fetchone()[0]
            oldest  = conn.execute("SELECT MIN(ts_ist) FROM candles").fetchone()[0]
        return (
            f"Data Store: {total:,} candles | "
            f"{symbols} symbols | "
            f"Since: {str(oldest)[:10] if oldest else 'empty'}"
        )


# Singleton
_store: Optional[MarketDataStore] = None

def get_data_store() -> MarketDataStore:
    global _store
    if _store is None:
        _store = MarketDataStore()
    return _store


# ============================================================
# MODULE: self_learning.py
# ============================================================
"""
self_learning.py — NSE Momentum Groww AI Bot
Adaptive AI — Learns from Every Trade, Adapts Parameters Automatically

18yr Pro Rule: "The market changes. Strategies that worked in 2010 fail in 2024.
The only edge that lasts is ADAPTING faster than the market changes."

This module:
1. Reads trade history from trade_journal
2. Computes which patterns / sessions / regimes are actually profitable
3. Auto-adjusts signal thresholds (RSI levels, min score, volume filter)
4. Disables patterns with consistently poor performance
5. Increases size multiplier for high-performing setups
6. Saves adaptive config — applied live the next trading day
7. Sends weekly learning report via Telegram

Self-learning is ADDITIVE — it tightens filters, never loosens risk.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

from utils import format_ist_timestamp, get_current_ist_date
from trade_journal import get_journal

logger = logging.getLogger(__name__)

ADAPTIVE_CONFIG_PATH = "logs/adaptive_config.json"
MIN_TRADES_TO_LEARN  = 10   # Minimum trades before adapting a parameter
MAX_DISABLE_PCT      = 30   # Never disable more than 30% of patterns


@dataclass
class AdaptiveConfig:
    """Live-adjustable strategy parameters. Saved to disk nightly, loaded at startup."""
    # Signal thresholds
    min_signal_score:    float = 65.0
    high_conf_score:     float = 80.0

    # RSI thresholds (adapted per performance)
    rsi_oversold:        float = 35.0
    rsi_overbought:      float = 65.0

    # Volume filter
    min_volume_ratio:    float = 1.5   # Minimum volume vs SMA to take trade

    # Pattern weights (pattern_name → multiplier, 0.0 = disabled)
    pattern_weights: Dict[str, float] = None

    # Session multipliers (session → size multiplier)
    session_multipliers: Dict[str, float] = None

    # Regime multipliers
    regime_multipliers: Dict[str, float] = None

    # Symbol blacklist (persistent bad performers this week)
    symbol_blacklist: List[str] = None

    # Metadata
    last_updated:   str = ""
    total_trades:   int = 0
    overall_win_rate: float = 0.0
    version:        int = 1

    def __post_init__(self):
        if self.pattern_weights is None:
            self.pattern_weights = {}
        if self.session_multipliers is None:
            self.session_multipliers = {
                "OPENING_DRIVE": 1.2,
                "MORNING":       1.0,
                "MIDDAY_CHOP":   0.3,
                "AFTERNOON_TREND": 0.9,
                "EOD":           0.0,
            }
        if self.regime_multipliers is None:
            self.regime_multipliers = {
                "STRONG_TREND_UP":   1.2,
                "STRONG_TREND_DOWN": 1.2,
                "WEAK_TREND_UP":     0.8,
                "WEAK_TREND_DOWN":   0.8,
                "RANGING":           0.2,
                "HIGH_VOLATILITY":   0.6,
                "LOW_VOLATILITY":    0.4,
                "OPENING_DRIVE":     1.2,
                "MIDDAY_CHOP":       0.2,
                "AFTERNOON_TREND":   0.9,
            }
        if self.symbol_blacklist is None:
            self.symbol_blacklist = []
        if not self.last_updated:
            self.last_updated = format_ist_timestamp()


class SelfLearningEngine:
    """
    Reads trade history → computes stats → adapts parameters → saves config.
    Run at EOD after market close, applied next morning at startup.
    """

    def __init__(self):
        self.journal  = get_journal()
        self.config   = self._load_config()

    # -------------------------------------------------------
    # MAIN LEARNING CYCLE — called at EOD
    # -------------------------------------------------------

    def run_learning_cycle(self, lookback_days: int = 20) -> AdaptiveConfig:
        """
        Full adaptive learning pass.
        Returns updated AdaptiveConfig to be used from next session.
        """
        logger.info(f"[{format_ist_timestamp()}] Self-learning cycle started (lookback={lookback_days}d)...")

        trades_df = self.journal.get_trades_last_n_days(lookback_days)
        if trades_df.empty or len(trades_df) < MIN_TRADES_TO_LEARN:
            logger.info(f"[{format_ist_timestamp()}] Insufficient trades ({len(trades_df)}) — keeping current config")
            return self.config

        # 1. Overall performance
        self._adapt_overall_thresholds(trades_df)

        # 2. Pattern-level adaptation
        self._adapt_pattern_weights(trades_df)

        # 3. Session-level adaptation
        self._adapt_session_multipliers(trades_df)

        # 4. Regime-level adaptation
        self._adapt_regime_multipliers(trades_df)

        # 5. Symbol blacklist (3+ consecutive losses on same stock this week)
        self._update_symbol_blacklist(trades_df)

        # 6. RSI threshold adaptation
        self._adapt_rsi_thresholds(trades_df)

        # 7. Signal score threshold
        self._adapt_signal_score(trades_df)

        self.config.last_updated   = format_ist_timestamp()
        self.config.total_trades   = len(trades_df)
        self.config.overall_win_rate = self._win_rate(trades_df)
        self.config.version       += 1

        self._save_config()
        self._log_learning_report()
        return self.config

    # -------------------------------------------------------
    # ADAPTATION METHODS
    # -------------------------------------------------------

    def _adapt_overall_thresholds(self, df: pd.DataFrame):
        """If win rate is low, tighten min_signal_score."""
        wr = self._win_rate(df)
        if wr < 45:
            # Poor win rate — raise the bar
            self.config.min_signal_score = min(self.config.min_signal_score + 3.0, 80.0)
            logger.info(f"[{format_ist_timestamp()}] Low win rate {wr:.1f}% → raising min score to {self.config.min_signal_score}")
        elif wr > 65:
            # Good win rate — can slightly loosen (but not below 60)
            self.config.min_signal_score = max(self.config.min_signal_score - 1.0, 60.0)
            logger.info(f"[{format_ist_timestamp()}] Good win rate {wr:.1f}% → min score {self.config.min_signal_score}")

    def _adapt_pattern_weights(self, df: pd.DataFrame):
        """
        For each pattern:
        - Win rate > 60% and avg_pnl > 0 → increase weight (max 1.5x)
        - Win rate < 40% or avg_pnl < 0 → reduce weight (min 0.3x)
        - Win rate < 30% with 15+ trades → disable (weight = 0)
        """
        if "entry_pattern" not in df.columns:
            return

        # Explode multi-pattern entries
        exploded = df.assign(pattern=df["entry_pattern"].str.split("+")).explode("pattern")
        exploded["pattern"] = exploded["pattern"].str.strip()

        for pattern, grp in exploded.groupby("pattern"):
            if len(grp) < 5:
                continue
            wr      = self._win_rate(grp)
            avg_pnl = grp["net_pnl"].mean() if "net_pnl" in grp.columns else 0

            current = self.config.pattern_weights.get(pattern, 1.0)

            if wr > 60 and avg_pnl > 0:
                new_w = min(current * 1.1, 1.5)
            elif wr < 30 and len(grp) >= 15:
                new_w = 0.0   # Disable
                logger.warning(f"[{format_ist_timestamp()}] Pattern DISABLED: {pattern} (WR={wr:.0f}%)")
            elif wr < 40 or avg_pnl < 0:
                new_w = max(current * 0.85, 0.3)
            else:
                new_w = current

            if new_w != current:
                self.config.pattern_weights[pattern] = round(new_w, 2)
                logger.info(f"[{format_ist_timestamp()}] Pattern {pattern}: weight {current:.2f}→{new_w:.2f} (WR={wr:.0f}%)")

    def _adapt_session_multipliers(self, df: pd.DataFrame):
        """Scale position sizing by session performance."""
        if "session" not in df.columns:
            return
        for session, grp in df.groupby("session"):
            if len(grp) < 5:
                continue
            wr      = self._win_rate(grp)
            avg_pnl = grp["net_pnl"].mean() if "net_pnl" in grp.columns else 0
            current = self.config.session_multipliers.get(session, 1.0)

            if session == "MIDDAY_CHOP":
                # Never increase midday — experience says it's always bad
                new_m = min(current, 0.3)
            elif wr > 60 and avg_pnl > 0:
                new_m = min(current * 1.05, 1.4)
            elif wr < 35:
                new_m = max(current * 0.8, 0.1)
            else:
                new_m = current

            if new_m != current:
                self.config.session_multipliers[session] = round(new_m, 2)

    def _adapt_regime_multipliers(self, df: pd.DataFrame):
        """Scale sizing by regime performance."""
        if "regime" not in df.columns:
            return
        for regime, grp in df.groupby("regime"):
            if len(grp) < 5:
                continue
            wr  = self._win_rate(grp)
            current = self.config.regime_multipliers.get(regime, 1.0)

            if wr > 60:
                new_m = min(current * 1.05, 1.5)
            elif wr < 35:
                new_m = max(current * 0.85, 0.1)
            else:
                new_m = current

            if new_m != current:
                self.config.regime_multipliers[regime] = round(new_m, 2)
                logger.info(f"[{format_ist_timestamp()}] Regime {regime}: multiplier {current:.2f}→{new_m:.2f}")

    def _update_symbol_blacklist(self, df: pd.DataFrame):
        """Blacklist symbols with 3+ losses in last 5 days."""
        if "symbol" not in df.columns:
            return
        recent = df[df["outcome"].isin(["WIN", "LOSS"])].copy()
        if recent.empty:
            return

        # Reset blacklist weekly (Friday EOD)
        from utils import get_current_ist_time
        if get_current_ist_time().weekday() == 4:  # Friday
            self.config.symbol_blacklist = []
            return

        new_blacklist = []
        for symbol, grp in recent.groupby("symbol"):
            losses = sum(1 for o in grp["outcome"] if o == "LOSS")
            if losses >= 3 and len(grp) >= 3:
                consecutive = all(o == "LOSS" for o in grp["outcome"].tail(3))
                if consecutive:
                    new_blacklist.append(symbol)
                    logger.warning(f"[{format_ist_timestamp()}] Symbol blacklisted: {symbol} (3 consecutive losses)")

        self.config.symbol_blacklist = list(set(new_blacklist))

    def _adapt_rsi_thresholds(self, df: pd.DataFrame):
        """
        Find the RSI range that produced best entries.
        Analyse winning trades' RSI at entry vs losing trades'.
        """
        if "rsi_at_entry" not in df.columns or len(df) < 15:
            return
        wins   = df[df["outcome"] == "WIN"]["rsi_at_entry"].dropna()
        losses = df[df["outcome"] == "LOSS"]["rsi_at_entry"].dropna()

        if len(wins) >= 5 and len(losses) >= 5:
            # If winning longs tend to enter at lower RSI, tighten oversold threshold
            long_wins = df[(df["outcome"] == "WIN") & (df["direction"] == "LONG")]["rsi_at_entry"].dropna()
            if len(long_wins) >= 5:
                optimal_rsi_buy = long_wins.median()
                if optimal_rsi_buy < 45:
                    self.config.rsi_oversold = round(max(optimal_rsi_buy - 5, 25), 1)
            short_wins = df[(df["outcome"] == "WIN") & (df["direction"] == "SHORT")]["rsi_at_entry"].dropna()
            if len(short_wins) >= 5:
                optimal_rsi_sell = short_wins.median()
                if optimal_rsi_sell > 55:
                    self.config.rsi_overbought = round(min(optimal_rsi_sell + 5, 80), 1)

    def _adapt_signal_score(self, df: pd.DataFrame):
        """Find minimum signal score that correlates with wins."""
        if "signal_score" not in df.columns or len(df) < 10:
            return
        wins  = df[df["outcome"] == "WIN"]["signal_score"].dropna()
        if len(wins) >= 5:
            # 25th percentile of winning scores = practical minimum
            practical_min = wins.quantile(0.25)
            current = self.config.min_signal_score
            # Move slowly toward optimal
            new_score = current * 0.8 + practical_min * 0.2
            self.config.min_signal_score = round(max(min(new_score, 80), 55), 1)

    # -------------------------------------------------------
    # QUERY HELPERS
    # -------------------------------------------------------

    def get_pattern_weight(self, pattern_name: str) -> float:
        """Return current weight for a pattern (0 = disabled)."""
        return self.config.pattern_weights.get(pattern_name, 1.0)

    def get_session_multiplier(self, session: str) -> float:
        return self.config.session_multipliers.get(session, 1.0)

    def get_regime_multiplier(self, regime: str) -> float:
        return self.config.regime_multipliers.get(regime, 1.0)

    def is_symbol_blacklisted(self, symbol: str) -> bool:
        return symbol in self.config.symbol_blacklist

    def is_pattern_disabled(self, pattern_name: str) -> bool:
        return self.config.pattern_weights.get(pattern_name, 1.0) == 0.0

    def get_adaptive_min_score(self) -> float:
        return self.config.min_signal_score

    # -------------------------------------------------------
    # PERFORMANCE SUMMARY (for Telegram report)
    # -------------------------------------------------------

    def get_learning_summary(self) -> str:
        """Human-readable learning report for Telegram."""
        cfg = self.config
        disabled = [p for p, w in cfg.pattern_weights.items() if w == 0.0]
        boosted  = [p for p, w in cfg.pattern_weights.items() if w > 1.2]

        lines = [
            "🧠 Self-Learning Report",
            f"Updated: {cfg.last_updated}",
            f"Trades analysed: {cfg.total_trades} | Win rate: {cfg.overall_win_rate:.1f}%",
            f"Min signal score: {cfg.min_signal_score:.0f}",
            f"RSI thresholds: oversold={cfg.rsi_oversold} / overbought={cfg.rsi_overbought}",
        ]
        if disabled:
            lines.append(f"⛔ Disabled patterns: {', '.join(disabled)}")
        if boosted:
            lines.append(f"⬆️ Boosted patterns: {', '.join(boosted)}")
        if cfg.symbol_blacklist:
            lines.append(f"🚫 Symbol blacklist: {', '.join(cfg.symbol_blacklist)}")
        return "\n".join(lines)

    # -------------------------------------------------------
    # PERSISTENCE
    # -------------------------------------------------------

    def _save_config(self):
        Path(ADAPTIVE_CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(ADAPTIVE_CONFIG_PATH, "w") as f:
            json.dump(asdict(self.config), f, indent=2)
        logger.info(f"[{format_ist_timestamp()}] Adaptive config saved → {ADAPTIVE_CONFIG_PATH}")

    def _load_config(self) -> AdaptiveConfig:
        if Path(ADAPTIVE_CONFIG_PATH).exists():
            try:
                with open(ADAPTIVE_CONFIG_PATH) as f:
                    data = json.load(f)
                cfg = AdaptiveConfig(**{k: v for k, v in data.items()
                                        if k in AdaptiveConfig.__dataclass_fields__})
                logger.info(f"[{format_ist_timestamp()}] Adaptive config loaded (v{cfg.version})")
                return cfg
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] Could not load adaptive config: {e}")
        return AdaptiveConfig()

    def _log_learning_report(self):
        logger.info(f"[{format_ist_timestamp()}] {self.get_learning_summary()}")

    @staticmethod
    def _win_rate(df: pd.DataFrame) -> float:
        if df.empty or "outcome" not in df.columns:
            return 50.0
        valid = df[df["outcome"].isin(["WIN", "LOSS"])]
        if valid.empty:
            return 50.0
        return valid["outcome"].eq("WIN").mean() * 100


# Singleton
_learner: Optional[SelfLearningEngine] = None

def get_learner() -> SelfLearningEngine:
    global _learner
    if _learner is None:
        _learner = SelfLearningEngine()
    return _learner


# ============================================================
# MODULE: update_token.py
# ============================================================
"""
update_token.py — Auth Diagnostic & Manual Override for KingTrades Bot

The bot is now FULLY AUTOMATIC. You should NEVER need to run this.
  • GROWW_AUTH_TOKEN  = permanent JWT from developer.groww.in (set once, never changes)
  • GROWW_TOTP_SECRET = base32 TOTP secret (set once, never changes)
  • The bot refreshes its access_token every morning at 6:05 AM IST automatically.

Run this ONLY for diagnostics or if auto-refresh is broken:
    python update_token.py          — test auto-refresh
    python update_token.py --test   — same as above
    python update_token.py --status — show current token age / cache info
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST  = ZoneInfo("Asia/Kolkata")
CACHE = Path("data/.token_cache.json")


def show_status():
    """Show current token cache status."""
    print("\n── Token Cache Status ───────────────────────────────────")
    if not CACHE.exists():
        print("  No cache file found at data/.token_cache.json")
    else:
        try:
            data = json.loads(CACHE.read_text())
            api_key      = data.get("api_key", "")
            access_token = data.get("access_token", "")
            ts_str       = data.get("timestamp", "")
            ts = datetime.fromisoformat(ts_str).replace(tzinfo=IST) if ts_str else None
            age_h = ((datetime.now(IST) - ts).total_seconds() / 3600) if ts else None
            print(f"  api_key set:      {'✅ YES (' + api_key[:20] + '...)' if api_key else '❌ NO'}")
            print(f"  access_token set: {'✅ YES (' + access_token[:20] + '...)' if access_token else '❌ NO'}")
            print(f"  timestamp:        {ts_str or 'not set'}")
            if age_h is not None:
                status = "✅ VALID" if age_h < 12 else "⚠️  STALE (>12h)"
                print(f"  token age:        {age_h:.1f}h  {status}")
        except Exception as e:
            print(f"  Cache read error: {e}")

    print("\n── Environment Variables ────────────────────────────────")
    api_key = os.getenv("GROWW_AUTH_TOKEN", "")
    totp    = os.getenv("GROWW_TOTP_SECRET", "")
    print(f"  GROWW_AUTH_TOKEN:  {'✅ SET' if api_key else '❌ NOT SET'}")
    print(f"  GROWW_TOTP_SECRET: {'✅ SET' if totp else '❌ NOT SET'}")
    print()


def test_auto_refresh():
    """Test the automatic TOTP refresh and show the result."""
    print("\n── Testing Automatic TOTP Refresh ───────────────────────")
    print("This simulates what the bot does every morning at 6:05 AM IST.\n")

    try:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from auth_groww import GrowwAuthManager

        mgr = GrowwAuthManager()
        print(f"  api_key loaded:      {'✅ YES' if mgr._api_key else '❌ NO — set GROWW_AUTH_TOKEN'}")
        print(f"  totp_secret loaded:  {'✅ YES' if mgr.totp_secret else '❌ NO — set GROWW_TOTP_SECRET'}\n")

        if not mgr._api_key or not mgr.totp_secret:
            print("Cannot test: missing env vars. Set them in Render Environment Variables.")
            print("  GROWW_AUTH_TOKEN  = permanent JWT from developer.groww.in")
            print("  GROWW_TOTP_SECRET = TOTP base32 secret from same page")
            return False

        print("Calling GrowwAPI.get_access_token()...")
        token = mgr.get_valid_token()

        if token:
            print(f"\n✅ SUCCESS! Access token obtained:")
            print(f"   {token[:30]}...{token[-10:]}")
            print(f"   Length: {len(token)} chars")
            print("\nThe bot will refresh this automatically every morning.")
            print("You never need to manually update any token.")
            return True
        else:
            print("\n❌ FAILED to get access token.")
            print("\nCheck:")
            print("  1. GROWW_AUTH_TOKEN = permanent API key from developer.groww.in → API Keys")
            print("     (NOT the daily token — the permanent key shown on that page)")
            print("  2. GROWW_TOTP_SECRET = base32 TOTP secret from developer.groww.in → API Keys")
            print("  3. Both set correctly in Render → kingtrades-bot → Environment")
            return False

    except ImportError as e:
        print(f"Import error: {e}")
        print("Run from the kingtrades/ directory: python update_token.py")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "--test"

    if arg == "--status":
        show_status()
    else:
        show_status()
        success = test_auto_refresh()
        sys.exit(0 if success else 1)


# ============================================================
# MODULE: watchdog.py
# ============================================================
"""
watchdog.py — NSE Momentum Groww AI Bot
Fully Automatic Process Watchdog

This is the ONLY file you need to run. It handles everything:
  ✅ Starts main.py automatically
  ✅ Restarts main.py if it crashes
  ✅ Skips weekends (no market = no bot)
  ✅ Skips NSE holidays automatically
  ✅ Sends Telegram alert if bot crashes
  ✅ Runs 24/7 in background — main.py manages its own schedule
  ✅ Logs everything to logs/watchdog.log

Usage:
  python watchdog.py          # Start the watchdog (keeps running forever)
  python watchdog.py --once   # Run main.py once without auto-restart (testing)
  python watchdog.py --status # Show current bot status
"""

import argparse
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Paths ─────────────────────────────────────────────────────────
BOT_DIR   = Path(__file__).parent.resolve()
MAIN_PY   = BOT_DIR / "main.py"
VENV_PY   = (
    BOT_DIR / "venv" / "Scripts" / "python.exe"  # Windows
    if platform.system() == "Windows"
    else BOT_DIR / "venv" / "bin" / "python"      # Linux/Mac
)
PYTHON    = str(VENV_PY) if VENV_PY.exists() else sys.executable
LOGS_DIR  = BOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
STATUS_FILE = BOT_DIR / "data" / "watchdog_status.json"
STATUS_FILE.parent.mkdir(exist_ok=True)

IST = ZoneInfo("Asia/Kolkata")

# ── NSE Holidays 2025-2026 (add more as announced) ────────────────
NSE_HOLIDAYS = {
    # 2025
    "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31",
    "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
    "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-02",
    "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25",
    # 2026
    "2026-01-26", "2026-03-02", "2026-03-20", "2026-04-03",
    "2026-04-14", "2026-04-17", "2026-05-01", "2026-07-17",
    "2026-08-15", "2026-09-04", "2026-10-01", "2026-10-09",
    "2026-10-28", "2026-11-06", "2026-12-25",
}

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOGS_DIR / "watchdog.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("watchdog")


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def ist_now() -> datetime:
    return datetime.now(tz=IST)


def is_trading_day() -> bool:
    """True if today is a weekday and not an NSE holiday."""
    now = ist_now()
    if now.weekday() >= 5:           # Saturday=5, Sunday=6
        return False
    date_str = now.strftime("%Y-%m-%d")
    return date_str not in NSE_HOLIDAYS


def seconds_until(target_hour: int, target_min: int = 0) -> float:
    """Seconds until target time TODAY in IST (negative if already past)."""
    now = ist_now()
    target = now.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
    return (target - now).total_seconds()


def seconds_until_next_weekday_morning() -> float:
    """Seconds until 8:40 AM IST on the next trading day."""
    now = ist_now()
    candidate = now + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
        candidate += timedelta(days=1)
    target = candidate.replace(hour=8, minute=40, second=0, microsecond=0)
    return (target - now).total_seconds()


def send_telegram(message: str) -> None:
    """Send a Telegram message without importing the full bot stack."""
    try:
        from dotenv import load_dotenv
        import requests as req
        load_dotenv(BOT_DIR / ".env")
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass  # Never let Telegram failure break the watchdog


def save_status(state: str, pid: int = 0, crashes: int = 0, last_start: str = "") -> None:
    data = {
        "state":      state,
        "pid":        pid,
        "crashes":    crashes,
        "last_start": last_start,
        "updated":    ist_now().strftime("%Y-%m-%d %H:%M:%S IST"),
    }
    try:
        STATUS_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def load_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────
# MAIN WATCHDOG LOOP
# ─────────────────────────────────────────────────────────────────

class Watchdog:
    MAX_CRASH_BACKOFF = 300   # Max 5-minute wait between restarts
    CRASH_ALERT_THRESHOLD = 3 # Alert on Telegram after 3 crashes

    def __init__(self, once: bool = False):
        self.once       = once
        self.crashes    = 0
        self.running    = True
        self.process: subprocess.Popen | None = None
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT,  self._handle_signal)

    def _handle_signal(self, signum, frame):
        log.info(f"Watchdog received signal {signum} — shutting down gracefully")
        self.running = False
        if self.process and self.process.poll() is None:
            log.info("Terminating main.py process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
        save_status("STOPPED")
        sys.exit(0)

    def _wait_if_weekend_or_holiday(self) -> None:
        """Sleep until next trading day if needed."""
        if is_trading_day():
            return
        now = ist_now()
        secs = seconds_until_next_weekday_morning()
        wake = ist_now() + timedelta(seconds=secs)
        day_type = "weekend" if now.weekday() >= 5 else "NSE holiday"
        log.info(
            f"Today ({now.strftime('%A %Y-%m-%d')}) is a {day_type}. "
            f"Sleeping until {wake.strftime('%A %Y-%m-%d %H:%M')} IST "
            f"({secs/3600:.1f} hours)"
        )
        save_status(f"SLEEPING — {day_type}")
        send_telegram(
            f"🌙 <b>KingTrades Watchdog</b>\n"
            f"Today is a {day_type}. Bot will resume on\n"
            f"<b>{wake.strftime('%A, %d %b %Y at %H:%M IST')}</b>"
        )
        # Sleep in 1-hour chunks so we re-check (in case holiday list updates)
        while secs > 0 and self.running:
            sleep_chunk = min(3600, secs)
            time.sleep(sleep_chunk)
            secs -= sleep_chunk
            if not is_trading_day():
                secs = seconds_until_next_weekday_morning()

    def _wait_for_trading_time(self) -> None:
        """If it's before 8:40 AM IST, wait."""
        secs = seconds_until(8, 40)
        if secs > 0:
            wake = ist_now() + timedelta(seconds=secs)
            log.info(
                f"Market prep time (8:40 AM IST) is "
                f"{secs/60:.0f} minutes away. "
                f"Sleeping until {wake.strftime('%H:%M IST')}"
            )
            save_status("WAITING — pre-market")
            time.sleep(secs)

    def _wait_after_market_close(self) -> None:
        """After market close, wait until next trading day."""
        secs = seconds_until_next_weekday_morning()
        wake = ist_now() + timedelta(seconds=secs)
        log.info(
            f"Market day complete. Sleeping until "
            f"{wake.strftime('%A %Y-%m-%d %H:%M IST')} "
            f"({secs/3600:.1f} hours)"
        )
        save_status("SLEEPING — post-market")
        # Sleep in chunks
        while secs > 0 and self.running:
            time.sleep(min(3600, secs))
            secs -= 3600

    def _preflight_check(self) -> bool:
        """
        Quick sanity checks before launching main.py:
        1. Token cache age — if >20h, trigger a pre-emptive refresh so main.py
           doesn't start with a stale token.
        2. Connectivity — light HTTP check to api.groww.in.

        Always returns True (non-fatal) — main.py handles failures gracefully.
        Problems are logged so user sees them in Render logs.
        """
        import json as _json
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI

        _ist = _ZI("Asia/Kolkata")

        # ── 1. Token cache age ───────────────────────────────────────────────
        token_cache = BOT_DIR / "data" / ".token_cache.json"
        token_age_h = float("inf")
        if token_cache.exists():
            try:
                data = _json.loads(token_cache.read_text())
                ts_str = data.get("timestamp", "")
                if ts_str:
                    ts = _dt.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_ist)
                    token_age_h = (_dt.now(tz=_ist) - ts).total_seconds() / 3600
                    log.info(f"Pre-flight: token cache age {token_age_h:.1f}h")
            except Exception as e:
                log.warning(f"Pre-flight: token cache read error: {e}")
        else:
            log.warning("Pre-flight: no token cache found — main.py will use env token")

        if token_age_h > 20:
            log.warning(
                f"Pre-flight: token is {token_age_h:.1f}h old — "
                "running pre-emptive token refresh before starting main.py..."
            )
            try:
                result = subprocess.run(
                    [PYTHON, "-c",
                     "import logging; logging.basicConfig(level=logging.WARNING); "
                     "from auth_groww import initialize_auth; initialize_auth()"],
                    cwd=str(BOT_DIR),
                    timeout=90,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    log.info("Pre-flight: token refresh subprocess OK")
                else:
                    log.warning(
                        f"Pre-flight: token refresh subprocess returned {result.returncode}: "
                        f"{result.stderr[:300]}"
                    )
            except subprocess.TimeoutExpired:
                log.warning("Pre-flight: token refresh timed out (main.py will retry)")
            except Exception as e:
                log.warning(f"Pre-flight: token refresh error: {e}")

        # ── 2. Basic connectivity check ──────────────────────────────────────
        try:
            import urllib.request as _urlreq
            _urlreq.urlopen("https://api.groww.in", timeout=10)
            log.info("Pre-flight: Groww API reachable ✅")
        except Exception as e:
            # Non-fatal — Render occasionally has slow cold starts
            log.warning(f"Pre-flight: Groww API connectivity: {e} (continuing anyway)")

        return True

    def _run_main_once(self) -> int:
        """Launch main.py and wait for it to finish. Returns exit code."""
        cmd = [PYTHON, str(MAIN_PY)]
        start_time = ist_now().strftime("%Y-%m-%d %H:%M:%S IST")
        log.info(f"Starting main.py | Python: {PYTHON} | Time: {start_time}")
        save_status("RUNNING", last_start=start_time)

        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(BOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,          # Line-buffered
                encoding="utf-8",
                errors="replace",
            )
            save_status("RUNNING", pid=self.process.pid, crashes=self.crashes, last_start=start_time)

            # Stream output to watchdog log in real time
            log_file_path = LOGS_DIR / f"trading_{ist_now().strftime('%Y-%m-%d')}.log"
            with open(log_file_path, "a", encoding="utf-8") as log_file:
                for line in self.process.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    log_file.write(line)
                    log_file.flush()

            self.process.wait()
            return self.process.returncode

        except FileNotFoundError:
            log.critical(
                f"Cannot find Python at: {PYTHON}\n"
                "Run: python setup_autostart.py — to fix the path"
            )
            return -1
        except Exception as e:
            log.error(f"Failed to start main.py: {e}")
            return -1

    def run(self) -> None:
        """Main watchdog loop."""
        log.info("=" * 60)
        log.info("  KingTrades Watchdog starting")
        log.info(f"  Bot dir:  {BOT_DIR}")
        log.info(f"  Python:   {PYTHON}")
        log.info(f"  IST time: {ist_now().strftime('%Y-%m-%d %H:%M:%S')}")
        log.info("=" * 60)

        send_telegram(
            f"🤖 <b>KingTrades Watchdog Started</b>\n"
            f"Time: {ist_now().strftime('%d %b %Y %H:%M IST')}\n"
            f"Will trade on next market day automatically."
        )

        while self.running:
            # 1. Skip weekends and holidays
            self._wait_if_weekend_or_holiday()
            if not self.running:
                break

            # 2. Wait until 8:40 AM IST
            self._wait_for_trading_time()
            if not self.running:
                break

            # 3. Pre-flight: token freshness + connectivity
            self._preflight_check()

            # 4. Run main.py for the day
            log.info(f"Launching main.py for {ist_now().strftime('%A %d %b %Y')}")
            exit_code = self._run_main_once()

            if self.once:
                log.info("--once flag: exiting after single run")
                break

            # 4. Handle exit
            if exit_code == 0:
                # Clean shutdown (normal EOD)
                log.info("main.py exited cleanly (EOD shutdown)")
                self.crashes = 0
                self._wait_after_market_close()

            elif exit_code == 42:
                # Special code: bot sent kill-switch — do NOT restart today
                log.warning("Kill-switch exit (code 42) — not restarting today")
                save_status("KILLED — manual stop")
                send_telegram(
                    "🛑 <b>KingTrades KILL-SWITCH activated</b>\n"
                    "Bot will not restart until next trading day."
                )
                self._wait_after_market_close()

            else:
                # Crash — restart with backoff
                self.crashes += 1
                backoff = min(30 * (2 ** (self.crashes - 1)), self.MAX_CRASH_BACKOFF)
                now_ist = ist_now()
                log.error(
                    f"main.py crashed (exit code {exit_code}) | "
                    f"Crash #{self.crashes} | Restarting in {backoff}s"
                )
                save_status(f"CRASHED #{self.crashes} — restarting", crashes=self.crashes)

                if self.crashes >= self.CRASH_ALERT_THRESHOLD:
                    send_telegram(
                        f"⚠️ <b>KingTrades Crash Alert</b>\n"
                        f"Crash #{self.crashes} at {now_ist.strftime('%H:%M IST')}\n"
                        f"Exit code: {exit_code}\n"
                        f"Restarting in {backoff}s...\n"
                        f"Check logs/watchdog.log if this repeats."
                    )

                # Don't restart if market is already closed (after 3:35 PM IST)
                after_market = seconds_until(15, 35) < 0
                if after_market:
                    log.info("Market closed — not restarting today, waiting for tomorrow")
                    self.crashes = 0
                    self._wait_after_market_close()
                else:
                    time.sleep(backoff)

        save_status("STOPPED")
        log.info("Watchdog stopped.")


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def print_status() -> None:
    s = load_status()
    if not s:
        print("No status file found. Has the bot run yet?")
        return
    print("\nKingTrades Bot Status")
    print("─" * 40)
    for k, v in s.items():
        print(f"  {k:<12}: {v}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="KingTrades Watchdog")
    parser.add_argument("--once",   action="store_true", help="Run once, no auto-restart")
    parser.add_argument("--status", action="store_true", help="Show bot status and exit")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    wd = Watchdog(once=args.once)
    wd.run()


if __name__ == "__main__":
    main()


# ============================================================
# MODULE: setup_autostart.py
# ============================================================
"""
setup_autostart.py — KingTrades One-Click Auto-Start Setup
=============================================================
Run this ONCE to configure the bot to start automatically when your PC boots.

Windows: Creates a Windows Task Scheduler task that runs watchdog.py
         at user logon — silently in the background.

Linux:   Creates a systemd user service (no root required).

Usage:
  python setup_autostart.py          # Install auto-start
  python setup_autostart.py remove   # Remove auto-start
  python setup_autostart.py status   # Check current status
  python setup_autostart.py test     # Run bot once to test (no restart loop)
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

BOT_DIR  = Path(__file__).parent.resolve()
SYSTEM   = platform.system()

# Python interpreter inside the venv
VENV_PY = (
    BOT_DIR / "venv" / "Scripts" / "python.exe"
    if SYSTEM == "Windows"
    else BOT_DIR / "venv" / "bin" / "python"
)
PYTHON = str(VENV_PY) if VENV_PY.exists() else sys.executable

TASK_NAME = "KingTrades_NSE_Bot"
VBS_FILE  = BOT_DIR / "run_silent.vbs"


def banner(text: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {text}")
    print('=' * 55)


# ─────────────────────────────────────────────────────────────────
# WINDOWS
# ─────────────────────────────────────────────────────────────────

def windows_create_vbs() -> None:
    """
    Create a VBScript launcher that runs watchdog.py with NO visible window.
    Double-clicking this starts the bot completely silently.
    """
    vbs = f'''
' KingTrades Silent Launcher — auto-generated by setup_autostart.py
' Runs the watchdog in the background with no console window.
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & "{PYTHON}" & Chr(34) & " " & Chr(34) & "{BOT_DIR / 'watchdog.py'}" & Chr(34), 0, False
Set WshShell = Nothing
'''.strip()
    VBS_FILE.write_text(vbs, encoding="utf-8")
    print(f"  Created: {VBS_FILE}")


def windows_install() -> None:
    banner("Installing Windows Auto-Start (Task Scheduler)")

    # Create the VBScript silent launcher
    windows_create_vbs()

    # Build schtasks command
    # Trigger: at logon of current user
    # Action: run the VBScript (which silently starts watchdog.py)
    # RunLevel: HighestAvailable = runs with normal user privileges
    cmd = [
        "schtasks", "/create", "/f",
        "/tn",  TASK_NAME,
        "/tr",  f'wscript.exe "{VBS_FILE}"',
        "/sc",  "ONLOGON",
        "/ru",  os.getenv("USERNAME", ""),
        "/rl",  "HIGHEST",
        "/it",                       # Only when user is logged in
    ]

    print(f"\n  Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print("  SUCCESS: Task created in Windows Task Scheduler")
        print(f"\n  Task name : {TASK_NAME}")
        print(f"  Trigger   : At logon (auto-start when you log in to Windows)")
        print(f"  Action    : {VBS_FILE}")
        print(f"  Window    : Hidden (runs silently in background)")
        print(f"\n  The bot will now start AUTOMATICALLY every time you log in.")
        print(f"  To see it: open Task Manager → Details → look for python.exe")
    else:
        print(f"  ERROR: {result.stderr.strip()}")
        print("\n  FALLBACK: Adding to Startup folder instead...")
        windows_startup_folder_fallback()


def windows_startup_folder_fallback() -> None:
    """Add VBScript to Windows Startup folder as a fallback."""
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        )
        startup = winreg.QueryValueEx(key, "Startup")[0]
        winreg.CloseKey(key)
    except Exception:
        startup = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    import shutil
    dest = Path(startup) / "KingTrades_Bot.vbs"
    shutil.copy(VBS_FILE, dest)
    print(f"\n  Startup shortcut created: {dest}")
    print("  Bot will start silently when you log into Windows.")


def windows_remove() -> None:
    banner("Removing Windows Auto-Start")
    result = subprocess.run(
        ["schtasks", "/delete", "/f", "/tn", TASK_NAME],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"  Removed Task Scheduler task: {TASK_NAME}")
    else:
        print(f"  Task not found or already removed: {result.stderr.strip()}")

    # Also clean up startup folder
    startup_vbs = (
        Path.home() / "AppData" / "Roaming" / "Microsoft"
        / "Windows" / "Start Menu" / "Programs" / "Startup"
        / "KingTrades_Bot.vbs"
    )
    if startup_vbs.exists():
        startup_vbs.unlink()
        print(f"  Removed startup shortcut: {startup_vbs}")

    if VBS_FILE.exists():
        VBS_FILE.unlink()
        print(f"  Removed: {VBS_FILE}")

    print("\n  Auto-start has been removed.")


def windows_status() -> None:
    banner("Windows Task Scheduler Status")
    result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"  Task '{TASK_NAME}' not found. Run: python setup_autostart.py")


# ─────────────────────────────────────────────────────────────────
# LINUX / MAC
# ─────────────────────────────────────────────────────────────────

def linux_install() -> None:
    banner(f"Installing Linux Auto-Start (systemd user service)")

    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_file = service_dir / "kingtrades.service"

    service_content = f"""[Unit]
Description=KingTrades NSE Momentum Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={BOT_DIR}
ExecStart={PYTHON} {BOT_DIR / "watchdog.py"}
Restart=always
RestartSec=60
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
    service_file.write_text(service_content)
    print(f"  Created: {service_file}")

    cmds = [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "kingtrades"],
        ["systemctl", "--user", "start", "kingtrades"],
        ["loginctl",  "enable-linger", os.getenv("USER", "")],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        status = "OK" if result.returncode == 0 else f"WARN: {result.stderr.strip()}"
        print(f"  {' '.join(cmd)} → {status}")

    print("\n  SUCCESS: systemd user service installed")
    print("  The bot will start automatically when your PC boots (even without login)")
    print("\n  Useful commands:")
    print("    systemctl --user status kingtrades   # Check status")
    print("    systemctl --user restart kingtrades  # Restart bot")
    print("    journalctl --user -u kingtrades -f   # Live logs")


def linux_remove() -> None:
    banner("Removing Linux Auto-Start")
    cmds = [
        ["systemctl", "--user", "stop",    "kingtrades"],
        ["systemctl", "--user", "disable", "kingtrades"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, capture_output=True)
        print(f"  {' '.join(cmd)} → done")

    service_file = Path.home() / ".config" / "systemd" / "user" / "kingtrades.service"
    if service_file.exists():
        service_file.unlink()
        print(f"  Removed: {service_file}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    print("\n  Auto-start removed.")


def linux_status() -> None:
    banner("Linux Service Status")
    subprocess.run(["systemctl", "--user", "status", "kingtrades"])


# ─────────────────────────────────────────────────────────────────
# SHARED
# ─────────────────────────────────────────────────────────────────

def pre_checks() -> bool:
    """Verify everything is in place before setting up auto-start."""
    banner("Pre-flight Checks")
    ok = True

    checks = [
        (VENV_PY.exists(),       f"venv/{'Scripts' if SYSTEM=='Windows' else 'bin'}/python", "Run: python -m venv venv && pip install -r requirements.txt"),
        ((BOT_DIR / "main.py").exists(),     "main.py",     "Bot files missing — git clone the repo again"),
        ((BOT_DIR / "watchdog.py").exists(), "watchdog.py", "watchdog.py missing"),
        ((BOT_DIR / ".env").exists(),        ".env file",   "Copy .env.example to .env and fill in credentials"),
    ]

    for condition, label, fix in checks:
        status = "OK" if condition else "MISSING"
        icon   = "✅" if condition else "❌"
        print(f"  {icon} {label:<35} {status}")
        if not condition:
            print(f"       Fix: {fix}")
            ok = False

    # Check .env has required keys
    if (BOT_DIR / ".env").exists():
        env_text = (BOT_DIR / ".env").read_text()
        required = ["GROWW_EMAIL", "GROWW_TOTP_SECRET", "TELEGRAM_BOT_TOKEN"]
        for key in required:
            filled = key in env_text and f"{key}=" in env_text and \
                     env_text.split(f"{key}=")[1].split("\n")[0].strip() not in ("", "your_value_here")
            icon = "✅" if filled else "⚠️ "
            print(f"  {icon} .env: {key:<30} {'SET' if filled else 'NOT SET (fill in .env)'}")

    return ok


def test_run() -> None:
    """Run the bot once in test mode (no restart loop)."""
    banner("Test Run (single run, no auto-restart)")
    print(f"  Running: {PYTHON} watchdog.py --once")
    print("  Press Ctrl+C to stop\n")
    subprocess.run([PYTHON, str(BOT_DIR / "watchdog.py"), "--once"])


def main() -> None:
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "install"

    if action == "install":
        if not pre_checks():
            print("\n⚠️  Fix the above issues before setting up auto-start.\n")
            return
        print()
        if SYSTEM == "Windows":
            windows_install()
        else:
            linux_install()
        print("\n" + "─" * 55)
        print("  SETUP COMPLETE")
        print("  The bot will now start automatically when your PC boots.")
        print("  No manual action needed on trading days.")
        print("─" * 55)

    elif action == "remove":
        if SYSTEM == "Windows":
            windows_remove()
        else:
            linux_remove()

    elif action == "status":
        pre_checks()
        if SYSTEM == "Windows":
            windows_status()
        else:
            linux_status()
        # Also show watchdog status
        status_file = BOT_DIR / "data" / "watchdog_status.json"
        if status_file.exists():
            import json
            s = json.loads(status_file.read_text())
            print("\nWatchdog last known state:")
            for k, v in s.items():
                print(f"  {k:<12}: {v}")

    elif action == "test":
        test_run()

    elif action == "uninstall":
        if SYSTEM == "Windows":
            windows_remove()
        else:
            linux_remove()

    else:
        print(__doc__)


if __name__ == "__main__":
    main()


# ============================================================
# MODULE: main.py
# ============================================================
"""
main.py — NSE Momentum Groww AI Bot
Central Orchestrator with IST Market Hours + Auto-Shutdown + AI Learning

⚠️ WARNING: THIS BOT PLACES REAL ORDERS WITH REAL MONEY ON GROWW.
⚠️ Server runs in UK (UTC) — ALL market logic uses IST (Asia/Kolkata).
⚠️ Start with LIVE_TRADING_ENABLED=False until confident in the setup.
⚠️ Monitor manually for at least 2 weeks before increasing capital.

Full lifecycle:
  [24/7 Background]   continuous_learner.py — downloads data, learns, adapts
  [08:00 AM IST]      Overnight analysis (global markets, Gift Nifty, VIX)
  [08:30 AM IST]      Morning brief → Telegram
  [08:45 AM IST]      TOTP login to Groww → token refresh
  [09:00 AM IST]      Pre-market watchlist scan + AI stock assessment
  [09:15 AM IST]      Market open → trading begins
  [09:15–10:00 IST]   OPENING DRIVE — most aggressive momentum window
  [10:00–11:00 IST]   Morning session — normal trading
  [11:00–13:00 IST]   MIDDAY CHOP — 70% reduced size / skip
  [13:30–15:00 IST]   Afternoon trend — institutional activity
  [15:15 AM IST]      Square-off warning sent
  [15:20 AM IST]      Force close all positions
  [15:30 AM IST]      EOD shutdown
  [16:00 PM IST]      Self-learning cycle
  [16:30 PM IST]      AI trade review → lessons extracted
  [17:00 PM IST]      Incremental trainer run
"""

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
from zoneinfo import ZoneInfo

from utils import (
    format_ist_timestamp, get_current_ist_time, is_market_open_ist,
    is_pre_market_ist, should_force_squareoff_ist, is_squareoff_time_ist,
    minutes_until_market_open, minutes_until_market_close,
    is_token_refresh_time, setup_logging, is_market_day_ist
)
import config

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class TradingBot:
    """
    Main NSE Momentum Trading Bot Orchestrator.
    Handles the full lifecycle from startup to EOD shutdown.
    """

    def __init__(self):
        self.running = False
        self.market_open_today = False
        self.eod_done = False
        self._token_refreshed_today = False
        self._token_refreshed_date = ""   # "YYYY-MM-DD" — prevents double-refresh
        self._day_initialized = False
        self._scan_interval = 60  # seconds between full watchlist scans

        # Modules (initialized lazily after auth)
        self.fetcher = None
        self.risk_manager = None
        self.executor = None
        self.signal_gen = None
        self.alerter = None
        self.news_filter = None
        self.watchlist_mgr = None
        self.dashboard = None
        self.mtf_analyzer = None
        self.journal = None
        self.learner = None
        self.ai_brain = None
        self.overnight = None
        self.calendar = None
        self.data_store = None
        self.cont_learner = None
        self.oc_analyzer = None   # Option Chain analyzer
        self.fii_tracker = None   # FII/DII flow tracker
        self.gap_analyzer = None  # Pre-market gap analyzer
        self._last_trade_date = ""
        self._overnight_run_today = False

        # Automation state
        self._watchlist_cache: List[str] = []
        self._watchlist_cache_time: Optional[datetime] = None
        self._watchlist_cache_ttl = 900       # 15 min cache
        self._last_heartbeat_min = -1          # track heartbeat by minute
        self._token_refresh_attempts = 0
        self._premarket_scan_done = False
        self._capital_file = Path("data/capital.json")
        self._capital_file.parent.mkdir(exist_ok=True)
        self._last_reconcile_time: Optional[datetime] = None  # position reconciliation
        self._weekly_pnl_file = Path(config.WEEKLY_DATA_FILE)
        self._weekly_pnl_file.parent.mkdir(exist_ok=True)
        self._weekly_mode: str = "NORMAL"   # NORMAL / PROTECT / LOCKED

    # --------------------------------------------------------
    # STARTUP
    # --------------------------------------------------------

    def initialize(self) -> bool:
        """Initialize all bot components. Returns True if ready."""
        logger.info(f"[{format_ist_timestamp()}] 🚀 NSE Momentum Bot initializing...")
        logger.info(f"[{format_ist_timestamp()}] Server time: {datetime.now(IST).astimezone(ZoneInfo('UTC'))} UTC")
        logger.info(f"[{format_ist_timestamp()}] IST time: {format_ist_timestamp()}")

        # Validate config
        issues = config.validate_config()
        if issues:
            for issue in issues:
                logger.warning(f"[{format_ist_timestamp()}] Config warning: {issue}")

        if config.LIVE_TRADING_ENABLED:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚡⚡⚡ LIVE TRADING ENABLED ⚡⚡⚡\n"
                "REAL ORDERS WILL BE PLACED ON GROWW. "
                "Capital: " + str(config.MAX_DAILY_CAPITAL)
            )
        else:
            logger.info(f"[{format_ist_timestamp()}] 🔒 DRY RUN MODE — No real orders")

        # Initialize Groww auth
        from auth_groww import initialize_auth, get_auth_manager
        if not initialize_auth():
            logger.warning(f"[{format_ist_timestamp()}] Auth init incomplete — will retry at 8:45 AM IST")

        # Initialize data fetcher
        from data_fetch_groww import GrowwDataFetcher
        self.fetcher = GrowwDataFetcher()

        # Initialize risk manager
        from risk_manager import RiskManager
        self.risk_manager = RiskManager(
            max_daily_capital=config.MAX_DAILY_CAPITAL,
            max_risk_pct=config.MAX_RISK_PER_TRADE_PCT,
            daily_loss_limit_pct=config.DAILY_LOSS_LIMIT_PCT,
            max_positions=config.MAX_POSITIONS,
            nifty_circuit_pct=config.NIFTY_CIRCUIT_PCT,
            consecutive_loss_limit=config.CONSECUTIVE_LOSS_LIMIT,
            pause_minutes=config.PAUSE_AFTER_LOSSES_MINUTES,
        )

        # Initialize executor
        from execution_groww import GrowwExecutor
        self.executor = GrowwExecutor(
            self.risk_manager,
            live_enabled=config.LIVE_TRADING_ENABLED
        )

        # Initialize news filter
        from news_filter import NewsFilter
        self.news_filter = NewsFilter(
            news_api_key=config.NEWS_API_KEY,
            blackout_minutes=config.NEWS_BLACKOUT_MINUTES
        )

        # Initialize watchlist manager
        from watchlist_manager import WatchlistManager
        self.watchlist_mgr = WatchlistManager()

        # Initialize signal generator
        from signal_generator import SignalGenerator
        self.signal_gen = SignalGenerator(
            data_fetcher=self.fetcher,
            news_filter=self.news_filter,
            min_signal_score=config.MIN_SIGNAL_SCORE,
            high_confidence_score=config.HIGH_CONFIDENCE_SCORE,
        )

        # Initialize Telegram alerter
        from alerts_telegram import TelegramAlerter
        self.alerter = TelegramAlerter(
            bot_token=config.TELEGRAM_BOT_TOKEN,
            chat_id=config.TELEGRAM_CHAT_ID,
        )

        # Initialize AI Brain (Claude-powered market intelligence)
        try:
            from ai_brain import get_ai_brain
            self.ai_brain = get_ai_brain()
            logger.info(f"[{format_ist_timestamp()}] AI Brain initialized "
                        f"({'Claude API connected' if self.ai_brain._enabled else 'rule-based mode'})")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] AI Brain failed: {e}")

        # Initialize market data store
        try:
            from market_data_store import get_data_store
            self.data_store = get_data_store()
            logger.info(f"[{format_ist_timestamp()}] {self.data_store.get_store_summary()}")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Data store failed: {e}")

        # Initialize overnight analyzer
        try:
            from overnight_analyzer import get_overnight_analyzer
            self.overnight = get_overnight_analyzer()
            logger.info(f"[{format_ist_timestamp()}] Overnight analyzer ready")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Overnight analyzer failed: {e}")

        # Initialize economic calendar
        try:
            from economic_calendar import get_calendar
            self.calendar = get_calendar()
            events = self.calendar.get_today_events()
            if events:
                logger.info(f"[{format_ist_timestamp()}] Today's events: "
                            + ", ".join(e['event'] for e in events))
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Calendar failed: {e}")

        # Initialize trade journal (SQLite)
        from trade_journal import get_journal
        self.journal = get_journal()
        logger.info(f"[{format_ist_timestamp()}] Trade journal ready")

        # Initialize self-learning engine
        from self_learning import get_learner
        self.learner = get_learner()
        adaptive_cfg = self.learner.config
        logger.info(
            f"[{format_ist_timestamp()}] Self-learning loaded "
            f"(v{adaptive_cfg.version}, min_score={adaptive_cfg.min_signal_score:.0f}, "
            f"WR history={adaptive_cfg.overall_win_rate:.1f}%)"
        )

        # Apply adaptive thresholds to signal generator (from self-learning)
        if self.signal_gen:
            self.signal_gen.min_score = adaptive_cfg.min_signal_score

        # Apply EOD-trained parameters (from yesterday's walk-forward optimization)
        self._apply_eod_trained_params()

        # Initialize MTF analyzer
        from multi_timeframe import MultiTimeframeAnalyzer
        self.mtf_analyzer = MultiTimeframeAnalyzer()
        logger.info(f"[{format_ist_timestamp()}] MTF analyzer ready")

        # Initialize institutional intelligence modules
        try:
            from option_chain import get_option_chain_analyzer
            self.oc_analyzer = get_option_chain_analyzer()
            logger.info(f"[{format_ist_timestamp()}] Option Chain analyzer ready")
        except Exception as e:
            self.oc_analyzer = None
            logger.warning(f"[{format_ist_timestamp()}] Option Chain init failed: {e}")

        try:
            from fii_dii_tracker import get_fii_dii_tracker
            self.fii_tracker = get_fii_dii_tracker()
            logger.info(f"[{format_ist_timestamp()}] FII/DII tracker ready")
        except Exception as e:
            self.fii_tracker = None
            logger.warning(f"[{format_ist_timestamp()}] FII/DII tracker init failed: {e}")

        # Initialize dashboard (wired to journal)
        from dashboard import PerformanceDashboard
        self.dashboard = PerformanceDashboard(journal=self.journal, alerter=self.alerter)

        # Start Telegram command listener (background thread)
        self._start_telegram_listener()

        # Start continuous learner in background (24/7 learning)
        try:
            from continuous_learner import ContinuousLearner
            self.cont_learner = ContinuousLearner()
            self.cont_learner.start(blocking=False)
            logger.info(f"[{format_ist_timestamp()}] Continuous learner started (background)")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Continuous learner failed: {e}")

        logger.info(f"[{format_ist_timestamp()}] ✅ Bot initialized successfully")
        return True

    # --------------------------------------------------------
    # MARKET DAY INITIALIZATION
    # --------------------------------------------------------

    def _api_health_check(self) -> bool:
        """
        Verify Groww API is responding before starting the trading session.
        Prevents silent failures where orders appear to place but don't execute.

        Returns True if API is healthy, False if there's a connectivity issue.
        """
        if not config.API_HEALTH_CHECK_ENABLED:
            return True

        try:
            logger.info(f"[{format_ist_timestamp()}] Running API health check...")

            # 1. Check auth token is valid
            from auth_groww import get_auth_manager
            manager = get_auth_manager()
            token = manager.get_valid_token()
            if not token:
                logger.error(
                    f"[{format_ist_timestamp()}] ❌ API health: No valid auth token. "
                    "Run python update_token.py --test to diagnose."
                )
                if self.alerter:
                    self.alerter.send_text(
                        "🚨 API HEALTH FAIL: No valid Groww token at market open!\n"
                        "Bot will NOT trade today. Fix: check GROWW_TOTP_SECRET in .env"
                    )
                return False

            # 2. Test a quote fetch (proves API is connected and token works)
            from data_fetch_groww import get_data_fetcher
            fetcher = get_data_fetcher()
            test_quote = fetcher.get_quote("RELIANCE")
            if not test_quote or not test_quote.get("ltp"):
                logger.warning(
                    f"[{format_ist_timestamp()}] ⚠️ API health: Quote fetch returned "
                    f"empty for RELIANCE. Market may not be open yet or API is slow."
                )
                # Non-fatal — market may be just opening
                return True

            ltp = test_quote.get("ltp", 0)
            logger.info(
                f"[{format_ist_timestamp()}] ✅ API health OK — "
                f"RELIANCE LTP: ₹{ltp:.2f} | Token valid"
            )
            return True

        except Exception as e:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ API health check failed: {e}"
            )
            if self.alerter:
                self.alerter.send_text(
                    f"🚨 API HEALTH FAIL at market open: {e}\n"
                    "Check Groww connectivity and token."
                )
            return False

    def initialize_market_day(self):
        """Called once at market open each day (9:15 AM IST)."""
        if self._day_initialized:
            return

        logger.info(f"[{format_ist_timestamp()}] 🔔 MARKET OPEN — Initializing trading day...")

        # ── API health check — verify connectivity before any trading ──
        api_ok = self._api_health_check()
        if not api_ok:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚠️ API health check failed — "
                "bot will continue but trading may be impaired. Monitor closely."
            )
            # Not fatal — allow the day to proceed but alert was sent

        # Get live balance from Groww; fall back to auto-compounded capital
        balance_info = self.fetcher.get_account_balance()
        available = balance_info.get("available", 0)
        if available == 0:
            available = self._load_compounded_capital()

        # Sync any open positions from Groww (recovery after restart)
        self._sync_positions_from_groww()

        # Get Nifty opening price
        nifty_q = self.fetcher.get_nifty_quote()
        nifty_open = nifty_q.get("ltp", 0) if nifty_q else 0

        # Initialize risk manager for the day
        self.risk_manager.initialize_day(available, nifty_open)

        # Build watchlist (sector filter from overnight analysis)
        watchlist = self.watchlist_mgr.get_watchlist(
            data_fetcher=self.fetcher, learner=self.learner
        )
        if self.overnight:
            avoid_sectors = self.overnight.get_sectors_to_avoid()
            if avoid_sectors:
                watchlist = [
                    s for s in watchlist
                    if self.watchlist_mgr.get_sector_for_symbol(s) not in avoid_sectors
                ]

        # ── Pre-load gap analysis for all watchlist symbols ────────────
        # Gap data is used in Gate 8 of high_accuracy_filter.
        # Load once at open — gaps don't change during the session.
        try:
            from gap_analyzer import get_gap_analyzer
            gap_analyzer = get_gap_analyzer()
            gap_analyzer.clear()   # Fresh data for new day
            gap_analyzer.load_gaps_for_watchlist(self.fetcher, watchlist)
            self.gap_analyzer = gap_analyzer

            # Send gapped stocks summary to Telegram
            gap_summary = gap_analyzer.get_gapped_stocks_summary()
            if "No significant gaps" not in gap_summary:
                logger.info(f"[{format_ist_timestamp()}] {gap_summary}")
                if self.alerter:
                    self.alerter.send_text(gap_summary)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Gap analysis failed: {e}")
            self.gap_analyzer = None

        # ── Pre-load corporate actions for today ──────────────────────
        try:
            from corporate_actions import get_corp_filter
            corp_filter = get_corp_filter()
            today_events = corp_filter.get_upcoming_events_today()
            if today_events:
                event_text = "⚠️ Corporate actions TODAY:\n" + "\n".join(
                    f"  {e['symbol']}: {e['purpose']}" for e in today_events[:10]
                )
                logger.info(f"[{format_ist_timestamp()}] {event_text}")
                if self.alerter:
                    self.alerter.send_text(event_text)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Corp actions load failed: {e}")

        # Morning brief (AI thesis + global cues + events + OC + FII/DII)
        try:
            # Gather option chain summary
            oc_summary = ""
            if self.oc_analyzer:
                try:
                    oc_summary = self.oc_analyzer.format_telegram("NIFTY")
                    # Apply OC FII size multiplier to risk manager
                    oc_result = self.oc_analyzer.analyze("NIFTY")
                    if oc_result and self.risk_manager:
                        self.risk_manager.set_institutional_multiplier(
                            oc_mult=1.1 if oc_result.direction_bias == "BULLISH" else (
                                0.9 if oc_result.direction_bias == "BEARISH" else 1.0
                            )
                        )
                except Exception as e:
                    logger.warning(f"OC morning: {e}")

            # Gather FII/DII summary + apply size multiplier
            fii_summary = ""
            if self.fii_tracker:
                try:
                    fii_summary = self.fii_tracker.format_telegram()
                    fii_mult = self.fii_tracker.get_position_size_multiplier()
                    if self.risk_manager:
                        self.risk_manager.set_institutional_multiplier(fii_mult=fii_mult)
                except Exception as e:
                    logger.warning(f"FII/DII morning: {e}")

            # Send combined morning brief
            self.alerter.send_morning_brief(
                watchlist, available, nifty_open,
                oc_summary=oc_summary,
                fii_summary=fii_summary,
            )

            # Also send overnight analysis text
            if self.overnight:
                brief = self.overnight.format_morning_brief()
                if self.calendar:
                    brief += "\n" + self.calendar.format_upcoming_events()
                self.alerter.send_text(brief)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Morning brief failed: {e}")

        # Log day-of-week mode
        now_ist  = get_current_ist_time()
        dow      = now_ist.weekday()
        dow_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][dow]
        dow_mult = config.DOW_SIZE_MULTIPLIERS.get(dow, 1.0)
        dow_min  = config.DOW_MIN_SCORE.get(dow, config.MIN_SIGNAL_SCORE)
        dow_max  = config.DOW_MAX_TRADES.get(dow, config.MAX_TRADES_PER_DAY)
        logger.info(
            f"[{format_ist_timestamp()}] Day initialized | "
            f"Balance: {available} | Nifty: {nifty_open} | "
            f"Watchlist: {len(watchlist)} stocks\n"
            f"  DOW mode: {dow_name} | Size: {dow_mult:.0%} | "
            f"Min score: {dow_min:.0f} | Max trades: {dow_max}"
        )
        self._day_initialized = True
        self.market_open_today = True

    # --------------------------------------------------------
    # MAIN TRADING LOOP
    # --------------------------------------------------------

    def run(self):
        """Main bot run loop. Blocks until market close."""
        self.running = True
        logger.info(f"[{format_ist_timestamp()}] Bot running. Waiting for market open...")

        try:
            while self.running:
                now_ist = get_current_ist_time()

                # ── TOTP token refresh at 5:50 AM IST (10 min BEFORE 6 AM expiry) ──
                # Groww invalidates all JWTs at exactly 6:00 AM IST daily.
                # We use the still-valid current JWT to get the next one at 5:50 AM.
                today_str = now_ist.strftime("%Y-%m-%d")
                if is_token_refresh_time() and self._token_refreshed_date != today_str:
                    self._do_token_refresh(today_str)
                # Retry at 5:53 and 5:57 if primary failed (still before 6 AM expiry)
                elif (self._token_refreshed_date != today_str
                        and now_ist.hour == 5
                        and now_ist.minute in (53, 57)
                        and self._token_refresh_attempts < 3):
                    logger.warning(
                        f"[{format_ist_timestamp()}] Token refresh retry "
                        f"#{self._token_refresh_attempts + 1}/3 (still before 6 AM)..."
                    )
                    self._do_token_refresh(today_str)

                # ── Reset for new calendar date ────────────────────────────────
                # Token refresh flag resets at midnight (new calendar day),
                # not at 9 AM — so 5:50 AM refresh is seen as "today's refresh"
                if today_str != getattr(self, "_last_trade_date", ""):
                    self._day_initialized = False
                    self.eod_done = False
                    self._token_refreshed_today = False   # legacy compat
                    self._token_refresh_attempts = 0
                    self._premarket_scan_done = False
                    self._watchlist_cache = []
                    self._watchlist_cache_time = None
                    self._last_heartbeat_min = -1
                    self._last_trade_date = today_str
                    logger.info(
                        f"[{format_ist_timestamp()}] 📅 New trading day: {today_str}"
                    )

                # Overnight analysis at 8:00 AM IST (before market)
                if (now_ist.hour == 8 and now_ist.minute < 5
                        and not self._overnight_run_today):
                    self._run_overnight_analysis()

                # Pre-market top-picks scan at 9:05 AM IST
                if (now_ist.hour == 9 and now_ist.minute >= 5
                        and now_ist.minute < 15 and not self._premarket_scan_done):
                    self._send_premarket_scan()
                    self._premarket_scan_done = True

                # Pre-market: build watchlist + holiday check
                if is_pre_market_ist() and not self._day_initialized:
                    # Check if today is a holiday
                    if self.calendar and self.calendar.is_holiday_today():
                        events = self.calendar.get_today_events()
                        holiday = next((e["event"] for e in events if e["impact"] == "HOLIDAY"), "Holiday")
                        logger.info(f"[{format_ist_timestamp()}] NSE Holiday today: {holiday}. Bot idle.")
                        time.sleep(3600)
                        continue
                    logger.info(f"[{format_ist_timestamp()}] Pre-market: preparing watchlist...")
                    self.watchlist_mgr.get_watchlist(
                        data_fetcher=self.fetcher, learner=self.learner
                    )

                # Market is open
                if is_market_open_ist():
                    # Initialize day on first open
                    if not self._day_initialized:
                        self.initialize_market_day()

                    # Check for force square-off time (3:20 PM IST)
                    if should_force_squareoff_ist():
                        if not self.eod_done:
                            self._do_eod_squareoff()
                    # Warning at 3:15 PM IST
                    elif is_squareoff_time_ist():
                        open_pos = self.risk_manager.state.positions
                        if open_pos:
                            logger.warning(
                                f"[{format_ist_timestamp()}] ⏰ 3:15 PM IST — "
                                f"{len(open_pos)} positions still open. Square-off in 5 min!"
                            )
                            self.alerter.send_text(
                                f"⚠️ 3:15 PM IST — Square-off in 5 min! "
                                f"{len(open_pos)} positions open."
                            )
                    else:
                        # Normal trading cycle
                        self._trading_cycle()

                elif self.market_open_today and not self.eod_done:
                    # Market just closed
                    self._do_eod_shutdown()

                else:
                    # Waiting for market
                    mins = minutes_until_market_open()
                    if mins > 0:
                        sleep_secs = min(30, max(5, mins * 30))
                        logger.debug(
                            f"[{format_ist_timestamp()}] Market closed. "
                            f"Opens in {mins:.0f} min. Sleeping {sleep_secs:.0f}s..."
                        )
                        time.sleep(sleep_secs)
                    else:
                        time.sleep(10)
                    continue

                time.sleep(self._scan_interval)

        except KeyboardInterrupt:
            logger.info(f"[{format_ist_timestamp()}] Bot stopped by user (Ctrl+C)")
        finally:
            self._cleanup()

    # --------------------------------------------------------
    # TRADING CYCLE
    # --------------------------------------------------------

    def _trading_cycle(self):
        """
        One full scan cycle:
        1. Update all open positions (trailing stops, SL hits)
        2. Check Nifty circuit breaker
        3. Calendar & VIX blackout check
        4. Scan watchlist for new signals
        5. Execute valid signals
        """
        try:
            # 1. Update open positions (ALWAYS — even if paused)
            self._update_positions()

            # 1b. Reconcile positions every 10 min (detect server-side SL hits)
            self._reconcile_positions()

            # 1c. Update weekly P&L mode (4-day profit optimizer)
            self._update_weekly_mode()
            if self._weekly_mode == "LOCKED":
                logger.debug(f"[{format_ist_timestamp()}] Weekly target hit — locked to A+ only")
                return

            # 2. Check Nifty circuit
            nifty_q = self.fetcher.get_nifty_quote()
            if nifty_q:
                self.risk_manager.check_nifty_circuit(nifty_q.get("ltp", 0))

            # 3. Economic calendar blackout check
            if self.calendar:
                blackout, reason = self.calendar.is_blackout_now()
                if blackout:
                    logger.info(f"[{format_ist_timestamp()}] Blackout: {reason}")
                    return

            # 3b. F&O expiry warning
            if self.calendar and self.calendar.is_fno_expiry_today():
                logger.debug(f"[{format_ist_timestamp()}] F&O expiry day — extra caution")

            # 3c. Apply overnight VIX size multiplier
            overnight_mult = 1.0
            if self.overnight:
                overnight_mult = self.overnight.get_size_multiplier()

            # 4. Check if we can take new trades
            if self.risk_manager.state.circuit_breaker_active:
                logger.debug(f"[{format_ist_timestamp()}] Circuit breaker — skipping new signals")
                return

            if self.risk_manager.state.trading_paused:
                logger.debug(f"[{format_ist_timestamp()}] Trading paused — skipping signals")
                return

            # Hourly heartbeat (on the hour, e.g. 9:00, 10:00, 11:00...)
            now_ist = get_current_ist_time()
            if now_ist.minute < 2 and now_ist.hour != self._last_heartbeat_min:
                self._send_heartbeat()
                self._last_heartbeat_min = now_ist.hour

            # 4. Scan watchlist (15-min cached)
            watchlist = self._get_watchlist_cached()
            max_new = config.MAX_POSITIONS - len(self.risk_manager.state.positions)
            if max_new <= 0:
                logger.debug(f"[{format_ist_timestamp()}] Max positions reached — no new entries")
                return

            signals = self.signal_gen.scan_watchlist(
                symbols=watchlist,
                max_signals=min(max_new, 3)  # Max 3 new signals per cycle
            )

            # 4b. Apply day-of-week minimum score filter
            dow = now_ist.weekday()
            dow_min = config.DOW_MIN_SCORE.get(dow, config.MIN_SIGNAL_SCORE)
            dow_max_trades = config.DOW_MAX_TRADES.get(dow, config.MAX_TRADES_PER_DAY)
            if self.risk_manager.state.daily_trades >= dow_max_trades:
                logger.debug(
                    f"[{format_ist_timestamp()}] DOW max trades "
                    f"({dow_max_trades}) reached for {['Mon','Tue','Wed','Thu','Fri'][dow]}"
                )
                return
            signals = [s for s in signals if s.signal_score >= dow_min]
            if self._weekly_mode == "PROTECT":
                # Weekly profit at 1.5% — only A/A+ trades, filter C/B
                signals = [s for s in signals if s.quality_grade in ("A+", "A")]

            # 5. Execute signals
            for signal in signals:
                # Apply FII/DII institutional size multiplier to signal
                if self.fii_tracker:
                    try:
                        fii_mult = self.fii_tracker.get_position_size_multiplier()
                        signal.size_multiplier = round(
                            signal.size_multiplier * fii_mult * overnight_mult, 2
                        )
                        signal.size_multiplier = max(0.25, min(signal.size_multiplier, 2.0))
                    except Exception:
                        pass

                logger.info(f"[{format_ist_timestamp()}] {signal.summary()}")
                result = self.executor.place_entry_order(signal)
                if result.success:
                    # Send Telegram alert with chart
                    try:
                        df_5m = self.fetcher.get_today_candles(signal.symbol)
                        self.alerter.send_entry_alert(signal, df_5m)
                    except Exception as e:
                        logger.warning(f"Alert failed: {e}")

            # Print dashboard periodically
            if get_current_ist_time().minute % 15 == 0:
                self.dashboard.print_live_dashboard(self.risk_manager)
                self.dashboard.print_positions(self.risk_manager)

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Trading cycle error: {e}")

    # --------------------------------------------------------
    # POSITION MANAGEMENT
    # --------------------------------------------------------

    def _update_positions(self):
        """Update trailing stops and check SL/TP for all open positions."""
        positions = list(self.risk_manager.state.positions.values())
        for pos in positions:
            try:
                quote = self.fetcher.get_quote(pos.symbol)
                if not quote:
                    continue
                ltp = quote.get("ltp", 0)

                # ── Smart Trade Health Monitor (runs BEFORE trailing stop) ──
                # Fetch last 3 candles for candle-reversal rule (cheap — uses cache)
                recent_candles = None
                try:
                    df_today = self.fetcher.get_today_candles(pos.symbol, interval="5m")
                    if df_today is not None and len(df_today) >= 2:
                        recent_candles = df_today.tail(3).to_dict("records")
                except Exception:
                    pass

                health = self.risk_manager.check_position_health(pos, ltp, recent_candles)

                if health["action"] == "EXIT_NOW":
                    logger.info(
                        f"[{format_ist_timestamp()}] HEALTH EXIT: {pos.symbol} | "
                        f"{health['reason']}"
                    )
                    result = self.executor.place_exit_order(
                        symbol=pos.symbol,
                        quantity=pos.quantity,
                        direction=pos.direction,
                        reason=health["reason"],
                        use_market_order=True,
                    )
                    if result.success:
                        pnl = pos.pnl
                        self.alerter.send_exit_alert(
                            pos.symbol, pos.direction, pos.entry_price,
                            ltp, pos.quantity, pnl, health["reason"]
                        )
                    continue   # skip trailing-stop logic for this position

                elif health["action"] == "BREAK_EVEN":
                    new_sl = health["new_sl"]
                    logger.info(
                        f"[{format_ist_timestamp()}] BREAK-EVEN: {pos.symbol} | "
                        f"{health['reason']}"
                    )
                    pos.stop_loss = new_sl
                    self.executor.modify_stop_loss(pos.symbol, new_sl)
                    # Fall through — let trailing stop logic run normally

                action = self.risk_manager.update_trailing_stop(pos, ltp)

                if action["action"] == "EXIT":
                    logger.info(
                        f"[{format_ist_timestamp()}] EXIT signal: {pos.symbol} | "
                        f"{action['reason']}"
                    )
                    result = self.executor.place_exit_order(
                        symbol=pos.symbol,
                        quantity=pos.quantity,
                        direction=pos.direction,
                        reason=action["reason"],
                        use_market_order=True
                    )
                    if result.success:
                        pnl = pos.pnl
                        self.alerter.send_exit_alert(
                            pos.symbol, pos.direction, pos.entry_price,
                            ltp, pos.quantity, pnl, action["reason"]
                        )

                elif action["action"] in ("PARTIAL_EXIT_T1", "PARTIAL_EXIT_T2"):
                    exit_qty = action.get("exit_qty", max(1, pos.quantity // 2))
                    result = self.executor.place_exit_order(
                        pos.symbol, exit_qty, pos.direction,
                        reason=action["reason"]
                    )
                    if result.success:
                        # Update local position quantity
                        pos.quantity = max(0, pos.quantity - exit_qty)
                        self.executor.modify_stop_loss(pos.symbol, action["new_sl"])
                        pnl_partial = (ltp - pos.entry_price) * exit_qty if pos.direction == "LONG" else (pos.entry_price - ltp) * exit_qty
                        logger.info(
                            f"[{format_ist_timestamp()}] {action['action']}: "
                            f"{pos.symbol} {exit_qty}qty @ ₹{ltp:.2f} | "
                            f"Partial P&L: ₹{pnl_partial:.0f}"
                        )
                        try:
                            self.alerter.send_exit_alert(
                                pos.symbol, pos.direction, pos.entry_price,
                                ltp, exit_qty, pnl_partial, action["reason"]
                            )
                        except Exception:
                            pass

                elif action["action"] == "UPDATE_SL":
                    self.executor.modify_stop_loss(pos.symbol, action["new_sl"])

            except Exception as e:
                logger.error(f"Position update error {pos.symbol}: {e}")

    # --------------------------------------------------------
    # 4-DAY WEEKLY PROFIT OPTIMIZER
    # --------------------------------------------------------

    def _load_weekly_pnl(self) -> dict:
        """Load this week's accumulated P&L from disk."""
        try:
            if self._weekly_pnl_file.exists():
                return json.loads(self._weekly_pnl_file.read_text())
        except Exception:
            pass
        return {"week": "", "net_pnl": 0.0, "days_traded": 0, "profitable_days": 0}

    def _save_weekly_pnl(self, data: dict) -> None:
        try:
            self._weekly_pnl_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug(f"Weekly PnL save: {e}")

    def _update_weekly_pnl(self) -> None:
        """Called at EOD — add today's P&L to the weekly tracker."""
        if not self.risk_manager:
            return
        now  = get_current_ist_time()
        week = now.strftime("%Y-W%W")   # e.g. "2026-W15"
        data = self._load_weekly_pnl()
        if data.get("week") != week:
            data = {"week": week, "net_pnl": 0.0, "days_traded": 0, "profitable_days": 0}

        today_pnl = self.risk_manager.state.daily_pnl
        data["net_pnl"]        = round(data["net_pnl"] + today_pnl, 2)
        data["days_traded"]    += 1
        data["profitable_days"] += 1 if today_pnl > 0 else 0
        data["capital"]         = config.MAX_DAILY_CAPITAL
        data["weekly_pct"]      = round(data["net_pnl"] / config.MAX_DAILY_CAPITAL * 100, 2)
        self._save_weekly_pnl(data)

        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri"][now.weekday()]
        logger.info(
            f"[{format_ist_timestamp()}] Weekly tracker [{week}] "
            f"Day {data['days_traded']}/5 ({day_name}): "
            f"Today ₹{today_pnl:+,.0f} | "
            f"Week ₹{data['net_pnl']:+,.0f} ({data['weekly_pct']:+.2f}%) | "
            f"Profitable days: {data['profitable_days']}"
        )
        # Send weekly summary on Friday EOD
        if now.weekday() == 4 and self.alerter:
            try:
                self.alerter.send_text(
                    f"📊 <b>Weekly Summary</b>\n"
                    f"Week: {week}\n"
                    f"Net P&L: ₹{data['net_pnl']:+,.0f} ({data['weekly_pct']:+.2f}%)\n"
                    f"Profitable days: {data['profitable_days']}/5\n"
                    f"Target: 4/5 days 🎯"
                )
            except Exception:
                pass

    def _update_weekly_mode(self) -> None:
        """
        Check weekly P&L and set trading mode:
          NORMAL  — below profit lock target, trade normally
          PROTECT — hit 1.5% weekly → A/A+ only, 50% size via DOW mult
          LOCKED  — hit 2.0% weekly target → no new entries, protect gains
        """
        try:
            data = self._load_weekly_pnl()
            now  = get_current_ist_time()
            week = now.strftime("%Y-W%W")
            if data.get("week") != week:
                self._weekly_mode = "NORMAL"
                return
            pct = data.get("weekly_pct", 0.0)
            # Add today's unrealised P&L
            if self.risk_manager:
                today_pnl  = self.risk_manager.state.daily_pnl
                total_pct  = pct + (today_pnl / max(config.MAX_DAILY_CAPITAL, 1) * 100)
            else:
                total_pct = pct

            if total_pct >= config.WEEKLY_PROFIT_TARGET_PCT:
                if self._weekly_mode != "LOCKED":
                    self._weekly_mode = "LOCKED"
                    logger.info(
                        f"[{format_ist_timestamp()}] 🔒 Weekly profit target "
                        f"{config.WEEKLY_PROFIT_TARGET_PCT}% reached "
                        f"({total_pct:.2f}%) — LOCKED (no new entries)"
                    )
                    if self.alerter:
                        self.alerter.send_text(
                            f"🎯 <b>Weekly Profit Target Hit!</b>\n"
                            f"Week P&L: {total_pct:.2f}% ≥ {config.WEEKLY_PROFIT_TARGET_PCT}%\n"
                            f"Mode: LOCKED — protecting gains, no new entries.\n"
                            f"Existing positions monitored until 3:20 PM."
                        )
            elif total_pct >= config.WEEKLY_PROFIT_LOCK_PCT:
                if self._weekly_mode not in ("PROTECT", "LOCKED"):
                    self._weekly_mode = "PROTECT"
                    logger.info(
                        f"[{format_ist_timestamp()}] 🛡 Weekly profit at "
                        f"{total_pct:.2f}% — PROTECT mode (A/A+ only)"
                    )
                    if self.alerter:
                        self.alerter.send_text(
                            f"🛡 <b>Weekly Protect Mode</b>\n"
                            f"Week P&L: {total_pct:.2f}% — protecting gains.\n"
                            f"Only A/A+ grade trades allowed."
                        )
            elif total_pct <= -config.WEEKLY_LOSS_STOP_PCT:
                if self._weekly_mode != "LOCKED":
                    self._weekly_mode = "LOCKED"
                    logger.warning(
                        f"[{format_ist_timestamp()}] 🛑 Weekly loss limit "
                        f"−{config.WEEKLY_LOSS_STOP_PCT}% hit "
                        f"({total_pct:.2f}%) — LOCKED (no new entries)"
                    )
                    if self.alerter:
                        self.alerter.send_text(
                            f"🛑 <b>Weekly Loss Limit Hit!</b>\n"
                            f"Week P&L: {total_pct:.2f}% ≤ −{config.WEEKLY_LOSS_STOP_PCT}%\n"
                            f"Stopped new entries for the week. Rest and review."
                        )
            else:
                self._weekly_mode = "NORMAL"
        except Exception as e:
            logger.debug(f"Weekly mode check: {e}")

    # --------------------------------------------------------
    # POSITION RECONCILIATION (every 10 min during market hours)
    # --------------------------------------------------------

    def _reconcile_positions(self):
        """
        Sync the bot's local position tracker against Groww's actual positions.
        Runs every 10 minutes during market hours.

        Handles two drift cases:
        1. Bot tracks position but Groww doesn't have it → exchange/server SL hit
           Bot removes the ghost entry and alerts Telegram.
        2. Groww has position the bot doesn't know about → add and monitor it.
        """
        if not self.fetcher or not self.risk_manager:
            return

        now = get_current_ist_time()
        if (self._last_reconcile_time and
                (now - self._last_reconcile_time).total_seconds() < 600):
            return  # Not yet 10 min since last reconcile
        self._last_reconcile_time = now

        try:
            groww_raw = self.fetcher.get_positions()
            if groww_raw is None:
                return

            # Build set of symbols Groww actually holds (non-zero quantity)
            groww_syms: set = {
                str(p.get("symbol", ""))
                for p in groww_raw
                if int(p.get("quantity", 0)) != 0
            }
            bot_syms: set = set(self.risk_manager.state.positions.keys())

            # ── Case 1: ghost positions (bot tracks, Groww doesn't) ──────────
            for sym in list(bot_syms - groww_syms):
                pos = self.risk_manager.state.positions.pop(sym, None)
                if not pos:
                    continue
                # Update daily P&L so the loss/gain is accounted for
                try:
                    q = self.fetcher.get_quote(sym)
                    ltp = float(q.get("ltp", pos.entry_price)) if q else pos.entry_price
                    pnl = (ltp - pos.entry_price) * pos.quantity if pos.direction == "LONG" \
                          else (pos.entry_price - ltp) * pos.quantity
                    self.risk_manager.state.daily_pnl += pnl
                    self.risk_manager.state.available_capital += ltp * pos.quantity
                except Exception:
                    pass

                logger.warning(
                    f"[{format_ist_timestamp()}] Reconcile REMOVED: {sym} "
                    f"({pos.direction} {pos.quantity}@₹{pos.entry_price:.2f}) — "
                    f"Groww shows no position (SL hit or exchange square-off)"
                )
                try:
                    self.alerter.send_text(
                        f"🔄 <b>Position Auto-Reconciled</b>\n"
                        f"Symbol: <b>{sym}</b>\n"
                        f"Direction: {pos.direction} | Qty: {pos.quantity}\n"
                        f"Entry: ₹{pos.entry_price:.2f}\n"
                        f"Removed: Groww closed position (SL hit or square-off)\n"
                        f"Time: {format_ist_timestamp()}"
                    )
                except Exception:
                    pass

            # ── Case 2: unknown positions (Groww has, bot doesn't) ───────────
            for sym in list(groww_syms - bot_syms):
                raw = next((p for p in groww_raw if str(p.get("symbol", "")) == sym), None)
                if not raw:
                    continue
                qty = int(raw.get("quantity", 0))
                avg = float(raw.get("avg_price", 0))
                if qty == 0 or avg == 0:
                    continue
                from risk_manager import Position
                pos = Position(
                    symbol=sym,
                    direction="LONG" if qty > 0 else "SHORT",
                    quantity=abs(qty),
                    entry_price=avg,
                    stop_loss=avg * 0.98,   # 2% fallback SL until ATR calc
                    target1=avg * 1.02,
                    target2=avg * 1.04,
                    entry_time=get_current_ist_time(),
                )
                self.risk_manager.state.positions[sym] = pos
                logger.warning(
                    f"[{format_ist_timestamp()}] Reconcile ADDED: {sym} "
                    f"({'LONG' if qty > 0 else 'SHORT'} {abs(qty)}@₹{avg:.2f}) — "
                    f"found in Groww but not in bot tracker"
                )
                try:
                    self.alerter.send_text(
                        f"🔄 <b>Unknown Position Detected</b>\n"
                        f"Symbol: <b>{sym}</b>\n"
                        f"Direction: {'LONG' if qty > 0 else 'SHORT'} | Qty: {abs(qty)}\n"
                        f"Avg Price: ₹{avg:.2f}\n"
                        f"Added to tracker — monitoring with 2% fallback SL."
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Position reconciliation error: {e}")

    # --------------------------------------------------------
    # EOD
    # --------------------------------------------------------

    def _do_eod_squareoff(self):
        """Force square-off all positions at 3:20 PM IST."""
        if not self.eod_done:
            logger.warning(f"[{format_ist_timestamp()}] 🔴 EOD SQUARE-OFF (3:20 PM IST)")
            self.executor.square_off_all("EOD automatic square-off 3:20 PM IST")
            self.eod_done = True

    def _do_eod_shutdown(self):
        """End-of-day tasks: report, logging, shutdown."""
        logger.info(f"[{format_ist_timestamp()}] Market closed. Running EOD tasks...")
        if not self.eod_done:
            self._do_eod_squareoff()

        # Run self-learning cycle (adapts parameters for tomorrow)
        if self.learner:
            try:
                logger.info(f"[{format_ist_timestamp()}] Running self-learning cycle...")
                self.learner.run_learning_cycle(lookback_days=20)
                logger.info(f"[{format_ist_timestamp()}] {self.learner.get_learning_summary()}")
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] Self-learning error: {e}")

        # EOD performance report (sends to Telegram with chart)
        if self.dashboard:
            try:
                report = self.dashboard.generate_eod_report(
                    capital=config.MAX_DAILY_CAPITAL,
                    learner=self.learner
                )
                self.dashboard.print_pattern_table()
                logger.info(f"[{format_ist_timestamp()}] EOD: P&L={report.get('net_pnl',0):+.0f} "
                            f"WR={report.get('win_rate',0):.1f}%")
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] EOD report error: {e}")

        # Update weekly P&L tracker (4-day profit mode)
        self._update_weekly_pnl()

        # Save compounded capital for tomorrow
        self._save_compounded_capital()

        self.market_open_today = False
        self.eod_done = True
        logger.info(f"[{format_ist_timestamp()}] Bot EOD complete. Shutting down.")
        self.running = False

    # --------------------------------------------------------
    # OVERNIGHT / PRE-MARKET INTELLIGENCE
    # --------------------------------------------------------

    def _run_overnight_analysis(self):
        """Run global market analysis at 8:00 AM IST. Sets day's trading bias."""
        logger.info(f"[{format_ist_timestamp()}] Running overnight intelligence...")
        try:
            if self.overnight:
                result = self.overnight.run(ai_brain=self.ai_brain)
                bias   = result.get("day_bias", "NEUTRAL")
                score  = result.get("bias_score", 0)
                risks  = result.get("key_risks", [])

                # Log VIX warning
                vix = result.get("vix", {}).get("vix", 15)
                if vix > 20:
                    logger.warning(
                        f"[{format_ist_timestamp()}] HIGH VIX={vix:.1f} today — "
                        "using 50% position sizes"
                    )

                logger.info(
                    f"[{format_ist_timestamp()}] Day bias: {bias} "
                    f"(score={score:+d}, VIX={vix:.1f})"
                )
                if risks:
                    for r in risks:
                        logger.warning(f"[{format_ist_timestamp()}] Risk: {r}")

            self._overnight_run_today = True
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Overnight analysis failed: {e}")
            self._overnight_run_today = True  # Don't retry

    # --------------------------------------------------------
    # TOKEN REFRESH
    # --------------------------------------------------------

    def _do_token_refresh(self, today_str: str = ""):
        """
        Refresh Groww token at 5:50 AM IST — BEFORE the 6:00 AM expiry.
        Propagates fresh token to fetcher and executor immediately.
        """
        from auth_groww import get_auth_manager
        mgr = get_auth_manager()
        success = mgr.refresh_token_if_needed()
        self._token_refresh_attempts += 1
        if success:
            self._token_refreshed_today = True       # legacy compat
            self._token_refreshed_date = today_str   # date-based guard
            # Push fresh token into fetcher and executor
            if self.fetcher:
                try:
                    self.fetcher._refresh_api_if_needed()
                except Exception:
                    try:
                        self.fetcher._init_api()
                    except Exception:
                        pass
            if self.executor:
                try:
                    self.executor._init_api()
                except Exception:
                    pass
        logger.info(
            f"[{format_ist_timestamp()}] Token refresh at 5:50 AM IST: "
            f"{'✅ succeeded' if success else '❌ FAILED'} "
            f"(attempt {self._token_refresh_attempts}/3)"
        )

    # --------------------------------------------------------
    # TELEGRAM COMMAND LISTENER
    # --------------------------------------------------------

    def _start_telegram_listener(self):
        """Start Telegram bot command listener in background thread."""
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            logger.warning(f"[{format_ist_timestamp()}] Telegram not configured — command listener disabled")
            return

        def listener_thread():
            try:
                asyncio.run(self._telegram_listener())
            except Exception as e:
                logger.error(f"Telegram listener error: {e}")

        t = threading.Thread(target=listener_thread, daemon=True)
        t.start()
        logger.info(f"[{format_ist_timestamp()}] Telegram command listener started")

    async def _telegram_listener(self):
        """
        Async Telegram command handler with Conflict retry.
        Render deploys overlap briefly — old + new instance both poll simultaneously,
        causing 409 Conflict. Retry with backoff until old instance dies (~30s).
        """
        import telegram.error as tg_error
        from telegram.ext import Application, CommandHandler

        # ── Command handlers (defined once, reused across retries) ──────
        async def cmd_kill(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            logger.critical(f"[{format_ist_timestamp()}] /kill received!")
            self.risk_manager.emergency_stop()
            self.alerter.send_kill_alert()
            self.executor.square_off_all("KILL SWITCH by Telegram /kill")

        async def cmd_status(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            self.alerter.send_status(self.risk_manager)

        async def cmd_pause(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            self.risk_manager._pause_trading("Manual pause via /pause")
            self.alerter.send_text(f"⏸ Trading paused at {format_ist_timestamp()}")

        async def cmd_resume(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            self.risk_manager.manual_resume()
            self.alerter.send_text(f"▶️ Trading resumed at {format_ist_timestamp()}")

        async def cmd_watchlist(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            status = self.watchlist_mgr.format_watchlist_message()
            self.alerter.send_text(status)

        async def cmd_report(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            self.alerter.send_eod_report(self.risk_manager)

        async def cmd_balance(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            try:
                now_str = format_ist_timestamp()

                # ── Live Groww balance ────────────────────────────────────
                bal = self.fetcher.get_account_balance() if self.fetcher else {}
                available   = bal.get("available",   0)
                used_margin = bal.get("used_margin",  0)
                collateral  = bal.get("collateral",   0)
                total       = bal.get("total",        available + used_margin)
                opening     = bal.get("opening",      total)

                # ── Bot state (use correct RiskState attribute names) ─────
                compounded  = self._load_compounded_capital()
                rm          = self.risk_manager
                daily_pnl   = rm.state.daily_pnl      if rm else 0
                open_count  = len(rm.state.positions)  if rm else 0
                trades_done = rm.state.daily_trades    if rm else 0
                loss_pct    = rm.state.daily_loss_pct  if rm else 0
                daily_loss_limit = config.MAX_DAILY_CAPITAL * config.DAILY_LOSS_LIMIT_PCT / 100

                # ── Open positions live value ─────────────────────────────
                positions_pnl   = 0.0
                positions_lines = []
                if rm and rm.state.positions:
                    for sym, pos in list(rm.state.positions.items()):
                        try:
                            q = self.fetcher.get_quote(sym)
                            ltp = q.get("ltp", 0) if q else 0
                            if ltp and pos.entry_price:
                                mult = 1 if pos.direction in ("LONG", "BUY") else -1
                                pos_pnl = mult * (ltp - pos.entry_price) * pos.quantity
                                positions_pnl += pos_pnl
                                icon = "🟢" if pos_pnl >= 0 else "🔴"
                                positions_lines.append(
                                    f"  {icon} {sym}: ₹{ltp:.1f} "
                                    f"({pos_pnl:+,.0f})"
                                )
                        except Exception:
                            pass

                # ── Margin utilisation % ─────────────────────────────────
                util_pct = (used_margin / total * 100) if total > 0 else 0

                # ── Build HTML message ────────────────────────────────────
                lines = [
                    "💰 <b>Groww Account Balance</b>",
                    f"🕐 {now_str}",
                    "",
                    "<b>📊 Live Funds</b>",
                    f"  Available Cash : ₹{available:,.2f}",
                    f"  Margin Used    : ₹{used_margin:,.2f}  ({util_pct:.1f}%)",
                ]
                if collateral > 0:
                    lines.append(f"  Collateral     : ₹{collateral:,.2f}")
                lines += [
                    f"  Opening Balance: ₹{opening:,.2f}",
                    f"  Total Net Value: ₹{total:,.2f}",
                    "",
                    "<b>📈 Today's Trading</b>",
                    f"  Realised P&L   : ₹{daily_pnl:+,.2f}",
                ]
                if open_count > 0:
                    lines += [
                        f"  Unrealised P&L : ₹{positions_pnl:+,.2f}",
                        f"  Total P&L      : ₹{daily_pnl + positions_pnl:+,.2f}",
                    ]
                lines += [
                    f"  Trades Today   : {trades_done}",
                    f"  Open Positions : {open_count}",
                    f"  Daily Loss     : {loss_pct:.2f}% of ₹{daily_loss_limit:,.0f} limit",
                ]
                if positions_lines:
                    lines += ["", "<b>📌 Open Positions</b>"]
                    lines.extend(positions_lines)

                lines += [
                    "",
                    "<b>⚙️ Bot Config</b>",
                    f"  Bot Capital    : ₹{compounded:,.0f}",
                    f"  Base Capital   : ₹{config.MAX_DAILY_CAPITAL:,.0f}",
                    f"  Daily Loss Lim : ₹{daily_loss_limit:,.0f}",
                    f"  Live Trading   : {'✅ ON' if config.LIVE_TRADING_ENABLED else '🔒 OFF'}",
                ]

                # Show cache notice if data is from cache (market closed / API offline)
                if bal.get("_from_cache"):
                    age = bal.get("_cache_age_min", 0)
                    lines += [
                        "",
                        f"⚠️ <i>Live balance unavailable — showing cached data from {age:.0f} min ago.</i>",
                        "<i>Groww balance API is only active during market hours (9:15 AM–3:30 PM IST).</i>",
                    ]
                elif available == 0 and not bal.get("_from_cache"):
                    lines += [
                        "",
                        "⚠️ <i>Groww returned ₹0 — balance API may be offline outside market hours.</i>",
                        "<i>Balance will update automatically during trading hours.</i>",
                    ]

                self.alerter.send_html("\n".join(lines))

            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] /balance error: {e}")
                self.alerter.send_text(f"Balance fetch error: {e}")

        async def cmd_capital(update, context):
            if str(update.effective_chat.id) != str(config.TELEGRAM_CHAT_ID):
                return
            try:
                data = json.loads(self._capital_file.read_text()) if self._capital_file.exists() else {}
                base = config.MAX_DAILY_CAPITAL
                compounded = data.get("compounded_capital", base)
                growth = data.get("total_growth_pct", 0)
                date = data.get("date", "never")
                self.alerter.send_text(
                    f"📈 <b>Capital Growth</b>\n"
                    f"Base: ₹{base:,.0f}\n"
                    f"Current: ₹{compounded:,.0f}\n"
                    f"Total Growth: {growth:+.2f}%\n"
                    f"Last Updated: {date}"
                )
            except Exception as e:
                self.alerter.send_text(f"Capital data error: {e}")

        # ── Retry loop — handles Render deployment overlap ───────────────
        max_retries = 15
        retry_delay = 20  # seconds; old instance usually dies within 30s

        # Kill any stale polling session from previous deployment before starting
        try:
            import requests as _req
            _req.get(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
                "/deleteWebhook?drop_pending_updates=true",
                timeout=10,
            )
            logger.info(f"[{format_ist_timestamp()}] Telegram: cleared stale webhook/session")
        except Exception as _e:
            logger.debug(f"deleteWebhook cleanup: {_e}")

        for attempt in range(max_retries):
            app = None
            try:
                app = (
                    Application.builder()
                    .token(config.TELEGRAM_BOT_TOKEN)
                    .connect_timeout(30)
                    .read_timeout(30)
                    .build()
                )

                app.add_handler(CommandHandler("kill",       cmd_kill))
                app.add_handler(CommandHandler("status",     cmd_status))
                app.add_handler(CommandHandler("pause",      cmd_pause))
                app.add_handler(CommandHandler("resume",     cmd_resume))
                app.add_handler(CommandHandler("watchlist",  cmd_watchlist))
                app.add_handler(CommandHandler("report",     cmd_report))
                app.add_handler(CommandHandler("balance",    cmd_balance))
                app.add_handler(CommandHandler("capital",    cmd_capital))

                # Absorb 409 Conflict inside the PTB network loop — prevents crash on deploy
                async def _tg_error_handler(update, context):
                    if isinstance(context.error, tg_error.Conflict):
                        logger.debug("Telegram Conflict absorbed by error handler — still running")
                    else:
                        logger.warning(f"[{format_ist_timestamp()}] TG error: {context.error}")
                app.add_error_handler(_tg_error_handler)

                await app.initialize()
                await app.start()
                await app.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=["message"],
                )
                logger.info(f"[{format_ist_timestamp()}] Telegram polling active")

                while self.running:
                    await asyncio.sleep(1)

                await app.updater.stop()
                await app.stop()
                await app.shutdown()
                return  # Clean exit

            except tg_error.Conflict:
                logger.warning(
                    f"[{format_ist_timestamp()}] Telegram Conflict — previous instance still running. "
                    f"Retry {attempt + 1}/{max_retries} in {retry_delay}s..."
                )
                if app:
                    try:
                        await app.shutdown()
                    except Exception:
                        pass
                await asyncio.sleep(retry_delay)
                retry_delay = min(int(retry_delay * 1.5), 120)

            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] Telegram listener error: {e}")
                if app:
                    try:
                        await app.shutdown()
                    except Exception:
                        pass
                return

        logger.error(
            f"[{format_ist_timestamp()}] Telegram listener gave up after {max_retries} retries. "
            "Alerts still work — only /commands are unavailable."
        )

    # --------------------------------------------------------
    # EOD-TRAINED ADAPTIVE PARAMETERS
    # --------------------------------------------------------

    def _apply_eod_trained_params(self) -> None:
        """
        Load yesterday's walk-forward optimized params and apply to live trading.
        Called once at startup and again each morning at 9:00 AM IST.

        Updates:
          config.ATR_SL_MULTIPLIER   → better stop-loss distance
          config.ATR_TP_MULTIPLIER   → better target distance
          config.MAX_RISK_PER_TRADE_PCT
          signal_gen.min_signal_score → adaptive entry quality bar
        """
        try:
            from trainer import EODSelfTrainer
            params = EODSelfTrainer.load_params()
            if not params or not params.get("improved", False):
                return  # No trained params or no improvement — keep defaults

            date_str = params.get("date", "?")

            # Apply ATR SL/TP multipliers to config (live override)
            if "atr_sl" in params:
                config.ATR_SL_MULTIPLIER = float(params["atr_sl"])
            if "atr_t1" in params:
                config.ATR_TP_MULTIPLIER = float(params["atr_t1"])
            if "risk_pct" in params:
                # Hard cap at 1% for safety regardless of trainer output
                config.MAX_RISK_PER_TRADE_PCT = min(float(params["risk_pct"]), 1.0)
                if self.risk_manager:
                    self.risk_manager.max_risk_pct = config.MAX_RISK_PER_TRADE_PCT

            # Apply trained min_score to signal generator
            if "min_score" in params and self.signal_gen:
                self.signal_gen.min_score = float(params["min_score"])

            logger.info(
                f"[{format_ist_timestamp()}] ✅ EOD params applied (trained {date_str}): "
                f"ATR_SL={config.ATR_SL_MULTIPLIER}x  "
                f"ATR_T1={config.ATR_TP_MULTIPLIER}x  "
                f"Risk={config.MAX_RISK_PER_TRADE_PCT}%  "
                f"MinScore={params.get('min_score', '?')}  "
                f"Sharpe={params.get('sharpe', 0):.2f}"
            )
            # Notify via Telegram so user sees params being applied
            try:
                self.alerter.send_text(
                    f"🧠 <b>Trained Params Active</b> — {date_str}\n"
                    f"ATR SL: {config.ATR_SL_MULTIPLIER}×  "
                    f"ATR T1: {config.ATR_TP_MULTIPLIER}×\n"
                    f"Risk/trade: {config.MAX_RISK_PER_TRADE_PCT}%  "
                    f"Min score: {params.get('min_score', '?')}\n"
                    f"OOS Sharpe: {params.get('sharpe', 0):.2f}  "
                    f"WR: {params.get('win_rate', 0):.1f}%"
                )
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] EOD param load failed: {e}")

    # --------------------------------------------------------
    # AUTO-COMPOUND CAPITAL
    # --------------------------------------------------------

    def _load_compounded_capital(self) -> float:
        """
        Returns today's capital = base + cumulative net P&L.
        Reads from data/capital.json written each EOD.
        Caps at 5× base to prevent runaway sizing after a lucky streak.
        """
        base = config.MAX_DAILY_CAPITAL
        try:
            if self._capital_file.exists():
                data = json.loads(self._capital_file.read_text())
                compounded = float(data.get("compounded_capital", base))
                cap = base * 5
                compounded = max(base, min(compounded, cap))
                if compounded != base:
                    logger.info(
                        f"[{format_ist_timestamp()}] Auto-compound: "
                        f"base ₹{base:,.0f} → today ₹{compounded:,.0f}"
                    )
                return compounded
        except Exception as e:
            logger.warning(f"Capital load error: {e}")
        return base

    def _save_compounded_capital(self):
        """Save today's P&L to capital.json for tomorrow's compound."""
        try:
            if not self.risk_manager:
                return
            today_pnl = self.risk_manager.state.daily_pnl
            base = config.MAX_DAILY_CAPITAL
            existing = json.loads(self._capital_file.read_text()) if self._capital_file.exists() else {}
            prev = float(existing.get("compounded_capital", base))
            new_capital = max(base * 0.8, prev + today_pnl)  # max 20% drawdown on capital
            self._capital_file.write_text(json.dumps({
                "date": get_current_ist_time().strftime("%Y-%m-%d"),
                "base_capital": base,
                "today_pnl": round(today_pnl, 2),
                "compounded_capital": round(new_capital, 2),
                "total_growth_pct": round((new_capital - base) / base * 100, 2),
            }, indent=2))
            logger.info(
                f"[{format_ist_timestamp()}] Capital saved: "
                f"₹{prev:,.0f} + P&L ₹{today_pnl:+,.0f} = ₹{new_capital:,.0f}"
            )
        except Exception as e:
            logger.warning(f"Capital save error: {e}")

    # --------------------------------------------------------
    # POSITION SYNC ON RESTART
    # --------------------------------------------------------

    def _sync_positions_from_groww(self):
        """
        On startup/restart, read open positions from Groww and register
        them in the risk manager so trailing stops & exits work correctly.
        Called during initialize_market_day() if market is open.
        """
        if not self.fetcher or not self.risk_manager:
            return
        try:
            positions = self.fetcher.get_positions()
            if not positions:
                return
            synced = 0
            for p in positions:
                sym = p.get("symbol", "")
                qty = int(p.get("quantity", 0))
                avg = float(p.get("avg_price", 0))
                if not sym or qty == 0:
                    continue
                # Register in risk manager state so bot tracks them
                if sym not in self.risk_manager.state.positions:
                    from risk_manager import Position
                    pos = Position(
                        symbol=sym,
                        direction="LONG" if qty > 0 else "SHORT",
                        quantity=abs(qty),
                        entry_price=avg,
                        stop_loss=avg * 0.98,   # 2% fallback SL until ATR recalculated
                        target1=avg * 1.02,
                        target2=avg * 1.04,
                        entry_time=get_current_ist_time(),
                    )
                    self.risk_manager.state.positions[sym] = pos
                    synced += 1
            if synced:
                logger.info(
                    f"[{format_ist_timestamp()}] Synced {synced} open position(s) from Groww"
                )
                self.alerter.send_text(
                    f"🔄 Bot restarted — synced {synced} open position(s) from Groww.\n"
                    "Trailing stops re-applied. Monitoring active."
                )
        except Exception as e:
            logger.warning(f"Position sync error: {e}")

    # --------------------------------------------------------
    # PRE-MARKET TOP PICKS SCAN
    # --------------------------------------------------------

    def _send_premarket_scan(self):
        """
        At 9:05 AM IST: scan full watchlist for the top 5 high-momentum
        setups and send a 'Today's Top Picks' Telegram alert.
        """
        if not self.signal_gen or not self.alerter:
            return
        try:
            watchlist = self.watchlist_mgr.get_watchlist(
                data_fetcher=self.fetcher, learner=self.learner
            )
            logger.info(
                f"[{format_ist_timestamp()}] Pre-market scan: {len(watchlist)} stocks..."
            )
            # Quick score scan (limit candle fetches)
            picks = []
            for sym in watchlist[:30]:
                try:
                    df = self.fetcher.get_candles(sym, interval="5m", days=2)
                    if df is None or len(df) < 20:
                        continue
                    q = self.fetcher.get_quote(sym)
                    if not q:
                        continue
                    chg = float(q.get("change_pct", 0))
                    vol = int(q.get("volume", 0))
                    ltp = float(q.get("ltp", 0))
                    # Simple momentum score: abs(change) + volume surge proxy
                    score = abs(chg) * 10 + (1 if vol > 500000 else 0)
                    if abs(chg) >= 0.3:  # Only stocks moving
                        picks.append((sym, chg, ltp, vol, score))
                except Exception:
                    continue
            picks.sort(key=lambda x: x[4], reverse=True)
            top5 = picks[:5]
            if not top5:
                return
            lines = [f"🎯 <b>Pre-Market Top Picks</b> — {get_current_ist_time().strftime('%d %b %Y')}\n"]
            for i, (sym, chg, ltp, vol, _) in enumerate(top5, 1):
                arrow = "📈" if chg > 0 else "📉"
                lines.append(f"{i}. {arrow} <b>{sym}</b> ₹{ltp:.1f} ({chg:+.2f}%)")
            lines.append("\n⏰ Market opens 9:15 AM IST — watch for breakout confirmation")
            self.alerter.send_text("\n".join(lines))
            logger.info(
                f"[{format_ist_timestamp()}] Pre-market picks sent: "
                + ", ".join(p[0] for p in top5)
            )
        except Exception as e:
            logger.warning(f"Pre-market scan error: {e}")

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------

    def _send_heartbeat(self):
        """Send hourly 'bot alive' status to Telegram during market hours."""
        try:
            if not self.risk_manager or not self.alerter:
                return
            state = self.risk_manager.state
            n_pos = len(state.positions)
            pnl = state.daily_pnl
            # Prefer risk-manager capital (always valid — guarded against 0 override)
            cap = state.available_capital
            # Also show live Groww balance
            live_bal = 0.0
            try:
                bal_info = self.fetcher.get_account_balance() if self.fetcher else {}
                live_bal = bal_info.get("available", 0) if bal_info else 0
            except Exception:
                pass
            status = "🟢 TRADING" if not state.trading_paused else "⏸ PAUSED"
            if state.circuit_breaker_active:
                status = "🔴 CIRCUIT BREAK"
            pos_symbols = ", ".join(state.positions.keys()) if state.positions else "none"
            bal_line = f"₹{live_bal:,.0f}" if live_bal > 0 else f"₹{cap:,.0f} (cached)"
            self.alerter.send_html(
                f"💓 <b>KingTrades Heartbeat</b> — {format_ist_timestamp()}\n"
                f"Status: {status}\n"
                f"Positions: {n_pos} ({pos_symbols})\n"
                f"Day P&amp;L: ₹{pnl:+,.0f}\n"
                f"Available: {bal_line}"
            )
        except Exception as e:
            logger.debug(f"Heartbeat error: {e}")

    # --------------------------------------------------------
    # CACHED WATCHLIST
    # --------------------------------------------------------

    def _get_watchlist_cached(self) -> List[str]:
        """Get watchlist with 15-min cache to avoid excessive API calls."""
        now = get_current_ist_time()
        if (not self._watchlist_cache or
                self._watchlist_cache_time is None or
                (now - self._watchlist_cache_time).total_seconds() > self._watchlist_cache_ttl):
            self._watchlist_cache = self.watchlist_mgr.get_watchlist(
                data_fetcher=self.fetcher, learner=self.learner
            )
            self._watchlist_cache_time = now
        return self._watchlist_cache

    # --------------------------------------------------------
    # CLEANUP
    # --------------------------------------------------------

    def _cleanup(self):
        """Graceful shutdown."""
        self.running = False
        logger.info(f"[{format_ist_timestamp()}] Bot cleanup complete.")


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    # Setup IST logging
    setup_logging(
        log_dir=config.LOG_DIR,
        level=config.LOG_LEVEL,
        module_name="kingtrades"
    )

    logger.info("=" * 60)
    logger.info("  NSE MOMENTUM GROWW AI BOT")
    logger.info("  ⚠️  REAL MONEY — LIVE TRADING BOT")
    logger.info(f"  Server: UK (UTC) | Trading: IST (Asia/Kolkata)")
    logger.info(f"  IST Time: {format_ist_timestamp()}")
    logger.info(f"  Live Trading: {'⚡ ENABLED' if config.LIVE_TRADING_ENABLED else '🔒 DISABLED'}")
    logger.info("=" * 60)

    bot = TradingBot()
    if not bot.initialize():
        logger.critical("Bot initialization failed. Exiting.")
        sys.exit(1)

    # Handle system signals gracefully
    def handle_signal(sig, frame):
        logger.info(f"\n[{format_ist_timestamp()}] Signal {sig} received — shutting down...")
        bot.running = False
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    bot.run()


if __name__ == "__main__":
    main()

