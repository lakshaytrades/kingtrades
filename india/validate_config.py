"""
validate_config.py — stress-test ONE config so we don't deploy an overfit mirage.

The rr_sweep is IN-SAMPLE (best-of-N on a fixed window) — it flatters itself. Before
trusting any config (especially a seductive high-WR / low-RR one), this checks the two
things that expose overfitting and fragility:

  1. COST SENSITIVITY — return at 0.10 / 0.18 / 0.25 / 0.35 / 0.45% cost. Low-RR / high-WR
     configs die fastest as cost rises; if it's only green at 0.18% it won't survive live.
  2. WALK-FORWARD — return in each rolling time window. A real edge is positive in ALL
     of them; an overfit one is positive overall but negative in some windows.

A config is TRUSTWORTHY only if it stays positive across BOTH. This is NOT a way to make
a backtest look good — it's a way to catch the ones that only LOOK good.

Usage:
  python3 india/validate_config.py --cache uni.pkl --rv 3.0 --sl 2.0 --rr 1.5
  python3 india/validate_config.py --cache uni.pkl --rv 2.0 --sl 2.0 --rr 1.0 --be 1.0
"""
from __future__ import annotations
import argparse, pickle, sys
from pathlib import Path
import numpy as np

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import orb_fast as of
import strategy_lab as sl


def _run(prepped, params, cost):
    return sl.run_strat(prepped, sl.s_wide_mom, params, cost=cost)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--rv", type=float, default=2.0)
    ap.add_argument("--sl", type=float, default=2.0)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--be", type=float, default=None)
    ap.add_argument("--trail-arm", type=float, default=None,
                    help="R-multiple at which the trailing stop arms (live default 2.0)")
    ap.add_argument("--trail-atr", type=float, default=None,
                    help="trail width in ATR once armed (live default 1.5)")
    ap.add_argument("--lock-trigger", type=float, default=None,
                    help="early lock: arm at this many R in profit (live default 0.75)")
    ap.add_argument("--lock-at", type=float, default=None,
                    help="early lock: move stop to entry + this many R (live default 0.15)")
    ap.add_argument("--cost", type=float, default=0.0018, help="your expected real cost")
    ap.add_argument("--max-symbols", type=int, default=150)
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
    n_days = len({d for pp in prepped.values() for d in pp.index.normalize().unique()})
    params = {"rv": args.rv, "sl": args.sl, "rr": args.rr, "be": args.be,
              "trail_arm": args.trail_arm, "trail_atr": args.trail_atr,
              "lock_trigger": args.lock_trigger, "lock_at": args.lock_at}

    lock = ""
    if args.lock_trigger: lock += f", lock@{args.lock_trigger}R->{args.lock_at}R"
    if args.be:        lock += f", breakeven={args.be}R"
    if args.trail_arm: lock += f", trail@{args.trail_arm}R/{args.trail_atr}xATR"
    print("=" * 78)
    print(f"  VALIDATE CONFIG: rv={args.rv}, SL={args.sl}xATR, RR={args.rr}{lock}")
    print(f"  {len(prepped)} symbols, {n_days} trading days")
    print("=" * 78)

    base = _run(prepped, params, args.cost)
    print(f"\n  AT YOUR COST ({args.cost*100:.2f}%):")
    print(f"    trades {base['trades']}  WR {base['wr']:.0f}%  Ret {base['ret']:+.1f}%/mo  "
          f"DD {base['dd']:.1f}%  PF {base['pf']:.2f}")

    # 1) cost sensitivity. HARD GATE = positive at 0.25% (a realistic bad day at
    # ₹1L+ scale). 0.35/0.45% are TINY-ACCOUNT costs (₹5-25k positions) shown for
    # information — no intraday edge survives 0.45% round-trip, and demanding it
    # would just reject every real strategy.
    print("\n  1) COST SENSITIVITY — does it survive a higher real cost?")
    print(f"     {'cost':>7} {'Ret/mo':>9} {'PF':>6}")
    survives_cost = True
    for c in [0.0010, 0.0018, 0.0025, 0.0035, 0.0045]:
        m = _run(prepped, params, c)
        flag = "" if m["ret"] > 0 else "  <- LOSES"
        if c == 0.0025 and m["ret"] <= 0:
            survives_cost = False
        if c > 0.0025 and m["ret"] <= 0:
            flag += "  (tiny-account cost — informational)"
        print(f"     {c*100:>6.2f}% {m['ret']:>+8.1f}% {m['pf']:>5.2f}{flag}")

    # 2) walk-forward
    print("\n  2) WALK-FORWARD — is it positive in EVERY time window?")
    windows = of.make_windows(prepped)
    wf = []
    for k, w in windows.items():
        if k == "full":
            continue
        m = _run(w, params, args.cost)
        wf.append(m["ret"])
        print(f"     {k:>5}: {m['ret']:+.1f}%/mo  (WR {m['wr']:.0f}%, PF {m['pf']:.2f})")
    wf_robust = bool(wf) and all(r > 0 for r in wf)

    # verdict
    print("\n" + "=" * 78)
    print("  VERDICT")
    print("=" * 78)
    strong_pf = base["pf"] >= 1.10
    ok = base["ret"] > 0 and survives_cost and wf_robust and base["trades"] >= 20
    if ok and strong_pf:
        print("  TRUSTWORTHY — positive at your cost, survives 0.25% cost, robust across")
        print("  every window, and PF >= 1.10. This is a real candidate for the pilot.")
        print("  REQUIREMENT: keep real all-in cost <= ~0.22% => ₹1L+ positions on")
        print("  liquid names with tight fills. At small size it still loses to fees.")
    elif ok:
        print("  MARGINAL — robust and positive, but PF is thin (< 1.10). Real but fragile;")
        print("  deploy only tiny, and expect the live slippage test to be decisive.")
    else:
        reasons = []
        if not survives_cost: reasons.append("goes negative at realistic higher cost")
        if not wf_robust:     reasons.append("negative in at least one walk-forward window")
        if base["ret"] <= 0:  reasons.append("not profitable even at your stated cost")
        if base["trades"] < 20: reasons.append("too few trades to trust")
        print("  DO NOT DEPLOY — " + "; ".join(reasons) + ".")
        print("  This config looked good in-sample but fails the stress test — a classic")
        print("  overfit. Trying to 'fix' it with more filters makes it worse, not better.")
    print("=" * 78)

    out = _HERE.parent / "validate_config_report.txt"
    out.write_text(f"rv={args.rv} sl={args.sl} rr={args.rr} be={args.be}\n"
                   f"base: {base}\nwf: {wf}\nsurvives_cost={survives_cost} wf_robust={wf_robust}")
    print(f"\n  Report: {out}")


if __name__ == "__main__":
    main()
