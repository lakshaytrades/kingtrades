"""
orb_fast.py — Clean, fast, *honest* ORB_BULL_CONFIRM backtester.

WHY THIS EXISTS
---------------
The production engine (backtest_engine_india.py) pinned win-rate at ~33% across
640 configs and took ~7 min PER backtest. Three independent code reviews found
the cause was NOT "no edge" alone but measurement bugs that bias the result:

  1. RVOL look-ahead  : rvol divided each bar's volume by the WHOLE day's average
                        (including future bars) -> the rvol>=X gate wrongly rejected
                        the early-morning ORB breakouts the live bot actually takes.
  2. Bar-close entry  : entry = close of the breakout candle -> you buy the top of
                        the move; the live bot fills near the breakout level intrabar.
  3. Tangled exit     : T1 and T2 both forced to 4.5R, partials capping winners while
                        losses ran full -1R; documented 2:1 R:R was not what ran.
  4. ~1000x slowness  : per-timestep full-DataFrame rescans.

This module fixes all four with a self-contained, vectorized implementation:
  * CAUSAL rvol (only bars up to & including the current bar, within the day)
  * realistic entry fill at the breakout TRIGGER level + explicit slippage
  * one simple, trustworthy exit: fixed R-multiple SL/TP, pessimistic same-bar
    tie-break (SL wins ties), hard square-off at 15:25 IST
  * pure-numpy per-symbol scan + chronological portfolio sim -> runs in seconds

It does NOT touch backtest_engine_india.py or any live-trading code. It only reads
the cached OHLCV the optimizer already fetched. Read the result honestly: if no
config holds up across walk-forward windows, the strategy has no edge as built and
the only remaining real test is a tiny live pilot.

Usage:
  python3 india/orb_fast.py                      # uses optimizer_cache.pkl
  python3 india/orb_fast.py --rr 2.0 --rv 1.5    # single config, verbose
  python3 india/orb_fast.py --grid               # full grid + walk-forward (default)
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
CACHE_FILE = _HERE / "optimizer_cache.pkl"
REPORT_FILE = _HERE.parent / "orb_fast_report.txt"

# ── Fixed, audited constants (match production where it matters) ───────────────
COST_RT_PCT   = 0.0045          # round-trip costs (DO NOT CHANGE — per CLAUDE.md)
SQUAREOFF     = dtime(15, 25)   # hard intraday square-off
ORB_START     = dtime(9, 15)
ORB_END       = dtime(9, 30)
ENTRY_START   = dtime(9, 30)    # no entries until ORB window closes
ENTRY_CUTOFF  = dtime(13, 0)    # afternoon breakouts historically weak
SLIPPAGE      = 0.0005          # 5 bps entry slippage past the trigger
TRIGGER_BUF   = 1.0005          # breakout confirmed 0.05% above ORB high
ATR_LEN       = 14
RISK_PER_TRADE = 0.007          # 0.7% equity risked per trade
MAX_OPEN      = 5               # max concurrent positions
START_CAPITAL = 500_000.0


# ── Causal indicators (no look-ahead) ─────────────────────────────────────────

def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = ATR_LEN) -> np.ndarray:
    """Wilder ATR, strictly causal."""
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    atr = np.full_like(tr, np.nan, dtype=float)
    if len(tr) < n:
        return atr
    atr[n - 1] = tr[:n].mean()
    alpha = 1.0 / n
    for i in range(n, len(tr)):
        atr[i] = (atr[i - 1] * (n - 1) + tr[i]) * alpha
    return atr


def _prep_symbol(df: pd.DataFrame) -> pd.DataFrame | None:
    """Return a clean per-symbol frame with causal orb_high, rvol_causal, atr.

    Ignores any precomputed (and buggy) indicator columns in the cache.
    """
    need = {"open", "high", "low", "close", "volume"}
    if not need.issubset(df.columns) or len(df) < ATR_LEN + 5:
        return None
    d = df[["open", "high", "low", "close", "volume"]].copy()
    if d.index.tz is None:
        # cache is IST-naive in rare cases; treat as IST
        d.index = d.index.tz_localize("Asia/Kolkata")
    d = d.between_time("09:15", "15:30")
    if len(d) < ATR_LEN + 5:
        return None

    day = d.index.normalize()
    d["day"] = day

    # ORB high/low from 9:15-9:30 ONLY (no look-ahead — known by 9:30)
    in_orb = (d.index.time >= ORB_START) & (d.index.time <= ORB_END)
    orb_h = d[in_orb].groupby(d[in_orb]["day"])["high"].max()
    orb_l = d[in_orb].groupby(d[in_orb]["day"])["low"].min()
    d["orb_high"] = d["day"].map(orb_h)
    d["orb_low"] = d["day"].map(orb_l)

    # CAUSAL rvol: volume / mean(volume of PRIOR bars that same day).
    # Uses only bars strictly before the current one -> no future leakage.
    g = d.groupby("day")["volume"]
    prior_mean = g.apply(lambda s: s.expanding().mean().shift(1)).reset_index(level=0, drop=True)
    d["rvol"] = d["volume"] / prior_mean.replace(0, np.nan)
    d["rvol"] = d["rvol"].fillna(0.0)

    d["atr"] = _atr(d["high"].to_numpy(), d["low"].to_numpy(), d["close"].to_numpy())
    return d


# ── Per-symbol candidate generation (vectorized) ──────────────────────────────

def _candidates(d: pd.DataFrame, rv_thresh: float, sl_mult: float, rr: float) -> list[dict]:
    """All fresh-ORB long breakouts for one symbol with realistic fills + SL/TP."""
    idx = d.index
    t = idx.time
    c = d["close"].to_numpy()
    h = d["high"].to_numpy()
    o = d["open"].to_numpy()
    orb_h = d["orb_high"].to_numpy()
    rvol = d["rvol"].to_numpy()
    atr = d["atr"].to_numpy()

    prev_c = np.concatenate([[np.nan], c[:-1]])
    trig = orb_h * TRIGGER_BUF

    in_window = (t >= ENTRY_START) & (t < ENTRY_CUTOFF)
    fresh = (c > trig) & (prev_c <= trig)            # first close above the ORB high
    has_data = np.isfinite(orb_h) & np.isfinite(atr) & (atr > 0)
    vol_ok = rvol >= rv_thresh
    sig = in_window & fresh & has_data & vol_ok

    out = []
    for i in np.flatnonzero(sig):
        # Realistic fill: you get in as price crosses the trigger, plus slippage,
        # but never better than the bar could actually offer (cap at bar high).
        entry = min(trig[i] * (1 + SLIPPAGE), float(h[i]))
        entry = max(entry, float(o[i]))             # can't fill below the open on a gap-up
        sl_dist = sl_mult * float(atr[i])
        if sl_dist <= 0:
            continue
        sl = entry - sl_dist
        tp = entry + rr * sl_dist
        # skip trades whose target can't clear round-trip costs
        if (tp - entry) / entry < COST_RT_PCT:
            continue
        out.append({
            "sym": None, "i": int(i), "t_entry": idx[i], "day": idx[i].normalize(),
            "entry": entry, "sl": sl, "tp": tp, "sl_dist": sl_dist,
            "atr": float(atr[i]),      # ATR at entry (used for the optional trailing stop)
        })
    return out


def _resolve_exit(d: pd.DataFrame, cand: dict) -> dict:
    """Walk forward within the same day: SL (pessimistic tie) -> TP -> square-off.

    Optional profit-protection (all armed for FUTURE bars only — no same-bar lookahead):
      * cand["be"]        = R-multiple at which to move the stop to breakeven (entry).
                            e.g. be=1.0 -> once price gains 1x the initial risk the stop
                            becomes the entry, so the winner can't turn back into a loser.
      * cand["trail_arm"] = R-multiple profit at which a TRAILING stop switches on.
      * cand["trail_atr"] = trail distance (in ATR) below the running high-water mark
                            once armed. The stop only ever ratchets UP, never down.

    Design note: this strategy's edge comes from runners reaching the ~3R target, so an
    early breakeven (be=1.0) HURTS it (kills mid-trades that would have run). Arm any
    protection LATE (be≈1.5R, trail≈2R) so sub-1.5R trades behave exactly as before and
    only clearly-winning trades get their profit locked."""
    i = cand["i"]
    idx = d.index
    h = d["high"].to_numpy()
    lo = d["low"].to_numpy()
    c = d["close"].to_numpy()
    day = cand["day"]
    sl, tp = cand["sl"], cand["tp"]
    entry, risk = cand["entry"], cand["sl_dist"]
    atr = cand.get("atr")                            # ATR at entry (for the trail width)
    be_r = cand.get("be")
    be_trigger = entry + be_r * risk if be_r else None
    be_armed = False
    trail_arm_r = cand.get("trail_arm")
    trail_atr = cand.get("trail_atr")
    trail_trigger = (entry + trail_arm_r * risk
                     if (trail_arm_r and trail_atr and atr) else None)
    trail_armed = False
    hi_water = entry                                 # running high-water mark

    j = i + 1
    n = len(d)
    while j < n and idx[j].normalize() == day:
        # hard square-off
        if idx[j].time() >= SQUAREOFF:
            exit_px = float(c[j]); return _close(cand, idx[j], exit_px, "SQUAREOFF")
        if lo[j] <= sl:                              # SL wins same-bar ties (pessimistic)
            why = "TRAIL" if trail_armed else ("BE" if be_armed else "SL")
            return _close(cand, idx[j], sl, why)
        if h[j] >= tp:
            return _close(cand, idx[j], tp, "TP")
        # arm breakeven for the NEXT bar once this bar reaches the trigger (no lookahead)
        if be_trigger is not None and not be_armed and h[j] >= be_trigger:
            sl = max(sl, entry); be_armed = True
        # arm the trailing stop once this bar reaches the trail trigger, then ratchet it
        # up as the high-water mark rises (only tightens — never loosens the stop)
        if trail_trigger is not None:
            hi_water = max(hi_water, float(h[j]))
            if not trail_armed and h[j] >= trail_trigger:
                trail_armed = True
            if trail_armed:
                sl = max(sl, hi_water - trail_atr * atr)
        j += 1
    # ran out of same-day bars -> exit at last available close of the day
    last = j - 1 if j - 1 >= 0 else i
    return _close(cand, idx[last], float(c[last]), "EOD")


def _close(cand: dict, t_exit, exit_px: float, why: str) -> dict:
    gross = (exit_px - cand["entry"]) / cand["entry"]
    net = gross - COST_RT_PCT
    return {**cand, "t_exit": t_exit, "exit": exit_px,
            "gross_ret": gross, "net_ret": net, "why": why}


# ── Portfolio simulation (chronological, concurrency-capped) ───────────────────

def _simulate(all_trades: list[dict]) -> dict:
    """Compound equity through time with MAX_OPEN concurrency and per-symbol locks."""
    trades = sorted(all_trades, key=lambda x: x["t_entry"])
    equity = START_CAPITAL
    peak = equity
    max_dd = 0.0
    open_pos: list[dict] = []         # currently held, with t_exit/pnl
    taken: list[dict] = []
    rets: list[float] = []
    eq_curve = [equity]

    for tr in trades:
        # close anything that exited at/before this entry time
        still_open = []
        for p in open_pos:
            if p["t_exit"] <= tr["t_entry"]:
                equity += p["pnl"]
                rets.append(p["pnl"] / max(START_CAPITAL, 1))
                eq_curve.append(equity)
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
            else:
                still_open.append(p)
        open_pos = still_open

        # capacity / per-symbol lock
        if len(open_pos) >= MAX_OPEN:
            continue
        if any(p["sym"] == tr["sym"] for p in open_pos):
            continue

        qty = int((equity * RISK_PER_TRADE) / tr["sl_dist"])
        qty = min(qty, int(equity * 5.0 / max(tr["entry"], 1)))   # 5x leverage cap
        if qty < 1:
            continue
        pnl = tr["net_ret"] * tr["entry"] * qty
        p = {**tr, "qty": qty, "pnl": pnl}
        open_pos.append(p)
        taken.append(p)

    for p in open_pos:                # flush remaining
        equity += p["pnl"]
        rets.append(p["pnl"] / max(START_CAPITAL, 1))
        eq_curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

    return _metrics(taken, equity, max_dd, rets)


def _metrics(taken: list[dict], equity: float, max_dd: float, rets: list[float]) -> dict:
    n = len(taken)
    if n == 0:
        return {"trades": 0, "wr": 0.0, "ret": 0.0, "dd": 0.0, "sharpe": 0.0,
                "pf": 0.0, "tpm": 0.0, "months": 0.0}
    wins = [t for t in taken if t["pnl"] > 0]
    losses = [t for t in taken if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses)) or 1e-9
    days = (max(t["t_exit"] for t in taken) - min(t["t_entry"] for t in taken)).days
    months = max(days / 30.44, 0.1)
    total_ret = (equity - START_CAPITAL) / START_CAPITAL
    sharpe = 0.0
    if len(rets) > 2 and np.std(rets) > 0:
        sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(252))
    return {
        "trades": n,
        "wr": len(wins) / n * 100,
        "ret": (total_ret / months) * 100,         # %/month
        "dd": max_dd * 100,
        "sharpe": sharpe,
        "pf": gw / gl,
        "tpm": n / months,
        "months": months,
    }


# ── One full backtest over a data dict ────────────────────────────────────────

def run(prepped: dict, rv_thresh: float, sl_mult: float, rr: float) -> dict:
    all_trades = []
    for sym, d in prepped.items():
        for cand in _candidates(d, rv_thresh, sl_mult, rr):
            cand["sym"] = sym
            all_trades.append(_resolve_exit(d, cand))
    return _simulate(all_trades)


# ── Data loading + windows ────────────────────────────────────────────────────

def load_prepped(max_symbols: int) -> dict:
    if not CACHE_FILE.exists():
        print(f"ERROR: {CACHE_FILE} not found. Run the optimizer/orb_report once to "
              f"populate the cache, or copy it here.")
        sys.exit(1)
    with open(CACHE_FILE, "rb") as f:
        raw = pickle.load(f)
    prepped = {}
    for sym, df in list(raw.items())[:max_symbols]:
        p = _prep_symbol(df)
        if p is not None:
            prepped[sym] = p
    return prepped


def make_windows(prepped: dict) -> dict:
    """Full period + 3 rolling 60%-width walk-forward windows."""
    all_days = sorted({d for p in prepped.values() for d in p.index.normalize().unique()})
    res = {"full": prepped}
    total = len(all_days)
    if total < 8:
        return res
    width = max(int(total * 0.6), 8)
    step = max(int(total * 0.2), 3)
    for k, s in enumerate(range(0, total - width + 1, step)[:3]):
        lo, hi = all_days[s], all_days[min(s + width, total - 1)]
        win = {}
        for sym, p in prepped.items():
            sl = p[(p.index.normalize() >= lo) & (p.index.normalize() <= hi)]
            if len(sl) >= ATR_LEN + 5:
                win[sym] = sl
        if win:
            res[f"wf{k+1}"] = win
    return res


# ── Reporting ─────────────────────────────────────────────────────────────────

def _fmt(m: dict) -> str:
    return (f"T={m['trades']:>3}  WR={m['wr']:>5.1f}%  Ret={m['ret']:>+6.2f}%/mo  "
            f"T/mo={m['tpm']:>4.1f}  DD={m['dd']:>4.1f}%  Sharpe={m['sharpe']:>5.2f}  "
            f"PF={m['pf']:>4.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-symbols", type=int, default=50)
    ap.add_argument("--rv", type=float, default=None, help="single rvol threshold")
    ap.add_argument("--sl", type=float, default=1.5, help="ATR SL multiplier")
    ap.add_argument("--rr", type=float, default=None, help="single reward:risk")
    ap.add_argument("--grid", action="store_true", help="full grid + walk-forward (default)")
    ap.add_argument("--cache", type=str, default=None,
                    help="alternate cache pickle (e.g. midcap_cache.pkl)")
    args = ap.parse_args()
    if args.cache:
        global CACHE_FILE
        CACHE_FILE = Path(args.cache) if Path(args.cache).is_absolute() else _HERE / args.cache

    print("=" * 78)
    print("  ORB_BULL_CONFIRM — clean fast honest backtest")
    print("  causal rvol | trigger-level fills | simple SL/TP exit | vectorized")
    print("=" * 78)
    prepped = load_prepped(args.max_symbols)
    n_days = len({d for p in prepped.values() for d in p.index.normalize().unique()})
    print(f"  Symbols: {len(prepped)} | Trading days: {n_days} | "
          f"Cost RT: {COST_RT_PCT*100:.2f}% | Risk/trade: {RISK_PER_TRADE*100:.1f}%")

    lines = []
    def emit(s=""):
        print(s); lines.append(s)

    # Single-config mode
    if args.rv is not None and args.rr is not None and not args.grid:
        m = run(prepped, args.rv, args.sl, args.rr)
        emit(f"\n  rv={args.rv} sl={args.sl} rr={args.rr}:  {_fmt(m)}")
        REPORT_FILE.write_text("\n".join(lines))
        return

    # Grid mode (default)
    rv_grid = [1.0, 1.2, 1.3, 1.5, 1.8, 2.0, 2.5, 3.0]
    rr_grid = [1.5, 2.0, 2.5, 3.0]
    sl = args.sl

    emit("\n  GRID (full period) — ranked by monthly return")
    emit("  " + "-" * 74)
    results = []
    for rv in rv_grid:
        for rr in rr_grid:
            m = run(prepped, rv, sl, rr)
            results.append((rv, rr, m))
    results.sort(key=lambda x: (x[2]["ret"], x[2]["wr"]), reverse=True)
    for rv, rr, m in results:
        emit(f"  rv={rv:<4} rr={rr:<4} | {_fmt(m)}")

    # RVOL axis summary (best rr at each rvol)
    emit("\n  RVOL AXIS — best result at each rvol threshold")
    emit("  " + "-" * 74)
    for rv in rv_grid:
        grp = [r for r in results if r[0] == rv and r[2]["trades"] >= 5]
        if not grp:
            emit(f"  rv={rv:<4} | (too few trades)"); continue
        best = max(grp, key=lambda x: x[2]["ret"])
        verdict = ("PROFITABLE" if best[2]["wr"] >= 50 and best[2]["ret"] > 0
                   else "edge?" if best[2]["ret"] > 0 else "losing")
        emit(f"  rv={rv:<4} | rr={best[1]:<4} {_fmt(best[2])}  [{verdict}]")

    # Walk-forward on the best full-period config
    best_rv, best_rr, best_m = results[0]
    emit(f"\n  BEST FULL-PERIOD CONFIG: rv={best_rv} sl={sl} rr={best_rr}")
    emit(f"    {_fmt(best_m)}")
    emit("\n  WALK-FORWARD — same config on rolling windows (robustness check)")
    emit("  " + "-" * 74)
    windows = make_windows(prepped)
    wf = []
    for wk, wd in windows.items():
        if wk == "full":
            continue
        m = run(wd, best_rv, sl, best_rr)
        wf.append(m)
        emit(f"  {wk:<5} | {_fmt(m)}")
    if wf:
        avg_wr = np.mean([m["wr"] for m in wf])
        avg_ret = np.mean([m["ret"] for m in wf])
        robust = avg_wr >= 50 and avg_ret > 0 and all(m["ret"] > -1 for m in wf)
        emit(f"\n  WF avg WR={avg_wr:.1f}%  avg Ret={avg_ret:+.2f}%/mo  "
             f"-> {'ROBUST' if robust else 'NOT ROBUST'}")

    # Honest verdict
    emit("\n" + "=" * 78)
    emit("  VERDICT")
    emit("=" * 78)
    if best_m["wr"] >= 50 and best_m["ret"] >= 4 and best_m["dd"] < 8:
        emit("  Edge found in clean backtest. Deploy fixed logic with SMALL capital and")
        emit(f"  monitor manually. Config: rv={best_rv} sl={sl} rr={best_rr}")
    elif best_m["ret"] > 0:
        emit("  Marginal positive edge after fixing the bugs — better than the old 33%/-39%,")
        emit("  but below the 4%/mo target. Do NOT scale. A ₹5k live pilot is the real test.")
    else:
        emit("  Still no edge after fixing rvol look-ahead + realistic fills + clean exit.")
        emit("  This ORB-momentum signal does not have a tradeable edge on this universe as")
        emit("  built. Parameter tuning will not fix that. Options: (a) rethink the signal,")
        emit("  (b) run a tiny ₹5k live pilot to test the 'real fills are better' theory.")
    emit("=" * 78)

    REPORT_FILE.write_text("\n".join(lines))
    print(f"\n  Report written: {REPORT_FILE}")


if __name__ == "__main__":
    main()
