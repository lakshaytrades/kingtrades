"""
regime_detector.py — OHLCV-Only NSE Market Regime + F&O Expiry Detector

Detects market regime from historical OHLCV data with NO live API dependency.
Used by backtest_engine_india.py during replay and by live signal_generator.

Regimes: BULL_STRONG, BULL_WEAK, SIDEWAYS, BEAR_WEAK, BEAR_STRONG
F&O Expiry: weekly (every Thursday) and monthly (last Thursday of month).
"""
from enum import Enum
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class MarketRegime(Enum):
    BULL_STRONG  = "BULL_STRONG"
    BULL_WEAK    = "BULL_WEAK"
    SIDEWAYS     = "SIDEWAYS"
    BEAR_WEAK    = "BEAR_WEAK"
    BEAR_STRONG  = "BEAR_STRONG"


# Per-regime score multipliers {regime: {direction: multiplier}}
REGIME_SCORE_MULTS: Dict[MarketRegime, Dict[str, float]] = {
    MarketRegime.BULL_STRONG:  {"LONG": 1.30, "SHORT": 0.50},
    MarketRegime.BULL_WEAK:    {"LONG": 1.10, "SHORT": 0.75},
    MarketRegime.SIDEWAYS:     {"LONG": 0.70, "SHORT": 0.70},
    MarketRegime.BEAR_WEAK:    {"LONG": 0.75, "SHORT": 1.10},
    MarketRegime.BEAR_STRONG:  {"LONG": 0.50, "SHORT": 1.30},
}


def get_regime_score_mult(regime: MarketRegime, direction: str) -> float:
    """Get score multiplier for a regime + direction. Defaults 1.0 on miss."""
    return REGIME_SCORE_MULTS.get(regime, {}).get(direction, 1.0)


# ── F&O Expiry Detection ──────────────────────────────────────────────────────

def is_weekly_expiry(d: date) -> bool:
    """True if date is a Thursday (NSE weekly F&O expiry day)."""
    return d.weekday() == 3   # 0=Mon … 3=Thu


def is_monthly_expiry(d: date) -> bool:
    """True if date is the LAST Thursday of the month (NSE monthly F&O expiry)."""
    if d.weekday() != 3:
        return False
    return (d + timedelta(weeks=1)).month != d.month


def get_expiry_context(d: date) -> Dict:
    """
    Returns F&O expiry context for the given date.

    Keys:
      is_expiry_day   — bool, today is Thu (weekly expiry)
      is_monthly_expiry — bool, today is last-Thu of month
      is_pre_expiry   — bool, tomorrow is expiry (Wed)
      size_multiplier — float [0.5, 1.0], suggested position size factor
      bias            — str, 'AVOID' | 'SHORT_BIASED' | 'NEUTRAL'
    """
    monthly = is_monthly_expiry(d)
    weekly  = is_weekly_expiry(d)
    pre_exp = is_weekly_expiry(d + timedelta(days=1))

    if monthly:
        return {
            "is_expiry_day": True, "is_monthly_expiry": True,
            "is_pre_expiry": False, "size_multiplier": 0.6, "bias": "AVOID",
        }
    if weekly:
        return {
            "is_expiry_day": True, "is_monthly_expiry": False,
            "is_pre_expiry": False, "size_multiplier": 0.75, "bias": "SHORT_BIASED",
        }
    if pre_exp:
        return {
            "is_expiry_day": False, "is_monthly_expiry": False,
            "is_pre_expiry": True, "size_multiplier": 0.85, "bias": "NEUTRAL",
        }
    return {
        "is_expiry_day": False, "is_monthly_expiry": False,
        "is_pre_expiry": False, "size_multiplier": 1.0, "bias": "NEUTRAL",
    }


# ── OHLCV-Only Regime Detection ───────────────────────────────────────────────

def detect_regime_from_data(
    data: Dict[str, pd.DataFrame],
    now_ts: pd.Timestamp,
) -> Tuple[MarketRegime, float, Dict]:
    """
    Detect market regime from a dict of symbol DataFrames at given timestamp.

    Pure OHLCV — no live API calls. Safe to call in backtest replay.

    Signals (cross-sectional):
      1. Breadth:      % symbols with close > today's open
      2. VWAP breadth: % symbols with close > VWAP
      3. EMA bias:     median (EMA9 - EMA21) / EMA21 across all symbols
      4. ADX proxy:    median ADX across all symbols
      5. 5-bar ROC:    median 5-bar price return across all symbols
      6. Vol ratio:    median realized vol / historical vol (>2 = fear)

    Returns:
        (MarketRegime, confidence 0-1, details_dict)
    Fails open → (SIDEWAYS, 0.3, {})
    """
    try:
        today = now_ts.date()
        pct_above_open, pct_above_vwap, ema_align = [], [], []
        adx_vals, roc5_vals, vol_ratios = [], [], []

        for sym, df in data.items():
            try:
                if now_ts not in df.index:
                    continue
                idx = df.index.get_loc(now_ts)
                row = df.loc[now_ts]
                c = float(row.get("close", 0) or 0)
                if c <= 0:
                    continue

                today_df = df[df.index.date == today]
                if len(today_df) > 0:
                    day_open = float(today_df.iloc[0]["open"])
                    pct_above_open.append(1 if c > day_open else 0)

                vwap = float(row.get("vwap", 0) or 0)
                if vwap > 0:
                    pct_above_vwap.append(1 if c > vwap else 0)

                e9  = float(row.get("ema9",  c) or c)
                e21 = float(row.get("ema21", c) or c)
                if e21 > 0:
                    ema_align.append((e9 - e21) / e21)

                adx_vals.append(float(row.get("adx", 20) or 20))

                if idx >= 5:
                    c5 = float(df.iloc[idx - 5].get("close", c) or c)
                    if c5 > 0:
                        roc5_vals.append((c - c5) / c5)

                if idx >= 5:
                    rec  = df.iloc[max(0, idx - 5): idx + 1]
                    hist = df.iloc[max(0, idx - 20): idx + 1]
                    rv  = ((rec["high"]  - rec["low"])  / rec["close"].clip(lower=0.01)).mean()
                    hv  = ((hist["high"] - hist["low"]) / hist["close"].clip(lower=0.01)).mean()
                    if hv > 0:
                        vol_ratios.append(rv / hv)
            except Exception:
                continue

        if len(pct_above_open) < 3:
            return MarketRegime.SIDEWAYS, 0.3, {}

        breadth      = float(np.mean(pct_above_open))
        vwap_breadth = float(np.mean(pct_above_vwap)) if pct_above_vwap else 0.5
        ema_bias     = float(np.median(ema_align))     if ema_align     else 0.0
        median_adx   = float(np.median(adx_vals))      if adx_vals      else 20.0
        median_roc5  = float(np.median(roc5_vals))     if roc5_vals     else 0.0
        vol_ratio    = float(np.median(vol_ratios))    if vol_ratios    else 1.0

        details = {
            "breadth":      round(breadth, 3),
            "vwap_breadth": round(vwap_breadth, 3),
            "ema_bias":     round(ema_bias, 5),
            "median_adx":   round(median_adx, 1),
            "median_roc5":  round(median_roc5, 4),
            "vol_ratio":    round(vol_ratio, 2),
        }

        # High realized vol → regime uncertain, don't trade aggressively
        if vol_ratio > 2.0:
            return MarketRegime.SIDEWAYS, 0.5, details

        trending = median_adx >= 22
        bull_signals = int(breadth > 0.60) + int(vwap_breadth > 0.55) + \
                       int(ema_bias > 0.001) + int(median_roc5 > 0.001)
        bear_signals = int(breadth < 0.40) + int(vwap_breadth < 0.45) + \
                       int(ema_bias < -0.001) + int(median_roc5 < -0.001)

        confidence = abs(bull_signals - bear_signals) / 4.0

        if bull_signals >= 3 and trending:
            return MarketRegime.BULL_STRONG, min(confidence + 0.2, 1.0), details
        if bull_signals >= 2:
            return MarketRegime.BULL_WEAK, confidence, details
        if bear_signals >= 3 and trending:
            return MarketRegime.BEAR_STRONG, min(confidence + 0.2, 1.0), details
        if bear_signals >= 2:
            return MarketRegime.BEAR_WEAK, confidence, details
        return MarketRegime.SIDEWAYS, 1.0 - confidence, details

    except Exception:
        return MarketRegime.SIDEWAYS, 0.3, {}
