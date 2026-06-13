"""
NSE historical data loader using yfinance (Yahoo Finance).
Enables 2-5 year backtests on NSE stocks using hourly bars.

Usage:
    from data_yfinance import load_nse_data_yfinance
    data = load_nse_data_yfinance(symbols, period="3y", interval="1h")

NSE ticker format on Yahoo: RELIANCE.NS, TCS.NS, INFY.NS
"""
from __future__ import annotations
import pandas as pd
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional

IST = ZoneInfo("Asia/Kolkata")

# Yahoo Finance NSE suffix
NSE_SUFFIX = ".NS"

# Default 50 liquid NSE symbols for backtesting
DEFAULT_SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "BAJFINANCE", "ASIANPAINT", "MARUTI",
    "TITAN", "SUNPHARMA", "ULTRACEMCO", "NESTLEIND", "WIPRO",
    "HCLTECH", "TECHM", "POWERGRID", "ONGC", "NTPC",
    "COALINDIA", "TATAMOTORS", "JSWSTEEL", "TATASTEEL", "ADANIENT",
    "DIVISLAB", "DRREDDY", "CIPLA", "EICHERMOT", "BAJAJFINSV",
    "BAJAJ-AUTO", "HEROMOTOCO", "GRASIM", "BRITANNIA", "INDUSINDBK",
    "BPCL", "HINDALCO", "VEDL", "SBILIFE", "HDFCLIFE",
    "PIDILITIND", "TORNTPHARM", "BERGEPAINT", "MUTHOOTFIN", "HAVELLS",
]


def load_nse_data_yfinance(
    symbols: List[str],
    period: str = "2y",
    interval: str = "1h",
    verbose: bool = True,
) -> Dict[str, pd.DataFrame]:
    """
    Load NSE historical data from Yahoo Finance.

    Args:
        symbols: List of NSE symbols WITHOUT .NS suffix (e.g. ["RELIANCE", "TCS"])
        period: "1y", "2y", "3y", "5y" (Yahoo Finance period string)
        interval: "1h" for hourly (2yr max), "1d" for daily (unlimited)
        verbose: Print loading progress

    Returns:
        Dict[symbol -> pd.DataFrame] with columns: open, high, low, close, volume
        Index is IST-timezone DatetimeIndex.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed. Run: pip install yfinance")

    result: Dict[str, pd.DataFrame] = {}
    total = len(symbols)

    for i, sym in enumerate(symbols):
        ticker_str = sym.upper() + NSE_SUFFIX
        try:
            ticker = yf.Ticker(ticker_str)
            # For intervals > 1d, Yahoo caps at 730 days; use "max" for daily
            if interval in ("1d", "1wk"):
                raw = ticker.history(period=period, interval=interval, auto_adjust=True)
            else:
                # Hourly: Yahoo caps at 730 days regardless of period string
                raw = ticker.history(period=period, interval=interval, auto_adjust=True)

            if raw is None or raw.empty:
                if verbose:
                    print(f"  [{i+1}/{total}] {sym}: no data from Yahoo")
                continue

            # Rename to lowercase
            raw.columns = [c.lower() for c in raw.columns]

            # Keep only OHLCV columns
            cols_needed = [c for c in ["open", "high", "low", "close", "volume"] if c in raw.columns]
            df = raw[cols_needed].copy()

            # Convert index to IST
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df.index = df.index.tz_convert(IST)

            # For intraday intervals, filter to market hours only
            if interval not in ("1d", "1wk"):
                df = df.between_time("09:15", "15:30")

            # Drop rows with NaN close
            df = df.dropna(subset=["close"])
            df = df[df["close"] > 0]

            if len(df) < 20:
                if verbose:
                    print(f"  [{i+1}/{total}] {sym}: too few bars ({len(df)}), skipping")
                continue

            result[sym] = df
            if verbose:
                print(f"  [{i+1}/{total}] {sym}: {len(df)} bars ({df.index[0].date()} -> {df.index[-1].date()})")

        except Exception as e:
            if verbose:
                print(f"  [{i+1}/{total}] {sym}: error - {e}")
            continue

    return result


def get_nifty50_data(period: str = "2y", interval: str = "1h") -> Optional[pd.DataFrame]:
    """Load Nifty50 index data for market regime filter."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("^NSEI")
        raw = ticker.history(period=period, interval=interval, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        raw.columns = [c.lower() for c in raw.columns]
        df = raw[["open", "high", "low", "close", "volume"]].copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(IST)
        if interval not in ("1d", "1wk"):
            df = df.between_time("09:15", "15:30")
        return df.dropna(subset=["close"])
    except Exception:
        return None
