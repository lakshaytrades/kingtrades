"""
edgar_sentiment.py — SEC EDGAR NLP Sentiment Analysis (v28.0)

Free proxy for: $25k/year Bloomberg terminal news sentiment feed.

SEC EDGAR full-text search (free, no API key):
  efts.sec.gov/hits.action?q="SYMBOL"&dateRange=custom&...

Analyzes recent 8-K filings (material events) for tone using
Loughran-McDonald (2011) finance-specific word lists.
Proven to predict 1-3 day abnormal returns.

LM word lists (free, published in the paper):
  Positive: "record", "exceeded", "growth", "raised", "strong", "exceeded expectations"
  Negative: "impairment", "restructuring", "below", "withdrawn", "weakness", "decline"
"""

import logging
import re
import time as _time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

_cache: Dict[str, Tuple[float, str, float]] = {}
_CACHE_TTL = 86400.0  # 24 hours (SEC filings don't change intraday)

# Loughran-McDonald positive/negative word lists (subset — key predictive words)
_LM_POSITIVE = {
    "record", "exceeded", "exceeds", "surpassed", "strong", "growth",
    "raised", "increase", "increased", "improvement", "improved",
    "outperform", "upside", "expansion", "accelerating", "momentum",
    "profitability", "gains", "achieved", "benefit", "favorable",
    "robust", "significant growth", "record revenue", "record earnings",
    "raised guidance", "beat", "beats", "above expectations",
}

_LM_NEGATIVE = {
    "impairment", "restructuring", "restructure", "decline", "declined",
    "below", "miss", "missed", "shortfall", "disappointing", "weakness",
    "weaker", "slowdown", "withdrawn", "withdrawal", "writedown", "writeoff",
    "loss", "losses", "below expectations", "challenging", "headwinds",
    "uncertainty", "risk", "adverse", "unfavorable", "deterioration",
    "reduction", "reduced guidance", "lowered guidance", "negative",
    "downturn", "difficult", "pressured", "eroded",
}


def get_edgar_sentiment_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Fetch and analyze recent SEC 8-K filings for the symbol.
    Returns (score_delta, reason). Fail-open.
    """
    now = _time.monotonic()
    cached = _cache.get(symbol)
    if cached and (now - cached[2]) < _CACHE_TTL:
        raw, reason, _ = cached
        return _directional(raw, direction), reason

    score, reason = _fetch_edgar_sentiment(symbol)
    _cache[symbol] = (score, reason, now)
    return _directional(score, direction), reason


def _fetch_edgar_sentiment(symbol: str) -> Tuple[float, str]:
    """Query EDGAR full-text search for recent 8-K filings."""
    try:
        import requests

        # EDGAR full-text search API (free)
        url = (
            "https://efts.sec.gov/LATEST/search-index?q=%22"
            + symbol
            + "%22&dateRange=custom&startdt="
            + _days_ago(5)
            + "&enddt="
            + _days_ago(0)
            + "&forms=8-K"
        )
        headers = {
            "User-Agent": "KingTrades research@kingtrades.ai",
            "Accept": "application/json",
        }
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return 0.0, f"edgar:http-{resp.status_code}"

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        if not hits:
            return 0.0, "edgar:no-recent-filings"

        # Analyze the most recent hit
        latest = hits[0]
        source = latest.get("_source", {})
        display_names = source.get("display_names", [])
        file_date = source.get("file_date", "unknown")

        # Get filing text snippet
        filing_text = ""
        highlight = latest.get("highlight", {})
        for key, frags in highlight.items():
            filing_text += " ".join(frags) + " "

        if not filing_text:
            # Try to get summary from source
            filing_text = str(source.get("period_of_report", "")) + " " + \
                          str(source.get("entity_name", ""))

        filing_text_lower = filing_text.lower()

        pos_count = sum(1 for w in _LM_POSITIVE if w in filing_text_lower)
        neg_count = sum(1 for w in _LM_NEGATIVE if w in filing_text_lower)

        total = pos_count + neg_count
        if total == 0:
            return 0.0, f"edgar:neutral(filed {file_date})"

        sentiment = (pos_count - neg_count) / total

        if sentiment > 0.5:
            return 8.0, f"edgar:bullish(pos={pos_count},neg={neg_count},date={file_date})"
        elif sentiment > 0.2:
            return 4.0, f"edgar:mildly-bullish(pos={pos_count},neg={neg_count})"
        elif sentiment < -0.5:
            return -8.0, f"edgar:bearish(neg={neg_count},pos={pos_count},date={file_date})"
        elif sentiment < -0.2:
            return -4.0, f"edgar:mildly-bearish(neg={neg_count},pos={pos_count})"
        return 0.0, f"edgar:balanced(pos={pos_count},neg={neg_count})"

    except Exception as e:
        logger.debug(f"[edgar] {symbol}: {e}")
        return 0.0, "edgar:error"


def _days_ago(n: int) -> str:
    """Return ISO date string for n days ago."""
    from datetime import datetime, timedelta
    d = datetime.utcnow() - timedelta(days=n)
    return d.strftime("%Y-%m-%d")


def _directional(raw_score: float, direction: str) -> float:
    if direction == "SHORT":
        return -raw_score
    return raw_score
