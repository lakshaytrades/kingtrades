"""
crypto_config.py — Top-1% Crypto Trading Configuration
24/7 BTC/ETH/SOL trading via Alpaca Crypto API.

Key differences from stock config:
  - 24/7 operation (no market hours)
  - Fractional/notional orders (buy $X worth)
  - Higher volatility → wider ATR stops, bigger targets
  - No PDT rule
  - Session-based sizing (US peak hours most liquid)
  - BTC correlation awareness for altcoins
"""

import os
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
ET  = ZoneInfo("America/New_York")

# ── Enabled flag ────────────────────────────────────────────────────────────────
CRYPTO_ENABLED: bool = os.getenv("CRYPTO_ENABLED", "True").lower() in ("true", "1", "yes")

# ── Trading pairs (Alpaca format: BTC/USD, ETH/USD, SOL/USD) ───────────────────
CRYPTO_SYMBOLS = [
    "BTC/USD",   # Bitcoin    — highest liquidity, leads the market
    "ETH/USD",   # Ethereum   — second by volume, DeFi/AI narrative
    "SOL/USD",   # Solana     — high beta, 3-5x BTC moves on breakouts
    "AVAX/USD",  # Avalanche  — ecosystem momentum
    "LINK/USD",  # Chainlink  — oracle narrative, BTC correlated
    "DOGE/USD",  # Dogecoin   — retail sentiment, viral momentum
]

# ── Alpaca crypto endpoint ──────────────────────────────────────────────────────
CRYPTO_DATA_URL   = "https://data.alpaca.markets"
CRYPTO_STREAM_URL = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"

# ── Timeframes ─────────────────────────────────────────────────────────────────
CRYPTO_PRIMARY_TF      = "15Min"   # 15-min candles for entry signals
CRYPTO_CONFIRM_TF      = "1Hour"   # 1-hour for trend confirmation
CRYPTO_MACRO_TF        = "4Hour"   # 4-hour macro direction
CRYPTO_BAR_LIMIT       = 100       # candles to fetch per request

# ── Signal thresholds ──────────────────────────────────────────────────────────
CRYPTO_MIN_SIGNAL_SCORE: float = 68.0   # slightly lower than stocks (24/7 = more setups)
CRYPTO_HIGH_CONFIDENCE: float  = 80.0
CRYPTO_PREMIUM_SCORE: float    = 74.0

# ── Risk management (per crypto trade) ────────────────────────────────────────
CRYPTO_MAX_RISK_PCT: float     = 1.5    # 1.5% of crypto capital per trade
CRYPTO_DAILY_LOSS_PCT: float   = 4.0   # 4% daily loss limit on crypto pool
CRYPTO_MAX_POSITIONS: int      = 4     # max concurrent crypto positions
CRYPTO_MAX_TRADES_DAY: int     = 15    # max crypto trades per 24h window

# ── ATR parameters (crypto is 3x more volatile than stocks) ───────────────────
CRYPTO_ATR_PERIOD: int         = 14
CRYPTO_ATR_SL_MULT: float      = 1.5   # wider stop — crypto has bigger candles
CRYPTO_ATR_T1_MULT: float      = 2.5   # T1 target: 2.5x ATR
CRYPTO_ATR_T2_MULT: float      = 5.0   # T2 target: 5x ATR — let runners run
CRYPTO_ATR_RUNNER_MULT: float  = 8.0   # runner: 8x ATR (crypto can 5-10% in hours)
CRYPTO_TRAIL_MULT: float       = 1.0   # trail stop: 1x ATR (wider for crypto noise)
CRYPTO_BREAKEVEN_PCT: float    = 0.5   # move BE after 0.5% gain (crypto noise level)

# ── Position sizing ────────────────────────────────────────────────────────────
CRYPTO_MAX_NOTIONAL_USD: float = 5000.0   # max single position in USD notional
CRYPTO_MIN_NOTIONAL_USD: float = 10.0     # min order size ($10)
CRYPTO_CAPITAL_PCT_MAX: float  = 25.0     # max 25% of crypto capital in one coin

# ── Exit percentages ────────────────────────────────────────────────────────────
CRYPTO_T1_EXIT_PCT: float  = 35.0   # exit 35% at T1
CRYPTO_T2_EXIT_PCT: float  = 25.0   # exit 25% at T2
CRYPTO_RUNNER_PCT: float   = 40.0   # run 40% to the moon

# ── Session sizing multipliers (UTC hours) ─────────────────────────────────────
# Crypto trades 24/7 but volume peaks during US/EU overlap hours
CRYPTO_SESSION_MULTIPLIERS = {
    "US_PEAK":     2.0,   # 13:00-21:00 UTC (9AM-5PM ET) — highest volume
    "EU_MORNING":  1.5,   # 07:00-13:00 UTC — EU overlap, good liquidity
    "ASIA":        1.2,   # 00:00-07:00 UTC — Asian session, decent BTC liquidity
    "US_NIGHT":    0.8,   # 21:00-00:00 UTC — thin liquidity, smaller size
}

# ── BTC correlation thresholds (for altcoin signals) ──────────────────────────
# When BTC drops >2% in 1h, suppress altcoin LONG signals
CRYPTO_BTC_DRAG_PCT: float     = 2.0
# When BTC rises >3% in 1h, boost altcoin LONG score
CRYPTO_BTC_BOOST_PCT: float    = 3.0
CRYPTO_ALT_SCORE_BOOST: float  = 8.0   # boost pts when BTC rising strongly
CRYPTO_ALT_SCORE_DRAG: float   = 12.0  # drag pts when BTC falling hard

# ── Fear & Greed thresholds ────────────────────────────────────────────────────
CRYPTO_FNG_EXTREME_FEAR: int   = 25    # extreme fear → contrarian LONG bias
CRYPTO_FNG_FEAR: int           = 40    # fear zone
CRYPTO_FNG_GREED: int          = 65    # greed zone → reduce LONG size
CRYPTO_FNG_EXTREME_GREED: int  = 80    # extreme greed → no new LONGs

# ── Volume surge (crypto needs higher threshold — 24/7 noise) ──────────────────
CRYPTO_VOLUME_SURGE_MULT: float    = 2.5   # 2.5x average to count as surge
CRYPTO_VOLUME_HIGH_CONV: float     = 4.0   # 4x = high conviction

# ── Consecutive loss protection ────────────────────────────────────────────────
CRYPTO_CONSEC_LOSS_LIMIT: int      = 3
CRYPTO_PAUSE_AFTER_LOSSES_MIN: int = 60    # pause 60 minutes after 3 losses

# ── Capital pool ────────────────────────────────────────────────────────────────
# 0 = use CRYPTO_CAPITAL_PCT_OF_TOTAL × total account balance
# >0 = fixed USD amount for crypto trades
CRYPTO_CAPITAL_USD: float       = float(os.getenv("CRYPTO_CAPITAL_USD", "0"))
CRYPTO_CAPITAL_PCT_OF_TOTAL: float = float(os.getenv("CRYPTO_CAPITAL_PCT_OF_TOTAL", "30.0"))
# 30% of total account to crypto — 70% stays in stocks
# With $5k total: $1.5k crypto, $3.5k stocks

# ── RSI thresholds (crypto-specific — wider range) ────────────────────────────
CRYPTO_RSI_OVERSOLD: float     = 30.0
CRYPTO_RSI_OVERBOUGHT: float   = 70.0
CRYPTO_RSI_EXTREME_OS: float   = 20.0
CRYPTO_RSI_EXTREME_OB: float   = 80.0

# ── Minimum price movement for valid signal ───────────────────────────────────
CRYPTO_MIN_RANGE_PCT: float    = 0.5    # candle range must be > 0.5% to count

# ── Short selling for crypto ──────────────────────────────────────────────────
# Alpaca supports crypto shorts (market-neutral, no borrowing fee)
CRYPTO_SHORT_ENABLED: bool     = os.getenv("CRYPTO_SHORT_ENABLED", "True").lower() in ("true","1","yes")

# ── Scan interval (seconds) ───────────────────────────────────────────────────
CRYPTO_SCAN_INTERVAL: int      = 60    # scan every 60 seconds (24/7)

# ── Max capital per coin (correlation risk) ───────────────────────────────────
CRYPTO_MAX_BTC_EXPOSURE_PCT: float = 50.0   # max 50% of crypto pool in BTC
CRYPTO_MAX_ALT_EXPOSURE_PCT: float = 25.0   # max 25% per altcoin

# ── Whale alert threshold (notional USD equivalent) ──────────────────────────
CRYPTO_WHALE_THRESHOLD_USD: float  = 1_000_000.0   # $1M+ move = whale activity

# ── News blackout ─────────────────────────────────────────────────────────────
CRYPTO_NEWS_BLACKOUT_KEYWORDS = [
    "SEC", "CFTC", "hack", "exploit", "rug pull", "exchange halt",
    "regulatory", "ban", "shutdown", "insolvency",
]

# ── Profit targets ────────────────────────────────────────────────────────────
CRYPTO_DAILY_TARGET_PCT: float = 2.0    # 2%/day on crypto pool = ~60%/month compounded
