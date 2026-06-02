"""
factor_alpha.py — Cross-Sectional Factor Alpha Model v1.0

AQR, Renaissance, and Two Sigma allocate based on FACTOR RANKS, not
individual signals. A stock in the top decile of multiple factors
simultaneously is far more likely to outperform than one that only
has a strong chart pattern.

4 factors combined into a composite rank score:

  Factor 1: Price Momentum (20-day total return)
    — Jegadeesh & Titman (1993): documented alpha in every decade
    — Buy top quintile, short bottom quintile
    — Score: rank percentile mapped to -8..+8

  Factor 2: Relative Strength vs SPY (beta-adjusted excess return)
    — Alpha = return - beta × SPY_return
    — Stocks beating the market on risk-adjusted basis → higher quality
    — Score: top quintile +5, bottom quintile -4

  Factor 3: Earnings Quality (EPS growth consistency)
    — yfinance earnings data: QoQ EPS growth trend
    — Consistent growers > one-hit wonders
    — Score: +4 if positive growth streak, -3 if deteriorating

  Factor 4: Short-Term Reversal Guard (5-day RSI extremes)
    — Filters out overbought momentum longs and oversold shorts
    — If stock up >12% in 5 days → mean reversion risk → -4
    — Prevents buying extended breakouts

Combined factor score → rank percentile → score delta:
  Top 20% (>80th pct): +10
  Top 40% (60-80th):   +5
  Bottom 20% (<20th):  -8
  Bottom 40% (20-40th): -4

Cache: 1 hour TTL (factor ranks don't change bar-to-bar).
Fail-open. Uses yfinance batch download.
"""

import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_cache: Dict = {}
_cache_ts: Dict = {}
_TTL_RANK   = 3600    # factor ranks refresh hourly
_TTL_SCREEN = 7200    # full screen refresh every 2 hours

# ── Store: symbol → factor scores ─────────────────────────────────────────────
_factor_scores: Dict[str, Dict] = {}   # symbol → {momentum, rs, quality, reversal, composite, rank_pct}
_last_screen_ts: float = 0.0
_SPY_RETURN: float = 0.0


def _cached(key: str, ttl: float):
    v = _cache.get(key)
    if v is not None and _time.time() - _cache_ts.get(key, 0) < ttl:
        return v
    return None


def _store(key: str, val):
    _cache[key] = val
    _cache_ts[key] = _time.time()
    return val


# ── Factor computation helpers ────────────────────────────────────────────────

def _compute_momentum_20d(symbol: str) -> float:
    """20-day price return. Cached per symbol."""
    key = f"mom_{symbol}"
    cached = _cached(key, _TTL_RANK)
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        df = yf.download(symbol, period="25d", interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or len(df) < 5:
            return 0.0
        closes = df["Close"].dropna().values
        ret    = float((closes[-1] - closes[-min(21, len(closes))]) / max(closes[-min(21, len(closes))], 0.01))
        return _store(key, ret)
    except Exception:
        return 0.0


def _compute_spy_return_20d() -> float:
    """SPY 20-day return for beta-adjusted RS. Cached."""
    global _SPY_RETURN
    cached = _cached("spy_ret_20d", _TTL_RANK)
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        df = yf.download("SPY", period="25d", interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or len(df) < 5:
            return 0.0
        closes = df["Close"].dropna().values
        ret    = float((closes[-1] - closes[-min(21, len(closes))]) / max(closes[-min(21, len(closes))], 0.01))
        _SPY_RETURN = ret
        return _store("spy_ret_20d", ret)
    except Exception:
        return 0.0


def _compute_relative_strength(symbol_mom: float, spy_mom: float) -> float:
    """Alpha = stock_return - spy_return (simplified RS without beta)."""
    return symbol_mom - spy_mom


def _compute_eps_quality(symbol: str) -> float:
    """EPS growth quality score: -1 to +1. Cached 2 hours."""
    key = f"eps_{symbol}"
    cached = _cached(key, _TTL_SCREEN)
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        fin = t.quarterly_earnings
        if fin is None or fin.empty or len(fin) < 3:
            return _store(key, 0.0)
        eps = fin["Earnings"].values[-4:][::-1]  # oldest first
        pos_growth = sum(1 for i in range(1, len(eps)) if eps[i] > eps[i - 1])
        score = (pos_growth / (len(eps) - 1)) * 2 - 1.0   # -1 to +1
        return _store(key, float(score))
    except Exception:
        return _store(key, 0.0)


def _compute_5d_return(symbol: str) -> float:
    """5-day return for reversal guard."""
    key = f"ret5_{symbol}"
    cached = _cached(key, _TTL_RANK)
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        df = yf.download(symbol, period="8d", interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or len(df) < 3:
            return 0.0
        closes = df["Close"].dropna().values
        ret    = float((closes[-1] - closes[-min(6, len(closes))]) / max(closes[-min(6, len(closes))], 0.01))
        return _store(key, ret)
    except Exception:
        return 0.0


# ── Batch screener ────────────────────────────────────────────────────────────

def run_factor_screen(watchlist: List[str]) -> None:
    """
    Compute all 4 factors for every symbol in watchlist.
    Rank within the group → store percentile in _factor_scores.
    Run once per hour (or on first call).
    """
    global _last_screen_ts, _factor_scores
    now = _time.time()
    if now - _last_screen_ts < _TTL_RANK and _factor_scores:
        return   # already fresh

    spy_mom = _compute_spy_return_20d()
    raw: Dict[str, Dict] = {}

    for sym in watchlist[:60]:   # cap at 60 to avoid rate limits
        try:
            mom  = _compute_momentum_20d(sym)
            rs   = _compute_relative_strength(mom, spy_mom)
            qual = _compute_eps_quality(sym)
            ret5 = _compute_5d_return(sym)
            raw[sym] = {"momentum": mom, "rs": rs, "quality": qual, "ret5": ret5}
        except Exception:
            raw[sym] = {"momentum": 0.0, "rs": 0.0, "quality": 0.0, "ret5": 0.0}

    if not raw:
        return

    # Rank each factor across the universe
    syms   = list(raw.keys())
    mom_v  = np.array([raw[s]["momentum"] for s in syms])
    rs_v   = np.array([raw[s]["rs"]       for s in syms])
    qual_v = np.array([raw[s]["quality"]  for s in syms])

    def rank_pct(arr: np.ndarray) -> np.ndarray:
        """Return percentile rank 0-1 for each element."""
        from scipy.stats import rankdata
        return rankdata(arr) / len(arr)

    try:
        from scipy.stats import rankdata
        mom_r  = rank_pct(mom_v)
        rs_r   = rank_pct(rs_v)
        qual_r = rank_pct(qual_v)
    except Exception:
        # Fallback: simple sort-based rank
        def _fallback_rank(arr):
            order = np.argsort(np.argsort(arr))
            return order / max(len(order) - 1, 1)
        mom_r  = _fallback_rank(mom_v)
        rs_r   = _fallback_rank(rs_v)
        qual_r = _fallback_rank(qual_v)

    # Composite: momentum 40%, RS 35%, quality 25%
    composite = 0.40 * mom_r + 0.35 * rs_r + 0.25 * qual_r

    try:
        comp_r = rank_pct(composite)
    except Exception:
        comp_r = composite

    for i, sym in enumerate(syms):
        _factor_scores[sym] = {
            "momentum":    float(mom_r[i]),
            "rs":          float(rs_r[i]),
            "quality":     float(qual_r[i]),
            "composite":   float(composite[i]),
            "rank_pct":    float(comp_r[i]),
            "ret5":        raw[sym]["ret5"],
        }

    _last_screen_ts = now
    logger.info(f"factor_alpha: screened {len(_factor_scores)} symbols")


# ── Score function ─────────────────────────────────────────────────────────────

def get_factor_score(symbol: str, direction: str, watchlist: Optional[List[str]] = None) -> Tuple[float, str]:
    """
    Return (score_delta, reason) based on factor rank.
    Triggers a fresh screen if stale. Fail-open.
    """
    try:
        sym = symbol.upper()

        # Try to refresh the screen (non-blocking if fresh)
        if watchlist:
            run_factor_screen(watchlist)

        fs = _factor_scores.get(sym)
        if fs is None:
            # Score just this one symbol quickly
            spy_mom = _compute_spy_return_20d()
            mom     = _compute_momentum_20d(sym)
            rs      = _compute_relative_strength(mom, spy_mom)
            qual    = _compute_eps_quality(sym)
            ret5    = _compute_5d_return(sym)
            _factor_scores[sym] = {
                "momentum": 0.5, "rs": 0.5, "quality": 0.5,
                "composite": (0.40 * mom + 0.35 * rs + 0.25 * qual),
                "rank_pct": 0.5, "ret5": ret5,
            }
            fs = _factor_scores[sym]

        rank = fs["rank_pct"]
        ret5 = fs["ret5"]
        is_long = direction.upper() in ("LONG", "BUY")

        # Reversal guard — override score if too extended
        if is_long and ret5 > 0.12:
            return -4.0, f"factor:reversal_risk 5d={ret5:.0%}"
        if not is_long and ret5 < -0.12:
            return -4.0, f"factor:reversal_risk 5d={ret5:.0%}"

        # Factor rank → score
        if is_long:
            if rank >= 0.80:
                return +10.0, f"factor:top_quintile rank={rank:.0%}"
            elif rank >= 0.60:
                return +5.0,  f"factor:above_avg rank={rank:.0%}"
            elif rank <= 0.20:
                return -8.0,  f"factor:bottom_quintile rank={rank:.0%}"
            elif rank <= 0.40:
                return -4.0,  f"factor:below_avg rank={rank:.0%}"
        else:   # SHORT
            # For shorts: low-rank stocks are the best shorts
            short_rank = 1.0 - rank
            if short_rank >= 0.80:
                return +10.0, f"factor:top_short rank={rank:.0%}"
            elif short_rank >= 0.60:
                return +5.0,  f"factor:good_short rank={rank:.0%}"
            elif short_rank <= 0.20:
                return -8.0,  f"factor:weak_short rank={rank:.0%}"
            elif short_rank <= 0.40:
                return -4.0,  f"factor:below_avg_short rank={rank:.0%}"

        return 0.0, f"factor:neutral rank={rank:.0%}"
    except Exception as e:
        logger.debug(f"factor_alpha.get_factor_score suppressed: {e}")
        return 0.0, "factor:error"
