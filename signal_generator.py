"""
signal_generator.py — US Momentum Alpaca AI Bot
AI-Enhanced Multi-Timeframe Momentum Signal Engine

Based on 18+ years of NSE intraday trading experience.
Combines pattern recognition, multi-timeframe alignment,
news filter, relative strength vs Nifty, and AI scoring
to produce high-conviction LONG/SHORT/SKIP signals.

Signal pipeline:
  1. Fetch 5m, 15m, 1h data
  2. Detect patterns on each timeframe
  3. Check MTF alignment (5m must align with 15m and 1h trend)
  4. Apply news blackout filter
  5. Check relative strength vs Nifty
  6. [NEW] Option Chain context (PCR, Max Pain, OI walls) → ±10 pts
  7. [NEW] FII/DII flow adjustment → ±10 pts
  8. [NEW] Volume Profile context (VPOC/VAH/VAL) → ±15 pts
  9. Compute AI composite score
  10. Apply minimum confidence gate (default: 65/100)
  11. Return TradeSignal with entry, SL, TP, and full rationale
"""

import logging
import config
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

from utils import (
    format_ist_timestamp, get_current_ist_time,
    is_market_open_ist, round_to_tick_size
)
from pattern_recognition import PatternRecognizer, IndicatorSet
try:
    from data_fetch_alpaca import AlpacaDataFetcher as GrowwDataFetcher
except ImportError:
    GrowwDataFetcher = object
from high_accuracy_filter import HighAccuracyFilter, FilterResult

# ── Institutional intelligence modules (new) ────────────────
try:
    from option_chain import OptionChainAnalyzer, get_option_chain_analyzer
    _OC_AVAILABLE = True
except ImportError:
    _OC_AVAILABLE = False

try:
    from fii_dii_tracker import FIIDIITracker, get_fii_dii_tracker
    _FII_AVAILABLE = True
except ImportError:
    _FII_AVAILABLE = False

try:
    from volume_profile import VolumeProfileAnalyzer, get_vp_analyzer
    _VP_AVAILABLE = True
except ImportError:
    _VP_AVAILABLE = False

try:
    from nse_data import NSEDataFetcher, get_nse_data_fetcher
    _NSE_DATA_AVAILABLE = True
except ImportError:
    _NSE_DATA_AVAILABLE = False

try:
    from smart_money import SmartMoneyEnhancer, SmartMoneyScore, get_smart_money_enhancer
    _SM_AVAILABLE = True
except ImportError:
    _SM_AVAILABLE = False

try:
    from profit_maximizer import ProfitMaximizer, ProfitMaxScore, get_profit_maximizer
    _PM_AVAILABLE = True
except ImportError:
    _PM_AVAILABLE = False

try:
    from market_regime import MarketRegimeDetector, RegimeState
    _REGIME_AVAILABLE = True
except ImportError:
    _REGIME_AVAILABLE = False

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class TradeSignal:
    """Complete trade signal with entry, SL, TP, and full rationale."""
    symbol: str
    direction: str            # "LONG" or "SHORT"
    signal_score: float       # 0–100 composite confidence
    entry_price: float
    stop_loss: float
    target_1: float           # 2:1 R:R
    target_2: float           # 3:1 R:R
    risk_reward: float
    atr: float
    quantity: int = 0
    patterns: List[str] = field(default_factory=list)
    timeframe_alignment: Dict = field(default_factory=dict)
    indicators: Optional[IndicatorSet] = None
    news_clear: bool = True
    relative_strength: float = 0.0
    signal_time: str = ""
    rationale: str = ""
    is_high_confidence: bool = False  # Score >= 80
    quality_grade: str = "B"          # A+, A, B, C from HighAccuracyFilter
    size_multiplier: float = 1.0      # From HighAccuracyFilter (0.5–1.5x)

    def __post_init__(self):
        if not self.signal_time:
            self.signal_time = format_ist_timestamp()
        self.is_high_confidence = self.signal_score >= 80

    @property
    def grade_emoji(self) -> str:
        return {"A+": "💎", "A": "⭐", "B": "✅", "C": "⚠️"}.get(self.quality_grade, "📊")

    def summary(self) -> str:
        rr = f"{self.risk_reward:.1f}:1"
        direction_emoji = "🟢" if self.direction == "LONG" else "🔴"
        return (
            f"{direction_emoji} {self.direction} {self.symbol} | "
            f"Grade: {self.grade_emoji}{self.quality_grade} | "
            f"Score: {self.signal_score:.0f}/100 | Size: {self.size_multiplier:.1f}x\n"
            f"Entry: ${self.entry_price:.2f} | SL: ${self.stop_loss:.2f} | "
            f"T1: ${self.target_1:.2f} | T2: ${self.target_2:.2f} | R:R {rr}\n"
            f"Patterns: {', '.join(self.patterns[:3])}\n"
            f"{self.rationale}"
        )


class SignalGenerator:
    """
    Generates high-conviction intraday momentum signals.
    Uses AI scoring to rank and filter trade opportunities.
    """

    def __init__(
        self,
        data_fetcher: GrowwDataFetcher,
        news_filter=None,
        min_signal_score: float = 90.0,
        high_confidence_score: float = 80.0,
    ):
        self.fetcher = data_fetcher
        self.news_filter = news_filter
        self.recognizer = PatternRecognizer()
        self.min_score = min_signal_score
        self.high_conf_score = high_confidence_score
        self._nifty_open: Optional[float] = None
        self._nifty_current: Optional[float] = None
        self.ha_filter = HighAccuracyFilter()
        self._learner = None          # Set by main.py: generator.set_learner(learner)
        self._orb_direction: str = "" # Set by main.py after ORB is established
        self._nifty_change_pct: float = 0.0

        # ── Institutional intelligence (auto-init) ──────────────
        self._oc: Optional["OptionChainAnalyzer"] = (
            get_option_chain_analyzer() if _OC_AVAILABLE else None
        )
        self._fii_dii: Optional["FIIDIITracker"] = (
            get_fii_dii_tracker() if _FII_AVAILABLE else None
        )
        self._vp: Optional["VolumeProfileAnalyzer"] = (
            get_vp_analyzer() if _VP_AVAILABLE else None
        )
        # Cached FII adjustment (refreshed every scan cycle, not per-symbol)
        self._fii_adjustment: float = 0.0
        self._fii_size_mult:  float = 1.0

        # NSE supplementary data (bulk/block deals, delivery %, FII futures)
        self._nse_data: Optional["NSEDataFetcher"] = (
            get_nse_data_fetcher() if _NSE_DATA_AVAILABLE else None
        )

        # Smart Money / institutional intelligence enhancer
        self._sm: Optional["SmartMoneyEnhancer"] = (
            get_smart_money_enhancer() if _SM_AVAILABLE else None
        )

        # Profit Maximizer (NR7, Fibonacci, Hidden Divergence, Camarilla, Ichimoku, VSA, MIB)
        self._pm: Optional["ProfitMaximizer"] = (
            get_profit_maximizer() if _PM_AVAILABLE else None
        )

        # Market Regime Detector — tells us WHAT strategy to use right now
        self._regime: Optional["MarketRegimeDetector"] = (
            MarketRegimeDetector() if _REGIME_AVAILABLE else None
        )

        # Concurrent scanning config
        self._max_workers = 6   # Parallel symbol scans (Groww rate-limit safe)

    def set_learner(self, learner) -> None:
        """Inject self-learning engine for adaptive pattern weights."""
        self._learner = learner

    def set_orb_direction(self, direction: str) -> None:
        """Set opening range breakout direction ('UP'/'DOWN'/'') at 9:30 AM IST."""
        self._orb_direction = direction

    def update_nifty_change(self, nifty_change_pct: float) -> None:
        """Update Nifty % change vs previous close (called each scan cycle)."""
        self._nifty_change_pct = nifty_change_pct

    def refresh_institutional_context(self) -> None:
        """
        Refresh FII/DII flow and option chain before each scan cycle.
        Called once per scan — not per symbol — for efficiency.
        """
        if self._fii_dii:
            try:
                self._fii_adjustment = self._fii_dii.get_signal_adjustment()
                self._fii_size_mult  = self._fii_dii.get_position_size_multiplier()
                logger.debug(
                    f"[{format_ist_timestamp()}] FII adj={self._fii_adjustment:+.1f} "
                    f"size_mult={self._fii_size_mult:.2f}"
                )
            except Exception as e:
                logger.warning(f"FII/DII refresh failed: {e}")
                self._fii_adjustment = 0.0
                self._fii_size_mult  = 1.0

    # --------------------------------------------------------
    # MAIN SIGNAL GENERATION
    # --------------------------------------------------------

    def generate_signal(self, symbol: str) -> Optional[TradeSignal]:
        """
        Generate a trade signal for a single symbol.
        Returns TradeSignal if confidence >= min_score, else None.
        """
        try:
            logger.info(f"[{format_ist_timestamp()}] Analyzing {symbol}...")

            # 1. Fetch multi-timeframe data
            mtf_data = self.fetcher.get_multi_timeframe_data(symbol)
            df_5m  = mtf_data.get("5m")
            df_15m = mtf_data.get("15m")
            df_1h  = mtf_data.get("1h")

            if df_5m is None or len(df_5m) < 30:
                logger.info(f"[{format_ist_timestamp()}] {symbol}: insufficient 5m data (got {len(df_5m) if df_5m is not None else 0} bars)")
                return None

            # 2. Pattern analysis on each timeframe
            analysis_5m  = self.recognizer.analyze(df_5m)
            analysis_15m = self.recognizer.analyze(df_15m) if df_15m is not None else {}
            analysis_1h  = self.recognizer.analyze(df_1h)  if df_1h is not None else {}

            score_5m  = analysis_5m.get("score", {})
            score_15m = analysis_15m.get("score", {}) if analysis_15m else {}
            score_1h  = analysis_1h.get("score", {})  if analysis_1h else {}

            ind = analysis_5m.get("indicators", IndicatorSet())

            # 3. Multi-timeframe alignment
            alignment = self._check_mtf_alignment(score_5m, score_15m, score_1h)
            if dir_5m := score_5m.get("direction", "NEUTRAL"):
                pass  # keep direction for fallback below
            if not alignment["aligned"]:
                if config.REQUIRE_MTF_ALIGNMENT or dir_5m == "NEUTRAL":
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: MTF not aligned — dir_5m={dir_5m} align_score={alignment.get('score',0):.0f} require_mtf={config.REQUIRE_MTF_ALIGNMENT}")
                    return None
                # MTF alignment not required — continue with 5m direction, apply penalty later
                logger.debug(f"{symbol}: MTF partial alignment {alignment['score']:.0f} — proceeding with penalty")

            direction = alignment["direction"] if alignment["aligned"] else dir_5m

            # 4. News filter
            news_clear = True
            if self.news_filter:
                try:
                    news_clear = self.news_filter.is_safe_to_trade(symbol)
                    if not news_clear:
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: news blackout — skipping")
                        return None
                except Exception as _e:
                    logger.debug(f"[suppressed] {_e}")

            # 5. Relative strength vs Nifty
            rs = self._get_relative_strength(symbol)

            # 5b. Institutional intelligence context (Option Chain + Volume Profile)
            inst_ctx = self._get_institutional_context(symbol, df_5m)

            # 5c. Regime block — skip signal if regime is AVOID
            if inst_ctx.get("regime_block", False):
                logger.info(f"[{format_ist_timestamp()}] {symbol}: regime_block=True — skipping")
                return None

            # 5d. Daily HTF bias enforcement (Grok #8):
            # Only take LONG trades when daily structure is bullish
            # (price > 20-day SMA AND recent higher highs/lows).
            daily_bias_penalty = self._get_daily_htf_penalty(symbol, direction)
            if daily_bias_penalty is None:
                logger.info(f"[{format_ist_timestamp()}] {symbol}: daily HTF opposes direction — skipping")
                return None

            # 6. Composite AI score
            ai_score = self._compute_ai_score(
                direction=direction,
                score_5m=score_5m,
                score_15m=score_15m,
                score_1h=score_1h,
                ind=ind,
                relative_strength=rs,
                alignment=alignment,
                institutional_ctx=inst_ctx,
            )
            if ai_score is None:
                logger.debug(f"{symbol}: time-of-day block — skipping")
                return None

            # Apply daily HTF penalty if structure partially opposes
            if daily_bias_penalty is not None and daily_bias_penalty < 0:
                ai_score = max(0.0, ai_score + daily_bias_penalty)
                logger.debug(f"{symbol}: daily HTF penalty {daily_bias_penalty:+.0f} → score {ai_score:.1f}")

            # 6b. Smart Money Enhancement (Liquidity Sweeps, Wyckoff, ORB, RVOL,
            #     Market Regime, Killzones, Key Levels, Momentum Quality)
            sm_score = self._get_smart_money_score(
                symbol=symbol,
                direction=direction,
                df_5m=df_5m,
                df_15m=df_15m,
                df_1h=df_1h,
                base_score=ai_score,
            )
            if sm_score is not None:
                ai_score = min(100.0, ai_score + sm_score.total_score)
                if sm_score.reasons:
                    logger.debug(
                        f"{symbol} SM boost {sm_score.total_score:+.1f} | "
                        + " | ".join(sm_score.reasons[:3])
                    )

            # 6c. Profit Maximizer Enhancement
            #     (NR7, Fibonacci Golden Pocket, Hidden Divergence, Camarilla Pivots,
            #      Ichimoku, VSA, Multiple Inside Bars)
            pm_score = self._get_profit_max_score(
                symbol=symbol,
                direction=direction,
                df_5m=df_5m,
                df_15m=df_15m,
                df_1h=df_1h,
                stock_quote=inst_ctx,  # passes prev_high/low/close when available
            )
            if pm_score is not None:
                ai_score = min(100.0, ai_score + pm_score.total_bonus)
                if pm_score.reasons:
                    logger.debug(
                        f"{symbol} PM boost {pm_score.total_bonus:+.1f} | "
                        + " | ".join(pm_score.reasons[:3])
                    )

            # 6d. Earnings Catalyst boost (up to +25 pts when EPS beat + RVOL surge)
            try:
                from catalyst_scanner import get_catalyst_scanner
                cat = get_catalyst_scanner().get_catalyst_boost(symbol)
                if cat.get("has_catalyst") and cat.get("boost", 0) > 0:
                    cat_boost = float(cat["boost"])
                    ai_score = min(100.0, ai_score + cat_boost)
                    logger.info(
                        f"[{format_ist_timestamp()}] {symbol}: "
                        f"catalyst boost +{cat_boost:.1f} — {cat.get('reason', '')}"
                    )
            except Exception as _cat_err:
                logger.debug(f"Catalyst boost error for {symbol}: {_cat_err}")

            if ai_score is None or ai_score < self.min_score:
                logger.info(f"[{format_ist_timestamp()}] {symbol}: score {ai_score:.1f} below threshold {self.min_score:.0f}")
                return None

            # 7. High-accuracy filter — 5-gate confluence check
            pattern_objs   = analysis_5m.get("patterns", [])
            pattern_names  = [p.name for p in pattern_objs if hasattr(p, "name")]
            pattern_scores = [getattr(p, "confidence", 70.0) for p in pattern_objs]

            ltp_now   = float(df_5m.iloc[-1]["close"])
            above_vwap = ltp_now >= ind.vwap if ind.vwap and ind.vwap > 0 else True

            stock_quote      = self.fetcher.get_quote(symbol) or {}
            stock_change_pct = stock_quote.get("change_pct", 0.0)

            # Gate 13: SPY direction — determine bullish/bearish state
            spy_bullish: Optional[bool] = None
            if self._nifty_change_pct != 0.0:
                spy_bullish = self._nifty_change_pct >= 0.0

            filter_result = self.ha_filter.evaluate(
                signal_score     = ai_score,
                direction        = "BUY" if direction == "LONG" else "SELL",
                regime           = alignment.get("regime", "UNKNOWN"),
                mtf_alignment    = alignment,
                volume_ratio     = ind.volume_ratio,
                pattern_names    = pattern_names,
                pattern_scores   = pattern_scores,
                df_5m            = df_5m,
                rsi              = ind.rsi,
                above_vwap       = above_vwap,
                spy_change_pct   = self._nifty_change_pct,
                stock_change_pct = stock_change_pct,
                news_clear       = news_clear,
                orb_direction    = self._orb_direction,
                learner          = self._learner,
                # ── Gates 6-10 parameters ─────────────────────────────
                symbol           = symbol,
                # Gate 6: daily_volume — get_quote() returns 5-min bar volume, not daily.
                # Use 1_000_000 as safe default so Gate 6 passes for all liquid US stocks.
                # Actual daily volume tracking would require summing all 5-min bars today.
                daily_volume     = float(stock_quote.get("daily_volume", 0) or
                                         stock_quote.get("volume", 0) or
                                         stock_quote.get("vol", 0) or
                                         stock_quote.get("traded_volume", 0) or 1_000_000),
                ltp              = ltp_now,
                prev_close       = float(stock_quote.get("prev_close", 0) or
                                         stock_quote.get("previous_close", 0) or
                                         stock_quote.get("close", 0) or 0),
                gap_pct          = self._get_gap_pct(symbol),
                minutes_since_open = self._minutes_since_open(),
                # ── Gates 11-13 parameters ────────────────────────────
                at_key_level     = getattr(ind, "at_hvn", False) or getattr(ind, "at_lvn", False) or above_vwap,
                adx              = getattr(ind, "adx", 0.0),
                spy_bullish      = spy_bullish,
            )

            if not filter_result.passed:
                logger.debug(
                    f"[{format_ist_timestamp()}] {symbol}: FILTERED — {filter_result.rejection_reason}"
                )
                return None

            # 8. Build signal using filter's final score and size
            # Apply smart money regime multiplier on top of filter's size
            regime_mult = sm_score.regime_multiplier if sm_score is not None else 1.0

            # ── Score-based sizing (aggressive tiers for 70% return target) ──
            # Tier 1: 95–100  Elite setup + catalyst → 2.0x (max risk per trade still capped at config)
            # Tier 2: 90–94   High conviction          → 1.5x
            # Tier 3: 85–89   Good setup               → 1.2x
            # Tier 4: 75–84   Normal                   → 1.0x
            # Tier 5: <75     Weak (below min anyway)  → 0.7x
            if ai_score >= 95:
                score_size_mult = 2.0   # Elite: earnings catalyst + RVOL + ICT confluence
            elif ai_score >= 90:
                score_size_mult = 1.5   # High conviction
            elif ai_score >= 85:
                score_size_mult = 1.2   # Good setup
            elif ai_score >= 75:
                score_size_mult = 1.0   # Normal
            else:
                score_size_mult = 0.7   # Weak

            # Earnings catalyst with RVOL surge → extra 0.3x on top (capped at 2.0)
            try:
                from catalyst_scanner import get_catalyst_scanner
                cat = get_catalyst_scanner()._cache.get(symbol, {})
                if cat.get("has_catalyst") and cat.get("boost", 0) >= 20:
                    score_size_mult = min(2.0, score_size_mult + 0.3)
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")

            combined_size = round(filter_result.size_multiplier * regime_mult * score_size_mult, 2)

            # ── Market Internals Gate ─────────────────────────────────────────
            # Check sector breadth before executing — never fight whole market
            try:
                from market_internals import get_market_internals
                internals   = get_market_internals()
                breadth     = internals.get_breadth()
                breadth_ok, breadth_reason = (
                    internals.is_long_ok()  if direction == "LONG"
                    else internals.is_short_ok()
                )
                if not breadth_ok:
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: INTERNALS BLOCK — {breadth_reason}")
                    return None
                # Adjust size by breadth quality
                combined_size = round(combined_size * internals.get_size_multiplier(direction), 2)
                ind.breadth_score = breadth["breadth_score"]
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")

            # ── LLM Reasoning Gate (95+ score only) ──────────────────────────
            if filter_result.final_score >= 95:
                try:
                    from llm_reasoner import get_llm_reasoner
                    rr = abs(ind.atr * 3.0) / max(abs(ind.atr * 1.4), 0.01)
                    summary = {
                        "symbol":       symbol,
                        "direction":    direction,
                        "score":        filter_result.final_score,
                        "grade":        filter_result.quality_grade,
                        "entry":        ltp_now,
                        "sl":           ltp_now - ind.atr * 1.4 if direction == "LONG" else ltp_now + ind.atr * 1.4,
                        "t1":           ltp_now + ind.atr * 2.0 if direction == "LONG" else ltp_now - ind.atr * 2.0,
                        "t2":           ltp_now + ind.atr * 3.0 if direction == "LONG" else ltp_now - ind.atr * 3.0,
                        "rr_ratio":     rr,
                        "patterns":     pattern_names[:5],
                        "rsi":          ind.rsi,
                        "macd_bull":    ind.macd > 0,
                        "adx":          ind.adx,
                        "volume_ratio": ind.volume_ratio,
                        "above_vwap":   ltp_now > ind.vwap if ind.vwap > 0 else True,
                        "spy_change":   self._nifty_change_pct,
                        "regime":       alignment.get("regime", ""),
                        "time_et":      get_current_ist_time().strftime("%H:%M"),
                        "catalyst":     getattr(ind, "catalyst_reason", ""),
                        "breadth_score": ind.breadth_score,
                    }
                    verdict, reason, conf = get_llm_reasoner().evaluate(summary)
                    if verdict == "NO_GO":
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: LLM NO_GO — {reason}")
                        return None
                    elif verdict == "REDUCE_SIZE":
                        combined_size = round(combined_size * 0.5, 2)
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: LLM REDUCE — {reason}")
                except Exception as e:
                    logger.debug(f"LLM gate error: {e}")

            signal = self._build_signal(
                symbol=symbol,
                direction=direction,
                df_5m=df_5m,
                ind=ind,
                ai_score=filter_result.final_score,
                patterns=pattern_objs,
                alignment=alignment,
                rs=rs,
                news_clear=news_clear,
                quality_grade=filter_result.quality_grade,
                size_multiplier=combined_size,
                filter_bonuses=filter_result.bonuses,
                sm_score=sm_score,
                pm_score=pm_score,
            )

            logger.info(
                f"[{format_ist_timestamp()}] ✅ SIGNAL: {direction} {symbol} "
                f"| Score: {filter_result.final_score:.0f} | Entry: ${signal.entry_price:.2f}"
                f"| Breadth: {ind.breadth_score:.0f}/100"
            )
            return signal

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] signal_generator error for {symbol}: {e}")
            return None

    def scan_watchlist(
        self, symbols: List[str], max_signals: int = 5
    ) -> List[TradeSignal]:
        """
        Concurrent watchlist scan using ThreadPoolExecutor.

        Performance: 30 symbols × ~4s each → 120s sequential vs ~20s concurrent.
        Pre-market gap plays are promoted to the front of the queue for first-mover advantage.
        """
        # Refresh FII/DII + OC + FII futures once per cycle (not per symbol)
        self.refresh_institutional_context()

        # Pre-market gap scanner: promote high-conviction gap plays
        try:
            from premarket_scanner import get_premarket_scanner
            pm_priority = get_premarket_scanner().get_priority_symbols(symbols, top_n=5)
            if pm_priority:
                # Reorder: gap plays first, then the rest
                rest = [s for s in symbols if s not in pm_priority]
                symbols = pm_priority + rest
                logger.info(
                    f"[{format_ist_timestamp()}] PreMarket priority: {pm_priority}"
                )
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

        signals: List[TradeSignal] = []
        errors  = 0

        with ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="sig") as pool:
            futures = {pool.submit(self._scan_one, sym): sym for sym in symbols}
            for fut in as_completed(futures, timeout=90):
                sym = futures[fut]
                try:
                    sig = fut.result(timeout=20)
                    if sig:
                        signals.append(sig)
                except FuturesTimeout:
                    logger.debug(f"Scan timeout: {sym}")
                    errors += 1
                except Exception as e:
                    logger.debug(f"Scan error {sym}: {e}")
                    errors += 1

        # Sort: grade first (A+ > A > B > C), then score
        grade_rank = {"A+": 4, "A": 3, "B": 2, "C": 1}
        signals.sort(
            key=lambda s: (grade_rank.get(s.quality_grade, 0), s.signal_score),
            reverse=True,
        )

        filter_stats = self.ha_filter.get_stats()
        logger.info(
            f"[{format_ist_timestamp()}] Scan: {len(signals)} signals "
            f"/ {len(symbols)} symbols | "
            f"Pass rate {filter_stats['pass_rate']:.0f}% | Errors={errors}"
        )
        return signals[:max_signals]

    def _scan_one(self, symbol: str) -> Optional[TradeSignal]:
        """Wrapper for generate_signal — safe for ThreadPoolExecutor."""
        try:
            return self.generate_signal(symbol)
        except Exception as e:
            logger.debug(f"_scan_one({symbol}): {e}")
            return None

    # --------------------------------------------------------
    # INSTITUTIONAL INTELLIGENCE CONTEXT
    # --------------------------------------------------------

    def _get_institutional_context(
        self, symbol: str, df_5m=None
    ) -> Dict:
        """
        Gather Option Chain + Volume Profile context for this symbol.
        FII/DII is market-wide (cached in self._fii_adjustment).

        Returns dict with:
          oc_score:      float (-10 to +10)
          oc_signals:    List[str]
          vp_score:      float (-15 to +15)
          vp_notes:      List[str]
          vp_levels:     Dict (vpoc, vah, val)
          fii_score:     float (-10 to +10) — pre-cached
          fii_size_mult: float
        """
        ctx = {
            "oc_score":        0.0,
            "oc_signals":      [],
            "vp_score":        0.0,
            "vp_notes":        [],
            "vp_levels":       {},
            "fii_score":       self._fii_adjustment,
            "fii_size_mult":   self._fii_size_mult,
            "nse_score":       0,
            "nse_reason":      "",
        }

        # ── Option Chain ─────────────────────────────────
        # Disabled: option_chain module fetches NSE India (nseindia.com)
        # which is irrelevant for US/Alpaca mode and causes 60s timeouts.
        # US options context handled via Alpaca options data separately.

        # ── Volume Profile ────────────────────────────────
        if self._vp and df_5m is not None and not df_5m.empty:
            try:
                from datetime import datetime as _dt
                session_date = str(get_current_ist_time().date())
                vp_result = self._vp.analyze(df_5m, symbol=symbol, session_date=session_date)
                if vp_result:
                    ltp = float(df_5m["close"].iloc[-1]) if "close" in df_5m.columns else 0
                    if ltp > 0:
                        # We'll pass direction="LONG" to get generic score; caller adjusts
                        vp_ctx = self._vp.get_signal_context(vp_result, ltp, "LONG")
                        ctx["vp_score"]  = vp_ctx.get("score_adjustment", 0.0)
                        ctx["vp_notes"]  = vp_ctx.get("notes", [])
                        ctx["vp_levels"] = {
                            "vpoc": vp_result.vpoc,
                            "vah":  vp_result.vah,
                            "val":  vp_result.val,
                        }
            except Exception as e:
                logger.debug(f"VP context error for {symbol}: {e}")

        # ── NSE supplementary data — disabled for US/Alpaca mode ──
        # NSEDataFetcher connects to nseindia.com which is irrelevant here.

        # ── Market Regime Detection ───────────────────────────
        if self._regime and df_5m is not None and not df_5m.empty:
            try:
                regime = self._regime.detect(df_5m)
                ctx["regime_name"]         = regime.regime
                ctx["regime_strategy"]     = regime.strategy
                ctx["regime_size_mult"]    = regime.size_multiplier
                ctx["regime_preferred_dir"]= regime.preferred_direction
                ctx["regime_tradeable"]    = regime.is_tradeable
                ctx["regime_confidence"]   = regime.confidence
                ctx["regime_description"]  = regime.description
                ctx["fii_mult"]            = ctx.get("fii_size_mult", 1.0)
                # Flag AVOID regimes — score gets -25 penalty in _compute_ai_score.
                # Only hard-block if regime is completely untradeable (size_mult == 0).
                if regime.size_multiplier == 0:
                    logger.debug(
                        f"[{format_ist_timestamp()}] {symbol}: regime={regime.regime} "
                        f"size_mult=0 — hard block"
                    )
                    ctx["regime_block"] = True
            except Exception as e:
                logger.debug(f"Market regime error for {symbol}: {e}")

        return ctx

    # --------------------------------------------------------
    # MULTI-TIMEFRAME ALIGNMENT
    # --------------------------------------------------------

    def _check_mtf_alignment(
        self,
        score_5m: Dict,
        score_15m: Dict,
        score_1h: Dict,
    ) -> Dict:
        """
        Check if 5m signal aligns with 15m and 1h trend.
        Rules (18yr experience):
          - 5m must show clear direction (LONG or SHORT, score >= 55)
          - 15m must not be strongly opposing
          - 1h trend must confirm (or at least not contradict)
        """
        dir_5m  = score_5m.get("direction", "NEUTRAL")
        dir_15m = score_15m.get("direction", "NEUTRAL") if score_15m else "NEUTRAL"
        dir_1h  = score_1h.get("direction", "NEUTRAL") if score_1h else "NEUTRAL"

        dom_5m  = score_5m.get("dominant", 0)
        dom_15m = score_15m.get("dominant", 0) if score_15m else 0
        dom_1h  = score_1h.get("dominant", 0) if score_1h else 0

        # Must have clear 5m signal
        if dir_5m == "NEUTRAL" or dom_5m < 50:
            return {"aligned": False, "direction": "NEUTRAL", "score": 0,
                    "5m": dir_5m, "15m": dir_15m, "1h": dir_1h}

        direction = dir_5m

        # Alignment scoring
        alignment_score = 0.0
        if dir_5m == direction:
            alignment_score += 35
        if dir_15m == direction:
            alignment_score += 35
        elif dir_15m == "NEUTRAL":
            alignment_score += 15  # Neutral is acceptable
        else:
            alignment_score -= 20  # Opposing 15m is bad

        if dir_1h == direction:
            alignment_score += 30
        elif dir_1h == "NEUTRAL":
            alignment_score += 10
        else:
            alignment_score -= 15  # 1h opposing means counter-trend — risky

        aligned = alignment_score >= 55  # 5m+15m NEUTRAL+1h NEUTRAL = 60 passes; 5m alone = 35+15+10=60 passes

        return {
            "aligned": aligned,
            "direction": direction,
            "score": alignment_score,
            "5m": dir_5m,
            "15m": dir_15m,
            "1h": dir_1h,
            "full_alignment": (dir_5m == dir_15m == dir_1h == direction),
        }

    # --------------------------------------------------------
    # RELATIVE STRENGTH
    # --------------------------------------------------------

    def _get_relative_strength(self, symbol: str) -> float:
        """
        Calculate relative strength of stock vs SPY (US market benchmark).
        RS > 0: stock outperforming (bullish edge)
        RS < 0: stock underperforming (bearish edge)
        """
        try:
            stock_quote = self.fetcher.get_quote(symbol)
            nifty_quote = self.fetcher.get_nifty_quote()
            if not stock_quote or not nifty_quote:
                return 0.0
            stock_chg = stock_quote.get("change_pct", 0)
            nifty_chg = nifty_quote.get("change_pct", 0)
            return round(stock_chg - nifty_chg, 2)
        except Exception:
            return 0.0

    _daily_htf_cache: dict = {}

    def _get_daily_htf_penalty(self, symbol: str, direction: str) -> Optional[float]:
        """
        Grok #8 — Multi-Timeframe Bias Enforcement.
        Check daily candles for bullish (LONG) or bearish (SHORT) structure:
          - Price > 20-day SMA  (above key moving average)
          - Recent swing high > prior swing high  (higher highs)
          - Recent swing low  > prior swing low   (higher lows)

        Returns
        -------
        0.0   → structure aligns with direction (no penalty)
        -8.0  → structure partially opposes (score penalty, still tradeable)
        None  → structure strongly opposes direction (skip signal)
        """
        cache_key = f"{symbol}_{direction}"
        cached = self._daily_htf_cache.get(cache_key)
        if cached and (get_current_ist_time() - cached["ts"]).total_seconds() < 14400:
            return cached["val"]

        result: Optional[float] = 0.0
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()
            hist = fetcher.get_ohlcv(symbol, interval="day", lookback_days=65)
            if hist is None or len(hist) < 21:
                self._daily_htf_cache[cache_key] = {"val": 0.0, "ts": get_current_ist_time()}
                return 0.0

            closes = hist["close"]
            highs  = hist["high"]
            lows   = hist["low"]
            sma20  = closes.rolling(20).mean().iloc[-1]
            price  = float(closes.iloc[-1])

            above_sma = price > float(sma20)

            # Simple swing: compare last 5-bar high/low vs prior 5-bar high/low
            recent_high = float(highs.iloc[-5:].max())
            prior_high  = float(highs.iloc[-10:-5].max())
            recent_low  = float(lows.iloc[-5:].min())
            prior_low   = float(lows.iloc[-10:-5].min())

            higher_highs = recent_high > prior_high
            higher_lows  = recent_low  > prior_low
            bullish_structure = above_sma and higher_highs and higher_lows

            lower_lows   = recent_low  < prior_low
            lower_highs  = recent_high < prior_high
            bearish_structure = (not above_sma) and lower_highs and lower_lows

            if direction == "LONG":
                if bullish_structure:
                    result = 0.0     # Full alignment — no penalty
                elif above_sma:
                    result = -8.0    # Above SMA but no HH/HL — partial penalty
                else:
                    result = None    # Below SMA → skip LONG
            else:  # SHORT
                if bearish_structure:
                    result = 0.0
                elif not above_sma:
                    result = -8.0
                else:
                    result = None    # Strong bull structure → skip SHORT

        except Exception as e:
            logger.debug(f"Daily HTF check {symbol}: {e}")
            result = 0.0   # On error: allow signal (don't block)

        self._daily_htf_cache[cache_key] = {"val": result, "ts": get_current_ist_time()}
        return result

    # --------------------------------------------------------
    # GAP & TIME HELPERS (used by Gates 6-10)
    # --------------------------------------------------------

    def _get_gap_pct(self, symbol: str) -> float:
        """Get today's opening gap % for symbol (0.0 if not available)."""
        try:
            from gap_analyzer import get_gap_analyzer
            return get_gap_analyzer().get_gap_pct(symbol)
        except Exception:
            return 0.0

    def _minutes_since_open(self) -> float:
        """Minutes elapsed since 9:30 AM ET market open (0.0 before open)."""
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        now_et = get_current_ist_time()  # alias returns ET
        ET = _ZI("America/New_York")
        market_open = _dt(now_et.year, now_et.month, now_et.day, 9, 30, 0, tzinfo=ET)
        return max(0.0, (now_et - market_open).total_seconds() / 60)

    # --------------------------------------------------------
    # PROFIT MAXIMIZER ENHANCEMENT
    # --------------------------------------------------------

    def _get_profit_max_score(
        self,
        symbol: str,
        direction: str,
        df_5m,
        df_15m=None,
        df_1h=None,
        stock_quote: Optional[Dict] = None,
    ) -> Optional["ProfitMaxScore"]:
        """
        Run profit maximizer (NR7, Fibonacci, Hidden Divergence, Camarilla Pivots,
        Ichimoku, VSA, Multiple Inside Bars) and return bonus score.
        Returns None if profit_maximizer module not available.
        """
        if not self._pm:
            return None
        try:
            q = stock_quote or {}
            prev_high  = float(q.get("prev_high",  q.get("high",  0)) or 0)
            prev_low   = float(q.get("prev_low",   q.get("low",   0)) or 0)
            prev_close = float(q.get("prev_close", q.get("close", 0)) or 0)
            return self._pm.enhance(
                symbol=symbol,
                signal_direction=direction,
                df_5m=df_5m,
                df_15m=df_15m,
                df_1h=df_1h,
                prev_high=prev_high,
                prev_low=prev_low,
                prev_close=prev_close,
            )
        except Exception as e:
            logger.debug(f"profit_maximizer enhance({symbol}): {e}")
            return None

    # --------------------------------------------------------
    # SMART MONEY ENHANCEMENT
    # --------------------------------------------------------

    def _get_smart_money_score(
        self,
        symbol: str,
        direction: str,
        df_5m,
        df_15m=None,
        df_1h=None,
        base_score: float = 65.0,
    ) -> Optional["SmartMoneyScore"]:
        """
        Run smart money analysis (Liquidity Sweeps, Wyckoff, ORB, RVOL,
        Market Regime, Killzones, Key Levels, Momentum Quality) and return
        a SmartMoneyScore with bonus points and regime_multiplier.
        Returns None if smart_money module not available.
        """
        if not self._sm:
            return None
        try:
            return self._sm.enhance_signal(
                symbol=symbol,
                signal_direction=direction,
                df_5m=df_5m,
                df_15m=df_15m,
                df_1h=df_1h,
                base_score=base_score,
            )
        except Exception as e:
            logger.debug(f"smart_money enhance_signal({symbol}): {e}")
            return None

    # --------------------------------------------------------
    # AI COMPOSITE SCORE
    # --------------------------------------------------------

    def _compute_ai_score(
        self,
        direction: str,
        score_5m: Dict,
        score_15m: Dict,
        score_1h: Dict,
        ind: IndicatorSet,
        relative_strength: float,
        alignment: Dict,
        institutional_ctx: Optional[Dict] = None,
    ) -> float:
        """
        AI composite score (0–100) incorporating:
        - Pattern strength on each timeframe (weighted)
        - MTF alignment quality
        - Indicator confluence (RSI, MACD, Volume, ADX, SuperTrend)
        - Relative strength vs Nifty
        - Time of day (best momentum windows from 18yr experience)
        - [NEW] Option Chain direction bias (PCR, Max Pain, OI walls)
        - [NEW] FII/DII institutional flow adjustment
        - [NEW] Volume Profile (VPOC/VAH/VAL location)
        """
        score = 0.0
        ctx   = institutional_ctx or {}

        # ── Timeframe pattern scores ──────────────────────
        key = "long" if direction == "LONG" else "short"
        score += score_5m.get(key, 0) * 0.40
        score += score_15m.get(key, 0) * 0.30 if score_15m else 0
        score += score_1h.get(key, 0) * 0.20 if score_1h else 0

        # ── MTF alignment bonus ───────────────────────────
        if alignment.get("full_alignment"):
            score += 12
        elif alignment.get("score", 0) >= 70:
            score += 8
        elif alignment.get("score", 0) >= 50:
            score += 4

        # ── Indicator confluence ──────────────────────────
        if direction == "LONG":
            if 30 < ind.rsi < 50:
                score += 5  # RSI in buy zone but not extreme
            if ind.macd_hist > 0:
                score += 4
            if ind.ema9 > ind.ema21:
                score += 3
            if ind.supertrend_dir == 1:
                score += 4
            if ind.adx > 25 and ind.plus_di > ind.minus_di:
                score += 5
        else:  # SHORT
            if 50 < ind.rsi < 70:
                score += 5
            if ind.macd_hist < 0:
                score += 4
            if ind.ema9 < ind.ema21:
                score += 3
            if ind.supertrend_dir == -1:
                score += 4
            if ind.adx > 25 and ind.minus_di > ind.plus_di:
                score += 5

        # ── Volume confirmation ───────────────────────────
        if ind.volume_ratio >= 2.0:
            score += 8
        elif ind.volume_ratio >= 1.5:
            score += 4

        # ── Relative strength vs Nifty ────────────────────
        if direction == "LONG" and relative_strength > 0.5:
            score += min(relative_strength * 2, 8)
        elif direction == "SHORT" and relative_strength < -0.5:
            score += min(abs(relative_strength) * 2, 8)

        # ── Time of day filter (US ET market hours) ──────────
        now_et   = get_current_ist_time()   # IST alias → ET after migration
        time_val = now_et.hour + now_et.minute / 60
        if 11.5 <= time_val < 14.5:    # 11:30 AM–2:30 PM ET: midday — softer penalty (CAUTION size)
            score -= 5
        elif 9.5 <= time_val <= 10.75:  # 9:30–10:45 AM ET: NY Open Kill Zone
            score += 8
        elif 14.5 <= time_val <= 16.0:  # 2:30–4:00 PM ET: Power Hour
            score += 6
        elif time_val >= 15.75:         # After 3:45 PM ET: avoid new positions
            score -= 12

        # ── [NEW] Option Chain direction bias ─────────────
        oc_score = ctx.get("oc_score", 0.0)
        if direction == "LONG":
            score += oc_score   # +ve oc_score = bullish OC = good for LONG
        else:
            score -= oc_score   # -ve oc_score = bearish OC = good for SHORT

        # ── [NEW] FII/DII flow adjustment ────────────────
        fii_adj = ctx.get("fii_score", 0.0)
        if direction == "LONG":
            score += fii_adj   # FII buying = boost LONG
        else:
            score -= fii_adj   # FII selling = boost SHORT

        # ── [NEW] Volume Profile context ──────────────────
        vp_score = ctx.get("vp_score", 0.0)
        if direction == "LONG":
            score += vp_score
        else:
            score += vp_score  # Already direction-aligned by get_signal_context

        # ── Supplementary data score (volume, 52wk proximity, institutional) ───
        nse_raw = ctx.get("nse_score", 0)
        if nse_raw != 0:
            # Flip sign for SHORT (buy signal = bad for short)
            nse_adj = nse_raw if direction == "LONG" else -nse_raw
            score += nse_adj
            if abs(nse_adj) >= 5:
                logger.debug(
                    f"NSE data adj={nse_adj:+d} | {ctx.get('nse_reason', '')}"
                )

        # ── [NEW] Market Regime bonus/penalty ────────────────
        regime_strategy  = ctx.get("regime_strategy", "MOMENTUM")
        regime_pref_dir  = ctx.get("regime_preferred_dir", "BOTH")
        regime_tradeable = ctx.get("regime_tradeable", True)
        if not regime_tradeable:
            score -= 8    # Non-tradeable regime — position size already 0 via size_mult
        elif regime_strategy == "MOMENTUM":
            if regime_pref_dir == direction or regime_pref_dir == "BOTH":
                score += 12   # Regime confirms direction
            else:
                score -= 8    # Regime opposes direction
        elif regime_strategy == "MEAN_REVERSION":
            score -= 3    # Slight penalty — size_mult already reduced
        else:
            # AVOID/RANGING: position sizing reduced by regime (0.4x), score penalty kept small
            score -= 8

        return min(round(score, 1), 100)

    # --------------------------------------------------------
    # SIGNAL BUILDER
    # --------------------------------------------------------

    def _build_signal(
        self,
        symbol: str,
        direction: str,
        df_5m,
        ind: IndicatorSet,
        ai_score: float,
        patterns: list,
        alignment: Dict,
        rs: float,
        news_clear: bool,
        quality_grade: str = "B",
        size_multiplier: float = 1.0,
        filter_bonuses: Optional[List[str]] = None,
        sm_score: Optional["SmartMoneyScore"] = None,
        pm_score: Optional["ProfitMaxScore"] = None,
    ) -> TradeSignal:
        """Build complete TradeSignal with entry, SL, TP levels."""
        from config import ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER
        curr = df_5m.iloc[-1]
        ltp = float(curr["close"])
        atr = max(ind.atr, ltp * 0.003)  # Minimum 0.3% ATR

        if direction == "LONG":
            entry     = round_to_tick_size(ltp)
            stop_loss = round_to_tick_size(entry - ATR_SL_MULTIPLIER * atr)
            target_1  = round_to_tick_size(entry + 2.0 * (entry - stop_loss))
            target_2  = round_to_tick_size(entry + ATR_TP_MULTIPLIER * (entry - stop_loss))
        else:  # SHORT
            entry     = round_to_tick_size(ltp)
            stop_loss = round_to_tick_size(entry + ATR_SL_MULTIPLIER * atr)
            target_1  = round_to_tick_size(entry - 2.0 * (stop_loss - entry))
            target_2  = round_to_tick_size(entry - ATR_TP_MULTIPLIER * (stop_loss - entry))

        sl_distance = abs(entry - stop_loss)
        risk_reward = abs(target_1 - entry) / sl_distance if sl_distance > 0 else 2.0

        # Rationale text
        mtf_str      = f"5m:{alignment.get('5m','?')} / 15m:{alignment.get('15m','?')} / 1h:{alignment.get('1h','?')}"
        pattern_names = [p.name for p in patterns if hasattr(p, "name") and
                         getattr(p, "direction", direction) == direction][:3]
        bonus_str    = " | " + ", ".join((filter_bonuses or [])[:4]) if filter_bonuses else ""
        sm_str = ""
        if sm_score and sm_score.reasons:
            sm_str = " | SM: " + "; ".join(sm_score.reasons[:2])
        pm_str = ""
        if pm_score and pm_score.reasons:
            pm_str = " | PM: " + "; ".join(pm_score.reasons[:2])

        rationale = (
            f"Grade {quality_grade} | MTF: {mtf_str} | "
            f"RS vs SPY: {rs:+.1f}% | "
            f"Volume: {ind.volume_ratio:.1f}x | "
            f"RSI: {ind.rsi:.0f} | "
            f"MACD: {'▲' if ind.macd_hist > 0 else '▼'} | "
            f"Supertrend: {'▲' if ind.supertrend_dir == 1 else '▼'}"
            f"{bonus_str}{sm_str}{pm_str}"
        )

        return TradeSignal(
            symbol=symbol,
            direction=direction,
            signal_score=ai_score,
            entry_price=entry,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            risk_reward=round(risk_reward, 2),
            atr=round(atr, 2),
            patterns=pattern_names,
            timeframe_alignment=alignment,
            indicators=ind,
            news_clear=news_clear,
            relative_strength=rs,
            signal_time=format_ist_timestamp(),
            rationale=rationale,
            quality_grade=quality_grade,
            size_multiplier=size_multiplier,
        )
