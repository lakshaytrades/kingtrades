"""
trainer.py — NSE Momentum Groww AI Bot
Robust Backtesting Engine + Walk-Forward Optimization + Statistical Validation

⚠️ OFFLINE ONLY — Trainer never places live orders.
Run this BEFORE going live to validate and optimize strategy parameters.

18yr Rule: "Backtest is NOT a crystal ball. It's a confidence builder.
Walk-forward validation is the ONLY honest way to test a strategy."

Includes:
  BacktestEngine          — Event-driven 5m candle simulation with 50/30/20 partial exits
  WalkForwardOptimizer    — Optuna-powered walk-forward parameter search
  MonteCarloSimulator     — 1000-run trade shuffle stress test + ruin probability
  BenchmarkComparator     — Alpha/Beta/Info ratio vs Nifty50 buy-and-hold
  SensitivityAnalyzer     — Robustness score: how much does Sharpe degrade per ±10% param shift
  PortfolioStressTest     — Combined stress: crash scenario + fat tail simulation

Usage:
  python trainer.py --symbols RELIANCE,TCS --days 365
  python trainer.py --full-optimization
  python trainer.py --monte-carlo
  python trainer.py --benchmark
  python trainer.py --sensitivity
  python trainer.py --quick-test           # All analyses, 30d data

Targets:
  Win rate > 55% | Sharpe > 1.5 | Max drawdown < 8% | Net return > 5%/month
  Monte Carlo ruin prob < 5% | Benchmark alpha > 5%/yr | Sensitivity score > 0.7
"""

import argparse
import json
import logging
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class _NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy scalars and booleans."""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

from utils import (
    format_ist_timestamp, get_current_ist_time,
    filter_market_hours, setup_logging
)
import config

logger = logging.getLogger(__name__)
RESULTS_DIR = Path("trainer_results")
RESULTS_DIR.mkdir(exist_ok=True)

# -----------------------------------------------------------
# COST MODEL (realistic NSE intraday costs)
# -----------------------------------------------------------
SLIPPAGE_PCT   = 0.10   # 0.10% per trade (market impact)
BROKERAGE_PCT  = 0.03   # ~₹20 flat → ~0.03% on ₹70k trade
STT_PCT        = 0.025  # Securities Transaction Tax (sell side)
TOTAL_COST_PCT = SLIPPAGE_PCT + BROKERAGE_PCT + STT_PCT  # ~0.155%


# -----------------------------------------------------------
# BACKTEST ENGINE
# -----------------------------------------------------------

class BacktestEngine:
    """
    Event-driven vectorised backtester for 5-min NSE data.
    Simulates: entry, SL, T1 (40% exit), T2 (40% exit), T3 trailing runner.
    """

    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.capital         = initial_capital
        self.trades: List[Dict] = []

    def run(
        self,
        df:          pd.DataFrame,
        signals_df:  pd.DataFrame,
        atr_sl_mult: float = 1.5,
        atr_t1_mult: float = 2.5,
        atr_t2_mult: float = 4.0,
        max_risk_pct: float = 0.5,
    ) -> Dict:
        """
        Run backtest on signal list.

        df:         Raw OHLCV + indicators (5-min, IST index)
        signals_df: DataFrame with columns: timestamp, direction, entry, atr, score
        """
        self.capital = self.initial_capital
        self.trades  = []
        equity_curve = [self.initial_capital]
        peak_equity  = self.initial_capital

        for _, sig in signals_df.iterrows():
            # Find entry candle in df
            try:
                entry_idx = df.index.searchsorted(sig["timestamp"])
            except Exception:
                continue

            if entry_idx >= len(df) - 5:
                continue

            entry_price = float(sig["entry"])
            direction   = str(sig["direction"])
            atr         = float(sig.get("atr", entry_price * 0.005))
            score       = float(sig.get("score", 65))

            # Position sizing
            sl_dist    = atr * atr_sl_mult
            risk_amt   = self.capital * (max_risk_pct / 100)
            qty        = max(1, int(risk_amt / sl_dist))
            max_qty    = int(self.capital * 0.15 / entry_price)
            qty        = min(qty, max_qty)

            if qty <= 0:
                continue

            sl     = entry_price - sl_dist if direction == "LONG" else entry_price + sl_dist
            t1     = entry_price + atr * atr_t1_mult if direction == "LONG" else entry_price - atr * atr_t1_mult
            t2     = entry_price + atr * atr_t2_mult if direction == "LONG" else entry_price - atr * atr_t2_mult

            # Simulate trade through subsequent candles
            result = self._simulate_trade(
                df, entry_idx, direction, entry_price, sl, t1, t2, qty
            )

            if result:
                self.trades.append(result)
                self.capital += result["net_pnl"]
                equity_curve.append(self.capital)
                peak_equity = max(peak_equity, self.capital)

        return self._compute_stats(equity_curve, peak_equity)

    def _simulate_trade(
        self, df, entry_idx, direction, entry, sl, t1, t2, qty
    ) -> Optional[Dict]:
        """Walk forward candle-by-candle to find exit."""
        qty_remaining = qty
        t1_done       = False
        realized_pnl  = 0.0
        exit_price    = entry
        exit_reason   = "TIMEOUT"
        entry_cost    = entry * qty * TOTAL_COST_PCT / 100

        for i in range(entry_idx + 1, min(entry_idx + 80, len(df))):
            c = df.iloc[i]
            h, l = float(c["high"]), float(c["low"])

            if direction == "LONG":
                # Check SL
                if l <= sl:
                    exit_price  = sl
                    exit_reason = "HIT_SL"
                    realized_pnl += (exit_price - entry) * qty_remaining
                    qty_remaining = 0
                    break
                # Check T1
                if not t1_done and h >= t1:
                    t1_qty = max(1, int(qty * 0.4))
                    realized_pnl += (t1 - entry) * t1_qty
                    qty_remaining -= t1_qty
                    sl = entry   # Move SL to breakeven
                    t1_done = True
                    exit_reason = "HIT_T1"
                # Check T2
                if t1_done and h >= t2:
                    realized_pnl += (t2 - entry) * qty_remaining
                    qty_remaining = 0
                    exit_reason   = "HIT_T2"
                    break
            else:  # SHORT
                if h >= sl:
                    exit_price  = sl
                    exit_reason = "HIT_SL"
                    realized_pnl += (entry - exit_price) * qty_remaining
                    qty_remaining = 0
                    break
                if not t1_done and l <= t1:
                    t1_qty = max(1, int(qty * 0.4))
                    realized_pnl += (entry - t1) * t1_qty
                    qty_remaining -= t1_qty
                    sl = entry
                    t1_done      = True
                    exit_reason  = "HIT_T1"
                if t1_done and l <= t2:
                    realized_pnl += (entry - t2) * qty_remaining
                    qty_remaining = 0
                    exit_reason   = "HIT_T2"
                    break

        # Time exit (remaining qty closed at last candle)
        if qty_remaining > 0:
            last_close = float(df.iloc[min(entry_idx + 79, len(df)-1)]["close"])
            if direction == "LONG":
                realized_pnl += (last_close - entry) * qty_remaining
            else:
                realized_pnl += (entry - last_close) * qty_remaining
            if exit_reason not in ("HIT_T1", "HIT_T2"):
                exit_reason = "TIMEOUT"

        gross_pnl = realized_pnl
        cost      = entry * qty * TOTAL_COST_PCT / 100
        net_pnl   = gross_pnl - cost

        return {
            "direction":   direction,
            "entry":       entry,
            "qty":         qty,
            "exit_reason": exit_reason,
            "gross_pnl":   round(gross_pnl, 2),
            "cost":        round(cost, 2),
            "net_pnl":     round(net_pnl, 2),
            "outcome":     "WIN" if net_pnl > 0 else "LOSS",
        }

    def _compute_stats(self, equity: List[float], peak: float) -> Dict:
        if not self.trades:
            return {"error": "No trades"}

        net_pnls = [t["net_pnl"] for t in self.trades]
        wins     = [p for p in net_pnls if p > 0]
        losses   = [p for p in net_pnls if p <= 0]
        total_pnl = sum(net_pnls)
        win_rate  = len(wins) / len(net_pnls) * 100

        pnl_arr = np.array(net_pnls)

        # ── Sharpe (annualised using trade-level returns) ──
        # Guard: need ≥2 trades with meaningful variance; 1e-9 epsilon causes
        # billions when std≈0 (single trade or all trades identical P&L).
        _std = float(np.std(pnl_arr))
        if len(pnl_arr) < 2 or _std < 1e-6:
            sharpe = 0.0
        else:
            sharpe = (float(np.mean(pnl_arr)) / _std) * float(np.sqrt(252))
            sharpe = max(-30.0, min(sharpe, 30.0))  # Hard cap ±30

        # ── Sortino (penalise only downside deviation) ──────
        downside = pnl_arr[pnl_arr < 0]
        _sort_std = float(np.std(downside)) if len(downside) > 1 else 0.0
        if _sort_std < 1e-6:
            sortino = 0.0
        else:
            sortino = (float(np.mean(pnl_arr)) / _sort_std) * float(np.sqrt(252))
            sortino = max(-30.0, min(sortino, 30.0))

        # ── Max drawdown ────────────────────────────────────
        eq_arr   = np.array(equity)
        peak_arr = np.maximum.accumulate(eq_arr)
        dd_arr   = (eq_arr - peak_arr) / (peak_arr + 1e-9) * 100
        max_dd   = float(abs(dd_arr.min()))

        # ── Calmar = annualised return / max drawdown ───────
        total_return_pct = (self.capital - self.initial_capital) / self.initial_capital * 100
        n_days           = max(len(equity), 1)
        annualised_ret   = total_return_pct / (n_days / 252)
        calmar           = annualised_ret / max(max_dd, 0.01)

        # ── Monthly return ───────────────────────────────────
        monthly_return = total_return_pct / max(n_days / 22, 1)

        # ── Profit factor, payoff ratio ─────────────────────
        profit_factor = abs(sum(wins)) / (abs(sum(losses)) + 1e-9)
        avg_win       = np.mean(wins)  if wins   else 0.0
        avg_loss      = np.mean(losses) if losses else 0.0
        payoff_ratio  = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

        # ── Consecutive streaks ─────────────────────────────
        max_cons_wins = max_cons_losses = cur_w = cur_l = 0
        for p in net_pnls:
            if p > 0:
                cur_w += 1; cur_l = 0
                max_cons_wins = max(max_cons_wins, cur_w)
            else:
                cur_l += 1; cur_w = 0
                max_cons_losses = max(max_cons_losses, cur_l)

        # ── Recovery factor ─────────────────────────────────
        recovery_factor = total_pnl / (max_dd / 100 * self.initial_capital + 1e-9)

        # ── Session breakdown ───────────────────────────────
        session_stats: Dict[str, Dict] = {
            "opening_drive": {"trades": [], "label": "09:15-10:00"},
            "morning":       {"trades": [], "label": "10:00-11:00"},
            "midday":        {"trades": [], "label": "11:00-13:30"},
            "afternoon":     {"trades": [], "label": "13:30-15:20"},
        }
        for t in self.trades:
            ts = t.get("entry_time")
            if ts:
                try:
                    h = ts.hour if hasattr(ts, "hour") else int(str(ts)[11:13])
                    m = ts.minute if hasattr(ts, "minute") else int(str(ts)[14:16])
                    tot = h * 60 + m
                    if 555 <= tot < 600:          # 09:15-10:00
                        session_stats["opening_drive"]["trades"].append(t["net_pnl"])
                    elif 600 <= tot < 660:         # 10:00-11:00
                        session_stats["morning"]["trades"].append(t["net_pnl"])
                    elif 660 <= tot < 810:         # 11:00-13:30
                        session_stats["midday"]["trades"].append(t["net_pnl"])
                    elif 810 <= tot < 920:         # 13:30-15:20
                        session_stats["afternoon"]["trades"].append(t["net_pnl"])
                except Exception:
                    pass

        sessions: Dict[str, Dict] = {}
        for name, info in session_stats.items():
            pts = info["trades"]
            if pts:
                sessions[name] = {
                    "label":     info["label"],
                    "trades":    len(pts),
                    "win_rate":  round(sum(1 for p in pts if p > 0) / len(pts) * 100, 1),
                    "total_pnl": round(sum(pts), 2),
                    "avg_pnl":   round(np.mean(pts), 2),
                }

        passes_targets = bool(
            win_rate       >= config.TARGET_WIN_RATE and
            sharpe         >= config.TARGET_SHARPE and
            max_dd         <= config.MAX_DRAWDOWN_LIMIT and
            monthly_return >= config.TARGET_MONTHLY_RETURN_PCT
        )

        return {
            "total_trades":      len(self.trades),
            "wins":              len(wins),
            "losses":            len(losses),
            "win_rate":          round(win_rate, 2),
            "avg_win":           round(avg_win, 2),
            "avg_loss":          round(avg_loss, 2),
            "payoff_ratio":      round(payoff_ratio, 2),
            "profit_factor":     round(profit_factor, 2),
            "total_pnl":         round(total_pnl, 2),
            "total_return_pct":  round(total_return_pct, 2),
            "monthly_return":    round(monthly_return, 2),
            "annualised_return": round(annualised_ret, 2),
            "sharpe":            round(sharpe, 2),
            "sortino":           round(sortino, 2),
            "calmar":            round(calmar, 2),
            "max_drawdown":      round(max_dd, 2),
            "recovery_factor":   round(recovery_factor, 2),
            "max_cons_wins":     max_cons_wins,
            "max_cons_losses":   max_cons_losses,
            "final_capital":     round(self.capital, 2),
            "passes_targets":    passes_targets,
            "sessions":          sessions,
        }


# -----------------------------------------------------------
# WALK-FORWARD OPTIMIZER (Optuna)
# -----------------------------------------------------------

class WalkForwardOptimizer:
    """
    Optuna-powered walk-forward parameter optimization.
    Uses in-sample training window + out-of-sample validation.
    Prevents look-ahead bias.
    """

    def __init__(self, df: pd.DataFrame, n_splits: int = 5):
        self.df       = df
        self.n_splits = n_splits

    def optimize(self, n_trials: int = 100) -> Dict:
        """Run Optuna optimization over walk-forward windows."""
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            logger.error("optuna not installed: pip install optuna")
            return {}

        all_results = []
        window_size = len(self.df) // (self.n_splits + 1)

        for split in range(self.n_splits):
            train_start = split * window_size
            train_end   = train_start + window_size
            test_start  = train_end
            test_end    = min(test_start + window_size // 2, len(self.df))

            df_train = self.df.iloc[train_start:train_end]
            df_test  = self.df.iloc[test_start:test_end]

            if len(df_train) < 100 or len(df_test) < 20:
                continue

            logger.info(f"[{format_ist_timestamp()}] Walk-forward split {split+1}/{self.n_splits}...")

            def objective(trial):
                atr_sl   = trial.suggest_float("atr_sl",  1.0, 2.5, step=0.1)
                atr_t1   = trial.suggest_float("atr_t1",  1.5, 3.5, step=0.1)
                atr_t2   = trial.suggest_float("atr_t2",  3.0, 6.0, step=0.5)
                risk_pct = trial.suggest_float("risk_pct", 0.3, 1.0, step=0.1)

                signals = self._generate_signals(df_train, atr_sl)
                if signals.empty:
                    return -1000.0

                engine = BacktestEngine(initial_capital=100000)
                stats  = engine.run(df_train, signals, atr_sl, atr_t1, atr_t2, risk_pct)
                if "error" in stats:
                    return -1000.0

                # Objective: maximise Sharpe, penalise drawdown
                score = stats["sharpe"] * 10 - max(0, stats["max_drawdown"] - 5) * 2
                return score

            study = optuna.create_study(direction="maximize")
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
            best = study.best_params

            # Validate on out-of-sample test window
            signals_test = self._generate_signals(df_test, best.get("atr_sl", 1.5))
            if not signals_test.empty:
                engine = BacktestEngine(100000)
                oos_stats = engine.run(
                    df_test, signals_test,
                    best["atr_sl"], best["atr_t1"], best["atr_t2"], best["risk_pct"]
                )
                all_results.append({"split": split, "params": best, "oos": oos_stats})
                logger.info(
                    f"[{format_ist_timestamp()}] Split {split+1} OOS: "
                    f"WR={oos_stats.get('win_rate',0):.1f}% "
                    f"Sharpe={oos_stats.get('sharpe',0):.2f} "
                    f"DD={oos_stats.get('max_drawdown',0):.1f}%"
                )

        if not all_results:
            return {}

        # Average best parameters across splits
        param_keys = ["atr_sl", "atr_t1", "atr_t2", "risk_pct"]
        avg_params = {}
        for k in param_keys:
            vals = [r["params"].get(k, 0) for r in all_results]
            avg_params[k] = round(np.mean(vals), 2)

        self._save_optimized_params(avg_params, all_results)
        return {"best_params": avg_params, "split_results": all_results}

    def _generate_signals(self, df: pd.DataFrame, atr_sl_mult: float) -> pd.DataFrame:
        """Generate mock signals from indicators for backtesting."""
        signals = []
        if len(df) < 30:
            return pd.DataFrame()
        for i in range(30, len(df)):
            row = df.iloc[i]
            rsi  = float(row.get("rsi", 50) or 50)
            hist = float(row.get("macd_hist", 0) or 0)
            close = float(row.get("close", 0))
            vol_ratio = float(row.get("volume_ratio", 1) or 1)
            atr  = float(row.get("atr", close * 0.005) or close * 0.005)

            score = 0
            direction = None

            if rsi < 40 and hist > 0 and vol_ratio > 1.5:
                score     = 70
                direction = "LONG"
            elif rsi > 60 and hist < 0 and vol_ratio > 1.5:
                score     = 70
                direction = "SHORT"

            if direction and score >= 65:
                signals.append({
                    "timestamp": df.index[i],
                    "direction": direction,
                    "entry":     close,
                    "atr":       atr,
                    "score":     score,
                })
        return pd.DataFrame(signals)

    def _save_optimized_params(self, params: Dict, results: List):
        out = RESULTS_DIR / f"optimized_params_{get_current_ist_time().strftime('%Y%m%d')}.json"
        with open(out, "w") as f:
            json.dump({"params": params, "results": [r["oos"] for r in results]}, f, indent=2, cls=_NumpyEncoder)
        logger.info(f"[{format_ist_timestamp()}] Optimized params saved → {out}")


# -----------------------------------------------------------
# EOD SELF-TRAINER  (runs automatically at 4:30 PM IST daily)
# -----------------------------------------------------------

ADAPTIVE_PARAMS_FILE = Path("data/adaptive_params.json")
ADAPTIVE_PARAMS_FILE.parent.mkdir(exist_ok=True)


class EODSelfTrainer:
    """
    Automatic end-of-day strategy optimizer.

    Runs at 4:30 PM IST after every market close:
      1. Downloads last 30 days of 5m/15m data for top 10 watchlist stocks
      2. Walk-forward optimizes ATR_SL, ATR_T1, ATR_T2, risk_pct (20 trials × 3 splits)
      3. Averages best params across all symbols
      4. Safety gate: only adopts new params if Sharpe improved ≥ 5%
      5. Saves to data/adaptive_params.json (read by main.py each morning)
      6. Sends results summary to Telegram

    18yr Rule: "Your strategy should get smarter every single day.
    Markets evolve — a strategy that never adapts will eventually die."

    Param file format:
      {
        "date":         "2026-04-09",
        "atr_sl":       1.4,
        "atr_t1":       2.6,
        "atr_t2":       4.2,
        "risk_pct":     0.5,
        "min_score":    72.0,
        "sharpe":       1.87,
        "win_rate":     61.2,
        "monthly_ret":  6.4,
        "symbols_used": ["RELIANCE", "TCS", ...],
        "improved":     true
      }
    """

    # Safe bounds — trainer can never drift params outside these
    PARAM_BOUNDS = {
        "atr_sl":   (1.0, 2.5),
        "atr_t1":   (1.5, 4.0),
        "atr_t2":   (3.0, 7.0),
        "risk_pct": (0.3, 1.0),
    }

    def run(
        self,
        fetcher,
        symbols:   List[str] = None,
        lookback:  int        = 30,
        n_trials:  int        = 20,
        n_splits:  int        = 3,
        alerter                = None,
    ) -> Dict:
        """
        Full EOD training cycle.
        Returns summary dict (also sent to Telegram).
        """
        import config as _cfg

        if symbols is None:
            symbols = _cfg.DEFAULT_WATCHLIST[:10]

        logger.info(
            f"[{format_ist_timestamp()}] EOD Self-Trainer starting — "
            f"{len(symbols)} symbols, {lookback}d lookback, {n_trials} trials"
        )

        # Load current (yesterday's) params as baseline
        baseline = self.load_params()
        baseline_sharpe = baseline.get("sharpe", 0.0)

        per_symbol_params: List[Dict] = []
        per_symbol_stats:  List[Dict] = []

        for sym in symbols:
            try:
                df = fetcher.get_candles(sym, interval="5m", days=lookback)
                if df is None or len(df) < 100:
                    continue

                # Add indicators
                try:
                    from pattern_recognition import TechnicalIndicators
                    df = TechnicalIndicators().compute(df)
                except Exception:
                    continue

                # Walk-forward optimization
                opt    = WalkForwardOptimizer(df, n_splits=n_splits)
                result = opt.optimize(n_trials=n_trials)

                if result.get("best_params") and result.get("split_results"):
                    best_p = result["best_params"]
                    oos_stats = [r["oos"] for r in result["split_results"] if "oos" in r]

                    if oos_stats:
                        avg_sharpe = float(np.mean([s.get("sharpe", 0) for s in oos_stats]))
                        avg_wr     = float(np.mean([s.get("win_rate", 0) for s in oos_stats]))
                        avg_ret    = float(np.mean([s.get("monthly_return", 0) for s in oos_stats]))

                        per_symbol_params.append(best_p)
                        per_symbol_stats.append({
                            "symbol":  sym,
                            "sharpe":  round(avg_sharpe, 3),
                            "win_rate":round(avg_wr, 1),
                            "monthly": round(avg_ret, 2),
                        })
                        logger.info(
                            f"[{format_ist_timestamp()}] {sym}: "
                            f"Sharpe={avg_sharpe:.2f} WR={avg_wr:.1f}% "
                            f"Monthly={avg_ret:.1f}%"
                        )
            except Exception as e:
                logger.warning(f"EOD train failed for {sym}: {e}")

        if not per_symbol_params:
            logger.warning(f"[{format_ist_timestamp()}] EOD Self-Trainer: no usable results")
            return {"improved": False, "reason": "No usable optimization results"}

        # Average params across all symbols (ensemble approach)
        param_keys  = ["atr_sl", "atr_t1", "atr_t2", "risk_pct"]
        avg_params  = {}
        for k in param_keys:
            vals = [p[k] for p in per_symbol_params if k in p]
            if vals:
                raw = float(np.mean(vals))
                lo, hi = self.PARAM_BOUNDS[k]
                avg_params[k] = round(max(lo, min(raw, hi)), 3)

        # Overall performance summary
        avg_sharpe  = float(np.mean([s["sharpe"]   for s in per_symbol_stats]))
        avg_wr      = float(np.mean([s["win_rate"]  for s in per_symbol_stats]))
        avg_monthly = float(np.mean([s["monthly"]   for s in per_symbol_stats]))

        # Safety gate: only adopt if Sharpe improved ≥ 5% over yesterday's
        improvement = (avg_sharpe - baseline_sharpe) / max(abs(baseline_sharpe), 0.1)
        improved    = bool(improvement >= 0.05 or baseline_sharpe == 0.0)

        # Adaptive min_score: tighten if win rate is high, loosen if low
        current_min = baseline.get("min_score", _cfg.MIN_SIGNAL_SCORE)
        if avg_wr >= 65:
            new_min_score = min(current_min + 1.0, 82.0)   # Getting better → tighten
        elif avg_wr <= 50:
            new_min_score = max(current_min - 1.0, 65.0)   # Getting worse → loosen
        else:
            new_min_score = current_min

        new_params = {
            "date":         get_current_ist_time().strftime("%Y-%m-%d"),
            **avg_params,
            "min_score":    round(new_min_score, 1),
            "sharpe":       round(avg_sharpe, 3),
            "win_rate":     round(avg_wr, 1),
            "monthly_ret":  round(avg_monthly, 2),
            "symbols_used": [s["symbol"] for s in per_symbol_stats],
            "improved":     improved,
            "improvement_pct": round(improvement * 100, 1),
            "baseline_sharpe": round(baseline_sharpe, 3),
        }

        if improved:
            self._save_params(new_params)
            logger.info(
                f"[{format_ist_timestamp()}] ✅ EOD Self-Trainer: params UPDATED — "
                f"Sharpe {baseline_sharpe:.2f}→{avg_sharpe:.2f} "
                f"(+{improvement*100:.1f}%)"
            )
        else:
            # Still save for reference but keep yesterday's params active
            new_params["active"] = False
            logger.info(
                f"[{format_ist_timestamp()}] ℹ️ EOD Self-Trainer: no improvement "
                f"({improvement*100:+.1f}%) — keeping yesterday's params"
            )

        # Send Telegram summary
        if alerter:
            self._send_telegram_summary(alerter, new_params, per_symbol_stats, improved)

        return new_params

    def _save_params(self, params: Dict) -> None:
        """Save adaptive params to disk — read by main.py each morning."""
        try:
            ADAPTIVE_PARAMS_FILE.write_text(json.dumps(params, indent=2, cls=_NumpyEncoder))
            # Also archive daily copy
            archive = RESULTS_DIR / f"params_{params['date']}.json"
            archive.write_text(json.dumps(params, indent=2, cls=_NumpyEncoder))
        except Exception as e:
            logger.error(f"Failed to save adaptive params: {e}")

    @staticmethod
    def load_params() -> Dict:
        """
        Load yesterday's trained params.
        Called by main.py at 9:00 AM IST to apply for today's trading.
        Returns empty dict if no params file exists (use config defaults).
        """
        try:
            if ADAPTIVE_PARAMS_FILE.exists():
                data = json.loads(ADAPTIVE_PARAMS_FILE.read_text())
                # Only use if trained today or yesterday (not stale)
                from datetime import date, timedelta
                today     = get_current_ist_time().date()
                train_date = date.fromisoformat(data.get("date", "2000-01-01"))
                if (today - train_date).days <= 3:   # Accept params up to 3 days old
                    return data
        except Exception as e:
            logger.warning(f"Load adaptive params failed: {e}")
        return {}

    def _send_telegram_summary(
        self, alerter, params: Dict, sym_stats: List[Dict], improved: bool
    ) -> None:
        try:
            emoji  = "✅" if improved else "ℹ️"
            lines  = [
                f"{emoji} <b>EOD Self-Training Complete</b> — "
                f"{params.get('date', '')}",
                "",
                f"📊 <b>Optimized Parameters:</b>",
                f"  ATR SL:   {params.get('atr_sl', '-')}×",
                f"  ATR T1:   {params.get('atr_t1', '-')}×",
                f"  ATR T2:   {params.get('atr_t2', '-')}×",
                f"  Risk/trade: {params.get('risk_pct', '-')}%",
                f"  Min score:  {params.get('min_score', '-')}",
                "",
                f"📈 <b>Backtest Performance (OOS avg):</b>",
                f"  Sharpe: {params.get('baseline_sharpe',0):.2f} → "
                f"{params.get('sharpe',0):.2f} "
                f"({params.get('improvement_pct',0):+.1f}%)",
                f"  Win rate:    {params.get('win_rate',0):.1f}%",
                f"  Monthly est: {params.get('monthly_ret',0):.1f}%",
                "",
            ]
            if sym_stats:
                lines.append("🔬 <b>Symbol Results:</b>")
                for s in sym_stats[:5]:
                    lines.append(
                        f"  {s['symbol']:12s} WR={s['win_rate']:.0f}%  "
                        f"Sharpe={s['sharpe']:.2f}"
                    )
            if improved:
                lines.append("\n✅ Params adopted — trading tomorrow with updated strategy")
            else:
                lines.append("\nℹ️ No improvement — keeping yesterday's params")

            alerter.send_text("\n".join(lines))
        except Exception as e:
            logger.warning(f"EOD training Telegram summary failed: {e}")


# -----------------------------------------------------------
# MONTE CARLO SIMULATOR
# -----------------------------------------------------------

class MonteCarloSimulator:
    """
    Stress-tests the strategy by shuffling trade order 1000× times.

    18yr Rule: "If your strategy only works in one specific sequence of trades,
    it's not a strategy — it's luck. MC simulation exposes the difference."

    Key outputs:
      P10/P50/P90 final equity   → confidence band on expected outcome
      Ruin probability           → % of sims with drawdown > 40%
      Max drawdown distribution  → 90th percentile worst-case drawdown
    """

    def simulate(
        self,
        trades:       List[Dict],
        initial_capital: float = 100000,
        n_simulations:   int   = 1000,
        ruin_threshold:  float = 40.0,   # % drawdown = "ruin"
    ) -> Dict:
        if not trades or len(trades) < 5:
            return {"error": "Need at least 5 trades for MC simulation"}

        net_pnls     = [t["net_pnl"] for t in trades]
        final_equities: List[float] = []
        max_drawdowns:  List[float] = []
        ruin_count   = 0

        rng = random.Random(42)   # Reproducible

        for _ in range(n_simulations):
            shuffled = net_pnls[:]
            rng.shuffle(shuffled)

            capital  = initial_capital
            peak     = initial_capital
            max_dd   = 0.0

            for pnl in shuffled:
                capital += pnl
                peak     = max(peak, capital)
                dd       = (peak - capital) / peak * 100
                max_dd   = max(max_dd, dd)

            final_equities.append(capital)
            max_drawdowns.append(max_dd)
            if max_dd >= ruin_threshold:
                ruin_count += 1

        eq_arr  = sorted(final_equities)
        dd_arr  = sorted(max_drawdowns)
        n       = len(eq_arr)
        ruin_pct = ruin_count / n_simulations * 100

        def percentile(arr: list, p: float) -> float:
            idx = int(len(arr) * p / 100)
            return round(arr[min(idx, len(arr) - 1)], 2)

        result = {
            "simulations":         n_simulations,
            "ruin_probability_pct": round(ruin_pct, 2),
            "final_equity": {
                "p10": percentile(eq_arr, 10),
                "p25": percentile(eq_arr, 25),
                "p50": percentile(eq_arr, 50),
                "p75": percentile(eq_arr, 75),
                "p90": percentile(eq_arr, 90),
            },
            "max_drawdown": {
                "p50": percentile(dd_arr, 50),
                "p75": percentile(dd_arr, 75),
                "p90": percentile(dd_arr, 90),
                "p95": percentile(dd_arr, 95),
            },
            "expected_return_pct": round(
                (np.mean(final_equities) - initial_capital) / initial_capital * 100, 2
            ),
            "passes_mc": ruin_pct < 5.0,   # Must be < 5% ruin prob to be deployable
        }

        logger.info(
            f"[{format_ist_timestamp()}] MC ({n_simulations} sims): "
            f"P50 equity=₹{result['final_equity']['p50']:,.0f} | "
            f"Ruin={ruin_pct:.1f}% | "
            f"P90 drawdown={result['max_drawdown']['p90']:.1f}%"
        )
        return result

    def format_report(self, mc: Dict) -> str:
        if "error" in mc:
            return f"MC Error: {mc['error']}"
        p = mc["passes_mc"]
        lines = [
            f"\n{'✅' if p else '❌'} Monte Carlo ({mc['simulations']} simulations)",
            f"  Ruin probability:  {mc['ruin_probability_pct']:.1f}%  ({'SAFE' if p else 'RISKY'})",
            f"  Expected return:   {mc['expected_return_pct']:+.1f}%",
            f"  Final equity (₹):  P10={mc['final_equity']['p10']:,.0f}  "
            f"P50={mc['final_equity']['p50']:,.0f}  P90={mc['final_equity']['p90']:,.0f}",
            f"  Max drawdown (%):  P50={mc['max_drawdown']['p50']:.1f}  "
            f"P90={mc['max_drawdown']['p90']:.1f}  P95={mc['max_drawdown']['p95']:.1f}",
        ]
        return "\n".join(lines)


# -----------------------------------------------------------
# BENCHMARK COMPARATOR
# -----------------------------------------------------------

class BenchmarkComparator:
    """
    Compares strategy returns vs Nifty50 buy-and-hold.

    Key metrics:
      Alpha       = strategy return - benchmark return (excess return)
      Beta        = correlation of strategy returns to Nifty daily moves
      Info Ratio  = alpha / tracking_error (consistency of outperformance)
      Sharpe vs   = strategy Sharpe vs Nifty Sharpe
    """

    def compare(
        self,
        strategy_trades: List[Dict],
        initial_capital:  float = 100000,
        period_days:      int   = 180,
    ) -> Dict:
        try:
            import yfinance as yf
        except ImportError:
            return {"error": "yfinance not installed"}

        if not strategy_trades:
            return {"error": "No trades to compare"}

        # Fetch Nifty50 data for same period
        try:
            end_date   = get_current_ist_time()
            start_date = end_date - timedelta(days=period_days + 10)
            nifty = yf.download(
                "^NSEI", start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                progress=False, auto_adjust=True,
            )
            if nifty.empty:
                return {"error": "Could not fetch Nifty data"}
        except Exception as e:
            return {"error": f"Nifty fetch failed: {e}"}

        # Benchmark: buy-and-hold Nifty
        nifty_start = float(nifty["Close"].iloc[0])
        nifty_end   = float(nifty["Close"].iloc[-1])
        bm_return   = (nifty_end - nifty_start) / nifty_start * 100

        # Strategy equity curve
        capital     = initial_capital
        strat_pnls  = [t["net_pnl"] for t in strategy_trades]
        strat_return = sum(strat_pnls) / initial_capital * 100

        # Nifty daily returns
        nifty_daily = nifty["Close"].pct_change().dropna().values
        # Strategy daily P&L approximation
        strat_daily = np.array(strat_pnls)

        # Alpha & Beta (simple regression)
        if len(nifty_daily) >= 5 and len(strat_daily) >= 5:
            n = min(len(nifty_daily), len(strat_daily))
            x = nifty_daily[:n]
            y = strat_daily[:n] / initial_capital  # Convert to returns

            beta    = float(np.cov(x, y)[0][1] / (np.var(x) + 1e-9))
            alpha_d = float(np.mean(y) - beta * np.mean(x))
            alpha   = alpha_d * 252 * 100  # Annualised %

            # Information ratio
            excess_returns   = y - x * beta
            tracking_error   = float(np.std(excess_returns) * np.sqrt(252))
            info_ratio       = float(alpha / (tracking_error * 100 + 1e-9))

            # Sharpe comparison
            strat_sharpe = (np.mean(y) / (np.std(y) + 1e-9)) * np.sqrt(252)
            nifty_sharpe = (np.mean(x) / (np.std(x) + 1e-9)) * np.sqrt(252)
        else:
            beta = alpha = info_ratio = 0.0
            strat_sharpe = nifty_sharpe = 0.0

        result = {
            "strategy_return_pct":   round(strat_return, 2),
            "benchmark_return_pct":  round(bm_return, 2),
            "alpha_annualised_pct":  round(alpha, 2),
            "beta":                  round(beta, 3),
            "info_ratio":            round(info_ratio, 3),
            "strategy_sharpe":       round(float(strat_sharpe), 2),
            "benchmark_sharpe":      round(float(nifty_sharpe), 2),
            "outperformance_pct":    round(strat_return - bm_return, 2),
            "passes_benchmark":      strat_return > bm_return and alpha > 0,
        }

        logger.info(
            f"[{format_ist_timestamp()}] Benchmark: "
            f"Strategy {strat_return:+.1f}% vs Nifty {bm_return:+.1f}% | "
            f"Alpha={alpha:+.1f}% | Beta={beta:.2f} | IR={info_ratio:.2f}"
        )
        return result

    def format_report(self, bm: Dict) -> str:
        if "error" in bm:
            return f"Benchmark Error: {bm['error']}"
        p = bm.get("passes_benchmark", False)
        lines = [
            f"\n{'✅' if p else '❌'} Benchmark vs Nifty50",
            f"  Strategy return:  {bm['strategy_return_pct']:+.1f}%",
            f"  Nifty return:     {bm['benchmark_return_pct']:+.1f}%",
            f"  Outperformance:   {bm['outperformance_pct']:+.1f}%",
            f"  Alpha (ann):      {bm['alpha_annualised_pct']:+.1f}%",
            f"  Beta:             {bm['beta']:.3f}",
            f"  Info Ratio:       {bm['info_ratio']:.3f}",
            f"  Sharpe: strategy={bm['strategy_sharpe']:.2f} vs Nifty={bm['benchmark_sharpe']:.2f}",
        ]
        return "\n".join(lines)


# -----------------------------------------------------------
# SENSITIVITY ANALYZER
# -----------------------------------------------------------

class SensitivityAnalyzer:
    """
    Tests how strategy performance degrades when parameters shift ±10/20/30%.

    18yr Rule: "A robust strategy is one whose edge survives slightly wrong
    parameters. If it only works at one exact setting, it's curve-fitted."

    Robustness score 0-1:
      > 0.7 = robust (Sharpe stays within 30% across ±20% param range)
      0.4-0.7 = moderate (use with caution)
      < 0.4 = fragile (likely overfit — do NOT deploy)
    """

    def analyze(
        self,
        df:          pd.DataFrame,
        base_params: Dict,
        variations:  List[float] = None,
    ) -> Dict:
        if variations is None:
            variations = [-0.30, -0.20, -0.10, 0.0, 0.10, 0.20, 0.30]

        params_to_test = ["atr_sl", "atr_t1", "atr_t2", "risk_pct"]
        results: Dict[str, List] = {p: [] for p in params_to_test}

        base_sharpe = self._run_single(df, base_params)

        for param in params_to_test:
            base_val = base_params.get(param, 1.0)
            for var in variations:
                test_params = base_params.copy()
                test_params[param] = round(base_val * (1 + var), 3)
                sharpe = self._run_single(df, test_params)
                results[param].append({
                    "variation_pct": round(var * 100, 0),
                    "param_value":   test_params[param],
                    "sharpe":        round(sharpe, 3),
                    "sharpe_delta":  round(sharpe - base_sharpe, 3),
                })

        # Robustness score: average Sharpe retention at ±20% variation
        robustness_scores = []
        for param, param_results in results.items():
            vals_at_20 = [
                r["sharpe"] for r in param_results
                if abs(r["variation_pct"]) <= 20
            ]
            if vals_at_20 and base_sharpe > 0:
                avg_retention = np.mean(vals_at_20) / max(base_sharpe, 0.01)
                robustness_scores.append(avg_retention)

        robustness = round(float(np.mean(robustness_scores)) if robustness_scores else 0, 3)

        result = {
            "base_sharpe":    round(base_sharpe, 3),
            "robustness_score": robustness,
            "grade": ("ROBUST" if robustness > 0.7 else
                      "MODERATE" if robustness > 0.4 else "FRAGILE"),
            "param_sensitivity": results,
            "passes_sensitivity": robustness > 0.7,
        }

        logger.info(
            f"[{format_ist_timestamp()}] Sensitivity: "
            f"Robustness={robustness:.3f} ({result['grade']}) | "
            f"Base Sharpe={base_sharpe:.3f}"
        )
        return result

    def _run_single(self, df: pd.DataFrame, params: Dict) -> float:
        """Run a quick backtest and return Sharpe ratio."""
        try:
            signals = WalkForwardOptimizer(df)._generate_signals(
                df, params.get("atr_sl", 1.5)
            )
            if signals.empty:
                return 0.0
            engine = BacktestEngine(100000)
            stats  = engine.run(
                df, signals,
                atr_sl_mult  = params.get("atr_sl",   1.5),
                atr_t1_mult  = params.get("atr_t1",   2.5),
                atr_t2_mult  = params.get("atr_t2",   4.0),
                max_risk_pct = params.get("risk_pct",  0.5),
            )
            return float(stats.get("sharpe", 0.0))
        except Exception:
            return 0.0

    def format_report(self, sens: Dict) -> str:
        if "error" in sens:
            return f"Sensitivity Error: {sens['error']}"
        p = sens.get("passes_sensitivity", False)
        lines = [
            f"\n{'✅' if p else '❌'} Sensitivity Analysis — {sens['grade']}",
            f"  Base Sharpe:       {sens['base_sharpe']:.3f}",
            f"  Robustness score:  {sens['robustness_score']:.3f}  "
            f"(>0.7 = robust, 0.4-0.7 = moderate, <0.4 = fragile)",
        ]
        # Show worst param
        worst_param = ""
        worst_score = 9999
        for param, vals in sens.get("param_sensitivity", {}).items():
            sharpes = [v["sharpe"] for v in vals if v["sharpe"] > 0]
            if sharpes:
                avg = np.mean(sharpes)
                if avg < worst_score:
                    worst_score = avg
                    worst_param = param
        if worst_param:
            lines.append(f"  Most sensitive param: {worst_param}")
        return "\n".join(lines)


# -----------------------------------------------------------
# CLI ENTRY POINT
# -----------------------------------------------------------

def main():
    setup_logging(config.LOG_DIR, config.LOG_LEVEL)
    parser = argparse.ArgumentParser(
        description="NSE Bot Trainer — Backtesting + Walk-Forward + MC + Benchmark + Sensitivity"
    )
    parser.add_argument("--symbols",          default="RELIANCE,TCS,INFY")
    parser.add_argument("--days",             type=int,  default=180)
    parser.add_argument("--trials",           type=int,  default=50)
    parser.add_argument("--capital",          type=float, default=100000)
    parser.add_argument("--quick-test",       action="store_true", help="30d, all analyses fast")
    parser.add_argument("--full-optimization",action="store_true", help="Full walk-forward optimization")
    parser.add_argument("--monte-carlo",      action="store_true", help="Run MC simulation")
    parser.add_argument("--benchmark",        action="store_true", help="Benchmark vs Nifty50")
    parser.add_argument("--sensitivity",      action="store_true", help="Parameter sensitivity")
    parser.add_argument("--all",              action="store_true", help="Run all analyses")
    args = parser.parse_args()

    if args.quick_test or args.all:
        if args.quick_test:
            args.days   = 30
            args.trials = 20
        args.monte_carlo = args.benchmark = args.sensitivity = True

    symbols = [s.strip() for s in args.symbols.split(",")]
    logger.info(f"[{format_ist_timestamp()}] Trainer. Symbols: {symbols}, Days: {args.days}")

    try:
        from data_fetch_groww import get_data_fetcher
        fetcher = get_data_fetcher()
    except Exception as e:
        logger.error(f"Cannot initialize data fetcher: {e}")
        sys.exit(1)

    all_results   = {}
    mc_runner     = MonteCarloSimulator()
    bm_runner     = BenchmarkComparator()
    sens_runner   = SensitivityAnalyzer()

    for symbol in symbols:
        logger.info(f"[{format_ist_timestamp()}] ── Processing {symbol} ──")
        try:
            df = fetcher.get_candles(symbol, interval="5m", days=args.days)
            if df is None or len(df) < 100:
                logger.warning(f"Insufficient data for {symbol}")
                continue

            try:
                from pattern_recognition import TechnicalIndicators
                df = TechnicalIndicators().compute(df)
            except Exception as e:
                logger.warning(f"Indicator compute failed {symbol}: {e}")
                continue

            sym_result: Dict = {}

            # ── Core backtest ──────────────────────────────────
            engine  = BacktestEngine(args.capital)
            signals = WalkForwardOptimizer(df)._generate_signals(df, 1.5)
            bt      = engine.run(df, signals) if not signals.empty else {"error": "no signals"}
            sym_result["backtest"] = bt

            if "win_rate" in bt:
                p = "✅" if bt.get("passes_targets") else "❌"
                print(
                    f"\n{p} {symbol} Backtest: "
                    f"WR={bt['win_rate']:.1f}%  Sharpe={bt['sharpe']:.2f}  "
                    f"Sortino={bt['sortino']:.2f}  Calmar={bt['calmar']:.2f}  "
                    f"DD={bt['max_drawdown']:.1f}%  Monthly={bt['monthly_return']:.1f}%  "
                    f"PF={bt['profit_factor']:.2f}"
                )
                # Session breakdown
                if bt.get("sessions"):
                    print("  Sessions:")
                    for sname, sdata in bt["sessions"].items():
                        print(
                            f"    {sname:15s} ({sdata['label']}): "
                            f"{sdata['trades']} trades | "
                            f"WR={sdata['win_rate']:.0f}% | "
                            f"P&L=₹{sdata['total_pnl']:+,.0f}"
                        )

            # ── Walk-forward optimization ──────────────────────
            if args.full_optimization:
                optimizer = WalkForwardOptimizer(df, n_splits=5)
                wf = optimizer.optimize(n_trials=args.trials)
                sym_result["walk_forward"] = wf
                if wf.get("best_params"):
                    print(f"  WF best params: {wf['best_params']}")

            # ── Monte Carlo ────────────────────────────────────
            if getattr(args, "monte_carlo", False) and engine.trades:
                mc = mc_runner.simulate(engine.trades, args.capital)
                sym_result["monte_carlo"] = mc
                print(mc_runner.format_report(mc))

            # ── Benchmark vs Nifty ─────────────────────────────
            if getattr(args, "benchmark", False) and engine.trades:
                bm = bm_runner.compare(engine.trades, args.capital, args.days)
                sym_result["benchmark"] = bm
                print(bm_runner.format_report(bm))

            # ── Sensitivity analysis ───────────────────────────
            if getattr(args, "sensitivity", False):
                base_params = {"atr_sl": 1.5, "atr_t1": 2.5, "atr_t2": 4.0, "risk_pct": 0.5}
                sens = sens_runner.analyze(df, base_params)
                sym_result["sensitivity"] = sens
                print(sens_runner.format_report(sens))

            all_results[symbol] = sym_result

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Trainer failed for {symbol}: {e}")

    out_file = RESULTS_DIR / f"trainer_{get_current_ist_time().strftime('%Y%m%d_%H%M')}.json"
    with open(out_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"[{format_ist_timestamp()}] Results → {out_file}")
    print(f"\n✅ Trainer complete. Results saved → {out_file}")


if __name__ == "__main__":
    main()
