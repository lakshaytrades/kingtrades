"""
momentum_factor_india.py — 12-1 Month Cross-Sectional Momentum Factor

Academic source: IIM Ahmedabad Fama-French-Momentum data library.
Key finding: The 12-1 month momentum factor returned 21.9% annually
in Indian equities (1994-2014) — HIGHER than the US (10-12%).

Why higher in India: Less efficient, slower institutional arbitrage,
retail-dominated orderbook causes slower price discovery.

This upgrades the existing 20-day CSM in institutional_strategies.py
to the academically-validated 252-21 trading day (12 months minus 1 month
skip) lookback, as used in the IIM-A data library.

Strategy:
  1. For each symbol in watchlist, compute:
     momentum = (price[t-21] / price[t-252]) - 1
     (return from 12 months ago to 1 month ago — skip last month to avoid reversal)
  2. Rank all symbols by momentum (cross-sectional percentile)
  3. Top quintile (>80th pct): +12 score boost for LONG signals
  4. Second quintile (60-80th pct): +6
  5. Bottom quintile (<20th pct): -10 for LONG (these are losers)
  6. For SHORT signals: inverted (bottom quintile gets +10)

Cache: Updated once per day in pre-market (data doesn't change intraday).
Data: Dhan daily OHLCV if available; yfinance .NS fallback.
"""
import json
import logging
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

logger = logging.getLogger("momentum_factor")
IST = ZoneInfo("Asia/Kolkata")

_CACHE_FILE = Path(__file__).parent.parent / "data" / "momentum_factor.json"
_factor_cache: Dict[str, float] = {}   # symbol → percentile rank 0-1
_cache_date: Optional[str] = None
_LOOKBACK_LONG  = 252   # 12 months of trading days
_LOOKBACK_SKIP  = 21    # skip last 1 month (reversal contamination)


def _compute_momentum_return(symbol: str, dhan_client=None) -> Optional[float]:
    """
    Compute the 12-1 month momentum return for a single symbol.
    Returns float (e.g., 0.25 = +25%) or None on failure.
    """
    try:
        # Try Dhan first
        if dhan_client is not None:
            try:
                from data_fetch_dhan import get_ohlcv
                df = get_ohlcv(symbol, interval="1d")
                if df is not None and len(df) >= _LOOKBACK_LONG:
                    closes = df["close"].values.astype(float)
                    p_start = closes[-_LOOKBACK_LONG]
                    p_end   = closes[-_LOOKBACK_SKIP]
                    if p_start > 0:
                        return float(p_end / p_start - 1)
            except Exception:
                pass

        # Fallback: yfinance
        import yfinance as yf
        ticker = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
        df = yf.download(ticker, period="14mo", interval="1d",
                         auto_adjust=True, progress=False, timeout=20)
        if df is None or len(df) < _LOOKBACK_LONG:
            return None
        closes = df["Close"].values.astype(float)
        p_start = closes[-min(_LOOKBACK_LONG, len(closes))]
        p_end   = closes[-min(_LOOKBACK_SKIP, len(closes))]
        if p_start <= 0:
            return None
        return float(p_end / p_start - 1)

    except Exception as e:
        logger.debug(f"momentum_return {symbol}: {e}")
        return None


def update_momentum_factor(watchlist: List[str], dhan_client=None) -> int:
    """
    Compute 12-1 month momentum percentile ranks for all watchlist symbols.
    Updates the factor cache. Returns number of symbols successfully ranked.
    Called once per day in pre-market (9:00-9:10 AM IST).
    """
    global _factor_cache, _cache_date

    today = datetime.now(IST).strftime("%Y-%m-%d")
    if _cache_date == today and _factor_cache:
        return len(_factor_cache)

    # Try loading from file first
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            if data.get("date") == today and data.get("ranks"):
                _factor_cache = data["ranks"]
                _cache_date   = today
                logger.info(f"Momentum factor loaded from cache: {len(_factor_cache)} symbols")
                return len(_factor_cache)
    except Exception:
        pass

    # Compute fresh
    returns: Dict[str, float] = {}
    for sym in watchlist:
        ret = _compute_momentum_return(sym, dhan_client)
        if ret is not None:
            returns[sym] = ret

    if len(returns) < 5:
        logger.warning(f"Momentum factor: only {len(returns)} symbols computed — too few")
        return 0

    # Compute percentile ranks
    values = list(returns.values())
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    ranks = {}
    for sym, ret in returns.items():
        idx = sorted_vals.index(ret)
        ranks[sym] = round(idx / max(n - 1, 1), 4)

    _factor_cache = ranks
    _cache_date   = today

    # Save to file
    try:
        _CACHE_FILE.parent.mkdir(exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            "date": today,
            "ranks": ranks,
            "computed_at": datetime.now(IST).strftime("%H:%M IST"),
        }, indent=2))
    except Exception:
        pass

    logger.info(
        f"Momentum factor updated: {len(ranks)} symbols | "
        f"top5: {sorted(ranks.items(), key=lambda x: -x[1])[:5]}"
    )
    return len(ranks)


def get_momentum_factor_score(symbol: str, direction: str) -> Tuple[int, str]:
    """
    Return (score_adj, reason) based on 12-1 month cross-sectional momentum.
    Fail-open: (0, "") if cache not populated or symbol not ranked.
    """
    try:
        if not _factor_cache:
            # Try loading from today's cache file
            try:
                today = datetime.now(IST).strftime("%Y-%m-%d")
                if _CACHE_FILE.exists():
                    data = json.loads(_CACHE_FILE.read_text())
                    if data.get("date") == today:
                        global _cache_date
                        _factor_cache.update(data.get("ranks", {}))
                        _cache_date = today
            except Exception:
                pass

        rank = _factor_cache.get(symbol.upper())
        if rank is None:
            return (0, "")

        # Score mapping (academically motivated — top quintile has the edge)
        if direction == "LONG":
            if rank >= 0.80:
                return (12, f"MOM12_TOP20% rank={rank:.0%}")
            if rank >= 0.60:
                return (6, f"MOM12_TOP40% rank={rank:.0%}")
            if rank <= 0.20:
                return (-10, f"MOM12_BOT20% rank={rank:.0%}")
            if rank <= 0.40:
                return (-4, f"MOM12_BOT40% rank={rank:.0%}")
        else:   # SHORT: inverse — short the losers, not the winners
            if rank <= 0.20:
                return (12, f"MOM12_BOT20%_SHORT rank={rank:.0%}")
            if rank <= 0.40:
                return (6, f"MOM12_BOT40%_SHORT rank={rank:.0%}")
            if rank >= 0.80:
                return (-10, f"MOM12_TOP20%_contra rank={rank:.0%}")
            if rank >= 0.60:
                return (-4, f"MOM12_TOP40%_contra rank={rank:.0%}")

        return (0, "")

    except Exception as e:
        logger.debug(f"get_momentum_factor_score {symbol}: {e}")
        return (0, "")


def get_factor_stats() -> str:
    """Return a brief description of the current factor state."""
    if not _factor_cache:
        return "Momentum factor not computed yet"
    n = len(_factor_cache)
    top5 = sorted(_factor_cache.items(), key=lambda x: -x[1])[:5]
    bot5 = sorted(_factor_cache.items(), key=lambda x: x[1])[:5]
    top_str = ", ".join(f"{s}({r:.0%})" for s, r in top5)
    bot_str = ", ".join(f"{s}({r:.0%})" for s, r in bot5)
    return (f"MOM12 factor: {n} symbols | "
            f"Top5: {top_str} | Bot5: {bot_str}")
