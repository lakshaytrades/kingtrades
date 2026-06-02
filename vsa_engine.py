"""
vsa_engine.py — Volume Spread Analysis (Tom Williams / Wyckoff methodology)
Reads institutional intent from price spread + volume. Free equivalent of VSA software.
All functions return (score_delta: float, reason: str). Fail-open.
"""
import logging
import time as _time
from typing import Dict, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

_vsa_cache: Dict = {}
_VSA_TTL = 60.0  # 1-min cache per symbol


def _is_cache_valid(cache_key: str) -> bool:
    """Check if cache entry exists and is still fresh."""
    entry = _vsa_cache.get(cache_key)
    if entry is None:
        return False
    return (_time.monotonic() - entry["ts"]) < _VSA_TTL


def _set_cache(cache_key: str, result: Tuple[float, str]) -> None:
    _vsa_cache[cache_key] = {"result": result, "ts": _time.monotonic()}


def get_vsa_score(symbol: str, df_5m: pd.DataFrame, direction: str) -> Tuple[float, str]:
    """
    Analyze last 20 bars for VSA patterns.
    Returns (score_delta, reason). Positive = trade with direction. Negative = against.
    Fail-open: returns (0.0, "VSA_ERROR") on any exception.

    The 6 VSA signals:
      1. Stopping Volume  (+12 LONG)
      2. No Supply        (+8  LONG)
      3. Effort vs Result (+6  REVERSAL WARNING)
      4. Upthrust         (+10 SHORT)
      5. Bag Holding      (+9  SHORT)
      6. Climactic Action (+8  REVERSAL)
    """
    try:
        cache_key = f"{symbol}_{direction}"
        if _is_cache_valid(cache_key):
            return _vsa_cache[cache_key]["result"]

        # Guard: need at least 20 bars with OHLCV columns
        if df_5m is None or len(df_5m) < 20:
            return (0.0, "VSA_INSUFFICIENT_DATA")

        required_cols = {"open", "high", "low", "close", "volume"}
        actual_cols = {c.lower() for c in df_5m.columns}
        if not required_cols.issubset(actual_cols):
            return (0.0, "VSA_MISSING_COLUMNS")

        # Normalise column names to lower-case
        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]
        df = df.tail(20).reset_index(drop=True)

        # ── Core metrics ───────────────────────────────────────────────────────
        # Normalised spread = (high-low) / close  (fraction of price)
        df["spread"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)

        vol_ma20 = df["volume"].mean()
        if vol_ma20 <= 0:
            return (0.0, "VSA_ZERO_VOLUME")

        df["vol_ratio"] = df["volume"] / vol_ma20

        spread_mean = float(df["spread"].mean())
        spread_std  = float(df["spread"].std(ddof=1)) if len(df) > 1 else 0.0

        # Helper thresholds (relative to the 20-bar window)
        def _is_high_vol(idx: int, threshold: float = 1.6) -> bool:
            return float(df.loc[idx, "vol_ratio"]) >= threshold

        def _is_low_vol(idx: int, threshold: float = 0.6) -> bool:
            return float(df.loc[idx, "vol_ratio"]) <= threshold

        def _is_wide_spread(idx: int) -> bool:
            s = float(df.loc[idx, "spread"])
            return s > spread_mean + 0.5 * spread_std

        def _is_narrow_spread(idx: int) -> bool:
            s = float(df.loc[idx, "spread"])
            return s < spread_mean - 0.3 * spread_std

        def _close_near_low(idx: int, pct: float = 0.35) -> bool:
            bar = df.loc[idx]
            rng = float(bar["high"]) - float(bar["low"])
            if rng == 0:
                return False
            return (float(bar["close"]) - float(bar["low"])) / rng <= pct

        def _close_near_high(idx: int, pct: float = 0.65) -> bool:
            bar = df.loc[idx]
            rng = float(bar["high"]) - float(bar["low"])
            if rng == 0:
                return False
            return (float(bar["close"]) - float(bar["low"])) / rng >= pct

        def _up_bar(idx: int) -> bool:
            return float(df.loc[idx, "close"]) > float(df.loc[idx, "open"])

        def _down_bar(idx: int) -> bool:
            return float(df.loc[idx, "close"]) < float(df.loc[idx, "open"])

        last = len(df) - 1  # index of the most-recent bar

        # ── 1. Stopping Volume ─────────────────────────────────────────────────
        # Ultra-high vol + down bar closing near its low → institutions absorbing
        # supply at support.  Bullish reversal signal → favour LONG.
        def _check_stopping_volume() -> Tuple[float, str]:
            idx = last
            if (
                _is_high_vol(idx, threshold=2.0)
                and _down_bar(idx)
                and _close_near_low(idx, pct=0.30)
            ):
                score = 12.0 if direction == "LONG" else -12.0
                return (score, "VSA:StoppingVolume")
            return (0.0, "")

        # ── 2. No Supply ───────────────────────────────────────────────────────
        # Low-vol narrow-spread up bar → no selling pressure, markup imminent.
        # Bullish → favour LONG.
        def _check_no_supply() -> Tuple[float, str]:
            idx = last
            if (
                _is_low_vol(idx, threshold=0.6)
                and _is_narrow_spread(idx)
                and _up_bar(idx)
            ):
                score = 8.0 if direction == "LONG" else -8.0
                return (score, "VSA:NoSupply")
            return (0.0, "")

        # ── 3. Effort vs Result ────────────────────────────────────────────────
        # High-vol bar with a small net price move (close ≈ open) → institutions
        # hiding trades, accumulating/distributing.  Score in direction of the
        # NEXT bar's move (use last-1 as the EVR bar, last as confirming bar).
        def _check_effort_vs_result() -> Tuple[float, str]:
            if last < 1:
                return (0.0, "")
            evr_idx = last - 1
            if not _is_high_vol(evr_idx, threshold=1.6):
                return (0.0, "")
            # "Small move" = body < 30 % of spread
            bar = df.loc[evr_idx]
            rng = float(bar["high"]) - float(bar["low"])
            if rng == 0:
                return (0.0, "")
            body = abs(float(bar["close"]) - float(bar["open"]))
            if body / rng > 0.30:
                return (0.0, "")
            # Confirm direction with the FOLLOWING bar
            next_bar = df.loc[last]
            if float(next_bar["close"]) > float(next_bar["open"]):
                # Confirming up move → EVR resolved bullish
                score = 6.0 if direction == "LONG" else -6.0
                return (score, "VSA:EffortVsResult(Bull)")
            else:
                # Confirming down move → EVR resolved bearish
                score = 6.0 if direction == "SHORT" else -6.0
                return (score, "VSA:EffortVsResult(Bear)")

        # ── 4. Upthrust ────────────────────────────────────────────────────────
        # Wide up bar closes near its LOW on high volume → fake breakout,
        # professional selling into retail FOMO.  Bearish → favour SHORT.
        def _check_upthrust() -> Tuple[float, str]:
            idx = last
            if (
                _is_high_vol(idx, threshold=1.5)
                and _is_wide_spread(idx)
                and _up_bar(idx)
                and _close_near_low(idx, pct=0.35)
            ):
                score = 10.0 if direction == "SHORT" else -10.0
                return (score, "VSA:Upthrust")
            return (0.0, "")

        # ── 5. Bag Holding ─────────────────────────────────────────────────────
        # High-vol up bar followed by next bar closing BELOW the up bar's close
        # → smart money sold into the rally, retail holds the bag.  Bearish.
        def _check_bag_holding() -> Tuple[float, str]:
            if last < 1:
                return (0.0, "")
            prev = last - 1
            curr = last
            if (
                _is_high_vol(prev, threshold=1.5)
                and _up_bar(prev)
                and float(df.loc[curr, "close"]) < float(df.loc[prev, "close"])
            ):
                score = 9.0 if direction == "SHORT" else -9.0
                return (score, "VSA:BagHolding")
            return (0.0, "")

        # ── 6. Climactic Action ────────────────────────────────────────────────
        # Highest volume in the 20-bar window on a wide-spread bar → exhaustion,
        # trend reversal imminent.  Score in the reversal direction.
        def _check_climactic_action() -> Tuple[float, str]:
            max_vol_idx = int(df["volume"].idxmax())
            if max_vol_idx != last:
                return (0.0, "")   # climax bar must be the most recent bar
            if not _is_wide_spread(last):
                return (0.0, "")
            # Big UP climax → exhaustion of buyers → bearish reversal
            if _up_bar(last) and _close_near_high(last):
                score = 8.0 if direction == "SHORT" else -8.0
                return (score, "VSA:ClimacticAction(BearRev)")
            # Big DOWN climax → exhaustion of sellers → bullish reversal
            if _down_bar(last) and _close_near_low(last):
                score = 8.0 if direction == "LONG" else -8.0
                return (score, "VSA:ClimacticAction(BullRev)")
            return (0.0, "")

        # ── Aggregate all signals ──────────────────────────────────────────────
        checks = [
            _check_stopping_volume,
            _check_no_supply,
            _check_effort_vs_result,
            _check_upthrust,
            _check_bag_holding,
            _check_climactic_action,
        ]

        total_score = 0.0
        hit_reasons = []
        for fn in checks:
            try:
                s, r = fn()
                if s != 0.0:
                    total_score += s
                    hit_reasons.append(r)
            except Exception as _e:
                logger.debug(f"[VSA] {fn.__name__} suppressed: {_e}")

        reason = "|".join(hit_reasons) if hit_reasons else "VSA:NoSignal"

        # Clamp individual module contribution
        total_score = max(-20.0, min(20.0, total_score))

        result: Tuple[float, str] = (total_score, reason)
        _set_cache(cache_key, result)
        return result

    except Exception as e:
        logger.debug(f"[VSA] get_vsa_score fail-open: {e}")
        return (0.0, "VSA_ERROR")
