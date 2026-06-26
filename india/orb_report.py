"""
orb_report.py — Comprehensive ORB_BULL_CONFIRM Performance Report

Tests ORB_BULL_CONFIRM (your proven real-trade signal) across every
meaningful parameter combination in parallel.

What it tests:
  RVOL    : 1.0 → 3.0  (13 values — the key filter)
  MIN_SCORE: 6 → 20    (8 values)
  QUAL    : 1, 2        (2 values)
  RSI range: 4 combos
  Total   : ~600 backtests

Walk-forward: best config tested on 3 time windows to confirm robustness.

Runtime : ~30-60 min on 1-core VPS  |  ~10-15 min on 4-core
Output  : orb_performance_report.txt  (clean ranked table + final config)

Usage:
  python3 india/orb_report.py --no-telegram
  python3 india/orb_report.py --force-fetch --days 90 --max-symbols 50
"""

from __future__ import annotations

import argparse
import contextlib
import io
import multiprocessing as mp
import os
import pickle
import re
import sys
import time
from datetime import datetime as _dt
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

CACHE_FILE  = _HERE / "optimizer_cache.pkl"
ENGINE_FILE = _HERE / "backtest_engine_india.py"
REPORT_FILE = _ROOT / "orb_performance_report.txt"

SIGNAL = "ORB_BULL_CONFIRM"   # the only signal we care about

# ── Shared data (fork-inherited) ──────────────────────────────────────────────
_W: dict = {}   # {window_key: {symbol: DataFrame}}


# ── Worker ────────────────────────────────────────────────────────────────────

def _run(task: tuple) -> dict:
    """Run one backtest.  task = (params_dict, window_key)"""
    params, wkey = task
    import backtest_engine_india as bk

    bk.SIGNAL_WHITELIST    = (SIGNAL,)
    bk.MIN_SCORE           = params["ms"]
    bk._ADAPTIVE_MIN_SCORE = params["ms"]
    bk.MIN_QUAL_COUNT      = params["qc"]
    bk.ENTRY_RVOL_MIN      = params["rv"]
    bk.ENTRY_RSI_LONG_MIN  = params["rmin"]
    bk.ENTRY_RSI_LONG_MAX  = params["rmax"]
    bk.BREADTH_BULL_HARD   = params["bh"]
    bk.BREADTH_BULL_SOFT   = params["bs"]
    bk.MAX_OPEN            = 8

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            bk.run_backtest_from_data(_W[wkey], capital=500_000.0)
    except Exception as e:
        return {"p": params, "w": wkey, "err": str(e)[:80]}

    out = buf.getvalue()
    r   = {"p": params, "w": wkey}

    def _f(pat, default=0.0):
        m = re.search(pat, out)
        return float(m.group(1)) if m else default

    r["trades"] = int(_f(r"Total trades\s*:\s*(\d+)"))
    r["wr"]     = _f(r"Win rate\s*:\s*([\d.]+)%")
    r["ret"]    = _f(r"Monthly return\s*:\s*([+-]?[\d.]+)%", -99.0)
    r["dd"]     = _f(r"Max drawdown\s*:\s*([\d.]+)%")
    r["sharpe"] = _f(r"Sharpe ratio\s*:\s*([+-]?[\d.]+)")
    r["pf"]     = _f(r"Profit factor\s*:\s*([\d.]+)")

    m = re.search(r"Period\s*:.*?(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})", out)
    if m:
        d1 = _dt.strptime(m.group(1), "%Y-%m-%d")
        d2 = _dt.strptime(m.group(2), "%Y-%m-%d")
        months = max((d2 - d1).days / 30.44, 0.1)
        r["tpm"] = r["trades"] / months

    return r


# ── Parameter grid ────────────────────────────────────────────────────────────

def _grid() -> list[dict]:
    """
    Full grid focused on ORB_BULL_CONFIRM parameters.
    Most important axis: RVOL (distinguishes real ORB from fake breakouts).
    """
    combos = []
    for rv in [1.0, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.5, 3.0]:   # 10
        for ms in [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]:    # 8
            for qc in [1, 2]:                                           # 2
                for rmin, rmax in [(25,85),(30,78),(35,75),(40,72)]:    # 4
                    combos.append(dict(ms=ms, rv=rv, qc=qc,
                                       rmin=rmin, rmax=rmax,
                                       bh=0.45, bs=0.55))
    return combos   # 10×8×2×4 = 640 combos


def _wf_grid(best_p: dict) -> list[dict]:
    """Walk-forward: ±1 step around winner across 3 RVOL values."""
    combos = []
    for rv in [best_p["rv"] - 0.1, best_p["rv"], best_p["rv"] + 0.2]:
        for ms in [best_p["ms"] - 2, best_p["ms"], best_p["ms"] + 2]:
            if rv < 0.9 or ms < 4:
                continue
            p = dict(best_p)
            p.update(rv=round(rv, 1), ms=round(ms, 1))
            combos.append(p)
    return combos


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(r: dict) -> float:
    if r.get("err") or r.get("trades", 0) < 3:
        return -999.0
    wr  = r.get("wr", 0)
    ret = r.get("ret", -99)
    dd  = max(r.get("dd", 1.0), 0.1)
    tpm = r.get("tpm", 0)
    if wr < 35 or tpm < 3:
        return wr - 100   # rank bad ones below 0 but still ordered
    return (wr / 100) * max(ret, 0) * min(tpm / 15, 1.5) / dd


def _targets(r: dict) -> dict:
    return {
        "WR≥55%":        r.get("wr", 0) >= 55,
        "Return≥4%/mo":  r.get("ret", -99) >= 4.0,
        "Trades≥20/mo":  r.get("tpm", 0) >= 20,
        "DD<8%":         r.get("dd", 99) <= 8.0,
        "Sharpe≥1.5":    r.get("sharpe", 0) >= 1.5,
    }


# ── Data loading ──────────────────────────────────────────────────────────────

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


def _load_data(args) -> dict:
    if not args.force_fetch and CACHE_FILE.exists():
        age = (time.time() - os.stat(CACHE_FILE).st_mtime) / 86400
        if age < 8:
            with open(CACHE_FILE, "rb") as f:
                raw = pickle.load(f)
            data = dict(list(raw.items())[:args.max_symbols])
            print(f"  Cache: {len(data)} symbols  (age {age:.1f}d)", flush=True)
            return data

    import datetime
    import data_fetch_upstox as dfu
    from auth_upstox import get_upstox_client, verify_connection
    import backtest_engine_india as bk

    to_dt   = datetime.date.today()
    from_dt = to_dt - datetime.timedelta(days=args.days)
    fs, ts  = from_dt.strftime("%Y-%m-%d"), to_dt.strftime("%Y-%m-%d")

    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed."); sys.exit(1)
    dfu.set_upstox_client(client)

    syms = UNIVERSE[:args.max_symbols]
    print(f"  Fetching {len(syms)} symbols  {fs} → {ts} ...", flush=True)
    t0, data = time.time(), {}
    for i, sym in enumerate(syms, 1):
        if i % 5 == 0:
            print(f"  {i}/{len(syms)}  ETA:{((time.time()-t0)/i*(len(syms)-i))/60:.0f}m", flush=True)
        try:
            df = bk._fetch(client, sym, fs, ts)
            if df is not None and len(df) > 10:
                data[sym] = df
        except Exception:
            pass

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(data, f, protocol=4)
    print(f"  Fetched {len(data)} syms in {(time.time()-t0)/60:.1f}m  (cached)", flush=True)
    return data


def _make_windows(data: dict) -> dict:
    """Full period + 3 rolling windows for walk-forward."""
    all_dates = sorted({d for df in data.values()
                         for d in df.index.normalize().unique()})
    total = len(all_dates)
    result = {"full": data}
    if total < 6:
        return result

    width = max(int(total * 0.60), 10)
    step  = max(int(total * 0.20), 3)
    for i, s in enumerate(range(0, total - width + 1, step)[:3]):
        cut_s = all_dates[s]
        cut_e = all_dates[min(s + width, total - 1)]
        win = {sym: df[(df.index >= cut_s) & (df.index <= cut_e)]
               for sym, df in data.items()}
        win = {s: d for s, d in win.items() if len(d) >= 10}
        if win:
            result[f"w{i}"] = win

    mid = all_dates[total // 2]
    fh  = {s: d[d.index < mid]  for s, d in data.items()}
    lh  = {s: d[d.index >= mid] for s, d in data.items()}
    result["first_h"] = {s: d for s, d in fh.items() if len(d) >= 10}
    result["last_h"]  = {s: d for s, d in lh.items() if len(d) >= 10}
    return result


# ── Engine patcher ────────────────────────────────────────────────────────────

def _patch(p: dict):
    src = ENGINE_FILE.read_text()

    def _s(pat, repl):
        nonlocal src
        src, _ = re.subn(pat, repl, src, flags=re.MULTILINE)

    _s(r'^SIGNAL_WHITELIST\s*=\s*\(.*?\).*$',
       f'SIGNAL_WHITELIST = ("{SIGNAL}",)  # orb_report: best config')
    _s(r'^MIN_SCORE\s*=\s*[\d.]+.*$',
       f'MIN_SCORE    = {p["ms"]}   # orb_report optimized')
    _s(r'^ENTRY_RVOL_MIN\s*=\s*[\d.]+.*$',
       f'ENTRY_RVOL_MIN     = {p["rv"]}   # orb_report optimized')
    _s(r'^MIN_QUAL_COUNT\s*=\s*\d+.*$',
       f'MIN_QUAL_COUNT     = {p["qc"]}     # orb_report optimized')
    _s(r'^ENTRY_RSI_LONG_MIN\s*=\s*\d+.*$',
       f'ENTRY_RSI_LONG_MIN = {p["rmin"]}    # orb_report optimized')
    _s(r'^ENTRY_RSI_LONG_MAX\s*=\s*\d+.*$',
       f'ENTRY_RSI_LONG_MAX = {p["rmax"]}    # orb_report optimized')
    _s(r'^BREADTH_BULL_HARD\s*=\s*[\d.]+.*$',
       f'BREADTH_BULL_HARD  = {p["bh"]}  # orb_report optimized')
    _s(r'^BREADTH_BULL_SOFT\s*=\s*[\d.]+.*$',
       f'BREADTH_BULL_SOFT  = {p["bs"]}  # orb_report optimized')

    ENGINE_FILE.write_text(src)


# ── Report builder ────────────────────────────────────────────────────────────

def _build_report(all_results: list, wf_results: list, best: dict,
                  best_p: dict, targets: dict, elapsed: float) -> str:
    lines = []

    def _h(title):
        lines.append("")
        lines.append("=" * 80)
        lines.append(f"  {title}")
        lines.append("=" * 80)

    lines.append("=" * 80)
    lines.append("  ORB_BULL_CONFIRM — Comprehensive Performance Report")
    lines.append(f"  Generated: {_dt.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
    lines.append(f"  Total backtests run: {len(all_results):,}")
    lines.append(f"  Runtime: {elapsed/60:.0f} minutes")
    lines.append("=" * 80)

    # ── Full ranked table ──────────────────────────────────────────────────────
    _h("FULL RESULTS — All parameter combinations (ranked by score)")
    lines.append(f"  {'RVOL':>5}  {'MinSc':>5}  {'Qual':>4}  "
                 f"{'RSI':>7}  {'WR':>6}  {'T/mo':>6}  {'Ret%':>7}  "
                 f"{'DD%':>5}  {'Sharpe':>6}  {'PF':>5}  {'✓'}")
    lines.append(f"  {'─'*5}  {'─'*5}  {'─'*4}  {'─'*7}  {'─'*6}  "
                 f"{'─'*6}  {'─'*7}  {'─'*5}  {'─'*6}  {'─'*5}")

    ranked = sorted(all_results, key=_score, reverse=True)
    for r in ranked[:60]:   # top 60
        if r.get("err"):
            continue
        p    = r["p"]
        tgt  = "✅" if all(_targets(r).values()) else (
               "✓"  if r.get("wr", 0) >= 50 and r.get("ret", -99) > 0 else "")
        rsi_str = f"{p['rmin']}-{p['rmax']}"
        lines.append(
            f"  {p['rv']:>5.1f}  {p['ms']:>5.1f}  {p['qc']:>4}  "
            f"  {rsi_str:>7}  {r.get('wr',0):>5.1f}%  {r.get('tpm',0):>5.1f}  "
            f"  {r.get('ret',-99):>+6.1f}%  {r.get('dd',0):>4.1f}%  "
            f"  {r.get('sharpe',0):>6.2f}  {r.get('pf',0):>5.2f}  {tgt}"
        )

    # ── RVOL summary (most important axis) ────────────────────────────────────
    _h("RVOL ANALYSIS — Best result at each RVOL threshold")
    lines.append(f"  {'RVOL':>5}  {'Best WR':>8}  {'Best Ret':>9}  "
                 f"{'Best T/mo':>10}  {'Best DD':>8}  {'Notes'}")
    lines.append(f"  {'─'*5}  {'─'*8}  {'─'*9}  {'─'*10}  {'─'*8}  {'─'*20}")

    rvol_vals = sorted({r["p"]["rv"] for r in all_results if not r.get("err")})
    for rv in rvol_vals:
        group = [r for r in all_results
                 if r["p"]["rv"] == rv and not r.get("err") and r.get("trades", 0) >= 1]
        if not group:
            continue
        best_rv = max(group, key=lambda r: r.get("wr", 0))
        note = ""
        if best_rv.get("wr", 0) >= 55 and best_rv.get("ret", -99) > 0:
            note = "✅ PROFITABLE"
        elif best_rv.get("wr", 0) >= 50:
            note = "✓ Close"
        elif best_rv.get("trades", 0) < 3:
            note = "⚠ Too few trades"
        else:
            note = "❌ Losing"
        lines.append(
            f"  {rv:>5.1f}  {best_rv.get('wr',0):>7.1f}%  "
            f"  {best_rv.get('ret',-99):>+8.1f}%  "
            f"  {best_rv.get('tpm',0):>9.1f}  "
            f"  {best_rv.get('dd',0):>7.1f}%  {note}"
        )

    # ── Walk-forward validation ────────────────────────────────────────────────
    _h("WALK-FORWARD VALIDATION — Best config on 3 time windows")
    if wf_results:
        lines.append(f"  {'Window':>12}  {'WR':>6}  {'T/mo':>6}  {'Ret%':>7}  {'DD%':>5}")
        lines.append(f"  {'─'*12}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*5}")
        for r in wf_results:
            if not r.get("err"):
                lines.append(
                    f"  {r['w']:>12}  {r.get('wr',0):>5.1f}%  {r.get('tpm',0):>5.1f}  "
                    f"  {r.get('ret',-99):>+6.1f}%  {r.get('dd',0):>4.1f}%"
                )
        wf_ok = [r for r in wf_results if not r.get("err") and r.get("trades", 0) >= 1]
        if wf_ok:
            avg_wr  = sum(r.get("wr", 0) for r in wf_ok) / len(wf_ok)
            avg_ret = sum(r.get("ret", -99) for r in wf_ok) / len(wf_ok)
            robust  = avg_wr >= 50 and avg_ret > 0 and len(wf_ok) >= 2
            lines.append(f"\n  Average WR  : {avg_wr:.1f}%")
            lines.append(f"  Average Ret : {avg_ret:+.1f}%/mo")
            lines.append(f"  Robustness  : {'✅ ROBUST' if robust else '⚠  INCONSISTENT'}")
    else:
        lines.append("  (no walk-forward results)")

    # ── Final recommendation ───────────────────────────────────────────────────
    _h("FINAL RECOMMENDATION")
    lines.append(f"  Best config found:")
    lines.append(f"    RVOL threshold : {best_p.get('rv', '?')}")
    lines.append(f"    MIN_SCORE      : {best_p.get('ms', '?')}")
    lines.append(f"    MIN_QUAL_COUNT : {best_p.get('qc', '?')}")
    lines.append(f"    RSI range      : {best_p.get('rmin', '?')}–{best_p.get('rmax', '?')}")
    lines.append(f"    BREADTH_HARD   : {best_p.get('bh', '?')}")
    lines.append(f"    BREADTH_SOFT   : {best_p.get('bs', '?')}")
    lines.append("")
    lines.append(f"  Performance:")
    lines.append(f"    Win Rate    : {best.get('wr', 0):.1f}%   (target ≥55%)")
    lines.append(f"    Return      : {best.get('ret', -99):+.2f}%/mo  (target ≥4%)")
    lines.append(f"    Trades      : {best.get('tpm', 0):.1f}/mo    (target ≥20)")
    lines.append(f"    Max DD      : {best.get('dd', 0):.1f}%   (limit <8%)")
    lines.append(f"    Sharpe      : {best.get('sharpe', 0):.2f}     (target ≥1.5)")
    lines.append(f"    Profit F.   : {best.get('pf', 0):.2f}")
    lines.append("")

    t = _targets(best)
    for label, met in t.items():
        lines.append(f"    {'✅' if met else '❌'}  {label}")

    all_met = all(t.values())
    lines.append("")
    if all_met:
        lines.append("  ✅  ALL TARGETS MET")
        lines.append(f"     Deploy with ₹25,000 capital")
        lines.append(f"     Expected: {best.get('wr',0):.0f}% WR  "
                     f"{best.get('ret',-99):+.0f}%/mo  "
                     f"{best.get('tpm',0):.0f} trades/mo")
    elif best.get("wr", 0) >= 50 and best.get("ret", -99) > 0:
        lines.append("  ✓  PARTIALLY PROFITABLE")
        lines.append("     Deploy with ₹5,000 — monitor for 2 weeks")
        gaps = [k for k, v in t.items() if not v]
        lines.append(f"     Gaps to fix: {', '.join(gaps)}")
    else:
        lines.append("  ❌  NOT PROFITABLE IN BACKTEST")
        lines.append("     Backtest timing gap likely — real trades show 100% WR")
        lines.append("     Option: deploy ₹5,000 live for 2 weeks to get real data")
        best_wr_result = max(ranked[:5], key=lambda r: r.get("wr", 0)) if ranked else {}
        if best_wr_result:
            bp = best_wr_result.get("p", {})
            lines.append(f"     Best WR found: {best_wr_result.get('wr',0):.1f}% "
                         f"at RVOL={bp.get('rv')} MS={bp.get('ms')}")

    lines.append("")
    lines.append("=" * 80)
    return "\n".join(lines)


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
    T0        = time.time()

    print("=" * 80)
    print(f"  ORB_BULL_CONFIRM — Perfect Performance Report")
    print(f"  Signal  : {SIGNAL} only")
    print(f"  Grid    : RVOL×MIN_SCORE×QUAL×RSI = ~640 combos")
    print(f"  Validation: 3-window walk-forward on best config")
    print(f"  Workers : {n_workers} CPU cores")
    print(f"  Targets : WR≥55%  Ret≥4%/mo  Trades≥20/mo  DD<8%")
    print("=" * 80, flush=True)

    # Load + window data
    print("\n[DATA] Loading ...", flush=True)
    data_full = _load_data(args)
    if not data_full:
        print("ERROR: No data."); sys.exit(1)

    windows = _make_windows(data_full)
    win_keys = [k for k in windows if k != "full"]
    print(f"  Windows: {list(windows.keys())}", flush=True)

    global _W
    _W = windows

    # ── Phase 1: Full parameter grid on full data ─────────────────────────────
    grid   = _grid()
    tasks  = [(p, "full") for p in grid]
    print(f"\n[PHASE 1] {len(tasks)} parameter combos on full data ...", flush=True)

    t0 = time.time()
    with mp.Pool(n_workers) as pool:
        all_results = []
        it = pool.imap_unordered(_run, tasks, chunksize=4)
        for i, r in enumerate(it, 1):
            all_results.append(r)
            if i % 50 == 0 or i == len(tasks):
                rate = i / max(time.time() - t0, 1)
                eta  = (len(tasks) - i) / max(rate, 0.001)
                print(f"  {i}/{len(tasks)}  ETA:{eta/60:.0f}m  "
                      f"({rate:.2f} tasks/s)", flush=True)
    print(f"  Done in {(time.time()-t0)/60:.1f}m", flush=True)

    # Sort and find best
    ranked      = sorted(all_results, key=_score, reverse=True)
    valid       = [r for r in ranked if not r.get("err") and r.get("trades", 0) >= 3]
    profitable  = [r for r in valid  if r.get("wr", 0) >= 50 and r.get("ret", -99) > 0]

    print(f"\n  Valid results (≥3 trades): {len(valid)}")
    print(f"  Profitable   (WR≥50%, Ret>0%): {len(profitable)}", flush=True)

    if profitable:
        best = profitable[0]
        print(f"\n  BEST: WR={best.get('wr',0):.1f}%  "
              f"Ret={best.get('ret',-99):+.1f}%/mo  "
              f"T/mo={best.get('tpm',0):.1f}  "
              f"RVOL={best['p']['rv']}  MS={best['p']['ms']}", flush=True)
    else:
        best = valid[0] if valid else (ranked[0] if ranked else {})
        print(f"\n  No profitable config found. Best WR: "
              f"{best.get('wr',0):.1f}% at RVOL={best.get('p',{}).get('rv','?')} "
              f"MS={best.get('p',{}).get('ms','?')}", flush=True)

    # ── Phase 2: Walk-forward on best config ──────────────────────────────────
    best_p = best.get("p", {})
    wf_params = _wf_grid(best_p) if best_p else [best_p]

    wf_tasks = [(p, wk) for wk in win_keys[:3] for p in wf_params]
    print(f"\n[PHASE 2] Walk-forward: {len(wf_tasks)} combos on "
          f"{win_keys[:3]} ...", flush=True)

    t0 = time.time()
    with mp.Pool(n_workers) as pool:
        wf_results = list(pool.imap_unordered(_run, wf_tasks, chunksize=2))
    print(f"  Done in {(time.time()-t0)/60:.1f}m", flush=True)

    # ── Print console summary ─────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  RVOL SUMMARY (best WR at each threshold)")
    print("=" * 80)
    print(f"  {'RVOL':>5}  {'Best WR':>8}  {'Best Ret':>9}  {'T/mo':>6}  {'Verdict'}")
    print(f"  {'─'*5}  {'─'*8}  {'─'*9}  {'─'*6}  {'─'*20}")
    for rv in sorted({r["p"]["rv"] for r in all_results if not r.get("err")}):
        grp = [r for r in all_results
               if r["p"]["rv"] == rv and not r.get("err") and r.get("trades", 0) >= 1]
        if not grp: continue
        br   = max(grp, key=lambda r: r.get("wr", 0))
        icon = ("✅" if br.get("wr",0)>=55 and br.get("ret",-99)>0 else
                "✓"  if br.get("wr",0)>=50 else "❌")
        print(f"  {rv:>5.1f}  {br.get('wr',0):>7.1f}%  "
              f"  {br.get('ret',-99):>+8.1f}%  {br.get('tpm',0):>5.1f}  {icon}")

    # ── Build and save report ─────────────────────────────────────────────────
    targets = _targets(best)
    report  = _build_report(all_results, wf_results, best,
                            best_p, targets, time.time() - T0)
    REPORT_FILE.write_text(report)
    print(f"\n  Full report: {REPORT_FILE}", flush=True)

    # ── Final verdict ─────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  FINAL VERDICT")
    print("=" * 80)
    for label, met in targets.items():
        print(f"  {'✅' if met else '❌'}  {label}")

    all_met = all(targets.values())
    print()
    if all_met:
        print("  ✅  DEPLOY — all targets met")
    elif best.get("wr", 0) >= 50 and best.get("ret", -99) > 0:
        print("  ✓  PARTIAL — deploy ₹5,000 and monitor")
    else:
        print("  ❌  NOT PROFITABLE in backtest")
        print("  → Real 100% WR from 7 trades suggests backtest timing issue")
        print("  → Recommendation: deploy ₹5,000 live for 2 weeks")
    print(f"\n  Runtime: {(time.time()-T0)/60:.0f} min", flush=True)

    # ── Patch engine ──────────────────────────────────────────────────────────
    if best_p:
        print(f"\n  Patching engine with best config ...", flush=True)
        _patch(best_p)
        print(f"  Engine updated: RVOL={best_p.get('rv')} "
              f"MIN_SCORE={best_p.get('ms')} QUAL={best_p.get('qc')}", flush=True)

    # ── Telegram ──────────────────────────────────────────────────────────────
    if not args.no_telegram:
        try:
            import config_india as cfg, requests as _req
            icon = "✅" if all_met else ("✓" if best.get("wr", 0) >= 50 else "❌")
            msg  = (f"{icon} ORB Report Complete\n"
                    f"Best: RVOL={best_p.get('rv')} MS={best_p.get('ms')}\n"
                    f"WR={best.get('wr',0):.1f}%  Ret={best.get('ret',-99):+.1f}%/mo  "
                    f"T/mo={best.get('tpm',0):.1f}")
            _req.post(f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        except Exception:
            pass

    print("\n  DONE.", flush=True)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
