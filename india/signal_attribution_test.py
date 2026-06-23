"""
signal_attribution_test.py — Fast parallel comprehensive signal + parameter test

Phase 1 : All 18 signals tested individually in parallel   (~2-4 min)
Phase 2 : All pairs of top candidates in parallel          (~3-5 min)
Phase 3 : Full param grid on best combo in parallel        (~3-5 min)
Phase 4 : Patch engine + save report

Total runtime: ~10-15 min (vs 30-40 min single-threaded)

Usage:
  python3 india/signal_attribution_test.py --no-telegram
  python3 india/signal_attribution_test.py --force-fetch   # re-download data
"""

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

# Make imports work from any working directory
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

# ── Worker (runs in subprocess via fork) ─────────────────────────────────────
# Must be module-level for multiprocessing pickling.

_WORKER_DATA = None   # set once per worker via initializer


def _init_worker(data_bytes: bytes):
    """Called once per worker process — deserialise data into process memory."""
    global _WORKER_DATA
    _WORKER_DATA = pickle.loads(data_bytes)


def _run_task(task):
    """
    task = (signal_list, params_dict)
    Returns metrics dict.  Runs in a subprocess; each process has its own
    copy of backtest_engine_india so setting module-level attrs is thread-safe.
    """
    signal_names, params = task

    import backtest_engine_india as bk

    bk.SIGNAL_WHITELIST    = tuple(signal_names)
    bk.MIN_SCORE           = params["MIN_SCORE"]
    bk._ADAPTIVE_MIN_SCORE = params["MIN_SCORE"]
    bk.MIN_QUAL_COUNT      = params["MIN_QUAL_COUNT"]
    bk.ENTRY_RVOL_MIN      = params["ENTRY_RVOL_MIN"]
    bk.ENTRY_RSI_LONG_MIN  = params["RSI_MIN"]
    bk.ENTRY_RSI_LONG_MAX  = params["RSI_MAX"]
    bk.BREADTH_BULL_HARD   = params["BREADTH_HARD"]
    bk.BREADTH_BULL_SOFT   = params["BREADTH_SOFT"]

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            bk.run_backtest_from_data(_WORKER_DATA, capital=500_000.0)
    except Exception as e:
        return {"signals": list(signal_names), "params": params, "error": str(e)}

    output  = buf.getvalue()
    result  = {"signals": list(signal_names), "params": params}

    m = re.search(r"Total trades\s*:\s*(\d+)", output)
    if m: result["trades"] = int(m.group(1))

    m = re.search(r"Win rate\s*:\s*([\d.]+)%", output)
    if m: result["wr"] = float(m.group(1))

    m = re.search(r"Monthly return\s*:\s*([+-]?[\d.]+)%", output)
    if m: result["monthly_pct"] = float(m.group(1))

    m = re.search(r"Max drawdown\s*:\s*([\d.]+)%", output)
    if m: result["max_dd"] = float(m.group(1))

    m = re.search(r"Sharpe ratio\s*:\s*([-\d.]+)", output)
    if m: result["sharpe"] = float(m.group(1))

    m = re.search(r"Period\s*:.*?(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})", output)
    if m:
        from datetime import datetime as _dt
        d1 = _dt.strptime(m.group(1), "%Y-%m-%d")
        d2 = _dt.strptime(m.group(2), "%Y-%m-%d")
        months = max((d2 - d1).days / 30.44, 0.1)
        result["trades_pm"] = result.get("trades", 0) / months

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _score(r):
    """Composite ranking: reward WR×return, penalise drawdown."""
    wr  = r.get("wr", 0)
    ret = max(r.get("monthly_pct", -99), 0)
    dd  = max(r.get("max_dd", 1.0), 1.0)
    tpm = r.get("trades_pm", 0)
    if tpm < 5:
        return 0.0   # too few trades — not tradeable
    return (wr / 100) * ret / dd


def _is_winner(r):
    return (r.get("wr", 0) >= 55.0
            and r.get("monthly_pct", -99) >= 2.0
            and r.get("trades_pm", 0) >= 10.0
            and "error" not in r)


def _fmt_sigs(signals):
    return "+".join(signals)


def _print_table(results, title=""):
    ok = [r for r in results if "error" not in r and r.get("trades", 0) >= 1]
    ok.sort(key=_score, reverse=True)
    sep = "─" * 76
    print(f"\n{sep}")
    print(f"  {title}")
    print(f"  {'Signal(s)':<36} {'WR':>6}  {'T/mo':>6}  {'Ret%':>7}  {'DD%':>6}  {'Sharpe':>7}")
    print(f"  {'─'*36} {'─'*6}  {'─'*6}  {'─'*7}  {'─'*6}  {'─'*7}")
    for r in ok[:25]:
        sigs   = _fmt_sigs(r.get("signals", ["?"]))[:36]
        wr     = r.get("wr", 0)
        tpm    = r.get("trades_pm", 0)
        ret    = r.get("monthly_pct", -99)
        dd     = r.get("max_dd", 0)
        sharpe = r.get("sharpe", 0)
        flag   = " ✅" if _is_winner(r) else ""
        print(f"  {sigs:<36} {wr:>5.1f}%  {tpm:>5.1f}  {ret:>+6.1f}%  {dd:>5.1f}%  {sharpe:>6.2f}{flag}")
    print(sep, flush=True)


def _patch_engine(signals, params):
    """Rewrite key constants in backtest_engine_india.py."""
    src = ENGINE_FILE.read_text()

    whitelist_str = ", ".join(f'"{s}"' for s in signals)
    src, n1 = re.subn(
        r'^SIGNAL_WHITELIST\s*=\s*\(.*?\).*$',
        f'SIGNAL_WHITELIST = ({whitelist_str},)  # auto-set by attribution test',
        src, flags=re.MULTILINE,
    )
    src, n2 = re.subn(
        r'^MIN_SCORE\s*=\s*[\d.]+.*$',
        f'MIN_SCORE    = {params["MIN_SCORE"]}   # auto-optimized',
        src, flags=re.MULTILINE,
    )
    src, n3 = re.subn(
        r'^ENTRY_RVOL_MIN\s*=\s*[\d.]+.*$',
        f'ENTRY_RVOL_MIN     = {params["ENTRY_RVOL_MIN"]}   # auto-optimized',
        src, flags=re.MULTILINE,
    )
    src, n4 = re.subn(
        r'^MIN_QUAL_COUNT\s*=\s*\d+.*$',
        f'MIN_QUAL_COUNT     = {params["MIN_QUAL_COUNT"]}     # auto-optimized',
        src, flags=re.MULTILINE,
    )

    ENGINE_FILE.write_text(src)
    changed = sum([n1, n2, n3, n4])
    print(f"  Engine patched ({changed} lines changed):", flush=True)
    print(f"    SIGNAL_WHITELIST = {signals}")
    print(f"    MIN_SCORE        = {params['MIN_SCORE']}")
    print(f"    ENTRY_RVOL_MIN   = {params['ENTRY_RVOL_MIN']}")
    print(f"    MIN_QUAL_COUNT   = {params['MIN_QUAL_COUNT']}", flush=True)


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_data(args):
    if not args.force_fetch and CACHE_FILE.exists():
        with open(CACHE_FILE, "rb") as f:
            data = pickle.load(f)
        data = dict(list(data.items())[:args.max_symbols])
        print(f"  Loaded {len(data)} symbols from cache.", flush=True)
        return data

    # Fresh fetch from Upstox
    import datetime
    import data_fetch_upstox as dfu
    from auth_upstox import get_upstox_client, verify_connection
    import backtest_engine_india as bk

    to_dt   = datetime.date.today()
    from_dt = to_dt - datetime.timedelta(days=args.days)
    from_s  = from_dt.strftime("%Y-%m-%d")
    to_s    = to_dt.strftime("%Y-%m-%d")

    TOP30 = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
        "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
        "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "BAJFINANCE",
        "TITAN", "HCLTECH", "SUNPHARMA", "ULTRACEMCO", "WIPRO",
        "ONGC", "POWERGRID", "NTPC", "COALINDIA", "TATAMOTORS",
        "JSWSTEEL", "HINDALCO", "TECHM", "INDUSINDBK", "BAJAJFINSV",
    ]

    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed."); sys.exit(1)
    dfu.set_upstox_client(client)

    data = {}
    syms = TOP30[:args.max_symbols]
    print(f"  Fetching {len(syms)} symbols ({from_s} → {to_s}) ...", flush=True)
    t0 = time.time()
    for i, sym in enumerate(syms, 1):
        if i % 5 == 0:
            eta = (time.time() - t0) / i * (len(syms) - i)
            print(f"  {i}/{len(syms)}  ETA:{eta/60:.0f}m", flush=True)
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


# ── Parameter sets ────────────────────────────────────────────────────────────

def _relaxed():
    """Fully relaxed — only the whitelist gate matters."""
    return dict(MIN_SCORE=6.0, MIN_QUAL_COUNT=1, ENTRY_RVOL_MIN=1.0,
                RSI_MIN=25, RSI_MAX=85, BREADTH_HARD=0.35, BREADTH_SOFT=0.45)


def _param_grid():
    """Parameter combinations for Phase 3."""
    grid = []
    for ms in [8.0, 10.0, 12.0, 14.0, 16.0, 18.0]:
        for rv in [1.0, 1.3, 1.5, 1.8, 2.0]:
            for qc in [1, 2]:
                grid.append(dict(
                    MIN_SCORE=ms, ENTRY_RVOL_MIN=rv, MIN_QUAL_COUNT=qc,
                    RSI_MIN=30, RSI_MAX=80,
                    BREADTH_HARD=0.45, BREADTH_SOFT=0.55,
                ))
    return grid  # 6×5×2 = 60 combos


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-fetch",  action="store_true",
                    help="Re-download data even if cache exists")
    ap.add_argument("--no-telegram",  action="store_true")
    ap.add_argument("--max-symbols",  type=int, default=30)
    ap.add_argument("--days",         type=int, default=60)
    ap.add_argument("--workers",      type=int, default=0,
                    help="Number of parallel workers (0 = auto)")
    args = ap.parse_args()

    n_workers = args.workers or min(mp.cpu_count(), 8)

    print("=" * 76)
    print("  NSE Signal Attribution + Parameter Optimizer  (parallel)")
    print(f"  Phase 1 : {len(ALL_SIGNALS)} signals × individual")
    print(f"  Phase 2 : pairs of top candidates")
    print(f"  Phase 3 : 60-combo param grid on best signals")
    print(f"  Workers : {n_workers} CPU cores")
    print(f"  Target  : WR≥55%, Return≥4%/mo, Trades≥20/mo, DD<8%")
    print("=" * 76, flush=True)

    # ── Load data ──────────────────────────────────────────────────────────────
    print("\n[DATA] Loading ...", flush=True)
    data = _load_data(args)
    if not data:
        print("ERROR: No data loaded."); sys.exit(1)
    data_bytes = pickle.dumps(data, protocol=4)
    print(f"  Serialised: {len(data_bytes)/1e6:.1f} MB  ({len(data)} symbols)", flush=True)

    # ── Phase 1: all 18 signals individually ──────────────────────────────────
    print(f"\n[PHASE 1] {len(ALL_SIGNALS)} individual signals ...", flush=True)
    t0 = time.time()
    tasks_p1 = [([sig], _relaxed()) for sig in ALL_SIGNALS]
    with mp.Pool(n_workers, initializer=_init_worker, initargs=(data_bytes,)) as pool:
        results_p1 = pool.map(_run_task, tasks_p1)
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)
    _print_table(results_p1, "PHASE 1 — Individual signals (all filters relaxed)")

    # Candidates: WR≥45% and at least 3 total trades
    candidates = sorted(
        [r for r in results_p1 if r.get("wr", 0) >= 45 and r.get("trades", 0) >= 3 and "error" not in r],
        key=lambda r: r.get("wr", 0), reverse=True,
    )
    cand_sigs = [r["signals"][0] for r in candidates[:8]]
    print(f"\n  Candidates (WR≥45%): {cand_sigs}", flush=True)

    if not cand_sigs:
        print("  WARNING: No signal reached WR≥45%. Lowering threshold to top-4 by WR.")
        top4 = sorted(results_p1, key=lambda r: r.get("wr", 0), reverse=True)[:4]
        cand_sigs = [r["signals"][0] for r in top4 if "error" not in r]

    # ── Phase 2: pairs + triples of candidates ─────────────────────────────────
    tasks_p2: list = []
    # Include singles again for clean combined ranking
    for sig in cand_sigs:
        tasks_p2.append(([sig], _relaxed()))
    # All pairs
    for a, b in combinations(cand_sigs, 2):
        tasks_p2.append(([a, b], _relaxed()))
    # All triples (only if ≤6 candidates, else combos explode)
    if len(cand_sigs) <= 6:
        for a, b, c in combinations(cand_sigs, 3):
            tasks_p2.append(([a, b, c], _relaxed()))

    print(f"\n[PHASE 2] {len(tasks_p2)} signal combos (singles + pairs + triples) ...", flush=True)
    t0 = time.time()
    with mp.Pool(n_workers, initializer=_init_worker, initargs=(data_bytes,)) as pool:
        results_p2 = pool.map(_run_task, tasks_p2)
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)
    _print_table(results_p2, "PHASE 2 — Signal combinations")

    ok_p2 = [r for r in results_p2 if "error" not in r and r.get("trades", 0) >= 3]
    if ok_p2:
        best_p2 = max(ok_p2, key=_score)
        best_signals = best_p2["signals"]
    else:
        best_signals = ["ORB_BULL_CONFIRM"]
    print(f"\n  Best combo entering Phase 3: {best_signals}", flush=True)

    # ── Phase 3: parameter grid on best combo ──────────────────────────────────
    param_grid = _param_grid()
    tasks_p3 = [(best_signals, p) for p in param_grid]

    print(f"\n[PHASE 3] {len(tasks_p3)} parameter combos on {best_signals} ...", flush=True)
    t0 = time.time()
    with mp.Pool(n_workers, initializer=_init_worker, initargs=(data_bytes,)) as pool:
        results_p3 = pool.map(_run_task, tasks_p3)
    print(f"  Done in {time.time()-t0:.0f}s", flush=True)
    _print_table(results_p3, "PHASE 3 — Parameter grid")

    # ── Final: pick best overall ───────────────────────────────────────────────
    winners_p3 = sorted(
        [r for r in results_p3
         if r.get("wr", 0) >= 55
         and r.get("monthly_pct", -99) >= 2.0
         and r.get("trades_pm", 0) >= 10
         and r.get("max_dd", 100) <= 12.0
         and "error" not in r],
        key=_score, reverse=True,
    )

    print("\n" + "=" * 76)
    print("  FINAL RESULT")
    print("=" * 76, flush=True)

    if winners_p3:
        best = winners_p3[0]
    else:
        # Fall back: best by score with any trades
        fallback = [r for r in results_p3 if "error" not in r and r.get("trades", 0) >= 3]
        best = max(fallback, key=_score) if fallback else None

    if best:
        bp = best.get("params", _relaxed())
        wr   = best.get("wr", 0)
        ret  = best.get("monthly_pct", -99)
        tpm  = best.get("trades_pm", 0)
        dd   = best.get("max_dd", 0)
        sh   = best.get("sharpe", 0)

        print(f"  Signals  : {best['signals']}")
        print(f"  WR       : {wr:.1f}%    (target ≥55%)")
        print(f"  Return   : {ret:+.2f}%/mo  (target ≥4%)")
        print(f"  Trades   : {tpm:.1f}/mo  (target ≥20)")
        print(f"  Max DD   : {dd:.1f}%    (limit <8%)")
        print(f"  Sharpe   : {sh:.2f}    (target ≥1.5)")
        print(f"  Params   : MIN_SCORE={bp.get('MIN_SCORE')}  RVOL={bp.get('ENTRY_RVOL_MIN')}  QUAL={bp.get('MIN_QUAL_COUNT')}")
        print()

        meets = (wr >= 55 and ret >= 4.0 and tpm >= 20 and dd <= 8.0)
        if meets:
            print("  ✅ ALL TARGETS MET — ready to deploy on Friday with ₹25,000")
        else:
            gaps = []
            if wr  <  55: gaps.append(f"WR {wr:.1f}% < 55%")
            if ret <  4.0: gaps.append(f"Return {ret:.1f}% < 4%")
            if tpm < 20:   gaps.append(f"Trades {tpm:.1f}/mo < 20")
            if dd  >  8.0: gaps.append(f"DD {dd:.1f}% > 8%")
            print(f"  ⚠  Gaps: {', '.join(gaps)}")

        print(f"\n  Patching engine ...", flush=True)
        _patch_engine(best["signals"], bp)
    else:
        print("  No valid result found. Keeping current engine settings.")

    # ── Save report ────────────────────────────────────────────────────────────
    lines = [
        "=" * 76,
        "  NSE Signal Attribution Report",
        "=" * 76,
        "",
        "PHASE 1 — Individual signals",
        f"  {'Signal':<30} {'WR':>6}  {'T/mo':>6}  {'Ret%':>7}",
    ]
    for r in sorted(results_p1, key=lambda x: x.get("wr", 0), reverse=True):
        flag = "  WINNER" if _is_winner(r) else ""
        lines.append(
            f"  {r['signals'][0]:<30} {r.get('wr',0):>5.1f}%  "
            f"{r.get('trades_pm',0):>5.1f}  {r.get('monthly_pct',-99):>+6.1f}%{flag}"
        )

    lines += ["", "PHASE 2 — Signal combinations (top 20)"]
    for r in sorted(ok_p2, key=_score, reverse=True)[:20]:
        sigs = _fmt_sigs(r.get("signals", []))
        flag = "  WINNER" if _is_winner(r) else ""
        lines.append(
            f"  {sigs:<36} {r.get('wr',0):>5.1f}%  "
            f"{r.get('trades_pm',0):>5.1f}  {r.get('monthly_pct',-99):>+6.1f}%{flag}"
        )

    lines += ["", "PHASE 3 — Parameter grid (top 20 by score)"]
    for r in sorted([x for x in results_p3 if "error" not in x], key=_score, reverse=True)[:20]:
        sigs = _fmt_sigs(r.get("signals", []))
        p    = r.get("params", {})
        flag = "  WINNER" if _is_winner(r) else ""
        lines.append(
            f"  {sigs:<24} MS={p.get('MIN_SCORE'):<5} RV={p.get('ENTRY_RVOL_MIN'):<4} "
            f"Q={p.get('MIN_QUAL_COUNT')}  WR={r.get('wr',0):.1f}% "
            f"Ret={r.get('monthly_pct',-99):+.1f}%{flag}"
        )

    if best:
        lines += [
            "", "=" * 76, "  FINAL CONFIG", "=" * 76,
            f"  SIGNAL_WHITELIST = {best['signals']}",
            f"  MIN_SCORE        = {best.get('params', {}).get('MIN_SCORE')}",
            f"  ENTRY_RVOL_MIN   = {best.get('params', {}).get('ENTRY_RVOL_MIN')}",
            f"  MIN_QUAL_COUNT   = {best.get('params', {}).get('MIN_QUAL_COUNT')}",
            f"  WR={best.get('wr',0):.1f}%  Return={best.get('monthly_pct',-99):+.1f}%/mo  "
            f"Trades={best.get('trades_pm',0):.1f}/mo  DD={best.get('max_dd',0):.1f}%",
        ]

    rpt_path = _ROOT / "signal_attribution_report.txt"
    rpt_path.write_text("\n".join(lines))
    print(f"\n  Report saved: {rpt_path}", flush=True)

    # ── Telegram ───────────────────────────────────────────────────────────────
    if not args.no_telegram and best:
        try:
            import config_india as cfg, requests as _req
            msg = (
                f"📊 Attribution complete\n"
                f"Signals: {best['signals']}\n"
                f"WR={best.get('wr',0):.1f}%  Return={best.get('monthly_pct',-99):+.1f}%/mo  "
                f"Trades={best.get('trades_pm',0):.1f}/mo"
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
