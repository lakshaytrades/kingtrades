"""
backtest_engine_india.py — Self-Contained NSE Historical Backtest Engine

Does NOT depend on the live signal generator or any external API beyond
Upstox historical OHLCV data. Implements a full institutional-grade signal
stack using only price/volume, then runs a tick-accurate replay.

Signal stack (OHLCV-only, verifiable):
  Tier 1 — Momentum:   RSI(14) regime, MACD(12,26,9) cross, EMA(9,21,50) alignment
  Tier 2 — Structure:  ORB breakout (9:15–9:30 range), VWAP deviation, ATR volatility
  Tier 3 — Volume:     Volume surge (>2x SMA20), OBV momentum, RVOL
  Tier 4 — Regime:     ADX(14) trend filter, Bollinger squeeze, multi-timeframe sync
  Tier 5 — Risk:       Half-Kelly sizing, 1.5×ATR SL, 3×ATR TP, 50% partial at 1R

Targets: Sharpe ≥ 2.0, Monthly return 10–20%, Max DD < 8%

Usage:
    # Requires UPSTOX_ACCESS_TOKEN in .env
    python3 india/backtest_engine_india.py --days 60 --capital 500000

    # With specific symbols
    python3 india/backtest_engine_india.py --symbols RELIANCE,INFY,TCS --days 90
"""
import argparse
import logging
import sys
import time as _time
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

import numpy as np
import pandas as pd

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_engine")

COST_RT_PCT  = 0.0029    # 29 bps round-trip (brokerage + STT + exchange + slippage)
SQUAREOFF    = dtime(15, 20)
MARKET_OPEN  = dtime(9, 15)
ORB_END      = dtime(9, 30)

# ── Pure OHLCV indicators ────────────────────────────────────────────────────

def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series) -> Tuple[pd.Series, pd.Series]:
    fast = _ema(close, 12); slow = _ema(close, 26)
    line = fast - slow; signal = _ema(line, 9)
    return line, signal


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average Directional Index — trend strength 0-100."""
    up   = high.diff().clip(lower=0)
    down = (-low.diff()).clip(lower=0)
    tr   = _atr(high, low, close, 1)   # 1-period TR
    smooth = tr.ewm(alpha=1/n, adjust=False).mean().replace(0, 1e-9)
    pdi = 100 * up.ewm(alpha=1/n, adjust=False).mean()   / smooth
    ndi = 100 * down.ewm(alpha=1/n, adjust=False).mean() / smooth
    dx  = (100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9))
    return dx.ewm(alpha=1/n, adjust=False).mean()


def _vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP reset daily."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    date = df.index.normalize()
    cum_tv = (tp * df["volume"]).groupby(date).cumsum()
    cum_v  = df["volume"].groupby(date).cumsum().replace(0, 1e-9)
    return cum_tv / cum_v


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def _bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)
    return mid - k*std, mid, mid + k*std


def _compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Compute full indicator stack on a 5-min OHLCV DataFrame."""
    out = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    out["ema9"]  = _ema(c, 9)
    out["ema21"] = _ema(c, 21)
    out["ema50"] = _ema(c, 50)
    out["rsi"]   = _rsi(c)
    out["macd"], out["macd_sig"] = _macd(c)
    out["macd_hist"] = out["macd"] - out["macd_sig"]
    out["atr"]   = _atr(h, l, c)
    out["adx"]   = _adx(h, l, c)
    out["vwap"]  = _vwap(df)
    out["obv"]   = _obv(c, v)
    out["obv_ema"] = _ema(out["obv"], 21)
    out["vol_sma"] = v.rolling(20).mean()
    out["rvol"]    = v / out["vol_sma"].replace(0, 1e-9)
    out["bb_lo"], out["bb_mid"], out["bb_hi"] = _bollinger(c)
    out["bb_width"] = (out["bb_hi"] - out["bb_lo"]) / out["bb_mid"].replace(0, 1e-9)
    return out


# ── Opening Range Breakout ────────────────────────────────────────────────────

def _build_orb(df: pd.DataFrame) -> pd.DataFrame:
    """Add orb_high / orb_low columns — ORB from 9:15 to 9:30 each day."""
    df = df.copy()
    df["date"] = df.index.normalize()
    orb_h = {}; orb_l = {}
    for d, grp in df.groupby("date"):
        orb = grp.between_time("09:15", "09:30")
        if not orb.empty:
            orb_h[d] = orb["high"].max()
            orb_l[d] = orb["low"].min()
    df["orb_high"] = df["date"].map(orb_h)
    df["orb_low"]  = df["date"].map(orb_l)
    df.drop(columns=["date"], inplace=True)
    return df


# ── Signal scoring ────────────────────────────────────────────────────────────

def _score_bar(row: pd.Series, prev: pd.Series, df_5m: pd.DataFrame,
               df_15m: Optional[pd.DataFrame], df_1h: Optional[pd.DataFrame],
               idx: int) -> Tuple[float, str]:
    """
    Score a single 5-min bar for LONG direction (0-100 scale, mirrored for SHORT).
    Returns (long_score - short_score, direction_str).
    A positive net score > threshold → LONG; negative → SHORT.
    """
    score_long = 0.0; score_short = 0.0; reasons = []

    # ── Tier 1: RSI momentum (max 15 pts) ──────────────────────────────────
    rsi = row.get("rsi", 50)
    if 40 < rsi < 65:         score_long  += 8;  reasons.append(f"RSI_BULL({rsi:.0f})")
    elif rsi < 35:             score_long  += 5;  reasons.append(f"RSI_OS({rsi:.0f})")
    if 35 < rsi < 60:         score_short += 8
    elif rsi > 65:             score_short += 5

    # ── Tier 1: MACD cross (max 12 pts) ────────────────────────────────────
    mh = row.get("macd_hist", 0); pmh = prev.get("macd_hist", 0)
    ml = row.get("macd", 0);  ms  = row.get("macd_sig", 0)
    if mh > 0 and pmh <= 0:  score_long  += 12; reasons.append("MACD_X_UP")
    elif mh > 0:              score_long  += 6
    if mh < 0 and pmh >= 0:  score_short += 12; reasons.append("MACD_X_DN")
    elif mh < 0:              score_short += 6

    # ── Tier 1: EMA alignment (max 12 pts) ─────────────────────────────────
    c = row["close"]; e9 = row.get("ema9",c); e21 = row.get("ema21",c); e50 = row.get("ema50",c)
    if c > e9 > e21 > e50:   score_long  += 12; reasons.append("EMA_BULL_STACK")
    elif c > e9 > e21:        score_long  += 7
    if c < e9 < e21 < e50:   score_short += 12; reasons.append("EMA_BEAR_STACK")
    elif c < e9 < e21:        score_short += 7

    # ── Tier 2: VWAP deviation (max 10 pts) ────────────────────────────────
    vwap = row.get("vwap", c)
    vwap_dev = (c - vwap) / max(vwap, 1e-9)
    if 0.001 < vwap_dev < 0.025:   score_long  += 10; reasons.append("ABOVE_VWAP")
    elif vwap_dev < -0.001:         score_long  += 4   # cheap vs VWAP
    if -0.025 < vwap_dev < -0.001: score_short += 10; reasons.append("BELOW_VWAP")
    elif vwap_dev > 0.001:          score_short += 4

    # ── Tier 2: ORB breakout (max 15 pts) ──────────────────────────────────
    if row.get("orb_high") and row.index.time > ORB_END:
        orb_h = row["orb_high"]; orb_l = row["orb_low"]
        if c > orb_h * 1.001:  score_long  += 15; reasons.append("ORB_BREAK_UP")
        if c < orb_l * 0.999:  score_short += 15; reasons.append("ORB_BREAK_DN")

    # ── Tier 3: Volume surge (max 12 pts) ──────────────────────────────────
    rvol = row.get("rvol", 1.0)
    if rvol > 2.5:   score_long += 12; score_short += 12; reasons.append(f"RVOL_{rvol:.1f}x")
    elif rvol > 1.5: score_long += 7;  score_short += 7

    # ── Tier 3: OBV trend (max 8 pts) ──────────────────────────────────────
    obv = row.get("obv", 0); obv_ema = row.get("obv_ema", 0)
    if obv > obv_ema:  score_long  += 8
    else:              score_short += 8

    # ── Tier 4: ADX trend filter (gate — halve score if choppy) ────────────
    adx = row.get("adx", 20)
    if adx < 20:   # choppy — halve both scores (low trend conviction)
        score_long  *= 0.5; score_short *= 0.5

    # ── Tier 4: Bollinger squeeze expansion (max 8 pts) ────────────────────
    bw = row.get("bb_width", 0.02)
    prev_bw = prev.get("bb_width", 0.02)
    if bw > prev_bw * 1.3 and bw > 0.02:
        score_long += 6; score_short += 6; reasons.append("BB_EXPAND")

    # ── Tier 4: 15-min alignment (max 10 pts) ──────────────────────────────
    if df_15m is not None:
        try:
            ts_15 = df_15m.index[df_15m.index <= row.name]
            if len(ts_15) >= 2:
                r15 = df_15m.loc[ts_15[-1]]
                if r15["ema9"] > r15["ema21"]:  score_long  += 10; reasons.append("15M_BULL")
                elif r15["ema9"] < r15["ema21"]: score_short += 10; reasons.append("15M_BEAR")
        except Exception:
            pass

    # ── Tier 4: 1-hour macro trend (max 8 pts) ─────────────────────────────
    if df_1h is not None:
        try:
            ts_1h = df_1h.index[df_1h.index <= row.name]
            if len(ts_1h) >= 2:
                r1h = df_1h.loc[ts_1h[-1]]
                c1h = r1h["close"]; e50_1h = r1h.get("ema50", c1h)
                if c1h > e50_1h:   score_long  += 8; reasons.append("1H_MACRO_BULL")
                elif c1h < e50_1h: score_short += 8; reasons.append("1H_MACRO_BEAR")
        except Exception:
            pass

    net = score_long - score_short
    direction = "LONG" if net > 0 else "SHORT"
    return net, direction, " | ".join(reasons)


# ── Regime detection (OHLCV-only, for backtest use) ─────────────────────────

# Regime names — mirrors RegimeSwitcher in signal_generator_india.py
REGIME_BULL_TREND    = "BULL_TREND"
REGIME_BEAR_TREND    = "BEAR_TREND"
REGIME_CHOPPY        = "CHOPPY"
REGIME_HIGH_VOL_FEAR = "HIGH_VOL_FEAR"

# Per-regime score multipliers {regime: {direction: multiplier}}
_REGIME_MULTS: Dict[str, Dict[str, float]] = {
    REGIME_BULL_TREND:    {"LONG": 1.3, "SHORT": 0.7},
    REGIME_BEAR_TREND:    {"SHORT": 1.3, "LONG": 0.7},
    REGIME_CHOPPY:        {"LONG": 0.5, "SHORT": 0.5},
    REGIME_HIGH_VOL_FEAR: {"LONG": 0.3, "SHORT": 0.3},
}


def _detect_regime(nifty_df: pd.DataFrame) -> Tuple[str, Dict[str, float]]:
    """
    Detect market regime from Nifty50 OHLCV data.  OHLCV-only — no live APIs.
    Used by backtest_engine_india to apply regime-conditional score multipliers.

    Logic (mirrors RegimeSwitcher in signal_generator_india.py):
      1. HIGH_VOL_FEAR if implied-vol proxy (ATR/price ratio) > threshold
         (In backtest we can't fetch India VIX, so we use ATR% as a proxy:
          ATR(14) / close > 2% ≈ VIX > 22 territory for NSE stocks)
      2. CHOPPY  if ADX(14) < 20  (no directional conviction)
      3. BULL_TREND if EMA5 > EMA20 slope positive AND breadth proxy (advancing bars) > 60%
      4. BEAR_TREND if EMA5 < EMA20 slope negative AND breadth proxy < 40%
      5. CHOPPY  otherwise (mixed signals)

    Args:
        nifty_df: DataFrame with OHLCV columns indexed by datetime (5-min bars).
                  Minimum 30 bars required; fewer → returns (CHOPPY, neutral multipliers).

    Returns:
        (regime_name, {direction: multiplier}) — fail-safe defaults to CHOPPY + 0.5×.
    """
    _safe_default = (REGIME_CHOPPY, _REGIME_MULTS[REGIME_CHOPPY])

    try:
        if nifty_df is None or nifty_df.empty or len(nifty_df) < 30:
            return _safe_default

        c = nifty_df["close"].astype(float)
        h = nifty_df["high"].astype(float)
        lo = nifty_df["low"].astype(float)

        # ── High-vol fear proxy: ATR(14) / close > 2% ───────────────────────
        atr14 = _atr(h, lo, c, 14).iloc[-1]
        close_last = float(c.iloc[-1])
        atr_pct = atr14 / (close_last + 1e-9)
        if atr_pct > 0.02:
            return REGIME_HIGH_VOL_FEAR, _REGIME_MULTS[REGIME_HIGH_VOL_FEAR]

        # ── ADX(14) — choppiness gate ────────────────────────────────────────
        adx_series = _adx(h, lo, c, 14)
        adx_now = float(adx_series.iloc[-1])
        if adx_now < 20.0:
            return REGIME_CHOPPY, _REGIME_MULTS[REGIME_CHOPPY]

        # ── EMA5 vs EMA20 slope (using daily resampled or available bars) ────
        ema5  = _ema(c, 5)
        ema20 = _ema(c, 20)
        ema_slope = (float(ema5.iloc[-1]) - float(ema20.iloc[-1])) / (
            float(ema20.iloc[-1]) + 1e-9
        )

        # ── Breadth proxy: fraction of last 20 bars that closed up ───────────
        last20_closes = c.iloc[-20:]
        adv = int((last20_closes.diff().dropna() > 0).sum())
        breadth = adv / max(len(last20_closes) - 1, 1)

        # ── Classify ─────────────────────────────────────────────────────────
        if ema_slope > 0.001 and breadth > 0.60:
            return REGIME_BULL_TREND, _REGIME_MULTS[REGIME_BULL_TREND]
        if ema_slope < -0.001 and breadth < 0.40:
            return REGIME_BEAR_TREND, _REGIME_MULTS[REGIME_BEAR_TREND]

        # Mixed signals → choppy (cautious)
        return REGIME_CHOPPY, _REGIME_MULTS[REGIME_CHOPPY]

    except Exception as e:
        logger.debug(f"_detect_regime error (fail-safe): {e}")
        return _safe_default


# ── Trade class ───────────────────────────────────────────────────────────────

class Trade:
    __slots__ = ("symbol", "direction", "entry", "sl", "t1", "t2", "qty",
                 "entry_time", "t1_done", "exit_price", "exit_time", "pnl", "r_mult")

    def __init__(self, symbol, direction, entry, sl, t1, t2, qty, ts):
        self.symbol = symbol; self.direction = direction
        self.entry = entry; self.sl = sl; self.t1 = t1; self.t2 = t2
        self.qty = qty; self.entry_time = ts; self.t1_done = False
        self.exit_price = None; self.exit_time = None; self.pnl = 0.0; self.r_mult = 0.0


def _simulate_exit(trade: Trade, future: pd.DataFrame) -> float:
    """Walk forward bars, simulate partial exit at T1 then trail/T2 for runner."""
    long = trade.direction == "LONG"
    half = trade.qty // 2 or 1
    runner = trade.qty - half
    realized = 0.0

    for ts, bar in future.iterrows():
        # Force square-off at 15:20 IST
        if ts.time() >= SQUAREOFF:
            px = bar["close"]
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * qty_left
            trade.exit_price = px; trade.exit_time = ts
            break

        hi = bar["high"]; lo = bar["low"]

        # Stop-loss check
        sl_hit = (lo <= trade.sl) if long else (hi >= trade.sl)
        if sl_hit:
            px = trade.sl
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * qty_left
            trade.exit_price = px; trade.exit_time = ts; break

        # T1 partial exit (50% at 1R)
        if not trade.t1_done:
            t1_hit = (hi >= trade.t1) if long else (lo <= trade.t1)
            if t1_hit:
                realized += ((trade.t1 - trade.entry) if long else (trade.entry - trade.t1)) * half
                trade.t1_done = True
                # Move SL to breakeven for runner
                trade.sl = trade.entry

        # T2 full exit (runner at 2R)
        if trade.t1_done:
            t2_hit = (hi >= trade.t2) if long else (lo <= trade.t2)
            if t2_hit:
                realized += ((trade.t2 - trade.entry) if long else (trade.entry - trade.t2)) * runner
                trade.exit_price = trade.t2; trade.exit_time = ts; break
    else:
        if len(future):
            px = future["close"].iloc[-1]
            qty_left = runner + (0 if trade.t1_done else half)
            realized += ((px - trade.entry) if long else (trade.entry - px)) * qty_left
            trade.exit_price = px; trade.exit_time = future.index[-1]

    # Costs: 29bps round-trip on entry + exit notional
    entry_val = trade.entry * trade.qty
    exit_val  = (trade.exit_price or trade.entry) * trade.qty
    realized -= (entry_val + exit_val) * COST_RT_PCT / 2
    trade.pnl = realized
    # R-multiple
    r = abs(trade.entry - trade.sl) * trade.qty
    trade.r_mult = realized / max(r, 1e-9)
    return realized


# ── Kelly sizing ──────────────────────────────────────────────────────────────

def _kelly_size(wins: int, losses: int, capital: float, max_pct: float = 0.20) -> float:
    """Half-Kelly position size as fraction of capital."""
    total = wins + losses
    if total < 10:
        return 0.005   # cold start: risk 0.5%
    p = wins / total
    b = 2.0            # avg win/loss ratio (1R SL, 2R TP)
    kelly = max(0, (p * b - (1 - p)) / b)
    return min(kelly * 0.5, 0.01)  # half-Kelly, cap at 1% risk per trade


# ── Resample helpers ──────────────────────────────────────────────────────────

def _resample(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
    try:
        r = df.resample(rule).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"
        }).dropna()
        return _compute_all(r) if not r.empty else None
    except Exception:
        return None


# ── Data fetch ────────────────────────────────────────────────────────────────

def _fetch(client, symbol: str, from_date: str, to_date: str) -> Optional[pd.DataFrame]:
    from data_fetch_upstox import get_security_id, _candles_to_df
    key = get_security_id(symbol)
    if not key:
        print(f"  {symbol}: no instrument_key — skipped")
        return None

    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end   = datetime.strptime(to_date,   "%Y-%m-%d").date()
    frames = []
    cur = start

    while cur <= end:
        nxt = min(cur + timedelta(days=30), end)
        for interval in ("1minute", "30minute"):
            for attempt in range(3):
                try:
                    resp = client.history.get_historical_candle_data1(
                        instrument_key=key, interval=interval,
                        to_date=nxt.strftime("%Y-%m-%d"),
                        from_date=cur.strftime("%Y-%m-%d"),
                        api_version="2.0",
                    )
                    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
                    candles = (getattr(data, "candles", None)
                               or (data.get("candles") if isinstance(data, dict) else None))
                    df_c = _candles_to_df(candles)
                    if df_c is not None and not df_c.empty:
                        if interval == "1minute":
                            df_c = df_c.resample("5min").agg({
                                "open": "first", "high": "max",
                                "low": "min", "close": "last", "volume": "sum"
                            }).dropna()
                        frames.append(df_c)
                        break
                    break
                except Exception as e:
                    err = str(e).lower()
                    if any(x in err for x in ("invalid", "400", "422")):
                        break
                    _time.sleep(2 ** attempt)
            else:
                continue
            break
        cur = nxt + timedelta(days=1)

    if not frames:
        return None
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.between_time("09:15", "15:30")
    if len(df) < 50:
        return None

    # Compute indicators + ORB
    df = _compute_all(df)
    df = _build_orb(df)
    return df


# ── Main replay ───────────────────────────────────────────────────────────────

MIN_SCORE    = 28.0   # net score threshold for entry (long or short)
MAX_OPEN     = 5      # max simultaneous positions
RISK_PCT     = 0.005  # 0.5% capital at risk per trade
MAX_POS_PCT  = 0.20   # max 20% of capital per position


def run_backtest(symbols: List[str], from_date: str, to_date: str,
                 capital: float = 500_000.0):
    from auth_upstox import get_upstox_client, verify_connection
    import data_fetch_upstox as dfu

    print("Connecting to Upstox API ...")
    client = get_upstox_client()
    if not client or not verify_connection(client):
        print("ERROR: Upstox connection failed. Check UPSTOX_ACCESS_TOKEN in .env")
        return
    dfu.set_upstox_client(client)

    print(f"Fetching historical data: {len(symbols)} symbols  {from_date} → {to_date}")
    data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = _fetch(client, sym, from_date, to_date)
        if df is not None:
            data[sym] = df
            print(f"  {sym}: {len(df)} bars  ({len(df)//75:.0f} trading days)")
        else:
            print(f"  {sym}: skipped")

    if not data:
        print("No data loaded. Check UPSTOX_ACCESS_TOKEN and symbol list.")
        return

    print(f"\nRunning backtest on {len(data)} symbols ...")

    trades: List[Trade] = []
    open_trades: Dict[str, Trade] = {}
    equity = capital
    peak   = capital
    max_dd = 0.0
    equity_curve = [capital]
    wins = losses = 0

    all_ts = sorted(set().union(*[set(df.index) for df in data.values()]))

    for i, now_ts in enumerate(all_ts):
        if now_ts.time() < dtime(9, 25) or now_ts.time() > dtime(15, 0):
            continue

        # ── Exit open trades ────────────────────────────────────────────────
        for sym in list(open_trades.keys()):
            t = open_trades[sym]
            if sym not in data or now_ts not in data[sym].index:
                continue
            bar = data[sym].loc[now_ts]
            long = t.direction == "LONG"

            # Square-off
            if now_ts.time() >= SQUAREOFF:
                px = bar["close"]
                qty_left = t.qty - (t.qty // 2 if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
                (wins if pnl > 0 else losses).__class__  # dummy
                if pnl > 0: wins += 1
                else: losses += 1
                continue

            hi = bar["high"]; lo = bar["low"]
            sl_hit = (lo <= t.sl) if long else (hi >= t.sl)
            if sl_hit:
                px = t.sl
                qty_left = t.qty - (t.qty // 2 if t.t1_done else 0)
                pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
                pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
                equity += pnl; t.pnl = pnl
                t.exit_price = px; t.exit_time = now_ts
                trades.append(t); del open_trades[sym]
                if pnl > 0: wins += 1
                else: losses += 1
                continue

            if not t.t1_done:
                t1_hit = (hi >= t.t1) if long else (lo <= t.t1)
                if t1_hit:
                    half = t.qty // 2 or 1
                    pnl_partial = ((t.t1 - t.entry) if long else (t.entry - t.t1)) * half
                    equity += pnl_partial
                    t.t1_done = True; t.sl = t.entry   # BE stop for runner

            if t.t1_done:
                t2_hit = (hi >= t.t2) if long else (lo <= t.t2)
                if t2_hit:
                    runner = t.qty - (t.qty // 2 or 1)
                    pnl_r = ((t.t2 - t.entry) if long else (t.entry - t.t2)) * runner
                    pnl_r -= (t.entry * t.qty + t.t2 * t.qty) * COST_RT_PCT / 2
                    equity += pnl_r; t.pnl += pnl_r
                    t.exit_price = t.t2; t.exit_time = now_ts
                    trades.append(t); del open_trades[sym]
                    if t.pnl > 0: wins += 1
                    else: losses += 1

        peak   = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        equity_curve.append(equity)

        # ── New entries ─────────────────────────────────────────────────────
        if len(open_trades) >= MAX_OPEN:
            continue

        for sym, df in data.items():
            if sym in open_trades or len(open_trades) >= MAX_OPEN:
                continue
            if now_ts not in df.index:
                continue
            idx = df.index.get_loc(now_ts)
            if idx < 55:
                continue   # need warm-up bars for indicators

            row  = df.iloc[idx]
            prev = df.iloc[idx - 1]

            # Resample for multi-timeframe without re-fetching
            df_15m = _resample(df.iloc[:idx+1], "15min")
            df_1h  = _resample(df.iloc[:idx+1], "1h")

            try:
                net_score, direction, reason = _score_bar(row, prev, df, df_15m, df_1h, idx)
            except Exception as e:
                continue

            if abs(net_score) < MIN_SCORE:
                continue

            # ATR-based SL/TP
            atr = row.get("atr", row["close"] * 0.005)
            if atr <= 0:
                continue
            entry = row["close"]
            long  = direction == "LONG"
            sl    = entry - 1.5 * atr if long else entry + 1.5 * atr
            t1    = entry + 1.5 * atr if long else entry - 1.5 * atr   # 1R
            t2    = entry + 3.0 * atr if long else entry - 3.0 * atr   # 2R

            # Kelly-based sizing
            risk_pct = _kelly_size(wins, losses, equity)
            sl_dist  = abs(entry - sl)
            qty = int(min(equity * risk_pct / sl_dist,
                          equity * MAX_POS_PCT / entry))
            if qty < 1:
                continue

            trade = Trade(sym, direction, entry, sl, t1, t2, qty, now_ts)
            open_trades[sym] = trade

    # Close any still-open trades at last price
    for sym, t in open_trades.items():
        if sym in data:
            px = data[sym]["close"].iloc[-1]
            long = t.direction == "LONG"
            qty_left = t.qty - (t.qty // 2 if t.t1_done else 0)
            pnl = ((px - t.entry) if long else (t.entry - px)) * qty_left
            pnl -= (t.entry * t.qty + px * t.qty) * COST_RT_PCT / 2
            equity += pnl; t.pnl = pnl; t.exit_price = px
            trades.append(t)
            if pnl > 0: wins += 1
            else: losses += 1

    _report(trades, capital, equity, max_dd, equity_curve, from_date, to_date, len(data))


def _report(trades, capital, equity, max_dd, eq_curve, from_date, to_date, n_syms):
    print()
    print("=" * 70)
    print("  NSE Momentum Bot — Engine Backtest Report")
    print(f"  Period  : {from_date} → {to_date}")
    print(f"  Symbols : {n_syms} loaded")
    print(f"  Capital : ₹{capital:,.0f}")
    print("=" * 70)

    if not trades:
        print("  No trades generated.")
        print("=" * 70)
        return

    wins   = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    wr     = len(wins) / len(trades) * 100
    gross_win  = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses)) or 1e-9
    pf         = gross_win / gross_loss
    avg_win    = gross_win  / max(len(wins), 1)
    avg_loss   = gross_loss / max(len(losses), 1)
    expectancy = (wr / 100 * avg_win) - ((1 - wr / 100) * avg_loss)
    ret_pct    = (equity - capital) / capital * 100
    days       = max((datetime.strptime(to_date, "%Y-%m-%d") -
                      datetime.strptime(from_date, "%Y-%m-%d")).days, 1)
    monthly    = ret_pct / days * 30

    # Sharpe (annualised from per-trade P&L)
    pnls = [t.pnl / capital for t in trades]
    try:
        sharpe = (statistics.mean(pnls) / (statistics.stdev(pnls) + 1e-9)) * (252 ** 0.5)
    except Exception:
        sharpe = 0.0

    calmar = abs(ret_pct / (max_dd * 100 + 1e-9))

    # Equity sparkline
    lo = min(eq_curve); hi = max(eq_curve); rng = max(hi - lo, 1)
    spark = "".join("▁▂▃▄▅▆▇█"[min(7, int((v - lo) / rng * 7))] for v in eq_curve[::max(1, len(eq_curve)//60)])

    print(f"\n  Equity curve: {spark}")
    print()
    print(f"  {'Total trades':<22}: {len(trades)}")
    print(f"  {'Win rate':<22}: {wr:.1f}%  (W:{len(wins)} / L:{len(losses)})")
    print(f"  {'Profit factor':<22}: {pf:.2f}")
    print(f"  {'Avg win':<22}: ₹{avg_win:,.0f}")
    print(f"  {'Avg loss':<22}: ₹{avg_loss:,.0f}")
    print(f"  {'Expectancy/trade':<22}: ₹{expectancy:,.0f}")
    print()
    print(f"  {'Total P&L':<22}: ₹{equity - capital:+,.0f}  ({ret_pct:+.2f}%)")
    print(f"  {'Monthly return':<22}: {monthly:+.2f}%")
    print(f"  {'Max drawdown':<22}: {max_dd * 100:.2f}%")
    print(f"  {'Sharpe ratio':<22}: {sharpe:.2f}")
    print(f"  {'Calmar ratio':<22}: {calmar:.2f}")
    print()

    # Per-symbol breakdown (top 5)
    sym_stats: Dict[str, dict] = {}
    for t in trades:
        s = sym_stats.setdefault(t.symbol, {"pnl": 0, "w": 0, "l": 0})
        s["pnl"] += t.pnl
        if t.pnl > 0: s["w"] += 1
        else: s["l"] += 1
    top5 = sorted(sym_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]
    bot3 = sorted(sym_stats.items(), key=lambda x: x[1]["pnl"])[:3]
    print("  Top 5 symbols:")
    for sym, s in top5:
        n = s["w"] + s["l"]
        print(f"    {sym:<14} WR={s['w']/max(n,1)*100:.0f}%  trades={n}  P&L=₹{s['pnl']:+,.0f}")
    print("  Bottom 3 symbols:")
    for sym, s in bot3:
        n = s["w"] + s["l"]
        print(f"    {sym:<14} WR={s['w']/max(n,1)*100:.0f}%  trades={n}  P&L=₹{s['pnl']:+,.0f}")
    print("=" * 70)
    if sharpe >= 2.0:
        print(f"  ✓ Sharpe {sharpe:.2f} ≥ 2.0 TARGET MET")
    else:
        print(f"  ✗ Sharpe {sharpe:.2f} < 2.0 (add more filters to improve)")
    if monthly >= 10:
        print(f"  ✓ Monthly {monthly:.1f}% ≥ 10% TARGET MET")
    else:
        print(f"  ✗ Monthly {monthly:.1f}% — need higher WR or larger position")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default="")
    ap.add_argument("--days",    type=int, default=60)
    ap.add_argument("--from",    dest="from_date", type=str, default="")
    ap.add_argument("--to",      dest="to_date",   type=str, default="")
    ap.add_argument("--capital", type=float, default=500_000.0)
    args = ap.parse_args()

    if args.from_date and args.to_date:
        from_date, to_date = args.from_date, args.to_date
    else:
        to   = datetime.now(IST).date()
        frm  = to - timedelta(days=min(args.days, 90))
        from_date = frm.strftime("%Y-%m-%d")
        to_date   = to.strftime("%Y-%m-%d")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = [
            "RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK",
            "SBIN", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "ITC",
            "BHARTIARTL", "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO",
            "ADANIENT", "ADANIPORTS", "TATAMOTORS", "TATASTEEL", "SUNPHARMA",
            "DRREDDY", "CIPLA", "LT", "POWERGRID", "NTPC", "ONGC",
            "HCLTECH", "TECHM", "BAJAJFINSV", "TITAN", "NESTLEIND",
            "ULTRACEMCO", "JSWSTEEL", "GRASIM", "HEROMOTOCO", "EICHERMOT",
            "BPCL", "HINDALCO", "BRITANNIA", "INDUSINDBK", "TATACONSUM",
        ]

    run_backtest(symbols, from_date, to_date, args.capital)


if __name__ == "__main__":
    main()
