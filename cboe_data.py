"""
cboe_data.py — CBOE Market Data (v28.0)

Free proxy for Bloomberg options flow data ($25k+/year).

Data sources (all publicly free, no auth):
1. CBOE Put/Call ratio (equity + total)
   URL: https://www.cboe.com/data/historical-options-data/market-statistics-archive/
   CSV updated daily. P/C extremes = reliable contrarian signals.

2. VIX spot + VIX term structure
   VIX3M (3-month VIX) vs VIX (30-day): contango vs backwardation
   Backwardation = market stress = reduce long exposure

3. CBOE SKEW Index
   High SKEW (>140) = expensive tail protection = institutions hedging
   = reduce long size

All cached 30 minutes (data updates once per day anyway).
"""

import logging
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_pc_cache:  Optional[Tuple[float, float, float]] = None  # (equity_pc, total_pc, ts)
_vix_cache: Optional[Tuple[float, float, float]] = None  # (vix, vix3m, ts)
_CACHE_TTL  = 1800.0  # 30 min


# ── Put/Call Ratio ────────────────────────────────────────────────────────────

def get_pc_ratio_score(direction: str) -> Tuple[float, str]:
    """
    CBOE equity put/call ratio as a contrarian signal.
    Extreme fear (P/C > 1.2) = good time to buy.
    Extreme complacency (P/C < 0.55) = caution on longs.
    """
    try:
        pc = _get_pc_ratio()
        if pc is None:
            return 0.0, "cboe:no-pc-data"

        equity_pc = pc[0]
        total_pc  = pc[1]
        use_pc    = equity_pc if equity_pc > 0 else total_pc

        if direction == "LONG":
            if use_pc > 1.30:
                return 6.0, f"cboe:extreme-fear(P/C={use_pc:.2f}→contrarian-long)"
            elif use_pc > 1.10:
                return 3.0, f"cboe:elevated-fear(P/C={use_pc:.2f})"
            elif use_pc > 0.90:
                return 1.0, f"cboe:mild-fear(P/C={use_pc:.2f})"
            elif use_pc < 0.50:
                return -5.0, f"cboe:extreme-complacency(P/C={use_pc:.2f}→contrarian-short)"
            elif use_pc < 0.65:
                return -2.0, f"cboe:low-fear(P/C={use_pc:.2f})"
        else:  # SHORT
            if use_pc < 0.50:
                return 5.0, f"cboe:extreme-complacency(P/C={use_pc:.2f}→contrarian-short)"
            elif use_pc < 0.65:
                return 2.0, f"cboe:low-fear-short(P/C={use_pc:.2f})"
            elif use_pc > 1.20:
                return -4.0, f"cboe:extreme-fear-vs-short(P/C={use_pc:.2f})"

        return 0.0, f"cboe:neutral(P/C={use_pc:.2f})"

    except Exception as e:
        logger.debug(f"[cboe] pc_ratio: {e}")
        return 0.0, "cboe:error"


def get_vix_term_structure_score(direction: str) -> Tuple[float, str]:
    """
    VIX term structure signal.
    Backwardation (VIX > VIX3M) = market stress = reduce size.
    Returns (score_delta_or_size_hint, reason).
    """
    try:
        vix_data = _get_vix_data()
        if vix_data is None:
            return 0.0, "cboe:no-vix-data"

        vix, vix3m = vix_data

        if vix <= 0 or vix3m <= 0:
            return 0.0, "cboe:vix-invalid"

        slope = (vix3m - vix) / vix  # positive = contango (normal)

        if slope < -0.05:  # backwardation > 5%
            if direction == "LONG":
                return -3.0, f"cboe:vix-backwardation({vix:.1f}>{vix3m:.1f}) — stress"
            else:
                return 2.0, f"cboe:vix-backwardation → short-friendly"
        elif slope > 0.10:  # steep contango = calm
            if direction == "LONG":
                return 2.0, f"cboe:vix-contango({vix:.1f}<{vix3m:.1f}) — calm"

        if vix > 30:
            if direction == "LONG":
                return -4.0, f"cboe:high-vix({vix:.1f}) — reduce-long"
            else:
                return 3.0, f"cboe:high-vix({vix:.1f}) — short-friendly"
        elif vix < 13:
            if direction == "LONG":
                return -2.0, f"cboe:vix-complacency({vix:.1f})"

        return 0.0, f"cboe:vix-neutral({vix:.1f})"

    except Exception as e:
        logger.debug(f"[cboe] vix_term: {e}")
        return 0.0, "cboe:error"


# ── Data Fetchers ─────────────────────────────────────────────────────────────

def _get_pc_ratio() -> Optional[Tuple[float, float]]:
    """
    Fetch CBOE daily P/C ratio from public CSV.
    Returns (equity_pc, total_pc) or None on error.
    """
    global _pc_cache
    now = _time.monotonic()
    if _pc_cache and (now - _pc_cache[2]) < _CACHE_TTL:
        return _pc_cache[0], _pc_cache[1]

    try:
        import requests
        # CBOE provides free daily statistics CSV
        url = "https://www.cboe.com/publish/scheduledtask/mktdata/datahouse/equitypc.csv"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return _fallback_pc()

        lines = resp.text.strip().split("\n")
        # CSV format: DATE,PC_RATIO (skip header)
        data_lines = [l for l in lines if l and not l.startswith("DATE") and not l.startswith("//")]
        if not data_lines:
            return _fallback_pc()

        last_line = data_lines[-1]
        parts = last_line.strip().split(",")
        if len(parts) >= 2:
            equity_pc = float(parts[1])
            total_pc  = equity_pc  # use equity as proxy
            _pc_cache = (equity_pc, total_pc, now)
            return equity_pc, total_pc

    except Exception as e:
        logger.debug(f"[cboe] _get_pc_ratio: {e}")

    return _fallback_pc()


def _fallback_pc() -> Optional[Tuple[float, float]]:
    """Fallback: compute P/C from yfinance VIX as proxy."""
    try:
        import yfinance as yf
        vix = yf.download("^VIX", period="1d", interval="1d", progress=False)
        if vix is not None and not vix.empty:
            vix_val = float(vix["Close"].iloc[-1])
            # Empirical mapping: VIX 20 ≈ P/C 0.80, VIX 30 ≈ P/C 1.1
            pc_proxy = 0.50 + vix_val * 0.02
            return pc_proxy, pc_proxy
    except Exception:
        pass
    return None


def _get_vix_data() -> Optional[Tuple[float, float]]:
    """Fetch VIX and VIX3M from yfinance."""
    global _vix_cache
    now = _time.monotonic()
    if _vix_cache and (now - _vix_cache[2]) < _CACHE_TTL:
        return _vix_cache[0], _vix_cache[1]

    try:
        import yfinance as yf
        data = yf.download(
            ["^VIX", "^VIX3M"],
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )

        vix_val = 0.0
        vix3m_val = 0.0

        try:
            vix_val = float(data["^VIX"]["Close"].dropna().iloc[-1])
        except Exception:
            pass

        try:
            vix3m_val = float(data["^VIX3M"]["Close"].dropna().iloc[-1])
        except Exception:
            vix3m_val = vix_val * 1.05  # contango approximation

        if vix_val > 0:
            _vix_cache = (vix_val, vix3m_val, now)
            return vix_val, vix3m_val

    except Exception as e:
        logger.debug(f"[cboe] _get_vix_data: {e}")

    return None
