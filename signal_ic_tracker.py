"""
signal_ic_tracker.py — Information Coefficient Tracker + Bayesian Signal Weights v1.0

Renaissance Technologies' edge is NOT having better signals — it's knowing
WHICH signals are working RIGHT NOW and weighting them accordingly.

Information Coefficient (IC):
  IC = Spearman rank correlation between signal value and subsequent return
  IC > 0.05: signal has predictive power (useful)
  IC > 0.10: strong signal (weight 2×)
  IC < 0.00: signal is noise or negatively predictive (weight 0)

This module:
  1. Records every signal's predicted direction + confidence
  2. When the trade closes, records actual outcome
  3. Computes rolling 20-trade IC per signal source
  4. Weights each signal's contribution by its current IC
  5. Publishes IC decay alerts when a signal stops working

Bayesian Signal Weighting (Thompson Sampling):
  Each signal source has Beta(α, β) prior.
  α = number of correctly predicted outcomes
  β = number of incorrectly predicted outcomes
  Weight = α / (α + β) = rolling accuracy
  This is the same mechanism used by Google/Facebook for ad ranking.

Score modification:
  We don't re-score here — we provide a WEIGHT MULTIPLIER (0.2-1.5)
  that the caller applies to signal deltas BEFORE adding to final score.
  Poorly performing signals get shrunk toward 0 contribution.

Usage:
  register_signal_prediction(signal_name, predicted_direction, confidence)
  record_signal_outcome(signal_name, predicted_direction, was_correct)
  get_signal_weight(signal_name) → 0.2 to 1.5
  get_all_ic_report() → dict of {signal: IC, weight, n_trades}
"""

import logging
import time as _time
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ── Per-signal state ──────────────────────────────────────────────────────────

class SignalState:
    __slots__ = ["alpha", "beta", "outcomes", "signals", "n_total"]

    def __init__(self):
        self.alpha   = 1.0   # prior: 1 win  (start neutral)
        self.beta    = 1.0   # prior: 1 loss (start neutral)
        self.outcomes = deque(maxlen=30)   # 1=correct, 0=wrong
        self.signals  = deque(maxlen=30)   # confidence values at prediction time
        self.n_total  = 0

    @property
    def accuracy(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def ic(self) -> float:
        """Spearman IC between signal confidence and binary outcome."""
        if len(self.outcomes) < 5:
            return 0.0
        try:
            from scipy.stats import spearmanr
            sig = np.array(self.signals)
            out = np.array(self.outcomes, dtype=float)
            r, p = spearmanr(sig, out)
            return float(r) if np.isfinite(r) else 0.0
        except Exception:
            return 0.0

    def weight(self) -> float:
        """Thompson-sampling weight: accuracy mapped to [0.2, 1.5]."""
        acc = self.accuracy
        ic  = self.ic
        # Combine accuracy and IC
        if self.n_total < 5:
            return 1.0   # not enough data, neutral weight
        # High IC + high accuracy → boost. Low IC → shrink.
        combined = acc * 0.6 + max(ic, 0.0) * 4.0 * 0.4
        return float(np.clip(combined * 1.5, 0.2, 1.5))


_signal_states: Dict[str, SignalState] = {}


def _get_state(signal_name: str) -> SignalState:
    if signal_name not in _signal_states:
        _signal_states[signal_name] = SignalState()
    return _signal_states[signal_name]


# ── Public API ────────────────────────────────────────────────────────────────

def register_signal_prediction(
    signal_name: str,
    predicted_direction: str,
    confidence: float,        # e.g. signal_score / 100
) -> None:
    """Record that a signal predicted a direction with given confidence."""
    try:
        st = _get_state(signal_name)
        # Confidence: 0.5 = neutral, 1.0 = max bull, 0.0 = max bear
        st.signals.append(float(np.clip(confidence, 0.0, 1.0)))
    except Exception as e:
        logger.debug(f"signal_ic_tracker.register suppressed: {e}")


def record_signal_outcome(
    signal_name: str,
    was_correct: bool,
) -> None:
    """
    Record whether the signal's prediction was correct.
    Call after each closed trade.
    """
    try:
        st = _get_state(signal_name)
        correct = 1 if was_correct else 0
        st.outcomes.append(correct)
        st.n_total += 1
        if was_correct:
            st.alpha += 1.0
        else:
            st.beta  += 1.0
        # Log IC decay warnings
        if st.n_total > 10 and st.n_total % 10 == 0:
            ic = st.ic
            if ic < 0.0:
                logger.warning(
                    f"signal_ic_tracker: [{signal_name}] IC={ic:.3f} — "
                    f"signal may be NEGATIVELY predictive (weight={st.weight():.2f})"
                )
    except Exception as e:
        logger.debug(f"signal_ic_tracker.record suppressed: {e}")


def record_trade_outcome(signal_scores: Dict[str, float], was_win: bool) -> None:
    """
    Bulk-record outcome across all signals that contributed to a trade.
    signal_scores: {signal_name: score_delta} — only +ve contributors matter.
    """
    try:
        for name, delta in signal_scores.items():
            predicted_correct = (delta > 0) == was_win
            record_signal_outcome(name, predicted_correct)
    except Exception as e:
        logger.debug(f"signal_ic_tracker.record_trade suppressed: {e}")


def get_signal_weight(signal_name: str) -> float:
    """
    Return weight multiplier for this signal based on recent IC.
    Returns 1.0 if no history (neutral — don't penalise unknown signals).
    """
    try:
        return _get_state(signal_name).weight()
    except Exception:
        return 1.0


def get_composite_weight(signal_deltas: Dict[str, float]) -> float:
    """
    Compute IC-weighted composite of multiple signal deltas.
    Returns a single weighted score adjustment.
    """
    try:
        total_w = 0.0
        weighted_sum = 0.0
        for name, delta in signal_deltas.items():
            w = get_signal_weight(name)
            weighted_sum += delta * w
            total_w += abs(w)
        if total_w == 0:
            return 0.0
        return float(weighted_sum)
    except Exception:
        return 0.0


def get_all_ic_report() -> Dict[str, dict]:
    """Return IC and weight for all tracked signals (for diagnostics)."""
    try:
        return {
            name: {
                "ic":       round(st.ic, 4),
                "accuracy": round(st.accuracy, 3),
                "weight":   round(st.weight(), 3),
                "n_trades": st.n_total,
                "alpha":    round(st.alpha, 1),
                "beta":     round(st.beta, 1),
            }
            for name, st in _signal_states.items()
            if st.n_total > 0
        }
    except Exception:
        return {}


def get_low_ic_signals() -> List[str]:
    """Return signal names with IC < 0 (should be disabled or reversed)."""
    try:
        return [
            name for name, st in _signal_states.items()
            if st.n_total >= 10 and st.ic < -0.02
        ]
    except Exception:
        return []


# ── Convenience: pre-register known signal sources ───────────────────────────

KNOWN_SIGNALS = [
    "kalman", "hmm", "factor_alpha", "vpin",
    "ml_ensemble", "execution_optimizer", "regime_v2", "portfolio_optimizer",
    "dark_pool", "short_squeeze", "gex", "sector_rotation", "breadth",
    "smart_money", "vol_surface", "ofi", "csm", "vwap_reclaim",
    "power_hour", "pairs", "tod_rvol", "sortino",
    "random_forest", "ml_gate", "elliott_wave", "harmonic",
    "vanna_charm", "seasonal", "minervini", "weinstein",
]

for _s in KNOWN_SIGNALS:
    _get_state(_s)   # initialise with neutral priors
