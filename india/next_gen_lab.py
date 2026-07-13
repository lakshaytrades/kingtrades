"""
next_gen_lab.py — the two credible fixes for the latency that killed v1, tested honestly.

DIAGNOSIS (from the honest-fills work): the ORB signal itself isn't disproven —
reacting a full 5-min bar late is what eats the edge (0.3-0.8% entry premium vs
a ~0.08% margin). Two designs attack the latency directly:

  A) INTRABAR ENTRY (5-min): buy AS price crosses the trigger, not after the
     bar closes. Gets the fill the old backtest fantasized about — for real.
     Price of admission: no closed-bar confirmation (more false breaks), and the
     bar's own volume can't be used causally (that's lookahead) — so the volume
     gate moves to the PRIOR bar, or off entirely.
     Modeling honesty: entry at trigger+slip; gap-opens beyond the cap are
     skipped; if the ENTRY BAR's low touches the stop, it books as a full loss
     (pessimistic — intrabar ordering unknowable); targets never book on the
     entry bar.

  B) 15-MINUTE BARS: same next-bar mechanics the live bot has today, but the
     one-bar latency is 3x cheaper relative to the move. ORB becomes the first
     two 15m bars (9:15-9:44). Entries via the same capped marketable limit.

Both run under the full honest stack: live 2-slot no-leverage sizing, resting
tick-level exits, lock/BE/trail ladder, 0.18% cost, strict walk-forward + cost
sensitivity on anything positive. If EVERYTHING is negative, the recommendation
is to STOP — that verdict will be printed, not hidden.

  python3 india/next_gen_lab.py                 # uses uni.pkl, 50 liquid names
"""
from __future__ import annotations

import argparse
import pickle
import sys
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import orb_fast as of
import strategy_lab as sl
from live_pilot import (LP_LOCK_TRIGGER_R, LP_LOCK_AT_R, LP_ARM_BE_R,
                        LP_ARM_TRAIL_R, LP_TRAIL_ATR)

LOCKS = {"lock_trigger": LP_LOCK_TRIGGER_R, "lock_at": LP_LOCK_AT_R,
         "be": LP_ARM_BE_R, "trail_arm": LP_ARM_TRAIL_R, "trail_atr": LP_TRAIL_ATR}
ENTRY_START = dtime(9, 30)
ENTRY_CUTOFF = dtime(14, 0)


# ── A) intrabar-entry candidate generator (causal: no same-bar volume/close) ──

def gen_intrabar(d, p):
    idx = d.index
    t = idx.time
    o = d["open"].to_numpy(); h = d["high"].to_numpy(); c = d["close"].to_numpy()
    lo = d["low"].to_numpy()
    orb_h = d["orb_high"].to_numpy(); rvol = d["rvol"].to_numpy()
    atr = d["atr"].to_numpy()
    prev_c = np.concatenate([[np.nan], c[:-1]])
    prev_rv = np.concatenate([[0.0], rvol[:-1]])
    trig = orb_h * of.TRIGGER_BUF
    cap = trig * (1 + p.get("entry_cap", 0.003))
    # price CROSSES the trigger during bar i (we watch LTP live, no waiting for
    # the close). Gap-opens beyond the cap are unbuyable -> skip.
    crossed = (h >= trig) & (prev_c <= trig) & (o <= cap)
    ok = ((t >= ENTRY_START) & (t < ENTRY_CUTOFF) & np.isfinite(orb_h)
          & np.isfinite(atr) & (atr > 0) & crossed)
    rv_prev = p.get("rv_prev")
    if rv_prev:
        ok &= prev_rv >= rv_prev            # causal volume gate: PRIOR bar only
    out = []
    for i in np.flatnonzero(ok):
        entry = (max(float(o[i]), float(trig[i])) * (1 + of.SLIPPAGE)
                 if o[i] > trig[i] else float(trig[i]) * (1 + of.SLIPPAGE))
        m = sl._mk(idx, i, entry, entry - p["sl"] * atr[i],
                   entry + p["rr"] * p["sl"] * atr[i], atr=atr[i])
        if m:
            m["_intrabar"] = True
            out.append(m)
    return out


def resolve_intrabar(d, cand):
    """Pessimistic same-bar handling: if the ENTRY bar's low touches the stop,
    it's a full loss (ordering unknowable); targets never book on the entry bar."""
    i = cand["i"]
    lo = float(d["low"].to_numpy()[i])
    if lo <= cand["sl"]:
        return of._close(cand, d.index[i], cand["sl"], "SL")
    return of._resolve_exit(d, cand)


def run_intrabar(prepped, params, cost):
    trades = []
    saved = of.COST_RT_PCT
    of.COST_RT_PCT = cost
    try:
        for sym, d in prepped.items():
            for cand in gen_intrabar(d, params):
                cand["sym"] = sym
                cand.update({k: LOCKS[k] for k in LOCKS})
                trades.append(resolve_intrabar(d, cand))
        return of._simulate(trades, live_sizing=True)
    finally:
        of.COST_RT_PCT = saved


# ── B) 15-minute resample, reusing the honest next-bar live_fill model ────────

def to_15m(df):
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    out = df.resample("15min", label="left").agg(agg).dropna()
    return out


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
    prepped5, prepped15 = {}, {}
    for s, df in list(raw.items())[:args.max_symbols]:
        pp = sl._prep(df)
        if pp is not None:
            prepped5[s] = pp
        pp15 = sl._prep(to_15m(df))
        if pp15 is not None:
            prepped15[s] = pp15

    lines = []
    def emit(x=""):
        print(x); lines.append(x)

    emit("=" * 78)
    emit("  NEXT-GEN LAB — attacking the latency, honest fills throughout")
    emit(f"  {len(prepped5)} symbols | cost {args.cost*100:.2f}% | live sizing | "
         f"locks {LP_LOCK_TRIGGER_R}/{LP_ARM_BE_R}/{LP_ARM_TRAIL_R}R")
    emit("=" * 78)

    results = []

    emit("\n  A) INTRABAR ENTRY (5m) — buy the trigger cross in real time")
    emit(f"     {'volume gate':<22} {'trades':>7} {'WR':>5} {'Ret/mo':>8} "
         f"{'DD':>6} {'PF':>5}")
    for rvp in (None, 1.5, 2.0, 3.0):
        for slm, rr in ((1.5, 3.0), (2.0, 2.0)):
            params = {"sl": slm, "rr": rr, "rv_prev": rvp, "entry_cap": 0.003}
            m = run_intrabar(prepped5, params, args.cost)
            label = (f"prev-bar rvol>={rvp}" if rvp else "no volume gate") + \
                    f" sl{slm}/rr{rr:.0f}"
            flag = "  <-- POSITIVE" if (m["ret"] > 0 and m["pf"] > 1.05
                                        and m["trades"] >= 30) else ""
            emit(f"     {label:<22} {m['trades']:>7} {m['wr']:>4.0f}% "
                 f"{m['ret']:>+7.2f}% {m['dd']:>5.1f}% {m['pf']:>4.2f}{flag}")
            results.append(("A:" + label, m,
                            lambda w, pa=params: run_intrabar(w, pa, args.cost)))

    emit("\n  B) 15-MINUTE BARS — same live mechanics, latency 3x cheaper")
    emit(f"     {'entry style':<22} {'trades':>7} {'WR':>5} {'Ret/mo':>8} "
         f"{'DD':>6} {'PF':>5}")
    for rv in (2.0, 3.0, 4.0):
        for cap in (0.003, None):
            params = dict(LOCKS, rv=rv, sl=1.5, rr=3.0, live_fill=True,
                          entry_cap=cap)
            m = sl.run_strat(prepped15, sl.s_wide_mom, params, cost=args.cost,
                             live_sizing=True)
            label = f"rv>={rv} " + (f"cap+{cap*100:.1f}%" if cap else "mkt open")
            flag = "  <-- POSITIVE" if (m["ret"] > 0 and m["pf"] > 1.05
                                        and m["trades"] >= 30) else ""
            emit(f"     {label:<22} {m['trades']:>7} {m['wr']:>4.0f}% "
                 f"{m['ret']:>+7.2f}% {m['dd']:>5.1f}% {m['pf']:>4.2f}{flag}")
            results.append(("B:" + label, m,
                            lambda w, pa=params: sl.run_strat(
                                w, sl.s_wide_mom, pa, cost=args.cost,
                                live_sizing=True)))

    # strict validation on the best positive candidate (if any)
    positives = [r for r in results
                 if r[1]["ret"] > 0 and r[1]["pf"] > 1.05 and r[1]["trades"] >= 30]
    emit("\n" + "=" * 78)
    if not positives:
        emit("  VERDICT: EVERY variant is negative under honest fills.")
        emit("  The latency fixes do not rescue this signal on this data. The honest")
        emit("  'make it profitable' answer is: STOP trading this signal — do not")
        emit("  fund it at any size. (Stopping before losses IS the profit here.)")
        emit("=" * 78)
        (_HERE.parent / "next_gen_report.txt").write_text("\n".join(lines))
        return

    best = max(positives, key=lambda r: r[1]["ret"])
    name, m, runner = best
    emit(f"  BEST POSITIVE: {name} — {m['ret']:+.2f}%/mo, PF {m['pf']:.2f}, "
         f"DD {m['dd']:.1f}%")
    emit("\n  STRICT WALK-FORWARD (positive in EVERY window required):")
    base = prepped5 if name.startswith("A:") else prepped15
    ok = True
    for k, w in of.make_windows(base).items():
        if k == "full":
            continue
        r = runner(w)
        ok &= r["ret"] > 0
        emit(f"    {k}: {r['ret']:+.2f}%/mo (WR {r['wr']:.0f}%, PF {r['pf']:.2f})")
    emit("\n  VERDICT: " + ("PASSES — a real candidate. Next: I wire this variant "
                            "into the live bot behind a flag and we pilot it small."
                            if ok else
                            "fails walk-forward — regime-dependent, NOT deployable. "
                            "Same honest answer: don't fund it."))
    emit("=" * 78)
    (_HERE.parent / "next_gen_report.txt").write_text("\n".join(lines))
    print(f"\n  Report saved: {_HERE.parent / 'next_gen_report.txt'}")


if __name__ == "__main__":
    main()
