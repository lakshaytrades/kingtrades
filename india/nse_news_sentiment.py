"""
nse_news_sentiment.py — NSE News NLP Sentiment (God Mode)
RSS feeds: MoneyControl + Economic Times + BusinessLine
Real-time stock-specific sentiment. Shock event detector.
Score: -15 to +12. Cache 20 min. Fail-open.
"""
import logging
import time as _time
from typing import List, Dict, Tuple

logger = logging.getLogger("nse_news")
_headlines_cache: dict = {"data": [], "ts": 0.0}
_sentiment_cache: dict = {}
_TTL_HEADLINES = 1200.0  # 20 min
_TTL_SYMBOL = 1200.0

_RSS = [
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://economictimes.indiatimes.com/markets/stocks/rss.cms",
]

_STRONG_POS = [
    "beat estimates", "record profit", "strong results", "upgrade", "buyback",
    "dividend declared", "deal won", "promoter buying", "target raised",
]
_MILD_POS = [
    "profit up", "revenue growth", "order win", "partnership", "positive outlook",
]
_STRONG_NEG = [
    "miss estimates", "profit warning", "downgrade", "fraud", "investigation",
    "default", "loss widens", "pledging", "ED raid", "SEBI notice", "fire", "accident",
]
_MILD_NEG = [
    "below expectations", "slowdown", "margins pressured", "competition", "delay",
]
_SHOCK = [
    "rbi rate", "rbi policy", "budget", "election result", "nifty circuit", "trading halt",
]

_shock_cache: dict = {"result": None, "ts": 0.0}
_SHOCK_TTL = 900.0  # 15 min


def _fetch_headlines() -> List[Dict]:
    """Fetch RSS headlines. Try feedparser first, fallback to requests + xml.etree. Cache 20 min."""
    now = _time.monotonic()
    if _headlines_cache["ts"] > now - _TTL_HEADLINES and _headlines_cache["data"]:
        return _headlines_cache["data"]

    items: List[Dict] = []

    # Attempt feedparser first
    try:
        import feedparser
        for url in _RSS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    items.append({"title": title, "summary": summary})
            except Exception as e:
                logger.debug(f"feedparser {url}: {e}")
    except ImportError:
        # Fallback: requests + xml.etree.ElementTree
        try:
            import requests
            import xml.etree.ElementTree as ET
            for url in _RSS:
                try:
                    resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                    root = ET.fromstring(resp.content)
                    channel = root.find("channel")
                    if channel is None:
                        channel = root
                    for item in list(channel.findall("item"))[:20]:
                        title = (item.findtext("title") or "").strip()
                        summary = (item.findtext("description") or "").strip()
                        items.append({"title": title, "summary": summary})
                except Exception as e:
                    logger.debug(f"rss fallback {url}: {e}")
        except Exception as e:
            logger.debug(f"rss fetch failed entirely: {e}")

    _headlines_cache["data"] = items
    _headlines_cache["ts"] = now
    return items


def get_news_sentiment_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Filter headlines for symbol name, score against keyword lists.
    Cache per-symbol 20 min. Clamp to [-15, +12].
    For SHORT direction: negative news = boost, positive = penalty.
    """
    now = _time.monotonic()
    cache_key = f"{symbol}:{direction}"
    if cache_key in _sentiment_cache:
        entry = _sentiment_cache[cache_key]
        if entry["ts"] > now - _TTL_SYMBOL:
            return entry["score"], entry["reason"]

    try:
        headlines = _fetch_headlines()
        sym_lower = symbol.lower()

        relevant = [
            h for h in headlines
            if sym_lower in h.get("title", "").lower()
            or sym_lower in h.get("summary", "").lower()
        ]

        if not relevant:
            _sentiment_cache[cache_key] = {"score": 0.0, "reason": "", "ts": now}
            return 0.0, ""

        raw_score = 0.0
        matched_reason = ""

        for h in relevant:
            text = (h.get("title", "") + " " + h.get("summary", "")).lower()

            for kw in _STRONG_POS:
                if kw.lower() in text:
                    raw_score += 8.0
                    matched_reason = matched_reason or f"strong_pos:{kw}"
                    break

            for kw in _MILD_POS:
                if kw.lower() in text:
                    raw_score += 4.0
                    matched_reason = matched_reason or f"mild_pos:{kw}"
                    break

            for kw in _STRONG_NEG:
                if kw.lower() in text:
                    raw_score -= 10.0
                    matched_reason = matched_reason or f"strong_neg:{kw}"
                    break

            for kw in _MILD_NEG:
                if kw.lower() in text:
                    raw_score -= 4.0
                    matched_reason = matched_reason or f"mild_neg:{kw}"
                    break

        # For SHORT: flip sign (negative news boosts SHORT, positive penalises)
        if direction == "SHORT":
            raw_score = -raw_score

        final_score = max(-15.0, min(12.0, raw_score))
        reason = f"NEWS:{matched_reason}" if matched_reason else ""

        _sentiment_cache[cache_key] = {"score": final_score, "reason": reason, "ts": now}
        return final_score, reason

    except Exception as e:
        logger.debug(f"get_news_sentiment_score: {e}")
        return 0.0, ""


def is_shock_event_active() -> Tuple[bool, str]:
    """Check headlines for shock macro keywords. Return (True, keyword) if found. Cache 15 min."""
    now = _time.monotonic()
    if _shock_cache["result"] is not None and _shock_cache["ts"] > now - _SHOCK_TTL:
        result = _shock_cache["result"]
        return result[0], result[1]

    try:
        headlines = _fetch_headlines()
        for h in headlines:
            text = (h.get("title", "") + " " + h.get("summary", "")).lower()
            for kw in _SHOCK:
                if kw.lower() in text:
                    _shock_cache["result"] = (True, kw)
                    _shock_cache["ts"] = now
                    return True, kw
    except Exception as e:
        logger.debug(f"is_shock_event_active: {e}")

    _shock_cache["result"] = (False, "")
    _shock_cache["ts"] = now
    return False, ""
