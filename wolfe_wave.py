"""
wolfe_wave.py — Wolfe Wave pattern detector
One of the highest-accuracy reversal setups. Points 1-2-3-4-5 geometry.
Bullish: 5 points where point 5 below trendline 1-3, targets line 1-4.
Bearish: 5 points where point 5 above trendline 1-3, targets line 1-4.
Returns (score_delta: float, reason: str). +12 max for confirmed Wolfe Wave.
"""
import logging
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def _find_swing_points(df: pd.DataFrame, window: int = 5) -> Tuple[list, list]:
    """Find swing highs and lows in OHLCV dataframe."""
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []

    if len(df) < (2 * window + 1):
        return highs, lows

    highs_vals = df["high"].values
    lows_vals = df["low"].values

    for i in range(window, len(df) - window):
        if all(highs_vals[i] >= highs_vals[i - j] for j in range(1, window + 1)) and \
           all(highs_vals[i] >= highs_vals[i + j] for j in range(1, window + 1)):
            highs.append((i, highs_vals[i]))
        if all(lows_vals[i] <= lows_vals[i - j] for j in range(1, window + 1)) and \
           all(lows_vals[i] <= lows_vals[i + j] for j in range(1, window + 1)):
            lows.append((i, lows_vals[i]))

    return highs, lows


def _line_value_at(x1: float, y1: float, x2: float, y2: float, x: float) -> Optional[float]:
    """Evaluate a line defined by two points at position x. Returns None if vertical."""
    if x2 == x1:
        return None
    slope = (y2 - y1) / (x2 - x1)
    return y1 + slope * (x - x1)


def _check_bullish_wolfe(lows: list, highs: list, current_idx: int, current_price: float) -> Tuple[bool, str]:
    """
    Bullish Wolfe Wave structure (5 points):
      Pt1: low, Pt2: high, Pt3: lower low, Pt4: lower high, Pt5: lowest low (below line 1-3)
      Price at/near Pt5 triggers long entry targeting line 1-4 extended.

    Rules:
      - Pt3 < Pt1 (lower low)
      - Pt5 < Pt3 (even lower low) and Pt5 below line 1→3 extended
      - Pt4 < Pt2 (lower high)
      - Pt2 and Pt4 form a declining channel
      - Lines 1-3 and 2-4 converge (wedge geometry)
    """
    if len(lows) < 3 or len(highs) < 2:
        return False, ""

    # Try last few combinations of 3 lows and 2 highs
    for li in range(len(lows) - 3, max(-1, len(lows) - 8), -1):
        for hi in range(len(highs) - 2, max(-1, len(highs) - 6), -1):
            try:
                idx1, p1 = lows[li]
                idx2, p2 = highs[hi]
                idx3, p3 = lows[li + 1]
                # Ensure p2 is between p1 and p3 in time
                if not (idx1 < idx2 < idx3):
                    continue

                idx4 = None
                p4 = None
                for hj in range(hi + 1, min(len(highs), hi + 5)):
                    if highs[hj][0] > idx3:
                        idx4, p4 = highs[hj]
                        break
                if idx4 is None:
                    continue

                if not (len(lows) > li + 2):
                    continue
                idx5, p5 = lows[li + 2]
                if idx5 <= idx4:
                    continue

                # Pt3 < Pt1 (lower low)
                if p3 >= p1:
                    continue
                # Pt4 < Pt2 (lower high)
                if p4 >= p2:
                    continue
                # Pt5 < Pt3
                if p5 >= p3:
                    continue

                # Pt5 should be below or near line 1→3 extended
                line13_at5 = _line_value_at(idx1, p1, idx3, p3, idx5)
                if line13_at5 is None:
                    continue
                if p5 > line13_at5 * 1.005:  # 0.5% tolerance
                    continue

                # Current price should be near Pt5 (within 1%)
                if abs(current_price - p5) / max(p5, 1e-9) > 0.01:
                    continue

                # Lines 1-3 and 2-4 should converge (channel narrowing)
                slope13 = (p3 - p1) / max(idx3 - idx1, 1)
                slope24 = (p4 - p2) / max(idx4 - idx2, 1)
                # Both declining; 1-3 falls faster or at similar rate
                if slope13 > 0 or slope24 > 0:
                    continue

                # Target: line 1→4 extended to current time
                target = _line_value_at(idx1, p1, idx4, p4, current_idx)
                if target is None or target <= current_price:
                    continue

                reward_pct = (target - current_price) / max(current_price, 1e-9) * 100
                if reward_pct < 0.3:
                    continue

                reason = (
                    f"Bullish Wolfe Wave: P1={p1:.2f} P3={p3:.2f} P5={p5:.2f} "
                    f"target={target:.2f} (+{reward_pct:.1f}%)"
                )
                return True, reason

            except Exception as e:
                logger.debug(f"[wolfe_wave] bullish check iteration: {e}")
                continue

    return False, ""


def _check_bearish_wolfe(lows: list, highs: list, current_idx: int, current_price: float) -> Tuple[bool, str]:
    """
    Bearish Wolfe Wave structure (5 points):
      Pt1: high, Pt2: low, Pt3: higher high, Pt4: higher low, Pt5: highest high (above line 1-3)
      Price at/near Pt5 triggers short entry targeting line 1-4 extended.
    """
    if len(highs) < 3 or len(lows) < 2:
        return False, ""

    for hi in range(len(highs) - 3, max(-1, len(highs) - 8), -1):
        for li in range(len(lows) - 2, max(-1, len(lows) - 6), -1):
            try:
                idx1, p1 = highs[hi]
                idx2, p2 = lows[li]
                idx3, p3 = highs[hi + 1]
                if not (idx1 < idx2 < idx3):
                    continue

                idx4 = None
                p4 = None
                for lj in range(li + 1, min(len(lows), li + 5)):
                    if lows[lj][0] > idx3:
                        idx4, p4 = lows[lj]
                        break
                if idx4 is None:
                    continue

                if not (len(highs) > hi + 2):
                    continue
                idx5, p5 = highs[hi + 2]
                if idx5 <= idx4:
                    continue

                # Pt3 > Pt1 (higher high)
                if p3 <= p1:
                    continue
                # Pt4 > Pt2 (higher low)
                if p4 <= p2:
                    continue
                # Pt5 > Pt3
                if p5 <= p3:
                    continue

                # Pt5 above line 1→3 extended
                line13_at5 = _line_value_at(idx1, p1, idx3, p3, idx5)
                if line13_at5 is None:
                    continue
                if p5 < line13_at5 * 0.995:
                    continue

                # Current price near Pt5
                if abs(current_price - p5) / max(p5, 1e-9) > 0.01:
                    continue

                # Both slopes rising
                slope13 = (p3 - p1) / max(idx3 - idx1, 1)
                slope24 = (p4 - p2) / max(idx4 - idx2, 1)
                if slope13 < 0 or slope24 < 0:
                    continue

                # Target: line 1→4 extended to current time
                target = _line_value_at(idx1, p1, idx4, p4, current_idx)
                if target is None or target >= current_price:
                    continue

                reward_pct = (current_price - target) / max(current_price, 1e-9) * 100
                if reward_pct < 0.3:
                    continue

                reason = (
                    f"Bearish Wolfe Wave: P1={p1:.2f} P3={p3:.2f} P5={p5:.2f} "
                    f"target={target:.2f} (-{reward_pct:.1f}%)"
                )
                return True, reason

            except Exception as e:
                logger.debug(f"[wolfe_wave] bearish check iteration: {e}")
                continue

    return False, ""


def get_wolfe_wave_score(symbol: str, df_5m: pd.DataFrame, direction: str) -> Tuple[float, str]:
    """
    Detect Wolfe Wave pattern in df_5m.
    Returns (+12, reason) for confirmed pattern in direction.
    Returns (0.0, "") if no pattern or fail-open on error.
    """
    try:
        if df_5m is None or len(df_5m) < 40:
            return 0.0, ""

        # Normalise column names to lowercase
        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]

        required = {"high", "low", "close"}
        if not required.issubset(set(df.columns)):
            return 0.0, ""

        # Use last 80 bars for pattern detection
        df = df.tail(80).reset_index(drop=True)
        current_idx = len(df) - 1
        current_price = float(df["close"].iloc[-1])

        highs, lows = _find_swing_points(df, window=5)

        if direction.upper() in ("LONG", "BUY"):
            found, reason = _check_bullish_wolfe(lows, highs, current_idx, current_price)
            if found:
                logger.info(f"[wolfe_wave] {symbol}: {reason}")
                return 12.0, reason

        elif direction.upper() in ("SHORT", "SELL"):
            found, reason = _check_bearish_wolfe(lows, highs, current_idx, current_price)
            if found:
                logger.info(f"[wolfe_wave] {symbol}: {reason}")
                return 12.0, reason

        return 0.0, ""

    except Exception as e:
        logger.debug(f"[wolfe_wave] {symbol}: {e}")
        return 0.0, ""
