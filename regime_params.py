"""
regime_params.py — Regime-Adaptive Parameter Switching

When market regime changes, automatically adjust trading parameters.
Used by: signal_generator.py, risk_manager.py

Key insight: momentum strategies fail in RANGING/CHOP markets.
This module dynamically adjusts thresholds to match the regime.
"""

from typing import Dict, Tuple
import logging

logger = logging.getLogger(__name__)

# Per-regime adjustments: (min_score_delta, size_multiplier, adx_delta, reason)
REGIME_PARAMS: Dict[str, Tuple[float, float, float, str]] = {
    "STRONG_TREND_UP":   (-2.0, 1.10, -2.0, "Strong trend — lower bar, bigger size"),
    "STRONG_TREND_DOWN": (-2.0, 1.10, -2.0, "Strong trend — lower bar, bigger size"),
    "WEAK_TREND_UP":     ( 0.0, 0.90,  0.0, "Weak trend — standard params"),
    "WEAK_TREND_DOWN":   ( 0.0, 0.90,  0.0, "Weak trend — standard params"),
    "RANGING":           (+6.0, 0.60, +4.0, "Ranging market — raise bar, cut size"),
    "HIGH_VOLATILITY":   (+3.0, 0.75, +2.0, "High vol — tighter filter, smaller size"),
    "LOW_VOLATILITY":    (+2.0, 0.80, +1.0, "Low vol squeeze — wait for breakout"),
    "OPENING_DRIVE":     (-3.0, 1.10, -2.0, "Opening drive — capture momentum"),
    "MIDDAY_CHOP":       (+8.0, 0.50, +5.0, "Midday chop — near-blackout"),
    "AFTERNOON_TREND":   (-1.0, 0.95,  0.0, "Afternoon trend — slight edge"),
    "UNKNOWN":           ( 0.0, 1.00,  0.0, "Unknown — neutral"),
}

def get_regime_adjustments(regime_name: str) -> Tuple[float, float, float, str]:
    """
    Returns (score_delta, size_mult, adx_delta, reason) for the given regime.
    score_delta: add to MIN_SIGNAL_SCORE (positive = stricter, negative = looser)
    size_mult: multiply position size by this
    adx_delta: add to ADX_MIN_TREND
    """
    return REGIME_PARAMS.get(regime_name, REGIME_PARAMS["UNKNOWN"])


def apply_regime_to_score(signal_score: float, regime_name: str) -> Tuple[float, str]:
    """
    Adjust a signal's effective score for regime context.
    Used in signal_generator to penalize signals generated in bad regimes.
    Returns (adjusted_score, reason).
    """
    score_delta, size_mult, adx_delta, reason = get_regime_adjustments(regime_name)
    if score_delta == 0:
        return signal_score, ""
    adjusted = max(0.0, min(100.0, signal_score - score_delta))
    direction = "reduced" if score_delta > 0 else "boosted"
    return adjusted, f"REGIME_{regime_name} score {direction} by {abs(score_delta):.0f}pts"


def get_regime_size_multiplier(regime_name: str) -> float:
    """Returns position size multiplier for given regime. 0.5–1.1."""
    _, size_mult, _, _ = get_regime_adjustments(regime_name)
    return size_mult
