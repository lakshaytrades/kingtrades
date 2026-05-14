"""
execution_options_alpaca.py — US Options Order Execution via Alpaca

Options execution strategy:
  Entry: LIMIT at mid-price → wait 8s → widen to ask → MARKET fallback
  Stop:  Exit at 45% premium loss (sell for 55% of paid)
  T1:    Exit 60% of position at 80% premium gain
  T2:    Exit remaining at 150% premium gain
  Time:  Force-close 0DTE by 3:30 PM ET, all options by 3:50 PM ET

Position monitoring runs in a background thread and checks every 30s.
"""

import logging
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from auth_alpaca import get_auth_manager
from utils import format_et_timestamp, get_current_et_time

logger = logging.getLogger(__name__)


@dataclass
class OptionsPosition:
    """Tracks a live options position."""
    opt_symbol:    str          # OCC option symbol
    underlying:    str
    direction:     str          # "LONG_CALL" or "LONG_PUT"
    contracts:     int          # number of contracts held
    entry_premium: float        # avg premium paid per contract
    entry_time:    datetime     = field(default_factory=lambda: get_current_et_time())
    order_id:      str          = ""
    dte:           int          = 1

    # State
    t1_taken:      bool = False
    t1_contracts:  int  = 0
    closed:        bool = False
    pnl:           float = 0.0

    @property
    def total_cost(self) -> float:
        return self.entry_premium * self.contracts * 100

    def current_pnl_pct(self, current_mid: float) -> float:
        if self.entry_premium <= 0:
            return 0.0
        return (current_mid - self.entry_premium) / self.entry_premium * 100

    def should_stop(self, current_mid: float, stop_pct: float = 45.0) -> bool:
        return self.current_pnl_pct(current_mid) <= -stop_pct

    def should_take_t1(self, current_mid: float, t1_pct: float = 80.0) -> bool:
        return not self.t1_taken and self.current_pnl_pct(current_mid) >= t1_pct

    def should_take_t2(self, current_mid: float, t2_pct: float = 150.0) -> bool:
        return self.t1_taken and self.current_pnl_pct(current_mid) >= t2_pct


@dataclass
class OptionsOrderResult:
    success:       bool
    order_id:      str   = ""
    message:       str   = ""
    fill_premium:  float = 0.0   # per-contract premium paid/received
    contracts:     int   = 0


class AlpacaOptionsExecutor:
    """
    Places and monitors live US options orders via Alpaca.

    Thread-safe position monitor runs every 30s and handles:
      - Stop loss (45% of premium)
      - Partial T1 exit (60% at 80% gain)
      - Full T2 exit (150% gain)
      - Time-based close (3:30 ET for 0DTE)
      - EOD close (3:50 ET all options)
    """

    LIMIT_WAIT_SECONDS = 8
    POLL_INTERVAL      = 2
    MONITOR_INTERVAL   = 30   # seconds between position checks

    def __init__(self, live_enabled: bool = False):
        self.live_enabled  = live_enabled
        self._auth         = get_auth_manager()
        self._positions:   Dict[str, OptionsPosition] = {}
        self._pos_lock     = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event   = threading.Event()

    # ──────────────────────────────────────────────────────────────
    # ENTRY
    # ──────────────────────────────────────────────────────────────

    def place_entry(
        self,
        opt_symbol:    str,
        underlying:    str,
        direction:     str,   # "LONG_CALL" or "LONG_PUT"
        contracts:     int,
        max_premium:   float,  # max premium per contract we'll pay
        dte:           int = 1,
    ) -> OptionsOrderResult:
        """
        Buy {contracts} option contracts.
        Returns OptionsOrderResult with fill premium per contract.
        """
        if not self.live_enabled:
            logger.info(
                f"[{format_et_timestamp()}] PAPER: would buy {contracts}x {opt_symbol} "
                f"max_premium=${max_premium:.2f}/contract"
            )
            return OptionsOrderResult(False, message="Paper mode — options not placed")

        # LIMIT-first at mid-price, then widen to ask, then MARKET
        from options_alpaca import get_options_data
        opt_data = get_options_data()

        client  = opt_data._get_option_client()
        mid_price = max_premium  # use passed max as starting limit

        for attempt in range(3):
            if attempt == 0:
                order_type  = "LIMIT"
                limit_price = round(mid_price, 2)
            elif attempt == 1:
                order_type  = "LIMIT"
                # Widen to ask (add 5% buffer)
                limit_price = round(mid_price * 1.05, 2)
            else:
                order_type  = "MARKET"
                limit_price = None

            result = self._submit_options_order(
                opt_symbol  = opt_symbol,
                contracts   = contracts,
                side        = "BUY",
                order_type  = order_type,
                limit_price = limit_price,
            )

            if not result.success:
                if attempt < 2:
                    _time.sleep(1)
                continue

            if order_type == "LIMIT":
                fill = self._wait_for_fill(result.order_id, wait=self.LIMIT_WAIT_SECONDS)
                if fill > 0:
                    result.fill_premium = fill
                    result.contracts    = contracts
                    break
                else:
                    self._cancel_order(result.order_id)
                    continue
            else:
                fill = self._wait_for_fill(result.order_id, wait=15)
                result.fill_premium = fill if fill > 0 else mid_price
                result.contracts    = contracts
                break

        if not result.success or result.fill_premium <= 0:
            return OptionsOrderResult(False, message=f"Failed to fill {opt_symbol}")

        # Register position for monitoring
        pos = OptionsPosition(
            opt_symbol    = opt_symbol,
            underlying    = underlying,
            direction     = direction,
            contracts     = contracts,
            entry_premium = result.fill_premium,
            order_id      = result.order_id,
            dte           = dte,
        )
        with self._pos_lock:
            self._positions[opt_symbol] = pos

        logger.info(
            f"[{format_et_timestamp()}] OPTIONS ENTRY: {opt_symbol} "
            f"{contracts}x @ ${result.fill_premium:.2f} premium "
            f"| total cost=${result.fill_premium * contracts * 100:.0f}"
        )

        try:
            from alerts_telegram import get_alert_manager
            get_alert_manager().send_options_entry_alert(pos)
        except Exception:
            pass

        return result

    # ──────────────────────────────────────────────────────────────
    # EXIT
    # ──────────────────────────────────────────────────────────────

    def place_exit(
        self,
        opt_symbol: str,
        contracts:  int,
        reason:     str = "EXIT",
        limit_price: Optional[float] = None,
    ) -> OptionsOrderResult:
        """Sell {contracts} of an existing options position."""
        if not self.live_enabled:
            return OptionsOrderResult(False, message="Paper mode")

        result = OptionsOrderResult(False)
        if limit_price:
            result = self._submit_options_order(
                opt_symbol  = opt_symbol,
                contracts   = contracts,
                side        = "SELL",
                order_type  = "LIMIT",
                limit_price = limit_price,
            )
            if result.success:
                fill = self._wait_for_fill(result.order_id, wait=10)
                if fill > 0:
                    result.fill_premium = fill
                    return result
                self._cancel_order(result.order_id)

        # Market exit
        result = self._submit_options_order(
            opt_symbol = opt_symbol,
            contracts  = contracts,
            side       = "SELL",
            order_type = "MARKET",
        )
        if result.success:
            fill = self._wait_for_fill(result.order_id, wait=15)
            result.fill_premium = fill if fill > 0 else 0.0
            result.contracts    = contracts

        logger.info(
            f"[{format_et_timestamp()}] OPTIONS EXIT ({reason}): {opt_symbol} "
            f"{contracts}x @ ${result.fill_premium:.2f}"
        )

        with self._pos_lock:
            if opt_symbol in self._positions:
                pos = self._positions[opt_symbol]
                remaining = pos.contracts - contracts
                if remaining <= 0:
                    pos.closed = True
                    pnl = (result.fill_premium - pos.entry_premium) * pos.contracts * 100
                    pos.pnl = pnl
                    logger.info(
                        f"[{format_et_timestamp()}] OPTIONS CLOSED: {opt_symbol} "
                        f"P&L=${pnl:+.0f} ({(result.fill_premium-pos.entry_premium)/pos.entry_premium*100:+.1f}%)"
                    )
                    try:
                        from alerts_telegram import get_alert_manager
                        get_alert_manager().send_options_exit_alert(pos, result.fill_premium, reason)
                    except Exception:
                        pass
                    del self._positions[opt_symbol]
                else:
                    pos.contracts = remaining
                    if not pos.t1_taken:
                        pos.t1_taken     = True
                        pos.t1_contracts = contracts

        return result

    # ──────────────────────────────────────────────────────────────
    # POSITION MONITOR (background thread)
    # ──────────────────────────────────────────────────────────────

    def start_monitor(self) -> None:
        """Start the background position monitoring thread."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target = self._monitor_loop,
            name   = "options-monitor",
            daemon = True,
        )
        self._monitor_thread.start()
        logger.info(f"[{format_et_timestamp()}] Options position monitor started")

    def stop_monitor(self) -> None:
        self._stop_event.set()

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._check_positions()
            except Exception as e:
                logger.debug(f"Options monitor error: {e}")
            self._stop_event.wait(timeout=self.MONITOR_INTERVAL)

    def _check_positions(self) -> None:
        now_et = get_current_et_time()
        h, m   = now_et.hour, now_et.minute

        with self._pos_lock:
            symbols = list(self._positions.keys())

        for opt_sym in symbols:
            with self._pos_lock:
                if opt_sym not in self._positions:
                    continue
                pos = self._positions[opt_sym]

            # ── Time-based exits ─────────────────────────────────
            # 0DTE: force close by 3:30 PM ET to avoid expiry
            if pos.dte == 0 and (h, m) >= (15, 30):
                logger.warning(
                    f"[{format_et_timestamp()}] TIME STOP (0DTE): {opt_sym} "
                    "— closing before expiry"
                )
                self.place_exit(opt_sym, pos.contracts, reason="TIME_STOP_0DTE")
                continue

            # All options: close by 3:50 PM ET
            if (h, m) >= (15, 50):
                self.place_exit(opt_sym, pos.contracts, reason="EOD_SQUAREOFF")
                continue

            # ── Premium-based exits ──────────────────────────────
            current_mid = self._get_current_mid(opt_sym)
            if current_mid <= 0:
                continue

            pnl_pct = pos.current_pnl_pct(current_mid)

            # Stop loss
            if pos.should_stop(current_mid, stop_pct=45.0):
                logger.warning(
                    f"[{format_et_timestamp()}] STOP LOSS: {opt_sym} "
                    f"P&L={pnl_pct:.1f}% (entry={pos.entry_premium:.2f} now={current_mid:.2f})"
                )
                self.place_exit(opt_sym, pos.contracts, reason="STOP_LOSS",
                                limit_price=current_mid * 0.95)
                continue

            # T1: 80% gain → exit 60%
            if pos.should_take_t1(current_mid, t1_pct=80.0):
                t1_qty = max(1, round(pos.contracts * 0.60))
                logger.info(
                    f"[{format_et_timestamp()}] T1 HIT: {opt_sym} +{pnl_pct:.1f}% "
                    f"— selling {t1_qty}/{pos.contracts} contracts"
                )
                self.place_exit(opt_sym, t1_qty, reason="TARGET_1",
                                limit_price=current_mid * 0.98)
                continue

            # T2: 150% gain → exit all
            if pos.should_take_t2(current_mid, t2_pct=150.0):
                logger.info(
                    f"[{format_et_timestamp()}] T2 HIT: {opt_sym} +{pnl_pct:.1f}% "
                    f"— closing all {pos.contracts} contracts"
                )
                self.place_exit(opt_sym, pos.contracts, reason="TARGET_2",
                                limit_price=current_mid * 0.98)

    def _get_current_mid(self, opt_sym: str) -> float:
        """Get current mid-price for an option contract."""
        try:
            from alpaca.data.requests import OptionLatestQuoteRequest
            from options_alpaca import get_options_data
            client = get_options_data()._get_option_client()
            if not client:
                return 0.0
            req  = OptionLatestQuoteRequest(symbol_or_symbols=opt_sym)
            data = client.get_option_latest_quote(req)
            q    = data.get(opt_sym)
            if q:
                bid = float(q.bid_price or 0)
                ask = float(q.ask_price or 0)
                if bid > 0 and ask > 0:
                    return (bid + ask) / 2
        except Exception as e:
            logger.debug(f"_get_current_mid({opt_sym}): {e}")
        return 0.0

    # ──────────────────────────────────────────────────────────────
    # CLOSE ALL (for EOD / kill switch)
    # ──────────────────────────────────────────────────────────────

    def close_all_options(self) -> None:
        """Close all open options positions immediately."""
        with self._pos_lock:
            syms = list(self._positions.keys())
        for s in syms:
            with self._pos_lock:
                if s not in self._positions:
                    continue
                pos = self._positions[s]
            self.place_exit(s, pos.contracts, reason="KILL_SWITCH")

    def get_open_options(self) -> List[OptionsPosition]:
        with self._pos_lock:
            return [p for p in self._positions.values() if not p.closed]

    # ──────────────────────────────────────────────────────────────
    # INTERNAL
    # ──────────────────────────────────────────────────────────────

    def _submit_options_order(
        self,
        opt_symbol:  str,
        contracts:   int,
        side:        str,   # "BUY" or "SELL"
        order_type:  str,   # "LIMIT" or "MARKET"
        limit_price: Optional[float] = None,
    ) -> OptionsOrderResult:
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
            from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest

            trading_client = self._auth.get_trading_client()
            order_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL

            if order_type == "LIMIT" and limit_price:
                req = LimitOrderRequest(
                    symbol        = opt_symbol,
                    qty           = contracts,
                    side          = order_side,
                    time_in_force = TimeInForce.DAY,
                    limit_price   = round(limit_price, 2),
                )
            else:
                req = MarketOrderRequest(
                    symbol        = opt_symbol,
                    qty           = contracts,
                    side          = order_side,
                    time_in_force = TimeInForce.DAY,
                )

            order = trading_client.submit_order(req)
            return OptionsOrderResult(
                success  = True,
                order_id = str(order.id),
                contracts= contracts,
            )

        except Exception as e:
            logger.warning(
                f"[{format_et_timestamp()}] Options order failed {opt_symbol} "
                f"{side} {order_type}: {e}"
            )
            return OptionsOrderResult(False, message=str(e))

    def _wait_for_fill(self, order_id: str, wait: int = 10) -> float:
        deadline = _time.monotonic() + wait
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()
            while _time.monotonic() < deadline:
                status = fetcher.get_order_status(order_id)
                if status.get("status") in ("filled", "partially_filled"):
                    price = status.get("filled_avg_price", 0.0)
                    if price > 0:
                        return float(price)
                if status.get("status") in ("canceled", "expired", "rejected"):
                    return 0.0
                _time.sleep(self.POLL_INTERVAL)
        except Exception as e:
            logger.debug(f"Options _wait_for_fill: {e}")
        return 0.0

    def _cancel_order(self, order_id: str) -> None:
        try:
            trading_client = self._auth.get_trading_client()
            trading_client.cancel_order_by_id(order_id)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_opt_executor: Optional[AlpacaOptionsExecutor] = None


def get_options_executor(live_enabled: bool = False) -> AlpacaOptionsExecutor:
    global _opt_executor
    if _opt_executor is None:
        _opt_executor = AlpacaOptionsExecutor(live_enabled=live_enabled)
        logger.info(
            f"[{format_et_timestamp()}] AlpacaOptionsExecutor ready "
            f"({'LIVE' if live_enabled else 'PAPER'})"
        )
    return _opt_executor
