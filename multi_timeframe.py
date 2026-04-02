"""
multi_timeframe.py — NSE Momentum Groww AI Bot
5-min / 15-min / 1-hour Alignment Engine

18+ years of experience rule: Never trade against the higher timeframe trend.
- 5m signal is the trigger
- 15m is the trend filter
- 1h is the macro context
"""

import logging
from typing import Dict, Optional
import pandas as pd

from utils import format_ist_timestamp
from pattern_recognition import TechnicalIndicators, PatternRecognizer

logger = logging.getLogger(__name__)

TREND_BULLISH = "BULLISH"
TREND_BEARISH = "BEARISH"
TREND_NEUTRAL = "NEUTRAL"


class MultiTimeframeAnalyzer:
    """
    Computes trend direction for each timeframe and checks alignment.
    """

    def __init__(self):
        self.indicators = TechnicalIndicators()
        self.recognizer = PatternRecognizer()

    def analyze_trend(self, df: pd.DataFrame) -> Dict:
        """
        Determine trend for a single timeframe DataFrame.

        Returns:
            {trend, ema_trend, macd_trend, rsi_zone, supertrend, score}
        """
        if df is None or len(df) < 20:
            return {"trend": TREND_NEUTRAL, "score": 0}

        df = self.indicators.compute(df)
        row = df.iloc[-1]

        score = 0
        signals = []

        # EMA alignment
        ema9  = row.get("ema9", 0)
        ema21 = row.get("ema21", 0)
        ema50 = row.get("ema50", 0)
        close = row.get("close", 0)

        if ema9 > ema21 > ema50 and close > ema9:
            score += 30
            signals.append("EMA bullish stack")
        elif ema9 < ema21 < ema50 and close < ema9:
            score -= 30
            signals.append("EMA bearish stack")

        # MACD
        macd_hist = row.get("macd_hist", 0)
        if macd_hist > 0:
            score += 15
        elif macd_hist < 0:
            score -= 15

        # RSI zone
        rsi = row.get("rsi", 50)
        if rsi > 55:
            score += 10
        elif rsi < 45:
            score -= 10

        # Supertrend
        st_dir = int(row.get("supertrend_dir", 0))
        if st_dir == 1:
            score += 20
        elif st_dir == -1:
            score -= 20

        # ADX confirms trend
        adx = row.get("adx", 0)
        plus_di = row.get("plus_di", 0)
        minus_di = row.get("minus_di", 0)
        if adx > 20:
            if plus_di > minus_di:
                score += 15
            else:
                score -= 15

        # Volume trend
        vol_ratio = row.get("volume_ratio", 1)
        if vol_ratio > 1.5 and score > 0:
            score += 10
        elif vol_ratio > 1.5 and score < 0:
            score -= 10

        # Determine trend
        if score >= 35:
            trend = TREND_BULLISH
        elif score <= -35:
            trend = TREND_BEARISH
        else:
            trend = TREND_NEUTRAL

        return {
            "trend": trend,
            "score": score,
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "ema50": round(ema50, 2),
            "rsi": round(rsi, 1),
            "macd_hist": round(macd_hist, 4),
            "supertrend_dir": st_dir,
            "adx": round(adx, 1),
            "signals": signals,
        }

    def check_full_alignment(
        self,
        trend_5m: Dict,
        trend_15m: Dict,
        trend_1h: Dict,
    ) -> Dict:
        """
        Check if all three timeframes are aligned.

        Full alignment = all three same direction → highest confidence
        2/3 aligned = medium confidence
        1/3 = avoid trade
        """
        trends = [
            trend_5m.get("trend", TREND_NEUTRAL),
            trend_15m.get("trend", TREND_NEUTRAL),
            trend_1h.get("trend", TREND_NEUTRAL),
        ]

        bullish_count = sum(1 for t in trends if t == TREND_BULLISH)
        bearish_count = sum(1 for t in trends if t == TREND_BEARISH)

        if bullish_count == 3:
            return {"direction": "LONG", "confidence": "HIGH", "aligned": True,
                    "score": 100, "reason": "All 3 timeframes bullish — highest confidence"}
        elif bearish_count == 3:
            return {"direction": "SHORT", "confidence": "HIGH", "aligned": True,
                    "score": 100, "reason": "All 3 timeframes bearish — highest confidence"}
        elif bullish_count == 2 and trend_5m.get("trend") == TREND_BULLISH:
            return {"direction": "LONG", "confidence": "MEDIUM", "aligned": True,
                    "score": 70, "reason": "5m + 1 other bullish — medium confidence"}
        elif bearish_count == 2 and trend_5m.get("trend") == TREND_BEARISH:
            return {"direction": "SHORT", "confidence": "MEDIUM", "aligned": True,
                    "score": 70, "reason": "5m + 1 other bearish — medium confidence"}
        else:
            return {"direction": "NEUTRAL", "confidence": "LOW", "aligned": False,
                    "score": 20, "reason": "MTF conflict — skip trade"}

    def get_support_resistance(self, df: pd.DataFrame, lookback: int = 20) -> Dict:
        """
        Calculate key support/resistance levels from recent price action.
        """
        if df is None or len(df) < lookback:
            return {}
        recent = df.tail(lookback)
        resistance = recent["high"].max()
        support = recent["low"].min()
        pivot = (resistance + support + recent["close"].iloc[-1]) / 3
        r1 = 2 * pivot - support
        s1 = 2 * pivot - resistance
        return {
            "resistance": round(resistance, 2),
            "support": round(support, 2),
            "pivot": round(pivot, 2),
            "r1": round(r1, 2),
            "s1": round(s1, 2),
        }
