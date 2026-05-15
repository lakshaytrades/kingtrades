"""
block_deal_scanner.py — NSE Momentum Groww AI Bot
Scans NSE block and bulk deals for large institutional activity.
Sends Telegram alerts when 10Cr+ deals appear — retail momentum follows within minutes.
"""

import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Set
from zoneinfo import ZoneInfo

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/market-data/block-deal",
    "Connection": "keep-alive",
}

FII_KEYWORDS = {
    "FII", "FPI", "FOREIGN", "MAURITIUS", "SINGAPORE", "CAYMAN",
    "NOMURA", "GOLDMAN", "MORGAN", "MERRILL", "CITIGROUP", "JPMORGAN",
    "DEUTSCHE", "HSBC", "BARCLAYS", "CREDIT SUISSE", "SOCIETE",
}

CACHE_TTL_SECONDS = 5 * 60  # 5 minutes
SESSION_TTL_SECONDS = 30 * 60  # re-warm NSE cookies every 30 minutes


@dataclass
class DealSignal:
    symbol: str
    deal_type: str    # "BLOCK" or "BULK"
    side: str         # "BUY" or "SELL"
    quantity: int
    price: float
    value_cr: float   # in crores
    client_name: str
    timestamp: datetime
    is_fii: bool      # True if client name contains FII/FPI keywords


class BlockDealScanner:

    def __init__(self):
        self._session: Optional[requests.Session] = None
        self._session_created_at: float = 0.0

        self._block_cache: List[DealSignal] = []
        self._bulk_cache: List[DealSignal] = []
        self._block_fetched_at: float = 0.0
        self._bulk_fetched_at: float = 0.0

        # Deal keys already alerted today — reset at midnight IST
        self._alerted_today: Set[str] = set()
        self._alert_date: Optional[str] = None

    # ------------------------------------------------------------------
    # SESSION
    # ------------------------------------------------------------------

    def _get_nse_session(self) -> requests.Session:
        """
        Returns a requests.Session seeded with NSE cookies.
        NSE rejects API calls with 403 unless you first hit the homepage —
        that GET sets session cookies the API endpoints require.
        Session is reused for SESSION_TTL_SECONDS before being refreshed.
        """
        now = _time.monotonic()
        if self._session and (now - self._session_created_at) < SESSION_TTL_SECONDS:
            return self._session

        session = requests.Session()
        session.headers.update(NSE_HEADERS)
        try:
            resp = session.get("https://www.nseindia.com/", timeout=10)
            resp.raise_for_status()
            logger.debug(
                f"[{format_ist_timestamp()}] NSE session established, "
                f"cookies: {list(session.cookies.keys())}"
            )
        except Exception as exc:
            logger.warning(f"[{format_ist_timestamp()}] NSE session warmup failed: {exc}")

        self._session = session
        self._session_created_at = now
        return session

    # ------------------------------------------------------------------
    # FII DETECTION
    # ------------------------------------------------------------------

    def _is_fii_client(self, name: str) -> bool:
        upper = name.upper()
        return any(kw in upper for kw in FII_KEYWORDS)

    # ------------------------------------------------------------------
    # PARSING HELPERS
    # ------------------------------------------------------------------

    def _parse_side(self, raw: str) -> str:
        raw_upper = raw.strip().upper()
        if raw_upper in ("B", "BUY", "P", "PURCHASE"):
            return "BUY"
        return "SELL"

    def _safe_float(self, val) -> float:
        try:
            return float(str(val).replace(",", "").strip())
        except (ValueError, TypeError):
            return 0.0

    def _safe_int(self, val) -> int:
        try:
            return int(float(str(val).replace(",", "").strip()))
        except (ValueError, TypeError):
            return 0

    def _build_deal(self, row: dict, deal_type: str) -> Optional[DealSignal]:
        """
        Parse a raw NSE API row into a DealSignal.
        Block-deal fields:  symbol, clientName, buySell, quantity, price, tradeDate
        Bulk-deal fields:   symbol, clientName, buySell, quantityTraded, tradePrice, tradeDate
        Field names differ between block and bulk — handled via fallback keys.
        """
        try:
            symbol = str(row.get("symbol", row.get("Symbol", ""))).strip().upper()
            if not symbol:
                return None

            client_name = str(
                row.get("clientName", row.get("ClientName", row.get("client", "")))
            ).strip()

            buy_sell_raw = str(
                row.get("buySell", row.get("BuySell", row.get("buyOrSell", "S")))
            ).strip()
            side = self._parse_side(buy_sell_raw)

            if deal_type == "BLOCK":
                qty = self._safe_int(row.get("quantity", row.get("Quantity", 0)))
                price = self._safe_float(row.get("price", row.get("Price", 0)))
            else:  # BULK
                qty = self._safe_int(
                    row.get("quantityTraded", row.get("quantity", row.get("Quantity", 0)))
                )
                price = self._safe_float(
                    row.get("tradePrice", row.get("price", row.get("Price", 0)))
                )

            if qty <= 0 or price <= 0:
                return None

            value_cr = round((qty * price) / 1e7, 2)

            trade_date_raw = row.get("tradeDate", row.get("date", ""))
            try:
                ts = datetime.strptime(
                    str(trade_date_raw).strip()[:10], "%d-%b-%Y"
                ).replace(tzinfo=IST)
            except Exception:
                ts = get_current_ist_time()

            return DealSignal(
                symbol=symbol,
                deal_type=deal_type,
                side=side,
                quantity=qty,
                price=price,
                value_cr=value_cr,
                client_name=client_name,
                timestamp=ts,
                is_fii=self._is_fii_client(client_name),
            )
        except Exception as exc:
            logger.debug(
                f"[{format_ist_timestamp()}] Failed to parse {deal_type} row: {exc} | row={row}"
            )
            return None

    # ------------------------------------------------------------------
    # API FETCH
    # ------------------------------------------------------------------

    def fetch_block_deals(self) -> List[DealSignal]:
        now = _time.monotonic()
        if self._block_cache and (now - self._block_fetched_at) < CACHE_TTL_SECONDS:
            return self._block_cache

        results: List[DealSignal] = []
        try:
            session = self._get_nse_session()
            resp = session.get("https://www.nseindia.com/api/block-deal", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", data.get("BlockDeals", []))
            for row in rows:
                deal = self._build_deal(row, "BLOCK")
                if deal:
                    results.append(deal)
            logger.info(f"[{format_ist_timestamp()}] Fetched {len(results)} block deals from NSE")
        except Exception as exc:
            logger.warning(f"[{format_ist_timestamp()}] Block deal fetch failed: {exc}")
            # Invalidate session so next call re-warms cookies
            self._session = None

        self._block_cache = results
        self._block_fetched_at = now
        return results

    def fetch_bulk_deals(self) -> List[DealSignal]:
        now = _time.monotonic()
        if self._bulk_cache and (now - self._bulk_fetched_at) < CACHE_TTL_SECONDS:
            return self._bulk_cache

        results: List[DealSignal] = []
        try:
            session = self._get_nse_session()
            resp = session.get(
                "https://www.nseindia.com/api/bulk-deal-day-data", timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", data.get("BulkDeals", []))
            for row in rows:
                deal = self._build_deal(row, "BULK")
                if deal:
                    results.append(deal)
            logger.info(f"[{format_ist_timestamp()}] Fetched {len(results)} bulk deals from NSE")
        except Exception as exc:
            logger.warning(f"[{format_ist_timestamp()}] Bulk deal fetch failed: {exc}")
            self._session = None

        self._bulk_cache = results
        self._bulk_fetched_at = now
        return results

    # ------------------------------------------------------------------
    # FILTERS
    # ------------------------------------------------------------------

    def get_high_conviction_buys(self, min_value_cr: float = 10.0) -> List[DealSignal]:
        all_deals = self.fetch_block_deals() + self.fetch_bulk_deals()
        return [d for d in all_deals if d.side == "BUY" and d.value_cr >= min_value_cr]

    def get_sell_pressure_stocks(self, min_value_cr: float = 10.0) -> List[DealSignal]:
        all_deals = self.fetch_block_deals() + self.fetch_bulk_deals()
        return [d for d in all_deals if d.side == "SELL" and d.value_cr >= min_value_cr]

    # ------------------------------------------------------------------
    # TELEGRAM
    # ------------------------------------------------------------------

    def format_telegram_alert(self, deal: DealSignal) -> str:
        deal_emoji = "🏦" if deal.deal_type == "BLOCK" else "📦"
        fii_tag = " (FII)" if deal.is_fii else ""
        signal_text = (
            "Strong institutional accumulation"
            if deal.side == "BUY"
            else "Heavy institutional distribution"
        )
        return (
            f"{deal_emoji} {deal.deal_type} DEAL ALERT\n"
            f"Symbol: {deal.symbol} ({deal.side})\n"
            f"Value: ₹{deal.value_cr:.1f} Cr | Qty: {deal.quantity:,}\n"
            f"Price: ₹{deal.price:,.2f}\n"
            f"Client: {deal.client_name}{fii_tag}\n"
            f"Signal: {signal_text}"
        )

    def _send_telegram(self, text: str) -> None:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.debug("Telegram not configured — skipping block deal alert")
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=8,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning(f"[{format_ist_timestamp()}] Telegram send failed: {exc}")

    def _reset_alert_log_if_new_day(self) -> None:
        today_str = get_current_ist_time().strftime("%Y-%m-%d")
        if self._alert_date != today_str:
            self._alerted_today.clear()
            self._alert_date = today_str

    def _deal_key(self, deal: DealSignal) -> str:
        return f"{deal.symbol}|{deal.deal_type}|{deal.side}|{deal.client_name}|{deal.value_cr}"

    def scan_and_alert(self) -> List[str]:
        """
        Scans all deals >= 10 Cr, sends Telegram for any not yet alerted today,
        and returns the list of symbols that have active buy signals.
        """
        self._reset_alert_log_if_new_day()
        all_deals = self.fetch_block_deals() + self.fetch_bulk_deals()
        buy_symbols: List[str] = []

        for deal in all_deals:
            if deal.value_cr < 10.0:
                continue

            key = self._deal_key(deal)
            if key not in self._alerted_today:
                self._alerted_today.add(key)
                msg = self.format_telegram_alert(deal)
                self._send_telegram(msg)
                logger.info(
                    f"[{format_ist_timestamp()}] Deal alert sent: "
                    f"{deal.symbol} {deal.side} ₹{deal.value_cr:.1f}Cr"
                )

            if deal.side == "BUY" and deal.symbol not in buy_symbols:
                buy_symbols.append(deal.symbol)

        return buy_symbols


# ------------------------------------------------------------------
# MODULE-LEVEL SINGLETON
# ------------------------------------------------------------------

_scanner: Optional[BlockDealScanner] = None


def get_block_deal_scanner() -> BlockDealScanner:
    global _scanner
    if _scanner is None:
        _scanner = BlockDealScanner()
    return _scanner


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scanner = get_block_deal_scanner()
    print(f"Block deals: {len(scanner.fetch_block_deals())}")
    print(f"Bulk deals:  {len(scanner.fetch_bulk_deals())}")
    buys = scanner.get_high_conviction_buys(min_value_cr=10.0)
    print(f"High conviction buys (>=10Cr): {len(buys)}")
    for d in buys[:3]:
        print(scanner.format_telegram_alert(d))
        print()
    symbols = scanner.scan_and_alert()
    print(f"Buy signal symbols today: {symbols}")
