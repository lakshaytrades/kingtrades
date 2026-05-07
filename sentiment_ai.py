"""
sentiment_ai.py — NLP Sentiment Scorer for NSE stocks
VADER + NSE-specific keyword rules. Zero cost, runs in milliseconds.
Blocks trades when sentiment is strongly negative.
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _VADER_AVAILABLE = True
except ImportError:
    _VADER_AVAILABLE = False
    logger.warning(f"[{format_ist_timestamp()}] vaderSentiment not installed — keyword-only scoring active")

STRONG_POSITIVE = [
    "beat estimates", "record profit", "buyback", "dividend", "upgrade",
    "strong results", "order win", "contract awarded", "expansion", "acquisition",
    "debt free", "bonus shares", "stellar", "outperform", "surge", "rally",
]

STRONG_NEGATIVE = [
    "fraud", "scam", "sebi probe", "ed raid", "income tax", "promoter sell",
    "loss", "downgrade", "default", "npa", "write off", "penalty", "ban",
    "recall", "investigation", "miss estimates", "weak results", "debt trap",
]

HIGH_IMPACT_EVENTS = [
    "rbi policy", "fed meeting", "budget", "election", "gdp data",
    "inflation data", "earnings", "results", "quarterly",
]

_SENTIMENT_CACHE: Dict[str, Tuple[float, "SentimentResult"]] = {}
_CACHE_TTL_SECONDS = 1800


@dataclass
class SentimentResult:
    symbol: str
    score: float
    label: str
    headlines_used: int
    key_phrase: str
    blocks_long: bool
    blocks_short: bool
    high_impact_event: bool


class SentimentAI:

    def __init__(self):
        self._analyzer = SentimentIntensityAnalyzer() if _VADER_AVAILABLE else None

    def score_symbol(self, symbol: str, headlines: List[str]) -> SentimentResult:
        if not headlines:
            return SentimentResult(
                symbol=symbol, score=0.0, label="NEUTRAL",
                headlines_used=0, key_phrase="",
                blocks_long=False, blocks_short=False,
                high_impact_event=False,
            )

        scores: List[float] = []
        has_strong_negative = False
        high_impact = False
        key_phrase = headlines[0]
        worst_score = 1.0

        for headline in headlines:
            lower = headline.lower()

            if any(kw in lower for kw in HIGH_IMPACT_EVENTS):
                high_impact = True

            if any(kw in lower for kw in STRONG_NEGATIVE):
                has_strong_negative = True

            vader = self._vader_score(headline)
            keyword = self._keyword_score(headline)
            combined = self._combine_scores(vader, keyword)
            scores.append(combined)

            if combined < worst_score:
                worst_score = combined
                key_phrase = headline

        avg_score = sum(scores) / len(scores)

        if has_strong_negative:
            avg_score = min(avg_score, -50.0)

        label = self._label(avg_score)

        blocks_long = avg_score < -25 or (high_impact and avg_score < 0)
        blocks_short = avg_score > 25 or (high_impact and avg_score > 0)

        return SentimentResult(
            symbol=symbol,
            score=round(avg_score, 2),
            label=label,
            headlines_used=len(headlines),
            key_phrase=key_phrase,
            blocks_long=blocks_long,
            blocks_short=blocks_short,
            high_impact_event=high_impact,
        )

    def should_block_trade(
        self, symbol: str, direction: str, headlines: List[str]
    ) -> Tuple[bool, str]:
        result = self.score_symbol(symbol, headlines)
        direction_upper = direction.upper()

        if direction_upper == "LONG" and result.blocks_long:
            reason = (
                f"Sentiment {result.label} (score={result.score:.1f}) blocks LONG"
                + (" — high-impact event nearby" if result.high_impact_event else "")
            )
            return True, reason

        if direction_upper == "SHORT" and result.blocks_short:
            reason = (
                f"Sentiment {result.label} (score={result.score:.1f}) blocks SHORT"
                + (" — high-impact event nearby" if result.high_impact_event else "")
            )
            return True, reason

        return False, ""

    def score_market_sentiment(self, nifty_headlines: List[str]) -> float:
        result = self.score_symbol("NIFTY", nifty_headlines)
        return result.score

    def _vader_score(self, text: str) -> float:
        if self._analyzer is None:
            return 0.0
        return self._analyzer.polarity_scores(text)["compound"]

    def _keyword_score(self, text: str) -> float:
        lower = text.lower()
        pos = sum(1 for kw in STRONG_POSITIVE if kw in lower)
        neg = sum(1 for kw in STRONG_NEGATIVE if kw in lower)
        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / total

    def _combine_scores(self, vader: float, keyword: float) -> float:
        combined = vader * 0.6 + keyword * 0.4
        return combined * 100.0

    @staticmethod
    def _label(score: float) -> str:
        if score > 40:
            return "STRONG_BULLISH"
        if score > 15:
            return "BULLISH"
        if score >= -15:
            return "NEUTRAL"
        if score >= -40:
            return "BEARISH"
        return "STRONG_BEARISH"


_sentiment_ai_instance: Optional[SentimentAI] = None


def get_sentiment_ai() -> SentimentAI:
    global _sentiment_ai_instance
    if _sentiment_ai_instance is None:
        _sentiment_ai_instance = SentimentAI()
    return _sentiment_ai_instance


def score_with_cache(symbol: str, headlines: List[str]) -> SentimentResult:
    now = time.monotonic()
    cached = _SENTIMENT_CACHE.get(symbol)
    if cached is not None:
        cached_time, cached_result = cached
        if now - cached_time < _CACHE_TTL_SECONDS:
            return cached_result

    result = get_sentiment_ai().score_symbol(symbol, headlines)
    _SENTIMENT_CACHE[symbol] = (now, result)
    return result
