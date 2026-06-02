"""
intermarket_analysis.py — Macro regime detection via bond/dollar/commodity correlations
John Murphy / Stan Weinstein methodology. Free via yfinance. Cached 30 min.
Returns (score_adjustment: float, regime: str, reasons: list).
"""
import logging
import time as _time
from typing import List, Tuple, Dict

logger = logging.getLogger(__name__)

# ── Cache (30-min TTL — macro regime doesn't change every minute) ─────────────
_im_cache: Dict = {}
_IM_TTL = 1800.0  # 30 minutes

_IM_CACHE_KEY = "intermarket_global"


def _is_cache_valid() -> bool:
    entry = _im_cache.get(_IM_CACHE_KEY)
    if entry is None:
        return False
    return (_time.monotonic() - entry["ts"]) < _IM_TTL


def _set_cache(result: Tuple[float, str, List[str]]) -> None:
    _im_cache[_IM_CACHE_KEY] = {"result": result, "ts": _time.monotonic()}


def _get_5day_perf(ticker_sym: str) -> float:
    """
    Return the 5-trading-day percentage return for ticker_sym using yfinance.
    Returns 0.0 on any error (fail-open).
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker_sym)
        hist = tk.history(period="10d", interval="1d", auto_adjust=True, timeout=8)
        if hist is None or len(hist) < 2:
            return 0.0
        # Use last 5 available rows (handles partial weeks)
        hist = hist.tail(6)
        if len(hist) < 2:
            return 0.0
        start_price = float(hist["Close"].iloc[0])
        end_price   = float(hist["Close"].iloc[-1])
        if start_price == 0:
            return 0.0
        return (end_price - start_price) / start_price * 100.0
    except Exception as e:
        logger.debug(f"[INTERMARKET] _get_5day_perf({ticker_sym}) suppressed: {e}")
        return 0.0


def get_intermarket_score(direction: str) -> Tuple[float, str, List[str]]:
    """
    Check TLT, UUP, GLD, USO, HYG, VXX 5-day performance.
    Returns (score_delta, regime_name, reasons).

    Scoring logic (John Murphy intermarket methodology):
      Risk-on signals  → favour LONG (positive score)
      Risk-off signals → penalise LONG (negative score)

    Individual signals:
      TLT  (20yr Treasuries): +ve = risk-on for equities       → +3 LONG / -3 SHORT when rising
      UUP  (Dollar ETF):      +ve = risk-off (strong $)        → -2 LONG / +2 SHORT when rising
      GLD  (Gold):            +ve = fear/inflation hedge        → -2 LONG / +2 SHORT when rising
      USO  (Oil):             +ve = reflation/risk-on signal    → +2 LONG / -2 SHORT when rising
      HYG  (High-yield bonds):+ve = credit health = risk-on     → +3 LONG / -3 SHORT when rising
      VXX  (VIX futures):     +ve = fear spike                  → -4 LONG / +4 SHORT when rising

    Composite:
      score >= +7  → "RISK_ON"   → final delta = +8  LONG / -8  SHORT
      score <= -7  → "RISK_OFF"  → final delta = -10 LONG / +10 SHORT
      in between   → "NEUTRAL"   → final delta = 0.0

    Fail-open: returns (0.0, "NEUTRAL", []) on any error.
    """
    try:
        # Return cached result regardless of direction (regime is global)
        if _is_cache_valid():
            cached_score, cached_regime, cached_reasons = _im_cache[_IM_CACHE_KEY]["result"]
            # Adjust sign for direction
            return _apply_direction(cached_score, cached_regime, cached_reasons, direction)

        etf_config = [
            # (ticker, risk_on_if_positive, weight_long, weight_short)
            ("TLT", True,   3.0,  -3.0),   # bonds up = risk-on
            ("UUP", False, -2.0,   2.0),   # dollar up = risk-off
            ("GLD", False, -2.0,   2.0),   # gold up = risk-off / fear
            ("USO", True,   2.0,  -2.0),   # oil up = reflation / risk-on
            ("HYG", True,   3.0,  -3.0),   # credit health up = risk-on
            ("VXX", False, -4.0,   4.0),   # VIX up = fear
        ]

        raw_score = 0.0
        reasons: List[str] = []

        for ticker, risk_on_up, weight_long, weight_short in etf_config:
            perf = _get_5day_perf(ticker)
            if perf == 0.0:
                # No data → skip (fail-open)
                continue
            direction_positive = perf > 0.0
            if direction_positive:
                # Ticker moved up
                contribution = weight_long  # positive = risk-on, negative = risk-off
                sign_str = f"+{perf:.1f}%"
            else:
                # Ticker moved down
                contribution = -weight_long  # reverse the weight
                sign_str = f"{perf:.1f}%"
            raw_score += contribution
            label = "RiskOn" if (contribution > 0) else "RiskOff"
            reasons.append(f"{ticker}:{sign_str}({label})")

        # Classify regime
        if raw_score >= 7.0:
            regime = "RISK_ON"
        elif raw_score <= -7.0:
            regime = "RISK_OFF"
        else:
            regime = "NEUTRAL"

        # Cache raw_score + regime (direction-independent)
        _set_cache((raw_score, regime, reasons))

        return _apply_direction(raw_score, regime, reasons, direction)

    except Exception as e:
        logger.debug(f"[INTERMARKET] get_intermarket_score fail-open: {e}")
        return (0.0, "NEUTRAL", [])


def _apply_direction(
    raw_score: float,
    regime: str,
    reasons: List[str],
    direction: str,
) -> Tuple[float, str, List[str]]:
    """
    Convert the raw regime score into a signed score_delta for the given direction.
    """
    try:
        if regime == "RISK_ON":
            delta = 8.0 if direction == "LONG" else -8.0
        elif regime == "RISK_OFF":
            delta = -10.0 if direction == "LONG" else 10.0
        else:
            delta = 0.0
        return (delta, regime, reasons)
    except Exception as e:
        logger.debug(f"[INTERMARKET] _apply_direction fail-open: {e}")
        return (0.0, "NEUTRAL", [])
