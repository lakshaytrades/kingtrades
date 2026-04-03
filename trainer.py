"""
trainer.py — NSE Momentum Groww AI Bot
Walk-Forward Backtesting + Optuna Strategy Optimization

⚠️ OFFLINE ONLY — Trainer never places live orders.
Run this BEFORE going live to validate and optimize strategy parameters.

18yr Rule: "Backtest is NOT a crystal ball. It's a confidence builder.
Walk-forward validation is the ONLY honest way to test a strategy."

Usage:
  python trainer.py --symbols RELIANCE,TCS --days 365
  python trainer.py --full-optimization
  python trainer.py --quick-test

Targets:
  Win rate > 55% | Sharpe > 1.5 | Max drawdown < 8% | Net return > 5%/month
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

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

        net_pnls   = [t["net_pnl"] for t in self.trades]
        wins       = [p for p in net_pnls if p > 0]
        losses     = [p for p in net_pnls if p <= 0]
        total_pnl  = sum(net_pnls)
        win_rate   = len(wins) / len(net_pnls) * 100

        # Sharpe ratio (annualised, daily returns proxy)
        pnl_arr   = np.array(net_pnls)
        sharpe    = (np.mean(pnl_arr) / (np.std(pnl_arr) + 1e-9)) * np.sqrt(252)

        # Max drawdown
        eq_arr   = np.array(equity)
        peak_arr = np.maximum.accumulate(eq_arr)
        dd_arr   = (eq_arr - peak_arr) / (peak_arr + 1e-9) * 100
        max_dd   = float(abs(dd_arr.min()))

        # Monthly return estimate
        total_return_pct = (self.capital - self.initial_capital) / self.initial_capital * 100
        n_days           = len(equity)
        monthly_return   = total_return_pct / max(n_days / 22, 1)

        # Profit factor
        profit_factor = abs(sum(wins)) / (abs(sum(losses)) + 1e-9)

        passes_targets = (
            win_rate      >= config.TARGET_WIN_RATE and
            sharpe        >= config.TARGET_SHARPE and
            max_dd        <= config.MAX_DRAWDOWN_LIMIT and
            monthly_return >= config.TARGET_MONTHLY_RETURN_PCT
        )

        return {
            "total_trades":     len(self.trades),
            "wins":             len(wins),
            "losses":           len(losses),
            "win_rate":         round(win_rate, 2),
            "total_pnl":        round(total_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "monthly_return":   round(monthly_return, 2),
            "sharpe":           round(sharpe, 2),
            "max_drawdown":     round(max_dd, 2),
            "profit_factor":    round(profit_factor, 2),
            "final_capital":    round(self.capital, 2),
            "passes_targets":   passes_targets,
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
            json.dump({"params": params, "results": [r["oos"] for r in results]}, f, indent=2)
        logger.info(f"[{format_ist_timestamp()}] Optimized params saved → {out}")


# -----------------------------------------------------------
# CLI ENTRY POINT
# -----------------------------------------------------------

def main():
    setup_logging(config.LOG_DIR, config.LOG_LEVEL)
    parser = argparse.ArgumentParser(description="NSE Bot Trainer")
    parser.add_argument("--symbols",    default="RELIANCE,TCS,INFY", help="Comma-separated symbols")
    parser.add_argument("--days",       type=int, default=180,        help="Days of history")
    parser.add_argument("--trials",     type=int, default=50,         help="Optuna trials per split")
    parser.add_argument("--quick-test", action="store_true",          help="Quick 30-day test")
    parser.add_argument("--full-optimization", action="store_true",   help="Full walk-forward optimization")
    args = parser.parse_args()

    if args.quick_test:
        args.days   = 30
        args.trials = 20

    symbols = [s.strip() for s in args.symbols.split(",")]
    logger.info(f"[{format_ist_timestamp()}] Trainer started. Symbols: {symbols}, Days: {args.days}")

    # Fetch data using Groww fetcher
    try:
        from data_fetch_groww import get_data_fetcher
        fetcher = get_data_fetcher()
    except Exception as e:
        logger.error(f"Cannot initialize data fetcher: {e}")
        sys.exit(1)

    all_results = {}
    for symbol in symbols:
        logger.info(f"[{format_ist_timestamp()}] Processing {symbol}...")
        try:
            df = fetcher.get_candles(symbol, interval="5m", days=args.days)
            if df is None or len(df) < 100:
                logger.warning(f"Insufficient data for {symbol}")
                continue

            # Add indicators
            try:
                from pattern_recognition import TechnicalIndicators
                ti = TechnicalIndicators()
                df = ti.compute(df)
            except Exception as e:
                logger.warning(f"Indicator compute failed: {e}")
                continue

            # Run optimization or quick backtest
            if args.full_optimization or args.quick_test:
                optimizer = WalkForwardOptimizer(df, n_splits=3 if args.quick_test else 5)
                result    = optimizer.optimize(n_trials=args.trials)
            else:
                engine = BacktestEngine(100000)
                signals = WalkForwardOptimizer(df)._generate_signals(df, 1.5)
                result  = engine.run(df, signals)

            all_results[symbol] = result

            if isinstance(result, dict) and "win_rate" in result:
                passes = "✅" if result.get("passes_targets") else "❌"
                print(
                    f"\n{passes} {symbol}: "
                    f"WR={result.get('win_rate',0):.1f}%  "
                    f"Sharpe={result.get('sharpe',0):.2f}  "
                    f"DD={result.get('max_drawdown',0):.1f}%  "
                    f"Monthly={result.get('monthly_return',0):.1f}%"
                )

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Trainer failed for {symbol}: {e}")

    # Save all results
    out_file = RESULTS_DIR / f"trainer_results_{get_current_ist_time().strftime('%Y%m%d_%H%M')}.json"
    with open(out_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"[{format_ist_timestamp()}] Results saved → {out_file}")
    print(f"\nTrainer complete. Results: {out_file}")


if __name__ == "__main__":
    main()
