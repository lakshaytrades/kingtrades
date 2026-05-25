"""
sector_rs.py — Sector-Relative Strength Scorer (Top-1% Edge)

Top-1% traders don't just compare a stock to SPY.
They compare it to its SECTOR ETF (XLK, XLF, XLY, XLE, etc.)

Why it matters:
  - NVDA +1% when XLK is +0.2% = massive outperformance = institutional buying
  - NVDA +1% when XLK is +1.5% = lagging sector = fade signal
  - Stock must LEAD its sector to be worth trading

RS Score:
  Positive (+5 to +15): Stock outperforming sector → add to signal score
  Negative (-5 to -15): Stock lagging sector → subtract from signal score
  Near zero (-4 to +4): Neutral — no adjustment

Also detects:
  - Sector leadership rotation (hot sector emerging)
  - Sector breakdown (avoid stocks in weak sector)
  - Cross-sector divergence (tech strong, energy weak = avoid energy longs)
"""

import logging
import time
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ── GICS Sector → ETF mapping ─────────────────────────────────────────────────
SECTOR_ETF_MAP: Dict[str, str] = {
    # Technology
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK", "INTC": "XLK",
    "QCOM": "XLK", "AVGO": "XLK", "ARM": "XLK", "TXN": "XLK", "MCHP": "XLK",
    "MU": "XLK", "LRCX": "XLK", "KLAC": "XLK", "AMAT": "XLK", "ASML": "XLK",
    "TSM": "XLK", "SMCI": "XLK", "MRVL": "XLK", "ON": "XLK",
    # AI/Cloud SaaS (part of Technology + Communication)
    "GOOGL": "XLC", "GOOG": "XLC", "META": "XLC",
    "NFLX": "XLC", "SNAP": "XLC",
    # AI/Software (Technology)
    "PLTR": "XLK", "CRWD": "XLK", "PANW": "XLK", "ZS": "XLK", "DDOG": "XLK",
    "NET": "XLK", "NOW": "XLK", "SNOW": "XLK", "TEAM": "XLK", "HUBS": "XLK",
    "OKTA": "XLK", "MDB": "XLK", "GTLB": "XLK", "U": "XLK",
    "AI": "XLK", "SOUN": "XLK", "BBAI": "XLK",
    # Consumer Discretionary
    "AMZN": "XLY", "TSLA": "XLY", "UBER": "XLY", "LYFT": "XLY",
    "SHOP": "XLY", "ABNB": "XLY", "MELI": "XLY", "RBLX": "XLY",
    "DASH": "XLY", "YELP": "XLY",
    # Financials
    "JPM": "XLF", "GS": "XLF", "MS": "XLF", "BAC": "XLF",
    "SOFI": "XLF", "HOOD": "XLF", "AFRM": "XLF",
    "SQ": "XLF", "PYPL": "XLF", "V": "XLF", "MA": "XLF",
    "COIN": "XLF",
    # Energy
    "XOM": "XLE", "CVX": "XLE", "OXY": "XLE", "SLB": "XLE", "MPC": "XLE",
    # Healthcare
    "MRNA": "XLV", "LLY": "XLV", "NVO": "XLV", "VKTX": "XLV", "RXRX": "XLV",
    "HIMS": "XLV",
    # Industrials
    "GE": "XLI", "CAT": "XLI", "LMT": "XLI", "RTX": "XLI", "NOC": "XLI",
    # Crypto proxies → treat as XLK (high-beta tech)
    "MSTR": "XLK", "MARA": "XLK", "RIOT": "XLK", "HUT": "XLK",
    "CLSK": "XLK", "BTBT": "XLK", "CIFR": "XLK",
    # EV/Clean energy → Consumer Discretionary
    "RIVN": "XLY", "LCID": "XLY", "NIO": "XLY",
    "PLUG": "XLI", "FSLR": "XLI", "ENPH": "XLI",
    # Leveraged ETFs → use underlying benchmark
    "TQQQ": "QQQ", "QQQ": "QQQ", "SPY": "SPY", "IWM": "IWM",
    "SPXL": "SPY", "SOXL": "XLK", "TECL": "XLK", "FNGU": "XLK",
}

# ── Price change cache (keyed by symbol, 5-min TTL) ──────────────────────────
_RS_CACHE: Dict[str, Tuple[float, float]] = {}   # symbol → (change_pct, timestamp)
_RS_CACHE_TTL = 300    # 5 minutes

# ── Sector momentum cache (keyed by ETF, 5-min TTL) ──────────────────────────
_SECTOR_CACHE: Dict[str, Tuple[float, float]] = {}


def _get_change_pct(symbol: str, lookback_bars: int = 12) -> float:
    """
    Get recent momentum for a symbol (% change over last N 5-min bars).
    Uses Alpaca data fetcher for stocks, cached aggressively.
    Returns 0.0 on failure (never blocks a trade).
    """
    cached = _RS_CACHE.get(symbol)
    if cached and (time.monotonic() - cached[1]) < _RS_CACHE_TTL:
        return cached[0]

    try:
        from data_fetch_alpaca import get_data_fetcher
        fetcher = get_data_fetcher()
        df = fetcher.get_historical_bars(symbol, "5Min", limit=lookback_bars + 2)
        if df is None or len(df) < 3:
            return 0.0

        curr  = float(df["close"].iloc[-1])
        ref   = float(df["close"].iloc[-lookback_bars]) if len(df) >= lookback_bars else float(df["close"].iloc[0])
        if ref <= 0:
            return 0.0

        pct = round((curr - ref) / ref * 100, 3)
        _RS_CACHE[symbol] = (pct, time.monotonic())
        return pct

    except Exception as e:
        logger.debug(f"sector_rs._get_change_pct({symbol}): {e}")
        return 0.0


def _get_sector_change(sector_etf: str, lookback_bars: int = 12) -> float:
    """Get sector ETF momentum (cached)."""
    cached = _SECTOR_CACHE.get(sector_etf)
    if cached and (time.monotonic() - cached[1]) < _RS_CACHE_TTL:
        return cached[0]

    pct = _get_change_pct(sector_etf, lookback_bars)
    _SECTOR_CACHE[sector_etf] = (pct, time.monotonic())
    return pct


def get_sector_rs_score(symbol: str, direction: str,
                         lookback_bars: int = 12) -> Tuple[float, str]:
    """
    Compute sector-relative strength score for a signal.

    Args:
        symbol:        Stock symbol (e.g. "NVDA")
        direction:     "LONG" or "SHORT"
        lookback_bars: Number of 5-min bars for momentum (default 12 = 60 min)

    Returns:
        (score_delta, reason_str)
        score_delta: [-15, +15] — add to filter_result.final_score
        reason_str:  description for rationale
    """
    sector_etf = SECTOR_ETF_MAP.get(symbol)
    if not sector_etf or sector_etf in ("SPY", "QQQ", "IWM"):
        # Broad index or unknown — skip RS adjustment
        return 0.0, ""

    stock_chg  = _get_change_pct(symbol, lookback_bars)
    sector_chg = _get_sector_change(sector_etf, lookback_bars)

    if stock_chg == 0.0 and sector_chg == 0.0:
        return 0.0, ""

    # Relative strength = stock outperformance vs sector
    rs = stock_chg - sector_chg

    # Scale: ±0.5% RS = ±5pts, ±1.0% RS = ±10pts, ±1.5%+ RS = ±15pts
    raw_delta = rs * 10.0   # linear scaling

    if direction == "LONG":
        score_delta = max(-15.0, min(15.0, raw_delta))
        if rs >= 1.0:
            reason = f"SECTOR LEADER: {symbol} +{stock_chg:.2f}% vs {sector_etf} +{sector_chg:.2f}% (+{rs:.2f}% RS)"
        elif rs >= 0.3:
            reason = f"Sector outperform: {symbol} vs {sector_etf} (+{rs:.2f}% RS)"
        elif rs <= -1.0:
            reason = f"SECTOR LAGGARD: {symbol} +{stock_chg:.2f}% vs {sector_etf} +{sector_chg:.2f}% ({rs:.2f}% RS)"
        elif rs <= -0.3:
            reason = f"Sector underperform: {symbol} vs {sector_etf} ({rs:.2f}% RS)"
        else:
            score_delta = 0.0
            reason = ""
    else:  # SHORT
        # For shorts: stock falling more than sector = strong short
        #             stock falling less than sector = weak short
        score_delta = max(-15.0, min(15.0, -raw_delta))
        if rs <= -1.0:
            reason = f"SHORT LEADER: {symbol} {stock_chg:.2f}% vs {sector_etf} {sector_chg:.2f}% ({rs:.2f}% RS)"
        elif rs <= -0.3:
            reason = f"Sector weak short: {symbol} vs {sector_etf} ({rs:.2f}% RS)"
        elif rs >= 1.0:
            reason = f"RESIST SHORT: {symbol} outperforming sector despite short signal ({rs:.2f}% RS)"
        else:
            score_delta = 0.0
            reason = ""

    if score_delta != 0.0:
        logger.debug(
            f"sector_rs: {symbol} ({direction}) sector={sector_etf} "
            f"stock={stock_chg:+.2f}% sector={sector_chg:+.2f}% RS={rs:+.2f}% → Δscore={score_delta:+.1f}"
        )

    return round(score_delta, 1), reason


def get_hot_sectors(top_n: int = 3) -> Dict[str, float]:
    """
    Return top N performing sector ETFs by 1-hour momentum.
    Used by main.py for watchlist prioritization.
    Returns {etf: change_pct} sorted descending.
    """
    sectors = ["XLK", "XLF", "XLY", "XLE", "XLV", "XLI", "XLB", "XLRE", "XLC", "XLU"]
    scores  = {}
    for etf in sectors:
        pct = _get_sector_change(etf, lookback_bars=12)
        if pct != 0.0:
            scores[etf] = pct
    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n])


def get_cold_sectors(bottom_n: int = 2) -> Dict[str, float]:
    """Return bottom N performing sectors — stocks in these sectors are short candidates."""
    sectors = ["XLK", "XLF", "XLY", "XLE", "XLV", "XLI", "XLB", "XLRE", "XLC", "XLU"]
    scores  = {}
    for etf in sectors:
        pct = _get_sector_change(etf, lookback_bars=12)
        if pct != 0.0:
            scores[etf] = pct
    return dict(sorted(scores.items(), key=lambda x: x[1])[:bottom_n])
