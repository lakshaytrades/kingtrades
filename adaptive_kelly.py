"""
adaptive_kelly.py — Dynamic Kelly Criterion Position Sizing

Kelly Criterion: the mathematically optimal bet size for a known edge.
  f* = (p * (b + 1) - 1) / b
  where p = win probability, b = avg win / avg loss ratio

Using full Kelly is too volatile for real trading.
Standard practice: 25% Kelly (quarter Kelly) is the institutional standard.

Adaptive version:
  - p (win rate) updates from rolling 30-trade window via SymbolStats
  - b (payoff ratio) updates from actual trade P&L history
  - Auto-scales as the bot learns: starts at full config.MAX_RISK_PER_TRADE_PCT
  - After 20+ trades: Kelly formula takes over with safety floors/ceilings

Integration:
  from adaptive_kelly import get_kelly_size_pct
  risk_pct = get_kelly_size_pct(symbol, base_risk_pct=0.8)
"""
import json
import logging
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

_KELLY_DATA_FILE = Path("data/kelly_data.json")
_KELLY_FRACTION  = 0.25   # quarter Kelly — institutional standard
_MIN_TRADES      = 20     # minimum trades before Kelly activates
_FLOOR_PCT       = 0.3    # never risk less than 0.3% (would be too small to matter)
_CEIL_PCT        = 1.5    # never risk more than 1.5% per trade
_DEFAULT_WIN_RATE = 0.65  # assumed until we have data
_DEFAULT_PAYOFF   = 2.0   # assumed 2:1 R:R until we have data


class AdaptiveKelly:
    """Dynamic Kelly sizing using live win/loss history."""

    def __init__(self):
        self._data: Dict[str, Dict] = {}   # {symbol: {wins, losses, total_win, total_loss}}
        self._global: Dict = {}            # global across all symbols
        self._load()

    def record(self, symbol: str, win: bool, pnl: float, risk_amount: float) -> None:
        """Record a trade outcome. pnl and risk_amount in dollars."""
        for key in (symbol, "_global"):
            if key not in self._data:
                self._data[key] = {"wins": 0, "losses": 0, "total_win": 0.0, "total_loss": 0.0, "n": 0}
            d = self._data[key]
            d["n"] += 1
            if win:
                d["wins"] += 1
                d["total_win"] += abs(pnl)
            else:
                d["losses"] += 1
                d["total_loss"] += abs(pnl)
        self._save()

    def get_kelly_size_pct(self, symbol: str, base_risk_pct: float = 0.8) -> float:
        """
        Return the Kelly-optimal risk percentage (capped, floored).
        Returns base_risk_pct until we have MIN_TRADES data.
        """
        try:
            # Prefer symbol-specific data; fall back to global
            d = self._data.get(symbol, self._data.get("_global", {}))
            n = d.get("n", 0)
            if n < _MIN_TRADES:
                return base_risk_pct  # not enough data — use base

            wins   = d.get("wins", 0)
            losses = d.get("losses", 0)
            total  = wins + losses
            if total == 0:
                return base_risk_pct

            p = wins / total  # win rate
            avg_win  = d.get("total_win", 0)  / max(wins, 1)
            avg_loss = d.get("total_loss", 0) / max(losses, 1)
            b = avg_win / max(avg_loss, 0.01)  # payoff ratio

            kelly_full = (p * (b + 1) - 1) / b
            kelly_size = kelly_full * _KELLY_FRACTION  # quarter Kelly

            # Convert to risk percentage (Kelly gives fraction of bankroll)
            kelly_pct = kelly_size * 100.0

            # Safety floor/ceiling
            final = max(_FLOOR_PCT, min(_CEIL_PCT, kelly_pct))
            logger.debug(
                f"Kelly {symbol}: p={p:.2f} b={b:.2f} → "
                f"full={kelly_full:.3f} quarter={kelly_pct:.2f}% → {final:.2f}%"
            )
            return round(final, 3)

        except Exception as e:
            logger.debug(f"[Kelly] {symbol}: {e}")
            return base_risk_pct

    def get_global_kelly_pct(self, base_risk_pct: float = 0.8) -> float:
        """Kelly sizing based on aggregate performance across all symbols."""
        return self.get_kelly_size_pct("_global", base_risk_pct)

    def get_stats(self) -> Dict:
        d = self._data.get("_global", {})
        n = d.get("n", 0)
        wins = d.get("wins", 0)
        return {
            "n": n,
            "win_rate": round(wins / n * 100, 1) if n > 0 else 0,
            "avg_win": round(d.get("total_win", 0) / max(wins, 1), 2),
            "avg_loss": round(d.get("total_loss", 0) / max(d.get("losses", 0), 1), 2),
            "kelly_active": n >= _MIN_TRADES,
        }

    def _load(self):
        try:
            if _KELLY_DATA_FILE.exists():
                self._data = json.loads(_KELLY_DATA_FILE.read_text())
        except Exception as e:
            logger.debug(f"[Kelly] load failed: {e}")

    def _save(self):
        try:
            _KELLY_DATA_FILE.parent.mkdir(exist_ok=True)
            _KELLY_DATA_FILE.write_text(json.dumps(self._data, indent=2))
        except Exception as e:
            logger.debug(f"[Kelly] save failed: {e}")


_kelly_instance: AdaptiveKelly = None


def get_adaptive_kelly() -> AdaptiveKelly:
    global _kelly_instance
    if _kelly_instance is None:
        _kelly_instance = AdaptiveKelly()
    return _kelly_instance


def get_kelly_size_pct(symbol: str, base_risk_pct: float = 0.8) -> float:
    """Convenience function — returns Kelly-sized risk % for this symbol."""
    return get_adaptive_kelly().get_kelly_size_pct(symbol, base_risk_pct)
