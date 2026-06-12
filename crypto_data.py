"""
crypto_data.py — Crypto Market Data via Alpaca Crypto API

Fetches OHLCV candles, live quotes, and Fear & Greed index for
BTC/USD, ETH/USD, SOL/USD and other Alpaca-supported pairs.

Key differences from stock data:
  - 24/7 data (no market hours restriction)
  - Alpaca CryptoHistoricalDataClient (separate from stocks)
  - Symbols use format "BTC/USD" (with slash)
  - WebSocket stream: wss://stream.data.alpaca.markets/v1beta3/crypto/us
"""

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")
_QUOTE_CACHE: Dict[str, dict] = {}
_QUOTE_CACHE_TTL = 15    # seconds — crypto prices move fast
_BAR_CACHE: Dict[str, pd.DataFrame] = {}
_BAR_CACHE_TS: Dict[str, float] = {}
_BAR_CACHE_TTL = 60      # 60s cache for 15-min bars


def _get_crypto_client():
    """Return Alpaca CryptoHistoricalDataClient (no auth needed for market data)."""
    try:
        from alpaca.data.historical.crypto import CryptoHistoricalDataClient
        api_key    = os.getenv("ALPACA_API_KEY", "")
        api_secret = os.getenv("ALPACA_SECRET_KEY", "")
        if api_key and api_secret:
            return CryptoHistoricalDataClient(api_key, api_secret)
        return CryptoHistoricalDataClient()
    except Exception as e:
        logger.error(f"CryptoHistoricalDataClient init failed: {e}")
        return None


def _get_trading_client():
    """Return Alpaca TradingClient for crypto orders."""
    try:
        from alpaca.trading.client import TradingClient
        api_key    = os.getenv("ALPACA_API_KEY", "")
        api_secret = os.getenv("ALPACA_SECRET_KEY", "")
        paper      = os.getenv("ALPACA_BASE_URL", "paper").lower().find("paper") >= 0
        return TradingClient(api_key, api_secret, paper=paper)
    except Exception as e:
        logger.error(f"TradingClient init failed: {e}")
        return None


def get_crypto_bars(symbol: str, timeframe_str: str = "15Min",
                    limit: int = 100) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV bars for a crypto symbol.
    symbol: "BTC/USD", "ETH/USD", etc.
    timeframe_str: "15Min", "1Hour", "4Hour", "1Day"
    Returns DataFrame with columns: open, high, low, close, volume, vwap, timestamp
    """
    cache_key = f"{symbol}_{timeframe_str}"
    now_ts = time.monotonic()
    if cache_key in _BAR_CACHE and (now_ts - _BAR_CACHE_TS.get(cache_key, 0)) < _BAR_CACHE_TTL:
        return _BAR_CACHE[cache_key]

    client = _get_crypto_client()
    if client is None:
        return _synthetic_bars(symbol, timeframe_str, limit)

    try:
        from alpaca.data.requests import CryptoBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        # Map timeframe string to Alpaca TimeFrame
        tf_map = {
            "1Min":   TimeFrame.Minute,
            "5Min":   TimeFrame(5,  TimeFrameUnit.Minute),
            "15Min":  TimeFrame(15, TimeFrameUnit.Minute),
            "30Min":  TimeFrame(30, TimeFrameUnit.Minute),
            "1Hour":  TimeFrame.Hour,
            "4Hour":  TimeFrame(4,  TimeFrameUnit.Hour),
            "1Day":   TimeFrame.Day,
        }
        tf = tf_map.get(timeframe_str, TimeFrame(15, TimeFrameUnit.Minute))

        end   = datetime.now(UTC)
        # Go back far enough for the requested bars
        hours_map = {"1Min": 2, "5Min": 12, "15Min": 36, "30Min": 72,
                     "1Hour": 200, "4Hour": 800, "1Day": 200}
        hours_back = hours_map.get(timeframe_str, 36)
        start = end - timedelta(hours=hours_back)

        req = CryptoBarsRequest(
            symbol_or_symbols = symbol,
            timeframe          = tf,
            start              = start,
            end                = end,
            limit              = limit,
        )
        bars_resp = client.get_crypto_bars(req)
        df = bars_resp.df

        if df is None or df.empty:
            return None

        # Flatten multi-index if symbol is in index
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0) if symbol in df.index.get_level_values(0) else df.reset_index(level=0, drop=True)

        df = df.rename(columns=str.lower)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df.sort_index()

        # Ensure required columns exist
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                logger.warning(f"crypto_data: missing column '{col}' for {symbol}")
                return None

        if "vwap" not in df.columns:
            # Approximate VWAP from OHLCV
            df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3

        _BAR_CACHE[cache_key] = df
        _BAR_CACHE_TS[cache_key] = now_ts
        return df

    except Exception as e:
        logger.error(f"get_crypto_bars({symbol}, {timeframe_str}): {e}")
        return _synthetic_bars(symbol, timeframe_str, limit)


def get_crypto_quote(symbol: str) -> Dict:
    """
    Get live quote for a crypto pair.
    Returns dict with: symbol, ltp, bid, ask, volume_24h, change_pct_24h
    """
    now_ts = time.monotonic()
    cached = _QUOTE_CACHE.get(symbol)
    if cached and (now_ts - cached.get("_ts", 0)) < _QUOTE_CACHE_TTL:
        return cached

    client = _get_crypto_client()
    if client is None:
        return {}

    try:
        from alpaca.data.requests import CryptoLatestQuoteRequest, CryptoLatestBarRequest

        # Latest quote
        quote_req = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
        quote_resp = client.get_crypto_latest_quote(quote_req)
        quote = quote_resp.get(symbol) if quote_resp else None

        bid = ask = ltp = 0.0
        if quote:
            bid = float(getattr(quote, "bid_price", 0) or 0)
            ask = float(getattr(quote, "ask_price", 0) or 0)
            ltp = (bid + ask) / 2 if bid and ask else 0.0

        # Latest bar for volume + price fallback
        bar_req = CryptoLatestBarRequest(symbol_or_symbols=symbol)
        bar_resp = client.get_crypto_latest_bar(bar_req)
        bar = bar_resp.get(symbol) if bar_resp else None

        volume = 0.0
        if bar:
            if ltp == 0:
                ltp = float(getattr(bar, "close", 0) or 0)
            volume = float(getattr(bar, "volume", 0) or 0)

        result = {
            "symbol":        symbol,
            "ltp":           ltp,
            "bid":           bid,
            "ask":           ask,
            "volume":        volume,
            "change_pct":    0.0,   # computed from bars
            "_ts":           now_ts,
        }

        # Compute 24h change from daily bar
        try:
            daily_df = get_crypto_bars(symbol, "1Day", limit=2)
            if daily_df is not None and len(daily_df) >= 2:
                prev_close = float(daily_df["close"].iloc[-2])
                if prev_close > 0 and ltp > 0:
                    result["change_pct"] = round((ltp - prev_close) / prev_close * 100, 2)
        except Exception:
            pass

        _QUOTE_CACHE[symbol] = result
        return result

    except Exception as e:
        logger.debug(f"get_crypto_quote({symbol}): {e}")
        return {}


def get_fear_greed_index() -> int:
    """
    Fetch Crypto Fear & Greed Index (0=extreme fear, 100=extreme greed).
    Uses alternative.me API (free, no auth).
    Returns cached value on failure (default 50 = neutral).
    """
    _fng_cache = getattr(get_fear_greed_index, "_cache", None)
    _fng_ts    = getattr(get_fear_greed_index, "_ts", 0)
    if _fng_cache is not None and (time.monotonic() - _fng_ts) < 3600:
        return _fng_cache

    try:
        import urllib.request
        import json
        url  = "https://api.alternative.me/fng/?limit=1"
        req  = urllib.request.Request(url, headers={"User-Agent": "SataVector/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        val = int(data["data"][0]["value"])
        get_fear_greed_index._cache = val
        get_fear_greed_index._ts    = time.monotonic()
        logger.info(f"Fear & Greed Index: {val} ({data['data'][0]['value_classification']})")
        return val
    except Exception as e:
        logger.debug(f"Fear & Greed fetch failed: {e}")
        return getattr(get_fear_greed_index, "_cache", 50)


def get_btc_change_pct(hours: int = 1) -> float:
    """
    Get BTC price change over the last N hours.
    Used to detect BTC drag/boost effect on altcoins.
    """
    try:
        df = get_crypto_bars("BTC/USD", "1Hour", limit=hours + 2)
        if df is None or len(df) < 2:
            return 0.0
        ref_price  = float(df["close"].iloc[-(hours + 1)])
        curr_price = float(df["close"].iloc[-1])
        if ref_price <= 0:
            return 0.0
        return round((curr_price - ref_price) / ref_price * 100, 2)
    except Exception as e:
        logger.debug(f"get_btc_change_pct: {e}")
        return 0.0


_FUNDING_CACHE: Dict[str, dict] = {}
_FUNDING_CACHE_TTL = 1800  # 30 minutes (funding settles every 8h)


def get_funding_rate(symbol: str) -> Dict:
    """
    Fetch perpetual futures funding rate from Binance public API.
    High positive = longs paying shorts = bearish pressure
    High negative = shorts paying longs = potential short squeeze (bullish)

    symbol: "BTC/USD", "ETH/USD", "SOL/USD"
    Returns: {"rate": float, "annualized": float, "sentiment": "LONG_HEAVY"|"SHORT_HEAVY"|"NEUTRAL"}
    """
    # Map to Binance symbol format
    sym_map = {"BTC/USD": "BTCUSDT", "ETH/USD": "ETHUSDT", "SOL/USD": "SOLUSDT"}
    binance_sym = sym_map.get(symbol)
    if not binance_sym:
        return {"rate": 0.0, "annualized": 0.0, "sentiment": "NEUTRAL"}

    now_ts = time.monotonic()
    cached = _FUNDING_CACHE.get(symbol)
    if cached and (now_ts - cached.get("_ts", 0)) < _FUNDING_CACHE_TTL:
        return cached

    try:
        import urllib.request
        import json
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={binance_sym}"
        req = urllib.request.Request(url, headers={"User-Agent": "SataVector/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        rate = float(data.get("lastFundingRate", 0))
        annualized = rate * 3 * 365 * 100  # 3 settlements/day * 365 * 100%
        sentiment = (
            "LONG_HEAVY"  if rate >  0.0005   # > 0.05% = longs overextended
            else "SHORT_HEAVY" if rate < -0.0003  # < -0.03% = shorts overextended
            else "NEUTRAL"
        )
        result = {"rate": rate, "annualized": annualized, "sentiment": sentiment, "_ts": now_ts}
        _FUNDING_CACHE[symbol] = result
        logger.debug(f"Funding rate {symbol}: {rate*100:.4f}% ({sentiment})")
        return result
    except Exception as e:
        logger.debug(f"get_funding_rate({symbol}): {e}")
        return {"rate": 0.0, "annualized": 0.0, "sentiment": "NEUTRAL", "_ts": now_ts}


def get_crypto_account_info() -> Dict:
    """Get Alpaca account balance and crypto buying power."""
    try:
        client = _get_trading_client()
        if not client:
            return {}
        acct = client.get_account()
        return {
            "equity":           float(acct.equity or 0),
            "cash":             float(acct.cash or 0),
            "buying_power":     float(acct.buying_power or 0),
            "portfolio_value":  float(acct.portfolio_value or 0),
        }
    except Exception as e:
        logger.debug(f"get_crypto_account_info: {e}")
        return {}


def get_crypto_positions() -> List[Dict]:
    """Get all open crypto positions from Alpaca."""
    try:
        client = _get_trading_client()
        if not client:
            return []
        positions = client.get_all_positions()
        result = []
        for p in positions:
            sym = str(getattr(p, "symbol", ""))
            # Alpaca crypto positions use "BTCUSD" format (no slash)
            if any(c in sym for c in ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE"]):
                result.append({
                    "symbol":    sym,
                    "side":      str(getattr(p, "side", "long")).lower(),
                    "qty":       float(getattr(p, "qty", 0) or 0),
                    "entry":     float(getattr(p, "avg_entry_price", 0) or 0),
                    "current":   float(getattr(p, "current_price", 0) or 0),
                    "unrealized_pnl": float(getattr(p, "unrealized_pl", 0) or 0),
                    "market_value":   float(getattr(p, "market_value", 0) or 0),
                })
        return result
    except Exception as e:
        logger.debug(f"get_crypto_positions: {e}")
        return []


def _synthetic_bars(symbol: str, timeframe_str: str, limit: int) -> Optional[pd.DataFrame]:
    """
    Generate synthetic OHLCV data for testing when API is unavailable.
    Returns None in production — only used during development/testing.
    """
    if os.getenv("CRYPTO_SYNTHETIC_DATA", "False").lower() not in ("true", "1"):
        return None
    import numpy as np
    np.random.seed(42)
    prices = {"BTC/USD": 67000, "ETH/USD": 3500, "SOL/USD": 180}.get(symbol, 100)
    dates  = pd.date_range(end=datetime.now(UTC), periods=limit, freq="15min", tz="UTC")
    rng    = np.random.RandomState(hash(symbol) % 2**31)
    close  = prices * np.cumprod(1 + rng.normal(0.0002, 0.003, limit))
    hi     = close * (1 + rng.uniform(0.001, 0.005, limit))
    lo     = close * (1 - rng.uniform(0.001, 0.005, limit))
    vol    = rng.uniform(100, 500, limit) * (prices / 100)
    return pd.DataFrame({"open": close, "high": hi, "low": lo, "close": close,
                          "volume": vol, "vwap": (hi + lo + close) / 3}, index=dates)
