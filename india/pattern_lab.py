"""
pattern_lab.py — INTRADAY CHART PATTERNS ONLY (no indicators), honestly tested.

12 classic price-action patterns, detected purely from OHLC structure — no RSI,
no MACD, no moving averages, no VWAP. Candlesticks: engulfing, hammer, morning
star, three soldiers, inside-bar break, doji-at-low. Structure: double bottom,
flag/pennant, narrow-range break, prev-day-high break, gap fade, first pullback.

HONEST RULES (the same ones that killed the indicator bot):
  * signal on a CLOSED 5-min bar -> entry at the NEXT bar's open (no lookahead)
  * stop = the pattern's own structural low | target = 2R (gap-fade: gap fill)
  * exits bar-by-bar, stop-first on ties (pessimistic) | square-off 15:00
  * cost 0.25% round-trip (realistic ₹1L+ intraday) | live 2-slot sizing
  * strict thirds walk-forward on anything positive

  python3 india/pattern_lab.py            # uses uni.pkl on the VPS
"""
from __future__ import annotations
import argparse, pickle, sys
from datetime import time as dtime
from pathlib import Path
import numpy as np

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import orb_fast as of

COST = 0.0025
ENTRY_START, ENTRY_END = dtime(9, 45), dtime(14, 30)
SQUAREOFF = dtime(15, 0)


def _trades_for_day(sym, o, h, l, c, idx, prev_day_high, out):
    n = len(c)
    if n < 12:
        return
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    med_body = np.median(body[:max(6, n // 4)]) or 1e-9
    day_low_so_far = np.minimum.accumulate(l)
    day_open = o[0]
    prev_close_gap = None

    def emit(i, stop, target, tag):
        """entry at next bar open; walk exits to squareoff."""
        if i + 1 >= n:
            return
        t = idx[i + 1].time()
        if not (ENTRY_START <= t <= ENTRY_END):
            return
        entry = float(o[i + 1])
        if not (stop < entry < target):
            return
        px, why, j_exit = None, None, None
        for j in range(i + 1, n):
            if idx[j].time() >= SQUAREOFF:
                px, why, j_exit = float(o[j]), "SQOFF", j
                break
            if l[j] <= stop:                      # stop-first, pessimistic
                px, why, j_exit = float(stop), "SL", j
                break
            if h[j] >= target:
                px, why, j_exit = float(target), "TP", j
                break
        if px is None:
            px, why, j_exit = float(c[-1]), "EOD", n - 1
        gross = px / entry - 1.0
        out[tag].append({"sym": sym, "t_entry": idx[i + 1], "t_exit": idx[j_exit],
                         "entry": entry, "sl_dist": max(entry - stop, entry * 5e-3),
                         "gross_ret": gross, "net_ret": gross - COST,
                         "exit": px, "why": why})

    for i in range(6, n - 1):
        green = c[i] > o[i]
        # 1) BULLISH ENGULFING after a dip
        if (green and c[i - 1] < o[i - 1] and o[i] <= c[i - 1] and c[i] >= o[i - 1]
                and body[i] > body[i - 1] and c[i - 1] < c[max(0, i - 5)]):
            emit(i, l[i], o[i + 1] if False else c[i] + 2 * (c[i] - l[i]), "engulfing")
        # 2) HAMMER (pin bar) after a dip
        low_wick = np.minimum(o[i], c[i]) - l[i]
        if (low_wick >= 2 * body[i] and (h[i] - np.maximum(o[i], c[i])) <= 0.3 * rng[i]
                and c[i] >= l[i] + 0.6 * rng[i] and c[i] < c[max(0, i - 4)]):
            emit(i, l[i], c[i] + 2 * (c[i] - l[i]), "hammer")
        # 3) MORNING STAR (3-bar reversal)
        if (i >= 2 and c[i - 2] < o[i - 2] and body[i - 2] > med_body
                and body[i - 1] < 0.5 * med_body and green
                and c[i] > (o[i - 2] + c[i - 2]) / 2):
            stop = min(l[i - 2], l[i - 1], l[i])
            emit(i, stop, c[i] + 2 * (c[i] - stop), "morning_star")
        # 4) THREE WHITE SOLDIERS
        if (i >= 2 and all(c[k] > o[k] for k in (i - 2, i - 1, i))
                and c[i] > c[i - 1] > c[i - 2]
                and all(body[k] > med_body for k in (i - 2, i - 1, i))):
            emit(i, l[i - 2], c[i] + 2 * (c[i] - l[i - 2]), "three_soldiers")
        # 5) INSIDE-BAR BREAKOUT
        if (i >= 2 and h[i - 1] < h[i - 2] and l[i - 1] > l[i - 2]
                and c[i] > h[i - 2]):
            emit(i, l[i - 1], c[i] + 2 * (c[i] - l[i - 1]), "inside_break")
        # 6) DOJI AT SESSION LOW
        if (body[i] <= 0.15 * rng[i] and l[i] <= day_low_so_far[i] * 1.0005
                and c[i] < day_open):
            emit(i, l[i], c[i] + 2 * (c[i] - l[i]), "doji_at_low")
        # 7) DOUBLE BOTTOM (two matching lows, neckline break)
        if i >= 8:
            w_l = l[i - 8:i + 1]
            k1 = int(np.argmin(w_l[:5]))
            k2 = 5 + int(np.argmin(w_l[5:]))
            lo1, lo2 = w_l[k1], w_l[k2]
            neck = float(np.max(h[i - 8 + k1:i - 8 + k2 + 1]))
            if (abs(lo1 - lo2) <= 0.002 * lo1 and k2 - k1 >= 3
                    and c[i] > neck and c[i - 1] <= neck):
                emit(i, min(lo1, lo2), c[i] + 2 * (c[i] - min(lo1, lo2)),
                     "double_bottom")
        # 8) FLAG / PENNANT (pole + tight consolidation + break)
        if i >= 9:
            pole = c[i - 4] / c[i - 9] - 1.0
            cons_h = float(np.max(h[i - 3:i]))
            cons_l = float(np.min(l[i - 3:i]))
            if (pole >= 0.012 and (cons_h - cons_l) <= 0.4 * (c[i - 4] - c[i - 9])
                    and c[i] > cons_h):
                emit(i, cons_l, c[i] + 2 * (c[i] - cons_l), "flag_break")
        # 9) NARROW-RANGE (NR7) BREAK
        if i >= 7 and rng[i - 1] == np.min(rng[i - 7:i]) and c[i] > h[i - 1]:
            emit(i, l[i - 1], c[i] + 2 * (c[i] - l[i - 1]), "nr7_break")
        # 10) PREV-DAY-HIGH BREAK (first cross of the day)
        if (prev_day_high and c[i] > prev_day_high
                and np.all(c[:i] <= prev_day_high)):
            emit(i, l[i], c[i] + 2 * (c[i] - l[i]), "pdh_break")
        # 12) FIRST PULLBACK after an opening drive
        if 4 <= i <= 10:
            drive = c[2] / o[0] - 1.0
            pulled = c[i - 1] < c[2] and l[i - 1] > day_open
            if drive >= 0.008 and pulled and c[i] > h[i - 1]:
                emit(i, float(np.min(l[3:i])), c[i] + 2 * (c[i] - np.min(l[3:i])),
                     "first_pullback")
    # 11) GAP FADE (open gap-down, first green bar, target = gap fill)
    # prev_day_high used as proxy prev close unavailable -> handled by caller
    return


def run(cache, max_symbols):
    raw = pickle.load(open(cache, "rb"))
    PATTERNS = ["engulfing", "hammer", "morning_star", "three_soldiers",
                "inside_break", "doji_at_low", "double_bottom", "flag_break",
                "nr7_break", "pdh_break", "first_pullback", "gap_fade"]
    out = {p: [] for p in PATTERNS}
    n_syms = 0
    for sym, df in list(raw.items())[:max_symbols]:
        if df is None or len(df) < 500:
            continue
        n_syms += 1
        df = df.sort_index()
        days = df.index.normalize()
        uniq = days.unique()
        prev_high, prev_close = None, None
        for d in uniq:
            day = df[days == d]
            o = day["open"].to_numpy(); h = day["high"].to_numpy()
            l = day["low"].to_numpy(); c = day["close"].to_numpy()
            idx = day.index
            _trades_for_day(sym, o, h, l, c, idx, prev_high, out)
            # GAP FADE needs prev close
            if prev_close and len(c) > 8:
                gap = o[0] / prev_close - 1.0
                if gap <= -0.01:
                    for i in range(1, 6):
                        if c[i] > o[i]:                     # first green bar
                            entry_i = i
                            if entry_i + 1 < len(c):
                                entry = float(o[entry_i + 1])
                                stop = float(np.min(l[:entry_i + 1]))
                                target = float(prev_close)   # gap fill
                                if stop < entry < target:
                                    px, why, je = None, None, None
                                    for j in range(entry_i + 1, len(c)):
                                        if idx[j].time() >= SQUAREOFF:
                                            px, je = float(o[j]), j; break
                                        if l[j] <= stop:
                                            px, je = stop, j; break
                                        if h[j] >= target:
                                            px, je = target, j; break
                                    if px is None:
                                        px, je = float(c[-1]), len(c) - 1
                                    g = px / entry - 1.0
                                    out["gap_fade"].append(
                                        {"sym": sym, "t_entry": idx[entry_i + 1],
                                         "t_exit": idx[je], "entry": entry,
                                         "sl_dist": max(entry - stop, entry * 5e-3),
                                         "gross_ret": g, "net_ret": g - COST,
                                         "exit": px, "why": "GF"})
                            break
            prev_high, prev_close = float(day["high"].max()), float(c[-1])
    return out, n_syms


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
    out, n_syms = run(p, args.max_symbols)

    lines = []
    def emit(x=""):
        print(x); lines.append(x)
    emit("=" * 78)
    emit("  PATTERN LAB — 12 pure chart patterns, intraday, NO indicators")
    emit(f"  {n_syms} symbols | honest next-bar entries | 2R targets, structural "
         f"stops | {COST*100:.2f}% cost")
    emit("=" * 78)
    emit(f"  {'pattern':<16} {'trades':>7} {'WR':>5} {'Ret/mo':>8} {'DD':>6} {'PF':>5}")
    results = []
    for name, trades in out.items():
        m = (of._simulate(trades, live_sizing=True) if trades
             else {"trades": 0, "wr": 0, "ret": 0, "dd": 0, "pf": 0})
        flag = ("  <-- POSITIVE" if m["ret"] > 0 and m["pf"] > 1.05
                and m["trades"] >= 40 else "")
        emit(f"  {name:<16} {m['trades']:>7} {m['wr']:>4.0f}% {m['ret']:>+7.2f}% "
             f"{m['dd']:>5.1f}% {m['pf']:>4.2f}{flag}")
        results.append((name, m, trades))

    emit("\n  STRICT WALK-FORWARD on positives (all thirds must be positive):")
    any_pass = False
    for name, m, trades in results:
        if not (m["ret"] > 0 and m["pf"] > 1.05 and m["trades"] >= 40):
            continue
        parts = [of._simulate(t, live_sizing=True)["ret"] if t else -99
                 for t in thirds(trades)]
        ok = all(r > 0 for r in parts)
        any_pass |= ok
        emit(f"    {name:<16} " + " ".join(f"{r:+.2f}%" for r in parts)
             + ("   PASSES" if ok else "   fails"))
    emit("\n" + "=" * 78)
    if any_pass:
        emit("  VERDICT: a pattern PASSES the honest stack — send this output back;")
        emit("  next step is cost-stress + paper pilot before any deployment talk.")
    else:
        emit("  VERDICT: no chart pattern survives honest intraday testing on this")
        emit("  data. Patterns aren't magic exempt from costs+latency — same physics")
        emit("  that killed the indicator strategies. The MF plan remains the answer.")
    emit("=" * 78)
    (_HERE.parent / "pattern_report.txt").write_text("\n".join(lines))
    print(f"\n  Report saved: {_HERE.parent / 'pattern_report.txt'}")


if __name__ == "__main__":
    main()
