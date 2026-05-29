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

# Capital (USD) — hard cap what the bot uses as its "bankroll" each day
# IMPORTANT: 0 means "use full account balance" — DANGEROUS on a $90k account.
# Set this to the actual USD amount you want to risk, e.g. 5000 for a $5k sub-account.
MAX_DAILY_CAPITAL: float = float(os.getenv("MAX_DAILY_CAPITAL", "0"))
# Safety net: when MAX_DAILY_CAPITAL=0 we cap internally at 10% of account equity
# via the risk_manager so a $90k account can never accidentally use $90k as capital.
# Set MAX_DAILY_CAPITAL explicitly in .env to override, e.g. MAX_DAILY_CAPITAL=5000

MAX_RISK_PER_TRADE_PCT: float = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.8"))
MAX_RISK_PER_TRADE_PCT = min(MAX_RISK_PER_TRADE_PCT, 2.0)   # raised cap: A+ signals need room to size

# Grade-based risk unlocking — institutional practice: size your best setups bigger
# A+ Grand Slam (7+ confluence): 1.5× base risk — these are 70%+ win rate setups
# A  grade       (5+ confluence): 1.0× base risk — standard full size
# B  grade       (borderline)   : 0.6× base risk — conservative
HIGH_CONFIDENCE_RISK_MULTIPLIER: float = float(os.getenv("HIGH_CONFIDENCE_RISK_MULTIPLIER", "1.5"))

DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "2.0"))
DAILY_LOSS_LIMIT_PCT = min(DAILY_LOSS_LIMIT_PCT, 2.0)   # hard cap 2% — stop the day early

# Intraday leverage multiplier.
# ⚠️  PDT RULE: Alpaca margin accounts under $25,000 → max 3 day trades/week.
#     Bot does 10-30 trades/day → PDT frozen on day 1 if margin account.
# SAFE OPTION (< $25K): Use Alpaca CASH account → no PDT, no leverage (1x).
# FULL POWER ($25K+):   Margin account → 4x intraday, no PDT restriction.
# Default 1.0 = cash account safe mode. Set to 4.0 only when account ≥ $25,000.
ALPACA_LEVERAGE: float = float(os.getenv("ALPACA_LEVERAGE", "1.0"))
ALPACA_LEVERAGE = max(1.0, min(ALPACA_LEVERAGE, 4.0))  # hard cap at 4x

MAX_POSITIONS: int = 5       # was 20 — too many concurrent losers compound the damage
MIN_POSITIONS: int = 1
MAX_CAPITAL_PER_TRADE_PCT: float = 10.0   # was 30% — 30% of $90k = $27k per trade, catastrophic

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
ATR_SL_MULTIPLIER: float = 1.0          # 1× ATR stop — wide enough to survive intraday noise
ATR_T1_MULTIPLIER: float = 1.5          # T1 quick-book at 1.5:1 — lock partial profit fast
ATR_TP_MULTIPLIER: float = 3.5          # T2 at 3.5:1 — was 2.5, wider target = bigger wins
ATR_TP_RUNNER: float = 6.0             # T3 runner extended from 5.0 — catches full trend moves
ATR_TRAIL_MULTIPLIER: float = 0.80      # wider trail after T2 (was 0.50) — runners breathe more
BREAKEVEN_TRIGGER_PCT: float = 0.15     # move stop to breakeven after only 0.15% gain — convert near-losses to free trades
PARTIAL_EXIT_T1_PCT: float = 40.0       # 40% at T1 (up from 30%) — lock more profit early, less at risk
PARTIAL_EXIT_T2_PCT: float = 20.0       # 20% at T2 — keep runner alive
RUNNER_PCT: float = 40.0                # 40% runner (down from 50%) — slightly more locked in

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

MIN_SIGNAL_SCORE: float = float(os.getenv("MIN_SIGNAL_SCORE", "72.0"))
                                       # 72 = achievable on typical good setups; 18 hard gates do quality filtering
HIGH_CONFIDENCE_SCORE: float = 80.0   # A+ after bonuses: 72 base + 8 bonus points = 80
GRAND_SLAM_MIN_SCORE: float = float(os.getenv("GRAND_SLAM_MIN_SCORE", "88.0"))  # Grand Slam requires 88+ (2× size)
PREMIUM_SCORE: float = 78.0           # A grade entry: solid signal with good confluence

# ── 70-80% Win Rate Precision Gates ────────────────────────────────────────
RETEST_ENTRY_ENABLED: bool  = os.getenv("RETEST_ENTRY_ENABLED",  "True").lower()  in ("true","1","yes")
FALSE_BREAKOUT_GATE:  bool  = os.getenv("FALSE_BREAKOUT_GATE",   "True").lower()  in ("true","1","yes")
CLEAR_AIR_GATE:       bool  = os.getenv("CLEAR_AIR_GATE",        "True").lower()  in ("true","1","yes")
DAILY_HTF_GATE:       bool  = os.getenv("DAILY_HTF_GATE",        "True").lower()  in ("true","1","yes")
SPREAD_MAX_PCT:       float = float(os.getenv("SPREAD_MAX_PCT",  "0.15"))  # max bid-ask spread %
EARNINGS_PROXIMITY_GATE: bool = os.getenv("EARNINGS_PROXIMITY_GATE", "True").lower() in ("true","1","yes")
EARNINGS_PROXIMITY_DAYS: int  = int(os.getenv("EARNINGS_PROXIMITY_DAYS", "3"))    # skip N days before earnings
# Gate 19: Minimum indicator confluence — require ≥2 of 4 core indicators aligned
# Prevents opening-window time bonus from pushing weak-indicator signals over the threshold
INDICATOR_FLOOR_GATE: bool = os.getenv("INDICATOR_FLOOR_GATE", "True").lower() in ("true","1","yes")
INDICATOR_FLOOR_MIN:  int  = int(os.getenv("INDICATOR_FLOOR_MIN", "2"))  # minimum indicators that must align
# Gate 20: Bid/Ask Volume Imbalance — buyers/sellers must be aggressive side
BA_IMBALANCE_GATE:      bool  = os.getenv("BA_IMBALANCE_GATE", "True").lower() in ("true","1","yes")
BA_IMBALANCE_MIN_RATIO: float = float(os.getenv("BA_IMBALANCE_MIN_RATIO", "0.52"))
# Gate 21: Order Flow Imbalance — cumulative delta must not strongly oppose direction
OFI_GATE_ENABLED:       bool  = os.getenv("OFI_GATE_ENABLED", "True").lower() in ("true","1","yes")
# Score components: OFI, Dark Pool, Session Momentum
OFI_SCORE_ENABLED:             bool = os.getenv("OFI_SCORE_ENABLED", "True").lower() in ("true","1","yes")
DARK_POOL_ENABLED:             bool = os.getenv("DARK_POOL_ENABLED", "True").lower() in ("true","1","yes")
SESSION_MOMENTUM_ENABLED:      bool = os.getenv("SESSION_MOMENTUM_ENABLED", "True").lower() in ("true","1","yes")
# Sector RS + Squeeze scanner (already in code, ensure flags exist)
SECTOR_RS_ENABLED:             bool = os.getenv("SECTOR_RS_ENABLED", "True").lower() in ("true","1","yes")
SQUEEZE_SCANNER_ENABLED:       bool = os.getenv("SQUEEZE_SCANNER_ENABLED", "True").lower() in ("true","1","yes")
PEAD_SCORER_ENABLED:           bool = os.getenv("PEAD_SCORER_ENABLED", "True").lower() in ("true","1","yes")
FUTURES_BIAS_ENABLED:          bool = os.getenv("FUTURES_BIAS_ENABLED", "True").lower() in ("true","1","yes")
MIN_VOLUME_RATIO: float = 1.0         # minimum to enter pipeline — bonuses reward higher volume
REQUIRE_MTF_ALIGNMENT: bool = False   # MTF gates as bonuses (+8 pts each TF agreed) not hard blocks
REQUIRE_POWER_HOUR: bool = False
HEIKIN_ASHI_CONFIRM: bool = False
MAX_TRADES_PER_DAY: int = 15          # enough capacity to scan 100 symbols for 2-3 real setups
MAX_TRADES_PER_STOCK: int = 3         # allow re-entry on strong trends

# ============================================================
# CIRCUIT BREAKERS
# ============================================================
NIFTY_CIRCUIT_PCT: float = 2.0        # Reused as SPY circuit threshold
CONSECUTIVE_LOSS_LIMIT: int = 2        # pause after 2 consecutive losses — catches losing streaks fast
PAUSE_AFTER_LOSSES_MINUTES: int = 25   # 25 min pause — enough time for market conditions to shift
LARGE_LOSS_PAUSE_PCT: float = 1.5      # pause if single trade loses ≥1.5% of daily capital (was 3%)
LARGE_LOSS_PAUSE_MINUTES: int = 25     # pause duration after large single loss
NO_ENTRY_AFTER_ET_HOUR: int = 15       # no new entries at or after 3:00 PM ET (last 30 min = noisy reversals)
NO_ENTRY_AFTER_ET_MINUTE: int = 0
SKIP_VOLATILE_LONGS: bool = True       # in HIGH_VOLATILITY regime, skip LONG entries (only shorts)

# ── Short selling ──────────────────────────────────────────────────────────
SHORT_SELLING_ENABLED: bool = os.getenv("SHORT_SELLING_ENABLED", "True").lower() in ("true","1","yes")

# ── VWAP mean-reversion strategy (best in choppy markets) ──────────────────
VWAP_REVERSION_ENABLED: bool = True
VWAP_REVERSION_MIN_DEVIATION_ATR: float = 1.5   # price must be 1.5+ ATR from VWAP

# ── AI News sentiment filter ────────────────────────────────────────────────
GEMINI_NEWS_FILTER_ENABLED: bool = True          # use Gemini/Claude to score news sentiment
GEMINI_NEWS_SCORE_MAX_DELTA: float = 15.0        # max pts added/removed from signal score

# ── Aggressive sizing on elite setups — defined earlier from env var, not duplicated here ──

# ── Opening Range Breakout (9:30–9:45 AM) ──────────────────────────────────
ORB_ENABLED: bool = True
ORB_WINDOW_MINUTES: int = 15         # range established in first 15 min
ORB_MIN_RANGE_PCT: float = 0.3       # range must be at least 0.3% of price
ORB_RISK_MULTIPLIER: float = 1.2     # slightly larger size on ORB plays

# ============================================================
# WATCHLIST — US liquid momentum stocks
# ============================================================
DEFAULT_WATCHLIST = [
    # ── Mega-cap tech & AI (deepest liquidity, daily 3–8% moves) ──────────
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA",
    # ── Semiconductors — highest beta, follow NVDA ─────────────────────────
    "AMD", "MU", "QCOM", "ARM", "SMCI", "AVGO", "INTC", "MRVL", "ON",
    "LRCX", "KLAC", "AMAT", "ASML", "TSM", "TXN", "MCHP",
    # ── AI / Cloud / SaaS high-momentum ────────────────────────────────────
    "NFLX", "COIN", "PLTR", "MSTR", "CRWD", "PANW", "ZS", "DDOG", "NET",
    "NOW", "SNOW", "TEAM", "HUBS", "OKTA", "MDB", "GTLB", "U",
    "AI", "SOUN", "BBAI",
    # ── Consumer & social momentum ──────────────────────────────────────────
    "UBER", "SHOP", "ABNB", "MELI", "RBLX", "LYFT", "DASH", "YELP",
    # ── Crypto / blockchain high-beta ─────────────────────────────────────
    "MARA", "RIOT", "HUT", "CLSK", "BTBT", "CIFR",
    # ── Leveraged ETFs — 2-3x index (strongest momentum signals) ──────────
    "SPY", "QQQ", "IWM", "TQQQ", "SPXL", "SOXL", "TECL", "FNGU",
    # ── Fintech / high-growth finance ──────────────────────────────────────
    "SOFI", "HOOD", "AFRM", "SQ", "PYPL", "V", "MA",
    # ── Big finance & energy ────────────────────────────────────────────────
    "JPM", "GS", "MS", "BAC", "XOM", "CVX", "OXY", "SLB", "MPC",
    # ── Biotech / healthcare momentum ──────────────────────────────────────
    "MRNA", "HIMS", "LLY", "NVO", "VKTX", "RXRX",
    # ── EV & clean energy ──────────────────────────────────────────────────
    "RIVN", "LCID", "NIO", "PLUG", "FSLR", "ENPH",
    # ── Defense & industrials ──────────────────────────────────────────────
    "LMT", "RTX", "NOC", "GE", "CAT",
]
# 100 symbols — all liquid US stocks with min $1M daily dollar volume.
# More watchlist = more breakout setups scanned per day = more executable signals.

DAILY_PROFIT_TARGET: float = float(os.getenv("DAILY_PROFIT_TARGET", "0"))
# 0 = compute from DAILY_PROFIT_TARGET_PCT × live balance (recommended — auto-compounds)
# >0 = fixed dollar target (overrides percentage calculation)

DAILY_PROFIT_TARGET_PCT: float = float(os.getenv("DAILY_PROFIT_TARGET_PCT", "1.0"))
# 1.0%/day — realistic achievable target: one clean A+ trade hits it
# At 1%: switch to PROTECTION (A-grade only), at 1.5%: LOCK (A+ only, 60% size), at 2%: STOP

MONTHLY_TARGET_PCT: float = float(os.getenv("MONTHLY_TARGET_PCT", "20.0"))
# 1.0%/day × 22 trading days = ~22% monthly (realistic top-decile retail)

# ============================================================
# OPTIONS SCALPING — Alpaca Markets
# ============================================================
OPTIONS_ENABLED:         bool  = os.getenv("OPTIONS_ENABLED", "False").lower() in ("true", "1", "yes")
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
# CRYPTO TRADING (BTC/ETH/SOL via Alpaca — 24/7, no PDT)
# ============================================================
# ============================================================
# TOP-1% EQUITY EDGE MODULES
# ============================================================
# Stock vs its sector ETF (XLK, XLF, XLY, XLE, XLV, XLI...)
# Leaders get +15 pts, laggards get -15 pts — institutional money flows
SECTOR_RS_ENABLED: bool = os.getenv("SECTOR_RS_ENABLED", "True").lower() in ("true", "1", "yes")

# Short squeeze detector — boosts LONG on high-short-float stocks, penalizes shorting into squeeze
# GME/AMC/MARA type moves come from here. Score boost: +15 pts max
SQUEEZE_SCANNER_ENABLED: bool = os.getenv("SQUEEZE_SCANNER_ENABLED", "True").lower() in ("true", "1", "yes")

# Post-Earnings Announcement Drift (PEAD) — 80% accuracy, 3-21 days post-earnings
# Beat → drift up → boost LONG. Miss → drift down → boost SHORT.
PEAD_SCORER_ENABLED: bool = os.getenv("PEAD_SCORER_ENABLED", "True").lower() in ("true", "1", "yes")

# Pre-market ES/NQ futures bias — 73% predictive accuracy for opening direction
# Against bias = -10 pts. Aligned = +10 pts + size multiplier
FUTURES_BIAS_ENABLED: bool = os.getenv("FUTURES_BIAS_ENABLED", "True").lower() in ("true", "1", "yes")

# ============================================================
# CRYPTO TRADING (BTC/ETH/SOL via Alpaca — 24/7, no PDT)
# ============================================================
CRYPTO_ENABLED: bool = os.getenv("CRYPTO_ENABLED", "True").lower() in ("true", "1", "yes")
# Capital split: 30% to crypto pool, 70% stays in stocks.
# Crypto runs 24/7 as a separate background engine alongside stock trading.
# No PDT rule, fractional orders, 3x more volatile = accelerates compounding.
CRYPTO_CAPITAL_PCT_OF_TOTAL: float = float(os.getenv("CRYPTO_CAPITAL_PCT_OF_TOTAL", "30.0"))
CRYPTO_CAPITAL_USD: float = float(os.getenv("CRYPTO_CAPITAL_USD", "0"))
CRYPTO_SHORT_ENABLED: bool = os.getenv("CRYPTO_SHORT_ENABLED", "True").lower() in ("true", "1", "yes")

# ============================================================
# 1% DAILY TARGET DISCIPLINE (Professional Trading Rules)
# ============================================================
# When 1% daily target is hit:
#   True  = stop all new entries, protect the profit (recommended)
#   False = continue trading A+ signals at 60% size
STOP_NEW_ENTRIES_AFTER_TARGET: bool = os.getenv("STOP_NEW_ENTRIES_AFTER_TARGET", "False").lower() in ("true","1","yes")

# Post-target mode: min score required for new entries after 1% is hit
# 84.0 = A+ only (very selective — only the best of the best)
POST_TARGET_MIN_SCORE: float = float(os.getenv("POST_TARGET_MIN_SCORE", "84.0"))

# Time-based cutoff: never enter new trades after this ET hour
# (Even when target not yet hit — avoids noisy last-30-min reversals)
EOD_NO_ENTRY_ET_HOUR: int = 15   # 3:00 PM ET — existing NO_ENTRY_AFTER_ET_HOUR
EOD_NO_ENTRY_ET_MINUTE: int = 0

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
    if LIVE_TRADING_ENABLED and os.getenv("ALPACA_PAPER", "true").lower() != "false":
        issues.append(
            "CRITICAL: LIVE_TRADING_ENABLED=True but ALPACA_PAPER is not 'false'. "
            "Auth client will route to paper endpoint. Add ALPACA_PAPER=false to .env."
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
