"""
economic_surprise.py — FRED macro economic surprise scorer (v17.0)

Compares latest FRED data releases against 6-month rolling averages to detect
positive/negative economic surprises that influence market direction.

FRED allows anonymous requests for recent data with api_key= (empty key).

Thread-safe singleton with 2-hour TTL cache.  Fail-open (returns 0.0).
"""

import logging
import threading
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── FRED series definitions ───────────────────────────────────────────────────
FRED_SERIES = {
    "NFP":          "PAYEMS",    # Non-Farm Payrolls
    "CPI":          "CPIAUCSL",  # Consumer Price Index
    "UNEMPLOYMENT": "UNRATE",    # Unemployment Rate
    "PMI_MFG":      "MANEMP",    # Manufacturing Employment
    "RETAIL_SALES": "RSAFS",     # Retail & Food Services Sales
}

# ── TTL cache ─────────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, float, str]] = {}
_cache_lock = threading.Lock()
_TTL = 7200  # 2 hours


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


# ── FRED fetcher ──────────────────────────────────────────────────────────────

def _fetch_fred_series(series_id: str, limit: int = 7) -> Optional[List[float]]:
    """Fetch latest observations from FRED. Returns list of values (newest last)."""
    try:
        import urllib.request
        import json

        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key=&file_type=json"
            f"&limit={limit}&sort_order=desc"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "KingTradesBot research@kingtrades.ai"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())

        obs = data.get("observations", [])
        values = []
        for o in obs:
            v = o.get("value", ".")
            if v not in (".", ""):
                try:
                    values.append(float(v))
                except ValueError:
                    pass

        if len(values) < 3:
            return None
        # Reverse so index 0 = oldest, -1 = newest
        return list(reversed(values))
    except Exception as exc:
        logger.debug(f"FRED fetch {series_id} error: {exc}")
        return None


def _series_surprise(values: List[float]) -> str:
    """Compare most recent value to rolling average. Returns 'POS', 'NEG', or 'NEUTRAL'."""
    if len(values) < 4:
        return "NEUTRAL"
    history = np.array(values[:-1])
    latest  = values[-1]
    mean    = float(np.mean(history))
    std     = float(np.std(history))
    if std < 1e-9:
        return "NEUTRAL"
    z = (latest - mean) / std
    if z > 1.0:
        return "POS"
    elif z < -1.0:
        return "NEG"
    return "NEUTRAL"


# ── Public API ────────────────────────────────────────────────────────────────

def get_economic_surprise_score(direction: str) -> Tuple[float, str]:
    """
    FRED macro surprise scorer.

    Checks 5 series: NFP, CPI, Unemployment, PMI_MFG, Retail Sales.
    2+ positive surprises  → LONG +8, SHORT -6
    2+ negative surprises  → LONG -8, SHORT +8
    Mixed                  → 0

    Returns (score_delta, reason_string).
    Fail-open: returns (0.0, "econ_surprise N/A") on any error.
    2-hour TTL cache (direction-specific).
    """
    cache_key = f"econ:{direction}"
    hit, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"econ cached: {cached_reason}"

    try:
        pos_count  = 0
        neg_count  = 0
        details    = []

        for name, series_id in FRED_SERIES.items():
            vals = _fetch_fred_series(series_id, limit=7)
            if vals is None:
                continue
            surprise = _series_surprise(vals)
            details.append(f"{name}={surprise}")
            if surprise == "POS":
                pos_count += 1
            elif surprise == "NEG":
                neg_count += 1

        reason = " ".join(details) if details else "no_data"

        if pos_count >= 2:
            score = 8.0 if direction == "LONG" else -6.0
        elif neg_count >= 2:
            score = -8.0 if direction == "LONG" else 8.0
        else:
            score = 0.0

        _cache_set(cache_key, score, reason)
        return score, reason
    except Exception as exc:
        logger.debug(f"get_economic_surprise_score error (fail-open): {exc}")
        return 0.0, "econ_surprise N/A"
