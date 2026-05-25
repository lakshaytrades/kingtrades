"""
crypto_config.py — Top-1% Crypto Trading Configuration
24/7 BTC/ETH/SOL trading via Alpaca Crypto API.

PROFIT-FIRST RULES (after -$4K loss event):
  1. Only 3 pairs: BTC, ETH, SOL — liquid, tight spreads
  2. Max $500 per trade (not $5000) — limit single-trade damage
  3. US_NIGHT session disabled — no trades in thin market
  4. 2% daily stop on crypto pool — stop fast, preserve capital
  5. Require 2/3 TF alignment as hard gate — no weak signals
  6. Consecutive loss pause: 2 losses → 3-hour break
"""

import os
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
ET  = ZoneInfo("America/New_York")

# ── Enabled flag ────────────────────────────────────────────────────────────────
CRYPTO_ENABLED: bool = os.getenv("CRYPTO_ENABLED", "True").lower() in ("true", "1", "yes")

# ── Trading pairs — ONLY liquid pairs with tight spreads ───────────────────────
# Removed AVAX, LINK, DOGE — low liquidity on Alpaca, wide spreads, unpredictable
CRYPTO_SYMBOLS = [
    "BTC/USD",   # Bitcoin  — leads the market, most liquid
    "ETH/USD",   # Ethereum — second by volume, reliable signals
    "SOL/USD",   # Solana   — high beta but liquid enough for clean entries
]

# ── Alpaca crypto endpoint ──────────────────────────────────────────────────────
CRYPTO_DATA_URL   = "https://data.alpaca.markets"
CRYPTO_STREAM_URL = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"

# ── Timeframes ─────────────────────────────────────────────────────────────────
CRYPTO_PRIMARY_TF      = "15Min"
CRYPTO_CONFIRM_TF      = "1Hour"
CRYPTO_MACRO_TF        = "4Hour"
CRYPTO_BAR_LIMIT       = 100

# ── Signal thresholds ──────────────────────────────────────────────────────────
# RAISED: need 75+ before bonuses (old 68 let weak signals through)
# With bonuses: MTF+10, FNG+10, vol+8 → elite setups reach 85-95
CRYPTO_MIN_SIGNAL_SCORE: float = 75.0   # raised from 68 — only trade quality setups
CRYPTO_HIGH_CONFIDENCE: float  = 88.0   # A+: all confluence present
CRYPTO_PREMIUM_SCORE: float    = 80.0   # A grade: solid setup

# ── Risk management — TIGHTENED after loss event ───────────────────────────────
CRYPTO_MAX_RISK_PCT: float     = 0.5    # 0.5% of crypto pool per trade (was 1.5% — 3x too big)
CRYPTO_DAILY_LOSS_PCT: float   = 2.0    # 2% daily stop on crypto pool (was 4%)
CRYPTO_MAX_POSITIONS: int      = 2      # max 2 concurrent positions (was 4)
CRYPTO_MAX_TRADES_DAY: int     = 6      # max 6 trades/day (was 12)

# ── ATR stop/target parameters ─────────────────────────────────────────────────
CRYPTO_ATR_PERIOD: int         = 14
CRYPTO_ATR_SL_MULT: float      = 1.5    # 1.5× ATR stop — keeps R:R healthy
CRYPTO_ATR_T1_MULT: float      = 2.0    # T1 at 2× ATR — quick partial lock
CRYPTO_ATR_T2_MULT: float      = 4.0    # T2 at 4× ATR — main target
CRYPTO_ATR_RUNNER_MULT: float  = 7.0    # runner at 7× ATR
CRYPTO_TRAIL_MULT: float       = 1.0    # trail stop 1× ATR
CRYPTO_BREAKEVEN_PCT: float    = 0.8    # move to BE after 0.8% gain (covers spread)

# ── Position sizing — HARD CAPS to prevent large single-trade losses ───────────
CRYPTO_MAX_NOTIONAL_USD: float = 500.0  # HARD CAP: max $500 per trade (was $5000 — 10× too big)
CRYPTO_MIN_NOTIONAL_USD: float = 25.0   # min $25 per order
CRYPTO_CAPITAL_PCT_MAX: float  = 8.0    # max 8% of crypto pool per position (was 25%)

# ── Exit percentages ────────────────────────────────────────────────────────────
CRYPTO_T1_EXIT_PCT: float  = 40.0   # exit 40% at T1 — lock profits fast
CRYPTO_T2_EXIT_PCT: float  = 30.0   # exit 30% at T2
CRYPTO_RUNNER_PCT: float   = 30.0   # run 30% — reduced from 40% (take profits)

# ── Session sizing multipliers ─────────────────────────────────────────────────
# US_NIGHT completely disabled — thin liquidity = noise = losses
CRYPTO_SESSION_MULTIPLIERS = {
    "US_PEAK":     1.2,   # 13:00-21:00 UTC (9AM-5PM ET) — full size but capped at $500
    "EU_MORNING":  1.0,   # 07:00-13:00 UTC — standard size
    "ASIA":        0.7,   # 00:00-07:00 UTC — reduced size, BTC-only setups
    "US_NIGHT":    0.0,   # 21:00-00:00 UTC — DISABLED: thin market, stop-hunt territory
}

# ── Session trading hours — which sessions are allowed ────────────────────────
CRYPTO_DISABLED_SESSIONS = {"US_NIGHT"}   # no new entries in these sessions

# ── BTC macro trend requirement ────────────────────────────────────────────────
# For altcoin LONG signals: BTC must be above 50-EMA on 4H
# Prevents longing ETH/SOL in a BTC downtrend
CRYPTO_REQUIRE_BTC_UPTREND_FOR_ALTS: bool = True

# ── BTC correlation thresholds ─────────────────────────────────────────────────
CRYPTO_BTC_DRAG_PCT: float     = 1.5    # drag alts when BTC -1.5% in 1h (was 2.0)
CRYPTO_BTC_BOOST_PCT: float    = 2.0    # boost alts when BTC +2% in 1h (was 3.0)
CRYPTO_ALT_SCORE_BOOST: float  = 8.0
CRYPTO_ALT_SCORE_DRAG: float   = 15.0  # increased drag — protect against BTC selloffs

# ── Fear & Greed thresholds ────────────────────────────────────────────────────
CRYPTO_FNG_EXTREME_FEAR: int   = 25    # contrarian LONG (strong buy signal)
CRYPTO_FNG_FEAR: int           = 40    # fear zone — longs get score boost
CRYPTO_FNG_GREED: int          = 60    # greed — reduce longs (tightened from 65)
CRYPTO_FNG_EXTREME_GREED: int  = 75    # no new longs (tightened from 80)

# ── Volume requirements ─────────────────────────────────────────────────────────
CRYPTO_VOLUME_SURGE_MULT: float    = 2.0   # 2x required for bonus (was 2.5)
CRYPTO_VOLUME_HIGH_CONV: float     = 3.5   # 3.5x = high conviction (was 4.0)
CRYPTO_VOLUME_MIN_GATE: float      = 1.0   # HARD gate: reject if < 1.0x avg volume

# ── MTF alignment — now a hard requirement ─────────────────────────────────────
# At least 1H must agree with 15m direction. 4H disagreement blocks the trade.
CRYPTO_REQUIRE_1H_ALIGNMENT: bool  = True   # 1H EMA must agree with 15m signal
CRYPTO_REQUIRE_4H_NO_CONFLICT: bool = True  # 4H must not be opposite direction

# ── Consecutive loss protection — TIGHTENED ────────────────────────────────────
CRYPTO_CONSEC_LOSS_LIMIT: int      = 2      # pause after 2 losses (was 3)
CRYPTO_PAUSE_AFTER_LOSSES_MIN: int = 180    # 3-hour pause (was 60 min — too short)

# ── Capital pool ────────────────────────────────────────────────────────────────
CRYPTO_CAPITAL_USD: float          = float(os.getenv("CRYPTO_CAPITAL_USD", "0"))
CRYPTO_CAPITAL_PCT_OF_TOTAL: float = float(os.getenv("CRYPTO_CAPITAL_PCT_OF_TOTAL", "20.0"))
# Reduced from 30% → 20% after loss event. With $96K: $19.2K crypto pool.
# Max trade: $500. Max daily loss: 2% × $19.2K = $384. That's manageable.

# ── RSI thresholds ─────────────────────────────────────────────────────────────
CRYPTO_RSI_OVERSOLD: float     = 32.0   # slightly stricter (was 30)
CRYPTO_RSI_OVERBOUGHT: float   = 68.0   # slightly stricter (was 70)
CRYPTO_RSI_EXTREME_OS: float   = 22.0
CRYPTO_RSI_EXTREME_OB: float   = 78.0

# ── Minimum candle range ────────────────────────────────────────────────────────
CRYPTO_MIN_RANGE_PCT: float    = 0.3    # candle must move 0.3% to count (filters noise)

# ── Short selling ──────────────────────────────────────────────────────────────
# Disabled shorts for now — longs only until bot is proven profitable
CRYPTO_SHORT_ENABLED: bool     = os.getenv("CRYPTO_SHORT_ENABLED", "False").lower() in ("true","1","yes")

# ── Scan interval ──────────────────────────────────────────────────────────────
CRYPTO_SCAN_INTERVAL: int      = 90    # scan every 90s (was 60s — slower = more selective)

# ── Exposure limits ────────────────────────────────────────────────────────────
CRYPTO_MAX_BTC_EXPOSURE_PCT: float = 60.0   # BTC can be 60% of crypto pool
CRYPTO_MAX_ALT_EXPOSURE_PCT: float = 20.0   # each alt max 20% of crypto pool

# ── Daily target ───────────────────────────────────────────────────────────────
CRYPTO_DAILY_TARGET_PCT: float = 1.5    # 1.5%/day on crypto pool = ~33%/month

# ── News blackout keywords ─────────────────────────────────────────────────────
CRYPTO_NEWS_BLACKOUT_KEYWORDS = [
    "SEC", "CFTC", "hack", "exploit", "rug pull", "exchange halt",
    "regulatory", "ban", "shutdown", "insolvency",
]
