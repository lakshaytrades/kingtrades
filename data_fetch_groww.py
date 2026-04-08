"""
data_fetch_groww.py — NSE Momentum Groww AI Bot
Real-time quotes + OHLCV candles via growwapi SDK

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — ALL timestamps converted to IST.

Features:
- Real-time LTP, OHLC, volume via get_quote()
- Historical 5-min, 15-min, 1-hour OHLCV candles
- Nifty50 index data for relative strength
- Auto-retry with exponential backoff
- All timestamps in IST
- LEVERAGE: Configured for MIS (Intraday)
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

from utils import (
    format_ist_timestamp, get_current_ist_time, convert_to_ist,
    convert_candle_timestamps_to_ist, filter_market_hours,
    retry_with_backoff, get_market_open_datetime_ist
)
from auth_groww import get_groww_token

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Candle interval mapping for Groww API
INTERVAL_MAP = {
    "1m":  "1minute",
    "5m":  "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h":  "60minute",
    "1d":  "1day",
}

class GrowwDataFetcher:
    """
    Fetches market data from Groww via growwapi SDK.
    All returned DataFrames have IST-aware datetime index.
    """

    def __init__(self):
        self._api = None
        self._cache: Dict[str, Tuple[pd.DataFrame, datetime]] = {}
        self._cache_ttl_seconds = 30 
        self._init_api()

    def _init_api(self):
        """Initialize Groww API client with current token."""
        try:
            from growwapi import GrowwAPI
            token = get_groww_token()
            if token:
                self._api = GrowwAPI(token)
                logger.info(f"[{format_ist_timestamp()}] GrowwAPI initialized")
            else:
                logger.error(f"[{format_ist_timestamp()}] No Groww token — data fetch disabled")
        except ImportError:
            logger.error(f"[{format_ist_timestamp()}] growwapi not installed.")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] GrowwAPI init failed: {e}")

    def _refresh_api_if_needed(self):
        """Re-initialize API if token was refreshed."""
        try:
            new_token = get_groww_token()
            if new_token:
                from growwapi import GrowwAPI
                self._api = GrowwAPI(new_token)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] API refresh failed: {e}")

    # --------------------------------------------------------
    # REAL-TIME QUOTES
    # --------------------------------------------------------

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_quote(self, symbol: str) -> Optional[Dict]:
        if not self._api:
            self._init_api()
            if not self._api: return None

        cache_key = f"quote_{symbol}"
        if cache_key in self._cache:
            cached_data, cached_time = self._cache[cache_key]
            if (get_current_ist_time() - cached_time).total_seconds() < self._cache_ttl_seconds:
                return cached_data

        try:
            # FIX: Mandatory exchange and segment added
            raw = self._api.get_quote(trading_symbol=symbol, exchange="NSE", segment="CASH")
            if not raw: return None

            quote = {
                "symbol": symbol,
                "ltp": float(raw.get("ltp") or raw.get("last_price") or 0),
                "open": float(raw.get("open") or 0),
                "high": float(raw.get("high") or 0),
                "low": float(raw.get("low") or 0),
                "close": float(raw.get("close") or raw.get("prev_close") or 0),
                "volume": int(raw.get("volume") or 0),
                "change": float(raw.get("change") or 0),
                "change_pct": float(raw.get("change_percent") or 0),
                "bid": float(raw.get("bid") or 0),
                "ask": float(raw.get("ask") or 0),
                "product": "MIS", # Support for Leverage
                "timestamp": format_ist_timestamp(),
                "timestamp_dt": get_current_ist_time(),
            }
            self._cache[cache_key] = (quote, get_current_ist_time())
            return quote
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] get_quote({symbol}) failed: {e}")
            raise 

    def get_ltp(self, symbol: str) -> Optional[float]:
        quote = self.get_quote(symbol)
        return quote["ltp"] if quote else None

    def get_multiple_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        results = {}
        for symbol in symbols:
            try:
                quote = self.get_quote(symbol)
                if quote: results[symbol] = quote
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] Quote failed for {symbol}: {e}")
        return results

    # --------------------------------------------------------
    # HISTORICAL CANDLES
    # --------------------------------------------------------

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_candles(self, symbol: str, interval: str = "5m", days: int = 5, from_dt=None, to_dt=None) -> Optional[pd.DataFrame]:
        if not self._api: self._init_api()
        if not self._api: return None

        now_ist = get_current_ist_time()
        to_dt = to_dt or now_ist
        from_dt = from_dt or (to_dt - timedelta(days=days))

        try:
            # FIX: Mandatory exchange and segment added
            raw_candles = self._api.get_historical_data(
                symbol=symbol, exchange="NSE", segment="CASH",
                interval=INTERVAL_MAP.get(interval, "5minute"),
                from_timestamp=int(from_dt.timestamp() * 1000),
                to_timestamp=int(to_dt.timestamp() * 1000),
            )
            return self._parse_candles(raw_candles, symbol, interval)
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] get_candles({symbol}) failed: {e}")
            raise

    def _parse_candles(self, raw: list, symbol: str, interval: str) -> Optional[pd.DataFrame]:
        if not raw: return None
        records = []
        for candle in raw:
            try:
                if isinstance(candle, (list, tuple)):
                    ts, o, h, l, c, v = candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]
                elif isinstance(candle, dict):
                    ts = candle.get("timestamp") or candle.get("time")
                    o, h, l, c, v = candle.get("open"), candle.get("high"), candle.get("low"), candle.get("close"), candle.get("volume", 0)
                else: continue

                dt = datetime.fromtimestamp(ts/1000 if ts > 1e12 else ts, tz=UTC).astimezone(IST)
                records.append({"datetime": dt, "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": int(v)})
            except: continue
        
        if not records: return None
        df = pd.DataFrame(records).sort_values("datetime").drop_duplicates("datetime").set_index("datetime")
        df.index = pd.DatetimeIndex(df.index)
        return df

    # --------------------------------------------------------
    # PREVIOUS ANALYSIS DATA (MTF, ORB, TODAY)
    # --------------------------------------------------------

    def get_multi_timeframe_data(self, symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
        data = {}
        for interval, days in [("5m", 5), ("15m", 10), ("1h", 30)]:
            try:
                data[interval] = self.get_candles(symbol, interval=interval, days=days)
            except: data[interval] = None
        return data

    def get_today_candles(self, symbol: str, interval: str = "5m") -> Optional[pd.DataFrame]:
        return self.get_candles(symbol, interval=interval, from_dt=get_market_open_datetime_ist(), to_dt=get_current_ist_time())

    def get_nifty_quote(self) -> Optional[Dict]:
        for sym in ["NIFTY 50", "NIFTY", "NSE_NIFTY"]:
            try:
                q = self.get_quote(sym)
                if q: return q
            except: continue
        return None

    def get_nifty_candles(self, interval: str = "5m", days: int = 5) -> Optional[pd.DataFrame]:
        for sym in ["NIFTY 50", "NIFTY"]:
            try:
                df = self.get_candles(sym, interval=interval, days=days)
                if df is not None: return df
            except: continue
        return None

    # --------------------------------------------------------
    # ACCOUNT & POSITIONS (FIXED Attribute Error)
    # --------------------------------------------------------

    def get_account_balance(self) -> Dict:
        if not self._api: return {"available": 0, "used": 0, "total": 0}
        try:
            # Check for available methods in your specific SDK version
            if hasattr(self._api, 'get_funds'): res = self._api.get_funds()
            elif hasattr(self._api, 'get_balance'): res = self._api.get_balance()
            else: return {"available": 0, "used": 0, "total": 0}
            
            avail = float(res.get("available_cash") or res.get("available_margin") or 0)
            return {"available": avail, "used": float(res.get("used_margin", 0)), "total": avail, "timestamp": format_ist_timestamp()}
        except: return {"available": 0, "used": 0, "total": 0}

    def get_positions(self) -> List[Dict]:
        if not self._api: return []
        try:
            raw = self._api.get_positions()
            return [{
                "symbol": p.get("symbol") or p.get("trading_symbol"),
                "quantity": int(p.get("quantity", 0)),
                "avg_price": float(p.get("average_price", 0)),
                "ltp": float(p.get("ltp", 0)),
                "pnl": float(p.get("pnl", 0)),
                "product": "MIS", # Leverage
                "direction": "BUY" if int(p.get("quantity", 0)) > 0 else "SELL"
            } for p in (raw or []) if int(p.get("quantity", 0)) != 0]
        except: return []

    def get_opening_range(self, symbol: str) -> Optional[Dict]:
        market_open = get_market_open_datetime_ist()
        df = self.get_candles(symbol, interval="5m", from_dt=market_open, to_dt=market_open + timedelta(minutes=20))
        if df is None or df.empty: return None
        orb_candles = df[(df.index.time >= pd.Timestamp("09:15").time()) & (df.index.time <= pd.Timestamp("09:30").time())]
        if orb_candles.empty: return None
        h, l = orb_candles["high"].max(), orb_candles["low"].min()
        return {"symbol": symbol, "orb_high": h, "orb_low": l, "orb_mid": (h + l) / 2}

_fetcher = None
def get_data_fetcher() -> GrowwDataFetcher:
    global _fetcher
    if _fetcher is None: _fetcher = GrowwDataFetcher()
    return _fetcher
