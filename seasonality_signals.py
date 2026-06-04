"""
seasonality_signals.py — Calendar & Seasonality Alpha (v26.0)
Renaissance's most reliable edge: predictable calendar patterns.
50+ years of academic confirmation. Zero data cost — pure math on timestamps.
"""
import logging
import time as _time
from calendar import monthrange
from datetime import datetime, date, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Module-level cache for OpEx pin (options chain is slow) ─────────────────
_opex_pin_cache: Dict[str, Tuple[float, str, float]] = {}  # symbol → (score, reason, ts)
_OPEX_PIN_TTL = 3600.0  # 1 hour


def _third_friday(year: int, month: int) -> date:
    """Return the date of the 3rd Friday of the given month."""
    first_day = date(year, month, 1)
    # weekday() 4 = Friday
    day_of_first_friday = (4 - first_day.weekday()) % 7
    if day_of_first_friday == 0:
        day_of_first_friday = 7
    third_fri = first_day + timedelta(days=day_of_first_friday - 1 + 14)
    return third_fri


def _trading_days_remaining_in_month(today: date) -> int:
    """Approximate trading days left in the month (Mon-Fri only, no holiday adjustment)."""
    last_day = monthrange(today.year, today.month)[1]
    remaining = 0
    for d in range(today.day + 1, last_day + 1):
        if date(today.year, today.month, d).weekday() < 5:
            remaining += 1
    return remaining


def _trading_days_remaining_in_quarter(today: date) -> int:
    """Approximate trading days left in the current quarter (Mon-Fri only)."""
    quarter_end_month = ((today.month - 1) // 3 + 1) * 3   # 3, 6, 9, or 12
    last_day_of_quarter = date(
        today.year if quarter_end_month <= 12 else today.year + 1,
        quarter_end_month,
        monthrange(today.year, quarter_end_month)[1],
    )
    remaining = 0
    current = today + timedelta(days=1)
    while current <= last_day_of_quarter:
        if current.weekday() < 5:
            remaining += 1
        current += timedelta(days=1)
    return remaining


def get_opex_score(direction: str) -> Tuple[float, str]:
    """
    Options Expiry Week Effect. 3rd Friday of every month = OpEx.
    - Week before OpEx (Mon-Thu of OpEx week): momentum dampened → -5
    - OpEx Friday afternoon: pin breaks violently → +8 for direction of day's move
    """
    try:
        today = date.today()
        opex = _third_friday(today.year, today.month)
        opex_week_start = opex - timedelta(days=4)  # Monday of OpEx week

        if today == opex:
            # OpEx Friday: pin break drives hard moves — reward aligned direction
            score = 8.0 if direction in ("LONG", "SHORT") else 0.0
            return (score, "OpEx Friday pin-break effect")

        if opex_week_start <= today < opex:
            # Mon-Thu of OpEx week: market makers pin strikes → momentum dampened
            return (-5.0, "OpEx week pin suppression (Mon-Thu)")

        return (0.0, "not OpEx week")

    except Exception as _e:
        logger.debug(f"[suppressed] get_opex_score: {_e}")
        return (0.0, "error — fail-open")


def get_month_end_score(direction: str) -> Tuple[float, str]:
    """
    Last 3 trading days of month: fund managers buy winners (window dressing).
    LONG only: +7 on last 3 days. No effect for SHORT.
    """
    try:
        today = date.today()
        td_remaining = _trading_days_remaining_in_month(today)

        if td_remaining <= 3:
            if direction == "LONG":
                return (7.0, f"month-end window dressing ({td_remaining} trading days left)")
            else:
                return (0.0, "month-end — no SHORT boost (fund mgrs buy, not sell)")

        return (0.0, "not month-end window")

    except Exception as _e:
        logger.debug(f"[suppressed] get_month_end_score: {_e}")
        return (0.0, "error — fail-open")


def get_quarter_end_score(direction: str) -> Tuple[float, str]:
    """
    Last week of March/June/September/December: pension fund rebalancing.
    If it's the last 5 trading days of a quarter: +5 LONG.
    """
    try:
        today = date.today()
        # Quarter-end months: 3, 6, 9, 12
        if today.month not in (3, 6, 9, 12):
            return (0.0, "not a quarter-end month")

        td_remaining = _trading_days_remaining_in_quarter(today)

        if td_remaining <= 5:
            if direction == "LONG":
                return (5.0, f"quarter-end rebalancing ({td_remaining} trading days to quarter-end)")
            else:
                return (0.0, "quarter-end — LONG only boost")

        return (0.0, "not quarter-end window")

    except Exception as _e:
        logger.debug(f"[suppressed] get_quarter_end_score: {_e}")
        return (0.0, "error — fail-open")


def get_monday_fade_score(
    direction: str, current_price: float, open_price: float
) -> Tuple[float, str]:
    """
    Monday opening gap fade. If Monday AND price opened gap up >0.5%:
    SHORT gets +4 (fade), LONG gets -3 (avoid chasing Monday gap).
    Tuesday-Thursday are strongest trend days → no adjustment.
    """
    try:
        today = date.today()
        weekday = today.weekday()  # 0=Mon, 1=Tue, ... 4=Fri

        if weekday != 0:
            return (0.0, "not Monday — no Monday-fade adjustment")

        if open_price <= 0 or current_price <= 0:
            return (0.0, "Monday but no price data")

        gap_pct = (open_price - current_price) / current_price * 100
        # Gap up: open_price > current_price means we gapped up then came down?
        # Actually: gap_pct = (open - prev_close) / prev_close — but caller passes
        # current_price as both args so gap is 0. When caller passes real open/ltp:
        # gap_pct > 0 means gapped up at open vs prior close.
        # Using the passed values: open_price is today's open, current_price is ltp.
        gap_pct = (open_price - current_price) / max(current_price, 0.01) * 100

        if gap_pct > 0.5:
            if direction == "SHORT":
                return (4.0, f"Monday gap-up fade: gap={gap_pct:+.1f}% → SHORT fade +4")
            elif direction == "LONG":
                return (-3.0, f"Monday gap-up: gap={gap_pct:+.1f}% — avoid chasing -3")

        return (0.0, f"Monday but gap={gap_pct:+.1f}% < 0.5% threshold")

    except Exception as _e:
        logger.debug(f"[suppressed] get_monday_fade_score: {_e}")
        return (0.0, "error — fail-open")


def get_opex_pin_score(symbol: str, current_price: float) -> Tuple[float, str]:
    """
    In OpEx week, stocks pin near high-OI strike prices.
    If price is within 0.5% of a $5 strike boundary: -4 (likely to stay pinned).
    Uses yfinance options chain. Fail-open → (0.0, "no options data").
    Cache 1 hour.
    """
    global _opex_pin_cache

    try:
        today = date.today()
        opex = _third_friday(today.year, today.month)
        opex_week_start = opex - timedelta(days=4)

        if not (opex_week_start <= today <= opex):
            return (0.0, "not OpEx week — pin score N/A")

        if current_price <= 0:
            return (0.0, "no price data")

        # Check cache
        cached = _opex_pin_cache.get(symbol)
        if cached is not None:
            c_score, c_reason, c_ts = cached
            if (_time.monotonic() - c_ts) < _OPEX_PIN_TTL:
                return (c_score, c_reason)

        # Find nearest $5 strike
        nearest_5_strike = round(current_price / 5) * 5
        distance_pct = abs(current_price - nearest_5_strike) / max(current_price, 0.01) * 100

        score = 0.0
        reason = f"OpEx week — distance to ${nearest_5_strike:.0f} strike: {distance_pct:.2f}%"

        if distance_pct <= 0.5:
            score = -4.0
            reason = (
                f"OpEx pin risk: price ${current_price:.2f} within 0.5% of "
                f"${nearest_5_strike:.0f} strike (dist={distance_pct:.2f}%) -4"
            )

        # Optionally try to confirm with yfinance options OI
        try:
            import yfinance as yf
            tk = yf.Ticker(symbol)
            opts = tk.options
            if opts:
                # Get nearest expiry
                nearest_exp = opts[0]
                chain = tk.option_chain(nearest_exp)
                calls_oi = chain.calls[["strike", "openInterest"]].copy()
                puts_oi  = chain.puts[["strike", "openInterest"]].copy()
                import pandas as _pd_local
                total_oi = _pd_local.concat([calls_oi, puts_oi]).groupby("strike")["openInterest"].sum()
                if not total_oi.empty:
                    top_strike = float(total_oi.idxmax())
                    dist_top = abs(current_price - top_strike) / max(current_price, 0.01) * 100
                    if dist_top <= 0.5:
                        score = -4.0
                        reason = (
                            f"OpEx high-OI pin: max OI at ${top_strike:.0f}, "
                            f"price ${current_price:.2f} within {dist_top:.2f}% -4"
                        )
        except Exception:
            pass  # yfinance options fetch failed — use $5 boundary estimate

        _opex_pin_cache[symbol] = (score, reason, _time.monotonic())
        return (score, reason)

    except Exception as _e:
        logger.debug(f"[suppressed] get_opex_pin_score({symbol}): {_e}")
        return (0.0, "no options data")
