"""
phd_strategies.py — 500 PhDs Intelligence Layer v28.0

Six strategies grounded in Nobel-adjacent academic finance.
All fail-open. All zero external APIs. Pure OHLCV mathematics.

Strategy 1: Recent-High Proximity (George & Hwang 2004, JF)
  Most-replicated momentum anomaly in academic finance.
  Stocks within 5% of recent high break out; within 10% of recent low break down.
  Uses 1-hour bars or daily bars as lookback. Score: ±10.
  Ref: George & Hwang (2004) "The 52-Week High and Momentum Investing"

Strategy 2: Amihud Illiquidity Ratio (Amihud 2002, JFM)
  illiq = mean(|log_return_i| / dollar_volume_i) × 10^6
  Very liquid (illiq < 0.001): tight spreads, clean fills → +4
  Very illiquid (illiq > 0.1): huge price impact, avoid → -8
  Ref: Amihud (2002) "Illiquidity and stock returns"

Strategy 3: Chaikin Money Flow (CMF)
  CMF = Σ(money_flow_volume, 20) / Σ(volume, 20)
  money_flow_mult = ((close-low)-(high-close))/(high-low)
  CMF > 0.25: institutional accumulation → +10 LONG
  CMF < -0.25: institutional distribution → +10 SHORT
  Ref: Chaikin (1982), used by every institutional desk

Strategy 4: Kaufman Efficiency Ratio (Kaufman 1995)
  ER = |P[n] - P[0]| / Σ|P[i] - P[i-1]|
  ER → 1.0: perfectly trending → +9. ER → 0.0: pure chop → -7
  Ref: Kaufman (1995) "Smarter Trading"

Strategy 5: Volume-Weighted Momentum Score (VWMS)
  Correlation between bar direction and volume weight.
  Heavy-volume up bars = institutional buying → +8 LONG.
  Distinct from CVD (cumulative) and TOD RVOL (time-slot).
  Ref: Blume, Easley & O'Hara (1994) "Market Statistics and Technical Analysis"

Strategy 6: Microstructure Noise Ratio (Aït-Sahalia & Yu 2009, RFS)
  NR = Var(1-bar returns) / Var(5-bar returns)
  Theoretical floor for RW: NR = 0.2 (Var scales linearly with horizon)
  NR < 0.15: trending, clean signal → +8. NR > 0.40: noisy, avoid → -8
  Ref: Aït-Sahalia & Yu (2009) "High frequency market microstructure noise"
"""

import logging
import math
import time as _time
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_CACHE: dict = {}


def _cget(key: str):
    e = _CACHE.get(key)
    if e and _time.monotonic() < e[1]:
        return e[0]
    return None


def _cset(key: str, value, ttl: float) -> None:
    _CACHE[key] = (value, _time.monotonic() + ttl)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — Recent-High Proximity (George & Hwang 2004, JF)
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_high_proximity_score(
    df_bars,
    current_price: float,
    direction: str,
) -> Tuple[float, str]:
    """
    52-Week High momentum anomaly — most replicated momentum factor.

    Stocks within 5% of recent high tend to break out (LONG signal).
    Stocks within 10% of recent low tend to break down (SHORT signal).

    Args:
        df_bars: Daily or hourly OHLCV DataFrame (whichever has more rows).
        current_price: Current last traded price.
        direction: 'LONG' or 'SHORT'.

    Returns:
        (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_bars is None or len(df_bars) < 15:
            return 0.0, ""

        col_map = {c.lower(): c for c in df_bars.columns}
        high_c = col_map.get("high", "High")
        low_c  = col_map.get("low",  "Low")

        highs = df_bars[high_c].values.astype(float)
        lows  = df_bars[low_c].values.astype(float)

        high_52w = float(np.max(highs))
        low_52w  = float(np.min(lows))

        if high_52w <= 0 or current_price <= 0:
            return 0.0, ""

        hl_range = high_52w - low_52w
        if hl_range < 1e-10:
            return 0.0, ""

        prox_high      = current_price / high_52w            # 1.0 = at 52W high
        prox_low_range = (current_price - low_52w) / hl_range  # 0.0 = at 52W low

        if prox_high >= 0.95 and direction == "LONG":
            return 10.0, f"52W_HIGH_PROX[price={current_price:.2f}_at_{prox_high*100:.1f}%_of_52W_high_LONG(+10)]"
        if prox_high >= 0.90 and direction == "LONG":
            return 6.0,  f"52W_HIGH_PROX[price={current_price:.2f}_at_{prox_high*100:.1f}%_of_52W_high_LONG(+6)]"
        if prox_low_range <= 0.10 and direction == "SHORT":
            return 10.0, f"52W_LOW_PROX[price={current_price:.2f}_at_{prox_low_range*100:.1f}%_above_52W_low_SHORT(+10)]"
        if prox_low_range <= 0.20 and direction == "SHORT":
            return 6.0,  f"52W_LOW_PROX[price={current_price:.2f}_at_{prox_low_range*100:.1f}%_above_52W_low_SHORT(+6)]"
        if prox_high >= 0.95 and direction == "SHORT":
            return -6.0, f"52W_HIGH_PROX[price_at_52W_high_AGAINST_SHORT(-6)]"
        if prox_low_range <= 0.10 and direction == "LONG":
            return -6.0, f"52W_LOW_PROX[price_at_52W_low_AGAINST_LONG(-6)]"

        return 0.0, ""

    except Exception as e:
        logger.debug(f"[suppressed] recent_high_proximity: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — Amihud Illiquidity Ratio (Amihud 2002, JFM)
# ─────────────────────────────────────────────────────────────────────────────

def get_amihud_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Amihud (2002) illiquidity ratio — measures price impact per unit of trading.

    illiq = mean(|log_return_i| / dollar_volume_i) × 10^6

    Low illiquidity = liquid stock = tight spreads, clean fills.
    High illiquidity = avoid (huge price impact on execution).

    Returns:
        (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, ""

        col_map = {c.lower(): c for c in df_5m.columns}
        close_c  = col_map.get("close",  "Close")
        volume_c = col_map.get("volume", "Volume")

        df = df_5m.tail(20)
        closes  = df[close_c].values.astype(float)
        volumes = df[volume_c].values.astype(float)

        # Log-returns between consecutive bars
        log_returns  = np.abs(np.diff(np.log(closes + 1e-10)))
        # Dollar volume per bar (use bar[i+1] price × bar[i+1] volume)
        dollar_vols  = np.maximum(volumes[1:] * closes[1:], 1.0)

        illiq_ratios = log_returns / dollar_vols * 1e6
        illiq        = float(np.mean(illiq_ratios))

        if illiq < 0.001:
            return 4.0,  f"AMIHUD[illiq={illiq:.5f}_highly_liquid_clean_execution(+4)]"
        if illiq < 0.01:
            return 2.0,  f"AMIHUD[illiq={illiq:.5f}_normal_liquidity(+2)]"
        if illiq > 0.1:
            return -8.0, f"AMIHUD[illiq={illiq:.5f}_very_illiquid_AVOID(-8)]"
        if illiq > 0.05:
            return -4.0, f"AMIHUD[illiq={illiq:.5f}_illiquid_elevated_risk(-4)]"

        return 0.0, f"AMIHUD[illiq={illiq:.5f}_acceptable(0)]"

    except Exception as e:
        logger.debug(f"[suppressed] amihud_score: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3 — Chaikin Money Flow (CMF)
# ─────────────────────────────────────────────────────────────────────────────

def get_cmf_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Chaikin Money Flow (1982) — institutional accumulation/distribution proxy.

    CMF = Σ(MFV_i) / Σ(Volume_i)
    MFV_i = ((Close - Low) - (High - Close)) / (High - Low) × Volume

    CMF > 0.25: institutional accumulation → bullish.
    CMF < -0.25: institutional distribution → bearish.

    Returns:
        (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, ""

        col_map = {c.lower(): c for c in df_5m.columns}
        high_c   = col_map.get("high",   "High")
        low_c    = col_map.get("low",    "Low")
        close_c  = col_map.get("close",  "Close")
        volume_c = col_map.get("volume", "Volume")

        df = df_5m.tail(20)
        highs   = df[high_c].values.astype(float)
        lows    = df[low_c].values.astype(float)
        closes  = df[close_c].values.astype(float)
        volumes = df[volume_c].values.astype(float)

        hl_range = np.maximum(highs - lows, 1e-6)
        mfm      = ((closes - lows) - (highs - closes)) / hl_range  # [-1, +1]
        mfv      = mfm * volumes

        total_vol = float(np.sum(volumes))
        if total_vol < 1.0:
            return 0.0, ""

        cmf = float(np.sum(mfv)) / total_vol

        if cmf > 0.25 and direction == "LONG":
            return 10.0, f"CMF[{cmf:.3f}_institutional_accumulation_LONG(+10)]"
        if cmf > 0.15 and direction == "LONG":
            return 6.0,  f"CMF[{cmf:.3f}_moderate_accumulation_LONG(+6)]"
        if cmf < -0.25 and direction == "SHORT":
            return 10.0, f"CMF[{cmf:.3f}_institutional_distribution_SHORT(+10)]"
        if cmf < -0.15 and direction == "SHORT":
            return 6.0,  f"CMF[{cmf:.3f}_moderate_distribution_SHORT(+6)]"
        if cmf > 0.15 and direction == "SHORT":
            return -8.0, f"CMF[{cmf:.3f}_accumulation_AGAINST_SHORT(-8)]"
        if cmf < -0.15 and direction == "LONG":
            return -8.0, f"CMF[{cmf:.3f}_distribution_AGAINST_LONG(-8)]"

        return 0.0, f"CMF[{cmf:.3f}_neutral(0)]"

    except Exception as e:
        logger.debug(f"[suppressed] cmf_score: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 4 — Kaufman Efficiency Ratio (Kaufman 1995)
# ─────────────────────────────────────────────────────────────────────────────

def get_efficiency_ratio_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Kaufman (1995) Efficiency Ratio — measures how efficiently price moves.

    ER = |P[n] - P[0]| / Σ|P[i] - P[i-1]|

    ER = 1.0: perfectly trending (all movement in one direction).
    ER = 0.0: pure random walk / chop (maximum inefficiency).

    High ER → strong directional trend → signal is reliable.
    Low ER → choppy market → any signal is noise.

    Returns:
        (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, ""

        col_map = {c.lower(): c for c in df_5m.columns}
        close_c = col_map.get("close", "Close")

        closes = df_5m[close_c].values.astype(float)[-20:]

        net_change  = abs(float(closes[-1]) - float(closes[0]))
        bar_changes = np.abs(np.diff(closes))
        sum_changes = float(np.sum(bar_changes))

        er = net_change / max(sum_changes, 1e-10)

        if er > 0.70:
            return 9.0,  f"EFF_RATIO[ER={er:.3f}_strongly_trending_high_confidence(+9)]"
        if er > 0.55:
            return 5.0,  f"EFF_RATIO[ER={er:.3f}_moderate_trend(+5)]"
        if er < 0.20:
            return -7.0, f"EFF_RATIO[ER={er:.3f}_pure_chop_no_edge(-7)]"
        if er < 0.35:
            return -3.0, f"EFF_RATIO[ER={er:.3f}_slightly_choppy(-3)]"

        return 0.0, f"EFF_RATIO[ER={er:.3f}_neutral(0)]"

    except Exception as e:
        logger.debug(f"[suppressed] efficiency_ratio_score: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 5 — Volume-Weighted Momentum Score (VWMS)
# ─────────────────────────────────────────────────────────────────────────────

def get_vwms_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Volume-Weighted Momentum Score — Blume, Easley & O'Hara (1994).

    Measures whether heavy-volume bars move in the signal direction.
    High-volume up bars = institutional buying conviction.
    Distinct from CVD (cumulative) and TOD RVOL (time-of-day).

    vwms = Σ(bar_return × vol_weight) / Σ(vol_weight)

    Returns:
        (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 15:
            return 0.0, ""

        col_map  = {c.lower(): c for c in df_5m.columns}
        close_c  = col_map.get("close",  "Close")
        volume_c = col_map.get("volume", "Volume")
        open_c   = col_map.get("open",   None)

        df = df_5m.tail(15)
        closes  = df[close_c].values.astype(float)
        volumes = df[volume_c].values.astype(float)

        # Bar returns: prefer open→close if open column exists
        if open_c is not None and open_c in df.columns:
            opens       = df[open_c].values.astype(float)
            bar_returns = (closes - opens) / np.maximum(opens, 1e-6)
        else:
            bar_returns = np.diff(closes) / np.maximum(closes[:-1], 1e-6)
            closes      = closes[1:]
            volumes     = volumes[1:]

        mean_vol    = float(np.mean(volumes))
        vol_weights = volumes / max(mean_vol, 1.0)

        total_weight = float(np.sum(vol_weights))
        if total_weight < 1e-10:
            return 0.0, ""

        vwms = float(np.sum(bar_returns * vol_weights)) / total_weight

        if vwms > 0.003 and direction == "LONG":
            return 8.0,  f"VWMS[{vwms:.5f}_strong_inst_buying_LONG(+8)]"
        if vwms > 0.001 and direction == "LONG":
            return 4.0,  f"VWMS[{vwms:.5f}_mild_buying_LONG(+4)]"
        if vwms < -0.003 and direction == "SHORT":
            return 8.0,  f"VWMS[{vwms:.5f}_strong_inst_selling_SHORT(+8)]"
        if vwms < -0.001 and direction == "SHORT":
            return 4.0,  f"VWMS[{vwms:.5f}_mild_selling_SHORT(+4)]"
        if vwms > 0.002 and direction == "SHORT":
            return -6.0, f"VWMS[{vwms:.5f}_buying_pressure_AGAINST_SHORT(-6)]"
        if vwms < -0.002 and direction == "LONG":
            return -6.0, f"VWMS[{vwms:.5f}_selling_pressure_AGAINST_LONG(-6)]"

        return 0.0, f"VWMS[{vwms:.5f}_neutral(0)]"

    except Exception as e:
        logger.debug(f"[suppressed] vwms_score: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 6 — Microstructure Noise Ratio (Aït-Sahalia & Yu 2009, RFS)
# ─────────────────────────────────────────────────────────────────────────────

def get_noise_ratio_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Microstructure Noise Ratio — Aït-Sahalia & Yu (2009, Review of Financial Studies).

    NR = Var(1-bar returns) / Var(5-bar returns)

    For a pure random walk: Var scales linearly with horizon → NR = 0.2.
    NR << 0.2: variance doesn't scale → price is trending (persistent).
    NR >> 0.2: excess short-horizon variance → microstructure noise dominant.

    NR < 0.15: clean trending signal → high confidence.
    NR > 0.40: noisy, signals unreliable → penalise.

    Returns:
        (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 30:
            return 0.0, ""

        col_map = {c.lower(): c for c in df_5m.columns}
        close_c = col_map.get("close", "Close")

        closes     = df_5m[close_c].values.astype(float)[-30:]
        log_closes = np.log(closes + 1e-10)
        returns_1  = np.diff(log_closes)   # 29 one-bar log returns

        # Non-overlapping 5-bar returns
        n5 = len(returns_1) // 5
        if n5 < 4:
            return 0.0, ""

        r5 = np.array([returns_1[i * 5:(i + 1) * 5].sum() for i in range(n5)])

        var_1 = float(np.var(returns_1))
        var_5 = float(np.var(r5))

        nr = var_1 / max(var_5, 1e-14)

        if nr < 0.15:
            return 8.0,  f"NOISE_RATIO[NR={nr:.3f}_trending_clean_signal(+8)]"
        if nr < 0.20:
            return 4.0,  f"NOISE_RATIO[NR={nr:.3f}_low_noise(+4)]"
        if nr > 0.40:
            return -8.0, f"NOISE_RATIO[NR={nr:.3f}_high_noise_unreliable(-8)]"
        if nr > 0.30:
            return -4.0, f"NOISE_RATIO[NR={nr:.3f}_elevated_noise(-4)]"

        return 0.0, f"NOISE_RATIO[NR={nr:.3f}_normal(0)]"

    except Exception as e:
        logger.debug(f"[suppressed] noise_ratio_score: {e}")
        return 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Master Aggregator
# ─────────────────────────────────────────────────────────────────────────────

def get_phd_score_boost(
    df_5m,
    df_1h,
    df_daily,
    direction: str,
    current_price: float,
) -> Tuple[float, str]:
    """
    Aggregate all 6 PhD-grade academic finance strategies.

    Args:
        df_5m:         5-minute OHLCV DataFrame.
        df_1h:         1-hour OHLCV DataFrame.
        df_daily:      Daily OHLCV DataFrame (may be None).
        direction:     'LONG' or 'SHORT'.
        current_price: Current last traded price.

    Returns:
        (total_delta: float, combined_reason: str)
        All strategies are fail-open — errors return delta=0.
    """
    total_delta = 0.0
    reasons: list = []

    # Strategy 1: Recent-High Proximity
    # Prefer daily bars (more lookback); fall back to 1-hour
    try:
        if df_daily is not None and len(df_daily) >= 15:
            _bars_s1 = df_daily
        else:
            _bars_s1 = df_1h
        s1, r1 = get_recent_high_proximity_score(_bars_s1, current_price, direction)
        if s1 != 0.0:
            total_delta += s1
            if r1:
                reasons.append(r1)
    except Exception as e:
        logger.debug(f"[suppressed] phd S1 recent_high_proximity: {e}")

    # Strategy 2: Amihud Illiquidity Ratio
    try:
        s2, r2 = get_amihud_score(df_5m, direction)
        if s2 != 0.0:
            total_delta += s2
            if r2:
                reasons.append(r2)
    except Exception as e:
        logger.debug(f"[suppressed] phd S2 amihud: {e}")

    # Strategy 3: Chaikin Money Flow
    try:
        s3, r3 = get_cmf_score(df_5m, direction)
        if s3 != 0.0:
            total_delta += s3
            if r3:
                reasons.append(r3)
    except Exception as e:
        logger.debug(f"[suppressed] phd S3 cmf: {e}")

    # Strategy 4: Kaufman Efficiency Ratio
    try:
        s4, r4 = get_efficiency_ratio_score(df_5m, direction)
        if s4 != 0.0:
            total_delta += s4
            if r4:
                reasons.append(r4)
    except Exception as e:
        logger.debug(f"[suppressed] phd S4 efficiency_ratio: {e}")

    # Strategy 5: Volume-Weighted Momentum Score
    try:
        s5, r5 = get_vwms_score(df_5m, direction)
        if s5 != 0.0:
            total_delta += s5
            if r5:
                reasons.append(r5)
    except Exception as e:
        logger.debug(f"[suppressed] phd S5 vwms: {e}")

    # Strategy 6: Microstructure Noise Ratio
    try:
        s6, r6 = get_noise_ratio_score(df_5m, direction)
        if s6 != 0.0:
            total_delta += s6
            if r6:
                reasons.append(r6)
    except Exception as e:
        logger.debug(f"[suppressed] phd S6 noise_ratio: {e}")

    combined_reason = " | ".join(reasons)
    logger.debug(
        f"[phd_v28] {direction}: delta={total_delta:+.1f} strategies_fired={len(reasons)}"
    )
    return float(total_delta), combined_reason
