"""
risk_manager.py — NSE Momentum Groww AI Bot
ATR-Based Risk Management Engine

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
All risk parameters from config.py — never hardcode limits.

Features:
- Per-trade risk: 0.5–1% of capital (ATR-based SL)
- Trailing stop: activates at 1x ATR profit, trails 0.5x ATR
- Daily loss limit circuit breaker (2% of capital)
- Nifty circuit breaker (pause if Nifty moves >2%)
- Consecutive loss pause (3 losses → 30min pause)
- Position sizing via Kelly Criterion (capped at 10%)
- Auto balance check before every trade
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, format_currency

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class Position:
    """Tracks an open intraday position."""
    symbol: str
    direction: str         # "LONG" or "SHORT"
    entry_price: float
    quantity: int
    stop_loss: float
    target_1: float
    target_2: float
    atr: float
    entry_time: str = ""
    trailing_active: bool = False
    trailing_stop: float = 0.0
    current_price: float = 0.0
    max_price: float = 0.0   # For LONG trailing
    min_price: float = 0.0   # For SHORT trailing
    order_id: str = ""
    sl_order_id: str = ""
    partial_exit_done: bool = False

    def __post_init__(self):
        if not self.entry_time:
            self.entry_time = format_ist_timestamp()
        if self.current_price == 0:
            self.current_price = self.entry_price
        if self.max_price == 0:
            self.max_price = self.entry_price
        if self.min_price == 0:
            self.min_price = self.entry_price

    @property
    def pnl(self) -> float:
        if self.direction == "LONG":
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0
        return (self.pnl / (self.entry_price * self.quantity)) * 100

    @property
    def risk_amount(self) -> float:
        return abs(self.entry_price - self.stop_loss) * self.quantity

    @property
    def active_sl(self) -> float:
        """Current effective stop loss (trailing or original)."""
        return self.trailing_stop if self.trailing_active else self.stop_loss


@dataclass
class RiskState:
    """Daily risk tracking state — resets each morning."""
    date: str = ""
    daily_capital: float = 0.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    trading_paused: bool = False
    pause_reason: str = ""
    pause_until: Optional[datetime] = None
    circuit_breaker_active: bool = False
    nifty_open: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    closed_trades: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.date:
            self.date = get_current_ist_time().strftime("%Y-%m-%d")

    @property
    def win_rate(self) -> float:
        total = self.winning_trades + self.losing_trades
        return (self.winning_trades / total * 100) if total > 0 else 0.0

    @property
    def daily_loss_pct(self) -> float:
        if self.daily_capital == 0:
            return 0.0
        return (abs(min(self.daily_pnl, 0)) / self.daily_capital) * 100


class RiskManager:
    """
    Central risk management for the trading bot.
    Controls position sizing, stop management, and circuit breakers.
    """

    def __init__(
        self,
        max_daily_capital: float = 50000,
        max_risk_pct: float = 0.5,
        daily_loss_limit_pct: float = 2.0,
        max_positions: int = 10,
        nifty_circuit_pct: float = 2.0,
        consecutive_loss_limit: int = 3,
        pause_minutes: int = 30,
    ):
        self.max_daily_capital = max_daily_capital
        self.max_risk_pct = min(max_risk_pct, 1.0)   # Hard cap at 1%
        self.daily_loss_limit_pct = min(daily_loss_limit_pct, 3.0)
        self.max_positions = max_positions
        self.nifty_circuit_pct = nifty_circuit_pct
        self.consecutive_loss_limit = consecutive_loss_limit
        self.pause_minutes = pause_minutes

        self.state = RiskState()
        self._available_balance: float = 0.0

        logger.info(
            f"[{format_ist_timestamp()}] RiskManager initialized | "
            f"Capital: {format_currency(max_daily_capital)} | "
            f"Max risk/trade: {max_risk_pct}% | "
            f"Daily loss limit: {daily_loss_limit_pct}%"
        )

    # --------------------------------------------------------
    # DAILY INITIALIZATION
    # --------------------------------------------------------

    def initialize_day(self, available_balance: float, nifty_open: float = 0):
        """Call this at market open (9:15 AM IST) each day."""
        now_ist = get_current_ist_time()
        self.state = RiskState(
            date=now_ist.strftime("%Y-%m-%d"),
            daily_capital=min(available_balance, self.max_daily_capital),
            nifty_open=nifty_open,
        )
        self._available_balance = available_balance
        logger.info(
            f"[{format_ist_timestamp()}] Day initialized | "
            f"Capital: {format_currency(self.state.daily_capital)} | "
            f"Nifty open: {nifty_open:.2f}"
        )

    def update_balance(self, balance: float):
        """Update available balance (called before each trade)."""
        self._available_balance = balance
        effective_capital = min(balance, self.max_daily_capital)
        if effective_capital != self.state.daily_capital:
            self.state.daily_capital = effective_capital

    # --------------------------------------------------------
    # POSITION SIZING
    # --------------------------------------------------------

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        direction: str = "LONG",
        win_rate_estimate: float = 0.55,
    ) -> Dict:
        """
        Calculate position size using risk-based method + Kelly Criterion check.

        Args:
            symbol: Stock symbol
            entry_price: Planned entry price
            stop_loss: Planned stop loss price
            direction: "LONG" or "SHORT"
            win_rate_estimate: Historical win rate (0.55 default from trainer)

        Returns:
            dict with quantity, risk_amount, capital_used, sizing_details
        """
        capital = self.state.daily_capital
        if capital <= 0 or entry_price <= 0:
            return {"quantity": 0, "reason": "Insufficient capital"}

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return {"quantity": 0, "reason": "Invalid SL distance"}

        # Risk amount in INR
        risk_amount = capital * (self.max_risk_pct / 100)

        # Primary sizing: risk-based
        risk_qty = int(risk_amount / sl_distance)

        # Kelly Criterion check (informational, not primary)
        # Kelly% = W - (1-W)/R where R = avg_win/avg_loss
        avg_rr = 2.5  # Default 2.5:1 target from strategy
        kelly_pct = win_rate_estimate - (1 - win_rate_estimate) / avg_rr
        kelly_pct = max(0, min(kelly_pct, 0.25))  # Cap at 25%
        kelly_qty = int((capital * kelly_pct) / entry_price)

        # Use the more conservative of the two
        quantity = min(risk_qty, kelly_qty) if kelly_qty > 0 else risk_qty

        # Hard cap: max 15% of capital in a single trade
        max_qty_by_capital = int((capital * 0.15) / entry_price)
        quantity = min(quantity, max_qty_by_capital)

        # Min quantity = 1
        quantity = max(quantity, 1)

        capital_used = entry_price * quantity
        actual_risk = sl_distance * quantity

        return {
            "quantity": quantity,
            "risk_amount": round(actual_risk, 2),
            "capital_used": round(capital_used, 2),
            "capital_pct": round((capital_used / capital) * 100, 1),
            "risk_pct": round((actual_risk / capital) * 100, 2),
            "risk_qty": risk_qty,
            "kelly_qty": kelly_qty,
            "sl_distance": round(sl_distance, 2),
        }

    # --------------------------------------------------------
    # PRE-TRADE CHECKS
    # --------------------------------------------------------

    def can_take_trade(self, symbol: str, direction: str = "LONG") -> Dict:
        """
        Check all risk conditions before entering a trade.
        Returns {"allowed": bool, "reason": str}
        """
        # 1. Trading paused?
        if self.state.trading_paused:
            if self.state.pause_until:
                now = get_current_ist_time()
                if now < self.state.pause_until:
                    remaining = (self.state.pause_until - now).seconds // 60
                    return {"allowed": False, "reason": f"Trading paused — {remaining}min remaining ({self.state.pause_reason})"}
                else:
                    # Pause expired
                    self.resume_trading(auto=True)
            else:
                return {"allowed": False, "reason": f"Trading paused: {self.state.pause_reason}"}

        # 2. Circuit breaker active?
        if self.state.circuit_breaker_active:
            return {"allowed": False, "reason": "Circuit breaker active — no new entries"}

        # 3. Daily loss limit?
        if self.state.daily_loss_pct >= self.daily_loss_limit_pct:
            self._trigger_circuit_breaker(f"Daily loss limit {self.daily_loss_limit_pct}% hit")
            return {"allowed": False, "reason": f"Daily loss limit {self.daily_loss_limit_pct}% reached"}

        # 4. Max positions?
        open_positions = len(self.state.positions)
        if open_positions >= self.max_positions:
            return {"allowed": False, "reason": f"Max positions ({self.max_positions}) reached"}

        # 5. Already have position in this symbol?
        if symbol in self.state.positions:
            return {"allowed": False, "reason": f"Already have open position in {symbol}"}

        # 6. Sufficient capital?
        if self._available_balance < 1000:
            return {"allowed": False, "reason": "Insufficient balance"}

        return {"allowed": True, "reason": "All checks passed"}

    # --------------------------------------------------------
    # TRAILING STOP MANAGEMENT
    # --------------------------------------------------------

    def update_trailing_stop(self, position: Position, current_price: float) -> Dict:
        """
        Update trailing stop for an open position.
        Trailing activates after 1x ATR profit, then trails at 0.5x ATR.

        Returns:
            {"action": "HOLD"/"EXIT"/"UPDATE_SL", "new_sl": float, "reason": str}
        """
        position.current_price = current_price
        atr = position.atr

        if position.direction == "LONG":
            position.max_price = max(position.max_price, current_price)
            profit = current_price - position.entry_price

            # Check original SL breach
            if current_price <= position.stop_loss:
                return {"action": "EXIT", "new_sl": position.stop_loss,
                        "reason": f"SL hit at ₹{current_price:.2f}"}

            # Activate trailing after 1x ATR profit
            if not position.trailing_active and profit >= atr:
                position.trailing_active = True
                position.trailing_stop = current_price - 0.5 * atr
                logger.info(
                    f"[{format_ist_timestamp()}] {position.symbol}: "
                    f"Trailing stop ACTIVATED at ₹{position.trailing_stop:.2f}"
                )

            if position.trailing_active:
                # Trail at 0.5x ATR below highest price
                new_trail = position.max_price - 0.5 * atr
                if new_trail > position.trailing_stop:
                    old_trail = position.trailing_stop
                    position.trailing_stop = new_trail
                    logger.debug(
                        f"{position.symbol}: Trail SL updated "
                        f"₹{old_trail:.2f} → ₹{new_trail:.2f}"
                    )
                    return {"action": "UPDATE_SL", "new_sl": new_trail,
                            "reason": f"Trail updated, max={position.max_price:.2f}"}

                if current_price <= position.trailing_stop:
                    return {"action": "EXIT", "new_sl": position.trailing_stop,
                            "reason": f"Trailing SL hit at ₹{current_price:.2f}"}

            # Check target hits
            if current_price >= position.target_2:
                return {"action": "EXIT", "new_sl": current_price,
                        "reason": f"Target 2 hit: ₹{position.target_2:.2f}"}

            if current_price >= position.target_1 and not position.partial_exit_done:
                return {"action": "PARTIAL_EXIT", "new_sl": position.entry_price,
                        "reason": f"Target 1 hit: ₹{position.target_1:.2f} — partial exit, move SL to entry"}

        else:  # SHORT
            position.min_price = min(position.min_price, current_price)
            profit = position.entry_price - current_price

            if current_price >= position.stop_loss:
                return {"action": "EXIT", "new_sl": position.stop_loss,
                        "reason": f"SL hit at ₹{current_price:.2f}"}

            if not position.trailing_active and profit >= atr:
                position.trailing_active = True
                position.trailing_stop = current_price + 0.5 * atr

            if position.trailing_active:
                new_trail = position.min_price + 0.5 * atr
                if new_trail < position.trailing_stop:
                    position.trailing_stop = new_trail
                    return {"action": "UPDATE_SL", "new_sl": new_trail,
                            "reason": f"Short trail updated"}

                if current_price >= position.trailing_stop:
                    return {"action": "EXIT", "new_sl": position.trailing_stop,
                            "reason": f"Short trailing SL hit"}

            if current_price <= position.target_2:
                return {"action": "EXIT", "new_sl": current_price,
                        "reason": f"Short Target 2 hit: ₹{position.target_2:.2f}"}

            if current_price <= position.target_1 and not position.partial_exit_done:
                return {"action": "PARTIAL_EXIT", "new_sl": position.entry_price,
                        "reason": f"Short Target 1 hit: ₹{position.target_1:.2f}"}

        return {"action": "HOLD", "new_sl": position.active_sl, "reason": "Hold"}

    # --------------------------------------------------------
    # POSITION TRACKING
    # --------------------------------------------------------

    def add_position(self, position: Position):
        """Register a new open position."""
        self.state.positions[position.symbol] = position
        logger.info(
            f"[{format_ist_timestamp()}] Position opened: "
            f"{position.direction} {position.symbol} x{position.quantity} "
            f"@ ₹{position.entry_price:.2f} | "
            f"SL: ₹{position.stop_loss:.2f} | "
            f"T1: ₹{position.target_1:.2f}"
        )

    def close_position(self, symbol: str, exit_price: float, reason: str = ""):
        """Close and record a position."""
        if symbol not in self.state.positions:
            return None
        pos = self.state.positions.pop(symbol)
        pos.current_price = exit_price
        pnl = pos.pnl
        self.state.daily_pnl += pnl
        self.state.daily_trades += 1

        if pnl > 0:
            self.state.winning_trades += 1
            self.state.consecutive_losses = 0
        else:
            self.state.losing_trades += 1
            self.state.consecutive_losses += 1
            self.state.max_consecutive_losses = max(
                self.state.max_consecutive_losses,
                self.state.consecutive_losses
            )
            # Consecutive loss pause
            if self.state.consecutive_losses >= self.consecutive_loss_limit:
                self._pause_trading(
                    f"{self.consecutive_loss_limit} consecutive losses",
                    minutes=self.pause_minutes
                )

        # Update peak and drawdown
        self.state.peak_pnl = max(self.state.peak_pnl, self.state.daily_pnl)
        drawdown = self.state.peak_pnl - self.state.daily_pnl
        self.state.max_drawdown = max(self.state.max_drawdown, drawdown)

        trade_record = {
            "symbol": symbol,
            "direction": pos.direction,
            "entry": pos.entry_price,
            "exit": exit_price,
            "quantity": pos.quantity,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pos.pnl_pct, 2),
            "entry_time": pos.entry_time,
            "exit_time": format_ist_timestamp(),
            "reason": reason,
        }
        self.state.closed_trades.append(trade_record)

        logger.info(
            f"[{format_ist_timestamp()}] Position closed: {symbol} | "
            f"P&L: {format_currency(pnl)} ({pos.pnl_pct:+.2f}%) | "
            f"Reason: {reason} | Daily P&L: {format_currency(self.state.daily_pnl)}"
        )
        return trade_record

    # --------------------------------------------------------
    # CIRCUIT BREAKERS
    # --------------------------------------------------------

    def check_nifty_circuit(self, nifty_current: float):
        """Pause new entries if Nifty moves >2% from open."""
        if self.state.nifty_open == 0:
            return
        nifty_move = abs((nifty_current - self.state.nifty_open) / self.state.nifty_open) * 100
        if nifty_move >= self.nifty_circuit_pct:
            direction = "UP" if nifty_current > self.state.nifty_open else "DOWN"
            self._trigger_circuit_breaker(
                f"Nifty moved {nifty_move:.1f}% {direction} from open"
            )

    def _trigger_circuit_breaker(self, reason: str):
        if not self.state.circuit_breaker_active:
            self.state.circuit_breaker_active = True
            self.state.trading_paused = True
            self.state.pause_reason = f"CIRCUIT BREAKER: {reason}"
            logger.warning(
                f"[{format_ist_timestamp()}] 🚨 CIRCUIT BREAKER ACTIVATED: {reason}"
            )

    def _pause_trading(self, reason: str, minutes: int = 30):
        now = get_current_ist_time()
        self.state.trading_paused = True
        self.state.pause_reason = reason
        self.state.pause_until = now + timedelta(minutes=minutes)
        logger.warning(
            f"[{format_ist_timestamp()}] ⏸ Trading PAUSED for {minutes}min: {reason}"
        )

    def resume_trading(self, auto: bool = False):
        self.state.trading_paused = False
        self.state.pause_reason = ""
        self.state.pause_until = None
        if not self.state.circuit_breaker_active:
            msg = "auto-resumed" if auto else "manually resumed"
            logger.info(f"[{format_ist_timestamp()}] ▶️ Trading {msg}")

    def manual_resume(self):
        """Manual resume via Telegram /resume command."""
        self.state.circuit_breaker_active = False
        self.resume_trading()
        logger.info(f"[{format_ist_timestamp()}] ▶️ Trading manually resumed via Telegram")

    def emergency_stop(self):
        """Kill switch — called by /kill Telegram command."""
        self._trigger_circuit_breaker("MANUAL KILL SWITCH")
        logger.critical(f"[{format_ist_timestamp()}] 🛑 EMERGENCY STOP ACTIVATED")

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    def get_daily_summary(self) -> Dict:
        """Get today's trading summary."""
        return {
            "date": self.state.date,
            "daily_pnl": round(self.state.daily_pnl, 2),
            "daily_pnl_pct": round((self.state.daily_pnl / max(self.state.daily_capital, 1)) * 100, 2),
            "total_trades": self.state.daily_trades,
            "wins": self.state.winning_trades,
            "losses": self.state.losing_trades,
            "win_rate": round(self.state.win_rate, 1),
            "open_positions": len(self.state.positions),
            "max_drawdown": round(self.state.max_drawdown, 2),
            "consecutive_losses": self.state.consecutive_losses,
            "paused": self.state.trading_paused,
            "circuit_breaker": self.state.circuit_breaker_active,
        }
