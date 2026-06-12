# SataVector India — Deep Audit & Backtest Report
*Generated 2026-06-12 | Branch: claude/nse-momentum-groww-bot-hvkv9*

---

## 1. Deep Audit Findings

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

### Noted (not changed — by design or low impact)

- **Manual mode dormancy**: `MANUAL_SIGNALS_ONLY=True` (current production mode) means pyramiding, chandelier exits, partial exits, breakeven moves, and the Sortino/optimizer feedback loop **never engage** — positions aren't tracked when you trade manually in the Dhan app. These features only function in auto-execution mode. The optimizer and adaptive threshold also receive no trade outcomes in manual mode.
- **RS baseline quirk**: 5-day stock return is compared against Nifty's *daily* change. Harmless — the RS score is a cross-sectional percentile rank, and a constant offset cancels out.
- **Macro CPI/IIP blackouts** (17:30 IST) fall after market close — harmless. RBI MPC and PMI (10:00 IST) blackouts are the meaningful ones and work correctly.
- **Structural SL override** changes the stop but keeps ATR-based targets — intentional (stops on structure, targets on volatility).

---

## 2. Backtest Methodology — read this first

**This is NOT a historical replay.** Two reasons:
1. This environment has no market-data egress (NSE/Yahoo blocked), and
2. The India bot has **zero live trade history** to validate against.

Instead, this is a **3,000-path Monte Carlo simulation of the bot's exact mechanics**: Kelly-clamped sizing (0.1–1.5%) with the 20% position cap, ~1.2% ATR stops, 50% partial at 1R + runner (40% reach 2R / 35% trail out +0.5R / 25% breakeven), 15% of losers scratched at breakeven, 3-loss guard, −2% daily circuit, and the full NSE intraday cost stack (~0.15% of turnover round-trip: brokerage + STT + txn + GST + slippage).

The **win rate is an input, not a result**. Nobody can honestly tell you the live WR of an untested signal stack — so the table shows what each WR level produces.

## 3. Monte Carlo Results (₹5,00,000 capital, 21 trading days)

| Scenario | WR | Median month | P5 | P95 | P(profit) | P(≥20%) | Avg DD |
|----------|----|----------|------|------|--------|---------|--------|
| BEAR (edge fails) | 45% | **−2.1%** | −4.8% | +1.1% | 14% | 0% | 3.2% |
| BASE (realistic) | 55% | **+0.8%** | −2.8% | +4.7% | 64% | 0% | 2.3% |
| STRONG (tuned) | 62% | **+3.9%** | −0.2% | +8.2% | 94% | 0% | 1.7% |
| TARGET (70% WR) | 70% | **+8.6%** | +4.0% | +13.1% | 100% | 0% | 1.2% |

### Why 20%/month is unreachable with current sizing

The 20% position cap means a typical trade risks only ~0.24% of capital (20% position × 1.2% stop), regardless of what Kelly suggests. **Even a 70% WR bot makes ~8.6%/month median.** With Dhan MIS leverage (sizing against margin buying power instead of cash):

| Position cap | WR | Median month | P(≥20%) | Avg DD |
|------|----|----------|---------|--------|
| 40% (≈2x leverage) | 62% | +7.8% | 2% | 3.3% |
| 40% (≈2x leverage) | 70% | +17.7% | 35% | 2.4% |
| 60% (≈3x leverage) | 62% | +11.4% | 16% | 5.0% |
| 60% (≈3x leverage) | 70% | **+26.7%** | 76% | 3.6% |

**20% monthly requires BOTH a sustained 70% WR AND ~3x effective leverage.** That combination also triples drawdowns and makes the −2% daily circuit fire regularly. No systematic intraday equity desk sustains 70% WR at scale; 55–62% is the realistic band for a well-built momentum stack.

## 4. Live Expectations — honest summary

- **First month (manual signals mode, current setup)**: treat as a **validation period, not a profit period**. Expect 55–65% of alerted signals to be valid setups. Track every alert vs outcome — this builds the data the adaptive systems need.
- **Realistic steady state** (after tuning, auto-execution): **+2% to +6% per month** on cash sizing, with 2–4% drawdowns and losing months possible (P5 is negative until WR > 60%).
- **Good outcome**: 62%+ WR → **+4% to +8%/month**, ~94% probability of a profitable month.
- **The 20%/month target**: not achievable with cash-capped sizing at any believable WR. Requires deliberate leverage expansion **after** 200+ live trades prove a ≥62% WR — not before.
- **Costs matter**: at 4–6 trades/day, you pay roughly 1.8–2.7% of capital per month in costs at 20% position sizes. The edge has to clear that bar first.

### Recommended path
1. Run manual-signal mode 4+ weeks; journal every alert (the bot can't learn from manual trades).
2. If logged WR ≥ 58% over ≥60 signals → enable auto-execution with small capital so the feedback loops (Sortino, optimizer, adaptive threshold, ML) start learning.
3. Only after 200+ auto trades at ≥62% WR, consider raising position caps toward MIS margin.

*Run the simulation yourself: `python3 india/backtest_satavector_india.py`*
