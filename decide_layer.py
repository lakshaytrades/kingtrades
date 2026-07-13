"""
Decide Layer — HMM regime detection, Kalman trend filter, Bayesian conviction scoring.
Combines all orient/observe signals into a final conviction score and direction bias.
"""

import logging
import sqlite3
import os
import math
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# HMM states
HMM_STATES = ["BULL_TREND", "BEAR_TREND", "VOLATILE", "RANGING"]


class HMMRegimeDetector:
    """
    4-state Hidden Markov Model on discretized returns.
    Falls back to rule-based detection if hmmlearn unavailable.
    """

    def __init__(self):
        self._model = None
        self._fitted = False
        self._try_init_hmm()

    def _try_init_hmm(self):
        try:
            from hmmlearn.hmm import GaussianHMM
            self._model = GaussianHMM(n_components=4, covariance_type="diag",
                                      n_iter=200, random_state=42)
        except ImportError:
            logger.debug("hmmlearn not installed — using rule-based HMM fallback")

    def detect_state(self, closes: np.ndarray) -> str:
        if closes is None or len(closes) < 20:
            return "UNKNOWN"
        closes = np.asarray(closes, dtype=float)
        returns = np.diff(np.log(closes + 1e-10)).reshape(-1, 1)

        if self._model is not None:
            try:
                if not self._fitted and len(returns) >= 50:
                    self._model.fit(returns)
                    self._fitted = True
                if self._fitted:
                    states = self._model.predict(returns)
                    current_state = int(states[-1])
                    return HMM_STATES[current_state % 4]
            except Exception as e:
                logger.debug(f"HMM predict error: {e}")

        # Rule-based fallback
        return self._rule_based(closes, returns)

    def _rule_based(self, closes: np.ndarray, returns: np.ndarray) -> str:
        vol = float(np.std(returns[-20:])) if len(returns) >= 20 else 0.01
        mean_ret = float(np.mean(returns[-20:])) if len(returns) >= 20 else 0

        # High volatility regime
        if vol > 0.025:
            return "VOLATILE"

        # Trending up/down
        mom = closes[-1] / closes[-20] - 1 if closes[-20] != 0 else 0
        ema10 = float(np.mean(closes[-10:]))
        ema20 = float(np.mean(closes[-20:]))

        if mom > 0.03 and ema10 > ema20:
            return "BULL_TREND"
        if mom < -0.03 and ema10 < ema20:
            return "BEAR_TREND"

        return "RANGING"


class KalmanTrendFilter:
    """
    2-state Kalman filter: state = [price_level, price_velocity].
    Provides optimal trend estimate and velocity (momentum) signal.
    """

    def __init__(self):
        # Process noise covariance
        self.Q = np.array([[1e-4, 0], [0, 1e-4]])
        # Measurement noise
        self.R = np.array([[1e-2]])
        # State transition matrix
        self.F = np.array([[1, 1], [0, 1]])
        # Observation matrix
        self.H = np.array([[1, 0]])

    def filter(self, prices: np.ndarray) -> dict:
        """Run Kalman filter on price series. Returns trend level, velocity, deviation."""
        if prices is None or len(prices) < 5:
            return {"level": 0.0, "velocity": 0.0, "deviation": 0.0, "trend_score": 0.0}

        prices = np.asarray(prices, dtype=float)
        n = len(prices)

        # Initial state
        x = np.array([[prices[0]], [0.0]])
        P = np.eye(2) * 1.0

        levels = []
        velocities = []

        for z in prices:
            # Predict
            x_pred = self.F @ x
            P_pred = self.F @ P @ self.F.T + self.Q

            # Update (Kalman gain)
            S = self.H @ P_pred @ self.H.T + self.R
            K = P_pred @ self.H.T @ np.linalg.inv(S)
            innovation = np.array([[z]]) - self.H @ x_pred
            x = x_pred + K @ innovation
            P = (np.eye(2) - K @ self.H) @ P_pred

            levels.append(float(x[0, 0]))
            velocities.append(float(x[1, 0]))

        level = levels[-1]
        velocity = velocities[-1]
        current = prices[-1]
        deviation = (current - level) / level if level != 0 else 0

        # Normalize velocity to a trend score [-5, +5]
        price_scale = np.std(np.diff(prices)) if len(prices) > 1 else 1.0
        trend_score = velocity / (price_scale + 1e-10) * 5
        trend_score = max(-5.0, min(5.0, trend_score))

        return {
            "level": level,
            "velocity": velocity,
            "deviation": deviation,
            "trend_score": trend_score,
        }


class BayesianConvictionScorer:
    """
    Bayesian conviction: P(success | pattern_type, regime, session) from trade history.
    Uses Wilson score for confidence interval.
    Falls back to priors if no history.
    """

    # Priors (base rates from literature)
    PRIORS = {
        "BULL_TREND": {"win_rate": 0.58, "n": 20},
        "BEAR_TREND": {"win_rate": 0.55, "n": 20},
        "VOLATILE": {"win_rate": 0.45, "n": 20},
        "RANGING": {"win_rate": 0.50, "n": 20},
        "UNKNOWN": {"win_rate": 0.50, "n": 10},
        "default": {"win_rate": 0.52, "n": 15},
    }

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), "data", "trade_journal.db")
        self.db_path = db_path

    def get_conviction(self, regime: str, wyckoff_phase: str = "UNKNOWN",
                       elliott_wave: str = "UNKNOWN",
                       direction: str = "LONG") -> dict:
        """Returns {conviction: float, ci_low: float, ci_high: float}."""
        wins, total = self._query_history(regime, wyckoff_phase, direction)
        prior = self.PRIORS.get(regime, self.PRIORS["default"])

        # Bayesian update: posterior = (prior_wins + wins) / (prior_n + total)
        prior_wins = prior["win_rate"] * prior["n"]
        prior_n = prior["n"]

        posterior_n = prior_n + total
        posterior_wins = prior_wins + wins
        conviction = posterior_wins / posterior_n if posterior_n > 0 else 0.5

        # Elliott wave adjustment
        if "BULL_IMPULSE" in elliott_wave and direction == "LONG":
            conviction = min(0.95, conviction + 0.05)
        elif "BEAR_IMPULSE" in elliott_wave and direction == "SHORT":
            conviction = min(0.95, conviction + 0.05)
        elif "CORRECTION" in elliott_wave:
            conviction = max(0.30, conviction - 0.05)

        # Wilson score confidence interval
        ci_low, ci_high = self._wilson_ci(conviction, posterior_n)

        return {
            "conviction": conviction,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "confidence_interval": (ci_low, ci_high),
            "n_samples": int(posterior_n),
        }

    def _query_history(self, regime: str, wyckoff_phase: str, direction: str):
        """Query trade_journal.db for historical win rate in this context."""
        wins = 0
        total = 0
        try:
            if not os.path.exists(self.db_path):
                return wins, total
            conn = sqlite3.connect(self.db_path, timeout=5)
            cur = conn.cursor()
            # Try to get win/loss by regime; fallback if columns missing
            try:
                cur.execute("""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                    FROM trades
                    WHERE direction = ?
                    LIMIT 1000
                """, (direction,))
                row = cur.fetchone()
                if row and row[0]:
                    total = int(row[0])
                    wins = int(row[1]) if row[1] else 0
            except sqlite3.Error:
                pass
            conn.close()
        except Exception as e:
            logger.debug(f"Bayesian DB query: {e}")
        return wins, total

    def _wilson_ci(self, p: float, n: float, z: float = 1.645) -> tuple:
        """Wilson score confidence interval at 90% CI (z=1.645)."""
        if n == 0:
            return (0.3, 0.7)
        center = (p + z**2 / (2 * n)) / (1 + z**2 / n)
        margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / (1 + z**2 / n)
        return (max(0.0, center - margin), min(1.0, center + margin))


class DecideLayer:
    """Combines HMM, Kalman, and Bayesian conviction into a final decision."""

    def __init__(self):
        self.hmm = HMMRegimeDetector()
        self.kalman = KalmanTrendFilter()
        self.bayesian = BayesianConvictionScorer()

    def decide(self, ctx, prices: Optional[np.ndarray] = None,
               volumes: Optional[np.ndarray] = None) -> dict:
        """
        ctx: OODAContext dataclass (accessed as ctx.regime, ctx.wyckoff_phase, etc.)
        Returns dict with conviction, direction_bias, confidence_interval, hmm_state, kalman_trend.
        """
        result = {
            "conviction": 0.5,
            "direction_bias": "NEUTRAL",
            "confidence_interval": (0.4, 0.6),
            "hmm_state": "UNKNOWN",
            "kalman_trend": 0.0,
        }

        # 1. HMM state
        hmm_state = "UNKNOWN"
        if prices is not None and len(prices) >= 20:
            try:
                hmm_state = self.hmm.detect_state(np.asarray(prices, dtype=float))
                result["hmm_state"] = hmm_state
            except Exception as e:
                logger.debug(f"HMM state error: {e}")

        # 2. Kalman filter
        kalman_result = {"trend_score": 0.0, "velocity": 0.0}
        if prices is not None and len(prices) >= 5:
            try:
                kalman_result = self.kalman.filter(np.asarray(prices, dtype=float))
                result["kalman_trend"] = kalman_result.get("trend_score", 0.0)
            except Exception as e:
                logger.debug(f"Kalman error: {e}")

        # 3. Bayesian conviction
        regime = getattr(ctx, "regime", hmm_state)
        wyckoff = getattr(ctx, "wyckoff_phase", "UNKNOWN")
        elliott = getattr(ctx, "elliott_wave", "UNKNOWN")

        # Determine provisional direction from Kalman + HMM
        trend_score = kalman_result.get("trend_score", 0.0)
        if hmm_state == "BULL_TREND" or trend_score > 1.5:
            provisional_dir = "LONG"
        elif hmm_state == "BEAR_TREND" or trend_score < -1.5:
            provisional_dir = "SHORT"
        else:
            provisional_dir = "LONG"  # default for Bayesian lookup

        try:
            bayes = self.bayesian.get_conviction(regime, wyckoff, elliott, provisional_dir)
            result["conviction"] = bayes["conviction"]
            result["confidence_interval"] = bayes["confidence_interval"]
        except Exception as e:
            logger.debug(f"Bayesian error: {e}")

        # 4. Direction bias: weighted vote
        bull_score = 0.0
        bear_score = 0.0

        # HMM vote
        if hmm_state == "BULL_TREND":
            bull_score += 2
        elif hmm_state == "BEAR_TREND":
            bear_score += 2

        # Kalman vote
        if trend_score > 1.0:
            bull_score += trend_score
        elif trend_score < -1.0:
            bear_score += abs(trend_score)

        # Observe layer scores (from ctx)
        macro = getattr(ctx, "macro_score", 0.0)
        sentiment = getattr(ctx, "sentiment_score", 0.0)
        flow = getattr(ctx, "flow_score", 0.0)
        combined_observe = macro + sentiment + flow
        if combined_observe > 3:
            bull_score += 1.5
        elif combined_observe < -3:
            bear_score += 1.5

        # Wyckoff
        wyckoff = getattr(ctx, "wyckoff_phase", "UNKNOWN")
        if wyckoff in ("MARKUP", "SPRING"):
            bull_score += 1.5
        elif wyckoff in ("MARKDOWN", "DISTRIBUTION", "UTAD"):
            bear_score += 1.5

        # Elliott
        if "BULL_IMPULSE" in elliott:
            bull_score += 1.5
        elif "BEAR_IMPULSE" in elliott:
            bear_score += 1.5

        # Smart money + gamma
        sm = getattr(ctx, "smart_money_score", 0.0)
        gamma = getattr(ctx, "gamma_score", 0.0)
        if sm + gamma > 3:
            bull_score += 1
        elif sm + gamma < -3:
            bear_score += 1

        # Final bias
        margin = abs(bull_score - bear_score)
        if bull_score > bear_score and margin > 1.5:
            result["direction_bias"] = "BULL"
        elif bear_score > bull_score and margin > 1.5:
            result["direction_bias"] = "BEAR"
        else:
            result["direction_bias"] = "NEUTRAL"

        return result
