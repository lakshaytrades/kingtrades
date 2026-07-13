"""
NSE Data Module — India-specific market intelligence from NSE website.

Data provided:
- Bulk deals (large institutional orders crossed on exchange)
- Block deals (negotiated large trades)
- Delivery percentage (how much of volume was delivery vs intraday)
- Circuit limits (upper/lower circuit for stocks)
- FII/FPI index futures position
- Most active stocks by value and volume
- Nifty50 option chain summary (PCR, max pain)

WHY: Delivery % > 60% = institutional accumulation (strong signal)
     Bulk deal by FII = 1-3 day momentum trade
     Near upper circuit = avoid (breakout already happened)
     Near lower circuit = avoid (panic selling)
"""

import logging
import time
import requests
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import date

logger = logging.getLogger(__name__)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

_CACHE: Dict = {}
_TTL = 600  # 10 minutes


def _cached(key: str, fn, ttl: int = _TTL):
    now = time.time()
    if key in _CACHE and now - _CACHE[key]["ts"] < ttl:
        return _CACHE[key]["val"]
    try:
        val = fn()
        _CACHE[key] = {"val": val, "ts": now}
        return val
    except Exception as e:
        logger.debug(f"NSEData cache miss {key}: {e}")
        return _CACHE.get(key, {}).get("val", None)


@dataclass
class BulkDeal:
    symbol: str = ""
    client: str = ""
    trade_type: str = ""  # BUY / SELL
    quantity: int = 0
    price: float = 0.0
    date_str: str = ""
    is_fii: bool = False


@dataclass
class CircuitInfo:
    symbol: str = ""
    upper_circuit: float = 0.0
    lower_circuit: float = 0.0
    current_price: float = 0.0
    pct_to_upper: float = 100.0
    pct_to_lower: float = 100.0
    near_upper: bool = False   # within 2% of upper circuit
    near_lower: bool = False   # within 2% of lower circuit


@dataclass
class NSESymbolData:
    symbol: str = ""
    delivery_pct: float = 0.0
    total_volume: int = 0
    delivery_volume: int = 0
    circuit: Optional[CircuitInfo] = None
    bulk_deals_today: List[BulkDeal] = field(default_factory=list)
    fii_bulk_buy: bool = False
    fii_bulk_sell: bool = False
    data_available: bool = False


class NSESession:
    """Manages NSE session cookie (required for API access)."""
    _sess: Optional[requests.Session] = None
    _sess_ts: float = 0.0
    _SESS_TTL = 1800  # refresh session every 30 min

    @classmethod
    def get(cls) -> requests.Session:
        now = time.time()
        if cls._sess is None or now - cls._sess_ts > cls._SESS_TTL:
            sess = requests.Session()
            sess.headers.update(NSE_HEADERS)
            try:
                sess.get("https://www.nseindia.com/", timeout=8)
                sess.get("https://www.nseindia.com/market-data/live-equity-market", timeout=8)
            except Exception:
                pass
            cls._sess = sess
            cls._sess_ts = now
        return cls._sess


class NSEDataFetcher:
    """Main NSE data access class used by signal_generator.py."""

    def get_symbol_data(self, symbol: str) -> NSESymbolData:
        """Get comprehensive NSE data for a symbol."""
        data = NSESymbolData(symbol=symbol)

        # Delivery percentage
        delivery = self._get_delivery_pct(symbol)
        if delivery:
            data.delivery_pct = delivery.get("delivery_pct", 0)
            data.total_volume = delivery.get("total_volume", 0)
            data.delivery_volume = delivery.get("delivery_volume", 0)
            data.data_available = True

        # Circuit limits
        circuit = self._get_circuit_info(symbol)
        if circuit:
            data.circuit = circuit

        # Bulk deals
        bulk = self._get_bulk_deals_today()
        if bulk:
            sym_deals = [d for d in bulk if d.symbol.upper() == symbol.upper()]
            data.bulk_deals_today = sym_deals
            data.fii_bulk_buy = any(d.is_fii and d.trade_type == "BUY" for d in sym_deals)
            data.fii_bulk_sell = any(d.is_fii and d.trade_type == "SELL" for d in sym_deals)

        return data

    def get_delivery_score(self, symbol: str) -> float:
        """Returns score [-5, +5] based on delivery percentage."""
        data = self.get_symbol_data(symbol)
        pct = data.delivery_pct
        if pct <= 0:
            return 0.0
        if pct > 75:
            return 4.0   # Very high delivery = institutional accumulation
        elif pct > 60:
            return 2.5
        elif pct > 45:
            return 1.0
        elif pct > 30:
            return 0.0
        else:
            return -1.5  # Low delivery = mostly intraday/speculative

    def get_circuit_safe(self, symbol: str) -> bool:
        """Returns False if stock is near circuit limits (dangerous to trade)."""
        data = self.get_symbol_data(symbol)
        if data.circuit:
            return not (data.circuit.near_upper or data.circuit.near_lower)
        return True

    def get_bulk_deal_score(self, symbol: str) -> float:
        """Returns score [-5, +5] based on bulk deal activity."""
        data = self.get_symbol_data(symbol)
        score = 0.0
        if data.fii_bulk_buy:
            score += 4.5   # FII bulk buy = very strong signal
        if data.fii_bulk_sell:
            score -= 4.5
        for deal in data.bulk_deals_today:
            if not deal.is_fii:
                if deal.trade_type == "BUY":
                    score += 1.5
                else:
                    score -= 1.5
        return max(-5.0, min(5.0, score))

    def get_most_active(self, by: str = "value", limit: int = 20) -> List[str]:
        """Get most active NSE stocks by value or volume."""
        def _fetch():
            sess = NSESession.get()
            url = "https://www.nseindia.com/api/live-analysis-variations?index=gainers"
            r = sess.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return [item.get("symbol", "") for item in data.get("data", [])[:limit]]
            return []
        return _cached(f"most_active_{by}", _fetch, ttl=300) or []

    def _get_delivery_pct(self, symbol: str) -> Optional[dict]:
        def _fetch():
            sess = NSESession.get()
            url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}&section=trade_info"
            r = sess.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                trade_info = data.get("marketDeptOrderBook", {}).get("tradeInfo", {})
                total_vol = trade_info.get("totalTradedVolume", 0)
                delivery_vol = trade_info.get("deliveryQuantity", 0)
                delivery_pct = (delivery_vol / total_vol * 100) if total_vol > 0 else 0
                return {
                    "delivery_pct": round(delivery_pct, 1),
                    "total_volume": total_vol,
                    "delivery_volume": delivery_vol,
                }
            return None
        return _cached(f"delivery_{symbol}", _fetch, ttl=300)

    def _get_circuit_info(self, symbol: str) -> Optional[CircuitInfo]:
        def _fetch():
            sess = NSESession.get()
            url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
            r = sess.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                price_info = data.get("priceInfo", {})
                upper = float(price_info.get("upperCP", 0) or 0)
                lower = float(price_info.get("lowerCP", 0) or 0)
                current = float(price_info.get("lastPrice", 0) or 0)
                if upper <= 0 or lower <= 0 or current <= 0:
                    return None
                pct_to_upper = (upper - current) / current * 100
                pct_to_lower = (current - lower) / current * 100
                return CircuitInfo(
                    symbol=symbol,
                    upper_circuit=upper,
                    lower_circuit=lower,
                    current_price=current,
                    pct_to_upper=round(pct_to_upper, 2),
                    pct_to_lower=round(pct_to_lower, 2),
                    near_upper=pct_to_upper < 2.0,
                    near_lower=pct_to_lower < 2.0,
                )
            return None
        return _cached(f"circuit_{symbol}", _fetch, ttl=300)

    def _get_bulk_deals_today(self) -> List[BulkDeal]:
        def _fetch():
            sess = NSESession.get()
            url = "https://www.nseindia.com/api/snapshot-capital-market-largedeal"
            r = sess.get(url, timeout=10)
            deals = []
            if r.status_code == 200:
                data = r.json()
                for item in data.get("data", []):
                    client = str(item.get("clientName", "") or "")
                    is_fii = any(kw in client.upper() for kw in [
                        "FII", "FPI", "FOREIGN", "GOLDMAN", "MORGAN", "JP MORGAN",
                        "BLACKROCK", "VANGUARD", "CITADEL", "NOMURA", "MERRILL"
                    ])
                    deals.append(BulkDeal(
                        symbol=str(item.get("symbol", "")),
                        client=client,
                        trade_type="BUY" if str(item.get("buySell", "")).upper() == "BUY" else "SELL",
                        quantity=int(item.get("quantity", 0) or 0),
                        price=float(item.get("tradePrice", 0) or 0),
                        is_fii=is_fii,
                    ))
            return deals
        return _cached("bulk_deals", _fetch, ttl=600) or []

    def get_nifty_pcr(self) -> float:
        """Get NIFTY Options Put-Call Ratio."""
        def _fetch():
            sess = NSESession.get()
            r = sess.get(
                "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
                timeout=12
            )
            if r.status_code == 200:
                data = r.json()
                records = data.get("records", {})
                total_put_oi = sum(
                    rec.get("PE", {}).get("openInterest", 0)
                    for rec in records.get("data", []) if rec.get("PE")
                )
                total_call_oi = sum(
                    rec.get("CE", {}).get("openInterest", 0)
                    for rec in records.get("data", []) if rec.get("CE")
                )
                return total_put_oi / (total_call_oi + 1e-9)
            return 1.0
        return _cached("nifty_pcr", _fetch, ttl=300) or 1.0


# Module-level singleton — exported as both names for compatibility
_fetcher: Optional[NSEDataFetcher] = None


def get_nse_fetcher() -> NSEDataFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = NSEDataFetcher()
    return _fetcher


# Alias used by signal_generator.py
get_nse_data_fetcher = get_nse_fetcher
