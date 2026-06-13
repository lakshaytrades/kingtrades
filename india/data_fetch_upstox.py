"""
data_fetch_upstox.py — Market data for NSE via Upstox API v2

Replaces data_fetch_dhan.py. Public function names are IDENTICAL so call sites
only change the import path.

  Instrument master:  Upstox NSE.csv.gz → {symbol: instrument_key}  (NSE_EQ|ISIN)
  Real-time LTP:      Upstox MarketQuoteApi.ltp()
  OHLCV candles:      Upstox HistoryApi (1-minute) resampled to 5m/15m/1h
  India VIX / Nifty:  Upstox index instrument keys (NSE fallback retained)

`get_security_id(symbol)` returns the Upstox instrument_key (a string like
"NSE_EQ|INE002A01018"). Call sites treat it opaquely and hand it to the
executor, so the Dhan→Upstox change is transparent to them.
"""
import gzip
import io
import json
import logging
import os
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger("data_fetch_upstox")
IST = ZoneInfo("Asia/Kolkata")

logging.getLogger("urllib3").setLevel(logging.WARNING)

# Upstox NSE instrument master — try multiple URLs (Upstox changes these occasionally)
_INSTRUMENTS_URLS = [
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
    "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz",
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz",
]
_INSTRUMENTS_URL  = _INSTRUMENTS_URLS[0]   # kept for compat
_INSTR_CACHE_PATH = Path(__file__).parent.parent / "data" / "upstox_instruments_nse.json"
_INSTR_CACHE_TTL  = 86400   # refresh daily

_quote_cache: Dict[str, dict] = {}
_quote_cache_ts: Dict[str, float] = {}
_QUOTE_CACHE_TTL = 30.0

_symbol_map: Dict[str, str] = {}    # symbol → instrument_key
_symbol_map_loaded = False

# Module-level client — set via set_upstox_client() at bot startup
_upstox_client_ref = None

_OHLCV_CACHE_TTL = 290.0
_ohlcv_cache: Dict[str, dict] = {}

# Index instrument keys
_NIFTY_KEY = "NSE_INDEX|Nifty 50"
_VIX_KEY   = "NSE_INDEX|India VIX"

_NSE_INTERVAL_MAP = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "1d": "1440"}

# Comprehensive fallback — Nifty 50 + Next 50 + liquid midcaps.
# Used when the Upstox instrument master download fails.
_FALLBACK_MAP: Dict[str, str] = {
    # Nifty 50 core
    "RELIANCE":    "NSE_EQ|INE002A01018",
    "TCS":         "NSE_EQ|INE467B01029",
    "INFY":        "NSE_EQ|INE009A01021",
    "HDFCBANK":    "NSE_EQ|INE040A01034",
    "ICICIBANK":   "NSE_EQ|INE090A01021",
    "SBIN":        "NSE_EQ|INE062A01020",
    "AXISBANK":    "NSE_EQ|INE238A01034",
    "KOTAKBANK":   "NSE_EQ|INE237A01028",
    "HINDUNILVR":  "NSE_EQ|INE030A01027",
    "ITC":         "NSE_EQ|INE154A01025",
    "BHARTIARTL":  "NSE_EQ|INE397D01024",
    "ASIANPAINT":  "NSE_EQ|INE021A01026",
    "MARUTI":      "NSE_EQ|INE585B01010",
    "BAJFINANCE":  "NSE_EQ|INE296A01024",
    "WIPRO":       "NSE_EQ|INE075A01022",
    "ADANIENT":    "NSE_EQ|INE423A01024",
    "ADANIPORTS":  "NSE_EQ|INE742F01042",
    "TATAMOTORS":  "NSE_EQ|INE155A01022",
    "TATACONSUM":  "NSE_EQ|INE192A01025",
    "TATASTEEL":   "NSE_EQ|INE081A01012",
    "SUNPHARMA":   "NSE_EQ|INE044A01036",
    "DRREDDY":     "NSE_EQ|INE089A01023",
    "CIPLA":       "NSE_EQ|INE059A01026",
    "DIVISLAB":    "NSE_EQ|INE361B01024",
    "LT":          "NSE_EQ|INE018A01030",
    "POWERGRID":   "NSE_EQ|INE752E01010",
    "NTPC":        "NSE_EQ|INE733E01010",
    "ONGC":        "NSE_EQ|INE213A01029",
    "COALINDIA":   "NSE_EQ|INE522F01014",
    "BAJAJFINSV":  "NSE_EQ|INE918I01026",
    "HCLTECH":     "NSE_EQ|INE860A01027",
    "TECHM":       "NSE_EQ|INE669C01036",
    "NESTLEIND":   "NSE_EQ|INE239A01016",
    "TITAN":       "NSE_EQ|INE280A01028",
    "ULTRACEMCO":  "NSE_EQ|INE481G01011",
    "JSWSTEEL":    "NSE_EQ|INE019A01038",
    "GRASIM":      "NSE_EQ|INE047A01021",
    "HEROMOTOCO":  "NSE_EQ|INE158A01026",
    "EICHERMOT":   "NSE_EQ|INE066A01021",
    "BPCL":        "NSE_EQ|INE029A01011",
    "M&M":         "NSE_EQ|INE101A01026",
    "HINDALCO":    "NSE_EQ|INE038A01020",
    "BRITANNIA":   "NSE_EQ|INE216A01030",
    "SBILIFE":     "NSE_EQ|INE123W01016",
    "HDFCLIFE":    "NSE_EQ|INE795G01014",
    "APOLLOHOSP":  "NSE_EQ|INE437A01024",
    "DMART":       "NSE_EQ|INE192R01011",
    "INDUSINDBK":  "NSE_EQ|INE095A01012",
    "SHREECEM":    "NSE_EQ|INE070A01015",
    "TRENT":       "NSE_EQ|INE849A01020",
    # Nifty Next 50 / liquid midcaps
    "BAJAJ-AUTO":  "NSE_EQ|INE917I01010",
    "BAJAJ_AUTO":  "NSE_EQ|INE917I01010",
    "BAJAJAUTO":   "NSE_EQ|INE917I01010",
    "AMBUJACEM":   "NSE_EQ|INE079A01024",
    "ACC":         "NSE_EQ|INE012A01025",
    "ADANIGREEN":  "NSE_EQ|INE364U01010",
    "ADANITRANS":  "NSE_EQ|INE931S01010",
    "AUROPHARMA":  "NSE_EQ|INE406A01037",
    "BANDHANBNK":  "NSE_EQ|INE545U01014",
    "BANKBARODA":  "NSE_EQ|INE028A01039",
    "BEL":         "NSE_EQ|INE263A01024",
    "BERGEPAINT":  "NSE_EQ|INE463A01038",
    "BIOCON":      "NSE_EQ|INE376G01013",
    "BOSCHLTD":    "NSE_EQ|INE323A01026",
    "CANBK":       "NSE_EQ|INE476A01014",
    "CHOLAFIN":    "NSE_EQ|INE121A01024",
    "COLPAL":      "NSE_EQ|INE259A01022",
    "CONCOR":      "NSE_EQ|INE111A01025",
    "DABUR":       "NSE_EQ|INE016A01026",
    "DLF":         "NSE_EQ|INE271C01023",
    "FEDERALBNK":  "NSE_EQ|INE171A01029",
    "FORTIS":      "NSE_EQ|INE142G01027",
    "GAIL":        "NSE_EQ|INE129A01019",
    "GODREJCP":    "NSE_EQ|INE102D01028",
    "GODREJPROP":  "NSE_EQ|INE484J01027",
    "HAVELLS":     "NSE_EQ|INE176B01034",
    "ICICIPRULI":  "NSE_EQ|INE726G01019",
    "IDEA":        "NSE_EQ|INE669E01016",
    "IDFCFIRSTB":  "NSE_EQ|INE818H01020",
    "INDHOTEL":    "NSE_EQ|INE053A01029",
    "INDUSTOWER":  "NSE_EQ|INE121J01017",
    "IRCTC":       "NSE_EQ|INE335Y01020",
    "IRFC":        "NSE_EQ|INE053F01010",
    "JINDALSTEL":  "NSE_EQ|INE749A01030",
    "JUBLFOOD":    "NSE_EQ|INE797F01012",
    "LTF":         "NSE_EQ|INE523H01014",
    "LTIM":        "NSE_EQ|INE214T01019",
    "LUPIN":       "NSE_EQ|INE326A01037",
    "MCDOWELL-N":  "NSE_EQ|INE562A01011",
    "MFSL":        "NSE_EQ|INE247F01036",
    "MOTHERSON":   "NSE_EQ|INE775A01035",
    "MPHASIS":     "NSE_EQ|INE356A01018",
    "MRF":         "NSE_EQ|INE883A01011",
    "NAUKRI":      "NSE_EQ|INE663F01024",
    "NHPC":        "NSE_EQ|INE848E01016",
    "NMDC":        "NSE_EQ|INE584A01023",
    "OBEROIRLTY":  "NSE_EQ|INE093I01010",
    "OFSS":        "NSE_EQ|INE881D01027",
    "PAGEIND":     "NSE_EQ|INE761H01022",
    "PEL":         "NSE_EQ|INE142I01023",
    "PERSISTENT":  "NSE_EQ|INE262H01021",
    "PETRONET":    "NSE_EQ|INE347G01014",
    "PFC":         "NSE_EQ|INE134E01011",
    "PIDILITIND":  "NSE_EQ|INE318A01026",
    "PIIND":       "NSE_EQ|INE160A01022",
    "PNB":         "NSE_EQ|INE160A01022",
    "POLYCAB":     "NSE_EQ|INE455K01017",
    "RECLTD":      "NSE_EQ|INE020B01018",
    "SAIL":        "NSE_EQ|INE114A01011",
    "SIEMENS":     "NSE_EQ|INE003A01024",
    "SRF":         "NSE_EQ|INE647A01010",
    "TATAPOWER":   "NSE_EQ|INE245A01021",
    "TORNTPHARM":  "NSE_EQ|INE685A01028",
    "TORNTPOWER":  "NSE_EQ|INE813H01021",
    "TVSMOTOR":    "NSE_EQ|INE494B01023",
    "UBL":         "NSE_EQ|INE686F01025",
    "UNIONBANK":   "NSE_EQ|INE692A01016",
    "UPL":         "NSE_EQ|INE628A01036",
    "VEDL":        "NSE_EQ|INE205A01025",
    "VOLTAS":      "NSE_EQ|INE226A01021",
    "YESBANK":     "NSE_EQ|INE528G01035",
    "ZOMATO":      "NSE_EQ|INE758T01015",
    "ZYDUSLIFE":   "NSE_EQ|INE769A01020",
}


def set_upstox_client(client) -> None:
    """Register the live Upstox client so OHLCV/LTP/VIX functions can use it."""
    global _upstox_client_ref
    _upstox_client_ref = client


# ── Instrument master (symbol → instrument_key) ──────────────────────────────

def _load_instruments() -> Dict[str, str]:
    global _symbol_map, _symbol_map_loaded
    if _symbol_map_loaded:
        return _symbol_map

    # Local cache
    if _INSTR_CACHE_PATH.exists():
        age = _time.time() - _INSTR_CACHE_PATH.stat().st_mtime
        if age < _INSTR_CACHE_TTL:
            try:
                _symbol_map = json.loads(_INSTR_CACHE_PATH.read_text())
                _symbol_map_loaded = True
                logger.info(f"Upstox instruments loaded from cache: {len(_symbol_map)} symbols")
                return _symbol_map
            except Exception as e:
                logger.debug(f"instrument cache read failed: {e}")

    # Download fresh — try each URL in order
    import requests
    downloaded = False
    for url in _INSTRUMENTS_URLS:
        try:
            resp = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            content = resp.content
            # Try gzip decompress, fall back to raw
            try:
                raw = gzip.decompress(content)
            except Exception:
                raw = content
            # Try JSON parse; if CSV fall through to exception
            instruments = json.loads(raw)
            parsed = _parse_instruments(instruments)
            if len(parsed) > 50:   # sanity check — at least 50 symbols
                _symbol_map = parsed
                _INSTR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                _INSTR_CACHE_PATH.write_text(json.dumps(_symbol_map))
                _symbol_map_loaded = True
                logger.info(f"Upstox instruments downloaded from {url}: {len(_symbol_map)} symbols")
                downloaded = True
                break
        except Exception as e:
            logger.debug(f"Instrument download failed ({url}): {e}")

    if not downloaded:
        logger.warning(f"All instrument URLs failed — using expanded fallback map ({len(_FALLBACK_MAP)} symbols)")
        _symbol_map = _FALLBACK_MAP.copy()
        _symbol_map_loaded = True

    return _symbol_map


def _parse_instruments(instruments) -> Dict[str, str]:
    """Parse Upstox NSE instrument JSON → {trading_symbol: instrument_key} for equity."""
    result: Dict[str, str] = {}
    try:
        for inst in instruments:
            # Equity cash segment only
            seg = str(inst.get("segment", "")).upper()
            itype = str(inst.get("instrument_type", "")).upper()
            if seg != "NSE_EQ":
                continue
            if itype and itype not in ("EQ", "EQUITY"):
                continue
            sym = str(inst.get("trading_symbol", "") or inst.get("tradingsymbol", "")).strip().upper()
            key = str(inst.get("instrument_key", "")).strip()
            if sym and key:
                result[sym] = key
    except Exception as e:
        logger.warning(f"instrument parse error: {e}")
        return _FALLBACK_MAP.copy()
    if not result:
        return _FALLBACK_MAP.copy()
    # Merge fallback for any names missing from the master
    for s, k in _FALLBACK_MAP.items():
        result.setdefault(s, k)
    return result


def get_security_id(symbol: str) -> Optional[str]:
    """Return Upstox instrument_key for an NSE symbol (opaque to callers)."""
    mapping = _load_instruments()
    key = mapping.get(symbol.upper()) or mapping.get(symbol.upper().replace("-EQ", ""))
    if not key:
        logger.debug(f"instrument_key not found for {symbol}")
    return key


# ── Real-time LTP from Upstox ─────────────────────────────────────────────────

def _quote_response_data(resp):
    """Normalize Upstox SDK response to a plain dict of {key: entry}."""
    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp.get("data")
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    # SDK object: convert attribute map
    try:
        return {k: v for k, v in data.items()}
    except Exception:
        return {}


def get_ltp(symbol: str, dhan_client=None) -> Optional[float]:
    """Fetch current last-traded price via Upstox. Cached 30s.
    (Second arg kept for signature compatibility; uses module client if None.)"""
    now = _time.monotonic()
    if symbol in _quote_cache and now - _quote_cache_ts.get(symbol, 0) < _QUOTE_CACHE_TTL:
        return _quote_cache[symbol].get("ltp")

    client = dhan_client or _upstox_client_ref
    if client is None:
        return None
    key = get_security_id(symbol)
    if not key:
        return None

    for attempt in range(4):
        try:
            resp = client.market_quote.ltp(instrument_key=key, api_version="2.0")
            data = _quote_response_data(resp)
            for _k, entry in data.items():
                ltp = float(getattr(entry, "last_price", None)
                            or (entry.get("last_price") if isinstance(entry, dict) else 0) or 0)
                if ltp > 0:
                    _quote_cache[symbol] = {"ltp": ltp}
                    _quote_cache_ts[symbol] = now
                    return ltp
            return None
        except Exception as e:
            logger.debug(f"LTP {symbol} attempt {attempt+1}: {e}")
            _time.sleep(2 ** attempt)
    return None


def get_multiple_ltp(symbols: List[str], dhan_client=None) -> Dict[str, float]:
    """Batch LTP fetch. Upstox accepts comma-separated instrument keys."""
    result: Dict[str, float] = {}
    client = dhan_client or _upstox_client_ref
    if client is None:
        return result

    key_to_sym: Dict[str, str] = {}
    keys: List[str] = []
    for sym in symbols:
        k = get_security_id(sym)
        if k:
            key_to_sym[k] = sym
            keys.append(k)
    if not keys:
        return result

    # Upstox allows up to ~500 keys per request; page at 100 to be safe
    for i in range(0, len(keys), 100):
        batch = keys[i:i + 100]
        for attempt in range(4):
            try:
                resp = client.market_quote.ltp(
                    instrument_key=",".join(batch), api_version="2.0")
                data = _quote_response_data(resp)
                for _resp_key, entry in data.items():
                    # Upstox returns instrument_token inside each entry
                    itok = (getattr(entry, "instrument_token", None)
                            or (entry.get("instrument_token") if isinstance(entry, dict) else None))
                    ltp = float(getattr(entry, "last_price", None)
                                or (entry.get("last_price") if isinstance(entry, dict) else 0) or 0)
                    sym = key_to_sym.get(itok)
                    if sym is None:
                        # Fall back: response key is like "NSE_EQ:RELIANCE"
                        tail = str(_resp_key).split(":")[-1].upper()
                        sym = next((s for s in symbols if s.upper() == tail), None)
                    if sym and ltp > 0:
                        result[sym] = ltp
                break
            except Exception as e:
                logger.debug(f"Batch LTP attempt {attempt+1}: {e}")
                _time.sleep(2 ** attempt)
    return result


# ── OHLCV candles from Upstox (1-min base, resampled) ────────────────────────

def _candles_to_df(candles) -> Optional[pd.DataFrame]:
    """Upstox candle list [[ts,o,h,l,c,v,oi], ...] → IST-indexed OHLCV DataFrame."""
    if not candles:
        return None
    try:
        rows = []
        idx = []
        for c in candles:
            idx.append(pd.to_datetime(c[0]))
            rows.append({"open": float(c[1]), "high": float(c[2]),
                         "low": float(c[3]), "close": float(c[4]),
                         "volume": float(c[5]) if len(c) > 5 else 0.0})
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
        # Upstox returns newest-first — sort ascending
        df = df.sort_index()
        if df.index.tz is None:
            df.index = df.index.tz_localize("Asia/Kolkata")
        else:
            df.index = df.index.tz_convert("Asia/Kolkata")
        return df if not df.empty else None
    except Exception as e:
        logger.debug(f"_candles_to_df: {e}")
        return None


def _upstox_ohlcv(symbol: str, client, days: int = 7) -> Optional[pd.DataFrame]:
    """
    Fetch 1-minute candles from Upstox: historical (prior days) + intraday (today),
    concatenated. Returns a 1-minute IST-indexed OHLCV DataFrame or None.
    """
    key = get_security_id(symbol)
    if not key:
        return None

    frames = []
    # Historical 1-minute for prior days
    try:
        to_date   = datetime.now(IST).strftime("%Y-%m-%d")
        from_date = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d")
        resp = client.history.get_historical_candle_data1(
            instrument_key=key, interval="1minute",
            to_date=to_date, from_date=from_date, api_version="2.0")
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
        candles = (getattr(data, "candles", None)
                   or (data.get("candles") if isinstance(data, dict) else None))
        df_hist = _candles_to_df(candles)
        if df_hist is not None:
            frames.append(df_hist)
    except Exception as e:
        logger.debug(f"hist candles {symbol}: {e}")

    # Intraday 1-minute for today
    try:
        resp = client.history.get_intra_day_candle_data(
            instrument_key=key, interval="1minute", api_version="2.0")
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
        candles = (getattr(data, "candles", None)
                   or (data.get("candles") if isinstance(data, dict) else None))
        df_intra = _candles_to_df(candles)
        if df_intra is not None:
            frames.append(df_intra)
    except Exception as e:
        logger.debug(f"intraday candles {symbol}: {e}")

    if not frames:
        return None
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df if not df.empty else None


def _resample(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
    try:
        r = df.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                   "close": "last", "volume": "sum"}).dropna()
        return r if not r.empty else None
    except Exception:
        return None


def get_ohlcv(symbol: str, interval: str = "5m", period: str = "5d") -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV candles at the requested interval via Upstox (1-min base
    resampled). Cached ~5 min. Returns IST-indexed open/high/low/close/volume.
    """
    cache_key = f"{symbol}_{interval}"
    now_mono  = _time.monotonic()
    cached    = _ohlcv_cache.get(cache_key)
    if cached and now_mono - cached["ts"] < _OHLCV_CACHE_TTL:
        return cached["df"]

    client = _upstox_client_ref
    if client is None:
        logger.debug(f"{symbol}: no Upstox client registered")
        return None

    # Daily bars: use historical 'day' interval directly
    if interval in ("1d", "day"):
        df = _upstox_daily(symbol, client)
        if df is not None and not df.empty:
            _ohlcv_cache[cache_key] = {"df": df, "ts": now_mono}
        return df

    base = _upstox_ohlcv(symbol, client)
    if base is None or base.empty:
        return None

    interval_min = int(_NSE_INTERVAL_MAP.get(interval, "5"))
    if interval_min == 1:
        df = base
    else:
        df = _resample(base, f"{interval_min}min")
    if df is not None and not df.empty:
        _ohlcv_cache[cache_key] = {"df": df, "ts": now_mono}
        return df
    return None


def _upstox_daily(symbol: str, client, days: int = 400) -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV via Upstox historical 'day' interval (for MOM12 / pairs)."""
    key = get_security_id(symbol)
    if not key:
        return None
    try:
        to_date   = datetime.now(IST).strftime("%Y-%m-%d")
        from_date = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d")
        resp = client.history.get_historical_candle_data1(
            instrument_key=key, interval="day",
            to_date=to_date, from_date=from_date, api_version="2.0")
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
        candles = (getattr(data, "candles", None)
                   or (data.get("candles") if isinstance(data, dict) else None))
        return _candles_to_df(candles)
    except Exception as e:
        logger.debug(f"daily candles {symbol}: {e}")
        return None


def get_ohlcv_multi_tf(symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
    """Fetch 1-min base once, resample to 5m/15m/1h. One network round per symbol."""
    client = _upstox_client_ref
    if client is None:
        return {"5m": None, "15m": None, "1h": None}
    base = _upstox_ohlcv(symbol, client)
    if base is None or base.empty:
        return {"5m": None, "15m": None, "1h": None}
    return {
        "5m":  _resample(base, "5min"),
        "15m": _resample(base, "15min"),
        "1h":  _resample(base, "1h"),
    }


# ── India VIX (Upstox index quote, NSE fallback) ─────────────────────────────

_vix_cache: dict = {}
_VIX_TTL = 1800.0


def get_india_vix() -> float:
    """Fetch India VIX via Upstox index LTP. Cached 30 min. 0.0 on failure."""
    now = _time.monotonic()
    if _vix_cache.get("ts", 0) > now - _VIX_TTL:
        return _vix_cache.get("vix", 0.0)

    client = _upstox_client_ref
    if client is not None:
        try:
            resp = client.market_quote.ltp(instrument_key=_VIX_KEY, api_version="2.0")
            data = _quote_response_data(resp)
            for _k, entry in data.items():
                vix = float(getattr(entry, "last_price", None)
                            or (entry.get("last_price") if isinstance(entry, dict) else 0) or 0)
                if vix > 0:
                    _vix_cache["vix"] = vix
                    _vix_cache["ts"] = now
                    return vix
        except Exception as e:
            logger.debug(f"get_india_vix (upstox): {e}")

    return _vix_cache.get("vix", 0.0)


# ── Nifty 50 level & intraday (Upstox index) ─────────────────────────────────

def get_nifty_intraday(interval: str = "5m") -> Optional[pd.DataFrame]:
    """Fetch Nifty 50 intraday candles via Upstox index instrument."""
    client = _upstox_client_ref
    if client is None:
        return None
    try:
        resp = client.history.get_intra_day_candle_data(
            instrument_key=_NIFTY_KEY, interval="1minute", api_version="2.0")
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
        candles = (getattr(data, "candles", None)
                   or (data.get("candles") if isinstance(data, dict) else None))
        base = _candles_to_df(candles)
        if base is None:
            return None
        interval_min = int(_NSE_INTERVAL_MAP.get(interval, "5"))
        return base if interval_min == 1 else _resample(base, f"{interval_min}min")
    except Exception as e:
        logger.debug(f"get_nifty_intraday: {e}")
        return None


_nifty_cache: dict = {}
_NIFTY_TTL = 120.0


def get_nifty_level() -> dict:
    """Return Nifty50 snapshot {level, open, change_pct}. Zeros on failure."""
    now = _time.monotonic()
    if _nifty_cache.get("ts", 0) > now - _NIFTY_TTL:
        return _nifty_cache.get("data", {"level": 0.0, "open": 0.0, "change_pct": 0.0})

    client = _upstox_client_ref
    if client is not None:
        try:
            resp = client.market_quote.get_full_market_quote(
                instrument_key=_NIFTY_KEY, api_version="2.0")
            data = _quote_response_data(resp)
            for _k, entry in data.items():
                def _g(name, default=0.0):
                    return (getattr(entry, name, None)
                            or (entry.get(name) if isinstance(entry, dict) else None) or default)
                ohlc = _g("ohlc", {})
                o = float((ohlc.get("open") if isinstance(ohlc, dict) else getattr(ohlc, "open", 0)) or 0)
                last = float(_g("last_price", 0) or 0)
                chg = ((last - o) / o * 100) if o > 0 else 0.0
                snap = {"level": last, "open": o, "change_pct": round(chg, 2)}
                _nifty_cache["data"] = snap
                _nifty_cache["ts"] = now
                return snap
        except Exception as e:
            logger.debug(f"get_nifty_level: {e}")

    return _nifty_cache.get("data", {"level": 0.0, "open": 0.0, "change_pct": 0.0})


# ── Sector index returns (Upstox index quotes) ───────────────────────────────

_SECTOR_INDEX_KEYS = {
    "Banking":  "NSE_INDEX|Nifty Bank",
    "Auto":     "NSE_INDEX|Nifty Auto",
    "IT":       "NSE_INDEX|Nifty IT",
    "Pharma":   "NSE_INDEX|Nifty Pharma",
    "FMCG":     "NSE_INDEX|Nifty FMCG",
    "Energy":   "NSE_INDEX|Nifty Energy",
    "Metal":    "NSE_INDEX|Nifty Metal",
    "Realty":   "NSE_INDEX|Nifty Realty",
    "PSU Bank": "NSE_INDEX|Nifty PSU Bank",
    "Infra":    "NSE_INDEX|Nifty Infra",
}

_sector_cache: dict = {}
_SECTOR_TTL = 120.0


def get_all_sector_returns() -> Dict[str, float]:
    """Return {sector: daily_change_pct relative to Nifty} via Upstox index quotes."""
    now = _time.monotonic()
    if _sector_cache.get("ts", 0) > now - _SECTOR_TTL:
        return _sector_cache.get("data", {})

    client = _upstox_client_ref
    if client is None:
        return {}

    nifty_pct = get_nifty_level().get("change_pct", 0.0)
    out: Dict[str, float] = {}
    try:
        keys = ",".join(_SECTOR_INDEX_KEYS.values())
        resp = client.market_quote.get_full_market_quote(
            instrument_key=keys, api_version="2.0")
        data = _quote_response_data(resp)
        # Build reverse lookup from instrument_key tail → sector
        keytail_to_sector = {v.split("|")[-1].upper(): s for s, v in _SECTOR_INDEX_KEYS.items()}
        for resp_key, entry in data.items():
            def _g(name, default=0.0):
                return (getattr(entry, name, None)
                        or (entry.get(name) if isinstance(entry, dict) else None) or default)
            ohlc = _g("ohlc", {})
            o = float((ohlc.get("open") if isinstance(ohlc, dict) else getattr(ohlc, "open", 0)) or 0)
            last = float(_g("last_price", 0) or 0)
            if o <= 0:
                continue
            chg = (last - o) / o * 100
            tail = str(resp_key).split(":")[-1].upper()
            sector = keytail_to_sector.get(tail)
            if sector is None:
                # Match by name fragment
                for s, key in _SECTOR_INDEX_KEYS.items():
                    if key.split("|")[-1].upper().replace("NIFTY ", "") in tail:
                        sector = s
                        break
            if sector:
                out[sector] = round(chg - nifty_pct, 2)
        if out:
            _sector_cache["data"] = out
            _sector_cache["ts"] = now
    except Exception as e:
        logger.debug(f"get_all_sector_returns: {e}")
    return out or _sector_cache.get("data", {})


# ── Market breadth (fraction of sector indices advancing) ────────────────────

_breadth_cache: dict = {}
_BREADTH_TTL = 120.0


def get_market_breadth() -> float:
    """
    Fraction (0-1) of NSE sector indices that are advancing today, with a
    Nifty directional nudge. Returns 0.5 (neutral) on failure.
    """
    now = _time.monotonic()
    if _breadth_cache.get("ts", 0) > now - _BREADTH_TTL:
        return _breadth_cache.get("val", 0.5)

    client = _upstox_client_ref
    if client is None:
        return 0.5
    try:
        keys = ",".join(_SECTOR_INDEX_KEYS.values())
        resp = client.market_quote.get_full_market_quote(
            instrument_key=keys, api_version="2.0")
        data = _quote_response_data(resp)
        up = total = 0
        for _k, entry in data.items():
            def _g(name, default=0.0):
                return (getattr(entry, name, None)
                        or (entry.get(name) if isinstance(entry, dict) else None) or default)
            ohlc = _g("ohlc", {})
            o = float((ohlc.get("open") if isinstance(ohlc, dict) else getattr(ohlc, "open", 0)) or 0)
            last = float(_g("last_price", 0) or 0)
            if o <= 0:
                continue
            total += 1
            if (last - o) > 0:
                up += 1
        if total == 0:
            return 0.5
        breadth = up / total
        nifty_chg = get_nifty_level().get("change_pct", 0.0)
        if nifty_chg > 1.5:
            breadth = min(1.0, breadth + 0.1)
        elif nifty_chg < -1.5:
            breadth = max(0.0, breadth - 0.1)
        _breadth_cache["val"] = breadth
        _breadth_cache["ts"] = now
        return breadth
    except Exception as e:
        logger.debug(f"get_market_breadth: {e}")
    return _breadth_cache.get("val", 0.5)
