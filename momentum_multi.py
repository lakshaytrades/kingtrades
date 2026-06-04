"""
momentum_multi.py — Multi-Period Momentum Factor (v26.0)
The most replicated alpha factor in finance (Jegadeesh & Titman 1993).
AQR, Renaissance, Two Sigma all use this as a core signal.
"""
import logging
import time as _time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

_cache: Dict = {}
_TTL = 300.0  # 5 min cache


def get_multi_momentum_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Compute momentum at 1d, 3d, 5d, 10d, 20d lookback periods using yfinance.

    Scoring:
    - All 5 periods aligned with direction: +12 (acceleration)
    - 4/5 aligned: +8
    - 3/5 aligned: +4
    - Short-term reversal vs long-term (1d,3d oppose 10d,20d): -6 (exhaustion)
    - All 5 opposing direction: -8

    Also compute momentum quality:
    - What % of last 10 days were positive closes? >70% → +4 more

    Cached 5 min. Fail-open (0.0, "error").
    """
    try:
        cache_key = (symbol, direction)
        cached = _cache.get(cache_key)
        if cached is not None:
            c_score, c_reason, c_ts = cached
            if (_time.monotonic() - c_ts) < _TTL:
                return (c_score, c_reason)

        import yfinance as yf
        tk = yf.Ticker(symbol)
        hist = tk.history(period="30d", interval="1d", auto_adjust=True)

        if hist is None or hist.empty or len(hist) < 21:
            result = (0.0, f"insufficient history ({len(hist) if hist is not None else 0} days)")
            _cache[cache_key] = (*result, _time.monotonic())
            return result

        closes = hist["Close"].dropna()
        if len(closes) < 21:
            result = (0.0, "not enough close data")
            _cache[cache_key] = (*result, _time.monotonic())
            return result

        current = float(closes.iloc[-1])

        # Returns at each lookback
        lookbacks = [1, 3, 5, 10, 20]
        returns = {}
        for lb in lookbacks:
            if len(closes) > lb:
                past = float(closes.iloc[-(lb + 1)])
                returns[lb] = (current - past) / max(past, 0.01)
            else:
                returns[lb] = 0.0

        # Count aligned periods
        if direction == "LONG":
            aligned = sum(1 for r in returns.values() if r > 0)
            opposing = sum(1 for r in returns.values() if r < 0)
        else:  # SHORT
            aligned = sum(1 for r in returns.values() if r < 0)
            opposing = sum(1 for r in returns.values() if r > 0)

        # Short-term vs long-term reversal check
        # 1d,3d oppose 10d,20d = exhaustion signal
        st_aligned_long = (returns[1] > 0 and returns[3] > 0)
        lt_aligned_long = (returns[10] > 0 and returns[20] > 0)
        reversal = (
            (st_aligned_long and not lt_aligned_long) or
            (not st_aligned_long and lt_aligned_long)
        )

        # Momentum quality: % positive days in last 10
        last_10_closes = closes.iloc[-11:]
        pos_days = int((last_10_closes.diff().dropna() > 0).sum())
        quality_pct = pos_days / 10.0 if len(last_10_closes) >= 10 else 0.5

        # Base score
        if aligned == 5:
            base_score = 12.0
            reason = f"5/5 momentum periods aligned ({direction})"
        elif aligned == 4:
            base_score = 8.0
            reason = f"4/5 momentum periods aligned ({direction})"
        elif aligned == 3:
            base_score = 4.0
            reason = f"3/5 momentum periods aligned ({direction})"
        elif opposing == 5:
            base_score = -8.0
            reason = f"all 5 periods oppose {direction}"
        elif reversal:
            base_score = -6.0
            reason = f"short-term/long-term reversal (exhaustion) {direction}"
        else:
            base_score = 0.0
            reason = f"{aligned}/5 periods aligned — no strong signal"

        # Quality bonus
        quality_bonus = 0.0
        if direction == "LONG" and quality_pct > 0.70:
            quality_bonus = 4.0
            reason += f" | quality bonus: {pos_days}/10 positive days"
        elif direction == "SHORT" and (1 - quality_pct) > 0.70:
            quality_bonus = 4.0
            reason += f" | quality bonus: {10 - pos_days}/10 negative days"

        total = base_score + quality_bonus
        result = (total, reason)
        _cache[cache_key] = (*result, _time.monotonic())
        return result

    except Exception as _e:
        logger.debug(f"[suppressed] get_multi_momentum_score({symbol}): {_e}")
        return (0.0, "error — fail-open")
