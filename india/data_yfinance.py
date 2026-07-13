"""
NSE historical data loader using yfinance (Yahoo Finance).
Enables 2-5 year backtests on NSE stocks using hourly bars.

Usage:
    from data_yfinance import load_nse_data_yfinance
    data = load_nse_data_yfinance(symbols, period="3y", interval="1h")

NSE ticker format on Yahoo: RELIANCE.NS, TCS.NS, INFY.NS
"""
from __future__ import annotations
import logging
import warnings
import pandas as pd
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

IST = ZoneInfo("Asia/Kolkata")

# Yahoo Finance NSE suffix
NSE_SUFFIX = ".NS"

# Silence yfinance's noisy "no data found" / "delisted" warnings
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Ordered by intraday momentum quality: banking/auto/metals have strongest 1h trends.
# IT stocks (TCS, INFY, WIPRO) placed at back — they trend slowly intraday.
# Default backtest uses first 40: covers high-momentum sectors across NSE.
# Removed: ADANIENT/ADANIPORTS (0% WR, promoter manipulation), DRREDDY (event risk),
#           TATAMOTORS (Yahoo 404), PAYTM (speculative), HDFC (merged into HDFCBANK),
#           ADANIGREEN/ADANITRANS (Adani group), M&MFIN (broken ticker), SHREECEM (low volume)
DEFAULT_SYMBOLS = [
    # Banking & Finance (strong intraday momentum, institutional volume)
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "BAJFINANCE", "INDUSINDBK",
    # Energy & Commodities (trending, volume-driven)
    "RELIANCE", "ONGC", "BPCL", "COALINDIA",
    # Auto (momentum sector, strong trends)
    "M&M", "MARUTI", "BAJAJ-AUTO", "HEROMOTOCO", "EICHERMOT", "BALKRISIND",
    # Metals & Infrastructure (high beta, strong momentum)
    "TATASTEEL", "JSWSTEEL", "HINDALCO", "LT",
    # IT (slower intraday momentum — move to back)
    "TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "PERSISTENT",
    # Telecom & Consumer
    "BHARTIARTL", "ITC", "ASIANPAINT",
    # FMCG & Retail
    "GODREJCP", "TRENT",
    # Pharma
    "SUNPHARMA", "CIPLA", "DIVISLAB",
    # Finance & Conglomerates
    "BAJAJFINSV", "TITAN", "ULTRACEMCO", "NTPC",
    "POWERGRID", "HAVELLS",
    # Industrials & Consumer Durables (strong momentum, institutional participation)
    "POLYCAB", "VOLTAS", "CROMPTON", "CUMMINSIND",
    # Building Materials (clean trend followers)
    "ASTRAL", "SUPREMEIND",
]


def _process_raw_df(raw: pd.DataFrame, interval: str) -> Optional[pd.DataFrame]:
    """Normalize a raw yfinance DataFrame to standard OHLCV IST format."""
    if raw is None or raw.empty:
        return None
    raw = raw.copy()
    raw.columns = [c.lower() for c in raw.columns]
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in raw.columns]
    if not cols or "close" not in cols:
        return None
    df = raw[cols]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(IST)
    if interval not in ("1d", "1wk"):
        df = df.between_time("09:15", "15:30")
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    return df if len(df) >= 20 else None


def _fetch_one(sym: str, effective_period: str, interval: str) -> tuple:
    """Download a single NSE symbol. Returns (sym, df_or_None)."""
    import yfinance as yf
    ticker_str = sym + NSE_SUFFIX
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = yf.Ticker(ticker_str).history(
                period=effective_period,
                interval=interval,
                auto_adjust=True,
            )
        df = _process_raw_df(raw, interval)
        return sym, df
    except Exception:
        return sym, None


def load_nse_data_yfinance(
    symbols: List[str],
    period: str = "2y",
    interval: str = "1h",
    verbose: bool = True,
    max_workers: int = 20,
) -> Dict[str, pd.DataFrame]:
    """
    Load NSE historical data from Yahoo Finance using parallel threads.

    Downloads all symbols concurrently (default 20 threads) — much faster
    than sequential (200 symbols: ~60s vs 10+ min) without the brittle
    MultiIndex column structure of yf.download() batch mode.

    Args:
        symbols: NSE symbols WITHOUT .NS suffix (e.g. ["RELIANCE", "TCS"])
        period: "1y", "2y", "3y", "5y"
        interval: "1h", "5m", "1d"
        verbose: Print loading progress
        max_workers: Parallel download threads (20 is safe for Yahoo rate limits)
    """
    try:
        import yfinance as yf  # noqa: F401 — verify installed
    except ImportError:
        raise ImportError("yfinance not installed. Run: pip install yfinance")

    # Cap intraday intervals to 60 days (Yahoo Finance hard limit)
    _period_days = {"1y": 365, "2y": 730, "3y": 1095, "5y": 1825}.get(period, 730)
    if interval in ("1m", "2m", "5m", "15m", "30m", "90m") and _period_days > 60:
        effective_period = "60d"
        if verbose:
            print(f"  Note: {interval} limited to 60 days on yfinance → using period=60d")
    else:
        effective_period = period

    symbols = [s.upper() for s in symbols]
    total = len(symbols)
    result: Dict[str, pd.DataFrame] = {}

    if verbose:
        print(f"  Downloading {total} symbols with {max_workers} parallel threads "
              f"(interval={interval}, period={effective_period}) ...")

    loaded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_one, sym, effective_period, interval): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym, df = future.result()
            if df is not None:
                result[sym] = df
                loaded += 1
            else:
                failed += 1

    if verbose:
        print(f"  Loaded {loaded}/{total} symbols "
              f"({failed} unavailable on Yahoo Finance).")
        if total >= 30 and loaded < 20:
            print(f"  WARNING: Only {loaded} symbols loaded — check internet connection.")
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
