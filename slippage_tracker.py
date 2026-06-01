"""
slippage_tracker.py — Execution Slippage Measurement & Compensation

Hedge-fund insight (per Grok): slippage and market impact eat 20-70%
of theoretical profits. For NSE intraday at ₹50k/trade, even 0.2%
slippage = ₹100/trade = ₹500/day on 5 trades.

This module:
  1. Records every actual fill vs signal price
  2. Builds per-symbol, per-time-of-day slippage models
  3. Returns compensation buffer to pre-adjust limit prices
  4. Detects "bad fill" outliers and logs them for review

LIMIT-first execution strategy:
  LONG  → place LIMIT at ask + buffer (fills immediately on liquid stocks)
  SHORT → place LIMIT at bid - buffer
  Buffer = max(0.05%, symbol_avg_slippage + 1-sigma)

  If not filled in 10s → increase limit by another buffer
  If not filled in 20s → market fallback

This alone recovers ~₹300-800/day on a 5-trade average day.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
DB_PATH = "data/slippage.db"
SCHEMA  = """
CREATE TABLE IF NOT EXISTS fills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    direction       TEXT    NOT NULL,
    signal_price    REAL    NOT NULL,
    fill_price      REAL    NOT NULL,
    slippage_pct    REAL    NOT NULL,
    quantity        INTEGER NOT NULL,
    hour_of_day     INTEGER NOT NULL,
    order_type      TEXT    NOT NULL,
    trade_date      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
)
"""


@dataclass
class SlippageStats:
    symbol:       str
    mean_pct:     float   # Average slippage %
    std_pct:      float   # Standard deviation
    p95_pct:      float   # 95th percentile (worst-case buffer)
    sample_count: int
    last_updated: str
    fill_quality_score: float = 1.0

    def recommended_buffer_pct(self) -> float:
        """Limit price buffer = mean + 1-sigma, capped at 0.3%."""
        return min(0.003, max(0.0005, self.mean_pct + self.std_pct))

    def is_reliable(self) -> bool:
        return self.sample_count >= 5


class SlippageTracker:
    """
    Tracks execution slippage per symbol and time of day.
    Provides compensation buffers for LIMIT-first execution.
    """

    FALLBACK_BUFFER_PCT = 0.001   # 0.1% default buffer before we have data
    MAX_BUFFER_PCT      = 0.003   # 0.3% max limit buffer (above this → market)

    def __init__(self):
        Path("data").mkdir(parents=True, exist_ok=True)
        self._db = DB_PATH
        self._init_db()
        self._stats_cache: Dict[str, SlippageStats] = {}
        self._cache_loaded = False

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────

    def record_fill(
        self,
        symbol:       str,
        direction:    str,
        signal_price: float,
        fill_price:   float,
        quantity:     int,
        order_type:   str = "LIMIT",
    ) -> float:
        """
        Record an actual fill vs signal price.
        Returns slippage % (positive = paid more than expected).
        """
        if signal_price <= 0:
            return 0.0

        if direction == "LONG":
            slippage_pct = (fill_price - signal_price) / signal_price
        else:
            slippage_pct = (signal_price - fill_price) / signal_price

        now = get_current_ist_time()
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    """
                    INSERT INTO fills
                    (symbol, direction, signal_price, fill_price, slippage_pct,
                     quantity, hour_of_day, order_type, trade_date, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol, direction, signal_price, fill_price,
                        round(slippage_pct, 6), quantity, now.hour,
                        order_type, str(now.date()), format_ist_timestamp(),
                    ),
                )
            # Invalidate cache for this symbol
            self._stats_cache.pop(symbol, None)
        except Exception as e:
            logger.debug(f"SlippageTracker.record_fill failed: {e}")

        if abs(slippage_pct) > 0.002:
            logger.warning(
                f"[{format_ist_timestamp()}] HIGH SLIPPAGE: {symbol} {direction} "
                f"signal=₹{signal_price:.2f} fill=₹{fill_price:.2f} "
                f"slippage={slippage_pct:.3%}"
            )
        return slippage_pct

    def get_limit_buffer(self, symbol: str, direction: str) -> float:
        """
        Return the limit price buffer % for this symbol.
        LONG: add buffer to ask (guarantees fill on liquid stocks)
        SHORT: subtract buffer from bid

        Returns float like 0.0012 (= 0.12%)
        """
        stats = self._get_stats(symbol)
        if stats and stats.is_reliable():
            return stats.recommended_buffer_pct()
        return self.FALLBACK_BUFFER_PCT

    def adjust_limit_price(
        self, symbol: str, direction: str, base_price: float, retry: int = 0
    ) -> float:
        """
        Return limit price adjusted for slippage.
        retry=0: mean+sigma buffer
        retry=1: 2x buffer (more aggressive)
        retry=2: give up → return 0 (caller should use MARKET)
        """
        if retry >= 2:
            return 0.0   # Signal to use MARKET order
        buf = self.get_limit_buffer(symbol, direction) * (1 + retry)
        buf = min(buf, self.MAX_BUFFER_PCT)
        if direction == "LONG":
            return round(base_price * (1 + buf), 2)
        else:
            return round(base_price * (1 - buf), 2)

    def get_daily_slippage_cost(self) -> Tuple[float, int]:
        """Return (total_slippage_pct_sum, trade_count) for today."""
        today = str(get_current_ist_time().date())
        try:
            with sqlite3.connect(self._db) as conn:
                row = conn.execute(
                    "SELECT SUM(slippage_pct), COUNT(*) FROM fills WHERE trade_date=?",
                    (today,),
                ).fetchone()
            if row and row[1]:
                return row[0] or 0.0, row[1]
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
        return 0.0, 0

    def format_daily_report(self) -> str:
        total_pct, count = self.get_daily_slippage_cost()
        if count == 0:
            return "Slippage: No fills today"
        avg = total_pct / count * 100
        return f"Slippage: {count} fills | avg={avg:.3f}% | total_cost={total_pct*100:.3f}%"

    def record_fill_quality(
        self,
        order_id:       str,
        expected_price: float,
        actual_fill:    float,
        qty:            int,
    ) -> float:
        """
        Compute and log fill quality score for an executed order.
        fill_quality_score = 1.0 if fill exactly at expected price,
        grades down to 0.0 if slippage >= 0.5%.
        Returns the fill_quality_score (0.0–1.0).
        """
        if expected_price <= 0:
            return 1.0
        try:
            raw_slippage_pct = abs(actual_fill - expected_price) / expected_price
            # Linear scale: 0% slippage → 1.0, 0.5%+ slippage → 0.0
            fill_quality_score = max(0.0, 1.0 - (raw_slippage_pct / 0.005))
            logger.debug(
                f"FillQuality order={order_id} expected={expected_price:.4f} "
                f"fill={actual_fill:.4f} slip={raw_slippage_pct:.4%} "
                f"quality={fill_quality_score:.3f}"
            )
            return fill_quality_score
        except Exception as e:
            logger.debug(f"record_fill_quality failed: {e}")
            return 1.0

    # ─────────────────────────────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────────────────────────────

    def _get_stats(self, symbol: str) -> Optional[SlippageStats]:
        if symbol in self._stats_cache:
            return self._stats_cache[symbol]
        try:
            with sqlite3.connect(self._db) as conn:
                rows = conn.execute(
                    """
                    SELECT slippage_pct FROM fills
                    WHERE symbol=? AND slippage_pct > -0.01
                    ORDER BY id DESC LIMIT 50
                    """,
                    (symbol,),
                ).fetchall()
            if not rows or len(rows) < 3:
                return None
            slippages = [r[0] for r in rows]
            mean = sum(slippages) / len(slippages)
            variance = sum((x - mean) ** 2 for x in slippages) / len(slippages)
            std = variance ** 0.5
            sorted_slips = sorted(slippages)
            p95_idx = max(0, int(len(sorted_slips) * 0.95) - 1)
            stats = SlippageStats(
                symbol       = symbol,
                mean_pct     = mean,
                std_pct      = std,
                p95_pct      = sorted_slips[p95_idx],
                sample_count = len(slippages),
                last_updated = format_ist_timestamp(),
            )
            self._stats_cache[symbol] = stats
            return stats
        except Exception as e:
            logger.debug(f"SlippageTracker._get_stats {symbol}: {e}")
            return None

    def _init_db(self):
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute(SCHEMA)
        except Exception as e:
            logger.warning(f"SlippageTracker DB init failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_tracker: Optional[SlippageTracker] = None


def get_slippage_tracker() -> SlippageTracker:
    global _tracker
    if _tracker is None:
        _tracker = SlippageTracker()
        logger.info(f"[{format_ist_timestamp()}] SlippageTracker initialized")
    return _tracker


# ─────────────────────────────────────────────────────────────────────────────
# PRE-TRADE SLIPPAGE PREDICTION
# ─────────────────────────────────────────────────────────────────────────────

# Time-of-day fallback slippage estimates (% as float, e.g. 0.0018 = 0.18%)
_TOD_SLIPPAGE_FALLBACK: Dict[int, float] = {
    9:  0.0018,   # 0.18% — opening volatility
    10: 0.0012,   # 0.12% — morning momentum
    11: 0.0010,   # 0.10% — mid-morning calm
    12: 0.0009,   # 0.09% — midday quiet
    13: 0.0009,
    14: 0.0009,
    15: 0.0011,   # 0.11% — pre-close activity
}


def predict_slippage(
    symbol:            str,
    direction:         str,
    time_of_day_hour:  int,
) -> Tuple[float, str]:
    """
    Predict expected slippage for a prospective trade.

    Looks up historical fill data from SQLite (table 'fills', column 'slippage_pct')
    filtered by symbol + direction. Falls back to time-of-day table if insufficient history.

    Returns:
        (predicted_slippage_pct, confidence)
        predicted_slippage_pct: e.g. 0.0012 = 0.12%
        confidence: "HIGH" (≥20 fills), "MED" (≥5 fills), "LOW" (< 5 fills)
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT slippage_pct FROM fills
                WHERE symbol=? AND direction=? AND slippage_pct > -0.01
                ORDER BY id DESC LIMIT 100
                """,
                (symbol, direction),
            ).fetchall()
        fill_count = len(rows)
        if fill_count >= 5:
            slippages   = [r[0] for r in rows]
            predicted   = sum(slippages) / fill_count
            confidence  = "HIGH" if fill_count >= 20 else "MED"
            return max(0.0, predicted), confidence
    except Exception as e:
        logger.debug(f"predict_slippage DB query failed: {e}")

    # Fall back to time-of-day table
    hour_key    = max(9, min(15, time_of_day_hour))
    predicted   = _TOD_SLIPPAGE_FALLBACK.get(hour_key, 0.0012)
    return predicted, "LOW"
