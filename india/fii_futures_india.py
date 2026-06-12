"""
fii_futures_india.py — FII Index Futures Participant-wise OI Signal

Research: NSE publishes participant-wise OI (reportOIPW) daily after market close
(~5:30 PM IST) showing how many NIFTY index futures contracts each participant
category (FII/DII/Client/Pro) holds net long or short.

Key research finding: When FIIs are net long in NIFTY index futures AND the
position is rising (INCREASING_LONG), the next-session equity return is positively
biased. When FIIs flip to net short (INCREASING_SHORT), corrections follow.
Effect persists 1-2 sessions.

Score: ±6 for directional alignment, used as pre-market bias filter.
  FII net long  + INCREASING_LONG  → LONG +6, SHORT -4
  FII net short + INCREASING_SHORT → SHORT +6, LONG -4
  Flat/unclear  → (0, "")

Cache: 4 hours (data is EOD, only refreshes after ~5:30 PM IST).
History: last 5 days stored in data/fii_futures_history.json for trend calc.
Fail-open: all errors return (0, "") or empty dict — never raises.

Data sources (tried in order):
  1. NSE reportOIPW API  (primary, primed with session cookie)
  2. NSE archives CSV fallback
  3. niftytrader.in HTML parse (secondary fallback)
"""

import json
import logging
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("fii_futures_india")

IST = ZoneInfo("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────────

_CACHE_TTL        = 4 * 3600.0         # 4 hours in seconds (data is EOD)
_HISTORY_FILE     = Path(__file__).parent.parent / "data" / "fii_futures_history.json"
_HISTORY_MAX_DAYS = 5                  # keep last 5 trading days
_TREND_LOOKBACK   = 3                  # compare today vs 3-day average for trend

MARKET_OPEN_IST   = time(9, 15)
MARKET_CLOSE_IST  = time(15, 30)

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# Module-level position cache: {"data": dict, "ts": float}
_POS_CACHE: dict = {}


# ── IST helpers ───────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _is_market_hours() -> bool:
    """True if we are currently inside NSE trading hours (IST)."""
    t = _now_ist().time()
    return MARKET_OPEN_IST <= t <= MARKET_CLOSE_IST


def _ist_date_str() -> str:
    return _now_ist().strftime("%Y-%m-%d")


# ── History persistence ───────────────────────────────────────────────────────

def _load_history() -> List[dict]:
    """Load FII futures history from JSON file. Returns [] on any error."""
    try:
        if _HISTORY_FILE.exists():
            with _HISTORY_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
    except Exception as exc:
        logger.debug("fii_futures: load_history failed: %s", exc)
    return []


def _save_history(history: List[dict]) -> None:
    """Persist history to JSON, keeping only the last N days. Fail-silent."""
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Keep most recent _HISTORY_MAX_DAYS entries
        trimmed = history[-_HISTORY_MAX_DAYS:]
        with _HISTORY_FILE.open("w", encoding="utf-8") as fh:
            json.dump(trimmed, fh, indent=2)
    except Exception as exc:
        logger.debug("fii_futures: save_history failed: %s", exc)


def _append_to_history(entry: dict) -> None:
    """Add today's entry to history (de-duplicate by date)."""
    history = _load_history()
    today_str = entry.get("date", "")
    # Remove any existing entry for today (we overwrite with fresh data)
    history = [h for h in history if h.get("date") != today_str]
    history.append(entry)
    _save_history(history)


# ── Trend calculation ─────────────────────────────────────────────────────────

def _compute_trend(current_net: int, history: List[dict]) -> str:
    """
    Compare current_net vs average of last _TREND_LOOKBACK days.

    Returns one of:
      "INCREASING_LONG"  — net is rising / more positive than recent average
      "INCREASING_SHORT" — net is falling / more negative than recent average
      "FLAT"             — no meaningful change
    """
    if not history:
        return "FLAT"

    recent = history[-_TREND_LOOKBACK:]
    if not recent:
        return "FLAT"

    avg_net = sum(h.get("fii_net", 0) for h in recent) / len(recent)
    delta = current_net - avg_net

    # Threshold: 5000 contracts difference (typical day swing ~50–200k contracts)
    THRESHOLD = 5_000
    if delta > THRESHOLD:
        return "INCREASING_LONG"
    if delta < -THRESHOLD:
        return "INCREASING_SHORT"
    return "FLAT"


# ── Data source 1: NSE reportOIPW API ────────────────────────────────────────

def _fetch_nse_reportoipw() -> dict:
    """
    Fetch participant-wise OI from NSE reportOIPW API.

    NSE JSON structure (list of dicts):
      [
        {"clientType": "FII", "futureIndexLong": 123456, "futureIndexShort": 100000, ...},
        {"clientType": "DII", ...},
        ...
      ]

    Returns dict with fii_net_long, fii_net_short, fii_net on success.
    Returns {} on any failure.
    """
    try:
        import requests
        sess = requests.Session()
        sess.headers.update(_NSE_HEADERS)

        # Prime session cookie — NSE Akamai protection requires a page visit first
        try:
            sess.get("https://www.nseindia.com/", timeout=8)
        except Exception:
            pass

        url = "https://www.nseindia.com/api/reportOIPW"
        resp = sess.get(url, timeout=12)
        if resp.status_code != 200:
            logger.debug("fii_futures NSE API status %s", resp.status_code)
            return {}

        raw = resp.json()

        # API may return {"data": [...]} or directly a list
        if isinstance(raw, dict):
            rows = raw.get("data", [])
        elif isinstance(raw, list):
            rows = raw
        else:
            logger.debug("fii_futures NSE API unexpected type: %s", type(raw))
            return {}

        fii_long = 0
        fii_short = 0
        for row in rows:
            client_type = str(
                row.get("clientType", row.get("client_type", ""))
            ).strip().upper()
            if client_type == "FII":
                def _int(v: object) -> int:
                    try:
                        return int(str(v).replace(",", ""))
                    except (ValueError, TypeError):
                        return 0

                fii_long = _int(
                    row.get("futureIndexLong",
                    row.get("future_index_long",
                    row.get("futLongOI", 0)))
                )
                fii_short = _int(
                    row.get("futureIndexShort",
                    row.get("future_index_short",
                    row.get("futShortOI", 0)))
                )
                break

        if fii_long == 0 and fii_short == 0:
            logger.debug("fii_futures: NSE API returned no FII row (rows=%d)", len(rows))
            return {}

        result = {
            "fii_net_long":  fii_long,
            "fii_net_short": fii_short,
            "fii_net":       fii_long - fii_short,
            "source":        "NSE_API",
        }
        logger.debug(
            "fii_futures NSE API: net_long=%d net_short=%d net=%d",
            fii_long, fii_short, result["fii_net"],
        )
        return result

    except Exception as exc:
        logger.debug("_fetch_nse_reportoipw failed: %s", exc)
        return {}


# ── Data source 2: NSE Archives CSV ──────────────────────────────────────────

def _fetch_nse_csv_fallback() -> dict:
    """
    Fallback: parse participant-wise OI CSV from NSE archives.

    NSE publishes files at:
      https://archives.nseindia.com/content/nsccl/fao_participant_oi_<DDMMYYYY>.csv

    Tries today and up to 2 prior dates (in case today's file not yet published).
    Returns {} on any failure.
    """
    try:
        import csv
        import io
        import requests

        sess = requests.Session()
        sess.headers.update(_NSE_HEADERS)
        try:
            sess.get("https://www.nseindia.com/", timeout=8)
        except Exception:
            pass

        # Try today and yesterday (in case today's not published yet)
        for delta_days in (0, 1, 2):
            check_date = _now_ist().date() - timedelta(days=delta_days)
            date_str = check_date.strftime("%d%m%Y")
            url = (
                "https://archives.nseindia.com/content/nsccl/"
                f"fao_participant_oi_{date_str}.csv"
            )
            try:
                resp = sess.get(url, timeout=10)
                if resp.status_code != 200:
                    continue

                reader = csv.DictReader(io.StringIO(resp.text))
                for row in reader:
                    ct = str(
                        row.get("Client Type", row.get("clientType", ""))
                    ).strip().upper()
                    if ct == "FII":
                        def _csv_int(v: str) -> int:
                            try:
                                return int(str(v).replace(",", "").strip())
                            except (ValueError, TypeError):
                                return 0

                        fii_long = _csv_int(
                            row.get("Future Index Long",
                            row.get("futureIndexLong", "0"))
                        )
                        fii_short = _csv_int(
                            row.get("Future Index Short",
                            row.get("futureIndexShort", "0"))
                        )
                        if fii_long == 0 and fii_short == 0:
                            continue
                        result = {
                            "fii_net_long":  fii_long,
                            "fii_net_short": fii_short,
                            "fii_net":       fii_long - fii_short,
                            "source":        "NSE_CSV",
                        }
                        logger.debug(
                            "fii_futures NSE CSV: net_long=%d net_short=%d net=%d",
                            fii_long, fii_short, result["fii_net"],
                        )
                        return result
            except Exception as inner:
                logger.debug("fii_futures CSV %s: %s", url, inner)
                continue

    except Exception as exc:
        logger.debug("_fetch_nse_csv_fallback failed: %s", exc)
    return {}


# ── Data source 3: niftytrader.in HTML parse ─────────────────────────────────

def _fetch_niftytrader_fallback() -> dict:
    """
    Tertiary fallback: scrape FII index futures OI from niftytrader.in.

    The page at https://www.niftytrader.in/participant-wise-oi contains
    a table with FII net long / net short positions in index futures.

    Returns {} on any failure.
    """
    try:
        import re
        import requests

        url = "https://www.niftytrader.in/participant-wise-oi"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            logger.debug("fii_futures niftytrader: status %s", resp.status_code)
            return {}

        html = resp.text

        # Find the FII row and extract the first two large numbers (long, short)
        # Typical pattern: ...FII...123,456...100,000...
        fii_section_match = re.search(
            r'FII.*?(\d[\d,]+).*?(\d[\d,]+)',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not fii_section_match:
            logger.debug("fii_futures niftytrader: FII row not found in HTML")
            return {}

        def _parse_num(s: str) -> int:
            try:
                return int(s.replace(",", ""))
            except (ValueError, TypeError):
                return 0

        fii_long  = _parse_num(fii_section_match.group(1))
        fii_short = _parse_num(fii_section_match.group(2))

        if fii_long == 0 and fii_short == 0:
            return {}

        result = {
            "fii_net_long":  fii_long,
            "fii_net_short": fii_short,
            "fii_net":       fii_long - fii_short,
            "source":        "NIFTYTRADER",
        }
        logger.debug(
            "fii_futures niftytrader: net_long=%d net_short=%d net=%d",
            fii_long, fii_short, result["fii_net"],
        )
        return result

    except Exception as exc:
        logger.debug("_fetch_niftytrader_fallback failed: %s", exc)
        return {}


# ── Core fetch (multi-source with fallback) ───────────────────────────────────

def _fetch_fii_futures_position() -> dict:
    """
    Try all data sources in order. Return first successful result.
    Returns {} if all sources fail (fail-open).
    """
    for fetcher in (
        _fetch_nse_reportoipw,
        _fetch_nse_csv_fallback,
        _fetch_niftytrader_fallback,
    ):
        try:
            result = fetcher()
            if result:
                return result
        except Exception as exc:
            logger.debug("fii_futures fetcher %s raised: %s", fetcher.__name__, exc)
    logger.debug("fii_futures: all data sources failed, returning empty")
    return {}


# ── Cache management ──────────────────────────────────────────────────────────

def _get_cached_position() -> dict:
    """
    Return cached FII futures position, refreshing if stale (>4 hours old).

    During market hours (9:15–15:30 IST) we prefer historical data anyway
    since NSE only publishes after ~5:30 PM IST. If live fetch returns 403
    or empty during market hours, fall back to last known good value from history.
    """
    now_mono = _time.monotonic()
    cached_ts = _POS_CACHE.get("ts", 0.0)

    if cached_ts > 0 and (now_mono - cached_ts) < _CACHE_TTL:
        return _POS_CACHE.get("data", {})

    # Attempt a fresh fetch
    fresh = _fetch_fii_futures_position()

    if fresh:
        today_str = _ist_date_str()
        fresh["date"] = today_str
        _POS_CACHE["data"] = fresh
        _POS_CACHE["ts"]   = now_mono
        # Persist to history for trend calculation
        _append_to_history(fresh)
        return fresh

    # Fetch failed — try to use the most recent history entry as fallback
    history = _load_history()
    if history:
        last = history[-1]
        logger.debug(
            "fii_futures: live fetch failed, using history entry date=%s",
            last.get("date", "unknown"),
        )
        # Cache this fallback for 1 hour (shorter TTL so we retry sooner)
        _POS_CACHE["data"] = last
        _POS_CACHE["ts"]   = now_mono - (_CACHE_TTL - 3600.0)
        return last

    # No data at all — cache the empty result briefly to avoid hammering
    _POS_CACHE["data"] = {}
    _POS_CACHE["ts"]   = now_mono - (_CACHE_TTL - 1800.0)  # retry in 30 min
    return {}


# ── Public API ────────────────────────────────────────────────────────────────

def get_fii_futures_position() -> dict:
    """
    Fetch (or return cached) FII index futures participant-wise OI from NSE.

    Returns a dict with the following keys (all present on success):
      fii_net_long  (int)  — total FII long contracts in index futures
      fii_net_short (int)  — total FII short contracts in index futures
      fii_net       (int)  — fii_net_long - fii_net_short
      trend         (str)  — "INCREASING_LONG" | "INCREASING_SHORT" | "FLAT"
      bias          (str)  — "BULLISH" | "BEARISH" | "NEUTRAL"
      date          (str)  — data date as "YYYY-MM-DD"
      reason        (str)  — human-readable description
      source        (str)  — which data source was used

    Returns {} on complete failure (fail-open — callers must handle empty dict).

    Note: During market hours (9:15–15:30 IST), returns previous session data
    since NSE only publishes the update after ~5:30 PM IST.
    """
    try:
        pos = _get_cached_position()
        if not pos:
            return {}

        fii_long  = pos.get("fii_net_long",  0)
        fii_short = pos.get("fii_net_short", 0)
        fii_net   = pos.get("fii_net",       fii_long - fii_short)
        data_date = pos.get("date",          _ist_date_str())
        source    = pos.get("source",        "UNKNOWN")

        # Compute trend vs recent history (excluding today's entry for baseline)
        history = _load_history()
        prior_history = [h for h in history if h.get("date") != data_date]
        trend = _compute_trend(fii_net, prior_history)

        # Determine bias: strong only when both net position AND trend agree
        if fii_net > 0 and trend == "INCREASING_LONG":
            bias = "BULLISH"
        elif fii_net < 0 and trend == "INCREASING_SHORT":
            bias = "BEARISH"
        elif fii_net > 0:
            # Net long but trend flat — mild bullish but not strong signal
            bias = "BULLISH"
        elif fii_net < 0:
            # Net short but trend flat — mild bearish but not strong signal
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        reason = (
            f"FII Index Futures: long={fii_long:,} short={fii_short:,} "
            f"net={fii_net:+,} trend={trend} bias={bias} "
            f"[{data_date} src={source}]"
        )

        logger.debug("fii_futures position: %s", reason)

        return {
            "fii_net_long":  fii_long,
            "fii_net_short": fii_short,
            "fii_net":       fii_net,
            "trend":         trend,
            "bias":          bias,
            "date":          data_date,
            "reason":        reason,
            "source":        source,
        }

    except Exception as exc:
        logger.debug("get_fii_futures_position failed (fail-open): %s", exc)
        return {}


def get_fii_futures_score(direction: str) -> Tuple[int, str]:
    """
    Return (score_adjustment, reason) based on FII index futures positioning.

    Scoring rules (only fires when both net position AND accelerating trend agree):
      FII net long  + trend INCREASING_LONG:
        LONG  → +6  (institutions positioned with you)
        SHORT → -4  (fighting institutional positioning)

      FII net short + trend INCREASING_SHORT:
        SHORT → +6  (institutions positioned with you)
        LONG  → -4  (fighting institutional positioning)

      Flat trend / unclear net / data unavailable → (0, "")

    Args:
        direction: "LONG" or "SHORT"

    Returns:
        (score_adj, reason) — score_adj is an int in {-4, 0, +6}
        On any error, returns (0, "") — fail-open.
    """
    try:
        pos = get_fii_futures_position()
        if not pos:
            return (0, "")

        fii_net = pos.get("fii_net", 0)
        trend   = pos.get("trend",   "FLAT")
        reason  = pos.get("reason",  "")

        direction = direction.upper()

        # Score only when net position and INCREASING trend both confirm direction
        if fii_net > 0 and trend == "INCREASING_LONG":
            if direction == "LONG":
                return (6, f"FII_FUTURES bullish tailwind: {reason}")
            if direction == "SHORT":
                return (-4, f"FII_FUTURES bullish headwind (fighting FII longs): {reason}")

        if fii_net < 0 and trend == "INCREASING_SHORT":
            if direction == "SHORT":
                return (6, f"FII_FUTURES bearish tailwind: {reason}")
            if direction == "LONG":
                return (-4, f"FII_FUTURES bearish headwind (fighting FII shorts): {reason}")

        # Net position present but trend is flat — no score adjustment
        # Avoids false signals on stale or reversing positioning
        return (0, "")

    except Exception as exc:
        logger.debug("get_fii_futures_score failed (fail-open): %s", exc)
        return (0, "")
