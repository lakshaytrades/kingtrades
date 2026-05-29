"""
insider_flow.py — SEC Form 4 Insider Transaction Tracker

Insider buying = the most honest signal in the market.
All US public company insiders must report within 2 business days via Form 4.

Data pipeline:
  1. SEC company_tickers.json  → ticker → CIK lookup (bulk, cached once per day)
  2. SEC submissions API       → recent Form 4 filing list for the company
  3. SEC filing XML parser     → transaction codes P (purchase) / S (sale)

Cache: 24 hours per symbol (Form 4 updates 1-2x/day)
Score range: -5 to +10
"""
import logging
import re
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_insider_cache: Dict = {}       # symbol → {data, ts}
_cik_cache:     Dict = {}       # ticker → cik (int)
_cik_cache_ts:  float = 0.0    # when _cik_cache was last populated

_INS_TTL    = 86400.0   # 24 hours
_CIK_TTL    = 86400.0   # refresh CIK map once per day
_HEADERS    = {"User-Agent": "KingTrades/1.0 contact@kingtrades.io"}
_MAX_FORMS  = 5          # max Form 4 XML fetches per symbol per day


def get_insider_score(symbol: str) -> Tuple[float, str]:
    """Returns (score_delta, reason). Cached 24h. Fail-open."""
    now = _time.monotonic()
    cached = _insider_cache.get(symbol)
    if cached and now - cached["ts"] < _INS_TTL:
        return _score(cached["data"])

    data = _fetch(symbol)
    if data is not None:
        _insider_cache[symbol] = {"data": data, "ts": now}
        return _score(data)
    return 0.0, ""


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

def _get_cik(symbol: str) -> Optional[int]:
    """Resolve ticker → CIK using SEC company_tickers.json (bulk, cached 24h)."""
    global _cik_cache, _cik_cache_ts
    now = _time.monotonic()
    if _cik_cache and now - _cik_cache_ts < _CIK_TTL:
        return _cik_cache.get(symbol.upper())

    try:
        import requests
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_HEADERS, timeout=10,
        )
        if r.status_code != 200:
            return None
        for entry in r.json().values():
            t = entry.get("ticker", "").upper()
            c = int(entry.get("cik_str", 0))
            if t and c:
                _cik_cache[t] = c
        _cik_cache_ts = now
        return _cik_cache.get(symbol.upper())
    except Exception as e:
        logger.debug(f"CIK lookup error: {e}")
        return None


def _fetch(symbol: str) -> Optional[Dict]:
    """
    Fetch insider buy/sell counts from SEC EDGAR.
    Returns {"buys": int, "sells": int} or None on failure.
    """
    try:
        import requests
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        cik = _get_cik(symbol)
        if not cik:
            return None

        # Fetch structured filing list from submissions endpoint
        sub_url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        r = requests.get(sub_url, headers=_HEADERS, timeout=8)
        if r.status_code != 200:
            return None

        recent   = r.json().get("filings", {}).get("recent", {})
        forms    = recent.get("form", [])
        accs     = recent.get("accessionNumber", [])
        dates    = recent.get("filingDate", [])
        doc_list = recent.get("primaryDocument", [])

        buys = sells = checked = 0
        for form, acc, date, doc in zip(forms, accs, dates, doc_list):
            if date < cutoff:
                break  # filings are newest-first; stop when older than window
            if form != "4":
                continue
            try:
                acc_clean  = acc.replace("-", "")
                filing_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{doc}"
                )
                r2 = requests.get(filing_url, headers=_HEADERS, timeout=5)
                if r2.status_code == 200:
                    txt = r2.text
                    # transactionCode: P = open-market purchase, S = open-market sale
                    for c in re.findall(r'<transactionCode>\s*(\w)\s*</transactionCode>', txt):
                        if c.upper() == 'P':   buys  += 1
                        elif c.upper() == 'S': sells += 1
                    # acquisitionOrDisposalCode: A = acquired, D = disposed
                    for c in re.findall(
                        r'<acquisitionOrDisposalCode>\s*(\w)\s*</acquisitionOrDisposalCode>', txt
                    ):
                        if c.upper() == 'A':   buys  += 1
                        elif c.upper() == 'D': sells += 1
            except Exception:
                pass
            checked += 1
            if checked >= _MAX_FORMS:
                break

        return {"buys": buys, "sells": sells}
    except Exception as e:
        logger.debug(f"insider_flow({symbol}): {e}")
        return None


def _score(data: Dict) -> Tuple[float, str]:
    b, s = data.get("buys", 0), data.get("sells", 0)
    if b == 0 and s == 0:
        return 0.0, ""
    score, reasons = 0.0, []
    if b >= 3:   score += 10; reasons.append(f"INSIDER_CLUSTER({b}buys+10)")
    elif b >= 1: score += 6;  reasons.append(f"INSIDER_BUY({b}+6)")
    if s >= 3:   score -= 5;  reasons.append(f"INSIDER_SELL_CLUSTER({s}-5)")
    elif s >= 2: score -= 3
    return round(score, 1), " | ".join(reasons)
