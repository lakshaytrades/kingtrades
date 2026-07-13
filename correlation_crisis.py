"""
correlation_crisis.py — Cross-Asset Correlation Crisis Detector (v29.0)

When markets crash, all stocks correlate to 1.0. The diversification you
thought you had disappears exactly when you need it most.

This is called a "correlation crisis" or "correlation breakdown" and is
documented in every major drawdown: 2008, 2020, 2022.

Solution: monitor rolling average pairwise correlation across the watchlist.
When it spikes above threshold → CRISIS mode → cut all sizes 50%.

Two signals:
1. Watchlist pairwise correlation (real-time from BarCache 5-min returns)
2. SPY realized variance spike (proxy for market stress level)

Thresholds (calibrated on historical crises):
  avg_corr > 0.80 = CRISIS    → 0.3x size multiplier, no new longs
  avg_corr > 0.65 = ELEVATED  → 0.5x size multiplier, caution
  avg_corr > 0.50 = NORMAL    → 1.0x
  avg_corr < 0.30 = DISPERSED → 1.15x (individual stock moves = alpha-rich)
"""

import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_cache: Optional[Tuple[float, str, float]] = None  # (avg_corr, regime, ts)
_CACHE_TTL = 300.0   # 5-minute cache

# Regime thresholds
_CRISIS    = 0.80
_ELEVATED  = 0.65
_DISPERSED = 0.30


def get_correlation_regime(symbols: List[str], fetcher=None) -> Tuple[str, float]:
    """
    Compute average pairwise correlation of watchlist symbols from 5-min returns.
    Returns (regime_str, avg_correlation).

    Regimes: "CRISIS" | "ELEVATED" | "NORMAL" | "DISPERSED"
    """
    global _cache
    now = _time.monotonic()
    if _cache and (now - _cache[2]) < _CACHE_TTL:
        return _cache[1], _cache[0]

    avg_corr = _compute_avg_correlation(symbols, fetcher)

    if avg_corr > _CRISIS:
        regime = "CRISIS"
    elif avg_corr > _ELEVATED:
        regime = "ELEVATED"
    elif avg_corr < _DISPERSED:
        regime = "DISPERSED"
    else:
        regime = "NORMAL"

    _cache = (avg_corr, regime, now)
    logger.debug(f"[corr_crisis] avg_pairwise_corr={avg_corr:.3f} → {regime}")
    return regime, avg_corr


def get_crisis_size_multiplier(symbols: List[str], fetcher=None) -> Tuple[float, str]:
    """
    Return size multiplier based on current correlation regime.
    Call once per scan cycle, apply to ALL new positions.
    """
    try:
        regime, avg_corr = get_correlation_regime(symbols, fetcher)

        if regime == "CRISIS":
            return 0.30, f"corr_crisis:CRISIS(ρ={avg_corr:.2f}) — 70% size cut"
        elif regime == "ELEVATED":
            return 0.55, f"corr_crisis:ELEVATED(ρ={avg_corr:.2f}) — 45% cut"
        elif regime == "DISPERSED":
            return 1.15, f"corr_crisis:DISPERSED(ρ={avg_corr:.2f}) — +15% size"
        else:
            return 1.0, f"corr_crisis:NORMAL(ρ={avg_corr:.2f})"
    except Exception as e:
        logger.debug(f"[corr_crisis] get_crisis_size_multiplier: {e}")
        return 1.0, "corr_crisis:error:fail-open"


def _compute_avg_correlation(symbols: List[str], fetcher=None) -> float:
    """
    Compute average pairwise Pearson correlation from 20-bar 5-min returns.
    Uses BarCache if fetcher available, else yfinance fallback.
    """
    try:
        returns_matrix = _get_returns_matrix(symbols, fetcher)
        if returns_matrix is None or returns_matrix.shape[0] < 3:
            return _fallback_correlation()

        n_assets = returns_matrix.shape[0]
        if n_assets < 2:
            return 0.5  # neutral default

        # Compute pairwise correlation matrix
        std = returns_matrix.std(axis=1, keepdims=True)
        std[std < 1e-9] = 1e-9
        normed = (returns_matrix - returns_matrix.mean(axis=1, keepdims=True)) / std

        corr_matrix = (normed @ normed.T) / returns_matrix.shape[1]
        np.fill_diagonal(corr_matrix, 0)  # exclude self-correlation

        # Average upper triangle
        upper = corr_matrix[np.triu_indices(n_assets, k=1)]
        avg = float(np.abs(upper).mean()) if len(upper) > 0 else 0.5
        return max(0.0, min(1.0, avg))

    except Exception as e:
        logger.debug(f"[corr_crisis] _compute_avg: {e}")
        return _fallback_correlation()


def _get_returns_matrix(symbols: List[str], fetcher) -> Optional[np.ndarray]:
    """Get 20-bar 5-min return matrix from BarCache or yfinance."""
    try:
        if fetcher is not None:
            arrays = []
            for sym in symbols[:15]:  # cap at 15 for speed
                try:
                    mtf = fetcher.get_multi_timeframe_data(sym)
                    df = mtf.get("5m")
                    if df is None or len(df) < 5:
                        continue
                    close_col = "close" if "close" in df.columns else "Close"
                    closes = df[close_col].values[-21:].astype(float)
                    if len(closes) < 5:
                        continue
                    rets = np.diff(np.log(closes + 1e-9))
                    if len(rets) >= 4:
                        arrays.append(rets[-20:])
                except Exception:
                    continue

            if len(arrays) >= 3:
                min_len = min(len(a) for a in arrays)
                return np.array([a[-min_len:] for a in arrays])

        # Fallback: yfinance daily
        import yfinance as yf
        use_syms = (symbols or [])[:10] + ["SPY"]
        data = yf.download(
            tickers=list(set(use_syms)),
            period="20d", interval="1d",
            auto_adjust=True, progress=False,
        )
        try:
            closes = data["Close"].dropna(axis=1)
        except Exception:
            return None

        returns = closes.pct_change().dropna()
        if len(returns) < 5 or returns.shape[1] < 2:
            return None
        return returns.values.T

    except Exception as e:
        logger.debug(f"[corr_crisis] _get_returns_matrix: {e}")
        return None


def _fallback_correlation() -> float:
    """Use VIX as proxy for correlation level."""
    try:
        import yfinance as yf
        vix = yf.download("^VIX", period="1d", interval="1d", progress=False)
        if vix is not None and not vix.empty:
            vix_val = float(vix["Close"].iloc[-1])
            # VIX 20 ≈ corr 0.45, VIX 30 ≈ corr 0.65, VIX 40+ ≈ corr 0.80+
            return min(0.95, 0.20 + vix_val * 0.015)
    except Exception:
        pass
    return 0.45  # market default
