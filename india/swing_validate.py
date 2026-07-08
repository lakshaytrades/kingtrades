"""
swing_validate.py — stress-test the ONE swing strategy that passed new_edge_lab.

new_edge_lab found rev:buy-5%-day (+1.49%/mo, 60% WR, 4.9% DD, PF 1.53) and it
passed strict walk-forward. Before risking a rupee this asks the two questions
that kill thin edges: does it survive HIGHER cost (small accounts pay more), and
what real ₹/month does each account size produce after honest delivery fees?

  python3 india/swing_validate.py            # uses uni.pkl
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import cost_model as cm
import new_edge_lab as nl

# the validated config's exit rule is baked into nl.gen_daily_swing ("-5%" line)
COSTS = [0.0030, 0.0040, 0.0055, 0.0070, 0.0090]


def _run(daily, cost):
    fam = nl.gen_daily_swing(daily, cost)
    return nl.sim(fam["rev: buy -5% day, +2%/wk"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="uni.pkl")
    ap.add_argument("--max-symbols", type=int, default=127)
    args = ap.parse_args()
    p = Path(args.cache)
    if not p.is_absolute():
        p = _HERE / p
    raw = pickle.load(open(p, "rb"))
    daily = {}
    for s, df in list(raw.items())[:args.max_symbols]:
        d = nl.to_daily(df)
        if len(d) > 60:
            daily[s] = d

    print("=" * 74)
    print("  SWING VALIDATE — rev:buy-5%-day (the new_edge_lab passer)")
    print(f"  {len(daily)} symbols | buy -5% close, exit +2% / -5% / Friday")
    print("=" * 74)

    print("\n  1) COST SENSITIVITY — does the edge survive higher real cost?")
    print(f"     {'cost RT':>8} {'trades':>7} {'WR':>5} {'Ret/mo':>8} {'PF':>5}")
    survives = True
    for c in COSTS:
        m = _run(daily, c)
        flag = "" if m["ret"] > 0 else "  <- LOSES"
        if c >= 0.0055 and m["ret"] <= 0:
            survives = False
        print(f"     {c*100:>7.2f}% {m['trades']:>7} {m['wr']:>4.0f}% "
              f"{m['ret']:>+7.2f}% {m['pf']:>4.2f}{flag}")

    print("\n  2) WHAT ₹/MONTH BY ACCOUNT SIZE (honest delivery fees per position)")
    print(f"     {'account':>10} {'pos size':>10} {'cost RT':>8} {'Ret/mo':>8} "
          f"{'₹/month':>10}")
    base = _run(daily, 0.0040)
    ret_at = {c: _run(daily, c)["ret"] for c in COSTS}
    for cap in (25_000, 50_000, 100_000, 200_000):
        pos = cap / nl_maxpos()
        cpct = cm.round_trip_cost(pos, 0.05)["total_pct"] / 100.0
        # delivery STT is 0.2% RT vs intraday 0.025% -> add the 0.175% delta
        cpct += 0.00175
        ret = float(np.interp(cpct, COSTS, [ret_at[c] for c in COSTS]))
        print(f"     ₹{cap:>8,} ₹{pos:>9,.0f} {cpct*100:>7.2f}% {ret:>+7.2f}% "
              f"₹{cap*ret/100:>+9,.0f}")

    print("\n" + "=" * 74)
    if survives:
        print("  VERDICT: survives realistic higher cost. This is a genuine, modest")
        print("  swing edge (~15-18%/yr, low DD). NEXT: paper-pilot it 2-4 weeks via")
        print("  swing_pilot.py (INDIA_SWING_STRAT=mean_rev), then tiny live. Only set")
        print("  INDIA_SWING_VALIDATED=true after the paper run matches this.")
    else:
        print("  VERDICT: dies at small-account cost. Real only at ₹1L+ positions —")
        print("  paper-pilot to confirm before funding, and don't run it tiny.")
    print("=" * 74)


def nl_maxpos():
    return 2      # matches the validated sim (2 slots) and swing_pilot.MAX_POS


if __name__ == "__main__":
    main()
