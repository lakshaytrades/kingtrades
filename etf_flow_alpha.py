"""
etf_flow_alpha.py — ETF Fund Flow Alpha (God Mode)
When institutions buy SPY/QQQ/sector ETFs, individual stocks in those ETFs follow.
Monitor daily flows: large inflows = institutional accumulation.
Data: yfinance ETF price + volume (free, derived flow proxy).

Method: ETF price return vs NAV proxy. Large above-average volume on up day
        = inflow (institutional buying). Below-average volume on down day
        = low-conviction selling.

Key ETFs monitored:
  SPY/QQQ (broad market), XLK (tech), XLF (financials), XLE (energy),
  XLV (healthcare), XLI (industrials), XLY (consumer), SOXS/SOXX (semis)

Score: -8 to +10 per symbol. Cache 30 min. Fail-open.
"""
import logging
import time as _time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("etf_flow_alpha")

_SECTOR_ETF_MAP = {
    "Technology":    "XLK",  "Financial":   "XLF",
    "Energy":        "XLE",  "Healthcare":  "XLV",
    "Industrial":    "XLI",  "Consumer":    "XLY",
    "Semiconductor": "SOXX", "Broad":       "SPY",
}

_SYMBOL_SECTOR: Dict[str, Tuple[str, float]] = {}  # symbol -> (sector, timestamp)
_flow_cache: Dict[str, Tuple[float, float]] = {}    # etf -> (flow_signal, timestamp)
_TTL = 1800.0   # 30-minute cache for ETF flow
_SECTOR_TTL = 14400.0  # 4-hour cache for sector lookup


def _get_etf_flow_signal(etf: str) -> float:
    """
    Download 20d daily data for ETF via yfinance.
    Flow proxy = (today's close - yesterday's close) / yesterday's close × volume / 20d avg volume.
    Positive = inflow, negative = outflow. Normalized to [-1, 1].
    Cache 30 min per ETF.
    """
    now = _time.time()
    cached = _flow_cache.get(etf)
    if cached is not None and now - cached[1] < _TTL:
        return cached[0]

    try:
        import yfinance as yf
        df = yf.download(etf, period="22d", interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 2:
            _flow_cache[etf] = (0.0, now)
            return 0.0

        # Normalise multi-index columns (yfinance sometimes returns MultiIndex)
        import pandas as pd
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        close_col  = "close"  if "close"  in df.columns else "adj close"
        volume_col = "volume" if "volume" in df.columns else None

        if close_col not in df.columns:
            _flow_cache[etf] = (0.0, now)
            return 0.0

        closes = df[close_col].dropna()
        if len(closes) < 2:
            _flow_cache[etf] = (0.0, now)
            return 0.0

        pct_change = float((closes.iloc[-1] - closes.iloc[-2]) / max(abs(closes.iloc[-2]), 1e-9))

        vol_ratio = 1.0
        if volume_col and volume_col in df.columns:
            volumes = df[volume_col].dropna()
            if len(volumes) >= 20 and float(volumes.iloc[-1]) > 0:
                avg_vol_20 = float(volumes.iloc[-20:].mean())
                if avg_vol_20 > 0:
                    vol_ratio = float(volumes.iloc[-1]) / avg_vol_20

        raw_flow = pct_change * vol_ratio

        # Normalize to [-1, 1] using tanh-like clamp
        # Typical daily pct_change ≈ 0.5-2%, vol_ratio ≈ 0.5-3x → raw ≈ 0.01-0.06
        # Scale so 0.05 raw → ~1.0 signal
        normalized = max(-1.0, min(1.0, raw_flow / 0.05))

        _flow_cache[etf] = (normalized, now)
        return normalized

    except Exception as e:
        logger.debug(f"etf_flow: {etf} data error — {e}")
        _flow_cache[etf] = (0.0, now)
        return 0.0


def _get_symbol_sector(symbol: str) -> str:
    """
    Use yfinance Ticker(symbol).info.get("sector", "Unknown"). Cache per symbol 4h.
    """
    now = _time.time()
    cached = _SYMBOL_SECTOR.get(symbol)
    if cached is not None and now - cached[1] < _SECTOR_TTL:
        return cached[0]

    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        sector = info.get("sector", "Unknown") or "Unknown"
        _SYMBOL_SECTOR[symbol] = (sector, now)
        return sector
    except Exception as e:
        logger.debug(f"etf_flow: sector lookup for {symbol} failed — {e}")
        _SYMBOL_SECTOR[symbol] = ("Unknown", now)
        return "Unknown"


def get_etf_flow_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    - Get symbol's sector from yfinance
    - Map to sector ETF
    - Get SPY flow + sector ETF flow
    - Both strong positive + LONG: +10
    - Both strong negative + SHORT: +10
    - Divergence (SPY up, sector down): lower weight
    - Against direction: -8
    - Fail-open (0.0, "")
    """
    try:
        sector = _get_symbol_sector(symbol)
        sector_etf = _SECTOR_ETF_MAP.get(sector, "SPY")

        spy_flow    = _get_etf_flow_signal("SPY")
        sector_flow = _get_etf_flow_signal(sector_etf) if sector_etf != "SPY" else spy_flow

        # Weighted combined flow: SPY=40%, sector=60%
        if sector_etf == "SPY":
            combined = spy_flow
        else:
            combined = spy_flow * 0.4 + sector_flow * 0.6

        # Check divergence penalty
        divergence = (spy_flow > 0 and sector_flow < -0.3) or (spy_flow < 0 and sector_flow > 0.3)
        if divergence:
            combined *= 0.5  # dampen mixed signals

        # Map to score
        if direction == "LONG":
            if combined >= 0.6:
                score  = 10.0
                reason = f"ETF_inflow_strong SPY={spy_flow:+.2f} {sector_etf}={sector_flow:+.2f}"
            elif combined >= 0.3:
                score  = 5.0
                reason = f"ETF_inflow_mod SPY={spy_flow:+.2f} {sector_etf}={sector_flow:+.2f}"
            elif combined >= 0.0:
                score  = 2.0
                reason = f"ETF_inflow_weak SPY={spy_flow:+.2f}"
            elif combined >= -0.3:
                score  = -3.0
                reason = f"ETF_mild_outflow SPY={spy_flow:+.2f}"
            else:
                score  = -8.0
                reason = f"ETF_outflow LONG_risk SPY={spy_flow:+.2f} {sector_etf}={sector_flow:+.2f}"
        else:  # SHORT
            if combined <= -0.6:
                score  = 10.0
                reason = f"ETF_outflow_strong SPY={spy_flow:+.2f} {sector_etf}={sector_flow:+.2f}"
            elif combined <= -0.3:
                score  = 5.0
                reason = f"ETF_outflow_mod SPY={spy_flow:+.2f} {sector_etf}={sector_flow:+.2f}"
            elif combined <= 0.0:
                score  = 2.0
                reason = f"ETF_outflow_weak SPY={spy_flow:+.2f}"
            elif combined <= 0.3:
                score  = -3.0
                reason = f"ETF_mild_inflow SHORT_risk SPY={spy_flow:+.2f}"
            else:
                score  = -8.0
                reason = f"ETF_inflow SHORT_risk SPY={spy_flow:+.2f} {sector_etf}={sector_flow:+.2f}"

        return float(score), reason

    except Exception as e:
        logger.debug(f"get_etf_flow_score({symbol}): {e}")
        return 0.0, ""


def get_market_flow_regime() -> str:
    """
    "RISK_ON" if SPY + QQQ both showing positive flows.
    "RISK_OFF" if both negative.
    "NEUTRAL" otherwise.
    Used as market sentiment context.
    """
    try:
        spy_flow = _get_etf_flow_signal("SPY")
        qqq_flow = _get_etf_flow_signal("QQQ")

        if spy_flow > 0.2 and qqq_flow > 0.2:
            return "RISK_ON"
        elif spy_flow < -0.2 and qqq_flow < -0.2:
            return "RISK_OFF"
        else:
            return "NEUTRAL"
    except Exception as e:
        logger.debug(f"get_market_flow_regime: {e}")
        return "NEUTRAL"
