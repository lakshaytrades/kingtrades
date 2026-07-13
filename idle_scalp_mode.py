"""
idle_scalp_mode.py — Midday Idle Scalp Mode Engine (v33.1)

When the bot has had no executed trade for IDLE_THRESHOLD minutes
AND daily P&L is below 70% of the daily target, activates a lower-
conviction scalp mode to find small trades rather than sitting idle.

Idle scalp mode vs prime mode:
  Score threshold:  55  (prime: 60)
  Min R:R:          1.5 (prime: 2.0)
  Position size:    40% of normal (keeps risk small)
  T1 target:        0.8:1 R:R (quick first book)
  T2 target:        1.5:1 R:R (fast full exit)
  Time stop:        10 min (no lingering in scalps)

Active window: 9:45 AM – 2:45 PM ET only (outside opening/close rush)
Deactivates when:
  - Trade executed (timer resets)
  - Daily P&L >= 70% of daily target
  - VIX > 35 (too volatile)
  - After 2:45 PM ET
"""
import logging
import time as _time
from datetime import datetime
from typing import Dict
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# Module-level state (updated by main.py each cycle)
_last_trade_ts:    float = 0.0   # monotonic time of last executed trade
_daily_pnl_pct:    float = 0.0   # current day P&L as % of capital
_daily_target_pct: float = 1.0   # daily profit target %
_vix_level:        float = 20.0  # current VIX (updated if available)


def update_state(
    last_trade_ts:    float,
    daily_pnl_pct:    float,
    daily_target_pct: float = 1.0,
    vix_level:        float = 0.0,
) -> None:
    """Call this from main.py before each scan cycle."""
    global _last_trade_ts, _daily_pnl_pct, _daily_target_pct, _vix_level
    _last_trade_ts    = last_trade_ts
    _daily_pnl_pct    = daily_pnl_pct
    _daily_target_pct = daily_target_pct
    if vix_level > 0:
        _vix_level = vix_level


def is_active() -> bool:
    """
    Returns True when idle scalp mode should activate.
    All conditions must be met simultaneously.
    """
    try:
        import config
        if not getattr(config, "IDLE_SCALP_ENABLED", True):
            return False

        # Time gate: only active 9:45 AM – 2:45 PM ET
        now_et = datetime.now(ET)
        et_min = now_et.hour * 60 + now_et.minute
        if et_min < 585 or et_min >= 885:   # before 9:45 or after 2:45 PM
            return False

        # Don't activate if already near/at daily target
        target_pct = _daily_target_pct if _daily_target_pct > 0 else 1.0
        if _daily_pnl_pct >= target_pct * 0.70:
            return False

        # Don't activate in extreme fear (too volatile for small scalps)
        if _vix_level > 35:
            return False

        # Check idle time
        threshold_min = getattr(config, "IDLE_SCALP_THRESHOLD_MIN", 30)
        elapsed_min = (_time.monotonic() - _last_trade_ts) / 60.0
        if _last_trade_ts == 0.0:
            # Bot just started — use time since 9:30 AM as idle
            market_open_min = et_min - 570  # minutes since 9:30
            elapsed_min = max(0.0, float(market_open_min))

        return elapsed_min >= threshold_min

    except Exception as e:
        logger.debug(f"[idle_scalp] is_active error: {e}")
        return False


def get_params() -> Dict:
    """Return scalp parameters. Always safe to call."""
    try:
        import config
        return {
            "score_min":     getattr(config, "IDLE_SCALP_MIN_SCORE",     55.0),
            "min_rr":        getattr(config, "IDLE_SCALP_MIN_RR",         1.5),
            "size_mult":     getattr(config, "IDLE_SCALP_SIZE_MULT",      0.40),
            "time_stop_min": getattr(config, "IDLE_SCALP_TIME_STOP_MIN",  10),
            "t1_rr":         0.8,   # T1 at 0.8:1 — quick first book
            "t2_rr":         1.5,   # T2 at 1.5:1 — fast full exit
        }
    except Exception:
        return {"score_min": 55.0, "min_rr": 1.5, "size_mult": 0.40,
                "time_stop_min": 10, "t1_rr": 0.8, "t2_rr": 1.5}


def idle_minutes() -> float:
    """Returns minutes since last trade (or since market open if no trade yet)."""
    try:
        if _last_trade_ts == 0.0:
            now_et = datetime.now(ET)
            et_min = now_et.hour * 60 + now_et.minute
            return max(0.0, float(et_min - 570))
        return (_time.monotonic() - _last_trade_ts) / 60.0
    except Exception:
        return 0.0
