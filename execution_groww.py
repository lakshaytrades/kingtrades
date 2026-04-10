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
                logger.info(f"[{format_ist_timestamp()}] GrowwAPI executor initialized")
        except ImportError:
            logger.error("growwapi not installed — order execution disabled")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Executor API init error: {e}")

    def _init_db(self):
        """Initialize SEBI-compliant trade journal SQLite DB."""
        Path(TRADE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(TRADE_DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
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
        # Pre-trade risk check
        can_trade = self.risk_manager.can_take_trade(signal.symbol, signal.direction)
        if not can_trade["allowed"]:
            logger.warning(
                f"[{format_ist_timestamp()}] Trade BLOCKED: {signal.symbol} — "
                f"{can_trade['reason']}"
            )
            return OrderResult(False, message=can_trade["reason"])

        # Get updated balance
        from data_fetch_groww import get_data_fetcher
        fetcher = get_data_fetcher()
        balance = fetcher.get_account_balance()
        available = balance.get("available", 0)
        self.risk_manager.update_balance(available)

        # Recalculate quantity with live balance, apply filter size multiplier
        sizing = self.risk_manager.calculate_position_size(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
        )
        quantity = sizing.get("quantity", 0)
        if quantity <= 0:
            return OrderResult(False, message="Position size = 0 — insufficient capital")
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
            self._init_api()
            if not self._api:
                return OrderResult(False, message="Groww API not initialized")

        try:
            order_params = {
                "symbol": signal.symbol,
                "exchange": "NSE",
                "transaction_type": transaction_type,
                "quantity": quantity,
                "product": "MIS",          # Intraday only — never delivery
                "order_type": "LIMIT",
                "price": entry_price,
                "validity": "DAY",
            }

            response = self._api.place_order(**order_params)

            if response and (response.get("order_id") or response.get("id")):
                order_id = str(response.get("order_id") or response.get("id"))

                # ── Confirm actual fill before adding to position tracker ──
                filled_price = self._wait_for_fill(order_id, entry_price, timeout=30)
                if filled_price is None:
                    logger.warning(
                        f"[{format_ist_timestamp()}] ⚠️ Order {order_id} not confirmed filled "
                        f"in 30s — cancelling to avoid phantom position"
                    )
                    self._cancel_order(order_id)
                    return OrderResult(False, order_id=order_id,
                                       message="Order not filled — cancelled")

                position = self._create_position(signal, quantity, filled_price, order_id)
                self.risk_manager.add_position(position)
                self._log_to_db(signal, quantity, filled_price, order_id, live=True)

                logger.info(
                    f"[{format_ist_timestamp()}] ✅ ORDER FILLED: {order_id} | "
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
            exit_price = quote["ltp"] if quote else 0
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
            exit_price = round_to_tick_size(quote["ltp"]) if quote else 0

            params = {
                "symbol": symbol,
                "exchange": "NSE",
                "transaction_type": exit_type,
                "quantity": quantity,
                "product": "MIS",
                "order_type": order_type,
                "validity": "DAY",
            }
            if order_type == "LIMIT" and exit_price:
                params["price"] = exit_price

            response = self._api.place_order(**params)

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
                response2 = self._api.place_order(**params)
                if response2 and (response2.get("order_id") or response2.get("id")):
                    order_id = str(response2.get("order_id") or response2.get("id"))
                    self.risk_manager.close_position(symbol, exit_price, reason + " (MARKET fallback)")
                    return OrderResult(True, order_id=order_id, raw=response2)
                return OrderResult(False, message=f"Exit failed: {response}")

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
                       timeout: int = 30) -> Optional[float]:
        """
        Poll Groww order status until FILLED or timeout.
        Returns actual fill price on success, None if not filled.

        Groww MIS LIMIT orders typically fill within 1–5 seconds on liquid stocks.
        We wait up to 30 seconds before cancelling.
        """
        import time as _time
        deadline = _time.time() + timeout
        poll_interval = 2  # Check every 2 seconds

        while _time.time() < deadline:
            try:
                status_resp = self._api.get_order(order_id=order_id)
                if not status_resp:
                    _time.sleep(poll_interval)
                    continue

                status = (
                    status_resp.get("status") or
                    status_resp.get("order_status") or
                    (status_resp.get("data") or {}).get("status", "")
                ).upper()

                if status in ("COMPLETE", "FILLED", "TRADED", "EXECUTED"):
                    # Get actual average fill price
                    fill_price = (
                        status_resp.get("average_price") or
                        status_resp.get("avg_price") or
                        (status_resp.get("data") or {}).get("average_price") or
                        expected_price  # Fallback to signal price
                    )
                    return float(fill_price)

                if status in ("CANCELLED", "REJECTED", "EXPIRED"):
                    logger.warning(
                        f"[{format_ist_timestamp()}] Order {order_id} {status}"
                    )
                    return None

                # PENDING / OPEN / TRIGGER_PENDING — keep waiting
                logger.debug(f"Order {order_id} status: {status} — waiting...")
                _time.sleep(poll_interval)

            except Exception as e:
                logger.debug(f"Order status check error: {e}")
                _time.sleep(poll_interval)

        # Timed out — order still pending (price moved away from limit)
        logger.warning(
            f"[{format_ist_timestamp()}] Order {order_id} not filled in {timeout}s "
            f"(price likely moved away from ₹{expected_price:.2f})"
        )
        return None

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
                INSERT INTO trades
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
                UPDATE trades SET exit_price=?, pnl=?, pnl_pct=?,
                exit_time=?, exit_reason=?
                WHERE symbol=? AND exit_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (exit_price, pnl, pos_closed.get("pnl_pct", 0),
                  format_ist_timestamp(), reason, symbol))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB exit update error: {e}")
