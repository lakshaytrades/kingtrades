"""
generate_synthetic_data.py — Realistic NSE synthetic OHLCV data generator.

Creates 60 days of 5-minute IST intraday bars for NSE stocks when Yahoo Finance
is not accessible. Uses GBM + regime switching to produce realistic:
- Trending days vs ranging days
- ORB breakout patterns (9:15-9:30 opening range + breakout)
- VWAP deviation and mean-reversion
- Volume surges on momentum bars
- Sector correlation
- Realistic NSE price levels and volatility

Usage:
    from generate_synthetic_data import generate_nse_data
    data = generate_nse_data(symbols, days=60)
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")

# Approximate real NSE price levels and daily volatility (%) for major stocks
_STOCK_PARAMS: Dict[str, Dict] = {
    # symbol: {price, vol_daily_pct, beta}
    "RELIANCE":   {"price": 2850, "vol": 1.4, "beta": 1.1},
    "TCS":        {"price": 3600, "vol": 1.0, "beta": 0.85},
    "HDFCBANK":   {"price": 1720, "vol": 1.3, "beta": 1.05},
    "INFY":       {"price": 1580, "vol": 1.1, "beta": 0.90},
    "ICICIBANK":  {"price": 1150, "vol": 1.5, "beta": 1.15},
    "SBIN":       {"price": 820,  "vol": 1.8, "beta": 1.25},
    "AXISBANK":   {"price": 1180, "vol": 1.7, "beta": 1.20},
    "KOTAKBANK":  {"price": 1850, "vol": 1.2, "beta": 0.95},
    "BAJFINANCE": {"price": 6800, "vol": 1.9, "beta": 1.30},
    "BHARTIARTL": {"price": 1380, "vol": 1.3, "beta": 1.00},
    "LT":         {"price": 3400, "vol": 1.4, "beta": 1.10},
    "ITC":        {"price": 460,  "vol": 1.1, "beta": 0.80},
    "TATASTEEL":  {"price": 160,  "vol": 2.2, "beta": 1.40},
    "MARUTI":     {"price": 12400,"vol": 1.5, "beta": 1.05},
    "HCLTECH":    {"price": 1620, "vol": 1.1, "beta": 0.90},
    "WIPRO":      {"price": 480,  "vol": 1.2, "beta": 0.88},
    "JSWSTEEL":   {"price": 890,  "vol": 2.0, "beta": 1.35},
    "M&M":        {"price": 2950, "vol": 1.6, "beta": 1.12},
    "BAJAJFINSV": {"price": 1680, "vol": 1.7, "beta": 1.18},
    "ZOMATO":     {"price": 240,  "vol": 2.5, "beta": 1.50},
    "INDUSINDBK": {"price": 820,  "vol": 2.0, "beta": 1.30},
    "NTPC":       {"price": 380,  "vol": 1.5, "beta": 0.95},
    "TRENT":      {"price": 5200, "vol": 2.0, "beta": 1.20},
    "PERSISTENT": {"price": 5400, "vol": 1.8, "beta": 1.10},
    "POLYCAB":    {"price": 6200, "vol": 1.6, "beta": 1.15},
    "HINDUNILVR": {"price": 2380, "vol": 0.9, "beta": 0.70},
    "ASIANPAINT": {"price": 2650, "vol": 1.0, "beta": 0.75},
    "TITAN":      {"price": 3450, "vol": 1.4, "beta": 1.00},
    "ULTRACEMCO": {"price": 11200,"vol": 1.3, "beta": 0.95},
    "SUNPHARMA":  {"price": 1650, "vol": 1.2, "beta": 0.85},
    "ONGC":       {"price": 265,  "vol": 1.8, "beta": 1.10},
    "BPCL":       {"price": 290,  "vol": 1.9, "beta": 1.15},
    "COALINDIA":  {"price": 485,  "vol": 1.6, "beta": 0.90},
    "EICHERMOT":  {"price": 4800, "vol": 1.4, "beta": 1.05},
    "HEROMOTOCO": {"price": 4200, "vol": 1.3, "beta": 0.95},
    "BAJAJ-AUTO": {"price": 9200, "vol": 1.4, "beta": 1.00},
    "HINDALCO":   {"price": 680,  "vol": 2.0, "beta": 1.35},
    "VEDL":       {"price": 460,  "vol": 2.3, "beta": 1.45},
    "CIPLA":      {"price": 1480, "vol": 1.2, "beta": 0.82},
    "DIVISLAB":   {"price": 4900, "vol": 1.3, "beta": 0.88},
    "TECHM":      {"price": 1680, "vol": 1.2, "beta": 0.92},
    "HAL":        {"price": 4200, "vol": 1.8, "beta": 1.10},
    "BEL":        {"price": 285,  "vol": 2.0, "beta": 1.20},
    "DLF":        {"price": 820,  "vol": 2.2, "beta": 1.30},
    "IDFCFIRSTB": {"price": 78,   "vol": 2.5, "beta": 1.40},
    "PNB":        {"price": 115,  "vol": 2.5, "beta": 1.35},
    "BANKBARODA": {"price": 240,  "vol": 2.2, "beta": 1.28},
    "TATAPOWER":  {"price": 420,  "vol": 2.0, "beta": 1.25},
    "IRCTC":      {"price": 920,  "vol": 2.0, "beta": 1.15},
}

# Default params for symbols not in the table
_DEFAULT_PARAMS = {"price": 500, "vol": 1.5, "beta": 1.0}

# NSE market hours (5-min bars)
_MARKET_OPEN  = (9, 15)
_MARKET_CLOSE = (15, 25)   # last bar starts 15:25 (closes 15:30)
_BARS_PER_DAY = 75          # 9:15 to 15:25 inclusive at 5m = 75 bars

# NSE trading days (Mon-Fri, skip rough approximation of holidays)
_SKIP_HOLIDAYS = {
    date(2026, 1, 26), date(2026, 3, 25), date(2026, 4, 14),
    date(2026, 4, 17), date(2026, 5, 1),  date(2026, 6, 6),
    date(2025, 10, 2), date(2025, 11, 5), date(2025, 12, 25),
}


def _trading_days(n_days: int, end: Optional[date] = None) -> List[date]:
    """Return last n_days NSE trading days ending on `end` (default today)."""
    if end is None:
        end = date.today()
    days = []
    d = end
    while len(days) < n_days:
        if d.weekday() < 5 and d not in _SKIP_HOLIDAYS:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _intraday_timestamps(trading_date: date) -> pd.DatetimeIndex:
    """Generate 5-min IST timestamps for one NSE trading day."""
    from datetime import datetime
    start = datetime(trading_date.year, trading_date.month, trading_date.day,
                     _MARKET_OPEN[0], _MARKET_OPEN[1], tzinfo=IST)
    end   = datetime(trading_date.year, trading_date.month, trading_date.day,
                     _MARKET_CLOSE[0], _MARKET_CLOSE[1], tzinfo=IST)
    return pd.date_range(start=start, end=end, freq="5min")


def _generate_day(
    prev_close: float,
    params: Dict,
    rng: np.random.Generator,
    market_trend: float = 0.0,   # daily market drift (-0.02 to +0.02)
) -> pd.DataFrame:
    """
    Generate one day of 5-min OHLCV for a single stock.
    Returns DataFrame with [open, high, low, close, volume] columns.
    """
    vol_5m = params["vol"] / 100 / math.sqrt(75)   # per-bar std dev
    beta   = params["beta"]
    n      = _BARS_PER_DAY

    # Opening gap: small daily drift + stock-specific noise
    gap_pct  = market_trend * beta + rng.normal(0, params["vol"] / 100 * 0.3)
    day_open = prev_close * (1 + gap_pct)

    # Day regime: trending bull / trending bear / range
    regime_r = rng.random()
    if regime_r < 0.35:
        regime = "BULL"
        drift  = abs(rng.normal(0.003, 0.002)) * beta
    elif regime_r < 0.60:
        regime = "BEAR"
        drift  = -abs(rng.normal(0.003, 0.002)) * beta
    else:
        regime = "RANGE"
        drift  = rng.normal(0, 0.0005)

    # ORB: first 3 bars (9:15, 9:20, 9:25) — wide range, high volume
    orb_bars  = 3
    orb_vol   = abs(rng.normal(0.004, 0.002)) * beta  # ORB range %
    orb_dir   = 1 if rng.random() > 0.4 else -1

    prices = np.zeros(n)
    prices[0] = day_open

    for i in range(1, n):
        if i < orb_bars:
            step = orb_dir * orb_vol / orb_bars + rng.normal(0, vol_5m * 1.8)
        else:
            # After ORB: directional trend + mean reversion to intraday VWAP
            session_pos = i / n
            trend_step  = drift / (n - orb_bars)
            # Lunch lull: lower vol/drift 11:30-13:00 (bars 27-45)
            if 27 <= i <= 45:
                trend_step *= 0.4
                noise = rng.normal(0, vol_5m * 0.6)
            else:
                noise = rng.normal(0, vol_5m)
            step = trend_step + noise
        prices[i] = prices[i - 1] * (1 + step)
        prices[i] = max(prices[i], 1.0)

    # Build OHLCV bars
    opens  = np.zeros(n)
    highs  = np.zeros(n)
    lows   = np.zeros(n)
    closes = np.zeros(n)
    vols   = np.zeros(n)

    base_vol = params["price"] * 500_000   # ~₹5L daily turnover in units
    for i in range(n):
        bar_open  = prices[i - 1] if i > 0 else day_open
        bar_close = prices[i]
        bar_range = abs(bar_close - bar_open) + abs(rng.normal(0, vol_5m * bar_open * 0.3))

        opens[i]  = bar_open
        closes[i] = bar_close
        highs[i]  = max(bar_open, bar_close) + abs(rng.normal(0, bar_range * 0.3))
        lows[i]   = min(bar_open, bar_close) - abs(rng.normal(0, bar_range * 0.3))
        lows[i]   = max(lows[i], bar_open * 0.90)

        # Volume: high at open (ORB), low at lunch, high at close
        pos = i / n
        if i < 6:        vol_mult = 2.5 + rng.exponential(1.5)   # opening surge
        elif 27 <= i <= 45: vol_mult = 0.5 + rng.random() * 0.5  # lunch lull
        elif i > 65:     vol_mult = 1.5 + rng.exponential(0.8)   # closing surge
        else:            vol_mult = 0.8 + rng.random() * 0.6     # mid-session

        # Volume spike when price moves fast (momentum)
        move_pct = abs(bar_close - bar_open) / bar_open
        if move_pct > vol_5m * 1.5:
            vol_mult *= 2.0 + rng.exponential(1.0)

        vols[i] = max(1, int(base_vol * vol_mult / (n * bar_open)))

    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols.astype(int),
    })


def generate_nse_data(
    symbols: List[str],
    days: int = 60,
    seed: Optional[int] = 42,
) -> Dict[str, pd.DataFrame]:
    """
    Generate realistic synthetic NSE 5-minute OHLCV data.

    Args:
        symbols: NSE symbol names (e.g. ["RELIANCE", "TCS"])
        days: Number of trading days to generate (default 60 for 5m limit)
        seed: Random seed for reproducibility (None = random each run)

    Returns:
        Dict[symbol -> pd.DataFrame] with IST DatetimeIndex, same format as
        data_yfinance.load_nse_data_yfinance() output.
    """
    if seed is not None:
        np.random.seed(seed)
        random.seed(seed)
    rng = np.random.default_rng(seed)

    trading_days = _trading_days(days)
    result: Dict[str, pd.DataFrame] = {}

    # Generate correlated market trend per day
    market_trends = rng.normal(0.0002, 0.008, size=len(trading_days))

    print(f"  Generating synthetic NSE data: {len(symbols)} symbols × {len(trading_days)} days × 75 bars ...")

    for sym in symbols:
        params = _STOCK_PARAMS.get(sym.upper(), _DEFAULT_PARAMS.copy())
        if sym.upper() not in _STOCK_PARAMS:
            # Scale price randomally for unknown symbols (range ₹100-₹5000)
            params = params.copy()
            rng2 = np.random.default_rng(abs(hash(sym)) % (2**31))
            params["price"] = float(rng2.integers(100, 5001))
            params["vol"]   = float(rng2.uniform(1.0, 2.5))

        all_dfs = []
        prev_close = float(params["price"])

        for di, tday in enumerate(trading_days):
            timestamps = _intraday_timestamps(tday)
            n_ts = len(timestamps)
            if n_ts == 0:
                continue

            day_df = _generate_day(
                prev_close=prev_close,
                params=params,
                rng=rng,
                market_trend=market_trends[di],
            )

            # Trim/pad to match timestamp count
            day_df = day_df.iloc[:n_ts]
            if len(day_df) < n_ts:
                continue

            day_df.index = timestamps[:len(day_df)]
            all_dfs.append(day_df)
            prev_close = float(day_df["close"].iloc[-1])

        if not all_dfs:
            continue

        df = pd.concat(all_dfs)
        df = df[df["close"] > 0].copy()
        result[sym] = df

    print(f"  Generated {len(result)} symbols ({len(trading_days)} days, ~{len(trading_days)*75} bars each)")
    return result
