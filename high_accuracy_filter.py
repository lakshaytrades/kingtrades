"""
high_accuracy_filter.py — US Momentum Alpaca AI Bot
High-Accuracy Signal Gate — Targets 9/10 Win Rate

18yr Truth: "Most traders fail because they take EVERY signal.
The 9/10 win rate comes from taking ONLY THE BEST 10% of signals.
Patience is your edge. Waiting IS the strategy."

This filter sits BETWEEN signal_generator and execution.
A signal must PASS ALL 13 gates to become a trade.

THE 13 CONFLUENCE GATES:
  Gate 1:  POWER HOURS ONLY      — Trade only in high-probability ET time windows
  Gate 2:  REGIME ALIGNMENT      — Market regime must be MOMENTUM (not RANGING)
  Gate 3:  MULTI-TF ALIGNMENT    — At least 2 of 3 timeframes must agree
  Gate 4:  VOLUME SURGE          — Current volume must be ≥ 2.0x 20-period SMA
  Gate 5:  PATTERN QUALITY       — Signal score ≥ 80/100 (only A-grade setups)
  Gate 6:  LIQUIDITY             — Min daily volume ≥ 1M shares (US liquid stocks)
  Gate 7:  CIRCUIT BREAKER       — Stock not near 5/10/20% halt bands
  Gate 8:  GAP RISK              — No extreme gap open; wait window respected
  Gate 9:  CORP ACTIONS          — No ex-dividend/bonus/split within 2 days
  Gate 10: SHORT ELIGIBILITY     — SELL signals only on shortable stocks
  Gate 11: ENTRY AT LEVEL        — Price within 0.3% of key level (FVG/OB/VWAP/POC)
  Gate 12: ADX TRENDING          — ADX > 20 (no choppy directionless market)
  Gate 13: SPY ALIGNMENT         — SPY green for LONGs, SPY red for SHORTs

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
    (time(14, 30), time(16, 0)),    # Power Hour — institutional accumulation/distribution
]

# Reduced-size window (can trade but 60% size)
CAUTION_WINDOWS = [
    (time(10, 45), time(11, 30)),   # Post-opening fade
    (time(13, 30), time(14, 30)),   # Early afternoon pickup
]

# NO TRADE windows
AVOID_WINDOWS = [
    (time(11, 30), time(13, 30)),   # Midday chop — 18yr rule: ALWAYS avoid
    (time(15, 50), time(16, 0)),    # EOD — no new entries
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
    13 gates. 9/10 win rate target.
    """

    def __init__(self):
        self._rejection_log: List[Dict] = []
        self._pass_count    = 0
        self._reject_count  = 0

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
    ) -> FilterResult:

        result = FilterResult()
        now_et = get_current_ist_time()   # Returns ET after IST=ET alias in utils.py

        # ── GATE 1: POWER HOURS ───────────────────────────
        window, size_mult = self._check_time_window(now_et.time())
        result.time_window = window

        if window == "AVOID":
            result.rejection_reason = (
                f"AVOID window ({now_et.strftime('%H:%M')} ET). "
                "No trades during midday chop (11:30–13:30) or EOD."
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
                f"Volume ratio {volume_ratio:.1f}x < 2.0x required. "
                "Institutional trades require 2x+ volume confirmation."
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
                f"Signal score {signal_score:.0f} < 80 required. "
                f"Best pattern: {best_pattern}. Only A-grade setups qualify."
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

        # ── GATE 11: ENTRY AT KEY LEVEL ──────────────────
        # Big players only enter at defined levels — FVG, OB, VWAP, POC, S/R
        level_ok, level_reason = self._check_key_level(at_key_level, ltp, above_vwap)
        if not level_ok:
            result.gates_failed.append("NOT_AT_LEVEL")
            result.rejection_reason = level_reason
            self._log_rejection(result, signal_score, direction)
            return result
        result.gates_passed.append("AT_LEVEL")

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
            spy_ok, spy_reason = self._check_spy_alignment(direction, spy_bullish)
            if not spy_ok:
                result.gates_failed.append("SPY_CONFLICT")
                result.rejection_reason = spy_reason
                self._log_rejection(result, signal_score, direction)
                return result
            result.gates_passed.append("SPY_ALIGNED")

        # ─────────────────────────────────────────────────
        # ALL GATES PASSED — now calculate bonus score
        # ─────────────────────────────────────────────────
        result.passed   = True
        bonus_score     = 0.0

        # Bonus 1: Heikin Ashi confirmation
        if df_5m is not None and len(df_5m) >= 3:
            ha_ok, ha_note = self._check_heikin_ashi(df_5m, direction)
            if ha_ok:
                bonus_score += 5
                result.bonuses.append(f"HA({ha_note})")
            else:
                bonus_score -= 3
                result.bonuses.append(f"HA_WEAK({ha_note})")

        # Bonus 2: VWAP position
        if direction == "BUY" and above_vwap:
            bonus_score += 6
            result.bonuses.append("ABOVE_VWAP")
        elif direction == "SELL" and not above_vwap:
            bonus_score += 6
            result.bonuses.append("BELOW_VWAP")
        elif direction == "BUY" and not above_vwap:
            bonus_score -= 5
            result.size_multiplier *= 0.8
            result.bonuses.append("BELOW_VWAP(weak_buy)")

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

        # Bonus 7: Premium ICT/institutional patterns
        premium_patterns = {
            "ORB_BREAKOUT", "VWAP_RECLAIM", "BULLISH_ENGULFING",
            "BEARISH_ENGULFING", "VOLUME_SURGE_BREAKOUT", "FLAG_BREAKOUT",
            "BOS_BULLISH", "BOS_BEARISH", "FVG_FILL", "OB_BOUNCE",
            "JUDAS_SWING", "INDUCEMENT_TRAP",
        }
        if any(p in premium_patterns for p in pattern_names):
            bonus_score += 6
            matched = [p for p in pattern_names if p in premium_patterns]
            result.bonuses.append(f"PREMIUM({','.join(matched[:2])})")

        # ── FINAL SCORE & GRADE ───────────────────────────
        result.final_score = signal_score + bonus_score

        # Reject if post-bonus score drops below 80
        if result.final_score < 80:
            result.passed = False
            result.rejection_reason = (
                f"Post-bonus score {result.final_score:.0f} < 80. "
                f"Bonuses: {bonus_score:+.0f}. Too many counter-indicators."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        # Grade the setup — institutional quality tiers
        if result.final_score >= 92:
            result.quality_grade   = "A+"
            result.size_multiplier = min(result.size_multiplier * 1.3, 2.0)
        elif result.final_score >= 85:
            result.quality_grade   = "A"
            result.size_multiplier = min(result.size_multiplier * 1.15, 1.5)
        elif result.final_score >= 80:
            result.quality_grade   = "B"
        else:
            result.quality_grade   = "C"
            result.size_multiplier *= 0.75

        # Hard cap size multiplier
        result.size_multiplier = round(min(result.size_multiplier, 2.0), 2)

        self._pass_count += 1
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
        for start, end in POWER_WINDOWS:
            if start <= t < end:
                return "POWER", 1.0
        for start, end in CAUTION_WINDOWS:
            if start <= t < end:
                return "CAUTION", 0.6
        for start, end in AVOID_WINDOWS:
            if start <= t < end:
                return "AVOID", 0.0
        return "POWER", 1.0   # Outside all windows (at exact boundaries)

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
        }
        blocked = {"RANGING", "LOW_VOLATILITY", "MIDDAY_CHOP"}

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
        if alignment_score < 60:
            return False, alignment_score
        signal_dir = "LONG" if direction == "BUY" else "SHORT"
        if entry_dir != signal_dir:
            return False, alignment_score
        return True, alignment_score

    def _check_volume(self, volume_ratio: float) -> Tuple[bool, float]:
        """Require 2.0x volume — institutional participation threshold."""
        if volume_ratio < 2.0:
            return False, 0
        bonus = min((volume_ratio - 2.0) * 5, 10)
        return True, bonus

    def _check_pattern_quality(
        self,
        signal_score:  float,
        pat_scores:    List[float],
        pat_names:     List[str],
        learner=None,
    ) -> Tuple[bool, float, str]:
        """Require minimum 80 base score — only A-grade setups."""
        best_score   = max(pat_scores) if pat_scores else signal_score
        best_pattern = pat_names[pat_scores.index(best_score)] if pat_scores and pat_names else "?"

        effective_score = signal_score
        if learner and best_pattern:
            weight = learner.get_pattern_weight(best_pattern)
            if weight == 0.0:
                return False, 0, f"{best_pattern}(DISABLED)"
            effective_score = signal_score * weight

        return effective_score >= 80, effective_score, best_pattern

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
            return True, ""   # No price data — fail open
        return False, (
            "Price not at a key level (FVG/OB/VWAP/POC/Pivot). "
            "Institutional entries require defined reference levels. "
            "Wait for pullback to level or breakout confirmation at level."
        )

    def _check_adx(self, adx: float) -> Tuple[bool, str]:
        """
        Gate 12: ADX > 20 confirms directional trend exists.
        ADX ≤ 20 = choppy/ranging market — momentum strategies fail.
        """
        if adx >= 20:
            return True, ""
        return False, (
            f"ADX {adx:.0f} ≤ 20 — market is choppy/ranging. "
            "Momentum strategies require ADX > 20. "
            "Wait for directional trend to develop."
        )

    def _check_spy_alignment(
        self, direction: str, spy_bullish: bool
    ) -> Tuple[bool, str]:
        """
        Gate 13: Never fight the market. SPY must confirm signal direction.
        LONG signals require SPY to be net positive today.
        SHORT signals require SPY to be net negative today.
        """
        if direction == "BUY" and not spy_bullish:
            return False, (
                "SPY is bearish — avoid LONG entries against market direction. "
                "Big players don't buy when the S&P 500 is selling off."
            )
        if direction == "SELL" and spy_bullish:
            return False, (
                "SPY is bullish — avoid SHORT entries against market direction. "
                "Big players don't short when the S&P 500 is rallying."
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
            from execution_alpaca import AlpacaExecutor
            exec_ = AlpacaExecutor()
            if hasattr(exec_, 'is_shortable') and not exec_.is_shortable(symbol):
                return False, (
                    f"{symbol} is not shortable on Alpaca. "
                    "Cannot place SHORT — signal converted to SKIP."
                )
            return True, ""
        except Exception:
            return True, ""   # Fail open — don't block on data errors

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
        return {
            "total_evaluated": total,
            "passed":    self._pass_count,
            "rejected":  self._reject_count,
            "pass_rate": round(self._pass_count / max(total, 1) * 100, 1),
            "top_rejection_reasons": self._top_rejections(),
        }

    def _top_rejections(self) -> List[str]:
        from collections import Counter
        if not self._rejection_log:
            return []
        reasons = [r["reason"][:50] for r in self._rejection_log]
        return [r for r, _ in Counter(reasons).most_common(5)]

    def print_stats(self):
        s = self.get_stats()
        print(
            f"\nHigh-Accuracy Filter Stats:\n"
            f"  Evaluated: {s['total_evaluated']} | "
            f"Passed: {s['passed']} ({s['pass_rate']:.0f}%) | "
            f"Rejected: {s['rejected']}\n"
            f"  Top rejections:\n"
            + "\n".join(f"    • {r}" for r in s["top_rejection_reasons"])
        )
