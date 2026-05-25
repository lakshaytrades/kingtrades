"""
crypto_executor.py — Crypto Order Execution via Alpaca

Handles BTC/ETH/SOL and other Alpaca-supported crypto pairs.

Key differences from stock executor:
  - Notional (USD amount) orders, not quantity-based
  - 24/7 execution (no market hours check)
  - GTC time_in_force for non-US-session hours
  - Alpaca crypto symbols: "BTCUSD" (no slash) in orders
  - Fractional quantities supported natively
  - No PDT rule
"""

import logging
import os
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)


@dataclass
class CryptoOrderResult:
    success:    bool
    order_id:   str   = ""
    message:    str   = ""
    fill_price: float = 0.0
    filled_qty: float = 0.0
    notional:   float = 0.0
    order_type: str   = "MARKET"


@dataclass
class CryptoPosition:
    """Tracks an open crypto position in memory."""
    symbol:         str
    direction:      str       # "LONG" or "SHORT"
    notional_usd:   float     # original USD invested
    entry_price:    float
    stop_loss:      float
    target_1:       float
    target_2:       float
    target_runner:  float
    atr:            float
    quality_grade:  str = "B"
    filled_qty:     float = 0.0
    entry_time:     str = ""
    t1_done:        bool = False
    t2_done:        bool = False
    sl_order_id:    str = ""
    running_pnl:    float = 0.0


class CryptoExecutor:
    """
    Executes crypto orders on Alpaca. Notional-based (buy $X of BTC).
    Thread-safe for use in a background crypto loop.
    """

    POLL_INTERVAL  = 2    # seconds between order status polls
    FILL_WAIT      = 15   # seconds to wait for market order fill

    def __init__(self, live_enabled: bool = False):
        self.live_enabled    = live_enabled
        self.positions: Dict[str, CryptoPosition] = {}
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._consecutive_losses: int = 0
        self._paused_until: Optional[float] = None
        self._daily_capital: float = 1000.0   # updated by engine at start of each day

    def set_daily_capital(self, capital: float) -> None:
        """Called by CryptoEngine at daily reset with actual account capital."""
        self._daily_capital = max(capital, 50.0)

    def _get_trading_client(self):
        try:
            from alpaca.trading.client import TradingClient
            api_key    = os.getenv("ALPACA_API_KEY", "")
            api_secret = os.getenv("ALPACA_SECRET_KEY", "")
            paper      = os.getenv("ALPACA_BASE_URL", "paper").lower().find("paper") >= 0
            return TradingClient(api_key, api_secret, paper=paper)
        except Exception as e:
            logger.error(f"TradingClient init: {e}")
            return None

    def _alpaca_symbol(self, symbol: str) -> str:
        """Convert 'BTC/USD' → 'BTCUSD' for Alpaca order API."""
        return symbol.replace("/", "")

    def is_paused(self) -> bool:
        if self._paused_until and _time.monotonic() < self._paused_until:
            return True
        self._paused_until = None
        return False

    def can_trade(self, symbol: str) -> tuple:
        """Returns (allowed: bool, reason: str)."""
        import crypto_config as ccfg

        if self.is_paused():
            secs_left = int(self._paused_until - _time.monotonic())
            return False, f"Paused after losses ({secs_left}s remaining)"

        daily_loss_limit = self._daily_capital * ccfg.CRYPTO_DAILY_LOSS_PCT / 100
        if self._daily_pnl <= -daily_loss_limit:
            return False, f"Daily loss limit hit ({ccfg.CRYPTO_DAILY_LOSS_PCT}%)"

        if len(self.positions) >= ccfg.CRYPTO_MAX_POSITIONS:
            return False, f"Max positions ({ccfg.CRYPTO_MAX_POSITIONS}) reached"

        if self._daily_trades >= ccfg.CRYPTO_MAX_TRADES_DAY:
            return False, f"Max daily trades ({ccfg.CRYPTO_MAX_TRADES_DAY}) reached"

        if symbol in self.positions:
            return False, f"Already have position in {symbol}"

        return True, "ok"

    def place_entry(self, signal) -> CryptoOrderResult:
        """
        Place a crypto entry order (notional-based).
        signal: CryptoSignal object with notional_usd, entry_price, etc.
        """
        allowed, reason = self.can_trade(signal.symbol)
        if not allowed:
            logger.info(f"[CRYPTO] Trade blocked: {signal.symbol} — {reason}")
            return CryptoOrderResult(False, message=reason)

        alpaca_sym = self._alpaca_symbol(signal.symbol)
        notional   = signal.notional_usd

        if not self.live_enabled:
            # Paper: simulate fill at current price
            fill_qty = round(notional / signal.entry_price, 8) if signal.entry_price > 0 else 0.0
            logger.info(
                f"[{format_ist_timestamp()}] PAPER CRYPTO {signal.direction} {signal.symbol} "
                f"notional=${notional:.2f} @ ${signal.entry_price:,.2f} "
                f"qty={fill_qty:.6f} | {signal.rationale[:60]}"
            )
            result = CryptoOrderResult(
                success=True, fill_price=signal.entry_price,
                filled_qty=fill_qty, notional=notional, message="Paper fill"
            )
            self._register_position(signal, result)
            return result

        # Live order
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            client = self._get_trading_client()
            if not client:
                return CryptoOrderResult(False, message="Trading client unavailable")

            side = OrderSide.BUY if signal.direction == "LONG" else OrderSide.SELL

            req = MarketOrderRequest(
                symbol        = alpaca_sym,
                notional      = round(notional, 2),
                side          = side,
                time_in_force = TimeInForce.GTC,   # GTC works 24/7 for crypto
            )
            order = client.submit_order(req)
            order_id = str(order.id)

            # Poll for fill
            fill_p, fill_q = self._wait_for_fill(order_id, client)
            if fill_p <= 0:
                fill_p = signal.entry_price
                fill_q = round(notional / fill_p, 8)

            logger.info(
                f"[{format_ist_timestamp()}] ✅ CRYPTO FILLED: {signal.direction} "
                f"{signal.symbol} notional=${notional:.2f} @ ${fill_p:,.4f} "
                f"qty={fill_q:.6f} order_id={order_id}"
            )

            result = CryptoOrderResult(
                success=True, order_id=order_id, fill_price=fill_p,
                filled_qty=fill_q, notional=notional, message="Live fill"
            )
            self._register_position(signal, result)
            return result

        except Exception as e:
            logger.error(f"[CRYPTO] place_entry({signal.symbol}): {e}")
            return CryptoOrderResult(False, message=str(e))

    def place_exit(self, symbol: str, reason: str = "EXIT") -> CryptoOrderResult:
        """Close an entire crypto position by symbol."""
        pos = self.positions.get(symbol)
        if not pos:
            return CryptoOrderResult(False, message=f"No position in {symbol}")

        alpaca_sym = self._alpaca_symbol(symbol)

        if not self.live_enabled:
            # Paper: get current price from data module
            from crypto_data import get_crypto_quote
            q = get_crypto_quote(symbol)
            exit_price = q.get("ltp", pos.entry_price)
            if exit_price <= 0:
                exit_price = pos.entry_price

            pnl = self._compute_pnl(pos, exit_price)
            self._record_trade_close(symbol, pnl, exit_price, reason)
            logger.info(
                f"[{format_ist_timestamp()}] PAPER CRYPTO EXIT: {symbol} "
                f"@ ${exit_price:,.4f} P&L=${pnl:+.2f} reason={reason}"
            )
            return CryptoOrderResult(
                success=True, fill_price=exit_price,
                filled_qty=pos.filled_qty, notional=pos.notional_usd,
                message=f"Paper exit ({reason})"
            )

        try:
            from alpaca.trading.client import TradingClient

            client = self._get_trading_client()
            if not client:
                return CryptoOrderResult(False, message="Trading client unavailable")

            # Close entire position by symbol (Alpaca close_position endpoint)
            client.close_position(alpaca_sym)

            # Get last price for P&L
            from crypto_data import get_crypto_quote
            q = get_crypto_quote(symbol)
            exit_price = q.get("ltp", pos.entry_price)

            pnl = self._compute_pnl(pos, exit_price)
            self._record_trade_close(symbol, pnl, exit_price, reason)
            logger.info(
                f"[{format_ist_timestamp()}] CRYPTO EXIT: {symbol} "
                f"@ ${exit_price:,.4f} P&L=${pnl:+.2f} reason={reason}"
            )
            return CryptoOrderResult(success=True, fill_price=exit_price,
                                     filled_qty=pos.filled_qty, message=reason)

        except Exception as e:
            logger.error(f"[CRYPTO] place_exit({symbol}): {e}")
            return CryptoOrderResult(False, message=str(e))

    def close_all(self, reason: str = "EOD") -> None:
        """Close all open crypto positions."""
        syms = list(self.positions.keys())
        for sym in syms:
            try:
                self.place_exit(sym, reason=reason)
            except Exception as e:
                logger.error(f"close_all: {sym} failed: {e}")

    def update_positions(self) -> List[str]:
        """
        Check all open positions against current prices.
        Execute T1/T2 exits, trailing stops, and stop-loss hits.
        Returns list of symbols that were closed.
        """
        import crypto_config as ccfg
        closed = []

        for symbol, pos in list(self.positions.items()):
            try:
                from crypto_data import get_crypto_quote
                q = get_crypto_quote(symbol)
                ltp = q.get("ltp", 0.0)
                if ltp <= 0:
                    continue

                # ── Stop loss hit ─────────────────────────────────────────
                sl_hit = (
                    (pos.direction == "LONG"  and ltp <= pos.stop_loss) or
                    (pos.direction == "SHORT" and ltp >= pos.stop_loss)
                )
                if sl_hit:
                    self.place_exit(symbol, reason="STOP_LOSS")
                    closed.append(symbol)
                    continue

                # ── T1 exit (35% of position) ─────────────────────────────
                if not pos.t1_done:
                    t1_hit = (
                        (pos.direction == "LONG"  and ltp >= pos.target_1) or
                        (pos.direction == "SHORT" and ltp <= pos.target_1)
                    )
                    if t1_hit:
                        pos.t1_done = True
                        # Partial exit: move SL to breakeven
                        pos.stop_loss = pos.entry_price
                        logger.info(
                            f"[{format_ist_timestamp()}] CRYPTO T1 HIT: {symbol} "
                            f"@ ${ltp:,.4f} | SL moved to breakeven ${pos.entry_price:,.4f}"
                        )
                        # In live mode, modify broker stop order
                        if self.live_enabled:
                            try:
                                self._place_stop_order(symbol, pos.filled_qty, pos.entry_price, pos.direction)
                            except Exception:
                                pass
                        continue

                # ── T2 exit (close runner or full close) ──────────────────
                if pos.t1_done and not pos.t2_done:
                    t2_hit = (
                        (pos.direction == "LONG"  and ltp >= pos.target_2) or
                        (pos.direction == "SHORT" and ltp <= pos.target_2)
                    )
                    if t2_hit:
                        pos.t2_done = True
                        # Update trailing stop for the runner
                        if pos.direction == "LONG":
                            pos.stop_loss = round(ltp - ccfg.CRYPTO_TRAIL_MULT * pos.atr, 6)
                        else:
                            pos.stop_loss = round(ltp + ccfg.CRYPTO_TRAIL_MULT * pos.atr, 6)
                        logger.info(
                            f"[{format_ist_timestamp()}] CRYPTO T2 HIT: {symbol} "
                            f"@ ${ltp:,.4f} | Trail SL: ${pos.stop_loss:,.4f}"
                        )
                        continue

                # ── Trailing stop (after T2) ──────────────────────────────
                if pos.t2_done:
                    if pos.direction == "LONG":
                        new_sl = round(ltp - ccfg.CRYPTO_TRAIL_MULT * pos.atr, 6)
                        if new_sl > pos.stop_loss:
                            pos.stop_loss = new_sl
                    else:
                        new_sl = round(ltp + ccfg.CRYPTO_TRAIL_MULT * pos.atr, 6)
                        if new_sl < pos.stop_loss:
                            pos.stop_loss = new_sl

                # ── Breakeven move after small gain ───────────────────────
                if not pos.t1_done:
                    gain_pct = abs(ltp - pos.entry_price) / max(pos.entry_price, 1) * 100
                    if gain_pct >= ccfg.CRYPTO_BREAKEVEN_PCT:
                        if pos.direction == "LONG" and pos.stop_loss < pos.entry_price:
                            pos.stop_loss = pos.entry_price
                        elif pos.direction == "SHORT" and pos.stop_loss > pos.entry_price:
                            pos.stop_loss = pos.entry_price

                # ── Runner target hit ─────────────────────────────────────
                if pos.t2_done:
                    runner_hit = (
                        (pos.direction == "LONG"  and ltp >= pos.target_runner) or
                        (pos.direction == "SHORT" and ltp <= pos.target_runner)
                    )
                    if runner_hit:
                        self.place_exit(symbol, reason="RUNNER_TARGET")
                        closed.append(symbol)
                        continue

            except Exception as e:
                logger.warning(f"update_positions({symbol}): {e}")

        return closed

    def reset_daily(self) -> None:
        """Reset daily counters (called at UTC midnight for 24/7 crypto)."""
        self._daily_pnl    = 0.0
        self._daily_trades = 0
        logger.info(f"[{format_ist_timestamp()}] Crypto daily counters reset")

    def get_status(self) -> Dict:
        """Return current state summary."""
        return {
            "positions":          len(self.positions),
            "daily_pnl":          round(self._daily_pnl, 2),
            "daily_trades":       self._daily_trades,
            "consecutive_losses": self._consecutive_losses,
            "paused":             self.is_paused(),
        }

    # ─────────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────────────

    def _register_position(self, signal, result: CryptoOrderResult) -> None:
        """Register a filled order as an open position."""
        pos = CryptoPosition(
            symbol        = signal.symbol,
            direction     = signal.direction,
            notional_usd  = result.notional,
            entry_price   = result.fill_price,
            stop_loss     = signal.stop_loss,
            target_1      = signal.target_1,
            target_2      = signal.target_2,
            target_runner = signal.target_runner,
            atr           = signal.atr,
            quality_grade = signal.quality_grade,
            filled_qty    = result.filled_qty,
            entry_time    = format_ist_timestamp(),
        )
        self.positions[signal.symbol] = pos
        self._daily_trades += 1

        # Place broker-side stop order in live mode
        if self.live_enabled and result.filled_qty > 0:
            try:
                self._place_stop_order(signal.symbol, result.filled_qty,
                                       signal.stop_loss, signal.direction)
            except Exception as e:
                logger.warning(f"Crypto stop order failed {signal.symbol}: {e}")

    def _compute_pnl(self, pos: CryptoPosition, exit_price: float) -> float:
        if pos.filled_qty > 0:
            if pos.direction == "LONG":
                return round((exit_price - pos.entry_price) * pos.filled_qty, 2)
            else:
                return round((pos.entry_price - exit_price) * pos.filled_qty, 2)
        if pos.notional_usd > 0 and pos.entry_price > 0:
            pct = (exit_price - pos.entry_price) / pos.entry_price
            if pos.direction == "SHORT":
                pct = -pct
            return round(pos.notional_usd * pct, 2)
        return 0.0

    def _record_trade_close(self, symbol: str, pnl: float,
                             exit_price: float, reason: str) -> None:
        """Update counters after closing a position."""
        import crypto_config as ccfg
        self._daily_pnl += pnl
        pos = self.positions.pop(symbol, None)

        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= ccfg.CRYPTO_CONSEC_LOSS_LIMIT:
                pause_secs = ccfg.CRYPTO_PAUSE_AFTER_LOSSES_MIN * 60
                self._paused_until = _time.monotonic() + pause_secs
                logger.warning(
                    f"[{format_ist_timestamp()}] CRYPTO: {self._consecutive_losses} consecutive losses "
                    f"— pausing {ccfg.CRYPTO_PAUSE_AFTER_LOSSES_MIN} min"
                )
        else:
            self._consecutive_losses = 0

    def _wait_for_fill(self, order_id: str, client) -> tuple:
        """Poll until filled. Returns (fill_price, fill_qty)."""
        deadline = _time.monotonic() + self.FILL_WAIT
        while _time.monotonic() < deadline:
            try:
                order = client.get_order_by_id(order_id)
                status = str(getattr(order, "status", "")).lower()
                if status in ("filled", "partially_filled"):
                    fp = float(getattr(order, "filled_avg_price", 0) or 0)
                    fq = float(getattr(order, "filled_qty", 0) or 0)
                    if fp > 0:
                        return fp, fq
            except Exception as e:
                logger.debug(f"poll fill {order_id}: {e}")
            _time.sleep(self.POLL_INTERVAL)
        return 0.0, 0.0

    def _place_stop_order(self, symbol: str, qty: float,
                           stop_price: float, direction: str) -> str:
        """Place a broker-side stop order for crypto position."""
        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            client = self._get_trading_client()
            if not client:
                return ""

            alpaca_sym = self._alpaca_symbol(symbol)
            stop_side  = OrderSide.SELL if direction == "LONG" else OrderSide.BUY

            req = StopOrderRequest(
                symbol        = alpaca_sym,
                qty           = round(qty, 8),
                side          = stop_side,
                time_in_force = TimeInForce.GTC,
                stop_price    = round(stop_price, 4),
            )
            order = client.submit_order(req)
            order_id = str(order.id)
            logger.debug(f"Crypto stop order: {symbol} stop=${stop_price:,.4f} id={order_id}")
            return order_id
        except Exception as e:
            logger.warning(f"_place_stop_order({symbol}): {e}")
            return ""


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_crypto_executor: Optional[CryptoExecutor] = None


def get_crypto_executor(live_enabled: bool = False) -> CryptoExecutor:
    global _crypto_executor
    if _crypto_executor is None:
        _crypto_executor = CryptoExecutor(live_enabled=live_enabled)
        logger.info(
            f"[{format_ist_timestamp()}] CryptoExecutor ready "
            f"({'LIVE' if live_enabled else 'PAPER'})"
        )
    return _crypto_executor
