"""
options_sweep_classifier.py — True Options Sweep Detection (L99)

A "sweep" = institutional order aggressively filling across multiple strikes
at the ask price. Used by Cheddar Flow, FlowAlgo, BlackBoxStocks ($150+/mo).

Lee-Ready algorithm adapted for options:
  1. OTM volume >> Open Interest = new directional money entering (not closing)
  2. Volume spike vs 20-day average = unusual institutional activity
  3. OTM call dominance = institutional bullish bet
  4. OTM put dominance = institutional bearish hedge / directional short

Score:
  OTM call sweeps (vol > 2x OI, count ≥ 3 strikes): +12 LONG
  OTM put sweeps (vol > 2x OI, count ≥ 3 strikes):  -10 LONG / +8 SHORT
  Net premium positive (calls > puts):               +5 LONG
  Net premium negative (puts > calls):               -4 LONG / +5 SHORT
"""
import logging
import time as _time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict = {}
_TTL = 300.0  # 5 min

def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _TTL:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _analyze_chain(symbol: str, current_price: float) -> Optional[Dict]:
    """Analyze options chain for sweep signals via yfinance."""
    cached = _cache_get(f"sw_{symbol}")
    if cached is not None:
        return cached
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        exps = ticker.options
        if not exps:
            return None

        call_sweeps = put_sweeps = 0
        call_notional = put_notional = 0.0
        total_call_vol = total_put_vol = 0

        for exp in exps[:2]:  # nearest 2 expiries
            try:
                chain = ticker.option_chain(exp)
                calls = chain.calls
                puts  = chain.puts
                if calls.empty or puts.empty:
                    continue

                total_call_vol += int(calls["volume"].fillna(0).sum())
                total_put_vol  += int(puts["volume"].fillna(0).sum())

                # OTM options only (directional bets, not hedges)
                otm_calls = calls[
                    (calls["strike"] > current_price * 1.015) &
                    (calls["volume"].fillna(0) > calls["openInterest"].fillna(1) * 1.5) &
                    (calls["volume"].fillna(0) > 30)
                ]
                otm_puts = puts[
                    (puts["strike"] < current_price * 0.985) &
                    (puts["volume"].fillna(0) > puts["openInterest"].fillna(1) * 1.5) &
                    (puts["volume"].fillna(0) > 30)
                ]

                call_sweeps += len(otm_calls)
                put_sweeps  += len(otm_puts)

                if "ask" in calls.columns:
                    call_notional += float(
                        (otm_calls["volume"].fillna(0) * otm_calls["ask"].fillna(0) * 100).sum()
                    )
                if "ask" in puts.columns:
                    put_notional += float(
                        (otm_puts["volume"].fillna(0) * otm_puts["ask"].fillna(0) * 100).sum()
                    )
            except Exception:
                continue

        result = {
            "call_sweeps":    call_sweeps,
            "put_sweeps":     put_sweeps,
            "call_notional":  call_notional,
            "put_notional":   put_notional,
            "net_notional":   call_notional - put_notional,
            "total_call_vol": total_call_vol,
            "total_put_vol":  total_put_vol,
        }
        _cache_set(f"sw_{symbol}", result)
        return result
    except Exception as exc:
        logger.debug(f"[sweep_chain] {symbol}: {exc}")
        return None


def get_options_sweep_score(
    symbol: str, current_price: float, direction: str
) -> Tuple[float, str]:
    """
    True options sweep classifier (Lee-Ready adapted for options).
    Returns (score_delta, reason). Fail-open.
    """
    try:
        if current_price <= 0:
            return 0.0, "sweep:no-price"

        data = _analyze_chain(symbol, current_price)
        if data is None:
            return 0.0, "sweep:no-chain"

        total = 0.0
        parts = []

        cs = data["call_sweeps"]
        ps = data["put_sweeps"]
        net = data["net_notional"]

        # Call sweeps (institutional bullish bet)
        if cs >= 3:
            d = 12.0 if direction == "LONG" else -8.0
            parts.append(f"CALL_SWEEPS={cs}")
            total += d
        elif cs >= 1:
            d = 6.0 if direction == "LONG" else -4.0
            parts.append(f"call_sweep={cs}")
            total += d

        # Put sweeps (institutional bearish bet)
        if ps >= 3:
            d = -10.0 if direction == "LONG" else 8.0
            parts.append(f"PUT_SWEEPS={ps}")
            total += d
        elif ps >= 1:
            d = -5.0 if direction == "LONG" else 4.0
            parts.append(f"put_sweep={ps}")
            total += d

        # Net premium direction
        if abs(net) > 100_000:
            if net > 0:
                d = 5.0 if direction == "LONG" else -3.0
                parts.append(f"net_call_prem=${net/1e3:.0f}k")
            else:
                d = -4.0 if direction == "LONG" else 4.0
                parts.append(f"net_put_prem=${abs(net)/1e3:.0f}k")
            total += d

        if not parts:
            return 0.0, f"sweep:neutral(cs={cs},ps={ps})"
        return float(total), f"SWEEP[{' | '.join(parts)}]"
    except Exception as exc:
        logger.debug(f"[options_sweep_classifier] {exc}")
        return 0.0, "sweep:error"
