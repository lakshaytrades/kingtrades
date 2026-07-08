"""
new_edge_lab.py — FOUR genuinely different strategy families, honestly tested.

The intraday ORB signal is dead (no edge under real fills — see next_gen_lab).
These four families don't die from what killed it (5-min latency, intraday fee
drag). All are built from the SAME cached 1-yr 5-min data, aggregated to daily:

  1) OVERNIGHT GAP   — buy at the close, sell at the next open (zero intraday
                       latency; one decision a day)
  2) WEEKLY MOMENTUM — hold the 3 strongest names for a week at a time
                       (latency irrelevant; moves 10x bigger than costs)
  3) MEAN REVERSION  — buy hard down-days at the close, exit on the bounce
                       (late entries HELP: price is lower, not higher)
  4) BIG-MOVE DRIFT  — after a +5% high-volume day, ride the continuation

HONEST COSTS: these hold overnight => DELIVERY, not MIS. Upstox delivery:
STT 0.1% BOTH sides (0.2% RT — 8x the intraday STT), brokerage min(2.5%, ₹30)/
order, ~0.1% slippage RT => ~0.40% round-trip at ₹50k positions (falls toward
~0.33% at ₹2L). Default --cost 0.0040. If a family only works at 0.20%, it
doesn't work.

Sizing: live-style equal-split, 2 slots (rotation: 3 slots), NO leverage.
Walk-forward: every positive variant must be positive in EACH third of the year.

  python3 india/new_edge_lab.py                 # uses uni.pkl
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import orb_fast as of

DELIV_RT = 0.0040          # honest delivery round-trip at ~₹50k positions


def to_daily(df):
    g = df.groupby(df.index.normalize())
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                      "low": g["low"].min(), "close": g["close"].last(),
                      "volume": g["volume"].sum()}).dropna()
    return d


def _trade(sym, t_in, t_out, entry, exit_px, cost):
    gross = exit_px / entry - 1.0
    return {"sym": sym, "t_entry": t_in, "t_exit": t_out, "entry": float(entry),
            "sl_dist": float(entry) * 0.02, "gross_ret": gross,
            "net_ret": gross - cost, "exit": float(exit_px), "why": "X"}


# ── 1) overnight gap variants ─────────────────────────────────────────────────

def gen_overnight(daily, cost):
    """Three conditions for holding one night: strong close, hard down day,
    high-volume up day."""
    out = {"gap: strong close": [], "gap: -3% down day": [], "gap: vol-up day": []}
    for sym, d in daily.items():
        o = d["open"].to_numpy(); h = d["high"].to_numpy(); l = d["low"].to_numpy()
        c = d["close"].to_numpy(); v = d["volume"].to_numpy()
        vavg = pd.Series(v).rolling(20).mean().to_numpy()
        idx = d.index
        for i in range(20, len(d) - 1):
            rng = max(h[i] - l[i], 1e-9)
            day_ret = c[i] / o[i] - 1.0
            t_in = idx[i] + pd.Timedelta(hours=15, minutes=25)
            t_out = idx[i + 1] + pd.Timedelta(hours=9, minutes=15)
            tr = _trade(sym, t_in, t_out, c[i], o[i + 1], cost)
            if (c[i] - l[i]) / rng >= 0.8 and day_ret > 0.01:
                out["gap: strong close"].append(tr)
            if day_ret <= -0.03:
                out["gap: -3% down day"].append(tr)
            if day_ret >= 0.02 and vavg[i] > 0 and v[i] >= 2 * vavg[i]:
                out["gap: vol-up day"].append(tr)
    return out


# ── 3) mean reversion / 4) big-move drift (daily, close entries) ─────────────

def gen_daily_swing(daily, cost):
    out = {"rev: buy -4% day, +2%/5d": [], "rev: buy -5% day, +2%/5d": [],
           "drift: +5% 2xVol, hold 3d": []}
    for sym, d in daily.items():
        o = d["open"].to_numpy(); c = d["close"].to_numpy(); v = d["volume"].to_numpy()
        vavg = pd.Series(v).rolling(20).mean().to_numpy()
        idx = d.index
        n = len(d)
        for i in range(20, n - 6):
            day_ret = c[i] / c[i - 1] - 1.0
            t_in = idx[i] + pd.Timedelta(hours=15, minutes=25)
            # mean reversion: exit first close >= +2%, stop at close <= -5%, else day 5
            for lbl, thr in (("rev: buy -4% day, +2%/5d", -0.04),
                             ("rev: buy -5% day, +2%/5d", -0.05)):
                if day_ret <= thr:
                    entry = c[i]
                    j_exit, px = i + 5, c[i + 5]
                    for j in range(i + 1, i + 6):
                        if c[j] >= entry * 1.02 or c[j] <= entry * 0.95:
                            j_exit, px = j, c[j]
                            break
                    out[lbl].append(_trade(sym, t_in,
                                           idx[j_exit] + pd.Timedelta(hours=15, minutes=25),
                                           entry, px, cost))
            # drift: big up day on volume -> hold 3 days
            if day_ret >= 0.05 and vavg[i] > 0 and v[i] >= 2 * vavg[i]:
                out["drift: +5% 2xVol, hold 3d"].append(
                    _trade(sym, t_in, idx[i + 3] + pd.Timedelta(hours=15, minutes=25),
                           c[i], c[i + 3], cost))
    return out


# ── 2) weekly momentum rotation (custom always-invested sim) ─────────────────

def run_rotation(daily, lookback=20, top_n=3, cost=DELIV_RT):
    closes = pd.DataFrame({s: d["close"] for s, d in daily.items()}).dropna(
        how="all").ffill()
    days = closes.index
    weeks = list(range(lookback, len(days) - 5, 5))
    eq = 1.0
    curve = [1.0]
    wk_rets = []
    for k in weeks:
        mom = closes.iloc[k] / closes.iloc[k - lookback] - 1.0
        top = mom.dropna().nlargest(top_n).index
        fwd = (closes.iloc[min(k + 5, len(days) - 1)][top]
               / closes.iloc[k][top] - 1.0).mean()
        # conservative: full basket turnover charged every week
        net = float(fwd) - cost
        eq *= (1 + net)
        wk_rets.append(net)
        curve.append(eq)
    curve = np.array(curve)
    dd = float(((np.maximum.accumulate(curve) - curve)
                / np.maximum.accumulate(curve)).max() * 100)
    months = max(len(wk_rets) * 5 / 21.0, 0.1)
    ret_mo = ((eq ** (1 / months)) - 1) * 100 if eq > 0 else -99.0
    wins = sum(1 for r in wk_rets if r > 0)
    pf = (sum(r for r in wk_rets if r > 0)
          / max(abs(sum(r for r in wk_rets if r <= 0)), 1e-9))
    return {"trades": len(wk_rets), "wr": wins / max(len(wk_rets), 1) * 100,
            "ret": ret_mo, "dd": dd, "pf": pf, "rets": wk_rets}


# ── shared: sim + thirds walk-forward ─────────────────────────────────────────

def sim(trades):
    return of._simulate(trades, live_sizing=True)


def thirds(trades):
    ts = sorted(trades, key=lambda t: t["t_entry"])
    n = len(ts)
    return [ts[:n // 3], ts[n // 3:2 * n // 3], ts[2 * n // 3:]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="uni.pkl")
    ap.add_argument("--max-symbols", type=int, default=127)
    ap.add_argument("--cost", type=float, default=DELIV_RT,
                    help="delivery round-trip cost (default 0.40%%)")
    args = ap.parse_args()

    p = Path(args.cache)
    if not p.is_absolute():
        p = _HERE / p
    raw = pickle.load(open(p, "rb"))
    daily = {}
    for s, df in list(raw.items())[:args.max_symbols]:
        d = to_daily(df)
        if len(d) > 60:
            daily[s] = d

    lines = []
    def emit(x=""):
        print(x); lines.append(x)

    emit("=" * 78)
    emit("  NEW-EDGE LAB — four families, delivery costs, live sizing, no leverage")
    emit(f"  {len(daily)} symbols | ~{len(next(iter(daily.values())))} trading days "
         f"| cost {args.cost*100:.2f}% RT (delivery incl. 0.2% STT)")
    emit("=" * 78)
    emit(f"  {'strategy':<28} {'trades':>7} {'WR':>5} {'Ret/mo':>8} {'DD':>6} {'PF':>5}")

    family_trades = {}
    family_trades.update(gen_overnight(daily, args.cost))
    family_trades.update(gen_daily_swing(daily, args.cost))

    results = []
    for name, trades in family_trades.items():
        m = sim(trades) if trades else {"trades": 0, "wr": 0, "ret": 0,
                                        "dd": 0, "pf": 0}
        flag = ("  <-- POSITIVE" if m["ret"] > 0 and m["pf"] > 1.05
                and m["trades"] >= 30 else "")
        emit(f"  {name:<28} {m['trades']:>7} {m['wr']:>4.0f}% {m['ret']:>+7.2f}% "
             f"{m['dd']:>5.1f}% {m['pf']:>4.2f}{flag}")
        results.append((name, m, trades))

    rot = run_rotation(daily, cost=args.cost)
    flag = ("  <-- POSITIVE" if rot["ret"] > 0 and rot["pf"] > 1.05
            and rot["trades"] >= 20 else "")
    emit(f"  {'weekly momentum top-3':<28} {rot['trades']:>7} {rot['wr']:>4.0f}% "
         f"{rot['ret']:>+7.2f}% {rot['dd']:>5.1f}% {rot['pf']:>4.2f}{flag}")

    # walk-forward on every positive trade-list family
    emit("\n  STRICT WALK-FORWARD (each third of the year must be positive):")
    any_pass = False
    for name, m, trades in results:
        if not (m["ret"] > 0 and m["pf"] > 1.05 and m["trades"] >= 30):
            continue
        parts = [sim(t)["ret"] if t else -99 for t in thirds(trades)]
        ok = all(r > 0 for r in parts)
        any_pass |= ok
        emit(f"    {name:<28} " +
             " ".join(f"{r:+.1f}%" for r in parts) +
             ("   PASSES" if ok else "   fails"))
    if rot["ret"] > 0 and rot["pf"] > 1.05:
        r3 = [rot["rets"][i::1] for i in [0]]  # thirds of weekly rets
        n = len(rot["rets"])
        parts = []
        for a, b in ((0, n // 3), (n // 3, 2 * n // 3), (2 * n // 3, n)):
            seg = rot["rets"][a:b]
            months = max(len(seg) * 5 / 21.0, 0.1)
            eqs = float(np.prod([1 + r for r in seg]))
            parts.append(((eqs ** (1 / months)) - 1) * 100 if eqs > 0 else -99)
        ok = all(r > 0 for r in parts)
        any_pass |= ok
        emit(f"    {'weekly momentum top-3':<28} " +
             " ".join(f"{r:+.1f}%" for r in parts) +
             ("   PASSES" if ok else "   fails"))

    emit("\n" + "=" * 78)
    if any_pass:
        emit("  VERDICT: at least one family PASSES the full honest stack. Next step:")
        emit("  cost-sensitivity + paper pilot of the passer(s) — still zero real money")
        emit("  until that holds too. Send this output back for the build-out.")
    else:
        emit("  VERDICT: no family passes on this data at delivery costs. The honest")
        emit("  answer stays: index SIP for the capital; no live trading. Nothing here")
        emit("  earns real money yet — and that finding cost ₹0.")
    emit("=" * 78)
    (_HERE.parent / "new_edge_report.txt").write_text("\n".join(lines))
    print(f"\n  Report saved: {_HERE.parent / 'new_edge_report.txt'}")


if __name__ == "__main__":
    main()
