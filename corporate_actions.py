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
    Tracks NSE corporate actions and blocks trades near ex-dates.

    Prevents:
    - False bearish signals on ex-dividend days (price drops by dividend)
    - Misleading technical levels around bonus/split price adjustments
    - Unexpected volatility from rights/buyback corporate events
    """

    def __init__(self, buffer_days: int = CORP_ACTION_BUFFER_DAYS):
        self.buffer_days = buffer_days
        self._actions: Dict[str, List[dict]] = {}   # symbol → [action, ...]
        self._cache_date: Optional[date] = None
        self._load_cache()

    # ── CACHE ──────────────────────────────────────────────────

    def _load_cache(self):
        """Load corporate actions from disk if today's cache exists."""
        try:
            if CORP_CACHE_FILE.exists():
                with open(CORP_CACHE_FILE) as f:
                    data = json.load(f)
                cached_date = date.fromisoformat(data.get("date", "2000-01-01"))
                if cached_date == get_current_ist_date():
                    self._actions = data.get("actions", {})
                    self._cache_date = cached_date
                    stocks_with_actions = len(self._actions)
                    logger.info(
                        f"Corp actions loaded from cache: "
                        f"{stocks_with_actions} stocks with upcoming events"
                    )
                    return
        except Exception as e:
            logger.debug(f"Corp actions cache load error: {e}")

        self._refresh()

    # ── NSE FETCH ──────────────────────────────────────────────

    def _refresh(self) -> bool:
        """
        Fetch corporate actions from NSE for the next 7 days.
        Returns True on success, False on failure (uses empty dict fallback).
        """
        try:
            today = get_current_ist_date()
            # Include yesterday (ex-date may already be today's event)
            from_date = (today - timedelta(days=1)).strftime("%d-%m-%Y")
            to_date   = (today + timedelta(days=7)).strftime("%d-%m-%Y")

            session = requests.Session()
            # NSE requires session cookie from homepage first
            session.get(
                "https://www.nseindia.com",
                headers=NSE_HEADERS,
                timeout=10,
            )

            url  = NSE_CORP_API.format(from_date=from_date, to_date=to_date)
            resp = session.get(url, headers=NSE_HEADERS, timeout=15)
            resp.raise_for_status()

            raw_data = resp.json()
            self._parse_actions(raw_data)
            self._cache_date = today
            self._save_cache()

            logger.info(
                f"Corp actions refreshed: {len(self._actions)} stocks "
                f"have events in next 7 days"
            )
            return True

        except Exception as e:
            logger.warning(
                f"Corp actions fetch failed: {e} — "
                "assuming no corporate events (safe fallback)"
            )
            self._actions = {}
            self._cache_date = get_current_ist_date()
            self._save_cache()
            return False

    def _parse_actions(self, raw_data):
        """
        Parse NSE corporate actions JSON response.
        NSE returns a list or dict with 'data' key.
        """
        self._actions = {}
        records = raw_data if isinstance(raw_data, list) else raw_data.get("data", [])

        for record in records:
            symbol = (
                record.get("symbol") or record.get("sym") or ""
            ).upper().strip()
            if not symbol:
                continue

            purpose = (
                record.get("purpose") or record.get("subject") or ""
            ).upper().strip()

            # Check if this action type impacts price
            is_price_impact = any(
                keyword in purpose for keyword in PRICE_IMPACT_ACTIONS
            )
            if not is_price_impact:
                continue

            # Parse ex-date (NSE uses multiple formats)
            ex_date_str = (
                record.get("exDate") or record.get("ex_date") or
                record.get("exdate") or ""
            ).strip()

            if not ex_date_str:
                continue

            ex_date = self._parse_date(ex_date_str)
            if ex_date is None:
                continue

            if symbol not in self._actions:
                self._actions[symbol] = []

            self._actions[symbol].append({
                "ex_date":    str(ex_date),
                "purpose":    purpose,
                "series":     record.get("series", "EQ"),
                "face_value": record.get("faceVal", ""),
            })

    @staticmethod
    def _parse_date(date_str: str) -> Optional[date]:
        """Try multiple date formats used by NSE."""
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%b %d, %Y"):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _save_cache(self):
        """Persist corporate actions to disk cache."""
        try:
            CORP_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CORP_CACHE_FILE, "w") as f:
                json.dump(
                    {
                        "date":    str(self._cache_date),
                        "actions": self._actions,
                        "count":   len(self._actions),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.debug(f"Corp actions cache save error: {e}")

    # ── PUBLIC API ─────────────────────────────────────────────

    def has_upcoming_action(
        self, symbol: str, buffer_days: Optional[int] = None
    ) -> Tuple[bool, str]:
        """
        Check if a stock has a price-impacting corporate action within buffer_days.

        Args:
            symbol:      NSE symbol to check
            buffer_days: Days around ex-date to block (default: self.buffer_days)

        Returns:
            (has_action: bool, reason: str)
            has_action=True means DO NOT TRADE this stock.
        """
        today = get_current_ist_date()
        if self._cache_date != today:
            self._refresh()

        buf = buffer_days if buffer_days is not None else self.buffer_days
        actions = self._actions.get(symbol.upper(), [])

        if not actions:
            return False, ""

        for action in actions:
            ex_date = self._parse_date(action["ex_date"])
            if ex_date is None:
                continue
            days_diff = (ex_date - today).days
            # Block if within buffer (before OR on the ex-date)
            if -1 <= days_diff <= buf:
                purpose = action.get("purpose", "CORPORATE ACTION")
                return True, (
                    f"{purpose} ex-date {action['ex_date']} "
                    f"({'+' if days_diff >= 0 else ''}{days_diff} days)"
                )

        return False, ""

    def is_safe_to_trade(self, symbol: str) -> Tuple[bool, str]:
        """
        Convenience wrapper.

        Returns:
            (safe: bool, reason: str)
            safe=True → no corporate event blocking this stock.
        """
        has_action, reason = self.has_upcoming_action(symbol)
        return (not has_action), reason

    def get_upcoming_events_today(self) -> List[dict]:
        """Return all stocks with ex-dates today (for morning brief)."""
        today = get_current_ist_date()
        if self._cache_date != today:
            self._refresh()

        events = []
        for symbol, actions in self._actions.items():
            for action in actions:
                ex_date = self._parse_date(action["ex_date"])
                if ex_date == today:
                    events.append({
                        "symbol":  symbol,
                        "purpose": action["purpose"],
                        "ex_date": action["ex_date"],
                    })
        return events

    def force_refresh(self) -> bool:
        """Force refresh from NSE (ignore cache)."""
        return self._refresh()


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
