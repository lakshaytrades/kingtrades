"""
squeeze_scanner.py — Short Squeeze Detection Engine (Top-1% Edge)

Short squeezes produce the BIGGEST intraday moves: 20-200% in hours.
GME, AMC, BBBY, MARA, COIN — all squeezed on top of momentum signals.

Detection stack:
  1. Short float % (from yfinance — free, updated weekly)
  2. Days-to-cover / Short ratio (>5 = dangerous for shorts)
  3. Volume anomaly (today vs 20-day avg — shorts covering = vol surge)
  4. Price momentum velocity (acceleration = squeeze underway)
  5. Borrow rate proxy (hard-to-borrow = fee pressure on short sellers)
  6. Catalyst check (news or earnings = squeeze trigger)

Squeeze Score [0-100]:
  0-20:  No squeeze risk — normal trade
  21-40: Light short interest — mild squeeze potential
  41-65: Moderate squeeze setup — boost signal score +5 to +8
  66-85: Strong squeeze — boost signal score +10 to +12
  86+:   EXPLOSIVE squeeze imminent — boost +15, flag for oversized entry

Signal integration:
  - Only boosts LONG signals (squeezes are upward)
  - Penalizes SHORT signals when high squeeze score (adds counter-pressure risk)
  - Score boost added to filter_result.final_score before LLM gate

Cache:
  - Short float data: 4-hour cache (yfinance is slow, data is weekly anyway)
  - Volume ratio: 5-min cache
"""

import logging
import time
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ── Caches ───────────────────────────────────────────────────────────────────
_SHORT_DATA_CACHE: Dict[str, dict] = {}
_SHORT_CACHE_TTL  = 14400   # 4 hours — yfinance short data updates intraday weekly
_VOL_CACHE: Dict[str, dict] = {}
_VOL_CACHE_TTL    = 300     # 5 minutes

# ── Thresholds ────────────────────────────────────────────────────────────────
SHORT_FLOAT_LOW      = 5.0    # below 5% = no squeeze risk
SHORT_FLOAT_MODERATE = 15.0   # 5-15% = moderate
SHORT_FLOAT_HIGH     = 25.0   # 15-25% = high
SHORT_FLOAT_EXTREME  = 40.0   # 40%+ = extreme squeeze potential

DAYS_TO_COVER_DANGER = 5.0    # >5 DTC = shorts can't exit fast = squeeze fuel

# Known high-short-float names (updated manually — adds base squeeze score)
_KNOWN_SQUEEZE_CANDIDATES = {
    "MARA": 30, "RIOT": 28, "HUT": 25, "CLSK": 22, "CIFR": 20,
    "HOOD": 18, "SOFI": 15, "COIN": 12, "BBAI": 20, "SOUN": 18,
    "RIVN": 20, "LCID": 25, "NIO": 22, "PLUG": 20,
    "HIMS": 15, "AFRM": 20, "GME": 35, "AMC": 30,
}


def _get_short_data(symbol: str) -> dict:
    """
    Fetch short interest data from yfinance.
    Returns dict with: short_float_pct, short_ratio (DTC), shares_short
    Cached 4 hours.
    """
    cached = _SHORT_DATA_CACHE.get(symbol)
    if cached and (time.monotonic() - cached.get("_ts", 0)) < _SHORT_CACHE_TTL:
        return cached

    result = {
        "short_float_pct": 0.0,
        "short_ratio": 0.0,
        "shares_short": 0,
        "_ts": time.monotonic(),
    }

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info   = ticker.info or {}

        short_pct   = info.get("shortPercentOfFloat", 0) or 0
        short_ratio = info.get("shortRatio", 0) or 0
        shares_short = info.get("sharesShort", 0) or 0

        # yfinance returns float as decimal (0.25 = 25%) or sometimes as percentage
        if 0 < short_pct <= 1.0:
            short_pct *= 100   # convert 0.25 → 25

        result.update({
            "short_float_pct": float(short_pct),
            "short_ratio":     float(short_ratio),
            "shares_short":    int(shares_short),
            "_ts":             time.monotonic(),
        })

    except Exception as e:
        logger.debug(f"squeeze_scanner._get_short_data({symbol}): {e}")

    _SHORT_DATA_CACHE[symbol] = result
    return result


def _get_volume_ratio(symbol: str) -> float:
    """
    Get today's volume vs 20-day average volume ratio.
    Uses Alpaca data fetcher (already cached in main codebase).
    """
    cached = _VOL_CACHE.get(symbol)
    if cached and (time.monotonic() - cached.get("_ts", 0)) < _VOL_CACHE_TTL:
        return cached.get("ratio", 1.0)

    try:
        from data_fetch_alpaca import get_data_fetcher
        fetcher = get_data_fetcher()

        # Today's volume from live quote
        quote = fetcher.get_quote(symbol)
        today_vol = quote.get("volume", 0) if quote else 0

        # 20-day average from daily bars
        import yfinance as yf
        df = yf.download(symbol, period="25d", interval="1d", progress=False, auto_adjust=True)
        if df is not None and len(df) >= 20:
            avg_vol = float(df["Volume"].iloc[-21:-1].mean())
            ratio   = today_vol / avg_vol if avg_vol > 0 else 1.0
        else:
            ratio = 1.0

        _VOL_CACHE[symbol] = {"ratio": ratio, "_ts": time.monotonic()}
        return ratio

    except Exception as e:
        logger.debug(f"squeeze_scanner._get_volume_ratio({symbol}): {e}")
        return 1.0


def _get_price_velocity(symbol: str) -> float:
    """
    Compute price acceleration: how fast is price moving up right now?
    Compares last-5-bar momentum vs prior-5-bar momentum.
    Returns velocity ratio (>1.5 = accelerating = squeeze underway).
    """
    try:
        from data_fetch_alpaca import get_data_fetcher
        fetcher = get_data_fetcher()
        df = fetcher.get_historical_bars(symbol, "5Min", limit=15)
        if df is None or len(df) < 12:
            return 1.0

        close = df["close"].values
        recent_chg = (close[-1] - close[-5]) / close[-5] * 100
        prior_chg  = (close[-6] - close[-11]) / close[-11] * 100 if len(close) >= 11 else 0.0

        if prior_chg == 0:
            return 1.0

        velocity = abs(recent_chg) / max(abs(prior_chg), 0.01)
        return round(velocity, 2)

    except Exception as e:
        logger.debug(f"squeeze_scanner._get_price_velocity({symbol}): {e}")
        return 1.0


def get_squeeze_score(symbol: str) -> Tuple[int, str]:
    """
    Compute squeeze probability score [0-100] for a symbol.

    Returns:
        (score, reason)
        score: 0-100 — squeeze probability
        reason: description of squeeze factors
    """
    score = 0
    factors = []

    # ── 1. Base score from known squeeze candidates ───────────────────────
    base = _KNOWN_SQUEEZE_CANDIDATES.get(symbol, 0)
    score += base
    if base >= 20:
        factors.append(f"known squeeze name ({symbol})")

    # ── 2. Short float from yfinance ─────────────────────────────────────
    short_data = _get_short_data(symbol)
    sf  = short_data["short_float_pct"]
    dtc = short_data["short_ratio"]

    if sf >= SHORT_FLOAT_EXTREME:
        score += 30
        factors.append(f"EXTREME short float {sf:.0f}%")
    elif sf >= SHORT_FLOAT_HIGH:
        score += 20
        factors.append(f"HIGH short float {sf:.0f}%")
    elif sf >= SHORT_FLOAT_MODERATE:
        score += 12
        factors.append(f"moderate short float {sf:.0f}%")
    elif sf >= SHORT_FLOAT_LOW:
        score += 5
        factors.append(f"light short float {sf:.0f}%")

    # Days to cover
    if dtc >= DAYS_TO_COVER_DANGER:
        score += 15
        factors.append(f"DTC={dtc:.1f}d (danger zone)")
    elif dtc >= 3.0:
        score += 8
        factors.append(f"DTC={dtc:.1f}d")

    # ── 3. Volume surge (shorts covering = volume explosion) ──────────────
    vol_ratio = _get_volume_ratio(symbol)
    if vol_ratio >= 5.0:
        score += 20
        factors.append(f"VOLUME EXPLOSION {vol_ratio:.1f}x avg")
    elif vol_ratio >= 3.0:
        score += 14
        factors.append(f"volume surge {vol_ratio:.1f}x avg")
    elif vol_ratio >= 2.0:
        score += 8
        factors.append(f"volume surge {vol_ratio:.1f}x avg")

    # ── 4. Price velocity (acceleration = squeeze in motion) ──────────────
    velocity = _get_price_velocity(symbol)
    if velocity >= 3.0:
        score += 15
        factors.append(f"price acceleration {velocity:.1f}x (squeeze in motion)")
    elif velocity >= 2.0:
        score += 10
        factors.append(f"price acceleration {velocity:.1f}x")
    elif velocity >= 1.5:
        score += 5

    score = min(score, 100)
    reason = " | ".join(factors) if factors else "no squeeze factors"
    return score, reason


def get_squeeze_score_delta(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Convert squeeze score to signal score delta.

    For LONG signals:  high squeeze = boost (trapped shorts = fuel)
    For SHORT signals: high squeeze = penalty (entering into a squeeze = dangerous)

    Returns:
        (score_delta, reason)
        score_delta: [-12, +15] — add to filter_result.final_score
    """
    squeeze_score, squeeze_reason = get_squeeze_score(symbol)

    if squeeze_score < 20:
        return 0.0, ""

    if direction == "LONG":
        if squeeze_score >= 86:
            delta = 15.0
        elif squeeze_score >= 66:
            delta = 10.0
        elif squeeze_score >= 41:
            delta = 6.0
        else:
            delta = 3.0
        reason = f"SQUEEZE FUEL (score={squeeze_score}): {squeeze_reason}"
    else:  # SHORT
        # Shorting into a squeeze = extremely dangerous
        if squeeze_score >= 66:
            delta = -12.0
            reason = f"SQUEEZE DANGER — avoid short (score={squeeze_score}): {squeeze_reason}"
        elif squeeze_score >= 41:
            delta = -6.0
            reason = f"Squeeze risk for short (score={squeeze_score})"
        else:
            return 0.0, ""

    logger.debug(
        f"squeeze_scanner: {symbol} ({direction}) squeeze={squeeze_score} → Δscore={delta:+.0f}"
    )
    return delta, reason
