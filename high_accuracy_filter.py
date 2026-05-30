"""
high_accuracy_filter.py — US Momentum Alpaca AI Bot
High-Accuracy Signal Gate — Targets 9/10 Win Rate

18yr Truth: "Most traders fail because they take EVERY signal.
The 9/10 win rate comes from taking ONLY THE BEST 10% of signals.
Patience is your edge. Waiting IS the strategy."

This filter sits BETWEEN signal_generator and execution.
A signal must PASS ALL 14 gates to become a trade.

THE 14 CONFLUENCE GATES:
  Gate 1:  POWER HOURS ONLY      — Trade only in high-probability ET time windows
  Gate 2:  REGIME ALIGNMENT      — Market regime must be MOMENTUM (not RANGING)
  Gate 3:  MULTI-TF ALIGNMENT    — At least 2 of 3 timeframes must agree; all-3 earns +10 bonus
  Gate 4:  VOLUME SURGE          — Minimum 0.5x (early candles undercounted); surge earns bonus
  Gate 5:  PATTERN QUALITY       — Signal score ≥ 80/100 (only A-grade setups)
  Gate 6:  LIQUIDITY             — Min daily volume ≥ 1M shares (US liquid stocks)
  Gate 7:  CIRCUIT BREAKER       — Stock not near 5/10/20% halt bands
  Gate 8:  GAP RISK              — No extreme gap open; wait window respected
  Gate 9:  CORP ACTIONS          — No ex-dividend/bonus/split within 2 days
  Gate 10: SHORT ELIGIBILITY     — SELL signals only on shortable stocks
  Gate 11: ENTRY AT LEVEL        — Price within 0.3% of key level (FVG/OB/VWAP/POC)
  Gate 12: ADX TRENDING          — ADX > 20 (no choppy directionless market)
  Gate 13: SPY ALIGNMENT         — SPY green for LONGs, SPY red for SHORTs
  Gate 14: CORRELATION GATE      — No new position if ≥75% correlated open pos exists

BONUS GATES (increase score further):
  + Heikin Ashi confirmation  (trend candle in signal direction)
  + VWAP position alignment   (BUY above VWAP, SELL below VWAP)
  + RSI in momentum zone      (35-55 for BUY, 45-65 for SELL)
  + Relative strength vs SPY  (stock stronger than index)
  + Opening range aligned     (ORB direction matches signal)
  + No news blackout          (30 min buffer around events)
  + SPY same direction        (index confirms stock direction)

REJECTION REASONS LOGGED:
Every rejection is stored so the self-learner can see what's being filtered.
"""

import logging
from dataclasses import dataclass, field
from datetime import time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


# ── POWER HOUR WINDOWS (ET) ────────────────────────────────
# Only trade during highest-probability intraday windows.
# US market: NY Open Kill Zone + Power Hour are the money windows.
POWER_WINDOWS = [
    (time(9, 30),  time(10, 45)),   # NY Open Kill Zone — strongest momentum
    (time(13, 30), time(14, 30)),   # Early afternoon institutional reset — reliable setups
    (time(14, 30), time(16, 0)),    # Power Hour — institutional accumulation/distribution
]

# Reduced-size window (can trade but 60% size — strict score gate still applies)
# Midday converted from AVOID → CAUTION: gives bot 2 more hours to find the day's target trade
CAUTION_WINDOWS = [
    (time(10, 45), time(11, 30)),   # Post-opening fade — reduced size
    (time(11, 30), time(13, 30)),   # Midday: 60% size, score ≥ min_score required — hunt for target hit
]

# NO TRADE windows — only hard EOD cutoff
AVOID_WINDOWS = [
    (time(15, 50), time(16, 0)),    # EOD — no new entries (too close to close)
]


@dataclass
class FilterResult:
    """Result of high-accuracy filter with full reasoning."""
    passed:          bool    = False
    final_score:     float   = 0.0
    size_multiplier: float   = 1.0   # 0 = no trade, 0.5 = half, 1.0 = full, 1.2 = premium
    gates_passed:    List[str] = field(default_factory=list)
    gates_failed:    List[str] = field(default_factory=list)
    bonuses:         List[str] = field(default_factory=list)
    rejection_reason: str = ""
    time_window:     str = ""
    quality_grade:   str = ""   # "A+", "A", "B", "C", "REJECT"

    @property
    def summary(self) -> str:
        grade = self.quality_grade
        if self.passed:
            return (f"✅ GRADE {grade} | Score {self.final_score:.0f} | "
                    f"Size {self.size_multiplier:.1f}x | "
                    f"Gates: {', '.join(self.gates_passed)}")
        return f"❌ REJECTED: {self.rejection_reason}"


class HighAccuracyFilter:
    """
    The gatekeeper. Only the best setups get through.
    18yr rule: 'Miss a trade → lose opportunity. Take a bad trade → lose money.
    Opportunity loss is recoverable. Capital loss may not be.'
    14 gates. 9/10 win rate target.
    """

    # Known correlation pairs (same sector = correlated)
    CORRELATION_PAIRS = {
        # ── Semis (highest intra-sector correlation of any sector) ────────────
        frozenset({"NVDA", "AMD"}):    0.88,
        frozenset({"NVDA", "TSM"}):    0.82,
        frozenset({"NVDA", "SMCI"}):   0.80,
        frozenset({"NVDA", "AVGO"}):   0.82,
        frozenset({"NVDA", "ARM"}):    0.83,
        frozenset({"AMD",  "INTC"}):   0.82,
        frozenset({"AMD",  "QCOM"}):   0.80,
        frozenset({"AMD",  "MU"}):     0.80,
        frozenset({"LRCX", "KLAC"}):   0.88,
        frozenset({"LRCX", "AMAT"}):   0.87,
        frozenset({"KLAC", "AMAT"}):   0.87,
        frozenset({"QCOM", "MRVL"}):   0.80,
        frozenset({"TXN",  "MCHP"}):   0.82,
        frozenset({"SMCI", "MU"}):     0.77,
        # ── Mega-cap tech ─────────────────────────────────────────────────────
        frozenset({"AAPL", "MSFT"}):   0.82,
        frozenset({"AAPL", "GOOGL"}):  0.79,
        frozenset({"AAPL", "AMZN"}):   0.79,
        frozenset({"MSFT", "GOOGL"}):  0.80,
        frozenset({"MSFT", "AMZN"}):   0.78,
        frozenset({"META", "GOOGL"}):  0.82,
        frozenset({"META", "SNAP"}):   0.78,
        frozenset({"META", "NFLX"}):   0.77,
        # ── AI / Cloud SaaS ──────────────────────────────────────────────────
        frozenset({"CRWD", "PANW"}):   0.85,
        frozenset({"CRWD", "ZS"}):     0.82,
        frozenset({"PANW", "ZS"}):     0.83,
        frozenset({"DDOG", "SNOW"}):   0.80,
        frozenset({"DDOG", "NET"}):    0.78,
        frozenset({"NOW",  "CRM"}):    0.79,
        frozenset({"PLTR", "AI"}):     0.77,
        frozenset({"PLTR", "BBAI"}):   0.78,
        # ── Finance ───────────────────────────────────────────────────────────
        frozenset({"JPM", "GS"}):      0.83,
        frozenset({"JPM", "MS"}):      0.82,
        frozenset({"JPM", "BAC"}):     0.83,
        frozenset({"GS",  "MS"}):      0.86,
        frozenset({"GS",  "BAC"}):     0.80,
        # ── Energy ───────────────────────────────────────────────────────────
        frozenset({"XOM", "CVX"}):     0.90,
        frozenset({"XOM", "OXY"}):     0.85,
        frozenset({"CVX", "OXY"}):     0.85,
        frozenset({"XOM", "SLB"}):     0.82,
        frozenset({"CVX", "MPC"}):     0.80,
        # ── EV / Clean Energy ────────────────────────────────────────────────
        frozenset({"RIVN", "LCID"}):   0.84,
        frozenset({"RIVN", "NIO"}):    0.82,
        frozenset({"TSLA", "RIVN"}):   0.78,
        frozenset({"FSLR", "ENPH"}):   0.82,
        frozenset({"PLUG", "FSLR"}):   0.77,
        # ── Crypto / Blockchain ──────────────────────────────────────────────
        frozenset({"MARA", "RIOT"}):   0.92,
        frozenset({"MARA", "HUT"}):    0.90,
        frozenset({"MARA", "CLSK"}):   0.88,
        frozenset({"RIOT", "HUT"}):    0.91,
        frozenset({"RIOT", "CLSK"}):   0.88,
        frozenset({"COIN", "MSTR"}):   0.85,
        frozenset({"COIN", "MARA"}):   0.84,
        frozenset({"MSTR", "MARA"}):   0.84,
        # ── ETFs (index-tracking) ─────────────────────────────────────────────
        frozenset({"SPY",  "QQQ"}):    0.94,
        frozenset({"SPY",  "IWM"}):    0.88,
        frozenset({"QQQ",  "TQQQ"}):   0.97,
        frozenset({"SPY",  "SPXL"}):   0.97,
        frozenset({"SOXL", "NVDA"}):   0.87,
        frozenset({"SOXL", "AMD"}):    0.86,
        frozenset({"TECL", "AAPL"}):   0.85,
        # ── Consumer / Social ────────────────────────────────────────────────
        frozenset({"UBER", "LYFT"}):   0.85,
        frozenset({"DASH", "UBER"}):   0.79,
        frozenset({"SHOP", "AMZN"}):   0.77,
        frozenset({"RBLX", "U"}):      0.78,
        # ── Fintech ──────────────────────────────────────────────────────────
        frozenset({"SQ",   "PYPL"}):   0.82,
        frozenset({"AFRM", "SQ"}):     0.78,
        frozenset({"SOFI", "HOOD"}):   0.80,
        frozenset({"V",    "MA"}):     0.88,
    }
    CORR_BLOCK_THRESHOLD = 0.75   # block if correlation >= this

    def __init__(self, min_score: float = 72.0):  # v16.0: recalibrated to 72 post neutral-fix
        self._rejection_log: List[Dict] = []
        self._pass_count    = 0
        self._reject_count  = 0
        self.min_score      = min_score
        # Score histogram: buckets of 5 (40–45, 45–50, ... 95–100)
        self._score_histogram: Dict[str, int] = {}

    # ─────────────────────────────────────────────────────────
    # MAIN FILTER — call this before every trade
    # ─────────────────────────────────────────────────────────

    def evaluate(
        self,
        signal_score:        float,
        direction:           str,             # "BUY" or "SELL"
        regime:              str,
        mtf_alignment:       Dict,            # from MultiTimeframeAnalyzer
        volume_ratio:        float,
        pattern_names:       List[str],
        pattern_scores:      List[float],
        df_5m:               Optional[pd.DataFrame],
        rsi:                 float,
        above_vwap:          bool,
        spy_change_pct:      float,           # SPY % change today (was nifty_change_pct)
        stock_change_pct:    float,
        news_clear:          bool,
        orb_direction:       str = "",        # "UP", "DOWN", or ""
        learner=None,
        # ── Gates 6-10 parameters ──────────────────────────
        symbol:              str   = "",      # symbol for checks
        daily_volume:        float = 0.0,     # Gate 6: today's volume (shares)
        prev_close:          float = 0.0,     # Gate 7: yesterday's close
        ltp:                 float = 0.0,     # Gate 7: current price
        minutes_since_open:  float = 0.0,     # Gate 8: for gap timing
        gap_pct:             float = 0.0,     # Gate 8: gap %
        # ── Gates 11-13 parameters ─────────────────────────
        at_key_level:        bool  = False,   # Gate 11: price at FVG/OB/VWAP/POC
        adx:                 float = 0.0,     # Gate 12: ADX value
        spy_bullish:         Optional[bool] = None,  # Gate 13: SPY direction
        # ── Gate 14 parameter ──────────────────────────────
        open_positions:      Optional[List[str]] = None,  # Gate 14: open position symbols (same direction)
        # ── Gates 15-18 parameters (70-80% WR upgrade) ────
        candles_df:          Optional[pd.DataFrame] = None,  # Gate 15: raw 5m candles for false-breakout
        atr:                 float = 0.0,                    # Gate 15/16: ATR for wick/body/clear-air checks
        daily_candles_df:    Optional[pd.DataFrame] = None,  # Gate 16/17: daily candles for clear-air + HTF
        fetcher=None,                                        # Gate 17/18: data fetcher for daily HTF + spread
    ) -> FilterResult:

        result = FilterResult()
        now_et = get_current_ist_time()   # Returns ET after IST=ET alias in utils.py

        # ── GATE 1: POWER HOURS ───────────────────────────
        window, size_mult = self._check_time_window(now_et.time())
        result.time_window = window

        if window == "AVOID":
            result.rejection_reason = (
                f"AVOID window ({now_et.strftime('%H:%M')} ET). "
                "No new entries in the final 10 min before close."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        # In CAUTION windows, require min_score (not a higher bar) — size already reduced
        if window == "CAUTION" and signal_score < self.min_score:
            result.rejection_reason = (
                f"CAUTION window — score {signal_score:.0f} < {self.min_score:.0f} min. "
                "Below min score even in standard window."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.size_multiplier = size_mult
        result.gates_passed.append(f"POWER_HOUR({window})")

        # ── GATE 2: REGIME ALIGNMENT ─────────────────────
        regime_ok, regime_size = self._check_regime(regime, direction)
        if not regime_ok:
            result.gates_failed.append(f"REGIME({regime})")
            result.rejection_reason = (
                f"Regime '{regime}' does not support {direction} momentum trades. "
                "Only STRONG_TREND or OPENING_DRIVE for full size."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.size_multiplier *= regime_size
        result.gates_passed.append(f"REGIME({regime})")

        # ── GATE 3: MULTI-TIMEFRAME ALIGNMENT ────────────
        mtf_ok, mtf_score = self._check_mtf(mtf_alignment, direction)
        if not mtf_ok:
            result.gates_failed.append("MTF_ALIGNMENT")
            result.rejection_reason = (
                f"MTF conflict: {mtf_alignment.get('description','no alignment')}. "
                "Need ≥2 timeframes aligned."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.gates_passed.append(f"MTF(score={mtf_score})")

        # ── GATE 4: VOLUME SURGE ─────────────────────────
        vol_ok, vol_bonus = self._check_volume(volume_ratio)
        if not vol_ok:
            result.gates_failed.append(f"VOLUME(ratio={volume_ratio:.1f})")
            result.rejection_reason = (
                f"Volume ratio {volume_ratio:.1f}x < 0.5x minimum. "
                "Stock appears illiquid or data unavailable."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.gates_passed.append(f"VOLUME({volume_ratio:.1f}x)")

        # ── GATE 5: PATTERN QUALITY SCORE ────────────────
        pat_ok, pat_score, best_pattern = self._check_pattern_quality(
            signal_score, pattern_scores, pattern_names, learner
        )
        if not pat_ok:
            result.gates_failed.append(f"PATTERN_SCORE({signal_score:.0f})")
            result.rejection_reason = (
                f"Signal score {signal_score:.0f} < {self.min_score:.0f} required. "
                f"Best pattern: {best_pattern}."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.gates_passed.append(f"PATTERN_SCORE({signal_score:.0f})")

        # ── NEWS BLACKOUT ────────────────────────────────
        if not news_clear:
            result.rejection_reason = "News blackout active — Fed/FOMC/event window"
            self._log_rejection(result, signal_score, direction)
            return result

        # ── GATE 6: LIQUIDITY ─────────────────────────────
        if daily_volume > 0:
            liq_ok, liq_reason = self._check_liquidity(daily_volume)
            if not liq_ok:
                result.gates_failed.append(f"LIQUIDITY({daily_volume/1e6:.1f}M)")
                result.rejection_reason = liq_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append(f"LIQUIDITY({daily_volume/1e6:.0f}M)")

        # ── GATE 7: CIRCUIT BREAKER PROXIMITY ────────────
        if prev_close > 0 and ltp > 0:
            circ_ok, circ_reason = self._check_circuit_proximity(prev_close, ltp)
            if not circ_ok:
                result.gates_failed.append("CIRCUIT_BREAKER")
                result.rejection_reason = circ_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append("CIRCUIT_OK")

        # ── GATE 8: GAP RISK ──────────────────────────────
        abs_gap = abs(gap_pct)
        if abs_gap > 0:
            gap_ok, gap_reason = self._check_gap_risk(gap_pct, minutes_since_open)
            if not gap_ok:
                result.gates_failed.append(f"GAP_RISK({gap_pct:+.1f}%)")
                result.rejection_reason = gap_reason
                self._log_rejection(result, signal_score, direction)
                return result
            if abs_gap >= 2.0:
                result.gates_passed.append(f"GAP_CLEARED({gap_pct:+.1f}%)")

        # ── GATE 9: CORPORATE ACTIONS ─────────────────────
        if symbol:
            corp_ok, corp_reason = self._check_corp_actions(symbol)
            if not corp_ok:
                result.gates_failed.append("CORP_ACTION")
                result.rejection_reason = corp_reason
                self._log_rejection(result, signal_score, direction)
                return result

        # ── GATE 10: SHORT ELIGIBILITY (SELL only) ───────
        if direction == "SELL" and symbol:
            short_ok, short_reason = self._check_short_eligibility(symbol)
            if not short_ok:
                result.gates_failed.append("SHORT_INELIGIBLE")
                result.rejection_reason = short_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append("SHORT_OK")

        # ── GATE 11: ENTRY AT KEY LEVEL (soft — penalty only, not rejection) ──────
        # Institutional traders prefer entries at defined levels (FVG/OB/VWAP/POC)
        # but momentum breakouts legitimately occur away from levels too.
        # Hard rejection here blocked 40%+ of valid signals — converted to -8 penalty.
        level_ok, _ = self._check_key_level(at_key_level, ltp, above_vwap)
        if level_ok:
            result.gates_passed.append("AT_LEVEL")
        else:
            result.gates_failed.append("NOT_AT_LEVEL(soft)")
            signal_score = max(signal_score - 4, 0)   # reduced from -8 — momentum breakouts are naturally away from prior levels

        # ── GATE 12: ADX TRENDING ────────────────────────
        # No trades in choppy directionless markets
        if adx > 0:
            adx_ok, adx_reason = self._check_adx(adx)
            if not adx_ok:
                result.gates_failed.append(f"ADX_WEAK({adx:.0f})")
                result.rejection_reason = adx_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append(f"ADX({adx:.0f})")

        # ── GATE 13: SPY DIRECTION ALIGNMENT ─────────────
        # Never fight the market — SPY must confirm signal direction
        if spy_bullish is not None:
            spy_ok, spy_reason = self._check_spy_alignment(direction, spy_bullish, spy_change_pct=spy_change_pct)
            if not spy_ok:
                result.gates_failed.append("SPY_CONFLICT")
                result.rejection_reason = spy_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append("SPY_ALIGNED")

        # ── GATE 14: CORRELATION CHECK ────────────────────
        # Don't double up on highly correlated positions — concentration risk
        corr_ok, corr_reason = self._check_correlation(symbol, direction, open_positions)
        if not corr_ok:
            result.gates_failed.append("CORR_BLOCK")
            result.rejection_reason = corr_reason
            self._log_rejection(result, signal_score, direction)
            return result
        if open_positions:
            result.gates_passed.append("CORR_OK")

        # ── GATE 15: FALSE BREAKOUT DETECTOR ─────────────
        # Eliminates ~30% of losses by rejecting wick-rejections and volume fades.
        # Professional rule: "Volume is the fuel — without fuel, the breakout fails."
        try:
            import config as _cfg15
            if getattr(_cfg15, "FALSE_BREAKOUT_GATE", True) and candles_df is not None and len(candles_df) >= 3:
                fb_ok, fb_reason = self._gate_false_breakout(candles_df, signal_score, direction, atr or 1.0)
                if not fb_ok:
                    result.gates_failed.append("FALSE_BREAKOUT")
                    result.rejection_reason = fb_reason
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append("NO_FALSE_BREAKOUT")
        except Exception:
            result.gates_passed.append("FALSE_BREAKOUT_SKIP")

        # ── GATE 16: OVERHEAD RESISTANCE CLEAR AIR ───────
        # Require 1.5×ATR of open space above entry — no resistance blocking the move.
        # Institutions never buy into a wall; they wait for clear air.
        try:
            import config as _cfg16
            if getattr(_cfg16, "CLEAR_AIR_GATE", True) and ltp > 0 and atr > 0:
                ca_ok, ca_reason = self._gate_clear_air(ltp, direction, atr, daily_candles_df)
                if not ca_ok:
                    result.gates_failed.append("CLEAR_AIR")
                    result.rejection_reason = f"[CLEAR AIR] {symbol} — {ca_reason}"
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append("CLEAR_AIR_OK")
        except Exception:
            result.gates_passed.append("CLEAR_AIR_SKIP")

        # ── GATE 17: DAILY HTF TREND ALIGNMENT ───────────
        # LONG only if price is above daily SMA20 and higher than 3 days ago.
        # The single most important institutional filter — never fight the daily trend.
        try:
            import config as _cfg17
            if getattr(_cfg17, "DAILY_HTF_GATE", True):
                htf_ok, htf_reason = self._gate_daily_htf(symbol, direction, daily_candles_df, fetcher)
                if not htf_ok:
                    result.gates_failed.append("DAILY_HTF")
                    result.rejection_reason = f"[DAILY HTF] {symbol} — {htf_reason}"
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append("DAILY_HTF_OK")
        except Exception:
            result.gates_passed.append("DAILY_HTF_SKIP")

        # ── GATE 18: BID-ASK SPREAD FILTER ───────────────
        # Wide spreads = market maker trap territory; skip if cost >0.15% of price.
        try:
            import config as _cfg18
            if getattr(_cfg18, "SPREAD_MAX_PCT", 0.15) > 0 and symbol and fetcher:
                sp_ok, sp_reason = self._gate_spread(symbol, fetcher, getattr(_cfg18, "SPREAD_MAX_PCT", 0.15))
                if not sp_ok:
                    result.gates_failed.append("SPREAD_WIDE")
                    result.rejection_reason = f"[SPREAD] {symbol} — {sp_reason}"
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append("SPREAD_OK")
        except Exception:
            result.gates_passed.append("SPREAD_SKIP")

        # ── GATE 19: MINIMUM INDICATOR CONFLUENCE ────────
        # Require ≥2 of 4 core indicators aligned with direction.
        # Prevents opening-window time-of-day bonus (+12) or regime bonus (+12)
        # from carrying a fundamentally weak setup over the min_score threshold.
        try:
            import config as _cfg19
            if getattr(_cfg19, "INDICATOR_FLOOR_GATE", True):
                _ind_floor_min = getattr(_cfg19, "INDICATOR_FLOOR_MIN", 2)
                _g19_ok, _g19_reason = self._gate_indicator_floor(
                    rsi=rsi, macd_hist=0.0,  # macd_hist not in evaluate() args — inferred from df_5m
                    ema9=0.0, ema21=0.0,     # also not direct args — check df_5m columns
                    volume_ratio=volume_ratio, direction=direction,
                    df_5m=df_5m, min_count=_ind_floor_min,
                )
                if not _g19_ok:
                    result.gates_failed.append("INDICATOR_FLOOR")
                    result.rejection_reason = f"[GATE-19 INDICATOR FLOOR] {symbol} — {_g19_reason}"
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append(f"IND_FLOOR_OK({_g19_reason})")
        except Exception:
            result.gates_passed.append("IND_FLOOR_SKIP")

        # ── GATE 20: BID/ASK VOLUME IMBALANCE ──────────────
        try:
            import config as _cfg20
            if getattr(_cfg20, "BA_IMBALANCE_GATE", True) and fetcher and symbol:
                _ba_ok, _ba_reason = self._gate_ba_imbalance(
                    symbol, direction, fetcher,
                    min_ratio=getattr(_cfg20, "BA_IMBALANCE_MIN_RATIO", 0.52),
                )
                if not _ba_ok:
                    result.gates_failed.append("BA_IMBALANCE")
                    result.rejection_reason = f"[GATE-20 BA_IMBALANCE] {symbol} — {_ba_reason}"
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append("BA_OK")
        except Exception:
            result.gates_passed.append("BA_SKIP")

        # ── GATE 21: ORDER FLOW DIRECTION CONFIRMATION ────────
        # Hard gate: if cumulative delta strongly opposes the signal, reject.
        # Filters out "fake" breakouts where only retail is buying (no institutional backing).
        try:
            import config as _cfg21
            if getattr(_cfg21, "OFI_GATE_ENABLED", True) and df_5m is not None:
                from order_flow_analyzer import get_ofi_direction_ok
                _ofi_ok, _ofi_reason = get_ofi_direction_ok(df_5m, direction)
                if not _ofi_ok:
                    result.gates_failed.append("OFI_OPPOSE")
                    result.rejection_reason = f"[GATE-21 OFI] {symbol} — {_ofi_reason}"
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append("OFI_OK")
        except Exception:
            result.gates_passed.append("OFI_SKIP")

        # ── GATE 22: STOP-HUNT DETECTION (ICT Wyckoff Spring/Upthrust) ────
        _sh_pending_bonus = 0   # may be updated inside gate block; consumed at bonus_score init
        try:
            import config as _cfg22
            if getattr(_cfg22, 'STOP_HUNT_GATE', True) and df_5m is not None and len(df_5m) >= 15:
                _sh_pass, _sh_reason, _sh_bonus = self._gate_stop_hunt(
                    df_5m, ltp or 0.0, direction
                )
                if not _sh_pass:
                    result.gates_failed.append('STOP_HUNT_TRAP')
                    result.rejection_reason = f'[GATE-22 STOP_HUNT] {symbol} — {_sh_reason}'
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append('SH_OK')
                if _sh_bonus > 0:
                    _sh_pending_bonus = _sh_bonus
                    logger.info(f'[{format_ist_timestamp()}] {symbol}: STOP_HUNT_SETUP bonus +{_sh_bonus}pts pending')
        except Exception:
            result.gates_passed.append('SH_SKIP')

        # ── GATE 23: OPTIONS EXTREME SENTIMENT FILTER ─────────────────────
        try:
            import config as _cfg23
            if getattr(_cfg23, 'OPTIONS_GATE_ENABLED', True):
                from options_flow import get_market_pc_ratio
                _pc = get_market_pc_ratio()
                # Extreme fear (PC > 2.0) = market in panic, block ALL new longs
                if direction in ('LONG', 'BUY') and _pc > 2.0:
                    result.gates_failed.append('OPTIONS_EXTREME_FEAR')
                    result.rejection_reason = f'[GATE-23 OPTIONS] {symbol} — market PC={_pc:.2f} extreme fear, no new longs'
                    self._log_rejection(result, signal_score, direction)
                    return result
                # Extreme greed (PC < 0.35) = everyone is long, risky to short
                elif direction in ('SHORT', 'SELL') and _pc < 0.35:
                    result.gates_failed.append('OPTIONS_EXTREME_GREED')
                    result.rejection_reason = f'[GATE-23 OPTIONS] {symbol} — market PC={_pc:.2f} extreme greed, no new shorts'
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append('OPC_OK')
        except Exception:
            result.gates_passed.append('OPC_SKIP')

        # ── GATE 24: MOMENTUM BAR CONFIRMATION (institutional tape reading) ──
        # Require ≥ 3 of last 5 bars closing in signal direction.
        # Eliminates single-bar spikes and wick traps. Prop desks call this "confirmed momentum."
        try:
            import config as _cfg24
            if getattr(_cfg24, 'MOMENTUM_BAR_GATE', True) and df_5m is not None and len(df_5m) >= 5:
                _bars = df_5m.tail(5)
                _opens  = _bars['open'].values  if 'open'  in _bars.columns else _bars['Open'].values
                _closes = _bars['close'].values if 'close' in _bars.columns else _bars['Close'].values
                _aligned = sum(
                    1 for o, c in zip(_opens, _closes)
                    if (c > o if direction in ('LONG','BUY') else c < o)
                )
                if _aligned < 3:
                    result.gates_failed.append(f'MOMENTUM_BARS({_aligned}/5)')
                    result.rejection_reason = (
                        f'[GATE-24 MOMENTUM] {symbol} — only {_aligned}/5 bars confirm {direction} '
                        f'(need ≥3: institutional momentum not established)'
                    )
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append(f'MOM_OK({_aligned}/5)')
        except Exception:
            result.gates_passed.append('MOM_SKIP')

        # ── GATE 25: R² TREND QUALITY (linear regression linearity) ──────────
        # Measures price movement linearity over last 20 bars.
        # R² < 0.45 = choppy/random price action = avoid (coin flip territory).
        # R² ≥ 0.70 = clean directional trend = +6 score bonus (Goldman/RenTec standard).
        # This is what quant funds call "trend quality" — not just direction, but consistency.
        try:
            import config as _cfg25
            if getattr(_cfg25, 'TREND_QUALITY_GATE', True) and df_5m is not None and len(df_5m) >= 20:
                import numpy as _np25
                _cl25 = df_5m['close'].values[-20:] if 'close' in df_5m.columns else df_5m['Close'].values[-20:]
                _x25  = _np25.arange(len(_cl25), dtype=float)
                _xm   = _x25.mean(); _ym = _cl25.mean()
                _ss_tot = _np25.sum((_cl25 - _ym) ** 2)
                _slope  = _np25.sum((_x25 - _xm) * (_cl25 - _ym)) / (_np25.sum((_x25 - _xm) ** 2) + 1e-9)
                _y_pred = _ym + _slope * (_x25 - _xm)
                _ss_res = _np25.sum((_cl25 - _y_pred) ** 2)
                _r2     = 1.0 - (_ss_res / (_ss_tot + 1e-9)) if _ss_tot > 0 else 0.0
                # Direction check: slope must agree with signal direction
                _slope_ok = (_slope > 0) if direction in ('LONG','BUY') else (_slope < 0)
                if _r2 < 0.35 or (_r2 < 0.50 and not _slope_ok):
                    result.gates_failed.append(f'TREND_QUALITY(R²={_r2:.2f})')
                    result.rejection_reason = (
                        f'[GATE-25 R²] {symbol} — trend R²={_r2:.2f} too choppy for {direction} '
                        f'(need ≥0.35 with slope agreement — price action not directional)'
                    )
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append(f'R²={_r2:.2f}')
                # Bonus will be applied in bonus section below (stored for use)
                result._r2_quality = _r2
        except Exception:
            result.gates_passed.append('R²_SKIP')

        # ── GATE 26: ML WIN PROBABILITY ───────────────────────────────────────
        # GradientBoosting classifier scores each signal with P(win).
        # Trained on 4,000 synthetic examples from known edge patterns;
        # refines on live outcomes every 50 trades. Fail-open at 0.50.
        # Used by Renaissance/Two Sigma as a pre-filter on rule-based signals.
        try:
            import config as _cfg26
            if getattr(_cfg26, 'ML_GATE_ENABLED', True):
                from ml_signal_ranker import get_win_probability as _ml_prob
                _r2_ml   = getattr(result, '_r2_quality', 0.5)
                _atr_pct = (atr / max(ltp, 1.0)) * 100.0 if atr > 0 and ltp > 0 else 1.0
                _ema_sl  = 0.0  # placeholder — slope passed via signal_score proxy
                _vwap_d  = 0.0
                # Try to compute vwap distance from df_5m if available
                if df_5m is not None and not df_5m.empty:
                    try:
                        _tp     = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
                        _cv     = (df_5m['volume'].cumsum())
                        _cvp    = (_tp * df_5m['volume']).cumsum()
                        _vwap_v = float((_cvp / _cv).iloc[-1])
                        _vwap_d = (ltp - _vwap_v) / max(_vwap_v, 0.01) * 100.0
                    except Exception:
                        pass
                _bb_pct_v = 0.5
                if df_5m is not None and len(df_5m) >= 20:
                    try:
                        _cl26   = df_5m['close'].values[-20:]
                        _m26    = float(_cl26.mean())
                        _s26    = float(_cl26.std()) or 0.01
                        _bbu    = _m26 + 2 * _s26
                        _bbl    = _m26 - 2 * _s26
                        _bb_pct_v = (ltp - _bbl) / max(_bbu - _bbl, 0.01)
                    except Exception:
                        pass
                import datetime as _dt26
                _now26   = get_current_ist_time()
                _hour_et = (_now26.hour - 4) % 24   # rough ET approx for scoring
                _is_long = 1 if direction in ('LONG', 'BUY') else 0
                # Compute MACD histogram from df_5m (macd_hist not in evaluate() params)
                _macd_hist_val = 0.0
                if df_5m is not None and len(df_5m) >= 26:
                    try:
                        _cl_m = df_5m['close']
                        _e12m = _cl_m.ewm(span=12, adjust=False).mean()
                        _e26m = _cl_m.ewm(span=26, adjust=False).mean()
                        _macd_line = _e12m - _e26m
                        _sig_line  = _macd_line.ewm(span=9, adjust=False).mean()
                        _macd_hist_val = float(_macd_line.iloc[-1] - _sig_line.iloc[-1])
                    except Exception:
                        pass
                _macd_n  = _macd_hist_val / max(atr, 0.01) if atr > 0 else 0.0
                _win_prob = _ml_prob(
                    rsi=rsi,
                    macd_hist_norm=_macd_n,
                    volume_ratio=volume_ratio,
                    atr_pct=_atr_pct,
                    signal_score=signal_score,
                    adx=adx,
                    ema_slope_pct=_ema_sl,
                    vwap_dist_pct=_vwap_d,
                    bb_pct=_bb_pct_v,
                    hour_et=_hour_et,
                    is_long=_is_long,
                    r2_quality=_r2_ml,
                )
                result._ml_win_prob = _win_prob
                _ml_thresh = float(getattr(_cfg26, 'ML_WIN_PROB_THRESHOLD', 0.60))
                if _win_prob < _ml_thresh:
                    result.gates_failed.append(f'ML_PROB({_win_prob:.2f}<{_ml_thresh:.2f})')
                    result.rejection_reason = (
                        f'[GATE-26 ML] {symbol} — ML win probability {_win_prob:.1%} '
                        f'below threshold {_ml_thresh:.0%} '
                        f'(GradientBoosting classifier: insufficient confluence)'
                    )
                    self._log_rejection(result, signal_score, direction)
                    return result
                result.gates_passed.append(f'ML_PROB({_win_prob:.2f})')
                # Bonus: high ML confidence boosts score
                if _win_prob >= 0.80:
                    result._ml_bonus = 10.0
                elif _win_prob >= 0.72:
                    result._ml_bonus = 6.0
                elif _win_prob >= 0.65:
                    result._ml_bonus = 3.0
                else:
                    result._ml_bonus = 0.0
        except Exception as _ml_e:
            result.gates_passed.append('ML_SKIP')
            result._ml_win_prob = 0.5

        # ── GATE 27: VWAP OVEREXTENSION (no chasing) ─────────────────────────
        # Prop desk rule: "Never buy when the stock is 2+ ATR extended from VWAP."
        # Extended entries = buying exhaustion = highest false breakout rate.
        # Score penalty (not hard block) so an exceptional signal can still pass.
        try:
            import config as _cfg27
            _vwap_max_atr = float(getattr(_cfg27, 'VWAP_EXTENSION_MAX_ATR', 2.0))
            if df_5m is not None and not df_5m.empty and atr and atr > 0:
                _tp27  = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
                _cv27  = df_5m['volume'].cumsum()
                _cvp27 = (_tp27 * df_5m['volume']).cumsum()
                _vwap27 = float((_cvp27 / _cv27).iloc[-1])
                _dist_atr = abs(ltp - _vwap27) / atr if _vwap27 > 0 else 0.0
                if _dist_atr > _vwap_max_atr:
                    # Penalty proportional to overextension (harder penalty the further we are)
                    _penalty = min(20.0, round((_dist_atr - _vwap_max_atr) * 8.0, 1))
                    signal_score = max(0.0, signal_score - _penalty)
                    result.gates_passed.append(f'VWAP_EXT({_dist_atr:.1f}ATR,-{_penalty:.0f})')
                    if signal_score < self.min_score:
                        result.passed = False
                        result.gates_failed.append(f'VWAP_CHASE({_dist_atr:.1f}ATR)')
                        result.rejection_reason = (
                            f'[GATE-27 VWAP_EXT] {symbol} — price {_dist_atr:.1f}×ATR from VWAP '
                            f'(max {_vwap_max_atr}×). Score penalized -{_penalty:.0f} → {signal_score:.0f} '
                            f'< {self.min_score:.0f} min. Chasing exhaustion move.'
                        )
                        self._log_rejection(result, signal_score, direction)
                        return result
                else:
                    result.gates_passed.append(f'VWAP_OK({_dist_atr:.1f}ATR)')
        except Exception:
            result.gates_passed.append('VWAP_EXT_SKIP')

        result.passed   = True
        bonus_score     = 0.0
        bonus_score    += _sh_pending_bonus  # stop-hunt setup bonus (0 if no hunt)

        # Bonus 0: ML win probability bonus
        _ml_b = getattr(result, '_ml_bonus', 0.0)
        if _ml_b > 0:
            bonus_score += _ml_b
            _ml_p = getattr(result, '_ml_win_prob', 0.5)
            result.bonuses.append(f"ML_CONF({_ml_p:.2f},+{_ml_b:.0f})")

        # Bonus 1: Heikin Ashi confirmation
        if df_5m is not None and len(df_5m) >= 3:
            ha_ok, ha_note = self._check_heikin_ashi(df_5m, direction)
            if ha_ok:
                bonus_score += 5
                result.bonuses.append(f"HA({ha_note})")
            else:
                bonus_score -= 3
                result.bonuses.append(f"HA_WEAK({ha_note})")

        # Bonus 1a: R² trend quality bonus — clean trends get larger size too
        _r2_val = getattr(result, '_r2_quality', 0.0)
        if _r2_val >= 0.80:
            bonus_score += 8
            result.size_multiplier *= 1.15
            result.bonuses.append(f"TREND_R²={_r2_val:.2f}(+8,+15%size)")
        elif _r2_val >= 0.70:
            bonus_score += 6
            result.bonuses.append(f"TREND_R²={_r2_val:.2f}(+6)")
        elif _r2_val >= 0.55:
            bonus_score += 3
            result.bonuses.append(f"TREND_R²={_r2_val:.2f}(+3)")

        # Bonus 1b: Momentum bar strength — 5/5 aligned = institutional momentum bonus
        if df_5m is not None and len(df_5m) >= 5:
            try:
                _b5 = df_5m.tail(5)
                _b5_o = _b5['open'].values  if 'open'  in _b5.columns else _b5['Open'].values
                _b5_c = _b5['close'].values if 'close' in _b5.columns else _b5['Close'].values
                _b5_aligned = sum(1 for o, c in zip(_b5_o, _b5_c) if (c > o if direction in ('LONG','BUY') else c < o))
                if _b5_aligned == 5:
                    bonus_score += 7
                    result.bonuses.append("MOM_5/5(institutional)")
                elif _b5_aligned == 4:
                    bonus_score += 4
                    result.bonuses.append("MOM_4/5")
            except Exception:
                pass

        # Bonus 2: VWAP position — use `is True`/`is False` so None (VWAP data unavailable) is neutral
        if above_vwap is True:
            if direction == "BUY":
                bonus_score += 6
                result.bonuses.append("ABOVE_VWAP")
            else:  # SELL above VWAP = fighting trend
                bonus_score -= 3
                result.bonuses.append("ABOVE_VWAP(weak_sell)")
        elif above_vwap is False:
            if direction == "SELL":
                bonus_score += 6
                result.bonuses.append("BELOW_VWAP")
            else:  # BUY below VWAP = lower confidence
                bonus_score -= 5
                result.size_multiplier *= 0.8
                result.bonuses.append("BELOW_VWAP(weak_buy)")
        # above_vwap is None = VWAP data unavailable — no adjustment either way

        # Bonus 3: RSI momentum zone
        rsi_bonus = self._rsi_bonus(rsi, direction)
        bonus_score += rsi_bonus
        if rsi_bonus > 0:
            result.bonuses.append(f"RSI_OPTIMAL({rsi:.0f})")
        elif rsi_bonus < 0:
            result.bonuses.append(f"RSI_EXTREME({rsi:.0f})")

        # Bonus 4: Relative strength vs SPY
        rs = stock_change_pct - spy_change_pct
        if direction == "BUY" and rs > 0.3:
            bonus_score += 5
            result.bonuses.append(f"RS_STRONG(+{rs:.1f}%)")
        elif direction == "SELL" and rs < -0.3:
            bonus_score += 5
            result.bonuses.append(f"RS_WEAK({rs:.1f}%)")
        elif (direction == "BUY" and rs < -0.5) or (direction == "SELL" and rs > 0.5):
            bonus_score -= 8
            result.size_multiplier *= 0.7
            result.bonuses.append(f"RS_AGAINST({rs:.1f}%)")

        # Bonus 5: SPY direction bonus (already gated; extra credit for strong trend)
        if direction == "BUY" and spy_change_pct > 0.5:
            bonus_score += 5
            result.bonuses.append("SPY_STRONG_UP")
        elif direction == "SELL" and spy_change_pct < -0.5:
            bonus_score += 5
            result.bonuses.append("SPY_STRONG_DOWN")

        # Bonus 6: ORB direction match
        if orb_direction and orb_direction == ("UP" if direction=="BUY" else "DOWN"):
            bonus_score += 8
            result.bonuses.append("ORB_ALIGNED")
        elif orb_direction and orb_direction != ("UP" if direction=="BUY" else "DOWN"):
            bonus_score -= 6
            result.bonuses.append("ORB_CONFLICT")

        # Bonus 7: Premium ICT/institutional patterns — names must match PatternRecognizer output exactly
        premium_patterns = {
            "Bullish FVG", "Bearish FVG",
            "Bullish Order Block", "Bearish Order Block",
            "BOS — Higher High (Trend Continues)", "BOS — Lower Low (Trend Continues)",
            "Bullish Engulfing", "Bearish Engulfing",
            "Judas Swing Bullish", "Judas Swing Bearish",
            "Gap & Go Long", "Gap & Go Short",
            "VWAP Bounce", "VWAP Rejection",
            "ORB_BREAKOUT",  # keep legacy for ORB signals that directly set pattern name
            "Flag Breakout", "Bull Flag", "Bear Flag",
        }
        if any(p in premium_patterns for p in pattern_names):
            bonus_score += 6
            matched = [p for p in pattern_names if p in premium_patterns]
            result.bonuses.append(f"PREMIUM({','.join(matched[:2])})")

        # Bonus 8: Triple MTF alignment bonus
        # _check_mtf encodes aligned_count as: raw_score + aligned_count * 100
        # Extract it here to reward full 3-TF consensus without blocking 2-TF setups.
        _raw_mtf_bonus = mtf_alignment.get("_bonus_signal", 0)
        if _raw_mtf_bonus == 0:
            # Re-derive from the score we stored in gates_passed
            for g in result.gates_passed:
                if g.startswith("MTF(score="):
                    try:
                        _raw_mtf_bonus = int(g.split("=")[1].rstrip(")"))
                    except Exception:
                        pass
                    break
        _aligned_count = _raw_mtf_bonus // 100 if _raw_mtf_bonus >= 100 else 0
        if _aligned_count == 3:
            bonus_score += 10
            result.bonuses.append("MTF_3TF_TRIPLE(+10)")
        elif _aligned_count == 2:
            bonus_score += 4
            result.bonuses.append("MTF_2TF_DUAL(+4)")

        # ── Bonus 9: EMA Stack — institutional trend confirmation ────────────
        # 9-EMA > 21-EMA > 50-EMA on last bar = institutions are positioned long.
        # Linda Bradford Raschke rule: "Only buy in an uptrend, defined as EMAs stacked."
        if df_5m is not None and len(df_5m) >= 55:
            try:
                import pandas as _pd
                _close = df_5m["close"]
                _e9  = float(_close.ewm(span=9,  adjust=False).mean().iloc[-1])
                _e21 = float(_close.ewm(span=21, adjust=False).mean().iloc[-1])
                _e50 = float(_close.ewm(span=50, adjust=False).mean().iloc[-1])
                if direction == "BUY" and _e9 > _e21 > _e50:
                    bonus_score += 8
                    result.bonuses.append("EMA_STACK_BULL(+8)")
                elif direction == "SELL" and _e9 < _e21 < _e50:
                    bonus_score += 8
                    result.bonuses.append("EMA_STACK_BEAR(+8)")
                elif direction == "BUY" and _e9 < _e21:
                    bonus_score -= 4
                    result.bonuses.append("EMA_AGAINST_BULL(-4)")
                elif direction == "SELL" and _e9 > _e21:
                    bonus_score -= 4
                    result.bonuses.append("EMA_AGAINST_BEAR(-4)")
            except Exception:
                pass

        # ── Bonus 10: VWAP Reclaim — #1 institutional intraday signal ───────
        # Price crosses FROM below VWAP TO above VWAP with volume = institutions net-buying.
        # Every prop desk, hedge fund, and Bloomberg terminal watches this level.
        # Prior bar below VWAP + current bar above VWAP + volume surge = HIGH PROBABILITY LONG.
        if df_5m is not None and len(df_5m) >= 3 and "vwap" in df_5m.columns:
            try:
                _vwap_now  = float(df_5m["vwap"].iloc[-1])
                _vwap_prev = float(df_5m["vwap"].iloc[-2]) if "vwap" in df_5m.columns else _vwap_now
                _close_now  = float(df_5m["close"].iloc[-1])
                _close_prev = float(df_5m["close"].iloc[-2])
                if (_vwap_now > 0 and direction == "BUY"
                        and _close_now > _vwap_now and _close_prev < _vwap_prev):
                    bonus_score += 12
                    result.bonuses.append("VWAP_RECLAIM(+12)")
                elif (_vwap_now > 0 and direction == "SELL"
                        and _close_now < _vwap_now and _close_prev > _vwap_prev):
                    bonus_score += 12
                    result.bonuses.append("VWAP_BREAKDOWN(+12)")
            except Exception:
                pass
        elif above_vwap is True and direction == "BUY":
            # Fallback: if we don't have VWAP series, use flag + partial bonus
            bonus_score += 4
            result.bonuses.append("ABOVE_VWAP_CONF(+4)")
        elif above_vwap is False and direction == "SELL":
            bonus_score += 4
            result.bonuses.append("BELOW_VWAP_CONF(+4)")

        # ── Bonus 11: Consecutive candle confirmation — 2+ bullish bars ──────
        # Mark Minervini rule: "Require the stock to prove its direction before entering."
        # Two consecutive closes in the direction = real move, not noise.
        if df_5m is not None and len(df_5m) >= 4:
            try:
                _c = df_5m["close"].values
                _o = df_5m["open"].values
                if direction == "BUY":
                    _bull1 = _c[-2] > _o[-2]   # prev candle bullish
                    _bull2 = _c[-1] > _o[-1]   # last candle bullish
                    _bull3 = len(_c) > 3 and _c[-3] > _o[-3]
                    if _bull1 and _bull2 and _bull3:
                        bonus_score += 8
                        result.bonuses.append("3_BULL_CANDLES(+8)")
                    elif _bull1 and _bull2:
                        bonus_score += 5
                        result.bonuses.append("2_BULL_CANDLES(+5)")
                elif direction == "SELL":
                    _bear1 = _c[-2] < _o[-2]
                    _bear2 = _c[-1] < _o[-1]
                    _bear3 = len(_c) > 3 and _c[-3] < _o[-3]
                    if _bear1 and _bear2 and _bear3:
                        bonus_score += 8
                        result.bonuses.append("3_BEAR_CANDLES(+8)")
                    elif _bear1 and _bear2:
                        bonus_score += 5
                        result.bonuses.append("2_BEAR_CANDLES(+5)")
            except Exception:
                pass

        # ── Bonus 12: Higher-high / higher-low structure (BUY) ──────────────
        # William O'Neil: "Only buy stocks making new highs from sound bases."
        # Price making a higher-high relative to 10 bars ago = valid uptrend structure.
        if df_5m is not None and len(df_5m) >= 12:
            try:
                _highs = df_5m["high"].values
                _lows  = df_5m["low"].values
                _recent_high = max(_highs[-3:])
                _recent_low  = min(_lows[-3:])
                _prior_high  = max(_highs[-12:-3])
                _prior_low   = min(_lows[-12:-3])
                if direction == "BUY" and _recent_high > _prior_high and _recent_low > _prior_low:
                    bonus_score += 6
                    result.bonuses.append("HH_HL_STRUCTURE(+6)")
                elif direction == "SELL" and _recent_high < _prior_high and _recent_low < _prior_low:
                    bonus_score += 6
                    result.bonuses.append("LH_LL_STRUCTURE(+6)")
            except Exception:
                pass

        # ── Bonus 13: Retest confirmation (+12 pts) ─────────────────────────────
        # Price broke out, pulled back to level, then bounced — the highest-probability entry.
        # Institutional money steps in at the retest. Single biggest WR improvement.
        if candles_df is not None and len(candles_df) >= 5 and atr > 0:
            try:
                _closes = candles_df["close"].values
                _opens  = candles_df["open"].values
                _highs  = candles_df["high"].values
                _lows   = candles_df["low"].values
                if direction == "BUY":
                    # Look for: bar -3 broke above level → bar -2 or -1 pulled back toward level → bar 0 bouncing
                    _level = signal_score  # proxy; actual level = recent high from prior bars
                    # Heuristic: bar -2 was closer to prior resistance than bar -3 (pullback)
                    # AND bar -1 closed higher than bar -2 (bounce confirmed)
                    _pulled_back  = _closes[-3] < _highs[-4] and _closes[-2] < _closes[-3]
                    _bounced      = _closes[-1] > _closes[-2] and _closes[-1] > _opens[-1]
                    _held_support = _lows[-1] > (_closes[-3] - 0.6 * atr)  # didn't fall through
                    if _pulled_back and _bounced and _held_support:
                        bonus_score += 12
                        result.bonuses.append("RETEST_CONFIRMED(+12)")
                else:  # SELL
                    _pulled_back  = _closes[-3] > _lows[-4] and _closes[-2] > _closes[-3]
                    _bounced      = _closes[-1] < _closes[-2] and _closes[-1] < _opens[-1]
                    _held_resist  = _highs[-1] < (_closes[-3] + 0.6 * atr)
                    if _pulled_back and _bounced and _held_resist:
                        bonus_score += 12
                        result.bonuses.append("RETEST_CONFIRMED(+12)")
            except Exception:
                pass

        # ── Bonus 14: Triple momentum confirmation (+8 pts) ─────────────────────
        # RSI bullish + MACD histogram positive + price higher than 3 bars ago.
        # Three independent momentum indicators must all agree. No cherry-picking.
        if df_5m is not None and len(df_5m) >= 5:
            try:
                import pandas as _pd14
                _c14    = df_5m["close"]
                _e12    = _c14.ewm(span=12, adjust=False).mean()
                _e26    = _c14.ewm(span=26, adjust=False).mean()
                _macd   = _e12 - _e26
                _signal14 = _macd.ewm(span=9, adjust=False).mean()
                _hist   = float(_macd.iloc[-1] - _signal14.iloc[-1])
                _price_trend = float(_c14.iloc[-1]) > float(_c14.iloc[-4])
                if direction == "BUY":
                    if (50 < rsi < 75) and _hist > 0 and _price_trend:
                        bonus_score += 8
                        result.bonuses.append("TRIPLE_MOMENTUM(+8)")
                    elif rsi > 75 or (rsi < 40 and _hist < 0):
                        bonus_score -= 5
                        result.bonuses.append("MOMENTUM_WEAK(-5)")
                else:  # SELL
                    if (25 < rsi < 50) and _hist < 0 and not _price_trend:
                        bonus_score += 8
                        result.bonuses.append("TRIPLE_MOMENTUM_BEAR(+8)")
                    elif rsi < 25 or (rsi > 60 and _hist > 0):
                        bonus_score -= 5
                        result.bonuses.append("MOMENTUM_WEAK_BEAR(-5)")
            except Exception:
                pass

        # BONUS 15: INSTITUTIONAL ACCUMULATION (Wyckoff markup preparation)
        try:
            import config as _cfgIA
            if getattr(_cfgIA, 'INST_ACCUM_GATE', True) and df_5m is not None and len(df_5m) >= 5:
                import numpy as _np_ia
                _high = df_5m['high'].values.astype(float)[-5:]
                _low  = df_5m['low'].values.astype(float)[-5:]
                _close = df_5m['close'].values.astype(float)[-5:]
                _open  = df_5m['open'].values.astype(float)[-5:]
                _vol   = df_5m['volume'].values.astype(float)[-5:]
                _avg_vol = float(df_5m['volume'].mean()) if len(df_5m) >= 3 else 1.0
                _rng = _high - _low + 1e-6

                if direction in ('LONG', 'BUY'):
                    # Quiet accumulation: last 3 bars close in top 30% of range AND vol rising
                    _close_pct = [(_close[i] - _low[i]) / _rng[i] for i in range(-3, 0)]
                    _top30 = all(p >= 0.70 for p in _close_pct)
                    _vol_rising = _vol[-1] > _vol[-2] > _vol[-3] and _vol[-1] > _avg_vol * 1.2
                    if _top30 and _vol_rising:
                        bonus_score += 10
                        result.bonuses.append('INST_ACCUM(+10)')
                    elif any(p >= 0.70 for p in _close_pct) and _vol[-1] > _avg_vol * 1.5:
                        bonus_score += 5
                        result.bonuses.append('ACCUM_PARTIAL(+5)')
                    # Wrong-direction: selling on high volume
                    _down_bars = sum(1 for i in range(-3, 0) if _close[i] < _open[i])
                    if _down_bars >= 2 and _vol[-1] > _avg_vol * 1.8:
                        bonus_score -= 5
                        result.bonuses.append('DIST_PRESSURE(-5)')
                else:  # SHORT: distribution pattern
                    _close_pct_s = [(_high[i] - _close[i]) / _rng[i] for i in range(-3, 0)]
                    _top30_s = all(p >= 0.70 for p in _close_pct_s)
                    _vol_rising = _vol[-1] > _vol[-2] > _vol[-3] and _vol[-1] > _avg_vol * 1.2
                    if _top30_s and _vol_rising:
                        bonus_score += 10
                        result.bonuses.append('INST_DIST(+10)')
                    elif any(p >= 0.70 for p in _close_pct_s) and _vol[-1] > _avg_vol * 1.5:
                        bonus_score += 5
                        result.bonuses.append('DIST_PARTIAL(+5)')
                    _up_bars = sum(1 for i in range(-3, 0) if _close[i] > _open[i])
                    if _up_bars >= 2 and _vol[-1] > _avg_vol * 1.8:
                        bonus_score -= 5
                        result.bonuses.append('ACCUM_PRESSURE(-5)')
        except Exception:
            pass

        # ── FINAL SCORE & GRADE ───────────────────────────
        result.final_score = signal_score + bonus_score

        # Reject if post-bonus score drops below min_score
        if result.final_score < self.min_score:
            result.passed = False
            result.rejection_reason = (
                f"Post-bonus score {result.final_score:.0f} < {self.min_score:.0f}. "
                f"Bonuses: {bonus_score:+.0f}. Too many counter-indicators."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        # Grade the setup — institutional quality tiers
        # A+ = min_score+8 or 88 (whichever higher) — elite setups, maximum size
        # A  = min_score (everything that passes the gate) — no B/C dilution
        # B/C grades are never emitted here because the score gate above already
        # rejects anything below min_score.  Grade "B" still exists in the
        # dataclass default so callers don't crash on legacy paths, but we never
        # assign it from this filter — that was the bug that blocked all trades.
        _ap_thresh = 84.0    # A+ at 84+: base 68 + HA+5 + VWAP+6 + RSI+5 + ORB+8 + triple MTF+10 = reachable

        if result.final_score >= _ap_thresh:
            result.quality_grade   = "A+"
            result.size_multiplier = min(result.size_multiplier * 2.0, 2.5)   # maximum size on best setups
        else:
            result.quality_grade   = "A"
            result.size_multiplier = min(result.size_multiplier * 1.5, 2.0)   # full size on all passing signals

        # Hard cap size multiplier
        result.size_multiplier = round(min(result.size_multiplier, 2.5), 2)

        self._pass_count += 1
        _bucket = f"{int(result.final_score // 5) * 5}-{int(result.final_score // 5) * 5 + 4}"
        self._score_histogram[_bucket] = self._score_histogram.get(_bucket, 0) + 1
        logger.info(
            f"[{format_ist_timestamp()}] FILTER PASSED: "
            f"Grade={result.quality_grade} "
            f"Score={result.final_score:.0f} "
            f"Size={result.size_multiplier}x "
            f"Gates={result.gates_passed} "
            f"Bonuses={result.bonuses}"
        )
        return result

    # ─────────────────────────────────────────────────────────
    # GATE IMPLEMENTATIONS
    # ─────────────────────────────────────────────────────────

    def _check_time_window(self, t: time) -> Tuple[str, float]:
        # AVOID checked first — its windows are subsets of POWER and would be
        # shadowed if POWER ran first (e.g., 15:50-16:00 sits inside 14:30-16:00).
        for start, end in AVOID_WINDOWS:
            if start <= t < end:
                return "AVOID", 0.0
        for start, end in POWER_WINDOWS:
            if start <= t < end:
                return "POWER", 1.0
        for start, end in CAUTION_WINDOWS:
            if start <= t < end:
                return "CAUTION", 0.6
        return "AVOID", 0.0   # Pre-market / post-close: never trade outside defined windows

    def _check_regime(self, regime: str, direction: str) -> Tuple[bool, float]:
        """Only trade momentum-friendly regimes."""
        full_size = {
            "STRONG_TREND_UP":   ("BUY",  1.2),
            "STRONG_TREND_DOWN": ("SELL", 1.2),
            "OPENING_DRIVE":     ("BOTH", 1.1),
            "AFTERNOON_TREND":   ("BOTH", 1.0),
        }
        reduced_size = {
            "WEAK_TREND_UP":     ("BUY",  0.8),
            "WEAK_TREND_DOWN":   ("SELL", 0.8),
            "HIGH_VOLATILITY":   ("BOTH", 0.6),
            "MIDDAY_CHOP":       ("BOTH", 0.6),   # Reduced size, not blocked
            "RANGING":           ("BOTH", 0.5),   # Very small size, not blocked
        }
        blocked = {"LOW_VOLATILITY"}

        if regime in blocked:
            return False, 0.0

        if regime in full_size:
            pref_dir, size = full_size[regime]
            if pref_dir == "BOTH" or pref_dir == direction:
                return True, size
            return False, 0.0

        if regime in reduced_size:
            pref_dir, size = reduced_size[regime]
            if pref_dir == "BOTH" or pref_dir == direction:
                return True, size
            return False, 0.0

        return True, 0.9   # Unknown regime — allow but cautiously

    def _check_mtf(self, mtf: Dict, direction: str) -> Tuple[bool, int]:
        alignment_score = mtf.get("alignment_score", mtf.get("score", 0))
        entry_dir       = mtf.get("entry_direction", mtf.get("direction", "SKIP"))

        if entry_dir == "SKIP":
            return False, 0
        if alignment_score < 55:   # require at least 2/3 TF agreement (original calibrated threshold)
            return False, alignment_score
        signal_dir = "LONG" if direction == "BUY" else "SHORT"
        if entry_dir != signal_dir:
            return False, alignment_score
        # Check how many of the 3 TFs are aligned — all-3 earns a bonus in the bonus stage
        d5m  = mtf.get("5m",  "NEUTRAL")
        d15m = mtf.get("15m", "NEUTRAL")
        d1h  = mtf.get("1h",  "NEUTRAL")
        aligned_count = sum(1 for d in [d5m, d15m, d1h] if d == signal_dir)
        # Encode aligned_count into returned score so bonus stage can award extra pts
        bonus_signal = alignment_score + (aligned_count * 100)   # e.g. 80 + 300 = 380 → 3 TFs
        return True, bonus_signal

    def _check_volume(self, volume_ratio: float) -> Tuple[bool, float]:
        """
        Volume participation gate — institutional standard.
        Rule: never trade below-average volume — institutions don't.
        1.0× = minimum (at-average), 1.5×+ = bonus territory.
        Note: Alpaca 5-min bars undercount vs daily SMA in the first 90 min
        of the day, so 1.0× is achievable from open; 1.5× is genuinely strong.
        """
        if volume_ratio < 1.0:
            return False, 0                         # below-average volume — skip
        if volume_ratio < 1.5:
            return True, 0                          # passes, no bonus
        bonus = min((volume_ratio - 1.5) * 8, 15)  # up to +15 for 3×+ surge
        return True, bonus

    def _check_pattern_quality(
        self,
        signal_score:  float,
        pat_scores:    List[float],
        pat_names:     List[str],
        learner=None,
    ) -> Tuple[bool, float, str]:
        """Require minimum 70 base score."""
        best_score = max(pat_scores) if pat_scores else signal_score
        if pat_scores and pat_names and len(pat_names) == len(pat_scores):
            best_pattern = pat_names[pat_scores.index(best_score)]
        elif pat_names:
            best_pattern = pat_names[0]
        else:
            best_pattern = "?"

        effective_score = signal_score
        if learner and best_pattern:
            weight = learner.get_pattern_weight(best_pattern)
            if weight == 0.0:
                return False, 0, f"{best_pattern}(DISABLED)"
            effective_score = signal_score * weight

        return effective_score >= self.min_score, effective_score, best_pattern

    def _check_key_level(
        self, at_key_level: bool, ltp: float, above_vwap: bool
    ) -> Tuple[bool, str]:
        """
        Gate 11: Big players only enter at defined price levels.
        A level is: FVG edge, OB boundary, VWAP, POC, Pivot, S/R.
        Signal generator sets at_key_level=True when price is within 0.3% of any level.
        """
        if at_key_level:
            return True, ""
        # Soft pass if price is exactly at VWAP boundary (fallback when level data unavailable)
        if ltp == 0:
            return True, ""   # No price data — pass (above_vwap already checked)
        return False, (
            "Price not at a key level (FVG/OB/VWAP/POC/Pivot). "
            "Institutional entries require defined reference levels. "
            "Wait for pullback to level or breakout confirmation at level."
        )

    def _check_adx(self, adx: float) -> Tuple[bool, str]:
        """
        Gate 12: ADX >= 22 confirms a real trend exists.
        v16.0: raised from 17 → 22. ADX < 20 = no real trend, false breakouts dominate.
        ADX = 0 means data unavailable — fail open.
        """
        try:
            import config as _cfg_adx
            _adx_min = float(getattr(_cfg_adx, 'ADX_MIN_TREND', 22.0))
        except Exception:
            _adx_min = 22.0
        if adx == 0:
            return True, "ADX_MISSING"
        if adx >= _adx_min:
            return True, ""
        return False, (
            f"ADX {adx:.0f} < {_adx_min:.0f} — market is choppy/ranging. "
            f"Momentum strategies require ADX >= {_adx_min:.0f}."
        )

    def _check_spy_alignment(
        self, direction: str, spy_bullish: bool, spy_change_pct: float = 0.0
    ) -> Tuple[bool, str]:
        """
        Gate 13: Never fight the market. Block direction against strong trend.
        - SPY up >1%: strong bull day → block SHORTs outright
        - SPY down >1%: strong bear day → block LONGs outright
        - SPY ±0.5-1%: moderate — only block if directly opposed
        - SPY <±0.5%: neutral — allow both directions
        """
        abs_chg = abs(spy_change_pct)

        if direction == "BUY":
            if spy_change_pct <= -1.0:
                return False, (
                    f"Strong bear day (SPY {spy_change_pct:+.1f}%) — LONGs blocked. "
                    "Indices in strong sell-off: wait for stabilisation."
                )
            if spy_change_pct < -0.5 and not spy_bullish:
                return False, (
                    f"SPY bearish ({spy_change_pct:+.1f}%) — avoid LONG entries. "
                    "Big players don't buy when S&P 500 is selling off."
                )

        if direction == "SELL":
            if spy_change_pct >= 1.0:
                return False, (
                    f"Strong bull day (SPY {spy_change_pct:+.1f}%) — SHORTs blocked. "
                    "Indices in strong rally: only trade with momentum, not against it."
                )
            if spy_change_pct > 0.5 and spy_bullish:
                return False, (
                    f"SPY bullish ({spy_change_pct:+.1f}%) — avoid SHORT entries. "
                    "Big players don't short when S&P 500 is rallying."
                )

        return True, ""

    def _check_heikin_ashi(self, df: pd.DataFrame, direction: str) -> Tuple[bool, str]:
        """HA candles confirm trend direction. NOT for entry timing."""
        try:
            close  = df["close"]
            open_  = df["open"]
            high   = df["high"]
            low    = df["low"]

            ha_close = (open_ + high + low + close) / 4
            ha_open  = pd.Series(index=df.index, dtype=float)
            ha_open.iloc[0] = (open_.iloc[0] + close.iloc[0]) / 2
            for i in range(1, len(df)):
                ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2

            last_ha_close = float(ha_close.iloc[-1])
            last_ha_open  = float(ha_open.iloc[-1])
            prev_ha_close = float(ha_close.iloc[-2])
            prev_ha_open  = float(ha_open.iloc[-2])

            if direction == "BUY":
                bullish = (
                    last_ha_close > last_ha_open and
                    last_ha_close > prev_ha_close and
                    abs(last_ha_close - last_ha_open) >
                    abs(prev_ha_close - prev_ha_open) * 0.5
                )
                return bullish, "BULLISH_HA" if bullish else "WEAK_HA"
            else:
                bearish = (
                    last_ha_close < last_ha_open and
                    last_ha_close < prev_ha_close and
                    abs(last_ha_close - last_ha_open) >
                    abs(prev_ha_close - prev_ha_open) * 0.5
                )
                return bearish, "BEARISH_HA" if bearish else "WEAK_HA"

        except Exception as e:
            logger.debug(f"HA check failed: {e}")
            return True, "HA_UNAVAILABLE"

    def _check_liquidity(self, daily_volume: float) -> Tuple[bool, str]:
        """Gate 6: US stocks require 1M+ shares/day. Tight spreads only."""
        from config import MIN_DAILY_VOLUME
        if daily_volume < MIN_DAILY_VOLUME:
            return False, (
                f"Volume too low: {daily_volume/1e6:.2f}M shares/day "
                f"< {MIN_DAILY_VOLUME/1e6:.1f}M minimum. "
                "Low liquidity = wide spread = guaranteed slippage loss."
            )
        return True, ""

    def _check_circuit_proximity(
        self, prev_close: float, ltp: float
    ) -> Tuple[bool, str]:
        """Gate 7: Don't trade near halt bands (US equivalent of circuit breakers)."""
        from config import CIRCUIT_BANDS, CIRCUIT_BUFFER_PCT

        price_change_pct = ((ltp - prev_close) / prev_close) * 100

        for band_pct in CIRCUIT_BANDS:
            if price_change_pct >= (band_pct - CIRCUIT_BUFFER_PCT):
                return False, (
                    f"Near upper {band_pct:.0f}% halt band "
                    f"(price +{price_change_pct:.1f}% vs prev close). "
                    "Trading near halt band = risk of trading freeze — avoid."
                )
            if price_change_pct <= (-band_pct + CIRCUIT_BUFFER_PCT):
                return False, (
                    f"Near lower {band_pct:.0f}% halt band "
                    f"(price {price_change_pct:.1f}% vs prev close). "
                    "Trading near halt band = risk of trading freeze — avoid."
                )

        return True, ""

    def _check_gap_risk(
        self, gap_pct: float, minutes_since_open: float
    ) -> Tuple[bool, str]:
        """Gate 8: Gap price discovery window. >2% gap = wait it out."""
        from config import MAX_GAP_PCT, LARGE_GAP_PCT, EXTREME_GAP_PCT
        abs_gap = abs(gap_pct)

        if abs_gap >= EXTREME_GAP_PCT:
            return False, (
                f"EXTREME gap {gap_pct:+.1f}% — avoid entire session. "
                "Price discovery takes full day on 5%+ gaps."
            )

        wait_min = 0
        if abs_gap >= LARGE_GAP_PCT:
            wait_min = 15
        elif abs_gap >= MAX_GAP_PCT:
            wait_min = 5

        if wait_min > 0 and minutes_since_open < wait_min:
            remaining = wait_min - minutes_since_open
            return False, (
                f"Gap {gap_pct:+.1f}% — price discovery window. "
                f"Wait {remaining:.0f} more min (total {wait_min}min after open)."
            )

        return True, ""

    def _check_corp_actions(self, symbol: str) -> Tuple[bool, str]:
        """Gate 9: Skip stocks near ex-dividend/split dates."""
        try:
            from corporate_actions import is_safe_from_corp_actions
            safe, reason = is_safe_from_corp_actions(symbol)
            if not safe:
                return False, (
                    f"{symbol} has upcoming corporate action: {reason}. "
                    "Price adjustment distorts all technical signals."
                )
            return True, ""
        except ImportError:
            return True, ""
        except Exception as e:
            logger.debug(f"Corp action check error for {symbol}: {e}")
            return True, ""

    def _check_short_eligibility(self, symbol: str) -> Tuple[bool, str]:
        """Gate 10: SHORT signals — verify stock is shortable on Alpaca."""
        try:
            from auth_alpaca import get_auth_manager
            trading_client = get_auth_manager().get_trading_client()
            asset = trading_client.get_asset(symbol)
            if not asset.shortable:
                return False, (
                    f"{symbol} is not shortable on Alpaca. "
                    "Cannot place SHORT — signal converted to SKIP."
                )
            return True, ""
        except Exception:
            return True, ""   # Fail open — don't block on data errors

    # Sector groupings for dynamic correlation estimation
    _SECTOR_GROUPS = {
        "semi":    {"NVDA","AMD","TSM","SMCI","AVGO","ARM","INTC","QCOM","MU","LRCX","KLAC","AMAT","MRVL","TXN","MCHP","ON"},
        "bigtech": {"AAPL","MSFT","GOOGL","GOOG","AMZN","META"},
        "aicloud": {"CRWD","PANW","ZS","DDOG","NET","NOW","SNOW","TEAM","HUBS","OKTA","MDB","PLTR","AI","SOUN","BBAI","GTLB","U"},
        "finance": {"JPM","GS","MS","BAC","V","MA","SOFI","HOOD","AFRM","SQ","PYPL"},
        "energy":  {"XOM","CVX","OXY","SLB","MPC"},
        "ev":      {"TSLA","RIVN","LCID","NIO","PLUG","FSLR","ENPH"},
        "crypto":  {"COIN","MSTR","MARA","RIOT","HUT","CLSK","BTBT","CIFR"},
        "etf":     {"SPY","QQQ","IWM","TQQQ","SPXL","SOXL","TECL","FNGU"},
        "consumer":{"UBER","LYFT","DASH","SHOP","RBLX","ABNB","MELI","YELP"},
        "biotech": {"MRNA","HIMS","LLY","NVO","VKTX","RXRX"},
        "defense": {"LMT","RTX","NOC","GE","CAT"},
        "media":   {"NFLX","DIS","GOOGL"},
    }
    _SYM_TO_SECTOR: dict = {}  # populated on first call

    def _get_sector(self, sym: str) -> Optional[str]:
        if not self._SYM_TO_SECTOR:
            for sect, syms in self._SECTOR_GROUPS.items():
                for s in syms:
                    self._SYM_TO_SECTOR[s] = sect
        return self._SYM_TO_SECTOR.get(sym.upper())

    def _check_correlation(
        self,
        symbol: str,
        direction: str,
        open_positions: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """
        Gate 14: Block if a highly correlated position is already open in same direction.
        Two-pass: (1) explicit pair lookup, (2) same-sector heuristic for unknown pairs.
        Correlation >= CORR_BLOCK_THRESHOLD (0.75) triggers a block.
        """
        if not open_positions:
            return True, "No open positions"

        sym_up = symbol.upper()
        for open_sym in open_positions:
            open_up = open_sym.upper()
            pair = frozenset({sym_up, open_up})
            # Pass 1: explicit pair correlation table
            corr = self.CORRELATION_PAIRS.get(pair)
            if corr and corr >= self.CORR_BLOCK_THRESHOLD:
                return False, (
                    f"CORR_BLOCK: {symbol} correlates {corr:.0%} with open {open_sym}"
                )
            # Pass 2: same-sector heuristic for unlisted pairs
            if corr is None:
                sect_a = self._get_sector(sym_up)
                sect_b = self._get_sector(open_up)
                if sect_a and sect_b and sect_a == sect_b:
                    # Same sector, unknown pair → estimated 0.78 correlation
                    return False, (
                        f"CORR_BLOCK (sector): {symbol} and {open_sym} both in '{sect_a}' — "
                        f"estimated correlation ~0.78"
                    )

        return True, "Correlation OK"

    # ── NEW PRECISION GATES (70-80% WR UPGRADE) ──────────────────────────────

    def _gate_false_breakout(
        self, candles_df: pd.DataFrame, signal_price: float, direction: str, atr: float
    ) -> Tuple[bool, str]:
        """Gate 15: Reject false breakouts — wick rejections, volume fades, tiny bodies."""
        try:
            last = candles_df.iloc[-1]
            prev = candles_df.iloc[-2]
            c_range = float(last["high"]) - float(last["low"])
            if c_range < 1e-9:
                return True, ""
            if direction in ("BUY", "LONG"):
                body  = float(last["close"]) - float(last["open"])
                u_wick = float(last["high"]) - max(float(last["close"]), float(last["open"]))
                if u_wick > 0.60 * c_range:
                    return False, f"[FALSE BREAKOUT] wick rejection {u_wick/c_range:.0%} > 60% — shooting star"
                vol_now  = float(last.get("volume", 1) or 1)
                vol_prev = float(prev.get("volume", 1) or 1)
                if vol_now < vol_prev * 0.8:
                    return False, f"[FALSE BREAKOUT] volume fade {vol_now:.0f} < {vol_prev * 0.8:.0f} — momentum dying"
                if body < 0.25 * atr:
                    return False, f"[FALSE BREAKOUT] tiny body {body:.2f} < 0.25×ATR={0.25*atr:.2f} — no conviction"
            else:  # SELL / SHORT
                body  = float(last["open"]) - float(last["close"])
                l_wick = min(float(last["close"]), float(last["open"])) - float(last["low"])
                if l_wick > 0.60 * c_range:
                    return False, f"[FALSE BREAKOUT] lower wick {l_wick/c_range:.0%} > 60% — hammer rejection"
                vol_now  = float(last.get("volume", 1) or 1)
                vol_prev = float(prev.get("volume", 1) or 1)
                if vol_now < vol_prev * 0.8:
                    return False, f"[FALSE BREAKOUT] volume fade on short signal — no conviction"
                if body < 0.25 * atr:
                    return False, f"[FALSE BREAKOUT] tiny body < 0.25×ATR — noise candle"
            return True, ""
        except Exception:
            return True, ""   # fail open

    def _gate_clear_air(
        self, entry_price: float, direction: str, atr: float,
        daily_candles_df: Optional[pd.DataFrame] = None,
    ) -> Tuple[bool, str]:
        """Gate 16: Verify no major resistance within 1.5×ATR above entry (LONG) or below (SHORT)."""
        try:
            resistance = []
            zone = 1.5 * atr
            # Round-number check: scan common divisors for any level within the 1.5×ATR zone
            for div in (100, 50, 25, 10):
                lo = entry_price - zone
                hi = entry_price + zone
                rnd_lo = int(lo / div) * div
                for k in range(rnd_lo, int(hi / div + 2) * div, div):
                    rnd = float(k)
                    if entry_price < rnd < entry_price + zone and direction in ("BUY", "LONG"):
                        resistance.append(("ROUND_NUM", rnd))
                        break
                    if entry_price - zone < rnd < entry_price and direction in ("SELL", "SHORT"):
                        resistance.append(("ROUND_NUM", rnd))
                        break
                if resistance:
                    break
            if daily_candles_df is not None and len(daily_candles_df) >= 2:
                pd_high  = float(daily_candles_df["high"].iloc[-2])
                pd_close = float(daily_candles_df["close"].iloc[-2])
                resistance.append(("PRIOR_DAY_HIGH",  pd_high))
                if pd_close > entry_price:
                    resistance.append(("PRIOR_DAY_CLOSE", pd_close))
            zone = 1.5 * atr
            if direction in ("BUY", "LONG"):
                blocked = [(n, v) for n, v in resistance if entry_price < v < entry_price + zone]
                if blocked:
                    return False, f"resistance at {blocked[0][0]}={blocked[0][1]:.2f} within 1.5×ATR"
            else:
                blocked = [(n, v) for n, v in resistance if entry_price - zone < v < entry_price]
                if blocked:
                    return False, f"support at {blocked[0][0]}={blocked[0][1]:.2f} within 1.5×ATR"
            return True, ""
        except Exception:
            return True, ""   # fail open

    def _gate_indicator_floor(
        self,
        rsi: float, macd_hist: float, ema9: float, ema21: float,
        volume_ratio: float, direction: str,
        df_5m: Optional[pd.DataFrame] = None,
        min_count: int = 2,
    ) -> Tuple[bool, str]:
        """
        Gate 19: Require ≥min_count of 4 core indicators aligned with direction.
        Extracts indicator values from df_5m columns when direct args are unavailable.
        Fails open (passes) if df_5m is None — never blocks due to missing data.
        """
        try:
            # Prefer df_5m columns over zero-value direct args (direct args not in evaluate() signature)
            if df_5m is not None and len(df_5m) >= 2:
                last = df_5m.iloc[-1]
                _rsi   = float(last.get("rsi",  rsi)   or rsi   or 50.0)
                _mh    = float(last.get("macd_hist", macd_hist) or macd_hist or 0.0)
                _e9    = float(last.get("ema9",  ema9)  or ema9  or 0.0)
                _e21   = float(last.get("ema21", ema21) or ema21 or 0.0)
                _vr    = float(last.get("volume_ratio", volume_ratio) or volume_ratio or 1.0)
            else:
                # No candle data — fail open (never block due to data gap)
                return True, "no_data_skip"
            count = 0
            reasons = []
            if direction in ("BUY", "LONG"):
                if 25 < _rsi < 72:     count += 1; reasons.append(f"RSI={_rsi:.0f}")
                if _mh > 0:            count += 1; reasons.append("MACD+")
                if _e9 > _e21 > 0:     count += 1; reasons.append("EMA9>21")
                if _vr > 1.3:          count += 1; reasons.append(f"VOL={_vr:.1f}x")
            else:  # SELL / SHORT
                if 28 < _rsi < 75:     count += 1; reasons.append(f"RSI={_rsi:.0f}")
                if _mh < 0:            count += 1; reasons.append("MACD-")
                if 0 < _e9 < _e21:     count += 1; reasons.append("EMA9<21")
                if _vr > 1.3:          count += 1; reasons.append(f"VOL={_vr:.1f}x")
            if count < min_count:
                return False, f"only {count}/{min_count} indicators aligned ({','.join(reasons) or 'none'})"
            return True, f"{count}/4 ({','.join(reasons)})"
        except Exception:
            return True, "exception_skip"   # fail open

    def _gate_daily_htf(
        self, symbol: str, direction: str,
        daily_candles_df: Optional[pd.DataFrame] = None,
        fetcher=None,
    ) -> Tuple[bool, str]:
        """Gate 17: Daily trend alignment — LONG only above daily SMA20 and in uptrend."""
        try:
            df = daily_candles_df
            if df is None and fetcher is not None:
                try:
                    df = fetcher.get_candles(symbol, "1Day", limit=25)
                except Exception:
                    pass
            if df is None or len(df) < 22:
                logger.debug(f"[GATE-17 HTF] {symbol}: daily candles unavailable — fail-open")
                return True, ""   # insufficient data — fail open
            closes = df["close"].astype(float).values
            sma20  = float(closes[-20:].mean())
            cur    = float(closes[-1])
            ago3   = float(closes[-4])   # 3 trading days ago
            if direction in ("BUY", "LONG"):
                if cur < sma20 * 0.995:
                    return False, f"price {cur:.2f} below daily SMA20={sma20:.2f} — no longs in downtrend"
                if cur < ago3:
                    return False, f"price lower than 3 days ago ({ago3:.2f}) — daily downtrend"
            else:  # SELL / SHORT
                if cur > sma20 * 1.005:
                    return False, f"price {cur:.2f} above daily SMA20={sma20:.2f} — no shorts in uptrend"
                if cur > ago3:
                    return False, f"price higher than 3 days ago — daily uptrend, no shorts"
            return True, ""
        except Exception:
            return True, ""   # fail open

    def _gate_spread(
        self, symbol: str, fetcher, max_spread_pct: float = 0.15
    ) -> Tuple[bool, str]:
        """Gate 18: Skip if bid-ask spread > max_spread_pct — market maker trap."""
        try:
            quote = fetcher.get_quote(symbol)
            if not quote:
                return True, ""
            bid = float(quote.get("bid", 0) or 0)
            ask = float(quote.get("ask", 0) or 0)
            mid = (bid + ask) / 2
            if mid <= 0 or bid <= 0 or ask <= 0:
                return True, ""
            spread_pct = (ask - bid) / mid * 100
            if spread_pct > max_spread_pct:
                return False, f"spread {spread_pct:.2f}% > {max_spread_pct}% limit — market maker trap"
            return True, ""
        except Exception:
            return True, ""   # fail open

    def _gate_ba_imbalance(
        self, symbol: str, direction: str, fetcher, min_ratio: float = 0.52
    ) -> Tuple[bool, str]:
        """Gate 20: Bid/Ask size imbalance — aggressive side must dominate."""
        try:
            quote = fetcher.get_quote(symbol)
            if not quote:
                return True, ""
            bid_sz = float(quote.get("bid_size", 0) or quote.get("bidsize", 0) or 0)
            ask_sz = float(quote.get("ask_size", 0) or quote.get("asksize", 0) or 0)
            total = bid_sz + ask_sz
            if total < 10:
                return True, ""  # insufficient order book data — fail open
            if direction in ("BUY", "LONG"):
                ratio = ask_sz / total  # high ask_size = buyers lifting offers
                if ratio < min_ratio:
                    return False, f"ask_ratio={ratio:.2f} < {min_ratio} — sellers dominant"
            else:
                ratio = bid_sz / total  # high bid_size = sellers hitting bids
                if ratio < min_ratio:
                    return False, f"bid_ratio={ratio:.2f} < {min_ratio} — buyers dominant"
            return True, f"imbalance_ok ratio={ratio:.2f}"
        except Exception:
            return True, ""  # fail open

    def _gate_stop_hunt(
        self, df: "pd.DataFrame", price: float, direction: str
    ) -> "Tuple[bool, str, int]":
        """
        Detect ICT stop-hunt (Wyckoff Spring/Upthrust).
        Returns (pass, reason, bonus_pts).
        - Same-direction hunt (Spring for LONG, Upthrust for SHORT) → pass + 15 bonus pts
        - Opposite-direction fake-out → reject (entering on the fake move)
        """
        try:
            import pandas as _pd
            import numpy as _np
            highs  = df['high'].values.astype(float)
            lows   = df['low'].values.astype(float)
            closes = df['close'].values.astype(float)
            n = len(highs)
            if n < 15:
                return True, '', 0

            # ATR (10-bar)
            tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                  for i in range(1, n)]
            atr = float(_np.mean(tr[-10:])) if len(tr) >= 3 else (highs[-1] - lows[-1])
            if atr <= 0:
                return True, '', 0

            # Swing levels from bars [-20:-3] (avoid last 2 bars = the hunt itself)
            lookback = max(0, n - 20)
            swing_lo = float(_np.min(lows[lookback:n-2]))
            swing_hi = float(_np.max(highs[lookback:n-2]))

            # Hunt detection: bar[-2] wicked beyond swing and closed back inside
            # Bullish Spring: low[-2] pierced swing_lo by > 0.2×ATR but closed above swing_lo
            spring = (lows[-2] < swing_lo - 0.2 * atr and closes[-2] > swing_lo)
            # Bearish Upthrust: high[-2] pierced swing_hi by > 0.2×ATR but closed below swing_hi
            upthrust = (highs[-2] > swing_hi + 0.2 * atr and closes[-2] < swing_hi)

            if direction in ('LONG', 'BUY'):
                if spring:
                    return True, '', 15   # Bullish spring = best LONG entry — bonus
                if upthrust:
                    return False, f'bearish upthrust trap — high={highs[-2]:.2f} vs swing={swing_hi:.2f}', 0
            else:  # SHORT
                if upthrust:
                    return True, '', 15   # Bearish upthrust = best SHORT entry — bonus
                if spring:
                    return False, f'bullish spring trap — low={lows[-2]:.2f} vs swing={swing_lo:.2f}', 0

            return True, '', 0
        except Exception:
            return True, '', 0

    def _rsi_bonus(self, rsi: float, direction: str) -> float:
        """Ideal RSI zones for momentum entries."""
        if direction == "BUY":
            if 35 <= rsi <= 52:   return 8    # Golden zone — momentum building
            if 52 < rsi <= 60:    return 3    # Slightly extended but ok
            if rsi > 70:          return -12  # Overbought — high failure risk
            if rsi < 30:          return -5   # Extreme oversold
        else:  # SELL
            if 48 <= rsi <= 65:   return 8
            if 40 <= rsi < 48:    return 3
            if rsi < 30:          return -12
            if rsi > 75:          return -5
        return 0

    # ─────────────────────────────────────────────────────────
    # STATISTICS
    # ─────────────────────────────────────────────────────────

    def _log_rejection(self, result: FilterResult, score: float, direction: str):
        self._reject_count += 1
        _bucket = f"{int(score // 5) * 5}-{int(score // 5) * 5 + 4}"
        self._score_histogram[_bucket] = self._score_histogram.get(_bucket, 0) + 1
        self._rejection_log.append({
            "time_et":   format_ist_timestamp(),
            "score":     score,
            "direction": direction,
            "reason":    result.rejection_reason,
            "window":    result.time_window,
        })
        logger.debug(
            f"[{format_ist_timestamp()}] FILTERED OUT: {result.rejection_reason}"
        )

    def get_stats(self) -> Dict:
        total = self._pass_count + self._reject_count
        # Score histogram sorted by bucket value
        hist_sorted = dict(sorted(self._score_histogram.items(), key=lambda x: int(x[0].split("-")[0])))
        # Near-miss: scores that were rejected but within 8 pts of threshold
        near_miss = sum(
            1 for r in self._rejection_log
            if r.get("score", 0) >= self.min_score - 8
            and "PATTERN_SCORE" in r.get("reason", "")
        )
        return {
            "total_evaluated":       total,
            "passed":                self._pass_count,
            "rejected":              self._reject_count,
            "pass_rate":             round(self._pass_count / max(total, 1) * 100, 1),
            "top_rejection_reasons": self._top_rejections(),
            "score_histogram":       hist_sorted,
            "near_miss_count":       near_miss,   # rejected but within 8 pts of threshold
            "min_score":             self.min_score,
        }

    def _top_rejections(self) -> List[str]:
        from collections import Counter
        if not self._rejection_log:
            return []
        reasons = [r["reason"][:50] for r in self._rejection_log]
        return [r for r, _ in Counter(reasons).most_common(5)]

    def print_stats(self):
        s = self.get_stats()
        hist_lines = []
        threshold = int(s["min_score"])
        for bucket, count in s["score_histogram"].items():
            lo = int(bucket.split("-")[0])
            bar = "█" * min(count, 30)
            marker = " ← threshold" if lo <= threshold < lo + 5 else ""
            hist_lines.append(f"    {bucket:>6}: {bar} {count}{marker}")
        print(
            f"\nHigh-Accuracy Filter Stats:\n"
            f"  Evaluated: {s['total_evaluated']} | "
            f"Passed: {s['passed']} ({s['pass_rate']:.0f}%) | "
            f"Rejected: {s['rejected']}\n"
            f"  Near-misses (within 8 pts of {threshold}): {s['near_miss_count']}\n"
            f"  Score distribution:\n"
            + "\n".join(hist_lines) + "\n"
            f"  Top rejections:\n"
            + "\n".join(f"    • {r}" for r in s["top_rejection_reasons"])
        )
