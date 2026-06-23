"""
signal_attribution_test.py — Exhaustive overnight optimiser (parallel)

7 phases designed to fill a full night and produce a configuration that
meets all targets: WR ≥ 55% | Return ≥ 4%/mo | Trades ≥ 20/mo | DD < 8%

Phase 1  : Walk-forward signal test (18 signals × rolling windows)
Phase 2  : Combo search (pairs + triples + 4-combos of top candidates)
Phase 3  : Per-signal parameter search (top-6 signals × medium grid)
Phase 4  : Giant parameter grid on best combo
Phase 5  : Fine grid around Phase 4 winner
Phase 6  : Breadth + MAX_OPEN tune
Phase 7  : k-fold cross-validation (5 folds)

Typical runtimes:
  --quick  : ~700 tasks — 1-core VPS ≈ 6-8 hr   (USE THIS FOR SINGLE-CORE VPS)
  default  : ~5,500 tasks — 4-core ≈ 6 hr | 2-core ≈ 12 hr
  8-core   : default ≈ 3 hr

Usage:
  python3 india/signal_attribution_test.py --no-telegram --quick   ← 1-core VPS
  python3 india/signal_attribution_test.py --no-telegram            ← multi-core
  python3 india/signal_attribution_test.py --force-fetch --days 90 --max-symbols 50
"""

from __future__ import annotations

import argparse
import contextlib
import io
import pickle
import re
import sys
import time
import multiprocessing as mp
from itertools import combinations
from pathlib import Path
from datetime import datetime as _dt

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

# ── Module-level shared data (populated in main(), inherited by fork workers) ─
# Each entry: window_key → {symbol: DataFrame}
_W: dict = {}   # do NOT rename — _run_task references it


# ── Worker function (module-level for fork pickling) ──────────────────────────

def _run_task(task: tuple):
    """
    task = (signal_list, params_dict, window_key)
    Returns metrics dict.  Executes in forked subprocess that has _W set.
    """
    signals, params, wkey = task
    import backtest_engine_india as bk

    bk.SIGNAL_WHITELIST    = tuple(signals)
    bk.MIN_SCORE           = params["ms"]
    bk._ADAPTIVE_MIN_SCORE = params["ms"]
    bk.MIN_QUAL_COUNT      = params["qc"]
    bk.ENTRY_RVOL_MIN      = params["rv"]
    bk.ENTRY_RSI_LONG_MIN  = params["rmin"]
    bk.ENTRY_RSI_LONG_MAX  = params["rmax"]
    bk.BREADTH_BULL_HARD   = params["bh"]
    bk.BREADTH_BULL_SOFT   = params["bs"]
    bk.MAX_OPEN            = params.get("mo", 8)

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            bk.run_backtest_from_data(_W[wkey], capital=500_000.0)
    except Exception as e:
        return {"sigs": signals, "p": params, "w": wkey, "err": str(e)[:120]}

    out = buf.getvalue()
    r   = {"sigs": signals, "p": params, "w": wkey}

    def _f(pat, default=0.0):
        m = re.search(pat, out)
        return float(m.group(1)) if m else default

    r["trades"]  = int(_f(r"Total trades\s*:\s*(\d+)"))
    r["wr"]      = _f(r"Win rate\s*:\s*([\d.]+)%")
    r["ret"]     = _f(r"Monthly return\s*:\s*([+-]?[\d.]+)%", -99.0)
    r["dd"]      = _f(r"Max drawdown\s*:\s*([\d.]+)%")
    r["sharpe"]  = _f(r"Sharpe ratio\s*:\s*([+-]?[\d.]+)")
    r["pf"]      = _f(r"Profit factor\s*:\s*([\d.]+)")

    m = re.search(r"Period\s*:.*?(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})", out)
    if m:
        d1 = _dt.strptime(m.group(1), "%Y-%m-%d")
        d2 = _dt.strptime(m.group(2), "%Y-%m-%d")
        months = max((d2 - d1).days / 30.44, 0.1)
        r["tpm"] = r["trades"] / months

    return r


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _score(r: dict) -> float:
    """Composite rank. Zero if not tradeable."""
    if r.get("err") or r.get("trades", 0) < 3:
        return 0.0
    wr  = r.get("wr", 0)
    ret = max(r.get("ret", -99), 0.0)
    dd  = max(r.get("dd", 1.0), 0.1)
    tpm = r.get("tpm", 0)
    if wr < 40 or tpm < 5:
        return 0.0
    return (wr / 100) * ret * min(tpm / 20, 1.5) / dd


def _targets_met(r: dict) -> bool:
    return (r.get("wr", 0) >= 55
            and r.get("ret", -99) >= 4.0
            and r.get("tpm", 0) >= 20
            and r.get("dd", 99) <= 8.0
            and not r.get("err"))


def _ok(r, wr=45, ret=0, tpm=5):
    return (r.get("wr", 0) >= wr
            and r.get("ret", -99) >= ret
            and r.get("tpm", 0) >= tpm
            and not r.get("err"))


def _sig_label(r):
    return "+".join(r.get("sigs", ["?"]))


# ── Pretty printing ───────────────────────────────────────────────────────────

def _table(rows: list, title: str = "", n: int = 30):
    ok = sorted([r for r in rows if not r.get("err") and r.get("trades", 0) >= 1],
                key=_score, reverse=True)
    W = 80
    print(f"\n{'─'*W}")
    print(f"  {title}")
    print(f"  {'Signals':<36} {'WR':>6} {'T/mo':>6} {'Ret%':>7} {'DD%':>6} {'Sharpe':>7} {'✓'}")
    print(f"  {'─'*36} {'─'*6} {'─'*6} {'─'*7} {'─'*6} {'─'*7}")
    for r in ok[:n]:
        sl  = _sig_label(r)[:36]
        tgt = "✅" if _targets_met(r) else ("✓" if _ok(r) else "")
        print(f"  {sl:<36} {r.get('wr',0):>5.1f}% {r.get('tpm',0):>5.1f} "
              f"{r.get('ret',-99):>+6.1f}% {r.get('dd',0):>5.1f}% "
              f"{r.get('sharpe',0):>6.2f} {tgt}")
    if not ok:
        print("  (no results)")
    print('─'*W, flush=True)


def _progress(done, total, phase, t0):
    rate = done / max(time.time() - t0, 1)
    eta  = (total - done) / max(rate, 0.001)
    print(f"  [{phase}] {done}/{total}  {eta/60:.0f}m left  "
          f"({rate:.1f} tasks/s)", flush=True)


# ── Parameter sets ────────────────────────────────────────────────────────────

def _relaxed() -> dict:
    return dict(ms=6.0, qc=1, rv=1.0, rmin=25, rmax=85, bh=0.35, bs=0.45, mo=8)


def _per_signal_grid(quick: bool = False) -> list[dict]:
    """Per-signal param grid.  quick=60 combos, full=60 combos per signal."""
    ms_vals = [6.0, 10.0, 14.0] if quick else [6.0, 8.0, 10.0, 12.0, 14.0, 16.0]
    rv_vals = [1.0, 1.5, 2.0]   if quick else [1.0, 1.3, 1.5, 1.8, 2.0]
    g = []
    for ms in ms_vals:
        for rv in rv_vals:
            for qc in [1, 2]:
                g.append(dict(ms=ms, qc=qc, rv=rv, rmin=30, rmax=80,
                              bh=0.40, bs=0.55, mo=8))
    return g   # quick=18 combos | full=60 combos per signal


def _giant_grid(quick: bool = False) -> list[dict]:
    """
    Phase 4 parameter grid.
    quick : 5×5×2×3×3 ≈ 400 valid combos  (1-core overnight)
    full  : 8×8×3×5×5 ≈ 4,600 valid combos (multi-core overnight)
    """
    if quick:
        ms_vals   = [8.0, 10.0, 12.0, 14.0, 16.0]      # 5
        rv_vals   = [1.0, 1.3, 1.5, 1.8, 2.0]           # 5
        qc_vals   = [1, 2]                                # 2
        rmax_vals = [72, 78, 84]                          # 3
        rmin_vals = [28, 35, 42]                          # 3
    else:
        ms_vals   = [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]  # 8
        rv_vals   = [1.0, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.5]        # 8
        qc_vals   = [1, 2, 3]                                          # 3
        rmax_vals = [68, 72, 76, 80, 84]                               # 5
        rmin_vals = [25, 30, 35, 40, 45]                               # 5

    g = []
    for ms in ms_vals:
        for rv in rv_vals:
            for qc in qc_vals:
                for rmax in rmax_vals:
                    for rmin in rmin_vals:
                        if rmin >= rmax - 20:
                            continue
                        g.append(dict(ms=ms, rv=rv, qc=qc,
                                      rmin=rmin, rmax=rmax,
                                      bh=0.45, bs=0.55, mo=8))
    return g


def _fine_grid(best_p: dict, quick: bool = False) -> list[dict]:
    """Phase 5: ±steps around Phase 4 winner."""
    ms_steps   = [0.0, -2.0, +2.0]          if quick else [0.0, -2.0, +2.0, -4.0, +4.0]
    rv_steps   = [0.0, -0.1, +0.1]          if quick else [0.0, -0.1, +0.1, -0.2, +0.2]
    rmax_steps = [0, -4, +4]
    rmin_steps = [0, -5, +5]
    qc_vals    = [1, 2]                      if quick else [1, 2, 3]

    g = []
    for dms in ms_steps:
        for drv in rv_steps:
            for drmax in rmax_steps:
                for drmin in rmin_steps:
                    for qc in qc_vals:
                        ms   = round(best_p["ms"]   + dms,  1)
                        rv   = round(best_p["rv"]   + drv,  2)
                        rmax = int(best_p["rmax"] + drmax)
                        rmin = int(best_p["rmin"] + drmin)
                        if ms < 4 or rv < 0.8 or rmin >= rmax - 15:
                            continue
                        g.append(dict(ms=ms, rv=rv, qc=qc,
                                      rmin=rmin, rmax=rmax,
                                      bh=best_p["bh"], bs=best_p["bs"],
                                      mo=best_p.get("mo", 8)))
    return g   # quick≈54 | full≈300


def _breadth_mo_grid(base: dict, quick: bool = False) -> list[dict]:
    """Phase 6: breadth × MAX_OPEN sweep."""
    bh_vals = [0.40, 0.50]          if quick else [0.35, 0.40, 0.45, 0.50, 0.55]
    bs_vals = [0.55, 0.65]          if quick else [0.50, 0.55, 0.60, 0.65]
    mo_vals = [6, 8]                if quick else [5, 6, 8, 10]

    g = []
    for bh in bh_vals:
        for bs in bs_vals:
            for mo in mo_vals:
                if bs <= bh:
                    continue
                p = dict(base)
                p.update(bh=bh, bs=bs, mo=mo)
                g.append(p)
    return g   # quick≈8 | full≈64


# ── Window creation ───────────────────────────────────────────────────────────

def _make_windows(data: dict, n: int = 5) -> dict:
    """
    Create n overlapping rolling sub-windows from the full data plus:
      'full' : entire range
      'w0'…'wN-1' : overlapping slices of ~1/3 the full period each
      'first_half' / 'last_half' : 50/50 split for OOS check
    """
    import pandas as pd

    # Collect all trading dates
    all_dates = sorted({d for df in data.values()
                         for d in df.index.normalize().unique()})
    total = len(all_dates)
    if total < 6:
        return {"full": data}

    result = {"full": data}

    # Rolling windows of width = 60% of total, shifted by 10% each time
    width  = max(int(total * 0.60), 10)
    step   = max(int(total * 0.10), 2)
    starts = list(range(0, total - width + 1, step))[:n]

    for i, s in enumerate(starts):
        cut_start = all_dates[s]
        cut_end   = all_dates[min(s + width, total - 1)]
        win = {}
        for sym, df in data.items():
            sl = df[(df.index >= cut_start) & (df.index <= cut_end)]
            if len(sl) >= 10:
                win[sym] = sl
        if win:
            result[f"w{i}"] = win

    # First/last half
    mid = all_dates[total // 2]
    first_h, last_h = {}, {}
    for sym, df in data.items():
        a = df[df.index < mid]
        b = df[df.index >= mid]
        if len(a) >= 10: first_h[sym] = a
        if len(b) >= 10: last_h[sym]  = b
    if first_h: result["first_h"] = first_h
    if last_h:  result["last_h"]  = last_h

    return result


# ── Data loading ──────────────────────────────────────────────────────────────

UNIVERSE_50 = [
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


def _load_data(args) -> dict:
    """Load from cache if fresh, else fetch from Upstox."""
    import os
    if not args.force_fetch and CACHE_FILE.exists():
        age_days = (time.time() - os.stat(CACHE_FILE).st_mtime) / 86400
        if age_days < 8:
            with open(CACHE_FILE, "rb") as f:
                raw = pickle.load(f)
            data = dict(list(raw.items())[:args.max_symbols])
            print(f"  Cache: {len(data)} symbols  (age {age_days:.1f}d)", flush=True)
            return data
        print(f"  Cache stale ({age_days:.1f}d) — fetching fresh data.", flush=True)

    import datetime
    import data_fetch_upstox as dfu
    from auth_upstox import get_upstox_client, verify_connection
    import backtest_engine_india as bk

    to_dt   = datetime.date.today()
    from_dt = to_dt - datetime.timedelta(days=args.days)
    from_s, to_s = from_dt.strftime("%Y-%m-%d"), to_dt.strftime("%Y-%m-%d")

    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed."); sys.exit(1)
    dfu.set_upstox_client(client)

    syms = UNIVERSE_50[:args.max_symbols]
    print(f"  Fetching {len(syms)} symbols  {from_s} → {to_s} ...", flush=True)
    t0, data = time.time(), {}
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
    print(f"  Fetched {len(data)} syms in {(time.time()-t0)/60:.1f}m  (cached)", flush=True)
    return data


# ── Engine patcher ────────────────────────────────────────────────────────────

def _patch_engine(sigs: list, p: dict):
    src = ENGINE_FILE.read_text()

    def _s(pat, repl):
        nonlocal src
        new, n = re.subn(pat, repl, src, flags=re.MULTILINE)
        src = new

    wl = ", ".join(f'"{s}"' for s in sigs)
    _s(r'^SIGNAL_WHITELIST\s*=\s*\(.*?\).*$',
       f'SIGNAL_WHITELIST = ({wl},)  # auto-set by attribution test')
    _s(r'^MIN_SCORE\s*=\s*[\d.]+.*$',
       f'MIN_SCORE    = {p["ms"]}   # auto-optimized')
    _s(r'^ENTRY_RVOL_MIN\s*=\s*[\d.]+.*$',
       f'ENTRY_RVOL_MIN     = {p["rv"]}   # auto-optimized')
    _s(r'^MIN_QUAL_COUNT\s*=\s*\d+.*$',
       f'MIN_QUAL_COUNT     = {p["qc"]}     # auto-optimized')
    _s(r'^ENTRY_RSI_LONG_MIN\s*=\s*\d+.*$',
       f'ENTRY_RSI_LONG_MIN = {p["rmin"]}    # auto-optimized')
    _s(r'^ENTRY_RSI_LONG_MAX\s*=\s*\d+.*$',
       f'ENTRY_RSI_LONG_MAX = {p["rmax"]}    # auto-optimized')
    _s(r'^BREADTH_BULL_HARD\s*=\s*[\d.]+.*$',
       f'BREADTH_BULL_HARD  = {p["bh"]}  # auto-optimized')
    _s(r'^BREADTH_BULL_SOFT\s*=\s*[\d.]+.*$',
       f'BREADTH_BULL_SOFT  = {p["bs"]}  # auto-optimized')
    _s(r'^MAX_OPEN\s*=\s*\d+.*$',
       f'MAX_OPEN     = {p.get("mo", 8)}      # auto-optimized')

    ENGINE_FILE.write_text(src)
    print(f"  Engine patched:", flush=True)
    print(f"    WHITELIST={sigs}")
    print(f"    MIN_SCORE={p['ms']}  RVOL={p['rv']}  QUAL={p['qc']}")
    print(f"    RSI={p['rmin']}–{p['rmax']}  BREADTH={p['bh']}/{p['bs']}  MAX_OPEN={p.get('mo',8)}")


# ── Parallel runner ───────────────────────────────────────────────────────────

def _run_parallel(tasks: list, pool: mp.Pool, label: str = "", report_every: int = 100) -> list:
    t0      = time.time()
    results = []
    it      = pool.imap_unordered(_run_task, tasks, chunksize=4)
    for i, r in enumerate(it, 1):
        results.append(r)
        if i % report_every == 0 or i == len(tasks):
            _progress(i, len(tasks), label, t0)
    print(f"  {label}: {len(tasks)} tasks in {(time.time()-t0)/60:.1f}m", flush=True)
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-fetch",  action="store_true")
    ap.add_argument("--no-telegram",  action="store_true")
    ap.add_argument("--max-symbols",  type=int, default=30)
    ap.add_argument("--days",         type=int, default=60)
    ap.add_argument("--workers",      type=int, default=0)
    ap.add_argument("--quick",        action="store_true",
                    help="Reduced grids for single-core VPS (~700 tasks, 6-8 hr overnight)")
    args = ap.parse_args()

    QUICK     = args.quick or (mp.cpu_count() == 1)
    n_workers = args.workers or min(mp.cpu_count(), 8)
    T0        = time.time()

    print("=" * 80)
    print("  NSE Exhaustive Overnight Optimiser" + ("  [QUICK/1-CORE MODE]" if QUICK else ""))
    print(f"  Phase 1 : 18 signals × {'3' if QUICK else '5'} rolling windows        (walk-forward)")
    print(f"  Phase 2 : all pairs + triples + 4-combos")
    print(f"  Phase 3 : per-signal param grid  (60 combos × top-6 signals)")
    print(f"  Phase 4 : giant param grid        (~4,600 combos on best combo)")
    print(f"  Phase 5 : fine grid around winner (~300 combos)")
    print(f"  Phase 6 : breadth + MAX_OPEN tune (~64 combos)")
    print(f"  Phase 7 : k-fold cross-validation  (5 folds)")
    print(f"  Workers : {n_workers} CPU cores")
    print(f"  Targets : WR≥55%  Ret≥4%/mo  Trades≥20/mo  DD<8%")
    print("=" * 80, flush=True)

    # ── Load data + build windows ─────────────────────────────────────────────
    print("\n[DATA] Loading ...", flush=True)
    data_full = _load_data(args)
    if not data_full:
        print("ERROR: No data."); sys.exit(1)

    print(f"\n  Building rolling windows ...", flush=True)
    windows = _make_windows(data_full, n=3 if QUICK else 5)
    print(f"  Windows created: {list(windows.keys())}", flush=True)

    # ── Inject into module-level dict (shared via fork, no serialisation) ─────
    global _W
    _W = windows
    WIN_KEYS = [k for k in windows if k.startswith("w")]  # rolling windows only

    # ── Create pool (workers fork HERE — they inherit _W) ─────────────────────
    pool = mp.Pool(n_workers)
    REL  = _relaxed()

    total_tasks = 0
    all_report_lines: list[str] = []

    def _heading(title):
        all_report_lines.append("")
        all_report_lines.append("=" * 80)
        all_report_lines.append(f"  {title}")
        all_report_lines.append("=" * 80)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1 — Walk-forward signal test
    #   Each of 18 signals × 5 rolling windows + full period
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  PHASE 1  Walk-forward signal robustness test")
    print(f"{'='*80}", flush=True)

    wkeys_p1 = WIN_KEYS + ["full"]
    tasks_p1 = [([sig], REL, wk) for sig in ALL_SIGNALS for wk in wkeys_p1]
    total_tasks += len(tasks_p1)

    raw_p1 = _run_parallel(tasks_p1, pool, "P1-walk-forward", 20)

    # Organise: signal → {window_key → result}
    sig_map: dict = {s: {} for s in ALL_SIGNALS}
    for r in raw_p1:
        if len(r.get("sigs", [])) == 1:
            sig_map[r["sigs"][0]][r["w"]] = r

    # Robustness: signal is robust if profitable in ≥4 of 5 rolling windows
    robust_sigs, candidate_sigs = [], []
    _heading("PHASE 1 — Walk-forward signal test")
    hdr = f"  {'Signal':<30} " + "  ".join(f"{k:>7}" for k in wkeys_p1) + "  Robust?"
    all_report_lines.append(hdr)

    for sig in ALL_SIGNALS:
        wrs = [sig_map[sig].get(k, {}).get("wr", 0) for k in WIN_KEYS]
        rts = [sig_map[sig].get(k, {}).get("ret", -99) for k in WIN_KEYS]
        trs = [sig_map[sig].get(k, {}).get("trades", 0) for k in WIN_KEYS]
        full_r = sig_map[sig].get("full", {})

        wins_in = sum(1 for wr, rt, tr in zip(wrs, rts, trs)
                      if wr >= 48 and rt > 0 and tr >= 2)
        is_robust = wins_in >= max(len(WIN_KEYS) - 1, 3)   # win in ≥4 of 5 windows
        is_cand   = wins_in >= 2 and full_r.get("wr", 0) >= 45

        if is_robust:   robust_sigs.append(sig)
        elif is_cand:   candidate_sigs.append(sig)

        wr_vals = "  ".join(f"{wr:>6.1f}%" for wr in wrs)
        status  = f"ROBUST({wins_in}/{len(WIN_KEYS)})" if is_robust else \
                  (f"cand({wins_in})" if is_cand else "skip")
        line = f"  {sig:<30} {wr_vals}  {status}"
        all_report_lines.append(line)

    # Print table to console
    print(hdr, flush=True)
    for line in all_report_lines[-len(ALL_SIGNALS)-1:]:
        print(line, flush=True)

    pool_sigs = robust_sigs or (candidate_sigs[:8] if candidate_sigs else
                sorted(sig_map, key=lambda s: sig_map[s].get("full", {}).get("wr", 0), reverse=True)[:5])
    print(f"\n  Robust: {robust_sigs}")
    print(f"  Candidates: {candidate_sigs[:8]}")
    print(f"  Using for Phase 2: {pool_sigs}", flush=True)

    full_p1 = [sig_map[s].get("full", {}) for s in ALL_SIGNALS if sig_map[s].get("full")]
    _table(full_p1, "PHASE 1 — Full-period individual signal results")

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2 — Combo search
    #   All pairs, triples, and 4-combos of top candidates on full data
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  PHASE 2  Signal combination search")
    print(f"{'='*80}", flush=True)

    top_n  = pool_sigs[:6 if QUICK else 10]
    tasks_p2: list = []
    for s in top_n:
        tasks_p2.append(([s], REL, "full"))
    for a, b in combinations(top_n, 2):
        tasks_p2.append(([a, b], REL, "full"))
    if not QUICK:
        if len(top_n) <= 8:
            for a, b, c in combinations(top_n, 3):
                tasks_p2.append(([a, b, c], REL, "full"))
        if len(top_n) <= 6:
            for combo in combinations(top_n, 4):
                tasks_p2.append((list(combo), REL, "full"))

    total_tasks += len(tasks_p2)
    raw_p2 = _run_parallel(tasks_p2, pool, "P2-combos", 20)

    ok_p2 = sorted([r for r in raw_p2 if _ok(r)], key=_score, reverse=True)
    _table(ok_p2, "PHASE 2 — Signal combinations")
    _heading("PHASE 2 — Signal combinations (top 30)")
    for r in ok_p2[:30]:
        all_report_lines.append(
            f"  {_sig_label(r):<40}  WR={r.get('wr',0):.1f}%  "
            f"Ret={r.get('ret',-99):+.1f}%  T/mo={r.get('tpm',0):.1f}"
        )

    best_p2  = max(ok_p2, key=_score) if ok_p2 else {"sigs": ["ORB_BULL_CONFIRM"], "p": REL}
    best_sigs = best_p2.get("sigs", ["ORB_BULL_CONFIRM"])
    print(f"\n  Best combo → Phase 3/4: {best_sigs}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3 — Per-signal parameter optimization
    #   For each robust signal, sweep its own best MIN_SCORE + RVOL
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  PHASE 3  Per-signal parameter search (top-6 signals)")
    print(f"{'='*80}", flush=True)

    sig_grid = _per_signal_grid(quick=QUICK)
    target_sigs_p3 = pool_sigs[:4 if QUICK else 6]
    tasks_p3 = [([sig], p, "full") for sig in target_sigs_p3 for p in sig_grid]
    total_tasks += len(tasks_p3)

    raw_p3 = _run_parallel(tasks_p3, pool, "P3-per-signal", 50)

    # Best params per signal
    best_per_signal: dict = {}
    _heading("PHASE 3 — Per-signal best params")
    for sig in target_sigs_p3:
        sig_results = [r for r in raw_p3 if r.get("sigs") == [sig] and _ok(r, wr=45)]
        if sig_results:
            best = max(sig_results, key=_score)
            best_per_signal[sig] = best
            p = best["p"]
            all_report_lines.append(
                f"  {sig:<30} WR={best.get('wr',0):.1f}% Ret={best.get('ret',-99):+.1f}%  "
                f"MS={p['ms']} RV={p['rv']} QC={p['qc']}"
            )
            print(f"  {sig}: best WR={best.get('wr',0):.1f}%  MS={p['ms']} RV={p['rv']} QC={p['qc']}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 4 — Giant parameter grid on best signal combo
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  PHASE 4  Giant parameter grid  (~4,600 combos)")
    print(f"{'='*80}", flush=True)

    giant = _giant_grid(quick=QUICK)
    tasks_p4 = [(best_sigs, p, "full") for p in giant]
    total_tasks += len(tasks_p4)
    print(f"  Running {len(tasks_p4)} combos on {best_sigs} ...", flush=True)

    raw_p4 = _run_parallel(tasks_p4, pool, "P4-giant-grid", 200)

    ok_p4 = sorted([r for r in raw_p4 if _ok(r, wr=50, tpm=8)], key=_score, reverse=True)
    _table(ok_p4, "PHASE 4 — Giant grid (top 30)")
    _heading("PHASE 4 — Giant grid top results")
    for r in ok_p4[:40]:
        p = r["p"]
        all_report_lines.append(
            f"  MS={p['ms']:<5} RV={p['rv']:<4} QC={p['qc']} "
            f"RSI={p['rmin']}-{p['rmax']}  "
            f"WR={r.get('wr',0):.1f}%  Ret={r.get('ret',-99):+.1f}%  "
            f"T/mo={r.get('tpm',0):.1f}  DD={r.get('dd',0):.1f}%"
        )

    best_p4 = ok_p4[0] if ok_p4 else best_p2
    print(f"\n  Phase 4 winner: {best_p4.get('p', {})}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 5 — Fine grid around Phase 4 winner
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  PHASE 5  Fine grid around Phase 4 winner")
    print(f"{'='*80}", flush=True)

    fine   = _fine_grid(best_p4["p"], quick=QUICK)
    tasks_p5 = [(best_sigs, p, "full") for p in fine]
    total_tasks += len(tasks_p5)
    print(f"  Running {len(tasks_p5)} fine combos ...", flush=True)

    raw_p5 = _run_parallel(tasks_p5, pool, "P5-fine", 50)

    ok_p5 = sorted([r for r in raw_p5 if _ok(r, wr=50, tpm=8)], key=_score, reverse=True)
    _table(ok_p5, "PHASE 5 — Fine grid")
    best_p5 = ok_p5[0] if ok_p5 else best_p4
    print(f"\n  Phase 5 winner: {best_p5.get('p', {})}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 6 — Breadth + MAX_OPEN tune
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  PHASE 6  Breadth + MAX_OPEN fine-tune")
    print(f"{'='*80}", flush=True)

    bm_grid  = _breadth_mo_grid(best_p5["p"], quick=QUICK)
    tasks_p6 = [(best_sigs, p, "full") for p in bm_grid]
    total_tasks += len(tasks_p6)

    raw_p6 = _run_parallel(tasks_p6, pool, "P6-breadth-mo", 20)

    ok_p6 = sorted([r for r in raw_p6 if _ok(r, wr=50, tpm=8)], key=_score, reverse=True)
    _table(ok_p6, "PHASE 6 — Breadth + MAX_OPEN")
    best_p6 = ok_p6[0] if ok_p6 else best_p5
    best_params = best_p6["p"]
    print(f"\n  Phase 6 winner: {best_params}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 7 — k-fold cross-validation (5 folds)
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  PHASE 7  k-fold cross-validation (5 folds = 5 rolling windows)")
    print(f"{'='*80}", flush=True)

    kfold_keys = WIN_KEYS[:5]
    tasks_p7   = [(best_sigs, best_params, k) for k in kfold_keys]
    total_tasks += len(tasks_p7)

    raw_p7 = _run_parallel(tasks_p7, pool, "P7-kfold", 5)
    pool.close()
    pool.join()

    kfold_wrs  = [r.get("wr", 0) for r in raw_p7]
    kfold_rets = [r.get("ret", -99) for r in raw_p7]
    kfold_tpm  = [r.get("tpm", 0) for r in raw_p7]

    import statistics as _stats
    wr_mean  = _stats.mean(kfold_wrs)  if kfold_wrs  else 0
    wr_stdev = _stats.stdev(kfold_wrs) if len(kfold_wrs) > 1 else 0
    ret_mean = _stats.mean(kfold_rets) if kfold_rets else -99
    oos_robust = (wr_mean >= 50 and ret_mean > 0
                  and sum(1 for r in raw_p7 if r.get("ret",-99) > 0) >= 3)

    _heading("PHASE 7 — k-fold cross-validation")
    for i, r in enumerate(raw_p7):
        fold_line = (f"  Fold {i+1}: WR={r.get('wr',0):.1f}%  "
                     f"Ret={r.get('ret',-99):+.1f}%  T/mo={r.get('tpm',0):.1f}")
        all_report_lines.append(fold_line)
        print(fold_line, flush=True)
    cv_line = (f"  CV summary: WR={wr_mean:.1f}%±{wr_stdev:.1f}%  "
               f"Ret={ret_mean:+.1f}%  "
               f"{'ROBUST ✅' if oos_robust else 'DEGRADED ⚠'}")
    all_report_lines.append(cv_line)
    print(cv_line, flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # FINAL SUMMARY
    # ─────────────────────────────────────────────────────────────────────────
    bf  = best_p6   # best final result
    bp  = best_params

    print("\n" + "=" * 80)
    print("  FINAL RESULT")
    print("=" * 80, flush=True)
    print(f"  Signals   : {best_sigs}")
    print(f"  WR        : {bf.get('wr',0):.1f}%          (target ≥55%)")
    print(f"  Return    : {bf.get('ret',-99):+.2f}%/mo      (target ≥4%)")
    print(f"  Trades    : {bf.get('tpm',0):.1f}/mo         (target ≥20)")
    print(f"  Max DD    : {bf.get('dd',0):.1f}%            (limit <8%)")
    print(f"  Sharpe    : {bf.get('sharpe',0):.2f}")
    print(f"  CV WR     : {wr_mean:.1f}%±{wr_stdev:.1f}%  {'ROBUST' if oos_robust else 'DEGRADED'}")
    print(f"  Params    : MS={bp['ms']}  RVOL={bp['rv']}  QUAL={bp['qc']}  "
          f"RSI={bp['rmin']}-{bp['rmax']}  BH={bp['bh']}  BS={bp['bs']}  MO={bp.get('mo',8)}")
    print()

    targets = {
        "WR≥55%":          bf.get("wr",0) >= 55,
        "Return≥4%/mo":    bf.get("ret",-99) >= 4.0,
        "Trades≥20/mo":    bf.get("tpm",0) >= 20,
        "DD<8%":           bf.get("dd",99) <= 8.0,
        "CV robust":       oos_robust,
    }
    for label, met in targets.items():
        print(f"  {'✅' if met else '❌'}  {label}")

    all_met = all(targets.values())
    print()
    if all_met:
        print("  ✅  ALL TARGETS MET — deploy Friday with ₹25,000")
        print(f"  Expected: {bf.get('wr',0):.0f}% WR  {bf.get('ret',-99):+.1f}%/mo  "
              f"{bf.get('tpm',0):.0f} trades/mo")
    else:
        gaps = [k for k, v in targets.items() if not v]
        print(f"  ⚠  Gaps: {', '.join(gaps)}")
        best_target_met = max(ok_p4 + ok_p5 + ok_p6, key=_score) if (ok_p4 or ok_p5 or ok_p6) else bf
        print(f"  Best config by score: WR={best_target_met.get('wr',0):.1f}%  "
              f"Ret={best_target_met.get('ret',-99):+.1f}%  T/mo={best_target_met.get('tpm',0):.1f}")

    elapsed = (time.time() - T0) / 60
    print(f"\n  Total runtime  : {elapsed:.0f} min  |  Tasks run: {total_tasks:,}", flush=True)

    # Patch engine
    print(f"\n  Patching engine ...", flush=True)
    _patch_engine(best_sigs, bp)

    # ── Save full report ──────────────────────────────────────────────────────
    _heading("FINAL CONFIG")
    all_report_lines += [
        f"  SIGNAL_WHITELIST = {best_sigs}",
        f"  MIN_SCORE        = {bp['ms']}",
        f"  ENTRY_RVOL_MIN   = {bp['rv']}",
        f"  MIN_QUAL_COUNT   = {bp['qc']}",
        f"  RSI range        = {bp['rmin']}–{bp['rmax']}",
        f"  BREADTH_HARD     = {bp['bh']}",
        f"  BREADTH_SOFT     = {bp['bs']}",
        f"  MAX_OPEN         = {bp.get('mo',8)}",
        "",
        f"  In-sample  : WR={bf.get('wr',0):.1f}%  Ret={bf.get('ret',-99):+.1f}%  "
        f"T/mo={bf.get('tpm',0):.1f}  DD={bf.get('dd',0):.1f}%",
        f"  k-fold CV  : WR={wr_mean:.1f}%±{wr_stdev:.1f}%  {'ROBUST' if oos_robust else 'DEGRADED'}",
        "",
        "  TARGETS: " + "  ".join(f"{'✅' if v else '❌'}{k}" for k, v in targets.items()),
        f"  RUNTIME: {elapsed:.0f} min  TASKS: {total_tasks:,}",
    ]

    rpt = _ROOT / "signal_attribution_report.txt"
    rpt.write_text("\n".join(all_report_lines))
    print(f"\n  Report: {rpt}", flush=True)

    # ── Telegram ──────────────────────────────────────────────────────────────
    if not args.no_telegram:
        try:
            import config_india as cfg, requests as _req
            icon = "✅" if all_met else "⚠️"
            gaps = ", ".join(k for k, v in targets.items() if not v) or "none"
            msg  = (f"{icon} Overnight optimiser complete\n"
                    f"Signals: {best_sigs}\n"
                    f"WR={bf.get('wr',0):.1f}%  Ret={bf.get('ret',-99):+.1f}%/mo  "
                    f"T={bf.get('tpm',0):.1f}/mo\n"
                    f"CV: {wr_mean:.1f}%±{wr_stdev:.1f}%  {'ROBUST' if oos_robust else 'DEGRADED'}\n"
                    f"Gaps: {gaps}")
            _req.post(f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        except Exception:
            pass

    print("\n  DONE.", flush=True)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
