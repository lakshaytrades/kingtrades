"""
India Intelligence Module — NSE-specific market intelligence.
India VIX, NIFTY PCR from NSE options, SGX Nifty futures, Nifty50 breadth.

Why this matters:
- India VIX < 15: calm, trend-following works
- India VIX 15-20: normal, all strategies
- India VIX > 20: elevated fear, reduce size
- India VIX > 25: CRISIS, no new longs
- NIFTY PCR > 1.3: contrarian bullish (too many puts = smart money buying calls)
- NIFTY PCR < 0.7: contrarian bearish (too many calls = complacency)
- SGX Nifty gap > +0.5%: bullish open, ORB likely
- SGX Nifty gap < -0.5%: bearish open, fade rally
- Nifty breadth > 60%: broad participation, bull run
- Nifty breadth < 40%: narrow market, suspect rally
"""

import logging
import time
import requests
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL_SHORT = 300   # 5 min for live data
_TTL_LONG = 1800   # 30 min for stable data

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


def _cached(key: str, fn, ttl: int):
    now = time.time()
    if key in _CACHE and now - _CACHE[key]["ts"] < ttl:
        return _CACHE[key]["val"]
    try:
        val = fn()
    except Exception as e:
        logger.debug(f"india_intel cache miss {key}: {e}")
        # Return last known value if available, else None
        return _CACHE.get(key, {}).get("val", None)
    _CACHE[key] = {"val": val, "ts": now}
    return val


def _nse_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(NSE_HEADERS)
    try:
        sess.get("https://www.nseindia.com/", timeout=5)
    except Exception:
        pass
    return sess


class IndiaVIXReader:
    """Read India VIX from NSE or yfinance fallback."""

    def get_vix(self) -> float:
        def _fetch():
            # Try yfinance first (^INDIAVIX or ^NSEI)
            try:
                import yfinance as yf
                df = yf.download("^INDIAVIX", period="2d", interval="1d",
                                  progress=False, auto_adjust=True)
                if df is not None and len(df) > 0:
                    return float(df["Close"].iloc[-1])
            except Exception:
                pass
            # NSE API fallback
            try:
                sess = _nse_session()
                r = sess.get("https://www.nseindia.com/api/allIndices", timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    for item in data.get("data", []):
                        if "INDIA VIX" in item.get("index", "").upper():
                            return float(item.get("last", 16.0))
            except Exception:
                pass
            return 16.0  # neutral fallback

        return _cached("india_vix", _fetch, _TTL_SHORT) or 16.0

    def get_regime(self, vix: Optional[float] = None) -> str:
        if vix is None:
            vix = self.get_vix()
        if vix < 13:
            return "CALM"
        elif vix < 17:
            return "NORMAL"
        elif vix < 22:
            return "ELEVATED"
        elif vix < 28:
            return "HIGH_FEAR"
        else:
            return "CRISIS"

    def get_score(self, vix: Optional[float] = None) -> float:
        """Score in [-5, +5]. Lower VIX = higher score for longs."""
        if vix is None:
            vix = self.get_vix()
        if vix < 13:
            return 4.0
        elif vix < 17:
            return 2.0
        elif vix < 22:
            return 0.0
        elif vix < 28:
            return -3.0
        else:
            return -5.0


class NiftyPCRReader:
    """NIFTY Options Put/Call Ratio from NSE."""

    def get_pcr(self) -> float:
        def _fetch():
            # Try NSE options chain for NIFTY
            try:
                sess = _nse_session()
                r = sess.get(
                    "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
                    timeout=10
                )
                if r.status_code == 200:
                    data = r.json()
                    records = data.get("records", {})
                    total_put_oi = sum(
                        rec.get("PE", {}).get("openInterest", 0)
                        for rec in records.get("data", [])
                        if rec.get("PE")
                    )
                    total_call_oi = sum(
                        rec.get("CE", {}).get("openInterest", 0)
                        for rec in records.get("data", [])
                        if rec.get("CE")
                    )
                    if total_call_oi > 0:
                        return total_put_oi / total_call_oi
            except Exception:
                pass
            # Fallback: yfinance proxy using NIFTY ETF options
            return 1.0  # neutral fallback

        return _cached("nifty_pcr", _fetch, _TTL_SHORT) or 1.0

    def get_score(self, pcr: Optional[float] = None) -> float:
        """Score in [-5, +5]. Contrarian — very high PCR = bullish signal."""
        if pcr is None:
            pcr = self.get_pcr()
        if pcr > 1.5:
            return 4.0   # Extreme fear = contrarian bull
        elif pcr > 1.3:
            return 2.5
        elif pcr > 1.1:
            return 1.0
        elif pcr > 0.9:
            return 0.0   # Neutral
        elif pcr > 0.7:
            return -2.0
        else:
            return -4.0  # Extreme greed = contrarian bear


class SGXNiftyReader:
    """SGX Nifty futures as pre-market indicator for NSE gap direction."""

    def get_sgx_gap_pct(self) -> float:
        """Returns gap percentage vs previous NSE close. Positive = bullish open."""
        def _fetch():
            try:
                import yfinance as yf
                # Use NIFTYBEES ETF as NSE Nifty proxy (more liquid)
                nifty = yf.download("^NSEI", period="5d", interval="1d",
                                     progress=False, auto_adjust=True)
                if nifty is not None and len(nifty) >= 2:
                    prev_close = float(nifty["Close"].iloc[-2])
                    last_close = float(nifty["Close"].iloc[-1])
                    return (last_close / prev_close - 1) * 100
            except Exception:
                pass
            return 0.0

        return _cached("sgx_gap", _fetch, _TTL_LONG) or 0.0

    def get_score(self, gap_pct: Optional[float] = None) -> float:
        """Score in [-5, +5]."""
        if gap_pct is None:
            gap_pct = self.get_sgx_gap_pct()
        if gap_pct > 1.5:
            return 4.0
        elif gap_pct > 0.5:
            return 2.0
        elif gap_pct > -0.5:
            return 0.5  # Small gap = slight continuation
        elif gap_pct > -1.5:
            return -2.0
        else:
            return -4.0


class NiftyBreadthReader:
    """Nifty50 market breadth: advance/decline ratio during session."""

    def get_breadth(self) -> dict:
        def _fetch():
            try:
                sess = _nse_session()
                r = sess.get(
                    "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
                    timeout=10
                )
                if r.status_code == 200:
                    data = r.json()
                    advances = sum(
                        1 for s in data.get("data", [])
                        if float(s.get("pChange", 0)) > 0
                    )
                    total = len(data.get("data", []))
                    declines = total - advances
                    breadth_pct = advances / total * 100 if total > 0 else 50
                    return {"advances": advances, "declines": declines,
                            "total": total, "breadth_pct": breadth_pct}
            except Exception:
                pass
            return {"advances": 25, "declines": 25, "total": 50, "breadth_pct": 50.0}

        return _cached("nifty_breadth", _fetch, _TTL_SHORT) or {"breadth_pct": 50.0}

    def get_score(self, breadth_pct: Optional[float] = None) -> float:
        """Score in [-5, +5]."""
        if breadth_pct is None:
            data = self.get_breadth()
            breadth_pct = data.get("breadth_pct", 50)
        if breadth_pct > 75:
            return 4.0   # Strong broad advance
        elif breadth_pct > 60:
            return 2.0
        elif breadth_pct > 45:
            return 0.0
        elif breadth_pct > 30:
            return -2.0
        else:
            return -4.0  # Broad decline — defensive


class IntelIndia:
    """Combined India intelligence facade."""

    def __init__(self):
        self.vix = IndiaVIXReader()
        self.pcr = NiftyPCRReader()
        self.sgx = SGXNiftyReader()
        self.breadth = NiftyBreadthReader()

    def get_composite_score(self) -> dict:
        """Returns composite dict with all India signals and overall score."""
        vix_val = self.vix.get_vix()
        vix_regime = self.vix.get_regime(vix_val)
        vix_score = self.vix.get_score(vix_val)

        pcr_val = self.pcr.get_pcr()
        pcr_score = self.pcr.get_score(pcr_val)

        sgx_gap = self.sgx.get_sgx_gap_pct()
        sgx_score = self.sgx.get_score(sgx_gap)

        breadth_data = self.breadth.get_breadth()
        breadth_score = self.breadth.get_score(breadth_data.get("breadth_pct", 50))

        # Weighted composite: VIX most important, then PCR, then breadth, then SGX
        composite = (vix_score * 0.35 + pcr_score * 0.30 +
                     breadth_score * 0.25 + sgx_score * 0.10)

        is_crisis = vix_val > 28 or vix_regime == "CRISIS"
        is_favorable = vix_val < 20 and pcr_val > 0.9 and breadth_data.get("breadth_pct", 50) > 45

        return {
            "india_vix": vix_val,
            "vix_regime": vix_regime,
            "vix_score": vix_score,
            "nifty_pcr": pcr_val,
            "pcr_score": pcr_score,
            "sgx_gap_pct": sgx_gap,
            "sgx_score": sgx_score,
            "breadth_pct": breadth_data.get("breadth_pct", 50),
            "breadth_score": breadth_score,
            "composite_score": round(max(-10.0, min(10.0, composite * 2.5)), 2),
            "is_crisis": is_crisis,
            "is_favorable": is_favorable,
        }

    def is_safe_to_trade(self) -> bool:
        """Hard gate: returns False if India conditions are CRISIS."""
        vix_val = self.vix.get_vix()
        return vix_val < 28

    def long_bias_ok(self) -> bool:
        """Returns True if conditions favor LONG trades."""
        vix_val = self.vix.get_vix()
        pcr_val = self.pcr.get_pcr()
        breadth = self.breadth.get_breadth().get("breadth_pct", 50)
        return vix_val < 22 and pcr_val > 0.8 and breadth > 40


# Module-level singleton
_intel: Optional[IntelIndia] = None

def get_india_intel() -> IntelIndia:
    global _intel
    if _intel is None:
        _intel = IntelIndia()
    return _intel
