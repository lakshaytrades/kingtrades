"""
phase_of_day_india.py — NSE Phase-of-Day Adaptive Engine
5 trading windows with distinct score/SL/size multipliers.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_PHASES = [
    # (name, start_time, end_time, score_boost, min_score_add, size_mult, sl_mult)
    ("ORB",            time(9,  15), time(9,  45), 0,  0,  0.85, 0.8),
    ("MORNING_DRIVE",  time(9,  45), time(11, 30), 0,  0,  1.00, 1.0),
    ("MIDDAY_CHOP",    time(11, 30), time(13, 30), 0,  8,  0.60, 1.0),
    ("AFTERNOON_TREND",time(13, 30), time(15,  0), 3,  0,  1.00, 1.0),
    ("POWER_CLOSE",    time(15,  0), time(15, 20), 8,  0,  0.90, 1.0),
]


def get_phase(now_ist: datetime) -> str:
    t = now_ist.time()
    for name, start, end, *_ in _PHASES:
        if start <= t < end:
            return name
    return "OUTSIDE_MARKET"


def get_multipliers(now_ist: datetime) -> dict:
    t = now_ist.time()
    for name, start, end, score_boost, min_score_add, size_mult, sl_mult in _PHASES:
        if start <= t < end:
            return {
                "phase":         name,
                "score_boost":   score_boost,
                "score_penalty": 0,
                "min_score_add": min_score_add,
                "size_mult":     size_mult,
                "sl_mult":       sl_mult,
            }
    return {
        "phase":         "OUTSIDE_MARKET",
        "score_boost":   0,
        "score_penalty": 0,
        "min_score_add": 0,
        "size_mult":     1.0,
        "sl_mult":       1.0,
    }


def is_orb_window(now_ist: datetime) -> bool:
    t = now_ist.time()
    return time(9, 15) <= t < time(9, 45)
