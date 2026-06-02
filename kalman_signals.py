"""
kalman_signals.py — Adaptive Kalman Filter Price Signals v1.0

Renaissance Technologies and D.E. Shaw use Kalman filtering to separate
signal from noise in price data. Standard EMAs have fixed lag; Kalman
automatically adjusts based on how "noisy" the current market is.

Three outputs per bar:
  1. Kalman price    — noise-filtered "true" price
  2. Velocity        — rate of change (momentum)
  3. Acceleration    — momentum of momentum (trend conviction)

Why it beats EMA:
  - In trending markets: Kalman gain drops → smooth signal → ride the trend
  - In choppy markets: Kalman gain rises → responsive signal → fade extremes
  - Self-calibrating: no fixed parameter to optimize

Score signal:
  - Velocity aligned with direction + positive acceleration: +8
  - Velocity aligned, acceleration fading: +3
  - Velocity against direction: -6
  - Acceleration turning negative (trend exhaustion): -4 warning

Fail-open. No external deps beyond numpy.
"""

import logging
import time as _time
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Per-symbol Kalman state ───────────────────────────────────────────────────
# State vector: [price, velocity]
# Observation: close price

_states: Dict[str, dict] = {}   # symbol → {x, P, Q, R, last_ts}

# Process noise (how much does the "true" price drift per bar?)
_Q_DEFAULT = np.array([[1e-4, 0], [0, 1e-4]])
# Observation noise (how noisy is the close price as a measurement?)
_R_DEFAULT = np.array([[0.1]])
# State transition matrix (constant velocity model)
_F = np.array([[1.0, 1.0], [0.0, 1.0]])   # price += velocity each step
# Observation matrix (we observe price, not velocity)
_H = np.array([[1.0, 0.0]])


def _init_state(price: float) -> dict:
    return {
        "x": np.array([price, 0.0]),        # [price, velocity]
        "P": np.eye(2) * 1.0,               # initial uncertainty
        "Q": _Q_DEFAULT.copy(),
        "R": _R_DEFAULT.copy(),
        "prev_velocity": 0.0,               # for acceleration
        "history_vel": [],                  # rolling velocity for R adaptation
    }


def update(symbol: str, close: float) -> Tuple[float, float, float]:
    """
    Update Kalman filter with the latest close price.

    Returns (kalman_price, velocity, acceleration).
    velocity   > 0 = trending up
    acceleration > 0 = momentum building, < 0 = momentum fading
    """
    try:
        sym = symbol.upper()
        if sym not in _states:
            _states[sym] = _init_state(close)
            return close, 0.0, 0.0

        st = _states[sym]
        x  = st["x"]
        P  = st["P"]
        Q  = st["Q"]
        R  = st["R"]

        # ── Predict ──────────────────────────────────────────────────────────
        x_pred = _F @ x
        P_pred = _F @ P @ _F.T + Q

        # ── Update ───────────────────────────────────────────────────────────
        z   = np.array([close])
        y   = z - (_H @ x_pred)              # innovation
        S   = _H @ P_pred @ _H.T + R
        K   = P_pred @ _H.T @ np.linalg.inv(S)   # Kalman gain

        x_new = x_pred + K.flatten() * y[0]
        P_new = (np.eye(2) - K @ _H) @ P_pred

        st["x"] = x_new
        st["P"] = P_new

        kalman_price = float(x_new[0])
        velocity     = float(x_new[1])
        acceleration = velocity - st["prev_velocity"]
        st["prev_velocity"] = velocity

        # Adaptive R: if price is jumping a lot, increase measurement noise
        st["history_vel"].append(abs(velocity))
        if len(st["history_vel"]) > 20:
            st["history_vel"].pop(0)
        if len(st["history_vel"]) >= 5:
            vol_estimate = float(np.std(st["history_vel"]))
            # More volatile → increase R so filter is less aggressive
            R_adapted = max(_R_DEFAULT[0, 0], vol_estimate * 2)
            st["R"] = np.array([[R_adapted]])

        return kalman_price, velocity, acceleration
    except Exception as e:
        logger.debug(f"kalman_signals.update({symbol}) suppressed: {e}")
        return close, 0.0, 0.0


def get_kalman_score(symbol: str, df_5m, direction: str) -> Tuple[float, str]:
    """
    Feed last N bars through Kalman and return (score_delta, reason).
    Fail-open: returns (0.0, 'kalman:error') on any exception.
    """
    try:
        if df_5m is None or len(df_5m) < 10:
            return 0.0, "kalman:insufficient_data"

        col  = "close" if "close" in df_5m.columns else "Close"
        prices = df_5m[col].values[-15:].astype(float)

        # Feed bars through filter (warmup on first 10, score on last 5)
        kprice = vel = accel = 0.0
        for p in prices:
            kprice, vel, accel = update(symbol, p)

        is_long = direction.upper() in ("LONG", "BUY")

        # Velocity score
        vel_aligned = (vel > 0) == is_long
        vel_mag     = abs(vel)   # magnitude: how fast?

        if vel_aligned and vel_mag > 0.02 and accel > 0:
            return +8.0, f"kalman:trend_accel vel={vel:.3f} acc={accel:.3f}"
        elif vel_aligned and vel_mag > 0.01 and accel >= 0:
            return +5.0, f"kalman:trend_steady vel={vel:.3f}"
        elif vel_aligned and vel_mag > 0.005:
            return +3.0, f"kalman:trend_weak vel={vel:.3f}"
        elif vel_aligned and accel < 0 and vel_mag < 0.005:
            return -4.0, f"kalman:trend_exhaustion vel={vel:.3f} acc={accel:.3f}"
        elif not vel_aligned and vel_mag > 0.02:
            return -6.0, f"kalman:counter_trend vel={vel:.3f}"
        elif not vel_aligned and vel_mag > 0.01:
            return -3.0, f"kalman:slight_counter vel={vel:.3f}"
        else:
            return 0.0, f"kalman:neutral vel={vel:.4f}"
    except Exception as e:
        logger.debug(f"kalman_signals.get_kalman_score suppressed: {e}")
        return 0.0, "kalman:error"


def get_kalman_state(symbol: str) -> Optional[dict]:
    """Return raw Kalman state for a symbol (for diagnostics)."""
    return _states.get(symbol.upper())
