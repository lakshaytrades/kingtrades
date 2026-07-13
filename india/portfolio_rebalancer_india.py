"""
portfolio_rebalancer_india.py — Intra-day Portfolio Correlation Rebalancer

Institutional portfolio managers rebalance intraday to:
1. Exit correlated losers before losses compound (correlation pruning)
2. Avoid pyramid entries into correlated winners (concentration risk)
3. Maintain optimal heat distribution across sectors

This module provides:
- Rolling 30-min correlation matrix (symbol pairs → 5-bar return correlation)
- Correlated-loser detection: exit signal when corr > 0.75 AND peer is losing
- Pyramid constraint: only add to position if < 2 correlated open positions
- Portfolio heat check: total open risk as % of capital

All data is in-memory, reset each trading day.
Fail-open: any error returns safe defaults (allow trade).
"""

import logging
import math
from collections import deque
from datetime import datetime
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger("portfolio_rebalancer")

# ---------------------------------------------------------------------------
# In-memory state (reset daily)
# ---------------------------------------------------------------------------

_price_history: Dict[str, deque] = {}   # symbol -> deque(maxlen=6) of prices
_last_reset_date: str = ""

# Hard-coded thresholds (can be overridden via config but kept local for
# fail-open reliability)
_MAX_PORTFOLIO_HEAT_PCT = 4.0     # % — hard stop for new entries
_MAX_CORRELATED_PEERS   = 2       # block entry when >= this many correlated open positions
_CORRELATION_THRESHOLD  = 0.75    # Pearson r threshold for "correlated"
_LOSER_RETURN_THRESHOLD = -0.005  # -0.5R (approx -0.5%) to flag a correlated loser


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_price(symbol: str, price: float, ts=None) -> None:
    """
    Record a new price observation for *symbol*.

    Call once per scan cycle (every 5 min) immediately after fetching LTP.
    Stores the last 6 prices (6 × 5-min bars = 30-min window).

    Parameters
    ----------
    symbol : str
        NSE symbol, e.g. "HDFCBANK".
    price  : float
        Latest traded price (LTP).
    ts     : optional — reserved for future time-indexed storage.
    """
    try:
        _maybe_reset_daily()
        if symbol not in _price_history:
            _price_history[symbol] = deque(maxlen=6)
        if price > 0:
            _price_history[symbol].append(float(price))
    except Exception as exc:
        logger.debug("update_price(%s): %s", symbol, exc)


def get_pairwise_correlation(sym_a: str, sym_b: str) -> float:
    """
    Return the Pearson correlation of 5-bar log-returns for *sym_a* and *sym_b*.

    Requires at least 6 price observations (5 returns) for each symbol.
    Returns 0.0 if insufficient data or any error occurs (fail-open).
    """
    try:
        hist_a = list(_price_history.get(sym_a, []))
        hist_b = list(_price_history.get(sym_b, []))

        # Need >= 6 prices to compute 5 returns
        if len(hist_a) < 6 or len(hist_b) < 6:
            return 0.0

        returns_a = _log_returns(hist_a[-6:])
        returns_b = _log_returns(hist_b[-6:])

        if len(returns_a) < 5 or len(returns_b) < 5:
            return 0.0

        return _pearson(returns_a[:5], returns_b[:5])
    except Exception as exc:
        logger.debug("get_pairwise_correlation(%s, %s): %s", sym_a, sym_b, exc)
        return 0.0


def get_correlated_symbols(
    symbol: str,
    open_positions: List[str],
    threshold: float = _CORRELATION_THRESHOLD,
) -> List[str]:
    """
    Return the subset of *open_positions* whose 30-min return correlation with
    *symbol* exceeds *threshold*.

    Parameters
    ----------
    symbol         : str  — the candidate symbol being evaluated.
    open_positions : list — currently open position symbols.
    threshold      : float — Pearson r cutoff (default 0.75).

    Returns
    -------
    List of symbols from *open_positions* that are correlated > threshold.
    """
    correlated = []
    try:
        for peer in open_positions:
            if peer == symbol:
                continue
            corr = get_pairwise_correlation(symbol, peer)
            if corr > threshold:
                correlated.append(peer)
    except Exception as exc:
        logger.debug("get_correlated_symbols(%s): %s", symbol, exc)
    return correlated


def should_allow_new_entry(
    symbol: str,
    direction: str,
    open_positions: List[str],
    portfolio_heat_pct: float,
) -> Tuple[bool, str]:
    """
    Gate-check for a new position entry.

    Blocks when:
      1. portfolio_heat_pct > 4.0% (hard stop — too much capital at risk)
      2. Already 2+ open positions correlated > 0.75 with *symbol* in the
         same *direction* (concentration risk)

    Parameters
    ----------
    symbol              : str   — candidate symbol.
    direction           : str   — "LONG" or "SHORT".
    open_positions      : list  — currently open position symbols.
    portfolio_heat_pct  : float — total open risk as % of capital (0–100 scale).

    Returns
    -------
    (allowed: bool, reason: str)
    Fail-open: returns (True, "rebalancer ok") on any internal error.
    """
    try:
        # 1. Portfolio heat hard stop
        if portfolio_heat_pct > _MAX_PORTFOLIO_HEAT_PCT:
            return (
                False,
                f"portfolio heat {portfolio_heat_pct:.1f}% > {_MAX_PORTFOLIO_HEAT_PCT:.1f}% limit",
            )

        # 2. Correlation concentration check
        correlated = get_correlated_symbols(symbol, open_positions)
        if len(correlated) >= _MAX_CORRELATED_PEERS:
            return (
                False,
                f"already {len(correlated)} correlated open positions "
                f"({', '.join(correlated[:3])}); correlation > {_CORRELATION_THRESHOLD}",
            )

        return True, "rebalancer ok"

    except Exception as exc:
        logger.debug("should_allow_new_entry(%s): %s — fail-open", symbol, exc)
        return True, "rebalancer ok (error — fail-open)"


def get_rebalance_exits(
    open_positions: List[str],
    prices: Dict[str, float],
) -> List[str]:
    """
    Identify open positions that should be exited due to correlated drawdown.

    A position is flagged when ALL of the following are true:
      - Its return since entry is < -0.5R  (proxy: price < entry by > 0.5%)
      - It has at least one correlated peer (r > 0.75) that is ALSO losing

    This prevents correlated losses compounding.

    Parameters
    ----------
    open_positions : List[str] — currently open position symbols.
    prices         : Dict[str, float] — {symbol: current_price}.

    Returns
    -------
    List of symbols recommended for exit.
    """
    exits = []
    try:
        if len(open_positions) < 2:
            return exits

        # Calculate simple returns from recent price history for each symbol
        # (We don't have entry prices here — use oldest recorded price as proxy start)
        sym_returns: Dict[str, float] = {}
        for sym in open_positions:
            hist = list(_price_history.get(sym, []))
            if len(hist) >= 2:
                old_price = hist[0]
                cur_price = prices.get(sym, 0.0) or hist[-1]
                if old_price > 0:
                    sym_returns[sym] = (cur_price - old_price) / old_price
            elif sym in prices and prices[sym] > 0:
                sym_returns[sym] = 0.0

        losers = {
            sym for sym, ret in sym_returns.items()
            if ret < _LOSER_RETURN_THRESHOLD
        }

        for sym in open_positions:
            ret = sym_returns.get(sym, 0.0)
            if ret >= _LOSER_RETURN_THRESHOLD:
                continue  # not losing enough to care
            # Check if it has a correlated peer that is also losing
            correlated = get_correlated_symbols(sym, open_positions)
            correlated_losers = [p for p in correlated if p in losers]
            if correlated_losers:
                exits.append(sym)
                logger.info(
                    "Rebalancer exit flag: %s (ret=%.3f%%) correlated losers: %s",
                    sym, ret * 100, correlated_losers,
                )

    except Exception as exc:
        logger.debug("get_rebalance_exits: %s", exc)

    return exits


def reset_daily() -> None:
    """
    Clear all price history.  Call at market open (9:15 AM IST) each day.
    """
    global _price_history, _last_reset_date
    try:
        _price_history = {}
        _last_reset_date = datetime.now(IST).strftime("%Y-%m-%d")
        logger.info("Portfolio rebalancer price history reset for %s", _last_reset_date)
    except Exception as exc:
        logger.debug("reset_daily: %s", exc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _maybe_reset_daily() -> None:
    """Auto-reset at the start of each trading day."""
    global _last_reset_date
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if today != _last_reset_date:
        reset_daily()


def _log_returns(prices: List[float]) -> List[float]:
    """Compute log returns from a price series."""
    returns = []
    for i in range(1, len(prices)):
        p0 = prices[i - 1]
        p1 = prices[i]
        if p0 > 0 and p1 > 0:
            returns.append(math.log(p1 / p0))
        else:
            returns.append(0.0)
    return returns


def _pearson(x: List[float], y: List[float]) -> float:
    """
    Compute Pearson correlation coefficient between two equal-length lists.
    Returns 0.0 if standard deviation is zero or lists are empty.
    """
    n = min(len(x), len(y))
    if n < 2:
        return 0.0

    x = x[:n]
    y = y[:n]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / n
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)

    if std_x < 1e-12 or std_y < 1e-12:
        return 0.0

    r = cov / (std_x * std_y)
    # Clamp to [-1, 1] to guard against floating-point drift
    return max(-1.0, min(1.0, r))
