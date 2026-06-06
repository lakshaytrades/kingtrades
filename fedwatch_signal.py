"""
fedwatch_signal.py — CME FedWatch + Full FRED Yield Curve (L99 Macro)

Two macro signals:

1. Full FRED Yield Curve: DGS3MO, DGS2, DGS5, DGS10, DGS30
   Shapes: inverted / flat / normal / steep → risk regime classification
   Source: fred.stlouisfed.org CSV (free, no key needed)

2. Fed Rate Expectation Proxy:
   Compare 3M Treasury vs Fed Funds Effective Rate.
   When 3M < Fed Funds by >50bps → market pricing in cut = risk-on.
   Source: FRED FEDFUNDS + DGS3MO series

Bloomberg charges $2k/month for this data.
FRED gives it free in 1 API call.
"""
import logging
import time as _time
import requests
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict = {}
_FRED_TTL = 3600.0  # 1h

def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _FRED_TTL:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _fetch_fred(series_id: str) -> Optional[float]:
    """Fetch latest value for a FRED series. No API key required."""
    cached = _cache_get(f"fred_{series_id}")
    if cached is not None:
        return cached
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        r = requests.get(url, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        lines = [l for l in r.text.strip().splitlines()
                 if l and not l.startswith("DATE")]
        for line in reversed(lines):
            val_str = line.split(",")[-1].strip()
            if val_str and val_str != ".":
                try:
                    val = float(val_str)
                    _cache_set(f"fred_{series_id}", val)
                    return val
                except ValueError:
                    continue
    except Exception as exc:
        logger.debug(f"[fedwatch_fred] {series_id}: {exc}")
    return None


def _get_curve_shape() -> Tuple[str, float]:
    """
    Classify yield curve shape from FRED data.
    Returns (shape, 2Y-10Y_spread).
    """
    two_y  = _fetch_fred("DGS2")
    ten_y  = _fetch_fred("DGS10")
    thirty = _fetch_fred("DGS30")
    three_m = _fetch_fred("DGS3MO")

    if two_y is None or ten_y is None:
        return "unknown", 0.0

    spread_2_10 = ten_y - two_y

    if spread_2_10 < -0.30:
        return "inverted", spread_2_10
    elif spread_2_10 < 0.20:
        return "flat", spread_2_10
    elif thirty is not None and thirty < ten_y:
        return "humped", spread_2_10
    elif spread_2_10 > 1.50:
        return "steep", spread_2_10
    else:
        return "normal", spread_2_10


def _get_cut_probability() -> Optional[float]:
    """
    Estimate Fed cut probability from FRED.
    Returns 0-100 (% probability of cut at next FOMC).
    """
    cached = _cache_get("cut_prob")
    if cached is not None:
        return cached
    try:
        fedfunds = _fetch_fred("FEDFUNDS")
        t3m      = _fetch_fred("DGS3MO")
        if fedfunds is None or t3m is None:
            return None
        # 3M Treasury discount vs Fed Funds = market-implied cut expectation
        spread = t3m - fedfunds
        if spread < -0.75:
            prob = min(90.0, 55.0 + abs(spread) * 40.0)
        elif spread < -0.30:
            prob = 40.0
        elif spread > 0.50:
            prob = 10.0  # hike expected
        else:
            prob = 25.0
        _cache_set("cut_prob", prob)
        return prob
    except Exception as exc:
        logger.debug(f"[fedwatch_cut_prob] {exc}")
        return None


def get_fedwatch_score(direction: str) -> Tuple[float, str]:
    """
    CME FedWatch + FRED full yield curve macro score.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        total = 0.0
        parts = []

        shape, spread = _get_curve_shape()
        if shape != "unknown":
            if shape == "inverted":
                d = -7.0 if direction == "LONG" else 5.0
                parts.append(f"curve=INVERTED(2s10s={spread:+.2f}%)")
                total += d
            elif shape == "steep":
                d = 4.0 if direction == "LONG" else -2.0
                parts.append(f"curve=STEEP(2s10s={spread:+.2f}%)")
                total += d
            elif shape == "flat":
                d = -2.0 if direction == "LONG" else 1.0
                parts.append(f"curve=FLAT(2s10s={spread:+.2f}%)")
                total += d
            elif shape == "humped":
                d = -3.0 if direction == "LONG" else 2.0
                parts.append(f"curve=HUMPED")
                total += d

        cut_prob = _get_cut_probability()
        if cut_prob is not None:
            if cut_prob > 70:
                d = 5.0 if direction == "LONG" else -3.0
                parts.append(f"fedwatch=CUT_LIKELY({cut_prob:.0f}%)")
                total += d
            elif cut_prob < 20:
                d = -3.0 if direction == "LONG" else 4.0
                parts.append(f"fedwatch=HIKE({cut_prob:.0f}%_cut_prob)")
                total += d

        if not parts:
            return 0.0, "fedwatch:no-data"
        return float(total), f"MACRO[{' | '.join(parts)}]"
    except Exception as exc:
        logger.debug(f"[fedwatch_signal] {exc}")
        return 0.0, "fedwatch:error"
