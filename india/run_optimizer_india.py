"""
run_optimizer_india.py — Self-Running Parameter Optimizer

Usage:
  python3 india/run_optimizer_india.py --source cache
  python3 india/run_optimizer_india.py --source upstox
  python3 india/run_optimizer_india.py --source synthetic   # quick smoke-test only

Target:  WR >= 75%  AND  trades >= 25/month  AND  monthly return >= 4%
When target is met, best parameters are auto-written to backtest_engine_india.py.

Runtime: ~2–10 min depending on data source and universe size.
No manual intervention needed.
"""
import argparse
import contextlib
import io
import re
import sys
import time
from itertools import product
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

# ── Targets ──────────────────────────────────────────────────────────────────
TARGET_WR        = 0.75    # 75% win rate
TARGET_TRADES_PM = 25      # minimum trades per month
TARGET_MONTHLY   = 4.0     # minimum monthly return %

# ── Parameter search grid (coarse → fine) ────────────────────────────────────
# Listed in order of expected impact on WR.
# Optimizer stops as soon as it finds a combo meeting ALL 3 targets.
GRID = {
    "MIN_SCORE":          [18.0, 19.0, 20.0, 21.0, 22.0],
    "MIN_QUAL_COUNT":     [2, 3],
    "ENTRY_RSI_LONG_MAX": [72, 68, 65],
    "ENTRY_RSI_LONG_MIN": [42, 45],
    "ENTRY_RVOL_MIN":     [1.3, 1.4, 1.5],
    "BREADTH_BULL_HARD":  [0.52, 0.55, 0.58],
    "BREADTH_BULL_SOFT":  [0.60, 0.65],
}

# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_report(text: str) -> Optional[Dict]:
    """Extract key metrics from backtest stdout."""
    metrics = {}
    try:
        # Total trades
        m = re.search(r"Total trades\s*:\s*(\d+)", text)
        if m:
            metrics["trades"] = int(m.group(1))
        # Win rate
        m = re.search(r"Win rate\s*:\s*([\d.]+)%", text)
        if m:
            metrics["wr"] = float(m.group(1)) / 100.0
        # Monthly return (first number after Monthly return:)
        m = re.search(r"Monthly return\s*:\s*([-\d.]+)%", text)
        if m:
            metrics["monthly_pct"] = float(m.group(1))
        # Sharpe
        m = re.search(r"Sharpe ratio\s*:\s*([-\d.]+)", text)
        if m:
            metrics["sharpe"] = float(m.group(1))
        # Max drawdown
        m = re.search(r"Max drawdown\s*:\s*([-\d.]+)%", text)
        if m:
            metrics["max_dd"] = float(m.group(1))
        # Days in period
        m = re.search(r"Period\s*:.*?(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})", text)
        if m:
            from datetime import datetime
            d1 = datetime.strptime(m.group(1), "%Y-%m-%d")
            d2 = datetime.strptime(m.group(2), "%Y-%m-%d")
            days = max((d2 - d1).days, 1)
            metrics["months"] = days / 30.44
        if "trades" in metrics and "months" in metrics and metrics["months"] > 0:
            metrics["trades_pm"] = metrics["trades"] / metrics["months"]
        else:
            metrics["trades_pm"] = metrics.get("trades", 0)
    except Exception as e:
        print(f"  [parse error] {e}", flush=True)
    return metrics if metrics else None


def _score_params(m: Dict) -> float:
    """Score a parameter combo. Higher = better. Returns -inf if targets not met."""
    wr  = m.get("wr", 0.0)
    tpm = m.get("trades_pm", 0.0)
    mp  = m.get("monthly_pct", -99.0)
    if wr < TARGET_WR or tpm < TARGET_TRADES_PM or mp < TARGET_MONTHLY:
        # Partial credit for ranking near-misses
        return wr * 0.5 + min(tpm / TARGET_TRADES_PM, 1.0) * 0.3 + min(max(mp, -5) / TARGET_MONTHLY, 1.0) * 0.2
    sharpe = m.get("sharpe", 0.0)
    dd     = abs(m.get("max_dd", 10.0))
    return 1.0 + wr * 0.4 + min(tpm / 60, 1.0) * 0.2 + min(mp / 10, 1.0) * 0.2 + sharpe * 0.1 - dd * 0.005


def _run_combo(eng, data: Dict, params: Dict) -> Optional[Dict]:
    """Patch module constants, run backtest, return parsed metrics."""
    import backtest_engine_india as bk
    for k, v in params.items():
        setattr(bk, k, v)
    # Also update adaptive threshold base
    bk._ADAPTIVE_MIN_SCORE = params.get("MIN_SCORE", bk.MIN_SCORE)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            bk.run_backtest_from_data(data, capital=500_000.0)
    except Exception as e:
        print(f"  [run error] {e}", flush=True)
        return None
    return _parse_report(buf.getvalue())


def _patch_engine(params: Dict):
    """Write winning parameters back to backtest_engine_india.py."""
    engine_path = Path(__file__).parent / "backtest_engine_india.py"
    src = engine_path.read_text()
    replacements = {
        "MIN_SCORE":          (r"^MIN_SCORE\s*=\s*[\d.]+",        f"MIN_SCORE    = {params['MIN_SCORE']}"),
        "MIN_QUAL_COUNT":     (r"^MIN_QUAL_COUNT\s*=\s*\d+",       f"MIN_QUAL_COUNT     = {params['MIN_QUAL_COUNT']}"),
        "ENTRY_RSI_LONG_MAX": (r"^ENTRY_RSI_LONG_MAX\s*=\s*\d+",   f"ENTRY_RSI_LONG_MAX = {params['ENTRY_RSI_LONG_MAX']}"),
        "ENTRY_RSI_LONG_MIN": (r"^ENTRY_RSI_LONG_MIN\s*=\s*\d+",   f"ENTRY_RSI_LONG_MIN = {params['ENTRY_RSI_LONG_MIN']}"),
        "ENTRY_RVOL_MIN":     (r"^ENTRY_RVOL_MIN\s*=\s*[\d.]+",    f"ENTRY_RVOL_MIN     = {params['ENTRY_RVOL_MIN']}"),
        "BREADTH_BULL_HARD":  (r"^BREADTH_BULL_HARD\s*=\s*[\d.]+", f"BREADTH_BULL_HARD  = {params['BREADTH_BULL_HARD']}"),
        "BREADTH_BULL_SOFT":  (r"^BREADTH_BULL_SOFT\s*=\s*[\d.]+", f"BREADTH_BULL_SOFT  = {params['BREADTH_BULL_SOFT']}"),
    }
    for _key, (pattern, replacement) in replacements.items():
        # Replace only the first-occurring definition line (module-level, not in comments)
        new_src, n = re.subn(pattern, replacement, src, count=1, flags=re.MULTILINE)
        if n:
            src = new_src
    engine_path.write_text(src)
    print(f"\n  Engine patched with winning parameters.", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Self-running parameter optimizer for NSE backtest")
    ap.add_argument("--source", default="cache",
                    choices=["upstox", "cache", "synthetic"],
                    help="Data source (default: cache)")
    ap.add_argument("--days", type=int, default=90, help="Days of history for cache/upstox")
    ap.add_argument("--capital", type=float, default=500_000.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print winning params without patching the engine")
    args = ap.parse_args()

    print("=" * 65)
    print("  NSE Backtest Parameter Optimizer")
    print(f"  Target: WR >= {TARGET_WR*100:.0f}% | Trades >= {TARGET_TRADES_PM}/mo | Return >= {TARGET_MONTHLY}%/mo")
    print("=" * 65, flush=True)

    # ── Load data once ────────────────────────────────────────────────────────
    data: Dict = {}
    print(f"\n[1/3] Loading data (source={args.source}) ...", flush=True)

    if args.source == "synthetic":
        print("  WARNING: Synthetic data has uncorrelated breadth — WR stats are not realistic.", flush=True)
        print("  Use --source cache or --source upstox for meaningful optimization.", flush=True)
        from generate_synthetic_data import generate_nse_data
        from watchlist_india import get_active_watchlist
        import backtest_engine_india as _bk
        syms = get_active_watchlist()[:50]
        raw = generate_nse_data(symbols=syms, days=60, seed=42)
        for sym, df in raw.items():
            try:
                df2 = _bk._compute_all(df)
                df3 = _bk._build_orb(df2)
                data[sym] = df3
            except Exception:
                pass

    elif args.source == "cache":
        from data_cache import load_data as load_cache, cache_stats
        stats = cache_stats()
        if stats["symbols"] == 0:
            print("  ERROR: Cache is empty. Run with --source upstox first to populate it.", flush=True)
            sys.exit(1)
        from watchlist_india import get_active_watchlist
        import backtest_engine_india as _bk
        syms = get_active_watchlist()
        raw = load_cache(syms, interval="5m")
        if not raw:
            raw = load_cache(syms, interval="1h")
        if not raw:
            print("  ERROR: No data in cache for any interval.", flush=True)
            sys.exit(1)
        for sym, df in raw.items():
            try:
                df2 = _bk._compute_all(df)
                df3 = _bk._build_orb(df2)
                data[sym] = df3
            except Exception:
                pass
        print(f"  Loaded {len(data)} symbols from cache.", flush=True)

    elif args.source == "upstox":
        import datetime
        to_dt   = datetime.date.today()
        from_dt = to_dt - datetime.timedelta(days=args.days)
        from_s  = from_dt.strftime("%Y-%m-%d")
        to_s    = to_dt.strftime("%Y-%m-%d")
        from watchlist_india import get_active_watchlist
        from auth_upstox import get_upstox_client, verify_connection
        import data_fetch_upstox as dfu
        import backtest_engine_india as _bk
        syms = get_active_watchlist()
        print(f"  Connecting to Upstox ...", flush=True)
        client = get_upstox_client()
        if not client or not verify_connection(client):
            print("  ERROR: Upstox connection failed. Check UPSTOX_ACCESS_TOKEN in .env", flush=True)
            sys.exit(1)
        dfu.set_upstox_client(client)
        print(f"  Fetching {len(syms)} symbols ({from_s} → {to_s}) ...", flush=True)
        for i, sym in enumerate(syms, 1):
            if i % 20 == 0:
                print(f"    {i}/{len(syms)} fetched ...", flush=True)
            try:
                df = _bk._fetch(client, sym, from_s, to_s)
                if df is not None and len(df) > 10:
                    data[sym] = df   # _fetch already calls _compute_all + _build_orb
            except Exception:
                pass
        print(f"  Fetched {len(data)} symbols.", flush=True)

    if not data:
        print("  ERROR: No data loaded. Aborting.", flush=True)
        sys.exit(1)

    # ── Build parameter combinations ──────────────────────────────────────────
    keys   = list(GRID.keys())
    values = list(GRID.values())
    combos = list(product(*values))
    total  = len(combos)
    print(f"\n[2/3] Grid search: {total} combinations ...", flush=True)
    print(f"  Params: {', '.join(keys)}\n", flush=True)

    import backtest_engine_india as bk

    results: List[Tuple[float, Dict, Dict]] = []
    best_score   = -99.0
    best_params  = {}
    best_metrics = {}
    found_target = False
    t0 = time.time()

    # Save original constants so we can restore on exit
    originals = {k: getattr(bk, k) for k in keys}

    try:
        for idx, combo in enumerate(combos, 1):
            params = dict(zip(keys, combo))

            # Skip obviously bad combos early
            if params["ENTRY_RSI_LONG_MIN"] >= params["ENTRY_RSI_LONG_MAX"] - 10:
                continue
            if params["BREADTH_BULL_HARD"] >= params["BREADTH_BULL_SOFT"]:
                continue

            elapsed = time.time() - t0
            eta_s   = (elapsed / idx) * (total - idx) if idx > 1 else 0
            print(
                f"  [{idx:>4}/{total}] MIN_SCORE={params['MIN_SCORE']} "
                f"QUAL={params['MIN_QUAL_COUNT']} "
                f"RSI={params['ENTRY_RSI_LONG_MIN']}-{params['ENTRY_RSI_LONG_MAX']} "
                f"RVOL={params['ENTRY_RVOL_MIN']} "
                f"BREADTH={params['BREADTH_BULL_HARD']:.2f}/{params['BREADTH_BULL_SOFT']:.2f} "
                f"ETA:{eta_s/60:.0f}m",
                end=" ", flush=True
            )

            metrics = _run_combo(bk, data, params)
            if not metrics:
                print("→ ERROR", flush=True)
                continue

            wr    = metrics.get("wr", 0.0)
            tpm   = metrics.get("trades_pm", 0.0)
            mp    = metrics.get("monthly_pct", -99.0)
            score = _score_params(metrics)

            print(
                f"→ WR={wr*100:.1f}%  Trades/mo={tpm:.1f}  Return={mp:.1f}%  Score={score:.3f}",
                flush=True
            )

            results.append((score, params, metrics))
            if score > best_score:
                best_score   = score
                best_params  = params
                best_metrics = metrics

            # Early stop if all 3 targets met
            if wr >= TARGET_WR and tpm >= TARGET_TRADES_PM and mp >= TARGET_MONTHLY:
                found_target = True
                print(f"\n  TARGET MET at combo {idx}/{total}! Stopping early.", flush=True)
                break

    finally:
        # Restore originals so the module is clean if we don't patch the file
        for k, v in originals.items():
            setattr(bk, k, v)

    # ── Report ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    if found_target:
        print(f"  TARGET ACHIEVED — {TARGET_WR*100:.0f}% WR + {TARGET_TRADES_PM}+ trades/mo + {TARGET_MONTHLY}%+/mo")
    else:
        print("  Target not fully met — showing best found:")

    print(f"\n  Best parameters:")
    for k, v in best_params.items():
        print(f"    {k} = {v}")
    print(f"\n  Best metrics:")
    for k, v in best_metrics.items():
        if isinstance(v, float):
            print(f"    {k} = {v:.3f}")
        else:
            print(f"    {k} = {v}")
    print("=" * 65, flush=True)

    if not found_target:
        print(
            "\n  DIAGNOSIS: Even the best parameter combo doesn't hit all 3 targets.")
        print("  Likely causes:")
        print("    • Test period has too many bear days (breadth < threshold)")
        print("    • Signal patterns need further filtering (see Strategy Attribution above)")
        print("    • WIN RATE gap: check which signal types are losing in the best run")
        print("  Consider running on a longer/more recent dataset.")

    # ── Patch engine ─────────────────────────────────────────────────────────
    if best_params and not args.dry_run:
        print(f"\n[3/3] Patching engine with best parameters ...", flush=True)
        _patch_engine(best_params)
        print(f"\n  Done. Run the backtest again to confirm:", flush=True)
        print(f"    python3 india/backtest_engine_india.py --source cache", flush=True)
        print(f"  Or with Upstox:", flush=True)
        print(f"    python3 india/backtest_engine_india.py --source upstox", flush=True)
    elif args.dry_run:
        print("\n  [dry-run] Engine not patched.", flush=True)


if __name__ == "__main__":
    main()
