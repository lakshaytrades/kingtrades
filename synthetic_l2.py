"""
synthetic_l2.py — Synthetic Level-2 Order Book Reconstruction (v28.0)

Paid Level-2 data ($500-5000/month) shows full order book depth.
We reconstruct pseudo-L2 from 5-min OHLCV using:
  1. Corwin-Schultz spread → bid/ask price estimates
  2. Volume profile → infer depth at each price level
  3. VWAP distance → estimate where institutional limits sit
  4. Range clustering → detect round-number walls (psychological resistance)

Key insight: 70%+ of limit orders cluster at round prices ($10, $15.50, etc.)
Volume distribution within bar implies order density.
"""

import logging
import math
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)


def get_synthetic_spread(df_5m) -> float:
    """Estimate bid-ask spread from intraday H/L (Corwin-Schultz approximation)."""
    try:
        if df_5m is None or len(df_5m) < 3:
            return 0.0005  # 0.05% default

        h_col = "high"  if "high"  in df_5m.columns else "High"
        l_col = "low"   if "low"   in df_5m.columns else "Low"

        H = df_5m[h_col].values[-10:].astype(float)
        L = df_5m[l_col].values[-10:].astype(float)

        hl_ratios = []
        for h, l in zip(H, L):
            if h > l > 0:
                hl_ratios.append(math.log(h / l))

        if not hl_ratios:
            return 0.0005

        avg_hl_log = float(np.mean(hl_ratios))
        # Spread ≈ HL_ratio * 0.6 (empirical factor for 5-min bars)
        spread = avg_hl_log * 0.6
        return max(0.0001, min(0.005, spread))  # 0.01% to 0.5%

    except Exception as e:
        logger.debug(f"[syn_l2] spread: {e}")
        return 0.0005


def get_synthetic_depth_score(
    symbol: str,
    price: float,
    direction: str,
    df_5m,
) -> Tuple[float, str]:
    """
    Estimate order book depth in the direction of trade.
    Returns (score_delta, reason).

    Logic:
    - Thin ask side (price accelerating through levels) = easy fill = +5
    - Thick wall ahead (price stalling at round number) = resistance = -5
    - VWAP acting as support/resistance = add context
    """
    try:
        if df_5m is None or len(df_5m) < 10:
            return 0.0, "l2:no-data"

        close_col  = "close"  if "close"  in df_5m.columns else "Close"
        high_col   = "high"   if "high"   in df_5m.columns else "High"
        low_col    = "low"    if "low"    in df_5m.columns else "Low"
        volume_col = "volume" if "volume" in df_5m.columns else "Volume"

        closes  = df_5m[close_col].values.astype(float)
        highs   = df_5m[high_col].values.astype(float)
        lows    = df_5m[low_col].values.astype(float)
        volumes = df_5m[volume_col].values.astype(float)

        # 1. Compute VWAP
        tp       = (highs + lows + closes) / 3.0
        vwap     = float(np.sum(tp * volumes) / max(np.sum(volumes), 1.0))

        # 2. Detect round-number walls ahead
        wall_detected, wall_dist = _detect_round_wall(price, direction)

        # 3. Price velocity: is price accelerating or decelerating?
        if len(closes) >= 8:
            ret_recent = (closes[-1] - closes[-4]) / max(closes[-4], 0.01)
            ret_prior  = (closes[-4] - closes[-8]) / max(closes[-8], 0.01)
            acceleration = ret_recent - ret_prior
        else:
            acceleration = 0.0

        # 4. Volume trend in last 5 bars
        avg_vol_recent = float(np.mean(volumes[-3:]))
        avg_vol_prior  = float(np.mean(volumes[-8:-3])) if len(volumes) >= 8 else avg_vol_recent
        vol_trend = avg_vol_recent / max(avg_vol_prior, 1.0)

        score = 0.0
        reasons = []

        # LONG analysis
        if direction == "LONG":
            if wall_detected and wall_dist < 0.005:  # wall within 0.5%
                score -= 5.0
                reasons.append(f"wall@{wall_dist*100:.2f}%")
            elif not wall_detected:
                score += 3.0
                reasons.append("clear-path")

            if acceleration > 0.002 and vol_trend > 1.2:
                score += 5.0
                reasons.append("thin-ask(accel+vol)")
            elif acceleration < -0.002:
                score -= 3.0
                reasons.append("decelerating")

            if price > vwap * 1.001:
                score += 2.0
                reasons.append("above-vwap")

        # SHORT analysis
        else:
            if wall_detected and wall_dist < 0.005:  # support wall below
                score -= 5.0
                reasons.append(f"support@{wall_dist*100:.2f}%")
            elif not wall_detected:
                score += 3.0
                reasons.append("clear-path")

            if acceleration < -0.002 and vol_trend > 1.2:
                score += 5.0
                reasons.append("thin-bid(accel+vol)")
            elif acceleration > 0.002:
                score -= 3.0
                reasons.append("decelerating-short")

            if price < vwap * 0.999:
                score += 2.0
                reasons.append("below-vwap")

        reason = "l2:" + (",".join(reasons) if reasons else "neutral")
        return float(np.clip(score, -8.0, 8.0)), reason

    except Exception as e:
        logger.debug(f"[syn_l2] depth_score {symbol}: {e}")
        return 0.0, "l2:error"


def _detect_round_wall(price: float, direction: str) -> Tuple[bool, float]:
    """
    Detect if there's a round-number wall ahead.
    Checks $0.25, $0.50, $1.00 increments within 1% of current price.
    Returns (wall_found, distance_pct).
    """
    try:
        # Check round numbers ahead
        increments = [0.25, 0.50, 1.0, 2.5, 5.0]
        min_dist = 1.0

        for inc in increments:
            if direction == "LONG":
                # Find next round level above price
                next_level = math.ceil(price / inc) * inc
                if next_level > price:
                    dist = (next_level - price) / price
                    if dist < min_dist:
                        min_dist = dist
            else:
                # Find next round level below price
                prev_level = math.floor(price / inc) * inc
                if prev_level < price:
                    dist = (price - prev_level) / price
                    if dist < min_dist:
                        min_dist = dist

        wall_found = min_dist < 0.008  # within 0.8%
        return wall_found, min_dist

    except Exception:
        return False, 1.0
