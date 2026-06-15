#!/usr/bin/env python3
"""
Clean, fully-auditable research backtest — intraday trend-day continuation.

Purpose
-------
The production engine (backtest_engine_india.py) churns ~100-280 trades/month
through 45 bps round-trip costs on entries that measure as noise (~12% win rate
in BOTH directions once the circuit-breaker bug is fixed). That structure cannot
be profitable.

This script tests a fundamentally different, cost-aware structure on the SAME
yfinance 1h cache:

  * LOW frequency: at most ONE trade per symbol per day.
  * Enter only genuine trend days (stock already moved >= ENTRY_PCT from the
    day's open by the decision bar, in the direction of the move).
  * LET WINNERS RUN: hold to the 15:15 square-off; exit early only on a hard
    protective stop. No early partial, no breakeven, no time-stop that caps the
    trend day.
  * Realistic costs: COST_RT bps charged round-trip on notional.

Everything is explicit and vectorisable. No look-ahead: the entry decision at
the decision bar uses only data up to and including that bar's close; exits use
strictly later bars.

A HARD train/test split is enforced: parameters are chosen on TRAIN only, then
the identical rule is scored on the untouched TEST half. A result that is
positive on TRAIN but not TEST is overfitting and is reported as such.
"""
from __future__ import annotations
import os
import sys
import numpy as np
import pandas as pd
import data_cache
from data_yfinance import DEFAULT_SYMBOLS

# ── Cost & session constants ────────────────────────────────────────────────
# Override with COST_RT_BPS env var (e.g. COST_RT_BPS=15). Default 45 = engine value.
COST_RT = float(os.environ.get("COST_RT_BPS", "45")) / 10000.0
DAY_OPEN_T = pd.Timestamp("09:15").time()
DECISION_T = pd.Timestamp("11:15").time()   # decide after first 2 hours
SQUAREOFF_T = pd.Timestamp("15:15").time()

CAPITAL = 500_000.0


def _per_symbol_trades(df: pd.DataFrame, entry_pct: float, stop_pct: float,
                       decision_time=DECISION_T, fade: bool = False) -> pd.DataFrame:
    """Return one row per (day, side) trade for a single symbol. No look-ahead.

    fade=False : continuation — trade WITH the morning move (long up days).
    fade=True  : mean-reversion — trade AGAINST it (short up days, long down days).
    """
    rows = []
    for day, g in df.groupby(df.index.date):
        g = g.sort_index()
        times = [t.time() for t in g.index]
        if DAY_OPEN_T not in times or decision_time not in times:
            continue
        day_open = float(g.iloc[0]["open"])
        if day_open <= 0:
            continue
        # Decision bar
        dmask = [t.time() == decision_time for t in g.index]
        dbar = g[dmask]
        if dbar.empty:
            continue
        dts = dbar.index[0]
        dclose = float(dbar.iloc[0]["close"])
        r = (dclose - day_open) / day_open
        side = 0
        if r >= entry_pct:
            side = +1     # up move
        elif r <= -entry_pct:
            side = -1     # down move
        if side == 0:
            continue
        if fade:
            side = -side  # mean-reversion: trade against the morning move
        entry = dclose
        # Subsequent bars (strictly after decision) up to & incl square-off
        fut = g[g.index > dts]
        fut = fut[[t.time() <= SQUAREOFF_T for t in fut.index]]
        if fut.empty:
            continue
        stop = entry * (1 - stop_pct) if side == +1 else entry * (1 + stop_pct)
        exit_px = float(fut.iloc[-1]["close"])   # default: square-off close
        exit_reason = "EOD"
        for _, b in fut.iterrows():
            if side == +1 and float(b["low"]) <= stop:
                exit_px = stop; exit_reason = "STOP"; break
            if side == -1 and float(b["high"]) >= stop:
                exit_px = stop; exit_reason = "STOP"; break
        gross = (exit_px - entry) / entry if side == +1 else (entry - exit_px) / entry
        net = gross - COST_RT                      # round-trip cost as return
        rows.append({"day": pd.Timestamp(day), "side": side, "entry": entry,
                     "exit": exit_px, "ret_net": net, "reason": exit_reason})
    return pd.DataFrame(rows)


def backtest(data: dict, entry_pct: float, stop_pct: float, max_pos: int = 8,
             fade: bool = False):
    """Equal-weight portfolio: each day take up to max_pos strongest signals,
    allocate capital/max_pos notional each, compound daily. Returns metrics."""
    all_trades = []
    for sym, df in data.items():
        t = _per_symbol_trades(df, entry_pct, stop_pct, fade=fade)
        if not t.empty:
            t["symbol"] = sym
            all_trades.append(t)
    if not all_trades:
        return None
    trades = pd.concat(all_trades, ignore_index=True)
    trades["absmove"] = trades["ret_net"].abs()  # not used for ranking; rank by signal strength below

    # Rank within each day by |entry-day move| proxy: use net ret magnitude is
    # look-ahead, so rank by nothing — just cap to max_pos by symbol order
    # deterministically (alphabetical) to avoid selection bias.
    daily_ret = []
    eq = CAPITAL
    curve = [CAPITAL]
    per_day = trades.groupby("day")
    days_sorted = sorted(trades["day"].unique())
    n_trades = 0
    wins = 0
    for day in days_sorted:
        d = per_day.get_group(day).sort_values("symbol").head(max_pos)
        # equal weight: each trade gets 1/max_pos of capital
        w = 1.0 / max_pos
        day_pnl_frac = float((d["ret_net"] * w).sum())
        eq *= (1 + day_pnl_frac)
        curve.append(eq)
        daily_ret.append(day_pnl_frac)
        n_trades += len(d)
        wins += int((d["ret_net"] > 0).sum())
    daily_ret = np.array(daily_ret)
    total_ret = eq / CAPITAL - 1
    # annualised sharpe from daily returns
    sharpe = (daily_ret.mean() / (daily_ret.std() + 1e-12)) * np.sqrt(252) if len(daily_ret) > 1 else 0.0
    curve = np.array(curve)
    peak = np.maximum.accumulate(curve)
    max_dd = float(((peak - curve) / peak).max())
    n_days = len(days_sorted)
    months = n_days / 21.0
    monthly = (total_ret / months) if months > 0 else 0.0
    return {
        "total_ret": total_ret, "monthly": monthly, "sharpe": sharpe,
        "max_dd": max_dd, "n_trades": n_trades, "win_rate": wins / max(n_trades, 1),
        "n_days": n_days, "final_eq": eq, "all_trades": trades,
    }


def _fmt(m, label):
    if m is None:
        return f"  {label:<10}: NO TRADES"
    return (f"  {label:<10}: ret={m['total_ret']*100:+7.2f}%  monthly={m['monthly']*100:+6.2f}%  "
            f"sharpe={m['sharpe']:+5.2f}  maxDD={m['max_dd']*100:4.1f}%  "
            f"WR={m['win_rate']*100:4.1f}%  trades={m['n_trades']}")


def main():
    syms = DEFAULT_SYMBOLS[:40]
    print(f"Loading {len(syms)} symbols (1h cache) ...")
    data = data_cache.load_data(syms, interval="1h")
    print(f"Loaded {len(data)} symbols.")
    # Determine split date (mid-point of the union timeline)
    all_days = sorted({d.date() for df in data.values() for d in df.index})
    split = all_days[len(all_days) // 2]
    print(f"Train: {all_days[0]} → {split}   |   Test: {split} → {all_days[-1]}")

    def slice_data(lo, hi):
        out = {}
        for s, df in data.items():
            m = (df.index.date >= lo) & (df.index.date < hi)
            if m.sum() > 0:
                out[s] = df[m]
        return out

    train = slice_data(all_days[0], split)
    test = slice_data(split, all_days[-1] + pd.Timedelta(days=1).to_pytimedelta())

    grid_entry = [0.003, 0.005, 0.008, 0.012]
    grid_stop = [0.006, 0.010, 0.015]

    for fade in (False, True):
        mode = "MEAN-REVERSION (fade morning move)" if fade else "CONTINUATION (trade with move)"
        print("\n" + "=" * 72)
        print(f"  MODE: {mode}")
        print("=" * 72)
        print("  --- TRAIN grid (params chosen on train half only, by Sharpe) ---")
        best = None
        for ep in grid_entry:
            for sp in grid_stop:
                m = backtest(train, ep, sp, fade=fade)
                if m is None:
                    continue
                tag = f"entry={ep*100:.1f}% stop={sp*100:.1f}%"
                print(f"    {tag:<26} ret={m['total_ret']*100:+7.2f}%  sharpe={m['sharpe']:+5.2f}  "
                      f"WR={m['win_rate']*100:4.1f}%  trades={m['n_trades']}")
                if m["n_trades"] >= 50 and (best is None or m["sharpe"] > best[2]["sharpe"]):
                    best = (ep, sp, m)
        if best is None:
            print("    No viable parameter set on TRAIN.")
            continue
        ep, sp, mtr = best
        mte = backtest(test, ep, sp, fade=fade)
        mfull = backtest(data, ep, sp, fade=fade)
        print(f"\n  Locked on TRAIN: entry={ep*100:.1f}% stop={sp*100:.1f}%  →  score on untouched TEST:")
        print(_fmt(mtr, "TRAIN"))
        print(_fmt(mte, "TEST"))
        print(_fmt(mfull, "FULL"))
        if mte and mte["total_ret"] > 0 and mte["sharpe"] > 0.5:
            print("  VERDICT: TEST positive with positive Sharpe — edge survives OUT-OF-SAMPLE.")
        else:
            print("  VERDICT: TEST not robustly positive — overfitting / no real edge.")


if __name__ == "__main__":
    main()
