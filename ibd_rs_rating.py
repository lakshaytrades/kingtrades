"""
ibd_rs_rating.py — IBD Relative Strength Rating (v16.0)
Free replica of Investor's Business Daily RS Rating 1-99.

Ranks stock by 1-year price performance vs a peer universe using
IBD's proprietary weighting formula (40% most-recent quarter +
20% × 3 prior quarters). Returns score delta for signal_generator.

Cache TTL: 4 hours (14400 seconds). Fail-open on any error.
"""

import logging
import threading
import time as _time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
CACHE_TTL = 14400  # 4 hours in seconds

# 50 liquid US symbols used as the RS peer universe (IBD-style)
RS_UNIVERSE = [
    "SPY", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "NFLX",
    "COIN", "PLTR", "GME", "AMC", "MSTR", "SMCI", "ARM", "AVGO", "QCOM", "MU",
    "INTC", "CRM", "ORCL", "ADBE", "NOW", "SNOW", "UBER", "LYFT", "ABNB", "DIS",
    "V", "MA", "JPM", "GS", "MS", "BAC", "WFC", "XOM", "CVX", "PFE",
    "JNJ", "KO", "PEP", "MCD", "WMT", "COST", "TGT", "HD", "LOW", "QQQ",
]

# ── Thread-safe singleton cache ───────────────────────────────────────────────
_lock = threading.Lock()
_cache: Dict[str, float] = {}          # symbol → composite_score
_ranks: Dict[str, int] = {}            # symbol → RS rating 1-99
_last_refresh: float = 0.0


def _compute_composite_score(prices: List[float]) -> float:
    """
    IBD composite score formula:
    - Split 252 trading days into 4 quarters (~63 days each)
    - Q1 = most recent quarter (weight 40%)
    - Q2, Q3, Q4 = prior 3 quarters (weight 20% each)
    - Returns weighted return as a percentage
    """
    n = len(prices)
    if n < 4:
        return 0.0

    def quarter_return(start_idx: int, end_idx: int) -> float:
        """Return % change from start_idx to end_idx (0=oldest)."""
        s = prices[start_idx]
        e = prices[end_idx]
        if s <= 0:
            return 0.0
        return (e - s) / s * 100.0

    # Use up to 252 bars; if fewer, scale quarter boundaries proportionally
    period = min(n - 1, 251)
    q_size = period // 4

    if q_size < 1:
        # Not enough data — just use total return
        return quarter_return(0, n - 1)

    # Q4 = oldest quarter; Q1 = most recent quarter
    q4_start = 0
    q4_end   = q_size
    q3_start = q_size
    q3_end   = q_size * 2
    q2_start = q_size * 2
    q2_end   = q_size * 3
    q1_start = q_size * 3
    q1_end   = n - 1   # most recent bar

    r1 = quarter_return(q1_start, q1_end)   # most recent Q
    r2 = quarter_return(q2_start, q2_end)
    r3 = quarter_return(q3_start, q3_end)
    r4 = quarter_return(q4_start, q4_end)

    return 0.40 * r1 + 0.20 * r2 + 0.20 * r3 + 0.20 * r4


def _refresh_ratings() -> None:
    """Download 1-year daily closes for universe and recompute RS ratings."""
    global _cache, _ranks, _last_refresh

    try:
        import yfinance as yf
        import pandas as pd

        logger.info("[IBD_RS] Refreshing RS ratings for %d symbols…", len(RS_UNIVERSE))
        raw = yf.download(
            RS_UNIVERSE,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        # Extract 'Close' prices; handle both single and multi-symbol returns
        if isinstance(raw.columns, pd.MultiIndex):
            close_df = raw["Close"]
        else:
            close_df = raw[["Close"]] if "Close" in raw.columns else raw

        scores: Dict[str, float] = {}
        for sym in RS_UNIVERSE:
            try:
                if sym not in close_df.columns:
                    continue
                prices = close_df[sym].dropna().tolist()
                if len(prices) < 20:
                    continue
                scores[sym] = _compute_composite_score(prices)
            except Exception as _e:
                logger.debug("[IBD_RS] %s score error: %s", sym, _e)

        if not scores:
            logger.warning("[IBD_RS] No scores computed — keeping stale cache")
            return

        # Rank 1-99 (1=weakest, 99=strongest)
        sorted_syms = sorted(scores.keys(), key=lambda s: scores[s])
        n_total = len(sorted_syms)
        ranks: Dict[str, int] = {}
        for i, sym in enumerate(sorted_syms):
            # Percentile rank scaled to 1-99
            rank = max(1, min(99, int(round(1 + 98 * i / max(n_total - 1, 1)))))
            ranks[sym] = rank

        with _lock:
            _cache = dict(scores)
            _ranks = dict(ranks)
            _last_refresh = _time.monotonic()

        logger.info(
            "[IBD_RS] Ratings refreshed: %d symbols | top-5: %s",
            len(ranks),
            [(s, ranks[s]) for s in sorted_syms[-5:]],
        )

    except Exception as exc:
        logger.warning("[IBD_RS] Refresh failed (fail-open): %s", exc)


def _ensure_fresh() -> None:
    """Trigger a refresh if cache is stale or empty."""
    global _last_refresh
    with _lock:
        stale = (_time.monotonic() - _last_refresh) > CACHE_TTL
        empty = not _ranks

    if stale or empty:
        _refresh_ratings()


def get_rs_rating(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Return (score_delta, reason) for the given symbol and trade direction.

    RS rating buckets (LONG):
      ≥ 90  → +12  (top decile — strong relative strength)
      75-89 → +7   (strong RS)
      40-60 → 0    (neutral)
      < 40  → -8   (weak — avoid buying weak stocks)

    For SHORT: reverse logic (low RS = tailwind for shorts).
    Cached 4h. Fail-open — returns (0.0, "RS_UNAVAILABLE") on any error.
    """
    try:
        _ensure_fresh()

        with _lock:
            rs = _ranks.get(symbol.upper())

        if rs is None:
            # Symbol not in universe — attempt on-demand score
            try:
                import yfinance as yf
                hist = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
                if len(hist) >= 20:
                    prices = hist["Close"].dropna().tolist()
                    score = _compute_composite_score(prices)
                    # Compare to universe scores to derive rank
                    with _lock:
                        all_scores = sorted(_cache.values())
                    if all_scores:
                        import bisect
                        pos = bisect.bisect_left(all_scores, score)
                        rs = max(1, min(99, int(round(1 + 98 * pos / max(len(all_scores) - 1, 1)))))
                    else:
                        rs = 50   # neutral default
                else:
                    return 0.0, "RS_INSUFFICIENT_DATA"
            except Exception:
                return 0.0, "RS_UNAVAILABLE"

        if direction == "LONG":
            if rs >= 90:
                return 12.0, f"RS_RATING={rs} (top decile — strong momentum)"
            elif rs >= 75:
                return 7.0, f"RS_RATING={rs} (strong RS)"
            elif rs >= 40:
                return 0.0, f"RS_RATING={rs} (neutral)"
            else:
                return -8.0, f"RS_RATING={rs} (weak — avoid buying laggards)"
        else:  # SHORT
            if rs <= 10:
                return 12.0, f"RS_RATING={rs} (worst decile — prime short)"
            elif rs <= 25:
                return 7.0, f"RS_RATING={rs} (weak RS — good short)"
            elif rs >= 75:
                return -8.0, f"RS_RATING={rs} (strong RS — dangerous short)"
            else:
                return 0.0, f"RS_RATING={rs} (neutral)"

    except Exception as exc:
        logger.debug("[IBD_RS] get_rs_rating(%s) failed: %s", symbol, exc)
        return 0.0, "RS_ERROR"
