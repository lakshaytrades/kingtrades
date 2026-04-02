"""
watchlist_manager.py — NSE Momentum Groww AI Bot
Dynamic NSE Stock Watchlist Management

Selects highest-momentum liquid NSE stocks each day.
- Uses user-defined list from .env OR auto-detects top movers
- Filters by min volume, price range, and liquidity
- Scores stocks by pre-market momentum and relative strength
"""

import logging
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time
from data_fetch_groww import GrowwDataFetcher

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Nifty 500 high-liquidity universe — expanded for best momentum candidates
LIQUID_UNIVERSE = [
    # Nifty 50 large caps
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL",
    "ITC", "KOTAKBANK", "LT", "WIPRO", "HCLTECH", "AXISBANK", "MARUTI",
    "SUNPHARMA", "TATAMOTORS", "BAJFINANCE", "ADANIENT", "ULTRACEMCO", "TITAN",
    "ASIANPAINT", "POWERGRID", "NTPC", "HINDUNILVR", "ONGC", "JSWSTEEL",
    "TATASTEEL", "INDUSINDBK", "BAJAJFINSV", "TECHM",
    # Mid-cap momentum names
    "ZOMATO", "PAYTM", "NYKAA", "DELHIVERY", "POLICYBZR",
    "IRFC", "RVNL", "IRCTC", "RAILVIKAS", "HUDCO",
    "ADANIPOWER", "ADANIGREEN", "ADANIPORTS", "ADANITRANS",
    "DIXON", "AMBER", "KAYNES", "MAPMYINDIA",
    "HFCL", "TEJASNET", "STERLITE",
    "TATAPOWER", "CESC", "TORNTPOWER",
    "DRREDDY", "CIPLA", "DIVISLAB", "AUROPHARMA",
    "BANKBARODA", "CANARABANK", "PNB", "UNIONBANK",
    "MUTHOOTFIN", "CHOLAFIN", "SHRIRAMFIN",
    "HINDALCO", "VEDL", "NATIONALUM",
    "PIDILITIND", "BERGEPAINT", "KANSAINER",
    "HAVELLS", "POLYCAB", "BATAINDIA",
    "PVR", "INOXLEISUR",
    "MOTHERSON", "SUNDRMFAST", "BALKRISIND",
    "PERSISTENT", "MPHASIS", "LTIM", "COFORGE",
]


class WatchlistManager:
    """
    Manages the daily trading watchlist with momentum scoring.
    """

    def __init__(
        self,
        fetcher: GrowwDataFetcher,
        custom_list: Optional[List[str]] = None,
        max_watchlist_size: int = 25,
        min_price: float = 50.0,
        max_price: float = 10000.0,
        min_volume_lakh: float = 5.0,
    ):
        self.fetcher = fetcher
        self.custom_list = custom_list or []
        self.max_size = max_watchlist_size
        self.min_price = min_price
        self.max_price = max_price
        self.min_volume = min_volume_lakh * 100000  # Convert to units
        self._watchlist: List[str] = []
        self._scored: List[Dict] = []
        self._last_refresh: Optional[str] = None

    def get_watchlist(self) -> List[str]:
        """
        Get today's watchlist. Uses custom list if set, else auto-selects.
        """
        if self.custom_list:
            self._watchlist = self.custom_list
            return self._watchlist

        # Auto-select if not already built today
        today = get_current_ist_time().strftime("%Y-%m-%d")
        if self._last_refresh == today and self._watchlist:
            return self._watchlist

        self._watchlist = self._build_dynamic_watchlist()
        self._last_refresh = today
        return self._watchlist

    def _build_dynamic_watchlist(self) -> List[str]:
        """
        Score and rank liquid universe. Returns top momentum stocks.
        Called once at market open.
        """
        logger.info(
            f"[{format_ist_timestamp()}] Building dynamic watchlist from "
            f"{len(LIQUID_UNIVERSE)} candidates..."
        )
        scored = []
        # Fetch quotes for universe in batches of 20
        for i in range(0, len(LIQUID_UNIVERSE), 20):
            batch = LIQUID_UNIVERSE[i:i+20]
            quotes = self.fetcher.get_multiple_quotes(batch)
            for symbol, q in quotes.items():
                try:
                    ltp = q.get("ltp", 0)
                    vol = q.get("volume", 0)
                    chg_pct = q.get("change_pct", 0)

                    # Basic filters
                    if ltp < self.min_price or ltp > self.max_price:
                        continue
                    if vol < self.min_volume:
                        continue

                    # Momentum score
                    momentum_score = (
                        abs(chg_pct) * 3 +          # Magnitude of move
                        (vol / self.min_volume) * 2  # Relative volume
                    )
                    scored.append({
                        "symbol": symbol,
                        "ltp": ltp,
                        "change_pct": chg_pct,
                        "volume": vol,
                        "momentum_score": momentum_score,
                    })
                except Exception:
                    continue

        # Sort by momentum score
        scored.sort(key=lambda x: x["momentum_score"], reverse=True)
        self._scored = scored
        watchlist = [s["symbol"] for s in scored[:self.max_size]]

        logger.info(
            f"[{format_ist_timestamp()}] Watchlist ready: {len(watchlist)} stocks | "
            f"Top: {', '.join(watchlist[:5])}"
        )
        return watchlist

    def get_top_movers(self, n: int = 5) -> List[Dict]:
        """Get top n momentum movers from today's watchlist."""
        return self._scored[:n]

    def add_symbol(self, symbol: str):
        """Manually add a symbol to today's watchlist."""
        if symbol not in self._watchlist:
            self._watchlist.insert(0, symbol)
            logger.info(f"[{format_ist_timestamp()}] Added {symbol} to watchlist")

    def remove_symbol(self, symbol: str):
        """Remove symbol from today's watchlist."""
        if symbol in self._watchlist:
            self._watchlist.remove(symbol)
            logger.info(f"[{format_ist_timestamp()}] Removed {symbol} from watchlist")

    def get_status(self) -> str:
        wl = self.get_watchlist()
        return (
            f"📋 Watchlist ({len(wl)} stocks)\n"
            f"Last refresh: {self._last_refresh or 'Never'}\n"
            f"Stocks: {', '.join(wl[:10])}{'...' if len(wl) > 10 else ''}"
        )
