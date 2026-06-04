"""
stat_arb_cointegration.py — Statistical Arbitrage via Engle-Granger Cointegration

DE Shaw and Millennium's core strategy. Finds stock PAIRS where the spread is
statistically mean-reverting (cointegrated), then trades the deviation.

Key distinction from simple correlation:
  - Correlated stocks can diverge permanently (e.g. AAPL and MSFT moving apart
    due to fundamentals)
  - COINTEGRATED stocks have a long-run equilibrium: the spread ALWAYS reverts
    to its mean, regardless of how far it deviates

Algorithm:
  1. Download 60 days of daily closes for all watchlist symbols
  2. Run Engle-Granger test on all valid pairs
     Step 1: regress log(Y) on log(X) → get hedge ratio β
     Step 2: test the residual (spread) for stationarity (ADF test)
     ADF p-value < 0.05 → cointegrated
  3. For cointegrated pairs, track Z-score of spread
  4. Z-score > 2.0 → short the expensive leg, long the cheap leg
  5. Z-score > 3.0 → higher confidence signal

No statsmodels needed — implements ADF test manually using scipy.stats.

Score output:
  Z-score > 3.0 AND direction aligns: +15
  Z-score > 2.0 AND direction aligns: +10
  Z-score < -2.0 (spread compressed, about to expand away): -5

Cache:
  Cointegration pairs: 4 hours (stable relationships)
  Z-scores: 15 minutes (spread moves with prices)

Pairs universe: top 40 liquid US stocks (pre-screened for adequate history)
"""

import logging
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Stocks to test for cointegration (large-cap with liquid options = best pairs)
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "INTC", "QCOM",  # Semis + Tech
    "GOOGL", "META", "AMZN", "NFLX",                  # Internet
    "GS", "JPM", "MS", "BAC", "WFC", "C",             # Financials
    "XOM", "CVX", "COP",                               # Energy
    "JNJ", "PFE", "ABBV",                              # Healthcare
    "SPY", "QQQ",                                      # ETF pairs
    "TSLA", "RIVN",                                    # EV
    "COIN", "MSTR",                                    # Crypto-adjacent
]

# Thresholds
_P_VALUE_THRESHOLD = 0.05   # ADF p-value for cointegration acceptance
_ZSCORE_ENTRY      = 2.0    # z-score to trigger signal
_ZSCORE_STRONG     = 3.0    # z-score for strong signal
_LOOKBACK_DAYS     = 60     # days of history for cointegration test
_Z_LOOKBACK        = 20     # rolling window for z-score computation

# Caches
_coint_pairs: List[Dict] = []        # [{sym1, sym2, hedge_ratio, ts}]
_coint_cache_ts: float    = 0.0
_COINT_TTL: float         = 14400.0  # 4 hours

_zscore_cache: Dict[str, Tuple[float, float]] = {}  # {pair_key: (z, ts)}
_Z_TTL: float             = 900.0    # 15 minutes

_price_data: Dict[str, np.ndarray] = {}  # {symbol: close_array}
_price_ts: float = 0.0
_PRICE_TTL: float = 3600.0  # 1 hour


# ── Manual ADF test (no statsmodels) ─────────────────────────────────────────

def _adf_pvalue(series: np.ndarray) -> float:
    """
    Simplified Augmented Dickey-Fuller test.
    Returns approximate p-value for H0: series has unit root (non-stationary).
    Low p-value (< 0.05) → reject unit root → series is stationary.

    Uses OLS regression: Δy_t = α + β·y_{t-1} + ε
    Test statistic = β_hat / SE(β_hat)
    MacKinnon critical values approximated.
    """
    from scipy import stats
    try:
        y = np.array(series, dtype=float)
        if len(y) < 20:
            return 1.0
        dy = np.diff(y)
        y_lag = y[:-1]
        # Remove NaN/inf
        mask = np.isfinite(dy) & np.isfinite(y_lag)
        if mask.sum() < 15:
            return 1.0
        dy, y_lag = dy[mask], y_lag[mask]
        slope, intercept, r_value, p_value, std_err = stats.linregress(y_lag, dy)
        if std_err < 1e-12:
            return 1.0
        t_stat = slope / std_err
        # MacKinnon (1994) critical values approximation for n→∞:
        # 1%: -3.43, 5%: -2.86, 10%: -2.57
        # Map t_stat to approximate p-value
        if t_stat < -3.43:
            return 0.01
        elif t_stat < -2.86:
            return 0.01 + (t_stat - (-3.43)) / ((-2.86) - (-3.43)) * 0.04
        elif t_stat < -2.57:
            return 0.05 + (t_stat - (-2.86)) / ((-2.57) - (-2.86)) * 0.05
        elif t_stat < -1.95:
            return 0.10 + (t_stat - (-2.57)) / ((-1.95) - (-2.57)) * 0.15
        else:
            return min(1.0, 0.25 + (t_stat + 1.95) * 0.10)
    except Exception:
        return 1.0


def _engle_granger_test(log_x: np.ndarray, log_y: np.ndarray) -> Tuple[float, float]:
    """
    Engle-Granger cointegration test.
    Returns (hedge_ratio β, adf_pvalue of residual).
    """
    from scipy import stats
    try:
        if len(log_x) < 30 or len(log_y) < 30:
            return 1.0, 1.0
        slope, intercept, *_ = stats.linregress(log_x, log_y)
        spread = log_y - (slope * log_x + intercept)
        p = _adf_pvalue(spread)
        return slope, p
    except Exception:
        return 1.0, 1.0


def _fetch_price_data() -> Dict[str, np.ndarray]:
    """Fetch 60-day daily close prices for all universe symbols."""
    global _price_data, _price_ts
    if _time.time() - _price_ts < _PRICE_TTL and _price_data:
        return _price_data
    try:
        import yfinance as yf
        data = {}
        # Batch download (faster than individual)
        tickers = " ".join(UNIVERSE)
        df = yf.download(tickers, period="90d", interval="1d",
                         auto_adjust=True, progress=False, group_by="ticker")
        if df.empty:
            return _price_data
        for sym in UNIVERSE:
            try:
                if sym in df.columns.get_level_values(0):
                    closes = df[sym]["Close"].dropna().values[-_LOOKBACK_DAYS:]
                elif "Close" in df.columns:
                    closes = df["Close"].dropna().values[-_LOOKBACK_DAYS:]
                else:
                    continue
                if len(closes) >= 30:
                    data[sym] = closes.astype(float)
            except Exception:
                continue
        if data:
            _price_data = data
            _price_ts = _time.time()
            logger.debug(f"coint: loaded {len(data)} symbols for cointegration")
    except Exception as e:
        logger.debug(f"coint price fetch: {e}")
    return _price_data


def _build_cointegrated_pairs(prices: Dict[str, np.ndarray]) -> List[Dict]:
    """Find all cointegrated pairs in the universe. Returns sorted list by strength."""
    pairs = []
    symbols = sorted(prices.keys())
    for i, s1 in enumerate(symbols):
        for j, s2 in enumerate(symbols):
            if j <= i:
                continue
            x = prices[s1]
            y = prices[s2]
            # Align lengths
            n = min(len(x), len(y))
            if n < 30:
                continue
            x, y = x[-n:], y[-n:]
            log_x, log_y = np.log(x + 1e-9), np.log(y + 1e-9)
            hedge, p = _engle_granger_test(log_x, log_y)
            if p < _P_VALUE_THRESHOLD:
                spread = log_y - (hedge * log_x)
                pairs.append({
                    "sym1": s1, "sym2": s2,
                    "hedge": hedge,
                    "p_value": p,
                    "spread_mean": float(spread.mean()),
                    "spread_std":  float(spread.std()) + 1e-9,
                    "ts": _time.time(),
                })
    # Sort by p-value (most cointegrated first), keep top 30
    pairs.sort(key=lambda d: d["p_value"])
    return pairs[:30]


def _refresh_pairs_if_needed():
    global _coint_pairs, _coint_cache_ts
    if _time.time() - _coint_cache_ts < _COINT_TTL and _coint_pairs:
        return
    prices = _fetch_price_data()
    if not prices:
        return
    t0 = _time.time()
    _coint_pairs = _build_cointegrated_pairs(prices)
    _coint_cache_ts = _time.time()
    elapsed = _time.time() - t0
    logger.info(
        f"coint: found {len(_coint_pairs)} cointegrated pairs "
        f"(p<0.05) in {elapsed:.1f}s"
    )


def _get_current_zscore(pair: Dict, prices: Dict[str, np.ndarray]) -> Optional[float]:
    """Compute current spread z-score for a pair using latest prices."""
    sym1, sym2 = pair["sym1"], pair["sym2"]
    key = f"{sym1}_{sym2}"
    cached = _zscore_cache.get(key)
    if cached:
        z, ts = cached
        if _time.time() - ts < _Z_TTL:
            return z

    try:
        x = prices.get(sym1)
        y = prices.get(sym2)
        if x is None or y is None:
            return None
        n = min(len(x), len(y), _Z_LOOKBACK + 5)
        x, y = x[-n:], y[-n:]
        log_x, log_y = np.log(x + 1e-9), np.log(y + 1e-9)
        hedge = pair["hedge"]
        spread = log_y - (hedge * log_x)
        # Rolling z-score over _Z_LOOKBACK
        if len(spread) < _Z_LOOKBACK:
            return None
        recent = spread[-_Z_LOOKBACK:]
        z = (spread[-1] - recent.mean()) / (recent.std() + 1e-9)
        _zscore_cache[key] = (float(z), _time.time())
        return float(z)
    except Exception:
        return None


def get_cointegration_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Check if symbol is in any cointegrated pair with an actionable signal.
    Returns (score_delta, reason).

    Logic:
      - Find all pairs containing this symbol
      - Compute z-score for each pair
      - If z-score > threshold AND direction aligns with reversion:
        (z > 2 means sym2 expensive relative to sym1 → sell sym2, buy sym1)
      - Score by z-score magnitude
    """
    try:
        _refresh_pairs_if_needed()
        if not _coint_pairs:
            return 0.0, ""

        prices = _fetch_price_data()
        if not prices:
            return 0.0, ""

        best_delta = 0.0
        best_reason = ""

        for pair in _coint_pairs:
            sym1, sym2 = pair["sym1"], pair["sym2"]
            if symbol not in (sym1, sym2):
                continue

            z = _get_current_zscore(pair, prices)
            if z is None:
                continue

            # Determine what the spread deviation means for each symbol:
            # spread = log(sym2) - hedge * log(sym1)
            # z > 0 → sym2 EXPENSIVE relative to sym1 → trade: SHORT sym2, LONG sym1
            # z < 0 → sym2 CHEAP relative to sym1 → trade: LONG sym2, SHORT sym1

            if symbol == sym2:
                # sym2: z > 0 = expensive = should go SHORT (or avoid LONG)
                reversion_dir = "SHORT" if z > 0 else "LONG"
            else:
                # sym1: z > 0 = sym2 expensive = sym1 is cheap = should LONG sym1
                reversion_dir = "LONG" if z > 0 else "SHORT"

            abs_z = abs(z)

            if direction in ("LONG", "BUY") and reversion_dir == "LONG":
                if abs_z >= _ZSCORE_STRONG:
                    delta = +15.0
                elif abs_z >= _ZSCORE_ENTRY:
                    delta = +10.0
                else:
                    delta = 0.0
            elif direction in ("SHORT", "SELL") and reversion_dir == "SHORT":
                if abs_z >= _ZSCORE_STRONG:
                    delta = +15.0
                elif abs_z >= _ZSCORE_ENTRY:
                    delta = +10.0
                else:
                    delta = 0.0
            elif abs_z >= _ZSCORE_STRONG:
                # Direction opposite to reversion — penalise (about to snap back against us)
                delta = -5.0
            else:
                delta = 0.0

            if abs(delta) > abs(best_delta):
                partner = sym2 if symbol == sym1 else sym1
                best_delta = delta
                best_reason = (
                    f"COINT {symbol}/{partner} z={z:+.2f} p={pair['p_value']:.3f} "
                    f"revert→{reversion_dir}"
                )

        return best_delta, best_reason

    except Exception as e:
        logger.debug(f"coint signal {symbol}: {e}")
        return 0.0, ""


def get_cointegration_report() -> List[Dict]:
    """Return list of active cointegrated pairs (for logging/dashboard)."""
    _refresh_pairs_if_needed()
    return [
        {
            "pair": f"{p['sym1']}/{p['sym2']}",
            "p_value": round(p["p_value"], 4),
            "hedge": round(p["hedge"], 3),
        }
        for p in _coint_pairs
    ]
