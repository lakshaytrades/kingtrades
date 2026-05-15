"""
news_filter.py — US Momentum Alpaca AI Bot
News/Sentiment Filter — Economic Calendar + Earnings Avoidance

Skips signals 30 minutes before/after:
- Fed/FOMC decisions, CPI, NFP, GDP releases
- Individual stock earnings dates (via yfinance)
- Any high-impact US market events

Sources: NewsAPI, yfinance earnings calendar
"""

import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

import requests

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# High-impact US market keywords that trigger blackout
HIGH_IMPACT_KEYWORDS = [
    "federal reserve", "fomc", "fed rate", "interest rate decision",
    "cpi", "consumer price index", "inflation data",
    "nonfarm payroll", "nfp", "jobs report", "unemployment",
    "gdp", "gross domestic product",
    "pce", "personal consumption",
    "earnings beat", "earnings miss", "quarterly earnings", "eps results",
    "circuit breaker", "market halt", "trading halt",
    "sec investigation", "fraud", "accounting restatement",
    "index rebalance", "msci rebalance",
    "us treasury", "debt ceiling", "government shutdown",
]

# US economic calendar RSS feeds
CALENDAR_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.investing.com/rss/news_25.rss",  # economic calendar
]

# Earnings blackout window (days before/after earnings)
EARNINGS_BUFFER_DAYS = 1


class NewsFilter:
    """
    Filters trading signals near high-impact US market events.
    Checks Fed calendar, earnings dates, and real-time news sentiment.
    """

    def __init__(self, news_api_key: str = "", blackout_minutes: int = 30):
        self.api_key = news_api_key
        self.blackout_minutes = blackout_minutes
        self._event_cache: List[Dict] = []
        self._earnings_cache: Dict[str, Optional[date]] = {}  # symbol → next earnings date
        self._sentiment_cache: Dict[str, Dict] = {}
        self._last_refresh: Optional[datetime] = None

    def is_safe_to_trade(self, symbol: str = "") -> bool:
        """
        Returns False if within blackout window of a high-impact event
        or within 1 day of the symbol's earnings date.
        """
        now = get_current_ist_time()

        # Refresh events every 30 minutes
        if (self._last_refresh is None or
                (now - self._last_refresh).total_seconds() > 1800):
            self._refresh_events()

        # Check macro event blackout
        for event in self._event_cache:
            event_time = event.get("time")
            if not event_time:
                continue
            time_diff = abs((now - event_time).total_seconds() / 60)
            if time_diff <= self.blackout_minutes:
                logger.warning(
                    f"[{format_ist_timestamp()}] NEWS BLACKOUT: "
                    f"'{event['title'][:60]}' within {time_diff:.0f} min window"
                )
                return False

        # Check earnings blackout for this symbol
        if symbol:
            if self._near_earnings(symbol):
                logger.warning(
                    f"[{format_ist_timestamp()}] {symbol}: near earnings date — skipping"
                )
                return False

            sentiment = self.get_symbol_sentiment(symbol)
            if sentiment.get("strong_negative"):
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: strong negative news — caution"
                )

        return True

    def _near_earnings(self, symbol: str) -> bool:
        """Check if symbol has earnings within EARNINGS_BUFFER_DAYS."""
        if symbol not in self._earnings_cache:
            self._earnings_cache[symbol] = self._fetch_next_earnings(symbol)
        earnings_date = self._earnings_cache.get(symbol)
        if earnings_date is None:
            return False
        today = get_current_ist_time().date()
        days_away = abs((earnings_date - today).days)
        return days_away <= EARNINGS_BUFFER_DAYS

    def _fetch_next_earnings(self, symbol: str) -> Optional[date]:
        """Fetch next earnings date via yfinance."""
        try:
            import yfinance as yf
            tk = yf.Ticker(symbol)
            cal = tk.calendar
            if cal is None:
                return None
            # calendar can be a dict or DataFrame
            if hasattr(cal, "to_dict"):
                cal = cal.to_dict()
            earnings_dt = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date") or cal.get("earnings_date")
                if ed:
                    if hasattr(ed, "__iter__") and not isinstance(ed, str):
                        ed = list(ed)[0]
                    if hasattr(ed, "date"):
                        earnings_dt = ed.date()
                    elif isinstance(ed, str):
                        earnings_dt = date.fromisoformat(ed[:10])
            return earnings_dt
        except Exception as e:
            logger.debug(f"Earnings fetch {symbol}: {e}")
            return None

    def _refresh_events(self):
        """Refresh US macro event cache from RSS and NewsAPI."""
        self._event_cache = []
        self._last_refresh = get_current_ist_time()

        # 1. RSS feeds
        try:
            import feedparser
            for feed_url in CALENDAR_FEEDS:
                try:
                    feed = feedparser.parse(feed_url)
                    for entry in feed.entries[:20]:
                        title = entry.get("title", "").lower()
                        if any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
                            pub = entry.get("published_parsed")
                            if pub:
                                from time import mktime
                                dt = datetime.fromtimestamp(mktime(pub), tz=ET)
                            else:
                                dt = get_current_ist_time()
                            self._event_cache.append({
                                "title": entry.get("title", ""),
                                "time": dt,
                                "source": "RSS",
                            })
                except Exception as e:
                    logger.debug(f"RSS feed error ({feed_url}): {e}")
        except ImportError as _e:
            logger.debug(f"[suppressed] feedparser not installed: {_e}")

        # 2. NewsAPI
        if self.api_key:
            try:
                url = "https://newsapi.org/v2/everything"
                params = {
                    "q": "Fed OR FOMC OR CPI OR NFP OR earnings OR GDP",
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "apiKey": self.api_key,
                }
                resp = requests.get(url, params=params, timeout=5)
                if resp.status_code == 200:
                    for art in resp.json().get("articles", []):
                        title = (art.get("title") or "").lower()
                        if any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
                            pub_str = art.get("publishedAt", "")
                            try:
                                pub_dt = datetime.fromisoformat(
                                    pub_str.replace("Z", "+00:00")
                                ).astimezone(ET)
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
        Sentiment score for a US stock from recent news.
        Returns: {"score": float, "positive": bool, "strong_negative": bool}
        """
        if not self.api_key:
            return {"score": 0, "positive": False, "strong_negative": False}

        if symbol in self._sentiment_cache:
            cached = self._sentiment_cache[symbol]
            if (get_current_ist_time() - cached["cached_at"]).total_seconds() < 300:
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

            positive_words = ["surge", "rally", "gain", "buy", "bullish", "upgrade",
                              "beat", "outperform", "strong", "profit", "record high"]
            negative_words = ["fall", "drop", "loss", "sell", "bearish", "downgrade",
                              "miss", "underperform", "weak", "fraud", "investigation",
                              "penalty", "fine", "layoff", "recall", "lawsuit"]

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

    def get_market_sentiment_score(self) -> float:
        """Get overall US market sentiment score (-1 to +1) from SPY/market news."""
        try:
            if not self.api_key:
                return 0.0
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": "S&P500 Nasdaq stock market Wall Street",
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 15,
                "apiKey": self.api_key,
            }
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return 0.0
            articles = resp.json().get("articles", [])
            positive_words = ["surge", "rally", "gain", "bullish", "rise",
                              "breakout", "all-time high", "record", "recovery"]
            negative_words = ["fall", "drop", "crash", "bearish", "decline",
                              "selloff", "fear", "uncertainty", "recession"]
            pos = neg = 0
            for art in articles:
                text = ((art.get("title") or "") + " " +
                        (art.get("description") or "")).lower()
                pos += sum(1 for w in positive_words if w in text)
                neg += sum(1 for w in negative_words if w in text)
            total = pos + neg
            return round((pos - neg) / total, 2) if total else 0.0
        except Exception:
            return 0.0

    # backward-compat alias
    def get_nifty_sentiment_score(self) -> float:
        return self.get_market_sentiment_score()

    def clear_earnings_cache(self):
        """Clear earnings cache — call at start of each new trading day."""
        self._earnings_cache.clear()
