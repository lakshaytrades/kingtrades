"""
backtest_satavector_india.py — SataVector India Monte Carlo Backtest

NOT a historical replay (no tick data needed). Simulates the bot's EXACT
trade mechanics over thousands of month-long paths:

  - Kelly-clamped risk sizing (0.1%–1.5%) with the 20%/25% position cap
  - ATR stop ~1.2% of price (lognormal), T1 partial 50% at +1R,
    runner trails to ~2R / +0.5R / breakeven
  - Breakeven move at +0.3% (15% of losers scratch at 0)
  - Loss guard: 3 consecutive losses pauses entries
  - Daily circuit: -2% capital stops the day
  - Real NSE intraday cost stack: brokerage Rs.20/order + STT 0.025% sell +
    txn/GST/stamp + slippage ≈ 0.12–0.18% of turnover round-trip

Run: python3 india/backtest_satavector_india.py
"""
import random
import statistics
import sys

CAPITAL          = 500_000.0
TRADING_DAYS     = 21
N_PATHS          = 3000
POSITION_CAP_PCT = 0.20      # 20% of capital per position (25% for A+)
SL_PCT_MEAN      = 0.012     # ATR(1.5x) stop ≈ 1.2% of price on NSE liquid names
SL_PCT_SD        = 0.004
COST_RT_PCT      = 0.0015    # 0.15% of turnover round-trip (brokerage+STT+slip)
DAILY_LOSS_LIMIT = 0.02
BE_SCRATCH_RATE  = 0.15      # fraction of losers scratched at breakeven

SCENARIOS = {
    "BEAR (edge fails)":     {"wr": 0.45, "signals": (2, 4)},
    "BASE (realistic)":      {"wr": 0.55, "signals": (3, 5)},
    "STRONG (tuned)":        {"wr": 0.62, "signals": (3, 6)},
    "TARGET (70% WR)":       {"wr": 0.70, "signals": (4, 6)},
}


def _kelly_risk_pct(wins: int, losses: int) -> float:
    """Mirror of main_india._kelly_fraction (half-Kelly, clamped 0.1–1.5%)."""
    total = wins + losses
    if total < 5:
        return 0.005
    p = wins / total
    b = 2.0                       # ATR_TP 3.0 / ATR_SL 1.5
    kelly = (p * b - (1 - p)) / b
    return max(0.001, min(0.015, kelly * 0.5))


def _runner_outcome(rng: random.Random) -> float:
    """R earned by the 50% runner after T1: trail to 2R, partial trail, or BE."""
    x = rng.random()
    if x < 0.40:
        return 2.0     # runner reaches T2 (2R)
    if x < 0.75:
        return 0.5     # trailed out mid-way
    return 0.0         # trailed back to breakeven


def simulate_month(wr: float, sig_range: tuple, rng: random.Random) -> dict:
    capital = CAPITAL
    wins = losses = trades = 0
    peak = capital
    max_dd = 0.0

    for _day in range(TRADING_DAYS):
        day_start = capital
        consec_losses = 0
        n_signals = rng.randint(*sig_range)

        for _sig in range(n_signals):
            if consec_losses >= 3:                  # loss guard
                break
            if (capital - day_start) / day_start <= -DAILY_LOSS_LIMIT:
                break                               # daily circuit

            sl_pct = max(0.004, rng.lognormvariate(0, 0.3) * SL_PCT_MEAN)
            risk_pct = _kelly_risk_pct(wins, losses)

            # Position sizing exactly as _calculate_qty: risk/SL, capped
            position_val = min(capital * risk_pct / sl_pct,
                               capital * POSITION_CAP_PCT)
            risk_inr = position_val * sl_pct        # actual 1R in rupees
            cost = position_val * COST_RT_PCT

            if rng.random() < wr:
                # 50% exits at +1R, 50% runner
                r_mult = 0.5 * 1.0 + 0.5 * _runner_outcome(rng)
                pnl = risk_inr * r_mult - cost
                wins += 1
                consec_losses = 0
            else:
                if rng.random() < BE_SCRATCH_RATE:
                    pnl = -cost                     # breakeven scratch
                else:
                    pnl = -risk_inr - cost
                losses += 1
                consec_losses += 1

            capital += pnl
            trades += 1
            peak = max(peak, capital)
            max_dd = max(max_dd, (peak - capital) / peak)

    return {
        "return_pct": (capital - CAPITAL) / CAPITAL * 100,
        "trades": trades,
        "wr": wins / trades if trades else 0,
        "max_dd": max_dd * 100,
    }


def run_scenario(name: str, cfg: dict) -> dict:
    rng = random.Random(42)
    results = [simulate_month(cfg["wr"], cfg["signals"], rng)
               for _ in range(N_PATHS)]
    rets = sorted(r["return_pct"] for r in results)
    dds  = [r["max_dd"] for r in results]
    return {
        "name": name,
        "wr_in": cfg["wr"],
        "median": statistics.median(rets),
        "mean": statistics.mean(rets),
        "p5":  rets[int(0.05 * N_PATHS)],
        "p95": rets[int(0.95 * N_PATHS)],
        "prob_profit": sum(1 for r in rets if r > 0) / N_PATHS * 100,
        "prob_20pct":  sum(1 for r in rets if r >= 20) / N_PATHS * 100,
        "avg_dd": statistics.mean(dds),
        "worst_dd": max(dds),
        "avg_trades": statistics.mean(r["trades"] for r in results),
    }


def main():
    print(f"SataVector India — Monte Carlo Backtest "
          f"({N_PATHS} paths x {TRADING_DAYS} days, Rs.{CAPITAL:,.0f})")
    print("=" * 96)
    hdr = (f"{'SCENARIO':<22}{'WR':>5}{'MEDIAN':>9}{'MEAN':>8}"
           f"{'P5':>8}{'P95':>8}{'P(>0)':>8}{'P(>20%)':>9}"
           f"{'AvgDD':>8}{'Trades':>8}")
    print(hdr)
    print("-" * 96)
    for name, cfg in SCENARIOS.items():
        s = run_scenario(name, cfg)
        print(f"{s['name']:<22}{s['wr_in']*100:>4.0f}%"
              f"{s['median']:>8.1f}%{s['mean']:>7.1f}%"
              f"{s['p5']:>7.1f}%{s['p95']:>7.1f}%"
              f"{s['prob_profit']:>7.0f}%{s['prob_20pct']:>8.1f}%"
              f"{s['avg_dd']:>7.1f}%{s['avg_trades']:>8.0f}")
    print("=" * 96)
    print("Monthly return distribution per scenario. Costs: 0.15% turnover RT.")
    print("Position cap 20% of capital is the binding sizing constraint.")


if __name__ == "__main__":
    main()
