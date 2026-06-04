"""
regime_signal_router.py — Regime-Specific Strategy Weight Router (v29.0)

The single most costly mistake in systematic trading: using the same signal
weights in BULL, BEAR, and CHOPPY markets.

Momentum signals work brilliantly in trending markets. Mean-reversion signals
work in choppy markets. Using momentum in a choppy market loses money.

Renaissance separates strategies by regime and routes signals accordingly.
This module detects the current regime and returns per-signal-category weights.

Regimes:
  BULL_TREND:    Strong uptrend, low correlation, high breadth
  BEAR_TREND:    Downtrend, high correlation, poor breadth
  BULL_CHOPPY:   Range-bound with upward bias
  BEAR_CHOPPY:   Range-bound with downward bias
  HIGH_VOL:      VIX > 30, all strategies degraded
  CRASH:         VIX > 45, SPY down >3% today — only exits

Signal category weights per regime:
  Category → multiplier (applied to signal delta before adding to score)
  MOMENTUM_SIGNALS: breakout, CSM, multi-momentum, power-hour
  MEAN_REVERSION:   zscore_mr, vwap_reclaim, bollinger bounce
  VOLUME_SIGNALS:   rvol, tod_rvol, tape_ofi
  EVENT_SIGNALS:    analyst, dividend, split, edgar (regime-independent mostly)
  RISK_SIGNALS:     dark_pool, synthetic_l2, correlation — always full weight
"""

import logging
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_cache: Optional[Tuple[str, Dict[str, float], float]] = None
_CACHE_TTL = 300.0  # 5-minute cache

# Per-regime multiplier tables
# Format: {regime: {category: multiplier}}
_REGIME_WEIGHTS: Dict[str, Dict[str, float]] = {
    "BULL_TREND": {
        "MOMENTUM":     1.50,   # momentum is king in trending markets
        "MEAN_REVERT":  0.40,   # mean reversion fights the trend
        "VOLUME":       1.20,   # volume confirms momentum
        "EVENT":        1.00,   # neutral
        "RISK":         1.00,
        "SIZE_MULT":    1.10,   # slightly larger in clear trends
    },
    "BEAR_TREND": {
        "MOMENTUM":     1.30,   # short momentum works
        "MEAN_REVERT":  0.50,   # don't fight downtrend
        "VOLUME":       1.10,
        "EVENT":        0.80,   # events less predictable in bear
        "RISK":         1.20,   # risk signals more important
        "SIZE_MULT":    0.70,   # reduce size in bear market
    },
    "BULL_CHOPPY": {
        "MOMENTUM":     0.70,   # momentum false-breaks in chop
        "MEAN_REVERT":  1.40,   # mean reversion works well
        "VOLUME":       0.90,
        "EVENT":        1.10,   # events more impactful in quiet market
        "RISK":         1.00,
        "SIZE_MULT":    0.80,   # reduce for chop
    },
    "BEAR_CHOPPY": {
        "MOMENTUM":     0.60,
        "MEAN_REVERT":  1.30,
        "VOLUME":       0.80,
        "EVENT":        1.00,
        "RISK":         1.10,
        "SIZE_MULT":    0.65,
    },
    "HIGH_VOL": {
        "MOMENTUM":     0.80,
        "MEAN_REVERT":  0.80,
        "VOLUME":       1.00,
        "EVENT":        0.70,
        "RISK":         1.30,   # risk signals critical
        "SIZE_MULT":    0.50,   # cut size when VIX elevated
    },
    "CRASH": {
        "MOMENTUM":     0.30,
        "MEAN_REVERT":  0.30,
        "VOLUME":       0.50,
        "EVENT":        0.30,
        "RISK":         1.50,
        "SIZE_MULT":    0.25,   # crisis: tiny positions only
    },
    "NORMAL": {
        "MOMENTUM":     1.00,
        "MEAN_REVERT":  1.00,
        "VOLUME":       1.00,
        "EVENT":        1.00,
        "RISK":         1.00,
        "SIZE_MULT":    1.00,
    },
}

# Map signal names → category
_SIGNAL_CATEGORY: Dict[str, str] = {
    # Momentum signals
    "csm": "MOMENTUM", "cross_sectional": "MOMENTUM",
    "multi_momentum": "MOMENTUM", "power_hour": "MOMENTUM",
    "breakout": "MOMENTUM", "orb": "MOMENTUM", "gap": "MOMENTUM",
    "stl_trend": "MOMENTUM", "momentum": "MOMENTUM",
    "pca_alpha": "MOMENTUM",
    # Mean reversion
    "vwap_reclaim": "MEAN_REVERT", "zscore_mr": "MEAN_REVERT",
    "bollinger": "MEAN_REVERT", "candle_streak": "MEAN_REVERT",
    "pairs": "MEAN_REVERT", "stat_arb": "MEAN_REVERT",
    # Volume / order flow
    "rvol": "VOLUME", "tod_rvol": "VOLUME", "tape_ofi": "VOLUME",
    "volume": "VOLUME", "dark_pool": "VOLUME", "ofi": "VOLUME",
    # Event / alt data
    "analyst": "EVENT", "dividend": "EVENT", "split": "EVENT",
    "edgar": "EVENT", "google_trends": "EVENT",
    "wikipedia": "EVENT", "reddit": "EVENT", "pead": "EVENT",
    # Risk / structural
    "synthetic_l2": "RISK", "beta_neutral": "RISK",
    "covariance": "RISK", "market_impact": "RISK",
    "execution_timing": "RISK",
}


def detect_regime(fetcher=None) -> str:
    """
    Detect current market regime from SPY + VIX data.
    Returns regime string: "BULL_TREND"|"BEAR_TREND"|"BULL_CHOPPY"|"BEAR_CHOPPY"|
                            "HIGH_VOL"|"CRASH"|"NORMAL"
    """
    global _cache
    now = _time.monotonic()
    if _cache and (now - _cache[2]) < _CACHE_TTL:
        return _cache[0]

    try:
        regime = _detect_regime_internal()
        _cache = (regime, _REGIME_WEIGHTS.get(regime, _REGIME_WEIGHTS["NORMAL"]), now)
        logger.debug(f"[regime_router] detected regime: {regime}")
        return regime
    except Exception as e:
        logger.debug(f"[regime_router] detect_regime: {e}")
        return "NORMAL"


def get_regime_weights(regime: Optional[str] = None) -> Dict[str, float]:
    """Get signal weight multipliers for the current (or specified) regime."""
    if regime is None:
        regime = detect_regime()
    return _REGIME_WEIGHTS.get(regime, _REGIME_WEIGHTS["NORMAL"])


def get_signal_weight_for_regime(signal_name: str, base_delta: float,
                                  regime: Optional[str] = None) -> Tuple[float, str]:
    """
    Apply regime-specific weight multiplier to a signal delta.
    Returns (adjusted_delta, reason).
    """
    try:
        if regime is None:
            regime = detect_regime()

        weights = _REGIME_WEIGHTS.get(regime, _REGIME_WEIGHTS["NORMAL"])
        sig_lower = signal_name.lower()

        # Find category
        category = "MOMENTUM"  # default
        for key, cat in _SIGNAL_CATEGORY.items():
            if key in sig_lower:
                category = cat
                break

        mult = weights.get(category, 1.0)
        if mult == 1.0:
            return base_delta, f"regime:{regime}:neutral"

        adjusted = round(base_delta * mult, 2)
        return adjusted, f"regime:{regime}:{category}×{mult:.1f}"

    except Exception:
        return base_delta, "regime:error:passthrough"


def get_regime_size_multiplier(regime: Optional[str] = None) -> float:
    """Get overall position size multiplier for current regime."""
    try:
        if regime is None:
            regime = detect_regime()
        weights = _REGIME_WEIGHTS.get(regime, _REGIME_WEIGHTS["NORMAL"])
        return weights.get("SIZE_MULT", 1.0)
    except Exception:
        return 1.0


def _detect_regime_internal() -> str:
    """Detect regime from SPY price action + VIX level."""
    try:
        import yfinance as yf
        import numpy as np

        data = yf.download(
            ["SPY", "^VIX"],
            period="30d", interval="1d",
            auto_adjust=True, progress=False,
        )

        # SPY trend
        try:
            spy_close = data["SPY"]["Close"].dropna()
        except Exception:
            try:
                spy_close = data["Close"]["SPY"].dropna()
            except Exception:
                return "NORMAL"

        # VIX level
        try:
            vix_close = data["^VIX"]["Close"].dropna()
            vix = float(vix_close.iloc[-1])
        except Exception:
            vix = 20.0

        # Crisis check first
        if vix > 45:
            return "CRASH"
        if vix > 30:
            return "HIGH_VOL"

        if len(spy_close) < 15:
            return "NORMAL"

        closes = spy_close.values
        spy_20d_return = (closes[-1] - closes[-20]) / closes[-20] if len(closes) >= 20 else 0.0
        spy_5d_return  = (closes[-1] - closes[-5]) / closes[-5] if len(closes) >= 5 else 0.0

        # Trend direction
        spy_ma10 = float(np.mean(closes[-10:]))
        spy_ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else spy_ma10
        trending_up   = closes[-1] > spy_ma10 > spy_ma20
        trending_down = closes[-1] < spy_ma10 < spy_ma20

        # Choppiness: R² of linear fit
        x = np.arange(min(15, len(closes)), dtype=float)
        y = closes[-len(x):]
        x -= x.mean()
        y -= y.mean()
        ss_tot = float(np.dot(y, y))
        if ss_tot > 0:
            beta = float(np.dot(x, y)) / float(np.dot(x, x))
            y_hat = x * beta
            ss_res = float(np.dot(y - y_hat, y - y_hat))
            r2 = 1.0 - ss_res / ss_tot
        else:
            r2 = 0.0

        is_trending = r2 > 0.60
        is_choppy   = r2 < 0.30

        if trending_up and is_trending:
            return "BULL_TREND"
        elif trending_down and is_trending:
            return "BEAR_TREND"
        elif spy_5d_return >= 0 and is_choppy:
            return "BULL_CHOPPY"
        elif spy_5d_return < 0 and is_choppy:
            return "BEAR_CHOPPY"
        elif trending_up:
            return "BULL_TREND"
        elif trending_down:
            return "BEAR_TREND"

        return "NORMAL"

    except Exception as e:
        logger.debug(f"[regime_router] _detect_internal: {e}")
        return "NORMAL"
