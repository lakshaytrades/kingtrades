"""
bloomberg_proxy.py — Free Bloomberg Terminal Equivalent (L99)

Bloomberg Terminal ($2,000/month) provides:
  VIX real-time · credit spreads · dollar strength · equity risk premium
  NYSE TICK · TRIN · new highs/lows · Fed watch · cross-asset risk

This module aggregates free equivalents into a single Bloomberg-style
macro environment score for momentum trading context.

Positive score = Bloomberg green (risk-on, momentum works)
Negative score = Bloomberg red (risk-off, avoid momentum longs)
"""
import logging
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict = {}
_PRICE_TTL = 300.0   # 5 min
_MACRO_TTL = 3600.0  # 1h

def _cache_get(key: str, ttl: float):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < ttl:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _get_vix() -> Optional[float]:
    cached = _cache_get("vix", _PRICE_TTL)
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        data = yf.Ticker("^VIX").history(period="2d", interval="5m")
        if data is not None and not data.empty:
            v = float(data["Close"].iloc[-1])
            _cache_set("vix", v)
            return v
    except Exception:
        pass
    return None


def _get_credit_spread() -> Optional[float]:
    """HYG vs LQD daily return spread as credit risk proxy."""
    cached = _cache_get("credit", _PRICE_TTL)
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        hyg = yf.Ticker("HYG").history(period="5d")
        lqd = yf.Ticker("LQD").history(period="5d")
        if hyg is not None and lqd is not None and not hyg.empty and not lqd.empty:
            hyg_r = float(hyg["Close"].pct_change().iloc[-1])
            lqd_r = float(lqd["Close"].pct_change().iloc[-1])
            spread = hyg_r - lqd_r  # positive = HY outperforming = risk-on
            _cache_set("credit", spread)
            return spread
    except Exception:
        pass
    return None


def _get_dollar_chg() -> Optional[float]:
    """UUP ETF % change as DXY proxy. Strong USD = risk-off."""
    cached = _cache_get("dxy", _PRICE_TTL)
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        data = yf.Ticker("UUP").history(period="5d")
        if data is not None and not data.empty:
            chg = float(data["Close"].pct_change().iloc[-1]) * 100
            _cache_set("dxy", chg)
            return chg
    except Exception:
        pass
    return None


def _get_tick_proxy(df_spy_5m) -> Optional[float]:
    """Approximate NYSE TICK from SPY bar close positions."""
    if df_spy_5m is None:
        return None
    try:
        import numpy as np
        close_col = "close" if "close" in df_spy_5m.columns else "Close"
        high_col  = "high"  if "high"  in df_spy_5m.columns else "High"
        low_col   = "low"   if "low"   in df_spy_5m.columns else "Low"
        closes = df_spy_5m[close_col].values[-10:]
        highs  = df_spy_5m[high_col].values[-10:]
        lows   = df_spy_5m[low_col].values[-10:]
        ranges = highs - lows
        pos = np.where(ranges > 0, (closes - lows) / ranges, 0.5)
        # Scale to TICK-like range [-1000, +1000]
        return float(np.mean(pos) * 2 - 1) * 800
    except Exception:
        return None


def get_bloomberg_score(direction: str, df_spy_5m=None) -> Tuple[float, str]:
    """
    Composite Bloomberg-equivalent macro environment score.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        total = 0.0
        parts = []

        # 1. VIX regime
        vix = _get_vix()
        if vix is not None:
            if vix < 15:
                d = 4.0 if direction == "LONG" else -2.0
                parts.append(f"VIX={vix:.0f}_LOW")
                total += d
            elif vix > 32:
                d = -7.0 if direction == "LONG" else 5.0
                parts.append(f"VIX={vix:.0f}_FEAR")
                total += d
            elif vix > 22:
                d = -3.0 if direction == "LONG" else 3.0
                parts.append(f"VIX={vix:.0f}_ELEVATED")
                total += d

        # 2. Credit spread (HYG - LQD)
        credit = _get_credit_spread()
        if credit is not None:
            if credit > 0.003:
                d = 3.0 if direction == "LONG" else -2.0
                parts.append(f"CREDIT=RISK_ON")
                total += d
            elif credit < -0.003:
                d = -3.0 if direction == "LONG" else 3.0
                parts.append(f"CREDIT=RISK_OFF")
                total += d

        # 3. Dollar (strong = risk-off)
        dxy = _get_dollar_chg()
        if dxy is not None:
            if dxy > 0.30:
                d = -2.0 if direction == "LONG" else 2.0
                parts.append(f"USD_STRONG({dxy:+.2f}%)")
                total += d
            elif dxy < -0.30:
                d = 2.0 if direction == "LONG" else -2.0
                parts.append(f"USD_WEAK({dxy:+.2f}%)")
                total += d

        # 4. TICK proxy
        tick = _get_tick_proxy(df_spy_5m)
        if tick is not None:
            if tick > 400:
                d = 3.0 if direction == "LONG" else -2.0
                parts.append(f"TICK~{tick:+.0f}")
                total += d
            elif tick < -400:
                d = -3.0 if direction == "LONG" else 3.0
                parts.append(f"TICK~{tick:+.0f}")
                total += d

        if not parts:
            return 0.0, "bloomberg:no-data"
        return float(total), f"BLOOMBERG[{' | '.join(parts)}]"
    except Exception as exc:
        logger.debug(f"[bloomberg_proxy] {exc}")
        return 0.0, "bloomberg:error"
