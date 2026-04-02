"""
pattern_recognition.py — NSE Momentum Groww AI Bot
20+ Chart Patterns + Full Indicator Stack

Based on 18+ years of NSE intraday trading experience.
Detects: engulfing, hammer, doji, breakouts, flags, pennants,
         VWAP deviations, divergences, ORB, and more.

All patterns return a confidence score (0–100) and direction (LONG/SHORT/NEUTRAL).
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

try:
    import pandas_ta as ta
except ImportError:
    ta = None
    logging.warning("pandas_ta not installed. Run: pip install pandas-ta")

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)


@dataclass
class PatternResult:
    name: str
    direction: str        # "LONG", "SHORT", "NEUTRAL"
    confidence: float     # 0–100
    description: str
    candle_index: int = -1


@dataclass
class IndicatorSet:
    """Complete indicator values for a single bar."""
    rsi: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    atr: float = 0.0
    vwap: float = 0.0
    bb_upper: float = 0.0
    bb_mid: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    ema9: float = 0.0
    ema21: float = 0.0
    ema50: float = 0.0
    ema200: float = 0.0
    volume_sma: float = 0.0
    volume_ratio: float = 1.0  # current / SMA
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    obv: float = 0.0
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    supertrend: float = 0.0
    supertrend_dir: int = 1   # 1 = bullish, -1 = bearish


class TechnicalIndicators:
    """Computes full indicator stack using pandas_ta."""

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add all technical indicators to a candles DataFrame.
        Input: OHLCV DataFrame. Returns augmented DataFrame.
        """
        if ta is None:
            logger.error("pandas_ta not available — cannot compute indicators")
            return df

        df = df.copy()

        try:
            # RSI
            df["rsi"] = ta.rsi(df["close"], length=14)

            # MACD
            macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
            if macd is not None:
                df["macd"] = macd.iloc[:, 0]
                df["macd_hist"] = macd.iloc[:, 1]
                df["macd_signal"] = macd.iloc[:, 2]

            # ATR
            df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

            # Bollinger Bands
            bb = ta.bbands(df["close"], length=20, std=2)
            if bb is not None:
                df["bb_lower"] = bb.iloc[:, 0]
                df["bb_mid"] = bb.iloc[:, 1]
                df["bb_upper"] = bb.iloc[:, 2]
                df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

            # EMAs
            df["ema9"]  = ta.ema(df["close"], length=9)
            df["ema21"] = ta.ema(df["close"], length=21)
            df["ema50"] = ta.ema(df["close"], length=50)
            df["ema200"]= ta.ema(df["close"], length=200)

            # Volume SMA and ratio
            df["volume_sma"] = ta.sma(df["volume"].astype(float), length=20)
            df["volume_ratio"] = df["volume"] / df["volume_sma"].replace(0, np.nan)

            # Stochastic
            stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3, smooth_k=3)
            if stoch is not None:
                df["stoch_k"] = stoch.iloc[:, 0]
                df["stoch_d"] = stoch.iloc[:, 1]

            # OBV
            df["obv"] = ta.obv(df["close"], df["volume"])

            # ADX / DI
            adx = ta.adx(df["high"], df["low"], df["close"], length=14)
            if adx is not None:
                df["adx"]      = adx.iloc[:, 0]
                df["plus_di"]  = adx.iloc[:, 1]
                df["minus_di"] = adx.iloc[:, 2]

            # VWAP (only meaningful for intraday — resets each day)
            try:
                df["vwap"] = self._calculate_vwap(df)
            except Exception:
                df["vwap"] = df["close"]

            # Supertrend
            try:
                st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3)
                if st is not None:
                    df["supertrend"]     = st.iloc[:, 0]
                    df["supertrend_dir"] = st.iloc[:, 3].apply(lambda x: 1 if x > 0 else -1)
            except Exception:
                df["supertrend"] = df["close"]
                df["supertrend_dir"] = 1

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Indicator compute error: {e}")

        return df.fillna(method="ffill").fillna(0)

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate VWAP resetting each trading day (9:15 AM IST).
        VWAP = cumsum(typical_price * volume) / cumsum(volume)
        """
        df = df.copy()
        tp = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"].astype(float)

        # Group by date for daily reset
        if hasattr(df.index, 'date'):
            dates = pd.Series(df.index.date, index=df.index)
            vwap = pd.Series(index=df.index, dtype=float)
            for day in dates.unique():
                mask = dates == day
                day_tp = tp[mask]
                day_vol = vol[mask]
                cum_tpv = (day_tp * day_vol).cumsum()
                cum_vol = day_vol.cumsum()
                vwap[mask] = cum_tpv / cum_vol.replace(0, np.nan)
        else:
            cum_tpv = (tp * vol).cumsum()
            cum_vol = vol.cumsum()
            vwap = cum_tpv / cum_vol.replace(0, np.nan)

        return vwap

    def get_latest_indicators(self, df: pd.DataFrame) -> IndicatorSet:
        """Extract the latest bar's indicator values as IndicatorSet."""
        if df.empty:
            return IndicatorSet()
        row = df.iloc[-1]
        return IndicatorSet(
            rsi=float(row.get("rsi", 50)),
            macd=float(row.get("macd", 0)),
            macd_signal=float(row.get("macd_signal", 0)),
            macd_hist=float(row.get("macd_hist", 0)),
            atr=float(row.get("atr", 0)),
            vwap=float(row.get("vwap", row.get("close", 0))),
            bb_upper=float(row.get("bb_upper", 0)),
            bb_mid=float(row.get("bb_mid", 0)),
            bb_lower=float(row.get("bb_lower", 0)),
            bb_width=float(row.get("bb_width", 0)),
            ema9=float(row.get("ema9", 0)),
            ema21=float(row.get("ema21", 0)),
            ema50=float(row.get("ema50", 0)),
            ema200=float(row.get("ema200", 0)),
            volume_sma=float(row.get("volume_sma", 0)),
            volume_ratio=float(row.get("volume_ratio", 1)),
            stoch_k=float(row.get("stoch_k", 50)),
            stoch_d=float(row.get("stoch_d", 50)),
            obv=float(row.get("obv", 0)),
            adx=float(row.get("adx", 0)),
            plus_di=float(row.get("plus_di", 0)),
            minus_di=float(row.get("minus_di", 0)),
            supertrend=float(row.get("supertrend", row.get("close", 0))),
            supertrend_dir=int(row.get("supertrend_dir", 1)),
        )


class PatternRecognizer:
    """
    Detects 20+ candlestick and chart patterns.
    Each detector returns a PatternResult with confidence 0–100.
    """

    def __init__(self):
        self.indicators = TechnicalIndicators()

    def analyze(self, df: pd.DataFrame) -> Dict:
        """
        Run full pattern analysis on a DataFrame.
        Returns dict with all detected patterns and indicator values.
        """
        if df is None or len(df) < 50:
            return {"patterns": [], "indicators": IndicatorSet(), "score": 0}

        # Add indicators
        df = self.indicators.compute(df)
        ind = self.indicators.get_latest_indicators(df)

        # Detect all patterns
        patterns = []
        detectors = [
            self.detect_bullish_engulfing,
            self.detect_bearish_engulfing,
            self.detect_hammer,
            self.detect_shooting_star,
            self.detect_doji,
            self.detect_morning_star,
            self.detect_evening_star,
            self.detect_three_white_soldiers,
            self.detect_three_black_crows,
            self.detect_bullish_harami,
            self.detect_bearish_harami,
            self.detect_piercing_line,
            self.detect_dark_cloud_cover,
            self.detect_breakout,
            self.detect_breakdown,
            self.detect_flag_pattern,
            self.detect_pennant_pattern,
            self.detect_vwap_bounce,
            self.detect_vwap_breakdown,
            self.detect_volume_surge_breakout,
            self.detect_rsi_divergence,
            self.detect_macd_crossover,
            self.detect_bb_squeeze_breakout,
            self.detect_ema_crossover,
            self.detect_supertrend_signal,
            self.detect_orb_breakout,
        ]

        for detector in detectors:
            try:
                result = detector(df, ind)
                if result and result.confidence >= 40:
                    patterns.append(result)
            except Exception as e:
                logger.debug(f"Pattern detector {detector.__name__} error: {e}")

        # Compute composite score
        score = self._compute_composite_score(patterns, ind)

        return {
            "patterns": patterns,
            "indicators": ind,
            "score": score,
            "long_patterns": [p for p in patterns if p.direction == "LONG"],
            "short_patterns": [p for p in patterns if p.direction == "SHORT"],
        }

    # --------------------------------------------------------
    # CANDLESTICK PATTERNS
    # --------------------------------------------------------

    def detect_bullish_engulfing(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        prev_bearish = prev["close"] < prev["open"]
        curr_bullish = curr["close"] > curr["open"]
        engulfs = curr["open"] < prev["close"] and curr["close"] > prev["open"]
        if not (prev_bearish and curr_bullish and engulfs):
            return None
        body_ratio = abs(curr["close"] - curr["open"]) / max(abs(prev["close"] - prev["open"]), 0.01)
        confidence = min(50 + body_ratio * 10 + (ind.volume_ratio * 5), 90)
        # Stronger in oversold
        if ind.rsi < 40:
            confidence = min(confidence + 10, 95)
        return PatternResult("Bullish Engulfing", "LONG", confidence,
                             f"Bullish engulfing with {body_ratio:.1f}x body ratio")

    def detect_bearish_engulfing(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        prev_bullish = prev["close"] > prev["open"]
        curr_bearish = curr["close"] < curr["open"]
        engulfs = curr["open"] > prev["close"] and curr["close"] < prev["open"]
        if not (prev_bullish and curr_bearish and engulfs):
            return None
        body_ratio = abs(curr["close"] - curr["open"]) / max(abs(prev["close"] - prev["open"]), 0.01)
        confidence = min(50 + body_ratio * 10 + (ind.volume_ratio * 5), 90)
        if ind.rsi > 60:
            confidence = min(confidence + 10, 95)
        return PatternResult("Bearish Engulfing", "SHORT", confidence,
                             f"Bearish engulfing with {body_ratio:.1f}x body ratio")

    def detect_hammer(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        upper_shadow = c["high"] - max(c["close"], c["open"])
        if body / total_range > 0.35:
            return None
        if lower_shadow < 2 * body:
            return None
        if upper_shadow > body:
            return None
        confidence = 65 + (lower_shadow / total_range) * 20
        if ind.rsi < 40:
            confidence = min(confidence + 10, 92)
        return PatternResult("Hammer", "LONG", confidence,
                             f"Hammer — strong rejection at lows, RSI={ind.rsi:.0f}")

    def detect_shooting_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        upper_shadow = c["high"] - max(c["close"], c["open"])
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        if body / total_range > 0.35:
            return None
        if upper_shadow < 2 * body:
            return None
        if lower_shadow > body:
            return None
        confidence = 65 + (upper_shadow / total_range) * 20
        if ind.rsi > 60:
            confidence = min(confidence + 10, 92)
        return PatternResult("Shooting Star", "SHORT", confidence,
                             f"Shooting Star — rejection at highs, RSI={ind.rsi:.0f}")

    def detect_doji(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        if body / total_range > 0.1:
            return None
        return PatternResult("Doji", "NEUTRAL", 60,
                             "Doji — market indecision, watch for breakout")

    def detect_morning_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] < c1["open"] and
                abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3 and
                c3["close"] > c3["open"] and
                c3["close"] > (c1["open"] + c1["close"]) / 2):
            confidence = 75 + (ind.volume_ratio - 1) * 5
            return PatternResult("Morning Star", "LONG", min(confidence, 92),
                                 "3-candle morning star reversal at bottom")
        return None

    def detect_evening_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] > c1["open"] and
                abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3 and
                c3["close"] < c3["open"] and
                c3["close"] < (c1["open"] + c1["close"]) / 2):
            confidence = 75 + (ind.volume_ratio - 1) * 5
            return PatternResult("Evening Star", "SHORT", min(confidence, 92),
                                 "3-candle evening star reversal at top")
        return None

    def detect_three_white_soldiers(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] > c1["open"] and c2["close"] > c2["open"] and c3["close"] > c3["open"] and
                c2["open"] > c1["open"] and c3["open"] > c2["open"] and
                c2["close"] > c1["close"] and c3["close"] > c2["close"]):
            return PatternResult("Three White Soldiers", "LONG", 80,
                                 "Strong 3-candle bullish momentum continuation")
        return None

    def detect_three_black_crows(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] < c1["open"] and c2["close"] < c2["open"] and c3["close"] < c3["open"] and
                c2["open"] < c1["open"] and c3["open"] < c2["open"] and
                c2["close"] < c1["close"] and c3["close"] < c2["close"]):
            return PatternResult("Three Black Crows", "SHORT", 80,
                                 "Strong 3-candle bearish momentum continuation")
        return None

    def detect_bullish_harami(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        if (prev["close"] < prev["open"] and
                curr["close"] > curr["open"] and
                curr["open"] > prev["close"] and curr["close"] < prev["open"]):
            return PatternResult("Bullish Harami", "LONG", 62,
                                 "Bullish harami — potential reversal inside prior bearish candle")
        return None

    def detect_bearish_harami(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        if (prev["close"] > prev["open"] and
                curr["close"] < curr["open"] and
                curr["open"] < prev["close"] and curr["close"] > prev["open"]):
            return PatternResult("Bearish Harami", "SHORT", 62,
                                 "Bearish harami — potential reversal inside prior bullish candle")
        return None

    def detect_piercing_line(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        mid_prev = (prev["open"] + prev["close"]) / 2
        if (prev["close"] < prev["open"] and
                curr["close"] > curr["open"] and
                curr["open"] < prev["close"] and
                curr["close"] > mid_prev and curr["close"] < prev["open"]):
            return PatternResult("Piercing Line", "LONG", 68,
                                 "Piercing line — bullish reversal, price penetrates >50% of prior bearish bar")
        return None

    def detect_dark_cloud_cover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        mid_prev = (prev["open"] + prev["close"]) / 2
        if (prev["close"] > prev["open"] and
                curr["close"] < curr["open"] and
                curr["open"] > prev["close"] and
                curr["close"] < mid_prev and curr["close"] > prev["open"]):
            return PatternResult("Dark Cloud Cover", "SHORT", 68,
                                 "Dark cloud cover — bearish reversal, penetrates >50% of prior bullish bar")
        return None

    # --------------------------------------------------------
    # CHART PATTERNS
    # --------------------------------------------------------

    def detect_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Breakout above recent resistance with volume."""
        if len(df) < 20:
            return None
        recent_high = df["high"].iloc[-20:-1].max()
        curr_close = df["close"].iloc[-1]
        if curr_close > recent_high * 1.001 and ind.volume_ratio >= 1.5:
            breakout_pct = ((curr_close - recent_high) / recent_high) * 100
            confidence = min(60 + breakout_pct * 10 + (ind.volume_ratio - 1) * 15, 92)
            return PatternResult("Resistance Breakout", "LONG", confidence,
                                 f"Breakout above ₹{recent_high:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    def detect_breakdown(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Breakdown below recent support with volume."""
        if len(df) < 20:
            return None
        recent_low = df["low"].iloc[-20:-1].min()
        curr_close = df["close"].iloc[-1]
        if curr_close < recent_low * 0.999 and ind.volume_ratio >= 1.5:
            breakdown_pct = ((recent_low - curr_close) / recent_low) * 100
            confidence = min(60 + breakdown_pct * 10 + (ind.volume_ratio - 1) * 15, 92)
            return PatternResult("Support Breakdown", "SHORT", confidence,
                                 f"Breakdown below ₹{recent_low:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    def detect_flag_pattern(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Bull/bear flag: sharp move then tight consolidation."""
        if len(df) < 15:
            return None
        # Strong move in last 5 bars
        move = df["close"].iloc[-10] - df["close"].iloc[-15]
        atr = ind.atr if ind.atr > 0 else 1
        if abs(move) < 3 * atr:
            return None
        # Tight range in last 5 bars (flag)
        recent = df.iloc[-5:]
        flag_range = recent["high"].max() - recent["low"].min()
        if flag_range > 1.5 * atr:
            return None
        if move > 0:
            return PatternResult("Bull Flag", "LONG", 72,
                                 f"Bull flag: {move:.2f} pole, tight {flag_range:.2f} consolidation")
        else:
            return PatternResult("Bear Flag", "SHORT", 72,
                                 f"Bear flag: {abs(move):.2f} pole, tight {flag_range:.2f} consolidation")

    def detect_pennant_pattern(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Pennant: sharp move then converging highs/lows."""
        if len(df) < 15:
            return None
        # Strong initial move
        move = df["close"].iloc[-10] - df["close"].iloc[-15]
        atr = ind.atr if ind.atr > 0 else 1
        if abs(move) < 3 * atr:
            return None
        # Converging in last 5 bars
        recent = df.iloc[-5:]
        highs = recent["high"].values
        lows = recent["low"].values
        if len(highs) < 3:
            return None
        high_slope = np.polyfit(range(len(highs)), highs, 1)[0]
        low_slope = np.polyfit(range(len(lows)), lows, 1)[0]
        if move > 0 and high_slope < 0 and low_slope > 0:
            return PatternResult("Bull Pennant", "LONG", 74,
                                 "Bull pennant — converging consolidation after strong move up")
        elif move < 0 and high_slope < 0 and low_slope > 0:
            return PatternResult("Bear Pennant", "SHORT", 74,
                                 "Bear pennant — converging consolidation after strong move down")
        return None

    def detect_vwap_bounce(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Price bouncing off VWAP from below (bullish)."""
        if ind.vwap == 0 or len(df) < 3:
            return None
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        # Was below VWAP, now crossing above
        if (prev["low"] <= ind.vwap and curr["close"] > ind.vwap * 1.001 and
                curr["close"] > curr["open"]):
            dev_pct = ((curr["close"] - ind.vwap) / ind.vwap) * 100
            if dev_pct < 0.5:  # Not too far above VWAP yet
                confidence = 70 + (ind.volume_ratio - 1) * 10
                return PatternResult("VWAP Bounce", "LONG", min(confidence, 85),
                                     f"Price bouncing above VWAP ₹{ind.vwap:.2f}")
        return None

    def detect_vwap_breakdown(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Price breaking below VWAP (bearish)."""
        if ind.vwap == 0 or len(df) < 3:
            return None
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        if (prev["high"] >= ind.vwap and curr["close"] < ind.vwap * 0.999 and
                curr["close"] < curr["open"]):
            confidence = 70 + (ind.volume_ratio - 1) * 10
            return PatternResult("VWAP Breakdown", "SHORT", min(confidence, 85),
                                 f"Price broke below VWAP ₹{ind.vwap:.2f}")
        return None

    def detect_volume_surge_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Strong directional move with 2x+ volume surge."""
        if ind.volume_ratio < 2.0 or len(df) < 5:
            return None
        curr = df.iloc[-1]
        body = abs(curr["close"] - curr["open"])
        atr = ind.atr if ind.atr > 0 else 1
        if body < 0.8 * atr:
            return None
        confidence = min(55 + (ind.volume_ratio - 2) * 15 + (body / atr) * 5, 90)
        direction = "LONG" if curr["close"] > curr["open"] else "SHORT"
        return PatternResult("Volume Surge", direction, confidence,
                             f"{ind.volume_ratio:.1f}x volume surge with {body/atr:.1f}x ATR move")

    def detect_rsi_divergence(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """RSI divergence: price makes new high/low but RSI doesn't."""
        if len(df) < 20 or "rsi" not in df.columns:
            return None
        # Bullish divergence: price lower low, RSI higher low
        prices = df["close"].iloc[-20:]
        rsiv = df["rsi"].iloc[-20:]
        if rsiv.isna().all():
            return None
        # Find recent swing lows
        price_min_idx = prices.idxmin()
        prev_prices = df["close"].iloc[-40:-20] if len(df) >= 40 else None
        if prev_prices is not None and not prev_prices.empty:
            prev_min = prev_prices.min()
            curr_min = prices.min()
            prev_rsi = df["rsi"].iloc[-40:-20].min() if len(df) >= 40 else 50
            curr_rsi = rsiv.min()
            if curr_min < prev_min and curr_rsi > prev_rsi and ind.rsi < 45:
                return PatternResult("Bullish RSI Divergence", "LONG", 75,
                                     f"Bullish divergence: price lower low, RSI higher low ({ind.rsi:.0f})")
            if curr_min > prev_min and curr_rsi < prev_rsi and ind.rsi > 55:
                return PatternResult("Bearish RSI Divergence", "SHORT", 75,
                                     f"Bearish divergence: price higher high, RSI lower high ({ind.rsi:.0f})")
        return None

    def detect_macd_crossover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """MACD line crossing signal line."""
        if len(df) < 3 or "macd" not in df.columns:
            return None
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_diff = prev.get("macd", 0) - prev.get("macd_signal", 0)
        curr_diff = curr.get("macd", 0) - curr.get("macd_signal", 0)
        if prev_diff < 0 and curr_diff > 0:
            hist_strength = abs(ind.macd_hist)
            confidence = min(62 + hist_strength * 2, 85)
            return PatternResult("MACD Bullish Crossover", "LONG", confidence,
                                 f"MACD crossed above signal, hist={ind.macd_hist:.4f}")
        if prev_diff > 0 and curr_diff < 0:
            hist_strength = abs(ind.macd_hist)
            confidence = min(62 + hist_strength * 2, 85)
            return PatternResult("MACD Bearish Crossover", "SHORT", confidence,
                                 f"MACD crossed below signal, hist={ind.macd_hist:.4f}")
        return None

    def detect_bb_squeeze_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Bollinger Band squeeze followed by expansion breakout."""
        if len(df) < 25 or "bb_width" not in df.columns:
            return None
        # Squeeze: BB width at recent minimum
        recent_width = df["bb_width"].iloc[-25:]
        if recent_width.isna().all():
            return None
        curr_width = ind.bb_width
        avg_width = recent_width.mean()
        # Was squeezed, now expanding
        if curr_width > avg_width * 1.2:
            prev_width = df["bb_width"].iloc[-5:-1].mean()
            if prev_width < avg_width * 0.8:  # Was in squeeze
                curr = df.iloc[-1]
                if curr["close"] > ind.bb_upper:
                    return PatternResult("BB Squeeze Breakout Long", "LONG", 78,
                                        f"Bollinger Band squeeze explosion upward")
                elif curr["close"] < ind.bb_lower:
                    return PatternResult("BB Squeeze Breakout Short", "SHORT", 78,
                                        f"Bollinger Band squeeze explosion downward")
        return None

    def detect_ema_crossover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """EMA9 crossing EMA21 (fast momentum signal)."""
        if len(df) < 3 or "ema9" not in df.columns:
            return None
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_diff = prev.get("ema9", 0) - prev.get("ema21", 0)
        curr_diff = curr.get("ema9", 0) - curr.get("ema21", 0)
        close = curr["close"]
        if prev_diff < 0 and curr_diff > 0 and close > ind.ema50:
            return PatternResult("EMA9 x EMA21 Bullish", "LONG", 68,
                                 f"EMA9 crossed above EMA21, price above EMA50")
        if prev_diff > 0 and curr_diff < 0 and close < ind.ema50:
            return PatternResult("EMA9 x EMA21 Bearish", "SHORT", 68,
                                 f"EMA9 crossed below EMA21, price below EMA50")
        return None

    def detect_supertrend_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Supertrend direction change signal."""
        if len(df) < 3 or "supertrend_dir" not in df.columns:
            return None
        prev_dir = int(df["supertrend_dir"].iloc[-2])
        curr_dir = int(df["supertrend_dir"].iloc[-1])
        if prev_dir == -1 and curr_dir == 1:
            return PatternResult("Supertrend Flip Bullish", "LONG", 76,
                                 f"Supertrend flipped bullish — trend change confirmed")
        if prev_dir == 1 and curr_dir == -1:
            return PatternResult("Supertrend Flip Bearish", "SHORT", 76,
                                 f"Supertrend flipped bearish — trend change confirmed")
        return None

    def detect_orb_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Opening Range Breakout (9:15–9:30 AM IST)."""
        if len(df) < 4:
            return None
        # First 3 candles form the ORB
        orb_candles = df.iloc[:3]
        orb_high = orb_candles["high"].max()
        orb_low = orb_candles["low"].min()
        curr = df.iloc[-1]
        if curr["close"] > orb_high * 1.001 and ind.volume_ratio >= 1.5:
            return PatternResult("ORB Bullish Breakout", "LONG", 80,
                                 f"ORB breakout above ₹{orb_high:.2f} with {ind.volume_ratio:.1f}x volume")
        if curr["close"] < orb_low * 0.999 and ind.volume_ratio >= 1.5:
            return PatternResult("ORB Bearish Breakdown", "SHORT", 80,
                                 f"ORB breakdown below ₹{orb_low:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    # --------------------------------------------------------
    # COMPOSITE SCORE
    # --------------------------------------------------------

    def _compute_composite_score(
        self, patterns: List[PatternResult], ind: IndicatorSet
    ) -> Dict:
        """
        Compute composite bullish/bearish score from all patterns + indicators.
        Returns {"long": 0-100, "short": 0-100, "direction": "LONG"/"SHORT"/"NEUTRAL"}
        """
        long_score = 0.0
        short_score = 0.0

        # Pattern scores
        for p in patterns:
            if p.direction == "LONG":
                long_score += p.confidence * 0.6
            elif p.direction == "SHORT":
                short_score += p.confidence * 0.6

        # Indicator confluence
        # RSI
        if ind.rsi < 35:
            long_score += 15
        elif ind.rsi > 65:
            short_score += 15

        # MACD histogram direction
        if ind.macd_hist > 0:
            long_score += 10
        elif ind.macd_hist < 0:
            short_score += 10

        # EMA alignment
        if ind.ema9 > ind.ema21 > ind.ema50:
            long_score += 12
        elif ind.ema9 < ind.ema21 < ind.ema50:
            short_score += 12

        # Volume confirmation
        if ind.volume_ratio >= 2.0:
            if long_score > short_score:
                long_score += 10
            else:
                short_score += 10

        # Supertrend
        if ind.supertrend_dir == 1:
            long_score += 8
        else:
            short_score += 8

        # ADX trend strength
        if ind.adx > 25:
            if ind.plus_di > ind.minus_di:
                long_score += 8
            else:
                short_score += 8

        # Normalize to 0-100
        max_score = max(long_score, short_score, 1)
        long_norm = min((long_score / max_score) * 100 * (max_score / 150), 100)
        short_norm = min((short_score / max_score) * 100 * (max_score / 150), 100)

        direction = "NEUTRAL"
        if long_norm > short_norm and long_norm >= 55:
            direction = "LONG"
        elif short_norm > long_norm and short_norm >= 55:
            direction = "SHORT"

        return {
            "long": round(long_norm, 1),
            "short": round(short_norm, 1),
            "direction": direction,
            "dominant": max(long_norm, short_norm),
        }
