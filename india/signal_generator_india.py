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

# ── Regime names ──────────────────────────────────────────────────────────────
REGIME_BULL_TREND    = "BULL_TREND"
REGIME_BEAR_TREND    = "BEAR_TREND"
REGIME_CHOPPY        = "CHOPPY"
REGIME_HIGH_VOL_FEAR = "HIGH_VOL_FEAR"

# Multipliers per (regime, direction):  {regime: {direction: multiplier}}
_REGIME_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    REGIME_BULL_TREND:    {"LONG": 1.3, "SHORT": 0.7},
    REGIME_BEAR_TREND:    {"SHORT": 1.3, "LONG": 0.7},
    REGIME_CHOPPY:        {"LONG": 0.5, "SHORT": 0.5},
    REGIME_HIGH_VOL_FEAR: {"LONG": 0.3, "SHORT": 0.3},
}


class RegimeSwitcher:
    """
    Detects NSE market regime every 30 minutes using:
      - Nifty50 5-day vs 20-day EMA slope
      - India VIX level  (<15 = calm, 15-22 = normal, >22 = fear)
      - Advance-Decline ratio from NSE breadth (>0.65 = broad bull, <0.35 = broad bear)
      - ADX(14) on Nifty50  (<20 = choppy, >25 = trending)

    Regimes:
      BULL_TREND    — EMA upslope + ADX>25 + breadth >60% advancing
      BEAR_TREND    — EMA downslope + ADX>25 + breadth <40% advancing
      CHOPPY        — ADX<20 (no directional conviction)
      HIGH_VOL_FEAR — VIX>22 regardless of other conditions

    Multipliers applied to signal score before MIN_SIGNAL_SCORE comparison:
      BULL_TREND    → LONG ×1.3 / SHORT ×0.7
      BEAR_TREND    → SHORT ×1.3 / LONG  ×0.7
      CHOPPY        → all ×0.5
      HIGH_VOL_FEAR → all ×0.3

    Fail-open: returns (None, 1.0) on any error so signal pipeline is unaffected.
    All timestamps in IST. All external calls wrapped in try/except.
    """

    _TTL_SECONDS = 1800.0  # refresh every 30 minutes

    def __init__(self):
        self._regime:      Optional[str] = None
        self._multipliers: Dict[str, float] = {"LONG": 1.0, "SHORT": 1.0}
        self._last_update: float = 0.0
        self._last_vix:    float = 0.0
        self._last_adx:    float = 20.0
        self._last_breadth: float = 0.5
        self._last_ema_slope: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def get_regime(self) -> Optional[str]:
        """Return current regime name, refreshing if stale. Fail-open: None."""
        self._maybe_refresh()
        return self._regime

    def get_multiplier(self, direction: str) -> float:
        """
        Return score multiplier for the given direction in the current regime.
        Fail-open: 1.0 (no effect).
        """
        self._maybe_refresh()
        return self._multipliers.get(direction, 1.0)

    def get_regime_and_multiplier(self, direction: str) -> Tuple[Optional[str], float]:
        """Combined getter — returns (regime_name, multiplier)."""
        self._maybe_refresh()
        return self._regime, self._multipliers.get(direction, 1.0)

    # ── Internal refresh logic ─────────────────────────────────────────────────

    def _maybe_refresh(self):
        import time as _time_mod
        if _time_mod.monotonic() - self._last_update < self._TTL_SECONDS:
            return
        try:
            regime = self._compute_regime()
        except Exception as e:
            logger.debug(f"RegimeSwitcher._compute_regime error (fail-open): {e}")
            regime = None

        if regime is None:
            # Fail-open: keep previous regime if available, else no-op multipliers
            if self._regime is None:
                self._multipliers = {"LONG": 1.0, "SHORT": 1.0}
        else:
            self._regime = regime
            self._multipliers = _REGIME_MULTIPLIERS.get(regime, {"LONG": 1.0, "SHORT": 1.0})

        import time as _time_mod2
        self._last_update = _time_mod2.monotonic()
        logger.info(
            f"[IST {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}] "
            f"RegimeSwitcher: regime={self._regime}  "
            f"LONG×{self._multipliers.get('LONG', 1.0):.1f}  "
            f"SHORT×{self._multipliers.get('SHORT', 1.0):.1f}  "
            f"vix={self._last_vix:.1f}  adx={self._last_adx:.1f}  "
            f"breadth={self._last_breadth:.2f}  ema_slope={self._last_ema_slope:.4f}"
        )

    def _compute_regime(self) -> Optional[str]:
        """
        Core regime detection using Nifty50 OHLCV + India VIX + breadth.
        Returns regime name or None on failure.
        All errors are caught so caller stays fail-open.
        """
        import numpy as np
        import pandas as pd

        # ── Step 1: Fetch India VIX (cached 30 min upstream) ─────────────────
        vix = 0.0
        try:
            from data_fetch_upstox import get_india_vix
            vix = float(get_india_vix() or 0.0)
        except Exception as e:
            logger.debug(f"RegimeSwitcher: vix fetch error: {e}")
        self._last_vix = vix

        # HIGH_VOL_FEAR trumps everything — check it first
        if vix > 22.0:
            return REGIME_HIGH_VOL_FEAR

        # ── Step 2: Fetch Nifty50 daily data for EMA slope ───────────────────
        nifty_close: Optional[pd.Series] = None
        try:
            from data_fetch_upstox import get_nifty_daily
            df_nifty = get_nifty_daily(days=30)
            if df_nifty is not None and not df_nifty.empty and len(df_nifty) >= 20:
                nifty_close = df_nifty["close"].astype(float)
        except Exception as e:
            logger.debug(f"RegimeSwitcher: nifty daily fetch error: {e}")

        ema_slope = 0.0
        if nifty_close is not None and len(nifty_close) >= 20:
            try:
                ema5  = float(nifty_close.ewm(span=5,  adjust=False).mean().iloc[-1])
                ema5_prev = float(nifty_close.ewm(span=5, adjust=False).mean().iloc[-2])
                ema20 = float(nifty_close.ewm(span=20, adjust=False).mean().iloc[-1])
                # Slope = (EMA5 - EMA20) / EMA20, positive = uptrend
                ema_slope = (ema5 - ema20) / (ema20 + 1e-9)
            except Exception as e:
                logger.debug(f"RegimeSwitcher: EMA calc error: {e}")
        self._last_ema_slope = ema_slope

        # ── Step 3: Fetch ADX on Nifty50 (5-min intraday) ────────────────────
        adx = 20.0  # default: neutral
        try:
            from data_fetch_upstox import get_nifty_intraday
            df_intra = get_nifty_intraday(interval="5m")
            if df_intra is not None and not df_intra.empty and len(df_intra) >= 20:
                h = df_intra["high"].astype(float)
                l = df_intra["low"].astype(float)
                c = df_intra["close"].astype(float)
                # Compute ADX(14) inline (pure OHLCV)
                up   = h.diff().clip(lower=0)
                down = (-l.diff()).clip(lower=0)
                tr_s = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
                smooth = tr_s.ewm(alpha=1/14, adjust=False).mean().replace(0, 1e-9)
                pdi = 100 * up.ewm(alpha=1/14, adjust=False).mean() / smooth
                ndi = 100 * down.ewm(alpha=1/14, adjust=False).mean() / smooth
                dx  = (100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9))
                adx = float(dx.ewm(alpha=1/14, adjust=False).mean().iloc[-1])
        except Exception as e:
            logger.debug(f"RegimeSwitcher: ADX calc error: {e}")
        self._last_adx = adx

        # ── Step 4: Advance-Decline breadth ratio ─────────────────────────────
        breadth = 0.5  # neutral default
        try:
            from data_fetch_upstox import get_market_breadth
            b = float(get_market_breadth() or 0.5)
            breadth = max(0.0, min(1.0, b))
        except Exception as e:
            logger.debug(f"RegimeSwitcher: breadth fetch error: {e}")
        self._last_breadth = breadth

        # ── Step 5: Regime classification ─────────────────────────────────────
        # CHOPPY: ADX < 20 (no directional conviction regardless of EMA)
        if adx < 20.0:
            return REGIME_CHOPPY

        # TRENDING: ADX >= 25 gives high conviction; 20-25 is borderline
        trending = adx >= 25.0

        # BULL_TREND: EMA upslope + broad market advancing (>60%)
        if ema_slope > 0.001 and breadth > 0.60:
            return REGIME_BULL_TREND

        # BEAR_TREND: EMA downslope + broad market declining (<40%)
        if ema_slope < -0.001 and breadth < 0.40:
            return REGIME_BEAR_TREND

        # Weak trend or mixed signals → CHOPPY (cautious)
        return REGIME_CHOPPY


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
    7 quality gates + 25+ signal sources (Renaissance Round 3 enhancements).
    """

    def __init__(self, config, watchlist: List[str] = None):
        self._config    = config
        self._watchlist = watchlist or []
        self._recognizer = PatternRecognizer()
        self._signals_today: List[IndiaTradeSignal] = []

        # ── RegimeSwitcher (lazy-init, fail-open) ─────────────────────────────
        self._regime_switcher: Optional[RegimeSwitcher] = None
        try:
            if getattr(config, "REGIME_SWITCHER_ENABLED", True):
                self._regime_switcher = RegimeSwitcher()
        except Exception:
            pass

        # ── God Mode modules (lazy, fail-open) ───────────────────────────────
        self._vol_profile   = None
        self._confluence    = None
        self._global_cues   = None   # cached dict from global_cues_india.fetch()
        # Round 2 modules
        self._rs            = None
        self._gap           = None
        self._macro         = None
        try:
            if getattr(config, "VOLUME_PROFILE_GODMODE", False):
                from volume_profile_india import VolumeProfile
                self._vol_profile = VolumeProfile()
        except Exception:
            pass
        try:
            if getattr(config, "SECTOR_CONFLUENCE_ENABLED", False):
                from sector_confluence_india import SectorConfluence
                self._confluence = SectorConfluence()
        except Exception:
            pass
        try:
            if getattr(config, "RS_RANKING_ENABLED", True):
                from relative_strength_india import RelativeStrength
                self._rs = RelativeStrength()
        except Exception:
            pass
        try:
            if getattr(config, "GAP_ANALYSIS_ENABLED", True):
                from gap_analysis_india import GapAnalysis
                self._gap = GapAnalysis()
        except Exception:
            pass
        try:
            if getattr(config, "MACRO_SCORING_ENABLED", True):
                from macro_india import MacroScoring
                self._macro = MacroScoring()
        except Exception:
            pass

    # ── Main signal method ────────────────────────────────────────────────────

    def generate_signal(self, symbol: str,
                        current_price: float = 0.0) -> Optional[IndiaTradeSignal]:
        """
        Full NSE signal pipeline for one symbol.
        Returns IndiaTradeSignal if all gates pass, None otherwise.
        """
        try:
            from data_fetch_upstox import get_ohlcv_multi_tf, get_security_id
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

            # ── Regime multiplier (applied before MIN_SIGNAL_SCORE gate) ───────
            # BULL_TREND: LONG×1.3/SHORT×0.7 | BEAR_TREND: SHORT×1.3/LONG×0.7
            # CHOPPY: all×0.5 | HIGH_VOL_FEAR: all×0.3
            _regime_name: Optional[str] = None
            _regime_mult: float = 1.0
            if self._regime_switcher is not None:
                try:
                    _regime_name, _regime_mult = (
                        self._regime_switcher.get_regime_and_multiplier(direction)
                    )
                    if _regime_mult != 1.0:
                        logger.debug(
                            f"{symbol}: regime={_regime_name} "
                            f"score {score:.1f} × {_regime_mult:.1f} = "
                            f"{score * _regime_mult:.1f}"
                        )
                        score = score * _regime_mult
                except Exception as _re:
                    logger.debug(f"{symbol}: regime_switcher error (fail-open): {_re}")

            if score < self._config.MIN_SIGNAL_SCORE:
                return None

            sl_dist = atr * self._config.ATR_SL_MULTIPLIER
            tp_dist = atr * self._config.ATR_TP_MULTIPLIER
            # T1 at 1R (partial 50% exit), T2 at 2R (runner) — previously both
            # were at 2R, making the partial-exit/runner split a no-op
            t1_dist = sl_dist * getattr(self._config, "ATR_T1_MULTIPLIER", 1.0)

            if direction == "LONG":
                stop_loss = ltp - sl_dist
                target_1  = ltp + t1_dist
                target_2  = ltp + tp_dist
            else:
                stop_loss = ltp + sl_dist
                target_1  = ltp - t1_dist
                target_2  = ltp - tp_dist

            rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

            # ── Institutional boosters ────────────────────────────────────────
            score = self._apply_institutional_boosters(
                symbol, direction, score, df_5m, ltp, ind, current_price
            )

            if score <= -990:  # shock event flag from NLP god mode
                return None

            # ── God Mode boosts ───────────────────────────────────────────────
            now_ist = datetime.now(IST)
            _phase_min_add = 0
            _phase_size_mult = 1.0
            _phase_sl_mult   = 1.0

            # Phase-of-Day
            if getattr(self._config, "PHASE_ENGINE_ENABLED", False):
                try:
                    from phase_of_day_india import get_multipliers
                    pm = get_multipliers(now_ist)
                    score += pm["score_boost"] - pm["score_penalty"]
                    _phase_min_add   = pm["min_score_add"]
                    _phase_size_mult = pm["size_mult"]
                    _phase_sl_mult   = pm["sl_mult"]
                except Exception:
                    pass

            # Volume Profile VPOC
            if self._vol_profile is not None:
                try:
                    vp_profile = self._vol_profile.compute(df_5m)
                    score += self._vol_profile.score_signal(ltp, direction, vp_profile)
                except Exception:
                    pass

            # Global Cues (pre-fetched into self._global_cues by main_india)
            if getattr(self._config, "GLOBAL_CUES_ENABLED", False):
                try:
                    import global_cues_india
                    score += global_cues_india.get_signal_adjustment(
                        direction, self._global_cues
                    )
                except Exception:
                    pass

            # Sector Confluence
            if self._confluence is not None:
                try:
                    from watchlist_india import _SECTOR_MAP
                    score += self._confluence.get_confluence_score(
                        symbol, direction, _SECTOR_MAP
                    )
                except Exception:
                    pass

            # ── Structural stop-loss override ─────────────────────────────────
            if getattr(self._config, "STRUCTURAL_SL_ENABLED", False):
                try:
                    struct_sl = self._structural_sl(df_5m, direction, atr, ltp)
                    if struct_sl > 0:
                        stop_loss = struct_sl
                        sl_dist   = abs(ltp - stop_loss)
                        tp_dist   = atr * self._config.ATR_TP_MULTIPLIER
                        rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else rr
                except Exception:
                    pass

            # Apply phase SL multiplier
            if _phase_sl_mult != 1.0:
                dist = abs(stop_loss - ltp)
                if direction == "LONG":
                    stop_loss = ltp - dist * _phase_sl_mult
                else:
                    stop_loss = ltp + dist * _phase_sl_mult

            # ── Gate 8: Final score threshold ─────────────────────────────────
            _final_min = (
                getattr(self._config, 'IDLE_SCALP_MIN_SCORE', 55.0)
                if _is_scalp_mode
                else self._config.FINAL_EXEC_MIN_SCORE
            )
            _final_min += _phase_min_add  # midday chop adds +8
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
                patterns      = getattr(ind, "_patterns", []),
                quality_grade = quality_grade,
                size_multiplier = size_mult,
                rationale     = (
                    f"Score {score:.0f} | {direction} | RR {rr:.1f} | Grade {quality_grade}"
                    + (f" | REGIME_{_regime_name}×{_regime_mult:.1f}" if _regime_name else "")
                ),
                vix_level     = _vix_now,
            )

            # Apply phase size multiplier
            if _phase_size_mult != 1.0:
                sig.size_multiplier = round(sig.size_multiplier * _phase_size_mult, 3)

            # Update sector confluence cache
            if self._confluence is not None:
                try:
                    self._confluence.update_signal(symbol, direction, score)
                except Exception:
                    pass

            # ── Round 2 God Mode boosters ─────────────────────────────────────
            now_ist2 = datetime.now(IST)

            # Relative Strength (IBD-style RS 1-99)
            if self._rs is not None and getattr(self._config, "RS_RANKING_ENABLED", False):
                try:
                    rs_adj = self._rs.score_signal(symbol, direction)
                    score += rs_adj
                    if rs_adj != 0:
                        sig.rationale += f" | RS{self._rs.get_rs_score(symbol)}:{rs_adj:+d}"
                except Exception:
                    pass

            # Gap Analysis
            if self._gap is not None and getattr(self._config, "GAP_ANALYSIS_ENABLED", False):
                try:
                    gap_info = self._gap.compute_gap(symbol, df_5m)
                    gap_adj  = self._gap.score_signal(gap_info, direction, now_ist2)
                    score += gap_adj
                    if gap_adj != 0:
                        sig.rationale += f" | GAP_{gap_info.get('gap_type','?')}:{gap_adj:+d}"
                except Exception:
                    pass

            # Futures OI Intelligence
            if getattr(self._config, "FUTURES_OI_ENABLED", False):
                try:
                    from futures_oi_india import get_futures_oi_score
                    foi_adj = get_futures_oi_score(symbol, direction)
                    score += foi_adj
                    if foi_adj != 0:
                        sig.rationale += f" | FOI:{foi_adj:+d}"
                except Exception:
                    pass

            # Delivery V2 — 5-day accumulation trend
            if getattr(self._config, "DELIVERY_V2_ENABLED", False):
                try:
                    from nse_delivery_volume import get_delivery_score_v2
                    d_adj, d_reason = get_delivery_score_v2(symbol, direction)
                    if d_adj == 0 and "NOISE" in d_reason:
                        return None   # speculation-only stock — skip
                    score += d_adj
                    if d_adj != 0:
                        sig.rationale += f" | DLV:{d_adj:+.0f}"
                except Exception:
                    pass

            # Unusual Options Activity
            if getattr(self._config, "UOA_ENABLED", False):
                try:
                    from nse_option_chain import get_unusual_options_activity
                    uoa = get_unusual_options_activity(symbol)
                    uoa_adj = uoa.get("score_adj", 0)
                    uoa_sig = uoa.get("signal")
                    if uoa_sig == "WASHOUT":
                        score += uoa_adj
                    elif uoa_sig == "CALL_ACCUMULATION" and direction == "LONG":
                        score += uoa_adj
                    elif uoa_sig == "PUT_ACCUMULATION" and direction == "SHORT":
                        score += uoa_adj
                    elif uoa_sig in ("CALL_ACCUMULATION", "PUT_ACCUMULATION"):
                        score -= 6   # smart money positioned against signal direction
                        uoa_adj = -6
                    if uoa_adj != 0:
                        sig.rationale += f" | UOA_{uoa_sig}:{uoa_adj:+d}"
                except Exception:
                    pass

            # Macro Scoring (blackout + FII MTD)
            if self._macro is not None and getattr(self._config, "MACRO_SCORING_ENABLED", False):
                try:
                    blackout, evt = self._macro.is_blackout(now_ist2)
                    if blackout:
                        logger.debug("%s: MACRO BLACKOUT event=%s — skipped", symbol, evt)
                        return None
                    macro_adj = self._macro.score_signal(direction, now_ist=now_ist2)
                    score += macro_adj
                    if macro_adj != 0:
                        sig.rationale += f" | MACRO:{macro_adj:+d}"
                except Exception:
                    pass

            # ── Round 3 Renaissance Boosters ─────────────────────────────────

            # Wyckoff VSA (Volume Spread Analysis)
            if getattr(self._config, "WYCKOFF_VSA_ENABLED", False):
                try:
                    from wyckoff_vsa_india import get_wyckoff_score
                    w_adj, w_reason = get_wyckoff_score(df_5m, direction)
                    score += w_adj
                    if w_adj != 0:
                        sig.rationale += f" | {w_reason}"
                except Exception:
                    pass

            # Microstructure tape engine
            if getattr(self._config, "MICROSTRUCTURE_ENABLED", False):
                try:
                    from microstructure_india import get_microstructure_score
                    ms_adj, ms_reason = get_microstructure_score(df_5m, direction)
                    score += ms_adj
                    if ms_adj != 0:
                        sig.rationale += f" | {ms_reason}"
                except Exception:
                    pass

            # Cross-asset correlation engine
            if getattr(self._config, "CROSS_ASSET_ENABLED", False):
                try:
                    from cross_asset_india import get_cross_asset_score
                    from watchlist_india import get_sector
                    sector = get_sector(symbol)
                    ca_adj, ca_reason = get_cross_asset_score(direction, sector)
                    score += ca_adj
                    if ca_adj != 0:
                        sig.rationale += f" | CA:{ca_adj:+d}({ca_reason[:20]})"
                except Exception:
                    pass

            # PEAD alpha
            if getattr(self._config, "PEAD_ENABLED", False):
                try:
                    from pead_india import get_pead_score
                    pead_adj, pead_reason = get_pead_score(symbol, direction)
                    score += pead_adj
                    if pead_adj != 0:
                        sig.rationale += f" | {pead_reason}"
                except Exception:
                    pass

            # Block/bulk deal institutional signal
            if getattr(self._config, "BLOCK_DEAL_ENABLED", False):
                try:
                    from block_deal_india import get_block_deal_score
                    bd_adj, bd_reason = get_block_deal_score(symbol, direction)
                    score += bd_adj
                    if bd_adj != 0:
                        sig.rationale += f" | {bd_reason}"
                except Exception:
                    pass

            # Multi-timeframe cascade bonus
            if getattr(self._config, "MTF_CASCADE_ENABLED", False):
                try:
                    aligned_tfs = self._count_aligned_tfs(direction, df_5m, df_15m, df_1h)
                    if aligned_tfs >= 4:
                        mtf_bonus = getattr(self._config, "MTF_FULL_CASCADE_BONUS", 15)
                        score += mtf_bonus
                        sig.rationale += f" | MTF_CASCADE_4TF:{mtf_bonus:+d}"
                    elif aligned_tfs >= 3:
                        mtf_bonus = getattr(self._config, "MTF_THREE_TF_BONUS", 8)
                        score += mtf_bonus
                        sig.rationale += f" | MTF_3TF:{mtf_bonus:+d}"
                    elif aligned_tfs <= 1:
                        mtf_pen = getattr(self._config, "MTF_CONFLICT_PENALTY", -10)
                        score += mtf_pen
                        sig.rationale += f" | MTF_CONFLICT:{mtf_pen:+d}"
                except Exception:
                    pass

            # Elite self-learning pattern tracker
            if getattr(self._config, "ELITE_TRACKER_ENABLED", False):
                try:
                    from elite_tracker_india import get_elite_score
                    _patterns = getattr(ind, "_patterns", []) or sig.patterns
                    elite_adj, elite_reason = get_elite_score(_patterns, direction, score)
                    score += elite_adj
                    if elite_adj != 0:
                        sig.rationale += f" | {elite_reason}"
                except Exception:
                    pass

            # ── Deep Research Additions (Kalman Pairs, VIX Regime, 12-month MOM) ──

            # Kalman Filter Pairs Trading (Renaissance-level cointegration signal)
            if getattr(self._config, "KALMAN_PAIRS_ENABLED", False):
                try:
                    from kalman_pairs_india import get_kalman_pairs_score
                    _dhan = getattr(self, "_dhan_client", None)
                    kp_adj, kp_reason = get_kalman_pairs_score(symbol, direction, _dhan)
                    score += kp_adj
                    if kp_adj != 0:
                        sig.rationale += f" | {kp_reason}"
                except Exception:
                    pass

            # India VIX Regime Signal
            if getattr(self._config, "VIX_REGIME_ENABLED", False):
                try:
                    from vix_regime_india import get_vix_regime_score
                    vix_adj, vix_reason = get_vix_regime_score(direction)
                    score += vix_adj
                    if vix_adj != 0:
                        sig.rationale += f" | {vix_reason}"
                except Exception:
                    pass

            # 12-1 Month Cross-Sectional Momentum Factor (IIM-A: 21.9% annual)
            if getattr(self._config, "MOMENTUM_FACTOR_ENABLED", False):
                try:
                    from momentum_factor_india import get_momentum_factor_score
                    mf_adj, mf_reason = get_momentum_factor_score(symbol, direction)
                    score += mf_adj
                    if mf_adj != 0:
                        sig.rationale += f" | {mf_reason}"
                except Exception:
                    pass

            # ── Goal 70%+ WR: GEX + FII Futures + Change-in-OI PCR ──────────────

            # NIFTY Gamma Exposure regime signal (positive=range, negative=trending)
            if getattr(self._config, "GEX_SIGNAL_ENABLED", False):
                try:
                    from gex_signal_india import get_gex_score
                    _sig_type = "MEAN_REVERSION" if (
                        direction == "LONG" and
                        abs(float(getattr(ind, "vwap_gap_pct", 0) or 0)) < 0.5
                    ) else "MOMENTUM"
                    gex_adj, gex_reason = get_gex_score(direction, _sig_type)
                    score += gex_adj
                    if gex_adj != 0:
                        sig.rationale += f" | {gex_reason}"
                except Exception:
                    pass

            # FII participant-wise index futures positioning (pre-market bias)
            if getattr(self._config, "FII_FUTURES_ENABLED", False):
                try:
                    from fii_futures_india import get_fii_futures_score
                    fii_fut_adj, fii_fut_reason = get_fii_futures_score(direction)
                    score += fii_fut_adj
                    if fii_fut_adj != 0:
                        sig.rationale += f" | {fii_fut_reason}"
                except Exception:
                    pass

            # Change-in-OI PCR (more sensitive than total-OI PCR)
            if getattr(self._config, "CHNG_OI_PCR_ENABLED", False):
                try:
                    from nse_option_chain import get_chng_pcr_score
                    chng_pcr_adj, chng_pcr_reason = get_chng_pcr_score(symbol, direction)
                    score += chng_pcr_adj
                    if chng_pcr_adj != 0:
                        sig.rationale += f" | {chng_pcr_reason}"
                except Exception:
                    pass

            # GEX dealer flow inference (rate-of-change of GEX = buy/sell pressure)
            if getattr(self._config, "GEX_SIGNAL_ENABLED", False):
                try:
                    from gex_signal_india import get_dealer_flow_signal
                    _df_adj, _df_r = get_dealer_flow_signal(direction)
                    score += _df_adj
                    if _df_adj != 0:
                        sig.rationale += f" | {_df_r}"
                except Exception:
                    pass

            # IV term structure (near vs far expiry IV comparison)
            if getattr(self._config, "GEX_SIGNAL_ENABLED", False):
                try:
                    from gex_signal_india import get_iv_term_structure_score
                    _ivts_adj, _ivts_r = get_iv_term_structure_score(direction)
                    score += _ivts_adj
                    if _ivts_adj != 0:
                        sig.rationale += f" | {_ivts_r}"
                except Exception:
                    pass

            # ── Signal Decay (freshness gate) ──────────────────────────────────
            if getattr(self._config, "SIGNAL_DECAY_ENABLED", True):
                try:
                    from signal_decay_india import apply_decay, record_signal_start
                    record_signal_start(symbol, direction)
                    _vix_now = float(getattr(self, "_last_india_vix", 15.0))
                    score, _decay_r = apply_decay(score, symbol, direction, _vix_now)
                    if _decay_r:
                        sig.rationale += f" | {_decay_r}"
                except Exception:
                    pass

            # ── Regime-Conditional Strategy Multipliers ─────────────────────────
            if getattr(self._config, "REGIME_SELECTOR_ENABLED", True):
                try:
                    from regime_strategy_selector import get_regime_score_adj, get_day_of_week_adj
                    _adx_v = float(getattr(ind, "adx", 20.0) or 20.0)
                    _vix_v = float(getattr(self, "_last_india_vix", 15.0))
                    _brd_v = float(getattr(self, "_breadth_pct", 0.5))
                    _srt = ("REVERSION" if ("FADE" in sig.rationale or "RETEST" in sig.rationale)
                            else "MOMENTUM")
                    _rg_adj, _rg_r = get_regime_score_adj(_srt, _adx_v, _vix_v, _brd_v)
                    score += _rg_adj
                    if _rg_adj != 0:
                        sig.rationale += f" | {_rg_r}"
                    _dow_adj, _dow_r = get_day_of_week_adj()
                    score += _dow_adj
                    if _dow_adj != 0:
                        sig.rationale += f" | {_dow_r}"
                except Exception:
                    pass

            # Update final score on signal
            sig.signal_score = round(score, 1)
            sig.is_high_confidence = score >= 80

            # Re-check threshold: all boosters (Rounds 2+3) must be able to reject
            if score < _final_min:
                logger.debug(f"{symbol}: score {score:.1f} fell below "
                             f"{_final_min} after Round 3 adjustments")
                return None

            # Re-grade with final score (cross all grade bands after all boosters)
            if score >= grand_slam:
                sig.quality_grade, _base_mult = "A+", 1.35
            elif score >= 78:
                sig.quality_grade, _base_mult = "A", 1.00
            elif score >= 72:
                sig.quality_grade, _base_mult = "B+", 0.80
            else:
                sig.quality_grade, _base_mult = "B", 0.65
            sig.size_multiplier = round(_base_mult * _phase_size_mult, 3)

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
                    return None

            return sig

        except Exception as e:
            logger.debug(f"generate_signal {symbol}: {e}")
            return None

    # ── Multi-timeframe alignment counter ────────────────────────────────────

    def _count_aligned_tfs(self, direction: str,
                           df_5m: Optional[pd.DataFrame],
                           df_15m: Optional[pd.DataFrame],
                           df_1h: Optional[pd.DataFrame]) -> int:
        """
        Count how many timeframes (1m, 5m, 15m, 1h) align with direction.
        Returns 0-4 aligned count.
        """
        aligned = 0
        try:
            def _ema_direction(df: pd.DataFrame) -> Optional[str]:
                if df is None or len(df) < 21:
                    return None
                close = df["close"].values.astype(float)
                ema9  = _ema_calc(close, 9)
                ema21 = _ema_calc(close, 21)
                if ema9[-1] > ema21[-1]:
                    return "LONG"
                elif ema9[-1] < ema21[-1]:
                    return "SHORT"
                return None

            def _ema_calc(prices, period):
                alpha = 2.0 / (period + 1)
                ema = [prices[0]]
                for p in prices[1:]:
                    ema.append(alpha * p + (1 - alpha) * ema[-1])
                return ema

            # 5m
            if df_5m is not None and len(df_5m) >= 21:
                if _ema_direction(df_5m) == direction:
                    aligned += 1

            # 15m
            if df_15m is not None and len(df_15m) >= 21:
                if _ema_direction(df_15m) == direction:
                    aligned += 1

            # 1h
            if df_1h is not None and len(df_1h) >= 21:
                if _ema_direction(df_1h) == direction:
                    aligned += 1

            # 1m (try to get it from data_fetch_upstox)
            try:
                from data_fetch_upstox import get_ohlcv
                df_1m = get_ohlcv(df_5m.index[0] if hasattr(df_5m, 'index') else None,
                                  interval="1m") if df_5m is not None else None
                if df_1m is not None and len(df_1m) >= 10:
                    close_1m = df_1m["close"].values.astype(float)
                    if len(close_1m) >= 3:
                        if direction == "LONG" and close_1m[-1] > close_1m[-2] > close_1m[-3]:
                            aligned += 1
                        elif direction == "SHORT" and close_1m[-1] < close_1m[-2] < close_1m[-3]:
                            aligned += 1
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"_count_aligned_tfs: {e}")

        return aligned

    # ── Structural stop-loss ─────────────────────────────────────────────────

    def _structural_sl(self, df: pd.DataFrame, direction: str,
                       atr: float, entry: float) -> float:
        """
        LONG: SL = last swing low in last 10 bars, floored at entry - 1.0*ATR.
        SHORT: SL = last swing high in last 10 bars, capped at entry + 1.0*ATR.
        Falls back to 1.5*ATR if no swing point found.
        """
        try:
            window = df.tail(12)
            if len(window) < 3:
                raise ValueError("not enough bars")

            if direction == "LONG":
                swing_lows = []
                for i in range(1, len(window) - 1):
                    lo = float(window["low"].iloc[i])
                    if lo < float(window["low"].iloc[i - 1]) and lo < float(window["low"].iloc[i + 1]):
                        swing_lows.append(lo)
                atr_floor = entry - 1.0 * atr
                if swing_lows:
                    swing_sl = max(swing_lows)   # highest swing low = tightest valid stop
                    return max(swing_sl, atr_floor)
                return atr_floor

            else:  # SHORT
                swing_highs = []
                for i in range(1, len(window) - 1):
                    hi = float(window["high"].iloc[i])
                    if hi > float(window["high"].iloc[i - 1]) and hi > float(window["high"].iloc[i + 1]):
                        swing_highs.append(hi)
                atr_ceil = entry + 1.0 * atr
                if swing_highs:
                    swing_sl = min(swing_highs)  # lowest swing high = tightest valid stop
                    return min(swing_sl, atr_ceil)
                return atr_ceil

        except Exception:
            fallback = entry - 1.5 * atr if direction == "LONG" else entry + 1.5 * atr
            return fallback

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
                from data_fetch_upstox import get_india_vix as _get_vix
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
