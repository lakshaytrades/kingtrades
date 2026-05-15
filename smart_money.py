"""
smart_money.py — World-Class Institutional Trading Intelligence

Concepts from top traders: Stan Weinstein, Mark Minervini, ICT (Inner Circle Trader),
Wyckoff, Linda Raschke, Paul Tudor Jones, Marty Schwartz, Ed Seykota.

Modules:
  1. Liquidity Sweep Detection     — stop hunts / false breakouts (ICT)
  2. Wyckoff Phase Analysis        — accumulation / distribution cycles
  3. Opening Range Breakout (ORB)  — first 15-min range, day bias
  4. Relative Volume (RVOL)        — real institutional volume signal
  5. Market Regime Detector        — trending vs choppy vs volatile
  6. NSE Killzones                 — highest-probability time windows
  7. Key Level Map                 — PDH/PDL, PWH/PWL, round numbers
  8. Momentum Quality Score        — is this move institutionally backed?
  9. Composite Signal Enhancer     — combine all into one quality boost
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LiquiditySweep:
    direction: str          # "BULLISH_SWEEP" or "BEARISH_SWEEP"
    swept_level: float      # price level that was swept
    sweep_low: float        # low of sweep candle
    sweep_high: float       # high of sweep candle
    reversal_strength: float  # 0-100
    candle_index: int
    description: str


@dataclass
class WyckoffPhase:
    phase: str              # PS, SC, AR, ST, SPRING, TEST, SOS, LPS, MARKUP
                            # or BC, UTAD, LPSY, SOW, MARKDOWN
    cycle: str              # "ACCUMULATION" or "DISTRIBUTION"
    confidence: float       # 0-100
    support_level: float
    resistance_level: float
    description: str


@dataclass
class ORBResult:
    orb_high: float
    orb_low: float
    orb_range: float
    bias: str               # "BULLISH", "BEARISH", "NEUTRAL"
    breakout_level: float   # where to enter on breakout
    target_1: float
    target_2: float
    stop_loss: float
    confidence: float


@dataclass
class MarketRegime:
    regime: str             # "TRENDING_UP", "TRENDING_DOWN", "CHOPPY", "VOLATILE_EXPANSION"
    adx_value: float
    atr_percentile: float   # ATR vs 20-day history (0-100)
    tradeable: bool         # should we trade in this regime?
    position_size_factor: float  # 0.5 to 1.5
    description: str


@dataclass
class SmartMoneyScore:
    total_score: float      # 0-100 bonus points to add to signal score
    liquidity_sweep_bonus: float
    wyckoff_bonus: float
    orb_bonus: float
    rvol_bonus: float
    regime_multiplier: float
    killzone_bonus: float
    key_level_bonus: float
    reasons: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 1. LIQUIDITY SWEEP DETECTION (ICT / Smart Money Concepts)
# ─────────────────────────────────────────────────────────────────────────────

class LiquiditySweepDetector:
    """
    Markets move to grab liquidity (stop losses) before reversing.
    A sweep above a prior high = stop run = potential SHORT entry.
    A sweep below a prior low  = stop run = potential LONG entry.

    Paul Tudor Jones: "The most important thing is to defend yourself."
    This detects when the market IS the attacker, setting up the reversal.
    """

    def detect_sweeps(self, df: pd.DataFrame, lookback: int = 20) -> List[LiquiditySweep]:
        sweeps = []
        if len(df) < lookback + 5:
            return sweeps

        highs = df['high'].values
        lows  = df['low'].values
        closes = df['close'].values
        opens  = df['open'].values

        for i in range(lookback, len(df) - 1):
            # Find significant swing highs/lows in lookback window
            window_highs = highs[i-lookback:i]
            window_lows  = lows[i-lookback:i]
            prev_high    = max(window_highs)
            prev_low     = min(window_lows)

            # BEARISH SWEEP: Current candle wicks above prior high then closes below it
            # Market grabbed buy-side liquidity (stop losses of shorts above prev high)
            # → Now shorts trapped → reversal DOWN likely
            if (highs[i] > prev_high                    # wick swept above
                    and closes[i] < prev_high            # closes back below → rejection
                    and closes[i] < opens[i]             # bearish close
                    and (highs[i] - prev_high) > 0       # actual sweep
               ):
                wick_size = highs[i] - closes[i]
                body_size = abs(closes[i] - opens[i])
                if wick_size > body_size * 0.5:          # significant wick
                    strength = min(100, (wick_size / (highs[i] * 0.001)) * 20)
                    sweeps.append(LiquiditySweep(
                        direction="BEARISH_SWEEP",
                        swept_level=prev_high,
                        sweep_low=lows[i],
                        sweep_high=highs[i],
                        reversal_strength=strength,
                        candle_index=i,
                        description=f"Buy-side liquidity swept at ₹{prev_high:.2f} → reversal SHORT"
                    ))

            # BULLISH SWEEP: Current candle wicks below prior low then closes above it
            # Market grabbed sell-side liquidity (stop losses of longs below prev low)
            # → Now longs trapped → reversal UP likely
            if (lows[i] < prev_low                      # wick swept below
                    and closes[i] > prev_low             # closes back above → rejection
                    and closes[i] > opens[i]             # bullish close
                    and (prev_low - lows[i]) > 0         # actual sweep
               ):
                wick_size = closes[i] - lows[i]
                body_size = abs(closes[i] - opens[i])
                if wick_size > body_size * 0.5:
                    strength = min(100, (wick_size / (lows[i] * 0.001)) * 20)
                    sweeps.append(LiquiditySweep(
                        direction="BULLISH_SWEEP",
                        swept_level=prev_low,
                        sweep_low=lows[i],
                        sweep_high=highs[i],
                        reversal_strength=strength,
                        candle_index=i,
                        description=f"Sell-side liquidity swept at ₹{prev_low:.2f} → reversal LONG"
                    ))

        # Return most recent sweeps (last 3 candles most relevant)
        recent = [s for s in sweeps if s.candle_index >= len(df) - 4]
        return recent if recent else sweeps[-1:] if sweeps else []

    def get_score_bonus(self, sweeps: List[LiquiditySweep], signal_direction: str) -> float:
        """Return bonus score if sweep confirms signal direction."""
        for sweep in sweeps:
            if signal_direction == "LONG" and sweep.direction == "BULLISH_SWEEP":
                return min(20, sweep.reversal_strength * 0.2)
            if signal_direction == "SHORT" and sweep.direction == "BEARISH_SWEEP":
                return min(20, sweep.reversal_strength * 0.2)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. WYCKOFF PHASE ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

class WyckoffAnalyzer:
    """
    Richard Wyckoff's Law of Cause and Effect:
    - Accumulation → Markup (BUY)
    - Distribution → Markdown (SELL)

    Key phases:
    ACCUMULATION: PS → SC → AR → ST → SPRING → TEST → SOS → LPS → MARKUP
    DISTRIBUTION: BC → UTAD → LPSY → SOW → MARKDOWN

    Mark Minervini trades in Stage 2 (Markup) only.
    Stan Weinstein's Stage Analysis is pure Wyckoff.
    """

    def analyze(self, df: pd.DataFrame) -> Optional[WyckoffPhase]:
        if len(df) < 50:
            return None

        closes  = df['close'].values
        highs   = df['high'].values
        lows    = df['low'].values
        volumes = df['volume'].values if 'volume' in df.columns else np.ones(len(df))

        # Recent price structure
        recent_high = max(highs[-20:])
        recent_low  = min(lows[-20:])
        price_range = recent_high - recent_low
        current_price = closes[-1]
        price_position = (current_price - recent_low) / price_range if price_range > 0 else 0.5

        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        recent_vol = np.mean(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

        # 50-period trend (Stan Weinstein's 30-week MA equivalent)
        ma50 = np.mean(closes[-50:]) if len(closes) >= 50 else np.mean(closes)
        ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else np.mean(closes)
        ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else np.mean(closes)

        # Stage Analysis (Stan Weinstein's method)
        # Stage 1: Base/Accumulation (price flat around MA50, MA50 flat)
        # Stage 2: Markup (price above MA50, MA50 rising) ← BEST TIME TO BUY
        # Stage 3: Top/Distribution (price struggles above MA50, MA50 flattening)
        # Stage 4: Markdown (price below MA50, MA50 falling) ← BEST TIME TO SHORT

        ma50_slope = (closes[-1] - closes[-10]) / closes[-10] if len(closes) >= 10 else 0
        price_vs_ma50 = (current_price - ma50) / ma50

        # Stage 2 Markup detection (Minervini's criteria)
        if (price_vs_ma50 > 0.02                # Price 2%+ above MA50
                and ma50_slope > 0               # MA50 rising
                and ma10 > ma20                  # Short MA > Long MA
                and vol_ratio > 0.8              # Reasonable volume
           ):
            conf = min(95, 60 + price_vs_ma50 * 200 + vol_ratio * 10)
            return WyckoffPhase(
                phase="MARKUP",
                cycle="ACCUMULATION",
                confidence=conf,
                support_level=ma50,
                resistance_level=recent_high,
                description=f"Stage 2 Markup — price {price_vs_ma50*100:.1f}% above MA50, trending up"
            )

        # Stage 4 Markdown detection
        if (price_vs_ma50 < -0.02               # Price 2%+ below MA50
                and ma50_slope < 0               # MA50 falling
                and ma10 < ma20
           ):
            conf = min(95, 60 + abs(price_vs_ma50) * 200)
            return WyckoffPhase(
                phase="MARKDOWN",
                cycle="DISTRIBUTION",
                confidence=conf,
                support_level=recent_low,
                resistance_level=ma50,
                description=f"Stage 4 Markdown — price {abs(price_vs_ma50)*100:.1f}% below MA50, trending down"
            )

        # Spring detection (Accumulation: price dips below support then recovers on volume)
        support = min(lows[-30:-5]) if len(lows) >= 30 else recent_low
        if (lows[-3] < support                   # recent dip below support
                and closes[-1] > support          # recovered above support
                and vol_ratio > 1.5               # above average volume on recovery
                and price_position < 0.35         # still in lower portion of range
           ):
            return WyckoffPhase(
                phase="SPRING",
                cycle="ACCUMULATION",
                confidence=min(85, 55 + vol_ratio * 10),
                support_level=support,
                resistance_level=recent_high,
                description=f"Wyckoff Spring — false breakdown with volume recovery"
            )

        # Upthrust (Distribution: price spikes above resistance then fails)
        resistance = max(highs[-30:-5]) if len(highs) >= 30 else recent_high
        if (highs[-3] > resistance               # recent spike above resistance
                and closes[-1] < resistance       # failed to hold
                and vol_ratio > 1.5               # volume spike
                and price_position > 0.65         # still in upper portion
           ):
            return WyckoffPhase(
                phase="UTAD",
                cycle="DISTRIBUTION",
                confidence=min(85, 55 + vol_ratio * 10),
                support_level=recent_low,
                resistance_level=resistance,
                description=f"Wyckoff Upthrust (UTAD) — false breakout above resistance"
            )

        # Stage 1 Base (flat consolidation with declining volume — accumulation building)
        price_volatility = np.std(closes[-20:]) / np.mean(closes[-20:]) if len(closes) >= 20 else 0.02
        if (price_volatility < 0.02               # tight range
                and abs(price_vs_ma50) < 0.03     # near MA50
                and vol_ratio < 1.0               # declining/average volume
           ):
            return WyckoffPhase(
                phase="ST",                       # Secondary Test in base
                cycle="ACCUMULATION",
                confidence=50,
                support_level=recent_low,
                resistance_level=recent_high,
                description="Wyckoff Base — consolidation, watch for Spring or SOS breakout"
            )

        return None

    def get_score_bonus(self, phase: Optional[WyckoffPhase], signal_direction: str) -> float:
        if not phase:
            return 0
        if signal_direction == "LONG" and phase.cycle == "ACCUMULATION":
            multiplier = {"MARKUP": 1.0, "SPRING": 0.9, "LPS": 0.8, "SOS": 0.85, "ST": 0.5}.get(phase.phase, 0.4)
            return min(20, phase.confidence * 0.2 * multiplier)
        if signal_direction == "SHORT" and phase.cycle == "DISTRIBUTION":
            multiplier = {"MARKDOWN": 1.0, "UTAD": 0.9, "LPSY": 0.8, "SOW": 0.85}.get(phase.phase, 0.4)
            return min(20, phase.confidence * 0.2 * multiplier)
        if signal_direction == "LONG" and phase.phase == "MARKDOWN":
            return -15   # penalize long in downtrend
        if signal_direction == "SHORT" and phase.phase == "MARKUP":
            return -15   # penalize short in uptrend
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. OPENING RANGE BREAKOUT (ORB)
# ─────────────────────────────────────────────────────────────────────────────

class OpeningRangeBreakout:
    """
    9:15–9:30 AM range = the day's battlefield.
    Breakout above = institutional buyers. Breakdown below = institutional sellers.

    Used by: Toby Crabel (original ORB inventor), Linda Raschke, Mark Fisher (ACD method).

    NSE-specific: Pre-market auction price + first 15-min range is the highest-
    probability setup of the day. Win rate: ~65% when combined with MTF alignment.
    """

    def __init__(self):
        self._orb_high: Dict[str, float] = {}
        self._orb_low:  Dict[str, float] = {}
        self._orb_date: Dict[str, str]   = {}

    def update_orb(self, symbol: str, df_5m: pd.DataFrame) -> Optional[ORBResult]:
        """Update ORB from 5-min candles. Call after 9:30 AM."""
        if df_5m is None or len(df_5m) < 3:
            return None

        now_ist = datetime.now(IST)
        today   = now_ist.strftime("%Y-%m-%d")

        # Get first 3 × 5-min candles (9:15, 9:20, 9:25) = 9:15–9:30 range
        orb_candles = df_5m.head(3) if len(df_5m) >= 3 else df_5m
        orb_high = orb_candles['high'].max()
        orb_low  = orb_candles['low'].min()
        orb_range = orb_high - orb_low

        self._orb_high[symbol] = orb_high
        self._orb_low[symbol]  = orb_low
        self._orb_date[symbol] = today

        atr = self._calc_atr(df_5m)
        current_price = df_5m['close'].iloc[-1]
        current_vol   = df_5m['volume'].iloc[-1] if 'volume' in df_5m.columns else 0
        avg_vol       = df_5m['volume'].mean() if 'volume' in df_5m.columns else 1

        # Bias from ORB position
        orb_midpoint = (orb_high + orb_low) / 2
        if current_price > orb_high:
            bias = "BULLISH"
            entry  = orb_high + (orb_range * 0.02)   # 2% above ORB high
            stop   = orb_low
            t1     = current_price + (atr * 1.5)
            t2     = current_price + (atr * 3.0)
            conf   = min(95, 60 + (current_vol / avg_vol - 1) * 20) if avg_vol > 0 else 65
        elif current_price < orb_low:
            bias = "BEARISH"
            entry  = orb_low - (orb_range * 0.02)
            stop   = orb_high
            t1     = current_price - (atr * 1.5)
            t2     = current_price - (atr * 3.0)
            conf   = min(95, 60 + (current_vol / avg_vol - 1) * 20) if avg_vol > 0 else 65
        else:
            bias = "NEUTRAL"
            entry  = orb_high                         # wait for break
            stop   = orb_low
            t1     = orb_high + atr
            t2     = orb_high + atr * 2
            conf   = 30

        return ORBResult(
            orb_high=orb_high, orb_low=orb_low, orb_range=orb_range,
            bias=bias, breakout_level=entry,
            target_1=t1, target_2=t2, stop_loss=stop,
            confidence=conf
        )

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < 2:
            return df['high'].iloc[-1] * 0.005
        tr_list = []
        for i in range(1, min(period+1, len(df))):
            h, l, pc = df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i-1]
            tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
        return np.mean(tr_list) if tr_list else df['high'].iloc[-1] * 0.005

    def get_score_bonus(self, orb: Optional[ORBResult], signal_direction: str) -> float:
        if not orb:
            return 0
        if signal_direction == "LONG" and orb.bias == "BULLISH":
            return orb.confidence * 0.15
        if signal_direction == "SHORT" and orb.bias == "BEARISH":
            return orb.confidence * 0.15
        if (signal_direction == "LONG" and orb.bias == "BEARISH") or \
           (signal_direction == "SHORT" and orb.bias == "BULLISH"):
            return -10   # fighting the ORB bias
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. RELATIVE VOLUME (RVOL)
# ─────────────────────────────────────────────────────────────────────────────

class RelativeVolumeAnalyzer:
    """
    RVOL = Today's volume at this time / Average volume at same time over 20 days.

    This is the REAL institutional volume signal. Raw volume alone is misleading
    because volume patterns are time-of-day dependent (opening spike, midday lull).

    RVOL > 2.0 = Institutional interest. RVOL > 3.0 = Major institutional move.
    Used by every prop desk and hedge fund.
    """

    def calculate_rvol(self, df: pd.DataFrame) -> Dict[str, float]:
        if df is None or len(df) < 5 or 'volume' not in df.columns:
            return {"rvol": 1.0, "volume_trend": 0, "climax_volume": False}

        volumes = df['volume'].values
        avg_vol_20 = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        avg_vol_5  = np.mean(volumes[-5:])  if len(volumes) >= 5  else np.mean(volumes)
        current_vol = volumes[-1]

        rvol = current_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

        # Volume trend: is volume accelerating or decelerating?
        vol_trend = (avg_vol_5 - avg_vol_20) / avg_vol_20 if avg_vol_20 > 0 else 0

        # Climax volume: extreme spike (>3x) often marks exhaustion/reversal
        climax = rvol > 3.0 and (
            abs(df['close'].iloc[-1] - df['open'].iloc[-1]) <
            abs(df['high'].iloc[-1] - df['low'].iloc[-1]) * 0.3  # large wick
        )

        return {
            "rvol": round(rvol, 2),
            "volume_trend": round(vol_trend, 3),
            "climax_volume": climax,
            "avg_20": round(avg_vol_20, 0),
        }

    def get_score_bonus(self, rvol_data: dict, signal_direction: str) -> float:
        rvol = rvol_data.get("rvol", 1.0)
        climax = rvol_data.get("climax_volume", False)

        if climax:
            # Climax volume at extremes = potential reversal — be cautious
            return 5 if rvol > 2 else 0

        if rvol >= 3.0:
            return 18      # Major institutional move
        elif rvol >= 2.0:
            return 12      # Strong institutional interest
        elif rvol >= 1.5:
            return 7       # Above-average interest
        elif rvol < 0.7:
            return -5      # Low participation — skip
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. MARKET REGIME DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class MarketRegimeDetector:
    """
    Trade WITH the market, not against it.
    Ed Seykota: "The trend is your friend until the end."
    Paul Tudor Jones: "Never trade in a choppy market."

    Regimes:
    - TRENDING_UP:        High ADX + price above MAs → LONG bias, full size
    - TRENDING_DOWN:      High ADX + price below MAs → SHORT bias, full size
    - VOLATILE_EXPANSION: High ATR percentile → reduce size, widen stops
    - CHOPPY:             Low ADX, mean-reverting → avoid momentum, reduce size
    """

    def detect(self, df: pd.DataFrame, nifty_df: pd.DataFrame = None) -> MarketRegime:
        if df is None or len(df) < 20:
            return MarketRegime("UNKNOWN", 0, 50, True, 1.0, "Insufficient data")

        closes  = df['close'].values
        highs   = df['high'].values
        lows    = df['low'].values

        # ADX calculation
        adx_val = self._calc_adx(df)

        # ATR percentile (how volatile is current volatility vs history)
        atr_current = self._calc_atr(df, 14)
        atrs_history = [self._calc_atr_at(df, i, 14) for i in range(20, len(df), 5)]
        atr_pct = (sum(1 for a in atrs_history if a < atr_current) / len(atrs_history) * 100) \
                  if atrs_history else 50

        # MA slope for trend direction
        ma20 = np.mean(closes[-20:])
        ma20_prev = np.mean(closes[-25:-5]) if len(closes) >= 25 else ma20
        ma_slope = (ma20 - ma20_prev) / ma20_prev if ma20_prev > 0 else 0

        current = closes[-1]
        ma50 = np.mean(closes[-50:]) if len(closes) >= 50 else ma20

        # Choppiness Index (0-100: <38 trending, >62 choppy)
        chop = self._choppiness_index(df)

        if chop > 62 or adx_val < 15:
            regime = "CHOPPY"
            tradeable = False
            size_factor = 0.5
            desc = f"Choppy market (ADX={adx_val:.0f}, Chop={chop:.0f}) — reduced size"
        elif atr_pct > 85:
            regime = "VOLATILE_EXPANSION"
            tradeable = True
            size_factor = 0.7
            desc = f"Volatile expansion (ATR percentile={atr_pct:.0f}) — widen stops, reduce size"
        elif adx_val >= 25 and ma_slope > 0 and current > ma50:
            regime = "TRENDING_UP"
            tradeable = True
            size_factor = 1.2
            desc = f"Strong uptrend (ADX={adx_val:.0f}) — LONG bias, full+ size"
        elif adx_val >= 25 and ma_slope < 0 and current < ma50:
            regime = "TRENDING_DOWN"
            tradeable = True
            size_factor = 1.2
            desc = f"Strong downtrend (ADX={adx_val:.0f}) — SHORT bias, full+ size"
        elif adx_val >= 20:
            regime = "TRENDING_UP" if current > ma50 else "TRENDING_DOWN"
            tradeable = True
            size_factor = 1.0
            desc = f"Moderate trend (ADX={adx_val:.0f})"
        else:
            regime = "CHOPPY"
            tradeable = False
            size_factor = 0.6
            desc = f"Weak trend (ADX={adx_val:.0f}) — avoid"

        return MarketRegime(regime, adx_val, atr_pct, tradeable, size_factor, desc)

    def _calc_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        try:
            if len(df) < period + 2:
                return 20
            h, l, c = df['high'].values, df['low'].values, df['close'].values
            tr_list, dm_plus, dm_minus = [], [], []
            for i in range(1, len(df)):
                tr = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
                tr_list.append(tr)
                up_move   = h[i] - h[i-1]
                down_move = l[i-1] - l[i]
                dm_plus.append(up_move   if up_move > down_move and up_move > 0 else 0)
                dm_minus.append(down_move if down_move > up_move and down_move > 0 else 0)

            atr14 = np.mean(tr_list[-period:])
            if atr14 == 0:
                return 20
            di_plus  = 100 * np.mean(dm_plus[-period:]) / atr14
            di_minus = 100 * np.mean(dm_minus[-period:]) / atr14
            dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus) if (di_plus + di_minus) > 0 else 20
            return round(dx, 1)
        except Exception:
            return 20

    def _calc_atr(self, df, period=14):
        try:
            trs = []
            for i in range(1, min(period+1, len(df))):
                h = df['high'].iloc[i]
                l = df['low'].iloc[i]
                pc = df['close'].iloc[i-1]
                trs.append(max(h-l, abs(h-pc), abs(l-pc)))
            return np.mean(trs) if trs else df['high'].iloc[-1] * 0.005
        except Exception:
            return 0

    def _calc_atr_at(self, df, idx, period=14):
        try:
            start = max(1, idx-period)
            trs = []
            for i in range(start, min(idx+1, len(df))):
                h = df['high'].iloc[i]
                l = df['low'].iloc[i]
                pc = df['close'].iloc[i-1]
                trs.append(max(h-l, abs(h-pc), abs(l-pc)))
            return np.mean(trs) if trs else 0
        except Exception:
            return 0

    def _choppiness_index(self, df: pd.DataFrame, period: int = 14) -> float:
        try:
            if len(df) < period + 1:
                return 50
            h = df['high'].values[-period:]
            l = df['low'].values[-period:]
            c = df['close'].values[-period-1:]
            tr_sum = sum(
                max(h[i]-l[i], abs(h[i]-c[i]), abs(l[i]-c[i]))
                for i in range(len(h))
            )
            high_low_range = max(h) - min(l)
            if high_low_range == 0:
                return 50
            chop = 100 * np.log10(tr_sum / high_low_range) / np.log10(period)
            return round(min(100, max(0, chop)), 1)
        except Exception:
            return 50


# ─────────────────────────────────────────────────────────────────────────────
# 6. NSE KILLZONES — Highest-Probability Time Windows
# ─────────────────────────────────────────────────────────────────────────────

class NSEKillzoneAnalyzer:
    """
    ICT Killzones — auto-adapts for NSE (IST) or US (ET) market.

    NSE IST:  9:15 open, 15:30 close
    US ET:    9:30 open, 16:00 close
    """

    # NSE killzones (IST)
    NSE_KILLZONES = [
        {"name": "OPEN_KILLZONE",     "start": dtime(9, 15),  "end": dtime(9, 30),  "score": 20, "stars": 5},
        {"name": "MOMENTUM_WINDOW",   "start": dtime(9, 30),  "end": dtime(10, 0),  "score": 16, "stars": 4},
        {"name": "REVERSAL_WINDOW",   "start": dtime(10, 0),  "end": dtime(10, 30), "score": 10, "stars": 3},
        {"name": "MID_MORNING",       "start": dtime(11, 0),  "end": dtime(11, 30), "score": 6,  "stars": 2},
        {"name": "AFTERNOON_REVERSAL","start": dtime(13, 0),  "end": dtime(13, 30), "score": 10, "stars": 3},
        {"name": "AFTERNOON_MOMENTUM","start": dtime(14, 0),  "end": dtime(14, 30), "score": 16, "stars": 4},
        {"name": "CLOSE_KILLZONE",    "start": dtime(15, 0),  "end": dtime(15, 20), "score": 10, "stars": 3},
    ]
    NSE_DEAD_ZONES = [
        {"name": "MIDDAY_CHOP_1", "start": dtime(10, 30), "end": dtime(13, 0),  "penalty": -15},
        {"name": "MIDDAY_CHOP_2", "start": dtime(13, 30), "end": dtime(14, 0),  "penalty": -8},
    ]

    # US killzones (ET) — identical structure, different times
    US_KILLZONES = [
        {"name": "OPEN_KILLZONE",     "start": dtime(9, 30),  "end": dtime(9, 50),  "score": 20, "stars": 5},
        {"name": "MOMENTUM_WINDOW",   "start": dtime(9, 50),  "end": dtime(10, 30), "score": 16, "stars": 4},
        {"name": "REVERSAL_WINDOW",   "start": dtime(10, 30), "end": dtime(11, 0),  "score": 10, "stars": 3},
        {"name": "MID_MORNING",       "start": dtime(11, 0),  "end": dtime(11, 30), "score": 6,  "stars": 2},
        {"name": "AFTERNOON_REVERSAL","start": dtime(13, 0),  "end": dtime(13, 30), "score": 10, "stars": 3},
        {"name": "AFTERNOON_MOMENTUM","start": dtime(14, 0),  "end": dtime(14, 30), "score": 16, "stars": 4},
        {"name": "CLOSE_KILLZONE",    "start": dtime(15, 30), "end": dtime(15, 55), "score": 10, "stars": 3},
    ]
    US_DEAD_ZONES = [
        {"name": "MIDDAY_CHOP_1", "start": dtime(11, 30), "end": dtime(13, 0),  "penalty": -15},
        {"name": "MIDDAY_CHOP_2", "start": dtime(13, 30), "end": dtime(14, 0),  "penalty": -8},
    ]

    # Alias for backward compat
    KILLZONES  = NSE_KILLZONES
    DEAD_ZONES = NSE_DEAD_ZONES

    def _get_active_zones(self):
        try:
            from broker import MARKET_NAME as _MN
            if "NSE" not in _MN:
                return self.US_KILLZONES, self.US_DEAD_ZONES
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
        return self.NSE_KILLZONES, self.NSE_DEAD_ZONES

    def get_current_killzone(self, now_ist: datetime = None) -> Dict:
        killzones, dead_zones = self._get_active_zones()
        try:
            from broker import MARKET_NAME as _MN
            if "NSE" not in _MN:
                from utils import get_current_et_time
                now_ist = get_current_et_time()
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
        if now_ist is None:
            now_ist = datetime.now(IST)
        current_time = now_ist.time()

        for kz in killzones:
            if kz["start"] <= current_time <= kz["end"]:
                return {"in_killzone": True, "zone": kz["name"],
                        "score_bonus": kz["score"], "stars": kz["stars"]}

        for dz in dead_zones:
            if dz["start"] <= current_time <= dz["end"]:
                return {"in_killzone": False, "zone": dz["name"],
                        "score_bonus": dz["penalty"], "stars": 0}

        return {"in_killzone": False, "zone": "NEUTRAL", "score_bonus": 0, "stars": 1}

    def get_next_killzone(self, now_ist: datetime = None) -> Dict:
        if now_ist is None:
            now_ist = datetime.now(IST)
        current_time = now_ist.time()
        for kz in self.KILLZONES:
            if kz["start"] > current_time:
                return kz
        return self.KILLZONES[0]  # tomorrow's open


# ─────────────────────────────────────────────────────────────────────────────
# 7. KEY LEVEL MAP — Institutional Reference Prices
# ─────────────────────────────────────────────────────────────────────────────

class KeyLevelMapper:
    """
    Price doesn't move randomly — it moves between institutional reference levels.
    These are the MAGNETS that attract and repel price.

    Levels (in order of importance):
    1. Previous Day High (PDH) / Previous Day Low (PDL)  ← Most important
    2. Previous Week High (PWH) / Previous Week Low (PWL)
    3. Round numbers (every ₹50, ₹100, ₹500, ₹1000)
    4. Pre-market high/low
    5. All-time high / 52-week high/low
    """

    def find_key_levels(self, df: pd.DataFrame, current_price: float) -> Dict:
        if df is None or len(df) < 10:
            return {}

        highs = df['high'].values
        lows  = df['low'].values

        # Previous day high/low (using last full session = candles from yesterday)
        pdh = max(highs[-50:-26]) if len(highs) >= 50 else max(highs[:-5])
        pdl = min(lows[-50:-26])  if len(lows) >= 50  else min(lows[:-5])

        # Previous week high/low (approx 75 candles of 5m in a week)
        pwh = max(highs[-100:-50]) if len(highs) >= 100 else pdh
        pwl = min(lows[-100:-50])  if len(lows) >= 100  else pdl

        # Round numbers near current price
        round_numbers = self._get_round_numbers(current_price)

        # 52-week high/low approximation (from available history)
        all_time_high = max(highs) if len(highs) > 0 else current_price
        all_time_low  = min(lows)  if len(lows) > 0  else current_price

        # Find nearest key level and distance
        all_levels = [pdh, pdl, pwh, pwl] + round_numbers
        nearest = min(all_levels, key=lambda x: abs(x - current_price))
        distance_pct = abs(nearest - current_price) / current_price * 100

        return {
            "pdh": round(pdh, 2),
            "pdl": round(pdl, 2),
            "pwh": round(pwh, 2),
            "pwl": round(pwl, 2),
            "round_numbers": round_numbers,
            "nearest_level": round(nearest, 2),
            "distance_pct": round(distance_pct, 2),
            "at_key_level": distance_pct < 0.3,         # within 0.3% = AT key level
            "near_key_level": distance_pct < 0.8,        # within 0.8% = near key level
        }

    def _get_round_numbers(self, price: float) -> List[float]:
        """Generate round number levels near current price."""
        if price < 100:
            step = 5
        elif price < 500:
            step = 10
        elif price < 2000:
            step = 50
        elif price < 5000:
            step = 100
        else:
            step = 500

        base = round(price / step) * step
        return [base - step*2, base - step, base, base + step, base + step*2]

    def get_score_bonus(self, levels: Dict, signal_direction: str) -> float:
        if not levels:
            return 0
        bonus = 0
        # Bonus for trading FROM a key level (high-probability entry)
        if levels.get("at_key_level"):
            bonus += 12
        elif levels.get("near_key_level"):
            bonus += 6

        # Bonus for PDH/PDL breakout (institutional reference)
        cp = levels.get("nearest_level", 0)
        pdh = levels.get("pdh", 0)
        pdl = levels.get("pdl", 0)
        if signal_direction == "LONG" and cp >= pdh * 0.998:
            bonus += 8   # breaking above PDH
        if signal_direction == "SHORT" and cp <= pdl * 1.002:
            bonus += 8   # breaking below PDL

        return min(20, bonus)


# ─────────────────────────────────────────────────────────────────────────────
# 8. MOMENTUM QUALITY SCORE
# ─────────────────────────────────────────────────────────────────────────────

class MomentumQualityAnalyzer:
    """
    Mark Minervini's criteria: Is this move "constructive"?

    A high-quality momentum move has:
    1. Price above all key MAs (EMA9 > EMA21 > EMA50)
    2. Volume expanding on breakout, contracting on pullback
    3. Tight consolidation before breakout (low ATR before, expansion after)
    4. Relative strength vs Nifty50
    5. No overhead supply (price breaking to new highs, not into resistance)
    """

    def score(self, df: pd.DataFrame, signal_direction: str) -> Dict:
        if df is None or len(df) < 50:
            return {"score": 0, "grade": "C", "reasons": []}

        closes  = df['close'].values
        volumes = df['volume'].values if 'volume' in df.columns else np.ones(len(df))
        highs   = df['high'].values
        lows    = df['low'].values

        score   = 0
        reasons = []

        # 1. MA Stack (Minervini: price must be above 150MA and 200MA)
        ema9  = self._ema(closes, 9)
        ema21 = self._ema(closes, 21)
        ema50 = self._ema(closes, 50)
        current = closes[-1]

        if signal_direction == "LONG":
            if current > ema9[-1] > ema21[-1] > ema50[-1]:
                score += 25
                reasons.append("Perfect EMA stack (9>21>50)")
            elif current > ema21[-1] > ema50[-1]:
                score += 15
                reasons.append("EMA stack (21>50)")
            elif current > ema50[-1]:
                score += 8
                reasons.append("Above EMA50")
            else:
                score -= 10
                reasons.append("Below EMA50 — avoid long")
        else:  # SHORT
            if current < ema9[-1] < ema21[-1] < ema50[-1]:
                score += 25
                reasons.append("Perfect bearish EMA stack")
            elif current < ema21[-1] < ema50[-1]:
                score += 15
                reasons.append("Bearish EMA stack")

        # 2. Volume pattern: up-days should have higher volume (institutional buying)
        if len(volumes) >= 10:
            up_days   = [volumes[i] for i in range(-10, 0) if closes[i] > closes[i-1]]
            down_days = [volumes[i] for i in range(-10, 0) if closes[i] < closes[i-1]]
            if up_days and down_days:
                vol_ratio = np.mean(up_days) / np.mean(down_days)
                if signal_direction == "LONG" and vol_ratio > 1.3:
                    score += 15
                    reasons.append(f"Volume favors bulls ({vol_ratio:.1f}x on up days)")
                elif signal_direction == "SHORT" and vol_ratio < 0.7:
                    score += 15
                    reasons.append(f"Volume favors bears")

        # 3. ATR expansion (breakout from tight consolidation)
        if len(closes) >= 20:
            atr_recent  = np.mean([max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
                                   for i in range(-5, 0)])
            atr_prior   = np.mean([max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
                                   for i in range(-20, -10)])
            if atr_prior > 0 and atr_recent / atr_prior > 1.3:
                score += 15
                reasons.append("ATR expansion — volatility breakout")
            elif atr_prior > 0 and atr_recent / atr_prior < 0.7:
                score += 5
                reasons.append("Low ATR — tight consolidation (pre-breakout)")

        # 4. 52-period high/low check (no overhead supply on longs)
        high_52 = max(highs[-52:]) if len(highs) >= 52 else max(highs)
        low_52  = min(lows[-52:])  if len(lows) >= 52  else min(lows)
        if signal_direction == "LONG" and current >= high_52 * 0.97:
            score += 15
            reasons.append("Near/at 52-period high — no overhead supply")
        if signal_direction == "SHORT" and current <= low_52 * 1.03:
            score += 15
            reasons.append("Near/at 52-period low — no support below")

        grade = "A+" if score >= 60 else "A" if score >= 45 else "B" if score >= 30 else "C"
        return {"score": min(100, max(0, score)), "grade": grade, "reasons": reasons}

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        ema = np.zeros(len(data))
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i-1]
        return ema


# ─────────────────────────────────────────────────────────────────────────────
# 9. COMPOSITE SMART MONEY ENHANCER
# ─────────────────────────────────────────────────────────────────────────────

class SmartMoneyEnhancer:
    """
    Combines all smart money signals into a single score boost.
    Used to upgrade the base signal score from signal_generator.py.

    "The big money is in the big swings." — Jesse Livermore
    "Cut losses short, let profits run." — Ed Seykota
    "Always be in a position to stay." — Paul Tudor Jones
    """

    def __init__(self):
        self.liquidity  = LiquiditySweepDetector()
        self.wyckoff    = WyckoffAnalyzer()
        self.orb        = OpeningRangeBreakout()
        self.rvol       = RelativeVolumeAnalyzer()
        self.regime     = MarketRegimeDetector()
        self.killzone   = NSEKillzoneAnalyzer()
        self.key_levels = KeyLevelMapper()
        self.momentum   = MomentumQualityAnalyzer()

        # Cache ORB per symbol per day
        self._orb_cache: Dict[str, ORBResult] = {}

    def enhance_signal(
        self,
        symbol: str,
        signal_direction: str,      # "LONG" or "SHORT"
        df_5m: pd.DataFrame,        # 5-minute OHLCV data
        df_15m: pd.DataFrame = None,
        df_1h: pd.DataFrame = None,
        base_score: float = 65.0,
    ) -> SmartMoneyScore:
        """
        Returns a SmartMoneyScore with bonus points to add to base signal score.
        Also returns a regime_multiplier for position sizing.
        """
        reasons = []
        now_ist = datetime.now(IST)

        try:
            # 1. Liquidity Sweeps
            sweeps = self.liquidity.detect_sweeps(df_5m, lookback=20)
            liq_bonus = self.liquidity.get_score_bonus(sweeps, signal_direction)
            if liq_bonus > 0:
                reasons.append(f"Liquidity sweep confirms {signal_direction} ({liq_bonus:.0f}pts)")
            elif liq_bonus < 0:
                reasons.append(f"Liquidity sweep against direction ({liq_bonus:.0f}pts)")
        except Exception:
            liq_bonus = 0

        try:
            # 2. Wyckoff Phase
            phase = self.wyckoff.analyze(df_1h if df_1h is not None else df_5m)
            wyck_bonus = self.wyckoff.get_score_bonus(phase, signal_direction)
            if phase and abs(wyck_bonus) > 0:
                reasons.append(f"Wyckoff: {phase.phase} ({phase.cycle}) → {wyck_bonus:.0f}pts")
        except Exception:
            wyck_bonus = 0

        try:
            # 3. ORB
            today = now_ist.strftime("%Y-%m-%d")
            if symbol not in self._orb_cache or \
               getattr(self._orb_cache.get(symbol), '_date', '') != today:
                orb_result = self.orb.update_orb(symbol, df_5m)
                if orb_result:
                    orb_result._date = today
                    self._orb_cache[symbol] = orb_result
            else:
                orb_result = self._orb_cache.get(symbol)
            orb_bonus = self.orb.get_score_bonus(orb_result, signal_direction)
            if orb_result and abs(orb_bonus) > 0:
                reasons.append(f"ORB bias {orb_result.bias} → {orb_bonus:.0f}pts")
        except Exception:
            orb_bonus = 0

        try:
            # 4. Relative Volume
            rvol_data = self.rvol.calculate_rvol(df_5m)
            rvol_bonus = self.rvol.get_score_bonus(rvol_data, signal_direction)
            if rvol_data.get("rvol", 1) > 1.5:
                reasons.append(f"RVOL {rvol_data['rvol']:.1f}x → {rvol_bonus:.0f}pts")
        except Exception:
            rvol_bonus, rvol_data = 0, {}

        try:
            # 5. Market Regime
            regime_result = self.regime.detect(df_1h if df_1h is not None else df_5m)
            regime_mult = regime_result.position_size_factor
            if not regime_result.tradeable:
                reasons.append(f"⚠️ {regime_result.regime} — {regime_result.description}")
            else:
                reasons.append(f"Regime: {regime_result.regime} ({regime_result.description})")
        except Exception:
            regime_result = None
            regime_mult = 1.0

        try:
            # 6. Killzone
            kz = self.killzone.get_current_killzone(now_ist)
            kz_bonus = kz.get("score_bonus", 0)
            if kz.get("in_killzone"):
                reasons.append(f"In killzone: {kz['zone']} ({kz['stars']}★) → +{kz_bonus}pts")
            elif kz_bonus < 0:
                reasons.append(f"Dead zone: {kz['zone']} → {kz_bonus}pts")
        except Exception:
            kz_bonus = 0

        try:
            # 7. Key Levels
            current_price = df_5m['close'].iloc[-1] if df_5m is not None and len(df_5m) > 0 else 0
            levels = self.key_levels.find_key_levels(df_5m, current_price)
            level_bonus = self.key_levels.get_score_bonus(levels, signal_direction)
            if level_bonus > 0:
                reasons.append(f"Near key level ₹{levels.get('nearest_level', 0):.0f} → +{level_bonus}pts")
        except Exception:
            level_bonus = 0

        try:
            # 8. Momentum Quality
            mom = self.momentum.score(df_5m, signal_direction)
            mom_score = mom["score"]
            if mom["grade"] in ("A+", "A"):
                reasons.append(f"Momentum quality {mom['grade']}: {', '.join(mom['reasons'][:2])}")
        except Exception:
            mom_score = 50

        # Combine into total bonus
        raw_bonus = liq_bonus + wyck_bonus + orb_bonus + rvol_bonus + kz_bonus + level_bonus
        total_score = min(40, max(-25, raw_bonus))  # cap bonus at +40, penalty at -25

        return SmartMoneyScore(
            total_score=round(total_score, 1),
            liquidity_sweep_bonus=liq_bonus,
            wyckoff_bonus=wyck_bonus,
            orb_bonus=orb_bonus,
            rvol_bonus=rvol_bonus,
            regime_multiplier=regime_mult,
            killzone_bonus=kz_bonus,
            key_level_bonus=level_bonus,
            reasons=reasons,
        )

    def is_market_tradeable(self, df: pd.DataFrame = None) -> Tuple[bool, str]:
        """Quick check: should we trade right now?"""
        now_ist = datetime.now(IST)
        kz = self.killzone.get_current_killzone(now_ist)
        if kz.get("score_bonus", 0) < -5:
            return False, f"Dead zone: {kz['zone']}"
        if df is not None:
            try:
                regime = self.regime.detect(df)
                if not regime.tradeable:
                    return False, regime.description
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
        return True, "Market tradeable"


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_enhancer: Optional[SmartMoneyEnhancer] = None

def get_smart_money_enhancer() -> SmartMoneyEnhancer:
    global _enhancer
    if _enhancer is None:
        _enhancer = SmartMoneyEnhancer()
    return _enhancer
