"""
fear_greed_engine.py — CNN Fear & Greed Index Free Replica (v16.0)
Composite of 7 components, each scored 0-100, averaged into a
Fear & Greed index (0=extreme fear, 100=extreme greed).

Components (all free data via yfinance):
  1. Market Momentum       : SPY vs 125-day MA
  2. VIX Level             : VIX<12=greed, VIX>30=fear
  3. VIX Trend             : VIX vs 50-day MA
  4. Safe Haven Demand     : SPY vs TLT 20-day return spread
  5. Junk Bond Demand      : HYG vs LQD 20-day return spread
  6. Put/Call Ratio        : SPY options OI ratio
  7. Market Breadth        : QQQ vs SPY 5-day return spread

Cache TTL: 1 hour (3600 seconds). Fail-open on any error.
"""

import logging
import threading
import time as _time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_TTL = 3600  # 1 hour

_lock = threading.Lock()
_cached_fg: Optional[float] = None
_last_refresh: float = 0.0


def _score_market_momentum(spy_prices) -> float:
    """SPY vs 125-day MA: above=greed(>50), below=fear(<50)."""
    try:
        if len(spy_prices) < 125:
            return 50.0
        ma125 = float(spy_prices[-125:].mean())
        current = float(spy_prices.iloc[-1])
        if ma125 <= 0:
            return 50.0
        dev_pct = (current - ma125) / ma125 * 100
        # +5% above MA → 80 (greed); -5% below → 20 (fear); scale linearly
        score = 50.0 + dev_pct * 6.0
        return max(0.0, min(100.0, score))
    except Exception:
        return 50.0


def _score_vix_level(vix_prices) -> float:
    """VIX<12=extreme greed(95), VIX 12-20=greed/neutral, VIX 20-30=fear, VIX>30=extreme fear(5)."""
    try:
        vix = float(vix_prices.iloc[-1])
        if vix < 12:
            return 95.0
        elif vix < 15:
            return 80.0
        elif vix < 20:
            return 65.0
        elif vix < 25:
            return 45.0
        elif vix < 30:
            return 25.0
        elif vix < 40:
            return 10.0
        else:
            return 2.0
    except Exception:
        return 50.0


def _score_vix_trend(vix_prices) -> float:
    """VIX vs 50-day MA: rising=fear, falling=greed."""
    try:
        if len(vix_prices) < 50:
            return 50.0
        ma50 = float(vix_prices[-50:].mean())
        current = float(vix_prices.iloc[-1])
        if ma50 <= 0:
            return 50.0
        # VIX rising above MA = fear
        dev_pct = (current - ma50) / ma50 * 100
        score = 50.0 - dev_pct * 5.0
        return max(0.0, min(100.0, score))
    except Exception:
        return 50.0


def _pct_return_20d(prices) -> float:
    """20-day percentage return."""
    try:
        if len(prices) < 21:
            return 0.0
        p_start = float(prices.iloc[-21])
        p_end = float(prices.iloc[-1])
        if p_start <= 0:
            return 0.0
        return (p_end - p_start) / p_start * 100.0
    except Exception:
        return 0.0


def _pct_return_5d(prices) -> float:
    """5-day percentage return."""
    try:
        if len(prices) < 6:
            return 0.0
        p_start = float(prices.iloc[-6])
        p_end = float(prices.iloc[-1])
        if p_start <= 0:
            return 0.0
        return (p_end - p_start) / p_start * 100.0
    except Exception:
        return 0.0


def _score_safe_haven(spy_ret: float, tlt_ret: float) -> float:
    """Stocks outperforming bonds = greed; bonds outperforming = fear."""
    spread = spy_ret - tlt_ret
    score = 50.0 + spread * 4.0
    return max(0.0, min(100.0, score))


def _score_junk_bond(hyg_ret: float, lqd_ret: float) -> float:
    """HYG outperforming LQD = greed (risk-on); LQD outperforming = fear."""
    spread = hyg_ret - lqd_ret
    score = 50.0 + spread * 10.0
    return max(0.0, min(100.0, score))


def _score_put_call(symbol: str = "SPY") -> float:
    """
    SPY 30-day P/C OI ratio from yfinance options.
    High P/C (>1.2) = fear; Low P/C (<0.7) = greed.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return 50.0

        # Find expiry roughly 30 days out
        from datetime import datetime, timedelta
        target = datetime.now() + timedelta(days=30)
        best_exp = min(expirations, key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d") - target).days
        ))

        chain = ticker.option_chain(best_exp)
        total_call_oi = float(chain.calls["openInterest"].sum())
        total_put_oi = float(chain.puts["openInterest"].sum())

        if total_call_oi <= 0:
            return 50.0

        pc_ratio = total_put_oi / total_call_oi

        # P/C > 1.2 → extreme fear (score ~5)
        # P/C < 0.7 → extreme greed (score ~95)
        # Linear between
        if pc_ratio >= 1.5:
            return 5.0
        elif pc_ratio >= 1.2:
            return 20.0
        elif pc_ratio >= 1.0:
            return 35.0
        elif pc_ratio >= 0.8:
            return 55.0
        elif pc_ratio >= 0.7:
            return 70.0
        else:
            return 90.0

    except Exception as exc:
        logger.debug("[FG] put_call_score error: %s", exc)
        return 50.0


def _score_breadth(qqq_ret: float, spy_ret: float) -> float:
    """QQQ outperforming SPY = risk-on/greed; SPY outperforming = defensive/fear."""
    spread = qqq_ret - spy_ret
    score = 50.0 + spread * 8.0
    return max(0.0, min(100.0, score))


def _compute_fear_greed() -> float:
    """
    Download required tickers and compute composite Fear & Greed score 0-100.
    Returns 50.0 as neutral if data is unavailable.
    """
    try:
        import yfinance as yf

        tickers = yf.download(
            ["SPY", "^VIX", "TLT", "HYG", "LQD", "QQQ"],
            period="200d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        import pandas as pd
        if isinstance(tickers.columns, pd.MultiIndex):
            close = tickers["Close"]
        else:
            close = tickers

        def get_series(sym: str):
            if sym in close.columns:
                return close[sym].dropna()
            return pd.Series(dtype=float)

        spy_p = get_series("SPY")
        vix_p = get_series("^VIX")
        tlt_p = get_series("TLT")
        hyg_p = get_series("HYG")
        lqd_p = get_series("LQD")
        qqq_p = get_series("QQQ")

        components = []
        weights = []

        # 1. Market Momentum
        if len(spy_p) >= 125:
            components.append(_score_market_momentum(spy_p))
            weights.append(1.0)

        # 2. VIX Level
        if len(vix_p) >= 1:
            components.append(_score_vix_level(vix_p))
            weights.append(1.5)  # VIX level gets extra weight

        # 3. VIX Trend
        if len(vix_p) >= 50:
            components.append(_score_vix_trend(vix_p))
            weights.append(1.0)

        # 4. Safe Haven Demand
        if len(spy_p) >= 21 and len(tlt_p) >= 21:
            spy_20d = _pct_return_20d(spy_p)
            tlt_20d = _pct_return_20d(tlt_p)
            components.append(_score_safe_haven(spy_20d, tlt_20d))
            weights.append(1.0)

        # 5. Junk Bond Demand
        if len(hyg_p) >= 21 and len(lqd_p) >= 21:
            hyg_20d = _pct_return_20d(hyg_p)
            lqd_20d = _pct_return_20d(lqd_p)
            components.append(_score_junk_bond(hyg_20d, lqd_20d))
            weights.append(1.0)

        # 6. Put/Call Ratio
        pc_score = _score_put_call("SPY")
        components.append(pc_score)
        weights.append(1.0)

        # 7. Breadth (QQQ vs SPY)
        if len(qqq_p) >= 6 and len(spy_p) >= 6:
            qqq_5d = _pct_return_5d(qqq_p)
            spy_5d = _pct_return_5d(spy_p)
            components.append(_score_breadth(qqq_5d, spy_5d))
            weights.append(0.8)

        if not components:
            return 50.0

        total_weight = sum(weights)
        composite = sum(c * w for c, w in zip(components, weights)) / total_weight

        logger.info(
            "[FG] Fear&Greed composite=%.1f | components=%s",
            composite,
            [f"{c:.0f}" for c in components],
        )
        return round(composite, 1)

    except Exception as exc:
        logger.warning("[FG] Compute failed (fail-open): %s", exc)
        return 50.0


def _ensure_fresh() -> None:
    """Refresh cache if stale."""
    global _cached_fg, _last_refresh
    with _lock:
        stale = (_time.monotonic() - _last_refresh) > CACHE_TTL
        empty = _cached_fg is None

    if stale or empty:
        fg = _compute_fear_greed()
        with _lock:
            _cached_fg = fg
            _last_refresh = _time.monotonic()


def get_fear_greed_score(
    direction: str,
    is_reversal: bool = False,
) -> Tuple[float, str, float]:
    """
    Returns (score_delta, reason, fg_index_value).

    For LONG signals:
      FG > 70  (greed)       : +4  (trend-following momentum)
      FG 50-70 (neutral-greed): +2
      FG 30-50 (neutral-fear) : -2  (caution)
      FG < 30  (extreme fear) : -8  (panic market — avoid LONG)
      FG < 20  + is_reversal  : +10 (contrarian buy-panic setup)

    For SHORT: reverse logic.
    Cached 1h. Fail-open.
    """
    try:
        _ensure_fresh()
        with _lock:
            fg = _cached_fg if _cached_fg is not None else 50.0

        if direction == "LONG":
            if is_reversal and fg < 20:
                return 10.0, f"FG={fg:.0f} EXTREME_FEAR reversal — contrarian buy", fg
            elif fg > 70:
                return 4.0, f"FG={fg:.0f} greed — trend following", fg
            elif fg >= 50:
                return 2.0, f"FG={fg:.0f} neutral-greed", fg
            elif fg >= 30:
                return -2.0, f"FG={fg:.0f} neutral-fear — caution", fg
            else:
                return -8.0, f"FG={fg:.0f} extreme fear — avoid LONG", fg
        else:  # SHORT
            if is_reversal and fg > 80:
                return 10.0, f"FG={fg:.0f} EXTREME_GREED reversal — contrarian short", fg
            elif fg < 30:
                return 4.0, f"FG={fg:.0f} fear — short trend-following", fg
            elif fg <= 50:
                return 2.0, f"FG={fg:.0f} neutral-fear — short friendly", fg
            elif fg <= 70:
                return -2.0, f"FG={fg:.0f} neutral-greed — caution on short", fg
            else:
                return -8.0, f"FG={fg:.0f} extreme greed — avoid SHORT", fg

    except Exception as exc:
        logger.debug("[FG] get_fear_greed_score error: %s", exc)
        return 0.0, "FG_ERROR", 50.0
