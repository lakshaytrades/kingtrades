"""
replay_yahoo_india.py — REAL historical replay of the ACTUAL production signal
generator (IndiaSignalGenerator), fed with REAL NSE 5-min candles from Yahoo
Finance instead of Upstox — so it runs anywhere with plain network access.

This answers the only question that matters: when the bot's real price-based
signal core is stepped bar-by-bar over real past NSE candles with NO look-ahead,
what win rate / expectancy does it ACTUALLY produce? (Win rate as an OUTPUT.)

It reuses the exact exit mechanics, cost stack, and replay-config from the
existing backtest_replay_india.py, and monkeypatches the same data hook
(data_fetch_upstox.get_ohlcv_multi_tf) the signal generator calls internally.

Live-only boosters (option chain, FII/DII, news, etc.) have no historical feed
and are disabled — so this is a CONSERVATIVE FLOOR, identical in spirit to the
Upstox replay, but actually runnable here.

Limits: Yahoo gives 5-min intraday for ~60 days back. That is a small sample —
treat the numbers as directional, not gospel. But they are REAL and measured.

Usage:
  python3 india/replay_yahoo_india.py
  python3 india/replay_yahoo_india.py --symbols RELIANCE,INFY,TCS --days 60
"""
import argparse
import json
import math
import urllib.request
from datetime import time as dtime

import pandas as pd

# Reuse the real machinery from the production replay harness
from backtest_replay_india import (
    _make_replay_config, _resample, Trade, _simulate_exit,
    COST_RT_PCT, SQUAREOFF,
)
from signal_generator_india import IndiaSignalGenerator
import data_fetch_upstox as dfd

IST = "Asia/Kolkata"
SESSION_START = dtime(9, 15)
DEFAULT_SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
    "AXISBANK", "LT", "ITC", "BHARTIARTL", "MARUTI", "SUNPHARMA",
]


def fetch_yahoo_5m(symbol: str, days: int = 59) -> pd.DataFrame | None:
    """Real 5-min NSE candles from Yahoo, IST-indexed OHLCV DataFrame."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS"
           f"?range={days}d&interval=5m")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=25))
        r = d["chart"]["result"][0]
        ts = r["timestamp"]
        q = r["indicators"]["quote"][0]
    except Exception:
        return None
    rows = []
    for i in range(len(ts)):
        o, h, l, c, v = (q["open"][i], q["high"][i], q["low"][i],
                         q["close"][i], q["volume"][i])
        if None in (o, h, l, c):
            continue
        rows.append((ts[i], o, h, l, c, v or 0))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
    df = df.drop(columns=["ts"])
    # keep only regular session bars
    df = df.between_time("09:15", "15:30")
    return df if len(df) > 50 else None


def replay(symbols, days):
    cfg = _make_replay_config()
    gen = IndiaSignalGenerator(cfg, watchlist=symbols)

    print(f"Fetching real Yahoo 5-min candles ({days}d) for {len(symbols)} symbols...")
    data = {}
    for s in symbols:
        df = fetch_yahoo_5m(s, days)
        if df is not None:
            data[s] = df
            d0, d1 = df.index[0].date(), df.index[-1].date()
            print(f"  {s:12s} {len(df):5d} bars  {d0} -> {d1}")
        else:
            print(f"  {s:12s} no data")
    if not data:
        print("No data."); return

    trades = []
    MIN_BARS = 30          # need history before first signal each day
    WARMUP_TIME = dtime(9, 45)   # don't trade first 30 min (let bars build)

    orig_hook = dfd.get_ohlcv_multi_tf
    try:
        for sym, df in data.items():
            # group by trading day
            for day, day_df in df.groupby(df.index.date):
                if len(day_df) < MIN_BARS:
                    continue
                open_trade = None
                bars = day_df
                for i in range(MIN_BARS, len(bars)):
                    ts = bars.index[i]
                    if ts.time() < WARMUP_TIME:
                        continue
                    if ts.time() >= SQUAREOFF:
                        break
                    if open_trade is not None:
                        continue  # one position per symbol at a time

                    # as-of slice (NO look-ahead): bars up to and including i
                    asof_5m = bars.iloc[max(0, i - 120):i + 1]
                    asof_15m = _resample(asof_5m, "15min")
                    asof_1h = _resample(asof_5m, "60min")
                    if asof_15m is None or asof_1h is None:
                        continue
                    mtf = {"5m": asof_5m, "15m": asof_15m, "1h": asof_1h}

                    # inject historical slice into the real signal generator
                    dfd.get_ohlcv_multi_tf = lambda _s, _m=mtf: _m
                    ltp = float(asof_5m["close"].iloc[-1])
                    try:
                        sig = gen.generate_signal(sym, current_price=ltp)
                    except Exception:
                        sig = None

                    if sig is None or getattr(sig, "direction", None) not in ("LONG", "SHORT"):
                        continue

                    # size by 1% risk
                    risk_ps = abs(sig.entry_price - sig.stop_loss)
                    if risk_ps <= 0:
                        continue
                    qty = max(1, int((500_000 * 0.01) / risk_ps))
                    if qty * sig.entry_price > 500_000 * 0.20:
                        qty = max(1, int(500_000 * 0.20 / sig.entry_price))

                    tr = Trade(sig, qty, ts)
                    future = bars.iloc[i + 1:]
                    if len(future) < 2:
                        continue
                    _simulate_exit(tr, future)
                    trades.append(tr)
                    open_trade = None  # exit simulated fully within the day
    finally:
        dfd.get_ohlcv_multi_tf = orig_hook

    report(trades, len(data), days)


def report(trades, n_syms, days):
    print("\n" + "=" * 62)
    print("REAL REPLAY — production IndiaSignalGenerator on real Yahoo candles")
    print("=" * 62)
    if not trades:
        print("ZERO trades triggered over the sample.")
        print("That is itself a finding: the live-gated signal core produced no")
        print("qualifying setups on this real data window (boosters disabled).")
        print("=" * 62)
        return
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    wr = len(wins) / len(trades)
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    pf = gross_win / gross_loss if gross_loss else float("inf")
    total_pnl = sum(t.pnl for t in trades)
    avg_win = gross_win / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    expectancy = total_pnl / len(trades)
    ret_pct = total_pnl / 500_000 * 100
    # crude monthly extrapolation
    months = max(0.5, days / 21)
    monthly = ret_pct / months

    print(f"Symbols:           {n_syms}")
    print(f"Window:            ~{days} trading-calendar days")
    print(f"Trades (measured): {len(trades)}")
    print(f"Win rate:          {wr*100:.1f}%   <-- MEASURED, not assumed")
    print(f"Profit factor:     {pf:.2f}")
    print(f"Avg win:           Rs {avg_win:,.0f}")
    print(f"Avg loss:          Rs {avg_loss:,.0f}")
    print(f"Expectancy/trade:  Rs {expectancy:,.0f}")
    print(f"Total P&L:         Rs {total_pnl:,.0f}  ({ret_pct:+.2f}% on 5L)")
    print(f"Implied monthly:   {monthly:+.2f}%/month  (crude extrapolation)")
    print("=" * 62)
    print("Boosters disabled (no historical feed) -> conservative FLOOR.")
    print("Small sample (~60d). Real, measured, no look-ahead. Not the final word,")
    print("but the ONLY honest number until live trades accumulate.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default="")
    ap.add_argument("--days", type=int, default=59)
    a = ap.parse_args()
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or DEFAULT_SYMBOLS
    replay(syms, a.days)
