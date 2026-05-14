"""
execution_alpaca.py — US Stock Order Execution via Alpaca

Strategy:
  LIMIT-first execution (same as NSE approach):
  - LONG  → LIMIT at ask + slippage buffer (fills immediately on liquid US stocks)
  - SHORT → LIMIT at bid - slippage buffer
  - Poll 12s → widen limit → retry → MARKET fallback at attempt 2

Leverage:
  All orders use Alpaca margin. 4x intraday buying power available
  for PDT-qualified accounts (or international accounts without PDT rule).
  product_type is handled automatically by Alpaca — no MIS/NRML distinction.

Short selling:
  Alpaca supports shorting on most liquid US stocks (HTB fee may apply
  for hard-to-borrow names — the API will reject the order if not available).
"""

import logging
import time as _time
from dataclasses import dataclass, field
from typing import List, Optional

from auth_alpaca import get_auth_manager
from data_fetch_alpaca import get_data_fetcher
from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success:    bool
    order_id:   str   = ""
    message:    str   = ""
    fill_price: float = 0.0
    quantity:   int   = 0
    order_type: str   = "MARKET"


class AlpacaExecutor:
    """
    Executes intraday orders on US markets via Alpaca API.
    Interface matches GrowwExecutor so main.py needs no changes.
    """

    LIMIT_WAIT_SECONDS  = 12    # Wait this long for limit fill before widening
    POLL_INTERVAL       = 2     # Check order status every N seconds

    def __init__(self, risk_manager, live_enabled: bool = False):
        self.risk_manager = risk_manager
        self.live_enabled = live_enabled
        self._auth        = get_auth_manager()

    # ─────────────────────────────────────────────────────────────────────
    # ENTRY
    # ─────────────────────────────────────────────────────────────────────

    def place_entry_order(self, signal) -> OrderResult:
        """
        Place entry order for a signal.
        Returns OrderResult with success flag and fill details.
        """
        symbol    = signal.symbol
        direction = signal.direction   # "LONG" or "SHORT"

        if not self.live_enabled:
            logger.info(
                f"[{format_ist_timestamp()}] PAPER mode — would {direction} {symbol} "
                f"@ ₹{signal.entry_price:.2f} (live_enabled=False)"
            )
            return OrderResult(False, message="Live trading disabled — paper mode")

        if not self._auth.is_configured():
            return OrderResult(False, message="Alpaca API keys not configured")

        # ── Live balance + risk check ─────────────────────────────────────
        fetcher   = get_data_fetcher()
        bal       = fetcher.get_account_balance()
        available = bal.get("available", 0)
        self.risk_manager.update_balance(available)

        logger.info(
            f"[{format_ist_timestamp()}] PRE-TRADE: {symbol} {direction} | "
            f"balance=${available:,.0f} | score={signal.signal_score:.0f}"
        )

        can_trade = self.risk_manager.can_take_trade(symbol, direction)
        if not can_trade["allowed"]:
            logger.warning(
                f"[{format_ist_timestamp()}] Trade BLOCKED: {symbol} — {can_trade['reason']}"
            )
            return OrderResult(False, message=can_trade["reason"])

        # ── Position sizing ───────────────────────────────────────────────
        sizing = self.risk_manager.calculate_position_size(
            symbol          = symbol,
            entry_price     = signal.entry_price,
            stop_loss       = signal.stop_loss,
            direction       = direction,
            size_multiplier = getattr(signal, "size_multiplier", 1.0),
            signal_rr       = getattr(signal, "risk_reward", 2.0),
        )
        quantity = sizing.get("quantity", 0)
        if quantity <= 0:
            return OrderResult(
                False,
                message=f"Position size=0 — {sizing.get('reason', 'insufficient capital')}"
            )

        logger.info(
            f"[{format_ist_timestamp()}] SIZING: {symbol} qty={quantity} | "
            f"capital=${sizing.get('capital_used', 0):,.0f} | "
            f"{sizing.get('reason', '')}"
        )

        # ── LIMIT-first execution (3 attempts) ───────────────────────────
        from slippage_tracker import get_slippage_tracker
        _slip = get_slippage_tracker()
        signal_price = signal.entry_price

        for attempt in range(3):
            if attempt >= 2:
                order_type  = "MARKET"
                limit_price = None
            else:
                order_type  = "LIMIT"
                limit_price = _slip.adjust_limit_price(symbol, direction, signal_price, retry=attempt)
                if limit_price == 0.0:
                    order_type  = "MARKET"
                    limit_price = None

            result = self._submit_order(
                symbol      = symbol,
                qty         = quantity,
                direction   = direction,
                order_type  = order_type,
                limit_price = limit_price,
            )

            if not result.success:
                logger.warning(
                    f"[{format_ist_timestamp()}] Order attempt {attempt+1} failed: "
                    f"{symbol} — {result.message}"
                )
                if attempt < 2:
                    _time.sleep(1)
                continue

            if order_type == "LIMIT":
                # Poll for fill
                filled = self._wait_for_fill(result.order_id, wait=self.LIMIT_WAIT_SECONDS)
                if filled:
                    result.fill_price = filled
                    result.order_type = "LIMIT"
                    break
                else:
                    # Cancel unfilled limit, retry with wider or MARKET
                    self.cancel_order(result.order_id)
                    logger.info(
                        f"[{format_ist_timestamp()}] Limit not filled in {self.LIMIT_WAIT_SECONDS}s "
                        f"— attempt {attempt+2}"
                    )
                    continue
            else:
                # Market order — poll for fill
                filled = self._wait_for_fill(result.order_id, wait=10)
                if filled:
                    result.fill_price = filled
                break

        if not result.success or result.fill_price == 0:
            return OrderResult(False, message=f"All order attempts failed: {symbol}")

        # ── Post-fill: record slippage + alert ────────────────────────────
        _slip.record_fill(
            symbol       = symbol,
            direction    = direction,
            signal_price = signal_price,
            fill_price   = result.fill_price,
            quantity     = quantity,
            order_type   = result.order_type,
        )

        logger.info(
            f"[{format_ist_timestamp()}] ✅ FILLED: {symbol} {direction} "
            f"qty={quantity} @ ${result.fill_price:.2f} "
            f"[{result.order_type}] order_id={result.order_id}"
        )

        try:
            from alerts_telegram import get_alert_manager
            get_alert_manager().send_entry_alert(signal, result.fill_price, quantity)
        except Exception as e:
            logger.debug(f"Entry alert failed: {e}")

        return result

    # ─────────────────────────────────────────────────────────────────────
    # EXIT
    # ─────────────────────────────────────────────────────────────────────

    def place_exit_order(
        self,
        symbol:      str,
        quantity:    int,
        direction:   str,   # original entry direction (LONG/SHORT)
        reason:      str = "EXIT",
        limit_price: Optional[float] = None,
    ) -> OrderResult:
        """
        Close a position. direction is the ENTRY direction.
        LONG entry → sell to close. SHORT entry → buy to cover.
        """
        if not self.live_enabled:
            return OrderResult(False, message="Live trading disabled")

        exit_side = "SHORT" if direction == "LONG" else "LONG"

        if limit_price:
            result = self._submit_order(symbol, quantity, exit_side, "LIMIT", limit_price)
            if result.success:
                filled = self._wait_for_fill(result.order_id, wait=10)
                if filled:
                    result.fill_price = filled
                    return result
                self.cancel_order(result.order_id)

        # Market exit (always succeeds on liquid US stocks)
        result = self._submit_order(symbol, quantity, exit_side, "MARKET")
        if result.success:
            filled = self._wait_for_fill(result.order_id, wait=10)
            if filled:
                result.fill_price = filled

        logger.info(
            f"[{format_ist_timestamp()}] EXIT: {symbol} qty={quantity} "
            f"reason={reason} @ ${result.fill_price:.2f}"
        )
        return result

    # ─────────────────────────────────────────────────────────────────────
    # SQUARE OFF ALL
    # ─────────────────────────────────────────────────────────────────────

    def square_off_all(self) -> List[OrderResult]:
        """
        Close ALL open positions via Alpaca's close-all endpoint.
        Used at EOD or on /kill command.
        """
        results = []
        if not self.live_enabled:
            return results
        try:
            trading_client = self._auth.get_trading_client()
            trading_client.close_all_positions(cancel_orders=True)
            logger.info(f"[{format_ist_timestamp()}] square_off_all: sent close_all_positions")
            results.append(OrderResult(True, message="All positions closed"))
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] square_off_all failed: {e}")
            results.append(OrderResult(False, message=str(e)))
        return results

    # ─────────────────────────────────────────────────────────────────────
    # CANCEL
    # ─────────────────────────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        try:
            trading_client = self._auth.get_trading_client()
            trading_client.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            logger.debug(f"cancel_order({order_id}): {e}")
            return False

    def get_open_positions(self) -> List:
        return get_data_fetcher().get_positions()

    # ─────────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────────────

    def _submit_order(
        self,
        symbol:      str,
        qty:         int,
        direction:   str,   # "LONG" or "SHORT"
        order_type:  str,   # "LIMIT" or "MARKET"
        limit_price: Optional[float] = None,
    ) -> OrderResult:
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderType
            from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest

            side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL

            trading_client = self._auth.get_trading_client()

            if order_type == "LIMIT" and limit_price:
                req = LimitOrderRequest(
                    symbol         = symbol,
                    qty            = qty,
                    side           = side,
                    time_in_force  = TimeInForce.DAY,
                    limit_price    = round(limit_price, 2),
                )
            else:
                req = MarketOrderRequest(
                    symbol        = symbol,
                    qty           = qty,
                    side          = side,
                    time_in_force = TimeInForce.DAY,
                )

            order = trading_client.submit_order(req)
            return OrderResult(
                success    = True,
                order_id   = str(order.id),
                quantity   = qty,
                order_type = order_type,
            )

        except Exception as e:
            msg = str(e)
            logger.warning(
                f"[{format_ist_timestamp()}] _submit_order {symbol} {direction} "
                f"{order_type} qty={qty}: {msg}"
            )
            return OrderResult(False, message=msg)

    def _wait_for_fill(self, order_id: str, wait: int = 12) -> float:
        """
        Poll order status until filled or timeout.
        Returns fill price (float) or 0.0 if not filled.
        """
        deadline = _time.monotonic() + wait
        fetcher  = get_data_fetcher()
        while _time.monotonic() < deadline:
            status = fetcher.get_order_status(order_id)
            if status.get("status") in ("filled", "partially_filled"):
                price = status.get("filled_avg_price", 0.0)
                if price > 0:
                    return float(price)
            if status.get("status") in ("canceled", "expired", "rejected"):
                return 0.0
            _time.sleep(self.POLL_INTERVAL)
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON FACTORY
# ─────────────────────────────────────────────────────────────────────────────

_executor: Optional[AlpacaExecutor] = None


def get_executor(risk_manager=None, live_enabled: bool = False) -> AlpacaExecutor:
    global _executor
    if _executor is None:
        if risk_manager is None:
            raise ValueError("risk_manager required for first AlpacaExecutor init")
        _executor = AlpacaExecutor(risk_manager, live_enabled)
        logger.info(
            f"[{format_ist_timestamp()}] AlpacaExecutor ready "
            f"({'LIVE' if live_enabled else 'PAPER'})"
        )
    return _executor
