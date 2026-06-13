"""
gex_signal_india.py — NIFTY Gamma Exposure (GEX) Regime Signal

Research: Positive GEX = dealer gamma dampens moves (range-bound, mean reversion works).
          Negative GEX = dealer gamma amplifies moves (trending, momentum works).
          GEX flip level = strike where net GEX = 0 (acts as intraday pivot).

Formula (Black-Scholes gamma, per strike):
  GEX_call = spot^2 * gamma_call * call_OI * lot_size / 1e7
  GEX_put  = spot^2 * gamma_put  * put_OI  * lot_size / 1e7   (puts SUBTRACT)
  Net GEX  = sum(GEX_call) - sum(GEX_put) across all strikes

Score impact:
  POSITIVE GEX + signal is MEAN_REVERSION:  +8 (regime confirms)
  POSITIVE GEX + signal is MOMENTUM/BREAKOUT: -6 (regime fights it)
  NEGATIVE GEX + signal is MOMENTUM:         +8
  NEGATIVE GEX + signal is MEAN_REVERSION:  -6
  NEAR FLIP ZONE (|GEX| < threshold):         0 (ambiguous)

Magnitude boost: if |gex_total| > 2× 20-day median → ±10 / ±8 instead of ±8 / ±6.
Cache TTL: 900 s (15 min). History persisted to data/gex_history.json.
"""

import json
import logging
import math
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger("gex_signal_india")

# ── Constants ─────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")
NIFTY_LOT_SIZE = 25
RISK_FREE_RATE = 0.065          # India RBI repo rate proxy (6.5%)
_TTL = 900.0                    # 15-minute cache
_HISTORY_FILE = Path(__file__).parent.parent / "data" / "gex_history.json"
_GEX_HISTORY_MAX = 20           # rolling days kept on disk
_NEUTRAL_BAND_PCT = 0.10        # |GEX| < 10% of range → NEUTRAL

# ── Module-level state ────────────────────────────────────────────────────────
_session: Optional[requests.Session] = None
_gex_cache: Optional[Tuple[float, dict]] = None   # (timestamp, result_dict)
_gex_history: List[float] = []                     # rolling 20-day GEX totals


# ══════════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _norm_pdf(x: float) -> float:
    """Standard normal probability density function.  Fallback without scipy."""
    try:
        from scipy.stats import norm
        return float(norm.pdf(x))
    except ImportError:
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_gamma(S: float, K: float, sigma: float, T: float) -> float:
    """
    Black-Scholes gamma for both calls and puts (identical formula).
    Returns 0.0 on any invalid input.
    """
    if S <= 0.0 or K <= 0.0 or sigma <= 0.0 or T <= 0.0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (RISK_FREE_RATE + 0.5 * sigma ** 2) * T) / (
            sigma * math.sqrt(T)
        )
        return _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    except Exception:
        return 0.0


def _get_session() -> requests.Session:
    """Return a primed NSE session (creates + primes once per process)."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })
        try:
            _session.get("https://www.nseindia.com/", timeout=8)
        except Exception:
            pass
    return _session


def _parse_expiry_date(expiry_str: str) -> Optional[date]:
    """
    Parse NSE expiry date string like '07-Nov-2024' into datetime.date.
    Returns None on failure.
    """
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry_str.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _nearest_weekly_expiry(expiry_dates: List[str]) -> date:
    """
    From the list of expiry date strings returned by NSE, pick the nearest
    weekly expiry on or after today (IST).  Falls back to the next Thursday
    if parsing fails for all entries.
    """
    today_ist = datetime.now(IST).date()
    parsed: List[date] = []
    for s in expiry_dates:
        d = _parse_expiry_date(s)
        if d is not None and d >= today_ist:
            parsed.append(d)
    if parsed:
        return min(parsed)
    # Fallback: next Thursday
    days_ahead = (3 - today_ist.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today_ist + timedelta(days=days_ahead)


def _time_to_expiry_years(expiry: date) -> float:
    """Convert calendar days to expiry into annualised fraction (365 calendar days)."""
    today_ist = datetime.now(IST).date()
    delta = (expiry - today_ist).days
    cal_days = max(delta, 1)
    return cal_days / 365.0


# ── History persistence ───────────────────────────────────────────────────────

def _load_gex_history() -> None:
    global _gex_history
    try:
        if _HISTORY_FILE.exists():
            raw = json.loads(_HISTORY_FILE.read_text())
            if isinstance(raw, list):
                _gex_history = [float(v) for v in raw if isinstance(v, (int, float))]
    except Exception:
        _gex_history = []


def _save_gex_history() -> None:
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_FILE.write_text(
            json.dumps(_gex_history[-_GEX_HISTORY_MAX:])
        )
    except Exception:
        pass


def _append_history(gex_total: float) -> None:
    global _gex_history
    _gex_history.append(gex_total)
    if len(_gex_history) > _GEX_HISTORY_MAX:
        _gex_history = _gex_history[-_GEX_HISTORY_MAX:]
    _save_gex_history()


# ── Core fetch & compute ──────────────────────────────────────────────────────

def _fetch_nifty_chain() -> dict:
    """
    Fetch raw NIFTY option chain from NSE public API.
    Returns dict with keys: strikes, spot, expiry_dates.
    Fail-open returns {} on any error.
    """
    try:
        s = _get_session()
        resp = s.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            headers={"Referer": "https://www.nseindia.com/"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", {})
        return {
            "strikes": records.get("data", []),
            "spot": float(records.get("underlyingValue", 0) or 0),
            "expiry_dates": records.get("expiryDates", []),
        }
    except Exception as e:
        logger.debug(f"gex_india _fetch_nifty_chain: {e}")
        return {}


def _compute_gex(raw: dict) -> dict:
    """
    Compute Net GEX from raw option chain data.

    Returns dict with:
      gex_total     — net GEX in ₹ crore units (lot-size & S^2 scaled)
      gex_by_strike — {strike: net_gex_value} for flip-level search
      spot          — NIFTY spot used
      expiry        — date object used for T
    """
    strikes_data: list = raw.get("strikes", [])
    spot: float = raw.get("spot", 0.0)
    expiry_dates: list = raw.get("expiry_dates", [])

    if not strikes_data or spot <= 0:
        return {}

    expiry: date = _nearest_weekly_expiry(expiry_dates)
    T: float = _time_to_expiry_years(expiry)

    gex_by_strike: Dict[float, float] = {}
    total_gex: float = 0.0

    for entry in strikes_data:
        ce = entry.get("CE") or {}
        pe = entry.get("PE") or {}
        strike = float(entry.get("strikePrice") or 0)
        if strike <= 0:
            continue

        # IV: NSE returns percentage (e.g. 12.5 means 12.5%), divide by 100.
        # Floor at small positive value to avoid zero-IV gamma blowups near expiry.
        ce_iv_raw = float(ce.get("impliedVolatility") or 0)
        pe_iv_raw = float(pe.get("impliedVolatility") or 0)
        ce_iv = max(ce_iv_raw / 100.0, 0.01) if ce_iv_raw > 0 else 0.15
        pe_iv = max(pe_iv_raw / 100.0, 0.01) if pe_iv_raw > 0 else 0.15

        ce_oi = float(ce.get("openInterest") or 0)
        pe_oi = float(pe.get("openInterest") or 0)

        # GEX contribution = S^2 * gamma * OI * lot_size / 1e7
        # Dividing by 1e7 keeps the number in a tractable ₹ crore range.
        call_gamma = _bs_gamma(spot, strike, ce_iv, T)
        put_gamma = _bs_gamma(spot, strike, pe_iv, T)

        gex_call = (spot ** 2) * call_gamma * ce_oi * NIFTY_LOT_SIZE / 1e7
        gex_put = (spot ** 2) * put_gamma * pe_oi * NIFTY_LOT_SIZE / 1e7

        net_strike_gex = gex_call - gex_put
        gex_by_strike[strike] = net_strike_gex
        total_gex += net_strike_gex

    return {
        "gex_total": total_gex,
        "gex_by_strike": gex_by_strike,
        "spot": spot,
        "expiry": expiry,
    }


def _find_flip_level(gex_by_strike: Dict[float, float], spot: float) -> float:
    """
    Find the strike where cumulative GEX (sorted by proximity to spot) crosses zero.
    That strike is the GEX flip level — acts as an intraday pivot.
    Returns 0.0 if no zero-crossing is found.
    """
    if not gex_by_strike:
        return 0.0
    try:
        # Sort strikes by absolute distance from spot (nearest first)
        sorted_strikes = sorted(gex_by_strike.keys(), key=lambda k: abs(k - spot))
        cumulative = 0.0
        prev_strike = spot
        for strike in sorted_strikes:
            prev_cumulative = cumulative
            cumulative += gex_by_strike[strike]
            if prev_cumulative * cumulative < 0:
                # Sign change between prev_strike and this strike — use midpoint
                return round((prev_strike + strike) / 2.0, 0)
            prev_strike = strike
        return 0.0
    except Exception:
        return 0.0


def _gex_percentile(gex_total: float, history: List[float]) -> float:
    """
    Return percentile (0–100) of current GEX relative to 20-day history.
    Returns 50.0 if history is too short to be meaningful.
    """
    if len(history) < 3:
        return 50.0
    try:
        below = sum(1 for v in history if v < gex_total)
        return round(100.0 * below / len(history), 1)
    except Exception:
        return 50.0


def _classify_regime(
    gex_total: float, history: List[float]
) -> Tuple[str, str]:
    """
    Classify GEX into POSITIVE / NEGATIVE / NEUTRAL based on sign and magnitude.
    NEUTRAL band = |GEX| < 10% of historical range (history-aware) or near zero
    when no history is available.
    Returns (regime_str, reason_str).
    """
    if not history or len(history) < 3:
        if gex_total > 0:
            return ("POSITIVE", f"GEX={gex_total:.1f} POSITIVE (no history)")
        elif gex_total < 0:
            return ("NEGATIVE", f"GEX={gex_total:.1f} NEGATIVE (no history)")
        return ("NEUTRAL", "GEX~0 NEUTRAL")

    hist_min = min(history)
    hist_max = max(history)
    hist_range = hist_max - hist_min

    if hist_range < 1e-9:
        neutral_threshold = max(abs(gex_total) * 0.10, 1e-6)
    else:
        neutral_threshold = hist_range * _NEUTRAL_BAND_PCT

    if abs(gex_total) < neutral_threshold:
        return ("NEUTRAL", f"GEX={gex_total:.1f} NEUTRAL near flip zone")
    elif gex_total > 0:
        return (
            "POSITIVE",
            f"GEX={gex_total:.1f} POSITIVE dealer long-gamma range-compress",
        )
    else:
        return (
            "NEGATIVE",
            f"GEX={gex_total:.1f} NEGATIVE dealer short-gamma trend-amplify",
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Public API
# ══════════════════════════════════════════════════════════════════════════════

def get_nifty_gex() -> dict:
    """
    Compute full NIFTY Gamma Exposure profile.

    Returns dict:
      gex_total      (float) — net GEX in ₹ crore units
      regime         (str)   — 'POSITIVE' / 'NEGATIVE' / 'NEUTRAL'
      flip_level     (float) — strike where GEX crosses zero (intraday pivot)
      gex_percentile (float) — current GEX vs 20-day history (0–100)
      reason         (str)   — human-readable explanation

    Fail-open: returns safe defaults (zeros / NEUTRAL) on any error.
    """
    _SAFE = {
        "gex_total": 0.0,
        "regime": "NEUTRAL",
        "flip_level": 0.0,
        "gex_percentile": 50.0,
        "reason": "",
    }

    global _gex_cache

    try:
        # ── Cache check ───────────────────────────────────────────────────────
        now_ts = _time.monotonic()
        if _gex_cache is not None:
            cached_ts, cached_result = _gex_cache
            if now_ts - cached_ts < _TTL:
                logger.debug("gex_india: cache hit")
                return cached_result

        # ── Fetch & compute ───────────────────────────────────────────────────
        raw = _fetch_nifty_chain()
        if not raw:
            logger.debug("gex_india: empty chain response")
            return _SAFE

        computed = _compute_gex(raw)
        if not computed:
            logger.debug("gex_india: compute returned empty")
            return _SAFE

        gex_total: float = computed["gex_total"]
        gex_by_strike: Dict[float, float] = computed["gex_by_strike"]
        spot: float = computed["spot"]

        # ── History ───────────────────────────────────────────────────────────
        if not _gex_history:
            _load_gex_history()
        _append_history(gex_total)

        # ── Regime classification ─────────────────────────────────────────────
        regime, reason = _classify_regime(gex_total, _gex_history)

        # ── Flip level ────────────────────────────────────────────────────────
        flip_level = _find_flip_level(gex_by_strike, spot)

        # ── Percentile ────────────────────────────────────────────────────────
        # Exclude current reading from percentile denominator
        gex_pct = _gex_percentile(gex_total, _gex_history[:-1])

        # Augment reason with flip level if found
        if flip_level > 0:
            reason += f" | flip@{flip_level:.0f}"

        result = {
            "gex_total": round(gex_total, 2),
            "regime": regime,
            "flip_level": flip_level,
            "gex_percentile": gex_pct,
            "reason": reason,
        }

        _gex_cache = (now_ts, result)
        logger.debug(f"gex_india: {result}")
        return result

    except Exception as e:
        logger.debug(f"gex_india get_nifty_gex: {e}")
        return _SAFE


def get_gex_score(direction: str, signal_type: str = "MOMENTUM") -> Tuple[int, str]:
    """
    Return (score_adjustment, reason_string) for a given trade signal
    based on the current NIFTY GEX regime.

    Parameters
    ----------
    direction   : "LONG" or "SHORT"
    signal_type : "MOMENTUM" or "MEAN_REVERSION"

    Scoring logic
    -------------
    POSITIVE GEX + MEAN_REVERSION  → +8  (regime confirms mean-revert)
    POSITIVE GEX + MOMENTUM        → -6  (regime fights breakout/momentum)
    NEGATIVE GEX + MOMENTUM        → +8  (regime amplifies both directions)
    NEGATIVE GEX + MEAN_REVERSION  → -6  (regime fights mean reversion)
    NEUTRAL GEX                    →  0  (ambiguous, no adjustment)

    Magnitude boost: if |gex_total| > 2× 20-day median absolute value,
    scores shift to ±10 / ±8 instead of ±8 / ±6.

    Returns (0, "") on any error (fail-open).
    """
    try:
        gex_data = get_nifty_gex()
        regime = gex_data.get("regime", "NEUTRAL")
        gex_total = gex_data.get("gex_total", 0.0)
        reason_base = gex_data.get("reason", "")

        if regime == "NEUTRAL":
            return (0, "")

        # ── Magnitude boost check ─────────────────────────────────────────────
        use_strong = False
        history_snapshot = list(_gex_history)
        if len(history_snapshot) >= 3:
            abs_vals = sorted(abs(v) for v in history_snapshot)
            mid = len(abs_vals) // 2
            if len(abs_vals) % 2 == 0:
                median_abs = (abs_vals[mid - 1] + abs_vals[mid]) / 2.0
            else:
                median_abs = abs_vals[mid]
            if median_abs > 0 and abs(gex_total) > 2.0 * median_abs:
                use_strong = True

        sig = signal_type.upper()

        # ── Score matrix ──────────────────────────────────────────────────────
        score: int = 0

        if regime == "POSITIVE":
            if sig == "MEAN_REVERSION":
                score = 10 if use_strong else 8
                tag = f"GEX_POS_MR_CONFIRM{'+STRONG' if use_strong else ''}:{score:+d}"
            else:
                # MOMENTUM or BREAKOUT — positive GEX opposes it
                score = -8 if use_strong else -6
                tag = f"GEX_POS_MOM_FIGHT{'+STRONG' if use_strong else ''}:{score:+d}"

        elif regime == "NEGATIVE":
            if sig == "MOMENTUM":
                # Negative GEX amplifies moves — benefits both LONG and SHORT momentum
                score = 10 if use_strong else 8
                tag = f"GEX_NEG_MOM_AMPLIFY{'+STRONG' if use_strong else ''}:{score:+d}"
            else:
                # MEAN_REVERSION — negative GEX opposes it
                score = -8 if use_strong else -6
                tag = f"GEX_NEG_MR_FIGHT{'+STRONG' if use_strong else ''}:{score:+d}"

        if score == 0:
            return (0, "")

        full_reason = f"{tag} | {reason_base}" if reason_base else tag
        return (score, full_reason)

    except Exception as e:
        logger.debug(f"gex_india get_gex_score: {e}")
        return (0, "")


# ══════════════════════════════════════════════════════════════════════════════
#  Unit 8: GEX Dealer Flow Inference + IV Term Structure
# ══════════════════════════════════════════════════════════════════════════════

# Rolling GEX readings for rate-of-change computation (intraday)
_gex_roc_buffer: List[Tuple[float, float]] = []   # [(monotonic_ts, gex_value)]
_ROC_WINDOW_MAX = 12                               # keep last 12 readings (3h at 15-min TTL)


def _record_gex_roc(gex_total: float) -> None:
    """Append current GEX reading to the intraday rate-of-change buffer."""
    global _gex_roc_buffer
    _gex_roc_buffer.append((_time.monotonic(), gex_total))
    if len(_gex_roc_buffer) > _ROC_WINDOW_MAX:
        _gex_roc_buffer = _gex_roc_buffer[-_ROC_WINDOW_MAX:]


def _compute_gex_roc() -> Tuple[float, str]:
    """
    Compute GEX rate-of-change over the last 2 available readings.

    Returns (roc_normalised, direction_str) where:
      roc_normalised > +1.5  → DEALER_BUYING  (GEX rising fast → calls bought → bullish)
      roc_normalised < -1.5  → DEALER_SELLING (GEX falling fast → puts bought → bearish)
      else                   → DEALER_NEUTRAL
    """
    if len(_gex_roc_buffer) < 2:
        return (0.0, "DEALER_NEUTRAL")
    try:
        prev_ts, prev_gex = _gex_roc_buffer[-2]
        curr_ts, curr_gex = _gex_roc_buffer[-1]
        age = max(curr_ts - prev_ts, 60.0)          # at least 60 s between readings

        roc_raw = curr_gex - prev_gex
        # Normalise by historical std-dev of GEX if available, else by |prev|
        if len(_gex_history) >= 3:
            import statistics as _stats
            try:
                std = _stats.stdev(_gex_history) or 1.0
            except Exception:
                std = max(abs(prev_gex), 1.0)
        else:
            std = max(abs(prev_gex), 1.0)

        roc_norm = roc_raw / std

        if roc_norm > 1.5:
            return (roc_norm, "DEALER_BUYING")
        elif roc_norm < -1.5:
            return (roc_norm, "DEALER_SELLING")
        return (roc_norm, "DEALER_NEUTRAL")
    except Exception:
        return (0.0, "DEALER_NEUTRAL")


def get_dealer_flow_signal(direction: str) -> Tuple[int, str]:
    """
    Infer dealer buy/sell pressure from GEX rate-of-change.

    DEALER_BUYING  (GEX rising):  dealers accumulating long gamma = bullish bias
      → LONG +5, SHORT -4
    DEALER_SELLING (GEX falling): dealers shedding gamma / buying puts = bearish bias
      → SHORT +5, LONG -4

    Returns (score_adj, reason).  Fail-open: (0, "").
    """
    try:
        gex_data = get_nifty_gex()   # updates cache + history
        _record_gex_roc(gex_data.get("gex_total", 0.0))

        roc_norm, flow = _compute_gex_roc()
        if flow == "DEALER_NEUTRAL":
            return (0, "")

        if flow == "DEALER_BUYING":
            score = 5 if direction == "LONG" else -4
            return (score, f"DEALER_FLOW_BUY roc={roc_norm:+.2f}σ:{score:+d}")
        else:  # DEALER_SELLING
            score = 5 if direction == "SHORT" else -4
            return (score, f"DEALER_FLOW_SELL roc={roc_norm:+.2f}σ:{score:+d}")
    except Exception as e:
        logger.debug(f"get_dealer_flow_signal: {e}")
        return (0, "")


# ── IV Term Structure ─────────────────────────────────────────────────────────

def _fetch_nifty_chain_by_expiry() -> Dict[str, List[dict]]:
    """
    Fetch NIFTY option chain and group strike entries by expiryDate string.
    Returns {expiry_str: [entry, ...]}. Fail-open: {}.
    """
    try:
        s = _get_session()
        resp = s.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            headers={"Referer": "https://www.nseindia.com/"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", {}).get("data", [])
        grouped: Dict[str, List[dict]] = {}
        for entry in records:
            exp = entry.get("expiryDate") or entry.get("CE", {}).get("expiryDate") or ""
            if not exp:
                ce = entry.get("CE") or {}
                pe = entry.get("PE") or {}
                exp = ce.get("expiryDate") or pe.get("expiryDate") or "UNKNOWN"
            grouped.setdefault(exp, []).append(entry)
        return grouped
    except Exception as e:
        logger.debug(f"_fetch_nifty_chain_by_expiry: {e}")
        return {}


def _avg_iv_for_expiry(entries: List[dict], spot: float, window_pct: float = 0.05) -> float:
    """
    Compute OI-weighted average ATM implied volatility for a set of option entries.
    Uses strikes within ±window_pct of spot. Returns 0.0 if no valid data.
    """
    if not entries or spot <= 0:
        return 0.0
    iv_sum = oi_sum = 0.0
    for entry in entries:
        strike = float(entry.get("strikePrice") or 0)
        if strike <= 0 or abs(strike - spot) / spot > window_pct:
            continue
        for side in ("CE", "PE"):
            opt = entry.get(side) or {}
            iv  = float(opt.get("impliedVolatility") or 0)
            oi  = float(opt.get("openInterest") or 0)
            if iv > 0 and oi > 0:
                iv_sum  += iv * oi
                oi_sum  += oi
    return iv_sum / oi_sum if oi_sum > 0 else 0.0


def get_iv_term_structure() -> Tuple[str, float, str]:
    """
    Compare near-expiry vs next-expiry ATM implied volatility.

    Normal term structure  (near_iv < far_iv): calm near-term, markets healthy.
    Inverted term structure (near_iv > far_iv): near-term fear/event risk.

    Returns (regime, slope, reason) where:
      regime: "NORMAL" | "INVERTED" | "FLAT" | "UNKNOWN"
      slope : near_iv - far_iv  (positive = inverted, negative = normal)
      reason: human-readable string

    Fail-open returns ("UNKNOWN", 0.0, "").
    """
    _EMPTY = ("UNKNOWN", 0.0, "")
    try:
        by_expiry = _fetch_nifty_chain_by_expiry()
        if len(by_expiry) < 2:
            return _EMPTY

        # Parse and sort expiry dates
        sorted_expiries: List[Tuple[date, str]] = []
        for exp_str in by_expiry:
            d = _parse_expiry_date(exp_str)
            if d is not None:
                sorted_expiries.append((d, exp_str))
        sorted_expiries.sort(key=lambda x: x[0])

        if len(sorted_expiries) < 2:
            return _EMPTY

        today = datetime.now(IST).date()
        # Near = soonest expiry on or after today; far = second
        future_expiries = [(d, s) for d, s in sorted_expiries if d >= today]
        if len(future_expiries) < 2:
            return _EMPTY

        near_date, near_str = future_expiries[0]
        far_date,  far_str  = future_expiries[1]

        # Need spot price — re-use main chain fetch
        raw = _fetch_nifty_chain()
        spot = raw.get("spot", 0.0) if raw else 0.0
        if spot <= 0:
            return _EMPTY

        near_iv = _avg_iv_for_expiry(by_expiry[near_str], spot)
        far_iv  = _avg_iv_for_expiry(by_expiry[far_str],  spot)

        if near_iv <= 0 or far_iv <= 0:
            return _EMPTY

        slope = near_iv - far_iv           # positive = inverted, negative = normal

        if abs(slope) < 0.5:
            regime = "FLAT"
            reason = f"IV_TS FLAT near={near_iv:.1f} far={far_iv:.1f}"
        elif slope > 0:
            regime = "INVERTED"
            reason = f"IV_TS INVERTED near={near_iv:.1f}>far={far_iv:.1f} slope={slope:+.1f}"
        else:
            regime = "NORMAL"
            reason = f"IV_TS NORMAL near={near_iv:.1f}<far={far_iv:.1f} slope={slope:+.1f}"

        return (regime, round(slope, 2), reason)

    except Exception as e:
        logger.debug(f"get_iv_term_structure: {e}")
        return ("UNKNOWN", 0.0, "")


def get_iv_term_structure_score(direction: str) -> Tuple[int, str]:
    """
    Score signal from IV term structure.

    NORMAL  (near < far)  → calm, no fear premium → slight bullish bias: LONG +3, SHORT -2
    INVERTED (near > far) → near-term fear/event  → cautious: SHORT +4, LONG -3
    FLAT / UNKNOWN        → no adjustment

    Returns (score_adj, reason). Fail-open: (0, "").
    """
    try:
        regime, slope, reason = get_iv_term_structure()
        if regime in ("FLAT", "UNKNOWN"):
            return (0, "")
        if regime == "NORMAL":
            score = 3 if direction == "LONG" else -2
        else:  # INVERTED
            score = 4 if direction == "SHORT" else -3
        return (score, f"{reason}:{score:+d}")
    except Exception as e:
        logger.debug(f"get_iv_term_structure_score: {e}")
        return (0, "")


# ── Load history on import ────────────────────────────────────────────────────
_load_gex_history()
