"""
swing_live_backtest.py — ONE backtest on LIVE market data, everything in it.

Reads uni.pkl (the cached 1-year real NSE data already on the VPS) and runs the
DEPLOYED config (rev:buy-5%-day) through the honest engine, then reports the
REAL backtested edge + WR + PF + drawdown, walk-forward robustness, cost
sensitivity, and the ₹/month deployment grid at ₹1/2/5/10L x (1x, 2x MTF) —
all computed from the actual trades, not hardcoded.

  python3 india/swing_live_backtest.py
  python3 india/swing_live_backtest.py --cache uni.pkl --rate 0.16
"""
from __future__ import annotations
import argparse, pickle, sys
from pathlib import Path
import numpy as np

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import new_edge_lab as nl

FAMILY = "rev: buy -5% day, +2%/wk"        # the deployed, validated config
HOLD_DAYS = 2.5


def _metrics(daily, cost):
    fam = nl.gen_daily_swing(daily, cost)
    trades = fam[FAMILY]
    m = nl.sim(trades)
    return m, trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="uni.pkl")
    ap.add_argument("--max-symbols", type=int, default=127)
    ap.add_argument("--rate", type=float, default=0.16, help="MTF interest %/yr")
    args = ap.parse_args()

    p = Path(args.cache)
    if not p.is_absolute():
        p = _HERE / p
    if not p.exists():
        print(f"ERROR: {p} not found. It holds the cached LIVE data. Rebuild with a\n"
              f"  working token: python3 india/fetch_midcaps.py --days 365 --out uni.pkl")
        sys.exit(1)
    raw = pickle.load(open(p, "rb"))
    daily = {}
    for s, df in list(raw.items())[:args.max_symbols]:
        d = nl.to_daily(df)
        if len(d) > 60:
            daily[s] = d
    n_days = len({d for dd in daily.values() for d in dd.index.normalize().unique()})

    print("=" * 78)
    print("  LIVE-DATA BACKTEST — deployed config (rev: buy -5% day)")
    print(f"  {len(daily)} real NSE symbols | {n_days} trading days | cached uni.pkl")
    print("=" * 78)

    # 1) the real edge at honest delivery cost
    m, trades = _metrics(daily, 0.0040)
    print("\n  1) THE EDGE (backtested on live data, 0.40% delivery cost)")
    print(f"     trades {m['trades']}  |  WIN RATE {m['wr']:.0f}%  |  "
          f"Ret {m['ret']:+.2f}%/mo  |  DD {m['dd']:.1f}%  |  PF {m['pf']:.2f}")
    base_ret = m["ret"] / 100.0
    base_dd = m["dd"] / 100.0

    # 2) walk-forward (each third of the year must be positive)
    print("\n  2) WALK-FORWARD — positive in every third of the year?")
    parts = []
    for lbl, seg in zip(("1st", "2nd", "3rd"), nl.thirds(trades)):
        r = nl.sim(seg)["ret"] if seg else -99
        parts.append(r)
        print(f"     {lbl} third: {r:+.2f}%/mo")
    robust = all(x > 0 for x in parts)
    print(f"     -> {'ROBUST (all positive)' if robust else 'NOT robust'}")

    # 3) cost sensitivity
    print("\n  3) COST SENSITIVITY — survives higher real cost?")
    print(f"     {'cost':>7} {'WR':>5} {'Ret/mo':>8} {'PF':>5}")
    for c in (0.0030, 0.0040, 0.0055, 0.0070):
        mc, _ = _metrics(daily, c)
        print(f"     {c*100:>6.2f}% {mc['wr']:>4.0f}% {mc['ret']:>+7.2f}% {mc['pf']:>4.2f}")

    # 4) deployment grid from the ACTUAL backtested edge
    live = 0.70                                    # backtests overstate; plan 70%
    deployed_frac = min(5 * HOLD_DAYS / 30.0, 1.0)
    def net(L):
        gross = L * base_ret * live
        interest = (L - 1) * args.rate / 12.0 * deployed_frac
        return gross - interest
    print(f"\n  4) DEPLOYMENT GRID — ₹1/2/5/10L x (1x, 2x MTF), from THIS backtest")
    print(f"     (realistic live = 70% of the {base_ret*100:.2f}%/mo above; "
          f"interest {args.rate*100:.0f}%; WR {m['wr']:.0f}% at every size)")
    print(f"     {'capital':>9} | {'1x ₹/mo':>9} {'1x DD':>10} | {'2x ₹/mo':>9} {'2x DD':>11}")
    n1, n2 = net(1.0), net(2.0)
    for c in (100000, 200000, 500000, 1000000):
        dd1 = c * 1 * base_dd * 2
        dd2 = c * 2 * base_dd * 2
        print(f"     ₹{c:>7,} | ₹{c*n1:>7,.0f} ₹{-dd1:>8,.0f} | "
              f"₹{c*n2:>7,.0f} ₹{-dd2:>9,.0f}")

    print("\n" + "=" * 78)
    ok = base_ret > 0 and robust and m["trades"] >= 30
    if ok:
        print("  VERDICT: real edge on live data — WR ~{:.0f}%, +{:.2f}%/mo, robust."
              .format(m["wr"], m["ret"]))
        print("  Deploy 1x first; 2x only after live months confirm it. Drawdowns in")
        print("  the grid (rupees) are what you must sit through — they are NORMAL.")
    else:
        print("  VERDICT: does not clear the bar on this data — do not deploy.")
    print("=" * 78)


if __name__ == "__main__":
    main()
