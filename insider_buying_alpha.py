"""
insider_buying_alpha.py — SEC Form 4 Insider Trading Signal (God Mode)
Corporate insiders (CEOs, CFOs, directors) must file Form 4 within 2 days of trading.
Heavy insider buying = management confident in company = strong bullish signal.
Data: SEC EDGAR RSS feed (free, official US government data)
      https://efts.sec.gov/LATEST/search-index?q=%22form+4%22&dateRange=custom&startdt={}&enddt={}

Insider buy scoring:
  Multiple insiders buying within 30 days: +10 (cluster buying)
  Single large insider buy (>$100k): +6
  Insider selling (except options exercise): -5 (weak short signal)
"""
import logging
import time as _time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("insider_alpha")

_cache: dict = {}
_TTL = 7200.0  # 2 hours

_EDGAR_SEARCH_URL = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=%22{symbol}%22&forms=4&dateRange=custom&startdt={start}&enddt={end}"
    "&hits.hits._source=period_of_report,entity_name,file_num,period_of_report"
)


def _fetch_insider_trades(symbol: str) -> List[Dict]:
    """
    Fetch recent Form 4 insider trades for symbol from SEC EDGAR.
    Tries EDGAR full-text search first, yfinance as fallback.
    Returns list of {transaction_type, value, shares, insider, date, position}.
    Fail-open returns [].
    """
    now = _time.time()
    cache_key = f"raw:{symbol}"
    if cache_key in _cache and now - _cache[cache_key][1] < _TTL:
        return _cache[cache_key][0]

    trades: List[Dict] = []
    end_date = datetime.utcnow().strftime("%Y-%m-%d")
    start_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

    # 1. SEC EDGAR full-text search for Form 4 filings
    try:
        url = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?q=%22{symbol}%22&forms=4&dateRange=custom"
            f"&startdt={start_date}&enddt={end_date}"
        )
        resp = requests.get(
            url,
            headers={"User-Agent": "research-bot contact@example.com", "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            for hit in hits:
                src = hit.get("_source", {})
                trades.append({
                    "transaction_type": "unknown",   # EDGAR FTS doesn't expose tx direction in this endpoint
                    "value":            0.0,
                    "shares":           0,
                    "insider":          str(src.get("entity_name") or ""),
                    "date":             str(src.get("period_of_report") or ""),
                    "position":         "",
                    "source":           "edgar_fts",
                })
            logger.debug(f"insider_alpha: EDGAR returned {len(trades)} Form 4 hits for {symbol}")
    except Exception as e:
        logger.debug(f"insider_alpha: EDGAR FTS failed for {symbol}: {e}")

    # 2. Fallback: yfinance insider_transactions (richer data with amounts)
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(symbol)
        insiders_df = ticker_obj.insider_transactions
        if insiders_df is not None and not insiders_df.empty:
            cutoff = datetime.utcnow() - timedelta(days=30)
            for _, row in insiders_df.iterrows():
                try:
                    # yfinance columns: Shares, Value, URL, Text, Insider, Start Date, Position, Transaction
                    tx_date_raw = row.get("Start Date") or row.get("startDate") or row.name
                    if hasattr(tx_date_raw, "to_pydatetime"):
                        tx_date = tx_date_raw.to_pydatetime().replace(tzinfo=None)
                    elif isinstance(tx_date_raw, str):
                        tx_date = datetime.strptime(tx_date_raw[:10], "%Y-%m-%d")
                    else:
                        tx_date = datetime.utcnow() - timedelta(days=60)  # assume old, skip

                    if tx_date < cutoff:
                        continue

                    tx_text = str(row.get("Transaction") or row.get("Text") or "").lower()
                    # Skip options exercises — not open-market buying conviction
                    if "option" in tx_text or "exercise" in tx_text:
                        continue

                    shares = int(row.get("Shares") or 0)
                    value = float(row.get("Value") or 0.0)
                    insider_name = str(row.get("Insider") or "")
                    position = str(row.get("Position") or "")

                    if "sale" in tx_text or "sold" in tx_text or "sell" in tx_text:
                        tx_type = "Sale"
                    elif "purchase" in tx_text or "buy" in tx_text or "bought" in tx_text or "acquisition" in tx_text:
                        tx_type = "Purchase"
                    else:
                        tx_type = "unknown"

                    trades.append({
                        "transaction_type": tx_type,
                        "value":            value,
                        "shares":           shares,
                        "insider":          insider_name,
                        "date":             tx_date.strftime("%Y-%m-%d"),
                        "position":         position,
                        "source":           "yfinance",
                    })
                except Exception:
                    continue
            logger.debug(f"insider_alpha: yfinance returned {len([t for t in trades if t['source']=='yfinance'])} insider trades for {symbol}")
    except Exception as e:
        logger.debug(f"insider_alpha: yfinance fallback failed for {symbol}: {e}")

    _cache[cache_key] = (trades, now)
    return trades


def get_insider_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Score insider trading activity for the given symbol and direction.
    Returns (score_delta, reason). Fail-open returns (0.0, "").
    """
    try:
        now = _time.time()
        cache_key = f"score:{symbol}:{direction}"
        if cache_key in _cache and now - _cache[cache_key][1] < _TTL:
            return _cache[cache_key][0]

        all_trades = _fetch_insider_trades(symbol)
        if not all_trades:
            _cache[cache_key] = ((0.0, ""), now)
            return (0.0, "")

        # Count buys and sells from yfinance data (has tx_type), ignore EDGAR FTS stubs
        yf_trades = [t for t in all_trades if t.get("source") == "yfinance"]

        buy_insiders: List[str] = []
        sell_insiders: List[str] = []
        max_buy_value = 0.0

        for t in yf_trades:
            tx = t.get("transaction_type", "")
            value = float(t.get("value") or 0.0)
            insider = t.get("insider", "unknown")
            if tx == "Purchase":
                buy_insiders.append(insider)
                if value > max_buy_value:
                    max_buy_value = value
            elif tx == "Sale":
                sell_insiders.append(insider)

        unique_buyers = set(buy_insiders)
        unique_sellers = set(sell_insiders)
        buy_count = len(unique_buyers)
        sell_count = len(unique_sellers)

        # Apply scoring logic
        if buy_count >= 3:
            # Cluster buying — strongest signal
            score = 10.0
            reason = f"insider_cluster_buy:{buy_count}"
        elif buy_count >= 1 and max_buy_value >= 100_000:
            # Single large buy
            score = 6.0
            reason = f"insider_large_buy:${max_buy_value:,.0f}"
        elif buy_count >= 1:
            # Single small buy
            score = 3.0
            reason = f"insider_buy:{buy_count}"
        elif sell_count >= 1:
            # Net sellers
            if direction == "LONG":
                score = -5.0
                reason = f"insider_sell:{sell_count}"
            else:
                # Selling = mild SHORT hint
                score = 2.0
                reason = f"insider_sell_short_hint:{sell_count}"
        else:
            # No clear yfinance data — check if EDGAR FTS found filings at all
            edgar_hits = len([t for t in all_trades if t.get("source") == "edgar_fts"])
            if edgar_hits > 0:
                # EDGAR found Form 4 filings but couldn't classify — neutral
                score = 0.0
                reason = f"insider_form4_found:{edgar_hits}"
            else:
                score = 0.0
                reason = ""

        # Flip LONG scores for SHORT and vice versa where appropriate
        if direction == "SHORT" and score > 0 and "sell" not in reason and "short" not in reason:
            score = -score
        elif direction == "LONG" and score < 0 and "against" not in reason:
            pass  # already penalized correctly

        result = (float(score), reason)
        _cache[cache_key] = (result, now)
        return result

    except Exception as e:
        logger.debug(f"insider_buying_alpha fail-open {symbol}: {e}")
        return (0.0, "")


def is_heavy_insider_buying(symbol: str) -> bool:
    """
    Returns True if 2+ insiders bought in last 14 days.
    Used as a confirmation gate. Fail-open returns False.
    """
    try:
        all_trades = _fetch_insider_trades(symbol)
        yf_trades = [t for t in all_trades if t.get("source") == "yfinance"]

        cutoff = datetime.utcnow() - timedelta(days=14)
        buyers: set = set()

        for t in yf_trades:
            try:
                date_str = t.get("date", "")
                if not date_str:
                    continue
                trade_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                if trade_date < cutoff:
                    continue
            except Exception:
                continue

            if t.get("transaction_type") == "Purchase":
                buyers.add(t.get("insider", f"unknown_{len(buyers)}"))

        return len(buyers) >= 2

    except Exception as e:
        logger.debug(f"is_heavy_insider_buying fail-open {symbol}: {e}")
        return False
