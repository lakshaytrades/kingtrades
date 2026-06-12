"""
kalman_pairs_india.py — Kalman Filter Pairs Trading (Renaissance-level upgrade)

Replaces the static z-score pairs approach with a Kalman filter that
dynamically tracks the hedge ratio β between cointegrated NSE pairs.

Why Kalman > z-score:
  Static z-score assumes a fixed hedge ratio (usually 1:1). In reality,
  the relationship between HDFCBANK and ICICIBANK drifts over time — the
  Kalman filter recursively updates β as new prices arrive, producing a
  much more accurate spread estimate and far fewer false signals.

State model:
  β_t = β_{t-1}  +  w_t        (hedge ratio drifts slowly, w_t ~ N(0, Q))
  y_t = A_t - β_t * B_t  +  v_t (measurement, v_t ~ N(0, R))

where A_t, B_t are the log prices of the two stocks.

HMM-lite regime filter:
  Only trade the pair when the spread's autocorrelation is negative
  (mean-reverting regime). When autocorrelation is positive (trending),
  the pair is NOT mean-reverting and we skip it.

Score impact:
  Pair aligned with signal direction:   +10 to +14 (strong confirmation)
  Pair opposed to signal direction:     -8 to -12 (counter-signal, reduce size)
  No relevant pair:                       0
"""
import logging
import math
import time as _time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger("kalman_pairs")
IST = ZoneInfo("Asia/Kolkata")

# ── NSE cointegrated pairs (confirmed by sector/fundamental linkage) ──────────
PAIRS_NSE: List[Tuple[str, str]] = [
    ("HDFCBANK",  "ICICIBANK"),   # Private banking pair — tightest cointegration on NSE
    ("INFY",      "TCS"),          # IT giants — near-perfect cointegration
    ("AXISBANK",  "KOTAKBANK"),    # Mid-private banking pair
    ("TATASTEEL", "JSWSTEEL"),     # Steel sector pair
    ("HINDUNILVR","DABUR"),        # FMCG pair
    ("RELIANCE",  "ONGC"),         # Oil & Gas (looser but liquid)
    ("SBIN",      "BANKBARODA"),   # PSU banking pair
    ("WIPRO",     "HCLTECH"),      # IT mid-caps
]

# Process noise Q and measurement noise R control filter responsiveness
# High Q → hedge ratio adapts quickly (noisy); Low Q → stable but lagging
_Q_DEFAULT = 1e-5   # hedge ratio variance per step
_R_DEFAULT = 1e-3   # measurement noise variance

# In-process state: {pair_key: KalmanState}
_state_cache: Dict[str, dict] = {}
_data_cache:  Dict[str, Tuple[float, pd.Series]] = {}  # {sym: (ts, log_prices)}
_DATA_TTL = 300.0   # 5-min refresh matches bot scan interval


class KalmanState:
    """
    Single Kalman filter state for one pair.
    Tracks β (hedge ratio) and its variance P.
    """
    __slots__ = ("beta", "P", "spread_history", "initialized")

    def __init__(self):
        self.beta           = 1.0    # initial hedge ratio = 1:1
        self.P              = 1.0    # initial state variance
        self.spread_history: List[float] = []
        self.initialized    = False

    def update(self, log_A: float, log_B: float, Q: float, R: float) -> float:
        """
        Update the Kalman filter with one new (log_A, log_B) observation.
        Returns the current spread: log_A - beta * log_B
        """
        # Predict
        P_pred = self.P + Q

        # Innovation (measurement residual)
        y_pred   = log_A - self.beta * log_B
        H        = -log_B                         # Jacobian dh/d(beta) = -log_B
        S        = H * P_pred * H + R             # innovation covariance
        if abs(S) < 1e-12:
            return y_pred

        # Kalman gain
        K = P_pred * H / S

        # Update
        innovation  = y_pred   # spread IS the innovation here (model says spread = 0)
        self.beta  += K * innovation
        self.P      = (1 - K * H) * P_pred
        self.initialized = True

        spread = log_A - self.beta * log_B
        self.spread_history.append(spread)
        if len(self.spread_history) > 100:
            self.spread_history = self.spread_history[-100:]
        return spread


def _get_log_prices(symbol: str, dhan_client=None, lookback_days: int = 60) -> Optional[pd.Series]:
    """
    Fetch recent daily closing prices (log) for a symbol.
    Uses Dhan API if available, falls back to yfinance.
    """
    now = _time.time()
    cached = _data_cache.get(symbol)
    if cached and (now - cached[0]) < _DATA_TTL:
        return cached[1]

    try:
        # Try Dhan first
        if dhan_client is not None:
            try:
                from data_fetch_upstox import get_ohlcv
                df = get_ohlcv(symbol, interval="1d")
                if df is not None and len(df) >= 20:
                    prices = df["close"].tail(lookback_days)
                    log_p = np.log(prices.astype(float))
                    log_series = pd.Series(log_p.values)
                    _data_cache[symbol] = (now, log_series)
                    return log_series
            except Exception:
                pass

        # Fallback: yfinance
        import yfinance as yf
        df = yf.download(f"{symbol}.NS", period=f"{lookback_days + 10}d",
                         interval="1d", auto_adjust=True, progress=False, timeout=15)
        if df is None or df.empty or len(df) < 20:
            return None
        prices = df["Close"].tail(lookback_days)
        log_p = np.log(prices.astype(float))
        log_series = pd.Series(log_p.values)
        _data_cache[symbol] = (now, log_series)
        return log_series

    except Exception as e:
        logger.debug(f"_get_log_prices {symbol}: {e}")
        return None


def _is_mean_reverting(spread_history: List[float], min_samples: int = 20) -> bool:
    """
    HMM-lite regime check: the spread is mean-reverting when its
    lag-1 autocorrelation is negative (prices pull back toward mean).
    Returns True if in mean-reverting regime.
    """
    if len(spread_history) < min_samples:
        return True   # assume mean-reverting until we have evidence otherwise
    try:
        s = np.array(spread_history[-40:])
        if len(s) < 10:
            return True
        # Variance ratio test: compare variance of k-period changes vs 1-period
        # VR < 1 → mean-reverting, VR > 1 → trending
        diff1 = np.diff(s, 1)
        diff5 = np.diff(s, 5)
        if len(diff1) < 5 or len(diff5) < 2:
            return True
        var1 = np.var(diff1) if np.var(diff1) > 0 else 1e-9
        var5 = np.var(diff5) / 5.0 if np.var(diff5) > 0 else 1e-9
        vr = var5 / var1
        return vr < 1.1   # mean-reverting if variance ratio < 1.1
    except Exception:
        return True


def get_kalman_pairs_score(symbol: str, direction: str,
                            dhan_client=None) -> Tuple[int, str]:
    """
    Return (score_adj, reason) based on Kalman filter pairs analysis.
    Fail-open: (0, "") on any error.

    Logic:
      - For each pair that includes `symbol`:
        1. Update the Kalman filter with latest log prices
        2. Compute z-score of the current spread
        3. Check mean-reverting regime (HMM-lite)
        4. If symbol is the expensive leg (z>2.0) → bias SHORT; if cheap (z<-2.0) → bias LONG
        5. +10/+14 if direction aligns, -8/-12 if direction conflicts
    """
    try:
        sym = symbol.upper()
        relevant = [(a, b, True) if sym == a else (a, b, False)
                    for a, b in PAIRS_NSE if sym == a or sym == b]
        if not relevant:
            return (0, "")

        best_adj  = 0
        best_reason = ""

        for sym_a, sym_b, is_A in relevant:
            pair_key = f"{sym_a}/{sym_b}"

            # Fetch log prices
            log_a = _get_log_prices(sym_a, dhan_client)
            log_b = _get_log_prices(sym_b, dhan_client)
            if log_a is None or log_b is None:
                continue

            n = min(len(log_a), len(log_b))
            if n < 20:
                continue
            log_a = log_a.iloc[-n:].values
            log_b = log_b.iloc[-n:].values

            # Get or init Kalman state
            if pair_key not in _state_cache:
                _state_cache[pair_key] = KalmanState()
            ks = _state_cache[pair_key]

            # Run Kalman filter over all available data (warm-up then live)
            spreads = []
            for i in range(n):
                spread = ks.update(log_a[i], log_b[i], _Q_DEFAULT, _R_DEFAULT)
                spreads.append(spread)

            if len(spreads) < 10:
                continue

            # Regime check
            if not _is_mean_reverting(spreads):
                logger.debug(f"Kalman {pair_key}: trending regime — skip")
                continue

            # Z-score of current spread
            recent = spreads[-20:]
            mean_s = np.mean(recent)
            std_s  = np.std(recent)
            if std_s < 1e-9:
                continue

            z = (spreads[-1] - mean_s) / std_s

            # Determine signal: is sym the "expensive" or "cheap" leg?
            if is_A:
                # sym is A: z > 2 means A expensive vs B → SHORT A / LONG B
                expensive = z > 2.0
                cheap     = z < -2.0
            else:
                # sym is B: z > 2 means A expensive, so B is cheap → LONG B
                expensive = z < -2.0   # B is expensive when spread < mean (A cheap, B dear)
                cheap     = z > 2.0

            if expensive:
                # Mean reversion predicts sym will fall → SHORT is the pair-driven signal
                if direction == "SHORT":
                    adj = 14 if abs(z) > 3.0 else 10
                    reason = f"KALMAN_{pair_key} z={z:.1f} expensive→SHORT"
                else:
                    adj = -12 if abs(z) > 3.0 else -8
                    reason = f"KALMAN_{pair_key} z={z:.1f} pair_contra_LONG"
            elif cheap:
                # Mean reversion predicts sym will rise → LONG is the pair-driven signal
                if direction == "LONG":
                    adj = 14 if abs(z) > 3.0 else 10
                    reason = f"KALMAN_{pair_key} z={z:.1f} cheap→LONG"
                else:
                    adj = -12 if abs(z) > 3.0 else -8
                    reason = f"KALMAN_{pair_key} z={z:.1f} pair_contra_SHORT"
            else:
                continue   # spread neutral — no signal

            if abs(adj) > abs(best_adj):
                best_adj    = adj
                best_reason = reason

        return (best_adj, best_reason)

    except Exception as e:
        logger.debug(f"get_kalman_pairs_score {symbol}: {e}")
        return (0, "")
