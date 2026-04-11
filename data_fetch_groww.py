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
        self._balance_cache: Dict = {}        # last successful balance response
        self._balance_cache_time: Optional[datetime] = None
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

        Behaviour:
        - Tries get_balance() / get_funds() / get_margins() in order
        - Handles dict, object, and nested envelope (data/payload/result) responses
        - On empty/None response: retries once with a fresh token
        - If still empty (Groww API offline or market closed): returns last cached
          balance with a '_from_cache' + '_cache_age_min' flag for the caller to display
        - Every successful fetch is stored in self._balance_cache
        """
        _empty = {
            "available": 0, "used_margin": 0, "total": 0,
            "collateral": 0, "opening": 0,
            "_from_cache": False, "_cache_age_min": 0,
        }

        def _call_api() -> Optional[Dict]:
            """Call Groww balance endpoint, return raw dict or None."""
            if not self._api:
                return None
            for method_name in ("get_balance", "get_funds", "get_margins"):
                if hasattr(self._api, method_name):
                    try:
                        raw = getattr(self._api, method_name)()
                        logger.info(f"[balance_raw:{method_name}] {raw}")
                        return raw
                    except Exception as e:
                        logger.debug(f"balance {method_name} error: {e}")
            return None

        def _parse(res) -> Optional[Dict]:
            """Parse raw response → normalised balance dict. Returns None if empty."""
            if res is None:
                return None
            # Convert SDK object → dict
            if not isinstance(res, dict):
                try:
                    res = vars(res)
                except TypeError:
                    try:
                        res = dict(res)
                    except Exception:
                        return None
            if not res:
                return None

            # Unwrap envelope keys
            for key in ("data", "payload", "result", "response"):
                if key in res and isinstance(res[key], dict) and res[key]:
                    res = res[key]
                    break

            logger.info(f"[balance_fields] {list(res.keys())}")

            def _get(*keys) -> float:
                for k in keys:
                    v = res.get(k)
                    if v is not None:
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            pass
                return 0.0

            available = _get(
                "available_cash", "available_margin", "available",
                "available_amount", "available_limit", "cash",
                "net_available", "free_cash", "liquid_cash",
                "trading_power",
            )
            used_margin = _get(
                "used_margin", "utilised_margin", "margin_used",
                "used_amount", "blocked_amount", "used",
            )
            collateral = _get(
                "collateral", "collateral_margin", "collateral_amount",
            )
            total = _get(
                "net", "net_value", "total", "net_amount",
                "opening_balance", "total_balance",
            ) or (available + used_margin)
            opening = _get(
                "opening_balance", "start_of_day_limit", "sod_balance",
            ) or total

            # Treat as empty if everything is 0 (API returned zeroes, not real data)
            if available == 0 and used_margin == 0 and total == 0:
                return None

            return {
                "available":   available,
                "used_margin": used_margin,
                "collateral":  collateral,
                "total":       total,
                "opening":     opening,
                "_raw":        res,
                "_from_cache": False,
                "_cache_age_min": 0,
            }

        # ── Attempt 1: use current API instance ───────────────────────────
        result = _parse(_call_api())

        # ── Attempt 2: reinit with fresh token and retry once ─────────────
        if result is None:
            logger.info("Balance empty — reinitialising API with fresh token and retrying...")
            try:
                self._reinit_with_fresh_token()
                result = _parse(_call_api())
            except Exception as e:
                logger.debug(f"Balance retry error: {e}")

        # ── Success: update cache ─────────────────────────────────────────
        if result is not None:
            self._balance_cache = result.copy()
            self._balance_cache_time = get_current_ist_time()
            return result

        # ── Fallback: return last cached balance ──────────────────────────
        if self._balance_cache:
            age_min = 0
            if self._balance_cache_time:
                age_min = (get_current_ist_time() - self._balance_cache_time).total_seconds() / 60
            cached = self._balance_cache.copy()
            cached["_from_cache"]     = True
            cached["_cache_age_min"]  = round(age_min, 0)
            logger.info(f"Returning cached balance (age: {age_min:.0f} min)")
            return cached

        # ── Nothing available ─────────────────────────────────────────────
        logger.warning("Groww balance API returned empty — market may be closed")
        return _empty

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
