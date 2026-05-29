"""
pattern_analytics.py — Track per-pattern win rates and adapt scoring.
Records every trade outcome by pattern name, time-of-day, and market regime.
Used by signal_generator to weight pattern scores by proven performance.
"""
import json
import logging
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)
ANALYTICS_FILE = Path("data/pattern_analytics.json")


class PatternAnalytics:
    """Rolling pattern performance tracker. Records win/loss per pattern."""

    def __init__(self):
        self._data: Dict[str, Dict] = {}  # {pattern: {wins, losses, total_pnl}}
        self._load()

    def record(self, pattern: str, win: bool, pnl: float = 0.0,
               time_bucket: str = "", regime: str = "") -> None:
        key = f"{pattern}|{time_bucket}|{regime}" if time_bucket else pattern
        if key not in self._data:
            self._data[key] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
        self._data[key]["wins" if win else "losses"] += 1
        self._data[key]["total_pnl"] = round(self._data[key]["total_pnl"] + pnl, 2)
        self._save()

    def win_rate(self, pattern: str, time_bucket: str = "", regime: str = "") -> Tuple[float, int]:
        key = f"{pattern}|{time_bucket}|{regime}" if time_bucket else pattern
        d = self._data.get(key, self._data.get(pattern, {}))
        w, l = d.get("wins", 0), d.get("losses", 0)
        n = w + l
        return (w / n, n) if n >= 5 else (0.60, 0)

    def confidence_multiplier(self, pattern: str, time_bucket: str = "", regime: str = "") -> float:
        """Return 0.7-1.3x multiplier based on pattern's historical WR."""
        wr, n = self.win_rate(pattern, time_bucket, regime)
        if n < 5:
            return 1.0  # no data — neutral
        if wr >= 0.75:
            return 1.30   # proven: boost confidence
        if wr >= 0.65:
            return 1.15   # above average
        if wr >= 0.55:
            return 1.00   # baseline
        if wr >= 0.45:
            return 0.85   # below average
        return 0.70       # poor history: reduce confidence

    def top_patterns(self, n: int = 10) -> list:
        """Return top N patterns by win rate (min 5 trades)."""
        results = []
        for key, d in self._data.items():
            w, l = d.get("wins", 0), d.get("losses", 0)
            total = w + l
            if total >= 5:
                results.append({
                    "pattern": key,
                    "wr": round(w / total * 100, 1),
                    "trades": total,
                    "pnl": d.get("total_pnl", 0),
                })
        return sorted(results, key=lambda x: (-x["wr"], -x["trades"]))[:n]

    def _load(self):
        try:
            if ANALYTICS_FILE.exists():
                self._data = json.loads(ANALYTICS_FILE.read_text())
        except Exception as e:
            logger.debug(f"[PATTERN_ANALYTICS] load failed: {e}")

    def _save(self):
        try:
            ANALYTICS_FILE.parent.mkdir(exist_ok=True)
            ANALYTICS_FILE.write_text(json.dumps(self._data, indent=2))
        except Exception as e:
            logger.debug(f"[PATTERN_ANALYTICS] save failed: {e}")
