"""
beta_manager.py — Dynamic Beta-Neutral Position Sizing (v28.0)

Every institutional fund calculates beta before sizing a position.
Beta = sensitivity of stock to market (SPY) movements.

Rolling 20-day beta from 5-min bar returns:
  beta = Cov(stock, SPY) / Var(SPY)

Size adjustment:
  target_beta_contribution = 1.0 per position
  size_mult = 1.0 / beta
  High-beta stock (beta=2.0) → 0.5x size (already gives 1 unit of market exposure)
  Low-beta stock (beta=0.5)  → 1.5x size (need more to get full exposure)
  Capped 0.4x to 2.0x

Portfolio-level: if total portfolio beta > MAX_PORTFOLIO_BETA (=5.0):
  new position gets 0.5x size penalty (already overexposed to market)
"""

import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_beta_cache: Dict[str, Tuple[float, float]] = {}  # {symbol: (beta, ts)}
_BETA_TTL = 3600.0   # 1 hour cache

_TARGET_BETA = 1.0   # each position contributes 1 beta unit
_MAX_PORTFOLIO_BETA = 5.0
_MIN_MULT = 0.4
_MAX_MULT = 2.0


def get_beta(symbol: str, df_5m=None) -> float:
    """
    Compute rolling 20-day beta of symbol vs SPY using 5-min returns.
    Falls back to yfinance daily data if df_5m not available.
    Returns beta coefficient (1.0 = market-neutral).
    """
    now = _time.monotonic()
    cached = _beta_cache.get(symbol)
    if cached and (now - cached[1]) < _BETA_TTL:
        return cached[0]

    beta = _compute_beta(symbol, df_5m)
    _beta_cache[symbol] = (beta, now)
    return beta


def _compute_beta(symbol: str, df_5m=None) -> float:
    """Core beta computation. Returns 1.0 on any failure."""
    try:
        if symbol == "SPY":
            return 1.0

        import yfinance as yf

        # Use daily data for stability (20-day rolling beta)
        data = yf.download(
            [symbol, "SPY"],
            period="30d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )

        if data.empty:
            return 1.0

        try:
            stock_close = data[symbol]["Close"].dropna()
            spy_close   = data["SPY"]["Close"].dropna()
        except Exception:
            try:
                stock_close = data["Close"][symbol].dropna()
                spy_close   = data["Close"]["SPY"].dropna()
            except Exception:
                return 1.0

        # Align
        common_idx = stock_close.index.intersection(spy_close.index)
        if len(common_idx) < 10:
            return 1.0

        stock_ret = stock_close.loc[common_idx].pct_change().dropna().values
        spy_ret   = spy_close.loc[common_idx].pct_change().dropna().values

        n = min(len(stock_ret), len(spy_ret))
        if n < 5:
            return 1.0

        stock_ret = stock_ret[-n:]
        spy_ret   = spy_ret[-n:]

        spy_var = float(np.var(spy_ret, ddof=1))
        if spy_var < 1e-9:
            return 1.0

        covariance = float(np.cov(stock_ret, spy_ret)[0, 1])
        beta = covariance / spy_var

        # Sanity bounds: stocks shouldn't have beta < 0.1 or > 4.0 for normal US equities
        beta = max(0.1, min(4.0, beta))
        return round(beta, 3)

    except Exception as e:
        logger.debug(f"[beta] _compute_beta {symbol}: {e}")
        return 1.0


def get_beta_size_multiplier(
    symbol: str,
    df_5m=None,
    portfolio_beta: float = 0.0,
) -> float:
    """
    Returns size multiplier based on beta-neutral sizing.
    Also penalizes if portfolio is already at max beta.
    """
    try:
        beta = get_beta(symbol, df_5m)

        # Beta-neutral: size = target / beta
        if beta > 0:
            mult = _TARGET_BETA / beta
        else:
            mult = 1.0

        mult = max(_MIN_MULT, min(_MAX_MULT, mult))

        # Portfolio-level beta cap
        if portfolio_beta > _MAX_PORTFOLIO_BETA:
            mult = min(mult, 0.5)
            logger.debug(
                f"[beta] {symbol}: portfolio_beta={portfolio_beta:.1f} > "
                f"{_MAX_PORTFOLIO_BETA} → size capped at 0.5x"
            )

        return round(mult, 3)

    except Exception as e:
        logger.debug(f"[beta] get_beta_size_multiplier {symbol}: {e}")
        return 1.0


def get_portfolio_beta(open_positions: List) -> float:
    """
    Compute total portfolio beta from list of open positions.
    open_positions: list of position objects or symbol strings.
    """
    try:
        total_beta = 0.0
        for pos in open_positions:
            sym = pos if isinstance(pos, str) else getattr(pos, "symbol", None)
            if sym is None:
                continue
            size = 1.0 if isinstance(pos, str) else getattr(pos, "size_multiplier", 1.0)
            b = get_beta(sym)
            total_beta += b * size
        return round(total_beta, 2)
    except Exception as e:
        logger.debug(f"[beta] get_portfolio_beta: {e}")
        return 0.0
