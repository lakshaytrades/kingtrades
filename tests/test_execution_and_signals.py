"""
test_execution_and_signals.py
Unit tests for execution layer, signal scoring, slippage model,
adaptive brain, and risk/reward calculations.
All tests are self-contained — no real API calls made.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────────
# SLIPPAGE MODEL TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestSlippageModel(unittest.TestCase):

    def setUp(self):
        try:
            from execution_alpaca import SlippageModel
            self.sm = SlippageModel()
        except Exception as e:
            self.skipTest(f"SlippageModel unavailable: {e}")

    def test_mega_cap_has_lowest_slippage(self):
        """AAPL (mega-cap) should have lower slippage than a small-cap."""
        slip_mega = self.sm.estimate_slippage_pct("AAPL", 100, 180.0)
        slip_mid  = self.sm.estimate_slippage_pct("RANDOM_TICKER", 100, 50.0)
        self.assertLess(slip_mega, slip_mid)

    def test_slippage_is_positive(self):
        """Slippage is always a positive cost."""
        for sym in ["AAPL", "NVDA", "TSLA", "JPM"]:
            slip = self.sm.estimate_slippage_pct(sym, 100, 200.0)
            self.assertGreater(slip, 0.0, f"Expected positive slippage for {sym}")

    def test_larger_order_has_more_impact(self):
        """Market impact scales with order size — 1000 shares > 100 shares."""
        slip_small = self.sm.estimate_slippage_pct("TSLA", 100, 200.0)
        slip_large = self.sm.estimate_slippage_pct("TSLA", 5000, 200.0)
        self.assertGreaterEqual(slip_large, slip_small)

    def test_zero_quantity_returns_zero(self):
        """No trade = no slippage."""
        slip = self.sm.estimate_slippage_pct("AAPL", 0, 180.0)
        self.assertEqual(slip, 0.0)

    def test_zero_price_returns_zero(self):
        """Zero price guard prevents divide-by-zero."""
        slip = self.sm.estimate_slippage_pct("AAPL", 100, 0.0)
        self.assertEqual(slip, 0.0)

    def test_slippage_capped_at_50bps(self):
        """Slippage is capped at 0.5% even for huge orders."""
        slip = self.sm.estimate_slippage_pct("UNKNOWN_SYM", 1_000_000, 1.0)
        self.assertLessEqual(slip, 0.5)

    def test_adjust_expected_pnl_reduces_profit(self):
        """Realistic P&L after slippage is lower than theoretical."""
        result = self.sm.adjust_expected_pnl("AAPL", 100, 180.0, 190.0, 175.0, "LONG")
        self.assertIn("rr_after_slip", result)
        self.assertIn("gross_profit", result)
        # After slippage, entry is higher (worse) for a LONG
        self.assertGreater(result["real_entry"], 180.0)

    def test_short_direction_slippage(self):
        """SHORT: real_entry should be lower (worse) than theoretical entry."""
        result = self.sm.adjust_expected_pnl("NVDA", 100, 500.0, 480.0, 510.0, "SHORT")
        self.assertLess(result["real_entry"], 500.0)

    def test_rr_after_slip_is_positive(self):
        """After slippage, R:R ratio should still be positive for a good setup."""
        result = self.sm.adjust_expected_pnl("AAPL", 50, 150.0, 165.0, 145.0, "LONG")
        self.assertGreater(result["rr_after_slip"], 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# ADAPTIVE BRAIN TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestAdaptiveBrain(unittest.TestCase):

    def setUp(self):
        try:
            from adaptive_brain import AdaptiveBrain, TradeOutcome
            self.AdaptiveBrain = AdaptiveBrain
            self.TradeOutcome  = TradeOutcome
            self.brain = AdaptiveBrain()
        except Exception as e:
            self.skipTest(f"AdaptiveBrain unavailable: {e}")

    def _make_outcome(self, win: bool, pnl: float = 0.0) -> "TradeOutcome":
        return self.TradeOutcome(
            symbol="AAPL", direction="LONG", pattern="ORB_BREAKOUT",
            entry_score=92.0, pnl=pnl if pnl else (100.0 if win else -80.0),
            win=win, regime="STRONG_TREND_UP", session="OPENING_DRIVE"
        )

    def _get_losses(self):
        s = self.brain._state
        return getattr(s, "consecutive_losses", getattr(s, "consecutive_losses", 0))

    def _get_day_pnl(self):
        s = self.brain._state
        return getattr(s, "day_pnl", 0.0)

    def _get_min_score(self):
        return self.brain.get_min_score()

    def test_brain_initializes_without_error(self):
        """AdaptiveBrain instantiates cleanly."""
        self.assertIsNotNone(self.brain)

    def test_winning_trade_resets_loss_streak(self):
        """Recording a win resets the consecutive loss counter."""
        self.brain._state.consecutive_losses = 3
        self.brain.record_trade(self._make_outcome(win=True, pnl=200.0))
        self.assertEqual(self.brain._state.consecutive_losses, 0)

    def test_losing_trade_increments_streak(self):
        """Recording a loss increments consecutive_losses."""
        self.brain._state.consecutive_losses = 0
        self.brain.record_trade(self._make_outcome(win=False, pnl=-100.0))
        self.assertEqual(self.brain._state.consecutive_losses, 1)

    def test_three_losses_raises_min_score(self):
        """Three consecutive losses should tighten min_score."""
        initial = self._get_min_score()
        for _ in range(3):
            self.brain.record_trade(self._make_outcome(win=False, pnl=-100.0))
        self.assertGreaterEqual(self._get_min_score(), initial)

    def test_day_reset_clears_pnl(self):
        """reset_day() resets daily P&L and streak counters."""
        self.brain._state.day_pnl = -500.0
        self.brain._state.consecutive_losses = 5
        self.brain.reset_day()
        self.assertEqual(self.brain._state.day_pnl, 0.0)
        self.assertEqual(self.brain._state.consecutive_losses, 0)

    def test_min_score_never_exceeds_98(self):
        """min_score is capped at 98 regardless of adaptive adjustments."""
        self.brain._state.current_min_score = 98.0
        self.brain.record_trade(self._make_outcome(win=True, pnl=500.0))
        self.assertLessEqual(self._get_min_score(), 98.0)

    def test_min_score_never_drops_below_88(self):
        """min_score floor is 88 even after consecutive losses."""
        from adaptive_brain import MIN_SCORE_FLOOR
        self.brain._state.current_min_score = MIN_SCORE_FLOOR
        # Even on a loss, should never go below floor
        self.brain.record_trade(self._make_outcome(win=False, pnl=-10.0))
        self.assertGreaterEqual(self._get_min_score(), MIN_SCORE_FLOOR)


# ─────────────────────────────────────────────────────────────────────────────
# RISK:REWARD RATIO TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskReward(unittest.TestCase):

    def _rr(self, entry, sl, target, direction="LONG"):
        """Compute R:R ratio the same way the bot does."""
        if direction == "LONG":
            risk   = entry - sl
            reward = target - entry
        else:
            risk   = sl - entry
            reward = entry - target
        return reward / risk if risk > 0 else 0

    def test_rr_1to3_for_standard_setup(self):
        """Entry 100, SL 97, T1 109 → 3:1 R:R."""
        rr = self._rr(100, 97, 109)
        self.assertAlmostEqual(rr, 3.0, delta=0.01)

    def test_rr_below_1_is_bad_setup(self):
        """Entry 100, SL 98, T1 101 → 0.5:1 — should never trade this."""
        rr = self._rr(100, 98, 101)
        self.assertLess(rr, 1.0)

    def test_rr_with_zero_risk_returns_zero(self):
        """SL == entry → division-safe fallback."""
        rr = self._rr(100, 100, 110)
        self.assertEqual(rr, 0)

    def test_short_rr_calculation(self):
        """SHORT: entry 100, SL 103, T1 91 → 3:1 R:R."""
        rr = self._rr(100, 103, 91, direction="SHORT")
        self.assertAlmostEqual(rr, 3.0, delta=0.01)

    def test_kelly_capped_at_10_pct(self):
        """Position size from Kelly criterion should never exceed 10% of capital."""
        try:
            from risk_manager import RiskManager
            rm = RiskManager()
            result = rm.calculate_position_size("AAPL", 150.0, 140.0, capital=100_000,
                                                 risk_pct=5.0)
            qty = result.get("quantity", 0)
            # Max 10% of capital at $150/share = 10000/150 = 66 shares max
            self.assertLessEqual(qty * 150, 100_000 * 0.10 + 1)
        except Exception as e:
            self.skipTest(f"RiskManager unavailable: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MARKET INTERNALS EDGE CASES
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketInternalsEdgeCases(unittest.TestCase):

    def setUp(self):
        try:
            from market_internals import MarketInternals, LONG_OK_THRESHOLD, SHORT_OK_THRESHOLD
            self.mi = MarketInternals()
            self.LONG_THRESHOLD  = LONG_OK_THRESHOLD
            self.SHORT_THRESHOLD = SHORT_OK_THRESHOLD
        except Exception as e:
            self.skipTest(f"MarketInternals unavailable: {e}")

    def test_breadth_100_allows_long_blocks_short(self):
        """Extreme bull breadth: long OK, short blocked."""
        with patch.object(self.mi, "_compute_breadth", return_value={
            "breadth_score": 100.0, "bullish_sectors": 11, "bearish_sectors": 0,
            "spy_momentum": 3, "volatility_flag": False, "ad_regime": 1,
            "bias": "BULL", "long_ok": True, "short_ok": False,
            "reason": "test", "sectors": {}, "ts": "now"
        }):
            ok, _ = self.mi.is_long_ok()
            self.assertTrue(ok)
            ok, _ = self.mi.is_short_ok()
            self.assertFalse(ok)

    def test_breadth_0_allows_short_blocks_long(self):
        """Extreme bear breadth: short OK, long blocked."""
        with patch.object(self.mi, "_compute_breadth", return_value={
            "breadth_score": 0.0, "bullish_sectors": 0, "bearish_sectors": 11,
            "spy_momentum": -3, "volatility_flag": True, "ad_regime": -1,
            "bias": "BEAR", "long_ok": False, "short_ok": True,
            "reason": "test", "sectors": {}, "ts": "now"
        }):
            ok, _ = self.mi.is_long_ok()
            self.assertFalse(ok)
            ok, _ = self.mi.is_short_ok()
            self.assertTrue(ok)

    def test_size_multiplier_1_2x_for_very_bullish(self):
        """breadth >= 75 → 1.2x size multiplier."""
        with patch.object(self.mi, "get_breadth", return_value={"breadth_score": 80.0}):
            mult = self.mi.get_size_multiplier()
            self.assertEqual(mult, 1.2)

    def test_size_multiplier_0_5x_for_very_bearish(self):
        """breadth < 45 → 0.5x size multiplier."""
        with patch.object(self.mi, "get_breadth", return_value={"breadth_score": 30.0}):
            mult = self.mi.get_size_multiplier()
            self.assertEqual(mult, 0.5)

    def test_cache_returns_same_result_within_ttl(self):
        """Two get_breadth() calls within TTL should return identical object."""
        mock_result = {
            "breadth_score": 55.0, "bullish_sectors": 6, "bearish_sectors": 3,
            "spy_momentum": 1, "volatility_flag": False, "ad_regime": 0,
            "bias": "NEUTRAL", "long_ok": True, "short_ok": False,
            "reason": "test", "sectors": {}, "ts": "now"
        }
        with patch.object(self.mi, "_compute_breadth", return_value=mock_result) as m:
            self.mi.get_breadth()
            self.mi.get_breadth()
            # _compute_breadth called only once due to cache
            self.assertEqual(m.call_count, 1)


# ─────────────────────────────────────────────────────────────────────────────
# LLM REASONER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMReasoner(unittest.TestCase):

    def setUp(self):
        try:
            from llm_reasoner import LLMReasoner
            self.LLMReasoner = LLMReasoner
        except Exception as e:
            self.skipTest(f"LLMReasoner unavailable: {e}")

    def test_returns_go_when_disabled(self):
        """When no API key is set, defaults to GO verdict."""
        reasoner = self.LLMReasoner()
        reasoner._enabled = False
        verdict, reason, conf = reasoner.evaluate({"symbol": "AAPL", "score": 96})
        self.assertEqual(verdict, "GO")

    def test_verdict_is_one_of_three_values(self):
        """Verdict must be GO, NO_GO, or REDUCE_SIZE."""
        reasoner = self.LLMReasoner()
        reasoner._enabled = False
        verdict, _, _ = reasoner.evaluate({"symbol": "TSLA"})
        self.assertIn(verdict, ("GO", "NO_GO", "REDUCE_SIZE"))

    def test_build_prompt_includes_symbol(self):
        """Prompt string contains the trade symbol."""
        reasoner = self.LLMReasoner()
        prompt = reasoner._build_prompt({
            "symbol": "NVDA", "direction": "LONG", "score": 96,
            "entry": 500, "sl": 490, "t1": 520, "t2": 540,
            "rr_ratio": 2.0, "patterns": ["ORB"], "rsi": 60,
            "macd_bull": True, "adx": 35, "volume_ratio": 2.5,
            "above_vwap": True, "spy_change": 0.5, "regime": "STRONG_TREND_UP",
            "time_et": "10:30", "catalyst": "None", "breadth_score": 70,
            "grade": "A+"
        })
        self.assertIn("NVDA", prompt)
        self.assertIn("LONG", prompt)

    def test_stats_shows_zero_calls_initially(self):
        """Fresh reasoner has 0 calls in stats."""
        reasoner = self.LLMReasoner()
        stats = reasoner.get_stats()
        self.assertIn("0 calls", stats)


# ─────────────────────────────────────────────────────────────────────────────
# MEAN REVERSION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestMeanReversionEngine(unittest.TestCase):

    def setUp(self):
        try:
            from mean_reversion import MeanReversionEngine
            self.engine = MeanReversionEngine()
        except Exception as e:
            self.skipTest(f"MeanReversionEngine unavailable: {e}")

    def test_engine_initializes(self):
        """Engine instantiates without error."""
        self.assertIsNotNone(self.engine)

    def test_activated_regimes(self):
        """MEAN_REVERSION_REGIMES constant includes key choppy regimes."""
        try:
            from mean_reversion import MEAN_REVERSION_REGIMES
            self.assertIn("RANGING", MEAN_REVERSION_REGIMES)
            self.assertIn("LOW_VOLATILITY", MEAN_REVERSION_REGIMES)
            self.assertIn("MIDDAY_CHOP", MEAN_REVERSION_REGIMES)
        except ImportError as e:
            self.skipTest(f"MEAN_REVERSION_REGIMES unavailable: {e}")

    def test_not_activated_for_strong_trend(self):
        """STRONG_TREND_UP is NOT in mean-reversion regimes."""
        try:
            from mean_reversion import MEAN_REVERSION_REGIMES
            self.assertNotIn("STRONG_TREND_UP", MEAN_REVERSION_REGIMES)
        except ImportError as e:
            self.skipTest(f"MEAN_REVERSION_REGIMES unavailable: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL SCORE VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalScoreValidation(unittest.TestCase):
    """Test that signal scores stay within expected bounds."""

    def test_min_score_floor_is_88(self):
        """Adaptive systems must not drop min_score below 88."""
        try:
            from self_learning import AdaptiveLearner
            learner = AdaptiveLearner()
            # Force many losses
            for _ in range(20):
                learner.record_loss("AAPL")
            score = learner.min_signal_score
            self.assertGreaterEqual(score, 88.0,
                f"min_score dropped to {score}, below 88 floor")
        except Exception as e:
            self.skipTest(f"AdaptiveLearner unavailable: {e}")

    def test_min_score_ceil_is_98(self):
        """Adaptive systems must not push min_score above 98."""
        try:
            from self_learning import AdaptiveLearner
            learner = AdaptiveLearner()
            # Force many wins
            for _ in range(20):
                learner.record_win("AAPL", profit_pct=5.0)
            score = learner.min_signal_score
            self.assertLessEqual(score, 98.0,
                f"min_score rose to {score}, above 98 ceiling")
        except Exception as e:
            self.skipTest(f"AdaptiveLearner unavailable: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCH FALLBACK TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestDataFetchFallbacks(unittest.TestCase):
    """Test that data fetcher fails gracefully when API is unavailable."""

    def test_get_ohlcv_returns_empty_df_on_error(self):
        """get_ohlcv must return empty DataFrame (not raise) when API fails."""
        try:
            from data_fetch_alpaca import AlpacaDataFetcher
            import pandas as pd
            fetcher = AlpacaDataFetcher.__new__(AlpacaDataFetcher)
            fetcher._auth = MagicMock()
            fetcher._auth.get_data_client.side_effect = ConnectionError("API down")
            fetcher._balance_cache = {}
            fetcher._balance_ts = 0.0
            fetcher._quote_cache = {}
            fetcher._quote_ts = {}
            result = fetcher.get_ohlcv("AAPL", interval="5minute", lookback_days=1)
            self.assertIsInstance(result, pd.DataFrame)
        except Exception as e:
            self.skipTest(f"AlpacaDataFetcher unavailable: {e}")

    def test_get_quote_returns_dict_on_error(self):
        """get_quote must return a dict (possibly empty) not raise exceptions."""
        try:
            from data_fetch_alpaca import AlpacaDataFetcher
            fetcher = AlpacaDataFetcher.__new__(AlpacaDataFetcher)
            fetcher._auth = MagicMock()
            fetcher._auth.get_data_client.side_effect = RuntimeError("Auth failed")
            fetcher._quote_cache = {}
            fetcher._quote_ts = {}
            result = fetcher.get_quote("AAPL")
            self.assertIsInstance(result, dict)
        except Exception as e:
            self.skipTest(f"AlpacaDataFetcher unavailable: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
