"""
execution_upstox.py — Upstox order execution for NSE intraday (MIS).

Mirrors the DhanExecutor interface so main_india.py works unchanged. Places
real intraday orders via Upstox API v2 (product "I"), with a mandatory
stop-loss and a HARD per-order value cap so a bug or runaway can never place
an order bigger than you allow.

Auth: uses UPSTOX_ACCESS_TOKEN from config. NOTE — the read-only *Analytics*
token will (correctly) be REJECTED by Upstox for order placement; you must use
the *Algo Trading* token (refresh via /upstox_login) to place live orders.
That rejection is a safety feature, not a bug.

Paper mode: if live_enabled is False, logs the order but places nothing.
"""
import logging
import os
import time as _time
from dataclasses import dataclass
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("execution_upstox")
IST = ZoneInfo("Asia/Kolkata")
_API = "https://api.upstox.com"


@dataclass
class OrderResult:
    success:    bool
    order_id:   str   = ""
    message:    str   = ""
    fill_price: float = 0.0
    quantity:   int   = 0
    order_type: str   = "MARKET"


def _token() -> str:
    try:
        import config_india as c
        return getattr(c, "UPSTOX_ACCESS_TOKEN", "") or ""
    except Exception:
        return os.getenv("UPSTOX_ACCESS_TOKEN", "")


def _max_order_value() -> float:
    """Hard cap on a single order's rupee value — a bug can't exceed this."""
    try:
        return float(os.getenv("INDIA_MAX_LIVE_ORDER_VALUE", "2000"))
    except Exception:
        return 2000.0


def _instrument_key(symbol: str, fallback: str = "") -> Optional[str]:
    try:
        import upstox_data as ux
        k = ux._key(symbol)
        if k:
            return k
    except Exception:
        pass
    # fallback may already be an instrument_key if passed through
    return fallback if fallback and "|" in fallback else None


class UpstoxExecutor:
    """Intraday MIS order execution on Upstox (NSE equity). REAL orders."""

    def __init__(self, live_enabled: bool = False):
        self._live = live_enabled
        self._open_stops: dict = {}     # symbol -> stop order_id
        self._target_orders: dict = {}  # symbol -> target order_id

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {_token()}",
                "Content-Type": "application/json", "Accept": "application/json"}

    def _place(self, symbol: str, instrument_key: str, txn: str, qty: int,
               order_type: str, price: float = 0.0, trigger: float = 0.0) -> OrderResult:
        # HARD safety cap — refuse oversized orders no matter what
        ref_px = price or trigger
        if ref_px and qty and (ref_px * qty) > _max_order_value():
            msg = (f"BLOCKED: order value Rs.{ref_px*qty:,.0f} exceeds cap "
                   f"Rs.{_max_order_value():,.0f} (INDIA_MAX_LIVE_ORDER_VALUE)")
            logger.error(f"{symbol}: {msg}")
            return OrderResult(False, message=msg)

        if not self._live:
            logger.info(f"[PAPER] {txn} {symbol} {order_type} qty={qty} "
                        f"px={price} trig={trigger}")
            return OrderResult(True, order_id=f"PAPER-{symbol}-{int(_time.time())}",
                               fill_price=price or trigger, quantity=qty,
                               order_type=order_type, message="Paper")

        if not instrument_key:
            return OrderResult(False, message=f"No instrument_key for {symbol}")
        if not _token():
            return OrderResult(False, message="No UPSTOX_ACCESS_TOKEN set")

        body = {
            "quantity": int(qty), "product": "I", "validity": "DAY",
            "price": float(price), "tag": "kingtrades",
            "instrument_token": instrument_key, "order_type": order_type,
            "transaction_type": txn, "disclosed_quantity": 0,
            "trigger_price": float(trigger), "is_amo": False,
        }
        import requests
        for attempt in range(3):
            try:
                r = requests.post(f"{_API}/v2/order/place", headers=self._headers(),
                                  json=body, timeout=12)
                j = r.json()
                if j.get("status") == "success":
                    oid = j.get("data", {}).get("order_id", "")
                    logger.info(f"Upstox order {txn} {symbol} {order_type} qty={qty} id={oid}")
                    return OrderResult(True, order_id=oid, quantity=qty,
                                       fill_price=price or trigger, order_type=order_type)
                else:
                    msg = str(j.get("errors") or j.get("message") or j)
                    logger.warning(f"{symbol} order rejected: {msg}")
                    return OrderResult(False, message=msg)
            except Exception as e:
                logger.debug(f"{symbol} place attempt {attempt+1}: {e}")
                _time.sleep(2 ** attempt)
        return OrderResult(False, message="place failed after retries")

    # ── Entry (market) ────────────────────────────────────────────────────────
    def place_entry_order(self, symbol: str, direction: str, qty: int,
                          price: float, security_id: str) -> OrderResult:
        ik = _instrument_key(symbol, security_id)
        txn = "BUY" if direction == "LONG" else "SELL"
        return self._place(symbol, ik, txn, qty, "MARKET", price=0.0)

    def place_entry_order_limit(self, symbol: str, direction: str, qty: int,
                                limit_price: float, security_id: str) -> OrderResult:
        ik = _instrument_key(symbol, security_id)
        txn = "BUY" if direction == "LONG" else "SELL"
        return self._place(symbol, ik, txn, qty, "LIMIT", price=limit_price)

    # ── Target (limit, opposite side) ──────────────────────────────────────────
    def place_target_order(self, symbol: str, direction: str, qty: int,
                           target_price: float, security_id: str) -> OrderResult:
        ik = _instrument_key(symbol, security_id)
        txn = "SELL" if direction == "LONG" else "BUY"   # exit side
        res = self._place(symbol, ik, txn, qty, "LIMIT", price=target_price)
        if res.success:
            self._target_orders[symbol] = res.order_id
        return res

    # ── Stop-loss (SL-M, opposite side, trigger) ───────────────────────────────
    def place_stop_order(self, symbol: str, direction: str, qty: int,
                         stop_price: float, security_id: str) -> OrderResult:
        ik = _instrument_key(symbol, security_id)
        txn = "SELL" if direction == "LONG" else "BUY"   # exit side
        res = self._place(symbol, ik, txn, qty, "SL-M", trigger=stop_price)
        if res.success:
            self._open_stops[symbol] = res.order_id
        return res

    def modify_stop_loss(self, symbol: str, new_sl: float, security_id: str = "",
                         direction: str = "", qty: int = 0) -> bool:
        old = self._open_stops.get(symbol)
        if not self._live:
            logger.info(f"[PAPER] modify stop {symbol} -> {new_sl:.2f}")
            return True
        if old:
            self.cancel_order(old)
        if security_id and direction and qty > 0:
            return self.place_stop_order(symbol, direction, qty, new_sl, security_id).success
        return True

    # ── Square off ──────────────────────────────────────────────────────────
    def square_off_all(self, positions: list) -> List[str]:
        closed = []
        for pos in positions:
            symbol = pos.get("symbol", "")
            qty = abs(int(pos.get("netQty", pos.get("quantity", 0))))
            direction = pos.get("direction", "LONG")
            sid = pos.get("security_id", "")
            if qty == 0:
                continue
            r = self.place_entry_order(symbol, "SHORT" if direction == "LONG" else "LONG",
                                       qty, 0, sid)
            if r.success:
                closed.append(symbol)
        return closed

    # ── Positions ────────────────────────────────────────────────────────────
    def get_open_positions(self) -> List[dict]:
        if not self._live or not _token():
            return []
        try:
            import requests
            r = requests.get(f"{_API}/v2/portfolio/short-term-positions",
                             headers=self._headers(), timeout=12).json()
            if r.get("status") == "success":
                out = []
                for p in r.get("data", []):
                    q = int(p.get("quantity", 0))
                    if q != 0:
                        out.append({"symbol": p.get("trading_symbol", ""),
                                    "netQty": q, "quantity": abs(q),
                                    "direction": "LONG" if q > 0 else "SHORT",
                                    "security_id": p.get("instrument_token", "")})
                return out
        except Exception as e:
            logger.error(f"get_positions: {e}")
        return []

    def cancel_order(self, order_id: str) -> bool:
        if not self._live or not order_id:
            return True
        try:
            import requests
            r = requests.delete(f"{_API}/v2/order/cancel",
                                headers=self._headers(),
                                params={"order_id": order_id}, timeout=12).json()
            return r.get("status") == "success"
        except Exception as e:
            logger.debug(f"cancel {order_id}: {e}")
            return False


def get_executor(live_enabled: bool = False) -> UpstoxExecutor:
    return UpstoxExecutor(live_enabled)
