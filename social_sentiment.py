"""
social_sentiment.py — Reddit WSB + StockTwits sentiment replica (v17.0)

Combines Reddit r/wallstreetbets mention/sentiment with StockTwits
Bullish/Bearish ratio for a free social sentiment score.

Thread-safe singleton with 30-min TTL cache.  Fail-open (returns 0.0).
"""

import logging
import threading
import time as _time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# ── TTL cache ─────────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, float]] = {}   # key → (score, expire_ts)
_cache_lock = threading.Lock()
_TTL = 1800   # 30 minutes


def _cache_get(key: str) -> Tuple[bool, float]:
    with _cache_lock:
        if key in _cache:
            score, exp = _cache[key]
            if _time.monotonic() < exp:
                return True, score
    return False, 0.0


def _cache_set(key: str, score: float) -> None:
    with _cache_lock:
        _cache[key] = (score, _time.monotonic() + _TTL)


# ── Reddit WSB ────────────────────────────────────────────────────────────────

_BULLISH_WORDS = {"calls", "moon", "buy", "bull", "long", "calls", "yolo", "squeeze"}
_BEARISH_WORDS = {"puts", "crash", "bear", "sell", "short", "dump", "puts", "drop"}
_BULL_EMOJI    = {"🚀", "📈", "🟢", "💎"}
_BEAR_EMOJI    = {"🩸", "📉", "🔴", "💀"}


def _wsb_score(symbol: str, direction: str) -> float:
    """Fetch WSB Reddit posts and compute sentiment score. Fail-open → 0.0"""
    try:
        import urllib.request
        import json

        url = (
            f"https://www.reddit.com/r/wallstreetbets/search.json"
            f"?q={symbol}&sort=new&limit=25&t=day"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "KingTradesBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        posts = data.get("data", {}).get("children", [])
        mention_count = len(posts)
        bullish = 0
        bearish = 0

        for post in posts:
            pd = post.get("data", {})
            text = (pd.get("title", "") + " " + pd.get("selftext", "")).lower()
            for w in _BULLISH_WORDS:
                if w in text:
                    bullish += 1
            for e in _BULL_EMOJI:
                if e in text:
                    bullish += 1
            for w in _BEARISH_WORDS:
                if w in text:
                    bearish += 1
            for e in _BEAR_EMOJI:
                if e in text:
                    bearish += 1

        if mention_count > 20:
            if direction == "LONG":
                return 10.0 if bullish > bearish else -8.0
            else:
                return 10.0 if bearish > bullish else -8.0
        elif mention_count >= 10:
            if direction == "LONG":
                return 5.0 if bullish > bearish else -5.0
            else:
                return 5.0 if bearish > bullish else -5.0
        return 0.0
    except Exception as exc:
        logger.debug(f"WSB fetch error (fail-open): {exc}")
        return 0.0


def _stocktwits_score(symbol: str, direction: str) -> float:
    """Fetch StockTwits free stream and compute Bullish/Bearish ratio. Fail-open → 0.0"""
    try:
        import urllib.request
        import json

        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        req = urllib.request.Request(url, headers={"User-Agent": "KingTradesBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        messages = data.get("messages", [])
        bullish_count = 0
        bearish_count = 0
        for msg in messages:
            sent = msg.get("entities", {}).get("sentiment", None)
            if sent:
                basic = sent.get("basic", "")
                if basic == "Bullish":
                    bullish_count += 1
                elif basic == "Bearish":
                    bearish_count += 1

        total = bullish_count + bearish_count
        if total == 0:
            return 0.0

        bull_ratio = bullish_count / total
        if direction == "LONG":
            if bull_ratio > 0.65:
                return 8.0
            elif bull_ratio < 0.35:
                return -6.0
        else:  # SHORT
            if bull_ratio < 0.35:
                return 8.0
            elif bull_ratio > 0.65:
                return -6.0
        return 0.0
    except Exception as exc:
        logger.debug(f"StockTwits fetch error (fail-open): {exc}")
        return 0.0


# ── Public API ────────────────────────────────────────────────────────────────

def get_social_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Reddit WSB × 0.6 + StockTwits × 0.4 social sentiment score.

    Returns (score_delta, reason_string).
    Fail-open: returns (0.0, "social N/A") on any error.
    30-min TTL cache per symbol+direction.
    """
    cache_key = f"{symbol}:{direction}"
    hit, cached = _cache_get(cache_key)
    if hit:
        return cached, f"social cached {cached:+.0f}"

    try:
        wsb = _wsb_score(symbol, direction)
        st  = _stocktwits_score(symbol, direction)
        combined = round(wsb * 0.6 + st * 0.4, 1)
        _cache_set(cache_key, combined)
        reason = f"WSB={wsb:+.0f} ST={st:+.0f} → {combined:+.1f}"
        return combined, reason
    except Exception as exc:
        logger.debug(f"get_social_score error (fail-open): {exc}")
        return 0.0, "social N/A"
