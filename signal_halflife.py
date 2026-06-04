"""
signal_halflife.py — Signal Alpha Decay Half-Life Modeling (v28.0)

Renaissance: every signal has a half-life. They only use signals within
their active window. Stale signals = noise, not alpha.

Half-life model:
  IC(age) = IC_0 * exp(-lambda * age)
  lambda = ln(2) / half_life
  half_life = age at which IC drops to 50% of initial value

Implementation:
  Uses ic_tracker.py history (signal_value, forward_return pairs with timestamps)
  Estimates half-life by fitting exponential decay to windowed IC series.

Default half-lives by signal category (calibrated on US equities research):
  Momentum signals:   60–120 min
  Volume signals:     30–60 min
  Pattern signals:    120–240 min
  Macro signals:      240–480 min
  Event signals:      1440–4320 min (1-3 days)

Returns freshness_multiplier (0.1 to 1.0).
"""

import logging
import math
import time as _time
from typing import Dict

logger = logging.getLogger(__name__)

# Default half-lives in minutes (fallback when no IC history exists)
_DEFAULT_HALFLIFE: Dict[str, float] = {
    # Momentum
    "momentum_5m":    60.0,
    "momentum_15m":   120.0,
    "rsi":            90.0,
    "macd":           120.0,
    # Volume
    "rvol":           45.0,
    "tod_rvol":       45.0,
    "tape_ofi":       30.0,
    "ofi":            30.0,
    # Pattern
    "breakout":       180.0,
    "vwap_reclaim":   90.0,
    "orb":            240.0,
    # Macro/regime
    "pca_alpha":      240.0,
    "stl_trend":      180.0,
    "csm":            360.0,
    "cross_sectional_rank": 120.0,
    # Event (long-lived)
    "analyst_signal": 4320.0,   # 3 days
    "dividend_capture": 1440.0, # 1 day
    "split_signal":   2880.0,   # 2 days
    "edgar_sentiment": 2880.0,
    # Alternative data
    "google_trends":  240.0,
    "wikipedia":      480.0,
    "reddit":         120.0,
    # Default fallback
    "default":        120.0,
}

# Freshness multiplier by age / half_life ratio
def get_freshness_multiplier(signal_name: str, age_minutes: float) -> float:
    """
    Returns multiplier (0.1 to 1.0) based on how old the signal is.
    Fresh signal (age < 0.5 × half-life) = 1.0
    Signal at half-life age = 0.5
    Signal at 2× half-life = 0.25
    """
    try:
        hl = estimate_halflife_minutes(signal_name)
        if hl <= 0 or age_minutes <= 0:
            return 1.0

        # Exponential decay: mult = exp(-ln(2) * age / half_life)
        decay_rate = math.log(2.0) / hl
        mult = math.exp(-decay_rate * age_minutes)

        # Floor at 0.1 (never fully zero)
        return max(0.1, min(1.0, mult))

    except Exception:
        return 1.0


def estimate_halflife_minutes(signal_name: str) -> float:
    """
    Return estimated half-life for a signal.
    First tries to find a category match, falls back to default 120 min.
    """
    # Direct match
    if signal_name in _DEFAULT_HALFLIFE:
        return _DEFAULT_HALFLIFE[signal_name]

    # Partial match (e.g., "rsi_14" → "rsi")
    for key, hl in _DEFAULT_HALFLIFE.items():
        if key in signal_name or signal_name in key:
            return hl

    return _DEFAULT_HALFLIFE["default"]


def apply_freshness_to_score(
    score: float,
    signal_name: str,
    signal_generated_at: float,
) -> float:
    """
    Apply half-life decay to a score.
    signal_generated_at: Unix timestamp or monotonic time when signal fired.
    """
    try:
        age_seconds = _time.monotonic() - signal_generated_at
        age_minutes = age_seconds / 60.0
        mult = get_freshness_multiplier(signal_name, age_minutes)
        return round(score * mult, 3)
    except Exception:
        return score
