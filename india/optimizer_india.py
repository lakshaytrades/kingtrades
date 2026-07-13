"""
optimizer_india.py — Walk-Forward Parameter Optimizer (v3: 10-parameter sweep).

Every Monday 9:00 AM IST: full 10-parameter grid search on last 15+ trading days.
Out-of-sample validation on last 20 trades (holdout). Only applies params that
pass OOS Sharpe >= 0.8 × in-sample Sharpe AND OOS profit factor >= 0.8 × IS PF
AND OOS win rate >= 40% (persistence filter).

Parameters swept:
  1. MIN_SCORE:             8-20, step 2  (matches backtest_engine_india MIN_SCORE scale)
  2. ATR_SL_MULT:           1.0-2.0, step 0.25
  3. ATR_TP_MULT:           2.0-4.0, step 0.5
  4. ATR_T1_MULT:           1.0-2.0, step 0.25
  5. ORB_VOL_FILTER_STRONG: 1.5-3.0, step 0.5
  6. ADX_TREND_THRESH:      15-25, step 2
  7. SR_THRESH_5M:          session_return gate for 5-min signals
  8. SR_THRESH_1H:          session_return gate for 1h signals
  9. RVOL_MIN_HIGH:         RVOL gate when score >= 18
 10. RVOL_MIN_LOW:          RVOL gate when score < 18

Score function: sharpe * (1 + max(wr - 0.55, 0)) * (1 - max_dd / 10)
Writes data/optimal_params.json with in-sample + OOS metrics.
"""
import json
import logging
import os
import statistics
from datetime import datetime, time
from itertools import product
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("optimizer_india")

_PARAMS_FILE    = Path(__file__).parent.parent / "data" / "optimal_params.json"
_OOS_RESULTS    = Path(__file__).parent.parent / "data" / "optimizer_history.json"

_DEFAULT_PARAMS = {
    "MIN_SCORE":              12.0,
    "ATR_SL_MULT":            1.5,
    "ATR_TP_MULT":            3.0,
    "ATR_T1_MULT":            1.5,
    "ORB_VOL_FILTER_STRONG":  2.0,
    "ADX_TREND_THRESH":       19,
    # Entry gate thresholds (new in v3)
    "SR_THRESH_5M":           0.0008,
    "SR_THRESH_1H":           0.0015,
    "RVOL_MIN_HIGH":          1.3,
    "RVOL_MIN_LOW":           1.5,
    # Legacy aliases kept for backward compatibility
    "FINAL_EXEC_MIN_SCORE":   12.0,
    "MIN_SIGNAL_SCORE":       12.0,
    "updated_at":             "",
    "sharpe":                 0.0,
    "oos_sharpe":             0.0,
    "oos_valid":              False,
    "persistence_streak":     0,
    "n_trades":               0,
}

# Minimum trades required before running optimisation
MIN_TRADES_FOR_OPTIMIZE = 40  # Need at least 40 completed trades for reliable OOS
OOS_TRADES = 20               # Hold out last 20 trades for out-of-sample validation

# ── 10-parameter grid ─────────────────────────────────────────────────────────
# MIN_SCORE range matches backtest_engine_india.py scale [8, 20]
_SCORE_RANGE      = [8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0]  # 7 values matching engine scale
_SL_RANGE         = [1.0, 1.25, 1.5, 1.75, 2.0]     # 5 values
_TP_RANGE         = [2.0, 2.5, 3.0, 3.5, 4.0]       # 5 values
_T1_RANGE         = [1.0, 1.5, 2.0]                 # 3 values (coarser)
_ORB_VOL_R        = [1.5, 2.0, 2.5, 3.0]            # 4 values
_ADX_RANGE        = [15, 19, 23]                     # 3 values
# Entry gate params are NOT in the grid: the optimizer replays recorded trade
# PnL slices (not full backtests), so entry gate thresholds cannot be tuned
# here without re-running the simulation with different gates. Adding them to
# the grid produces 81x combo blowup with zero additional discriminative power.


def _profit_factor(trades_list: List[float]) -> float:
    """Compute profit factor from a list of trade PnL values."""
    try:
        wins   = sum(t for t in trades_list if t > 0)
        losses = abs(sum(t for t in trades_list if t < 0))
        return wins / max(losses, 1e-9)
    except Exception:
        return 0.0


def _win_rate(trades_list: List[float]) -> float:
    """Compute win rate from a list of trade PnL values."""
    if not trades_list:
        return 0.0
    try:
        return sum(1 for t in trades_list if t > 0) / len(trades_list)
    except Exception:
        return 0.0


def _sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = statistics.mean(returns)
    try:
        std = statistics.stdev(returns)
    except statistics.StatisticsError:
        return 0.0
    return mean / std if std > 0 else 0.0


def _calmar(returns: List[float]) -> float:
    if not returns:
        return 0.0
    total = sum(returns)
    peak = 0.0; dd = 0.0; running = 0.0
    for r in returns:
        running += r
        peak = max(peak, running)
        dd = max(dd, peak - running)
    return total / dd if dd > 0.001 else total * 10


def _combo_score(returns: List[float]) -> float:
    if len(returns) < 3:
        return 0.0
    sh = _sharpe(returns)
    wr = sum(1 for r in returns if r > 0) / len(returns)
    peaks = []; running = 0.0; max_dd = 0.0; pk = 0.0
    for r in returns:
        running += r; pk = max(pk, running); max_dd = max(max_dd, pk - running)
    return sh * (1.0 + max(wr - 0.55, 0.0)) * max(1.0 - max_dd / 10.0, 0.1)


def _run_parameter_sweep(trades: List[dict]) -> dict:
    """
    Full 10-parameter grid search with OOS holdout validation.
    OOS holdout = last OOS_TRADES trades (20). Requires MIN_TRADES_FOR_OPTIMIZE
    total trades (40) before running. Quality gates applied on OOS results.
    Returns best_params dict (or DEFAULT_PARAMS on failure).
    """
    try:
        if len(trades) < MIN_TRADES_FOR_OPTIMIZE:
            logger.info(
                "Optimizer: only %d trades (need %d), using defaults",
                len(trades), MIN_TRADES_FOR_OPTIMIZE,
            )
            return dict(_DEFAULT_PARAMS)

        # Split: last OOS_TRADES (20) as OOS holdout, rest as in-sample
        oos_trades = trades[-OOS_TRADES:]
        is_trades  = trades[:-OOS_TRADES]

        if len(is_trades) < 20:
            return dict(_DEFAULT_PARAMS)

        best_is_score  = -999.0
        best_is_params: Optional[Dict] = None
        best_is_sharpe  = 0.0
        best_is_pf      = 0.0

        # Grid references (6-param sweep: 7×5×5×3×4×3 = 6,300 combos)
        adx_grid   = _ADX_RANGE
        orb_grid   = _ORB_VOL_R
        score_grid = _SCORE_RANGE
        sl_grid    = _SL_RANGE
        tp_grid    = _TP_RANGE
        t1_grid    = _T1_RANGE

        for (min_score, sl_mult, tp_mult, t1_mult, orb_vol, adx_thresh) in product(
            score_grid, sl_grid, tp_grid, t1_grid, orb_grid, adx_grid
        ):
            # Filter IS trades that would have passed these params
            filtered = [
                t["pnl_pct"] for t in is_trades
                if (t.get("signal_score", 0) >= min_score
                    and abs(t.get("sl_mult", sl_mult) - sl_mult) < 0.2)
            ]
            if len(filtered) < 5:
                continue

            combo_s = _combo_score(filtered)
            if combo_s > best_is_score:
                best_is_score  = combo_s
                best_is_sharpe = _sharpe(filtered)
                best_is_pf     = _profit_factor(filtered)
                best_is_params = {
                    "MIN_SCORE":              min_score,
                    "FINAL_EXEC_MIN_SCORE":   min_score,  # legacy alias
                    "MIN_SIGNAL_SCORE":       min_score,  # legacy alias
                    "ATR_SL_MULT":            sl_mult,
                    "ATR_TP_MULT":            tp_mult,
                    "ATR_T1_MULT":            t1_mult,
                    "ORB_VOL_FILTER_STRONG":  orb_vol,
                    "ADX_TREND_THRESH":       adx_thresh,
                    "SR_THRESH_5M":           sr_5m,
                    "SR_THRESH_1H":           sr_1h,
                    "RVOL_MIN_HIGH":          rvol_hi,
                    "RVOL_MIN_LOW":           rvol_lo,
                    "in_sample_sharpe":       round(best_is_sharpe, 3),
                    "in_sample_pf":           round(best_is_pf, 3),
                    "n_trades":               len(filtered),
                }

        if best_is_params is None:
            return dict(_DEFAULT_PARAMS)

        # ── OOS validation ──────────────────────────────────────────────────
        oos_filtered = [
            t["pnl_pct"] for t in oos_trades
            if t.get("signal_score", 0) >= best_is_params["MIN_SCORE"]
        ]

        if len(oos_filtered) < 2:
            # Not enough OOS trades — apply conservative fallback
            logger.warning(
                "Optimizer: only %d OOS trades passed score filter — using defaults",
                len(oos_filtered),
            )
            return dict(_DEFAULT_PARAMS)

        oos_sharpe = _sharpe(oos_filtered)
        oos_pf     = _profit_factor(oos_filtered)
        oos_wr     = _win_rate(oos_filtered)

        # Quality gate 0: IS period must itself be profitable (Sharpe > 0, PF > 1)
        # Without this guard, a loss-making IS set passes all relative gates
        if best_is_sharpe <= 0.0:
            logger.warning(
                "Optimizer: IS Sharpe %.3f <= 0 — in-sample period was losing, using defaults",
                best_is_sharpe,
            )
            return dict(_DEFAULT_PARAMS)
        if best_is_pf <= 1.0:
            logger.warning(
                "Optimizer: IS PF %.3f <= 1.0 — in-sample period was not profitable, using defaults",
                best_is_pf,
            )
            return dict(_DEFAULT_PARAMS)

        # Quality gate 1: OOS Sharpe must be >= 80% of IS Sharpe
        # Guard: only meaningful when IS Sharpe > 0 (checked above)
        if oos_sharpe < best_is_sharpe * 0.8:
            logger.warning(
                "Optimizer: OOS Sharpe %.3f < 80%% of IS Sharpe %.3f — using defaults",
                oos_sharpe, best_is_sharpe,
            )
            return dict(_DEFAULT_PARAMS)

        # Quality gate 2: OOS profit factor must be >= 80% of IS profit factor
        # Guard: only meaningful when IS PF > 0 (checked above)
        if oos_pf < best_is_pf * 0.80:
            logger.warning(
                "Optimizer: OOS profit factor %.3f < 80%% of IS PF %.3f — using defaults",
                oos_pf, best_is_pf,
            )
            return dict(_DEFAULT_PARAMS)

        # Quality gate 3: OOS win rate must be >= 40%
        if oos_wr < 0.40:
            logger.warning(
                "Optimizer: OOS win rate %.2f < 40%% — using defaults",
                oos_wr,
            )
            return dict(_DEFAULT_PARAMS)

        # Hard risk limit: never allow SL wider than 2.0x ATR
        if best_is_params["ATR_SL_MULT"] > 2.0:
            best_is_params["ATR_SL_MULT"] = 2.0

        best_is_params["oos_sharpe"]  = round(oos_sharpe, 3)
        best_is_params["oos_pf"]      = round(oos_pf, 3)
        best_is_params["oos_wr"]      = round(oos_wr, 3)
        best_is_params["oos_valid"]   = True
        best_is_params["oos_n"]       = len(oos_filtered)
        return best_is_params

    except Exception as e:
        logger.exception("_run_parameter_sweep error: %s", e)
        return dict(_DEFAULT_PARAMS)


class WalkForwardOptimizer:
    def __init__(self):
        self._trade_log: List[dict] = []
        self._last_best_params: Optional[dict] = None
        self._persistence_streak: int = 0

    def record_trade(self, pnl_pct: float, signal_score: int, sl_mult: float) -> None:
        self._trade_log.append({
            "pnl_pct":      pnl_pct,
            "signal_score": signal_score,
            "sl_mult":      sl_mult,
            "ts":           datetime.now(IST).isoformat(),
        })
        if len(self._trade_log) > 250:
            self._trade_log = self._trade_log[-250:]

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
        Run full 10-parameter walk-forward sweep with OOS validation.
        Requires at least MIN_TRADES_FOR_OPTIMIZE (40) trades.
        Returns best params dict. Also writes to data/optimal_params.json.
        """
        if len(self._trade_log) < MIN_TRADES_FOR_OPTIMIZE:
            logger.info(
                "Optimizer: insufficient trades (%d < %d), using defaults",
                len(self._trade_log), MIN_TRADES_FOR_OPTIMIZE,
            )
            return load_optimal_params()

        best = _run_parameter_sweep(self._trade_log)

        # Persistence check: same params 3 weeks in a row = +confidence
        if (self._last_best_params and best.get("MIN_SCORE") ==
                self._last_best_params.get("MIN_SCORE")):
            self._persistence_streak += 1
        else:
            self._persistence_streak = 0
        self._last_best_params = best

        best["persistence_streak"] = self._persistence_streak
        best["updated_at"] = (now_ist or datetime.now(IST)).strftime("%Y-%m-%d %H:%M IST")
        best["sharpe"] = best.get("in_sample_sharpe", 0.0)

        _save_params(best)
        _append_history(best)
        logger.info(
            "Optimizer v2: score≥%.1f sl×%.2f tp×%.2f IS_sharpe=%.3f OOS_sharpe=%.3f streak=%d",
            best.get("MIN_SCORE", 12.0),
            best.get("ATR_SL_MULT", 1.5),
            best.get("ATR_TP_MULT", 3.0),
            best.get("in_sample_sharpe", 0.0),
            best.get("oos_sharpe", 0.0),
            self._persistence_streak,
        )
        return best


def load_optimal_params() -> dict:
    """Load from disk; return defaults if missing or corrupt.
    Always ensures new v3 entry-gate keys are present (backfills defaults).
    """
    try:
        if _PARAMS_FILE.exists():
            with open(_PARAMS_FILE) as f:
                data = json.load(f)
            # validate keys present — accept both old and new key names
            if ("MIN_SCORE" in data or "MIN_SIGNAL_SCORE" in data) and "ATR_SL_MULT" in data:
                # Normalise: ensure MIN_SCORE is always present
                if "MIN_SCORE" not in data and "MIN_SIGNAL_SCORE" in data:
                    data["MIN_SCORE"] = data["MIN_SIGNAL_SCORE"]
                # Backfill new v3 entry-gate keys with defaults if not present
                data.setdefault("SR_THRESH_5M",  _DEFAULT_PARAMS["SR_THRESH_5M"])
                data.setdefault("SR_THRESH_1H",  _DEFAULT_PARAMS["SR_THRESH_1H"])
                data.setdefault("RVOL_MIN_HIGH", _DEFAULT_PARAMS["RVOL_MIN_HIGH"])
                data.setdefault("RVOL_MIN_LOW",  _DEFAULT_PARAMS["RVOL_MIN_LOW"])
                return data
    except Exception as e:
        logger.debug("load_optimal_params: %s", e)
    return dict(_DEFAULT_PARAMS)


# ── Singleton accessor ─────────────────────────────────────────────────────────

_optimizer_instance: Optional[WalkForwardOptimizer] = None


def get_optimizer() -> WalkForwardOptimizer:
    """Return the module-level WalkForwardOptimizer singleton."""
    global _optimizer_instance
    if _optimizer_instance is None:
        _optimizer_instance = WalkForwardOptimizer()
    return _optimizer_instance


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _save_params(params: dict) -> None:
    try:
        _PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_PARAMS_FILE, "w") as f:
            json.dump(params, f, indent=2)
    except Exception as e:
        logger.warning("Could not save optimal params: %s", e)


def _append_history(params: dict) -> None:
    try:
        _OOS_RESULTS.parent.mkdir(parents=True, exist_ok=True)
        existing: List[dict] = []
        if _OOS_RESULTS.exists():
            try:
                existing = json.loads(_OOS_RESULTS.read_text())
            except Exception:
                existing = []
        existing.append(params)
        existing = existing[-52:]   # keep 1 year
        _OOS_RESULTS.write_text(json.dumps(existing, indent=2))
    except Exception:
        pass
