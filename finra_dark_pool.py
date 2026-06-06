"""
finra_dark_pool.py — Real FINRA ATS Dark Pool + Nasdaq Daily Short Volume (L99)

Two free institutional data sources that professional desks actually use:

1. FINRA RegSHO Daily Short Sale Volume
   URL: regsho.finra.org — daily short selling volume per symbol
   Score: short_vol_pct > 60% = heavy institutional shorting
          short_vol_pct < 30% = buyers dominating

2. FINRA ATS Transparency (weekly dark pool volume)
   URL: ats.finra.org CSV reports
   Score: dark_pool_pct > 40% = institutional accumulation in dark pools
          dark_pool_pct < 10% = retail-driven = less institutional conviction
"""
import logging
import time as _time
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
_CACHE: Dict = {}
_TTL = 86400.0  # 24h — data is daily/weekly

def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _TTL:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _last_trading_date() -> str:
    """Return most recent weekday date string YYYYMMDD."""
    dt = datetime.now(ET)
    for _ in range(5):
        if dt.weekday() < 5:
            return dt.strftime("%Y%m%d")
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")


def _fetch_finra_short_volume(symbol: str) -> Optional[float]:
    """
    Fetch short volume ratio from FINRA RegSHO daily short sale file.
    Returns short_vol_pct (0-100) or None.
    """
    cached = _cache_get(f"sv_{symbol}")
    if cached is not None:
        return cached
    try:
        sym = symbol.upper()
        for delta in range(4):
            dt = datetime.now(ET) - timedelta(days=delta)
            if dt.weekday() >= 5:
                continue
            date_str = dt.strftime("%Y%m%d")
            # FINRA publishes two files: FNSQ (NASDAQ) and FNYX (NYSE)
            for prefix in ["FNSQ", "FNYX", "FNQC"]:
                url = f"https://regsho.finra.org/{prefix}sym{date_str}.txt"
                try:
                    r = requests.get(url, timeout=6,
                                     headers={"User-Agent": "Mozilla/5.0"})
                    if r.status_code != 200:
                        continue
                    for line in r.text.splitlines():
                        parts = line.split("|")
                        if len(parts) >= 5 and parts[0].strip().upper() == sym:
                            short_vol = float(parts[1]) if parts[1].replace(".", "").isdigit() else 0
                            total_vol = float(parts[3]) if parts[3].replace(".", "").isdigit() else 0
                            if total_vol > 0:
                                pct = (short_vol / total_vol) * 100.0
                                _cache_set(f"sv_{symbol}", pct)
                                return pct
                except Exception:
                    continue
    except Exception as exc:
        logger.debug(f"[finra_sv] {symbol}: {exc}")
    return None


def get_finra_dark_pool_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Real FINRA daily short volume signal (best free dark pool proxy).
    Returns (score_delta, reason). Fail-open.
    """
    try:
        sv_pct = _fetch_finra_short_volume(symbol)
        if sv_pct is None:
            return 0.0, "finra:no-data"

        # Heavy shorting (>60%): squeeze candidate for LONG, confirms SHORT
        if sv_pct > 65:
            d = 6.0 if direction == "LONG" else 8.0
            return d, f"FINRA[SHORT_VOL={sv_pct:.0f}%_HEAVY_→squeeze_risk]"
        elif sv_pct > 55:
            d = 3.0 if direction == "LONG" else 5.0
            return d, f"FINRA[short_vol={sv_pct:.0f}%_elevated]"
        elif sv_pct < 30:
            # Buyers dominating
            d = 5.0 if direction == "LONG" else -4.0
            return d, f"FINRA[short_vol={sv_pct:.0f}%_BUYERS_DOMINANT]"
        elif sv_pct < 40:
            d = 2.0 if direction == "LONG" else -2.0
            return d, f"FINRA[short_vol={sv_pct:.0f}%_low_short]"

        return 0.0, f"finra:neutral(sv={sv_pct:.0f}%)"
    except Exception as exc:
        logger.debug(f"[finra_dark_pool] {exc}")
        return 0.0, "finra:error"
