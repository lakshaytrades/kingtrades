"""
chandelier_exit_india.py — VIX-adaptive Chandelier Exit trailing stop.
LONG: trail = highest_close_since_entry − mult×ATR
SHORT: trail = lowest_close_since_entry + mult×ATR
Multiplier: VIX<15→2.0, VIX 15-22→3.0, VIX>22→4.0
Tightens to 2×ATR when price > T1. Never widens (ratchet only).
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("chandelier_india")

_VIX_TIGHT   = 15.0
_VIX_LOOSE   = 22.0
_MULT_TIGHT  = 2.0
_MULT_NORMAL = 3.0
_MULT_LOOSE  = 4.0
_MULT_PROFIT = 2.0   # tighter once above T1


@dataclass
class ChandelierState:
    symbol: str
    direction: str
    entry: float
    atr: float
    target_1: float
    current_stop: float
    extreme_price: float   # highest close (LONG) or lowest close (SHORT)
    t1_cleared: bool = False


class ChandelierExit:
    """
    Maintains per-symbol chandelier trailing stops.
    Call update(symbol, close, atr, vix) every 5-min bar.
    Returns updated stop level.
    """

    def __init__(self):
        self._states: dict[str, ChandelierState] = {}

    # ------------------------------------------------------------------ #

    def register(
        self,
        symbol: str,
        direction: str,
        entry: float,
        atr: float,
        initial_stop: float,
        target_1: float,
    ) -> None:
        self._states[symbol] = ChandelierState(
            symbol=symbol,
            direction=direction,
            entry=entry,
            atr=atr,
            target_1=target_1,
            current_stop=initial_stop,
            extreme_price=entry,
        )

    def remove(self, symbol: str) -> None:
        self._states.pop(symbol, None)

    def update(
        self, symbol: str, close: float, atr: float, vix: float = 18.0
    ) -> Optional[float]:
        """
        Returns new stop level (ratcheted — never widens).
        Returns None if symbol not tracked.
        """
        s = self._states.get(symbol)
        if s is None:
            return None

        try:
            s.atr = atr
            mult = _vix_multiplier(vix)

            # Update extreme
            if s.direction == "LONG":
                s.extreme_price = max(s.extreme_price, close)
                if close >= s.target_1:
                    s.t1_cleared = True
            else:
                s.extreme_price = min(s.extreme_price, close)
                if close <= s.target_1:
                    s.t1_cleared = True

            if s.t1_cleared:
                mult = min(mult, _MULT_PROFIT)

            new_stop = _compute_stop(s.direction, s.extreme_price, atr, mult)

            # Ratchet
            if s.direction == "LONG":
                s.current_stop = max(s.current_stop, new_stop)
            else:
                s.current_stop = min(s.current_stop, new_stop)

            return s.current_stop

        except Exception as e:
            logger.debug("ChandelierExit.update %s: %s", symbol, e)
            return s.current_stop if s else None

    def get_stop(self, symbol: str) -> Optional[float]:
        s = self._states.get(symbol)
        return s.current_stop if s else None


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _vix_multiplier(vix: float) -> float:
    if vix < _VIX_TIGHT:
        return _MULT_TIGHT
    if vix <= _VIX_LOOSE:
        return _MULT_NORMAL
    return _MULT_LOOSE


def _compute_stop(direction: str, extreme: float, atr: float, mult: float) -> float:
    if direction == "LONG":
        return extreme - mult * atr
    return extreme + mult * atr
