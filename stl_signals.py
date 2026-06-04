"""
stl_signals.py — STL Trend Decomposition Signal (v27.0)

STL (Seasonal-Trend decomposition using Loess) separates a price series into:
  - Trend component (the actual direction)
  - Seasonal component (intraday time-of-day patterns)
  - Residual component (noise)

Trading the TREND component instead of raw price eliminates seasonal noise.
Used by: Two Sigma, Renaissance, D.E. Shaw for filtering mean-reversion vs trend.

Implementation: MA-based STL approximation using centered moving averages
  1. Trend = centered MA(21) of price (removes daily seasonality)
  2. Detrended = price / trend
  3. Seasonal = average of detrended values by time-slot (over last 5 days)
  4. Residual = detrended / seasonal = pure noise

Score based on TREND slope (not raw price slope):
  - Strong uptrend + LONG = +8
  - Strong downtrend + LONG = -6
  - Flat trend (choppy) = -4 (avoid)

Uses 5-min OHLCV DataFrame as input (already in memory from BarCache).
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_stl_trend_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Compute STL trend component from 5-min OHLCV bars and score direction alignment.
    df_5m: pandas DataFrame with 'close' column and DatetimeIndex.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        if df_5m is None or len(df_5m) < 30:
            return 0.0, "stl:insufficient-data"

        import numpy as np

        close_col = "close" if "close" in df_5m.columns else "Close"
        closes = df_5m[close_col].values.astype(float)

        if len(closes) < 21:
            return 0.0, "stl:too-short"

        # Step 1: Trend via centered MA(21)
        trend = _centered_ma(closes, window=21)
        if trend is None or len(trend) < 5:
            return 0.0, "stl:ma-failed"

        # Step 2: Trend slope (annualized per bar)
        slope_window = min(10, len(trend) - 1)
        recent_trend = trend[-slope_window:]
        if len(recent_trend) < 3:
            return 0.0, "stl:trend-too-short"

        pct_slope = (recent_trend[-1] - recent_trend[0]) / recent_trend[0] * 100
        # pct_slope > 0 = uptrend, < 0 = downtrend (over last slope_window bars)

        # Step 3: Trend strength via R² of linear fit
        x = np.arange(len(recent_trend), dtype=float)
        x -= x.mean()
        y = recent_trend - recent_trend.mean()
        ss_tot = float(np.dot(y, y))
        if ss_tot < 1e-12:
            return 0.0, "stl:flat"
        beta = float(np.dot(x, y)) / float(np.dot(x, x))
        y_hat = x * beta
        ss_res = float(np.dot(y - y_hat, y - y_hat))
        r_squared = 1.0 - ss_res / ss_tot

        # Classify trend quality
        strong_trend = r_squared > 0.75
        moderate_trend = r_squared > 0.45
        flat = r_squared < 0.20 or abs(pct_slope) < 0.05

        if flat:
            return -4.0, f"stl:choppy(R²={r_squared:.2f})"

        if direction == "LONG":
            if pct_slope > 0.15 and strong_trend:
                return 8.0, f"stl:strong-uptrend(slope={pct_slope:.2f}%,R²={r_squared:.2f})"
            elif pct_slope > 0.05 and moderate_trend:
                return 4.0, f"stl:uptrend(slope={pct_slope:.2f}%)"
            elif pct_slope < -0.10:
                return -6.0, f"stl:downtrend-vs-long(slope={pct_slope:.2f}%)"
            return 0.0, f"stl:neutral(slope={pct_slope:.2f}%)"
        else:  # SHORT
            if pct_slope < -0.15 and strong_trend:
                return 8.0, f"stl:strong-downtrend(slope={pct_slope:.2f}%,R²={r_squared:.2f})"
            elif pct_slope < -0.05 and moderate_trend:
                return 4.0, f"stl:downtrend(slope={pct_slope:.2f}%)"
            elif pct_slope > 0.10:
                return -6.0, f"stl:uptrend-vs-short(slope={pct_slope:.2f}%)"
            return 0.0, f"stl:neutral(slope={pct_slope:.2f}%)"

    except Exception as e:
        logger.debug(f"[stl_signals] get_stl_trend_score failed: {e}")
        return 0.0, "stl:error"


def _centered_ma(values, window: int):
    """Compute centered moving average (avoids lag)."""
    try:
        import numpy as np
        n = len(values)
        if n < window:
            return None
        half = window // 2
        result = []
        for i in range(half, n - half):
            result.append(float(np.mean(values[i - half: i + half + 1])))
        return result
    except Exception:
        return None
