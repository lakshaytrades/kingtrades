# Reality Audit — kingtrades

*Generated 2026-06-13 from direct code inspection + a real, no-look-ahead replay
of the production signal generator on real NSE candles. This document states
what the repository actually IS, versus what its marketing docs claim.*

---

## 1. You do not have one bot. You have three, layered by churn.

| Stack | Entrypoint | Broker / Market | Status |
|---|---|---|---|
| **US momentum** | `main.py` (root) | Alpaca — NYSE/NASDAQ | What `requirements.txt` installs; `broker.py` wires Alpaca |
| **India momentum** | `india/main_india.py` | Upstox — NSE | The most recent active development (see git log) |
| **NSE/Groww** (the `CLAUDE.md` spec) | — | Groww | **Dead.** `growwapi` appears only in `archive_unused/` and `tests/` |

`CLAUDE.md` describes a **Groww** bot. There is **no live Groww code** — it was
migrated Dhan → Upstox and the Groww integration was archived. The bot you think
you have is not the bot in the repo. This incoherence is itself a live-trading
hazard: you cannot reason about what will execute.

**Recommendation:** pick ONE stack (the India/Upstox bot is the real current
work) and archive the other two so the live path is auditable. Update or replace
`CLAUDE.md` to match reality.

---

## 2. FATAL bug found and fixed: the India bot could never place a trade

`PatternRecognizer` has **no `compute_indicators()` method** — it does not exist
anywhere in the repo. Yet it was called inside the core decision path:

- `india/signal_generator_india.py:139` — inside `generate_signal()`, wrapped in
  a broad `try/except` that **silently swallowed the `AttributeError` on every
  bar**, returning `None` for every symbol on every bar.
- `india/main_india.py:1665` — same call; its fallback branch
  (`_rec.get_latest_indicators`) also doesn't exist on `PatternRecognizer`.

**Effect:** the production India signal core returned `None` 100% of the time.
Run live, the bot would scan all day and **never trade**.

**Fix (this branch):** both sites now use the correct in-repo API — the same one
`PatternRecognizer.analyze()` uses — `TechnicalIndicators.compute()` then
`get_latest_indicators()`, which returns a populated `IndicatorSet`. Locked in by
`india/test_signal_core_smoke.py` (4/4 passing).

**Root cause that let it hide:** `signal_generator_india.py` alone contains **69
broad `except` blocks**. Catch-all exception handling masked a fatal crash as a
"no signal." This pattern should be audited repo-wide.

---

## 3. The real, measured edge — for the first time

With the core fixed, `india/replay_yahoo_india.py` replays the **actual**
`IndiaSignalGenerator` bar-by-bar over **real NSE 5-min candles** (Yahoo), no
look-ahead. Win rate is an OUTPUT, not an assumption.

**Measured (6 large caps, ~59 trading days, 38 trades):**

| Metric | Measured | Claimed in repo docs |
|---|---|---|
| Win rate | **36.8%** | "62–68%" |
| Profit factor | **0.45** | — |
| Expectancy / trade | **−₹160** | positive |
| Net result | **−1.22%** (−0.43%/mo) | "+7–10%/mo" |

Caveats (stated honestly): small sample (38 trades); this is the *conservative
floor* (live-only boosters disabled, since they can't be replayed historically).
It is **not** a final verdict — but it is the **only real number that exists**,
and it is **negative**, the opposite direction from the projections.

Every "7–10%/month" figure in the repo comes from a Monte Carlo that **takes win
rate as an input** and assumed 65%. The measured price-core win rate is 37%.

---

## 4. On the request for "15–20%/month, Sharpe > 2, easily"

- A clean, non-optimized momentum strategy on 5 years of real NSE large caps
  (`india/honest_nse_backtest.py`): **−6.6%/yr, Sharpe −0.38, −39% max DD.**
  Nifty buy-and-hold over the same period: **+8.5%/yr (~0.68%/mo).**
- Your own best-case simulation (`india/BACKTEST_REPORT_INDIA.md`) caps at
  **+11.6%/mo median with a 2% chance of ≥20%** — and only at an unproven 70% WR.
- 15–20%/month is ~25–30× what the index returns. It is not a tuning problem;
  the number does not exist as a reliable outcome for anyone.

**More code is the disease, not the cure.** 300+ modules named `quantum`,
`citadel`, `renaissance`, `genius`, `phd` are an overfitting surface that hid a
core which couldn't trade at all. The path to "better" is *subtract and verify*,
not *add and assume*.

---

## 5. Recommended path (money-first)

1. **Prove edge before risking capital.** Run `india/replay_yahoo_india.py` and
   the Upstox replay on more symbols / longer windows. If net-of-cost expectancy
   isn't positive out-of-sample, **no new strategy matters** — fix the core edge
   or don't trade.
2. **Collapse to one coherent stack** (India/Upstox). Archive US/Alpaca + the
   dead Groww remnants. Make `CLAUDE.md` true.
3. **Harden the money-protection layer** (square-off, daily-loss circuit,
   kill-switch, position caps) with real tests — the part that matters most live.
4. **Audit the 69 broad `except` blocks** — re-raise or narrow them so the next
   fatal bug can't masquerade as "no signal."
5. **Keep `LIVE_TRADING_ENABLED=False`** until a positive, out-of-sample,
   cost-inclusive edge is demonstrated and the risk layer is test-covered.

---

*Artifacts added on branch `claude/new-session-gc10i8`:
`india/honest_nse_backtest.py`, `india/replay_yahoo_india.py`,
`india/test_signal_core_smoke.py`, plus the two-line fatal-bug fix.*
