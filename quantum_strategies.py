"""
quantum_strategies.py — 8 Professional Trading Strategies (v12.0)

Institutional-grade strategies that complement the existing signal engine.
Each returns (score_delta: float, reason: str). All cached, all fail-open.

Strategy 1: Opening Range Breakout (ORB)
  The #1 intraday strategy used by professional traders.
  First 30-min candle (9:30-10:00 ET) defines the range.
  Price breaking above ORB high with volume = LONG signal.
  Price breaking below ORB low with volume = SHORT signal.
  Score: +15 if price breaks ORB in direction + RVOL>1.5x
         +10 if price breaks ORB in direction

Strategy 2: VWAP Deviation Bands
  Price > 2 std deviations above VWAP = mean reversion SHORT
  Price < 2 std deviations below VWAP = mean reversion LONG
  Price between VWAP +/- 1 std = trend continuation
  Score: +12 for reversion entry at 2-std band
         +8 for continuation in VWAP trend direction

Strategy 3: Market Profile / POC Analysis (replaces Sierra Charts $50/month)
  Point of Control (POC) = price level with most volume today
  Value Area High (VAH) = top 70% of volume
  Value Area Low (VAL) = bottom 70% of volume
  Trading: price returns to POC = high-probability trade
  Score: +10 if price approaching POC from outside
         +7 if price at VAH/VAL boundary

Strategy 4: Order Flow Imbalance (replaces Bookmap $200/month)
  Calculate buy/sell pressure from bar data:
  Buy pressure = (close - low) / (high - low) * volume
  Sell pressure = (high - close) / (high - low) * volume
  Cumulative delta = sum of (buy - sell) over last 10 bars
  Score: +10 if cumulative delta > +200k (heavy buying)
         -10 if cumulative delta < -200k (heavy selling)
         +6 if bar delta strongly positive + price up

Strategy 5: Gamma Squeeze Setup (replaces SpotGamma $100/month)
  Options gamma exposure approximation from options chain data.
  When stock price approaches major options strike with high OI,
  market makers must hedge = price acceleration ("gamma squeeze")
  Use yfinance to find nearest expiry strike with highest OI
  Score: +8 if price within 1% of max-OI strike + price moving toward it
         +12 if price just crossed max-OI strike (gamma squeeze active)

Strategy 6: Statistical Z-Score Mean Reversion
  Calculate 20-day rolling mean and std of closing prices.
  Z-score = (current_price - rolling_mean) / rolling_std
  Trade reversions to mean:
  Score: +10 if z_score < -2.0 (extremely oversold) + direction LONG
         +10 if z_score > +2.0 (extremely overbought) + direction SHORT
         +6 if |z_score| between 1.5-2.0
         -8 if trading against the reversion signal

Strategy 7: Earnings Momentum (PEAD -- Post-Earnings Announcement Drift)
  After earnings beat: stocks drift UP for 3-20 days (PEAD effect)
  After earnings miss: stocks drift DOWN for 3-20 days
  Uses yfinance for quarterly earnings data and analyst estimates
  Score: +10 if beat > 10% + within 5 days + direction LONG
         +8 if beat > 5% + within 10 days
         -10 if miss > 5% + direction LONG (fight the drift)

Strategy 8: Multi-Day Momentum Persistence
  Stocks up >3% in last 3 days tend to continue (momentum persistence)
  Stocks down >5% in 3 days tend to continue (negative persistence)
  Calculate 3-day and 5-day momentum from daily closes via yfinance
  Score LONG: +8 if 3d_momentum > +3% (bullish persistence)
              +5 if 3d_momentum > +1.5%
              -6 if 3d_momentum < -3% (fight the trend)
  Score SHORT: opposite signs
"""

import logging
import time as _time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---- Cache containers -------------------------------------------------------
_orb_cache:      Dict = {}   # {symbol: {"ts": float, "orb_high": float, "orb_low": float, "rvol": float}}
_vwap_dev_cache: Dict = {}   # {symbol: {"ts": float, "upper2": float, "lower2": float, "vwap": float}}
_mp_cache:       Dict = {}   # {symbol: {"ts": float, "poc": float, "vah": float, "val": float}}
_of_cache:       Dict = {}   # {symbol: {"ts": float, "cum_delta": float}}
_gamma_cache:    Dict = {}   # {symbol: {"ts": float, "max_oi_strike": float, "expiry": str}}
_zscore_cache:   Dict = {}   # {symbol: {"ts": float, "zscore": float, "mean": float, "std": float}}
_pead_cache:     Dict = {}   # {symbol: {"ts": float, "beat_pct": float, "days_since": int}}
_momentum_cache: Dict = {}   # {symbol: {"ts": float, "mom_3d": float, "mom_5d": float}}

_ORB_TTL    = 300.0    # 5 min -- ORB refreshes intraday as more bars form
_VWAP_TTL   = 300.0    # 5 min
_MP_TTL     = 600.0    # 10 min -- market profile changes slowly
_OF_TTL     = 120.0    # 2 min -- order flow is fast-moving
_GAMMA_TTL  = 3600.0   # 1 hr -- options OI does not change fast
_ZSCORE_TTL = 3600.0   # 1 hr -- z-score uses daily data
_PEAD_TTL   = 14400.0  # 4 hr -- earnings do not change during day
_MOM_TTL    = 3600.0   # 1 hr -- momentum uses daily closes


# =============================================================================
# Strategy 1: Opening Range Breakout (ORB)
# =============================================================================

def _compute_orb_data(symbol: str, df_5m: pd.DataFrame) -> Optional[Dict]:
    """
    Derive ORB high/low from the first 6 bars (9:30-10:00 AM ET).
    Returns dict with orb_high, orb_low, rvol or None on failure.
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 7:
            return None

        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]

        # Identify ORB bars: first 6 bars of the session (first 30 min)
        idx = df.index
        try:
            if hasattr(idx[0], 'tzinfo') and idx[0].tzinfo is not None:
                from zoneinfo import ZoneInfo
                et = ZoneInfo("America/New_York")
                open_time = idx[0].replace(hour=9, minute=30, second=0, microsecond=0).astimezone(et)
                orb_end   = open_time + timedelta(minutes=30)
                orb_bars  = df[df.index < orb_end]
            else:
                orb_bars = df.iloc[:6]
        except Exception:
            orb_bars = df.iloc[:6]

        if orb_bars.empty:
            return None

        orb_high = float(orb_bars['high'].max())
        orb_low  = float(orb_bars['low'].min())

        # RVOL: compare most recent bar volume to 20-bar average
        if 'volume' in df.columns:
            vol_series = df['volume']
        else:
            return {"orb_high": orb_high, "orb_low": orb_low, "rvol": 1.0}

        avg_vol = vol_series.iloc[:-1].tail(20).mean()
        cur_vol = float(vol_series.iloc[-1])
        rvol = cur_vol / avg_vol if avg_vol > 0 else 1.0

        return {"orb_high": orb_high, "orb_low": orb_low, "rvol": rvol}
    except Exception as e:
        logger.debug(f"[quantum] ORB compute error for {symbol}: {e}")
        return None


def get_orb_score(
    symbol: str,
    df_5m: pd.DataFrame,
    direction: str,
    ltp: float,
) -> Tuple[float, str]:
    """
    Strategy 1: Opening Range Breakout.
    Score +15 if price breaks ORB range in direction with RVOL>1.5x.
    Score +10 for range break without elevated volume.
    Score -8  if price breaks opposite to trade direction.
    """
    try:
        now_ts = _time.monotonic()
        cached = _orb_cache.get(symbol)
        if not cached or (now_ts - cached["ts"]) > _ORB_TTL:
            data = _compute_orb_data(symbol, df_5m)
            if data is None:
                return 0.0, ""
            _orb_cache[symbol] = {"ts": now_ts, **data}
            cached = _orb_cache[symbol]

        orb_high = cached["orb_high"]
        orb_low  = cached["orb_low"]
        rvol     = cached.get("rvol", 1.0)

        if ltp <= 0 or orb_high <= orb_low:
            return 0.0, ""

        orb_range = orb_high - orb_low
        # Breakout condition: price must be meaningfully outside the range (5% of range)
        breakout_threshold = orb_range * 0.05

        if direction == "LONG":
            if ltp > orb_high + breakout_threshold:
                if rvol >= 1.5:
                    return 15.0, f"ORB LONG breakout @ {ltp:.2f} > {orb_high:.2f} RVOL={rvol:.1f}x"
                return 10.0, f"ORB LONG breakout @ {ltp:.2f} > {orb_high:.2f}"
            if ltp < orb_low - breakout_threshold:
                return -8.0, f"ORB LONG blocked -- price {ltp:.2f} broke ORB low {orb_low:.2f}"
        else:  # SHORT
            if ltp < orb_low - breakout_threshold:
                if rvol >= 1.5:
                    return 15.0, f"ORB SHORT breakdown @ {ltp:.2f} < {orb_low:.2f} RVOL={rvol:.1f}x"
                return 10.0, f"ORB SHORT breakdown @ {ltp:.2f} < {orb_low:.2f}"
            if ltp > orb_high + breakout_threshold:
                return -8.0, f"ORB SHORT blocked -- price {ltp:.2f} broke ORB high {orb_high:.2f}"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"[quantum] get_orb_score {symbol}: {e}")
        return 0.0, ""


# =============================================================================
# Strategy 2: VWAP Deviation Bands
# =============================================================================

def _compute_vwap_bands(df_5m: pd.DataFrame) -> Optional[Dict]:
    """
    Compute VWAP and +/-1 sigma / +/-2 sigma deviation bands from intraday bar data.
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 5:
            return None

        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]

        typical_price = (df['high'] + df['low'] + df['close']) / 3.0
        vol = df['volume'] if 'volume' in df.columns else pd.Series(
            np.ones(len(df)), index=df.index
        )
        vol = vol.replace(0, 1)

        cum_tp_vol = (typical_price * vol).cumsum()
        cum_vol    = vol.cumsum()
        vwap       = cum_tp_vol / cum_vol

        # Rolling std of typical price deviation around VWAP
        dev         = typical_price - vwap
        rolling_std = dev.rolling(window=min(20, len(df))).std().fillna(dev.std())
        std_val     = float(rolling_std.iloc[-1])
        vwap_val    = float(vwap.iloc[-1])

        return {
            "vwap":   vwap_val,
            "upper1": vwap_val + std_val,
            "upper2": vwap_val + 2 * std_val,
            "lower1": vwap_val - std_val,
            "lower2": vwap_val - 2 * std_val,
        }
    except Exception as e:
        logger.debug(f"[quantum] VWAP bands compute error: {e}")
        return None


def get_vwap_deviation_score(
    symbol: str,
    df_5m: pd.DataFrame,
    direction: str,
    vwap_ref: float,
) -> Tuple[float, str]:
    """
    Strategy 2: VWAP Deviation Bands.
    Mean reversion at 2-std band (+12) or trend continuation (+8).
    """
    try:
        now_ts = _time.monotonic()
        cached = _vwap_dev_cache.get(symbol)
        if not cached or (now_ts - cached["ts"]) > _VWAP_TTL:
            data = _compute_vwap_bands(df_5m)
            if data is None:
                return 0.0, ""
            _vwap_dev_cache[symbol] = {"ts": now_ts, **data}
            cached = _vwap_dev_cache[symbol]

        if df_5m is None or df_5m.empty:
            return 0.0, ""
        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]
        ltp = float(df['close'].iloc[-1])

        vwap   = cached["vwap"]
        upper2 = cached["upper2"]
        lower2 = cached["lower2"]
        upper1 = cached["upper1"]
        lower1 = cached["lower1"]

        if vwap <= 0:
            return 0.0, ""

        if direction == "LONG":
            if ltp <= lower2:
                return 12.0, f"VWAP reversion LONG: price {ltp:.2f} at -2 sigma band {lower2:.2f}"
            if ltp <= lower1:
                return 8.0, f"VWAP reversion LONG: price {ltp:.2f} at -1 sigma band {lower1:.2f}"
            if lower1 < ltp <= vwap:
                return 5.0, f"VWAP trend LONG: price {ltp:.2f} approaching VWAP {vwap:.2f}"
            if ltp > upper2:
                return -10.0, f"VWAP LONG blocked: price {ltp:.2f} extended +2 sigma above VWAP {vwap:.2f}"
        else:  # SHORT
            if ltp >= upper2:
                return 12.0, f"VWAP reversion SHORT: price {ltp:.2f} at +2 sigma band {upper2:.2f}"
            if ltp >= upper1:
                return 8.0, f"VWAP reversion SHORT: price {ltp:.2f} at +1 sigma band {upper1:.2f}"
            if vwap <= ltp < upper1:
                return 5.0, f"VWAP trend SHORT: price {ltp:.2f} at VWAP {vwap:.2f}"
            if ltp < lower2:
                return -10.0, f"VWAP SHORT blocked: price {ltp:.2f} extended -2 sigma below VWAP {vwap:.2f}"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"[quantum] get_vwap_deviation_score {symbol}: {e}")
        return 0.0, ""


# =============================================================================
# Strategy 3: Market Profile / POC Analysis
# =============================================================================

def _compute_market_profile(df_5m: pd.DataFrame) -> Optional[Dict]:
    """
    Compute Point of Control (POC), Value Area High (VAH), Value Area Low (VAL)
    from intraday 5-min bars using volume distribution across 50 price levels.
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 5:
            return None

        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]
        vol = (
            df['volume']
            if 'volume' in df.columns
            else pd.Series(np.ones(len(df)), index=df.index)
        )

        price_min = float(df['low'].min())
        price_max = float(df['high'].max())
        if price_max <= price_min:
            return None

        n_levels   = 50
        price_bins = np.linspace(price_min, price_max, n_levels + 1)
        bin_vol    = np.zeros(n_levels)

        for i in range(len(df)):
            bar_vol  = float(vol.iloc[i])
            bar_low  = float(df['low'].iloc[i])
            bar_high = float(df['high'].iloc[i])
            bar_span = max(bar_high - bar_low, 1e-8)
            for b in range(n_levels):
                bin_lo = price_bins[b]
                bin_hi = price_bins[b + 1]
                overlap_lo = max(bin_lo, bar_low)
                overlap_hi = min(bin_hi, bar_high)
                if overlap_hi > overlap_lo:
                    frac = (overlap_hi - overlap_lo) / bar_span
                    bin_vol[b] += bar_vol * frac

        poc_idx = int(np.argmax(bin_vol))
        poc     = float((price_bins[poc_idx] + price_bins[poc_idx + 1]) / 2.0)

        # Value Area = 70% of total volume centred on POC
        total_vol  = bin_vol.sum()
        target_vol = total_vol * 0.70
        val_idx    = poc_idx
        vah_idx    = poc_idx
        accum_vol  = bin_vol[poc_idx]

        while accum_vol < target_vol:
            expand_lo = (val_idx > 0 and bin_vol[val_idx - 1] > 0)
            expand_hi = (vah_idx < n_levels - 1 and bin_vol[vah_idx + 1] > 0)
            if not expand_lo and not expand_hi:
                break
            add_lo = bin_vol[val_idx - 1] if expand_lo else -1.0
            add_hi = bin_vol[vah_idx + 1] if expand_hi else -1.0
            if add_hi >= add_lo and expand_hi:
                vah_idx   += 1
                accum_vol += bin_vol[vah_idx]
            elif expand_lo:
                val_idx   -= 1
                accum_vol += bin_vol[val_idx]
            else:
                break

        vah = float((price_bins[vah_idx] + price_bins[vah_idx + 1]) / 2.0)
        val = float((price_bins[val_idx] + price_bins[val_idx + 1]) / 2.0)

        return {"poc": poc, "vah": vah, "val": val}
    except Exception as e:
        logger.debug(f"[quantum] Market profile compute error: {e}")
        return None


def get_market_profile_score(
    symbol: str,
    df_5m: pd.DataFrame,
    direction: str,
    ltp: float,
) -> Tuple[float, str]:
    """
    Strategy 3: Market Profile / POC Analysis.
    Score based on price proximity to POC, VAH, VAL.
    """
    try:
        now_ts = _time.monotonic()
        cached = _mp_cache.get(symbol)
        if not cached or (now_ts - cached["ts"]) > _MP_TTL:
            data = _compute_market_profile(df_5m)
            if data is None:
                return 0.0, ""
            _mp_cache[symbol] = {"ts": now_ts, **data}
            cached = _mp_cache[symbol]

        poc = cached["poc"]
        vah = cached["vah"]
        val = cached["val"]

        if poc <= 0 or ltp <= 0:
            return 0.0, ""

        pct_from_poc = abs(ltp - poc) / poc * 100.0

        if direction == "LONG":
            if val <= ltp <= poc and pct_from_poc <= 0.5:
                return 10.0, f"MktProfile: price {ltp:.2f} at POC {poc:.2f} (value area)"
            if abs(ltp - val) / poc * 100.0 <= 0.5:
                return 7.0, f"MktProfile: price {ltp:.2f} at VAL {val:.2f}"
            if ltp > vah:
                return 5.0, f"MktProfile: price {ltp:.2f} above VAH {vah:.2f} (breakout from value)"
        else:  # SHORT
            if poc <= ltp <= vah and pct_from_poc <= 0.5:
                return 10.0, f"MktProfile: price {ltp:.2f} at POC {poc:.2f} (value area)"
            if abs(ltp - vah) / poc * 100.0 <= 0.5:
                return 7.0, f"MktProfile: price {ltp:.2f} at VAH {vah:.2f}"
            if ltp < val:
                return 5.0, f"MktProfile: price {ltp:.2f} below VAL {val:.2f} (breakdown from value)"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"[quantum] get_market_profile_score {symbol}: {e}")
        return 0.0, ""


# =============================================================================
# Strategy 4: Order Flow Imbalance
# =============================================================================

def _compute_order_flow(df_5m: pd.DataFrame) -> Optional[Dict]:
    """
    Approximate order flow from bar data.
    Buy pressure  = (close - low)  / (high - low) * volume
    Sell pressure = (high - close) / (high - low) * volume
    Cumulative delta = sum of (buy - sell) over last 10 bars.
    """
    try:
        if df_5m is None or df_5m.empty or len(df_5m) < 3:
            return None

        df = df_5m.copy()
        df.columns = [c.lower() for c in df.columns]
        vol = (
            df['volume']
            if 'volume' in df.columns
            else pd.Series(np.ones(len(df)), index=df.index)
        )

        bar_range  = (df['high'] - df['low']).replace(0, 1e-8)
        buy_press  = (df['close'] - df['low'])  / bar_range * vol
        sell_press = (df['high']  - df['close']) / bar_range * vol

        n          = min(10, len(df))
        cum_delta  = float((buy_press.tail(n) - sell_press.tail(n)).sum())
        last_delta = float(buy_press.iloc[-1] - sell_press.iloc[-1])
        last_up    = float(df['close'].iloc[-1]) > float(df['open'].iloc[-1])

        return {"cum_delta": cum_delta, "last_bar_delta": last_delta, "last_bar_up": last_up}
    except Exception as e:
        logger.debug(f"[quantum] Order flow compute error: {e}")
        return None


def get_order_flow_score(
    symbol: str,
    df_5m: pd.DataFrame,
    direction: str,
) -> Tuple[float, str]:
    """
    Strategy 4: Order Flow Imbalance.
    Cumulative delta heavy buying (+10) / selling (-10) / bar confirmation (+6).
    """
    try:
        now_ts = _time.monotonic()
        cached = _of_cache.get(symbol)
        if not cached or (now_ts - cached["ts"]) > _OF_TTL:
            data = _compute_order_flow(df_5m)
            if data is None:
                return 0.0, ""
            _of_cache[symbol] = {"ts": now_ts, **data}
            cached = _of_cache[symbol]

        cum_delta      = cached["cum_delta"]
        last_bar_delta = cached["last_bar_delta"]
        last_bar_up    = cached["last_bar_up"]

        if direction == "LONG":
            if cum_delta > 200_000:
                return 10.0, f"OrderFlow LONG: cum_delta={cum_delta:,.0f} (heavy buying)"
            if cum_delta < -200_000:
                return -10.0, f"OrderFlow LONG blocked: cum_delta={cum_delta:,.0f} (heavy selling)"
            if last_bar_delta > 0 and last_bar_up:
                return 6.0, f"OrderFlow LONG: last bar delta positive={last_bar_delta:,.0f}"
        else:  # SHORT
            if cum_delta < -200_000:
                return 10.0, f"OrderFlow SHORT: cum_delta={cum_delta:,.0f} (heavy selling)"
            if cum_delta > 200_000:
                return -10.0, f"OrderFlow SHORT blocked: cum_delta={cum_delta:,.0f} (heavy buying)"
            if last_bar_delta < 0 and not last_bar_up:
                return 6.0, f"OrderFlow SHORT: last bar delta negative={last_bar_delta:,.0f}"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"[quantum] get_order_flow_score {symbol}: {e}")
        return 0.0, ""


# =============================================================================
# Strategy 5: Gamma Squeeze Setup
# =============================================================================

def _fetch_gamma_data(symbol: str, ltp: float) -> Optional[Dict]:
    """
    Fetch options chain from yfinance to find max-OI strike near current price.
    Approximates gamma exposure for squeeze detection.
    """
    try:
        import yfinance as yf
        tk          = yf.Ticker(symbol)
        expirations = tk.options
        if not expirations:
            return None

        expiry = expirations[0]
        chain  = tk.option_chain(expiry)
        calls  = chain.calls
        puts   = chain.puts

        if calls.empty and puts.empty:
            return None

        all_oi = pd.concat([
            calls[['strike', 'openInterest']],
            puts[['strike', 'openInterest']],
        ]).groupby('strike')['openInterest'].sum().reset_index()

        all_oi = all_oi[all_oi['openInterest'] > 0]
        if all_oi.empty:
            return None

        max_oi_row    = all_oi.loc[all_oi['openInterest'].idxmax()]
        max_oi_strike = float(max_oi_row['strike'])
        max_oi_val    = float(max_oi_row['openInterest'])

        return {"max_oi_strike": max_oi_strike, "max_oi_val": max_oi_val, "expiry": expiry}
    except Exception as e:
        logger.debug(f"[quantum] Gamma data fetch error for {symbol}: {e}")
        return None


def get_gamma_squeeze_score(
    symbol: str,
    direction: str,
    ltp: float,
) -> Tuple[float, str]:
    """
    Strategy 5: Gamma Squeeze Setup.
    Score +8 if price approaching max-OI strike, +12 if just crossed it.
    """
    try:
        if ltp <= 0:
            return 0.0, ""

        now_ts = _time.monotonic()
        cached = _gamma_cache.get(symbol)
        if not cached or (now_ts - cached["ts"]) > _GAMMA_TTL:
            data = _fetch_gamma_data(symbol, ltp)
            if data is None:
                return 0.0, ""
            _gamma_cache[symbol] = {"ts": now_ts, **data}
            cached = _gamma_cache[symbol]

        max_oi_strike = cached["max_oi_strike"]
        expiry        = cached.get("expiry", "?")

        pct_diff = abs(ltp - max_oi_strike) / max_oi_strike * 100.0

        if direction == "LONG":
            if ltp > max_oi_strike and pct_diff <= 1.0:
                return 12.0, f"Gamma squeeze LONG: crossed max-OI strike {max_oi_strike:.2f} exp={expiry}"
            if ltp < max_oi_strike and pct_diff <= 1.0:
                return 8.0, f"Gamma magnet LONG: price {ltp:.2f} within 1% of max-OI {max_oi_strike:.2f}"
        else:  # SHORT
            if ltp < max_oi_strike and pct_diff <= 1.0:
                return 12.0, f"Gamma squeeze SHORT: crossed max-OI strike {max_oi_strike:.2f} exp={expiry}"
            if ltp > max_oi_strike and pct_diff <= 1.0:
                return 8.0, f"Gamma magnet SHORT: price {ltp:.2f} within 1% of max-OI {max_oi_strike:.2f}"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"[quantum] get_gamma_squeeze_score {symbol}: {e}")
        return 0.0, ""


# =============================================================================
# Strategy 6: Statistical Z-Score Mean Reversion
# =============================================================================

def _fetch_zscore_data(symbol: str) -> Optional[Dict]:
    """
    Download 30 daily bars and compute z-score of current price.
    """
    try:
        import yfinance as yf
        df = yf.download(symbol, period="40d", interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 10:
            return None

        closes = df['Close'].dropna()
        if len(closes) < 10:
            return None

        rolling_mean = float(closes.tail(20).mean())
        rolling_std  = float(closes.tail(20).std())
        current      = float(closes.iloc[-1])

        if rolling_std <= 0:
            return None

        zscore = (current - rolling_mean) / rolling_std
        return {"zscore": zscore, "mean": rolling_mean, "std": rolling_std, "current": current}
    except Exception as e:
        logger.debug(f"[quantum] Z-score data fetch error for {symbol}: {e}")
        return None


def get_zscore_reversion_score(
    symbol: str,
    direction: str,
    ltp: float,
) -> Tuple[float, str]:
    """
    Strategy 6: Statistical Z-Score Mean Reversion.
    Score +10 if z-score > +/-2.0 and direction aligns with reversion.
    Score -8 if trading against reversion signal.
    """
    try:
        now_ts = _time.monotonic()
        cached = _zscore_cache.get(symbol)
        if not cached or (now_ts - cached["ts"]) > _ZSCORE_TTL:
            data = _fetch_zscore_data(symbol)
            if data is None:
                return 0.0, ""
            _zscore_cache[symbol] = {"ts": now_ts, **data}
            cached = _zscore_cache[symbol]

        zscore = cached["zscore"]
        mean   = cached["mean"]
        std    = cached["std"]

        if direction == "LONG":
            if zscore < -2.0:
                return 10.0, f"ZScore LONG: z={zscore:.2f} (extremely oversold, mean={mean:.2f})"
            if -2.0 <= zscore < -1.5:
                return 6.0, f"ZScore LONG: z={zscore:.2f} (oversold range)"
            if zscore > 2.0:
                return -8.0, f"ZScore LONG blocked: z={zscore:.2f} (extremely overbought)"
        else:  # SHORT
            if zscore > 2.0:
                return 10.0, f"ZScore SHORT: z={zscore:.2f} (extremely overbought, mean={mean:.2f})"
            if 1.5 < zscore <= 2.0:
                return 6.0, f"ZScore SHORT: z={zscore:.2f} (overbought range)"
            if zscore < -2.0:
                return -8.0, f"ZScore SHORT blocked: z={zscore:.2f} (extremely oversold)"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"[quantum] get_zscore_reversion_score {symbol}: {e}")
        return 0.0, ""


# =============================================================================
# Strategy 7: Earnings Momentum (PEAD)
# =============================================================================

def _fetch_pead_data(symbol: str) -> Optional[Dict]:
    """
    Fetch earnings surprise data via yfinance.
    Returns beat_pct and days_since_earnings.
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)

        earnings_hist = getattr(tk, 'earnings_history', None)
        if earnings_hist is None or earnings_hist.empty:
            return None

        earnings_hist = earnings_hist.sort_index(ascending=False)
        latest = earnings_hist.iloc[0]

        eps_actual   = latest.get('epsActual', None)
        eps_estimate = latest.get('epsEstimate', None)

        if eps_actual is None or eps_estimate is None:
            return None
        eps_estimate = float(eps_estimate)
        if eps_estimate == 0:
            return None

        beat_pct = (float(eps_actual) - eps_estimate) / abs(eps_estimate) * 100.0

        try:
            edate = pd.to_datetime(earnings_hist.index[0]).date()
        except Exception:
            return None

        days_since = (datetime.now().date() - edate).days

        return {"beat_pct": beat_pct, "days_since": days_since, "earnings_date": str(edate)}
    except Exception as e:
        logger.debug(f"[quantum] PEAD data fetch error for {symbol}: {e}")
        return None


def get_pead_score(
    symbol: str,
    direction: str,
) -> Tuple[float, str]:
    """
    Strategy 7: PEAD -- Post-Earnings Announcement Drift.
    Score +10 for large beat + recent + LONG direction.
    Score -10 for large miss + LONG (fight the drift).
    """
    try:
        now_ts = _time.monotonic()
        cached = _pead_cache.get(symbol)
        if not cached or (now_ts - cached["ts"]) > _PEAD_TTL:
            data = _fetch_pead_data(symbol)
            if data is None:
                return 0.0, ""
            _pead_cache[symbol] = {"ts": now_ts, **data}
            cached = _pead_cache[symbol]

        beat_pct   = cached["beat_pct"]
        days_since = cached["days_since"]
        edate      = cached.get("earnings_date", "?")

        if days_since > 20 or days_since < 0:
            return 0.0, ""

        if direction == "LONG":
            if beat_pct > 10.0 and days_since <= 5:
                return 10.0, f"PEAD LONG: beat={beat_pct:+.1f}% {days_since}d ago ({edate})"
            if beat_pct > 5.0 and days_since <= 10:
                return 8.0, f"PEAD LONG: beat={beat_pct:+.1f}% {days_since}d ago ({edate})"
            if beat_pct < -5.0:
                return -10.0, f"PEAD LONG blocked: miss={beat_pct:+.1f}% {days_since}d ago (drift DOWN)"
        else:  # SHORT
            if beat_pct < -10.0 and days_since <= 5:
                return 10.0, f"PEAD SHORT: miss={beat_pct:+.1f}% {days_since}d ago ({edate})"
            if beat_pct < -5.0 and days_since <= 10:
                return 8.0, f"PEAD SHORT: miss={beat_pct:+.1f}% {days_since}d ago ({edate})"
            if beat_pct > 5.0:
                return -10.0, f"PEAD SHORT blocked: beat={beat_pct:+.1f}% {days_since}d ago (drift UP)"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"[quantum] get_pead_score {symbol}: {e}")
        return 0.0, ""


# =============================================================================
# Strategy 8: Multi-Day Momentum Persistence
# =============================================================================

def _fetch_momentum_data(symbol: str) -> Optional[Dict]:
    """
    Fetch 10 daily bars to compute 3-day and 5-day momentum.
    """
    try:
        import yfinance as yf
        df = yf.download(symbol, period="15d", interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 6:
            return None

        closes = df['Close'].dropna()
        if len(closes) < 6:
            return None

        c_now = float(closes.iloc[-1])
        c_3d  = float(closes.iloc[-4]) if len(closes) >= 4 else c_now
        c_5d  = float(closes.iloc[-6]) if len(closes) >= 6 else c_now

        mom_3d = (c_now - c_3d) / c_3d * 100.0 if c_3d > 0 else 0.0
        mom_5d = (c_now - c_5d) / c_5d * 100.0 if c_5d > 0 else 0.0

        return {"mom_3d": mom_3d, "mom_5d": mom_5d, "current": c_now}
    except Exception as e:
        logger.debug(f"[quantum] Momentum data fetch error for {symbol}: {e}")
        return None


def get_momentum_persistence_score(
    symbol: str,
    direction: str,
) -> Tuple[float, str]:
    """
    Strategy 8: Multi-Day Momentum Persistence.
    Score +8 if 3-day momentum > +3% and direction LONG (persistence).
    Score -6 if trading against strong 3-day momentum.
    """
    try:
        now_ts = _time.monotonic()
        cached = _momentum_cache.get(symbol)
        if not cached or (now_ts - cached["ts"]) > _MOM_TTL:
            data = _fetch_momentum_data(symbol)
            if data is None:
                return 0.0, ""
            _momentum_cache[symbol] = {"ts": now_ts, **data}
            cached = _momentum_cache[symbol]

        mom_3d = cached["mom_3d"]
        mom_5d = cached["mom_5d"]

        if direction == "LONG":
            if mom_3d > 3.0:
                return 8.0, f"MomPersist LONG: 3d={mom_3d:+.1f}% 5d={mom_5d:+.1f}% (bullish persistence)"
            if mom_3d > 1.5:
                return 5.0, f"MomPersist LONG: 3d={mom_3d:+.1f}% (mild bullish persistence)"
            if mom_3d < -3.0:
                return -6.0, f"MomPersist LONG blocked: 3d={mom_3d:+.1f}% (negative persistence)"
        else:  # SHORT
            if mom_3d < -3.0:
                return 8.0, f"MomPersist SHORT: 3d={mom_3d:+.1f}% 5d={mom_5d:+.1f}% (bearish persistence)"
            if mom_3d < -1.5:
                return 5.0, f"MomPersist SHORT: 3d={mom_3d:+.1f}% (mild bearish persistence)"
            if mom_3d > 3.0:
                return -6.0, f"MomPersist SHORT blocked: 3d={mom_3d:+.1f}% (positive persistence)"

        return 0.0, ""
    except Exception as e:
        logger.debug(f"[quantum] get_momentum_persistence_score {symbol}: {e}")
        return 0.0, ""
