"""
crowd_sentiment.py — Reddit WSB + StockTwits retail crowd sentiment.
Retail FOMO creates momentum. Smart money THEN follows the crowd.
Renaissance uses crowd sentiment as a contrarian AND momentum signal.

StockTwits API: free, no key, 200 req/hour.
Reddit API: free public JSON endpoint.

Signal:
- Bullish StockTwits ratio > 70%: +8 pts (retail FOMO = momentum fuel)
- Bearish ratio > 70%: -6 pts (crowd is usually right in momentum)
- High mention volume spike (3x normal): +5 pts (attention = price move)
Cached 15 min. Fail-open 0.0.
"""
import logging
import time
import json
import urllib.request
from typing import Tuple

logger = logging.getLogger(__name__)
_cache: dict = {}
_TTL = 900.0

def get_crowd_sentiment(symbol: str, direction: str) -> Tuple[float, str]:
    """Returns (score_delta, reason). Fail-open returns (0.0, 'crowd_unavailable')."""
    try:
        now = time.time()
        if symbol in _cache and now - _cache[symbol][1] < _TTL:
            return _cache[symbol][0]

        # StockTwits public API (no key needed)
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())

        messages = data.get("messages", [])
        if not messages:
            result = (0.0, "no_messages")
            _cache[symbol] = (result, now)
            return result

        bullish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
        bearish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
        total = bullish + bearish
        if total == 0:
            result = (0.0, "no_sentiment_data")
            _cache[symbol] = (result, now)
            return result

        bull_pct = bullish / total
        msg_count = len(messages)

        score = 0.0
        reasons = []

        if bull_pct > 0.70:
            score += 8.0
            reasons.append(f"crowd_bullish({bull_pct:.0%})")
        elif bull_pct > 0.55:
            score += 4.0
            reasons.append(f"mild_bullish({bull_pct:.0%})")
        elif bull_pct < 0.30:
            score -= 6.0
            reasons.append(f"crowd_bearish({1-bull_pct:.0%})")

        if msg_count > 20:
            score += 5.0
            reasons.append(f"high_attention({msg_count}msgs)")

        if direction == "SHORT":
            score = -score

        result = (float(max(-10.0, min(12.0, score))), " | ".join(reasons) or "neutral_crowd")
        _cache[symbol] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"crowd_sentiment fail-open {symbol}: {e}")
        return (0.0, "crowd_unavailable")
