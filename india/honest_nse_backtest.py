"""
honest_nse_backtest.py — A deliberately SIMPLE, NON-optimized momentum backtest
on REAL NSE daily data, with realistic Indian intraday/swing costs.

Purpose: ground-truth calibration. This is NOT the production strategy. It is a
transparent, fixed-parameter momentum rule so we can see the honest *magnitude*
of returns a disciplined momentum edge produces on real NSE large caps — with no
curve-fitting, no look-ahead, and full cost accounting.

Rules (fixed, never optimized):
  - Universe: liquid NSE large caps.
  - Long-only daily swing momentum.
  - ENTER at next open when: close > 20-day high (breakout) AND close > 50-day EMA.
  - STOP: 2.0 x ATR(14) below entry (checked on daily low).
  - EXIT: close < 10-day low (momentum break) OR stop hit, at next open.
  - Equal risk per trade; max concurrent positions capped.
  - Costs: round-trip ~0.20% of turnover (brokerage+STT+exch+GST+stamp+slippage).

Stdlib only. Run anywhere with network: python3 india/honest_nse_backtest.py
"""

import json
import math
import urllib.request
from datetime import datetime, timezone

# Liquid NSE large caps (Yahoo .NS tickers)
UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS", "LT.NS", "ITC.NS",
    "HINDUNILVR.NS", "BHARTIARTL.NS", "MARUTI.NS", "TATAMOTORS.NS",
    "SUNPHARMA.NS", "BAJFINANCE.NS", "ASIANPAINT.NS", "TITAN.NS",
]
BENCHMARK = "^NSEI"  # Nifty 50

YEARS = 5
ROUND_TRIP_COST = 0.0020   # 0.20% of turnover, round trip (realistic NSE)
ATR_STOP_MULT = 2.0
MAX_POSITIONS = 8
RISK_PER_TRADE = 0.01      # 1% of equity risked per trade (ATR-based sizing)
START_CAPITAL = 500_000.0


def fetch_daily(ticker, years=YEARS):
    rng = f"{years}y"
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?range={rng}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=20))
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    bars = []
    for i in range(len(ts)):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        bars.append({
            "date": datetime.fromtimestamp(ts[i], tz=timezone.utc).strftime("%Y-%m-%d"),
            "o": o, "h": h, "l": l, "c": c,
        })
    return bars


def ema(values, period):
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(bars, period=14):
    trs = [bars[0]["h"] - bars[0]["l"]]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    # Wilder smoothing
    out = [sum(trs[:period]) / period] * period
    for i in range(period, len(trs)):
        out.append((out[-1] * (period - 1) + trs[i]) / period)
    return out


def backtest():
    print("Fetching real NSE daily data from Yahoo Finance...")
    data = {}
    for t in UNIVERSE:
        try:
            b = fetch_daily(t)
            if len(b) > 250:
                data[t] = b
                print(f"  {t}: {len(b)} bars  {b[0]['date']} -> {b[-1]['date']}")
        except Exception as e:
            print(f"  {t}: FAILED {repr(e)[:80]}")
    if not data:
        print("No data fetched."); return

    # Precompute indicators per symbol
    ind = {}
    for t, bars in data.items():
        closes = [x["c"] for x in bars]
        ind[t] = {
            "ema50": ema(closes, 50),
            "atr14": atr(bars, 14),
            "hi20": [max(closes[max(0, i - 20):i] or [closes[i]]) for i in range(len(closes))],
            "lo10": [min(closes[max(0, i - 10):i] or [closes[i]]) for i in range(len(closes))],
        }

    # Build a unified date index (use benchmark dates as calendar)
    all_dates = sorted({x["date"] for bars in data.values() for x in bars})
    # index maps per symbol: date -> position
    pos_idx = {t: {b["date"]: i for i, b in enumerate(bars)} for t, bars in data.items()}

    equity = START_CAPITAL
    open_pos = {}   # ticker -> dict(entry, stop, qty)
    trades = []
    equity_curve = []
    pending_entries = []  # (ticker) signaled today, enter at next bar open

    for di, date in enumerate(all_dates):
        # 1) process exits & stops at today's bar
        for t in list(open_pos.keys()):
            if date not in pos_idx[t]:
                continue
            i = pos_idx[t][date]
            bar = data[t][i]
            p = open_pos[t]
            exit_price = None
            # stop hit intrabar
            if bar["l"] <= p["stop"]:
                exit_price = min(p["stop"], bar["o"])  # gap-through realism
            else:
                # momentum break: close < 10-day low -> exit next open; approximate at close
                if bar["c"] < ind[t]["lo10"][i]:
                    exit_price = bar["c"]
            if exit_price is not None:
                gross = (exit_price - p["entry"]) * p["qty"]
                turnover = (exit_price + p["entry"]) * p["qty"]
                cost = turnover * ROUND_TRIP_COST / 2  # cost already half each side; total ~ROUND_TRIP
                net = gross - turnover * ROUND_TRIP_COST
                equity += net
                trades.append({"t": t, "entry": p["entry"], "exit": exit_price,
                               "net": net, "ret": net / (p["entry"] * p["qty"])})
                del open_pos[t]

        # 2) execute pending entries at today's open
        for t in pending_entries:
            if t in open_pos or len(open_pos) >= MAX_POSITIONS:
                continue
            if date not in pos_idx[t]:
                continue
            i = pos_idx[t][date]
            entry = data[t][i]["o"]
            a = ind[t]["atr14"][i]
            if a <= 0:
                continue
            stop = entry - ATR_STOP_MULT * a
            risk_per_share = entry - stop
            qty = max(0, int((equity * RISK_PER_TRADE) / risk_per_share))
            # cap notional at 20% of equity
            if qty * entry > equity * 0.20:
                qty = int(equity * 0.20 / entry)
            if qty <= 0:
                continue
            open_pos[t] = {"entry": entry, "stop": stop, "qty": qty}
        pending_entries = []

        # 3) generate signals on today's close (enter next bar)
        for t, bars in data.items():
            if date not in pos_idx[t] or t in open_pos:
                continue
            i = pos_idx[t][date]
            if i < 55:
                continue
            c = bars[i]["c"]
            if c > ind[t]["hi20"][i] and c > ind[t]["ema50"][i]:
                pending_entries.append(t)

        equity_curve.append((date, equity + sum(
            (data[t][pos_idx[t][date]]["c"] - p["entry"]) * p["qty"]
            for t, p in open_pos.items() if date in pos_idx[t])))

    # ---- metrics ----
    rets = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1][1]
        cur = equity_curve[i][1]
        rets.append((cur - prev) / prev if prev else 0.0)

    n_days = len(equity_curve)
    years_actual = n_days / 252
    final = equity_curve[-1][1]
    total_ret = final / START_CAPITAL - 1
    cagr = (final / START_CAPITAL) ** (1 / years_actual) - 1 if years_actual else 0

    mean_d = sum(rets) / len(rets) if rets else 0
    var_d = sum((r - mean_d) ** 2 for r in rets) / len(rets) if rets else 0
    std_d = math.sqrt(var_d)
    sharpe = (mean_d / std_d * math.sqrt(252)) if std_d else 0
    downside = [r for r in rets if r < 0]
    dd_std = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0
    sortino = (mean_d / dd_std * math.sqrt(252)) if dd_std else 0

    peak = -1e18; maxdd = 0
    for _, v in equity_curve:
        peak = max(peak, v)
        maxdd = min(maxdd, v / peak - 1)

    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    wr = len(wins) / len(trades) if trades else 0
    gross_win = sum(t["net"] for t in wins)
    gross_loss = -sum(t["net"] for t in losses)
    pf = gross_win / gross_loss if gross_loss else float("inf")
    avg_month = (1 + cagr) ** (1 / 12) - 1

    print("\n" + "=" * 60)
    print("HONEST NSE MOMENTUM BACKTEST — REAL DATA, FIXED PARAMS")
    print("=" * 60)
    print(f"Period:            {equity_curve[0][0]} -> {equity_curve[-1][0]} ({years_actual:.1f}y)")
    print(f"Symbols:           {len(data)} NSE large caps")
    print(f"Trades:            {len(trades)}")
    print(f"Start capital:     Rs {START_CAPITAL:,.0f}")
    print(f"Final equity:      Rs {final:,.0f}")
    print(f"Total return:      {total_ret*100:+.1f}%")
    print(f"CAGR:              {cagr*100:+.1f}%/year")
    print(f"Avg monthly:       {avg_month*100:+.2f}%/month  <-- compare to '15-20%/mo'")
    print(f"Sharpe (ann.):     {sharpe:.2f}            <-- compare to 'Sharpe > 2'")
    print(f"Sortino (ann.):    {sortino:.2f}")
    print(f"Max drawdown:      {maxdd*100:.1f}%")
    print(f"Win rate:          {wr*100:.1f}%")
    print(f"Profit factor:     {pf:.2f}")
    print("=" * 60)

    # Benchmark: Nifty buy & hold
    try:
        bench = fetch_daily(BENCHMARK)
        bret = bench[-1]["c"] / bench[0]["c"] - 1
        byrs = len(bench) / 252
        bcagr = (1 + bret) ** (1 / byrs) - 1
        print(f"Nifty50 buy&hold:  {bret*100:+.1f}% total | {bcagr*100:+.1f}%/yr | "
              f"{((1+bcagr)**(1/12)-1)*100:+.2f}%/mo")
    except Exception as e:
        print(f"Benchmark failed: {repr(e)[:80]}")

    print("\nNOTE: Fixed parameters, NOT optimized. This is calibration of honest")
    print("momentum magnitude on real data — not the production intraday strategy.")


if __name__ == "__main__":
    backtest()
