"""
nse_delivery_volume.py — NSE Delivery Volume Signal
India specialist: NSE publishes delivery % daily — a unique free signal.

Delivery % = what fraction of traded volume was actual delivery (non-intraday).
> 60%  → institutional accumulation — strong hands holding — BULLISH
> 80%  → very strong institutional buying — strong BULLISH
< 20%  → pure speculative/intraday trade — WEAK signal
< 10%  → operator/algo churn — IGNORE signal

Data: NSE Bhav Copy CSV published at ~6 PM IST for previous day.
URL: https://archives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
Also: https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F.CSV

Score impact:
  LONG + delivery >80%:  +10 pts (institutions accumulating)
  LONG + delivery >60%:  +6 pts
  SHORT + delivery >80%: -6 pts (don't fight institutional buying)
  SHORT + delivery >60%: -3 pts
  Any direction + delivery <20%: 0 (pure speculation, ignore)
"""
import logging
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("nse_delivery")
IST = ZoneInfo("Asia/Kolkata")

_cache: Dict[str, float] = {}        # symbol → delivery_pct (0-100)
_cache_date: Optional[str] = None    # date string when cache was loaded
_CACHE_DIR  = Path(__file__).parent.parent / "data"


def _get_bhav_date() -> str:
    """Return the most recent NSE trading date for bhav copy (skip weekends)."""
    now = datetime.now(IST)
    # Bhav copy for today is available after 6 PM IST; else use yesterday
    if now.hour < 18:
        d = now.date() - timedelta(days=1)
    else:
        d = now.date()
    # Skip weekends
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%d%m%Y"), d.strftime("%Y%m%d")


def _download_bhav_copy() -> Dict[str, float]:
    """
    Download NSE equity bhav copy and extract delivery %.
    Tries two URL formats (old and new NSE CDN).
    Returns {symbol: delivery_pct} or empty dict on failure.
    """
    dmy, ymd = _get_bhav_date()
    urls = [
        f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{dmy}.csv",
        f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F.CSV",
    ]

    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.nseindia.com/",
    }

    import pandas as pd
    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            import io
            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = [c.strip().upper() for c in df.columns]

            sym_col  = next((c for c in df.columns if "SYMBOL" in c), None)
            deliv_col = next((c for c in df.columns if "DELIV_PER" in c or "DELPER" in c
                              or "DELIVERYPERC" in c), None)
            series_col = next((c for c in df.columns if "SERIES" in c), None)

            if sym_col is None or deliv_col is None:
                logger.debug(f"Bhav CSV columns not recognised: {list(df.columns[:8])}")
                continue

            # Filter EQ series only
            if series_col:
                df = df[df[series_col].astype(str).str.strip() == "EQ"]

            result = {}
            for _, row in df.iterrows():
                sym = str(row[sym_col]).strip().upper()
                try:
                    pct = float(str(row[deliv_col]).replace(",", "").strip())
                    if 0 <= pct <= 100:
                        result[sym] = pct
                except (ValueError, TypeError):
                    pass

            if result:
                logger.info(f"Delivery data loaded: {len(result)} symbols from {url}")
                return result

        except Exception as e:
            logger.debug(f"Bhav URL {url}: {e}")

    return {}


def _load_delivery_data() -> Dict[str, float]:
    """Load delivery data, using cache if available for today."""
    global _cache, _cache_date
    today = datetime.now(IST).strftime("%Y-%m-%d")

    if _cache_date == today and _cache:
        return _cache

    # Try local file cache first
    cache_file = _CACHE_DIR / f"delivery_{today}.json"
    if cache_file.exists():
        try:
            import json
            data = json.loads(cache_file.read_text())
            _cache = data
            _cache_date = today
            return _cache
        except Exception:
            pass

    # Download fresh
    data = _download_bhav_copy()
    if data:
        _cache = data
        _cache_date = today
        try:
            import json
            _CACHE_DIR.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(data))
        except Exception:
            pass
    return _cache


def get_delivery_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Return (score_delta, reason) based on NSE delivery volume %.
    Fail-open: returns (0.0, "") on any error.
    """
    try:
        data = _load_delivery_data()
        if not data:
            return (0.0, "")

        delivery_pct = data.get(symbol.upper())
        if delivery_pct is None:
            return (0.0, "")

        if direction == "LONG":
            if delivery_pct >= 80:
                return (10.0, f"delivery_NSE {delivery_pct:.0f}% — strong institutional buying")
            if delivery_pct >= 60:
                return (6.0, f"delivery_NSE {delivery_pct:.0f}% — institutional accumulation")
            if delivery_pct < 20:
                return (0.0, "")   # speculation — neutral
        else:  # SHORT
            if delivery_pct >= 80:
                return (-6.0, f"delivery_NSE {delivery_pct:.0f}% — fight institutions SHORT warning")
            if delivery_pct >= 60:
                return (-3.0, f"delivery_NSE {delivery_pct:.0f}% — caution shorting strong delivery")

    except Exception as e:
        logger.debug(f"delivery_score {symbol}: {e}")

    return (0.0, "")


def get_delivery_pct(symbol: str) -> Optional[float]:
    """Return raw delivery % for a symbol. None if unavailable."""
    data = _load_delivery_data()
    return data.get(symbol.upper())
