"""
us_portfolio_intelligence.py — US Bot Portfolio Intelligence (God Mode)
Prevents correlated losses in the Alpaca bot.
3 guards: sector concentration, position correlation, portfolio heat.
Dynamic position sizing based on market regime.
"""
import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("us_portfolio_intel")
_corr_cache: Dict[str, Tuple[float, float]] = {}  # "SYM_A:SYM_B" -> (correlation, timestamp)
_CORR_TTL = 3600.0   # 1-hour cache
_SECTOR_CACHE: Dict[str, Tuple[str, float]] = {}
_SECTOR_TTL = 14400.0  # 4-hour cache for sector lookup

_US_SECTOR_MAP = {
    "AAPL": "Technology",  "MSFT": "Technology",  "NVDA": "Technology",
    "GOOGL": "Technology", "META": "Technology",   "AMZN": "Consumer",
    "TSLA": "Consumer",    "JPM": "Financial",     "BAC": "Financial",
    "GS": "Financial",     "XOM": "Energy",        "CVX": "Energy",
    "JNJ": "Healthcare",   "UNH": "Healthcare",    "PFE": "Healthcare",
    "AMD": "Technology",   "INTC": "Technology",   "QCOM": "Technology",
}


def get_symbol_sector(symbol: str) -> str:
    """
    Check _US_SECTOR_MAP first. If not found, try yfinance Ticker(symbol).info.get("sector", "Unknown").
    Cache 4h.
    """
    # Fast path: static map
    if symbol in _US_SECTOR_MAP:
        return _US_SECTOR_MAP[symbol]

    now = _time.time()
    cached = _SECTOR_CACHE.get(symbol)
    if cached is not None and now - cached[1] < _SECTOR_TTL:
        return cached[0]

    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        sector = info.get("sector", "Unknown") or "Unknown"
        _SECTOR_CACHE[symbol] = (sector, now)
        return sector
    except Exception as e:
        logger.debug(f"us_portfolio_intel: sector lookup for {symbol} failed — {e}")
        _SECTOR_CACHE[symbol] = ("Unknown", now)
        return "Unknown"


def get_correlation(sym_a: str, sym_b: str) -> float:
    """
    Download 20d daily returns for both via yfinance. Compute Pearson correlation.
    Cache 1h. Return 0.0 on failure.
    """
    key = ":".join(sorted([sym_a, sym_b]))
    now = _time.time()
    cached = _corr_cache.get(key)
    if cached is not None and now - cached[1] < _CORR_TTL:
        return cached[0]

    try:
        import yfinance as yf
        import pandas as pd

        df_a = yf.download(sym_a, period="22d", interval="1d", progress=False, auto_adjust=True)
        df_b = yf.download(sym_b, period="22d", interval="1d", progress=False, auto_adjust=True)

        if df_a is None or df_b is None or df_a.empty or df_b.empty:
            _corr_cache[key] = (0.0, now)
            return 0.0

        # Normalise columns
        for _df in [df_a, df_b]:
            if isinstance(_df.columns, pd.MultiIndex):
                _df.columns = [str(c[0]).lower() for c in _df.columns]
            else:
                _df.columns = [str(c).lower() for c in _df.columns]

        col_a = "close" if "close" in df_a.columns else "adj close"
        col_b = "close" if "close" in df_b.columns else "adj close"

        if col_a not in df_a.columns or col_b not in df_b.columns:
            _corr_cache[key] = (0.0, now)
            return 0.0

        ret_a = df_a[col_a].pct_change().dropna()
        ret_b = df_b[col_b].pct_change().dropna()

        # Align by index
        common = ret_a.index.intersection(ret_b.index)
        if len(common) < 5:
            _corr_cache[key] = (0.0, now)
            return 0.0

        corr = float(np.corrcoef(ret_a.loc[common].values, ret_b.loc[common].values)[0, 1])
        if np.isnan(corr):
            corr = 0.0

        _corr_cache[key] = (corr, now)
        return corr

    except Exception as e:
        logger.debug(f"get_correlation({sym_a},{sym_b}): {e}")
        _corr_cache[key] = (0.0, now)
        return 0.0


def check_position_allowed(
    new_symbol: str,
    direction: str,
    open_positions: dict,
    max_sector: int = 2,
    max_corr: float = 0.75,
    max_heat_pct: float = 0.04,
    capital: float = 5000,
) -> Tuple[bool, str]:
    """
    3-rule portfolio gate:
      Rule 1: Sector concentration — max_sector open positions per sector
      Rule 2: Correlation guard — block if r > max_corr with same-direction position
      Rule 3: Portfolio heat — total $ at risk must be < max_heat_pct of capital
    Returns (allowed: bool, reason: str).
    """
    # Rule 1: Sector concentration
    sector = get_symbol_sector(new_symbol)
    sector_count = sum(
        1 for sym in open_positions
        if get_symbol_sector(sym) == sector and sector != "Unknown"
    )
    if sector_count >= max_sector:
        return False, f"sector_limit:{sector}({sector_count})"

    # Rule 2: Correlation guard
    for sym, pos in open_positions.items():
        corr = get_correlation(new_symbol, sym)
        pos_dir = (
            getattr(pos, "direction", "LONG")
            if hasattr(pos, "direction")
            else "LONG"
        )
        if corr > max_corr and direction == pos_dir:
            return False, f"corr_guard:{new_symbol}↔{sym} r={corr:.2f}"

    # Rule 3: Portfolio heat (% of capital at risk)
    total_risk = sum(
        abs(getattr(p, "entry_price", 0) - getattr(p, "stop_loss", 0))
        * getattr(p, "quantity", 0)
        for p in open_positions.values()
        if hasattr(p, "entry_price")
    )
    if total_risk / max(capital, 1) >= max_heat_pct:
        return False, f"portfolio_heat:{total_risk / capital:.1%}"

    return True, "ok"


def get_dynamic_max_positions(base_max: int = 5) -> int:
    """
    Check market regime using VIX:
      VIX >= 30: panic mode — halve positions
      VIX >= 22: elevated — reduce by 1
      VIX <= 14: calm — slight increase
    """
    try:
        import yfinance as yf
        import pandas as pd

        vix_df = yf.download("^VIX", period="1d", interval="5m", progress=False)
        if vix_df is not None and not vix_df.empty:
            if isinstance(vix_df.columns, pd.MultiIndex):
                vix_df.columns = [str(c[0]).lower() for c in vix_df.columns]
            else:
                vix_df.columns = [str(c).lower() for c in vix_df.columns]
            vix = float(vix_df["close"].iloc[-1])
            if vix >= 30:
                return max(2, base_max // 2)   # panic: half positions
            if vix >= 22:
                return max(3, base_max - 1)    # elevated: reduce
            if vix <= 14:
                return base_max + 1            # calm: slight increase
    except Exception as e:
        logger.debug(f"get_dynamic_max_positions VIX fetch: {e}")
    return base_max


def get_position_beta(symbol: str) -> float:
    """
    Compute 60d beta vs SPY via yfinance. Return 1.0 on failure.
    """
    try:
        import yfinance as yf
        import pandas as pd

        df_sym = yf.download(symbol, period="65d", interval="1d", progress=False, auto_adjust=True)
        df_spy = yf.download("SPY",   period="65d", interval="1d", progress=False, auto_adjust=True)

        if df_sym is None or df_spy is None or df_sym.empty or df_spy.empty:
            return 1.0

        for _df in [df_sym, df_spy]:
            if isinstance(_df.columns, pd.MultiIndex):
                _df.columns = [str(c[0]).lower() for c in _df.columns]
            else:
                _df.columns = [str(c).lower() for c in _df.columns]

        col_s = "close" if "close" in df_sym.columns else "adj close"
        col_p = "close" if "close" in df_spy.columns else "adj close"

        if col_s not in df_sym.columns or col_p not in df_spy.columns:
            return 1.0

        ret_sym = df_sym[col_s].pct_change().dropna()
        ret_spy = df_spy[col_p].pct_change().dropna()

        common = ret_sym.index.intersection(ret_spy.index)
        if len(common) < 20:
            return 1.0

        x = ret_spy.loc[common].values
        y = ret_sym.loc[common].values
        var_x = float(np.var(x))
        if var_x < 1e-12:
            return 1.0
        beta = float(np.cov(x, y)[0, 1] / var_x)
        return round(max(0.0, beta), 3)

    except Exception as e:
        logger.debug(f"get_position_beta({symbol}): {e}")
        return 1.0
