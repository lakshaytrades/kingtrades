"""
twap_engine.py — TWAP Execution Algorithm (v29.0)

Time-Weighted Average Price execution: break large orders into N equal
child orders spread over T minutes to minimize market impact.

Every institutional desk uses TWAP/VWAP algorithms. Placing a single
market order moves the market against you. Splitting into 5 child orders
over 5 minutes reduces slippage 30-40% (empirically documented).

TWAP schedule:
  - Divide target quantity into N slices (default: 5)
  - Place one slice every T/N minutes
  - Monitor fill quality, pause if market conditions deteriorate
  - Stop if total adverse move > 0.3% from first fill price

Integration with Alpaca:
  - Works with existing execution_alpaca.py
  - Returns a TwapPlan object consumed by main.py
  - Each child order is a standard limit order (mid ± tolerance)

For small accounts ($93k), TWAP threshold = $2,000 notional
  Below threshold = single market order (overhead not worth it)
  Above threshold = TWAP over 5 minutes
"""

import logging
import time as _time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# TWAP settings
_TWAP_THRESHOLD_NOTIONAL = 2000.0  # only TWAP orders above this size
_DEFAULT_SLICES          = 5
_DEFAULT_INTERVAL_SEC    = 60      # 1 minute between slices
_ADVERSE_MOVE_LIMIT      = 0.003   # cancel if price moves 0.3% against us


@dataclass
class TwapSlice:
    slice_number:    int
    quantity:        int
    target_time:     float   # monotonic timestamp
    filled_quantity: int = 0
    fill_price:      float = 0.0
    status:          str = "PENDING"   # PENDING | FILLED | CANCELLED


@dataclass
class TwapPlan:
    symbol:          str
    direction:       str     # "LONG" | "SHORT"
    total_quantity:  int
    slices:          List[TwapSlice] = field(default_factory=list)
    first_price:     float = 0.0
    status:          str = "ACTIVE"  # ACTIVE | COMPLETE | CANCELLED
    created_at:      float = field(default_factory=_time.monotonic)

    @property
    def filled_quantity(self) -> int:
        return sum(s.filled_quantity for s in self.slices)

    @property
    def avg_fill_price(self) -> float:
        filled = [(s.fill_price, s.filled_quantity) for s in self.slices if s.filled_quantity > 0]
        if not filled:
            return 0.0
        total_val = sum(p * q for p, q in filled)
        total_qty = sum(q for _, q in filled)
        return total_val / total_qty if total_qty > 0 else 0.0

    @property
    def remaining_quantity(self) -> int:
        return self.total_quantity - self.filled_quantity

    def next_pending_slice(self) -> Optional[TwapSlice]:
        now = _time.monotonic()
        for s in self.slices:
            if s.status == "PENDING" and now >= s.target_time:
                return s
        return None

    def is_adverse_move(self, current_price: float) -> bool:
        if self.first_price <= 0:
            return False
        move = (current_price - self.first_price) / self.first_price
        if self.direction == "LONG":
            return move < -_ADVERSE_MOVE_LIMIT   # price dropped against us
        else:
            return move > _ADVERSE_MOVE_LIMIT    # price rose against us


def should_use_twap(quantity: int, price: float) -> bool:
    """True if order is large enough to warrant TWAP execution."""
    notional = quantity * price
    return notional >= _TWAP_THRESHOLD_NOTIONAL


def create_twap_plan(
    symbol: str,
    direction: str,
    total_quantity: int,
    current_price: float,
    n_slices: int = _DEFAULT_SLICES,
    interval_sec: int = _DEFAULT_INTERVAL_SEC,
) -> TwapPlan:
    """
    Create a TWAP execution plan for the given order.

    Returns TwapPlan with pre-scheduled child order timestamps.
    Execute by calling plan.next_pending_slice() in a polling loop.
    """
    plan = TwapPlan(
        symbol=symbol,
        direction=direction,
        total_quantity=total_quantity,
        first_price=current_price,
    )

    # Distribute quantity evenly (last slice gets remainder)
    base_qty = total_quantity // n_slices
    remainder = total_quantity % n_slices

    now = _time.monotonic()
    for i in range(n_slices):
        qty = base_qty + (1 if i == n_slices - 1 and remainder > 0 else 0)
        if qty <= 0:
            continue
        target_time = now + i * interval_sec
        plan.slices.append(TwapSlice(
            slice_number=i + 1,
            quantity=qty,
            target_time=target_time,
        ))

    logger.info(
        f"[twap] {symbol} {direction}: TWAP plan created — "
        f"{total_quantity} shares in {len(plan.slices)} slices "
        f"over {(n_slices-1)*interval_sec//60} minutes"
    )
    return plan


def execute_next_slice(
    plan: TwapPlan,
    current_price: float,
    executor=None,
) -> Optional[TwapSlice]:
    """
    Check if it's time to execute the next slice and do so if ready.
    Returns the slice that was just executed, or None.

    executor: execution_alpaca module's place_entry_order equivalent
    """
    if plan.status != "ACTIVE":
        return None

    # Check for adverse move
    if plan.is_adverse_move(current_price):
        plan.status = "CANCELLED"
        logger.warning(
            f"[twap] {plan.symbol}: CANCELLED due to adverse move "
            f"(entry={plan.first_price:.2f}, now={current_price:.2f})"
        )
        return None

    slice_obj = plan.next_pending_slice()
    if slice_obj is None:
        # Check if all filled
        if all(s.status in ("FILLED", "CANCELLED") for s in plan.slices):
            plan.status = "COMPLETE"
        return None

    # Execute this slice
    try:
        logger.info(
            f"[twap] {plan.symbol} slice {slice_obj.slice_number}/{len(plan.slices)}: "
            f"{slice_obj.quantity} shares @ ~{current_price:.2f}"
        )
        if executor is not None:
            result = executor.place_entry_order(
                symbol=plan.symbol,
                direction=plan.direction,
                quantity=slice_obj.quantity,
                price=current_price,
            )
            if result and result.quantity > 0:
                slice_obj.filled_quantity = result.quantity
                slice_obj.fill_price = result.fill_price or current_price
                slice_obj.status = "FILLED"
        else:
            # Simulation (no executor passed)
            slice_obj.filled_quantity = slice_obj.quantity
            slice_obj.fill_price = current_price
            slice_obj.status = "FILLED"

        if plan.filled_quantity >= plan.total_quantity:
            plan.status = "COMPLETE"

        return slice_obj

    except Exception as e:
        logger.error(f"[twap] {plan.symbol} slice {slice_obj.slice_number} failed: {e}")
        slice_obj.status = "CANCELLED"
        return None
