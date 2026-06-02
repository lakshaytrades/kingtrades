"""
vanna_charm_flow.py — Vanna & Charm Options Dealer Flow (v17.0)

Vanna = dDelta/dVol — when IV expands/contracts, dealers rebalance delta.
Charm = -dDelta/dt  — time decay accelerates delta changes near expiry.

Uses yfinance options chain. Fail-open (returns 0.0).
Thread-safe with 30-min TTL cache.
"""

import logging
import threading
import time as _time
from datetime import datetime, date
from typing import Dict, Optional, Tuple

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


# ── VIX fetcher ────────────────────────────────────────────────────────────────

def _get_vix_trend() -> Optional[str]:
    """
    Fetch VIX recent trend.  Returns 'falling', 'rising', or 'neutral'.
    """
    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d", interval="1d")
        if hist is None or len(hist) < 3:
            return "neutral"
        closes = hist["Close"].values
        if closes[-1] < closes[-3] * 0.97:
            return "falling"
        elif closes[-1] > closes[-3] * 1.03:
            return "rising"
        return "neutral"
    except Exception as exc:
        logger.debug(f"VIX trend fetch error: {exc}")
        return "neutral"


# ── Options chain Vanna approximation ─────────────────────────────────────────

def _compute_vanna_exposure(symbol: str, current_price: float) -> Optional[float]:
    """
    Approximate net Vanna exposure from yfinance options chain.
    Positive = dealers long Vanna (buy when IV falls).
    Negative = dealers short Vanna (sell when IV falls).
    """
    try:
        import yfinance as yf
        import numpy as np

        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return None

        # Use nearest 2 expirations
        expirations = expirations[:2]
        net_vanna = 0.0

        for exp in expirations:
            try:
                chain = ticker.option_chain(exp)
                calls = chain.calls
                puts  = chain.puts

                for df, sign in [(calls, 1), (puts, -1)]:
                    if df is None or df.empty:
                        continue
                    for _, row in df.iterrows():
                        strike = float(row.get("strike", 0))
                        oi     = float(row.get("openInterest", 0) or 0)
                        delta  = float(row.get("delta", 0.5) or 0.5)
                        iv     = float(row.get("impliedVolatility", 0.3) or 0.3)

                        if oi <= 0 or iv <= 0:
                            continue

                        # Vanna proxy: delta sensitivity to IV change
                        # Higher OTM = higher Vanna magnitude
                        moneyness = abs(strike - current_price) / (current_price + 1e-9)
                        vanna_proxy = sign * delta * moneyness * oi * iv

                        net_vanna += vanna_proxy
            except Exception:
                continue

        return net_vanna
    except Exception as exc:
        logger.debug(f"Vanna computation error: {exc}")
        return None


# ── Charm / OPEX detection ────────────────────────────────────────────────────

def _is_opex_friday() -> bool:
    """True if today is the 3rd Friday of the month (monthly OPEX)."""
    try:
        today = date.today()
        if today.weekday() != 4:  # 4 = Friday
            return False
        # Count Fridays so far this month
        day = today.day
        fridays_this_month = sum(
            1 for d in range(1, day + 1)
            if date(today.year, today.month, d).weekday() == 4
        )
        return fridays_this_month == 3
    except Exception:
        return False


def _get_nearest_dte(symbol: str) -> int:
    """Return DTE of nearest expiration. Returns 30 on error."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        exps = ticker.options
        if not exps:
            return 30
        nearest = exps[0]
        exp_date = datetime.strptime(nearest, "%Y-%m-%d").date()
        dte = max(0, (exp_date - date.today()).days)
        return dte
    except Exception:
        return 30


# ── Public API ────────────────────────────────────────────────────────────────

def get_vanna_charm_score(
    symbol: str, direction: str, current_price: float
) -> Tuple[float, str]:
    """
    Vanna/Charm options dealer hedging flow score.

    Score (LONG):
      Positive Vanna + VIX falling : +10
      Negative Vanna + VIX rising  : +10 SHORT confirmation → -8 LONG
      Strong Charm flow (DTE < 5)  : +6
      OPEX Friday: all scores × 1.5

    Fail-open: returns (0.0, "vanna_charm N/A") on any error.
    30-min TTL cache.
    """
    cache_key = f"{symbol}:{direction}:{current_price:.0f}"
    hit, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"vanna_charm cached: {cached_reason}"

    try:
        score = 0.0
        reasons = []

        vix_trend = _get_vix_trend()
        net_vanna = _compute_vanna_exposure(symbol, current_price)
        dte       = _get_nearest_dte(symbol)
        opex      = _is_opex_friday()

        # Vanna flow scoring
        if net_vanna is not None:
            if direction == "LONG":
                if net_vanna > 0 and vix_trend == "falling":
                    score += 10.0
                    reasons.append(f"vanna_bull(vix={vix_trend})")
                elif net_vanna < 0 and vix_trend == "rising":
                    score -= 8.0
                    reasons.append(f"vanna_bear(vix={vix_trend})")
            else:  # SHORT
                if net_vanna < 0 and vix_trend == "rising":
                    score += 10.0
                    reasons.append(f"vanna_bear_short(vix={vix_trend})")
                elif net_vanna > 0 and vix_trend == "falling":
                    score -= 8.0
                    reasons.append(f"vanna_bull_vs_short(vix={vix_trend})")

        # Charm (DTE-based)
        if dte <= 5:
            score += 6.0
            reasons.append(f"charm_near_expiry(dte={dte})")

        # OPEX multiplier
        if opex:
            score *= 1.5
            reasons.append("opex_friday")

        # Clamp
        score = float(max(-15.0, min(15.0, score)))
        reason = " ".join(reasons) if reasons else "vanna_neutral"

        _cache_set(cache_key, score, reason)
        return score, reason
    except Exception as exc:
        logger.debug(f"get_vanna_charm_score error (fail-open): {exc}")
        return 0.0, "vanna_charm N/A"
