"""
congressional_alpha.py — Congressional stock trading signal.
US politicians trade on non-public legislative information.
Studies show congressional portfolios beat market by 6-12% annually.
Free data from Quiver Quantitative public API (no key for basic data).
Cached 6 hours. Fail-open 0.0.
"""
import logging
import time
import json
import urllib.request
from typing import Tuple

logger = logging.getLogger(__name__)
_cache: dict = {}
_TTL = 21600.0  # 6 hours

def get_congressional_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Returns (score_delta, reason). Fail-open returns (0.0, 'congress_unavailable').
    Checks if Congress members bought this stock in the last 90 days.
    """
    try:
        now = time.time()
        if symbol in _cache and now - _cache[symbol][1] < _TTL:
            return _cache[symbol][0]

        # Quiver Quantitative free API
        url = f"https://api.quiverquant.com/beta/live/congresstrading/{symbol}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        if not data:
            result = (0.0, "no_congress_data")
            _cache[symbol] = (result, now)
            return result

        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=90)
        recent_buys = 0
        recent_sells = 0

        for trade in data[:20]:
            try:
                trade_date = datetime.strptime(trade.get("Date", ""), "%Y-%m-%d")
                if trade_date < cutoff:
                    continue
                tx = (trade.get("Transaction") or "").lower()
                if "purchase" in tx or "buy" in tx:
                    recent_buys += 1
                elif "sale" in tx or "sell" in tx:
                    recent_sells += 1
            except Exception:
                continue

        if recent_buys >= 3:
            score = 15.0
            reason = f"congress_cluster_buy({recent_buys})"
        elif recent_buys >= 1:
            score = 8.0
            reason = f"congress_buy({recent_buys})"
        elif recent_sells >= 2:
            score = -6.0
            reason = f"congress_sell({recent_sells})"
        else:
            score = 0.0
            reason = "no_recent_congress_trade"

        if direction == "SHORT" and score > 0:
            score = -score

        result = (float(score), reason)
        _cache[symbol] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"congressional_alpha fail-open {symbol}: {e}")
        return (0.0, "congress_unavailable")
