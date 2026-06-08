"""
volatility_targeting_india.py — India VIX-based position sizing
India specialist: uses ^INDIAVIX instead of CBOE ^VIX.
India VIX behaves differently — spikes before RBI events, budget, elections.

Target: 0.5% daily portfolio vol (same as US bot but tuned for NSE volatility).
India VIX bands:
  < 12  : ultra-low → reduce size (complacency before budget/election)
  12–18 : normal    → 1.0x size
  18–24 : elevated  → 0.80x size
  24–30 : high      → 0.65x size
  > 30  : crisis    → 0.40x size (2020 Covid, 2024 election)
"""
import logging
import time as _time
from typing import Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger("vol_target_india")
IST = ZoneInfo("Asia/Kolkata")

TARGET_DAILY_VOL_PCT: float = 0.5   # 0.5% daily = ~8% annualized
MAX_SIZE_MULT: float = 1.5
MIN_SIZE_MULT: float = 0.30

_india_vix_cache: dict = {}
_INDIA_VIX_TTL = 1800.0  # 30-min cache


def _get_india_vix() -> float:
    """Fetch India VIX (^INDIAVIX). Cached 30 min. Returns 18.0 on failure."""
    now = _time.monotonic()
    if _india_vix_cache and now - _india_vix_cache.get("ts", 0) < _INDIA_VIX_TTL:
        return _india_vix_cache["vix"]
    try:
        from data_fetch_dhan import get_india_vix as _get_vix
        vix = _get_vix()
        if vix > 0:
            _india_vix_cache.update({"vix": vix, "ts": now})
            logger.debug(f"India VIX: {vix:.1f}")
            return vix
    except Exception as e:
        logger.debug(f"India VIX fetch failed: {e}")
    return 18.0  # neutral default


def _india_vix_multiplier(vix: float) -> float:
    """Convert India VIX level to size multiplier."""
    if vix > 30:
        return 0.40   # crisis / election / budget shock
    if vix > 24:
        return 0.65   # high stress
    if vix > 18:
        return 0.80   # elevated
    if vix < 12:
        return 0.85   # complacency — dangerous before events
    return 1.0        # normal (12–18)


def get_vol_target_size_multiplier_india(symbol: str,
                                         df_5m: pd.DataFrame = None,
                                         target_daily_vol_pct: float = TARGET_DAILY_VOL_PCT
                                         ) -> Tuple[float, str]:
    """
    India VIX-based position sizing.
    Returns (size_multiplier 0.30–1.50, reason_string).
    Fail-open: returns (1.0, "") on data failure.
    """
    try:
        # Realized volatility from 5m bars
        realized_vol = 0.0
        if df_5m is not None and not df_5m.empty and len(df_5m) >= 20:
            rets = df_5m["close"].pct_change().dropna()
            if len(rets) >= 10:
                intraday_std = float(rets.std())
                # 5m bars per day ≈ 75 (6.25 hours)
                realized_vol = intraday_std * (75 ** 0.5) * 100  # annualised %

        if realized_vol <= 0.0:
            return (1.0, "")  # fail-open

        # Vol targeting: scale position to hit target
        raw_mult = target_daily_vol_pct / (realized_vol / (252 ** 0.5) + 1e-9)
        raw_mult = float(np.clip(raw_mult, MIN_SIZE_MULT, MAX_SIZE_MULT))

        # India VIX overlay
        india_vix = _get_india_vix()
        vix_adj   = _india_vix_multiplier(india_vix)
        final     = float(np.clip(raw_mult * vix_adj, MIN_SIZE_MULT, MAX_SIZE_MULT))
        final     = round(final, 2)

        reason = (f"vol_target_india: realized={realized_vol:.1f}% "
                  f"INDIAVIX={india_vix:.1f} → {final:.2f}x")
        return (final, reason)

    except Exception as e:
        logger.debug(f"vol_target_india {symbol}: {e}")
        return (1.0, "")
