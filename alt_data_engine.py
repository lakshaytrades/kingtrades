"""
alt_data_engine.py — Alternative Data Proxies (v28.0)

Paid alt data costs $10k-500k/year. Free proxies:

1. Google Trends (pytrends) — FREE
   Search volume for ticker = retail interest proxy.
   Acceleration in search interest precedes price moves (Da et al. 2011).

2. Wikipedia edit velocity — FREE (Wikimedia API)
   Abnormal Wikipedia edits for a company = breaking news catalyst.
   Moat & Preis (2013): Wikipedia views predict stock moves.

3. Reddit WallStreetBets — FREE (public JSON API, no auth)
   Mention spike = retail momentum. Mania phase = mean-reversion signal.
   Note: excessive Reddit hype on already-overbought stock = fade signal.

All cached 4h. All fail-open. No API keys required.
"""

import logging
import time as _time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

_trends_cache: Dict[str, Tuple[float, str, float]] = {}
_wiki_cache:   Dict[str, Tuple[float, str, float]] = {}
_reddit_cache: Dict[str, Tuple[float, str, float]] = {}

_TRENDS_TTL = 14400.0   # 4 hours
_WIKI_TTL   = 14400.0
_REDDIT_TTL = 3600.0    # 1 hour (more volatile)


# ── Google Trends ─────────────────────────────────────────────────────────────

def get_google_trends_score(
    symbol: str, company_name: str, direction: str
) -> Tuple[float, str]:
    """
    Fetch Google Trends search interest for the company.
    Accelerating interest → retail FOMO signal.
    """
    now = _time.monotonic()
    cached = _trends_cache.get(symbol)
    if cached and (now - cached[2]) < _TRENDS_TTL:
        raw, reason, _ = cached
        return _directional(raw, direction), reason

    score, reason = _fetch_trends(symbol, company_name)
    _trends_cache[symbol] = (score, reason, now)
    return _directional(score, direction), reason


def _fetch_trends(symbol: str, company_name: str) -> Tuple[float, str]:
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=360, timeout=(5, 10))
        kw_list = [symbol]
        pytrends.build_payload(kw_list, cat=0, timeframe="today 1-m", geo="US")
        data = pytrends.interest_over_time()

        if data is None or data.empty or symbol not in data.columns:
            return 0.0, "trends:no-data"

        values = data[symbol].values.astype(float)
        if len(values) < 4:
            return 0.0, "trends:too-short"

        # 4-week trend: compare last week vs 3 weeks ago
        recent  = float(values[-7:].mean()) if len(values) >= 7 else float(values[-1])
        earlier = float(values[-21:-7].mean()) if len(values) >= 21 else float(values[:max(1, len(values)-7)].mean())

        if earlier <= 0:
            return 0.0, "trends:baseline-zero"

        ratio = recent / earlier

        if ratio > 2.5:
            return 5.0, f"trends:viral({ratio:.1f}x surge)"
        elif ratio > 1.8:
            return 4.0, f"trends:rising({ratio:.1f}x)"
        elif ratio > 1.3:
            return 2.0, f"trends:accelerating({ratio:.1f}x)"
        elif ratio < 0.5:
            return -3.0, f"trends:fading({ratio:.1f}x)"
        return 0.0, f"trends:neutral({ratio:.1f}x)"

    except ImportError:
        return 0.0, "trends:pytrends-not-installed"
    except Exception as e:
        logger.debug(f"[alt_data] trends {symbol}: {e}")
        return 0.0, "trends:error"


# ── Wikipedia Edit Velocity ───────────────────────────────────────────────────

def get_wikipedia_score(symbol: str, direction: str) -> Tuple[float, str]:
    """Check Wikipedia page edit velocity for the company (last 48h vs baseline)."""
    now = _time.monotonic()
    cached = _wiki_cache.get(symbol)
    if cached and (now - cached[2]) < _WIKI_TTL:
        raw, reason, _ = cached
        return _directional(raw, direction), reason

    score, reason = _fetch_wikipedia(symbol)
    _wiki_cache[symbol] = (score, reason, now)
    return _directional(score, direction), reason


def _fetch_wikipedia(symbol: str) -> Tuple[float, str]:
    """Query Wikimedia API for page edit activity (free, no auth)."""
    try:
        import requests

        # Map symbol to company name for Wikipedia
        _COMPANY_MAP = {
            "AAPL": "Apple_Inc.", "MSFT": "Microsoft", "GOOGL": "Alphabet_Inc.",
            "AMZN": "Amazon_(company)", "NVDA": "Nvidia", "META": "Meta_Platforms",
            "TSLA": "Tesla,_Inc.", "AMD": "Advanced_Micro_Devices",
            "NFLX": "Netflix", "JPM": "JPMorgan_Chase", "GS": "Goldman_Sachs",
            "BAC": "Bank_of_America", "V": "Visa_Inc.", "MA": "Mastercard",
            "SPY": "SPDR_S%26P_500_ETF_Trust", "QQQ": "Invesco_QQQ_Trust",
        }
        page = _COMPANY_MAP.get(symbol, symbol)

        url = (
            f"https://en.wikipedia.org/w/api.php"
            f"?action=query&format=json&prop=revisions"
            f"&titles={page}&rvprop=timestamp&rvlimit=50"
            f"&rvstart=now&rvdir=older&rvend=now-48h"
        )
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return 0.0, "wiki:http-error"

        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        revisions = []
        for p in pages.values():
            revisions.extend(p.get("revisions", []))

        recent_edits = len(revisions)

        # Get baseline: same 48h window from 2 weeks ago
        url_base = (
            f"https://en.wikipedia.org/w/api.php"
            f"?action=query&format=json&prop=revisions"
            f"&titles={page}&rvprop=timestamp&rvlimit=50"
            f"&rvstart=now-14d&rvdir=older&rvend=now-16d"
        )
        resp_b = requests.get(url_base, timeout=5)
        base_data = resp_b.json() if resp_b.status_code == 200 else {}
        base_pages = base_data.get("query", {}).get("pages", {})
        base_revisions = []
        for p in base_pages.values():
            base_revisions.extend(p.get("revisions", []))
        baseline = max(len(base_revisions), 1)

        ratio = recent_edits / baseline

        if ratio > 5.0:
            return 6.0, f"wiki:viral-edits({recent_edits}edits,{ratio:.1f}x)"
        elif ratio > 3.0:
            return 4.0, f"wiki:high-activity({recent_edits}edits)"
        elif ratio > 2.0:
            return 2.0, f"wiki:elevated({recent_edits}edits)"
        elif ratio < 0.3:
            return -2.0, f"wiki:quiet({recent_edits}edits)"
        return 0.0, f"wiki:normal({recent_edits}edits)"

    except Exception as e:
        logger.debug(f"[alt_data] wikipedia {symbol}: {e}")
        return 0.0, "wiki:error"


# ── Reddit WallStreetBets ─────────────────────────────────────────────────────

def get_reddit_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Scan WallStreetBets for mention velocity.
    Free Reddit JSON API (no OAuth needed).
    """
    now = _time.monotonic()
    cached = _reddit_cache.get(symbol)
    if cached and (now - cached[2]) < _REDDIT_TTL:
        raw, reason, _ = cached
        return _directional(raw, direction), reason

    score, reason = _fetch_reddit(symbol)
    _reddit_cache[symbol] = (score, reason, now)
    return _directional(score, direction), reason


def _fetch_reddit(symbol: str) -> Tuple[float, str]:
    """Count WSB mentions in last 24h vs baseline."""
    try:
        import requests

        headers = {"User-Agent": "KingTrades/1.0 (research bot)"}

        # Search WSB for the ticker
        url = (
            f"https://www.reddit.com/r/wallstreetbets/search.json"
            f"?q={symbol}&restrict_sr=1&sort=new&limit=100&t=day"
        )
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return 0.0, "reddit:http-error"

        data = resp.json()
        posts_today = len(data.get("data", {}).get("children", []))

        # Weekly baseline
        url_w = (
            f"https://www.reddit.com/r/wallstreetbets/search.json"
            f"?q={symbol}&restrict_sr=1&sort=new&limit=100&t=week"
        )
        resp_w = requests.get(url_w, headers=headers, timeout=8)
        posts_week = 0
        if resp_w.status_code == 200:
            data_w = resp_w.json()
            posts_week = len(data_w.get("data", {}).get("children", []))

        daily_avg = max(posts_week / 7.0, 0.1)
        ratio = posts_today / daily_avg

        if ratio > 5.0:
            # Extreme mentions = retail mania → contrarian fade signal if overbought
            return -3.0, f"reddit:mania({posts_today}posts,{ratio:.1f}x) — fade risk"
        elif ratio > 3.0:
            return 4.0, f"reddit:trending({posts_today}posts,{ratio:.1f}x)"
        elif ratio > 2.0:
            return 2.0, f"reddit:rising({posts_today}posts)"
        elif posts_today == 0:
            return 0.0, "reddit:no-mentions"
        return 0.0, f"reddit:normal({posts_today}posts)"

    except Exception as e:
        logger.debug(f"[alt_data] reddit {symbol}: {e}")
        return 0.0, "reddit:error"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _directional(raw_score: float, direction: str) -> float:
    """Positive raw = LONG bullish. Flip for SHORT."""
    if direction == "SHORT":
        return -raw_score
    return raw_score
