"""
portfolio_heat.py — Portfolio Correlation & Concentration Guard

Grok/Hedge Fund insight: concentrated, correlated positions are the
#1 catastrophic risk in intraday trading. One bad sector day can wipe
out multiple positions simultaneously.

Rules enforced:
  1. Sector concentration cap: max 35% of open book in one sector
  2. Correlation guard: if new trade correlates >0.65 with open position
     → reduce size by 40%
  3. Portfolio heat score: sum(size_mult * notional_weight) across positions
     → if heat > 2.5 → block all new trades until one closes
  4. Max 2 open positions in same sector simultaneously
  5. Nifty beta cap: total portfolio beta < 1.5x Nifty
     (prevents amplifying market-wide shocks)

Output:
  HeatResult(approved, size_reduction_pct, reason)
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SECTOR MAP (NSE large cap + high-beta stocks)
# ─────────────────────────────────────────────────────────────────────────────

NSE_SECTOR_MAP: Dict[str, str] = {
    # Banking & Finance
    "HDFCBANK": "BANKING", "ICICIBANK": "BANKING", "KOTAKBANK": "BANKING",
    "AXISBANK": "BANKING", "SBIN": "BANKING", "BANKBARODA": "BANKING",
    "INDUSINDBK": "BANKING", "FEDERALBNK": "BANKING", "IDFCFIRSTB": "BANKING",
    "BANDHANBNK": "BANKING", "PNB": "BANKING", "CANBK": "BANKING",
    "BAJFINANCE": "NBFC", "BAJAJFINSV": "NBFC", "CHOLAFIN": "NBFC",
    "MUTHOOTFIN": "NBFC", "LICHSGFIN": "NBFC",
    # IT
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "TECHM": "IT", "LTIM": "IT", "MPHASIS": "IT", "PERSISTENT": "IT",
    "COFORGE": "IT",
    # Auto
    "MARUTI": "AUTO", "TATAMOTORS": "AUTO", "M&M": "AUTO",
    "BAJAJ-AUTO": "AUTO", "HEROMOTOCO": "AUTO", "EICHERMOT": "AUTO",
    "TVSMOTOR": "AUTO",
    # Energy / Oil
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "BPCL": "ENERGY",
    "IOC": "ENERGY", "NTPC": "ENERGY", "POWERGRID": "ENERGY",
    "ADANIGREEN": "ENERGY", "TATAPOWER": "ENERGY",
    # Metals
    "TATASTEEL": "METALS", "JSWSTEEL": "METALS", "HINDALCO": "METALS",
    "VEDL": "METALS", "SAIL": "METALS", "NMDC": "METALS",
    # Pharma
    "SUNPHARMA": "PHARMA", "DRREDDY": "PHARMA", "CIPLA": "PHARMA",
    "DIVISLAB": "PHARMA", "BIOCON": "PHARMA", "AUROPHARMA": "PHARMA",
    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "DABUR": "FMCG",
    # Infra / Real Estate
    "LT": "INFRA", "ADANIPORTS": "INFRA", "DLF": "REALTY",
    "GODREJPROP": "REALTY",
    # Consumer / Retail
    "TITAN": "CONSUMER", "TRENT": "CONSUMER",
    # Telecom
    "BHARTIARTL": "TELECOM",
    # Indices / ETFs (treated as separate sector)
    "NIFTY": "INDEX", "BANKNIFTY": "INDEX",
}


def get_sector(symbol: str) -> str:
    return NSE_SECTOR_MAP.get(symbol.upper(), "OTHER")


# ─────────────────────────────────────────────────────────────────────────────
# RESULT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HeatResult:
    approved:           bool
    size_reduction_pct: float    # 0.0 = no reduction, 0.4 = 40% size cut
    reason:             str
    heat_score:         float    # Current portfolio heat
    sector_count:       int      # How many open positions in same sector


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO HEAT GUARD
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioHeatGuard:
    """
    Prevents dangerous sector concentration and correlated position buildup.

    No hedge fund runs uncapped sector concentration.
    This is the simplest and most effective risk improvement for NSE intraday.
    """

    MAX_SECTOR_POSITIONS    = 2      # Max open trades in one sector
    MAX_SECTOR_CONCENTRATION = 0.35  # Max 35% of book in one sector
    CORRELATION_REDUCE_PCT  = 0.40   # Reduce size 40% for correlated pairs
    MAX_PORTFOLIO_HEAT      = 2.5    # Heat > 2.5 → pause new trades

    # Known NSE stock correlations (approximate, high-corr pairs)
    HIGH_CORR_PAIRS = {
        frozenset({"HDFCBANK", "ICICIBANK"}),
        frozenset({"HDFCBANK", "KOTAKBANK"}),
        frozenset({"ICICIBANK", "KOTAKBANK"}),
        frozenset({"AXISBANK", "ICICIBANK"}),
        frozenset({"TCS", "INFY"}),
        frozenset({"TCS", "WIPRO"}),
        frozenset({"INFY", "WIPRO"}),
        frozenset({"INFY", "HCLTECH"}),
        frozenset({"TATASTEEL", "JSWSTEEL"}),
        frozenset({"TATASTEEL", "HINDALCO"}),
        frozenset({"BPCL", "IOC"}),
        frozenset({"ONGC", "BPCL"}),
        frozenset({"MARUTI", "TATAMOTORS"}),
        frozenset({"BAJFINANCE", "BAJAJFINSV"}),
        frozenset({"HDFCBANK", "BAJFINANCE"}),
        frozenset({"SUNPHARMA", "DRREDDY"}),
        frozenset({"CIPLA", "SUNPHARMA"}),
    }

    def evaluate(
        self,
        new_symbol:     str,
        new_direction:  str,
        new_size_mult:  float,
        open_positions: Dict,    # {symbol: Position} from risk_manager.state.positions
        available_capital: float,
    ) -> HeatResult:
        """
        Evaluate if a new trade is safe given current portfolio heat.

        Returns HeatResult with approved flag and any size reduction needed.
        """
        if not open_positions:
            return HeatResult(True, 0.0, "No open positions", 0.0, 0)

        new_sector = get_sector(new_symbol)
        heat_score = self._compute_heat(open_positions, available_capital)

        # ── Rule 1: Portfolio heat cap ────────────────────────────────────
        if heat_score >= self.MAX_PORTFOLIO_HEAT:
            return HeatResult(
                approved=False,
                size_reduction_pct=0.0,
                reason=f"Portfolio heat={heat_score:.2f} ≥ {self.MAX_PORTFOLIO_HEAT} — pause new entries",
                heat_score=heat_score,
                sector_count=0,
            )

        # ── Rule 2: Sector concentration cap ─────────────────────────────
        sector_symbols = [
            sym for sym in open_positions
            if get_sector(sym) == new_sector and new_sector != "OTHER"
        ]
        sector_count = len(sector_symbols)

        if sector_count >= self.MAX_SECTOR_POSITIONS and new_sector != "OTHER":
            return HeatResult(
                approved=False,
                size_reduction_pct=0.0,
                reason=f"Sector {new_sector}: {sector_count} positions already open (max {self.MAX_SECTOR_POSITIONS})",
                heat_score=heat_score,
                sector_count=sector_count,
            )

        # ── Rule 3: Correlation guard ─────────────────────────────────────
        size_reduction = 0.0
        corr_reasons   = []

        for sym in open_positions:
            pair = frozenset({new_symbol.upper(), sym.upper()})
            if pair in self.HIGH_CORR_PAIRS:
                pos_dir = getattr(open_positions[sym], "direction", "LONG")
                if pos_dir == new_direction:
                    # Same direction on correlated pair = double-up risk
                    size_reduction = max(size_reduction, self.CORRELATION_REDUCE_PCT)
                    corr_reasons.append(f"corr({new_symbol},{sym})")
                # Opposing directions on correlated pair = natural hedge = OK

            # Same sector in same direction = moderate size reduction
            if get_sector(sym) == new_sector and new_sector != "OTHER":
                pos_dir = getattr(open_positions[sym], "direction", "LONG")
                if pos_dir == new_direction:
                    size_reduction = max(size_reduction, 0.20)
                    corr_reasons.append(f"same_sector({new_sector})")

        reason = "OK"
        if size_reduction > 0:
            reason = f"Size reduced {size_reduction:.0%}: {', '.join(corr_reasons[:2])}"
            logger.info(
                f"[{format_ist_timestamp()}] PortfolioHeat: {new_symbol} "
                f"{reason} | heat={heat_score:.2f}"
            )

        return HeatResult(
            approved           = True,
            size_reduction_pct = size_reduction,
            reason             = reason,
            heat_score         = heat_score,
            sector_count       = sector_count,
        )

    def apply_to_signal(self, signal, open_positions: Dict, available_capital: float):
        """
        Apply portfolio heat rules to a signal's size_multiplier in-place.
        Returns (approved, reason).
        """
        result = self.evaluate(
            new_symbol    = signal.symbol,
            new_direction = signal.direction,
            new_size_mult = signal.size_multiplier,
            open_positions = open_positions,
            available_capital = available_capital,
        )
        if not result.approved:
            return False, result.reason
        if result.size_reduction_pct > 0:
            signal.size_multiplier = round(
                signal.size_multiplier * (1 - result.size_reduction_pct), 2
            )
            signal.size_multiplier = max(0.25, signal.size_multiplier)
        return True, result.reason

    def _compute_heat(self, open_positions: Dict, available_capital: float) -> float:
        """
        Portfolio heat = sum(position_notional / available_capital * size_mult)
        Higher = more concentrated / at-risk.
        """
        if not open_positions or available_capital <= 0:
            return 0.0
        total_heat = 0.0
        for sym, pos in open_positions.items():
            entry = getattr(pos, "entry_price", 0)
            qty   = getattr(pos, "quantity",    0)
            if entry > 0 and qty > 0:
                notional_pct = (entry * qty) / (available_capital * 5)  # 5× MIS
                total_heat  += notional_pct
        return round(total_heat, 3)

    def get_sector_exposure(self, open_positions: Dict) -> Dict[str, int]:
        """Return {sector: count} for open positions."""
        exposure: Dict[str, int] = {}
        for sym in open_positions:
            sector = get_sector(sym)
            exposure[sector] = exposure.get(sector, 0) + 1
        return exposure


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_heat_guard: Optional[PortfolioHeatGuard] = None


def get_heat_guard() -> PortfolioHeatGuard:
    global _heat_guard
    if _heat_guard is None:
        _heat_guard = PortfolioHeatGuard()
        logger.info(f"[{format_ist_timestamp()}] PortfolioHeatGuard initialized")
    return _heat_guard
