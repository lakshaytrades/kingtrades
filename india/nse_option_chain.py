"""
nse_option_chain.py — NSE Option Chain Intelligence (God Mode)
Free NSE public API. No auth required but needs cookie priming.
6 signals: PCR, Max Pain, GEX proxy, OI walls, IV skew, OI velocity.
Score: -15 to +15. Cache 10 min. All fail-open.
"""
import logging
import time as _time
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger("nse_option_chain")
_cache: Dict[str, Tuple[float, dict]] = {}
_TTL = 600.0
_session = None


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        try:
            _session.get("https://www.nseindia.com", headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }, timeout=8)
        except Exception:
            pass
    return _session


def _fetch_chain(symbol: str) -> dict:
    now = _time.monotonic()
    if symbol in _cache and now - _cache[symbol][0] < _TTL:
        return _cache[symbol][1]
    try:
        s = _get_session()
        resp = s.get(
            f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}",
            headers={"Referer": "https://www.nseindia.com/"},
            timeout=12,
        )
        data = resp.json()
        records = data.get("records", {})
        strikes = records.get("data", [])
        spot = float(records.get("underlyingValue", 0))
        parsed = {"strikes": strikes, "spot": spot}
        _cache[symbol] = (now, parsed)
        return parsed
    except Exception as e:
        logger.debug(f"option_chain {symbol}: {e}")
        return {}


def get_option_chain_score(symbol: str, direction: str,
                           current_price: float) -> Tuple[float, str]:
    """
    Compute god-mode option chain score from 6 sub-signals.
    Score is clamped to [-15, +15]. Fail-open returns (0.0, "").
    """
    try:
        data = _fetch_chain(symbol)
        if not data:
            return (0.0, "")

        strikes = data.get("strikes", [])
        spot = data.get("spot", current_price) or current_price
        if not strikes or spot <= 0:
            return (0.0, "")

        score = 0.0
        reasons = []

        # Collect per-strike OI data
        call_oi_map: Dict[float, float] = {}
        put_oi_map:  Dict[float, float] = {}
        call_iv_atm: list = []
        put_iv_atm:  list = []
        call_chg_atm: list = []
        put_chg_atm:  list = []

        for entry in strikes:
            ce = entry.get("CE", {}) or {}
            pe = entry.get("PE", {}) or {}
            strike = float(entry.get("strikePrice", 0) or 0)
            if strike <= 0:
                continue

            ce_oi = float(ce.get("openInterest", 0) or 0)
            pe_oi = float(pe.get("openInterest", 0) or 0)
            call_oi_map[strike] = ce_oi
            put_oi_map[strike]  = pe_oi

            # Collect IV and OI change for ATM ±2% strikes
            if spot > 0 and abs(strike - spot) / spot <= 0.02:
                ce_iv = float(ce.get("impliedVolatility", 0) or 0)
                pe_iv = float(pe.get("impliedVolatility", 0) or 0)
                ce_chg = float(ce.get("changeinOpenInterest", 0) or 0)
                pe_chg = float(pe.get("changeinOpenInterest", 0) or 0)
                if ce_iv > 0:
                    call_iv_atm.append(ce_iv)
                if pe_iv > 0:
                    put_iv_atm.append(pe_iv)
                call_chg_atm.append(ce_chg)
                put_chg_atm.append(pe_chg)

        total_call_oi = sum(call_oi_map.values())
        total_put_oi  = sum(put_oi_map.values())

        # ── Signal 1: PCR (Put-Call Ratio) ──────────────────────────────────
        if total_call_oi > 0:
            pcr = total_put_oi / total_call_oi
            if pcr > 1.5:
                # High PCR = more put buyers = contrarian bullish
                if direction == "LONG":
                    score += 5.0
                    reasons.append(f"PCR={pcr:.2f}↑BULL")
                else:
                    score -= 4.0
                    reasons.append(f"PCR={pcr:.2f}↑AGAINST")
            elif pcr < 0.7:
                # Low PCR = more call buyers = contrarian bearish
                if direction == "SHORT":
                    score += 5.0
                    reasons.append(f"PCR={pcr:.2f}↓BEAR")
                else:
                    score -= 4.0
                    reasons.append(f"PCR={pcr:.2f}↓AGAINST")

        # ── Signal 2: Max Pain ───────────────────────────────────────────────
        strike_list = sorted(call_oi_map.keys())
        if len(strike_list) >= 3:
            pain_map: Dict[float, float] = {}
            for target in strike_list:
                pain = 0.0
                for s, oi in call_oi_map.items():
                    if s < target:
                        pain += oi * (target - s)    # ITM calls
                for s, oi in put_oi_map.items():
                    if s > target:
                        pain += oi * (s - target)    # ITM puts
                pain_map[target] = pain
            max_pain_strike = min(pain_map, key=pain_map.get)

            if max_pain_strike > spot:
                if direction == "LONG":
                    score += 4.0
                    reasons.append(f"MaxPain={max_pain_strike:.0f}>spot MAGNET")
                else:
                    score -= 2.0
                    reasons.append(f"MaxPain={max_pain_strike:.0f}>spot AGAINST")
            elif max_pain_strike < spot:
                if direction == "SHORT":
                    score += 4.0
                    reasons.append(f"MaxPain={max_pain_strike:.0f}<spot MAGNET")
                else:
                    score -= 2.0
                    reasons.append(f"MaxPain={max_pain_strike:.0f}<spot AGAINST")

        # ── Signal 3: OI Walls (call wall = resistance, put wall = support) ─
        strikes_above = {k: v for k, v in call_oi_map.items() if k > spot}
        strikes_below = {k: v for k, v in put_oi_map.items()  if k < spot}

        call_wall: Optional[float] = None
        put_wall:  Optional[float] = None

        if strikes_above:
            call_wall = max(strikes_above, key=strikes_above.get)
        if strikes_below:
            put_wall = max(strikes_below, key=strikes_below.get)

        if call_wall and spot > 0:
            dist_to_call_wall = (call_wall - spot) / spot
            if dist_to_call_wall <= 0.01:
                # Within 1% of call wall = strong resistance
                if direction == "LONG":
                    score -= 6.0
                    reasons.append(f"CallWall={call_wall:.0f} NEAR_RESIST")
                else:
                    score += 3.0
                    reasons.append(f"CallWall={call_wall:.0f} SUPPORT_SHORT")

        if put_wall and spot > 0:
            dist_to_put_wall = (spot - put_wall) / spot
            if dist_to_put_wall <= 0.01:
                # Within 1% of put wall = strong support
                if direction == "SHORT":
                    score -= 6.0
                    reasons.append(f"PutWall={put_wall:.0f} NEAR_SUPPORT")
                else:
                    score += 3.0
                    reasons.append(f"PutWall={put_wall:.0f} SUPPORT_LONG")

        # ── Signal 4: IV Skew (put IV / call IV for ATM strikes) ────────────
        if len(call_iv_atm) >= 2 and len(put_iv_atm) >= 2:
            mean_put_iv  = sum(put_iv_atm)  / len(put_iv_atm)
            mean_call_iv = sum(call_iv_atm) / len(call_iv_atm)
            if mean_call_iv > 0:
                iv_skew = mean_put_iv / mean_call_iv
                if iv_skew > 1.3:
                    if direction == "SHORT":
                        score += 3.0
                        reasons.append(f"IVskew={iv_skew:.2f} PUT_PREM")
                elif iv_skew < 0.9:
                    if direction == "LONG":
                        score += 3.0
                        reasons.append(f"IVskew={iv_skew:.2f} CALL_PREM")

        # ── Signal 5: OI Velocity (changeinOI for ATM ±2 strikes) ───────────
        net_call_chg = sum(call_chg_atm)
        net_put_chg  = sum(put_chg_atm)
        if net_call_chg > abs(net_put_chg) * 0.5 and net_call_chg > 0:
            # Net call OI being added near ATM = resistance building
            score -= 2.0
            reasons.append(f"OI_VEL: call+{net_call_chg:.0f} RESIST")
        elif net_put_chg > abs(net_call_chg) * 0.5 and net_put_chg > 0:
            # Net put OI added near ATM = floor building
            score += 2.0
            reasons.append(f"OI_VEL: put+{net_put_chg:.0f} FLOOR")

        # Clamp to [-15, +15]
        score = max(-15.0, min(15.0, score))
        reason_str = " | ".join(reasons) if reasons else ""
        return (score, reason_str)

    except Exception as e:
        logger.debug(f"get_option_chain_score {symbol}: {e}")
        return (0.0, "")


def get_max_pain(symbol: str) -> Optional[float]:
    """Return max pain strike price. Fail-open returns None."""
    try:
        data = _fetch_chain(symbol)
        if not data:
            return None
        strikes_data = data.get("strikes", [])
        if not strikes_data:
            return None

        call_oi_map: Dict[float, float] = {}
        put_oi_map:  Dict[float, float] = {}
        for entry in strikes_data:
            ce = entry.get("CE", {}) or {}
            pe = entry.get("PE", {}) or {}
            strike = float(entry.get("strikePrice", 0) or 0)
            if strike <= 0:
                continue
            call_oi_map[strike] = float(ce.get("openInterest", 0) or 0)
            put_oi_map[strike]  = float(pe.get("openInterest", 0) or 0)

        strike_list = sorted(call_oi_map.keys())
        if len(strike_list) < 3:
            return None

        pain_map: Dict[float, float] = {}
        for target in strike_list:
            pain = 0.0
            for s, oi in call_oi_map.items():
                if s < target:
                    pain += oi * (target - s)
            for s, oi in put_oi_map.items():
                if s > target:
                    pain += oi * (s - target)
            pain_map[target] = pain

        return float(min(pain_map, key=pain_map.get))
    except Exception as e:
        logger.debug(f"get_max_pain {symbol}: {e}")
        return None


def get_put_call_ratio(symbol: str) -> float:
    """Return raw PCR value. Fail-open returns 0.0."""
    try:
        data = _fetch_chain(symbol)
        if not data:
            return 0.0
        strikes_data = data.get("strikes", [])
        total_call_oi = 0.0
        total_put_oi  = 0.0
        for entry in strikes_data:
            ce = entry.get("CE", {}) or {}
            pe = entry.get("PE", {}) or {}
            total_call_oi += float(ce.get("openInterest", 0) or 0)
            total_put_oi  += float(pe.get("openInterest", 0) or 0)
        if total_call_oi <= 0:
            return 0.0
        return round(total_put_oi / total_call_oi, 4)
    except Exception as e:
        logger.debug(f"get_put_call_ratio {symbol}: {e}")
        return 0.0


def get_call_wall(symbol: str, price: float) -> Optional[float]:
    """Return strongest resistance strike above price. Fail-open returns None."""
    try:
        data = _fetch_chain(symbol)
        if not data:
            return None
        strikes_data = data.get("strikes", [])
        call_oi_above: Dict[float, float] = {}
        for entry in strikes_data:
            ce = entry.get("CE", {}) or {}
            strike = float(entry.get("strikePrice", 0) or 0)
            if strike <= price:
                continue
            oi = float(ce.get("openInterest", 0) or 0)
            call_oi_above[strike] = oi
        if not call_oi_above:
            return None
        return float(max(call_oi_above, key=call_oi_above.get))
    except Exception as e:
        logger.debug(f"get_call_wall {symbol}: {e}")
        return None


def get_put_wall(symbol: str, price: float) -> Optional[float]:
    """Return strongest support strike below price. Fail-open returns None."""
    try:
        data = _fetch_chain(symbol)
        if not data:
            return None
        strikes_data = data.get("strikes", [])
        put_oi_below: Dict[float, float] = {}
        for entry in strikes_data:
            pe = entry.get("PE", {}) or {}
            strike = float(entry.get("strikePrice", 0) or 0)
            if strike >= price:
                continue
            oi = float(pe.get("openInterest", 0) or 0)
            put_oi_below[strike] = oi
        if not put_oi_below:
            return None
        return float(max(put_oi_below, key=put_oi_below.get))
    except Exception as e:
        logger.debug(f"get_put_wall {symbol}: {e}")
        return None


def _bsm_gamma(S: float, K: float, sigma: float, T: float) -> float:
    """Approximate Black-Scholes gamma for a strike."""
    import math
    if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
        nd1 = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
        return nd1 / (S * sigma * math.sqrt(T))
    except Exception:
        return 0.0


def get_unusual_options_activity(symbol: str) -> dict:
    """
    Unusual Options Activity (UOA) detector.
    Compares today's ATM call/put OI build vs 5-day intraday average.
    Returns {"signal": "CALL_ACCUMULATION"/"PUT_ACCUMULATION"/"WASHOUT"/None,
             "score_adj": int, "reason": str}
    Fail-open returns {"signal": None, "score_adj": 0, "reason": ""}.
    """
    _empty = {"signal": None, "score_adj": 0, "reason": ""}
    try:
        data = _fetch_chain(symbol)
        if not data:
            return _empty

        strikes_data = data.get("strikes", [])
        spot_price = data.get("spot", 0.0)
        if not strikes_data or spot_price <= 0:
            return _empty

        total_call_chg = 0.0
        total_put_chg  = 0.0
        total_call_oi  = 0.0
        total_put_oi   = 0.0

        for entry in strikes_data:
            ce = entry.get("CE", {}) or {}
            pe = entry.get("PE", {}) or {}
            strike = float(entry.get("strikePrice", 0) or 0)
            if strike <= 0 or spot_price <= 0:
                continue
            # ATM ±5% window
            if abs(strike - spot_price) / spot_price > 0.05:
                continue
            ce_oi  = float(ce.get("openInterest", 0) or 0)
            pe_oi  = float(pe.get("openInterest", 0) or 0)
            ce_chg = float(ce.get("changeinOpenInterest", 0) or 0)
            pe_chg = float(pe.get("changeinOpenInterest", 0) or 0)
            total_call_oi  += ce_oi
            total_put_oi   += pe_oi
            total_call_chg += ce_chg
            total_put_chg  += pe_chg

        # No historical average available intraday — use ratio of change-to-existing OI
        # Unusual = today's change > 25% of existing OI (proxy for 3× avg daily build)
        uoa_call = total_call_oi > 0 and (total_call_chg / total_call_oi) > 0.25
        uoa_put  = total_put_oi  > 0 and (total_put_chg  / total_put_oi)  > 0.25
        washout  = (
            total_call_oi > 0 and total_put_oi > 0
            and (total_call_chg / total_call_oi) < -0.20
            and (total_put_chg  / total_put_oi)  < -0.20
        )

        if washout:
            return {
                "signal": "WASHOUT",
                "score_adj": -8,
                "reason": f"UOA_WASHOUT call_chg={total_call_chg:.0f} put_chg={total_put_chg:.0f}",
            }
        if uoa_call and not uoa_put:
            return {
                "signal": "CALL_ACCUMULATION",
                "score_adj": 10,
                "reason": f"UOA_CALL call_chg={total_call_chg:.0f} ({total_call_chg/total_call_oi*100:.0f}%)",
            }
        if uoa_put and not uoa_call:
            return {
                "signal": "PUT_ACCUMULATION",
                "score_adj": 10,
                "reason": f"UOA_PUT put_chg={total_put_chg:.0f} ({total_put_chg/total_put_oi*100:.0f}%)",
            }
        return _empty

    except Exception as e:
        logger.debug(f"get_unusual_options_activity {symbol}: {e}")
        return _empty


def get_gex(symbol: str, spot: float = 0.0) -> dict:
    """
    Gamma Exposure = Σ(call_gamma × call_OI - put_gamma × put_OI).
    Positive GEX → market makers stabilise price (bad for breakouts).
    Negative GEX → market makers amplify moves (good for breakouts).
    Returns: {"gex": float, "regime": str, "breakout_adj": int}
    Fail-open returns zeros.
    """
    try:
        data = _fetch_chain(symbol)
        if not data:
            return {"gex": 0.0, "regime": "UNKNOWN", "breakout_adj": 0}

        strikes_data = data.get("strikes", [])
        s = spot or data.get("spot", 0.0)
        if not strikes_data or s <= 0:
            return {"gex": 0.0, "regime": "UNKNOWN", "breakout_adj": 0}

        # Days to next weekly expiry (approximate — assume Thursday, max 7 days)
        import datetime as _dt
        today = _dt.date.today()
        days_to_expiry = max(1, (3 - today.weekday()) % 7 + 1)  # next Thursday
        T = days_to_expiry / 252.0

        total_gex = 0.0
        for entry in strikes_data:
            ce = entry.get("CE", {}) or {}
            pe = entry.get("PE", {}) or {}
            K  = float(entry.get("strikePrice", 0) or 0)
            if K <= 0:
                continue

            ce_iv = float(ce.get("impliedVolatility", 0) or 0) / 100.0 or 0.2
            pe_iv = float(pe.get("impliedVolatility", 0) or 0) / 100.0 or 0.2
            ce_oi = float(ce.get("openInterest", 0) or 0)
            pe_oi = float(pe.get("openInterest", 0) or 0)

            call_g = _bsm_gamma(s, K, ce_iv, T)
            put_g  = _bsm_gamma(s, K, pe_iv, T)
            total_gex += (call_g * ce_oi - put_g * pe_oi)

        if total_gex > 0:
            regime = "POSITIVE_GEX"
            breakout_adj = -4  # MMs stabilise → bad for breakouts
        elif total_gex < 0:
            regime = "NEGATIVE_GEX"
            breakout_adj = 6   # MMs amplify → good for breakouts
        else:
            regime = "NEUTRAL_GEX"
            breakout_adj = 0

        return {"gex": round(total_gex, 2), "regime": regime, "breakout_adj": breakout_adj}

    except Exception as e:
        logger.debug(f"get_gex {symbol}: {e}")
        return {"gex": 0.0, "regime": "UNKNOWN", "breakout_adj": 0}


def get_change_in_oi_pcr(symbol: str) -> Tuple[float, str]:
    """
    Change-in-OI PCR: ratio of net NEW put OI added vs net NEW call OI added.
    More sensitive than total-OI PCR for intraday positioning shifts.

    Research: Change-in-OI PCR > 1.2 and rising = institutional bullish (more puts
    being WRITTEN = hedging = confidence). < 0.8 and falling = bearish shift.
    Returns (pcr_value, regime_str). Fail-open: (1.0, "NEUTRAL").
    """
    try:
        data = _fetch_chain(symbol)
        if not data:
            return (1.0, "NEUTRAL")
        strikes = data.get("strikes", [])
        if not strikes:
            return (1.0, "NEUTRAL")

        total_call_chg = 0.0
        total_put_chg  = 0.0
        for entry in strikes:
            ce = entry.get("CE", {}) or {}
            pe = entry.get("PE", {}) or {}
            ce_chg = float(ce.get("changeinOpenInterest", 0) or 0)
            pe_chg = float(pe.get("changeinOpenInterest", 0) or 0)
            # Only count POSITIVE changes (new positions being added)
            if ce_chg > 0:
                total_call_chg += ce_chg
            if pe_chg > 0:
                total_put_chg += pe_chg

        if total_call_chg <= 0:
            return (1.0, "NEUTRAL")

        pcr = total_put_chg / total_call_chg

        if pcr >= 1.3:
            regime = "BULLISH_BIAS"   # more puts being written = institutional hedging = bullish
        elif pcr <= 0.7:
            regime = "BEARISH_BIAS"   # more calls being written = calls bought = bearish
        elif pcr >= 1.1:
            regime = "MILD_BULLISH"
        elif pcr <= 0.9:
            regime = "MILD_BEARISH"
        else:
            regime = "NEUTRAL"

        return (round(pcr, 3), regime)

    except Exception as e:
        logger.debug(f"get_change_in_oi_pcr {symbol}: {e}")
        return (1.0, "NEUTRAL")


def get_chng_pcr_score(symbol: str, direction: str) -> Tuple[float, str]:
    """
    Score signal from change-in-OI PCR. Score: -8 to +8. Fail-open: (0, "").
    """
    try:
        pcr, regime = get_change_in_oi_pcr(symbol)
        if regime == "NEUTRAL":
            return (0.0, "")

        score = 0.0
        if direction == "LONG":
            if regime == "BULLISH_BIAS":
                score = 8.0
            elif regime == "MILD_BULLISH":
                score = 4.0
            elif regime == "BEARISH_BIAS":
                score = -7.0
            elif regime == "MILD_BEARISH":
                score = -3.0
        else:  # SHORT
            if regime == "BEARISH_BIAS":
                score = 8.0
            elif regime == "MILD_BEARISH":
                score = 4.0
            elif regime == "BULLISH_BIAS":
                score = -7.0
            elif regime == "MILD_BULLISH":
                score = -3.0

        if score == 0.0:
            return (0.0, "")
        return (score, f"CHNG_PCR={pcr:.2f}_{regime}:{score:+.0f}")

    except Exception as e:
        logger.debug(f"get_chng_pcr_score {symbol}: {e}")
        return (0.0, "")
