"""
symbol_stats.py — Per-symbol adaptive win-rate tracker.

Tracks rolling 30-trade win rate per symbol so the signal generator can:
  • Skip symbols that have been consistently losing (WR < 40%)
  • Lower the minimum score bar for proven symbols (WR > 70%)
  • Raise the bar for underperforming symbols

This is one of the highest-impact enhancements for long-run win rate:
a symbol that has lost 7 of the last 10 trades is probably in a
regime where the bot's edge doesn't apply to it.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

STATS_FILE = Path("data/symbol_stats.json")


class SymbolStats:
    """Rolling 30-trade per-symbol win-rate tracker with adaptive score thresholds."""

    def __init__(self):
        self._stats: Dict[str, list] = {}   # {symbol: [1, 0, 1, ...]}  1=win, 0=loss
        self._load()

    # ── recording ──────────────────────────────────────────

    def record(self, symbol: str, win: bool) -> None:
        """Record trade outcome. Call after every closed trade."""
        sym = symbol.upper()
        if sym not in self._stats:
            self._stats[sym] = []
        self._stats[sym].append(1 if win else 0)
        self._stats[sym] = self._stats[sym][-30:]   # rolling window
        self._save()
        wr, n = self.win_rate(sym)
        logger.debug(f"[SYMBOL_STATS] {sym}: {'WIN' if win else 'LOSS'} | WR={wr*100:.1f}% ({n} trades)")

    # ── querying ──────────────────────────────────────────

    def win_rate(self, symbol: str) -> Tuple[float, int]:
        """Returns (win_rate 0.0-1.0, n_trades). Default 0.55 if < 5 trades."""
        trades = self._stats.get(symbol.upper(), [])
        n = len(trades)
        if n < 5:
            return 0.55, 0
        return sum(trades) / n, n

    def should_skip(self, symbol: str, min_wr: float = 0.40) -> bool:
        """
        Return True if symbol should be skipped entirely.
        Only kicks in after 10+ trades to avoid premature filtering.
        """
        wr, n = self.win_rate(symbol)
        skip = n >= 10 and wr < min_wr
        if skip:
            logger.info(f"[SYMBOL_STATS] Skipping {symbol} — WR={wr*100:.1f}% < {min_wr*100:.0f}% ({n} trades)")
        return skip

    def min_score_for(self, symbol: str, base_min: float = 82.0) -> float:
        """
        Adaptive minimum score threshold for a symbol.
        Proven winners get a 5-pt lower bar; persistent losers get a 5-pt higher bar.
        """
        wr, n = self.win_rate(symbol)
        if n < 10:
            return base_min        # not enough data yet
        if wr >= 0.70:
            return base_min - 5.0  # proven winner: allow slightly lower-score entries
        if wr >= 0.60:
            return base_min - 2.0  # good history: minor relief
        if wr < 0.45:
            return base_min + 5.0  # struggling: require stronger signals
        return base_min

    def get_all_stats(self) -> Dict[str, Dict]:
        """Return summary dict for all tracked symbols."""
        out = {}
        for sym, trades in self._stats.items():
            n  = len(trades)
            wr = sum(trades) / n if n > 0 else 0.0
            out[sym] = {"n": n, "wr_pct": round(wr * 100, 1)}
        return dict(sorted(out.items(), key=lambda x: -x[1]["wr_pct"]))

    # ── persistence ───────────────────────────────────────

    def _load(self) -> None:
        try:
            if STATS_FILE.exists():
                self._stats = json.loads(STATS_FILE.read_text())
        except Exception as e:
            logger.debug(f"[SYMBOL_STATS] load failed: {e}")

    def _save(self) -> None:
        try:
            STATS_FILE.parent.mkdir(exist_ok=True)
            STATS_FILE.write_text(json.dumps(self._stats))
        except Exception as e:
            logger.debug(f"[SYMBOL_STATS] save failed: {e}")
