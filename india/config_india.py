"""
config_india.py — KingTrades India Bot Configuration
Dhan broker | NSE equity | IST timezone | INR capital
"""
from datetime import time
from zoneinfo import ZoneInfo
import os

IST = ZoneInfo("Asia/Kolkata")

# -- Market hours (IST) -------------------------------------------------------
MARKET_OPEN_IST       = time(9, 15)
MARKET_CLOSE_IST      = time(15, 30)
SQUAREOFF_TIME_IST    = time(15, 20)   # force close all MIS positions
SQUAREOFF_WARN_IST    = time(15, 15)   # telegram warning
PRE_MARKET_START_IST  = time(9, 0)

# -- Capital (INR) ------------------------------------------------------------
MAX_DAILY_CAPITAL       = float(os.getenv("INDIA_MAX_DAILY_CAPITAL",   "500000"))
MAX_RISK_PER_TRADE_PCT  = float(os.getenv("INDIA_MAX_RISK_PCT",        "0.5")) / 100
DAILY_LOSS_LIMIT_PCT    = float(os.getenv("INDIA_DAILY_LOSS_LIMIT_PCT","2.0")) / 100
MAX_POSITIONS           = int(os.getenv(  "INDIA_MAX_POSITIONS",       "5"))

# -- Dhan credentials (from env only -- never hardcode) -----------------------
DHAN_CLIENT_ID    = os.getenv("DHAN_CLIENT_ID",    "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

# -- Telegram (shared with US bot if using same channel) ----------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# -- Signal thresholds (optimised for NSE 2:1 R:R, target WR 57-61%) ---------
# Break-even WR at 2:1 R:R = 33.3%. Optimal EV at ~58% WR.
# 67 post-HAF threshold captures more HAF-validated signals vs 70 (+~15% trades)
# 63 pre-filter is tighter than old 60 -- reduces noise entering the 26-gate pipeline
# 82 Grand Slam unchanged -- highest conviction -> 1.35x Kelly-weighted size
MIN_SIGNAL_SCORE      = float(os.getenv("INDIA_MIN_SIGNAL_SCORE",     "63.0"))
FINAL_EXEC_MIN_SCORE  = float(os.getenv("INDIA_FINAL_EXEC_MIN_SCORE", "67.0"))
GRAND_SLAM_MIN_SCORE  = float(os.getenv("INDIA_GRAND_SLAM_MIN_SCORE", "82.0"))

# -- ATR-based SL / TP --------------------------------------------------------
ATR_SL_MULTIPLIER      = float(os.getenv("INDIA_ATR_SL_MULTIPLIER",     "1.5"))
ATR_TP_MULTIPLIER      = float(os.getenv("INDIA_ATR_TP_MULTIPLIER",     "3.0"))

# -- Live trading gate --------------------------------------------------------
LIVE_TRADING_ENABLED = os.getenv("INDIA_LIVE_TRADING_ENABLED", "False") == "True"

# -- Manual signals mode: bot sends alerts, YOU place orders in Dhan app -----
# True  = bot detects setups and messages you → you trade manually in Dhan
# False = bot auto-executes in Dhan (requires LIVE_TRADING_ENABLED=True)
MANUAL_SIGNALS_ONLY = os.getenv("INDIA_MANUAL_SIGNALS_ONLY", "True") != "False"

# -- Feature flags (all on by default) ----------------------------------------
def _flag(key: str) -> bool:
    return os.getenv(key, "True") != "False"

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

# -- Dhan exchange constants --------------------------------------------------
EXCHANGE        = "NSE_EQ"
PRODUCT_TYPE    = "INTRADAY"   # MIS equivalent on Dhan
NSE_SUFFIX = "-EQ"             # Dhan security ID suffix for NSE equity

# -- Scan interval ------------------------------------------------------------
SCAN_INTERVAL_SECONDS = 300   # scan watchlist every 5 minutes

# -- Tier 1 feature flags (India-specific free data) --------------------------
OPTION_CHAIN_ENABLED    = _flag("INDIA_OPTION_CHAIN_ENABLED")    # NSE option chain PCR/OI
FII_DII_ENABLED         = _flag("INDIA_FII_DII_ENABLED")         # FII/DII daily flow
DELIVERY_VOL_ENABLED    = _flag("INDIA_DELIVERY_VOL_ENABLED")    # NSE delivery %
VOL_PROFILE_ENABLED     = _flag("INDIA_VOL_PROFILE_ENABLED")     # Volume profile VPOC
NEWS_SENTIMENT_ENABLED  = _flag("INDIA_NEWS_SENTIMENT_ENABLED")  # MoneyControl/ET RSS
ML_INDIA_ENABLED        = _flag("INDIA_ML_INDIA_ENABLED")        # NSE-specific ML models

# -- R:R + target config ------------------------------------------------------
ATR_T1_MULTIPLIER       = float(os.getenv("INDIA_ATR_T1_MULT", "1.0"))   # T1 at 1:1
MIN_RISK_REWARD         = float(os.getenv("INDIA_MIN_RR", "2.0"))
MONTHLY_TARGET_PCT      = float(os.getenv("INDIA_MONTHLY_TARGET", "10.0"))
DAILY_TARGET_PCT        = float(os.getenv("INDIA_DAILY_TARGET", "0.5"))

# -- Trailing stop stages -----------------------------------------------------
BREAKEVEN_TRIGGER_PCT   = float(os.getenv("INDIA_BREAKEVEN_TRIGGER", "0.3")) / 100
TRAILING_T1_PCT         = float(os.getenv("INDIA_TRAILING_T1", "50.0")) / 100
TRAILING_TIGHT_ATR      = float(os.getenv("INDIA_TRAILING_TIGHT_ATR", "0.3"))

# -- Idle scalp mode ----------------------------------------------------------
IDLE_SCALP_ENABLED          = _flag("INDIA_IDLE_SCALP_ENABLED")
IDLE_SCALP_THRESHOLD_MIN    = int(os.getenv("INDIA_SCALP_THRESHOLD_MIN", "30"))
IDLE_SCALP_MIN_SCORE        = float(os.getenv("INDIA_SCALP_MIN_SCORE", "55.0"))
IDLE_SCALP_MIN_RR           = float(os.getenv("INDIA_SCALP_MIN_RR", "1.5"))
IDLE_SCALP_SIZE_MULT        = float(os.getenv("INDIA_SCALP_SIZE_MULT", "0.40"))
IDLE_SCALP_TIME_STOP_MIN    = int(os.getenv("INDIA_SCALP_TIME_STOP", "10"))

# -- India VIX thresholds -----------------------------------------------------
INDIA_VIX_ENABLED           = _flag("INDIA_VIX_ENABLED")
INDIA_VIX_HIGH_THRESHOLD    = float(os.getenv("INDIA_VIX_HIGH", "22.0"))
INDIA_VIX_EXTREME_THRESHOLD = float(os.getenv("INDIA_VIX_EXTREME", "28.0"))

# -- Position sizing limits ---------------------------------------------------
MAX_POSITION_PCT            = float(os.getenv("INDIA_MAX_POS_PCT", "20.0")) / 100
GRAND_SLAM_MAX_PCT          = float(os.getenv("INDIA_GS_MAX_PCT", "25.0")) / 100

# -- ORB config ---------------------------------------------------------------
ORB_VALID_UNTIL_HOUR        = int(os.getenv("INDIA_ORB_VALID_UNTIL", "13"))
ORB_VOLUME_SURGE_MULT       = float(os.getenv("INDIA_ORB_VOL_SURGE", "1.5"))

# -- Advanced feature flags ---------------------------------------------------
REGIME_FILTER_ENABLED       = _flag("INDIA_REGIME_FILTER_ENABLED")
PORTFOLIO_INTEL_ENABLED     = _flag("INDIA_PORTFOLIO_INTEL_ENABLED")
ML_ENSEMBLE_ENABLED         = _flag("INDIA_ML_ENSEMBLE_ENABLED")
IC_TRACKER_ENABLED          = _flag("INDIA_IC_TRACKER_ENABLED")
SMART_EXECUTION_ENABLED     = _flag("INDIA_SMART_EXEC_ENABLED")
OPTION_CHAIN_GODMODE        = _flag("INDIA_OC_GODMODE_ENABLED")
NEWS_NLP_GODMODE            = _flag("INDIA_NEWS_NLP_ENABLED")
CORP_EVENTS_ENABLED         = _flag("INDIA_CORP_EVENTS_ENABLED")
SECTOR_ROTATION_ENABLED     = _flag("INDIA_SECTOR_ROTATION_ENABLED")
TELEGRAM_COMMANDS_ENABLED   = _flag("INDIA_TG_COMMANDS_ENABLED")
TRAILING_STOP_ENABLED       = _flag("INDIA_TRAILING_STOP_ENABLED")
PARTIAL_EXIT_ENABLED        = _flag("INDIA_PARTIAL_EXIT_ENABLED")
SPREAD_CHECK_ENABLED        = _flag("INDIA_SPREAD_CHECK_ENABLED")
VOLUME_CONFIRM_ENABLED      = _flag("INDIA_VOL_CONFIRM_ENABLED")
MAX_SECTOR_CONCENTRATION    = int(os.getenv("INDIA_MAX_SECTOR_CONC", "2"))
MAX_CORRELATION_THRESHOLD   = float(os.getenv("INDIA_MAX_CORR", "0.75"))
MAX_PORTFOLIO_HEAT_PCT      = float(os.getenv("INDIA_PORTFOLIO_HEAT", "4.0")) / 100

# -- God Mode enhancement flags (Unit 1–10) -----------------------------------
PHASE_ENGINE_ENABLED        = _flag("INDIA_PHASE_ENGINE_ENABLED")
VOLUME_PROFILE_GODMODE      = _flag("INDIA_VOL_PROFILE_GODMODE")
STRUCTURAL_SL_ENABLED       = _flag("INDIA_STRUCTURAL_SL_ENABLED")
GLOBAL_CUES_ENABLED         = _flag("INDIA_GLOBAL_CUES_ENABLED")
ADAPTIVE_THRESHOLD_ENABLED  = _flag("INDIA_ADAPTIVE_THRESHOLD_ENABLED")
GEX_ENABLED                 = _flag("INDIA_GEX_ENABLED")
SECTOR_CONFLUENCE_ENABLED   = _flag("INDIA_SECTOR_CONFLUENCE_ENABLED")
MARKET_BREADTH_ENABLED      = _flag("INDIA_MARKET_BREADTH_ENABLED")
MORNING_INTEL_ENABLED       = _flag("INDIA_MORNING_INTEL_ENABLED")
