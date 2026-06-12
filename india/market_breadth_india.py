"""
market_breadth_india.py — NSE Market Breadth Gate
Every 15 min, computes % of sector indices advancing.
Blocks LONG when < 30%, blocks SHORT when > 70%.
"""
import logging
import time as _time
from typing import Optional

logger = logging.getLogger("market_breadth_india")

_LONG_BLOCK_THRESHOLD  = 0.30  # < 30% advancing → block LONGs
_SHORT_BLOCK_THRESHOLD = 0.70  # > 70% advancing → block SHORTs
_UPDATE_INTERVAL       = 900.0  # 15 minutes


class MarketBreadth:
    def __init__(self):
        self._breadth:     float = 0.5
        self._last_update: float = 0.0

    def update(self) -> float:
        now = _time.monotonic()
        if now - self._last_update < _UPDATE_INTERVAL:
            return self._breadth
        self._last_update = now
        try:
            breadth = self._fetch_breadth()
            self._breadth = float(max(0.0, min(1.0, breadth)))
        except Exception as e:
            logger.debug(f"MarketBreadth.update: {e}")
        return self._breadth

    def _fetch_breadth(self) -> float:
        """Fetch NSE allIndices and count % of equity indices advancing."""
        from data_fetch_dhan import _get_nse_session, _NSE_MAIN
        import requests as _req

        sess = _get_nse_session()
        r = sess.get(
            f"{_NSE_MAIN}/api/allIndices",
            headers={"Referer": f"{_NSE_MAIN}/"},
            timeout=10,
        )
        data = r.json().get("data", [])

        # Focus on equity sector indices only
        _EQUITY_KEYWORDS = (
            "NIFTY", "BANK", "IT", "AUTO", "PHARMA", "FMCG",
            "METAL", "ENERGY", "INFRA", "REALTY", "MEDIA",
            "FINANCE", "CONSUMER",
        )
        equity = [
            d for d in data
            if any(kw in d.get("index", "").upper() for kw in _EQUITY_KEYWORDS)
        ]

        if not equity:
            return 0.5

        up   = sum(1 for d in equity if float(d.get("percentChange", 0) or 0) > 0)
        total = len(equity)

        breadth = up / total if total > 0 else 0.5

        # Nifty 50 directional adjustment
        nifty50 = next((d for d in data if d.get("index") == "NIFTY 50"), None)
        if nifty50:
            nifty_chg = float(nifty50.get("percentChange", 0) or 0)
            if nifty_chg > 1.5:
                breadth = min(1.0, breadth + 0.1)
            elif nifty_chg < -1.5:
                breadth = max(0.0, breadth - 0.1)

        return breadth

    def allows_long(self) -> bool:
        return self._breadth >= _LONG_BLOCK_THRESHOLD

    def allows_short(self) -> bool:
        return self._breadth <= _SHORT_BLOCK_THRESHOLD

    def get_regime(self) -> str:
        if self._breadth > 0.65:
            return "BULL"
        if self._breadth < 0.35:
            return "BEAR"
        return "NEUTRAL"

    def get_status(self) -> dict:
        return {
            "breadth_pct":    round(self._breadth, 3),
            "regime":         self.get_regime(),
            "longs_allowed":  self.allows_long(),
            "shorts_allowed": self.allows_short(),
        }
