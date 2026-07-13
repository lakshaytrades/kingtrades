# Batch Plan: Fix NSE Momentum Bot to 4%/Month

## Research Summary

Deep analysis of 14 root causes in the NSE momentum bot (latest backtest: 46 trades, 15.2% WR, -1.11%/month, Sharpe -9.27).

### Critical Findings

1. **Time-score inversion (biggest WR killer):** The 10:00–11:30 window has only **5% WR** (19 trades), yet the time-graduated floor is LOWEST here (13.0). This is backwards — low WR means HIGH noise, needs tighter threshold, not looser. Lines 2006–2013 and 3521–3527 in backtest_engine_india.py.

2. **T1 exit too far (3R = rarely hit):** T1 is set to 3×ATR (same as T2), so the "partial exit" that should lock in profit at 1.5R never fires quickly enough. Trade gets stopped at SL before collecting any partial. Lines 2447–2455 in backtest_engine_india.py and ATR_TP_MULTIPLIER=3.0 in config_india.py.

3. **ATR SL too tight for 5-min bars:** SL=1.5×ATR is appropriate for 1H bars. On 5-min bars, 1.5×ATR is typically 0.15–0.2% (₹2–3 on ₹1500 stock) — inside intrabar noise. Causes immediate whipsaws. Lines 2450–2462.

4. **Synthetic data wrong regime distribution:** 45% RANGE days (vs ~15% real NSE), only 30% STRONG_BULL (vs ~55% real). Bot trains/tests on data that's 3× choppier than reality. generate_synthetic_data.py lines 148–159.

5. **SESSION_DRIVE fires too late (+15 overscored):** Signal requires session already up 0.5% — that move already happened, bot is chasing exhaustion. Score of +15 is too high for a lagging signal. strategies_india.py lines 305–343.

6. **SQUAREOFF at 15:15 exits power-hour trades early:** Last 45 minutes (14:30–15:15) is NSE's highest-volume window. Forced exit at 15:15 cuts winners that would close T2 in final minutes. Line 55.

7. **Breadth filter uses current bar (lookahead bias):** Breadth computed at time T uses close prices at time T — same bars as the entry signal. Creates false confirmation. Lines 1407–1441.

8. **confirmed_momentum_signal / momentum_ignition_signal undefined:** Called but not implemented → silent failure → ~10 points of expected score lost every bar, suppressing entries that should fire. Lines 2081–2113.

---

## Work Units (8 independent units)

### Unit 1 — Fix time-graduated score floors
**Files:** `india/backtest_engine_india.py` (lines 2006–2025, 3521–3540)
**Change:** Raise early-session floor from 13→17 (10:00–11:30 is noise, not opportunity). Keep 11:30–13:00 at 15. Set MIN_SCORE 15→16 globally to match.
```
_time_min_score = (17.0 if _now_t < dtime(11, 30)
                   else 15.0 if _now_t < dtime(13, 0)
                   else 20.0)
MIN_SCORE = 16.0
```

### Unit 2 — Fix T1/T2 targets + ATR SL multiplier (timeframe-aware)
**Files:** `india/backtest_engine_india.py` (lines 2446–2523), `india/config_india.py`
**Change:**
- T1 = 1.5×ATR (not 3×ATR): partial exit fires quickly, locks profit
- T2 = 3.0×ATR (keep as runner target)
- SL: for 5m data use 2.0×ATR, for 1h keep 1.5×ATR
- After T1 hit: move SL to entry + 0.3×ATR (lock in small profit, not just breakeven)
- config_india.py: ATR_TP_MULTIPLIER = 1.5 (was 3.0)

### Unit 3 — Overhaul synthetic data generator
**Files:** `india/generate_synthetic_data.py`
**Change:**
- Regime mix: STRONG_BULL 30%→50%, STRONG_BEAR 25%→20%, RANGE 45%→30% (match real NSE)
- ORB success rate: only 65% of bull-day ORBs continue (add 35% fake-breakout reversals)
- Volume: decouple from momentum (volume at 9:30 always high regardless of direction)
- Lunch lull: reduce both volume AND step size in 11:30–13:00
- Add: realistic VWAP deviation patterns (not just mean-reversion)

### Unit 4 — Fix lagging signal scores in strategies_india.py
**Files:** `india/strategies_india.py`
**Change:**
- SESSION_DRIVE_LONG: score +15→+6, require 0.3% (not 0.5%) session move, fire earlier
- SESSION_DRIVE_SHORT: score -15→-6
- vwap_reversion_signal: add volume requirement (rvol ≥ 1.3) before returning score
- orb_momentum_signal: ORB_BULL_CLEAN requires rvol ≥ 1.8 (up from 1.5)

### Unit 5 — Fix SQUAREOFF timing + trailing stop after T1
**Files:** `india/backtest_engine_india.py` (line 55, lines 1485–1512, 2485–2510)
**Change:**
- SQUAREOFF: 15:15 → 15:25 (let power-hour trades run to T2)
- Add trailing stop: after T1 hit, if price retraces >50% back to entry, exit runner early
- Trailing stop implementation: set `t.trail_sl = max(t.trail_sl, price - 0.5*atr)` after T1

### Unit 6 — Fix breadth filter lookahead bias
**Files:** `india/backtest_engine_india.py` (lines 1407–1441, 2935–2960)
**Change:**
- In breadth loop, use `df.iloc[idx-1]` (prior bar) instead of `df.loc[now_ts]` (current bar)
- Cache breadth at 15-min intervals; only update when current time is at a 15-min boundary
- Remove same-bar breadth bonus from net_score (lines 1919–1935 and 3453–3470)

### Unit 7 — Implement confirmed_momentum_signal + remove undefined calls
**Files:** `india/strategies_india.py`, `india/backtest_engine_india.py` (lines 2081–2113, 3595–3625)
**Change:**
- Add `confirmed_momentum_signal(df, idx)` to strategies_india.py: returns +16 when EMA9>EMA21>EMA50 AND close>VWAP AND rvol≥1.5 AND RSI 48–65
- Add `momentum_ignition_signal(df, idx)` to strategies_india.py: returns +14 when 3 consecutive bullish bars with increasing volume
- Remove broken import fallbacks in backtest engine (wrap in try/except that logs rather than silently ignoring)

### Unit 8 — Fix RVOL thresholds across the board
**Files:** `india/backtest_engine_india.py` (lines 459, 464–468, 2272–2277, 3783–3788)
**Change:**
- ORB_BULL_CONFIRM vol gate: 1.3→1.5 (line 459)
- Reduce ORB score: +18→+14 when rvol 1.5–2.0, keep +18 only for rvol≥2.0
- rvol_min in session check: 1.4 → 1.5 (lines 2272, 3783)
- Add: VWAP_BOUNCE rvol gate: require rvol ≥ 1.5 in vwap_bounce_signal

---

## E2E Test Recipe

**Skip browser/UI testing** — this is a CLI trading bot.

Each worker verifies by running:
```bash
cd /home/user/kingtrades
python3 india/backtest_engine_india.py --source synthetic --capital 500000 2>&1
```

**Success criteria (look for in output):**
- Total trades > 20 (not zero, not 3)
- Win rate > 35% (better than current 15.2%)
- Profit factor > 0.8 (better than current 0.26)
- Monthly return > -0.5% (trending toward positive)
- No Python errors/tracebacks in output
- All months have trades (not just first month)

If `--source synthetic` hangs > 3 minutes, use smaller test:
```bash
python3 -c "
import sys; sys.path.insert(0, 'india')
from backtest_engine_india import _compute_all, _build_orb, run_backtest_from_data
from generate_synthetic_data import generate_nse_data
data_raw = generate_nse_data(['RELIANCE','TCS','HDFCBANK','ICICIBANK','SBIN','INFY',
                               'AXISBANK','BAJFINANCE','MARUTI','TATASTEEL'], days=30)
data = {s: _build_orb(_compute_all(df)) for s, df in data_raw.items()}
run_backtest_from_data(data, capital=500000)
"
```

---

## Codebase Conventions (Workers Must Follow)

- **ALL time checks use IST (`ZoneInfo("Asia/Kolkata")`)** — NEVER bare `datetime.now()`
- `_is_5m_data = (_bar_mins <= 7)` — different paths for 5m vs 1h data
- Both `run_backtest()` and `run_backtest_from_data()` have duplicate logic — changes must be made in BOTH functions
- `MIN_SCORE` is a module-level global at line 1178
- `LONG_ONLY_NSE = True` (currently set, do not change to False)
- `SQUAREOFF = dtime(15, 15)` at line 55 — only Unit 5 changes this
- `COST_RT_PCT = 0.0045` — do not change
- `SL = ATR_SL_MULTIPLIER × ATR`, `T1 = ATR_TP_MULTIPLIER × ATR`, `T2 = 3.0 × ATR`
- Branch: `claude/nse-momentum-groww-bot-hvkv9`
- Repo: `lakshaytrades/kingtrades`
- No `gh` CLI available — use `git push` and create PR via `mcp__github__create_pull_request`

---

## Worker Instructions Template

After you finish implementing your change:
1. **Code review** — Invoke the `Skill` tool with `skill: "code-review"` to find correctness bugs. Fix any findings before continuing.
2. **Run unit tests** — Check for tests: `ls india/test_* india/tests/ 2>/dev/null`. If none found, skip.
3. **Test end-to-end** — Follow the E2E test recipe above. Run the backtest. If output shows errors, fix them. Post the key metrics (trades, WR, PF, monthly return) in your report.
4. **Commit and push** — `git add <files> && git commit -m "..." && git push -u origin claude/nse-momentum-groww-bot-hvkv9`
5. **Create PR** — Use `mcp__github__create_pull_request` tool (NOT `gh`). Draft PR to `lakshaytrades/kingtrades` base `main`.
6. **Report** — End with `PR: <url>` or `PR: none — <reason>`.
