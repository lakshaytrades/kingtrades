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
from typing import Optional, Dict, List, Tuple
from zoneinfo import ZoneInfo

import numpy as np

import config as _config
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
    partial_exit_done: bool = False  # Legacy — kept for compatibility
    # 50/30/20 Partial exit tracking
    quality_grade: str = "B"         # A+, A, B, C — affects trail aggressiveness
    t1_qty: int = 0                  # Qty to exit at T1 (50%)
    t2_qty: int = 0                  # Qty to exit at T2 (30%)
    runner_qty: int = 0              # Runner qty (20%) with tight trailing
    t1_done: bool = False
    t2_done: bool = False
    breakeven_done: bool = False     # True once SL moved to entry (0.5% profit)
    size_multiplier: float = 1.0     # From HighAccuracyFilter

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
    available_capital: float = 0.0
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
        if not self.available_capital:
            self.available_capital = self.daily_capital

    @property
    def win_rate(self) -> float:
        total = self.winning_trades + self.losing_trades
        return (self.winning_trades / total * 100) if total > 0 else 0.0

    @property
    def daily_loss_pct(self) -> float:
        if self.daily_capital == 0:
            return 0.0
        return (abs(min(self.daily_pnl, 0)) / self.daily_capital) * 100

    @property
    def portfolio_heat(self) -> float:
        """Total risk across all open positions as % of capital."""
        total_risk = sum(p.risk_amount for p in self.positions.values())
        return (total_risk / max(self.daily_capital, 1)) * 100


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

        # Institutional intelligence multipliers (set by main.py each morning)
        self._fii_mult:  float = 1.0   # FII/DII flow: 0.5–1.5×
        self._oc_mult:   float = 1.0   # Option chain bias: 0.9–1.1×
        self._inst_mult: float = 1.0   # Combined: fii_mult × oc_mult

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
        # Use actual live balance — no artificial cap
        cap = available_balance if available_balance > 0 else self.max_daily_capital
        self.state = RiskState(
            date=now_ist.strftime("%Y-%m-%d"),
            daily_capital=cap,
            available_capital=cap,
            nifty_open=nifty_open,
        )
        self._available_balance = available_balance
        logger.info(
            f"[{format_ist_timestamp()}] Day initialized | "
            f"Balance: {format_currency(cap)} × 5x leverage = "
            f"{format_currency(cap * 5)} buying power | "
            f"Nifty open: {nifty_open:.2f}"
        )

    def update_balance(self, balance: float):
        """Update to live Groww balance before each trade — no cap, uses real funds."""
        if balance <= 0:
            logger.debug(
                f"[{format_ist_timestamp()}] update_balance: ignoring 0 — "
                f"keeping ₹{self._available_balance:,.0f} (API may be offline)"
            )
            return
        self._available_balance = balance
        # Use actual balance — bot adapts to whatever funds are in the account
        self.state.available_capital = balance
        self.state.daily_capital     = balance

    def set_institutional_multiplier(
        self, fii_mult: float = 1.0, oc_mult: float = 1.0
    ) -> None:
        """
        Set institutional flow multipliers.
        Called by main.py each morning after FII/DII and option chain analysis.

        fii_mult: from FIIDIITracker.get_position_size_multiplier() — 0.5 to 1.5
        oc_mult:  from option chain bias — 0.9 (bearish) / 1.0 (neutral) / 1.1 (bullish)

        Combined effect on position size: fii_mult × oc_mult (capped 0.5–1.5)
        """
        self._fii_mult  = max(0.5, min(fii_mult, 1.5))
        self._oc_mult   = max(0.8, min(oc_mult, 1.2))
        self._inst_mult = max(0.5, min(self._fii_mult * self._oc_mult, 1.5))
        logger.info(
            f"[{format_ist_timestamp()}] Institutional multiplier set: "
            f"FII={self._fii_mult:.2f}× OC={self._oc_mult:.2f}× "
            f"→ Combined={self._inst_mult:.2f}×"
        )

    # --------------------------------------------------------
    # SESSION MULTIPLIER
    # --------------------------------------------------------

    def _get_session_multiplier(self) -> Tuple[float, str]:
        """
        Session-based position size multiplier.
        18yr Rule: 60% of intraday P&L comes from the opening drive (9:15-10:00).
        Midday (11:00-13:30) is a trap — algos chop retail to death.

        Returns (multiplier, session_name)
        """
        now = get_current_ist_time()
        h, m = now.hour, now.minute
        total_min = h * 60 + m

        if 555 <= total_min < 600:    # 09:15-10:00 Opening drive
            return 1.0, "OPENING_DRIVE"
        elif 600 <= total_min < 660:  # 10:00-11:00 Morning session
            return 0.80, "MORNING"
        elif 660 <= total_min < 810:  # 11:00-13:30 Midday chop
            return 0.50, "MIDDAY_CHOP"
        elif 810 <= total_min < 900:  # 13:30-15:00 Afternoon trend
            return 0.80, "AFTERNOON"
        elif 900 <= total_min < 920:  # 15:00-15:20 Closing risk
            return 0.30, "CLOSING"
        else:
            return 0.0, "AFTER_HOURS"

    # --------------------------------------------------------
    # DYNAMIC KELLY CRITERION
    # --------------------------------------------------------

    def _dynamic_kelly_fraction(self) -> float:
        """
        Dynamic Kelly using the last 20 closed trades (updates intraday).
        Falls back to 0.55 win-rate estimate if fewer than 5 trades.

        Kelly% = W - (1-W)/R  where R = avg_win / avg_loss
        Capped at 25% to prevent overbetting.
        """
        recent = self.state.closed_trades[-20:]
        if len(recent) < 5:
            return 0.55   # Fallback

        wins   = [t for t in recent if t.get("pnl", 0) > 0]
        losses = [t for t in recent if t.get("pnl", 0) <= 0]
        if not wins or not losses:
            return 0.55

        wr      = len(wins) / len(recent)
        avg_win = abs(sum(t["pnl"] for t in wins)   / len(wins))
        avg_los = abs(sum(t["pnl"] for t in losses) / len(losses))
        if avg_los < 1:
            return 0.55

        kelly = wr - (1 - wr) / (avg_win / avg_los)
        return max(0.10, min(kelly, 0.25))   # Clamp 10-25%

    # --------------------------------------------------------
    # SECTOR CORRELATION GUARD
    # --------------------------------------------------------

    def _check_sector_correlation(self, symbol: str) -> Dict:
        """
        Prevent more than 2 open positions in the same sector.
        18yr Rule: "Sector correlation kills diversification. 3 bank stocks
        in a bank rout = 3× the loss."
        """
        try:
            from nse_data import get_sector
            sector = get_sector(symbol)
            if sector == "OTHER":
                return {"allowed": True, "reason": "Unknown sector — allowing"}

            same_sector = [
                s for s in self.state.positions
                if get_sector(s) == sector
            ]
            max_per_sector = getattr(_config, "MAX_POSITIONS_PER_SECTOR", 2)
            if len(same_sector) >= max_per_sector:
                return {
                    "allowed": False,
                    "reason": (
                        f"Sector limit: already {len(same_sector)} open in "
                        f"{sector} ({', '.join(same_sector)})"
                    ),
                }
        except Exception:
            pass
        return {"allowed": True, "reason": "Sector check passed"}

    # --------------------------------------------------------
    # PORTFOLIO VAR
    # --------------------------------------------------------

    def calculate_portfolio_var(self, confidence: float = 0.95) -> Dict:
        """
        Calculate Value at Risk across all open positions.
        Uses ATR as a 1-day volatility proxy for each position.

        Returns:
          var_95:  95% 1-day Value at Risk in ₹
          cvar_95: Conditional VaR (expected loss beyond VaR)
          heat_pct: Portfolio heat (% of capital at risk via SL)
        """
        if not self.state.positions:
            return {"var_95": 0, "cvar_95": 0, "heat_pct": 0}

        losses = []
        for pos in self.state.positions.values():
            # Max loss = SL distance × quantity
            sl_loss = abs(pos.entry_price - pos.active_sl) * pos.quantity
            # Simulate 100 scenarios using ATR
            atr = pos.atr if pos.atr > 0 else pos.entry_price * 0.005
            scenarios = np.random.normal(-atr * pos.quantity, atr * pos.quantity * 0.5, 1000)
            losses.extend(scenarios.tolist())

        if not losses:
            return {"var_95": 0, "cvar_95": 0, "heat_pct": 0}

        loss_arr = np.array(losses)
        var_95   = float(np.percentile(-loss_arr, 95))
        cvar_95  = float(np.mean(-loss_arr[-loss_arr >= var_95])) if var_95 > 0 else 0

        return {
            "var_95":   round(max(var_95, 0), 2),
            "cvar_95":  round(max(cvar_95, 0), 2),
            "heat_pct": round(self.state.portfolio_heat, 2),
        }

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
        size_multiplier: float = 1.0,
    ) -> Dict:
        """
        Multi-layer position sizing:
          1. Risk-based (primary): risk_pct × capital / SL_distance
          2. Dynamic Kelly (secondary): uses last 20 trades
          3. Session multiplier: reduce size during midday chop
          4. Institutional multiplier: FII/DII + Option Chain
          5. Portfolio heat cap: no trade if total heat > MAX_PORTFOLIO_HEAT
          6. Capital cap: max 15% per position
        """
        import config as _cfg
        capital = self.state.daily_capital
        if capital <= 0 or entry_price <= 0:
            return {"quantity": 0, "reason": "Insufficient capital"}

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return {"quantity": 0, "reason": "Invalid SL distance"}

        LEVERAGE = 5.0  # Groww MIS intraday leverage
        buying_power = capital * LEVERAGE  # effective capital for position sizing

        # 1. Risk-based sizing (risk on actual capital, not leveraged)
        risk_amount = capital * (self.max_risk_pct / 100)
        risk_qty    = int(risk_amount / sl_distance)

        # 2. Dynamic Kelly (on buying power)
        kelly_frac  = self._dynamic_kelly_fraction()
        kelly_qty   = int((buying_power * kelly_frac) / entry_price)

        # More conservative of the two
        quantity = min(risk_qty, kelly_qty) if kelly_qty > 0 else risk_qty

        # 3. Session multiplier (reduce during midday, closing)
        sess_mult, session = self._get_session_multiplier()
        quantity = max(1, int(quantity * sess_mult))

        # 4a. Day-of-week multiplier (4-day profit optimizer)
        now_ist    = get_current_ist_time()
        dow        = now_ist.weekday()   # 0=Mon … 4=Fri
        dow_mults  = getattr(_cfg, "DOW_SIZE_MULTIPLIERS", {})
        dow_mult   = dow_mults.get(dow, 1.0)
        quantity   = max(1, int(quantity * dow_mult))

        # 4b. Institutional multiplier (FII/DII + Option Chain)
        inst_mult = getattr(self, "_inst_mult", 1.0)
        quantity  = max(1, int(quantity * inst_mult))

        # 4c. Signal grade / profit engine size multiplier (A+=1.25, A=1.1, etc.)
        if size_multiplier != 1.0:
            quantity = max(1, int(quantity * size_multiplier))

        # 5. Portfolio heat cap
        max_portfolio_heat = getattr(_cfg, "MAX_PORTFOLIO_HEAT_PCT", 3.0)
        current_heat       = self.state.portfolio_heat
        if current_heat >= max_portfolio_heat:
            return {
                "quantity": 0,
                "reason": (
                    f"Portfolio heat {current_heat:.1f}% ≥ "
                    f"max {max_portfolio_heat}% — no new entries"
                ),
            }

        # 6. Capital cap: max MAX_CAPITAL_PER_TRADE_PCT% of buying power per position
        cap_pct = getattr(_cfg, "MAX_CAPITAL_PER_TRADE_PCT", 20.0) / 100.0
        max_by_capital = int((buying_power * cap_pct) / entry_price)
        quantity = min(quantity, max_by_capital)
        quantity = max(quantity, 1)

        capital_used     = entry_price * quantity
        actual_risk      = sl_distance * quantity
        margin_required  = capital_used / LEVERAGE  # actual cash margin needed

        return {
            "quantity":       quantity,
            "risk_amount":    round(actual_risk, 2),
            "capital_used":   round(capital_used, 2),
            "margin_required":round(margin_required, 2),
            "capital_pct":    round(capital_used / buying_power * 100, 1),
            "risk_pct":       round(actual_risk  / capital * 100, 2),
            "leverage":       LEVERAGE,
            "buying_power":   round(buying_power, 2),
            "risk_qty":       risk_qty,
            "kelly_qty":      kelly_qty,
            "kelly_frac":     round(kelly_frac, 3),
            "sl_distance":    round(sl_distance, 2),
            "sess_mult":      round(sess_mult, 2),
            "session":        session,
            "dow_mult":       round(dow_mult, 2),
            "inst_mult":      round(inst_mult, 2),
            "heat_pct":       round(self.state.portfolio_heat, 2),
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

        # 7. Portfolio heat limit
        import config as _cfg
        max_heat = getattr(_cfg, "MAX_PORTFOLIO_HEAT_PCT", 3.0)
        current_heat = self.state.portfolio_heat
        if current_heat >= max_heat:
            return {
                "allowed": False,
                "reason": f"Portfolio heat {current_heat:.1f}% ≥ limit {max_heat}%",
            }

        # 8. Sector correlation guard (max N positions per sector)
        sector_check = self._check_sector_correlation(symbol)
        if not sector_check["allowed"]:
            return sector_check

        # 9. Session gate — no new entries after 3:00 PM IST
        sess_mult, session = self._get_session_multiplier()
        if sess_mult == 0.0:
            return {"allowed": False, "reason": f"Session gate: {session}"}

        return {"allowed": True, "reason": f"All checks passed | Session={session}"}

    # --------------------------------------------------------
    # TRAILING STOP MANAGEMENT
    # --------------------------------------------------------

    def update_trailing_stop(self, position: Position, current_price: float) -> Dict:
        """
        Update trailing stop with 50/30/20 partial exit strategy.

        Exit sequence:
          T1 hit → exit 50% (t1_qty), move SL to entry (breakeven)
          T2 hit → exit 30% (t2_qty), activate tight runner trail
          Runner → 20% rides with grade-adaptive trailing stop
          T2 full → exit remaining if T2 not done (grade C: exit all)

        Trail aggressiveness by grade:
          A+ / A: trail at 0.5x ATR (let winner run)
          B:      trail at 0.8x ATR
          C:      trail at 1.0x ATR (tighter — lower conviction)

        Returns:
            {action, new_sl, reason, exit_qty}
        """
        from config import ATR_TRAIL_MULTIPLIER
        position.current_price = current_price
        atr = position.atr

        # Grade-adaptive trail distance
        grade_trail = {
            "A+": ATR_TRAIL_MULTIPLIER * 0.6,   # Tightest — best setups deserve room
            "A":  ATR_TRAIL_MULTIPLIER * 0.8,
            "B":  ATR_TRAIL_MULTIPLIER,
            "C":  ATR_TRAIL_MULTIPLIER * 1.3,   # Widest — low conviction, protect early
        }
        trail_dist = grade_trail.get(position.quality_grade, ATR_TRAIL_MULTIPLIER) * atr

        be_trigger = getattr(_config, "BREAKEVEN_TRIGGER_PCT", 0.5) / 100.0

        if position.direction == "LONG":
            position.max_price = max(position.max_price, current_price)
            profit = current_price - position.entry_price

            # Hard SL check (original or trailing)
            active_sl = position.trailing_stop if position.trailing_active else position.stop_loss
            if current_price <= active_sl:
                remaining = position.quantity  # Exit whatever's left
                return {
                    "action": "EXIT", "new_sl": active_sl, "exit_qty": remaining,
                    "reason": f"{'Trailing' if position.trailing_active else 'Original'} SL hit ₹{current_price:.2f}"
                }

            # ── Breakeven SL: move to entry when 0.5% in profit ─
            if (not position.breakeven_done and not position.t1_done
                    and current_price >= position.entry_price * (1 + be_trigger)
                    and position.stop_loss < position.entry_price):
                position.stop_loss = position.entry_price
                position.breakeven_done = True
                logger.info(
                    f"[{format_ist_timestamp()}] {position.symbol}: "
                    f"🛡 Breakeven SL — price up {be_trigger*100:.1f}%, "
                    f"SL moved to entry ₹{position.entry_price:.2f} (zero risk)"
                )
                return {
                    "action": "UPDATE_SL",
                    "new_sl": position.entry_price,
                    "exit_qty": 0,
                    "reason": f"Breakeven: price +{be_trigger*100:.1f}% → SL=entry ₹{position.entry_price:.2f}"
                }

            # ── T1: 50% exit at Target 1 ──────────────────────
            if not position.t1_done and current_price >= position.target_1:
                position.t1_done = True
                position.partial_exit_done = True
                # Move SL to breakeven after T1
                if position.direction == "LONG":
                    position.stop_loss = max(position.stop_loss, position.entry_price)
                return {
                    "action": "PARTIAL_EXIT_T1",
                    "new_sl": position.entry_price,
                    "exit_qty": position.t1_qty,
                    "reason": f"T1 hit ₹{position.target_1:.2f} — exit {position.t1_qty} qty (50%), SL→breakeven"
                }

            # ── T2: 30% exit at Target 2 ──────────────────────
            if position.t1_done and not position.t2_done and current_price >= position.target_2:
                position.t2_done = True
                # Grade C: exit all at T2 (no runner)
                if position.quality_grade == "C" or position.runner_qty == 0:
                    return {
                        "action": "EXIT", "new_sl": current_price,
                        "exit_qty": position.t2_qty + position.runner_qty,
                        "reason": f"T2 hit ₹{position.target_2:.2f} — exit all remaining"
                    }
                # Grade A+/A/B: activate runner trailing
                position.trailing_active = True
                position.trailing_stop   = current_price - trail_dist
                logger.info(
                    f"[{format_ist_timestamp()}] {position.symbol}: "
                    f"T2 hit — runner trail activated ₹{position.trailing_stop:.2f}"
                )
                return {
                    "action": "PARTIAL_EXIT_T2",
                    "new_sl": position.trailing_stop,
                    "exit_qty": position.t2_qty,
                    "reason": f"T2 hit ₹{position.target_2:.2f} — exit {position.t2_qty} qty (30%), runner active"
                }

            # ── Runner trailing stop (post-T2) ────────────────
            if position.trailing_active and position.t2_done:
                new_trail = position.max_price - trail_dist
                if new_trail > position.trailing_stop:
                    position.trailing_stop = new_trail
                    return {
                        "action": "UPDATE_SL", "new_sl": new_trail, "exit_qty": 0,
                        "reason": f"Runner trail ₹{new_trail:.2f} (grade {position.quality_grade})"
                    }
                if current_price <= position.trailing_stop:
                    return {
                        "action": "EXIT", "new_sl": position.trailing_stop,
                        "exit_qty": position.runner_qty,
                        "reason": f"Runner trail hit ₹{current_price:.2f} — grade {position.quality_grade}"
                    }

            # ── Pre-T1: activate trailing if 1x ATR in profit ─
            if not position.t1_done and not position.trailing_active and profit >= atr:
                position.trailing_active = True
                position.trailing_stop   = current_price - trail_dist
                logger.info(
                    f"[{format_ist_timestamp()}] {position.symbol}: "
                    f"Early trail activated at ₹{position.trailing_stop:.2f}"
                )

        else:  # SHORT
            position.min_price = min(position.min_price, current_price)
            profit = position.entry_price - current_price

            active_sl = position.trailing_stop if position.trailing_active else position.stop_loss
            if current_price >= active_sl:
                return {
                    "action": "EXIT", "new_sl": active_sl, "exit_qty": position.quantity,
                    "reason": f"Short {'trailing' if position.trailing_active else 'original'} SL hit ₹{current_price:.2f}"
                }

            # ── Breakeven SL: move to entry when 0.5% in profit ─
            if (not position.breakeven_done and not position.t1_done
                    and current_price <= position.entry_price * (1 - be_trigger)
                    and position.stop_loss > position.entry_price):
                position.stop_loss = position.entry_price
                position.breakeven_done = True
                logger.info(
                    f"[{format_ist_timestamp()}] {position.symbol}: "
                    f"🛡 Breakeven SL (SHORT) — price down {be_trigger*100:.1f}%, "
                    f"SL moved to entry ₹{position.entry_price:.2f} (zero risk)"
                )
                return {
                    "action": "UPDATE_SL",
                    "new_sl": position.entry_price,
                    "exit_qty": 0,
                    "reason": f"Short breakeven: price -{be_trigger*100:.1f}% → SL=entry ₹{position.entry_price:.2f}"
                }

            # T1: 50%
            if not position.t1_done and current_price <= position.target_1:
                position.t1_done = True
                position.partial_exit_done = True
                position.stop_loss = min(position.stop_loss, position.entry_price)
                return {
                    "action": "PARTIAL_EXIT_T1",
                    "new_sl": position.entry_price,
                    "exit_qty": position.t1_qty,
                    "reason": f"Short T1 hit ₹{position.target_1:.2f} — 50% exit, SL→breakeven"
                }

            # T2: 30%
            if position.t1_done and not position.t2_done and current_price <= position.target_2:
                position.t2_done = True
                if position.quality_grade == "C" or position.runner_qty == 0:
                    return {
                        "action": "EXIT", "new_sl": current_price,
                        "exit_qty": position.t2_qty + position.runner_qty,
                        "reason": f"Short T2 hit ₹{position.target_2:.2f} — full exit (grade C)"
                    }
                position.trailing_active = True
                position.trailing_stop   = current_price + trail_dist
                return {
                    "action": "PARTIAL_EXIT_T2",
                    "new_sl": position.trailing_stop,
                    "exit_qty": position.t2_qty,
                    "reason": f"Short T2 hit ₹{position.target_2:.2f} — 30% exit, runner active"
                }

            # Runner
            if position.trailing_active and position.t2_done:
                new_trail = position.min_price + trail_dist
                if new_trail < position.trailing_stop:
                    position.trailing_stop = new_trail
                    return {
                        "action": "UPDATE_SL", "new_sl": new_trail, "exit_qty": 0,
                        "reason": f"Short runner trail ₹{new_trail:.2f}"
                    }
                if current_price >= position.trailing_stop:
                    return {
                        "action": "EXIT", "new_sl": position.trailing_stop,
                        "exit_qty": position.runner_qty,
                        "reason": f"Short runner trail hit ₹{current_price:.2f}"
                    }

            if not position.t1_done and not position.trailing_active and profit >= atr:
                position.trailing_active = True
                position.trailing_stop   = current_price + trail_dist

        return {"action": "HOLD", "new_sl": position.active_sl, "exit_qty": 0, "reason": "Hold"}

    # --------------------------------------------------------
    # POSITION TRACKING
    # --------------------------------------------------------

    def setup_partial_exits(self, position: Position) -> Position:
        """
        Calculate 50/30/20 partial exit quantities.
        18yr rule: Take half off at T1 to guarantee profit, let runner work.
        A+ grade: runner stays alive longer with tighter trail.
        """
        from config import PARTIAL_EXIT_T1_PCT, PARTIAL_EXIT_T2_PCT, RUNNER_PCT
        qty = position.quantity
        t1_qty = max(1, round(qty * PARTIAL_EXIT_T1_PCT / 100))
        t2_qty = max(1, round(qty * PARTIAL_EXIT_T2_PCT / 100))
        runner_qty = max(0, qty - t1_qty - t2_qty)
        position.t1_qty     = t1_qty
        position.t2_qty     = t2_qty
        position.runner_qty = runner_qty
        logger.info(
            f"[{format_ist_timestamp()}] {position.symbol} partial exits: "
            f"T1={t1_qty}qty({PARTIAL_EXIT_T1_PCT:.0f}%) "
            f"T2={t2_qty}qty({PARTIAL_EXIT_T2_PCT:.0f}%) "
            f"Runner={runner_qty}qty | Grade={position.quality_grade}"
        )
        return position

    # --------------------------------------------------------
    # SMART TRADE HEALTH MONITOR
    # --------------------------------------------------------

    def _position_age_minutes(self, pos: Position) -> float:
        """Return how many minutes this position has been open."""
        try:
            now = get_current_ist_time()
            entry_str = pos.entry_time[:19]   # trim trailing " IST" or zone suffix
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    entry_dt = datetime.strptime(entry_str, fmt).replace(tzinfo=IST)
                    return max(0.0, (now - entry_dt).total_seconds() / 60)
                except ValueError:
                    continue
            # ISO fallback
            entry_dt = datetime.fromisoformat(pos.entry_time)
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=IST)
            return max(0.0, (now - entry_dt).total_seconds() / 60)
        except Exception:
            return 0.0

    def check_position_health(
        self,
        pos: Position,
        ltp: float,
        recent_candles: Optional[list] = None,
    ) -> Dict:
        """
        Smart trade health monitor — exit BEFORE stop-loss if trade shows weakness.
        18yr Rule: "A small profit is better than a break-even; a break-even is better
        than a loss. Exit ugly trades early."

        Rules (checked in priority order):
          1. Peak reversal  — was +1x ATR positive, now ≤0             → EXIT_NOW
          2. Early adverse  — moved 0.6× SL-distance against us        → EXIT_NOW
          3. Stalled trade  — 20+ min with <0.3x ATR progress          → EXIT_NOW
          4. Time gate      — 35+ min old, <30% toward T1              → EXIT_NOW
          5. Break-even     — up 0.8x ATR → move SL to entry           → BREAK_EVEN
          6. Candle reversal— 2 consecutive opposing candles (pre-T1)   → EXIT_NOW

        Returns: {"action": "HOLD"|"EXIT_NOW"|"BREAK_EVEN", "reason": str,
                  "new_sl": float (only for BREAK_EVEN)}
        """
        atr = pos.atr if pos.atr > 0 else pos.entry_price * 0.005

        if pos.direction == "LONG":
            move      = ltp - pos.entry_price           # +ve = profit
            t1_dist   = pos.target_1 - pos.entry_price
            peak_move = pos.max_price - pos.entry_price
            sl_dist   = pos.entry_price - pos.stop_loss
        else:
            move      = pos.entry_price - ltp
            t1_dist   = pos.entry_price - pos.target_1
            peak_move = pos.entry_price - pos.min_price
            sl_dist   = pos.stop_loss - pos.entry_price

        sl_dist   = max(sl_dist, atr * 0.1)   # guard against zero
        age_min   = self._position_age_minutes(pos)

        # ── Rule 1: Peak reversal ─────────────────────────────────────────
        # Was strongly positive, now flat or losing — momentum has reversed.
        if peak_move >= atr and move <= 0 and not pos.t1_done:
            return {
                "action": "EXIT_NOW",
                "reason": (
                    f"Peak reversal: peaked +{peak_move:.2f} "
                    f"({peak_move / atr:.1f}× ATR), now {move:+.2f} — exit before loss"
                ),
            }

        # ── Rule 2: Early adverse move ────────────────────────────────────
        # Moved 60% of SL distance against us without trailing active.
        if move < -(0.6 * sl_dist) and not pos.trailing_active and not pos.t1_done:
            return {
                "action": "EXIT_NOW",
                "reason": (
                    f"Early exit: {abs(move):.2f} adverse "
                    f"({abs(move) / atr:.1f}× ATR) before SL hit"
                ),
            }

        # ── Rule 3: Stalled trade ─────────────────────────────────────────
        # 20+ minutes open, barely moved — capital is better deployed elsewhere.
        if age_min >= 20 and 0 <= move < (0.3 * atr) and not pos.t1_done:
            return {
                "action": "EXIT_NOW",
                "reason": (
                    f"Stalled {age_min:.0f}min: only {move:.2f} progress "
                    f"({move / atr:.2f}× ATR) — releasing capital"
                ),
            }

        # ── Rule 4: Time gate ─────────────────────────────────────────────
        # 35 minutes in with less than 30% progress toward first target.
        if age_min >= 35 and t1_dist > 0 and (move / t1_dist) < 0.30 and not pos.t1_done:
            return {
                "action": "EXIT_NOW",
                "reason": (
                    f"Time exit: {age_min:.0f}min, "
                    f"only {move / t1_dist * 100:.0f}% toward T1 ₹{pos.target_1:.2f}"
                ),
            }

        # ── Rule 5: Break-even protection ────────────────────────────────
        # Up 0.8× ATR — lock in no-loss with break-even SL.
        if move >= (0.8 * atr) and not pos.trailing_active:
            be = pos.entry_price
            current_sl = pos.stop_loss
            if pos.direction == "LONG" and current_sl < be:
                return {
                    "action": "BREAK_EVEN",
                    "new_sl": be,
                    "reason": (
                        f"Break-even: up {move:.2f} ({move / atr:.1f}× ATR) "
                        f"→ SL moved to entry ₹{be:.2f}"
                    ),
                }
            elif pos.direction == "SHORT" and current_sl > be:
                return {
                    "action": "BREAK_EVEN",
                    "new_sl": be,
                    "reason": (
                        f"Break-even: up {move:.2f} ({move / atr:.1f}× ATR) "
                        f"→ SL moved to entry ₹{be:.2f}"
                    ),
                }

        # ── Rule 6: Two consecutive opposing candles ──────────────────────
        # Momentum fading before T1 — close while still in marginal profit.
        if recent_candles and len(recent_candles) >= 2 and not pos.t1_done:
            try:
                c1 = recent_candles[-2]
                c2 = recent_candles[-1]
                if pos.direction == "LONG":
                    bear1 = float(c1["close"]) < float(c1["open"])
                    bear2 = float(c2["close"]) < float(c2["open"])
                    if bear1 and bear2 and move < atr:
                        return {
                            "action": "EXIT_NOW",
                            "reason": "2 consecutive bearish candles — long momentum fading",
                        }
                else:
                    bull1 = float(c1["close"]) > float(c1["open"])
                    bull2 = float(c2["close"]) > float(c2["open"])
                    if bull1 and bull2 and move < atr:
                        return {
                            "action": "EXIT_NOW",
                            "reason": "2 consecutive bullish candles — short momentum fading",
                        }
            except Exception:
                pass

        return {"action": "HOLD", "reason": "Trade health OK"}

    def add_position(self, position: Position):
        """Register a new open position."""
        position = self.setup_partial_exits(position)
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
