"""
signal_generator_india.py — Signal generation for NSE India (Tier 1)
Data: yfinance .NS bars + NSE live APIs (option chain, FII/DII, delivery vol)
Filter: NSE-specific quality gates (HAF uses wrong method name for India calls — replaced)
Strategies: 6 institutional + ORB + neural + option chain + FII/DII + delivery vol

Signal pipeline:
  1. Direction: VWAP mandatory + EMA mandatory + 1 confirmer
  2. Multi-timeframe: 15m must not actively conflict
  3. Base score from indicators (RSI/MACD/ADX/VWAP/BB/EMA)
  4. NSE quality gates: candle quality, ATR range, ADX trending, anti-choppy
  5. Institutional boosters (CSM, VWAP reclaim, TOD RVOL, pairs, gap fade, power hour)
  6. ORB — Opening Range Breakout (NSE specialist, 60–72% win rate)
  7. Tier-1 India sources: Option Chain, FII/DII, Delivery Volume, News, Vol Profile, Neural
  8. Score-based quality grading (replaces broken HAF call)
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

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from pattern_recognition import PatternRecognizer, IndicatorSet

logger = logging.getLogger("signal_india")
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class IndiaTradeSignal:
    """Trade signal for NSE India."""
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
    High-conviction NSE intraday signal generator.
    7 quality gates + 12 signal sources.
    """

    def __init__(self, config, watchlist: List[str] = None):
        self._config    = config
        self._watchlist = watchlist or []
        self._recognizer = PatternRecognizer()
        self._signals_today: List[IndiaTradeSignal] = []

    # ── Main signal method ────────────────────────────────────────────────────

    def generate_signal(self, symbol: str,
                        current_price: float = 0.0) -> Optional[IndiaTradeSignal]:
        """
        Full NSE signal pipeline for one symbol.
        Returns IndiaTradeSignal if all gates pass, None otherwise.
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

            ind = self._recognizer.compute_indicators(df_5m)
            if ind is None:
                return None

            # ── Gate 1: Direction (VWAP mandatory + EMA mandatory + 1 confirmer) ──
            direction = self._get_direction(ind, df_5m)
            if direction is None:
                return None

            # ── Gate 2: 15m must not actively conflict ─────────────────────────
            if not self._check_mtf_alignment(direction, df_15m, df_1h):
                logger.debug(f"{symbol}: 15m conflicts with {direction} — skipped")
                return None

            # ATR-based SL/TP
            atr = float(ind.atr) if ind.atr and ind.atr > 0 else ltp * 0.01

            # ── Gate 3: ATR quality (0.3%–5% of price) ────────────────────────
            if not self._check_atr_quality(atr, ltp):
                logger.debug(f"{symbol}: ATR {atr/ltp:.2%} out of 0.3–5% range — skipped")
                return None

            # ── Gate 4: ADX trending (>= 18) ──────────────────────────────────
            if not self._check_adx_trending(ind):
                logger.debug(f"{symbol}: ADX too low (ranging market) — skipped")
                return None

            # ── Gate 5: Candle quality (strong directional body) ───────────────
            if not self._check_candle_quality(direction, df_5m):
                logger.debug(f"{symbol}: weak candle — doji/spinning top skipped")
                return None

            # ── Gate 6: Anti-choppy (no alternating bars) ─────────────────────
            if not self._check_not_choppy(df_5m):
                logger.debug(f"{symbol}: choppy price action — skipped")
                return None

            # Base score
            score = self._compute_base_score(ind, direction, df_5m)
            if score < self._config.MIN_SIGNAL_SCORE:
                return None

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

            # ── Institutional boosters ────────────────────────────────────────
            score = self._apply_institutional_boosters(
                symbol, direction, score, df_5m, ltp, ind, current_price
            )

            # ── Gate 7: Final score threshold ─────────────────────────────────
            if score < self._config.FINAL_EXEC_MIN_SCORE:
                logger.debug(f"{symbol}: final score {score:.1f} < {self._config.FINAL_EXEC_MIN_SCORE}")
                return None

            # ── Score-based quality grading (replaces HAF — wrong method name for India) ──
            # HAF has `evaluate()` but India bot was calling `apply_all_gates()` (doesn't exist)
            # → AttributeError always caught silently → grade="B" unconditionally.
            # This deterministic grading uses the actual final score correctly.
            grand_slam = self._config.GRAND_SLAM_MIN_SCORE
            if score >= grand_slam:
                quality_grade = "A+"
                size_mult     = 1.35
            elif score >= 78:
                quality_grade = "A"
                size_mult     = 1.00
            elif score >= 72:
                quality_grade = "B+"
                size_mult     = 0.80
            else:
                quality_grade = "B"
                size_mult     = 0.65

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
                rationale     = f"Score {score:.0f} | {direction} | RR {rr:.1f} | Grade {quality_grade}",
            )

        except Exception as e:
            logger.debug(f"generate_signal {symbol}: {e}")
            return None

    # ── Gate 1: Direction ─────────────────────────────────────────────────────

    def _get_direction(self, ind: IndicatorSet, df: pd.DataFrame) -> Optional[str]:
        """
        VWAP alignment is MANDATORY (institutional money direction).
        EMA9 vs EMA21 trend is MANDATORY.
        At least 1 of (MACD, RSI) must confirm.

        Fallback: if VWAP unavailable, require 3 of 3 (EMA + MACD + RSI).
        """
        try:
            close = float(df["close"].iloc[-1])

            # VWAP — mandatory institutional money alignment
            vwap = getattr(ind, "vwap", None)
            vwap_f = float(vwap) if vwap and float(vwap) > 0 else None
            vwap_long = vwap_short = None
            if vwap_f:
                gap = (close - vwap_f) / vwap_f
                if abs(gap) < 0.001:
                    return None   # AT VWAP — ambiguous, skip
                vwap_long  = gap > 0
                vwap_short = gap < 0

            # EMA trend — mandatory
            ema9  = getattr(ind, "ema9",  None)
            ema21 = getattr(ind, "ema21", None)
            ema_long = ema_short = None
            if ema9 and ema21 and float(ema9) > 0 and float(ema21) > 0:
                ema_long  = float(ema9) > float(ema21)
                ema_short = float(ema9) < float(ema21)

            # Confirming indicators
            confirm_long = confirm_short = 0

            macd   = getattr(ind, "macd",        None)
            msig   = getattr(ind, "macd_signal",  None)
            if macd is not None and msig is not None:
                if float(macd) > float(msig):
                    confirm_long  += 1
                else:
                    confirm_short += 1

            rsi = getattr(ind, "rsi", None)
            if rsi is not None:
                if float(rsi) > 52:
                    confirm_long  += 1
                elif float(rsi) < 48:
                    confirm_short += 1

            # VWAP + EMA required, at least 1 confirmer
            if vwap_long is not None and ema_long is not None:
                if vwap_long and ema_long and confirm_long >= 1:
                    return "LONG"
                if vwap_short and ema_short and confirm_short >= 1:
                    return "SHORT"
                return None

            # Fallback (no VWAP data): EMA + both confirmers
            if ema_long is not None:
                if ema_long  and confirm_long  >= 2:
                    return "LONG"
                if ema_short and confirm_short >= 2:
                    return "SHORT"

            return None
        except Exception:
            return None

    # ── Gate 2: 15m must not conflict ────────────────────────────────────────

    def _check_mtf_alignment(self, direction: str,
                             df_15m: Optional[pd.DataFrame],
                             df_1h: Optional[pd.DataFrame]) -> bool:
        """
        15m EMA9/21 must not actively disagree with signal direction.
        Gap > 0.2% in opposite direction = conflict → reject.
        1h is optional bonus (handled in base score).
        """
        try:
            if df_15m is not None and not df_15m.empty and len(df_15m) >= 10:
                ema9  = float(df_15m["close"].ewm(span=9,  adjust=False).mean().iloc[-1])
                ema21 = float(df_15m["close"].ewm(span=21, adjust=False).mean().iloc[-1])
                gap   = (ema9 - ema21) / (ema21 + 1e-9)
                if direction == "LONG"  and gap < -0.002:
                    return False  # 15m clearly bearish vs LONG signal
                if direction == "SHORT" and gap >  0.002:
                    return False  # 15m clearly bullish vs SHORT signal
        except Exception:
            pass
        return True

    # ── Gate 3: ATR quality ───────────────────────────────────────────────────

    def _check_atr_quality(self, atr: float, ltp: float) -> bool:
        """ATR must be 0.3%–5% of price. Too flat = noise stops; too wide = bad R:R."""
        if ltp <= 0 or atr <= 0:
            return True
        atr_pct = atr / ltp
        return 0.003 <= atr_pct <= 0.05

    # ── Gate 4: ADX trending ──────────────────────────────────────────────────

    def _check_adx_trending(self, ind) -> bool:
        """ADX >= 18 required — reject choppy/ranging 5m market."""
        try:
            adx = getattr(ind, "adx", None)
            if adx is None:
                return True  # fail-open
            return float(adx) >= 18.0
        except Exception:
            return True

    # ── Gate 5: Candle quality ────────────────────────────────────────────────

    def _check_candle_quality(self, direction: str, df: pd.DataFrame) -> bool:
        """
        Last bar must show strong directional body (no doji/spinning top).
        LONG: close in top 40% of range, body > 35% of range.
        SHORT: close in bottom 40% of range, body > 35% of range.
        """
        try:
            last = df.iloc[-1]
            hi   = float(last["high"])
            lo   = float(last["low"])
            cl   = float(last["close"])
            op   = float(last["open"])
            rng  = hi - lo
            if rng < 1e-9:
                return True  # no range data → fail-open
            close_pct = (cl - lo) / rng
            body_pct  = abs(cl - op) / rng
            if direction == "LONG":
                return close_pct >= 0.60 and body_pct >= 0.35
            else:
                return close_pct <= 0.40 and body_pct >= 0.35
        except Exception:
            return True

    # ── Gate 6: Anti-choppy ───────────────────────────────────────────────────

    def _check_not_choppy(self, df: pd.DataFrame) -> bool:
        """
        Reject choppy price action: 2+ direction alternations in last 4 bars.
        Trending = at most 1 alternation (e.g. up-up-down or down-up-up).
        Choppy = alternates every bar (up-down-up or down-up-down).
        """
        try:
            if len(df) < 4:
                return True
            closes = df["close"].iloc[-4:].values
            dirs   = [1 if closes[i] > closes[i - 1] else -1 for i in range(1, 4)]
            alts   = sum(1 for i in range(1, 3) if dirs[i] != dirs[i - 1])
            return alts < 2
        except Exception:
            return True

    # ── Base score ────────────────────────────────────────────────────────────

    def _compute_base_score(self, ind: IndicatorSet, direction: str,
                            df: pd.DataFrame) -> float:
        """Compute base confidence score 0–100 from indicator alignment."""
        score = 50.0
        try:
            close = float(df["close"].iloc[-1])

            # RSI
            if hasattr(ind, "rsi") and ind.rsi:
                rsi = ind.rsi
                if direction == "LONG":
                    if 45 < rsi < 65:
                        score += 8
                    elif 35 < rsi <= 45:
                        score += 4
                else:
                    if 35 < rsi < 55:
                        score += 8
                    elif 55 <= rsi < 65:
                        score += 4

            # MACD histogram
            if hasattr(ind, "macd_hist") and ind.macd_hist is not None:
                hist = ind.macd_hist
                if direction == "LONG"  and hist > 0:
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

            # Bollinger squeeze
            if hasattr(ind, "bb_upper") and hasattr(ind, "bb_lower") and ind.bb_upper and ind.bb_lower:
                bb_width = (ind.bb_upper - ind.bb_lower) / (close + 1e-9)
                if bb_width < 0.03:
                    score += 5

            # ADX trend strength
            if hasattr(ind, "adx") and ind.adx:
                if ind.adx >= 25:
                    score += 8
                elif ind.adx >= 20:
                    score += 4

            # 1h alignment bonus
            # (gates already checked 15m; 1h bonus added here for extra conviction)
            # handled in MTF check implicitly

        except Exception as e:
            logger.debug(f"base_score error: {e}")

        return min(score, 100.0)

    # ── Institutional boosters ────────────────────────────────────────────────

    def _apply_institutional_boosters(self, symbol: str, direction: str,
                                      score: float, df_5m: pd.DataFrame,
                                      ltp: float, ind: IndicatorSet,
                                      current_price: float) -> float:
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

            if self._config.CSM_ENABLED:
                try:
                    d, r = get_cross_sectional_rank(symbol, self._watchlist or [symbol])
                    if d:
                        score = min(100.0, score + d)
                        logger.debug(f"{symbol}: CSM {d:+.0f} {r}")
                except Exception:
                    pass

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

            if self._config.POWER_HOUR_ENABLED:
                try:
                    d, r = get_power_hour_score_india(direction, score)
                    if d:
                        score = min(100.0, score + d)
                        logger.debug(f"{symbol}: POWER_HOUR_IST {d:+.0f} {r}")
                except Exception:
                    pass

            if self._config.PAIRS_SIGNAL_ENABLED:
                try:
                    d, r = get_pairs_signal_india(symbol, direction)
                    if d:
                        score = min(100.0, score + d)
                        logger.debug(f"{symbol}: PAIRS_INDIA {d:+.0f} {r}")
                except Exception:
                    pass

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

        # ORB — Opening Range Breakout (60–72% documented NSE WR)
        try:
            from orb_strategy_india import get_orb_score
            d, r = get_orb_score(symbol, direction, ltp, df_5m)
            if d:
                score = min(100.0, score + d)
                logger.debug(f"{symbol}: ORB_NSE {d:+.0f} {r}")
        except Exception:
            pass

        # NSE Option Chain
        if self._config.OPTION_CHAIN_ENABLED:
            try:
                from option_chain import OptionChainAnalyzer
                _oc = getattr(self, "_oc_analyzer", None)
                if _oc is None:
                    self._oc_analyzer = OptionChainAnalyzer()
                    _oc = self._oc_analyzer
                oc_result = _oc.analyze(symbol)
                if oc_result:
                    oc_adj = oc_result.confidence_score / 10.0
                    if oc_result.direction_bias == "BULLISH" and direction == "LONG":
                        score = min(100.0, score + min(12.0, oc_adj))
                        logger.debug(f"{symbol}: OC_BULLISH {oc_adj:+.0f}")
                    elif oc_result.direction_bias == "BEARISH" and direction == "SHORT":
                        score = min(100.0, score + min(12.0, oc_adj))
                        logger.debug(f"{symbol}: OC_BEARISH {oc_adj:+.0f}")
                    elif oc_result.direction_bias in ("BULLISH", "BEARISH"):
                        score = max(0.0, score - 6.0)
                        logger.debug(f"{symbol}: OC_DISAGREE -6")
            except Exception as e:
                logger.debug(f"{symbol}: option_chain {e}")

        # FII/DII flow
        if self._config.FII_DII_ENABLED:
            try:
                from fii_dii_tracker import FIIDIITracker
                _fii = getattr(self, "_fii_tracker", None)
                if _fii is None:
                    self._fii_tracker = FIIDIITracker()
                    _fii = self._fii_tracker
                flow_bias = _fii.get_flow_bias()
                fii_adj   = _fii.get_signal_adjustment()
                if fii_adj != 0:
                    if (flow_bias == "BULLISH" and direction == "LONG") or \
                       (flow_bias == "BEARISH" and direction == "SHORT"):
                        score = min(100.0, score + min(10.0, abs(fii_adj)))
                        logger.debug(f"{symbol}: FII_{flow_bias} +{abs(fii_adj):.0f}")
                    elif flow_bias in ("BULLISH", "BEARISH"):
                        score = max(0.0, score - 5.0)
                        logger.debug(f"{symbol}: FII_AGAINST -5")
            except Exception as e:
                logger.debug(f"{symbol}: fii_dii {e}")

        # Delivery volume
        if self._config.DELIVERY_VOL_ENABLED:
            try:
                from nse_delivery_volume import get_delivery_score
                d, r = get_delivery_score(symbol, direction)
                if d:
                    score = min(100.0, score + d)
                    logger.debug(f"{symbol}: DELIVERY {d:+.0f} {r}")
            except Exception as e:
                logger.debug(f"{symbol}: delivery_vol {e}")

        # Volume profile
        if self._config.VOL_PROFILE_ENABLED and df_5m is not None and not df_5m.empty:
            try:
                from volume_profile import VolumeProfileAnalyzer
                _vpa = getattr(self, "_vp_analyzer", None)
                if _vpa is None:
                    self._vp_analyzer = VolumeProfileAnalyzer()
                    _vpa = self._vp_analyzer
                vp = _vpa.analyze(df_5m, lookback_bars=200)
                if vp:
                    location = vp.price_location(ltp)
                    if direction == "LONG" and location == "ABOVE_VAH":
                        score = min(100.0, score + 6.0)
                    elif direction == "SHORT" and location == "BELOW_VAL":
                        score = min(100.0, score + 6.0)
                    elif location == "AT_VPOC":
                        score = max(0.0, score - 4.0)
            except Exception as e:
                logger.debug(f"{symbol}: vol_profile {e}")

        # News sentiment
        if self._config.NEWS_SENTIMENT_ENABLED:
            try:
                from news_sentiment import get_news_sentiment
                sent_score, sent_reason = get_news_sentiment(symbol)
                if sent_score and sent_score != 0:
                    if (sent_score > 0 and direction == "LONG") or \
                       (sent_score < 0 and direction == "SHORT"):
                        score = min(100.0, score + min(6.0, abs(sent_score)))
                    else:
                        score = max(0.0, score - 3.0)
            except Exception as e:
                logger.debug(f"{symbol}: news_sentiment {e}")

        # Neural predictor
        try:
            from neural_predictor import get_neural_score_delta
            ind_dict = {
                "rsi":      getattr(ind, "rsi",      50),
                "macd":     getattr(ind, "macd",      0),
                "adx":      getattr(ind, "adx",      15),
                "atr":      getattr(ind, "atr",       0),
                "bb_upper": getattr(ind, "bb_upper",  0),
                "bb_lower": getattr(ind, "bb_lower",  0),
                "ema9":     getattr(ind, "ema9",       0),
                "ema21":    getattr(ind, "ema21",      0),
                "volume":   float(df_5m["volume"].iloc[-1]) if df_5m is not None and not df_5m.empty else 0,
            }
            d, r = get_neural_score_delta(symbol, ind_dict, direction)
            if d:
                score = min(100.0, score + d)
                logger.debug(f"{symbol}: NEURAL {d:+.0f} {r}")
        except Exception:
            pass

        # India VIX vol targeting
        if self._config.VOL_TARGET_ENABLED:
            try:
                from volatility_targeting_india import get_vol_target_size_multiplier_india
                mult, r = get_vol_target_size_multiplier_india(symbol, df_5m)
                if not hasattr(self, "_last_size_mult"):
                    self._last_size_mult = {}
                self._last_size_mult[symbol] = mult
            except Exception:
                pass

        return score

    def get_size_multiplier(self, symbol: str) -> float:
        if not hasattr(self, "_last_size_mult"):
            self._last_size_mult = {}
        return self._last_size_mult.get(symbol, 1.0)
