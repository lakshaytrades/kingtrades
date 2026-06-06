"""
watchlist_india.py — NSE liquid stock watchlist
80 Nifty/Nifty Next 50 + high-momentum stocks, filtered daily by liquidity.
Tier classification: T1 (highest), T2 (high), T3 (good).
"""
import logging
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("watchlist_india")
IST = ZoneInfo("Asia/Kolkata")

# ── Core NSE liquid universe (80 symbols) ────────────────────────────────────
_CORE_WATCHLIST: List[str] = [
    # Nifty 50 heavyweights (original 50)
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "SBIN", "BHARTIARTL", "KOTAKBANK", "ITC",
    "AXISBANK", "LT", "BAJFINANCE", "ASIANPAINT", "MARUTI",
    "TITAN", "WIPRO", "ULTRACEMCO", "NESTLEIND", "TECHM",
    "SUNPHARMA", "POWERGRID", "NTPC", "TATASTEEL",
    "JSWSTEEL", "ONGC", "COALINDIA", "BPCL", "HCLTECH",
    "BAJAJFINSV", "GRASIM", "DIVISLAB", "DRREDDY", "CIPLA",
    "EICHERMOT", "HEROMOTOCO", "M&M", "TATACONSUM", "BRITANNIA",
    "APOLLOHOSP", "INDUSINDBK", "HDFCLIFE", "SBILIFE", "PIDILITIND",
    # High-momentum mid-cap (original, consistently liquid)
    "ADANIPORTS", "ADANIENT", "DABUR", "HINDPETRO", "IOC",
    # ── New 30 additions ──────────────────────────────────────────────────────
    # Auto & Industrial
    "TATAMOTORS", "BAJAJ-AUTO", "SIEMENS", "ABB", "HAVELLS",
    # Cement & Infra
    "SHREECEM",
    # Finance & NBFC
    "HDFC", "M&MFIN", "PFC", "RECLTD", "MUTHOOTFIN", "CHOLAFIN", "IDFCFIRSTB",
    # Power & Green Energy
    "TATAPOWER", "ADANIGREEN", "ADANITRANS",
    # Metals & Mining
    "VEDL", "HINDALCO",
    # Agri & Chemicals
    "UPL",
    # Consumer & Retail
    "DMART",
    # New-age Tech & Internet
    "NAUKRI", "PAYTM", "ZOMATO", "IRCTC",
    # PSU Banks
    "BANKBARODA", "CANBK", "PNB", "FEDERALBNK",
    # IT mid-cap
    "LTIM", "MPHASIS",
]

# ── Momentum tier classification ─────────────────────────────────────────────
# T1 = top 25 by volume/momentum (always scan first)
# T2 = next 30 high-liquidity
# T3 = rest (good but scan last)
_MOMENTUM_TIER: Dict[str, str] = {
    # T1 — highest liquidity & momentum (top 25)
    "RELIANCE":   "T1",
    "TCS":        "T1",
    "HDFCBANK":   "T1",
    "INFY":       "T1",
    "ICICIBANK":  "T1",
    "SBIN":       "T1",
    "AXISBANK":   "T1",
    "KOTAKBANK":  "T1",
    "BAJFINANCE": "T1",
    "BHARTIARTL": "T1",
    "LT":         "T1",
    "ITC":        "T1",
    "TATASTEEL":  "T1",
    "TATAMOTORS": "T1",
    "ADANIENT":   "T1",
    "ADANIPORTS": "T1",
    "MARUTI":     "T1",
    "HCLTECH":    "T1",
    "WIPRO":      "T1",
    "JSWSTEEL":   "T1",
    "M&M":        "T1",
    "BAJAJFINSV": "T1",
    "ZOMATO":     "T1",
    "INDUSINDBK": "T1",
    "NTPC":       "T1",
    # T2 — high liquidity (next 30)
    "TECHM":      "T2",
    "SUNPHARMA":  "T2",
    "ONGC":       "T2",
    "BPCL":       "T2",
    "COALINDIA":  "T2",
    "HINDUNILVR": "T2",
    "ASIANPAINT": "T2",
    "TITAN":      "T2",
    "DRREDDY":    "T2",
    "CIPLA":      "T2",
    "GRASIM":     "T2",
    "ULTRACEMCO": "T2",
    "HDFC":       "T2",
    "TATAPOWER":  "T2",
    "VEDL":       "T2",
    "HINDALCO":   "T2",
    "BAJAJ-AUTO": "T2",
    "EICHERMOT":  "T2",
    "HEROMOTOCO": "T2",
    "BANKBARODA": "T2",
    "PNB":        "T2",
    "CANBK":      "T2",
    "IRCTC":      "T2",
    "NAUKRI":     "T2",
    "POWERGRID":  "T2",
    "IOC":        "T2",
    "HINDPETRO":  "T2",
    "ADANIGREEN": "T2",
    "LTIM":       "T2",
    "IDFCFIRSTB": "T2",
    # T3 — good liquidity (remaining)
    "DIVISLAB":   "T3",
    "APOLLOHOSP": "T3",
    "HDFCLIFE":   "T3",
    "SBILIFE":    "T3",
    "NESTLEIND":  "T3",
    "BRITANNIA":  "T3",
    "TATACONSUM": "T3",
    "PIDILITIND": "T3",
    "DABUR":      "T3",
    "SHREECEM":   "T3",
    "M&MFIN":     "T3",
    "PFC":        "T3",
    "RECLTD":     "T3",
    "ADANITRANS": "T3",
    "UPL":        "T3",
    "DMART":      "T3",
    "PAYTM":      "T3",
    "FEDERALBNK": "T3",
    "MUTHOOTFIN": "T3",
    "CHOLAFIN":   "T3",
    "MPHASIS":    "T3",
    "SIEMENS":    "T3",
    "ABB":        "T3",
    "HAVELLS":    "T3",
}

# ── Sector mapping (all 80 symbols) ──────────────────────────────────────────
_SECTOR_MAP: Dict[str, str] = {
    # Energy
    "RELIANCE":   "Energy",      "ONGC":       "Energy",
    "BPCL":       "Energy",      "IOC":        "Energy",      "HINDPETRO":  "Energy",
    # IT
    "TCS":        "IT",          "INFY":       "IT",          "WIPRO":      "IT",
    "HCLTECH":    "IT",          "TECHM":      "IT",          "LTIM":       "IT",
    "MPHASIS":    "IT",
    # Banking
    "HDFCBANK":   "Banking",     "ICICIBANK":  "Banking",     "SBIN":       "Banking",
    "KOTAKBANK":  "Banking",     "AXISBANK":   "Banking",     "INDUSINDBK": "Banking",
    "BANKBARODA": "Banking",     "CANBK":      "Banking",     "PNB":        "Banking",
    "FEDERALBNK": "Banking",     "IDFCFIRSTB": "Banking",
    # Finance & NBFC
    "BAJFINANCE": "Finance",     "BAJAJFINSV": "Finance",     "HDFCLIFE":   "Finance",
    "SBILIFE":    "Finance",     "HDFC":       "Finance",     "M&MFIN":     "Finance",
    "PFC":        "Finance",     "RECLTD":     "Finance",     "MUTHOOTFIN": "Finance",
    "CHOLAFIN":   "Finance",
    # Infra & Conglomerate
    "LT":         "Infra",       "ADANIPORTS": "Infra",       "POWERGRID":  "Infra",
    "ADANIENT":   "Conglomerate",
    # Power & Green Energy
    "NTPC":       "Power",       "TATAPOWER":  "Power",
    "ADANIGREEN": "Power",       "ADANITRANS": "Power",
    # Mining & Metals
    "COALINDIA":  "Mining",      "TATASTEEL":  "Metal",       "JSWSTEEL":   "Metal",
    "VEDL":       "Metal",       "HINDALCO":   "Metal",
    # Auto
    "MARUTI":     "Auto",        "EICHERMOT":  "Auto",        "HEROMOTOCO": "Auto",
    "BAJAJ-AUTO": "Auto",        "M&M":        "Auto",        "TATAMOTORS": "Auto",
    # Pharma & Healthcare
    "SUNPHARMA":  "Pharma",      "DRREDDY":    "Pharma",      "CIPLA":      "Pharma",
    "DIVISLAB":   "Pharma",      "APOLLOHOSP": "Healthcare",
    # FMCG
    "HINDUNILVR": "FMCG",        "ITC":        "FMCG",        "NESTLEIND":  "FMCG",
    "BRITANNIA":  "FMCG",        "TATACONSUM": "FMCG",        "DABUR":      "FMCG",
    # Paints, Chemicals & Consumer
    "ASIANPAINT": "Paints",      "PIDILITIND": "Chemicals",   "UPL":        "Agri",
    # Telecom
    "BHARTIARTL": "Telecom",
    # Consumer & Retail
    "TITAN":      "Consumer",    "DMART":      "Retail",
    # Cement
    "ULTRACEMCO": "Cement",      "GRASIM":     "Cement",      "SHREECEM":   "Cement",
    # Industrial & Capital Goods
    "SIEMENS":    "Industrial",  "ABB":        "Industrial",  "HAVELLS":    "Industrial",
    # New-age Tech & Internet
    "NAUKRI":     "Internet",    "PAYTM":      "Fintech",
    "ZOMATO":     "Internet",    "IRCTC":      "Internet",
}


def get_priority_watchlist() -> List[str]:
    """
    Return full watchlist ordered by momentum tier: T1 first, then T2, then T3.
    Within each tier, order is preserved from _CORE_WATCHLIST.
    """
    t1 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) == "T1"]
    t2 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) == "T2"]
    t3 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) == "T3"]
    return t1 + t2 + t3


def get_active_watchlist(dhan_client=None) -> List[str]:
    """
    Return watchlist filtered by minimum liquidity, ordered by priority tier.
    Falls back to full priority-ordered list if data unavailable (fail-open).
    """
    ordered = get_priority_watchlist()

    if dhan_client is None:
        logger.info(f"Watchlist (no LTP filter): {len(ordered)} symbols")
        return ordered

    try:
        from data_fetch_dhan import get_multiple_ltp
        ltp_map = get_multiple_ltp(ordered, dhan_client)

        # Filter: price > ₹50, has LTP data — preserve tier ordering
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


def get_tier(symbol: str) -> str:
    """Return momentum tier (T1/T2/T3) for a symbol, or 'T3' as default."""
    return _MOMENTUM_TIER.get(symbol.upper(), "T3")


def get_nifty_symbol() -> str:
    """yfinance symbol for Nifty 50 index (used as market benchmark)."""
    return "^NSEI"
