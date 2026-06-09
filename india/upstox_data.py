"""
upstox_data.py — FREE real-time NSE data via Upstox API (v3 candles + v2 LTP).

No IP whitelisting (unlike Dhan). Activated when UPSTOX_ACCESS_TOKEN is set in
.env. Wired as a high-priority source in data_fetch_dhan (before Yahoo), with
automatic fallback to Yahoo if anything fails — so the bot never breaks.

Get a token (valid until ~3:30 AM next day):
  1. https://account.upstox.com/developer/apps  → create an app
  2. Generate an access token (login flow) — see auth_upstox notes in README
  3. Put it in .env:  UPSTOX_ACCESS_TOKEN=your_token
"""
import gzip
import json
import logging
import time as _time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger("upstox_data")

_BASE        = Path(__file__).parent.parent
_INSTR_CACHE = _BASE / "logs" / "upstox_instruments.json"
_INSTR_URL   = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_API         = "https://api.upstox.com"

_sym_to_key: Dict[str, str] = {}


def _token() -> str:
    try:
        import config_india as c
        return getattr(c, "UPSTOX_ACCESS_TOKEN", "") or ""
    except Exception:
        import os
        return os.getenv("UPSTOX_ACCESS_TOKEN", "")


def enabled() -> bool:
    return bool(_token())


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}


def _load_instruments(force: bool = False) -> None:
    """Build {TRADING_SYMBOL: instrument_key} for NSE equity. Cached to disk weekly."""
    global _sym_to_key
    if _sym_to_key and not force:
        return
    # disk cache (refresh weekly)
    try:
        if _INSTR_CACHE.exists() and _time.time() - _INSTR_CACHE.stat().st_mtime < 7 * 86400:
            _sym_to_key = json.loads(_INSTR_CACHE.read_text())
            if _sym_to_key:
                return
    except Exception:
        pass
    # download fresh instrument master
    try:
        import requests
        r = requests.get(_INSTR_URL, timeout=30)
        arr = json.loads(gzip.decompress(r.content))
        m: Dict[str, str] = {}
        for it in arr:
            seg = it.get("segment") or ""
            itype = (it.get("instrument_type") or "").upper()
            if seg == "NSE_EQ" and itype == "EQ":
                ts  = (it.get("trading_symbol") or it.get("name") or "").upper()
                key = it.get("instrument_key")
                if ts and key:
                    m[ts] = key
        if m:
            _sym_to_key = m
            try:
                _INSTR_CACHE.parent.mkdir(exist_ok=True)
                _INSTR_CACHE.write_text(json.dumps(m))
            except Exception:
                pass
            logger.info(f"Upstox instruments loaded: {len(m)} NSE equity symbols")
    except Exception as e:
        logger.warning(f"Upstox instrument load failed: {e}")


def _key(symbol: str) -> Optional[str]:
    _load_instruments()
    # Upstox uses '-' style symbols (e.g. M&M -> M&M, BAJAJ-AUTO). Try direct.
    return _sym_to_key.get(symbol.upper())


def get_ohlcv(symbol: str, interval_min: int = 5) -> Optional[pd.DataFrame]:
    """Real-time intraday OHLCV via Upstox v3. Returns IST-indexed DataFrame or None."""
    if not _token():
        return None
    key = _key(symbol)
    if not key:
        return None
    try:
        import requests
        url = f"{_API}/v3/historical-candle/intraday/{key}/minutes/{int(interval_min)}"
        r = requests.get(url, headers=_headers(), timeout=12)
        if r.status_code != 200:
            logger.debug(f"upstox ohlcv {symbol}: HTTP {r.status_code}")
            return None
        candles = r.json().get("data", {}).get("candles", [])
        if not candles:
            return None
        rows = list(reversed(candles))   # Upstox returns newest-first
        idx = pd.to_datetime([c[0] for c in rows], utc=True).tz_convert("Asia/Kolkata")
        df = pd.DataFrame({
            "open":   [c[1] for c in rows],
            "high":   [c[2] for c in rows],
            "low":    [c[3] for c in rows],
            "close":  [c[4] for c in rows],
            "volume": [c[5] for c in rows],
        }, index=idx).dropna()
        return df if not df.empty else None
    except Exception as e:
        logger.debug(f"upstox ohlcv {symbol}: {e}")
        return None


def get_ltp(symbols: List[str]) -> Dict[str, float]:
    """Real-time LTP for symbols via Upstox v2 market-quote. Returns {symbol: ltp}."""
    if not _token():
        return {}
    key_to_sym: Dict[str, str] = {}
    for s in symbols:
        k = _key(s)
        if k:
            key_to_sym[k] = s
    if not key_to_sym:
        return {}
    out: Dict[str, float] = {}
    try:
        import requests
        keys = list(key_to_sym.keys())
        for i in range(0, len(keys), 100):   # batch
            batch = keys[i:i+100]
            r = requests.get(f"{_API}/v2/market-quote/ltp", headers=_headers(),
                             params={"instrument_key": ",".join(batch)}, timeout=12)
            if r.status_code != 200:
                continue
            for _resp_key, v in r.json().get("data", {}).items():
                ik  = v.get("instrument_token")        # equals our instrument_key
                sym = key_to_sym.get(ik)
                if sym is None:                        # fallback: match by symbol suffix
                    ts = _resp_key.split(":")[-1].upper()
                    sym = next((s for s in key_to_sym.values() if s.upper() == ts), None)
                px = float(v.get("last_price", 0) or 0)
                if sym and px > 0:
                    out[sym] = px
    except Exception as e:
        logger.debug(f"upstox ltp: {e}")
    return out
