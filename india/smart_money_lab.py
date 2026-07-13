"""
smart_money_lab.py — retail-testable versions of BIG-PLAYER strategies.

HONEST CAVEAT: the real institutional edge is INFRASTRUCTURE (co-location speed,
order-flow data, near-zero cost, huge capital) — none of which a retail account
has. This tests the retail-accessible CONCEPTS behind common institutional
plays, on your cached data, with the same honest harness (next-bar entries,
real cost, structural stops, 15:00 flat, thirds walk-forward):

  1) VWAP REVERSION      — institutions defend fair value: buy a deep stretch
                           BELOW intraday VWAP, target the return to VWAP.
  2) LIQUIDITY SWEEP      — 'stop hunt' accumulation: price sweeps BELOW the
                           prior-day low then RECLAIMS it -> smart-money long.
  3) RELATIVE STRENGTH    — money flows to strength: each day buy the names that
                           held up best vs the universe, exit next day.

  python3 india/smart_money_lab.py            # uses uni.pkl
"""
from __future__ import annotations
import argparse, pickle, sys
from datetime import time as dtime
from pathlib import Path
import numpy as np
import pandas as pd

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import orb_fast as of

COST = 0.0025
ENTRY_START, ENTRY_END = dtime(9, 45), dtime(14, 30)
SQUAREOFF = dtime(15, 0)


def _vwap(day):
    tp = (day["high"] + day["low"] + day["close"]) / 3.0
    pv = (tp * day["volume"]).cumsum()
    vv = day["volume"].cumsum().replace(0, np.nan)
    return (pv / vv).to_numpy()


def _walk_long(o, h, l, c, idx, i, stop, target, sym, out, tag):
    n = len(c)
    if i + 1 >= n or not (ENTRY_START <= idx[i + 1].time() <= ENTRY_END):
        return
    entry = float(o[i + 1])
    if not (stop < entry < target):
        return
    px, je = None, None
    for j in range(i + 1, n):
        if idx[j].time() >= SQUAREOFF:
            px, je = float(o[j]), j; break
        if l[j] <= stop:
            px, je = float(stop), j; break
        if h[j] >= target:
            px, je = float(target), j; break
    if px is None:
        px, je = float(c[-1]), n - 1
    g = px / entry - 1.0
    out[tag].append({"sym": sym, "t_entry": idx[i + 1], "t_exit": idx[je],
                     "entry": entry, "sl_dist": max(entry - stop, entry * 5e-3),
                     "gross_ret": g, "net_ret": g - COST, "exit": px, "why": tag})


def run_intraday(raw, max_symbols):
    out = {"vwap_reversion": [], "liquidity_sweep": []}
    n_syms = 0
    for sym, df in list(raw.items())[:max_symbols]:
        if df is None or len(df) < 500:
            continue
        n_syms += 1
        df = df.sort_index()
        days = df.index.normalize()
        prev_low = None
        for d in days.unique():
            day = df[days == d]
            o = day["open"].to_numpy(); h = day["high"].to_numpy()
            l = day["low"].to_numpy(); c = day["close"].to_numpy()
            idx = day.index
            n = len(c)
            if n < 12:
                prev_low = float(day["low"].min()); continue
            vwap = _vwap(day)
            swept = False
            for i in range(6, n - 1):
                # 1) VWAP reversion: price >=0.8% below VWAP -> target VWAP
                if vwap[i] > 0 and c[i] <= vwap[i] * 0.992:
                    _walk_long(o, h, l, c, idx, i, l[i], float(vwap[i]),
                               sym, out, "vwap_reversion")
                # 2) liquidity sweep: dip below prev-day low then reclaim it
                if prev_low and not swept and l[i] < prev_low and c[i] > prev_low:
                    swept = True
                    _walk_long(o, h, l, c, idx, i, l[i],
                               c[i] + 2 * (c[i] - l[i]), sym, out,
                               "liquidity_sweep")
            prev_low = float(day["low"].min())
    return out, n_syms


def run_rel_strength(raw, max_symbols):
    """Daily: each day rank stocks by prior-day return; buy the top few at close,
    exit next close. Institutional money-flow-follows-strength, honestly costed."""
    closes = {}
    for sym, df in list(raw.items())[:max_symbols]:
        if df is None or len(df) < 500:
            continue
        d = df.sort_index()
        g = d.groupby(d.index.normalize())["close"].last()
        closes[sym] = g
    if len(closes) < 10:
        return []
    px = pd.DataFrame(closes).dropna(how="all").ffill()
    rets = px.pct_change()
    days = px.index
    trades = []
    for k in range(2, len(days) - 1):
        yday = rets.iloc[k]                       # yesterday's move = strength
        top = yday.dropna().nlargest(3).index     # 3 strongest
        for s in top:
            entry = float(px[s].iloc[k])
            exit_px = float(px[s].iloc[k + 1])
            if entry <= 0:
                continue
            g = exit_px / entry - 1.0
            trades.append({"sym": s, "t_entry": days[k], "t_exit": days[k + 1],
                           "entry": entry, "sl_dist": entry * 0.02,
                           "gross_ret": g, "net_ret": g - COST,
                           "exit": exit_px, "why": "RS"})
    return trades


def thirds(trades):
    ts = sorted(trades, key=lambda t: t["t_entry"]); n = len(ts)
    return [ts[:n // 3], ts[n // 3:2 * n // 3], ts[2 * n // 3:]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="uni.pkl")
    ap.add_argument("--max-symbols", type=int, default=127)
    args = ap.parse_args()
    p = Path(args.cache)
    if not p.is_absolute():
        p = _HERE / p
    raw = pickle.load(open(p, "rb"))

    out, n_syms = run_intraday(raw, args.max_symbols)
    out["rel_strength"] = run_rel_strength(raw, args.max_symbols)

    lines = []
    def emit(x=""):
        print(x); lines.append(x)
    emit("=" * 78)
    emit("  SMART-MONEY LAB — retail-testable big-player concepts")
    emit(f"  {n_syms} symbols | honest harness | {COST*100:.2f}% cost | 15:00 flat")
    emit("  NOTE: the REAL institutional edge is speed/data/cost you can't buy;")
    emit("  this tests only the CONCEPTS on retail-accessible data.")
    emit("=" * 78)
    emit(f"  {'strategy':<18} {'trades':>7} {'WR':>5} {'Ret/mo':>8} {'DD':>6} {'PF':>5}")
    results = []
    for name in ("vwap_reversion", "liquidity_sweep", "rel_strength"):
        trades = out.get(name, [])
        m = (of._simulate(trades, live_sizing=True) if trades
             else {"trades": 0, "wr": 0, "ret": 0, "dd": 0, "pf": 0})
        flag = ("  <-- POSITIVE" if m["ret"] > 0 and m["pf"] > 1.05
                and m["trades"] >= 40 else "")
        emit(f"  {name:<18} {m['trades']:>7} {m['wr']:>4.0f}% {m['ret']:>+7.2f}% "
             f"{m['dd']:>5.1f}% {m['pf']:>4.2f}{flag}")
        results.append((name, m, trades))

    emit("\n  STRICT WALK-FORWARD on positives (all thirds positive):")
    passers = []
    for name, m, trades in results:
        if not (m["ret"] > 0 and m["pf"] > 1.05 and m["trades"] >= 40):
            continue
        parts = [of._simulate(t, live_sizing=True)["ret"] if t else -99
                 for t in thirds(trades)]
        ok = all(x > 0 for x in parts)
        if ok:
            passers.append(name)
        emit(f"    {name:<18} " + " ".join(f"{x:+.2f}%" for x in parts)
             + ("   PASSES" if ok else "   fails"))
    emit("\n" + "=" * 78)
    if passers:
        emit(f"  VERDICT: {', '.join(passers)} PASS honest testing. Next: cost-")
        emit("  stress + fresh-data re-test before believing it.")
    else:
        emit("  VERDICT: the big-player CONCEPTS don't survive retail costs on this")
        emit("  data — because the institutional edge was never the concept, it was")
        emit("  the infrastructure. Same lesson, confirmed. MF plan stands.")
    emit("=" * 78)
    (_HERE.parent / "smart_money_report.txt").write_text("\n".join(lines))
    print(f"\n  Report saved: {_HERE.parent / 'smart_money_report.txt'}")


if __name__ == "__main__":
    main()
