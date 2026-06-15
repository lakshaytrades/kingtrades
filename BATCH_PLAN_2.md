# Batch Plan 2: NSE Bot → 8-10% Monthly

## Research Summary (from deep codebase analysis)

The overnight hold bug is fixed. The bot is structurally sound. Remaining gaps to 8-10%:

| Gap | Root Cause | Expected Impact |
|-----|-----------|----------------|
| Too few trades (~30/month) | session_return gate 0.15%/0.30% too strict | +40% trade volume |
| Static RVOL gate | Always 1.5× regardless of signal conviction | +5% WR on high-conviction days |
| Regime multipliers unused | `get_vol_target_scalar()` + regime mults exist but NOT called | +0.3 Sharpe |
| ML disabled for 2+ days | min 300 samples required before ML activates | ML boost from day 1 |
| Mean-reversion disabled | VWAP_REVERSION / OPENING_DRIVE removed as "noisy" | +4-5% WR on ADX<15 days (40% of days) |
| Optimizer OOS = 5 trades | Too small to be statistically valid | More reliable auto-tuning |
| Manual token renewal | No daemon thread for daily TOTP | Live bot can't run 24/7 |

### Path to 8-10%
- **Current**: ~48% WR, ~1.0 Sharpe, ~30 trades/month → ~5% monthly
- **After Unit 1+2**: ~53% WR, ~1.5 Sharpe, ~60 trades → ~7% monthly
- **After all units**: ~57% WR, ~1.8 Sharpe, ~80 trades → **8-10% monthly**

---

## Work Units (6 parallel, all on `claude/nse-momentum-groww-bot-hvkv9`)

### Unit 1 — Adaptive Entry Gates + Market Alignment Bonus
**Files**: `india/backtest_engine_india.py` (entry gate section in BOTH loops)

**Changes**:
1. Relax `_sr_thresh` for high-conviction signals:
   - 5m: `0.0005` when `net_score >= 16`, else `0.001` (was always `0.0015`)
   - 1h: `0.001` when `net_score >= 16`, else `0.002` (was always `0.003`)
2. Dynamic RVOL gate:
   - `_rvol_min = 1.3 if net_score >= 18 else 1.5` (was always `1.5`)
3. Market alignment bonus — AFTER 3-TF gate, BEFORE min-profit filter:
   ```python
   if direction == "LONG" and _session_breadth > 0.62:
       net_score += 5; reason += "+MKTBIAS_LONG"
   elif direction == "LONG" and _session_breadth < 0.38:
       net_score -= 5; reason += "+MKTBIAS_CONTRA"
   elif direction == "SHORT" and _session_breadth < 0.38:
       net_score += 5; reason += "+MKTBIAS_SHORT"
   elif direction == "SHORT" and _session_breadth > 0.62:
       net_score -= 5; reason += "+MKTBIAS_CONTRA"
   ```
4. Apply in **BOTH** `run_backtest` and `run_backtest_from_data` loops.

**Locate with**: `grep -n "_sr_thresh" india/backtest_engine_india.py` and `grep -n "_rvol_g < 1.5" india/backtest_engine_india.py`

---

### Unit 2 — Risk Manager Overhaul (Sizing + Dynamic Exits)
**Files**: `india/risk_manager.py` (primary) + 4 lines in `india/backtest_engine_india.py` (position-sizing call sites)

**Changes to `risk_manager.py`**:
1. `compute_kelly_position()`: apply `get_vol_target_scalar()` result to scale Kelly:
   ```python
   vol_scalar = get_vol_target_scalar(returns_history) if len(returns_history) >= 10 else 1.0
   raw_kelly *= vol_scalar
   ```
2. `compute_kelly_position()`: add optional `regime_mult=1.0` kwarg, multiply Kelly result:
   ```python
   raw_kelly *= regime_mult
   ```
3. Add helper `get_kelly_regime_mult(regime) -> float`:
   ```python
   from regime_detector import RegimeType, REGIME_SCORE_MULTS
   def get_kelly_regime_mult(regime) -> float:
       if regime is None: return 1.0
       mults = REGIME_SCORE_MULTS.get(regime, {})
       return mults.get("LONG", 1.0)  # or SHORT based on direction
   ```
4. Cap: `raw_kelly = min(raw_kelly, 0.14)` (14% max per position, caps leverage at 1.7×)
5. `compute_exit_stages()`: add `signal_type=""` kwarg:
   - If `"CONFIRMED" in signal_type or "IGNITION" in signal_type`: `stage1_r = 1.0` (earlier lock-in at 1R)
   - Else: `stage1_r = 1.5` (current behavior)

**Changes to `backtest_engine_india.py`** (ONLY at position-sizing call sites):
- Find `compute_kelly_position(` calls, add `regime_mult=_regime_mult` kwarg
- Before the call, add: `_regime_mult = get_kelly_regime_mult(_regime) if _regime else 1.0`
- Find `compute_exit_stages(` calls, add `signal_type=reason` kwarg

**Locate with**: `grep -n "compute_kelly_position\|compute_exit_stages" india/backtest_engine_india.py`

---

### Unit 3 — ML Scorer Warm-Start + Stronger Boost
**Files**: `india/ml_scorer_india.py` ONLY

**Changes**:
1. Lower `min_samples` to train: `50` (from `300`); when `50 <= n < 200` use `cv=3` StratifiedKFold to prevent overfitting
2. Boost values:
   - `ML_STRONG_CONFIRM` (P≥0.78): `+20` (from `+16`)
   - `ML_CONFIRM` (P≥0.70): `+12` (from `+8`)
   - `ML_UNCERTAIN` (P≥0.40): `-4` (from `-3`)
   - `ML_DISAGREE` (P<0.40): `-10` (from `-6`)
3. Add 2 new features (total 23):
   - Feature 22: `adx_norm` = `min(row.get("adx", 25.0) / 30.0, 1.0)` (ADX normalized to 0-1)
   - Feature 23: `breadth_score` = session breadth passed in as context (default 0.5 if unavailable)
4. Update feature-count guard in `load()` from 21 to 23 to discard old stale pickles
5. Update `_features` list at top of class

**Locate with**: `grep -n "min_samples\|300\|ML_STRONG\|feature_count" india/ml_scorer_india.py`

---

### Unit 4 — Conditional Mean-Reversion + Strategy Weighting
**Files**: `india/strategies_india.py` + `india/backtest_engine_india.py` (`_score_bar` function ONLY, lines ~400-510)

**Changes to `strategies_india.py`**:
1. `vwap_reversion_signal(row, adx=None) -> tuple`: re-enable but add gate:
   - Only fires when `adx is None or adx < 15` (range-bound market)
   - LONG: price is 0.8%+ BELOW VWAP + RSI < 40 + RVOL > 1.3 → score +14
   - SHORT: price is 0.8%+ ABOVE VWAP + RSI > 60 + RVOL > 1.3 → score +14
   - No signal if ADX ≥ 15 (trending market — don't fade trends)
2. `opening_drive_signal(row, adx=None) -> tuple`: re-enable with gate:
   - Only fires in first 45 min (09:15-10:00)
   - LONG: first 3 bars all green + RVOL > 2.0 + close > VWAP → score +14
   - Gate: ADX must be > 20 (need some directionality for opening drive)

**Changes to `_score_bar` in engine**:
- Call `vwap_reversion_signal(row, adx=adx)` and add score
- Call `opening_drive_signal(row, adx=adx)` and add score
- Note: `adx` is already available inside `_score_bar`

**Locate with**: `grep -n "vwap_reversion\|opening_drive\|_score_bar" india/backtest_engine_india.py`

---

### Unit 5 — Optimizer OOS Quality Gate + Param Grid
**Files**: `india/optimizer_india.py` ONLY

**Changes**:
1. OOS holdout: `OOS_TRADES = 20` (from `5`); require at least 40 total trades before optimizer runs
2. Accept new params only if BOTH:
   - `OOS_PF >= 0.80 * IS_PF` (OOS profit factor at least 80% of in-sample)
   - `OOS_WR >= 0.40` (minimum 40% OOS win rate)
3. Add to param grid:
   - `"SR_THRESH_5M"`: `[0.0005, 0.001, 0.002]` (session_return gate for 5m)
   - `"SR_THRESH_1H"`: `[0.001, 0.002, 0.003]` (session_return gate for 1h)
   - `"RVOL_MIN"`: `[1.2, 1.35, 1.5]`
4. `load_optimal_params()`: also return `SR_THRESH_5M`, `SR_THRESH_1H`, `RVOL_MIN` in the dict
5. In `backtest_engine_india.py` startup: read and apply these new params from `load_optimal_params()`

**Locate with**: `grep -n "OOS\|oos\|param_grid\|_SCORE_RANGE" india/optimizer_india.py`

---

### Unit 6 — Live Trading Infrastructure + Auto-Token Daemon
**Files**: `india/auth_upstox.py` + `india/.env.example`

**Changes to `auth_upstox.py`**:
1. `schedule_auto_renewal()` — if not already a proper daemon thread:
   ```python
   def schedule_auto_renewal():
       import threading, schedule as _sched, time as _time
       def _renewal_job():
           while True:
               _sched.run_pending(); _time.sleep(30)
       _sched.every().day.at("08:30").do(_auto_renew_with_retry)
       t = threading.Thread(target=_renewal_job, daemon=True, name="upstox-token-renewal")
       t.start()
       return t
   ```
2. `_auto_renew_with_retry()`: wrap `auto_renew_token()` with 3 retries (2s, 4s, 8s) + Telegram alert on failure
3. `ensure_token_fresh(max_age_hours=20)`: read token metadata, call `auto_renew_token()` if stale
4. Daily circuit breaker constants — add to `auth_upstox.py` or a shared constants module:
   ```python
   DAILY_LOSS_HALT_PCT = 0.03   # 3% daily loss → halt new entries
   CONSEC_LOSS_HALT   = 5       # 5 consecutive losses → pause 30 min
   ```
5. Update `.env.example` with: `UPSTOX_AUTO_RENEW_ENABLED=true`, `DAILY_LOSS_HALT_PCT=0.03`

**Note**: Do NOT hardcode credentials. Read all secrets from env vars only.

---

## E2E Test Recipe (all units follow this)

Network is blocked — NO live Upstox/yfinance calls. Validate with:

```bash
cd /home/user/kingtrades

# 1. Syntax check all changed files
python -m py_compile india/<file>.py && echo "syntax OK"

# 2. Import check
python -c "import sys; sys.path.insert(0,'india'); from <module> import <key_fn>; print('import OK')"

# 3. Synthetic logic test with pandas DataFrame
python -c "
import pandas as pd, numpy as np
from zoneinfo import ZoneInfo
IST = ZoneInfo('Asia/Kolkata')
# Build 30-bar OHLCV + indicators, run the changed function
# Assert outputs are correct
print('logic test passed')
"

# 4. Full engine CLI smoke test
python india/backtest_engine_india.py --help
```

No full backtest run — data download is blocked. Validate: syntax + import + synthetic test + help.

---

## Conventions All Workers Must Follow

- **IST timezone always**: `ZoneInfo("Asia/Kolkata")`. NEVER bare `datetime.now()`.
- **Both loops**: Entry/exit logic changes in `backtest_engine_india.py` go in BOTH `run_backtest` AND `run_backtest_from_data`.
- **Wrap in try/except**: Any new call to `row.get()` or external module → `try: ... except Exception: pass`
- **No new dependencies** beyond `pyotp` and `pyarrow` (already checked installed).
- **Branch**: `claude/nse-momentum-groww-bot-hvkv9`
- **COST_RT_PCT** stays at `0.0045` — do not change.
- **MIN_SCORE** default stays `12.0` — override at runtime via optimizer only.
- **No credential hardcoding** — all secrets from env vars.

---

## Worker Template (copy verbatim into each agent prompt)

```
After you finish implementing the change:
1. **Code review** — Invoke the `Skill` tool with `skill: "code-review"` to find correctness bugs. Fix any findings before continuing.
2. **Run unit tests** — `python -m py_compile india/<file>.py && echo OK` for each changed file.
3. **Test end-to-end** — Follow the e2e recipe from this prompt.
4. **Commit and push** — Commit with a clear message, push to `claude/nse-momentum-groww-bot-hvkv9`.
5. **Report** — End with: `PR: none — pushed to branch`
```
