"""
tape_speed.py — Tape Speed / Urgency Analyzer (v17.0)

Measures how quickly bars are printing relative to historical norm.
Fast tape = institutional urgency (FOMO buying or panic selling).

Thread-safe.  Fail-open (returns 0.0).  No external data required.
"""

import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def get_tape_speed_score(
    symbol: str, df_5m, direction: str
) -> Tuple[float, str]:
    """
    Tape speed / urgency analysis using OHLCV bar data.

    Metrics:
    1. Bar range velocity: (high-low) vs 20-bar average
    2. Close-to-close speed: |ΔP| per bar vs 20-bar average
    3. Volume surge per bar vs 20-bar average
    4. Directional consistency: consecutive bars in same direction

    Score (LONG):
      All 3 metrics > 2x avg + direction consistency > 5: +10
      2 of 3 metrics > 1.5x avg:                         +6
      All metrics < 0.5x avg:                            -4 (no conviction)
      Metrics in opposite direction:                      inverse penalties

    Fail-open: returns (0.0, "tape_speed N/A") on any error.
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, "tape_speed: insufficient data"

        # Column normalization
        col_map = {}
        for c in df_5m.columns:
            col_map[c.lower()] = c

        high_col   = col_map.get("high",   col_map.get("High",   None))
        low_col    = col_map.get("low",    col_map.get("Low",    None))
        close_col  = col_map.get("close",  col_map.get("Close",  None))
        volume_col = col_map.get("volume", col_map.get("Volume", None))

        if not all([high_col, low_col, close_col]):
            return 0.0, "tape_speed: missing OHLCV columns"

        highs   = df_5m[high_col].values.astype(float)
        lows    = df_5m[low_col].values.astype(float)
        closes  = df_5m[close_col].values.astype(float)
        volumes = df_5m[volume_col].values.astype(float) if volume_col else None

        n = len(closes)
        lookback = min(20, n - 1)

        # 1. Bar range velocity
        bar_ranges = highs - lows
        recent_range = float(np.mean(bar_ranges[-3:])) if n >= 3 else bar_ranges[-1]
        avg_range    = float(np.mean(bar_ranges[-lookback - 3:-3])) if n > lookback + 3 else float(np.mean(bar_ranges))
        range_ratio  = recent_range / (avg_range + 1e-9)

        # 2. Close-to-close speed (absolute)
        cc_moves = np.abs(np.diff(closes))
        recent_cc = float(np.mean(cc_moves[-3:])) if len(cc_moves) >= 3 else cc_moves[-1]
        avg_cc    = float(np.mean(cc_moves[-lookback - 3:-3])) if len(cc_moves) > lookback + 3 else float(np.mean(cc_moves))
        cc_ratio  = recent_cc / (avg_cc + 1e-9)

        # 3. Volume surge
        vol_ratio = 1.0
        if volumes is not None and len(volumes) >= lookback + 3:
            recent_vol = float(np.mean(volumes[-3:]))
            avg_vol    = float(np.mean(volumes[-lookback - 3:-3]))
            vol_ratio  = recent_vol / (avg_vol + 1e-9)

        # 4. Directional consistency (last 7 bars)
        direction_bars = min(7, n - 1)
        if direction_bars > 1:
            diffs = np.diff(closes[-direction_bars - 1:])
            up_bars   = int(np.sum(diffs > 0))
            down_bars = int(np.sum(diffs < 0))
            consec_up   = 0
            consec_down = 0
            for d in reversed(diffs):
                if d > 0:
                    consec_up += 1
                else:
                    break
            for d in reversed(diffs):
                if d < 0:
                    consec_down += 1
                else:
                    break
        else:
            up_bars = down_bars = consec_up = consec_down = 0

        # Score logic
        metrics_above_2x   = sum(1 for r in [range_ratio, cc_ratio, vol_ratio] if r > 2.0)
        metrics_above_1p5x = sum(1 for r in [range_ratio, cc_ratio, vol_ratio] if r > 1.5)
        metrics_below_half = sum(1 for r in [range_ratio, cc_ratio, vol_ratio] if r < 0.5)

        # Determine current tape direction
        tape_bullish = (up_bars > down_bars) and (consec_up >= 2 or closes[-1] > closes[-2])
        tape_bearish = (down_bars > up_bars) and (consec_down >= 2 or closes[-1] < closes[-2])

        score = 0.0
        reasons = []

        if direction == "LONG":
            if tape_bullish:
                if metrics_above_2x >= 3 and consec_up >= 5:
                    score = 10.0
                    reasons.append(f"urgent_buying(cons={consec_up})")
                elif metrics_above_1p5x >= 2:
                    score = 6.0
                    reasons.append(f"fast_tape_bull(metrics>{metrics_above_1p5x})")
                elif metrics_above_1p5x >= 1:
                    score = 3.0
                    reasons.append("moderate_tape")
            elif tape_bearish:
                if metrics_above_2x >= 2:
                    score = -8.0
                    reasons.append(f"urgent_selling_vs_long(cons={consec_down})")
                elif metrics_above_1p5x >= 2:
                    score = -5.0
                    reasons.append("fast_tape_bear_vs_long")

            if metrics_below_half >= 3:
                score = -4.0
                reasons.append("dying_tape")

        else:  # SHORT
            if tape_bearish:
                if metrics_above_2x >= 3 and consec_down >= 5:
                    score = 10.0
                    reasons.append(f"urgent_selling(cons={consec_down})")
                elif metrics_above_1p5x >= 2:
                    score = 6.0
                    reasons.append(f"fast_tape_bear(metrics>{metrics_above_1p5x})")
                elif metrics_above_1p5x >= 1:
                    score = 3.0
                    reasons.append("moderate_tape_bear")
            elif tape_bullish:
                if metrics_above_2x >= 2:
                    score = -8.0
                    reasons.append(f"urgent_buying_vs_short(cons={consec_up})")
                elif metrics_above_1p5x >= 2:
                    score = -5.0
                    reasons.append("fast_tape_bull_vs_short")

            if metrics_below_half >= 3:
                score = -4.0
                reasons.append("dying_tape_short")

        reason = " ".join(reasons) if reasons else f"tape_neutral(rng={range_ratio:.1f} cc={cc_ratio:.1f} vol={vol_ratio:.1f})"
        return float(score), reason
    except Exception as exc:
        logger.debug(f"get_tape_speed_score error (fail-open): {exc}")
        return 0.0, "tape_speed N/A"
