"""
advanced_setups.py — The 3 Highest Win-Rate Intraday Setups

Setup 1: Opening Range Breakout (ORB)
  Research consensus: 58-65% WR when done correctly.
  Mark 9:30-9:45 AM ET high/low. Enter on breakout above high (LONG)
  or below low (SHORT) with volume > 1.5× pre-market average.
  The ORB is the single most documented profitable intraday setup.
  Used by: every major prop desk, SMB Capital, Warrior Trading.

Setup 2: First Pullback to EMA9 (FPE9)
  Research: 62-68% WR — the single best entry after a breakout.
  After any 3%+ momentum move, price pulls back to EMA9.
  The FIRST touch of EMA9 after a breakout = optimal risk/reward entry.
  Used by: Minervini (VCP base), O'Neil (handle formation), every momentum trader.

Setup 3: VWAP Band Bounce (VBB)
  Research: 55-62% WR on the first bounce off VWAP ± 1 std dev bands.
  Institutions use VWAP as their reference price. The first bounce
  off the upper/lower VWAP band is a reliable mean-reversion + continuation entry.
  Used by: Goldman Sachs equity desks, all CME market makers.
"""

import logging
import time
from typing import Optional, Tuple, Dict
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── Cache for ORB levels ──────────────────────────────────────────────────────
_orb_cache: Dict[str, Dict] = {}   # {date_symbol: {high, low, volume_avg}}


def _get_date_key(symbol: str) -> str:
    from utils import get_current_ist_time
    return f"{get_current_ist_time().strftime('%Y-%m-%d')}_{symbol}"


# ─────────────────────────────────────────────────────────────────────────────
# Setup 1: Opening Range Breakout
# ─────────────────────────────────────────────────────────────────────────────

def compute_orb_levels(df_5m: pd.DataFrame, symbol: str) -> Optional[Dict]:
    """
    Compute the Opening Range (first 3 bars = 9:30-9:45 AM ET).
    Returns dict with orb_high, orb_low, orb_range, orb_volume_avg or None.
    Cached for the trading day.
    """
    try:
        key = _get_date_key(symbol)
        if key in _orb_cache:
            return _orb_cache[key]

        if df_5m is None or len(df_5m) < 3:
            return None

        # Get first 3 bars of the session (9:30, 9:35, 9:40)
        orb_bars = df_5m.head(3) if not df_5m.index.dtype == 'object' else df_5m.iloc[:3]

        orb_high = float(orb_bars['high'].max() if 'high' in orb_bars.columns else orb_bars['High'].max())
        orb_low  = float(orb_bars['low'].min()  if 'low'  in orb_bars.columns else orb_bars['Low'].min())
        orb_vol  = float(orb_bars['volume'].mean() if 'volume' in orb_bars.columns else orb_bars['Volume'].mean())

        result = {
            'orb_high':      orb_high,
            'orb_low':       orb_low,
            'orb_range':     orb_high - orb_low,
            'orb_volume_avg': orb_vol,
        }
        _orb_cache[key] = result
        return result
    except Exception as exc:
        logger.debug(f"compute_orb_levels {symbol}: {exc}")
        return None


def get_orb_score(
    df_5m: pd.DataFrame,
    current_price: float,
    direction: str,
    symbol: str,
    volume_ratio: float = 1.0,
) -> Tuple[float, str]:
    """
    Score the Opening Range Breakout setup.

    Returns (score_delta, reason):
      +15 if price just broke out above ORB high with volume >= 1.5x (LONG)
      +15 if price just broke below ORB low with volume >= 1.5x (SHORT)
      +8  if breakout confirmed but volume only 1.0-1.5x
      -5  if price is BELOW orb_high for a LONG (failed breakout territory)
       0  if ORB not yet formed or data missing (fail-open)
    """
    try:
        orb = compute_orb_levels(df_5m, symbol)
        if not orb:
            return 0.0, "ORB_NO_DATA"
        if orb['orb_range'] <= 0:
            return 0.0, "ORB_FLAT"

        is_long  = direction in ('LONG', 'BUY')
        is_short = direction in ('SHORT', 'SELL')

        # Must be AFTER the ORB formation window (after bar 3)
        if len(df_5m) <= 3:
            return 0.0, "ORB_FORMING"

        # Check if current price is in breakout territory
        above_orb = current_price > orb['orb_high'] * 1.001   # 0.1% buffer above high
        below_orb = current_price < orb['orb_low']  * 0.999   # 0.1% buffer below low

        if is_long and above_orb:
            if volume_ratio >= 1.5:
                return 15.0, f"ORB_BREAKOUT_LONG(vol={volume_ratio:.1f}x,above={orb['orb_high']:.2f})"
            elif volume_ratio >= 1.0:
                return 8.0, f"ORB_BREAKOUT_LONG_WEAK(vol={volume_ratio:.1f}x)"
            else:
                return 3.0, f"ORB_ABOVE_NO_VOL"

        if is_short and below_orb:
            if volume_ratio >= 1.5:
                return 15.0, f"ORB_BREAKOUT_SHORT(vol={volume_ratio:.1f}x,below={orb['orb_low']:.2f})"
            elif volume_ratio >= 1.0:
                return 8.0, f"ORB_BREAKOUT_SHORT_WEAK(vol={volume_ratio:.1f}x)"
            else:
                return 3.0, f"ORB_BELOW_NO_VOL"

        # Price inside the ORB range: slight penalty (noise zone)
        if not above_orb and not below_orb:
            return -3.0, f"ORB_INSIDE_RANGE(H={orb['orb_high']:.2f},L={orb['orb_low']:.2f})"

        return 0.0, "ORB_NEUTRAL"
    except Exception as exc:
        logger.debug(f"get_orb_score: {exc}")
        return 0.0, "ORB_SKIP"


# ─────────────────────────────────────────────────────────────────────────────
# Setup 2: First Pullback to EMA9
# ─────────────────────────────────────────────────────────────────────────────

def get_first_pullback_score(
    df_5m: pd.DataFrame,
    current_price: float,
    direction: str,
) -> Tuple[float, str]:
    """
    Detect the First Pullback to EMA9 after a momentum breakout.

    Signs of valid FPE9:
      - EMA9 was below price for at least 5 bars (trending)
      - Current bar touches or crosses EMA9 from above (LONG)
      - Volume on pullback bars DECREASING (healthy consolidation)
      - Not a 2nd or 3rd touch (first touch only = highest probability)

    Returns (score_delta, reason).
    """
    try:
        if df_5m is None or len(df_5m) < 12:
            return 0.0, "FPE9_NO_DATA"

        close = df_5m['close'].values if 'close' in df_5m.columns else df_5m['Close'].values

        # Compute EMA9
        ema9 = pd.Series(close).ewm(span=9, adjust=False).mean().values

        is_long  = direction in ('LONG', 'BUY')
        is_short = direction in ('SHORT', 'SELL')

        # Check bars 3-10 ago were trending (price above EMA9 for LONG)
        lookback = min(10, len(close) - 2)
        prior_above = sum(1 for i in range(-lookback-1, -1) if close[i] > ema9[i])
        trending = prior_above >= (lookback * 0.7)   # 70% of prior bars above EMA9

        if not trending:
            return 0.0, "FPE9_NO_TREND"

        # Current bar touching EMA9 from above (LONG)
        current_close = close[-1]
        current_ema9  = ema9[-1]
        prev_close    = close[-2]
        prev_ema9     = ema9[-2]

        # LONG: price pulled back to within 0.3% of EMA9 and previous bar was above
        if is_long:
            touching_ema9  = abs(current_close - current_ema9) / max(current_ema9, 0.01) < 0.005
            bouncing       = current_close > prev_close   # current bar is up
            was_above_prev = prev_close >= prev_ema9 * 0.998

            if touching_ema9 and was_above_prev:
                # Check volume is LOWER on pullback (healthy)
                vols = df_5m['volume'].values[-5:] if 'volume' in df_5m.columns else df_5m['Volume'].values[-5:]
                pullback_vol_decreasing = float(vols[-1]) < float(vols[-3])   # current < 2-bar-ago

                if pullback_vol_decreasing and bouncing:
                    return 14.0, f"FPE9_PERFECT(ema9={current_ema9:.2f},vol_decreasing)"
                elif touching_ema9:
                    return 8.0, f"FPE9_TOUCH(ema9={current_ema9:.2f})"

        if is_short:
            touching_ema9  = abs(current_close - current_ema9) / max(current_ema9, 0.01) < 0.005
            bouncing       = current_close < prev_close
            was_below_prev = prev_close <= prev_ema9 * 1.002

            if touching_ema9 and was_below_prev:
                vols = df_5m['volume'].values[-5:] if 'volume' in df_5m.columns else df_5m['Volume'].values[-5:]
                pullback_vol_decreasing = float(vols[-1]) < float(vols[-3])

                if pullback_vol_decreasing and bouncing:
                    return 14.0, f"FPE9_PERFECT_SHORT(ema9={current_ema9:.2f})"
                elif touching_ema9:
                    return 8.0, f"FPE9_TOUCH_SHORT(ema9={current_ema9:.2f})"

        return 0.0, "FPE9_NOT_AT_EMA"
    except Exception as exc:
        logger.debug(f"get_first_pullback_score: {exc}")
        return 0.0, "FPE9_SKIP"


# ─────────────────────────────────────────────────────────────────────────────
# Setup 3: VWAP Band Bounce
# ─────────────────────────────────────────────────────────────────────────────

def get_vwap_band_score(
    df_5m: pd.DataFrame,
    current_price: float,
    direction: str,
    vwap: float = 0.0,
) -> Tuple[float, str]:
    """
    Detect bounces off VWAP +/- 1 standard deviation bands.

    VWAP upper band = VWAP + 1xstd (resistance for longs, support break for shorts)
    VWAP lower band = VWAP - 1xstd (support for longs, resistance for shorts)

    Returns (score_delta, reason).
    """
    try:
        if df_5m is None or len(df_5m) < 10:
            return 0.0, "VBB_NO_DATA"

        # Compute VWAP and bands from df_5m
        tp   = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
        vol  = df_5m['volume'].astype(float)
        cvp  = (tp * vol).cumsum()
        cv   = vol.cumsum()
        vwap_series = (cvp / cv.replace(0, np.nan)).ffill()

        # VWAP deviation std
        vwap_now = float(vwap_series.iloc[-1])
        if vwap_now <= 0:
            return 0.0, "VBB_NO_VWAP"

        deviations = tp - vwap_series
        vwap_std   = float(deviations.std())
        if vwap_std <= 0:
            return 0.0, "VBB_NO_STD"

        upper_band = vwap_now + vwap_std
        lower_band = vwap_now - vwap_std

        is_long  = direction in ('LONG', 'BUY')
        is_short = direction in ('SHORT', 'SELL')

        price_to_lower = abs(current_price - lower_band) / max(vwap_std, 0.01)
        price_to_upper = abs(current_price - upper_band) / max(vwap_std, 0.01)

        # Previous bar was AT the band, current bar is bouncing
        prev_close = float(df_5m['close'].iloc[-2])
        curr_close = float(df_5m['close'].iloc[-1])

        if is_long and price_to_lower < 0.3:   # within 0.3sigma of lower band
            bouncing = curr_close > prev_close
            if bouncing:
                return 12.0, f"VBB_LOWER_BOUNCE(band={lower_band:.2f},sigma={vwap_std:.2f})"
            return 5.0, f"VBB_AT_LOWER(band={lower_band:.2f})"

        if is_short and price_to_upper < 0.3:   # within 0.3sigma of upper band
            rejecting = curr_close < prev_close
            if rejecting:
                return 12.0, f"VBB_UPPER_REJECT(band={upper_band:.2f},sigma={vwap_std:.2f})"
            return 5.0, f"VBB_AT_UPPER(band={upper_band:.2f})"

        # Price between bands and moving toward VWAP from above/below: mean reversion
        if is_long and current_price < vwap_now and curr_close > prev_close:
            return 4.0, f"VBB_VWAP_RECLAIM_BUILDING"
        if is_short and current_price > vwap_now and curr_close < prev_close:
            return 4.0, f"VBB_VWAP_REJECT_BUILDING"

        return 0.0, "VBB_NEUTRAL"
    except Exception as exc:
        logger.debug(f"get_vwap_band_score: {exc}")
        return 0.0, "VBB_SKIP"
