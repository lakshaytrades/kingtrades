"""
options_flow.py — Options Chain Intelligence Engine

THE single largest institutional signal available to retail traders.
Institutions position in options BEFORE moving stock.
A 10,000 call sweep at the ask = someone knows something.

Data: yfinance options chain (free, no API key)
Cache: 30 minutes per symbol (options data moves slowly intraday)
Score range: -12 to +15
"""
import logging
import time as _time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)
_opts_cache: Dict = {}
_OPTS_TTL = 1800.0


def get_options_signal(symbol: str, current_price: float, direction: str) -> Tuple[float, str]:
    """Detect unusual options activity. Returns (score_delta, reason). Fail-open."""
    now = _time.monotonic()
    cached = _opts_cache.get(symbol)
    if cached and now - cached["ts"] < _OPTS_TTL:
        return _score_options(cached["data"], direction)

    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        expiry_dates = tk.options
        if not expiry_dates:
            return 0.0, ""

        total_call_vol = total_put_vol = 0
        unusual_calls = unusual_puts = 0

        for expiry in expiry_dates[:2]:   # only near-term (0-30 DTE)
            try:
                chain = tk.option_chain(expiry)
                calls, puts = chain.calls, chain.puts

                # Near-the-money: within 5% of current price
                if current_price > 0:
                    lo, hi = current_price * 0.95, current_price * 1.05
                    calls = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)]
                    puts  = puts[(puts["strike"]  >= lo) & (puts["strike"]  <= hi)]

                c_vol = int(calls["volume"].fillna(0).sum())
                p_vol = int(puts["volume"].fillna(0).sum())
                total_call_vol += c_vol
                total_put_vol  += p_vol

                # Unusual = volume > 5x open interest (fresh institutional sweep, not hedging)
                for _, r in calls.iterrows():
                    v, oi = (r.get("volume") or 0), max(r.get("openInterest") or 1, 1)
                    if v >= 100 and v > oi * 5:
                        unusual_calls += 1
                for _, r in puts.iterrows():
                    v, oi = (r.get("volume") or 0), max(r.get("openInterest") or 1, 1)
                    if v >= 100 and v > oi * 5:
                        unusual_puts += 1
            except Exception:
                continue

        data = {
            "pc_ratio":      total_put_vol / max(total_call_vol, 1),
            "unusual_calls": unusual_calls,
            "unusual_puts":  unusual_puts,
            "call_vol":      total_call_vol,
            "put_vol":       total_put_vol,
        }
        _opts_cache[symbol] = {"data": data, "ts": now}
        return _score_options(data, direction)

    except Exception as e:
        logger.debug(f"options_flow({symbol}): {e}")
        return 0.0, ""


def _score_options(data: Dict, direction: str) -> Tuple[float, str]:
    pc      = data.get("pc_ratio", 1.0)
    ucalls  = data.get("unusual_calls", 0)
    uputs   = data.get("unusual_puts",  0)
    score, reasons = 0.0, []

    if direction in ("LONG", "BUY"):
        if pc < 0.5:    score += 8;  reasons.append(f"CALL_SWEEP pc={pc:.2f}(+8)")
        elif pc < 0.7:  score += 4;  reasons.append(f"CALL_BIAS pc={pc:.2f}(+4)")
        elif pc > 1.8:  score -= 10; reasons.append(f"PUT_SWEEP pc={pc:.2f}(-10)")
        elif pc > 1.3:  score -= 5;  reasons.append(f"PUT_BIAS pc={pc:.2f}(-5)")
        if ucalls:
            s = min(7, ucalls * 3); score += s
            reasons.append(f"UOA_CALLS({ucalls}sweeps+{s})")
    else:
        if pc > 1.8:    score += 8;  reasons.append(f"PUT_SWEEP pc={pc:.2f}(+8)")
        elif pc > 1.3:  score += 4;  reasons.append(f"PUT_BIAS pc={pc:.2f}(+4)")
        elif pc < 0.5:  score -= 10; reasons.append(f"CALL_SWEEP_FIGHTS_SHORT(-10)")
        if uputs:
            s = min(7, uputs * 3); score += s
            reasons.append(f"UOA_PUTS({uputs}sweeps+{s})")

    return round(score, 1), " | ".join(reasons)


def get_market_pc_ratio() -> float:
    """SPY put/call ratio as market-wide fear gauge. Cached 30 min."""
    try:
        now = _time.monotonic()
        cached = _opts_cache.get("__SPY_PC__")
        if cached and now - cached["ts"] < _OPTS_TTL:
            return cached["data"].get("pc_ratio", 1.0)
        import yfinance as yf
        spy = yf.Ticker("SPY")
        if not spy.options:
            return 1.0
        chain = spy.option_chain(spy.options[0])
        c_vol = int(chain.calls["volume"].fillna(0).sum())
        p_vol = int(chain.puts["volume"].fillna(0).sum())
        pc = round(p_vol / max(c_vol, 1), 3)
        _opts_cache["__SPY_PC__"] = {"data": {"pc_ratio": pc}, "ts": now}
        return pc
    except Exception:
        return 1.0
