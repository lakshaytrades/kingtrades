"""
premarket_gap_scanner.py — Pre-market gap classification.
Runs 9:00-9:30 AM ET. Classifies each stock's overnight gap.
Gap types drive different strategies at open:
- Momentum gap (2-5%, volume 2x): join the gap continuation
- Earnings gap (>5%): fade after 30 min (mean reversion)
- Weak gap (<1%): ignore
Cached per day. Fail-open returns {}.
"""
import logging
import time
from typing import Dict, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)
_gap_cache: Dict[str, Tuple[float, str]] = {}  # symbol -> (score, type)
_cache_date: str = ""


def scan_gaps(symbols: list) -> Dict[str, Tuple[float, str]]:
    """
    Returns {symbol: (score_delta, gap_type)} for all symbols with notable gaps.
    Call once at 9:00-9:25 AM ET. Results persist all day.
    """
    global _gap_cache, _cache_date
    today = datetime.now().strftime("%Y-%m-%d")
    if _cache_date == today and _gap_cache:
        return _gap_cache
    try:
        import yfinance as yf
        result = {}
        for sym in symbols[:50]:  # limit to 50 to avoid rate limits
            try:
                hist = yf.Ticker(sym).history(period="3d", auto_adjust=True)
                if len(hist) < 2:
                    continue
                prev_close = float(hist["Close"].iloc[-2])
                today_open = float(hist["Open"].iloc[-1])
                gap_pct = (today_open - prev_close) / prev_close
                today_vol = float(hist["Volume"].iloc[-1])
                avg_vol = float(hist["Volume"].iloc[:-1].mean())
                vol_ratio = today_vol / max(avg_vol, 1)
                if abs(gap_pct) < 0.01:
                    continue  # weak gap, skip
                elif abs(gap_pct) > 0.05:
                    # Earnings/news gap — fade after 30 min
                    score = 6.0 if gap_pct > 0 else -6.0
                    result[sym] = (score, f"earnings_gap_{gap_pct:+.1%}")
                elif abs(gap_pct) > 0.02 and vol_ratio > 1.5:
                    # Momentum gap — join it
                    score = 10.0 if gap_pct > 0 else -10.0
                    result[sym] = (score, f"momentum_gap_{gap_pct:+.1%}_vol{vol_ratio:.1f}x")
                else:
                    score = 4.0 if gap_pct > 0 else -4.0
                    result[sym] = (score, f"gap_{gap_pct:+.1%}")
                time.sleep(0.1)  # rate limit
            except Exception:
                continue
        _gap_cache = result
        _cache_date = today
        logger.info(f"Pre-market gap scan: {len(result)} notable gaps found")
        return result
    except Exception as e:
        logger.debug(f"premarket_gap_scanner fail-open: {e}")
        return {}


def get_gap_score(symbol: str, direction: str) -> Tuple[float, str]:
    """Get cached gap score for a symbol. Fail-open returns (0.0, 'no_gap_data')."""
    try:
        if symbol not in _gap_cache:
            return (0.0, "no_gap_data")
        score, gap_type = _gap_cache[symbol]
        # Align score with trade direction
        if direction == "SHORT":
            score = -score  # reverse for shorts
        return (score, gap_type)
    except Exception:
        return (0.0, "gap_error")
