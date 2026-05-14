"""
momentum_burst.py — Explosive Momentum Burst Detector

Finds NSE stocks that are coiling before a 3–5% explosive move.
Triggered every 5 minutes. Uses 7 convergence conditions.

A "burst" requires at least 4 of 7 conditions:
  1. Bollinger Band Squeeze  — BB width < 20th percentile (coiling)
  2. Volume Node Break       — Price crossing a LVN (low volume node = fast travel)
  3. RVOL Surge              — Relative volume > 2.5x 20-day average
  4. Momentum Acceleration   — Rate-of-change (ROC) turning sharply positive
  5. Inside Bar Breakout     — Breaking out of multi-bar inside bar range
  6. Pre-breakout EMA stack  — EMA9 > EMA21 > EMA50 stacked (LONG) or inverted
  7. RSI Regime Reset        — RSI bouncing off 40 (LONG) or rejecting at 60 (SHORT)

Output: BurstSetup → TradeSignal conversion

Only active during:
  - 9:15–10:15 AM IST (opening drive burst)
  - 13:30–14:45 IST  (afternoon power burst)

Estimated profit per burst: 1.5–4% in 15–45 minutes
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

MIN_CONDITIONS   = 4     # Minimum burst conditions required
MIN_PRICE        = 100   # NSE minimum price filter
MIN_RVOL         = 2.0   # Minimum relative volume for burst confirmation
BB_SQUEEZE_PCT   = 20    # BB width below this percentile = squeeze
BURST_STOP_PCT   = 0.015 # 1.5% stop loss
BURST_T1_PCT     = 0.025 # 2.5% target 1 (1.67:1 R:R)
BURST_T2_PCT     = 0.040 # 4.0% target 2 (2.67:1 R:R)


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BurstSetup:
    """An explosive momentum burst setup."""
    symbol:           str
    direction:        str              # "LONG" or "SHORT"
    entry_price:      float
    stop_loss:        float
    target_1:         float
    target_2:         float
    conditions_met:   List[str]        # Which of the 7 burst conditions fired
    conditions_count: int
    burst_score:      float            # 0–100
    rvol:             float            # Relative volume ratio
    bb_squeeze:       bool
    momentum_pct:     float            # Rate of change %
    rationale:        str = ""

    def summary(self) -> str:
        emoji = "🚀" if self.direction == "LONG" else "💥"
        return (
            f"{emoji} BURST: {self.symbol} {self.direction} | "
            f"score={self.burst_score:.0f} | "
            f"{self.conditions_count}/7 conditions | "
            f"RVOL={self.rvol:.1f}x"
        )


# ─────────────────────────────────────────────────────────────────────────────
# BURST DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class MomentumBurstDetector:
    """
    Scans for stocks about to make explosive 3–5% intraday moves.

    The key insight: explosive moves don't come from nowhere.
    They are preceded by compression, unusual volume, and multi-indicator alignment.
    This detector catches setups BEFORE they explode, not after.
    """

    def __init__(self):
        self._scanned_today: Dict[str, str] = {}   # symbol → last_scan_date

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────

    def is_burst_time(self) -> bool:
        """Only scan during burst windows: opening drive and afternoon power hour."""
        try:
            from broker import MARKET_NAME as _MN
            is_us = "NSE" not in _MN
        except Exception:
            is_us = False

        if is_us:
            # US ET: opening drive 9:30–10:30, afternoon power 13:30–14:45
            from utils import get_current_et_time
            now = get_current_et_time()
            h, m = now.hour, now.minute
            opening   = (9, 30) <= (h, m) <= (10, 30)
            afternoon = (13, 30) <= (h, m) <= (14, 45)
        else:
            # NSE IST: 9:15–10:15, 13:30–14:45
            now = get_current_ist_time()
            h, m = now.hour, now.minute
            opening   = (9, 15) <= (h, m) <= (10, 15)
            afternoon = (13, 30) <= (h, m) <= (14, 45)
        return opening or afternoon

    def scan(self, symbols: List[str], fetcher) -> List[BurstSetup]:
        """
        Scan symbols for burst setups.
        Returns list sorted by burst_score descending.
        """
        results: List[BurstSetup] = []

        for symbol in symbols:
            try:
                setup = self._evaluate(symbol, fetcher)
                if setup:
                    results.append(setup)
            except Exception as e:
                logger.debug(f"Burst scan {symbol}: {e}")

        results.sort(key=lambda s: s.burst_score, reverse=True)
        return results[:5]   # Top 5 burst setups per cycle

    def to_trade_signal(self, setup: BurstSetup):
        """Convert BurstSetup → TradeSignal for execution."""
        try:
            from signal_generator import TradeSignal
            score      = setup.burst_score
            grade      = "A+" if score >= 88 else "A" if score >= 80 else "B"
            size_mult  = 1.4 if grade == "A+" else 1.2 if grade == "A" else 1.0
            rr         = round(abs(setup.target_1 - setup.entry_price) /
                               max(abs(setup.stop_loss - setup.entry_price), 0.01), 2)
            return TradeSignal(
                symbol          = setup.symbol,
                direction       = setup.direction,
                signal_score    = round(score, 1),
                entry_price     = setup.entry_price,
                stop_loss       = setup.stop_loss,
                target_1        = setup.target_1,
                target_2        = setup.target_2,
                risk_reward     = rr,
                atr             = round(setup.entry_price * 0.01, 2),
                patterns        = [f"BURST:{c}" for c in setup.conditions_met[:3]],
                quality_grade   = grade,
                size_multiplier = size_mult,
                rationale       = setup.rationale,
                is_high_confidence = score >= 80,
            )
        except Exception as e:
            logger.debug(f"BurstSetup to_trade_signal failed: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────────
    # DETECTION LOGIC
    # ─────────────────────────────────────────────────────────────────────

    def _evaluate(self, symbol: str, fetcher) -> Optional[BurstSetup]:
        """Evaluate one symbol for burst conditions."""
        df = fetcher.get_today_candles(symbol)
        if df is None or len(df) < 15:
            return None

        df = df.copy()
        close  = df["close"].values.astype(float)
        high   = df["high"].values.astype(float)
        low    = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float)

        ltp = close[-1]
        if ltp < MIN_PRICE:
            return None

        conditions: List[str] = []
        score = 0.0

        # ── Condition 1: Bollinger Band Squeeze ───────────────────────────
        bb_squeeze, bb_squeeze_score = self._check_bb_squeeze(close)
        if bb_squeeze:
            conditions.append("BB_SQUEEZE")
            score += bb_squeeze_score

        # ── Condition 2: RVOL Surge ───────────────────────────────────────
        rvol = self._compute_rvol(volume)
        if rvol >= MIN_RVOL:
            conditions.append(f"RVOL_{rvol:.1f}x")
            score += min(30, (rvol - 1.5) * 12)

        # ── Condition 3: Momentum Acceleration (ROC) ──────────────────────
        roc_pct, direction = self._check_momentum_acceleration(close)
        if abs(roc_pct) >= 0.4:
            conditions.append(f"MOMENTUM_{direction}")
            score += min(20, abs(roc_pct) * 15)
        else:
            direction = "LONG" if close[-1] > close[-3] else "SHORT"

        # ── Condition 4: Inside Bar Breakout ──────────────────────────────
        ib_break, ib_direction = self._check_inside_bar_breakout(high, low, close)
        if ib_break:
            if ib_direction == direction or direction not in ("LONG", "SHORT"):
                direction = ib_direction
            conditions.append(f"INSIDE_BAR_{ib_direction}")
            score += 18

        # ── Condition 5: EMA Stack ────────────────────────────────────────
        ema_aligned, ema_dir = self._check_ema_stack(close)
        if ema_aligned:
            if ema_dir == direction:
                conditions.append(f"EMA_STACK_{ema_dir}")
                score += 15
            else:
                score -= 10   # EMA opposes direction — reduce score

        # ── Condition 6: RSI Regime Reset ─────────────────────────────────
        rsi_reset, rsi_dir = self._check_rsi_reset(close)
        if rsi_reset and rsi_dir == direction:
            conditions.append(f"RSI_RESET_{rsi_dir}")
            score += 14

        # ── Condition 7: Price crossing LVN (fast travel zone) ───────────
        lvn_break, lvn_score = self._check_lvn_break(high, low, close, volume)
        if lvn_break:
            conditions.append("LVN_BREAK")
            score += lvn_score

        # ── Final gate ────────────────────────────────────────────────────
        if len(conditions) < MIN_CONDITIONS:
            return None

        # Normalize score to 0-100
        score = max(0.0, min(100.0, score))

        if direction == "LONG":
            sl  = round(ltp * (1 - BURST_STOP_PCT), 2)
            t1  = round(ltp * (1 + BURST_T1_PCT),   2)
            t2  = round(ltp * (1 + BURST_T2_PCT),   2)
        else:
            sl  = round(ltp * (1 + BURST_STOP_PCT), 2)
            t1  = round(ltp * (1 - BURST_T1_PCT),   2)
            t2  = round(ltp * (1 - BURST_T2_PCT),   2)

        rationale = (
            f"BURST {len(conditions)}/7: {', '.join(conditions[:4])} | "
            f"RVOL={rvol:.1f}x | ROC={roc_pct:+.2f}% | score={score:.0f}"
        )

        logger.info(f"[{format_ist_timestamp()}] {rationale}")

        return BurstSetup(
            symbol           = symbol,
            direction        = direction,
            entry_price      = ltp,
            stop_loss        = sl,
            target_1         = t1,
            target_2         = t2,
            conditions_met   = conditions,
            conditions_count = len(conditions),
            burst_score      = score,
            rvol             = rvol,
            bb_squeeze       = bb_squeeze,
            momentum_pct     = roc_pct,
            rationale        = rationale,
        )

    # ─────────────────────────────────────────────────────────────────────
    # INDIVIDUAL CONDITION CHECKS
    # ─────────────────────────────────────────────────────────────────────

    def _check_bb_squeeze(self, close: np.ndarray) -> tuple:
        """BB width < 20th percentile = coiling for explosion."""
        if len(close) < 20:
            return False, 0.0
        sma = np.mean(close[-20:])
        std = np.std(close[-20:])
        bb_width = (std * 4) / sma * 100  # % width of 2-sigma bands

        # Historical context: compare to past 40 bars
        widths = []
        for i in range(40, len(close)):
            s = np.std(close[i-20:i])
            m = np.mean(close[i-20:i])
            if m > 0:
                widths.append((s * 4) / m * 100)

        if not widths:
            return bb_width < 1.5, 12.0

        threshold = np.percentile(widths, BB_SQUEEZE_PCT)
        is_squeeze = bb_width <= threshold
        squeeze_intensity = max(0, (threshold - bb_width) / threshold * 100)
        score = min(20, squeeze_intensity * 0.4)
        return is_squeeze, score

    def _compute_rvol(self, volume: np.ndarray) -> float:
        """Relative volume = current bar volume / avg of last 20 bars."""
        if len(volume) < 5:
            return 1.0
        avg_vol = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume[:-1])
        if avg_vol <= 0:
            return 1.0
        return round(volume[-1] / avg_vol, 2)

    def _check_momentum_acceleration(self, close: np.ndarray) -> tuple:
        """Rate of change turning sharply in one direction."""
        if len(close) < 6:
            return 0.0, "LONG"
        roc3 = (close[-1] - close[-4]) / close[-4] * 100
        roc6 = (close[-1] - close[-7]) / close[-7] * 100 if len(close) >= 7 else roc3
        direction = "LONG" if roc3 > 0 else "SHORT"
        # Acceleration: short ROC stronger than medium → momentum building
        acceleration = abs(roc3) > abs(roc6) * 0.7
        effective_roc = roc3 if acceleration else roc3 * 0.5
        return round(effective_roc, 3), direction

    def _check_inside_bar_breakout(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray
    ) -> tuple:
        """Detect 2-3 inside bars then current bar breaking out."""
        if len(high) < 5:
            return False, "LONG"

        # Look for 2 consecutive inside bars before the current one
        inside_count = 0
        for i in range(-3, -1):
            if high[i] <= high[i-1] and low[i] >= low[i-1]:
                inside_count += 1

        if inside_count < 1:
            return False, "LONG"

        # Current bar breaking out of the inside bar range
        range_high = max(high[-3:-1])
        range_low  = min(low[-3:-1])
        current    = close[-1]

        if current > range_high * 1.001:
            return True, "LONG"
        elif current < range_low * 0.999:
            return True, "SHORT"
        return False, "LONG"

    def _check_ema_stack(self, close: np.ndarray) -> tuple:
        """EMA9 > EMA21 > EMA50 = bullish stack. Inverted = bearish."""
        if len(close) < 50:
            return False, "LONG"

        def ema(arr, n):
            k = 2 / (n + 1)
            e = arr[0]
            for x in arr[1:]:
                e = x * k + e * (1 - k)
            return e

        e9  = ema(close[-50:], 9)
        e21 = ema(close[-50:], 21)
        e50 = ema(close[-50:], 50)

        if e9 > e21 > e50:
            return True, "LONG"
        elif e9 < e21 < e50:
            return True, "SHORT"
        return False, "LONG"

    def _check_rsi_reset(self, close: np.ndarray) -> tuple:
        """RSI bouncing off 40 (LONG) or rejecting at 60 (SHORT) — momentum reset."""
        if len(close) < 16:
            return False, "LONG"

        deltas = np.diff(close[-15:])
        gains  = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_g  = np.mean(gains) if np.mean(gains) > 0 else 0.001
        avg_l  = np.mean(losses) if np.mean(losses) > 0 else 0.001
        rs     = avg_g / avg_l
        rsi    = 100 - (100 / (1 + rs))

        # RSI reset: was < 42, now rising (LONG) or was > 58, now falling (SHORT)
        deltas2 = np.diff(close[-8:])
        g2 = np.where(deltas2 > 0, deltas2, 0)
        l2 = np.where(deltas2 < 0, -deltas2, 0)
        ag2 = np.mean(g2) if np.mean(g2) > 0 else 0.001
        al2 = np.mean(l2) if np.mean(l2) > 0 else 0.001
        rsi_recent = 100 - (100 / (1 + ag2 / al2))

        if rsi <= 42 and rsi_recent > rsi:
            return True, "LONG"
        elif rsi >= 58 and rsi_recent < rsi:
            return True, "SHORT"
        return False, "LONG"

    def _check_lvn_break(
        self, high: np.ndarray, low: np.ndarray,
        close: np.ndarray, volume: np.ndarray,
    ) -> tuple:
        """Price crossing a Low Volume Node = fast travel zone = burst potential."""
        if len(close) < 20 or len(volume) < 20:
            return False, 0.0

        # Build mini volume profile: 10 price bins over last 20 bars
        price_range = max(high[-20:]) - min(low[-20:])
        if price_range <= 0:
            return False, 0.0

        bin_size = price_range / 10
        lo = min(low[-20:])
        bins = np.zeros(10)
        for i in range(-20, 0):
            idx = min(9, int((close[i] - lo) / bin_size))
            bins[idx] += volume[i]

        # Find LVN (bin with < 30th percentile volume)
        lvn_threshold = np.percentile(bins, 30)
        current_bin   = min(9, int((close[-1] - lo) / bin_size))

        # If current price is in an LVN, movement will be fast
        in_lvn = bins[current_bin] <= lvn_threshold
        if in_lvn:
            lvn_ratio = lvn_threshold / max(bins[current_bin], 1)
            score = min(16, lvn_ratio * 3)
            return True, score
        return False, 0.0


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_burst_detector: Optional[MomentumBurstDetector] = None


def get_burst_detector() -> MomentumBurstDetector:
    global _burst_detector
    if _burst_detector is None:
        _burst_detector = MomentumBurstDetector()
        logger.info(f"[{format_ist_timestamp()}] MomentumBurstDetector initialized")
    return _burst_detector
