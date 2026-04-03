"""
market_regime.py — NSE Momentum Groww AI Bot
Market Regime Detection Engine — 18+ Years of Trading Experience

⚠️ MOST IMPORTANT LESSON FROM 18 YEARS:
"The strategy that works in trending markets DESTROYS capital in ranging markets.
 Detect the regime FIRST, then apply the right strategy."

Regimes:
  STRONG_TREND_UP   — Strong directional move, ride momentum hard
  STRONG_TREND_DOWN — Strong directional move, short heavily
  WEAK_TREND_UP     — Mild uptrend, lighter positions
  WEAK_TREND_DOWN   — Mild downtrend, lighter positions
  RANGING           — Chop/consolidation — AVOID breakouts, fade extremes
  HIGH_VOLATILITY   — Explosive moves, wider SL, smaller size
  LOW_VOLATILITY    — Squeeze building — wait for breakout
  OPENING_DRIVE     — 9:15–10:00 AM: highest momentum window of day
  MIDDAY_CHOP       — 11:00 AM–1:00 PM: lowest signal quality — reduce size
  AFTERNOON_TREND   — 1:30–3:00 PM: institutional activity resumes

Each regime has:
  - Optimal strategy (momentum / mean-reversion / avoid)
  - Position size multiplier (scale up in ideal conditions)
  - Preferred patterns
  - Risk multiplier
"""

import logging
from dataclasses import dataclass
from datetime import time
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class RegimeState:
    """Complete market regime description with trading guidance."""
    regime: str                    # e.g., "STRONG_TREND_UP"
    confidence: float              # 0–100
    strategy: str                  # "MOMENTUM" / "MEAN_REVERSION" / "AVOID"
    size_multiplier: float         # Scale position size (0.0–1.5)
    risk_multiplier: float         # Scale SL width
    preferred_direction: str       # "LONG" / "SHORT" / "BOTH" / "NONE"
    description: str
    adx: float = 0.0
    bb_width: float = 0.0
    atr_pct: float = 0.0
    trend_strength: float = 0.0
    session: str = ""              # Current trading session

    @property
    def is_tradeable(self) -> bool:
        return self.strategy != "AVOID" and self.size_multiplier > 0

    @property
    def emoji(self) -> str:
        mapping = {
            "STRONG_TREND_UP":   "🚀",
            "STRONG_TREND_DOWN": "💣",
            "WEAK_TREND_UP":     "📈",
            "WEAK_TREND_DOWN":   "📉",
            "RANGING":           "↔️",
            "HIGH_VOLATILITY":   "⚡",
            "LOW_VOLATILITY":    "😴",
            "OPENING_DRIVE":     "🔥",
            "MIDDAY_CHOP":       "⚠️",
            "AFTERNOON_TREND":   "🌅",
        }
        return mapping.get(self.regime, "📊")


# Session windows (IST)
OPENING_DRIVE_START = time(9, 15)
OPENING_DRIVE_END   = time(10, 0)
MORNING_END         = time(11, 0)
MIDDAY_START        = time(11, 0)
MIDDAY_END          = time(13, 0)
AFTERNOON_START     = time(13, 30)
AFTERNOON_END       = time(15, 0)
EOD_START           = time(15, 0)


class MarketRegimeDetector:
    """
    Detects current market regime from multiple data sources.

    18-year rule: "Trade WITH the regime, not against it.
    A good trader doesn't fight the market — they read it."
    """

    def __init__(self):
        self._nifty_regime: Optional[RegimeState] = None
        self._last_regime_time: Optional[float] = None
        self._regime_cache_ttl = 300  # Refresh every 5 minutes

    # --------------------------------------------------------
    # MAIN DETECTION
    # --------------------------------------------------------

    def detect(self, df: pd.DataFrame, nifty_df: Optional[pd.DataFrame] = None) -> RegimeState:
        """
        Detect the current market regime from price data.

        Args:
            df: 5-min OHLCV DataFrame (with indicators already computed)
            nifty_df: Nifty50 DataFrame for macro context

        Returns:
            RegimeState with complete trading guidance
        """
        if df is None or len(df) < 30:
            return self._default_regime()

        session = self._get_current_session()

        # Extract key metrics
        adx         = self._get_adx(df)
        bb_width    = self._get_bb_width(df)
        atr_pct     = self._get_atr_pct(df)
        trend_str   = self._get_trend_strength(df)
        trend_dir   = self._get_trend_direction(df)
        volatility  = self._classify_volatility(df, atr_pct)

        # Opening drive (9:15–10:00 AM) — highest momentum session
        if session == "OPENING_DRIVE":
            return RegimeState(
                regime="OPENING_DRIVE",
                confidence=85,
                strategy="MOMENTUM",
                size_multiplier=1.2,       # Bigger size in opening drive
                risk_multiplier=1.0,
                preferred_direction=trend_dir,
                description="Opening drive — highest momentum window. Trade breakouts aggressively.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # Midday chop (11 AM–1 PM) — avoid or reduce heavily
        if session == "MIDDAY_CHOP":
            return RegimeState(
                regime="MIDDAY_CHOP",
                confidence=80,
                strategy="AVOID",
                size_multiplier=0.3,       # Very small size or skip
                risk_multiplier=0.8,
                preferred_direction="NONE",
                description="Midday chop (11 AM–1 PM IST). Low signal quality. Reduce size 70% or skip.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # EOD — reduce risk
        if session == "EOD":
            return RegimeState(
                regime="RANGING",
                confidence=70,
                strategy="AVOID",
                size_multiplier=0.0,
                risk_multiplier=0.5,
                preferred_direction="NONE",
                description="EOD (3 PM+). No new entries. Close all positions by 3:20 PM IST.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # High volatility — explosive moves (earnings, news, events)
        if atr_pct > 0.8:
            return RegimeState(
                regime="HIGH_VOLATILITY",
                confidence=75,
                strategy="MOMENTUM",
                size_multiplier=0.6,       # Smaller size with wider SL
                risk_multiplier=1.5,       # Wider stops needed
                preferred_direction=trend_dir,
                description=f"High volatility regime (ATR {atr_pct:.2f}%). Wider SL, smaller size.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # Low volatility / squeeze — wait for breakout
        if bb_width < 0.02 and atr_pct < 0.2:
            return RegimeState(
                regime="LOW_VOLATILITY",
                confidence=70,
                strategy="AVOID",
                size_multiplier=0.4,
                risk_multiplier=0.8,
                preferred_direction="BOTH",
                description="Volatility squeeze. BB tight. Wait for explosive breakout then enter.",
                adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                trend_strength=trend_str, session=session
            )

        # Strong trend (ADX > 30)
        if adx >= 30:
            if trend_dir == "LONG":
                return RegimeState(
                    regime="STRONG_TREND_UP",
                    confidence=min(50 + adx, 95),
                    strategy="MOMENTUM",
                    size_multiplier=1.2,
                    risk_multiplier=1.0,
                    preferred_direction="LONG",
                    description=f"Strong uptrend. ADX={adx:.0f}. Trade LONGs with momentum. Avoid shorts.",
                    adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                    trend_strength=trend_str, session=session
                )
            else:
                return RegimeState(
                    regime="STRONG_TREND_DOWN",
                    confidence=min(50 + adx, 95),
                    strategy="MOMENTUM",
                    size_multiplier=1.2,
                    risk_multiplier=1.0,
                    preferred_direction="SHORT",
                    description=f"Strong downtrend. ADX={adx:.0f}. Trade SHORTs with momentum. Avoid longs.",
                    adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                    trend_strength=trend_str, session=session
                )

        # Weak trend (ADX 20–30)
        if 20 <= adx < 30:
            if trend_dir == "LONG":
                return RegimeState(
                    regime="WEAK_TREND_UP",
                    confidence=60,
                    strategy="MOMENTUM",
                    size_multiplier=0.8,
                    risk_multiplier=1.0,
                    preferred_direction="LONG",
                    description=f"Mild uptrend. ADX={adx:.0f}. Prefer longs but tighten targets.",
                    adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                    trend_strength=trend_str, session=session
                )
            else:
                return RegimeState(
                    regime="WEAK_TREND_DOWN",
                    confidence=60,
                    strategy="MOMENTUM",
                    size_multiplier=0.8,
                    risk_multiplier=1.0,
                    preferred_direction="SHORT",
                    description=f"Mild downtrend. ADX={adx:.0f}. Prefer shorts.",
                    adx=adx, bb_width=bb_width, atr_pct=atr_pct,
                    trend_strength=trend_str, session=session
                )

        # Ranging (ADX < 20)
        return RegimeState(
            regime="RANGING",
            confidence=65,
            strategy="AVOID",
            size_multiplier=0.4,
            risk_multiplier=0.7,
            preferred_direction="BOTH",
            description=f"Ranging/choppy market. ADX={adx:.0f}. Skip momentum signals. Fade extremes only.",
            adx=adx, bb_width=bb_width, atr_pct=atr_pct,
            trend_strength=trend_str, session=session
        )

    def detect_nifty_regime(self, nifty_df: pd.DataFrame) -> RegimeState:
        """Detect Nifty50 macro regime — used to filter stock signals."""
        return self.detect(nifty_df)

    # --------------------------------------------------------
    # SESSION DETECTION (IST)
    # --------------------------------------------------------

    def _get_current_session(self) -> str:
        now_ist = get_current_ist_time()
        t = now_ist.time()
        if OPENING_DRIVE_START <= t < OPENING_DRIVE_END:
            return "OPENING_DRIVE"
        elif OPENING_DRIVE_END <= t < MIDDAY_START:
            return "MORNING"
        elif MIDDAY_START <= t < MIDDAY_END:
            return "MIDDAY_CHOP"
        elif AFTERNOON_START <= t < AFTERNOON_END:
            return "AFTERNOON_TREND"
        elif t >= EOD_START:
            return "EOD"
        return "MORNING"

    # --------------------------------------------------------
    # METRIC EXTRACTORS
    # --------------------------------------------------------

    def _get_adx(self, df: pd.DataFrame) -> float:
        if "adx" in df.columns:
            val = df["adx"].iloc[-1]
            return float(val) if not pd.isna(val) else 20.0
        return 20.0

    def _get_bb_width(self, df: pd.DataFrame) -> float:
        if "bb_width" in df.columns:
            val = df["bb_width"].iloc[-1]
            return float(val) if not pd.isna(val) else 0.03
        return 0.03

    def _get_atr_pct(self, df: pd.DataFrame) -> float:
        """ATR as percentage of price."""
        if "atr" in df.columns:
            atr = df["atr"].iloc[-1]
            close = df["close"].iloc[-1]
            if close > 0:
                return float(atr / close * 100)
        return 0.3

    def _get_trend_strength(self, df: pd.DataFrame) -> float:
        """0–100 trend strength score."""
        adx = self._get_adx(df)
        return min(adx * 2, 100)

    def _get_trend_direction(self, df: pd.DataFrame) -> str:
        """Determine trend direction from EMAs and price."""
        try:
            row = df.iloc[-1]
            ema9  = float(row.get("ema9", 0))
            ema21 = float(row.get("ema21", 0))
            ema50 = float(row.get("ema50", 0))
            close = float(row.get("close", 0))
            plus_di  = float(row.get("plus_di", 0))
            minus_di = float(row.get("minus_di", 0))

            bullish_points = 0
            if ema9 > ema21:
                bullish_points += 1
            if ema21 > ema50:
                bullish_points += 1
            if close > ema9:
                bullish_points += 1
            if plus_di > minus_di:
                bullish_points += 1

            return "LONG" if bullish_points >= 3 else "SHORT"
        except Exception:
            return "LONG"

    def _classify_volatility(self, df: pd.DataFrame, atr_pct: float) -> str:
        if atr_pct > 0.8:
            return "HIGH"
        elif atr_pct < 0.2:
            return "LOW"
        return "NORMAL"

    def _default_regime(self) -> RegimeState:
        return RegimeState(
            regime="RANGING",
            confidence=30,
            strategy="AVOID",
            size_multiplier=0.5,
            risk_multiplier=1.0,
            preferred_direction="BOTH",
            description="Insufficient data for regime detection.",
            session=self._get_current_session()
        )

    # --------------------------------------------------------
    # GAP ANALYSIS (at 9:15 AM IST open)
    # --------------------------------------------------------

    def analyze_gap(self, prev_close: float, today_open: float) -> Dict:
        """
        Analyze opening gap vs previous close.
        18yr rule: "Gap direction sets the day's bias."

        Gap > 0.5%: Bullish gap — buy dips in first hour
        Gap < -0.5%: Bearish gap — sell rallies in first hour
        Gap > 2%: Huge gap — fade after 30-min equilibrium
        """
        if prev_close <= 0:
            return {"type": "NO_GAP", "pct": 0, "bias": "NEUTRAL"}

        gap_pct = ((today_open - prev_close) / prev_close) * 100

        if gap_pct >= 2.0:
            return {
                "type": "HUGE_GAP_UP", "pct": gap_pct,
                "bias": "FADE_AFTER_BALANCE",
                "note": "Gap >2%: Wait 30min, then fade if market reverses. Don't chase."
            }
        elif gap_pct >= 0.5:
            return {
                "type": "GAP_UP", "pct": gap_pct,
                "bias": "BULLISH",
                "note": f"Gap up {gap_pct:.1f}%: Buy pullbacks toward VWAP in first hour."
            }
        elif gap_pct <= -2.0:
            return {
                "type": "HUGE_GAP_DOWN", "pct": gap_pct,
                "bias": "FADE_AFTER_BALANCE",
                "note": "Gap down >2%: Wait 30min equilibrium, then trade direction."
            }
        elif gap_pct <= -0.5:
            return {
                "type": "GAP_DOWN", "pct": gap_pct,
                "bias": "BEARISH",
                "note": f"Gap down {gap_pct:.1f}%: Short rallies toward VWAP in first hour."
            }
        else:
            return {
                "type": "FLAT_OPEN", "pct": gap_pct,
                "bias": "NEUTRAL",
                "note": "Flat open: Wait for breakout direction above/below ORB."
            }

    # --------------------------------------------------------
    # ADR (Average Daily Range) FILTER
    # --------------------------------------------------------

    def get_adr_filter(self, df_daily: pd.DataFrame, adr_days: int = 20) -> Dict:
        """
        Calculate Average Daily Range.
        18yr rule: "Only trade stocks that MOVE. Min 1.5% ADR for intraday."
        """
        if df_daily is None or len(df_daily) < adr_days:
            return {"adr_pct": 0, "tradeable": True}

        recent = df_daily.tail(adr_days)
        daily_ranges = (recent["high"] - recent["low"]) / recent["close"] * 100
        adr_pct = daily_ranges.mean()

        return {
            "adr_pct": round(adr_pct, 2),
            "tradeable": adr_pct >= 1.5,
            "note": f"ADR={adr_pct:.1f}% {'✅ sufficient' if adr_pct >= 1.5 else '❌ too low for intraday'}"
        }
