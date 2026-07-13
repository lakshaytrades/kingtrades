"""
nse_sector_momentum.py — NSE sector rotation and momentum scoring
Computes which sectors are outperforming Nifty today.
Data: yfinance sector ETFs / Nifty sector indices. Cache 30 min.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("nse_sector_momentum")
IST = ZoneInfo("Asia/Kolkata")

# ── Sector index tickers (yfinance) ──────────────────────────────────────────
_SECTOR_ETF_MAP: Dict[str, str] = {
    "^NIFTYBANK":  "Banking",
    "^CNXAUTO":    "Auto",
    "^CNXIT":      "IT",
    "^CNXPHARMA":  "Pharma",
    "^CNXFMCG":    "FMCG",
    "^CNXENERGY":  "Energy",
    "^CNXMETAL":   "Metal",
    "^CNXINFRA":   "Infra",
}

# Map sector names (from watchlist_india._SECTOR_MAP) to index tickers
_SECTOR_TO_ETF: Dict[str, str] = {
    "Banking":      "^NIFTYBANK",
    "IT":           "^CNXIT",
    "Auto":         "^CNXAUTO",
    "Pharma":       "^CNXPHARMA",
    "FMCG":         "^CNXFMCG",
    "Energy":       "^CNXENERGY",
    "Metal":        "^CNXMETAL",
    "Infra":        "^CNXINFRA",
    "Finance":      "^NIFTYBANK",   # proxy
    "Power":        "^CNXENERGY",   # proxy
    "Mining":       "^CNXMETAL",    # proxy
    "Cement":       "^CNXINFRA",    # proxy
}

# ── Cache: {date_str: {"returns": {sector: return_pct}, "fetched_at": datetime}} ──
_sector_returns_cache: Dict[str, dict] = {}
_CACHE_TTL_MINUTES = 30


def _get_today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _cache_is_fresh(date_str: str) -> bool:
    """Return True if cached data for today exists and is < 30 minutes old."""
    entry = _sector_returns_cache.get(date_str)
    if not entry:
        return False
    age = datetime.now(IST) - entry["fetched_at"]
    return age < timedelta(minutes=_CACHE_TTL_MINUTES)


def _get_sector_returns() -> Dict[str, float]:
    """
    Download all sector indices + Nifty 50, compute 1-day return vs Nifty.
    Returns {sector_name: relative_return_pct}.
    Fail-open: returns {} on any error.
    """
    today = _get_today_ist()

    if _cache_is_fresh(today):
        return _sector_returns_cache[today]["returns"]

    try:
        from data_fetch_upstox import get_all_sector_returns as _sector_returns
        sector_returns = _sector_returns()

        if sector_returns:
            _sector_returns_cache[today] = {
                "returns":    sector_returns,
                "fetched_at": datetime.now(IST),
            }
            logger.info(f"sector_returns refreshed: {sector_returns}")
            return sector_returns

    except Exception as e:
        logger.debug(f"sector_returns fetch failed: {e}")
    return {}


def get_sector_momentum_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Score sector momentum for a symbol in a given trade direction.

    Returns (score_delta, reason):
      +8  — sector outperforming Nifty by >0.5% and direction==LONG
      +8  — sector underperforming Nifty by >0.5% and direction==SHORT
      -4  — sector outperforming but direction==SHORT (fighting momentum)
      -4  — sector underperforming but direction==LONG
       0  — no data or sector unknown (fail-open)
    """
    try:
        from watchlist_india import _SECTOR_MAP  # type: ignore

        sector = _SECTOR_MAP.get(symbol.upper(), "Unknown")
        if sector == "Unknown":
            return (0.0, "")

        etf_ticker = _SECTOR_TO_ETF.get(sector)
        if not etf_ticker:
            # Sector exists but not tracked (e.g. Telecom, Healthcare, etc.)
            return (0.0, "")

        # Resolve sector name from ETF map (handles proxy mappings)
        tracked_sector = _SECTOR_ETF_MAP.get(etf_ticker, sector)

        returns = _get_sector_returns()
        if not returns:
            return (0.0, "")

        rel_return = returns.get(tracked_sector)
        if rel_return is None:
            return (0.0, "")

        outperforming = rel_return > 0.5    # sector beat Nifty by >0.5%
        underperforming = rel_return < -0.5  # sector lagged Nifty by >0.5%

        if outperforming and direction == "LONG":
            return (8.0, f"SECTOR_LONG_TAILWIND {sector} +{rel_return:.1f}% vs Nifty")
        if underperforming and direction == "SHORT":
            return (8.0, f"SECTOR_SHORT_TAILWIND {sector} {rel_return:.1f}% vs Nifty")
        if outperforming and direction == "SHORT":
            return (-4.0, f"SECTOR_HEADWIND_SHORT {sector} +{rel_return:.1f}% vs Nifty")
        if underperforming and direction == "LONG":
            return (-4.0, f"SECTOR_HEADWIND_LONG {sector} {rel_return:.1f}% vs Nifty")

        # Sector within ±0.5% of Nifty — neutral
        return (0.0, f"SECTOR_NEUTRAL {sector} {rel_return:+.1f}% vs Nifty")

    except Exception as e:
        logger.debug(f"get_sector_momentum_score {symbol}: {e}")
        return (0.0, "")
