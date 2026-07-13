"""
analyst_consensus.py — Analyst Recommendation Consensus Signal

Analyst consensus captures the aggregated view of sell-side analysts who spend
all day modelling individual companies. When analysts are unusually aligned (>80%
Buy) or unusually divided (high dispersion), that information has alpha beyond
the individual analyst call.

Two distinct signals:

1. CONSENSUS SCORE
   Bullish consensus (>75% Strong Buy/Buy) = institutional coverage strongly bullish
   Bearish consensus (>60% Sell/Underperform) = professional skepticism

2. DISPERSION SIGNAL (reduces size when analysts disagree)
   High dispersion (rating spread across all 5 categories) = genuine uncertainty.
   Professional models disagree = increase error bar → size down.

Data: yfinance ticker.recommendations_summary (free, no API key)
Cache: 6 hours (analyst ratings update rarely intraday)
"""
import logging
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Tuple[float, str, float]] = {}  # symbol → (score, reason, ts)
_TTL = 21600.0  # 6 hours


def _fetch_consensus(symbol: str) -> Optional[Tuple[int, int, int, int, int]]:
    """
    Returns (strongBuy, buy, hold, sell, strongSell) counts from latest month.
    Returns None on failure.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        rec = t.recommendations_summary
        if rec is None or rec.empty:
            return None
        # Most recent period is first row
        row = rec.iloc[0]
        sb = int(row.get("strongBuy",  0) or 0)
        b  = int(row.get("buy",        0) or 0)
        h  = int(row.get("hold",       0) or 0)
        s  = int(row.get("sell",       0) or 0)
        ss = int(row.get("strongSell", 0) or 0)
        return (sb, b, h, s, ss)
    except Exception as e:
        logger.debug(f"[analyst_consensus] _fetch_consensus {symbol}: {e}")
        return None


def get_analyst_consensus_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Returns (score_delta, reason). Fail-open (0.0, reason) on any error.

    Score logic:
      Bullish consensus (≥75% buy-side) → +5 LONG / -4 SHORT
      Mild bullish (≥60% buy-side)     → +3 LONG / -2 SHORT
      Bearish consensus (≥55% sell)    → -5 LONG / +5 SHORT
      Neutral / mixed                  → 0

    Size modifier via dispersion (returned as part of reason, applied in caller):
      High dispersion (Shannon entropy > 2.0) → caller should reduce size 0.85x
    """
    now = _time.monotonic()
    cached = _CACHE.get(symbol)
    if cached and (now - cached[2]) < _TTL:
        return cached[0], cached[1]

    result = _fetch_consensus(symbol)
    if result is None:
        return 0.0, "analyst:no-data"

    sb, b, h, s, ss = result
    total = sb + b + h + s + ss
    if total < 3:
        return 0.0, "analyst:too-few-ratings"

    bullish_pct = (sb + b) / total
    bearish_pct = (s + ss) / total

    import math
    # Shannon entropy: measures dispersion across 5 categories
    entropy = 0.0
    for cnt in [sb, b, h, s, ss]:
        if cnt > 0:
            p = cnt / total
            entropy -= p * math.log2(p)
    # max entropy = log2(5) ≈ 2.32

    high_dispersion = entropy > 2.0
    dispersion_tag  = "|HIGH_DISPERSION" if high_dispersion else ""

    if bullish_pct >= 0.75:
        d = 5.0 if direction == "LONG" else -4.0
        reason = f"analyst:STRONG_BUY({bullish_pct:.0%}of{total}){dispersion_tag}"
    elif bullish_pct >= 0.60:
        d = 3.0 if direction == "LONG" else -2.0
        reason = f"analyst:MILD_BUY({bullish_pct:.0%}of{total}){dispersion_tag}"
    elif bearish_pct >= 0.55:
        d = -5.0 if direction == "LONG" else 5.0
        reason = f"analyst:BEARISH({bearish_pct:.0%}sell,{total}analysts){dispersion_tag}"
    elif bearish_pct >= 0.35:
        d = -2.0 if direction == "LONG" else 2.0
        reason = f"analyst:MILD_SELL({bearish_pct:.0%}sell){dispersion_tag}"
    else:
        d = 0.0
        reason = f"analyst:neutral(bull={bullish_pct:.0%},bear={bearish_pct:.0%}){dispersion_tag}"

    _CACHE[symbol] = (d, reason, now)
    return d, reason


def get_analyst_dispersion_size_mult(symbol: str) -> float:
    """
    Returns size multiplier [0.75, 1.0] based on analyst disagreement.
    High analyst dispersion = uncertainty = reduce position size.
    Cached alongside consensus score (same TTL).
    """
    try:
        result = _fetch_consensus(symbol)
        if result is None:
            return 1.0
        sb, b, h, s, ss = result
        total = sb + b + h + s + ss
        if total < 3:
            return 1.0

        import math
        entropy = 0.0
        for cnt in [sb, b, h, s, ss]:
            if cnt > 0:
                p = cnt / total
                entropy -= p * math.log2(p)

        if entropy > 2.0:
            return 0.80  # very high disagreement
        elif entropy > 1.7:
            return 0.90
        return 1.0
    except Exception:
        return 1.0
