"""
high_accuracy_filter.py — NSE Momentum Groww AI Bot
High-Accuracy Signal Gate — Targets 70-80% Win Rate

18yr Truth: "Most traders fail because they take EVERY signal.
The 70%+ win rate comes from taking ONLY THE BEST 20% of signals.
Patience is your edge. Waiting IS the strategy."

This filter sits BETWEEN signal_generator and execution.
A signal must PASS ALL 5 gates to become a trade.

THE 5 CONFLUENCE GATES:
  Gate 1: POWER HOURS ONLY    — Trade only in high-probability time windows
  Gate 2: REGIME ALIGNMENT    — Market regime must be MOMENTUM (not RANGING)
  Gate 3: MULTI-TF ALIGNMENT  — At least 2 of 3 timeframes must agree
  Gate 4: VOLUME SURGE        — Current volume must be ≥ 1.8x 20-period SMA
  Gate 5: PATTERN QUALITY     — Pattern confidence score ≥ 72/100

BONUS GATES (increase score further):
  + Heikin Ashi confirmation  (trend candle in signal direction)
  + VWAP position alignment   (BUY above VWAP, SELL below VWAP)
  + RSI in momentum zone      (35-55 for BUY, 45-65 for SELL)
  + Relative strength vs Nifty (stock stronger than index)
  + Opening range aligned     (ORB direction matches signal)
  + No news blackout          (30 min buffer around events)
  + Nifty same direction      (index confirms stock direction)

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
IST = ZoneInfo("Asia/Kolkata")


# ── POWER HOUR WINDOWS (IST) ───────────────────────────────
# Only trade during highest-probability intraday windows.
# Based on 18yr observation: 70%+ of profitable moves start here.
POWER_WINDOWS = [
    (time(9, 15), time(10, 30)),   # Opening drive — strongest momentum
    (time(14, 0), time(15, 0)),    # Afternoon institutional — second best
]

# Reduced-size window (can trade but 60% size)
CAUTION_WINDOWS = [
    (time(10, 30), time(11, 0)),   # Post-opening fade
    (time(13, 0),  time(14, 0)),   # Early afternoon pickup
]

# NO TRADE windows
AVOID_WINDOWS = [
    (time(11, 0),  time(13, 0)),   # Midday chop — 18yr rule: ALWAYS avoid
    (time(15, 0),  time(15, 30)),  # EOD — no new entries
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
        signal_score:      float,
        direction:         str,             # "BUY" or "SELL"
        regime:            str,
        mtf_alignment:     Dict,            # from MultiTimeframeAnalyzer
        volume_ratio:      float,
        pattern_names:     List[str],
        pattern_scores:    List[float],
        df_5m:             Optional[pd.DataFrame],
        rsi:               float,
        above_vwap:        bool,
        nifty_change_pct:  float,
        stock_change_pct:  float,
        news_clear:        bool,
        orb_direction:     str = "",        # "UP", "DOWN", or ""
        learner=None,
    ) -> FilterResult:

        result = FilterResult()
        now_ist = get_current_ist_time()

        # ── GATE 1: POWER HOURS ───────────────────────────
        window, size_mult = self._check_time_window(now_ist.time())
        result.time_window = window

        if window == "AVOID":
            result.rejection_reason = (
                f"AVOID window ({now_ist.strftime('%H:%M')} IST). "
                "No trades during midday chop (11:00–13:00) or EOD."
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
                f"Volume ratio {volume_ratio:.1f}x < 1.8x required. "
                "No volume = no conviction = no trade."
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
                f"Signal score {signal_score:.0f} < 72 required. "
                f"Best pattern: {best_pattern}. Wait for stronger setup."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        result.gates_passed.append(f"PATTERN_SCORE({signal_score:.0f})")

        # ── NEWS BLACKOUT ────────────────────────────────
        if not news_clear:
            result.rejection_reason = "News blackout active — RBI/FOMC/event window"
            self._log_rejection(result, signal_score, direction)
            return result

        # ─────────────────────────────────────────────────
        # ALL 5 GATES PASSED — now calculate bonus score
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
            result.size_multiplier *= 0.8   # Below VWAP buy = weaker
            result.bonuses.append("BELOW_VWAP(weak_buy)")

        # Bonus 3: RSI momentum zone
        rsi_bonus = self._rsi_bonus(rsi, direction)
        bonus_score += rsi_bonus
        if rsi_bonus > 0:
            result.bonuses.append(f"RSI_OPTIMAL({rsi:.0f})")
        elif rsi_bonus < 0:
            result.bonuses.append(f"RSI_EXTREME({rsi:.0f})")

        # Bonus 4: Relative strength vs Nifty
        rs = stock_change_pct - nifty_change_pct
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

        # Bonus 5: Nifty alignment
        if direction == "BUY" and nifty_change_pct > 0.2:
            bonus_score += 5
            result.bonuses.append("NIFTY_ALIGNED")
        elif direction == "SELL" and nifty_change_pct < -0.2:
            bonus_score += 5
            result.bonuses.append("NIFTY_ALIGNED")
        elif (direction == "BUY" and nifty_change_pct < -0.5) or \
             (direction == "SELL" and nifty_change_pct > 0.5):
            bonus_score -= 10
            result.bonuses.append("NIFTY_AGAINST")

        # Bonus 6: ORB direction match
        if orb_direction and orb_direction == ("UP" if direction=="BUY" else "DOWN"):
            bonus_score += 8
            result.bonuses.append("ORB_ALIGNED")
        elif orb_direction and orb_direction != ("UP" if direction=="BUY" else "DOWN"):
            bonus_score -= 6
            result.bonuses.append("ORB_CONFLICT")

        # Bonus 7: Premium patterns
        premium_patterns = {
            "ORB_BREAKOUT", "VWAP_RECLAIM", "BULLISH_ENGULFING",
            "BEARISH_ENGULFING", "VOLUME_SURGE_BREAKOUT", "FLAG_BREAKOUT",
            "BOS_BULLISH", "BOS_BEARISH"
        }
        if any(p in premium_patterns for p in pattern_names):
            bonus_score += 6
            matched = [p for p in pattern_names if p in premium_patterns]
            result.bonuses.append(f"PREMIUM({','.join(matched[:2])})")

        # ── FINAL SCORE & GRADE ───────────────────────────
        result.final_score = signal_score + bonus_score

        # Reduce size if bonus brought score below threshold
        if result.final_score < 72:
            result.passed = False
            result.rejection_reason = (
                f"Post-bonus score {result.final_score:.0f} < 72. "
                f"Bonuses: {bonus_score:+.0f}. Too many counter-indicators."
            )
            self._log_rejection(result, signal_score, direction)
            return result

        # Grade the setup
        if result.final_score >= 90:
            result.quality_grade   = "A+"
            result.size_multiplier = min(result.size_multiplier * 1.2, 1.5)
        elif result.final_score >= 82:
            result.quality_grade   = "A"
            result.size_multiplier = min(result.size_multiplier * 1.1, 1.3)
        elif result.final_score >= 75:
            result.quality_grade   = "B"
        else:
            result.quality_grade   = "C"
            result.size_multiplier *= 0.75   # Low-conviction C grade = smaller size

        # Hard cap size multiplier
        result.size_multiplier = round(min(result.size_multiplier, 1.5), 2)

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
        return "POWER", 1.0   # Outside all windows (e.g., at exact boundaries)

    def _check_regime(self, regime: str, direction: str) -> Tuple[bool, float]:
        """
        Only trade momentum-friendly regimes.
        18yr rule: A ranging market kills momentum strategies.
        """
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
            return False, 0.0  # Wrong direction for regime

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
        # Check direction match
        signal_dir = "LONG" if direction == "BUY" else "SHORT"
        if entry_dir != signal_dir:
            return False, alignment_score
        return True, alignment_score

    def _check_volume(self, volume_ratio: float) -> Tuple[bool, float]:
        """Require 1.8x volume. No volume = no institutional participation."""
        if volume_ratio < 1.8:
            return False, 0
        bonus = min((volume_ratio - 1.8) * 5, 10)   # Up to +10 for very high volume
        return True, bonus

    def _check_pattern_quality(
        self,
        signal_score:  float,
        pat_scores:    List[float],
        pat_names:     List[str],
        learner=None,
    ) -> Tuple[bool, float, str]:
        """Require minimum 72 base score. Apply learner weights."""
        best_score   = max(pat_scores) if pat_scores else signal_score
        best_pattern = pat_names[pat_scores.index(best_score)] if pat_scores and pat_names else "?"

        # Apply learner weight to best pattern
        effective_score = signal_score
        if learner and best_pattern:
            weight = learner.get_pattern_weight(best_pattern)
            if weight == 0.0:
                return False, 0, f"{best_pattern}(DISABLED)"
            effective_score = signal_score * weight

        return effective_score >= 72, effective_score, best_pattern

    def _check_heikin_ashi(self, df: pd.DataFrame, direction: str) -> Tuple[bool, str]:
        """
        Heikin Ashi candles smooth noise and confirm trend direction.
        18yr rule: HA is NOT for entry timing — it's for TREND CONFIRMATION.
        """
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

            # Bullish HA: close > open, increasing
            if direction == "BUY":
                bullish = (
                    last_ha_close > last_ha_open and        # Green HA candle
                    last_ha_close > prev_ha_close and       # Rising
                    abs(last_ha_close - last_ha_open) >     # Solid body
                    abs(prev_ha_close - prev_ha_open) * 0.5
                )
                return bullish, "BULLISH_HA" if bullish else "WEAK_HA"

            else:  # SELL
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

    def _rsi_bonus(self, rsi: float, direction: str) -> float:
        """
        Ideal RSI zones for momentum entries.
        18yr rule: The best BUY entries are RSI 38-52 (momentum building, not overbought).
        Extreme RSI = fading momentum = dangerous entry.
        """
        if direction == "BUY":
            if 35 <= rsi <= 52:   return 8    # Golden zone — momentum building
            if 52 < rsi <= 60:    return 3    # Slightly extended but ok
            if rsi > 70:          return -12  # Overbought — high failure risk
            if rsi < 30:          return -5   # Extreme — trend may continue down
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
            "time_ist":  format_ist_timestamp(),
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
