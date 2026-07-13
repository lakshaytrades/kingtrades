"""
macro_india.py — Macro Economic Scoring Engine for NSE.
Hard-coded Indian economic calendar (RBI MPC, PMI, CPI, IIP).
Blackout window: 30 min before → 45 min after event.
Post-event scoring: PMI>52 → BULL_MACRO (+4 LONG for 2 hrs).
FII MTD net > +5000 Cr → sustained bull → +3.
"""
import logging
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("macro_india")

# ------------------------------------------------------------------ #
# Economic calendar — key 2026 dates (extend as needed)
# Format: (date_str, event_name, time_ist, expected_bias)
# ------------------------------------------------------------------ #
_CALENDAR = [
    # RBI MPC decisions (bi-monthly)
    ("2026-02-07", "RBI_MPC",   time(10, 0), "NEUTRAL"),
    ("2026-04-09", "RBI_MPC",   time(10, 0), "NEUTRAL"),
    ("2026-06-06", "RBI_MPC",   time(10, 0), "NEUTRAL"),
    ("2026-08-06", "RBI_MPC",   time(10, 0), "NEUTRAL"),
    ("2026-10-08", "RBI_MPC",   time(10, 0), "NEUTRAL"),
    ("2026-12-05", "RBI_MPC",   time(10, 0), "NEUTRAL"),
    # PMI Manufacturing (last Wed of month, 10 AM)
    ("2026-01-21", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-02-18", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-03-25", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-04-22", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-05-20", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-06-24", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-07-22", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-08-26", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-09-23", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-10-21", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-11-25", "PMI_MFG",   time(10, 0), "BULL"),
    ("2026-12-23", "PMI_MFG",   time(10, 0), "BULL"),
    # CPI inflation (second Wed of month, 17:30 IST — released after market)
    ("2026-01-14", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-02-11", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-03-11", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-04-15", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-05-13", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-06-10", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-07-15", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-08-12", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-09-09", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-10-14", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-11-11", "CPI",       time(17, 30), "NEUTRAL"),
    ("2026-12-09", "CPI",       time(17, 30), "NEUTRAL"),
    # IIP (second Friday of month, 17:30)
    ("2026-01-09", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-02-13", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-03-13", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-04-10", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-05-08", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-06-12", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-07-10", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-08-14", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-09-11", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-10-09", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-11-13", "IIP",       time(17, 30), "NEUTRAL"),
    ("2026-12-11", "IIP",       time(17, 30), "NEUTRAL"),
    # Budget day
    ("2026-02-01", "UNION_BUDGET", time(11, 0), "NEUTRAL"),
]

_BLACKOUT_BEFORE = timedelta(minutes=30)
_BLACKOUT_AFTER  = timedelta(minutes=45)
_BULL_WINDOW     = timedelta(hours=2)

# Module-level state
_macro_bias: str = "NEUTRAL"    # "BULL_MACRO" / "BEAR_MACRO" / "NEUTRAL"
_bias_until: Optional[datetime] = None


class MacroScoring:
    def is_blackout(self, now_ist: Optional[datetime] = None) -> tuple[bool, str]:
        """
        Returns (True, event_name) if inside blackout window, else (False, "").
        """
        if now_ist is None:
            now_ist = datetime.now(IST)
        today = now_ist.date()
        for date_str, name, evt_time, _ in _CALENDAR:
            evt_date = date.fromisoformat(date_str)
            if evt_date != today:
                continue
            evt_dt = datetime.combine(evt_date, evt_time, tzinfo=IST)
            if (evt_dt - _BLACKOUT_BEFORE) <= now_ist <= (evt_dt + _BLACKOUT_AFTER):
                return True, name
        return False, ""

    def record_event_result(self, event_name: str, value: float) -> None:
        """
        Call after event release with the actual print.
        PMI > 52 → BULL_MACRO for 2 hrs. PMI < 48 → BEAR_MACRO.
        """
        global _macro_bias, _bias_until
        now = datetime.now(IST)
        if event_name == "PMI_MFG":
            if value > 52:
                _macro_bias = "BULL_MACRO"
                _bias_until = now + _BULL_WINDOW
            elif value < 48:
                _macro_bias = "BEAR_MACRO"
                _bias_until = now + _BULL_WINDOW
        # CPI, IIP — if surprise, set macro bias for 1 hr
        elif event_name in ("CPI", "IIP"):
            _bias_until = now + timedelta(hours=1)
            _macro_bias = "BULL_MACRO" if value > 0 else "BEAR_MACRO"

    def get_macro_bias(self, now_ist: Optional[datetime] = None) -> str:
        """Returns current macro bias; resets to NEUTRAL if window expired."""
        global _macro_bias, _bias_until
        if now_ist is None:
            now_ist = datetime.now(IST)
        if _bias_until and now_ist > _bias_until:
            _macro_bias = "NEUTRAL"
            _bias_until = None
        return _macro_bias

    def score_signal(
        self,
        direction: str,
        fii_mtd_cr: float = 0.0,
        now_ist: Optional[datetime] = None,
    ) -> int:
        """
        Returns score adjustment based on macro environment.
        fii_mtd_cr: month-to-date FII net flow in Crores (positive = buying).
        """
        if now_ist is None:
            now_ist = datetime.now(IST)

        blackout, _ = self.is_blackout(now_ist)
        if blackout:
            return -20  # strong penalty — avoid event risk

        bias = self.get_macro_bias(now_ist)
        score = 0

        if bias == "BULL_MACRO":
            if direction == "LONG":
                score += 4
            elif direction == "SHORT":
                score -= 3
        elif bias == "BEAR_MACRO":
            if direction == "SHORT":
                score += 4
            elif direction == "LONG":
                score -= 3

        if fii_mtd_cr > 5000 and direction == "LONG":
            score += 3
        elif fii_mtd_cr < -5000 and direction == "SHORT":
            score += 3

        return score

    def get_today_events(self, now_ist: Optional[datetime] = None) -> list[dict]:
        """Return list of today's economic events with times."""
        if now_ist is None:
            now_ist = datetime.now(IST)
        today = now_ist.date()
        events = []
        for date_str, name, evt_time, bias in _CALENDAR:
            if date.fromisoformat(date_str) == today:
                events.append({
                    "name": name,
                    "time": evt_time.strftime("%H:%M"),
                    "expected_bias": bias,
                })
        return events
