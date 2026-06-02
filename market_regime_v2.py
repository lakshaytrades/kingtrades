"""
market_regime_v2.py — Multi-Dimensional Market Regime Engine v2.0

6 independent regime dimensions combined into a single composite signal.
Impossible to track all 6 simultaneously as a human trader.

Dimensions:
  1. VIX Regime      — volatility level → CALM / NORMAL / ELEVATED / HIGH / CRISIS
  2. Trend Regime    — SPY directional quality (R² + slope)
  3. Momentum Regime — ROC acceleration vs deceleration
  4. Breadth Regime  — % of watchlist symbols above 20-period EMA
  5. Correlation     — SPY/QQQ/IWM correlation (risk-on vs decorrelated)
  6. Time Regime     — session window (opening/morning/midday/afternoon/power-hour)

Composite → OPTIMAL / GOOD / NEUTRAL / CAUTION / AVOID
Each level carries a score_multiplier and size_multiplier.

All external calls are cached and fail-open.
"""

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Cache helpers ─────────────────────────────────────────────────────────────
_CACHE: Dict[str, object]   = {}
_CACHE_TS: Dict[str, float] = {}


def _cached(key: str, ttl: float):
    """Return cached value if fresh, else None."""
    if key in _CACHE and _time.time() - _CACHE_TS.get(key, 0) < ttl:
        return _CACHE[key]
    return None


def _store(key: str, value):
    _CACHE[key] = value
    _CACHE_TS[key] = _time.time()
    return value


# ── Result container ──────────────────────────────────────────────────────────
@dataclass
class RegimeState:
    composite:        str    # OPTIMAL / GOOD / NEUTRAL / CAUTION / AVOID / UNKNOWN
    score_multiplier: float  # multiplier applied to signal score (e.g. 1.2 OPTIMAL)
    size_multiplier:  float  # multiplier applied to position size
    details:          dict   = field(default_factory=dict)
    reason:           str    = ""


# ── Sector ETFs for breadth check ─────────────────────────────────────────────
_BREADTH_ETFS = ["SPY", "QQQ", "IWM"]

# Global breadth state updated from signal_generator each bar
_breadth_above_ema: Dict[str, bool] = {}


def update_breadth(symbol: str, price: float, ema20: float) -> None:
    """Called each bar from signal_generator. Updates breadth state."""
    _breadth_above_ema[symbol.upper()] = price > ema20


# ── Individual dimension functions ────────────────────────────────────────────

def _get_vix_regime() -> Tuple[str, float, float]:
    """Return (name, score_mult, size_mult). TTL 15 min."""
    cached = _cached("vix_regime", 900)
    if cached:
        return cached
    try:
        import yfinance as yf
        df = yf.download("^VIX", period="2d", interval="5m",
                         auto_adjust=False, progress=False)
        if df is None or df.empty:
            raise ValueError("empty VIX data")
        vix = float(df["Close"].dropna().iloc[-1])
        if vix < 15:
            result = ("CALM", 0.9, 0.85)
        elif vix < 20:
            result = ("NORMAL", 1.0, 1.0)
        elif vix < 28:
            result = ("ELEVATED", 1.15, 1.0)    # bigger moves = more opportunity
        elif vix < 35:
            result = ("HIGH", 0.85, 0.70)
        else:
            result = ("CRISIS", 0.30, 0.30)
        return _store("vix_regime", result)
    except Exception as e:
        logger.debug(f"market_regime_v2._get_vix_regime suppressed: {e}")
        return _store("vix_regime", ("UNKNOWN", 1.0, 1.0))


def _get_trend_regime() -> Tuple[str, float, float]:
    """SPY 20-bar R² + slope direction. TTL 2 min."""
    cached = _cached("trend_regime", 120)
    if cached:
        return cached
    try:
        import yfinance as yf
        df = yf.download("SPY", period="3d", interval="5m",
                         auto_adjust=True, progress=False)
        if df is None or df.empty or len(df) < 25:
            raise ValueError("insufficient SPY data")
        closes = df["Close"].dropna().values[-25:].astype(float)
        x = np.arange(len(closes), dtype=float)
        xm, ym = x.mean(), closes.mean()
        slope = np.sum((x - xm) * (closes - ym)) / (np.sum((x - xm) ** 2) + 1e-9)
        y_pred = ym + slope * (x - xm)
        ss_res = np.sum((closes - y_pred) ** 2)
        ss_tot = np.sum((closes - ym) ** 2)
        r2 = 1.0 - ss_res / (ss_tot + 1e-9) if ss_tot > 0 else 0.0

        if r2 >= 0.75:
            name   = "STRONG_TREND_UP"   if slope > 0 else "STRONG_TREND_DOWN"
            result = (name, 1.20, 1.10)
        elif r2 >= 0.45:
            name   = "WEAK_TREND_UP"   if slope > 0 else "WEAK_TREND_DOWN"
            result = (name, 0.95, 0.90)
        elif r2 >= 0.20:
            result = ("RANGING", 0.65, 0.65)
        else:
            result = ("CHOPPY", 0.40, 0.40)
        return _store("trend_regime", result)
    except Exception as e:
        logger.debug(f"market_regime_v2._get_trend_regime suppressed: {e}")
        return _store("trend_regime", ("UNKNOWN", 1.0, 1.0))


def _get_momentum_regime() -> Tuple[str, float, float]:
    """SPY ROC acceleration: 5-bar vs 20-bar rate of change. TTL 2 min."""
    cached = _cached("momentum_regime", 120)
    if cached:
        return cached
    try:
        import yfinance as yf
        df = yf.download("SPY", period="3d", interval="5m",
                         auto_adjust=True, progress=False)
        if df is None or df.empty or len(df) < 25:
            raise ValueError("insufficient SPY data")
        closes = df["Close"].dropna().values[-25:].astype(float)
        roc5   = (closes[-1] - closes[-6])  / max(closes[-6], 0.01) * 100
        roc20  = (closes[-1] - closes[-21]) / max(closes[-21], 0.01) * 100

        if roc5 > 0 and roc5 > roc20 * 0.4:
            result = ("ACCELERATING", 1.20, 1.10)
        elif roc5 > 0 and roc20 > 0:
            result = ("STEADY", 1.05, 1.00)
        elif roc5 < 0 and roc20 > 0:
            result = ("DECELERATING", 0.80, 0.85)
        elif roc5 < 0 and roc20 < 0:
            result = ("DECLINING", 0.70, 0.75)
        else:
            result = ("FLAT", 0.75, 0.80)
        return _store("momentum_regime", result)
    except Exception as e:
        logger.debug(f"market_regime_v2._get_momentum_regime suppressed: {e}")
        return _store("momentum_regime", ("UNKNOWN", 1.0, 1.0))


def _get_breadth_regime() -> Tuple[str, float, float]:
    """% of tracked symbols above 20-EMA. TTL 5 min."""
    cached = _cached("breadth_regime", 300)
    if cached:
        return cached
    try:
        if not _breadth_above_ema:
            return _store("breadth_regime", ("NO_DATA", 1.0, 1.0))
        pct = sum(_breadth_above_ema.values()) / max(len(_breadth_above_ema), 1)
        if pct >= 0.70:
            result = ("BULL_THRUST", 1.20, 1.10)
        elif pct >= 0.55:
            result = ("BULL", 1.05, 1.00)
        elif pct >= 0.45:
            result = ("NEUTRAL", 1.00, 1.00)
        elif pct >= 0.30:
            result = ("BEAR", 0.80, 0.80)
        else:
            result = ("BEAR_PANIC", 0.50, 0.50)
        return _store("breadth_regime", result)
    except Exception as e:
        logger.debug(f"market_regime_v2._get_breadth_regime suppressed: {e}")
        return _store("breadth_regime", ("UNKNOWN", 1.0, 1.0))


def _get_correlation_regime() -> Tuple[str, float, float]:
    """SPY/QQQ/IWM pairwise correlation on 20 5-min bars. TTL 30 min."""
    cached = _cached("correlation_regime", 1800)
    if cached:
        return cached
    try:
        import yfinance as yf
        dfs = {}
        for etf in ["SPY", "QQQ", "IWM"]:
            df = yf.download(etf, period="2d", interval="5m",
                             auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                dfs[etf] = df["Close"].dropna().values[-20:].astype(float)
        if len(dfs) < 2:
            raise ValueError("need at least 2 ETFs")
        pairs = []
        etfs  = list(dfs.keys())
        for i in range(len(etfs)):
            for j in range(i + 1, len(etfs)):
                a, b = dfs[etfs[i]], dfs[etfs[j]]
                n = min(len(a), len(b))
                if n >= 5:
                    pairs.append(float(np.corrcoef(a[-n:], b[-n:])[0, 1]))
        if not pairs:
            raise ValueError("no valid pairs")
        avg_corr = float(np.mean(pairs))
        if avg_corr >= 0.90:
            result = ("RISK_ON_CORRELATED", 1.10, 1.00)   # directional market
        elif avg_corr >= 0.65:
            result = ("NORMAL_CORRELATION", 1.00, 1.00)
        elif avg_corr >= 0.35:
            result = ("DECORRELATED", 1.15, 1.05)          # stock-picker's market
        else:
            result = ("DIVERGING", 0.80, 0.80)             # macro disruption
        return _store("correlation_regime", result)
    except Exception as e:
        logger.debug(f"market_regime_v2._get_correlation_regime suppressed: {e}")
        return _store("correlation_regime", ("UNKNOWN", 1.0, 1.0))


def _get_time_regime() -> Tuple[str, float, float]:
    """US ET session classification. TTL 60 s."""
    cached = _cached("time_regime", 60)
    if cached:
        return cached
    try:
        from zoneinfo import ZoneInfo
        import datetime
        et = datetime.datetime.now(ZoneInfo("America/New_York"))
        h, m = et.hour, et.minute
        t = h * 60 + m
        if t < 9 * 60 + 30:
            result = ("PRE_MARKET", 0.0, 0.0)
        elif t < 10 * 60:
            result = ("OPENING_DRIVE", 1.25, 1.15)
        elif t < 11 * 60 + 30:
            result = ("MORNING", 1.05, 1.00)
        elif t < 13 * 60 + 30:
            result = ("MIDDAY_CHOP", 0.55, 0.45)
        elif t < 15 * 60:
            result = ("AFTERNOON", 0.95, 0.90)
        elif t < 15 * 60 + 30:
            result = ("POWER_HOUR", 1.30, 1.20)
        else:
            result = ("EOD_CLOSE", 0.0, 0.0)
        return _store("time_regime", result)
    except Exception as e:
        logger.debug(f"market_regime_v2._get_time_regime suppressed: {e}")
        return _store("time_regime", ("UNKNOWN", 1.0, 1.0))


# ── Composite engine ──────────────────────────────────────────────────────────

class RegimeEngineV2:
    """
    Multi-dimensional regime classifier.

    Call get_composite_regime() once per scan cycle (TTL 90s cached internally).
    """

    def get_vix_regime(self)         -> Tuple[str, float]: return _get_vix_regime()[:2]
    def get_trend_regime(self)       -> Tuple[str, float]: return _get_trend_regime()[:2]
    def get_momentum_regime(self)    -> Tuple[str, float]: return _get_momentum_regime()[:2]
    def get_breadth_regime(self)     -> Tuple[str, float]: return _get_breadth_regime()[:2]
    def get_correlation_regime(self) -> Tuple[str, float]: return _get_correlation_regime()[:2]
    def get_time_regime(self)        -> Tuple[str, float]: return _get_time_regime()[:2]

    def get_composite_regime(self, _watchlist: Optional[List[str]] = None) -> RegimeState:
        cached = _cached("composite_regime", 90)
        if cached:
            return cached

        try:
            vix_n,    vix_sm    = _get_vix_regime()[:2]
            trend_n,  trend_sm  = _get_trend_regime()[:2]
            mom_n,    mom_sm    = _get_momentum_regime()[:2]
            bread_n,  bread_sm  = _get_breadth_regime()[:2]
            corr_n,   corr_sm   = _get_correlation_regime()[:2]
            time_n,   time_sm   = _get_time_regime()[:2]

            # Hard blocks (time gates)
            if time_sm == 0.0:
                result = RegimeState(
                    composite="AVOID", score_multiplier=0.0, size_multiplier=0.0,
                    details={"time": time_n},
                    reason=f"Outside trading window: {time_n}",
                )
                return _store("composite_regime", result)

            # Weighted composite (time has highest weight — market structure driver)
            weights = {"vix": 2.0, "trend": 2.5, "mom": 1.5, "breadth": 1.5, "corr": 1.0, "time": 3.0}
            mults   = {"vix": vix_sm, "trend": trend_sm, "mom": mom_sm,
                       "breadth": bread_sm, "corr": corr_sm, "time": time_sm}
            total_w   = sum(weights.values())
            composite_sm = sum(weights[k] * mults[k] for k in weights) / total_w

            # Classify composite
            if composite_sm >= 1.15:
                composite = "OPTIMAL";  sc_mult = 1.30; sz_mult = 1.20
            elif composite_sm >= 1.00:
                composite = "GOOD";     sc_mult = 1.10; sz_mult = 1.10
            elif composite_sm >= 0.80:
                composite = "NEUTRAL";  sc_mult = 1.00; sz_mult = 1.00
            elif composite_sm >= 0.55:
                composite = "CAUTION";  sc_mult = 0.80; sz_mult = 0.65
            else:
                composite = "AVOID";    sc_mult = 0.0;  sz_mult = 0.0

            reason = (
                f"VIX:{vix_n} | Trend:{trend_n} | Mom:{mom_n} | "
                f"Breadth:{bread_n} | Corr:{corr_n} | Time:{time_n}"
            )
            result = RegimeState(
                composite=composite,
                score_multiplier=sc_mult,
                size_multiplier=sz_mult,
                details={
                    "vix": vix_n, "trend": trend_n, "momentum": mom_n,
                    "breadth": bread_n, "correlation": corr_n, "time": time_n,
                    "weighted_score": round(composite_sm, 3),
                },
                reason=reason,
            )
            return _store("composite_regime", result)
        except Exception as e:
            logger.debug(f"market_regime_v2.get_composite_regime suppressed: {e}")
            return RegimeState("UNKNOWN", 1.0, 1.0, {}, str(e))


# ── Module singleton ──────────────────────────────────────────────────────────

_engine: Optional[RegimeEngineV2] = None


def get_regime_engine() -> RegimeEngineV2:
    global _engine
    if _engine is None:
        _engine = RegimeEngineV2()
    return _engine


def get_composite_regime(watchlist: Optional[List[str]] = None) -> RegimeState:
    """Convenience wrapper. Fail-open."""
    try:
        return get_regime_engine().get_composite_regime(watchlist)
    except Exception:
        return RegimeState("UNKNOWN", 1.0, 1.0)
