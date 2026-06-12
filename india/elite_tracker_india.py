"""
elite_tracker_india.py — Self-Learning Elite Signal Tracker

Renaissance-style feedback loop: track which specific signal combinations
(pattern sets) actually win vs lose, and dynamically adjust their weights.

After each completed trade, records:
  - Which signals fired (pattern list from IndiaTradeSignal.patterns)
  - Which boosters contributed (score rationale)
  - Win (1) or Loss (0)
  - Score at entry
  - Grade

After 15+ samples per pattern combination, adjusts the score weight.
High-WR patterns get +5 to +10 bonus in future signals.
Low-WR patterns get -3 to -6 penalty.

Storage: data/elite_tracker.json  (persists across sessions)
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("elite_tracker")
IST = ZoneInfo("Asia/Kolkata")

_TRACKER_FILE = Path(__file__).parent.parent / "data" / "elite_tracker.json"
_MIN_SAMPLES = 15    # minimum trades before adjusting weights
_NEUTRAL_WR  = 0.55  # baseline WR — no adjustment below this sample count

# {pattern_key: {"wins": int, "losses": int, "last_seen": str}}
_pattern_stats: Dict[str, dict] = {}
_loaded = False


def _load():
    global _pattern_stats, _loaded
    if _loaded:
        return
    try:
        if _TRACKER_FILE.exists():
            _pattern_stats = json.loads(_TRACKER_FILE.read_text())
    except Exception as e:
        logger.debug(f"elite_tracker load: {e}")
    _loaded = True


def _save():
    try:
        _TRACKER_FILE.parent.mkdir(exist_ok=True)
        _TRACKER_FILE.write_text(json.dumps(_pattern_stats, indent=2))
    except Exception as e:
        logger.debug(f"elite_tracker save: {e}")


def _pattern_key(patterns: List[str], direction: str) -> str:
    """Create a canonical key from a set of patterns + direction."""
    sorted_p = sorted(set(p.upper() for p in patterns if p))
    # Use top 3 patterns for the key to avoid combinatorial explosion
    top3 = sorted_p[:3]
    return f"{direction}|{'_'.join(top3)}" if top3 else f"{direction}|GENERIC"


def record_trade_outcome(patterns: List[str], direction: str,
                          signal_score: float, grade: str, win: bool):
    """Record the outcome of a completed trade for learning."""
    _load()
    key = _pattern_key(patterns, direction)
    if key not in _pattern_stats:
        _pattern_stats[key] = {"wins": 0, "losses": 0, "last_seen": ""}

    if win:
        _pattern_stats[key]["wins"] += 1
    else:
        _pattern_stats[key]["losses"] += 1
    _pattern_stats[key]["last_seen"] = datetime.now(IST).strftime("%Y-%m-%d")
    _save()
    logger.info(f"Elite tracker: {key} {'WIN' if win else 'LOSS'} "
                f"(W:{_pattern_stats[key]['wins']} L:{_pattern_stats[key]['losses']})")


def get_elite_score(patterns: List[str], direction: str,
                    signal_score: float) -> Tuple[int, str]:
    """
    Return (score_adj, reason) based on historical performance of this pattern set.
    Returns (0, "") if insufficient data.
    """
    _load()
    try:
        key = _pattern_key(patterns, direction)
        stats = _pattern_stats.get(key)
        if stats is None:
            return (0, "")

        total = stats["wins"] + stats["losses"]
        if total < _MIN_SAMPLES:
            return (0, "")  # not enough data yet

        wr = stats["wins"] / total
        # Wilson confidence interval lower bound (95%)
        # More conservative with small samples
        z = 1.96
        p_hat = wr
        denominator = 1 + z**2 / total
        centre = (p_hat + z**2 / (2 * total)) / denominator
        margin = (z * math.sqrt(p_hat * (1 - p_hat) / total + z**2 / (4 * total**2))) / denominator
        wilson_lower = centre - margin

        # Score adjustment based on Wilson lower bound vs neutral WR
        delta = wilson_lower - _NEUTRAL_WR

        if delta > 0.15:    # significantly above average
            adj = 10
            reason = f"ELITE_WIN_PATTERN WR{wr*100:.0f}%n{total}"
        elif delta > 0.08:
            adj = 7
            reason = f"ELITE_STRONG WR{wr*100:.0f}%n{total}"
        elif delta > 0.03:
            adj = 4
            reason = f"ELITE_POSITIVE WR{wr*100:.0f}%n{total}"
        elif delta < -0.15:  # significantly below average
            adj = -8
            reason = f"ELITE_LOW_WR WR{wr*100:.0f}%n{total}"
        elif delta < -0.08:
            adj = -5
            reason = f"ELITE_WEAK WR{wr*100:.0f}%n{total}"
        elif delta < -0.03:
            adj = -3
            reason = f"ELITE_BELOW_AVG WR{wr*100:.0f}%n{total}"
        else:
            return (0, "")

        return (adj, reason)

    except Exception as e:
        logger.debug(f"get_elite_score: {e}")
        return (0, "")


def get_stats_summary() -> str:
    """Return a brief summary of the tracked pattern performance."""
    _load()
    if not _pattern_stats:
        return "No pattern data yet"
    total_patterns = len(_pattern_stats)
    total_trades = sum(
        v["wins"] + v["losses"] for v in _pattern_stats.values()
    )
    qualified = [k for k, v in _pattern_stats.items()
                 if v["wins"] + v["losses"] >= _MIN_SAMPLES]
    return (f"{total_patterns} patterns tracked | "
            f"{total_trades} total trades | "
            f"{len(qualified)} qualified (>={_MIN_SAMPLES} samples)")
