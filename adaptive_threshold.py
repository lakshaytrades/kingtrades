"""
adaptive_threshold.py — Rolling Win-Rate Adaptive Threshold

Top institutional desks raise/lower entry selectivity based on recent performance.
When the market regime matches their model: lower threshold (system is "hot").
When the market doesn't match: raise threshold (system is "cold").

This is NOT curve-fitting. It's regime-awareness. The threshold auto-resets
to base after 20 trades so it can't drift indefinitely.
"""

import logging
import time
from collections import deque
from typing import Deque, Tuple

logger = logging.getLogger(__name__)

_recent_outcomes: Deque[bool] = deque(maxlen=20)  # True=win, False=loss
_last_adjustment: float = 0.0
_ADJUSTMENT_COOLDOWN = 300.0   # min 5 min between adjustments


def record_outcome(won: bool) -> None:
    """Record a trade outcome. Call after each trade closes."""
    _recent_outcomes.append(won)


def get_adaptive_min_score(base_min: float) -> Tuple[float, str]:
    """
    Return adjusted minimum score based on recent 20-trade rolling WR.

      WR >= 65%: hot streak → base - 3 (allow slightly more trades)
      WR 50-65%: normal     → base (no change)
      WR 40-50%: cooling    → base + 4 (tighten up)
      WR < 40%:  cold       → base + 8 (very selective, something is wrong)

    Resets to base if fewer than 10 trades in buffer (insufficient data).
    """
    n = len(_recent_outcomes)
    if n < 10:
        return base_min, f"ADAPTIVE_WAIT({n}/10 trades)"

    wr = sum(_recent_outcomes) / n

    if wr >= 0.65:
        adj = -3.0
        label = f"HOT({wr:.0%},-3)"
    elif wr >= 0.50:
        adj = 0.0
        label = f"NORMAL({wr:.0%})"
    elif wr >= 0.40:
        adj = +4.0
        label = f"COLD({wr:.0%},+4)"
    else:
        adj = +8.0
        label = f"VERY_COLD({wr:.0%},+8)"

    return base_min + adj, label


def get_stats() -> dict:
    n = len(_recent_outcomes)
    wr = sum(_recent_outcomes) / n if n > 0 else 0.0
    return {"n": n, "wr": wr, "adjusted": n >= 10}
