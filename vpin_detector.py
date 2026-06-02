"""
vpin_detector.py — VPIN: Volume-Synchronized Probability of Informed Trading v1.0

Developed by Easley, López de Prado & O'Hara (2012). Used by exchanges,
regulators, and top HFT firms to detect when INFORMED traders are active.

The Flash Crash of May 6, 2010 was predicted 75 minutes in advance by VPIN.
High VPIN = toxic order flow = your limit orders will be picked off by
traders who KNOW something you don't. Skip the trade or reduce size.

How it works:
  1. Divide volume into equal "buckets" (each bucket = V* total volume)
  2. For each bucket, classify volume as BUY or SELL using the bulk
     volume classification method (Easley et al.):
       Buy volume = V × Φ((close - avg_price) / σ_price)
       Sell volume = V - Buy volume
  3. |Buy_vol - Sell_vol| per bucket = Order Imbalance (OI)
  4. VPIN = avg(OI) over last 50 buckets / V*

Interpretation:
  VPIN < 0.15: Low toxicity — mostly uninformed flow — SAFE to trade
  VPIN 0.15-0.30: Normal — proceed with standard sizing
  VPIN 0.30-0.50: Elevated — informed traders may be active — -3 score
  VPIN > 0.50: HIGH TOXICITY — someone knows something — -7 score, avoid

For intraday momentum: HIGH VPIN on breakouts can mean GOOD (informed
buyers in) or BAD (informed sellers distributing). We combine with
direction to separate:
  - High VPIN + price rising strongly + LONG: informed accumulation → +6
  - High VPIN + price rising + SHORT: informed distribution against us → -7
  - High VPIN flat: unknown → -3 (caution)

Implementation: pure pandas/numpy on 5-min bars. No external deps.
Fail-open. Per-symbol rolling state.
"""

import logging
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── VPIN state per symbol ─────────────────────────────────────────────────────
_vpin_state: Dict[str, dict] = {}

# Bucket size: fraction of average daily volume
# We use 1/50 of the average volume across last 10 bars as a bucket
_N_BUCKETS  = 50    # rolling window of buckets for VPIN estimate


def _get_state(symbol: str) -> dict:
    sym = symbol.upper()
    if sym not in _vpin_state:
        _vpin_state[sym] = {
            "buckets_oi":  [],    # order imbalance per bucket (|buy-sell|/V*)
            "bucket_vol":  0.0,   # volume accumulated in current open bucket
            "bucket_buy":  0.0,   # buy volume in current open bucket
            "vstar":       None,  # bucket size (set on first update)
            "price_sigma": 0.01,  # rolling price std for BVC
            "prev_close":  None,
        }
    return _vpin_state[sym]


def update_vpin(symbol: str, df_5m) -> Optional[float]:
    """
    Recompute VPIN from the last N bars of 5-min data.
    Returns current VPIN estimate (0.0-1.0) or None on failure.
    """
    try:
        if df_5m is None or len(df_5m) < 10:
            return None

        col_c = "close"  if "close"  in df_5m.columns else "Close"
        col_h = "high"   if "high"   in df_5m.columns else "High"
        col_l = "low"    if "low"    in df_5m.columns else "Low"
        col_v = "volume" if "volume" in df_5m.columns else "Volume"

        closes = df_5m[col_c].values.astype(float)
        highs  = df_5m[col_h].values.astype(float)
        lows   = df_5m[col_l].values.astype(float)
        vols   = df_5m[col_v].values.astype(float)

        n = len(closes)
        st = _get_state(symbol)

        # Price sigma: std of close-to-close returns × price level
        # (more robust than half-range std on short windows or synthetic data)
        returns = np.diff(closes[-21:]) if len(closes) >= 2 else np.array([0.01])
        ret_sigma = float(np.std(returns)) if len(returns) > 1 else 0.0
        # Fallback: use mean intrabar range / 4 if return sigma is tiny
        intrabar_ranges = (highs - lows)
        range_sigma = float(np.mean(intrabar_ranges[-20:]) / 4.0)
        sigma_candidate = max(ret_sigma, range_sigma, closes[-1] * 0.0005)
        st["price_sigma"] = float(sigma_candidate)

        # V* = average volume per bar over last 20 bars
        v_star = float(np.mean(vols[-20:])) if st["vstar"] is None else st["vstar"]
        if v_star < 1:
            v_star = 1.0
        st["vstar"] = v_star

        # BVC: classify each bar's volume into buy/sell
        from scipy.stats import norm
        sigma = st["price_sigma"]
        ois = []
        for i in range(max(1, n - 30), n):
            v   = float(vols[i])
            c   = float(closes[i])
            pc  = float(closes[i - 1])
            tp  = float((highs[i] + lows[i] + c) / 3)  # typical price
            # BVC: Φ((close - typical_price) / sigma) × volume
            z        = (c - tp) / sigma
            buy_frac = float(norm.cdf(z))
            buy_v    = v * buy_frac
            sell_v   = v * (1.0 - buy_frac)
            oi       = abs(buy_v - sell_v) / max(v_star, 1.0)
            ois.append(oi)

        if not ois:
            return None

        # VPIN = average OI across all mini-buckets
        vpin = float(np.mean(ois))
        return min(vpin, 1.0)
    except Exception as e:
        logger.debug(f"vpin_detector.update_vpin suppressed: {e}")
        return None


def get_vpin_score(symbol: str, df_5m, direction: str, ltp: float) -> Tuple[float, str]:
    """
    Compute VPIN and return (score_delta, reason).
    Fail-open.
    """
    try:
        vpin = update_vpin(symbol, df_5m)
        if vpin is None:
            return 0.0, "vpin:no_data"

        is_long = direction.upper() in ("LONG", "BUY")

        # Price momentum proxy (last 5 bars)
        if df_5m is not None and len(df_5m) >= 5:
            col = "close" if "close" in df_5m.columns else "Close"
            closes = df_5m[col].values[-6:].astype(float)
            price_rising = closes[-1] > closes[-5]
        else:
            price_rising = True

        if vpin < 0.15:
            # Uninformed flow — market makers providing liquidity, safe environment
            return +3.0, f"vpin:clean_flow {vpin:.2f}"
        elif vpin < 0.30:
            return 0.0, f"vpin:normal {vpin:.2f}"
        elif vpin < 0.50:
            if is_long and price_rising:
                # Informed buyers active — accumulation signal
                return +6.0, f"vpin:informed_buyers {vpin:.2f}"
            elif not is_long and not price_rising:
                return +6.0, f"vpin:informed_sellers {vpin:.2f}"
            else:
                return -3.0, f"vpin:elevated_unknown {vpin:.2f}"
        else:
            # High toxicity — someone knows something
            if is_long and price_rising:
                return +8.0, f"vpin:SMART_MONEY_LONG {vpin:.2f}"
            elif not is_long and not price_rising:
                return +8.0, f"vpin:SMART_MONEY_SHORT {vpin:.2f}"
            else:
                return -7.0, f"vpin:TOXIC_FLOW {vpin:.2f}"
    except Exception as e:
        logger.debug(f"vpin_detector.get_vpin_score suppressed: {e}")
        return 0.0, "vpin:error"
