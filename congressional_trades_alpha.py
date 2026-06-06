"""
congressional_trades_alpha.py — Congressional Stock Trading Alpha (God Mode)
US law requires Congress members to disclose stock trades within 45 days.
Congressional buys often precede policy-driven institutional buying.
Data: House Clerk & Senate public disclosure APIs (free)
      QuiverQuant public feed (free tier)

When a Congress member recently bought a stock we're considering buying:
  → +8 score (informed buying by policy makers = strong signal)
When a Congress member recently sold a stock we're considering shorting:
  → +6 score (informed selling)
Cache: 4 hours (disclosures update slowly)
"""
import logging
import time as _time
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger("congressional_alpha")

_cache: dict = {}
_TTL = 14400.0  # 4 hours
_QUIVER_URL = "https://api.quiverquant.com/beta/live/congresstrading"
_CACHE_FILE = Path(__file__).parent / "logs" / "congressional_cache.json"

# Per-symbol score cache
_symbol_cache: dict = {}


def _fetch_congressional_trades() -> List[Dict]:
    """
    Fetch congressional trading disclosures from public sources.
    Returns list of {ticker, transaction_type, transaction_date, amount, member_name}.
    Fail-open: returns [] on complete failure.
    """
    now = _time.time()

    # Check global trades cache (all symbols at once)
    if "all_trades" in _cache and now - _cache["all_trades"][1] < _TTL:
        return _cache["all_trades"][0]

    trades: List[Dict] = []

    # 1. Try QuiverQuant free API (full list endpoint)
    try:
        resp = requests.get(
            _QUIVER_URL,
            headers={
                "Authorization": "Token ",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            raw = resp.json()
            if isinstance(raw, list) and raw:
                for item in raw:
                    try:
                        trades.append({
                            "ticker":           str(item.get("Ticker") or item.get("ticker") or "").upper().strip(),
                            "transaction_type": str(item.get("Transaction") or item.get("transaction") or ""),
                            "transaction_date": str(item.get("Date") or item.get("date") or ""),
                            "amount":           str(item.get("Range") or item.get("range") or item.get("Amount") or ""),
                            "member_name":      str(item.get("Representative") or item.get("Senator") or
                                                    item.get("member") or ""),
                        })
                    except Exception:
                        continue
                logger.debug(f"congressional_alpha: fetched {len(trades)} trades from QuiverQuant")
    except Exception as e:
        logger.debug(f"congressional_alpha: QuiverQuant primary failed: {e}")

    # 2. Fallback: Senate eFD EDGAR search
    if not trades:
        try:
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
            senate_url = (
                f"https://efts.senate.gov/LATEST/search.json"
                f"?q=&dateFrom={thirty_days_ago}&dateTo={today_str}&senator=&page=1"
            )
            resp2 = requests.get(
                senate_url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                timeout=10,
            )
            if resp2.status_code == 200:
                data2 = resp2.json()
                hits = data2.get("hits", {}).get("hits", [])
                for hit in hits:
                    src = hit.get("_source", {})
                    # Senate eFD returns PTR disclosures — extract ticker if available
                    ticker = str(src.get("asset_name") or src.get("ticker") or "").upper().strip()
                    if ticker:
                        trades.append({
                            "ticker":           ticker,
                            "transaction_type": str(src.get("type") or ""),
                            "transaction_date": str(src.get("transaction_date") or src.get("date") or ""),
                            "amount":           str(src.get("amount") or ""),
                            "member_name":      str(src.get("first_name", "") + " " + src.get("last_name", "")).strip(),
                        })
                logger.debug(f"congressional_alpha: Senate eFD returned {len(trades)} items")
        except Exception as e:
            logger.debug(f"congressional_alpha: Senate eFD fallback failed: {e}")

    # 3. Save successful fetch to disk cache
    if trades:
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_CACHE_FILE, "w") as f:
                json.dump({"ts": now, "trades": trades}, f)
        except Exception:
            pass
        _cache["all_trades"] = (trades, now)
        return trades

    # 4. Load from disk backup if all live sources failed
    try:
        if _CACHE_FILE.exists():
            with open(_CACHE_FILE, "r") as f:
                cached = json.load(f)
            disk_trades = cached.get("trades", [])
            if disk_trades:
                logger.debug(f"congressional_alpha: loaded {len(disk_trades)} trades from disk cache")
                _cache["all_trades"] = (disk_trades, now)
                return disk_trades
    except Exception:
        pass

    # 5. Fail-open: return empty
    _cache["all_trades"] = ([], now)
    return []


def get_congressional_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Score congressional trading activity for the given symbol and direction.
    Returns (score_delta, reason). Fail-open returns (0.0, "").
    """
    try:
        now = _time.time()
        cache_key = f"{symbol}:{direction}"
        if cache_key in _symbol_cache and now - _symbol_cache[cache_key][1] < _TTL:
            return _symbol_cache[cache_key][0]

        all_trades = _fetch_congressional_trades()
        if not all_trades:
            _symbol_cache[cache_key] = ((0.0, ""), now)
            return (0.0, "")

        # Filter trades for this symbol within last 30 days
        cutoff = datetime.utcnow() - timedelta(days=30)
        purchases: List[str] = []
        sales: List[str] = []

        sym_upper = symbol.upper().strip()
        for trade in all_trades:
            if trade.get("ticker", "").upper().strip() != sym_upper:
                continue
            try:
                date_str = trade.get("transaction_date", "")
                if not date_str:
                    continue
                trade_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                if trade_date < cutoff:
                    continue
            except Exception:
                continue

            tx = (trade.get("transaction_type") or "").lower()
            member = trade.get("member_name", "unknown")
            if "purchase" in tx or "buy" in tx:
                purchases.append(member)
            elif "sale" in tx or "sell" in tx:
                sales.append(member)

        buy_count = len(purchases)
        sell_count = len(sales)

        if buy_count == 0 and sell_count == 0:
            result = (0.0, "")
        elif buy_count > sell_count and direction == "LONG":
            members_str = ",".join(set(purchases))[:60]
            result = (8.0, f"congressional_buy:{buy_count}_{members_str}")
        elif sell_count > buy_count and direction == "SHORT":
            members_str = ",".join(set(sales))[:60]
            result = (6.0, f"congressional_sell:{sell_count}_{members_str}")
        elif buy_count > 0 and direction == "SHORT":
            result = (-4.0, "congressional_against")
        elif sell_count > 0 and direction == "LONG":
            result = (-4.0, "congressional_against")
        else:
            result = (0.0, "")

        _symbol_cache[cache_key] = (result, now)
        return result

    except Exception as e:
        logger.debug(f"congressional_trades_alpha fail-open {symbol}: {e}")
        return (0.0, "")


def get_top_congressional_buys(days: int = 7) -> List[str]:
    """
    Return list of symbols with most congressional purchases in last N days.
    Used for watchlist enhancement. Fail-open returns [].
    """
    try:
        all_trades = _fetch_congressional_trades()
        if not all_trades:
            return []

        cutoff = datetime.utcnow() - timedelta(days=days)
        buy_counts: Dict[str, int] = {}

        for trade in all_trades:
            try:
                date_str = trade.get("transaction_date", "")
                if not date_str:
                    continue
                trade_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                if trade_date < cutoff:
                    continue
            except Exception:
                continue

            tx = (trade.get("transaction_type") or "").lower()
            ticker = trade.get("ticker", "").upper().strip()
            if not ticker:
                continue
            if "purchase" in tx or "buy" in tx:
                buy_counts[ticker] = buy_counts.get(ticker, 0) + 1

        # Sort by most purchases, return top 20
        sorted_symbols = sorted(buy_counts, key=lambda s: buy_counts[s], reverse=True)
        return sorted_symbols[:20]

    except Exception as e:
        logger.debug(f"get_top_congressional_buys fail-open: {e}")
        return []
