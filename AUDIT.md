# KingTrades — Honest Audit & Operating Report

_Last updated: 2026-06-08. Branch: `claude/live-market-code-recovery-b6Wsw`._

## 1. What this actually is

Three separate trading bots living in one repo (~143,000 lines, 304 Python files):

| Bot | Entry point | Broker / Market | Status |
|-----|-------------|-----------------|--------|
| **India** | `india/main_india.py` | Dhan / NSE equity (INTRADAY/MIS) | Active |
| **US** | `main.py` | Alpaca / US stocks | Active |
| **Crypto** | `crypto_engine.py` | Alpaca crypto | **Deliberately OFF** (`main.py:528`) |

Both active bots share the big engines: `signal_generator.py`, `pattern_recognition.py`,
`high_accuracy_filter.py`, `risk_manager.py`.

## 2. How a bot works (per scan, during market hours)

```
watchlist → fetch OHLCV (5m + 15m + 1h)
          → indicators (RSI/MACD/ATR/VWAP/EMA/ADX/BB/Stoch)
          → hard gates (direction, MTF alignment, ATR quality, ADX trend,
                        candle quality, anti-chop, VIX)
          → base score ≥ threshold
          → institutional boosters (option chain, FII/DII, sector, ORB, …)
          → final score ≥ exec threshold  → quality grade A+/A/B+/B
          → market-regime + time-window filters
          → position sizing (ATR risk + many multipliers)
          → place entry + protective stop  → trail → square off before close
```

- **Timezone:** all logic + logs in IST (India) / ET (US). Server is UTC.
- **Risk:** ~0.5% risk/trade, ATR stops, 2:1+ R:R, daily-loss limit, loss-streak
  shrink, VIX circuit breaker, hard intraday square-off.
- **Control:** Telegram (`/status /kill /pause /resume /close …`).

## 3. Bugs found & fixed in this audit

| Severity | Bug | Fix |
|----------|-----|-----|
| 🔴 CRITICAL | India `generate_signal` called `recognizer.compute_indicators()` — **method doesn't exist** → AttributeError swallowed → **0 trades always** | Use real API `indicators.compute()` + `get_latest_indicators()` |
| 🔴 CRITICAL | India market data via NSE charting API — **IP-blocked (403) on VPS** | Made Dhan the primary data source; NSE fallback |
| 🔴 CRITICAL | US bot: `alpaca-py` + `yfinance` not installed → no data → 0 signals/orders | Auto-install preflight in `start.sh` |
| 🟠 HIGH | Logs were UTC mislabeled `[IST]` → a correct 15:21 square-off looked like a 09:51 crash | IST log converter in all entry points |
| 🟠 HIGH | `dhanhq` SDK not installed → silent paper mode | Auto-install preflight in `start_india.sh` |
| 🟡 MED | US `get_candles(…,"1Day",limit=25)` wrong kwarg + unmapped → daily gates silently got no data | `days=25` + mapped `1Day→day` |
| 🟡 MED | US `analyze()` returned `score=0` (int) on <50 bars → `.get()` crash swallowed | return `{}` |
| 🟡 MED | US futures-bias size multiplier called a **phantom method** → never applied | Added `set_session_size_mult()` + wired into sizing |
| 🟡 MED | US self-learner **never wired** into signal generator → adaptive weighting ignored | Added `set_learner()` call |
| 🟡 LOW | US ML feature read `bb_pct` (real field `bb_pct_b`) → constant 0.5 fed to model | Fixed field name |
| — | Hardening: India `generate_signal` now logs `AttributeError`/`TypeError` at ERROR (not silently) so future phantom bugs are visible | — |

## 4. Honest assessment

- **The recurring "0 trades" history is explained.** The dominant cause was a
  silent crash (phantom method) + missing SDKs, not strategy. Those are fixed.
- **This codebase shows signs of fast/bulk generation:** 300+ modules, many
  "elite/godmode/institutional" engines, broad `try/except: pass` everywhere.
  That pattern is exactly what hid the critical bugs — failures don't surface,
  they silently degrade to "no signal." Treat green logs with suspicion until
  you see real fills.
- **Not verified here:** profitability, backtest realism, that live Dhan/Alpaca
  data actually flows on YOUR VPS (no credentials in this environment), and the
  edge of the many booster modules. I fixed correctness/plumbing, not strategy.
- **Real-money risk is real.** There is no true paper-trading simulator beyond a
  flag; "paper mode" mostly means "don't send the order." `LIVE_TRADING_ENABLED`
  defaults to `True` on the US bot (kept by owner's choice).

## 5. What to do next (recommended order)

1. **Install deps on the VPS** (or just run the launchers, which now auto-install):
   `pip install -r requirements.txt` (US) and `pip install -r india/requirements_india.txt`.
2. **Confirm live data flows:** `python3 india/test_nse_data.py` (needs Dhan creds).
3. **Run PAPER first** for one full session each bot
   (`LIVE_TRADING_ENABLED=False`, India `INDIA_MANUAL_SIGNALS_ONLY=True`).
   Confirm signals now fire — they literally could not before the fix.
4. **Then LIVE + MANUAL:** bot alerts, you place orders by hand. Build trust.
5. **Then LIVE + AUTO with tiny capital** (₹25–50k / a few hundred $).
6. **Watch for the silent-failure pattern:** if you ever see "0 signals" all day
   with data OK, check logs at ERROR level — the new hardening will surface code
   bugs that used to hide.
7. **Consider pruning.** 300 modules is a maintenance and reliability liability.
   A smaller, verified core would be safer with real money than a large engine
   whose components can't all be validated.
