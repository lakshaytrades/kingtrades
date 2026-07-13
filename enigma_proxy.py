"""
enigma_proxy.py — Free Enigma Credit Card / Consumer Flow Proxy (L99)

Enigma ($50,000+/year) provides anonymized credit card transaction data
showing real consumer spending patterns by merchant category.

Free equivalents using FRED (same underlying economic reality):
  PSAVERT  — Personal Savings Rate (low = high spending, retail bullish)
  DRCCLACBS — Credit Card Delinquency Rate (rising = consumer stress)
  UMCSENT  — Consumer Sentiment Index (forward spending intent)
  TOTALSL  — Total Consumer Credit (credit expansion = spending)

Composite = weighted consumer health score → direction-adjusted signal.
"""
import logging
import time as _time
import requests
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict = {}
_TTL = 86400.0  # 24h — FRED data is monthly/quarterly

def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _TTL:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _fred(series: str) -> Optional[float]:
    """Fetch latest FRED value. No key needed."""
    cached = _cache_get(f"fred_{series}")
    if cached is not None:
        return cached
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            for line in reversed(r.text.strip().splitlines()):
                v = line.split(",")[-1].strip()
                if v and v != ".":
                    try:
                        val = float(v)
                        _cache_set(f"fred_{series}", val)
                        return val
                    except ValueError:
                        continue
    except Exception as exc:
        logger.debug(f"[enigma_fred] {series}: {exc}")
    return None


def _get_consumer_score() -> Optional[float]:
    """
    Composite consumer health: -1.0 (distressed) to +1.0 (healthy).
    """
    cached = _cache_get("consumer")
    if cached is not None:
        return cached

    scores = []

    # Savings rate: 4-7% = healthy. <3% = over-leveraged. >12% = hoarding.
    sv = _fred("PSAVERT")
    if sv is not None:
        if 4.0 < sv < 8.0:
            scores.append(0.5)
        elif sv < 2.5:
            scores.append(-0.4)
        elif sv > 12.0:
            scores.append(-0.2)

    # Credit card delinquency: <2.5% = healthy. >4.5% = stress.
    delinq = _fred("DRCCLACBS")
    if delinq is not None:
        if delinq < 2.5:
            scores.append(0.4)
        elif delinq > 4.5:
            scores.append(-0.6)
        elif delinq > 3.5:
            scores.append(-0.3)

    # Consumer sentiment: >90 = optimistic. <65 = pessimistic.
    sentiment = _fred("UMCSENT")
    if sentiment is not None:
        if sentiment > 90:
            scores.append(0.5)
        elif sentiment > 75:
            scores.append(0.2)
        elif sentiment < 65:
            scores.append(-0.5)
        elif sentiment < 75:
            scores.append(-0.2)

    if not scores:
        return None

    composite = sum(scores) / len(scores)
    _cache_set("consumer", composite)
    return composite


def get_enigma_score(direction: str) -> Tuple[float, str]:
    """
    Consumer spending / credit card flow proxy (Enigma equivalent).
    Returns (score_delta, reason). Fail-open.
    """
    try:
        composite = _get_consumer_score()
        if composite is None:
            return 0.0, "enigma:no-data"

        if composite > 0.40:
            d = 5.0 if direction == "LONG" else -3.0
            return d, f"ENIGMA[CONSUMER_HEALTHY={composite:+.2f}]"
        elif composite > 0.15:
            d = 2.0 if direction == "LONG" else -1.0
            return d, f"ENIGMA[consumer_ok={composite:+.2f}]"
        elif composite < -0.35:
            d = -5.0 if direction == "LONG" else 4.0
            return d, f"ENIGMA[CONSUMER_STRESS={composite:+.2f}]"
        elif composite < -0.10:
            d = -2.0 if direction == "LONG" else 2.0
            return d, f"ENIGMA[consumer_weak={composite:+.2f}]"

        return 0.0, f"enigma:neutral({composite:+.2f})"
    except Exception as exc:
        logger.debug(f"[enigma_proxy] {exc}")
        return 0.0, "enigma:error"
