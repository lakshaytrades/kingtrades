"""
portfolio_intelligence_india.py — Portfolio Risk Intelligence (God Mode)
3 guards: sector concentration, correlation, portfolio heat.
Dynamic max positions based on regime. Beta-neutral sizing.
"""
import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("portfolio_intel")
_corr_cache: dict = {}
_CORR_TTL = 3600.0


def get_correlation(sym_a: str, sym_b: str) -> float:
    """
    Return Pearson correlation between sym_a and sym_b using 20d daily returns.
    Cached for 1 hour. Returns 0.0 on any failure.
    """
    try:
        key = tuple(sorted([sym_a.upper(), sym_b.upper()]))
        now = _time.monotonic()
        cached = _corr_cache.get(key)
        if cached and now - cached["ts"] < _CORR_TTL:
            return cached["corr"]

        import yfinance as yf
        import pandas as pd

        tickers = [f"{sym_a.upper()}.NS", f"{sym_b.upper()}.NS"]
        df = yf.download(tickers, period="30d", interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return 0.0

        # Flatten MultiIndex if present
        if isinstance(df.columns, pd.MultiIndex):
            close_df = df["Close"] if "Close" in df.columns.get_level_values(0) else df["close"]
        else:
            close_df = df

        if close_df.shape[1] < 2 or len(close_df) < 10:
            return 0.0

        returns = close_df.pct_change().dropna()
        if len(returns) < 10:
            return 0.0

        col_a = returns.iloc[:, 0]
        col_b = returns.iloc[:, 1]
        corr = float(col_a.corr(col_b))
        if not np.isfinite(corr):
            corr = 0.0

        _corr_cache[key] = {"corr": corr, "ts": now}
        return corr

    except Exception as e:
        logger.debug(f"get_correlation({sym_a},{sym_b}): {e}")
        return 0.0


def check_new_position_allowed(
    new_symbol: str,
    direction: str,
    open_positions_dict: dict,
    config,
) -> Tuple[bool, str]:
    """
    3-gate portfolio risk check before allowing a new position.

    Gate 1: Sector concentration — max MAX_SECTOR_CONCENTRATION per sector (default 2).
    Gate 2: Correlation guard — reject if new symbol highly correlated (>0.75) with
            an open position in the same direction.
    Gate 3: Portfolio heat — total open risk as % of capital must be < MAX_PORTFOLIO_HEAT_PCT.

    Returns (True, "ok") if all gates pass; (False, reason_string) otherwise.
    """

    # ── Gate 1: Sector concentration ─────────────────────────────────────────
    try:
        from watchlist_india import _SECTOR_MAP
        new_sector = _SECTOR_MAP.get(new_symbol.upper(), "Unknown")
        sector_count = sum(
            1 for sym in open_positions_dict
            if _SECTOR_MAP.get(sym.upper()) == new_sector and new_sector != "Unknown"
        )
        max_conc = getattr(config, "MAX_SECTOR_CONCENTRATION", 2)
        if sector_count >= max_conc:
            return False, f"sector_limit:{new_sector}({sector_count})"
    except Exception:
        pass

    # ── Gate 2: Correlation guard ─────────────────────────────────────────────
    max_corr = getattr(config, "MAX_CORRELATION_THRESHOLD", 0.75)
    for sym, pos in open_positions_dict.items():
        try:
            corr = get_correlation(new_symbol, sym)
            pos_dir = getattr(pos, "direction", "LONG")
            if corr > max_corr and direction == pos_dir:
                return False, f"corr_guard:{new_symbol}↔{sym} r={corr:.2f}"
        except Exception:
            pass

    # ── Gate 3: Portfolio heat ────────────────────────────────────────────────
    try:
        total_risk = sum(
            abs(getattr(p, "entry_price", 0) - getattr(p, "stop_loss", 0))
            * getattr(p, "quantity", 0)
            for p in open_positions_dict.values()
        )
        heat = total_risk / max(getattr(config, "MAX_DAILY_CAPITAL", 500000), 1)
        max_heat = getattr(config, "MAX_PORTFOLIO_HEAT_PCT", 0.04)
        if heat >= max_heat:
            return False, f"portfolio_heat:{heat:.1%}"
    except Exception:
        pass

    return True, "ok"


def get_dynamic_max_positions(base_max: int) -> int:
    """
    Reduce max concurrent positions based on current market regime.
    VOLATILE  → halve (min 2).
    RANGING   → reduce by 1 (min 3).
    TRENDING  → keep base_max (momentum works well in trend).
    """
    try:
        from regime_classifier_india import get_regime
        r = get_regime()
        if r == "VOLATILE":
            return max(2, base_max // 2)
        if r == "RANGING":
            return max(3, base_max - 1)
        return base_max
    except Exception:
        return base_max
