# KINGTRADES INDIA — COMPLETE GUIDE (keep in notes)

## ⚖️ HONEST STATUS (read first, re-read often)
- The bot reads charts + indicators and tells you trades. The machinery works.
- PROVEN on 16 years of real data: intraday/swing TRADING loses after costs
  (in bull AND bear years). Do NOT expect profit from trading.
- The ONE thing that makes money: `/invest` (yearly momentum, ~15-18%/yr).
- Default mode = PAPER (no real orders). Keep it there until proven.
- Never put money you NEED into trading. Never deposit to "try for real"
  until `/proof` is green for 2+ weeks on tiny money you can fully lose.

---

## 1. DEPLOY / UPDATE (run on VPS)
```
cd /root/kingtrades
git fetch origin
git checkout claude/live-market-code-recovery-b6Wsw
git pull
pkill -f ai_supervisor.py; pkill -f watchdog.py    # kill old US processes
bash stop.sh
bash india/start_india.sh
```

## 2. .ENV CONFIG (edit: `nano .env`)
```
# Telegram (ONE token only, no comma. Chat IDs CAN be comma-separated)
TELEGRAM_BOT_TOKEN=your_single_bot_token
TELEGRAM_CHAT_ID=chat_id_1,chat_id_2

# Mode — SAFE defaults (paper)
INDIA_MANUAL_SIGNALS_ONLY=True
INDIA_LIVE_TRADING_ENABLED=False
INDIA_TELEGRAM_VERBOSE=False
US_BOT_ENABLED=False
LIVE_TRADING_ENABLED=False

# Universe + capital
INDIA_UNIVERSE=wide
INDIA_MAX_DAILY_CAPITAL=500000
INDIA_MAX_RISK_PCT=1.0
INDIA_DAILY_LOSS_LIMIT_PCT=2.0

# Upstox DATA (use the ANALYTICS token — read-only, 1-year, can't trade)
UPSTOX_ACCESS_TOKEN=your_analytics_token

# Upstox ALGO (for FUTURE live orders only — leave blank for now)
UPSTOX_API_KEY=
UPSTOX_API_SECRET=
UPSTOX_REDIRECT_URI=https://127.0.0.1
```

## 3. UPSTOX TOKENS — which is which
- ANALYTICS token: read-only, valid 1 YEAR, CANNOT place orders. USE THIS for
  data. Paste it as UPSTOX_ACCESS_TOKEN. Safest option.
- ALGO TRADING token: CAN place real orders, expires daily. Use ONLY when going
  live (future). Get it each morning with:
  ```
  python3 india/auth_upstox.py            # prints login link
  python3 india/auth_upstox.py YOUR_CODE  # saves the daily token
  ```

## 4. CHECK EVERYTHING WORKS (read-only, safe)
```
python3 india/verify_setup.py
```
Want to see: "Paper mode", Telegram ✅, Upstox/Yahoo data ✅, signal engine ✅.

## 5. TELEGRAM COMMANDS
```
/today    — scan for setups right now
/status   — open positions + live P&L
/proof    — 90-day GO/NO-GO performance (the truth)
/weekly   /monthly  — P&L reports
/research — re-run NSE backtest after real costs
/data     — which feed is active + live price sample
/invest   — yearly momentum portfolio (the profitable one)
/buy SYMBOL QTY [PRICE]   /sell ...   /confirm   /cancel   /close SYMBOL
/pause /resume /kill
/help
```

## 6. RUN IN PAPER MODE (do this now — zero risk)
```
bash stop.sh && bash india/start_india.sh
```
Bot sends 👑 signals + shadow-tracks them. Check `/proof` after a few days.

## 7. GO LIVE IN FUTURE (ONLY after /proof green 2+ weeks, tiny money)
WARNING: trading is proven to lose. Only do this with money you can fully lose.
1. Fund Upstox with a TINY amount (e.g. ₹5,000)
2. Get the ALGO token: `python3 india/auth_upstox.py` (+ paste code)
3. In .env set: `INDIA_LIVE_TRADING_ENABLED=True` and `INDIA_MANUAL_SIGNALS_ONLY=False`
   (NOTE: live Upstox order execution must be built first — ask for it then)
4. Supervisor auto-halts at -2% daily loss. Set tighter if you want.
5. Restart: `bash stop.sh && bash india/start_india.sh`

## 8. SAFETY LAYERS
- supervisor_india.py: halts new trades at -2% daily loss (writes halt flag)
- watchdog_india.py: restarts the bot if it crashes
- Daily loss limit, 3-consecutive-loss guard, 15:20 auto square-off
- Set tighter daily loss: `INDIA_DAILY_LOSS_LIMIT_PCT=0.5` in .env

## 9. STOP EVERYTHING (emergency)
```
pkill -f main.py; pkill -f main_india.py
pkill -f watchdog; pkill -f ai_supervisor; pkill -f supervisor_india
cd /root/kingtrades && bash stop.sh
```
In Telegram: `/kill`

## 10. DAILY ROUTINE (if running live, future)
- Morning: refresh ALGO token (`python3 india/auth_upstox.py` + code)
  (Analytics token for data lasts a year — no daily refresh)
- Market opens 9:15 IST, auto square-off 15:20 IST
- Check `/proof` weekly

## 11. THE PROFITABLE PATH (the real answer)
- `/invest` → buy the 20 stocks → hold 1 year → re-run yearly. ~15-18%.
- Or a Nifty 50 index fund SIP. ~12-15%/yr, zero effort.
- These BEAT the trading bot, proven on the same data.

## 12. SECURITY
- Never commit .env (it's gitignored). Never share/screenshot API keys/tokens.
- Tokens in .env only. Regenerate on Upstox if ever exposed.
```
