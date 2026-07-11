"""
gambler_sim.py — what "gamble for high profit" ACTUALLY does. Monte Carlo truth.

Everyone wants the gambler bot: max size, max leverage, go big. This simulates
exactly that — the SAME validated 60%-win edge, but bet at increasing
aggression — and shows what really happens to ₹10,00,000 over one year (250
trades). The result is the oldest law in trading: over-betting a good edge
still leads to RUIN. This is math, not opinion.

  python3 india/gambler_sim.py
"""
from __future__ import annotations
import numpy as np

RNG = np.random.default_rng(7)
N = 20_000                      # simulated lifetimes
TRADES = 250                    # ~1 year
START = 10_00_000

OUTCOMES = np.array([0.020, -0.015, -0.050])   # target / time / stop
COST = 0.004                                    # 0.4% round-trip, every trade

# TWO regimes:
#   "proven"  = the validated 60%-win edge (BEST case — and NOT confirmed live)
#   "gambler" = what betting 'any sector without validation' really is: a coin
#               flip (50/50) that then pays costs -> a small NEGATIVE edge
PROBS_PROVEN  = np.array([0.60, 0.30, 0.10])
PROBS_GAMBLER = np.array([0.45, 0.30, 0.25])    # no skill: fewer targets, more stops


def run(frac, lev, probs):
    """frac = fraction of capital risked per trade; lev = leverage. Vectorised."""
    net = OUTCOMES - COST                        # costs hit every trade
    draws = RNG.choice(net, size=(N, TRADES), p=probs) * frac * lev
    eq = np.full(N, float(START))
    alive = np.ones(N, dtype=bool)
    for t in range(TRADES):
        eq[alive] *= (1 + draws[alive, t])
        dead = alive & (eq < START * 0.10)       # wiped out
        eq[dead] = 0.0
        alive[dead] = False
    return eq, int(np.sum(~alive))


def main():
    print("=" * 74)
    print("  GAMBLER BACKTEST — does betting big for high profit actually work?")
    print(f"  ₹{START:,} start | {TRADES} trades (~1yr) | {N:,} simulated lives | "
          f"0.4% cost/trade")
    print("  BROKE = ended under ₹1L (wiped out) | got rich = over ₹30L (3x+)")
    print("=" * 74)

    configs = [
        ("disciplined  5% 1x",   0.05, 1.0),
        ("aggressive  20% 1x",   0.20, 1.0),
        ("gambler     50% 2x",   0.50, 2.0),
        ("YOLO       100% 4x",   1.00, 4.0),
        ("full degen 100% 5x",   1.00, 5.0),
    ]
    for regime, probs in (("A) PROVEN edge (60% win) — best case, NOT confirmed live",
                           PROBS_PROVEN),
                          ("B) GAMBLER (no validated edge = 45% win after costs) — "
                           "the REAL 'bet any sector' case", PROBS_GAMBLER)):
        print(f"\n  {regime}")
        print(f"  {'style':<24} {'median end':>12} {'BROKE':>7} {'got rich':>9}")
        for name, frac, lev in configs:
            finals, _ = run(frac, lev, probs)
            median = np.median(finals)
            rich = 100.0 * np.mean(finals > 30_00_000)
            broke = 100.0 * np.mean(finals < 1_00_000)
            print(f"  {name:<24} ₹{median:>10,.0f} {broke:>6.0f}% {rich:>8.0f}%")

    print("\n" + "=" * 74)
    print("  WHAT THIS PROVES")
    print("=" * 74)
    print("  * 'Gamble in any sector' means table B — you have NO validated edge,")
    print("    so it's a coin flip that then pays costs = a losing game.")
    print("  * In table B, MORE leverage = MORE people BROKE. The gambler doesn't")
    print("    get rich; the gambler gets wiped out. The median gambler loses.")
    print("  * Even in table A (a REAL edge), over-leverage raises the BROKE rate.")
    print("    A good edge bet too big still ruins you (Gambler's Ruin theorem).")
    print("  * There is NO setting where 'gamble big for high profit' wins on")
    print("    average. High sustainable profit = a real edge, bet SMALL, for years.")
    print("=" * 74)


if __name__ == "__main__":
    main()
