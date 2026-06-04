"""
dark_pool_proxy.py — Dark Pool Activity Proxy (v28.0)

FINRA ATS data ($300-2000/month) shows off-exchange dark pool prints.
38% of US equity volume executes in dark pools.

Free proxy using three behavioral signals:

1. Round-price volume clustering: dark pools print at round numbers ($100.00,
   $100.50) because institutional algos use VWAP/TWAP benchmarks that land
   on round levels. Clusters at $X.00/$X.50 = institutional.

2. Volume timing anomaly: dark pools execute in specific windows when
   liquidity is available but not concentrated at exchange. Volume spikes
   in the 10:00-10:30, 13:00-14:00 windows not explained by normal patterns
   = off-exchange prints being reported.

3. Price-volume divergence: dark pool buying leaves a footprint:
   price doesn't move much but volume is heavy. Large block buys absorbed
   without moving price = dark pool (vs lit market which moves price).
"""

import logging
import math
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)


def get_dark_pool_score(symbol: str, df_5m, direction: str) -> Tuple[float, str]:
    """
    Detect institutional dark pool activity from OHLCV pattern analysis.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        if df_5m is None or len(df_5m) < 12:
            return 0.0, "dp:no-data"

        close_col  = "close"  if "close"  in df_5m.columns else "Close"
        high_col   = "high"   if "high"   in df_5m.columns else "High"
        low_col    = "low"    if "low"    in df_5m.columns else "Low"
        volume_col = "volume" if "volume" in df_5m.columns else "Volume"

        closes  = df_5m[close_col].values.astype(float)
        highs   = df_5m[high_col].values.astype(float)
        lows    = df_5m[low_col].values.astype(float)
        volumes = df_5m[volume_col].values.astype(float)

        signals = []

        # 1. Price-volume divergence: heavy volume but small price movement
        # = institutional absorption (dark pool)
        last_8_vols   = volumes[-8:]
        last_8_ranges = np.abs(highs[-8:] - lows[-8:])
        avg_vol   = float(np.mean(last_8_vols))
        avg_range = float(np.mean(last_8_ranges))

        # Recent 3 bars
        recent_vol   = float(np.mean(volumes[-3:]))
        recent_range = float(np.mean(np.abs(highs[-3:] - lows[-3:])))

        vol_ratio = recent_vol / max(avg_vol, 1.0)
        range_ratio = recent_range / max(avg_range, 0.0001)

        # High volume + compressed range = dark pool absorption
        if vol_ratio > 1.5 and range_ratio < 0.7:
            # Determine direction of absorption from price trend
            if len(closes) >= 6:
                price_trend = closes[-1] - closes[-6]
            else:
                price_trend = 0.0

            if direction == "LONG" and price_trend >= 0:
                signals.append(("buy-absorption", 7.0))
            elif direction == "SHORT" and price_trend <= 0:
                signals.append(("sell-absorption", 7.0))
            elif direction == "LONG" and price_trend < 0:
                signals.append(("sell-absorption-vs-long", -6.0))

        # 2. Round-price clustering (closes landing exactly at round numbers)
        round_count = 0
        for c in closes[-10:]:
            frac = c % 0.50  # check if close to 0.50 increment
            if frac < 0.03 or frac > 0.47:  # within 3 cents of $X.00 or $X.50
                round_count += 1

        round_pct = round_count / min(10, len(closes[-10:]))
        if round_pct > 0.5:  # >50% of closes near round numbers
            if direction == "LONG":
                signals.append(("round-clustering-long", 4.0))
            else:
                signals.append(("round-clustering-short", 4.0))

        # 3. Volume anomaly outside normal hours (simulate intraday window check)
        # Use bar index as proxy for time-of-day (can't get exact time reliably here)
        # Recent volume much higher than early session = accumulation phase signal
        if len(volumes) >= 20:
            early_vol = float(np.mean(volumes[:10]))
            mid_vol   = float(np.mean(volumes[10:20]))
            if mid_vol > early_vol * 1.8:
                signals.append(("mid-session-surge", 3.0))

        if not signals:
            return 0.0, "dp:no-signal"

        total_score = sum(s[1] for s in signals)
        tags = "+".join(s[0] for s in signals)
        total_score = float(np.clip(total_score, -8.0, 8.0))
        return total_score, f"dp:{tags}"

    except Exception as e:
        logger.debug(f"[dark_pool] {symbol}: {e}")
        return 0.0, "dp:error"
