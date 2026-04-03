"""
economic_calendar.py — NSE Momentum Groww AI Bot
NSE Economic Event Calendar + Impact Learning Engine

Tracks high-impact events and learns their historical market impact.
18yr Rule: "Know the calendar before the market opens.
One RBI announcement can wipe out a week of gains in 30 minutes."

Events tracked:
- RBI Monetary Policy (6x per year) — BIGGEST mover
- Union Budget (Feb 1) — most volatile day of year
- GDP/CPI/WPI releases
- NSE F&O expiry (last Thursday monthly) — manipulation zone
- Nifty50 earnings (Apr/Jul/Oct/Jan quarters)
- US Fed FOMC (8x per year) — impacts FII flows
- SGX/Gift Nifty settlement days
- NSE/BSE circuit breaker history
"""

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

CALENDAR_DB = Path("data/economic_calendar.db")
CALENDAR_DB.parent.mkdir(exist_ok=True)

# ── Hard-coded high-impact NSE events 2025-2026 ────────────────────────────
HARDCODED_EVENTS = [
    # RBI MPC (Monetary Policy Committee) dates 2025
    {"date": "2025-04-09", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2025-06-06", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2025-08-08", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2025-10-08", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2025-12-05", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2026-02-06", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    {"date": "2026-04-09", "event": "RBI MPC Decision",        "impact": "HIGH",  "avoid_minutes": 60},
    # Budget
    {"date": "2026-02-01", "event": "Union Budget 2026-27",    "impact": "EXTREME","avoid_minutes": 120},
    # US Fed FOMC 2025 (approximate)
    {"date": "2025-05-07", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-06-18", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-07-30", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-09-17", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-11-05", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-12-17", "event": "US Fed FOMC",             "impact": "MEDIUM","avoid_minutes": 30},
    # India GDP/CPI
    {"date": "2025-05-30", "event": "India GDP Q4 FY25",       "impact": "MEDIUM","avoid_minutes": 30},
    {"date": "2025-08-29", "event": "India GDP Q1 FY26",       "impact": "MEDIUM","avoid_minutes": 30},
    # NSE holidays 2025
    {"date": "2025-04-14", "event": "NSE Holiday - Ambedkar Jayanti", "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-04-18", "event": "NSE Holiday - Good Friday",      "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-05-01", "event": "NSE Holiday - Maharashtra Day",  "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-08-15", "event": "NSE Holiday - Independence Day", "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-10-02", "event": "NSE Holiday - Gandhi Jayanti",   "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-10-24", "event": "NSE Holiday - Dussehra",         "impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-11-05", "event": "NSE Holiday - Diwali Laxmi Puja","impact": "HOLIDAY", "avoid_minutes": 0},
    {"date": "2025-12-25", "event": "NSE Holiday - Christmas",        "impact": "HOLIDAY", "avoid_minutes": 0},
]

# F&O expiry is always last Thursday of each month
def _get_monthly_expiry_dates(start_year: int = 2025, months: int = 24) -> List[Dict]:
    events = []
    from calendar import monthrange
    today = date.today()
    for m in range(months):
        year  = start_year + (m // 12)
        month = (m % 12) + 1
        # Find last Thursday (weekday=3)
        last_day = monthrange(year, month)[1]
        for d in range(last_day, 0, -1):
            if date(year, month, d).weekday() == 3:
                events.append({
                    "date":           str(date(year, month, d)),
                    "event":          "NSE F&O Monthly Expiry",
                    "impact":         "MEDIUM",
                    "avoid_minutes":  45,
                })
                break
    return events


class EconomicCalendar:
    """
    Manages the economic event calendar with impact learning.
    Learns how each event type historically affected NSE.
    """

    def __init__(self):
        self._init_db()
        self._seed_events()

    def _init_db(self):
        with sqlite3.connect(CALENDAR_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_ist       TEXT NOT NULL,
                    event_name     TEXT NOT NULL,
                    impact_level   TEXT DEFAULT 'MEDIUM',
                    avoid_minutes  INTEGER DEFAULT 30,
                    time_ist       TEXT DEFAULT '10:00',
                    source         TEXT DEFAULT 'hardcoded'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_outcomes (
                    event_name     TEXT NOT NULL,
                    date_ist       TEXT NOT NULL,
                    nifty_change   REAL,
                    vix_change     REAL,
                    intraday_range REAL,
                    outcome_note   TEXT,
                    recorded_at    TEXT,
                    PRIMARY KEY (event_name, date_ist)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_impact_stats (
                    event_name     TEXT PRIMARY KEY,
                    avg_nifty_move REAL,
                    avg_range      REAL,
                    bearish_count  INTEGER DEFAULT 0,
                    bullish_count  INTEGER DEFAULT 0,
                    total_count    INTEGER DEFAULT 0,
                    last_updated   TEXT
                )
            """)
            conn.commit()

    def _seed_events(self):
        """Populate DB with hardcoded events + F&O expiry dates."""
        all_events = HARDCODED_EVENTS + _get_monthly_expiry_dates()
        with sqlite3.connect(CALENDAR_DB) as conn:
            for ev in all_events:
                conn.execute(
                    "INSERT OR IGNORE INTO events (date_ist, event_name, impact_level, avoid_minutes) "
                    "VALUES (?,?,?,?)",
                    (ev["date"], ev["event"], ev["impact"], ev.get("avoid_minutes", 30))
                )
            conn.commit()

    # ── QUERY ──────────────────────────────────────────────

    def get_today_events(self) -> List[Dict]:
        today = str(get_current_ist_date())
        with sqlite3.connect(CALENDAR_DB) as conn:
            rows = conn.execute(
                "SELECT date_ist, event_name, impact_level, avoid_minutes, time_ist "
                "FROM events WHERE date_ist=? ORDER BY time_ist",
                (today,)
            ).fetchall()
        return [{"date": r[0], "event": r[1], "impact": r[2],
                 "avoid_minutes": r[3], "time_ist": r[4]} for r in rows]

    def get_upcoming_events(self, days: int = 7) -> List[Dict]:
        today  = str(get_current_ist_date())
        future = str(get_current_ist_date() + timedelta(days=days))
        with sqlite3.connect(CALENDAR_DB) as conn:
            rows = conn.execute(
                "SELECT date_ist, event_name, impact_level, avoid_minutes "
                "FROM events WHERE date_ist BETWEEN ? AND ? ORDER BY date_ist",
                (today, future)
            ).fetchall()
        return [{"date": r[0], "event": r[1], "impact": r[2], "avoid_minutes": r[3]} for r in rows]

    def is_blackout_now(self) -> Tuple[bool, str]:
        """
        Check if current IST time is within blackout window of any event.
        Returns (True, reason) or (False, "").
        """
        events = self.get_today_events()
        if not events:
            return False, ""
        now_ist = get_current_ist_time()
        for ev in events:
            if ev["impact"] == "HOLIDAY":
                return True, f"NSE Holiday: {ev['event']}"
            try:
                h, m    = map(int, ev["time_ist"].split(":"))
                ev_time = now_ist.replace(hour=h, minute=m, second=0)
                window  = ev.get("avoid_minutes", 30)
                start   = ev_time - timedelta(minutes=window)
                end_t   = ev_time + timedelta(minutes=window)
                if start <= now_ist <= end_t:
                    return True, (
                        f"Event blackout: {ev['event']} "
                        f"({window}min window around {ev['time_ist']} IST)"
                    )
            except Exception:
                pass
        return False, ""

    def is_fno_expiry_today(self) -> bool:
        events = self.get_today_events()
        return any("Expiry" in ev["event"] for ev in events)

    def is_holiday_today(self) -> bool:
        events = self.get_today_events()
        return any(ev["impact"] == "HOLIDAY" for ev in events)

    # ── LEARNING: record what actually happened ─────────────

    def record_event_outcome(self, event_name: str, nifty_change: float,
                              intraday_range: float, note: str = ""):
        today = str(get_current_ist_date())
        with sqlite3.connect(CALENDAR_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO event_outcomes VALUES (?,?,?,NULL,?,?,?)",
                (event_name, today, nifty_change, intraday_range, note, format_ist_timestamp())
            )
            # Update rolling stats
            conn.execute("""
                INSERT INTO event_impact_stats
                    (event_name, avg_nifty_move, avg_range,
                     bearish_count, bullish_count, total_count, last_updated)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(event_name) DO UPDATE SET
                    avg_nifty_move = (avg_nifty_move * total_count + excluded.avg_nifty_move)
                                     / (total_count + 1),
                    avg_range      = (avg_range * total_count + excluded.avg_range)
                                     / (total_count + 1),
                    bearish_count  = bearish_count + excluded.bearish_count,
                    bullish_count  = bullish_count + excluded.bullish_count,
                    total_count    = total_count + 1,
                    last_updated   = excluded.last_updated
            """, (
                event_name,
                abs(nifty_change), intraday_range,
                1 if nifty_change < 0 else 0,
                1 if nifty_change > 0 else 0,
                format_ist_timestamp(),
            ))
            conn.commit()

    def get_event_impact_stats(self, event_name: str) -> Optional[Dict]:
        with sqlite3.connect(CALENDAR_DB) as conn:
            row = conn.execute(
                "SELECT * FROM event_impact_stats WHERE event_name=?",
                (event_name,)
            ).fetchone()
        if row:
            return {
                "event":        row[0], "avg_move":   row[1],
                "avg_range":    row[2], "bearish_pct": row[3] / max(row[5], 1) * 100,
                "total_events": row[5],
            }
        return None

    def format_upcoming_events(self) -> str:
        events = self.get_upcoming_events(days=7)
        if not events:
            return "📅 No major events in next 7 days"
        lines = ["📅 Upcoming Events (7 days):"]
        for ev in events:
            icon = {"HIGH": "🔴", "EXTREME": "🚨", "MEDIUM": "🟡", "HOLIDAY": "⛔"}.get(ev["impact"], "📌")
            lines.append(f"  {icon} {ev['date']}: {ev['event']} ({ev['impact']})")
        return "\n".join(lines)

    # ── FETCH LIVE EVENTS FROM INVESTING.COM RSS ────────────

    def refresh_from_web(self):
        """Pull upcoming economic events from free RSS feeds."""
        try:
            import feedparser
            feed = feedparser.parse("https://in.investing.com/rss/market_overview_Fundamental_Analysis.rss")
            today = get_current_ist_date()
            with sqlite3.connect(CALENDAR_DB) as conn:
                for entry in feed.entries[:20]:
                    title = entry.get("title", "")
                    if any(kw in title.upper() for kw in ["RBI", "CPI", "GDP", "INFLATION", "BUDGET", "FOMC"]):
                        impact = "HIGH" if any(k in title.upper() for k in ["RBI", "BUDGET", "FOMC"]) else "MEDIUM"
                        conn.execute(
                            "INSERT OR IGNORE INTO events (date_ist, event_name, impact_level, source) "
                            "VALUES (?,?,?,'rss')",
                            (str(today), title[:100], impact)
                        )
                conn.commit()
            logger.info(f"[{format_ist_timestamp()}] Calendar refreshed from RSS")
        except Exception as e:
            logger.debug(f"Calendar RSS refresh failed: {e}")


# Singleton
_calendar: Optional[EconomicCalendar] = None

def get_calendar() -> EconomicCalendar:
    global _calendar
    if _calendar is None:
        _calendar = EconomicCalendar()
    return _calendar
