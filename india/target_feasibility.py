"""
target_feasibility.py — can intraday hit the +4%/mo target, and under what conditions?

The 1-year test showed the RAW signal (RANGE_BREAK_HOLD) has a real edge — +25%/mo
at ZERO cost, PF 1.64 over 224 days — but it's eaten by cost: +8%/mo at 0.10%,
flat at 0.20%. So the target is NOT a tuning problem; it's an execution problem:
hit it only if real all-in round-trip cost lands below ~0.14%.

This ties the two halves together: it builds the strategy's return-vs-cost curve
on YOUR 1-year data, then for a grid of (position size x slippage) computes the
exact Groww all-in cost and the resulting monthly return — and marks which
combinations clear +4%/mo. It answers, honestly and precisely:
  "At what position size and execution quality does intraday hit my target?"

Usage:
  python3 india/target_feasibility.py --cache /root/kingtrades/midcap_1y.pkl
"""

from __future__ import annotations
import argparse
import pickle
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import orb_fast as of
import strategy_lab as sl
import cost_model as cm

TARGET_MO = 4.0          # %/month target
SIZES     = [25_000, 50_000, 100_000, 200_000, 300_000, 500_000]
SLIPS     = [0.02, 0.03, 0.05, 0.08]        # % per side
# strategies to evaluate (name, generator, params)
STRATS = [
    ("RANGE_BREAK_HOLD", sl.s_range_hold, {"rv": 2.0, "sl": 2.0}),
    ("WIDE_MOMENTUM",    sl.s_wide_mom,   {"rv": 2.0, "sl": 1.5, "rr": 6.0}),
]
COST_GRID = [0.0, 0.0005, 0.0010, 0.0014, 0.0018, 0.0022, 0.0030, 0.0045]


def _ret_at_cost(prepped, gen, params, cost):
    return sl.run_strat(prepped, gen, params, cost=cost)["ret"]


def _interp_curve(costs, rets):
    """Linear interpolation of return as a function of cost fraction."""
    xs = np.array(costs); ys = np.array(rets)
    def f(c):
        return float(np.interp(c, xs, ys))
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--max-symbols", type=int, default=60)
    args = ap.parse_args()

    cache = Path(args.cache)
    if not cache.is_absolute():
        cache = _HERE / cache
    if not cache.exists():
        print(f"ERROR: {cache} not found."); sys.exit(1)
    with open(cache, "rb") as f:
        raw = pickle.load(f)
    prepped = {}
    for s, df in list(raw.items())[:args.max_symbols]:
        p = sl._prep(df)
        if p is not None:
            prepped[s] = p
    n_days = len({d for p in prepped.values() for d in p.index.normalize().unique()})

    lines = []
    def emit(s=""):
        print(s); lines.append(s)

    emit("=" * 78)
    emit(f"  TARGET FEASIBILITY — can intraday hit +{TARGET_MO:.0f}%/mo, and how?")
    emit(f"  data: {len(prepped)} symbols, {n_days} trading days")
    emit("=" * 78)

    for name, gen, params in STRATS:
        # return-vs-cost curve on this data
        costs, rets = [], []
        for c in COST_GRID:
            costs.append(c); rets.append(_ret_at_cost(prepped, gen, params, c))
        f = _interp_curve(costs, rets)

        # cost needed to hit target (solve f(c) = TARGET by scanning)
        cc = np.linspace(0, 0.0045, 451)
        hit = [c for c in cc if f(c) >= TARGET_MO]
        max_cost = max(hit) if hit else None

        emit(f"\n  ── {name} {params} ──")
        emit(f"  return vs cost (1-year):")
        emit("     " + "  ".join(f"{c*100:.2f}%→{r:+.1f}" for c, r in zip(COST_GRID, rets)))
        if max_cost is None:
            emit(f"  Target +{TARGET_MO:.0f}%/mo is UNREACHABLE at any cost ≥0 — raw signal too weak.")
        else:
            emit(f"  To hit +{TARGET_MO:.0f}%/mo you need all-in cost ≤ {max_cost*100:.3f}%")

        # feasibility matrix: size x slippage -> all-in cost -> net return -> hit?
        emit(f"\n  feasibility (cell = net %/mo ; '*' = hits +{TARGET_MO:.0f}%):")
        hdr = "    size\\slip " + "".join(f"{s:>9.2f}%" for s in SLIPS)
        emit(hdr)
        for size in SIZES:
            row = f"   ₹{size:>8,}"
            for slp in SLIPS:
                cost_pct = cm.round_trip_cost(size, slp)["total_pct"] / 100.0
                r = f(cost_pct)
                mark = "*" if r >= TARGET_MO else " "
                row += f"  {r:>+6.1f}{mark}"
            emit(row)
        emit(f"    (slip = % per side; all-in cost from Groww fee schedule + slip)")

    emit("\n" + "=" * 78)
    emit("  HOW TO READ THIS")
    emit("=" * 78)
    emit("  * cells = configurations that hit your target. If there are NONE, the")
    emit("    target is not reachable intraday on this universe — stop.")
    emit("  * If '*' appears only at large size + tiny slippage, the target is")
    emit("    reachable ONLY with excellent execution at real capital — and the")
    emit("    slippage number must be PROVEN live (₹5k pilot) before scaling.")
    emit("  * Slippage on breakout entries is the make-or-break unknown. Limit")
    emit("    orders on the most liquid names keep it low; market orders blow it up.")
    emit("=" * 78)

    out = _HERE.parent / "target_feasibility_report.txt"
    out.write_text("\n".join(lines))
    print(f"\n  Report written: {out}")


if __name__ == "__main__":
    main()
