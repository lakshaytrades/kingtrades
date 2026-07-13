"""
vix_regime_india.py — India VIX Regime & Trend Signal

Research finding (Macrosynergy, confirmed): when VIX is in backwardation
(near-term fear spike above long-term) and then starts falling, subsequent
equity returns are strongly positive. When VIX is rising from low levels,
equity returns deteriorate.

India VIX is published by NSE and fetched via data_fetch_upstox.get_india_vix().
We store a rolling 10-day history to compute:
  1. VIX trend (slope of last 5 days)
  2. VIX level regime (panic / elevated / normal / calm)
  3. VIX reversal pattern (spike→fall = contrarian LONG; low→rising = caution)

Score impact:
  Panic spike reversing (VIX > 22 → now falling): LONG +10, SHORT -8
  Elevated + falling slowly:                       LONG +5
  VIX normal + stable:                              0
  VIX rising from low (<14 → now rising):          LONG -4
  VIX elevated + rising:                           LONG -10, SHORT +8
  VIX extreme + rising (>28):                      LONG -15, SHORT +12

State stored in memory; history seeded from NSE on first call.
"""
import json
import logging
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

logger = logging.getLogger("vix_regime")
IST = ZoneInfo("Asia/Kolkata")

_HISTORY_FILE = Path(__file__).parent.parent / "data" / "vix_history.json"
_vix_history: List[float] = []   # rolling last 10 readings
_last_fetch_ts: float = 0.0
_FETCH_TTL = 1800.0   # 30 minutes (matches data_fetch_upstox VIX TTL)


def _load_history():
    global _vix_history
    try:
        if _HISTORY_FILE.exists():
            _vix_history = json.loads(_HISTORY_FILE.read_text())
    except Exception:
        _vix_history = []


def _save_history():
    try:
        _HISTORY_FILE.parent.mkdir(exist_ok=True)
        _HISTORY_FILE.write_text(json.dumps(_vix_history[-20:]))
    except Exception:
        pass


def _update_vix():
    """Fetch current India VIX and append to history."""
    global _last_fetch_ts, _vix_history
    now = _time.time()
    if now - _last_fetch_ts < _FETCH_TTL:
        return

    try:
        from data_fetch_upstox import get_india_vix
        vix = get_india_vix()
        if vix > 0:
            _vix_history.append(float(vix))
            if len(_vix_history) > 20:
                _vix_history = _vix_history[-20:]
            _save_history()
            _last_fetch_ts = now
    except Exception as e:
        logger.debug(f"vix_update: {e}")


def get_vix_regime() -> dict:
    """
    Compute the current India VIX regime.
    Returns:
      {
        "regime": "PANIC_REVERSAL"/"ELEVATED_FALLING"/"NORMAL"/"LOW_RISING"/"ELEVATED_RISING"/"PANIC_RISING",
        "vix_now": float,
        "vix_slope": float,   # 5-day linear slope
        "score_adj_long": int,
        "score_adj_short": int,
        "reason": str,
      }
    Fail-open: returns NORMAL, 0 adjustments.
    """
    _empty = {"regime": "NORMAL", "vix_now": 0.0, "vix_slope": 0.0,
              "score_adj_long": 0, "score_adj_short": 0, "reason": ""}

    if not _vix_history:
        _load_history()
    _update_vix()

    try:
        if not _vix_history:
            return _empty

        vix_now = _vix_history[-1]

        # Compute 5-day slope (linear regression over last 5 readings)
        hist = _vix_history[-5:]
        if len(hist) >= 3:
            x = np.arange(len(hist), dtype=float)
            y = np.array(hist, dtype=float)
            slope = float(np.polyfit(x, y, 1)[0])
        else:
            slope = 0.0

        # Was there a recent spike? (max of last 5 days)
        recent_max = max(hist) if hist else vix_now
        spike_occurred = recent_max > vix_now * 1.15 and recent_max > 20

        # ── Regime classification ─────────────────────────────────────────────

        if vix_now > 28:
            if slope < -0.3:   # extreme but falling = capitulation/reversal
                regime = "PANIC_REVERSAL"
                adj_l, adj_s = +10, -8
                reason = f"VIX={vix_now:.1f} PANIC reversing ↓{abs(slope):.1f}/day"
            else:
                regime = "PANIC_RISING"
                adj_l, adj_s = -15, +12
                reason = f"VIX={vix_now:.1f} PANIC ↑{slope:+.1f}/day"

        elif vix_now > 22:
            if slope < -0.2:   # elevated but falling
                regime = "ELEVATED_FALLING"
                adj_l, adj_s = +5, -5
                reason = f"VIX={vix_now:.1f} elevated ↓{abs(slope):.1f}/day"
            elif slope > 0.2:  # elevated and rising
                regime = "ELEVATED_RISING"
                adj_l, adj_s = -10, +8
                reason = f"VIX={vix_now:.1f} elevated ↑{slope:.1f}/day"
            else:
                regime = "ELEVATED_STABLE"
                adj_l, adj_s = -4, +2
                reason = f"VIX={vix_now:.1f} elevated stable"

        elif vix_now < 14:
            if slope > 0.15:   # complacency ending — rising from low
                regime = "LOW_RISING"
                adj_l, adj_s = -4, +3
                reason = f"VIX={vix_now:.1f} low but ↑{slope:.1f}/day"
            else:
                regime = "CALM"
                adj_l, adj_s = +3, -3   # calm markets favour long
                reason = f"VIX={vix_now:.1f} calm"

        else:   # 14-22: normal zone
            if spike_occurred and slope < -0.1:   # spike then fall = buying opp
                regime = "SPIKE_REVERSAL"
                adj_l, adj_s = +6, -4
                reason = f"VIX spike→{recent_max:.1f} now reversing to {vix_now:.1f}"
            else:
                regime = "NORMAL"
                adj_l, adj_s = 0, 0
                reason = f"VIX={vix_now:.1f} normal"

        return {
            "regime": regime,
            "vix_now": round(vix_now, 1),
            "vix_slope": round(slope, 3),
            "score_adj_long": adj_l,
            "score_adj_short": adj_s,
            "reason": reason,
        }

    except Exception as e:
        logger.debug(f"get_vix_regime: {e}")
        return _empty


def get_vix_regime_score(direction: str) -> Tuple[int, str]:
    """
    Return (score_adj, reason) for a signal given the current VIX regime.
    Fail-open: (0, "") on any error.
    """
    try:
        regime = get_vix_regime()
        adj = (regime["score_adj_long"] if direction == "LONG"
               else regime["score_adj_short"])
        if adj == 0:
            return (0, "")
        return (adj, f"VIX_REGIME_{regime['regime']}:{adj:+d}")
    except Exception as e:
        logger.debug(f"get_vix_regime_score: {e}")
        return (0, "")


# Load history on import
_load_history()
