# CLAUDE.md — NSE Momentum Groww AI Bot
## Permanent Project Specifications (DO NOT MODIFY WITHOUT EXPLICIT APPROVAL)

---

## ⚠️ CRITICAL: LIVE TRADING BOT — REAL MONEY AT RISK
This bot places **REAL orders with real money** on Groww. There is **NO paper trading mode**.
All changes to execution, risk management, or order logic must be reviewed carefully.
**Start with very small capital only. Monitor manually at first.**

---

## Project Identity
- **Project Name**: NSE Momentum Groww AI Bot
- **Purpose**: Production-ready AI intraday momentum trading bot for NSE Indian equity
- **Broker**: Groww (via official `growwapi` Python SDK)
- **Market**: NSE (National Stock Exchange of India) — Equity segment only
- **Trading Style**: Pure intraday momentum — no overnight positions
- **Experience Basis**: 18+ years intraday trading expertise since 2008

---

## ⏰ TIMEZONE — MOST CRITICAL REQUIREMENT

### The Problem
- **Server runs in UK (UTC)** — server local time is UTC
- **Indian markets run in IST (UTC+5:30)**
- Using server local time WILL cause trades at wrong times, missed market hours, wrong timestamps

### The Solution (MANDATORY)
- **ALL trading logic uses IST (Asia/Kolkata)**
- Use Python `zoneinfo` (Python 3.9+) or `pytz` for UTC→IST conversion
- **NEVER use `datetime.now()` or `datetime.utcnow()` without explicit timezone**
- Always use `get_current_ist_time()` from `utils.py`
- All logs, alerts, order timestamps → IST
- All market hours checks → IST

### IST Utility Functions (in `utils.py`)
```python
from utils import is_market_open_ist, get_current_ist_time, convert_to_ist
```

### IST Constants
```python
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN_IST = time(9, 15)
MARKET_CLOSE_IST = time(15, 30)
PRE_MARKET_START = time(9, 0)
POST_MARKET_END = time(16, 0)
```

---

## Broker Integration

### Groww API
- **SDK**: `growwapi` (pip install growwapi)
- **Class**: `GrowwAPI`
- **Auth**: `AUTH_TOKEN` from `.env` (env group: `trades secret`)
- **Token**: Daily TOTP-based auto-refresh using `auth_groww.py`
- **TOTP Setup**: Store TOTP secret in `GROWW_TOTP_SECRET` env var

### Groww Capabilities Used
1. Real-time quotes (`get_quote()`)
2. Historical 5-min OHLCV candles
3. Place intraday equity orders (MIS product type)
4. Modify/cancel orders
5. Get positions and holdings
6. Account balance/funds

### TOTP Auto-Login Flow
1. At 8:45 AM IST: bot auto-logs into Groww using email + password + TOTP
2. Fetches fresh auth token for the day
3. Stores token in memory (never on disk unencrypted)
4. Refreshes if token expires during trading hours

---

## Trading Window
- **Start**: 9:15 AM IST (strict — no pre-market trades)
- **End**: 3:30 PM IST (strict — all positions squared off by 3:20 PM)
- **Auto-shutdown**: Bot auto-stops at 3:30 PM IST
- **Square-off warning**: Alert at 3:15 PM IST if open positions exist
- **Emergency exit**: Kill-switch via Telegram command `/kill`

---

## Trading Strategy

### Core Approach: Multi-Timeframe Momentum
1. **Primary**: 5-min candles for entry signals
2. **Confirmation**: 15-min trend alignment
3. **Context**: 1-hour macro trend

### Signals & Patterns
- Breakout above resistance / below support with volume surge
- Bullish/Bearish engulfing candles
- Flag and pennant continuations
- VWAP deviation entries (price returning to VWAP after extension)
- RSI divergence + MACD crossover confluence
- Relative strength vs Nifty50 (stock > market momentum)
- Opening range breakout (9:15-9:30 AM high/low)
- Volume-weighted momentum scoring

### Indicator Stack
- RSI (14) — overbought/oversold with divergence
- MACD (12,26,9) — crossovers and histogram momentum
- ATR (14) — volatility-based SL/TP calculation
- VWAP — intraday fair value reference
- Bollinger Bands (20,2) — volatility squeeze signals
- EMA (9, 21, 50) — trend direction
- Volume SMA (20) — volume surge detection (>2x average)
- Stochastic (14,3,3) — momentum confirmation

### Multi-Timeframe Rules
- 5-min signal must align with 15-min trend direction
- 15-min must align with 1-hour macro direction
- All 3 timeframes aligned = high-confidence trade

---

## Risk Management

### Per-Trade Risk
- **Max risk per trade**: 0.5–1% of capital
- **Stop-loss**: ATR-based (1.5x ATR from entry)
- **Target**: 2:1 or 3:1 reward-to-risk (ATR-based)
- **Trailing stop**: Activated after 1x ATR profit, trails at 0.5x ATR

### Portfolio Risk
- **Max simultaneous positions**: 5–10
- **Daily loss limit**: 2% of daily capital
- **Circuit breaker triggers**:
  - Nifty moves >2% from open (pause new entries)
  - Daily loss limit hit (stop all new entries, hold existing)
  - 3 consecutive losses (pause 30 minutes)
  - Manual kill-switch via Telegram

### Position Sizing
- Kelly Criterion informed sizing (capped at 10% max per position)
- Auto-calculate based on available balance
- Adjusts for volatility (ATR-based scaling)

---

## Telegram Controls
- `/kill` — Emergency stop all trading + square off all positions
- `/status` — Current P&L, positions, bot state
- `/pause` — Pause new entries (hold existing)
- `/resume` — Resume after pause
- `/watchlist` — Show current watchlist
- `/report` — Force EOD report now

---

## Module Architecture

```
kingtrades/
├── main.py                # Orchestrator + IST-based market hours + auto-shutdown
├── config.py              # Central config + IST timezone settings
├── utils.py               # IST time utilities + helpers
├── auth_groww.py          # TOTP auto-login for Groww daily token refresh
├── data_fetch_groww.py    # Real-time quotes + OHLCV candles via growwapi
├── pattern_recognition.py # 20+ chart patterns + indicator analysis
├── signal_generator.py    # Multi-timeframe momentum signals
├── risk_manager.py        # ATR SL/TP, trailing stops, circuit breakers
├── execution_groww.py     # Live order placement/modification/cancellation
├── alerts_telegram.py     # Rich alerts with matplotlib chart images
├── news_filter.py         # Economic calendar + sentiment filter
├── watchlist_manager.py   # Dynamic liquid NSE stocks management
├── multi_timeframe.py     # 5-min + 15-min + 1-hr alignment engine
├── trainer.py             # Backtesting + walk-forward + adaptive optimization
├── dashboard.py           # Performance dashboard + EOD reports
├── requirements.txt
├── .env.example
├── .gitignore
├── CLAUDE.md
└── README.md
```

---

## Trainer Module Targets
- **Minimum target**: 5% net monthly return (after ~0.1% slippage + brokerage + taxes)
- **Win rate target**: >55%
- **Sharpe ratio target**: >1.5
- **Max drawdown limit**: <8%
- **Optimization method**: Walk-forward optimization (no look-ahead bias)
- **Adaptive learning**: Auto-adjust RSI thresholds, volume filters, pattern weights

---

## News & Sentiment Filter
- Skip signals 30 minutes before/after high-impact events
- Track: RBI decisions, GDP releases, Nifty earnings (Nifty 50 components)
- Sources: NewsAPI, economic calendar RSS feeds
- Sentiment scoring on stock-specific news

---

## Logging Standards
- **All timestamps in IST** (even on UK server)
- Log format: `[IST YYYY-MM-DD HH:MM:SS] [LEVEL] [MODULE] message`
- Log files: `logs/trading_YYYY-MM-DD.log`
- Trade journal: SQLite database with SEBI-compliant fields
- Performance metrics: logged daily to `logs/performance/`

---

## Security Standards
- Never hardcode credentials — always from environment variables
- `.env` file must NEVER be committed to git
- TOTP secret stored only in `.env`
- Auth tokens stored in memory only (never written to disk)
- All API calls use HTTPS
- Retry logic: exponential backoff (2s, 4s, 8s, 16s, max 4 retries)

---

## Environment Variables (Group: trades secret)
```
GROWW_AUTH_TOKEN
GROWW_CLIENT_ID
GROWW_CLIENT_SECRET
GROWW_EMAIL
GROWW_PASSWORD
GROWW_TOTP_SECRET
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
NEWS_API_KEY
ALPHA_VANTAGE_KEY
LIVE_TRADING_ENABLED
MAX_DAILY_CAPITAL
MAX_RISK_PER_TRADE_PCT
DAILY_LOSS_LIMIT_PCT
CUSTOM_WATCHLIST
LOG_LEVEL
LOG_DIR
TIMEZONE=Asia/Kolkata
```

---

## Compliance Notes
- All orders logged with exchange timestamp, order ID, symbol, quantity, price
- MIS (Margin Intraday Square-off) product type only — no delivery
- Square-off before 3:20 PM IST mandatory
- No overnight positions — pure intraday only

---

*Last Updated: 2026-04-02 | Specs are PERMANENT unless explicitly revised*
