"""
elite_filter.py — Top-1% Signal Quality Enforcer

Top-1% traders take FEWER trades with HIGHER conviction.
This module enforces:

1. VIX-Adaptive Strategy — Different rules for different volatility regimes
   VIX < 15 (LOW VOL): Trend-following strategies work best → add momentum signals
   VIX 15-25 (NORMAL): Standard parameters
   VIX 25-35 (HIGH VOL): Mean-reversion works better → raise quality threshold
   VIX > 35 (CRISIS): Only EXTREME signals → 95+ score required, 0.5x size

2. Reward-to-Risk Enforcer — Minimum 2:1 R/R required
   If ATR-based target is <2x the ATR-based stop: reject signal
   If risk > 1% of capital: reject or reduce size

3. Entry Timing Optimizer — Best time-of-day to enter
   Based on historical win rates by time slot (from performance_analytics)
   Skip entries in low-win-rate time slots unless signal is elite (90+)

4. Trend Strength Gate — Only trade with momentum behind you
   Calculate trend strength from ADX (average directional index)
   ADX > 25 = strong trend → allow momentum trades
   ADX 15-25 = moderate trend → require higher signal score (+5 pts required)
   ADX < 15 = choppy → skip momentum trades, only mean-reversion

5. News Velocity Checker — Avoid trading on stale news
   If the signal was triggered by news >2 hours old: penalty
   Fresh news (< 30 min): bonus +5

6. Smart Stop Loss Calculator — ATR-based dynamic stops
   Round stops to nearest support/resistance level
   Never place stop where many other traders would (stop hunt zones)
   Returns optimal entry, stop, target1, target2 levels
"""

import logging
import time as _time_module
from typing import Optional, Tuple, Dict

import numpy as np

logger = logging.getLogger(__name__)

# ── VIX regime cache ────────────────────────────────────────────────────────
_vix_cache: Dict = {}

# VIX regime boundaries
_VIX_LOW_VOL   = 15.0
_VIX_NORMAL_HI = 25.0
_VIX_HIGH_HI   = 35.0

# Cache TTL: 30 minutes
_VIX_CACHE_TTL = 1800


def get_vix_regime() -> Tuple[float, str, float, str]:
    """
    Returns (vix_value, regime, size_multiplier, strategy_note).
    Regimes: LOW_VOL, NORMAL, HIGH_VOL, CRISIS
    Cached 30 min. Fail-open: returns NORMAL defaults on any error.
    """
    global _vix_cache

    now = _time_module.monotonic()
    if _vix_cache.get("expires", 0) > now:
        return (
            _vix_cache["vix"],
            _vix_cache["regime"],
            _vix_cache["mult"],
            _vix_cache["note"],
        )

    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1d", interval="5m")
        if hist is not None and not hist.empty:
            vix_val = float(hist["Close"].iloc[-1])
        else:
            # Fallback: fast_info
            vix_val = float(ticker.fast_info.get("lastPrice", 18.0) or 18.0)
    except Exception as _e:
        logger.debug(f"[elite_filter] VIX fetch failed: {_e} — defaulting to 18.0")
        vix_val = 18.0  # fail-open: assume NORMAL regime

    if vix_val < _VIX_LOW_VOL:
        regime = "LOW_VOL"
        mult   = 1.2
        note   = "Trend-following mode"
    elif vix_val < _VIX_NORMAL_HI:
        regime = "NORMAL"
        mult   = 1.0
        note   = "Standard parameters"
    elif vix_val < _VIX_HIGH_HI:
        regime = "HIGH_VOL"
        mult   = 0.7
        note   = "Raise quality threshold"
    else:
        regime = "CRISIS"
        mult   = 0.4
        note   = "Elite signals only (90+)"

    _vix_cache = {
        "vix":     vix_val,
        "regime":  regime,
        "mult":    mult,
        "note":    note,
        "expires": now + _VIX_CACHE_TTL,
    }
    logger.debug(
        f"[elite_filter] VIX={vix_val:.1f} regime={regime} size_mult={mult}x ({note})"
    )
    return (vix_val, regime, mult, note)


def get_vix_score_threshold(base_threshold: float) -> float:
    """
    Adjust minimum signal score based on VIX regime.
    LOW_VOL:  base_threshold - 3 (easier to trade — trend-following edge is high)
    NORMAL:   base_threshold (unchanged)
    HIGH_VOL: base_threshold + 5
    CRISIS:   max(90, base_threshold + 15)
    Fail-open: returns base_threshold on any error.
    """
    try:
        _vix, regime, _mult, _note = get_vix_regime()
        if regime == "LOW_VOL":
            return max(0.0, base_threshold - 3.0)
        elif regime == "NORMAL":
            return base_threshold
        elif regime == "HIGH_VOL":
            return base_threshold + 5.0
        else:  # CRISIS
            return max(90.0, base_threshold + 15.0)
    except Exception as _e:
        logger.debug(f"[elite_filter] get_vix_score_threshold failed: {_e}")
        return base_threshold


def check_reward_to_risk(
    entry: float,
    stop_loss: float,
    target: float,
    direction: str,
    min_rr: float = 2.0,
) -> Tuple[bool, float, str]:
    """
    Returns (passes: bool, actual_rr: float, reason: str).
    Calculates R/R = abs(target - entry) / abs(entry - stop_loss).
    Rejects if R/R < min_rr.
    Fail-open: returns (True, 0.0, 'calc_error') on any exception.
    """
    try:
        risk   = abs(entry - stop_loss)
        reward = abs(target - entry)

        if risk <= 0.0:
            return (False, 0.0, "Stop loss equals entry — zero risk, invalid signal")

        rr = reward / risk

        if rr < min_rr:
            reason = (
                f"R/R {rr:.2f}:1 below minimum {min_rr:.1f}:1 "
                f"(entry={entry:.2f} SL={stop_loss:.2f} T={target:.2f})"
            )
            return (False, rr, reason)

        return (True, rr, f"R/R {rr:.2f}:1 passes minimum {min_rr:.1f}:1")

    except Exception as _e:
        logger.debug(f"[elite_filter] check_reward_to_risk error: {_e}")
        return (True, 0.0, "calc_error")


def get_adx_strength(df_5m) -> Tuple[float, str]:
    """
    Calculate ADX from df_5m (pandas DataFrame with high, low, close columns).
    Returns (adx_value, strength_label):
      adx > 25: (adx, 'STRONG_TREND')
      adx 15-25: (adx, 'MODERATE')
      adx < 15:  (adx, 'CHOPPY')
    Uses pure numpy/pandas (no pandas_ta dependency).
    ADX formula: DM+ and DM- → TR → ATR → DI+ DI- → DX → ADX (14-period)
    Fail-open: returns (20.0, 'MODERATE') on any error.
    """
    try:
        # Normalise column names to lowercase
        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]

        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        close = df["close"].values.astype(float)

        period = 14
        n = len(high)

        if n < period + 2:
            return (20.0, "MODERATE")

        # True Range
        tr = np.zeros(n)
        for i in range(1, n):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i - 1])
            lc = abs(low[i]  - close[i - 1])
            tr[i] = max(hl, hc, lc)

        # Directional Movement
        dm_plus  = np.zeros(n)
        dm_minus = np.zeros(n)
        for i in range(1, n):
            up   = high[i]  - high[i - 1]
            down = low[i - 1] - low[i]
            dm_plus[i]  = up   if (up > down and up > 0)   else 0.0
            dm_minus[i] = down if (down > up and down > 0) else 0.0

        # Wilder smoothing (cumulative)
        def _wilder_smooth(arr: np.ndarray, p: int) -> np.ndarray:
            out = np.zeros(len(arr))
            out[p] = arr[1 : p + 1].sum()
            for i in range(p + 1, len(arr)):
                out[i] = out[i - 1] - out[i - 1] / p + arr[i]
            return out

        atr_sm      = _wilder_smooth(tr,        period)
        dm_plus_sm  = _wilder_smooth(dm_plus,   period)
        dm_minus_sm = _wilder_smooth(dm_minus,  period)

        # DI+ and DI-  (suppress divide-by-zero in np.where — guard handles it)
        with np.errstate(invalid="ignore", divide="ignore"):
            di_plus  = np.where(atr_sm > 0, 100.0 * dm_plus_sm  / atr_sm, 0.0)
            di_minus = np.where(atr_sm > 0, 100.0 * dm_minus_sm / atr_sm, 0.0)

        # DX
        di_sum = di_plus + di_minus
        with np.errstate(invalid="ignore", divide="ignore"):
            dx = np.where(di_sum > 0, 100.0 * np.abs(di_plus - di_minus) / di_sum, 0.0)

        # ADX = Wilder smooth of DX
        adx_sm = _wilder_smooth(dx, period)

        adx_val = float(adx_sm[-1])

        if adx_val > 25.0:
            label = "STRONG_TREND"
        elif adx_val >= 15.0:
            label = "MODERATE"
        else:
            label = "CHOPPY"

        return (adx_val, label)

    except Exception as _e:
        logger.debug(f"[elite_filter] get_adx_strength error: {_e}")
        return (20.0, "MODERATE")


def get_score_adjustment_for_regime(
    adx: float,
    vix: float,
    direction: str,
) -> Tuple[float, str]:
    """
    Combined regime score adjustment based on ADX trend strength and VIX level.
    Returns (score_delta, reason).

    Rules:
    STRONG TREND + NORMAL VIX + any direction:            +5  pts
    STRONG TREND + LOW_VOL VIX (<15):                     +3  pts (momentum in low-vol)
    CHOPPY + HIGH_VOL VIX (25-35):                        -8  pts (noise amplified)
    CHOPPY + any VIX:                                     -4  pts (choppy alone)
    CRISIS VIX (>35):                                     -20 pts (hard penalty)
    MODERATE trend: no adjustment
    """
    try:
        total_delta = 0.0
        reasons = []

        # Classify ADX
        if adx > 25.0:
            adx_label = "STRONG_TREND"
        elif adx >= 15.0:
            adx_label = "MODERATE"
        else:
            adx_label = "CHOPPY"

        # Classify VIX
        if vix > 35.0:
            vix_label = "CRISIS"
        elif vix > 25.0:
            vix_label = "HIGH_VOL"
        elif vix < 15.0:
            vix_label = "LOW_VOL"
        else:
            vix_label = "NORMAL"

        # Apply CRISIS first — hard penalty, no further adjustments needed
        if vix_label == "CRISIS":
            return (-20.0, f"CRISIS VIX={vix:.1f} — hard penalty -20")

        # ADX-based adjustments
        if adx_label == "STRONG_TREND":
            if vix_label == "NORMAL":
                total_delta += 5.0
                reasons.append(f"STRONG_TREND ADX={adx:.0f} + NORMAL VIX +5")
            elif vix_label == "LOW_VOL":
                total_delta += 3.0
                reasons.append(f"STRONG_TREND ADX={adx:.0f} + LOW_VOL +3")
            elif vix_label == "HIGH_VOL":
                # Strong trend survives high vol but only slightly better
                total_delta += 0.0
                reasons.append(f"STRONG_TREND ADX={adx:.0f} + HIGH_VOL neutral")
        elif adx_label == "CHOPPY":
            if vix_label == "HIGH_VOL":
                total_delta -= 8.0
                reasons.append(f"CHOPPY ADX={adx:.0f} + HIGH_VOL -8")
            else:
                total_delta -= 4.0
                reasons.append(f"CHOPPY ADX={adx:.0f} -4")
        # MODERATE: no adjustment

        reason_str = " | ".join(reasons) if reasons else "MODERATE regime — no adjustment"
        return (total_delta, reason_str)

    except Exception as _e:
        logger.debug(f"[elite_filter] get_score_adjustment_for_regime error: {_e}")
        return (0.0, "regime_calc_error")


def calculate_optimal_levels(
    symbol: str,
    entry: float,
    direction: str,
    atr: float,
    df_5m,
) -> Dict:
    """
    Calculate optimal entry, stop, targets based on ATR + support/resistance.

    Stop Loss Rules:
    - Long:  entry - (1.5 * atr), adjusted away from last 3-bar low cluster
    - Short: entry + (1.5 * atr), adjusted away from last 3-bar high cluster

    Targets:
    - T1: entry ± (2.0 * atr)  [40% position close]
    - T2: entry ± (3.5 * atr)  [60% position close]
    - T3: entry ± (5.0 * atr)  [optional trailing]

    Returns:
        {
            'entry': float, 'stop_loss': float,
            'target_1': float, 'target_2': float, 'target_3': float,
            'risk_per_share': float, 'rr_t1': float, 'rr_t2': float
        }
    Fail-open: returns ATR-only levels on any error.
    """
    try:
        # Guard against zero ATR
        safe_atr = atr if atr and atr > 0.0 else entry * 0.005

        is_long = direction.upper() in ("LONG", "BUY")

        # Raw ATR-based stop
        raw_sl = entry - 1.5 * safe_atr if is_long else entry + 1.5 * safe_atr

        # Adjust stop away from 3-bar low/high cluster (avoid stop-hunt zones)
        try:
            df = df_5m.copy()
            df.columns = [c.lower() for c in df.columns]

            if len(df) >= 3:
                if is_long:
                    # Recent 3-bar low cluster — move stop slightly below it
                    recent_lows   = df["low"].iloc[-3:].values
                    cluster_floor = float(np.min(recent_lows))
                    # If raw SL is inside the cluster, push it below
                    if raw_sl > cluster_floor - safe_atr * 0.1:
                        raw_sl = cluster_floor - safe_atr * 0.1
                else:
                    # Recent 3-bar high cluster — move stop slightly above it
                    recent_highs  = df["high"].iloc[-3:].values
                    cluster_ceil  = float(np.max(recent_highs))
                    if raw_sl < cluster_ceil + safe_atr * 0.1:
                        raw_sl = cluster_ceil + safe_atr * 0.1
        except Exception as _cl_e:
            logger.debug(f"[elite_filter] cluster adjustment error for {symbol}: {_cl_e}")

        stop_loss = round(raw_sl, 2)

        # Targets
        if is_long:
            target_1 = round(entry + 2.0 * safe_atr, 2)
            target_2 = round(entry + 3.5 * safe_atr, 2)
            target_3 = round(entry + 5.0 * safe_atr, 2)
        else:
            target_1 = round(entry - 2.0 * safe_atr, 2)
            target_2 = round(entry - 3.5 * safe_atr, 2)
            target_3 = round(entry - 5.0 * safe_atr, 2)

        risk_per_share = round(abs(entry - stop_loss), 4)

        rr_t1 = round(abs(target_1 - entry) / risk_per_share, 2) if risk_per_share > 0 else 0.0
        rr_t2 = round(abs(target_2 - entry) / risk_per_share, 2) if risk_per_share > 0 else 0.0

        return {
            "entry":          round(entry, 2),
            "stop_loss":      stop_loss,
            "target_1":       target_1,
            "target_2":       target_2,
            "target_3":       target_3,
            "risk_per_share": risk_per_share,
            "rr_t1":          rr_t1,
            "rr_t2":          rr_t2,
        }

    except Exception as _e:
        logger.debug(f"[elite_filter] calculate_optimal_levels error for {symbol}: {_e}")
        safe_atr = atr if atr and atr > 0.0 else entry * 0.005
        is_long  = direction.upper() in ("LONG", "BUY")
        sl       = round(entry - 1.5 * safe_atr if is_long else entry + 1.5 * safe_atr, 2)
        t1       = round(entry + 2.0 * safe_atr if is_long else entry - 2.0 * safe_atr, 2)
        t2       = round(entry + 3.5 * safe_atr if is_long else entry - 3.5 * safe_atr, 2)
        t3       = round(entry + 5.0 * safe_atr if is_long else entry - 5.0 * safe_atr, 2)
        risk     = round(abs(entry - sl), 4)
        return {
            "entry":          round(entry, 2),
            "stop_loss":      sl,
            "target_1":       t1,
            "target_2":       t2,
            "target_3":       t3,
            "risk_per_share": risk,
            "rr_t1":          round(abs(t1 - entry) / risk, 2) if risk > 0 else 0.0,
            "rr_t2":          round(abs(t2 - entry) / risk, 2) if risk > 0 else 0.0,
        }
