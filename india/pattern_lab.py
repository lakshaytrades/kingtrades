"""
pattern_lab.py — THE FULL PATTERN CATALOG, intraday, pure price action.

~30 classic chart/candlestick patterns — LONG and SHORT — detected purely from
OHLC structure (no indicators). Candles: engulfing (bull/bear), hammer,
shooting star, morning/evening star, three soldiers/crows, harami (bull/bear),
piercing line, dark cloud, tweezer top/bottom, marubozu, inside-bar break,
doji at extreme. Structures: double bottom/top, head & shoulders + inverse,
ascending/descending triangle, flag up/down, NR7 break/down, prev-day high/low
break, gap fade both sides, first pullback, session-range break/down.

HONEST RULES (identical to everything else in this repo):
  closed-bar signal -> NEXT bar's open entry | structural stop | 2R target
  (gap fades target the fill) | stop-first ties | 15:00 squareoff | 0.25% cost
  | live 2-slot sizing | strict thirds walk-forward on positives.

  python3 india/pattern_lab.py            # uses uni.pkl
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

PATTERNS = [
    # candles — long
    "bull_engulfing", "hammer", "morning_star", "three_soldiers", "bull_harami",
    "piercing_line", "tweezer_bottom", "marubozu_up", "inside_break_up",
    "doji_at_low",
    # candles — short
    "bear_engulfing", "shooting_star", "evening_star", "three_crows",
    "bear_harami", "dark_cloud", "tweezer_top", "marubozu_down",
    "inside_break_dn", "doji_at_high",
    # structures — long
    "double_bottom", "inv_head_shoulders", "asc_triangle", "flag_up",
    "nr7_break_up", "pdh_break", "gap_dn_fade", "first_pullback",
    "range_break_up",
    # structures — short
    "double_top", "head_shoulders", "desc_triangle", "flag_down",
    "nr7_break_dn", "pdl_break", "gap_up_fade", "range_break_dn",
]


def _day_engine(sym, o, h, l, c, idx, prev_high, prev_low, prev_close, out):
    n = len(c)
    if n < 14:
        return
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    med_body = max(float(np.median(body[:max(6, n // 4)])), 1e-9)
    day_open = o[0]
    run_low = np.minimum.accumulate(l)
    run_high = np.maximum.accumulate(h)

    def walk(i, entry, stop, target, tag, short=False):
        px, je = None, None
        for j in range(i + 1, n):
            if idx[j].time() >= SQUAREOFF:
                px, je = float(o[j]), j; break
            hit_stop = (h[j] >= stop) if short else (l[j] <= stop)
            hit_tp = (l[j] <= target) if short else (h[j] >= target)
            if hit_stop:                              # stop-first, pessimistic
                px, je = float(stop), j; break
            if hit_tp:
                px, je = float(target), j; break
        if px is None:
            px, je = float(c[-1]), n - 1
        gross = (entry - px) / entry if short else (px - entry) / entry
        out[tag].append({"sym": sym, "t_entry": idx[i + 1], "t_exit": idx[je],
                         "entry": entry, "sl_dist": max(abs(entry - stop),
                                                        entry * 5e-3),
                         "gross_ret": gross, "net_ret": gross - COST,
                         "exit": px, "why": tag})

    def go(i, stop, tag, target=None, short=False):
        if i + 1 >= n:
            return
        t = idx[i + 1].time()
        if not (ENTRY_START <= t <= ENTRY_END):
            return
        entry = float(o[i + 1])
        if short:
            tgt = target if target else entry - 2 * (stop - entry)
            if not (tgt < entry < stop):
                return
        else:
            tgt = target if target else entry + 2 * (entry - stop)
            if not (stop < entry < tgt):
                return
        walk(i, entry, float(stop), float(tgt), tag, short)

    for i in range(6, n - 1):
        g = c[i] > o[i]; r = c[i] < o[i]
        g1 = c[i - 1] > o[i - 1]; r1 = c[i - 1] < o[i - 1]
        upwick = h[i] - max(o[i], c[i]); dnwick = min(o[i], c[i]) - l[i]
        fell = c[i - 1] < c[max(0, i - 5)]; rose = c[i - 1] > c[max(0, i - 5)]

        # ── candles: long ──
        if g and r1 and o[i] <= c[i - 1] and c[i] >= o[i - 1] \
                and body[i] > body[i - 1] and fell:
            go(i, l[i], "bull_engulfing")
        if dnwick >= 2 * body[i] and upwick <= 0.3 * rng[i] \
                and c[i] >= l[i] + 0.6 * rng[i] and fell:
            go(i, l[i], "hammer")
        if i >= 2 and c[i - 2] < o[i - 2] and body[i - 2] > med_body \
                and body[i - 1] < 0.5 * med_body and g \
                and c[i] > (o[i - 2] + c[i - 2]) / 2:
            go(i, min(l[i - 2], l[i - 1], l[i]), "morning_star")
        if i >= 2 and all(c[k] > o[k] for k in (i - 2, i - 1, i)) \
                and c[i] > c[i - 1] > c[i - 2] \
                and all(body[k] > med_body for k in (i - 2, i - 1, i)):
            go(i, l[i - 2], "three_soldiers")
        if g and r1 and o[i] > c[i - 1] and c[i] < o[i - 1] \
                and body[i] < 0.6 * body[i - 1] and fell:          # bull harami
            go(i, min(l[i], l[i - 1]), "bull_harami")
        if g and r1 and o[i] < l[i - 1] and c[i] > (o[i - 1] + c[i - 1]) / 2 \
                and c[i] < o[i - 1] and fell:
            go(i, l[i], "piercing_line")
        if r1 and g and abs(l[i] - l[i - 1]) <= 0.001 * l[i] and fell:
            go(i, min(l[i], l[i - 1]), "tweezer_bottom")
        if g and body[i] >= 0.85 * rng[i] and body[i] > 1.5 * med_body:
            go(i, l[i], "marubozu_up")
        if i >= 2 and h[i - 1] < h[i - 2] and l[i - 1] > l[i - 2] \
                and c[i] > h[i - 2]:
            go(i, l[i - 1], "inside_break_up")
        if body[i] <= 0.15 * rng[i] and l[i] <= run_low[i] * 1.0005 \
                and c[i] < day_open:
            go(i, l[i], "doji_at_low")

        # ── candles: short ──
        if r and g1 and o[i] >= c[i - 1] and c[i] <= o[i - 1] \
                and body[i] > body[i - 1] and rose:
            go(i, h[i], "bear_engulfing", short=True)
        if upwick >= 2 * body[i] and dnwick <= 0.3 * rng[i] \
                and c[i] <= h[i] - 0.6 * rng[i] and rose:
            go(i, h[i], "shooting_star", short=True)
        if i >= 2 and c[i - 2] > o[i - 2] and body[i - 2] > med_body \
                and body[i - 1] < 0.5 * med_body and r \
                and c[i] < (o[i - 2] + c[i - 2]) / 2:
            go(i, max(h[i - 2], h[i - 1], h[i]), "evening_star", short=True)
        if i >= 2 and all(c[k] < o[k] for k in (i - 2, i - 1, i)) \
                and c[i] < c[i - 1] < c[i - 2] \
                and all(body[k] > med_body for k in (i - 2, i - 1, i)):
            go(i, h[i - 2], "three_crows", short=True)
        if r and g1 and o[i] < c[i - 1] and c[i] > o[i - 1] \
                and body[i] < 0.6 * body[i - 1] and rose:
            go(i, max(h[i], h[i - 1]), "bear_harami", short=True)
        if r and g1 and o[i] > h[i - 1] and c[i] < (o[i - 1] + c[i - 1]) / 2 \
                and c[i] > o[i - 1] and rose:
            go(i, h[i], "dark_cloud", short=True)
        if g1 and r and abs(h[i] - h[i - 1]) <= 0.001 * h[i] and rose:
            go(i, max(h[i], h[i - 1]), "tweezer_top", short=True)
        if r and body[i] >= 0.85 * rng[i] and body[i] > 1.5 * med_body:
            go(i, h[i], "marubozu_down", short=True)
        if i >= 2 and h[i - 1] < h[i - 2] and l[i - 1] > l[i - 2] \
                and c[i] < l[i - 2]:
            go(i, h[i - 1], "inside_break_dn", short=True)
        if body[i] <= 0.15 * rng[i] and h[i] >= run_high[i] * 0.9995 \
                and c[i] > day_open:
            go(i, h[i], "doji_at_high", short=True)

        # ── structures ──
        if i >= 8:
            wl, wh = l[i - 8:i + 1], h[i - 8:i + 1]
            k1 = int(np.argmin(wl[:5])); k2 = 5 + int(np.argmin(wl[5:]))
            if abs(wl[k1] - wl[k2]) <= 0.002 * wl[k1] and k2 - k1 >= 3:
                neck = float(np.max(wh[k1:k2 + 1]))
                if c[i] > neck >= c[i - 1]:
                    go(i, min(wl[k1], wl[k2]), "double_bottom")
            q1 = int(np.argmax(wh[:5])); q2 = 5 + int(np.argmax(wh[5:]))
            if abs(wh[q1] - wh[q2]) <= 0.002 * wh[q1] and q2 - q1 >= 3:
                neck = float(np.min(wl[q1:q2 + 1]))
                if c[i] < neck <= c[i - 1]:
                    go(i, max(wh[q1], wh[q2]), "double_top", short=True)
        if i >= 12:
            wh, wl = h[i - 12:i + 1], l[i - 12:i + 1]
            p2 = int(np.argmax(wh))
            if 3 <= p2 <= 9:
                p1 = int(np.argmax(wh[:p2 - 1])) if p2 >= 2 else 0
                p3 = p2 + 1 + int(np.argmax(wh[p2 + 1:]))
                if (wh[p2] > wh[p1] and wh[p2] > wh[p3]
                        and abs(wh[p1] - wh[p3]) <= 0.004 * wh[p1]):
                    neck = float(min(np.min(wl[p1:p2 + 1]), np.min(wl[p2:p3 + 1])))
                    if c[i] < neck <= c[i - 1]:
                        go(i, wh[p2], "head_shoulders", short=True)
            v2 = int(np.argmin(wl))
            if 3 <= v2 <= 9:
                v1 = int(np.argmin(wl[:v2 - 1])) if v2 >= 2 else 0
                v3 = v2 + 1 + int(np.argmin(wl[v2 + 1:]))
                if (wl[v2] < wl[v1] and wl[v2] < wl[v3]
                        and abs(wl[v1] - wl[v3]) <= 0.004 * wl[v1]):
                    neck = float(max(np.max(h[i - 12:i + 1][v1:v2 + 1]),
                                     np.max(h[i - 12:i + 1][v2:v3 + 1])))
                    if c[i] > neck >= c[i - 1]:
                        go(i, wl[v2], "inv_head_shoulders")
        if i >= 7:
            wh, wl = h[i - 6:i], l[i - 6:i]
            flat_hi = (np.max(wh) - np.min(wh)) <= 0.0015 * c[i]
            rising_lo = wl[-1] > wl[0] and wl[-2] > wl[1]
            if flat_hi and rising_lo and c[i] > np.max(wh):
                go(i, float(wl[-1]), "asc_triangle")
            flat_lo = (np.max(wl) - np.min(wl)) <= 0.0015 * c[i]
            falling_hi = wh[-1] < wh[0] and wh[-2] < wh[1]
            if flat_lo and falling_hi and c[i] < np.min(wl):
                go(i, float(wh[-1]), "desc_triangle", short=True)
        if i >= 9:
            pole = c[i - 4] / c[i - 9] - 1.0
            ch, cl = float(np.max(h[i - 3:i])), float(np.min(l[i - 3:i]))
            if pole >= 0.012 and (ch - cl) <= 0.4 * abs(c[i - 4] - c[i - 9]) \
                    and c[i] > ch:
                go(i, cl, "flag_up")
            if pole <= -0.012 and (ch - cl) <= 0.4 * abs(c[i - 4] - c[i - 9]) \
                    and c[i] < cl:
                go(i, ch, "flag_down", short=True)
        if i >= 7 and rng[i - 1] == np.min(rng[i - 7:i]):
            if c[i] > h[i - 1]:
                go(i, l[i - 1], "nr7_break_up")
            if c[i] < l[i - 1]:
                go(i, h[i - 1], "nr7_break_dn", short=True)
        if prev_high and c[i] > prev_high and np.all(c[:i] <= prev_high):
            go(i, l[i], "pdh_break")
        if prev_low and c[i] < prev_low and np.all(c[:i] >= prev_low):
            go(i, h[i], "pdl_break", short=True)
        if 4 <= i <= 10:
            drive = c[2] / o[0] - 1.0
            if drive >= 0.008 and c[i - 1] < c[2] and l[i - 1] > day_open \
                    and c[i] > h[i - 1]:
                go(i, float(np.min(l[3:i])), "first_pullback")
        if i >= 6:                                    # session range break (1st 30m)
            rh, rl = float(np.max(h[:6])), float(np.min(l[:6]))
            if c[i] > rh and np.all(c[6:i] <= rh):
                go(i, rl + 0.5 * (rh - rl), "range_break_up")
            if c[i] < rl and np.all(c[6:i] >= rl):
                go(i, rl + 0.5 * (rh - rl), "range_break_dn", short=True)

    # gap fades (need prev_close)
    if prev_close and n > 8:
        gap = o[0] / prev_close - 1.0
        if gap <= -0.01:
            for i in range(1, 6):
                if c[i] > o[i]:
                    go(i, float(np.min(l[:i + 1])), "gap_dn_fade",
                       target=float(prev_close))
                    break
        if gap >= 0.01:
            for i in range(1, 6):
                if c[i] < o[i]:
                    go(i, float(np.max(h[:i + 1])), "gap_up_fade",
                       target=float(prev_close), short=True)
                    break


def run(cache, max_symbols):
    raw = pickle.load(open(cache, "rb"))
    out = {p: [] for p in PATTERNS}
    n_syms = 0
    for sym, df in list(raw.items())[:max_symbols]:
        if df is None or len(df) < 500:
            continue
        n_syms += 1
        df = df.sort_index()
        days = df.index.normalize()
        prev_high = prev_low = prev_close = None
        for d in days.unique():
            day = df[days == d]
            _day_engine(sym, day["open"].to_numpy(), day["high"].to_numpy(),
                        day["low"].to_numpy(), day["close"].to_numpy(),
                        day.index, prev_high, prev_low, prev_close, out)
            prev_high = float(day["high"].max())
            prev_low = float(day["low"].min())
            prev_close = float(day["close"].iloc[-1])
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
    emit(f"  PATTERN LAB — {len(PATTERNS)} chart patterns, LONG + SHORT, no "
         f"indicators")
    emit(f"  {n_syms} symbols | next-bar entries | structural stops, 2R | "
         f"{COST*100:.2f}% cost | 15:00 flat")
    emit("=" * 78)
    emit(f"  {'pattern':<20} {'side':<6} {'trades':>7} {'WR':>5} {'Ret/mo':>8} "
         f"{'DD':>6} {'PF':>5}")
    results = []
    SHORTS = {"bear_engulfing", "shooting_star", "evening_star", "three_crows",
              "bear_harami", "dark_cloud", "tweezer_top", "marubozu_down",
              "inside_break_dn", "doji_at_high", "double_top", "head_shoulders",
              "desc_triangle", "flag_down", "nr7_break_dn", "pdl_break",
              "gap_up_fade", "range_break_dn"}
    for name in PATTERNS:
        trades = out[name]
        m = (of._simulate(trades, live_sizing=True) if trades
             else {"trades": 0, "wr": 0, "ret": 0, "dd": 0, "pf": 0})
        side = "SHORT" if name in SHORTS else "LONG"
        flag = ("  <-- POSITIVE" if m["ret"] > 0 and m["pf"] > 1.05
                and m["trades"] >= 40 else "")
        emit(f"  {name:<20} {side:<6} {m['trades']:>7} {m['wr']:>4.0f}% "
             f"{m['ret']:>+7.2f}% {m['dd']:>5.1f}% {m['pf']:>4.2f}{flag}")
        results.append((name, m, trades))

    emit("\n  STRICT WALK-FORWARD on positives (all thirds must be positive):")
    passers = []
    for name, m, trades in results:
        if not (m["ret"] > 0 and m["pf"] > 1.05 and m["trades"] >= 40):
            continue
        parts = [of._simulate(t, live_sizing=True)["ret"] if t else -99
                 for t in thirds(trades)]
        ok = all(x > 0 for x in parts)
        if ok:
            passers.append(name)
        emit(f"    {name:<20} " + " ".join(f"{x:+.2f}%" for x in parts)
             + ("   PASSES" if ok else "   fails"))
    emit("\n" + "=" * 78)
    if passers:
        emit(f"  VERDICT: {len(passers)} pattern(s) PASS the honest stack: "
             f"{', '.join(passers)}")
        emit("  CAUTION: testing ~35 patterns means ~2 could pass by pure luck")
        emit("  (multiple-testing). Next: cost-stress + fresh-data re-test before")
        emit("  believing any of them. Send this output back for that step.")
    else:
        emit("  VERDICT: none of the ~35 classic patterns survives honest intraday")
        emit("  testing on this data. This is the textbook academic result — chart")
        emit("  patterns alone don't beat costs+latency. The MF plan stands.")
    emit("=" * 78)
    (_HERE.parent / "pattern_report.txt").write_text("\n".join(lines))
    print(f"\n  Report saved: {_HERE.parent / 'pattern_report.txt'}")


if __name__ == "__main__":
    main()
