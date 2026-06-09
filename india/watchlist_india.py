"""
watchlist_india.py — NSE liquid stock watchlist
Top 80 Nifty/Nifty Next 50 stocks, filtered daily by liquidity.
"""
import logging
import os
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("watchlist_india")
IST = ZoneInfo("Asia/Kolkata")

# ── Curated ULTRA-LIQUID core (research: KingEdge hit 57% WR on these) ────────
# Tightest spreads + deepest volume on NSE = least slippage, best fill quality.
# Default trading universe — frequent trading on thin names LOSES after costs.
_LIQUID_CORE: List[str] = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "SBIN", "AXISBANK", "BHARTIARTL", "LT", "ITC",
    "KOTAKBANK", "TATAMOTORS",
]

# Liquid-only mode ON by default (per nse_research.py — accuracy was highest and
# cost drag lowest on the most liquid names). Set INDIA_LIQUID_ONLY=False to use
# the full 80-symbol universe.
LIQUID_ONLY_MODE = os.getenv("INDIA_LIQUID_ONLY", "True") != "False"

# ── Core NSE liquid universe (80 symbols) ────────────────────────────────────
_CORE_WATCHLIST: List[str] = [
    # Nifty 50 heavyweights
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "SBIN", "BHARTIARTL", "KOTAKBANK", "ITC",
    "AXISBANK", "LT", "BAJFINANCE", "ASIANPAINT", "MARUTI",
    "TITAN", "WIPRO", "ULTRACEMCO", "NESTLEIND", "TECHM",
    "SUNPHARMA", "POWERGRID", "NTPC", "TATASTEEL",
    "JSWSTEEL", "ONGC", "COALINDIA", "BPCL", "HCLTECH",
    "BAJAJFINSV", "GRASIM", "DIVISLAB", "DRREDDY", "CIPLA",
    "EICHERMOT", "HEROMOTOCO", "M&M", "TATACONSUM", "BRITANNIA",
    "APOLLOHOSP", "INDUSINDBK", "HDFCLIFE", "SBILIFE", "PIDILITIND",
    # High-momentum mid-cap (consistently liquid)
    "ADANIPORTS", "ADANIENT", "DABUR", "HINDPETRO", "IOC",
    # New additions — 30 high-liquidity momentum stocks
    "TATAMOTORS", "BAJAJ-AUTO", "SHREECEM", "M&MFIN", "PFC",
    "RECLTD", "TATAPOWER", "ADANIGREEN", "ADANITRANS", "SIEMENS",
    "ABB", "HAVELLS", "VEDL", "HINDALCO", "UPL",
    "DMART", "NAUKRI", "ZOMATO", "IRCTC", "BANKBARODA",
    "CANBK", "PNB", "FEDERALBNK", "MUTHOOTFIN", "CHOLAFIN",
    "IDFCFIRSTB", "LTIM", "MPHASIS", "PAYTM", "HDFC",
]

# ── Momentum tier classification ─────────────────────────────────────────────
# T1: 25 highest-liquidity / index heavyweights — preferred for entry
# T2: 30 solid mid/large-cap with good momentum — secondary preference
# T3: 25 remaining — use only when T1+T2 slots full or strong setup present
_MOMENTUM_TIER: Dict[str, str] = {
    # T1 — 25 symbols
    "RELIANCE":   "T1", "TCS":        "T1", "HDFCBANK":   "T1",
    "INFY":       "T1", "ICICIBANK":  "T1", "SBIN":       "T1",
    "AXISBANK":   "T1", "KOTAKBANK":  "T1", "BAJFINANCE": "T1",
    "BHARTIARTL": "T1", "LT":         "T1", "ITC":        "T1",
    "TATASTEEL":  "T1", "TATAMOTORS": "T1", "ADANIENT":   "T1",
    "ADANIPORTS": "T1", "MARUTI":     "T1", "HCLTECH":    "T1",
    "WIPRO":      "T1", "JSWSTEEL":   "T1", "M&M":        "T1",
    "BAJAJFINSV": "T1", "ZOMATO":     "T1", "INDUSINDBK": "T1",
    "NTPC":       "T1",
    # T2 — 30 symbols
    "TECHM":      "T2", "SUNPHARMA":  "T2", "ONGC":       "T2",
    "BPCL":       "T2", "COALINDIA":  "T2", "HINDUNILVR": "T2",
    "ASIANPAINT": "T2", "TITAN":      "T2", "DRREDDY":    "T2",
    "CIPLA":      "T2", "GRASIM":     "T2", "ULTRACEMCO": "T2",
    "HDFC":       "T2", "TATAPOWER":  "T2", "VEDL":       "T2",
    "HINDALCO":   "T2", "BAJAJ-AUTO": "T2", "EICHERMOT":  "T2",
    "HEROMOTOCO": "T2", "BANKBARODA": "T2", "PNB":        "T2",
    "CANBK":      "T2", "IRCTC":      "T2", "NAUKRI":     "T2",
    "POWERGRID":  "T2", "IOC":        "T2", "HINDPETRO":  "T2",
    "ADANIGREEN": "T2", "LTIM":       "T2", "IDFCFIRSTB": "T2",
    # T3 — 25 symbols
    "NESTLEIND":  "T3", "DIVISLAB":   "T3", "BRITANNIA":  "T3",
    "APOLLOHOSP": "T3", "HDFCLIFE":   "T3", "SBILIFE":    "T3",
    "PIDILITIND": "T3", "DABUR":      "T3", "TATACONSUM": "T3",
    "SHREECEM":   "T3", "M&MFIN":     "T3", "PFC":        "T3",
    "RECLTD":     "T3", "ADANITRANS": "T3", "SIEMENS":    "T3",
    "ABB":        "T3", "HAVELLS":    "T3", "UPL":        "T3",
    "DMART":      "T3", "FEDERALBNK": "T3", "MUTHOOTFIN": "T3",
    "CHOLAFIN":   "T3", "MPHASIS":    "T3", "PAYTM":      "T3",
}

# ── Sector mapping ────────────────────────────────────────────────────────────
_SECTOR_MAP: Dict[str, str] = {
    "RELIANCE":   "Energy",      "ONGC":       "Energy",
    "BPCL":       "Energy",      "IOC":        "Energy",       "HINDPETRO":  "Energy",
    "TATAPOWER":  "Power",       "ADANIGREEN": "Power",        "PFC":        "Power",
    "RECLTD":     "Power",       "ADANITRANS": "Power",
    "TCS":        "IT",          "INFY":       "IT",           "WIPRO":      "IT",
    "HCLTECH":    "IT",          "TECHM":      "IT",           "LTIM":       "IT",
    "MPHASIS":    "IT",
    "HDFCBANK":   "Banking",     "ICICIBANK":  "Banking",      "SBIN":       "Banking",
    "KOTAKBANK":  "Banking",     "AXISBANK":   "Banking",      "INDUSINDBK": "Banking",
    "BANKBARODA": "Banking",     "PNB":        "Banking",      "CANBK":      "Banking",
    "FEDERALBNK": "Banking",     "IDFCFIRSTB": "Banking",      "PAYTM":      "Banking",
    "BAJFINANCE": "Finance",     "BAJAJFINSV": "Finance",      "HDFCLIFE":   "Finance",
    "SBILIFE":    "Finance",     "HDFC":       "Finance",      "M&MFIN":     "Finance",
    "MUTHOOTFIN": "Finance",     "CHOLAFIN":   "Finance",
    "LT":         "Infra",       "ADANIPORTS": "Infra",        "POWERGRID":  "Infra",
    "SIEMENS":    "Infra",       "ABB":        "Infra",        "HAVELLS":    "Infra",
    "NTPC":       "Power",       "COALINDIA":  "Mining",
    "TATASTEEL":  "Metal",       "JSWSTEEL":   "Metal",        "VEDL":       "Metal",
    "HINDALCO":   "Metal",
    "MARUTI":     "Auto",        "EICHERMOT":  "Auto",
    "HEROMOTOCO": "Auto",        "BAJAJ-AUTO": "Auto",         "M&M":        "Auto",
    "TATAMOTORS": "Auto",
    "SUNPHARMA":  "Pharma",      "DRREDDY":    "Pharma",       "CIPLA":      "Pharma",
    "DIVISLAB":   "Pharma",      "APOLLOHOSP": "Healthcare",
    "HINDUNILVR": "FMCG",        "ITC":        "FMCG",         "NESTLEIND":  "FMCG",
    "BRITANNIA":  "FMCG",        "TATACONSUM": "FMCG",         "DABUR":      "FMCG",
    "DMART":      "Retail",      "NAUKRI":     "Internet",     "ZOMATO":     "Internet",
    "IRCTC":      "Travel",
    "ASIANPAINT": "Paints",      "PIDILITIND": "Chemicals",    "UPL":        "Agro",
    "BHARTIARTL": "Telecom",
    "TITAN":      "Consumer",    "ULTRACEMCO": "Cement",       "GRASIM":     "Cement",
    "SHREECEM":   "Cement",
    "ADANIENT":   "Conglomerate",
}


def get_priority_watchlist() -> List[str]:
    """
    Return full watchlist ordered by tier: T1 first, then T2, then T3.
    Within each tier the order matches _CORE_WATCHLIST insertion order.
    """
    t1 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) == "T1"]
    t2 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) == "T2"]
    t3 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) == "T3"]
    return t1 + t2 + t3


def get_tier(symbol: str) -> str:
    """Return momentum tier ('T1', 'T2', or 'T3') for a symbol."""
    return _MOMENTUM_TIER.get(symbol.upper(), "T3")


def get_active_watchlist(dhan_client=None) -> List[str]:
    """
    Return watchlist filtered by minimum liquidity, ordered by priority tier.
    Falls back to full priority list if data unavailable (fail-open).
    """
    # Liquid-only mode: trade ONLY the ultra-liquid core (best accuracy, least
    # slippage — see nse_research.py). This is the default profitable-defense.
    if LIQUID_ONLY_MODE:
        logger.info(f"Watchlist: LIQUID-ONLY mode — {len(_LIQUID_CORE)} ultra-liquid names")
        return list(_LIQUID_CORE)

    ordered = get_priority_watchlist()

    if dhan_client is None:
        logger.info(f"Watchlist (no LTP filter): {len(ordered)} symbols")
        return ordered

    try:
        from data_fetch_dhan import get_multiple_ltp
        ltp_map = get_multiple_ltp(_CORE_WATCHLIST, dhan_client)

        # Filter: price > Rs.50, has LTP data — preserve priority order
        filtered = [s for s in ordered if ltp_map.get(s, 0) >= 50]

        if len(filtered) < 10:
            logger.warning("LTP filter too aggressive — using full watchlist")
            return ordered

        logger.info(f"Watchlist: {len(filtered)} symbols (filtered from {len(ordered)})")
        return filtered
    except Exception as e:
        logger.warning(f"Watchlist filter failed: {e} — using full list")
        return ordered


def get_sector(symbol: str) -> str:
    return _SECTOR_MAP.get(symbol.upper(), "Unknown")


def get_nifty_symbol() -> str:
    """NSE symbol for Nifty 50 index (used as market benchmark)."""
    return "NIFTY 50"
