"""
gann_levels.py — W.D. Gann price levels and angles
Gann Square of Nine, Gann angles (1x1, 2x1, 1x2), Gann fan lines.
Returns (score_delta: float, reason: str). Fail-open.
"""
import logging
import math
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Gann angle multipliers: (price_units_per_time_unit)
# 1x1 = 45° (balanced), 2x1 = steep rise, 1x2 = slow rise
_GANN_ANGLE_RATIOS = [2.0, 1.618, 1.0, 0.618, 0.5, 0.25]

_PROXIMITY_PCT = 0.003  # 0.3% tolerance for "at level"


def _gann_square_of_nine(price: float) -> List[float]:
    """
    Calculate key Gann Square of Nine levels near a price.
    These are natural resistance/support levels many traders use.

    Method: take sqrt(price), add/subtract multiples of 0.125 (1/8 of circle),
    square back to get price levels.
    """
    if price <= 0:
        return []

    root = math.sqrt(price)
    levels = []
    offsets = [
        -1.0, -0.875, -0.75, -0.625, -0.5, -0.375, -0.25, -0.125,
        0.0,
        0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0,
    ]
    for offset in offsets:
        candidate = (root + offset) ** 2
        if candidate > 0:
            levels.append(round(candidate, 2))

    return sorted(levels)


def _gann_fan_levels(anchor_price: float, anchor_idx: int,
                     current_idx: int) -> List[float]:
    """
    Calculate Gann fan levels from a swing anchor point to current bar.
    Uses standard Gann angles: 1x8, 1x4, 1x3, 1x2, 1x1, 2x1, 3x1, 4x1, 8x1.

    Returns list of price levels at current_idx.
    """
    if anchor_price <= 0 or current_idx <= anchor_idx:
        return []

    # Normalise: 1 unit = 1 bar, 1 price unit = 1 tick (relative)
    # Use price * 0.001 as base tick (0.1% of price per bar)
    tick = anchor_price * 0.001
    bars_elapsed = current_idx - anchor_idx

    fan_levels = []
    angle_ratios = [8.0, 4.0, 3.0, 2.0, 1.0, 0.5, 0.333, 0.25, 0.125]
    for ratio in angle_ratios:
        level = anchor_price + ratio * tick * bars_elapsed
        if level > 0:
            fan_levels.append(round(level, 2))
        level_down = anchor_price - ratio * tick * bars_elapsed
        if level_down > 0:
            fan_levels.append(round(level_down, 2))

    return sorted(fan_levels)


def _nearest_gann_level(price: float, levels: List[float]) -> Tuple[Optional[float], float]:
    """Return (nearest_level, proximity_pct). proximity_pct = 0 means exact match."""
    if not levels:
        return None, 1.0
    distances = [(abs(lv - price) / max(price, 1e-9), lv) for lv in levels]
    distances.sort()
    best_pct, best_lv = distances[0]
    return best_lv, best_pct


def _find_swing_anchor(df: pd.DataFrame) -> Tuple[Optional[float], int]:
    """Find the most recent significant swing low (for bull) or high (for bear)."""
    try:
        window = 10
        lows_vals = df["low"].values
        highs_vals = df["high"].values
        n = len(df)

        # Find most recent swing low (last 40 bars)
        for i in range(n - 2, max(n - 40, window), -1):
            if all(lows_vals[i] <= lows_vals[i - j] for j in range(1, min(window + 1, i + 1))) and \
               all(lows_vals[i] <= lows_vals[i + j] for j in range(1, min(window + 1, n - i))):
                return float(lows_vals[i]), i

        # Fallback: absolute low of last 30 bars
        slice_start = max(0, n - 30)
        min_idx = int(np.argmin(lows_vals[slice_start:])) + slice_start
        return float(lows_vals[min_idx]), min_idx

    except Exception:
        return None, 0


def get_gann_score(symbol: str, df_5m: pd.DataFrame, ltp: float, direction: str) -> Tuple[float, str]:
    """
    Check if price is near a Gann Square of Nine level or on a Gann angle.
    Returns (+8, reason) if price at key Gann level and direction aligns.
    Returns (0.0, "") otherwise or on error.
    """
    try:
        if df_5m is None or len(df_5m) < 20 or ltp <= 0:
            return 0.0, ""

        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]

        required = {"high", "low", "close"}
        if not required.issubset(set(df.columns)):
            return 0.0, ""

        df = df.tail(80).reset_index(drop=True)
        current_idx = len(df) - 1

        # ── Square of Nine levels ────────────────────────────────────────────
        s9_levels = _gann_square_of_nine(ltp)
        nearest_s9, prox_s9 = _nearest_gann_level(ltp, s9_levels)

        # ── Gann fan levels from recent swing anchor ──────────────────────────
        anchor_price, anchor_idx = _find_swing_anchor(df)
        fan_levels: List[float] = []
        if anchor_price and anchor_idx < current_idx:
            fan_levels = _gann_fan_levels(anchor_price, anchor_idx, current_idx)

        nearest_fan, prox_fan = _nearest_gann_level(ltp, fan_levels)

        # ── Check proximity and direction ─────────────────────────────────────
        score = 0.0
        reasons: List[str] = []

        dir_up = direction.upper() in ("LONG", "BUY")

        # Proximity to S9 level
        if prox_s9 <= _PROXIMITY_PCT and nearest_s9 is not None:
            # Is price approaching from correct direction?
            if dir_up and ltp >= nearest_s9 * (1 - _PROXIMITY_PCT):
                score += 5.0
                reasons.append(f"S9_support={nearest_s9:.2f} ({prox_s9*100:.2f}% away)")
            elif not dir_up and ltp <= nearest_s9 * (1 + _PROXIMITY_PCT):
                score += 5.0
                reasons.append(f"S9_resistance={nearest_s9:.2f} ({prox_s9*100:.2f}% away)")

        # Proximity to Gann fan level
        if prox_fan <= _PROXIMITY_PCT and nearest_fan is not None:
            if dir_up and ltp >= nearest_fan * (1 - _PROXIMITY_PCT):
                score += 3.0
                reasons.append(f"Gann_fan_support={nearest_fan:.2f} ({prox_fan*100:.2f}%)")
            elif not dir_up and ltp <= nearest_fan * (1 + _PROXIMITY_PCT):
                score += 3.0
                reasons.append(f"Gann_fan_resistance={nearest_fan:.2f} ({prox_fan*100:.2f}%)")

        # Combined: both S9 + fan agree
        if score >= 7.0:
            score = 8.0
            reasons.append("Gann_confluence")

        if score == 0.0:
            return 0.0, ""

        reason_str = f"Gann {direction}: " + " | ".join(reasons)
        return min(8.0, score), reason_str

    except Exception as e:
        logger.debug(f"[gann_levels] {symbol}: {e}")
        return 0.0, ""
