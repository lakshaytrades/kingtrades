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


# ── Delivery V2: 5-day trend analysis ────────────────────────────────────── #

_hist_cache: Dict[str, list] = {}   # {symbol: [pct_day1, pct_day2, ...]} newest last


def _get_hist_dates(n: int = 5) -> list:
    """Return last n trading day date strings (DMY format) and (YMD format)."""
    now = datetime.now(IST)
    dates = []
    d = now.date()
    if now.hour < 18:
        d -= timedelta(days=1)
    while len(dates) < n:
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        dates.append((d.strftime("%d%m%Y"), d.strftime("%Y%m%d")))
        d -= timedelta(days=1)
    return list(reversed(dates))   # oldest first


def _load_delivery_history(symbol: str, days: int = 5) -> list:
    """Return list of delivery % values for last `days` trading days (oldest→newest)."""
    sym = symbol.upper()
    cached = _hist_cache.get(sym)
    if cached and len(cached) >= days:
        return cached[-days:]

    import requests, io
    import pandas as pd

    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.nseindia.com/",
    }

    for dmy, ymd in _get_hist_dates(days):
        urls = [
            f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{dmy}.csv",
            f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F.CSV",
        ]
        for url in urls:
            try:
                r = requests.get(url, headers=headers, timeout=10)
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text))
                df.columns = [c.strip().upper() for c in df.columns]
                sym_col   = next((c for c in df.columns if "SYMBOL" in c), None)
                deliv_col = next((c for c in df.columns if "DELIV_PER" in c
                                  or "DELPER" in c or "DELIVERYPERC" in c), None)
                if sym_col and deliv_col:
                    row = df[df[sym_col].astype(str).str.strip().str.upper() == sym]
                    if not row.empty:
                        try:
                            pct = float(str(row[deliv_col].iloc[0]).replace(",", "").strip())
                            if 0 <= pct <= 100:
                                results.append(pct)
                                break
                        except Exception:
                            pass
            except Exception:
                pass
        else:
            continue

    _hist_cache[sym] = results
    return results


def get_delivery_trend(symbol: str) -> dict:
    """
    5-day delivery% trend analysis.
    Returns:
      {"trend": "ACCUMULATING"/"DISTRIBUTING"/"NEUTRAL",
       "score_adj": int,  "pct_today": float, "history": list}
    Fail-open returns neutral.
    """
    _empty = {"trend": "NEUTRAL", "score_adj": 0, "pct_today": 0.0, "history": []}
    try:
        hist = _load_delivery_history(symbol, days=5)
        if len(hist) < 3:
            return _empty

        pct_today = hist[-1]
        trend_score = 0

        # 3 consecutive rising delivery days = ACCUMULATING (+10)
        if all(hist[i] < hist[i + 1] for i in range(len(hist) - 3, len(hist) - 1)):
            trend_score += 10
            trend = "ACCUMULATING"
        # 3 consecutive falling delivery days = DISTRIBUTING (-5)
        elif all(hist[i] > hist[i + 1] for i in range(len(hist) - 3, len(hist) - 1)):
            trend_score -= 5
            trend = "DISTRIBUTING"
        else:
            trend = "NEUTRAL"

        # Delivery % > 30-day avg by 50%: institutional accumulation (+8)
        avg_5d = sum(hist) / len(hist)
        if avg_5d > 0 and pct_today > avg_5d * 1.5 and pct_today > 40:
            trend_score += 8

        # Block signal if delivery < 15% (pure speculation)
        if pct_today < 15:
            return {"trend": "NOISE", "score_adj": -999, "pct_today": pct_today, "history": hist}

        return {
            "trend": trend,
            "score_adj": trend_score,
            "pct_today": round(pct_today, 1),
            "history": hist,
        }

    except Exception as e:
        logger.debug(f"get_delivery_trend {symbol}: {e}")
        return _empty


def get_delivery_score_v2(symbol: str, direction: str) -> tuple:
    """
    Enhanced delivery score combining today's % + 5-day trend.
    Returns (score_delta, reason). Fail-open: (0.0, "").
    """
    try:
        today_score, today_reason = get_delivery_score(symbol, direction)
        trend = get_delivery_trend(symbol)

        if trend.get("score_adj") == -999:
            return (0.0, f"DELIVERY_NOISE {trend['pct_today']:.0f}% — skip")

        trend_adj  = trend.get("score_adj", 0)
        trend_name = trend.get("trend", "NEUTRAL")

        if direction == "LONG":
            final = today_score + (trend_adj if trend_adj > 0 else 0)
        else:
            final = today_score - (trend_adj if trend_adj > 0 else 0)

        reason = today_reason
        if trend_name != "NEUTRAL":
            reason = (reason + f" | {trend_name}").strip(" | ")

        return (final, reason)

    except Exception as e:
        logger.debug(f"get_delivery_score_v2 {symbol}: {e}")
        return (0.0, "")
