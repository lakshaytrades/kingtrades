"""
watchlist_india.py — NSE liquid stock watchlist
Top 50 Nifty/Nifty Next 50 stocks, filtered daily by liquidity.
"""
import logging
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("watchlist_india")
IST = ZoneInfo("Asia/Kolkata")

# ── Core NSE liquid universe ─────────────────────────────────────────────────
_CORE_WATCHLIST: List[str] = [
    # Nifty 50 heavyweights
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "SBIN", "BHARTIARTL", "KOTAKBANK", "ITC",
    "AXISBANK", "LT", "BAJFINANCE", "ASIANPAINT", "MARUTI",
    "TITAN", "WIPRO", "ULTRACEMCO", "NESTLEIND", "TECHM",
    "SUNPHARMA", "POWERGRID", "NTPC", "TATAMOTORS", "TATASTEEL",
    "JSWSTEEL", "ONGC", "COALINDIA", "BPCL", "HCLTECH",
    "BAJAJFINSV", "GRASIM", "DIVISLAB", "DRREDDY", "CIPLA",
    "EICHERMOT", "HEROMOTOCO", "M&M", "TATACONSUM", "BRITANNIA",
    "APOLLOHOSP", "INDUSINDBK", "HDFCLIFE", "SBILIFE", "PIDILITIND",
    # High-momentum mid-cap (consistently liquid)
    "ADANIPORTS", "ADANIENT", "DABUR", "HINDPETRO", "IOC",
]

# ── Sector mapping ────────────────────────────────────────────────────────────
_SECTOR_MAP: Dict[str, str] = {
    "RELIANCE":   "Energy",      "ONGC":       "Energy",
    "BPCL":       "Energy",      "IOC":        "Energy",       "HINDPETRO":  "Energy",
    "TCS":        "IT",          "INFY":       "IT",           "WIPRO":      "IT",
    "HCLTECH":    "IT",          "TECHM":      "IT",
    "HDFCBANK":   "Banking",     "ICICIBANK":  "Banking",      "SBIN":       "Banking",
    "KOTAKBANK":  "Banking",     "AXISBANK":   "Banking",      "INDUSINDBK": "Banking",
    "BAJFINANCE": "Finance",     "BAJAJFINSV": "Finance",      "HDFCLIFE":   "Finance",
    "SBILIFE":    "Finance",
    "LT":         "Infra",       "ADANIPORTS": "Infra",        "POWERGRID":  "Infra",
    "NTPC":       "Power",       "COALINDIA":  "Mining",
    "TATASTEEL":  "Metal",       "JSWSTEEL":   "Metal",
    "TATAMOTORS": "Auto",        "MARUTI":     "Auto",         "EICHERMOT":  "Auto",
    "HEROMOTOCO": "Auto",        "BAJAJ-AUTO": "Auto",         "M&M":        "Auto",
    "SUNPHARMA":  "Pharma",      "DRREDDY":    "Pharma",       "CIPLA":      "Pharma",
    "DIVISLAB":   "Pharma",      "APOLLOHOSP": "Healthcare",
    "HINDUNILVR": "FMCG",        "ITC":        "FMCG",         "NESTLEIND":  "FMCG",
    "BRITANNIA":  "FMCG",        "TATACONSUM": "FMCG",         "DABUR":      "FMCG",
    "ASIANPAINT": "Paints",      "PIDILITIND": "Chemicals",
    "BHARTIARTL": "Telecom",
    "TITAN":      "Consumer",    "ULTRACEMCO": "Cement",       "GRASIM":     "Cement",
    "ADANIENT":   "Conglomerate",
}


def get_active_watchlist(dhan_client=None) -> List[str]:
    """
    Return watchlist filtered by minimum liquidity.
    Falls back to full core list if data unavailable (fail-open).
    """
    if dhan_client is None:
        logger.info(f"Watchlist (no LTP filter): {len(_CORE_WATCHLIST)} symbols")
        return list(_CORE_WATCHLIST)

    try:
        from data_fetch_dhan import get_multiple_ltp
        ltp_map = get_multiple_ltp(_CORE_WATCHLIST, dhan_client)

        # Filter: price > ₹50, has LTP data
        filtered = [s for s in _CORE_WATCHLIST
                    if ltp_map.get(s, 0) >= 50]

        if len(filtered) < 10:
            logger.warning("LTP filter too aggressive — using full watchlist")
            return list(_CORE_WATCHLIST)

        logger.info(f"Watchlist: {len(filtered)} symbols (filtered from {len(_CORE_WATCHLIST)})")
        return filtered
    except Exception as e:
        logger.warning(f"Watchlist filter failed: {e} — using full list")
        return list(_CORE_WATCHLIST)


def get_sector(symbol: str) -> str:
    return _SECTOR_MAP.get(symbol.upper(), "Unknown")


def get_nifty_symbol() -> str:
    """yfinance symbol for Nifty 50 index (used as market benchmark)."""
    return "^NSEI"
