"""
signal_decay_india.py — Signal Freshness / Time-Decay Scorer

Research: NSE intraday momentum setups lose ~50% of expected edge after 20 minutes.
Setups older than 40 min should be downweighted to 0.25x of original score.

Decay formula: multiplier = exp(-age_minutes / TAU)
TAU = 20 minutes (half-life = ~14 min)

Score impact: Multiplies the accumulated score by decay factor before final threshold
check. This ensures old setups don't trigger entries at the same conviction level as
fresh ones.

Special cases:
  - ORB signals (9:15-9:45 AM): TAU = 30 min (ORB valid longer)
  - Power hour signals (last 30 min): TAU = 10 min (urgent, decay faster)
  - Signals in PANIC regime (VIX > 22): TAU = 15 min (fast-moving markets)

Fail-open: if timing data is unavailable, returns multiplier=1.0 (no decay applied).
"""
import logging
import math
from datetime import datetime, date
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("signal_decay")

IST = ZoneInfo("Asia/Kolkata")

# ── Decay time constants ──────────────────────────────────────────────────────
TAU_STANDARD    = 20.0   # standard 20-min half-life
TAU_ORB         = 30.0   # ORB signals have longer validity
TAU_POWER_HOUR  = 10.0   # last 30 min urgent — faster decay
TAU_PANIC       = 15.0   # high VIX = fast-moving markets, tighter decay

MIN_MULTIPLIER  = 0.15   # never decay below 15% floor

# ── Module-level state ────────────────────────────────────────────────────────
# {symbol: {"time": datetime, "signal_type": str, "date": date}}
_signal_registry: Dict[str, Dict] = {}

# ── VIX panic threshold ───────────────────────────────────────────────────────
_VIX_PANIC_THRESHOLD = 22.0

# ── Power hour window (3:00 PM – 3:30 PM IST) ────────────────────────────────
_POWER_HOUR_START_H  = 15
_POWER_HOUR_START_M  = 0

# ── ORB window (9:15 AM – 9:45 AM IST) ───────────────────────────────────────
_ORB_START_H   = 9
_ORB_START_M   = 15
_ORB_END_H     = 9
_ORB_END_M     = 45


def _reset_if_new_day() -> None:
    """Clear the registry if the date has rolled over (daily reset)."""
    today = datetime.now(IST).date()
    stale = [
        sym for sym, rec in _signal_registry.items()
        if rec.get("date") != today
    ]
    for sym in stale:
        del _signal_registry[sym]


def record_signal_start(symbol: str, signal_type: str) -> None:
    """
    Record the moment a new signal candidate is first detected for a symbol.

    Only updates the timestamp if no existing record exists for today, or if
    the signal_type has changed (new direction = fresh clock).

    Args:
        symbol:      NSE ticker (e.g. "RELIANCE")
        signal_type: Direction string — typically "LONG" or "SHORT"
    """
    try:
        _reset_if_new_day()
        now = datetime.now(IST)
        today = now.date()

        existing = _signal_registry.get(symbol)
        if existing is None or existing.get("signal_type") != signal_type:
            # Fresh signal or direction flip — reset the clock
            _signal_registry[symbol] = {
                "time":        now,
                "signal_type": signal_type,
                "date":        today,
            }
            logger.debug(
                "signal_decay: recorded %s %s at %s",
                symbol, signal_type,
                now.strftime("%H:%M:%S IST"),
            )
    except Exception as e:
        logger.debug("signal_decay.record_signal_start error (suppressed): %s", e)


def get_signal_age_minutes(symbol: str) -> float:
    """
    Return age in minutes of the last recorded signal setup for this symbol.

    Returns 0.0 if no record exists (treated as brand-new / fail-open).

    Args:
        symbol: NSE ticker

    Returns:
        Age in fractional minutes (0.0 if no record or error).
    """
    try:
        _reset_if_new_day()
        rec = _signal_registry.get(symbol)
        if rec is None:
            return 0.0
        now = datetime.now(IST)
        delta = now - rec["time"]
        minutes = delta.total_seconds() / 60.0
        return max(0.0, minutes)
    except Exception as e:
        logger.debug("signal_decay.get_signal_age_minutes error (suppressed): %s", e)
        return 0.0


def _select_tau(signal_type: str, vix_level: float) -> float:
    """
    Choose the appropriate TAU constant based on signal type and market regime.

    Priority order:
      1. PANIC regime (VIX > 22) overrides all → TAU_PANIC
      2. ORB signal (signal_type contains "ORB") → TAU_ORB
      3. Power hour (current IST time >= 15:00) → TAU_POWER_HOUR
      4. Check ORB time window (9:15–9:45 IST) → TAU_ORB
      5. Default → TAU_STANDARD

    Args:
        signal_type: Signal type / direction string (e.g. "LONG", "SHORT", "ORB_LONG")
        vix_level:   Current India VIX level (0 = unknown → use standard)

    Returns:
        TAU value in minutes.
    """
    try:
        # VIX panic regime has highest priority
        if vix_level > _VIX_PANIC_THRESHOLD:
            return TAU_PANIC

        # ORB signal type (explicit label)
        if signal_type and "ORB" in signal_type.upper():
            return TAU_ORB

        now = datetime.now(IST)
        h, m = now.hour, now.minute

        # Power hour: 15:00 IST onward
        if h >= _POWER_HOUR_START_H:
            return TAU_POWER_HOUR

        # ORB time window (9:15–9:45 IST)
        in_orb_window = (
            h == _ORB_START_H and _ORB_START_M <= m <= _ORB_END_M
        )
        if in_orb_window:
            return TAU_ORB

        return TAU_STANDARD

    except Exception:
        return TAU_STANDARD


def get_decay_multiplier(
    symbol: str,
    signal_type: str,
    vix_level: float = 15.0,
) -> float:
    """
    Compute the time-decay multiplier for a symbol's signal age.

    Formula: multiplier = max(MIN_MULTIPLIER, exp(-age / TAU))

    Fail-open: returns 1.0 if signal age cannot be determined.

    Args:
        symbol:      NSE ticker
        signal_type: Signal type / direction (e.g. "LONG", "SHORT", "ORB_LONG")
        vix_level:   Current India VIX level (0 = unknown / ignore)

    Returns:
        float in [MIN_MULTIPLIER, 1.0]
    """
    try:
        age_min = get_signal_age_minutes(symbol)
        if age_min <= 0.0:
            return 1.0  # brand-new signal — no decay

        tau = _select_tau(signal_type, vix_level)
        raw = math.exp(-age_min / tau)
        return max(MIN_MULTIPLIER, min(1.0, raw))

    except Exception as e:
        logger.debug("signal_decay.get_decay_multiplier error (fail-open): %s", e)
        return 1.0  # fail-open


def apply_decay(
    score: float,
    symbol: str,
    signal_type: str,
    vix_level: float = 15.0,
) -> Tuple[float, str]:
    """
    Apply time-decay multiplier to an accumulated signal score.

    The multiplier is applied to (score - 50) so that only the earned
    conviction above the baseline is decayed, preserving the 50-point
    neutral base. The floor ensures the result cannot drop below
    50 * MIN_MULTIPLIER due to decay alone.

    Args:
        score:       Accumulated signal score (0–100+)
        symbol:      NSE ticker
        signal_type: Signal direction / type ("LONG", "SHORT", etc.)
        vix_level:   Current India VIX level

    Returns:
        (decayed_score, reason_str) where reason_str is empty if no decay applied.
    """
    try:
        multiplier = get_decay_multiplier(symbol, signal_type, vix_level)

        if multiplier >= 0.999:
            return score, ""  # brand-new signal, no decay

        age_min = get_signal_age_minutes(symbol)
        tau     = _select_tau(signal_type, vix_level)

        # Decay only the earned conviction above baseline (50 pts)
        baseline   = 50.0
        conviction = score - baseline
        if conviction <= 0:
            # Score is at or below baseline — apply multiplier directly but respect floor
            decayed_score = max(score * MIN_MULTIPLIER, score * multiplier)
        else:
            decayed_conviction = conviction * multiplier
            decayed_score      = baseline + decayed_conviction

        decayed_score = round(max(0.0, decayed_score), 2)

        reason = (
            f"DECAY x{multiplier:.2f} "
            f"(age={age_min:.0f}m tau={tau:.0f}m)"
        )
        logger.debug(
            "signal_decay %s %s: score %.1f -> %.1f %s",
            symbol, signal_type, score, decayed_score, reason,
        )
        return decayed_score, reason

    except Exception as e:
        logger.debug("signal_decay.apply_decay error (fail-open): %s", e)
        return score, ""  # fail-open: return unchanged score
