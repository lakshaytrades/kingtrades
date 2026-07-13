"""
generate_synthetic_data.py — Realistic NSE synthetic OHLCV data generator.

Creates 60 days of 5-minute IST intraday bars for NSE stocks when Yahoo Finance
is not accessible. Uses GBM + regime switching to produce realistic:
- Trending days vs ranging days
- ORB breakout patterns (9:15-9:30 opening range + breakout)
- ORB fake-breakouts (35% of bull days: breakout that reverses — realistic NSE trap)
- VWAP deviation and mean-reversion
- Volume surges on momentum bars (high at open, low at lunch)
- rvol (relative volume) column for trading engine
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
    market_trend: float = 0.0,
) -> pd.DataFrame:
    """
    Generate one day of realistic 5-min NSE OHLCV.

    Produces 3 regime types matching real NSE distribution:
    - STRONG_BULL (50%): ORB breaks high, 75% of bars are bullish, trends to +1.5-3%
      - 35% of bull days are ORB fake-breakouts (breaks out then reverses — realistic NSE trap)
    - STRONG_BEAR (22%): ORB breaks low, 70% of bars are bearish, trends to -1.5-3%
    - RANGE (28%): choppy within ±0.5% of open, VWAP mean-reversion, low volume

    Volume follows NSE microstructure:
    - Opening (first 7 bars = 35 min): always high volume (2.5-4× base)
    - Lunch lull (bars 35-50 ≈ 11:30-13:00): low volume (0.3-0.6×)
    - Normal session: moderate (0.7-1.5×)
    - Direction bonus only on truly strong bars (not always 5×)
    """
    vol_5m = params["vol"] / 100 / math.sqrt(75)
    beta   = params["beta"]
    n      = _BARS_PER_DAY

    # Opening gap
    gap_pct  = market_trend * beta + rng.normal(0, params["vol"] / 100 * 0.25)
    day_open = max(prev_close * (1 + gap_pct), 1.0)

    # Day regime — real NSE distribution: ~50-55% bull, ~20-25% bear, ~15-20% range
    regime_r = rng.random()
    if regime_r < 0.50:
        regime = "STRONG_BULL"    # 50%: more bull days (real NSE has ~55% bull)
        day_drift  = abs(rng.normal(0.012, 0.005)) * beta    # +1.2% avg intraday gain
        orb_dir    = 1
    elif regime_r < 0.72:
        regime = "STRONG_BEAR"    # 22%: fewer bear days
        day_drift  = -abs(rng.normal(0.012, 0.005)) * beta
        orb_dir    = -1
    else:
        regime = "RANGE"          # 28%: much less choppy (real NSE ~15-20%)
        day_drift  = rng.normal(0, 0.003)
        orb_dir    = 1 if rng.random() > 0.5 else -1

    # ORB: first 3 bars (9:15-9:25) — defines the day's opening range
    # On trend days, ORB moves strongly in direction and STAYS there
    orb_range_pct = abs(rng.normal(0.006, 0.002)) * beta
    prices = np.zeros(n)
    prices[0] = day_open

    for i in range(1, 4):
        if i < 3:
            # ORB formation: directional with high noise
            step = orb_dir * orb_range_pct / 3 + rng.normal(0, vol_5m * 2.0)
        else:
            # Bar 3 (9:30): ORB breakout bar — clear directional push
            if regime in ("STRONG_BULL", "STRONG_BEAR"):
                # Clean breakout: strong bar in trend direction
                step = orb_dir * abs(rng.normal(vol_5m * 2.5, vol_5m * 0.5))
            else:
                step = rng.normal(0, vol_5m * 1.2)
        prices[i] = max(prices[i-1] * (1 + step), 1.0)

    # 35% of bull days: ORB fake-breakout (breaks out then reverses — realistic NSE trap)
    # The ORB bar looks like a real breakout but the rest of the day reverses
    # On fake-out days: price broke out to ~+0.5-1% above open, then chops back down
    # but doesn't fully revert — closes near the ORB high (~+0.3% typically)
    _orb_fakeout = (regime == "STRONG_BULL" and rng.random() < 0.35)
    if _orb_fakeout:
        regime = "RANGE"   # Treat rest of day as choppy after fake breakout
        # Keep the ORB bar as a bull bar (fake breakout looks real at first)

    # RANGE anchor: the level around which range/fakeout days oscillate
    # - True RANGE days: small positive bias (NSE has systematic upward drift from
    #   index fund inflows, MF SIPs) → close slightly above open ~60% of the time
    # - Fake-out days: anchor slightly BELOW open — the fake-out reversal traps
    #   buyers, price falls below pre-breakout level (classic NSE bull trap)
    _range_anchor = day_open * 0.997 if _orb_fakeout else day_open * 1.002

    # Post-ORB: regime-driven trend
    for i in range(4, n):
        bar_in_session = i - 3
        total_post_orb = n - 3
        prog = bar_in_session / total_post_orb

        if regime == "STRONG_BULL":
            # Sustained uptrend with small pullbacks — 75% bull bars
            if rng.random() < 0.75:
                step = abs(rng.normal(day_drift / total_post_orb, vol_5m * 1.0))
            else:
                step = -abs(rng.normal(0, vol_5m * 0.7))  # small pullback
            # Lunch lull: lower drift bars 27-45
            if 27 <= i <= 45:
                step *= 0.3
        elif regime == "STRONG_BEAR":
            if rng.random() < 0.70:
                step = -abs(rng.normal(abs(day_drift) / total_post_orb, vol_5m * 1.0))
            else:
                step = abs(rng.normal(0, vol_5m * 0.7))
            if 27 <= i <= 45:
                step *= 0.3
        else:
            # RANGE: oscillate within ±0.5% of anchor, mean-revert to slightly-positive anchor
            # Institutions don't participate — tight, low-conviction moves
            # Real NSE range days close slightly positive due to market-wide upward drift
            range_boundary = _range_anchor * 0.005   # ±0.5% boundary around anchor
            current_dev = prices[i-1] - _range_anchor
            # Strong mean-reversion pull toward anchor (midpoint with positive bias)
            mean_rev = -(current_dev / _range_anchor) * 0.45
            # Clamp: if price is outside ±0.5% band, push it back harder
            if abs(current_dev) > range_boundary:
                mean_rev = -(current_dev / _range_anchor) * 0.80
            # Use reduced noise on range days (lower conviction)
            step = mean_rev + rng.normal(0, vol_5m * 0.8)

        prices[i] = max(prices[i-1] * (1 + step), 1.0)

    # Build OHLCV bars
    opens  = np.zeros(n)
    highs  = np.zeros(n)
    lows   = np.zeros(n)
    closes = np.zeros(n)
    vols   = np.zeros(n)
    base_vol = params["price"] * 400_000

    for i in range(n):
        bar_open  = prices[i-1] if i > 0 else day_open
        bar_close = prices[i]
        move      = abs(bar_close - bar_open)
        noise_h   = abs(rng.normal(0, vol_5m * bar_open * 0.60))
        noise_l   = abs(rng.normal(0, vol_5m * bar_open * 0.60))
        opens[i]  = bar_open
        closes[i] = bar_close
        highs[i]  = max(bar_open, bar_close) + noise_h
        lows[i]   = max(min(bar_open, bar_close) - noise_l, bar_open * 0.85)

        # Volume: high at open (9:15-9:45) regardless of direction, drops at lunch
        # This matches real NSE microstructure (institutions trade on open/close)
        _is_opening = (i < 7)   # First 7 bars = first 35 min
        _is_lunch   = (35 <= i < 50)   # Lunch lull: bars 35-50 (11:30-13:00 approx)
        _base_vol_mult = (
            rng.uniform(2.5, 4.0) if _is_opening else   # Opening always high volume
            rng.uniform(0.3, 0.6) if _is_lunch else      # Lunch: low volume
            rng.uniform(0.7, 1.5)                         # Normal session
        )

        # Range days: even lower institutional participation
        if regime == "RANGE":
            _base_vol_mult *= 0.65

        # Direction bonus: add 0.3-0.8× only on truly strong moves (not always 5×)
        _dir_bonus = rng.uniform(0.3, 0.8) if abs(move / max(bar_open, 1)) > vol_5m * 1.5 else 0.0
        vol_mult = _base_vol_mult + _dir_bonus

        # Closing auction: extra volume in last 5 bars
        if i > 70:
            vol_mult *= 1.5 + rng.exponential(0.5)

        vols[i] = max(1, int(base_vol * vol_mult / (n * max(bar_open, 1))))

    day_df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols.astype(int),
    })

    # Compute rvol = volume / 20-bar rolling average volume
    # Needed by trading engine for volume surge detection
    if "volume" in day_df.columns:
        avg_vol = day_df["volume"].rolling(20, min_periods=5).mean()
        day_df["rvol"] = day_df["volume"] / avg_vol.replace(0, 1e-9)
        day_df["rvol"] = day_df["rvol"].fillna(1.0).clip(0.1, 10.0)

    return day_df


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
        Columns: open, high, low, close, volume, rvol
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

        # Cross-day rvol: recompute rvol using rolling window across full symbol data
        # (within-day rvol from _generate_day is overwritten here for accuracy)
        avg_vol_cross = df["volume"].rolling(20, min_periods=5).mean()
        df["rvol"] = df["volume"] / avg_vol_cross.replace(0, 1e-9)
        df["rvol"] = df["rvol"].fillna(1.0).clip(0.1, 10.0)

        result[sym] = df

    print(f"  Generated {len(result)} symbols ({len(trading_days)} days, ~{len(trading_days)*75} bars each)")
    return result
