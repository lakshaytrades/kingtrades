"""
gap_analyzer.py — NSE Momentum Groww AI Bot
Pre-Market Gap Analysis — Today Open vs Yesterday Close

Purpose: Detect and manage gap opens that create unreliable signals.

18yr RULE — "Gaps are NOT your friend at open":
  - Stocks with >2% gap up or down have UNPREDICTABLE price discovery for 5-10 min.
  - The first 1-3 candles are often noise — institutions testing retail.
  - Chasing a gap open is a 50/50 gamble, NOT momentum trading.
  - Wait for the gap to stabilize, then trade the direction after confirmation.

GAP CATEGORIES:
  SMALL  (<2%)   : Normal — trade immediately
  MEDIUM (2-3.5%): Wait 5 min for price discovery
  LARGE  (3.5-5%): Wait 15 min
  EXTREME (>5%)  : Avoid the stock for entire session

Special case — Gap Reclaim:
  If price FILLS the gap within the first 15 min → potential strong reversal signal.
  If price EXTENDS the gap beyond 15 min → trend continuation (trade with gap).
  Both are handled by the wait window: after the wait, signal direction is reliable.
"""

import logging
from datetime import date
from typing import Dict, Optional, Tuple

from utils import get_current_ist_date, get_current_ist_time, MARKET_OPEN

logger = logging.getLogger(__name__)


# ── GAP THRESHOLDS ─────────────────────────────────────────────────────────

class GapCategory:
    SMALL   = "SMALL"     # < 2% — no restriction
    MEDIUM  = "MEDIUM"    # 2.0% – 3.5% — wait 5 min
    LARGE   = "LARGE"     # 3.5% – 5.0% — wait 15 min
    EXTREME = "EXTREME"   # > 5% — avoid session


THRESHOLDS = {
    GapCategory.EXTREME: 5.0,
    GapCategory.LARGE:   3.5,
    GapCategory.MEDIUM:  2.0,
    GapCategory.SMALL:   0.0,
}

# Minutes to wait after market open before trading a gapped stock
GAP_WAIT_MINUTES: Dict[str, int] = {
    GapCategory.SMALL:   0,
    GapCategory.MEDIUM:  5,
    GapCategory.LARGE:   15,
    GapCategory.EXTREME: 9999,   # Session-long avoid
}


class GapInfo:
    """Complete gap analysis result for one symbol."""

    def __init__(
        self,
        symbol:      str,
        prev_close:  float,
        today_open:  float,
    ):
        self.symbol     = symbol
        self.prev_close = prev_close
        self.today_open = today_open

        if prev_close > 0 and today_open > 0:
            self.gap_pct = round((today_open - prev_close) / prev_close * 100, 2)
        else:
            self.gap_pct = 0.0

        self.abs_gap   = abs(self.gap_pct)
        self.direction = "UP" if self.gap_pct > 0.1 else ("DOWN" if self.gap_pct < -0.1 else "FLAT")
        self.category  = self._categorize()
        self.wait_min  = GAP_WAIT_MINUTES[self.category]

    def _categorize(self) -> str:
        if self.abs_gap >= THRESHOLDS[GapCategory.EXTREME]:
            return GapCategory.EXTREME
        if self.abs_gap >= THRESHOLDS[GapCategory.LARGE]:
            return GapCategory.LARGE
        if self.abs_gap >= THRESHOLDS[GapCategory.MEDIUM]:
            return GapCategory.MEDIUM
        return GapCategory.SMALL

    def is_safe(self, minutes_since_open: float = 0) -> Tuple[bool, str]:
        """
        Is it safe to trade this symbol right now given the gap?

        Args:
            minutes_since_open: Minutes elapsed since 9:15 AM IST open

        Returns:
            (safe: bool, reason: str)
        """
        if self.category == GapCategory.SMALL:
            return True, ""

        if self.category == GapCategory.EXTREME:
            return False, (
                f"EXTREME gap {self.gap_pct:+.1f}% — avoid entire session "
                f"(unresolvable price discovery risk)"
            )

        if minutes_since_open < self.wait_min:
            remaining = self.wait_min - minutes_since_open
            return False, (
                f"{self.category} gap {self.gap_pct:+.1f}% — "
                f"wait {remaining:.0f} more min for price discovery"
            )

        return True, ""

    def __repr__(self):
        return (
            f"GapInfo({self.symbol}: {self.gap_pct:+.2f}% "
            f"[{self.category}] wait={self.wait_min}min)"
        )


class GapAnalyzer:
    """
    Analyzes pre-market gaps for the watchlist.

    Usage:
        analyzer = GapAnalyzer()
        # At market open — pre-load all gaps
        analyzer.load_gaps_for_watchlist(fetcher, watchlist)

        # Before each signal
        safe, reason = analyzer.is_safe_to_trade("RELIANCE")
    """

    def __init__(self):
        self._gaps: Dict[str, GapInfo] = {}
        self._analysis_date: Optional[date] = None

    def set_gap(self, symbol: str, prev_close: float, today_open: float) -> GapInfo:
        """Manually set gap data for a symbol (from candle data)."""
        info = GapInfo(symbol, prev_close, today_open)
        self._gaps[symbol] = info
        if info.category != GapCategory.SMALL:
            logger.info(
                f"Gap detected: {symbol} {info.direction} {info.gap_pct:+.2f}% "
                f"[{info.category}] — wait {info.wait_min} min"
            )
        return info

    def is_safe_to_trade(self, symbol: str) -> Tuple[bool, str]:
        """
        Check if a symbol is safe to trade given its gap.
        Automatically calculates minutes since open.

        Returns:
            (safe: bool, reason: str)
        """
        gap_info = self._gaps.get(symbol.upper())
        if gap_info is None:
            return True, ""   # No gap data → assume safe (no false positives)

        # Calculate minutes since market open (9:15 AM IST)
        now_ist = get_current_ist_time()
        from datetime import datetime, time as dtime
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        today = now_ist.date()
        market_open_dt = datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)
        minutes_since_open = max(0.0, (now_ist - market_open_dt).total_seconds() / 60)

        return gap_info.is_safe(minutes_since_open)

    def get_gap_pct(self, symbol: str) -> float:
        """Get gap percentage for a symbol (0.0 if not analyzed)."""
        info = self._gaps.get(symbol.upper())
        return info.gap_pct if info else 0.0

    def get_gap_info(self, symbol: str) -> Optional[GapInfo]:
        """Get full GapInfo for a symbol."""
        return self._gaps.get(symbol.upper())

    def load_gaps_for_watchlist(self, data_fetcher, watchlist) -> Dict[str, GapInfo]:
        """
        Pre-load gap data for all watchlist symbols at market open.
        Uses 1-day OHLCV to get yesterday's close and today's open.

        Args:
            data_fetcher: GrowwDataFetcher instance
            watchlist:    list of NSE symbols

        Returns:
            dict of symbol → GapInfo
        """
        today = get_current_ist_date()
        if self._analysis_date == today and self._gaps:
            logger.debug("Gap analysis already done today — using cached data")
            return self._gaps

        loaded = 0
        errors = 0

        for symbol in watchlist:
            try:
                # Get last 3 daily candles: yesterday close + today open
                # get_candles() signature: (symbol, interval, days, from_dt, to_dt)
                df = data_fetcher.get_candles(symbol=symbol, interval="1d", days=3)
                if df is None or len(df) < 2:
                    continue

                prev_close  = float(df["close"].iloc[-2])
                today_open  = float(df["open"].iloc[-1])

                self.set_gap(symbol, prev_close, today_open)
                loaded += 1

            except Exception as e:
                logger.debug(f"Gap load error for {symbol}: {e}")
                errors += 1

        self._analysis_date = today
        extreme_count = sum(1 for g in self._gaps.values() if g.category == GapCategory.EXTREME)
        gapped_count  = sum(1 for g in self._gaps.values() if g.category != GapCategory.SMALL)

        logger.info(
            f"Gap analysis complete: {loaded}/{len(watchlist)} symbols | "
            f"Gapped: {gapped_count} | Extreme: {extreme_count} | Errors: {errors}"
        )
        return self._gaps

    def get_gapped_stocks_summary(self) -> str:
        """Format summary of gapped stocks for Telegram morning brief."""
        gapped = [g for g in self._gaps.values() if g.category != GapCategory.SMALL]
        if not gapped:
            return "No significant gaps today."

        lines = [f"📊 Gap opens ({len(gapped)} stocks):"]
        for g in sorted(gapped, key=lambda x: abs(x.gap_pct), reverse=True)[:10]:
            icon = "🔴" if g.direction == "DOWN" else "🟢"
            lines.append(
                f"  {icon} {g.symbol}: {g.gap_pct:+.1f}% [{g.category}] "
                f"— wait {g.wait_min}min"
            )
        return "\n".join(lines)

    def clear(self):
        """Clear all gap data (call at start of new trading day)."""
        self._gaps.clear()
        self._analysis_date = None


# ── SINGLETON ──────────────────────────────────────────────────────────────

_gap_analyzer_instance: Optional[GapAnalyzer] = None


def get_gap_analyzer() -> GapAnalyzer:
    """Return singleton GapAnalyzer instance."""
    global _gap_analyzer_instance
    if _gap_analyzer_instance is None:
        _gap_analyzer_instance = GapAnalyzer()
    return _gap_analyzer_instance


def is_gap_safe(symbol: str) -> Tuple[bool, str]:
    """Quick helper: (safe, reason) for gap check."""
    return get_gap_analyzer().is_safe_to_trade(symbol)


# ── SELF-TEST ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Gap Analyzer Test ===")
    analyzer = GapAnalyzer()

    # Simulate various gap scenarios
    test_cases = [
        ("RELIANCE",  1500.00, 1505.00),   # +0.33% — SMALL
        ("HDFCBANK",  1700.00, 1738.00),   # +2.24% — MEDIUM
        ("TATAMOTORS", 800.00,  830.00),   # +3.75% — LARGE
        ("ZOMATO",     220.00,  235.00),   # +6.82% — EXTREME
        ("TCS",       3500.00, 3430.00),   # -2.00% — MEDIUM down
    ]

    for sym, prev, today in test_cases:
        info = analyzer.set_gap(sym, prev, today)
        safe_now, reason = info.is_safe(minutes_since_open=0)
        safe_later, reason2 = info.is_safe(minutes_since_open=20)
        print(
            f"\n{sym}: {info.gap_pct:+.2f}% [{info.category}]"
            f"\n  At open (0 min): {'✅ Safe' if safe_now else f'⛔ {reason}'}"
            f"\n  After 20 min:   {'✅ Safe' if safe_later else f'⛔ {reason2}'}"
        )

    print("\n" + analyzer.get_gapped_stocks_summary())
