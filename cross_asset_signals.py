"""
cross_asset_signals.py — Cross-asset market regime filter.
VIX + bonds + dollar = overall market risk score.
Renaissance style: trade WITH the macro, not against it.
"""
import logging
import time
from typing import Tuple

logger = logging.getLogger(__name__)
_cache: dict = {}
_TTL = 900.0


def get_market_risk_score() -> Tuple[float, str]:
    """
    Returns (score_modifier, reason). Range: -15 to +10.
    Negative = risk-off (penalise longs, boost shorts).
    Positive = risk-on (boost longs).
    Fail-open: returns (0.0, "unavailable").
    """
    try:
        import yfinance as yf
        now = time.time()
        cached = _cache.get("score")
        if cached and now - cached[1] < _TTL:
            return cached[0]

        score = 0.0
        reasons = []

        # VIX
        try:
            vix = yf.Ticker("^VIX").fast_info.get("lastPrice", 20)
            if vix and vix > 0:
                if vix < 15:   score += 5;  reasons.append(f"VIX={vix:.0f}low")
                elif vix < 20: score += 2;  reasons.append(f"VIX={vix:.0f}ok")
                elif vix > 30: score -= 10; reasons.append(f"VIX={vix:.0f}FEAR")
                elif vix > 25: score -= 5;  reasons.append(f"VIX={vix:.0f}elev")
        except Exception:
            pass

        # TLT (bonds) 5-day slope
        try:
            tlt = yf.Ticker("TLT").history(period="8d", auto_adjust=True)
            if len(tlt) >= 5:
                ret = float(tlt["Close"].iloc[-1] / tlt["Close"].iloc[-5] - 1)
                if ret > 0.01:   score += 3; reasons.append("bonds_up")
                elif ret < -0.02: score -= 4; reasons.append("bonds_dn")
        except Exception:
            pass

        # UUP dollar
        try:
            uup = yf.Ticker("UUP").history(period="8d", auto_adjust=True)
            if len(uup) >= 5:
                ret = float(uup["Close"].iloc[-1] / uup["Close"].iloc[-5] - 1)
                if ret > 0.01:   score -= 3; reasons.append("USD_strong")
                elif ret < -0.01: score += 2; reasons.append("USD_weak")
        except Exception:
            pass

        result = (float(max(-15.0, min(10.0, score))), " | ".join(reasons) or "neutral")
        _cache["score"] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"cross_asset fail-open: {e}")
        return (0.0, "unavailable")
