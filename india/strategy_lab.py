"""
strategy_lab.py — Fast, honest multi-strategy edge search.

WHY THIS EXISTS
---------------
orb_fast.py proved (twice, two independent tools) that ORB momentum on Nifty-50
large-caps loses money — not because of low win-rate, but because the 0.45%
round-trip cost is larger than the intraday moves these efficient large-caps make
(best config: 64% WR yet -9%/mo, profit factor 0.37).

This lab reuses the same clean, bug-free harness (causal indicators, realistic
trigger fills, pessimistic exit, chronological portfolio sim) to test SEVERAL
genuinely different long-only edges on the same cached data — to find out, in
seconds, whether ANY of them clears costs:

  1. ORB_MOMENTUM        — baseline breakout (known loser, kept as control)
  2. VWAP_REVERSION      — buy panic dips far below VWAP, target the snap back
  3. EMA_PULLBACK_CONT   — uptrend pullback to EMA21 that resumes (trend-following)
  4. WIDE_MOMENTUM       — ORB breakout but let winners run (rr 4-6) to beat costs
  5. RANGE_BREAK_HOLD    — break the morning range and HOLD to square-off (big move)

It also runs a COST-SENSITIVITY pass on the best strategy: the same trades priced
at 0.45 / 0.30 / 0.20 / 0.10 / 0.00 % cost, so you can see exactly how much of the
problem is costs vs. signal.

Reads only optimizer_cache.pkl OHLCV. Touches no live-trading code. Long-only
(per LONG_ONLY_NSE). Runs in seconds.

Usage:
  python3 india/strategy_lab.py
  python3 india/strategy_lab.py --max-symbols 50
"""

from __future__ import annotations

import argparse
import sys
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import orb_fast as of   # reuse harness: _atr, _simulate, _metrics, _resolve_exit, constants

REPORT_FILE = _HERE.parent / "strategy_lab_report.txt"

ENTRY_START  = dtime(9, 30)
ENTRY_CUTOFF = dtime(14, 0)
SLIP = of.SLIPPAGE


# ── Extra causal indicators ───────────────────────────────────────────────────

def _rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(close, prepend=close[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    ru = np.full_like(close, np.nan, dtype=float)
    rd = np.full_like(close, np.nan, dtype=float)
    if len(close) <= n:
        return np.full_like(close, 50.0, dtype=float)
    ru[n] = up[1:n + 1].mean()
    rd[n] = dn[1:n + 1].mean()
    for i in range(n + 1, len(close)):
        ru[i] = (ru[i - 1] * (n - 1) + up[i]) / n
        rd[i] = (rd[i - 1] * (n - 1) + dn[i]) / n
    rs = ru / np.where(rd == 0, np.nan, rd)
    rsi = 100 - 100 / (1 + rs)
    return np.nan_to_num(rsi, nan=50.0)


def _prep(df: pd.DataFrame):
    """Clean per-symbol frame with causal orb/atr/rvol (from orb_fast) + vwap/ema/rsi."""
    d = of._prep_symbol(df)
    if d is None:
        return None
    h = d["high"].to_numpy(); l = d["low"].to_numpy(); c = d["close"].to_numpy()
    v = d["volume"].to_numpy()
    tp = (h + l + c) / 3.0
    # causal intraday VWAP (resets each day)
    day = d["day"]
    pv = pd.Series(tp * v, index=d.index)
    vv = pd.Series(v, index=d.index)
    d["vwap"] = (pv.groupby(day).cumsum() / vv.groupby(day).cumsum().replace(0, np.nan)).to_numpy()
    d["ema21"] = pd.Series(c, index=d.index).ewm(span=21, adjust=False).mean().to_numpy()
    d["ema50"] = pd.Series(c, index=d.index).ewm(span=50, adjust=False).mean().to_numpy()
    d["rsi"] = _rsi(c)
    return d


# ── Strategy candidate generators (all long-only) ─────────────────────────────
# Each returns a list of dicts: {i, t_entry, day, entry, sl, tp, sl_dist}

def _mk(idx, i, entry, sl, tp, atr=None):
    sl_dist = entry - sl
    if sl_dist <= 0 or tp <= entry:
        return None
    if (tp - entry) / entry < of.COST_RT_PCT:      # target can't clear costs -> skip
        return None
    return {"i": int(i), "t_entry": idx[i], "day": idx[i].normalize(),
            "entry": float(entry), "sl": float(sl), "tp": float(tp),
            "sl_dist": float(sl_dist),
            "atr": float(atr) if atr is not None else None}  # for optional trailing stop


def s_orb(d, p):
    idx = d.index; t = idx.time
    c = d["close"].to_numpy(); h = d["high"].to_numpy(); o = d["open"].to_numpy()
    orb_h = d["orb_high"].to_numpy(); rvol = d["rvol"].to_numpy(); atr = d["atr"].to_numpy()
    prev_c = np.concatenate([[np.nan], c[:-1]])
    trig = orb_h * of.TRIGGER_BUF
    sig = ((t >= ENTRY_START) & (t < ENTRY_CUTOFF) & (c > trig) & (prev_c <= trig)
           & np.isfinite(orb_h) & np.isfinite(atr) & (atr > 0) & (rvol >= p["rv"]))
    out = []
    for i in np.flatnonzero(sig):
        entry = max(min(trig[i] * (1 + SLIP), float(h[i])), float(o[i]))
        m = _mk(idx, i, entry, entry - p["sl"] * atr[i], entry + p["rr"] * p["sl"] * atr[i],
                atr=atr[i])
        if m: out.append(m)
    return out


def s_vwap_rev(d, p):
    """Buy capitulation: price >= k*ATR below VWAP, RSI oversold, green bar. Target VWAP."""
    idx = d.index; t = idx.time
    c = d["close"].to_numpy(); o = d["open"].to_numpy(); h = d["high"].to_numpy()
    vwap = d["vwap"].to_numpy(); atr = d["atr"].to_numpy(); rsi = d["rsi"].to_numpy()
    below = (vwap - c)
    green = c > o
    sig = ((t >= ENTRY_START) & (t < ENTRY_CUTOFF) & np.isfinite(vwap) & (atr > 0)
           & (below >= p["k"] * atr) & (rsi <= p["rsi_max"]) & green)
    out = []
    for i in np.flatnonzero(sig):
        entry = float(c[i]) * (1 + SLIP)
        sl = entry - p["sl"] * atr[i]
        tp = float(vwap[i])                         # mean-reversion target = VWAP
        m = _mk(idx, i, entry, sl, tp)
        if m: out.append(m)
    return out


def s_ema_pullback(d, p):
    """Uptrend (c>ema50), pullback tags ema21 then closes back above it. Target rr*risk."""
    idx = d.index; t = idx.time
    c = d["close"].to_numpy(); o = d["open"].to_numpy(); l = d["low"].to_numpy()
    e21 = d["ema21"].to_numpy(); e50 = d["ema50"].to_numpy(); atr = d["atr"].to_numpy()
    sig = ((t >= ENTRY_START) & (t < ENTRY_CUTOFF) & (atr > 0)
           & (c > e50) & (e21 > e50)                # established uptrend
           & (l <= e21) & (c > e21) & (c > o))      # pullback to ema21 that resumes up
    out = []
    for i in np.flatnonzero(sig):
        entry = float(c[i]) * (1 + SLIP)
        sl = min(float(l[i]), entry - p["sl"] * atr[i])   # below pullback low
        tp = entry + p["rr"] * (entry - sl)
        m = _mk(idx, i, entry, sl, tp)
        if m: out.append(m)
    return out


def s_wide_mom(d, p):
    """ORB breakout but wide target (rr 4-6) to capture rare big movers over costs."""
    return s_orb(d, p)   # same entry, p carries large rr


def s_range_hold(d, p):
    """Break morning range with volume, then HOLD to square-off (capture the whole day move)."""
    idx = d.index; t = idx.time
    c = d["close"].to_numpy(); h = d["high"].to_numpy(); o = d["open"].to_numpy()
    orb_h = d["orb_high"].to_numpy(); rvol = d["rvol"].to_numpy(); atr = d["atr"].to_numpy()
    prev_c = np.concatenate([[np.nan], c[:-1]])
    trig = orb_h * of.TRIGGER_BUF
    sig = ((t >= ENTRY_START) & (t < dtime(11, 0)) & (c > trig) & (prev_c <= trig)
           & np.isfinite(orb_h) & (atr > 0) & (rvol >= p["rv"]))
    out = []
    for i in np.flatnonzero(sig):
        entry = max(min(trig[i] * (1 + SLIP), float(h[i])), float(o[i]))
        sl = entry - p["sl"] * atr[i]
        tp = entry + 20.0 * atr[i]                  # effectively "never" -> exits at squareoff
        m = _mk(idx, i, entry, sl, tp, atr=atr[i])
        if m: out.append(m)
    return out


STRATS = {
    "ORB_MOMENTUM":     (s_orb,          [{"rv": 1.5, "sl": 1.5, "rr": 2.0}]),
    "VWAP_REVERSION":   (s_vwap_rev,     [{"k": 1.0, "rsi_max": 35, "sl": 1.5},
                                          {"k": 1.5, "rsi_max": 30, "sl": 1.5},
                                          {"k": 2.0, "rsi_max": 30, "sl": 2.0}]),
    "EMA_PULLBACK_CONT":(s_ema_pullback, [{"sl": 1.0, "rr": 2.0}, {"sl": 1.5, "rr": 3.0}]),
    "WIDE_MOMENTUM":    (s_wide_mom,     [{"rv": 1.5, "sl": 1.5, "rr": 4.0},
                                          {"rv": 2.0, "sl": 1.5, "rr": 6.0}]),
    "RANGE_BREAK_HOLD": (s_range_hold,   [{"rv": 1.5, "sl": 1.5},
                                          {"rv": 2.0, "sl": 2.0}]),
    # ── SAFER variants: breakeven-stop (be=R-mult) + nearer, realistic targets ──
    # Goal: raise win rate / cut drawdown without killing the edge. Validate before use.
    "RANGE_HOLD_SAFE":  (s_range_hold,   [{"rv": 2.0, "sl": 2.0, "be": 1.0},
                                          {"rv": 2.0, "sl": 2.0, "be": 0.5}]),
    "MOM_SAFE_3R_BE":   (s_wide_mom,     [{"rv": 2.0, "sl": 1.5, "rr": 3.0, "be": 1.0},
                                          {"rv": 2.0, "sl": 1.5, "rr": 2.0, "be": 1.0}]),
    # ── LATE profit-lock: keep the 3R target AND the runners, but once a trade is
    # clearly winning, protect it. be arms at +1.5R (SL->entry); trail arms at +2R and
    # follows 1.5xATR under the high. Sub-1.5R trades are UNTOUCHED (unlike be=1.0).
    # This is the variant deployed live (LP_ARM_BE_R / LP_ARM_TRAIL_R / LP_TRAIL_ATR).
    "MOM_3R_LATELOCK":  (s_wide_mom,     [{"rv": 4.0, "sl": 1.5, "rr": 3.0,
                                           "be": 1.5, "trail_arm": 2.0, "trail_atr": 1.5}]),
    # ── THE DEPLOYED LIVE CONFIG (user-mandated early lock on top of late-lock):
    # at +0.75R the stop moves to entry+0.15R (small profit guaranteed), then the
    # late stages (BE@1.5R, trail@2R/1.5xATR, 3R target). Validate this against
    # MOM_3R_LATELOCK to PRICE what the early lock costs in monthly return.
    "MOM_3R_USERLOCK":  (s_wide_mom,     [{"rv": 4.0, "sl": 1.5, "rr": 3.0,
                                           "lock_trigger": 0.75, "lock_at": 0.15,
                                           "be": 1.5, "trail_arm": 2.0, "trail_atr": 1.5}]),
}


# ── Run one strategy+params over a prepped data dict ──────────────────────────

def run_strat(prepped, gen, params, cost=None):
    trades = []
    saved = of.COST_RT_PCT
    of.COST_RT_PCT = saved if cost is None else cost  # let _resolve_exit/_close use this cost
    try:
        for sym, d in prepped.items():
            for cand in gen(d, params):
                cand["sym"] = sym
                cand["be"] = params.get("be")             # optional breakeven-stop R-mult
                cand["trail_arm"] = params.get("trail_arm")  # R-mult to arm trailing stop
                cand["trail_atr"] = params.get("trail_atr")  # trail width in ATR
                cand["lock_trigger"] = params.get("lock_trigger")  # early-lock arm (R)
                cand["lock_at"] = params.get("lock_at")            # early-lock level (R)
                trades.append(of._resolve_exit(d, cand))
        m = of._simulate(trades)
    finally:
        of.COST_RT_PCT = saved
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-symbols", type=int, default=50)
    ap.add_argument("--cache", type=str, default=None,
                    help="alternate cache pickle (e.g. midcap_cache.pkl)")
    ap.add_argument("--cost", type=float, default=None,
                    help="override round-trip cost (e.g. 0.0018 from cost_model.py)")
    args = ap.parse_args()
    if args.cache:
        of.CACHE_FILE = Path(args.cache) if Path(args.cache).is_absolute() else _HERE / args.cache
    if args.cost is not None:
        of.COST_RT_PCT = args.cost   # research override only; engine constant unchanged

    if not of.CACHE_FILE.exists():
        print(f"ERROR: {of.CACHE_FILE} not found. Populate the cache first."); sys.exit(1)
    import pickle
    with open(of.CACHE_FILE, "rb") as f:
        raw = pickle.load(f)
    prepped = {}
    for sym, df in list(raw.items())[:args.max_symbols]:
        p = _prep(df)
        if p is not None:
            prepped[sym] = p

    n_days = len({d for p in prepped.values() for d in p.index.normalize().unique()})
    lines = []
    def emit(s=""):
        print(s); lines.append(s)

    emit("=" * 80)
    emit("  STRATEGY LAB — honest multi-edge search (long-only, cost-aware)")
    emit(f"  Symbols: {len(prepped)} | Days: {n_days} | Cost RT: {of.COST_RT_PCT*100:.2f}%"
         f" | Risk/trade: {of.RISK_PER_TRADE*100:.1f}%")
    emit("=" * 80)
    emit(f"  {'Strategy':<20} {'params':<26} {'WR':>6} {'Ret/mo':>8} {'T/mo':>6} "
         f"{'DD':>6} {'PF':>5}")
    emit("  " + "-" * 76)

    best = None
    for name, (gen, grid) in STRATS.items():
        for params in grid:
            m = run_strat(prepped, gen, params)
            pstr = ",".join(f"{k}={v}" for k, v in params.items())
            tag = ""
            # "edge" = makes money after costs with a real profit factor and enough
            # trades. WR alone is the wrong gate: hold-to-squareoff / wide-target
            # styles win <45% of the time yet profit from a few big movers.
            if m["trades"] >= 10 and m["ret"] > 0 and m["pf"] >= 1.2:
                tag = "  <-- POSITIVE"
                if best is None or m["ret"] > best[2]["ret"]:
                    best = (name, params, m, gen)
            emit(f"  {name:<20} {pstr:<26} {m['wr']:>5.1f}% {m['ret']:>+7.2f}% "
                 f"{m['tpm']:>5.1f} {m['dd']:>5.1f}% {m['pf']:>4.2f}{tag}")

    # ── Cost sensitivity on the best (or least-bad) strategy ──────────────────
    if best is None:
        # pick least-bad by return for the cost study
        allres = []
        for name, (gen, grid) in STRATS.items():
            for params in grid:
                m = run_strat(prepped, gen, params)
                if m["trades"] >= 10:
                    allres.append((name, params, m, gen))
        best = max(allres, key=lambda x: x[2]["ret"]) if allres else None

    if best:
        name, params, m, gen = best
        emit("\n" + "=" * 80)
        emit(f"  COST SENSITIVITY — {name} {params}")
        emit("  How much of the loss is costs? Same trades, different cost levels:")
        emit("  " + "-" * 60)
        emit(f"  {'Cost RT':>8} {'WR':>6} {'Ret/mo':>9} {'PF':>5}")
        for cost in [0.0045, 0.0030, 0.0020, 0.0010, 0.0000]:
            mc = run_strat(prepped, gen, params, cost=cost)
            emit(f"  {cost*100:>7.2f}% {mc['wr']:>5.1f}% {mc['ret']:>+8.2f}% {mc['pf']:>4.2f}")
        emit("\n  If return only turns positive near 0.00% cost, the signal has no real")
        emit("  edge — it's just losing slower. If it's positive at 0.20-0.30%, lower")
        emit("  brokerage / fewer-but-bigger trades could make it viable.")

    emit("\n" + "=" * 80)
    emit("  VERDICT")
    emit("=" * 80)
    pos = (best is not None and best[2]["ret"] > 0 and best[2]["pf"] >= 1.2
           and best[2]["trades"] >= 10)
    if pos:
        n, p, m, gen = best
        emit(f"  Candidate edge: {n} {p}")
        emit(f"  {of._fmt(m)}")
        # robustness: same config across walk-forward windows
        wins = of.make_windows(prepped)
        wf = [run_strat(wd, gen, p) for wk, wd in wins.items() if wk != "full"]
        if wf:
            avg = np.mean([x["ret"] for x in wf])
            robust = avg > 0 and all(x["ret"] > -2 for x in wf)
            emit(f"  Walk-forward avg Ret={avg:+.2f}%/mo -> "
                 f"{'ROBUST' if robust else 'NOT ROBUST (likely overfit)'}")
        emit("  If robust: walk-forward holds -> tiny live pilot is the next real test.")
    else:
        emit("  No long-only intraday edge on these large-caps clears the 0.45% cost wall.")
        emit("  This is a structural cost problem, not a tuning problem. Realistic paths:")
        emit("   - Trade higher-volatility names (mid-caps / F&O) where moves >> costs")
        emit("   - Cut costs (delivery vs intraday brokerage tiers) and re-test")
        emit("   - Accept intraday large-cap momentum is not viable and don't risk capital")
    emit("=" * 80)

    REPORT_FILE.write_text("\n".join(lines))
    print(f"\n  Report written: {REPORT_FILE}")


if __name__ == "__main__":
    main()
