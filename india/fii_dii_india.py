"""
fii_dii_india.py — FII/DII flow analysis for NSE India
Data: NSE website FII/DII data (publicly available, updated daily)
Also infers intraday institutional direction from Nifty vs market breadth.

FII > +2000 Cr → Strong BULLISH bias (+8 LONG, -6 SHORT)
FII > +500 Cr  → Mild BULLISH (+4 LONG)
FII < -2000 Cr → Strong BEARISH (-6 LONG, +8 SHORT)
DII often counter-trades FII — combined view matters most.
"""

import logging
import time as _time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

logger = logging.getLogger("fii_dii_india")
IST = ZoneInfo("Asia/Kolkata")

_FII_CACHE: dict = {}
_FII_TTL = 3600.0  # refresh hourly (NSE updates once daily, but we check hourly)


# ── NSE FII/DII fetch ─────────────────────────────────────────────────────────

def _fetch_fii_dii_nse() -> dict:
    """
    Fetch latest FII/DII net flows from NSE public API.
    Returns dict with keys fii_net_cr, dii_net_cr (values in INR crores).
    Returns {} on any failure (fail-open).
    """
    try:
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.nseindia.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        # Prime session cookies (NSE requires a page visit before API calls)
        try:
            session.get("https://www.nseindia.com/", timeout=8)
        except Exception:
            pass

        resp = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # NSE format: list where index 0 = FII, index 1 = DII
        # Each entry has "netVal" field (net buy/sell in crores)
        if not isinstance(data, list) or len(data) < 2:
            logger.debug(f"FII/DII NSE response unexpected format: {str(data)[:200]}")
            return {}

        def _parse_net(entry: dict) -> float:
            raw = entry.get("netVal", entry.get("netBuySell", "0"))
            # NSE may return strings like "2,345.67" — strip commas
            try:
                return float(str(raw).replace(",", ""))
            except (ValueError, TypeError):
                return 0.0

        fii_net = _parse_net(data[0])
        dii_net = _parse_net(data[1])

        logger.debug(f"FII/DII NSE: FII={fii_net:+.0f} Cr  DII={dii_net:+.0f} Cr")
        return {"fii_net_cr": fii_net, "dii_net_cr": dii_net}

    except Exception as e:
        logger.debug(f"_fetch_fii_dii_nse failed: {e}")
        return {}


def _get_cached_fii_dii() -> dict:
    """Return cached FII/DII data, refreshing if stale."""
    now = _time.monotonic()
    if _FII_CACHE.get("ts", 0) > now - _FII_TTL:
        return _FII_CACHE.get("data", {})

    data = _fetch_fii_dii_nse()
    _FII_CACHE["data"] = data
    _FII_CACHE["ts"]   = now
    return data


# ── Public API ────────────────────────────────────────────────────────────────

def get_fii_dii_bias() -> Tuple[str, float, str]:
    """
    Compute market bias from FII+DII combined flows.

    Returns:
        (bias, confidence, reason)
        bias       : "BULLISH", "BEARISH", or "NEUTRAL"
        confidence : abs(combined_flow) / 5000 clipped to [0, 1]
        reason     : human-readable description
    """
    data = _get_cached_fii_dii()

    if not data:
        return ("NEUTRAL", 0.0, "FII/DII data unavailable")

    fii = data.get("fii_net_cr", 0.0)
    dii = data.get("dii_net_cr", 0.0)
    combined = fii + dii

    confidence = min(1.0, abs(combined) / 5000.0)

    if combined > 500:
        bias   = "BULLISH"
        reason = (f"FII {fii:+.0f} Cr + DII {dii:+.0f} Cr = "
                  f"combined {combined:+.0f} Cr BULLISH (conf {confidence:.2f})")
    elif combined < -500:
        bias   = "BEARISH"
        reason = (f"FII {fii:+.0f} Cr + DII {dii:+.0f} Cr = "
                  f"combined {combined:+.0f} Cr BEARISH (conf {confidence:.2f})")
    else:
        bias   = "NEUTRAL"
        reason = (f"FII {fii:+.0f} Cr + DII {dii:+.0f} Cr = "
                  f"combined {combined:+.0f} Cr NEUTRAL")

    return (bias, confidence, reason)


def get_fii_dii_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Return (score_delta, reason) for a trade signal based on FII/DII flows.

    Scoring rules:
      BULLISH + LONG  → +min(10, confidence*10)   (institutions behind you)
      BEARISH + SHORT → +min(10, confidence*10)   (institutions behind you)
      BULLISH + SHORT → -6                         (fighting institutions)
      BEARISH + LONG  → -6                         (fighting institutions)
      NEUTRAL         →  0                         (no edge either way)

    Fail-open: returns (0.0, "") on any error.
    """
    try:
        bias, confidence, reason = get_fii_dii_bias()

        if bias == "NEUTRAL":
            return (0.0, "")

        with_flow     = (bias == "BULLISH" and direction == "LONG") or \
                        (bias == "BEARISH" and direction == "SHORT")
        against_flow  = (bias == "BULLISH" and direction == "SHORT") or \
                        (bias == "BEARISH" and direction == "LONG")

        if with_flow:
            delta = min(10.0, confidence * 10.0)
            return (delta, f"FII/DII_{bias} tailwind: {reason}")
        elif against_flow:
            return (-6.0, f"FII/DII_{bias} headwind (fighting institutions): {reason}")

        return (0.0, "")

    except Exception as e:
        logger.debug(f"get_fii_dii_score failed (fail-open): {e}")
        return (0.0, "")
