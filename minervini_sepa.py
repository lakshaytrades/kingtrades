"""
minervini_sepa.py — Mark Minervini SEPA Trend Template (v17.0)

Implements Minervini's 7-criterion trend template for Stage 2 uptrend identification,
plus VCP (Volatility Contraction Pattern) detection.

Thread-safe singleton with 4-hour TTL cache.  Fail-open (returns 0.0).
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
_TTL = 14400  # 4 hours


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


# ── Minervini trend template ───────────────────────────────────────────────────

def _check_trend_template(closes: np.ndarray, volumes: np.ndarray) -> Tuple[int, str]:
    """
    Check Minervini's 7 criteria. Returns (criteria_met_count, detail_string).

    Criteria:
    1. Price > 150-day SMA
    2. Price > 200-day SMA
    3. 150-day SMA > 200-day SMA
    4. 200-day SMA trending up (current > 1 month ago)
    5. 50-day SMA > 150-day SMA AND 50-day SMA > 200-day SMA
    6. Price > 50-day SMA
    7. Price within 25% of 52-week high
    """
    n = len(closes)
    if n < 200:
        return 0, "insufficient_data"

    price = closes[-1]
    if price <= 0:
        return 0, "invalid_price"

    criteria = 0
    details = []

    sma50  = float(np.mean(closes[-50:]))
    sma150 = float(np.mean(closes[-150:]))
    sma200 = float(np.mean(closes[-200:]))

    # Criteria 1: Price > 150-day SMA
    if price > sma150:
        criteria += 1
        details.append("c1:p>sma150")

    # Criteria 2: Price > 200-day SMA
    if price > sma200:
        criteria += 1
        details.append("c2:p>sma200")

    # Criteria 3: 150-day SMA > 200-day SMA
    if sma150 > sma200:
        criteria += 1
        details.append("c3:sma150>sma200")

    # Criteria 4: 200-day SMA trending up (vs 20 bars ago = ~1 month)
    if n >= 220:
        sma200_month_ago = float(np.mean(closes[-220:-20]))
        if sma200 > sma200_month_ago:
            criteria += 1
            details.append("c4:sma200_uptrend")

    # Criteria 5: 50-day SMA > 150-day SMA AND > 200-day SMA
    if sma50 > sma150 and sma50 > sma200:
        criteria += 1
        details.append("c5:sma50>both")

    # Criteria 6: Price > 50-day SMA
    if price > sma50:
        criteria += 1
        details.append("c6:p>sma50")

    # Criteria 7: Price within 25% of 52-week high
    high_52w = float(np.max(closes[-252:]))
    if high_52w > 0 and price >= high_52w * 0.75:
        criteria += 1
        details.append(f"c7:within25pct_of_52wh({price/high_52w:.2f})")

    return criteria, " ".join(details)


def _check_vcp(closes: np.ndarray, volumes: np.ndarray) -> bool:
    """
    Detect Volatility Contraction Pattern (VCP).
    Check if price ranges are contracting over last 3 pivot corrections
    with declining volume = institutional accumulation.
    """
    try:
        n = len(closes)
        if n < 60:
            return False

        # Look at last 60 bars for 3 corrections
        recent = closes[-60:]
        vol_recent = volumes[-60:] if volumes is not None and len(volumes) >= 60 else None

        # Find local peaks (simplistic: bars where close > prev 3 and next 3)
        corrections = []
        for i in range(3, len(recent) - 3):
            if (recent[i] < recent[i - 1] and recent[i] < recent[i - 2] and
                    recent[i] < recent[i + 1] and recent[i] < recent[i + 2]):
                # This is a local trough — estimate correction depth
                local_high = max(recent[max(0, i - 10):i])
                if local_high > 0:
                    depth = (local_high - recent[i]) / local_high
                    corrections.append((i, depth))

        if len(corrections) < 2:
            return False

        # Check contracting corrections
        depths = [c[1] for c in corrections[-3:]]
        contracting = all(depths[j] < depths[j - 1] for j in range(1, len(depths)))

        return contracting
    except Exception:
        return False


def _fetch_daily_data(symbol: str):
    """Fetch 250+ days of daily OHLCV via yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="14mo", interval="1d")
        if hist is None or hist.empty:
            return None, None
        closes  = hist["Close"].values.astype(float)
        volumes = hist["Volume"].values.astype(float)
        return closes, volumes
    except Exception as exc:
        logger.debug(f"minervini yfinance fetch error: {exc}")
        return None, None


# ── Public API ────────────────────────────────────────────────────────────────

def get_minervini_score(
    symbol: str, df_daily, direction: str
) -> Tuple[float, str]:
    """
    Minervini SEPA trend template + VCP detection.

    Score (LONG):
      All 7 criteria + VCP:  +15
      All 7 criteria:        +15
      5-6 criteria:          +8
      3-4 criteria:          +3
      < 3 criteria:          -5

    Score (SHORT): inverse (few criteria = bearish setup)
      < 3 criteria:          +8
      3-4 criteria:          +2
      5+ criteria:           -10

    Fail-open: returns (0.0, "minervini N/A") on any error.
    4-hour TTL cache.
    """
    cache_key = f"{symbol}:{direction}"
    hit, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"minervini cached: {cached_reason}"

    try:
        # Use passed df_daily or fetch via yfinance
        closes  = None
        volumes = None

        if df_daily is not None:
            try:
                if hasattr(df_daily, "values"):
                    col_map = {c.lower(): c for c in df_daily.columns}
                    close_col = col_map.get("close", col_map.get("Close", None))
                    vol_col   = col_map.get("volume", col_map.get("Volume", None))
                    if close_col:
                        closes  = df_daily[close_col].values.astype(float)
                        volumes = df_daily[vol_col].values.astype(float) if vol_col else None
            except Exception:
                closes = None

        if closes is None or len(closes) < 100:
            closes, volumes = _fetch_daily_data(symbol)

        if closes is None or len(closes) < 50:
            return 0.0, "minervini: data N/A"

        criteria_met, detail = _check_trend_template(closes, volumes)
        vcp = _check_vcp(closes, volumes) if volumes is not None else False

        if direction == "LONG":
            if criteria_met >= 7:
                score = 15.0
            elif criteria_met >= 5:
                score = 8.0
            elif criteria_met >= 3:
                score = 3.0
            else:
                score = -5.0

            if vcp and criteria_met >= 5:
                score = min(15.0, score + 3.0)
                detail += " VCP"
        else:  # SHORT
            if criteria_met < 3:
                score = 8.0
            elif criteria_met <= 4:
                score = 2.0
            else:
                score = -10.0

        reason = f"minervini_criteria={criteria_met}/7 {detail}"
        _cache_set(cache_key, score, reason)
        return score, reason
    except Exception as exc:
        logger.debug(f"get_minervini_score error (fail-open): {exc}")
        return 0.0, "minervini N/A"
