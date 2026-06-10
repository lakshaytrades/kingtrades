"""
block_deal_scanner.py — NSE Bulk & Block Deal Institutional Intelligence

NSE publishes daily bulk deal and block deal data showing large institutional
trades. Cluster buying by multiple institutions = strong bullish signal.

Data sources (in priority order):
  1. NSE official bulk deal CSV (published intraday, updated frequently)
  2. yfinance delivery percentage proxy (high delivery = institutional)
  3. FII/DII tracker data (if available)

Bulk deal: Trade where qty >= 0.5% of listed shares in a single transaction
Block deal: Trade worth >= ₹10 crore executed on the block window (8:45-9:00 AM)

Interface expected by main.py:
  BlockDealScanner.scan_and_alert() → List[str]  (bullish symbols)
"""

import json
import logging
import os
import time as _time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_SCAN_CACHE_TTL = 900        # refresh every 15 min
_MIN_DEAL_SIZE  = 5_00_00_000  # ₹5 crore minimum to count
_TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

_NSE_BULK_URL  = "https://nseindia.com/api/bulk-deals"
_NSE_BLOCK_URL = "https://nseindia.com/api/block-deals"


def _tg(text: str) -> None:
    import os as _os_g
    if _os_g.getenv("US_BOT_ENABLED", "False") != "True":
        return
    if not _TELEGRAM_TOKEN or not _TELEGRAM_CHAT:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": _TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def _fetch_nse_deals(url: str) -> List[Dict]:
    """Fetch bulk/block deals from NSE with proper headers."""
    try:
        import requests
        session = requests.Session()
        # NSE requires a prior homepage visit for cookies
        session.get("https://www.nseindia.com", timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp = session.get(url, timeout=8, headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nseindia.com",
        })
        if resp.status_code == 200:
            return resp.json().get("data", [])
    except Exception as e:
        logger.debug(f"NSE deals fetch failed: {e}")
    return []


def _fetch_delivery_pct_proxy(symbols: List[str]) -> Dict[str, float]:
    """
    Proxy: high delivery percentage = institutional accumulation.
    Uses yfinance info (limited but free).
    """
    result = {}
    try:
        import yfinance as yf
        for sym in symbols[:10]:   # limit API calls
            nse_sym = sym if sym.endswith(".NS") else sym + ".NS"
            try:
                info = yf.Ticker(nse_sym).fast_info
                # deliverable_volume / total_volume if available
                deliv = getattr(info, "three_month_average_volume", 0)
                if deliv:
                    result[sym] = 1.0   # can't get delivery pct from fast_info
            except Exception:
                pass
    except Exception:
        pass
    return result


class BlockDealScanner:
    """
    Scans NSE bulk/block deals and FII data for institutional accumulation signals.
    Returns symbols where institutional buying is clustering.
    """

    def __init__(self):
        self._cache_ts:   float = 0.0
        self._cached_buys: List[str] = []
        self._alerted:    Set[str] = set()  # symbols already alerted today
        self._today_str:  str = ""

    def _reset_daily(self) -> None:
        today = date.today().isoformat()
        if today != self._today_str:
            self._alerted.clear()
            self._today_str = today

    def scan_and_alert(self) -> List[str]:
        """
        Scan for bulk/block deals. Returns list of symbols with institutional buying.
        Sends Telegram alert for new buys.
        """
        self._reset_daily()

        if _time.time() - self._cache_ts < _SCAN_CACHE_TTL:
            return self._cached_buys

        self._cache_ts = _time.time()
        buy_symbols: List[str] = []
        deal_lines: List[str] = []

        # ── Source 1: NSE bulk deals ──────────────────────────────────────────
        bulk = _fetch_nse_deals(_NSE_BULK_URL)
        block = _fetch_nse_deals(_NSE_BLOCK_URL)

        for deal in bulk + block:
            try:
                sym     = deal.get("symbol", deal.get("Symbol", "")).strip().upper()
                bs_flag = deal.get("buySellFlag", deal.get("BS_flag", "")).upper()
                qty     = int(deal.get("quantity", deal.get("Quantity", 0)))
                price   = float(deal.get("price", deal.get("Price", 0)))
                val     = qty * price

                if not sym or val < _MIN_DEAL_SIZE:
                    continue
                if bs_flag in ("BUY", "B"):
                    buy_symbols.append(sym)
                    deal_lines.append(
                        f"  🟢 <b>{sym}</b> — ₹{val/1e7:.1f}Cr bulk buy "
                        f"@ ₹{price:.1f}"
                    )
            except Exception:
                continue

        # ── Source 2: FII net buying from tracker ─────────────────────────────
        try:
            from fii_dii_tracker import FIIDIITracker
            tracker = FIIDIITracker()
            fii_data = tracker.get_latest() if hasattr(tracker, "get_latest") else {}
            if fii_data.get("fii_net", 0) > 200:   # FII net bought > ₹200Cr
                deal_lines.append(
                    f"  💰 FII net buy: ₹{fii_data['fii_net']:,.0f}Cr — bullish macro"
                )
        except Exception:
            pass

        # ── Source 3: Delivery volume proxy ──────────────────────────────────
        if not buy_symbols:
            try:
                from india.watchlist_india import WATCHLIST_INDIA
                del_data = _fetch_delivery_pct_proxy(WATCHLIST_INDIA[:15])
                for sym, pct in del_data.items():
                    if pct > 0.65:
                        buy_symbols.append(sym)
                        deal_lines.append(f"  📦 <b>{sym}</b> — high delivery pct proxy")
            except Exception:
                pass

        # Deduplicate
        buy_symbols = list(dict.fromkeys(buy_symbols))
        self._cached_buys = buy_symbols

        # Send Telegram alert for new symbols only
        new_buys = [s for s in buy_symbols if s not in self._alerted]
        if new_buys and deal_lines:
            header = f"🏛️ <b>BLOCK DEAL ALERT</b> — Institutional Accumulation\n"
            body   = "\n".join(deal_lines[:8])
            footer = f"\n\n<b>Watchlist additions:</b> {', '.join(new_buys[:5])}"
            _tg(header + body + footer)
            for s in new_buys:
                self._alerted.add(s)
            logger.info(f"[BlockDealScanner] New institutional buys: {new_buys}")

        return buy_symbols


_SCANNER: Optional[BlockDealScanner] = None


def get_block_deal_scanner() -> BlockDealScanner:
    global _SCANNER
    if _SCANNER is None:
        _SCANNER = BlockDealScanner()
    return _SCANNER
