"""
fibonacci_levels.py — Auto Fibonacci Level Engine

80% of institutional traders use the same Fibonacci levels.
That makes them self-fulfilling. This is not magic — it's market psychology.

Key levels:
  61.8% = Golden Ratio — highest probability reversal/continuation
  50.0% = Midpoint — most-watched institutional level
  38.2% = First retracement — trend continuation setups
  78.6% = Deep retracement — last-chance entry before trend fails

Data: computed from df_5m OHLCV (zero latency, no API calls)
Score range: 0 to +12
"""
import logging
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)

_FIB_SCORES = {0.618: 12, 0.500: 8, 0.382: 6, 0.786: 5, 0.236: 3}


def get_fibonacci_score(df, current_price: float, direction: str, tolerance: float = 0.003) -> Tuple[float, str]:
    """
    Returns (score_delta, reason).
    Price within `tolerance` (0.3%) of a key Fib level = precision institutional entry.
    Only adds points — never penalises. Fail-open.
    """
    try:
        if df is None or len(df) < 20 or current_price <= 0:
            return 0.0, ""

        n = min(60, len(df))
        highs = df["high"].values.astype(float)[-n:]
        lows  = df["low"].values.astype(float)[-n:]

        swing_high = float(np.max(highs))
        swing_low  = float(np.min(lows))
        rng = swing_high - swing_low
        if rng <= 0:
            return 0.0, ""

        for ratio, pts in sorted(_FIB_SCORES.items(), key=lambda x: -x[1]):
            # Retracement from swing high (bull pullback entry)
            lvl_bull = swing_high - ratio * rng
            # Retracement from swing low (bear pullback entry)
            lvl_bear = swing_low  + ratio * rng

            if abs(current_price - lvl_bull) / current_price <= tolerance:
                if direction in ("LONG", "BUY"):
                    name = f"FIB_{ratio*100:.1f}%_BULL_RETRACE"
                    logger.debug(f"Fib {ratio:.3f} bull level hit: {lvl_bull:.2f} vs price {current_price:.2f} +{pts}")
                    return float(pts), f"{name}(+{pts})"
                else:
                    return 3.0, f"FIB_{ratio*100:.1f}%_RESIST(+3)"

            if abs(current_price - lvl_bear) / current_price <= tolerance:
                if direction == "SHORT":
                    name = f"FIB_{ratio*100:.1f}%_BEAR_RETRACE"
                    return float(pts), f"{name}(+{pts})"
                else:
                    return 3.0, f"FIB_{ratio*100:.1f}%_SUPPORT(+3)"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"fibonacci_score: {e}")
        return 0.0, ""
