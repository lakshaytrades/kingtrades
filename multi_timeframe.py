"""
multi_timeframe.py — NSE Momentum Groww AI Bot
5-min / 15-min / 1-hour Alignment Engine + Swing Structure + Fibonacci

18yr Pro Rule: "Never trade against the higher timeframe. 
The 1h sets the map. The 15m sets the direction. The 5m pulls the trigger."

Advanced features:
- Swing high/low detection (pivot-based)
- Break of Structure (BoS) — confirms trend change
- Supply/Demand zones from institutional order blocks
- Fibonacci retracement levels (38.2%, 50%, 61.8%, 78.6%)
- Fair Value Gaps (FVG) — imbalance zones that price revisits
- Dynamic Support/Resistance clustering
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

TREND_BULLISH = "BULLISH"
TREND_BEARISH = "BEARISH"
TREND_NEUTRAL  = "NEUTRAL"


@dataclass
class SwingPoint:
    price: float
    index: int
    kind: str        # "HIGH" or "LOW"
    strength: int    # number of candles it spans


@dataclass
class Zone:
    top: float
    bottom: float
    kind: str        # "SUPPLY" or "DEMAND"
    strength: int    # touches / confirmations
    origin_index: int


@dataclass
class FibLevel:
    ratio: float
    price: float
    label: str


@dataclass
class FairValueGap:
    top: float
    bottom: float
    direction: str   # "BULLISH" or "BEARISH"
    index: int
    filled: bool = False


@dataclass
class MTFAnalysis:
    # Trend per timeframe
    trend_5m:  str = TREND_NEUTRAL
    trend_15m: str = TREND_NEUTRAL
    trend_1h:  str = TREND_NEUTRAL

    # Scores
    score_5m:  int = 0
    score_15m: int = 0
    score_1h:  int = 0

    # Alignment
    aligned:           bool = False
    alignment_score:   int  = 0
    entry_direction:   str  = "SKIP"
    htf_bias:          str  = TREND_NEUTRAL

    # Structure
    swing_highs: List[SwingPoint] = field(default_factory=list)
    swing_lows:  List[SwingPoint] = field(default_factory=list)
    bos_detected:  bool  = False
    bos_direction: str   = ""
    bos_level:     float = 0.0

    # Zones & levels
    supply_zones:  List[Zone]      = field(default_factory=list)
    demand_zones:  List[Zone]      = field(default_factory=list)
    fib_levels:    List[FibLevel]  = field(default_factory=list)
    fvg_list:      List[FairValueGap] = field(default_factory=list)
    key_resistance: float = 0.0
    key_support:    float = 0.0
    pivot:          float = 0.0
    r1: float = 0.0
    r2: float = 0.0
    s1: float = 0.0
    s2: float = 0.0

    description: str = ""

    @property
    def is_long_setup(self) -> bool:
        return self.entry_direction == "LONG" and self.aligned

    @property
    def is_short_setup(self) -> bool:
        return self.entry_direction == "SHORT" and self.aligned


class MultiTimeframeAnalyzer:
    """
    Full multi-timeframe analysis engine.
    Combines trend, structure, zones, and Fibonacci for high-probability setups.
    """

    # -------------------------------------------------------
    # MAIN ANALYSIS ENTRY POINT
    # -------------------------------------------------------

    def analyze(
        self,
        df_5m:  Optional[pd.DataFrame],
        df_15m: Optional[pd.DataFrame],
        df_1h:  Optional[pd.DataFrame],
    ) -> MTFAnalysis:
        result = MTFAnalysis()

        # Compute trend on each timeframe
        t5  = self._get_trend(df_5m,  "5m")
        t15 = self._get_trend(df_15m, "15m")
        t1h = self._get_trend(df_1h,  "1h")

        result.trend_5m,  result.score_5m  = t5["trend"],  t5["score"]
        result.trend_15m, result.score_15m = t15["trend"], t15["score"]
        result.trend_1h,  result.score_1h  = t1h["trend"], t1h["score"]

        # Higher timeframe bias (1h + 15m) — most important
        result.htf_bias = self._htf_bias(t15, t1h)

        # Alignment check
        align = self._check_alignment(t5, t15, t1h)
        result.aligned         = align["aligned"]
        result.alignment_score = align["score"]
        result.entry_direction = align["direction"]
        result.description     = align["reason"]

        # Structure: swing points, BoS
        if df_5m is not None and len(df_5m) >= 20:
            result.swing_highs, result.swing_lows = self.find_swing_points(df_5m)
            bos = self.check_break_of_structure(df_5m, df_15m)
            result.bos_detected  = bos["detected"]
            result.bos_direction = bos.get("direction", "")
            result.bos_level     = bos.get("level", 0.0)

        # Supply/Demand zones from 15m
        if df_15m is not None and len(df_15m) >= 20:
            result.supply_zones, result.demand_zones = self.find_supply_demand_zones(df_15m)
            result.fvg_list = self.find_fair_value_gaps(df_15m)

        # Fibonacci levels
        if result.swing_highs and result.swing_lows:
            result.fib_levels = self.calculate_fibonacci(result.swing_highs, result.swing_lows)

        # Classic pivot / S&R
        if df_5m is not None and not df_5m.empty:
            sr = self.get_support_resistance(df_5m)
            result.key_resistance = sr.get("resistance", 0.0)
            result.key_support    = sr.get("support", 0.0)
            result.pivot = sr.get("pivot", 0.0)
            result.r1, result.r2 = sr.get("r1", 0.0), sr.get("r2", 0.0)
            result.s1, result.s2 = sr.get("s1", 0.0), sr.get("s2", 0.0)

        logger.debug(
            f"[{format_ist_timestamp()}] MTF: 5m={result.trend_5m} "
            f"15m={result.trend_15m} 1h={result.trend_1h} "
            f"aligned={result.aligned} dir={result.entry_direction}"
        )
        return result

    # -------------------------------------------------------
    # TREND DETECTION PER TIMEFRAME
    # -------------------------------------------------------

    def _get_trend(self, df: Optional[pd.DataFrame], label: str) -> Dict:
        if df is None or len(df) < 15:
            return {"trend": TREND_NEUTRAL, "score": 0}

        row = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 2 else row
        score = 0

        # EMA hierarchy — compute on-the-fly if columns absent
        close = float(row.get("close", 0))
        if "ema9" in row.index and row["ema9"] > 0:
            ema9  = float(row["ema9"])
            ema21 = float(row.get("ema21", close))
            ema50 = float(row.get("ema50", close))
        else:
            ema9  = float(df["close"].ewm(span=9,  adjust=False).mean().iloc[-1])
            ema21 = float(df["close"].ewm(span=21, adjust=False).mean().iloc[-1])
            ema50 = float(df["close"].ewm(span=50, adjust=False).mean().iloc[-1])

        if ema9 > ema21 > ema50 and close > ema9:
            score += 30
        elif ema9 < ema21 < ema50 and close < ema9:
            score -= 30
        elif ema9 > ema21:
            score += 15
        elif ema9 < ema21:
            score -= 15

        # MACD histogram momentum
        hist      = float(row.get("macd_hist", row.get("MACDh_12_26_9", 0)) or 0)
        prev_hist = float(prev.get("macd_hist", prev.get("MACDh_12_26_9", 0)) or 0)
        if hist > 0 and hist > prev_hist:
            score += 20   # Growing bullish momentum
        elif hist > 0:
            score += 10
        elif hist < 0 and hist < prev_hist:
            score -= 20
        elif hist < 0:
            score -= 10

        # RSI
        rsi = float(row.get("rsi", row.get("RSI_14", 50)) or 50)
        if rsi > 55:
            score += 10
        elif rsi < 45:
            score -= 10

        # ADX + DI
        adx      = float(row.get("adx", row.get("ADX_14", 20)) or 20)
        plus_di  = float(row.get("plus_di", row.get("DMP_14", 25)) or 25)
        minus_di = float(row.get("minus_di", row.get("DMN_14", 25)) or 25)
        if adx > 25:
            if plus_di > minus_di:
                score += 15
            else:
                score -= 15

        # Price relative to VWAP
        vwap = float(row.get("vwap", 0) or 0)
        if vwap > 0:
            if close > vwap * 1.002:
                score += 10
            elif close < vwap * 0.998:
                score -= 10

        # Supertrend
        st_dir = int(row.get("supertrend_dir", row.get("SUPERTd_7_3.0", 1)) or 1)
        if st_dir == 1:
            score += 15
        elif st_dir == -1:
            score -= 15

        # Volume confirmation
        vol_ratio = float(row.get("volume_ratio", 1.0) or 1.0)
        if vol_ratio > 1.5 and score > 0:
            score += 10
        elif vol_ratio > 1.5 and score < 0:
            score -= 10

        if score >= 35:
            trend = TREND_BULLISH
        elif score <= -35:
            trend = TREND_BEARISH
        else:
            trend = TREND_NEUTRAL

        return {
            "trend": trend, "score": score,
            "ema9": ema9, "ema21": ema21, "ema50": ema50,
            "rsi": rsi, "adx": adx, "macd_hist": hist,
        }

    def _htf_bias(self, t15: Dict, t1h: Dict) -> str:
        """1h is the MAP. 15m is the DIRECTION. Never fight both."""
        if t1h["trend"] == TREND_BULLISH and t15["trend"] == TREND_BULLISH:
            return TREND_BULLISH
        if t1h["trend"] == TREND_BEARISH and t15["trend"] == TREND_BEARISH:
            return TREND_BEARISH
        return TREND_NEUTRAL

    def _check_alignment(self, t5: Dict, t15: Dict, t1h: Dict) -> Dict:
        """
        Full 3-timeframe alignment check.
        18yr rule: "The more timeframes agree, the higher the win rate."
        """
        trends = [t5["trend"], t15["trend"], t1h["trend"]]
        bulls = trends.count(TREND_BULLISH)
        bears = trends.count(TREND_BEARISH)

        # All 3 aligned — max confidence
        if bulls == 3:
            return {"aligned": True, "score": 100, "direction": "LONG",
                    "reason": "All 3 TF bullish (5m+15m+1h) — HIGHEST confidence LONG"}
        if bears == 3:
            return {"aligned": True, "score": 100, "direction": "SHORT",
                    "reason": "All 3 TF bearish (5m+15m+1h) — HIGHEST confidence SHORT"}

        # 1h + 15m agree (most important pair)
        if t1h["trend"] == TREND_BULLISH and t15["trend"] == TREND_BULLISH:
            return {"aligned": True, "score": 80, "direction": "LONG",
                    "reason": "1h+15m bullish (HTF aligned). 5m lagging — still LONG"}
        if t1h["trend"] == TREND_BEARISH and t15["trend"] == TREND_BEARISH:
            return {"aligned": True, "score": 80, "direction": "SHORT",
                    "reason": "1h+15m bearish (HTF aligned). 5m lagging — still SHORT"}

        # 5m + 15m agree, 1h neutral/opposite — medium confidence
        if t5["trend"] == TREND_BULLISH and t15["trend"] == TREND_BULLISH:
            score = 60 if t1h["trend"] != TREND_BEARISH else 40
            return {"aligned": score >= 60, "score": score, "direction": "LONG",
                    "reason": f"5m+15m bullish. 1h={t1h['trend']}. Moderate confidence."}
        if t5["trend"] == TREND_BEARISH and t15["trend"] == TREND_BEARISH:
            score = 60 if t1h["trend"] != TREND_BULLISH else 40
            return {"aligned": score >= 60, "score": score, "direction": "SHORT",
                    "reason": f"5m+15m bearish. 1h={t1h['trend']}. Moderate confidence."}

        # Conflict — skip trade
        return {
            "aligned": False, "score": 20, "direction": "SKIP",
            "reason": f"MTF conflict: 5m={t5['trend']} 15m={t15['trend']} 1h={t1h['trend']}. Skip."
        }

    # -------------------------------------------------------
    # SWING POINT DETECTION
    # -------------------------------------------------------

    def find_swing_points(
        self, df: pd.DataFrame, strength: int = 5
    ) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Detect pivot highs and lows.
        strength = number of candles on each side that must be lower/higher.
        18yr rule: "Swing points are the skeleton of price action."
        """
        highs: List[SwingPoint] = []
        lows:  List[SwingPoint] = []
        n = len(df)

        for i in range(strength, n - strength):
            window_h = df["high"].iloc[i - strength: i + strength + 1]
            window_l = df["low"].iloc[i - strength: i + strength + 1]

            # Pivot high: highest in the window
            if df["high"].iloc[i] == window_h.max():
                highs.append(SwingPoint(
                    price=float(df["high"].iloc[i]),
                    index=i,
                    kind="HIGH",
                    strength=strength
                ))

            # Pivot low: lowest in the window
            if df["low"].iloc[i] == window_l.min():
                lows.append(SwingPoint(
                    price=float(df["low"].iloc[i]),
                    index=i,
                    kind="LOW",
                    strength=strength
                ))

        # Return only last 5 significant swings each
        return highs[-5:], lows[-5:]

    # -------------------------------------------------------
    # BREAK OF STRUCTURE
    # -------------------------------------------------------

    def check_break_of_structure(
        self,
        df_5m: pd.DataFrame,
        df_15m: Optional[pd.DataFrame]
    ) -> Dict:
        """
        Break of Structure (BoS) — price breaks above last swing high (bullish BoS)
        or below last swing low (bearish BoS), confirmed on close.

        18yr rule: "BoS is the signal that the trend has changed.
        Don't fade it — join it immediately on the first pullback."
        """
        result = {"detected": False, "direction": "", "level": 0.0, "strength": 0.0}
        if df_5m is None or len(df_5m) < 20:
            return result

        highs, lows = self.find_swing_points(df_5m, strength=4)
        if not highs or not lows:
            return result

        last_close = float(df_5m["close"].iloc[-1])
        last_swing_high = max(highs, key=lambda x: x.index).price
        last_swing_low  = max(lows,  key=lambda x: x.index).price  # most recent low, not oldest

        # Bullish BoS: close above last significant swing high
        if last_close > last_swing_high * 1.001:
            # Confirm on 15m if available
            confirmed = True
            if df_15m is not None and len(df_15m) >= 3:
                close_15m = float(df_15m["close"].iloc[-1])
                confirmed = close_15m > last_swing_high
            result = {
                "detected": confirmed,
                "direction": "BULLISH",
                "level": last_swing_high,
                "strength": (last_close - last_swing_high) / last_swing_high * 100,
            }

        # Bearish BoS: close below last significant swing low
        elif last_close < last_swing_low * 0.999:
            confirmed = True
            if df_15m is not None and len(df_15m) >= 3:
                close_15m = float(df_15m["close"].iloc[-1])
                confirmed = close_15m < last_swing_low
            result = {
                "detected": confirmed,
                "direction": "BEARISH",
                "level": last_swing_low,
                "strength": (last_swing_low - last_close) / last_swing_low * 100,
            }

        return result

    # -------------------------------------------------------
    # SUPPLY / DEMAND ZONES
    # -------------------------------------------------------

    def find_supply_demand_zones(
        self, df: pd.DataFrame, lookback: int = 50
    ) -> Tuple[List[Zone], List[Zone]]:
        """
        Identify institutional supply (resistance) and demand (support) zones.
        A zone = area where price reversed sharply with big volume.

        18yr rule: "Supply/Demand zones are where institutions placed orders.
        Price ALWAYS revisits these — trade the retest, not the initial move."
        """
        supply: List[Zone] = []
        demand: List[Zone] = []
        df_r = df.tail(lookback).copy()
        n = len(df_r)

        avg_vol = df_r["volume"].mean()

        for i in range(2, n - 1):
            candle = df_r.iloc[i]
            prev   = df_r.iloc[i - 1]
            body   = abs(float(candle["close"]) - float(candle["open"]))
            prev_body = abs(float(prev["close"]) - float(prev["open"]))
            vol    = float(candle.get("volume", avg_vol))

            # Supply zone: strong bearish candle after rally, high volume
            if (float(candle["close"]) < float(candle["open"]) and   # bearish
                body > prev_body * 1.5 and                            # bigger than previous
                vol > avg_vol * 1.3):                                 # above avg volume
                supply.append(Zone(
                    top=float(candle["open"]),
                    bottom=float(candle["close"]),
                    kind="SUPPLY",
                    strength=int(vol / avg_vol * 10),
                    origin_index=i,
                ))

            # Demand zone: strong bullish candle after decline, high volume
            if (float(candle["close"]) > float(candle["open"]) and   # bullish
                body > prev_body * 1.5 and
                vol > avg_vol * 1.3):
                demand.append(Zone(
                    top=float(candle["close"]),
                    bottom=float(candle["open"]),
                    kind="DEMAND",
                    strength=int(vol / avg_vol * 10),
                    origin_index=i,
                ))

        # Return most recent + strongest zones
        supply.sort(key=lambda z: z.strength, reverse=True)
        demand.sort(key=lambda z: z.strength, reverse=True)
        return supply[:5], demand[:5]

    # -------------------------------------------------------
    # FAIR VALUE GAPS (FVG)
    # -------------------------------------------------------

    def find_fair_value_gaps(self, df: pd.DataFrame) -> List[FairValueGap]:
        """
        Fair Value Gaps (FVG) / Imbalances — 3-candle pattern.
        Candle[i-2] high < Candle[i] low → bullish FVG (gap up, price will return)
        Candle[i-2] low  > Candle[i] high → bearish FVG (gap down, price will return)

        18yr rule: "Price is a magnet for inefficiency.
        FVGs get filled 80%+ of the time in intraday — trade the fill."
        """
        fvgs: List[FairValueGap] = []
        n = len(df)
        if n < 3:
            return fvgs

        current_price = float(df["close"].iloc[-1])

        for i in range(2, n):
            c0 = df.iloc[i - 2]
            c2 = df.iloc[i]

            # Bullish FVG
            if float(c0["high"]) < float(c2["low"]):
                fvg = FairValueGap(
                    top=float(c2["low"]),
                    bottom=float(c0["high"]),
                    direction="BULLISH",
                    index=i,
                    filled=(current_price <= float(c0["high"])),
                )
                fvgs.append(fvg)

            # Bearish FVG
            elif float(c0["low"]) > float(c2["high"]):
                fvg = FairValueGap(
                    top=float(c0["low"]),
                    bottom=float(c2["high"]),
                    direction="BEARISH",
                    index=i,
                    filled=(current_price >= float(c0["low"])),
                )
                fvgs.append(fvg)

        # Last 10 unfilled FVGs only
        unfilled = [f for f in fvgs if not f.filled]
        return unfilled[-10:]

    # -------------------------------------------------------
    # FIBONACCI RETRACEMENTS
    # -------------------------------------------------------

    def calculate_fibonacci(
        self,
        swing_highs: List[SwingPoint],
        swing_lows:  List[SwingPoint],
    ) -> List[FibLevel]:
        """
        Calculate Fibonacci retracement levels from last major swing.
        Key levels: 23.6%, 38.2%, 50%, 61.8%, 78.6%

        18yr rule: "61.8% and 50% are the golden entry zones.
        Price bounces from these levels with uncanny precision."
        """
        if not swing_highs or not swing_lows:
            return []

        last_high = max(swing_highs, key=lambda x: x.index)
        last_low  = max(swing_lows,  key=lambda x: x.index)
        levels: List[FibLevel] = []

        if last_high.index > last_low.index:
            # Downswing: high → low, fib from bottom up
            swing_range = last_high.price - last_low.price
            for ratio, label in [
                (0.236, "23.6%"), (0.382, "38.2%"),
                (0.500, "50.0%"), (0.618, "61.8%"), (0.786, "78.6%")
            ]:
                levels.append(FibLevel(
                    ratio=ratio,
                    price=round(last_low.price + swing_range * ratio, 2),
                    label=label,
                ))
        else:
            # Upswing: low → high, fib from top down
            swing_range = last_high.price - last_low.price
            for ratio, label in [
                (0.236, "23.6%"), (0.382, "38.2%"),
                (0.500, "50.0%"), (0.618, "61.8%"), (0.786, "78.6%")
            ]:
                levels.append(FibLevel(
                    ratio=ratio,
                    price=round(last_high.price - swing_range * ratio, 2),
                    label=label,
                ))
        return levels

    def nearest_fib_level(self, price: float, fibs: List[FibLevel], tolerance_pct: float = 0.3) -> Optional[FibLevel]:
        """Return closest Fibonacci level if price is within tolerance."""
        for fib in fibs:
            dist = abs(price - fib.price) / fib.price * 100
            if dist <= tolerance_pct:
                return fib
        return None

    # -------------------------------------------------------
    # SUPPORT / RESISTANCE
    # -------------------------------------------------------

    def get_support_resistance(self, df: pd.DataFrame, lookback: int = 30) -> Dict:
        """Classic pivot-based S/R levels."""
        if df is None or len(df) < 3:
            return {}
        recent = df.tail(lookback)
        h = recent["high"].max()
        l = recent["low"].min()
        c = float(df["close"].iloc[-1])
        pivot = (h + l + c) / 3
        r1 = 2 * pivot - l
        r2 = pivot + (h - l)
        s1 = 2 * pivot - h
        s2 = pivot - (h - l)
        return {
            "resistance": round(h, 2), "support": round(l, 2),
            "pivot": round(pivot, 2),
            "r1": round(r1, 2), "r2": round(r2, 2),
            "s1": round(s1, 2), "s2": round(s2, 2),
        }

    # -------------------------------------------------------
    # ENTRY QUALITY NEAR ZONES
    # -------------------------------------------------------

    def is_near_demand_zone(self, price: float, zones: List[Zone], tolerance_pct: float = 0.5) -> Tuple[bool, Optional[Zone]]:
        """Check if price is near a demand zone (ideal BUY area)."""
        for zone in zones:
            mid = (zone.top + zone.bottom) / 2
            if abs(price - mid) / mid * 100 <= tolerance_pct:
                return True, zone
        return False, None

    def is_near_supply_zone(self, price: float, zones: List[Zone], tolerance_pct: float = 0.5) -> Tuple[bool, Optional[Zone]]:
        """Check if price is near a supply zone (ideal SELL area)."""
        for zone in zones:
            mid = (zone.top + zone.bottom) / 2
            if abs(price - mid) / mid * 100 <= tolerance_pct:
                return True, zone
        return False, None

    def get_nearest_fvg(self, price: float, fvgs: List[FairValueGap]) -> Optional[FairValueGap]:
        """Find nearest unfilled FVG to current price."""
        if not fvgs:
            return None
        return min(fvgs, key=lambda f: abs(((f.top + f.bottom) / 2) - price))
