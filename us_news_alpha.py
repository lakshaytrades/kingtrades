"""
us_news_alpha.py — US Stock News NLP Alpha (God Mode)
Sources: SEC EDGAR RSS (official filings), Yahoo Finance news,
         Reddit WallStreetBets mention surge detection.

3 signals:
  1. SEC 8-K material events (acquisition, guidance change, etc.)
  2. News sentiment from Yahoo Finance headlines
  3. WSB mention spike (contrarian: very high = reversal risk)

Score: -12 to +10. Cache 20 min. Fail-open.
"""
import logging
import time as _time
import requests
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

logger = logging.getLogger("us_news_alpha")
_cache: Dict[str, Tuple] = {}
_TTL = 1200.0   # 20-minute cache


# ── Keyword lists ──────────────────────────────────────────────────────────────
STRONG_POS = ["beat", "record", "upgrade", "raised target", "acquisition",
              "buyback", "partnership", "dividend increase", "guidance raised"]
STRONG_NEG = ["miss", "downgrade", "investigation", "fraud", "recall",
              "warning", "layoff", "bankruptcy", "going concern", "subpoena"]
MILD_POS   = ["growth", "expansion", "positive", "raised guidance",
              "outperform", "strong demand", "beat estimates"]
MILD_NEG   = ["below", "concern", "slowing", "pressure", "headwinds",
              "cut guidance", "uncertainty"]


def _fetch_yahoo_headlines(symbol: str) -> List[str]:
    """
    Use yfinance Ticker(symbol).news → list of dicts with 'title', 'summary'.
    Return list of title+summary strings. Cache 20 min.
    """
    cache_key = f"yahoo_news:{symbol}"
    now = _time.time()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[1] < _TTL:
        return cached[0]

    try:
        import yfinance as yf
        news_items = yf.Ticker(symbol).news or []
        headlines = []
        for item in news_items:
            title   = item.get("title",   "") or ""
            summary = item.get("summary", "") or ""
            combined = f"{title} {summary}".strip()
            if combined:
                headlines.append(combined.lower())
        _cache[cache_key] = (headlines, now)
        return headlines
    except Exception as e:
        logger.debug(f"_fetch_yahoo_headlines({symbol}): {e}")
        _cache[cache_key] = ([], now)
        return []


def _score_headlines(headlines: List[str], direction: str) -> Tuple[float, str]:
    """
    Keyword scoring against strong/mild positive/negative lists.
    Score aligned with direction. Clamp [-12, +10].
    """
    if not headlines:
        return 0.0, ""

    raw_score = 0.0
    hits: List[str] = []

    for text in headlines[:10]:  # limit to 10 most recent
        for kw in STRONG_POS:
            if kw in text:
                raw_score += 3.0
                hits.append(f"+{kw}")
                break
        for kw in STRONG_NEG:
            if kw in text:
                raw_score -= 4.0
                hits.append(f"-{kw}")
                break
        for kw in MILD_POS:
            if kw in text:
                raw_score += 1.5
                hits.append(f"+{kw}")
                break
        for kw in MILD_NEG:
            if kw in text:
                raw_score -= 1.5
                hits.append(f"-{kw}")
                break

    # Align with direction
    if direction == "SHORT":
        raw_score = -raw_score  # positive news is bad for shorts

    score = max(-12.0, min(10.0, raw_score))
    reason = f"news_kw:[{','.join(hits[:3])}]" if hits else "news_neutral"
    return float(score), reason


def get_sec_8k_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Fetch SEC EDGAR RSS for 8-K filings.
    Parse recent 8-K titles for material events.
    "Agreement and Plan of Merger" = M&A (+12 LONG).
    "Going Concern" = very bearish.
    Fail-open (0.0, "").
    """
    cache_key = f"sec_8k:{symbol}"
    now = _time.time()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[1] < _TTL:
        return cached[0], cached[1] if len(cached) > 2 else ""

    # Re-structure: cache stores (score, reason, timestamp)
    cached_v2 = _cache.get(cache_key + "_v2")
    if cached_v2 is not None and now - cached_v2[2] < _TTL:
        return cached_v2[0], cached_v2[1]

    try:
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&company={symbol}&type=8-K"
            f"&dateb=&owner=include&count=5&search_text=&output=atom"
        )
        headers = {"User-Agent": "kingtrades/1.0 research@example.com"}
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            _cache[cache_key + "_v2"] = (0.0, "", now)
            return 0.0, ""

        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)

        score  = 0.0
        reason = ""

        for entry in entries[:5]:
            title_el = entry.find("atom:title", ns)
            if title_el is None:
                continue
            title_text = (title_el.text or "").lower()

            # M&A events
            if "agreement and plan of merger" in title_text or "merger agreement" in title_text:
                if direction == "LONG":
                    score  = 12.0
                    reason = "SEC_8K:merger_agreement"
                else:
                    score  = -6.0
                    reason = "SEC_8K:merger_against_short"
                break

            # Going concern / bankruptcy
            if "going concern" in title_text or "chapter 11" in title_text:
                if direction == "SHORT":
                    score  = 10.0
                    reason = "SEC_8K:going_concern"
                else:
                    score  = -12.0
                    reason = "SEC_8K:going_concern_long_risk"
                break

            # Guidance raised
            if "guidance" in title_text and ("raised" in title_text or "increase" in title_text):
                if direction == "LONG":
                    score  = 6.0
                    reason = "SEC_8K:guidance_raised"
                else:
                    score  = -4.0
                    reason = "SEC_8K:guidance_raised_short_risk"
                break

            # Investigation / SEC action
            if "investigation" in title_text or "sec order" in title_text or "subpoena" in title_text:
                if direction == "SHORT":
                    score  = 8.0
                    reason = "SEC_8K:investigation"
                else:
                    score  = -10.0
                    reason = "SEC_8K:investigation_long_risk"
                break

            # Share buyback
            if "repurchase" in title_text or "buyback" in title_text:
                if direction == "LONG":
                    score  = 5.0
                    reason = "SEC_8K:buyback"
                else:
                    score  = -3.0
                    reason = "SEC_8K:buyback_short_risk"
                break

        _cache[cache_key + "_v2"] = (score, reason, now)
        return float(score), reason

    except Exception as e:
        logger.debug(f"get_sec_8k_score({symbol}): {e}")
        _cache[cache_key + "_v2"] = (0.0, "", now)
        return 0.0, ""


def get_news_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Combine: Yahoo news headlines score (weight 0.6) + SEC 8-K score (weight 0.4).
    Fail-open (0.0, "").
    """
    try:
        headlines = _fetch_yahoo_headlines(symbol)
        yahoo_score, yahoo_reason = _score_headlines(headlines, direction)
        sec_score,   sec_reason   = get_sec_8k_score(symbol, direction)

        combined = yahoo_score * 0.6 + sec_score * 0.4
        combined = max(-12.0, min(10.0, combined))

        parts = []
        if yahoo_reason:
            parts.append(f"yahoo:{yahoo_reason}")
        if sec_reason:
            parts.append(f"sec:{sec_reason}")
        reason = " | ".join(parts) if parts else "news_neutral"

        return float(combined), reason
    except Exception as e:
        logger.debug(f"get_news_score({symbol}): {e}")
        return 0.0, ""


def get_wsb_mention_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Try Reddit API for r/wallstreetbets mentions in last 24h.
    Very high mentions (>20) = contrarian signal (reversal risk).
    Low but non-zero = momentum confirmation.
    Fail-open (0.0, "").
    """
    cache_key = f"wsb:{symbol}"
    now = _time.time()
    cached = _cache.get(cache_key + "_v2")
    if cached is not None and now - cached[2] < _TTL:
        return cached[0], cached[1]

    try:
        url = (
            f"https://www.reddit.com/r/wallstreetbets/search.json"
            f"?q={symbol}&sort=new&limit=10&t=day"
        )
        headers = {"User-Agent": "kingtrades/1.0 research@example.com"}
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            _cache[cache_key + "_v2"] = (0.0, "", now)
            return 0.0, ""

        data = resp.json()
        posts = data.get("data", {}).get("children", [])

        # Count total mentions (title + body) containing the ticker
        ticker_upper = symbol.upper()
        mention_count = 0
        for post in posts:
            post_data = post.get("data", {})
            text = (post_data.get("title", "") + " " + post_data.get("selftext", "")).upper()
            # Count occurrences of exact ticker
            mention_count += text.count(f" {ticker_upper} ") + text.count(f"${ticker_upper}")

        if mention_count > 20:
            # Very high WSB buzz = contrarian — retail FOMO means likely near peak
            if direction == "LONG":
                score  = -3.0
                reason = f"WSB_overhype:{mention_count}mentions→contrarian_short_risk"
            else:
                score  = 2.0
                reason = f"WSB_overhype:{mention_count}mentions→contrarian_short_ok"
        elif mention_count >= 5:
            # Rising buzz = momentum confirmation
            if direction == "LONG":
                score  = 2.0
                reason = f"WSB_buzz:{mention_count}mentions→momentum_long"
            else:
                score  = -1.0
                reason = f"WSB_buzz:{mention_count}mentions→against_short"
        else:
            score  = 0.0
            reason = ""

        _cache[cache_key + "_v2"] = (float(score), reason, now)
        return float(score), reason

    except Exception as e:
        logger.debug(f"get_wsb_mention_score({symbol}): {e}")
        _cache[cache_key + "_v2"] = (0.0, "", now)
        return 0.0, ""
