# Batch Plan: NSE Bot — Regular Income Readiness

## Research Summary

Codebase is at `/home/user/kingtrades/india/`. Key findings from deep research:

- **3-TF alignment is weak**: 15m is a soft +8 additive bonus only — zero penalty when 15m disagrees. 1H is a 0.4× multiplier penalty. There is NO hard "all-3-must-agree" gate. This is the #1 WR killer.
- **Costs are undermodelled**: `COST_RT_PCT = 0.0029` (29bps). Real India costs = STT 0.025% + brokerage ~0.03% + GST 18% on brokerage + exchange charges + slippage = ~0.45% total. Backtest looks better than live because of this.
- **No minimum profit filter**: Only `qty < 1` is checked. Trades near the cost threshold generate friction for no net gain.
- **Walk-forward optimizer EXISTS but is disconnected**: `optimizer_india.py` has a full `WalkForwardOptimizer` with 6-param OOS sweep that writes `data/optimal_params.json` — but `backtest_engine_india.py` never reads this file. Parameters are all hardcoded.
- **No offline data source**: Only Upstox API (needs daily OAuth token) or yfinance (blocked by network policy). A local cache layer is missing.
- **Upstox token is fully manual**: No TOTP/auto-login. Manual daily renewal via Telegram `/newtoken`. `get_upstox_token.py` exists but is not integrated into `auth_upstox.py`.

---

## Work Units

### Unit 1 — Hard 3-Timeframe Alignment Gate
**Files**: `india/backtest_engine_india.py`
**Change**: Add 15m misalignment **penalty** (currently 0 — only gives +8 bonus when aligned, does nothing when misaligned) and a hard **block** when both 15m AND 1h disagree with signal direction.

Specifics:
- In both main loops, after `_score_bar()` returns and after the today-momentum/breadth/ML adjustments, but before final threshold check, add:
  ```python
  # 15m misalignment penalty (currently missing — only bonus exists in _score_bar)
  if df_15m available:
      if direction == "LONG" and 15m is bearish (e9 < e21): net_score -= 10
      if direction == "SHORT" and 15m is bullish (e9 > e21): net_score += 10
  # Hard block: both 15m AND 1h against direction
  if 15m disagrees AND 1h disagrees with direction:
      continue  # skip — all higher TFs against us
  ```
- The 1H already penalises via 0.4× in `_score_bar` at lines ~492-506. The 15m does NOT penalise — fix that.
- Expected WR improvement: +10-15 percentage points. This is the single highest-impact change.

---

### Unit 2 — Realistic Cost Model + Minimum Profit Filter
**Files**: `india/backtest_engine_india.py`
**Change**: Fix two cost modelling gaps that cause backtest to overstate real performance.

Specifics:
- Raise `COST_RT_PCT` from `0.0029` (line 53) to `0.0045` (0.45% round-trip) — realistic India total: STT 0.025% each leg, brokerage ~0.03%, GST 18% on brokerage, exchange charges, SEBI fee, slippage 0.1%
- Add minimum profit filter before opening a trade (after qty calculation, before `Trade(...)`):
  ```python
  # Expected profit must exceed 2× round-trip cost
  min_profit_pct = 2 * COST_RT_PCT  # 0.9%
  expected_profit = t1_dist * 0.4 + t2_dist * 0.6  # weighted avg exit (conservative)
  if expected_profit / entry < min_profit_pct:
      continue  # cost-inefficient trade, skip
  ```
- Add minimum SL distance: skip if `sl_dist < entry * 0.003` (< 0.3% SL is eaten by costs)
- Apply in BOTH loops (run_backtest and run_backtest_from_data)

---

### Unit 3 — Connect Walk-Forward Optimizer to Backtest Engine
**Files**: `india/backtest_engine_india.py`, `india/optimizer_india.py`
**Change**: Wire the existing (but disconnected) `WalkForwardOptimizer` into the backtest engine so parameters self-tune weekly.

Specifics:
- At startup of `run_backtest_from_data` (and `run_backtest`), add:
  ```python
  try:
      from optimizer_india import load_optimal_params
      _opt_params = load_optimal_params()  # reads data/optimal_params.json
      if _opt_params:
          MIN_SCORE = _opt_params.get("MIN_SCORE", MIN_SCORE)
          # similarly override ATR_SL_MULT, RVOL_MIN, etc.
  except Exception:
      pass
  ```
- After each trade closes (in wins/losses accounting), call:
  ```python
  try:
      from optimizer_india import get_optimizer
      get_optimizer().record_trade(trade)
  except Exception:
      pass
  ```
- In `optimizer_india.py`: add `load_optimal_params()` function that reads `data/optimal_params.json` and returns dict. Add `get_optimizer()` singleton accessor. Fix any missing params that engine needs (ensure `MIN_SCORE` is in the param grid).
- Check that `optimizer_india.py` param names match `backtest_engine_india.py` constant names exactly.

---

### Unit 4 — Local Data Cache (Offline Backtest)
**Files**: `india/data_cache.py` (new), `india/backtest_engine_india.py`
**Change**: Add a Parquet-based data cache so backtests run without any network/token dependency after initial data download.

Specifics:
- Create `india/data_cache.py`:
  ```python
  CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
  
  def save_data(data: Dict[str, pd.DataFrame], interval: str, source: str) -> None:
      """Save downloaded symbol data to Parquet files."""
      ...
  
  def load_data(symbols: List[str], interval: str, max_age_hours: int = 26) -> Dict[str, pd.DataFrame]:
      """Load cached data. Returns {} for symbols not in cache or stale."""
      ...
  
  def cache_stats() -> dict:
      """Return cache summary: symbols cached, oldest/newest timestamps, total size."""
      ...
  ```
  - Store as `data/cache/<SYMBOL>_<interval>.parquet`
  - Cache is valid for 26h for intraday (refresh daily), 7 days for daily bars
  - IST timezone preserved in Parquet index

- In `backtest_engine_india.py` main() function: add `--source cache` CLI option. When used, call `load_data()` instead of Upstox/yfinance. If cache miss, print which symbols are missing and their last cache date.
- Auto-save to cache after any successful Upstox or yfinance load (so cache builds up passively)

---

### Unit 5 — Upstox TOTP Auto-Login
**Files**: `india/auth_upstox.py`, `india/get_upstox_token.py`
**Change**: Automate the daily Upstox token renewal using TOTP + Upstox's login API (no browser needed).

Specifics:
- Read `get_upstox_token.py` first — it likely has the OAuth flow already sketched
- Add to `auth_upstox.py`:
  ```python
  def auto_renew_token() -> bool:
      """
      Auto-generate Upstox access token using TOTP.
      Reads: UPSTOX_EMAIL, UPSTOX_PASSWORD, UPSTOX_TOTP_SECRET, UPSTOX_API_KEY,
             UPSTOX_API_SECRET, UPSTOX_REDIRECT_URI from env.
      Returns True if successful.
      """
      # 1. POST to Upstox login endpoint with email+password
      # 2. POST TOTP code to 2FA endpoint  
      # 3. GET authorization code via redirect (handled via requests, no browser)
      # 4. Exchange code for access_token
      # 5. Call update_token_in_env(access_token)
      ...
  ```
- Use `pyotp` library for TOTP generation (`import pyotp; totp = pyotp.TOTP(secret); totp.now()`)
- Add `schedule_auto_renewal()` — registers a daily 8:30 AM IST job using the existing `schedule` library pattern in the codebase
- Call `auto_renew_token()` from the main trading loop's watchdog
- Add env vars: `UPSTOX_EMAIL`, `UPSTOX_PASSWORD`, `UPSTOX_TOTP_SECRET`, `UPSTOX_REDIRECT_URI`
- Fallback: if TOTP auto-login fails, fall back to Telegram `/newtoken` polling (already works)
- Update `.env.example` with new required vars

---

## E2E Test Recipe

Network access to yfinance and Upstox is blocked/unavailable on the CI server. Therefore:

**All units use this test recipe:**
```bash
cd /home/user/kingtrades

# 1. Syntax check
python -m py_compile india/<modified_file>.py && echo "syntax OK"

# 2. Import test
python -c "import sys; sys.path.insert(0, 'india'); from <module> import <key_fn>; print('import OK')"

# 3. Logic unit test with synthetic data
python -c "
import pandas as pd, numpy as np
from zoneinfo import ZoneInfo
IST = ZoneInfo('Asia/Kolkata')
# Build minimal synthetic OHLCV + indicators
# ... (each agent writes the appropriate synthetic test)
print('logic test passed')
"

# 4. Full engine dry-run (will fail on data load but must not crash on imports)
python india/backtest_engine_india.py --help
```

**No e2e backtest run** — network is blocked. Validate by: syntax OK + import OK + synthetic unit test + `--help` runs without crash.

---

## Conventions (all workers must follow)

- **IST timezone always**: `ZoneInfo("Asia/Kolkata")`. NEVER `datetime.now()` without tz. Use `get_current_ist_time()` from `utils.py`.
- **Both loops**: Any change to entry/exit logic in `backtest_engine_india.py` must go in BOTH `run_backtest` (Upstox, line ~1242) AND `run_backtest_from_data` (yfinance, line ~2373). Find anchors via `grep -n`.
- **Wrap in try/except**: New code accessing `row.get(...)` or external modules → `try: ... except Exception: pass`
- **Don't change MIN_SCORE hardcoded default** — Unit 3 overrides it at runtime, not compile time
- **No new dependencies** unless confirmed installable: `pyotp` (for TOTP) and `pyarrow`/`fastparquet` (for Parquet) are the only new ones. Check with `pip show pyotp pyarrow` first.
- **COST_RT_PCT**: Only Unit 2 changes this constant. Other units leave it at whatever value they find.
- **Branch**: All work goes to `claude/nse-momentum-groww-bot-hvkv9`

---

## Worker Template (copy verbatim into each agent prompt)

```
After you finish implementing the change:
1. **Code review** — Invoke the `Skill` tool with `skill: "code-review"` to find correctness bugs. Fix any findings before continuing.
2. **Run unit tests** — Run: `python -m py_compile india/<file>.py && echo OK` for each changed file.
3. **Test end-to-end** — Follow the e2e recipe from this prompt.
4. **Commit and push** — Commit with a clear message, push to `claude/nse-momentum-groww-bot-hvkv9`.
5. **Report** — End with: `PR: none — pushed to branch`
```
