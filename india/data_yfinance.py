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


def load_nse_data_yfinance(
    symbols: List[str],
    period: str = "2y",
    interval: str = "1h",
    verbose: bool = True,
    batch_size: int = 50,
) -> Dict[str, pd.DataFrame]:
    """
    Load NSE historical data from Yahoo Finance using batch downloads.

    Uses yf.download() to fetch all tickers in parallel batches — much faster
    than one-by-one Ticker.history() calls (200 symbols: ~30s vs ~10 min).

    Args:
        symbols: NSE symbols WITHOUT .NS suffix (e.g. ["RELIANCE", "TCS"])
        period: "1y", "2y", "3y", "5y"
        interval: "1h", "5m", "1d"
        verbose: Print loading progress
        batch_size: Symbols per yf.download() call (50 is safe for Yahoo rate limits)
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed. Run: pip install yfinance")

    # Cap 5m/intraday to 60 days (Yahoo Finance hard limit)
    _period_days = {"1y": 365, "2y": 730, "3y": 1095, "5y": 1825}.get(period, 730)
    if interval in ("1m", "2m", "5m", "15m", "30m", "90m") and _period_days > 60:
        effective_period = "60d"
        if verbose:
            print(f"  Note: {interval} limited to 60 days on yfinance → using period=60d")
    else:
        effective_period = period

    symbols = [s.upper() for s in symbols]
    tickers_ns = [s + NSE_SUFFIX for s in symbols]
    sym_map = {s + NSE_SUFFIX: s for s in symbols}  # "RELIANCE.NS" → "RELIANCE"
    total = len(symbols)
    result: Dict[str, pd.DataFrame] = {}

    if verbose:
        print(f"  Batch-downloading {total} symbols in groups of {batch_size} "
              f"(interval={interval}, period={effective_period}) ...")

    # Download in batches to avoid Yahoo rate limits
    for batch_start in range(0, total, batch_size):
        batch_ns = tickers_ns[batch_start: batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        if verbose:
            syms_preview = ", ".join(s.replace(NSE_SUFFIX, "") for s in batch_ns[:5])
            print(f"  Batch {batch_num}/{total_batches}: {len(batch_ns)} symbols ({syms_preview}...)")
        try:
            raw_all = yf.download(
                tickers=batch_ns,
                period=effective_period,
                interval=interval,
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as e:
            if verbose:
                print(f"  Batch {batch_num} failed: {e}")
            continue

        if raw_all is None or raw_all.empty:
            continue

        # Single ticker: yf.download returns flat columns (no MultiIndex)
        if len(batch_ns) == 1:
            sym_ns = batch_ns[0]
            sym = sym_map[sym_ns]
            df = _process_raw_df(raw_all, interval)
            if df is not None:
                result[sym] = df
                if verbose:
                    print(f"    {sym}: {len(df)} bars")
            continue

        # Multiple tickers: MultiIndex columns — level 0 = ticker, level 1 = OHLCV field
        loaded_batch = 0
        for sym_ns in batch_ns:
            sym = sym_map[sym_ns]
            try:
                if sym_ns not in raw_all.columns.get_level_values(0):
                    continue
                ticker_df = raw_all[sym_ns].copy()
                df = _process_raw_df(ticker_df, interval)
                if df is not None:
                    result[sym] = df
                    loaded_batch += 1
            except Exception:
                continue
        if verbose:
            print(f"    → {loaded_batch}/{len(batch_ns)} symbols loaded")

    loaded = len(result)
    if verbose:
        print(f"\n  Loaded {loaded}/{total} symbols successfully.")
        if total >= 30 and loaded < 30:
            print(f"  WARNING: Only {loaded} symbols loaded. "
                  "Some symbols may be unavailable on Yahoo Finance.")
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
