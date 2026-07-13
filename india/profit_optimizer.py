"""
profit_optimizer.py — the ONE honest lever to lift net profit: selectivity.

The 1-year cost-sensitivity proved the signal is gross-profitable (+25%/mo at 0 cost)
but cost eats it. The honest fix is NOT curve-fitting parameters — it's taking FEWER,
HIGHER-QUALITY trades so you pay the cost drag less often. This sweeps the selectivity
axis (rvol threshold) and the stop width, at the user's chosen 3:1 reward cap, on the
real 1-year data, and WALK-FORWARD validates the winner so we don't fool ourselves.

It reports, per config: net %/mo at real cost, trades/mo, win rate, drawdown, PF — and
flags the config with the best return that ALSO holds up across rolling windows.

Honest limits: searching N configs and picking the best inflates the headline; the
walk-forward column and (ultimately) the live ₹5k slippage test are the real proof. If
nothing is robustly positive, the truth is the edge needs bigger size / lower slippage —
no parameter fixes that.

Usage:
  python3 india/profit_optimizer.py --cache midcap_1y.pkl --cost 0.0018
"""
from __future__ import annotations
import argparse, pickle, sys
from pathlib import Path
import numpy as np

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import orb_fast as of
import strategy_lab as sl

TARGET_MO = 4.0
RR        = 3.0                       # user's professional reward cap (risk 1 : reward 3)
RV_GRID   = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]   # selectivity: higher = fewer, stronger trades
SL_GRID   = [1.5, 2.0, 2.5]


def _run(prepped, rv, slm, cost):
    params = {"rv": rv, "sl": slm, "rr": RR}
    return sl.run_strat(prepped, sl.s_wide_mom, params, cost=cost)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--cost", type=float, default=0.0018)
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
    windows = of.make_windows(prepped)
    wins = [w for k, w in windows.items() if k != "full"]

    lines = []
    def emit(s=""):
        print(s); lines.append(s)

    emit("=" * 84)
    emit(f"  PROFIT OPTIMIZER — selectivity sweep @ RR {RR:.0f}:1 | "
         f"{len(prepped)} symbols, {n_days} days | cost {args.cost*100:.2f}%")
    emit("  Idea: fewer, stronger trades pay less cost. WF = avg of rolling windows.")
    emit("=" * 84)
    emit(f"  {'rvol':>5} {'SL':>4} | {'WR':>6} {'Ret/mo':>8} {'T/mo':>6} {'DD':>6} "
         f"{'PF':>5} | {'WF Ret':>8}  {'robust?':>7}")
    emit("  " + "-" * 78)

    results = []
    for rv in RV_GRID:
        for slm in SL_GRID:
            m = _run(prepped, rv, slm, args.cost)
            wf = [_run(w, rv, slm, args.cost) for w in wins] if wins else []
            wf_ret = np.mean([x["ret"] for x in wf]) if wf else float("nan")
            robust = bool(wf) and all(x["ret"] > 0 for x in wf) and m["ret"] > 0
            results.append((rv, slm, m, wf_ret, robust))
            emit(f"  {rv:>5.1f} {slm:>4.1f} | {m['wr']:>5.1f}% {m['ret']:>+7.1f}% "
                 f"{m['tpm']:>5.1f} {m['dd']:>5.1f}% {m['pf']:>4.2f} | "
                 f"{wf_ret:>+7.1f}%  {'YES' if robust else '—':>7}")

    emit("\n" + "=" * 84)
    emit("  RECOMMENDATION")
    emit("=" * 84)
    robusts = [r for r in results if r[4] and r[2]["trades"] >= 10]
    if robusts:
        best = max(robusts, key=lambda r: r[2]["ret"])
        rv, slm, m, wf_ret, _ = best
        emit(f"  Best ROBUST config: rvol≥{rv}, SL {slm}xATR, RR {RR:.0f}:1")
        emit(f"    WR {m['wr']:.0f}%  Ret {m['ret']:+.1f}%/mo  T/mo {m['tpm']:.1f}  "
             f"DD {m['dd']:.1f}%  PF {m['pf']:.2f}  | walk-forward {wf_ret:+.1f}%/mo")
        if m["ret"] >= TARGET_MO:
            emit(f"    -> clears +{TARGET_MO:.0f}%/mo AND holds across windows. Strong candidate.")
        else:
            emit(f"    -> robustly POSITIVE but below +{TARGET_MO:.0f}%/mo at this size/cost.")
        emit(f"    Set the pilot to this rvol/SL. Confirm with the live ₹5k slippage test.")
    else:
        emit("  No config is BOTH positive and walk-forward robust at this cost.")
        emit("  Selectivity alone does not clear the cost wall on this data — the honest")
        emit("  conclusion stands: profit needs bigger size (₹1L+ positions) and/or lower")
        emit("  slippage (≤0.03%/side), not parameter changes. Measure slippage live first.")
    emit("=" * 84)

    out = _HERE.parent / "profit_optimizer_report.txt"
    out.write_text("\n".join(lines))
    print(f"\n  Report written: {out}")


if __name__ == "__main__":
    main()
