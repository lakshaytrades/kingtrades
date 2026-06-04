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
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

from utils import (
    format_ist_timestamp, get_current_ist_time, get_current_et_time,
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

try:
    from global_market_context import GlobalMarketContext, get_global_market_context
    _GLOBAL_CTX_AVAILABLE = True
except ImportError:
    _GLOBAL_CTX_AVAILABLE = False

try:
    from sector_rotation import SectorRotationEngine, get_sector_rotation
    _SECTOR_ROT_AVAILABLE = True
except ImportError:
    _SECTOR_ROT_AVAILABLE = False

try:
    from economic_calendar import EconomicCalendar, get_economic_calendar
    _ECON_CAL_AVAILABLE = True
except ImportError:
    _ECON_CAL_AVAILABLE = False

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")   # server is UTC; all time checks use ET


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
    time_stop_minutes: int = 30       # Exit if no movement in N minutes (top-1% rule)

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
        min_signal_score: float = config.MIN_SIGNAL_SCORE,
        high_confidence_score: float = getattr(config, "HIGH_CONFIDENCE_SCORE", 80.0),
    ):
        self.fetcher = data_fetcher
        self.news_filter = news_filter
        self.recognizer = PatternRecognizer()
        self.min_score = min_signal_score
        self.high_conf_score = high_confidence_score
        self._nifty_open: Optional[float] = None
        self._nifty_current: Optional[float] = None
        self.ha_filter = HighAccuracyFilter(min_score=min_signal_score)
        self._learner = None          # Set by main.py: generator.set_learner(learner)
        self._orb_direction: str = "" # Set by main.py after ORB is established
        self._nifty_change_pct: float = 0.0
        self._daily_htf_cache: dict = {}        # instance-level — no cross-instance contamination
        self._open_position_symbols: list = []  # updated by main.py before each scan
        self._last_inst_ctx: dict = {}          # last inst_ctx — exposes regime_name to main.py
        self._daily_candles_cache: dict = {}    # {symbol: daily_df} — refreshed per scan cycle

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

        # Session-level no-data tracker: symbols with 3+ consecutive 0-bar
        # failures are skipped for the rest of the session to avoid log spam.
        self._no_data_strikes: Dict[str, int] = {}

        # Pattern performance analytics (lazy-loaded)
        self._pattern_analytics = None
        self._session_skip: set = set()
        self._overnight_bias: int = 0   # set by main.py after overnight_analyzer.run_analysis()

        # Concurrent scanning config
        self._max_workers = 10  # Parallel symbol scans (Alpaca rate-limit safe)

        # ── World market intelligence (global context + sector rotation + calendar) ──
        self._global_ctx: Optional["GlobalMarketContext"] = None
        self._sector_rot: Optional["SectorRotationEngine"] = None
        self._econ_cal: Optional["EconomicCalendar"] = None
        if _GLOBAL_CTX_AVAILABLE:
            try:
                self._global_ctx = get_global_market_context(data_fetcher)
            except Exception as _e:
                logger.debug(f"GlobalMarketContext init failed: {_e}")
        if _SECTOR_ROT_AVAILABLE:
            try:
                self._sector_rot = get_sector_rotation(data_fetcher)
            except Exception as _e:
                logger.debug(f"SectorRotationEngine init failed: {_e}")
        if _ECON_CAL_AVAILABLE:
            try:
                self._econ_cal = get_economic_calendar()
            except Exception as _e:
                logger.debug(f"EconomicCalendar init failed: {_e}")

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
        Refresh FII/DII flow, global market context, and economic calendar
        before each scan cycle. Called once per scan — not per symbol.
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

        # Refresh global market context (inter-market: VIX, gold, yields, futures)
        if self._global_ctx:
            try:
                self._global_ctx.refresh()
                vix_regime, vix_mult = self._global_ctx.get_vix_regime()
                self._last_vix = float(vix_mult)   # size_mult: 0.3=extreme fear, 1.0=normal
                logger.info(
                    f"[{format_ist_timestamp()}] Global: VIX={vix_regime} "
                    f"size_mult={vix_mult:.2f} | {self._global_ctx.format_one_line()}"
                )
            except Exception as _ge:
                logger.debug(f"GlobalMarketContext refresh failed: {_ge}")

        # Refresh sector rotation (hot/cold sectors based on 5-day ETF momentum)
        if self._sector_rot:
            try:
                self._sector_rot.refresh()
                logger.debug(
                    f"[{format_ist_timestamp()}] Sector rotation: "
                    f"HOT={self._sector_rot.hot_sectors} COLD={self._sector_rot.cold_sectors}"
                )
            except Exception as _se:
                logger.debug(f"SectorRotation refresh failed: {_se}")

    # --------------------------------------------------------
    # MAIN SIGNAL GENERATION
    # --------------------------------------------------------

    def generate_signal(self, symbol: str) -> Optional[TradeSignal]:
        """
        Generate a trade signal for a single symbol.
        Returns TradeSignal if confidence >= min_score, else None.
        """
        try:
            # Reset ha_filter threshold to canonical min_score at start of each call.
            # Target chase mode may lower it mid-session; this prevents permanent drift.
            self.ha_filter.min_score = self.min_score

            # Adaptive threshold (adjusts based on recent 20-trade WR)
            try:
                from adaptive_threshold import get_adaptive_min_score as _get_adaptive_min
                _effective_min, _thresh_label = _get_adaptive_min(self.min_score)
                if _effective_min != self.min_score:
                    self.ha_filter.min_score = _effective_min
                    logger.debug(f"{symbol}: adaptive threshold {_thresh_label} → min={_effective_min:.0f}")
            except Exception:
                pass

            # Skip symbols that have repeatedly returned no data this session
            if symbol in self._session_skip:
                logger.debug(f"{symbol}: skipped — no data available (session blacklist)")
                return None

            logger.info(f"[{format_ist_timestamp()}] Analyzing {symbol}...")

            # 1. Fetch multi-timeframe data
            mtf_data = self.fetcher.get_multi_timeframe_data(symbol)
            df_5m  = mtf_data.get("5m")
            df_15m = mtf_data.get("15m")
            df_1h  = mtf_data.get("1h")

            bar_count = len(df_5m) if df_5m is not None else 0
            if bar_count < 10:
                strikes = self._no_data_strikes.get(symbol, 0) + 1
                self._no_data_strikes[symbol] = strikes
                if strikes >= 6:
                    self._session_skip.add(symbol)
                    logger.info(
                        f"[{format_ist_timestamp()}] {symbol}: no 5m data after {strikes} attempts "
                        f"— skipping for rest of session"
                    )
                else:
                    logger.info(
                        f"[{format_ist_timestamp()}] {symbol}: insufficient 5m data "
                        f"(got {bar_count} bars, attempt {strikes}/3)"
                    )
                return None

            # Data is available — reset strike counter
            self._no_data_strikes.pop(symbol, None)

            if bar_count < 30:
                logger.info(f"[{format_ist_timestamp()}] {symbol}: only {bar_count} bars — proceeding with limited history")

            # 1b. Fetch daily candles for Gates 16/17 (clear-air + HTF bias)
            # Cache by symbol so we don't re-fetch within the same scan cycle.
            if symbol not in self._daily_candles_cache:
                try:
                    _daily = self.fetcher.get_candles(symbol, "1Day", limit=25)
                    self._daily_candles_cache[symbol] = _daily
                except Exception:
                    self._daily_candles_cache[symbol] = None

            # 1c. Earnings proximity gate — skip 3 days before earnings (too unpredictable)
            try:
                import config as _cfg_ep
                if getattr(_cfg_ep, "EARNINGS_PROXIMITY_GATE", True):
                    _ep_days = getattr(_cfg_ep, "EARNINGS_PROXIMITY_DAYS", 3)
                    _ep_cal  = getattr(self, "_econ_cal", None)   # was "_calendar" — typo fix
                    if _ep_cal and hasattr(_ep_cal, "days_until_earnings"):
                        _dte = _ep_cal.days_until_earnings(symbol)
                        if _dte is not None and 0 < _dte <= _ep_days:
                            logger.debug(f"[EARNINGS GATE] {symbol} — earnings in {_dte}d — skipping")
                            return None
            except Exception:
                pass

            # 1d. Earnings calendar protection (v23.0) — skip within 2 days of earnings
            # Complements the EARNINGS_PROXIMITY_GATE above but uses yfinance calendar
            # as a second, independent data source for higher coverage.
            try:
                from earnings_calendar import is_near_earnings
                if getattr(config, 'EARNINGS_PROTECTION_ENABLED', True) and is_near_earnings(symbol):
                    logger.debug(f"{symbol}: SKIP — earnings within 2 days")
                    return None
            except Exception:
                pass

            # 2. Pattern analysis on each timeframe
            # Set df.attrs["symbol"] so pattern_recognizer can fetch daily OHLC for pivot levels
            df_5m.attrs["symbol"] = symbol
            # Record latest bar for Time-of-Day RVOL (institutional strategy 5)
            try:
                from institutional_strategies import record_bar_volume as _rbv
                if hasattr(df_5m.index[-1], 'strftime'):
                    _rbv(symbol, df_5m.index[-1].strftime("%H:%M"), int(df_5m['volume'].iloc[-1]) if 'volume' in df_5m.columns else int(df_5m['Volume'].iloc[-1]))
            except Exception:
                pass
            analysis_5m  = self.recognizer.analyze(df_5m)
            analysis_15m = self.recognizer.analyze(df_15m) if df_15m is not None else {}
            analysis_1h  = self.recognizer.analyze(df_1h)  if df_1h is not None else {}

            score_5m  = analysis_5m.get("score", {})
            score_15m = analysis_15m.get("score", {}) if analysis_15m else {}
            score_1h  = analysis_1h.get("score", {})  if analysis_1h else {}

            ind = analysis_5m.get("indicators", IndicatorSet())

            # Apply pattern analytics confidence multiplier (proven patterns score stronger)
            try:
                if self._pattern_analytics is None:
                    from pattern_analytics import PatternAnalytics
                    self._pattern_analytics = PatternAnalytics()
                _now_et = get_current_ist_time()
                _time_b = "OPEN" if _now_et.hour + _now_et.minute / 60 <= 11 else "MID"
                _regime_b = ""  # regime not yet computed here — use global bucket
                for _p in analysis_5m.get("patterns", []):
                    _mult = self._pattern_analytics.confidence_multiplier(
                        _p.name, _time_b, _regime_b
                    )
                    if _mult != 1.0 and hasattr(_p, "confidence"):
                        _p.confidence = min(100.0, _p.confidence * _mult)
            except Exception:
                pass

            # 3. Multi-timeframe alignment
            alignment = self._check_mtf_alignment(score_5m, score_15m, score_1h)
            if dir_5m := score_5m.get("direction", "NEUTRAL"):
                pass  # keep direction for fallback below
            if not alignment["aligned"]:
                if config.REQUIRE_MTF_ALIGNMENT:
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: MTF not aligned — dir_5m={dir_5m} align_score={alignment.get('score',0):.0f} require_mtf={config.REQUIRE_MTF_ALIGNMENT}")
                    return None
                # MTF alignment not required — continue with 5m direction, apply penalty later
                logger.debug(f"{symbol}: MTF partial alignment {alignment['score']:.0f} dir_5m={dir_5m} — proceeding with penalty")

            direction = alignment["direction"] if alignment["aligned"] else (dir_5m if dir_5m != "NEUTRAL" else "LONG")

            # 3b. SHORT signal override (v22.0) — check short conditions when enabled
            # Activates in BEAR or CHOP regime when LONG alignment is weak or absent
            if getattr(config, 'SHORT_SELLING_ENABLED', True) and direction == "LONG":
                try:
                    _regime_name_short = ""
                    # Get current regime from inst_ctx if available (will be populated below)
                    # Here we use a fast check on the 5m indicators directly
                    _close_vals = df_5m["close"].values if "close" in df_5m.columns else (
                        df_5m["Close"].values if "Close" in df_5m.columns else None
                    )
                    if _close_vals is not None and len(_close_vals) >= 20:
                        import numpy as _np_s
                        _ltp_s = float(_close_vals[-1])
                        # Quick EMA9 check
                        _ema9_s = float(pd.Series(_close_vals).ewm(span=9, adjust=False).mean().iloc[-1])
                        # RSI quick check using score_5m
                        _rsi_s = score_5m.get("rsi", 50.0) or 50.0
                        # MACD histogram from score_5m
                        _macd_hist_s = score_5m.get("macd_hist", 0.0) or 0.0
                        # VWAP from analysis_5m indicators (will be set later; use a best-effort approach)
                        _ind_s = analysis_5m.get("indicators", None)
                        _vwap_s = getattr(_ind_s, "vwap", 0.0) if _ind_s else 0.0

                        # Determine regime quickly from SPY change direction
                        _regime_is_bear_or_chop = (
                            self._nifty_change_pct < -1.0  # SPY down >1% = bearish session
                            or (not alignment["aligned"] and dir_5m in ("SHORT", "NEUTRAL"))
                        )

                        # SHORT conditions: RSI overbought + price below EMA9 + MACD neg + below VWAP
                        _short_rsi_ok    = float(_rsi_s) > 68
                        _short_ema9_ok   = _ltp_s < _ema9_s
                        _short_macd_ok   = float(_macd_hist_s) < 0
                        _short_vwap_ok   = (_vwap_s > 0 and _ltp_s < _vwap_s) or _vwap_s == 0

                        if (_regime_is_bear_or_chop
                                and _short_rsi_ok
                                and _short_ema9_ok
                                and _short_macd_ok
                                and _short_vwap_ok):
                            direction = "SHORT"
                            logger.info(
                                f"[{format_ist_timestamp()}] {symbol}: SHORT override "
                                f"(RSI={_rsi_s:.0f}>68, below EMA9=${_ema9_s:.2f}, "
                                f"MACD<0, {'below VWAP' if _short_vwap_ok else 'no VWAP'})"
                            )
                except Exception as _short_det_e:
                    logger.debug(f"[suppressed] short_detection: {_short_det_e}")

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
            inst_ctx = self._get_institutional_context(symbol, df_5m, direction=direction)

            # 5b-ii. Extract harmonic score for EliteBrain module
            _pat_objs_for_harm = analysis_5m.get("patterns", [])
            _harm_keywords = ("OTE", "Gartley", "Butterfly", "Bat", "Crab", "ABCD", "Three Drives", "Cypher")
            harm_long = sum(
                getattr(p, "confidence", 0) for p in _pat_objs_for_harm
                if getattr(p, "direction", "") == "LONG" and
                any(k in getattr(p, "name", "") for k in _harm_keywords)
            )
            harm_short = sum(
                getattr(p, "confidence", 0) for p in _pat_objs_for_harm
                if getattr(p, "direction", "") == "SHORT" and
                any(k in getattr(p, "name", "") for k in _harm_keywords)
            )
            inst_ctx["harmonic_score"] = max(-50, min(harm_long - harm_short, 50))  # clip ±50

            # 5c. Regime block — skip signal if regime is AVOID
            # Exception: RANGING/MIDDAY_CHOP regimes allow VWAP Mean Reversion through —
            # that strategy *works* in chop whereas breakouts fail.
            if inst_ctx.get("regime_block", False):
                _regime_name = inst_ctx.get("regime_name", "")
                _chop_regimes = ("RANGING", "MIDDAY_CHOP", "LOW_VOLATILITY")
                _vwap_rev_patterns = {"VWAP Mean Reversion", "VWAP Bounce", "VWAP Breakdown"}
                _analysis_pats = {p.name for p in analysis_5m.get("patterns", [])}
                if (_regime_name in _chop_regimes
                        and getattr(config, "VWAP_REVERSION_ENABLED", True)
                        and _analysis_pats & _vwap_rev_patterns):
                    logger.info(
                        f"[{format_ist_timestamp()}] {symbol}: regime={_regime_name} but "
                        f"VWAP reversion pattern detected — allowing through"
                    )
                else:
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: regime_block=True — skipping")
                    return None

            # 5c-alt. Economic Calendar hard block (FOMC/CPI/NFP before release)
            if inst_ctx.get("calendar_ok") is False:
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: CALENDAR BLOCK — "
                    f"{inst_ctx.get('calendar_note', 'high-impact event window')}"
                )
                return None

            # 5c-ii. No new entries at or after 3:00 PM ET — last 30 min reversals destroy P&L
            try:
                _et_now = get_current_et_time()
                _cutoff_h = getattr(config, "NO_ENTRY_AFTER_ET_HOUR", 15)
                _cutoff_m = getattr(config, "NO_ENTRY_AFTER_ET_MINUTE", 0)
                if (_et_now.hour > _cutoff_h
                        or (_et_now.hour == _cutoff_h and _et_now.minute >= _cutoff_m)):
                    logger.info(
                        f"[{format_ist_timestamp()}] {symbol}: after {_cutoff_h}:{_cutoff_m:02d} ET cutoff — no new entries"
                    )
                    return None
            except Exception:
                pass

            # 5c-iii. TARGET CHASE mode — after 1:30 PM ET with no winning trade yet,
            # temporarily lower threshold by 4 pts (74 instead of 78) for one last setup.
            # Prop trader rule: find ONE good trade per day — every day should hit target.
            try:
                _tc_now = get_current_et_time()
                if _tc_now.hour >= 13 and _tc_now.minute >= 30 or _tc_now.hour >= 14:
                    try:
                        from daily_profit_engine import get_profit_engine
                        _eng = get_profit_engine()
                        _today_wins = getattr(_eng.state, "winning_trades", 0)
                        if _today_wins == 0:
                            _chase_threshold = max(self.min_score - 4, 66.0)
                            if self.ha_filter.min_score > _chase_threshold:
                                self.ha_filter.min_score = _chase_threshold
                                logger.info(
                                    f"[{format_ist_timestamp()}] {symbol}: TARGET CHASE mode "
                                    f"(1:30 PM+ ET, 0 wins today) — threshold lowered to {_chase_threshold:.0f}"
                                )
                    except Exception:
                        pass
            except Exception:
                pass

            # 5c-iv. HIGH_VOLATILITY regime: skip LONG entries — shorts still allowed
            if (inst_ctx.get("regime_name", "") == "HIGH_VOLATILITY"
                    and direction == "LONG"
                    and getattr(config, "SKIP_VOLATILE_LONGS", True)):
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: HIGH_VOLATILITY regime — "
                    f"LONG skipped (only SHORTs allowed when VIX is extreme)"
                )
                return None

            # 5d. Daily HTF bias enforcement (Grok #8):
            # Only take LONG trades when daily structure is bullish
            # (price > 20-day SMA AND recent higher highs/lows).
            daily_bias_penalty = self._get_daily_htf_penalty(symbol, direction)
            if daily_bias_penalty is None:
                daily_bias_penalty = -5.0   # apply light penalty instead of hard block

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
                df_5m=df_5m,
                symbol=symbol,
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

            # 6e. Gap direction alignment boost (top-3% edge: gap + direction = very high win rate)
            try:
                gap_pct = self._get_gap_pct(symbol)
                if direction == "LONG" and gap_pct >= 0.5:
                    gap_boost = min(gap_pct * 2.0, config.GAP_DIRECTION_BOOST)
                    ai_score = min(100.0, ai_score + gap_boost)
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: gap boost +{gap_boost:.1f} (gap={gap_pct:+.1f}%)")
                elif direction == "SHORT" and gap_pct <= -0.5:
                    gap_boost = min(abs(gap_pct) * 2.0, config.GAP_DIRECTION_BOOST)
                    ai_score = min(100.0, ai_score + gap_boost)
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: gap boost +{gap_boost:.1f} (gap={gap_pct:+.1f}%)")
            except Exception:
                pass

            # 6f. ICT triple confluence bonus (OB + FVG + BOS together = institutional setup)
            try:
                _early_pattern_objs = analysis_5m.get("patterns", [])
                pat_names_all = {p.name for p in _early_pattern_objs if hasattr(p, "name")}
                ict_bull = {"Bullish FVG", "Bullish Order Block", "BOS — Higher High (Trend Continues)"}
                ict_bear = {"Bearish FVG", "Bearish Order Block", "BOS — Lower Low (Trend Continues)"}
                if direction == "LONG" and len(ict_bull & pat_names_all) >= 2:
                    ai_score = min(100.0, ai_score + config.ICT_CONFLUENCE_BOOST)
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: ICT confluence +{config.ICT_CONFLUENCE_BOOST:.0f} ({ict_bull & pat_names_all})")
                elif direction == "SHORT" and len(ict_bear & pat_names_all) >= 2:
                    ai_score = min(100.0, ai_score + config.ICT_CONFLUENCE_BOOST)
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: ICT confluence +{config.ICT_CONFLUENCE_BOOST:.0f} ({ict_bear & pat_names_all})")
            except Exception:
                pass

            # 6g. Sector ETF leading indicator boost (top-1%: trade with sector flow)
            try:
                sector_boost = self._get_sector_etf_boost(symbol, direction)
                if sector_boost > 0:
                    ai_score = min(100.0, ai_score + sector_boost)
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: sector ETF boost +{sector_boost:.1f}")
            except Exception:
                pass

            # 6h. RVOL mega-boost — >5x volume = explosive move, size conviction up
            try:
                rvol_threshold = getattr(config, "RVOL_MEGA_THRESHOLD", 5.0)
                rvol_bonus_pts = getattr(config, "RVOL_MEGA_BOOST_PTS", 8.0)
                if ind.volume_ratio >= rvol_threshold:
                    ai_score = min(100.0, ai_score + rvol_bonus_pts)
                    logger.info(
                        f"[{format_ist_timestamp()}] {symbol}: RVOL mega-boost "
                        f"+{rvol_bonus_pts:.0f} (RVOL={ind.volume_ratio:.1f}x)"
                    )
            except Exception:
                pass

            # Per-symbol adaptive score floor: proven symbols get -5 pts relief,
            # serial losers get +5 pts harder bar. Falls back to self.min_score.
            _sym_min = self.min_score
            try:
                _sym_stats_check = getattr(self, "_symbol_stats", None)
                if _sym_stats_check is None:
                    from symbol_stats import SymbolStats
                    self._symbol_stats = SymbolStats()
                    _sym_stats_check = self._symbol_stats
                _sym_min = _sym_stats_check.min_score_for(symbol, base_min=self.min_score)
            except Exception:
                pass

            if ai_score is None or ai_score < _sym_min:
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: score {ai_score:.1f} below "
                    f"threshold {_sym_min:.0f} (base={self.min_score:.0f})"
                )
                try:
                    from decision_log import log_rejected_score
                    log_rejected_score(symbol, direction, ai_score or 0.0, _sym_min)
                except Exception:
                    pass
                return None

            # 7. High-accuracy filter — 5-gate confluence check
            pattern_objs   = analysis_5m.get("patterns", [])
            pattern_names  = [p.name for p in pattern_objs if hasattr(p, "name")]
            pattern_scores = [getattr(p, "confidence", 70.0) for p in pattern_objs]

            ltp_now   = float(df_5m.iloc[-1]["close"])
            above_vwap = (ltp_now >= ind.vwap) if (ind.vwap and ind.vwap > 0) else None  # None = no VWAP data, not bullish

            stock_quote      = self.fetcher.get_quote(symbol) or {}
            stock_change_pct = stock_quote.get("change_pct", 0.0)

            # 6i. 52-week high breakout bonus — reuse already-fetched stock_quote (no duplicate call)
            try:
                _high52 = float(stock_quote.get("week_52_high", 0) or stock_quote.get("high_52w", 0) or 0)
                _ltp52  = float(df_5m.iloc[-1]["close"])
                if _high52 > 0 and _ltp52 >= _high52 * 0.995 and direction == "LONG":
                    ai_score = min(100.0, ai_score + 10.0)
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: 52W high breakout +10 pts")
            except Exception:
                pass

            # Expose spy_trend in inst_ctx so EliteBrain nifty_trend module gets correct value
            inst_ctx["spy_trend"] = self._nifty_change_pct
            # Expose mtf_alignment dict in inst_ctx for EliteBrain's MTF module (weight 1.8)
            inst_ctx["mtf_alignment"] = alignment
            # Cache for main.py regime-routing (mean-reversion engine)
            self._last_inst_ctx = inst_ctx

            # Gate 13: SPY direction — determine bullish/bearish state
            spy_bullish: Optional[bool] = None
            if abs(self._nifty_change_pct) >= 0.1:   # neutral band: ignore ±0.1% moves
                spy_bullish = self._nifty_change_pct > 0.0

            filter_result = self.ha_filter.evaluate(
                signal_score     = ai_score,
                direction        = "BUY" if direction == "LONG" else "SELL",
                # inst_ctx holds the actual regime string; alignment dict has no "regime" key
                regime           = inst_ctx.get("regime_name", "UNKNOWN"),
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
                # Gate 6: Use PREVIOUS day's full volume from daily bar cache.
                # The live quote volume is tiny at market open (only minutes of data)
                # which would falsely filter NVDA/AAPL/TSLA as "low volume".
                daily_volume     = self._get_prev_day_volume(
                                       symbol,
                                       stock_quote.get("daily_volume", 0) or
                                       stock_quote.get("traded_volume", 0) or
                                       stock_quote.get("volume", 0) or 0,
                                   ),
                ltp              = ltp_now,
                prev_close       = float(stock_quote.get("prev_close", 0) or
                                         stock_quote.get("previous_close", 0) or
                                         stock_quote.get("close", 0) or 0),
                gap_pct          = self._get_gap_pct(symbol),
                minutes_since_open = self._minutes_since_open(),
                # ── Gates 11-13 parameters ────────────────────────────
                at_key_level     = (
                    getattr(ind, "at_hvn", False)
                    or getattr(ind, "at_lvn", False)
                    # VWAP-proximity: within 2% only when VWAP data is actually available
                    or (ind.vwap > 0 and abs(ltp_now - ind.vwap) / ind.vwap <= 0.02)
                ),
                adx              = getattr(ind, "adx", 0.0),
                spy_bullish      = spy_bullish,
                # Gate 14: pass open position symbols for correlation check
                open_positions   = list(getattr(self, "_open_position_symbols", [])),
                # ── Gates 15-18 parameters (70-80% WR upgrade) ────────────────
                candles_df       = df_5m,
                atr              = getattr(ind, "atr", 0.0),
                daily_candles_df = getattr(self, "_daily_candles_cache", {}).get(symbol),
                fetcher          = self.fetcher,
            )

            if not filter_result.passed:
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: FILTERED — {filter_result.rejection_reason}"
                )
                try:
                    from decision_log import log_rejected_haf
                    log_rejected_haf(symbol, direction, ai_score, filter_result)
                except Exception:
                    pass
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
                    try:
                        from decision_log import log_rejected_internals
                        log_rejected_internals(symbol, direction, ai_score, breadth_reason)
                    except Exception:
                        pass
                    return None
                # Adjust size by breadth quality
                combined_size = round(combined_size * internals.get_size_multiplier(direction), 2)
                ind.breadth_score = breadth["breadth_score"]
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")

            # ── Elite Brain: 12-module ensemble fusion (top-1% gate) ────────
            try:
                from elite_brain import get_elite_brain
                elite = get_elite_brain()
                elite_decision = elite.evaluate(
                    symbol        = symbol,
                    direction     = direction,
                    signal_score  = ai_score,
                    ctx           = inst_ctx,
                    sm_score      = sm_score,
                    pm_score      = pm_score,
                    nifty_trend   = "bullish" if inst_ctx.get("spy_trend", 0) > 0 else
                                    "bearish" if inst_ctx.get("spy_trend", 0) < 0 else "neutral",
                    overnight_bias=getattr(self, '_overnight_bias', 0),
                )
                if not elite_decision.approved:
                    logger.info(
                        f"[{format_ist_timestamp()}] {symbol}: ELITE BRAIN REJECTED — "
                        f"{elite_decision.reject_reason}"
                    )
                    try:
                        from decision_log import log_rejected_elite
                        log_rejected_elite(symbol, direction, ai_score, elite_decision.reject_reason)
                    except Exception:
                        pass
                    return None
                # Grand Slam: 7+ modules aligned → scale up size aggressively
                if elite_decision.grand_slam:
                    combined_size = min(combined_size * 2.0, 3.0)
                    logger.info(
                        f"[{format_ist_timestamp()}] 🏆 {symbol}: GRAND SLAM — "
                        f"{elite_decision.aligned_count} modules aligned | "
                        f"conviction={elite_decision.conviction_score:.0f} | size={combined_size}x"
                    )
                else:
                    # Blend elite conviction into size
                    elite_size_adj = elite_decision.size_multiplier
                    combined_size  = round(min(combined_size * elite_size_adj, 3.0), 2)
                # Use elite conviction score if it's higher
                if elite_decision.conviction_score > ai_score:
                    ai_score = min(100.0, elite_decision.conviction_score)
            except Exception as _eb:
                logger.debug(f"[suppressed] elite_brain: {_eb}")

            # ── VIX regime sizing (top-1% rule: size by fear level) ──────────
            try:
                from data_fetch_alpaca import get_vix_level as _get_vix_level
                vix_level = _get_vix_level()
                if vix_level <= 0:
                    vix_level = 18.0   # safe default (optimal zone)
                if vix_level > 35:
                    if direction == "LONG":
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: VIX>{vix_level:.0f} — LONG blocked in extreme fear")
                        return None
                    combined_size = round(combined_size * 0.5, 2)   # SHORT only at 50% size in crash
                elif vix_level > 25:
                    combined_size = round(combined_size * 0.75, 2)  # High fear → reduce size
                elif vix_level < 14:
                    combined_size = round(combined_size * 0.8, 2)   # Low VIX = complacency → reduce size
                # VIX 14-25 = optimal momentum zone → full size
            except Exception as _vix:
                logger.debug(f"[suppressed] vix_sizing: {_vix}")

            # ── Booster cap: track base score so total booster delta cannot exceed +25 ──
            # 40+ booster modules can stack uncapped, pushing weak signals to false conviction.
            # Hard cap: booster contribution (delta from base) is limited to +25 points.
            _booster_base_score = filter_result.final_score
            _BOOSTER_MAX_DELTA  = 25.0

            # ── Gemini news sentiment adjustment (runs FIRST — adjusts score before LLM sees it) ─
            try:
                from gemini_filter import get_gemini_filter
                if getattr(config, "GEMINI_NEWS_FILTER_ENABLED", True):
                    _g_delta, _g_reason = get_gemini_filter().score_signal(
                        symbol, direction, filter_result.final_score
                    )
                    if _g_delta != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _g_delta))
                        if _g_delta < -5:
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: Gemini news penalty {_g_delta:+.0f} → {filter_result.final_score:.0f} | {_g_reason}")
                        elif _g_delta > 5:
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: Gemini news boost {_g_delta:+.0f} → {filter_result.final_score:.0f} | {_g_reason}")
                        # no early exit — let all boosters accumulate; final exec gate decides
            except Exception as _ge:
                logger.debug(f"[suppressed] gemini_filter: {_ge}")

            # ── Sector Relative Strength (stock vs its sector ETF) ─────────────────────
            # Top-1% edge: only trade stocks LEADING their sector, not lagging it
            try:
                if getattr(config, "SECTOR_RS_ENABLED", True):
                    from sector_rs import get_sector_rs_score
                    _rs_delta, _rs_reason = get_sector_rs_score(symbol, direction)
                    if _rs_delta != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _rs_delta))
                        if _rs_delta >= 5:
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: Sector RS boost {_rs_delta:+.0f} → {filter_result.final_score:.0f} | {_rs_reason}")
                        elif _rs_delta <= -5:
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: Sector RS drag {_rs_delta:+.0f} → {filter_result.final_score:.0f} | {_rs_reason}")
                        # no early exit — let all boosters accumulate; final exec gate decides
            except Exception as _rse:
                logger.debug(f"[suppressed] sector_rs: {_rse}")

            # ── Short Squeeze Detection ────────────────────────────────────────────────
            # Boost LONG signals on high-short-float stocks (squeeze fuel)
            # Penalize SHORT signals (entering against a squeeze = dangerous)
            try:
                if getattr(config, "SQUEEZE_SCANNER_ENABLED", True):
                    from squeeze_scanner import get_squeeze_score_delta
                    _sq_delta, _sq_reason = get_squeeze_score_delta(symbol, direction)
                    if _sq_delta != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _sq_delta))
                        if abs(_sq_delta) >= 5:
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: Squeeze {_sq_delta:+.0f} → {filter_result.final_score:.0f} | {_sq_reason}")
                        # no early exit — let all boosters accumulate; final exec gate decides
            except Exception as _sqe:
                logger.debug(f"[suppressed] squeeze_scanner: {_sqe}")

            # ── Post-Earnings Announcement Drift (PEAD) ────────────────────────────────
            # 80% accuracy: stocks drift in direction of EPS surprise for 3-21 days
            # Boost LONG on beat, LONG on miss gets penalized, vice versa for SHORT
            try:
                if getattr(config, "PEAD_SCORER_ENABLED", True):
                    from pead_scorer import get_pead_score_delta
                    _pead_delta, _pead_reason = get_pead_score_delta(symbol, direction)
                    if _pead_delta != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _pead_delta))
                        if abs(_pead_delta) >= 3:
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: PEAD {_pead_delta:+.0f} → {filter_result.final_score:.0f} | {_pead_reason}")
                        # no early exit — let all boosters accumulate; final exec gate decides
            except Exception as _pe2:
                logger.debug(f"[suppressed] pead_scorer: {_pe2}")

            # ── Futures / Pre-Market Bias ──────────────────────────────────────────────
            # Pre-market ES/NQ direction → 73% predictive accuracy for first 30 min
            # Trades AGAINST futures bias get penalized; aligned trades get boosted
            try:
                if getattr(config, "FUTURES_BIAS_ENABLED", True):
                    from futures_bias import get_futures_bias
                    _fbias = get_futures_bias()
                    _fb_delta, _fb_reason = _fbias.get_score_adjustment(direction)
                    if _fb_delta != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _fb_delta))
                        if abs(_fb_delta) >= 5:
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: Futures bias {_fb_delta:+.0f} → {filter_result.final_score:.0f} | {_fb_reason}")
                        # no early exit — let all boosters accumulate; final exec gate decides
                        # Apply futures size multiplier on top of existing size
                        if abs(_fb_delta) >= 5:
                            combined_size = round(combined_size * _fbias.size_mult, 3)
            except Exception as _fbe:
                logger.debug(f"[suppressed] futures_bias: {_fbe}")

            # ── Order Flow Imbalance (OFI) Score ───────────────────────────────────────
            # Cumulative delta tells you who is winning the order battle right now
            try:
                if getattr(config, "OFI_SCORE_ENABLED", True):
                    from order_flow_analyzer import get_ofi_score
                    _ofi_delta, _ofi_reason = get_ofi_score(df_5m, direction)
                    if _ofi_delta != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _ofi_delta))
                        if abs(_ofi_delta) >= 4:
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: OFI {_ofi_delta:+.0f} → {filter_result.final_score:.0f} | {_ofi_reason}")
                        # no early exit — let all boosters accumulate; final exec gate decides
            except Exception as _ofie:
                logger.debug(f"[suppressed] order_flow: {_ofie}")

            # ── Dark Pool / Institutional Block Detection ──────────────────────────────
            try:
                if getattr(config, "DARK_POOL_ENABLED", True):
                    from dark_pool_tracker import get_dark_pool_score
                    _dp_delta, _dp_reason = get_dark_pool_score(df_5m, direction, symbol)
                    if _dp_delta > 0:
                        filter_result.final_score = min(100.0, filter_result.final_score + _dp_delta)
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: DarkPool +{_dp_delta:.0f} → {filter_result.final_score:.0f} | {_dp_reason}")
            except Exception as _dpe:
                logger.debug(f"[suppressed] dark_pool: {_dpe}")

            # ── FIBONACCI PRECISION LEVELS (institutional entry zones) ────────────
            try:
                import config as _cfgFIB
                if getattr(_cfgFIB, 'FIBONACCI_ENABLED', True):
                    from fibonacci_levels import get_fibonacci_score
                    _fib_delta, _fib_reason = get_fibonacci_score(df_5m, ltp_now, direction)
                    if _fib_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _fib_delta)
                        logger.debug(f"{symbol}: FIB {_fib_delta:+.0f} {_fib_reason}")
            except Exception:
                pass

            # ── SHORT SQUEEZE INTELLIGENCE (high short float + volume surge) ──────
            try:
                import config as _cfgSQ
                if getattr(_cfgSQ, 'SHORT_SQUEEZE_ENABLED', True):
                    from short_squeeze_detector import get_short_squeeze_score
                    _sq_delta, _sq_reason = get_short_squeeze_score(
                        symbol, direction, float(getattr(ind, 'volume_ratio', 1.0) or 1.0)
                    )
                    if _sq_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _sq_delta)
                        if abs(_sq_delta) >= 4:
                            logger.debug(f"{symbol}: SQUEEZE {_sq_delta:+.0f} {_sq_reason}")
            except Exception:
                pass

            # ── OPTIONS FLOW (institutional call/put sweeps) ──────────────────────
            # Only run for signals that already look strong (score > 55) — options fetch is slow
            try:
                import config as _cfgOF
                if getattr(_cfgOF, 'OPTIONS_FLOW_ENABLED', True) and filter_result.final_score >= 55:
                    from options_flow import get_options_signal
                    _of_delta, _of_reason = get_options_signal(symbol, ltp_now, direction)
                    if _of_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _of_delta)
                        if abs(_of_delta) >= 4:
                            logger.debug(f"{symbol}: OPTIONS {_of_delta:+.0f} {_of_reason}")
            except Exception:
                pass

            # ── INSIDER TRANSACTION FLOW (SEC Form 4 — 24h cache) ────────────────
            # Only run for signals scoring > 60 — EDGAR fetch is slow
            try:
                import config as _cfgINS
                if getattr(_cfgINS, 'INSIDER_FLOW_ENABLED', True) and filter_result.final_score >= 60:
                    from insider_flow import get_insider_score
                    _ins_delta, _ins_reason = get_insider_score(symbol)
                    if _ins_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ins_delta)
                        if abs(_ins_delta) >= 3:
                            logger.debug(f"{symbol}: INSIDER {_ins_delta:+.0f} {_ins_reason}")
            except Exception:
                pass

            # ── PORTFOLIO GUARD — correlation + heat check ────────────────────
            try:
                from portfolio_guard import check_correlation_risk, check_sector_concentration
                _open_syms = list(getattr(self.risk_manager, '_positions', {}).keys()) if self.risk_manager else []
                if _open_syms:
                    _corr_risky, _corr_reason, _corr_score = check_correlation_risk(symbol, _open_syms)
                    if _corr_risky and _corr_score > 0.8:
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: correlation block — {_corr_reason}")
                        return None
                    _sec_risky, _sec_reason = check_sector_concentration(symbol, _open_syms)
                    if _sec_risky:
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: sector concentration — {_sec_reason}")
                        return None
            except Exception as _pg_e:
                logger.debug(f"[suppressed] portfolio_guard: {_pg_e}")

            # ── INSTITUTIONAL STRATEGIES (v10.0) ──────────────────────────────────
            try:
                from institutional_strategies import (
                    get_cross_sectional_rank,
                    get_vwap_reclaim_score,
                    get_power_hour_score,
                    get_pairs_signal,
                    get_tod_rvol_score,
                    get_sortino_size_multiplier,
                )
                _watchlist = list(getattr(self, '_open_position_symbols', None) or [])

                # 1. Cross-sectional momentum rank (AQR/Renaissance)
                if getattr(config, 'CSM_ENABLED', True):
                    _csm_delta, _csm_reason = get_cross_sectional_rank(symbol, _watchlist or [symbol])
                    if _csm_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _csm_delta)
                        logger.debug(f"{symbol}: CSM {_csm_delta:+.0f} {_csm_reason}")

                # 2. VWAP reclaim (prop desk / CME market makers)
                if getattr(config, 'VWAP_RECLAIM_ENABLED', True) and ind.vwap > 0:
                    _vr_delta, _vr_reason = get_vwap_reclaim_score(df_5m, ltp_now, direction, ind.vwap)
                    if _vr_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _vr_delta)
                        logger.debug(f"{symbol}: VWAP_RECLAIM {_vr_delta:+.0f} {_vr_reason}")

                # 3. Power hour boost (institutional rebalancing 3:00–3:30 PM ET)
                if getattr(config, 'POWER_HOUR_ENABLED', True):
                    _ph_delta, _ph_reason = get_power_hour_score(direction, filter_result.final_score)
                    if _ph_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ph_delta)
                        logger.debug(f"{symbol}: POWER_HOUR {_ph_delta:+.0f} {_ph_reason}")

                # 4. Statistical pairs convergence (JP Morgan / Goldman stat arb)
                if getattr(config, 'PAIRS_SIGNAL_ENABLED', True):
                    _ps_delta, _ps_reason = get_pairs_signal(symbol, direction)
                    if _ps_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ps_delta)
                        logger.debug(f"{symbol}: PAIRS {_ps_delta:+.0f} {_ps_reason}")

                # 5. Time-of-Day RVOL (honest same-slot volume comparison)
                if getattr(config, 'TOD_RVOL_ENABLED', True) and df_5m is not None and not df_5m.empty:
                    _bar_time = df_5m.index[-1].strftime("%H:%M") if hasattr(df_5m.index[-1], 'strftime') else ""
                    _cur_vol  = int(df_5m['volume'].iloc[-1]) if 'volume' in df_5m.columns else int(df_5m['Volume'].iloc[-1])
                    if _bar_time:
                        _tr_delta, _tr_reason = get_tod_rvol_score(symbol, _bar_time, _cur_vol, direction)
                        if _tr_delta:
                            filter_result.final_score = min(100.0, filter_result.final_score + _tr_delta)
                            logger.debug(f"{symbol}: TOD_RVOL {_tr_delta:+.0f} {_tr_reason}")

                # 6. Sortino-based dynamic sizing (Bridgewater / Citadel)
                if getattr(config, 'SORTINO_SIZING_ENABLED', True):
                    _sort_mult = get_sortino_size_multiplier(symbol)
                    if _sort_mult != 1.0:
                        combined_size = round(combined_size * _sort_mult, 2)
                        logger.debug(f"{symbol}: SORTINO size {_sort_mult:.2f}x")

            except Exception as _inst_e:
                logger.debug(f"[suppressed] institutional_strategies: {_inst_e}")

            # ── Cross-asset risk filter (v22.0) — VIX + bonds + dollar macro overlay ──
            if getattr(config, 'CROSS_ASSET_ENABLED', True):
                try:
                    from cross_asset_signals import get_market_risk_score
                    _ca_delta, _ca_reason = get_market_risk_score()
                    if _ca_delta != 0.0:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ca_delta)
                        logger.debug(f"{symbol}: CROSS_ASSET {_ca_delta:+.0f} {_ca_reason}")
                except Exception as _ca_e:
                    logger.debug(f"[suppressed] cross_asset: {_ca_e}")

            # ── News sentiment (v22.0) — NewsAPI keyword scoring per symbol ────────
            if getattr(config, 'NEWS_SENTIMENT_ENABLED', True):
                try:
                    from news_sentiment import get_news_sentiment
                    _ns_delta, _ns_reason = get_news_sentiment(symbol)
                    if _ns_delta != 0.0:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ns_delta)
                        logger.debug(f"{symbol}: NEWS_SENTIMENT {_ns_delta:+.0f} {_ns_reason}")
                except Exception as _ns_e:
                    logger.debug(f"[suppressed] news_sentiment: {_ns_e}")

            # ── PEAD SIGNAL (v23.0) — earnings_calendar.py Post-Earnings Drift ──
            if getattr(config, 'PEAD_SIGNAL_ENABLED', True):
                try:
                    from earnings_calendar import get_pead_score
                    _pead_delta, _pead_reason = get_pead_score(symbol, direction)
                    if _pead_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _pead_delta)
                        logger.debug(f"{symbol}: PEAD {_pead_delta:+.0f} {_pead_reason}")
                except Exception as _pe:
                    logger.debug(f"[suppressed] pead: {_pe}")

            # ── OPTIONS INTENSITY (v23.0) — unusual options volume signal ─────
            if getattr(config, 'OPTIONS_INTENSITY_ENABLED', True):
                try:
                    from options_intensity import get_options_intensity_score
                    _oi_delta, _oi_reason = get_options_intensity_score(symbol, direction)
                    if _oi_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _oi_delta)
                        logger.debug(f"{symbol}: OPTIONS_INTENSITY {_oi_delta:+.0f} {_oi_reason}")
                except Exception as _oi_e:
                    logger.debug(f"[suppressed] options_intensity: {_oi_e}")

            # ── GAP SCANNER (v23.0) — pre-market gap classification ───────────
            if getattr(config, 'GAP_SCANNER_ENABLED', True):
                try:
                    from premarket_gap_scanner import get_gap_score
                    _gap_delta, _gap_reason = get_gap_score(symbol, direction)
                    if _gap_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _gap_delta)
                        logger.debug(f"{symbol}: GAP {_gap_delta:+.0f} {_gap_reason}")
                except Exception as _gp_e:
                    logger.debug(f"[suppressed] gap_scanner: {_gp_e}")

            # ── ALTERNATIVE DATA INTELLIGENCE (v24.0) ─────────────────────────
            # SEC insider trades
            if getattr(config, 'INSIDER_INTELLIGENCE_ENABLED', True):
                try:
                    from insider_intelligence import get_insider_signal
                    _ins_delta, _ins_reason = get_insider_signal(symbol, direction)
                    if _ins_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ins_delta)
                        logger.debug(f"{symbol}: INSIDER {_ins_delta:+.0f} {_ins_reason}")
                except Exception as _ie: logger.debug(f"[suppressed] insider: {_ie}")

            # Gamma Exposure
            if getattr(config, 'GEX_ENABLED', True):
                try:
                    from gex_calculator import get_gex_signal
                    _gex_delta, _gex_reason = get_gex_signal(symbol, float(ltp_now), direction)
                    if _gex_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _gex_delta)
                        logger.debug(f"{symbol}: GEX {_gex_delta:+.0f} {_gex_reason}")
                except Exception as _ge: logger.debug(f"[suppressed] gex: {_ge}")

            # Crowd sentiment
            if getattr(config, 'CROWD_SENTIMENT_ENABLED', True):
                try:
                    from crowd_sentiment import get_crowd_sentiment
                    _cs_delta, _cs_reason = get_crowd_sentiment(symbol, direction)
                    if _cs_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _cs_delta)
                        logger.debug(f"{symbol}: CROWD {_cs_delta:+.0f} {_cs_reason}")
                except Exception as _cse: logger.debug(f"[suppressed] crowd: {_cse}")

            # Fama-French factors
            if getattr(config, 'FF_FACTORS_ENABLED', True):
                try:
                    from ff_factors import get_factor_signal
                    _ff_delta, _ff_reason = get_factor_signal(symbol, direction)
                    if _ff_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ff_delta)
                        logger.debug(f"{symbol}: FF_FACTOR {_ff_delta:+.0f} {_ff_reason}")
                except Exception as _ffe: logger.debug(f"[suppressed] ff: {_ffe}")

            # Congressional trading
            if getattr(config, 'CONGRESSIONAL_ALPHA_ENABLED', True):
                try:
                    from congressional_alpha import get_congressional_signal
                    _cong_delta, _cong_reason = get_congressional_signal(symbol, direction)
                    if _cong_delta:
                        filter_result.final_score = min(100.0, filter_result.final_score + _cong_delta)
                        logger.debug(f"{symbol}: CONGRESS {_cong_delta:+.0f} {_cong_reason}")
                except Exception as _conge: logger.debug(f"[suppressed] congress: {_conge}")

            # ── PREMIUM SCANNER (v11.0) — Free equivalents of paid tools ───────
            try:
                from premium_scanner import (
                    get_short_squeeze_score, get_options_flow_score,
                    get_sector_rotation_score, get_float_squeeze_score,
                    get_earnings_edge_score, get_dark_pool_score,
                )

                if getattr(config, 'SHORT_SQUEEZE_SCANNER_ENABLED', True):
                    _sq, _sqr = get_short_squeeze_score(symbol, direction)
                    if _sq:
                        filter_result.final_score = min(100.0, filter_result.final_score + _sq)
                        logger.debug(f"{symbol}: SHORT_SQUEEZE {_sq:+.0f} {_sqr}")

                if getattr(config, 'OPTIONS_FLOW_SCANNER_ENABLED', True):
                    _of, _ofr = get_options_flow_score(symbol, direction)
                    if _of is None:
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: options flow says skip — {_ofr}")
                        return None
                    if _of:
                        filter_result.final_score = min(100.0, filter_result.final_score + _of)
                        logger.debug(f"{symbol}: OPTIONS_FLOW {_of:+.0f} {_ofr}")

                if getattr(config, 'SECTOR_ROTATION_ENABLED', True):
                    _sr, _srr = get_sector_rotation_score(symbol, direction)
                    if _sr:
                        filter_result.final_score = min(100.0, filter_result.final_score + _sr)
                        logger.debug(f"{symbol}: SECTOR_ROT {_sr:+.0f} {_srr}")

                if getattr(config, 'FLOAT_SQUEEZE_ENABLED', True):
                    _fs, _fsr = get_float_squeeze_score(symbol, direction)
                    if _fs:
                        filter_result.final_score = min(100.0, filter_result.final_score + _fs)
                        logger.debug(f"{symbol}: FLOAT_SQZ {_fs:+.0f} {_fsr}")

                if getattr(config, 'EARNINGS_EDGE_ENABLED', True):
                    _ee, _eer = get_earnings_edge_score(symbol)
                    if _ee is None:
                        logger.debug(f"{symbol}: earnings tomorrow — skipping booster only, trade allowed")
                        _ee = 0.0  # skip booster; Gate 15 (EARNINGS_BLACKOUT) handles the hard block
                    if _ee:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ee)
                        logger.debug(f"{symbol}: EARNINGS_EDGE {_ee:+.0f} {_eer}")

                if getattr(config, 'DARK_POOL_SCANNER_ENABLED', True) and df_5m is not None and not df_5m.empty:
                    _dp, _dpr = get_dark_pool_score(symbol, df_5m, direction)
                    if _dp:
                        filter_result.final_score = min(100.0, filter_result.final_score + _dp)
                        logger.debug(f"{symbol}: DARK_POOL {_dp:+.0f} {_dpr}")

            except Exception as _prem_e:
                logger.debug(f"[suppressed] premium_scanner: {_prem_e}")

            # ── QUANTUM STRATEGIES (v12.0) — 8 institutional modules ──────────
            try:
                from quantum_strategies import (
                    get_orb_score, get_vwap_deviation_score,
                    get_market_profile_score, get_order_flow_score,
                    get_gamma_squeeze_score, get_zscore_reversion_score,
                    get_pead_score, get_momentum_persistence_score,
                )

                if getattr(config, 'ORB_SCORE_ENABLED', True) and df_5m is not None and not df_5m.empty:
                    _orb_d, _orb_r = get_orb_score(symbol, df_5m, direction, ltp_now)
                    if _orb_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _orb_d)
                        logger.debug(f"{symbol}: ORB {_orb_d:+.0f} {_orb_r}")

                if getattr(config, 'VWAP_BANDS_ENABLED', True) and df_5m is not None and not df_5m.empty:
                    _vb_d, _vb_r = get_vwap_deviation_score(symbol, df_5m, direction, ind.vwap if ind.vwap > 0 else ltp_now)
                    if _vb_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _vb_d)
                        logger.debug(f"{symbol}: VWAP_BANDS {_vb_d:+.0f} {_vb_r}")

                if getattr(config, 'MARKET_PROFILE_ENABLED', True) and df_5m is not None and not df_5m.empty:
                    _mp_d, _mp_r = get_market_profile_score(symbol, df_5m, direction, ltp_now)
                    if _mp_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _mp_d)
                        logger.debug(f"{symbol}: MKT_PROFILE {_mp_d:+.0f} {_mp_r}")

                if getattr(config, 'ORDER_FLOW_ENABLED', True) and df_5m is not None and not df_5m.empty:
                    _of_d, _of_r = get_order_flow_score(symbol, df_5m, direction)
                    if _of_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _of_d)
                        logger.debug(f"{symbol}: ORDER_FLOW {_of_d:+.0f} {_of_r}")

                if getattr(config, 'GAMMA_SQUEEZE_ENABLED', True):
                    _gm_d, _gm_r = get_gamma_squeeze_score(symbol, direction, ltp_now)
                    if _gm_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _gm_d)
                        logger.debug(f"{symbol}: GAMMA_SQZ {_gm_d:+.0f} {_gm_r}")

                if getattr(config, 'ZSCORE_REVERSION_ENABLED', True):
                    _zs_d, _zs_r = get_zscore_reversion_score(symbol, direction, ltp_now)
                    if _zs_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _zs_d)
                        logger.debug(f"{symbol}: ZSCORE {_zs_d:+.0f} {_zs_r}")

                if getattr(config, 'PEAD_SCORE_ENABLED', True):
                    _pe_d, _pe_r = get_pead_score(symbol, direction)
                    if _pe_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _pe_d)
                        logger.debug(f"{symbol}: PEAD {_pe_d:+.0f} {_pe_r}")

                if getattr(config, 'MOMENTUM_PERSIST_ENABLED', True):
                    _mom_d, _mom_r = get_momentum_persistence_score(symbol, direction)
                    if _mom_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _mom_d)
                        logger.debug(f"{symbol}: MOM_PERSIST {_mom_d:+.0f} {_mom_r}")

            except Exception as _quantum_e:
                logger.debug(f"[suppressed] quantum_strategies: {_quantum_e}")

            # ── HARMONIC PATTERNS (world-class) — Gartley/Butterfly/Bat/Crab ──
            try:
                if getattr(config, 'HARMONIC_PATTERNS_ENABLED', True) and df_5m is not None and len(df_5m) >= 30:
                    from harmonic_patterns import get_harmonic_detector
                    _hd = get_harmonic_detector()
                    _harm_results = []
                    for _detect_fn in [_hd.detect_abcd, _hd.detect_gartley, _hd.detect_butterfly, _hd.detect_bat, _hd.detect_crab]:
                        try:
                            _hr = _detect_fn(df_5m)
                            if _hr is not None:
                                _harm_results.append(_hr)
                        except Exception:
                            pass
                    # Score: aligning patterns give +pts, opposing patterns penalize
                    for _hr in _harm_results:
                        if _hr.direction == direction:
                            _h_delta = min(15.0, _hr.confidence * 0.15)  # max +15
                            filter_result.final_score = min(100.0, filter_result.final_score + _h_delta)
                            logger.info(f"[{format_ist_timestamp()}] {symbol}: HARMONIC {_hr.name} {_h_delta:+.0f}pts (conf={_hr.confidence:.0f})")
                        elif _hr.confidence > 75:
                            filter_result.final_score = max(0.0, filter_result.final_score - 8.0)
                            logger.debug(f"{symbol}: HARMONIC OPPOSE {_hr.name} -8pts")
            except Exception as _harm_e:
                logger.debug(f"[suppressed] harmonic_patterns: {_harm_e}")

            # ── WYCKOFF + SMART MONEY ADVANCED (world-class) ─────────────────
            try:
                if getattr(config, 'WYCKOFF_ENABLED', True) and df_5m is not None and len(df_5m) >= 20:
                    from smart_money_advanced import (
                        detect_liquidity_sweep, detect_breaker_block,
                        detect_wyckoff_spring, detect_wyckoff_upthrust,
                        detect_wyckoff_accumulation, detect_wyckoff_distribution,
                        detect_market_maker_cycle, detect_power_of_3,
                    )
                    _sma_score = 0.0
                    _sma_hits  = []
                    _sma_fns   = [
                        detect_liquidity_sweep, detect_breaker_block,
                        detect_wyckoff_spring, detect_wyckoff_upthrust,
                        detect_wyckoff_accumulation, detect_wyckoff_distribution,
                        detect_market_maker_cycle, detect_power_of_3,
                    ]
                    for _fn in _sma_fns:
                        try:
                            _pr = _fn(df_5m)
                            if _pr is not None:
                                if _pr.direction == direction:
                                    _pts = min(12.0, _pr.confidence * 0.13)
                                    _sma_score += _pts
                                    _sma_hits.append(f"{_pr.name}(+{_pts:.0f})")
                                elif _pr.confidence > 70:
                                    _sma_score -= 6.0
                                    _sma_hits.append(f"{_pr.name}(-6 OPPOSE)")
                        except Exception:
                            pass
                    if _sma_score != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _sma_score))
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: WYCKOFF/SMA {_sma_score:+.1f}pts [{', '.join(_sma_hits[:3])}]")
            except Exception as _sma_e:
                logger.debug(f"[suppressed] smart_money_advanced: {_sma_e}")

            # ── VSA ENGINE (v14.0) — Volume Spread Analysis ───────────────────
            try:
                if getattr(config, 'VSA_ENABLED', True) and df_5m is not None and len(df_5m) >= 20:
                    from vsa_engine import get_vsa_score
                    _vsa_d, _vsa_r = get_vsa_score(symbol, df_5m, direction)
                    if _vsa_d != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _vsa_d))
                        logger.debug(f"{symbol}: VSA {_vsa_d:+.0f} {_vsa_r}")
            except Exception as _vsa_e:
                logger.debug(f"[suppressed] vsa_engine: {_vsa_e}")

            # ── INTERMARKET ANALYSIS (v14.0) — macro regime ───────────────────
            try:
                if getattr(config, 'INTERMARKET_ENABLED', True):
                    from intermarket_analysis import get_intermarket_score
                    _im_d, _im_regime, _im_reasons = get_intermarket_score(direction)
                    if _im_d != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _im_d))
                        logger.debug(f"{symbol}: INTERMARKET {_im_d:+.0f} {_im_regime}")
            except Exception as _im_e:
                logger.debug(f"[suppressed] intermarket_analysis: {_im_e}")

            # ── EVENT SCANNER (v14.0) — corporate catalysts ───────────────────
            try:
                if getattr(config, 'EVENT_SCANNER_ENABLED', True):
                    from event_scanner import get_event_score
                    _ev_d, _ev_r = get_event_score(symbol, direction)
                    if _ev_d != 0.0:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _ev_d))
                        logger.debug(f"{symbol}: EVENT {_ev_d:+.0f} {_ev_r}")
            except Exception as _ev_e:
                logger.debug(f"[suppressed] event_scanner: {_ev_e}")

            # ── REGIME-ADAPTIVE SCORE ADJUSTMENT (v11.0) ──────────────────────
            try:
                if getattr(config, 'REGIME_ADAPTIVE_ENABLED', True):
                    from regime_params import apply_regime_to_score
                    _regime_name = getattr(self, '_last_regime_name', 'UNKNOWN')
                    _reg_adj, _reg_reason = apply_regime_to_score(filter_result.final_score, _regime_name)
                    if _reg_adj != filter_result.final_score:
                        filter_result.final_score = _reg_adj
                        logger.debug(f"{symbol}: {_reg_reason}")
            except Exception as _re:
                logger.debug(f"[suppressed] regime_params: {_re}")

            # ── OPTIMAL ENTRY TIMING (v12.0) — time-of-day WR windows ─────────
            try:
                if getattr(config, 'TOD_ENTRY_TIMING_ENABLED', True):
                    from optimal_entry_timing import get_tod_entry_score, is_entry_blocked
                    _tod_blocked, _tod_block_reason = is_entry_blocked()
                    if _tod_blocked:
                        logger.debug(f"{symbol}: {_tod_block_reason} — skipping")
                        return None
                    _tod_delta, _tod_reason = get_tod_entry_score(direction)
                    if _tod_delta and _tod_delta != -99.0:
                        filter_result.final_score = min(100.0, filter_result.final_score + _tod_delta)
                        if _tod_delta != 0:
                            logger.debug(f"{symbol}: TOD {_tod_delta:+.0f} {_tod_reason}")
            except Exception as _tod_e:
                logger.debug(f"[suppressed] optimal_entry_timing: {_tod_e}")

            # ── KING KNOWLEDGE BASE (v15.0) — 8 Legendary Trading Frameworks ──────────
            # Livermore · Minervini · O'Neil · Darvas · Wyckoff · Weinstein · Turtle · Soros
            # Each framework validates the setup independently. Consensus = conviction.
            try:
                if getattr(config, 'KNOWLEDGE_BASE_ENABLED', True):
                    from strategy_knowledge_base import compute_master_knowledge_score
                    _kb_delta, _kb_reasons = compute_master_knowledge_score(
                        symbol       = symbol,
                        direction    = direction,
                        df_5m        = df_5m,
                        volume_ratio = ind.volume_ratio,
                        rsi          = ind.rsi,
                        atr          = ind.atr,
                    )
                    if _kb_delta != 0.0:
                        filter_result.final_score = max(0.0, min(100.0,
                            filter_result.final_score + _kb_delta))
                        for _r in _kb_reasons:
                            logger.debug(f"{symbol}: KB {_kb_delta:+.1f} — {_r}")
                        if abs(_kb_delta) >= 8.0:
                            logger.info(
                                f"[{format_ist_timestamp()}] KING KB {symbol}: "
                                f"{_kb_delta:+.1f}pts | {' | '.join(_kb_reasons[:3])}"
                            )
            except Exception as _kb_e:
                logger.debug(f"[suppressed] knowledge_base: {_kb_e}")

            # ── ADVANCED SETUPS (v18.0) — ORB, FPE9, VWAP Band, Breadth, Gap ──
            try:
                from advanced_setups import get_orb_score, get_first_pullback_score, get_vwap_band_score
                from market_breadth import get_breadth_score
                from premarket_analyzer import get_gap_score

                _vwap_val = getattr(ind, 'vwap', 0.0) or 0.0

                # Opening Range Breakout
                if getattr(config, 'ORB_ENABLED', True):
                    _orb_d, _orb_r = get_orb_score(df_5m, ltp_now, direction, symbol, ind.volume_ratio)
                    if _orb_d:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _orb_d))
                        logger.debug(f"{symbol}: ORB {_orb_d:+.0f} {_orb_r}")

                # First Pullback to EMA9
                if getattr(config, 'FPE9_ENABLED', True):
                    _fpe_d, _fpe_r = get_first_pullback_score(df_5m, ltp_now, direction)
                    if _fpe_d:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _fpe_d))
                        logger.debug(f"{symbol}: FPE9 {_fpe_d:+.0f} {_fpe_r}")

                # VWAP Band Bounce
                if getattr(config, 'VBB_ENABLED', True) and df_5m is not None:
                    _vbb_d, _vbb_r = get_vwap_band_score(df_5m, ltp_now, direction, _vwap_val)
                    if _vbb_d:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _vbb_d))
                        logger.debug(f"{symbol}: VBB {_vbb_d:+.0f} {_vbb_r}")

                # Market Breadth
                if getattr(config, 'MARKET_BREADTH_ENABLED', True):
                    _mb_d, _mb_r = get_breadth_score(direction)
                    if _mb_d:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _mb_d))
                        logger.debug(f"{symbol}: BREADTH {_mb_d:+.0f} {_mb_r}")

                # Pre-market Gap
                if getattr(config, 'PREMARKET_FILTER_ENABLED', True):
                    _gap_d, _gap_r = get_gap_score(symbol, direction, ltp_now)
                    if _gap_d:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _gap_d))
                        logger.debug(f"{symbol}: GAP {_gap_d:+.0f} {_gap_r}")

            except Exception as _adv_e:
                logger.debug(f"[suppressed] advanced_setups: {_adv_e}")

            # ── Session Momentum — adapt to what's working this session ────────────────
            try:
                if getattr(config, "SESSION_MOMENTUM_ENABLED", True):
                    from session_momentum import get_session_momentum
                    _sm = get_session_momentum()
                    if _sm.should_pause():
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: Session 0/4 — pausing (session momentum hostile)")
                        return None
                    _sm_score_adj = _sm.get_score_adjustment()
                    _sm_effective_min = config.MIN_SIGNAL_SCORE + _sm_score_adj
                    if filter_result.final_score < _sm_effective_min:
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: Below session-adjusted min {_sm_effective_min:.0f} — skipping")
                        return None
                    combined_size = round(combined_size * _sm.get_size_multiplier(), 2)
            except Exception as _sme:
                logger.debug(f"[suppressed] session_momentum: {_sme}")

            # ── LLM Reasoning Gate (70+ score) — runs AFTER all adjustments ──────────
            if filter_result.final_score >= 70:
                try:
                    from llm_reasoner import get_llm_reasoner
                    _sl_dist = config.ATR_SL_MULTIPLIER * ind.atr
                    _t2_dist = config.ATR_TP_MULTIPLIER * ind.atr
                    rr = round(_t2_dist / max(_sl_dist, 0.01), 2)
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
                        "time_et":      get_current_et_time().strftime("%H:%M"),
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

            # ── MICROSTRUCTURE SIGNALS (v25.0) ── 8 additional institutional signals ─
            try:
                from microstructure_signals import (
                    get_orb_quality_score, get_52w_proximity_score,
                    get_float_momentum_score, get_tick_divergence_score,
                    get_zscore_mean_reversion, get_candle_streak_score,
                    get_premarket_vol_score, get_correlation_filter_score,
                )

                if getattr(config, 'ORB_QUALITY_ENABLED', True) and df_5m is not None:
                    _oq, _oqr = get_orb_quality_score(symbol, df_5m, direction, ltp_now)
                    if _oq:
                        filter_result.final_score = min(100.0, filter_result.final_score + _oq)
                        logger.debug(f"{symbol}: ORB_QUALITY {_oq:+.0f} {_oqr}")

                if getattr(config, 'W52_PROXIMITY_ENABLED', True):
                    _w52, _w52r = get_52w_proximity_score(symbol, direction)
                    if _w52:
                        filter_result.final_score = min(100.0, filter_result.final_score + _w52)
                        logger.debug(f"{symbol}: 52W_PROX {_w52:+.0f} {_w52r}")

                if getattr(config, 'FLOAT_MOMENTUM_ENABLED', True):
                    _fm, _fmr = get_float_momentum_score(symbol, direction)
                    if _fm:
                        filter_result.final_score = min(100.0, filter_result.final_score + _fm)
                        logger.debug(f"{symbol}: FLOAT_MOM {_fm:+.0f} {_fmr}")

                if getattr(config, 'TICK_DIVERGENCE_ENABLED', True) and df_5m is not None:
                    _td, _tdr = get_tick_divergence_score(df_5m, direction)
                    if _td:
                        filter_result.final_score = min(100.0, filter_result.final_score + _td)
                        logger.debug(f"{symbol}: TICK_DIV {_td:+.0f} {_tdr}")

                if getattr(config, 'ZSCORE_MR_ENABLED', True) and df_5m is not None:
                    _zs, _zsr = get_zscore_mean_reversion(df_5m, direction)
                    if _zs:
                        filter_result.final_score = min(100.0, filter_result.final_score + _zs)
                        logger.debug(f"{symbol}: ZSCORE_MR {_zs:+.0f} {_zsr}")

                if getattr(config, 'CANDLE_STREAK_ENABLED', True) and df_5m is not None:
                    _cs, _csr = get_candle_streak_score(df_5m, direction)
                    if _cs:
                        filter_result.final_score = min(100.0, filter_result.final_score + _cs)
                        logger.debug(f"{symbol}: CANDLE_STREAK {_cs:+.0f} {_csr}")

                if getattr(config, 'PREMARKET_VOL_ENABLED', True):
                    _pmv, _pmvr = get_premarket_vol_score(symbol, direction)
                    if _pmv:
                        filter_result.final_score = min(100.0, filter_result.final_score + _pmv)
                        logger.debug(f"{symbol}: PM_VOL {_pmv:+.0f} {_pmvr}")

                if getattr(config, 'CORRELATION_FILTER_ENABLED', True):
                    _cf, _cfr = get_correlation_filter_score(symbol, direction)
                    if _cf:
                        filter_result.final_score = min(100.0, filter_result.final_score + _cf)
                        logger.debug(f"{symbol}: CORR_FILTER {_cf:+.0f} {_cfr}")

            except Exception as _micro_e:
                logger.debug(f"[suppressed] microstructure_signals: {_micro_e}")

            # ── SEASONALITY + MULTI-MOMENTUM + LIQUIDITY (v26.0) ─────────────────
            try:
                from seasonality_signals import (
                    get_opex_score, get_month_end_score, get_quarter_end_score,
                    get_monday_fade_score, get_opex_pin_score,
                )
                if getattr(config, 'SEASONALITY_ENABLED', True):
                    _seas_pairs = [
                        (get_opex_score(direction), 'OPEX'),
                        (get_month_end_score(direction), 'MONTH_END'),
                        (get_quarter_end_score(direction), 'QTR_END'),
                        (get_monday_fade_score(direction, ltp_now, ltp_now), 'MON_FADE'),
                        (get_opex_pin_score(symbol, ltp_now), 'OPEX_PIN'),
                    ]
                    for (_d, _r), _tag in _seas_pairs:
                        if _d:
                            filter_result.final_score = min(100.0, filter_result.final_score + _d)
                            logger.debug(f"{symbol}: {_tag} {_d:+.0f} {_r}")
            except Exception as _seas_e:
                logger.debug(f"[suppressed] seasonality: {_seas_e}")

            try:
                from momentum_multi import get_multi_momentum_score
                if getattr(config, 'MULTI_MOMENTUM_ENABLED', True):
                    _mm, _mmr = get_multi_momentum_score(symbol, direction)
                    if _mm:
                        filter_result.final_score = min(100.0, filter_result.final_score + _mm)
                        logger.debug(f"{symbol}: MULTI_MOM {_mm:+.0f} {_mmr}")
            except Exception as _mme:
                logger.debug(f"[suppressed] multi_momentum: {_mme}")

            try:
                from liquidity_signals import (
                    get_amihud_score, get_spread_score,
                    get_kyle_lambda_score, get_volume_clock_score,
                )
                if getattr(config, 'LIQUIDITY_SIGNALS_ENABLED', True) and df_5m is not None and not df_5m.empty:
                    _liq_data = [
                        (get_amihud_score(symbol, df_5m, direction), 'AMIHUD'),
                        (get_spread_score(df_5m, ltp_now), 'SPREAD'),
                        (get_kyle_lambda_score(df_5m, direction), 'KYLE_L'),
                        (get_volume_clock_score(df_5m, direction), 'VOL_CLK'),
                    ]
                    for (_d, _r), _tag in _liq_data:
                        if _d:
                            filter_result.final_score = min(100.0, filter_result.final_score + _d)
                            logger.debug(f"{symbol}: {_tag} {_d:+.0f} {_r}")
            except Exception as _liqe:
                logger.debug(f"[suppressed] liquidity: {_liqe}")

            # ── RENAISSANCE MEDALLION STRATEGIES (v27.0) ─────────────────────────
            # IC tracker adaptive weighting, PCA alpha, event alpha (analyst/
            # dividend/split), STL trend decomposition, execution timing,
            # and market impact check. All fail-open.
            try:
                from ic_tracker import get_signal_weight
                from pca_alpha import get_pca_alpha_score
                from event_alpha import get_analyst_signal, get_dividend_capture_score, get_split_signal
                from stl_signals import get_stl_trend_score
                from market_impact import get_market_impact_score, get_execution_timing_score

                _watchlist_v27 = getattr(self, '_open_position_symbols', []) or []

                # 1. PCA alpha: trade idiosyncratic moves, not market noise
                if getattr(config, 'PCA_ALPHA_ENABLED', True):
                    _pca_d, _pca_r = get_pca_alpha_score(symbol, direction, _watchlist_v27 or [symbol])
                    _pca_w = get_signal_weight("pca_alpha", 1.0)
                    _pca_d = round(_pca_d * _pca_w, 2)
                    if _pca_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _pca_d)
                        logger.debug(f"{symbol}: PCA_ALPHA {_pca_d:+.1f} {_pca_r} (IC_w={_pca_w:.2f})")

                # 2. Event alpha: analyst upgrades, dividend capture, splits
                if getattr(config, 'EVENT_ALPHA_ENABLED', True):
                    _an_d, _an_r = get_analyst_signal(symbol, direction)
                    _an_w = get_signal_weight("analyst_signal", 1.0)
                    _an_d = round(_an_d * _an_w, 2)
                    if _an_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _an_d)
                        logger.debug(f"{symbol}: EVENT_ANALYST {_an_d:+.1f} {_an_r}")

                    _div_d, _div_r = get_dividend_capture_score(symbol, direction)
                    _div_w = get_signal_weight("dividend_capture", 1.0)
                    _div_d = round(_div_d * _div_w, 2)
                    if _div_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _div_d)
                        logger.debug(f"{symbol}: EVENT_DIV {_div_d:+.1f} {_div_r}")

                    _spl_d, _spl_r = get_split_signal(symbol, direction)
                    _spl_w = get_signal_weight("split_signal", 1.0)
                    _spl_d = round(_spl_d * _spl_w, 2)
                    if _spl_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _spl_d)
                        logger.debug(f"{symbol}: EVENT_SPLIT {_spl_d:+.1f} {_spl_r}")

                # 3. STL trend decomposition: trade the trend, not the noise
                if getattr(config, 'STL_ENABLED', True) and df_5m is not None and not df_5m.empty:
                    _stl_d, _stl_r = get_stl_trend_score(df_5m, direction)
                    _stl_w = get_signal_weight("stl_trend", 1.0)
                    _stl_d = round(_stl_d * _stl_w, 2)
                    if _stl_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _stl_d)
                        logger.debug(f"{symbol}: STL_TREND {_stl_d:+.1f} {_stl_r}")

                # 4. Execution timing: avoid open chaos and close MOC flow
                if getattr(config, 'EXECUTION_TIMING_ENABLED', True):
                    _et_d, _et_r = get_execution_timing_score(direction)
                    if _et_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _et_d)
                        logger.debug(f"{symbol}: EXEC_TIMING {_et_d:+.1f} {_et_r}")

                # 5. Market impact check (Almgren-Chriss): penalize oversized orders
                if getattr(config, 'MARKET_IMPACT_ENABLED', True) and combined_size > 0:
                    _notional = combined_size * ltp_now * 10  # approx shares (10 = unit)
                    _mi_d, _mi_r = get_market_impact_score(symbol, int(_notional / max(ltp_now, 0.01)), ltp_now, direction)
                    if _mi_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _mi_d)
                        logger.debug(f"{symbol}: MKT_IMPACT {_mi_d:+.1f} {_mi_r}")

            except Exception as _rene:
                logger.debug(f"[suppressed] renaissance_v27: {_rene}")

            # ── TOP 0.1% SIGNALS (v28.0) ─────────────────────────────────────────
            # HAR-RV volatility sizing, tape OFI, synthetic L2, dark pool,
            # alt data (Google Trends/Wikipedia/Reddit), EDGAR NLP,
            # CBOE P/C + VIX term structure, beta-neutral sizing, portfolio covariance
            try:
                # 1. HAR-RV volatility size adjustment (replaces/enhances GARCH)
                if getattr(config, 'HAR_RV_ENABLED', True) and df_5m is not None:
                    from har_rv import get_har_rv_forecast, get_har_size_multiplier
                    _rv_sigma, _rv_regime = get_har_rv_forecast(symbol, df_5m)
                    _rv_mult = get_har_size_multiplier(_rv_sigma)
                    if _rv_mult != 1.0:
                        combined_size = max(0.25, min(3.0, round(combined_size * _rv_mult, 2)))
                        logger.debug(f"{symbol}: HAR_RV {_rv_regime}(σ={_rv_sigma:.1f}%) → {_rv_mult:.2f}x size")

                # 2. Real-time tape OFI (Lee-Ready order flow imbalance)
                if getattr(config, 'TAPE_OFI_ENABLED', True):
                    from tape_classifier import get_tape_classifier
                    _tape_d, _tape_r = get_tape_classifier().get_ofi_score(symbol, direction)
                    if _tape_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _tape_d)
                        logger.debug(f"{symbol}: TAPE_OFI {_tape_d:+.0f} {_tape_r}")

                # 3. Synthetic Level-2 depth score
                if getattr(config, 'SYNTHETIC_L2_ENABLED', True) and df_5m is not None:
                    from synthetic_l2 import get_synthetic_depth_score
                    _l2_d, _l2_r = get_synthetic_depth_score(symbol, ltp_now, direction, df_5m)
                    if _l2_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _l2_d)
                        logger.debug(f"{symbol}: SYN_L2 {_l2_d:+.0f} {_l2_r}")

                # 4. Dark pool activity proxy
                if getattr(config, 'DARK_POOL_ENABLED', True) and df_5m is not None:
                    from dark_pool_proxy import get_dark_pool_score
                    _dp_d, _dp_r = get_dark_pool_score(symbol, df_5m, direction)
                    if _dp_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _dp_d)
                        logger.debug(f"{symbol}: DARK_POOL {_dp_d:+.0f} {_dp_r}")

                # 5. Alternative data: Google Trends + Wikipedia + Reddit
                if getattr(config, 'ALT_DATA_ENABLED', True):
                    from alt_data_engine import get_google_trends_score, get_wikipedia_score, get_reddit_score
                    _gt_d, _gt_r = get_google_trends_score(symbol, symbol, direction)
                    if _gt_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _gt_d)
                        logger.debug(f"{symbol}: GTRENDS {_gt_d:+.0f} {_gt_r}")
                    _wiki_d, _wiki_r = get_wikipedia_score(symbol, direction)
                    if _wiki_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _wiki_d)
                        logger.debug(f"{symbol}: WIKI {_wiki_d:+.0f} {_wiki_r}")
                    _red_d, _red_r = get_reddit_score(symbol, direction)
                    if _red_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _red_d)
                        logger.debug(f"{symbol}: REDDIT {_red_d:+.0f} {_red_r}")

                # 6. EDGAR 8-K sentiment (NLP on recent SEC filings)
                if getattr(config, 'EDGAR_SENTIMENT_ENABLED', True):
                    from edgar_sentiment import get_edgar_sentiment_score
                    _edg_d, _edg_r = get_edgar_sentiment_score(symbol, direction)
                    if _edg_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _edg_d)
                        logger.debug(f"{symbol}: EDGAR {_edg_d:+.0f} {_edg_r}")

                # 7. CBOE put/call ratio + VIX term structure
                if getattr(config, 'CBOE_DATA_ENABLED', True):
                    from cboe_data import get_pc_ratio_score, get_vix_term_structure_score
                    _pc_d, _pc_r = get_pc_ratio_score(direction)
                    if _pc_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _pc_d)
                        logger.debug(f"{symbol}: CBOE_PC {_pc_d:+.0f} {_pc_r}")
                    _vts_d, _vts_r = get_vix_term_structure_score(direction)
                    if _vts_d and abs(_vts_d) >= 2:
                        filter_result.final_score = min(100.0, filter_result.final_score + _vts_d)
                        logger.debug(f"{symbol}: VIX_TERM {_vts_d:+.0f} {_vts_r}")

                # 8. Beta-neutral sizing
                if getattr(config, 'BETA_NEUTRAL_ENABLED', True):
                    from beta_manager import get_beta_size_multiplier, get_portfolio_beta
                    _open_pos_list = list(getattr(self, '_open_positions', {}).values()) \
                                     if hasattr(self, '_open_positions') else []
                    _port_beta = get_portfolio_beta(_open_pos_list)
                    _beta_mult = get_beta_size_multiplier(symbol, df_5m, _port_beta)
                    if _beta_mult != 1.0:
                        combined_size = max(0.25, min(3.0, round(combined_size * _beta_mult, 2)))
                        logger.debug(f"{symbol}: BETA_NEUTRAL {_beta_mult:.2f}x (port_β={_port_beta:.1f})")

                # 9. Portfolio covariance optimizer (Ledoit-Wolf)
                if getattr(config, 'COV_OPTIMIZER_ENABLED', True):
                    from covariance_optimizer import get_portfolio_size_multiplier
                    _open_syms = list(getattr(self, '_open_positions', {}).keys()) \
                                 if hasattr(self, '_open_positions') else []
                    if _open_syms:
                        _cov_mult = get_portfolio_size_multiplier(symbol, direction, _open_syms)
                        if _cov_mult != 1.0:
                            combined_size = max(0.25, min(3.0, round(combined_size * _cov_mult, 2)))
                            logger.debug(f"{symbol}: COV_OPT {_cov_mult:.2f}x (corr to {len(_open_syms)} positions)")

            except Exception as _top01_e:
                logger.debug(f"[suppressed] top01_v28: {_top01_e}")

            # ── TOP 1% SIGNALS (v29.0) ───────────────────────────────────────────
            # PEAD drift, regime-adaptive weights, VaR sizing, correlation crisis,
            # TWAP flag for large orders. All fail-open.
            try:
                # 1. PEAD: Post-Earnings Announcement Drift (Ball & Brown 1968 anomaly)
                if getattr(config, 'PEAD_ENGINE_ENABLED', True):
                    from pead_engine import get_pead_score
                    _pead_d, _pead_r = get_pead_score(symbol, direction)
                    if _pead_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _pead_d)
                        logger.debug(f"{symbol}: PEAD {_pead_d:+.0f} {_pead_r}")

                # 2. Regime-adaptive signal routing: re-weight based on market regime
                if getattr(config, 'REGIME_ROUTER_ENABLED', True):
                    from regime_signal_router import detect_regime, get_regime_size_multiplier
                    _cur_regime = detect_regime()
                    _regime_size = get_regime_size_multiplier(_cur_regime)
                    if _regime_size != 1.0:
                        combined_size = max(0.25, min(3.0, round(combined_size * _regime_size, 2)))
                        logger.debug(f"{symbol}: REGIME_ROUTER {_cur_regime} → {_regime_size:.2f}x size")

                # 3. VaR-based position sizing (Historical Simulation)
                if getattr(config, 'INTRADAY_VAR_ENABLED', True) and df_5m is not None:
                    from intraday_var import get_var_size_multiplier
                    _open_ct = len(getattr(self, '_open_positions', {}).keys() if hasattr(self, '_open_positions') else [])
                    _day_cap = getattr(config, 'MAX_DAILY_CAPITAL', 50000.0)
                    _loss_pct = getattr(config, 'DAILY_LOSS_LIMIT_PCT', 2.0)
                    _var_mult, _var_r = get_var_size_multiplier(
                        symbol, df_5m, float(_day_cap), float(_loss_pct), _open_ct
                    )
                    if _var_mult != 1.0:
                        combined_size = max(0.25, min(3.0, round(combined_size * _var_mult, 2)))
                        logger.debug(f"{symbol}: VAR_SIZE {_var_r}")

                # 4. Correlation crisis circuit breaker
                if getattr(config, 'CORRELATION_CRISIS_ENABLED', True):
                    from correlation_crisis import get_crisis_size_multiplier
                    _watchlist_for_corr = getattr(self, '_current_watchlist', []) or []
                    _crisis_mult, _crisis_r = get_crisis_size_multiplier(
                        _watchlist_for_corr[:20], self.data_fetcher
                    )
                    if _crisis_mult < 1.0:
                        combined_size = max(0.25, round(combined_size * _crisis_mult, 2))
                        logger.debug(f"{symbol}: CORR_CRISIS {_crisis_r}")

            except Exception as _top1_e:
                logger.debug(f"[suppressed] top1_v29: {_top1_e}")

            # ── Enforce booster cap: clamp total booster contribution to +50 ────
            # Raised to +50 for v28.0 Top 0.1% modules (11 new signal sources)
            _BOOSTER_MAX_DELTA = 50.0
            _booster_delta = filter_result.final_score - _booster_base_score
            if _booster_delta > _BOOSTER_MAX_DELTA:
                filter_result.final_score = min(100.0, _booster_base_score + _BOOSTER_MAX_DELTA)
                logger.debug(
                    f"{symbol}: booster cap applied — delta was {_booster_delta:+.1f}, "
                    f"clamped to +{_BOOSTER_MAX_DELTA:.0f} → score {filter_result.final_score:.1f}"
                )

            # ── Final execution gate: after ALL boosters, require ≥70 ────────────
            # Pre-filter lets 63+ through so boosters (CSM, VWAP, OFI, etc.) can
            # add 8–20 pts. If no booster fired, the signal is too weak to trade.
            _FINAL_EXEC_MIN = getattr(config, "FINAL_EXEC_MIN_SCORE", 70.0)
            if filter_result.final_score < _FINAL_EXEC_MIN:
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: Final score "
                    f"{filter_result.final_score:.0f} < {_FINAL_EXEC_MIN:.0f} after all "
                    f"boosters — no conviction signal, skipping"
                )
                return None

            # ── Pre-entry transaction cost filter (v28.0) ────────────────────────
            # Skip if expected alpha < bid-ask spread + slippage (unprofitable after cost)
            if getattr(config, 'COST_FILTER_ENABLED', True):
                try:
                    from transaction_cost_model import get_cost_filter
                    _cost_ok, _cost_reason = get_cost_filter(
                        symbol,
                        filter_result.final_score,
                        int(combined_size * 100),
                        ltp_now,
                        direction,
                    )
                    if not _cost_ok:
                        logger.info(
                            f"[{format_ist_timestamp()}] {symbol}: COST_FILTER blocked — {_cost_reason}"
                        )
                        return None
                    logger.debug(f"{symbol}: COST_OK — {_cost_reason}")
                except Exception as _ce:
                    logger.debug(f"[suppressed] cost_filter: {_ce}")

            # ── ELITE FILTER (v11.0) — VIX adaptive + R/R enforcer ────────────
            try:
                from elite_filter import (
                    get_vix_regime, get_vix_score_threshold,
                    get_adx_strength, get_score_adjustment_for_regime,
                    check_reward_to_risk, calculate_optimal_levels,
                )
                # VIX regime adjustment
                _vix_val, _vix_regime, _vix_size_mult, _vix_note = get_vix_regime()
                _adj_threshold = get_vix_score_threshold(self.min_score)
                if filter_result.final_score < _adj_threshold:
                    logger.info(f"[{format_ist_timestamp()}] {symbol}: VIX-adjusted threshold {_adj_threshold:.0f} not met (score={filter_result.final_score:.0f}, VIX={_vix_val:.1f} {_vix_regime})")
                    return None

                # ADX trend strength adjustment
                if df_5m is not None and not df_5m.empty and len(df_5m) >= 20:
                    _adx, _adx_label = get_adx_strength(df_5m)
                    _regime_delta, _regime_reason = get_score_adjustment_for_regime(_adx, _vix_val, direction)
                    if _regime_delta != 0:
                        filter_result.final_score = min(100.0, filter_result.final_score + _regime_delta)
                        logger.debug(f"{symbol}: REGIME_ADJ {_regime_delta:+.0f} ADX={_adx:.0f} {_adx_label}")

            except Exception as _ef_e:
                logger.debug(f"[suppressed] elite_filter: {_ef_e}")

            # ── MEAN REVERSION ENGINE (v14.0) — 4-detector fade system ───────
            try:
                if getattr(config, 'MEAN_REVERSION_ENABLED', True) and df_5m is not None and len(df_5m) >= 30:
                    from mean_reversion import get_mean_reversion_engine
                    _mre = get_mean_reversion_engine()
                    _mr_regime = getattr(self, '_last_regime_name', 'RANGING')
                    _mr_sig = _mre._analyze_symbol(symbol, df_5m, ltp_now, _mr_regime)
                    if _mr_sig is not None:
                        if _mr_sig.direction == direction:
                            _mr_delta = min(12.0, _mr_sig.score * 0.12)
                            filter_result.final_score = min(100.0, filter_result.final_score + _mr_delta)
                            logger.info(
                                f"[{format_ist_timestamp()}] {symbol}: MEAN_REV "
                                f"{_mr_sig.strategy} {_mr_delta:+.0f}pts (score={_mr_sig.score:.0f})"
                            )
                        elif _mr_sig.score > 70:
                            filter_result.final_score = max(0.0, filter_result.final_score - 8.0)
                            logger.debug(f"{symbol}: MEAN_REV OPPOSE {_mr_sig.strategy} -8pts")
            except Exception as _mre_e:
                logger.debug(f"[suppressed] mean_reversion: {_mre_e}")

            # ── WOLFE WAVE (world-class reversal pattern) ─────────────────────
            try:
                if getattr(config, 'WOLFE_WAVE_ENABLED', True) and df_5m is not None and len(df_5m) >= 40:
                    from wolfe_wave import get_wolfe_wave_score
                    _ww_d, _ww_r = get_wolfe_wave_score(symbol, df_5m, direction)
                    if _ww_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _ww_d)
                        logger.info(f"[{format_ist_timestamp()}] {symbol}: WOLFE_WAVE {_ww_d:+.0f} {_ww_r}")
            except Exception as _ww_e:
                logger.debug(f"[suppressed] wolfe_wave: {_ww_e}")

            # ── ELLIOTT WAVE (wave 3 entry detection) ─────────────────────────
            try:
                if getattr(config, 'ELLIOTT_WAVE_ENABLED', True) and df_5m is not None and len(df_5m) >= 50:
                    from elliott_wave import get_elliott_wave_score
                    _ew_d, _ew_r = get_elliott_wave_score(symbol, df_5m, direction)
                    if _ew_d:
                        filter_result.final_score = max(0.0, min(100.0, filter_result.final_score + _ew_d))
                        logger.debug(f"{symbol}: ELLIOTT {_ew_d:+.0f} {_ew_r}")
            except Exception as _ew_e:
                logger.debug(f"[suppressed] elliott_wave: {_ew_e}")

            # ── GANN LEVELS (time-price geometry) ────────────────────────────
            try:
                if getattr(config, 'GANN_ENABLED', True) and df_5m is not None:
                    from gann_levels import get_gann_score
                    _gn_d, _gn_r = get_gann_score(symbol, df_5m, ltp_now, direction)
                    if _gn_d:
                        filter_result.final_score = min(100.0, filter_result.final_score + _gn_d)
                        logger.debug(f"{symbol}: GANN {_gn_d:+.0f} {_gn_r}")
            except Exception as _gn_e:
                logger.debug(f"[suppressed] gann_levels: {_gn_e}")

            # ── HOLLY AI RANKING (v16.0) — top-ranked symbol boost ────────────────
            try:
                if getattr(config, 'HOLLY_AI_ENABLED', True):
                    from holly_ai import get_holly_rankings
                    _watchlist_for_holly = getattr(self, '_watchlist', [symbol])
                    if _watchlist_for_holly and len(_watchlist_for_holly) >= 3:
                        _holly_ranks = get_holly_rankings(_watchlist_for_holly[:30])
                        _holly_top = [r.symbol for r in _holly_ranks[:10]]
                        _holly_rank_pos = next((i for i, r in enumerate(_holly_ranks) if r.symbol == symbol), None)
                        if _holly_rank_pos is not None:
                            if _holly_rank_pos < 3:
                                filter_result.final_score = min(100.0, filter_result.final_score + 12.0)
                                logger.info(f"{symbol}: HOLLY_AI rank #{_holly_rank_pos+1} +12")
                            elif _holly_rank_pos < 7:
                                filter_result.final_score = min(100.0, filter_result.final_score + 6.0)
                                logger.debug(f"{symbol}: HOLLY_AI rank #{_holly_rank_pos+1} +6")
                            elif _holly_rank_pos >= len(_holly_ranks) - 3:
                                filter_result.final_score = max(0.0, filter_result.final_score - 6.0)
            except Exception as _holly_e:
                logger.debug(f"[suppressed] holly_ai: {_holly_e}")

            # ── ML WIN PROBABILITY BOOST (v16.0) — GradientBoosting score modifier ──
            try:
                if getattr(config, 'ML_SCORE_BOOST_ENABLED', True):
                    from ml_signal_ranker import get_win_probability
                    _vwap_dist = abs(ltp_now - ind.vwap) / (ltp_now + 1e-9) * 100 if ind.vwap > 0 else 0.0
                    _ml_prob = get_win_probability(
                        rsi=float(ind.rsi or 50),
                        macd_hist_norm=float((ind.macd_hist or 0) / (ind.atr or 1)),
                        volume_ratio=float(ind.volume_ratio or 1.0),
                        atr_pct=float((ind.atr or 0) / (ltp_now + 1e-9) * 100),
                        signal_score=float(filter_result.final_score),
                        adx=float(ind.adx or 25),
                        ema_slope_pct=float(ind.ema_slope_pct if hasattr(ind, 'ema_slope_pct') else 0),
                        vwap_dist_pct=float(_vwap_dist),
                        bb_pct=float(ind.bb_pct if hasattr(ind, 'bb_pct') else 0.5),
                        hour_et=float(locals().get('_et_now', type('_', (), {'hour': 10})()).hour),
                        long_flag=1 if direction == 'LONG' else 0,
                    )
                    # ML boost: prob > 0.7 → +8; prob > 0.6 → +4; prob < 0.4 → -8
                    if _ml_prob >= 0.70:
                        filter_result.final_score = min(100.0, filter_result.final_score + 8.0)
                        logger.debug(f"{symbol}: ML_PROB {_ml_prob:.2f} +8")
                    elif _ml_prob >= 0.60:
                        filter_result.final_score = min(100.0, filter_result.final_score + 4.0)
                        logger.debug(f"{symbol}: ML_PROB {_ml_prob:.2f} +4")
                    elif _ml_prob < 0.40:
                        filter_result.final_score = max(0.0, filter_result.final_score - 8.0)
                        logger.debug(f"{symbol}: ML_PROB {_ml_prob:.2f} -8")
            except Exception as _ml_e:
                logger.debug(f"[suppressed] ml_signal_ranker boost: {_ml_e}")

            # ── MOMENTUM BURST DETECTOR (v16.0) — coil-and-explode setups ─────────
            try:
                if getattr(config, 'MOMENTUM_BURST_ENABLED', True) and df_5m is not None and len(df_5m) >= 30:
                    from momentum_burst import MomentumBurstDetector
                    _mb_det = MomentumBurstDetector()
                    _mb_setups = _mb_det.scan([symbol], {symbol: df_5m})
                    _mb_match = next((s for s in _mb_setups if s.symbol == symbol), None)
                    if _mb_match is not None:
                        _mb_dir = getattr(_mb_match, 'direction', direction)
                        if _mb_dir == direction:
                            _mb_score = getattr(_mb_match, 'score', 0)
                            _mb_delta = min(12.0, _mb_score * 0.12) if _mb_score > 0 else 0.0
                            if _mb_delta > 0:
                                filter_result.final_score = min(100.0, filter_result.final_score + _mb_delta)
                                logger.debug(f"{symbol}: MOMENTUM_BURST {_mb_delta:+.0f}")
            except Exception as _mb_e:
                logger.debug(f"[suppressed] momentum_burst: {_mb_e}")

            # ── RL AGENT CONFIRMATION (v16.0) — Q-learning action alignment ────────
            try:
                if getattr(config, 'RL_AGENT_ENABLED', True):
                    from rl_agent import LakshKingRL
                    _rl = LakshKingRL.get_instance() if hasattr(LakshKingRL, 'get_instance') else None
                    if _rl is None:
                        _rl = LakshKingRL()
                    _rl_action = _rl.get_signal(symbol) if hasattr(_rl, 'get_signal') else None
                    if _rl_action is not None:
                        _rl_dir = getattr(_rl_action, 'direction', None)
                        _rl_conf = getattr(_rl_action, 'confidence', 0)
                        if _rl_dir == direction and _rl_conf > 0.6:
                            filter_result.final_score = min(100.0, filter_result.final_score + 8.0)
                            logger.debug(f"{symbol}: RL_AGENT confirms {direction} conf={_rl_conf:.2f} +8")
                        elif _rl_dir is not None and _rl_dir != direction and _rl_conf > 0.7:
                            filter_result.final_score = max(0.0, filter_result.final_score - 6.0)
                            logger.debug(f"{symbol}: RL_AGENT opposes {direction} -6")
            except Exception as _rl_e:
                logger.debug(f"[suppressed] rl_agent: {_rl_e}")

            # ── IBD RS RATING (v16.0) — Relative Strength 1-99 ───────────────────
            try:
                if getattr(config, 'IBD_RS_ENABLED', True):
                    from ibd_rs_rating import get_rs_rating
                    _rs_delta, _rs_reason = get_rs_rating(symbol, direction)
                    if _rs_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _rs_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: RS_RATING {_rs_delta:+.0f} {_rs_reason}")
            except Exception as _rs_e:
                logger.debug(f"[suppressed] ibd_rs_rating: {_rs_e}")

            # ── FEAR & GREED INDEX (v16.0) — market sentiment composite ─────────
            try:
                if getattr(config, 'FEAR_GREED_ENABLED', True):
                    from fear_greed_engine import get_fear_greed_score
                    _fg_delta, _fg_reason, _fg_val = get_fear_greed_score(direction)
                    if _fg_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _fg_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: FEAR_GREED {_fg_delta:+.0f} FG={_fg_val:.0f} {_fg_reason}")
            except Exception as _fg_e:
                logger.debug(f"[suppressed] fear_greed_engine: {_fg_e}")

            # ── OPTIONS SKEW + GEX (v16.0) — SpotGamma free replica ─────────────
            try:
                if getattr(config, 'OPTIONS_SKEW_ENABLED', True):
                    from options_skew import get_skew_score, get_gex_score
                    _sk_delta, _sk_reason = get_skew_score(symbol, direction)
                    if _sk_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _sk_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: SKEW {_sk_delta:+.0f} {_sk_reason}")
                    _gex_delta, _gex_reason = get_gex_score(symbol, direction, ltp_now)
                    if _gex_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _gex_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: GEX {_gex_delta:+.0f} {_gex_reason}")
            except Exception as _sk_e:
                logger.debug(f"[suppressed] options_skew: {_sk_e}")

            # ── SOCIAL SENTIMENT (v17.0) — Reddit WSB + StockTwits ─────────────────
            try:
                if getattr(config, 'SOCIAL_SENTIMENT_ENABLED', True):
                    from social_sentiment import get_social_score
                    _ss_delta, _ss_reason = get_social_score(symbol, direction)
                    if _ss_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _ss_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: SOCIAL {_ss_delta:+.0f} {_ss_reason}")
            except Exception as _ss_e:
                logger.debug(f"[suppressed] social_sentiment: {_ss_e}")

            # ── 13F INSTITUTIONAL FLOW (v17.0) — quarterly ownership changes ────────
            try:
                if getattr(config, 'INSTITUTIONAL_FLOW_ENABLED', True):
                    from institutional_flow import get_institutional_score
                    _if_delta, _if_reason = get_institutional_score(symbol, direction)
                    if _if_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _if_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: INST_FLOW {_if_delta:+.0f} {_if_reason}")
            except Exception as _if_e:
                logger.debug(f"[suppressed] institutional_flow: {_if_e}")

            # ── ECONOMIC SURPRISE (v17.0) — FRED macro actual vs consensus ──────────
            try:
                if getattr(config, 'ECONOMIC_SURPRISE_ENABLED', True):
                    from economic_surprise import get_economic_surprise_score
                    _es_delta, _es_reason = get_economic_surprise_score(direction)
                    if _es_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _es_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: ECON_SURPRISE {_es_delta:+.0f} {_es_reason}")
            except Exception as _es_e:
                logger.debug(f"[suppressed] economic_surprise: {_es_e}")

            # ── McCLELLAN + BREADTH THRUST (v17.0) ───────────────────────────────────
            try:
                if getattr(config, 'MCCLELLAN_ENABLED', True):
                    from mcclellan_engine import get_mcclellan_score, get_breadth_thrust_score
                    _mc_delta, _mc_reason = get_mcclellan_score(direction)
                    if _mc_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _mc_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: MCCLELLAN {_mc_delta:+.0f} {_mc_reason}")
                    _bt_delta, _bt_reason = get_breadth_thrust_score(direction)
                    if _bt_delta > 0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _bt_delta, 0.0, 100.0))
                        logger.info(f"{symbol}: BREADTH_THRUST {_bt_delta:+.0f} {_bt_reason}")
            except Exception as _mc_e:
                logger.debug(f"[suppressed] mcclellan_engine: {_mc_e}")

            # ── SUPPLY/DEMAND ZONES (v17.0) — Sam Seiden methodology ─────────────────
            try:
                if getattr(config, 'SUPPLY_DEMAND_ENABLED', True) and df_5m is not None and len(df_5m) >= 50:
                    from supply_demand_zones import get_supply_demand_score
                    _sdz_delta, _sdz_reason = get_supply_demand_score(symbol, df_5m, direction, ltp_now)
                    if _sdz_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _sdz_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: SDZ {_sdz_delta:+.0f} {_sdz_reason}")
            except Exception as _sdz_e:
                logger.debug(f"[suppressed] supply_demand_zones: {_sdz_e}")

            # ── VANNA/CHARM FLOW (v17.0) — options dealer hedging flows ───────────────
            try:
                if getattr(config, 'VANNA_CHARM_ENABLED', True):
                    from vanna_charm_flow import get_vanna_charm_score
                    _vc_delta, _vc_reason = get_vanna_charm_score(symbol, direction, ltp_now)
                    if _vc_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _vc_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: VANNA_CHARM {_vc_delta:+.0f} {_vc_reason}")
            except Exception as _vc_e:
                logger.debug(f"[suppressed] vanna_charm_flow: {_vc_e}")

            # ── SEASONAL ALPHA (v17.0) — calendar statistical edges ──────────────────
            try:
                if getattr(config, 'SEASONAL_ALPHA_ENABLED', True):
                    from seasonal_alpha import get_seasonal_score
                    _sea_delta, _sea_reason = get_seasonal_score(symbol, direction)
                    if _sea_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _sea_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: SEASONAL {_sea_delta:+.0f} {_sea_reason}")
            except Exception as _sea_e:
                logger.debug(f"[suppressed] seasonal_alpha: {_sea_e}")

            # ── MINERVINI SEPA (v17.0) — 7-criterion trend template ──────────────────
            try:
                if getattr(config, 'MINERVINI_ENABLED', True):
                    from minervini_sepa import get_minervini_score
                    _mv_delta, _mv_reason = get_minervini_score(symbol, None, direction)
                    if _mv_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _mv_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: MINERVINI {_mv_delta:+.0f} {_mv_reason}")
            except Exception as _mv_e:
                logger.debug(f"[suppressed] minervini_sepa: {_mv_e}")

            # ── WEINSTEIN STAGE (v17.0) — 4-stage cycle analysis ─────────────────────
            try:
                if getattr(config, 'WEINSTEIN_ENABLED', True):
                    from weinstein_stage import get_weinstein_score
                    _ws_delta, _ws_reason = get_weinstein_score(symbol, direction)
                    if _ws_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _ws_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: WEINSTEIN {_ws_delta:+.0f} {_ws_reason}")
            except Exception as _ws_e:
                logger.debug(f"[suppressed] weinstein_stage: {_ws_e}")

            # ── TAPE SPEED / URGENCY (v17.0) ─────────────────────────────────────────
            try:
                if getattr(config, 'TAPE_SPEED_ENABLED', True) and df_5m is not None and len(df_5m) >= 20:
                    from tape_speed import get_tape_speed_score
                    _ts_delta, _ts_reason = get_tape_speed_score(symbol, df_5m, direction)
                    if _ts_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _ts_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: TAPE_SPEED {_ts_delta:+.0f} {_ts_reason}")
            except Exception as _ts_e:
                logger.debug(f"[suppressed] tape_speed: {_ts_e}")

            # ── COPPER/GOLD RATIO (v17.0) — Dr. Copper economic health ───────────────
            try:
                if getattr(config, 'COPPER_GOLD_ENABLED', True):
                    from copper_gold_ratio import get_copper_gold_score
                    _cg_delta, _cg_reason = get_copper_gold_score(direction)
                    if _cg_delta != 0.0:
                        filter_result.final_score = float(np.clip(filter_result.final_score + _cg_delta, 0.0, 100.0))
                        logger.debug(f"{symbol}: COPPER_GOLD {_cg_delta:+.0f} {_cg_reason}")
            except Exception as _cg_e:
                logger.debug(f"[suppressed] copper_gold_ratio: {_cg_e}")

            # ── RANDOM FOREST ENSEMBLE (v17.0) — second ML opinion ───────────────────
            try:
                if getattr(config, 'RANDOM_FOREST_ENABLED', True):
                    from random_forest_ranker import get_rf_win_probability
                    _vwap_dist2 = abs(ltp_now - ind.vwap) / (ltp_now + 1e-9) * 100 if ind.vwap > 0 else 0.0
                    _rf_prob = get_rf_win_probability(
                        rsi=float(ind.rsi or 50),
                        macd_hist_norm=float((ind.macd_hist or 0) / (ind.atr or 1)),
                        volume_ratio=float(ind.volume_ratio or 1.0),
                        atr_pct=float((ind.atr or 0) / (ltp_now + 1e-9) * 100),
                        signal_score=float(filter_result.final_score),
                        adx=float(ind.adx or 25),
                        ema_slope_pct=float(ind.ema_slope_pct if hasattr(ind, 'ema_slope_pct') else 0),
                        vwap_dist_pct=float(_vwap_dist2),
                        bb_pct=float(ind.bb_pct if hasattr(ind, 'bb_pct') else 0.5),
                        hour_et=float(locals().get('_et_now', type('_', (), {'hour': 10})()).hour),
                        long_flag=1 if direction == 'LONG' else 0,
                    )
                    if _rf_prob >= 0.70:
                        filter_result.final_score = min(100.0, filter_result.final_score + 6.0)
                        logger.debug(f"{symbol}: RF_PROB {_rf_prob:.2f} +6")
                    elif _rf_prob >= 0.60:
                        filter_result.final_score = min(100.0, filter_result.final_score + 3.0)
                    elif _rf_prob < 0.40:
                        filter_result.final_score = max(0.0, filter_result.final_score - 6.0)
                        logger.debug(f"{symbol}: RF_PROB {_rf_prob:.2f} -6")
            except Exception as _rf_e:
                logger.debug(f"[suppressed] random_forest_ranker: {_rf_e}")

            # ── PREMIUM DATA PROXIES (v15.0) — free duplicates of L2/dark pool/options/tick/earnings/alt/news/colocation ──
            try:
                if getattr(config, 'PREMIUM_PROXIES_ENABLED', True):
                    from premium_data_proxies import get_all_proxy_scores
                    _df_bars = df_5m if df_5m is not None else None
                    _proxy_delta, _proxy_reasons = get_all_proxy_scores(
                        symbol=symbol,
                        direction=direction,
                        df_bars=_df_bars,
                        current_score=filter_result.final_score,
                    )
                    if _proxy_delta != 0.0:
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _proxy_delta, 0.0, 100.0
                        ))
                        for _pr in _proxy_reasons:
                            logger.debug(f"{symbol}: PROXY {_pr}")
            except Exception as _prx_e:
                logger.debug(f"[suppressed] premium_proxies: {_prx_e}")

            # ── 4-MODEL ML ENSEMBLE (v19.0) — GBM+RF+ET+LR weighted ensemble ────────
            try:
                if getattr(config, 'ML_ENSEMBLE_ENABLED', True):
                    from ml_ensemble import get_ensemble_prediction, EnsemblePrediction
                    _ens_feat = {
                        "rsi":             float(ind.rsi or 50),
                        "macd_hist_norm":  float((ind.macd_hist or 0) / max(ind.atr or 1, 0.01)),
                        "volume_ratio":    float(ind.volume_ratio or 1.0),
                        "atr_pct":         float((ind.atr or 0) / max(ltp_now, 0.01) * 100),
                        "signal_score":    float(filter_result.final_score),
                        "adx":             float(ind.adx or 25),
                        "ema_slope_pct":   float(getattr(ind, "ema_slope_pct", 0.0)),
                        "vwap_dist_pct":   float(abs(ltp_now - ind.vwap) / max(ltp_now, 0.01) * 100) if ind.vwap > 0 else 0.0,
                        "bb_pct":          float(getattr(ind, "bb_pct", 0.5)),
                        "hour_et":         float(getattr(locals().get("_et_now", type("_", (), {"hour": 10})()), "hour", 10)),
                        "is_long":         1.0 if direction in ("LONG", "BUY") else 0.0,
                        "r2_quality":      float(getattr(filter_result, "_r2_quality", 0.5)),
                        "mfi_norm":        0.5,
                        "ofi_5bar":        0.0,
                        "vix_level_norm":  0.5,
                        "short_ratio_norm": 0.3,
                        "consecutive_wins": 0.0,
                    }
                    _ens: EnsemblePrediction = get_ensemble_prediction(_ens_feat)
                    if _ens.score_delta != 0.0:
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _ens.score_delta, 0.0, 100.0
                        ))
                        logger.debug(
                            f"{symbol}: ML_ENSEMBLE {_ens.score_delta:+.0f} "
                            f"prob={_ens.win_prob:.2f} agree={_ens.model_agreement}/4 {_ens.reason}"
                        )
            except Exception as _ens_e:
                logger.debug(f"[suppressed] ml_ensemble: {_ens_e}")

            # ── EXECUTION OPTIMIZER (v19.0) — timing + spread + fill quality ─────────
            try:
                if getattr(config, 'EXECUTION_OPTIMIZER_ENABLED', True) and df_5m is not None:
                    from execution_optimizer import get_execution_score
                    _exec_delta, _exec_reason = get_execution_score(
                        df_5m, ltp_now, float(ind.volume_ratio or 1.0), direction
                    )
                    if _exec_delta != 0.0:
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _exec_delta, 0.0, 100.0
                        ))
                        logger.debug(f"{symbol}: EXEC_OPT {_exec_delta:+.0f} {_exec_reason}")
            except Exception as _exec_e:
                logger.debug(f"[suppressed] execution_optimizer: {_exec_e}")

            # ── MULTI-DIMENSIONAL REGIME v2 (v19.0) — 6-factor composite ────────────
            try:
                if getattr(config, 'REGIME_V2_ENABLED', True):
                    from market_regime_v2 import get_composite_regime, update_breadth, RegimeState
                    # Update breadth cache with this symbol
                    _ema20_v2 = float(getattr(ind, "ema20", 0.0) or ltp_now)
                    update_breadth(symbol, ltp_now, _ema20_v2)
                    # Get composite regime (cached 90s — cheap)
                    _rv2: RegimeState = get_composite_regime()
                    if _rv2.composite == "AVOID":
                        logger.debug(f"{symbol}: REGIME_V2=AVOID — skip")
                        # soft skip: apply score penalty instead of hard block
                        filter_result.final_score = 0.0
                    elif _rv2.score_multiplier != 1.0 and _rv2.score_multiplier > 0:
                        _rv2_delta = (filter_result.final_score * _rv2.score_multiplier) - filter_result.final_score
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _rv2_delta * 0.3, 0.0, 100.0
                        ))
                        logger.debug(
                            f"{symbol}: REGIME_V2={_rv2.composite} "
                            f"sc_mult={_rv2.score_multiplier:.2f} {_rv2.reason[:60]}"
                        )
            except Exception as _rv2_e:
                logger.debug(f"[suppressed] market_regime_v2: {_rv2_e}")

            # ── PORTFOLIO OPTIMIZER (v19.0) — CVaR + Kelly + correlation ─────────────
            try:
                if getattr(config, 'PORTFOLIO_OPTIMIZER_ENABLED', True):
                    from advanced_portfolio import get_portfolio_optimizer
                    _po = get_portfolio_optimizer()
                    _po_corr = _po.get_correlation_penalty(symbol, direction)
                    _po_heat = _po.get_portfolio_heat_multiplier()
                    _po_dd   = _po.get_drawdown_control_multiplier()
                    if _po_dd == 0.0:
                        logger.info(f"{symbol}: PORTFOLIO_OPT drawdown stop — skip")
                        filter_result.final_score = 0.0
                    elif _po_heat == 0.0:
                        logger.info(f"{symbol}: PORTFOLIO_OPT max positions — skip")
                        filter_result.final_score = 0.0
                    elif _po_corr < 1.0:
                        combined_size = round(combined_size * _po_corr, 2)
                        logger.debug(f"{symbol}: PORTFOLIO_OPT corr_penalty={_po_corr:.2f}")
            except Exception as _po_e:
                logger.debug(f"[suppressed] advanced_portfolio: {_po_e}")

            # ── FRONTIER QUANT INTELLIGENCE (v20.0) ─────────────────────────────────
            # Kalman · HMM · Factor Alpha · VPIN · IC-Weighted Bayesian Signals
            # — mathematically impossible for any human to compute in real-time —
            _v20_signal_deltas: dict = {}
            try:
                # 1. Kalman Filter — adaptive momentum signal (noise-filtered velocity+accel)
                if getattr(config, 'KALMAN_ENABLED', True):
                    from kalman_signals import get_kalman_score
                    _kl_d, _kl_r = get_kalman_score(symbol, df_5m, direction)
                    if _kl_d != 0.0:
                        _v20_signal_deltas["kalman"] = _kl_d
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _kl_d, 0.0, 100.0))
                        logger.debug(f"{symbol}: KALMAN {_kl_d:+.0f} {_kl_r}")
            except Exception as _kl_e:
                logger.debug(f"[suppressed] kalman: {_kl_e}")

            try:
                # 2. HMM — Hidden Markov Model latent regime state (3-state Baum-Welch)
                if getattr(config, 'HMM_REGIME_ENABLED', True):
                    from hmm_regime import get_hmm_score
                    _hmm_d, _hmm_r = get_hmm_score(symbol, df_5m, direction)
                    if _hmm_d != 0.0:
                        _v20_signal_deltas["hmm"] = _hmm_d
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _hmm_d, 0.0, 100.0))
                        logger.debug(f"{symbol}: HMM {_hmm_d:+.0f} {_hmm_r}")
            except Exception as _hmm_e:
                logger.debug(f"[suppressed] hmm_regime: {_hmm_e}")

            try:
                # 3. Factor Alpha — cross-sectional rank (momentum + RS + quality)
                if getattr(config, 'FACTOR_ALPHA_ENABLED', True):
                    from factor_alpha import get_factor_score
                    _watchlist_fa = getattr(self, '_watchlist', []) or [symbol]
                    _fa_d, _fa_r  = get_factor_score(symbol, direction, _watchlist_fa)
                    if _fa_d != 0.0:
                        _v20_signal_deltas["factor_alpha"] = _fa_d
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _fa_d, 0.0, 100.0))
                        logger.debug(f"{symbol}: FACTOR_ALPHA {_fa_d:+.0f} {_fa_r}")
            except Exception as _fa_e:
                logger.debug(f"[suppressed] factor_alpha: {_fa_e}")

            try:
                # 4. VPIN — Volume-Synchronized Probability of Informed Trading
                if getattr(config, 'VPIN_ENABLED', True) and df_5m is not None:
                    from vpin_detector import get_vpin_score
                    _vp_d, _vp_r = get_vpin_score(symbol, df_5m, direction, ltp_now)
                    if _vp_d != 0.0:
                        _v20_signal_deltas["vpin"] = _vp_d
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _vp_d, 0.0, 100.0))
                        logger.debug(f"{symbol}: VPIN {_vp_d:+.0f} {_vp_r}")
            except Exception as _vp_e:
                logger.debug(f"[suppressed] vpin: {_vp_e}")

            try:
                # 5. IC-Weighted Bayesian boost — weight signals by rolling Spearman IC
                if getattr(config, 'IC_TRACKER_ENABLED', True) and _v20_signal_deltas:
                    from signal_ic_tracker import get_composite_weight, register_signal_prediction
                    _ic_boost = get_composite_weight(_v20_signal_deltas)
                    # Apply a gentle adjustment from IC weighting (cap at ±6 to avoid runaway)
                    _ic_adj = float(np.clip(_ic_boost * 0.15, -6.0, 6.0))
                    if abs(_ic_adj) >= 0.5:
                        filter_result.final_score = float(np.clip(
                            filter_result.final_score + _ic_adj, 0.0, 100.0))
                        logger.debug(f"{symbol}: IC_WEIGHT {_ic_adj:+.1f} (raw_boost={_ic_boost:.1f})")
                    # Register this signal prediction for future IC tracking
                    conf = float(np.clip(filter_result.final_score / 100.0, 0.0, 1.0))
                    for _sn in _v20_signal_deltas:
                        register_signal_prediction(_sn, direction, conf)
            except Exception as _ic_e:
                logger.debug(f"[suppressed] signal_ic_tracker: {_ic_e}")

            # ── MASTER CONFLUENCE GATE (v14.0) — require 2+ agreeing signals ──
            try:
                if getattr(config, 'MASTER_CONFLUENCE_ENABLED', True):
                    from master_confluence import compute_confluence, make_vote
                    _mc_votes = []
                    _base = filter_result.final_score
                    if _base >= 80:
                        _mc_votes.append(make_vote("SCORE_HIGH", direction, 10.0, _base, "momentum"))
                    elif _base >= 70:
                        _mc_votes.append(make_vote("SCORE_MED", direction, 6.0, _base, "momentum"))
                    else:
                        _mc_votes.append(make_vote("SCORE_LOW", direction, 2.0, _base, "momentum"))
                    if ind.volume_ratio >= 2.0:
                        _mc_votes.append(make_vote("VOLUME_SURGE", direction, 8.0, 80.0, "flow"))
                    elif ind.volume_ratio >= 1.5:
                        _mc_votes.append(make_vote("VOLUME_OK", direction, 4.0, 65.0, "flow"))
                    if direction == "LONG" and ind.rsi <= 35:
                        _mc_votes.append(make_vote("RSI_OVERSOLD", direction, 7.0, 75.0, "reversion"))
                    elif direction == "SHORT" and ind.rsi >= 65:
                        _mc_votes.append(make_vote("RSI_OVERBOUGHT", direction, 7.0, 75.0, "reversion"))
                    if (direction == "LONG" and ind.macd > 0) or (direction == "SHORT" and ind.macd < 0):
                        _mc_votes.append(make_vote("MACD_ALIGN", direction, 5.0, 70.0, "momentum"))
                    if ind.vwap > 0:
                        _above = ltp_now > ind.vwap
                        if (direction == "LONG" and _above) or (direction == "SHORT" and not _above):
                            _mc_votes.append(make_vote("VWAP_ALIGN", direction, 6.0, 72.0, "structure"))
                        else:
                            _mc_votes.append(make_vote("VWAP_OPPOSE", "SHORT" if direction == "LONG" else "LONG", 4.0, 65.0, "structure"))
                    if ind.adx >= 25:
                        _mc_votes.append(make_vote("ADX_TREND", direction, 5.0, 70.0, "regime"))
                    _min_agree = getattr(config, 'CONFLUENCE_MIN_AGREE', 2)
                    _mc_result = compute_confluence(direction, _mc_votes, _base, _min_agree)
                    if not _mc_result.approved:
                        logger.info(
                            f"[{format_ist_timestamp()}] {symbol}: CONFLUENCE BLOCK "
                            f"— only {_mc_result.confluence_count}/{_min_agree} signals agree "
                            f"(score={_base:.0f})"
                        )
                        return None
                    combined_size = round(combined_size * _mc_result.size_multiplier, 2)
                    logger.debug(
                        f"{symbol}: CONFLUENCE {_mc_result.confluence_count} agree "
                        f"size={_mc_result.size_multiplier:.1f}x conf={_mc_result.confidence_pct:.0f}%"
                    )
            except Exception as _mc_e:
                logger.debug(f"[suppressed] master_confluence: {_mc_e}")

            # ── FINAL SCORE → SIZE RECALIBRATION (v13.0) ─────────────────────
            # combined_size was set at line ~826 using the PRE-BOOST score.
            # Now ALL modules have fired. Recalculate using the FINAL score so
            # A+ setups (score 90+) get proper 1.5–2.0x sizing.
            _final_s = filter_result.final_score
            if _final_s >= 95:
                _final_size_mult = 2.0
            elif _final_s >= 90:
                _final_size_mult = 1.5
            elif _final_s >= 85:
                _final_size_mult = 1.2
            elif _final_s >= 75:
                _final_size_mult = 1.0
            else:
                _final_size_mult = 0.75
            # Blend: keep any regime/breadth adjustments already in combined_size
            # but replace the stale score-tier component with the final one
            combined_size = round(
                min(combined_size, _final_size_mult) * max(combined_size / max(_final_size_mult, 0.01), 0.5),
                2
            )
            combined_size = max(0.25, min(3.0, combined_size))

            # ── ADAPTIVE KELLY SIZING (v13.0) — live win-rate → optimal f ────
            # Uses per-symbol historical WR from trade journal. Grows size on
            # symbols with proven edge, shrinks on symbols where we lose.
            try:
                if getattr(config, 'ADAPTIVE_KELLY_ENABLED', True):
                    from adaptive_kelly import get_kelly_size_pct
                    _kelly_risk = get_kelly_size_pct(
                        symbol       = symbol,
                        base_risk_pct = getattr(config, 'RISK_PER_TRADE_PCT', 0.8),
                    )
                    # kelly returns recommended risk %, convert to size multiplier
                    _base_risk = getattr(config, 'RISK_PER_TRADE_PCT', 0.8)
                    _kelly_mult = _kelly_risk / max(_base_risk, 0.01)
                    _kelly_mult = max(0.4, min(2.0, _kelly_mult))
                    combined_size = round(combined_size * _kelly_mult, 2)
                    if abs(_kelly_mult - 1.0) > 0.1:
                        logger.debug(f"{symbol}: KELLY {_kelly_mult:.2f}x (risk={_kelly_risk:.2f}%)")
            except Exception as _ke:
                logger.debug(f"[suppressed] adaptive_kelly: {_ke}")

            combined_size = max(0.25, min(3.0, combined_size))
            logger.debug(
                f"{symbol}: FINAL score={_final_s:.0f} size={combined_size:.2f}x "
                f"(grade={filter_result.quality_grade})"
            )

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

            # Hard R:R gate — top-3% rule: never trade below 2:1 reward-to-risk
            min_rr = getattr(config, "MIN_RISK_REWARD", 2.0)
            if signal.risk_reward < min_rr:
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: R:R {signal.risk_reward:.1f}:1 < "
                    f"{min_rr}:1 minimum — skipping (SL too wide or target too close)"
                )
                return None

            # For A+ setups (score ≥ 92), extend T2 to 5R runner target
            runner_mult = getattr(config, "ATR_TP_RUNNER", 5.0)
            if filter_result.quality_grade == "A+" and signal.atr > 0:
                sl_dist = abs(signal.entry_price - signal.stop_loss)
                if direction == "LONG":
                    signal.target_2 = round(signal.entry_price + runner_mult * sl_dist, 2)
                else:
                    signal.target_2 = round(signal.entry_price - runner_mult * sl_dist, 2)
                logger.info(
                    f"[{format_ist_timestamp()}] {symbol}: A+ setup — T2 extended to "
                    f"${signal.target_2:.2f} ({runner_mult}R runner)"
                )

            logger.info(
                f"[{format_ist_timestamp()}] ✅ SIGNAL: {direction} {symbol} "
                f"| Score: {filter_result.final_score:.0f} | Grade: {filter_result.quality_grade} "
                f"| Entry: ${signal.entry_price:.2f} | SL: ${signal.stop_loss:.2f} "
                f"| T1: ${signal.target_1:.2f} | T2: ${signal.target_2:.2f} "
                f"| R:R {signal.risk_reward:.1f}:1 | Breadth: {ind.breadth_score:.0f}/100"
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

        # Store watchlist reference for correlation crisis + cross-sectional ranking
        self._current_watchlist = list(symbols)

        # Clear daily candles cache at start of each scan cycle (30-min staleness tolerance)
        self._daily_candles_cache = {}

        # Symbol-level adaptive filter: skip symbols with persistent poor WR
        try:
            from symbol_stats import SymbolStats
            _sym_stats = getattr(self, "_symbol_stats", None)
            if _sym_stats is None:
                self._symbol_stats = SymbolStats()
                _sym_stats = self._symbol_stats
            symbols = [s for s in symbols if not _sym_stats.should_skip(s, min_wr=0.40)]
        except Exception as _sse:
            logger.debug(f"[suppressed] symbol_stats: {_sse}")

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

        # Cross-sectional ranking (v27.0): pre-rank all symbols, keep top 40%
        # This ensures we only enter the strongest setups when the watchlist is large
        if getattr(config, 'CROSS_SECTIONAL_RANKING_ENABLED', True) and len(symbols) > 15:
            try:
                from cross_sectional_ranker import rank_symbols
                symbols = rank_symbols(symbols, self.data_fetcher, top_pct=0.40)
                logger.info(
                    f"[{format_ist_timestamp()}] Cross-sectional pre-rank: "
                    f"{len(symbols)} symbols selected"
                )
            except Exception as _csr_e:
                logger.debug(f"[suppressed] cross_sectional_ranker: {_csr_e}")

        # Pre-warm BarCache before concurrent scan so threads serve from memory (not blocking Alpaca).
        # On startup (empty cache) this blocks once; on subsequent calls it's a no-op (data is fresh).
        try:
            from data_fetch_alpaca import get_bar_cache as _get_bc
            _bc = _get_bc()
            _warmup_specs = [("5minute", 5), ("15minute", 10), ("1hour", 30)]
            for _iv, _ld in _warmup_specs:
                import time as _tw
                _last = _bc._last_refresh.get(_iv, 0.0)
                if (_tw.monotonic() - _last) > _bc.REFRESH_INTERVAL:
                    # Force blocking refresh so the cache is warm before threads start
                    with _bc._refresh_lock:
                        if (_tw.monotonic() - _bc._last_refresh.get(_iv, 0.0)) > _bc.REFRESH_INTERVAL:
                            _bc._last_refresh[_iv] = _tw.monotonic()
                            try:
                                _bc._refresh_interval(_iv, _ld)
                            except Exception as _wue:
                                _bc._last_refresh[_iv] = 0.0
                                logger.warning(f"BarCache pre-warm {_iv}: {_wue}")
            # Fetch any scan symbols not in the standard watchlist (top movers, dynamic additions).
            # BarCache._refresh_interval() only fetches config.WATCHLIST; extra symbols miss the
            # cache and fall back to slow per-symbol Alpaca calls → "insufficient 5m data".
            try:
                _cached_syms = set(k[0] for k in _bc._cache.keys() if k[1] == "5minute")
                _extra_syms  = [s for s in symbols if s not in _cached_syms]
                if _extra_syms:
                    logger.info(
                        f"[BarCache] Fetching {len(_extra_syms)} extra scan symbols: "
                        f"{', '.join(_extra_syms[:8])}{'...' if len(_extra_syms) > 8 else ''}"
                    )
                    for _iv2, _ld2 in [("5minute", 5), ("15minute", 10), ("1hour", 30)]:
                        _nd: dict = {}
                        try:
                            _bc._fetch_alpaca_bars(_iv2, _ld2, _extra_syms, _nd)
                        except Exception:
                            pass
                        if _nd:
                            with _bc._lock:
                                _bc._cache.update(_nd)
            except Exception as _ese:
                logger.debug(f"[suppressed] extra-symbol BarCache fetch: {_ese}")
        except Exception as _pwe:
            logger.debug(f"[suppressed] BarCache pre-warm: {_pwe}")

        signals: List[TradeSignal] = []
        errors  = 0

        with ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="sig") as pool:
            futures = {pool.submit(self._scan_one, sym): sym for sym in symbols}
            try:
                for fut in as_completed(futures, timeout=180):
                    sym = futures[fut]
                    try:
                        sig = fut.result(timeout=30)
                        if sig:
                            signals.append(sig)
                    except FuturesTimeout:
                        logger.debug(f"Scan timeout: {sym}")
                        errors += 1
                    except Exception as e:
                        logger.debug(f"Scan error {sym}: {e}")
                        errors += 1
            except FuturesTimeout:
                done = len(signals) + errors
                remaining = len(symbols) - done
                logger.warning(f"Scan cycle timeout — completed {done}/{len(symbols)}, skipped {remaining}")

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
        self, symbol: str, df_5m=None, direction: str = "LONG"
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
            "oc_score":                    0.0,
            "oc_signals":                  [],
            "vp_score":                    0.0,
            "vp_notes":                    [],
            "vp_levels":                   {},
            "fii_score":                   self._fii_adjustment,
            "fii_size_mult":               self._fii_size_mult,
            "nse_score":                   0,
            "nse_reason":                  "",
            "ltp":                         float(df_5m["close"].iloc[-1]) if df_5m is not None and not df_5m.empty and "close" in df_5m.columns else 0.0,
            # Pre-market conviction defaults (filled by global_market_context when available)
            "vix3m":                       0.0,
            "premarket_volume_ratio":      0.0,
            "premarket_consecutive_up":    0,
            "gap_pct":                     0.0,
            # Higher-timeframe AVWAP levels
            "weekly_vwap":                 0.0,
            "monthly_vwap":               0.0,
            # Multi-day momentum (3d and 5d price returns from daily candles)
            "3d_mom":                      0.0,
            "5d_mom":                      0.0,
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
                        vp_ctx = self._vp.get_signal_context(vp_result, ltp, direction)  # use actual direction
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

        # ── Global Market Context (inter-market: VIX, gold, yields, futures) ──
        if self._global_ctx:
            try:
                gmc_adj, gmc_reasons = self._global_ctx.get_score_adjustment(symbol, direction)
                ctx["gmc_score"]   = gmc_adj
                ctx["gmc_reasons"] = gmc_reasons
                _, vix_mult = self._global_ctx.get_vix_regime()
                ctx["vix_size_mult"] = vix_mult
            except Exception as _ge:
                logger.debug(f"GlobalMarketContext score failed for {symbol}: {_ge}")

        # ── Economic Calendar (FOMC/CPI/NFP proximity) ───────────
        if self._econ_cal:
            try:
                cal_adj, cal_note, trading_ok = self._econ_cal.get_calendar_score_adjustment()
                ctx["calendar_score"] = cal_adj
                ctx["calendar_note"]  = cal_note
                ctx["calendar_ok"]    = trading_ok
            except Exception as _ce:
                logger.debug(f"EconomicCalendar score failed: {_ce}")

        # ── Sector Rotation (hot/cold sector bias) ────────────────
        if self._sector_rot:
            try:
                sector_adj, sector_name = self._sector_rot.get_sector_bias(symbol)
                ctx["sector_adj"]  = sector_adj
                ctx["sector_name"] = sector_name
            except Exception as _sre:
                logger.debug(f"SectorRotation score failed for {symbol}: {_sre}")

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

        # ── Weekly / Monthly Anchored VWAP from daily candles cache ─────────
        try:
            _daily_df = getattr(self, "_daily_candles_cache", {}).get(symbol)
            if _daily_df is not None and len(_daily_df) >= 5 and \
               all(c in _daily_df.columns for c in ("close", "volume", "open")):
                import numpy as _np
                _c  = _daily_df["close"].values.astype(float)
                _v  = _daily_df["volume"].values.astype(float)
                _h  = _daily_df["high"].values.astype(float) if "high" in _daily_df else _c
                _lo = _daily_df["low"].values.astype(float) if "low" in _daily_df else _c
                _tp = (_h + _lo + _c) / 3.0  # typical price
                # Weekly AVWAP: last 5 trading days
                _wk = min(5, len(_daily_df))
                _w_num = (_tp[-_wk:] * _v[-_wk:]).sum()
                _w_den = _v[-_wk:].sum()
                if _w_den > 0:
                    ctx["weekly_vwap"] = float(_w_num / _w_den)
                # Monthly AVWAP: last 22 trading days
                _mo = min(22, len(_daily_df))
                _m_num = (_tp[-_mo:] * _v[-_mo:]).sum()
                _m_den = _v[-_mo:].sum()
                if _m_den > 0:
                    ctx["monthly_vwap"] = float(_m_num / _m_den)
        except Exception as _wv_e:
            logger.debug(f"[suppressed] weekly/monthly vwap: {_wv_e}")

        # ── Multi-day momentum from daily candles (3d and 5d returns) ────────
        try:
            _dd = getattr(self, '_daily_candles_cache', {}).get(symbol)
            if _dd is not None and len(_dd) >= 7 and 'close' in _dd.columns:
                import numpy as _np_md
                _c = _dd['close'].values.astype(float)
                ctx['3d_mom'] = round((_c[-1] - _c[-4]) / max(_c[-4], 0.01) * 100, 2) if len(_c) >= 4 else 0.0
                ctx['5d_mom'] = round((_c[-1] - _c[-6]) / max(_c[-6], 0.01) * 100, 2) if len(_c) >= 6 else 0.0
            else:
                ctx['3d_mom'] = 0.0
                ctx['5d_mom'] = 0.0
        except Exception:
            ctx['3d_mom'] = 0.0
            ctx['5d_mom'] = 0.0

        # ── Gap % — from pre-loaded GapAnalyzer (called at market open) ──────
        try:
            ctx["gap_pct"] = self._get_gap_pct(symbol)
        except Exception:
            pass

        # ── VIX3M for term structure (VIX/VIX3M ratio backwardation/contango) ─
        try:
            from data_fetch_alpaca import get_vix3m_level
            ctx["vix3m"] = get_vix3m_level()
        except Exception:
            pass

        # ── Pre-market conviction: volume ratio + consecutive up bars ─────────
        # Only computed before 11 AM ET; cached per-symbol per trading day.
        try:
            from data_fetch_alpaca import get_premarket_data as _get_pm
            _avg_vol = 0
            _daily_df = getattr(self, "_daily_candles_cache", {}).get(symbol)
            if _daily_df is not None and len(_daily_df) >= 5 and "volume" in _daily_df.columns:
                _avg_vol = int(float(_daily_df["volume"].tail(20).mean()))
            _pm = _get_pm(symbol, avg_daily_vol=_avg_vol)
            ctx["premarket_volume_ratio"]   = _pm.get("premarket_volume_ratio", 0.0)
            ctx["premarket_consecutive_up"] = _pm.get("premarket_consecutive_up", 0)
        except Exception:
            pass

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

        # Require 5m + at least partial HTF confirmation:
        # 5m(35) + 15m aligned(35) = 70 ✅  |  5m + two NEUTRALs = 55 ✅  |  5m + 1h opposing = 50 ❌
        aligned = alignment_score >= 55

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
                    result = None    # Below SMA: daily structure strongly opposes LONG — hard block
            else:  # SHORT
                if bearish_structure:
                    result = 0.0
                elif not above_sma:
                    result = -8.0
                else:
                    result = None    # Daily bull structure strongly opposes SHORT — hard block

        except Exception as e:
            logger.debug(f"Daily HTF check {symbol}: {e}")
            result = 0.0   # On error: allow signal (don't block)

        self._daily_htf_cache[cache_key] = {"val": result, "ts": get_current_ist_time()}
        return result

    # --------------------------------------------------------
    # GAP & TIME HELPERS (used by Gates 6-10)
    # --------------------------------------------------------

    # Sector ETF map: symbol → sector ETF
    _SECTOR_ETF_MAP: Dict[str, str] = {
        # Tech
        "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK",
        "QCOM": "XLK", "MU": "XLK", "ARM": "XLK", "SMCI": "XLK",
        # Communication
        "META": "XLC", "GOOGL": "XLC", "NFLX": "XLC",
        # Consumer Discretionary
        "AMZN": "XLY", "TSLA": "XLY",
        # Financials
        "JPM": "XLF", "GS": "XLF", "BAC": "XLF",
        # Energy
        "XOM": "XLE", "CVX": "XLE", "OXY": "XLE",
        # High-beta / no clear sector ETF — skip
        "COIN": "", "MSTR": "", "PLTR": "", "SOFI": "",
    }

    def _get_sector_etf_boost(self, symbol: str, direction: str) -> float:
        """
        Boost score by up to 8 pts when the sector ETF is trending strongly
        in the same direction as the signal (top-1% flow-with-sector technique).
        """
        etf = self._SECTOR_ETF_MAP.get(symbol, "")
        if not etf:
            return 0.0
        try:
            q = self.fetcher.get_quote(etf)
            if not q:
                return 0.0
            etf_change = float(q.get("change_pct", 0.0))
            if direction == "LONG" and etf_change >= 0.5:
                return min(8.0, etf_change * 2.0)
            if direction == "SHORT" and etf_change <= -0.5:
                return min(8.0, abs(etf_change) * 2.0)
        except Exception:
            pass
        return 0.0

    def _get_prev_day_volume(self, symbol: str, quote_volume: float) -> float:
        """Return previous completed day's volume from daily bar cache.

        The live quote volume is near-zero at market open (only minutes of data),
        which falsely flags liquid stocks like NVDA/AAPL as low-volume.
        We use the most recent COMPLETE daily bar instead.
        Falls back to quote_volume if cache is unavailable.
        """
        try:
            df = self._daily_candles_cache.get(symbol)
            if df is not None and not df.empty and "volume" in df.columns:
                today = __import__("utils").get_current_ist_time().date()
                # Use the most recent bar that is NOT today's partial bar
                for i in range(len(df) - 1, -1, -1):
                    bar_date = df.index[i]
                    if hasattr(bar_date, "date"):
                        bar_date = bar_date.date()
                    if bar_date < today:
                        vol = float(df["volume"].iloc[i])
                        if vol > 0:
                            return vol
                        break
        except Exception:
            pass
        return float(quote_volume or 0)

    def _get_gap_pct(self, symbol: str) -> float:
        """Get today's opening gap % for symbol (0.0 if not available)."""
        try:
            from gap_analyzer import get_gap_analyzer
            return get_gap_analyzer().get_gap_pct(symbol)
        except Exception:
            return 0.0

    def _minutes_since_open(self) -> float:
        """Minutes elapsed since 9:30 AM ET market open (0.0 before open)."""
        now_et = get_current_ist_time()  # alias returns ET
        # replace() preserves DST fold state; avoids ambiguity at DST transitions
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
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
        df_5m=None,
        symbol: str = "",
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
        # When 15m/1h data is unavailable (fetcher returned nothing) treat as neutral (50)
        # so the 5m pattern signal carries its full weight rather than being discarded.
        # Missing data ≠ opposing data — neutral fallback avoids the 40%-weight penalty.
        key     = "long" if direction == "LONG" else "short"
        opp_key = "short" if key == "long" else "long"

        def _tf_score(score_dict, k, opp_k):
            # Neutral fix: if both directions near 0 (no pattern fired), use 50 not 0.
            # Only use actual low score when the opposing direction genuinely fired (bearish 15m for LONG).
            if not score_dict:
                return 50
            val = score_dict.get(k, 0)
            opp = score_dict.get(opp_k, 0)
            if val < 10 and opp < 10:   # neither direction has a signal → genuinely neutral
                return 50
            return val

        _s15 = _tf_score(score_15m, key, opp_key)
        _s1h = _tf_score(score_1h,  key, opp_key)
        score += 10.0                        # base: signal exists at all (+10 pts)
        score += score_5m.get(key, 0) * 0.40
        score += _s15 * 0.30
        score += _s1h * 0.20                 # total weight = 10 + 40 + 30 + 20 = 100%

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
            elif 15 < ind.rsi <= 30:
                score += 3  # deep oversold bounce setup
            elif 50 <= ind.rsi < 65:
                score += 2  # momentum continuation zone
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

        # RVOL Percentile rank (institutional conviction signal)
        try:
            import config as _cfgRVP
            if getattr(_cfgRVP, 'RVOL_PERCENTILE_ENABLED', True) and df_5m is not None and len(df_5m) >= 25:
                import numpy as _np_rvp
                _vols = df_5m['volume'].values.astype(float)
                _roll_mean = _np_rvp.array([
                    _vols[max(0, i-20):i].mean() if i >= 5 else _np_rvp.nan
                    for i in range(len(_vols))
                ])
                _rvol_series = _np_rvp.where(_roll_mean > 0, _vols / _roll_mean, _np_rvp.nan)
                _valid = _rvol_series[~_np_rvp.isnan(_rvol_series)]
                if len(_valid) >= 5:
                    _cur_rvol = float(ind.volume_ratio or 1.0)
                    _pct = float(_np_rvp.mean(_valid <= _cur_rvol) * 100)
                    if _pct >= 95:
                        score += 12; logger.debug(f"{symbol}: RVOL_P{_pct:.0f}(+12)")
                    elif _pct >= 85:
                        score += 6;  logger.debug(f"{symbol}: RVOL_P{_pct:.0f}(+6)")
                    elif _pct >= 75:
                        score += 3
                    elif _pct < 40:
                        score -= 3;  logger.debug(f"{symbol}: RVOL_LOW_P{_pct:.0f}(-3)")
        except Exception:
            pass

        # ── Multi-indicator confluence bonus ─────────────────────────────────
        # 4+ indicators simultaneously aligned = institutional-grade confirmation;
        # this fires independently of pattern detection so strong-indicator / no-pattern
        # setups (common at opening drive) are scored appropriately.
        _conf_count = 0
        if direction == "LONG":
            if ind.rsi and 30 < ind.rsi < 65:                      _conf_count += 1
            if ind.macd_hist and ind.macd_hist > 0:                 _conf_count += 1
            if ind.ema9 and ind.ema21 and ind.ema9 > ind.ema21:     _conf_count += 1
            if ind.supertrend_dir == 1:                              _conf_count += 1
            if ind.adx and ind.adx > 20:                            _conf_count += 1
            if ind.volume_ratio and ind.volume_ratio >= 1.5:        _conf_count += 1
        else:
            if ind.rsi and 35 < ind.rsi < 70:                      _conf_count += 1
            if ind.macd_hist and ind.macd_hist < 0:                 _conf_count += 1
            if ind.ema9 and ind.ema21 and ind.ema9 < ind.ema21:     _conf_count += 1
            if ind.supertrend_dir == -1:                             _conf_count += 1
            if ind.adx and ind.adx > 20:                            _conf_count += 1
            if ind.volume_ratio and ind.volume_ratio >= 1.5:        _conf_count += 1
        if _conf_count >= 6:
            score += 12   # All 6 aligned — maximum institutional conviction
        elif _conf_count >= 5:
            score += 8
        elif _conf_count >= 4:
            score += 5

        # ── Relative strength vs Nifty ────────────────────
        if direction == "LONG" and relative_strength > 0.5:
            score += min(relative_strength * 2, 8)
        elif direction == "SHORT" and relative_strength < -0.5:
            score += min(abs(relative_strength) * 2, 8)

        # ── Extended Indicator Confluence (institutional toolbox) ──────────
        close = ctx.get("ltp", 0.0)

        if direction == "LONG":
            # Ichimoku Cloud (institutional trend filter)
            if ind.ichimoku_tenkan > 0 and ind.ichimoku_kijun > 0:
                if ind.ichimoku_tenkan > ind.ichimoku_kijun:
                    score += 3  # TK bullish cross
                if close > 0 and ind.ichimoku_senkou_a > 0 and ind.ichimoku_senkou_b > 0:
                    cloud_top = max(ind.ichimoku_senkou_a, ind.ichimoku_senkou_b)
                    if close > cloud_top:
                        score += 4  # Price above Kumo = institutional uptrend confirmed
            # Parabolic SAR aligned
            if ind.parabolic_sar_bull:
                score += 3
            # Money Flow (institutional accumulation signals)
            if ind.cmf > 0.15:
                score += 4  # Strong institutional buying
            elif ind.cmf > 0.05:
                score += 2
            if ind.mfi < 30:
                score += 3  # MFI oversold = accumulation zone
            elif ind.mfi < 45:
                score += 1
            # Williams %R oversold zone
            if ind.williams_r < -80:
                score += 3  # Deep oversold = bounce setup
            elif ind.williams_r < -60:
                score += 1
            # CCI recovering from oversold
            if -100 < ind.cci < -50:
                score += 2
            elif 0 < ind.cci < 100:
                score += 1  # Positive momentum building
            # Stoch RSI oversold cross
            if ind.stoch_rsi_k < 25 and ind.stoch_rsi_k > ind.stoch_rsi_d:
                score += 3
            # Keltner Squeeze (BB inside KC = imminent volatility expansion)
            if (ind.keltner_upper > 0 and ind.keltner_lower > 0 and
                    ind.bb_upper > 0 and ind.bb_lower > 0):
                if ind.bb_upper < ind.keltner_upper and ind.bb_lower > ind.keltner_lower:
                    score += 4  # Classic TTM squeeze — explosive move loading
            # VWAP standard deviation bands (institutional buy zones)
            if close > 0:
                if ind.vwap_lower_2 > 0 and close <= ind.vwap_lower_2:
                    score += 5  # At -2σ VWAP = high-conviction reversal zone
                elif ind.vwap_lower_1 > 0 and close <= ind.vwap_lower_1:
                    score += 3  # At -1σ VWAP = institutional buy zone
            # Pivot Point confluence (key institutional levels)
            if close > 0 and ind.pivot_pp > 0:
                above_pp = close > ind.pivot_pp
                if above_pp and ind.pivot_r1 > 0 and close < ind.pivot_r1:
                    score += 2  # Trading between PP and R1 = bullish structure
                if ind.pivot_s1 > 0 and 0 <= (close - ind.pivot_s1) / close <= 0.005:
                    score += 3  # Bouncing off S1 pivot support
            # Liquidity levels (ICT concept)
            if ind.eql:
                score += 2  # Equal lows = liquidity pool below = bounce likely
            # Volume Profile levels
            if ind.at_hvn:
                score += 2  # At HVN = institutional interest, high conviction area

        else:  # SHORT
            # Ichimoku Cloud
            if ind.ichimoku_tenkan > 0 and ind.ichimoku_kijun > 0:
                if ind.ichimoku_tenkan < ind.ichimoku_kijun:
                    score += 3  # TK bearish cross
                if close > 0 and ind.ichimoku_senkou_a > 0 and ind.ichimoku_senkou_b > 0:
                    cloud_bottom = min(ind.ichimoku_senkou_a, ind.ichimoku_senkou_b)
                    if close < cloud_bottom:
                        score += 4  # Price below Kumo = institutional downtrend confirmed
            # Parabolic SAR bearish
            if not ind.parabolic_sar_bull:
                score += 3
            # Money Flow bearish
            if ind.cmf < -0.15:
                score += 4
            elif ind.cmf < -0.05:
                score += 2
            if ind.mfi > 70:
                score += 3  # MFI overbought = distribution zone
            elif ind.mfi > 55:
                score += 1
            # Williams %R overbought
            if ind.williams_r > -20:
                score += 3
            elif ind.williams_r > -40:
                score += 1
            # CCI overbought zone
            if 50 < ind.cci < 100:
                score += 2
            elif -100 < ind.cci < 0:
                score += 1
            # Stoch RSI overbought cross
            if ind.stoch_rsi_k > 75 and ind.stoch_rsi_k < ind.stoch_rsi_d:
                score += 3
            # Keltner Squeeze
            if (ind.keltner_upper > 0 and ind.keltner_lower > 0 and
                    ind.bb_upper > 0 and ind.bb_lower > 0):
                if ind.bb_upper < ind.keltner_upper and ind.bb_lower > ind.keltner_lower:
                    score += 4
            # VWAP SD bands (distribution zones)
            if close > 0:
                if ind.vwap_upper_2 > 0 and close >= ind.vwap_upper_2:
                    score += 5  # At +2σ VWAP = high-conviction short zone
                elif ind.vwap_upper_1 > 0 and close >= ind.vwap_upper_1:
                    score += 3  # At +1σ VWAP = institutional sell zone
            # Pivot Point confluence
            if close > 0 and ind.pivot_pp > 0:
                below_pp = close < ind.pivot_pp
                if below_pp and ind.pivot_s1 > 0 and close > ind.pivot_s1:
                    score += 2  # Trading between PP and S1 = bearish structure
                if ind.pivot_r1 > 0 and 0 <= (ind.pivot_r1 - close) / close <= 0.005:
                    score += 3  # Rejecting at R1 = resistance short
            # Liquidity levels
            if ind.eqh:
                score += 2  # Equal highs = liquidity pool above = distribution likely
            # Volume Profile
            if ind.at_hvn:
                score += 2

        # ── Time of day filter (US ET market hours) ──────────
        now_et   = get_current_ist_time()   # aliased to ET
        time_val = now_et.hour + now_et.minute / 60
        _is_opening_window = 9.5 <= time_val <= 10.25   # 9:30–10:15 AM: ORB is primary signal type
        # Check EOD penalty FIRST — must never be shadowed by Power Hour branch
        if time_val >= 15.75:           # After 3:45 PM ET: NO new positions
            score -= 12
        elif 14.5 <= time_val < 15.75:  # 2:30–3:45 PM ET: Power Hour (pre-EOD)
            score += 6
        elif 9.5 <= time_val <= 10.75:  # 9:30–10:45 AM ET: NY Open Kill Zone — highest-probability window
            score += 12
        elif 10.75 < time_val <= 11.5:  # 10:45–11:30 AM ET: late opening continuation
            score += 4
        elif 11.5 <= time_val < 14.5:   # 11:30 AM–2:30 PM ET: midday chop
            score -= 3

        # ── ORB precision bonus/penalty (opening window 9:30-10:15 AM) ────────
        # ORB is the highest-probability opening signal type; non-ORB signals in
        # the opening window get an 8-pt penalty to prioritise ORB plays.
        _orb = getattr(self, "_orb_direction", "")
        if _is_opening_window:
            if _orb and _orb == ("UP" if direction == "LONG" else "DOWN"):
                score += 15   # ORB confirms direction — premium opening signal
            elif not _orb:
                score -= 8    # No ORB established yet — reduce opening confidence

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
            score -= 5    # Non-tradeable regime — size_mult handles severity, mild score drag
        elif regime_strategy == "MOMENTUM":
            if regime_pref_dir == direction or regime_pref_dir == "BOTH":
                score += 12   # Regime confirms direction — full bonus
            else:
                score -= 5    # Regime opposes direction — mild penalty (gates still filter)
        elif regime_strategy == "MEAN_REVERSION":
            score -= 2    # Slight penalty — size_mult already reduced
        else:
            # AVOID/RANGING: position sizing reduced by regime (0.4x); mild score penalty
            score -= 4

        # ── Regime Transition Bonus — RANGING→MOMENTUM = highest-probability entry ──
        # The moment trending starts is when the biggest moves happen (institutional FOMO)
        try:
            _prev_regime = getattr(self, "_prev_regime_strategy", regime_strategy)
            if (_prev_regime in ("MEAN_REVERSION", "RANGING") and
                    regime_strategy == "MOMENTUM"):
                score += 18
                logger.debug(f"{symbol}: REGIME TRANSITION {_prev_regime}→MOMENTUM +18pts")
            self._prev_regime_strategy = regime_strategy
        except Exception:
            pass

        # Multi-day momentum confirmation (3d + 5d price trend from daily candles)
        try:
            import config as _cfgMDM
            if getattr(_cfgMDM, 'MULTIDAY_MOMENTUM_ENABLED', True):
                _mom3 = float(ctx.get('3d_mom', 0.0))
                _mom5 = float(ctx.get('5d_mom', 0.0))
                if direction in ('LONG', 'BUY'):
                    if _mom3 > 7.0:
                        score -= 5;  logger.debug(f"{symbol}: MDM_OVEREXT 3d={_mom3:.1f}% -5")
                    elif _mom3 > 3.0 and _mom5 > 5.0:
                        score += 8;  logger.debug(f"{symbol}: MDM_BULL 3d={_mom3:.1f}% 5d={_mom5:.1f}% +8")
                    elif _mom3 > 1.5:
                        score += 3
                    elif _mom3 < -3.0:
                        score -= 5;  logger.debug(f"{symbol}: MDM_BEAR 3d={_mom3:.1f}% -5")
                else:  # SHORT
                    if _mom3 < -7.0:
                        score -= 5;  logger.debug(f"{symbol}: MDM_OVEREXT_SHORT 3d={_mom3:.1f}% -5")
                    elif _mom3 < -3.0 and _mom5 < -5.0:
                        score += 8;  logger.debug(f"{symbol}: MDM_BEAR 3d={_mom3:.1f}% 5d={_mom5:.1f}% +8")
                    elif _mom3 < -1.5:
                        score += 3
                    elif _mom3 > 3.0:
                        score -= 5;  logger.debug(f"{symbol}: MDM_BULL_FIGHTS_SHORT 3d={_mom3:.1f}% -5")
        except Exception:
            pass

        # ── Anchored VWAP from session open (more reliable than daily VWAP reset) ──
        # Price bouncing off AVWAP = institutional support confirmed
        try:
            _avwap = ind.vwap  # anchored from session open (9:30 AM)
            _ltp = ctx.get("ltp", 0.0)
            if _avwap > 0 and _ltp > 0:
                _avwap_dist = abs(_ltp - _avwap) / _avwap * 100
                if direction == "LONG":
                    if _avwap_dist < 0.15:
                        score += 12  # AT AVWAP = maximum precision entry
                    elif _avwap_dist < 0.3 and _ltp >= _avwap:
                        score += 8   # bouncing off AVWAP support — classic institutional entry
                else:  # SHORT
                    if _avwap_dist < 0.15:
                        score += 12
                    elif _avwap_dist < 0.3 and _ltp <= _avwap:
                        score += 8
        except Exception:
            pass

        # ── Weekly / Monthly Anchored VWAP (higher timeframe levels) ──────
        # Weekly AVWAP = where institutions anchor for the week
        # Monthly AVWAP = the most important institutional level of all
        try:
            _weekly_vwap  = ctx.get("weekly_vwap", 0.0)
            _monthly_vwap = ctx.get("monthly_vwap", 0.0)
            _ltp_wv       = ctx.get("ltp", 0.0)
            if _ltp_wv > 0:
                if _weekly_vwap > 0:
                    _w_dist = abs(_ltp_wv - _weekly_vwap) / _weekly_vwap * 100
                    if direction == "LONG":
                        if _w_dist < 0.2 and _ltp_wv >= _weekly_vwap:
                            score += 6   # AT weekly AVWAP = strong support
                        elif _w_dist < 0.4 and _ltp_wv >= _weekly_vwap:
                            score += 3
                        elif _ltp_wv < _weekly_vwap * 0.995:
                            score -= 4   # price below weekly AVWAP = headwind
                    else:  # SHORT
                        if _w_dist < 0.2 and _ltp_wv <= _weekly_vwap:
                            score += 6
                        elif _w_dist < 0.4 and _ltp_wv <= _weekly_vwap:
                            score += 3
                        elif _ltp_wv > _weekly_vwap * 1.005:
                            score -= 4
                if _monthly_vwap > 0:
                    _m_dist = abs(_ltp_wv - _monthly_vwap) / _monthly_vwap * 100
                    if direction == "LONG":
                        if _m_dist < 0.3 and _ltp_wv >= _monthly_vwap:
                            score += 8   # AT monthly AVWAP = institutional gold zone
                        elif _ltp_wv < _monthly_vwap * 0.99:
                            score -= 5   # below monthly AVWAP = structural headwind
                    else:  # SHORT
                        if _m_dist < 0.3 and _ltp_wv <= _monthly_vwap:
                            score += 8
                        elif _ltp_wv > _monthly_vwap * 1.01:
                            score -= 5
        except Exception:
            pass

        # ── VIX Term Structure Bias ───────────────────────────
        # Backwardation (spot > 3-month) = fear = short-bias
        # Contango (spot < 3-month) = calm = long-bias
        try:
            _vix   = ctx.get("vix", 0.0)
            _vix3m = ctx.get("vix3m", 0.0)
            if _vix > 0 and _vix3m > 0:
                _vts_ratio = _vix / _vix3m
                if direction == "LONG":
                    if _vts_ratio > 1.15:    # steep backwardation = panic
                        score -= 12
                    elif _vts_ratio > 1.05:  # mild backwardation
                        score -= 5
                    elif _vts_ratio < 0.90:  # contango = calm = trend-following works
                        score += 5
                else:  # SHORT
                    if _vts_ratio > 1.15:
                        score += 8   # panic = shorts work
                    elif _vts_ratio > 1.05:
                        score += 4
        except Exception:
            pass

        # ── Pre-Market Conviction Score ───────────────────────
        # Gap + pre-market volume + direction consistency = 68% opening follow-through
        try:
            _pm_vol    = ctx.get("premarket_volume_ratio", 0.0)
            _pm_consec = ctx.get("premarket_consecutive_up", 0)
            _gap       = ctx.get("gap_pct", 0.0)
            _pm_score  = 0.0
            if _pm_vol >= 2.0:   _pm_score += 6
            if _pm_vol >= 4.0:   _pm_score += 4
            if _pm_consec >= 3:  _pm_score += 5
            if _pm_consec >= 5:  _pm_score += 3
            if abs(_gap) >= 1.0 and _pm_score > 0:
                _pm_score += 4   # gap confirmed by pre-market activity
            if direction == "LONG":
                score += _pm_score if _gap >= 0 else -_pm_score
            else:
                score += _pm_score if _gap <= 0 else -_pm_score
        except Exception:
            pass

        # ── Global Market Context (inter-market: VIX, gold, yields, calendar) ──
        gmc_adj = ctx.get("gmc_score", 0.0)
        if gmc_adj != 0.0:
            score += max(-20, min(gmc_adj, 20))   # cap ±20
            if abs(gmc_adj) >= 5:
                logger.debug(f"GMC adj={gmc_adj:+.1f} | {ctx.get('gmc_reasons', [])[:2]}")

        # ── Economic Calendar (FOMC/CPI/NFP) ─────────────
        cal_adj = ctx.get("calendar_score", 0.0)
        if cal_adj != 0.0:
            score += max(-20, min(cal_adj, 10))   # hard cap: no trading bonuses > 10 from calendar
            logger.debug(f"Calendar adj={cal_adj:+.1f} | {ctx.get('calendar_note', '')}")

        # ── Sector Rotation bias ──────────────────────────
        sector_adj = ctx.get("sector_adj", 0.0)
        if sector_adj != 0.0:
            score += max(-10, min(sector_adj, 10))
            logger.debug(f"Sector adj={sector_adj:+.1f} ({ctx.get('sector_name', '?')})")

        # TICK proxy from market breadth (NYSE TICK equivalent)
        try:
            import config as _cfgTP
            if getattr(_cfgTP, 'TICK_PROXY_ENABLED', True):
                _breadth = float(getattr(ind, 'breadth_score', 50) or 50)
                if direction in ('LONG', 'BUY'):
                    if _breadth >= 80:
                        score += 4;  logger.debug(f"{symbol}: TICK_PROXY breadth={_breadth:.0f} +4")
                    elif _breadth >= 70:
                        score += 2
                    elif _breadth <= 35:
                        score -= 3;  logger.debug(f"{symbol}: TICK_PROXY breadth={_breadth:.0f} -3 (bears control)")
                else:  # SHORT
                    if _breadth <= 35:
                        score += 4;  logger.debug(f"{symbol}: TICK_PROXY breadth={_breadth:.0f} +4 (sellers in control)")
                    elif _breadth <= 45:
                        score += 2
                    elif _breadth >= 75:
                        score -= 3
        except Exception:
            pass

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
        from config import ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER, ATR_T1_MULTIPLIER
        curr = df_5m.iloc[-1]
        ltp = float(curr["close"])
        atr = max(ind.atr, ltp * 0.003)  # Minimum 0.3% ATR

        # VIX-adaptive stop: high VIX = tighter stop (moves are larger, drawdowns faster)
        # _last_vix is VIX size_multiplier from GlobalMarketContext.get_vix_regime():
        #   0.30 = EXTREME_FEAR, 0.50 = HIGH_FEAR, 0.70 = ELEVATED, 1.0 = NORMAL
        _sl_mult = ATR_SL_MULTIPLIER
        _tp_mult_adj = ATR_TP_MULTIPLIER
        try:
            _vix_size_mult = getattr(self, "_last_vix", 1.0) or 1.0
            if _vix_size_mult <= 0.35:   # EXTREME_FEAR (VIX equiv > 35)
                _sl_mult     = ATR_SL_MULTIPLIER * 0.60
                _tp_mult_adj = ATR_TP_MULTIPLIER * 0.70
            elif _vix_size_mult <= 0.55:  # HIGH_FEAR (VIX equiv 25-35)
                _sl_mult     = ATR_SL_MULTIPLIER * 0.75
                _tp_mult_adj = ATR_TP_MULTIPLIER * 0.85
            elif _vix_size_mult <= 0.75:  # ELEVATED (VIX equiv 18-25)
                _sl_mult     = ATR_SL_MULTIPLIER * 0.90
                _tp_mult_adj = ATR_TP_MULTIPLIER * 0.95
        except Exception:
            pass

        if direction == "LONG":
            entry     = round_to_tick_size(ltp)
            stop_loss = round_to_tick_size(entry - _sl_mult * atr)
            target_1  = round_to_tick_size(entry + ATR_T1_MULTIPLIER * (entry - stop_loss))
            target_2  = round_to_tick_size(entry + _tp_mult_adj * (entry - stop_loss))
        else:  # SHORT
            entry     = round_to_tick_size(ltp)
            stop_loss = round_to_tick_size(entry + _sl_mult * atr)
            target_1  = round_to_tick_size(entry - ATR_T1_MULTIPLIER * (stop_loss - entry))
            target_2  = round_to_tick_size(entry - _tp_mult_adj * (stop_loss - entry))

        sl_distance = abs(entry - stop_loss)
        # Use target_2 for R:R — measures institutional target (2.5x), not just scalp T1 (1.5x).
        # target_1-based R:R was always exactly 1.5 = MIN_RISK_REWARD, so the gate never rejected.
        risk_reward = abs(target_2 - entry) / sl_distance if sl_distance > 0 else 2.5

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
            time_stop_minutes=20 if quality_grade == "A+" else 30,
        )
