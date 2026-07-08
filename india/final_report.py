"""
final_report.py — ONE command, ONE complete report on the DEPLOYED live config.

  python3 india/final_report.py                 # uses uni.pkl (the 1-yr fetch)
  python3 india/final_report.py --cache uni.pkl --slippage 0.05

Runs the exact config live_pilot trades (imported from it — can't drift) on the
validated 50-name liquid universe and prints everything in one place:
  headline metrics | cost-sensitivity | walk-forward | expected ₹/month by
  account size | verdict + kill-rules.
Report saved to final_report.txt.
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
from live_pilot import (RV_MIN, WIDE_SL, MAX_RR, LP_LOCK_TRIGGER_R, LP_LOCK_AT_R,
                        LP_ARM_BE_R, LP_ARM_TRAIL_R, LP_TRAIL_ATR, MAX_OPEN)

REPORT = _HERE.parent / "final_report.txt"
COSTS = [0.0010, 0.0018, 0.0025, 0.0035, 0.0045]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="uni.pkl")
    ap.add_argument("--max-symbols", type=int, default=50,
                    help="50 = the VALIDATED liquid tier (default)")
    ap.add_argument("--slippage", type=float, default=0.05,
                    help="assumed slippage %%/side for the ₹-by-size table "
                         "(replace with the pilot's MEASURED number)")
    ap.add_argument("--plan", choices=["plus", "basic"], default="plus",
                    help="Upstox plan (brokerage cap ₹30 plus / ₹20 basic)")
    ap.add_argument("--capital", type=float, default=200_000,
                    help="equity for the leverage table (default ₹2L)")
    args = ap.parse_args()
    brk_cap = cm.BROKERAGE_CAP if args.plan == "plus" else cm.BROKERAGE_CAP_BASIC

    p = Path(args.cache)
    if not p.is_absolute():
        p = _HERE / p
    if not p.exists():
        print(f"ERROR: {p} not found — fetch data first:\n"
              f"  python3 india/fetch_midcaps.py --days 365 --out uni.pkl")
        sys.exit(1)
    raw = pickle.load(open(p, "rb"))
    prepped = {}
    for s, df in list(raw.items())[:args.max_symbols]:
        pp = sl._prep(df)
        if pp is not None:
            prepped[s] = pp
    n_days = len({d for pp in prepped.values() for d in pp.index.normalize().unique()})

    params = {"rv": RV_MIN, "sl": WIDE_SL, "rr": MAX_RR,
              "lock_trigger": LP_LOCK_TRIGGER_R, "lock_at": LP_LOCK_AT_R,
              "be": LP_ARM_BE_R, "trail_arm": LP_ARM_TRAIL_R,
              "trail_atr": LP_TRAIL_ATR}
    # LIVE-FIDELITY variant: same signals, modeled the way the LIVE bot actually
    # trades them — ENTRIES near the bar close (not the optimistic trigger fill)
    # with the 0.3% chase-guard skipping runaway bars, and live equal-split /
    # 2-slot / no-leverage sizing. EXITS are bar-extreme (tick-level) because the
    # bot rests both the SL-M stop AND the target limit at the broker.
    params_live = dict(params, live_fill=True)

    lines = []
    def emit(s=""):
        print(s); lines.append(s)

    emit("=" * 78)
    emit("  FINAL REPORT — the exact config the live bot trades")
    emit(f"  rvol>={RV_MIN} | SL {WIDE_SL}xATR | target {MAX_RR:.0f}R | "
         f"lock@+{LP_LOCK_TRIGGER_R}R->+{LP_LOCK_AT_R}R | BE@+{LP_ARM_BE_R}R | "
         f"trail@+{LP_ARM_TRAIL_R}R/{LP_TRAIL_ATR}xATR | squareoff 15:00")
    emit(f"  {len(prepped)} symbols (liquid tier) | {n_days} trading days of data")
    emit("=" * 78)

    # run at each cost level — LIVE-FIDELITY is the number that matters
    res = {c: sl.run_strat(prepped, sl.s_wide_mom, params_live, cost=c,
                           live_sizing=True) for c in COSTS}
    m = res[0.0018]
    m_opt = sl.run_strat(prepped, sl.s_wide_mom, params, cost=0.0018)
    emit("\n  HEADLINE at 0.18% cost — TWO models:")
    emit(f"    OPTIMISTIC SIM (risk-sized, 5 slots, up to 5x leverage — the old "
         f"headline):")
    emit(f"      {m_opt['ret']:+.1f}%/mo | {m_opt['trades']} trades | WR "
         f"{m_opt['wr']:.0f}% | DD {m_opt['dd']:.1f}% | PF {m_opt['pf']:.2f}")
    emit(f"    LIVE-FIDELITY (what the deployed bot can actually capture — "
         f"TRUST THIS ONE):")
    emit(f"      {m['ret']:+.1f}%/mo | {m['trades']} trades ({m['tpm']:.0f}/mo) | "
         f"WR {m['wr']:.0f}% | DD {m['dd']:.1f}% | PF {m['pf']:.2f}")
    emit("    Gap = real entries (close-price fills, chase-guard skips) + live")
    emit("    2-slot no-leverage sizing. Exits are broker-resting (tick-level).")

    emit("\n  1) COST SENSITIVITY — the edge lives or dies on cost")
    emit(f"     {'cost':>7} {'Ret/mo':>9} {'PF':>6}   what this cost means")
    notes = {0.0010: "institutional-grade fills",
             0.0018: "₹1L+ positions, slippage ~0.05%/side",
             0.0025: "the realistic bad case at scale  <- HARD GATE",
             0.0035: "small account (₹25-50k) — informational",
             0.0045: "tiny account (₹5k) — informational"}
    for c in COSTS:
        r = res[c]
        emit(f"     {c*100:>6.2f}% {r['ret']:>+8.1f}% {r['pf']:>5.2f}   {notes[c]}")
    survives = res[0.0025]["ret"] > 0

    emit("\n  2) WALK-FORWARD (live-fidelity) — does it work in EVERY period?")
    wf_rets = []
    for k, w in of.make_windows(prepped).items():
        if k == "full":
            continue
        r = sl.run_strat(w, sl.s_wide_mom, params_live, cost=0.0018, live_sizing=True)
        wf_rets.append(r["ret"])
        emit(f"     {k}: {r['ret']:+.1f}%/mo  (WR {r['wr']:.0f}%, PF {r['pf']:.2f})")
    robust = bool(wf_rets) and all(r > 0 for r in wf_rets)

    # ₹-by-size table: all-in cost per position size -> interpolate return.
    # For BIG sizes the dominant cost is MARKET IMPACT: a market order that is a
    # large fraction of the breakout bar's traded value moves the price against
    # itself. We estimate participation from the cached data's OWN signal bars
    # (rvol>=RV_MIN, entry window) and add impact ~ 0.3% x participation on top
    # of the base slippage. ESTIMATE — clearly labeled, err on the honest side.
    from datetime import time as dtime
    sig_turnover = []          # median ₹ traded per signal bar, per symbol
    for sym, d in prepped.items():
        t = d.index.time
        mask = ((d["rvol"] >= RV_MIN) & (t >= dtime(9, 30)) & (t < dtime(14, 0)))
        to = (d["close"] * d["volume"])[mask]
        if len(to) >= 3:
            sig_turnover.append(float(to.median()))
    sig_turnover.sort()
    med_to = sig_turnover[len(sig_turnover) // 2] if sig_turnover else 0.0
    p25_to = sig_turnover[len(sig_turnover) // 4] if sig_turnover else 0.0

    def _size_row(capital):
        pos = capital / MAX_OPEN
        part_med = pos / med_to if med_to else 0.0        # fraction of signal bar
        part_p25 = pos / p25_to if p25_to else 0.0        # worst-quartile name
        impact = 0.30 * part_med                          # %/side, ~bar-range model
        slip = args.slippage + impact
        cost_pct = cm.round_trip_cost(pos, slip,
                                      brokerage_cap=brk_cap)["total_pct"] / 100.0
        ret = float(np.interp(cost_pct, COSTS, ret_curve))
        return pos, part_med, part_p25, slip, cost_pct, ret

    emit(f"\n  3) WHAT ₹ TO EXPECT PER MONTH — capacity-aware "
         f"(base slippage {args.slippage:.2f}%/side + market-impact estimate; "
         f"Upstox {args.plan.upper()} plan brokerage cap ₹{brk_cap:.0f}/order)")
    emit(f"     median signal-bar turnover of the universe: ₹{med_to:,.0f} / 5-min bar")
    emit(f"     {'account':>11} {'pos size':>10} {'bar share':>10} {'slip est':>9} "
         f"{'all-in':>8} {'Ret/mo':>8} {'₹/month':>11}")
    ret_curve = [res[c]["ret"] for c in COSTS]
    for capital in (100_000, 200_000, 500_000, 1_000_000):
        pos, part, part25, slip, cost_pct, ret = _size_row(capital)
        warn = ("  <- IMPACT DEGRADES EDGE" if part > 0.10
                else "  <- caution on thinner names" if part25 > 0.10 else "")
        emit(f"     ₹{capital:>9,} ₹{pos:>9,.0f} {part*100:>9.1f}% {slip:>8.2f}% "
             f"{cost_pct*100:>7.2f}% {ret:>+7.1f}% ₹{capital*ret/100:>+10,.0f}{warn}")
    emit("     bar share = your order as % of the median breakout bar's traded value;")
    emit("     above ~10% your own market order moves the price against you.")

    # 4) LEVERAGE VIEW — intraday MIS margin (NOT MTF: that's a delivery product
    # with ~16-20%/yr interest that breaks the flat-by-close rule). Return AND
    # drawdown both scale ~1:1 with leverage; costs apply to the leveraged
    # notional, so the cost benefit of bigger positions is included.
    base_dd = m["dd"]
    emit(f"\n  4) LEVERAGE (intraday MIS margin) — on ₹{args.capital:,.0f} equity")
    emit(f"     {'lev':>5} {'buying pwr':>11} {'pos size':>10} {'all-in':>8} "
         f"{'Ret/mo':>8} {'₹/month':>10} {'est DD':>7}  verdict")
    for L in (1.0, 1.5, 2.0, 3.0):
        bp = args.capital * L
        pos = bp / MAX_OPEN
        part = pos / med_to if med_to else 0.0
        slip = args.slippage + 0.30 * part
        cost_pct = cm.round_trip_cost(pos, slip,
                                      brokerage_cap=brk_cap)["total_pct"] / 100.0
        ret_eq = float(np.interp(cost_pct, COSTS, ret_curve)) * L
        dd_eq = base_dd * L
        verdict = ("OK" if dd_eq < 20 else
                   "BREACHES 20% KILL-RULE — normal DD would stop you out"
                   if dd_eq < 30 else "ACCOUNT-THREATENING — never")
        emit(f"     {L:>4.1f}x ₹{bp:>10,.0f} ₹{pos:>9,.0f} {cost_pct*100:>7.2f}% "
             f"{ret_eq:>+7.1f}% ₹{args.capital*ret_eq/100:>+9,.0f} {dd_eq:>6.1f}%  {verdict}")
    emit("     Leverage multiplies the DRAWDOWN exactly as much as the return —")
    emit("     above ~1.4x the backtest's own normal 14% DD trips the 20% kill-rule.")

    emit("\n" + "=" * 78)
    emit("  VERDICT")
    emit("=" * 78)
    ok = m["ret"] > 0 and survives and robust and m["trades"] >= 20
    if ok:
        emit("  PASSES — positive at scaled cost, survives 0.25%, positive in every")
        emit("  walk-forward window. DEPLOYABLE — but ONLY at a size where your")
        emit("  all-in cost stays <= ~0.22% (see table 3). At tiny size it loses to fees.")
    else:
        why = []
        if not survives: why.append("fails the 0.25% cost gate")
        if not robust:   why.append("negative walk-forward window(s)")
        if m["ret"] <= 0: why.append("not profitable at scaled cost")
        emit("  DOES NOT PASS — " + "; ".join(why) + ". Do not scale this.")
    emit("\n  KILL-RULES (decided in advance, not in panic):")
    emit("   * measured slippage > ~0.05%/side after ~2 weeks  -> stop")
    emit("   * two consecutive losing MONTHS, or drawdown > 20% -> halt + revalidate")
    emit("   * WR 39% means 5-6 losses in a row is NORMAL — judge months, not days")
    emit("=" * 78)

    REPORT.write_text("\n".join(lines))
    print(f"\n  Report saved: {REPORT}")


if __name__ == "__main__":
    main()
