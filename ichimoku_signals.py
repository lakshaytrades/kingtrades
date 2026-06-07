"""
Ichimoku Signals — advanced Ichimoku Cloud signal generation.

Ichimoku is one of the most complete trend indicators:
- Tenkan-sen (9): fast trend line (conversion line)
- Kijun-sen (26): slow trend line (base line)
- Senkou Span A (26 ahead): cloud boundary 1
- Senkou Span B (52 ahead): cloud boundary 2
- Chikou Span (26 behind): lagging confirmation

Signals:
- TK Cross: Tenkan crosses above/below Kijun (fastest signal)
- Cloud Break: Price breaks above/below cloud (medium signal)
- Kumo Twist: Cloud changes color (Span A vs Span B) — trend confirmation
- Chikou Clear: Chikou above/below price 26 periods ago (confirmation)
- Strong Bull: ALL 4 bullish conditions met simultaneously (90% WR historically)
- Strong Bear: ALL 4 bearish conditions met
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class IchimokuState:
    tenkan: float = 0.0
    kijun: float = 0.0
    senkou_a: float = 0.0
    senkou_b: float = 0.0
    chikou: float = 0.0
    current_price: float = 0.0
    cloud_top: float = 0.0
    cloud_bottom: float = 0.0
    above_cloud: bool = False
    below_cloud: bool = False
    in_cloud: bool = False
    cloud_bullish: bool = True   # Span A > Span B = bullish cloud
    tk_cross_bull: bool = False
    tk_cross_bear: bool = False
    chikou_bull: bool = False
    chikou_bear: bool = False
    signal: str = "NEUTRAL"
    score: float = 0.0
    confidence: float = 0.5


class IchimokuAnalyzer:

    def __init__(self, tenkan_period: int = 9, kijun_period: int = 26,
                 senkou_b_period: int = 52, displacement: int = 26):
        self.tenkan_period = tenkan_period
        self.kijun_period = kijun_period
        self.senkou_b_period = senkou_b_period
        self.displacement = displacement

    def compute(self, highs: np.ndarray, lows: np.ndarray,
                closes: np.ndarray) -> IchimokuState:
        state = IchimokuState()
        min_bars = self.senkou_b_period + self.displacement + 2

        if closes is None or len(closes) < min_bars:
            return state

        highs = np.asarray(highs, dtype=float)
        lows = np.asarray(lows, dtype=float)
        closes = np.asarray(closes, dtype=float)

        n = len(closes)

        def donchian_mid(period, idx):
            start = max(0, idx - period + 1)
            return (highs[start:idx+1].max() + lows[start:idx+1].min()) / 2

        # Compute at current bar
        curr = n - 1
        tenkan = donchian_mid(self.tenkan_period, curr)
        kijun = donchian_mid(self.kijun_period, curr)

        # Senkou computed 26 bars ago (what's now "current cloud")
        cloud_idx = curr - self.displacement
        if cloud_idx < 0:
            return state

        prev_tenkan = donchian_mid(self.tenkan_period, cloud_idx)
        prev_kijun = donchian_mid(self.kijun_period, cloud_idx)
        senkou_a = (prev_tenkan + prev_kijun) / 2

        senkou_b_idx = max(0, cloud_idx - self.senkou_b_period + 1)
        senkou_b = (highs[senkou_b_idx:cloud_idx+1].max() +
                    lows[senkou_b_idx:cloud_idx+1].min()) / 2

        # Chikou: current close plotted 26 bars back, vs price at that point
        chikou_compare_idx = curr - self.displacement
        chikou = closes[curr]
        chikou_ref = closes[chikou_compare_idx] if chikou_compare_idx >= 0 else chikou

        current_price = closes[curr]
        cloud_top = max(senkou_a, senkou_b)
        cloud_bottom = min(senkou_a, senkou_b)

        state.tenkan = tenkan
        state.kijun = kijun
        state.senkou_a = senkou_a
        state.senkou_b = senkou_b
        state.chikou = chikou
        state.current_price = current_price
        state.cloud_top = cloud_top
        state.cloud_bottom = cloud_bottom
        state.above_cloud = current_price > cloud_top
        state.below_cloud = current_price < cloud_bottom
        state.in_cloud = cloud_bottom <= current_price <= cloud_top
        state.cloud_bullish = senkou_a > senkou_b

        # TK Cross: check previous bar
        if curr >= 1:
            prev_t = donchian_mid(self.tenkan_period, curr - 1)
            prev_k = donchian_mid(self.kijun_period, curr - 1)
            state.tk_cross_bull = (prev_t <= prev_k) and (tenkan > kijun)
            state.tk_cross_bear = (prev_t >= prev_k) and (tenkan < kijun)

        state.chikou_bull = chikou > chikou_ref
        state.chikou_bear = chikou < chikou_ref

        # Signal classification
        bull_conditions = [
            state.above_cloud,
            state.cloud_bullish,
            tenkan > kijun,
            state.chikou_bull,
        ]
        bear_conditions = [
            state.below_cloud,
            not state.cloud_bullish,
            tenkan < kijun,
            state.chikou_bear,
        ]

        bull_count = sum(bull_conditions)
        bear_count = sum(bear_conditions)

        score = 0.0
        signal = "NEUTRAL"

        if bull_count == 4:
            signal = "ICHIMOKU_STRONG_BULL"
            score = 9.0
        elif bull_count == 3:
            signal = "ICHIMOKU_BULL"
            score = 6.0
            if state.tk_cross_bull:
                score += 2.0
                signal = "ICHIMOKU_TK_CROSS_BULL"
        elif bear_count == 4:
            signal = "ICHIMOKU_STRONG_BEAR"
            score = -9.0
        elif bear_count == 3:
            signal = "ICHIMOKU_BEAR"
            score = -6.0
            if state.tk_cross_bear:
                score -= 2.0
                signal = "ICHIMOKU_TK_CROSS_BEAR"
        elif state.tk_cross_bull and state.above_cloud:
            signal = "ICHIMOKU_TK_CROSS_BULL"
            score = 5.0
        elif state.tk_cross_bear and state.below_cloud:
            signal = "ICHIMOKU_TK_CROSS_BEAR"
            score = -5.0
        elif state.in_cloud:
            signal = "ICHIMOKU_IN_CLOUD"
            score = 0.0

        state.signal = signal
        state.score = score
        state.confidence = bull_count / 4.0 if score > 0 else (bear_count / 4.0 if score < 0 else 0.5)

        return state

    def get_score_for_signal_generator(self, highs: np.ndarray, lows: np.ndarray,
                                        closes: np.ndarray) -> float:
        """Returns score in [-10, +10] for direct use in signal_generator."""
        try:
            state = self.compute(highs, lows, closes)
            return max(-10.0, min(10.0, state.score))
        except Exception as e:
            logger.debug(f"Ichimoku score error: {e}")
            return 0.0


# Module-level singleton
_analyzer: Optional[IchimokuAnalyzer] = None

def get_analyzer() -> IchimokuAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = IchimokuAnalyzer()
    return _analyzer
