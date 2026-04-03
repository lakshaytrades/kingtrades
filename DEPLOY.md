# DEPLOY.md — NSE Momentum Groww AI Bot
## Full Production Deployment Guide

> ⚠️ **REAL MONEY BOT** — Follow every step carefully. One mistake can cause losses.
> Start with minimum capital (₹5,000–₹10,000) and verify everything works before scaling.

---

## OVERVIEW

The bot runs on a **UK VPS (Ubuntu)** in UTC timezone.  
All trading logic uses **IST (Asia/Kolkata = UTC+5:30)**.  
The bot auto-starts at 8:45 AM IST, trades 9:15 AM–3:20 PM IST, shuts down at 3:30 PM IST.

---

## PHASE 1 — SERVER SETUP

### 1.1 Recommended VPS Specs
- **Provider**: DigitalOcean, Vultr, Hetzner, or Linode (UK/EU region for low latency to IST)
- **OS**: Ubuntu 22.04 LTS
- **RAM**: 2 GB minimum (4 GB recommended)
- **CPU**: 2 vCPU
- **Disk**: 40 GB SSD
- **Cost**: ~$12–$20/month

### 1.2 Initial Server Hardening
```bash
# Connect to your VPS
ssh root@YOUR_VPS_IP

# Create a non-root user
adduser tradebot
usermod -aG sudo tradebot

# Switch to tradebot user
su - tradebot

# Update packages
sudo apt update && sudo apt upgrade -y

# Install essential tools
sudo apt install -y git wget curl unzip htop screen tmux ufw fail2ban

# Basic firewall (only allow SSH)
sudo ufw allow OpenSSH
sudo ufw enable
```

### 1.3 Install Python 3.11
```bash
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Verify
python3.11 --version
# Should show: Python 3.11.x
```

### 1.4 Install Chrome + ChromeDriver (for Groww TOTP auto-login)
```bash
# Install Chrome
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb
google-chrome --version

# Install ChromeDriver (webdriver-manager handles this automatically at runtime)
# But install dependencies:
sudo apt install -y chromium-chromedriver xvfb
```

---

## PHASE 2 — BOT INSTALLATION

### 2.1 Clone the Repository
```bash
cd /home/tradebot
git clone https://github.com/lakshaytrades/kingtrades.git
cd kingtrades
git checkout claude/nse-momentum-groww-bot-v8Rma
```

### 2.2 Create Python Virtual Environment
```bash
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Verify critical packages
python -c "import growwapi; print('growwapi OK')"
python -c "import pandas_ta; print('pandas_ta OK')"
python -c "import anthropic; print('anthropic OK')"
python -c "from zoneinfo import ZoneInfo; print('zoneinfo OK')"
```

### 2.3 Create Environment File
```bash
cp .env.example .env
nano .env
```

Fill in ALL values:
```env
# ── Groww API (CRITICAL) ────────────────────────────────────
GROWW_AUTH_TOKEN=your_groww_auth_token_here
GROWW_CLIENT_ID=your_groww_client_id_here
GROWW_CLIENT_SECRET=your_groww_client_secret_here
GROWW_EMAIL=your_groww_registered_email@gmail.com
GROWW_PASSWORD=your_groww_account_password
GROWW_TOTP_SECRET=your_32_char_totp_secret_key

# ── Telegram Bot ────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID=123456789

# ── Claude AI (optional but recommended) ────────────────────
ANTHROPIC_API_KEY=sk-ant-api03-...

# ── News API (optional) ─────────────────────────────────────
NEWS_API_KEY=your_newsapi_key
ALPHA_VANTAGE_KEY=your_alphavantage_key

# ── Trading Config ──────────────────────────────────────────
LIVE_TRADING_ENABLED=False          # START WITH FALSE!
MAX_DAILY_CAPITAL=10000             # Start small: ₹10,000
MAX_RISK_PER_TRADE_PCT=0.5
DAILY_LOSS_LIMIT_PCT=2.0

# ── Logging ─────────────────────────────────────────────────
LOG_LEVEL=INFO
LOG_DIR=logs
TIMEZONE=Asia/Kolkata
```

```bash
# Protect the .env file
chmod 600 .env
```

---

## PHASE 3 — GROWW API SETUP

### 3.1 Get Groww API Token
1. Log into Groww web: https://groww.in
2. Go to: Profile → Settings → API Access
3. Generate API key / auth token
4. Copy the token → paste into `GROWW_AUTH_TOKEN` in `.env`
5. Note: Token expires daily — TOTP auto-refresh handles this

### 3.2 Get Groww TOTP Secret
This is the most important step for automated daily login:

**Method A (via Groww 2FA setup):**
1. Go to Groww → Profile → Security → Two-Factor Authentication
2. Click "Set up authenticator app"
3. Groww shows a QR code
4. Instead of scanning the QR, click "Can't scan? Enter manually"
5. Groww shows a 32-character secret key
6. Copy this → paste into `GROWW_TOTP_SECRET` in `.env`

**Verify TOTP works:**
```bash
source venv/bin/activate
python -c "
import pyotp, os
from dotenv import load_dotenv
load_dotenv()
secret = os.getenv('GROWW_TOTP_SECRET')
totp = pyotp.TOTP(secret)
print('Current TOTP:', totp.now())
print('Valid for:', totp.interval, 'seconds')
"
```
The 6-digit code should match your authenticator app.

### 3.3 Test Groww Connection
```bash
source venv/bin/activate
python -c "
from growwapi import GrowwAPI
import os
from dotenv import load_dotenv
load_dotenv()
api = GrowwAPI(os.getenv('GROWW_AUTH_TOKEN'))
balance = api.get_funds()
print('Balance:', balance)
"
```

---

## PHASE 4 — TELEGRAM SETUP

### 4.1 Create Telegram Bot
1. Open Telegram → search `@BotFather`
2. Send: `/newbot`
3. Choose name: `KingTradesBot` (or any name)
4. Choose username: `kingtrades_bot` (must end in `bot`)
5. BotFather gives you a token like: `1234567890:ABCdef...`
6. Copy → paste into `TELEGRAM_BOT_TOKEN`

### 4.2 Get Your Chat ID
```bash
source venv/bin/activate
python -c "
import requests, os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('TELEGRAM_BOT_TOKEN')
# First, send any message to your bot in Telegram
url = f'https://api.telegram.org/bot{token}/getUpdates'
r = requests.get(url).json()
print(r)
# Look for: result[0]['message']['chat']['id']
"
```
Copy the chat ID → paste into `TELEGRAM_CHAT_ID`

### 4.3 Test Telegram
```bash
source venv/bin/activate
python -c "
from alerts_telegram import TelegramAlerter
import asyncio
async def test():
    alerter = TelegramAlerter()
    await alerter.send_message('🤖 KingTrades bot connection test — OK!')
asyncio.run(test())
"
```
You should receive the message on Telegram.

---

## PHASE 5 — DRY RUN TESTING

### 5.1 Validate Configuration
```bash
source venv/bin/activate
python config.py
# Should show: ✅ Configuration valid
# Should show: 🔒 DISABLED (safe mode)
```

### 5.2 Test Individual Modules
```bash
# Test IST timezone
python utils.py

# Test data fetching
python -c "
from data_fetch_groww import GrowwDataFetcher
import os
from dotenv import load_dotenv
load_dotenv()
f = GrowwDataFetcher(os.getenv('GROWW_AUTH_TOKEN'))
df = f.get_ohlcv('RELIANCE', '5m', days=1)
print(df.tail(3))
"

# Test signal generation (paper mode)
python -c "
from signal_generator import SignalGenerator
from data_fetch_groww import GrowwDataFetcher
import os
from dotenv import load_dotenv
load_dotenv()
fetcher = GrowwDataFetcher(os.getenv('GROWW_AUTH_TOKEN'))
gen = SignalGenerator(fetcher)
sig = gen.generate_signal('RELIANCE')
if sig: print(sig.summary())
else: print('No signal (normal — filter working)')
"
```

### 5.3 Run Full Paper Trading Test (1 week minimum)
```bash
# Make sure LIVE_TRADING_ENABLED=False in .env
source venv/bin/activate
python main.py
```

Watch the logs:
```bash
tail -f logs/trading_$(date +%Y-%m-%d).log
```

Expected output:
```
[IST 2026-04-03 08:45:00] Starting KingTrades Bot
[IST 2026-04-03 08:45:01] TOTP login successful
[IST 2026-04-03 09:00:00] Overnight analysis complete | Bias: BULLISH (+35)
[IST 2026-04-03 09:15:00] Market OPEN — scanning watchlist
[IST 2026-04-03 09:17:23] SIGNAL: LONG RELIANCE | Grade A | Score 84
[IST 2026-04-03 09:17:23] PAPER TRADE (live disabled): BUY RELIANCE x12 @ ₹2847.50
```

### 5.4 Paper Trade Checklist
Run paper trading for **5 trading days minimum** and verify:
- [ ] Bot starts at 8:45 AM IST
- [ ] TOTP login succeeds
- [ ] Market open detected at exactly 9:15 AM IST
- [ ] Signals generated only during POWER HOURS
- [ ] No signals during 11:00 AM–1:00 PM (midday chop)
- [ ] All positions auto-closed by 3:20 PM IST
- [ ] Bot shuts down at 3:30 PM IST
- [ ] Telegram alerts arriving
- [ ] Logs showing IST timestamps (not UTC)
- [ ] Daily P&L report sent to Telegram by 3:45 PM IST

---

## PHASE 6 — GO LIVE CHECKLIST

**Only proceed if ALL paper trade checks pass.**

### 6.1 Pre-Live Checklist
- [ ] Paper traded for minimum 5 days
- [ ] Win rate in paper trading: ≥ 55%
- [ ] No crashes or errors in logs
- [ ] Telegram alerts working perfectly
- [ ] Daily loss limit circuit breaker tested
- [ ] Emergency `/kill` command tested
- [ ] Starting capital ready: recommend ₹10,000–₹25,000 initially

### 6.2 Enable Live Trading
```bash
nano .env
# Change: LIVE_TRADING_ENABLED=True
# Set capital: MAX_DAILY_CAPITAL=10000
```

### 6.3 First Live Day Protocol
1. Start bot at 9:00 AM IST: `python main.py`
2. Watch the first 30 minutes manually
3. Check each trade alert on Telegram
4. Keep Groww app open on your phone to monitor positions
5. Be ready to `/kill` if anything looks wrong
6. After first day, review P&L in EOD report

---

## PHASE 7 — PRODUCTION SYSTEMD SERVICE

Run the bot as a persistent system service that auto-restarts on crashes.

### 7.1 Create Systemd Service
```bash
sudo nano /etc/systemd/system/kingtrades.service
```

Paste:
```ini
[Unit]
Description=KingTrades NSE Momentum Bot
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=30
User=tradebot
WorkingDirectory=/home/tradebot/kingtrades
Environment=PATH=/home/tradebot/kingtrades/venv/bin:/usr/bin:/bin
ExecStart=/home/tradebot/kingtrades/venv/bin/python main.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=kingtrades

# Resource limits
MemoryMax=1G
CPUQuota=80%

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable kingtrades
sudo systemctl start kingtrades

# Check status
sudo systemctl status kingtrades

# View logs
sudo journalctl -u kingtrades -f
```

### 7.2 Auto-Restart Policy
The service auto-restarts after crashes (`Restart=always`).  
The bot handles its own scheduling, so systemd just keeps it alive.

---

## PHASE 8 — MONITORING & MAINTENANCE

### 8.1 Daily Monitoring Checklist
Every trading day:
- [ ] Receive morning brief on Telegram by 8:30 AM IST
- [ ] Watch bot start confirmation at 9:15 AM IST
- [ ] Check at least 2–3 trade alerts during the day
- [ ] Receive EOD P&L report by 3:45 PM IST
- [ ] Review `logs/trading_YYYY-MM-DD.log` for any errors

### 8.2 Key Log Files
```
logs/
├── trading_YYYY-MM-DD.log      # Daily trading log (all IST timestamps)
├── trades/                     # Individual trade records
├── performance/                # Daily/weekly performance stats
data/
├── candle_store.db             # Growing historical data (gets smarter daily)
├── trade_journal.db            # SEBI-compliant trade records
├── economic_calendar.db        # Event impact learning
logs/adaptive_config.json       # Self-learning parameters (updated nightly)
trainer_results/                # Backtest results from weekly trainer
```

### 8.3 Weekly Review
Every Sunday evening, the continuous learner auto-runs a strategy review.
Check `trainer_results/` for the latest optimization report.

Manual review command:
```bash
source venv/bin/activate
python trainer.py --report
```

### 8.4 Update Bot Code
```bash
cd /home/tradebot/kingtrades
git pull origin claude/nse-momentum-groww-bot-v8Rma
pip install -r requirements.txt  # If dependencies changed
sudo systemctl restart kingtrades
```

### 8.5 Telegram Commands (during trading hours)
| Command | Action |
|---------|--------|
| `/kill` | Emergency stop — close ALL positions immediately |
| `/status` | Current P&L, open positions, bot state |
| `/pause` | Pause new entries (holds existing positions) |
| `/resume` | Resume after pause |
| `/watchlist` | Show current trading watchlist |
| `/report` | Force EOD report now |

---

## PHASE 9 — TROUBLESHOOTING

### Bot won't start
```bash
sudo journalctl -u kingtrades -n 50
# Check for Python errors, missing .env values, import errors
```

### TOTP login failing
```bash
python -c "import pyotp, os; from dotenv import load_dotenv; load_dotenv(); print(pyotp.TOTP(os.getenv('GROWW_TOTP_SECRET')).now())"
# If error: check GROWW_TOTP_SECRET in .env
# Compare output with your authenticator app
```

### No signals being generated
This is NORMAL. The bot is designed to take only high-quality trades.
On some days, 0 trades is the correct answer.
Check: `grep "FILTERED" logs/trading_$(date +%Y-%m-%d).log | tail -20`

### Trades not executing (paper mode)
```bash
grep "LIVE_TRADING" logs/trading_$(date +%Y-%m-%d).log
# If showing DISABLED — set LIVE_TRADING_ENABLED=True in .env when ready
```

### Wrong time / trades at wrong hours
```bash
python -c "from utils import get_current_ist_time; print(get_current_ist_time())"
# Must show IST time (UTC+5:30), not server UTC time
```

### Memory growing over time
```bash
ps aux | grep python
# If RAM > 1 GB, restart: sudo systemctl restart kingtrades
# The candle store DB grows over time — this is expected (historical data)
```

### Groww API token expired
The TOTP auto-login refreshes this at 8:45 AM IST daily.
If it fails mid-day:
```bash
# Manually refresh via Telegram:
/kill  # Stop trading safely
# Then restart:
sudo systemctl restart kingtrades
```

---

## PHASE 10 — SCALING UP

After 2–4 weeks of successful live trading with minimum capital:

### 10.1 Increase Capital Gradually
```
Week 1–2: ₹10,000 (verify everything works)
Week 3–4: ₹25,000 (if win rate ≥ 55%)
Month 2:  ₹50,000 (if Sharpe > 1.5 and drawdown < 5%)
Month 3+: ₹1,00,000 (if monthly return ≥ 5% net)
```

### 10.2 Performance Benchmarks Before Scaling
- Win rate: ≥ 55% (target 65–75%)
- Sharpe ratio: ≥ 1.5
- Max drawdown: < 8%
- Monthly return: ≥ 5% net (after brokerage + taxes)
- Consecutive loss streaks: ≤ 3 before circuit breaker triggers

### 10.3 Configuration Scaling
```env
MAX_DAILY_CAPITAL=100000     # ₹1 lakh
MAX_RISK_PER_TRADE_PCT=0.5   # Keep at 0.5% — don't increase risk
MAX_POSITIONS=8              # Increase position slots
```

---

## SECURITY REMINDERS

- **NEVER** share your `.env` file or TOTP secret with anyone
- **NEVER** commit `.env` to git (`.gitignore` already excludes it)
- The bot stores auth tokens in memory only — never on disk
- Use a dedicated Groww account for bot trading if possible
- Enable 2FA on your VPS SSH access
- Rotate Groww API credentials every 90 days

---

## EXPECTED PERFORMANCE TARGETS

| Metric | Minimum | Target |
|--------|---------|--------|
| Win Rate | 55% | 65–75% |
| Monthly Return | 3% net | 5–8% net |
| Sharpe Ratio | 1.0 | ≥ 1.5 |
| Max Drawdown | < 10% | < 6% |
| Trades/Day | 1–3 | 2–4 |
| Avg Hold Time | 30–90 min | 45–75 min |

*Targets based on 18+ years NSE intraday experience and backtests.  
Past performance does not guarantee future results.*

---

*Last updated: 2026-04-03 | KingTrades NSE Momentum Bot*
