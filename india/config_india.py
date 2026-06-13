"""
config_india.py — SataVector India Bot Configuration
Upstox broker | NSE equity | IST timezone | INR capital
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

# -- Upstox credentials (from env only -- never hardcode) ---------------------
UPSTOX_API_KEY      = os.getenv("UPSTOX_API_KEY",      "")
UPSTOX_API_SECRET   = os.getenv("UPSTOX_API_SECRET",   "")
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")

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

# -- Manual signals mode: bot sends alerts, YOU place orders in Upstox app ---
# True  = bot detects setups and messages you → you trade manually in Upstox
# False = bot auto-executes in Upstox (requires LIVE_TRADING_ENABLED=True)
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

# -- Upstox exchange constants ------------------------------------------------
EXCHANGE        = "NSE_EQ"
PRODUCT_TYPE    = "I"          # MIS equivalent on Upstox (intraday)
NSE_SUFFIX      = ""           # Upstox uses full instrument keys (NSE_EQ|ISIN)

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

# -- God Mode enhancement flags (Round 1, Units 1–10) -------------------------
PHASE_ENGINE_ENABLED        = _flag("INDIA_PHASE_ENGINE_ENABLED")
VOLUME_PROFILE_GODMODE      = _flag("INDIA_VOL_PROFILE_GODMODE")
STRUCTURAL_SL_ENABLED       = _flag("INDIA_STRUCTURAL_SL_ENABLED")
GLOBAL_CUES_ENABLED         = _flag("INDIA_GLOBAL_CUES_ENABLED")
ADAPTIVE_THRESHOLD_ENABLED  = _flag("INDIA_ADAPTIVE_THRESHOLD_ENABLED")
GEX_ENABLED                 = _flag("INDIA_GEX_ENABLED")
SECTOR_CONFLUENCE_ENABLED   = _flag("INDIA_SECTOR_CONFLUENCE_ENABLED")
MARKET_BREADTH_ENABLED      = _flag("INDIA_MARKET_BREADTH_ENABLED")
MORNING_INTEL_ENABLED       = _flag("INDIA_MORNING_INTEL_ENABLED")

# -- Round 2 God Mode flags (Units 1–10, 20% monthly target) ------------------
RS_RANKING_ENABLED          = _flag("INDIA_RS_RANKING_ENABLED")       # IBD-style RS 1-99
GAP_ANALYSIS_ENABLED        = _flag("INDIA_GAP_ANALYSIS_ENABLED")     # gap-fill / continuation
PYRAMID_ENABLED             = _flag("INDIA_PYRAMID_ENABLED")          # add to winners after T1
CHANDELIER_EXIT_ENABLED     = _flag("INDIA_CHANDELIER_EXIT_ENABLED")  # VIX-adaptive trailing stop
FUTURES_OI_ENABLED          = _flag("INDIA_FUTURES_OI_ENABLED")       # NSE futures OI intelligence
DELIVERY_V2_ENABLED         = _flag("INDIA_DELIVERY_V2_ENABLED")      # 5-day accumulation trend
WALK_FORWARD_OPT_ENABLED    = _flag("INDIA_WALK_FORWARD_OPT_ENABLED") # weekly param optimizer
MACRO_SCORING_ENABLED       = _flag("INDIA_MACRO_SCORING_ENABLED")    # RBI/PMI/CPI blackouts
UOA_ENABLED                 = _flag("INDIA_UOA_ENABLED")              # unusual options activity
SORTINO_SIZING_ENABLED      = _flag("INDIA_SORTINO_SIZING_ENABLED")   # Sortino-optimised sizing

# -- Sortino dynamic position sizing config -----------------------------------
SORTINO_MAX_POSITIONS_EXPANSION = _flag("INDIA_SORTINO_EXPAND")   # allow 7 pos when Sortino>2
SORTINO_HIGH_THRESHOLD      = float(os.getenv("INDIA_SORTINO_HIGH", "2.0"))
SORTINO_LOW_THRESHOLD       = float(os.getenv("INDIA_SORTINO_LOW",  "1.0"))
SORTINO_HIGH_SIZE_MULT      = float(os.getenv("INDIA_SORTINO_HIGH_MULT", "1.25"))
SORTINO_LOW_SIZE_MULT       = float(os.getenv("INDIA_SORTINO_LOW_MULT",  "0.75"))
SORTINO_HIGH_MAX_POS        = int(os.getenv("INDIA_SORTINO_HIGH_MAX_POS", "7"))
SORTINO_MID_MAX_POS         = int(os.getenv("INDIA_SORTINO_MID_MAX_POS",  "6"))
SORTINO_LOW_MAX_POS         = int(os.getenv("INDIA_SORTINO_LOW_MAX_POS",  "4"))

# -- Round 3 Renaissance flags (Units 1–10, 7-10% monthly target) --------------
CROSS_ASSET_ENABLED         = _flag("INDIA_CROSS_ASSET_ENABLED")       # USD/INR, crude, gold, SGX
WYCKOFF_VSA_ENABLED         = _flag("INDIA_WYCKOFF_VSA_ENABLED")       # Volume Spread Analysis
PEAD_ENABLED                = _flag("INDIA_PEAD_ENABLED")              # Post-Earnings Drift
BLOCK_DEAL_ENABLED          = _flag("INDIA_BLOCK_DEAL_ENABLED")        # NSE block/bulk deals
MICROSTRUCTURE_ENABLED      = _flag("INDIA_MICROSTRUCTURE_ENABLED")    # tape velocity + urgency
ELITE_TRACKER_ENABLED       = _flag("INDIA_ELITE_TRACKER_ENABLED")     # self-learning WR tracker
MTF_CASCADE_ENABLED         = _flag("INDIA_MTF_CASCADE_ENABLED")       # 1m+5m+15m+1h cascade bonus

# -- Round 3 Deep Research additions (Kalman pairs, VIX regime, 12-month MOM) -
KALMAN_PAIRS_ENABLED        = _flag("INDIA_KALMAN_PAIRS_ENABLED")      # Kalman filter cointegrated pairs
VIX_REGIME_ENABLED          = _flag("INDIA_VIX_REGIME_ENABLED")        # India VIX trend/regime signal
MOMENTUM_FACTOR_ENABLED     = _flag("INDIA_MOMENTUM_FACTOR_ENABLED")   # 12-1 month CSM (IIM-A factor)

# -- Goal: 70%+ WR, 10-15%/month — Research-validated additions ---------------
GEX_SIGNAL_ENABLED          = _flag("INDIA_GEX_SIGNAL_ENABLED")        # NIFTY Gamma Exposure regime
FII_FUTURES_ENABLED         = _flag("INDIA_FII_FUTURES_ENABLED")        # NSE participant-wise futures OI
CHNG_OI_PCR_ENABLED         = _flag("INDIA_CHNG_OI_PCR_ENABLED")       # Change-in-OI PCR (more sensitive)
ORB5_PRECISION_ENABLED      = _flag("INDIA_ORB5_PRECISION_ENABLED")    # 5-min ORB precision mode (70% WR)
ORB_VOL_FILTER_STRONG       = float(os.getenv("INDIA_ORB_VOL_STRONG", "2.0"))  # ≥200% relvol for top score

# -- Adaptive MIS position sizing (leverage when signals are exceptional) -----
# When score >= GRAND_SLAM AND Sortino > 2.5: allow larger position cap
# Never exceed MIS_MAX_CAP_PCT in any single trade
MIS_LEVERAGE_ENABLED        = _flag("INDIA_MIS_LEVERAGE_ENABLED")      # allow >20% cap for A+
MIS_GRAND_SLAM_CAP_PCT      = float(os.getenv("INDIA_MIS_GS_CAP",  "30.0")) / 100  # 30% for A+
MIS_ELITE_CAP_PCT           = float(os.getenv("INDIA_MIS_ELITE_CAP","35.0")) / 100  # 35% for perfect
MIS_SORTINO_MIN             = float(os.getenv("INDIA_MIS_SORTINO_MIN", "2.5"))       # min Sortino for MIS
MIS_WEEKLY_WR_MIN           = float(os.getenv("INDIA_MIS_WR_MIN", "0.62"))           # min 7-day WR for MIS

# -- Multi-timeframe cascade config -------------------------------------------
MTF_FULL_CASCADE_BONUS      = int(os.getenv("INDIA_MTF_FULL_BONUS",   "15"))   # 1m+5m+15m+1h all aligned
MTF_THREE_TF_BONUS          = int(os.getenv("INDIA_MTF_3TF_BONUS",    "8"))    # 3 of 4 TFs aligned
MTF_CONFLICT_PENALTY        = int(os.getenv("INDIA_MTF_CONFLICT_PEN", "-10"))  # TFs in conflict

# -- Elite enhancements: 15-20% monthly target --------------------------------
SIGNAL_DECAY_ENABLED         = _flag("INDIA_SIGNAL_DECAY_ENABLED")          # exponential freshness decay
EXEC_QUALITY_ENABLED         = _flag("INDIA_EXEC_QUALITY_ENABLED")          # time-of-day liquidity + slippage model
REGIME_SELECTOR_ENABLED      = _flag("INDIA_REGIME_SELECTOR_ENABLED")       # ADX+VIX regime multipliers
PORTFOLIO_REBALANCER_ENABLED = _flag("INDIA_PORTFOLIO_REBALANCER_ENABLED")  # correlation pruning + heat check
ADVANCED_SIZING_ENABLED      = _flag("INDIA_ADVANCED_SIZING_ENABLED")       # Calmar+Omega adaptive sizing
ELITE_BAYES_ENABLED          = _flag("INDIA_ELITE_BAYES_ENABLED")           # Bayesian elite tracker
OPTIMIZER_MULTIPARAMS        = _flag("INDIA_OPTIMIZER_MULTIPARAMS")         # 6-param walk-forward sweep

# -- RegimeSwitcher: BULL/BEAR/CHOPPY/HIGH_VOL_FEAR detection every 30 min ---
# Multiplies signal score based on detected regime to reduce choppy/fear trades
# and amplify high-conviction trend trades (target: Sharpe ≥ 2)
REGIME_SWITCHER_ENABLED      = bool(os.getenv("INDIA_REGIME_SWITCHER", "True") == "True")
