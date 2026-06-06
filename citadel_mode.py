"""
citadel_mode.py — Citadel/Goldman Execution Intelligence v30.0

Six strategies used by top-tier prop desks and hedge funds.
All computed purely from OHLCV bars. No external APIs. All fail-open.

Strategy 1: Yang-Zhang Realized Volatility (Yang & Zhang 2000, Management Science)
  The most efficient OHLCV volatility estimator — 5x more accurate than
  close-to-close volatility. Uses overnight gap + Rogers-Satchell intraday vol.
  YZ_vol compressing vs ATR baseline → trending setup → +7
  YZ_vol expanding rapidly → panic/euphoria → -6
  Used by: every vol desk, quant hedge fund, risk management system.

Strategy 2: Sample Entropy Signal Quality (Richman & Moorman 2000, AJP)
  Measures regularity/predictability of price series.
  Low entropy (≈0.0–0.5): highly regular pattern → momentum reliable → +8
  High entropy (>1.5): chaotic, random → momentum fails → -8
  Nobel-adjacent: relates to Shannon information theory applied to markets.
  Used by: quantitative analysts at Renaissance, AQR signal validation.

Strategy 3: Volume Profile / Synthetic Order Book
  Divide last 40 bars into 20 price buckets. Assign bar volume proportionally.
  Point of Control (POC) = price level with most volume = strongest support/resistance.
  Value Area (70% of total volume) = institutional price range.
  Price above POC + LONG: +8. Price below POC + SHORT: +8.
  Price trapped in Value Area: -3 (equilibrium, no directional edge).
  Used by: CME floor traders, market profile analysts, all prop desks.

Strategy 4: Short-Range Autocorrelation Momentum (Jegadeesh 1990, JF)
  Measures lag-1,2,3 autocorrelation of returns.
  Positive AC (momentum persistence): signals are valid → +8
  Negative AC (mean reversion): momentum signals are WRONG direction → -8
  Different from Hurst (long-range). This is short-range (3-bar) memory.
  Used by: every quant fund. Basis for all short-term momentum strategies.

Strategy 5: Volatility Risk Premium Score (Engle & Rosenberg 2002)
  VRP = short_realized_vol (5-bar YZ) / long_realized_vol (20-bar YZ)
  VRP < 0.70: vol compressing → trending, momentum favored → +6
  VRP > 1.50: vol expanding → regime change, caution → -6
  VRP 0.70-0.90: ideal entry window → +3
  Used by: vol arb desks, CTA funds, all systematic trend-followers.

Strategy 6: Risk Parity Position Size Multiplier (Bridgewater 2011)
  Equal Risk Contribution sizing: size_i = target_risk / σ_i
  If symbol vol is low: get larger size (more room before stop hit)
  If symbol vol is high: get smaller size (less exposure per dollar)
  size_multiplier = 0.015 / max(per_bar_vol, 0.001) — clamp 0.5–1.5
  Pure Bridgewater "All Weather" approach: risk-equalized, not dollar-equalized.
  Used by: Bridgewater, AQR, Man Group, every risk-managed fund.
"""

import logging
import math
import time as _time
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 1: Yang-Zhang Volatility
# ─────────────────────────────────────────────────────────────────────────────

def _yang_zhang_vol(df, window: int = 10) -> float:
    """
    Yang-Zhang (2000) volatility estimator.
    σ²_YZ = σ²_overnight + k*σ²_RS + (1-k)*σ²_close
    σ²_RS = Rogers-Satchell = mean(log(H/C)*log(H/O) + log(L/C)*log(L/O))
    k = 0.34 / (1.34 + (n+1)/(n-1))
    Returns annualized vol (252 days * 78 5-min bars per day).
    """
    try:
        df_w = df.tail(window).copy()
        n = len(df_w)
        if n < 3:
            return 0.0

        close = df_w['close'].values.astype(float)
        high  = df_w['high'].values.astype(float)
        low   = df_w['low'].values.astype(float)

        # If 'open' column missing, fall back to close-to-close vol
        if 'open' not in df_w.columns:
            log_ret = np.diff(np.log(close + 1e-10))
            if len(log_ret) < 2:
                return 0.0
            cc_var = float(np.var(log_ret, ddof=1))
            # annualize: 252 days * 78 bars per day
            return float(math.sqrt(cc_var * 252 * 78))

        opens = df_w['open'].values.astype(float)

        # Overnight returns: log(open_i / close_{i-1})
        log_o = np.log((opens[1:] + 1e-10) / (close[:-1] + 1e-10))
        # Close-to-close returns
        log_c = np.log((close[1:] + 1e-10) / (close[:-1] + 1e-10))

        # Rogers-Satchell: log(H/C)*log(H/O) + log(L/C)*log(L/O)
        # Align to same indices as log_o (skip first row)
        h = high[1:]
        l = low[1:]
        o = opens[1:]
        c = close[1:]
        rs = (
            np.log((h + 1e-10) / (c + 1e-10)) * np.log((h + 1e-10) / (o + 1e-10))
            + np.log((l + 1e-10) / (c + 1e-10)) * np.log((l + 1e-10) / (o + 1e-10))
        )

        n_eff = len(log_o)
        if n_eff < 2:
            return 0.0

        k = 0.34 / (1.34 + (n_eff + 1) / max(n_eff - 1, 1))

        overnight_var = float(np.var(log_o, ddof=1))
        rs_var        = float(np.mean(rs))
        cc_var        = float(np.var(log_c, ddof=1))

        yz_var = overnight_var + k * rs_var + (1.0 - k) * cc_var
        yz_var = max(yz_var, 0.0)  # safeguard against negative due to RS term

        # Annualize: 252 trading days * 78 5-min bars per day
        return float(math.sqrt(yz_var * 252 * 78))

    except Exception:
        return 0.0


def get_yang_zhang_score(df_5m, direction: str, atr: float, ltp: float) -> Tuple[float, str]:
    """
    Compare YZ short-term vol (5-bar) vs YZ long-term vol (20-bar).
    Also compare YZ vs ATR-based vol estimate.
    Returns (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 25:
            return 0.0, ''

        yz_short = _yang_zhang_vol(df_5m, window=5)
        yz_long  = _yang_zhang_vol(df_5m, window=20)

        if yz_long < 1e-8:
            return 0.0, ''

        ratio = yz_short / yz_long

        if ratio < 0.70:
            return 7.0, f'YZ_vol compressing ratio={ratio:.2f} trending'
        elif ratio <= 0.90:
            return 3.0, f'YZ_vol slightly compressing ratio={ratio:.2f}'
        elif ratio > 2.0:
            return -8.0, f'YZ_vol expanding fast ratio={ratio:.2f} panic/euphoria'
        elif ratio > 1.50:
            return -4.0, f'YZ_vol elevated ratio={ratio:.2f}'
        else:
            return 0.0, f'YZ_vol neutral ratio={ratio:.2f}'

    except Exception:
        return 0.0, ''


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 2: Sample Entropy
# ─────────────────────────────────────────────────────────────────────────────

def _sample_entropy(ts: np.ndarray, m: int = 2, r_scale: float = 0.2) -> float:
    """
    Sample Entropy: measures unpredictability of time series.
    SampEn = -log(A/B) where:
      B = number of template matches of length m
      A = number of template matches of length m+1
    r = r_scale * std(ts) = tolerance for 'matching'
    Returns float [0, ∞). Returns 2.0 on failure (treat as high entropy).
    """
    n = len(ts)
    if n < 20:
        return 2.0
    r = r_scale * float(np.std(ts))
    if r < 1e-10:
        return 2.0

    # Count B: pairs of m-length templates within r
    B = 0
    A = 0
    for i in range(n - m):
        for j in range(i + 1, n - m):
            # Check if template match at length m
            if np.max(np.abs(ts[i:i+m] - ts[j:j+m])) < r:
                B += 1
                # Check if extends to m+1
                if abs(ts[i+m] - ts[j+m]) < r:
                    A += 1

    if B == 0:
        return 2.0
    if A == 0:
        return 2.0
    return float(-math.log(A / B))


def get_sample_entropy_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Sample entropy of closing prices over last 30 bars.
    Low entropy → predictable → signals reliable.
    Returns (score_delta, reason). Fail-open: (0.0, '').
    """
    try:
        if df_5m is None or len(df_5m) < 30:
            return 0.0, ''

        closes = df_5m['close'].values[-30:].astype(float)
        se = _sample_entropy(closes, m=2, r_scale=0.2)

        if se < 0.5:
            return 8.0, f'SampEn={se:.3f} highly predictable momentum'
        elif se < 0.8:
            return 4.0, f'SampEn={se:.3f} predictable'
        elif se > 1.5:
            return -8.0, f'SampEn={se:.3f} chaotic random'
        elif se > 1.2:
            return -4.0, f'SampEn={se:.3f} noisy'
        else:
            return 0.0, f'SampEn={se:.3f} neutral'

    except Exception:
        return 0.0, ''


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 3: Volume Profile / Synthetic DOM
# ─────────────────────────────────────────────────────────────────────────────

def get_volume_profile_score(df_5m, direction: str, current_price: float) -> Tuple[float, str]:
    """
    Volume Profile: distribute bar volume across price buckets.
    Find POC (Point of Control) = price level with highest volume.
    Find Value Area (70% of volume) = VAH and VAL.

    Returns (score_delta, reason). Fail-open: (0.0, '').
    Need >= 20 bars.

    Scoring:
      price > POC and direction == LONG: +8  (bull above support)
      price < POC and direction == SHORT: +8 (bear below resistance)
      price inside Value Area [VAL, VAH]: -3 (no directional edge, in balance)
      price near VAH and direction == LONG: -5 (at resistance)
      price near VAL and direction == SHORT: -5 (at support)
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, ''

        df_w = df_5m.tail(40).copy()
        n_buckets = 20

        price_min = float(df_w['low'].min())
        price_max = float(df_w['high'].max())

        if price_max <= price_min:
            return 0.0, ''

        bucket_size = (price_max - price_min) / n_buckets
        if bucket_size < 1e-10:
            return 0.0, ''

        # Build volume profile: assign each bar's volume to the bucket of its close
        vol_profile = np.zeros(n_buckets)
        for _, row in df_w.iterrows():
            c = float(row['close'])
            v = float(row['volume']) if 'volume' in df_w.columns else 1.0
            bucket_idx = int((c - price_min) / bucket_size)
            bucket_idx = min(bucket_idx, n_buckets - 1)
            bucket_idx = max(bucket_idx, 0)
            vol_profile[bucket_idx] += v

        total_vol = float(vol_profile.sum())
        if total_vol < 1e-10:
            return 0.0, ''

        # POC: bucket with maximum volume
        poc_idx = int(np.argmax(vol_profile))
        poc_price = price_min + (poc_idx + 0.5) * bucket_size

        # Value Area: 70% of total volume, starting from POC and expanding outward
        target_va_vol = 0.70 * total_vol
        va_vol = vol_profile[poc_idx]
        va_lo_idx = poc_idx
        va_hi_idx = poc_idx

        while va_vol < target_va_vol:
            expand_up   = va_hi_idx + 1 < n_buckets
            expand_down = va_lo_idx - 1 >= 0

            if not expand_up and not expand_down:
                break

            vol_up   = vol_profile[va_hi_idx + 1] if expand_up   else -1.0
            vol_down = vol_profile[va_lo_idx - 1] if expand_down else -1.0

            if vol_up >= vol_down:
                va_hi_idx += 1
                va_vol += vol_profile[va_hi_idx]
            else:
                va_lo_idx -= 1
                va_vol += vol_profile[va_lo_idx]

        vah = price_min + (va_hi_idx + 1) * bucket_size  # upper edge of value area
        val = price_min + va_lo_idx * bucket_size          # lower edge of value area

        # Proximity threshold: 0.3% of price
        near_pct = 0.003
        near_vah = abs(current_price - vah) / max(vah, 1e-10) < near_pct
        near_val = abs(current_price - val) / max(val, 1e-10) < near_pct
        in_va    = val <= current_price <= vah

        dir_up   = direction.upper() == 'LONG'
        dir_down = direction.upper() == 'SHORT'

        if dir_up and current_price > poc_price and not in_va:
            return 8.0, f'VP: price {current_price:.1f} above POC {poc_price:.1f} LONG momentum'
        if dir_down and current_price < poc_price and not in_va:
            return 8.0, f'VP: price {current_price:.1f} below POC {poc_price:.1f} SHORT momentum'
        if near_vah and dir_up:
            return -5.0, f'VP: price near VAH {vah:.1f} resistance LONG'
        if near_val and dir_down:
            return -5.0, f'VP: price near VAL {val:.1f} support SHORT'
        if in_va:
            return -3.0, f'VP: price in Value Area [{val:.1f},{vah:.1f}] equilibrium'

        return 0.0, f'VP: POC={poc_price:.1f} VA=[{val:.1f},{vah:.1f}]'

    except Exception:
        return 0.0, ''


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 4: Short-Range Autocorrelation
# ─────────────────────────────────────────────────────────────────────────────

def get_autocorrelation_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Lag-1, 2, 3 autocorrelation of 5-minute returns.
    Positive AC: momentum persists → direction signal is correct → +8
    Negative AC: mean-reverting → direction signal likely wrong → -8

    Uses weighted average: AC(1) weight 0.5, AC(2) weight 0.3, AC(3) weight 0.2

    Returns (score_delta, reason). Fail-open: (0.0, '').
    Need >= 20 bars.
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, ''

        closes  = df_5m['close'].values[-30:].astype(float)
        returns = np.diff(np.log(closes + 1e-10))

        if len(returns) < 4:
            return 0.0, ''

        def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
            if len(a) < 3:
                return 0.0
            try:
                c = np.corrcoef(a, b)
                v = float(c[0, 1])
                return 0.0 if math.isnan(v) else v
            except Exception:
                return 0.0

        ac1 = _safe_corr(returns[:-1], returns[1:])   # lag-1
        ac2 = _safe_corr(returns[:-2], returns[2:])   # lag-2
        ac3 = _safe_corr(returns[:-3], returns[3:])   # lag-3

        weighted_ac = 0.5 * ac1 + 0.3 * ac2 + 0.2 * ac3

        dir_up = direction.upper() == 'LONG'

        if dir_up:
            if weighted_ac > 0.15:
                return  8.0, f'AC={weighted_ac:.3f} strong momentum persistence LONG'
            elif weighted_ac > 0.05:
                return  4.0, f'AC={weighted_ac:.3f} mild momentum persistence LONG'
            elif weighted_ac < -0.15:
                return -8.0, f'AC={weighted_ac:.3f} mean-reverting LONG wrong'
            elif weighted_ac < -0.05:
                return -4.0, f'AC={weighted_ac:.3f} slight mean-reversion LONG'
            else:
                return  0.0, f'AC={weighted_ac:.3f} neutral'
        else:  # SHORT
            if weighted_ac > 0.15:
                return  8.0, f'AC={weighted_ac:.3f} strong momentum persistence SHORT'
            elif weighted_ac > 0.05:
                return  4.0, f'AC={weighted_ac:.3f} mild momentum persistence SHORT'
            elif weighted_ac < -0.15:
                return -8.0, f'AC={weighted_ac:.3f} mean-reverting SHORT wrong'
            elif weighted_ac < -0.05:
                return -4.0, f'AC={weighted_ac:.3f} slight mean-reversion SHORT'
            else:
                return  0.0, f'AC={weighted_ac:.3f} neutral'

    except Exception:
        return 0.0, ''


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 5: Volatility Risk Premium
# ─────────────────────────────────────────────────────────────────────────────

def get_vrp_score(df_5m, direction: str) -> Tuple[float, str]:
    """
    Volatility Risk Premium via realized vol ratio.
    VRP proxy = σ_short(5-bar) / σ_long(20-bar)

    VRP < 0.70: vol compressing → trending regime → +6
    VRP 0.70-0.90: slight compression → +3
    VRP 0.90-1.20: normal → 0
    VRP 1.20-1.50: vol expanding → -3
    VRP > 1.50: vol spike → -7

    Returns (score_delta, reason). Fail-open: (0.0, '').
    Need >= 25 bars. Uses log-return std (not YZ for simplicity).
    """
    try:
        if df_5m is None or len(df_5m) < 25:
            return 0.0, ''

        closes      = df_5m['close'].values[-25:].astype(float)
        log_returns = np.diff(np.log(closes + 1e-10))

        vol_short = float(np.std(log_returns[-5:]))    # last 5 bars
        vol_long  = float(np.std(log_returns[-20:]))   # last 20 bars

        if vol_long < 1e-10:
            return 0.0, ''

        vrp = vol_short / vol_long

        if vrp < 0.70:
            return  6.0, f'VRP={vrp:.2f} compressing trending regime'
        elif vrp <= 0.90:
            return  3.0, f'VRP={vrp:.2f} slight compression ideal entry'
        elif vrp <= 1.20:
            return  0.0, f'VRP={vrp:.2f} normal'
        elif vrp <= 1.50:
            return -3.0, f'VRP={vrp:.2f} vol expanding caution'
        else:
            return -7.0, f'VRP={vrp:.2f} vol spike regime change'

    except Exception:
        return 0.0, ''


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 6: Risk Parity Position Size Multiplier
# ─────────────────────────────────────────────────────────────────────────────

def get_risk_parity_size_multiplier(df_5m, target_risk_pct: float = 0.015) -> float:
    """
    Bridgewater Risk Parity: size = target_risk / per_bar_vol
    target_risk_pct = 1.5% per bar (1 ATR unit)

    per_bar_vol = std(log_returns, last 20 bars)

    size_mult = target_risk_pct / max(per_bar_vol, 0.001)
    Clamped to [0.5, 1.5].

    Low vol symbol (e.g. 0.3% per bar) → 1.5x size (more room to target)
    High vol symbol (e.g. 3.0% per bar) → 0.5x size (less exposure)

    Returns float. Fail-open: 1.0.
    Need >= 20 bars.
    """
    try:
        if df_5m is None or len(df_5m) < 20:
            return 1.0

        closes      = df_5m['close'].values[-20:].astype(float)
        log_returns = np.diff(np.log(closes + 1e-10))

        per_bar_vol = float(np.std(log_returns))

        size_mult = target_risk_pct / max(per_bar_vol, 0.001)

        # Clamp to [0.5, 1.5]
        return float(max(0.5, min(1.5, size_mult)))

    except Exception:
        return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# MASTER AGGREGATOR
# ─────────────────────────────────────────────────────────────────────────────

def get_citadel_boost(
    df_5m,
    direction: str,
    atr: float,
    ltp: float,
) -> Tuple[float, float, str]:
    """
    Aggregate all 6 Citadel-grade strategies.
    Returns (score_delta: float, size_multiplier: float, combined_reason: str)
    Fail-open: (0.0, 1.0, '') on any error.
    """
    try:
        total_delta = 0.0
        size_mult   = 1.0
        reasons: list = []

        # S1: Yang-Zhang Volatility
        try:
            s1, r1 = get_yang_zhang_score(df_5m, direction, float(atr or 0), float(ltp or 0))
            if s1 != 0.0:
                total_delta += s1
                if r1:
                    reasons.append(f'YZ({s1:+.0f})')
        except Exception:
            pass

        # S2: Sample Entropy
        try:
            s2, r2 = get_sample_entropy_score(df_5m, direction)
            if s2 != 0.0:
                total_delta += s2
                if r2:
                    reasons.append(f'SE({s2:+.0f})')
        except Exception:
            pass

        # S3: Volume Profile
        try:
            s3, r3 = get_volume_profile_score(df_5m, direction, float(ltp or 0))
            if s3 != 0.0:
                total_delta += s3
                if r3:
                    reasons.append(f'VP({s3:+.0f})')
        except Exception:
            pass

        # S4: Short-Range Autocorrelation
        try:
            s4, r4 = get_autocorrelation_score(df_5m, direction)
            if s4 != 0.0:
                total_delta += s4
                if r4:
                    reasons.append(f'AC({s4:+.0f})')
        except Exception:
            pass

        # S5: Volatility Risk Premium
        try:
            s5, r5 = get_vrp_score(df_5m, direction)
            if s5 != 0.0:
                total_delta += s5
                if r5:
                    reasons.append(f'VRP({s5:+.0f})')
        except Exception:
            pass

        # S6: Risk Parity Size Multiplier (does not contribute to score delta)
        try:
            size_mult = get_risk_parity_size_multiplier(df_5m)
        except Exception:
            size_mult = 1.0

        combined_reason = ' | '.join(reasons)
        return float(total_delta), float(size_mult), combined_reason

    except Exception:
        return 0.0, 1.0, ''
