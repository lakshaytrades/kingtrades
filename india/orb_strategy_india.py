"""
orb_strategy_india.py — Opening Range Breakout for NSE India
India specialist: ORB is THE most reliable NSE intraday signal.

Research finding (IntraDay Lab, 2022–2026, 2,122 trades):
  - 5-min ORB (9:15-9:20 AM) with relative volume ≥ 200%: 70% WR
  - 15-min ORB (9:15-9:30 AM) without volume filter: 48.7% WR
  - KEY: The volume gate (≥2x avg) is what creates the 70% WR edge

Dual-window strategy:
  - Primary ORB:   9:15–9:30 AM (15-min range, ±18 pts)
  - Precision ORB: 9:15–9:20 AM (5-min range, ±22 pts when vol ≥2x)
  - Both windows active simultaneously; highest score used
  - Fake breakout guard: current 5m bar must CLOSE above/below range
"""
import logging
from datetime import datetime, time
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger("orb_india")
IST = ZoneInfo("Asia/Kolkata")

_ORB_START        = time(9, 15)
_ORB_END_5M       = time(9, 20)   # 5-min precision ORB window (highest WR)
_ORB_END_15M      = time(9, 30)   # 15-min standard ORB window
_ORB_VALID_UNTIL  = time(13, 0)   # ORB signals valid until 1 PM IST
_ORB_FADE_VALID_UNTIL = time(11, 0)  # Failed ORB signals only valid in first 1.5 hours

# Volume thresholds (research-validated)
_VOL_SURGE_STRONG  = 2.0   # ≥200% relative volume → "strong" breakout (70% WR)
_VOL_SURGE_WEAK    = 1.5   # ≥150% relative volume → "moderate" breakout

# Cache: symbol → {high_15m, low_15m, high_5m, low_5m, avg_volume}
_orb_cache: Dict[str, dict] = {}
_orb_date: Optional[str] = None


def _get_today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def build_orb(symbol: str, df_5m: pd.DataFrame) -> Optional[dict]:
    """
    Extract dual opening range (5-min and 15-min) high/low from today's 5m bars.
    Caches result for the trading session.
    Returns dict with both windows, or None if no ORB bars available.
    """
    global _orb_date
    today = _get_today()

    if _orb_date != today:
        _orb_cache.clear()
        _orb_date = today

    if symbol in _orb_cache:
        return _orb_cache[symbol]

    if df_5m is None or df_5m.empty:
        return None

    try:
        # 5-min precision ORB: only the 9:15 bar
        bars_5m = df_5m[
            (df_5m.index.time >= _ORB_START) &
            (df_5m.index.time < _ORB_END_5M)
        ]
        # 15-min standard ORB: first 3 bars (9:15, 9:20, 9:25)
        bars_15m = df_5m[
            (df_5m.index.time >= _ORB_START) &
            (df_5m.index.time < _ORB_END_15M)
        ]

        if bars_15m.empty:
            return None

        # 15-min range
        high_15m = float(bars_15m["high"].max())
        low_15m  = float(bars_15m["low"].min())
        range_15m = high_15m - low_15m
        mid_15m   = (high_15m + low_15m) / 2.0 if (high_15m + low_15m) > 0 else 1.0
        range_pct_15m = (range_15m / mid_15m) * 100.0 if mid_15m > 0 else 0.0

        if range_pct_15m < 0.3:
            logger.debug(f"ORB {symbol}: 15m range too tight ({range_pct_15m:.2f}%) — skipping")
            return None

        # 5-min range (may not be available yet)
        high_5m = float(bars_5m["high"].max()) if not bars_5m.empty else high_15m
        low_5m  = float(bars_5m["low"].min())  if not bars_5m.empty else low_15m
        range_pct_5m = ((high_5m - low_5m) / mid_15m * 100.0) if mid_15m > 0 else 0.0

        # 20-bar rolling average volume as baseline
        avg_vol = float(df_5m["volume"].rolling(20).mean().iloc[-1])

        orb = {
            "high_15m":      high_15m,
            "low_15m":       low_15m,
            "high_5m":       high_5m,
            "low_5m":        low_5m,
            "avg_volume":    avg_vol,
            "volatile_open": range_pct_15m > 3.0,
            "range_pct":     range_pct_15m,
            "range_pct_5m":  range_pct_5m,
            "has_5m":        not bars_5m.empty,
        }
        _orb_cache[symbol] = orb
        logger.debug(
            f"ORB {symbol}: 15m H/L={high_15m:.2f}/{low_15m:.2f} ({range_pct_15m:.2f}%) | "
            f"5m H/L={high_5m:.2f}/{low_5m:.2f}"
        )
        return orb
    except Exception as e:
        logger.debug(f"ORB build {symbol}: {e}")
        return None


def get_orb_score(symbol: str, direction: str, current_price: float,
                  df_5m: pd.DataFrame) -> Tuple[float, str]:
    """
    Opening Range Breakout signal for NSE India.
    Only active 9:30 AM – 1:00 PM IST (breakouts valid in first half of session).
    Returns (score_delta, reason). Fail-open: returns (0.0, "").
    """
    try:
        now_t = datetime.now(IST).time()

        # Only score after ORB is built and before 1 PM
        if now_t < _ORB_END or now_t > _ORB_VALID_UNTIL:
            return (0.0, "")

        fade_window = now_t <= _ORB_FADE_VALID_UNTIL

        orb = build_orb(symbol, df_5m)
        if orb is None:
            return (0.0, "")

        high_15m = orb["high_15m"]
        low_15m  = orb["low_15m"]
        high_5m  = orb["high_5m"]
        low_5m   = orb["low_5m"]
        avg_vol  = orb["avg_volume"]
        has_5m   = orb["has_5m"]

        # Volume surge check (research: ≥2x is the key threshold for 70% WR)
        cur_vol = float(df_5m["volume"].iloc[-1]) if not df_5m.empty else 0
        vol_strong = cur_vol >= avg_vol * _VOL_SURGE_STRONG if avg_vol > 0 else False
        vol_moderate = cur_vol >= avg_vol * _VOL_SURGE_WEAK if avg_vol > 0 else False

        orb_range_15m = high_15m - low_15m
        if orb_range_15m <= 0:
            return (0.0, "")

        best_score = 0.0
        best_reason = ""

        # ── Failed ORB reverse signal (only valid in first 1.5 hours) ────────
        if fade_window:
            if direction == "SHORT" and current_price < high_15m:
                recent_highs = df_5m["high"].iloc[-3:].values if len(df_5m) >= 3 else []
                if any(h > high_15m for h in recent_highs) and current_price < high_15m:
                    return (14.0, f"ORB_FAILED_BREAKOUT_NSE short snapped below {high_15m:.2f}")

            if direction == "LONG" and current_price > low_15m:
                recent_lows = df_5m["low"].iloc[-3:].values if len(df_5m) >= 3 else []
                if any(l < low_15m for l in recent_lows) and current_price > low_15m:
                    return (14.0, f"ORB_FAILED_BREAKDOWN_NSE long snapped above {low_15m:.2f}")

        # ── Precision 5-min ORB (highest WR: 70% when vol ≥2x) ──────────────
        if has_5m and orb["range_pct_5m"] >= 0.3:
            orb_range_5m = high_5m - low_5m
            if orb_range_5m > 0:
                if direction == "LONG" and current_price > high_5m:
                    ext = (current_price - high_5m) / orb_range_5m
                    if ext <= 0.30:
                        s = 22.0 if vol_strong else (16.0 if vol_moderate else 8.0)
                        best_score = max(best_score, s)
                        best_reason = f"ORB5_LONG H={high_5m:.2f} vol_ratio={cur_vol/(avg_vol+1):.1f}x"
                elif direction == "SHORT" and current_price < low_5m:
                    ext = (low_5m - current_price) / orb_range_5m
                    if ext <= 0.30:
                        s = 22.0 if vol_strong else (16.0 if vol_moderate else 8.0)
                        best_score = max(best_score, s)
                        best_reason = f"ORB5_SHORT L={low_5m:.2f} vol_ratio={cur_vol/(avg_vol+1):.1f}x"

        # ── Standard 15-min ORB ───────────────────────────────────────────────
        if direction == "LONG" and current_price > high_15m:
            ext = (current_price - high_15m) / orb_range_15m
            if ext <= 0.25:
                s = 18.0 if vol_strong else (12.0 if vol_moderate else 6.0)
                if s > best_score:
                    best_score = s
                    best_reason = f"ORB_LONG_NSE H={high_15m:.2f} vol_ratio={cur_vol/(avg_vol+1):.1f}x"

        elif direction == "SHORT" and current_price < low_15m:
            ext = (low_15m - current_price) / orb_range_15m
            if ext <= 0.25:
                s = 18.0 if vol_strong else (12.0 if vol_moderate else 6.0)
                if s > best_score:
                    best_score = s
                    best_reason = f"ORB_SHORT_NSE L={low_15m:.2f} vol_ratio={cur_vol/(avg_vol+1):.1f}x"

        if best_score > 0:
            return (best_score, best_reason)

        # ── ORB retest entry (buy the dip back to ORB high) ──────────────────
        if direction == "LONG":
            dist_from_high = (current_price - high_15m) / (orb_range_15m + 1e-9)
            if -0.05 <= dist_from_high <= 0.10:  # within 10% of ORB high
                return (10.0, f"ORB_RETEST_LONG at {current_price:.2f} near H={high_15m:.2f}")

        # Price inside range — no ORB signal
        return (0.0, "")

    except Exception as e:
        logger.debug(f"orb_score {symbol}: {e}")
        return (0.0, "")
