"""
elliott_wave.py — Elliott Wave analysis
Identifies current wave count in 5-wave impulse structure.
Wave 3 trades = highest probability and biggest moves.
Returns (score_delta: float, reason: str). +10 max for Wave 3 confirmation.
"""
import logging
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def _find_pivots(prices: np.ndarray, window: int = 5) -> List[Tuple[int, float, str]]:
    """
    Find pivot highs and lows.
    Returns list of (index, price, 'H'|'L').
    """
    pivots: List[Tuple[int, float, str]] = []
    n = len(prices)
    for i in range(window, n - window):
        hi_slice = prices[max(0, i - window):i + window + 1]
        lo_slice = prices[max(0, i - window):i + window + 1]
        if prices[i] == hi_slice.max() and prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
            pivots.append((i, prices[i], "H"))
        elif prices[i] == lo_slice.min() and prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
            pivots.append((i, prices[i], "L"))

    # Deduplicate consecutive same types (keep most extreme)
    deduped: List[Tuple[int, float, str]] = []
    for p in pivots:
        if not deduped or deduped[-1][2] != p[2]:
            deduped.append(p)
        else:
            # Same type — keep more extreme
            prev = deduped[-1]
            if p[2] == "H" and p[1] > prev[1]:
                deduped[-1] = p
            elif p[2] == "L" and p[1] < prev[1]:
                deduped[-1] = p

    return deduped


def _identify_wave_structure(df: pd.DataFrame) -> Optional[dict]:
    """
    Identify Elliott Wave structure in last 50 bars.
    Returns dict with {wave_count, current_wave, wave3_target, confidence, direction}.
    None if no clear structure.

    Elliott Wave Rules enforced:
      - Wave 2 never retraces more than 100% of Wave 1
      - Wave 3 is never the shortest impulse wave (vs W1, W5)
      - Wave 4 never enters Wave 1 price territory
    """
    try:
        close = df["close"].values[-50:]
        n = len(close)
        if n < 20:
            return None

        pivots = _find_pivots(close, window=3)
        if len(pivots) < 6:
            pivots = _find_pivots(close, window=2)

        if len(pivots) < 6:
            return None

        best_fit = None
        best_confidence = 0.0

        # Try to fit the last 5+ pivots as a 5-wave impulse (up or down)
        for start in range(max(0, len(pivots) - 10), len(pivots) - 4):
            for direction_up in (True, False):
                try:
                    # Select 5 pivots in alternating H/L pattern
                    p = pivots[start:]
                    wave_pts = []
                    # Build 5-point sequence: alternating L-H-L-H-L (up) or H-L-H-L-H (down)
                    expected_types = ["L", "H", "L", "H", "L"] if direction_up else ["H", "L", "H", "L", "H"]
                    seq = []
                    for pv in p:
                        if len(seq) < 5 and pv[2] == expected_types[len(seq)]:
                            seq.append(pv)
                    if len(seq) < 5:
                        continue

                    w0_idx, w0, _ = seq[0]
                    w1_idx, w1, _ = seq[1]
                    w2_idx, w2, _ = seq[2]
                    w3_idx, w3, _ = seq[3]
                    w4_idx, w4, _ = seq[4]

                    if direction_up:
                        wave1_len = w1 - w0
                        wave2_retrace = w1 - w2
                        wave3_len = w3 - w2
                        wave4_retrace = w3 - w4

                        if wave1_len <= 0:
                            continue
                        # W2 < 100% retrace of W1
                        if wave2_retrace >= wave1_len:
                            continue
                        # W4 must not enter W1 territory
                        if w4 < w1:
                            continue
                        # W3 must not be shortest impulse
                        wave5_est = wave3_len * 0.618  # estimated
                        if wave3_len < wave1_len and wave3_len < wave5_est:
                            continue

                        confidence = 0.5
                        # Strong W3 (>1.618x W1)
                        if wave3_len >= wave1_len * 1.618:
                            confidence += 0.3
                        # W2 retraces 50-61.8% of W1 (ideal)
                        retrace_pct = wave2_retrace / wave1_len
                        if 0.382 <= retrace_pct <= 0.618:
                            confidence += 0.2

                        if confidence > best_confidence:
                            best_confidence = confidence
                            w3_fib_target = w2 + wave3_len * 1.618
                            best_fit = {
                                "wave_count": 5,
                                "current_wave": 4,  # W4 just printed
                                "direction": "LONG",
                                "w2_price": w2,
                                "w4_price": w4,
                                "wave3_target": w3_fib_target,
                                "confidence": confidence,
                                "wave1_len": wave1_len,
                                "wave3_len": wave3_len,
                            }
                    else:
                        # Bearish impulse
                        wave1_len = w0 - w1
                        wave2_retrace = w2 - w1
                        wave3_len = w2 - w3
                        wave4_retrace = w4 - w3

                        if wave1_len <= 0:
                            continue
                        if wave2_retrace >= wave1_len:
                            continue
                        if w4 > w1:
                            continue
                        wave5_est = wave3_len * 0.618
                        if wave3_len < wave1_len and wave3_len < wave5_est:
                            continue

                        confidence = 0.5
                        if wave3_len >= wave1_len * 1.618:
                            confidence += 0.3
                        retrace_pct = wave2_retrace / wave1_len
                        if 0.382 <= retrace_pct <= 0.618:
                            confidence += 0.2

                        if confidence > best_confidence:
                            best_confidence = confidence
                            w3_fib_target = w2 - wave3_len * 1.618
                            best_fit = {
                                "wave_count": 5,
                                "current_wave": 4,
                                "direction": "SHORT",
                                "w2_price": w2,
                                "w4_price": w4,
                                "wave3_target": w3_fib_target,
                                "confidence": confidence,
                                "wave1_len": wave1_len,
                                "wave3_len": wave3_len,
                            }
                except Exception:
                    continue

        # Check if price is in W3 territory (between W2 and W3 end in impulse direction)
        if best_fit:
            current_price = close[-1]
            w2 = best_fit["w2_price"]
            w4 = best_fit["w4_price"]
            direction = best_fit["direction"]

            # W5 setup: price is now pulling back from W4 and starting W5
            if direction == "LONG":
                w5_entry_zone = w4 < current_price < best_fit.get("wave3_target", 1e12)
                if w5_entry_zone:
                    best_fit["current_wave"] = 5
            elif direction == "SHORT":
                w5_entry_zone = w4 > current_price > best_fit.get("wave3_target", -1e12)
                if w5_entry_zone:
                    best_fit["current_wave"] = 5

        return best_fit

    except Exception as e:
        logger.debug(f"[elliott_wave] _identify_wave_structure: {e}")
        return None


def get_elliott_wave_score(symbol: str, df_5m: pd.DataFrame, direction: str) -> Tuple[float, str]:
    """
    Returns:
    +10: Price entering Wave 3 (strongest wave, trend acceleration)
    +6:  Wave 1 just completed, Wave 2 pullback = low-risk entry
    -8:  Wave 5 topping (potential reversal, avoid new longs)
    0:   No clear wave structure
    """
    try:
        if df_5m is None or len(df_5m) < 50:
            return 0.0, ""

        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]

        if "close" not in df.columns:
            return 0.0, ""

        wave_struct = _identify_wave_structure(df)

        if wave_struct is None:
            return 0.0, ""

        struct_direction = wave_struct.get("direction", "")
        current_wave = wave_struct.get("current_wave", 0)
        confidence = wave_struct.get("confidence", 0.5)
        wave3_target = wave_struct.get("wave3_target", 0)
        current_price = float(df["close"].iloc[-1])

        dir_up = direction.upper() in ("LONG", "BUY")
        dir_matches = (dir_up and struct_direction == "LONG") or \
                      (not dir_up and struct_direction == "SHORT")

        if not dir_matches:
            # Opposite wave structure is a penalty if we're trying to enter
            if current_wave == 5:
                return -8.0, f"Elliott Wave 5 {struct_direction} topping — avoid entry (confidence={confidence:.0%})"
            return 0.0, ""

        if current_wave == 3:
            score = 10.0 * confidence
            reason = (
                f"Elliott Wave 3 {struct_direction}: "
                f"target={wave3_target:.2f} confidence={confidence:.0%}"
            )
            return round(score, 1), reason

        elif current_wave == 2:
            # W1 complete, W2 pullback = low-risk W3 setup
            score = 6.0 * confidence
            reason = (
                f"Elliott W1→W2 pullback {struct_direction}: "
                f"W3 setup emerging confidence={confidence:.0%}"
            )
            return round(score, 1), reason

        elif current_wave == 4:
            # W4 pullback — next is W5, lower-confidence entry
            score = 4.0 * confidence
            reason = f"Elliott Wave 4 pullback {struct_direction}: W5 expected"
            return round(score, 1), reason

        elif current_wave == 5:
            # Wave 5 — danger of reversal
            return -8.0, f"Elliott Wave 5 topping {struct_direction} — reversal risk"

        return 0.0, ""

    except Exception as e:
        logger.debug(f"[elliott_wave] {symbol}: {e}")
        return 0.0, ""
