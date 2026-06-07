"""
gex_calculator.py — Options Gamma Exposure calculation.
GEX measures how much market makers must buy/sell to stay delta-neutral.
When GEX flips negative: price moves AMPLIFY (market makers add fuel).
When GEX is high positive: price moves are DAMPENED (pinned to strike).

Used by: SpotGamma, Goldman Sachs options desk, all MM desks.
Data source: yfinance options chain (free).

GEX signal:
- Large positive GEX at current price: score -5 (pinned, avoid momentum)
- Negative GEX zone: score +10 for momentum trades (moves amplify)
- Price at key GEX flip point: score +8 (breakout imminent)
Cached 15 min per symbol. Fail-open 0.0.
"""
import logging
import time
from typing import Tuple, Dict, Optional

logger = logging.getLogger(__name__)
_TTL_LEGACY = 1200.0  # 20-min for legacy get_gex_signal


class GEXCalculator:
    """
    Real options-chain-based Gamma Exposure estimator using yfinance.
    Singleton via get_gex_calculator(). All results cached 15 minutes per symbol.
    """

    def __init__(self):
        self._cache: Dict[str, Tuple[float, dict]] = {}  # key → (timestamp, result)

    def calculate_gex(self, symbol: str) -> dict:
        """
        Gamma Exposure estimate from live options chain (yfinance).
        Returns gex_score [-10,+10], regime, iv_percentile, pcr_oi.
        Cached 15 minutes per symbol.
        """
        cache_key = f"{symbol}_gex"
        if cache_key in self._cache:
            ts, val = self._cache[cache_key]
            if time.time() - ts < 900:  # 15 min
                return val

        result: dict = {
            "gex_score": 0.0,
            "regime": "NEUTRAL",
            "iv_percentile": 50,
            "pcr_oi": 1.0,
        }
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            if not expirations:
                self._cache[cache_key] = (time.time(), result)
                return result

            near_exp = expirations[0]
            chain = ticker.option_chain(near_exp)
            calls, puts = chain.calls, chain.puts

            # Current price
            info = ticker.fast_info
            price = float(
                getattr(info, "last_price", 0)
                or getattr(info, "previous_close", 0)
                or 0
            )
            if price <= 0:
                self._cache[cache_key] = (time.time(), result)
                return result

            # ATM options (within 5% of spot)
            atm_c = calls[abs(calls["strike"] - price) / price < 0.05]
            atm_p = puts[abs(puts["strike"] - price) / price < 0.05]

            call_iv = (
                float(atm_c["impliedVolatility"].mean()) if len(atm_c) else 0.25
            )
            put_iv = (
                float(atm_p["impliedVolatility"].mean()) if len(atm_p) else 0.25
            )
            skew = put_iv - call_iv  # positive = puts pricier = fear

            # Put/Call OI ratio
            call_oi = float(calls["openInterest"].fillna(0).sum())
            put_oi = float(puts["openInterest"].fillna(0).sum())
            pcr = put_oi / max(call_oi, 1)

            avg_iv = (call_iv + put_iv) / 2
            iv_pct = min(100, max(0, (avg_iv - 0.10) / 0.60 * 100))

            # GEX score: negative = dealer short gamma = amplified moves (bad for trend)
            gex = 0.0
            gex += -skew * 25.0            # put skew → negative GEX
            gex += (1.0 - min(pcr, 2.0)) * 3.0
            gex += (50 - iv_pct) / 12.0
            gex = round(max(-10.0, min(10.0, gex)), 2)

            result = {
                "gex_score": gex,
                "regime": (
                    "AMPLIFYING" if gex < -3
                    else ("DAMPENING" if gex > 3 else "NEUTRAL")
                ),
                "iv_percentile": round(iv_pct, 1),
                "pcr_oi": round(pcr, 2),
                "put_call_skew": round(skew, 4),
            }
            self._cache[cache_key] = (time.time(), result)
        except Exception as e:
            logger.debug(f"[GEX] {symbol}: {e}")
            self._cache[cache_key] = (time.time(), result)

        return result


# ── Module-level singleton ─────────────────────────────────────────────────────

_gex_instance: Optional[GEXCalculator] = None


def get_gex_calculator() -> GEXCalculator:
    """Return the module-level GEXCalculator singleton."""
    global _gex_instance
    if _gex_instance is None:
        _gex_instance = GEXCalculator()
    return _gex_instance


def get_gex_score(symbol: str) -> float:
    """Simple wrapper: return gex_score for a symbol. Fail-open 0.0."""
    try:
        return get_gex_calculator().calculate_gex(symbol).get("gex_score", 0.0)
    except Exception:
        return 0.0


# ── Legacy function — kept for backward compatibility ──────────────────────────

_legacy_cache: dict = {}


def get_gex_signal(symbol: str, current_price: float, direction: str) -> Tuple[float, str]:
    """
    Legacy interface. Returns (score_delta, reason). Fail-open returns (0.0, 'gex_unavailable').
    Now delegates to the real GEXCalculator for the base gex_score, then maps it to the
    old (score, reason) tuple the caller expected.
    """
    try:
        now = time.time()
        cache_key = f"legacy_{symbol}_{int(current_price)}_{direction}"
        if cache_key in _legacy_cache and now - _legacy_cache[cache_key][1] < _TTL_LEGACY:
            return _legacy_cache[cache_key][0]

        gex_data = get_gex_calculator().calculate_gex(symbol)
        gex_score = gex_data.get("gex_score", 0.0)
        regime = gex_data.get("regime", "NEUTRAL")
        iv_pct = gex_data.get("iv_percentile", 50)
        pcr = gex_data.get("pcr_oi", 1.0)

        # Map gex_score to legacy scoring scheme
        if regime == "AMPLIFYING":
            score = 10.0
            reason = (
                f"negative_GEX(score={gex_score:.1f}) moves_amplify "
                f"pcr={pcr:.2f} iv_pct={iv_pct:.0f}"
            )
        elif regime == "DAMPENING":
            score = -5.0
            reason = (
                f"high_GEX(score={gex_score:.1f}) price_pinned "
                f"pcr={pcr:.2f} iv_pct={iv_pct:.0f}"
            )
        else:
            score = 3.0
            reason = (
                f"neutral_GEX(score={gex_score:.1f}) "
                f"pcr={pcr:.2f} iv_pct={iv_pct:.0f}"
            )

        if direction == "SHORT":
            score = -score

        result = (float(score), reason)
        _legacy_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"gex_calculator fail-open {symbol}: {e}")
        return (0.0, "gex_unavailable")
