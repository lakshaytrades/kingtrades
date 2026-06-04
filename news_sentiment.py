"""
news_sentiment.py — Stock-specific news sentiment scoring.
Uses NewsAPI key from .env. Scores title keywords bullish/bearish.
Cached 30 min per symbol. Fail-open returns 0.0.
"""
import logging, os, time
from typing import Tuple

logger = logging.getLogger(__name__)
_cache: dict = {}
_TTL = 1800.0

_BULLISH = {"surge","soars","beats","raises","upgrade","bullish","rally","record","strong","growth","profit","revenue","buyback","raised","guidance"}
_BEARISH = {"crash","plunge","miss","cut","downgrade","bearish","fraud","loss","weak","decline","lawsuit","recall","investigated","probe","fine"}


def get_news_sentiment(symbol: str) -> Tuple[float, str]:
    """Returns (score_delta, reason). Range -10 to +10. Fail-open 0.0."""
    try:
        now = time.time()
        cached = _cache.get(symbol)
        if cached and now - cached[1] < _TTL:
            return cached[0]

        api_key = os.getenv("NEWS_API_KEY", "")
        if not api_key:
            return (0.0, "no_api_key")

        import requests
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": symbol, "sortBy": "publishedAt",
                    "pageSize": 5, "language": "en", "apiKey": api_key},
            timeout=4
        )
        articles = r.json().get("articles", [])
        if not articles:
            return (0.0, "no_news")

        pos = neg = 0
        for a in articles[:5]:
            t = (a.get("title") or "").lower()
            pos += sum(1 for w in _BULLISH if w in t)
            neg += sum(1 for w in _BEARISH if w in t)

        if pos > neg:
            score = min(10.0, (pos - neg) * 3.0)
            reason = f"bullish_news(+{pos})"
        elif neg > pos:
            score = max(-10.0, -(neg - pos) * 3.0)
            reason = f"bearish_news(-{neg})"
        else:
            score, reason = 0.0, "neutral_news"

        result = (score, reason)
        _cache[symbol] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"news_sentiment fail-open {symbol}: {e}")
        return (0.0, "unavailable")
