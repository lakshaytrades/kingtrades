"""
historical_backtester.py — Evidence-Based Strategy Validation

Fetches 2 years of Alpaca daily bars + 6 months of 5-min bars for the
top 20 watchlist symbols. Runs the existing pattern recognition on each bar,
simulates entries/exits with ATR-based SL/TP, and calculates:
  - Per-pattern win rate, profit factor, avg R:R
  - Overall system win rate, Sharpe ratio, max drawdown
  - Regime-based performance breakdown
  - Best/worst hours of day

Results saved to data/backtest_results.json for:
  - AdaptiveBrain warm-up (pre-calibrated pattern weights)
  - Confidence-based position sizing (Kelly)
  - Dashboard display

Usage: python historical_backtester.py [--symbols NVDA,TSLA] [--days 365]
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

# ET timezone — US market timestamps
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except ImportError:
    import pytz
    ET = pytz.timezone("America/New_York")

BACKTEST_RESULTS_FILE = Path("data/backtest_results.json")

# Top 15 default symbols from US_WATCHLIST
DEFAULT_SYMBOLS = [
    "NVDA", "TSLA", "AAPL", "MSFT", "META",
    "AMZN", "GOOGL", "AMD", "NFLX", "COIN",
    "SHOP", "UBER", "PLTR", "JPM", "GS",
]

# Minimum bars needed in window before we score
MIN_WINDOW_BARS = 50


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""
    symbols:      List[str] = field(default_factory=lambda: DEFAULT_SYMBOLS[:15])
    lookback_days: int       = 365
    initial_capital: float   = 50_000.0
    risk_pct:        float   = 0.5        # % of capital risked per trade
    atr_sl_mult:     float   = 1.5        # ATR multiplier for stop-loss
    atr_tp_mult:     float   = 3.0        # ATR multiplier for take-profit
    min_score:       float   = 90.0       # minimum signal score to enter


@dataclass
class BacktestResult:
    """Aggregated results from a backtest run."""
    total_trades:     int
    wins:             int
    losses:           int
    win_rate:         float
    profit_factor:    float              # gross_profit / gross_loss
    sharpe_ratio:     float
    max_drawdown_pct: float
    avg_rr:           float
    net_return_pct:   float
    pattern_stats:    Dict[str, Dict]   # per-pattern: {trades, wins, win_rate, profit_factor}
    hourly_stats:     Dict[int, Dict]   # per-hour-of-day: {trades, wins, win_rate}
    regime_stats:     Dict[str, Dict]   # per-regime: {trades, wins, win_rate}
    best_patterns:    List[str]         # top 5 by win_rate, >= 10 trades
    worst_patterns:   List[str]         # bottom 5 by win_rate, >= 10 trades
    generated_at:     str               # ET timestamp


# ─────────────────────────────────────────────────────────────────────────────
# INLINE INDICATOR HELPERS (no signal_generator import to avoid circular deps)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average — pure numpy."""
    result = np.full_like(series, np.nan, dtype=float)
    if len(series) < period:
        return result
    k = 2.0 / (period + 1)
    # Seed with first SMA
    result[period - 1] = np.mean(series[:period])
    for i in range(period, len(series)):
        result[i] = series[i] * k + result[i - 1] * (1 - k)
    return result


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    """RSI of last bar."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """ATR of last bar using SMA-smoothed true range."""
    if len(highs) < period + 1:
        return float(np.mean(highs[-period:] - lows[-period:])) if len(highs) > 0 else 0.0
    tr_vals = []
    for i in range(1, period + 1):
        idx = len(highs) - period - 1 + i
        hl  = highs[idx] - lows[idx]
        hpc = abs(highs[idx] - closes[idx - 1])
        lpc = abs(lows[idx]  - closes[idx - 1])
        tr_vals.append(max(hl, hpc, lpc))
    return float(np.mean(tr_vals))


def _vwap(df_window: pd.DataFrame) -> float:
    """Rolling VWAP for the window (treats all bars as intraday)."""
    typical = (df_window["high"] + df_window["low"] + df_window["close"]) / 3.0
    vol     = df_window["volume"].replace(0, 1)
    cumtp   = (typical * vol).cumsum()
    cumvol  = vol.cumsum()
    vwap_series = cumtp / cumvol
    return float(vwap_series.iloc[-1])


def _volume_ratio(volumes: np.ndarray, period: int = 20) -> float:
    """Current volume vs 20-bar SMA."""
    if len(volumes) < period + 1:
        return 1.0
    sma_vol = float(np.mean(volumes[-(period + 1):-1]))
    if sma_vol == 0:
        return 1.0
    return float(volumes[-1]) / sma_vol


def _detect_regime(daily_closes: np.ndarray) -> str:
    """
    Classify market regime from daily closes.
    Returns: "TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"
    """
    if len(daily_closes) < 20:
        return "RANGING"
    ema20 = _ema(daily_closes, 20)
    ema50 = _ema(daily_closes, 50) if len(daily_closes) >= 50 else ema20
    last_close = daily_closes[-1]
    e20 = ema20[-1] if not np.isnan(ema20[-1]) else last_close
    e50 = ema50[-1] if not np.isnan(ema50[-1]) else last_close

    # Volatility: std of returns over last 20 days
    returns  = np.diff(daily_closes[-21:]) / daily_closes[-21:-1]
    vol_pct  = float(np.std(returns) * 100) if len(returns) > 0 else 0.0

    if vol_pct > 3.0:
        return "VOLATILE"
    if last_close > e20 > e50:
        return "TRENDING_UP"
    if last_close < e20 < e50:
        return "TRENDING_DOWN"
    return "RANGING"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────────────────────────────────────

class HistoricalBacktester:
    """
    Evidence-based strategy validator.

    Fetches historical data, runs inline indicator scoring,
    simulates ATR-based entries/exits, and produces BacktestResult.
    """

    def __init__(self):
        # Defer all imports and API calls — nothing in __init__
        self._fetcher = None

    def _get_fetcher(self):
        if self._fetcher is None:
            from data_fetch_alpaca import get_data_fetcher
            self._fetcher = get_data_fetcher()
        return self._fetcher

    # ── PUBLIC API ─────────────────────────────────────────────────────────

    def run(self, config: BacktestConfig) -> BacktestResult:
        """
        Run the full backtest.

        1. For each symbol: fetch daily bars (for regime) + 5-min bars
        2. For each 5-min bar window of 50 bars: score → trade if score >= min_score
        3. Walk forward bar-by-bar to find SL/TP exit
        4. Aggregate into BacktestResult, save, return.
        """
        fetcher = self._get_fetcher()

        all_trades: List[Dict] = []

        for sym in config.symbols:
            logger.info(
                f"[{format_ist_timestamp()}] Backtester: fetching {sym} "
                f"({config.lookback_days}d daily + 90d 5-min)..."
            )

            # ── 1. Fetch daily bars for regime detection ──────────────────
            try:
                daily_df = fetcher.get_ohlcv(
                    sym, interval="day", lookback_days=config.lookback_days
                )
            except Exception as exc:
                logger.warning(
                    f"[{format_ist_timestamp()}] Backtester: {sym} daily fetch failed: {exc}"
                )
                daily_df = pd.DataFrame()

            # ── 2. Fetch 5-min bars ────────────────────────────────────────
            try:
                intraday_df = fetcher.get_ohlcv(
                    sym, interval="5minute", lookback_days=90
                )
            except Exception as exc:
                logger.warning(
                    f"[{format_ist_timestamp()}] Backtester: {sym} 5-min fetch failed: {exc}"
                )
                continue  # skip symbol entirely — fail-open

            if intraday_df is None or intraday_df.empty:
                logger.warning(
                    f"[{format_ist_timestamp()}] Backtester: {sym} — no 5-min data, skipping"
                )
                continue

            # Pre-compute regime array aligned to daily dates
            daily_closes = (
                daily_df["close"].values
                if not daily_df.empty
                else np.array([])
            )

            sym_trades = self._backtest_symbol(
                sym, intraday_df, daily_closes, config
            )
            all_trades.extend(sym_trades)
            logger.info(
                f"[{format_ist_timestamp()}] Backtester: {sym} → {len(sym_trades)} trades"
            )

        logger.info(
            f"[{format_ist_timestamp()}] Backtester: total {len(all_trades)} trades "
            f"across {len(config.symbols)} symbols"
        )

        result = self._aggregate(all_trades, config)
        self.save_results(result)
        return result

    # ── SYMBOL-LEVEL SIMULATION ───────────────────────────────────────────

    def _backtest_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        daily_closes: np.ndarray,
        config: BacktestConfig,
    ) -> List[Dict]:
        """
        Slide a 50-bar window across df. At each bar:
          - Score the window (inline indicators)
          - If score >= min_score: simulate entry + walk-forward exit
        Returns list of trade dicts.
        """
        trades: List[Dict] = []
        closes  = df["close"].values.astype(float)
        highs   = df["high"].values.astype(float)
        lows    = df["low"].values.astype(float)
        volumes = df["volume"].values.astype(float)
        n       = len(df)

        # Determine regime once from daily data
        regime = _detect_regime(daily_closes) if len(daily_closes) >= 20 else "RANGING"

        i = MIN_WINDOW_BARS
        while i < n - 1:
            window_slice = df.iloc[max(0, i - MIN_WINDOW_BARS): i + 1]

            score, direction, pattern_name = self._score_bar(window_slice, len(window_slice) - 1)

            if score < config.min_score or direction == "NEUTRAL":
                i += 1
                continue

            # ── Simulate the trade ────────────────────────────────────────
            entry  = float(closes[i])
            atr    = _atr(highs[: i + 1], lows[: i + 1], closes[: i + 1], 14)
            if atr <= 0:
                i += 1
                continue

            if direction == "LONG":
                sl = entry - atr * config.atr_sl_mult
                tp = entry + atr * config.atr_tp_mult
            else:  # SHORT
                sl = entry + atr * config.atr_sl_mult
                tp = entry - atr * config.atr_tp_mult

            # Walk forward to find exit
            exit_price, exit_idx, outcome_tag = self._find_exit(
                highs, lows, closes, i + 1, sl, tp, direction, n
            )

            if exit_price is None:
                # Could not find exit in remaining bars — skip
                i += 1
                continue

            # Compute R:R and P&L
            risk_dist = abs(entry - sl)
            reward_dist = abs(exit_price - entry)
            rr = reward_dist / risk_dist if risk_dist > 0 else 0.0

            win = outcome_tag == "TP"
            pnl_r = rr if win else -1.0  # in units of R

            # Hour of day from bar timestamp
            bar_ts = df.index[i]
            try:
                bar_et = bar_ts.astimezone(ET)
                hour = bar_et.hour
            except Exception:
                hour = -1

            trades.append({
                "symbol":    symbol,
                "direction": direction,
                "pattern":   pattern_name,
                "score":     score,
                "entry":     entry,
                "exit":      exit_price,
                "sl":        sl,
                "tp":        tp,
                "atr":       atr,
                "win":       win,
                "outcome":   outcome_tag,
                "rr":        rr,
                "pnl_r":     pnl_r,
                "regime":    regime,
                "hour":      hour,
            })

            # Jump past the exit bar to avoid overlapping trades
            i = exit_idx + 1

        return trades

    def _find_exit(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        start_idx: int,
        sl: float,
        tp: float,
        direction: str,
        n: int,
        max_bars: int = 78,  # ~6.5 hours of 5-min bars
    ) -> Tuple[Optional[float], int, str]:
        """
        Walk forward bar by bar.
        Returns (exit_price, exit_idx, tag) where tag is "TP", "SL", or "TIME".
        Returns (None, start_idx, "") if no valid exit found.
        """
        end = min(start_idx + max_bars, n)
        for j in range(start_idx, end):
            h = highs[j]
            l = lows[j]
            c = closes[j]

            if direction == "LONG":
                if l <= sl:
                    return sl, j, "SL"
                if h >= tp:
                    return tp, j, "TP"
            else:  # SHORT
                if h >= sl:
                    return sl, j, "SL"
                if l <= tp:
                    return tp, j, "TP"

        # Time exit — use last close
        last_idx = min(end - 1, n - 1)
        return float(closes[last_idx]), last_idx, "TIME"

    # ── SCORING (inline — no signal_generator import) ─────────────────────

    def _score_bar(
        self, df: pd.DataFrame, idx: int
    ) -> Tuple[float, str, str]:
        """
        Score a single bar using inline indicators.
        Returns (score 0-100, direction "LONG"/"SHORT"/"NEUTRAL", pattern_name).

        Scoring components (each adds to score):
          - EMA alignment       : 0–25 pts
          - RSI confirmation    : 0–20 pts
          - Volume surge        : 0–15 pts
          - VWAP position       : 0–15 pts
          - Candlestick pattern : 0–15 pts (engulfing, hammer, doji, ORB)
          - ATR filter          : 0–10 pts (minimum volatility check)
        """
        if len(df) < 20:
            return 0.0, "NEUTRAL", "UNKNOWN"

        closes  = df["close"].values.astype(float)
        highs   = df["high"].values.astype(float)
        lows    = df["low"].values.astype(float)
        volumes = df["volume"].values.astype(float)

        # ── Indicators ────────────────────────────────────────────────────
        ema9_arr  = _ema(closes, 9)
        ema21_arr = _ema(closes, 21)
        ema50_arr = _ema(closes, 50) if len(closes) >= 50 else ema21_arr

        ema9  = ema9_arr[-1]
        ema21 = ema21_arr[-1]
        ema50 = ema50_arr[-1]

        if any(np.isnan([ema9, ema21, ema50])):
            return 0.0, "NEUTRAL", "UNKNOWN"

        rsi        = _rsi(closes, 14)
        atr_val    = _atr(highs, lows, closes, 14)
        vol_ratio  = _volume_ratio(volumes, 20)
        vwap_val   = _vwap(df)
        close_now  = float(closes[-1])
        open_now   = float(df["open"].values[-1])
        high_now   = float(highs[-1])
        low_now    = float(lows[-1])

        score      = 0.0
        long_pts   = 0.0
        short_pts  = 0.0
        patterns   = []

        # ── 1. EMA Alignment (25 pts max) ────────────────────────────────
        if ema9 > ema21 > ema50:
            long_pts  += 25.0
            patterns.append("EMA_ALIGN_BULL")
        elif ema9 < ema21 < ema50:
            short_pts += 25.0
            patterns.append("EMA_ALIGN_BEAR")
        elif ema9 > ema21:
            long_pts  += 10.0
        elif ema9 < ema21:
            short_pts += 10.0

        # ── 2. RSI Confirmation (20 pts max) ────────────────────────────
        if 50 < rsi < 70:
            long_pts  += 20.0
            patterns.append("RSI_BULL")
        elif 30 < rsi < 50:
            short_pts += 20.0
            patterns.append("RSI_BEAR")
        elif rsi >= 70:
            # Overbought — slight short bias
            short_pts += 8.0
        elif rsi <= 30:
            # Oversold — slight long bias (reversal)
            long_pts  += 8.0

        # ── 3. Volume Surge (15 pts max) ────────────────────────────────
        if vol_ratio >= 2.0:
            long_pts  += 15.0
            short_pts += 15.0  # volume surge is direction-agnostic
            patterns.append("VOL_SURGE")
        elif vol_ratio >= 1.5:
            long_pts  += 8.0
            short_pts += 8.0

        # ── 4. VWAP Position (15 pts max) ────────────────────────────────
        if vwap_val > 0:
            vwap_pct = (close_now - vwap_val) / vwap_val * 100
            if vwap_pct > 0.1:
                long_pts  += 15.0
                patterns.append("ABOVE_VWAP")
            elif vwap_pct < -0.1:
                short_pts += 15.0
                patterns.append("BELOW_VWAP")
            elif abs(vwap_pct) <= 0.05:
                # At VWAP — follow trend
                if ema9 > ema21:
                    long_pts  += 5.0
                else:
                    short_pts += 5.0

        # ── 5. Candlestick Patterns (15 pts max) ─────────────────────────
        candle_pattern, candle_direction, candle_pts = self._detect_candle(
            df, idx
        )
        if candle_direction == "LONG":
            long_pts  += candle_pts
        elif candle_direction == "SHORT":
            short_pts += candle_pts
        if candle_pattern != "NONE":
            patterns.append(candle_pattern)

        # ── 6. ATR Filter (10 pts) ────────────────────────────────────────
        # Require minimum volatility (price must be > 0.1% of close)
        if close_now > 0 and atr_val / close_now >= 0.001:
            long_pts  += 10.0
            short_pts += 10.0

        # ── Determine direction and score ────────────────────────────────
        if long_pts > short_pts and long_pts >= 40:
            direction = "LONG"
            score = min(long_pts, 100.0)
        elif short_pts > long_pts and short_pts >= 40:
            direction = "SHORT"
            score = min(short_pts, 100.0)
        else:
            return 0.0, "NEUTRAL", "NEUTRAL"

        primary_pattern = candle_pattern if candle_pattern != "NONE" else (
            patterns[0] if patterns else "MOMENTUM"
        )

        return score, direction, primary_pattern

    def _detect_candle(
        self, df: pd.DataFrame, idx: int
    ) -> Tuple[str, str, float]:
        """
        Detect simple candlestick patterns at bar idx.
        Returns (pattern_name, direction, score_pts 0-15).
        """
        if len(df) < 3:
            return "NONE", "NEUTRAL", 0.0

        o  = float(df["open"].values[idx])
        h  = float(df["high"].values[idx])
        l  = float(df["low"].values[idx])
        c  = float(df["close"].values[idx])
        body = abs(c - o)
        rng  = h - l if h > l else 0.0001

        # Previous bar
        prev_idx = idx - 1
        po = float(df["open"].values[prev_idx])
        ph = float(df["high"].values[prev_idx])
        pl = float(df["low"].values[prev_idx])
        pc = float(df["close"].values[prev_idx])
        prev_body = abs(pc - po)

        # ── Engulfing ─────────────────────────────────────────────────────
        if (c > o and po > pc                          # bullish bar engulfs bearish
                and c >= po and o <= pc
                and body > prev_body * 0.8):
            return "BULLISH_ENGULFING", "LONG", 15.0

        if (c < o and pc > po                          # bearish bar engulfs bullish
                and c <= po and o >= pc
                and body > prev_body * 0.8):
            return "BEARISH_ENGULFING", "SHORT", 15.0

        # ── Hammer / Shooting Star ────────────────────────────────────────
        lower_wick = o - l if c > o else c - l
        upper_wick = h - c if c > o else h - o

        if rng > 0:
            if lower_wick >= 2 * body and upper_wick <= body * 0.3 and lower_wick / rng >= 0.6:
                return "HAMMER", "LONG", 12.0
            if upper_wick >= 2 * body and lower_wick <= body * 0.3 and upper_wick / rng >= 0.6:
                return "SHOOTING_STAR", "SHORT", 12.0

        # ── Doji ──────────────────────────────────────────────────────────
        if rng > 0 and body / rng < 0.1:
            # Doji — direction from prior trend
            if pc > po:
                return "DOJI_BEARISH", "SHORT", 6.0
            else:
                return "DOJI_BULLISH", "LONG", 6.0

        # ── Opening Range Breakout (ORB) ──────────────────────────────────
        # Detect if this bar breaks above/below the first-bar range
        try:
            bar_ts = df.index[idx]
            bar_et = bar_ts.astimezone(ET)
            if bar_et.hour == 9 and bar_et.minute >= 30:
                first_bar = df.iloc[0]
                orb_high = float(first_bar["high"])
                orb_low  = float(first_bar["low"])
                if c > orb_high and o >= orb_high * 0.999:
                    return "ORB_BULL", "LONG", 15.0
                if c < orb_low and o <= orb_low * 1.001:
                    return "ORB_BEAR", "SHORT", 15.0
        except Exception as _e:
            logger.debug(f"[suppressed] _detect_candle: {_e}")

        return "NONE", "NEUTRAL", 0.0

    # ── AGGREGATION ───────────────────────────────────────────────────────

    def _aggregate(
        self, trades: List[Dict], config: BacktestConfig
    ) -> BacktestResult:
        """Aggregate raw trade list into BacktestResult."""
        total   = len(trades)
        wins    = sum(1 for t in trades if t["win"])
        losses  = total - wins
        win_rate = (wins / total * 100) if total > 0 else 0.0

        gross_profit = sum(t["rr"] for t in trades if t["win"])
        gross_loss   = sum(abs(t["rr"]) for t in trades if not t["win"] and t["rr"] < 0)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        avg_rr = float(np.mean([t["rr"] for t in trades])) if trades else 0.0

        # ── Equity curve → Sharpe & Max Drawdown ─────────────────────────
        sharpe, max_dd = self._calc_sharpe_dd(trades, config)

        # ── Net return in % ───────────────────────────────────────────────
        net_return_r = sum(t["pnl_r"] for t in trades)
        # Convert R-based return to capital % (risk_pct per trade)
        net_return_pct = net_return_r * config.risk_pct

        # ── Per-pattern stats ─────────────────────────────────────────────
        pattern_stats: Dict[str, Dict] = {}
        for t in trades:
            pat = t["pattern"]
            if pat not in pattern_stats:
                pattern_stats[pat] = {"trades": 0, "wins": 0, "profit": 0.0, "loss": 0.0}
            pattern_stats[pat]["trades"] += 1
            if t["win"]:
                pattern_stats[pat]["wins"]   += 1
                pattern_stats[pat]["profit"] += t["rr"]
            else:
                pattern_stats[pat]["loss"] += abs(t["rr"])

        for pat, st in pattern_stats.items():
            st["win_rate"] = round(st["wins"] / max(st["trades"], 1) * 100, 1)
            st["profit_factor"] = round(
                st["profit"] / max(st["loss"], 0.001), 2
            )

        # ── Per-hour stats ─────────────────────────────────────────────────
        hourly_stats: Dict[int, Dict] = {}
        for t in trades:
            h = t.get("hour", -1)
            if h not in hourly_stats:
                hourly_stats[h] = {"trades": 0, "wins": 0}
            hourly_stats[h]["trades"] += 1
            if t["win"]:
                hourly_stats[h]["wins"] += 1
        for h, st in hourly_stats.items():
            st["win_rate"] = round(st["wins"] / max(st["trades"], 1) * 100, 1)

        # ── Per-regime stats ───────────────────────────────────────────────
        regime_stats: Dict[str, Dict] = {}
        for t in trades:
            reg = t.get("regime", "UNKNOWN")
            if reg not in regime_stats:
                regime_stats[reg] = {"trades": 0, "wins": 0}
            regime_stats[reg]["trades"] += 1
            if t["win"]:
                regime_stats[reg]["wins"] += 1
        for reg, st in regime_stats.items():
            st["win_rate"] = round(st["wins"] / max(st["trades"], 1) * 100, 1)

        # ── Best/Worst patterns (>= 10 trades) ────────────────────────────
        qualified = [
            (pat, st["win_rate"])
            for pat, st in pattern_stats.items()
            if st["trades"] >= 10
        ]
        qualified_sorted = sorted(qualified, key=lambda x: x[1], reverse=True)
        best_patterns  = [p for p, _ in qualified_sorted[:5]]
        worst_patterns = [p for p, _ in qualified_sorted[-5:]]

        # ── ET timestamp ──────────────────────────────────────────────────
        try:
            generated_at = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
        except Exception:
            generated_at = str(datetime.utcnow()) + " UTC"

        return BacktestResult(
            total_trades     = total,
            wins             = wins,
            losses           = losses,
            win_rate         = round(win_rate, 1),
            profit_factor    = round(profit_factor, 2),
            sharpe_ratio     = round(sharpe, 2),
            max_drawdown_pct = round(max_dd, 1),
            avg_rr           = round(avg_rr, 2),
            net_return_pct   = round(net_return_pct, 1),
            pattern_stats    = pattern_stats,
            hourly_stats     = hourly_stats,
            regime_stats     = regime_stats,
            best_patterns    = best_patterns,
            worst_patterns   = worst_patterns,
            generated_at     = generated_at,
        )

    def _calc_sharpe_dd(
        self, trades: List[Dict], config: BacktestConfig
    ) -> Tuple[float, float]:
        """
        Build equity curve from trades and compute:
          - Annualised Sharpe ratio (252 trading days × 78 bars/day)
          - Max drawdown %
        """
        if not trades:
            return 0.0, 0.0

        equity    = config.initial_capital
        peak      = equity
        max_dd    = 0.0
        returns: List[float] = []

        for t in trades:
            # Dollar P&L: risk_pct of current equity × R multiple
            trade_risk = equity * (config.risk_pct / 100.0)
            pnl        = trade_risk * t["pnl_r"]
            equity    += pnl
            returns.append(pnl / (equity - pnl) if (equity - pnl) > 0 else 0.0)

            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd

        if len(returns) < 2:
            return 0.0, max_dd

        arr    = np.array(returns)
        mean_r = float(np.mean(arr))
        std_r  = float(np.std(arr))

        if std_r == 0:
            return 0.0, max_dd

        # Annualise: assume ~252 × 78 5-min bars per year ≈ ~5000 trades/year
        # But we scale by sqrt(trades_per_year / sample_size)
        trades_per_year = 252 * 20  # ~20 trades/day estimate
        sharpe = (mean_r / std_r) * math.sqrt(trades_per_year)
        return float(sharpe), float(max_dd)

    # ── PERSISTENCE ────────────────────────────────────────────────────────

    def save_results(self, result: BacktestResult) -> None:
        """Save BacktestResult to data/backtest_results.json."""
        try:
            BACKTEST_RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "total_trades":     result.total_trades,
                "wins":             result.wins,
                "losses":           result.losses,
                "win_rate":         result.win_rate,
                "profit_factor":    result.profit_factor,
                "sharpe_ratio":     result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "avg_rr":           result.avg_rr,
                "net_return_pct":   result.net_return_pct,
                "pattern_stats":    result.pattern_stats,
                "hourly_stats":     {str(k): v for k, v in result.hourly_stats.items()},
                "regime_stats":     result.regime_stats,
                "best_patterns":    result.best_patterns,
                "worst_patterns":   result.worst_patterns,
                "generated_at":     result.generated_at,
            }
            BACKTEST_RESULTS_FILE.write_text(json.dumps(data, indent=2))
            logger.info(
                f"[{format_ist_timestamp()}] Backtester: results saved to {BACKTEST_RESULTS_FILE} | "
                f"WR={result.win_rate:.1f}% | PF={result.profit_factor:.2f} | "
                f"Sharpe={result.sharpe_ratio:.2f} | MaxDD={result.max_drawdown_pct:.1f}%"
            )
        except Exception as exc:
            logger.warning(
                f"[{format_ist_timestamp()}] Backtester: save_results failed: {exc}"
            )

    def load_results(self) -> Optional[BacktestResult]:
        """Load BacktestResult from data/backtest_results.json. Returns None if missing/corrupt."""
        try:
            if not BACKTEST_RESULTS_FILE.exists():
                logger.debug("Backtester: no saved results found")
                return None
            data = json.loads(BACKTEST_RESULTS_FILE.read_text())

            # Restore int keys for hourly_stats
            hourly_stats = {
                int(k): v for k, v in data.get("hourly_stats", {}).items()
            }

            return BacktestResult(
                total_trades     = data.get("total_trades", 0),
                wins             = data.get("wins", 0),
                losses           = data.get("losses", 0),
                win_rate         = data.get("win_rate", 0.0),
                profit_factor    = data.get("profit_factor", 0.0),
                sharpe_ratio     = data.get("sharpe_ratio", 0.0),
                max_drawdown_pct = data.get("max_drawdown_pct", 0.0),
                avg_rr           = data.get("avg_rr", 0.0),
                net_return_pct   = data.get("net_return_pct", 0.0),
                pattern_stats    = data.get("pattern_stats", {}),
                hourly_stats     = hourly_stats,
                regime_stats     = data.get("regime_stats", {}),
                best_patterns    = data.get("best_patterns", []),
                worst_patterns   = data.get("worst_patterns", []),
                generated_at     = data.get("generated_at", ""),
            )
        except Exception as exc:
            logger.warning(
                f"[{format_ist_timestamp()}] Backtester: load_results failed: {exc}"
            )
            return None


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_backtester: Optional[HistoricalBacktester] = None


def get_backtester() -> HistoricalBacktester:
    global _backtester
    if _backtester is None:
        _backtester = HistoricalBacktester()
    return _backtester


# ─────────────────────────────────────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Historical backtester — validates which patterns actually work"
    )
    parser.add_argument(
        "--symbols",
        default="NVDA,TSLA,AAPL,MSFT,META",
        help="Comma-separated list of symbols (default: NVDA,TSLA,AAPL,MSFT,META)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Lookback days for daily data (default: 365)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=90.0,
        help="Minimum signal score to simulate a trade (default: 90.0)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=50_000.0,
        help="Initial capital for simulation (default: 50000)",
    )
    args = parser.parse_args()

    config = BacktestConfig(
        symbols       = args.symbols.split(","),
        lookback_days = args.days,
        initial_capital = args.capital,
        min_score     = args.min_score,
    )

    bt     = HistoricalBacktester()
    result = bt.run(config)

    print(
        f"\n{'='*60}\n"
        f"Backtest Results  ({result.generated_at})\n"
        f"{'='*60}\n"
        f"Symbols     : {', '.join(config.symbols)}\n"
        f"Trades      : {result.total_trades} ({result.wins}W / {result.losses}L)\n"
        f"Win rate    : {result.win_rate:.1f}%\n"
        f"Profit factor: {result.profit_factor:.2f}\n"
        f"Sharpe ratio : {result.sharpe_ratio:.2f}\n"
        f"Max drawdown : {result.max_drawdown_pct:.1f}%\n"
        f"Avg R:R      : {result.avg_rr:.2f}\n"
        f"Net return   : {result.net_return_pct:.1f}%\n"
        f"Best patterns: {result.best_patterns}\n"
        f"Worst patterns: {result.worst_patterns}\n"
        f"{'='*60}"
    )
