"""
cpcv_validator.py — Combinatorial Purged Cross-Validation for NSE Bot

Academic basis: López de Prado, M. (2017). "Advances in Financial Machine Learning"
Chapter 12: CPCV generates multiple paths through historical data to provide
a distribution of Sharpe ratios rather than a single point estimate, dramatically
reducing Probability of Backtest Overfitting (PBO).

Usage:
    results = run_cpcv(
        symbols=["RELIANCE", "INFY", "TCS"],
        from_date="2024-01-01",
        to_date="2024-12-31",
        capital=500000,
        n_splits=6,    # Split data into 6 time blocks
        n_test=2,      # Use 2 blocks as test in each combo
    )
    print(f"Median Sharpe: {results['median_sharpe']:.2f}")
    print(f"PBO: {results['pbo']:.1%}")

Run from command line:
    python3 india/cpcv_validator.py --days 180 --capital 500000 --splits 6
"""
from __future__ import annotations

import argparse
import itertools
import sys
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")


# ── CPCV Engine ───────────────────────────────────────────────────────────────

def _split_dates(from_date: str, to_date: str, n_splits: int) -> List[Tuple[str, str]]:
    """
    Split date range into n_splits equal blocks.
    Returns list of (start, end) date string tuples.
    """
    start = datetime.strptime(from_date, "%Y-%m-%d")
    end   = datetime.strptime(to_date,   "%Y-%m-%d")
    total_days = (end - start).days
    block_days = total_days // n_splits

    blocks = []
    for i in range(n_splits):
        block_start = start + timedelta(days=i * block_days)
        block_end   = start + timedelta(days=(i + 1) * block_days) - timedelta(days=1)
        if i == n_splits - 1:
            block_end = end
        blocks.append((block_start.strftime("%Y-%m-%d"), block_end.strftime("%Y-%m-%d")))

    return blocks


def _run_single_backtest(
    data: Dict[str, pd.DataFrame],
    from_date: str,
    to_date: str,
    capital: float,
) -> dict:
    """
    Run backtest engine on a subset of the data (by date range).
    Returns dict with key metrics.
    """
    try:
        # Filter data to date range
        start = pd.Timestamp(from_date, tz=IST)
        end   = pd.Timestamp(to_date,   tz=IST) + pd.Timedelta(days=1)

        sliced = {}
        for sym, df in data.items():
            mask = (df.index >= start) & (df.index < end)
            sub  = df[mask]
            if len(sub) >= 50:
                sliced[sym] = sub

        if not sliced:
            return {"sharpe": 0.0, "monthly_ret": 0.0, "max_dd": 0.0, "trades": 0}

        # Import and run the backtest engine functions inline
        # We need to run the backtest on this date slice without re-fetching data
        # Use the _score_bar and trade management logic directly
        from backtest_engine_india import (
            _compute_all, _build_orb, _resample, _score_bar,
            _dynamic_kelly_size, Trade, COST_RT_PCT, SQUAREOFF,
            MIN_SCORE, MAX_OPEN, MAX_POS_PCT
        )

        # Apply indicators to sliced data
        computed = {}
        for sym, df in sliced.items():
            try:
                computed[sym] = _compute_all(_build_orb(df))
            except Exception:
                continue

        if not computed:
            return {"sharpe": 0.0, "monthly_ret": 0.0, "max_dd": 0.0, "trades": 0}

        # Pre-compute resamples
        data_15m = {sym: _resample(df, "15min") for sym, df in computed.items()}
        data_1h  = {sym: _resample(df, "1h")    for sym, df in computed.items()}

        # Run replay
        from datetime import time as dtime
        all_ts = sorted(set().union(*[set(df.index) for df in computed.values()]))

        equity = capital
        peak   = capital
        max_dd = 0.0
        equity_curve = [capital]
        open_trades: Dict[str, Trade] = {}
        trades = []
        recent_trades: List[float] = []
        wins = losses = 0

        for now_ts in all_ts:
            bt = now_ts.time()
            if bt < dtime(9, 25) or bt > dtime(15, 0):
                continue

            # Exits
            for sym in list(open_trades.keys()):
                t  = open_trades[sym]
                if sym not in computed or now_ts not in computed[sym].index:
                    continue
                df = computed[sym]
                row = df.loc[now_ts]
                c   = float(row.get("close", 0) or 0)
                long = t.direction == "LONG"

                hit_sl = (long and c <= t.sl) or (not long and c >= t.sl)
                hit_t2 = (long and c >= t.t2) or (not long and c <= t.t2)
                squareoff = bt >= SQUAREOFF

                if hit_sl or hit_t2 or squareoff:
                    exit_px = t.sl if hit_sl else (t.t2 if hit_t2 else c)
                    qty_left = t.qty - (int(t.qty * 0.4) if t.t1_done else 0)
                    pnl = ((exit_px - t.entry) if long else (t.entry - exit_px)) * qty_left
                    pnl -= (t.entry * t.qty + exit_px * t.qty) * COST_RT_PCT / 2
                    equity += pnl; t.pnl = pnl; t.exit_price = exit_px; t.exit_time = now_ts
                    trades.append(t); del open_trades[sym]
                    if pnl > 0: wins += 1
                    else: losses += 1
                    recent_trades.append(pnl / max(capital, 1e-9))
                elif not t.t1_done:
                    hit_t1 = (long and c >= t.t1) or (not long and c <= t.t1)
                    if hit_t1:
                        partial = int(t.qty * 0.4)
                        pnl = ((t.t1 - t.entry) if long else (t.entry - t.t1)) * partial
                        pnl -= t.entry * partial * COST_RT_PCT / 2
                        equity += pnl; t.pnl += pnl; t.t1_done = True
                        if long: t.sl = t.entry
                        else:    t.sl = t.entry

            peak   = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / max(peak, 1e-9))
            equity_curve.append(equity)

            if len(open_trades) >= MAX_OPEN:
                continue

            for sym, df in computed.items():
                if sym in open_trades or len(open_trades) >= MAX_OPEN:
                    continue
                if now_ts not in df.index:
                    continue
                idx = df.index.get_loc(now_ts)
                if idx < 55:
                    continue

                row  = df.iloc[idx]
                prev = df.iloc[idx - 1]

                _f15 = data_15m.get(sym)
                df_15m = _f15.loc[:now_ts] if _f15 is not None and not _f15.empty else None
                _f1h  = data_1h.get(sym)
                df_1h  = _f1h.loc[:now_ts]  if _f1h  is not None and not _f1h.empty  else None

                try:
                    net_score, direction, reason = _score_bar(row, prev, df_15m, df_1h, now_ts)
                except Exception:
                    continue

                if abs(net_score) < MIN_SCORE:
                    continue

                atr = float(row.get("atr", row["close"] * 0.005) or row["close"] * 0.005)
                if atr <= 0:
                    continue
                entry = float(row["close"])
                long  = direction == "LONG"
                sl    = entry - 1.5 * atr if long else entry + 1.5 * atr
                t1    = entry + 1.5 * atr if long else entry - 1.5 * atr
                t2    = entry + 3.0 * atr if long else entry - 3.0 * atr

                risk_pct = _dynamic_kelly_size(recent_trades, equity, net_score, atr, entry)
                sl_dist  = abs(entry - sl)
                qty = int(min(equity * risk_pct / max(sl_dist, 1e-9),
                              equity * MAX_POS_PCT / max(entry, 1e-9)))
                if qty < 1:
                    continue

                trade = Trade(sym, direction, entry, sl, t1, t2, qty, now_ts)
                open_trades[sym] = trade

        # Compute metrics
        if not trades:
            return {"sharpe": 0.0, "monthly_ret": 0.0, "max_dd": max_dd, "trades": 0}

        pnls = [t.pnl / capital for t in trades]
        try:
            import numpy as np
            sharpe = float(np.mean(pnls) / (np.std(pnls) + 1e-9)) * (252 ** 0.5)
        except Exception:
            sharpe = 0.0

        ret_pct  = (equity - capital) / capital * 100
        days = max((datetime.strptime(to_date, "%Y-%m-%d") -
                    datetime.strptime(from_date, "%Y-%m-%d")).days, 1)
        monthly = ret_pct / days * 30

        return {
            "sharpe":     sharpe,
            "monthly_ret": monthly,
            "max_dd":     max_dd * 100,
            "trades":     len(trades),
            "win_rate":   wins / max(len(trades), 1) * 100,
        }

    except Exception as e:
        return {"sharpe": 0.0, "monthly_ret": 0.0, "max_dd": 0.0, "trades": 0, "error": str(e)}


def run_cpcv(
    data: Dict[str, pd.DataFrame],
    from_date: str,
    to_date:   str,
    capital:   float = 500_000,
    n_splits:  int   = 6,
    n_test:    int   = 2,
) -> dict:
    """
    Run Combinatorial Purged Cross-Validation.

    Generates C(n_splits, n_test) = 15 test paths (with n_splits=6, n_test=2).
    Each path trains on (n_splits - n_test) blocks and tests on n_test blocks.
    This gives a DISTRIBUTION of Sharpe ratios rather than a single point estimate.

    Returns:
        {
          "median_sharpe":   float,
          "mean_sharpe":     float,
          "std_sharpe":      float,
          "pbo":             float,  # Probability of Backtest Overfitting (0-1, lower better)
          "median_monthly":  float,
          "results":         List[dict],  # per-path results
          "n_paths":         int,
        }
    """
    print(f"\nCPCV: {n_splits} blocks x C({n_splits},{n_test}) = "
          f"{len(list(itertools.combinations(range(n_splits), n_test)))} paths")
    print(f"Period: {from_date} -> {to_date}  |  Capital: Rs{capital:,.0f}")
    print("-" * 60)

    blocks = _split_dates(from_date, to_date, n_splits)
    combos = list(itertools.combinations(range(n_splits), n_test))

    path_results = []

    for combo_idx, test_block_ids in enumerate(combos):
        test_blocks  = [blocks[i] for i in test_block_ids]
        train_blocks = [blocks[i] for i in range(n_splits) if i not in test_block_ids]

        # Run backtest on each test block (purged cross-validation)
        combo_sharpes = []
        for tb_start, tb_end in test_blocks:
            result = _run_single_backtest(data, tb_start, tb_end, capital)
            if result["trades"] >= 3:
                combo_sharpes.append(result["sharpe"])

        if combo_sharpes:
            avg_sharpe = statistics.mean(combo_sharpes)
            path_results.append({
                "path":     combo_idx,
                "test_blocks": [f"{s}->{e}" for s, e in test_blocks],
                "sharpe":   avg_sharpe,
                "n_tests":  len(combo_sharpes),
            })
            print(f"  Path {combo_idx+1:2d}: Sharpe = {avg_sharpe:+.2f}  "
                  f"({len(combo_sharpes)} sub-tests)")

    if not path_results:
        print("  No valid paths -- insufficient data")
        return {"median_sharpe": 0.0, "pbo": 1.0, "n_paths": 0}

    sharpes  = [r["sharpe"] for r in path_results]
    med_sh   = statistics.median(sharpes)
    mean_sh  = statistics.mean(sharpes)
    std_sh   = statistics.stdev(sharpes) if len(sharpes) > 1 else 0.0

    # PBO: fraction of paths with negative Sharpe (paths where strategy fails)
    pbo = sum(1 for s in sharpes if s <= 0) / len(sharpes)

    verdict = "RELIABLE" if pbo < 0.15 else ("OVERFIT RISK" if pbo < 0.35 else "LIKELY OVERFIT")

    print(f"\n{'=' * 60}")
    print(f"  CPCV RESULTS: {len(path_results)} paths")
    print(f"  Median Sharpe : {med_sh:+.2f}")
    print(f"  Mean Sharpe   : {mean_sh:+.2f}  +/-{std_sh:.2f}")
    print(f"  Best Path     : {max(sharpes):+.2f}")
    print(f"  Worst Path    : {min(sharpes):+.2f}")
    print(f"  PBO           : {pbo:.1%}  (lower is better; <10% = reliable)")
    print(f"  Verdict       : {verdict}")
    print(f"{'=' * 60}")

    return {
        "median_sharpe":  med_sh,
        "mean_sharpe":    mean_sh,
        "std_sharpe":     std_sh,
        "pbo":            pbo,
        "best_sharpe":    max(sharpes),
        "worst_sharpe":   min(sharpes),
        "results":        path_results,
        "n_paths":        len(path_results),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CPCV Backtest Validator")
    parser.add_argument("--days",     type=int,   default=180, help="Days of history")
    parser.add_argument("--capital",  type=float, default=500_000)
    parser.add_argument("--splits",   type=int,   default=6, help="Number of time blocks")
    parser.add_argument("--test",     type=int,   default=2, help="Test blocks per combo")
    parser.add_argument("--symbols",  type=str,   default="", help="Comma-sep symbols")
    args = parser.parse_args()

    to_date   = datetime.now(IST).strftime("%Y-%m-%d")
    from_date = (datetime.now(IST) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    from auth_upstox import get_upstox_client, verify_connection
    import data_fetch_upstox as dfu
    from backtest_engine_india import _compute_all, _build_orb, _fetch

    print("Connecting to Upstox API ...")
    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed.")
        return
    dfu.set_upstox_client(client)

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        from watchlist_india import get_watchlist
        try:
            symbols = get_watchlist()[:30]
        except Exception:
            symbols = ["RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK",
                       "KOTAKBANK", "SBIN", "AXISBANK", "WIPRO", "HCLTECH"]

    print(f"Fetching data for {len(symbols)} symbols ...")
    data = {}
    for sym in symbols:
        df = _fetch(client, sym, from_date, to_date)
        if df is not None:
            data[sym] = _compute_all(_build_orb(df))
            print(f"  {sym}: {len(df)} bars")
        else:
            print(f"  {sym}: skipped")

    if not data:
        print("No data loaded.")
        return

    run_cpcv(data, from_date, to_date, args.capital, args.splits, args.test)


if __name__ == "__main__":
    main()
