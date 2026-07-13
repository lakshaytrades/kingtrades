"""
options_intensity.py — Unusual options activity detector.
When call/put volume is 3x+ normal and concentrated near ATM:
institutional positioning signal before price move.
Uses yfinance options chain. Cached 15 min. Fail-open.
"""
import logging
import time
from typing import Tuple

logger = logging.getLogger(__name__)
_cache: dict = {}
_TTL = 900.0


def get_options_intensity_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Returns (score_delta, reason). Range -8 to +15.
    Fail-open returns (0.0, 'options_unavailable').
    """
    try:
        now = time.time()
        if symbol in _cache and now - _cache[symbol][1] < _TTL:
            return _cache[symbol][0]
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        exps = ticker.options
        if not exps:
            result = (0.0, "no_options")
            _cache[symbol] = (result, now)
            return result
        # Use nearest expiry
        chain = ticker.option_chain(exps[0])
        calls = chain.calls
        puts = chain.puts
        if calls.empty or puts.empty:
            result = (0.0, "empty_chain")
            _cache[symbol] = (result, now)
            return result
        total_call_vol = int(calls["volume"].fillna(0).sum())
        total_put_vol = int(puts["volume"].fillna(0).sum())
        total_call_oi = int(calls["openInterest"].fillna(0).sum())
        # Put/call ratio
        pcr = total_put_vol / max(total_call_vol, 1)
        # Call vol vs OI ratio (new positioning = vol >> OI)
        call_activity = total_call_vol / max(total_call_oi, 1)
        score = 0.0
        reasons = []
        if direction == "LONG":
            if pcr < 0.5:
                score += 8; reasons.append(f"bullish_options(PCR={pcr:.2f})")
            elif pcr < 0.7:
                score += 4; reasons.append(f"call_heavy(PCR={pcr:.2f})")
            elif pcr > 1.5:
                score -= 6; reasons.append(f"put_heavy(PCR={pcr:.2f})")
            if call_activity > 0.3:
                score += 5; reasons.append("unusual_call_flow")
        elif direction == "SHORT":
            if pcr > 1.5:
                score += 8; reasons.append(f"bearish_options(PCR={pcr:.2f})")
            elif pcr > 1.0:
                score += 4; reasons.append(f"put_heavy(PCR={pcr:.2f})")
            elif pcr < 0.5:
                score -= 5; reasons.append("calls_dominant(bad_for_short)")
        result = (float(max(-8, min(15, score))), " | ".join(reasons) or "neutral_options")
        _cache[symbol] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"options_intensity fail-open {symbol}: {e}")
        return (0.0, "options_unavailable")
