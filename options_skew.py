"""
options_skew.py — Options Skew + Net Gamma Exposure (GEX) (v16.0)
Free replica of SpotGamma GEX + TastyTrade skew analysis.

Part A — Options Skew:
  Measures the implied-volatility difference between OTM puts and OTM calls.
  High positive skew (puts >> calls) = institutional hedging / fear = bearish for LONG.
  Negative skew (calls >> puts) = unusual bullishness = bullish for LONG.

Part B — Net Gamma Exposure (GEX):
  Computes aggregate dealer gamma across all strikes.
  Positive GEX: dealers are long gamma → price-stabilizing (pinning) → harder to break out.
  Negative GEX: dealers are short gamma → price-amplifying → breakout/breakdown accelerates.

Cache TTL: 30 minutes (1800 seconds). Fail-open on any error.
"""

import logging
import threading
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_TTL = 1800  # 30 minutes

# ── Thread-safe caches ────────────────────────────────────────────────────────
_skew_lock = threading.Lock()
_skew_cache: Dict[str, Tuple[float, str, float]] = {}   # sym → (delta, reason, skew_val)
_skew_ts: Dict[str, float] = {}

_gex_lock = threading.Lock()
_gex_cache: Dict[str, Tuple[float, str, float]] = {}    # sym → (delta, reason, gex_val)
_gex_ts: Dict[str, float] = {}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_atm_strike(strikes, spot: float) -> float:
    """Return the strike closest to the current spot price."""
    return min(strikes, key=lambda k: abs(k - spot))


def _find_otm_put_strike(strikes, spot: float, otm_pct: float = 0.05) -> Optional[float]:
    """Return strike closest to spot * (1 - otm_pct) from below."""
    target = spot * (1.0 - otm_pct)
    put_strikes = [k for k in strikes if k <= spot]
    if not put_strikes:
        return None
    return min(put_strikes, key=lambda k: abs(k - target))


def _find_otm_call_strike(strikes, spot: float, otm_pct: float = 0.05) -> Optional[float]:
    """Return strike closest to spot * (1 + otm_pct) from above."""
    target = spot * (1.0 + otm_pct)
    call_strikes = [k for k in strikes if k >= spot]
    if not call_strikes:
        return None
    return min(call_strikes, key=lambda k: abs(k - target))


def _get_nearest_expiry(expirations, min_days: int = 7):
    """Find the nearest expiry with at least min_days to expiration."""
    from datetime import datetime
    today = datetime.now()
    valid = []
    for exp in expirations:
        try:
            dt = datetime.strptime(exp, "%Y-%m-%d")
            days = (dt - today).days
            if days >= min_days:
                valid.append((days, exp))
        except Exception:
            continue
    if not valid:
        return None
    valid.sort()
    return valid[0][1]


def _fetch_options_data(symbol: str):
    """
    Fetch options chain for symbol. Returns (calls_df, puts_df, spot, expiry) or raises.
    Uses nearest expiry >= 7 days.
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol)

    # Get spot price
    info = ticker.fast_info
    spot = float(getattr(info, 'last_price', None) or getattr(info, 'regularMarketPrice', None) or 0.0)
    if spot <= 0:
        # Fallback: use history
        hist = ticker.history(period="2d", interval="1d")
        if len(hist) > 0:
            spot = float(hist["Close"].iloc[-1])
        else:
            raise ValueError(f"Cannot determine spot price for {symbol}")

    expirations = ticker.options
    if not expirations:
        raise ValueError(f"No options data for {symbol}")

    expiry = _get_nearest_expiry(expirations, min_days=7)
    if expiry is None:
        raise ValueError(f"No valid expiry >= 7 days for {symbol}")

    chain = ticker.option_chain(expiry)
    return chain.calls, chain.puts, spot, expiry


# ── Part A: Options Skew ──────────────────────────────────────────────────────

def _compute_skew(symbol: str) -> Tuple[float, str]:
    """
    Compute OTM put IV - OTM call IV skew.
    Returns (skew_value, description).
    Positive = put premium (fear); Negative = call premium (bullish).
    """
    calls, puts, spot, expiry = _fetch_options_data(symbol)

    # Extract strikes and IV
    call_strikes = calls["strike"].tolist()
    put_strikes = puts["strike"].tolist()
    all_strikes = sorted(set(call_strikes + put_strikes))

    otm_put_strike = _find_otm_put_strike(all_strikes, spot, otm_pct=0.05)
    otm_call_strike = _find_otm_call_strike(all_strikes, spot, otm_pct=0.05)

    if otm_put_strike is None or otm_call_strike is None:
        return 0.0, f"SKEW_NO_STRIKES exp={expiry}"

    # Get IVs for the selected strikes
    put_row = puts[abs(puts["strike"] - otm_put_strike) < 0.01]
    call_row = calls[abs(calls["strike"] - otm_call_strike) < 0.01]

    if put_row.empty or call_row.empty:
        # Widen tolerance
        put_row = puts.iloc[(puts["strike"] - otm_put_strike).abs().argsort()[:1]]
        call_row = calls.iloc[(calls["strike"] - otm_call_strike).abs().argsort()[:1]]

    put_iv = float(put_row["impliedVolatility"].iloc[0]) if not put_row.empty else None
    call_iv = float(call_row["impliedVolatility"].iloc[0]) if not call_row.empty else None

    if put_iv is None or call_iv is None:
        return 0.0, f"SKEW_NO_IV exp={expiry}"

    skew = put_iv - call_iv
    return skew, (
        f"put_IV={put_iv:.2f} call_IV={call_iv:.2f} "
        f"skew={skew:+.3f} exp={expiry}"
    )


def get_skew_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Returns (score_delta, reason). 30-min TTL. Fail-open.

    Scoring:
      Skew > 0.15  → -8 LONG / +8 SHORT  (put premium = fear = institutional hedging)
      Skew < -0.05 → +6 LONG / -6 SHORT  (call premium = unusual bullishness)
      Skew 0.05-0.15 → neutral
    """
    sym = symbol.upper()
    now = _time.monotonic()

    with _skew_lock:
        cached = _skew_cache.get(sym)
        ts = _skew_ts.get(sym, 0.0)
        if cached is not None and (now - ts) < CACHE_TTL:
            delta, reason, _ = cached
            # Flip cached delta for actual direction if needed
            # (cache stores raw skew value)
            raw_skew = cached[2]
            score_delta, reason_str = _skew_delta_from_value(raw_skew, direction, reason)
            return score_delta, reason_str

    try:
        skew_val, desc = _compute_skew(sym)
        score_delta, reason_str = _skew_delta_from_value(skew_val, direction, desc)

        with _skew_lock:
            _skew_cache[sym] = (score_delta, reason_str, skew_val)
            _skew_ts[sym] = _time.monotonic()

        return score_delta, reason_str

    except Exception as exc:
        logger.debug("[SKEW] %s error (fail-open): %s", sym, exc)
        return 0.0, "SKEW_UNAVAILABLE"


def _skew_delta_from_value(skew_val: float, direction: str, desc: str) -> Tuple[float, str]:
    """Convert raw skew value to score delta based on direction."""
    if direction == "LONG":
        if skew_val > 0.15:
            return -8.0, f"SKEW_HIGH_PUT_PREMIUM {desc}"
        elif skew_val < -0.05:
            return 6.0, f"SKEW_CALL_PREMIUM_BULLISH {desc}"
        else:
            return 0.0, f"SKEW_NEUTRAL {desc}"
    else:  # SHORT
        if skew_val > 0.15:
            return 8.0, f"SKEW_HIGH_PUT_PREMIUM_BEARS {desc}"
        elif skew_val < -0.05:
            return -6.0, f"SKEW_CALL_PREMIUM_CAUTION_SHORT {desc}"
        else:
            return 0.0, f"SKEW_NEUTRAL {desc}"


# ── Part B: Net Gamma Exposure (GEX) ─────────────────────────────────────────

def _compute_gex(symbol: str, current_price: float) -> Tuple[float, str]:
    """
    Compute net GEX across all strikes and open interest.

    GEX formula:
      For calls: +gamma_call × OI_call × 100 × spot
      For puts:  -gamma_put  × OI_put  × 100 × spot
      Net GEX = sum of all contributions

    Returns (gex_value, description).
    """
    import pandas as pd

    calls, puts, spot, expiry = _fetch_options_data(symbol)
    if current_price > 0:
        spot = current_price  # use caller's price if provided

    # Calls: positive GEX
    call_gex = 0.0
    for _, row in calls.iterrows():
        try:
            g = float(row.get("gamma", 0) or 0)
            oi = float(row.get("openInterest", 0) or 0)
            if g > 0 and oi > 0:
                call_gex += g * oi * 100 * spot
        except Exception:
            continue

    # Puts: negative GEX
    put_gex = 0.0
    for _, row in puts.iterrows():
        try:
            g = float(row.get("gamma", 0) or 0)
            oi = float(row.get("openInterest", 0) or 0)
            if g > 0 and oi > 0:
                put_gex += g * oi * 100 * spot
        except Exception:
            continue

    net_gex = call_gex - put_gex
    desc = (
        f"net_GEX={net_gex/1e6:.1f}M "
        f"call_GEX={call_gex/1e6:.1f}M put_GEX={put_gex/1e6:.1f}M "
        f"exp={expiry}"
    )
    return net_gex, desc


def get_gex_score(symbol: str, direction: str, current_price: float) -> Tuple[float, str]:
    """
    Returns (score_delta, reason). 30-min TTL. Fail-open.

    Scoring:
      High positive GEX (>500M): -4 (price pinned — hard to break out)
      Strong negative GEX (<-200M): +8 LONG if breakout confirmed (gamma acceleration)
      Moderate GEX: neutral / small adjustment
    """
    sym = symbol.upper()
    now = _time.monotonic()

    with _gex_lock:
        cached = _gex_cache.get(sym)
        ts = _gex_ts.get(sym, 0.0)
        if cached is not None and (now - ts) < CACHE_TTL:
            # Re-score from cached raw gex_val
            raw_gex = cached[2]
            score_delta, reason_str = _gex_delta_from_value(raw_gex, direction, cached[1])
            return score_delta, reason_str

    try:
        gex_val, desc = _compute_gex(sym, current_price)
        score_delta, reason_str = _gex_delta_from_value(gex_val, direction, desc)

        with _gex_lock:
            _gex_cache[sym] = (score_delta, reason_str, gex_val)
            _gex_ts[sym] = _time.monotonic()

        return score_delta, reason_str

    except Exception as exc:
        logger.debug("[GEX] %s error (fail-open): %s", sym, exc)
        return 0.0, "GEX_UNAVAILABLE"


def _gex_delta_from_value(gex_val: float, direction: str, desc: str) -> Tuple[float, str]:
    """Convert raw GEX to score delta based on direction."""
    # Thresholds (in raw GEX units; for large-caps, 500M notional is high)
    HIGH_POSITIVE_THRESHOLD = 500_000_000   # $500M
    STRONG_NEGATIVE_THRESHOLD = -200_000_000  # -$200M

    if direction == "LONG":
        if gex_val > HIGH_POSITIVE_THRESHOLD:
            return -4.0, f"GEX_HIGH_POSITIVE_PIN {desc}"
        elif gex_val < STRONG_NEGATIVE_THRESHOLD:
            return 8.0, f"GEX_NEGATIVE_GAMMA_ACCEL_LONG {desc}"
        elif gex_val < 0:
            return 3.0, f"GEX_SLIGHTLY_NEGATIVE_BREAKOUT_FRIENDLY {desc}"
        else:
            return 0.0, f"GEX_NEUTRAL {desc}"
    else:  # SHORT
        if gex_val > HIGH_POSITIVE_THRESHOLD:
            return -4.0, f"GEX_HIGH_POSITIVE_PIN {desc}"
        elif gex_val < STRONG_NEGATIVE_THRESHOLD:
            return 8.0, f"GEX_NEGATIVE_GAMMA_ACCEL_SHORT {desc}"
        elif gex_val < 0:
            return 3.0, f"GEX_SLIGHTLY_NEGATIVE_BREAKDOWN_FRIENDLY {desc}"
        else:
            return 0.0, f"GEX_NEUTRAL {desc}"
