"""
institutional_strategies_india.py — Institutional strategies for NSE India
Same 6 frameworks as the US bot, adapted for Indian market:
  - IST timezone for power hour and gap fade
  - Indian stock pairs for stat arb
  - .NS suffix for yfinance data

Re-exports shared strategies unchanged; overrides India-specific ones.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

# Import shared strategies from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from institutional_strategies import (
    get_cross_sectional_rank,
    get_vwap_reclaim_score,
    get_tod_rvol_score,
    get_sortino_size_multiplier,
    record_trade_result,
    record_bar_volume,
)

logger = logging.getLogger("inst_india")
IST = ZoneInfo("Asia/Kolkata")

# ── Indian stock pairs for stat arb ──────────────────────────────────────────
PAIRS_INDIA: List[Tuple[str, str]] = [
    ("RELIANCE",  "ONGC"),       # Oil & Gas
    ("INFY",      "TCS"),        # IT giants
    ("HDFCBANK",  "ICICIBANK"),  # Private banks
    ("AXISBANK",  "KOTAKBANK"),  # Private banks
    ("TATASTEEL", "JSWSTEEL"),   # Steel
    ("HINDUNILVR","DABUR"),      # FMCG
]

_pairs_cache: Dict = {}
_PAIRS_TTL = 1800.0  # 30 min cache


# ── Power hour (IST) ─────────────────────────────────────────────────────────

def get_power_hour_score_india(direction: str, current_score: float) -> Tuple[float, str]:
    """
    Score boost for IST power windows:
      Opening range:  9:15–9:30 AM IST (high-volatility institutional activity)
      Power hour:     3:00–3:30 PM IST (institutional rebalancing + MIS square-off)
    Returns (score_delta, reason).
    """
    try:
        now = datetime.now(IST).time()
        from datetime import time

        opening_start = time(9, 15)
        opening_end   = time(9, 30)
        power_start   = time(15, 0)
        power_end     = time(15, 30)

        if opening_start <= now <= opening_end:
            if current_score >= 68:
                return (8.0, "opening_range_IST high-conviction")
            return (4.0, "opening_range_IST")

        if power_start <= now <= power_end:
            if current_score >= 72:
                return (10.0, "power_hour_IST high-conviction")
            return (6.0, "power_hour_IST")

    except Exception as e:
        logger.debug(f"power_hour_india: {e}")
    return (0.0, "")


# ── Gap fade (IST) ────────────────────────────────────────────────────────────

_GAP_FADE_MIN   = 0.015   # 1.5% minimum gap
_GAP_STRONG     = 0.030   # 3.0% strong gap
_FADE_WINDOW    = 45      # first 45 min of session (9:15–10:00 AM IST)

_gap_cache: Dict[str, float] = {}   # symbol → prev_close

def get_gap_fade_score_india(symbol: str, direction: str,
                              current_price: float = 0.0) -> Tuple[float, str]:
    """
    Gap fade for NSE India (9:15–10:00 AM IST window).
    Gaps >1.5% tend to fill within first session hour.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        now_ist = datetime.now(IST)
        from datetime import time
        session_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        elapsed_min   = (now_ist - session_start).total_seconds() / 60

        if elapsed_min < 0 or elapsed_min > _FADE_WINDOW:
            return (0.0, "")   # outside fade window

        # Fetch prev close via yfinance if not cached
        if symbol not in _gap_cache:
            try:
                import yfinance as yf
                hist = yf.download(f"{symbol}.NS", period="2d",
                                   interval="1d", progress=False, auto_adjust=True)
                if hist is not None and len(hist) >= 2:
                    if isinstance(hist.columns, pd.MultiIndex):
                        hist.columns = [str(c[0]).lower() for c in hist.columns]
                    else:
                        hist.columns = [str(c).lower() for c in hist.columns]
                    _gap_cache[symbol] = float(hist["close"].iloc[-2])
            except Exception:
                return (0.0, "")

        prev_close = _gap_cache.get(symbol, 0.0)
        if prev_close <= 0 or current_price <= 0:
            return (0.0, "")

        gap_pct = (current_price - prev_close) / prev_close

        if gap_pct >= _GAP_STRONG and direction == "SHORT":
            return (18.0, f"gap_fade_strong_up_india +{gap_pct:.1%}")
        if gap_pct >= _GAP_FADE_MIN and direction == "SHORT":
            return (12.0, f"gap_fade_up_india +{gap_pct:.1%}")
        if gap_pct <= -_GAP_STRONG and direction == "LONG":
            return (18.0, f"gap_fade_strong_down_india {gap_pct:.1%}")
        if gap_pct <= -_GAP_FADE_MIN and direction == "LONG":
            return (12.0, f"gap_fade_down_india {gap_pct:.1%}")

        # Trading WITH the gap = penalty
        if gap_pct >= _GAP_FADE_MIN and direction == "LONG":
            return (-8.0, f"gap_chase_penalty_india +{gap_pct:.1%}")
        if gap_pct <= -_GAP_FADE_MIN and direction == "SHORT":
            return (-8.0, f"gap_chase_penalty_india {gap_pct:.1%}")

    except Exception as e:
        logger.debug(f"gap_fade_india {symbol}: {e}")
    return (0.0, "")


# ── Statistical pairs (Indian universe) ─────────────────────────────────────

import time as _time
import numpy as np

def get_pairs_signal_india(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Statistical pairs convergence for Indian stocks.
    Same logic as US stat arb but uses PAIRS_INDIA universe.
    """
    import time as _t
    now = _t.monotonic()

    # Check if symbol is in any pair
    relevant_pairs = [(a, b) for a, b in PAIRS_INDIA
                      if a == symbol.upper() or b == symbol.upper()]
    if not relevant_pairs:
        return (0.0, "")

    cache_key = symbol
    if cache_key in _pairs_cache:
        cached_ts, cached_val = _pairs_cache[cache_key]
        if now - cached_ts < _PAIRS_TTL:
            return cached_val

    try:
        import yfinance as yf
        for sym_a, sym_b in relevant_pairs:
            try:
                tickers = [f"{sym_a}.NS", f"{sym_b}.NS"]
                hist_raw = yf.download(tickers, period="25d", interval="1d",
                                       progress=False, auto_adjust=True)
                if hist_raw is None or hist_raw.shape[0] < 20:
                    continue
                if isinstance(hist_raw.columns, pd.MultiIndex):
                    lvl0 = hist_raw.columns.get_level_values(0).str.lower()
                    if "close" in lvl0.tolist():
                        hist = hist_raw.xs("Close", level=0, axis=1, drop_level=True) \
                               if "Close" in hist_raw.columns.get_level_values(0) \
                               else hist_raw.xs("close", level=0, axis=1, drop_level=True)
                    else:
                        continue
                else:
                    col = "Close" if "Close" in hist_raw.columns else "close"
                    hist = hist_raw[col]

                series_a = hist[f"{sym_a}.NS"].dropna() if f"{sym_a}.NS" in hist.columns else pd.Series(dtype=float)
                series_b = hist[f"{sym_b}.NS"].dropna() if f"{sym_b}.NS" in hist.columns else pd.Series(dtype=float)
                if len(series_a) < 15 or len(series_b) < 15:
                    continue

                # Normalised spread
                norm_a = series_a / series_a.iloc[0]
                norm_b = series_b / series_b.iloc[0]
                spread = norm_a - norm_b
                z = (spread.iloc[-1] - spread.mean()) / (spread.std() + 1e-9)

                is_sym_a = symbol.upper() == sym_a

                # Positive z → sym_a overpriced vs sym_b
                if z > 2.0:
                    if (is_sym_a and direction == "SHORT") or (not is_sym_a and direction == "LONG"):
                        result = (10.0 if z < 3.0 else 15.0,
                                  f"pairs_{sym_a}/{sym_b} z={z:.1f} reversion")
                        _pairs_cache[cache_key] = (now, result)
                        return result
                    result = (-5.0, f"pairs_{sym_a}/{sym_b} z={z:.1f} against_reversion")
                    _pairs_cache[cache_key] = (now, result)
                    return result

                if z < -2.0:
                    if (is_sym_a and direction == "LONG") or (not is_sym_a and direction == "SHORT"):
                        result = (10.0 if z > -3.0 else 15.0,
                                  f"pairs_{sym_a}/{sym_b} z={z:.1f} reversion")
                        _pairs_cache[cache_key] = (now, result)
                        return result
                    result = (-5.0, f"pairs_{sym_a}/{sym_b} z={z:.1f} against_reversion")
                    _pairs_cache[cache_key] = (now, result)
                    return result

            except Exception:
                continue
    except Exception as e:
        logger.debug(f"pairs_india {symbol}: {e}")

    _pairs_cache[cache_key] = (now, (0.0, ""))
    return (0.0, "")
