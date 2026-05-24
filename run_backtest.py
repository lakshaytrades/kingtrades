#!/usr/bin/env python3
"""
run_backtest.py — KingTrades Strategy Backtester
Run this on your LOCAL machine (needs internet for yfinance).

Usage:
    python run_backtest.py                    # quick: 10 stocks, 60 days
    python run_backtest.py --full             # full: 30 stocks, 90 days
    python run_backtest.py --symbol NVDA      # single stock deep-dive
    python run_backtest.py --capital 1000     # test with $1,000 capital

Install deps first:
    pip install yfinance pandas numpy tabulate
"""

import argparse
import sys
from datetime import datetime, timedelta, date, time
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")

# ─────────────────────────────────────────────────────────────────────────────
# WATCHLIST — top momentum stocks (same as live bot)
# ─────────────────────────────────────────────────────────────────────────────

QUICK_SYMBOLS = [
    "NVDA", "TSLA", "AAPL", "AMD", "META",
    "COIN", "PLTR", "TQQQ", "SOXL", "MSTR",
]

FULL_SYMBOLS = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMD", "META", "AMZN", "GOOGL",
    "COIN", "PLTR", "MSTR", "CRWD", "PANW", "NET", "DDOG", "SNOW",
    "TQQQ", "SOXL", "SPXL", "QQQ", "SPY", "MARA", "RIOT", "SOFI",
    "HOOD", "UBER", "SHOP", "RBLX", "MU", "ARM",
]

MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(16, 0)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    symbols:         List[str]
    lookback_days:   int   = 90        # days of 5-min data
    initial_capital: float = 1_000.0
    risk_pct:        float = 1.0       # % of capital risked per trade
    atr_sl_mult:     float = 1.0       # SL = 1× ATR
    atr_t1_mult:     float = 1.5       # T1 = 1.5× ATR (40% exit)
    atr_t2_mult:     float = 2.5       # T2 = 2.5× ATR (25% exit)
    atr_runner_mult: float = 5.0       # Runner = 5× ATR (35% exit)
    min_score:       float = 72.0      # minimum signal score
    max_positions:   int   = 5         # max concurrent positions
    daily_loss_stop: float = 2.0       # % daily loss → stop trading
    slippage_pct:    float = 0.05      # 0.05% slippage per trade
    commission_pct:  float = 0.0       # Alpaca = $0 commission


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    result = np.full_like(series, np.nan, dtype=float)
    if len(series) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = np.mean(series[:period])
    for i in range(period, len(series)):
        result[i] = series[i] * k + result[i - 1] * (1 - k)
    return result


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    if len(highs) < period + 1:
        return float(np.mean(highs[-period:] - lows[-period:])) if len(highs) >= 1 else 0.0
    tr_vals = []
    for i in range(1, period + 1):
        idx = len(highs) - period - 1 + i
        tr_vals.append(max(
            highs[idx] - lows[idx],
            abs(highs[idx] - closes[idx - 1]),
            abs(lows[idx]  - closes[idx - 1]),
        ))
    return float(np.mean(tr_vals))


def _vwap(df: pd.DataFrame) -> float:
    tp  = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, 1)
    return float((tp * vol).sum() / vol.sum())


def _macd(closes: np.ndarray) -> Tuple[float, float]:
    """Returns (MACD line, Signal line). Positive MACD = bullish."""
    if len(closes) < 35:
        return 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    signal    = _ema(macd_line[~np.isnan(macd_line)], 9)
    return float(macd_line[-1]), float(signal[-1]) if len(signal) else 0.0


def _volume_ratio(volumes: np.ndarray, period: int = 20) -> float:
    if len(volumes) < period + 1:
        return 1.0
    sma_vol = float(np.mean(volumes[-(period + 1):-1]))
    return float(volumes[-1]) / sma_vol if sma_vol > 0 else 1.0


def _detect_regime(daily_closes: np.ndarray) -> str:
    if len(daily_closes) < 20:
        return "RANGING"
    ema20 = _ema(daily_closes, 20)
    ema50 = _ema(daily_closes, 50) if len(daily_closes) >= 50 else ema20
    last  = daily_closes[-1]
    e20   = ema20[-1] if not np.isnan(ema20[-1]) else last
    e50   = ema50[-1] if not np.isnan(ema50[-1]) else last
    returns = np.diff(daily_closes[-21:]) / daily_closes[-21:-1]
    vol_pct = float(np.std(returns) * 100) if len(returns) > 0 else 0.0
    if vol_pct > 3.0:
        return "VOLATILE"
    if last > e20 > e50:
        return "TRENDING_UP"
    if last < e20 < e50:
        return "TRENDING_DOWN"
    return "RANGING"


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL SCORING — mirrors the live bot's logic
# ─────────────────────────────────────────────────────────────────────────────

def _score_bar(df: pd.DataFrame) -> Tuple[float, str, str]:
    """
    Score a 50-bar window. Returns (score, direction, pattern_name).
    Mirrors signal_generator._compute_ai_score() key components.
    """
    if len(df) < 50:
        return 0.0, "NEUTRAL", "NONE"

    closes  = df["close"].values.astype(float)
    highs   = df["high"].values.astype(float)
    lows    = df["low"].values.astype(float)
    volumes = df["volume"].values.astype(float)

    ema9_arr  = _ema(closes, 9)
    ema21_arr = _ema(closes, 21)
    ema50_arr = _ema(closes, 50)

    ema9  = ema9_arr[-1]
    ema21 = ema21_arr[-1]
    ema50 = ema50_arr[-1]
    if any(np.isnan([ema9, ema21, ema50])):
        return 0.0, "NEUTRAL", "NONE"

    rsi       = _rsi(closes, 14)
    atr_val   = _atr(highs, lows, closes, 14)
    vol_ratio = _volume_ratio(volumes, 20)
    macd_l, macd_s = _macd(closes)
    vwap_val  = _vwap(df)

    close = float(closes[-1])
    open_ = float(df["open"].values[-1])
    high  = float(highs[-1])
    low   = float(lows[-1])

    # Minimum volatility guard (skip ultra-low-vol periods)
    if atr_val < close * 0.002:
        return 0.0, "NEUTRAL", "NONE"

    long_pts  = 0.0
    short_pts = 0.0
    best_pattern = "MOMENTUM"

    # 1. EMA stack alignment (25 pts)
    if ema9 > ema21 > ema50:
        long_pts  += 25.0
    elif ema9 < ema21 < ema50:
        short_pts += 25.0
    elif ema9 > ema21:
        long_pts  += 12.0
    elif ema9 < ema21:
        short_pts += 12.0

    # 2. RSI confirmation (20 pts)
    if 50 < rsi < 70:
        long_pts  += 20.0
        if rsi > 60:
            long_pts += 5.0
    elif 30 < rsi < 50:
        short_pts += 20.0
        if rsi < 40:
            short_pts += 5.0
    elif rsi >= 70:
        short_pts += 5.0  # overbought → slight short bias
    elif rsi <= 30:
        long_pts  += 5.0  # oversold → slight long bias

    # 3. Volume surge (15 pts)
    if vol_ratio >= 2.5:
        long_pts  += 15.0
        short_pts += 15.0  # surge confirms both directions; direction from price
        best_pattern = "VOLUME_BURST"
    elif vol_ratio >= 1.8:
        long_pts  += 10.0
        short_pts += 10.0
    elif vol_ratio >= 1.4:
        long_pts  += 5.0
        short_pts += 5.0

    # 4. VWAP position (15 pts)
    if close > vwap_val * 1.002:
        long_pts  += 15.0
    elif close < vwap_val * 0.998:
        short_pts += 15.0
    elif close > vwap_val:
        long_pts  += 7.0
    else:
        short_pts += 7.0

    # 5. MACD signal (10 pts)
    if macd_l > 0 and macd_l > macd_s:
        long_pts  += 10.0
    elif macd_l < 0 and macd_l < macd_s:
        short_pts += 10.0

    # 6. Candlestick patterns (15 pts)
    body = abs(close - open_)
    wick_up   = high - max(close, open_)
    wick_down = min(close, open_) - low

    # Bullish engulfing
    if len(df) >= 2:
        prev_close = float(closes[-2])
        prev_open  = float(df["open"].values[-2])
        if (close > open_ and prev_close < prev_open
                and close > prev_open and open_ < prev_close):
            long_pts += 15.0
            best_pattern = "Bullish Engulfing"
        # Bearish engulfing
        elif (close < open_ and prev_close > prev_open
              and close < prev_open and open_ > prev_close):
            short_pts += 15.0
            best_pattern = "Bearish Engulfing"
        # Hammer (bullish)
        elif (body > 0 and wick_down >= body * 2 and wick_up < body * 0.5
              and close > open_):
            long_pts += 12.0
            best_pattern = "Hammer"
        # Shooting star (bearish)
        elif (body > 0 and wick_up >= body * 2 and wick_down < body * 0.5
              and close < open_):
            short_pts += 12.0
            best_pattern = "Shooting Star"

    # Determine direction
    if long_pts >= 60.0 and long_pts > short_pts * 1.3:
        direction = "LONG"
        score = min(long_pts, 100.0)
    elif short_pts >= 60.0 and short_pts > long_pts * 1.3:
        direction = "SHORT"
        score = min(short_pts, 100.0)
    else:
        return 0.0, "NEUTRAL", "NONE"

    # Session time bonus: opening drive and afternoon get +5
    try:
        bar_time = df.index[-1].astimezone(ET).time()
        if time(9, 30) <= bar_time < time(10, 30):   # opening drive
            score = min(score + 5.0, 100.0)
        elif time(13, 30) <= bar_time < time(15, 30): # afternoon push
            score = min(score + 3.0, 100.0)
        elif time(11, 30) <= bar_time < time(13, 30): # midday chop penalty
            score = max(score - 8.0, 0.0)
    except Exception:
        pass

    return score, direction, best_pattern


# ─────────────────────────────────────────────────────────────────────────────
# EXIT SIMULATION — partial exits T1/T2/Runner
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_trade(
    highs: np.ndarray,
    lows:  np.ndarray,
    closes: np.ndarray,
    start_idx: int,
    entry: float,
    sl: float,
    t1: float,
    t2: float,
    runner: float,
    direction: str,
    n: int,
    slippage: float = 0.0005,
    max_bars: int = 78,  # 6.5 hours of 5-min bars
) -> Dict:
    """
    Simulate partial exits at T1 (40%), T2 (25%), Runner (35%).
    Returns dict with pnl_r, outcome, exit_bar.
    """
    end      = min(start_idx + max_bars, n)
    t1_done  = False
    t2_done  = False
    pnl_r    = 0.0
    sl_dist  = abs(entry - sl)
    outcome  = "TIME"

    # Notional sizes
    t1_size   = 0.40
    t2_size   = 0.25
    run_size  = 0.35

    # Apply entry slippage
    if direction == "LONG":
        actual_entry = entry * (1 + slippage)
    else:
        actual_entry = entry * (1 - slippage)

    trailing_sl = sl  # activates after T1

    for j in range(start_idx, end):
        h = float(highs[j])
        l = float(lows[j])
        c = float(closes[j])

        if direction == "LONG":
            # SL hit?
            if l <= trailing_sl:
                exit_price = trailing_sl * (1 - slippage)
                remaining  = t1_size * (not t1_done) + t2_size * (not t2_done) + run_size
                pnl_r -= remaining * abs(actual_entry - exit_price) / max(sl_dist, 0.001)
                outcome = "SL"
                return {"pnl_r": pnl_r, "outcome": outcome, "exit_bar": j, "win": False}
            # T1
            if not t1_done and h >= t1:
                exit_price = t1 * (1 - slippage)
                pnl_r += t1_size * abs(exit_price - actual_entry) / max(sl_dist, 0.001)
                trailing_sl = actual_entry  # breakeven stop
                t1_done = True
            # T2
            if t1_done and not t2_done and h >= t2:
                exit_price = t2 * (1 - slippage)
                pnl_r += t2_size * abs(exit_price - actual_entry) / max(sl_dist, 0.001)
                t2_done = True
            # Runner
            if t2_done and h >= runner:
                exit_price = runner * (1 - slippage)
                pnl_r += run_size * abs(exit_price - actual_entry) / max(sl_dist, 0.001)
                outcome = "TP"
                return {"pnl_r": pnl_r, "outcome": outcome, "exit_bar": j, "win": True}
        else:  # SHORT
            if h >= trailing_sl:
                exit_price = trailing_sl * (1 + slippage)
                remaining  = t1_size * (not t1_done) + t2_size * (not t2_done) + run_size
                pnl_r -= remaining * abs(exit_price - actual_entry) / max(sl_dist, 0.001)
                outcome = "SL"
                return {"pnl_r": pnl_r, "outcome": outcome, "exit_bar": j, "win": False}
            if not t1_done and l <= t1:
                exit_price = t1 * (1 + slippage)
                pnl_r += t1_size * abs(actual_entry - exit_price) / max(sl_dist, 0.001)
                trailing_sl = actual_entry
                t1_done = True
            if t1_done and not t2_done and l <= t2:
                exit_price = t2 * (1 + slippage)
                pnl_r += t2_size * abs(actual_entry - exit_price) / max(sl_dist, 0.001)
                t2_done = True
            if t2_done and l <= runner:
                exit_price = runner * (1 + slippage)
                pnl_r += run_size * abs(actual_entry - exit_price) / max(sl_dist, 0.001)
                outcome = "TP"
                return {"pnl_r": pnl_r, "outcome": outcome, "exit_bar": j, "win": True}

    # Time exit — close all at last bar's close
    last_close = float(closes[min(end - 1, n - 1)])
    if direction == "LONG":
        if not t1_done:
            pnl_r += (last_close - actual_entry) / max(sl_dist, 0.001)
        else:
            remaining = (t2_size if not t2_done else 0) + run_size
            pnl_r += remaining * (last_close - actual_entry) / max(sl_dist, 0.001)
    else:
        if not t1_done:
            pnl_r += (actual_entry - last_close) / max(sl_dist, 0.001)
        else:
            remaining = (t2_size if not t2_done else 0) + run_size
            pnl_r += remaining * (actual_entry - last_close) / max(sl_dist, 0.001)

    return {"pnl_r": pnl_r, "outcome": "TIME", "exit_bar": min(end - 1, n - 1), "win": pnl_r > 0}


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_data(symbol: str, days: int) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch daily bars + 5-min bars via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print("ERROR: yfinance not installed. Run: pip install yfinance")
        sys.exit(1)

    end_dt   = datetime.now(ET)
    start_5m = end_dt - timedelta(days=days + 3)
    start_1d = end_dt - timedelta(days=365)

    try:
        daily_df = yf.download(
            symbol, start=start_1d.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True, progress=False, timeout=15,
        )
    except Exception:
        daily_df = pd.DataFrame()

    try:
        intra_df = yf.download(
            symbol, start=start_5m.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            interval="5m", auto_adjust=True, progress=False, timeout=30,
        )
    except Exception:
        intra_df = pd.DataFrame()

    # Normalize column names
    for df in (daily_df, intra_df):
        if not df.empty:
            df.columns = [c.lower() if isinstance(c, str) else c[0].lower()
                          for c in df.columns]
            if "adj close" in df.columns:
                df.rename(columns={"adj close": "close"}, inplace=True)

    # Filter 5-min to market hours only
    if not intra_df.empty and isinstance(intra_df.index, pd.DatetimeIndex):
        if intra_df.index.tz is None:
            intra_df.index = intra_df.index.tz_localize("America/New_York")
        else:
            intra_df.index = intra_df.index.tz_convert("America/New_York")
        mask = (
            (intra_df.index.time >= MARKET_OPEN) &
            (intra_df.index.time <  MARKET_CLOSE) &
            (intra_df.index.weekday < 5)
        )
        intra_df = intra_df[mask]

    return daily_df, intra_df


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST ENGINE
# ─────────────────────────────────────────────────────────────────────────────

MIN_WINDOW = 50  # bars of 5-min data needed before scoring


def backtest_symbol(
    symbol: str,
    intra_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    cfg: BacktestConfig,
) -> List[Dict]:
    """Walk-forward simulation of one symbol."""
    if intra_df is None or len(intra_df) < MIN_WINDOW + 5:
        return []

    closes  = intra_df["close"].values.astype(float)
    highs   = intra_df["high"].values.astype(float)
    lows    = intra_df["low"].values.astype(float)
    volumes = intra_df["volume"].values.astype(float)
    n       = len(intra_df)

    daily_closes = daily_df["close"].values.astype(float) if not daily_df.empty else np.array([])
    regime = _detect_regime(daily_closes) if len(daily_closes) >= 20 else "RANGING"

    trades: List[Dict] = []
    i = MIN_WINDOW

    while i < n - 1:
        window = intra_df.iloc[i - MIN_WINDOW: i + 1]
        score, direction, pattern = _score_bar(window)

        if score < cfg.min_score or direction == "NEUTRAL":
            i += 1
            continue

        entry   = float(closes[i])
        atr_val = _atr(highs[:i+1], lows[:i+1], closes[:i+1], 14)
        if atr_val <= 0 or entry <= 0:
            i += 1
            continue

        # Price levels
        if direction == "LONG":
            sl     = entry - atr_val * cfg.atr_sl_mult
            t1     = entry + atr_val * cfg.atr_t1_mult
            t2     = entry + atr_val * cfg.atr_t2_mult
            runner = entry + atr_val * cfg.atr_runner_mult
        else:
            sl     = entry + atr_val * cfg.atr_sl_mult
            t1     = entry - atr_val * cfg.atr_t1_mult
            t2     = entry - atr_val * cfg.atr_t2_mult
            runner = entry - atr_val * cfg.atr_runner_mult

        result = _simulate_trade(
            highs, lows, closes, i + 1,
            entry, sl, t1, t2, runner, direction, n,
            slippage=cfg.slippage_pct / 100,
        )

        bar_ts = intra_df.index[i]
        try:
            et_time = bar_ts.astimezone(ET)
            hour    = et_time.hour
            trade_date = et_time.date()
        except Exception:
            hour = -1
            trade_date = None

        trades.append({
            "symbol":     symbol,
            "direction":  direction,
            "pattern":    pattern,
            "score":      round(score, 1),
            "entry":      entry,
            "sl":         sl,
            "t1":         t1,
            "t2":         t2,
            "runner":     runner,
            "atr":        atr_val,
            "rr_t2":      abs(t2 - entry) / max(abs(sl - entry), 0.001),
            "pnl_r":      round(result["pnl_r"], 3),
            "win":        result["win"],
            "outcome":    result["outcome"],
            "regime":     regime,
            "hour":       hour,
            "date":       trade_date,
        })

        # Skip to after exit bar (no overlapping trades on same symbol)
        i = result["exit_bar"] + 1

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO-LEVEL SIMULATION (apply capital, risk, daily limits)
# ─────────────────────────────────────────────────────────────────────────────

def simulate_portfolio(all_trades: List[Dict], cfg: BacktestConfig) -> Dict:
    """
    Convert raw trade results into a realistic portfolio equity curve.
    Applies: risk sizing, daily loss limit, max positions per day.
    """
    if not all_trades:
        return {"equity_curve": [], "daily_pnl": {}, "trades": []}

    # Sort by date then hour
    trades_sorted = sorted(
        [t for t in all_trades if t["date"] is not None],
        key=lambda t: (t["date"], t["hour"])
    )

    capital = cfg.initial_capital
    equity_curve = [capital]
    daily_pnl: Dict[date, float] = {}
    portfolio_trades = []

    by_date: Dict[date, List[Dict]] = {}
    for t in trades_sorted:
        by_date.setdefault(t["date"], []).append(t)

    for trade_date in sorted(by_date.keys()):
        day_trades = by_date[trade_date]
        day_pnl    = 0.0
        day_loss_stop = capital * (cfg.daily_loss_stop / 100)
        positions_today = 0

        for t in day_trades:
            # Daily loss stop check
            if day_pnl < -day_loss_stop:
                break
            # Max positions check (simplified: 1 trade per symbol per signal)
            if positions_today >= cfg.max_positions:
                break

            # Risk amount in $
            risk_usd = capital * (cfg.risk_pct / 100)
            # P&L in $ (pnl_r is in units of R)
            pnl_usd  = t["pnl_r"] * risk_usd

            day_pnl     += pnl_usd
            capital     += pnl_usd
            capital      = max(capital, 1.0)  # can't go negative
            positions_today += 1

            portfolio_trades.append({**t, "pnl_usd": round(pnl_usd, 2), "capital": round(capital, 2)})
            equity_curve.append(capital)

        daily_pnl[trade_date] = round(day_pnl, 2)

    return {
        "equity_curve":    equity_curve,
        "daily_pnl":       daily_pnl,
        "trades":          portfolio_trades,
        "final_capital":   capital,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RESULTS ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyze(cfg: BacktestConfig, sim: Dict) -> None:
    trades = sim["trades"]
    if not trades:
        print("\n❌ No trades generated. Likely not enough data or min_score too high.\n")
        return

    pnl_r_list = [t["pnl_r"] for t in trades]
    wins   = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]

    win_rate    = len(wins) / len(trades) * 100
    avg_win_r   = float(np.mean([t["pnl_r"] for t in wins])) if wins else 0
    avg_loss_r  = float(np.mean([abs(t["pnl_r"]) for t in losses])) if losses else 0
    profit_factor = (sum(t["pnl_r"] for t in wins) /
                     max(sum(abs(t["pnl_r"]) for t in losses), 0.001))

    eq   = np.array(sim["equity_curve"])
    roll_max = np.maximum.accumulate(eq)
    dd   = (eq - roll_max) / roll_max * 100
    max_dd = float(np.min(dd))

    # Sharpe (daily returns)
    daily_pnl_list = list(sim["daily_pnl"].values())
    daily_ret = [p / cfg.initial_capital for p in daily_pnl_list]
    sharpe = (float(np.mean(daily_ret)) / max(float(np.std(daily_ret)), 0.001)
              * np.sqrt(252)) if len(daily_ret) > 1 else 0.0

    total_return_pct = (sim["final_capital"] - cfg.initial_capital) / cfg.initial_capital * 100
    trading_days     = len(sim["daily_pnl"])
    avg_daily_pct    = total_return_pct / max(trading_days, 1)

    # By outcome
    outcomes = {}
    for t in trades:
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1

    # By pattern
    pattern_stats: Dict[str, Dict] = {}
    for t in trades:
        p = t["pattern"]
        if p not in pattern_stats:
            pattern_stats[p] = {"trades": 0, "wins": 0, "pnl_r": 0.0}
        pattern_stats[p]["trades"] += 1
        pattern_stats[p]["wins"]   += int(t["win"])
        pattern_stats[p]["pnl_r"]  += t["pnl_r"]

    # By hour
    hour_stats: Dict[int, Dict] = {}
    for t in trades:
        h = t["hour"]
        if h not in hour_stats:
            hour_stats[h] = {"trades": 0, "wins": 0}
        hour_stats[h]["trades"] += 1
        hour_stats[h]["wins"]   += int(t["win"])

    # By direction
    longs  = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]

    print("\n" + "=" * 65)
    print("  KINGTRADES BACKTEST RESULTS")
    print("=" * 65)
    print(f"  Symbols      : {len(cfg.symbols)} stocks | "
          f"{cfg.lookback_days}d lookback")
    print(f"  Capital      : ${cfg.initial_capital:,.0f} → "
          f"${sim['final_capital']:,.0f}")
    print(f"  Period       : {trading_days} trading days")
    print()
    print("── PERFORMANCE ────────────────────────────────────────────")
    print(f"  Total Return  : {total_return_pct:+.1f}%")
    print(f"  Avg Daily     : {avg_daily_pct:+.2f}%   "
          f"({'✅ above 4% target' if avg_daily_pct >= 4 else '⚠️  below 4% target — see notes'})")
    print(f"  Sharpe Ratio  : {sharpe:.2f}   "
          f"({'✅ excellent' if sharpe >= 1.5 else '⚠️  below 1.5 target'})")
    print(f"  Max Drawdown  : {max_dd:.1f}%   "
          f"({'✅ within 8% limit' if abs(max_dd) <= 8 else '⚠️  exceeds 8% limit'})")
    print()
    print("── TRADE STATISTICS ────────────────────────────────────────")
    print(f"  Total Trades  : {len(trades)}")
    print(f"  Win Rate      : {win_rate:.1f}%   "
          f"({'✅ above 55% target' if win_rate >= 55 else '⚠️  below 55% target'})")
    print(f"  Profit Factor : {profit_factor:.2f}   "
          f"({'✅ strong' if profit_factor >= 1.5 else '⚠️  improve signal quality'})")
    print(f"  Avg Win       : +{avg_win_r:.2f}R  "
          f"| Avg Loss: -{avg_loss_r:.2f}R")
    print(f"  Avg R:R (T2)  : {float(np.mean([t['rr_t2'] for t in trades])):.2f}:1")
    print()
    print(f"  Outcomes:")
    for tag, cnt in sorted(outcomes.items(), key=lambda x: -x[1]):
        pct = cnt / len(trades) * 100
        print(f"    {tag:6s}: {cnt:4d}  ({pct:.0f}%)")
    print()
    print(f"  By Direction:")
    print(f"    LONG  : {len(longs):4d} trades | WR {sum(1 for t in longs if t['win'])/max(len(longs),1)*100:.0f}%")
    print(f"    SHORT : {len(shorts):4d} trades | WR {sum(1 for t in shorts if t['win'])/max(len(shorts),1)*100:.0f}%")
    print()
    print("── DAILY P&L DISTRIBUTION ──────────────────────────────────")
    dpnl = list(sim["daily_pnl"].values())
    pos_days = [d for d in dpnl if d > 0]
    neg_days = [d for d in dpnl if d < 0]
    zero_days = [d for d in dpnl if d == 0]
    print(f"  Positive days : {len(pos_days):3d}  "
          f"avg +${float(np.mean(pos_days)):.2f}" if pos_days else "  Positive days : 0")
    print(f"  Negative days : {len(neg_days):3d}  "
          f"avg -${abs(float(np.mean(neg_days))):.2f}" if neg_days else "  Negative days : 0")
    print(f"  Zero/flat days: {len(zero_days):3d}")
    if dpnl:
        target_pct = 4.0
        target_usd = cfg.initial_capital * target_pct / 100
        days_at_target = sum(1 for d in dpnl if d >= target_usd)
        print(f"  Days ≥4% target: {days_at_target}/{trading_days}  "
              f"({days_at_target/max(trading_days,1)*100:.0f}% of trading days)")
    print()
    print("── TOP PATTERNS ────────────────────────────────────────────")
    sorted_pats = sorted(
        [(p, s) for p, s in pattern_stats.items() if s["trades"] >= 3],
        key=lambda x: x[1]["wins"] / max(x[1]["trades"], 1), reverse=True
    )
    for pat, st in sorted_pats[:6]:
        wr = st["wins"] / max(st["trades"], 1) * 100
        pf = st["pnl_r"] / max(st["trades"] - st["wins"], 0.001)
        print(f"  {pat:25s} WR {wr:.0f}%  "
              f"({st['trades']:3d} trades, {st['pnl_r']:+.1f}R total)")
    print()
    print("── BEST HOURS (ET) ─────────────────────────────────────────")
    for h in sorted(hour_stats.keys()):
        s = hour_stats[h]
        if s["trades"] < 3:
            continue
        wr = s["wins"] / s["trades"] * 100
        bar = "█" * int(wr / 10)
        print(f"  {h:02d}:00  {bar:<10s} WR {wr:.0f}%  ({s['trades']} trades)")
    print()
    print("── REGIME BREAKDOWN ────────────────────────────────────────")
    regime_stats: Dict[str, Dict] = {}
    for t in trades:
        r = t["regime"]
        if r not in regime_stats:
            regime_stats[r] = {"trades": 0, "wins": 0}
        regime_stats[r]["trades"] += 1
        regime_stats[r]["wins"]   += int(t["win"])
    for rg, s in sorted(regime_stats.items()):
        wr = s["wins"] / max(s["trades"], 1) * 100
        print(f"  {rg:16s}: {s['trades']:4d} trades | WR {wr:.0f}%")
    print()
    print("── REALISTIC DAILY EXPECTATIONS (on $1,000 account) ────────")
    risk_usd = 1000 * cfg.risk_pct / 100
    print(f"  Risk per trade : ${risk_usd:.2f} ({cfg.risk_pct:.1f}% of $1,000)")
    if pos_days:
        avg_pos = float(np.mean(pos_days))
        avg_pos_scaled = avg_pos * (1000 / cfg.initial_capital)
        print(f"  Avg winning day: +${avg_pos_scaled:.2f}")
    if neg_days:
        avg_neg = abs(float(np.mean(neg_days)))
        avg_neg_scaled = avg_neg * (1000 / cfg.initial_capital)
        print(f"  Avg losing day : -${avg_neg_scaled:.2f}")
    print(f"  Target (4%/day): ${1000 * 0.04:.0f}")
    print(f"  Days hitting 4%: {days_at_target}/{trading_days} "
          f"({days_at_target/max(trading_days,1)*100:.0f}% of days)")
    print("=" * 65)
    print()

    # Honest assessment
    print("── HONEST ASSESSMENT ───────────────────────────────────────")
    if win_rate >= 55 and profit_factor >= 1.5 and sharpe >= 1.2:
        print("  ✅ Strategy is PROFITABLE with consistent edge.")
        print(f"  Expected monthly return: ~{avg_daily_pct * 22:.1f}%")
        if avg_daily_pct >= 4.0:
            print("  ✅ 4%/day target appears achievable ON BEST DAYS.")
            print("  ⚠️  Compound average is the real metric — focus on that.")
        else:
            print(f"  📊 Realistic avg: {avg_daily_pct:.1f}%/day ({avg_daily_pct*22:.0f}%/month)")
            print("  ⚠️  4%/EVERY day is ambitious — see notes below.")
    elif win_rate >= 48:
        print("  ⚠️  Strategy shows edge but needs more conditions.")
        print("  Focus on TRENDING_UP regime only for better results.")
    else:
        print("  ⚠️  Strategy needs improvement. Win rate below 48%.")
        print("  Consider raising min_score threshold or adding regime filter.")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="KingTrades Backtest Runner")
    parser.add_argument("--full",    action="store_true", help="Full 30-stock backtest")
    parser.add_argument("--symbol",  type=str,   default="", help="Single symbol (e.g. NVDA)")
    parser.add_argument("--capital", type=float, default=1000.0, help="Starting capital in $")
    parser.add_argument("--days",    type=int,   default=60, help="Days of 5-min history (max ~60 on yfinance)")
    parser.add_argument("--risk",    type=float, default=1.0, help="Risk per trade %")
    parser.add_argument("--score",   type=float, default=72.0, help="Min signal score (72-85)")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.full:
        symbols = FULL_SYMBOLS
    else:
        symbols = QUICK_SYMBOLS

    cfg = BacktestConfig(
        symbols         = symbols,
        lookback_days   = min(args.days, 58),  # yfinance free max ~59d for 5m
        initial_capital = args.capital,
        risk_pct        = args.risk,
        min_score       = args.score,
    )

    print(f"\n🔍 KingTrades Backtest — {len(symbols)} symbols × {cfg.lookback_days}d")
    print(f"   Capital ${cfg.initial_capital:,.0f} | Risk {cfg.risk_pct}%/trade | Min score {cfg.min_score}")
    print(f"   Fetching data (needs internet)...\n")

    all_trades: List[Dict] = []

    for sym in symbols:
        print(f"   ↳ {sym} ...", end="", flush=True)
        try:
            daily_df, intra_df = fetch_data(sym, cfg.lookback_days)
            trades = backtest_symbol(sym, intra_df, daily_df, cfg)
            all_trades.extend(trades)
            print(f" {len(trades)} trades")
        except Exception as e:
            print(f" ERROR: {e}")

    print(f"\n   Total trades found: {len(all_trades)}")

    sim = simulate_portfolio(all_trades, cfg)
    analyze(cfg, sim)

    # Save to CSV
    if sim["trades"]:
        out_file = "backtest_results.csv"
        pd.DataFrame(sim["trades"]).to_csv(out_file, index=False)
        print(f"📁 Full trade log saved to: {out_file}\n")


if __name__ == "__main__":
    main()
