"""
pca_alpha.py — PCA Factor Decomposition for Pure Alpha Extraction (v27.0)

Renaissance Medallion uses PCA to decompose stock returns into:
  - Market factor (beta to SPY)
  - Sector factors
  - Idiosyncratic residual = PURE ALPHA

They trade the residual, not the noisy raw return. A stock moving +2% because
SPY is up 2% = ZERO alpha. A stock moving +2% while SPY is flat = +2% alpha.

Implementation:
  1. Download 60-day daily returns for all watchlist symbols + SPY
  2. Build correlation matrix
  3. Extract first 3 principal components via power iteration (no sklearn)
  4. Compute residual for each stock = return unexplained by factors
  5. Score based on residual direction and magnitude

Score: +8 if positive idiosyncratic alpha for LONG
       -6 if stock is a pure market follower (low residual) for LONG
       Symmetric for SHORT

Cache: 4 hours (daily factors don't change intraday significantly)
"""

import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_cache: Dict[str, Tuple[float, float, float]] = {}  # {symbol: (score, residual, ts)}
_CACHE_TTL = 14400.0  # 4 hours
_MIN_SYMBOLS = 5


def get_pca_alpha_score(symbol: str, direction: str, watchlist: List[str]) -> Tuple[float, str]:
    """
    Compute idiosyncratic alpha for symbol vs watchlist peers using PCA.
    Returns (score_delta, reason). Fail-open on any error.
    """
    try:
        now = _time.monotonic()
        cached = _cache.get(symbol)
        if cached and (now - cached[2]) < _CACHE_TTL:
            return _score_from_residual(cached[1], direction)

        residuals = _compute_residuals(symbol, watchlist)
        if residuals is None:
            return 0.0, "pca:no-data"

        residual = residuals.get(symbol, 0.0)
        _cache[symbol] = (0.0, residual, now)

        return _score_from_residual(residual, direction)
    except Exception as e:
        logger.debug(f"[pca_alpha] {symbol} failed: {e}")
        return 0.0, "pca:error"


def _score_from_residual(residual: float, direction: str) -> Tuple[float, str]:
    """Convert idiosyncratic residual to score delta."""
    abs_r = abs(residual)

    if abs_r < 0.003:  # < 0.3% residual = pure market follower
        score = -4.0 if direction == "LONG" else -4.0
        return score, f"pca:market-follower(residual={residual:.3f})"

    if direction == "LONG":
        if residual > 0.015:   # >1.5% idiosyncratic move up
            return 8.0, f"pca:strong-alpha(+{residual:.3f})"
        elif residual > 0.005:
            return 4.0, f"pca:mild-alpha(+{residual:.3f})"
        elif residual < -0.005:
            return -5.0, f"pca:neg-alpha({residual:.3f})"
    else:  # SHORT
        if residual < -0.015:  # >1.5% idiosyncratic move down
            return 8.0, f"pca:strong-short-alpha({residual:.3f})"
        elif residual < -0.005:
            return 4.0, f"pca:mild-short-alpha({residual:.3f})"
        elif residual > 0.005:
            return -5.0, f"pca:wrong-direction(+{residual:.3f})"

    return 0.0, "pca:neutral"


def _compute_residuals(symbol: str, watchlist: List[str]) -> Optional[Dict[str, float]]:
    """
    Download 60-day returns, run PCA via power iteration, return residuals.
    Uses yfinance for daily data (free).
    """
    try:
        import yfinance as yf

        peers = list(set([symbol] + [s for s in (watchlist or []) if s != symbol] + ["SPY"]))
        if len(peers) < _MIN_SYMBOLS:
            peers = [symbol, "SPY", "QQQ", "IWM", "XLK"]

        # Download 60 days of daily data
        data = yf.download(
            tickers=peers,
            period="60d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )

        # Extract close prices
        if len(peers) == 1:
            closes = data["Close"].to_frame(peers[0])
        else:
            try:
                closes = data["Close"]
            except Exception:
                closes = data.xs("Close", axis=1, level=1)

        closes = closes.dropna(axis=1, how="all").dropna(axis=0)
        if len(closes) < 20 or symbol not in closes.columns:
            return None

        # Daily returns matrix
        returns = closes.pct_change().dropna()
        if len(returns) < 15:
            return None

        R = returns.values.T  # shape: (n_assets, n_days)
        n_assets, n_days = R.shape

        # Demean
        R = R - R.mean(axis=1, keepdims=True)

        # PCA via power iteration (3 components)
        n_components = min(3, n_assets - 1, n_days - 1)
        factors = _power_iteration_pca(R, n_components)

        # Project returns onto factors
        explained = factors.T @ (factors @ R)  # shape: (n_assets, n_days)

        # Residual = unexplained component
        residual_matrix = R - explained

        # Use most recent day's residual
        sym_idx = list(closes.columns).index(symbol)
        residuals = {col: float(residual_matrix[i, -1]) for i, col in enumerate(closes.columns)}

        return residuals

    except Exception as e:
        logger.debug(f"[pca_alpha] compute_residuals failed: {e}")
        return None


def _power_iteration_pca(R: np.ndarray, n_components: int) -> np.ndarray:
    """
    Extract top n_components principal components via power iteration.
    R: (n_assets, n_days) centered return matrix.
    Returns: (n_assets, n_components) factor loading matrix.
    """
    cov = R @ R.T / R.shape[1]
    factors = []
    R_deflated = cov.copy()

    for _ in range(n_components):
        # Random init, normalize
        v = np.random.randn(R_deflated.shape[0])
        v = v / (np.linalg.norm(v) + 1e-9)

        # Power iteration
        for _iter in range(30):
            v_new = R_deflated @ v
            norm = np.linalg.norm(v_new)
            if norm < 1e-9:
                break
            v_new = v_new / norm
            if np.linalg.norm(v_new - v) < 1e-6:
                break
            v = v_new

        factors.append(v)

        # Deflate: remove this component
        eigenval = float(v @ R_deflated @ v)
        R_deflated = R_deflated - eigenval * np.outer(v, v)

    return np.array(factors)  # (n_components, n_assets)
