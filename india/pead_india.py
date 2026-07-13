"""
pead_india.py — Post-Earnings Announcement Drift (PEAD) Alpha Tracker
Academic alpha: Bernard & Thomas 1989, Chan 2003 — stocks drift in the direction
of earnings surprises for 1-60 days after announcement.

NSE-specific implementation:
  - Tracks NSE quarterly results dates from corporate_events_india.py
  - Uses price reaction on earnings day as surprise proxy (no consensus estimates needed)
  - Positive reaction ≥+2% on earnings day → PEAD LONG for next 5 trading days (+5 per day decay)
  - Negative reaction ≤-2% on earnings day → PEAD SHORT for next 5 trading days
  - Max PEAD score decays: day 1=+15, day 2=+12, day 3=+8, day 4=+5, day 5=+3

State stored in memory (single trading session). Data loaded from data/pead_cache.json
at startup (persists across days).
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("pead_india")
IST = ZoneInfo("Asia/Kolkata")

_CACHE_FILE = Path(__file__).parent.parent / "data" / "pead_cache.json"
_DECAY_SCORES = [15, 12, 8, 5, 3]   # score for day 1, 2, 3, 4, 5 post-earnings

# {symbol: {"earnings_date": "YYYY-MM-DD", "reaction_pct": float, "direction": "LONG"/"SHORT"}}
_pead_registry: Dict[str, dict] = {}


def _load_cache():
    global _pead_registry
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            # Only load entries that are still within 6 days window
            today = datetime.now(IST).date()
            valid = {}
            for sym, entry in data.items():
                try:
                    ed = datetime.strptime(entry["earnings_date"], "%Y-%m-%d").date()
                    if (today - ed).days <= 6:
                        valid[sym] = entry
                except Exception:
                    pass
            _pead_registry = valid
    except Exception as e:
        logger.debug(f"pead load_cache: {e}")


def _save_cache():
    try:
        _CACHE_FILE.parent.mkdir(exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(_pead_registry, indent=2))
    except Exception as e:
        logger.debug(f"pead save_cache: {e}")


def register_earnings_reaction(symbol: str, earnings_date: str, reaction_pct: float):
    """
    Register a stock's reaction to its earnings announcement.
    earnings_date: "YYYY-MM-DD"
    reaction_pct: % price change on earnings day (+ve = positive surprise)
    """
    if abs(reaction_pct) < 2.0:
        return   # too small to qualify as a PEAD signal
    direction = "LONG" if reaction_pct > 0 else "SHORT"
    _pead_registry[symbol.upper()] = {
        "earnings_date": earnings_date,
        "reaction_pct": reaction_pct,
        "direction": direction,
    }
    _save_cache()
    logger.info(f"PEAD registered: {symbol} {direction} {reaction_pct:+.1f}% on {earnings_date}")


def get_pead_score(symbol: str, direction: str) -> Tuple[int, str]:
    """
    Return (score_adj, reason) based on PEAD drift.
    Returns (0, "") if no PEAD or expired.
    """
    try:
        if not _pead_registry:
            _load_cache()

        entry = _pead_registry.get(symbol.upper())
        if entry is None:
            return (0, "")

        today = datetime.now(IST).date()
        try:
            ed = datetime.strptime(entry["earnings_date"], "%Y-%m-%d").date()
        except Exception:
            return (0, "")

        days_since = (today - ed).days
        if days_since < 0 or days_since >= len(_DECAY_SCORES):
            return (0, "")   # future date or expired

        pead_direction = entry["direction"]
        decay_score = _DECAY_SCORES[days_since]
        reaction_pct = entry.get("reaction_pct", 0.0)

        # Boost if PEAD direction matches signal direction
        if pead_direction == direction:
            reason = (f"PEAD_{pead_direction} day{days_since + 1} "
                      f"(earnings {reaction_pct:+.1f}% reaction)")
            return (decay_score, reason)
        else:
            # Counter-PEAD: shorting a post-positive-earnings stock or longing post-negative
            penalty = -min(8, decay_score // 2)
            reason = f"PEAD_CONTRA_{pead_direction}:{penalty:+d}"
            return (penalty, reason)

    except Exception as e:
        logger.debug(f"get_pead_score {symbol}: {e}")
        return (0, "")


def auto_detect_earnings_reactions(symbols: list, df_getter) -> int:
    """
    Auto-detect earnings reactions for a list of symbols.
    df_getter: callable(symbol) → pd.DataFrame of daily OHLCV or None
    Returns number of new PEAD signals detected.
    Called once per day in the pre-market window.
    """
    detected = 0
    try:
        # Load corporate events to find recent earnings dates
        try:
            from corporate_events_india import get_earnings_dates
            earnings_events = get_earnings_dates()  # {symbol: ["YYYY-MM-DD", ...]}
        except Exception:
            earnings_events = {}

        today = datetime.now(IST).date()

        for sym in symbols:
            sym_upper = sym.upper()
            if sym_upper in _pead_registry:
                # Check if already registered for today or yesterday
                existing = _pead_registry[sym_upper]
                try:
                    ed = datetime.strptime(existing["earnings_date"], "%Y-%m-%d").date()
                    if (today - ed).days <= 1:
                        continue  # recently registered, skip
                except Exception:
                    pass

            # Check if there was an earnings event in the last 2 trading days
            sym_events = earnings_events.get(sym_upper, earnings_events.get(sym, []))
            recent_event = None
            for ev_date_str in sym_events:
                try:
                    ev_date = datetime.strptime(ev_date_str, "%Y-%m-%d").date()
                    days_ago = (today - ev_date).days
                    if 0 <= days_ago <= 2:
                        recent_event = ev_date_str
                        break
                except Exception:
                    pass

            if recent_event is None:
                continue

            # Measure the price reaction on that day
            try:
                df = df_getter(sym)
                if df is None or df.empty or len(df) < 3:
                    continue

                ev_date = datetime.strptime(recent_event, "%Y-%m-%d").date()
                # Find rows matching earnings date
                df_index = df.index
                if hasattr(df_index, "date"):
                    mask = df_index.date == ev_date
                    if not mask.any():
                        continue
                    ev_close = float(df[mask]["close"].iloc[0])
                    prev_idx = df.index.get_loc(df[mask].index[0]) - 1
                    if prev_idx < 0:
                        continue
                    prev_close = float(df.iloc[prev_idx]["close"])
                else:
                    continue

                reaction_pct = (ev_close - prev_close) / prev_close * 100
                if abs(reaction_pct) >= 2.0:
                    register_earnings_reaction(sym_upper, recent_event, reaction_pct)
                    detected += 1

            except Exception as e:
                logger.debug(f"pead auto_detect {sym}: {e}")

    except Exception as e:
        logger.debug(f"auto_detect_earnings_reactions: {e}")

    return detected


# Load cache on import
_load_cache()
