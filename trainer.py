"""
trainer.py — NSE Momentum Groww AI Bot
Offline Backtesting + Walk-Forward Optimization + Adaptive Learning

⚠️ Trainer runs OFFLINE only — never places real orders.
⚠️ Backtest results do not guarantee live performance.

Features:
- Historical NSE 5-min data backtesting (via CSV or Groww API)
- Walk-forward optimization (no look-ahead bias)
- Optuna hyperparameter tuning (RSI levels, volume filter, ATR multiplier)
- Tracks win rate, Sharpe ratio, max drawdown per symbol/pattern
- Auto-suggests config improvements for live bot
- Target: 5% net monthly return, >55% win rate, Sharpe >1.5, DD <8%

Usage:
    python trainer.py --symbols RELIANCE,TCS --days 90
    python trainer.py --full-optimization
    python trainer.py --report
"""

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from utils import (
    format_ist_timestamp, get_current_ist_time,
    convert_candle_timestamps_to_ist, setup_logging
)

logger = logging.getLogger(__name__)

# Trainer output directory
RESULTS_DIR = Path("trainer_results")
RESULTS_DIR.mkdir(exist_ok=True)

# Backtest cost assumptions (0.1% total round-trip)
SLIPPAGE_PCT   = 0.05   # 0.05% per leg
BROKERAGE_PCT  = 0.03   # Groww intraday brokerage
STT_PCT        = 0.025  # STT on sell side
TOTAL_COST_PCT = SLIPPAGE_PCT + BROKERAGE_PCT + STT_PCT  # ~0.1% per trade


# ============================================================
# BACKTEST ENGINE
# ============================================================

class BacktestEngine:
    """
    Vectorized backtest engine for NSE intraday momentum strategy.
    Runs on historical 5-min OHLCV data.
    """

    def __init__(self, params: Dict = None):
        self.params = params or self._default_params()

    def _default_params(self) -> Dict:
        return {
            "rsi_oversold": 35.0,
            "rsi_overbought": 65.0,
            "volume_multiplier": 2.0,
            "atr_sl_mult": 1.5,
            "atr_tp_mult": 3.0,
            "min_adx": 20.0,
            "ema_fast": 9,
            "ema_slow": 21,
            "min_signal_score": 65.0,
        }

    def run(
        self,
        df: pd.DataFrame,
        symbol: str = "",
        initial_capital: float = 100000,
    ) -> Dict:
        """
        Run backtest on a DataFrame of 5-min OHLCV candles.
        Returns performance metrics.
        """
        try:
            from pattern_recognition import TechnicalIndicators
            indicators = TechnicalIndicators()
            df = indicators.compute(df.copy())
        except Exception as e:
            logger.error(f"Indicator computation failed: {e}")
            return self._empty_result(symbol)

        trades = []
        capital = initial_capital
        equity_curve = [capital]
        in_trade = False
        entry_price = sl = tp = qty = direction = None

        for i in range(50, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i - 1]

            # Market hours filter (9:15–15:20 IST)
            if hasattr(df.index, 'time'):
                t = df.index[i].time()
                from datetime import time as dtime
                if not (dtime(9, 15) <= t <= dtime(15, 20)):
                    continue
                # Force exit before 3:20 PM
                if in_trade and t >= dtime(15, 20):
                    exit_price = float(row["close"])
                    pnl = self._calc_pnl(direction, entry_price, exit_price, qty, capital)
                    capital += pnl["net_pnl"]
                    trades.append({**pnl, "exit_reason": "EOD", "symbol": symbol})
                    equity_curve.append(capital)
                    in_trade = False
                    continue

            # Manage open trade
            if in_trade:
                close = float(row["close"])
                high  = float(row["high"])
                low   = float(row["low"])

                # Check SL/TP
                hit_sl = (direction == "LONG" and low <= sl) or \
                         (direction == "SHORT" and high >= sl)
                hit_tp = (direction == "LONG" and high >= tp) or \
                         (direction == "SHORT" and low <= tp)

                if hit_sl:
                    exit_p = sl
                    pnl = self._calc_pnl(direction, entry_price, exit_p, qty, capital)
                    capital += pnl["net_pnl"]
                    trades.append({**pnl, "exit_reason": "SL", "symbol": symbol})
                    equity_curve.append(capital)
                    in_trade = False
                elif hit_tp:
                    exit_p = tp
                    pnl = self._calc_pnl(direction, entry_price, exit_p, qty, capital)
                    capital += pnl["net_pnl"]
                    trades.append({**pnl, "exit_reason": "TP", "symbol": symbol})
                    equity_curve.append(capital)
                    in_trade = False
                continue

            # Look for entry signals
            signal = self._generate_backtest_signal(df, i, self.params)
            if signal is None:
                continue

            close = float(row["close"])
            atr = float(row.get("atr", close * 0.01))
            risk_per_trade = capital * 0.005  # 0.5% risk

            if signal == "LONG":
                entry_price = close
                sl = entry_price - self.params["atr_sl_mult"] * atr
                tp = entry_price + self.params["atr_tp_mult"] * atr
                sl_dist = entry_price - sl
            else:
                entry_price = close
                sl = entry_price + self.params["atr_sl_mult"] * atr
                tp = entry_price - self.params["atr_tp_mult"] * atr
                sl_dist = sl - entry_price

            if sl_dist <= 0:
                continue

            qty = max(1, int(risk_per_trade / sl_dist))
            capital_used = entry_price * qty
            if capital_used > capital * 0.15:
                qty = max(1, int((capital * 0.15) / entry_price))

            direction = signal
            in_trade = True

        return self._compute_metrics(trades, equity_curve, initial_capital, symbol)

    def _generate_backtest_signal(self, df: pd.DataFrame, idx: int, params: Dict) -> Optional[str]:
        """Generate LONG/SHORT/None signal for backtesting."""
        row  = df.iloc[idx]
        prev = df.iloc[idx - 1]

        rsi    = float(row.get("rsi", 50))
        macd_h = float(row.get("macd_hist", 0))
        vol_r  = float(row.get("volume_ratio", 1))
        adx    = float(row.get("adx", 0))
        ema9   = float(row.get("ema9", 0))
        ema21  = float(row.get("ema21", 0))
        close  = float(row["close"])
        prev_macd_h = float(prev.get("macd_hist", 0))

        # Volume filter
        if vol_r < params["volume_multiplier"] * 0.7:
            return None

        # ADX filter
        if adx < params["min_adx"]:
            return None

        # LONG: RSI recovering from oversold + MACD hist turning positive + EMA bullish
        if (rsi < params["rsi_overbought"] and
                prev_macd_h < 0 and macd_h > 0 and
                ema9 > ema21 and close > ema9 and
                vol_r >= params["volume_multiplier"] * 0.8):
            return "LONG"

        # SHORT: RSI at overbought + MACD hist turning negative + EMA bearish
        if (rsi > params["rsi_oversold"] and
                prev_macd_h > 0 and macd_h < 0 and
                ema9 < ema21 and close < ema9 and
                vol_r >= params["volume_multiplier"] * 0.8):
            return "SHORT"

        return None

    def _calc_pnl(
        self, direction: str, entry: float, exit_price: float,
        qty: int, capital: float
    ) -> Dict:
        if direction == "LONG":
            gross_pnl = (exit_price - entry) * qty
        else:
            gross_pnl = (entry - exit_price) * qty

        cost = entry * qty * (TOTAL_COST_PCT / 100)
        net_pnl = gross_pnl - cost
        return {
            "direction": direction,
            "entry": entry,
            "exit": exit_price,
            "qty": qty,
            "gross_pnl": round(gross_pnl, 2),
            "cost": round(cost, 2),
            "net_pnl": round(net_pnl, 2),
            "return_pct": round((net_pnl / (entry * qty)) * 100, 3),
            "win": net_pnl > 0,
        }

    def _compute_metrics(
        self, trades: List[Dict], equity: List[float],
        initial: float, symbol: str
    ) -> Dict:
        if not trades:
            return self._empty_result(symbol)

        df_t = pd.DataFrame(trades)
        wins = df_t[df_t["win"] == True]
        losses = df_t[df_t["win"] == False]

        win_rate = len(wins) / len(df_t) * 100
        total_pnl = df_t["net_pnl"].sum()
        avg_win = wins["net_pnl"].mean() if len(wins) > 0 else 0
        avg_loss = abs(losses["net_pnl"].mean()) if len(losses) > 0 else 0
        profit_factor = (wins["net_pnl"].sum() / abs(losses["net_pnl"].sum())
                         if losses["net_pnl"].sum() != 0 else float("inf"))

        # Sharpe ratio (annualized, assuming 250 trading days)
        returns = df_t["return_pct"].values
        sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(250)
                  if np.std(returns) > 0 else 0)

        # Max drawdown
        eq_series = pd.Series(equity)
        roll_max = eq_series.expanding().max()
        drawdown = (eq_series - roll_max) / roll_max * 100
        max_dd = abs(drawdown.min())

        # Monthly return estimate
        monthly_return = (total_pnl / initial) * 100 / max(len(trades) / 20, 1)

        return {
            "symbol": symbol,
            "total_trades": len(df_t),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round((total_pnl / initial) * 100, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "monthly_return_est_pct": round(monthly_return, 2),
            "meets_targets": (
                win_rate >= 55 and
                sharpe >= 1.5 and
                max_dd <= 8 and
                monthly_return >= 5
            ),
        }

    def _empty_result(self, symbol: str) -> Dict:
        return {
            "symbol": symbol, "total_trades": 0, "win_rate": 0,
            "total_pnl": 0, "total_return_pct": 0, "avg_win": 0,
            "avg_loss": 0, "profit_factor": 0, "sharpe": 0,
            "max_drawdown_pct": 0, "monthly_return_est_pct": 0,
            "meets_targets": False,
        }


# ============================================================
# WALK-FORWARD OPTIMIZER (using Optuna)
# ============================================================

class WalkForwardOptimizer:
    """
    Walk-forward optimization to prevent overfitting.
    Trains on in-sample window, tests on out-of-sample.
    """

    def __init__(self, n_trials: int = 100, n_folds: int = 4):
        self.n_trials = n_trials
        self.n_folds = n_folds

    def optimize(self, df: pd.DataFrame, symbol: str = "") -> Dict:
        """Run walk-forward optimization on historical data."""
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            logger.warning("optuna not installed — using default params")
            return BacktestEngine()._default_params()

        fold_size = len(df) // (self.n_folds + 1)
        all_results = []

        logger.info(f"[{format_ist_timestamp()}] Starting WFO for {symbol}: "
                    f"{self.n_folds} folds, {self.n_trials} trials each")

        for fold in range(self.n_folds):
            train_start = fold * fold_size
            train_end   = train_start + fold_size
            test_start  = train_end
            test_end    = test_start + fold_size

            train_df = df.iloc[train_start:train_end]
            test_df  = df.iloc[test_start:test_end]

            if len(train_df) < 100 or len(test_df) < 50:
                continue

            def objective(trial):
                params = {
                    "rsi_oversold":       trial.suggest_float("rsi_oversold", 25, 45),
                    "rsi_overbought":     trial.suggest_float("rsi_overbought", 55, 75),
                    "volume_multiplier":  trial.suggest_float("volume_multiplier", 1.2, 3.0),
                    "atr_sl_mult":        trial.suggest_float("atr_sl_mult", 1.0, 2.5),
                    "atr_tp_mult":        trial.suggest_float("atr_tp_mult", 2.0, 4.0),
                    "min_adx":            trial.suggest_float("min_adx", 15, 35),
                    "ema_fast":           trial.suggest_int("ema_fast", 5, 15),
                    "ema_slow":           trial.suggest_int("ema_slow", 15, 30),
                    "min_signal_score":   trial.suggest_float("min_signal_score", 55, 80),
                }
                engine = BacktestEngine(params)
                result = engine.run(train_df, symbol)
                # Objective: maximize Sharpe * win_rate, penalize drawdown
                score = (result["sharpe"] * result["win_rate"] / 100 -
                         result["max_drawdown_pct"] * 0.1)
                return score

            study = optuna.create_study(direction="maximize")
            study.optimize(objective, n_trials=self.n_trials, show_progress_bar=False)
            best_params = study.best_params

            # Test on out-of-sample
            engine = BacktestEngine(best_params)
            oos_result = engine.run(test_df, symbol)
            oos_result["fold"] = fold
            oos_result["best_params"] = best_params
            all_results.append(oos_result)

            logger.info(
                f"[{format_ist_timestamp()}] Fold {fold+1}/{self.n_folds}: "
                f"WR={oos_result['win_rate']:.1f}% | "
                f"Sharpe={oos_result['sharpe']:.2f} | "
                f"DD={oos_result['max_drawdown_pct']:.1f}%"
            )

        if not all_results:
            return BacktestEngine()._default_params()

        # Average best params across folds
        param_keys = list(all_results[0].get("best_params", {}).keys())
        avg_params = {}
        for key in param_keys:
            vals = [r["best_params"].get(key, 0) for r in all_results if "best_params" in r]
            avg_params[key] = round(float(np.mean(vals)), 3)

        # Summary
        avg_wr  = np.mean([r["win_rate"] for r in all_results])
        avg_sr  = np.mean([r["sharpe"] for r in all_results])
        avg_dd  = np.mean([r["max_drawdown_pct"] for r in all_results])
        avg_ret = np.mean([r["monthly_return_est_pct"] for r in all_results])

        logger.info(
            f"[{format_ist_timestamp()}] WFO Complete for {symbol} | "
            f"Avg WR: {avg_wr:.1f}% | Sharpe: {avg_sr:.2f} | "
            f"DD: {avg_dd:.1f}% | Monthly: {avg_ret:.1f}%"
        )

        return {
            "optimized_params": avg_params,
            "wfo_summary": {
                "avg_win_rate": round(avg_wr, 1),
                "avg_sharpe": round(avg_sr, 2),
                "avg_max_drawdown": round(avg_dd, 1),
                "avg_monthly_return": round(avg_ret, 1),
                "meets_targets": avg_wr >= 55 and avg_sr >= 1.5 and avg_dd <= 8,
            },
            "fold_results": all_results,
        }


# ============================================================
# DATA LOADER
# ============================================================

def load_historical_data(
    symbol: str,
    days: int = 180,
    data_dir: str = "data"
) -> Optional[pd.DataFrame]:
    """
    Load historical 5-min data.
    First checks local CSV cache, then fetches from Groww API.
    """
    Path(data_dir).mkdir(exist_ok=True)
    csv_path = Path(data_dir) / f"{symbol}_5m.csv"

    if csv_path.exists():
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        df = convert_candle_timestamps_to_ist(df)
        logger.info(f"Loaded {len(df)} rows from cache: {csv_path}")
        return df

    # Fetch from Groww
    try:
        from data_fetch_groww import get_data_fetcher
        fetcher = get_data_fetcher()
        df = fetcher.get_candles(symbol, interval="5m", days=days)
        if df is not None and not df.empty:
            df.to_csv(csv_path)
            logger.info(f"Fetched and cached {len(df)} rows for {symbol}")
        return df
    except Exception as e:
        logger.error(f"Failed to load data for {symbol}: {e}")
        return None


# ============================================================
# MAIN CLI
# ============================================================

def main():
    setup_logging(level="INFO")
    parser = argparse.ArgumentParser(description="NSE Momentum Bot Trainer")
    parser.add_argument("--symbols", default="RELIANCE,TCS,INFY,HDFCBANK,SBIN",
                        help="Comma-separated NSE symbols")
    parser.add_argument("--days", type=int, default=180,
                        help="Days of historical data")
    parser.add_argument("--trials", type=int, default=100,
                        help="Optuna optimization trials per fold")
    parser.add_argument("--full-optimization", action="store_true",
                        help="Run full walk-forward optimization")
    parser.add_argument("--report", action="store_true",
                        help="Print saved results")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    logger.info(f"[{format_ist_timestamp()}] Trainer starting | Symbols: {symbols}")

    all_results = {}
    optimized_params = {}

    for symbol in symbols:
        logger.info(f"\n{'='*60}\nProcessing {symbol}...\n{'='*60}")
        df = load_historical_data(symbol, days=args.days)
        if df is None or df.empty:
            logger.warning(f"No data for {symbol} — skipping")
            continue

        if args.full_optimization:
            optimizer = WalkForwardOptimizer(n_trials=args.trials)
            wfo_result = optimizer.optimize(df, symbol)
            all_results[symbol] = wfo_result
            optimized_params[symbol] = wfo_result.get("optimized_params", {})

            summary = wfo_result.get("wfo_summary", {})
            print(f"\n{symbol} WFO Results:")
            print(f"  Win Rate:      {summary.get('avg_win_rate', 0):.1f}%  (target: >55%)")
            print(f"  Sharpe:        {summary.get('avg_sharpe', 0):.2f}  (target: >1.5)")
            print(f"  Max Drawdown:  {summary.get('avg_max_drawdown', 0):.1f}%  (target: <8%)")
            print(f"  Monthly Ret:   {summary.get('avg_monthly_return', 0):.1f}%  (target: >5%)")
            meets = summary.get("meets_targets", False)
            print(f"  Meets Targets: {'✅ YES' if meets else '❌ NO'}")
        else:
            engine = BacktestEngine()
            result = engine.run(df, symbol)
            all_results[symbol] = result
            print(f"\n{symbol} Backtest:")
            print(f"  Trades:        {result['total_trades']}")
            print(f"  Win Rate:      {result['win_rate']:.1f}%")
            print(f"  Total Return:  {result['total_return_pct']:.1f}%")
            print(f"  Sharpe:        {result['sharpe']:.2f}")
            print(f"  Max Drawdown:  {result['max_drawdown_pct']:.1f}%")
            print(f"  Monthly Est:   {result['monthly_return_est_pct']:.1f}%")
            meets = result.get("meets_targets", False)
            print(f"  Meets Targets: {'✅ YES' if meets else '❌ NO'}")

    # Save results
    results_file = RESULTS_DIR / f"results_{get_current_ist_time().strftime('%Y%m%d_%H%M')}.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"Results saved to {results_file}")

    if optimized_params:
        params_file = RESULTS_DIR / "optimized_params.json"
        with open(params_file, "w") as f:
            json.dump(optimized_params, f, indent=2)
        logger.info(f"Optimized params saved to {params_file}")
        print(f"\n💡 Copy optimized params to config.py for live trading!")


if __name__ == "__main__":
    main()
