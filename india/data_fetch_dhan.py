"""
data_fetch_dhan.py — Market data for Indian NSE market
Primary OHLCV:   Dhan intraday candle API (replaces yfinance — Yahoo 403 blocked)
Real-time LTP:   Dhan API (for order placement & live P&L)
Scrip master:    Dhan CSV → symbol → security_id mapping
"""
import io
import logging
import os
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger("data_fetch_dhan")
IST = ZoneInfo("Asia/Kolkata")

# Suppress yfinance's own verbose ERROR/WARNING spam (404s etc handled in get_ohlcv)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

_SCRIP_MASTER_URL  = "https://images.dhan.co/api-data/api-scrip-master.csv"
_SCRIP_CACHE_PATH  = Path(__file__).parent.parent / "data" / "dhan_scrip_master.csv"
_SCRIP_CACHE_TTL   = 86400  # refresh daily

_quote_cache: Dict[str, dict] = {}
_quote_cache_ts: Dict[str, float] = {}
_QUOTE_CACHE_TTL = 30.0   # 30-second quote cache

_symbol_map: Dict[str, str] = {}   # symbol → security_id
_symbol_map_loaded = False

# Module-level Dhan client — set via set_dhan_client() at bot startup
_dhan_client_ref = None

def set_dhan_client(client) -> None:
    """Register the live Dhan client so OHLCV/VIX functions can use it."""
    global _dhan_client_ref
    _dhan_client_ref = client


# ── Scrip master (symbol → security_id) ──────────────────────────────────────

def _load_scrip_master() -> Dict[str, str]:
    global _symbol_map, _symbol_map_loaded
    if _symbol_map_loaded:
        return _symbol_map

    # Try local cache first
    if _SCRIP_CACHE_PATH.exists():
        age = _time.time() - _SCRIP_CACHE_PATH.stat().st_mtime
        if age < _SCRIP_CACHE_TTL:
            try:
                df = pd.read_csv(_SCRIP_CACHE_PATH, low_memory=False)
                _symbol_map = _parse_scrip_df(df)
                _symbol_map_loaded = True
                logger.info(f"Scrip master loaded from cache: {len(_symbol_map)} symbols")
                return _symbol_map
            except Exception as e:
                logger.debug(f"Scrip cache read failed: {e}")

    # Download fresh
    try:
        import requests
        resp = requests.get(_SCRIP_MASTER_URL, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), low_memory=False)
        _SCRIP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(_SCRIP_CACHE_PATH, index=False)
        _symbol_map = _parse_scrip_df(df)
        _symbol_map_loaded = True
        logger.info(f"Scrip master downloaded: {len(_symbol_map)} NSE symbols")
    except Exception as e:
        logger.warning(f"Scrip master download failed: {e} — using fallback map")
        _symbol_map = _FALLBACK_MAP.copy()
        _symbol_map_loaded = True

    return _symbol_map


def _parse_scrip_df(df: pd.DataFrame) -> Dict[str, str]:
    """Parse Dhan scrip master CSV into {symbol: security_id} for NSE equity."""
    result: Dict[str, str] = {}
    try:
        # Column names vary slightly by version — try common patterns
        sym_col = next((c for c in df.columns if "TRADING_SYMBOL" in c.upper()), None)
        sid_col = next((c for c in df.columns if "SECURITY_ID" in c.upper() or "SMST_SECURITY" in c.upper()), None)
        seg_col = next((c for c in df.columns if "EXCH_ID" in c.upper() or "SEGMENT" in c.upper()), None)

        if not sym_col or not sid_col:
            logger.warning(f"Scrip master columns not recognised: {list(df.columns[:10])}")
            return _FALLBACK_MAP.copy()

        nse_df = df
        if seg_col:
            nse_df = df[df[seg_col].astype(str).str.contains("NSE", na=False)]

        for _, row in nse_df.iterrows():
            sym = str(row[sym_col]).strip().upper()
            sid = str(row[sid_col]).strip()
            if sym and sid and sid.isdigit():
                result[sym] = sid
    except Exception as e:
        logger.warning(f"Scrip parse error: {e}")
        return _FALLBACK_MAP.copy()
    return result


def get_security_id(symbol: str) -> Optional[str]:
    """Return Dhan security_id for NSE symbol, or None if not found."""
    mapping = _load_scrip_master()
    sid = mapping.get(symbol.upper()) or mapping.get(symbol.upper() + "-EQ")
    if not sid:
        logger.debug(f"security_id not found for {symbol}")
    return sid


# ── Real-time LTP from Dhan ──────────────────────────────────────────────────

def get_ltp(symbol: str, dhan_client) -> Optional[float]:
    """Fetch current last-traded price from Dhan API. Cached 30s."""
    now = _time.monotonic()
    if symbol in _quote_cache and now - _quote_cache_ts.get(symbol, 0) < _QUOTE_CACHE_TTL:
        return _quote_cache[symbol].get("ltp")

    if dhan_client is None:
        return None

    security_id = get_security_id(symbol)
    if not security_id:
        return None

    for attempt in range(4):
        try:
            resp = dhan_client.ohlc_data(
                securities={"NSE_EQ": [int(security_id)]}
            )
            if resp and resp.get("status") == "success":
                data = resp.get("data", {}).get("NSE_EQ", {})
                entry = data.get(security_id) or data.get(str(security_id))
                if entry:
                    ltp = float(entry.get("last_price", 0) or entry.get("ltp", 0))
                    _quote_cache[symbol] = {"ltp": ltp}
                    _quote_cache_ts[symbol] = now
                    return ltp if ltp > 0 else None
        except Exception as e:
            wait = 2 ** attempt
            logger.debug(f"LTP fetch {symbol} attempt {attempt+1} failed: {e}")
            _time.sleep(wait)
    return None


def get_multiple_ltp(symbols: List[str], dhan_client) -> Dict[str, float]:
    """Batch LTP fetch for up to 25 symbols. Returns {symbol: ltp}."""
    result: Dict[str, float] = {}
    if dhan_client is None:
        return result

    # Map symbols to security_ids
    id_to_sym: Dict[str, str] = {}
    sids: List[int] = []
    for sym in symbols:
        sid = get_security_id(sym)
        if sid:
            id_to_sym[sid] = sym
            sids.append(int(sid))

    if not sids:
        return result

    # Dhan batch is max 25 at a time
    for i in range(0, len(sids), 25):
        batch = sids[i:i+25]
        for attempt in range(4):
            try:
                resp = dhan_client.ohlc_data(securities={"NSE_EQ": batch})
                if resp and resp.get("status") == "success":
                    data = resp.get("data", {}).get("NSE_EQ", {})
                    for sid_str, entry in data.items():
                        sym = id_to_sym.get(sid_str)
                        if sym:
                            ltp = float(entry.get("last_price", 0) or entry.get("ltp", 0))
                            if ltp > 0:
                                result[sym] = ltp
                break
            except Exception as e:
                wait = 2 ** attempt
                logger.debug(f"Batch LTP attempt {attempt+1} failed: {e}")
                _time.sleep(wait)
    return result


# ── Historical OHLCV from Dhan intraday API ───────────────────────────────────
# Replaces yfinance — Yahoo Finance blocks VPS IPs with HTTP 403.
# Dhan provides 1m/5m/15m/25m/60m candles free for account holders.

_DHAN_INTERVAL_MAP = {
    "1m": "1", "5m": "5", "15m": "15", "25m": "25",
    "30m": "25", "60m": "60", "1h": "60",
}
# Dhan max window per interval (calendar days to cover 5 trading days safely)
_DHAN_LOOKBACK_DAYS = {"1": 3, "5": 7, "15": 7, "25": 7, "60": 10}

_ohlcv_cache: Dict[str, dict] = {}
_OHLCV_CACHE_TTL = 60.0   # 1-min candle cache


def _dhan_to_df(data: dict) -> Optional[pd.DataFrame]:
    """Convert Dhan intraday API response dict → normalised OHLCV DataFrame (IST index)."""
    try:
        ts   = data.get("timestamp") or data.get("start_Time") or []
        opens  = data.get("open",   [])
        highs  = data.get("high",   [])
        lows   = data.get("low",    [])
        closes = data.get("close",  [])
        vols   = data.get("volume", [])
        if not ts or not closes:
            return None
        # timestamps may be Unix seconds (int) or ISO strings
        if isinstance(ts[0], (int, float)):
            index = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
        else:
            index = pd.to_datetime(ts, utc=True).tz_convert("Asia/Kolkata")
        df = pd.DataFrame({
            "open":   [float(v) for v in opens],
            "high":   [float(v) for v in highs],
            "low":    [float(v) for v in lows],
            "close":  [float(v) for v in closes],
            "volume": [float(v) for v in vols],
        }, index=index)
        return df.dropna()
    except Exception as e:
        logger.debug(f"_dhan_to_df: {e}")
        return None


def get_ohlcv(symbol: str, interval: str = "5m", period: str = "5d") -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV candles from Dhan's intraday API.
    Falls back to NSE public JSON API if Dhan client is unavailable.
    Returns DataFrame: lowercase columns open/high/low/close/volume, IST-indexed.
    """
    cache_key = f"{symbol}_{interval}"
    now_mono  = _time.monotonic()
    cached    = _ohlcv_cache.get(cache_key)
    if cached and now_mono - cached["ts"] < _OHLCV_CACHE_TTL:
        return cached["df"]

    client = _dhan_client_ref
    dhan_interval = _DHAN_INTERVAL_MAP.get(interval, "5")

    # ── Attempt 1: Dhan intraday API ─────────────────────────────────────────
    if client is not None:
        security_id = get_security_id(symbol)
        if security_id:
            lookback = _DHAN_LOOKBACK_DAYS.get(dhan_interval, 7)
            from_dt  = datetime.now(IST) - timedelta(days=lookback)
            to_dt    = datetime.now(IST)
            from_date = from_dt.strftime("%Y-%m-%d")
            to_date   = to_dt.strftime("%Y-%m-%d")
            for attempt in range(3):
                try:
                    resp = client.intraday_minute_data(
                        security_id      = security_id,
                        exchange_segment = "NSE_EQ",
                        instrument_type  = "EQUITY",
                        interval         = dhan_interval,
                        from_date        = from_date,
                        to_date          = to_date,
                    )
                    if resp and resp.get("status") == "success":
                        df = _dhan_to_df(resp.get("data", {}))
                        if df is not None and not df.empty:
                            _ohlcv_cache[cache_key] = {"df": df, "ts": now_mono}
                            return df
                    break
                except Exception as e:
                    wait = 2 ** attempt
                    logger.debug(f"Dhan OHLCV {symbol} attempt {attempt+1}: {e}")
                    _time.sleep(wait)

    # ── Attempt 2: NSE public JSON API (no auth, EOD only for 1h+ intervals) ─
    # Only useful as a last-resort for daily/hourly data when Dhan is down
    if dhan_interval == "60":
        try:
            import requests
            nse_url = (
                "https://www.nseindia.com/api/historical/cm/equity"
                f"?symbol={symbol}&series=[%22EQ%22]"
                f"&from={(datetime.now(IST)-timedelta(days=30)).strftime('%d-%m-%Y')}"
                f"&to={datetime.now(IST).strftime('%d-%m-%Y')}"
            )
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://www.nseindia.com",
            }
            r = requests.get(nse_url, headers=headers, timeout=10)
            if r.status_code == 200:
                rows = r.json().get("data", [])
                if rows:
                    records = []
                    for row in rows:
                        try:
                            records.append({
                                "open":   float(row.get("CH_OPENING_PRICE", 0)),
                                "high":   float(row.get("CH_TRADE_HIGH_PRICE", 0)),
                                "low":    float(row.get("CH_TRADE_LOW_PRICE", 0)),
                                "close":  float(row.get("CH_CLOSING_PRICE", 0)),
                                "volume": float(row.get("CH_TOT_TRADED_QTY", 0)),
                                "date":   row.get("CH_TIMESTAMP", ""),
                            })
                        except Exception:
                            pass
                    if records:
                        df = pd.DataFrame(records)
                        df["date"] = pd.to_datetime(df["date"])
                        df = df.set_index("date").sort_index()
                        if df.index.tzinfo is None:
                            df.index = df.index.tz_localize("Asia/Kolkata")
                        _ohlcv_cache[cache_key] = {"df": df, "ts": now_mono}
                        return df
        except Exception as e:
            logger.debug(f"NSE public API fallback {symbol}: {e}")

    logger.debug(f"{symbol}: no OHLCV data available (Dhan client not set + no fallback)")
    return None


def get_ohlcv_multi_tf(symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
    """
    Fetch 5m candles from Dhan and resample to 15m and 1h.
    One API call per symbol instead of three.
    """
    df_5m = get_ohlcv(symbol, interval="5m", period="5d")
    if df_5m is None or df_5m.empty:
        return {"5m": None, "15m": None, "1h": None}

    def _resample(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
        try:
            r = df.resample(rule).agg({
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }).dropna()
            return r if not r.empty else None
        except Exception:
            return None

    return {
        "5m":  df_5m,
        "15m": _resample(df_5m, "15min"),
        "1h":  _resample(df_5m, "1h"),
    }


# ── India VIX ────────────────────────────────────────────────────────────────

_vix_cache: dict = {}
_VIX_TTL = 1800.0  # 30 min

# Dhan security IDs for NSE indices
_NIFTY_SECURITY_ID  = "13"    # Nifty 50
_VIX_SECURITY_ID    = "20626" # India VIX
_NSE_INDEX_SEGMENT  = "IDX_I"
_INDEX_INSTRUMENT   = "INDEX"


def get_india_vix() -> float:
    """
    Fetch India VIX from Dhan index API, fallback to NSE public endpoint.
    Cached 30 minutes. Returns 0.0 on failure (fail-open).
    """
    now = _time.monotonic()
    if _vix_cache.get("ts", 0) > now - _VIX_TTL:
        return _vix_cache.get("vix", 0.0)

    # Attempt 1: Dhan index candle data
    client = _dhan_client_ref
    if client is not None:
        try:
            from_date = (datetime.now(IST) - timedelta(days=3)).strftime("%Y-%m-%d")
            to_date   = datetime.now(IST).strftime("%Y-%m-%d")
            resp = client.intraday_minute_data(
                security_id      = _VIX_SECURITY_ID,
                exchange_segment = _NSE_INDEX_SEGMENT,
                instrument_type  = _INDEX_INSTRUMENT,
                interval         = "5",
                from_date        = from_date,
                to_date          = to_date,
            )
            if resp and resp.get("status") == "success":
                df = _dhan_to_df(resp.get("data", {}))
                if df is not None and not df.empty:
                    vix = float(df["close"].iloc[-1])
                    _vix_cache["vix"] = vix
                    _vix_cache["ts"]  = now
                    return vix
        except Exception as e:
            logger.debug(f"Dhan VIX fetch: {e}")

    # Attempt 2: NSE public API
    try:
        import requests
        r = requests.get(
            "https://www.nseindia.com/api/allIndices",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                     "Referer": "https://www.nseindia.com"},
            timeout=8,
        )
        if r.status_code == 200:
            for idx in r.json().get("data", []):
                if "VIX" in str(idx.get("indexSymbol", "")).upper():
                    vix = float(idx.get("last", 0))
                    if vix > 0:
                        _vix_cache["vix"] = vix
                        _vix_cache["ts"]  = now
                        return vix
    except Exception as e:
        logger.debug(f"NSE VIX fallback: {e}")

    return 0.0


# ── Nifty 50 intraday data ────────────────────────────────────────────────────

def get_nifty_intraday(interval: str = "5m") -> Optional[pd.DataFrame]:
    """Fetch Nifty50 intraday data from Dhan index API for regime detection."""
    dhan_interval = _DHAN_INTERVAL_MAP.get(interval, "5")
    client = _dhan_client_ref
    if client is None:
        logger.debug("get_nifty_intraday: no Dhan client")
        return None
    try:
        from_date = (datetime.now(IST) - timedelta(days=7)).strftime("%Y-%m-%d")
        to_date   = datetime.now(IST).strftime("%Y-%m-%d")
        resp = client.intraday_minute_data(
            security_id      = _NIFTY_SECURITY_ID,
            exchange_segment = _NSE_INDEX_SEGMENT,
            instrument_type  = _INDEX_INSTRUMENT,
            interval         = dhan_interval,
            from_date        = from_date,
            to_date          = to_date,
        )
        if resp and resp.get("status") == "success":
            return _dhan_to_df(resp.get("data", {}))
    except Exception as e:
        logger.debug(f"Nifty intraday Dhan: {e}")

    # Fallback: NSE public API for Nifty last price (returns scalar, not OHLCV series)
    try:
        import requests
        r = requests.get(
            "https://www.nseindia.com/api/allIndices",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                     "Referer": "https://www.nseindia.com"},
            timeout=8,
        )
        if r.status_code == 200:
            for idx in r.json().get("data", []):
                if idx.get("indexSymbol") == "NIFTY 50":
                    price = float(idx.get("last", 0))
                    if price > 0:
                        now_ist = datetime.now(IST)
                        df = pd.DataFrame(
                            [{"open": price, "high": price, "low": price,
                              "close": price, "volume": 0}],
                            index=pd.DatetimeIndex([now_ist]),
                        )
                        return df
    except Exception as e:
        logger.debug(f"NSE Nifty fallback: {e}")

    return None


# ── Fallback security_id map (top 30 NSE stocks) ─────────────────────────────
# Used only if scrip master download fails. Dhan security IDs as of 2024.
_FALLBACK_MAP: Dict[str, str] = {
    "RELIANCE":    "2885",
    "TCS":         "11536",
    "HDFCBANK":    "1333",
    "INFY":        "1594",
    "ICICIBANK":   "4963",
    "SBIN":        "3045",
    "BHARTIARTL":  "10604",
    "KOTAKBANK":   "1922",
    "ITC":         "1660",
    "AXISBANK":    "5900",
    "LT":          "11483",
    "BAJFINANCE":  "317",
    "MARUTI":      "10999",
    "TITAN":       "3506",
    "WIPRO":       "3787",
    "TATAMOTORS":  "3456",
    "TATASTEEL":   "3457",
    "JSWSTEEL":    "11723",
    "ONGC":        "2475",
    "NTPC":        "11630",
    "POWERGRID":   "14977",
    "SUNPHARMA":   "3351",
    "DIVISLAB":    "10940",
    "DRREDDY":     "881",
    "CIPLA":       "694",
    "HCLTECH":     "7229",
    "TECHM":       "13538",
    "HINDUNILVR":  "1394",
    "NESTLEIND":   "17963",
    "ASIANPAINT":  "236",
}
