# NSE Momentum Groww AI Bot

## ⚠️ CRITICAL RISK WARNING
```
THIS BOT PLACES REAL ORDERS WITH REAL MONEY ON GROWW.
THERE IS NO PAPER TRADING MODE.
TRADING INVOLVES SUBSTANTIAL RISK OF LOSS.
NO STRATEGY GUARANTEES 5% MONTHLY RETURNS.
BACKTESTS DO NOT PREDICT LIVE PERFORMANCE.
START WITH VERY SMALL CAPITAL ONLY (₹10,000–₹25,000 MAX TO START).
MONITOR MANUALLY FOR AT LEAST 2 WEEKS BEFORE SCALING UP.
THE AUTHOR ACCEPTS NO LIABILITY FOR TRADING LOSSES.
```

---

## Overview
Production-ready AI intraday momentum trading bot for NSE Indian equity markets.
- **Broker**: Groww (via official `growwapi` SDK)
- **Strategy**: Multi-timeframe momentum with 20+ chart patterns
- **Server**: Runs on UK/UTC server — ALL logic uses IST (Asia/Kolkata)
- **Trading Window**: 9:15 AM – 3:30 PM IST strictly

---

## Prerequisites
- Python 3.11+
- Chrome browser (for Groww TOTP login automation)
- Groww account with API access enabled
- Telegram bot (for notifications and kill-switch)

---

## Groww API Token Setup

### Step 1: Enable API Access on Groww
1. Log in at [groww.in](https://groww.in)
2. Navigate to **Profile → Settings → API Access**
3. Enable "Trade API" access
4. Visit [groww.in/trade-api](https://groww.in/trade-api) to generate your credentials
5. Note your **Client ID**, **Client Secret**, and **Auth Token**

### Step 2: Set Up TOTP for Auto-Login
1. Enable 2FA on your Groww account
2. When setting up 2FA, instead of just scanning the QR code, also click **"Show secret key"**
3. Copy the TOTP secret key (looks like: `JBSWY3DPEHPK3PXP`)
4. Store this as `GROWW_TOTP_SECRET` in your `.env` file
5. The bot uses this to auto-generate TOTP codes and refresh the daily auth token

### Step 3: Token Refresh
- Groww auth tokens expire daily
- The bot auto-refreshes the token every morning at **8:45 AM IST** using TOTP
- If running on a UK server (UTC), the bot correctly converts to IST for refresh timing

### Security Warning
```
NEVER share your TOTP secret with anyone.
NEVER commit .env to git.
NEVER store credentials in code files.
Treat GROWW_TOTP_SECRET like your bank PIN.
```

---

## Telegram Bot Setup

### Step 1: Create Bot
1. Open Telegram → Search `@BotFather`
2. Send `/newbot` → Follow prompts → Get bot token
3. Save token as `TELEGRAM_BOT_TOKEN`

### Step 2: Get Chat ID
1. Send any message to your new bot
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat": {"id": XXXXXXXX}` — that's your `TELEGRAM_CHAT_ID`

### Telegram Commands
| Command | Description |
|---------|-------------|
| `/kill` | Emergency stop + square off all positions |
| `/status` | Current P&L and positions |
| `/pause` | Pause new entries |
| `/resume` | Resume after pause |
| `/watchlist` | Show current watchlist |
| `/report` | Force EOD summary report |

---

## Installation

```bash
# Clone the repository
git clone https://github.com/lakshaytrades/nse-intraday-momentum-groww-ai-bot.git
cd nse-intraday-momentum-groww-ai-bot

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
nano .env  # Fill in all required values
```

---

## Configuration

Edit `.env` with your credentials:

```bash
# Groww API
GROWW_AUTH_TOKEN=your_token
GROWW_CLIENT_ID=your_client_id
GROWW_CLIENT_SECRET=your_secret
GROWW_EMAIL=your@email.com
GROWW_PASSWORD=your_password
GROWW_TOTP_SECRET=your_totp_secret

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# CRITICAL — set False to start safely
LIVE_TRADING_ENABLED=False
MAX_DAILY_CAPITAL=50000
```

---

## Running the Bot

### First Run — Dry Mode (Recommended)
```bash
# Start with LIVE_TRADING_ENABLED=False in .env
python main.py
```
This will run all logic, generate Telegram alerts, but NOT place real orders.

### Live Trading Mode
```bash
# Only after testing! Set in .env:
# LIVE_TRADING_ENABLED=True
python main.py
```

### Trainer (Offline Backtesting)
```bash
# Download historical data and optimize strategy
python trainer.py --symbols RELIANCE,TCS,INFY --days 365
python trainer.py --full-optimization
```

### Dashboard Only
```bash
python dashboard.py
```

---

## Timezone Note for UK Server

This bot is designed to run on a UK (UTC) server but trade Indian markets (IST = UTC+5:30):

```
UK Server Time: 03:45 AM UTC  →  Market Open: 09:15 AM IST
UK Server Time: 10:00 AM UTC  →  Market Close: 03:30 PM IST
```

**The bot handles this automatically** using `utils.py` timezone functions.
All logs, alerts, and timestamps show IST time regardless of server location.

Set a cron job on your UK server:
```bash
# Start bot at 8:30 AM IST (3:00 AM UTC)
0 3 * * 1-5 cd /path/to/bot && /path/to/venv/bin/python main.py >> logs/cron.log 2>&1
```

---

## Deployment (Cloud/VPS)

### AWS EC2 (Recommended)
```bash
# Launch Ubuntu 22.04 t3.medium (UK region)
# Install Python 3.11, Chrome, ChromeDriver

# As service (systemd)
sudo nano /etc/systemd/system/kingtrades.service
```

```ini
[Unit]
Description=NSE Momentum Groww Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/kingtrades
ExecStart=/home/ubuntu/kingtrades/venv/bin/python main.py
Restart=on-failure
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable kingtrades
sudo systemctl start kingtrades
sudo journalctl -u kingtrades -f  # Follow logs
```

### Google Cloud / DigitalOcean
Same process — use UK/Europe region for low latency to Groww API.

---

## File Structure

```
kingtrades/
├── main.py                # Bot orchestrator + IST market hours
├── config.py              # All configuration
├── utils.py               # IST timezone utilities (CRITICAL)
├── auth_groww.py          # TOTP auto-login for Groww
├── data_fetch_groww.py    # Market data fetching
├── pattern_recognition.py # Chart pattern detection (20+ patterns)
├── signal_generator.py    # Multi-timeframe momentum signals
├── risk_manager.py        # Risk management engine
├── execution_groww.py     # Live order execution
├── alerts_telegram.py     # Telegram notifications + charts
├── news_filter.py         # News/sentiment filter
├── watchlist_manager.py   # Dynamic watchlist
├── multi_timeframe.py     # MTF analysis (5m/15m/1h)
├── trainer.py             # Backtesting + optimization
├── dashboard.py           # Performance dashboard
├── logs/                  # Trading logs (IST timestamps)
├── requirements.txt
├── .env.example           # Template — copy to .env
├── .gitignore
├── CLAUDE.md              # Permanent project specs
└── README.md
```

---

## Risk Management Summary

| Parameter | Value |
|-----------|-------|
| Max risk per trade | 0.5–1% of capital |
| Daily loss limit | 2% of capital |
| Max simultaneous positions | 5–10 |
| Stop-loss method | ATR-based (1.5x ATR) |
| Target | 2:1 to 3:1 R:R |
| Trailing stop | Activated at 1x ATR profit |
| Circuit breaker | Nifty >2% move or daily loss hit |

---

## Legal Disclaimer

This software is provided for **educational and informational purposes only**.
- Trading in financial markets involves substantial risk of loss
- Past performance and backtests do not predict future results
- The 5% monthly return target is an optimization goal, not a guarantee
- Always consult a SEBI-registered investment advisor before trading
- The authors accept **no liability** for financial losses
- Ensure compliance with Indian tax laws (STCG, STT, brokerage)
- This tool is for use by the account owner only — not for managing others' money

---

## Support

For issues, questions, or improvements:
- GitHub Issues: [lakshaytrades/kingtrades](https://github.com/lakshaytrades/kingtrades)
- Monitor bot logs: `tail -f logs/trading_$(date +%Y-%m-%d).log`

---

*Built with 18+ years of NSE intraday trading experience. Use responsibly.*
