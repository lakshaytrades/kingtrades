"""
backtest_today.py — run the VALIDATED live config on TODAY's real market, trade by trade.

Shows exactly what the pilot WOULD have done today: every entry (time, price, stop,
target), every exit (reason, price), and the P&L — using the same rvol>=4 / 1.5xATR
stop / 3:1 target logic that runs live. A concrete preview of tomorrow.

Needs a valid Upstox token (fetches today's intraday data). Run after the token step:
  python3 india/backtest_today.py
  python3 india/backtest_today.py --cache midcap_1y.pkl   # or backtest a cached day
"""
from __future__ import annotations
import argparse, datetime, pickle, sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE)); sys.path.insert(0, str(_HERE.parent))

import orb_fast as of
import strategy_lab as sl
from live_pilot import TOP_LIQUID, RV_MIN, WIDE_SL, MAX_RR
from fetch_midcaps import HIGH_VOL_UNIVERSE


def _fetch(days: int, universe: list) -> dict:
    import data_fetch_upstox as dfu
    from auth_upstox import get_upstox_client, verify_connection
    import backtest_engine_india as bk
    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed — refresh the token first "
              "(python3 india/get_upstox_token.py)."); sys.exit(1)
    dfu.set_upstox_client(client)
    to = datetime.date.today()
    frm = to - datetime.timedelta(days=days)
    print(f"  Fetching {len(universe)} symbols, last {days} days ...")
    data = {}
    for s in universe:
        try:
            df = bk._fetch(client, s, frm.strftime("%Y-%m-%d"), to.strftime("%Y-%m-%d"))
            if df is not None and len(df) > 20:
                data[s] = df
        except Exception:
            pass
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None, help="use a cached pickle instead of fetching")
    ap.add_argument("--days", type=int, default=10, help="calendar days to fetch")
    ap.add_argument("--last", type=int, default=5, help="how many trading days to show (5 ~= 1 week)")
    ap.add_argument("--cost", type=float, default=0.0018, help="round-trip cost for net P&L")
    ap.add_argument("--full", action="store_true",
                    help="full validated universe (~39 trades/mo) instead of the 18 liquid names")
    args = ap.parse_args()

    universe = HIGH_VOL_UNIVERSE if args.full else TOP_LIQUID
    if args.cache:
        p = Path(args.cache)
        if not p.is_absolute():
            p = _HERE / p
        raw = pickle.load(open(p, "rb"))
        raw = {k: raw[k] for k in universe if k in raw}
    else:
        raw = _fetch(args.days, universe)
    if not raw:
        print("ERROR: no data."); sys.exit(1)

    prepped = {}
    for s, df in raw.items():
        pp = sl._prep(df)
        if pp is not None:
            prepped[s] = pp

    all_days = sorted({d for p in prepped.values() for d in p.index.normalize().unique()})
    show_days = set(all_days[-args.last:])   # last N trading days (~1 week)
    params = {"rv": RV_MIN, "sl": WIDE_SL, "rr": MAX_RR}

    of.COST_RT_PCT = args.cost
    trades = []
    for symbol, d in prepped.items():
        for cand in sl.s_wide_mom(d, params):
            if cand["t_entry"].normalize() not in show_days:
                continue
            cand["sym"] = symbol
            trades.append(of._resolve_exit(d, cand))
    trades.sort(key=lambda t: t["t_entry"])
    span = sorted(show_days)

    print("=" * 84)
    print(f"  LAST-WEEK BACKTEST — validated config (rvol>={RV_MIN}, {WIDE_SL}xATR stop, "
          f"{MAX_RR:.0f}:1)")
    print(f"  {len(prepped)} liquid symbols | {span[0].date()} -> {span[-1].date()} "
          f"({len(span)} trading days) | cost {args.cost*100:.2f}%")
    print("=" * 84)

    # ── full trade log ──
    if trades:
        print(f"  {'date':>10} {'time':>6}  {'symbol':<11} {'entry':>8} {'stop':>8} "
              f"{'target':>8} {'exit':>8} {'why':<8} {'net%':>7}")
        print("  " + "-" * 82)
        for t in trades:
            print(f"  {t['t_entry'].strftime('%Y-%m-%d'):>10} {t['t_entry'].strftime('%H:%M'):>6}  "
                  f"{t['sym']:<11} {t['entry']:>8.2f} {t['sl']:>8.2f} {t['tp']:>8.2f} "
                  f"{t['exit']:>8.2f} {t['why']:<8} {t['net_ret']*100:>+6.2f}%")

    # ── per-day summary ──
    print("\n  PER-DAY SUMMARY")
    print("  " + "-" * 50)
    print(f"  {'date':>10} {'trades':>7} {'wins':>6} {'net%':>8}")
    for day in span:
        dtr = [t for t in trades if t["t_entry"].normalize() == day]
        if not dtr:
            print(f"  {day.date()!s:>10} {0:>7} {'-':>6} {'  0.00%':>8}  (no breakout hit rvol>=4)")
            continue
        w = sum(1 for t in dtr if t["net_ret"] > 0)
        net = sum(t["net_ret"] for t in dtr) * 100
        print(f"  {day.date()!s:>10} {len(dtr):>7} {w:>6} {net:>+7.2f}%")

    # ── grand total ──
    n = len(trades)
    print("  " + "-" * 50)
    if n:
        wins = sum(1 for t in trades if t["net_ret"] > 0)
        total = sum(t["net_ret"] for t in trades) * 100
        print(f"  WEEK TOTAL: {n} trades | {wins} wins ({wins/n*100:.0f}%) | "
              f"net {total:+.2f}% | ~₹{total/100*2500:+.0f} on ₹2,500/trade")
    else:
        print("  WEEK TOTAL: 0 trades — no qualifying breakouts this week (very selective).")
    print("=" * 84)
    print("  NOTE: even a week is a small sample. The +3.9%/mo edge shows over ~39 trades")
    print("  (a full month). Judge the MECHANICS here — clean entries, exact 3:1, tight stops.")


if __name__ == "__main__":
    main()
