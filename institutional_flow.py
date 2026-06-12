"""
institutional_flow.py — SEC 13F institutional ownership + EDGAR filing scanner (v17.0)

Combines EDGAR 13F filing counts with yfinance institutional holders data
to gauge institutional conviction changes for a stock.

Thread-safe singleton with 4-hour TTL cache.  Fail-open (returns 0.0).
"""

import logging
import threading
import time as _time
from datetime import datetime, timedelta
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# ── TTL cache ─────────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, float, str]] = {}  # key → (score, expire_ts, reason)
_cache_lock = threading.Lock()
_TTL = 14400  # 4 hours


def _cache_get(key: str) -> Tuple[bool, float, str]:
    with _cache_lock:
        if key in _cache:
            score, exp, reason = _cache[key]
            if _time.monotonic() < exp:
                return True, score, reason
    return False, 0.0, ""


def _cache_set(key: str, score: float, reason: str) -> None:
    with _cache_lock:
        _cache[key] = (score, _time.monotonic() + _TTL, reason)


# ── EDGAR 13F scanner ─────────────────────────────────────────────────────────

def _edgar_13f_score(symbol: str, direction: str) -> Tuple[float, str]:
    """Query EDGAR full-text search for 13F filings mentioning the symbol."""
    try:
        import urllib.request
        import json

        today = datetime.utcnow().date()
        start = (today - timedelta(days=90)).isoformat()
        end   = today.isoformat()

        url = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22"
            f"&forms=13F-HR&dateRange=custom&startdt={start}&enddt={end}"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "SataVectorBot research@kingtrades.ai"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())

        hits = data.get("hits", {}).get("hits", [])
        n_filings = len(hits)

        if n_filings == 0:
            return 0.0, "no 13F filings"

        # Check for large single filer (filing description may mention percentage)
        large_filer = False
        for h in hits[:5]:
            src = str(h.get("_source", {})).lower()
            if any(pct in src for pct in ["5%", "5.0%", "6%", "7%", "8%", "9%", "10%"]):
                large_filer = True
                break

        if large_filer:
            score = 12.0 if direction == "LONG" else -12.0
            return score, f"large_13F_filer={n_filings} filings"

        if n_filings >= 5:
            score = 8.0 if direction == "LONG" else -6.0
            return score, f"new_13F_filers={n_filings}"
        elif n_filings >= 2:
            score = 4.0 if direction == "LONG" else -3.0
            return score, f"some_13F_activity={n_filings}"

        return 0.0, f"13F_filings={n_filings}"
    except Exception as exc:
        logger.debug(f"EDGAR 13F error (fail-open): {exc}")
        return 0.0, "edgar N/A"


def _yfinance_holders_score(symbol: str, direction: str) -> Tuple[float, str]:
    """Use yfinance institutional_holders and major_holders for ownership trend."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        # major_holders: rows = ['% of Shares Held by All Insider', ...]
        mh = ticker.major_holders
        if mh is None or (hasattr(mh, 'empty') and mh.empty):
            return 0.0, "major_holders N/A"

        # Parse institutional ownership percentage
        inst_pct = None
        try:
            # Row 2 typically: "% of Shares Held by Institutions"
            for i in range(len(mh)):
                row = mh.iloc[i]
                val_str = str(row.iloc[0]).replace('%', '').strip()
                label   = str(row.iloc[1]).lower() if len(row) > 1 else ""
                if "institution" in label:
                    inst_pct = float(val_str)
                    break
        except Exception:
            pass

        if inst_pct is None:
            return 0.0, "inst_pct parse N/A"

        if inst_pct > 70.0:
            score = 6.0 if direction == "LONG" else -4.0
            return score, f"inst_ownership={inst_pct:.0f}%"
        elif inst_pct < 30.0:
            score = -4.0 if direction == "LONG" else 4.0
            return score, f"low_inst={inst_pct:.0f}%"

        return 0.0, f"inst_ownership={inst_pct:.0f}%"
    except Exception as exc:
        logger.debug(f"yfinance holders error (fail-open): {exc}")
        return 0.0, "holders N/A"


# ── Public API ────────────────────────────────────────────────────────────────

def get_institutional_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    EDGAR 13F + yfinance institutional ownership score.

    Returns (score_delta, reason_string).
    Fail-open: returns (0.0, "inst_flow N/A") on any error.
    4-hour TTL cache per symbol+direction.
    """
    cache_key = f"{symbol}:{direction}"
    hit, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"inst_flow cached: {cached_reason}"

    try:
        edgar_score, edgar_reason = _edgar_13f_score(symbol, direction)
        yf_score, yf_reason       = _yfinance_holders_score(symbol, direction)

        # Combine: EDGAR is primary signal, yfinance adds context
        combined = round(edgar_score * 0.6 + yf_score * 0.4, 1)
        reason   = f"edgar={edgar_score:+.0f}({edgar_reason}) yf={yf_score:+.0f}({yf_reason})"

        _cache_set(cache_key, combined, reason)
        return combined, reason
    except Exception as exc:
        logger.debug(f"get_institutional_score error (fail-open): {exc}")
        return 0.0, "inst_flow N/A"
