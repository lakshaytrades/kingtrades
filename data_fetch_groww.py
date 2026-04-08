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
        self._cache_ttl_seconds = 30  # Cache quotes for 30s
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
            logger.error(f"[{format_ist_timestamp()}] growwapi not installed. Run: pip install growwapi")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] GrowwAPI init failed: {e}")

    def _refresh_api_if_needed(self):
        """Re-initialize API if token was refreshed."""
        try:
            from growwapi import GrowwAPI
            new_token = get_groww_token()
            if new_token:
                self._api = GrowwAPI(new_token)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] API refresh failed: {e}")

    # --------------------------------------------------------
    # REAL-TIME QUOTES
    # --------------------------------------------------------

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_quote(self, symbol: str) -> Optional[Dict]:
        """
        Get real-time quote for a symbol.
        """
        if not self._api:
            self._init_api()
            if not self._api:
                return None

        cache_key = f"quote_{symbol}"
        if cache_key in self._cache:
            cached_df, cached_time = self._cache[cache_key]
            age = (get_current_ist_time() - cached_time).total_seconds()
            if age < self._cache_ttl_seconds:
                return cached_df

        try:
            # EDIT 1: Added mandatory exchange and segment parameters
            raw = self._api.get_quote(trading_symbol=symbol, exchange="NSE", segment="CASH")
            if not raw:
                return None

            quote = {
                "symbol": symbol,
                "ltp": float(raw.get("ltp") or raw.get("last_price") or 0),
                "open": float(raw.get("open") or 0),
                "high": float(raw.get("high") or 0),
                "low": float(raw.get("low") or 0),
                "close": float(raw.get("close") or raw.get("prev_close") or 0),
                "volume": int(raw.get("volume") or 0),
                "change": float(raw.get("change") or 0),
                "change_pct": float(raw.get("change_percent") or raw.get("pct_change") or 0),
                "bid": float(raw.get("bid") or 0),
                "ask": float(raw.get("ask") or 0),
                "timestamp": format_ist_timestamp(),
                "timestamp_dt": get_current_ist_time(),
            }

            self._cache[cache_key] = (quote, get_current_ist_time())
            logger.debug(f"[{format_ist_timestamp()}] Quote {symbol}: ₹{quote['ltp']}")
            return quote

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] get_quote({symbol}) failed: {e}")
            raise 

    def get_ltp(self, symbol: str) -> Optional[float]:
        """Get last traded price for a symbol."""
        quote = self.get_quote(symbol)
        return quote["ltp"] if quote else None

    def get_multiple_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Fetch quotes for multiple symbols. Returns {symbol: quote_dict}."""
        results = {}
        for symbol in symbols:
            try:
                quote = self.get_quote(symbol)
                if quote:
                    results[symbol] = quote
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] Quote failed for {symbol}: {e}")
        return results

    # --------------------------------------------------------
    # HISTORICAL CANDLES
    # --------------------------------------------------------

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_candles(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 5,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles from Groww.
        """
        if not self._api:
            self._init_api()
            if not self._api:
                return None

        now_ist = get_current_ist_time()

        if to_dt is None:
            to_dt = now_ist
        if from_dt is None:
            from_dt = to_dt - timedelta(days=days)

        from_ts = int(from_dt.timestamp() * 1000)
        to_ts = int(to_dt.timestamp() * 1000)

        groww_interval = INTERVAL_MAP.get(interval, "5minute")

        try:
            # EDIT 2: Added mandatory exchange and segment for historical data
            raw_candles = self._api.get_historical_data(
                symbol=symbol,
                exchange="NSE",
                segment="CASH",
                interval=groww_interval,
                from_timestamp=from_ts,
                to_timestamp=to_ts,
            )

            if not raw_candles:
                logger.warning(f"[{format_ist_timestamp()}] No candles returned for {symbol} {interval}")
                return None

            df = self._parse_candles(raw_candles, symbol, interval)
            if df is not None and not df.empty:
                logger.info(
                    f"[{format_ist_timestamp()}] Fetched {len(df)} {interval} candles "
                    f"for {symbol} | Last: {df.index[-1].strftime('%H:%M IST')}"
                )
            return df

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] get_candles({symbol}, {interval}) failed: {e}")
            raise

    def _parse_candles(self, raw: list, symbol: str, interval: str) -> Optional[pd.DataFrame]:
        """Parse raw Groww candle data into clean DataFrame with IST index."""
        if not raw:
            return None

        records = []
        for candle in raw:
            try:
                if isinstance(candle, (list, tuple)):
                    ts, o, h, l, c, v = candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]
                elif isinstance(candle, dict):
                    ts = candle.get("timestamp") or candle.get("time") or candle.get("date")
                    o = candle.get("open")
                    h = candle.get("high")
                    l = candle.get("low")
                    c = candle.get("close")
                    v = candle.get("volume", 0)
                else:
                    continue

                if isinstance(ts, (int, float)):
                    if ts > 1e12: ts = ts / 1000
                    dt = datetime.fromtimestamp(ts, tz=UTC)
                elif isinstance(ts, str):
                    dt = pd.to_datetime(ts)
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=UTC)
                else:
                    continue

                dt_ist = dt.astimezone(IST)
                records.append({
                    "datetime": dt_ist,
                    "open": float(o), "high": float(h), "low": float(l),
                    "close": float(c), "volume": int(v),
                })
            except Exception as e:
                logger.debug(f"Candle parse error: {e}")
                continue

        if not records: return None

        df = pd.DataFrame(records)
        df = df.sort_values("datetime").drop_duplicates("datetime")
        df = df.set_index("datetime")
        df.index = pd.DatetimeIndex(df.index)
        df.attrs["symbol"] = symbol
        df.attrs["interval"] = interval
        return df

    def get_multi_timeframe_data(self, symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
        data = {}
        configs = [("5m", 5), ("15m", 10), ("1h", 30)]
        for interval, days in configs:
            try:
                df = self.get_candles(symbol, interval=interval, days=days)
                data[interval] = df
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] MTF fetch failed for {symbol}: {e}")
                data[interval] = None
        return data

    def get_today_candles(self, symbol: str, interval: str = "5m") -> Optional[pd.DataFrame]:
        market_open = get_market_open_datetime_ist()
        now_ist = get_current_ist_time()
        return self.get_candles(symbol, interval=interval, from_dt=market_open, to_dt=now_ist)

    def get_nifty_quote(self) -> Optional[Dict]:
        nifty_symbols = ["NIFTY 50", "NIFTY50"]
        for sym in nifty_symbols:
            try:
                quote = self.get_quote(sym)
                if quote and quote.get("ltp"):
                    return quote
            except Exception:
                continue
        return None

    def get_nifty_candles(self, interval: str = "5m", days: int = 5) -> Optional[pd.DataFrame]:
        for sym in ["NIFTY 50", "NIFTY50"]:
            try:
                df = self.get_candles(sym, interval=interval, days=days)
                if df is not None and not df.empty: return df
            except Exception:
                continue
        return None

    @retry_with_backoff(max_retries=3, delays=[2, 4, 8])
    def get_account_balance(self) -> Dict:
        if not self._api: return {"available": 0, "used": 0, "total": 0}
        try:
            funds = self._api.get_funds()
            if isinstance(funds, dict):
                available = float(funds.get("available_cash") or funds.get("available_margin") or 0)
                used = float(funds.get("used_margin") or 0)
                return {"available": available, "used": used, "total": available + used, "timestamp": format_ist_timestamp()}
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] balance failed: {e}")
        return {"available": 0, "used": 0, "total": 0}

    @retry_with_backoff(max_retries=3, delays=[2, 4, 8])
    def get_positions(self) -> List[Dict]:
        """Fetch current open intraday positions (Leverage/MIS)."""
        if not self._api: return []
        try:
            raw_positions = self._api.get_positions()
            positions = []
            for pos in (raw_positions or []):
                if isinstance(pos, dict):
                    qty = int(pos.get("quantity") or 0)
                    if qty == 0: continue
                    positions.append({
                        "symbol": pos.get("symbol") or pos.get("trading_symbol", ""),
                        "quantity": qty,
                        "avg_price": float(pos.get("average_price") or 0),
                        "ltp": float(pos.get("ltp") or 0),
                        "product": pos.get("product", "MIS"), # LEVERAGE: Ensures it shows as MIS
                        "direction": "BUY" if qty > 0 else "SELL",
                    })
            return positions
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] positions failed: {e}")
            return []

    @retry_with_backoff(max_retries=3, delays=[2, 4, 8])
    def get_orders(self) -> List[Dict]:
        if not self._api: return []
        try:
            raw_orders = self._api.get_orders()
            orders = []
            for order in (raw_orders or []):
                if isinstance(order, dict):
                    orders.append({
                        "order_id": order.get("order_id", ""),
                        "symbol": order.get("symbol", ""),
                        "quantity": int(order.get("quantity") or 0),
                        "status": order.get("status", ""),
                        "product": order.get("product", "MIS"), # LEVERAGE
                    })
            return orders
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] orders failed: {e}")
            return []

    def get_opening_range(self, symbol: str) -> Optional[Dict]:
        now_ist = get_current_ist_time()
        market_open = get_market_open_datetime_ist()
        orb_end = market_open.replace(hour=9, minute=30)
        df = self.get_candles(symbol, interval="5m", from_dt=market_open, to_dt=min(now_ist, orb_end + timedelta(minutes=5)))
        if df is None or df.empty: return None
        orb_candles = df[(df.index.time >= pd.Timestamp("09:15").time()) & (df.index.time <= pd.Timestamp("09:30").time())]
        if orb_candles.empty: return None
        orb_high, orb_low = orb_candles["high"].max(), orb_candles["low"].min()
        return {"symbol": symbol, "orb_high": orb_high, "orb_low": orb_low, "orb_mid": (orb_high + orb_low) / 2}

_fetcher: Optional[GrowwDataFetcher] = None

def get_data_fetcher() -> GrowwDataFetcher:
    global _fetcher
    if _fetcher is None: _fetcher = GrowwDataFetcher()
    return _fetcher
