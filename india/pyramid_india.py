"""
pyramid_india.py — Pyramid into winners after T1 hit.
After T1: add 50% of original qty if price pulls back to BE + 0.3×ATR.
Max 1 pyramid per position. Only A+ / A grade signals.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("pyramid_india")


@dataclass
class PyramidState:
    symbol: str
    direction: str          # "LONG" / "SHORT"
    original_qty: int
    original_entry: float
    original_sl: float
    target_1: float
    target_2: float
    atr: float
    grade: str              # "A+" / "A"
    t1_hit: bool = False
    pyramid_triggered: bool = False
    pyramid_qty: int = 0
    pyramid_entry: float = 0.0
    pyramid_sl: float = 0.0
    pyramid_tp: float = 0.0


class PyramidManager:
    """
    Tracks pyramid eligibility per symbol.
    Call on_price_update() each bar — returns pyramid order dict when triggered.
    """

    def __init__(self):
        self._states: dict[str, PyramidState] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def register_position(
        self,
        symbol: str,
        direction: str,
        qty: int,
        entry: float,
        sl: float,
        target_1: float,
        target_2: float,
        atr: float,
        grade: str,
    ) -> None:
        if grade not in ("A+", "A"):
            return  # only pyramid on best signals
        self._states[symbol] = PyramidState(
            symbol=symbol,
            direction=direction,
            original_qty=qty,
            original_entry=entry,
            original_sl=sl,
            target_1=target_1,
            target_2=target_2,
            atr=atr,
            grade=grade,
        )

    def remove_position(self, symbol: str) -> None:
        self._states.pop(symbol, None)

    def on_price_update(self, symbol: str, current_price: float) -> Optional[dict]:
        """
        Returns pyramid order dict if trigger fires, else None.
        Dict: {"symbol", "direction", "qty", "entry_hint", "sl", "tp", "reason"}
        """
        s = self._states.get(symbol)
        if s is None or s.pyramid_triggered:
            return None

        try:
            if not s.t1_hit:
                s.t1_hit = _t1_reached(s, current_price)
                if not s.t1_hit:
                    return None

            trigger_price = _pyramid_trigger_price(s)
            if _price_at_trigger(s, current_price, trigger_price):
                s.pyramid_triggered = True
                pyr_qty = max(1, int(s.original_qty * 0.5))
                s.pyramid_qty = pyr_qty
                s.pyramid_entry = current_price
                s.pyramid_sl = s.original_sl
                s.pyramid_tp = s.target_2
                logger.info(
                    "Pyramid triggered %s %s qty=%d @ %.2f SL=%.2f TP=%.2f",
                    symbol, s.direction, pyr_qty, current_price, s.pyramid_sl, s.pyramid_tp,
                )
                return {
                    "symbol": symbol,
                    "direction": s.direction,
                    "qty": pyr_qty,
                    "entry_hint": current_price,
                    "sl": s.pyramid_sl,
                    "tp": s.pyramid_tp,
                    "reason": f"PYRAMID_{s.direction}_{s.grade}",
                }
        except Exception as e:
            logger.debug("PyramidManager.on_price_update %s: %e", symbol, e)
        return None

    def get_state(self, symbol: str) -> Optional[PyramidState]:
        return self._states.get(symbol)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _t1_reached(s: PyramidState, price: float) -> bool:
    if s.direction == "LONG":
        return price >= s.target_1
    return price <= s.target_1


def _pyramid_trigger_price(s: PyramidState) -> float:
    """Pullback to breakeven + 0.3×ATR in trend direction."""
    if s.direction == "LONG":
        return s.original_entry + 0.3 * s.atr
    return s.original_entry - 0.3 * s.atr


def _price_at_trigger(s: PyramidState, price: float, trigger: float) -> bool:
    """Price has pulled back to / through trigger level."""
    if s.direction == "LONG":
        return price <= trigger
    return price >= trigger
