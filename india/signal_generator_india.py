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
from datetime import datetime, timedelta
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
    time_stop_min: int          = 0      # for scalp mode: auto-close after N minutes
    is_scalp:     bool          = False  # flagged for idle scalp mode
    vix_level:    float         = 0.0   # India VIX at signal time

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
            self._king_setup = False   # reset KingEdge confluence flag per signal
            bars = get_ohlcv_multi_tf(symbol)
            df_5m  = bars.get("5m")
            df_15m = bars.get("15m")
            df_1h  = bars.get("1h")

            if df_5m is None or df_5m.empty or len(df_5m) < 20:
                return None

            ltp = current_price or float(df_5m["close"].iloc[-1])
            if ltp <= 0:
                return None

            # PatternRecognizer has no compute_indicators() — that phantom call
            # raised AttributeError on EVERY symbol, was swallowed by the except
            # below, and returned None → 0 trades, always. Use the real API:
            # compute() adds indicator columns, get_latest_indicators() reads them.
            df_5m = self._recognizer.indicators.compute(df_5m)
            ind = self._recognizer.indicators.get_latest_indicators(df_5m)
            if ind is None:
                return None

            # ── Check idle scalp mode ──────────────────────────────────────────
            _is_scalp_mode = False
            try:
                _cfg_scalp = getattr(self._config, 'IDLE_SCALP_ENABLED', False)
                if _cfg_scalp and hasattr(self, '_idle_scalp_active') and self._idle_scalp_active:
                    _is_scalp_mode = True
            except Exception:
                pass

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

            # ── Gate 7: India VIX gate (don't trade in panic markets) ─────────
            if not self._check_india_vix(direction):
                logger.debug(f"{symbol}: India VIX too high — skipped")
                return None

            # Base score — pass df_1h for 1h alignment scoring
            score = self._compute_base_score(ind, direction, df_5m, df_1h=df_1h)
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

            if score <= -990:  # shock event flag from NLP god mode
                return None

            # ── Gate 8: Final score threshold ─────────────────────────────────
            _final_min = (
                getattr(self._config, 'IDLE_SCALP_MIN_SCORE', 55.0)
                if _is_scalp_mode
                else self._config.FINAL_EXEC_MIN_SCORE
            )
            if score < _final_min:
                logger.debug(f"{symbol}: final score {score:.1f} < {_final_min}")
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

            # KingEdge confluence = the only edge that survived realistic costs
            # in OOS research (61.5% WR, PF 1.32). Promote to top conviction.
            _is_king = getattr(self, "_king_setup", False)
            if _is_king:
                quality_grade = "A+"
                size_mult     = max(size_mult, 1.35)

            security_id = get_security_id(symbol) or ""
            _vix_now = getattr(self, '_last_india_vix', 0.0)

            sig = IndiaTradeSignal(
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
                patterns      = (["👑 KingEdge"] + list(getattr(ind, "_patterns", []))) if _is_king else getattr(ind, "_patterns", []),
                quality_grade = quality_grade,
                size_multiplier = size_mult,
                rationale     = (f"👑 KINGEDGE confluence | " if _is_king else "") + f"Score {score:.0f} | {direction} | RR {rr:.1f} | Grade {quality_grade}",
                vix_level     = _vix_now,
            )

            # ── Idle scalp mode adjustments ───────────────────────────────────
            if _is_scalp_mode:
                _scalp_min = getattr(self._config, 'IDLE_SCALP_MIN_SCORE', 55.0)
                if score >= _scalp_min:
                    sig.time_stop_min = getattr(self._config, 'IDLE_SCALP_TIME_STOP_MIN', 10)
                    sig.is_scalp      = True
                    sig.size_multiplier = sig.size_multiplier * getattr(
                        self._config, 'IDLE_SCALP_SIZE_MULT', 0.40
                    )
                else:
                    # Score too low even for scalp mode threshold
                    return None

            return sig

        except (AttributeError, TypeError) as e:
            # Code bugs (phantom method, wrong args) must be LOUD — this exact
            # silent-swallow is what hid the compute_indicators 0-trades bug.
            logger.error(f"generate_signal {symbol} CODE ERROR: {e}", exc_info=True)
            return None
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

            # Stochastic momentum confirmer
            stoch_k = getattr(ind, "stoch_k", None)
            stoch_d = getattr(ind, "stoch_d", None)
            if stoch_k is not None and stoch_d is not None:
                sk = float(stoch_k); sd = float(stoch_d)
                if sk < 80 and sk > sd:   # not overbought + bullish cross
                    confirm_long  += 1
                if sk > 20 and sk < sd:   # not oversold + bearish cross
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

    # ── Gate 7: India VIX gate ────────────────────────────────────────────────

    def _check_india_vix(self, direction: str) -> bool:
        """
        Fetch ^INDIAVIX via yfinance (cached 30 min).
        VIX >= 28.0 (EXTREME): block all signals.
        VIX >= 22.0 (HIGH): allow but flag for size reduction.
        Stores self._last_india_vix for downstream sizing.
        Fail-open: returns True on any error.
        """
        try:
            now = datetime.now(IST)
            cache_valid = (
                hasattr(self, "_vix_cache_value")
                and hasattr(self, "_vix_cache_time")
                and (now - self._vix_cache_time) < timedelta(minutes=30)
            )
            if cache_valid:
                vix = self._vix_cache_value
            else:
                from data_fetch_dhan import get_india_vix as _get_vix
                vix = _get_vix()
                if vix <= 0:
                    self._last_india_vix = 0.0
                    self._vix_size_mult = 1.0
                    return True
                self._vix_cache_value = vix
                self._vix_cache_time = now

            self._last_india_vix = vix

            # Determine size multiplier based on VIX level
            if vix < 12.0:
                self._vix_size_mult = 1.1   # low volatility — good for momentum
            elif vix < 22.0:
                self._vix_size_mult = 1.0
            elif vix < 28.0:
                self._vix_size_mult = 0.7   # high VIX — reduce size
            else:
                self._vix_size_mult = 0.0   # blocked — never reached (gate filters)

            if vix >= 28.0:
                logger.debug(f"India VIX {vix:.1f} >= 28.0 EXTREME — all signals blocked")
                return False   # EXTREME: block all

            # VIX >= 22.0: allow but flag for size reduction (handled in boosters)
            return True

        except Exception as e:
            logger.debug(f"_check_india_vix error (fail-open): {e}")
            self._last_india_vix = 0.0
            self._vix_size_mult = 1.0
            return True

    # ── Base score ────────────────────────────────────────────────────────────

    def _compute_base_score(self, ind: IndicatorSet, direction: str,
                            df: pd.DataFrame, df_1h: Optional[pd.DataFrame] = None) -> float:
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

            # 20-bar breakout with volume — the highest-Sharpe validated edge
            # (OOS 2020-2026: breakout Sharpe 1.00, PF 1.59, WR 51%). Reward a
            # close that takes out the prior 20-bar high/low on above-avg volume.
            if len(df) >= 21 and "volume" in df.columns:
                try:
                    prior = df.iloc[-21:-1]   # exclude current bar
                    hh20 = float(prior["high"].max())
                    ll20 = float(prior["low"].min())
                    vol_avg_bo = prior["volume"].mean()
                    vol_now_bo = float(df["volume"].iloc[-1])
                    vol_ok = vol_avg_bo > 0 and vol_now_bo >= 1.5 * vol_avg_bo
                    if direction == "LONG" and close >= hh20 and vol_ok:
                        score += 10
                        logger.debug("breakout: 20-bar high + volume +10")
                    elif direction == "SHORT" and close <= ll20 and vol_ok:
                        score += 10
                        logger.debug("breakdown: 20-bar low + volume +10")

                    # ── KINGEDGE: full-confluence high-accuracy setup ──────────
                    # The ONLY edge that survived realistic costs in OOS research
                    # (US: 61.5% WR, PF 1.32, 2.4% MDD). Requires ALL of:
                    #   EMA9>EMA21>EMA50 stack | ADX>=25 | 20-bar breakout |
                    #   2x volume | RSI in trend-not-exhausted band.
                    # When it fires, this is the top-conviction trade -> big boost.
                    try:
                        e9  = float(getattr(ind, "ema9",  0) or 0)
                        e21 = float(getattr(ind, "ema21", 0) or 0)
                        e50 = float(getattr(ind, "ema50", 0) or 0)
                        adx = float(getattr(ind, "adx",   0) or 0)
                        rsi = float(getattr(ind, "rsi",  50) or 50)
                        vol_2x = vol_avg_bo > 0 and vol_now_bo >= 2.0 * vol_avg_bo
                        if direction == "LONG":
                            king = (e9 > e21 > e50 > 0 and adx >= 25
                                    and close >= hh20 and vol_2x
                                    and 50 <= rsi < 72 and close > e50)
                        else:
                            king = (0 < e9 < e21 < e50 and adx >= 25
                                    and close <= ll20 and vol_2x
                                    and 28 < rsi <= 50 and close < e50)
                        if king:
                            score += 15
                            self._king_setup = True
                            logger.info(f"👑 KINGEDGE confluence {direction} "
                                        f"(ADX={adx:.0f} RSI={rsi:.0f}) +15")
                    except Exception as _ke:
                        logger.debug(f"kingedge score error: {_ke}")
                except Exception as _bo:
                    logger.debug(f"breakout score error: {_bo}")

            # Engulfing candle detection (last 2 bars)
            if len(df) >= 2:
                prev = df.iloc[-2]
                curr = df.iloc[-1]
                prev_op = float(prev["open"]); prev_cl = float(prev["close"])
                curr_op = float(curr["open"]); curr_cl = float(curr["close"])
                prev_body = abs(prev_cl - prev_op)
                curr_body = abs(curr_cl - curr_op)
                if prev_body > 1e-9 and curr_body > prev_body * 1.2:
                    prev_bearish = prev_cl < prev_op
                    curr_bullish = curr_cl > curr_op
                    prev_bullish = prev_cl > prev_op
                    curr_bearish = curr_cl < curr_op
                    if direction == "LONG" and prev_bearish and curr_bullish:
                        score += 7   # bullish engulfing
                        logger.debug("engulfing: bullish +7")
                    elif direction == "SHORT" and prev_bullish and curr_bearish:
                        score += 7   # bearish engulfing
                        logger.debug("engulfing: bearish +7")

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

            # 1h trend alignment (+8 if aligned, -4 if conflicting)
            if df_1h is not None and not df_1h.empty and len(df_1h) >= 21:
                try:
                    ema9_1h  = float(df_1h["close"].ewm(span=9,  adjust=False).mean().iloc[-1])
                    ema21_1h = float(df_1h["close"].ewm(span=21, adjust=False).mean().iloc[-1])
                    gap_1h   = (ema9_1h - ema21_1h) / (ema21_1h + 1e-9)
                    if direction == "LONG":
                        if gap_1h >= 0.001:    # EMA9 > EMA21 by 0.1%+
                            score += 8
                            logger.debug("1h EMA aligned LONG +8")
                        elif gap_1h <= -0.001:  # 1h conflicts
                            score -= 4
                            logger.debug("1h EMA conflicts LONG -4")
                    else:  # SHORT
                        if gap_1h <= -0.001:   # EMA9 < EMA21 by 0.1%+
                            score += 8
                            logger.debug("1h EMA aligned SHORT +8")
                        elif gap_1h >= 0.001:   # 1h conflicts
                            score -= 4
                            logger.debug("1h EMA conflicts SHORT -4")
                except Exception as _e1h:
                    logger.debug(f"1h scoring error: {_e1h}")

        except Exception as e:
            logger.debug(f"base_score error: {e}")

        return min(score, 100.0)

    # ── Institutional boosters ────────────────────────────────────────────────

    def _apply_institutional_boosters(self, symbol: str, direction: str,
                                      score: float, df_5m: pd.DataFrame,
                                      ltp: float, ind: IndicatorSet,
                                      current_price: float) -> float:
        # India VIX size adjustment — dampen score in high-VIX environments
        _vix = getattr(self, '_last_india_vix', 0.0)
        if _vix >= 22.0:
            score = score * 0.85   # don't boost to false confidence in high-VIX
            logger.debug(f"{symbol}: VIX {_vix:.1f} >= 22 — score dampened to {score:.1f}")

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

        # NSE Sector rotation momentum
        if getattr(self._config, 'SECTOR_ROTATION_ENABLED', True):
            try:
                from nse_sector_momentum import get_sector_momentum_score
                _sm_delta, _sm_reason = get_sector_momentum_score(symbol, direction)
                if _sm_delta:
                    score = min(100.0, score + _sm_delta)
                    logger.debug(f"{symbol}: SECTOR_MOMENTUM {_sm_delta:+.0f} {_sm_reason}")
            except Exception:
                pass

        # NSE Option Chain God Mode
        if getattr(self._config, 'OPTION_CHAIN_GODMODE', True):
            try:
                from nse_option_chain import get_option_chain_score
                _oc_d, _oc_r = get_option_chain_score(symbol, direction, ltp)
                if _oc_d:
                    score = min(100.0, score + _oc_d)
                    logger.debug(f"{symbol}: OC_GODMODE {_oc_d:+.0f} {_oc_r}")
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

        # FII/DII India intraday flow (new module)
        if getattr(self._config, 'FII_DII_ENABLED', True):
            try:
                from fii_dii_india import get_fii_dii_score as _fii_india_score
                _fii_d, _fii_r = _fii_india_score(symbol, direction)
                if _fii_d:
                    score = min(100.0, score + _fii_d)
                    logger.debug(f"{symbol}: FII_DII_INDIA {_fii_d:+.0f} {_fii_r}")
            except Exception:
                pass

        # FII/DII India Live (God Mode)
        if getattr(self._config, 'FII_DII_ENABLED', True):
            try:
                from fii_dii_india import get_fii_dii_score as _fii_live
                _fd_d, _fd_r = _fii_live(symbol, direction)
                if _fd_d:
                    score = min(100.0, score + _fd_d)
                    logger.debug(f"{symbol}: FII_DII_LIVE {_fd_d:+.0f} {_fd_r}")
            except Exception:
                pass

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

        # News NLP God Mode
        if getattr(self._config, 'NEWS_NLP_GODMODE', True):
            try:
                from nse_news_sentiment import get_news_sentiment_score, is_shock_event_active
                _shock, _shock_r = is_shock_event_active()
                if _shock:
                    logger.info(f"{symbol}: shock event {_shock_r} — skipping")
                    return -999.0  # caller checks for this
                _ns_d, _ns_r = get_news_sentiment_score(symbol, direction)
                if _ns_d:
                    score = min(100.0, score + _ns_d)
                    logger.debug(f"{symbol}: NEWS_NLP {_ns_d:+.0f} {_ns_r}")
            except Exception as _nse:
                logger.debug(f"[suppressed] nse_news: {_nse}")

        # Corporate Events
        if getattr(self._config, 'CORP_EVENTS_ENABLED', True):
            try:
                from corporate_events_india import should_avoid_trading, get_event_score_modifier, get_bulk_deal_signal
                _avoid, _ar = should_avoid_trading(symbol)
                if _avoid:
                    logger.debug(f"{symbol}: corp event avoid — {_ar}")
                    return score  # return current score unchanged
                _ev_d, _ev_r = get_event_score_modifier(symbol, direction)
                if _ev_d:
                    score = min(100.0, score + _ev_d)
                _bd_d, _bd_r = get_bulk_deal_signal(symbol, direction)
                if _bd_d:
                    score = min(100.0, score + _bd_d)
            except Exception:
                pass

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

        # Regime alignment + ML Ensemble (God Mode)

        # Regime size multiplier
        try:
            from regime_classifier_india import get_regime_size_multiplier, is_direction_aligned_with_regime
            if not is_direction_aligned_with_regime(direction):
                score = score * 0.80  # penalize regime-opposing trades
            _rsz = get_regime_size_multiplier()
            if not hasattr(self, '_regime_size_mult'):
                self._regime_size_mult = {}
            self._regime_size_mult[symbol] = _rsz
        except Exception:
            pass

        # ML Ensemble
        if getattr(self._config, 'ML_ENSEMBLE_ENABLED', True):
            try:
                from ml_signal_india import get_ml_score_delta
                _wins   = getattr(self, '_session_wins', 0)
                _losses = getattr(self, '_session_losses', 0)
                _ml_d, _ml_r = get_ml_score_delta(symbol, ind, df_5m, direction, _wins, _losses)
                if _ml_d:
                    score = min(100.0, score + _ml_d)
                    logger.debug(f"{symbol}: ML_GODMODE {_ml_d:+.0f} {_ml_r}")
            except Exception as _mle:
                logger.debug(f"[suppressed] ml_signal_india: {_mle}")

        return score

    def get_size_multiplier(self, symbol: str) -> float:
        if not hasattr(self, "_last_size_mult"):
            self._last_size_mult = {}
        return self._last_size_mult.get(symbol, 1.0)
