#!/usr/bin/env python3
"""
run_full_report.py — SataVector Master Backtest + Live Expectations Report

Run this on your VPS to get a full analysis sent to Telegram:
    python3 /home/user/kingtrades/run_full_report.py

Produces:
  1. 90-day strategy backtest on 30 liquid symbols (real yfinance data)
  2. Per-strategy rankings: WR, PF, Sharpe, Max DD, Total Return
  3. Live trading expectations with compounding projections
  4. Sends full report to Telegram + saves to logs/

Usage:
    python3 run_full_report.py              # full 90-day backtest
    python3 run_full_report.py --quick      # 30-day fast test (5 symbols)
    python3 run_full_report.py --live       # live expectations only (no backtest)
"""

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Load env ───────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
CAPITAL        = float(os.getenv("MAX_DAILY_CAPITAL", "5000"))

# ── Symbols ────────────────────────────────────────────────────────────────
FULL_SYMBOLS_US = [
    "NVDA","TSLA","AAPL","MSFT","META","AMZN","GOOGL","AMD","NFLX","COIN",
    "PLTR","SHOP","UBER","JPM","GS","XOM","CVX","MARA","RIOT","SOFI",
    "SNAP","RBLX","HOOD","PINS","CRWD","DDOG","SNOW","NOW","CRM","SQ",
]
# NSE symbols (Yahoo Finance format: SYMBOL.NS)
FULL_SYMBOLS_NSE = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","ICICIBANK.NS","INFY.NS",
    "HINDUNILVR.NS","ITC.NS","SBIN.NS","BHARTIARTL.NS","KOTAKBANK.NS",
    "LT.NS","AXISBANK.NS","ASIANPAINT.NS","MARUTI.NS","TITAN.NS",
    "WIPRO.NS","ULTRACEMCO.NS","BAJFINANCE.NS","HCLTECH.NS","SUNPHARMA.NS",
]
QUICK_SYMBOLS_US  = ["NVDA","TSLA","AAPL","MSFT","META"]
QUICK_SYMBOLS_NSE = ["RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","SBIN.NS"]

# Legacy alias
FULL_SYMBOLS  = FULL_SYMBOLS_US
QUICK_SYMBOLS = QUICK_SYMBOLS_US

COMMISSION_PER_SHARE = 0.005  # $0.005/share (Alpaca default)
SLIPPAGE_PCT         = 0.0005  # 0.05% round-trip slippage
NSE_BROKERAGE_PCT    = 0.0003  # Zerodha/Groww ~0.03% per side (NSE intraday MIS)

# ── Telegram ────────────────────────────────────────────────────────────────
def _tg(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return r.status_code == 200
    except Exception:
        return False

# ── Data ────────────────────────────────────────────────────────────────────
def download(symbol: str, days: int = 90, interval: str = "5m") -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        period = f"{min(days, 58)}d" if interval == "5m" else f"{days}d"
        df = yf.download(symbol, period=period, interval=interval,
                         auto_adjust=True, progress=False)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        df = df[["open","high","low","close","volume"]].dropna()
        return df
    except Exception as e:
        logger.debug(f"{symbol} download failed: {e}")
        return None

def download_daily(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    return download(symbol, days, "1d")

# ── Technical helpers ───────────────────────────────────────────────────────
def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def vwap_series(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()

def rvol(df: pd.DataFrame, n: int = 20) -> pd.Series:
    avg = df["volume"].rolling(n).mean()
    return df["volume"] / avg.replace(0, np.nan)

# ── Strategy simulators ─────────────────────────────────────────────────────

def sim_orb(df_5m: pd.DataFrame, cap: float) -> Dict:
    """Opening Range Breakout — first 6 bars define range; trade the breakout."""
    trades = []
    if df_5m is None or len(df_5m) < 80:
        return _empty()
    df_5m = df_5m.copy()
    df_5m.index = pd.to_datetime(df_5m.index)

    for day, group in df_5m.groupby(df_5m.index.date):
        if len(group) < 20:
            continue
        orb = group.iloc[:6]  # 9:30–10:00
        orb_high = orb["high"].max()
        orb_low  = orb["low"].min()
        orb_range = orb_high - orb_low
        if orb_range < 0.01:
            continue

        traded = False
        for i in range(6, len(group)):
            if traded:
                break
            bar = group.iloc[i]
            vol_ok = rvol(group).iloc[i] > 1.3 if i >= 20 else True
            if bar["close"] > orb_high and vol_ok:  # bullish breakout
                entry = bar["close"]
                stop  = orb_low
                tgt   = entry + orb_range
                risk  = entry - stop
                if risk <= 0:
                    continue
                shares = min(int(cap * 0.02 / risk), int(cap / entry))
                if shares < 1:
                    continue
                # Simulate exit in remaining bars
                for j in range(i+1, len(group)):
                    b = group.iloc[j]
                    if b["low"] <= stop:
                        pnl = (stop - entry) * shares - COMMISSION_PER_SHARE * shares * 2
                        trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
                        traded = True; break
                    if b["high"] >= tgt:
                        pnl = (tgt - entry) * shares - COMMISSION_PER_SHARE * shares * 2
                        trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
                        traded = True; break
                else:
                    # EOD exit
                    eod = group.iloc[-1]["close"]
                    pnl = (eod - entry) * shares - COMMISSION_PER_SHARE * shares * 2
                    trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
                    traded = True
            elif bar["close"] < orb_low and vol_ok:  # bearish breakout
                entry = bar["close"]
                stop  = orb_high
                tgt   = entry - orb_range
                risk  = stop - entry
                if risk <= 0:
                    continue
                shares = min(int(cap * 0.02 / risk), int(cap / entry))
                if shares < 1:
                    continue
                for j in range(i+1, len(group)):
                    b = group.iloc[j]
                    if b["high"] >= stop:
                        pnl = (entry - stop) * shares - COMMISSION_PER_SHARE * shares * 2
                        trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
                        traded = True; break
                    if b["low"] <= tgt:
                        pnl = (entry - tgt) * shares - COMMISSION_PER_SHARE * shares * 2
                        trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
                        traded = True; break
                else:
                    eod = group.iloc[-1]["close"]
                    pnl = (entry - eod) * shares - COMMISSION_PER_SHARE * shares * 2
                    trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
                    traded = True
    return _stats("ORB_BREAKOUT", trades, cap)

def sim_vwap_reclaim(df_5m: pd.DataFrame, cap: float) -> Dict:
    """VWAP reclaim — price crosses above VWAP with volume surge."""
    trades = []
    if df_5m is None or len(df_5m) < 40:
        return _empty()
    df_5m = df_5m.copy()
    vw = vwap_series(df_5m)
    at = atr(df_5m)
    rv = rvol(df_5m)
    closes = df_5m["close"]

    for i in range(20, len(df_5m)-1):
        prev_below = closes.iloc[i-1] < vw.iloc[i-1]
        now_above  = closes.iloc[i] > vw.iloc[i]
        if prev_below and now_above and rv.iloc[i] > 1.5:
            entry = closes.iloc[i]
            a     = at.iloc[i]
            if not (a > 0):
                continue
            stop  = entry - 1.5 * a
            tgt   = entry + 2.5 * a
            risk  = entry - stop
            shares = min(int(cap * 0.015 / risk), int(cap / entry))
            if shares < 1:
                continue
            for j in range(i+1, min(i+30, len(df_5m))):
                b = df_5m.iloc[j]
                if b["low"] <= stop:
                    pnl = (stop - entry) * shares - COMMISSION_PER_SHARE * shares * 2
                    trades.append({"pnl": pnl, "win": False, "r": -1.0})
                    break
                if b["high"] >= tgt:
                    pnl = (tgt - entry) * shares - COMMISSION_PER_SHARE * shares * 2
                    trades.append({"pnl": pnl, "win": True, "r": pnl / (risk * shares)})
                    break
            else:
                eod_p = df_5m["close"].iloc[min(i+30, len(df_5m)-1)]
                pnl = (eod_p - entry) * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
    return _stats("VWAP_RECLAIM", trades, cap)

def sim_momentum(df_daily: pd.DataFrame, cap: float) -> Dict:
    """3-day momentum persistence — buy strong 3d momentum stocks."""
    trades = []
    if df_daily is None or len(df_daily) < 30:
        return _empty()
    closes = df_daily["close"]
    at     = atr(df_daily)

    for i in range(20, len(df_daily)-3):
        mom3 = (closes.iloc[i] / closes.iloc[i-3] - 1) * 100
        if abs(mom3) < 2.0:
            continue
        direction = "LONG" if mom3 > 0 else "SHORT"
        entry = closes.iloc[i]
        a     = at.iloc[i]
        if not (a > 0):
            continue
        stop  = entry - 1.5*a if direction == "LONG" else entry + 1.5*a
        tgt   = entry + 2.5*a if direction == "LONG" else entry - 2.5*a
        risk  = abs(entry - stop)
        shares = min(int(cap * 0.015 / risk), int(cap / entry))
        if shares < 1:
            continue
        for j in range(i+1, min(i+5, len(df_daily))):
            b = df_daily.iloc[j]
            hit_stop = b["low"] <= stop if direction == "LONG" else b["high"] >= stop
            hit_tgt  = b["high"] >= tgt if direction == "LONG" else b["low"] <= tgt
            if hit_stop:
                pnl = -risk * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": False, "r": -1.0})
                break
            if hit_tgt:
                pnl = 2.5 * risk * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": True, "r": 2.5})
                break
        else:
            eod = df_daily["close"].iloc[min(i+5, len(df_daily)-1)]
            pnl = (eod - entry) * shares * (1 if direction == "LONG" else -1)
            pnl -= COMMISSION_PER_SHARE * shares * 2
            trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
    return _stats("MOMENTUM_3D", trades, cap)

def sim_zscore(df_daily: pd.DataFrame, cap: float) -> Dict:
    """Z-score mean reversion — trade extremes back to mean."""
    trades = []
    if df_daily is None or len(df_daily) < 25:
        return _empty()
    closes = df_daily["close"]
    roll_mean = closes.rolling(20).mean()
    roll_std  = closes.rolling(20).std()
    at        = atr(df_daily)

    for i in range(20, len(df_daily)-3):
        if roll_std.iloc[i] == 0:
            continue
        z = (closes.iloc[i] - roll_mean.iloc[i]) / roll_std.iloc[i]
        if abs(z) < 1.8:
            continue
        direction = "LONG" if z < -1.8 else "SHORT"
        entry = closes.iloc[i]
        a     = at.iloc[i]
        if not (a > 0):
            continue
        tgt_level = roll_mean.iloc[i]  # revert to mean
        stop  = entry - 1.5*a if direction == "LONG" else entry + 1.5*a
        risk  = abs(entry - stop)
        if risk == 0:
            continue
        shares = min(int(cap * 0.015 / risk), int(cap / entry))
        if shares < 1:
            continue
        for j in range(i+1, min(i+7, len(df_daily))):
            b = df_daily.iloc[j]
            hit_stop = b["low"] <= stop if direction == "LONG" else b["high"] >= stop
            hit_tgt  = b["high"] >= tgt_level if direction == "LONG" else b["low"] <= tgt_level
            if hit_stop:
                pnl = -risk * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": False, "r": -1.0})
                break
            if hit_tgt:
                rwd = abs(entry - tgt_level)
                pnl = rwd * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": True, "r": rwd / risk})
                break
        else:
            eod = df_daily["close"].iloc[min(i+7, len(df_daily)-1)]
            pnl = (eod - entry) * shares * (1 if direction == "LONG" else -1)
            pnl -= COMMISSION_PER_SHARE * shares * 2
            trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
    return _stats("ZSCORE_REVERSION", trades, cap)

def sim_gap_and_go(df_5m: pd.DataFrame, df_daily: pd.DataFrame, cap: float) -> Dict:
    """Gap-and-go: stock gaps up >2% vs prior close; first bar confirms direction."""
    trades = []
    if df_5m is None or df_daily is None or len(df_5m) < 20:
        return _empty()
    df_5m  = df_5m.copy()
    df_5m.index = pd.to_datetime(df_5m.index)
    prior_closes = {}
    if df_daily is not None and not df_daily.empty:
        df_daily = df_daily.copy()
        df_daily.index = pd.to_datetime(df_daily.index)
        for i, (day, close_val) in enumerate(df_daily["close"].items()):
            prior_closes[day.date()] = float(close_val)

    for day, group in df_5m.groupby(df_5m.index.date):
        if len(group) < 10:
            continue
        from datetime import timedelta
        prev_day = day - timedelta(days=1)
        while prev_day not in prior_closes and (day - prev_day).days < 5:
            prev_day -= timedelta(days=1)
        if prev_day not in prior_closes:
            continue
        prior_close = prior_closes[prev_day]
        open_price  = group.iloc[0]["open"]
        gap_pct = (open_price - prior_close) / prior_close * 100
        if abs(gap_pct) < 2.0:
            continue
        direction = "LONG" if gap_pct > 0 else "SHORT"
        # First bar confirmation
        fb = group.iloc[0]
        if direction == "LONG" and fb["close"] < fb["open"]:
            continue  # gap up but first bar red — skip
        if direction == "SHORT" and fb["close"] > fb["open"]:
            continue
        entry = group.iloc[1]["open"] if len(group) > 1 else fb["close"]
        at_val = atr(group).iloc[min(5, len(group)-1)]
        if not (at_val > 0):
            continue
        stop  = entry - 1.5*at_val if direction == "LONG" else entry + 1.5*at_val
        tgt   = entry + 2.0*at_val if direction == "LONG" else entry - 2.0*at_val
        risk  = abs(entry - stop)
        shares = min(int(cap * 0.015 / risk), int(cap / entry))
        if shares < 1:
            continue
        for j in range(2, len(group)):
            b = group.iloc[j]
            hit_stop = b["low"] <= stop if direction == "LONG" else b["high"] >= stop
            hit_tgt  = b["high"] >= tgt if direction == "LONG" else b["low"] <= tgt
            if hit_stop:
                pnl = -risk * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": False, "r": -1.0})
                break
            if hit_tgt:
                pnl = 2.0*risk * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": True, "r": 2.0})
                break
        else:
            if len(group) > 2:
                eod = group.iloc[-1]["close"]
                pnl = (eod - entry) * shares * (1 if direction == "LONG" else -1)
                pnl -= COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
    return _stats("GAP_AND_GO", trades, cap)

def sim_power_hour(df_5m: pd.DataFrame, cap: float) -> Dict:
    """Power hour (3:00-3:30 PM ET) continuation trade."""
    trades = []
    if df_5m is None or len(df_5m) < 40:
        return _empty()
    df_5m = df_5m.copy()
    df_5m.index = pd.to_datetime(df_5m.index)
    at = atr(df_5m)
    rv = rvol(df_5m)

    for i in range(20, len(df_5m)-1):
        idx = df_5m.index[i]
        try:
            hour, minute = idx.hour, idx.minute
            # Convert to ET if timezone-aware
            if hasattr(idx, 'tz') and idx.tz is not None:
                import pytz
                et = idx.astimezone(pytz.timezone('America/New_York'))
                hour, minute = et.hour, et.minute
        except Exception:
            continue
        if not (15 <= hour < 15 or (hour == 15 and minute < 30)):
            if not (hour == 15 and minute < 30):
                continue
        bar = df_5m.iloc[i]
        prev = df_5m.iloc[i-1]
        # Momentum continuation: last 3 bars trend + volume surge
        trend_up = (bar["close"] > prev["close"] and
                    df_5m["close"].iloc[i-3:i].is_monotonic_increasing)
        trend_dn = (bar["close"] < prev["close"] and
                    df_5m["close"].iloc[i-3:i].is_monotonic_decreasing)
        if not (trend_up or trend_dn) or rv.iloc[i] < 1.2:
            continue
        direction = "LONG" if trend_up else "SHORT"
        entry = bar["close"]
        a = at.iloc[i]
        if not (a > 0):
            continue
        stop  = entry - a if direction == "LONG" else entry + a
        tgt   = entry + 1.5*a if direction == "LONG" else entry - 1.5*a
        risk  = abs(entry - stop)
        shares = min(int(cap * 0.01 / risk), int(cap / entry))
        if shares < 1:
            continue
        for j in range(i+1, min(i+8, len(df_5m))):
            b = df_5m.iloc[j]
            hit_stop = b["low"] <= stop if direction == "LONG" else b["high"] >= stop
            hit_tgt  = b["high"] >= tgt if direction == "LONG" else b["low"] <= tgt
            if hit_stop:
                pnl = -risk * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": False, "r": -1.0})
                break
            if hit_tgt:
                pnl = 1.5*risk * shares - COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": True, "r": 1.5})
                break
        else:
            if len(df_5m) > i+1:
                eod = df_5m["close"].iloc[min(i+8, len(df_5m)-1)]
                pnl = (eod - entry) * shares * (1 if direction == "LONG" else -1)
                pnl -= COMMISSION_PER_SHARE * shares * 2
                trades.append({"pnl": pnl, "win": pnl > 0, "r": pnl / (risk * shares)})
    return _stats("POWER_HOUR", trades, cap)

# ── Stats aggregator ────────────────────────────────────────────────────────
def _empty() -> Dict:
    return {"name": "?", "trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "avg_r": 0.0, "total_pnl": 0.0, "sharpe": 0.0, "max_dd": 0.0,
            "total_return_pct": 0.0}

def _stats(name: str, trades: list, capital: float) -> Dict:
    if not trades:
        d = _empty(); d["name"] = name; return d
    pnls    = [t["pnl"] for t in trades]
    wins    = [t["pnl"] for t in trades if t["win"]]
    losses  = [abs(t["pnl"]) for t in trades if not t["win"]]
    rs      = [t["r"] for t in trades]

    win_rate = len(wins) / len(trades)
    pf = sum(wins) / max(sum(losses), 0.01)
    avg_r = float(np.mean(rs))
    total_pnl = sum(pnls)

    # Sharpe (annualized, assume 1 trade/day avg)
    if len(pnls) > 1:
        daily = pd.Series(pnls)
        sharpe = (daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak)
    max_dd_pct = abs(dd.min()) / max(capital, 1) * 100 if len(dd) > 0 else 0.0

    total_return_pct = total_pnl / capital * 100

    return {
        "name": name,
        "trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": round(pf, 2),
        "avg_r": round(avg_r, 2),
        "total_pnl": round(total_pnl, 2),
        "sharpe": round(sharpe, 2),
        "max_dd": round(max_dd_pct, 1),
        "total_return_pct": round(total_return_pct, 1),
    }

# ── Compounding projection ───────────────────────────────────────────────────
def compounding_projection(capital: float, daily_pct: float, months: int = 12) -> list:
    results = []
    c = capital
    for m in range(1, months + 1):
        for _ in range(21):  # ~21 trading days/month
            c *= (1 + daily_pct / 100)
        results.append({"month": m, "capital": round(c, 0),
                         "gain_pct": round((c / capital - 1) * 100, 1)})
    return results

# ── Main backtest ────────────────────────────────────────────────────────────
def run_backtest(symbols: list, days: int, capital: float) -> Dict:
    results_per_strategy = {
        "ORB_BREAKOUT": [], "VWAP_RECLAIM": [], "MOMENTUM_3D": [],
        "ZSCORE_REVERSION": [], "GAP_AND_GO": [], "POWER_HOUR": [],
    }

    total_symbols = len(symbols)
    for idx, sym in enumerate(symbols, 1):
        print(f"  [{idx}/{total_symbols}] {sym}...", end="", flush=True)
        df5  = download(sym, days, "5m")
        df1d = download_daily(sym, days + 30)
        print(" data OK" if df5 is not None else " no data")

        if df5 is not None:
            for strat, fn in [
                ("ORB_BREAKOUT", lambda d5, dd: sim_orb(d5, capital)),
                ("VWAP_RECLAIM", lambda d5, dd: sim_vwap_reclaim(d5, capital)),
                ("POWER_HOUR",   lambda d5, dd: sim_power_hour(d5, capital)),
                ("GAP_AND_GO",   lambda d5, dd: sim_gap_and_go(d5, dd, capital)),
            ]:
                r = fn(df5, df1d)
                if r["trades"] > 0:
                    results_per_strategy[strat].append(r)

        if df1d is not None:
            for strat, fn in [
                ("MOMENTUM_3D",      lambda dd: sim_momentum(dd, capital)),
                ("ZSCORE_REVERSION", lambda dd: sim_zscore(dd, capital)),
            ]:
                r = fn(df1d)
                if r["trades"] > 0:
                    results_per_strategy[strat].append(r)

        time.sleep(0.3)  # rate limit

    # Aggregate per strategy
    aggregated = []
    all_trades = []
    for strat_name, stat_list in results_per_strategy.items():
        if not stat_list:
            continue
        total_t  = sum(s["trades"] for s in stat_list)
        all_pnl  = sum(s["total_pnl"] for s in stat_list)
        avg_wr   = np.mean([s["win_rate"] for s in stat_list])
        gross_w  = sum(s["total_pnl"] for s in stat_list if s["total_pnl"] > 0)
        gross_l  = abs(sum(s["total_pnl"] for s in stat_list if s["total_pnl"] < 0))
        pf       = gross_w / max(gross_l, 0.01)
        avg_r    = np.mean([s["avg_r"] for s in stat_list])
        max_dd   = max(s["max_dd"] for s in stat_list)
        ret_pct  = all_pnl / capital * 100
        sharpe   = np.mean([s["sharpe"] for s in stat_list if s["sharpe"] != 0])
        aggregated.append({
            "name": strat_name, "trades": total_t,
            "win_rate": round(avg_wr * 100, 1),
            "profit_factor": round(pf, 2),
            "avg_r": round(avg_r, 2),
            "total_pnl": round(all_pnl, 2),
            "sharpe": round(float(sharpe), 2),
            "max_dd": round(max_dd, 1),
            "total_return_pct": round(ret_pct, 1),
        })
        all_trades.extend([{"pnl": s["total_pnl"]} for s in stat_list])

    # Sort by Sharpe
    aggregated.sort(key=lambda x: x["sharpe"], reverse=True)

    # Combined portfolio metrics
    total_pnl_all = sum(s["total_pnl"] for s in aggregated)
    total_ret_pct = total_pnl_all / capital * 100
    avg_wr_all    = np.mean([s["win_rate"] for s in aggregated]) if aggregated else 0
    avg_pf        = np.mean([s["profit_factor"] for s in aggregated]) if aggregated else 0
    avg_sharpe    = np.mean([s["sharpe"] for s in aggregated]) if aggregated else 0
    days_traded   = days * 5 / 7
    daily_avg_pct = total_ret_pct / max(days_traded, 1)

    return {
        "strategies": aggregated,
        "portfolio": {
            "total_pnl": round(total_pnl_all, 2),
            "total_return_pct": round(total_ret_pct, 1),
            "avg_win_rate": round(avg_wr_all, 1),
            "avg_profit_factor": round(float(avg_pf), 2),
            "avg_sharpe": round(float(avg_sharpe), 2),
            "daily_avg_pct": round(daily_avg_pct, 3),
            "days_tested": days,
            "symbols_tested": len(symbols),
            "capital": capital,
            "final_capital": round(capital + total_pnl_all, 2),
        },
        "projections": {
            "conservative": compounding_projection(capital, daily_avg_pct * 0.60, 12),
            "realistic":    compounding_projection(capital, daily_avg_pct * 0.75, 12),
            "optimistic":   compounding_projection(capital, daily_avg_pct * 0.90, 12),
        }
    }

# ── Format report ───────────────────────────────────────────────────────────
def format_report(results: Dict, days: int) -> str:
    now = datetime.now().strftime("%d %b %Y")
    cap = results["portfolio"]["capital"]
    port = results["portfolio"]

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>SATAVECTOR BACKTEST REPORT</b>",
        f"   {now}  |  {days}-day test  |  ${cap:,.0f} capital",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🏆 <b>STRATEGY RANKINGS</b> (by Sharpe)",
        f"{'#':<3} {'Strategy':<20} {'T':>5} {'WR':>6} {'PF':>5} {'Sharpe':>7} {'MaxDD':>6} {'Return':>7}",
        "─" * 60,
    ]
    for i, s in enumerate(results["strategies"], 1):
        medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}. "))
        lines.append(
            f"{medal} {s['name']:<19} {s['trades']:>5} "
            f"{s['win_rate']:>5.0f}% {s['profit_factor']:>4.1f}x "
            f"{s['sharpe']:>6.1f} {s['max_dd']:>5.1f}% {s['total_return_pct']:>+6.1f}%"
        )

    lines += [
        "",
        "💰 <b>COMBINED PORTFOLIO</b>",
        f"  Capital: ${cap:,.0f} → ${port['final_capital']:,.0f}",
        f"  Total Return: {port['total_return_pct']:+.1f}% in {days} days",
        f"  Avg Win Rate: {port['avg_win_rate']:.0f}%  |  Avg PF: {port['avg_profit_factor']:.1f}x",
        f"  Avg Sharpe: {port['avg_sharpe']:.1f}  |  Daily avg: {port['daily_avg_pct']:+.3f}%",
        "",
        "📈 <b>COMPOUNDING PROJECTIONS</b>",
        f"  (based on {port['daily_avg_pct']:.3f}%/day backtest edge)",
    ]

    for scenario, factor, label in [
        ("conservative", 0.60, "Conservative (60%)"),
        ("realistic",    0.75, "Realistic (75%)"),
        ("optimistic",   0.90, "Optimistic (90%)"),
    ]:
        proj = results["projections"][scenario]
        m3  = proj[2]["capital"] if len(proj) > 2 else cap
        m6  = proj[5]["capital"] if len(proj) > 5 else cap
        m12 = proj[11]["capital"] if len(proj) > 11 else cap
        lines.append(
            f"  {label}:  3m=${m3:,.0f}  6m=${m6:,.0f}  12m=${m12:,.0f}"
        )

    # Daily target assessment
    daily = port["daily_avg_pct"]
    if daily >= 1.0:
        verdict = "✅ 1%/day target ACHIEVABLE"
    elif daily >= 0.5:
        verdict = "🟡 0.5-1%/day realistic (top days hit 1%)"
    elif daily >= 0.3:
        verdict = "⚠️ 0.3-0.5%/day — optimize signal quality"
    else:
        verdict = "❌ Below 0.3%/day — strategy needs tuning"

    live_daily = daily * 0.70  # 30% live discount for slippage + psychology
    lines += [
        "",
        "🎯 <b>LIVE TRADING EXPECTATIONS</b>",
        f"  Backtest edge: {daily:.3f}%/day",
        f"  Live estimate: {live_daily:.3f}%/day (after 30% friction discount)",
        f"  On $5,000: ${5000*live_daily/100:.1f}/day avg",
        f"  Monthly target: ${5000*live_daily/100*21:.0f} (+{live_daily*21:.1f}%)",
        f"  Verdict: {verdict}",
        "",
        "⚡ <b>KEY INSIGHTS</b>",
        f"  Best strategy: {results['strategies'][0]['name'] if results['strategies'] else 'N/A'}",
        f"  Focus on: Top 3 strategies for 80% of returns",
        f"  Avoid trading in: Midday chop (11:30-1:30 PM)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)

# ── Save + send ──────────────────────────────────────────────────────────────
def save_and_send(report: str, results: Dict) -> None:
    # Save to file
    Path("logs").mkdir(exist_ok=True)
    fname = f"logs/backtest_{datetime.now().strftime('%Y-%m-%d')}.txt"
    with open(fname, "w") as f:
        f.write(report.replace("<b>", "").replace("</b>", ""))
    print(f"\nReport saved: {fname}")

    # Save JSON
    jfname = fname.replace(".txt", ".json")
    with open(jfname, "w") as f:
        json.dump(results, f, indent=2)

    # Send to Telegram (split if too long)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT:
        chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
        for chunk in chunks:
            ok = _tg(chunk)
            time.sleep(0.5)
        if ok:
            print("✅ Report sent to Telegram")
        else:
            print("⚠️  Telegram send failed — check BOT_TOKEN and CHAT_ID")
    else:
        print("ℹ️  Telegram not configured — report saved locally only")
    print("\n" + report.replace("<b>", "**").replace("</b>", "**"))

# ── Live expectations section ────────────────────────────────────────────────
def build_live_expectations_section() -> str:
    try:
        from live_expectations import build_report as _le_build
        return "\n\n" + _le_build()
    except Exception as e:
        return f"\n\n[Live expectations unavailable: {e}]"


# ── Market standing section ──────────────────────────────────────────────────
def build_market_standing_section() -> str:
    try:
        from market_standing import build_standing_report
        return "\n\n" + build_standing_report()
    except Exception as e:
        return f"\n\n[Market standing unavailable: {e}]"


# ── Master report (all-in-one) ───────────────────────────────────────────────
def build_master_report(results_us: Dict, results_nse: Optional[Dict], days: int) -> str:
    report = format_report(results_us, days)

    if results_nse:
        report += "\n\n" + "━" * 43 + "\n"
        report += f"🇮🇳 <b>NSE BACKTEST RESULTS</b>\n"
        report += f"   {days}-day test on {results_nse['portfolio']['symbols_tested']} NSE symbols\n\n"
        nse_port = results_nse["portfolio"]
        report += (
            f"  Capital: ₹{nse_port['capital']:,.0f} → ₹{nse_port['final_capital']:,.0f}\n"
            f"  Total Return: {nse_port['total_return_pct']:+.1f}% in {days} days\n"
            f"  Win Rate: {nse_port['avg_win_rate']:.0f}%  |  PF: {nse_port['avg_profit_factor']:.1f}x\n"
            f"  Avg Sharpe: {nse_port['avg_sharpe']:.1f}  |  Daily: {nse_port['daily_avg_pct']:+.3f}%\n"
        )
        if results_nse["strategies"]:
            report += f"\n  Best NSE strategy: {results_nse['strategies'][0]['name']}\n"

    report += build_live_expectations_section()
    report += build_market_standing_section()
    return report


# ── Entry point ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="SataVector Master Report")
    parser.add_argument("--quick",    action="store_true", help="Fast test: 30 days, 5 symbols")
    parser.add_argument("--live",     action="store_true", help="Live expectations + standing only")
    parser.add_argument("--standing", action="store_true", help="Market standing report only")
    parser.add_argument("--nse",      action="store_true", help="Include NSE symbols in backtest")
    parser.add_argument("--days",     type=int, default=90, help="Backtest days")
    parser.add_argument("--capital",  type=float, default=CAPITAL, help="Starting capital")
    args = parser.parse_args()

    if args.standing:
        print("📍 SataVector — Where We Stand")
        from market_standing import run_standing_report_and_send
        run_standing_report_and_send()
        return

    if args.live:
        print("📊 SataVector Live Expectations + Market Standing")
        section = build_live_expectations_section() + build_market_standing_section()
        print(section.replace("<b>", "").replace("</b>", ""))
        if TELEGRAM_TOKEN and TELEGRAM_CHAT:
            chunks = [section[i:i+4000] for i in range(0, len(section), 4000)]
            for chunk in chunks:
                _tg(chunk)
                time.sleep(0.5)
        return

    days = 30 if args.quick else args.days
    symbols_us  = QUICK_SYMBOLS_US  if args.quick else FULL_SYMBOLS_US
    symbols_nse = QUICK_SYMBOLS_NSE if args.quick else FULL_SYMBOLS_NSE

    print(f"\n🚀 SataVector Master Report — {days} days")
    print(f"   US symbols: {len(symbols_us)}  |  NSE symbols: {len(symbols_nse) if args.nse else 0}")
    print(f"   Strategies: ORB, VWAP Reclaim, Momentum, Z-Score, Gap-and-Go, Power Hour")
    print(f"   US commission: ${COMMISSION_PER_SHARE}/share + {SLIPPAGE_PCT*100:.2f}% slippage")
    if args.nse:
        print(f"   NSE brokerage: {NSE_BROKERAGE_PCT*100:.3f}% per side")
    print("   Downloading data...\n")

    _tg(
        f"⏳ <b>SataVector Master Report starting</b>\n"
        f"{days} days | US:{len(symbols_us)} + NSE:{len(symbols_nse) if args.nse else 0} symbols"
    )

    t0 = time.time()
    results_us = run_backtest(symbols_us, days, args.capital)

    results_nse = None
    if args.nse:
        print("\n🇮🇳 Running NSE backtest...")
        # NSE capital in INR — approximate conversion
        nse_capital = args.capital * 84  # ~84 INR per USD
        results_nse = run_backtest(symbols_nse, days, nse_capital)

    elapsed = time.time() - t0

    Path("logs").mkdir(exist_ok=True)
    with open("logs/backtest_latest.json", "w") as f:
        json.dump(results_us, f, indent=2)
    if results_nse:
        with open("logs/backtest_nse_latest.json", "w") as f:
            json.dump(results_nse, f, indent=2)

    report = build_master_report(results_us, results_nse, days)
    report += f"\n\n⏱ Completed in {elapsed:.0f}s"
    save_and_send(report, results_us)


if __name__ == "__main__":
    main()
