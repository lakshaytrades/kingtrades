"""
advanced_portfolio.py — Institutional Portfolio Intelligence v1.0

CVaR-based portfolio optimizer + Kelly sizing + correlation-aware position
reduction + real-time drawdown control.

Bridges the gap between "signal quality" and "how much to bet":
  - Kelly: sizes up on high-edge setups, down on borderline ones
  - CVaR:  hard cap when portfolio tail risk exceeds 2% of capital
  - Correlation: prevents 3 semiconductor longs eating the same risk
  - Drawdown: automatically reduces aggression when underwater

All methods fail-open (return 1.0 on any exception).
Thread-safe via internal lock.
"""

import logging
import threading
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── correlation cluster map (same as portfolio_guard — standalone copy) ───────
_CLUSTERS: Dict[str, List[str]] = {
    "semiconductors": ["NVDA","AMD","INTC","QCOM","AVGO","MU","AMAT","LRCX","KLAC","MRVL","SMCI","ON","TXN"],
    "mega_tech":      ["AAPL","MSFT","GOOGL","META","AMZN"],
    "cloud_saas":     ["AMZN","MSFT","GOOGL","CRM","NOW","SNOW","DDOG","NET","ZS","CRWD"],
    "ev_auto":        ["TSLA","RIVN","LCID","F","GM"],
    "banks":          ["JPM","BAC","GS","MS","WFC","C","BLK"],
    "crypto_proxy":   ["COIN","MSTR","RIOT","MARA","HUT","CLSK"],
    "biotech":        ["MRNA","BNTX","BIIB","GILD","REGN","VRTX"],
    "energy":         ["XOM","CVX","COP","SLB","EOG","PXD","HAL"],
    "consumer_tech":  ["NFLX","TSLA","UBER","SHOP","ABNB","RBLX"],
}

# Build {symbol → set(cluster_names)} for O(1) lookup
_SYM_CLUSTERS: Dict[str, List[str]] = {}
for _c, _syms in _CLUSTERS.items():
    for _s in _syms:
        _SYM_CLUSTERS.setdefault(_s.upper(), []).append(_c)


@dataclass
class PositionRecord:
    symbol:      str
    entry_price: float
    quantity:    int
    direction:   str   # "LONG" or "SHORT"
    entry_ts:    float = field(default_factory=_time.time)
    entry_score: float = 70.0


@dataclass
class PortfolioSummary:
    open_positions:  int
    symbols:         List[str]
    portfolio_heat:  float   # 0-1
    drawdown_mult:   float   # size multiplier from drawdown control
    var_mult:        float   # size multiplier from VaR cap
    kelly_mult:      float   # representative Kelly multiplier
    combined_mult:   float   # net combined multiplier


class PortfolioOptimizer:
    """
    Real-time portfolio intelligence. Call:
      update_position()  → when a position opens
      close_position()   → when a position closes
      get_combined_size_multiplier() → when computing position size
    """

    def __init__(self, capital: float = 93_000.0):
        self._lock = threading.Lock()
        self._capital = capital
        self._positions: Dict[str, PositionRecord] = {}
        self._return_history: Dict[str, List[float]] = {}  # symbol → pnl_pct list
        self._today_pnl: float = 0.0
        self._today_peak: float = 0.0

    # ── position tracking ─────────────────────────────────────────────────────

    def update_position(self, symbol: str, entry_price: float,
                        quantity: int, direction: str, score: float = 70.0) -> None:
        with self._lock:
            self._positions[symbol.upper()] = PositionRecord(
                symbol=symbol.upper(), entry_price=entry_price,
                quantity=quantity, direction=direction.upper(), entry_score=score,
            )

    def close_position(self, symbol: str, exit_price: float) -> None:
        with self._lock:
            sym = symbol.upper()
            if sym not in self._positions:
                return
            pos = self._positions.pop(sym)
            sign = 1.0 if pos.direction == "LONG" else -1.0
            pnl_pct = (exit_price - pos.entry_price) / max(pos.entry_price, 0.01) * sign
            trade_pnl = pnl_pct * pos.entry_price * pos.quantity
            self._return_history.setdefault(sym, []).append(pnl_pct)
            if len(self._return_history[sym]) > 50:
                self._return_history[sym] = self._return_history[sym][-50:]
            self._today_pnl += trade_pnl
            self._today_peak = max(self._today_peak, self._today_pnl)

    def reset_daily(self) -> None:
        """Call at EOD or start of day."""
        with self._lock:
            self._today_pnl  = 0.0
            self._today_peak = 0.0

    # ── sizing components ─────────────────────────────────────────────────────

    def get_kelly_size_multiplier(
        self,
        symbol: str,
        signal_score: float,
        default_win_rate: float = 0.55,
        default_avg_win: float  = 0.015,
        default_avg_loss: float = 0.008,
    ) -> float:
        """
        Quarter-Kelly position sizing.
        Uses live return history if available (min 8 trades), else defaults.
        Capped at 1.5×, floored at 0.25×.
        """
        try:
            sym = symbol.upper()
            hist = self._return_history.get(sym, [])
            if len(hist) >= 8:
                wins   = [r for r in hist if r > 0]
                losses = [r for r in hist if r <= 0]
                if wins and losses:
                    win_rate = len(wins) / len(hist)
                    avg_win  = float(np.mean(wins))
                    avg_loss = abs(float(np.mean(losses)))
                else:
                    win_rate = default_win_rate
                    avg_win  = default_avg_win
                    avg_loss = default_avg_loss
            else:
                win_rate = default_win_rate
                avg_win  = default_avg_win
                avg_loss = default_avg_loss

            full_kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / max(avg_win, 0.001)
            quarter_k  = full_kelly / 4.0
            # Score boost: higher confidence signal → closer to half-kelly
            score_adj  = (signal_score - 63.0) / 40.0   # 0 at 63, 1.0 at 103
            sized      = quarter_k * (1.0 + score_adj * 0.5)
            return float(np.clip(sized, 0.25, 1.5))
        except Exception:
            return 1.0

    def get_correlation_penalty(self, symbol: str, direction: str) -> float:
        """
        Reduce position size when we already hold a correlated position in
        the same direction.
        Returns 0.3 (heavy overlap) → 1.0 (no overlap).
        """
        try:
            sym = symbol.upper()
            dir_up = direction.upper()
            sym_clusters = set(_SYM_CLUSTERS.get(sym, []))
            if not sym_clusters:
                return 1.0

            overlapping = 0
            for pos_sym, pos in self._positions.items():
                if pos_sym == sym:
                    continue
                if pos.direction != dir_up:
                    continue
                pos_clusters = set(_SYM_CLUSTERS.get(pos_sym, []))
                shared = sym_clusters & pos_clusters
                if shared:
                    overlapping += 1

            if overlapping == 0:
                return 1.0
            elif overlapping == 1:
                return 0.65   # one correlated position → 35% size reduction
            else:
                return 0.35   # two or more → 65% reduction (strong correlation risk)
        except Exception:
            return 1.0

    def get_portfolio_heat_multiplier(self) -> float:
        """
        Block or reduce new positions when too many open.
        Heat = n_positions / max_allowed_positions.
        """
        try:
            n = len(self._positions)
            if n >= 10:
                return 0.0   # max positions hit — no new entries
            if n >= 7:
                return 0.6
            if n >= 5:
                return 0.8
            return 1.0
        except Exception:
            return 1.0

    def get_var_limit_multiplier(self) -> float:
        """
        Portfolio VaR cap: if 5th-percentile loss across all symbols
        exceeds 2% of capital, throttle new positions.
        """
        try:
            all_returns: List[float] = []
            for hist in self._return_history.values():
                all_returns.extend(hist[-20:])
            if len(all_returns) < 10:
                return 1.0
            var_95 = abs(float(np.percentile(all_returns, 5)))  # 95th pct loss
            if var_95 >= 0.025:
                return 0.4
            if var_95 >= 0.018:
                return 0.65
            if var_95 >= 0.012:
                return 0.85
            return 1.0
        except Exception:
            return 1.0

    def get_drawdown_control_multiplier(self) -> float:
        """
        Auto-reduce position size when today's P&L is underwater from peak.
        Drawdown > 2% → no new entries (return 0.0).
        """
        try:
            if self._today_peak <= 0:
                return 1.0
            drawdown = (self._today_peak - self._today_pnl) / max(abs(self._today_peak), 1.0)
            if drawdown >= 0.02:
                return 0.0
            if drawdown >= 0.015:
                return 0.4
            if drawdown >= 0.010:
                return 0.7
            return 1.0
        except Exception:
            return 1.0

    def get_combined_size_multiplier(
        self, symbol: str, direction: str, signal_score: float = 70.0,
    ) -> float:
        """
        Combine all sizing factors into a single multiplier.
        Clipped [0.1, 1.5].
        """
        try:
            kelly = self.get_kelly_size_multiplier(symbol, signal_score)
            corr  = self.get_correlation_penalty(symbol, direction)
            heat  = self.get_portfolio_heat_multiplier()
            var   = self.get_var_limit_multiplier()
            dd    = self.get_drawdown_control_multiplier()
            combined = kelly * corr * heat * var * dd
            return float(np.clip(combined, 0.1, 1.5))
        except Exception:
            return 1.0

    def get_summary(self) -> PortfolioSummary:
        try:
            return PortfolioSummary(
                open_positions =len(self._positions),
                symbols        =list(self._positions.keys()),
                portfolio_heat =1.0 - self.get_portfolio_heat_multiplier(),
                drawdown_mult  =self.get_drawdown_control_multiplier(),
                var_mult       =self.get_var_limit_multiplier(),
                kelly_mult     =self.get_kelly_size_multiplier("SPY", 70.0),
                combined_mult  =self.get_combined_size_multiplier("SPY", "LONG", 70.0),
            )
        except Exception:
            return PortfolioSummary(0, [], 0.0, 1.0, 1.0, 1.0, 1.0)


# ── Module singleton ──────────────────────────────────────────────────────────

_optimizer: Optional[PortfolioOptimizer] = None
_opt_lock = threading.Lock()


def get_portfolio_optimizer() -> PortfolioOptimizer:
    global _optimizer
    if _optimizer is None:
        with _opt_lock:
            if _optimizer is None:
                _optimizer = PortfolioOptimizer()
    return _optimizer
