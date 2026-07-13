"""
alpha_vantage_news.py — Alpha Vantage Real-Time News NLP Sentiment (L99)

Alpha Vantage NEWS_SENTIMENT endpoint (free, 25 req/day with free key):
  https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=AAPL

Provides per-article NLP sentiment scored per ticker with relevance weight.
Labels: Bullish / Somewhat-Bullish / Neutral / Somewhat-Bearish / Bearish
Relevance: 0.0–1.0 (how much article focuses on this specific ticker)

Speed advantage: articles indexed within minutes of publication.
This is the same NLP scoring used by institutional news desks.
"""
import logging
import os
import time as _time
import requests
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict = {}
_TTL = 900.0  # 15 min — conserve free quota (25/day)

_LABEL_SCORE = {
    "Bullish":          1.0,
    "Somewhat-Bullish": 0.5,
    "Neutral":          0.0,
    "Somewhat-Bearish": -0.5,
    "Bearish":          -1.0,
}

def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _TTL:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _fetch_av_sentiment(symbol: str) -> Optional[float]:
    """
    Fetch Alpha Vantage news sentiment for symbol.
    Returns composite score -1.0 (bearish) to +1.0 (bullish).
    """
    cached = _cache_get(f"av_{symbol}")
    if cached is not None:
        return cached

    api_key = os.getenv("ALPHA_VANTAGE_KEY", "")
    if not api_key:
        return None

    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT"
            f"&tickers={symbol}"
            f"&limit=20"
            f"&sort=LATEST"
            f"&apikey={api_key}"
        )
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return None

        data = r.json()
        if "Information" in data:  # rate limit message
            return None
        feed = data.get("feed", [])
        if not feed:
            return None

        total_score  = 0.0
        total_weight = 0.0
        for article in feed[:15]:
            for td in article.get("ticker_sentiment", []):
                if td.get("ticker", "").upper() != symbol.upper():
                    continue
                relevance = float(td.get("relevance_score", 0.0))
                if relevance < 0.1:
                    continue
                raw     = float(td.get("ticker_sentiment_score", 0.0))
                label   = td.get("ticker_sentiment_label", "Neutral")
                blended = (_LABEL_SCORE.get(label, 0.0) * 0.6 + raw * 0.4) * relevance
                total_score  += blended
                total_weight += relevance

        if total_weight < 0.05:
            return None
        composite = total_score / total_weight
        _cache_set(f"av_{symbol}", composite)
        return composite
    except Exception as exc:
        logger.debug(f"[av_news] {symbol}: {exc}")
        return None


def get_av_news_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Alpha Vantage real-time news NLP score.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        composite = _fetch_av_sentiment(symbol)
        if composite is None:
            return 0.0, "av_news:no-data"

        if composite > 0.65:
            d = 8.0 if direction == "LONG" else -6.0
            return d, f"AV_NEWS[BULLISH={composite:.2f}]"
        elif composite > 0.30:
            d = 4.0 if direction == "LONG" else -3.0
            return d, f"AV_NEWS[somewhat_bull={composite:.2f}]"
        elif composite < -0.65:
            d = -7.0 if direction == "LONG" else 6.0
            return d, f"AV_NEWS[BEARISH={composite:.2f}]"
        elif composite < -0.30:
            d = -3.0 if direction == "LONG" else 4.0
            return d, f"AV_NEWS[somewhat_bear={composite:.2f}]"
        return 0.0, f"av_news:neutral({composite:.2f})"
    except Exception as exc:
        logger.debug(f"[av_news_score] {exc}")
        return 0.0, "av_news:error"
