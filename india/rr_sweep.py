"""
rr_sweep.py — find the SMALLEST, most achievable reward:risk that still profits.

You wanted a smaller RR (the 6:1 target is rarely reached). This sweeps small,
realistic targets (1:1 .. 3:1) on the momentum entry (fresh ORB break, rvol>=2),
with and without a breakeven stop, on YOUR 1-year data — and shows for each:
  WR, return/mo, drawdown, profit factor, AND the exit mix
  (what % actually HIT THE TARGET vs stopped out vs exited at 3:25 square-off).
A small RR is "achievable" when a high % of trades hit the target. The goal: the
LOWEST RR that is still green at your real cost.

Usage:
  python3 india/rr_sweep.py --cache midcap_1y.pkl --cost 0.0018
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
RR_GRID   = [1.0, 1.5, 2.0, 2.5, 3.0]
SL_GRID   = [1.5, 2.0]
BE_GRID   = [None, 1.0]          # None = no breakeven; 1.0 = stop->entry after +1R


def _resolve_all(prepped, params, cost):
    """Return (metrics, exit_mix%) for the momentum entry with these params."""
    trades = []
    saved = of.COST_RT_PCT
    of.COST_RT_PCT = cost
    try:
        for sym, d in prepped.items():
            for cand in sl.s_wide_mom(d, params):   # fresh ORB break + rvol>=2
                cand["sym"] = sym
                cand["be"] = params.get("be")
                trades.append(of._resolve_exit(d, cand))
        metrics = of._simulate(trades)
    finally:
        of.COST_RT_PCT = saved
    # exit mix across all resolved trades
    n = len(trades) or 1
    mix = {}
    for t in trades:
        w = t.get("why", "?")
        w = "TARGET" if w == "TP" else ("STOP" if w in ("SL", "BE") else "3:25/EOD")
        mix[w] = mix.get(w, 0) + 1
    mix = {k: v / n * 100 for k, v in mix.items()}
    return metrics, mix


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

    lines = []
    def emit(s=""):
        print(s); lines.append(s)

    emit("=" * 86)
    emit(f"  SMALL-RR SWEEP — momentum entry | {len(prepped)} symbols, {n_days} days | "
         f"cost {args.cost*100:.2f}%")
    emit(f"  Goal: smallest RR that still hits +{TARGET_MO:.0f}%/mo. "
         f"'TARGET%' = how often the target is actually reached (achievability).")
    emit("=" * 86)
    emit(f"  {'RR':>4} {'SL':>4} {'BE':>4} | {'WR':>6} {'Ret/mo':>8} {'DD':>6} {'PF':>5} "
         f"| {'TARGET%':>8} {'STOP%':>6} {'3:25%':>6}  hit+4%?")
    emit("  " + "-" * 82)

    rows = []
    for rr in RR_GRID:
        for slm in SL_GRID:
            for be in BE_GRID:
                params = {"rv": 2.0, "sl": slm, "rr": rr, "be": be}
                m, mix = _resolve_all(prepped, params, args.cost)
                hit = "  YES" if (m["ret"] >= TARGET_MO and m["trades"] >= 10) else ""
                rows.append((rr, slm, be, m, mix, hit))
                emit(f"  {rr:>4.1f} {slm:>4.1f} {str(be or '-'):>4} | "
                     f"{m['wr']:>5.1f}% {m['ret']:>+7.1f}% {m['dd']:>5.1f}% {m['pf']:>4.2f} | "
                     f"{mix.get('TARGET',0):>7.0f}% {mix.get('STOP',0):>5.0f}% "
                     f"{mix.get('3:25/EOD',0):>5.0f}%{hit}")

    # pick the SMALLEST RR that is profitable (ret>0, pf>1.05, >=10 trades)
    profitable = [r for r in rows if r[3]["ret"] > 0 and r[3]["pf"] >= 1.05 and r[3]["trades"] >= 10]
    emit("\n" + "=" * 86)
    emit("  RECOMMENDATION")
    emit("=" * 86)
    if not profitable:
        emit("  No small RR is profitable at this cost on this data. Smaller targets do NOT")
        emit("  help here — momentum needs the runners. Keep the validated config and rely on")
        emit("  size + daily-loss limit + stop for safety, not a smaller target.")
    else:
        # smallest RR; tie-break by best return
        best = sorted(profitable, key=lambda r: (r[0], -r[3]["ret"]))[0]
        rr, slm, be, m, mix, _ = best
        emit(f"  Smallest achievable profitable RR: {rr:.1f}:1  (SL {slm}xATR, "
             f"breakeven {'on' if be else 'off'})")
        emit(f"    WR {m['wr']:.0f}%  Ret {m['ret']:+.1f}%/mo  DD {m['dd']:.1f}%  PF {m['pf']:.2f}")
        emit(f"    target hit {mix.get('TARGET',0):.0f}% of trades  "
             f"(higher = more 'achievable')")
        if m["ret"] >= TARGET_MO:
            emit(f"    -> clears your +{TARGET_MO:.0f}%/mo goal. Good candidate for the pilot.")
        else:
            emit(f"    -> profitable but below +{TARGET_MO:.0f}%/mo. Lower RR = safer/steadier,")
            emit(f"       but smaller monthly return. Your call on the trade-off.")

    out = _HERE.parent / "rr_sweep_report.txt"
    out.write_text("\n".join(lines))
    print(f"\n  Report written: {out}")


if __name__ == "__main__":
    main()
