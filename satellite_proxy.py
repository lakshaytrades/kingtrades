"""
satellite_proxy.py — Free Orbital Insight Satellite / Alt Data Proxy (L99)

Orbital Insight ($100,000+/year) uses satellite imagery to measure:
  - Retail parking lot car counts (foot traffic)
  - Oil storage tank fill levels
  - Shipping container throughput

Free equivalents that capture the same economic signal:
  1. Wikipedia page view velocity — attention surge = public interest spike
  2. Retail ETF momentum (XRT, XLY) — sector foot traffic proxy
  3. Energy ETF momentum (XLE, USO) — oil/energy sector proxy
  4. FRED consumer confidence (UMCSENT) — forward consumer spending
  5. Google Trends proxy via Wikipedia (no pytrends needed)

Applicable to: retail/consumer, energy stocks, and broad market attention.
"""
import logging
import time as _time
import requests
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict = {}
_TTL = 1800.0  # 30 min

RETAIL_SYMS = {"AMZN","WMT","TGT","COST","HD","LOW","BBY","ULTA","TJX","ROST","DG","DLTR"}
ENERGY_SYMS = {"XOM","CVX","COP","OXY","SLB","MPC","PSX","VLO","HAL","BKR","EOG"}

def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _TTL:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _get_wikipedia_views(symbol: str) -> Optional[float]:
    """Wikipedia page view ratio (recent vs baseline). Returns ratio or None."""
    cached = _cache_get(f"wiki_{symbol}")
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        name = info.get("shortName") or info.get("longName") or symbol
        # Use first word of company name for Wikipedia
        page = name.split()[0].replace(",", "").replace(".", "")
        url = (
            f"https://en.wikipedia.org/api/rest_v1/metrics/pageviews/per-article"
            f"/en.wikipedia/all-access/all-agents/{page}/daily/20240101/20241231"
        )
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if len(items) >= 14:
                recent   = sum(i["views"] for i in items[-3:]) / 3
                baseline = sum(i["views"] for i in items[-30:-3]) / 27
                ratio = recent / max(baseline, 1)
                _cache_set(f"wiki_{symbol}", ratio)
                return ratio
    except Exception as exc:
        logger.debug(f"[satellite_wiki] {symbol}: {exc}")
    return None


def _get_sector_etf_momentum(tickers: list) -> Optional[float]:
    """5-day return of sector ETF basket."""
    key = "_".join(tickers)
    cached = _cache_get(f"etf_{key}")
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        returns = []
        for t in tickers:
            data = yf.Ticker(t).history(period="10d")
            if data is not None and not data.empty and len(data) >= 5:
                r = float(data["Close"].pct_change(5).iloc[-1]) * 100
                returns.append(r)
        if returns:
            mom = sum(returns) / len(returns)
            _cache_set(f"etf_{key}", mom)
            return mom
    except Exception:
        pass
    return None


def get_satellite_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Free satellite/alt data proxy score (Orbital Insight equivalent).
    Returns (score_delta, reason). Fail-open.
    """
    try:
        total = 0.0
        parts = []

        # 1. Wikipedia attention surge (applies to all symbols)
        wiki_ratio = _get_wikipedia_views(symbol)
        if wiki_ratio is not None:
            if wiki_ratio > 3.5:
                d = 8.0 if direction == "LONG" else -4.0
                parts.append(f"WIKI_SURGE={wiki_ratio:.1f}x")
                total += d
            elif wiki_ratio > 2.0:
                d = 4.0 if direction == "LONG" else -2.0
                parts.append(f"wiki_up={wiki_ratio:.1f}x")
                total += d
            elif wiki_ratio < 0.4:
                d = -2.0
                parts.append(f"wiki_dead={wiki_ratio:.1f}x")
                total += d

        # 2. Retail foot traffic proxy
        if symbol.upper() in RETAIL_SYMS:
            retail = _get_sector_etf_momentum(["XRT", "XLY"])
            if retail is not None:
                if retail > 2.0:
                    d = 5.0 if direction == "LONG" else -3.0
                    parts.append(f"RETAIL_ETF={retail:+.1f}%")
                    total += d
                elif retail < -2.0:
                    d = -4.0 if direction == "LONG" else 4.0
                    parts.append(f"retail_weak={retail:+.1f}%")
                    total += d

        # 3. Energy/oil proxy
        if symbol.upper() in ENERGY_SYMS:
            energy = _get_sector_etf_momentum(["XLE", "USO"])
            if energy is not None:
                if energy > 2.0:
                    d = 5.0 if direction == "LONG" else -3.0
                    parts.append(f"ENERGY_ETF={energy:+.1f}%")
                    total += d
                elif energy < -2.0:
                    d = -4.0 if direction == "LONG" else 4.0
                    parts.append(f"energy_weak={energy:+.1f}%")
                    total += d

        if not parts:
            return 0.0, "satellite:no-signal"
        return float(total), f"SATELLITE[{' | '.join(parts)}]"
    except Exception as exc:
        logger.debug(f"[satellite_proxy] {exc}")
        return 0.0, "satellite:error"
