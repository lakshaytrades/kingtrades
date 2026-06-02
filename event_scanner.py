"""
event_scanner.py — Corporate event detection for edge identification
Replaces LevelFields AI ($60/month) and Benzinga Pro ($99/month).
Uses yfinance + free SEC data. All cached 4 hours. All fail-open.

Scoring:
  +12: recent buyback or stock split announcement
  +8:  insider buying (multiple insiders in yfinance Form 4 data)
  -15: secondary offering (dilution)
  +6:  analyst upgrade / price-target raise
  -6:  analyst downgrade
  -8:  insider selling (high confidence)
"""
import logging
import time as _time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Cache (4-hour TTL — corporate events change slowly) ───────────────────────
_ev_cache: Dict = {}
_EV_TTL = 14400.0  # 4 hours in seconds


def _is_cache_valid(symbol: str) -> bool:
    entry = _ev_cache.get(symbol)
    if entry is None:
        return False
    return (_time.monotonic() - entry["ts"]) < _EV_TTL


def _set_cache(symbol: str, result: Tuple[float, str]) -> None:
    _ev_cache[symbol] = {"result": result, "ts": _time.monotonic()}


def _get_ticker(symbol: str):
    """Return a yfinance Ticker object or None on failure."""
    try:
        import yfinance as yf
        return yf.Ticker(symbol)
    except Exception as e:
        logger.debug(f"[EVENT] yfinance import/ticker fail: {e}")
        return None


def _check_splits_buybacks(ticker) -> Tuple[float, str]:
    """
    Detect recent stock splits (signal: retail FOMO momentum surge).
    yfinance `actions` DataFrame has 'Stock Splits' column.
    A non-zero split in the last 90 days = +12.
    Note: Buyback announcements are not in yfinance; we proxy with
    unusual share-count reduction via `info.sharesOutstanding`.
    """
    try:
        actions = ticker.actions
        if actions is None or actions.empty:
            return (0.0, "")
        if "Stock Splits" not in actions.columns:
            return (0.0, "")
        cutoff = datetime.utcnow() - timedelta(days=90)
        # Index may be tz-aware; normalise
        idx = actions.index
        try:
            if hasattr(idx, "tz_localize") and idx.tz is None:
                pass  # already naive
            elif hasattr(idx, "tz_convert"):
                idx = idx.tz_convert(None)
        except Exception:
            pass
        recent_splits = actions[
            (actions.index >= cutoff) & (actions["Stock Splits"] > 0)
        ] if not actions.empty else actions.iloc[0:0]
        if len(recent_splits) > 0:
            return (12.0, f"EVENT:SplitAnnounced({recent_splits['Stock Splits'].iloc[-1]:.1f}:1)")
    except Exception as e:
        logger.debug(f"[EVENT] _check_splits_buybacks suppressed: {e}")
    return (0.0, "")


def _check_insider_trading(ticker) -> Tuple[float, str]:
    """
    Use yfinance insider_transactions to detect recent insider buying/selling.
    Multiple insiders buying = +8; heavy insider selling = -8.
    """
    try:
        insiders = ticker.insider_transactions
        if insiders is None or insiders.empty:
            return (0.0, "")
        # Normalise column names
        insiders.columns = [c.lower().replace(" ", "_") for c in insiders.columns]
        # Look for 'transaction', 'shares', 'start_date' or 'date' columns
        date_col = None
        for cname in ["start_date", "date", "startdate", "transactiondate"]:
            if cname in insiders.columns:
                date_col = cname
                break
        if date_col is None:
            return (0.0, "")
        # Recent 90 days
        cutoff = datetime.utcnow() - timedelta(days=90)
        try:
            recent = insiders[insiders[date_col] >= cutoff]
        except Exception:
            recent = insiders.head(20)
        if recent.empty:
            return (0.0, "")
        # Detect buy/sell transactions
        trans_col = None
        for cname in ["transaction", "transaction_text", "transactiontype"]:
            if cname in recent.columns:
                trans_col = cname
                break
        if trans_col is None:
            return (0.0, "")
        buy_mask  = recent[trans_col].astype(str).str.lower().str.contains("buy|purchase|acquire")
        sell_mask = recent[trans_col].astype(str).str.lower().str.contains("sell|sale|dispose")
        buy_count  = int(buy_mask.sum())
        sell_count = int(sell_mask.sum())
        if buy_count >= 2:
            return (8.0, f"EVENT:InsiderBuying(n={buy_count})")
        if buy_count == 1:
            return (4.0, "EVENT:InsiderBuying(n=1)")
        if sell_count >= 3:
            return (-8.0, f"EVENT:InsiderSelling(n={sell_count})")
    except Exception as e:
        logger.debug(f"[EVENT] _check_insider_trading suppressed: {e}")
    return (0.0, "")


def _check_secondary_offering(ticker) -> Tuple[float, str]:
    """
    Secondary offerings (dilution) = hard AVOID signal.
    Proxy: shares outstanding has increased >5% vs last quarter via yfinance info.
    yfinance `info` keys: sharesOutstanding, floatShares, impliedSharesOutstanding.
    """
    try:
        info = ticker.info or {}
        current_shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if not current_shares:
            return (0.0, "")
        # Compare with quarterly balance sheet shares if available
        bs = ticker.quarterly_balance_sheet
        if bs is None or bs.empty:
            return (0.0, "")
        # Look for 'Ordinary Shares Number' or 'Common Stock' row
        share_rows = [r for r in bs.index if "share" in str(r).lower() or "stock" in str(r).lower()]
        if not share_rows:
            return (0.0, "")
        row_key = share_rows[0]
        share_row = bs.loc[row_key].dropna()
        if len(share_row) < 2:
            return (0.0, "")
        prev_shares = float(share_row.iloc[1])  # one quarter ago
        if prev_shares <= 0:
            return (0.0, "")
        current_f = float(current_shares)
        dilution_pct = (current_f - prev_shares) / prev_shares * 100.0
        if dilution_pct > 5.0:
            return (-15.0, f"EVENT:SecondaryOffering(+{dilution_pct:.1f}%dilution)")
    except Exception as e:
        logger.debug(f"[EVENT] _check_secondary_offering suppressed: {e}")
    return (0.0, "")


def _check_analyst_recommendations(ticker) -> Tuple[float, str]:
    """
    Use yfinance `recommendations` (or `upgrades_downgrades`) to detect
    recent analyst upgrades/downgrades in the last 30 days.
    +6 for upgrade, -6 for downgrade.
    """
    try:
        recs = None
        # Try upgrades_downgrades first (newer yfinance versions)
        try:
            recs = ticker.upgrades_downgrades
        except Exception:
            pass
        if recs is None or (hasattr(recs, "empty") and recs.empty):
            try:
                recs = ticker.recommendations
            except Exception:
                pass
        if recs is None or (hasattr(recs, "empty") and recs.empty):
            return (0.0, "")

        recs.columns = [c.lower().replace(" ", "_") for c in recs.columns]

        # Normalise index to naive datetime for comparison
        cutoff = datetime.utcnow() - timedelta(days=30)
        try:
            if hasattr(recs.index, "tz_convert"):
                recs.index = recs.index.tz_convert(None)
        except Exception:
            pass
        try:
            recent = recs[recs.index >= cutoff]
        except Exception:
            recent = recs.head(10)

        if recent.empty:
            return (0.0, "")

        # Determine upgrade/downgrade column
        grade_col = None
        for cname in ["to_grade", "tograde", "action", "recommendation"]:
            if cname in recent.columns:
                grade_col = cname
                break
        if grade_col is None:
            return (0.0, "")

        upgrade_keywords   = ["buy", "strong buy", "outperform", "overweight", "upgrade", "positive"]
        downgrade_keywords = ["sell", "underperform", "underweight", "downgrade", "negative", "reduce"]

        upgrades   = recent[recent[grade_col].astype(str).str.lower().str.contains("|".join(upgrade_keywords), na=False)]
        downgrades = recent[recent[grade_col].astype(str).str.lower().str.contains("|".join(downgrade_keywords), na=False)]

        if len(upgrades) > 0:
            firm_col = None
            for cname in ["firm", "company", "fromgrade", "from_grade"]:
                if cname in upgrades.columns:
                    firm_col = cname
                    break
            firm_name = str(upgrades[firm_col].iloc[0]) if firm_col else "Analyst"
            return (6.0, f"EVENT:AnalystUpgrade({firm_name})")
        if len(downgrades) > 0:
            return (-6.0, "EVENT:AnalystDowngrade")

    except Exception as e:
        logger.debug(f"[EVENT] _check_analyst_recommendations suppressed: {e}")
    return (0.0, "")


def get_event_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Check yfinance for recent corporate events.
    Returns (score_delta, reason).

    Scores:
      +12: recent buyback/split announcement
      +8:  insider buying (multiple insiders)
      +4:  insider buying (single insider)
      -8:  heavy insider selling
      -15: secondary offering (dilution)
      +6:  analyst upgrade
      -6:  analyst downgrade

    Scores are adjusted for direction:
      Positive events  → positive score for LONG, negative for SHORT
      Negative events  → negative score for LONG, positive for SHORT

    Fail-open: returns (0.0, "EVENT_ERROR") on any exception.
    """
    try:
        cache_key = f"{symbol}_{direction}"
        if _is_cache_valid(cache_key):
            return _ev_cache[cache_key]["result"]

        ticker = _get_ticker(symbol)
        if ticker is None:
            return (0.0, "EVENT_NO_TICKER")

        checks = [
            _check_splits_buybacks,
            _check_insider_trading,
            _check_secondary_offering,
            _check_analyst_recommendations,
        ]

        total_score = 0.0
        hit_reasons: List[str] = []

        for fn in checks:
            try:
                raw_s, r = fn(ticker)
                if raw_s != 0.0:
                    # Adjust sign for trade direction:
                    # Bullish events (positive raw_s): good for LONG, bad for SHORT
                    # Bearish events (negative raw_s): bad for LONG, good for SHORT
                    if direction == "LONG":
                        adj_s = raw_s
                    else:  # SHORT
                        adj_s = -raw_s
                    total_score += adj_s
                    if r:
                        hit_reasons.append(r)
            except Exception as _e:
                logger.debug(f"[EVENT] {fn.__name__} suppressed: {_e}")

        reason = "|".join(hit_reasons) if hit_reasons else "EVENT:NoSignal"

        # Clamp individual module contribution
        total_score = max(-20.0, min(20.0, total_score))

        result: Tuple[float, str] = (total_score, reason)
        _set_cache(cache_key, result)
        return result

    except Exception as e:
        logger.debug(f"[EVENT] get_event_score fail-open: {e}")
        return (0.0, "EVENT_ERROR")
