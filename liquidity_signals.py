"""
liquidity_signals.py — Liquidity & Market Impact Signals (v26.0)
Every professional firm models liquidity before entering.
Uses OHLCV to estimate order book conditions without Level 2 data.
"""
import logging
from typing import Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def get_amihud_score(
    symbol: str, df_5m: pd.DataFrame, direction: str
) -> Tuple[float, str]:
    """
    Amihud (2002) Illiquidity Ratio: |return| / dollar_volume
    Averaged over last 10 bars.
    - High Amihud (illiquid, price responsive): +5 for momentum trades
    - Low Amihud (liquid, price sticky): 0 (just less amplification)
    Fail-open (0.0).
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 10:
            return (0.0, "insufficient data for Amihud")

        close_col = "close" if "close" in df_5m.columns else "Close"
        vol_col   = "volume" if "volume" in df_5m.columns else "Volume"

        if close_col not in df_5m.columns or vol_col not in df_5m.columns:
            return (0.0, "missing close/volume columns")

        closes  = df_5m[close_col].iloc[-11:].values.astype(float)
        volumes = df_5m[vol_col].iloc[-11:].values.astype(float)

        if len(closes) < 2:
            return (0.0, "not enough bars")

        ratios = []
        for i in range(1, len(closes)):
            price_change = abs(closes[i] - closes[i - 1]) / max(closes[i - 1], 0.01)
            dollar_vol   = closes[i] * max(volumes[i], 1)
            amihud_ratio = price_change / dollar_vol * 1e6  # scale to readable units
            ratios.append(amihud_ratio)

        if not ratios:
            return (0.0, "no Amihud ratios computed")

        avg_amihud = float(np.mean(ratios))

        # High Amihud = illiquid = price moves easily = momentum amplifier
        # Threshold: empirically, avg_amihud > 0.05 = meaningfully illiquid for a 5-min bar
        if avg_amihud > 0.05:
            score = 5.0
            reason = f"Amihud={avg_amihud:.4f} (illiquid — momentum amplified) +5"
        else:
            score = 0.0
            reason = f"Amihud={avg_amihud:.4f} (liquid — normal momentum)"

        return (score, reason)

    except Exception as _e:
        logger.debug(f"[suppressed] get_amihud_score({symbol}): {_e}")
        return (0.0, "error — fail-open")


def get_spread_score(
    df_5m: pd.DataFrame, current_price: float
) -> Tuple[float, str]:
    """
    Roll (1984) spread estimator: spread = 2 * sqrt(max(0, -cov(delta_p_t, delta_p_{t-1})))
    Uses last 20 bars of close prices.
    - Spread > 0.5% of price: -6 (too expensive to enter/exit)
    - Spread < 0.1%: +3 (liquid, tight spread)
    Fail-open (0.0).
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 20:
            return (0.0, "insufficient data for Roll spread")

        close_col = "close" if "close" in df_5m.columns else "Close"
        if close_col not in df_5m.columns:
            return (0.0, "missing close column")

        closes = df_5m[close_col].iloc[-21:].values.astype(float)
        if len(closes) < 3:
            return (0.0, "not enough bars for Roll spread")

        delta_p = np.diff(closes)
        if len(delta_p) < 2:
            return (0.0, "not enough price changes")

        delta_t   = delta_p[1:]
        delta_t_1 = delta_p[:-1]

        cov_val = float(np.cov(delta_t, delta_t_1)[0, 1])
        roll_spread = 2.0 * np.sqrt(max(0.0, -cov_val))

        if current_price <= 0:
            return (0.0, "invalid current price")

        spread_pct = (roll_spread / current_price) * 100.0

        if spread_pct > 0.5:
            score  = -6.0
            reason = f"Roll spread={spread_pct:.3f}% — wide spread (costly entry/exit) -6"
        elif spread_pct < 0.1:
            score  = 3.0
            reason = f"Roll spread={spread_pct:.3f}% — tight spread (liquid) +3"
        else:
            score  = 0.0
            reason = f"Roll spread={spread_pct:.3f}% — neutral"

        return (score, reason)

    except Exception as _e:
        logger.debug(f"[suppressed] get_spread_score: {_e}")
        return (0.0, "error — fail-open")


def get_kyle_lambda_score(
    df_5m: pd.DataFrame, direction: str
) -> Tuple[float, str]:
    """
    Kyle's Lambda: price impact per unit of signed order flow.
    Estimated as regression of |price_change| on volume.
    - High lambda + direction aligns: +4 (price moves easily in our direction)
    - High lambda opposing: -3
    Fail-open (0.0).
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 15:
            return (0.0, "insufficient data for Kyle lambda")

        close_col = "close" if "close" in df_5m.columns else "Close"
        vol_col   = "volume" if "volume" in df_5m.columns else "Volume"

        if close_col not in df_5m.columns or vol_col not in df_5m.columns:
            return (0.0, "missing columns")

        closes  = df_5m[close_col].iloc[-15:].values.astype(float)
        volumes = df_5m[vol_col].iloc[-15:].values.astype(float)

        price_changes = np.abs(np.diff(closes))
        vols = volumes[1:]

        if len(price_changes) < 5 or np.sum(vols) == 0:
            return (0.0, "not enough data for regression")

        # Simple OLS: price_change = lambda * volume + epsilon
        vols_norm = vols / max(np.mean(vols), 1)
        cov_pv = float(np.cov(price_changes, vols_norm)[0, 1])
        var_v  = float(np.var(vols_norm))

        kyle_lambda = cov_pv / max(var_v, 1e-10)

        # Determine trend direction from closes
        recent_trend = closes[-1] - closes[-5] if len(closes) >= 5 else 0.0
        trend_aligns = (
            (direction == "LONG" and recent_trend > 0) or
            (direction == "SHORT" and recent_trend < 0)
        )

        # High lambda means price responds strongly to volume
        high_lambda = kyle_lambda > 0.001

        if high_lambda and trend_aligns:
            score  = 4.0
            reason = f"Kyle lambda={kyle_lambda:.5f} high + aligned with {direction} +4"
        elif high_lambda and not trend_aligns:
            score  = -3.0
            reason = f"Kyle lambda={kyle_lambda:.5f} high but opposing {direction} -3"
        else:
            score  = 0.0
            reason = f"Kyle lambda={kyle_lambda:.5f} low (price sticky)"

        return (score, reason)

    except Exception as _e:
        logger.debug(f"[suppressed] get_kyle_lambda_score: {_e}")
        return (0.0, "error — fail-open")


def get_volume_clock_score(
    df_5m: pd.DataFrame, direction: str
) -> Tuple[float, str]:
    """
    Front-loaded volume = institutional urgency.
    If first 6 bars (30 min) have >50% of today's total volume: +6.
    Even volume distribution: 0.
    Fail-open (0.0).
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 7:
            return (0.0, "insufficient data for volume clock")

        vol_col = "volume" if "volume" in df_5m.columns else "Volume"
        if vol_col not in df_5m.columns:
            return (0.0, "missing volume column")

        volumes = df_5m[vol_col].values.astype(float)

        # Today's bars: use the last 78 bars max (6.5 hrs × 12 bars/hr for 5-min)
        # For volume clock we look at the opening 6 bars vs total session
        total_vol = float(volumes.sum())
        if total_vol <= 0:
            return (0.0, "zero total volume")

        first_6_vol = float(volumes[:6].sum())
        front_load_pct = first_6_vol / total_vol

        if front_load_pct > 0.50:
            score  = 6.0
            reason = (
                f"front-loaded volume: first 6 bars = {front_load_pct:.0%} of total "
                f"(institutional urgency) +6"
            )
        else:
            score  = 0.0
            reason = f"even volume distribution: first 6 bars = {front_load_pct:.0%}"

        return (score, reason)

    except Exception as _e:
        logger.debug(f"[suppressed] get_volume_clock_score: {_e}")
        return (0.0, "error — fail-open")
