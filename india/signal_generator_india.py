"""
signal_generator_india.py — Signal generation for NSE India
Uses yfinance .NS data + parent pattern_recognition + high_accuracy_filter.
Applies all institutional strategies (IST-adapted versions).
Returns same TradeSignal dataclass as US bot — compatible interface.
"""
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# Import shared logic from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from pattern_recognition import PatternRecognizer, IndicatorSet
from high_accuracy_filter import HighAccuracyFilter, FilterResult

logger = logging.getLogger("signal_india")
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class IndiaTradeSignal:
    """Trade signal for NSE India — same fields as parent TradeSignal."""
    symbol:       str
    direction:    str           # "LONG" or "SHORT"
    signal_score: float         # 0–100
    entry_price:  float
    stop_loss:    float
    target_1:     float
    target_2:     float
    risk_reward:  float
    atr:          float
    quantity:     int           = 0
    security_id:  str           = ""
    patterns:     List[str]     = field(default_factory=list)
    rationale:    str           = ""
    quality_grade: str          = "B"
    size_multiplier: float      = 1.0
    signal_time:  str           = ""
    is_high_confidence: bool    = False

    def __post_init__(self):
        if not self.signal_time:
            self.signal_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        self.is_high_confidence = self.signal_score >= 80


class IndiaSignalGenerator:
    """
    Generates high-conviction NSE intraday signals.
    Data: yfinance .NS bars
    Filter: 26-gate HighAccuracyFilter (shared from parent)
    Strategies: 6 institutional frameworks (IST-adapted)
    """

    def __init__(self, config, watchlist: List[str] = None):
        self._config    = config
        self._watchlist = watchlist or []
        self._recognizer = PatternRecognizer()
        self._haf        = HighAccuracyFilter()
        self._signals_today: List[IndiaTradeSignal] = []

    # ── Main signal method ────────────────────────────────────────────────────

    def generate_signal(self, symbol: str,
                        current_price: float = 0.0) -> Optional[IndiaTradeSignal]:
        """
        Full signal pipeline for one NSE symbol.
        Returns IndiaTradeSignal if approved, None otherwise.
        """
        try:
            from data_fetch_dhan import get_ohlcv_multi_tf, get_security_id
            bars = get_ohlcv_multi_tf(symbol)
            df_5m  = bars.get("5m")
            df_15m = bars.get("15m")
            df_1h  = bars.get("1h")

            if df_5m is None or df_5m.empty or len(df_5m) < 20:
                return None

            ltp = current_price or float(df_5m["close"].iloc[-1])
            if ltp <= 0:
                return None

            # Indicators on 5m data
            ind = self._recognizer.compute_indicators(df_5m)
            if ind is None:
                return None

            # Direction from 5m momentum
            direction = self._get_direction(ind, df_5m)
            if direction is None:
                return None

            # Multi-timeframe alignment check
            if not self._check_mtf_alignment(direction, df_15m, df_1h):
                return None

            # Base score from indicators
            score = self._compute_base_score(ind, direction, df_5m)
            if score < self._config.MIN_SIGNAL_SCORE:
                return None

            # ATR-based SL/TP
            atr = float(ind.atr) if ind.atr and ind.atr > 0 else ltp * 0.01
            sl_dist = atr * self._config.ATR_SL_MULTIPLIER
            tp_dist = atr * self._config.ATR_TP_MULTIPLIER

            if direction == "LONG":
                stop_loss = ltp - sl_dist
                target_1  = ltp + sl_dist * 2
                target_2  = ltp + tp_dist
            else:
                stop_loss = ltp + sl_dist
                target_1  = ltp - sl_dist * 2
                target_2  = ltp - tp_dist

            rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

            # ── Institutional strategy boosters (IST-adapted) ─────────────────
            score = self._apply_institutional_boosters(
                symbol, direction, score, df_5m, ltp, ind, current_price
            )

            # ── 26-gate HAF ───────────────────────────────────────────────────
            try:
                filter_result: FilterResult = self._haf.apply_all_gates(
                    symbol=symbol,
                    direction=direction,
                    score=score,
                    ltp=ltp,
                    df_5m=df_5m,
                    indicators=ind,
                )
                if not filter_result.passed:
                    logger.debug(f"{symbol}: HAF rejected — {filter_result.rejection_reason}")
                    return None
                score          = filter_result.final_score
                quality_grade  = filter_result.quality_grade
                size_mult      = filter_result.size_multiplier
            except Exception as e:
                logger.debug(f"{symbol}: HAF error (fail-open): {e}")
                quality_grade = "B"
                size_mult     = 1.0

            if score < self._config.FINAL_EXEC_MIN_SCORE:
                logger.debug(f"{symbol}: final score {score:.1f} < {self._config.FINAL_EXEC_MIN_SCORE}")
                return None

            security_id = get_security_id(symbol) or ""

            return IndiaTradeSignal(
                symbol        = symbol,
                direction     = direction,
                signal_score  = round(score, 1),
                entry_price   = round(ltp, 2),
                stop_loss     = round(stop_loss, 2),
                target_1      = round(target_1, 2),
                target_2      = round(target_2, 2),
                risk_reward   = rr,
                atr           = round(atr, 4),
                security_id   = security_id,
                patterns      = getattr(ind, "_patterns", []),
                quality_grade = quality_grade,
                size_multiplier = size_mult,
                rationale     = f"Score {score:.0f} | {direction} | RR {rr:.1f}",
            )

        except Exception as e:
            logger.debug(f"generate_signal {symbol}: {e}")
            return None

    # ── Direction ─────────────────────────────────────────────────────────────

    def _get_direction(self, ind: IndicatorSet, df: pd.DataFrame) -> Optional[str]:
        """Determine LONG/SHORT from multi-indicator consensus."""
        try:
            bullish = 0
            bearish = 0

            close = float(df["close"].iloc[-1])

            # EMA trend
            if hasattr(ind, "ema9") and hasattr(ind, "ema21"):
                if ind.ema9 and ind.ema21:
                    if ind.ema9 > ind.ema21:
                        bullish += 1
                    else:
                        bearish += 1

            # MACD
            if hasattr(ind, "macd") and hasattr(ind, "macd_signal"):
                if ind.macd and ind.macd_signal:
                    if ind.macd > ind.macd_signal:
                        bullish += 1
                    else:
                        bearish += 1

            # RSI
            if hasattr(ind, "rsi") and ind.rsi:
                if ind.rsi > 55:
                    bullish += 1
                elif ind.rsi < 45:
                    bearish += 1

            # VWAP
            if hasattr(ind, "vwap") and ind.vwap and ind.vwap > 0:
                if close > ind.vwap:
                    bullish += 1
                else:
                    bearish += 1

            if bullish >= 3:
                return "LONG"
            if bearish >= 3:
                return "SHORT"
            return None  # no clear direction
        except Exception:
            return None

    # ── Base score ────────────────────────────────────────────────────────────

    def _compute_base_score(self, ind: IndicatorSet, direction: str,
                            df: pd.DataFrame) -> float:
        """Compute base confidence score 0–100 from indicator alignment."""
        score = 50.0
        try:
            close = float(df["close"].iloc[-1])

            # RSI contribution
            if hasattr(ind, "rsi") and ind.rsi:
                rsi = ind.rsi
                if direction == "LONG":
                    if 45 < rsi < 65:
                        score += 8
                    elif 35 < rsi <= 45:
                        score += 4   # oversold bounce
                else:
                    if 35 < rsi < 55:
                        score += 8
                    elif 55 <= rsi < 65:
                        score += 4   # overbought fade

            # MACD histogram
            if hasattr(ind, "macd_hist") and ind.macd_hist is not None:
                hist = ind.macd_hist
                if direction == "LONG" and hist > 0:
                    score += 6
                elif direction == "SHORT" and hist < 0:
                    score += 6

            # Volume surge
            if "volume" in df.columns and len(df) >= 20:
                vol_avg = df["volume"].rolling(20).mean().iloc[-1]
                vol_now = df["volume"].iloc[-1]
                if vol_avg > 0:
                    rvol = vol_now / vol_avg
                    if rvol >= 2.0:
                        score += 8
                    elif rvol >= 1.5:
                        score += 4

            # Bollinger squeeze break
            if hasattr(ind, "bb_upper") and hasattr(ind, "bb_lower") and ind.bb_upper and ind.bb_lower:
                bb_width = (ind.bb_upper - ind.bb_lower) / (close + 1e-9)
                if bb_width < 0.03:  # squeeze
                    score += 5

            # ADX trend strength
            if hasattr(ind, "adx") and ind.adx:
                if ind.adx >= 25:
                    score += 8
                elif ind.adx >= 20:
                    score += 4

        except Exception as e:
            logger.debug(f"base_score error: {e}")

        return min(score, 100.0)

    # ── MTF alignment ─────────────────────────────────────────────────────────

    def _check_mtf_alignment(self, direction: str,
                             df_15m: Optional[pd.DataFrame],
                             df_1h: Optional[pd.DataFrame]) -> bool:
        """At least one higher timeframe must align (not conflict)."""
        aligned = 0
        for df in [df_15m, df_1h]:
            if df is None or df.empty or len(df) < 10:
                aligned += 1   # no data = neutral (don't block)
                continue
            try:
                ema_fast = df["close"].ewm(span=9,  adjust=False).mean().iloc[-1]
                ema_slow = df["close"].ewm(span=21, adjust=False).mean().iloc[-1]
                if direction == "LONG" and ema_fast >= ema_slow:
                    aligned += 1
                elif direction == "SHORT" and ema_fast <= ema_slow:
                    aligned += 1
            except Exception:
                aligned += 1
        return aligned >= 1

    # ── Institutional boosters ────────────────────────────────────────────────

    def _apply_institutional_boosters(self, symbol: str, direction: str,
                                      score: float, df_5m: pd.DataFrame,
                                      ltp: float, ind: IndicatorSet,
                                      current_price: float) -> float:
        """Apply all 6 institutional strategies. All fail-open."""
        try:
            from institutional_strategies_india import (
                get_power_hour_score_india,
                get_gap_fade_score_india,
                get_pairs_signal_india,
                get_cross_sectional_rank,
                get_vwap_reclaim_score,
                get_tod_rvol_score,
                get_sortino_size_multiplier,
                record_bar_volume,
            )

            # 1. Cross-sectional momentum
            if self._config.CSM_ENABLED:
                try:
                    d, r = get_cross_sectional_rank(symbol, self._watchlist or [symbol])
                    if d:
                        score = min(100.0, score + d)
                        logger.debug(f"{symbol}: CSM {d:+.0f} {r}")
                except Exception:
                    pass

            # 2. VWAP reclaim
            if self._config.VWAP_RECLAIM_ENABLED:
                try:
                    vwap_val = float(ind.vwap) if hasattr(ind, "vwap") and ind.vwap else 0.0
                    if vwap_val > 0:
                        d, r = get_vwap_reclaim_score(df_5m, ltp, direction, vwap_val)
                        if d:
                            score = min(100.0, score + d)
                            logger.debug(f"{symbol}: VWAP_RECLAIM {d:+.0f} {r}")
                except Exception:
                    pass

            # 3. Power hour (IST)
            if self._config.POWER_HOUR_ENABLED:
                try:
                    d, r = get_power_hour_score_india(direction, score)
                    if d:
                        score = min(100.0, score + d)
                        logger.debug(f"{symbol}: POWER_HOUR_IST {d:+.0f} {r}")
                except Exception:
                    pass

            # 4. Indian pairs signal
            if self._config.PAIRS_SIGNAL_ENABLED:
                try:
                    d, r = get_pairs_signal_india(symbol, direction)
                    if d:
                        score = min(100.0, score + d)
                        logger.debug(f"{symbol}: PAIRS_INDIA {d:+.0f} {r}")
                except Exception:
                    pass

            # 5. TOD RVOL
            if self._config.TOD_RVOL_ENABLED and df_5m is not None and not df_5m.empty:
                try:
                    bar_time = df_5m.index[-1].strftime("%H:%M")
                    cur_vol  = int(df_5m["volume"].iloc[-1])
                    record_bar_volume(symbol, bar_time, cur_vol)
                    d, r = get_tod_rvol_score(symbol, bar_time, cur_vol, direction)
                    if d:
                        score = min(100.0, score + d)
                        logger.debug(f"{symbol}: TOD_RVOL {d:+.0f} {r}")
                except Exception:
                    pass

            # 6. Gap fade (IST window)
            if self._config.GAP_FADE_ENABLED:
                try:
                    d, r = get_gap_fade_score_india(symbol, direction, ltp)
                    if d:
                        score = min(100.0, score + d)
                        logger.debug(f"{symbol}: GAP_FADE_IST {d:+.0f} {r}")
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[suppressed] institutional boosters {symbol}: {e}")

        return score
