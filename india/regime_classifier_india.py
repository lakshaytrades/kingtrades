"""
regime_classifier_india.py — NSE Market Regime Classifier (God Mode)
Classifies market into: TRENDING_UP, TRENDING_DOWN, RANGING, VOLATILE, UNKNOWN
Uses: Nifty 15m EMA slope + ADX proxy + India VIX + realized vol ratio
Updates every 15 min. Cached. Fail-open returns UNKNOWN.
"""
import logging
import time as _time
from typing import Dict, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("regime_india")
IST = ZoneInfo("Asia/Kolkata")
_cache: dict = {"regime": "UNKNOWN", "ts": 0.0, "confidence": 0.0, "details": {}}
_TTL = 900.0  # 15 min


def _compute_regime() -> dict:
    try:
        import yfinance as yf
        import numpy as np
        import pandas as pd

        df = yf.download("^NSEI", period="5d", interval="15m", progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 30:
            return {"regime": "UNKNOWN", "confidence": 0.0, "details": {}}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        ema9  = close.ewm(span=9,  adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        gap   = (ema9.iloc[-1] - ema21.iloc[-1]) / (ema21.iloc[-1] + 1e-9)
        slope = (ema9.iloc[-1] - ema9.iloc[-5]) / (ema9.iloc[-5] + 1e-9)

        atr14       = (high - low).rolling(14).mean().iloc[-1]
        close_range = (close.rolling(14).max() - close.rolling(14).min()).iloc[-1]
        adx_proxy   = close_range / (atr14 * 14 + 1e-9) * 25

        recent_vol = (high - low).iloc[-20:].mean() / (close.iloc[-20:].mean() + 1e-9)
        hist_vol   = (high - low).iloc[-100:].mean() / (close.iloc[-100:].mean() + 1e-9)
        vol_ratio  = recent_vol / (hist_vol + 1e-9)

        try:
            from data_fetch_dhan import get_india_vix
            vix = get_india_vix()
        except Exception:
            vix = 0.0

        details = {
            "gap":       round(float(gap),       5),
            "slope":     round(float(slope),     5),
            "adx_proxy": round(float(adx_proxy), 2),
            "vol_ratio": round(float(vol_ratio), 2),
            "vix":       vix,
        }

        if vix >= 22.0 or vol_ratio >= 2.0:
            return {
                "regime":     "VOLATILE",
                "confidence": min(1.0, (vix / 28 + vol_ratio / 3) / 2),
                "details":    details,
            }
        if adx_proxy >= 20 and abs(gap) >= 0.002:
            r = "TRENDING_UP" if gap > 0 else "TRENDING_DOWN"
            return {
                "regime":     r,
                "confidence": min(1.0, adx_proxy / 35),
                "details":    details,
            }
        return {
            "regime":     "RANGING",
            "confidence": min(1.0, 1.0 - adx_proxy / 25),
            "details":    details,
        }

    except Exception as e:
        logger.debug(f"compute_regime: {e}")
        return {"regime": "UNKNOWN", "confidence": 0.0, "details": {}}


def _refresh():
    global _cache
    now = _time.monotonic()
    if now - _cache["ts"] > _TTL:
        result = _compute_regime()
        _cache = {**result, "ts": now}


def get_regime() -> str:
    _refresh()
    return _cache.get("regime", "UNKNOWN")


def get_regime_details() -> dict:
    _refresh()
    return dict(_cache)


def get_regime_score_multiplier() -> float:
    r = get_regime()
    return {
        "TRENDING_UP":   0.90,
        "TRENDING_DOWN": 0.90,
        "RANGING":       1.10,
        "VOLATILE":      1.20,
    }.get(r, 1.0)


def get_regime_size_multiplier() -> float:
    r = get_regime()
    return {
        "TRENDING_UP":   1.10,
        "TRENDING_DOWN": 1.10,
        "RANGING":       0.70,
        "VOLATILE":      0.50,
    }.get(r, 1.0)


def is_direction_aligned_with_regime(direction: str) -> bool:
    r = get_regime()
    if r == "TRENDING_UP"   and direction == "SHORT":
        return False
    if r == "TRENDING_DOWN" and direction == "LONG":
        return False
    return True
