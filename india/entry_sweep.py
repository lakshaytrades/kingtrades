"""
entry_sweep.py — find the entry style that survives HONEST fills (or prove none does).

The truthful simulator showed the deployed config negative: its 0.3% chase-cap
refuses the strong runaway breakouts where the optimistic backtest's profit
lived. The honest question isn't "how do we make the number positive" — it's
"which REAL entry style, priced at REAL fills, performs best?" This sweeps:

  cap=0.3%   current live behavior (retests only)
  cap=0.6%   pay up a little for stronger moves
  cap=1.0%   pay up more
  cap=None   BUY THE RUNAWAY at the next bar's open, whatever it costs

All under the live-fidelity model (next-bar fills, tick-level broker-resting
exits, 2-slot no-leverage sizing) at 0.18% cost, with strict walk-forward on
the best. If nothing passes, the strategy does not survive honest entries —
the fix is to stop, not to massage the model.

  python3 india/entry_sweep.py                # uses uni.pkl, 50 liquid names
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
from live_pilot import (RV_MIN, WIDE_SL, MAX_RR, LP_LOCK_TRIGGER_R, LP_LOCK_AT_R,
                        LP_ARM_BE_R, LP_ARM_TRAIL_R, LP_TRAIL_ATR)

CAPS = [0.003, 0.006, 0.010, None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="uni.pkl")
    ap.add_argument("--max-symbols", type=int, default=50)
    ap.add_argument("--cost", type=float, default=0.0018)
    args = ap.parse_args()

    p = Path(args.cache)
    if not p.is_absolute():
        p = _HERE / p
    raw = pickle.load(open(p, "rb"))
    prepped = {}
    for s, df in list(raw.items())[:args.max_symbols]:
        pp = sl._prep(df)
        if pp is not None:
            prepped[s] = pp

    base = {"rv": RV_MIN, "sl": WIDE_SL, "rr": MAX_RR,
            "lock_trigger": LP_LOCK_TRIGGER_R, "lock_at": LP_LOCK_AT_R,
            "be": LP_ARM_BE_R, "trail_arm": LP_ARM_TRAIL_R,
            "trail_atr": LP_TRAIL_ATR, "live_fill": True}

    print("=" * 78)
    print("  ENTRY SWEEP — honest fills, live sizing, tick-level resting exits")
    print(f"  {len(prepped)} symbols | cost {args.cost*100:.2f}% | "
          f"config rv>={RV_MIN} SL{WIDE_SL} {MAX_RR:.0f}R + locks")
    print("=" * 78)
    print(f"  {'entry style':<26} {'trades':>7} {'WR':>5} {'Ret/mo':>8} "
          f"{'DD':>6} {'PF':>5}")
    results = []
    for cap in CAPS:
        params = dict(base, entry_cap=cap)
        m = sl.run_strat(prepped, sl.s_wide_mom, params, cost=args.cost,
                         live_sizing=True)
        label = (f"cap trigger+{cap*100:.1f}%" if cap is not None
                 else "MARKET at next open")
        flag = "  <-- POSITIVE" if (m["ret"] > 0 and m["pf"] > 1.05
                                    and m["trades"] >= 20) else ""
        print(f"  {label:<26} {m['trades']:>7} {m['wr']:>4.0f}% {m['ret']:>+7.2f}% "
              f"{m['dd']:>5.1f}% {m['pf']:>4.2f}{flag}")
        results.append((cap, m))

    best = max(results, key=lambda x: x[1]["ret"])
    cap, m = best
    label = (f"cap trigger+{cap*100:.1f}%" if cap is not None
             else "MARKET at next open")
    print("\n  BEST: " + label)
    if m["ret"] <= 0:
        print("  ...and it is STILL NEGATIVE. Under honest fills no entry style of")
        print("  this signal clears costs — the edge was an artifact of optimistic")
        print("  fill modeling. Recommendation: STOP scaling plans; rethink the")
        print("  signal (different setup/timeframe), don't massage this one.")
        return

    print("\n  STRICT WALK-FORWARD on the best (must be positive in EVERY window):")
    ok = True
    for k, w in of.make_windows(prepped).items():
        if k == "full":
            continue
        r = sl.run_strat(w, sl.s_wide_mom, dict(base, entry_cap=cap),
                         cost=args.cost, live_sizing=True)
        ok &= r["ret"] > 0
        print(f"    {k}: {r['ret']:+.2f}%/mo (WR {r['wr']:.0f}%, PF {r['pf']:.2f})")
    print("\n  COST SENSITIVITY on the best:")
    for c in (0.0010, 0.0018, 0.0025):
        r = sl.run_strat(prepped, sl.s_wide_mom, dict(base, entry_cap=cap), cost=c,
                         live_sizing=True)
        print(f"    {c*100:.2f}%: {r['ret']:+.2f}%/mo (PF {r['pf']:.2f})")
    print("\n  VERDICT: " + ("PASSES walk-forward — this entry style is deployable; "
                             "tell the bot via INDIA_CHASE_CAP." if ok else
                             "fails walk-forward — regime-dependent; do NOT deploy."))


if __name__ == "__main__":
    main()
