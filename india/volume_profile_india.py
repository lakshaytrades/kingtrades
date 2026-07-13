"""
volume_profile_india.py — Intraday Volume Profile & VPOC Engine
Computes VPOC, VAH, VAL from today's 5m bars.
Score: +8 near VPOC bounce, +5 through value area, -6 against value area.
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("vol_profile_india")


class VolumeProfile:
    def compute(self, df: pd.DataFrame) -> dict:
        """
        Compute VPOC, VAH, VAL from intraday 5m OHLCV DataFrame.
        Returns dict with vpoc, vah, val; zeros on failure.
        """
        try:
            if df is None or df.empty or len(df) < 5:
                return {"vpoc": 0.0, "vah": 0.0, "val": 0.0}

            typical = (df["high"] + df["low"] + df["close"]) / 3
            price_mean = float(typical.mean())
            if price_mean <= 0:
                return {"vpoc": 0.0, "vah": 0.0, "val": 0.0}

            # Bin width = 0.1% of average price
            bin_w = max(0.05, price_mean * 0.001)
            lo = float(typical.min())
            hi = float(typical.max())

            bins = np.arange(lo, hi + bin_w, bin_w)
            if len(bins) < 2:
                return {"vpoc": 0.0, "vah": 0.0, "val": 0.0}

            vol_by_bin: dict = {}
            for i in range(len(df)):
                tp  = float(typical.iloc[i])
                vol = float(df["volume"].iloc[i]) if "volume" in df.columns else 1.0
                idx = int((tp - lo) / bin_w)
                idx = max(0, min(idx, len(bins) - 2))
                vol_by_bin[idx] = vol_by_bin.get(idx, 0.0) + vol

            if not vol_by_bin:
                return {"vpoc": 0.0, "vah": 0.0, "val": 0.0}

            vpoc_idx  = max(vol_by_bin, key=vol_by_bin.get)
            vpoc_price = lo + vpoc_idx * bin_w + bin_w / 2

            total_vol  = sum(vol_by_bin.values())
            target_vol = total_vol * 0.70

            # Expand outward from VPOC until 70% of volume captured
            accumulated = vol_by_bin.get(vpoc_idx, 0.0)
            lo_idx = hi_idx = vpoc_idx

            sorted_idxs = sorted(vol_by_bin.keys(), key=lambda x: vol_by_bin[x], reverse=True)
            for idx in sorted_idxs[1:]:
                if accumulated >= target_vol:
                    break
                accumulated += vol_by_bin[idx]
                if idx < lo_idx:
                    lo_idx = idx
                elif idx > hi_idx:
                    hi_idx = idx

            val = lo + lo_idx * bin_w
            vah = lo + (hi_idx + 1) * bin_w

            return {"vpoc": round(vpoc_price, 2), "vah": round(vah, 2), "val": round(val, 2)}

        except Exception as e:
            logger.debug(f"VolumeProfile.compute: {e}")
            return {"vpoc": 0.0, "vah": 0.0, "val": 0.0}

    def score_signal(self, current_price: float, direction: str, profile: dict) -> int:
        """
        +8 bouncing off VPOC (within 0.3%), +5 breaking through VAH/VAL in direction,
        -6 trading against value area.
        """
        try:
            vpoc = profile.get("vpoc", 0.0)
            vah  = profile.get("vah",  0.0)
            val  = profile.get("val",  0.0)

            if vpoc <= 0 or current_price <= 0:
                return 0

            # Near VPOC bounce
            if abs(current_price - vpoc) / vpoc <= 0.003:
                return 8

            if vah > 0 and val > 0:
                in_value_area = val <= current_price <= vah

                if direction == "LONG":
                    # Breaking above VAH — bullish
                    if current_price > vah:
                        return 5
                    # In value area going down — against
                    if not in_value_area and current_price < val:
                        return -6

                elif direction == "SHORT":
                    # Breaking below VAL — bearish
                    if current_price < val:
                        return 5
                    # In value area going up — against
                    if not in_value_area and current_price > vah:
                        return -6

            return 0

        except Exception as e:
            logger.debug(f"VolumeProfile.score_signal: {e}")
            return 0
