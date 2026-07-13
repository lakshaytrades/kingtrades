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
    quantity:   float = 0.0
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
        self._day_trade_count = 0
        self._day_trade_date  = ""   # "YYYY-MM-DD" — resets each trading day
        self._rl_agent        = None  # set via set_learning_callbacks()
        self._ml_ensemble_mod = None  # module ref for record_trade_outcome_ensemble
        self._last_known_account = None   # cache for get_account_safe() failover
        self._last_account_fetch: float = 0.0  # monotonic timestamp of last successful fetch

    def set_learning_callbacks(self, rl_agent=None, ml_ensemble_mod=None):
        """Wire RL agent and ML ensemble for post-trade learning."""
        self._rl_agent        = rl_agent
        self._ml_ensemble_mod = ml_ensemble_mod

    # ─────────────────────────────────────────────────────────────────────
    # BROKER ACCOUNT — RESILIENT FETCH WITH EXPONENTIAL BACKOFF
    # ─────────────────────────────────────────────────────────────────────

    def get_account_safe(self) -> dict:
        """
        Fetch Alpaca account info with 4-retry exponential backoff (2s, 4s, 8s, 16s).
        On all retries failing, returns cached _last_known_account if available,
        otherwise returns a zero-balance sentinel with CRITICAL log.
        Caches every successful fetch for failover.
        """
        delays = [2, 4, 8, 16]
        last_err = None
        for attempt, delay in enumerate(delays):
            try:
                trading_client = self._auth.get_trading_client()
                account = trading_client.get_account()
                result = {
                    "portfolio_value": str(getattr(account, "portfolio_value", "0") or "0"),
                    "buying_power":    str(getattr(account, "buying_power",    "0") or "0"),
                    "cash":            str(getattr(account, "cash",            "0") or "0"),
                }
                self._last_known_account = result
                self._last_account_fetch = _time.monotonic()
                return result
            except Exception as e:
                last_err = e
                if attempt < len(delays) - 1:
                    logger.warning(
                        f"[get_account_safe] Attempt {attempt + 1}/{len(delays)} failed: {e} "
                        f"— retrying in {delay}s"
                    )
                    _time.sleep(delay)

        # All retries exhausted
        if self._last_known_account is not None:
            logger.warning(
                f"[get_account_safe] All retries failed ({last_err}) "
                f"— returning cached account (age={_time.monotonic() - self._last_account_fetch:.0f}s)"
            )
            return self._last_known_account

        logger.critical(
            f"[get_account_safe] All retries failed AND no cached account available: {last_err} "
            "— returning zero-balance sentinel. Check Alpaca connectivity immediately!"
        )
        return {"portfolio_value": "0", "buying_power": "0", "cash": "0"}

    # ─────────────────────────────────────────────────────────────────────
    # POSITION RECONCILIATION
    # ─────────────────────────────────────────────────────────────────────

    def reconcile_positions_with_broker(self, risk_manager) -> dict:
        """
        Compare Alpaca's live positions vs bot's in-memory positions.
        Fixes mismatches to prevent ghost positions and missed fills.
        Called every 5 minutes from main loop.
        """
        result = {"added": [], "removed": [], "qty_fixed": []}
        try:
            trading_client = self._auth.get_trading_client()
            broker_positions = {str(p.symbol): p for p in trading_client.get_all_positions()}
        except Exception as e:
            logger.warning(f"[RECONCILE] Alpaca get_all_positions() failed: {e}")
            return result

        bot_symbols = set(risk_manager.positions.keys()) if hasattr(risk_manager, 'positions') else set()
        broker_symbols = set(broker_positions.keys())

        # In Alpaca but NOT in bot memory → add ghost position
        for sym in broker_symbols - bot_symbols:
            try:
                pos = broker_positions[sym]
                qty = int(float(pos.qty))
                price = float(pos.avg_entry_price)
                logger.warning(
                    f"[RECONCILE] {sym}: in Alpaca (qty={qty}, entry=${price:.2f}) "
                    "but NOT in bot — re-adding"
                )
                from risk_manager import Position
                import uuid
                ghost = Position(
                    symbol=sym,
                    direction="LONG" if qty > 0 else "SHORT",
                    entry_price=price,
                    quantity=abs(qty),
                    stop_loss=price * (0.97 if qty > 0 else 1.03),  # 3% default stop
                    target_1=price * (1.03 if qty > 0 else 0.97),
                    target_2=price * (1.06 if qty > 0 else 0.94),
                    order_id=str(uuid.uuid4()),
                    quality_grade="B",
                )
                risk_manager.positions[sym] = ghost
                result["added"].append(sym)
            except Exception as e:
                logger.warning(f"[RECONCILE] Failed to re-add {sym}: {e}")

        # In bot memory but NOT at Alpaca → remove ghost
        for sym in bot_symbols - broker_symbols:
            logger.warning(
                f"[RECONCILE] {sym}: in bot memory but NOT at Alpaca — removing ghost"
            )
            risk_manager.positions.pop(sym, None)
            result["removed"].append(sym)

        # Qty mismatch check
        for sym in broker_symbols & bot_symbols:
            try:
                broker_qty = abs(int(float(broker_positions[sym].qty)))
                bot_qty = getattr(risk_manager.positions[sym], 'quantity', 0)
                if broker_qty > 0 and abs(broker_qty - bot_qty) / max(broker_qty, 1) > 0.1:
                    logger.warning(
                        f"[RECONCILE] {sym}: qty mismatch Alpaca={broker_qty} bot={bot_qty} "
                        "— syncing"
                    )
                    risk_manager.positions[sym].quantity = broker_qty
                    result["qty_fixed"].append(sym)
            except Exception as e:
                logger.debug(f"[RECONCILE] qty check failed for {sym}: {e}")

        if any(result.values()):
            logger.info(
                f"[RECONCILE] Done: added={result['added']} "
                f"removed={result['removed']} fixed={result['qty_fixed']}"
            )
        return result

    def _fire_learning_callbacks(self, symbol: str, pnl: float, exit_reason: str,
                                  entry_price: float = 0.0, exit_price: float = 0.0):
        """Call RL and ML learning callbacks after a trade closes. Fail-open."""
        try:
            if self._rl_agent is not None:
                self._rl_agent.on_trade_closed(
                    symbol=symbol,
                    pnl=pnl,
                    exit_reason=exit_reason,
                    next_market_data={"price": exit_price, "pnl": pnl}
                )
        except Exception as _e:
            logger.debug(f"[RL] callback error for {symbol}: {_e}")
        try:
            if self._ml_ensemble_mod is not None:
                record_fn = getattr(self._ml_ensemble_mod, "record_trade_outcome_ensemble", None)
                if record_fn:
                    was_win = pnl > 0
                    features = {
                        "pnl_pct": (exit_price - entry_price) / max(entry_price, 0.01),
                        "exit_reason": exit_reason,
                    }
                    record_fn(features, was_win)
        except Exception as _e:
            logger.debug(f"[ML] callback error for {symbol}: {_e}")

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
            # Paper mode: simulate a fill so P&L and position tracking work correctly
            sizing = self.risk_manager.calculate_position_size(
                symbol          = symbol,
                entry_price     = signal.entry_price,
                stop_loss       = signal.stop_loss,
                direction       = direction,
                size_multiplier = getattr(signal, "size_multiplier", 1.0),
                signal_rr       = getattr(signal, "risk_reward", 2.0),
                quality_grade   = getattr(signal, "quality_grade", "B"),
                atr             = getattr(signal, "atr", 0.0),
            )
            qty          = sizing.get("quantity", 0)
            notional_amt = sizing.get("notional", 0.0)
            if qty <= 0 and notional_amt > 0:
                # Fractional share paper fill: convert notional to fractional qty
                frac_qty = round(notional_amt / signal.entry_price, 6)
                logger.info(
                    f"[{format_ist_timestamp()}] PAPER FRACTIONAL {direction} {symbol} "
                    f"notional=${notional_amt:.2f} → {frac_qty:.4f} shares @ ${signal.entry_price:.2f}"
                )
                return OrderResult(True, fill_price=signal.entry_price, quantity=frac_qty, message=f"Paper fractional fill ${notional_amt:.2f}")
            if qty <= 0:
                logger.info(
                    f"[{format_ist_timestamp()}] PAPER {direction} {symbol} "
                    f"@ ${signal.entry_price:.2f} — size=0 ({sizing.get('reason', 'capital')})"
                )
                return OrderResult(False, message=f"Paper size=0: {sizing.get('reason', 'insufficient capital')}")
            logger.info(
                f"[{format_ist_timestamp()}] PAPER {direction} {symbol} "
                f"qty={qty} @ ${signal.entry_price:.2f}"
            )
            return OrderResult(True, fill_price=signal.entry_price, quantity=qty, message="Paper fill")

        # ── SHORT selling gate (v22.0) ────────────────────────────────────────
        if direction == "SHORT":
            import config as _cfg_short
            if not getattr(_cfg_short, 'SHORT_SELLING_ENABLED', True):
                return OrderResult(False, message="SHORT_SELLING_ENABLED=false — short trades disabled")

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

        # ── PDT hard limit (margin accounts < $25K only) ──────────────────
        if not self._check_pdt_limit():
            return OrderResult(False, message="PDT day-trade limit reached — no new margin entries today")

        # ── Position sizing ───────────────────────────────────────────────
        sizing = self.risk_manager.calculate_position_size(
            symbol          = symbol,
            entry_price     = signal.entry_price,
            stop_loss       = signal.stop_loss,
            direction       = direction,
            size_multiplier = getattr(signal, "size_multiplier", 1.0),
            signal_rr       = getattr(signal, "risk_reward", 2.0),
            quality_grade   = getattr(signal, "quality_grade", "B"),
            atr             = getattr(signal, "atr", 0.0),
        )
        quantity       = sizing.get("quantity", 0)
        notional_order = sizing.get("notional", 0.0)

        if quantity <= 0 and notional_order > 0:
            # Fractional share path: stock too expensive for 1 whole share within cap limit.
            logger.info(
                f"[{format_ist_timestamp()}] FRACTIONAL {direction} {symbol} "
                f"notional=${notional_order:.2f} (1 share=${signal.entry_price:.2f})"
            )
            result = self._submit_order(
                symbol=symbol, qty=0, direction=direction,
                order_type="MARKET", notional=notional_order,
            )
            if result.success:
                self._record_day_trade(symbol)
            return result

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

        # ── Pullback entry: wait for Fibonacci retrace before entering ────────
        # Improves average fill by 0.15-0.4% on high-conviction signals.
        # Falls back to signal_price if the gate conditions are not met.
        try:
            import config as _cfgPB
            if getattr(_cfgPB, 'PULLBACK_ENTRY_ENABLED', True):
                from pullback_entry import compute_pullback_limit, should_use_pullback_entry
                _pb_score  = getattr(signal, 'signal_score', 0)
                _pb_grade  = getattr(signal, 'quality_grade', 'B')
                _pb_atr    = getattr(signal, 'atr', 0.0)
                _pb_high   = getattr(signal, 'bar_high', signal_price)
                _pb_low    = getattr(signal, 'bar_low',  signal_price)
                if should_use_pullback_entry(_pb_score, _pb_grade, _pb_atr, signal_price):
                    _pb_limit, _pb_wait = compute_pullback_limit(
                        current_price = signal_price,
                        bar_high      = _pb_high,
                        bar_low       = _pb_low,
                        direction     = direction,
                        signal_score  = _pb_score,
                        atr           = _pb_atr,
                    )
                    if _pb_limit != signal_price:
                        logger.info(
                            f"[{format_ist_timestamp()}] PULLBACK ENTRY: {symbol} {direction} "
                            f"signal={signal_price:.2f} → pullback_limit={_pb_limit:.2f} "
                            f"(grade={_pb_grade}, score={_pb_score:.0f}, wait={_pb_wait}s)"
                        )
                        signal_price = _pb_limit
        except Exception as _pb_exc:
            logger.debug(f"pullback_entry hook skipped: {_pb_exc}")

        # Smart limit: use live bid/ask for a marketable limit that fills instantly
        # but protects against flash spikes better than a pure market order
        _smart_limit_base: Optional[float] = None
        try:
            import config as _cfgSL
            if getattr(_cfgSL, 'SMART_LIMIT_ORDERS', True):
                from data_fetch_alpaca import get_quote as _gq
                _q = _gq(symbol)
                _ask = float(_q.get("ask", 0) or 0)
                _bid = float(_q.get("bid", 0) or 0)
                if direction in ("LONG", "BUY") and _ask > 0:
                    _smart_limit_base = round(_ask * 1.001, 2)
                elif direction in ("SHORT", "SELL") and _bid > 0:
                    _smart_limit_base = round(_bid * 0.999, 2)
        except Exception:
            pass

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
                if _smart_limit_base and attempt == 0:
                    limit_price = _smart_limit_base
                else:
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
                _fp, _fq = self._wait_for_fill(result.order_id, wait=self.LIMIT_WAIT_SECONDS)
                if _fp:
                    result.fill_price = _fp
                    if _fq > 0:
                        result.quantity = _fq   # use actual filled qty (handles partial fills)
                    result.order_type = "LIMIT"
                    break
                else:
                    # Cancel timed-out limit; check for partial fill before retrying
                    self.cancel_order(result.order_id)
                    # Re-fetch order status to get any partial fill that happened before cancel
                    _canceled_status = get_data_fetcher().get_order_status(result.order_id)
                    _already_filled = int(float(_canceled_status.get("filled_qty", 0) or 0))
                    if _already_filled > 0:
                        # Partial fill occurred — use it; retry only for remaining qty
                        _partial_price = float(_canceled_status.get("filled_avg_price", 0) or 0)
                        if _partial_price > 0:
                            result.fill_price = _partial_price
                            result.quantity   = _already_filled
                            result.order_type = "LIMIT"
                            logger.info(
                                f"[{format_ist_timestamp()}] Partial fill on canceled LIMIT: "
                                f"{symbol} filled_qty={_already_filled} @ ${_partial_price:.2f}; "
                                f"skipping retry for remaining {quantity - _already_filled} shares"
                            )
                            break   # treat partial fill as the result; do not double the position
                    logger.info(
                        f"[{format_ist_timestamp()}] Limit not filled in {self.LIMIT_WAIT_SECONDS}s "
                        f"— attempt {attempt+2}"
                    )
                    continue
            else:
                # Market order — poll for fill
                _fp, _fq = self._wait_for_fill(result.order_id, wait=10)
                if _fp:
                    result.fill_price = _fp
                    if _fq > 0:
                        result.quantity = _fq   # use actual filled qty (handles partial fills)
                elif result.order_id:
                    # Order submitted but fill not confirmed within 10s.
                    # Order IS live at Alpaca — use signal_price as estimated fill.
                    # Never return failure here: that would leave an untracked live position.
                    result.fill_price = signal_price
                    logger.warning(
                        f"[{format_ist_timestamp()}] {symbol}: market fill unconfirmed "
                        f"after 10s — using signal_price ${signal_price:.2f} as fill estimate"
                    )
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

        self._record_day_trade(symbol)

        # Note: entry alert is sent by main.py's _trading_cycle after add_position()
        # Duplicate alert removed to avoid double-sending.

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

            if not hasattr(OrderClass, "BRACKET"):
                raise AttributeError("OrderClass.BRACKET not available in this alpaca-py version")

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
            _fp, _fq = self._wait_for_fill(order_id, wait=15)
            fill_p = _fp if _fp else signal.entry_price

            logger.info(
                f"[{format_ist_timestamp()}] ✅ BRACKET FILLED: {symbol} {direction} "
                f"qty={quantity} @ ${fill_p:.2f} | SL=${sl_price:.2f} TP=${tp_price:.2f}"
            )
            result = OrderResult(True, message="Bracket order filled")
            result.order_id   = order_id
            result.fill_price = fill_p
            result.quantity   = float(quantity)  # must be set; stays 0.0 otherwise → 1-share fallback
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
        symbol:           str,
        quantity:         int,
        direction:        str,   # original entry direction (LONG/SHORT)
        reason:           str = "EXIT",
        limit_price:      Optional[float] = None,
        use_market_order: bool = False,
    ) -> OrderResult:
        """
        Close a position. direction is the ENTRY direction.
        LONG entry → sell to close. SHORT entry → buy to cover.
        use_market_order=True forces MARKET order (bypasses limit attempt).
        """
        if not self.live_enabled:
            # Paper mode: simulate fill. Caller uses ltp as fallback when fill_price=0.
            fill = limit_price if (limit_price and limit_price > 0) else 0.0
            logger.info(
                f"[{format_ist_timestamp()}] PAPER EXIT: {symbol} "
                f"qty={quantity} reason={reason}"
            )
            return OrderResult(True, fill_price=fill, quantity=quantity, message="Paper exit")

        exit_side = "SHORT" if direction == "LONG" else "LONG"

        if limit_price and not use_market_order:
            result = self._submit_order(symbol, quantity, exit_side, "LIMIT", limit_price)
            if result.success:
                _fp, _fq = self._wait_for_fill(result.order_id, wait=10)
                if _fp:
                    result.fill_price = _fp
                    if _fq > 0:
                        result.quantity = _fq
                    return result
                self.cancel_order(result.order_id)

        # Market exit (always succeeds on liquid US stocks)
        result = self._submit_order(symbol, quantity, exit_side, "MARKET")
        if result.success:
            _fp, _fq = self._wait_for_fill(result.order_id, wait=10)
            if _fp:
                result.fill_price = _fp
                if _fq > 0:
                    result.quantity = _fq

        logger.info(
            f"[{format_ist_timestamp()}] EXIT: {symbol} qty={quantity} "
            f"reason={reason} @ ${result.fill_price:.2f}"
        )
        return result

    # ─────────────────────────────────────────────────────────────────────
    # SQUARE OFF ALL
    # ─────────────────────────────────────────────────────────────────────

    def square_off_all(self, reason: str = "") -> List[OrderResult]:
        """
        Close ALL open positions via Alpaca's close-all endpoint.
        Used at EOD or on /kill command.
        """
        results = []
        if not self.live_enabled:
            logger.info(f"[{format_ist_timestamp()}] square_off_all ({reason}): paper mode — simulating close")
            results.append(OrderResult(True, message="Paper square-off"))
            return results
        try:
            trading_client = self._auth.get_trading_client()
            trading_client.close_all_positions(cancel_orders=True)
            logger.info(f"[{format_ist_timestamp()}] square_off_all ({reason}): sent close_all_positions")
            results.append(OrderResult(True, message="All positions closed"))
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] square_off_all failed: {e}")
            results.append(OrderResult(False, message=str(e)))
        return results

    # ─────────────────────────────────────────────────────────────────────
    # CANCEL
    # ─────────────────────────────────────────────────────────────────────

    def close_position(self, symbol: str, reason: str = "FORCE_CLOSE") -> OrderResult:
        """
        Force-close an entire position by symbol — used for scalp time-exits and EOD.
        Looks up the open position, determines qty and direction, then market-exits.
        """
        if not self.live_enabled:
            logger.info(f"[{format_ist_timestamp()}] PAPER close_position: {symbol} ({reason})")
            return OrderResult(True, message=f"Paper close {symbol}")
        try:
            trading_client = self._auth.get_trading_client()
            # Capture position details before closing for learning callbacks
            _entry_price = 0.0
            _exit_price  = 0.0
            _pnl         = 0.0
            try:
                _all_positions = trading_client.get_all_positions()
                for _p in _all_positions:
                    if _p.symbol == symbol:
                        _entry_price = float(_p.avg_entry_price or 0)
                        _exit_price  = float(_p.current_price or 0)
                        _unrealized  = float(_p.unrealized_pl or 0)
                        _pnl         = _unrealized
                        break
            except Exception as _pre:
                logger.debug(f"close_position: pre-close position lookup failed for {symbol}: {_pre}")
            # Alpaca close_position endpoint: atomically closes the entire position
            trading_client.close_position(symbol)
            logger.info(f"[{format_ist_timestamp()}] CLOSED POSITION: {symbol} ({reason})")
            self._fire_learning_callbacks(
                symbol=symbol,
                pnl=_pnl,
                exit_reason=reason,
                entry_price=_entry_price,
                exit_price=_exit_price,
            )
            return OrderResult(True, message=f"Position {symbol} closed")
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] close_position({symbol}) failed: {e}")
            # Fallback: manual market order
            try:
                positions = get_data_fetcher().get_positions()
                for p in positions:
                    if p["symbol"] == symbol:
                        direction = "LONG" if p["side"] == "long" else "SHORT"
                        qty = abs(p["qty"])
                        return self.place_exit_order(symbol, qty, direction, reason=reason, use_market_order=True)
            except Exception as _fe:
                logger.error(f"close_position fallback failed {symbol}: {_fe}")
            return OrderResult(False, message=str(e))

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

    def modify_stop_loss(self, symbol: str, new_sl: float) -> bool:
        """
        Update the stop-loss for an open position.
        Finds any open stop order for the symbol, cancels it, and places a new one.
        Falls back to no-op in paper mode (SL is tracked in memory by risk_manager).
        """
        if not self.live_enabled:
            logger.debug(f"modify_stop_loss({symbol}, {new_sl:.2f}): paper mode — SL updated in memory only")
            return True
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetOrdersRequest, StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
            import os

            api_key    = os.getenv("ALPACA_API_KEY", "")
            api_secret = os.getenv("ALPACA_SECRET_KEY", "")
            paper      = not self.live_enabled
            trading_client = TradingClient(api_key, api_secret, paper=paper)

            # Find open stop orders for the symbol
            open_orders = trading_client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            )
            for order in open_orders:
                order_type = str(order.type).lower()
                if "stop" in order_type:
                    try:
                        trading_client.cancel_order_by_id(str(order.id))
                        logger.debug(f"modify_stop_loss: cancelled old stop order {order.id} for {symbol}")
                    except Exception as _ce:
                        logger.debug(f"modify_stop_loss: cancel failed {order.id}: {_ce}")

            # Determine side and qty from position
            from alpaca.trading.enums import OrderType
            positions = trading_client.get_all_positions()
            pos_side = None
            pos_qty  = 0
            current_price = 0.0
            for p in positions:
                if p.symbol == symbol:
                    pos_side = str(p.side.value).lower()
                    pos_qty  = abs(int(p.qty))
                    current_price = float(p.current_price or 0)
                    break

            if pos_side is None or pos_qty == 0:
                logger.debug(f"modify_stop_loss: no open position found for {symbol}")
                return False

            # Sanity check: SL must be on the correct side of market price
            if current_price > 0:
                if pos_side == "long" and new_sl >= current_price:
                    new_sl = round(current_price * 0.995, 2)  # clamp to 0.5% below market
                    logger.debug(f"modify_stop_loss: {symbol} LONG SL clamped to ${new_sl:.2f} (below current ${current_price:.2f})")
                elif pos_side == "short" and new_sl <= current_price:
                    new_sl = round(current_price * 1.005, 2)  # clamp to 0.5% above market
                    logger.debug(f"modify_stop_loss: {symbol} SHORT SL clamped to ${new_sl:.2f} (above current ${current_price:.2f})")

            # Stop order side is opposite to position side (sell stop for long, buy stop for short)
            stop_side = OrderSide.SELL if pos_side == "long" else OrderSide.BUY
            req = StopOrderRequest(
                symbol        = symbol,
                qty           = pos_qty,
                side          = stop_side,
                time_in_force = TimeInForce.DAY,
                stop_price    = round(new_sl, 2),
            )
            trading_client.submit_order(req)
            logger.info(f"[{format_ist_timestamp()}] modify_stop_loss: {symbol} new SL=${new_sl:.2f}")
            return True
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] modify_stop_loss({symbol}, {new_sl:.2f}) failed: {e}")
            return False

    def place_stop_order(
        self,
        symbol:     str,
        qty:        int,
        stop_price: float,
        direction:  str,   # entry direction: "LONG" or "SHORT"
    ) -> str:
        """
        Place a broker-side stop order so SL is enforced even if the bot crashes.
        LONG entry → SELL STOP at stop_price (sell if price drops to SL).
        SHORT entry → BUY STOP at stop_price (buy to cover if price rises to SL).
        Returns Alpaca order ID on success, empty string on failure.
        """
        if not self.live_enabled:
            logger.debug(f"place_stop_order({symbol}): paper mode — stop tracked in memory only")
            return ""
        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderType

            stop_side = OrderSide.SELL if direction == "LONG" else OrderSide.BUY
            trading_client = self._auth.get_trading_client()
            req = StopOrderRequest(
                symbol        = symbol,
                qty           = qty,
                side          = stop_side,
                time_in_force = TimeInForce.DAY,
                stop_price    = round(stop_price, 2),
            )
            order = trading_client.submit_order(req)
            order_id = str(order.id)
            logger.info(
                f"[{format_ist_timestamp()}] STOP ORDER placed: {symbol} {stop_side.value} "
                f"qty={qty} @ ${stop_price:.2f} | order_id={order_id}"
            )
            return order_id
        except Exception as e:
            logger.warning(
                f"[{format_ist_timestamp()}] place_stop_order({symbol}, {stop_price:.2f}): {e}"
            )
            return ""

    # ─────────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────────────

    def _check_pdt_limit(self) -> bool:
        """
        Returns False (block trade) when PDT limit is reached on a margin account.
        Cash accounts (ACCOUNT_TYPE=CASH) are exempt — always returns True.
        """
        import config as _cfg
        if not getattr(_cfg, 'PDT_ENFORCE', True):
            return True
        if getattr(_cfg, 'ACCOUNT_TYPE', 'CASH').upper() == 'CASH':
            return True
        from datetime import date as _date
        today = str(_date.today())
        if self._day_trade_date != today:
            return True  # new day, counter resets on first trade
        max_dt = getattr(_cfg, 'PDT_MAX_DAY_TRADES', 3)
        if self._day_trade_count >= max_dt:
            logger.warning(
                f"[{format_ist_timestamp()}] PDT HARD BLOCK: {self._day_trade_count}/{max_dt} "
                "day trades used today — no new entries on margin account. "
                "Set ACCOUNT_TYPE=CASH or PDT_ENFORCE=False to override."
            )
            return False
        return True

    def _record_day_trade(self, symbol: str) -> None:
        """Track day-trade count. Hard limit enforced by _check_pdt_limit() before order."""
        from datetime import date as _date
        today = str(_date.today())
        if self._day_trade_date != today:
            self._day_trade_count = 0
            self._day_trade_date  = today
        self._day_trade_count += 1
        logger.info(
            f"[{format_ist_timestamp()}] Day trade #{self._day_trade_count} recorded ({symbol})"
        )

    def _submit_order(
        self,
        symbol:      str,
        qty:         int,
        direction:   str,   # "LONG" or "SHORT"
        order_type:  str,   # "LIMIT" or "MARKET"
        limit_price: Optional[float] = None,
        notional:    Optional[float] = None,   # USD notional for fractional-share orders
    ) -> OrderResult:
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderType
            from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest

            side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL

            trading_client = self._auth.get_trading_client()

            if notional and notional > 0:
                # Notional (fractional share) order — Alpaca converts $ amount to shares
                req = MarketOrderRequest(
                    symbol        = symbol,
                    notional      = round(notional, 2),
                    side          = side,
                    time_in_force = TimeInForce.DAY,
                )
            elif order_type == "LIMIT" and limit_price:
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
            # Graceful handling for short-sell failures (HTB, not enabled on account, etc.)
            if direction == "SHORT" and any(kw in msg.lower() for kw in (
                "short", "borrow", "locate", "not available", "prohibited", "not supported"
            )):
                logger.warning(
                    f"[{format_ist_timestamp()}] SHORT SELL REJECTED: {symbol} — {msg} "
                    "(stock may not be available to borrow or account shorting not enabled)"
                )
            else:
                logger.error(
                    f"[{format_ist_timestamp()}] ORDER FAILED: {symbol} {direction} "
                    f"{order_type} qty={qty} | Error: {msg}"
                )
            return OrderResult(False, message=msg)

    def _wait_for_fill(self, order_id: str, wait: int = 12) -> tuple:
        """
        Poll order status until filled or timeout.
        Returns (fill_price, filled_qty) or (0.0, 0) if not filled.
        Handles partial fills by returning the actual filled quantity.
        """
        deadline = _time.monotonic() + wait
        fetcher  = get_data_fetcher()
        while _time.monotonic() < deadline:
            status = fetcher.get_order_status(order_id)
            _status_str = str(status.get("status", "")).lower()
            if _status_str == "filled":
                price   = float(status.get("filled_avg_price", 0.0) or 0.0)
                qty_raw = status.get("filled_qty") or status.get("qty") or 0
                qty     = int(float(qty_raw or 0))
                return (price, qty) if price > 0 else (price, 0)
            if _status_str == "partially_filled":
                # Keep polling until fully filled or timeout — returning early leaves
                # remaining shares without a broker-side stop order.
                price   = float(status.get("filled_avg_price", 0.0) or 0.0)
                qty_raw = status.get("filled_qty") or status.get("qty") or 0
                qty     = int(float(qty_raw or 0))
                if price > 0 and qty > 0:
                    logger.info(
                        f"[{format_ist_timestamp()}] Partial fill {order_id}: "
                        f"qty={qty} @ ${price:.2f} — polling for full fill"
                    )
                # fall through: sleep and poll again
            if status.get("status") in ("canceled", "expired", "rejected"):
                return (0.0, 0)
            _time.sleep(self.POLL_INTERVAL)
        return (0.0, 0)


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
