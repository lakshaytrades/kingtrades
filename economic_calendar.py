"""
economic_calendar.py — US Momentum Alpaca AI Bot
US Economic Event Calendar + Trading-Impact Engine

Tracks ALL high-impact US economic releases and enforces trading
blackout windows around them. Protects capital from data-driven
volatility spikes that crush momentum setups.

18yr Rule: "One Fed meeting can undo 3 weeks of gains. Know the
calendar BEFORE you trade. Never be caught holding positions into
a surprise move."

Events tracked (2025-2026):
- FOMC meetings (8×/year) — BIGGEST market mover
- NFP (Non-Farm Payrolls) — first Friday of month, 8:30 AM ET
- CPI (Consumer Price Index) — mid-month, 8:30 AM ET
- PPI (Producer Price Index) — one day after CPI
- GDP (Advance Estimate) — quarterly, 8:30 AM ET
- Retail Sales — mid-month, 8:30 AM ET

Behavior:
  - Before high-impact release: BLOCK new entries (trading_ok=False)
  - After release + 15 min: ALLOW with bonus score (direction confirmed)
  - FOMC day pre-2PM: BLOCK all new entries
  - FOMC day post-2:30PM: ALLOW with score bonus

Score adjustments returned to signal_generator._compute_ai_score().
"""

import logging
from datetime import datetime, date, timedelta, time as dtime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# ─────────────────────────────────────────────────────────────────────────────
# HARD-CODED 2025-2026 US ECONOMIC CALENDAR
# ─────────────────────────────────────────────────────────────────────────────

FOMC_DATES: Dict[str, str] = {
    "2025-01-29": "FOMC Rate Decision",
    "2025-03-19": "FOMC Rate Decision",
    "2025-05-07": "FOMC Rate Decision",
    "2025-06-18": "FOMC Rate Decision",
    "2025-07-30": "FOMC Rate Decision",
    "2025-09-17": "FOMC Rate Decision",
    "2025-11-05": "FOMC Rate Decision",
    "2025-12-17": "FOMC Rate Decision",
    "2026-01-28": "FOMC Rate Decision",
    "2026-03-18": "FOMC Rate Decision",
    "2026-05-06": "FOMC Rate Decision",
    "2026-06-17": "FOMC Rate Decision",
    "2026-07-29": "FOMC Rate Decision",
    "2026-09-16": "FOMC Rate Decision",
    "2026-11-04": "FOMC Rate Decision",
    "2026-12-16": "FOMC Rate Decision",
}

NFP_DATES: Dict[str, str] = {
    "2025-01-10": "Non-Farm Payrolls",
    "2025-02-07": "Non-Farm Payrolls",
    "2025-03-07": "Non-Farm Payrolls",
    "2025-04-04": "Non-Farm Payrolls",
    "2025-05-02": "Non-Farm Payrolls",
    "2025-06-06": "Non-Farm Payrolls",
    "2025-07-03": "Non-Farm Payrolls",
    "2025-08-01": "Non-Farm Payrolls",
    "2025-09-05": "Non-Farm Payrolls",
    "2025-10-03": "Non-Farm Payrolls",
    "2025-11-07": "Non-Farm Payrolls",
    "2025-12-05": "Non-Farm Payrolls",
    "2026-01-09": "Non-Farm Payrolls",
    "2026-02-06": "Non-Farm Payrolls",
    "2026-03-06": "Non-Farm Payrolls",
    "2026-04-03": "Non-Farm Payrolls",
    "2026-05-01": "Non-Farm Payrolls",
    "2026-06-05": "Non-Farm Payrolls",
}

CPI_DATES: Dict[str, str] = {
    "2025-01-15": "CPI Report",
    "2025-02-12": "CPI Report",
    "2025-03-12": "CPI Report",
    "2025-04-10": "CPI Report",
    "2025-05-13": "CPI Report",
    "2025-06-11": "CPI Report",
    "2025-07-15": "CPI Report",
    "2025-08-12": "CPI Report",
    "2025-09-10": "CPI Report",
    "2025-10-15": "CPI Report",
    "2025-11-12": "CPI Report",
    "2025-12-10": "CPI Report",
    "2026-01-14": "CPI Report",
    "2026-02-11": "CPI Report",
    "2026-03-11": "CPI Report",
    "2026-04-09": "CPI Report",
    "2026-05-13": "CPI Report",
    "2026-06-10": "CPI Report",
}

PPI_DATES: Dict[str, str] = {
    "2025-01-14": "PPI Report",
    "2025-02-13": "PPI Report",
    "2025-03-13": "PPI Report",
    "2025-04-11": "PPI Report",
    "2025-05-15": "PPI Report",
    "2025-06-12": "PPI Report",
    "2025-07-16": "PPI Report",
    "2025-08-13": "PPI Report",
    "2025-09-11": "PPI Report",
    "2025-10-16": "PPI Report",
    "2025-11-13": "PPI Report",
    "2025-12-11": "PPI Report",
    "2026-01-15": "PPI Report",
    "2026-02-12": "PPI Report",
    "2026-03-12": "PPI Report",
    "2026-04-10": "PPI Report",
    "2026-05-14": "PPI Report",
    "2026-06-11": "PPI Report",
}

GDP_DATES: Dict[str, str] = {
    "2025-04-30": "GDP Advance Estimate Q1-2025",
    "2025-07-30": "GDP Advance Estimate Q2-2025",
    "2025-10-29": "GDP Advance Estimate Q3-2025",
    "2026-01-28": "GDP Advance Estimate Q4-2025",
    "2026-04-29": "GDP Advance Estimate Q1-2026",
    "2026-07-29": "GDP Advance Estimate Q2-2026",
}

RETAIL_SALES_DATES: Dict[str, str] = {
    "2025-01-16": "Retail Sales",
    "2025-02-14": "Retail Sales",
    "2025-03-17": "Retail Sales",
    "2025-04-16": "Retail Sales",
    "2025-05-15": "Retail Sales",
    "2025-06-17": "Retail Sales",
    "2025-07-17": "Retail Sales",
    "2025-08-15": "Retail Sales",
    "2025-09-16": "Retail Sales",
    "2025-10-17": "Retail Sales",
    "2025-11-14": "Retail Sales",
    "2025-12-16": "Retail Sales",
    "2026-01-15": "Retail Sales",
    "2026-02-13": "Retail Sales",
    "2026-03-16": "Retail Sales",
    "2026-04-15": "Retail Sales",
    "2026-05-15": "Retail Sales",
}

# Quad Witching (3rd Friday of March/June/Sep/Dec)
QUAD_WITCH_DATES: set = {
    "2025-03-21", "2025-06-20", "2025-09-19", "2025-12-19",
    "2026-03-20", "2026-06-19", "2026-09-18", "2026-12-18",
}

# FOMC minutes release dates (approximately 3 weeks after each FOMC)
FOMC_MINUTES_DATES: Dict[str, str] = {
    "2025-02-19": "FOMC Minutes",
    "2025-04-09": "FOMC Minutes",
    "2025-05-28": "FOMC Minutes",
    "2025-07-09": "FOMC Minutes",
    "2025-08-20": "FOMC Minutes",
    "2025-10-08": "FOMC Minutes",
    "2025-11-26": "FOMC Minutes",
    "2026-01-07": "FOMC Minutes",
}


class EconomicEvent:
    """A single economic event with trading impact rules."""

    def __init__(
        self,
        name: str,
        impact: str,            # "EXTREME", "HIGH", "MEDIUM", "LOW"
        release_time_et: str,   # "08:30" or "14:00" (24h format)
        pre_blackout_minutes: int,
        post_resume_minutes: int,
        pre_score_adj: float,
        post_score_adj: float,
    ):
        self.name = name
        self.impact = impact
        self.release_time_et = release_time_et
        self.pre_blackout_minutes = pre_blackout_minutes
        self.post_resume_minutes = post_resume_minutes
        self.pre_score_adj = pre_score_adj
        self.post_score_adj = post_score_adj

    def get_status(self, now_et: datetime) -> Tuple[str, float, bool]:
        """
        Returns (status, score_adj, trading_ok).
        status: "PRE_BLACKOUT" | "IN_RELEASE" | "POST_RESUME" | "CLEAR"
        """
        h, m = self.release_time_et.split(":")
        release_time = now_et.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        minutes_to = (release_time - now_et).total_seconds() / 60
        minutes_since = -minutes_to

        if 0 <= minutes_to <= self.pre_blackout_minutes:
            return "PRE_BLACKOUT", self.pre_score_adj, False
        if 0 < minutes_since <= 5:
            return "IN_RELEASE", self.pre_score_adj * 0.5, False
        if 5 < minutes_since <= self.post_resume_minutes:
            return "POST_RESUME", self.post_score_adj * 0.5, True
        if minutes_since > self.post_resume_minutes:
            return "POST_CLEAR", self.post_score_adj, True
        return "CLEAR", 0.0, True


# ─────────────────────────────────────────────────────────────────────────────
# EVENT DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────
EVENT_TEMPLATES: Dict[str, EconomicEvent] = {
    "FOMC": EconomicEvent(
        name="FOMC Rate Decision", impact="EXTREME",
        release_time_et="14:00",
        pre_blackout_minutes=240,   # block 9:30 AM - 2:00 PM (whole morning)
        post_resume_minutes=30,
        pre_score_adj=-20.0,
        post_score_adj=10.0,
    ),
    "NFP": EconomicEvent(
        name="Non-Farm Payrolls", impact="HIGH",
        release_time_et="08:30",
        pre_blackout_minutes=999,   # block from open until 9:45 ET (block until market digests)
        post_resume_minutes=15,
        pre_score_adj=-20.0,
        post_score_adj=12.0,
    ),
    "CPI": EconomicEvent(
        name="CPI Report", impact="HIGH",
        release_time_et="08:30",
        pre_blackout_minutes=999,   # block pre-market; allow after 9:30 AM
        post_resume_minutes=15,
        pre_score_adj=-18.0,
        post_score_adj=8.0,
    ),
    "PPI": EconomicEvent(
        name="PPI Report", impact="MEDIUM",
        release_time_et="08:30",
        pre_blackout_minutes=999,
        post_resume_minutes=10,
        pre_score_adj=-10.0,
        post_score_adj=5.0,
    ),
    "GDP": EconomicEvent(
        name="GDP Advance Estimate", impact="HIGH",
        release_time_et="08:30",
        pre_blackout_minutes=999,
        post_resume_minutes=20,
        pre_score_adj=-12.0,
        post_score_adj=8.0,
    ),
    "RETAIL_SALES": EconomicEvent(
        name="Retail Sales", impact="MEDIUM",
        release_time_et="08:30",
        pre_blackout_minutes=999,
        post_resume_minutes=10,
        pre_score_adj=-8.0,
        post_score_adj=4.0,
    ),
    "FOMC_MINUTES": EconomicEvent(
        name="FOMC Minutes", impact="MEDIUM",
        release_time_et="14:00",
        pre_blackout_minutes=60,
        post_resume_minutes=20,
        pre_score_adj=-10.0,
        post_score_adj=5.0,
    ),
}

# Map from date-string dict → event template key
DATE_TO_EVENT: List[Tuple[Dict[str, str], str]] = [
    (FOMC_DATES,          "FOMC"),
    (NFP_DATES,           "NFP"),
    (CPI_DATES,           "CPI"),
    (PPI_DATES,           "PPI"),
    (GDP_DATES,           "GDP"),
    (RETAIL_SALES_DATES,  "RETAIL_SALES"),
    (FOMC_MINUTES_DATES,  "FOMC_MINUTES"),
]


class EconomicCalendar:
    """
    Main calendar engine. Checks today's events and returns:
    - Score adjustment for the current moment
    - Whether new entries are allowed (trading_ok)
    - Today's events list for the morning brief
    """

    def __init__(self):
        self._today_events: Optional[List[Tuple[EconomicEvent, str]]] = None  # (event, date_str)
        self._last_check_date: Optional[date] = None

    def _load_today_events(self) -> List[Tuple[EconomicEvent, str]]:
        """Load all events for today from the hard-coded calendar."""
        today_str = str(get_current_ist_time().date())
        events = []
        for date_dict, template_key in DATE_TO_EVENT:
            if today_str in date_dict:
                event = EVENT_TEMPLATES.get(template_key)
                if event:
                    events.append((event, today_str))
        return events

    def get_today_events(self) -> List[EconomicEvent]:
        """Get all events for today."""
        today = get_current_ist_time().date()
        if self._last_check_date != today:
            self._today_events = self._load_today_events()
            self._last_check_date = today
        return [e for e, _ in (self._today_events or [])]

    def get_calendar_score_adjustment(self) -> Tuple[float, str, bool]:
        """
        Main entry point for signal_generator.
        Returns (score_adjustment, note, trading_ok).
        - score_adjustment: float (negative = caution, positive = post-release bonus)
        - note: human-readable reason
        - trading_ok: False = hard block on new entries
        """
        try:
            now_et = get_current_ist_time()
            today_str = str(now_et.date())
            events = self.get_today_events()

            if not events:
                # Check quad witch
                if today_str in QUAD_WITCH_DATES:
                    return -15.0, "QUAD_WITCH(extreme_noise)", True
                return 0.0, "", True

            # Evaluate all today's events and take the WORST (most restrictive)
            worst_score = 0.0
            worst_note  = ""
            trading_ok  = True

            for event in events:
                status, adj, ok = event.get_status(now_et)
                if not ok:
                    trading_ok = False
                if adj < worst_score:
                    worst_score = adj
                    worst_note  = f"{event.name}:{status}"
                elif adj > 0 and worst_score == 0:
                    worst_score = adj
                    worst_note  = f"{event.name}:{status}(post_release)"

            return worst_score, worst_note, trading_ok

        except Exception as e:
            logger.debug(f"EconomicCalendar.get_calendar_score_adjustment error: {e}")
            return 0.0, "", True

    def is_high_impact_window(self, buffer_minutes: int = 30) -> bool:
        """True if within buffer_minutes of a high-impact release."""
        _, _, trading_ok = self.get_calendar_score_adjustment()
        return not trading_ok

    def days_to_next_event(self) -> Tuple[str, int]:
        """(event_name, days_away) for the next upcoming event."""
        try:
            today = get_current_ist_time().date()
            nearest_date = None
            nearest_event = ""
            nearest_days = 999

            for date_dict, template_key in DATE_TO_EVENT:
                for date_str in sorted(date_dict.keys()):
                    d = date.fromisoformat(date_str)
                    if d >= today:
                        days = (d - today).days
                        if days < nearest_days:
                            nearest_days = days
                            nearest_date = date_str
                            nearest_event = date_dict[date_str]
                        break  # sorted, so first future date is nearest for this event type
            return nearest_event or "None", nearest_days
        except Exception:
            return "Unknown", 999

    def is_blackout_now(self) -> Tuple[bool, str]:
        """Returns (blackout_active, reason). Used by main.py trading cycle."""
        _, note, trading_ok = self.get_calendar_score_adjustment()
        if not trading_ok:
            return True, note or "High-impact event window — no new entries"
        return False, ""

    def is_fno_expiry_today(self) -> bool:
        """US bot stub — no F&O expiry concept in US markets."""
        return False

    def is_holiday_today(self) -> bool:
        """US bot stub — no holiday calendar hardcoded. Returns False."""
        return False

    def format_upcoming_events(self) -> str:
        """Next high-impact events for morning brief."""
        next_event, next_days = self.days_to_next_event()
        if next_days == 0:
            return f"📅 HIGH IMPACT TODAY: {next_event}"
        if next_days <= 3:
            return f"📅 Coming up: {next_event} in {next_days} day(s)"
        return f"📅 Next event: {next_event} in {next_days} day(s)"

    def format_telegram_brief(self) -> str:
        """Calendar section for morning Telegram message."""
        try:
            events = self.get_today_events()
            today_str = str(get_current_ist_time().date())
            is_quad = today_str in QUAD_WITCH_DATES

            lines = ["📅 ECONOMIC CALENDAR:"]

            if is_quad:
                lines.append("  ⚠️ QUAD WITCH DAY — extreme volume/volatility")

            if not events and not is_quad:
                lines.append("  ✅ No high-impact events today — clean tape")
            else:
                for event in events:
                    impact_emoji = {
                        "EXTREME": "🚨", "HIGH": "⚠️",
                        "MEDIUM": "📊", "LOW": "ℹ️",
                    }.get(event.impact, "📊")
                    lines.append(
                        f"  {impact_emoji} {event.name} @ {event.release_time_et} ET"
                        f" ({event.impact} impact)"
                    )
                lines.append("")
                lines.append("  ⏸️ New entries BLOCKED before release")
                lines.append("  ✅ Trading resumes after release + 15 min")

            next_event, next_days = self.days_to_next_event()
            if next_days > 0:
                lines.append(f"\n  📆 Next: {next_event} in {next_days} day(s)")

            return "\n".join(lines)
        except Exception as e:
            return f"⚠️ Calendar unavailable: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────
_cal_instance: Optional[EconomicCalendar] = None


def get_economic_calendar() -> EconomicCalendar:
    global _cal_instance
    if _cal_instance is None:
        _cal_instance = EconomicCalendar()
    return _cal_instance


# Alias expected by main.py
get_calendar = get_economic_calendar
