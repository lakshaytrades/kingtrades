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
            if "authentication" in err or "expired" in err or "401" in err or "403" in err:
                logger.warning(f"[{format_ist_timestamp()}] Auth error on get_quote — refreshing token...")
                if self._reinit_with_fresh_token():
                    raise  # Retry with new token via @retry_with_backoff
                return None
            # 400 Bad Request = wrong symbol/segment — no point retrying
            if "bad request" in err or "400" in err or "invalid symbol" in err or "not found" in err:
                logger.debug(f"get_quote({symbol}): bad request — unsupported symbol, skipping")
                return None  # Return None (not raise) so retry decorator is NOT triggered
            logger.error(f"get_quote({symbol}) failed: {e}")
            raise

    @retry_with_backoff(max_retries=4, delays=[2, 4, 8, 16])
    def get_candles(self, symbol: str, interval: str = "5m", days: int = 5, from_dt=None, to_dt=None) -> Optional[pd.DataFrame]:
        if not self._api: return None
        now_ist = get_current_ist_time()
        to_dt = to_dt or now_ist
        from_dt = from_dt or (to_dt - timedelta(days=days))

        from_ms = int(from_dt.timestamp() * 1000)
        to_ms   = int(to_dt.timestamp() * 1000)
        interval_str = INTERVAL_MAP.get(interval, "5minute")

        raw = self._call_candles_sdk(symbol, interval_str, from_ms, to_ms)
        if raw is None:
            raw = self._fetch_candles_http(symbol, interval_str, from_ms, to_ms)
        if raw is None:
            raw = self._fetch_candles_yfinance(symbol, interval_str, from_ms, to_ms)
        if raw is None:
            raise RuntimeError(f"All candle sources failed for {symbol} — check Groww API access")
        return self._parse_candles(raw, symbol, interval)

    def _call_candles_sdk(self, symbol: str, interval_str: str, from_ms: int, to_ms: int):
        """
        Try every known GrowwAPI method name for historical OHLCV data.
        Different SDK versions ship different method names — we try them all.
        Returns raw list-of-lists or None.
        """
        if not self._api:
            return None

        # Candidate method names across SDK versions
        candidates = [
            "get_historical_data",
            "get_historical_candle_data",
            "get_candle_data",
            "get_ohlcv",
            "historical_data",
            "candles",
        ]
        kwargs_sets = [
            # v1 SDK style
            dict(symbol=symbol, exchange="NSE", segment="CASH",
                 interval=interval_str, from_timestamp=from_ms, to_timestamp=to_ms),
            # v2 SDK style (different key names)
            dict(trading_symbol=symbol, exchange="NSE", segment="CASH",
                 interval=interval_str, from_timestamp=from_ms, to_timestamp=to_ms),
            # Positional fallback
            None,
        ]

        for method_name in candidates:
            fn = getattr(self._api, method_name, None)
            if fn is None:
                continue
            for kwargs in kwargs_sets:
                try:
                    if kwargs is None:
                        raw = fn(symbol, "NSE", "CASH", interval_str, from_ms, to_ms)
                    else:
                        raw = fn(**kwargs)
                    if raw:
                        logger.debug(f"Candle SDK hit: {method_name}")
                        return raw
                except TypeError:
                    continue  # wrong kwargs — try next set
                except Exception as e:
                    logger.debug(f"SDK candles {method_name}: {e}")
                    break  # method exists but failed — don't try other kwarg sets

        logger.debug(f"No SDK candle method worked for {symbol} — trying HTTP")
        return None

    def _fetch_candles_http(self, symbol: str, interval_str: str, from_ms: int, to_ms: int):
        """
        Direct REST fallback when SDK has no historical-data method.
        Tries known Groww API endpoint patterns.
        Returns raw list-of-lists or None.
        """
        import requests as req
        token = get_groww_token()
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "X-Api-Version": "1",
        }

        # Known Groww historical-data endpoint patterns
        endpoints = [
            ("GET", "https://api.groww.in/v1/historical-data", {
                "trading_symbol": symbol, "exchange": "NSE", "segment": "CASH",
                "interval": interval_str, "from": from_ms, "to": to_ms,
            }),
            ("GET", "https://api.groww.in/v2/historical-data", {
                "trading_symbol": symbol, "exchange": "NSE", "segment": "CASH",
                "interval": interval_str, "from_timestamp": from_ms, "to_timestamp": to_ms,
            }),
            ("GET", "https://api.groww.in/v1/charting_service/chart/historical", {
                "exchange": "NSE", "tradingsymbol": symbol,
                "timeperiod": interval_str, "starttime": from_ms, "endtime": to_ms,
            }),
        ]

        for method, url, params in endpoints:
            try:
                resp = req.request(method, url, params=params, headers=headers, timeout=20)
                if resp.status_code == 200:
                    body = resp.json()
                    # Handle envelope formats
                    candles = (body.get("candles") or body.get("data") or
                               body.get("ohlcv") or body.get("result") or body)
                    if isinstance(candles, list) and len(candles) > 0:
                        logger.debug(f"Candle HTTP hit: {url.split('/')[-1]} for {symbol}")
                        return candles
                elif resp.status_code == 400:
                    logger.debug(f"HTTP candles 400 from {url.split('/')[-1]} — wrong params")
                    continue
                elif resp.status_code in (401, 403):
                    logger.warning(f"HTTP candles auth error — refreshing token")
                    self._reinit_with_fresh_token()
                    break
            except Exception as e:
                logger.debug(f"HTTP candles {url.split('/')[-1]}: {e}")

        logger.debug(f"get_candles({symbol}): all Groww HTTP endpoints failed — trying yfinance")
        return None

    def _fetch_candles_yfinance(self, symbol: str, interval_str: str, from_ms: int, to_ms: int):
        """
        yfinance fallback for NSE historical OHLCV.
        NSE symbols need .NS suffix (e.g. RELIANCE → RELIANCE.NS).
        yf interval map: 1minute→1m, 5minute→5m, 15minute→15m, 30minute→30m, 60minute→60m, 1day→1d
        """
        try:
            import yfinance as yf
            from datetime import datetime as _dt

            yf_sym = f"{symbol}.NS"
            yf_interval_map = {
                "1minute": "1m", "5minute": "5m", "15minute": "15m",
                "30minute": "30m", "60minute": "60m", "1day": "1d",
            }
            yf_interval = yf_interval_map.get(interval_str, "5m")

            start_dt = _dt.fromtimestamp(from_ms / 1000, tz=IST)
            end_dt   = _dt.fromtimestamp(to_ms   / 1000, tz=IST)

            df = yf.download(
                yf_sym, start=start_dt, end=end_dt,
                interval=yf_interval, progress=False, auto_adjust=True,
            )
            if df is None or df.empty:
                logger.debug(f"yfinance: no data for {yf_sym}")
                return None

            # Flatten MultiIndex columns (newer yfinance returns (field, ticker) tuples)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Convert to standard list-of-lists [ts_ms, o, h, l, c, vol]
            result = []
            for ts, row in df.iterrows():
                try:
                    ts_ms = int(ts.timestamp() * 1000)
                    result.append([
                        ts_ms,
                        float(row["Open"].iloc[0])   if hasattr(row["Open"],   "iloc") else float(row["Open"]),
                        float(row["High"].iloc[0])   if hasattr(row["High"],   "iloc") else float(row["High"]),
                        float(row["Low"].iloc[0])    if hasattr(row["Low"],    "iloc") else float(row["Low"]),
                        float(row["Close"].iloc[0])  if hasattr(row["Close"],  "iloc") else float(row["Close"]),
                        int(  row["Volume"].iloc[0]) if hasattr(row["Volume"], "iloc") else int(row["Volume"]),
                    ])
                except Exception:
                    continue
            if result:
                logger.info(f"[{format_ist_timestamp()}] yfinance candles: {symbol} "
                            f"{len(result)} bars ({yf_interval})")
            return result or None

        except ImportError:
            logger.debug("yfinance not installed — skipping")
            return None
        except Exception as e:
            logger.debug(f"yfinance candles {symbol}: {e}")
            return None

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
        """Fetch Nifty 50 index quote. Tries INDEX segment first, falls back to CASH."""
        if not self._api:
            return None
        # Try INDEX segment (correct for index symbols)
        for sym in ["NIFTY 50", "NIFTY"]:
            try:
                raw = self._api.get_quote(trading_symbol=sym, exchange="NSE", segment="INDEX")
                if raw and raw.get("ltp"):
                    return {
                        "symbol": sym,
                        "ltp": float(raw.get("ltp") or 0),
                        "open": float(raw.get("open") or 0),
                        "high": float(raw.get("high") or 0),
                        "low": float(raw.get("low") or 0),
                        "close": float(raw.get("close") or 0),
                        "volume": int(raw.get("volume") or 0),
                        "change_pct": float(raw.get("change_percent") or 0),
                        "timestamp": format_ist_timestamp(),
                    }
            except Exception as e:
                logger.debug(f"Nifty quote ({sym}/INDEX) failed: {e}")
        # Fallback: try CASH segment (some SDK versions use this)
        for sym in ["NIFTY 50", "NIFTY"]:
            try:
                q = self.get_quote(sym)
                if q:
                    return q
            except Exception:
                continue
        return None

_fetcher = None
def get_data_fetcher() -> GrowwDataFetcher:
    global _fetcher
    if _fetcher is None: _fetcher = GrowwDataFetcher()
    return _fetcher
