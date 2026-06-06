"""
config_india.py — KingTrades India Bot Configuration (L99)
Dhan broker | NSE equity | IST timezone | INR capital
All new L99 flags: VIX, trailing stops, partial exits, idle scalp,
ORB, sector rotation, grand-slam sizing, and more.
"""
from datetime import time
from zoneinfo import ZoneInfo
import os

IST = ZoneInfo("Asia/Kolkata")

# ── Market hours (IST) ──────────────────────────────────────────────────────
MARKET_OPEN_IST       = time(9, 15)
MARKET_CLOSE_IST      = time(15, 30)
SQUAREOFF_TIME_IST    = time(15, 20)   # force close all MIS positions
SQUAREOFF_WARN_IST    = time(15, 15)   # telegram warning
PRE_MARKET_START_IST  = time(9, 0)

# ── Capital (INR) ────────────────────────────────────────────────────────────
MAX_DAILY_CAPITAL       = float(os.getenv("INDIA_MAX_DAILY_CAPITAL",   "500000"))
MAX_RISK_PER_TRADE_PCT  = float(os.getenv("INDIA_MAX_RISK_PCT",        "0.5")) / 100
DAILY_LOSS_LIMIT_PCT    = float(os.getenv("INDIA_DAILY_LOSS_LIMIT_PCT","2.0")) / 100
MAX_POSITIONS           = int(os.getenv(  "INDIA_MAX_POSITIONS",       "5"))

# ── Dhan credentials (from env only — never hardcode) ────────────────────────
DHAN_CLIENT_ID    = os.getenv("DHAN_CLIENT_ID",    "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# ── Signal thresholds ────────────────────────────────────────────────────────
# Break-even WR at 2:1 R:R = 33.3%. Optimal EV at ~58% WR.
# 67 post-HAF threshold captures more HAF-validated signals vs 70 (+~15% trades)
# 63 pre-filter is tighter than old 60 — reduces noise entering the 26-gate pipeline
# 82 Grand Slam unchanged — highest conviction → 1.35× Kelly-weighted size
MIN_SIGNAL_SCORE      = float(os.getenv("INDIA_MIN_SIGNAL_SCORE",     "63.0"))
FINAL_EXEC_MIN_SCORE  = float(os.getenv("INDIA_FINAL_EXEC_MIN_SCORE", "67.0"))
GRAND_SLAM_MIN_SCORE  = float(os.getenv("INDIA_GRAND_SLAM_MIN_SCORE", "82.0"))

# ── R:R configuration (2:1 minimum, 3:1 target for runners) ─────────────────
# ATR_T1_MULTIPLIER: fast partial exit at 1:1 to lock gains early
# ATR_TP_MULTIPLIER: full runner target at 3:1 R:R
ATR_SL_MULTIPLIER      = float(os.getenv("INDIA_ATR_SL_MULTIPLIER",    "1.5"))
ATR_TP_MULTIPLIER      = float(os.getenv("INDIA_ATR_TP_MULTIPLIER",    "3.0"))   # T2 at 3:1 for runners
ATR_T1_MULTIPLIER      = float(os.getenv("INDIA_ATR_T1_MULTIPLIER",    "1.0"))   # T1 at 1:1 — fast partial exit
MIN_RISK_REWARD        = float(os.getenv("INDIA_MIN_RISK_REWARD",       "2.0"))

# ── Return targets ───────────────────────────────────────────────────────────
# 10% monthly = ~0.4%/day compounded conservatively
# 0.5% daily minimum target triggers idle-scalp mode if not reached by midday
MONTHLY_TARGET_PCT     = float(os.getenv("INDIA_MONTHLY_TARGET_PCT",   "10.0"))
DAILY_TARGET_PCT       = float(os.getenv("INDIA_DAILY_TARGET_PCT",      "0.5"))

# ── Trailing stop configuration ──────────────────────────────────────────────
# BREAKEVEN_TRIGGER_PCT: move SL to break-even once 0.3% profit
# TRAILING_T1_PCT:       exit 50% of position at T1 target (runner stays)
# TRAILING_TIGHT_PCT:    trail remaining at 0.3 ATR once in profit
BREAKEVEN_TRIGGER_PCT  = float(os.getenv("INDIA_BREAKEVEN_TRIGGER_PCT", "0.003"))  # 0.3%
TRAILING_T1_PCT        = float(os.getenv("INDIA_TRAILING_T1_PCT",       "0.50"))   # exit 50% at T1
TRAILING_TIGHT_PCT     = float(os.getenv("INDIA_TRAILING_TIGHT_PCT",    "0.003"))  # trail at 0.3 ATR

# ── Position sizing limits ────────────────────────────────────────────────────
MAX_POSITION_PCT       = float(os.getenv("INDIA_MAX_POSITION_PCT",     "0.20"))   # 20% capital max
GRAND_SLAM_MAX_PCT     = float(os.getenv("INDIA_GRAND_SLAM_MAX_PCT",   "0.25"))   # 25% for A+ grades

# ── ORB (Opening Range Breakout) config ──────────────────────────────────────
# ORB range set in first 15 minutes (9:15–9:30 AM IST)
# Signals valid until 1 PM IST; volume surge confirms breakout
ORB_VALID_UNTIL_HOUR   = int(os.getenv("INDIA_ORB_VALID_UNTIL_HOUR",   "13"))     # signals valid until 1 PM IST
ORB_VOLUME_SURGE_MULT  = float(os.getenv("INDIA_ORB_VOLUME_SURGE_MULT","1.5"))    # 1.5x avg vol = surge

# ── India VIX thresholds ─────────────────────────────────────────────────────
# HIGH:    reduce all sizes by 30% (hedged environment)
# EXTREME: stop all new entries (tail-risk protection)
# LOW:     increase confidence scoring (benign vol → momentum works better)
INDIA_VIX_HIGH_THRESHOLD    = float(os.getenv("INDIA_VIX_HIGH_THRESHOLD",    "22.0"))
INDIA_VIX_EXTREME_THRESHOLD = float(os.getenv("INDIA_VIX_EXTREME_THRESHOLD", "28.0"))
INDIA_VIX_LOW_THRESHOLD     = float(os.getenv("INDIA_VIX_LOW_THRESHOLD",     "12.0"))

# ── Idle scalp mode ───────────────────────────────────────────────────────────
# Activates when no prime momentum trade found for IDLE_SCALP_THRESHOLD_MIN minutes.
# Uses tighter score/RR thresholds and smaller size (40% of normal).
# Auto time-stop after IDLE_SCALP_TIME_STOP_MIN minutes if no fill/move.
IDLE_SCALP_THRESHOLD_MIN    = int(os.getenv(  "INDIA_IDLE_SCALP_THRESHOLD_MIN",  "30"))
IDLE_SCALP_MIN_SCORE        = float(os.getenv("INDIA_IDLE_SCALP_MIN_SCORE",      "55.0"))
IDLE_SCALP_MIN_RR           = float(os.getenv("INDIA_IDLE_SCALP_MIN_RR",         "1.5"))
IDLE_SCALP_SIZE_MULT        = float(os.getenv("INDIA_IDLE_SCALP_SIZE_MULT",      "0.40"))
IDLE_SCALP_TIME_STOP_MIN    = int(os.getenv(  "INDIA_IDLE_SCALP_TIME_STOP_MIN",  "10"))

# ── NSE-specific market-structure filters ────────────────────────────────────
NIFTY_FILTER_ENABLED        = os.getenv("INDIA_NIFTY_FILTER_ENABLED",         "True") != "False"
NIFTY_BEARISH_ALLOW_LONG    = os.getenv("INDIA_NIFTY_BEARISH_ALLOW_LONG",     "False") == "True"
NIFTY_BULLISH_ALLOW_SHORT   = os.getenv("INDIA_NIFTY_BULLISH_ALLOW_SHORT",    "False") == "True"
SECTOR_ROTATION_ENABLED     = os.getenv("INDIA_SECTOR_ROTATION_ENABLED",      "True") != "False"

# ── Loss guard ────────────────────────────────────────────────────────────────
# Halve position size after 3 consecutive losses until a winner resets the count
LOSS_GUARD_HALF_SIZE        = os.getenv("INDIA_LOSS_GUARD_HALF_SIZE", "True") != "False"

# ── Live trading gate ────────────────────────────────────────────────────────
LIVE_TRADING_ENABLED = os.getenv("INDIA_LIVE_TRADING_ENABLED", "False") == "True"

# ── Feature flags (all on by default) ───────────────────────────────────────
def _flag(key: str) -> bool:
    return os.getenv(key, "True") != "False"

# Original feature flags (preserved)
NEURAL_PREDICTOR_ENABLED  = _flag("INDIA_NEURAL_PREDICTOR_ENABLED")
COINT_ARBIT_ENABLED       = _flag("INDIA_COINT_ARBIT_ENABLED")
GAP_FADE_ENABLED          = _flag("INDIA_GAP_FADE_ENABLED")
VOL_TARGET_ENABLED        = _flag("INDIA_VOL_TARGET_ENABLED")
CSM_ENABLED               = _flag("INDIA_CSM_ENABLED")
VWAP_RECLAIM_ENABLED      = _flag("INDIA_VWAP_RECLAIM_ENABLED")
POWER_HOUR_ENABLED        = _flag("INDIA_POWER_HOUR_ENABLED")
PAIRS_SIGNAL_ENABLED      = _flag("INDIA_PAIRS_SIGNAL_ENABLED")
TOD_RVOL_ENABLED          = _flag("INDIA_TOD_RVOL_ENABLED")
SORTINO_SIZING_ENABLED    = _flag("INDIA_SORTINO_SIZING_ENABLED")

# Tier 1 India-specific free data flags (preserved)
OPTION_CHAIN_ENABLED    = _flag("INDIA_OPTION_CHAIN_ENABLED")    # NSE option chain PCR/OI
FII_DII_ENABLED         = _flag("INDIA_FII_DII_ENABLED")         # FII/DII daily flow
DELIVERY_VOL_ENABLED    = _flag("INDIA_DELIVERY_VOL_ENABLED")    # NSE delivery %
VOL_PROFILE_ENABLED     = _flag("INDIA_VOL_PROFILE_ENABLED")     # Volume profile VPOC
NEWS_SENTIMENT_ENABLED  = _flag("INDIA_NEWS_SENTIMENT_ENABLED")  # MoneyControl/ET RSS
ML_INDIA_ENABLED        = _flag("INDIA_ML_INDIA_ENABLED")        # NSE-specific ML models

# New L99 feature flags
INDIA_VIX_ENABLED           = _flag("INDIA_VIX_ENABLED")           # India VIX adaptive sizing
TRAILING_STOP_ENABLED       = _flag("INDIA_TRAILING_STOP_ENABLED")  # dynamic trailing stops
PARTIAL_EXIT_ENABLED        = _flag("INDIA_PARTIAL_EXIT_ENABLED")   # T1/T2 partial exit ladder
IDLE_SCALP_ENABLED          = _flag("INDIA_IDLE_SCALP_ENABLED")     # idle scalp fallback mode
TELEGRAM_COMMANDS_ENABLED   = _flag("INDIA_TELEGRAM_COMMANDS_ENABLED")  # /kill /pause /resume etc.

# ── Dhan exchange constants ──────────────────────────────────────────────────
EXCHANGE        = "NSE_EQ"
PRODUCT_TYPE    = "INTRADAY"   # MIS equivalent on Dhan
YFINANCE_SUFFIX = ".NS"        # for yfinance historical data

# ── Scan interval ────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300   # scan watchlist every 5 minutes
