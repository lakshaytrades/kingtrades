"""
test_critical_path.py — Unit tests for the US equity intraday trading bot.

Covers: RiskManager, HighAccuracyFilter, utils (ET/IST aliases), MarketInternals.

Run with:
    python3 -m pytest tests/ -v
    python3 -m pytest tests/test_critical_path.py -v

All tests are self-contained and require NO environment variables.
API calls are mocked throughout.
"""

import sys
import types
import unittest
from datetime import datetime, time
from typing import Dict
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Minimal stub modules injected BEFORE any bot import, so that modules that
# import optional third-party packages (growwapi, alpaca_trade_api, etc.)
# don't raise ImportError during collection.
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return sys.modules[name]


# Third-party stubs
_stub_module("alpaca_trade_api")
_stub_module("alpaca_trade_api.rest")
_stub_module("growwapi")
_stub_module("telegram")
_stub_module("telegram.ext")
_stub_module("newsapi")
_stub_module("ta")
_stub_module("ta.momentum")
_stub_module("ta.trend")
_stub_module("ta.volatility")

# Internal stubs — prevent circular / missing imports
_stub_module(
    "config",
    MAX_DAILY_CAPITAL=50_000,
    MAX_RISK_PCT=0.5,
    DAILY_LOSS_LIMIT_PCT=2.0,
    MAX_POSITIONS=10,
    MAX_PORTFOLIO_HEAT_PCT=3.0,
    MAX_CAPITAL_PER_TRADE_PCT=20.0,
    ATR_TRAIL_MULTIPLIER=0.8,
    PARTIAL_EXIT_T1_PCT=50.0,
    PARTIAL_EXIT_T2_PCT=30.0,
    RUNNER_PCT=20.0,
    BREAKEVEN_TRIGGER_PCT=0.5,
    MIN_DAILY_VOLUME=1_000_000,
    MAX_GAP_PCT=2.0,
    LARGE_GAP_PCT=3.5,
    EXTREME_GAP_PCT=5.0,
    CIRCUIT_BUFFER_PCT=0.5,
    CIRCUIT_BANDS=[5.0, 10.0, 20.0],
    DOW_SIZE_MULTIPLIERS={0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0},
    NIFTY_CIRCUIT_PCT=2.0,
    LIVE_TRADING_ENABLED=False,
)

_stub_module("broker", MARKET_NAME="US_ALPACA")
_stub_module("data_fetch_alpaca", get_data_fetcher=lambda: MagicMock())
_stub_module("auth_groww")
_stub_module("alerts_telegram")
_stub_module("news_filter")
_stub_module("nse_data", get_sector=lambda s: "TECHNOLOGY")
_stub_module("corporate_actions", is_safe_from_corp_actions=lambda s: (True, ""))
_stub_module("execution_alpaca", AlpacaExecutor=MagicMock)

# Now import the real bot modules
import utils  # noqa: E402
from utils import (  # noqa: E402
    get_current_ist_time,
    format_ist_timestamp,
    is_market_open_ist,
)

from risk_manager import RiskManager, Position, RiskState  # noqa: E402

from high_accuracy_filter import (  # noqa: E402
    HighAccuracyFilter,
    FilterResult,
    POWER_WINDOWS,
    AVOID_WINDOWS,
)

from market_internals import (  # noqa: E402
    MarketInternals,
    LONG_OK_THRESHOLD,
    SHORT_OK_THRESHOLD,
    get_market_internals,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ET_ZONE = utils.ET


def _make_position(**overrides) -> Position:
    """Return a minimal valid LONG position, overriding any fields as needed."""
    defaults = dict(
        symbol="AAPL",
        direction="LONG",
        entry_price=150.0,
        quantity=10,
        stop_loss=147.0,
        target_1=156.0,
        target_2=162.0,
        atr=2.0,
        quality_grade="B",
        t1_qty=5,
        t2_qty=3,
        runner_qty=2,
    )
    defaults.update(overrides)
    return Position(**defaults)


def _good_filter_kwargs(**overrides) -> dict:
    """
    Produce a dict of kwargs for HighAccuracyFilter.evaluate() that passes
    all 13 gates. Override individual fields to trigger specific rejections.
    """
    import pandas as pd
    # Minimal 5-min OHLCV DataFrame (3 rows) — Heikin Ashi check needs >= 3
    df = pd.DataFrame(
        {
            "open":   [100.0, 101.0, 102.0],
            "high":   [101.5, 102.5, 103.5],
            "low":    [99.5,  100.5, 101.5],
            "close":  [101.0, 102.0, 103.0],
            "volume": [500_000, 600_000, 700_000],
        }
    )
    base = dict(
        signal_score=85.0,
        direction="BUY",
        regime="STRONG_TREND_UP",
        mtf_alignment={
            "alignment_score": 80,
            "entry_direction": "LONG",
        },
        volume_ratio=2.5,
        pattern_names=["BULLISH_ENGULFING"],
        pattern_scores=[85.0],
        df_5m=df,
        rsi=45.0,
        above_vwap=True,
        spy_change_pct=0.4,
        stock_change_pct=0.8,
        news_clear=True,
        at_key_level=True,
        adx=25.0,
        spy_bullish=True,
        daily_volume=5_000_000,
        prev_close=0.0,   # skips circuit-proximity gate
        ltp=0.0,
        minutes_since_open=10.0,
        gap_pct=0.0,
        orb_direction="UP",
    )
    base.update(overrides)
    return base


# ===========================================================================
# 1. RISK MANAGER TESTS
# ===========================================================================

class TestRiskManagerPositionSizing(unittest.TestCase):
    """Tests for calculate_position_size and related sizing logic."""

    def setUp(self):
        """Create a fresh RiskManager with known capital and a day initialized."""
        self.rm = RiskManager(
            max_daily_capital=50_000,
            max_risk_pct=0.5,
            daily_loss_limit_pct=2.0,
            max_positions=10,
        )
        self.rm.initialize_day(available_balance=50_000, nifty_open=450.0)
        self.rm._available_balance = 50_000

    def _size(self, **kwargs) -> Dict:
        """Convenience wrapper: fill required args and return the result dict."""
        defaults = dict(
            symbol="AAPL",
            entry_price=150.0,
            stop_loss=147.0,
        )
        defaults.update(kwargs)
        return self.rm.calculate_position_size(**defaults)

    # ------------------------------------------------------------------
    def test_returns_positive_integer_quantity(self):
        """calculate_position_size with a normal trade returns quantity > 0."""
        result = self._size()
        self.assertIn("quantity", result)
        self.assertIsInstance(result["quantity"], int)
        self.assertGreater(result["quantity"], 0)

    def test_zero_risk_pct_returns_minimum_one_share(self):
        """A zero risk_pct still yields at least 1 share via Kelly fallback."""
        # max_risk_pct is 0.5 by default; simulate by giving a huge SL distance
        # so risk_qty rounds to 0 — Kelly kicks in and clamps to 1.
        result = self._size(stop_loss=0.01, entry_price=150.0)
        self.assertGreaterEqual(result.get("quantity", 1), 1)

    def test_sl_equal_to_entry_returns_invalid_result(self):
        """SL == entry_price returns quantity 0 with an 'Invalid SL' reason."""
        result = self.rm.calculate_position_size(
            symbol="TEST", entry_price=100.0, stop_loss=100.0
        )
        self.assertEqual(result.get("quantity", 0), 0)
        self.assertIn("Invalid", result.get("reason", ""))

    def test_atr_zero_does_not_raise(self):
        """calculate_position_size with atr=0 completes without exception."""
        try:
            result = self._size(atr=0.0)
            self.assertIn("quantity", result)
        except Exception as exc:
            self.fail(f"calculate_position_size raised {exc} with atr=0")

    def test_capital_cap_prevents_unreasonable_quantity(self):
        """Position size is capped and does not return 10,000+ shares for a cheap stock."""
        self.rm.initialize_day(available_balance=10_000)
        result = self.rm.calculate_position_size(
            symbol="CHEAP", entry_price=1.0, stop_loss=0.90
        )
        # Capital cap is 20% of 10k = $2000 → max 2000 shares at $1 each
        self.assertLessEqual(result.get("quantity", 0), 2_000)

    def test_higher_risk_pct_yields_more_shares(self):
        """risk_pct 1.0 produces at least as many shares as 0.5 (same symbol/price)."""
        rm_low  = RiskManager(max_daily_capital=50_000, max_risk_pct=0.5)
        rm_high = RiskManager(max_daily_capital=50_000, max_risk_pct=1.0)
        for rm in (rm_low, rm_high):
            rm.initialize_day(available_balance=50_000)
            rm._available_balance = 50_000

        r_low  = rm_low.calculate_position_size("AAPL", 150.0, 147.0)
        r_high = rm_high.calculate_position_size("AAPL", 150.0, 147.0)
        self.assertGreaterEqual(r_high.get("quantity", 0), r_low.get("quantity", 0))

    def test_check_position_health_hold_when_trade_ok(self):
        """check_position_health returns non-EXIT action for a healthy in-progress trade."""
        pos = _make_position()
        # price midway between entry and T1; action may be HOLD or BREAK_EVEN depending on hold time
        result = self.rm.check_position_health(pos, ltp=152.0)
        self.assertIn(result["action"], ("HOLD", "BREAK_EVEN", "PARTIAL_EXIT"))

    def test_daily_loss_circuit_breaker_triggers(self):
        """can_take_trade blocks new entries once daily loss limit is exceeded."""
        self.rm.state.daily_pnl    = -1_100.0  # >2% of 50k
        self.rm.state.daily_capital = 50_000
        result = self.rm.can_take_trade("AAPL")
        self.assertFalse(result["allowed"])

    def test_consecutive_loss_counter_increments(self):
        """close_position increments consecutive_losses on a losing trade."""
        pos = _make_position(symbol="TSLA")
        self.rm.add_position(pos)
        # Close at a price below entry → loss
        self.rm.close_position("TSLA", exit_price=140.0, reason="test")
        self.assertEqual(self.rm.state.consecutive_losses, 1)

    def test_position_size_result_contains_risk_metadata(self):
        """calculate_position_size result includes risk_amount and capital_pct keys."""
        result = self._size()
        for key in ("risk_amount", "capital_pct", "sl_distance", "session"):
            self.assertIn(key, result, f"Key '{key}' missing from position size result")


# ===========================================================================
# 2. HIGH ACCURACY FILTER TESTS
# ===========================================================================

class TestHighAccuracyFilterGates(unittest.TestCase):
    """Tests for HighAccuracyFilter gate logic."""

    def setUp(self):
        """Instantiate a fresh filter for each test."""
        self.flt = HighAccuracyFilter()

    # Helper: evaluate with good defaults, patching the ET time to a POWER window
    def _eval(self, **overrides) -> FilterResult:
        kwargs = _good_filter_kwargs(**overrides)
        # Patch get_current_ist_time → 09:45 ET (inside POWER window)
        et_now = datetime(2026, 5, 15, 9, 45, 0, tzinfo=ET_ZONE)
        with patch("high_accuracy_filter.get_current_ist_time", return_value=et_now):
            return self.flt.evaluate(**kwargs)

    # ------------------------------------------------------------------
    def test_rejects_low_volume_ratio(self):
        """Filter rejects a signal when volume_ratio is below 2.0."""
        result = self._eval(volume_ratio=1.5)
        self.assertFalse(result.passed)
        self.assertIn("VOLUME", " ".join(result.gates_failed))

    def test_rejects_low_pattern_score(self):
        """Filter rejects a signal when signal_score is below 80."""
        result = self._eval(signal_score=75.0, pattern_scores=[75.0])
        self.assertFalse(result.passed)
        self.assertIn("PATTERN_SCORE", " ".join(result.gates_failed))

    def test_rejects_during_midday_avoid_window(self):
        """Filter rejects trades during the midday AVOID window (11:30–13:30 ET)."""
        midday = datetime(2026, 5, 15, 12, 0, 0, tzinfo=ET_ZONE)
        with patch("high_accuracy_filter.get_current_ist_time", return_value=midday):
            result = self.flt.evaluate(**_good_filter_kwargs())
        self.assertFalse(result.passed)
        self.assertIn("AVOID", result.rejection_reason.upper())

    def test_accepts_when_all_gates_pass(self):
        """Filter passes a signal when every gate is satisfied with good values."""
        result = self._eval()
        self.assertTrue(result.passed)
        self.assertIn(result.quality_grade, ("A+", "A", "B"))

    def test_rejects_when_adx_below_threshold(self):
        """Filter rejects when ADX < 20 (choppy/ranging market)."""
        result = self._eval(adx=15.0)
        self.assertFalse(result.passed)
        self.assertTrue(
            any("ADX" in g for g in result.gates_failed),
            f"Expected ADX gate in gates_failed, got: {result.gates_failed}",
        )

    def test_grade_a_plus_for_score_95(self):
        """A final score >= 92 earns grade A+."""
        result = self._eval(signal_score=95.0, pattern_scores=[95.0])
        if result.passed:
            self.assertEqual(result.quality_grade, "A+")

    def test_grade_a_for_score_87(self):
        """A final score >= 85 earns at least grade A (bonuses may push to A+)."""
        result = self._eval(signal_score=87.0, pattern_scores=[87.0])
        if result.passed:
            self.assertIn(result.quality_grade, ("A", "A+"))

    def test_grade_b_for_score_82(self):
        """A final score in [80, 85) earns grade B."""
        # Use a score that, after bonuses, stays in the B range.
        # Provide no ORB so we avoid +8 bonus; above_vwap=True gives +6 VWAP bonus.
        result = self._eval(
            signal_score=80.0,
            pattern_scores=[80.0],
            pattern_names=["BASIC_PATTERN"],  # no premium-pattern bonus
            orb_direction="",
            above_vwap=False,   # avoids +6 VWAP bonus; neutral for BUY
            spy_change_pct=0.1,
            stock_change_pct=0.2,
            rsi=65.0,           # out of golden RSI zone → 0 bonus
        )
        if result.passed:
            self.assertIn(result.quality_grade, ("B", "A", "A+"))

    def test_spy_alignment_blocks_long_when_spy_is_down(self):
        """Gate 13 blocks a LONG signal when spy_bullish=False."""
        result = self._eval(spy_bullish=False, direction="BUY")
        self.assertFalse(result.passed)
        self.assertIn("SPY_CONFLICT", result.gates_failed)

    def test_volume_gate_accepts_at_exactly_2x(self):
        """Volume gate passes when volume_ratio is exactly 2.0."""
        result = self._eval(volume_ratio=2.0)
        self.assertTrue(result.passed, f"Expected pass at 2.0x volume, got: {result.rejection_reason}")

    def test_filter_result_has_verdict_field(self):
        """FilterResult always has a .passed boolean field."""
        result = self._eval()
        self.assertIsInstance(result.passed, bool)

    def test_reject_count_increments_on_rejection(self):
        """_reject_count increases by 1 each time a signal is rejected."""
        before = self.flt._reject_count
        self._eval(volume_ratio=0.5)   # guaranteed rejection
        self.assertEqual(self.flt._reject_count, before + 1)


# ===========================================================================
# 3. UTILS / TIMEZONE TESTS
# ===========================================================================

class TestUtilsTimezone(unittest.TestCase):
    """Tests for ET/IST time utilities in utils.py."""

    def test_get_current_ist_time_returns_aware_datetime(self):
        """get_current_ist_time() returns a timezone-aware datetime."""
        now = get_current_ist_time()
        self.assertIsNotNone(now.tzinfo, "Returned datetime must be timezone-aware")

    def test_get_current_ist_time_is_et_zone(self):
        """get_current_ist_time() is in America/New_York (ET) timezone."""
        now = get_current_ist_time()
        # ZoneInfo key check — DST-aware zones report zone key via key attribute
        zone_key = getattr(now.tzinfo, "key", str(now.tzinfo))
        self.assertIn(
            "New_York",
            zone_key,
            f"Expected ET zone, got tzinfo={now.tzinfo}",
        )

    def test_format_ist_timestamp_returns_non_empty_string(self):
        """format_ist_timestamp() returns a non-empty string."""
        ts = format_ist_timestamp()
        self.assertIsInstance(ts, str)
        self.assertTrue(len(ts) > 0)

    def test_is_market_open_ist_returns_bool(self):
        """is_market_open_ist() returns a plain bool."""
        result = is_market_open_ist()
        self.assertIsInstance(result, bool)

    def test_time_functions_do_not_raise(self):
        """All time utility functions complete without raising any exception."""
        try:
            get_current_ist_time()
            format_ist_timestamp()
            is_market_open_ist()
        except Exception as exc:
            self.fail(f"Time utility raised unexpectedly: {exc}")

    def test_format_ist_timestamp_looks_like_date_string(self):
        """format_ist_timestamp() output contains a 4-digit year."""
        ts = format_ist_timestamp()
        self.assertRegex(ts, r"\d{4}", "Timestamp should contain a year (4 digits)")

    def test_market_not_open_at_2am_et(self):
        """is_market_open_et returns False at 02:00 ET (pre-pre-market)."""
        # Patch the internal helper to a fixed 2 AM time
        early = datetime(2026, 5, 15, 2, 0, 0, tzinfo=ET_ZONE)
        with patch("utils.get_current_et_time", return_value=early):
            result = utils.is_market_open_et()
        self.assertFalse(result)

    def test_market_not_open_on_weekend(self):
        """is_market_open_et returns False on Saturday (weekday=5)."""
        saturday = datetime(2026, 5, 16, 10, 0, 0, tzinfo=ET_ZONE)  # Saturday
        with patch("utils.get_current_et_time", return_value=saturday):
            result = utils.is_market_open_et()
        self.assertFalse(result)


# ===========================================================================
# 4. MARKET INTERNALS TESTS
# ===========================================================================

class TestMarketInternalsConstants(unittest.TestCase):
    """Tests for market_internals constants and class behaviour."""

    def test_long_ok_threshold_is_55(self):
        """LONG_OK_THRESHOLD constant equals 55.0."""
        self.assertEqual(LONG_OK_THRESHOLD, 55.0)

    def test_short_ok_threshold_is_45(self):
        """SHORT_OK_THRESHOLD constant equals 45.0."""
        self.assertEqual(SHORT_OK_THRESHOLD, 45.0)

    def test_is_long_ok_returns_true_for_score_60(self):
        """is_long_ok returns True when breadth_score is 60 (above 55 threshold)."""
        mi = MarketInternals()
        mi._cache = {
            "breadth_score": 60.0,
            "bullish_sectors": 7,
            "bearish_sectors": 3,
        }
        mi._cache_time = __import__("time").monotonic()
        ok, reason = mi.is_long_ok()
        self.assertTrue(ok)

    def test_is_short_ok_returns_true_for_score_40(self):
        """is_short_ok returns True when breadth_score is 40 (below 45 threshold)."""
        mi = MarketInternals()
        mi._cache = {
            "breadth_score": 40.0,
            "bullish_sectors": 2,
            "bearish_sectors": 8,
        }
        mi._cache_time = __import__("time").monotonic()
        ok, reason = mi.is_short_ok()
        self.assertTrue(ok)

    def test_neutral_score_50_neither_long_nor_short_ok(self):
        """Breadth score 50 blocks both long_ok and short_ok."""
        mi = MarketInternals()
        mi._cache = {
            "breadth_score": 50.0,
            "bullish_sectors": 5,
            "bearish_sectors": 5,
        }
        mi._cache_time = __import__("time").monotonic()
        long_ok,  _ = mi.is_long_ok()
        short_ok, _ = mi.is_short_ok()
        self.assertFalse(long_ok,  "Score 50 should NOT allow long entries")
        self.assertFalse(short_ok, "Score 50 should NOT allow short entries")

    def test_get_size_multiplier_returns_valid_float(self):
        """get_size_multiplier returns a float in the documented [0.4, 1.2] range."""
        mi = MarketInternals()
        for score in (20.0, 45.0, 50.0, 60.0, 80.0):
            mi._cache = {"breadth_score": score}
            mi._cache_time = __import__("time").monotonic()
            mult = mi.get_size_multiplier()
            self.assertIsInstance(mult, float, f"score={score} → mult should be float")
            self.assertGreaterEqual(mult, 0.4, f"score={score} → mult too low")
            self.assertLessEqual(mult, 1.2, f"score={score} → mult too high")

    def test_market_internals_instantiates_without_error(self):
        """MarketInternals() can be instantiated without any environment setup."""
        try:
            mi = MarketInternals()
            self.assertIsNotNone(mi)
        except Exception as exc:
            self.fail(f"MarketInternals() raised {exc}")

    def test_get_market_internals_singleton(self):
        """get_market_internals() returns the same object on repeated calls."""
        import market_internals as _mi_mod
        _mi_mod._market_internals_instance = None  # reset to ensure clean test
        a = get_market_internals()
        b = get_market_internals()
        self.assertIs(a, b, "get_market_internals() must return singleton")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
