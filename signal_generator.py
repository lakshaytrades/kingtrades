"""
signal_generator.py — NSE Momentum Groww AI Bot
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
from data_fetch_groww import GrowwDataFetcher
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
            f"Entry: ₹{self.entry_price:.2f} | SL: ₹{self.stop_loss:.2f} | "
            f"T1: ₹{self.target_1:.2f} | T2: ₹{self.target_2:.2f} | R:R {rr}\n"
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
        min_signal_score: float = 65.0,
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
                logger.debug(f"{symbol}: insufficient 5m data")
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
            if not alignment["aligned"]:
                logger.debug(f"{symbol}: MTF not aligned — skipping")
                return None

            direction = alignment["direction"]

            # 4. News filter
            news_clear = True
            if self.news_filter:
                try:
                    news_clear = self.news_filter.is_safe_to_trade(symbol)
                    if not news_clear:
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: news blackout — skipping")
                        return None
                except Exception:
                    pass

            # 5. Relative strength vs Nifty
            rs = self._get_relative_strength(symbol)

            # 5b. Institutional intelligence context (Option Chain + Volume Profile)
            inst_ctx = self._get_institutional_context(symbol, df_5m)

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

            if ai_score < self.min_score:
                logger.debug(f"{symbol}: score {ai_score:.1f} below threshold {self.min_score}")
                return None

            # 7. High-accuracy filter — 5-gate confluence check
            pattern_objs   = analysis_5m.get("patterns", [])
            pattern_names  = [p.name for p in pattern_objs if hasattr(p, "name")]
            pattern_scores = [getattr(p, "confidence", 70.0) for p in pattern_objs]

            ltp_now   = float(df_5m.iloc[-1]["close"])
            above_vwap = ltp_now >= ind.vwap if ind.vwap and ind.vwap > 0 else True

            stock_quote      = self.fetcher.get_quote(symbol) or {}
            stock_change_pct = stock_quote.get("change_pct", 0.0)

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
                nifty_change_pct = self._nifty_change_pct,
                stock_change_pct = stock_change_pct,
                news_clear       = news_clear,
                orb_direction    = self._orb_direction,
                learner          = self._learner,
                # ── Gates 6-10 parameters ─────────────────────────────
                symbol           = symbol,
                daily_volume     = float(stock_quote.get("volume", 0) or
                                         stock_quote.get("vol", 0) or
                                         stock_quote.get("traded_volume", 0) or 0),
                ltp              = ltp_now,
                prev_close       = float(stock_quote.get("prev_close", 0) or
                                         stock_quote.get("previous_close", 0) or
                                         stock_quote.get("close", 0) or 0),
                gap_pct          = self._get_gap_pct(symbol),
                minutes_since_open = self._minutes_since_open(),
            )

            if not filter_result.passed:
                logger.debug(
                    f"[{format_ist_timestamp()}] {symbol}: FILTERED — {filter_result.rejection_reason}"
                )
                return None

            # 8. Build signal using filter's final score and size
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
                size_multiplier=filter_result.size_multiplier,
                filter_bonuses=filter_result.bonuses,
            )

            logger.info(
                f"[{format_ist_timestamp()}] ✅ SIGNAL: {direction} {symbol} "
                f"| Score: {filter_result.final_score:.0f} | Entry: ₹{signal.entry_price:.2f}"
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
        Rate-limit safe: max 6 workers (Groww allows ~10 req/s).
        """
        # Refresh FII/DII + OC + FII futures once per cycle (not per symbol)
        self.refresh_institutional_context()

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
        if self._oc:
            try:
                oc_sym   = "NIFTY"  # Use Nifty chain for market bias
                ctx["oc_score"] = self._oc.get_direction_score(oc_sym)
                oc_result = self._oc.analyze(oc_sym)
                if oc_result:
                    ctx["oc_signals"] = oc_result.signals[:3]
            except Exception as e:
                logger.debug(f"OC context error: {e}")

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

        # ── NSE supplementary data (bulk/block, delivery, 52wk) ──
        if self._nse_data:
            try:
                # direction placeholder — adjusted in _compute_ai_score
                nse_score, nse_reason = self._nse_data.get_composite_score(
                    symbol, "LONG"
                )
                ctx["nse_score"]  = nse_score
                ctx["nse_reason"] = nse_reason
            except Exception as e:
                logger.debug(f"NSE data context error for {symbol}: {e}")

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

        aligned = alignment_score >= 70  # Raised: 50→70 — require strong HTF alignment

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
        Calculate relative strength of stock vs Nifty50.
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
        """Minutes elapsed since 9:15 AM IST market open (0.0 before open)."""
        from datetime import datetime as _dt
        now_ist = get_current_ist_time()
        market_open = _dt(now_ist.year, now_ist.month, now_ist.day, 9, 15, 0, tzinfo=IST)
        return max(0.0, (now_ist - market_open).total_seconds() / 60)

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

        # ── Time of day filter ────────────────────────────
        now_ist  = get_current_ist_time()
        time_val = now_ist.hour + now_ist.minute / 60
        if 11.0 <= time_val < 13.5:   # 11:00–1:30 PM: midday chop — hard block
            return None
        elif 9.25 <= time_val <= 10.5:  # 9:15–10:30: morning momentum power hour
            score += 6
        elif 13.5 <= time_val <= 14.5:  # 1:30–2:30 PM: afternoon institutional
            score += 4
        elif time_val >= 14.75:  # After 2:45 PM: avoid new positions
            score -= 8

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

        # ── [NEW] NSE bulk/block deal + delivery + 52wk ───
        nse_raw = ctx.get("nse_score", 0)
        if nse_raw != 0:
            # Flip sign for SHORT (buy signal = bad for short)
            nse_adj = nse_raw if direction == "LONG" else -nse_raw
            score += nse_adj
            if abs(nse_adj) >= 5:
                logger.debug(
                    f"NSE data adj={nse_adj:+d} | {ctx.get('nse_reason', '')}"
                )

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

        rationale = (
            f"Grade {quality_grade} | MTF: {mtf_str} | "
            f"RS vs Nifty: {rs:+.1f}% | "
            f"Volume: {ind.volume_ratio:.1f}x | "
            f"RSI: {ind.rsi:.0f} | "
            f"MACD: {'▲' if ind.macd_hist > 0 else '▼'} | "
            f"Supertrend: {'▲' if ind.supertrend_dir == 1 else '▼'}"
            f"{bonus_str}"
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
