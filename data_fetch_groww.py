"""
data_fetch_groww.py — NSE Momentum Groww AI Bot
Fixed: Missing positional arguments & AttributeError
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
from auth_groww import get_groww_token, get_auth_manager

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
    def __init__(self):
        self._api = None
        self._cache: Dict[str, Tuple[pd.DataFrame, datetime]] = {}
        self._cache_ttl_seconds = 30 
        self._init_api()

    def _init_api(self):
        try:
            from growwapi import GrowwAPI
            token = get_groww_token()
            if token:
                self._api = GrowwAPI(token)
                logger.info(f"[{format_ist_timestamp()}] GrowwAPI initialized")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Init failed: {e}")

    def _reinit_with_fresh_token(self) -> bool:
        """Force a fresh TOTP access_token and rebuild the API client."""
        logger.warning(f"[{format_ist_timestamp()}] Auth error — forcing token refresh...")
        try:
            manager = get_auth_manager()
            # force_refresh() always calls the SDK (ignores cache age)
            ok = manager.force_refresh()
            new_token = manager.token
            if ok and new_token:
                from growwapi import GrowwAPI
                self._api = GrowwAPI(new_token)
                logger.info(f"[{format_ist_timestamp()}] ✅ API client refreshed with new access_token")
                return True
            logger.error(
                f"[{format_ist_timestamp()}] ❌ Token refresh failed — "
                "check GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET in Render env vars"
            )
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Token refresh error: {e}")
        return False

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_quote(self, symbol: str) -> Optional[Dict]:
        if not self._api: self._init_api()
        if not self._api: return None

        try:
            raw = self._api.get_quote(trading_symbol=symbol, exchange="NSE", segment="CASH")
            if not raw: return None

            return {
                "symbol": symbol,
                "ltp": float(raw.get("ltp") or 0),
                "open": float(raw.get("open") or 0),
                "high": float(raw.get("high") or 0),
                "low": float(raw.get("low") or 0),
                "close": float(raw.get("close") or 0),
                "volume": int(raw.get("volume") or 0),
                "change_pct": float(raw.get("change_percent") or 0),
                "product": "MIS",
                "timestamp": format_ist_timestamp(),
            }
        except Exception as e:
            err = str(e).lower()
            if "authentication" in err or "expired" in err or "invalid" in err or "401" in err or "403" in err:
                logger.warning(f"[{format_ist_timestamp()}] Auth error on get_quote — refreshing token...")
                if self._reinit_with_fresh_token():
                    raise  # Retry with new token via @retry_with_backoff
                return None
            logger.error(f"get_quote({symbol}) failed: {e}")
            raise

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_candles(self, symbol: str, interval: str = "5m", days: int = 5, from_dt=None, to_dt=None) -> Optional[pd.DataFrame]:
        if not self._api: return None
        now_ist = get_current_ist_time()
        to_dt = to_dt or now_ist
        from_dt = from_dt or (to_dt - timedelta(days=days))

        try:
            # FIX: Added required 'exchange' and 'segment'
            raw_candles = self._api.get_historical_data(
                symbol=symbol, exchange="NSE", segment="CASH",
                interval=INTERVAL_MAP.get(interval, "5minute"),
                from_timestamp=int(from_dt.timestamp() * 1000),
                to_timestamp=int(to_dt.timestamp() * 1000),
            )
            return self._parse_candles(raw_candles, symbol, interval)
        except Exception as e:
            logger.error(f"get_candles({symbol}) failed: {e}")
            raise

    def _parse_candles(self, raw, symbol, interval):
        if not raw: return None
        records = []
        for c in raw:
            try:
                dt = datetime.fromtimestamp(c[0]/1000, tz=UTC).astimezone(IST)
                records.append({
                    "datetime": dt, "open": float(c[1]), "high": float(c[2]), 
                    "low": float(c[3]), "close": float(c[4]), "volume": int(c[5])
                })
            except: continue
        df = pd.DataFrame(records).sort_values("datetime").set_index("datetime")
        return df if not df.empty else None

    # --------------------------------------------------------
    # ACCOUNT & LEVERAGE (FIXED Attribute Error)
    # --------------------------------------------------------

    def _refresh_api_if_needed(self):
        """
        Re-initialize the GrowwAPI client with the latest token.
        Called by main.py after a TOTP token refresh at 8:45 AM IST.
        """
        self._init_api()

    # --------------------------------------------------------

    def get_account_balance(self) -> Dict:
        """
        Fetch live account balance from Groww.
        Returns full breakdown: available, used margin, collateral, total.
        """
        if not self._api:
            return {"available": 0, "used_margin": 0, "total": 0, "collateral": 0}
        try:
            if hasattr(self._api, 'get_balance'):
                res = self._api.get_balance()
            else:
                return {"available": 0, "used_margin": 0, "total": 0, "collateral": 0}

            # Groww SDK may return different field names — handle all variants
            available = float(
                res.get("available_cash") or
                res.get("available_margin") or
                res.get("available") or 0
            )
            used_margin = float(
                res.get("used_margin") or
                res.get("utilised_margin") or
                res.get("margin_used") or 0
            )
            collateral = float(
                res.get("collateral") or
                res.get("collateral_margin") or 0
            )
            total = float(
                res.get("net") or
                res.get("net_value") or
                res.get("total") or
                res.get("opening_balance") or
                (available + used_margin)
            )
            opening = float(
                res.get("opening_balance") or
                res.get("start_of_day_limit") or
                total
            )

            return {
                "available":    available,
                "used_margin":  used_margin,
                "collateral":   collateral,
                "total":        total,
                "opening":      opening,
                "_raw":         res,   # full response for debugging
            }
        except Exception as e:
            logger.error(f"Balance check failed: {e}")
            return {"available": 0, "used_margin": 0, "total": 0, "collateral": 0}

    def get_positions(self) -> List[Dict]:
        """Fetch MIS leverage positions."""
        if not self._api: return []
        try:
            raw = self._api.get_positions()
            return [{
                "symbol": p.get("symbol"),
                "quantity": int(p.get("quantity", 0)),
                "product": "MIS", # Ensures leverage is tracked correctly
                "avg_price": float(p.get("average_price", 0))
            } for p in (raw or []) if int(p.get("quantity", 0)) != 0]
        except: return []

    # --------------------------------------------------------
    # PREVIOUS FEATURES: MTF, ORB, TODAY
    # --------------------------------------------------------

    def get_multi_timeframe_data(self, symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
        data = {}
        for interval, days in [("5m", 5), ("15m", 10), ("1h", 30)]:
            try: data[interval] = self.get_candles(symbol, interval=interval, days=days)
            except: data[interval] = None
        return data

    def get_today_candles(self, symbol: str, interval: str = "5m") -> Optional[pd.DataFrame]:
        return self.get_candles(symbol, interval=interval, from_dt=get_market_open_datetime_ist())

    def get_nifty_quote(self) -> Optional[Dict]:
        # 'NIFTY 50' often requires 'INDEX' segment, but 'NSE'/'CASH' is safer to try first
        for sym in ["NIFTY 50", "NIFTY", "NSE_NIFTY"]:
            try:
                q = self.get_quote(sym)
                if q: return q
            except: continue
        return None

_fetcher = None
def get_data_fetcher() -> GrowwDataFetcher:
    global _fetcher
    if _fetcher is None: _fetcher = GrowwDataFetcher()
    return _fetcher
