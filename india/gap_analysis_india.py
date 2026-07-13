"""
gap_analysis_india.py — NSE Gap Analysis & Gap-Fill Strategy
Detects pre-market gaps and scores signals based on gap type.
Gap-fill (0.8-2%): +15 fade score. Continuation (>2%): +12. Exhaustion (>3%): +10.
"""
import logging
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger("gap_analysis_india")
IST = ZoneInfo("Asia/Kolkata")

_FADE_WINDOW_END   = time(10, 0)   # gap-fill fades only before 10 AM
_GAP_EXPIRE        = time(11, 0)   # no gap scoring after 11 AM
_MIN_GAP_PCT       = 0.5           # gaps < 0.5% = flat open, ignore
_FADE_MAX_GAP_PCT  = 2.0           # gaps > 2% → continuation, not fade
_CONT_MIN_GAP_PCT  = 2.0
_EXHAUS_MIN_GAP_PCT = 3.0


class GapAnalysis:
    def compute_gap(self, symbol: str, df_5m: pd.DataFrame) -> dict:
        _empty = {"gap_pct": 0.0, "gap_type": None, "fill_price": 0.0, "gap_size": "NONE"}
        try:
            if df_5m is None or df_5m.empty or len(df_5m) < 5:
                return _empty

            today = datetime.now(IST).date()
            # today's bars
            today_bars = df_5m[df_5m.index.date == today]
            # yesterday's bars
            yest_bars  = df_5m[df_5m.index.date < today]

            if today_bars.empty or yest_bars.empty:
                return _empty

            yest_close = float(yest_bars["close"].iloc[-1])
            today_open = float(today_bars["open"].iloc[0])

            if yest_close <= 0:
                return _empty

            gap_pct = (today_open - yest_close) / yest_close * 100.0

            abs_gap = abs(gap_pct)
            if abs_gap < _MIN_GAP_PCT:
                return {"gap_pct": gap_pct, "gap_type": None, "fill_price": yest_close, "gap_size": "FLAT"}

            if abs_gap >= _EXHAUS_MIN_GAP_PCT:
                gap_type = "EXHAUSTION"
                gap_size = "LARGE"
            elif abs_gap >= _CONT_MIN_GAP_PCT:
                gap_type = "CONTINUATION"
                gap_size = "LARGE"
            elif abs_gap >= _MIN_GAP_PCT:
                gap_type = "FILL_FADE"
                gap_size = "MEDIUM"
            else:
                gap_type = None
                gap_size = "SMALL"

            return {"gap_pct": round(gap_pct, 3), "gap_type": gap_type,
                    "fill_price": round(yest_close, 2), "gap_size": gap_size}

        except Exception as e:
            logger.debug(f"GapAnalysis.compute_gap {symbol}: {e}")
            return _empty

    def score_signal(self, gap: dict, direction: str, now_ist: datetime) -> int:
        try:
            gap_type = gap.get("gap_type")
            gap_pct  = gap.get("gap_pct", 0.0)
            t        = now_ist.time()

            if gap_type is None or t >= _GAP_EXPIRE:
                return 0

            gap_up   = gap_pct > 0
            gap_down = gap_pct < 0

            if gap_type == "FILL_FADE":
                if t >= _FADE_WINDOW_END:
                    multiplier = 0.5   # halved after 10 AM
                else:
                    multiplier = 1.0
                if gap_up and direction == "SHORT":
                    return int(15 * multiplier)
                if gap_down and direction == "LONG":
                    return int(15 * multiplier)
                # Chasing the gap — dangerous
                if gap_up and direction == "LONG":
                    return -8
                if gap_down and direction == "SHORT":
                    return -8

            elif gap_type == "CONTINUATION":
                if gap_up and direction == "LONG":
                    return 12
                if gap_down and direction == "SHORT":
                    return 12
                return -5  # counter-gap

            elif gap_type == "EXHAUSTION":
                # Large gap exhaustion → fade
                if gap_up and direction == "SHORT":
                    return 10
                if gap_down and direction == "LONG":
                    return 10
                if gap_up and direction == "LONG":
                    return -6
                if gap_down and direction == "SHORT":
                    return -6

            return 0

        except Exception as e:
            logger.debug(f"GapAnalysis.score_signal: {e}")
            return 0
