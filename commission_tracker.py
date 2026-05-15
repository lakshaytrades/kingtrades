"""
commission_tracker.py — Round-Trip Trade Cost Model

Tracks and reports actual trading costs: commission, SEC fees, FINRA TAF.
Used to give P&L estimates that match real brokerage statements.

Alpaca Commission Schedule (2024):
  - Equities: $0 commission (zero-commission)
  - SEC Fee: $27.80 per $1M of sales (0.00278%)
  - FINRA TAF: $0.000166 per share (buys/sells), max $8.30 per trade
  - Exchange fees: negligible (absorbed by Alpaca)
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# US REGULATORY COST CONSTANTS (2024 rates)
# ─────────────────────────────────────────────────────────────────────────────

SEC_FEE_RATE   = 0.0000278   # $27.80 per $1M of sale proceeds
FINRA_TAF_RATE = 0.000166    # per share (both buys and sells)
FINRA_TAF_MAX  = 8.30        # max per trade leg

# Alpaca is zero-commission
ALPACA_COMMISSION = 0.0


@dataclass
class TradeCost:
    """Actual costs for one round-trip trade."""
    symbol:         str
    quantity:       int
    entry_price:    float
    exit_price:     float
    direction:      str        # "LONG" or "SHORT"

    # Calculated fields
    sec_fee:        float = 0.0
    finra_taf:      float = 0.0
    commission:     float = 0.0
    total_cost:     float = 0.0
    cost_pct:       float = 0.0   # total_cost as % of trade value

    gross_pnl:      float = 0.0
    net_pnl:        float = 0.0

    def __post_init__(self):
        trade_value  = self.entry_price * self.quantity
        exit_value   = self.exit_price  * self.quantity

        # SEC fee applies to sell proceeds only
        sell_value   = exit_value if self.direction == "LONG" else trade_value
        self.sec_fee = round(sell_value * SEC_FEE_RATE, 4)

        # FINRA TAF on both legs (buy + sell)
        self.finra_taf = round(
            min(self.quantity * FINRA_TAF_RATE, FINRA_TAF_MAX) * 2, 4
        )

        self.commission  = ALPACA_COMMISSION
        self.total_cost  = round(self.sec_fee + self.finra_taf + self.commission, 4)
        self.cost_pct    = round(self.total_cost / trade_value * 100, 4) if trade_value > 0 else 0

        # P&L
        if self.direction == "LONG":
            self.gross_pnl = round((self.exit_price - self.entry_price) * self.quantity, 4)
        else:
            self.gross_pnl = round((self.entry_price - self.exit_price) * self.quantity, 4)

        self.net_pnl = round(self.gross_pnl - self.total_cost, 4)

    def summary(self) -> str:
        return (
            f"{self.symbol} {self.direction} {self.quantity}sh | "
            f"Gross: ${self.gross_pnl:+.2f} | "
            f"Costs: ${self.total_cost:.4f} (SEC ${self.sec_fee:.4f} + TAF ${self.finra_taf:.4f}) | "
            f"Net: ${self.net_pnl:+.2f}"
        )


class CommissionTracker:
    """
    Tracks all trade costs for the session.
    Provides daily totals and breakeven analysis.
    """

    def __init__(self):
        self._trades: List[TradeCost] = []

    def record_trade(
        self,
        symbol:      str,
        quantity:    int,
        entry_price: float,
        exit_price:  float,
        direction:   str = "LONG",
    ) -> TradeCost:
        cost = TradeCost(
            symbol=symbol, quantity=quantity,
            entry_price=entry_price, exit_price=exit_price,
            direction=direction
        )
        self._trades.append(cost)
        logger.info(f"[{format_ist_timestamp()}] CommissionTracker: {cost.summary()}")
        return cost

    def estimate_cost(
        self,
        symbol:      str,
        quantity:    int,
        entry_price: float,
        target_price: float,
        sl_price:    float,
        direction:   str = "LONG",
    ) -> Dict:
        """
        Estimate costs and realistic P&L BEFORE placing a trade.
        Returns dict with win_net_pnl, loss_net_pnl, breakeven_move_pct.
        """
        # Win scenario
        win = TradeCost(symbol, quantity, entry_price, target_price, direction)
        # Loss scenario
        loss = TradeCost(symbol, quantity, entry_price, sl_price, direction)

        trade_value = entry_price * quantity

        # Breakeven: how much price must move to cover all costs
        total_round_trip = win.total_cost  # costs are similar in both scenarios
        if direction == "LONG":
            breakeven_price = entry_price + (total_round_trip / quantity)
        else:
            breakeven_price = entry_price - (total_round_trip / quantity)

        breakeven_pct = abs(breakeven_price - entry_price) / entry_price * 100

        return {
            "win_gross":        round(win.gross_pnl, 2),
            "win_net":          round(win.net_pnl, 2),
            "loss_gross":       round(loss.gross_pnl, 2),
            "loss_net":         round(loss.net_pnl, 2),
            "total_cost":       round(win.total_cost, 4),
            "cost_pct":         round(win.cost_pct, 4),
            "breakeven_move_pct": round(breakeven_pct, 4),
            "realistic_rr":     round(win.net_pnl / abs(loss.net_pnl), 2) if loss.net_pnl != 0 else 0,
            "sec_fee":          round(win.sec_fee, 4),
            "finra_taf":        round(win.finra_taf, 4),
        }

    # ─────────────────────────────────────────────────────────────────────
    # SESSION TOTALS
    # ─────────────────────────────────────────────────────────────────────

    def session_totals(self) -> Dict:
        if not self._trades:
            return {"trades": 0, "total_cost": 0.0, "gross_pnl": 0.0, "net_pnl": 0.0}

        total_cost  = sum(t.total_cost  for t in self._trades)
        gross_pnl   = sum(t.gross_pnl   for t in self._trades)
        net_pnl     = sum(t.net_pnl     for t in self._trades)
        total_value = sum(t.entry_price * t.quantity for t in self._trades)

        return {
            "trades":       len(self._trades),
            "total_cost":   round(total_cost, 4),
            "cost_pct_avg": round(total_cost / total_value * 100, 4) if total_value > 0 else 0,
            "gross_pnl":    round(gross_pnl, 2),
            "net_pnl":      round(net_pnl, 2),
            "sec_fees":     round(sum(t.sec_fee    for t in self._trades), 4),
            "finra_tafs":   round(sum(t.finra_taf  for t in self._trades), 4),
        }

    def reset(self):
        self._trades.clear()


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_tracker: Optional[CommissionTracker] = None


def get_commission_tracker() -> CommissionTracker:
    global _tracker
    if _tracker is None:
        _tracker = CommissionTracker()
    return _tracker
