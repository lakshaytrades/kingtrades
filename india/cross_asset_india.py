"""
cross_asset_india.py — Cross-Asset Correlation Engine for NSE India
Renaissance Technologies secret: most equity moves are predicted by cross-asset flows
hours before they appear on the stock chart.

NSE India has unique cross-asset relationships:
  USD/INR   → FII flows, import cost inflation, IT sector bias
  Crude Oil → OMC/airlines/paints/FMCG; >$90 = inflationary headwind
  Gold      → fear gauge; rising gold = defensive rotation away from equities
  SGX Nifty → overnight sentiment; premium/discount shows gap direction
  US VIX    → global risk-off; >25 = no LONG on beta names
  DXY       → dollar strength = FII outflow from India = broad market headwind
  10Y UST   → rising yields = P/E compression = broad equity headwind

Fail-open: returns neutral (0, "") on any network/parse error.
"""
import logging
import time as _time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("cross_asset")
IST = ZoneInfo("Asia/Kolkata")

# In-process cache: {symbol: (timestamp, value)}
_cache: Dict[str, Tuple[float, float]] = {}
_CACHE_TTL_SECONDS = 300   # refresh every 5 minutes max

# Sector → impact of crude/USD changes
_CRUDE_SENSITIVE_LONG  = {"OMC", "OIL_GAS", "OILGAS"}         # crude up = good for upstream
_CRUDE_SENSITIVE_SHORT = {"AVIATION", "PAINTS", "FMCG", "TYRES", "CHEMICALS"}
_USD_SENSITIVE_LONG    = {"IT", "PHARMA", "TEXTILES", "METALS"}  # INR fall → revenue up
_USD_SENSITIVE_SHORT   = {"BANKING", "NBFC", "REALTY", "AUTO_ANCILLARY"}  # INR fall → import cost


def _fetch_price(ticker_symbol: str) -> Optional[float]:
    """Fetch last price via yfinance with caching. Returns None on failure."""
    now = _time.time()
    if ticker_symbol in _cache:
        ts, val = _cache[ticker_symbol]
        if now - ts < _CACHE_TTL_SECONDS:
            return val

    try:
        import yfinance as yf
        tkr = yf.Ticker(ticker_symbol)
        hist = tkr.history(period="2d", interval="5m")
        if hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])
        _cache[ticker_symbol] = (now, price)
        return price
    except Exception as e:
        logger.debug(f"cross_asset fetch {ticker_symbol}: {e}")
        return None


def _fetch_change_pct(ticker_symbol: str, days: int = 1) -> Optional[float]:
    """Return intraday % change for a cross-asset ticker."""
    try:
        import yfinance as yf
        tkr = yf.Ticker(ticker_symbol)
        hist = tkr.history(period="3d", interval="1h")
        if hist is None or len(hist) < 2:
            return None
        close_now  = float(hist["Close"].iloc[-1])
        close_prev = float(hist["Close"].iloc[-2])
        if close_prev <= 0:
            return None
        return (close_now - close_prev) / close_prev * 100
    except Exception as e:
        logger.debug(f"cross_asset change {ticker_symbol}: {e}")
        return None


# ── Regime state ─────────────────────────────────────────────────────────────

_regime_cache: Optional[dict] = None
_regime_ts: float = 0.0
_REGIME_TTL = 600   # 10 minutes


def get_cross_asset_regime() -> dict:
    """
    Compute a cross-asset regime dict:
      {
        "global_bias": "BULLISH"/"BEARISH"/"NEUTRAL",
        "score_adj": int (-20 to +15),
        "reason": str,
        "inr_chg": float,
        "crude_chg": float,
        "gold_chg": float,
        "us_vix": float,
        "sgx_nifty_prem": float,
      }
    Fail-open: returns NEUTRAL, score_adj=0 on any error.
    """
    global _regime_cache, _regime_ts
    _empty = {"global_bias": "NEUTRAL", "score_adj": 0, "reason": "",
              "inr_chg": 0.0, "crude_chg": 0.0, "gold_chg": 0.0,
              "us_vix": 0.0, "sgx_nifty_prem": 0.0}

    now = _time.time()
    if _regime_cache is not None and now - _regime_ts < _REGIME_TTL:
        return _regime_cache

    try:
        # USD/INR: USDINR=X  (higher = INR weaker = FII outflow risk)
        inr_chg = _fetch_change_pct("USDINR=X") or 0.0
        # Crude: BZ=F (Brent), CL=F (WTI)
        crude_chg = _fetch_change_pct("BZ=F") or 0.0
        # Gold: GC=F
        gold_chg = _fetch_change_pct("GC=F") or 0.0
        # US VIX: ^VIX
        us_vix = _fetch_price("^VIX") or 0.0
        # SGX Nifty premium approximation: ^NSEI vs SGX (^NSGM50)
        sgx_prem = 0.0
        try:
            nsei = _fetch_price("^NSEI") or 0.0
            sgx  = _fetch_price("^NSGM50") or 0.0
            if nsei > 0 and sgx > 0:
                sgx_prem = (sgx - nsei) / nsei * 100
        except Exception:
            pass

        score_adj = 0
        reasons = []

        # 1. India VIX equivalent: US VIX > 25 = global risk-off, no longs on beta
        if us_vix >= 30:
            score_adj -= 15
            reasons.append(f"USVIX={us_vix:.0f} PANIC")
        elif us_vix >= 25:
            score_adj -= 8
            reasons.append(f"USVIX={us_vix:.0f} elevated")
        elif us_vix <= 14:
            score_adj += 4
            reasons.append(f"USVIX={us_vix:.0f} complacent-bull")

        # 2. USD/INR spike = FII outflow, bad for LONG (except IT/pharma)
        if inr_chg > 0.5:   # INR weakening fast
            score_adj -= 6
            reasons.append(f"INR-{inr_chg:.1f}%")
        elif inr_chg > 0.2:
            score_adj -= 3
        elif inr_chg < -0.3:  # INR strengthening = FII inflow
            score_adj += 4
            reasons.append(f"INR+{abs(inr_chg):.1f}% FII")

        # 3. Crude spike = inflationary headwind
        if crude_chg > 2.0:
            score_adj -= 5
            reasons.append(f"crude+{crude_chg:.1f}%")
        elif crude_chg > 1.0:
            score_adj -= 2
        elif crude_chg < -2.0:  # crude falling = demand worry but helps India
            score_adj += 3
            reasons.append(f"crude{crude_chg:.1f}% India+")

        # 4. Gold surge = fear gauge (bad for equities)
        if gold_chg > 1.5:
            score_adj -= 6
            reasons.append(f"gold+{gold_chg:.1f}% fear")
        elif gold_chg > 0.8:
            score_adj -= 3

        # 5. SGX Nifty premium/discount
        if sgx_prem > 0.5:
            score_adj += 5
            reasons.append(f"SGX+{sgx_prem:.1f}%")
        elif sgx_prem < -0.5:
            score_adj -= 5
            reasons.append(f"SGX{sgx_prem:.1f}%")

        # Overall bias
        if score_adj >= 5:
            bias = "BULLISH"
        elif score_adj <= -8:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        result = {
            "global_bias": bias,
            "score_adj": score_adj,
            "reason": " | ".join(reasons) if reasons else "NEUTRAL",
            "inr_chg": inr_chg,
            "crude_chg": crude_chg,
            "gold_chg": gold_chg,
            "us_vix": us_vix,
            "sgx_nifty_prem": sgx_prem,
        }
        _regime_cache = result
        _regime_ts = now
        return result

    except Exception as e:
        logger.debug(f"cross_asset_regime: {e}")
        _regime_cache = _empty
        _regime_ts = now
        return _empty


def get_cross_asset_score(direction: str, sector: str = "") -> Tuple[int, str]:
    """
    Return (score_adj, reason) for a signal given the cross-asset regime.
    For SHORT signals, invert the regime score (bear market = good for shorts).
    """
    try:
        regime = get_cross_asset_regime()
        base_adj = regime["score_adj"]

        # Sector-specific overrides
        sector_upper = sector.upper() if sector else ""
        crude_chg = regime.get("crude_chg", 0.0)
        inr_chg   = regime.get("inr_chg", 0.0)

        # Crude sensitive adjustments
        if crude_chg > 1.5 and direction == "LONG":
            if any(s in sector_upper for s in _CRUDE_SENSITIVE_SHORT):
                base_adj -= 8  # rising crude is bad for these sectors
            elif any(s in sector_upper for s in _CRUDE_SENSITIVE_LONG):
                base_adj += 5  # rising crude is good for upstream
        elif crude_chg < -1.5 and direction == "SHORT":
            if any(s in sector_upper for s in _CRUDE_SENSITIVE_SHORT):
                base_adj += 5  # falling crude is good for cost-sensitive sectors (anti-short)

        # USD/INR sensitive adjustments
        if inr_chg > 0.3 and direction == "LONG":
            if any(s in sector_upper for s in _USD_SENSITIVE_LONG):
                base_adj += 4  # INR weakness = IT/pharma revenue boost (rupee hedged earners)
            elif any(s in sector_upper for s in _USD_SENSITIVE_SHORT):
                base_adj -= 4  # INR weakness = import cost pressure

        # For SHORT signals, invert the base market bias part
        # (if market is broadly bearish, shorts are more valid)
        if direction == "SHORT":
            market_bias_component = regime["score_adj"]
            base_adj = -market_bias_component   # bad for market = good for shorts

        base_adj = max(-20, min(15, base_adj))
        reason = regime["reason"]
        return (base_adj, reason)

    except Exception as e:
        logger.debug(f"get_cross_asset_score: {e}")
        return (0, "")
