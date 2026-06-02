"""
copper_gold_ratio.py — Dr. Copper Economic Health Indicator (v17.0)

Copper/Gold ratio = risk appetite proxy.
Rising ratio = economic expansion = risk-on = bullish equities.
Falling ratio = economic contraction = risk-off = bearish.

Uses yfinance CPER (copper ETF) and GLD (gold ETF).
Thread-safe singleton with 1-hour TTL cache.  Fail-open (returns 0.0).
"""

import logging
import threading
import time as _time
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

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


# ── Data fetcher ──────────────────────────────────────────────────────────────

def _fetch_ratio_data() -> Optional[Tuple[float, float, float, float]]:
    """
    Fetch CPER and GLD prices.
    Returns (ratio_now, ratio_20d_ago, cper_5d_ret, gld_5d_ret) or None.
    """
    try:
        import yfinance as yf
        import pandas as pd

        tickers = yf.download(
            "CPER GLD",
            period="35d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        if isinstance(tickers.columns, pd.MultiIndex):
            closes = tickers["Close"]
        else:
            closes = tickers

        if "CPER" not in closes.columns or "GLD" not in closes.columns:
            # Fallback: try HG=F (copper futures) and GLD individually
            return None

        cper = closes["CPER"].dropna().values
        gld  = closes["GLD"].dropna().values

        min_len = min(len(cper), len(gld))
        if min_len < 10:
            return None

        cper = cper[-min_len:]
        gld  = gld[-min_len:]

        # Ratio series
        ratio = cper / (gld + 1e-9)
        ratio_now    = float(ratio[-1])
        ratio_20d    = float(ratio[-min(20, len(ratio))])

        # 5-day individual returns
        cper_5d_ret = float((cper[-1] - cper[-min(5, len(cper))]) / (cper[-min(5, len(cper))] + 1e-9))
        gld_5d_ret  = float((gld[-1]  - gld[-min(5, len(gld))])   / (gld[-min(5, len(gld))]   + 1e-9))

        return ratio_now, ratio_20d, cper_5d_ret, gld_5d_ret
    except Exception as exc:
        logger.debug(f"copper_gold data fetch error: {exc}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_copper_gold_score(direction: str) -> Tuple[float, str]:
    """
    Copper/Gold ratio economic health score.

    Score (LONG):
      Ratio rising > 2% over 20 days:    +8 (economic strength)
      Ratio flat ±1%:                     0  (neutral)
      Ratio falling > 2% over 20 days:   -6 (economic worry)

    Also: CPER 5-day return vs GLD 5-day return for short-term signal.

    Fail-open: returns (0.0, "copper_gold N/A") on any error.
    1-hour TTL cache.
    """
    cache_key = f"copper_gold:{direction}"
    hit, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"copper_gold cached: {cached_reason}"

    try:
        data = _fetch_ratio_data()
        if data is None:
            return 0.0, "copper_gold: data N/A"

        ratio_now, ratio_20d, cper_5d, gld_5d = data

        # 20-day ratio change
        ratio_chg_20d = (ratio_now - ratio_20d) / (ratio_20d + 1e-9)

        # Short-term: copper stronger than gold = risk-on
        short_term_bull = cper_5d > gld_5d + 0.01   # CPER beats GLD by 1%+
        short_term_bear = gld_5d  > cper_5d + 0.01  # GLD beats CPER by 1%+

        score = 0.0
        reasons = []

        if direction == "LONG":
            if ratio_chg_20d > 0.02:
                score += 8.0
                reasons.append(f"Cu/Au_rising+{ratio_chg_20d:.1%}")
            elif ratio_chg_20d < -0.02:
                score -= 6.0
                reasons.append(f"Cu/Au_falling{ratio_chg_20d:.1%}")

            if short_term_bull:
                score += 3.0
                reasons.append(f"Cu_vs_Au_5d(Cu={cper_5d:.1%})")
            elif short_term_bear:
                score -= 3.0
                reasons.append(f"Au_vs_Cu_5d(Au={gld_5d:.1%})")

        else:  # SHORT
            if ratio_chg_20d < -0.02:
                score += 6.0
                reasons.append(f"Cu/Au_falling_short+{abs(ratio_chg_20d):.1%}")
            elif ratio_chg_20d > 0.02:
                score -= 8.0
                reasons.append(f"Cu/Au_rising_vs_short{ratio_chg_20d:.1%}")

            if short_term_bear:
                score += 3.0
                reasons.append(f"Au_stronger_short(Au={gld_5d:.1%})")
            elif short_term_bull:
                score -= 3.0
                reasons.append(f"Cu_stronger_vs_short")

        score = float(max(-10.0, min(10.0, score)))
        reason = " ".join(reasons) if reasons else f"Cu/Au={ratio_now:.4f}(chg={ratio_chg_20d:.1%})"

        _cache_set(cache_key, score, reason)
        return score, reason
    except Exception as exc:
        logger.debug(f"get_copper_gold_score error (fail-open): {exc}")
        return 0.0, "copper_gold N/A"
