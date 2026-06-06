"""
crypto_cross_asset.py — BTC/ETH Cross-Asset Correlation Signal (L99)

Crypto-correlated US equity signals:
  Direct:   COIN(0.85), MSTR(0.82), RIOT(0.80), MARA(0.78), CLSK(0.75)
  Moderate: NVDA(0.45), TSLA(0.42), SQ(0.55), HOOD(0.60), META(0.35)
  Low:      AAPL(0.28), MSFT(0.30), GOOGL(0.30)

Data: CoinGecko free API — 50 req/min, no key required.
BTC 24h change drives directional signal, scaled by per-symbol correlation.
"""
import logging
import time as _time
import requests
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict = {}
_TTL = 300.0  # 5 min

# Per-symbol BTC correlation coefficients (empirical, 2022-2025)
CRYPTO_CORR: Dict[str, float] = {
    "COIN": 0.85, "MSTR": 0.82, "RIOT": 0.80, "MARA": 0.78,
    "CLSK": 0.75, "HUT":  0.72, "BTBT": 0.70, "CIFR": 0.68,
    "HOOD": 0.60, "SQ":   0.55, "PYPL": 0.48,
    "NVDA": 0.45, "TSLA": 0.42, "AMD":  0.38,
    "META": 0.35, "MSFT": 0.30, "GOOGL":0.30, "AAPL": 0.28,
}

def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _TTL:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _fetch_crypto_data() -> Optional[Dict]:
    """Fetch BTC + ETH 24h price changes from CoinGecko (free, no key)."""
    cached = _cache_get("crypto")
    if cached is not None:
        return cached
    try:
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum"
            "&vs_currencies=usd"
            "&include_24hr_change=true"
        )
        r = requests.get(url, timeout=6,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            result = {
                "btc": float(data.get("bitcoin", {}).get("usd_24h_change") or 0.0),
                "eth": float(data.get("ethereum", {}).get("usd_24h_change") or 0.0),
            }
            _cache_set("crypto", result)
            return result
    except Exception as exc:
        logger.debug(f"[crypto_cross] fetch: {exc}")
    return None


def get_crypto_cross_asset_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    BTC/ETH cross-asset momentum signal scaled by per-symbol correlation.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        corr = CRYPTO_CORR.get(symbol.upper())
        if corr is None or corr < 0.25:
            return 0.0, "crypto:low-corr"

        data = _fetch_crypto_data()
        if data is None:
            return 0.0, "crypto:no-data"

        # Weighted composite: BTC dominates market direction
        composite = data["btc"] * 0.70 + data["eth"] * 0.30

        raw_delta = 0.0
        label = ""
        if composite > 4.0:
            raw_delta = 10.0
            label = f"CRYPTO_BULL({composite:+.1f}%)"
        elif composite > 2.0:
            raw_delta = 6.0
            label = f"CRYPTO_UP({composite:+.1f}%)"
        elif composite > 0.8:
            raw_delta = 3.0
            label = f"crypto_pos({composite:+.1f}%)"
        elif composite < -4.0:
            raw_delta = -9.0
            label = f"CRYPTO_CRASH({composite:+.1f}%)"
        elif composite < -2.0:
            raw_delta = -5.0
            label = f"CRYPTO_DOWN({composite:+.1f}%)"
        elif composite < -0.8:
            raw_delta = -2.0
            label = f"crypto_neg({composite:+.1f}%)"
        else:
            return 0.0, f"crypto:flat({composite:+.1f}%)"

        # SHORT direction: invert (crypto up = bad for shorts)
        if direction == "SHORT":
            raw_delta = -raw_delta * 0.8

        scaled = raw_delta * corr
        return float(scaled), f"CRYPTO[{symbol}_ρ={corr:.2f} {label}]"
    except Exception as exc:
        logger.debug(f"[crypto_cross_asset] {exc}")
        return 0.0, "crypto:error"
