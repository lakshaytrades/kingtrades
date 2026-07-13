"""
garch_sizing.py — Volatility-adjusted position sizing
Uses EWMA volatility forecast (proxy for GARCH) to scale position size.
Low predicted volatility = bigger size (more confident trend).
High predicted volatility = smaller size (uncertainty premium).
"""
import logging
import time
from typing import Dict
import numpy as np

logger = logging.getLogger(__name__)

_cache: Dict[str, tuple] = {}  # symbol -> (multiplier, timestamp)
_TTL = 1800.0  # 30 min cache


def get_size_multiplier(symbol: str, returns: list = None) -> float:
    """
    Returns position size multiplier (0.6 to 1.4) based on EWMA vol forecast.
    Fail-open: returns 1.0 on any error.
    """
    try:
        now = time.time()
        if symbol in _cache and now - _cache[symbol][1] < _TTL:
            return _cache[symbol][0]

        if not returns or len(returns) < 10:
            return 1.0

        r = np.array(returns[-30:], dtype=float)
        # EWMA volatility with lambda=0.94 (RiskMetrics standard)
        ewma_var = float(np.var(r))
        for ret in r:
            ewma_var = 0.94 * ewma_var + 0.06 * ret**2
        ewma_vol = float(np.sqrt(ewma_var * 252))  # annualized

        # Calibrated to typical stock vol: 20% = neutral
        # Below 15% = low vol = 1.3x, Above 35% = high vol = 0.7x
        if ewma_vol < 0.10:
            mult = 1.4
        elif ewma_vol < 0.15:
            mult = 1.3
        elif ewma_vol < 0.20:
            mult = 1.15
        elif ewma_vol < 0.25:
            mult = 1.0
        elif ewma_vol < 0.35:
            mult = 0.85
        elif ewma_vol < 0.50:
            mult = 0.75
        else:
            mult = 0.65

        _cache[symbol] = (mult, now)
        logger.debug(f"{symbol}: EWMA vol {ewma_vol:.1%} → size {mult:.2f}x")
        return mult
    except Exception as e:
        logger.debug(f"garch_sizing fail-open: {e}")
        return 1.0
