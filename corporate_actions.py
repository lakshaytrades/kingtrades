"""
corporate_actions.py — NSE Momentum Groww AI Bot
Corporate Actions Filter — Ex-Dividend, Bonus, Split Detection

Purpose: Avoid trading stocks near corporate action ex-dates.

WHY THIS MATTERS (18yr lesson):
  - Ex-dividend: Stock drops by dividend amount on ex-date → false bearish signal
  - Bonus issue: Price adjusts sharply → invalid technical levels
  - Stock split: All price-based indicators reset → unreliable signals
  - Rights issue: Uncertainty / volatility before ex-date
  Buffer rule: Skip stocks within 2 trading days of any ex-date.

Source: NSE public corporate actions API (no auth required, browser headers)
Cache: Refreshed once daily, stored in data/corp_actions_cache.json
Fallback: Empty actions (safe — no false positives)
"""

import json
import logging
import requests
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils import get_current_ist_date

logger = logging.getLogger(__name__)

CORP_CACHE_FILE = Path("data/corp_actions_cache.json")

# NSE API — corporate actions for equities (public, no login)
NSE_CORP_API = (
    "https://www.nseindia.com/api/corporates-corporateActions"
    "?index=equities&from_date={from_date}&to_date={to_date}"
)

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
}

# Action types that affect price significantly
PRICE_IMPACT_ACTIONS = {
    "DIVIDEND", "INTERIM DIVIDEND", "FINAL DIVIDEND",
    "BONUS", "SPLIT", "RIGHTS", "BUYBACK",
    "AMALGAMATION", "DEMERGER", "FACE VALUE CHANGE",
}

# Buffer: skip stocks within this many days of ex-date
CORP_ACTION_BUFFER_DAYS = 2


class CorporateActionsFilter:
    """
    No-op stub — NSE corporate actions disabled for US market migration.
    All methods return safe/empty values so the rest of the bot is unaffected.
    """

    def __init__(self, buffer_days: int = CORP_ACTION_BUFFER_DAYS):
        self.buffer_days = buffer_days
        self._actions: Dict[str, List[dict]] = {}
        self._cache_date: Optional[date] = None

    # ── CACHE ──────────────────────────────────────────────────

    # ── PUBLIC API (all stubs — NSE disabled for US market) ────

    def _refresh(self) -> bool:
        return True

    def has_upcoming_action(
        self, symbol: str, buffer_days: Optional[int] = None
    ) -> Tuple[bool, str]:
        return False, ""

    def is_safe_to_trade(self, symbol: str) -> Tuple[bool, str]:
        return True, ""

    def force_refresh(self) -> bool:
        return True

    def get_upcoming_events_today(self) -> List[dict]:
        return []



# ── SINGLETON ──────────────────────────────────────────────────────────────

_corp_filter_instance: Optional[CorporateActionsFilter] = None


def get_corp_filter() -> CorporateActionsFilter:
    """Return singleton CorporateActionsFilter instance."""
    global _corp_filter_instance
    if _corp_filter_instance is None:
        _corp_filter_instance = CorporateActionsFilter()
    return _corp_filter_instance


def is_safe_from_corp_actions(symbol: str) -> Tuple[bool, str]:
    """Quick helper: (safe, reason) for corporate action check."""
    return get_corp_filter().is_safe_to_trade(symbol)


# ── SELF-TEST ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Corporate Actions Filter Test ===")
    cf = CorporateActionsFilter()
    print(f"Stocks with upcoming events: {len(cf._actions)}")

    # Show today's ex-dates
    today_events = cf.get_upcoming_events_today()
    if today_events:
        print(f"\nToday's ex-dates ({len(today_events)} stocks):")
        for ev in today_events[:10]:
            print(f"  {ev['symbol']:20s}: {ev['purpose']}")
    else:
        print("\nNo ex-dates today.")

    # Test specific stocks
    test_stocks = ["RELIANCE", "TCS", "INFY", "HDFCBANK"]
    print("\nSpot checks:")
    for sym in test_stocks:
        safe, reason = cf.is_safe_to_trade(sym)
        status = "✅ Safe" if safe else f"⚠️ SKIP — {reason}"
        print(f"  {sym:20s}: {status}")
