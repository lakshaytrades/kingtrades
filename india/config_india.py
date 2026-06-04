"""
config_india.py — KingTrades India Bot Configuration
Dhan broker | NSE equity | IST timezone | INR capital
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

# ── Telegram (shared with US bot if using same channel) ──────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# ── Signal thresholds ────────────────────────────────────────────────────────
MIN_SIGNAL_SCORE      = float(os.getenv("INDIA_MIN_SIGNAL_SCORE",     "60.0"))
FINAL_EXEC_MIN_SCORE  = float(os.getenv("INDIA_FINAL_EXEC_MIN_SCORE", "70.0"))
GRAND_SLAM_MIN_SCORE  = float(os.getenv("INDIA_GRAND_SLAM_MIN_SCORE", "82.0"))

# ── ATR-based SL / TP ────────────────────────────────────────────────────────
ATR_SL_MULTIPLIER      = float(os.getenv("INDIA_ATR_SL_MULTIPLIER",     "1.5"))
ATR_TP_MULTIPLIER      = float(os.getenv("INDIA_ATR_TP_MULTIPLIER",     "3.0"))
BREAKEVEN_TRIGGER_PCT  = float(os.getenv("INDIA_BREAKEVEN_TRIGGER_PCT", "0.10"))

# ── Live trading gate ────────────────────────────────────────────────────────
LIVE_TRADING_ENABLED = os.getenv("INDIA_LIVE_TRADING_ENABLED", "False") == "True"

# ── Feature flags (all on by default) ───────────────────────────────────────
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

# ── Dhan exchange constants ──────────────────────────────────────────────────
EXCHANGE        = "NSE_EQ"
PRODUCT_TYPE    = "INTRADAY"   # MIS equivalent on Dhan
YFINANCE_SUFFIX = ".NS"        # for yfinance historical data

# ── Scan interval ────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300   # scan watchlist every 5 minutes
