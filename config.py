"""
config.py — US Momentum Alpaca AI Bot
Central configuration for NYSE/NASDAQ intraday trading.

⚠️ WARNING: This bot places REAL orders with REAL money on Alpaca.
All trading logic uses ET (America/New_York). Server may be UTC.
"""

import os
from datetime import time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# ============================================================
# TIMEZONE
# ============================================================
ET  = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

MARKET_OPEN_ET       = time(9, 30)
MARKET_CLOSE_ET      = time(16, 0)
MARKET_SQUAREOFF_ET  = time(15, 50)
MARKET_SQUAREOFF_WARN = time(15, 45)
PRE_MARKET_START_ET  = time(8, 30)

TRADING_DAYS = {0, 1, 2, 3, 4}   # Mon–Fri

# ============================================================
# ALPACA API CREDENTIALS
# ============================================================
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# NEWS / SENTIMENT
# ============================================================
NEWS_API_KEY       = os.getenv("NEWS_API_KEY", "")
ALPHA_VANTAGE_KEY  = os.getenv("ALPHA_VANTAGE_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

# ============================================================
# TRADING CONFIGURATION
# ============================================================
LIVE_TRADING_ENABLED: bool = os.getenv("LIVE_TRADING_ENABLED", "True").lower() in ("true", "1", "yes")

# Capital (USD)
MAX_DAILY_CAPITAL: float = float(os.getenv("MAX_DAILY_CAPITAL", "500"))

MAX_RISK_PER_TRADE_PCT: float = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.0"))
MAX_RISK_PER_TRADE_PCT = min(MAX_RISK_PER_TRADE_PCT, 1.0)

DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "2.0"))
DAILY_LOSS_LIMIT_PCT = min(DAILY_LOSS_LIMIT_PCT, 3.0)

MAX_POSITIONS: int = 15
MIN_POSITIONS: int = 1
MAX_CAPITAL_PER_TRADE_PCT: float = 20.0

# ============================================================
# STRATEGY PARAMETERS
# ============================================================
RSI_PERIOD: int = 14
RSI_OVERSOLD: float = 35.0
RSI_OVERBOUGHT: float = 65.0
RSI_EXTREME_OVERSOLD: float = 25.0
RSI_EXTREME_OVERBOUGHT: float = 75.0

MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL: int = 9

ATR_PERIOD: int = 14
ATR_SL_MULTIPLIER: float = 1.4
ATR_TP_MULTIPLIER: float = 3.0   # T2 = exact 1:3 R:R (Grok spec)
ATR_TRAIL_MULTIPLIER: float = 0.8
PARTIAL_EXIT_T1_PCT: float = 50.0
PARTIAL_EXIT_T2_PCT: float = 30.0
RUNNER_PCT: float = 20.0

EMA_FAST: int = 9
EMA_MID: int = 21
EMA_SLOW: int = 50
EMA_TREND: int = 200

VOLUME_SURGE_MULTIPLIER: float = 1.8
VOLUME_HIGH_CONVICTION: float = 2.5
VOLUME_SMA_PERIOD: int = 20

BB_PERIOD: int = 20
BB_STD: float = 2.0

STOCH_K_PERIOD: int = 14
STOCH_D_PERIOD: int = 3
STOCH_SMOOTH: int = 3

VWAP_DEVIATION_THRESHOLD: float = 0.5

PRIMARY_TIMEFRAME: str = "5Min"
CONFIRMATION_TIMEFRAME: str = "15Min"
TREND_TIMEFRAME: str = "1Hour"

MIN_SIGNAL_SCORE: float = 68.0
HIGH_CONFIDENCE_SCORE: float = 80.0
PREMIUM_SCORE: float = 90.0
MIN_VOLUME_RATIO: float = 1.8
REQUIRE_MTF_ALIGNMENT: bool = False
REQUIRE_POWER_HOUR: bool = False
HEIKIN_ASHI_CONFIRM: bool = False
MAX_TRADES_PER_DAY: int = 15
MAX_TRADES_PER_STOCK: int = 2

# ============================================================
# CIRCUIT BREAKERS
# ============================================================
BREAKEVEN_TRIGGER_PCT: float = 0.5
NIFTY_CIRCUIT_PCT: float = 2.0        # Reused as SPY circuit threshold
CONSECUTIVE_LOSS_LIMIT: int = 3
PAUSE_AFTER_LOSSES_MINUTES: int = 30

# ============================================================
# WATCHLIST — US liquid momentum stocks
# ============================================================
DEFAULT_WATCHLIST = [
    # Mega-cap tech & momentum (liquid, tight spreads)
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "AMD", "NFLX", "COIN",
    # ETFs — best for scalping (deepest liquidity)
    "SPY", "QQQ", "IWM", "TQQQ", "SPXL",
    # High-beta momentum plays
    "MSTR", "PLTR", "SOFI",
    "SMCI", "ARM", "MU", "QCOM",
    # Financials & energy
    "JPM", "GS", "BAC", "XOM", "CVX", "OXY",
]
# IEX free-tier WebSocket limit is 30 symbols — keep DEFAULT_WATCHLIST ≤28

DAILY_PROFIT_TARGET: float = float(os.getenv("DAILY_PROFIT_TARGET", "200"))

# ============================================================
# OPTIONS SCALPING — Alpaca Markets
# ============================================================
OPTIONS_ENABLED:         bool  = os.getenv("OPTIONS_ENABLED", "True").lower() in ("true", "1", "yes")
OPTIONS_MIN_STOCK_SCORE: float = float(os.getenv("OPTIONS_MIN_STOCK_SCORE", "70"))
OPTIONS_MAX_POSITIONS:   int   = int(os.getenv("OPTIONS_MAX_POSITIONS", "3"))
OPTIONS_MAX_PREMIUM_PCT: float = float(os.getenv("OPTIONS_MAX_PREMIUM_PCT", "5.0"))
OPTIONS_MAX_CONTRACTS:   int   = int(os.getenv("OPTIONS_MAX_CONTRACTS", "5"))
OPTIONS_PREFERRED_DTE:   int   = int(os.getenv("OPTIONS_PREFERRED_DTE", "1"))
OPTIONS_STOP_PCT:        float = float(os.getenv("OPTIONS_STOP_PCT", "45"))
OPTIONS_TARGET1_PCT:     float = float(os.getenv("OPTIONS_TARGET1_PCT", "80"))
OPTIONS_TARGET2_PCT:     float = float(os.getenv("OPTIONS_TARGET2_PCT", "150"))
OPTIONS_MAX_IV_RANK:     float = float(os.getenv("OPTIONS_MAX_IV_RANK", "65"))
OPTIONS_MIN_DELTA:       float = float(os.getenv("OPTIONS_MIN_DELTA", "0.25"))
OPTIONS_MAX_SPREAD_PCT:  float = float(os.getenv("OPTIONS_MAX_SPREAD_PCT", "18"))
UOA_SCAN_ENABLED:        bool  = os.getenv("UOA_SCAN_ENABLED", "True").lower() in ("true", "1", "yes")
UOA_VOL_OI_THRESHOLD:    float = float(os.getenv("UOA_VOL_OI_THRESHOLD", "2.5"))

CUSTOM_WATCHLIST_STR = os.getenv("CUSTOM_WATCHLIST", "")
WATCHLIST = (
    [s.strip() for s in CUSTOM_WATCHLIST_STR.split(",") if s.strip()]
    if CUSTOM_WATCHLIST_STR
    else DEFAULT_WATCHLIST
)

# S&P 500 benchmark
BENCHMARK_SYMBOL: str = "SPY"
NIFTY_SYMBOL = BENCHMARK_SYMBOL   # alias for any code still referencing NIFTY_SYMBOL
NIFTY_GROWW_SYMBOL = BENCHMARK_SYMBOL  # alias for legacy references

# ============================================================
# LOGGING
# ============================================================
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR: str = os.getenv("LOG_DIR", "logs")
TRADE_LOG_DIR: str = f"{LOG_DIR}/trades"
PERFORMANCE_LOG_DIR: str = f"{LOG_DIR}/performance"
CHART_DIR: str = "charts"

for d in [LOG_DIR, TRADE_LOG_DIR, PERFORMANCE_LOG_DIR, CHART_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

# ============================================================
# API RETRY SETTINGS
# ============================================================
MAX_RETRIES: int = 4
RETRY_DELAYS: list = [2, 4, 8, 16]
API_TIMEOUT: int = 10

# ============================================================
# NEWS FILTER
# ============================================================
NEWS_BLACKOUT_MINUTES: int = 30
HIGH_IMPACT_KEYWORDS = [
    "Fed", "FOMC", "interest rate", "CPI", "GDP", "inflation",
    "jobs report", "NFP", "SEC", "circuit breaker", "market halt",
]

# ============================================================
# TRAINER
# ============================================================
TRAINER_DATA_DIR: str = "data"
TRAINER_RESULTS_DIR: str = "trainer_results"
BACKTEST_SLIPPAGE_PCT: float = 0.05
BACKTEST_BROKERAGE_PCT: float = 0.01   # Alpaca ~$0 commission but spread cost
BACKTEST_TAX_PCT: float = 0.0
TARGET_MONTHLY_RETURN_PCT: float = 5.0
TARGET_WIN_RATE: float = 55.0
TARGET_SHARPE: float = 1.5
MAX_DRAWDOWN_LIMIT: float = 8.0

for d in [TRAINER_DATA_DIR, TRAINER_RESULTS_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

# ============================================================
# VOLUME PROFILE
# ============================================================
VP_ENABLED: bool = True
VP_NUM_BINS: int = 100
VP_VALUE_AREA_PCT: float = 70.0
VP_COMPOSITE_DAYS: int = 5
VP_HVN_STD_THRESHOLD: float = 0.5
VP_LVN_STD_THRESHOLD: float = 1.0

# ============================================================
# ADVANCED RISK MANAGEMENT
# ============================================================
MAX_PORTFOLIO_HEAT_PCT: float = 10.0   # was 3.0 — allows up to 10 concurrent 1%-risk trades
MAX_POSITIONS_PER_SECTOR: int = 5      # was 2 — allows more tech/momentum positions

SESSION_SIZE_MULTIPLIERS = {
    "OPENING_DRIVE": 1.00,   # 09:30-10:30 — peak momentum
    "MORNING":       0.80,   # 10:30-11:30 — good but fading
    "MIDDAY_CHOP":   0.50,   # 11:30-13:30 — chop zone
    "AFTERNOON":     0.80,   # 13:30-15:30 — institutional resumption
    "CLOSING":       0.30,   # 15:30-16:00 — risk-off
}

# ============================================================
# CONCURRENT SCANNING
# ============================================================
SCAN_MAX_WORKERS: int = 6
SCAN_SYMBOL_TIMEOUT: int = 20
SCAN_TOTAL_TIMEOUT: int = 90

# ============================================================
# TRAINER — ADVANCED
# ============================================================
MONTE_CARLO_SIMULATIONS: int = 1000
MONTE_CARLO_RUIN_THRESHOLD: float = 40.0
MONTE_CARLO_MAX_RUIN_PCT: float = 5.0
SENSITIVITY_ROBUSTNESS_MIN: float = 0.70

# ============================================================
# POSITION SIZING / GAP / LIQUIDITY
# ============================================================
MIN_DAILY_VOLUME: int = 1_000_000    # 1M shares/day minimum for US stocks
MAX_GAP_PCT: float = 2.0
LARGE_GAP_PCT: float = 3.5
EXTREME_GAP_PCT: float = 5.0
CIRCUIT_BUFFER_PCT: float = 0.5
CIRCUIT_BANDS: list = [5.0, 10.0, 20.0]
CORP_ACTION_BUFFER_DAYS: int = 2
FO_LIST_CACHE_HOURS: int = 24
API_HEALTH_CHECK_ENABLED: bool = True

# Day-of-week multipliers (US market patterns)
DOW_SIZE_MULTIPLIERS: dict = {
    0: 0.70,   # Monday   — gap-and-trap, cautious
    1: 1.00,   # Tuesday  — best trend day
    2: 1.00,   # Wednesday— trend continuation
    3: 0.85,   # Thursday — can be choppy
    4: 0.65,   # Friday   — early close risk, reduce size
}

DOW_MIN_SCORE: dict = {
    0: 68.0,   # Monday
    1: 68.0,   # Tuesday
    2: 68.0,   # Wednesday
    3: 68.0,   # Thursday
    4: 72.0,   # Friday — slightly higher (early close risk)
}

DOW_MAX_TRADES: dict = {
    0: 15,   # Monday
    1: 15,   # Tuesday
    2: 15,   # Wednesday
    3: 15,   # Thursday
    4: 10,   # Friday — slightly lower (early close risk)
}

WEEKLY_PROFIT_TARGET_PCT: float = 2.0
WEEKLY_PROFIT_LOCK_PCT: float = 1.5
WEEKLY_LOSS_STOP_PCT: float = 2.5
WEEKLY_DATA_FILE: str = "data/weekly_pnl.json"

# NSE-specific fields kept as stubs so any remaining references don't crash
NSE_DATA_ENABLED: bool = False
OPTION_CHAIN_ENABLED: bool = False
FII_DII_ENABLED: bool = False

# ============================================================
# VALIDATION
# ============================================================
def validate_config() -> list:
    """Validate critical configuration. Returns list of issues."""
    issues = []
    if not ALPACA_API_KEY:
        issues.append("ALPACA_API_KEY not set — Alpaca trading disabled")
    if not ALPACA_SECRET_KEY:
        issues.append("ALPACA_SECRET_KEY not set — Alpaca trading disabled")
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
    print(f"Alpaca key:   {'set' if ALPACA_API_KEY else 'NOT SET'}")
    print(f"Capital:      ${MAX_DAILY_CAPITAL:,.0f}")
    print(f"Timezone:     ET (America/New_York)")
