"""
sector_confluence_india.py — Multi-symbol sector confluence confirmation
Uses cached signals from current scan cycle — no extra API calls.
+12 if 2 peers align, +18 if 3+ peers align, -8 if peer opposes.
"""
import logging
import time as _time
from typing import Dict, Optional

logger = logging.getLogger("sector_confluence_india")

_CACHE_TTL = 300.0  # 5 min — matches scan cycle


class SectorConfluence:
    def __init__(self):
        self._cache: Dict[str, dict] = {}

    def update_signal(self, symbol: str, direction: str, score: float):
        self._cache[symbol.upper()] = {
            "direction": direction,
            "score":     score,
            "ts":        _time.monotonic(),
        }

    def get_confluence_score(self, symbol: str, direction: str,
                             sector_map: Dict[str, str]) -> int:
        sym = symbol.upper()
        now = _time.monotonic()
        my_sector = sector_map.get(sym)
        if not my_sector:
            return 0

        # Find same-sector peers (exclude self)
        peers = [
            s for s, sec in sector_map.items()
            if sec == my_sector and s != sym
        ]

        align_count  = 0
        oppose_count = 0

        for peer in peers:
            entry = self._cache.get(peer)
            if not entry:
                continue
            if now - entry["ts"] > _CACHE_TTL:
                continue
            if entry["score"] < 60:
                continue
            if entry["direction"] == direction:
                align_count += 1
            else:
                oppose_count += 1

        if oppose_count >= 1:
            return -8
        if align_count >= 3:
            return 18
        if align_count >= 2:
            return 12
        return 0

    def get_sector_momentum(self, sector: str, sector_map: Dict[str, str]) -> str:
        now = _time.monotonic()
        long_cnt = short_cnt = 0
        for sym, sec in sector_map.items():
            if sec != sector:
                continue
            entry = self._cache.get(sym)
            if not entry or now - entry["ts"] > _CACHE_TTL:
                continue
            if entry["direction"] == "LONG":
                long_cnt += 1
            else:
                short_cnt += 1
        if long_cnt > short_cnt:
            return "BULLISH"
        if short_cnt > long_cnt:
            return "BEARISH"
        return "NEUTRAL"
