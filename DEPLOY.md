# DEPLOY.md — NSE Momentum Groww AI Bot
## Local PC Deployment Guide (No VPS Required)

> ⚠️ **REAL MONEY BOT** — Follow every step. Start with ₹5,000–₹10,000 only.
> **Your PC must be ON and running during market hours (9:15 AM – 3:30 PM IST).**

---

## WHAT YOU NEED

| Requirement | Details |
|-------------|---------|
| Computer | Windows 10/11 or Ubuntu Linux |
| RAM | 4 GB minimum (8 GB recommended) |
| Internet | Stable broadband (not mobile hotspot) |
| Python | 3.11 (free to install) |
| Groww Account | With API access enabled |
| Telegram | For trade alerts on your phone |
| PC ON time | 8:45 AM – 3:45 PM IST every trading day |

---

## STEP 1 — INSTALL PYTHON 3.11

### Windows:
1. Go to: https://www.python.org/downloads/release/python-3118/
2. Click **"Windows installer (64-bit)"** and download
3. Run the installer
4. **IMPORTANT**: Check the box **"Add Python to PATH"** before clicking Install
5. Click **"Install Now"**

Verify install — open **Command Prompt** (press `Win + R`, type `cmd`, press Enter):
```
python --version
```
Should show: `Python 3.11.x`

### Ubuntu/Linux:
```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip
python3.11 --version
```

---

## STEP 2 — INSTALL GOOGLE CHROME

The bot uses Chrome to automatically log into Groww every morning.

### Windows:
- Download from: https://www.google.com/chrome/
- Install normally (most people already have Chrome)

### Ubuntu:
```bash
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb
```

---

## STEP 3 — DOWNLOAD THE BOT

### Windows:
1. Install Git: https://git-scm.com/download/win (click "64-bit Git for Windows")
2. Open **Git Bash** (right-click on Desktop → "Git Bash Here")
3. Run:
```bash
cd C:/Users/YourName        # Change to your home folder
git clone https://github.com/lakshaytrades/kingtrades.git
cd kingtrades
git checkout claude/nse-momentum-groww-bot-v8Rma
```

### Ubuntu:
```bash
sudo apt install -y git
cd ~
git clone https://github.com/lakshaytrades/kingtrades.git
cd kingtrades
git checkout claude/nse-momentum-groww-bot-v8Rma
```

---

## STEP 4 — SET UP PYTHON ENVIRONMENT

### Windows (in Git Bash or Command Prompt inside the `kingtrades` folder):
```bash
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Ubuntu:
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Installation takes 5–10 minutes. Wait for it to complete.

Verify:
```bash
python -c "import growwapi; print('growwapi OK')"
python -c "import pandas_ta; print('pandas_ta OK')"
```

---

## STEP 5 — CREATE YOUR .env FILE (CREDENTIALS)

Copy the example file:
```bash
# Windows:
copy .env.example .env

# Ubuntu:
cp .env.example .env
```

Now open `.env` in Notepad (Windows) or nano (Ubuntu):
```bash
# Windows:
notepad .env

# Ubuntu:
nano .env
```

Fill in every value — see each section below:

```env
# ── Groww Credentials ───────────────────────────────────────
GROWW_EMAIL=yourname@gmail.com
GROWW_PASSWORD=your_groww_password_here
GROWW_TOTP_SECRET=ABCD1234EFGH5678IJKL9012MNOP3456
GROWW_AUTH_TOKEN=your_groww_api_token_here
GROWW_CLIENT_ID=
GROWW_CLIENT_SECRET=

# ── Telegram ─────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrs
TELEGRAM_CHAT_ID=987654321

# ── Claude AI (optional — for morning market thesis) ─────────
ANTHROPIC_API_KEY=sk-ant-api03-...

# ── Optional APIs ────────────────────────────────────────────
NEWS_API_KEY=
ALPHA_VANTAGE_KEY=

# ── Trading Settings ─────────────────────────────────────────
LIVE_TRADING_ENABLED=False          # ← KEEP FALSE UNTIL STEP 11
MAX_DAILY_CAPITAL=10000             # ₹10,000 to start
MAX_RISK_PER_TRADE_PCT=0.5
DAILY_LOSS_LIMIT_PCT=2.0

# ── System ───────────────────────────────────────────────────
TIMEZONE=Asia/Kolkata
LOG_LEVEL=INFO
LOG_DIR=logs
```

Save and close.

---

## STEP 6 — GET YOUR GROWW TOTP SECRET

This is the most important step. It lets the bot log into Groww automatically every morning.

**Step 6a — Enable TOTP on Groww:**
1. Open Groww app on your phone
2. Go to: **Profile → Settings → Security → Two-Factor Authentication**
3. Tap **"Set up authenticator app"**
4. Groww shows a QR code

**Step 6b — Get the secret key (NOT the QR code):**
1. Below the QR code, tap **"Can't scan? Enter code manually"**
2. Groww shows a 32-character code like: `JBSWY3DPEHPK3PXP...`
3. Copy this code
4. Paste it as `GROWW_TOTP_SECRET=` in your `.env` file

**Step 6c — Verify it works:**
```bash
# Activate venv first (Windows):
venv\Scripts\activate

# Run this:
python -c "
import pyotp, os
from dotenv import load_dotenv
load_dotenv()
secret = os.getenv('GROWW_TOTP_SECRET')
totp = pyotp.TOTP(secret)
print('TOTP code:', totp.now())
"
```
Compare the 6-digit output with your Google Authenticator / Groww app.  
If they match — TOTP is set up correctly.

---

## STEP 7 — GET YOUR GROWW API TOKEN

1. Log into Groww: https://groww.in (on your browser)
2. Go to: **Profile → Settings → API Access**
3. Click **"Generate API Key"** or **"Create Token"**
4. Copy the token
5. Paste as `GROWW_AUTH_TOKEN=` in `.env`

> Note: This token expires daily. The bot auto-refreshes it via TOTP login at 8:45 AM IST.

---

## STEP 8 — SET UP TELEGRAM BOT

### Create the bot:
1. Open Telegram on your phone
2. Search: `@BotFather`
3. Send: `/newbot`
4. Name it: `KingTrades Alert Bot`
5. Username: `kingtrades_youname_bot` (must end in `bot`)
6. BotFather sends you a token like: `1234567890:ABCdef...`
7. Copy this → paste as `TELEGRAM_BOT_TOKEN=` in `.env`

### Get your Chat ID:
1. Send any message to your new bot (just type "hi" and send)
2. Open this URL in your browser (replace TOKEN with your token):
   ```
   https://api.telegram.org/botYOUR_TOKEN_HERE/getUpdates
   ```
3. Look for `"chat":{"id":123456789}` in the response
4. Copy that number → paste as `TELEGRAM_CHAT_ID=` in `.env`

### Test Telegram:
```bash
python -c "
import requests, os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('TELEGRAM_BOT_TOKEN')
chat_id = os.getenv('TELEGRAM_CHAT_ID')
url = f'https://api.telegram.org/bot{token}/sendMessage'
r = requests.post(url, json={'chat_id': chat_id, 'text': 'KingTrades bot is connected!'})
print('Telegram test:', r.status_code)
"
```
You should receive a message on Telegram.

---

## STEP 9 — TEST EVERYTHING (DRY RUN)

```bash
# Windows — activate venv:
venv\Scripts\activate

# Ubuntu:
source venv/bin/activate

# Run the bot in paper mode (LIVE_TRADING_ENABLED=False):
python main.py
```

You will see output like:
```
[IST 2026-04-03 08:45:00] KingTrades Bot starting...
[IST 2026-04-03 08:45:02] TOTP login successful
[IST 2026-04-03 09:00:00] Overnight analysis: BULLISH bias (+35)
[IST 2026-04-03 09:15:00] Market OPEN — scanning watchlist...
[IST 2026-04-03 09:17:45] SIGNAL: LONG RELIANCE | Grade A | Score 84
[IST 2026-04-03 09:17:45] PAPER TRADE: BUY 12 x RELIANCE @ ₹2847.50 (live disabled)
```

### Paper Trade for 5 Days — Check These:
- [ ] Bot starts on its own, logs in via TOTP
- [ ] Signals only appear during 9:15–10:30 AM and 2:00–3:00 PM
- [ ] No signals during 11:00 AM–1:00 PM (midday chop blocked)
- [ ] All positions "closed" before 3:20 PM
- [ ] Bot stops at 3:30 PM
- [ ] You receive alerts on Telegram for every signal
- [ ] Logs in `logs/` folder show IST timestamps (not UTC)
- [ ] EOD report arrives on Telegram by 3:45 PM

---

## STEP 10 — AUTO-START ON WINDOWS (So it starts with your PC)

Create a batch file so the bot starts automatically when you turn on your PC.

**Create `start_bot.bat`** in the `kingtrades` folder:
```bat
@echo off
cd C:\Users\YourName\kingtrades
call venv\Scripts\activate
python main.py
pause
```
Replace `YourName` with your actual Windows username.

**To auto-start with Windows:**
1. Press `Win + R` → type `shell:startup` → press Enter
2. A folder opens (Startup folder)
3. Copy your `start_bot.bat` into this folder
4. Now it will run every time Windows starts

**Recommended instead (manual start each morning):**
- Just double-click `start_bot.bat` at 8:45 AM each trading day
- The window must stay open during market hours
- Do NOT close the Command Prompt window while the bot is running

---

## STEP 11 — GO LIVE (REAL MONEY)

**Only do this after 5 days of successful paper trading.**

### Checklist before going live:
- [ ] Paper trade win rate ≥ 55%
- [ ] No crashes in 5 days
- [ ] Telegram alerts working
- [ ] You understand each alert message
- [ ] You have tested `/kill` command on Telegram
- [ ] Starting capital ready: ₹10,000 minimum, ₹25,000 recommended

### Enable live trading:
1. Open `.env` in Notepad
2. Change: `LIVE_TRADING_ENABLED=False` → `LIVE_TRADING_ENABLED=True`
3. Save the file

### First live day protocol:
1. Start bot at 8:45 AM: double-click `start_bot.bat`
2. Watch it run for the first 30 minutes
3. Keep Groww app open on your phone
4. After each trade alert, verify the order appeared in Groww
5. If anything looks wrong, send `/kill` on Telegram immediately

---

## STEP 12 — DAILY ROUTINE

| Time (IST) | What happens |
|------------|-------------|
| 8:30 AM | Start your PC, open the bot window |
| 8:45 AM | Bot auto-logs into Groww via TOTP |
| 9:00 AM | Morning market brief sent to Telegram |
| 9:15 AM | Market opens, bot starts scanning |
| 9:15–10:30 AM | Best trading window — most signals here |
| 11:00–1:00 PM | Bot pauses (midday chop filter active) |
| 2:00–3:00 PM | Second trading window |
| 3:20 PM | Bot starts closing all positions |
| 3:30 PM | Bot stops, market closes |
| 3:45 PM | EOD report sent to Telegram |
| 4:00 PM | Self-learning runs in background |
| You can close PC after 4:00 PM |

---

## STEP 13 — TELEGRAM COMMANDS

Send these to your Telegram bot anytime during market hours:

| Command | What it does |
|---------|-------------|
| `/kill` | **EMERGENCY** — closes all positions and stops the bot |
| `/status` | Shows current P&L, open positions, bot state |
| `/pause` | Pauses new trades (keeps existing positions open) |
| `/resume` | Resumes after pause |
| `/watchlist` | Shows stocks the bot is watching today |
| `/report` | Forces an immediate P&L report |

---

## TROUBLESHOOTING

### "Python not found" (Windows)
- Uninstall Python and reinstall, checking "Add to PATH"
- Or use: `py -3.11 main.py` instead of `python main.py`

### "No module named growwapi"
```bash
venv\Scripts\activate    # Windows
pip install growwapi
```

### Bot starts but TOTP login fails
- Check `GROWW_TOTP_SECRET` in `.env` — must be exact 32-char key from Groww
- Verify: `python -c "import pyotp; from dotenv import load_dotenv; load_dotenv(); import os; print(pyotp.TOTP(os.getenv('GROWW_TOTP_SECRET')).now())"`
- Compare output with your phone's authenticator app

### No signals being generated
This is **normal behavior**. The bot only trades when 5 gates pass simultaneously.
On some days, 0 trades is correct. Check: `logs/trading_YYYY-MM-DD.log`
Look for lines saying `FILTERED —` to see what's being rejected.

### Bot stops working mid-day
- Check the command prompt window — look for error messages
- Common cause: internet disconnected
- Restart: close window, double-click `start_bot.bat` again

### Trades happening at wrong times
```bash
python -c "from utils import get_current_ist_time; print(get_current_ist_time())"
```
Must show IST time. If showing UTC, check `TIMEZONE=Asia/Kolkata` in `.env`

### "Insufficient balance" error
- Check Groww account balance
- Check `MAX_DAILY_CAPITAL` in `.env` — set it to ≤ your available balance

---

## CAPITAL SCALING PLAN

Start small. Scale only after consistent profits.

| Period | Capital | Condition to scale |
|--------|---------|-------------------|
| Week 1–2 | ₹10,000 | Verify bot works, alerts arrive |
| Week 3–4 | ₹25,000 | Win rate ≥ 55%, no crashes |
| Month 2 | ₹50,000 | Monthly return ≥ 3% net |
| Month 3+ | ₹1,00,000 | Monthly return ≥ 5%, Sharpe > 1.5 |

**Never risk more than 2% of total capital in a day (bot enforces this).**

---

## IMPORTANT NOTES FOR LOCAL PC

1. **PC must be ON** during market hours. If it sleeps/hibernates, the bot stops.
   - Windows: Control Panel → Power Options → set "Never" for sleep during trading hours

2. **Stable internet required.** Use wired ethernet if possible. If internet drops during a trade, the bot will try to reconnect but you should check manually.

3. **Don't run heavy programs** during market hours (games, video editing) — bot needs RAM and CPU.

4. **Never close the terminal/Command Prompt window** while the bot is running.

5. **Daily backups**: The bot stores all data in `data/` and `logs/` folders. Keep these safe.

---

## LOG FILES TO MONITOR

```
kingtrades/
├── logs/
│   ├── trading_2026-04-03.log     ← Today's full log
│   └── performance/               ← Daily/weekly performance reports
├── data/
│   ├── trade_journal.db           ← Every trade ever made (SQLite)
│   └── candle_store.db            ← Historical price data (grows daily)
└── logs/adaptive_config.json      ← Self-learning parameters
```

To watch the log in real time (Windows Git Bash):
```bash
tail -f logs/trading_$(date +%Y-%m-%d).log
```

---

*KingTrades NSE Momentum Bot | Local PC Deployment | Updated 2026-04-03*

---

## FULLY AUTOMATIC SETUP (Recommended)

After completing Steps 1–8 (credentials), run this **once**:

```bash
# Windows (Git Bash or Command Prompt):
venv\Scripts\activate
python setup_autostart.py

# Ubuntu/Linux:
source venv/bin/activate
python setup_autostart.py
```

This single command:
- Verifies all credentials are in place
- Creates a Windows Task Scheduler task (or Linux systemd service)
- Configures the bot to start silently at PC boot
- Sets up automatic restart if the bot crashes

After this, **you never need to manually start the bot again**.
Turn on your PC → bot starts automatically → trades automatically → shuts down at 3:30 PM IST.

### Other setup_autostart.py commands:
```bash
python setup_autostart.py status    # Check if auto-start is configured
python setup_autostart.py test      # Run bot once to verify everything works
python setup_autostart.py remove    # Remove auto-start (if you want to stop)
```

### watchdog.py commands:
```bash
python watchdog.py --status         # Show bot current state
python watchdog.py --once           # Run bot once without restart loop
```

---

## AUTONOMOUS SUPERVISOR (VPS — Recommended)

The supervisor is a self-healing agent that keeps the bot running 24/7 and
alerts you via Telegram when something needs attention.

### What it does automatically (no human needed):
- **Auto-restart** if bot crashes (circuit breaker: max 5 restarts/hour)
- **Monitor logs** for CRITICAL/HIGH errors and alert via Telegram
- **Track P&L** every 15 minutes — warns if approaching daily loss limit (-1.5%)
- **Health checks** — Alpaca API connectivity, log freshness, disk space

### Run on VPS (recommended — use supervisor instead of running main.py directly):

```bash
# Install as systemd service (one-time)
sudo cp /opt/kingtrades/supervisor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kingtrades-supervisor
sudo systemctl start kingtrades-supervisor

# Check supervisor logs
sudo journalctl -u kingtrades-supervisor -f

# Run in foreground (testing)
python supervisor.py
```

### Telegram alerts you will receive:
| Event | Message |
|-------|---------|
| Bot crash | ⚠️ Bot restarting — reason + restart count |
| Circuit breaker | 🚨 5 restarts/hr limit hit — manual action needed |
| P&L alert | ⚠️ Approaching daily loss limit |
| P&L milestone | 💰 On track for target returns (+2%+ day) |
| Health issue | ⚠️ API unreachable / log stale / low disk |

