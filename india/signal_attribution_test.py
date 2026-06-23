"""
signal_attribution_test.py — Comprehensive per-signal WR test

Tests every signal in isolation to find which ones ACTUALLY make money.
Runs all signals in one session using cached data.

Usage:
  python3 india/signal_attribution_test.py --no-telegram
  python3 india/signal_attribution_test.py --force-fetch   (re-download data)

Output:
  signal_attribution_report.txt  — ranked table of all signals
  Auto-patches backtest_engine_india.py with winners-only whitelist
"""

import argparse
import contextlib
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import backtest_engine_india as bk

CACHE_FILE = Path(__file__).parent / "optimizer_cache.pkl"

# Every signal name that can appear in the reason string
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

# Signals to always hard-deny (known losers from real trade data)
KNOWN_LOSERS = {"VWAP_BOUNCE_LONG", "ATR_SQUEEZE_BREAKOUT", "HAMMER_REVERSAL_LONG"}


def _load_cache():
    import pickle
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _fetch_fresh(max_symbols=30, days=60):
    import datetime
    import data_fetch_upstox as dfu
    from auth_upstox import get_upstox_client, verify_connection
    import pickle

    to_dt   = datetime.date.today()
    from_dt = to_dt - datetime.timedelta(days=days)
    from_s  = from_dt.strftime("%Y-%m-%d")
    to_s    = to_dt.strftime("%Y-%m-%d")

    TOP30 = [
        "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
        "HINDUNILVR","ITC","SBIN","BHARTIARTL","KOTAKBANK",
        "LT","AXISBANK","ASIANPAINT","MARUTI","BAJFINANCE",
        "TITAN","HCLTECH","SUNPHARMA","ULTRACEMCO","WIPRO",
        "ONGC","POWERGRID","NTPC","COALINDIA","TATAMOTORS",
        "JSWSTEEL","HINDALCO","TECHM","INDUSINDBK","BAJAJFINSV",
    ]

    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed.", flush=True)
        sys.exit(1)
    dfu.set_upstox_client(client)

    data = {}
    syms = TOP30[:max_symbols]
    print(f"  Fetching {len(syms)} symbols ({from_s} → {to_s}) ...", flush=True)
    t0 = time.time()
    for i, sym in enumerate(syms, 1):
        if i % 10 == 0:
            eta = (time.time()-t0)/i * (len(syms)-i)
            print(f"  {i}/{len(syms)} fetched  ETA:{eta/60:.0f}m", flush=True)
        try:
            df = bk._fetch(client, sym, from_s, to_s)
            if df is not None and len(df) > 10:
                data[sym] = df
        except Exception:
            pass
    print(f"  Fetched {len(data)} symbols in {(time.time()-t0)/60:.1f}m", flush=True)
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(data, f, protocol=4)
        print(f"  Saved to cache.", flush=True)
    except Exception:
        pass
    return data


def _run_signal_test(data, signal_name):
    """Run backtest with only this signal whitelisted. Returns metrics dict."""
    buf = io.StringIO()
    orig_whitelist = getattr(bk, "SIGNAL_WHITELIST", None)
    orig_min_score = bk.MIN_SCORE
    orig_adaptive  = bk._ADAPTIVE_MIN_SCORE
    orig_qual      = bk.MIN_QUAL_COUNT
    orig_rvol      = bk.ENTRY_RVOL_MIN
    orig_rsi_min   = bk.ENTRY_RSI_LONG_MIN
    orig_rsi_max   = bk.ENTRY_RSI_LONG_MAX
    orig_bhard     = bk.BREADTH_BULL_HARD
    orig_bsoft     = bk.BREADTH_BULL_SOFT
    try:
        # Set whitelist to just this signal
        bk.SIGNAL_WHITELIST = (signal_name,)
        # Relax all other filters so only the whitelist gate matters
        bk.MIN_SCORE = 6.0
        bk._ADAPTIVE_MIN_SCORE = 6.0
        bk.MIN_QUAL_COUNT = 1
        bk.ENTRY_RVOL_MIN = 1.0
        bk.ENTRY_RSI_LONG_MIN = 25
        bk.ENTRY_RSI_LONG_MAX = 85
        bk.BREADTH_BULL_HARD = 0.35
        bk.BREADTH_BULL_SOFT = 0.45
        with contextlib.redirect_stdout(buf):
            bk.run_backtest_from_data(data, capital=500_000.0)
        output = buf.getvalue()
    except Exception as e:
        return {"error": str(e), "signal": signal_name}
    finally:
        bk.SIGNAL_WHITELIST = orig_whitelist
        bk.MIN_SCORE = orig_min_score
        bk._ADAPTIVE_MIN_SCORE = orig_adaptive
        bk.MIN_QUAL_COUNT = orig_qual
        bk.ENTRY_RVOL_MIN = orig_rvol
        bk.ENTRY_RSI_LONG_MIN = orig_rsi_min
        bk.ENTRY_RSI_LONG_MAX = orig_rsi_max
        bk.BREADTH_BULL_HARD = orig_bhard
        bk.BREADTH_BULL_SOFT = orig_bsoft

    # Parse metrics
    import re
    metrics = {"signal": signal_name}
    m = re.search(r"Total trades\s*:\s*(\d+)", output)
    if m: metrics["trades"] = int(m.group(1))
    m = re.search(r"Win rate\s*:\s*([\d.]+)%", output)
    if m: metrics["wr"] = float(m.group(1))
    m = re.search(r"Monthly return\s*:\s*([-\d.]+)%", output)
    if m: metrics["monthly_pct"] = float(m.group(1))
    m = re.search(r"Period\s*:.*?(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})", output)
    if m:
        from datetime import datetime as _dt
        d1 = _dt.strptime(m.group(1), "%Y-%m-%d")
        d2 = _dt.strptime(m.group(2), "%Y-%m-%d")
        months = max((d2-d1).days/30.44, 0.1)
        metrics["trades_pm"] = metrics.get("trades", 0) / months
    return metrics


def _patch_whitelist(winners):
    """Write the winning signals as the SIGNAL_WHITELIST in backtest_engine_india.py"""
    import re
    engine = Path(__file__).parent / "backtest_engine_india.py"
    src = engine.read_text()
    whitelist_str = ", ".join(f'"{s}"' for s in winners)
    pattern = r'^SIGNAL_WHITELIST\s*=\s*\(.*?\)$'
    replacement = f'SIGNAL_WHITELIST = ({whitelist_str},)  # auto-set by signal_attribution_test'
    new_src, n = re.subn(pattern, replacement, src, flags=re.MULTILINE)
    if n:
        engine.write_text(new_src)
        print(f"\n  Engine patched: SIGNAL_WHITELIST = {winners}", flush=True)
    else:
        print(f"\n  WARNING: SIGNAL_WHITELIST line not found in engine.", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-fetch", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--max-symbols", type=int, default=30)
    args = ap.parse_args()

    print("=" * 65)
    print("  NSE Signal Attribution Test")
    print(f"  Testing {len(ALL_SIGNALS)} signals in isolation")
    print(f"  Goal: find every signal with WR>55% and Return>0%")
    print("=" * 65, flush=True)

    # Load data
    print("\n[1/3] Loading data ...", flush=True)
    data = None
    if not args.force_fetch:
        data = _load_cache()
        if data:
            data = dict(list(data.items())[:args.max_symbols])
            print(f"  Loaded {len(data)} symbols from cache.", flush=True)
    if not data:
        data = _fetch_fresh(max_symbols=args.max_symbols)

    if not data:
        print("ERROR: No data.", flush=True); sys.exit(1)

    # Save original constants
    orig = {k: getattr(bk, k) for k in [
        "MIN_SCORE","_ADAPTIVE_MIN_SCORE","MIN_QUAL_COUNT","ENTRY_RVOL_MIN",
        "ENTRY_RSI_LONG_MIN","ENTRY_RSI_LONG_MAX","BREADTH_BULL_HARD","BREADTH_BULL_SOFT"
    ]}

    print(f"\n[2/3] Testing {len(ALL_SIGNALS)} signals one by one ...\n", flush=True)
    results = []
    t0 = time.time()

    for i, sig in enumerate(ALL_SIGNALS, 1):
        elapsed = time.time() - t0
        eta = (elapsed/i)*(len(ALL_SIGNALS)-i) if i > 1 else 0
        print(f"  [{i:>2}/{len(ALL_SIGNALS)}] {sig:<30} ETA:{eta/60:.0f}m  ", end="", flush=True)

        # Temporarily patch backtest to only allow this signal
        # We inject a custom whitelist by writing to the module
        # The fastest way: override bk._PRIMARY_WHITELIST is not used by the loop,
        # instead we create a wrapper
        m = _run_signal_test(data, sig)
        wr    = m.get("wr", 0)
        tpm   = m.get("trades_pm", 0)
        ret   = m.get("monthly_pct", -99)
        trades = m.get("trades", 0)
        if "error" in m:
            print(f"ERROR: {m['error'][:50]}", flush=True)
        else:
            flag = "✅ WINNER" if wr >= 55 and ret > 0 and trades >= 3 else "❌"
            print(f"WR={wr:.1f}%  Trades={tpm:.1f}/mo  Return={ret:.1f}%  {flag}", flush=True)
        results.append(m)

    # Restore
    for k, v in orig.items():
        setattr(bk, k, v)

    # Sort by WR descending
    results.sort(key=lambda x: x.get("wr", 0), reverse=True)

    winners = [r["signal"] for r in results
               if r.get("wr", 0) >= 55 and r.get("monthly_pct", -99) > 0 and r.get("trades", 0) >= 3]

    print("\n" + "=" * 65)
    print("  RESULTS (ranked by WR)")
    print("=" * 65)
    print(f"  {'Signal':<30} {'WR':>6}  {'Trades/mo':>10}  {'Return':>8}")
    print(f"  {'-'*30} {'-'*6}  {'-'*10}  {'-'*8}")
    for r in results:
        wr  = r.get("wr", 0)
        tpm = r.get("trades_pm", 0)
        ret = r.get("monthly_pct", -99)
        flag = " ✅" if r["signal"] in winners else ""
        print(f"  {r['signal']:<30} {wr:>5.1f}%  {tpm:>9.1f}  {ret:>7.1f}%{flag}")

    print(f"\n  WINNERS ({len(winners)}): {', '.join(winners) if winners else 'NONE'}")
    print("=" * 65, flush=True)

    # Save report
    report_lines = ["=" * 65, "  NSE Signal Attribution Report", "=" * 65,
                    f"  {'Signal':<30} {'WR':>6}  {'Trades/mo':>10}  {'Return':>8}", ""]
    for r in results:
        wr  = r.get("wr", 0)
        tpm = r.get("trades_pm", 0)
        ret = r.get("monthly_pct", -99)
        flag = " WINNER" if r["signal"] in winners else ""
        report_lines.append(f"  {r['signal']:<30} {wr:>5.1f}%  {tpm:>9.1f}  {ret:>7.1f}%{flag}")
    report_lines.append(f"\n  WINNERS: {', '.join(winners) if winners else 'NONE — all signals losing'}")
    report_lines.append("=" * 65)

    report_path = Path(__file__).parent.parent / "signal_attribution_report.txt"
    report_path.write_text("\n".join(report_lines))
    print(f"\n  Report saved: {report_path}", flush=True)

    # Patch engine with winners
    if winners:
        print(f"\n[3/3] Patching engine with {len(winners)} winning signals ...", flush=True)
        _patch_whitelist(winners)
        print("\n  DONE. Run the bot — only winning signals will trade.", flush=True)
    else:
        print("\n  No signals met the WR>55% + Return>0% threshold.", flush=True)
        print("  Strategy needs deeper review. Best signal by WR:", flush=True)
        best = results[0] if results else {}
        print(f"    {best.get('signal','?')}: {best.get('wr',0):.1f}% WR", flush=True)

    # Telegram
    if not args.no_telegram:
        try:
            import config_india as cfg, requests as _req
            msg = (f"📊 Signal Attribution Complete\n"
                   f"Winners: {', '.join(winners) if winners else 'NONE'}\n"
                   f"Best WR: {results[0].get('wr',0):.1f}% ({results[0].get('signal','')})")
            _req.post(f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        except Exception:
            pass


if __name__ == "__main__":
    main()
