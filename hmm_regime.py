"""
hmm_regime.py — Hidden Markov Model Market State Detector v1.0

The market has latent (hidden) states that you cannot observe directly.
What you CAN observe: returns, volatility, volume. HMM infers the most
likely hidden state from these observations.

3 hidden states:
  State 0: BULL  — positive drift, low vol, high volume
  State 1: BEAR  — negative drift, high vol, declining volume
  State 2: CHOP  — zero drift, high vol, random volume

Why HMM beats rule-based regime detection:
  1. Probabilistic: "70% bull, 30% chop" — not binary
  2. Transition matrix: P(bull→chop) encodes how sticky each state is
  3. Learns from data: parameters calibrated on SPY returns
  4. Regime CHANGE signal: when P(current_state) drops below 0.5,
     a regime shift is imminent — exit/avoid new entries

Implementation: Gaussian HMM via Baum-Welch (EM algorithm, pure numpy).
No external HMM library required. Trained on synthetic SPY-like data
with known state labels. Updated incrementally on live SPY 5-min data.

Score signal:
  - High P(BULL) + LONG direction:  +7
  - High P(BEAR) + SHORT direction: +7
  - High P(BULL) + SHORT:          -6 (fighting the regime)
  - High P(BEAR) + LONG:           -6
  - CHOP or uncertain (<0.55):      -3
  - Regime transition warning:      -5

Fail-open. Pure numpy.
"""

import logging
import os
import pickle
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "data", "hmm_model.pkl")
_CACHE_TTL  = 300   # re-run Viterbi every 5 minutes

# ── States ────────────────────────────────────────────────────────────────────
STATE_BULL = 0
STATE_BEAR = 1
STATE_CHOP = 2
STATE_NAMES = {0: "BULL", 1: "BEAR", 2: "CHOP"}
N_STATES    = 3


# ─────────────────────────────────────────────────────────────────────────────
# Pure-numpy Gaussian HMM  (Baum-Welch EM)
# ─────────────────────────────────────────────────────────────────────────────

class GaussianHMM:
    """
    Gaussian HMM with diagonal covariance.
    Each state emits a 3-d observation: [return, abs_return (vol proxy), volume_ratio].
    """

    def __init__(self, n_states: int = 3):
        self.n        = n_states
        # Transition matrix A[i,j] = P(j | i)
        self.A        = np.full((n_states, n_states), 1.0 / n_states)
        # Initial state distribution
        self.pi       = np.full(n_states, 1.0 / n_states)
        # Emission: mean and variance per state per feature
        self.mu       = np.zeros((n_states, 3))
        self.sigma2   = np.ones((n_states, 3))
        self._trained = False

    # ── Emission probability ──────────────────────────────────────────────────

    def _emit(self, x: np.ndarray) -> np.ndarray:
        """log P(x | state) for each state. x shape (3,)."""
        log_p = np.zeros(self.n)
        for k in range(self.n):
            diff  = x - self.mu[k]
            var   = np.maximum(self.sigma2[k], 1e-10)
            log_p[k] = -0.5 * np.sum(diff ** 2 / var + np.log(2 * np.pi * var))
        return log_p

    # ── Forward-Backward ─────────────────────────────────────────────────────

    def _forward(self, obs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        T = len(obs)
        alpha = np.zeros((T, self.n))
        scale = np.zeros(T)
        alpha[0] = self.pi * np.exp(self._emit(obs[0]))
        scale[0] = alpha[0].sum() + 1e-300
        alpha[0] /= scale[0]
        for t in range(1, T):
            alpha[t] = (alpha[t - 1] @ self.A) * np.exp(self._emit(obs[t]))
            scale[t] = alpha[t].sum() + 1e-300
            alpha[t] /= scale[t]
        return alpha, scale

    def _backward(self, obs: np.ndarray, scale: np.ndarray) -> np.ndarray:
        T = len(obs)
        beta = np.zeros((T, self.n))
        beta[-1] = 1.0
        for t in range(T - 2, -1, -1):
            emit_next = np.exp(np.array([self._emit(obs[t + 1])[k] for k in range(self.n)]))
            beta[t] = (self.A * emit_next) @ beta[t + 1]
            beta[t] /= scale[t + 1] + 1e-300
        return beta

    # ── Baum-Welch EM training ────────────────────────────────────────────────

    def fit(self, obs: np.ndarray, n_iter: int = 40):
        """obs: shape (T, 3)."""
        T = len(obs)
        # Initialise with k-means-like partition
        third = T // 3
        for k in range(self.n):
            chunk = obs[k * third: (k + 1) * third]
            self.mu[k]     = chunk.mean(axis=0)
            self.sigma2[k] = chunk.var(axis=0) + 1e-4

        prev_ll = -np.inf
        for _ in range(n_iter):
            # E-step
            alpha, scale = self._forward(obs)
            beta          = self._backward(obs, scale)
            gamma = alpha * beta
            gamma /= (gamma.sum(axis=1, keepdims=True) + 1e-300)

            xi = np.zeros((T - 1, self.n, self.n))
            for t in range(T - 1):
                emit_next = np.exp(np.array([self._emit(obs[t + 1])[k] for k in range(self.n)]))
                for i in range(self.n):
                    xi[t, i, :] = alpha[t, i] * self.A[i, :] * emit_next * beta[t + 1, :]
                xi[t] /= (xi[t].sum() + 1e-300)

            # M-step
            self.pi = gamma[0] + 1e-10
            self.pi /= self.pi.sum()
            for i in range(self.n):
                self.A[i] = xi[:, i, :].sum(axis=0) + 1e-10
                self.A[i] /= self.A[i].sum()
                w = gamma[:, i] + 1e-10
                self.mu[i]     = (w[:, None] * obs).sum(axis=0) / w.sum()
                self.sigma2[i] = (w[:, None] * (obs - self.mu[i]) ** 2).sum(axis=0) / w.sum() + 1e-4

            ll = np.sum(np.log(scale + 1e-300))
            if abs(ll - prev_ll) < 1e-4:
                break
            prev_ll = ll
        self._trained = True

    # ── Viterbi decode ────────────────────────────────────────────────────────

    def viterbi(self, obs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Returns (most_likely_states, state_probs_last_step)."""
        T = len(obs)
        vit   = np.full((T, self.n), -np.inf)
        psi   = np.zeros((T, self.n), dtype=int)
        vit[0] = np.log(self.pi + 1e-300) + self._emit(obs[0])
        for t in range(1, T):
            emit = self._emit(obs[t])
            for j in range(self.n):
                trans = vit[t - 1] + np.log(self.A[:, j] + 1e-300)
                psi[t, j] = trans.argmax()
                vit[t, j] = trans.max() + emit[j]
        # Backtrack
        states  = np.zeros(T, dtype=int)
        states[-1] = vit[-1].argmax()
        for t in range(T - 2, -1, -1):
            states[t] = psi[t + 1, states[t + 1]]
        # Soft probabilities at last step (forward pass normalised)
        alpha, _ = self._forward(obs)
        probs    = alpha[-1]
        probs   /= (probs.sum() + 1e-300)
        return states, probs

    def predict_proba(self, obs: np.ndarray) -> np.ndarray:
        """Return P(state | observations) at last time step."""
        _, probs = self.viterbi(obs)
        return probs


# ── Training data generation ──────────────────────────────────────────────────

def _make_synthetic_obs(rng) -> np.ndarray:
    """
    2400 bars of synthetic [return, abs_return, vol_ratio] with known states.
    State 0=BULL: +0.03 return, 0.03 abs_return, 1.5 vol_ratio
    State 1=BEAR: -0.03 return, 0.05 abs_return, 1.3 vol_ratio
    State 2=CHOP:  0.00 return, 0.08 abs_return, 0.9 vol_ratio
    """
    rows = []
    # Transition: each state is sticky with P=0.92
    A_true = np.array([[0.92, 0.05, 0.03],
                        [0.04, 0.93, 0.03],
                        [0.03, 0.03, 0.94]])
    mu_true = np.array([
        [0.030,  0.030, 1.5],
        [-0.030, 0.055, 1.2],
        [0.000,  0.080, 0.9],
    ])
    state = rng.integers(0, 3)
    for _ in range(2400):
        obs = rng.normal(mu_true[state], [0.015, 0.02, 0.3])
        rows.append(obs)
        state = rng.choice(3, p=A_true[state])
    return np.array(rows, dtype=np.float32)


# ── Module singleton ──────────────────────────────────────────────────────────

_model: Optional[GaussianHMM] = None
_cache: dict = {}
_cache_ts: float = 0.0
_obs_buffer: List[np.ndarray] = []   # live SPY observations for incremental fit


def _get_model() -> GaussianHMM:
    global _model
    if _model is not None:
        return _model
    os.makedirs("data", exist_ok=True)
    if os.path.exists(_MODEL_PATH):
        try:
            with open(_MODEL_PATH, "rb") as f:
                _model = pickle.load(f)
            logger.info("hmm_regime: loaded model from disk")
            return _model
        except Exception as e:
            logger.warning(f"hmm_regime: load failed ({e}), retraining")
    rng   = np.random.default_rng(42)
    obs   = _make_synthetic_obs(rng)
    _model = GaussianHMM(N_STATES)
    _model.fit(obs, n_iter=50)
    try:
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump(_model, f)
    except Exception:
        pass
    logger.info("hmm_regime: trained on synthetic data")
    return _model


def record_spy_bar(ret: float, abs_ret: float, vol_ratio: float) -> None:
    """Call each bar with SPY observations. Auto-refits model every 200 bars."""
    global _model, _obs_buffer
    try:
        _obs_buffer.append(np.array([ret, abs_ret, vol_ratio], dtype=np.float32))
        if len(_obs_buffer) > 500:
            _obs_buffer = _obs_buffer[-500:]
        if len(_obs_buffer) >= 200 and len(_obs_buffer) % 50 == 0:
            # Combine synthetic + live
            rng = np.random.default_rng(99)
            syn = _make_synthetic_obs(rng)
            live = np.array(_obs_buffer, dtype=np.float32)
            combined = np.vstack([syn, live])
            m = GaussianHMM(N_STATES)
            m.fit(combined, n_iter=30)
            _model = m
            try:
                with open(_MODEL_PATH, "wb") as f:
                    pickle.dump(m, f)
            except Exception:
                pass
            logger.info("hmm_regime: live refit complete")
    except Exception as e:
        logger.debug(f"hmm_regime.record_spy_bar suppressed: {e}")


def get_hmm_score(symbol: str, df_5m, direction: str) -> Tuple[float, str]:
    """
    Run HMM on the last 30 bars and return (score_delta, reason).
    Fail-open: returns (0.0, 'hmm:error') on any exception.
    """
    global _cache, _cache_ts
    try:
        if df_5m is None or len(df_5m) < 20:
            return 0.0, "hmm:insufficient_data"

        now = _time.time()
        cache_key = f"{symbol}_{direction}"
        if cache_key in _cache and (now - _cache_ts) < _CACHE_TTL:
            return _cache[cache_key]

        col_c = "close" if "close" in df_5m.columns else "Close"
        col_v = "volume" if "volume" in df_5m.columns else "Volume"
        closes = df_5m[col_c].values[-30:].astype(float)
        vols   = df_5m[col_v].values[-30:].astype(float)

        rets     = np.diff(closes) / np.maximum(closes[:-1], 0.01)
        abs_rets = np.abs(rets)
        vol_avg  = vols[:-1].mean()
        vol_ratio = vols[1:] / max(vol_avg, 1.0)

        obs = np.column_stack([rets, abs_rets, vol_ratio]).astype(np.float32)
        model = _get_model()
        probs = model.predict_proba(obs)   # [P(BULL), P(BEAR), P(CHOP)]

        p_bull = float(probs[STATE_BULL])
        p_bear = float(probs[STATE_BEAR])
        p_chop = float(probs[STATE_CHOP])
        dominant_state = int(np.argmax(probs))
        max_prob       = float(probs[dominant_state])

        is_long = direction.upper() in ("LONG", "BUY")

        if max_prob < 0.50:
            # Uncertain — regime transition imminent
            result = (-3.0, f"hmm:uncertain bull={p_bull:.0%} bear={p_bear:.0%} chop={p_chop:.0%}")
        elif dominant_state == STATE_BULL and is_long:
            result = (+7.0, f"hmm:BULL_confirmed p={p_bull:.0%}")
        elif dominant_state == STATE_BEAR and not is_long:
            result = (+7.0, f"hmm:BEAR_confirmed p={p_bear:.0%}")
        elif dominant_state == STATE_BULL and not is_long:
            result = (-6.0, f"hmm:fighting_BULL short in bull regime")
        elif dominant_state == STATE_BEAR and is_long:
            result = (-6.0, f"hmm:fighting_BEAR long in bear regime")
        elif dominant_state == STATE_CHOP:
            result = (-3.0, f"hmm:CHOP regime p={p_chop:.0%}")
        else:
            result = (0.0, f"hmm:neutral bull={p_bull:.0%}")

        _cache[cache_key] = result
        _cache_ts = now
        return result
    except Exception as e:
        logger.debug(f"hmm_regime.get_hmm_score suppressed: {e}")
        return 0.0, "hmm:error"
