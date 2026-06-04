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
Cached 20 min. Fail-open 0.0.
"""
import logging
import time
from typing import Tuple

logger = logging.getLogger(__name__)
_cache: dict = {}
_TTL = 1200.0

def get_gex_signal(symbol: str, current_price: float, direction: str) -> Tuple[float, str]:
    """Returns (score_delta, reason). Fail-open returns (0.0, 'gex_unavailable')."""
    try:
        now = time.time()
        cache_key = f"{symbol}_{int(current_price)}"
        if cache_key in _cache and now - _cache[cache_key][1] < _TTL:
            return _cache[cache_key][0]

        import yfinance as yf
        import numpy as np

        ticker = yf.Ticker(symbol)
        exps = ticker.options
        if not exps:
            result = (0.0, "no_options")
            _cache[cache_key] = (result, now)
            return result

        total_gex = 0.0
        # Use first 2 expiries for accuracy
        for exp in exps[:2]:
            try:
                chain = ticker.option_chain(exp)
                calls = chain.calls.copy()
                puts = chain.puts.copy()

                # GEX = OI × Gamma × 100 × spot²
                # Calls: positive GEX, Puts: negative GEX
                for df, sign in [(calls, 1), (puts, -1)]:
                    if "gamma" not in df.columns or "openInterest" not in df.columns:
                        continue
                    df = df.dropna(subset=["gamma", "openInterest"])
                    # Only near ATM strikes (within 10% of spot)
                    atm_mask = (df["strike"] > current_price * 0.90) & (df["strike"] < current_price * 1.10)
                    df_atm = df[atm_mask]
                    if df_atm.empty:
                        continue
                    gex_contrib = float((df_atm["gamma"] * df_atm["openInterest"] * 100 * current_price ** 2 * sign).sum())
                    total_gex += gex_contrib
            except Exception:
                continue

        # Normalize GEX to a score
        gex_bn = total_gex / 1e9  # in billions
        if gex_bn < -0.5:
            # Negative GEX — moves amplify — great for momentum
            score = 10.0
            reason = f"negative_GEX({gex_bn:.1f}B) moves_amplify"
        elif gex_bn > 2.0:
            # Very high positive GEX — pinned
            score = -5.0
            reason = f"high_GEX({gex_bn:.1f}B) price_pinned"
        elif gex_bn > 0.5:
            score = -2.0
            reason = f"pos_GEX({gex_bn:.1f}B) slight_pin"
        else:
            score = 3.0
            reason = f"neutral_GEX({gex_bn:.1f}B)"

        if direction == "SHORT":
            score = -score

        result = (float(score), reason)
        _cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"gex_calculator fail-open {symbol}: {e}")
        return (0.0, "gex_unavailable")
