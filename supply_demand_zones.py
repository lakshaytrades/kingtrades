"""
supply_demand_zones.py — Sam Seiden Supply/Demand Zone Detection (v17.0)

Identifies unfilled institutional order zones using the Sam Seiden methodology:
a price level where price departed strongly (>1.5x ATR in 1-3 bars) = unfilled orders.

Thread-safe with 30-min TTL cache per symbol.  Fail-open (returns 0.0).
"""

import logging
import threading
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── TTL cache ─────────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, float, str]] = {}
_cache_lock = threading.Lock()
_TTL = 1800  # 30 minutes


def _cache_get(key: str) -> Tuple[bool, float, str]:
    with _cache_lock:
        if key in _cache:
            score, exp, reason = _cache[key]
            if _time.monotonic() < exp:
                return True, score, reason
    return False, 0.0, ""


def _cache_set(key: str, score: float, reason: str) -> None:
    with _cache_lock:
        _cache[key] = (score, _time.monotonic() + _TTL, reason)


# ── Zone detection ────────────────────────────────────────────────────────────

def _detect_zones(df, atr_mult: float = 1.5) -> Tuple[List[dict], List[dict]]:
    """
    Scan OHLCV DataFrame for supply/demand zones.

    Returns (demand_zones, supply_zones).
    Each zone: {'price': float, 'freshness': int}  freshness = # times retested
    """
    try:
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values if "close" in df.columns else df["Close"].values
        n = len(closes)

        if n < 10:
            return [], []

        # Compute ATR (simple range average)
        ranges = highs - lows
        atr = float(np.mean(ranges[-20:])) if n >= 20 else float(np.mean(ranges))
        if atr < 1e-9:
            return [], []

        demand_zones: List[dict] = []
        supply_zones: List[dict] = []

        # Look for strong departure moves
        for i in range(2, min(n, 100)):
            bar_move = abs(closes[i] - closes[i - 1])
            if bar_move < atr_mult * atr:
                continue

            # Direction of departure
            if closes[i] > closes[i - 1]:
                # Strong up move → demand zone at base (low of bar before move)
                zone_price = float(lows[i - 1])
                demand_zones.append({"price": zone_price, "freshness": 0, "idx": i})
            else:
                # Strong down move → supply zone at top (high of bar before move)
                zone_price = float(highs[i - 1])
                supply_zones.append({"price": zone_price, "freshness": 0, "idx": i})

        # Count retest visits for each zone (bars after zone formation that touch zone)
        for zone in demand_zones:
            zp = zone["price"]
            tolerance = zp * 0.005
            for j in range(zone["idx"] + 1, n):
                if abs(lows[j] - zp) <= tolerance or abs(closes[j] - zp) <= tolerance:
                    zone["freshness"] += 1

        for zone in supply_zones:
            zp = zone["price"]
            tolerance = zp * 0.005
            for j in range(zone["idx"] + 1, n):
                if abs(highs[j] - zp) <= tolerance or abs(closes[j] - zp) <= tolerance:
                    zone["freshness"] += 1

        return demand_zones, supply_zones
    except Exception as exc:
        logger.debug(f"Zone detection error: {exc}")
        return [], []


# ── Public API ────────────────────────────────────────────────────────────────

def get_supply_demand_score(
    symbol: str, df_5m, direction: str, ltp: float
) -> Tuple[float, str]:
    """
    Sam Seiden supply/demand zone score.

    Score (LONG):
      Near demand zone (fresh):   +14
      Near demand zone (used 1x): +12
      Near demand zone (used 2x+): +9
      Near supply zone (fresh):   -10
      Near supply zone (used):    -7

    Score (SHORT):
      Near supply zone (fresh):   +14
      Near supply zone (used 1x): +12
      Near demand zone:           -8

    Proximity threshold: within 0.5% of zone price.
    Fail-open: returns (0.0, "sdz N/A") on any error.
    30-min TTL cache.
    """
    cache_key = f"{symbol}:{direction}:{ltp:.2f}"
    hit, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"sdz cached: {cached_reason}"

    try:
        if df_5m is None or len(df_5m) < 10:
            return 0.0, "sdz: insufficient data"

        demand_zones, supply_zones = _detect_zones(df_5m)

        threshold = ltp * 0.005  # 0.5% proximity
        best_score = 0.0
        best_reason = "sdz: no_zone_nearby"

        # Check demand zones
        for zone in demand_zones:
            if abs(ltp - zone["price"]) <= threshold:
                f = zone["freshness"]
                if direction == "LONG":
                    if f == 0:
                        s = 14.0
                    elif f == 1:
                        s = 12.0
                    else:
                        s = 9.0
                else:  # SHORT
                    s = -8.0
                r = f"demand_zone@{zone['price']:.2f}(fresh={f==0})"
                if abs(s) > abs(best_score):
                    best_score = s
                    best_reason = r

        # Check supply zones
        for zone in supply_zones:
            if abs(ltp - zone["price"]) <= threshold:
                f = zone["freshness"]
                if direction == "SHORT":
                    if f == 0:
                        s = 14.0
                    elif f == 1:
                        s = 12.0
                    else:
                        s = 9.0
                else:  # LONG
                    if f == 0:
                        s = -10.0
                    else:
                        s = -7.0
                r = f"supply_zone@{zone['price']:.2f}(fresh={f==0})"
                if abs(s) > abs(best_score):
                    best_score = s
                    best_reason = r

        _cache_set(cache_key, best_score, best_reason)
        return best_score, best_reason
    except Exception as exc:
        logger.debug(f"get_supply_demand_score error (fail-open): {exc}")
        return 0.0, "sdz N/A"
