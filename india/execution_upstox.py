"""
execution_upstox.py — Upstox order execution for NSE intraday

Replaces execution_dhan.py. Public API IDENTICAL (UpstoxExecutor mirrors
the old DhanExecutor) so main_india.py only changes the import + class name.

Upstox order mapping:
  product          = "I"   (Intraday / MIS)
  order_type       = "MARKET" / "LIMIT" / "SL" / "SL-M"
  transaction_type = "BUY" / "SELL"
  instrument_token = instrument_key (e.g. "NSE_EQ|INE002A01018")

Safety guards (unchanged):
  - Stop only placed if fill qty > 0 AND fill_price > 0
  - Partial fill: stop uses actual filled_qty
  - Paper mode: logs order but does NOT place if INDIA_LIVE_TRADING_ENABLED != True
"""
import logging
import os
import time as _time
from dataclasses import dataclass
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("execution_upstox")
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class OrderResult:
    success:    bool
    order_id:   str   = ""
    message:    str   = ""
    fill_price: float = 0.0
    quantity:   int   = 0
    order_type: str   = "MARKET"


def _resp_data(resp):
    """Normalize an Upstox SDK response to a dict."""
    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp.get("data")
    return data


def _order_id_of(resp) -> str:
    data = _resp_data(resp)
    if data is None:
        return ""
    if isinstance(data, dict):
        return str(data.get("order_id", "") or "")
    return str(getattr(data, "order_id", "") or "")


class UpstoxExecutor:
    """Executes intraday MIS orders on Upstox (NSE equity)."""

    def __init__(self, upstox_client, live_enabled: bool = False):
        self._client         = upstox_client
        self._live           = live_enabled
        self._open_stops:    dict = {}
        self._target_orders: dict = {}

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _place(self, instrument_key: str, txn: str, qty: int, order_type: str,
               price: float = 0.0, trigger: float = 0.0) -> Optional[str]:
        """Build + submit an Upstox PlaceOrderRequest. Returns order_id or None."""
        import upstox_client
        body = upstox_client.PlaceOrderRequest(
            quantity=qty,
            product="I",                # Intraday (MIS)
            validity="DAY",
            price=round(price, 2),
            tag="satavector",
            instrument_token=instrument_key,
            order_type=order_type,
            transaction_type=txn,
            disclosed_quantity=0,
            trigger_price=round(trigger, 2),
            is_amo=False,
        )
        resp = self._client.order.place_order(body=body, api_version="2.0")
        return _order_id_of(resp)

    # ── Entry order ──────────────────────────────────────────────────────────

    def place_entry_order(self, symbol: str, direction: str, qty: int,
                          price: float, security_id: str) -> OrderResult:
        """Place INTRADAY MARKET order. security_id = Upstox instrument_key."""
        if qty <= 0:
            return OrderResult(False, message=f"qty={qty} invalid")
        txn = "BUY" if direction == "LONG" else "SELL"

        if not self._live:
            logger.info(f"[PAPER] {txn} {qty} {symbol} @ ₹{price:.2f}")
            return OrderResult(True, order_id=f"PAPER-{symbol}-{int(_time.time())}",
                               fill_price=price, quantity=qty, message="Paper fill")
        if self._client is None:
            return OrderResult(False, message="Upstox client not initialised")

        for attempt in range(4):
            try:
                oid = self._place(security_id, txn, qty, "MARKET")
                if oid:
                    fp, fq = self._wait_for_fill(oid)
                    if fq >= 1 and fp > 0:
                        return OrderResult(True, order_id=oid, fill_price=fp,
                                           quantity=fq, message="Filled")
                    # placement succeeded but fill NOT confirmed. One last status
                    # check, then report failure HONESTLY — success used to mean
                    # "placed", which let callers book exits that never executed.
                    status, fp2, fq2 = self._order_status(oid)
                    if status in ("complete", "filled") and fq2 >= 1:
                        return OrderResult(True, order_id=oid, fill_price=fp2 or fp,
                                           quantity=fq2, message="Filled (late confirm)")
                    return OrderResult(False, order_id=oid,
                                       message=f"placed but not filled "
                                               f"(status={status or 'unknown'})")
                return OrderResult(False, message="No order_id returned")
            except Exception as e:
                logger.warning(f"Entry order attempt {attempt+1} failed ({symbol}): {e}")
                _time.sleep(2 ** attempt)
        return OrderResult(False, message="All retries exhausted")

    # ── Smart limit entry ────────────────────────────────────────────────────

    def place_entry_order_limit(self, symbol: str, direction: str, qty: int,
                                price: float, security_id: str,
                                limit_offset_pct: float = 0.001) -> OrderResult:
        if qty <= 0:
            return OrderResult(False, message=f"qty={qty} invalid")
        txn = "BUY" if direction == "LONG" else "SELL"
        limit_price = (round(price * (1 + limit_offset_pct), 2) if direction == "LONG"
                       else round(price * (1 - limit_offset_pct), 2))

        if not self._live:
            logger.info(f"[PAPER] LIMIT {txn} {qty} {symbol} @ ₹{limit_price:.2f}")
            return OrderResult(True, order_id=f"PAPER-LMT-{symbol}-{int(_time.time())}",
                               fill_price=limit_price, quantity=qty,
                               order_type="LIMIT", message="Paper limit fill")
        if self._client is None:
            return OrderResult(False, message="Upstox client not initialised")

        limit_order_id = None
        for attempt in range(4):
            try:
                limit_order_id = self._place(security_id, txn, qty, "LIMIT", price=limit_price)
                if limit_order_id:
                    break
                return OrderResult(False, message="Limit placement: no order_id")
            except Exception as e:
                logger.warning(f"Limit entry attempt {attempt+1} failed ({symbol}): {e}")
                _time.sleep(2 ** attempt)
        if not limit_order_id:
            return OrderResult(False, message="Limit order placement failed")

        for _ in range(30):
            _time.sleep(1)
            status, fp, fq = self._order_status(limit_order_id)
            if status in ("complete", "filled"):
                logger.info(f"Limit filled: {symbol} @ ₹{fp:.2f} qty={fq}")
                return OrderResult(True, order_id=limit_order_id, fill_price=fp,
                                   quantity=fq, order_type="LIMIT", message="Limit filled")
            if status in ("rejected", "cancelled"):
                logger.warning(f"Limit {limit_order_id} {status} — MARKET fallback")
                break

        self.cancel_order(limit_order_id)
        logger.info(f"Limit not filled in 30s for {symbol} — MARKET fallback")
        result = self.place_entry_order(symbol, direction, qty, price, security_id)
        result.order_type = "MARKET"
        result.message    = "Market fallback (limit timeout)"
        return result

    # ── Target order ─────────────────────────────────────────────────────────

    def place_target_order(self, symbol: str, direction: str, qty: int,
                           target_price: float, security_id: str) -> OrderResult:
        if qty <= 0 or target_price <= 0:
            return OrderResult(False, message=f"Guard: qty={qty} target={target_price}")
        txn = "SELL" if direction == "LONG" else "BUY"

        if not self._live:
            logger.info(f"[PAPER] TARGET {txn} {qty} {symbol} limit=₹{target_price:.2f}")
            tid = f"PAPER-TGT-{symbol}-{int(_time.time())}"
            self._target_orders[symbol] = tid
            return OrderResult(True, order_id=tid, quantity=qty,
                               fill_price=target_price, order_type="LIMIT",
                               message="Paper target")
        if self._client is None:
            return OrderResult(False, message="Upstox client not initialised")

        for attempt in range(4):
            try:
                oid = self._place(security_id, txn, qty, "LIMIT", price=round(target_price, 2))
                if oid:
                    self._target_orders[symbol] = oid
                    logger.info(f"Target placed: {symbol} {txn} limit=₹{target_price:.2f} id={oid}")
                    return OrderResult(True, order_id=oid, quantity=qty,
                                       fill_price=target_price, order_type="LIMIT",
                                       message="Target limit placed")
                return OrderResult(False, message="No order_id returned")
            except Exception as e:
                logger.warning(f"Target order attempt {attempt+1} failed ({symbol}): {e}")
                _time.sleep(2 ** attempt)
        return OrderResult(False, message="All retries exhausted")

    # ── Stop-loss order ──────────────────────────────────────────────────────

    def place_stop_order(self, symbol: str, direction: str, qty: int,
                         stop_price: float, security_id: str) -> OrderResult:
        if qty <= 0 or stop_price <= 0:
            return OrderResult(False, message=f"Guard: qty={qty} stop={stop_price}")
        txn = "SELL" if direction == "LONG" else "BUY"

        if not self._live:
            logger.info(f"[PAPER] STOP {txn} {qty} {symbol} trigger=₹{stop_price:.2f}")
            sid = f"PAPER-STOP-{symbol}-{int(_time.time())}"
            self._open_stops[symbol] = sid
            return OrderResult(True, order_id=sid, quantity=qty,
                               fill_price=stop_price, message="Paper stop")
        if self._client is None:
            return OrderResult(False, message="Upstox client not initialised")

        for attempt in range(4):
            try:
                # SL-M (stop-loss market) — Upstox order_type "SL-M", trigger only
                oid = self._place(security_id, txn, qty, "SL-M", trigger=round(stop_price, 2))
                if oid:
                    self._open_stops[symbol] = oid
                    logger.info(f"Stop placed: {symbol} trigger=₹{stop_price:.2f} id={oid}")
                    return OrderResult(True, order_id=oid, quantity=qty,
                                       fill_price=stop_price, message="SL-M placed")
                return OrderResult(False, message="No order_id returned")
            except Exception as e:
                logger.warning(f"Stop order attempt {attempt+1} failed ({symbol}): {e}")
                _time.sleep(2 ** attempt)
        return OrderResult(False, message="All retries exhausted")

    # ── Modify stop ──────────────────────────────────────────────────────────

    def modify_stop_loss(self, symbol: str, new_sl: float,
                         security_id: str = "", direction: str = "",
                         qty: int = 0) -> bool:
        old_id = self._open_stops.get(symbol)
        if not self._live:
            logger.info(f"[PAPER] Modify stop {symbol}: ₹{new_sl:.2f}")
            return True
        if old_id:
            try:
                self._client.order.cancel_order(order_id=old_id, api_version="2.0")
                logger.info(f"Cancelled old stop {old_id} for {symbol}")
            except Exception as e:
                logger.debug(f"Cancel stop failed ({symbol}): {e}")
        if security_id and direction and qty > 0:
            return self.place_stop_order(symbol, direction, qty, new_sl, security_id).success
        return True

    # ── Square off ──────────────────────────────────────────────────────────

    def square_off_all(self, positions: list) -> List[str]:
        closed = []
        for pos in positions:
            symbol    = pos.get("symbol", "")
            qty       = int(pos.get("netQty", 0))
            direction = pos.get("direction", "LONG")
            sid       = pos.get("security_id", "")
            if qty == 0:
                continue
            try:
                result = self.place_entry_order(
                    symbol, "SHORT" if direction == "LONG" else "LONG",
                    abs(qty), 0, sid)
                if result.success:
                    closed.append(symbol)
                    logger.info(f"Squared off {symbol} qty={qty}")
            except Exception as e:
                logger.error(f"Square-off failed {symbol}: {e}")
        return closed

    # ── Positions ────────────────────────────────────────────────────────────

    def get_open_positions_strict(self) -> List[dict]:
        """Open intraday positions; RAISES on API failure so callers can tell
        'API down' from 'genuinely flat'. Use for square-off / reconcile paths
        where treating an error as flat would strand a live position."""
        if not self._live or self._client is None:
            return []
        resp = self._client.portfolio.get_positions(api_version="2.0")
        data = _resp_data(resp) or []
        out = []
        for p in data:
            def _g(name, default=0):
                return (getattr(p, name, None)
                        or (p.get(name) if isinstance(p, dict) else None) or default)
            qty = int(_g("quantity", 0))
            product = str(_g("product", "")).upper()
            if qty != 0 and product in ("I", "INTRADAY", "MIS"):
                out.append({
                    "symbol": str(_g("trading_symbol", "") or _g("tradingsymbol", "")),
                    "netQty": qty,
                    "direction": "LONG" if qty > 0 else "SHORT",
                    "security_id": str(_g("instrument_token", "")),
                    "avgPrice": float(_g("average_price", 0) or _g("buy_price", 0) or 0),
                    "positionType": "INTRADAY",
                })
        return out

    def get_open_positions(self) -> List[dict]:
        try:
            return self.get_open_positions_strict()
        except Exception as e:
            logger.error(f"get_positions failed: {e}")
        return []

    def pending_order_symbols(self) -> set:
        """Symbols (uppercase) with an open/pending order in today's order book.
        Used before RE-selling a position whose earlier exit is unconfirmed —
        if the first sell is still pending, selling again would open a short.
        Raises on API failure."""
        if not self._live or self._client is None:
            return set()
        resp = self._client.order.get_order_book(api_version="2.0")
        data = _resp_data(resp) or []
        out = set()
        for o in data:
            def _g(name, default=""):
                return (getattr(o, name, None)
                        or (o.get(name) if isinstance(o, dict) else None) or default)
            status = str(_g("status")).lower()
            if status in ("open", "pending", "trigger pending", "open pending",
                          "modify pending", "after market order req received"):
                sym = str(_g("trading_symbol") or _g("tradingsymbol") or "")
                if sym:
                    out.add(sym.upper())
        return out

    # ── Cancel ───────────────────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        if not self._live or self._client is None:
            return True
        for attempt in range(4):
            try:
                resp = self._client.order.cancel_order(order_id=order_id, api_version="2.0")
                return bool(_resp_data(resp) is not None or resp)
            except Exception as e:
                logger.debug(f"Cancel attempt {attempt+1}: {e}")
                _time.sleep(2 ** attempt)
        return False

    # ── Order status / fill wait ─────────────────────────────────────────────

    def _order_status(self, order_id: str):
        """Return (status_lower, fill_price, filled_qty)."""
        try:
            resp = self._client.order.get_order_details(
                order_id=order_id, api_version="2.0")
            d = _resp_data(resp)
            if d is None:
                return ("", 0.0, 0)

            def _g(name, default=0):
                return (getattr(d, name, None)
                        or (d.get(name) if isinstance(d, dict) else None) or default)
            status = str(_g("status", "")).lower()
            fp = float(_g("average_price", 0) or 0)
            fq = int(_g("filled_quantity", 0) or 0)
            return (status, fp, fq)
        except Exception as e:
            logger.debug(f"order_status {order_id}: {e}")
            return ("", 0.0, 0)

    def _wait_for_fill(self, order_id: str, timeout: int = 15) -> tuple:
        for _ in range(timeout):
            _time.sleep(1)
            status, fp, fq = self._order_status(order_id)
            if status in ("complete", "filled") and fp > 0:
                return (fp, fq)
            if status in ("rejected", "cancelled"):
                logger.warning(f"Order {order_id} {status}")
                return (0.0, 0)
        logger.warning(f"Fill timeout for {order_id}")
        return (0.0, 0)


def get_executor(upstox_client=None, live_enabled: bool = False) -> UpstoxExecutor:
    return UpstoxExecutor(upstox_client, live_enabled)
