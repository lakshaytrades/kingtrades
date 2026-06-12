"""
adaptive_threshold_india.py — Self-adjusting signal score threshold
Tracks rolling 15-trade win rate; raises threshold when signals underperform.
"""
import logging
from datetime import datetime, time
from typing import List
from zoneinfo import ZoneInfo

logger = logging.getLogger("adaptive_threshold_india")
IST = ZoneInfo("Asia/Kolkata")

_MIDDAY_START = time(11, 30)
_MIDDAY_END   = time(13, 30)


class AdaptiveThreshold:
    """
    WR < 40%: base + 8   (very selective)
    WR 40-55%: base + 4
    WR 55-65%: base      (default)
    WR > 65%: base - 2   (relax)
    Floor=63, Ceiling=82. Midday adds +5.
    """
    FLOOR   = 63.0
    CEILING = 82.0

    def __init__(self, base_threshold: float = 67.0):
        self.base = base_threshold
        self._results: List[bool] = []

    def record_trade(self, win: bool):
        self._results.append(bool(win))
        if len(self._results) > 15:
            self._results = self._results[-15:]

    def get_rolling_wr(self) -> float:
        if len(self._results) < 5:
            return 0.5
        wins = sum(1 for r in self._results if r)
        return wins / len(self._results)

    def get_threshold(self, now_ist: datetime) -> float:
        wr  = self.get_rolling_wr()
        adj = 0.0

        if wr < 0.40:
            adj = 8.0
        elif wr < 0.55:
            adj = 4.0
        elif wr <= 0.65:
            adj = 0.0
        else:
            adj = -2.0

        # Midday chop: extra +5
        t = now_ist.time()
        if _MIDDAY_START <= t < _MIDDAY_END:
            adj += 5.0

        thresh = self.base + adj
        return float(max(self.FLOOR, min(self.CEILING, thresh)))

    def get_status(self) -> dict:
        wr = self.get_rolling_wr()
        threshold = self.get_threshold(datetime.now(IST))
        adj = threshold - self.base
        return {
            "threshold":   round(threshold, 1),
            "rolling_wr":  round(wr, 3),
            "trade_count": len(self._results),
            "adjustment":  f"{adj:+.0f}",
        }
