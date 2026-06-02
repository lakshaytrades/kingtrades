"""
mcclellan_engine.py — McClellan Oscillator + Zweig Breadth Thrust (v17.0)

Approximates the McClellan Oscillator using sector ETFs as an A/D proxy.
Also detects Zweig Breadth Thrust (rare, very bullish signal).

Thread-safe singleton with 1-hour TTL cache.  Fail-open (returns 0.0).
"""

import logging
import threading
import time as _time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Proxy basket of liquid ETFs ───────────────────────────────────────────────
_PROXY_BASKET = [
    "SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLV", "XLI",
    "XLY", "XLP", "XLB", "XLU", "XLRE", "GLD", "TLT", "HYG",
    "SLV", "USO", "VXX", "DIA",
]

# ── TTL cache ─────────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, float, str]] = {}
_cache_lock = threading.Lock()
_TTL = 3600  # 1 hour


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


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(values: List[float], period: int) -> float:
    """Compute EMA of a list. Returns last value."""
    if not values:
        return 0.0
    k = 2.0 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


# ── Data fetcher ──────────────────────────────────────────────────────────────

def _fetch_basket_returns(lookback: int = 45) -> Optional[List[List[float]]]:
    """
    Fetch daily close prices for proxy basket.
    Returns list of return series per ETF (5-day returns).
    Fail-open → None.
    """
    try:
        import yfinance as yf
        import pandas as pd

        tickers = yf.download(
            " ".join(_PROXY_BASKET),
            period=f"{lookback}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        closes = tickers["Close"] if isinstance(tickers.columns, pd.MultiIndex) else tickers
        if closes.empty:
            return None

        # 5-day returns per ETF
        returns_matrix = []
        for col in closes.columns:
            series = closes[col].dropna().values
            if len(series) >= 10:
                rets = []
                for i in range(5, len(series)):
                    r = (series[i] - series[i - 5]) / series[i - 5]
                    rets.append(r)
                returns_matrix.append(rets)
        return returns_matrix if returns_matrix else None
    except Exception as exc:
        logger.debug(f"McClellan basket fetch error: {exc}")
        return None


def _compute_mcclellan(returns_matrix: List[List[float]]) -> float:
    """
    Approximate McClellan Oscillator.
    Classify each ETF's recent 5-day return as advancing (+1) or declining (-1).
    Compute A-D series across ETFs over time, then 19-EMA minus 39-EMA.
    """
    if not returns_matrix:
        return 0.0

    # Align lengths
    min_len = min(len(r) for r in returns_matrix)
    if min_len < 5:
        return 0.0

    ad_series = []
    for t in range(min_len):
        advancing = sum(1 for r in returns_matrix if r[t] > 0)
        declining = sum(1 for r in returns_matrix if r[t] <= 0)
        ad_series.append(advancing - declining)

    if len(ad_series) < 10:
        return 0.0

    ema19 = _ema(ad_series, 19)
    ema39 = _ema(ad_series, min(39, len(ad_series)))
    return ema19 - ema39


# ── Public API ────────────────────────────────────────────────────────────────

def get_mcclellan_score(direction: str) -> Tuple[float, str]:
    """
    McClellan Oscillator approximation using sector ETF proxy basket.

    Score (LONG):
      > +100 : +8 (breadth thrust)
      > +50  : +5
      < -50  : -5
      < -100 : -8

    Fail-open: returns (0.0, "mcclellan N/A") on any error.
    1-hour TTL cache.
    """
    cache_key = f"mcclellan:{direction}"
    hit, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"mcclellan cached: {cached_reason}"

    try:
        returns_matrix = _fetch_basket_returns(lookback=50)
        if returns_matrix is None:
            return 0.0, "mcclellan data N/A"

        osc = _compute_mcclellan(returns_matrix)

        if direction == "LONG":
            if osc > 100:
                score = 8.0
            elif osc > 50:
                score = 5.0
            elif osc < -100:
                score = -8.0
            elif osc < -50:
                score = -5.0
            else:
                score = 0.0
        else:  # SHORT
            if osc < -100:
                score = 8.0
            elif osc < -50:
                score = 5.0
            elif osc > 100:
                score = -8.0
            elif osc > 50:
                score = -5.0
            else:
                score = 0.0

        reason = f"McClellan={osc:.1f}"
        _cache_set(cache_key, score, reason)
        return score, reason
    except Exception as exc:
        logger.debug(f"get_mcclellan_score error (fail-open): {exc}")
        return 0.0, "mcclellan N/A"


def get_breadth_thrust_score(direction: str) -> Tuple[float, str]:
    """
    Zweig Breadth Thrust detection.

    Checks if advancing ratio went from <40% to >61.5% in last 10 bars.
    Returns +15 on confirmed thrust (LONG), 0 otherwise.

    Fail-open: returns (0.0, "breadth_thrust N/A") on any error.
    1-hour TTL cache.
    """
    cache_key = f"breadth_thrust:{direction}"
    hit, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"breadth_thrust cached: {cached_reason}"

    try:
        returns_matrix = _fetch_basket_returns(lookback=30)
        if returns_matrix is None:
            return 0.0, "breadth_thrust data N/A"

        min_len = min(len(r) for r in returns_matrix)
        if min_len < 12:
            return 0.0, "breadth_thrust: insufficient data"

        n_etfs = len(returns_matrix)
        if n_etfs == 0:
            return 0.0, "breadth_thrust: no ETFs"

        # Compute advancing ratio for last 12 bars
        adv_ratios = []
        for t in range(max(0, min_len - 12), min_len):
            advancing = sum(1 for r in returns_matrix if r[t] > 0)
            adv_ratios.append(advancing / n_etfs)

        # Zweig Breadth Thrust: ratio started below 0.40 then crossed above 0.615
        if len(adv_ratios) >= 10:
            start_ratio = adv_ratios[0]
            end_ratio   = adv_ratios[-1]

            if start_ratio < 0.40 and end_ratio > 0.615:
                if direction == "LONG":
                    score = 15.0
                    reason = f"Zweig_BT: {start_ratio:.2f}→{end_ratio:.2f}"
                    _cache_set(cache_key, score, reason)
                    return score, reason
                else:
                    # Thrust is bullish signal — penalize SHORT
                    score = -10.0
                    reason = f"Zweig_BT_bearish: {start_ratio:.2f}→{end_ratio:.2f}"
                    _cache_set(cache_key, score, reason)
                    return score, reason

        _cache_set(cache_key, 0.0, "no_thrust")
        return 0.0, "no_breadth_thrust"
    except Exception as exc:
        logger.debug(f"get_breadth_thrust_score error (fail-open): {exc}")
        return 0.0, "breadth_thrust N/A"
