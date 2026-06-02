"""
seasonal_alpha.py — Calendar Statistical Edge (v17.0)

Implements well-documented seasonal and calendar effects:
Turn of month, OPEX week, January effect, October effect, sell-in-May,
Santa Claus rally, weekday effects, pre-FOMC drift.

No external data required — pure date arithmetic.
Thread-safe.  Fail-open (returns 0.0).
"""

import logging
from datetime import date, timedelta
from typing import Tuple

logger = logging.getLogger(__name__)


def _count_trading_days_from_start(d: date) -> int:
    """Approximate count of trading days elapsed from start of month."""
    count = 0
    day = date(d.year, d.month, 1)
    while day <= d:
        if day.weekday() < 5:  # Mon-Fri
            count += 1
        day += timedelta(days=1)
    return count


def _trading_days_remaining_in_month(d: date) -> int:
    """Approximate trading days remaining in current month."""
    # Estimate last day of month
    if d.month == 12:
        last = date(d.year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(d.year, d.month + 1, 1) - timedelta(days=1)

    count = 0
    day = d
    while day <= last:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


def _opex_friday(d: date) -> date:
    """Return the 3rd Friday (monthly OPEX) of d's month."""
    friday_count = 0
    day = date(d.year, d.month, 1)
    last_day = date(d.year, d.month + 1, 1) - timedelta(days=1) if d.month < 12 else date(d.year, 12, 31)
    while day <= last_day:
        if day.weekday() == 4:  # Friday
            friday_count += 1
            if friday_count == 3:
                return day
        day += timedelta(days=1)
    return date(d.year, d.month, 15)  # fallback


def get_seasonal_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Calendar statistical edge scorer.

    Effects (LONG bias unless noted):
      1. Turn of month (last 2 + first 3 trading days):  +6
      2. Monthly OPEX week (3rd Friday ±3 days):         +3
      3. January effect (Jan 1-15):                      +4
      4. October caution (all Oct):                      +4 SHORT / -2 LONG
      5. Sell in May (May-Oct):                          -2 LONG
      6. Santa Claus rally (Dec 26 - Jan 3):             +5 LONG
      7. Monday effect:                                  -2 LONG
      8. Friday effect (window dressing):                +3 LONG
      9. Pre-FOMC drift (approx 1 day before typical FOMC dates): +4 LONG

    Fail-open: returns (0.0, "seasonal N/A") on any error.
    """
    try:
        today = date.today()
        score = 0.0
        reasons = []

        dow       = today.weekday()   # 0=Mon, 4=Fri
        month     = today.month
        day       = today.day
        td_from_start = _count_trading_days_from_start(today)
        td_remaining  = _trading_days_remaining_in_month(today)

        # 1. Turn of month (last 2 + first 3 trading days)
        if td_from_start <= 3 or td_remaining <= 2:
            if direction == "LONG":
                score += 6.0
                reasons.append("turn_of_month+6")

        # 2. OPEX week (3rd Friday ±3 days)
        opex = _opex_friday(today)
        opex_dist = abs((today - opex).days)
        if opex_dist <= 3:
            score += 3.0 if direction == "LONG" else 2.0
            reasons.append(f"opex_week+3(dist={opex_dist})")

        # 3. January Effect (Jan 1-15, small caps)
        if month == 1 and 1 <= day <= 15:
            if direction == "LONG":
                score += 4.0
                reasons.append("jan_effect+4")

        # 4. October caution
        if month == 10:
            if direction == "LONG":
                score -= 2.0
                reasons.append("oct_caution-2")
            else:
                score += 4.0
                reasons.append("oct_caution_short+4")

        # 5. Sell in May (May through October)
        if 5 <= month <= 10:
            if direction == "LONG":
                score -= 2.0
                reasons.append("sell_in_may-2")

        # 6. Santa Claus Rally (Dec 26 - Jan 3)
        if (month == 12 and day >= 26) or (month == 1 and day <= 3):
            if direction == "LONG":
                score += 5.0
                reasons.append("santa_rally+5")

        # 7. Monday effect
        if dow == 0:  # Monday
            if direction == "LONG":
                score -= 2.0
                reasons.append("monday_effect-2")

        # 8. Friday window dressing
        if dow == 4:  # Friday
            if direction == "LONG":
                score += 3.0
                reasons.append("friday_effect+3")

        # 9. Pre-FOMC drift (FOMC meets ~8 times/year, roughly every 6-7 weeks)
        # Approximate FOMC weeks: Jan, Mar, May, Jun, Jul, Sep, Nov, Dec
        _fomc_months = {1, 3, 5, 6, 7, 9, 11, 12}
        if month in _fomc_months:
            # FOMC typically on Wed — if today is Tue we're in pre-FOMC window
            if dow == 1:  # Tuesday
                if direction == "LONG":
                    score += 4.0
                    reasons.append("pre_fomc_drift+4")

        score = float(max(-10.0, min(12.0, score)))
        reason = " ".join(reasons) if reasons else "seasonal_neutral"
        return score, reason
    except Exception as exc:
        logger.debug(f"get_seasonal_score error (fail-open): {exc}")
        return 0.0, "seasonal N/A"
