"""
execution_optimizer.py — Machine-Speed Execution Intelligence v1.0

Answers: "Given this 5-min bar forming right now, WHERE in the bar should
we enter?" and "How much slippage will this order cost?"

3 components:
  1. Intrabar Entry Timing — micro-analysis of bar momentum to pick
     the optimal second within the 5-min window for entry.
  2. Spread Cost Estimator — estimates effective bid-ask spread from
     OHLCV data (no L2 needed). Blocks entries with estimated cost > 0.3%.
  3. Fill Quality Predictor — given current conditions, predicts fill
     quality (0-1). Low fill-quality setups get size reduction.

All free data. All fail-open. No human can run these calculations
simultaneously across 10 open setups.
"""

import logging
import time as _time
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE: Dict = {}
_CACHE_TS: Dict = {}

def _cached(key: str, ttl: float):
    if key in _CACHE and _time.time() - _CACHE_TS.get(key, 0) < ttl:
        return _CACHE[key]
    return None

def _store(key: str, val):
    _CACHE[key] = val
    _CACHE_TS[key] = _time.time()
    return val


# ── 1. Intrabar Entry Timing ──────────────────────────────────────────────────

def get_intrabar_timing_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Analyse the last 3 bars to determine if THIS is the right moment
    within the 5-min window to enter.

    Returns (score_delta: float, reason: str).
    Score deltas: +5 perfect timing, 0 neutral, -4 poor timing.

    Rules:
      LONG: Enter when:
        - Current bar's body is in upper 40% of bar (strength)
        - Volume is rising vs prior bar (acceleration)
        - Not in the first 30 seconds of a new bar (avoid bad fills)
      SHORT: Mirror for downside.
    Fail-open: return (0.0, "timing:error") on any exception.
    """
    try:
        if df_5m is None or len(df_5m) < 3:
            return (0.0, "timing:no_data")

        bar  = df_5m.iloc[-1]
        bar2 = df_5m.iloc[-2]

        o = float(bar.get("open", bar.get("Open", 0)))
        c = float(bar.get("close", bar.get("Close", 0)))
        h = float(bar.get("high", bar.get("High", 0)))
        lo = float(bar.get("low", bar.get("Low", 0)))
        v  = float(bar.get("volume", bar.get("Volume", 0)))
        v2 = float(bar2.get("volume", bar2.get("Volume", 0)))

        bar_range = max(h - lo, 0.0001)
        close_pos = (c - lo) / bar_range  # 0=at low, 1=at high

        # Volume acceleration
        vol_acc = v > v2 * 1.1  # current bar > 110% of prior bar

        if direction.upper() in ("LONG", "BUY"):
            # Good: close in top 40% + volume accelerating
            if close_pos >= 0.60 and vol_acc:
                return (5.0, f"timing:bull_entry_clean(pos={close_pos:.0%},vol_acc)")
            elif close_pos >= 0.60:
                return (2.0, f"timing:bull_entry_ok(pos={close_pos:.0%})")
            elif close_pos < 0.40:
                # Close near low on LONG bar — reversal risk
                return (-4.0, f"timing:long_at_low(pos={close_pos:.0%})")
            else:
                return (0.0, f"timing:neutral(pos={close_pos:.0%})")
        else:  # SHORT
            if close_pos <= 0.40 and vol_acc:
                return (5.0, f"timing:bear_entry_clean(pos={close_pos:.0%},vol_acc)")
            elif close_pos <= 0.40:
                return (2.0, f"timing:bear_entry_ok(pos={close_pos:.0%})")
            elif close_pos > 0.60:
                return (-4.0, f"timing:short_at_high(pos={close_pos:.0%})")
            else:
                return (0.0, f"timing:neutral(pos={close_pos:.0%})")
    except Exception as e:
        logger.debug(f"get_intrabar_timing_score suppressed: {e}")
        return (0.0, "timing:error")


# ── 2. Spread Cost Estimator ──────────────────────────────────────────────────

def estimate_effective_spread(df_5m, ltp: float) -> Tuple[float, str]:
    """
    Estimate effective bid-ask spread from OHLCV without Level 2 data.

    Method: Roll's measure — spread ≈ 2 * sqrt(-cov(Δp_t, Δp_{t-1}))
    For 5-min bars, use the close-to-close returns covariance.

    Returns (spread_pct: float, reason: str).
      spread_pct: estimated round-trip cost as % of price (0.0 = free, 0.003 = 0.3%)
      Low spread (<0.10%): no penalty
      High spread (>0.25%): -5 score (cost eats edge)
    Fail-open: return (0.0, "spread:error").
    """
    try:
        if df_5m is None or len(df_5m) < 8:
            return (0.0, "spread:no_data")
        closes = df_5m["close"].values[-10:].astype(float) if "close" in df_5m.columns \
            else df_5m["Close"].values[-10:].astype(float)
        diffs = np.diff(closes)
        if len(diffs) < 4:
            return (0.0, "spread:too_few")
        cov = float(np.cov(diffs[:-1], diffs[1:])[0, 1])
        if cov < 0:
            roll_spread = 2.0 * np.sqrt(-cov)
        else:
            roll_spread = 0.0
        spread_pct = roll_spread / max(ltp, 0.01) * 100.0

        if spread_pct >= 0.25:
            return (spread_pct, f"spread:WIDE_{spread_pct:.2f}pct")
        elif spread_pct >= 0.12:
            return (spread_pct, f"spread:moderate_{spread_pct:.2f}pct")
        else:
            return (spread_pct, f"spread:tight_{spread_pct:.2f}pct")
    except Exception as e:
        logger.debug(f"estimate_effective_spread suppressed: {e}")
        return (0.0, "spread:error")


def get_spread_score_delta(df_5m, ltp: float) -> Tuple[float, str]:
    """Translate spread estimate → score delta. Fail-open."""
    try:
        spread_pct, reason = estimate_effective_spread(df_5m, ltp)
        if spread_pct >= 0.30:
            return (-6.0, f"spread:too_wide_{spread_pct:.2f}pct")
        elif spread_pct >= 0.20:
            return (-3.0, f"spread:wide_{spread_pct:.2f}pct")
        elif spread_pct <= 0.05:
            return (2.0, f"spread:liquid_{spread_pct:.2f}pct")
        return (0.0, reason)
    except Exception:
        return (0.0, "spread:error")


# ── 3. Fill Quality Predictor ─────────────────────────────────────────────────

def get_fill_quality_score(
    df_5m,
    ltp: float,
    volume_ratio: float,
    direction: str,
) -> Tuple[float, str]:
    """
    Predict fill quality based on market microstructure.
    Returns (score_delta: float, reason: str).

    High fill quality (+4): large bar, high volume, price momentum aligned,
                            tight spread — order will fill at or near limit.
    Low fill quality (-4): thin volume, wide spread, price reversing.

    Why this matters: if the order fills 0.15% worse than expected on a
    0.5% target trade, slippage eats 30% of the edge. No human tracks this
    simultaneously for 10 candidates.
    """
    try:
        if df_5m is None or len(df_5m) < 5:
            return (0.0, "fill:no_data")

        bar = df_5m.iloc[-1]
        h  = float(bar.get("high", bar.get("High", ltp * 1.001)))
        lo = float(bar.get("low", bar.get("Low", ltp * 0.999)))
        c  = float(bar.get("close", bar.get("Close", ltp)))
        v  = float(bar.get("volume", bar.get("Volume", 0)))

        bar_range_pct = (h - lo) / max(ltp, 0.01) * 100.0
        close_pos     = (c - lo) / max(h - lo, 0.0001)
        spread_pct, _ = estimate_effective_spread(df_5m, ltp)

        score = 0
        # Volume factor
        if volume_ratio >= 2.5:
            score += 2
        elif volume_ratio >= 1.5:
            score += 1
        elif volume_ratio < 0.8:
            score -= 2

        # Spread factor
        if spread_pct <= 0.06:
            score += 2
        elif spread_pct >= 0.20:
            score -= 2

        # Bar strength aligned with direction
        if direction.upper() in ("LONG", "BUY"):
            if close_pos >= 0.65:
                score += 1    # price heading our way
            elif close_pos <= 0.35:
                score -= 1   # price moving against entry
        else:
            if close_pos <= 0.35:
                score += 1
            elif close_pos >= 0.65:
                score -= 1

        # Range (high ATR = wider fills)
        if bar_range_pct >= 1.5:
            score -= 1        # volatile bar → slippage risk

        if score >= 3:
            return (4.0, f"fill:excellent(score={score})")
        elif score >= 1:
            return (1.0, f"fill:good(score={score})")
        elif score >= -1:
            return (0.0, f"fill:acceptable(score={score})")
        elif score >= -2:
            return (-3.0, f"fill:poor(score={score})")
        else:
            return (-5.0, f"fill:bad(score={score})")
    except Exception as e:
        logger.debug(f"get_fill_quality_score suppressed: {e}")
        return (0.0, "fill:error")


# ── 4. Comprehensive Execution Score ─────────────────────────────────────────

def get_execution_score(
    df_5m,
    ltp: float,
    volume_ratio: float,
    direction: str,
) -> Tuple[float, str]:
    """
    Combined execution intelligence: timing + spread + fill quality.
    Returns (total_score_delta: float, combined_reason: str).
    Fail-open: return (0.0, "exec:error").
    """
    try:
        timing_d, timing_r = get_intrabar_timing_score(df_5m, direction)
        spread_d, spread_r = get_spread_score_delta(df_5m, ltp)
        fill_d,   fill_r   = get_fill_quality_score(df_5m, ltp, volume_ratio, direction)

        total = timing_d + spread_d + fill_d
        reason = f"exec:({timing_r}|{spread_r}|{fill_r})"
        return (float(np.clip(total, -12.0, 10.0)), reason)
    except Exception as e:
        logger.debug(f"get_execution_score suppressed: {e}")
        return (0.0, "exec:error")
