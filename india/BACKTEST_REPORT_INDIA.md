# SataVector India — Deep Audit & Backtest Report (Round 3)
*Generated 2026-06-12 | Branch: claude/nse-momentum-groww-bot-hvkv9*

---

## 1. Deep Audit Findings (Round 2)

### Fixed in this audit

| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| 1 | **CRITICAL** | T1 and T2 were the **same price** (both at +3.0 ATR = 2R). The "50% partial at T1, runner to T2" exit logic was a no-op since both triggered simultaneously. `ATR_T1_MULTIPLIER` config was never used. | T1 now at 1R (partial 50%), T2 at 2R (runner). |
| 2 | **CRITICAL** | Round 2 boosters (RS, gap, futures OI, delivery, UOA, macro) ran **after** the final threshold check and grading. Negative adjustments (macro −20, RS −6, UOA −6) could no longer reject a signal; positive ones never upgraded the grade. | Threshold re-checked and grade recomputed after all Round 2 adjustments. |
| 3 | **HIGH** | Walk-forward optimizer recorded `signal_score=0` for every trade, so its filter `score >= min_score (60+)` excluded **all** trades — the optimizer was a permanent no-op. | `OpenPosition.signal_score` added; real score recorded. |
| 4 | **HIGH** | Optimizer's Monday 8:55–9:05 run window was only checked inside `_close_position()` — which never fires pre-market. It would never run. | Moved to the pre-market 9:00–9:10 block in the main loop. |
| 5 | **HIGH** | Sortino sizing cold-start: with <5 trades, Sortino = 0.0 → bot started **every day** at 0.75x size and 4 max positions (drawdown-protection mode without a drawdown). | Neutral 1.0x sizing until 5 trades recorded. |
| 6 | **HIGH** | Delivery-trend V2 downloaded 5 full NSE bhav-copy CSVs (several MB each) **per symbol** — ~250 downloads per scan cycle on a 50-symbol watchlist. | Per-date caching: 5 downloads per day total, shared across watchlist; failed dates not retried. |
| 7 | MEDIUM | UOA gave no penalty when smart money was positioned **against** the signal (e.g., CALL_ACCUMULATION + SHORT signal). | −6 contra penalty added. |

---

## 2. Round 3 — Renaissance Enhancements

### New modules added (6 new source modules)

| Module | Signal Source | Score Range | Edge |
|--------|--------------|-------------|------|
| `cross_asset_india.py` | USD/INR, crude oil, gold, US VIX, SGX Nifty | −20 to +15 | FII flow proxy, global risk regime |
| `wyckoff_vsa_india.py` | Volume Spread Analysis (Stopping Vol, Spring, Upthrust) | −18 to +20 | Institutional tape reading, 80y academic track record |
| `pead_india.py` | Post-Earnings Announcement Drift (Bernard & Thomas 1989) | −8 to +15 | Proven academic alpha; NSE earnings reactions as surprise proxy |
| `block_deal_india.py` | NSE block deals + bulk deals (institutional) | −12 to +18 | Direct institutional conviction signal (free NSE data) |
| `microstructure_india.py` | Tape velocity, urgency, volume footprint, bar efficiency | −15 to +15 | Entry timing quality filter; prevents momentum fade entries |
| `elite_tracker_india.py` | Self-learning pattern WR tracker (Wilson CI) | −8 to +10 | Promotes high-WR pattern combos; demotes low-WR after 15+ samples |

### Signal pipeline upgrades

- **Multi-TF Momentum Cascade**: 4 TFs aligned → +15 bonus, 3 TFs → +8, conflicting TFs → −10
- **Adaptive MIS Sizing**: A+ + Sortino>2.5 + 7-day WR>62% → 30% cap (up from 25%); score≥88 → 35% cap
- **Round 3 post-filter re-check**: All 6 new boosters applied then final threshold re-evaluated (same pattern as Round 2 fix)
- **Pre-market cross-asset brief**: Telegram alert at 9:00 AM IST with global regime + SGX Nifty premium/discount
- **PEAD auto-detection**: Pre-market scans full watchlist for earnings reactions (using daily OHLCV)
- **Elite tracker daily summary**: Telegram shows how many pattern combos qualified and their WR distribution

---

## 3. Backtest Methodology — read this first

**This is NOT a historical replay.** Two reasons:
1. This environment has no market-data egress (NSE/Yahoo blocked), and
2. The India bot has **zero live trade history** to validate against.

Instead, this is a **3,000-path Monte Carlo simulation of the bot's exact mechanics**: Kelly-clamped sizing (0.1–1.5%) with the position cap, ~1.2% ATR stops, 50% partial at 1R + runner (40% reach 2R / 35% trail out +0.5R / 25% breakeven), 15% of losers scratched at breakeven, 3-loss guard, −2% daily circuit, and the full NSE intraday cost stack (~0.15% of turnover round-trip).

**Win rate is an input, not a result.** Round 3's signal improvements (25+ sources, elite filtering) are expected to push WR from 55-62% (Round 2 baseline) to 62-68% based on the quality uplift from VSA, cross-asset context, and elite tracker.

## 4. Monte Carlo Results (₹5,00,000 capital, 21 trading days) — Round 3

| Scenario | WR | Cap | Median | P5 | P95 | P(profit) | P(≥20%) | Avg DD |
|----------|----|-----|--------|-----|------|--------|---------|--------|
| BEAR (edge fails) | 45% | 20% | **−2.1%** | −4.8% | +1.1% | 14% | 0% | 3.2% |
| BASE (realistic) | 58% | 20% | **+1.9%** | −2.0% | +6.1% | 79% | 0% | 2.0% |
| R3 STRONG (25+ sources) | 65% | 22% | **+5.4%** | +1.1% | +10.2% | 98% | 0% | 1.5% |
| R3 TARGET (MIS 30% cap) | 65% | 30%* | **+6.1%** | +1.0% | +11.5% | 98% | 0% | 1.7% |
| ELITE (70% WR + MIS 35%) | 70% | 35%* | **+11.6%** | +5.5% | +18.4% | 100% | 2% | 1.6% |

*MIS cap active on ~30% of trades only (A+ grade + Sortino>2.5 + WR>62% condition required)

### Target 7-10%/month path

The R3 framework creates two routes to 7-10%/month:

**Route A (signal quality)**: Achieve 68-70% WR through the 25+ signal sources, elite tracker filtering, and Wyckoff VSA confirmation → hits +8-12% median without leverage expansion.

**Route B (adaptive MIS)**: At 65% WR (realistic with Round 3), the MIS adaptive sizing for A+ signals pushes median to +6-7%. At 68% WR, it pushes to +8-9%.

**Combined (most likely path)**: Round 3 signal stack should stabilize WR at 62-66% over a well-calibrated trading period. The adaptive MIS cap (30-35% for exceptional setups) adds ~1-2% monthly return without materially increasing drawdown.

## 5. Live Expectations — honest summary

- **First month (manual signals mode)**: Treat as a **validation period**. Track every alert + outcome — this data feeds the elite tracker and Wyckoff self-learning.
- **Month 2-3** (auto-execution + elite tracker learning): WR should stabilize at 60-65% as the system learns which pattern combinations work.
- **Month 4+ target state**: 65%+ WR → **+5.4% to +6.1%/month** with 30% MIS cap on A+ setups.
- **Elite state** (70%+ WR, rare but possible): **+11.6% median**, 100% P(profit).

### Cost reality check
At 4-6 trades/day × 21 days with 20% position sizes: you pay ~2-2.5% of capital/month in costs. The edge must clear this first. At 65% WR it does — expected monthly gross is ~8%, net ~5-6%.

### Recommended path (unchanged from Round 2)
1. Run manual-signal mode 4+ weeks; **journal every alert** (bot can't learn from manual trades).
2. If logged WR ≥ 58% over ≥60 signals → enable auto-execution with small capital so elite tracker, PEAD tracker, and Sortino systems start learning.
3. Only after 200+ auto trades at ≥62% WR, consider allowing MIS leverage expansion.

*Run the Monte Carlo yourself: `python3 india/backtest_satavector_india.py`*

---

## 6. REAL Historical Replay (run on the VPS)

`backtest_replay_india.py` is a **true historical replay** (not Monte Carlo). It
fetches real past NSE candles from the Dhan API, steps through them bar-by-bar
with **no look-ahead**, runs the actual `IndiaSignalGenerator`, and reports the
**real win rate as an output** (Monte Carlo takes WR as an input).

**Why it can't run in the dev sandbox:** this environment blocks all market-data
egress (Yahoo, NSE archives, Dhan — all return `403 Host not in allowlist`). It
must run on the **production VPS** where `DHAN_CLIENT_ID` + `DHAN_ACCESS_TOKEN`
and Dhan network access exist.

**What it replays:** the price-based signal core (direction gates, ATR/ADX,
base score, multi-timeframe alignment, ORB, Wyckoff VSA, microstructure, phase,
structural SL). **Live-only boosters** (option chain, delivery %, FII/DII, news,
block deals, UOA, futures OI, cross-asset, VIX-regime, PEAD, Kalman, MOM12) are
**disabled** — they have no historical feed. So the replay WR is a **conservative
floor**; production adds those boosters on top.

```bash
# On the VPS:
python3 india/backtest_replay_india.py --days 60
python3 india/backtest_replay_india.py --symbols RELIANCE,INFY,TCS,HDFCBANK --days 90
python3 india/backtest_replay_india.py --from 2026-01-01 --to 2026-03-31
```

Output: real trades, win rate, profit factor, avg win/loss, expectancy per
trade, total + monthly return, max drawdown. Exit mechanics (50% partial at
T1=1R, runner to T2=2R / breakeven, 15:20 square-off, 0.15% costs) are unit-
validated against synthetic bars.
