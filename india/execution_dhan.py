"""
execution_dhan.py — Dhan order execution for NSE intraday
MIS-equivalent: product_type=INTRADAY on Dhan
All orders are NSE_EQ segment, intraday only (no overnight).

Safety guards (same as US bot):
  - Stop only placed if fill qty > 0 AND fill_price > 0
  - Partial fill: stop uses actual filled_qty
  - Paper mode: logs order but does NOT place if INDIA_LIVE_TRADING_ENABLED != True
"""
import logging
import os
import time as _time
from dataclasses import dataclass, field
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("execution_dhan")
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class OrderResult:
    success:    bool
    order_id:   str   = ""
    message:    str   = ""
    fill_price: float = 0.0
    quantity:   int   = 0
    order_type: str   = "MARKET"


class DhanExecutor:
    """
    Executes intraday MIS-equivalent orders on Dhan (NSE equity).
    Interface mirrors AlpacaExecutor so main_india.py stays clean.
    """

    def __init__(self, dhan_client, live_enabled: bool = False):
        self._client        = dhan_client
        self._live          = live_enabled
        self._open_stops:   dict = {}   # symbol → stop_order_id
        self._target_orders: dict = {}  # symbol → target_order_id

    # ── Entry order ──────────────────────────────────────────────────────────

    def place_entry_order(self, symbol: str, direction: str, qty: int,
                          price: float, security_id: str) -> OrderResult:
        """
        Place INTRADAY MARKET order.
        direction: "LONG" or "SHORT"
        """
        if qty <= 0:
            return OrderResult(False, message=f"qty={qty} invalid")

        txn = "BUY" if direction == "LONG" else "SELL"

        if not self._live:
            logger.info(f"[PAPER] {txn} {qty} {symbol} @ ₹{price:.2f}")
            return OrderResult(True, order_id=f"PAPER-{symbol}-{int(_time.time())}",
                               fill_price=price, quantity=qty, message="Paper fill")

        if self._client is None:
            return OrderResult(False, message="Dhan client not initialised")

        for attempt in range(4):
            try:
                resp = self._client.place_order(
                    security_id   = security_id,
                    exchange_segment = "NSE_EQ",
                    transaction_type = txn,
                    quantity         = qty,
                    order_type       = "MARKET",
                    product_type     = "INTRADAY",
                    price            = 0,
                )
                if resp and resp.get("status") == "success":
                    order_id = resp.get("data", {}).get("orderId", "")
                    fill_info = self._wait_for_fill(order_id)
                    return OrderResult(
                        True,
                        order_id   = order_id,
                        fill_price = fill_info[0],
                        quantity   = fill_info[1],
                        message    = "Filled",
                    )
                return OrderResult(False, message=str(resp))
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"Entry order attempt {attempt+1} failed ({symbol}): {e}")
                _time.sleep(wait)

        return OrderResult(False, message="All retries exhausted")

    # ── Smart limit entry order ──────────────────────────────────────────────

    def place_entry_order_limit(self, symbol: str, direction: str, qty: int,
                                price: float, security_id: str,
                                limit_offset_pct: float = 0.001) -> "OrderResult":
        """
        Smart limit order: sets limit 0.1% above bid for BUY, 0.1% below ask for SELL.
        Falls back to MARKET if not filled in 30s.
        """
        if qty <= 0:
            return OrderResult(False, message=f"qty={qty} invalid")

        txn = "BUY" if direction == "LONG" else "SELL"

        if direction == "LONG":
            limit_price = round(price * (1 + limit_offset_pct), 2)
        else:
            limit_price = round(price * (1 - limit_offset_pct), 2)

        if not self._live:
            logger.info(f"[PAPER] LIMIT {txn} {qty} {symbol} @ ₹{limit_price:.2f}")
            return OrderResult(True, order_id=f"PAPER-LMT-{symbol}-{int(_time.time())}",
                               fill_price=limit_price, quantity=qty,
                               order_type="LIMIT", message="Paper limit fill")

        if self._client is None:
            return OrderResult(False, message="Dhan client not initialised")

        # Place LIMIT order
        limit_order_id = None
        for attempt in range(4):
            try:
                resp = self._client.place_order(
                    security_id      = security_id,
                    exchange_segment = "NSE_EQ",
                    transaction_type = txn,
                    quantity         = qty,
                    order_type       = "LIMIT",
                    product_type     = "INTRADAY",
                    price            = limit_price,
                )
                if resp and resp.get("status") == "success":
                    limit_order_id = resp.get("data", {}).get("orderId", "")
                    break
                return OrderResult(False, message=str(resp))
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"Limit entry attempt {attempt+1} failed ({symbol}): {e}")
                _time.sleep(wait)

        if not limit_order_id:
            return OrderResult(False, message="Limit order placement failed")

        # Poll for fill up to 30 seconds
        for _ in range(30):
            _time.sleep(1)
            try:
                resp = self._client.get_order_by_id(order_id=limit_order_id)
                if resp and resp.get("status") == "success":
                    d = resp.get("data", {})
                    status = d.get("orderStatus", "")
                    if status in ("TRADED", "PART_TRADED"):
                        fp  = float(d.get("averageTradedPrice", 0) or d.get("price", 0))
                        fq  = int(d.get("filledQty", 0) or d.get("quantity", 0))
                        logger.info(f"Limit filled: {symbol} @ ₹{fp:.2f} qty={fq}")
                        return OrderResult(True, order_id=limit_order_id,
                                           fill_price=fp, quantity=fq,
                                           order_type="LIMIT", message="Limit filled")
                    if status in ("REJECTED", "CANCELLED"):
                        logger.warning(f"Limit order {limit_order_id} {status} — falling back to MARKET")
                        break
            except Exception as e:
                logger.debug(f"Limit poll error ({symbol}): {e}")

        # Cancel limit order and fall back to MARKET
        self.cancel_order(limit_order_id)
        logger.info(f"Limit not filled in 30s for {symbol} — placing MARKET fallback")
        result = self.place_entry_order(symbol, direction, qty, price, security_id)
        result.order_type = "MARKET"
        result.message    = "Market fallback (limit timeout)"
        return result

    # ── Target order ─────────────────────────────────────────────────────────

    def place_target_order(self, symbol: str, direction: str, qty: int,
                           target_price: float, security_id: str) -> "OrderResult":
        """Place limit order at T1/T2 target price for automatic exit."""
        if qty <= 0 or target_price <= 0:
            return OrderResult(False, message=f"Guard: qty={qty} target={target_price}")

        # Exit is opposite to entry direction
        txn = "SELL" if direction == "LONG" else "BUY"

        if not self._live:
            logger.info(f"[PAPER] TARGET {txn} {qty} {symbol} limit=₹{target_price:.2f}")
            tid = f"PAPER-TGT-{symbol}-{int(_time.time())}"
            self._target_orders[symbol] = tid
            return OrderResult(True, order_id=tid, quantity=qty,
                               fill_price=target_price, order_type="LIMIT",
                               message="Paper target")

        if self._client is None:
            return OrderResult(False, message="Dhan client not initialised")

        for attempt in range(4):
            try:
                resp = self._client.place_order(
                    security_id      = security_id,
                    exchange_segment = "NSE_EQ",
                    transaction_type = txn,
                    quantity         = qty,
                    order_type       = "LIMIT",
                    product_type     = "INTRADAY",
                    price            = round(target_price, 2),
                    trigger_price    = round(target_price, 2),
                )
                if resp and resp.get("status") == "success":
                    order_id = resp.get("data", {}).get("orderId", "")
                    self._target_orders[symbol] = order_id
                    logger.info(f"Target placed: {symbol} {txn} limit=₹{target_price:.2f} id={order_id}")
                    return OrderResult(True, order_id=order_id, quantity=qty,
                                       fill_price=target_price, order_type="LIMIT",
                                       message="Target limit placed")
                return OrderResult(False, message=str(resp))
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"Target order attempt {attempt+1} failed ({symbol}): {e}")
                _time.sleep(wait)

        return OrderResult(False, message="All retries exhausted")

    # ── Stop-loss order ──────────────────────────────────────────────────────

    def place_stop_order(self, symbol: str, direction: str, qty: int,
                         stop_price: float, security_id: str) -> OrderResult:
        """
        Place SLM (stop-loss market) order to protect open position.
        Guard: only place if qty > 0 and stop_price > 0.
        """
        if qty <= 0 or stop_price <= 0:
            return OrderResult(False, message=f"Guard: qty={qty} stop={stop_price}")

        # Stop direction is opposite of entry
        txn = "SELL" if direction == "LONG" else "BUY"

        if not self._live:
            logger.info(f"[PAPER] STOP {txn} {qty} {symbol} trigger=₹{stop_price:.2f}")
            sid = f"PAPER-STOP-{symbol}-{int(_time.time())}"
            self._open_stops[symbol] = sid
            return OrderResult(True, order_id=sid, quantity=qty,
                               fill_price=stop_price, message="Paper stop")

        if self._client is None:
            return OrderResult(False, message="Dhan client not initialised")

        for attempt in range(4):
            try:
                resp = self._client.place_order(
                    security_id      = security_id,
                    exchange_segment = "NSE_EQ",
                    transaction_type = txn,
                    quantity         = qty,
                    order_type       = "SLM",       # stop-loss market
                    product_type     = "INTRADAY",
                    price            = 0,
                    trigger_price    = round(stop_price, 2),
                )
                if resp and resp.get("status") == "success":
                    order_id = resp.get("data", {}).get("orderId", "")
                    self._open_stops[symbol] = order_id
                    logger.info(f"Stop placed: {symbol} trigger=₹{stop_price:.2f} id={order_id}")
                    return OrderResult(True, order_id=order_id, quantity=qty,
                                       fill_price=stop_price, message="SLM placed")
                return OrderResult(False, message=str(resp))
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"Stop order attempt {attempt+1} failed ({symbol}): {e}")
                _time.sleep(wait)

        return OrderResult(False, message="All retries exhausted")

    # ── Modify stop ──────────────────────────────────────────────────────────

    def modify_stop_loss(self, symbol: str, new_sl: float,
                         security_id: str = "", direction: str = "",
                         qty: int = 0) -> bool:
        """Cancel old SLM and place new SLM at new_sl price."""
        old_id = self._open_stops.get(symbol)
        if not self._live:
            logger.info(f"[PAPER] Modify stop {symbol}: ₹{new_sl:.2f}")
            return True
        # Cancel old
        if old_id:
            try:
                self._client.cancel_order(order_id=old_id)
                logger.info(f"Cancelled old stop {old_id} for {symbol}")
            except Exception as e:
                logger.debug(f"Cancel stop failed ({symbol}): {e}")
        # Place new if we have needed info
        if security_id and direction and qty > 0:
            result = self.place_stop_order(symbol, direction, qty, new_sl, security_id)
            return result.success
        return True  # partial re-placement deferred

    # ── Square off ──────────────────────────────────────────────────────────

    def square_off_all(self, positions: list) -> List[str]:
        """Close all open intraday positions. Returns list of symbols closed."""
        closed = []
        for pos in positions:
            symbol    = pos.get("symbol", "")
            qty       = int(pos.get("netQty", 0))
            direction = pos.get("direction", "LONG")
            sid       = pos.get("security_id", "")
            if qty == 0:
                continue
            try:
                result = self.place_entry_order(symbol, "SHORT" if direction == "LONG" else "LONG",
                                                qty, 0, sid)
                if result.success:
                    closed.append(symbol)
                    logger.info(f"Squared off {symbol} qty={qty}")
            except Exception as e:
                logger.error(f"Square-off failed {symbol}: {e}")
        return closed

    # ── Positions ────────────────────────────────────────────────────────────

    def get_open_positions(self) -> List[dict]:
        """Return list of open intraday positions from Dhan."""
        if not self._live or self._client is None:
            return []
        try:
            resp = self._client.get_positions()
            if resp and resp.get("status") == "success":
                positions = resp.get("data", [])
                # Filter intraday only with non-zero net qty
                return [p for p in positions
                        if p.get("positionType") == "INTRADAY" and int(p.get("netQty", 0)) != 0]
        except Exception as e:
            logger.error(f"get_positions failed: {e}")
        return []

    # ── Cancel ───────────────────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        if not self._live or self._client is None:
            return True
        for attempt in range(4):
            try:
                resp = self._client.cancel_order(order_id=order_id)
                return bool(resp and resp.get("status") == "success")
            except Exception as e:
                _time.sleep(2 ** attempt)
                logger.debug(f"Cancel attempt {attempt+1}: {e}")
        return False

    # ── Fill wait ────────────────────────────────────────────────────────────

    def _wait_for_fill(self, order_id: str, timeout: int = 15) -> tuple:
        """Poll order until filled. Returns (fill_price, filled_qty)."""
        for _ in range(timeout):
            _time.sleep(1)
            try:
                resp = self._client.get_order_by_id(order_id=order_id)
                if resp and resp.get("status") == "success":
                    d = resp.get("data", {})
                    status = d.get("orderStatus", "")
                    if status in ("TRADED", "PART_TRADED"):
                        fp  = float(d.get("averageTradedPrice", 0) or d.get("price", 0))
                        qty = int(d.get("filledQty", 0) or d.get("quantity", 0))
                        return (fp, qty)
                    if status in ("REJECTED", "CANCELLED"):
                        logger.warning(f"Order {order_id} {status}")
                        return (0.0, 0)
            except Exception as e:
                logger.debug(f"fill poll error: {e}")
        logger.warning(f"Fill timeout for {order_id}")
        return (0.0, 0)


def get_executor(dhan_client=None, live_enabled: bool = False) -> DhanExecutor:
    return DhanExecutor(dhan_client, live_enabled)
