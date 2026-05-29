"""
short_squeeze_detector.py — Short Interest + Squeeze Potential Engine

Short squeezes are the most explosive intraday moves (GME +1000%, AMC +400%).
Setup: high short float + volume surge + price breaking key level = covering cascade.

Data: yfinance.info (free, updates daily)
Cache: 1 hour per symbol
Score range: -8 to +18
"""
import logging
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)
_squeeze_cache: Dict = {}
_SQ_TTL = 3600.0


def get_short_squeeze_score(symbol: str, direction: str, volume_ratio: float = 1.0) -> Tuple[float, str]:
    """
    Returns (score_delta, reason).
    HIGH short float + volume surge + LONG direction = explosive squeeze potential.
    HIGH short float + SHORT direction = dangerous (squeeze risk).
    """
    now = _time.monotonic()
    cached = _squeeze_cache.get(symbol)
    if cached and now - cached["ts"] < _SQ_TTL:
        d = cached["data"]
    else:
        d = _fetch(symbol)
        if d:
            _squeeze_cache[symbol] = {"data": d, "ts": now}

    if not d:
        return 0.0, ""

    sf  = float(d.get("short_float", 0) or 0)   # e.g. 0.20 = 20%
    dtc = float(d.get("days_to_cover", 0) or 0)
    score, reasons = 0.0, []

    if direction in ("LONG", "BUY"):
        if sf >= 0.20 and dtc >= 5:
            if volume_ratio >= 3.0:
                score += 18; reasons.append(f"SQUEEZE_SETUP si={sf:.0%} dtc={dtc:.1f} vol={volume_ratio:.1f}x")
            elif volume_ratio >= 2.0:
                score += 12; reasons.append(f"SQUEEZE_LIKELY si={sf:.0%}")
            elif volume_ratio >= 1.5:
                score += 6;  reasons.append(f"SQUEEZE_WATCH si={sf:.0%}")
            else:
                score += 3
        elif sf >= 0.12:
            if volume_ratio >= 2.5:   score += 8; reasons.append(f"SHORT_COVER si={sf:.0%}")
            elif volume_ratio >= 1.5: score += 4
        elif sf < 0.02:
            score += 2   # very low short = no squeeze risk = clean LONG
    else:  # SHORT
        if sf >= 0.20:
            score -= 8; reasons.append(f"SQUEEZE_RISK si={sf:.0%}(-8)")
        elif sf >= 0.12:
            score -= 4; reasons.append(f"CROWDED_SHORT si={sf:.0%}(-4)")
        elif sf < 0.02:
            score += 4; reasons.append(f"LOW_SI_SHORT_SAFE(+4)")

    return round(score, 1), " | ".join(reasons)


def _fetch(symbol: str) -> Optional[Dict]:
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        return {
            "short_float":   float(info.get("shortPercentOfFloat") or 0),
            "days_to_cover": float(info.get("shortRatio") or 0),
        }
    except Exception as e:
        logger.debug(f"short_data({symbol}): {e}")
        return None
