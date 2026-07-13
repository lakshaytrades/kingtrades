"""
corporate_events_india.py — NSE Corporate Events Calendar (God Mode)
Tracks earnings, dividends, bulk deals. Avoids trading around events.
Data: NSE public API. Fail-open.
"""
import logging
import time as _time
import requests
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("corp_events")
IST = ZoneInfo("Asia/Kolkata")
_events_cache: dict = {}
_bulk_cache: dict = {}
_TTL = 1800.0


def _nse_session() -> requests.Session:
    """Create a requests Session with NSE cookie priming."""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get("https://www.nseindia.com", timeout=8)
    except Exception:
        pass
    return s


def _fetch_events() -> List[Dict]:
    """
    Fetch from NSE event-calendar API with cookie priming.
    Parses: symbol, company, purpose, date.
    Fail-open: returns [].
    """
    now = _time.monotonic()
    if _events_cache.get("ts", 0) > now - _TTL:
        return _events_cache.get("data", [])

    events: List[Dict] = []
    try:
        s = _nse_session()
        resp = s.get(
            "https://www.nseindia.com/api/event-calendar",
            headers={"Referer": "https://www.nseindia.com/"},
            timeout=12,
        )
        raw = resp.json()
        today_str = datetime.now(IST).strftime("%d-%b-%Y").upper()

        for item in raw if isinstance(raw, list) else []:
            try:
                symbol = (item.get("symbol") or "").strip().upper()
                company = item.get("companyName") or item.get("company") or ""
                purpose = (item.get("purpose") or "").upper()
                date_str = (item.get("date") or item.get("bm_date") or "").upper()

                # Normalise purpose to known categories
                event_type = "OTHER"
                if "RESULT" in purpose or "FINANCIAL" in purpose or "QUARTERLY" in purpose:
                    event_type = "RESULTS"
                elif "DIVIDEND" in purpose:
                    event_type = "DIVIDEND"
                elif "AGM" in purpose or "ANNUAL GENERAL" in purpose:
                    event_type = "AGM"
                elif "BOARD" in purpose:
                    event_type = "BOARD_MEETING"

                events.append({
                    "symbol": symbol,
                    "company": company,
                    "purpose": event_type,
                    "date": date_str,
                    "today": date_str == today_str,
                })
            except Exception:
                continue

        _events_cache["data"] = events
        _events_cache["ts"] = now
    except Exception as e:
        logger.debug(f"_fetch_events: {e}")

    return events


def get_events_for_symbol(symbol: str) -> List[Dict]:
    """Filter today's events for the given symbol."""
    sym = symbol.upper().strip()
    events = _fetch_events()
    return [e for e in events if e.get("symbol") == sym and e.get("today")]


def should_avoid_trading(symbol: str) -> Tuple[bool, str]:
    """
    Returns (True, reason) if symbol has RESULTS or AGM today (too risky).
    Otherwise (False, "").
    """
    today_events = get_events_for_symbol(symbol)
    for ev in today_events:
        if ev.get("purpose") in ("RESULTS", "AGM"):
            return True, "earnings_today"
    return False, ""


def get_event_score_modifier(symbol: str, direction: str) -> Tuple[float, str]:
    """
    - RESULTS today → -5.0 (earnings risk regardless of direction)
    - DIVIDEND today → +3.0 for LONG (price support), neutral for SHORT
    - No event → 0.0
    """
    today_events = get_events_for_symbol(symbol)
    if not today_events:
        return 0.0, ""

    for ev in today_events:
        purpose = ev.get("purpose", "")
        if purpose == "RESULTS":
            return -5.0, "earnings_risk"
        if purpose == "DIVIDEND":
            if direction == "LONG":
                return 3.0, "dividend_support"
            return 0.0, ""

    return 0.0, ""


def get_bulk_deal_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Fetch bulk/block deals from NSE snapshot API.
    Large buy aligned with LONG → +5; large sell against → -4.
    Fail-open: (0.0, "").
    """
    now = _time.monotonic()
    sym = symbol.upper().strip()

    if _bulk_cache.get("ts", 0) > now - _TTL:
        deals = _bulk_cache.get("data", [])
    else:
        deals = []
        try:
            s = _nse_session()
            resp = s.get(
                "https://www.nseindia.com/api/snapshot-capital-market-homePageData",
                headers={"Referer": "https://www.nseindia.com/"},
                timeout=12,
            )
            raw = resp.json()
            bulk_data = raw.get("bulkDeal", []) if isinstance(raw, dict) else []
            for item in bulk_data if isinstance(bulk_data, list) else []:
                try:
                    deals.append({
                        "symbol": (item.get("symbol") or "").upper().strip(),
                        "qty":    float(item.get("quantity") or 0),
                        "type":   (item.get("clientType") or item.get("buySell") or "").upper(),
                    })
                except Exception:
                    continue
            _bulk_cache["data"] = deals
            _bulk_cache["ts"] = now
        except Exception as e:
            logger.debug(f"get_bulk_deal_signal fetch: {e}")
            return 0.0, ""

    sym_deals = [d for d in deals if d.get("symbol") == sym]
    if not sym_deals:
        return 0.0, ""

    total_buy = sum(d["qty"] for d in sym_deals if "B" in d.get("type", ""))
    total_sell = sum(d["qty"] for d in sym_deals if "S" in d.get("type", ""))

    threshold = 100_000  # shares — "large" bulk deal

    if total_buy > threshold and direction == "LONG":
        return 5.0, f"BULK_BUY {int(total_buy):,}sh"
    if total_sell > threshold and direction == "SHORT":
        return 5.0, f"BULK_SELL {int(total_sell):,}sh"
    if total_sell > threshold and direction == "LONG":
        return -4.0, f"BULK_SELL_AGAINST {int(total_sell):,}sh"
    if total_buy > threshold and direction == "SHORT":
        return -4.0, f"BULK_BUY_AGAINST {int(total_buy):,}sh"

    return 0.0, ""
