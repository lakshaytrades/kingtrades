"""
data_fetch_dhan.py — Market data for Indian NSE market
Primary OHLCV:   Dhan API (authenticated — works reliably from server/VPS IPs)
Fallback OHLCV:  NSE charting API (charting.nseindia.com — no auth, but Akamai
                 blocks most datacenter/VPS IPs with HTTP 403 "Access Denied")
Real-time LTP:   Dhan API (for order placement & live P&L)
Index data:      Dhan IDX_I segment (NIFTY 50, INDIA VIX) → NSE allIndices fallback
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


# Dhan index security IDs (IDX_I segment). Used so VIX / Nifty level keep working
# when NSE's public allIndices endpoint is IP-blocked (403) on the VPS.
_DHAN_IDX_SEGMENT  = "IDX_I"
_DHAN_NIFTY50_ID   = 13   # NIFTY 50
_DHAN_INDIAVIX_ID  = 21   # INDIA VIX


def _fetch_dhan_index_ohlc(security_id: int) -> Optional[dict]:
    """
    Fetch an index snapshot from Dhan (IDX_I segment) via ohlc_data.
    Returns {'last': float, 'open': float, 'high': float, 'low': float,
    'close': float} or None. 'close' is the previous-day close.
    """
    client = _dhan_client_ref
    if client is None:
        return None
    for attempt in range(3):
        try:
            resp = client.ohlc_data(securities={_DHAN_IDX_SEGMENT: [int(security_id)]})
            if resp and resp.get("status") == "success":
                seg = resp.get("data", {}).get(_DHAN_IDX_SEGMENT, {})
                entry = seg.get(str(security_id)) or seg.get(security_id)
                if entry:
                    ltp = float(entry.get("last_price", 0) or entry.get("ltp", 0) or 0)
                    ohlc = entry.get("ohlc", {}) or {}
                    return {
                        "last":  ltp,
                        "open":  float(ohlc.get("open", 0) or 0),
                        "high":  float(ohlc.get("high", 0) or 0),
                        "low":   float(ohlc.get("low", 0) or 0),
                        "close": float(ohlc.get("close", 0) or 0),
                    }
            # transient empty/non-success → retry (don't abort the loop); this is
            # the primary VIX/Nifty path, so a single bad payload must not silently
            # disable the VIX risk gate.
            _time.sleep(2 ** attempt)
        except Exception as e:
            logger.debug(f"Dhan index {security_id} attempt {attempt+1}: {e}")
            _time.sleep(2 ** attempt)
    return None


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


# ── NSE charting session (shared across all OHLCV calls) ─────────────────────
# NSE requires browser-like headers and session cookies before it serves chart data.
# We create one session, warm it up with two GET requests, then reuse for 30 min.

_NSE_CHART_BASE  = "https://charting.nseindia.com"
_NSE_MAIN        = "https://www.nseindia.com"
_NSE_CHART_HDR   = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/plain, */*",
    "Accept-Encoding":  "gzip, deflate, br",
    "Accept-Language":  "en-US,en;q=0.9,hi;q=0.8",
    "Connection":       "keep-alive",
    "sec-ch-ua":        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

_nse_session: Optional[object] = None   # requests.Session
_nse_session_ts: float = 0.0
_NSE_SESSION_TTL = 1500.0   # refresh cookies every 25 min

_NSE_INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5",
    "10m": "10", "15m": "15", "30m": "30", "60m": "60", "1h": "60",
}

_ohlcv_cache: Dict[str, dict] = {}
_OHLCV_CACHE_TTL = 290.0   # cache just under scan interval (5 min)


def _get_nse_session():
    """
    Return a warmed-up requests.Session with valid NSE cookies.
    3-step warmup mirrors what a real browser does before loading charts:
      1. Main site → base cookies (nsit, nseappid, etc.)
      2. Market data page → trading-specific cookies
      3. Charting subdomain main page → charting-specific cookies
    Referer must always be nseindia.com (not charting subdomain) for CORS.
    """
    global _nse_session, _nse_session_ts
    import requests as _req
    now = _time.monotonic()
    if _nse_session is not None and now - _nse_session_ts < _NSE_SESSION_TTL:
        return _nse_session

    sess = _req.Session()
    sess.headers.update(_NSE_CHART_HDR)
    try:
        # Step 1: main NSE page — gets nsit, nseappid, ak_bmsc cookies
        sess.get(_NSE_MAIN, timeout=12)
        _time.sleep(0.6)
        # Step 2: market data page — gets additional trading cookies
        sess.get(
            f"{_NSE_MAIN}/market-data/live-equity-market",
            timeout=12,
            headers={"Referer": _NSE_MAIN + "/"},
        )
        _time.sleep(0.6)
        # Step 3: charting subdomain — gets charting-specific session token
        # Referer = nseindia.com (same-site origin, required by NSE CORS policy)
        sess.get(
            _NSE_CHART_BASE,
            timeout=12,
            headers={
                "Referer":        _NSE_MAIN + "/",
                "Origin":         _NSE_MAIN,
                "sec-fetch-site": "same-site",
                "sec-fetch-mode": "navigate",
                "sec-fetch-dest": "document",
            },
        )
        _time.sleep(0.3)
    except Exception as e:
        logger.debug(f"NSE session warmup: {e}")

    _nse_session = sess
    _nse_session_ts = now
    return sess


def _parse_nse_chart_response(raw) -> Optional[pd.DataFrame]:
    """
    Parse NSE charting API JSON into OHLCV DataFrame (IST-indexed).
    Handles two response shapes:
      - dict with 'grapthData' / 'graphData' key: list of [ts_ms, O, H, L, C, V]
      - dict with 'data' key containing the same list
    """
    try:
        if isinstance(raw, dict):
            rows = (
                raw.get("grapthData")
                or raw.get("graphData")
                or raw.get("data")
                or []
            )
        elif isinstance(raw, list):
            rows = raw
        else:
            return None

        if not rows:
            return None

        records = []
        for row in rows:
            try:
                if not isinstance(row, (list, tuple)) or len(row) < 6:
                    continue
                ts_raw = row[0]
                # timestamp can be ms epoch (int) or "DD-Mon-YYYY HH:MM" string
                if isinstance(ts_raw, (int, float)):
                    dt = pd.Timestamp(int(ts_raw), unit="ms", tz="Asia/Kolkata")
                else:
                    dt = pd.Timestamp(str(ts_raw)).tz_localize("Asia/Kolkata")
                records.append({
                    "datetime": dt,
                    "open":     float(row[1]),
                    "high":     float(row[2]),
                    "low":      float(row[3]),
                    "close":    float(row[4]),
                    "volume":   float(row[5]),
                })
            except Exception:
                continue

        if not records:
            return None

        df = pd.DataFrame(records).set_index("datetime").sort_index()
        return df.dropna()

    except Exception as e:
        logger.debug(f"_parse_nse_chart_response: {e}")
        return None


def _fetch_nse_chart(symbol: str, interval_min: int) -> Optional[pd.DataFrame]:
    """
    Call NSE charting endpoint for one symbol + interval.
    Retries once after refreshing cookies on 401/403/empty response.
    Key: Referer must be nseindia.com (same-site CORS) not the charting subdomain.
    """
    import requests as _req

    def _call(sess):
        url = f"{_NSE_CHART_BASE}/Charts/symbolhistoricaldata/{symbol}"
        params = {"time": str(interval_min), "type": "EQ"}
        # Referer = nseindia.com (browser behaviour: chart loaded from main site)
        # sec-fetch-site = same-site (charting.nseindia.com ↔ nseindia.com)
        hdrs = {
            "Referer":        _NSE_MAIN + "/",
            "Origin":         _NSE_MAIN,
            "sec-fetch-site": "same-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }
        resp = sess.get(url, params=params, headers=hdrs, timeout=15)
        return resp

    sess = _get_nse_session()
    for attempt in range(2):
        try:
            resp = _call(sess)
            if resp.status_code in (401, 403) and attempt == 0:
                # Cookies stale — force refresh and retry
                global _nse_session_ts
                _nse_session_ts = 0.0
                sess = _get_nse_session()
                continue
            if resp.status_code != 200:
                logger.debug(f"NSE chart {symbol}: HTTP {resp.status_code}")
                return None
            raw = resp.json()
            df = _parse_nse_chart_response(raw)
            if df is not None and not df.empty:
                return df
            # Empty response — force cookie refresh on next call
            if attempt == 0:
                _nse_session_ts = 0.0
                sess = _get_nse_session()
        except _req.exceptions.RequestException as e:
            logger.debug(f"NSE chart {symbol} attempt {attempt+1}: {e}")
            if attempt == 0:
                _time.sleep(2)
        except Exception as e:
            logger.debug(f"NSE chart parse {symbol}: {e}")
            break

    return None


def _fetch_dhan_ohlcv(symbol: str, interval_min: int) -> Optional[pd.DataFrame]:
    """
    Fetch intraday OHLCV from Dhan's authenticated API. Works reliably from
    server/VPS IPs (unlike NSE charting, which Akamai blocks with 403).
    Returns IST-indexed DataFrame or None.
    """
    client = _dhan_client_ref
    if client is None:
        return None
    security_id = get_security_id(symbol)
    if not security_id:
        return None

    dhan_iv   = str(interval_min) if interval_min in (1, 5, 15, 25, 60) else "5"
    from_date = (datetime.now(IST) - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date   = datetime.now(IST).strftime("%Y-%m-%d")
    for attempt in range(3):
        try:
            resp = client.intraday_minute_data(
                security_id=security_id, exchange_segment="NSE_EQ",
                instrument_type="EQUITY", interval=dhan_iv,
                from_date=from_date, to_date=to_date,
            )
            if resp and resp.get("status") == "success":
                raw = resp.get("data", {})
                ts  = raw.get("timestamp") or raw.get("start_Time") or []
                if ts:
                    idx = (
                        pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
                        if isinstance(ts[0], (int, float))
                        else pd.to_datetime(ts, utc=True).tz_convert("Asia/Kolkata")
                    )
                    df2 = pd.DataFrame({
                        "open":   raw.get("open",   []),
                        "high":   raw.get("high",   []),
                        "low":    raw.get("low",    []),
                        "close":  raw.get("close",  []),
                        "volume": raw.get("volume", []),
                    }, index=idx).dropna()
                    if not df2.empty:
                        return df2
            return None
        except Exception as e:
            logger.debug(f"Dhan OHLCV {symbol} attempt {attempt+1}: {e}")
            _time.sleep(2 ** attempt)
    return None


# ── Yahoo Finance (FREE, no broker account, works from VPS) ──────────────────
# Alternative data source for users who don't use Dhan. Serves NSE equity
# (SYMBOL.NS), India VIX (^INDIAVIX) and Nifty (^NSEI). Intraday ~60d history,
# ~15-min delayed on the free feed — fine for manual/swing signals.
_YH_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_YH_INTERVAL = {1: "1m", 3: "5m", 5: "5m", 10: "15m", 15: "15m",
                30: "30m", 60: "60m"}
_YH_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0.0.0 Safari/537.36"}


def _fetch_yahoo_chart(yf_symbol: str, interval_min: int,
                       rng: str = "5d") -> Optional[pd.DataFrame]:
    """Fetch OHLCV from Yahoo for a Yahoo symbol (e.g. RELIANCE.NS, ^NSEI)."""
    import requests as _req
    iv = _YH_INTERVAL.get(interval_min, "5m")
    url = f"{_YH_BASE}/{yf_symbol}"
    for attempt in range(2):
        try:
            r = _req.get(url, params={"range": rng, "interval": iv},
                         headers=_YH_HDR, timeout=15)
            if r.status_code != 200:
                _time.sleep(1.5)
                continue
            res = r.json()["chart"]["result"][0]
            ts = res.get("timestamp") or []
            q = res["indicators"]["quote"][0]
            rows = []
            for i in range(len(ts)):
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                v = (q.get("volume") or [0] * len(ts))[i]
                if None in (o, h, l, c):
                    continue
                rows.append((ts[i], o, h, l, c, v or 0))
            if not rows:
                return None
            idx = pd.to_datetime([x[0] for x in rows], unit="s", utc=True
                                 ).tz_convert("Asia/Kolkata")
            df = pd.DataFrame({
                "open":   [x[1] for x in rows],
                "high":   [x[2] for x in rows],
                "low":    [x[3] for x in rows],
                "close":  [x[4] for x in rows],
                "volume": [x[5] for x in rows],
            }, index=idx).dropna()
            return df if not df.empty else None
        except Exception as e:
            logger.debug(f"Yahoo {yf_symbol} attempt {attempt+1}: {e}")
            _time.sleep(1.5)
    return None


def _fetch_yahoo_ohlcv(symbol: str, interval_min: int) -> Optional[pd.DataFrame]:
    """NSE equity OHLCV via Yahoo (SYMBOL.NS)."""
    return _fetch_yahoo_chart(f"{symbol}.NS", interval_min, rng="5d")


def get_ohlcv(symbol: str, interval: str = "5m", period: str = "5d") -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV candles. Source priority:
      1. Dhan intraday API   (if a Dhan client is registered)
      2. Yahoo Finance       (free, no account — SYMBOL.NS)
      3. NSE charting API    (only on non-blocked/residential IPs)
    Returns DataFrame: lowercase open/high/low/close/volume, IST-indexed. ~5m cache.
    """
    cache_key = f"{symbol}_{interval}"
    now_mono  = _time.monotonic()
    cached    = _ohlcv_cache.get(cache_key)
    if cached and now_mono - cached["ts"] < _OHLCV_CACHE_TTL:
        return cached["df"]

    interval_min = int(_NSE_INTERVAL_MAP.get(interval, "5"))

    # 1. Dhan (only if a client is registered)
    df = _fetch_dhan_ohlcv(symbol, interval_min)
    if df is not None and not df.empty:
        _ohlcv_cache[cache_key] = {"df": df, "ts": now_mono}
        return df

    # 2. Yahoo Finance (free, no broker account)
    df = _fetch_yahoo_ohlcv(symbol, interval_min)
    if df is not None and not df.empty:
        _ohlcv_cache[cache_key] = {"df": df, "ts": now_mono}
        return df

    # 3. NSE charting (residential IPs only)
    df = _fetch_nse_chart(symbol, interval_min)
    if df is not None and not df.empty:
        _ohlcv_cache[cache_key] = {"df": df, "ts": now_mono}
        return df

    logger.debug(f"{symbol}: OHLCV unavailable from Dhan, Yahoo, or NSE")
    return None


def get_ohlcv_multi_tf(symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
    """
    Fetch 5m candles (NSE charting API) then resample to 15m and 1h.
    One network call per symbol.
    """
    df_5m = get_ohlcv(symbol, interval="5m", period="5d")
    if df_5m is None or df_5m.empty:
        return {"5m": None, "15m": None, "1h": None}

    def _resample(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
        try:
            r = df.resample(rule).agg({
                "open": "first", "high": "max",
                "low": "min",    "close": "last", "volume": "sum",
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


def get_india_vix() -> float:
    """
    Fetch India VIX. Dhan IDX_I (id 21) is primary; NSE allIndices is fallback
    (NSE is IP-blocked on most VPS hosts). Cached 30 min.
    Returns 0.0 on failure (fail-open — bot continues without VIX gate).
    """
    now = _time.monotonic()
    if _vix_cache.get("ts") and now - _vix_cache["ts"] < _VIX_TTL:
        return _vix_cache.get("vix", 0.0)

    # Primary: Dhan IDX_I segment
    snap = _fetch_dhan_index_ohlc(_DHAN_INDIAVIX_ID)
    if snap and snap.get("last", 0) > 0:
        vix = snap["last"]
        _vix_cache["vix"] = vix
        _vix_cache["ts"]  = now
        return vix

    # Yahoo: ^INDIAVIX (free, no account)
    ydf = _fetch_yahoo_chart("^INDIAVIX", 5, rng="1d")
    if ydf is not None and not ydf.empty:
        vix = float(ydf["close"].iloc[-1])
        if vix > 0:
            _vix_cache["vix"] = vix
            _vix_cache["ts"]  = now
            return vix

    # Fallback: NSE allIndices
    try:
        sess = _get_nse_session()
        r = sess.get(
            f"{_NSE_MAIN}/api/allIndices",
            headers={**_NSE_CHART_HDR, "Referer": _NSE_MAIN},
            timeout=8,
        )
        if r.status_code == 200:
            for idx in r.json().get("data", []):
                name = str(idx.get("indexSymbol", "") or idx.get("index", "")).upper()
                if "VIX" in name:
                    vix = float(idx.get("last") or idx.get("lastPrice") or 0)
                    if vix > 0:
                        _vix_cache["vix"] = vix
                        _vix_cache["ts"]  = now
                        return vix
    except Exception as e:
        logger.debug(f"get_india_vix: {e}")

    return 0.0


# ── Nifty 50 intraday data ────────────────────────────────────────────────────

def get_nifty_intraday(interval: str = "5m") -> Optional[pd.DataFrame]:
    """
    Fetch Nifty50 intraday candles from NSE charting API (symbol=NIFTY+50, type=IDX).
    Falls back to single-row DataFrame from allIndices if charting fails.
    """
    interval_min = int(_NSE_INTERVAL_MAP.get(interval, "5"))

    # Attempt -1: Yahoo ^NSEI (free, no broker account)
    ydf = _fetch_yahoo_chart("^NSEI", interval_min, rng="5d")
    if ydf is not None and not ydf.empty:
        return ydf

    # Attempt 0: Dhan index intraday (IDX_I) — primary on VPS where NSE is blocked
    client = _dhan_client_ref
    if client is not None:
        dhan_iv = str(interval_min) if interval_min in (1, 5, 15, 25, 60) else "5"
        from_date = (datetime.now(IST) - timedelta(days=5)).strftime("%Y-%m-%d")
        to_date   = datetime.now(IST).strftime("%Y-%m-%d")
        try:
            resp = client.intraday_minute_data(
                security_id=str(_DHAN_NIFTY50_ID), exchange_segment=_DHAN_IDX_SEGMENT,
                instrument_type="INDEX", interval=dhan_iv,
                from_date=from_date, to_date=to_date,
            )
            if resp and resp.get("status") == "success":
                raw = resp.get("data", {})
                ts  = raw.get("timestamp") or raw.get("start_Time") or []
                if ts:
                    idx_dt = (
                        pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
                        if isinstance(ts[0], (int, float))
                        else pd.to_datetime(ts, utc=True).tz_convert("Asia/Kolkata")
                    )
                    dfd = pd.DataFrame({
                        "open":   raw.get("open",   []),
                        "high":   raw.get("high",   []),
                        "low":    raw.get("low",    []),
                        "close":  raw.get("close",  []),
                        "volume": raw.get("volume", []),
                    }, index=idx_dt).dropna()
                    if not dfd.empty:
                        return dfd
        except Exception as e:
            logger.debug(f"Dhan Nifty intraday: {e}")

    # Attempt 1: NSE charting API for Nifty index
    try:
        sess = _get_nse_session()
        url  = f"{_NSE_CHART_BASE}/Charts/symbolhistoricaldata/NIFTY%2050"
        resp = sess.get(
            url,
            params={"time": str(interval_min), "type": "IDX"},
            headers={**_NSE_CHART_HDR, "Referer": f"{_NSE_CHART_BASE}/"},
            timeout=15,
        )
        if resp.status_code == 200:
            df = _parse_nse_chart_response(resp.json())
            if df is not None and not df.empty:
                return df
    except Exception as e:
        logger.debug(f"Nifty chart: {e}")

    # Attempt 2: allIndices last price → single-row df for regime detection
    try:
        sess = _get_nse_session()
        r = sess.get(
            f"{_NSE_MAIN}/api/allIndices",
            headers={**_NSE_CHART_HDR, "Referer": _NSE_MAIN},
            timeout=8,
        )
        if r.status_code == 200:
            for idx in r.json().get("data", []):
                sym = str(idx.get("indexSymbol", "") or idx.get("index", "")).upper()
                if sym in ("NIFTY 50", "NIFTY50"):
                    price = float(idx.get("last") or idx.get("lastPrice") or 0)
                    if price > 0:
                        now_ist = datetime.now(IST)
                        return pd.DataFrame(
                            [{"open": price, "high": price,
                              "low": price, "close": price, "volume": 0}],
                            index=pd.DatetimeIndex([now_ist]),
                        )
    except Exception as e:
        logger.debug(f"Nifty allIndices fallback: {e}")

    return None


# ── NSE allIndices helpers (Nifty level, VIX, sector returns) ────────────────
# These replace all yfinance ^NSEI / ^INDIAVIX / ^NIFTYBANK etc. calls across
# the codebase. Single shared session — same cookies as OHLCV fetches.

_NSE_SECTOR_INDEX_MAP = {
    "NIFTY BANK":     "Banking",
    "NIFTY AUTO":     "Auto",
    "NIFTY IT":       "IT",
    "NIFTY PHARMA":   "Pharma",
    "NIFTY FMCG":     "FMCG",
    "NIFTY ENERGY":   "Energy",
    "NIFTY METAL":    "Metal",
    "NIFTY INFRA":    "Infra",
    "NIFTY PSU BANK": "PSU Bank",
    "NIFTY REALTY":   "Realty",
    "NIFTY MIDCAP 100": "Midcap",
    "NIFTY SMALLCAP 100": "Smallcap",
}

_indices_cache: dict = {}
_INDICES_TTL = 120.0   # 2-min cache (allIndices is lightweight)


def _fetch_all_indices() -> list:
    """Fetch NSE allIndices JSON. Cached 2 min. Returns list of index dicts."""
    now = _time.monotonic()
    if _indices_cache.get("ts") and now - _indices_cache["ts"] < _INDICES_TTL:
        return _indices_cache.get("data", [])
    try:
        sess = _get_nse_session()
        r = sess.get(
            f"{_NSE_MAIN}/api/allIndices",
            headers={**_NSE_CHART_HDR, "Referer": _NSE_MAIN},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            _indices_cache["data"] = data
            _indices_cache["ts"]   = now
            return data
    except Exception as e:
        logger.debug(f"_fetch_all_indices: {e}")
    return _indices_cache.get("data", [])   # return stale if fetch fails


def get_nifty_level() -> dict:
    """
    Return current Nifty50 snapshot: level, open, change_pct.
    e.g. {'level': 24512.0, 'open': 24300.0, 'change_pct': 0.87}
    Returns zeros on failure (fail-open).
    """
    for idx in _fetch_all_indices():
        sym = str(idx.get("indexSymbol", "") or idx.get("index", "")).upper()
        if sym in ("NIFTY 50", "NIFTY50"):
            try:
                return {
                    "level":      float(idx.get("last")       or idx.get("lastPrice") or 0),
                    "open":       float(idx.get("open")       or 0),
                    "change_pct": float(idx.get("percChange") or idx.get("percentChange") or 0),
                }
            except Exception:
                pass

    # Fallback: Dhan IDX_I (id 13) when NSE allIndices is blocked on the VPS
    snap = _fetch_dhan_index_ohlc(_DHAN_NIFTY50_ID)
    if snap and snap.get("last", 0) > 0:
        level    = snap["last"]
        open_px  = snap.get("open", 0) or 0
        prev_clo = snap.get("close", 0) or 0
        base     = prev_clo or open_px
        change   = round((level - base) / base * 100, 2) if base else 0.0
        return {"level": level, "open": open_px, "change_pct": change}

    # Yahoo: ^NSEI daily (free, no account)
    ydf = _fetch_yahoo_chart("^NSEI", 60, rng="2d")
    if ydf is not None and not ydf.empty:
        level   = float(ydf["close"].iloc[-1])
        open_px = float(ydf["open"].iloc[-1])
        # change vs first bar of the latest session (intraday-style)
        sess = ydf[ydf.index.date == ydf.index[-1].date()]
        base = float(sess["open"].iloc[0]) if not sess.empty else open_px
        change = round((level - base) / base * 100, 2) if base else 0.0
        if level > 0:
            return {"level": level, "open": base, "change_pct": change}

    return {"level": 0.0, "open": 0.0, "change_pct": 0.0}


def get_all_sector_returns() -> Dict[str, float]:
    """
    Return {sector_name: daily_return_relative_to_nifty} using NSE allIndices.
    e.g. {'Banking': 1.2, 'IT': -0.4, ...}
    Replaces yfinance sector ETF downloads (blocked from server IPs).
    Cached 2 min via _fetch_all_indices().
    """
    indices = _fetch_all_indices()
    if not indices:
        return {}

    # Get Nifty base return first
    nifty_pct = 0.0
    for idx in indices:
        sym = str(idx.get("indexSymbol", "") or idx.get("index", "")).upper()
        if sym in ("NIFTY 50", "NIFTY50"):
            try:
                nifty_pct = float(idx.get("percChange") or idx.get("percentChange") or 0)
            except Exception:
                pass
            break

    result: Dict[str, float] = {}
    for idx in indices:
        sym = str(idx.get("indexSymbol", "") or idx.get("index", "")).upper()
        sector = _NSE_SECTOR_INDEX_MAP.get(sym)
        if sector:
            try:
                pct = float(idx.get("percChange") or idx.get("percentChange") or 0)
                result[sector] = round(pct - nifty_pct, 4)
            except Exception:
                pass

    return result


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
