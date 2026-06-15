"""
breadth_sector_filter.py — Market Breadth + Sector Momentum Filter

Works on already-loaded OHLCV data (no live API calls needed).
Provides market breadth scoring and sector momentum to reduce counter-trend trades.

Key functions:
- compute_market_breadth(data_dict, now_ts) → BreadthState
- get_sector_bias(data_dict, now_ts) → dict
- get_breadth_score_boost(symbol, breadth_state, sector_bias, direction) → (int, str)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ── NSE Sector Map ──────────────────────────────────────────────────────────
# Maps stock symbol → sector (covers Nifty50 + Next50 major stocks)
NSE_SECTOR_MAP: Dict[str, str] = {
    # Banking & Finance
    "HDFCBANK": "BANK",   "ICICIBANK": "BANK",  "KOTAKBANK": "BANK",
    "AXISBANK": "BANK",   "SBIN": "BANK",        "BANDHANBNK": "BANK",
    "FEDERALBNK": "BANK", "IDFCFIRSTB": "BANK",  "INDUSINDBK": "BANK",
    "BANKBARODA": "BANK", "PNB": "BANK",          "CANBK": "BANK",
    "BAJFINANCE": "FINANCE", "BAJAJFINSV": "FINANCE", "HDFCLIFE": "FINANCE",
    "SBILIFE": "FINANCE", "ICICIGI": "FINANCE",   "LICI": "FINANCE",
    "MUTHOOTFIN": "FINANCE", "CHOLAFIN": "FINANCE",
    # IT / Technology
    "TCS": "IT",          "INFY": "IT",          "WIPRO": "IT",
    "HCLTECH": "IT",      "TECHM": "IT",         "LTIM": "IT",
    "MPHASIS": "IT",      "COFORGE": "IT",       "PERSISTENT": "IT",
    "LTTS": "IT",
    # Pharma / Healthcare
    "SUNPHARMA": "PHARMA", "APOLLOHOSP": "PHARMA",  "CIPLA": "PHARMA",
    "DIVISLAB": "PHARMA",  "APOLLOHOSP": "PHARMA", "MANKIND": "PHARMA",
    "TORNTPHARM": "PHARMA", "AUROPHARMA": "PHARMA",
    # Auto / EV
    "MARUTI": "AUTO",     "BAJAJ-AUTO": "AUTO",  "M&M": "AUTO",
    "BAJAJ-AUTO": "AUTO", "EICHERMOT": "AUTO",   "HEROMOTOCO": "AUTO",
    "TVSMOTOR": "AUTO",   "ASHOKLEY": "AUTO",
    # Metals / Mining
    "TATASTEEL": "METAL", "HINDALCO": "METAL",   "JSWSTEEL": "METAL",
    "SAIL": "METAL",      "VEDL": "METAL",        "NMDC": "METAL",
    "COALINDIA": "METAL",
    # Energy / Oil
    "RELIANCE": "ENERGY", "ONGC": "ENERGY",      "BPCL": "ENERGY",
    "IOC": "ENERGY",      "NTPC": "ENERGY",       "POWERGRID": "ENERGY",
    "TATAPOWER": "ENERGY", "TRENT": "RETAIL", "POLYCAB": "INDUSTRIALS",
    # FMCG / Consumer
    "HINDUNILVR": "FMCG", "ITC": "FMCG",         "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG",  "DABUR": "FMCG",        "GODREJCP": "FMCG",
    "MARICO": "FMCG",     "TATACONSUM": "FMCG",
    # Infra / Cement
    "ULTRACEMCO": "INFRA", "SHREECEM": "INFRA",   "GRASIM": "INFRA",
    "LT": "INFRA",         "BHEL": "INFRA",        "CUMMINSIND": "INDUSTRIALS",
    "SIEMENS": "INFRA",
    # Telecom / Media
    "BHARTIARTL": "TELECOM", "IDEA": "TELECOM",
    # Chemicals
    "PIDILITIND": "CHEM", "AARTIIND": "CHEM",    "DEEPAKNTR": "CHEM",
    "SRF": "CHEM",
}

# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class BreadthState:
    """Snapshot of market breadth at a point in time."""
    pct_above_vwap:  float = 0.5   # 0.0-1.0
    pct_above_ema21: float = 0.5   # 0.0-1.0
    pct_rising_5bar: float = 0.5   # % of stocks up vs 5 bars ago
    bias: str = "NEUTRAL"          # "BULL", "BEAR", "NEUTRAL"
    n_symbols: int = 0             # how many symbols used
    timestamp: Optional[datetime] = None


@dataclass
class SectorBias:
    """Momentum score for each sector."""
    scores: Dict[str, float] = field(default_factory=dict)   # sector → momentum score
    top_sectors:    List[str] = field(default_factory=list)   # bull sectors
    bottom_sectors: List[str] = field(default_factory=list)   # bear sectors


# ── Core Functions ────────────────────────────────────────────────────────────


def compute_market_breadth(
    data_dict: Dict[str, pd.DataFrame],
    now_ts,
) -> BreadthState:
    """
    Compute market breadth from already-loaded 5-min OHLCV DataFrames.

    data_dict: {symbol: df} where df has columns [open, high, low, close, volume]
               and may have [vwap, ema21] pre-computed.
    now_ts: current bar timestamp (pd.Timestamp)

    Returns BreadthState with market bias.
    """
    n_above_vwap  = 0
    n_above_ema21 = 0
    n_rising_5bar = 0
    n_total       = 0

    for sym, df in data_dict.items():
        try:
            if now_ts not in df.index:
                continue
            idx = df.index.get_loc(now_ts)
            if idx < 5:
                continue

            row  = df.iloc[idx]
            c    = float(row.get("close", 0) or 0)
            if c <= 0:
                continue

            n_total += 1

            # VWAP comparison
            vwap = float(row.get("vwap", 0) or 0)
            if vwap > 0 and c > vwap:
                n_above_vwap += 1
            elif vwap <= 0:
                # Compute simple proxy: close vs daily open (first bar close)
                first_close = float(df.iloc[max(0, idx - idx % 75)].get("close", c))
                if c > first_close:
                    n_above_vwap += 1

            # EMA21 comparison
            ema21 = float(row.get("ema21", 0) or 0)
            if ema21 > 0 and c > ema21:
                n_above_ema21 += 1

            # 5-bar momentum
            c5 = float(df.iloc[idx - 5].get("close", 0) or 0)
            if c5 > 0 and c > c5:
                n_rising_5bar += 1

        except Exception:
            continue

    if n_total == 0:
        return BreadthState(n_symbols=0)

    pct_vwap  = n_above_vwap  / n_total
    pct_ema21 = n_above_ema21 / n_total
    pct_5bar  = n_rising_5bar / n_total

    # Composite breadth score
    composite = (pct_vwap * 0.4 + pct_ema21 * 0.35 + pct_5bar * 0.25)

    if composite >= 0.62:
        bias = "BULL"
    elif composite <= 0.38:
        bias = "BEAR"
    else:
        bias = "NEUTRAL"

    return BreadthState(
        pct_above_vwap  = pct_vwap,
        pct_above_ema21 = pct_ema21,
        pct_rising_5bar = pct_5bar,
        bias            = bias,
        n_symbols       = n_total,
        timestamp       = now_ts,
    )


def get_sector_bias(
    data_dict: Dict[str, pd.DataFrame],
    now_ts,
    sector_map: Optional[Dict[str, str]] = None,
) -> SectorBias:
    """
    Compute momentum score for each sector from loaded data.

    Returns SectorBias with top_sectors (bull) and bottom_sectors (bear).
    """
    if sector_map is None:
        sector_map = NSE_SECTOR_MAP

    sector_scores: Dict[str, List[float]] = {}

    for sym, df in data_dict.items():
        try:
            sector = sector_map.get(sym.upper(), "OTHER")

            if now_ts not in df.index:
                continue
            idx = df.index.get_loc(now_ts)
            if idx < 10:
                continue

            row = df.iloc[idx]
            c   = float(row.get("close", 0) or 0)
            if c <= 0:
                continue

            ema9  = float(row.get("ema9",  c) or c)
            ema21 = float(row.get("ema21", c) or c)
            ema50 = float(row.get("ema50", c) or c)

            # Momentum score: EMA alignment + 5-bar return
            mom = 0.0
            if ema9  > 0: mom += (c - ema9)  / ema9
            if ema21 > 0: mom += (c - ema21) / ema21 * 0.7
            if ema50 > 0: mom += (c - ema50) / ema50 * 0.4

            c5 = float(df.iloc[idx - 5].get("close", c) or c)
            if c5 > 0:
                mom += (c - c5) / c5 * 2.0  # 5-bar return gets 2x weight

            if sector not in sector_scores:
                sector_scores[sector] = []
            sector_scores[sector].append(mom)

        except Exception:
            continue

    # Average score per sector
    avg_scores: Dict[str, float] = {}
    for sector, scores in sector_scores.items():
        if scores:
            avg_scores[sector] = statistics.mean(scores)

    if not avg_scores:
        return SectorBias()

    # Sort sectors by score
    sorted_sectors = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)
    n_sectors      = len(sorted_sectors)
    top_n          = max(1, min(3, n_sectors // 2))

    top_sectors    = [s for s, _ in sorted_sectors[:top_n]]
    bottom_sectors = [s for s, _ in sorted_sectors[-top_n:]]

    return SectorBias(
        scores         = avg_scores,
        top_sectors    = top_sectors,
        bottom_sectors = bottom_sectors,
    )


def get_breadth_score_boost(
    symbol:        str,
    breadth_state: BreadthState,
    sector_bias:   SectorBias,
    direction:     str,
    sector_map:    Optional[Dict[str, str]] = None,
) -> Tuple[int, str]:
    """
    Returns (score_delta, reason) based on alignment of trade direction
    with market breadth and sector momentum.

    score_delta: positive = confirms signal; negative = against signal (anti-signal)
    direction:   "LONG" or "SHORT"
    """
    if sector_map is None:
        sector_map = NSE_SECTOR_MAP

    score  = 0
    reason_parts = []

    try:
        is_long  = direction.upper() == "LONG"
        is_short = not is_long

        # ── Market Breadth Alignment ─────────────────────────────────────────
        if breadth_state.n_symbols >= 5:
            if breadth_state.bias == "BULL" and is_long:
                score += 8
                reason_parts.append("BREADTH_BULL_LONG")
            elif breadth_state.bias == "BEAR" and is_short:
                score += 8
                reason_parts.append("BREADTH_BEAR_SHORT")
            elif breadth_state.bias == "BULL" and is_short:
                score -= 6   # Counter-breadth SHORT in bull market
                reason_parts.append("BREADTH_BULL_ANTI_SHORT")
            elif breadth_state.bias == "BEAR" and is_long:
                score -= 6   # Counter-breadth LONG in bear market
                reason_parts.append("BREADTH_BEAR_ANTI_LONG")

        # ── Sector Momentum Alignment ─────────────────────────────────────────
        sector = sector_map.get(symbol.upper(), "OTHER")
        if sector != "OTHER":
            if is_long and sector in sector_bias.top_sectors:
                score += 6
                reason_parts.append(f"SECTOR_BULL_{sector}")
            elif is_short and sector in sector_bias.bottom_sectors:
                score += 6
                reason_parts.append(f"SECTOR_BEAR_{sector}")
            elif is_long and sector in sector_bias.bottom_sectors:
                score -= 5
                reason_parts.append(f"SECTOR_ANTI_LONG_{sector}")
            elif is_short and sector in sector_bias.top_sectors:
                score -= 5
                reason_parts.append(f"SECTOR_ANTI_SHORT_{sector}")

    except Exception:
        pass

    reason = "+".join(reason_parts) if reason_parts else ""
    return score, reason


# ── Breadth Cache (for efficiency in backtest) ────────────────────────────────

class BreadthCache:
    """
    Caches breadth computation every N minutes to avoid O(N^2) per-bar calculation.

    Usage:
        cache = BreadthCache(refresh_minutes=15)
        breadth = cache.get(data_dict, now_ts)
        sector  = cache.get_sector(data_dict, now_ts)
    """

    def __init__(self, refresh_minutes: int = 15):
        self._refresh_mins   = refresh_minutes
        self._last_breadth_ts = None
        self._last_sector_ts  = None
        self._breadth_state   = BreadthState()
        self._sector_bias     = SectorBias()

    def get(self, data_dict: Dict[str, pd.DataFrame], now_ts) -> BreadthState:
        try:
            if (self._last_breadth_ts is None or
                    (now_ts - self._last_breadth_ts).total_seconds() >= self._refresh_mins * 60):
                self._breadth_state   = compute_market_breadth(data_dict, now_ts)
                self._last_breadth_ts = now_ts
        except Exception:
            pass
        return self._breadth_state

    def get_sector(self, data_dict: Dict[str, pd.DataFrame], now_ts) -> SectorBias:
        try:
            if (self._last_sector_ts is None or
                    (now_ts - self._last_sector_ts).total_seconds() >= self._refresh_mins * 60):
                self._sector_bias    = get_sector_bias(data_dict, now_ts)
                self._last_sector_ts = now_ts
        except Exception:
            pass
        return self._sector_bias
