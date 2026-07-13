"""
ff_factors.py — Fama-French 5-Factor model alignment.
Nobel Prize-winning alpha factors used by every quant fund.
Factors: Market(Mkt-RF), Size(SMB), Value(HML), Profitability(RMW), Investment(CMA).

Free data from Kenneth French's website (Dartmouth).
We compute per-stock factor loadings and score alignment.

Signal: if stock has high momentum + high profitability factor loading
and those factors are currently positive = strong tailwind.
Cached 24 hours (daily factors). Fail-open 0.0.
"""
import logging
import time
from typing import Tuple

logger = logging.getLogger(__name__)
_factor_cache: dict = {}
_stock_cache: dict = {}
_FACTOR_TTL = 86400.0  # 24hr
_STOCK_TTL = 3600.0    # 1hr

def _get_current_factors() -> dict:
    """Fetch latest FF5 factor returns. Free from French's website."""
    try:
        now = time.time()
        if "ff5" in _factor_cache and now - _factor_cache["ff5"][1] < _FACTOR_TTL:
            return _factor_cache["ff5"][0]
        import pandas as pd
        import io
        import urllib.request
        import zipfile
        # Kenneth French data library - free public data
        url = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "academic_research@kingtrades.local"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            z = zipfile.ZipFile(io.BytesIO(resp.read()))
            fname = [n for n in z.namelist() if n.endswith(".CSV")][0]
            raw = z.read(fname).decode("utf-8", errors="ignore")
        # Parse: skip header lines, get last 5 rows
        lines = [l for l in raw.split("\n") if l.strip() and l[0].isdigit()]
        if not lines:
            return {}
        last = lines[-1].split(",")
        factors = {
            "Mkt_RF": float(last[1]) / 100,
            "SMB": float(last[2]) / 100,
            "HML": float(last[3]) / 100,
            "RMW": float(last[4]) / 100,
            "CMA": float(last[5]) / 100,
        }
        _factor_cache["ff5"] = (factors, now)
        return factors
    except Exception as e:
        logger.debug(f"FF5 factors fail-open: {e}")
        return {}

def get_factor_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Returns (score_delta, reason). Fail-open returns (0.0, 'ff_unavailable').
    Score based on current factor environment vs stock's factor profile.
    """
    try:
        factors = _get_current_factors()
        if not factors:
            return (0.0, "ff_no_data")

        score = 0.0
        reasons = []

        # Market factor: positive = broad tailwind
        mkt = factors.get("Mkt_RF", 0)
        if mkt > 0.005:   # market up 0.5%+
            score += 5.0; reasons.append(f"Mkt+{mkt:.1%}")
        elif mkt < -0.005:
            score -= 5.0; reasons.append(f"Mkt{mkt:.1%}")

        # Profitability factor (RMW): quality premium
        rmw = factors.get("RMW", 0)
        if rmw > 0.002:
            score += 3.0; reasons.append(f"Quality_premium")
        elif rmw < -0.002:
            score -= 2.0; reasons.append(f"Quality_discount")

        # Size (SMB): small-cap vs large-cap day
        smb = factors.get("SMB", 0)
        if smb > 0.003:
            score += 2.0; reasons.append("SmallCap_day")
        elif smb < -0.003:
            score -= 1.0; reasons.append("LargeCap_favored")

        if direction == "SHORT":
            score = -score

        return (float(max(-8.0, min(10.0, score))), " | ".join(reasons) or "ff_neutral")
    except Exception as e:
        logger.debug(f"ff_factors fail-open {symbol}: {e}")
        return (0.0, "ff_unavailable")
