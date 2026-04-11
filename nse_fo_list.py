"""
nse_fo_list.py — NSE Momentum Groww AI Bot
NSE F&O Eligible Stocks List — Auto-fetching with Daily Cache

Purpose: Only NSE F&O segment stocks can be intraday shorted.
Equity-only stocks can ONLY be bought (no short selling intraday).
Attempting to short a non-F&O stock on Groww → order REJECTED.

Source: NSE public CSV at https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv
Cache: Refreshed daily, stored in data/fo_list_cache.json
Fallback: Built-in list covering ~95% of Nifty 500 F&O stocks
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional, Set

import requests

from utils import get_current_ist_date

logger = logging.getLogger(__name__)

# Cache location
FO_CACHE_FILE = Path("data/fo_list_cache.json")

# NSE public F&O lot-size CSV (no auth required — use browser headers)
NSE_FO_CSV_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
NSE_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# ── BUILT-IN FALLBACK LIST ─────────────────────────────────────────────────
# Covers NSE F&O eligible stocks (Nifty 50 + Nifty 100 + popular F&O names).
# Updated periodically. Refreshed from NSE API on successful fetch.
FALLBACK_FO_LIST: Set[str] = {
    # Nifty 50
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK",
    "ASIANPAINT", "MARUTI", "TITAN", "SUNPHARMA", "NESTLEIND",
    "ULTRACEMCO", "WIPRO", "HCLTECH", "BAJFINANCE", "TATAMOTORS",
    "BAJAJFINSV", "ONGC", "NTPC", "POWERGRID", "M&M", "COALINDIA",
    "ADANIENT", "ADANIPORTS", "JSWSTEEL", "TATASTEEL", "HINDALCO",
    "GRASIM", "EICHERMOT", "HEROMOTOCO", "BPCL", "CIPLA", "DRREDDY",
    "DIVISLAB", "HDFCLIFE", "SBICARD", "TECHM", "TATACONSUM",
    "BAJAJ-AUTO", "INDUSINDBK", "UPL", "SHREECEM", "DMART", "VEDL",
    # Nifty Next 50 / popular F&O
    "ZOMATO", "NYKAA", "PAYTM", "POLICYBZR",
    "BANKBARODA", "CANFINHOME", "CHOLAFIN", "CUB", "DLF", "FEDERALBNK",
    "GODREJCP", "GODREJPROP", "GRANULES", "HAL", "HAVELLS",
    "IDFCFIRSTB", "INDUSTOWER", "IRCTC", "JINDALSTEL", "JUBLFOOD",
    "LICI", "LUPIN", "MANAPPURAM", "MCDOWELL-N", "METROPOLIS",
    "MOTHERSON", "MPHASIS", "MRF", "MUTHOOTFIN", "NAUKRI",
    "OBEROIRLTY", "OFSS", "PEL", "PETRONET", "PFC", "PNB",
    "RECLTD", "SAIL", "SBILIFE", "SRF", "TRENT", "TVSMOTOR",
    "UBL", "UNIONBANK", "VBL", "VOLTAS", "ZYDUSLIFE",
    "ABB", "AMBUJACEM", "APOLLOHOSP", "AUROPHARMA", "BALKRISIND",
    "BANDHANBNK", "BERGEPAINT", "BEL", "BHEL", "BIOCON",
    "BOSCHLTD", "CANBK", "CONCOR", "DABUR", "DEEPAKNTR",
    "ESCORTS", "EXIDEIND", "GAIL", "GMRINFRA", "GNFC",
    "HDFCAMC", "HINDPETRO", "IBULHSGFIN", "ICICIPRULI",
    "IGL", "INDHOTEL", "IOC", "ITC", "JINDALSTEL",
    "LALPATHLAB", "LICHSGFIN", "LTIM", "LTTS",
    "MINDTREE", "MARICO", "NIACL", "NMDC", "PAGEIND",
    "PERSISTENT", "PIDILITIND", "PIIND", "RBLBANK",
    "SIEMENS", "STAR", "TATACHEM", "TATAPOWER",
    "TORNTPHARM", "TORNTPOWER", "WHIRLPOOL", "YESBANK",
    # Nifty Bank additional
    "AUBANK", "DCBBANK", "KARURVYSYA", "LAKSHVILAS",
}


class NSEFOList:
    """
    Manages NSE F&O eligible stocks list with auto daily refresh.

    On first use: fetches from NSE website → parses → caches to disk.
    Daily: checks cache date; re-fetches if stale.
    On failure: uses built-in fallback list (covers major F&O stocks).
    """

    def __init__(self):
        self._fo_symbols: Set[str] = set()
        self._cache_date: Optional[date] = None
        self._load_cache()

    # ── CACHE LOAD ─────────────────────────────────────────────

    def _load_cache(self):
        """Load F&O list from disk if today's cache exists."""
        try:
            if FO_CACHE_FILE.exists():
                with open(FO_CACHE_FILE) as f:
                    data = json.load(f)
                cached_date = date.fromisoformat(data.get("date", "2000-01-01"))
                if cached_date == get_current_ist_date():
                    self._fo_symbols = set(data.get("symbols", []))
                    self._cache_date = cached_date
                    logger.info(
                        f"F&O list loaded from cache: {len(self._fo_symbols)} stocks "
                        f"(date: {cached_date})"
                    )
                    return
        except Exception as e:
            logger.debug(f"F&O cache load error: {e}")

        # No valid cache → fetch fresh
        self._refresh()

    # ── NSE FETCH ──────────────────────────────────────────────

    def _refresh(self) -> bool:
        """
        Fetch fresh F&O list from NSE public CSV.
        Returns True on success, False if fallback used.
        """
        try:
            # NSE requires a session cookie from a preliminary GET
            session = requests.Session()
            session.get(
                "https://www.nseindia.com",
                headers=NSE_BASE_HEADERS,
                timeout=10,
            )

            resp = session.get(
                NSE_FO_CSV_URL,
                headers=NSE_BASE_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()

            symbols = self._parse_fo_csv(resp.text)
            if len(symbols) < 50:
                raise ValueError(
                    f"NSE CSV parsed only {len(symbols)} symbols — likely malformed"
                )

            self._fo_symbols = symbols
            self._cache_date = get_current_ist_date()
            self._save_cache()
            logger.info(
                f"F&O list refreshed from NSE: {len(self._fo_symbols)} eligible stocks"
            )
            return True

        except Exception as e:
            logger.warning(
                f"F&O list NSE fetch failed: {e} — using built-in fallback "
                f"({len(FALLBACK_FO_LIST)} stocks)"
            )
            self._fo_symbols = FALLBACK_FO_LIST.copy()
            self._cache_date = get_current_ist_date()
            # Save fallback to cache so we don't retry every call
            self._save_cache()
            return False

    def _parse_fo_csv(self, csv_text: str) -> Set[str]:
        """
        Parse NSE fo_mktlots.csv to extract stock symbols.
        CSV format: UNDERLYING,SYMBOL,... (header on row 1, data from row 2)
        """
        symbols: Set[str] = set()
        lines = csv_text.strip().split("\n")
        for line in lines[1:]:   # skip header
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            symbol = parts[1].strip().upper().replace('"', "")
            # Validate: non-empty, reasonable length, no spaces
            if symbol and symbol != "SYMBOL" and 1 <= len(symbol) <= 20 and " " not in symbol:
                symbols.add(symbol)
        return symbols

    def _save_cache(self):
        """Persist F&O list to disk cache."""
        try:
            FO_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(FO_CACHE_FILE, "w") as f:
                json.dump(
                    {
                        "date": str(self._cache_date),
                        "symbols": sorted(self._fo_symbols),
                        "count": len(self._fo_symbols),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.debug(f"F&O cache save error: {e}")

    # ── PUBLIC API ─────────────────────────────────────────────

    def is_fo_eligible(self, symbol: str) -> bool:
        """
        Check if a stock is F&O eligible (can be shorted intraday).

        Auto-refreshes if cache is stale (new trading day).
        Falls back to built-in list if NSE fetch fails.
        """
        today = get_current_ist_date()
        if self._cache_date != today:
            logger.info(f"F&O list stale ({self._cache_date}) — refreshing...")
            self._refresh()
        return symbol.upper() in self._fo_symbols

    def get_fo_symbols(self) -> Set[str]:
        """Return full set of F&O eligible symbols."""
        today = get_current_ist_date()
        if self._cache_date != today:
            self._refresh()
        return self._fo_symbols.copy()

    def force_refresh(self) -> bool:
        """Force refresh from NSE (ignore cache)."""
        return self._refresh()

    @property
    def count(self) -> int:
        return len(self._fo_symbols)


# ── SINGLETON ──────────────────────────────────────────────────────────────

_fo_list_instance: Optional[NSEFOList] = None


def get_fo_list() -> NSEFOList:
    """Return singleton NSEFOList instance."""
    global _fo_list_instance
    if _fo_list_instance is None:
        _fo_list_instance = NSEFOList()
    return _fo_list_instance


def is_fo_eligible(symbol: str) -> bool:
    """Quick helper: is this symbol F&O eligible (shortable)?"""
    return get_fo_list().is_fo_eligible(symbol)


# ── SELF-TEST ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== NSE F&O Eligibility Test ===")
    fo = NSEFOList()
    print(f"Total F&O eligible stocks: {fo.count}")
    test = ["RELIANCE", "HDFCBANK", "TCS", "RANDOMSTOCK123", "PAYTM", "ZOMATO"]
    for sym in test:
        eligible = fo.is_fo_eligible(sym)
        icon = "✅ F&O ELIGIBLE (can short)" if eligible else "❌ Equity only (BUY only)"
        print(f"  {sym:20s}: {icon}")
