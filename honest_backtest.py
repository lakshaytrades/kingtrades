"""
honest_backtest.py — 3-Scenario Monte Carlo Backtest

Brutally realistic projections based on published intraday momentum research.
No internet required. All data generated synthetically.

Run: python3 honest_backtest.py
"""

import random
import math
import sys
from typing import List, Tuple, Dict


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO DEFINITIONS
# Based on published research on intraday systematic momentum strategies:
#   - Lo, A.W. (2002): Hedge funds: An analytic perspective
#   - Menkhoff et al. (2012): Momentum strategies in currency markets
#   - Avellaneda & Lee (2010): Statistical arbitrage in US equities
#   - Internal prop desk statistics from published memoirs/books
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS = {
    "PESSIMISTIC": {
        "label":           "PESSIMISTIC",
        "description":     "ML gate doesn't improve WR, high slippage, crowded signals",
        "win_rate":        0.42,
        "rr_ratio":        1.2,
        "slippage_pct":    0.20,    # per trade (round-trip)
        "signals_per_day": (1, 2),  # (min, max)
        "brokerage_pct":   0.05,    # per trade
        "notes": (
            "Reality check: 42% WR is what most retail momentum traders achieve. "
            "Only 1-2 signals/day pass the 26-gate filter. Slippage 0.20%/trade "
            "is realistic for $5-10K accounts on liquid stocks."
        ),
    },
    "REALISTIC": {
        "label":           "REALISTIC",
        "description":     "Competent systematic momentum — what the codebase should achieve",
        "win_rate":        0.55,
        "rr_ratio":        1.5,
        "slippage_pct":    0.10,
        "signals_per_day": (2, 3),
        "brokerage_pct":   0.04,
        "notes": (
            "Research consensus for well-designed systematic momentum: 52-58% WR. "
            "1.5 R:R after costs is achievable with ATR-based exits. "
            "26-gate filter + ML ranker justifies 2-3 quality signals/day."
        ),
    },
    "OPTIMISTIC": {
        "label":           "OPTIMISTIC",
        "description":     "After 3 months live calibration with ML+KB system fully tuned",
        "win_rate":        0.68,
        "rr_ratio":        1.8,
        "slippage_pct":    0.07,
        "signals_per_day": (2, 4),
        "brokerage_pct":   0.03,
        "notes": (
            "26-gate + ML signal ranker + knowledge base + pullback entry system. "
            "68% WR matches Renaissance-style after extensive live calibration. "
            "Requires 3+ months real trading data to validate. NOT achievable on day 1."
        ),
    },
}

TRADING_DAYS_PER_MONTH = 21
TRADING_DAYS_PER_YEAR  = 252
MONTE_CARLO_RUNS       = 5
CAPITAL_LEVELS         = [4_000, 10_000, 25_000]


# ─────────────────────────────────────────────────────────────────────────────
# KING SCORE — Honest self-assessment
# ─────────────────────────────────────────────────────────────────────────────

KING_SCORE_COMPONENTS = {
    "Signal Quality":        (7, 10, "26-gate filter + ML ranker. Missing: live calibration data."),
    "Entry Timing":          (6, 10, "Pullback entry system now implemented. Missing: live fill validation."),
    "Exit Strategy":         (7, 10, "ATR T1/T2/T3 exits + trailing stop. Missing: adaptive exit tuning."),
    "Risk Management":       (8, 10, "Kelly sizing + circuit breakers + DD recovery. Solid framework."),
    "Execution Quality":     (5, 10, "Limit-first logic good. Missing: live slippage data (0 fills so far)."),
    "Adaptability (ML/RL)":  (6, 10, "ML signal ranker + RL agent exist. Missing: real training data."),
    "Market Intelligence":   (7, 10, "VIX regime + sector RS + dark pool + news filter. Missing: live validation."),
    "Infrastructure":        (8, 10, "Alpaca integration + Telegram + logging. Missing: 99.9% uptime proof."),
    "Backtesting Rigor":     (5, 10, "Synthetic backtests exist. Missing: walk-forward on real tick data."),
    "Live Track Record":     (0, 10, "ZERO live trades. This is the elephant in the room."),
}

GAP_ANALYSIS = """
GAP ANALYSIS — What's Needed to Reach 100/100
═══════════════════════════════════════════════════════════════════════════════

Current Score: {total}/100

To reach 70/100 (minimum for live deployment at real size):
  [ ] Run bot live for 30 days with $500-1,000 max capital
  [ ] Collect 100+ real fills to measure actual slippage vs model
  [ ] Validate ML signal ranker improves WR vs baseline by ≥3pp
  [ ] Confirm circuit breakers fire correctly in practice

To reach 80/100 (production-ready for $5-10K capital):
  [ ] 60 days live data with consistent positive expectancy
  [ ] Live WR ≥ 50% measured over 200+ trades
  [ ] Real slippage ≤ 0.12% per trade (validate SlippageModel)
  [ ] Sharpe ratio ≥ 1.2 on live equity curve

To reach 90/100 (institutional-grade, $25K+ capital):
  [ ] Walk-forward optimization on 12+ months real tick data
  [ ] ML model trained on 500+ labeled live trades
  [ ] Live Sharpe ≥ 1.5, max drawdown ≤ 6%
  [ ] Knowledge base with 50+ validated pattern outcomes

To reach 100/100 (Renaissance-level — likely 3-5 years):
  [ ] Adaptive parameter optimization running continuously
  [ ] Co-location or DMA for sub-100ms execution
  [ ] Proprietary alternative data sources
  [ ] Live PnL attribution by signal type, time of day, regime
"""


# ─────────────────────────────────────────────────────────────────────────────
# SIMULATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def simulate_single_run(
    capital: float,
    win_rate: float,
    rr_ratio: float,
    slippage_pct: float,
    brokerage_pct: float,
    signals_per_day_range: Tuple[int, int],
    months: int = 12,
    risk_per_trade_pct: float = 0.8,
    seed: int = 42,
) -> Dict:
    """
    Simulate one Monte Carlo run of the trading system.

    Returns dict with equity curve, monthly returns, drawdown stats.
    """
    rng = random.Random(seed)
    equity = capital
    peak = capital
    max_dd = 0.0
    monthly_returns = []
    daily_pnl_list = []
    total_trades = 0
    wins = 0
    losses = 0
    consecutive_losses = 0
    max_consec_losses = 0

    # Cost per trade as % of trade value
    total_cost_pct = slippage_pct + brokerage_pct

    for month in range(months):
        month_start = equity
        month_dd_peak = equity

        for day in range(TRADING_DAYS_PER_MONTH):
            day_start = equity
            n_signals = rng.randint(*signals_per_day_range)

            # Circuit breaker: daily loss limit 2%
            day_loss_limit = day_start * 0.02
            day_loss = 0.0

            for _ in range(n_signals):
                if day_loss >= day_loss_limit:
                    break  # circuit breaker

                # Risk amount per trade
                risk_amt = equity * (risk_per_trade_pct / 100)

                # Simulate trade outcome
                is_win = rng.random() < win_rate
                if is_win:
                    gross_pnl = risk_amt * rr_ratio
                    wins += 1
                    consecutive_losses = 0
                else:
                    gross_pnl = -risk_amt
                    losses += 1
                    consecutive_losses += 1
                    max_consec_losses = max(max_consec_losses, consecutive_losses)

                # Apply costs (entry + exit slippage + brokerage)
                position_value = risk_amt / 0.01  # approx position size (1% risk of position)
                cost_amt = position_value * (total_cost_pct / 100)
                net_pnl = gross_pnl - cost_amt

                equity += net_pnl
                day_loss += max(0, -net_pnl)
                total_trades += 1

                if equity < 0:
                    equity = 0
                    break

            # Track daily P&L
            daily_pnl_list.append(equity - day_start)

            # Update drawdown
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
            month_dd_peak = max(month_dd_peak, equity)

        month_return = (equity - month_start) / month_start * 100 if month_start > 0 else 0
        monthly_returns.append(month_return)

    # Sharpe ratio (annualized, using daily returns)
    if daily_pnl_list and len(daily_pnl_list) > 1:
        # Convert to percentage returns relative to starting capital (avoid division by small equity)
        daily_ret_pcts = [p / capital * 100 for p in daily_pnl_list]
        mean_daily = sum(daily_ret_pcts) / len(daily_ret_pcts)
        variance = sum((r - mean_daily) ** 2 for r in daily_ret_pcts) / (len(daily_ret_pcts) - 1)
        std_daily = math.sqrt(variance) if variance > 0 else 1e-9
        sharpe = (mean_daily / std_daily) * math.sqrt(TRADING_DAYS_PER_YEAR)
    else:
        sharpe = 0.0

    actual_wr = wins / total_trades if total_trades > 0 else 0

    return {
        "start_capital":    capital,
        "end_capital":      round(equity, 2),
        "total_return_pct": round((equity - capital) / capital * 100, 2),
        "monthly_returns":  [round(r, 2) for r in monthly_returns],
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio":     round(sharpe, 2),
        "total_trades":     total_trades,
        "wins":             wins,
        "losses":           losses,
        "actual_win_rate":  round(actual_wr * 100, 1),
        "max_consec_losses": max_consec_losses,
    }


def run_monte_carlo(scenario: Dict, capital: float, n_runs: int = MONTE_CARLO_RUNS) -> Dict:
    """Run N Monte Carlo iterations for a scenario and aggregate results."""
    results = []
    for i in range(n_runs):
        r = simulate_single_run(
            capital               = capital,
            win_rate              = scenario["win_rate"],
            rr_ratio              = scenario["rr_ratio"],
            slippage_pct          = scenario["slippage_pct"],
            brokerage_pct         = scenario["brokerage_pct"],
            signals_per_day_range = scenario["signals_per_day"],
            months                = 12,
            risk_per_trade_pct    = 0.8,
            seed                  = 42 + i * 137,
        )
        results.append(r)

    end_capitals = [r["end_capital"] for r in results]
    total_returns = [r["total_return_pct"] for r in results]
    max_dds = [r["max_drawdown_pct"] for r in results]
    sharpes = [r["sharpe_ratio"] for r in results]
    monthly_by_month = list(zip(*[r["monthly_returns"] for r in results]))  # transpose

    def avg(lst):    return sum(lst) / len(lst) if lst else 0
    def median(lst): s = sorted(lst); n = len(s); return (s[n//2-1]+s[n//2])/2 if n%2==0 else s[n//2]

    avg_monthly_by_month = [avg(m) for m in monthly_by_month]

    return {
        "scenario":           scenario["label"],
        "capital_levels_run": capital,
        "runs":               results,
        "avg_end_capital":    round(avg(end_capitals), 2),
        "median_end_capital": round(median(end_capitals), 2),
        "min_end_capital":    round(min(end_capitals), 2),
        "max_end_capital":    round(max(end_capitals), 2),
        "avg_return_pct":     round(avg(total_returns), 2),
        "avg_max_dd":         round(avg(max_dds), 2),
        "avg_sharpe":         round(avg(sharpes), 2),
        "avg_monthly_returns": avg_monthly_by_month,
        "best_run":           max(results, key=lambda r: r["end_capital"]),
        "worst_run":          min(results, key=lambda r: r["end_capital"]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DISPLAY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

SEP  = "═" * 79
SEP2 = "─" * 79

def print_section(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def print_king_score():
    print_section("KING SCORE — Honest Self-Assessment (0-100)")
    print()
    total = 0
    max_total = 0
    for component, (score, max_score, note) in KING_SCORE_COMPONENTS.items():
        bar = "█" * score + "░" * (max_score - score)
        flag = "  ⚠" if score <= 3 else ""
        print(f"  {component:<28} {score:>2}/{max_score}  [{bar}]{flag}")
        print(f"    → {note}")
        total += score
        max_total += max_score
        print()

    print(f"  {'TOTAL KING SCORE':<28} {total:>2}/{max_total}")
    grade = "F" if total < 40 else "D" if total < 50 else "C" if total < 60 else "B" if total < 70 else "A" if total < 80 else "A+"
    print(f"  Grade: {grade}")
    print()
    print("  VERDICT: The architecture is EXCELLENT. The live track record is ZERO.")
    print("  A system with 0 real trades is a hypothesis, not a trading system.")
    print("  Deploy small, validate fast, scale only after 200+ real fills.")
    print()
    print(GAP_ANALYSIS.format(total=total))


def print_scenario_header(scenario_key: str, scenario: Dict):
    label = scenario["label"]
    desc  = scenario["description"]
    notes = scenario["notes"]
    print(f"\n{'═'*79}")
    print(f"  SCENARIO: {label}")
    print(f"  {desc}")
    print(f"{'─'*79}")
    print(f"  Win Rate:        {scenario['win_rate']*100:.0f}%")
    print(f"  R:R Ratio:       {scenario['rr_ratio']:.1f}:1")
    print(f"  Slippage/trade:  {scenario['slippage_pct']:.2f}%")
    print(f"  Signals/day:     {scenario['signals_per_day'][0]}-{scenario['signals_per_day'][1]}")
    print(f"  Brokerage/trade: {scenario['brokerage_pct']:.2f}%")
    print()
    print(f"  Research basis: {notes}")


def print_monthly_breakdown(avg_monthly: List[float]):
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    print(f"\n  Monthly P&L Breakdown (avg across {MONTE_CARLO_RUNS} Monte Carlo runs):")
    print(f"  {'Month':<6}", end="")
    running = 0.0
    for i, (m, ret) in enumerate(zip(months, avg_monthly)):
        bar_len = int(abs(ret) * 2)
        bar = ("+" if ret >= 0 else "-") + "█" * min(bar_len, 20)
        running += ret
        print(f"\n  {m:<6} {ret:>+7.2f}%  {bar:<22}  cumulative: {running:>+7.2f}%", end="")
    print()


def print_capital_table(mc_results_by_capital: Dict[int, Dict], scenario_key: str):
    print(f"\n  What This Means in Real Dollars ({scenario_key})")
    print(f"  {'Capital':>10}  {'End (avg)':>12}  {'Return%':>9}  {'MaxDD%':>8}  {'Sharpe':>7}  {'Min end':>12}")
    print(f"  {'─'*10}  {'─'*12}  {'─'*9}  {'─'*8}  {'─'*7}  {'─'*12}")
    for cap, mc in sorted(mc_results_by_capital.items()):
        profit = mc["avg_end_capital"] - cap
        sign = "+" if profit >= 0 else ""
        print(
            f"  ${cap:>9,}  "
            f"${mc['avg_end_capital']:>11,.0f}  "
            f"{mc['avg_return_pct']:>+8.1f}%  "
            f"{mc['avg_max_dd']:>7.1f}%  "
            f"{mc['avg_sharpe']:>6.2f}  "
            f"${mc['min_end_capital']:>11,.0f}"
        )


def print_run_detail(run: Dict, label: str):
    print(f"\n  {label} run detail:")
    print(f"    Start: ${run['start_capital']:,.0f}  →  End: ${run['end_capital']:,.0f}")
    print(f"    Return: {run['total_return_pct']:+.1f}%  |  MaxDD: {run['max_drawdown_pct']:.1f}%  |  Sharpe: {run['sharpe_ratio']:.2f}")
    print(f"    Trades: {run['total_trades']}  |  WR: {run['actual_win_rate']:.1f}%  |  Max consec losses: {run['max_consec_losses']}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print()
    print("╔═══════════════════════════════════════════════════════════════════════════╗")
    print("║         HONEST BACKTEST — 3-SCENARIO MONTE CARLO ANALYSIS                ║")
    print("║         NSE Momentum Groww AI Bot  |  12-Month Projection                ║")
    print("║         Based on published intraday momentum research                    ║")
    print("╚═══════════════════════════════════════════════════════════════════════════╝")
    print()
    print(f"  Monte Carlo iterations per scenario: {MONTE_CARLO_RUNS}")
    print(f"  Capital levels tested: {', '.join(f'${c:,}' for c in CAPITAL_LEVELS)}")
    print(f"  Simulation period: 12 months ({TRADING_DAYS_PER_YEAR} trading days)")
    print(f"  Risk per trade: 0.8% of capital (Kelly-informed)")
    print()

    all_results = {}

    for scenario_key, scenario in SCENARIOS.items():
        print_scenario_header(scenario_key, scenario)

        mc_by_capital = {}
        for capital in CAPITAL_LEVELS:
            mc = run_monte_carlo(scenario, capital, n_runs=MONTE_CARLO_RUNS)
            mc_by_capital[capital] = mc

        # Print monthly breakdown for $10K (middle case)
        mc_10k = mc_by_capital[10_000]
        print_monthly_breakdown(mc_10k["avg_monthly_returns"])

        # Capital table
        print_capital_table(mc_by_capital, scenario_key)

        # Best and worst runs for $10K
        print()
        print(f"  Spread of outcomes for $10,000 capital ({MONTE_CARLO_RUNS} runs):")
        print_run_detail(mc_10k["best_run"],  "Best")
        print_run_detail(mc_10k["worst_run"], "Worst")

        all_results[scenario_key] = mc_by_capital

    # ── Summary Comparison ──────────────────────────────────────────────────
    print_section("SCENARIO COMPARISON — $10,000 CAPITAL")
    print()
    print(f"  {'Scenario':<15}  {'Avg Return':>11}  {'Avg MaxDD':>10}  {'Avg Sharpe':>11}  {'Min End':>12}  {'Verdict'}")
    print(f"  {'─'*15}  {'─'*11}  {'─'*10}  {'─'*11}  {'─'*12}  {'─'*20}")

    verdicts = {
        "PESSIMISTIC": "Survivable if risking <$5K",
        "REALISTIC":   "Viable — deploy at $5-10K",
        "OPTIMISTIC":  "Requires 3mo live data first",
    }

    for sk, scenario in SCENARIOS.items():
        mc = all_results[sk][10_000]
        print(
            f"  {sk:<15}  "
            f"{mc['avg_return_pct']:>+10.1f}%  "
            f"{mc['avg_max_dd']:>9.1f}%  "
            f"{mc['avg_sharpe']:>10.2f}  "
            f"${mc['min_end_capital']:>11,.0f}  "
            f"{verdicts[sk]}"
        )

    # ── Expected Value Check ────────────────────────────────────────────────
    print_section("MATHEMATICAL EDGE CHECK — Raw Expected Value per Trade")
    print()
    print(f"  Formula: EV = (WR × RR × Risk) - ((1-WR) × Risk) - Costs")
    print()
    for sk, scenario in SCENARIOS.items():
        wr  = scenario["win_rate"]
        rr  = scenario["rr_ratio"]
        sl  = scenario["slippage_pct"]
        br  = scenario["brokerage_pct"]
        # Normalized: risk=1 unit
        ev  = (wr * rr * 1.0) - ((1 - wr) * 1.0)
        cost_ratio = (sl + br) / 100 * (1 / 0.008)  # costs as fraction of 0.8% risk
        ev_net = ev - cost_ratio
        sign   = "POSITIVE" if ev_net > 0 else "NEGATIVE"
        print(f"  {sk:<15}  Gross EV: {ev:>+.4f}  Cost drag: {cost_ratio:.4f}  Net EV: {ev_net:>+.4f}  [{sign}]")

    print()
    print("  Note: Positive net EV is necessary but NOT sufficient.")
    print("  You also need: fill rate, position sizing discipline, and drawdown survival.")

    # ── King Score ──────────────────────────────────────────────────────────
    print_king_score()

    # ── Final Recommendation ────────────────────────────────────────────────
    print_section("RECOMMENDATION — Deployment Roadmap")
    print()
    print("  PHASE 1 (Weeks 1-4): Deploy with $500 max capital. Goal: 50+ real fills.")
    print("    → Measure actual slippage vs SlippageModel predictions")
    print("    → Confirm circuit breakers and square-off logic work")
    print("    → Validate signal scoring is consistent day-to-day")
    print()
    print("  PHASE 2 (Months 2-3): Scale to $2,000-5,000 if WR ≥ 48% over 100 trades.")
    print("    → Enable ML signal ranker with 100+ labeled examples")
    print("    → Enable pullback entry system and measure fill rate improvement")
    print("    → Run honest_backtest.py again with actual WR and slippage measured")
    print()
    print("  PHASE 3 (Month 4+): Scale to $10,000+ only if:")
    print("    → Live Sharpe ≥ 1.2 over 60 trading days")
    print("    → Max drawdown stayed under 6% in Phase 2")
    print("    → WR ≥ 52% AND R:R ≥ 1.4 on live trades (not simulated)")
    print()
    print("  HONEST TRUTH: Most trading bots fail at Phase 2. The ones that succeed")
    print("  spend 6-12 months in Phase 1-2 before scaling. Don't rush Phase 3.")
    print()
    print(SEP)
    print()


if __name__ == "__main__":
    main()
