"""
block_deal_india.py — NSE Block Deal & Bulk Deal Signal
NSE publishes block and bulk deal data daily at ~6 PM.
These are large institutional trades: block deals ≥ 5 lakh shares OR ≥ Rs 5 crore.

Why it matters: institutional block buys signal high conviction.
  - Block buy same day / next day → strong institutional accumulation
  - Block sell → distribution alert
  - Bulk deals (exchange-reported larger trades) add weight

URLs:
  Block: https://www.nseindia.com/api/block-deal
  Bulk:  https://www.nseindia.com/api/bulk-deal

Score impact:
  Block BUY  + LONG:  +12 pts (institutional conviction)
  Block BUY  + SHORT: -8 pts (fighting institutions)
  Block SELL + SHORT: +10 pts (distribution confirmation)
  Block SELL + LONG:  -6 pts (sell against)
  Bulk BUY   + LONG:  +6 pts
  Bulk SELL  + SHORT: +5 pts
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("block_deal")
IST = ZoneInfo("Asia/Kolkata")

_CACHE_DIR = Path(__file__).parent.parent / "data"
_cache_date: Optional[str] = None
_block_cache: Dict[str, List[dict]] = {}    # symbol → list of deal dicts
_bulk_cache:  Dict[str, List[dict]] = {}


def _nse_session_headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.nseindia.com/",
        "X-Requested-With": "XMLHttpRequest",
    }


def _fetch_nse_deals() -> bool:
    """
    Fetch block and bulk deal data from NSE. Returns True on success.
    Uses session cookie to bypass Akamai protection.
    """
    global _block_cache, _bulk_cache, _cache_date
    try:
        import requests
        sess = requests.Session()
        sess.headers.update(_nse_session_headers())
        # Warm up session with homepage
        try:
            sess.get("https://www.nseindia.com", timeout=8)
        except Exception:
            pass

        block_data = {}
        bulk_data  = {}

        # Block deals
        try:
            r = sess.get("https://www.nseindia.com/api/block-deal", timeout=10)
            if r.status_code == 200:
                deals = r.json().get("data", r.json() if isinstance(r.json(), list) else [])
                for deal in deals:
                    sym = str(deal.get("symbol", "")).strip().upper()
                    if not sym:
                        continue
                    block_data.setdefault(sym, []).append({
                        "type": "BLOCK",
                        "trade_type": str(deal.get("bdType", deal.get("tradeType", ""))).upper(),
                        "qty": float(deal.get("bdQty", deal.get("quantity", 0)) or 0),
                        "price": float(deal.get("bdPrice", deal.get("price", 0)) or 0),
                        "client": str(deal.get("clientName", "")),
                    })
        except Exception as e:
            logger.debug(f"block_deal fetch: {e}")

        # Bulk deals
        try:
            r = sess.get("https://www.nseindia.com/api/bulk-deal", timeout=10)
            if r.status_code == 200:
                deals = r.json().get("data", r.json() if isinstance(r.json(), list) else [])
                for deal in deals:
                    sym = str(deal.get("symbol", "")).strip().upper()
                    if not sym:
                        continue
                    bulk_data.setdefault(sym, []).append({
                        "type": "BULK",
                        "trade_type": str(deal.get("bdType", deal.get("tradeType", ""))).upper(),
                        "qty": float(deal.get("bdQty", deal.get("quantity", 0)) or 0),
                        "price": float(deal.get("bdPrice", deal.get("price", 0)) or 0),
                    })
        except Exception as e:
            logger.debug(f"bulk_deal fetch: {e}")

        _block_cache = block_data
        _bulk_cache  = bulk_data
        _cache_date  = datetime.now(IST).strftime("%Y-%m-%d")

        # Persist to file for next scan cycle
        try:
            _CACHE_DIR.mkdir(exist_ok=True)
            today = _cache_date
            cache_path = _CACHE_DIR / f"block_deals_{today}.json"
            cache_path.write_text(json.dumps({"block": block_data, "bulk": bulk_data}))
        except Exception:
            pass

        total = sum(len(v) for v in block_data.values()) + sum(len(v) for v in bulk_data.values())
        logger.info(f"Block/bulk deals loaded: {total} deals for {len(block_data)+len(bulk_data)} symbols")
        return True

    except Exception as e:
        logger.debug(f"_fetch_nse_deals: {e}")
        return False


def _load_deal_data():
    """Load deal data, using cache if today's data already loaded."""
    global _block_cache, _bulk_cache, _cache_date
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if _cache_date == today and (_block_cache or _bulk_cache):
        return

    # Try file cache first
    try:
        cache_path = _CACHE_DIR / f"block_deals_{today}.json"
        if cache_path.exists():
            data = json.loads(cache_path.read_text())
            _block_cache = data.get("block", {})
            _bulk_cache  = data.get("bulk", {})
            _cache_date  = today
            return
    except Exception:
        pass

    # Live fetch
    _fetch_nse_deals()


def get_block_deal_score(symbol: str, direction: str) -> Tuple[int, str]:
    """
    Return (score_adj, reason) based on block/bulk deal activity.
    Fail-open: returns (0, "") on any error.
    """
    try:
        _load_deal_data()
        sym = symbol.upper()
        score_adj = 0
        reasons = []

        # Check block deals
        for deal in _block_cache.get(sym, []):
            trade_type = deal.get("trade_type", "")
            is_buy  = "BUY" in trade_type or "B" == trade_type
            is_sell = "SELL" in trade_type or "S" == trade_type

            if is_buy and direction == "LONG":
                score_adj += 12
                reasons.append(f"BLOCK_BUY {deal.get('qty', 0)/100:.0f}L")
            elif is_buy and direction == "SHORT":
                score_adj -= 8
                reasons.append("BLOCK_BUY_CONTRA")
            elif is_sell and direction == "SHORT":
                score_adj += 10
                reasons.append(f"BLOCK_SELL {deal.get('qty', 0)/100:.0f}L")
            elif is_sell and direction == "LONG":
                score_adj -= 6
                reasons.append("BLOCK_SELL_CONTRA")

        # Check bulk deals (lighter weight)
        for deal in _bulk_cache.get(sym, []):
            trade_type = deal.get("trade_type", "")
            is_buy  = "BUY" in trade_type or "B" == trade_type
            is_sell = "SELL" in trade_type or "S" == trade_type

            if is_buy and direction == "LONG":
                score_adj += 6
                reasons.append(f"BULK_BUY {deal.get('qty', 0)/100:.0f}L")
            elif is_sell and direction == "SHORT":
                score_adj += 5
                reasons.append("BULK_SELL")

        # Cap
        score_adj = max(-12, min(18, score_adj))
        reason = " | ".join(reasons) if reasons else ""
        return (score_adj, reason)

    except Exception as e:
        logger.debug(f"get_block_deal_score {symbol}: {e}")
        return (0, "")
