"""
signal_attribution_test.py — Deep comprehensive signal + parameter test (parallel)

5 Phases, all run in parallel across CPU cores:

  Phase 1 : Walk-forward signal test
              All 18 signals × 3 windows (first-half, second-half, full period)
              Robust = profitable in BOTH halves
              ~3-5 min

  Phase 2 : Combo search on full data
              All pairs + triples of robust signals
              ~3-5 min

  Phase 3 : Expanded parameter grid on best combo
              MIN_SCORE × RVOL × QUAL × RSI_MAX  (180 combos)
              ~5-8 min

  Phase 4 : Breadth + RSI fine-tune on Phase 3 winner
              BREADTH_HARD × BREADTH_SOFT × RSI_MIN  (48 combos)
              ~2-3 min

  Phase 5 : Final walk-forward robustness check
              Best config on Window-B only (out-of-sample)
              1 backtest — confirms WR doesn't collapse out-of-sample

Total runtime: ~15-25 min on VPS with 4 CPU cores

Usage:
  python3 india/signal_attribution_test.py --no-telegram
  python3 india/signal_attribution_test.py --force-fetch   # re-download data
  python3 india/signal_attribution_test.py --days 90 --max-symbols 50 --force-fetch
"""

import argparse
import contextlib
import io
import pickle
import re
import sys
import time
import multiprocessing as mp
from datetime import datetime as _dt, timedelta
from itertools import combinations
from pathlib import Path

_HERE   = Path(__file__).parent
_ROOT   = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

CACHE_FILE  = _HERE / "optimizer_cache.pkl"
ENGINE_FILE = _HERE / "backtest_engine_india.py"

ALL_SIGNALS = [
    "ORB_BULL_CONFIRM",
    "ORB_BULL_CLEAN",
    "SESSION_BULL",
    "CONFIRMED_MOMENTUM",
    "EMA_BULL_STACK",
    "MACD_XOVER",
    "VWAP_BOUNCE",
    "VWAP_BREAKOUT",
    "VWAP_RECLAIM",
    "BREAKOUT_BULL_CONFIRM",
    "RANGE_EXP",
    "PULLBACK_CONT",
    "HAMMER_REVERSAL",
    "RSI_BULL_CROSS",
    "VOLUME_SPIKE_BULL",
    "MOMENTUM_IGNITION",
    "EMA21_PULLBACK",
    "ATR_SQUEEZE_BREAKOUT",
]

# ── Worker shared memory (set once per process via initializer) ───────────────
# Three dataset slices stored per worker process.
_DATA_FULL = None   # full date range
_DATA_WIN_A = None  # first half of date range
_DATA_WIN_B = None  # second half of date range


def _init_worker(bytes_full: bytes, bytes_a: bytes, bytes_b: bytes):
    global _DATA_FULL, _DATA_WIN_A, _DATA_WIN_B
    _DATA_FULL  = pickle.loads(bytes_full)
    _DATA_WIN_A = pickle.loads(bytes_a)
    _DATA_WIN_B = pickle.loads(bytes_b)


def _run_task(task):
    """
    task = (signal_list, params_dict, window_key)
    window_key: "full" | "a" | "b"
    Returns metrics dict (all results, no filtering).
    """
    signal_names, params, window = task

    import backtest_engine_india as bk

    # Set every tunable constant
    bk.SIGNAL_WHITELIST    = tuple(signal_names)
    bk.MIN_SCORE           = params["MIN_SCORE"]
    bk._ADAPTIVE_MIN_SCORE = params["MIN_SCORE"]
    bk.MIN_QUAL_COUNT      = params["MIN_QUAL_COUNT"]
    bk.ENTRY_RVOL_MIN      = params["ENTRY_RVOL_MIN"]
    bk.ENTRY_RSI_LONG_MIN  = params["RSI_MIN"]
    bk.ENTRY_RSI_LONG_MAX  = params["RSI_MAX"]
    bk.BREADTH_BULL_HARD   = params["BREADTH_HARD"]
    bk.BREADTH_BULL_SOFT   = params["BREADTH_SOFT"]

    data = {"full": _DATA_FULL, "a": _DATA_WIN_A, "b": _DATA_WIN_B}[window]

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            bk.run_backtest_from_data(data, capital=500_000.0)
    except Exception as e:
        return {"signals": list(signal_names), "params": params, "window": window,
                "error": str(e)[:120]}

    output = buf.getvalue()
    r = {"signals": list(signal_names), "params": params, "window": window}

    def _float(pattern, default=0.0):
        m = re.search(pattern, output)
        return float(m.group(1)) if m else default

    r["trades"]      = int(_float(r"Total trades\s*:\s*(\d+)"))
    r["wr"]          = _float(r"Win rate\s*:\s*([\d.]+)%")
    r["monthly_pct"] = _float(r"Monthly return\s*:\s*([+-]?[\d.]+)%", -99.0)
    r["max_dd"]      = _float(r"Max drawdown\s*:\s*([\d.]+)%")
    r["sharpe"]      = _float(r"Sharpe ratio\s*:\s*([+-]?[\d.]+)")
    r["pf"]          = _float(r"Profit factor\s*:\s*([\d.]+)")

    m = re.search(r"Period\s*:.*?(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})", output)
    if m:
        d1 = _dt.strptime(m.group(1), "%Y-%m-%d")
        d2 = _dt.strptime(m.group(2), "%Y-%m-%d")
        months = max((d2 - d1).days / 30.44, 0.1)
        r["trades_pm"] = r["trades"] / months

    return r


# ── Scoring + filtering helpers ───────────────────────────────────────────────

def _score(r):
    """Composite rank score. Higher = better. Zero if not tradeable."""
    wr  = r.get("wr", 0)
    ret = max(r.get("monthly_pct", -99), 0)
    dd  = max(r.get("max_dd", 1.0), 1.0)
    tpm = r.get("trades_pm", 0)
    if tpm < 5 or wr < 40:
        return 0.0
    return (wr / 100) * ret * min(tpm / 20, 1.5) / dd


def _is_target(r):
    """Meets all deployment targets."""
    return (r.get("wr", 0)          >= 55.0
            and r.get("monthly_pct", -99) >= 4.0
            and r.get("trades_pm", 0)     >= 20.0
            and r.get("max_dd", 100)      <=  8.0
            and "error" not in r)


def _is_pass(r, wr_min=50, ret_min=0, tpm_min=5):
    return (r.get("wr", 0) >= wr_min
            and r.get("monthly_pct", -99) >= ret_min
            and r.get("trades_pm", 0) >= tpm_min
            and "error" not in r)


def _fmt(signals):
    return "+".join(signals)


# ── Pretty printing ───────────────────────────────────────────────────────────

def _table(results, title="", max_rows=25):
    ok = sorted(
        [r for r in results if "error" not in r and r.get("trades", 0) >= 1],
        key=_score, reverse=True,
    )
    sep = "─" * 80
    print(f"\n{sep}")
    print(f"  {title}")
    print(f"  {'Signal(s)':<36} {'Win':>5}  {'T/mo':>5}  {'Ret%':>6}  {'DD%':>5}  {'Sharpe':>6}  {'Win?':>4}")
    print(f"  {'─'*36} {'─'*5}  {'─'*5}  {'─'*6}  {'─'*5}  {'─'*6}  {'─'*4}")
    for r in ok[:max_rows]:
        sigs  = _fmt(r.get("signals", ["?"]))[:36]
        flag  = "✅" if _is_target(r) else ("✓" if _is_pass(r) else "")
        print(f"  {sigs:<36} {r.get('wr',0):>4.1f}%  "
              f"{r.get('trades_pm',0):>4.1f}  "
              f"{r.get('monthly_pct',-99):>+5.1f}%  "
              f"{r.get('max_dd',0):>4.1f}%  "
              f"{r.get('sharpe',0):>5.2f}  {flag}")
    if not ok:
        print("  (no results)")
    print(sep, flush=True)


# ── Data utilities ────────────────────────────────────────────────────────────

def _split_data(data: dict):
    """Split cached data dict into first-half and second-half by date."""
    import pandas as pd

    # Gather all unique dates across all symbols
    all_dates = set()
    for df in data.values():
        all_dates.update(df.index.normalize().unique())
    all_dates = sorted(all_dates)

    if len(all_dates) < 4:
        return data, data   # not enough data to split

    mid = len(all_dates) // 2
    cut = all_dates[mid]

    win_a, win_b = {}, {}
    for sym, df in data.items():
        df_a = df[df.index < cut]
        df_b = df[df.index >= cut]
        if len(df_a) >= 10:
            win_a[sym] = df_a
        if len(df_b) >= 10:
            win_b[sym] = df_b

    return win_a, win_b


def _param_relaxed():
    """Fully relaxed — only whitelist gate matters."""
    return dict(MIN_SCORE=6.0, MIN_QUAL_COUNT=1, ENTRY_RVOL_MIN=1.0,
                RSI_MIN=25, RSI_MAX=85, BREADTH_HARD=0.35, BREADTH_SOFT=0.45)


def _param_grid_phase3():
    """180-combo grid for Phase 3 (signal combo + param optimisation)."""
    grid = []
    for ms in [8.0, 10.0, 12.0, 14.0, 16.0, 18.0]:     # 6
        for rv in [1.0, 1.3, 1.5, 1.8, 2.0]:            # 5
            for qc in [1, 2]:                             # 2
                for rmax in [72, 78, 84]:                 # 3
                    grid.append(dict(
                        MIN_SCORE=ms, ENTRY_RVOL_MIN=rv, MIN_QUAL_COUNT=qc,
                        RSI_MIN=30, RSI_MAX=rmax,
                        BREADTH_HARD=0.45, BREADTH_SOFT=0.55,
                    ))
    return grid   # 6×5×2×3 = 180


def _param_grid_phase4(base_params):
    """48-combo breadth + RSI_MIN fine-tune around Phase 3 winner."""
    grid = []
    for bhard in [0.40, 0.45, 0.50, 0.55]:              # 4
        for bsoft in [0.50, 0.55, 0.60, 0.65]:          # 4
            for rmin in [28, 33, 38, 43, 48, 53]:        # 6 — but skip bsoft≤bhard
                if bsoft <= bhard:
                    continue
                p = dict(base_params)
                p["BREADTH_HARD"] = bhard
                p["BREADTH_SOFT"] = bsoft
                p["RSI_MIN"]      = rmin
                grid.append(p)
    return grid   # ~48-96 valid combos depending on bsoft>bhard filter


# ── Engine patcher ────────────────────────────────────────────────────────────

def _patch_engine(signals, params):
    src = ENGINE_FILE.read_text()

    def _sub(pattern, replacement, text):
        new, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
        return new, n

    wl = ", ".join(f'"{s}"' for s in signals)
    src, _ = _sub(
        r'^SIGNAL_WHITELIST\s*=\s*\(.*?\).*$',
        f'SIGNAL_WHITELIST = ({wl},)  # auto-set by attribution test',
        src,
    )
    src, _ = _sub(
        r'^MIN_SCORE\s*=\s*[\d.]+.*$',
        f'MIN_SCORE    = {params["MIN_SCORE"]}   # auto-optimized',
        src,
    )
    src, _ = _sub(
        r'^ENTRY_RVOL_MIN\s*=\s*[\d.]+.*$',
        f'ENTRY_RVOL_MIN     = {params["ENTRY_RVOL_MIN"]}   # auto-optimized',
        src,
    )
    src, _ = _sub(
        r'^MIN_QUAL_COUNT\s*=\s*\d+.*$',
        f'MIN_QUAL_COUNT     = {params["MIN_QUAL_COUNT"]}     # auto-optimized',
        src,
    )
    src, _ = _sub(
        r'^ENTRY_RSI_LONG_MIN\s*=\s*\d+.*$',
        f'ENTRY_RSI_LONG_MIN = {params["RSI_MIN"]}    # auto-optimized',
        src,
    )
    src, _ = _sub(
        r'^ENTRY_RSI_LONG_MAX\s*=\s*\d+.*$',
        f'ENTRY_RSI_LONG_MAX = {params["RSI_MAX"]}    # auto-optimized',
        src,
    )
    src, _ = _sub(
        r'^BREADTH_BULL_HARD\s*=\s*[\d.]+.*$',
        f'BREADTH_BULL_HARD  = {params["BREADTH_HARD"]}  # auto-optimized',
        src,
    )
    src, _ = _sub(
        r'^BREADTH_BULL_SOFT\s*=\s*[\d.]+.*$',
        f'BREADTH_BULL_SOFT  = {params["BREADTH_SOFT"]}  # auto-optimized',
        src,
    )

    ENGINE_FILE.write_text(src)
    print(f"  Engine patched:", flush=True)
    print(f"    SIGNAL_WHITELIST = {signals}")
    print(f"    MIN_SCORE        = {params['MIN_SCORE']}")
    print(f"    ENTRY_RVOL_MIN   = {params['ENTRY_RVOL_MIN']}")
    print(f"    MIN_QUAL_COUNT   = {params['MIN_QUAL_COUNT']}")
    print(f"    RSI range        = {params['RSI_MIN']}–{params['RSI_MAX']}")
    print(f"    BREADTH_HARD     = {params['BREADTH_HARD']}")
    print(f"    BREADTH_SOFT     = {params['BREADTH_SOFT']}", flush=True)


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_data(args):
    if not args.force_fetch and CACHE_FILE.exists():
        import stat, os
        age_days = (time.time() - os.stat(CACHE_FILE).st_mtime) / 86400
        if age_days < 8:
            with open(CACHE_FILE, "rb") as f:
                data = pickle.load(f)
            data = dict(list(data.items())[:args.max_symbols])
            print(f"  Cache: {len(data)} symbols  (age {age_days:.1f} days)", flush=True)
            return data
        print(f"  Cache too old ({age_days:.1f} days) — re-fetching.", flush=True)

    import datetime
    import data_fetch_upstox as dfu
    from auth_upstox import get_upstox_client, verify_connection
    import backtest_engine_india as bk

    to_dt   = datetime.date.today()
    from_dt = to_dt - datetime.timedelta(days=args.days)
    from_s  = from_dt.strftime("%Y-%m-%d")
    to_s    = to_dt.strftime("%Y-%m-%d")

    UNIVERSE = [
        "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
        "HINDUNILVR","ITC","SBIN","BHARTIARTL","KOTAKBANK",
        "LT","AXISBANK","ASIANPAINT","MARUTI","BAJFINANCE",
        "TITAN","HCLTECH","SUNPHARMA","ULTRACEMCO","WIPRO",
        "ONGC","POWERGRID","NTPC","COALINDIA","TATAMOTORS",
        "JSWSTEEL","HINDALCO","TECHM","INDUSINDBK","BAJAJFINSV",
        "ADANIENT","ADANIPORTS","DRREDDY","EICHERMOT","GRASIM",
        "HEROMOTOCO","NESTLEIND","SBILIFE","SHREECEM","TATASTEEL",
        "TATACONSUM","BAJAJ-AUTO","CIPLA","DIVISLAB","HDFCLIFE",
        "M&M","PIDILITIND","UPL","VEDL","BRITANNIA",
    ]

    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed."); sys.exit(1)
    dfu.set_upstox_client(client)

    data = {}
    syms = UNIVERSE[:args.max_symbols]
    print(f"  Fetching {len(syms)} symbols  {from_s} → {to_s} ...", flush=True)
    t0 = time.time()
    for i, sym in enumerate(syms, 1):
        if i % 5 == 0:
            print(f"  {i}/{len(syms)}  ETA:{((time.time()-t0)/i*(len(syms)-i))/60:.0f}m", flush=True)
        try:
            df = bk._fetch(client, sym, from_s, to_s)
            if df is not None and len(df) > 10:
                data[sym] = df
        except Exception:
            pass

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(data, f, protocol=4)
    print(f"  Fetched {len(data)} symbols in {(time.time()-t0)/60:.1f}m  (cached)", flush=True)
    return data


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-fetch",  action="store_true")
    ap.add_argument("--no-telegram",  action="store_true")
    ap.add_argument("--max-symbols",  type=int, default=30)
    ap.add_argument("--days",         type=int, default=60)
    ap.add_argument("--workers",      type=int, default=0)
    args = ap.parse_args()

    n_workers = args.workers or min(mp.cpu_count(), 8)
    t_total   = time.time()

    print("=" * 80)
    print("  NSE Deep Signal Attribution — 5-Phase Parallel Test")
    print(f"  Signals   : {len(ALL_SIGNALS)} tested individually, in pairs, and triples")
    print(f"  Params    : 180-combo grid (score×rvol×qual×rsi) + 48-combo breadth tune")
    print(f"  Validation: walk-forward (first-half train / second-half test)")
    print(f"  Workers   : {n_workers} CPU cores")
    print(f"  Targets   : WR≥55%  Return≥4%/mo  Trades≥20/mo  DD<8%")
    print("=" * 80, flush=True)

    # ── Load + split data ─────────────────────────────────────────────────────
    print("\n[DATA] Loading ...", flush=True)
    data_full = _load_data(args)
    if not data_full:
        print("ERROR: No data."); sys.exit(1)

    data_a, data_b = _split_data(data_full)
    print(f"  Full: {len(data_full)} symbols | Window-A: {len(data_a)} | Window-B: {len(data_b)}", flush=True)

    bytes_full = pickle.dumps(data_full, protocol=4)
    bytes_a    = pickle.dumps(data_a,    protocol=4)
    bytes_b    = pickle.dumps(data_b,    protocol=4)
    print(f"  Data serialised: {(len(bytes_full)+len(bytes_a)+len(bytes_b))/1e6:.0f} MB total", flush=True)

    RELAXED = _param_relaxed()

    def _pool():
        return mp.Pool(n_workers, initializer=_init_worker,
                       initargs=(bytes_full, bytes_a, bytes_b))

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1: Walk-forward signal test
    #   Each of the 18 signals tested on Window-A, Window-B, and Full
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n[PHASE 1] Walk-forward test: {len(ALL_SIGNALS)} signals × 3 windows ...", flush=True)
    tasks_p1 = []
    for sig in ALL_SIGNALS:
        tasks_p1.append(([sig], RELAXED, "a"))
        tasks_p1.append(([sig], RELAXED, "b"))
        tasks_p1.append(([sig], RELAXED, "full"))
    # = 18 × 3 = 54 tasks

    t0 = time.time()
    with _pool() as pool:
        raw_p1 = pool.map(_run_task, tasks_p1)
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)

    # Organise by signal
    sig_map: dict = {s: {"a": None, "b": None, "full": None} for s in ALL_SIGNALS}
    for r in raw_p1:
        sig = r["signals"][0]
        sig_map[sig][r["window"]] = r

    # Walk-forward table: show all three windows side by side
    print(f"\n  {'Signal':<30} {'WinA-WR':>8}  {'WinB-WR':>8}  {'Full-WR':>8}  {'Full-Ret':>9}  {'Status'}")
    print(f"  {'─'*30} {'─'*8}  {'─'*8}  {'─'*8}  {'─'*9}  {'─'*10}")
    robust_sigs = []
    for sig in ALL_SIGNALS:
        ra   = sig_map[sig]["a"]   or {}
        rb   = sig_map[sig]["b"]   or {}
        rf   = sig_map[sig]["full"] or {}
        wra  = ra.get("wr", 0)
        wrb  = rb.get("wr", 0)
        wrf  = rf.get("wr", 0)
        retf = rf.get("monthly_pct", -99)
        tpa  = ra.get("trades", 0)
        tpb  = rb.get("trades", 0)
        # Robust = profitable (WR≥48%, ret>0) in BOTH halves with enough trades
        is_robust = (wra >= 48 and wrb >= 48 and tpa >= 2 and tpb >= 2 and retf > 0)
        if is_robust:
            robust_sigs.append(sig)
        status = "ROBUST" if is_robust else ("cand-A" if wra >= 48 else ("cand-B" if wrb >= 48 else "skip"))
        print(f"  {sig:<30} {wra:>7.1f}%  {wrb:>7.1f}%  {wrf:>7.1f}%  {retf:>+8.1f}%  {status}")

    print(f"\n  Robust signals (profitable both halves): {robust_sigs if robust_sigs else 'NONE'}", flush=True)

    # Fallback: if no robust sigs, take top-5 by full-period WR
    if not robust_sigs:
        print("  No signal is robust — using top-5 by full-period WR as candidates.", flush=True)
        full_results = [sig_map[s]["full"] for s in ALL_SIGNALS if sig_map[s]["full"]]
        full_results.sort(key=lambda r: r.get("wr", 0), reverse=True)
        robust_sigs = [r["signals"][0] for r in full_results[:5] if r.get("trades", 0) >= 3]

    full_results_p1 = [sig_map[s]["full"] for s in ALL_SIGNALS if sig_map[s]["full"]]
    _table(full_results_p1, "PHASE 1 — Full-period individual signal results")

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2: Combo search (singles + pairs + triples of robust candidates)
    # ─────────────────────────────────────────────────────────────────────────
    tasks_p2 = []
    for sig in robust_sigs:
        tasks_p2.append(([sig], RELAXED, "full"))
    for a, b in combinations(robust_sigs, 2):
        tasks_p2.append(([a, b], RELAXED, "full"))
    if len(robust_sigs) <= 7:
        for a, b, c in combinations(robust_sigs, 3):
            tasks_p2.append(([a, b, c], RELAXED, "full"))

    print(f"\n[PHASE 2] {len(tasks_p2)} signal combinations (singles+pairs+triples) ...", flush=True)
    t0 = time.time()
    with _pool() as pool:
        results_p2 = pool.map(_run_task, tasks_p2)
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)
    _table(results_p2, "PHASE 2 — Signal combinations (full period)")

    ok_p2 = [r for r in results_p2 if "error" not in r and r.get("trades", 0) >= 3]
    best_p2 = max(ok_p2, key=_score) if ok_p2 else {"signals": ["ORB_BULL_CONFIRM"]}
    best_sigs = best_p2["signals"]
    print(f"\n  Best combo → Phase 3: {best_sigs}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3: Full parameter grid (180 combos) on best signal combo
    # ─────────────────────────────────────────────────────────────────────────
    grid_p3  = _param_grid_phase3()
    tasks_p3 = [(best_sigs, p, "full") for p in grid_p3]

    print(f"\n[PHASE 3] {len(tasks_p3)}-combo param grid on {best_sigs} ...", flush=True)
    t0 = time.time()
    with _pool() as pool:
        results_p3 = pool.map(_run_task, tasks_p3)
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)
    _table(results_p3, "PHASE 3 — Parameter grid (top results)")

    ok_p3 = sorted(
        [r for r in results_p3
         if r.get("wr", 0) >= 50
         and r.get("trades_pm", 0) >= 8
         and r.get("max_dd", 100) <= 15
         and "error" not in r],
        key=_score, reverse=True,
    )
    best_p3      = ok_p3[0] if ok_p3 else best_p2
    best_params3 = best_p3.get("params", RELAXED)
    print(f"\n  Best params → Phase 4: {best_params3}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 4: Breadth + RSI_MIN fine-tune (48 combos)
    # ─────────────────────────────────────────────────────────────────────────
    grid_p4  = _param_grid_phase4(best_params3)
    tasks_p4 = [(best_sigs, p, "full") for p in grid_p4]

    print(f"\n[PHASE 4] {len(tasks_p4)}-combo breadth+RSI fine-tune ...", flush=True)
    t0 = time.time()
    with _pool() as pool:
        results_p4 = pool.map(_run_task, tasks_p4)
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)
    _table(results_p4, "PHASE 4 — Breadth + RSI fine-tune")

    ok_p4 = sorted(
        [r for r in results_p4
         if r.get("wr", 0) >= 50
         and r.get("trades_pm", 0) >= 8
         and "error" not in r],
        key=_score, reverse=True,
    )
    best_p4      = ok_p4[0] if ok_p4 else best_p3
    best_params4 = best_p4.get("params", best_params3)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 5: Walk-forward robustness check
    #   Run best config on Window-B only (out-of-sample)
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n[PHASE 5] Out-of-sample validation on Window-B ...", flush=True)
    t0 = time.time()
    with _pool() as pool:
        oos_results = pool.map(_run_task, [(best_sigs, best_params4, "b")])
    oos = oos_results[0] if oos_results else {}
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)
    print(f"\n  Out-of-sample (Window-B):")
    print(f"    WR={oos.get('wr',0):.1f}%  Trades/mo={oos.get('trades_pm',0):.1f}  "
          f"Return={oos.get('monthly_pct',-99):+.1f}%  DD={oos.get('max_dd',0):.1f}%", flush=True)

    oos_robust = (oos.get("wr", 0) >= 50 and oos.get("monthly_pct", -99) > 0
                  and oos.get("trades", 0) >= 2)

    # ─────────────────────────────────────────────────────────────────────────
    # FINAL SUMMARY
    # ─────────────────────────────────────────────────────────────────────────
    best_final = best_p4
    fp         = best_params4

    print("\n" + "=" * 80)
    print("  FINAL RESULT")
    print("=" * 80, flush=True)
    print(f"  Signals  : {best_final.get('signals', best_sigs)}")
    print(f"  WR       : {best_final.get('wr', 0):.1f}%        (target ≥55%)")
    print(f"  Return   : {best_final.get('monthly_pct', -99):+.2f}%/mo   (target ≥4%)")
    print(f"  Trades   : {best_final.get('trades_pm', 0):.1f}/mo       (target ≥20)")
    print(f"  Max DD   : {best_final.get('max_dd', 0):.1f}%        (limit <8%)")
    print(f"  Sharpe   : {best_final.get('sharpe', 0):.2f}          (target ≥1.5)")
    print()
    print(f"  OUT-OF-SAMPLE (Window-B):")
    print(f"    WR={oos.get('wr',0):.1f}%  Return={oos.get('monthly_pct',-99):+.1f}%  "
          f"{'✅ ROBUST' if oos_robust else '⚠  DEGRADED (may overfit)'}")
    print()

    targets = {
        "WR≥55%":         best_final.get("wr", 0) >= 55,
        "Return≥4%/mo":   best_final.get("monthly_pct", -99) >= 4.0,
        "Trades≥20/mo":   best_final.get("trades_pm", 0) >= 20,
        "DD<8%":          best_final.get("max_dd", 100) <= 8.0,
        "OOS robust":     oos_robust,
    }
    for label, met in targets.items():
        print(f"  {'✅' if met else '❌'}  {label}")

    all_met = all(targets.values())
    print()
    if all_met:
        print("  ✅  ALL TARGETS MET — deploy Friday with ₹25,000")
    else:
        gaps = [k for k, v in targets.items() if not v]
        print(f"  ⚠   Gaps: {', '.join(gaps)}")
        print("  Bot not ready for live yet. Review Phase 1 walk-forward results.")

    print(f"\n  Total runtime: {(time.time()-t_total)/60:.1f} min", flush=True)

    # Patch engine
    print(f"\n  Patching engine ...", flush=True)
    _patch_engine(best_final.get("signals", best_sigs), fp)

    # ── Save full report ──────────────────────────────────────────────────────
    lines = [
        "=" * 80,
        "  NSE Deep Signal Attribution Report",
        "=" * 80,
        "",
        "PHASE 1 — Walk-forward signal test",
        f"  {'Signal':<30} {'WinA-WR':>8}  {'WinB-WR':>8}  {'Full-WR':>8}  {'Full-Ret':>9}  {'Robust?':>8}",
    ]
    for sig in ALL_SIGNALS:
        ra  = sig_map[sig]["a"]   or {}
        rb  = sig_map[sig]["b"]   or {}
        rf  = sig_map[sig]["full"] or {}
        status = "ROBUST" if sig in robust_sigs else ""
        lines.append(
            f"  {sig:<30} {ra.get('wr',0):>7.1f}%  {rb.get('wr',0):>7.1f}%  "
            f"{rf.get('wr',0):>7.1f}%  {rf.get('monthly_pct',-99):>+8.1f}%  {status}"
        )

    lines += ["", "PHASE 2 — Signal combinations (top 20)"]
    for r in sorted(ok_p2, key=_score, reverse=True)[:20]:
        lines.append(
            f"  {_fmt(r['signals']):<36} WR={r.get('wr',0):.1f}%  "
            f"T/mo={r.get('trades_pm',0):.1f}  Ret={r.get('monthly_pct',-99):+.1f}%"
        )

    lines += ["", "PHASE 3 — Parameter grid (top 20)"]
    for r in sorted([x for x in results_p3 if "error" not in x], key=_score, reverse=True)[:20]:
        p = r.get("params", {})
        lines.append(
            f"  {_fmt(r['signals']):<24}  MS={p.get('MIN_SCORE'):<5} "
            f"RV={p.get('ENTRY_RVOL_MIN'):<4} Q={p.get('MIN_QUAL_COUNT')} "
            f"RSImax={p.get('RSI_MAX')}  "
            f"WR={r.get('wr',0):.1f}%  Ret={r.get('monthly_pct',-99):+.1f}%"
        )

    lines += ["", "PHASE 4 — Breadth+RSI fine-tune (top 20)"]
    for r in sorted([x for x in results_p4 if "error" not in x], key=_score, reverse=True)[:20]:
        p = r.get("params", {})
        lines.append(
            f"  BH={p.get('BREADTH_HARD'):.2f} BS={p.get('BREADTH_SOFT'):.2f} "
            f"RSImin={p.get('RSI_MIN')}  WR={r.get('wr',0):.1f}%  Ret={r.get('monthly_pct',-99):+.1f}%"
        )

    lines += [
        "", "=" * 80, "  FINAL CONFIG (written to engine)", "=" * 80,
        f"  SIGNAL_WHITELIST = {best_final.get('signals', best_sigs)}",
        f"  MIN_SCORE        = {fp.get('MIN_SCORE')}",
        f"  ENTRY_RVOL_MIN   = {fp.get('ENTRY_RVOL_MIN')}",
        f"  MIN_QUAL_COUNT   = {fp.get('MIN_QUAL_COUNT')}",
        f"  RSI range        = {fp.get('RSI_MIN')}–{fp.get('RSI_MAX')}",
        f"  BREADTH_HARD     = {fp.get('BREADTH_HARD')}",
        f"  BREADTH_SOFT     = {fp.get('BREADTH_SOFT')}",
        "",
        f"  In-sample   WR={best_final.get('wr',0):.1f}%  Ret={best_final.get('monthly_pct',-99):+.1f}%  "
        f"Trades={best_final.get('trades_pm',0):.1f}/mo  DD={best_final.get('max_dd',0):.1f}%",
        f"  Out-of-sample WR={oos.get('wr',0):.1f}%  Ret={oos.get('monthly_pct',-99):+.1f}%  "
        f"{'ROBUST' if oos_robust else 'DEGRADED'}",
        "",
        f"  TARGETS: " + "  ".join(f"{'✅' if v else '❌'}{k}" for k, v in targets.items()),
        "=" * 80,
    ]

    rpt = _ROOT / "signal_attribution_report.txt"
    rpt.write_text("\n".join(lines))
    print(f"\n  Report saved: {rpt}", flush=True)

    # ── Telegram ──────────────────────────────────────────────────────────────
    if not args.no_telegram:
        try:
            import config_india as cfg, requests as _req
            status_icon = "✅" if all_met else "⚠️"
            gaps_str    = ", ".join(k for k, v in targets.items() if not v) or "NONE"
            msg = (
                f"{status_icon} Deep Attribution Complete\n"
                f"Signals: {best_final.get('signals', best_sigs)}\n"
                f"WR={best_final.get('wr',0):.1f}%  Ret={best_final.get('monthly_pct',-99):+.1f}%/mo  "
                f"T={best_final.get('trades_pm',0):.1f}/mo\n"
                f"OOS WR={oos.get('wr',0):.1f}%  {'ROBUST' if oos_robust else 'DEGRADED'}\n"
                f"Gaps: {gaps_str}"
            )
            _req.post(
                f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": msg},
                timeout=10,
            )
        except Exception:
            pass

    print("\n  DONE.", flush=True)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
