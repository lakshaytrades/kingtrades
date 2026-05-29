"""
insider_flow.py — SEC Form 4 Insider Transaction Tracker

Insider buying = the most honest signal in the market.
When a CFO spends $200k of personal money buying stock, that's conviction.
All US public company insiders must report within 2 business days via Form 4.

Data: SEC EDGAR free public API (no key required)
Cache: 24 hours per symbol (Form 4 updates 1-2x/day)
Score range: -5 to +10
"""
import logging
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)
_insider_cache: Dict = {}
_INS_TTL = 86400.0   # 24 hours


def get_insider_score(symbol: str) -> Tuple[float, str]:
    """Returns (score_delta, reason). Cached 24h. Fail-open."""
    now = _time.monotonic()
    cached = _insider_cache.get(symbol)
    if cached and now - cached["ts"] < _INS_TTL:
        return _score(cached["data"])

    data = _fetch(symbol)
    if data is not None:
        _insider_cache[symbol] = {"data": data, "ts": now}
        return _score(data)
    return 0.0, ""


def _fetch(symbol: str) -> Optional[Dict]:
    try:
        import requests
        from datetime import datetime, timedelta
        start  = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        end    = datetime.now().strftime("%Y-%m-%d")
        url    = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q":         f'"{symbol}"',
            "forms":     "4",
            "dateRange": "custom",
            "startdt":   start,
            "enddt":     end,
        }
        headers = {"User-Agent": "KingTrades/1.0 contact@kingtrades.io"}
        r = requests.get(url, params=params, timeout=8, headers=headers)
        if r.status_code != 200:
            return None

        hits = r.json().get("hits", {}).get("hits", [])
        buys = sells = 0
        for h in hits[:25]:
            txt = str(h.get("_source", {})).lower()
            if any(w in txt for w in ["purchase", "acquisition", "p -"]):
                buys += 1
            elif any(w in txt for w in ["sale", "disposed", "s -"]):
                sells += 1

        return {"buys": buys, "sells": sells}
    except Exception as e:
        logger.debug(f"insider_flow({symbol}): {e}")
        return None


def _score(data: Dict) -> Tuple[float, str]:
    b, s = data.get("buys", 0), data.get("sells", 0)
    if b == 0 and s == 0:
        return 0.0, ""
    score, reasons = 0.0, []
    if b >= 3:   score += 10; reasons.append(f"INSIDER_CLUSTER({b}buys+10)")
    elif b >= 1: score += 6;  reasons.append(f"INSIDER_BUY({b}+6)")
    if s >= 3:   score -= 5;  reasons.append(f"INSIDER_SELL_CLUSTER({s}-5)")
    elif s >= 2: score -= 3
    return round(score, 1), " | ".join(reasons)
