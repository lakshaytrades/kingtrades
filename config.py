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
TOKEN_REFRESH_TIME_IST = time(8, 45)  # TOTP auto-login time

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
