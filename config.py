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
# 18yr rule: "It's not the number of trades, it's the quality."
MIN_SIGNAL_SCORE: float = 72.0          # Hard minimum — no trade below this
HIGH_CONFIDENCE_SCORE: float = 82.0     # Full size above this
PREMIUM_SCORE: float = 90.0             # 1.2x size — A+ grade setups only
MIN_VOLUME_RATIO: float = 1.8           # Min volume surge for entry
REQUIRE_MTF_ALIGNMENT: bool = True      # Always require ≥2 TF alignment
REQUIRE_POWER_HOUR: bool = True         # Only trade during power windows
HEIKIN_ASHI_CONFIRM: bool = True        # Require HA confirmation
MAX_TRADES_PER_DAY: int = 6             # Quality > quantity. Max 6 per day.
MAX_TRADES_PER_STOCK: int = 2           # Max 2 trades per stock per day

# ============================================================
# CIRCUIT BREAKERS
# ============================================================
# Breakeven SL — move SL to entry when trade is 0.5% in profit
BREAKEVEN_TRIGGER_PCT: float = 0.5   # % profit to trigger breakeven SL move

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
    if not GROWW_TOTP_SECRET:
        issues.append("GROWW_TOTP_SECRET not set — TOTP auto-login disabled")
    if not GROWW_CLIENT_ID and not GROWW_AUTH_TOKEN:
        issues.append("GROWW_CLIENT_ID not set — add Cloud API Key from Groww Trade API portal")
    if not TELEGRAM_BOT_TOKEN:
        issues.append("TELEGRAM_BOT_TOKEN not set — alerts disabled")
    if not TELEGRAM_CHAT_ID:
        issues.append("TELEGRAM_CHAT_ID not set — alerts disabled")
    return issues


if __name__ == "__main__":
    issues = validate_config()
    if issues:
        print("Configuration issues:")
        for i in issues:
            print(f"  ⚠️  {i}")
    else:
        print("✅ Configuration valid")
    print(f"Live trading: {'ENABLED — REAL MONEY' if LIVE_TRADING_ENABLED else 'DISABLED (safe mode)'}")
    print(f"Client ID set: {'yes' if GROWW_CLIENT_ID else 'NO — add GROWW_CLIENT_ID to .env'}")
    print(f"TOTP secret:   {'set' if GROWW_TOTP_SECRET else 'NO — add GROWW_TOTP_SECRET to .env'}")
    print(f"Timezone: IST (Asia/Kolkata) — Server: UK UTC")
