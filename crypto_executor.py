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

import numpy as np

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
    remaining_qty:  float = 0.0   # qty left after partial exits
    entry_time:     str = ""
    t1_done:        bool = False
    t2_done:        bool = False
    sl_order_id:    str = ""
    running_pnl:    float = 0.0   # locked-in P&L from partial exits


class WinRateTracker:
    """Tracks recent trade outcomes for Kelly Criterion dynamic position sizing."""

    def __init__(self, window: int = 20):
        self._window   = window
        self._outcomes: list = []  # True=win, False=loss
        self._pnls:     list = []

    def record(self, pnl: float) -> None:
        self._outcomes.append(pnl > 0)
        self._pnls.append(pnl)
        if len(self._outcomes) > self._window:
            self._outcomes.pop(0)
            self._pnls.pop(0)

    def kelly_fraction(self) -> float:
        """Half-Kelly multiplier clamped to [0.4, 1.5]. Returns 1.0 with < 5 trades."""
        n = len(self._outcomes)
        if n < 5:
            return 1.0
        wins   = [p for p in self._pnls if p > 0]
        losses = [abs(p) for p in self._pnls if p < 0]
        if not wins or not losses:
            return 1.0 if not wins else 1.3
        win_rate = sum(self._outcomes) / n
        b        = np.mean(wins) / np.mean(losses)
        q        = 1.0 - win_rate
        kelly    = (b * win_rate - q) / b
        return max(0.4, min(1.5, kelly / 2))  # half-Kelly, clamped

    def win_rate(self) -> float:
        if not self._outcomes:
            return 0.55  # default assumption
        return sum(self._outcomes) / len(self._outcomes)

    def trade_count(self) -> int:
        return len(self._outcomes)


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
        self._pause_until_utc: Optional[float] = None  # wall-clock for restart survival
        self._daily_capital: float = 1000.0   # updated by engine at start of each day
        self._win_tracker   = WinRateTracker()

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
        import time as _t
        # Runtime pause (resets on restart — normal operation)
        if self._paused_until and _time.monotonic() < self._paused_until:
            return True
        self._paused_until = None
        # Wall-clock pause (survives restart — set after consecutive losses)
        if self._pause_until_utc and _t.time() < self._pause_until_utc:
            return True
        self._pause_until_utc = None
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

        # Session gate — double-check even if signal was generated in wrong session
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import crypto_config as ccfg
        UTC = ZoneInfo("UTC")
        hour = datetime.now(UTC).hour
        if 21 <= hour or hour < 7:  # US_NIGHT (21-24) + ASIA (0-7)
            return False, f"Session blocked: hour {hour} UTC is outside EU_MORNING/US_PEAK"

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

        # HARD NOTIONAL CAP — belt-and-suspenders: reject oversized orders even if
        # _calculate_notional() had a bug. This catches any code path that bypasses
        # the config cap and is the last line of defence before an order hits the broker.
        hard_cap = ccfg.CRYPTO_MAX_NOTIONAL_USD * 1.1  # allow 10% rounding only, never 2×
        if notional > hard_cap:
            logger.error(
                f"[CRYPTO] HARD BLOCK: {signal.symbol} notional=${notional:.0f} > "
                f"hard cap ${hard_cap:.0f} — order rejected to prevent oversized loss"
            )
            return CryptoOrderResult(False, message=f"Notional ${notional:.0f} exceeds hard cap")

        notional = min(notional, ccfg.CRYPTO_MAX_NOTIONAL_USD)  # enforce exact cap

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

            terminal_pnl = self._compute_pnl(pos, exit_price)  # uses remaining_qty
            total_pnl    = round(pos.running_pnl + terminal_pnl, 2)
            self._record_trade_close(symbol, total_pnl, exit_price, reason)
            logger.info(
                f"[{format_ist_timestamp()}] PAPER CRYPTO EXIT: {symbol} "
                f"@ ${exit_price:,.4f} terminal=${terminal_pnl:+.2f} "
                f"locked=${pos.running_pnl:+.2f} total=${total_pnl:+.2f} reason={reason}"
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

            terminal_pnl = self._compute_pnl(pos, exit_price)  # uses remaining_qty
            total_pnl    = round(pos.running_pnl + terminal_pnl, 2)
            self._record_trade_close(symbol, total_pnl, exit_price, reason)
            logger.info(
                f"[{format_ist_timestamp()}] CRYPTO EXIT: {symbol} "
                f"@ ${exit_price:,.4f} terminal=${terminal_pnl:+.2f} "
                f"locked=${pos.running_pnl:+.2f} total=${total_pnl:+.2f} reason={reason}"
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

                # ── T1: sell CRYPTO_T1_EXIT_PCT% of position ─────────────
                if not pos.t1_done:
                    t1_hit = (
                        (pos.direction == "LONG"  and ltp >= pos.target_1) or
                        (pos.direction == "SHORT" and ltp <= pos.target_1)
                    )
                    if t1_hit:
                        pos.t1_done = True
                        t1_frac = ccfg.CRYPTO_T1_EXIT_PCT / 100.0
                        self._partial_exit(symbol, t1_frac, ltp, "T1")
                        pos.stop_loss = pos.entry_price   # move to breakeven
                        if self.live_enabled:
                            try:
                                self._place_stop_order(symbol, pos.remaining_qty,
                                                       pos.entry_price, pos.direction)
                            except Exception:
                                pass
                        continue

                # ── T2: sell CRYPTO_T2_EXIT_PCT% of position ─────────────
                if pos.t1_done and not pos.t2_done:
                    t2_hit = (
                        (pos.direction == "LONG"  and ltp >= pos.target_2) or
                        (pos.direction == "SHORT" and ltp <= pos.target_2)
                    )
                    if t2_hit:
                        pos.t2_done = True
                        t2_frac = ccfg.CRYPTO_T2_EXIT_PCT / 100.0
                        self._partial_exit(symbol, t2_frac, ltp, "T2")
                        if pos.direction == "LONG":
                            pos.stop_loss = round(ltp - ccfg.CRYPTO_TRAIL_MULT * pos.atr, 6)
                        else:
                            pos.stop_loss = round(ltp + ccfg.CRYPTO_TRAIL_MULT * pos.atr, 6)
                        continue

                # ── Trailing stop (after T2, on runner portion) ───────────
                if pos.t2_done:
                    if pos.direction == "LONG":
                        new_sl = round(ltp - ccfg.CRYPTO_TRAIL_MULT * pos.atr, 6)
                        if new_sl > pos.stop_loss:
                            pos.stop_loss = new_sl
                    else:
                        new_sl = round(ltp + ccfg.CRYPTO_TRAIL_MULT * pos.atr, 6)
                        if new_sl < pos.stop_loss:
                            pos.stop_loss = new_sl

                # ── Breakeven move before T1 ──────────────────────────────
                if not pos.t1_done:
                    gain_pct = abs(ltp - pos.entry_price) / max(pos.entry_price, 1) * 100
                    if gain_pct >= ccfg.CRYPTO_BREAKEVEN_PCT:
                        if pos.direction == "LONG" and pos.stop_loss < pos.entry_price:
                            pos.stop_loss = pos.entry_price
                        elif pos.direction == "SHORT" and pos.stop_loss > pos.entry_price:
                            pos.stop_loss = pos.entry_price

                # ── Runner target: close remaining position ───────────────
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

    def reconcile_broker_positions(self) -> int:
        """
        Sync in-memory positions with actual Alpaca broker state.
        Called at engine startup and after each daily reset to recover from restarts.

        - Oversized positions (> 5× notional cap) are closed immediately
        - Unknown positions get loaded with ATR-based stops so they're managed
        - Returns count of actions taken (loaded + closed)
        """
        import crypto_config as ccfg
        from crypto_data import get_crypto_positions

        try:
            broker_positions = get_crypto_positions()
        except Exception as e:
            logger.error(f"reconcile: cannot fetch broker positions: {e}")
            return 0

        if not broker_positions:
            return 0

        loaded  = 0
        closed  = 0
        cap     = ccfg.CRYPTO_MAX_NOTIONAL_USD  # $500

        for bp in broker_positions:
            raw_sym    = bp.get("symbol", "")
            # Convert BTCUSD → BTC/USD  (Alpaca stores without slash)
            if "/" not in raw_sym and len(raw_sym) >= 6:
                sym = raw_sym[:3] + "/" + raw_sym[3:]
            else:
                sym = raw_sym

            market_val  = abs(bp.get("market_value", 0.0))
            entry_price = bp.get("entry", 0.0)
            qty         = abs(bp.get("qty", 0.0))
            side        = bp.get("side", "long").lower()
            direction   = "LONG" if side == "long" else "SHORT"

            # ── Close positions that are way above our cap (pre-fix leftovers) ──
            if market_val > cap * 5:  # > $2,500 = definitely from old code
                logger.warning(
                    f"[CRYPTO] reconcile: OVERSIZED {sym} MV=${market_val:.0f} "
                    f"(cap=${cap:.0f}) — closing immediately"
                )
                try:
                    alpaca_sym = self._alpaca_symbol(sym)
                    client = self._get_trading_client()
                    if client:
                        client.close_position(alpaca_sym)
                        # Remove from in-memory tracking if present
                        self.positions.pop(sym, None)
                    closed += 1
                    logger.info(f"[CRYPTO] reconcile: closed oversized {sym}")
                except Exception as e:
                    logger.error(f"reconcile close_oversized({sym}): {e}")
                continue

            # ── Skip already tracked positions ─────────────────────────────────
            if sym in self.positions:
                continue

            # ── Skip if data is insufficient ───────────────────────────────────
            if entry_price <= 0 or qty <= 0:
                continue

            # ── Compute ATR-based stops for untracked position ─────────────────
            atr = entry_price * 0.015  # fallback: 1.5% of price
            try:
                from crypto_data import get_crypto_bars
                from crypto_signals import _compute_indicators
                df = get_crypto_bars(sym, "15Min", limit=50)
                if df is not None and len(df) >= 20:
                    ind = _compute_indicators(df)
                    if ind and ind.get("atr", 0) > 0:
                        atr = ind["atr"]
            except Exception:
                pass

            if direction == "LONG":
                sl     = round(entry_price - 1.5 * atr, 6)
                t1     = round(entry_price + 2.0 * atr, 6)
                t2     = round(entry_price + 4.0 * atr, 6)
                runner = round(entry_price + 7.0 * atr, 6)
            else:
                sl     = round(entry_price + 1.5 * atr, 6)
                t1     = round(entry_price - 2.0 * atr, 6)
                t2     = round(entry_price - 4.0 * atr, 6)
                runner = round(entry_price - 7.0 * atr, 6)

            # If current price is already past the stop-loss, close immediately
            try:
                from crypto_data import get_crypto_quote
                q = get_crypto_quote(sym)
                current_price = q.get("ltp", 0.0)
                if current_price > 0:
                    already_stopped = (
                        (direction == "LONG"  and current_price <= sl) or
                        (direction == "SHORT" and current_price >= sl)
                    )
                    if already_stopped:
                        logger.warning(
                            f"reconcile: {sym} already past SL (price=${current_price:.2f} SL=${sl:.2f}) — closing"
                        )
                        try:
                            alpaca_sym = self._alpaca_symbol(sym)
                            client = self._get_trading_client()
                            if client:
                                client.close_position(alpaca_sym)
                            closed += 1
                        except Exception as e:
                            logger.error(f"reconcile close_at_sl({sym}): {e}")
                        continue
            except Exception:
                pass

            pos = CryptoPosition(
                symbol        = sym,
                direction     = direction,
                notional_usd  = market_val,
                entry_price   = entry_price,
                stop_loss     = sl,
                target_1      = t1,
                target_2      = t2,
                target_runner = runner,
                atr           = atr,
                quality_grade = "B",
                filled_qty    = qty,
                remaining_qty = qty,
                entry_time    = "RECOVERED",
            )
            self.positions[sym] = pos
            loaded += 1
            logger.info(
                f"[CRYPTO] reconcile: loaded {sym} {direction} @ ${entry_price:.2f} "
                f"qty={qty:.6f} MV=${market_val:.0f} SL=${sl:.2f}"
            )

        if loaded or closed:
            logger.info(
                f"[CRYPTO] reconcile done: {loaded} loaded, {closed} oversized closed"
            )
        return loaded + closed

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
            remaining_qty = result.filled_qty,
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

    def _compute_pnl(self, pos: CryptoPosition, exit_price: float,
                      qty: Optional[float] = None) -> float:
        """Compute P&L for a given qty (defaults to remaining_qty or filled_qty)."""
        use_qty = qty if qty is not None else (
            pos.remaining_qty if pos.remaining_qty > 0 else pos.filled_qty
        )
        if use_qty > 0:
            if pos.direction == "LONG":
                return round((exit_price - pos.entry_price) * use_qty, 2)
            else:
                return round((pos.entry_price - exit_price) * use_qty, 2)
        if pos.notional_usd > 0 and pos.entry_price > 0:
            pct = (exit_price - pos.entry_price) / pos.entry_price
            if pos.direction == "SHORT":
                pct = -pct
            return round(pos.notional_usd * pct, 2)
        return 0.0

    def _partial_exit(self, symbol: str, fraction: float, ltp: float,
                       reason: str) -> float:
        """
        Close `fraction` of filled_qty. Locks in P&L, shrinks remaining_qty.
        Returns the realised P&L from this partial. Does NOT remove position.
        """
        pos = self.positions.get(symbol)
        if not pos or pos.filled_qty <= 0:
            return 0.0

        sell_qty = round(pos.filled_qty * fraction, 8)
        sell_qty = min(sell_qty, pos.remaining_qty)
        if sell_qty <= 0:
            return 0.0

        fill_price = ltp  # updated if live fill comes back

        if self.live_enabled:
            try:
                from alpaca.trading.requests import MarketOrderRequest
                from alpaca.trading.enums import OrderSide, TimeInForce
                client = self._get_trading_client()
                if client:
                    side = OrderSide.SELL if pos.direction == "LONG" else OrderSide.BUY
                    req = MarketOrderRequest(
                        symbol        = self._alpaca_symbol(symbol),
                        qty           = sell_qty,
                        side          = side,
                        time_in_force = TimeInForce.GTC,
                    )
                    order = client.submit_order(req)
                    fp, _ = self._wait_for_fill(str(order.id), client)
                    if fp > 0:
                        fill_price = fp
            except Exception as e:
                logger.warning(f"_partial_exit({symbol}, {reason}): {e}")

        pnl = self._compute_pnl(pos, fill_price, qty=sell_qty)
        pos.remaining_qty = max(0.0, round(pos.remaining_qty - sell_qty, 8))
        pos.running_pnl   = round(pos.running_pnl + pnl, 2)

        logger.info(
            f"[{format_ist_timestamp()}] CRYPTO {reason}: {symbol} "
            f"sold {sell_qty:.6f} @ ${fill_price:,.4f} pnl=${pnl:+.2f} "
            f"locked=${pos.running_pnl:+.2f} remaining={pos.remaining_qty:.6f}"
        )
        return pnl

    def get_kelly_multiplier(self) -> float:
        """Return Kelly Criterion half-Kelly size multiplier based on recent win rate."""
        return self._win_tracker.kelly_fraction()

    def check_correlation_limit(self, direction: str) -> tuple:
        """
        Check if opening another position in the given direction would breach
        the correlation limit (max 2 concurrent LONG positions).

        Returns (allowed: bool, reason: str)
        """
        if direction == "LONG":
            long_positions = [
                sym for sym, pos in self.positions.items()
                if pos.direction == "LONG"
            ]
            if len(long_positions) >= 2:
                return False, f"Correlation limit: already {len(long_positions)} LONG positions"
        return True, "ok"

    def can_trade_direction(self, symbol: str, direction: str) -> tuple:
        """
        Combines can_trade() + check_correlation_limit().
        Returns (allowed: bool, reason: str).
        """
        allowed, reason = self.can_trade(symbol)
        if not allowed:
            return allowed, reason
        return self.check_correlation_limit(direction)

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
                import time as _t
                self._pause_until_utc = _t.time() + pause_secs  # survives restart
                logger.warning(
                    f"[{format_ist_timestamp()}] CRYPTO: {self._consecutive_losses} consecutive losses "
                    f"— pausing {ccfg.CRYPTO_PAUSE_AFTER_LOSSES_MIN} min"
                )
        else:
            self._consecutive_losses = 0

        self._win_tracker.record(pnl)

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
