"""
weinstein_stage.py — Stan Weinstein 4-Stage Cycle Analysis (v17.0)

Implements Stan Weinstein's stage analysis using the 30-week (150-day) moving average.

Stage 1: Basing  (flat 30-wk MA, price range-bound) — NEUTRAL
Stage 2: Advancing (price > rising 30-wk MA)       — BUY ZONE
Stage 3: Topping (price flattening near 30-wk MA)  — EXIT
Stage 4: Declining (price < falling 30-wk MA)      — SHORT zone

Thread-safe singleton with 4-hour TTL cache.  Fail-open (returns 0.0).
"""

import logging
import threading
import time as _time
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── TTL cache ─────────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[int, float, float, str]] = {}
_cache_lock = threading.Lock()
_TTL = 14400  # 4 hours


def _cache_get(key: str) -> Tuple[bool, int, float, str]:
    with _cache_lock:
        if key in _cache:
            stage, exp, score, reason = _cache[key]
            if _time.monotonic() < exp:
                return True, stage, score, reason
    return False, 0, 0.0, ""


def _cache_set(key: str, stage: int, score: float, reason: str) -> None:
    with _cache_lock:
        _cache[key] = (stage, _time.monotonic() + _TTL, score, reason)


# ── Stage detection ───────────────────────────────────────────────────────────

def _detect_stage(closes: np.ndarray, volumes: np.ndarray) -> Tuple[int, str]:
    """
    Determine Weinstein stage (1-4) using 150-day SMA.

    Returns (stage, detail_string).
    """
    n = len(closes)
    if n < 155:
        return 1, "insufficient_data"

    price = closes[-1]
    sma150 = float(np.mean(closes[-150:]))
    sma150_prev = float(np.mean(closes[-155:-5]))  # 5 bars ago for slope

    # SMA slope
    sma_slope = (sma150 - sma150_prev) / (sma150_prev + 1e-9)

    # Price relationship to SMA
    price_vs_sma = (price - sma150) / (sma150 + 1e-9)

    # Volume trend (expanding vs contracting)
    vol_recent = float(np.mean(volumes[-20:])) if volumes is not None and len(volumes) >= 20 else 0.0
    vol_prior  = float(np.mean(volumes[-40:-20])) if volumes is not None and len(volumes) >= 40 else vol_recent
    vol_expanding = vol_recent > vol_prior * 1.1

    # Stage determination
    detail = f"price_vs_sma={price_vs_sma:.3f} sma_slope={sma_slope:.4f} vol_exp={vol_expanding}"

    if price_vs_sma > 0.02 and sma_slope > 0.002:
        # Price above rising SMA
        if price_vs_sma > 0.10 and sma_slope > 0.005:
            stage = 2  # Stage 2 established
        else:
            stage = 2  # Stage 2 (could be early)
        return stage, f"Stage2 {detail}"

    elif abs(price_vs_sma) < 0.03 and abs(sma_slope) < 0.002:
        # Price near flat SMA
        if vol_expanding:
            stage = 3  # Topping — volume expansion near flat SMA
        else:
            stage = 1  # Basing
        return stage, f"Stage{stage} {detail}"

    elif price_vs_sma < -0.02 and sma_slope < -0.002:
        # Price below falling SMA
        stage = 4
        return stage, f"Stage4 {detail}"

    elif price_vs_sma > 0 and sma_slope < -0.001:
        # Price above but SMA starting to turn down — late Stage 3
        return 3, f"Stage3_late {detail}"

    elif price_vs_sma < 0 and abs(sma_slope) < 0.002:
        # Price below flat SMA — late Stage 1 or early Stage 4
        return 1, f"Stage1_low {detail}"

    return 1, f"Stage1_default {detail}"


def _fetch_daily_data(symbol: str):
    """Fetch 200+ days of daily OHLCV via yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="14mo", interval="1d")
        if hist is None or hist.empty:
            return None, None
        closes  = hist["Close"].values.astype(float)
        volumes = hist["Volume"].values.astype(float)
        return closes, volumes
    except Exception as exc:
        logger.debug(f"weinstein yfinance fetch error: {exc}")
        return None, None


# ── Public API ────────────────────────────────────────────────────────────────

def get_weinstein_stage(
    symbol: str, df_daily=None
) -> Tuple[int, float, str]:
    """
    Determine Weinstein stage for symbol.

    Returns (stage: int 1-4, score_delta: float, reason: str).
    Fail-open: returns (1, 0.0, "weinstein N/A") on any error.
    4-hour TTL cache.
    """
    cache_key = f"{symbol}:stage"
    hit, stage, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return stage, cached_score, f"weinstein cached: {cached_reason}"

    try:
        closes  = None
        volumes = None

        if df_daily is not None:
            try:
                col_map = {c.lower(): c for c in df_daily.columns}
                close_col = col_map.get("close", None)
                vol_col   = col_map.get("volume", None)
                if close_col:
                    closes  = df_daily[close_col].values.astype(float)
                    volumes = df_daily[vol_col].values.astype(float) if vol_col else None
            except Exception:
                closes = None

        if closes is None or len(closes) < 100:
            closes, volumes = _fetch_daily_data(symbol)

        if closes is None or len(closes) < 50:
            return 1, 0.0, "weinstein: data N/A"

        stage, detail = _detect_stage(closes, volumes)
        _cache_set(cache_key, stage, 0.0, detail)
        return stage, 0.0, detail
    except Exception as exc:
        logger.debug(f"get_weinstein_stage error (fail-open): {exc}")
        return 1, 0.0, "weinstein N/A"


def get_weinstein_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Weinstein stage wrapper returning (score_delta, reason).

    Score (LONG):
      Stage 2 early breakout: +15
      Stage 2 established:    +8
      Stage 1:                +2
      Stage 3:                -8
      Stage 4:                -12

    Score (SHORT):
      Stage 4 early:          +15
      Stage 4 established:    +8
      Stage 2:                -10

    Fail-open: returns (0.0, "weinstein N/A") on any error.
    4-hour TTL cache.
    """
    cache_key = f"{symbol}:{direction}"
    hit, cached_stage, cached_score, cached_reason = _cache_get(cache_key)
    if hit:
        return cached_score, f"weinstein cached({cached_stage}): {cached_reason}"

    try:
        stage, _, detail = get_weinstein_stage(symbol)

        if direction == "LONG":
            if stage == 2:
                # Check if early stage 2 (recently broke out)
                score = 15.0 if "Stage2" in detail and "early" not in detail.lower() else 8.0
            elif stage == 1:
                score = 2.0
            elif stage == 3:
                score = -8.0
            elif stage == 4:
                score = -12.0
            else:
                score = 0.0
        else:  # SHORT
            if stage == 4:
                score = 15.0 if "early" in detail.lower() else 8.0
            elif stage == 3:
                score = 6.0  # Topping — shorting opportunity
            elif stage == 2:
                score = -10.0  # Don't short uptrends
            elif stage == 1:
                score = 2.0   # Neutral
            else:
                score = 0.0

        reason = f"weinstein_stage={stage} {detail}"
        _cache_set(cache_key, stage, score, reason)
        return score, reason
    except Exception as exc:
        logger.debug(f"get_weinstein_score error (fail-open): {exc}")
        return 0.0, "weinstein N/A"
