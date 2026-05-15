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
from typing import Dict, List, Optional

from auth_alpaca import get_auth_manager
from data_fetch_alpaca import get_data_fetcher
from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)


class SlippageModel:
    """
    Estimates realistic fill price vs theoretical mid-price.

    For live paper-to-live transition accuracy: models spread cost + market impact.
    Used to estimate P&L more accurately during paper trading.
    """

    # Typical half-spread as % of price by liquidity tier
    SPREAD_TIERS = {
        "mega":   0.01,   # AAPL, MSFT, NVDA — 1 cent spread on $100+ stock = ~0.01%
        "large":  0.03,   # SPY, QQQ components — ~3bps
        "mid":    0.08,   # Mid-caps — ~8bps
        "small":  0.20,   # Small-caps — ~20bps
    }

    MEGA_CAP = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK.B", "SPY", "QQQ"}
    LARGE_CAP = {"AMD", "NFLX", "COIN", "UBER", "SHOP", "PLTR", "JPM", "GS", "XOM", "CVX",
                 "BA", "CAT", "XLK", "XLF", "XLE", "XLY", "XLI"}

    def estimate_slippage_pct(self, symbol: str, quantity: int, price: float,
                               direction: str = "BUY") -> float:
        """
        Returns estimated total slippage as % of trade value.

        Components:
          1. Half-spread: cost of crossing bid-ask
          2. Market impact: price movement from our own order (scales with size)
        """
        if price <= 0 or quantity <= 0:
            return 0.0

        # Spread component
        if symbol in self.MEGA_CAP:
            tier = "mega"
        elif symbol in self.LARGE_CAP:
            tier = "large"
        else:
            tier = "mid"

        half_spread_pct = self.SPREAD_TIERS[tier]

        # Market impact component: sqrt model — small orders have little impact
        trade_value = quantity * price
        # Assume 1M shares ADV for mega, 500K for large, 200K for mid
        adv_shares = {"mega": 5_000_000, "large": 1_000_000, "mid": 300_000}[tier]
        participation = quantity / adv_shares
        impact_pct = 0.10 * (participation ** 0.5) * 100  # Almgren-Chriss simplified

        total_slippage_pct = half_spread_pct + impact_pct
        return round(min(total_slippage_pct, 0.5), 4)  # cap at 50bps

    def adjust_expected_pnl(self, symbol: str, quantity: int, entry: float,
                             target: float, sl: float, direction: str = "LONG") -> Dict:
        """
        Returns realistic P&L estimates accounting for slippage on entry and exit.
        """
        entry_slip = self.estimate_slippage_pct(symbol, quantity, entry, "BUY" if direction == "LONG" else "SELL")
        exit_slip  = self.estimate_slippage_pct(symbol, quantity, target, "SELL" if direction == "LONG" else "BUY")
        sl_slip    = self.estimate_slippage_pct(symbol, quantity, sl, "SELL" if direction == "LONG" else "BUY")

        slip_cost_entry = entry * entry_slip / 100
        slip_cost_exit  = target * exit_slip / 100
        slip_cost_sl    = sl * sl_slip / 100

        if direction == "LONG":
            real_entry  = entry + slip_cost_entry
            real_target = target - slip_cost_exit
            real_sl     = sl + slip_cost_sl
        else:
            real_entry  = entry - slip_cost_entry
            real_target = target + slip_cost_exit
            real_sl     = sl - slip_cost_sl

        gross_profit = abs(real_target - real_entry) * quantity
        gross_loss   = abs(real_entry - real_sl) * quantity

        return {
            "real_entry":     round(real_entry, 4),
            "real_target":    round(real_target, 4),
            "real_sl":        round(real_sl, 4),
            "gross_profit":   round(gross_profit, 2),
            "gross_loss":     round(gross_loss, 2),
            "rr_after_slip":  round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
            "total_slip_pct": round(entry_slip + exit_slip, 4),
        }


# Singleton
_slippage_model: Optional["SlippageModel"] = None

def get_slippage_model() -> "SlippageModel":
    global _slippage_model
    if _slippage_model is None:
        _slippage_model = SlippageModel()
    return _slippage_model


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

    LIMIT_WAIT_SECONDS  = 5     # Wait this long for limit fill before widening
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
                f"@ ${signal.entry_price:.2f} (live_enabled=False)"
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
            # High-conviction (90+): use market order immediately for speed
            if getattr(signal, 'signal_score', 0) >= 90 and attempt == 0:
                order_type  = "MARKET"
                limit_price = None
            elif attempt >= 2:
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

    def place_bracket_order(self, signal, quantity: int) -> "OrderResult":
        """
        Place bracket order: entry + SL + TP simultaneously.
        Used for high-score (≥90) setups — fastest institutional execution.
        SL = signal.stop_loss, TP = signal.target_1 (1:2 R:R first exit).
        """
        symbol    = signal.symbol
        direction = signal.direction
        sl_price  = round(signal.stop_loss, 2)
        tp_price  = round(signal.target_1,  2)

        if not self.live_enabled:
            logger.info(
                f"[{format_ist_timestamp()}] PAPER bracket: {direction} {symbol} "
                f"qty={quantity} SL=${sl_price:.2f} TP=${tp_price:.2f}"
            )
            from dataclasses import dataclass
            result = OrderResult(True, message="Paper bracket order")
            result.order_id   = f"PAPER-BKT-{symbol}"
            result.fill_price = signal.entry_price
            return result

        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import (
                MarketOrderRequest, LimitOrderRequest,
                TakeProfitRequest, StopLossRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
            import os

            api_key    = os.getenv("ALPACA_API_KEY", "")
            api_secret = os.getenv("ALPACA_SECRET_KEY", "")
            paper      = os.getenv("ALPACA_PAPER", "true").lower() != "false"
            trading_client = TradingClient(api_key, api_secret, paper=paper)

            side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL

            # Bracket: market entry with automatic SL and TP
            req = MarketOrderRequest(
                symbol         = symbol,
                qty            = quantity,
                side           = side,
                time_in_force  = TimeInForce.DAY,
                order_class    = OrderClass.BRACKET,
                take_profit    = TakeProfitRequest(limit_price=tp_price),
                stop_loss      = StopLossRequest(
                    stop_price  = sl_price,
                    limit_price = round(sl_price * (0.995 if direction == "LONG" else 1.005), 2),
                ),
            )
            order = trading_client.submit_order(req)
            order_id = str(order.id)

            # Wait for fill
            filled = self._wait_for_fill(order_id, wait=15)
            fill_p = filled if filled else signal.entry_price

            logger.info(
                f"[{format_ist_timestamp()}] ✅ BRACKET FILLED: {symbol} {direction} "
                f"qty={quantity} @ ${fill_p:.2f} | SL=${sl_price:.2f} TP=${tp_price:.2f}"
            )
            result = OrderResult(True, message="Bracket order filled")
            result.order_id   = order_id
            result.fill_price = fill_p
            result.order_type = "BRACKET"
            return result

        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Bracket order failed {symbol}: {e} — falling back to standard")
            return OrderResult(False, message=f"Bracket failed: {e}")

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
