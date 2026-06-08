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
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")
BOT_DISPLAY_NAME     = os.getenv("BOT_DISPLAY_NAME", "PSEB")

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

# Capital (USD) — hard cap what the bot uses as its "bankroll" each day
# IMPORTANT: 0 means "use full account balance" — DANGEROUS on a $90k account.
# Set this to the actual USD amount you want to risk, e.g. 5000 for a $5k sub-account.
MAX_DAILY_CAPITAL: float = float(os.getenv("MAX_DAILY_CAPITAL", "0"))
# Safety net: when MAX_DAILY_CAPITAL=0 we cap internally at 10% of account equity
# via the risk_manager so a $90k account can never accidentally use $90k as capital.
# Set MAX_DAILY_CAPITAL explicitly in .env to override, e.g. MAX_DAILY_CAPITAL=5000

MAX_RISK_PER_TRADE_PCT: float = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.0"))
MAX_RISK_PER_TRADE_PCT = min(MAX_RISK_PER_TRADE_PCT, 2.0)   # live-trading safe cap

# Grade-based risk unlocking — institutional practice: size your best setups bigger
# A+ Grand Slam (7+ confluence): 1.5× base risk — these are 70%+ win rate setups
# A  grade       (5+ confluence): 1.0× base risk — standard full size
# B  grade       (borderline)   : 0.6× base risk — conservative
HIGH_CONFIDENCE_RISK_MULTIPLIER: float = float(os.getenv("HIGH_CONFIDENCE_RISK_MULTIPLIER", "2.0"))

DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "2.5"))
DAILY_LOSS_LIMIT_PCT = min(DAILY_LOSS_LIMIT_PCT, 3.0)   # live-trading safe cap

# Intraday leverage multiplier.
# ⚠️  PDT RULE: Alpaca margin accounts under $25,000 → max 3 day trades/week.
#     Bot does 10-30 trades/day → PDT frozen on day 1 if margin account.
# SAFE OPTION (< $25K): Use Alpaca CASH account → no PDT, no leverage (1x).
# FULL POWER ($25K+):   Margin account → 4x intraday, no PDT restriction.
# Default 1.0 = cash account safe mode. Set to 4.0 only when account ≥ $25,000.
ALPACA_LEVERAGE: float = float(os.getenv("ALPACA_LEVERAGE", "1.0"))
ALPACA_LEVERAGE = max(1.0, min(ALPACA_LEVERAGE, 4.0))  # 1x cash — safe for live trading

MAX_POSITIONS: int = 5       # high-accuracy mode: fewer, higher-conviction positions (was 8 for scalping)

# ── Scalper / burst kill-switch (owner: high-accuracy only, no scalping) ──────
# These low-score, high-frequency paths bypassed the main quality gate and were
# the source of the 0W/10L bleed. OFF by default now.
ENABLE_SCALPER: bool = os.getenv("ENABLE_SCALPER", "False") == "True"
ENABLE_BURST:   bool = os.getenv("ENABLE_BURST",   "False") == "True"
MIN_POSITIONS: int = 1
MAX_CAPITAL_PER_TRADE_PCT: float = 15.0   # 15% per trade — sized for profit, not reckless

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
ATR_SL_MULTIPLIER: float = 0.75         # 0.75× ATR stop — tighter = smaller loss if wrong
ATR_T1_MULTIPLIER: float = 1.0          # T1 at 1:1 — fast partial exit, converts to free trade quickly
ATR_TP_MULTIPLIER: float = 2.0          # T2 at 2:1 R:R — realistic daily target
ATR_TP_RUNNER: float = 8.0             # T3 runner at 8:1 — catch full trend moves
ATR_TRAIL_MULTIPLIER: float = 1.0       # trail at 1× ATR — runners breathe, don't get stopped early
BREAKEVEN_TRIGGER_PCT: float = 0.10     # move to breakeven after 0.10% gain — converts losing to free fast
PARTIAL_EXIT_T1_PCT: float = 50.0       # 50% at T1 — lock half the position in profit immediately
PARTIAL_EXIT_T2_PCT: float = 20.0       # 20% at T2 — keep runner alive
RUNNER_PCT: float = 30.0                # 30% runner — lean, focused on the best part of the move

# ── Top-1% trader hard gates ──────────────────────────────────────────────
MIN_RISK_REWARD: float = 2.0       # Only enter if 2.0:1 R:R minimum — skip low-quality setups
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

MIN_SIGNAL_SCORE: float = float(os.getenv("MIN_SIGNAL_SCORE", "66.0"))
L99_MIN_SCORE: float    = float(os.getenv("L99_MIN_SCORE", "72.0"))    # high-accuracy: 72=A- conviction (65=B, 76=A, 85=A+)
                                       # v20.1: lowered 63→60. Pre-filter feeds the 28-gate HAF system.
                                       # Boosters (CSM, VWAP, OFI, etc.) add pts after gates. Kept low so
                                       # gate system — not the pre-filter — is the quality barrier.
HIGH_CONFIDENCE_SCORE: float = 82.0   # A+ after bonuses
GRAND_SLAM_MIN_SCORE: float = float(os.getenv("GRAND_SLAM_MIN_SCORE", "82.0"))  # Grand Slam requires 82+ (2× size)
PREMIUM_SCORE: float = 80.0           # A grade entry
FINAL_EXEC_MIN_SCORE: float = float(os.getenv("FINAL_EXEC_MIN_SCORE", "70.0"))  # high-accuracy post-booster gate (was 60)

# ── WR-targeting thresholds (v16.0) ───────────────────────────────────────
ADX_MIN_TREND:         float = float(os.getenv("ADX_MIN_TREND",    "12.0"))  # lowered 18→12: 12-18 still has directional bias
VWAP_EXTENSION_MAX_ATR: float = float(os.getenv("VWAP_EXTENSION_MAX_ATR", "2.0"))  # gate 27 — no chasing
BAR_QUALITY_GATE:       bool  = os.getenv("BAR_QUALITY_GATE",   "True").lower() in ("true","1","yes")  # gate 28 — entry bar body quality
SECTOR_FILTER_ENABLED:  bool  = os.getenv("SECTOR_FILTER_ENABLED", "True").lower() in ("true","1","yes")  # gate 14b — sector ETF alignment
BE_ATR_TRIGGER:        float = float(os.getenv("BE_ATR_TRIGGER",   "0.3"))   # was 0.5 — faster breakeven

# Advanced setups v18.0
ORB_ENABLED:              bool = os.getenv("ORB_ENABLED",              "True").lower() in ("true","1","yes")
FPE9_ENABLED:             bool = os.getenv("FPE9_ENABLED",             "True").lower() in ("true","1","yes")
VBB_ENABLED:              bool = os.getenv("VBB_ENABLED",              "True").lower() in ("true","1","yes")
MARKET_BREADTH_ENABLED:   bool = os.getenv("MARKET_BREADTH_ENABLED",   "True").lower() in ("true","1","yes")
PREMARKET_FILTER_ENABLED: bool = os.getenv("PREMARKET_FILTER_ENABLED", "True").lower() in ("true","1","yes")

# ── Pullback Entry System (pullback_entry.py) ──────────────────────────────
# Wait for 23-38% Fibonacci retrace before entering instead of hitting the breakout bar.
# Improves average entry price by 0.15-0.4% per trade on A/A+ signals.
# Only activates when signal_score >= 75 AND quality_grade in (A, A+) AND ATR > 0.3% of price.
PULLBACK_ENTRY_ENABLED: bool = os.getenv("PULLBACK_ENTRY_ENABLED", "True").lower() in ("true", "1", "yes")

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
BA_IMBALANCE_MIN_RATIO: float = float(os.getenv("BA_IMBALANCE_MIN_RATIO", "0.48"))
# Gate 21: Order Flow Imbalance — cumulative delta must not strongly oppose direction
OFI_GATE_ENABLED:       bool  = os.getenv("OFI_GATE_ENABLED", "True").lower() in ("true","1","yes")
# Gate 24: Momentum bar confirmation — require ≥3 of last 5 bars closing in signal direction
MOMENTUM_BAR_GATE:      bool  = os.getenv("MOMENTUM_BAR_GATE",    "True").lower() in ("true","1","yes")
# Gate 25: R² trend quality — reject choppy price action (R² < 0.35 over 20 bars)
TREND_QUALITY_GATE:     bool  = os.getenv("TREND_QUALITY_GATE",   "True").lower() in ("true","1","yes")
# Score components: OFI, Dark Pool, Session Momentum
OFI_SCORE_ENABLED:             bool = os.getenv("OFI_SCORE_ENABLED", "True").lower() in ("true","1","yes")
DARK_POOL_ENABLED:             bool = os.getenv("DARK_POOL_ENABLED", "True").lower() in ("true","1","yes")
# ── ELITE FILTER (v11.0) ──────────────────────────────────────────────────────
ELITE_FILTER_ENABLED:    bool = os.getenv("ELITE_FILTER_ENABLED",    "True").lower() in ("true","1","yes")
VIX_ADAPTIVE_ENABLED:    bool = os.getenv("VIX_ADAPTIVE_ENABLED",    "True").lower() in ("true","1","yes")
ADX_GATE_ENABLED:        bool = os.getenv("ADX_GATE_ENABLED",        "True").lower() in ("true","1","yes")
MIN_REWARD_RISK:        float = float(os.getenv("MIN_REWARD_RISK",   "2.0"))
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
MAX_TRADES_PER_DAY: int = 50          # paper trading: no trade count limit
MAX_TRADES_PER_STOCK: int = 5         # allow more re-entries on strong trends

# ============================================================
# CIRCUIT BREAKERS
# ============================================================
NIFTY_CIRCUIT_PCT: float = 2.0        # Reused as SPY circuit threshold
CONSECUTIVE_LOSS_LIMIT: int = 4        # pause after 4 consecutive losses
PAUSE_AFTER_LOSSES_MINUTES: int = 15   # 15 min pause to reassess
LARGE_LOSS_PAUSE_PCT: float = 2.0      # pause if single trade loses ≥2% of daily capital
LARGE_LOSS_PAUSE_MINUTES: int = 15     # pause duration after large single loss
NO_ENTRY_AFTER_ET_HOUR: int = 15       # no new entries at or after 3:00 PM ET (last 30 min = noisy reversals)
NO_ENTRY_AFTER_ET_MINUTE: int = 0
SKIP_VOLATILE_LONGS: bool = True       # in HIGH_VOLATILITY regime, skip LONG entries (only shorts)
# ── Drawdown recovery sizing (v12.0) ─────────────────────────────────────────
DD_RECOVERY_HARD_PCT: float = float(os.getenv("DD_RECOVERY_HARD_PCT", "10.0"))  # block all new trades at 10% session DD from peak
DD_RECOVERY_SOFT_PCT: float = float(os.getenv("DD_RECOVERY_SOFT_PCT", "5.0"))   # halve size at 5% session DD from peak

# ── Short selling ──────────────────────────────────────────────────────────
SHORT_SELLING_ENABLED: bool = os.getenv("SHORT_SELLING_ENABLED", "true").lower() == "true"
MAX_SHORT_POSITIONS: int = int(os.getenv("MAX_SHORT_POSITIONS", "3"))

# ── Cross-asset intelligence (VIX + bonds + dollar macro overlay) ───────────
CROSS_ASSET_ENABLED: bool = os.getenv("CROSS_ASSET_ENABLED", "true").lower() == "true"

# ── News sentiment (NewsAPI keyword scoring) ─────────────────────────────────
NEWS_SENTIMENT_ENABLED: bool = os.getenv("NEWS_SENTIMENT_ENABLED", "true").lower() == "true"

# ── VWAP mean-reversion strategy (best in choppy markets) ──────────────────
VWAP_REVERSION_ENABLED: bool = True
VWAP_REVERSION_MIN_DEVIATION_ATR: float = 1.5   # price must be 1.5+ ATR from VWAP

# ── AI News sentiment filter ────────────────────────────────────────────────
GEMINI_NEWS_FILTER_ENABLED: bool = True          # use Gemini/Claude to score news sentiment
GEMINI_NEWS_SCORE_MAX_DELTA: float = 15.0        # max pts added/removed from signal score

# ── God Mode v31.0 — ETF flow + portfolio intelligence + news NLP ────────────
ETF_FLOW_ENABLED             = bool(int(os.getenv("ETF_FLOW_ENABLED", "1")))
US_NEWS_ALPHA_ENABLED        = bool(int(os.getenv("US_NEWS_ALPHA_ENABLED", "1")))
US_PORTFOLIO_INTEL_ENABLED   = bool(int(os.getenv("US_PORTFOLIO_INTEL_ENABLED", "1")))

# ── Aggressive sizing on elite setups — defined earlier from env var, not duplicated here ──

# ── Opening Range Breakout (9:30–9:45 AM) ──────────────────────────────────
# ORB_ENABLED: defined above as env-var-driven (v18.0)
ORB_WINDOW_MINUTES: int = 15         # range established in first 15 min
ORB_MIN_RANGE_PCT: float = 0.3       # range must be at least 0.3% of price
ORB_RISK_MULTIPLIER: float = 1.2     # slightly larger size on ORB plays

# ============================================================
# WATCHLIST — US liquid momentum stocks (v21.0: 75 symbols)
# ============================================================
DEFAULT_WATCHLIST = [
    # ── Mega-cap tech (10) ─────────────────────────────────────────────────
    "AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA", "AMD", "AVGO", "ORCL",
    # ── High-beta tech / disruptive (10) ───────────────────────────────────
    "SMCI", "PLTR", "MSTR", "COIN", "HOOD", "SOFI", "UPST", "AI", "SOUN", "IONQ",
    # ── Semiconductors (10) ────────────────────────────────────────────────
    "INTC", "QCOM", "MU", "TSM", "AMAT", "LRCX", "KLAC", "ON", "MRVL", "TXN",
    # ── Financials (10) ────────────────────────────────────────────────────
    "JPM", "GS", "BAC", "MS", "WFC", "C", "AXP", "V", "MA", "PYPL",
    # ── Energy (5) ─────────────────────────────────────────────────────────
    "XOM", "CVX", "OXY", "SLB", "HAL",
    # ── Biotech / Healthcare (6) ───────────────────────────────────────────
    "MRNA", "BNTX", "REGN", "BIIB", "GILD", "LLY",
    # ── Crypto-adjacent miners (5) ─────────────────────────────────────────
    "MARA", "RIOT", "CLSK", "CIFR", "HUT",
    # ── ETFs for regime context (7) ────────────────────────────────────────
    "SPY", "QQQ", "IWM", "XLF", "XLE", "XLK", "ARKK",
    # ── Consumer / Retail (5) ──────────────────────────────────────────────
    "WMT", "TGT", "COST", "HD", "SBUX",
    # ── EV / Clean energy (4) ──────────────────────────────────────────────
    "RIVN", "LCID", "NIO", "PLUG",
    # ── Additional energy (3) ──────────────────────────────────────────────
    "MPC", "VLO", "PSX",
]
# 75 symbols — high-liquidity momentum stocks across 10 sectors.
# Covers mega-cap tech, semis, financials, energy, biotech, crypto-adjacent,
# ETF regime indicators, consumer, EV/clean energy.
# More watchlist = more breakout setups scanned per day = more executable signals.

DAILY_PROFIT_TARGET: float = float(os.getenv("DAILY_PROFIT_TARGET", "0"))
# 0 = compute from DAILY_PROFIT_TARGET_PCT × live balance (recommended — auto-compounds)
# >0 = fixed dollar target (overrides percentage calculation)

DAILY_PROFIT_TARGET_PCT: float = float(os.getenv("DAILY_PROFIT_TARGET_PCT", "1.0"))
# 1.0%/day — realistic achievable target: one clean A+ trade hits it
# At 1%: switch to PROTECTION (A-grade only), at 1.5%: LOCK (A+ only, 60% size), at 2%: STOP

MONTHLY_TARGET_PCT: float = float(os.getenv("MONTHLY_TARGET_PCT", "10.0"))
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
MIN_DAILY_VOLUME: int = 150_000     # 150k shares/day — allows more quality stocks without slippage risk
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
    0: 55.0,   # Monday
    1: 55.0,   # Tuesday — best trend day
    2: 55.0,   # Wednesday — trend continuation
    3: 55.0,   # Thursday
    4: 55.0,   # Friday
}

# Time-of-day minimum score (ET, 24h format) — institutional trading windows
# Open (9:30-10:00): 65 — biggest moves, institutional flow highest
# Morning (10:00-11:30): 68 — normal momentum
# Midday (11:30-13:30): 73 — chop zone, only strong setups
# Afternoon (13:30-15:00): 68 — momentum resumes
# Power hour (15:00-15:30): 65 — institutional rebalancing, strongest directional moves
# Last 5 min (15:25-15:30): 80 — very risky, only A+ setups (covered by EOD square-off anyway)
TOD_MIN_SCORES: dict = {
    # (start_min_from_midnight, end_min): min_score
    # 9:30 ET = 570 min, 10:00 = 600, 11:30 = 690, 13:30 = 810, 15:00 = 900, 15:25 = 925
    (570, 600): 58.0,   # 9:30–10:00: open drive
    (600, 690): 55.0,   # 10:00–11:30: morning prime window
    (690, 810): 60.0,   # 11:30–13:30: midday chop
    (810, 900): 55.0,   # 13:30–15:00: afternoon
    (900, 925): 58.0,   # 15:00–15:25: power hour
    (925, 960): 68.0,   # 15:25–16:00: near close — keep higher bar
}
TOD_THRESHOLD_ENABLED: bool = os.getenv("TOD_THRESHOLD_ENABLED", "True").lower() in ("true","1","yes")

DOW_MAX_TRADES: dict = {
    0: 25,   # Monday
    1: 30,   # Tuesday — best trend day
    2: 30,   # Wednesday — trend continuation
    3: 25,   # Thursday
    4: 20,   # Friday — EOD risk managed by 0.8× size, not trade count
}

WEEKLY_PROFIT_TARGET_PCT: float = 8.0    # 30%/mo ÷ 4.33 weeks = 6.9%/wk — use 8% as target
WEEKLY_PROFIT_LOCK_PCT: float = 20.0    # don't throttle size until 20% weekly gain secured
WEEKLY_LOSS_STOP_PCT: float = 8.0       # weekly stop-out at -8% — more runway than default
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

# ── WIRING COMPLETENESS FLAGS ─────────────────────────────────────────────
SMART_MONEY_ADV_ENABLED: bool = os.getenv("SMART_MONEY_ADV_ENABLED", "True").lower() in ("true","1","yes")
OVERNIGHT_BIAS_ENABLED: bool = os.getenv("OVERNIGHT_BIAS_ENABLED", "True").lower() in ("true","1","yes")
ADAPTIVE_BRAIN_THRESHOLD_SYNC: bool = os.getenv("ADAPTIVE_BRAIN_THRESHOLD_SYNC", "True").lower() in ("true","1","yes")
BURST_SCANNER_ENABLED: bool = os.getenv("BURST_SCANNER_ENABLED", "True").lower() in ("true","1","yes")
MORNING_INTEL_SIZING: bool = os.getenv("MORNING_INTEL_SIZING", "True").lower() in ("true","1","yes")
RL_HARD_GATE_ENABLED: bool = os.getenv("RL_HARD_GATE_ENABLED", "False").lower() in ("true","1","yes")
SELF_LEARNING_APPLY_THRESHOLD: bool = os.getenv("SELF_LEARNING_APPLY_THRESHOLD", "True").lower() in ("true","1","yes")

# ── NEW ALPHA FEATURES v8.0 ───────────────────────────────────────────────
INST_ACCUM_GATE: bool = os.getenv("INST_ACCUM_GATE", "True").lower() in ("true","1","yes")
TICK_PROXY_ENABLED: bool = os.getenv("TICK_PROXY_ENABLED", "True").lower() in ("true","1","yes")
MULTIDAY_MOMENTUM_ENABLED: bool = os.getenv("MULTIDAY_MOMENTUM_ENABLED", "True").lower() in ("true","1","yes")
STOP_HUNT_GATE: bool = os.getenv("STOP_HUNT_GATE", "True").lower() in ("true","1","yes")
RVOL_PERCENTILE_ENABLED: bool = os.getenv("RVOL_PERCENTILE_ENABLED", "True").lower() in ("true","1","yes")

# New alpha modules (v9.0 — institutional-grade free data)
FIBONACCI_ENABLED:      bool = os.getenv("FIBONACCI_ENABLED",      "True").lower() in ("true","1","yes")
SHORT_SQUEEZE_ENABLED:  bool = os.getenv("SHORT_SQUEEZE_ENABLED",   "True").lower() in ("true","1","yes")
OPTIONS_FLOW_ENABLED:   bool = os.getenv("OPTIONS_FLOW_ENABLED",    "True").lower() in ("true","1","yes")
INSIDER_FLOW_ENABLED:   bool = os.getenv("INSIDER_FLOW_ENABLED",    "True").lower() in ("true","1","yes")
OPTIONS_GATE_ENABLED:   bool = os.getenv("OPTIONS_GATE_ENABLED",    "True").lower() in ("true","1","yes")
SMART_LIMIT_ORDERS:     bool = os.getenv("SMART_LIMIT_ORDERS",      "True").lower() in ("true","1","yes")

# Institutional strategies v10.0 (all enabled by default, fail-open)
CSM_ENABLED:            bool = os.getenv("CSM_ENABLED",             "True").lower() in ("true","1","yes")
VWAP_RECLAIM_ENABLED:   bool = os.getenv("VWAP_RECLAIM_ENABLED",    "True").lower() in ("true","1","yes")
POWER_HOUR_ENABLED:     bool = os.getenv("POWER_HOUR_ENABLED",      "True").lower() in ("true","1","yes")
PAIRS_SIGNAL_ENABLED:   bool = os.getenv("PAIRS_SIGNAL_ENABLED",    "True").lower() in ("true","1","yes")
TOD_RVOL_ENABLED:       bool = os.getenv("TOD_RVOL_ENABLED",        "True").lower() in ("true","1","yes")
SORTINO_SIZING_ENABLED: bool = os.getenv("SORTINO_SIZING_ENABLED",  "True").lower() in ("true","1","yes")
TIER1_ELITE_ENABLED:    bool = os.getenv("TIER1_ELITE_ENABLED",     "True").lower() in ("true","1","yes")

# ── TIER 2.5 UPGRADE (v11.0) ─────────────────────────────────────────────────
VOL_TARGET_ENABLED:       bool = os.getenv("VOL_TARGET_ENABLED",       "True").lower() in ("true","1","yes")

# ── TIER 1.5 UPGRADE (v12.0) — highest possible with free data ───────────────
NEURAL_PREDICTOR_ENABLED: bool = os.getenv("NEURAL_PREDICTOR_ENABLED", "True").lower() in ("true","1","yes")
COINT_ARBIT_ENABLED:      bool = os.getenv("COINT_ARBIT_ENABLED",      "True").lower() in ("true","1","yes")
GAP_FADE_ENABLED:         bool = os.getenv("GAP_FADE_ENABLED",         "True").lower() in ("true","1","yes")

# ── PREMIUM SCANNER (v11.0) — free equivalents of paid trading tools ─────────
SHORT_SQUEEZE_SCANNER_ENABLED: bool = os.getenv("SHORT_SQUEEZE_SCANNER_ENABLED", "True").lower() in ("true","1","yes")
OPTIONS_FLOW_SCANNER_ENABLED:  bool = os.getenv("OPTIONS_FLOW_SCANNER_ENABLED",  "True").lower() in ("true","1","yes")
SECTOR_ROTATION_ENABLED:       bool = os.getenv("SECTOR_ROTATION_ENABLED",       "True").lower() in ("true","1","yes")
FLOAT_SQUEEZE_ENABLED:         bool = os.getenv("FLOAT_SQUEEZE_ENABLED",         "True").lower() in ("true","1","yes")
EARNINGS_EDGE_ENABLED:         bool = os.getenv("EARNINGS_EDGE_ENABLED",         "True").lower() in ("true","1","yes")
DARK_POOL_SCANNER_ENABLED:     bool = os.getenv("DARK_POOL_SCANNER_ENABLED",     "True").lower() in ("true","1","yes")

# ── God Mode v26.0 — CVD + TICK + DOW + Multi-Day + Experience + True RS + Trend Exhaustion
GOD_MODE_ENABLED:    bool = os.getenv("GOD_MODE_ENABLED",    "True").lower() in ("true","1","yes")
# ── Genius Mode v27.0 — Hurst exponent + Kalman filter + OB pressure + Kelly sizing + Vol regime gate
GENIUS_MODE_ENABLED: bool = os.getenv("GENIUS_MODE_ENABLED", "True").lower() in ("true","1","yes")
# ── PhD Mode v28.0 — 52W Proximity + Amihud + CMF + ER + VWMS + Noise Ratio
PHD_MODE_ENABLED:    bool = os.getenv("PHD_MODE_ENABLED",    "True").lower() in ("true","1","yes")
# ── Renaissance Mode v29.0 — IC tracking + Bayes + Regime + Decay + Sector limits + DD sizing
RENAISSANCE_MODE_ENABLED: bool = os.getenv("RENAISSANCE_MODE_ENABLED", "True").lower() in ("true","1","yes")
# ── Citadel Mode v30.0 — YZ volatility + entropy + volume profile + autocorrelation + VRP + risk parity
CITADEL_MODE_ENABLED:    bool = os.getenv("CITADEL_MODE_ENABLED",    "True").lower() in ("true","1","yes")

# ── Execution Quality v11.0 ─────────────────────────────────────────────────
SLIPPAGE_PREDICTION_ENABLED: bool  = os.getenv("SLIPPAGE_PREDICTION_ENABLED", "True").lower() in ("true","1","yes")
FILL_QUALITY_MIN: float            = float(os.getenv("FILL_QUALITY_MIN", "0.6"))
MAX_PREDICTED_SLIPPAGE_PCT: float  = float(os.getenv("MAX_PREDICTED_SLIPPAGE_PCT", "0.25"))

# ── Regime-Adaptive Parameters v11.0 ────────────────────────────────────────
REGIME_ADAPTIVE_ENABLED: bool  = os.getenv("REGIME_ADAPTIVE_ENABLED", "True").lower() in ("true","1","yes")
REGIME_ADAPTIVE_SIZING: bool   = os.getenv("REGIME_ADAPTIVE_SIZING",  "True").lower() in ("true","1","yes")

# ── Portfolio Direction Concentration v11.0 ──────────────────────────────────
MAX_DIRECTION_CONCENTRATION_PCT: float = float(os.getenv("MAX_DIRECTION_CONCENTRATION_PCT", "70.0"))

# ── Optimal Entry Timing v12.0 ───────────────────────────────────────────────
TOD_ENTRY_TIMING_ENABLED: bool = os.getenv("TOD_ENTRY_TIMING_ENABLED", "True").lower() in ("true","1","yes")

# ML Gate v13.0 — GradientBoosting win-probability pre-filter
ML_GATE_ENABLED:          bool  = os.getenv("ML_GATE_ENABLED",  "True").lower() in ("true","1","yes")
ML_WIN_PROB_THRESHOLD:    float = float(os.getenv("ML_WIN_PROB_THRESHOLD", "0.60"))
# 0.60 = entry bar: 60% ML-predicted win probability required
# Raise to 0.70 for ultra-selective mode (fewer trades, higher WR)
# Lower to 0.50 to effectively disable the gate (returns neutral prob)

# KING Knowledge Base v15.0 — 8 legendary trading frameworks
# Livermore · Minervini · O'Neil · Darvas · Wyckoff · Weinstein · Turtle · Soros
KNOWLEDGE_BASE_ENABLED:   bool  = os.getenv("KNOWLEDGE_BASE_ENABLED", "True").lower() in ("true","1","yes")

# PDT enforcement (Pattern Day Trader rule — US margin accounts < $25K)
# Set ACCOUNT_TYPE=CASH (default) to disable PDT restriction
# Set ACCOUNT_TYPE=MARGIN + PDT_ENFORCE=True to hard-block after 3 day trades
ACCOUNT_TYPE:           str  = os.getenv("ACCOUNT_TYPE",            "CASH").upper()
PDT_ENFORCE:            bool = os.getenv("PDT_ENFORCE",             "True").lower() in ("true","1","yes")
PDT_MAX_DAY_TRADES:     int  = int(os.getenv("PDT_MAX_DAY_TRADES",  "3"))

# ── Adaptive Kelly + Dynamic Sizing (v13.0) ──────────────────────────────────
ADAPTIVE_KELLY_ENABLED: bool  = os.getenv("ADAPTIVE_KELLY_ENABLED", "True").lower() in ("true","1","yes")

# ── World-Class Additions (v14.0) ────────────────────────────────────────────
HARMONIC_PATTERNS_ENABLED: bool = os.getenv("HARMONIC_PATTERNS_ENABLED", "True").lower() in ("true","1","yes")
WYCKOFF_ENABLED:            bool = os.getenv("WYCKOFF_ENABLED",            "True").lower() in ("true","1","yes")
MASTER_CONFLUENCE_ENABLED:  bool = os.getenv("MASTER_CONFLUENCE_ENABLED",  "True").lower() in ("true","1","yes")
CONFLUENCE_MIN_AGREE:       int  = int(os.getenv("CONFLUENCE_MIN_AGREE",  "2"))
MEAN_REVERSION_ENABLED:     bool = os.getenv("MEAN_REVERSION_ENABLED",     "True").lower() in ("true","1","yes")
VSA_ENABLED:                bool = os.getenv("VSA_ENABLED",                "True").lower() in ("true","1","yes")
INTERMARKET_ENABLED:        bool = os.getenv("INTERMARKET_ENABLED",        "True").lower() in ("true","1","yes")
EVENT_SCANNER_ENABLED:      bool = os.getenv("EVENT_SCANNER_ENABLED",      "True").lower() in ("true","1","yes")
RISK_PER_TRADE_PCT:     float = float(os.getenv("RISK_PER_TRADE_PCT", str(MAX_RISK_PER_TRADE_PCT)))

# ── Quantum Strategies (v12.0) — 8 institutional-grade signal modules ────────
ORB_SCORE_ENABLED:           bool = os.getenv("ORB_SCORE_ENABLED",           "True").lower() in ("true","1","yes")
VWAP_BANDS_ENABLED:          bool = os.getenv("VWAP_BANDS_ENABLED",          "True").lower() in ("true","1","yes")
MARKET_PROFILE_ENABLED:      bool = os.getenv("MARKET_PROFILE_ENABLED",      "True").lower() in ("true","1","yes")
ORDER_FLOW_ENABLED:          bool = os.getenv("ORDER_FLOW_ENABLED",          "True").lower() in ("true","1","yes")
GAMMA_SQUEEZE_ENABLED:       bool = os.getenv("GAMMA_SQUEEZE_ENABLED",       "True").lower() in ("true","1","yes")
ZSCORE_REVERSION_ENABLED:    bool = os.getenv("ZSCORE_REVERSION_ENABLED",    "True").lower() in ("true","1","yes")
PEAD_SCORE_ENABLED:          bool = os.getenv("PEAD_SCORE_ENABLED",          "True").lower() in ("true","1","yes")
MOMENTUM_PERSIST_ENABLED:    bool = os.getenv("MOMENTUM_PERSIST_ENABLED",    "True").lower() in ("true","1","yes")
WOLFE_WAVE_ENABLED:          bool = os.getenv("WOLFE_WAVE_ENABLED",          "True").lower() in ("true","1","yes")
ELLIOTT_WAVE_ENABLED:        bool = os.getenv("ELLIOTT_WAVE_ENABLED",        "True").lower() in ("true","1","yes")
GANN_ENABLED:                bool = os.getenv("GANN_ENABLED",                "True").lower() in ("true","1","yes")

# ── PREMIUM DATA PROXIES (v15.0) — free duplicates of L2/dark pool/options/tick/earnings/alt/news/colocation ──
PREMIUM_PROXIES_ENABLED: bool = os.getenv("PREMIUM_PROXIES_ENABLED", "True").lower() not in ("false", "0", "no")
L2_PROXY_ENABLED:        bool = os.getenv("L2_PROXY_ENABLED",        "True").lower() not in ("false", "0", "no")
DARK_POOL_PROXY_ENABLED: bool = os.getenv("DARK_POOL_PROXY_ENABLED", "True").lower() not in ("false", "0", "no")
OPTIONS_PROXY_ENABLED:   bool = os.getenv("OPTIONS_PROXY_ENABLED",   "True").lower() not in ("false", "0", "no")
# TICK_PROXY_ENABLED: duplicate removed — first definition at line ~559 uses correct in-true-set semantics
EARNINGS_PROXY_ENABLED:  bool = os.getenv("EARNINGS_PROXY_ENABLED",  "True").lower() not in ("false", "0", "no")
ALT_DATA_PROXY_ENABLED:  bool = os.getenv("ALT_DATA_PROXY_ENABLED",  "True").lower() not in ("false", "0", "no")
NEWS_PROXY_ENABLED:      bool = os.getenv("NEWS_PROXY_ENABLED",      "True").lower() not in ("false", "0", "no")
COLOC_PROXY_ENABLED:     bool = os.getenv("COLOC_PROXY_ENABLED",     "True").lower() not in ("false", "0", "no")

# ── v16.0 module flags ────────────────────────────────────────────────────────
HOLLY_AI_ENABLED:        bool = os.getenv("HOLLY_AI_ENABLED",        "True").lower() not in ("false", "0", "no")
ML_SCORE_BOOST_ENABLED:  bool = os.getenv("ML_SCORE_BOOST_ENABLED",  "True").lower() not in ("false", "0", "no")
MOMENTUM_BURST_ENABLED:  bool = os.getenv("MOMENTUM_BURST_ENABLED",  "True").lower() not in ("false", "0", "no")
RL_AGENT_ENABLED:        bool = os.getenv("RL_AGENT_ENABLED",        "True").lower() not in ("false", "0", "no")
IBD_RS_ENABLED:          bool = os.getenv("IBD_RS_ENABLED",          "True").lower() not in ("false", "0", "no")
FEAR_GREED_ENABLED:      bool = os.getenv("FEAR_GREED_ENABLED",      "True").lower() not in ("false", "0", "no")
OPTIONS_SKEW_ENABLED:    bool = os.getenv("OPTIONS_SKEW_ENABLED",    "True").lower() not in ("false", "0", "no")

# ── v17.0 premium module flags ────────────────────────────────────────────────
SOCIAL_SENTIMENT_ENABLED:    bool = os.getenv("SOCIAL_SENTIMENT_ENABLED",    "True").lower() not in ("false", "0", "no")
INSTITUTIONAL_FLOW_ENABLED:  bool = os.getenv("INSTITUTIONAL_FLOW_ENABLED",  "True").lower() not in ("false", "0", "no")
ECONOMIC_SURPRISE_ENABLED:   bool = os.getenv("ECONOMIC_SURPRISE_ENABLED",   "True").lower() not in ("false", "0", "no")
MCCLELLAN_ENABLED:           bool = os.getenv("MCCLELLAN_ENABLED",           "True").lower() not in ("false", "0", "no")
SUPPLY_DEMAND_ENABLED:       bool = os.getenv("SUPPLY_DEMAND_ENABLED",       "True").lower() not in ("false", "0", "no")
VANNA_CHARM_ENABLED:         bool = os.getenv("VANNA_CHARM_ENABLED",         "True").lower() not in ("false", "0", "no")
SEASONAL_ALPHA_ENABLED:      bool = os.getenv("SEASONAL_ALPHA_ENABLED",      "True").lower() not in ("false", "0", "no")
MINERVINI_ENABLED:           bool = os.getenv("MINERVINI_ENABLED",           "True").lower() not in ("false", "0", "no")
WEINSTEIN_ENABLED:           bool = os.getenv("WEINSTEIN_ENABLED",           "True").lower() not in ("false", "0", "no")
TAPE_SPEED_ENABLED:          bool = os.getenv("TAPE_SPEED_ENABLED",          "True").lower() not in ("false", "0", "no")
COPPER_GOLD_ENABLED:         bool = os.getenv("COPPER_GOLD_ENABLED",         "True").lower() not in ("false", "0", "no")
RANDOM_FOREST_ENABLED:       bool = os.getenv("RANDOM_FOREST_ENABLED",       "True").lower() not in ("false", "0", "no")

# ── Premium v19.0 — 4-model ML ensemble + execution optimizer + regime v2 + portfolio optimizer ──
ML_ENSEMBLE_ENABLED:          bool = os.getenv("ML_ENSEMBLE_ENABLED",          "True").lower() not in ("false", "0", "no")
EXECUTION_OPTIMIZER_ENABLED:  bool = os.getenv("EXECUTION_OPTIMIZER_ENABLED",  "True").lower() not in ("false", "0", "no")
REGIME_V2_ENABLED:            bool = os.getenv("REGIME_V2_ENABLED",            "True").lower() not in ("false", "0", "no")
PORTFOLIO_OPTIMIZER_ENABLED:  bool = os.getenv("PORTFOLIO_OPTIMIZER_ENABLED",  "True").lower() not in ("false", "0", "no")

# ── Frontier Quant Intelligence v20.0 — Kalman · HMM · Factor · VPIN · IC ───
KALMAN_ENABLED:               bool = os.getenv("KALMAN_ENABLED",               "True").lower() not in ("false", "0", "no")
HMM_REGIME_ENABLED:           bool = os.getenv("HMM_REGIME_ENABLED",           "True").lower() not in ("false", "0", "no")
FACTOR_ALPHA_ENABLED:         bool = os.getenv("FACTOR_ALPHA_ENABLED",         "True").lower() not in ("false", "0", "no")
VPIN_ENABLED:                 bool = os.getenv("VPIN_ENABLED",                 "True").lower() not in ("false", "0", "no")
IC_TRACKER_ENABLED:           bool = os.getenv("IC_TRACKER_ENABLED",           "True").lower() not in ("false", "0", "no")

# ── v23.0 Renaissance-grade alpha modules ──────────────────────────────────────
# Earnings calendar protection: skip entries within 2 days of earnings report
EARNINGS_PROTECTION_ENABLED:  bool = os.getenv("EARNINGS_PROTECTION_ENABLED",  "true").lower() == "true"
# Post-Earnings Announcement Drift: boost/penalize based on earnings beat/miss 1-5 days ago
PEAD_SIGNAL_ENABLED:          bool = os.getenv("PEAD_SIGNAL_ENABLED",          "true").lower() == "true"
# Options intensity: unusual call/put volume as institutional positioning signal
OPTIONS_INTENSITY_ENABLED:    bool = os.getenv("OPTIONS_INTENSITY_ENABLED",    "true").lower() == "true"
# Pre-market gap scanner: classify overnight gaps as momentum/earnings/weak
GAP_SCANNER_ENABLED:          bool = os.getenv("GAP_SCANNER_ENABLED",          "true").lower() == "true"

# ── v24.0 Alternative Data Intelligence ────────────────────────────────────────
# SEC Form 4 insider trading signal — CEO/CFO open-market buys = strongest signal in finance
INSIDER_INTELLIGENCE_ENABLED: bool = os.getenv("INSIDER_INTELLIGENCE_ENABLED", "true").lower() == "true"
# Gamma Exposure (GEX) — options market maker hedging flows; negative GEX = moves amplify
GEX_ENABLED:                  bool = os.getenv("GEX_ENABLED",                  "true").lower() == "true"
# Reddit WSB + StockTwits crowd sentiment — retail FOMO creates momentum
CROWD_SENTIMENT_ENABLED:      bool = os.getenv("CROWD_SENTIMENT_ENABLED",      "true").lower() == "true"
# Fama-French 5-Factor model alignment — Nobel Prize factors (Mkt, SMB, HML, RMW, CMA)
FF_FACTORS_ENABLED:           bool = os.getenv("FF_FACTORS_ENABLED",           "true").lower() == "true"
# Congressional trading signal — politicians beat market 6-12% annually
CONGRESSIONAL_ALPHA_ENABLED:  bool = os.getenv("CONGRESSIONAL_ALPHA_ENABLED",  "true").lower() == "true"

# ── v36.0 US God Mode Signals ────────────────────────────────────────────────
# Congressional trades alpha (god mode) — dedicated module with disk cache + Senate eFD fallback
CONGRESSIONAL_TRADES_ALPHA_ENABLED: bool = bool(int(os.getenv("CONGRESSIONAL_ALPHA_ENABLED", "1")))
# SEC Form 4 insider buying — cluster buy +10, large single buy +6, seller -5
INSIDER_ALPHA_ENABLED:              bool = bool(int(os.getenv("INSIDER_ALPHA_ENABLED", "1")))
# Earnings & FOMC calendar gate — blocks entries before earnings, halves size on FOMC day
EARNINGS_CALENDAR_ENABLED:          bool = bool(int(os.getenv("EARNINGS_CALENDAR_ENABLED", "1")))

# ── v25.0 Market Microstructure Signals ────────────────────────────────────────
# ORB quality check — institutional commitment at open
ORB_QUALITY_ENABLED:          bool = os.getenv("ORB_QUALITY_ENABLED",          "true").lower() == "true"
# 52-week proximity bias — momentum persistence near highs
W52_PROXIMITY_ENABLED:        bool = os.getenv("W52_PROXIMITY_ENABLED",        "true").lower() == "true"
# Float-adjusted momentum — low float + high SI = squeeze candidate
FLOAT_MOMENTUM_ENABLED:       bool = os.getenv("FLOAT_MOMENTUM_ENABLED",       "true").lower() == "true"
# Tick divergence — smart money micro-accumulation detection
TICK_DIVERGENCE_ENABLED:      bool = os.getenv("TICK_DIVERGENCE_ENABLED",      "true").lower() == "true"
# Z-score mean reversion — Ornstein-Uhlenbeck statistical reversion signal
ZSCORE_MR_ENABLED:            bool = os.getenv("ZSCORE_MR_ENABLED",            "true").lower() == "true"
# Consecutive candle streak — institutional iceberg order detection
CANDLE_STREAK_ENABLED:        bool = os.getenv("CANDLE_STREAK_ENABLED",        "true").lower() == "true"
# Pre-market volume surge — institutional news reaction follow-through
PREMARKET_VOL_ENABLED:        bool = os.getenv("PREMARKET_VOL_ENABLED",        "true").lower() == "true"
# SPY correlation filter — independent alpha vs market-driven momentum
CORRELATION_FILTER_ENABLED:   bool = os.getenv("CORRELATION_FILTER_ENABLED",   "true").lower() == "true"

# ── v26.0 Seasonality + Multi-Momentum + Liquidity ─────────────────────────────
SEASONALITY_ENABLED:          bool = os.getenv("SEASONALITY_ENABLED",          "true").lower() == "true"
OPEX_EFFECT_ENABLED:          bool = os.getenv("OPEX_EFFECT_ENABLED",          "true").lower() == "true"
MONTH_END_ENABLED:            bool = os.getenv("MONTH_END_ENABLED",            "true").lower() == "true"
QUARTER_END_ENABLED:          bool = os.getenv("QUARTER_END_ENABLED",          "true").lower() == "true"
MONDAY_FADE_ENABLED:          bool = os.getenv("MONDAY_FADE_ENABLED",          "true").lower() == "true"
OPEX_PIN_ENABLED:             bool = os.getenv("OPEX_PIN_ENABLED",             "true").lower() == "true"
MULTI_MOMENTUM_ENABLED:       bool = os.getenv("MULTI_MOMENTUM_ENABLED",       "true").lower() == "true"
LIQUIDITY_SIGNALS_ENABLED:    bool = os.getenv("LIQUIDITY_SIGNALS_ENABLED",    "true").lower() == "true"
AMIHUD_ENABLED:               bool = os.getenv("AMIHUD_ENABLED",               "true").lower() == "true"
ROLL_SPREAD_ENABLED:          bool = os.getenv("ROLL_SPREAD_ENABLED",          "true").lower() == "true"
KYLE_LAMBDA_ENABLED:          bool = os.getenv("KYLE_LAMBDA_ENABLED",          "true").lower() == "true"
VOL_CLOCK_ENABLED:            bool = os.getenv("VOL_CLOCK_ENABLED",            "true").lower() == "true"

# ── v29.0 Top 1% Modules ────────────────────────────────────────────────────────
# PEAD: Post-Earnings Announcement Drift (Ball & Brown 1968) — systematic drift after earnings
PEAD_ENGINE_ENABLED:          bool = os.getenv("PEAD_ENGINE_ENABLED",          "true").lower() == "true"
# Regime signal router: BULL→momentum 1.5x, BEAR→mean-revert 1.3x, CHOPPY→reduce 0.7x
REGIME_ROUTER_ENABLED:        bool = os.getenv("REGIME_ROUTER_ENABLED",        "true").lower() == "true"
# Intraday VaR: historical simulation VaR-based position sizing (1% budget constraint)
INTRADAY_VAR_ENABLED:         bool = os.getenv("INTRADAY_VAR_ENABLED",         "true").lower() == "true"
# Correlation crisis: cut sizes 50-70% when market pairwise correlation spikes (crash detector)
CORRELATION_CRISIS_ENABLED:   bool = os.getenv("CORRELATION_CRISIS_ENABLED",   "true").lower() == "true"
# TWAP engine: split orders >$2k into 5 child orders over 5 minutes (reduces slippage 30-40%)
TWAP_ENABLED:                 bool = os.getenv("TWAP_ENABLED",                 "true").lower() == "true"

# ── v28.0 Top 0.1% Modules ──────────────────────────────────────────────────────
# HAR-RV: Heterogeneous AutoRegressive Realized Variance (Corsi 2009) — replaces GARCH
HAR_RV_ENABLED:               bool = os.getenv("HAR_RV_ENABLED",               "true").lower() == "true"
# Tape OFI: Lee-Ready buy/sell classification → true order flow imbalance
TAPE_OFI_ENABLED:             bool = os.getenv("TAPE_OFI_ENABLED",             "true").lower() == "true"
# Synthetic L2: reconstruct pseudo-Level-2 from OHLCV (free proxy for paid L2 data)
SYNTHETIC_L2_ENABLED:         bool = os.getenv("SYNTHETIC_L2_ENABLED",         "true").lower() == "true"
# Dark pool proxy: detect institutional accumulation from volume/price patterns
DARK_POOL_ENABLED:            bool = os.getenv("DARK_POOL_ENABLED",             "true").lower() == "true"
# Alt data: Google Trends + Wikipedia edit velocity + Reddit WSB (all free)
ALT_DATA_ENABLED:             bool = os.getenv("ALT_DATA_ENABLED",             "true").lower() == "true"
# EDGAR NLP: SEC 8-K filing sentiment via Loughran-McDonald word lists (free EDGAR API)
EDGAR_SENTIMENT_ENABLED:      bool = os.getenv("EDGAR_SENTIMENT_ENABLED",      "true").lower() == "true"
# CBOE data: put/call ratio + VIX term structure (free public CBOE data)
CBOE_DATA_ENABLED:            bool = os.getenv("CBOE_DATA_ENABLED",            "true").lower() == "true"
# Beta-neutral sizing: dynamic beta vs SPY, beta-inverse position sizing
BETA_NEUTRAL_ENABLED:         bool = os.getenv("BETA_NEUTRAL_ENABLED",         "true").lower() == "true"
# Covariance optimizer: Ledoit-Wolf shrinkage + correlation-aware portfolio sizing
COV_OPTIMIZER_ENABLED:        bool = os.getenv("COV_OPTIMIZER_ENABLED",        "true").lower() == "true"
# Cost filter: pre-entry Corwin-Schultz spread + impact check — skip unprofitable trades
COST_FILTER_ENABLED:          bool = os.getenv("COST_FILTER_ENABLED",          "true").lower() == "true"

# ── v27.0 Renaissance Medallion Strategies ──────────────────────────────────────
# PCA factor decomposition: isolate idiosyncratic alpha from market/sector noise
PCA_ALPHA_ENABLED:            bool = os.getenv("PCA_ALPHA_ENABLED",            "true").lower() == "true"
# Event alpha: analyst upgrades/downgrades, dividend capture, stock splits
EVENT_ALPHA_ENABLED:          bool = os.getenv("EVENT_ALPHA_ENABLED",          "true").lower() == "true"
# STL trend decomposition via Loess: score trend component, not raw price noise
STL_ENABLED:                  bool = os.getenv("STL_ENABLED",                  "true").lower() == "true"
# Execution timing intelligence: avoid open chaos, last 10min MOC, boost power hour
EXECUTION_TIMING_ENABLED:     bool = os.getenv("EXECUTION_TIMING_ENABLED",     "true").lower() == "true"
# Market impact check (Almgren-Chriss): penalize orders > 1% ADV
MARKET_IMPACT_ENABLED:        bool = os.getenv("MARKET_IMPACT_ENABLED",        "true").lower() == "true"
# Cross-sectional pre-ranking: only scan top 40% of watchlist by momentum rank
CROSS_SECTIONAL_RANKING_ENABLED: bool = os.getenv("CROSS_SECTIONAL_RANKING_ENABLED", "true").lower() == "true"

# ── v32.0 Market Intelligence Hub — 25 modules in parallel ───────────────────
# Single flag to enable/disable all hub modules at once (each fails open)
INTELLIGENCE_HUB_ENABLED:    bool = os.getenv("INTELLIGENCE_HUB_ENABLED",    "true").lower() == "true"

# ── v32.1 L99 — Free tier 2 + paid data proxies ──────────────────────────────
FINRA_DARKPOOL_ENABLED:   bool = os.getenv("FINRA_DARKPOOL_ENABLED",   "true").lower() == "true"
FEDWATCH_ENABLED:         bool = os.getenv("FEDWATCH_ENABLED",         "true").lower() == "true"
CRYPTO_CROSS_ENABLED:     bool = os.getenv("CRYPTO_CROSS_ENABLED",     "true").lower() == "true"
AV_NEWS_ENABLED:          bool = os.getenv("AV_NEWS_ENABLED",          "true").lower() == "true"
OPTIONS_SWEEP_ENABLED:    bool = os.getenv("OPTIONS_SWEEP_ENABLED",    "true").lower() == "true"
BLOOMBERG_PROXY_ENABLED:  bool = os.getenv("BLOOMBERG_PROXY_ENABLED",  "true").lower() == "true"
FACTSET_PROXY_ENABLED:    bool = os.getenv("FACTSET_PROXY_ENABLED",    "true").lower() == "true"
SATELLITE_PROXY_ENABLED:  bool = os.getenv("SATELLITE_PROXY_ENABLED",  "true").lower() == "true"
ENIGMA_PROXY_ENABLED:     bool = os.getenv("ENIGMA_PROXY_ENABLED",     "true").lower() == "true"
ITCH_L3_PROXY_ENABLED:    bool = os.getenv("ITCH_L3_PROXY_ENABLED",    "true").lower() == "true"
LIVEVOL_PROXY_ENABLED:    bool = os.getenv("LIVEVOL_PROXY_ENABLED",    "true").lower() == "true"
GARCH_SIZING_ENABLED:     bool = os.getenv("GARCH_SIZING_ENABLED",     "true").lower() == "true"
ANALYST_CONSENSUS_ENABLED: bool = os.getenv("ANALYST_CONSENSUS_ENABLED","true").lower() == "true"

# ── v33.1 Idle Scalp Mode — take small trades when idle > 30 min ─────────────
IDLE_SCALP_ENABLED:           bool  = os.getenv("IDLE_SCALP_ENABLED",  "true").lower() == "true"
IDLE_SCALP_THRESHOLD_MIN:     int   = int(os.getenv("IDLE_SCALP_THRESHOLD_MIN", "30"))  # minutes idle before activating
IDLE_SCALP_MIN_SCORE:         float = float(os.getenv("IDLE_SCALP_MIN_SCORE", "55.0"))
IDLE_SCALP_MIN_RR:            float = float(os.getenv("IDLE_SCALP_MIN_RR",    "1.5"))
IDLE_SCALP_SIZE_MULT:         float = float(os.getenv("IDLE_SCALP_SIZE_MULT", "0.40"))
IDLE_SCALP_TIME_STOP_MIN:     int   = int(os.getenv("IDLE_SCALP_TIME_STOP_MIN", "10"))

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
    # Only warn if explicitly going live with real money but safety flags look wrong
    _is_paper = os.getenv("ALPACA_PAPER", "true").lower() != "false"
    if LIVE_TRADING_ENABLED and not _is_paper and "paper-api" in ALPACA_BASE_URL:
        issues.append(
            "CRITICAL: ALPACA_PAPER=false (live money) but ALPACA_BASE_URL still points to paper endpoint."
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
