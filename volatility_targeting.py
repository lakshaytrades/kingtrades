"""
volatility_targeting.py — Portfolio Volatility Targeting (Tier 2.5)

Bridgewater, AQR, and Winton all run "risk parity" or "volatility targeting":
scale position sizes so each position contributes equal risk to the portfolio,
and the total portfolio stays near a target annualized volatility.

Why this matters:
  - Without vol targeting: high-vol stocks (NVDA 50% ann vol) dominate risk
    while low-vol stocks (AAPL 28% ann vol) barely matter
  - With vol targeting: every position contributes the same daily $ risk
  - Result: smoother P&L, better Sharpe ratio, automatic deleveraging in
    volatile markets

Mechanics:
  1. Compute symbol's 20-day realized volatility (annualized)
  2. Compute current VIX (macro vol environment)
  3. size_mult = TARGET_DAILY_VOL_PCT / symbol_daily_vol
  4. Apply VIX overlay: high VIX → smaller positions
  5. Cap at 1.5x (aggressive) and floor at 0.3x (defensive)

TARGET_DAILY_VOL_PCT: 0.5% per day ≈ 8% annualized portfolio vol (conservative)
This means: on a day when NVDA moves 3%, we take a smaller NVDA position than
when QQQ moves 0.8%, so both contribute ~$150 daily risk per $30k position.

Cache: 30 minutes (realized vol doesn't change minute-to-minute)
"""

import logging
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Target daily portfolio volatility as % of capital
# 0.5% daily = ~8% annualized (conservative, institutional-grade risk budget)
TARGET_DAILY_VOL_PCT: float = 0.5

# Maximum leverage from vol targeting (cap to prevent over-sizing on low-vol stocks)
MAX_SIZE_MULT: float = 1.5

# Minimum size (floor at 30% to preserve position viability)
MIN_SIZE_MULT: float = 0.3

# Cache TTL
_CACHE_TTL: float = 1800.0  # 30 minutes
_vol_cache: Dict[str, Tuple[float, float]] = {}  # {symbol: (realized_vol_pct, timestamp)}
_vix_cache: Tuple[float, float] = (20.0, 0.0)   # (vix_level, timestamp)


def _get_vix_level() -> float:
    """Fetch VIX level. Cached 30 min."""
    global _vix_cache
    vix_val, ts = _vix_cache
    if _time.time() - ts < _CACHE_TTL:
        return vix_val
    try:
        import yfinance as yf
        tk = yf.Ticker("^VIX")
        hist = tk.history(period="2d", interval="1d")
        if not hist.empty:
            vix = float(hist["Close"].iloc[-1])
            _vix_cache = (vix, _time.time())
            return vix
    except Exception as e:
        logger.debug(f"VIX fetch for vol-target: {e}")
    return vix_val


def _get_realized_vol(symbol: str, df_5m=None) -> float:
    """
    Compute 20-day realized daily volatility for symbol (annualized %).
    Prefers using df_5m if passed (faster, no extra download).
    Falls back to yfinance daily. Returns annualized vol as decimal (e.g., 0.35 = 35%).
    """
    # Try df_5m first (faster)
    if df_5m is not None:
        try:
            import numpy as np
            import pandas as pd
            closes = df_5m["close"].values if "close" in df_5m.columns else df_5m["Close"].values
            if len(closes) >= 20:
                log_rets = np.diff(np.log(closes[-40:]))  # last 40 bars
                daily_vol = log_rets.std() * (252 ** 0.5) * (78 ** 0.5)  # 78 5-min bars/day
                if 0.05 <= daily_vol <= 2.0:
                    return float(daily_vol)
        except Exception:
            pass

    # Cached daily from yfinance
    cached = _vol_cache.get(symbol)
    if cached:
        vol_val, ts = cached
        if _time.time() - ts < _CACHE_TTL:
            return vol_val

    try:
        import yfinance as yf
        import numpy as np
        hist = yf.Ticker(symbol).history(period="30d", interval="1d")
        if hist.empty or len(hist) < 10:
            return 0.35  # default 35% annualized (typical large-cap vol)
        log_rets = np.diff(np.log(hist["Close"].values))
        daily_vol = log_rets.std() * (252 ** 0.5)
        _vol_cache[symbol] = (float(daily_vol), _time.time())
        return float(daily_vol)
    except Exception as e:
        logger.debug(f"Realized vol fetch {symbol}: {e}")
        return 0.0  # signal caller to skip (no data = no adjustment)


def get_vol_target_size_multiplier(
    symbol: str,
    df_5m=None,
    target_daily_vol_pct: float = TARGET_DAILY_VOL_PCT,
) -> Tuple[float, str]:
    """
    Returns (size_multiplier, reason).

    size_multiplier: 0.3 to 1.5
      >1.0 = symbol is low-vol, increase size to hit risk target
      <1.0 = symbol is high-vol, decrease size to not exceed risk target
      1.0  = symbol's daily vol exactly matches target

    The caller multiplies their base position size by this.
    """
    try:
        realized_vol = _get_realized_vol(symbol, df_5m)

        # Convert annualized vol to daily vol %
        import math
        daily_vol_pct = realized_vol / math.sqrt(252) * 100  # e.g. 35% ann = 2.2% daily

        if daily_vol_pct <= 0.1 or realized_vol <= 0.0:
            return 1.0, "vol_target: insufficient vol data"

        # Base size multiplier
        size_mult = target_daily_vol_pct / daily_vol_pct

        # VIX overlay: high VIX = market stress = reduce all positions
        vix = _get_vix_level()
        if vix >= 35:
            size_mult *= 0.50  # crisis: half size
        elif vix >= 28:
            size_mult *= 0.65  # elevated fear
        elif vix >= 22:
            size_mult *= 0.80  # mild concern
        elif vix <= 13:
            size_mult *= 0.90  # complacency (low VIX can precede spikes)

        # Clamp
        size_mult = max(MIN_SIZE_MULT, min(MAX_SIZE_MULT, round(size_mult, 3)))

        reason = (
            f"vol_target: ann_vol={realized_vol*100:.0f}% "
            f"daily_vol={daily_vol_pct:.2f}% "
            f"vix={vix:.1f} "
            f"→ {size_mult:.2f}x"
        )
        return size_mult, reason

    except Exception as e:
        logger.debug(f"vol_target error for {symbol}: {e}")
        return 1.0, ""
