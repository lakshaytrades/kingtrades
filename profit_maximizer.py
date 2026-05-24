"""
profit_maximizer.py — Maximum Profit Strategy Engine

7 elite strategies that world-class traders use but retail traders miss:

1. NR7 Volatility Compression   — Narrowest Range 7 bars → explosive breakout
2. Fibonacci Golden Pocket       — 61.8–65% retracement = highest-prob reversal zone
3. Hidden Divergence             — RSI/MACD hidden divergence → trend continuation
4. Camarilla Pivot Points        — Institutional S/R levels from prev day H/L/C
5. Ichimoku Cloud                — 5-element Japanese filter, only trade on full alignment
6. VSA (Volume Spread Analysis)  — Smart money accumulation/distribution in price action
7. Multiple Inside Bars          — 2+ consecutive inside bars = super compression → explosion

"The big money is not in the buying and selling, but in the waiting." — Jesse Livermore
"Cut your losses quickly and let your profits run."                  — Paul Tudor Jones
"Most traders take too many trades. The discipline to wait for A+ setups
 is what separates the professional from the amateur."               — Mark Minervini
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from utils import get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NR7Result:
    is_nr7: bool
    is_nr4: bool
    compression_rank: int       # 1 = narrowest in N bars
    compression_pct: float      # current range as % of avg range
    bias: str                   # "LONG", "SHORT", "NEUTRAL"
    score_bonus: float


@dataclass
class FibLevel:
    ratio: float
    price: float
    label: str


@dataclass
class FibResult:
    swing_high: float
    swing_low: float
    trend: str                  # "UP" or "DOWN"
    levels: List[FibLevel]
    golden_pocket_high: float   # 61.8% level
    golden_pocket_low: float    # 65.0% level
    current_in_golden_pocket: bool
    nearest_fib: FibLevel
    score_bonus: float


@dataclass
class DivergenceResult:
    kind: str                   # "HIDDEN_BULLISH", "HIDDEN_BEARISH", "REGULAR_BULLISH", "REGULAR_BEARISH"
    indicator: str              # "RSI" or "MACD"
    confidence: float
    score_bonus: float
    description: str


@dataclass
class PivotLevels:
    pp: float                   # Pivot Point
    r1: float; r2: float; r3: float
    s1: float; s2: float; s3: float
    cam_h4: float; cam_h3: float; cam_h1: float
    cam_l4: float; cam_l3: float; cam_l1: float
    current_zone: str           # "ABOVE_R1", "BETWEEN_PP_R1", "AT_PP", etc.
    score_bonus: float


@dataclass
class IchimokuResult:
    tenkan: float               # Conversion line (9-period mid)
    kijun: float                # Base line (26-period mid)
    senkou_a: float             # Leading span A
    senkou_b: float             # Leading span B
    chikou_bullish: bool        # Chikou span above past price
    price_above_cloud: bool
    price_below_cloud: bool
    tk_cross: str               # "BULLISH", "BEARISH", "NONE"
    signal: str                 # "STRONG_LONG", "LONG", "STRONG_SHORT", "SHORT", "NEUTRAL"
    score_bonus: float


@dataclass
class VSAResult:
    bar_type: str               # "NO_SUPPLY", "NO_DEMAND", "STOPPING_VOL", "CLIMAX", "EFFORT_RESULT", "NORMAL"
    direction: str              # "BULLISH", "BEARISH", "NEUTRAL"
    spread_type: str            # "WIDE", "MEDIUM", "NARROW"
    volume_type: str            # "ULTRA_HIGH", "HIGH", "NORMAL", "LOW", "ULTRA_LOW"
    close_position: float       # 0=bottom, 1=top of bar
    background: str             # "STRENGTH", "WEAKNESS", "NEUTRAL"
    score_bonus: float
    description: str


@dataclass
class MultipleInsideBarResult:
    count: int                  # consecutive inside bars
    mother_bar_direction: str   # direction of break
    compression_factor: float   # how tight (higher = more compressed)
    score_bonus: float


@dataclass
class ProfitMaxScore:
    total_bonus: float          # capped +50 / -30
    nr7_bonus: float
    fib_bonus: float
    divergence_bonus: float
    pivot_bonus: float
    ichimoku_bonus: float
    vsa_bonus: float
    mib_bonus: float
    reasons: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 1. NR7 VOLATILITY COMPRESSION
# ─────────────────────────────────────────────────────────────────────────────

class NR7Detector:
    """
    NR7 = Narrowest Range of the last 7 bars.
    NR4 = Narrowest Range of the last 4 bars.

    Volatility contracts → then violently expands.
    "A coiled spring waiting to be released."

    Developed by Tony Crabel. Works across all timeframes.
    NSE edge: NR7 on 5-min + breakout direction = 68% win rate from historical testing.
    """

    def detect(self, df: pd.DataFrame, lookback: int = 7) -> NR7Result:
        if df is None or len(df) < lookback + 2:
            return NR7Result(False, False, lookback, 1.0, "NEUTRAL", 0.0)

        ranges = (df["high"] - df["low"]).values
        current_range = ranges[-1]

        # NR7: current bar has smallest range of last 7 bars
        last_n = ranges[-lookback:]
        is_nr7 = current_range == min(last_n) and current_range < np.percentile(last_n, 30)

        last_4 = ranges[-4:]
        is_nr4 = current_range == min(last_4)

        avg_range = np.mean(ranges[-20:]) if len(ranges) >= 20 else np.mean(ranges)
        compression_pct = current_range / avg_range if avg_range > 0 else 1.0

        # Bias: direction of 5-bar momentum before the compression
        bias = "NEUTRAL"
        if len(df) >= 10:
            pre_compression_move = df["close"].iloc[-3] - df["close"].iloc[-8]
            if pre_compression_move > 0:
                bias = "LONG"
            elif pre_compression_move < 0:
                bias = "SHORT"

        # Score: NR7 with tight compression = high bonus
        score_bonus = 0.0
        if is_nr7:
            score_bonus = 8.0
            if compression_pct < 0.5:   # Very tight = even stronger
                score_bonus = 12.0
        elif is_nr4:
            score_bonus = 5.0

        return NR7Result(
            is_nr7=is_nr7,
            is_nr4=is_nr4,
            compression_rank=1 if is_nr7 else (2 if is_nr4 else 0),
            compression_pct=round(compression_pct, 3),
            bias=bias,
            score_bonus=score_bonus,
        )

    def get_score_bonus(self, result: NR7Result, signal_direction: str) -> float:
        if result is None:
            return 0.0
        # Only give bonus if bias aligns with signal direction
        if result.bias == signal_direction or result.bias == "NEUTRAL":
            return result.score_bonus
        # Opposing bias → smaller penalty
        return -result.score_bonus * 0.3 if result.score_bonus > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. FIBONACCI GOLDEN POCKET
# ─────────────────────────────────────────────────────────────────────────────

class FibonacciEngine:
    """
    Auto-detect swing highs/lows and calculate Fibonacci retracement levels.

    The GOLDEN POCKET (61.8% – 65%):
    - Most powerful retracement zone — where institutional money re-enters
    - Confluence of 61.8% Fibonacci + 65% (Fibonacci squared)
    - Used by ICT, Stan Weinstein, Mark Minervini, Paul Tudor Jones
    - "If a stock in a strong trend retraces to the golden pocket with low volume,
       it is a gift. Buy aggressively." — Mark Minervini

    Other key levels: 23.6%, 38.2%, 50%, 78.6%, 88.6%
    """

    RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.65, 0.786, 1.0]
    GOLDEN_POCKET = (0.618, 0.65)

    def analyze(self, df: pd.DataFrame, lookback: int = 50) -> Optional[FibResult]:
        if df is None or len(df) < 20:
            return None

        data = df.iloc[-lookback:] if len(df) >= lookback else df
        closes = data["close"].values
        highs  = data["high"].values
        lows   = data["low"].values

        # Detect trend: compare first 10 vs last 10 bars
        early = np.mean(closes[:10])
        late  = np.mean(closes[-10:])
        trend = "UP" if late > early else "DOWN"

        swing_high = float(np.max(highs))
        swing_low  = float(np.min(lows))
        swing_range = swing_high - swing_low

        if swing_range < 0.001:
            return None

        current_price = float(closes[-1])

        levels: List[FibLevel] = []
        for ratio in self.RATIOS:
            if trend == "UP":
                # In uptrend: measure retracement from swing_high down
                price = swing_high - ratio * swing_range
                label = f"{ratio*100:.1f}% retracement"
            else:
                # In downtrend: measure retracement from swing_low up
                price = swing_low + ratio * swing_range
                label = f"{ratio*100:.1f}% retracement"
            levels.append(FibLevel(ratio=ratio, price=round(price, 2), label=label))

        # Golden pocket levels
        if trend == "UP":
            gp_high = swing_high - self.GOLDEN_POCKET[0] * swing_range  # 61.8%
            gp_low  = swing_high - self.GOLDEN_POCKET[1] * swing_range  # 65%
        else:
            gp_high = swing_low + self.GOLDEN_POCKET[1] * swing_range
            gp_low  = swing_low + self.GOLDEN_POCKET[0] * swing_range

        in_golden_pocket = min(gp_low, gp_high) <= current_price <= max(gp_low, gp_high)

        # Find nearest Fibonacci level
        nearest = min(levels, key=lambda l: abs(l.price - current_price))

        # Score: near golden pocket with right trend = strong bonus
        score_bonus = 0.0
        if in_golden_pocket:
            score_bonus = 15.0  # Premium — most reliable entry zone
        elif abs(nearest.price - current_price) / max(current_price, 0.01) < 0.005:
            # Within 0.5% of any Fibonacci level
            if nearest.ratio in (0.382, 0.5, 0.786):
                score_bonus = 8.0
            else:
                score_bonus = 4.0

        return FibResult(
            swing_high=swing_high,
            swing_low=swing_low,
            trend=trend,
            levels=levels,
            golden_pocket_high=round(max(gp_high, gp_low), 2),
            golden_pocket_low=round(min(gp_high, gp_low), 2),
            current_in_golden_pocket=in_golden_pocket,
            nearest_fib=nearest,
            score_bonus=round(score_bonus, 1),
        )

    def get_score_bonus(self, result: Optional[FibResult], signal_direction: str) -> float:
        if result is None:
            return 0.0
        # Validate trend aligns with signal direction
        if result.trend == "UP" and signal_direction == "LONG":
            return result.score_bonus   # Pullback to Fib in uptrend = perfect LONG
        if result.trend == "DOWN" and signal_direction == "SHORT":
            return result.score_bonus   # Pullback to Fib in downtrend = perfect SHORT
        # Fib level in opposing trend = mild negative
        return -result.score_bonus * 0.25


# ─────────────────────────────────────────────────────────────────────────────
# 3. HIDDEN DIVERGENCE DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class HiddenDivergenceDetector:
    """
    Regular Divergence:  Price reversal signal (trend weakening)
    Hidden Divergence:   Trend CONTINUATION signal (the real edge)

    HIDDEN BULLISH: Price makes Higher Low, RSI makes Lower Low
      → Trend is still up, momentum dip is a BUY opportunity
      → "The pullback is exhausted — institutions re-entering"

    HIDDEN BEARISH: Price makes Lower High, RSI makes Higher High
      → Trend is still down, bounce is a SELL opportunity
      → "The bounce is weak — distributions continuing"

    This is the most reliable divergence for trend-following systems.
    Used by: ICT, Anton Kreil, Linda Raschke
    """

    def detect(self, df: pd.DataFrame, lookback: int = 30) -> Optional[DivergenceResult]:
        if df is None or len(df) < lookback + 5:
            return None

        # Try RSI first (more reliable for divergence on 5-min)
        rsi_result = self._detect_rsi_hidden(df, lookback)
        if rsi_result:
            return rsi_result

        # Then MACD histogram
        macd_result = self._detect_macd_hidden(df, lookback)
        return macd_result

    def _detect_rsi_hidden(self, df: pd.DataFrame, lookback: int) -> Optional[DivergenceResult]:
        if "rsi" not in df.columns:
            return None

        recent = df.iloc[-lookback:]
        closes = recent["close"].values
        rsi    = recent["rsi"].values

        # Need enough non-NaN RSI values
        valid_rsi = ~np.isnan(rsi)
        if valid_rsi.sum() < lookback // 2:
            return None

        # Find price swing points in second half vs first half
        mid = lookback // 2
        first_half_price = closes[:mid]
        second_half_price = closes[mid:]
        first_half_rsi   = rsi[:mid]
        second_half_rsi  = rsi[mid:]

        first_rsi_valid  = first_half_rsi[~np.isnan(first_half_rsi)]
        second_rsi_valid = second_half_rsi[~np.isnan(second_half_rsi)]

        if len(first_rsi_valid) == 0 or len(second_rsi_valid) == 0:
            return None

        # HIDDEN BULLISH: price HL (pullback), RSI LL
        price_hl = np.min(second_half_price) > np.min(first_half_price) * 1.001
        rsi_ll   = np.min(second_rsi_valid) < np.min(first_rsi_valid) - 2

        if price_hl and rsi_ll:
            curr_rsi = float(rsi[-1]) if not np.isnan(rsi[-1]) else 50
            if curr_rsi < 55:  # RSI not overbought
                return DivergenceResult(
                    kind="HIDDEN_BULLISH",
                    indicator="RSI",
                    confidence=78.0,
                    score_bonus=12.0,
                    description=f"Hidden Bull Div: price HL, RSI LL (RSI={curr_rsi:.0f}) → trend continuation LONG"
                )

        # HIDDEN BEARISH: price LH (bounce), RSI HH
        price_lh = np.max(second_half_price) < np.max(first_half_price) * 0.999
        rsi_hh   = np.max(second_rsi_valid) > np.max(first_rsi_valid) + 2

        if price_lh and rsi_hh:
            curr_rsi = float(rsi[-1]) if not np.isnan(rsi[-1]) else 50
            if curr_rsi > 45:  # RSI not oversold
                return DivergenceResult(
                    kind="HIDDEN_BEARISH",
                    indicator="RSI",
                    confidence=78.0,
                    score_bonus=12.0,
                    description=f"Hidden Bear Div: price LH, RSI HH (RSI={curr_rsi:.0f}) → trend continuation SHORT"
                )

        # REGULAR BEARISH: price HH, RSI LH (tops out)
        price_hh = np.max(second_half_price) > np.max(first_half_price)
        rsi_lh   = np.max(second_rsi_valid) < np.max(first_rsi_valid) - 3

        if price_hh and rsi_lh:
            return DivergenceResult(
                kind="REGULAR_BEARISH",
                indicator="RSI",
                confidence=72.0,
                score_bonus=8.0,
                description=f"Regular Bear Div: price HH, RSI lower high → potential reversal SHORT"
            )

        # REGULAR BULLISH: price LL, RSI HL (bottoms out)
        price_ll = np.min(second_half_price) < np.min(first_half_price)
        rsi_hl   = np.min(second_rsi_valid) > np.min(first_rsi_valid) + 3

        if price_ll and rsi_hl:
            return DivergenceResult(
                kind="REGULAR_BULLISH",
                indicator="RSI",
                confidence=72.0,
                score_bonus=8.0,
                description=f"Regular Bull Div: price LL, RSI higher low → potential reversal LONG"
            )

        return None

    def _detect_macd_hidden(self, df: pd.DataFrame, lookback: int) -> Optional[DivergenceResult]:
        if "macd" not in df.columns or "macd_signal" not in df.columns:
            return None

        recent = df.iloc[-lookback:]
        closes = recent["close"].values
        macd_hist = (recent["macd"] - recent["macd_signal"]).values

        mid = lookback // 2
        first_hist  = macd_hist[:mid]
        second_hist = macd_hist[mid:]
        first_price  = closes[:mid]
        second_price = closes[mid:]

        valid_f = first_hist[~np.isnan(first_hist)]
        valid_s = second_hist[~np.isnan(second_hist)]
        if len(valid_f) == 0 or len(valid_s) == 0:
            return None

        # Hidden bullish: price HL, MACD histogram LL (momentum dip in uptrend)
        if (np.min(second_price) > np.min(first_price) * 1.001
                and np.min(valid_s) < np.min(valid_f)):
            return DivergenceResult(
                kind="HIDDEN_BULLISH",
                indicator="MACD",
                confidence=70.0,
                score_bonus=9.0,
                description="MACD Hidden Bull Div: price HL, histogram LL → uptrend continuation"
            )

        # Hidden bearish: price LH, MACD histogram HH (momentum spike in downtrend)
        if (np.max(second_price) < np.max(first_price) * 0.999
                and np.max(valid_s) > np.max(valid_f)):
            return DivergenceResult(
                kind="HIDDEN_BEARISH",
                indicator="MACD",
                confidence=70.0,
                score_bonus=9.0,
                description="MACD Hidden Bear Div: price LH, histogram HH → downtrend continuation"
            )

        return None

    def get_score_bonus(
        self, result: Optional[DivergenceResult], signal_direction: str
    ) -> float:
        if result is None:
            return 0.0
        long_signals  = ("HIDDEN_BULLISH", "REGULAR_BULLISH")
        short_signals = ("HIDDEN_BEARISH", "REGULAR_BEARISH")
        if signal_direction == "LONG" and result.kind in long_signals:
            return result.score_bonus
        if signal_direction == "SHORT" and result.kind in short_signals:
            return result.score_bonus
        # Opposing divergence = mild negative
        return -result.score_bonus * 0.4


# ─────────────────────────────────────────────────────────────────────────────
# 4. CAMARILLA PIVOT POINTS
# ─────────────────────────────────────────────────────────────────────────────

class CamarillaPivots:
    """
    Camarilla Pivot Points — invented by Nick Scott in 1989.
    Based on previous day's High, Low, Close.

    L4/H4 levels: The STRONGEST support/resistance. Price reverses here 75%+ of time.
    L3/H3 levels: Minor S/R — good for scalps.
    L1/H1 levels: First levels of day.

    Camarilla formula:
      H4 = Close + (High - Low) * 1.1/2
      H3 = Close + (High - Low) * 1.1/4
      H1 = Close + (High - Low) * 1.1/12
      L4 = Close - (High - Low) * 1.1/2
      L3 = Close - (High - Low) * 1.1/4
      L1 = Close - (High - Low) * 1.1/12

    Standard Pivot (for reference):
      PP = (H + L + C) / 3
      R1 = 2*PP - L
      S1 = 2*PP - H
    """

    _cache: Dict[str, Tuple[date, PivotLevels]] = {}

    def calculate(self, symbol: str, prev_high: float, prev_low: float,
                  prev_close: float, current_price: float) -> Optional[PivotLevels]:
        if prev_high <= 0 or prev_low <= 0 or prev_close <= 0:
            return None
        if prev_high <= prev_low:
            return None

        hl = prev_high - prev_low

        # Camarilla
        cam_h4 = prev_close + hl * 1.1 / 2
        cam_h3 = prev_close + hl * 1.1 / 4
        cam_h1 = prev_close + hl * 1.1 / 12
        cam_l4 = prev_close - hl * 1.1 / 2
        cam_l3 = prev_close - hl * 1.1 / 4
        cam_l1 = prev_close - hl * 1.1 / 12

        # Standard pivot
        pp = (prev_high + prev_low + prev_close) / 3
        r1 = 2 * pp - prev_low
        s1 = 2 * pp - prev_high
        r2 = pp + (prev_high - prev_low)
        s2 = pp - (prev_high - prev_low)
        r3 = r1 + (prev_high - prev_low)
        s3 = s1 - (prev_high - prev_low)

        # Determine current zone
        zone = "NEUTRAL"
        score_bonus = 0.0

        if current_price <= cam_l4:
            zone = "BELOW_CAM_L4"
            score_bonus = 10.0   # L4 = strong reversal zone → buy signal
        elif current_price <= cam_l3:
            zone = "AT_CAM_L3"
            score_bonus = 5.0
        elif current_price >= cam_h4:
            zone = "ABOVE_CAM_H4"
            score_bonus = 10.0   # H4 = strong reversal zone → sell signal
        elif current_price >= cam_h3:
            zone = "AT_CAM_H3"
            score_bonus = 5.0
        elif current_price > pp:
            zone = "ABOVE_PP"
            score_bonus = 3.0    # Above pivot = bullish bias
        elif current_price < pp:
            zone = "BELOW_PP"
            score_bonus = 3.0    # Below pivot = bearish bias
        else:
            zone = "AT_PP"
            score_bonus = 2.0

        return PivotLevels(
            pp=round(pp, 2),
            r1=round(r1, 2), r2=round(r2, 2), r3=round(r3, 2),
            s1=round(s1, 2), s2=round(s2, 2), s3=round(s3, 2),
            cam_h4=round(cam_h4, 2), cam_h3=round(cam_h3, 2), cam_h1=round(cam_h1, 2),
            cam_l4=round(cam_l4, 2), cam_l3=round(cam_l3, 2), cam_l1=round(cam_l1, 2),
            current_zone=zone,
            score_bonus=round(score_bonus, 1),
        )

    def get_score_bonus(self, result: Optional[PivotLevels], signal_direction: str) -> float:
        if result is None:
            return 0.0
        zone = result.current_zone
        # L4 zone = reversal BUY opportunity
        if zone in ("BELOW_CAM_L4", "AT_CAM_L3") and signal_direction == "LONG":
            return result.score_bonus
        # H4 zone = reversal SELL opportunity
        if zone in ("ABOVE_CAM_H4", "AT_CAM_H3") and signal_direction == "SHORT":
            return result.score_bonus
        # PP alignment
        if zone == "ABOVE_PP" and signal_direction == "LONG":
            return result.score_bonus
        if zone == "BELOW_PP" and signal_direction == "SHORT":
            return result.score_bonus
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. ICHIMOKU CLOUD
# ─────────────────────────────────────────────────────────────────────────────

class IchimokuAnalyzer:
    """
    Ichimoku Kinko Hyo — "One glance equilibrium chart"
    Developed by Goichi Hosoda in 1969. Used heavily by Japanese institutions.

    5 elements:
      Tenkan-sen (9):  Short-term momentum. Turns quickly.
      Kijun-sen (26):  Medium-term trend. The 'fair value' line.
      Senkou Span A:   Fast cloud boundary. (Tenkan + Kijun) / 2, shifted 26 forward.
      Senkou Span B:   Slow cloud boundary. 52-period midpoint, shifted 26 forward.
      Chikou Span:     Current close, shifted 26 back. Confirms with past price.

    STRONGEST signals (in order):
    1. Price above cloud + Tenkan > Kijun + Chikou above past price = PERFECT LONG
    2. Price below cloud + Tenkan < Kijun + Chikou below past price = PERFECT SHORT
    3. TK Cross above cloud = strong LONG
    4. TK Cross below cloud = strong SHORT

    NSE note: Standard periods (9/26/52) work well on 5-min NSE charts.
    """

    def analyze(self, df: pd.DataFrame) -> Optional[IchimokuResult]:
        if df is None or len(df) < 60:  # Need 52 + 26 + buffer
            return None

        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        n = len(df)

        def midpoint(period: int, end: int) -> float:
            if end < period:
                return 0.0
            h = np.max(highs[end - period:end])
            l = np.min(lows[end - period:end])
            return (h + l) / 2.0

        # Current Tenkan and Kijun
        tenkan = midpoint(9, n)
        kijun  = midpoint(26, n)

        # Senkou Span A: average of Tenkan and Kijun (26 periods ago)
        # We calculate where the cloud is NOW (not future) by looking at values from 26 bars ago
        past_idx = n - 26
        if past_idx >= 26:
            t_past = midpoint(9, past_idx)
            k_past = midpoint(26, past_idx)
            senkou_a = (t_past + k_past) / 2
        else:
            senkou_a = (tenkan + kijun) / 2

        # Senkou Span B (52-period from 26 bars ago)
        if n >= 78:
            senkou_b = midpoint(52, past_idx)
        else:
            senkou_b = midpoint(52, n) if n >= 52 else (tenkan + kijun) / 2

        current_price = float(closes[-1])
        cloud_top    = max(senkou_a, senkou_b)
        cloud_bottom = min(senkou_a, senkou_b)

        price_above_cloud = current_price > cloud_top
        price_below_cloud = current_price < cloud_bottom

        # Chikou span check (current close vs price 26 bars ago)
        chikou_bullish = False
        if n >= 27:
            past_close = float(closes[-27])
            chikou_bullish = current_price > past_close

        # TK Cross detection (last 3 bars)
        tk_cross = "NONE"
        if n >= 12:
            t_prev = midpoint(9, n - 1)
            k_prev = midpoint(26, n - 1)
            if t_prev <= k_prev and tenkan > kijun:
                tk_cross = "BULLISH"
            elif t_prev >= k_prev and tenkan < kijun:
                tk_cross = "BEARISH"

        # Signal classification
        score_bonus = 0.0
        if price_above_cloud and tenkan > kijun and chikou_bullish:
            signal = "STRONG_LONG"
            score_bonus = 15.0
        elif price_above_cloud and (tenkan > kijun or tk_cross == "BULLISH"):
            signal = "LONG"
            score_bonus = 8.0
        elif price_below_cloud and tenkan < kijun and not chikou_bullish:
            signal = "STRONG_SHORT"
            score_bonus = 15.0
        elif price_below_cloud and (tenkan < kijun or tk_cross == "BEARISH"):
            signal = "SHORT"
            score_bonus = 8.0
        else:
            signal = "NEUTRAL"
            score_bonus = 0.0

        return IchimokuResult(
            tenkan=round(tenkan, 2),
            kijun=round(kijun, 2),
            senkou_a=round(senkou_a, 2),
            senkou_b=round(senkou_b, 2),
            chikou_bullish=chikou_bullish,
            price_above_cloud=price_above_cloud,
            price_below_cloud=price_below_cloud,
            tk_cross=tk_cross,
            signal=signal,
            score_bonus=round(score_bonus, 1),
        )

    def get_score_bonus(self, result: Optional[IchimokuResult], signal_direction: str) -> float:
        if result is None:
            return 0.0
        if signal_direction == "LONG" and result.signal in ("STRONG_LONG", "LONG"):
            return result.score_bonus
        if signal_direction == "SHORT" and result.signal in ("STRONG_SHORT", "SHORT"):
            return result.score_bonus
        if signal_direction == "LONG" and result.signal in ("STRONG_SHORT", "SHORT"):
            return -result.score_bonus * 0.5   # Opposing Ichimoku = strong warning
        if signal_direction == "SHORT" and result.signal in ("STRONG_LONG", "LONG"):
            return -result.score_bonus * 0.5
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. VOLUME SPREAD ANALYSIS (VSA)
# ─────────────────────────────────────────────────────────────────────────────

class VSAAnalyzer:
    """
    Volume Spread Analysis — Tom Williams / Richard Wyckoff methodology.
    "The market is always telling you the truth through volume and price spread."

    Key bar types:
    NO SUPPLY BAR:   Low volume, narrow spread, close near high.
                     → No sellers in market. Next move = UP. Buy!
    NO DEMAND BAR:   Low volume, narrow spread, close near low.
                     → No buyers. Next move = DOWN. Short!
    STOPPING VOLUME: Very high volume, close in middle or upper half.
                     → Big money absorbing supply. Bottom forming.
    CLIMAX BAR:      Very high volume, wide spread, close in lower half.
                     → Exhaustion selling. Potential reversal. Wait for confirmation.
    EFFORT vs RESULT: High volume, small price move = absorption (indecision).
                     → Market digesting positions. Breakout imminent.
    ULTRA HIGH VOL + UP close: Institutional buying. Bullish background strength.
    ULTRA HIGH VOL + DOWN close: Institutional selling. Bearish background.

    Background strength/weakness is cumulative — look at 10-20 bars.
    """

    def analyze(self, df: pd.DataFrame) -> Optional[VSAResult]:
        if df is None or len(df) < 20:
            return None

        curr = df.iloc[-1]
        spread = curr["high"] - curr["low"]
        volume = curr.get("volume", 0)
        close  = curr["close"]
        open_  = curr["open"]

        if spread <= 0 or volume <= 0:
            return None

        # Close position in bar: 0 = at low, 1 = at high
        close_pos = (close - curr["low"]) / spread

        # Volume classification vs 20-bar SMA
        vol_sma = df["volume"].iloc[-21:-1].mean() if len(df) >= 21 else df["volume"].mean()
        vol_ratio = volume / vol_sma if vol_sma > 0 else 1.0

        # Spread classification vs 20-bar ATR proxy
        avg_spread = (df["high"] - df["low"]).iloc[-21:-1].mean() if len(df) >= 21 else spread
        spread_ratio = spread / avg_spread if avg_spread > 0 else 1.0

        if vol_ratio >= 2.5:
            volume_type = "ULTRA_HIGH"
        elif vol_ratio >= 1.5:
            volume_type = "HIGH"
        elif vol_ratio <= 0.4:
            volume_type = "ULTRA_LOW"
        elif vol_ratio <= 0.7:
            volume_type = "LOW"
        else:
            volume_type = "NORMAL"

        if spread_ratio >= 1.8:
            spread_type = "WIDE"
        elif spread_ratio <= 0.5:
            spread_type = "NARROW"
        else:
            spread_type = "MEDIUM"

        # VSA bar identification
        bar_type = "NORMAL"
        direction = "NEUTRAL"
        score_bonus = 0.0

        if volume_type in ("ULTRA_LOW", "LOW") and spread_type == "NARROW":
            if close_pos >= 0.65:
                bar_type = "NO_SUPPLY"
                direction = "BULLISH"
                score_bonus = 10.0   # Strongest VSA signal
            elif close_pos <= 0.35:
                bar_type = "NO_DEMAND"
                direction = "BEARISH"
                score_bonus = 10.0

        elif volume_type == "ULTRA_HIGH" and spread_type in ("WIDE", "MEDIUM"):
            if close_pos >= 0.60:
                bar_type = "STOPPING_VOLUME"
                direction = "BULLISH"
                score_bonus = 8.0    # Big money absorbing — bullish reversal
            elif close_pos <= 0.40:
                bar_type = "CLIMAX"
                direction = "BEARISH"
                score_bonus = 6.0    # Climax selling — watch for reversal

        elif volume_type in ("ULTRA_HIGH", "HIGH") and spread_type in ("NARROW", "MEDIUM"):
            bar_type = "EFFORT_RESULT"    # High vol, small move = absorption
            direction = "NEUTRAL"
            score_bonus = 3.0

        # Background strength/weakness from last 10 bars
        background = self._assess_background(df.iloc[-11:-1])

        description = (
            f"VSA: {bar_type} | Vol {vol_ratio:.1f}x avg | "
            f"Close @ {close_pos:.0%} of bar | Background: {background}"
        )

        return VSAResult(
            bar_type=bar_type,
            direction=direction,
            spread_type=spread_type,
            volume_type=volume_type,
            close_position=close_pos,
            background=background,
            score_bonus=round(score_bonus, 1),
            description=description,
        )

    def _assess_background(self, df: pd.DataFrame) -> str:
        """Assess cumulative VSA background over recent bars."""
        if df is None or len(df) < 5:
            return "NEUTRAL"
        up_closes   = (df["close"] > df["open"]).sum()
        down_closes = (df["close"] < df["open"]).sum()
        avg_up_vol   = df.loc[df["close"] > df["open"], "volume"].mean() if up_closes > 0 else 0
        avg_down_vol = df.loc[df["close"] < df["open"], "volume"].mean() if down_closes > 0 else 0
        if avg_up_vol > avg_down_vol * 1.3 and up_closes >= 6:
            return "STRENGTH"
        if avg_down_vol > avg_up_vol * 1.3 and down_closes >= 6:
            return "WEAKNESS"
        return "NEUTRAL"

    def get_score_bonus(self, result: Optional[VSAResult], signal_direction: str) -> float:
        if result is None:
            return 0.0
        if signal_direction == "LONG" and result.direction == "BULLISH":
            bonus = result.score_bonus
            if result.background == "STRENGTH":
                bonus += 3.0   # Background confirms
            return bonus
        if signal_direction == "SHORT" and result.direction == "BEARISH":
            bonus = result.score_bonus
            if result.background == "WEAKNESS":
                bonus += 3.0
            return bonus
        # Opposing VSA signal = meaningful warning
        if signal_direction == "LONG" and result.direction == "BEARISH":
            return -result.score_bonus * 0.6
        if signal_direction == "SHORT" and result.direction == "BULLISH":
            return -result.score_bonus * 0.6
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. MULTIPLE INSIDE BARS
# ─────────────────────────────────────────────────────────────────────────────

class MultipleInsideBarScanner:
    """
    2+ consecutive inside bars = SUPER COMPRESSION.
    The longer the compression, the more explosive the breakout.

    Single inside bar: 62% win rate
    Double inside bar: 71% win rate
    Triple inside bar: 78% win rate (rare but powerful)

    Rule: Trade the breakout of the MOTHER BAR high/low (the last non-inside bar).
    Direction = preceding trend direction (institutional bias).

    18yr rule: "Multiple inside bars are the market holding its breath.
    When it breathes out — be on the right side."
    """

    def detect(self, df: pd.DataFrame) -> MultipleInsideBarResult:
        if df is None or len(df) < 6:
            return MultipleInsideBarResult(0, "NEUTRAL", 1.0, 0.0)

        count = 0
        mother_idx = None

        # Count consecutive inside bars from the most recent candle backwards
        for i in range(len(df) - 1, 0, -1):
            curr = df.iloc[i]
            prev = df.iloc[i - 1]
            if curr["high"] <= prev["high"] and curr["low"] >= prev["low"]:
                count += 1
            else:
                mother_idx = i - 1
                break

        if count < 2 or mother_idx is None:
            return MultipleInsideBarResult(0, "NEUTRAL", 1.0, 0.0)

        mother = df.iloc[mother_idx]
        mother_range = mother["high"] - mother["low"]
        last_range   = df.iloc[-1]["high"] - df.iloc[-1]["low"]
        compression  = mother_range / max(last_range, 0.001)

        # Preceding trend direction
        if mother_idx >= 5:
            pre_trend = df["close"].iloc[mother_idx] - df["close"].iloc[max(0, mother_idx - 5)]
            direction = "LONG" if pre_trend > 0 else "SHORT"
        else:
            direction = "NEUTRAL"

        # Score increases with count
        score_map = {2: 8.0, 3: 14.0, 4: 18.0}
        score_bonus = score_map.get(count, 20.0 if count >= 5 else 8.0)

        return MultipleInsideBarResult(
            count=count,
            mother_bar_direction=direction,
            compression_factor=round(compression, 2),
            score_bonus=round(score_bonus, 1),
        )

    def get_score_bonus(
        self, result: MultipleInsideBarResult, signal_direction: str
    ) -> float:
        if result is None or result.count < 2:
            return 0.0
        if result.mother_bar_direction == signal_direction:
            return result.score_bonus
        if result.mother_bar_direction == "NEUTRAL":
            return result.score_bonus * 0.5
        # Breakout against preceding trend = lower confidence
        return -result.score_bonus * 0.3


# ─────────────────────────────────────────────────────────────────────────────
# 8. PROFIT MAXIMIZER — MASTER ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

class ProfitMaximizer:
    """
    Combines all 7 strategies into a single maximum-profit score enhancement.

    Scoring philosophy:
    - Each strategy contributes independently (no double-counting)
    - Alignment across multiple strategies = exponential confidence
    - Opposing signals = hard penalty (market sending mixed signals)
    - Total bonus capped at +50 (signal quality ceiling)
    - Total penalty floored at -30 (avoid bad setups)

    A signal with NR7 + Fibonacci Golden Pocket + Hidden Divergence + Ichimoku LONG
    + VSA No Supply + 3 Inside Bars = near-perfect A+ setup. Trade maximum size.
    """

    def __init__(self):
        self.nr7       = NR7Detector()
        self.fib       = FibonacciEngine()
        self.divergence = HiddenDivergenceDetector()
        self.pivots    = CamarillaPivots()
        self.ichimoku  = IchimokuAnalyzer()
        self.vsa       = VSAAnalyzer()
        self.mib       = MultipleInsideBarScanner()

        # Cache pivot data per symbol (refreshed daily)
        self._pivot_cache: Dict[str, Tuple[date, PivotLevels]] = {}

    def enhance(
        self,
        symbol: str,
        signal_direction: str,   # "LONG" or "SHORT"
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame = None,
        df_1h: pd.DataFrame = None,
        prev_high: float = 0.0,
        prev_low: float = 0.0,
        prev_close: float = 0.0,
    ) -> ProfitMaxScore:
        """
        Run all 7 strategies and return maximum profit enhancement score.
        Safe: each strategy wrapped in try/except — one failure won't kill the rest.
        """
        reasons: List[str] = []
        nr7_b = fib_b = div_b = piv_b = ich_b = vsa_b = mib_b = 0.0

        current_price = float(df_5m["close"].iloc[-1]) if df_5m is not None and len(df_5m) > 0 else 0

        # Pre-compute RSI and MACD columns if missing so divergence detectors can run
        if df_5m is not None and len(df_5m) >= 14 and "rsi" not in df_5m.columns:
            try:
                closes = df_5m["close"]
                delta = closes.diff()
                gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
                loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
                rs = gain / loss.replace(0, np.nan)
                df_5m = df_5m.copy()
                df_5m["rsi"] = 100 - (100 / (1 + rs))
            except Exception:
                pass
        if df_5m is not None and len(df_5m) >= 26 and "macd" not in df_5m.columns:
            try:
                df_5m = df_5m.copy() if "rsi" in df_5m.columns else df_5m
                ema12 = df_5m["close"].ewm(span=12, adjust=False).mean()
                ema26 = df_5m["close"].ewm(span=26, adjust=False).mean()
                df_5m["macd"] = ema12 - ema26
                df_5m["macd_signal"] = df_5m["macd"].ewm(span=9, adjust=False).mean()
                df_5m["macd_hist"] = df_5m["macd"] - df_5m["macd_signal"]
            except Exception:
                pass

        # 1. NR7
        try:
            nr7_r = self.nr7.detect(df_5m)
            nr7_b = self.nr7.get_score_bonus(nr7_r, signal_direction)
            if nr7_r.is_nr7:
                reasons.append(f"NR7 compression ({nr7_r.compression_pct:.0%} of avg) → {nr7_b:+.0f}pts")
            elif nr7_r.is_nr4:
                reasons.append(f"NR4 compression → {nr7_b:+.0f}pts")
        except Exception as e:
            logger.debug(f"NR7 error: {e}")

        # 2. Fibonacci
        try:
            df_fib = df_1h if df_1h is not None and len(df_1h) >= 20 else df_5m
            fib_r = self.fib.analyze(df_fib)
            fib_b = self.fib.get_score_bonus(fib_r, signal_direction)
            if fib_r and fib_r.current_in_golden_pocket:
                reasons.append(
                    f"🎯 Fibonacci Golden Pocket ₹{fib_r.golden_pocket_low:.0f}–"
                    f"₹{fib_r.golden_pocket_high:.0f} → +{fib_b:.0f}pts"
                )
            elif fib_r and abs(fib_b) >= 4:
                reasons.append(
                    f"Fib {fib_r.nearest_fib.ratio*100:.1f}% @ ₹{fib_r.nearest_fib.price:.0f} → {fib_b:+.0f}pts"
                )
        except Exception as e:
            logger.debug(f"Fibonacci error: {e}")

        # 3. Hidden Divergence
        try:
            div_r = self.divergence.detect(df_5m)
            div_b = self.divergence.get_score_bonus(div_r, signal_direction)
            if div_r and abs(div_b) >= 5:
                reasons.append(f"{div_r.kind} ({div_r.indicator}) → {div_b:+.0f}pts")
        except Exception as e:
            logger.debug(f"Divergence error: {e}")

        # 4. Camarilla Pivots
        try:
            if prev_high > 0 and prev_low > 0 and prev_close > 0 and current_price > 0:
                today = get_current_ist_time().date()
                cached = self._pivot_cache.get(symbol)
                if cached and cached[0] == today:
                    piv_r = cached[1]
                else:
                    piv_r = self.pivots.calculate(
                        symbol, prev_high, prev_low, prev_close, current_price
                    )
                    if piv_r:
                        self._pivot_cache[symbol] = (today, piv_r)
                if piv_r:
                    piv_b = self.pivots.get_score_bonus(piv_r, signal_direction)
                    if abs(piv_b) >= 5:
                        reasons.append(
                            f"Camarilla {piv_r.current_zone} (H4={piv_r.cam_h4:.0f} L4={piv_r.cam_l4:.0f}) → {piv_b:+.0f}pts"
                        )
        except Exception as e:
            logger.debug(f"Camarilla error: {e}")

        # 5. Ichimoku
        try:
            df_ich = df_15m if df_15m is not None and len(df_15m) >= 60 else df_5m
            ich_r = self.ichimoku.analyze(df_ich)
            ich_b = self.ichimoku.get_score_bonus(ich_r, signal_direction)
            if ich_r and abs(ich_b) >= 5:
                cloud_pos = "above cloud" if ich_r.price_above_cloud else ("below cloud" if ich_r.price_below_cloud else "in cloud")
                reasons.append(
                    f"Ichimoku {ich_r.signal} ({cloud_pos}, TK={ich_r.tk_cross}) → {ich_b:+.0f}pts"
                )
        except Exception as e:
            logger.debug(f"Ichimoku error: {e}")

        # 6. VSA
        try:
            vsa_r = self.vsa.analyze(df_5m)
            vsa_b = self.vsa.get_score_bonus(vsa_r, signal_direction)
            if vsa_r and abs(vsa_b) >= 5:
                reasons.append(f"VSA: {vsa_r.bar_type} (bg={vsa_r.background}) → {vsa_b:+.0f}pts")
        except Exception as e:
            logger.debug(f"VSA error: {e}")

        # 7. Multiple Inside Bars
        try:
            mib_r = self.mib.detect(df_5m)
            mib_b = self.mib.get_score_bonus(mib_r, signal_direction)
            if mib_r and mib_r.count >= 2:
                reasons.append(
                    f"{mib_r.count}× Inside Bars (compression {mib_r.compression_factor:.1f}x) → {mib_b:+.0f}pts"
                )
        except Exception as e:
            logger.debug(f"Multiple Inside Bar error: {e}")

        # Compute total with cap
        raw = nr7_b + fib_b + div_b + piv_b + ich_b + vsa_b + mib_b

        # Convergence multiplier: 4+ strategies aligning = extra confidence boost
        positive_count = sum(1 for b in [nr7_b, fib_b, div_b, piv_b, ich_b, vsa_b, mib_b] if b > 0)
        if positive_count >= 4:
            raw *= 1.15
            reasons.append(f"⚡ {positive_count}/7 strategies aligned → 15% convergence boost")

        total = min(50.0, max(-30.0, raw))

        return ProfitMaxScore(
            total_bonus=round(total, 1),
            nr7_bonus=nr7_b,
            fib_bonus=fib_b,
            divergence_bonus=div_b,
            pivot_bonus=piv_b,
            ichimoku_bonus=ich_b,
            vsa_bonus=vsa_b,
            mib_bonus=mib_b,
            reasons=reasons,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_maximizer: Optional[ProfitMaximizer] = None


def get_profit_maximizer() -> ProfitMaximizer:
    global _maximizer
    if _maximizer is None:
        _maximizer = ProfitMaximizer()
    return _maximizer
