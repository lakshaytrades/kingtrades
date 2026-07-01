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


def _fetch(days: int) -> dict:
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
    print(f"  Fetching {len(TOP_LIQUID)} symbols, last {days} days ...")
    data = {}
    for s in TOP_LIQUID:
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
    ap.add_argument("--days", type=int, default=5, help="days to fetch (for ATR warmup)")
    ap.add_argument("--cost", type=float, default=0.0018, help="round-trip cost for net P&L")
    args = ap.parse_args()

    if args.cache:
        p = Path(args.cache)
        if not p.is_absolute():
            p = _HERE / p
        raw = pickle.load(open(p, "rb"))
        raw = {k: raw[k] for k in TOP_LIQUID if k in raw}
    else:
        raw = _fetch(args.days)
    if not raw:
        print("ERROR: no data."); sys.exit(1)

    prepped = {}
    for s, df in raw.items():
        pp = sl._prep(df)
        if pp is not None:
            prepped[s] = pp

    all_days = sorted({d for p in prepped.values() for d in p.index.normalize().unique()})
    day = all_days[-1]                       # the latest day = "today"
    params = {"rv": RV_MIN, "sl": WIDE_SL, "rr": MAX_RR}

    of.COST_RT_PCT = args.cost
    trades = []
    for symbol, d in prepped.items():
        for cand in sl.s_wide_mom(d, params):
            if cand["t_entry"].normalize() != day:
                continue
            cand["sym"] = symbol
            trades.append(of._resolve_exit(d, cand))
    trades.sort(key=lambda t: t["t_entry"])

    print("=" * 84)
    print(f"  TODAY'S BACKTEST — validated config (rvol>={RV_MIN}, {WIDE_SL}xATR stop, "
          f"{MAX_RR:.0f}:1) on {day.date()}")
    print(f"  {len(prepped)} liquid symbols | cost {args.cost*100:.2f}%")
    print("=" * 84)
    if not trades:
        print("  NO TRADES today — no fresh ORB breakout hit rvol>=4 in the 9:30-14:00 window.")
        print("  (This is normal and expected on many days — the filter is selective.)")
        print("=" * 84); return

    print(f"  {'time':>6}  {'symbol':<12} {'entry':>8} {'stop':>8} {'target':>8}  "
          f"{'exit':>8} {'why':<9} {'net%':>7}")
    print("  " + "-" * 78)
    wins = 0; total = 0.0
    for t in trades:
        risk = t["entry"] - t["sl"]
        net = t["net_ret"] * 100
        total += net
        if net > 0:
            wins += 1
        print(f"  {t['t_entry'].strftime('%H:%M'):>6}  {t['sym']:<12} "
              f"{t['entry']:>8.2f} {t['sl']:>8.2f} {t['tp']:>8.2f}  "
              f"{t['exit']:>8.2f} {t['why']:<9} {net:>+6.2f}%")
    n = len(trades)
    print("  " + "-" * 78)
    print(f"  Trades: {n}   Wins: {wins} ({wins/n*100:.0f}%)   "
          f"Sum of net returns: {total:+.2f}%")
    print(f"  On ₹2,500/trade that's roughly ₹{total/100*2500:+.0f} gross across the day")
    print("=" * 84)
    print("  NOTE: this is the validated config on ONE day — a single day is noisy (the")
    print("  edge shows over ~39 trades/month). It's a realistic preview of the mechanics.")


if __name__ == "__main__":
    main()
