"""
data_fetch_dhan.py — Market data for Indian NSE market
Primary OHLCV:   yfinance with .NS suffix (free, reliable)
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

_SCRIP_MASTER_URL  = "https://images.dhan.co/api-data/api-scrip-master.csv"
_SCRIP_CACHE_PATH  = Path(__file__).parent.parent / "data" / "dhan_scrip_master.csv"
_SCRIP_CACHE_TTL   = 86400  # refresh daily

_quote_cache: Dict[str, dict] = {}
_quote_cache_ts: Dict[str, float] = {}
_QUOTE_CACHE_TTL = 30.0   # 30-second quote cache

_symbol_map: Dict[str, str] = {}   # symbol → security_id
_symbol_map_loaded = False


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


# ── Historical OHLCV from yfinance ───────────────────────────────────────────

def get_ohlcv(symbol: str, interval: str = "5m", period: str = "5d") -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV bars from yfinance using NSE .NS suffix.
    Returns DataFrame with lowercase columns: open, high, low, close, volume
    Indexed by timezone-aware datetime (IST).
    Returns None on failure (fail-open).
    """
    try:
        import yfinance as yf
        ticker = f"{symbol.upper()}.NS"
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        if df.empty:
            return None
        # Convert index to IST
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("Asia/Kolkata")
        else:
            df.index = df.index.tz_convert("Asia/Kolkata")
        return df
    except Exception as e:
        logger.debug(f"yfinance {symbol}.NS failed: {e}")
        return None


def get_ohlcv_multi_tf(symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
    """Fetch 5m, 15m, and 1h data for multi-timeframe analysis."""
    return {
        "5m":  get_ohlcv(symbol, interval="5m",  period="5d"),
        "15m": get_ohlcv(symbol, interval="15m", period="10d"),
        "1h":  get_ohlcv(symbol, interval="1h",  period="30d"),
    }


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
