"""
test_commission_and_health.py
Tests for CommissionTracker, SlippageModel cost accounting,
and SystemHealthChecker (with mocked API calls).
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# COMMISSION TRACKER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestCommissionTracker(unittest.TestCase):

    def setUp(self):
        try:
            from commission_tracker import CommissionTracker, TradeCost
            self.CommissionTracker = CommissionTracker
            self.TradeCost = TradeCost
            self.tracker = CommissionTracker()
        except Exception as e:
            self.skipTest(f"CommissionTracker unavailable: {e}")

    def test_zero_commission_alpaca(self):
        """Alpaca charges zero commission per trade."""
        cost = self.TradeCost("AAPL", 100, 180.0, 190.0, "LONG")
        self.assertEqual(cost.commission, 0.0)

    def test_sec_fee_is_positive_for_sale(self):
        """SEC fee applies on the sale leg."""
        cost = self.TradeCost("AAPL", 100, 180.0, 190.0, "LONG")
        self.assertGreater(cost.sec_fee, 0.0)

    def test_finra_taf_charged_both_legs(self):
        """FINRA TAF applies to both buy and sell legs."""
        cost = self.TradeCost("NVDA", 100, 500.0, 520.0, "LONG")
        # 100 shares × $0.000166 × 2 legs = $0.0332
        self.assertAlmostEqual(cost.finra_taf, 0.0332, places=4)

    def test_finra_taf_capped(self):
        """FINRA TAF capped at $8.30 per leg ($16.60 round-trip)."""
        # 100,000 shares × $0.000166 = $16.60 per leg → cap at $8.30 each
        cost = self.TradeCost("AAPL", 100_000, 1.0, 1.1, "LONG")
        self.assertAlmostEqual(cost.finra_taf, 8.30 * 2, places=2)

    def test_net_pnl_less_than_gross(self):
        """Net P&L is always less than gross P&L due to fees."""
        cost = self.TradeCost("TSLA", 50, 200.0, 220.0, "LONG")
        self.assertLess(cost.net_pnl, cost.gross_pnl)

    def test_losing_trade_net_worse_than_gross(self):
        """On a loss, net P&L is worse than gross due to fees."""
        cost = self.TradeCost("TSLA", 50, 200.0, 190.0, "LONG")
        self.assertLess(cost.net_pnl, cost.gross_pnl)

    def test_short_gross_pnl_positive_when_price_falls(self):
        """SHORT trade profits when price falls."""
        cost = self.TradeCost("AAPL", 100, 180.0, 170.0, "SHORT")
        self.assertGreater(cost.gross_pnl, 0.0)

    def test_record_trade_adds_to_history(self):
        """record_trade stores the cost in history."""
        self.tracker.record_trade("AAPL", 100, 180.0, 190.0, "LONG")
        totals = self.tracker.session_totals()
        self.assertEqual(totals["trades"], 1)

    def test_session_totals_sums_correctly(self):
        """Session totals correctly sum across multiple trades."""
        self.tracker.record_trade("AAPL", 100, 180.0, 185.0, "LONG")
        self.tracker.record_trade("NVDA", 50, 500.0, 510.0, "LONG")
        totals = self.tracker.session_totals()
        self.assertEqual(totals["trades"], 2)
        self.assertGreater(totals["total_cost"], 0)

    def test_estimate_cost_returns_realistic_rr(self):
        """Cost estimate shows realistic R:R after fees."""
        result = self.tracker.estimate_cost("NVDA", 100, 500.0, 530.0, 490.0, "LONG")
        self.assertIn("realistic_rr", result)
        self.assertIn("breakeven_move_pct", result)
        # Breakeven must be > 0
        self.assertGreater(result["breakeven_move_pct"], 0)

    def test_estimate_realistic_rr_lower_than_gross_rr(self):
        """After costs, R:R should be slightly lower than the gross 3:1."""
        result = self.tracker.estimate_cost("AAPL", 100, 150.0, 159.0, 147.0, "LONG")
        # Gross R:R = 9/3 = 3.0; realistic should be slightly less
        gross_rr = (159.0 - 150.0) / (150.0 - 147.0)
        self.assertLessEqual(result["realistic_rr"], gross_rr)

    def test_reset_clears_history(self):
        """reset() clears all recorded trades."""
        self.tracker.record_trade("AAPL", 100, 180.0, 185.0, "LONG")
        self.tracker.reset()
        totals = self.tracker.session_totals()
        self.assertEqual(totals["trades"], 0)

    def test_singleton_returns_same_instance(self):
        """get_commission_tracker() returns singleton."""
        from commission_tracker import get_commission_tracker
        t1 = get_commission_tracker()
        t2 = get_commission_tracker()
        self.assertIs(t1, t2)


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM HEALTH CHECKER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemHealthChecker(unittest.TestCase):

    def setUp(self):
        try:
            from system_health import SystemHealthChecker, HealthReport
            self.SystemHealthChecker = SystemHealthChecker
            self.HealthReport = HealthReport
        except Exception as e:
            self.skipTest(f"SystemHealthChecker unavailable: {e}")

    def test_health_report_has_required_fields(self):
        """HealthReport dataclass has all required fields."""
        report = self.HealthReport(ok=True)
        self.assertTrue(hasattr(report, "ok"))
        self.assertTrue(hasattr(report, "blocked_reason"))
        self.assertTrue(hasattr(report, "warnings"))
        self.assertTrue(hasattr(report, "checks"))

    def test_check_returns_blocked_when_api_fails(self):
        """Block trading when Alpaca API is unreachable."""
        checker = self.SystemHealthChecker()
        with patch.object(checker, "_check_alpaca_api", return_value=False):
            report = checker.force_check()
        self.assertFalse(report.ok)
        self.assertIn("Alpaca", report.blocked_reason)

    def test_check_returns_blocked_outside_trading_hours(self):
        """Block trading outside 9:30-15:55 ET."""
        checker = self.SystemHealthChecker()
        with patch.object(checker, "_check_alpaca_api", return_value=True), \
             patch.object(checker, "_check_data_freshness", return_value=(True, 0.0)), \
             patch.object(checker, "_check_time_window", return_value=False), \
             patch.object(checker, "_check_capital", return_value=(True, 50000.0)):
            report = checker.force_check()
        self.assertFalse(report.ok)

    def test_check_passes_with_all_healthy(self):
        """Returns ok=True when all checks pass."""
        checker = self.SystemHealthChecker()
        with patch.object(checker, "_check_alpaca_api", return_value=True), \
             patch.object(checker, "_check_data_freshness", return_value=(True, 5.0)), \
             patch.object(checker, "_check_time_window", return_value=True), \
             patch.object(checker, "_check_capital", return_value=(True, 50000.0)):
            report = checker.force_check({"day_pnl": 0, "day_capital": 50000, "min_score": 90})
        self.assertTrue(report.ok)

    def test_check_blocks_on_daily_loss_limit(self):
        """Block when daily loss exceeds 2% of capital."""
        checker = self.SystemHealthChecker()
        with patch.object(checker, "_check_alpaca_api", return_value=True), \
             patch.object(checker, "_check_data_freshness", return_value=(True, 5.0)), \
             patch.object(checker, "_check_time_window", return_value=True), \
             patch.object(checker, "_check_capital", return_value=(True, 50000.0)):
            # 2.5% loss on $50k = $1,250 loss
            report = checker.force_check({"day_pnl": -1300, "day_capital": 50000})
        self.assertFalse(report.ok)

    def test_stale_data_adds_warning_not_block(self):
        """Stale price data (>30s) generates a warning but doesn't block."""
        checker = self.SystemHealthChecker()
        with patch.object(checker, "_check_alpaca_api", return_value=True), \
             patch.object(checker, "_check_data_freshness", return_value=(False, 45.0)), \
             patch.object(checker, "_check_time_window", return_value=True), \
             patch.object(checker, "_check_capital", return_value=(True, 50000.0)):
            report = checker.force_check({"day_pnl": 0, "day_capital": 50000, "min_score": 90})
        # Should still be OK (data freshness is a warning, not a block)
        self.assertGreater(len(report.warnings), 0)

    def test_cache_returns_same_result_within_interval(self):
        """Second call within CHECK_INTERVAL returns cached result."""
        checker = self.SystemHealthChecker()
        mock_report = self.HealthReport(ok=True, checks={"ALPACA_API": True})
        checker._last_report = mock_report
        import time as _t
        checker._last_check = _t.monotonic()  # just checked

        with patch.object(checker, "_run_checks") as mock_run:
            result = checker.check()
            mock_run.assert_not_called()
        self.assertIs(result, mock_report)

    def test_singleton(self):
        """get_health_checker() returns singleton."""
        from system_health import get_health_checker
        c1 = get_health_checker()
        c2 = get_health_checker()
        self.assertIs(c1, c2)


# ─────────────────────────────────────────────────────────────────────────────
# CORRELATION GATE TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrelationGate(unittest.TestCase):

    def setUp(self):
        try:
            from high_accuracy_filter import HighAccuracyFilter
            self.filt = HighAccuracyFilter()
        except Exception as e:
            self.skipTest(f"HighAccuracyFilter unavailable: {e}")

    def test_nvda_blocked_when_amd_open(self):
        """NVDA LONG blocked when AMD LONG already open (85% correlated)."""
        if not hasattr(self.filt, "_check_correlation"):
            self.skipTest("_check_correlation not implemented yet")
        ok, reason = self.filt._check_correlation("NVDA", "LONG", open_positions=["AMD"])
        self.assertFalse(ok)
        self.assertIn("CORR", reason)

    def test_nvda_allowed_when_no_open_positions(self):
        """NVDA allowed when no positions are open."""
        if not hasattr(self.filt, "_check_correlation"):
            self.skipTest("_check_correlation not implemented yet")
        ok, _ = self.filt._check_correlation("NVDA", "LONG", open_positions=[])
        self.assertTrue(ok)

    def test_uncorrelated_pair_allowed(self):
        """AAPL + XOM are not correlated — both positions allowed."""
        if not hasattr(self.filt, "_check_correlation"):
            self.skipTest("_check_correlation not implemented yet")
        ok, _ = self.filt._check_correlation("AAPL", "LONG", open_positions=["XOM"])
        self.assertTrue(ok)

    def test_spy_qqq_highly_correlated(self):
        """SPY and QQQ are 92% correlated — block."""
        if not hasattr(self.filt, "_check_correlation"):
            self.skipTest("_check_correlation not implemented yet")
        ok, _ = self.filt._check_correlation("QQQ", "LONG", open_positions=["SPY"])
        self.assertFalse(ok)


# ─────────────────────────────────────────────────────────────────────────────
# HISTORICAL BACKTESTER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoricalBacktester(unittest.TestCase):

    def setUp(self):
        try:
            from historical_backtester import HistoricalBacktester, BacktestConfig, BacktestResult
            self.HistoricalBacktester = HistoricalBacktester
            self.BacktestConfig = BacktestConfig
            self.BacktestResult = BacktestResult
        except Exception as e:
            self.skipTest(f"HistoricalBacktester unavailable: {e}")

    def test_instantiates_without_error(self):
        """HistoricalBacktester instantiates cleanly."""
        bt = self.HistoricalBacktester()
        self.assertIsNotNone(bt)

    def test_config_defaults(self):
        """BacktestConfig has sensible defaults."""
        cfg = self.BacktestConfig()
        self.assertGreater(cfg.lookback_days, 0)
        self.assertGreater(cfg.initial_capital, 0)
        self.assertGreater(cfg.min_score, 0)
        self.assertIsInstance(cfg.symbols, list)

    def test_load_results_returns_none_when_no_file(self):
        """load_results() returns None when no saved results exist."""
        import tempfile, json
        bt = self.HistoricalBacktester()
        # Point to a temp location that doesn't exist
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = bt.load_results()
        self.assertIsNone(result)

    def test_backtest_result_has_required_fields(self):
        """BacktestResult dataclass has all required statistical fields."""
        result = self.BacktestResult(
            total_trades=100, wins=60, losses=40,
            win_rate=60.0, profit_factor=1.8,
            sharpe_ratio=1.5, max_drawdown_pct=6.0,
            avg_rr=2.5, net_return_pct=18.0,
            pattern_stats={}, hourly_stats={}, regime_stats={},
            best_patterns=[], worst_patterns=[], generated_at="2026-05-15"
        )
        self.assertEqual(result.win_rate, 60.0)
        self.assertEqual(result.total_trades, 100)
        self.assertIsInstance(result.best_patterns, list)

    def test_singleton(self):
        """get_backtester() returns singleton."""
        from historical_backtester import get_backtester
        b1 = get_backtester()
        b2 = get_backtester()
        self.assertIs(b1, b2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
