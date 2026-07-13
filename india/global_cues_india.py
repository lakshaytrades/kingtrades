"""
global_cues_india.py — SGX Nifty + Global Pre-Market Bias
Fetches at 9:05 AM IST. Adjusts signal scores by global momentum.
Sources: stooq.com (free CSV, no auth).
"""
import io
import logging
import time as _time
from typing import Optional

import requests

logger = logging.getLogger("global_cues_india")

_cache_ts:   float = 0.0
_cache_data: dict  = {}
_CACHE_TTL   = 3600.0  # 1 hour — global data doesn't change intraday


def _stooq_chg(symbol: str) -> Optional[float]:
    """Fetch last 2 rows from stooq CSV and return % change."""
    try:
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or len(r.text) < 20:
            return None
        import csv
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if len(rows) < 2:
            return None
        prev_close = float(rows[-2].get("Close", 0) or 0)
        last_close = float(rows[-1].get("Close", 0) or 0)
        if prev_close <= 0 or last_close <= 0:
            return None
        return round((last_close / prev_close - 1) * 100, 3)
    except Exception as e:
        logger.debug(f"stooq {symbol}: {e}")
        return None


def fetch() -> dict:
    """
    Fetch global market cues. Cached for 1 hour.
    Returns bias dict with bias, bias_score, size_adj, signal adjustments.
    """
    global _cache_ts, _cache_data
    now = _time.monotonic()
    if _cache_data and now - _cache_ts < _CACHE_TTL:
        return _cache_data

    sgx_chg    = _stooq_chg("^nifty.sg") or 0.0
    dow_chg    = _stooq_chg("^dji")      or 0.0
    sp500_chg  = _stooq_chg("^spx")      or 0.0
    usdinr_chg = _stooq_chg("usdinr")    or 0.0

    # If SGX not available, fall back to Nifty 50 proxy
    if sgx_chg == 0.0:
        try:
            from data_fetch_upstox import get_nifty_level
            nifty = get_nifty_level()
            sgx_chg = nifty.get("change_pct", 0.0)
        except Exception:
            pass

    # Build bias score
    bias_score = 0
    if sgx_chg > 0.5:
        bias_score += 6
    elif sgx_chg < -0.5:
        bias_score -= 6

    if dow_chg > 0.5:
        bias_score += 3
    elif dow_chg < -0.5:
        bias_score -= 3

    # Conflict detection: SGX and Dow disagree
    conflict = (sgx_chg > 0.3 and dow_chg < -0.3) or (sgx_chg < -0.3 and dow_chg > 0.3)
    size_adj  = 0.8 if conflict else 1.0

    if bias_score >= 4:
        bias = "BULLISH"
    elif bias_score <= -4:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    arrows = {
        "BULLISH": "🟢",
        "BEARISH": "🔴",
        "NEUTRAL": "⚪",
    }
    summary = (
        f"Global: SGX {sgx_chg:+.2f}% | Dow {dow_chg:+.2f}% | "
        f"S&P {sp500_chg:+.2f}% | USD/INR {usdinr_chg:+.2f}% | "
        f"{arrows[bias]} {bias}"
    )

    _cache_data = {
        "sgx_nifty_chg": sgx_chg,
        "dow_chg":        dow_chg,
        "sp500_chg":      sp500_chg,
        "usdinr_chg":     usdinr_chg,
        "bias":           bias,
        "bias_score":     bias_score,
        "size_adj":       size_adj,
        "conflict":       conflict,
        "summary":        summary,
    }
    _cache_ts = now
    return _cache_data


def get_signal_adjustment(direction: str, global_cues: Optional[dict] = None) -> int:
    """
    +6 if direction aligns with bias, -4 if against. 0 if NEUTRAL.
    """
    if global_cues is None:
        global_cues = _cache_data
    if not global_cues:
        return 0
    bias = global_cues.get("bias", "NEUTRAL")
    if bias == "BULLISH" and direction == "LONG":
        return 6
    if bias == "BEARISH" and direction == "SHORT":
        return 6
    if bias == "BULLISH" and direction == "SHORT":
        return -4
    if bias == "BEARISH" and direction == "LONG":
        return -4
    return 0
