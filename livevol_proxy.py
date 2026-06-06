"""
livevol_proxy.py — Free CBOE LiveVol Options Flow Proxy (L99)

CBOE LiveVol ($5,000+/month) provides real-time classified options prints:
  SWEEP = order filling across multiple exchanges at ask (institutional aggression)
  BLOCK = single large order
  Aggressor: BUY-at-ask (bullish) vs SELL-at-bid (closing/bearish)

Free reconstruction algorithm (Lee-Ready adapted for options):
  1. OTM call/put volume vs open interest ratio (new money entering)
  2. Call/put notional premium comparison (net bullish/bearish $)
  3. Volume-to-OI surge detection (institutional sweep signature)
  4. Power hour amplification (3:00–3:30 PM ET = institutional rebalancing)

Score:
  OTM call sweep (vol>2x OI, 3+ strikes): +12 LONG
  OTM put sweep (vol>2x OI, 3+ strikes):  -10 LONG / +8 SHORT
  Net call premium dominance:              +5 LONG
  Net put premium dominance:               -4 LONG / +5 SHORT
  Power hour: all signals × 1.3x
"""
import logging
import time as _time
from datetime import datetime
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
_CACHE: Dict = {}
_TTL = 300.0  # 5 min

def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _TTL:
        return entry[1]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = (_time.time(), val)


def _is_power_hour() -> bool:
    try:
        now = datetime.now(ET)
        return now.hour == 15 and now.minute < 30
    except Exception:
        return False


def _analyze_flow(symbol: str, current_price: float) -> Optional[Dict]:
    """Reconstruct LiveVol-style flow from yfinance options chain."""
    cached = _cache_get(f"lv_{symbol}")
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
        total_call_oi  = total_put_oi  = 0

        for exp in exps[:2]:
            try:
                chain = ticker.option_chain(exp)
                calls = chain.calls
                puts  = chain.puts
                if calls.empty or puts.empty:
                    continue

                c_vol = calls["volume"].fillna(0)
                c_oi  = calls["openInterest"].fillna(1).clip(lower=1)
                p_vol = puts["volume"].fillna(0)
                p_oi  = puts["openInterest"].fillna(1).clip(lower=1)

                total_call_vol += int(c_vol.sum())
                total_put_vol  += int(p_vol.sum())
                total_call_oi  += int(c_oi.sum())
                total_put_oi   += int(p_oi.sum())

                # OTM sweeps: volume > 2× OI AND strike directionally OTM
                otm_c = calls[
                    (calls["strike"] > current_price * 1.015) &
                    (c_vol > c_oi * 1.8) & (c_vol > 25)
                ]
                otm_p = puts[
                    (puts["strike"] < current_price * 0.985) &
                    (p_vol > p_oi * 1.8) & (p_vol > 25)
                ]
                call_sweeps += len(otm_c)
                put_sweeps  += len(otm_p)

                ask_c = calls.get("ask", calls.get("lastPrice"))
                ask_p = puts.get("ask",  puts.get("lastPrice"))
                if ask_c is not None:
                    call_notional += float((otm_c["volume"].fillna(0) * otm_c[ask_c.name if hasattr(ask_c, 'name') else "ask"].fillna(0) * 100).sum())
                if ask_p is not None:
                    put_notional += float((otm_p["volume"].fillna(0) * otm_p[ask_p.name if hasattr(ask_p, 'name') else "ask"].fillna(0) * 100).sum())

            except Exception:
                continue

        result = {
            "call_sweeps":  call_sweeps,
            "put_sweeps":   put_sweeps,
            "call_notional": call_notional,
            "put_notional":  put_notional,
            "net_notional":  call_notional - put_notional,
            "total_vol":     total_call_vol + total_put_vol,
            "pc_ratio":      total_put_vol / max(total_call_vol, 1),
        }
        _cache_set(f"lv_{symbol}", result)
        return result
    except Exception as exc:
        logger.debug(f"[livevol_chain] {symbol}: {exc}")
        return None


def get_livevol_score(
    symbol: str, current_price: float, direction: str
) -> Tuple[float, str]:
    """
    CBOE LiveVol-equivalent options flow score.
    Returns (score_delta, reason). Fail-open.
    """
    try:
        if current_price <= 0:
            return 0.0, "livevol:no-price"

        data = _analyze_flow(symbol, current_price)
        if data is None:
            return 0.0, "livevol:no-chain"

        total = 0.0
        parts = []

        cs  = data["call_sweeps"]
        ps  = data["put_sweeps"]
        net = data["net_notional"]

        if cs >= 3:
            d = 12.0 if direction == "LONG" else -8.0
            parts.append(f"CALL_SWEEPS={cs}")
            total += d
        elif cs >= 1:
            d = 6.0 if direction == "LONG" else -4.0
            parts.append(f"call_sweep={cs}")
            total += d

        if ps >= 3:
            d = -10.0 if direction == "LONG" else 8.0
            parts.append(f"PUT_SWEEPS={ps}")
            total += d
        elif ps >= 1:
            d = -5.0 if direction == "LONG" else 4.0
            parts.append(f"put_sweep={ps}")
            total += d

        if abs(net) > 80_000:
            if net > 0:
                d = 5.0 if direction == "LONG" else -3.0
                parts.append(f"net_call=${net/1e3:.0f}k")
            else:
                d = -4.0 if direction == "LONG" else 4.0
                parts.append(f"net_put=${abs(net)/1e3:.0f}k")
            total += d

        if not parts:
            return 0.0, f"livevol:neutral(cs={cs},ps={ps},pc={data['pc_ratio']:.2f})"

        if _is_power_hour():
            total *= 1.3
            parts.append("PWR_HR×1.3")

        return float(total), f"LIVEVOL[{' | '.join(parts)}]"
    except Exception as exc:
        logger.debug(f"[livevol_proxy] {exc}")
        return 0.0, "livevol:error"
