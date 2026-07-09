# SWING SYSTEM — Final Specification (v1.0, 2026-07-08)

The ONE live strategy. Everything intraday is RETIRED (failed honest-fill
validation; live-blocked in code; cron removed by `setup_swing_cron.sh`).

---

## Strategy: `rev: buy -5% day` (panic mean-reversion, 1–4 day swing)

**Validated** on 127 liquid NSE names, 224 trading days, honest delivery costs:
+1.49%/mo · 60% WR · 4.9% max DD · PF 1.53 · walk-forward positive in every
third (+0.3/+3.9/+3.1) · survives cost to 0.90% RT · positive at every account
size ₹25k+.

## Every rule (exact, as deployed in `india/swing_pilot.py`)

| Rule | Value |
|---|---|
| **ENTRY signal** | Stock's daily close is **≤ −5%** vs previous close |
| **Entry time** | Once daily ~15:10 IST (cron), MARKET **delivery (CNC)** buy |
| **Entry days** | Monday–Thursday only. **Never Friday** (would carry weekend) |
| **TP (target)** | Close ≥ **entry +2%** → exit at next daily check |
| **SL (stop)** | Close ≤ **entry −5%** → exit at next daily check |
| **Time exit** | **Max hold 4 days**, and always **flat by Friday** |
| **Exit orders** | MARKET delivery sell, **fill verified**; failed sell keeps the position tracked and retries next run (never phantom-closed) |
| **Universe** | Full 127-name liquid list (`HIGH_VOL_UNIVERSE`) — matches validation |
| **Slots** | **2 positions max**, each ≤ 50% of capital — matches validation |
| **Sizing** | Available balance (funds API) ÷ 2, `INDIA_MAX_CAPITAL` optional cap, **no leverage** (delivery = full cash) |
| **Kill switch** | `touch ~/kingtrades/KILL` → next run exits everything and stops |
| **State** | `swing_state.json` (+ `.bak` fallback) — positions survive restarts |
| **Live gate** | Real orders need **both** `INDIA_LIVE_TRADING_ENABLED=true` **and** `INDIA_SWING_VALIDATED=true` in `.env` |

## Expected P&L (validated; judge MONTHLY, it's lumpy — ~5 trades/mo)

| Account | Ret/mo | ₹/month | Notes |
|---|---|---|---|
| ₹25,000 | ~+1.05% | ~+₹263 | works but slow |
| ₹50,000 | ~+1.05% | ~+₹526 | recommended live-pilot size |
| ₹1,00,000 | ~+1.12% | ~+₹1,123 | |
| ₹2,00,000 | ~+1.44% | ~+₹2,889 | best cost efficiency |

~15–18%/year, ~5% max drawdown in backtest (plan for ~10% live). A losing
month is NORMAL. **No weekly income guarantee exists.**

## Telegram notifications (automatic, needs TELEGRAM_BOT_TOKEN/CHAT_ID in .env)
* 🛒 **BOUGHT** — symbol, qty, price, target/stop, "flat by Friday"
* 🟢/🔴 **SOLD** — reason (TARGET/STOP/MAX_HOLD/FRIDAY_FLAT), fill price, **P&L in ₹ and %**
* 🟠 **PARTIAL SELL / ⚠️ SELL FAILED** — remainder still held, retry notice
* 📋 **Daily digest** after each run: every holding with entry/target/stop/date + cash
* Silent on quiet days (no holdings, no trades). Notify failures never block trading.


## Large-capital safeguards (matter at ₹3L+; harmless below)
* **Liquidity cap** — a single position never exceeds 1% of the stock's ~20-day
  median daily traded value; names thinner than ₹5cr/day ADV are skipped. This
  protects fills/price-impact at ₹5L+ per slot.
* **Startup warning** — capital > ₹3L logs a CRITICAL notice: the strategy is
  validated to ~₹2L and NOT yet confirmed live; a normal 10% drawdown at ₹10L
  is ₹1,00,000. Cap with `INDIA_MAX_CAPITAL` until live months prove it.
* **Recommended staging (do NOT deploy ₹10L on day one):** ₹1L month 1 (prove
  live) → ₹3L month 2 → ₹5L → ₹10L, each step earned by a profitable month.
  Use `INDIA_MAX_CAPITAL=100000` to let the bot see ₹10L but trade only ₹1L.

## Kill-rules (pre-agreed, executed without emotion)
1. Two consecutive losing **months** → halt (`touch KILL`), re-validate on fresh data.
2. Drawdown > **10%** of capital → same.
3. Live fills persistently worse than backtest closes (check monthly) → halt + review.
4. 2–3 losses in a row → **do nothing** (normal at 60% WR).

## Operations
```bash
# go live (after funding; ₹50k recommended to start)
echo 'INDIA_SWING_VALIDATED=true' >> .env
SWING_MODE=live bash india/setup_swing_cron.sh   # also strips ALL intraday cron

# daily: NOTHING — cron runs 15:10 IST Mon–Fri (server clock IS Indian time)
# monitor:   tail -20 logs/swing_$(date +%F).log      (or Upstox app)
# stop all:  touch ~/kingtrades/KILL
# token from phone: Telegram /token  (fallback /settoken)
# monthly health: python3 india/fetch_midcaps.py --days 365 --out uni.pkl
#                 && python3 india/swing_validate.py
```

## Known limitations (accepted, documented)
1. **Once-daily stop check:** an overnight gap below −5% exits at the gap price,
   not −5%. The backtest models exits the same way (close-based) — consistent.
2. **Friday NSE holiday:** the Friday-flatten run can't execute on a market
   holiday; a Thursday-entered position would sit through the weekend
   (~2–3 possible occurrences/yr). Before a long weekend, `touch KILL` Thursday
   afternoon if you want zero weekend exposure.
3. **State file is the position ledger.** If both `swing_state.json` and its
   `.bak` are lost while holdings exist, the log screams CRITICAL — reconcile
   manually in the app.
4. Backtests, however honest, overstate live results — treat the first live
   month at ₹50k as the final confirmation stage.

## Retired (do not run live)
`live_pilot.py` + `run_pilot.sh` + `squareoff_watchdog.py` (intraday ORB — every
variant failed honest-fill validation; live-blocked in code, kept for research).
