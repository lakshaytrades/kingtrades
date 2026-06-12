"""
optimizer_india.py — Walk-Forward Parameter Optimizer.
Every Monday 9:00 AM IST: tests MIN_SIGNAL_SCORE (60-75, step 3) and
ATR_SL_MULT (1.0-2.0, step 0.25) on last 7 days of trade history.
Maximises Sharpe. Writes data/optimal_params.json.
"""
import json
import logging
import os
from datetime import datetime, time
from itertools import product
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("optimizer_india")

_PARAMS_FILE = Path(__file__).parent.parent / "data" / "optimal_params.json"

_DEFAULT_PARAMS = {
    "MIN_SIGNAL_SCORE": 65,
    "ATR_SL_MULT": 1.5,
    "updated_at": "",
    "sharpe": 0.0,
    "n_trades": 0,
}

_SCORE_RANGE = list(range(60, 76, 3))       # [60, 63, 66, 69, 72, 75]
_SL_RANGE    = [round(x * 0.25, 2) for x in range(4, 9)]  # [1.0, 1.25, 1.5, 1.75, 2.0]


class WalkForwardOptimizer:
    def __init__(self):
        self._trade_log: list[dict] = []   # {"pnl_pct": float, "score": int, "sl_mult": float}

    # ------------------------------------------------------------------ #

    def record_trade(self, pnl_pct: float, signal_score: int, sl_mult: float) -> None:
        self._trade_log.append({
            "pnl_pct": pnl_pct,
            "signal_score": signal_score,
            "sl_mult": sl_mult,
            "ts": datetime.now(IST).isoformat(),
        })
        # Keep last 7 days = ~50-70 trades max
        if len(self._trade_log) > 200:
            self._trade_log = self._trade_log[-200:]

    def should_run(self, now_ist: Optional[datetime] = None) -> bool:
        """Returns True on Monday between 8:55–9:05 AM IST."""
        if now_ist is None:
            now_ist = datetime.now(IST)
        return (
            now_ist.weekday() == 0
            and time(8, 55) <= now_ist.time() <= time(9, 5)
        )

    def run(self, now_ist: Optional[datetime] = None) -> dict:
        """
        Run walk-forward sweep over cached trade log.
        Returns best params dict. Also writes to data/optimal_params.json.
        """
        if len(self._trade_log) < 10:
            logger.info("Optimizer: insufficient trades (%d), using defaults", len(self._trade_log))
            return load_optimal_params()

        best_sharpe = -999.0
        best_params = dict(_DEFAULT_PARAMS)

        for min_score, sl_mult in product(_SCORE_RANGE, _SL_RANGE):
            trades = [
                t["pnl_pct"]
                for t in self._trade_log
                if t["signal_score"] >= min_score and abs(t["sl_mult"] - sl_mult) < 0.13
            ]
            if len(trades) < 5:
                continue
            sharpe = _sharpe(trades)
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_params = {
                    "MIN_SIGNAL_SCORE": min_score,
                    "ATR_SL_MULT": sl_mult,
                    "sharpe": round(sharpe, 3),
                    "n_trades": len(trades),
                    "updated_at": (now_ist or datetime.now(IST)).strftime("%Y-%m-%d %H:%M IST"),
                }

        if best_sharpe > -999.0:
            _save_params(best_params)
            logger.info(
                "Optimizer result: score≥%d sl×%.2f Sharpe=%.3f n=%d",
                best_params["MIN_SIGNAL_SCORE"],
                best_params["ATR_SL_MULT"],
                best_params["sharpe"],
                best_params["n_trades"],
            )
        return best_params


def load_optimal_params() -> dict:
    """Load from disk; return defaults if missing or corrupt."""
    try:
        if _PARAMS_FILE.exists():
            with open(_PARAMS_FILE) as f:
                data = json.load(f)
            # validate keys present
            if "MIN_SIGNAL_SCORE" in data and "ATR_SL_MULT" in data:
                return data
    except Exception as e:
        logger.debug("load_optimal_params: %s", e)
    return dict(_DEFAULT_PARAMS)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    import statistics
    mean = statistics.mean(returns)
    try:
        std = statistics.stdev(returns)
    except statistics.StatisticsError:
        return 0.0
    return mean / std if std > 0 else 0.0


def _save_params(params: dict) -> None:
    try:
        _PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_PARAMS_FILE, "w") as f:
            json.dump(params, f, indent=2)
    except Exception as e:
        logger.warning("Could not save optimal params: %s", e)
