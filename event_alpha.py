"""
event_alpha.py — Event-Driven Alpha Signals (v27.0)

Systematic capture of high-predictability events used by Goldman, Citadel, and all
major stat-arb desks. These events create SCHEDULED price reactions that systematic
funds can exploit repeatedly.

Three event types:
  1. Analyst upgrades/downgrades — yfinance recommendations feed
     Upgrade from major bank (>10 analysts): +8 LONG, -8 SHORT signal
     Fresh downgrade: -7 LONG, +7 SHORT signal
     Score decays after 3 trading days

  2. Dividend capture window (Hartzmark & Solomon strategy, 2019)
     2-4 calendar days before ex-dividend date: institutions accumulate
     Day after ex-div: price drops exactly by dividend, retail sells
     Score: +6 LONG for capture window on dividend-paying stocks

  3. Stock splits (behavioral finance)
     Forward split announcement → retail FOMO buying +5 LONG
     Reverse split → distress signal -8 LONG / +6 SHORT

All calls are cached (1h for analyst, 24h for corporate actions).
All fail-open: return (0.0, reason) on any error.
"""

import logging
import time as _time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_analyst_cache: Dict[str, Tuple[float, str, float]] = {}  # {symbol: (score, reason, ts)}
_corp_cache: Dict[str, Tuple[float, str, float]] = {}     # {symbol: (score, reason, ts)}
_ANALYST_TTL = 3600.0   # 1 hour
_CORP_TTL    = 86400.0  # 24 hours


# ── Strategy 1: Analyst Signal ──────────────────────────────────────────────

def get_analyst_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Check recent analyst rating changes (last 5 days).
    Returns (score_delta, reason). Fail-open.
    """
    try:
        now = _time.monotonic()
        cached = _analyst_cache.get(symbol)
        if cached and (now - cached[2]) < _ANALYST_TTL:
            raw_score, reason, _ = cached
            return _directional(raw_score, direction), reason

        score, reason = _fetch_analyst_signal(symbol)
        _analyst_cache[symbol] = (score, reason, now)
        return _directional(score, direction), reason
    except Exception as e:
        logger.debug(f"[event_alpha] analyst {symbol}: {e}")
        return 0.0, "analyst:error"


def _fetch_analyst_signal(symbol: str) -> Tuple[float, str]:
    """Fetch yfinance recommendations and score recency + direction."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        recs = ticker.recommendations

        if recs is None or recs.empty:
            return 0.0, "analyst:no-data"

        # Filter last 5 trading days
        cutoff = datetime.now() - timedelta(days=5)
        try:
            recent = recs[recs.index >= cutoff]
        except Exception:
            recent = recs.tail(5)

        if recent.empty:
            return 0.0, "analyst:stale"

        # Score the most recent action
        latest = recent.iloc[-1]

        # Try different column names across yfinance versions
        action_col = None
        for col in ["Action", "action", "To Grade", "toGrade"]:
            if col in latest.index:
                action_col = col
                break

        firm_col = None
        for col in ["Firm", "firm", "firm_action"]:
            if col in latest.index:
                firm_col = col
                break

        if action_col is None:
            return 0.0, "analyst:no-action-col"

        action = str(latest[action_col]).upper()
        firm = str(latest[firm_col]) if firm_col else "unknown"

        UPGRADES   = {"UPGRADE", "UPGRADED", "BUY", "STRONG BUY", "OUTPERFORM", "OVERWEIGHT"}
        DOWNGRADES = {"DOWNGRADE", "DOWNGRADED", "SELL", "UNDERPERFORM", "UNDERWEIGHT"}

        if any(u in action for u in UPGRADES):
            score = 8.0
            return score, f"analyst:upgrade({firm})"
        elif any(d in action for d in DOWNGRADES):
            score = -7.0
            return score, f"analyst:downgrade({firm})"
        else:
            return 0.0, f"analyst:neutral({action})"

    except Exception as e:
        logger.debug(f"[event_alpha] _fetch_analyst_signal: {e}")
        return 0.0, "analyst:fetch-error"


# ── Strategy 2: Dividend Capture ─────────────────────────────────────────────

def get_dividend_capture_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Detect 2-4 day pre-ex-dividend window (optimal accumulation window).
    Returns (score_delta, reason). Fail-open.
    """
    try:
        now = _time.monotonic()
        cached = _corp_cache.get(f"div_{symbol}")
        if cached and (now - cached[2]) < _CORP_TTL:
            raw_score, reason, _ = cached
            return _directional(raw_score, direction), reason

        score, reason = _fetch_dividend_signal(symbol)
        _corp_cache[f"div_{symbol}"] = (score, reason, now)
        return _directional(score, direction), reason
    except Exception as e:
        logger.debug(f"[event_alpha] dividend {symbol}: {e}")
        return 0.0, "div:error"


def _fetch_dividend_signal(symbol: str) -> Tuple[float, str]:
    """Check if we're in the dividend capture window."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar

        if cal is None:
            return 0.0, "div:no-calendar"

        # Try to get ex-dividend date from calendar or dividends
        ex_div = None
        if isinstance(cal, dict):
            ex_div = cal.get("Ex-Dividend Date") or cal.get("exDividendDate")
        elif hasattr(cal, "get"):
            try:
                ex_div = cal.get("Ex-Dividend Date")
            except Exception:
                pass

        if ex_div is None:
            # Try dividends history for upcoming
            divs = ticker.dividends
            if divs is not None and not divs.empty:
                last_freq_days = _estimate_dividend_frequency(divs)
                if last_freq_days > 0:
                    last_ex = divs.index[-1]
                    if hasattr(last_ex, "to_pydatetime"):
                        last_ex = last_ex.to_pydatetime()
                    next_ex = last_ex + timedelta(days=last_freq_days)
                    ex_div = next_ex
            if ex_div is None:
                return 0.0, "div:no-ex-date"

        # Convert to date
        if hasattr(ex_div, "date"):
            ex_date = ex_div.date()
        elif hasattr(ex_div, "to_pydatetime"):
            ex_date = ex_div.to_pydatetime().date()
        else:
            ex_date = ex_div

        today = datetime.now().date()
        days_until_ex = (ex_date - today).days

        if 2 <= days_until_ex <= 4:
            return 6.0, f"div:capture-window({days_until_ex}d to ex-div)"
        elif days_until_ex == 0 or days_until_ex == 1:
            return -3.0, f"div:ex-div-imminent(sell pressure)"
        elif days_until_ex < 0 and days_until_ex >= -2:
            return -4.0, f"div:post-ex-div(price gap risk)"

        return 0.0, "div:not-in-window"

    except Exception as e:
        logger.debug(f"[event_alpha] _fetch_dividend_signal: {e}")
        return 0.0, "div:fetch-error"


def _estimate_dividend_frequency(divs) -> int:
    """Estimate dividend frequency in days from history."""
    try:
        if len(divs) < 2:
            return 0
        diffs = []
        idx = divs.index
        for i in range(1, min(5, len(idx))):
            d1 = idx[-i]
            d2 = idx[-i - 1]
            if hasattr(d1, "to_pydatetime"):
                d1 = d1.to_pydatetime().date()
                d2 = d2.to_pydatetime().date()
            diff = abs((d1 - d2).days)
            if 20 <= diff <= 400:
                diffs.append(diff)
        return int(sum(diffs) / len(diffs)) if diffs else 0
    except Exception:
        return 0


# ── Strategy 3: Stock Split Signal ────────────────────────────────────────────

def get_split_signal(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Detect recent forward/reverse split announcements.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        now = _time.monotonic()
        cached = _corp_cache.get(f"split_{symbol}")
        if cached and (now - cached[2]) < _CORP_TTL:
            raw_score, reason, _ = cached
            return _directional(raw_score, direction), reason

        score, reason = _fetch_split_signal(symbol)
        _corp_cache[f"split_{symbol}"] = (score, reason, now)
        return _directional(score, direction), reason
    except Exception as e:
        logger.debug(f"[event_alpha] split {symbol}: {e}")
        return 0.0, "split:error"


def _fetch_split_signal(symbol: str) -> Tuple[float, str]:
    """Check for recent stock splits (last 30 days)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        splits = ticker.splits

        if splits is None or splits.empty:
            return 0.0, "split:none"

        cutoff = datetime.now() - timedelta(days=30)
        try:
            recent = splits[splits.index >= cutoff]
        except Exception:
            recent = splits.tail(3)

        if recent.empty:
            return 0.0, "split:no-recent"

        latest_ratio = float(recent.iloc[-1])

        if latest_ratio > 1.0:
            # Forward split: stock cheaper = retail FOMO buying
            return 5.0, f"split:forward({latest_ratio:.1f}:1 — retail momentum)"
        elif latest_ratio < 1.0:
            # Reverse split: distress signal
            return -8.0, f"split:reverse({latest_ratio:.2f}:1 — distress)"

        return 0.0, "split:neutral"

    except Exception as e:
        logger.debug(f"[event_alpha] _fetch_split_signal: {e}")
        return 0.0, "split:fetch-error"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _directional(raw_score: float, direction: str) -> float:
    """Positive raw_score = LONG bullish. Flip for SHORT."""
    if direction == "SHORT":
        return -raw_score
    return raw_score
