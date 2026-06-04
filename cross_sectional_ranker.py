"""
cross_sectional_ranker.py — Portfolio-Level Stock Ranking (v27.0)

Renaissance does NOT evaluate stocks one at a time. They rank ALL stocks
simultaneously and only trade the top decile. This eliminates entering a
weak setup when better opportunities exist in the watchlist.

How it works:
1. Every scan cycle, compute a quick "rank score" for each symbol using
   fast indicators (RVOL, price vs VWAP, momentum, pre-market gap)
2. Sort all symbols by rank score (descending for LONG, ascending for SHORT)
3. Only pass the top 20% to the full signal generator
4. This cuts scan time AND ensures we only enter the strongest setups

Rank score uses only BarCache data (no new fetches):
  - Relative volume vs 20-bar average (+3 per 0.5x above 1.0)
  - Price vs VWAP direction (+5 above, -5 below for LONG)
  - 5-bar momentum: last close vs 5-bar-ago close (+/- points)
  - Pre-market gap from previous session close (cached from gap_scanner)
"""
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

def rank_symbols(symbols: List[str], fetcher, direction: str = "LONG",
                 top_pct: float = 0.40) -> List[str]:
    """
    Rank symbols by quick composite score. Return top top_pct fraction.
    Falls back to original order if ranking fails.

    Args:
        symbols: full watchlist
        fetcher: AlpacaDataFetcher instance with BarCache
        direction: "LONG" or "SHORT"
        top_pct: fraction to keep (0.40 = top 40%, keeps scan manageable)

    Returns:
        Ranked list (best first), trimmed to top_pct
    """
    if not symbols:
        return symbols

    scores: Dict[str, float] = {}

    for sym in symbols:
        try:
            score = _quick_rank_score(sym, fetcher, direction)
            scores[sym] = score
        except Exception:
            scores[sym] = 0.0  # fail-open: neutral score

    # Sort by score (higher = better for LONG, lower = better for SHORT)
    sorted_syms = sorted(symbols, key=lambda s: scores.get(s, 0.0), reverse=(direction == "LONG"))

    # Keep top fraction, minimum 10 symbols
    keep_n = max(10, int(len(sorted_syms) * top_pct))
    result = sorted_syms[:keep_n]

    if len(symbols) > 15:
        top3 = result[:3]
        logger.debug(f"[ranker] {direction}: top3={top3} from {len(symbols)} symbols")

    return result


def _quick_rank_score(symbol: str, fetcher, direction: str) -> float:
    """
    Compute a fast rank score using only cached OHLCV data.
    No new API calls — uses BarCache exclusively.
    """
    score = 0.0

    try:
        # Get 5m bars from BarCache (already in memory)
        mtf = fetcher.get_multi_timeframe_data(symbol)
        df = mtf.get("5m")
        if df is None or len(df) < 10:
            return 0.0

        close_col  = 'close'  if 'close'  in df.columns else 'Close'
        volume_col = 'volume' if 'volume' in df.columns else 'Volume'
        high_col   = 'high'   if 'high'   in df.columns else 'High'
        low_col    = 'low'    if 'low'    in df.columns else 'Low'

        closes  = df[close_col].values
        volumes = df[volume_col].values
        current = float(closes[-1])

        # 1. Relative volume (last bar vs 20-bar avg)
        avg_vol = float(volumes[-20:].mean()) if len(volumes) >= 20 else float(volumes.mean())
        last_vol = float(volumes[-1])
        rvol = last_vol / avg_vol if avg_vol > 0 else 1.0
        score += min(15.0, (rvol - 1.0) * 6.0)  # +6 per unit above 1x, max +15

        # 2. Short-term momentum (5-bar return)
        if len(closes) >= 6:
            ret_5 = (closes[-1] - closes[-6]) / closes[-6] * 100
            if direction == "LONG":
                score += min(10.0, ret_5 * 2.0)
            else:
                score += min(10.0, -ret_5 * 2.0)

        # 3. VWAP relationship
        try:
            highs  = df[high_col].values
            lows   = df[low_col].values
            tp = (highs + lows + closes) / 3.0
            vol_cumsum = volumes.cumsum()
            tvp_cumsum = (tp * volumes).cumsum()
            vwap = tvp_cumsum[-1] / vol_cumsum[-1] if vol_cumsum[-1] > 0 else current
            if direction == "LONG":
                score += 8.0 if current > vwap else -4.0
            else:
                score += 8.0 if current < vwap else -4.0
        except Exception:
            pass

        # 4. Price acceleration: is momentum speeding up?
        if len(closes) >= 10:
            ret_recent = (closes[-1] - closes[-4]) / closes[-4] * 100
            ret_prev   = (closes[-4] - closes[-8]) / closes[-8] * 100
            acceleration = ret_recent - ret_prev
            if direction == "LONG" and acceleration > 0:
                score += min(5.0, acceleration * 3.0)
            elif direction == "SHORT" and acceleration < 0:
                score += min(5.0, -acceleration * 3.0)

    except Exception as e:
        logger.debug(f"[ranker] {symbol} score failed: {e}")

    return score
