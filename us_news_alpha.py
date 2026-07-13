"""
us_news_alpha.py — US Stock News NLP Alpha (God Mode)
Sources: SEC EDGAR RSS (official filings), Yahoo Finance news,
         Reddit WallStreetBets mention surge detection.

3 signals:
  1. SEC 8-K material events (acquisition, guidance change, etc.)
  2. News sentiment from Yahoo Finance headlines (VADER NLP + keyword fallback)
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

# Breaking news terms that trigger an immediate -10 score override
BREAKING_NEG = [
    "going concern", "fraud", "restatement", "bankruptcy",
    "sec subpoena", "class action", "fda reject",
]

# ── Entity relevance map ───────────────────────────────────────────────────────
_COMPANY_MAP = {
    "AAPL": ["apple"], "MSFT": ["microsoft"], "NVDA": ["nvidia"],
    "GOOGL": ["google", "alphabet"], "GOOG": ["google", "alphabet"],
    "META": ["meta", "facebook"], "AMZN": ["amazon"], "TSLA": ["tesla"],
    "NFLX": ["netflix"], "AMD": ["advanced micro"], "INTC": ["intel"],
    "JPM": ["jpmorgan", "j.p. morgan"], "BAC": ["bank of america"],
    "SPY": ["s&p 500", "spx", "s&p500"], "QQQ": ["nasdaq", "qqq"],
}


def _score_keywords(text: str) -> float:
    """
    Pure keyword scoring on a single text string. Returns a raw float.
    Positive = bullish, negative = bearish.
    """
    score = 0.0
    for kw in STRONG_POS:
        if kw in text:
            score += 3.0
            break
    for kw in STRONG_NEG:
        if kw in text:
            score -= 4.0
            break
    for kw in MILD_POS:
        if kw in text:
            score += 1.5
            break
    for kw in MILD_NEG:
        if kw in text:
            score -= 1.5
            break
    return score


def _score_with_vader(self_obj, text: str) -> float:
    """
    VADER compound score [-1, +1]. Falls back to keyword scoring if unavailable.
    self_obj is a carrier object that stores the _vader instance lazily.
    """
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        if not hasattr(self_obj, '_vader'):
            self_obj._vader = SentimentIntensityAnalyzer()
        return self_obj._vader.polarity_scores(text)['compound']
    except ImportError:
        # Normalize keyword score to [-1, +1] range for blending
        raw = _score_keywords(text)
        return max(-1.0, min(1.0, raw / 4.0))
    except Exception:
        return 0.0


def _is_entity_relevant(text: str, symbol: str) -> bool:
    """True if the headline/text explicitly references the ticker or known company name."""
    tl = text.lower()
    if f" {symbol.lower()} " in f" {tl} ":
        return True
    for name in _COMPANY_MAP.get(symbol, []):
        if name in tl:
            return True
    return False


# Module-level carrier object to lazily hold the VADER analyzer singleton
class _VaderCarrier:
    pass

_vader_carrier = _VaderCarrier()


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
                headlines.append(combined)
        _cache[cache_key] = (headlines, now)
        return headlines
    except Exception as e:
        logger.debug(f"_fetch_yahoo_headlines({symbol}): {e}")
        _cache[cache_key] = ([], now)
        return []


def _score_headlines(headlines: List[str], direction: str, symbol: str = "") -> Tuple[float, str]:
    """
    VADER × 0.6 + keyword × 0.4 per headline.
    If entity-relevant, multiply headline score by 1.8.
    Detects BREAKING news and returns score = -10 immediately.
    Score aligned with direction. Clamp [-12, +10].
    """
    if not headlines:
        return 0.0, ""

    raw_score = 0.0
    hits: List[str] = []
    breaking_flag = False

    for text in headlines[:10]:  # limit to 10 most recent
        tl = text.lower()

        # Breaking news detection — immediate override
        for term in BREAKING_NEG:
            if term in tl:
                breaking_flag = True
                hits.append(f"BREAKING:{term}")
                break

        if breaking_flag:
            break

        # VADER score (compound, already in [-1, +1])
        vader_score = _score_with_vader(_vader_carrier, text)
        # Keyword score normalized to ~[-1, +1]
        kw_raw = _score_keywords(tl)
        kw_score = max(-1.0, min(1.0, kw_raw / 4.0))

        # Blend VADER + keyword
        blended = vader_score * 0.6 + kw_score * 0.4

        # Scale to roughly [-4, +3] per headline (matches old per-headline range)
        scaled = blended * 3.5

        # Entity relevance multiplier
        if symbol and _is_entity_relevant(text, symbol):
            scaled *= 1.8
            hits.append(f"entity_match({symbol})")

        raw_score += scaled
        if abs(vader_score) > 0.3:
            hits.append(f"vader={vader_score:.2f}")

    if breaking_flag:
        reason = f"BREAKING_NEWS:[{','.join(hits[:3])}]"
        return -10.0, reason

    # Align with direction
    if direction == "SHORT":
        raw_score = -raw_score  # positive news is bad for shorts

    score = max(-12.0, min(10.0, raw_score))
    reason = f"news_nlp:[{','.join(hits[:3])}]" if hits else "news_neutral"
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


def get_news_alpha_score(symbol: str, direction: str = "LONG") -> Tuple[float, str]:
    """
    Main entry point. Combines:
      - Yahoo Finance headlines (VADER × 0.6 + keyword × 0.4; entity-relevant × 1.8)
      - SEC EDGAR 8-K filings
    Cached 20 min. Fail-open (0.0, "").

    Breaking news override: if any headline contains a BREAKING_NEG term, returns -10.
    """
    cache_key = f"news_alpha:{symbol}:{direction}"
    now = _time.time()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[2] < _TTL:
        return cached[0], cached[1]

    try:
        headlines = _fetch_yahoo_headlines(symbol)
        yahoo_score, yahoo_reason = _score_headlines(headlines, direction, symbol=symbol)

        # If BREAKING news detected, short-circuit — don't even weight SEC
        if "BREAKING_NEWS" in yahoo_reason:
            _cache[cache_key] = (yahoo_score, yahoo_reason, now)
            return float(yahoo_score), yahoo_reason

        sec_score, sec_reason = get_sec_8k_score(symbol, direction)

        combined = yahoo_score * 0.6 + sec_score * 0.4
        combined = max(-12.0, min(10.0, combined))

        parts = []
        if yahoo_reason and yahoo_reason != "news_neutral":
            parts.append(f"yahoo:{yahoo_reason}")
        if sec_reason:
            parts.append(f"sec:{sec_reason}")
        reason = " | ".join(parts) if parts else "news_neutral"

        _cache[cache_key] = (float(combined), reason, now)
        return float(combined), reason
    except Exception as e:
        logger.debug(f"get_news_alpha_score({symbol}): {e}")
        return 0.0, ""


# Legacy alias — kept for backward compatibility with existing callers
def get_news_score(symbol: str, direction: str) -> Tuple[float, str]:
    """Backward-compatible alias for get_news_alpha_score."""
    return get_news_alpha_score(symbol, direction)


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
