"""
execution_groww.py — NSE Momentum Groww AI Bot
Live Order Placement, Modification, Cancellation via growwapi

⚠️ WARNING: THIS MODULE PLACES REAL ORDERS WITH REAL MONEY ON GROWW.
⚠️ ALL orders use MIS (Margin Intraday Square-off) product type.
⚠️ LIVE_TRADING_ENABLED must be True in .env for real order placement.
⚠️ Start with very small capital. Monitor manually at first.

Server runs in UK (UTC) — all timestamps in IST.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, round_to_tick_size
from auth_groww import get_groww_token
from risk_manager import Position, RiskManager
from signal_generator import TradeSignal

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class GrowwIPBlockedError(Exception):
    """Raised when Groww rejects order placement due to unregistered server IP."""

_ip_block_last_alerted: Optional[datetime] = None  # throttle spam alerts

# SEBI-compliant trade journal DB
TRADE_DB_PATH = "logs/trades/trade_journal.db"


class OrderResult:
    def __init__(self, success: bool, order_id: str = "", message: str = "",
                 raw: dict = None):
        self.success = success
        self.order_id = order_id
        self.message = message
        self.raw = raw or {}
        self.timestamp = format_ist_timestamp()

    def __repr__(self):
        status = "✅" if self.success else "❌"
        return f"{status} OrderResult(id={self.order_id}, msg={self.message})"


class GrowwExecutor:
    """
    Live order execution engine for Groww.
    All orders are MIS (intraday). No delivery orders.
    """

    def __init__(self, risk_manager: RiskManager, live_enabled: bool = False):
        self.risk_manager = risk_manager
        self.live_enabled = live_enabled
        self._api = None
        self._init_api()
        self._init_db()

        if self.live_enabled:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚡ LIVE TRADING ENABLED — "
                "REAL ORDERS WILL BE PLACED ON GROWW"
            )
        else:
            logger.info(
                f"[{format_ist_timestamp()}] 🔒 DRY RUN MODE — "
                "No real orders will be placed"
            )

    def _init_api(self):
        """Initialize Groww API client."""
        try:
            from growwapi import GrowwAPI
            token = get_groww_token()
            if token:
                self._api = GrowwAPI(token)
                # Log available order-placement methods for diagnostics
                order_methods = [m for m in dir(self._api)
                                 if not m.startswith("_") and
                                 any(k in m.lower() for k in ("order", "place", "trade", "buy", "sell"))]
                logger.info(
                    f"[{format_ist_timestamp()}] GrowwAPI executor initialized | "
                    f"Order methods: {order_methods}"
                )
        except ImportError:
            logger.error("growwapi not installed — order execution disabled")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Executor API init error: {e}")

    # --------------------------------------------------------
    # ROBUST ORDER PLACEMENT (method-name discovery)
    # --------------------------------------------------------

    _ORDER_METHOD_NAMES = [
        "place_order",
        "place_equity_order",
        "place_mis_order",
        "create_order",
        "new_order",
        "order",
        "place_new_order",
    ]

    def _call_place_order(self, order_params: dict) -> Optional[dict]:
        """
        Call Groww place_order with the given params.
        Falls back through a priority list of method name variants in case the
        installed SDK version differs. On any successful call (even None response)
        returns the response; only logs an error if NO method name matches at all.
        """
        if not self._api:
            return None

        # Build alternate params in case SDK uses different key names
        alt_params = dict(order_params)
        # Some SDK versions use "symbol" instead of "trading_symbol"
        if "trading_symbol" in alt_params:
            alt_params["symbol"] = alt_params.pop("trading_symbol")
        # Some SDK versions use "product_type" instead of "product"
        if "product" in alt_params:
            alt_params["product_type"] = alt_params.pop("product")
        # Some SDK versions omit "segment"
        alt_params_noseg = {k: v for k, v in order_params.items() if k != "segment"}

        tried = []
        for method_name in self._ORDER_METHOD_NAMES:
            if not hasattr(self._api, method_name):
                continue
            fn = getattr(self._api, method_name)
            tried.append(method_name)

            for label, params in (
                ("primary", order_params),
                ("alt",     alt_params),
                ("noseg",   alt_params_noseg),
            ):
                try:
                    resp = fn(**params)
                    logger.info(
                        f"[{format_ist_timestamp()}] place_order called via "
                        f"'{method_name}' ({label} params) → {resp}"
                    )
                    # Return whatever the SDK gives (None = order rejected at API level)
                    return resp
                except TypeError as te:
                    logger.debug(
                        f"[{format_ist_timestamp()}] '{method_name}' ({label}) "
                        f"TypeError: {te} — trying next param set"
                    )
                except Exception as e:
                    err_str = str(e).lower()
                    # IP whitelist errors affect ALL methods — bail immediately
                    if any(kw in err_str for kw in (
                        "ip", "inactive", "registered ip", "not whitelisted",
                        "ip not", "ip address", "allowed ip",
                    )):
                        raise GrowwIPBlockedError(str(e))
                    logger.warning(
                        f"[{format_ist_timestamp()}] '{method_name}' ({label}) error: {e}"
                    )
                    break  # Non-TypeError: method exists but call rejected → next method

        if not tried:
            # Log all API methods so we know what to add next time
            all_methods = [m for m in dir(self._api) if not m.startswith("_")]
            logger.error(
                f"[{format_ist_timestamp()}] ❌ No order method found in SDK. "
                f"Available API methods: {all_methods}"
            )
        return None

    def _init_db(self):
        """Initialize SEBI-compliant trade journal SQLite DB."""
        Path(TRADE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(TRADE_DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                symbol TEXT,
                direction TEXT,
                quantity INTEGER,
                entry_price REAL,
                exit_price REAL,
                stop_loss REAL,
                target_1 REAL,
                target_2 REAL,
                pnl REAL,
                pnl_pct REAL,
                product TEXT DEFAULT 'MIS',
                signal_score REAL,
                patterns TEXT,
                entry_time TEXT,
                exit_time TEXT,
                exit_reason TEXT,
                atr REAL,
                live_trade INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    # --------------------------------------------------------
    # ENTRY ORDERS
    # --------------------------------------------------------

    def place_entry_order(self, signal: TradeSignal) -> OrderResult:
        """
        Place entry order based on a TradeSignal.
        Uses LIMIT order near current price, falls back to MARKET.

        Args:
            signal: TradeSignal from signal_generator

        Returns:
            OrderResult with order_id on success
        """
        # ── F&O ELIGIBILITY CHECK (SELL/SHORT orders only) ──────────────
        # NSE only allows intraday short-selling on F&O segment stocks.
        # Attempting to short an equity-only stock → Groww REJECTS the order.
        # Rejection after position tracking = phantom position risk.
        if signal.direction in ("SHORT", "SELL"):
            try:
                from nse_fo_list import is_fo_eligible
                if not is_fo_eligible(signal.symbol):
                    logger.warning(
                        f"[{format_ist_timestamp()}] ⛔ SHORT BLOCKED: {signal.symbol} "
                        "is NOT F&O eligible — intraday short-selling not allowed "
                        "on equity-only stocks. Order would be rejected by Groww."
                    )
                    return OrderResult(
                        False,
                        message=(
                            f"{signal.symbol} not F&O eligible — "
                            "cannot short equity-only stock on NSE"
                        ),
                    )
            except Exception as e:
                logger.debug(f"F&O check error (allowing trade): {e}")

        # ── Fetch live balance BEFORE risk check so capital is current ──────
        from data_fetch_groww import get_data_fetcher
        fetcher = get_data_fetcher()
        balance = fetcher.get_account_balance()
        available = balance.get("available", 0)
        # update_balance() guards against 0 — keeps last known good capital
        self.risk_manager.update_balance(available)

        logger.info(
            f"[{format_ist_timestamp()}] PRE-TRADE: {signal.symbol} | "
            f"API balance: ₹{available:,.0f} | "
            f"Risk capital: ₹{self.risk_manager.state.daily_capital:,.0f} | "
            f"Score: {signal.signal_score:.0f} | "
            f"Live: {self.live_enabled}"
        )

        # Pre-trade risk check
        can_trade = self.risk_manager.can_take_trade(signal.symbol, signal.direction)
        if not can_trade["allowed"]:
            logger.warning(
                f"[{format_ist_timestamp()}] Trade BLOCKED: {signal.symbol} — "
                f"{can_trade['reason']}"
            )
            return OrderResult(False, message=can_trade["reason"])

        # Recalculate quantity with live balance, apply filter size multiplier
        sizing = self.risk_manager.calculate_position_size(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
        )
        quantity = sizing.get("quantity", 0)
        logger.info(
            f"[{format_ist_timestamp()}] SIZING: {signal.symbol} qty={quantity} | "
            f"{sizing.get('reason', 'OK')} | "
            f"capital=₹{sizing.get('capital_used', 0):,.0f} | "
            f"session={sizing.get('session', '?')}"
        )
        if quantity <= 0:
            return OrderResult(
                False,
                message=(
                    f"Position size = 0 — {sizing.get('reason', 'insufficient capital')} "
                    f"(capital=₹{self.risk_manager.state.daily_capital:,.0f})"
                )
            )
        # Apply grade-based size multiplier from HighAccuracyFilter
        size_mult = getattr(signal, "size_multiplier", 1.0)
        if size_mult != 1.0:
            quantity = max(1, int(quantity * size_mult))
            logger.info(
                f"[{format_ist_timestamp()}] Size adjusted by {size_mult:.1f}x "
                f"(Grade {getattr(signal, 'quality_grade', 'B')}) → {quantity} qty"
            )

        entry_price = round_to_tick_size(signal.entry_price)
        transaction_type = "BUY" if signal.direction == "LONG" else "SELL"

        logger.info(
            f"[{format_ist_timestamp()}] {'[LIVE]' if self.live_enabled else '[DRY]'} "
            f"Placing {transaction_type} {signal.symbol} x{quantity} @ ₹{entry_price:.2f} "
            f"| SL: ₹{signal.stop_loss:.2f} | Score: {signal.signal_score:.0f}"
        )

        if not self.live_enabled:
            # Dry run — simulate order
            fake_id = f"DRY_{signal.symbol}_{get_current_ist_time().strftime('%H%M%S')}"
            position = self._create_position(signal, quantity, entry_price, fake_id)
            self.risk_manager.add_position(position)
            self._log_to_db(signal, quantity, entry_price, fake_id, live=False)
            logger.info(
                f"[{format_ist_timestamp()}] [DRY RUN] Order simulated: {fake_id}"
            )
            return OrderResult(True, order_id=fake_id,
                               message=f"Dry run — {transaction_type} {quantity}x {signal.symbol}")

        # LIVE ORDER
        if not self._api:
            logger.warning(f"[{format_ist_timestamp()}] API not initialized — attempting re-init...")
            self._init_api()
            if not self._api:
                # This was the silent killer: auth not ready = zero orders placed
                msg = (
                    f"❌ Groww API not initialized for {signal.symbol}. "
                    "Token not yet obtained — will retry next cycle after 8:30 AM login."
                )
                logger.error(f"[{format_ist_timestamp()}] {msg}")
                try:
                    import requests as _req, config as _cfg
                    if _cfg.TELEGRAM_BOT_TOKEN and _cfg.TELEGRAM_CHAT_ID:
                        _req.post(
                            f"https://api.telegram.org/bot{_cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": _cfg.TELEGRAM_CHAT_ID, "parse_mode": "HTML",
                                  "text": f"⚠️ <b>Order Skipped — API Not Ready</b>\n"
                                          f"Symbol: {signal.symbol}\n"
                                          f"Reason: Groww token not obtained yet.\n"
                                          f"Bot will retry login in 5 minutes."},
                            timeout=5,
                        )
                except Exception:
                    pass
                return OrderResult(False, message=msg)

        try:
            # ── MARKET order: fills immediately at current price.
            # LIMIT orders at a stale signal price risk never filling within the
            # NSE session window, forcing cancellation and missed momentum moves.
            # For liquid NSE stocks the slippage on MARKET is negligible (<0.05%).
            order_params = {
                "trading_symbol": signal.symbol,
                "exchange": "NSE",
                "segment": "CASH",         # Equity cash segment
                "transaction_type": transaction_type,
                "quantity": quantity,
                "product": "MIS",          # Intraday only — never delivery
                "order_type": "MARKET",    # MARKET: fills instantly, no stale-price risk
                "validity": "DAY",
            }

            response = self._call_place_order(order_params)
            logger.info(f"[{format_ist_timestamp()}] place_order response: {response}")

            if response and (response.get("order_id") or response.get("id")):
                order_id = str(response.get("order_id") or response.get("id"))

                # ── Wait for fill confirmation (MARKET fills in <5s normally) ──
                # We do NOT cancel on timeout: a MARKET order is already sent to
                # the exchange and will fill at whatever the current price is.
                # Cancelling would create a phantom unclosed position.
                filled_price = self._wait_for_fill(
                    order_id, entry_price, timeout=60, is_market_order=True
                )

                # For MARKET orders: if status API is slow, trust the order went
                # through and use the live quote as a proxy for fill price.
                if filled_price is None:
                    from data_fetch_groww import get_data_fetcher
                    q = get_data_fetcher().get_quote(signal.symbol)
                    filled_price = q.get("ltp", entry_price) if q else entry_price
                    logger.warning(
                        f"[{format_ist_timestamp()}] ⚠️ {order_id}: fill status not "
                        f"confirmed in 60s — using live LTP ₹{filled_price:.2f} as fill price. "
                        f"CHECK GROWW APP to verify position."
                    )

                position = self._create_position(signal, quantity, filled_price, order_id)
                self.risk_manager.add_position(position)
                self._log_to_db(signal, quantity, filled_price, order_id, live=True)

                logger.info(
                    f"[{format_ist_timestamp()}] ✅ ORDER PLACED: {order_id} | "
                    f"{transaction_type} {signal.symbol} x{quantity} "
                    f"@ ₹{filled_price:.2f} (signal ₹{entry_price:.2f})"
                )
                return OrderResult(True, order_id=order_id,
                                   message=f"Filled: {order_id}", raw=response)
            else:
                logger.error(
                    f"[{format_ist_timestamp()}] Order placement failed: {response}"
                )
                return OrderResult(False, message=f"API error: {response}")

        except GrowwIPBlockedError as ip_err:
            msg = (
                "🚨 GROWW IP BLOCKED — Orders cannot be placed!\n\n"
                "Your VPS IP is not whitelisted in Groww.\n\n"
                "Fix:\n"
                "1. Go to Groww → Profile → Developer API Settings\n"
                "2. Add your VPS IP (187.127.154.164) to the allowed list\n"
                "3. Save and wait 1-2 minutes for it to take effect\n\n"
                f"Raw error: {ip_err}"
            )
            logger.error(f"[{format_ist_timestamp()}] {msg}")
            # Send Telegram alert only once per hour to avoid spam
            global _ip_block_last_alerted
            now = get_current_ist_time()
            if (_ip_block_last_alerted is None or
                    (now - _ip_block_last_alerted).total_seconds() > 3600):
                _ip_block_last_alerted = now
                try:
                    from alerts_telegram import TelegramAlerter
                    import os
                    alerter = TelegramAlerter(
                        os.getenv("TELEGRAM_BOT_TOKEN", ""),
                        os.getenv("TELEGRAM_CHAT_ID", ""),
                    )
                    alerter.send_text(msg)
                except Exception:
                    pass
            return OrderResult(False, message="IP not whitelisted on Groww")

        except Exception as e:
            logger.error(
                f"[{format_ist_timestamp()}] place_entry_order exception: {e}"
            )
            return OrderResult(False, message=str(e))

    # --------------------------------------------------------
    # EXIT ORDERS
    # --------------------------------------------------------

    def place_exit_order(
        self,
        symbol: str,
        quantity: int,
        direction: str,
        reason: str = "Signal exit",
        use_market_order: bool = False,
    ) -> OrderResult:
        """
        Place exit (square-off) order for an open position.
        Uses MARKET order for urgent exits (SL hit, kill switch, EOD).
        """
        exit_type = "SELL" if direction == "LONG" else "BUY"
        order_type = "MARKET" if use_market_order else "LIMIT"

        if not self.live_enabled:
            from data_fetch_groww import get_data_fetcher
            fetcher = get_data_fetcher()
            quote = fetcher.get_quote(symbol)
            exit_price = quote.get("ltp", 0) if quote else 0
            trade = self.risk_manager.close_position(symbol, exit_price, reason)
            if trade:
                self._update_db_exit(symbol, exit_price, reason)
            fake_id = f"DRY_EXIT_{symbol}_{get_current_ist_time().strftime('%H%M%S')}"
            logger.info(f"[{format_ist_timestamp()}] [DRY RUN] Exit simulated: {symbol}")
            return OrderResult(True, order_id=fake_id,
                               message=f"Dry exit {symbol} — {reason}")

        if not self._api:
            self._init_api()

        try:
            from data_fetch_groww import get_data_fetcher
            fetcher = get_data_fetcher()
            quote = fetcher.get_quote(symbol)
            exit_price = round_to_tick_size(quote.get("ltp", 0)) if quote else 0

            params = {
                "trading_symbol": symbol,  # Groww SDK uses trading_symbol, not symbol
                "exchange": "NSE",
                "segment": "CASH",         # Required by Groww SDK for equities
                "transaction_type": exit_type,
                "quantity": quantity,
                "product": "MIS",
                "order_type": order_type,
                "validity": "DAY",
            }
            if order_type == "LIMIT" and exit_price:
                params["price"] = exit_price

            response = self._call_place_order(params)

            if response and (response.get("order_id") or response.get("id")):
                order_id = str(response.get("order_id") or response.get("id"))
                trade = self.risk_manager.close_position(symbol, exit_price, reason)
                if trade:
                    self._update_db_exit(symbol, exit_price, reason)
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ EXIT ORDER: {symbol} x{quantity} | {reason}"
                )
                return OrderResult(True, order_id=order_id, raw=response)
            else:
                # Try market order fallback
                params["order_type"] = "MARKET"
                params.pop("price", None)
                response2 = self._call_place_order(params)
                if response2 and (response2.get("order_id") or response2.get("id")):
                    order_id = str(response2.get("order_id") or response2.get("id"))
                    self.risk_manager.close_position(symbol, exit_price, reason + " (MARKET fallback)")
                    return OrderResult(True, order_id=order_id, raw=response2)
                return OrderResult(False, message=f"Exit failed: {response}")

        except GrowwIPBlockedError as ip_err:
            logger.error(
                f"[{format_ist_timestamp()}] EXIT BLOCKED — IP not whitelisted: {ip_err}. "
                "Add VPS IP (187.127.154.164) to Groww Developer API Settings."
            )
            return OrderResult(False, message="IP not whitelisted — exit blocked")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] place_exit_order error: {e}")
            return OrderResult(False, message=str(e))

    # --------------------------------------------------------
    # MODIFY STOP LOSS
    # --------------------------------------------------------

    def modify_stop_loss(self, symbol: str, new_sl: float) -> OrderResult:
        """Modify the SL for an open position's SL order."""
        if symbol not in self.risk_manager.state.positions:
            return OrderResult(False, message=f"No position found for {symbol}")

        pos = self.risk_manager.state.positions[symbol]
        pos.stop_loss = new_sl
        if pos.trailing_active:
            pos.trailing_stop = new_sl

        if not self.live_enabled:
            logger.info(f"[{format_ist_timestamp()}] [DRY] SL modified: {symbol} → ₹{new_sl:.2f}")
            return OrderResult(True, message=f"Dry: SL updated to ₹{new_sl:.2f}")

        # Modify SL order on Groww if sl_order_id exists
        if pos.sl_order_id and self._api:
            try:
                response = self._api.modify_order(
                    order_id=pos.sl_order_id,
                    price=round_to_tick_size(new_sl),
                )
                if response:
                    logger.info(
                        f"[{format_ist_timestamp()}] SL modified: {symbol} → ₹{new_sl:.2f}"
                    )
                    return OrderResult(True, order_id=pos.sl_order_id)
            except Exception as e:
                logger.error(f"Modify SL error: {e}")

        return OrderResult(True, message=f"SL updated locally to ₹{new_sl:.2f}")

    # --------------------------------------------------------
    # SQUARE-OFF ALL (EOD / KILL SWITCH)
    # --------------------------------------------------------

    def square_off_all(self, reason: str = "EOD square-off") -> List[OrderResult]:
        """
        Square off ALL open positions immediately.
        Used for EOD (3:20 PM IST) and kill switch.
        Always uses MARKET orders for guaranteed execution.
        """
        logger.warning(
            f"[{format_ist_timestamp()}] 🔴 SQUARE OFF ALL POSITIONS: {reason}"
        )
        results = []
        positions = list(self.risk_manager.state.positions.values())

        for pos in positions:
            result = self.place_exit_order(
                symbol=pos.symbol,
                quantity=pos.quantity,
                direction=pos.direction,
                reason=reason,
                use_market_order=True,  # MARKET for guaranteed fill
            )
            results.append(result)
            if not result.success:
                logger.error(
                    f"[{format_ist_timestamp()}] ❌ Failed to exit {pos.symbol}: "
                    f"{result.message}"
                )
        logger.warning(
            f"[{format_ist_timestamp()}] Square-off complete: "
            f"{sum(1 for r in results if r.success)}/{len(results)} successful"
        )
        return results

    # --------------------------------------------------------
    # HELPERS
    # --------------------------------------------------------
    # ORDER FILL CONFIRMATION
    # --------------------------------------------------------

    def _wait_for_fill(self, order_id: str, expected_price: float,
                       timeout: int = 60,
                       is_market_order: bool = False) -> Optional[float]:
        """
        Poll Groww order status until FILLED or timeout.
        Returns actual fill price on success, None if status not confirmed.

        MARKET orders fill in <5s. LIMIT orders may take longer.
        For MARKET orders we do NOT cancel on timeout — the order is already at
        the exchange and will fill. Caller handles the None case by using LTP.

        Groww order statuses:
          Pending : PLACED, OPEN, PENDING, TRANSIT, TRIGGER_PENDING, OPEN_PENDING
          Filled  : COMPLETE, FILLED, TRADED, EXECUTED, PARTIAL_EXECUTED
          Terminal: CANCELLED, REJECTED, EXPIRED, FAILED
        """
        import time as _time
        deadline = _time.time() + timeout
        poll_interval = 2  # Check every 2 seconds

        # Status sets
        FILL_STATUSES     = {"COMPLETE", "FILLED", "TRADED", "EXECUTED",
                             "PARTIAL_EXECUTED", "FULL", "DONE", "SUCCESS"}
        TERMINAL_STATUSES = {"CANCELLED", "REJECTED", "EXPIRED", "FAILED",
                             "CANCEL", "REJECT"}

        while _time.time() < deadline:
            try:
                status_resp = self._api.get_order(order_id=order_id)
                if not status_resp:
                    _time.sleep(poll_interval)
                    continue

                # Unwrap envelope if needed
                data = status_resp
                for env_key in ("data", "payload", "result"):
                    if env_key in status_resp and isinstance(status_resp[env_key], dict):
                        data = status_resp[env_key]
                        break

                raw_status = (
                    data.get("status") or
                    data.get("order_status") or
                    data.get("orderStatus") or
                    status_resp.get("status") or
                    ""
                )
                status = str(raw_status).upper().strip()

                logger.debug(f"Order {order_id} status: {status!r}")

                if status in FILL_STATUSES:
                    fill_price = (
                        data.get("average_price") or
                        data.get("avg_price") or
                        data.get("averagePrice") or
                        data.get("filled_price") or
                        status_resp.get("average_price") or
                        expected_price
                    )
                    return float(fill_price)

                if status in TERMINAL_STATUSES:
                    reason = (
                        data.get("message") or data.get("reason") or
                        data.get("errorMessage") or data.get("reject_reason") or
                        "No reason provided"
                    )
                    logger.warning(
                        f"[{format_ist_timestamp()}] Order {order_id} {status}: {reason}"
                    )
                    r = str(reason).lower()
                    if "circuit"  in r:
                        logger.error(f"⛔ CIRCUIT LIMIT hit: {order_id}")
                    elif "margin" in r or "fund" in r:
                        logger.error(f"⛔ INSUFFICIENT MARGIN: {order_id} — reduce qty")
                    elif "short"  in r or "sell" in r:
                        logger.error(f"⛔ SHORT REJECTED: {order_id} — check F&O eligibility")
                    return None

                # Still pending (PLACED / OPEN / TRANSIT / etc.) — keep polling
                _time.sleep(poll_interval)

            except Exception as e:
                logger.debug(f"Order status check error: {e}")
                _time.sleep(poll_interval)

        # Timeout
        order_type_label = "MARKET" if is_market_order else "LIMIT"
        logger.warning(
            f"[{format_ist_timestamp()}] {order_type_label} order {order_id} "
            f"fill not confirmed in {timeout}s"
        )
        return None  # Caller decides whether to cancel or trust the fill

    def _cancel_order(self, order_id: str) -> bool:
        """Cancel an unfilled order."""
        try:
            resp = self._api.cancel_order(order_id=order_id)
            logger.info(f"[{format_ist_timestamp()}] Order {order_id} cancelled")
            return bool(resp)
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Cancel order {order_id} failed: {e}")
            return False

    # --------------------------------------------------------

    def _create_position(
        self, signal: TradeSignal, quantity: int,
        entry_price: float, order_id: str
    ) -> Position:
        return Position(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=signal.stop_loss,
            target_1=signal.target_1,
            target_2=signal.target_2,
            atr=signal.atr,
            order_id=order_id,
            quality_grade=getattr(signal, "quality_grade", "B"),
            size_multiplier=getattr(signal, "size_multiplier", 1.0),
        )

    def _log_to_db(
        self, signal: TradeSignal, quantity: int,
        entry_price: float, order_id: str, live: bool
    ):
        """Log trade entry to SEBI-compliant SQLite journal."""
        try:
            conn = sqlite3.connect(TRADE_DB_PATH)
            conn.execute("""
                INSERT INTO execution_trades
                (order_id, symbol, direction, quantity, entry_price,
                 stop_loss, target_1, target_2, product, signal_score,
                 patterns, entry_time, live_trade, created_at, atr)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                order_id, signal.symbol, signal.direction, quantity, entry_price,
                signal.stop_loss, signal.target_1, signal.target_2, "MIS",
                signal.signal_score, str(signal.patterns),
                format_ist_timestamp(), int(live),
                format_ist_timestamp(), signal.atr,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB log error: {e}")

    def _update_db_exit(self, symbol: str, exit_price: float, reason: str):
        """Update trade journal with exit details."""
        try:
            pos_closed = None
            for t in self.risk_manager.state.closed_trades:
                if t.get("symbol") == symbol:
                    pos_closed = t
                    break
            if not pos_closed:
                return
            pnl = pos_closed.get("pnl", 0)
            conn = sqlite3.connect(TRADE_DB_PATH)
            conn.execute("""
                UPDATE execution_trades SET exit_price=?, pnl=?, pnl_pct=?,
                exit_time=?, exit_reason=?
                WHERE symbol=? AND exit_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (exit_price, pnl, pos_closed.get("pnl_pct", 0),
                  format_ist_timestamp(), reason, symbol))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB exit update error: {e}")
