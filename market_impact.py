"""
market_impact.py — Market Impact & Execution Intelligence (v27.0)

Renaissance/Citadel/Two Sigma model market impact BEFORE placing orders.
Two key checks:

1. Almgren-Chriss Participation Rate (1999):
   If order size > 1% of Average Daily Volume, you MOVE the market against yourself.
   This is a well-documented source of alpha decay for large funds.
   Score: -8 if participation > 1% ADV, -4 if > 0.5%, -2 if > 0.25%

2. Execution Timing Intelligence:
   First 5 minutes (9:30-9:35 ET): Widest spreads, most noise, institutional algos
   clearing overnight imbalances. Worst time to enter.
   Last 10 minutes (3:50-4:00 ET): MOC order flow, index rebalancing.
   Best times: 9:45-10:30 AM (direction established), 3:00-3:30 PM (power hour).

Score bonuses/penalties are applied to signal score before position sizing.
"""

import logging
from datetime import time as dtime
from typing import Tuple

logger = logging.getLogger(__name__)

# Almgren-Chriss thresholds
_ADV_CACHE: dict = {}   # {symbol: (adv, ts)}
_ADV_TTL = 3600.0       # refresh ADV hourly


def get_market_impact_score(
    symbol: str,
    order_qty: int,
    current_price: float,
    direction: str,
) -> Tuple[float, str]:
    """
    Check if order size creates adverse market impact (Almgren-Chriss model).
    Returns (score_delta, reason). Fail-open.
    """
    try:
        if order_qty <= 0 or current_price <= 0:
            return 0.0, "impact:invalid-params"

        adv = _get_adv(symbol)
        if adv is None or adv <= 0:
            return 0.0, "impact:no-adv"

        participation_rate = order_qty / adv

        if participation_rate > 0.01:   # > 1% ADV
            return -8.0, f"impact:too-large({participation_rate*100:.2f}% ADV)"
        elif participation_rate > 0.005:  # > 0.5% ADV
            return -4.0, f"impact:large({participation_rate*100:.2f}% ADV)"
        elif participation_rate > 0.0025:  # > 0.25% ADV
            return -2.0, f"impact:moderate({participation_rate*100:.2f}% ADV)"
        elif participation_rate < 0.0005:  # < 0.05% ADV = trivially small
            return 2.0, f"impact:minimal({participation_rate*100:.3f}% ADV)"

        return 0.0, f"impact:acceptable({participation_rate*100:.3f}% ADV)"

    except Exception as e:
        logger.debug(f"[market_impact] {symbol} failed: {e}")
        return 0.0, "impact:error"


def get_execution_timing_score(direction: str) -> Tuple[float, str]:
    """
    Score based on current time relative to optimal execution windows.
    Uses Eastern Time for US market windows.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        from utils import get_current_ist_time
        import datetime

        # Get current ET time (IST - 9.5 hours = ET + 5.5h IST offset)
        # IST = UTC+5:30, ET = UTC-5:00 (EST) or UTC-4:00 (EDT)
        # Approximate: ET ≈ IST - 10.5h (winter) or IST - 9.5h (summer)
        # Use fixed offset for simplicity — we just need approximate window check
        now_ist = get_current_ist_time()
        # Convert IST → ET: subtract 10 hours 30 minutes (EST) or 9h30m (EDT)
        # Use 10h as conservative approximation
        ist_hour = now_ist.hour
        ist_min  = now_ist.minute
        total_ist_minutes = ist_hour * 60 + ist_min
        total_et_minutes  = total_ist_minutes - 630  # 10.5 hours = 630 min
        if total_et_minutes < 0:
            total_et_minutes += 1440  # wrap midnight

        et_hour = total_et_minutes // 60
        et_min  = total_et_minutes % 60
        t = dtime(et_hour % 24, et_min)

        # Avoid first 5 minutes: 9:30–9:35 ET
        if dtime(9, 30) <= t < dtime(9, 35):
            return -4.0, "timing:open-chaos(avoid first 5min)"

        # Avoid last 10 minutes: 3:50–4:00 ET
        if dtime(15, 50) <= t <= dtime(16, 0):
            return -6.0, "timing:close-MOC(avoid last 10min)"

        # Power hour: 3:00–3:30 PM ET (institutional rebalancing, strong moves)
        if dtime(15, 0) <= t <= dtime(15, 30):
            if direction in ("LONG", "SHORT"):
                return 4.0, "timing:power-hour(institutional flow)"

        # Morning window: 9:45–10:30 ET (direction established, spread narrows)
        if dtime(9, 45) <= t <= dtime(10, 30):
            return 3.0, "timing:morning-window(optimal entry)"

        # Lunch doldrums: 12:00–13:30 ET (low volume, choppy)
        if dtime(12, 0) <= t <= dtime(13, 30):
            return -2.0, "timing:lunch-doldrums(low volume)"

        return 0.0, f"timing:neutral({et_hour:02d}:{et_min:02d} ET)"

    except Exception as e:
        logger.debug(f"[market_impact] timing failed: {e}")
        return 0.0, "timing:error"


def _get_adv(symbol: str) -> float:
    """Get average daily volume for symbol. Uses yfinance 30-day average."""
    import time as _time
    now = _time.monotonic()
    cached = _ADV_CACHE.get(symbol)
    if cached and (now - cached[1]) < _ADV_TTL:
        return cached[0]

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        # fast_info has three_month_average_volume
        adv = getattr(info, "three_month_average_volume", None)
        if adv and adv > 0:
            _ADV_CACHE[symbol] = (float(adv), now)
            return float(adv)

        # Fallback: compute from 30-day history
        hist = ticker.history(period="30d", interval="1d")
        if hist is not None and not hist.empty and "Volume" in hist.columns:
            adv = float(hist["Volume"].mean())
            _ADV_CACHE[symbol] = (adv, now)
            return adv

    except Exception as e:
        logger.debug(f"[market_impact] _get_adv {symbol}: {e}")

    return 0.0
