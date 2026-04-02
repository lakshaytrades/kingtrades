"""
news_filter.py — NSE Momentum Groww AI Bot
News/Sentiment Filter — Economic Calendar + NewsAPI

Skips signals 30 minutes before/after:
- RBI policy decisions
- GDP / CPI releases
- Nifty50 component earnings
- Any high-impact market events

Sources: NewsAPI, economic calendar RSS feeds
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

import requests
import feedparser

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# High-impact keywords that trigger blackout
HIGH_IMPACT_KEYWORDS = [
    "rbi", "monetary policy", "repo rate", "cpi", "gdp", "inflation",
    "election", "union budget", "sebi", "circuit breaker", "market halt",
    "fed rate", "federal reserve", "us jobs", "nonfarm", "quantitative",
    "credit policy", "msci", "ftse rebalance", "index rebalance",
    "earnings results", "quarterly results", "q1 results", "q2 results",
    "q3 results", "q4 results", "board meeting dividend",
]

# Economic calendar RSS feeds (free sources)
CALENDAR_FEEDS = [
    "https://www.goodreturns.in/rss/news.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
]


class NewsFilter:
    """
    Filters out trading signals near high-impact news events.
    Uses NewsAPI for real-time sentiment and RSS for calendar.
    """

    def __init__(self, news_api_key: str = "", blackout_minutes: int = 30):
        self.api_key = news_api_key
        self.blackout_minutes = blackout_minutes
        self._event_cache: List[Dict] = []
        self._cache_refreshed: Optional[datetime] = None
        self._sentiment_cache: Dict[str, Dict] = {}
        self._last_refresh_ist: Optional[datetime] = None

    def is_safe_to_trade(self, symbol: str = "") -> bool:
        """
        Check if it's safe to trade right now.
        Returns False if within blackout period of any high-impact event.
        """
        now = get_current_ist_time()

        # Refresh events every 30 minutes
        if (self._last_refresh_ist is None or
                (now - self._last_refresh_ist).total_seconds() > 1800):
            self._refresh_events()

        # Check if any event is within blackout window
        for event in self._event_cache:
            event_time = event.get("time")
            if not event_time:
                continue
            time_diff = abs((now - event_time).total_seconds() / 60)
            if time_diff <= self.blackout_minutes:
                logger.warning(
                    f"[{format_ist_timestamp()}] 🚫 NEWS BLACKOUT: "
                    f"'{event['title'][:60]}' in {time_diff:.0f} min window"
                )
                return False

        # Check symbol-specific sentiment
        if symbol:
            sentiment = self.get_symbol_sentiment(symbol)
            if sentiment.get("strong_negative"):
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: strong negative news — caution"
                )
                # Don't block but warn (return True but log)

        return True

    def _refresh_events(self):
        """Refresh event cache from RSS feeds and NewsAPI."""
        self._event_cache = []
        self._last_refresh_ist = get_current_ist_time()

        # 1. Parse RSS economic calendars
        for feed_url in CALENDAR_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "").lower()
                    if any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
                        # Try to parse published time
                        pub = entry.get("published_parsed")
                        if pub:
                            from time import mktime
                            dt = datetime.fromtimestamp(mktime(pub), tz=IST)
                        else:
                            dt = get_current_ist_time()
                        self._event_cache.append({
                            "title": entry.get("title", ""),
                            "time": dt,
                            "source": "RSS",
                        })
            except Exception as e:
                logger.debug(f"RSS feed error ({feed_url}): {e}")

        # 2. NewsAPI for recent high-impact financial news
        if self.api_key:
            try:
                url = "https://newsapi.org/v2/everything"
                params = {
                    "q": "RBI OR budget OR GDP OR CPI OR NSE OR Sensex",
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "apiKey": self.api_key,
                }
                resp = requests.get(url, params=params, timeout=5)
                if resp.status_code == 200:
                    articles = resp.json().get("articles", [])
                    for art in articles:
                        title = (art.get("title") or "").lower()
                        if any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
                            pub_str = art.get("publishedAt", "")
                            try:
                                pub_dt = datetime.fromisoformat(
                                    pub_str.replace("Z", "+00:00")
                                ).astimezone(IST)
                            except Exception:
                                pub_dt = get_current_ist_time()
                            self._event_cache.append({
                                "title": art.get("title", ""),
                                "time": pub_dt,
                                "source": "NewsAPI",
                            })
            except Exception as e:
                logger.debug(f"NewsAPI error: {e}")

        if self._event_cache:
            logger.info(
                f"[{format_ist_timestamp()}] News filter: "
                f"{len(self._event_cache)} high-impact events loaded"
            )

    def get_symbol_sentiment(self, symbol: str) -> Dict:
        """
        Get basic sentiment score for a symbol from recent news.
        Returns: {"score": float, "positive": bool, "strong_negative": bool}
        """
        if not self.api_key:
            return {"score": 0, "positive": False, "strong_negative": False}

        # Check cache (5 minute TTL)
        if symbol in self._sentiment_cache:
            cached = self._sentiment_cache[symbol]
            age = (get_current_ist_time() - cached["cached_at"]).total_seconds()
            if age < 300:
                return cached

        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": symbol,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 10,
                "apiKey": self.api_key,
            }
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return {"score": 0, "positive": False, "strong_negative": False}

            articles = resp.json().get("articles", [])
            if not articles:
                return {"score": 0, "positive": False, "strong_negative": False}

            # Simple keyword-based sentiment
            positive_words = ["surge", "rally", "gain", "buy", "bullish", "upgrade",
                              "target raised", "beat", "outperform", "strong", "profit"]
            negative_words = ["fall", "drop", "loss", "sell", "bearish", "downgrade",
                              "target cut", "miss", "underperform", "weak", "fraud",
                              "penalty", "fine", "scam", "investigation"]

            pos_count = neg_count = 0
            for art in articles:
                text = (
                    (art.get("title") or "") + " " +
                    (art.get("description") or "")
                ).lower()
                pos_count += sum(1 for w in positive_words if w in text)
                neg_count += sum(1 for w in negative_words if w in text)

            score = (pos_count - neg_count) / max(len(articles), 1)
            result = {
                "score": round(score, 2),
                "positive": score > 0.3,
                "strong_negative": neg_count > pos_count * 2 and neg_count >= 3,
                "cached_at": get_current_ist_time(),
            }
            self._sentiment_cache[symbol] = result
            return result

        except Exception as e:
            logger.debug(f"Sentiment fetch error for {symbol}: {e}")
            return {"score": 0, "positive": False, "strong_negative": False}

    def get_nifty_sentiment_score(self) -> float:
        """Get overall market sentiment score (-1 to +1)."""
        try:
            if not self.api_key:
                return 0.0
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": "Nifty50 Sensex India stock market",
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 15,
                "apiKey": self.api_key,
            }
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return 0.0
            articles = resp.json().get("articles", [])
            positive_words = ["surge", "rally", "gain", "bullish", "up", "rise",
                              "breakout", "all-time high", "record"]
            negative_words = ["fall", "drop", "crash", "bearish", "down", "decline",
                              "selloff", "fear", "uncertainty"]
            pos = neg = 0
            for art in articles:
                text = ((art.get("title") or "") + " " +
                        (art.get("description") or "")).lower()
                pos += sum(1 for w in positive_words if w in text)
                neg += sum(1 for w in negative_words if w in text)
            total = pos + neg
            if total == 0:
                return 0.0
            return round((pos - neg) / total, 2)
        except Exception:
            return 0.0
