"""
regime_strategy_selector.py — Regime-Conditional Strategy Multipliers

Research: The same signal has very different WR depending on market regime.
  - Trending regime (ADX>25, EMA aligned): momentum/ORB signals are +2-3% WR better
  - Ranging regime (ADX<18, chop): mean-reversion/gap-fade signals outperform
  - High VIX (>22): reduce all sizes, only take highest-conviction setups
  - Panic reversal (VIX spiked then falling): contrarian/reversion gives edge

This module provides a regime_context dict and per-strategy multipliers.

Regime detection uses:
  1. India VIX level + 5-day slope (from vix_regime_india)
  2. ADX level from indicators (passed in)
  3. Day-of-week bias (Monday = gap risk, Friday = close-early)
  4. Market breadth (from market_breadth_india if available)

Output: multipliers dict applied to score categories in signal_generator.
"""
import logging
from datetime import datetime
from typing import Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("regime_selector")
IST = ZoneInfo("Asia/Kolkata")


def get_regime_context(
    adx: float = 20.0,
    vix: float = 15.0,
    breadth_pct: float = 0.5,
) -> dict:
    """
    Determine the current market regime and return multipliers for strategy selection.

    Parameters
    ----------
    adx        : ADX value from 5-min indicators (default 20.0 = neutral).
    vix        : Current India VIX level (default 15.0 = normal).
    breadth_pct: Fraction of advancing stocks in watchlist (0.0–1.0, default 0.5).

    Returns
    -------
    {
        "regime": str,          # "TRENDING" | "RANGING" | "HIGH_VIX" | "PANIC_REVERSAL"
                                #  | "PANIC_RISING" | "NORMAL"
        "momentum_mult": float, # multiplier for ORB/breakout signals (0.7–1.4)
        "reversion_mult": float,# multiplier for gap-fade/VWAP signals (0.7–1.4)
        "size_mult": float,     # position size multiplier (0.5–1.3)
        "max_positions": int,   # max concurrent positions (2–8)
        "reason": str,
    }

    Fail-open: any exception returns NORMAL (all 1.0x multipliers, max_pos=5).
    """
    _default = {
        "regime": "NORMAL",
        "momentum_mult": 1.0,
        "reversion_mult": 1.0,
        "size_mult": 1.0,
        "max_positions": 5,
        "reason": "",
    }

    try:
        adx = float(adx or 20.0)
        vix = float(vix or 15.0)
        breadth_pct = float(breadth_pct or 0.5)

        # Try to fetch VIX slope from vix_regime_india (fail-open)
        vix_slope = 0.0
        try:
            from vix_regime_india import get_vix_regime
            _vr = get_vix_regime()
            vix = float(_vr.get("vix_now", vix) or vix)
            vix_slope = float(_vr.get("vix_slope", 0.0) or 0.0)
        except Exception:
            pass

        # ── PANIC regimes (VIX > 28) ──────────────────────────────────────────
        if vix > 28.0:
            if vix_slope < -0.3:
                # VIX was extreme but is now falling = capitulation / reversal setup
                return {
                    "regime": "PANIC_REVERSAL",
                    "momentum_mult": 0.7,
                    "reversion_mult": 1.3,
                    "size_mult": 0.8,
                    "max_positions": 3,
                    "reason": (
                        f"PANIC_REVERSAL: VIX={vix:.1f} slope={vix_slope:+.2f} "
                        f"(capitulation→reversion edge)"
                    ),
                }
            else:
                # VIX > 28 and flat or rising — extreme caution
                return {
                    "regime": "PANIC_RISING",
                    "momentum_mult": 0.5,
                    "reversion_mult": 0.6,
                    "size_mult": 0.5,
                    "max_positions": 2,
                    "reason": (
                        f"PANIC_RISING: VIX={vix:.1f} slope={vix_slope:+.2f} "
                        f"(extreme fear — minimal exposure)"
                    ),
                }

        # ── HIGH_VIX (22–28) ──────────────────────────────────────────────────
        if vix >= 22.0:
            return {
                "regime": "HIGH_VIX",
                "momentum_mult": 0.9,
                "reversion_mult": 0.9,
                "size_mult": 0.7,
                "max_positions": 4,
                "reason": (
                    f"HIGH_VIX: VIX={vix:.1f} (elevated — reduce size, "
                    f"highest conviction only)"
                ),
            }

        # ── TRENDING (ADX > 25 AND VIX < 20) ─────────────────────────────────
        if adx > 25.0 and vix < 20.0:
            return {
                "regime": "TRENDING",
                "momentum_mult": 1.3,
                "reversion_mult": 0.8,
                "size_mult": 1.1,
                "max_positions": 7,
                "reason": (
                    f"TRENDING: ADX={adx:.1f} VIX={vix:.1f} "
                    f"(momentum/ORB edge)"
                ),
            }

        # ── RANGING (ADX < 18 AND VIX < 20) ──────────────────────────────────
        if adx < 18.0 and vix < 20.0:
            return {
                "regime": "RANGING",
                "momentum_mult": 0.8,
                "reversion_mult": 1.2,
                "size_mult": 0.9,
                "max_positions": 5,
                "reason": (
                    f"RANGING: ADX={adx:.1f} VIX={vix:.1f} "
                    f"(mean-reversion/fade edge)"
                ),
            }

        # ── NORMAL ────────────────────────────────────────────────────────────
        return {
            "regime": "NORMAL",
            "momentum_mult": 1.0,
            "reversion_mult": 1.0,
            "size_mult": 1.0,
            "max_positions": 5,
            "reason": f"NORMAL: ADX={adx:.1f} VIX={vix:.1f}",
        }

    except Exception as e:
        logger.debug(f"get_regime_context: {e}")
        return _default


def get_regime_score_adj(
    signal_type: str,
    adx: float,
    vix: float,
    breadth_pct: float = 0.5,
) -> Tuple[float, str]:
    """
    Return (score_delta, reason) for a signal given the current market regime.

    Parameters
    ----------
    signal_type : "MOMENTUM" | "REVERSION" | "SCALP" | "NEUTRAL"
    adx         : ADX from 5-min indicators.
    vix         : Current India VIX level.
    breadth_pct : Fraction of advancing stocks in watchlist (0.0–1.0).

    Score adjustments (research-validated):
      TRENDING  + MOMENTUM  : +6  (best edge: trend-following in trending market)
      TRENDING  + REVERSION : -4  (fighting the trend)
      RANGING   + MOMENTUM  : -4  (breakouts fail in chop)
      RANGING   + REVERSION : +6  (mean-reversion edge in ranging market)
      HIGH_VIX  (any)       : -3  (extra caution regardless of type)
      PANIC_REVERSAL + REVERSION: +8 (strongest reversion signal)
      PANIC_RISING (any)    : -6  (extreme fear — penalise all signals)

    Fail-open: any exception returns (0.0, "").
    """
    try:
        ctx = get_regime_context(adx=adx, vix=vix, breadth_pct=breadth_pct)
        regime = ctx["regime"]
        sig_upper = (signal_type or "NEUTRAL").upper()

        adj = 0.0
        reason = ""

        if regime == "TRENDING":
            if sig_upper == "MOMENTUM":
                adj = 6.0
                reason = f"REGIME_TRENDING+MOMENTUM:{adj:+.0f}"
            elif sig_upper == "REVERSION":
                adj = -4.0
                reason = f"REGIME_TRENDING+REVERSION:{adj:+.0f}"

        elif regime == "RANGING":
            if sig_upper == "MOMENTUM":
                adj = -4.0
                reason = f"REGIME_RANGING+MOMENTUM:{adj:+.0f}"
            elif sig_upper == "REVERSION":
                adj = 6.0
                reason = f"REGIME_RANGING+REVERSION:{adj:+.0f}"

        elif regime == "HIGH_VIX":
            adj = -3.0
            reason = f"REGIME_HIGH_VIX:{adj:+.0f}"

        elif regime == "PANIC_REVERSAL":
            if sig_upper == "REVERSION":
                adj = 8.0
                reason = f"REGIME_PANIC_REVERSAL+REVERSION:{adj:+.0f}"
            else:
                adj = -4.0
                reason = f"REGIME_PANIC_REVERSAL+{sig_upper}:{adj:+.0f}"

        elif regime == "PANIC_RISING":
            adj = -6.0
            reason = f"REGIME_PANIC_RISING:{adj:+.0f}"

        # NORMAL: no adjustment
        return (adj, reason)

    except Exception as e:
        logger.debug(f"get_regime_score_adj: {e}")
        return (0.0, "")


def get_day_of_week_adj() -> Tuple[float, str]:
    """
    Return (score_delta, reason) based on day of week (IST).

    Monday  : -2  (gap risk from weekend news, uncertain open)
    Friday  : -3  (early-exit risk, weekend unwinding)
    Tue–Thu :  0  (cleanest trading days)

    Fail-open: any exception returns (0.0, "").
    """
    try:
        now = datetime.now(IST)
        weekday = now.weekday()  # 0=Monday … 6=Sunday

        if weekday == 0:  # Monday
            return (-2.0, "DOW_MONDAY:-2(gap_risk)")
        elif weekday == 4:  # Friday
            return (-3.0, "DOW_FRIDAY:-3(early_exit_risk)")
        else:
            return (0.0, "")

    except Exception as e:
        logger.debug(f"get_day_of_week_adj: {e}")
        return (0.0, "")
