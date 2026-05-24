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
LIVE_TRADING_ENABLED: bool = os.getenv("LIVE_TRADING_ENABLED", "False").lower() in ("true", "1", "yes")

# Capital (USD) — paper account ~$99K; deploy 25% for 13%/month maths
MAX_DAILY_CAPITAL: float = float(os.getenv("MAX_DAILY_CAPITAL", "0"))
# 0 = use full Alpaca account balance dynamically (recommended)
# >0 = hard cap in USD (e.g. 25000 caps at $25K regardless of balance)

MAX_RISK_PER_TRADE_PCT: float = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.5"))
MAX_RISK_PER_TRADE_PCT = min(MAX_RISK_PER_TRADE_PCT, 3.0)   # hard cap 3% — beyond that is gambling

DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "2.5"))
DAILY_LOSS_LIMIT_PCT = min(DAILY_LOSS_LIMIT_PCT, 3.0)   # hard cap 3% — covers 1 full loss + buffer

# Intraday leverage multiplier.
# ⚠️  PDT RULE: Alpaca margin accounts under $25,000 → max 3 day trades/week.
#     Bot does 10-30 trades/day → PDT frozen on day 1 if margin account.
# SAFE OPTION (< $25K): Use Alpaca CASH account → no PDT, no leverage (1x).
# FULL POWER ($25K+):   Margin account → 4x intraday, no PDT restriction.
# Default 1.0 = cash account safe mode. Set to 4.0 only when account ≥ $25,000.
ALPACA_LEVERAGE: float = float(os.getenv("ALPACA_LEVERAGE", "1.0"))
ALPACA_LEVERAGE = max(1.0, min(ALPACA_LEVERAGE, 4.0))  # hard cap at 4x

MAX_POSITIONS: int = 15
MIN_POSITIONS: int = 1
MAX_CAPITAL_PER_TRADE_PCT: float = 30.0   # 30% = $300 on $1K; risk_mgr still limits by ATR-SL

# Fractional shares: Alpaca supports fractional/notional orders on most symbols.
# When enabled, stocks too expensive for 1 whole share use a notional ($ amount) order.
FRACTIONAL_SHARES_ENABLED: bool = os.getenv("FRACTIONAL_SHARES_ENABLED", "True").lower() in ("true", "1", "yes")

# Data delay: free Alpaca SIP feed = 15-min delay. Set 16 for free plan, 1 for Unlimited.
# Default 1: free-plan Alpaca calls return 0 bars and fall through to yfinance (BarCache).
ALPACA_DATA_DELAY_MINUTES: int = int(os.getenv("ALPACA_DATA_DELAY_MINUTES", "1"))

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
ATR_SL_MULTIPLIER: float = 1.0          # 1× ATR stop — wide enough to survive intraday noise, tight enough for 2:1+ R:R
ATR_T1_MULTIPLIER: float = 1.5          # T1 quick-book target at 1.5:1 R:R (scalping)
ATR_TP_MULTIPLIER: float = 2.5          # T2 at 2.5:1 — quick exit, don't overstay
ATR_TP_RUNNER: float = 5.0             # T3 runner for A+ setups — trimmed from 7.0
ATR_TRAIL_MULTIPLIER: float = 0.50      # wider trail = let winners breathe before stopping out
BREAKEVEN_TRIGGER_PCT: float = 0.30     # free trade at 0.3% profit — gives room before locking in
PARTIAL_EXIT_T1_PCT: float = 40.0       # 40% at T1 — lock some profit, keep 60% running
PARTIAL_EXIT_T2_PCT: float = 25.0       # 25% at T2 — meaningful second slice
RUNNER_PCT: float = 35.0                # 35% runner — bigger slice on high-conviction trades

# ── Top-1% trader hard gates ──────────────────────────────────────────────
MIN_RISK_REWARD: float = 2.0       # Minimum R:R measured at T2 target (2.5x SL) — previously
                                    # measured at T1 (1.5x) which was always exactly the minimum
GAP_DIRECTION_BOOST: float = 10.0  # Score boost when gap aligns with trade direction
ICT_CONFLUENCE_BOOST: float = 15.0 # Bonus when OB + FVG + BOS all fire together

# VIX regime thresholds (top-1% know: size by fear level)
VIX_COMPLACENCY: float = 14.0   # Below = low vol, mean-reversion, reduce momentum size
VIX_OPTIMAL_LOW: float = 14.0   # 14-25 = optimal momentum trading zone
VIX_OPTIMAL_HIGH: float = 25.0
VIX_CAUTION: float = 25.0       # 25-35 = fear rising, reduce size 25%
VIX_DANGER: float = 35.0        # 35+ = extreme fear, no LONG trades
VIX_CRASH: float = 40.0         # 40+ = crisis mode, flat or short only

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

MIN_SIGNAL_SCORE: float = 72.0        # calibrated to real market — best setups score 70-75; 14-gate HAF is the quality guard
HIGH_CONFIDENCE_SCORE: float = 80.0   # A+ tier: only hit on strong regime + full MTF + volume surge days
PREMIUM_SCORE: float = 76.0
MIN_VOLUME_RATIO: float = 1.8
REQUIRE_MTF_ALIGNMENT: bool = False   # soft MTF check via penalty in signal_gen; hard gate in HAF
REQUIRE_POWER_HOUR: bool = False      # OFF — midday now active at 0.5× size; power hour gate was wasting 2h/day
HEIKIN_ASHI_CONFIRM: bool = False
MAX_TRADES_PER_DAY: int = 20          # quality over quantity — 20 perfect trades > 40 marginal ones
MAX_TRADES_PER_STOCK: int = 3         # re-enter only on confirmed continuation setups

# ============================================================
# CIRCUIT BREAKERS
# ============================================================
NIFTY_CIRCUIT_PCT: float = 2.0        # Reused as SPY circuit threshold
CONSECUTIVE_LOSS_LIMIT: int = 3
PAUSE_AFTER_LOSSES_MINUTES: int = 15   # was 30 — half the wait, back in game faster

# ============================================================
# WATCHLIST — US liquid momentum stocks
# ============================================================
DEFAULT_WATCHLIST = [
    # Mega-cap tech & AI (deepest liquidity, daily 3–8% moves)
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    # Semiconductors — highest beta, follow NVDA
    "AMD", "MU", "QCOM", "ARM", "SMCI", "AVGO", "INTC", "MRVL", "ON", "LRCX",
    # High-momentum growth / SaaS
    "NFLX", "COIN", "PLTR", "MSTR", "CRWD", "PANW", "ZS", "DDOG", "NET", "NOW", "SNOW",
    # Consumer & social momentum
    "UBER", "SHOP", "ABNB", "MELI", "RBLX",
    # Crypto miners (very high beta)
    "MARA", "RIOT",
    # Leveraged ETFs — 2-3x the index move (best for momentum breakouts)
    "SPY", "QQQ", "IWM", "TQQQ", "SPXL", "SOXL",
    # Fintech / high-growth finance
    "SOFI", "HOOD",
    # Big finance
    "JPM", "GS", "MS",
    # Energy
    "XOM", "CVX", "OXY", "SLB",
    # Biotech momentum
    "MRNA", "HIMS",
]
# Bar data: Alpaca REST (unlimited symbols). WebSocket real-time: Alpaca stream.
# 55 symbols — all liquid US stocks, min $5M daily dollar volume.

DAILY_PROFIT_TARGET: float = float(os.getenv("DAILY_PROFIT_TARGET", "0"))
# 0 = compute from DAILY_PROFIT_TARGET_PCT × live balance (recommended — auto-compounds)
# >0 = fixed dollar target (overrides percentage calculation)

DAILY_PROFIT_TARGET_PCT: float = float(os.getenv("DAILY_PROFIT_TARGET_PCT", "4.0"))
# 4.0%/day target: bot stays in NORMAL/AGGRESSIVE mode until 4% hit, LOCK at 8%, STOP at 12%
# Scalping math: 15 trades × 1.5% risk × 1.5:1 R:R × 55% win rate = ~3.7% net/day
# On $1,500: $60/day target → PROTECTION at $60 → LOCK at $120 (8%) → STOP at $180 (12%)

MONTHLY_TARGET_PCT: float = float(os.getenv("MONTHLY_TARGET_PCT", "30.0"))
# 30% monthly target — catch-up engine boosts daily target when behind this pace
# Math: 22 days × 4%/day compounded = 138% (well above 30%) — very achievable floor

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
# Use absolute path so logs land in the right place regardless of cwd
_BASE_DIR = Path(__file__).parent.resolve()
LOG_DIR: str = os.getenv("LOG_DIR", str(_BASE_DIR / "logs"))
TRADE_LOG_DIR: str = f"{LOG_DIR}/trades"
PERFORMANCE_LOG_DIR: str = f"{LOG_DIR}/performance"
CHART_DIR: str = str(_BASE_DIR / "charts")

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
TARGET_MONTHLY_RETURN_PCT: float = 30.0  # hard target — matches MONTHLY_TARGET_PCT (live catch-up engine)
TARGET_WIN_RATE: float = 57.0            # slightly higher bar: 57%+ WR
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
    "OPENING_DRIVE": 2.2,    # 9:30-10:30 ET — maximum conviction, full aggression
    "MORNING":       1.8,    # 10:30-11:30 ET — trend continuation, push harder
    "MIDDAY_CHOP":   0.6,    # 11:30-13:30 ET — reduced size but NOT zero; lunch reversals are real
    "AFTERNOON":     2.0,    # 13:30-15:30 ET — institutional resumption, near-peak aggression
    "CLOSING":       1.0,    # 15:30-16:00 ET — EOD momentum, full size on confirmed moves
}

# ============================================================
# CONCURRENT SCANNING
# ============================================================
SCAN_MAX_WORKERS: int = 12          # more workers for 55-symbol watchlist
SCAN_SYMBOL_TIMEOUT: int = 15       # tighter per-symbol timeout
SCAN_TOTAL_TIMEOUT: int = 60        # full scan must finish in 60s for 30s cycle

# ── RVOL mega-boost (rare: > 5x volume = explosive move imminent) ──────────
RVOL_MEGA_THRESHOLD: float = 5.0    # > 5x average volume
RVOL_MEGA_BOOST_PTS: float = 8.0    # bonus score points

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
    0: 0.85,   # Monday   — gap reversals common, slightly cautious
    1: 1.10,   # Tuesday  — best trend day, size up
    2: 1.10,   # Wednesday— trend continuation, size up
    3: 0.95,   # Thursday — good but watch for reversal
    4: 0.80,   # Friday   — EOD risk, still tradeable
}

DOW_MIN_SCORE: dict = {
    0: 72.0,   # Monday
    1: 72.0,   # Tuesday — best trend day
    2: 72.0,   # Wednesday — trend continuation
    3: 72.0,   # Thursday
    4: 74.0,   # Friday — slightly stricter: EOD gap risk, no new runners
}

DOW_MAX_TRADES: dict = {
    0: 25,   # Monday
    1: 30,   # Tuesday — best trend day
    2: 30,   # Wednesday — trend continuation
    3: 25,   # Thursday
    4: 20,   # Friday — EOD risk managed by 0.8× size, not trade count
}

WEEKLY_PROFIT_TARGET_PCT: float = 8.0    # 30%/mo ÷ 4.33 weeks = 6.9%/wk — use 8% as target
WEEKLY_PROFIT_LOCK_PCT: float = 15.0    # don't throttle size until 15% weekly gain secured
WEEKLY_LOSS_STOP_PCT: float = 5.0       # weekly stop-out at -5% — slightly more runway
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
    if LIVE_TRADING_ENABLED and "paper-api" in ALPACA_BASE_URL:
        issues.append(
            "CRITICAL: LIVE_TRADING_ENABLED=True but ALPACA_BASE_URL points to paper endpoint. "
            "Set ALPACA_BASE_URL=https://api.alpaca.markets for real trading."
        )
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
