"""
futures_oi_india.py — NSE Futures OI Intelligence.
Fetches futures OI via NSE quote-derivative API.
Scores: OI buildup in trend direction +8, divergence −5, short buildup +6.
Cache 10 min. Fail-open (returns 0, "").
"""
import logging
import time as _time
from typing import Optional

import requests

logger = logging.getLogger("futures_oi_india")

_CACHE: dict[str, tuple[float, dict]] = {}   # symbol → (ts, data)
_CACHE_TTL = 600.0   # 10 min

_NSE_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
_TIMEOUT = 8


def get_futures_oi(symbol: str) -> dict:
    """
    Returns:
        {"oi": int, "oi_chg": int, "price_chg_pct": float,
         "signal": str, "score_adj": int}
    Fail-open: returns {"oi":0,"oi_chg":0,"price_chg_pct":0.0,"signal":"","score_adj":0}
    """
    _empty = {"oi": 0, "oi_chg": 0, "price_chg_pct": 0.0, "signal": "", "score_adj": 0}
    try:
        cached = _get_cache(symbol)
        if cached:
            return cached

        sess = requests.Session()
        sess.get("https://www.nseindia.com/", headers=_NSE_HDR, timeout=5)
        url = f"https://www.nseindia.com/api/quote-derivative?symbol={symbol.upper()}"
        resp = sess.get(url, headers=_NSE_HDR, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return _empty

        data = resp.json()
        fut_data = _extract_near_futures(data)
        if not fut_data:
            return _empty

        oi        = int(fut_data.get("openInterest", 0))
        oi_chg    = int(fut_data.get("changeinOpenInterest", 0))
        price_chg = float(fut_data.get("pChange", 0.0))

        signal, score_adj = _interpret(oi_chg, price_chg)

        result = {
            "oi": oi,
            "oi_chg": oi_chg,
            "price_chg_pct": round(price_chg, 3),
            "signal": signal,
            "score_adj": score_adj,
        }
        _set_cache(symbol, result)
        return result

    except Exception as e:
        logger.debug("get_futures_oi %s: %s", symbol, e)
        return _empty


def get_futures_oi_score(symbol: str, direction: str) -> int:
    """Convenience: returns directional score adjustment (+ve / −ve)."""
    d = get_futures_oi(symbol)
    score_adj = d.get("score_adj", 0)
    signal    = d.get("signal", "")

    if direction == "LONG":
        if signal == "OI_BUILDUP_BULL":
            return score_adj       # +8
        if signal == "OI_DIVERGENCE_BULL":
            return score_adj       # −5
    elif direction == "SHORT":
        if signal == "OI_BUILDUP_BEAR":
            return score_adj       # +6
        if signal == "OI_DIVERGENCE_BEAR":
            return score_adj       # −5
    return 0


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _extract_near_futures(data: dict) -> Optional[dict]:
    """Pull the near-month futures row from the quote-derivative response."""
    try:
        stocks = data.get("stocks", [])
        for item in stocks:
            meta = item.get("metadata", {})
            if meta.get("instrumentType", "").upper() == "FUTSTK":
                return item.get("marketDeptOrderBook", {}).get("tradeInfo", meta)
        # fallback: first FUTIDX
        for item in stocks:
            meta = item.get("metadata", {})
            if "FUT" in meta.get("instrumentType", "").upper():
                return meta
    except Exception:
        pass
    return None


def _interpret(oi_chg: int, price_chg: float) -> tuple[str, int]:
    if oi_chg > 0 and price_chg > 0:
        return "OI_BUILDUP_BULL", 8      # longs piling in — strong trend
    if oi_chg > 0 and price_chg < 0:
        return "OI_BUILDUP_BEAR", 6      # shorts piling in — strong bear
    if oi_chg < 0 and price_chg > 0:
        return "OI_DIVERGENCE_BULL", -5  # price up but OI down — weak
    if oi_chg < 0 and price_chg < 0:
        return "OI_DIVERGENCE_BEAR", -5  # price down but OI down — weak
    return "", 0


def _get_cache(symbol: str) -> Optional[dict]:
    entry = _CACHE.get(symbol.upper())
    if entry and (_time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _set_cache(symbol: str, data: dict) -> None:
    _CACHE[symbol.upper()] = (_time.monotonic(), data)
