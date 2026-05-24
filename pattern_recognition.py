"""
pattern_recognition.py — NSE Momentum Groww AI Bot
20+ Chart Patterns + Full Indicator Stack

Based on 18+ years of NSE intraday trading experience.
Detects: engulfing, hammer, doji, breakouts, flags, pennants,
         VWAP deviations, divergences, ORB, and more.

All patterns return a confidence score (0–100) and direction (LONG/SHORT/NEUTRAL).
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

try:
    import pandas_ta as ta
except ImportError:
    ta = None
    logging.warning("pandas_ta not installed. Run: pip install pandas-ta")

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)


@dataclass
class PatternResult:
    name: str
    direction: str        # "LONG", "SHORT", "NEUTRAL"
    confidence: float     # 0–100
    description: str
    candle_index: int = -1


@dataclass
class IndicatorSet:
    """Complete indicator values for a single bar."""
    rsi: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    atr: float = 0.0
    vwap: float = 0.0
    bb_upper: float = 0.0
    bb_mid: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    ema9: float = 0.0
    ema21: float = 0.0
    ema50: float = 0.0
    ema200: float = 0.0
    volume_sma: float = 0.0
    volume_ratio: float = 1.0  # current / SMA
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    obv: float = 0.0
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    supertrend: float = 0.0
    supertrend_dir: int = 1   # 1 = bullish, -1 = bearish
    # Extended indicators
    adx_plus_di: float = 0.0
    adx_minus_di: float = 0.0
    parabolic_sar: float = 0.0
    parabolic_sar_bull: bool = False
    keltner_upper: float = 0.0
    keltner_lower: float = 0.0
    keltner_mid: float = 0.0
    donchian_upper: float = 0.0
    donchian_lower: float = 0.0
    donchian_mid: float = 0.0
    chandelier_long: float = 0.0
    chandelier_short: float = 0.0
    stoch_rsi_k: float = 50.0
    stoch_rsi_d: float = 50.0
    cci: float = 0.0
    roc: float = 0.0
    williams_r: float = -50.0
    rvol: float = 1.0
    bb_pct_b: float = 0.5
    bb_bandwidth: float = 0.0
    ichimoku_tenkan: float = 0.0
    ichimoku_kijun: float = 0.0
    ichimoku_senkou_a: float = 0.0
    ichimoku_senkou_b: float = 0.0
    ichimoku_chikou: float = 0.0
    pivot_pp: float = 0.0
    pivot_r1: float = 0.0
    pivot_r2: float = 0.0
    pivot_r3: float = 0.0
    pivot_s1: float = 0.0
    pivot_s2: float = 0.0
    pivot_s3: float = 0.0
    vwap_upper_1: float = 0.0
    vwap_lower_1: float = 0.0
    vwap_upper_2: float = 0.0
    vwap_lower_2: float = 0.0
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    hvn_nearest: float = 0.0    # nearest High Volume Node price level
    lvn_nearest: float = 0.0    # nearest Low Volume Node price level
    at_hvn: bool = False         # price within 0.3% of nearest HVN
    at_lvn: bool = False         # price within 0.3% of nearest LVN
    # ── New: missing institutional indicators ─────────────────────────────
    mfi:           float = 50.0  # Money Flow Index (14)
    cmf:           float = 0.0   # Chaikin Money Flow (20)
    anchored_vwap: float = 0.0   # VWAP anchored to today's open
    at_cpr:        bool  = False  # Price near Central Pivot Range
    cpr_top:       float = 0.0   # CPR top (BC)
    cpr_bottom:    float = 0.0   # CPR bottom (TC)
    nr4:           bool  = False  # Narrowest range of last 4 bars
    nr7:           bool  = False  # Narrowest range of last 7 bars
    eqh:           bool  = False  # Equal highs (liquidity above)
    eql:           bool  = False  # Equal lows (liquidity below)
    breadth_score: float = 50.0  # Market internals breadth (0-100)


class TechnicalIndicators:
    """Computes full indicator stack using pandas_ta (or pure-pandas fallback)."""

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add all technical indicators to a candles DataFrame.
        Input: OHLCV DataFrame. Returns augmented DataFrame.
        Uses pandas_ta when available; falls back to pure pandas/numpy otherwise.
        """
        if ta is None:
            # pandas_ta not installed (incompatible Python version) — use built-in fallback
            return self._compute_with_pandas(df)

        df = df.copy()

        try:
            # RSI
            df["rsi"] = ta.rsi(df["close"], length=14)

            # MACD
            macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
            if macd is not None:
                df["macd"] = macd.iloc[:, 0]
                df["macd_hist"] = macd.iloc[:, 1]
                df["macd_signal"] = macd.iloc[:, 2]

            # ATR
            df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

            # Bollinger Bands
            bb = ta.bbands(df["close"], length=20, std=2)
            if bb is not None:
                df["bb_lower"] = bb.iloc[:, 0]
                df["bb_mid"] = bb.iloc[:, 1]
                df["bb_upper"] = bb.iloc[:, 2]
                df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

            # EMAs
            df["ema9"]  = ta.ema(df["close"], length=9)
            df["ema21"] = ta.ema(df["close"], length=21)
            df["ema50"] = ta.ema(df["close"], length=50)
            df["ema200"]= ta.ema(df["close"], length=200)

            # Volume SMA and ratio
            df["volume_sma"] = ta.sma(df["volume"].astype(float), length=20)
            df["volume_ratio"] = df["volume"] / df["volume_sma"].replace(0, np.nan)

            # Stochastic
            stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3, smooth_k=3)
            if stoch is not None:
                df["stoch_k"] = stoch.iloc[:, 0]
                df["stoch_d"] = stoch.iloc[:, 1]

            # OBV
            df["obv"] = ta.obv(df["close"], df["volume"])

            # ADX / DI
            adx = ta.adx(df["high"], df["low"], df["close"], length=14)
            if adx is not None:
                df["adx"]      = adx.iloc[:, 0]
                df["plus_di"]  = adx.iloc[:, 1]
                df["minus_di"] = adx.iloc[:, 2]

            # VWAP (only meaningful for intraday — resets each day)
            try:
                df["vwap"] = self._calculate_vwap(df)
            except Exception:
                df["vwap"] = df["close"]

            # Supertrend
            try:
                st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3)
                if st is not None:
                    df["supertrend"]     = st.iloc[:, 0]
                    df["supertrend_dir"] = st.iloc[:, 3].apply(lambda x: 1 if x > 0 else -1)
            except Exception:
                df["supertrend"] = df["close"]
                df["supertrend_dir"] = 1

            # Extended indicators block
            try:
                self._compute_extended_indicators(df)
            except Exception as ex:
                logger.debug(f"Extended indicator compute error: {ex}")

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Indicator compute error: {e}")

        return df.ffill().infer_objects(copy=False).fillna(0)

    # ── Pure-pandas fallback (used when pandas_ta is not installed) ──────────

    @staticmethod
    def _ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def _sma(series: pd.Series, length: int) -> pd.Series:
        return series.rolling(window=length, min_periods=1).mean()

    @staticmethod
    def _true_range(df: pd.DataFrame) -> pd.Series:
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift(1)).abs()
        lc = (df["low"]  - df["close"].shift(1)).abs()
        return pd.concat([hl, hc, lc], axis=1).max(axis=1)

    def _compute_with_pandas(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Full indicator stack using only pandas/numpy — zero external dependencies.
        Produces the same column names as the pandas_ta path so all downstream code works unchanged.
        """
        df = df.copy()
        if len(df) < 2:
            return df

        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"].astype(float)

        # ── RSI (14) ──────────────────────────────────────────────────────
        delta  = close.diff()
        gain   = delta.clip(lower=0)
        loss   = (-delta).clip(lower=0)
        avg_g  = gain.ewm(span=14, adjust=False).mean()
        avg_l  = loss.ewm(span=14, adjust=False).mean()
        rs     = avg_g / avg_l.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))

        # ── MACD (12, 26, 9) ──────────────────────────────────────────────
        ema12         = self._ema(close, 12)
        ema26         = self._ema(close, 26)
        df["macd"]        = ema12 - ema26
        df["macd_signal"] = self._ema(df["macd"], 9)
        df["macd_hist"]   = df["macd"] - df["macd_signal"]

        # ── ATR (14) ──────────────────────────────────────────────────────
        tr = self._true_range(df)
        df["atr"] = tr.ewm(span=14, adjust=False).mean()

        # ── Bollinger Bands (20, 2) ───────────────────────────────────────
        bb_mid          = close.rolling(20, min_periods=1).mean()
        bb_std          = close.rolling(20, min_periods=1).std(ddof=0)
        df["bb_mid"]    = bb_mid
        df["bb_upper"]  = bb_mid + 2 * bb_std
        df["bb_lower"]  = bb_mid - 2 * bb_std
        df["bb_width"]  = (df["bb_upper"] - df["bb_lower"]) / bb_mid.replace(0, np.nan)

        # ── EMAs ──────────────────────────────────────────────────────────
        df["ema9"]   = self._ema(close, 9)
        df["ema21"]  = self._ema(close, 21)
        df["ema50"]  = self._ema(close, 50)
        df["ema200"] = self._ema(close, 200)

        # ── Volume SMA / ratio ────────────────────────────────────────────
        df["volume_sma"]   = self._sma(volume, 20)
        df["volume_ratio"] = volume / df["volume_sma"].replace(0, np.nan)

        # ── Stochastic (14, 3, 3) ─────────────────────────────────────────
        low14  = low.rolling(14, min_periods=1).min()
        high14 = high.rolling(14, min_periods=1).max()
        denom  = (high14 - low14).replace(0, np.nan)
        raw_k  = 100 * (close - low14) / denom
        df["stoch_k"] = raw_k.rolling(3, min_periods=1).mean()   # smooth %K
        df["stoch_d"] = df["stoch_k"].rolling(3, min_periods=1).mean()

        # ── OBV ───────────────────────────────────────────────────────────
        direction    = np.sign(close.diff().fillna(0))
        df["obv"]    = (direction * volume).cumsum()

        # ── ADX / DI (14) ─────────────────────────────────────────────────
        up_move   = high.diff()
        dn_move   = low.shift(1) - low
        plus_dm   = up_move.where((up_move > dn_move) & (up_move > 0), 0.0)
        minus_dm  = dn_move.where((dn_move > up_move) & (dn_move > 0), 0.0)
        atr14     = tr.ewm(span=14, adjust=False).mean()
        plus_di   = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan))
        minus_di  = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan))
        dx        = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        df["adx"]      = dx.ewm(span=14, adjust=False).mean()
        df["plus_di"]  = plus_di
        df["minus_di"] = minus_di

        # ── VWAP ─────────────────────────────────────────────────────────
        try:
            df["vwap"] = self._calculate_vwap(df)
        except Exception:
            df["vwap"] = close

        # ── Supertrend (10, 3) ────────────────────────────────────────────
        try:
            atr10      = tr.ewm(span=10, adjust=False).mean()
            hl2        = (high + low) / 2
            upper_band = hl2 + 3 * atr10
            lower_band = hl2 - 3 * atr10
            supertrend = close.copy()
            direction_ = pd.Series(1, index=df.index)
            for i in range(1, len(df)):
                if close.iloc[i] > upper_band.iloc[i - 1]:
                    direction_.iloc[i] = 1
                elif close.iloc[i] < lower_band.iloc[i - 1]:
                    direction_.iloc[i] = -1
                else:
                    direction_.iloc[i] = direction_.iloc[i - 1]
                supertrend.iloc[i] = (lower_band.iloc[i] if direction_.iloc[i] == 1
                                      else upper_band.iloc[i])
            df["supertrend"]     = supertrend
            df["supertrend_dir"] = direction_
        except Exception:
            df["supertrend"]     = close
            df["supertrend_dir"] = 1

        # ── Extended indicators (ADX aliases, etc.) ───────────────────────
        df["_adx"]         = df["adx"]
        df["_adx_plus_di"] = df["plus_di"]
        df["_adx_minus_di"]= df["minus_di"]

        try:
            self._compute_extended_indicators(df)
        except Exception as ex:
            logger.debug(f"Extended indicator compute (pandas fallback) error: {ex}")

        return df.ffill().infer_objects(copy=False).fillna(0)

    def _compute_extended_indicators(self, df: pd.DataFrame) -> None:
        """Compute ADX, Parabolic SAR, Keltner, Donchian, Chandelier, StochRSI,
        CCI, ROC, Williams %R, RVOL, BB %B/BW, Ichimoku, Pivots, VWAP bands, POC."""
        if len(df) < 20:
            return

        # True Range (recompute for local use)
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr14 = tr.ewm(span=14, adjust=False).mean()

        # ADX (14)
        up_move = df['high'] - df['high'].shift()
        dn_move = df['low'].shift() - df['low']
        plus_dm = up_move.where((up_move > dn_move) & (up_move > 0), 0.0)
        minus_dm = dn_move.where((dn_move > up_move) & (dn_move > 0), 0.0)
        plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan))
        minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        df['_adx'] = dx.ewm(span=14, adjust=False).mean()
        df['_adx_plus_di'] = plus_di
        df['_adx_minus_di'] = minus_di

        # Parabolic SAR (step=0.02, max=0.20)
        try:
            af = 0.02
            af_step = 0.02
            af_max = 0.20
            closes = df['close'].values
            highs = df['high'].values
            lows = df['low'].values
            n = len(closes)
            sar = np.zeros(n)
            bull = True
            ep = lows[0]
            sar[0] = highs[0]
            for i in range(1, n):
                prev_sar = sar[i - 1]
                if bull:
                    sar[i] = prev_sar + af * (ep - prev_sar)
                    sar[i] = min(sar[i], lows[i - 1], lows[i - 2] if i > 1 else lows[i - 1])
                    if lows[i] < sar[i]:
                        bull = False
                        sar[i] = ep
                        ep = lows[i]
                        af = af_step
                    else:
                        if highs[i] > ep:
                            ep = highs[i]
                            af = min(af + af_step, af_max)
                else:
                    sar[i] = prev_sar + af * (ep - prev_sar)
                    sar[i] = max(sar[i], highs[i - 1], highs[i - 2] if i > 1 else highs[i - 1])
                    if highs[i] > sar[i]:
                        bull = True
                        sar[i] = ep
                        ep = highs[i]
                        af = af_step
                    else:
                        if lows[i] < ep:
                            ep = lows[i]
                            af = min(af + af_step, af_max)
            df['_psar'] = sar
            df['_psar_bull'] = bull
        except Exception:
            df['_psar'] = df['close']
            df['_psar_bull'] = True

        # Keltner Channels (20, 2x ATR)
        kc_mid = df['close'].ewm(span=20).mean()
        kc_atr = tr.rolling(20).mean()
        df['_kc_mid'] = kc_mid
        df['_kc_upper'] = kc_mid + 2 * kc_atr
        df['_kc_lower'] = kc_mid - 2 * kc_atr

        # Donchian Channels (20)
        df['_dc_upper'] = df['high'].rolling(20).max()
        df['_dc_lower'] = df['low'].rolling(20).min()
        df['_dc_mid'] = (df['_dc_upper'] + df['_dc_lower']) / 2

        # Chandelier Exit (22, 3x ATR)
        atr22 = tr.rolling(22).mean()
        df['_chan_long'] = df['high'].rolling(22).max() - 3 * atr22
        df['_chan_short'] = df['low'].rolling(22).min() + 3 * atr22

        # StochRSI (14)
        if 'rsi' in df.columns:
            rsi_s = df['rsi']
            rsi_min = rsi_s.rolling(14).min()
            rsi_max = rsi_s.rolling(14).max()
            stoch_rsi = (rsi_s - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
            df['_srsi_k'] = stoch_rsi.rolling(3).mean() * 100
            df['_srsi_d'] = df['_srsi_k'].rolling(3).mean()

        # CCI (20)
        tp = (df['high'] + df['low'] + df['close']) / 3
        tp_ma = tp.rolling(20).mean()
        tp_md = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
        df['_cci'] = (tp - tp_ma) / (0.015 * tp_md.replace(0, np.nan))

        # ROC (10)
        df['_roc'] = (df['close'] - df['close'].shift(10)) / df['close'].shift(10) * 100

        # Williams %R (14)
        highest14 = df['high'].rolling(14).max()
        lowest14 = df['low'].rolling(14).min()
        df['_willr'] = (highest14 - df['close']) / (highest14 - lowest14).replace(0, np.nan) * -100

        # RVOL
        vol_sma20 = df['volume'].rolling(20).mean()
        df['_rvol'] = df['volume'] / vol_sma20.replace(0, np.nan)

        # Ichimoku (9, 26, 52)
        tenkan = (df['high'].rolling(9).max() + df['low'].rolling(9).min()) / 2
        kijun = (df['high'].rolling(26).max() + df['low'].rolling(26).min()) / 2
        senkou_a = ((tenkan + kijun) / 2).shift(26)
        senkou_b = ((df['high'].rolling(52).max() + df['low'].rolling(52).min()) / 2).shift(26)
        df['_ichi_tenkan'] = tenkan
        df['_ichi_kijun'] = kijun
        df['_ichi_senkou_a'] = senkou_a
        df['_ichi_senkou_b'] = senkou_b

        # VWAP bands (1 and 2 std dev)
        tp2 = (df['high'] + df['low'] + df['close']) / 3
        cum_vol = df['volume'].cumsum()
        cum_tp_vol = (tp2 * df['volume']).cumsum()
        vwap_s = cum_tp_vol / cum_vol.replace(0, np.nan)
        vwap_dev = ((tp2 - vwap_s) ** 2 * df['volume']).cumsum() / cum_vol.replace(0, np.nan)
        vwap_std = np.sqrt(vwap_dev.clip(lower=0))
        df['_vwap_u1'] = vwap_s + vwap_std
        df['_vwap_l1'] = vwap_s - vwap_std
        df['_vwap_u2'] = vwap_s + 2 * vwap_std
        df['_vwap_l2'] = vwap_s - 2 * vwap_std

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate VWAP resetting each trading day (9:15 AM IST).
        VWAP = cumsum(typical_price * volume) / cumsum(volume)
        """
        df = df.copy()
        tp = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"].astype(float)

        # Group by date for daily reset
        if hasattr(df.index, 'date'):
            dates = pd.Series(df.index.date, index=df.index)
            vwap = pd.Series(index=df.index, dtype=float)
            for day in dates.unique():
                mask = dates == day
                day_tp = tp[mask]
                day_vol = vol[mask]
                cum_tpv = (day_tp * day_vol).cumsum()
                cum_vol = day_vol.cumsum()
                vwap[mask] = cum_tpv / cum_vol.replace(0, np.nan)
        else:
            cum_tpv = (tp * vol).cumsum()
            cum_vol = vol.cumsum()
            vwap = cum_tpv / cum_vol.replace(0, np.nan)

        return vwap

    def get_latest_indicators(self, df: pd.DataFrame) -> IndicatorSet:
        """Extract the latest bar's indicator values as IndicatorSet."""
        if df.empty:
            return IndicatorSet()
        row = df.iloc[-1]
        bb_upper = float(row.get("bb_upper", 0))
        bb_mid = float(row.get("bb_mid", 0))
        bb_lower = float(row.get("bb_lower", 0))
        close = float(row.get("close", 0))
        bb_pct_b = (close - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
        bb_bandwidth = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0.0

        # Pivot points from last 2 bars
        ind = IndicatorSet(
            rsi=float(row.get("rsi", 50)),
            macd=float(row.get("macd", 0)),
            macd_signal=float(row.get("macd_signal", 0)),
            macd_hist=float(row.get("macd_hist", 0)),
            atr=float(row.get("atr", 0)),
            vwap=float(row.get("vwap", close)),
            bb_upper=bb_upper,
            bb_mid=bb_mid,
            bb_lower=bb_lower,
            bb_width=float(row.get("bb_width", 0)),
            ema9=float(row.get("ema9", 0)),
            ema21=float(row.get("ema21", 0)),
            ema50=float(row.get("ema50", 0)),
            ema200=float(row.get("ema200", 0)),
            volume_sma=float(row.get("volume_sma", 0)),
            volume_ratio=float(row.get("volume_ratio", 1)),
            stoch_k=float(row.get("stoch_k", 50)),
            stoch_d=float(row.get("stoch_d", 50)),
            obv=float(row.get("obv", 0)),
            adx=float(row.get("adx", 0)),
            plus_di=float(row.get("plus_di", 0)),
            minus_di=float(row.get("minus_di", 0)),
            supertrend=float(row.get("supertrend", close)),
            supertrend_dir=int(row.get("supertrend_dir", 1)),
            # Extended indicators
            adx_plus_di=float(row.get("_adx_plus_di", row.get("plus_di", 0))),
            adx_minus_di=float(row.get("_adx_minus_di", row.get("minus_di", 0))),
            parabolic_sar=float(row.get("_psar", close)),
            parabolic_sar_bull=bool(row.get("_psar_bull", True)),
            keltner_upper=float(row.get("_kc_upper", 0)),
            keltner_lower=float(row.get("_kc_lower", 0)),
            keltner_mid=float(row.get("_kc_mid", 0)),
            donchian_upper=float(row.get("_dc_upper", 0)),
            donchian_lower=float(row.get("_dc_lower", 0)),
            donchian_mid=float(row.get("_dc_mid", 0)),
            chandelier_long=float(row.get("_chan_long", 0)),
            chandelier_short=float(row.get("_chan_short", 0)),
            stoch_rsi_k=float(row.get("_srsi_k", 50) or 50),
            stoch_rsi_d=float(row.get("_srsi_d", 50) or 50),
            cci=float(row.get("_cci", 0) or 0),
            roc=float(row.get("_roc", 0) or 0),
            williams_r=float(row.get("_willr", -50) or -50),
            rvol=float(row.get("_rvol", 1) or 1),
            bb_pct_b=bb_pct_b,
            bb_bandwidth=bb_bandwidth,
            ichimoku_tenkan=float(row.get("_ichi_tenkan", 0) or 0),
            ichimoku_kijun=float(row.get("_ichi_kijun", 0) or 0),
            ichimoku_senkou_a=float(row.get("_ichi_senkou_a", 0) or 0),
            ichimoku_senkou_b=float(row.get("_ichi_senkou_b", 0) or 0),
            ichimoku_chikou=float(df["close"].iloc[-26] if len(df) > 26 else 0),
            vwap_upper_1=float(row.get("_vwap_u1", 0) or 0),
            vwap_lower_1=float(row.get("_vwap_l1", 0) or 0),
            vwap_upper_2=float(row.get("_vwap_u2", 0) or 0),
            vwap_lower_2=float(row.get("_vwap_l2", 0) or 0),
        )

        # Pivot points (use second-to-last bar as prior session proxy)
        if len(df) > 1:
            ph = float(df["high"].iloc[-2])
            pl = float(df["low"].iloc[-2])
            pc = float(df["close"].iloc[-2])
        else:
            ph = float(df["high"].iloc[-1])
            pl = float(df["low"].iloc[-1])
            pc = float(df["close"].iloc[-1])
        pp = (ph + pl + pc) / 3
        ind.pivot_pp = pp
        ind.pivot_r1 = 2 * pp - pl
        ind.pivot_r2 = pp + (ph - pl)
        ind.pivot_r3 = ph + 2 * (pp - pl)
        ind.pivot_s1 = 2 * pp - ph
        ind.pivot_s2 = pp - (ph - pl)
        ind.pivot_s3 = pl - 2 * (ph - pp)

        # Volume Profile: POC / VAH / VAL / HVN / LVN
        try:
            price_range = df['high'].max() - df['low'].min()
            n_buckets = 20
            bucket_size = price_range / n_buckets if price_range > 0 else 1
            low_min = float(df['low'].min())
            buckets: dict = {}
            for _, r in df.iterrows():
                mid = (float(r['high']) + float(r['low'])) / 2
                bucket = int((mid - low_min) / bucket_size)
                buckets[bucket] = buckets.get(bucket, 0) + float(r['volume'])

            if buckets:
                # POC
                poc_bucket = max(buckets, key=buckets.get)  # type: ignore[arg-type]
                ind.poc = float(low_min + (poc_bucket + 0.5) * bucket_size)

                # VAH / VAL (70% of volume around POC)
                total_vol = sum(buckets.values())
                sorted_buckets = sorted(buckets.items(), key=lambda x: x[1], reverse=True)
                cum = 0
                va_buckets: list = []
                for b, v in sorted_buckets:
                    cum += v
                    va_buckets.append(b)
                    if cum >= total_vol * 0.70:
                        break
                if va_buckets:
                    ind.vah = float(low_min + (max(va_buckets) + 1) * bucket_size)
                    ind.val = float(low_min + min(va_buckets) * bucket_size)

                # HVN / LVN: classify each bucket relative to mean volume
                mean_vol = total_vol / max(len(buckets), 1)
                hvn_levels = [
                    low_min + (b + 0.5) * bucket_size
                    for b, v in buckets.items()
                    if v >= mean_vol * 1.5   # High Volume Node
                ]
                lvn_levels = [
                    low_min + (b + 0.5) * bucket_size
                    for b, v in buckets.items()
                    if v <= mean_vol * 0.5   # Low Volume Node
                ]
                current_price = float(df['close'].iloc[-1])
                if hvn_levels:
                    nearest_hvn = min(hvn_levels, key=lambda p: abs(p - current_price))
                    ind.hvn_nearest = float(nearest_hvn)
                    ind.at_hvn = abs(nearest_hvn - current_price) / max(current_price, 1) < 0.003
                if lvn_levels:
                    nearest_lvn = min(lvn_levels, key=lambda p: abs(p - current_price))
                    ind.lvn_nearest = float(nearest_lvn)
                    ind.at_lvn = abs(nearest_lvn - current_price) / max(current_price, 1) < 0.003
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

        # ── Money Flow Index (MFI 14) ─────────────────────────────────────
        try:
            if len(df) >= 15 and "high" in df.columns and "volume" in df.columns:
                h = df["high"].values
                l = df["low"].values
                c = df["close"].values
                v = df["volume"].values.astype(float)
                tp = (h + l + c) / 3
                rmf = tp * v
                pos_mf = neg_mf = 0.0
                for i in range(1, 15):
                    idx = len(tp) - 14 + i - 1
                    if idx > 0:
                        if tp[idx] > tp[idx - 1]:
                            pos_mf += rmf[idx]
                        elif tp[idx] < tp[idx - 1]:
                            neg_mf += rmf[idx]
                if neg_mf > 0:
                    ind.mfi = 100 - 100 / (1 + pos_mf / neg_mf)
                else:
                    ind.mfi = 100.0
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

        # ── Chaikin Money Flow (CMF 20) ───────────────────────────────────
        try:
            if len(df) >= 20 and "volume" in df.columns:
                h = df["high"].values[-20:]
                l = df["low"].values[-20:]
                c = df["close"].values[-20:]
                v = df["volume"].values[-20:].astype(float)
                hl = h - l
                hl_safe = np.where(hl > 0, hl, 1.0)  # avoid div-by-zero warning
                clv = np.where(hl > 0, ((c - l) - (h - c)) / hl_safe, 0.0)
                ind.cmf = float((clv * v).sum() / max(v.sum(), 1))
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

        # ── Anchored VWAP (to today's open bar) ──────────────────────────
        try:
            if len(df) >= 2 and "volume" in df.columns:
                v = df["volume"].values.astype(float)
                tp = (df["high"].values + df["low"].values + df["close"].values) / 3
                ind.anchored_vwap = float((tp * v).sum() / max(v.sum(), 1))
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

        # ── Central Pivot Range (CPR) ─────────────────────────────────────
        try:
            if len(df) >= 2:
                ph = float(df["high"].iloc[-2])
                pl = float(df["low"].iloc[-2])
                pc = float(df["close"].iloc[-2])
                pp  = (ph + pl + pc) / 3
                bc  = (ph + pl) / 2        # Bottom Central
                tc  = (pp - bc) + pp       # Top Central = mirror of BC around PP
                ind.cpr_top    = round(max(bc, tc), 4)
                ind.cpr_bottom = round(min(bc, tc), 4)
                ind.at_cpr = (ind.cpr_bottom * 0.998 <= current_price
                               <= ind.cpr_top * 1.002)
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

        # ── NR4 / NR7 (Narrow Range setups — volatility contraction) ─────
        try:
            if len(df) >= 7:
                ranges = (df["high"] - df["low"]).values
                cur_range = ranges[-1]
                ind.nr4 = bool(cur_range == min(ranges[-4:]))
                ind.nr7 = bool(cur_range == min(ranges[-7:]))
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

        # ── Equal Highs / Equal Lows (liquidity pools) ───────────────────
        try:
            if len(df) >= 10:
                highs = df["high"].values[-10:]
                lows  = df["low"].values[-10:]
                tol   = current_price * 0.001   # 0.1% tolerance
                top_h = highs[-1]
                ind.eqh = sum(1 for h in highs[:-1] if abs(h - top_h) <= tol) >= 2
                bot_l = lows[-1]
                ind.eql = sum(1 for lo in lows[:-1] if abs(lo - bot_l) <= tol) >= 2
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

        # ── Market internals breadth score ───────────────────────────────
        try:
            from market_internals import get_market_internals
            ind.breadth_score = get_market_internals().get_breadth()["breadth_score"]
        except Exception:
            ind.breadth_score = 50.0

        return ind


class PatternRecognizer:
    """
    Detects 20+ candlestick and chart patterns.
    Each detector returns a PatternResult with confidence 0–100.
    """

    def __init__(self):
        self.indicators = TechnicalIndicators()

    def analyze(self, df: pd.DataFrame) -> Dict:
        """
        Run full pattern analysis on a DataFrame.
        Returns dict with all detected patterns and indicator values.
        """
        if df is None or len(df) < 50:
            return {"patterns": [], "indicators": IndicatorSet(), "score": 0}

        # Add indicators
        df = self.indicators.compute(df)
        ind = self.indicators.get_latest_indicators(df)

        # Detect all patterns
        patterns = []
        # Standard detectors (df, ind) signature
        detectors = [
            # ── Candlestick reversals ──────────────────────────────────────
            self.detect_bullish_engulfing,
            self.detect_bearish_engulfing,
            self.detect_hammer,
            self.detect_shooting_star,
            self.detect_doji,
            self.detect_morning_star,
            self.detect_evening_star,
            self.detect_three_white_soldiers,
            self.detect_three_black_crows,
            self.detect_bullish_harami,
            self.detect_bearish_harami,
            self.detect_piercing_line,
            self.detect_dark_cloud_cover,
            # ── Chart patterns ────────────────────────────────────────────
            self.detect_breakout,
            self.detect_breakdown,
            self.detect_flag_pattern,
            self.detect_pennant_pattern,
            self.detect_bb_squeeze_breakout,
            self.detect_orb_breakout,
            # ── Indicator crossovers ──────────────────────────────────────
            self.detect_vwap_bounce,
            self.detect_vwap_breakdown,
            self.detect_volume_surge_breakout,
            self.detect_rsi_divergence,
            self.detect_macd_crossover,
            self.detect_ema_crossover,
            self.detect_supertrend_signal,
            # ── Elite 18-year patterns (Smart Money / NSE-specific) ───────
            self.detect_fair_value_gap,
            self.detect_order_block,
            self.detect_heikin_ashi_trend,
            self.detect_inside_bar,
            self.detect_double_bottom_top,
            self.detect_market_structure_break,
            self.detect_ema_stack,
            # ── Advanced chart patterns ───────────────────────────────────
            self.detect_cup_and_handle,
            self.detect_inverse_head_shoulders,
            self.detect_head_and_shoulders,
            self.detect_rising_wedge,
            self.detect_falling_wedge,
            self.detect_ascending_triangle,
            self.detect_descending_triangle,
            self.detect_tweezer_tops,
            self.detect_tweezer_bottoms,
            self.detect_three_inside_up,
            self.detect_three_inside_down,
            self.detect_kicker_pattern,
            self.detect_abandoned_baby,
            self.detect_symmetrical_triangle,
            # ── Professional candlestick continuations ────────────────────
            self.detect_belt_hold,
            self.detect_rising_three_methods,
            self.detect_falling_three_methods,
            self.detect_upside_tasuki_gap,
            self.detect_downside_tasuki_gap,
            self.detect_on_neck,
            self.detect_mat_hold,
            self.detect_two_crows,
            self.detect_homing_pigeon,
            self.detect_matching_low,
            self.detect_counterattack_lines,
            # ── New candlestick patterns ──────────────────────────────────
            self.detect_inverted_hammer,
            self.detect_hanging_man,
            self.detect_marubozu_bullish,
            self.detect_marubozu_bearish,
            self.detect_spinning_top,
            self.detect_dragonfly_doji,
            self.detect_gravestone_doji,
            # ── New chart patterns ────────────────────────────────────────
            self.detect_rectangle_pattern,
            self.detect_triple_top,
            self.detect_triple_bottom,
            self.detect_quasimodo,
            # ── New ICT / Smart Money patterns ────────────────────────────
            self.detect_breaker_block,
            self.detect_liquidity_pool,
            self.detect_kill_zone,
            self.detect_judas_swing,
            self.detect_premium_discount,
            self.detect_fibonacci_retracement,
            # ── New indicator-based signals ───────────────────────────────
            self.detect_adx_trend,
            self.detect_ichimoku_signal,
            self.detect_keltner_squeeze,
            self.detect_donchian_breakout,
            self.detect_chandelier_exit_signal,
            self.detect_stoch_rsi_signal,
            self.detect_cci_signal,
            self.detect_williams_r_signal,
            self.detect_pivot_bounce,
            self.detect_vwap_band_signal,
            self.detect_poc_reaction,
            self.detect_rvol_confirmation,
            # ── Gap strategies ────────────────────────────────────────────
            self.detect_gap_and_go,
            self.detect_gap_fill,
            # ── SMT Divergence (Smart Money Technique) ────────────────────
            self.detect_smt_divergence,
            # ── New high-accuracy patterns ────────────────────────────────
            self.detect_supply_demand_zone,
            self.detect_quasimodo_bullish,
            self.detect_volume_climax_reversal,
            self.detect_broadening_formation,
            self.detect_mitigation_block,
            self.detect_rounding_bottom,
        ]

        for detector in detectors:
            try:
                result = detector(df, ind)
                if result and result.confidence >= 40:
                    patterns.append(result)
            except Exception as e:
                logger.debug(f"Pattern detector {detector.__name__} error: {e}")

        # Harmonic patterns (Gartley, Butterfly, Bat, Crab, ABCD, OTE, Three Drives)
        for p in self._run_harmonic_patterns(df):
            if p.confidence >= 55:
                patterns.append(p)

        # Institutional / Smart Money patterns (Wyckoff, ICT, Liquidity Sweep, etc.)
        for p in self._run_smart_money_advanced(df):
            if p.confidence >= 58:
                patterns.append(p)

        # Compute composite score
        score = self._compute_composite_score(patterns, ind)

        return {
            "patterns": patterns,
            "indicators": ind,
            "score": score,
            "long_patterns": [p for p in patterns if p.direction == "LONG"],
            "short_patterns": [p for p in patterns if p.direction == "SHORT"],
        }

    # --------------------------------------------------------
    # CANDLESTICK PATTERNS
    # --------------------------------------------------------

    def detect_bullish_engulfing(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        prev_bearish = prev["close"] < prev["open"]
        curr_bullish = curr["close"] > curr["open"]
        engulfs = curr["open"] < prev["close"] and curr["close"] > prev["open"]
        if not (prev_bearish and curr_bullish and engulfs):
            return None
        body_ratio = abs(curr["close"] - curr["open"]) / max(abs(prev["close"] - prev["open"]), 0.01)
        confidence = min(50 + body_ratio * 10 + (ind.volume_ratio * 5), 90)
        # Stronger in oversold
        if ind.rsi < 40:
            confidence = min(confidence + 10, 95)
        return PatternResult("Bullish Engulfing", "LONG", confidence,
                             f"Bullish engulfing with {body_ratio:.1f}x body ratio")

    def detect_bearish_engulfing(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        prev_bullish = prev["close"] > prev["open"]
        curr_bearish = curr["close"] < curr["open"]
        engulfs = curr["open"] > prev["close"] and curr["close"] < prev["open"]
        if not (prev_bullish and curr_bearish and engulfs):
            return None
        body_ratio = abs(curr["close"] - curr["open"]) / max(abs(prev["close"] - prev["open"]), 0.01)
        confidence = min(50 + body_ratio * 10 + (ind.volume_ratio * 5), 90)
        if ind.rsi > 60:
            confidence = min(confidence + 10, 95)
        return PatternResult("Bearish Engulfing", "SHORT", confidence,
                             f"Bearish engulfing with {body_ratio:.1f}x body ratio")

    def detect_hammer(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        upper_shadow = c["high"] - max(c["close"], c["open"])
        if body / total_range > 0.35:
            return None
        if lower_shadow < 2 * body:
            return None
        if upper_shadow > body:
            return None
        confidence = 65 + (lower_shadow / total_range) * 20
        if ind.rsi < 40:
            confidence = min(confidence + 10, 92)
        return PatternResult("Hammer", "LONG", confidence,
                             f"Hammer — strong rejection at lows, RSI={ind.rsi:.0f}")

    def detect_shooting_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        upper_shadow = c["high"] - max(c["close"], c["open"])
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        if body / total_range > 0.35:
            return None
        if upper_shadow < 2 * body:
            return None
        if lower_shadow > body:
            return None
        confidence = 65 + (upper_shadow / total_range) * 20
        if ind.rsi > 60:
            confidence = min(confidence + 10, 92)
        return PatternResult("Shooting Star", "SHORT", confidence,
                             f"Shooting Star — rejection at highs, RSI={ind.rsi:.0f}")

    def detect_doji(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        if body / total_range > 0.1:
            return None
        return PatternResult("Doji", "NEUTRAL", 60,
                             "Doji — market indecision, watch for breakout")

    def detect_morning_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] < c1["open"] and
                abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3 and
                c3["close"] > c3["open"] and
                c3["close"] > (c1["open"] + c1["close"]) / 2):
            confidence = 75 + (ind.volume_ratio - 1) * 5
            return PatternResult("Morning Star", "LONG", min(confidence, 92),
                                 "3-candle morning star reversal at bottom")
        return None

    def detect_evening_star(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] > c1["open"] and
                abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3 and
                c3["close"] < c3["open"] and
                c3["close"] < (c1["open"] + c1["close"]) / 2):
            confidence = 75 + (ind.volume_ratio - 1) * 5
            return PatternResult("Evening Star", "SHORT", min(confidence, 92),
                                 "3-candle evening star reversal at top")
        return None

    def detect_three_white_soldiers(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] > c1["open"] and c2["close"] > c2["open"] and c3["close"] > c3["open"] and
                c2["open"] > c1["open"] and c3["open"] > c2["open"] and
                c2["close"] > c1["close"] and c3["close"] > c2["close"]):
            return PatternResult("Three White Soldiers", "LONG", 80,
                                 "Strong 3-candle bullish momentum continuation")
        return None

    def detect_three_black_crows(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if (c1["close"] < c1["open"] and c2["close"] < c2["open"] and c3["close"] < c3["open"] and
                c2["open"] < c1["open"] and c3["open"] < c2["open"] and
                c2["close"] < c1["close"] and c3["close"] < c2["close"]):
            return PatternResult("Three Black Crows", "SHORT", 80,
                                 "Strong 3-candle bearish momentum continuation")
        return None

    def detect_bullish_harami(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        if (prev["close"] < prev["open"] and
                curr["close"] > curr["open"] and
                curr["open"] > prev["close"] and curr["close"] < prev["open"]):
            return PatternResult("Bullish Harami", "LONG", 62,
                                 "Bullish harami — potential reversal inside prior bearish candle")
        return None

    def detect_bearish_harami(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        if (prev["close"] > prev["open"] and
                curr["close"] < curr["open"] and
                curr["open"] < prev["close"] and curr["close"] > prev["open"]):
            return PatternResult("Bearish Harami", "SHORT", 62,
                                 "Bearish harami — potential reversal inside prior bullish candle")
        return None

    def detect_piercing_line(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        mid_prev = (prev["open"] + prev["close"]) / 2
        if (prev["close"] < prev["open"] and
                curr["close"] > curr["open"] and
                curr["open"] < prev["close"] and
                curr["close"] > mid_prev and curr["close"] < prev["open"]):
            return PatternResult("Piercing Line", "LONG", 68,
                                 "Piercing line — bullish reversal, price penetrates >50% of prior bearish bar")
        return None

    def detect_dark_cloud_cover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]
        mid_prev = (prev["open"] + prev["close"]) / 2
        if (prev["close"] > prev["open"] and
                curr["close"] < curr["open"] and
                curr["open"] > prev["close"] and
                curr["close"] < mid_prev and curr["close"] > prev["open"]):
            return PatternResult("Dark Cloud Cover", "SHORT", 68,
                                 "Dark cloud cover — bearish reversal, penetrates >50% of prior bullish bar")
        return None

    # --------------------------------------------------------
    # CHART PATTERNS
    # --------------------------------------------------------

    def detect_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Breakout above recent resistance with volume."""
        if len(df) < 20:
            return None
        recent_high = df["high"].iloc[-20:-1].max()
        curr_close = df["close"].iloc[-1]
        if curr_close > recent_high * 1.001 and ind.volume_ratio >= 1.5:
            breakout_pct = ((curr_close - recent_high) / recent_high) * 100
            confidence = min(60 + breakout_pct * 10 + (ind.volume_ratio - 1) * 15, 92)
            return PatternResult("Resistance Breakout", "LONG", confidence,
                                 f"Breakout above ${recent_high:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    def detect_breakdown(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Breakdown below recent support with volume."""
        if len(df) < 20:
            return None
        recent_low = df["low"].iloc[-20:-1].min()
        curr_close = df["close"].iloc[-1]
        if curr_close < recent_low * 0.999 and ind.volume_ratio >= 1.5:
            breakdown_pct = ((recent_low - curr_close) / recent_low) * 100
            confidence = min(60 + breakdown_pct * 10 + (ind.volume_ratio - 1) * 15, 92)
            return PatternResult("Support Breakdown", "SHORT", confidence,
                                 f"Breakdown below ${recent_low:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    def detect_flag_pattern(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Bull/bear flag: sharp move then tight consolidation."""
        if len(df) < 15:
            return None
        # Strong move in last 5 bars
        move = df["close"].iloc[-10] - df["close"].iloc[-15]
        atr = ind.atr if ind.atr > 0 else 1
        if abs(move) < 3 * atr:
            return None
        # Tight range in last 5 bars (flag)
        recent = df.iloc[-5:]
        flag_range = recent["high"].max() - recent["low"].min()
        if flag_range > 1.5 * atr:
            return None
        if move > 0:
            return PatternResult("Bull Flag", "LONG", 72,
                                 f"Bull flag: {move:.2f} pole, tight {flag_range:.2f} consolidation")
        else:
            return PatternResult("Bear Flag", "SHORT", 72,
                                 f"Bear flag: {abs(move):.2f} pole, tight {flag_range:.2f} consolidation")

    def detect_pennant_pattern(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Pennant: sharp move then converging highs/lows."""
        if len(df) < 15:
            return None
        # Strong initial move
        move = df["close"].iloc[-10] - df["close"].iloc[-15]
        atr = ind.atr if ind.atr > 0 else 1
        if abs(move) < 3 * atr:
            return None
        # Converging in last 5 bars
        recent = df.iloc[-5:]
        highs = recent["high"].values
        lows = recent["low"].values
        if len(highs) < 3:
            return None
        high_slope = np.polyfit(range(len(highs)), highs, 1)[0]
        low_slope = np.polyfit(range(len(lows)), lows, 1)[0]
        if move > 0 and high_slope < 0 and low_slope > 0:
            return PatternResult("Bull Pennant", "LONG", 74,
                                 "Bull pennant — converging consolidation after strong move up")
        elif move < 0 and high_slope < 0 and low_slope > 0:
            return PatternResult("Bear Pennant", "SHORT", 74,
                                 "Bear pennant — converging consolidation after strong move down")
        return None

    def detect_vwap_bounce(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Price bouncing off VWAP from below (bullish)."""
        if ind.vwap == 0 or len(df) < 3:
            return None
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        # Was below VWAP, now crossing above
        if (prev["low"] <= ind.vwap and curr["close"] > ind.vwap * 1.001 and
                curr["close"] > curr["open"]):
            dev_pct = ((curr["close"] - ind.vwap) / ind.vwap) * 100
            if dev_pct < 0.5:  # Not too far above VWAP yet
                confidence = 70 + (ind.volume_ratio - 1) * 10
                return PatternResult("VWAP Bounce", "LONG", min(confidence, 85),
                                     f"Price bouncing above VWAP ${ind.vwap:.2f}")
        return None

    def detect_vwap_breakdown(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Price breaking below VWAP (bearish)."""
        if ind.vwap == 0 or len(df) < 3:
            return None
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        if (prev["high"] >= ind.vwap and curr["close"] < ind.vwap * 0.999 and
                curr["close"] < curr["open"]):
            confidence = 70 + (ind.volume_ratio - 1) * 10
            return PatternResult("VWAP Breakdown", "SHORT", min(confidence, 85),
                                 f"Price broke below VWAP ${ind.vwap:.2f}")
        return None

    def detect_volume_surge_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Strong directional move with 2x+ volume surge."""
        if ind.volume_ratio < 2.0 or len(df) < 5:
            return None
        curr = df.iloc[-1]
        body = abs(curr["close"] - curr["open"])
        atr = ind.atr if ind.atr > 0 else 1
        if body < 0.8 * atr:
            return None
        confidence = min(55 + (ind.volume_ratio - 2) * 15 + (body / atr) * 5, 90)
        direction = "LONG" if curr["close"] > curr["open"] else "SHORT"
        return PatternResult("Volume Surge", direction, confidence,
                             f"{ind.volume_ratio:.1f}x volume surge with {body/atr:.1f}x ATR move")

    def detect_rsi_divergence(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """RSI divergence: price makes new high/low but RSI doesn't."""
        if len(df) < 20 or "rsi" not in df.columns:
            return None
        # Bullish divergence: price lower low, RSI higher low
        prices = df["close"].iloc[-20:]
        rsiv = df["rsi"].iloc[-20:]
        if rsiv.isna().all():
            return None
        # Find recent swing lows
        price_min_idx = prices.idxmin()
        prev_prices = df["close"].iloc[-40:-20] if len(df) >= 40 else None
        if prev_prices is not None and not prev_prices.empty:
            prev_min = prev_prices.min()
            curr_min = prices.min()
            prev_rsi = df["rsi"].iloc[-40:-20].min() if len(df) >= 40 else 50
            curr_rsi = rsiv.min()
            if curr_min < prev_min and curr_rsi > prev_rsi and ind.rsi < 45:
                return PatternResult("Bullish RSI Divergence", "LONG", 75,
                                     f"Bullish divergence: price lower low, RSI higher low ({ind.rsi:.0f})")
            if curr_min > prev_min and curr_rsi < prev_rsi and ind.rsi > 55:
                return PatternResult("Bearish RSI Divergence", "SHORT", 75,
                                     f"Bearish divergence: price higher high, RSI lower high ({ind.rsi:.0f})")
        return None

    def detect_macd_crossover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """MACD line crossing signal line."""
        if len(df) < 3 or "macd" not in df.columns:
            return None
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_diff = prev.get("macd", 0) - prev.get("macd_signal", 0)
        curr_diff = curr.get("macd", 0) - curr.get("macd_signal", 0)
        if prev_diff < 0 and curr_diff > 0:
            hist_strength = abs(ind.macd_hist)
            confidence = min(62 + hist_strength * 2, 85)
            return PatternResult("MACD Bullish Crossover", "LONG", confidence,
                                 f"MACD crossed above signal, hist={ind.macd_hist:.4f}")
        if prev_diff > 0 and curr_diff < 0:
            hist_strength = abs(ind.macd_hist)
            confidence = min(62 + hist_strength * 2, 85)
            return PatternResult("MACD Bearish Crossover", "SHORT", confidence,
                                 f"MACD crossed below signal, hist={ind.macd_hist:.4f}")
        return None

    def detect_bb_squeeze_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Bollinger Band squeeze followed by expansion breakout."""
        if len(df) < 25 or "bb_width" not in df.columns:
            return None
        # Squeeze: BB width at recent minimum
        recent_width = df["bb_width"].iloc[-25:]
        if recent_width.isna().all():
            return None
        curr_width = ind.bb_width
        avg_width = recent_width.mean()
        # Was squeezed, now expanding
        if curr_width > avg_width * 1.2:
            prev_width = df["bb_width"].iloc[-5:-1].mean()
            if prev_width < avg_width * 0.8:  # Was in squeeze
                curr = df.iloc[-1]
                if curr["close"] > ind.bb_upper:
                    return PatternResult("BB Squeeze Breakout Long", "LONG", 78,
                                        f"Bollinger Band squeeze explosion upward")
                elif curr["close"] < ind.bb_lower:
                    return PatternResult("BB Squeeze Breakout Short", "SHORT", 78,
                                        f"Bollinger Band squeeze explosion downward")
        return None

    def detect_ema_crossover(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """EMA9 crossing EMA21 (fast momentum signal)."""
        if len(df) < 3 or "ema9" not in df.columns:
            return None
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_diff = prev.get("ema9", 0) - prev.get("ema21", 0)
        curr_diff = curr.get("ema9", 0) - curr.get("ema21", 0)
        close = curr["close"]
        if prev_diff < 0 and curr_diff > 0 and close > ind.ema50:
            return PatternResult("EMA9 x EMA21 Bullish", "LONG", 68,
                                 f"EMA9 crossed above EMA21, price above EMA50")
        if prev_diff > 0 and curr_diff < 0 and close < ind.ema50:
            return PatternResult("EMA9 x EMA21 Bearish", "SHORT", 68,
                                 f"EMA9 crossed below EMA21, price below EMA50")
        return None

    def detect_supertrend_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Supertrend direction change signal."""
        if len(df) < 3 or "supertrend_dir" not in df.columns:
            return None
        prev_dir = int(df["supertrend_dir"].iloc[-2])
        curr_dir = int(df["supertrend_dir"].iloc[-1])
        if prev_dir == -1 and curr_dir == 1:
            return PatternResult("Supertrend Flip Bullish", "LONG", 76,
                                 f"Supertrend flipped bullish — trend change confirmed")
        if prev_dir == 1 and curr_dir == -1:
            return PatternResult("Supertrend Flip Bearish", "SHORT", 76,
                                 f"Supertrend flipped bearish — trend change confirmed")
        return None

    def detect_orb_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """Opening Range Breakout (9:15–9:30 AM IST)."""
        if len(df) < 4:
            return None
        # First 3 candles form the ORB
        orb_candles = df.iloc[:3]
        orb_high = orb_candles["high"].max()
        orb_low = orb_candles["low"].min()
        curr = df.iloc[-1]
        if curr["close"] > orb_high * 1.001 and ind.volume_ratio >= 1.5:
            return PatternResult("ORB Bullish Breakout", "LONG", 80,
                                 f"ORB breakout above ${orb_high:.2f} with {ind.volume_ratio:.1f}x volume")
        if curr["close"] < orb_low * 0.999 and ind.volume_ratio >= 1.5:
            return PatternResult("ORB Bearish Breakdown", "SHORT", 80,
                                 f"ORB breakdown below ${orb_low:.2f} with {ind.volume_ratio:.1f}x volume")
        return None

    # --------------------------------------------------------
    # ELITE PATTERNS  (18-year NSE professional stack)
    # --------------------------------------------------------

    def detect_fair_value_gap(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Fair Value Gap (FVG) / Imbalance — ICT / Smart Money concept.
        Gap = candle 3's low > candle 1's high (bullish) or
              candle 3's high < candle 1's low (bearish).
        Price will often return to fill the imbalance before continuing.
        High-probability entry when price re-enters the gap with volume.
        """
        if len(df) < 4:
            return None
        c1, c2, c3 = df.iloc[-4], df.iloc[-3], df.iloc[-2]
        curr        = df.iloc[-1]

        # Bullish FVG: candle 3 low > candle 1 high (upside imbalance)
        if c3["low"] > c1["high"]:
            gap_size = c3["low"] - c1["high"]
            atr = ind.atr if ind.atr > 0 else 1
            if gap_size < 0.3 * atr:       # Too small — noise
                return None
            # Best entry: price pulling back INTO the gap from above
            in_gap = c1["high"] <= curr["close"] <= c3["low"]
            confidence = 75 if in_gap else 60
            if ind.ema9 > ind.ema21 and ind.supertrend_dir == 1:
                confidence = min(confidence + 10, 90)
            return PatternResult("Bullish FVG", "LONG", confidence,
                                 f"Bullish Fair Value Gap: ${c1['high']:.2f}–${c3['low']:.2f} "
                                 f"({'price in gap' if in_gap else 'approaching gap'})")

        # Bearish FVG: candle 3 high < candle 1 low (downside imbalance)
        if c3["high"] < c1["low"]:
            gap_size = c1["low"] - c3["high"]
            atr = ind.atr if ind.atr > 0 else 1
            if gap_size < 0.3 * atr:
                return None
            in_gap = c3["high"] <= curr["close"] <= c1["low"]
            confidence = 75 if in_gap else 60
            if ind.ema9 < ind.ema21 and ind.supertrend_dir == -1:
                confidence = min(confidence + 10, 90)
            return PatternResult("Bearish FVG", "SHORT", confidence,
                                 f"Bearish Fair Value Gap: ${c3['high']:.2f}–${c1['low']:.2f} "
                                 f"({'price in gap' if in_gap else 'approaching gap'})")
        return None

    def detect_order_block(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Order Block (OB) — Institutional accumulation/distribution zones.
        Bullish OB: the last bearish candle BEFORE a strong bullish impulse.
        Bearish OB: the last bullish candle BEFORE a strong bearish impulse.
        These zones act as high-probability support/resistance areas.
        18yr rule: "Institutions leave their footprint in order blocks."
        """
        if len(df) < 10:
            return None
        atr = ind.atr if ind.atr > 0 else 1

        # Scan for bullish OB: last bearish candle before 3+ candle bull move
        for i in range(-6, -2):
            ob_candle = df.iloc[i]
            if ob_candle["close"] >= ob_candle["open"]:
                continue   # Not bearish
            # Check if followed by 3 consecutive bullish candles
            subsequent = df.iloc[i+1:i+4]
            if len(subsequent) < 3:
                continue
            if not all(subsequent["close"] > subsequent["open"]):
                continue
            # Strong impulse: total move > 1.5 ATR
            impulse = subsequent["close"].iloc[-1] - ob_candle["low"]
            if impulse < 1.5 * atr:
                continue
            # Price currently returning to OB zone
            ob_high = ob_candle["high"]
            ob_low  = ob_candle["low"]
            curr_close = df.iloc[-1]["close"]
            if ob_low * 0.997 <= curr_close <= ob_high * 1.003:
                confidence = 80 + min((ind.volume_ratio - 1) * 5, 10)
                return PatternResult("Bullish Order Block", "LONG", min(confidence, 92),
                                     f"Institutional OB zone ${ob_low:.2f}–${ob_high:.2f} "
                                     f"— price returning to buy zone")

        # Scan for bearish OB: last bullish candle before 3+ candle bear move
        for i in range(-6, -2):
            ob_candle = df.iloc[i]
            if ob_candle["close"] <= ob_candle["open"]:
                continue
            subsequent = df.iloc[i+1:i+4]
            if len(subsequent) < 3:
                continue
            if not all(subsequent["close"] < subsequent["open"]):
                continue
            impulse = ob_candle["high"] - subsequent["close"].iloc[-1]
            if impulse < 1.5 * atr:
                continue
            ob_high = ob_candle["high"]
            ob_low  = ob_candle["low"]
            curr_close = df.iloc[-1]["close"]
            if ob_low * 0.997 <= curr_close <= ob_high * 1.003:
                confidence = 80 + min((ind.volume_ratio - 1) * 5, 10)
                return PatternResult("Bearish Order Block", "SHORT", min(confidence, 92),
                                     f"Institutional OB zone ${ob_low:.2f}–${ob_high:.2f} "
                                     f"— price returning to sell zone")
        return None

    def detect_heikin_ashi_trend(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Heikin Ashi trend signal — smoothed candles remove noise.
        Rules:
          Strong bull: 3+ consecutive HA bullish candles with no lower shadows
          Strong bear: 3+ consecutive HA bearish candles with no upper shadows
        18yr rule: "HA candles tell you the trend clearly — no guessing."
        """
        if len(df) < 5:
            return None

        # Calculate last 5 HA candles
        ha = pd.DataFrame(index=df.index[-5:])
        raw = df.iloc[-5:].copy()
        ha["close"] = (raw["open"] + raw["high"] + raw["low"] + raw["close"]) / 4
        ha_open = pd.Series(dtype=float, index=raw.index)
        ha_open.iloc[0] = (raw["open"].iloc[0] + raw["close"].iloc[0]) / 2
        for j in range(1, len(raw)):
            ha_open.iloc[j] = (ha_open.iloc[j-1] + ha["close"].iloc[j-1]) / 2
        ha["open"]  = ha_open
        ha["high"]  = pd.concat([raw["high"], ha["open"], ha["close"]], axis=1).max(axis=1)
        ha["low"]   = pd.concat([raw["low"],  ha["open"], ha["close"]], axis=1).min(axis=1)
        ha["bullish"] = ha["close"] > ha["open"]
        ha["lower_shadow"] = ha[["open", "close"]].min(axis=1) - ha["low"]

        last3 = ha.iloc[-3:]
        if last3["bullish"].all():
            no_lower = (last3["lower_shadow"] < (ha["close"] - ha["open"]).abs().mean() * 0.1).all()
            confidence = 78 if no_lower else 68
            if ind.supertrend_dir == 1 and ind.ema9 > ind.ema21:
                confidence = min(confidence + 8, 90)
            return PatternResult("Heikin Ashi Bull Trend", "LONG", confidence,
                                 f"3+ consecutive HA bull candles "
                                 f"{'(no lower shadows — strong trend)' if no_lower else ''}")

        if not last3["bullish"].any():
            upper_shadow = ha["high"] - ha[["open", "close"]].max(axis=1)
            no_upper = (upper_shadow.iloc[-3:] < (ha["close"] - ha["open"]).abs().mean() * 0.1).all()
            confidence = 78 if no_upper else 68
            if ind.supertrend_dir == -1 and ind.ema9 < ind.ema21:
                confidence = min(confidence + 8, 90)
            return PatternResult("Heikin Ashi Bear Trend", "SHORT", confidence,
                                 f"3+ consecutive HA bear candles "
                                 f"{'(no upper shadows — strong trend)' if no_upper else ''}")
        return None

    def detect_inside_bar(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Inside Bar — entire candle within previous candle's range.
        Signals consolidation before breakout continuation.
        Trade direction = direction of the preceding trend.
        18yr rule: "Inside bars are the market pausing to reload."
        """
        if len(df) < 5:
            return None
        mother = df.iloc[-2]   # Previous candle
        inside = df.iloc[-1]   # Current candle

        if not (inside["high"] <= mother["high"] and inside["low"] >= mother["low"]):
            return None

        # Body of mother candle should be meaningful (at least 0.5x ATR)
        mother_body = abs(mother["close"] - mother["open"])
        atr = ind.atr if ind.atr > 0 else 1
        if mother_body < 0.5 * atr:
            return None   # Doji mother — not a clean setup

        # Direction = preceding 5-bar trend
        trend_move = df["close"].iloc[-6] - df["close"].iloc[-10] if len(df) >= 10 else 0
        direction = "LONG" if trend_move > 0 else "SHORT"

        compression = (mother["high"] - mother["low"]) / max(inside["high"] - inside["low"], 0.01)
        confidence = min(62 + compression * 3, 80)

        return PatternResult(
            f"Inside Bar ({'Bull' if direction == 'LONG' else 'Bear'} Continuation)",
            direction, confidence,
            f"Inside bar after {'bull' if direction == 'LONG' else 'bear'} trend — "
            f"compression {compression:.1f}x. Breakout likely."
        )

    def detect_double_bottom_top(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Double Bottom (W-pattern) — bullish reversal at support.
        Double Top (M-pattern) — bearish reversal at resistance.
        Both bottoms/tops within 0.5% of each other = valid.
        18yr rule: "Double tops/bottoms are the most reliable reversal patterns in NSE."
        """
        if len(df) < 30:
            return None
        atr = ind.atr if ind.atr > 0 else 1

        # Double Bottom: two lows at similar level, second followed by break above midpoint
        lows = df["low"].iloc[-30:]
        sorted_low_idx = lows.nsmallest(3).index
        if len(sorted_low_idx) < 2:
            return None

        low1_idx = sorted_low_idx[0]
        low2_idx = sorted_low_idx[1]
        low1 = lows[low1_idx]
        low2 = lows[low2_idx]

        # Two bottoms within 0.5% and separated by at least 5 candles
        if abs(low1 - low2) / max(low1, 0.01) > 0.005:
            pass  # Too far apart
        elif abs(df.index.get_loc(low1_idx) - df.index.get_loc(low2_idx)) >= 5:
            # Find midpoint (the "neckline")
            between = df["high"].iloc[
                min(df.index.get_loc(low1_idx), df.index.get_loc(low2_idx)):
                max(df.index.get_loc(low1_idx), df.index.get_loc(low2_idx)) + 1
            ]
            neckline = between.max()
            curr_close = df.iloc[-1]["close"]
            if curr_close > neckline * 0.999:
                confidence = 78 + (ind.volume_ratio - 1) * 5
                confidence = min(confidence + (5 if ind.rsi < 50 else 0), 90)
                return PatternResult("Double Bottom", "LONG", confidence,
                                     f"W-pattern: two bottoms near ${min(low1,low2):.2f}, "
                                     f"breakout above neckline ${neckline:.2f}")

        # Double Top
        highs = df["high"].iloc[-30:]
        sorted_high_idx = highs.nlargest(3).index
        if len(sorted_high_idx) < 2:
            return None

        high1_idx = sorted_high_idx[0]
        high2_idx = sorted_high_idx[1]
        high1 = highs[high1_idx]
        high2 = highs[high2_idx]

        if abs(high1 - high2) / max(high1, 0.01) <= 0.005:
            if abs(df.index.get_loc(high1_idx) - df.index.get_loc(high2_idx)) >= 5:
                between = df["low"].iloc[
                    min(df.index.get_loc(high1_idx), df.index.get_loc(high2_idx)):
                    max(df.index.get_loc(high1_idx), df.index.get_loc(high2_idx)) + 1
                ]
                neckline = between.min()
                curr_close = df.iloc[-1]["close"]
                if curr_close < neckline * 1.001:
                    confidence = 78 + (ind.volume_ratio - 1) * 5
                    confidence = min(confidence + (5 if ind.rsi > 50 else 0), 90)
                    return PatternResult("Double Top", "SHORT", confidence,
                                        f"M-pattern: two tops near ${max(high1,high2):.2f}, "
                                        f"break below neckline ${neckline:.2f}")
        return None

    def detect_market_structure_break(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Break of Structure (BOS) / Change of Character (ChoCH).
        BOS = trend continuation: price breaks the last significant swing high/low.
        ChoCH = trend reversal: downtrend breaks a previous lower-high (bearish→bullish).
        18yr rule: "Structure breaks tell you WHO is in control — institutions or retail."
        """
        if len(df) < 20:
            return None
        atr = ind.atr if ind.atr > 0 else 1

        # Find last swing high and swing low using simple peak/valley detection
        highs = df["high"].values
        lows  = df["low"].values
        closes = df["close"].values

        # Swing high: local max in a 5-bar window
        swing_highs = []
        swing_lows  = []
        for j in range(2, len(df) - 2):
            if highs[j] > highs[j-1] and highs[j] > highs[j-2] and \
               highs[j] > highs[j+1] and highs[j] > highs[j+2]:
                swing_highs.append((j, highs[j]))
            if lows[j] < lows[j-1] and lows[j] < lows[j-2] and \
               lows[j] < lows[j+1] and lows[j] < lows[j+2]:
                swing_lows.append((j, lows[j]))

        curr_close = closes[-1]

        if len(swing_highs) >= 2:
            prev_swing_high = swing_highs[-2][1]  # Second-to-last swing high
            last_swing_high = swing_highs[-1][1]  # Most recent swing high
            # BOS Long: current price breaks ABOVE the last swing high
            if curr_close > last_swing_high * 1.001 and ind.volume_ratio >= 1.5:
                confidence = 76 + min((ind.volume_ratio - 1.5) * 8, 12)
                # Bonus if this creates HH (Higher High) = trend continuation
                if last_swing_high > prev_swing_high:
                    confidence = min(confidence + 7, 92)
                    return PatternResult("BOS — Higher High (Trend Continues)", "LONG",
                                        confidence,
                                        f"Break of Structure: new HH above ${last_swing_high:.2f} "
                                        f"with {ind.volume_ratio:.1f}x volume")
                else:
                    return PatternResult("ChoCH — Bullish Reversal", "LONG",
                                        confidence,
                                        f"Change of Character: broke above ${last_swing_high:.2f} "
                                        f"— downtrend reversing")

        if len(swing_lows) >= 2:
            prev_swing_low = swing_lows[-2][1]
            last_swing_low = swing_lows[-1][1]
            if curr_close < last_swing_low * 0.999 and ind.volume_ratio >= 1.5:
                confidence = 76 + min((ind.volume_ratio - 1.5) * 8, 12)
                if last_swing_low < prev_swing_low:
                    confidence = min(confidence + 7, 92)
                    return PatternResult("BOS — Lower Low (Trend Continues)", "SHORT",
                                        confidence,
                                        f"Break of Structure: new LL below ${last_swing_low:.2f} "
                                        f"with {ind.volume_ratio:.1f}x volume")
                else:
                    return PatternResult("ChoCH — Bearish Reversal", "SHORT",
                                        confidence,
                                        f"Change of Character: broke below ${last_swing_low:.2f} "
                                        f"— uptrend reversing")
        return None

    def detect_ema_stack(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        EMA Stack — full alignment of 9 > 21 > 50 > 200 (bullish) or reverse (bearish).
        Price above/below all EMAs = maximum trend conviction.
        18yr rule: "When all 4 EMAs are stacked, ride it — don't fight it."
        """
        if ind.ema9 == 0 or ind.ema200 == 0:
            return None
        curr_close = df.iloc[-1]["close"]

        # Full bullish stack
        if (ind.ema9 > ind.ema21 > ind.ema50 > ind.ema200 and
                curr_close > ind.ema9):
            # Measure momentum: price distance above EMA200 as % of ATR
            atr = ind.atr if ind.atr > 0 else 1
            dist_from_200 = (curr_close - ind.ema200) / atr
            confidence = 72 + min(dist_from_200 * 2, 15)
            # Only trade if price pulled back to EMA9 or EMA21 recently
            near_ema = min(abs(curr_close - ind.ema9), abs(curr_close - ind.ema21)) < 2 * atr
            if near_ema:
                confidence = min(confidence + 8, 92)
            return PatternResult("Full EMA Stack Bullish", "LONG", confidence,
                                 f"EMA9({ind.ema9:.0f}) > EMA21({ind.ema21:.0f}) > "
                                 f"EMA50({ind.ema50:.0f}) > EMA200({ind.ema200:.0f}) — "
                                 f"maximum bullish trend alignment")

        # Full bearish stack
        if (ind.ema9 < ind.ema21 < ind.ema50 < ind.ema200 and
                curr_close < ind.ema9):
            atr = ind.atr if ind.atr > 0 else 1
            dist_from_200 = (ind.ema200 - curr_close) / atr
            confidence = 72 + min(dist_from_200 * 2, 15)
            near_ema = min(abs(curr_close - ind.ema9), abs(curr_close - ind.ema21)) < 2 * atr
            if near_ema:
                confidence = min(confidence + 8, 92)
            return PatternResult("Full EMA Stack Bearish", "SHORT", confidence,
                                 f"EMA9({ind.ema9:.0f}) < EMA21({ind.ema21:.0f}) < "
                                 f"EMA50({ind.ema50:.0f}) < EMA200({ind.ema200:.0f}) — "
                                 f"maximum bearish trend alignment")
        return None

    def detect_relative_strength_vs_nifty(
        self, df: pd.DataFrame, ind: IndicatorSet,
        nifty_df: Optional[pd.DataFrame] = None
    ) -> Optional[PatternResult]:
        """
        Relative Strength vs Nifty50 — NSE-specific elite filter.
        Long only stocks stronger than Nifty. Short only stocks weaker.
        Measured as: stock % change vs Nifty % change over last 5 candles.
        18yr rule: "Never go long a weak stock in a strong market.
                    The best trades are in stocks LEADING Nifty."
        """
        if nifty_df is None or len(df) < 6 or len(nifty_df) < 6:
            return None

        # 5-bar return for stock and Nifty
        stock_ret  = (df["close"].iloc[-1] - df["close"].iloc[-6]) / max(df["close"].iloc[-6], 0.01) * 100
        nifty_ret  = (nifty_df["close"].iloc[-1] - nifty_df["close"].iloc[-6]) / max(nifty_df["close"].iloc[-6], 0.01) * 100
        rs_delta   = stock_ret - nifty_ret

        if rs_delta > 1.0:   # Stock outperforming Nifty by >1% over 5 bars
            confidence = min(62 + rs_delta * 5, 85)
            return PatternResult("RS+ vs Nifty", "LONG", confidence,
                                 f"Stock +{stock_ret:.1f}% vs Nifty +{nifty_ret:.1f}% "
                                 f"(RS delta: +{rs_delta:.1f}%) — leading the market")
        if rs_delta < -1.0:  # Stock underperforming Nifty by >1%
            confidence = min(62 + abs(rs_delta) * 5, 85)
            return PatternResult("RS- vs Nifty", "SHORT", confidence,
                                 f"Stock {stock_ret:.1f}% vs Nifty {nifty_ret:.1f}% "
                                 f"(RS delta: {rs_delta:.1f}%) — lagging the market")
        return None

    # --------------------------------------------------------
    # ADDITIONAL PATTERNS (Cup&Handle, H&S, Wedge, Triangle, etc.)
    # --------------------------------------------------------

    def detect_cup_and_handle(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Cup and Handle: U-shaped consolidation followed by a small pullback handle,
        then breakout above the cup rim. One of the most reliable bullish continuations.
        Needs 30+ bars.
        """
        if len(df) < 35:
            return None
        closes = df["close"].values
        highs  = df["high"].values

        # Rim = highest high in last 35 bars (left and right of cup)
        rim  = max(highs[-35:-20])          # left side
        cup_low = min(closes[-30:-10])       # cup bottom
        right_rim = max(highs[-15:-5])       # right side

        if cup_low <= 0 or rim <= 0:
            return None

        depth = (rim - cup_low) / rim * 100
        symmetry = abs(right_rim - rim) / rim * 100

        # Valid cup: 5-30% depth, right rim close to left rim
        if not (5 <= depth <= 30 and symmetry < 5):
            return None

        # Handle: small pullback then close near rim
        handle_low = min(closes[-8:-2])
        current    = closes[-1]
        handle_depth = (right_rim - handle_low) / right_rim * 100

        if not (1 <= handle_depth <= 10):
            return None

        # Breakout: current price breaks above rim
        if current < rim * 0.99:
            return None

        confidence = min(65 + ind.volume_ratio * 5 + max(0, 20 - depth), 90)
        return PatternResult(
            "Cup and Handle", "LONG", confidence,
            f"C&H breakout: cup={depth:.1f}% deep, handle={handle_depth:.1f}%, "
            f"vol={ind.volume_ratio:.1f}x"
        )

    def detect_inverse_head_shoulders(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Inverse Head & Shoulders: three troughs, middle trough (head) lowest.
        Major bullish reversal pattern. Neckline break = entry.
        """
        if len(df) < 40:
            return None
        lows   = df["low"].values
        closes = df["close"].values

        # Find three local lows in the last 40 bars
        def local_min(arr, start, end, window=5):
            best_val = arr[start + window]
            best_idx = start + window
            for i in range(start + window, end - window):
                if arr[i] == min(arr[i-window:i+window+1]):
                    if arr[i] < best_val:
                        best_val, best_idx = arr[i], i
            return best_idx, best_val

        n   = len(lows)
        lb  = n - 40
        ls_idx, ls = local_min(lows, lb,      lb + 15)
        hd_idx, hd = local_min(lows, lb + 12, lb + 28)
        rs_idx, rs = local_min(lows, lb + 25, n - 3)

        if not (hd < ls and hd < rs):   # head must be lowest
            return None

        shoulder_sym = abs(ls - rs) / max(ls, 0.01) * 100
        if shoulder_sym > 8:            # shoulders should be similar height
            return None

        neckline = (lows[ls_idx] + lows[rs_idx]) / 2 * 1.01
        if closes[-1] < neckline:       # must break above neckline
            return None

        depth = (neckline - hd) / neckline * 100
        confidence = min(68 + depth * 1.5 + ind.volume_ratio * 3, 92)
        return PatternResult(
            "Inverse Head & Shoulders", "LONG", confidence,
            f"IH&S breakout: head={depth:.1f}% below neckline, "
            f"shoulder_sym={shoulder_sym:.1f}%"
        )

    def detect_head_and_shoulders(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Head & Shoulders: three peaks, middle peak (head) highest.
        Classic bearish reversal. Neckline break below = entry.
        """
        if len(df) < 40:
            return None
        highs  = df["high"].values
        closes = df["close"].values

        def local_max(arr, start, end, window=5):
            best_val = arr[start + window]
            best_idx = start + window
            for i in range(start + window, end - window):
                if arr[i] == max(arr[i-window:i+window+1]):
                    if arr[i] > best_val:
                        best_val, best_idx = arr[i], i
            return best_idx, best_val

        n   = len(highs)
        lb  = n - 40
        ls_idx, ls = local_max(highs, lb,      lb + 15)
        hd_idx, hd = local_max(highs, lb + 12, lb + 28)
        rs_idx, rs = local_max(highs, lb + 25, n - 3)

        if not (hd > ls and hd > rs):
            return None

        shoulder_sym = abs(ls - rs) / max(ls, 0.01) * 100
        if shoulder_sym > 8:
            return None

        neckline = (highs[ls_idx] + highs[rs_idx]) / 2 * 0.99
        if closes[-1] > neckline:
            return None

        depth = (hd - neckline) / neckline * 100
        confidence = min(68 + depth * 1.5 + ind.volume_ratio * 3, 92)
        return PatternResult(
            "Head & Shoulders", "SHORT", confidence,
            f"H&S breakdown: head={depth:.1f}% above neckline"
        )

    def detect_rising_wedge(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Rising Wedge: price rises but range compresses (bearish divergence).
        Both highs and lows trending up, but converging — often precedes reversal.
        """
        if len(df) < 20:
            return None
        highs  = df["high"].values[-20:]
        lows   = df["low"].values[-20:]
        closes = df["close"].values[-20:]

        # Linear regression slopes for highs and lows
        x      = list(range(20))
        hi_slope = (highs[-1] - highs[0]) / 19
        lo_slope = (lows[-1]  - lows[0])  / 19

        # Both trending up, but lows rising faster (converging wedge)
        if not (hi_slope > 0 and lo_slope > 0 and lo_slope > hi_slope * 0.5):
            return None

        # Convergence: upper - lower channel narrowing
        range_start = highs[0]  - lows[0]
        range_end   = highs[-1] - lows[-1]
        if range_end >= range_start:
            return None   # must be compressing

        compression = (range_start - range_end) / max(range_start, 0.01) * 100
        if compression < 15:
            return None

        # RSI divergence: price up but RSI not making new highs = extra confirmation
        confidence = 58 + compression * 0.3
        if ind.rsi > 60:
            confidence += 8
        confidence = min(confidence, 85)

        return PatternResult(
            "Rising Wedge (Bearish)", "SHORT", confidence,
            f"Rising wedge: range compressed {compression:.1f}%, "
            f"RSI={ind.rsi:.0f}"
        )

    def detect_falling_wedge(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Falling Wedge: price falls but range compresses (bullish setup).
        Both highs and lows trending down, but converging — spring-loaded reversal.
        """
        if len(df) < 20:
            return None
        highs  = df["high"].values[-20:]
        lows   = df["low"].values[-20:]
        closes = df["close"].values[-20:]

        hi_slope = (highs[-1] - highs[0]) / 19
        lo_slope = (lows[-1]  - lows[0])  / 19

        if not (hi_slope < 0 and lo_slope < 0 and lo_slope < hi_slope * 0.5):
            return None

        range_start = highs[0]  - lows[0]
        range_end   = highs[-1] - lows[-1]
        if range_end >= range_start:
            return None

        compression = (range_start - range_end) / max(range_start, 0.01) * 100
        if compression < 15:
            return None

        # Bullish signal: last close near the upper trendline
        if closes[-1] < lows[-1] + (highs[-1] - lows[-1]) * 0.5:
            return None

        confidence = 60 + compression * 0.3
        if ind.rsi < 45:
            confidence += 8
        confidence = min(confidence, 88)

        return PatternResult(
            "Falling Wedge (Bullish)", "LONG", confidence,
            f"Falling wedge: range compressed {compression:.1f}% — coiled spring"
        )

    def detect_ascending_triangle(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Ascending Triangle: flat resistance + rising lows → bullish breakout.
        High-probability pattern when accompanied by volume surge on breakout.
        """
        if len(df) < 25:
            return None
        highs  = df["high"].values[-25:]
        lows   = df["low"].values[-25:]
        closes = df["close"].values

        # Flat resistance: recent highs within 0.5%
        resistance = max(highs)
        high_spread = (max(highs[-15:]) - min(highs[-15:])) / max(resistance, 0.01) * 100
        if high_spread > 1.5:
            return None

        # Rising lows: low of last 5 bars > low of first 5 bars
        early_low = min(lows[:10])
        late_low  = min(lows[-10:])
        if late_low <= early_low * 1.005:
            return None

        # Breakout: current close above resistance
        if closes[-1] < resistance * 0.998:
            return None

        confidence = min(65 + ind.volume_ratio * 6, 88)
        return PatternResult(
            "Ascending Triangle", "LONG", confidence,
            f"Ascending triangle breakout above ${resistance:.2f} "
            f"vol={ind.volume_ratio:.1f}x"
        )

    def detect_descending_triangle(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Descending Triangle: flat support + falling highs → bearish breakdown.
        """
        if len(df) < 25:
            return None
        highs  = df["high"].values[-25:]
        lows   = df["low"].values[-25:]
        closes = df["close"].values

        support   = min(lows)
        low_spread = (max(lows[-15:]) - min(lows[-15:])) / max(abs(support), 0.01) * 100
        if low_spread > 1.5:
            return None

        early_high = max(highs[:10])
        late_high  = max(highs[-10:])
        if late_high >= early_high * 0.995:
            return None

        if closes[-1] > support * 1.002:
            return None

        confidence = min(63 + ind.volume_ratio * 5, 85)
        return PatternResult(
            "Descending Triangle", "SHORT", confidence,
            f"Descending triangle breakdown below ${support:.2f}"
        )

    def detect_tweezer_tops(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Tweezer Tops: two candles with almost identical highs after an uptrend.
        Signals rejection at a level — bearish reversal.
        """
        if len(df) < 5:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]

        # Highs within 0.15%
        high_diff = abs(curr["high"] - prev["high"]) / max(prev["high"], 0.01) * 100
        if high_diff > 0.15:
            return None

        # At least one candle bearish
        if not (curr["close"] < curr["open"] or prev["close"] < prev["open"]):
            return None

        # In uptrend (recent lows rising)
        if len(df) >= 10:
            recent_lows = df["low"].values[-10:]
            if recent_lows[-1] < recent_lows[0]:
                return None

        confidence = 58 + (0.15 - high_diff) * 100
        if ind.rsi > 65:
            confidence += 8
        confidence = min(confidence, 80)
        return PatternResult(
            "Tweezer Tops", "SHORT", confidence,
            f"Tweezer tops: dual rejection at ${curr['high']:.2f}"
        )

    def detect_tweezer_bottoms(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Tweezer Bottoms: two candles with almost identical lows after a downtrend.
        Signals support holding — bullish reversal.
        """
        if len(df) < 5:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]

        low_diff = abs(curr["low"] - prev["low"]) / max(abs(prev["low"]), 0.01) * 100
        if low_diff > 0.15:
            return None

        if not (curr["close"] > curr["open"] or prev["close"] > prev["open"]):
            return None

        if len(df) >= 10:
            recent_highs = df["high"].values[-10:]
            if recent_highs[-1] > recent_highs[0]:
                return None

        confidence = 58 + (0.15 - low_diff) * 100
        if ind.rsi < 40:
            confidence += 8
        confidence = min(confidence, 80)
        return PatternResult(
            "Tweezer Bottoms", "LONG", confidence,
            f"Tweezer bottoms: dual support at ${curr['low']:.2f}"
        )

    def detect_three_inside_up(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Three Inside Up: bearish candle → smaller bullish inside bar →
        strong bullish close above bar-1's open. Reliable reversal.
        """
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

        # Bar 1: bearish
        if c1["close"] >= c1["open"]:
            return None
        # Bar 2: bullish inside bar
        if not (c2["close"] > c2["open"] and c2["high"] < c1["open"] and c2["low"] > c1["close"]):
            return None
        # Bar 3: bullish close above bar 1 open
        if c3["close"] <= c1["open"]:
            return None

        confidence = 62 + ind.volume_ratio * 5
        if ind.rsi < 45:
            confidence += 8
        confidence = min(confidence, 85)
        return PatternResult(
            "Three Inside Up", "LONG", confidence,
            "Three inside up: bearish engulfed, bullish continuation confirmed"
        )

    def detect_three_inside_down(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Three Inside Down: bullish candle → smaller bearish inside bar →
        strong bearish close below bar-1's open. Reliable reversal.
        """
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

        if c1["close"] <= c1["open"]:
            return None
        if not (c2["close"] < c2["open"] and c2["high"] < c1["close"] and c2["low"] > c1["open"]):
            return None
        if c3["close"] >= c1["open"]:
            return None

        confidence = 62 + ind.volume_ratio * 5
        if ind.rsi > 55:
            confidence += 8
        confidence = min(confidence, 85)
        return PatternResult(
            "Three Inside Down", "SHORT", confidence,
            "Three inside down: bullish engulfed, bearish continuation confirmed"
        )

    def detect_kicker_pattern(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Kicker: two candles where bar 2 opens at or above bar 1's open (bullish)
        or at or below bar 1's open (bearish), with a gap showing sentiment shift.
        One of the most powerful reversal signals.
        """
        if len(df) < 3:
            return None
        prev, curr = df.iloc[-2], df.iloc[-1]

        # Bullish kicker: prev bearish, curr gaps up and closes well above
        if (prev["close"] < prev["open"] and
                curr["open"] >= prev["open"] * 0.998 and
                curr["close"] > curr["open"]):
            gap_pct = (curr["open"] - prev["open"]) / max(prev["open"], 0.01) * 100
            body_pct = (curr["close"] - curr["open"]) / max(curr["open"], 0.01) * 100
            if body_pct >= 0.3:
                confidence = min(72 + gap_pct * 5 + ind.volume_ratio * 4, 92)
                return PatternResult(
                    "Bullish Kicker", "LONG", confidence,
                    f"Bullish kicker: gap up {gap_pct:.2f}%, strong bull body {body_pct:.2f}%"
                )

        # Bearish kicker: prev bullish, curr gaps down
        if (prev["close"] > prev["open"] and
                curr["open"] <= prev["open"] * 1.002 and
                curr["close"] < curr["open"]):
            gap_pct = (prev["open"] - curr["open"]) / max(prev["open"], 0.01) * 100
            body_pct = (curr["open"] - curr["close"]) / max(curr["open"], 0.01) * 100
            if body_pct >= 0.3:
                confidence = min(72 + gap_pct * 5 + ind.volume_ratio * 4, 92)
                return PatternResult(
                    "Bearish Kicker", "SHORT", confidence,
                    f"Bearish kicker: gap down {gap_pct:.2f}%, strong bear body {body_pct:.2f}%"
                )
        return None

    def detect_abandoned_baby(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Abandoned Baby: doji with gap on both sides = extreme reversal signal.
        Bullish: downtrend, gap-down doji, gap-up bullish close.
        """
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

        body2 = abs(c2["close"] - c2["open"])
        range2 = c2["high"] - c2["low"]
        is_doji = range2 > 0 and body2 / range2 < 0.1

        if not is_doji:
            return None

        # Bullish: c1 bearish, gap-down doji, gap-up bullish c3
        if (c1["close"] < c1["open"] and
                c2["high"] < c1["low"] and
                c3["low"] > c2["high"] and
                c3["close"] > c3["open"]):
            confidence = min(78 + ind.volume_ratio * 5, 93)
            return PatternResult(
                "Bullish Abandoned Baby", "LONG", confidence,
                "Abandoned baby: gap-down doji + gap-up reversal — extreme bull signal"
            )

        # Bearish: c1 bullish, gap-up doji, gap-down bearish c3
        if (c1["close"] > c1["open"] and
                c2["low"] > c1["high"] and
                c3["high"] < c2["low"] and
                c3["close"] < c3["open"]):
            confidence = min(78 + ind.volume_ratio * 5, 93)
            return PatternResult(
                "Bearish Abandoned Baby", "SHORT", confidence,
                "Abandoned baby: gap-up doji + gap-down reversal — extreme bear signal"
            )
        return None

    def detect_symmetrical_triangle(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Symmetrical Triangle: converging highs and lows — coiled spring.
        Breakout direction follows the prevailing trend (trade with momentum).
        """
        if len(df) < 20:
            return None
        highs  = df["high"].values[-20:]
        lows   = df["low"].values[-20:]
        closes = df["close"].values

        hi_slope = (highs[-1] - highs[0]) / 19
        lo_slope = (lows[-1]  - lows[0])  / 19

        # Highs falling, lows rising (converging)
        if not (hi_slope < 0 and lo_slope > 0):
            return None

        compression = ((highs[0] - lows[0]) - (highs[-1] - lows[-1])) / max(highs[0] - lows[0], 0.01) * 100
        if compression < 20:
            return None

        # Breakout direction
        mid = (highs[-1] + lows[-1]) / 2
        curr = closes[-1]

        if curr > highs[-1] * 0.998:
            direction  = "LONG"
            confidence = min(62 + compression * 0.3 + ind.volume_ratio * 5, 85)
            return PatternResult(
                "Symmetrical Triangle (Bullish Breakout)", direction, confidence,
                f"Symmetrical triangle breakout: compression {compression:.1f}%"
            )
        elif curr < lows[-1] * 1.002:
            direction  = "SHORT"
            confidence = min(62 + compression * 0.3 + ind.volume_ratio * 5, 85)
            return PatternResult(
                "Symmetrical Triangle (Bearish Breakdown)", direction, confidence,
                f"Symmetrical triangle breakdown: compression {compression:.1f}%"
            )
        return None

    # ─────────────────────────────────────────────────────────
    # MORE CANDLESTICK PATTERNS
    # ─────────────────────────────────────────────────────────

    def detect_belt_hold(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Belt Hold: Long body with no shadow on the opening side.
        Bullish Belt Hold: opens at low (no lower shadow), closes near high.
        Bearish Belt Hold: opens at high (no upper shadow), closes near low.
        """
        if len(df) < 2:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        rng  = c["high"] - c["low"]
        if rng <= 0 or body / rng < 0.7:
            return None

        # Bullish: open = low (no lower shadow)
        if c["close"] > c["open"] and (c["open"] - c["low"]) / rng < 0.03:
            conf = 58 + ind.volume_ratio * 5
            if ind.rsi < 45:
                conf += 8
            return PatternResult("Bullish Belt Hold", "LONG", min(conf, 80),
                                 "Belt Hold: opens at low, strong bull body")

        # Bearish: open = high (no upper shadow)
        if c["close"] < c["open"] and (c["high"] - c["open"]) / rng < 0.03:
            conf = 58 + ind.volume_ratio * 5
            if ind.rsi > 55:
                conf += 8
            return PatternResult("Bearish Belt Hold", "SHORT", min(conf, 80),
                                 "Belt Hold: opens at high, strong bear body")
        return None

    def detect_rising_three_methods(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Rising Three Methods: Long bullish candle, 3 small declining bars within
        the range, then strong bullish close above bar-1. Bullish continuation.
        """
        if len(df) < 5:
            return None
        c1, c2, c3, c4, c5 = df.iloc[-5], df.iloc[-4], df.iloc[-3], df.iloc[-2], df.iloc[-1]

        # First: big bull candle
        if c1["close"] <= c1["open"]:
            return None
        big_body = c1["close"] - c1["open"]

        # Middle 3: small candles within c1 range
        for c in [c2, c3, c4]:
            if c["high"] > c1["high"] * 1.001 or c["low"] < c1["low"] * 0.999:
                return None
        # Middle 3 trend slightly down or flat
        if c4["close"] >= c1["close"] or c2["close"] <= c1["open"]:
            return None

        # Final: strong bull close above c1's close
        if c5["close"] <= c1["close"]:
            return None

        conf = 68 + ind.volume_ratio * 5
        return PatternResult("Rising Three Methods", "LONG", min(conf, 85),
                             "Bullish continuation: 3-bar consolidation within bull range → breakout")

    def detect_falling_three_methods(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Falling Three Methods: Long bearish candle, 3 small rising bars within
        range, then strong bearish close below bar-1. Bearish continuation.
        """
        if len(df) < 5:
            return None
        c1, c2, c3, c4, c5 = df.iloc[-5], df.iloc[-4], df.iloc[-3], df.iloc[-2], df.iloc[-1]

        if c1["close"] >= c1["open"]:
            return None
        for c in [c2, c3, c4]:
            if c["high"] > c1["high"] * 1.001 or c["low"] < c1["low"] * 0.999:
                return None
        if c4["close"] <= c1["close"] or c2["close"] >= c1["open"]:
            return None
        if c5["close"] >= c1["close"]:
            return None

        conf = 68 + ind.volume_ratio * 5
        return PatternResult("Falling Three Methods", "SHORT", min(conf, 85),
                             "Bearish continuation: 3-bar bounce within bear range → breakdown")

    def detect_upside_tasuki_gap(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Upside Tasuki Gap: gap-up bull candle, second bull candle, third bear candle
        that PARTIALLY fills the gap but doesn't close it. Bullish continuation.
        """
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

        # Gap up between c1 and c2
        gap_up = c2["open"] > c1["high"]
        if not gap_up:
            return None
        if c2["close"] <= c2["open"]:  # c2 must be bullish
            return None
        if c3["close"] >= c3["open"]:  # c3 must be bearish
            return None
        # c3 partially fills gap but doesn't close it
        if c3["close"] <= c1["high"]:  # closed into the gap — invalid
            return None

        conf = 62 + ind.volume_ratio * 4
        return PatternResult("Upside Tasuki Gap", "LONG", min(conf, 78),
                             "Tasuki gap: partial gap fill → bulls still in control")

    def detect_downside_tasuki_gap(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Downside Tasuki Gap: gap-down bear candle, second bear candle, third bull
        candle that partially fills the gap. Bearish continuation.
        """
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

        gap_down = c2["open"] < c1["low"]
        if not gap_down:
            return None
        if c2["close"] >= c2["open"]:
            return None
        if c3["close"] <= c3["open"]:
            return None
        if c3["close"] >= c1["low"]:
            return None

        conf = 62 + ind.volume_ratio * 4
        return PatternResult("Downside Tasuki Gap", "SHORT", min(conf, 78),
                             "Downside Tasuki gap: partial fill → bears still in control")

    def detect_on_neck(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        On-Neck: Bear candle, then bull candle that closes near previous low.
        Bears still in control — bearish continuation.
        """
        if len(df) < 2:
            return None
        c1, c2 = df.iloc[-2], df.iloc[-1]

        if c1["close"] >= c1["open"]:
            return None
        if c2["close"] <= c2["open"]:
            return None
        # c2 close near c1 low (within 0.1%)
        near_c1_low = abs(c2["close"] - c1["low"]) / max(c1["low"], 0.01) < 0.001
        if not near_c1_low:
            return None

        conf = 58 + (5 if ind.rsi > 55 else 0)
        return PatternResult("On Neck (Bearish)", "SHORT", conf,
                             "On-neck: bull candle closes at prior low — bears in control")

    def detect_mat_hold(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Mat Hold: Like rising three methods but the first candle is huge (marubozu),
        and middle candles stay within the upper half of bar 1. Very bullish.
        """
        if len(df) < 5:
            return None
        c1, c2, c3, c4, c5 = df.iloc[-5], df.iloc[-4], df.iloc[-3], df.iloc[-2], df.iloc[-1]

        body1 = c1["close"] - c1["open"]
        rng1  = c1["high"] - c1["low"]
        if body1 <= 0 or rng1 <= 0 or body1 / rng1 < 0.75:
            return None

        # Middle candles gap up and stay in upper portion
        mid_low   = min(c2["low"], c3["low"], c4["low"])
        bar1_mid  = (c1["open"] + c1["close"]) / 2
        if mid_low < bar1_mid:
            return None

        # Final candle breaks out strongly
        if c5["close"] <= c1["close"]:
            return None
        if c5["close"] <= c5["open"]:
            return None

        conf = 70 + ind.volume_ratio * 5
        return PatternResult("Mat Hold (Bullish)", "LONG", min(conf, 88),
                             "Mat Hold: massive bull body, consolidation in upper half → breakout")

    def detect_two_crows(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Two Crows: Uptrend, gap-up bearish candle, second gap-up bearish candle
        that engulfs the first bear. Bearish reversal warning.
        """
        if len(df) < 3:
            return None
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

        if c1["close"] <= c1["open"]:  # c1 must be bull
            return None
        if c2["close"] >= c2["open"] or c2["open"] <= c1["close"]:  # c2 bears, gaps up
            return None
        if c3["close"] >= c3["open"]:  # c3 must be bearish
            return None
        if c3["open"] <= c2["open"] or c3["close"] >= c2["open"]:  # c3 engulfs c2
            return None

        conf = 62 + (8 if ind.rsi > 60 else 0)
        return PatternResult("Two Crows (Bearish)", "SHORT", min(conf, 80),
                             "Two Crows: sequential bearish engulfment after gap-up — reversal warning")

    def detect_homing_pigeon(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Homing Pigeon: Two bearish candles where c2 is inside c1.
        Similar to inside bar but both bearish. Bullish reversal.
        Bears losing momentum.
        """
        if len(df) < 3:
            return None
        c1, c2 = df.iloc[-2], df.iloc[-1]

        if c1["close"] >= c1["open"] or c2["close"] >= c2["open"]:
            return None
        # c2 inside c1 body
        if not (c2["open"] <= c1["open"] and c2["close"] >= c1["close"]):
            return None
        body2 = abs(c2["close"] - c2["open"])
        body1 = abs(c1["close"] - c1["open"])
        if body2 >= body1 * 0.6:
            return None  # c2 should be noticeably smaller

        conf = 60 + (8 if ind.rsi < 40 else 0)
        return PatternResult("Homing Pigeon (Bullish)", "LONG", min(conf, 75),
                             "Homing Pigeon: bear momentum fading — second smaller bear inside first")

    def detect_matching_low(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Matching Low: Two consecutive candles with almost identical lows.
        Strong support level — bulls defend this price. Bullish reversal.
        """
        if len(df) < 3:
            return None
        c1, c2 = df.iloc[-2], df.iloc[-1]

        # Both bearish (or c2 can be neutral)
        diff = abs(c2["low"] - c1["low"]) / max(c1["low"], 0.01) * 100
        if diff > 0.05:
            return None

        # In downtrend
        if len(df) >= 8 and df["close"].values[-8] < df["close"].values[-3]:
            return None

        conf = 62 + (8 if ind.rsi < 40 else 0)
        return PatternResult("Matching Low (Support)", "LONG", min(conf, 78),
                             f"Matching Low: identical lows at ${c1['low']:.2f} — strong support")

    def detect_counterattack_lines(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Counterattack: After strong directional candle, opposite candle
        opens with gap but closes AT SAME price. Reversal signal.
        """
        if len(df) < 2:
            return None
        c1, c2 = df.iloc[-2], df.iloc[-1]

        close_match = abs(c2["close"] - c1["close"]) / max(c1["close"], 0.01) * 100 < 0.05

        # Bullish counterattack: c1 bearish, c2 gaps down then recovers to c1 close
        if c1["close"] < c1["open"] and c2["close"] > c2["open"] and close_match:
            conf = 62 + (8 if ind.rsi < 40 else 0)
            return PatternResult("Bullish Counterattack", "LONG", min(conf, 78),
                                 "Counterattack: bulls recover entire bear day — momentum reversal")

        # Bearish counterattack
        if c1["close"] > c1["open"] and c2["close"] < c2["open"] and close_match:
            conf = 62 + (8 if ind.rsi > 60 else 0)
            return PatternResult("Bearish Counterattack", "SHORT", min(conf, 78),
                                 "Counterattack: bears recover entire bull day — momentum reversal")
        return None

    # ─────────────────────────────────────────────────────────
    # NEW CANDLESTICK PATTERNS
    # ─────────────────────────────────────────────────────────

    def detect_inverted_hammer(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 2:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        upper_shadow = c["high"] - max(c["close"], c["open"])
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        if upper_shadow < 2 * body or lower_shadow > body * 0.5:
            return None
        # Must be at support (near recent lows)
        recent_low = df["low"].iloc[-10:].min() if len(df) >= 10 else df["low"].min()
        if c["low"] > recent_low * 1.02:
            return None
        confidence = 65 + (upper_shadow / total_range) * 15
        if ind.rsi < 40:
            confidence = min(confidence + 10, 88)
        return PatternResult("Inverted Hammer", "LONG", min(confidence, 85),
                             f"Inverted hammer at support — rejection of lows, RSI={ind.rsi:.0f}")

    def detect_hanging_man(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 5:
            return None
        c = df.iloc[-1]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        upper_shadow = c["high"] - max(c["close"], c["open"])
        if lower_shadow < 2 * body or upper_shadow > body:
            return None
        # Must be at the top of an uptrend
        recent_high = df["high"].iloc[-10:].max() if len(df) >= 10 else df["high"].max()
        if c["high"] < recent_high * 0.98:
            return None
        confidence = 62 + (lower_shadow / total_range) * 15
        if ind.rsi > 60:
            confidence = min(confidence + 8, 82)
        return PatternResult("Hanging Man", "SHORT", min(confidence, 80),
                             f"Hanging man at uptrend top — bearish warning, RSI={ind.rsi:.0f}")

    def detect_marubozu_bullish(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        body = c["close"] - c["open"]
        if body <= 0:
            return None
        upper_shadow = c["high"] - c["close"]
        lower_shadow = c["open"] - c["low"]
        # Body must be >= 95% of total range (tiny wicks only)
        if body / total_range < 0.95:
            return None
        atr = ind.atr if ind.atr > 0 else 1
        confidence = 80 + min(ind.volume_ratio * 3, 8)
        if body > atr:
            confidence = min(confidence + 5, 92)
        return PatternResult("Bullish Marubozu", "LONG", confidence,
                             f"Bullish Marubozu: full body candle, no wicks — strong institutional buying")

    def detect_marubozu_bearish(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        body = c["open"] - c["close"]
        if body <= 0:
            return None
        if body / total_range < 0.95:
            return None
        atr = ind.atr if ind.atr > 0 else 1
        confidence = 80 + min(ind.volume_ratio * 3, 8)
        if body > atr:
            confidence = min(confidence + 5, 92)
        return PatternResult("Bearish Marubozu", "SHORT", confidence,
                             f"Bearish Marubozu: full body candle, no wicks — strong institutional selling")

    def detect_spinning_top(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        body = abs(c["close"] - c["open"])
        upper_shadow = c["high"] - max(c["close"], c["open"])
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        # Small body (<30% of range), roughly equal shadows
        if body / total_range > 0.3:
            return None
        shadow_ratio = min(upper_shadow, lower_shadow) / max(max(upper_shadow, lower_shadow), 0.001)
        if shadow_ratio < 0.5:
            return None
        return PatternResult("Spinning Top", "NEUTRAL", 55,
                             "Spinning top — indecision, equal upper/lower wicks; watch for breakout")

    def detect_dragonfly_doji(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        body = abs(c["close"] - c["open"])
        upper_shadow = c["high"] - max(c["close"], c["open"])
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        # Doji body (<10% of range), long lower shadow, tiny/no upper shadow
        if body / total_range > 0.1:
            return None
        if lower_shadow < total_range * 0.6:
            return None
        if upper_shadow > total_range * 0.1:
            return None
        confidence = 72 + (lower_shadow / total_range) * 10
        if ind.rsi < 40:
            confidence = min(confidence + 8, 88)
        return PatternResult("Dragonfly Doji", "LONG", min(confidence, 85),
                             f"Dragonfly Doji: long lower wick, strong rejection of lows — LONG reversal")

    def detect_gravestone_doji(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 1:
            return None
        c = df.iloc[-1]
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return None
        body = abs(c["close"] - c["open"])
        upper_shadow = c["high"] - max(c["close"], c["open"])
        lower_shadow = min(c["close"], c["open"]) - c["low"]
        # Doji body, long upper shadow, tiny/no lower shadow
        if body / total_range > 0.1:
            return None
        if upper_shadow < total_range * 0.6:
            return None
        if lower_shadow > total_range * 0.1:
            return None
        confidence = 72 + (upper_shadow / total_range) * 10
        if ind.rsi > 60:
            confidence = min(confidence + 8, 88)
        return PatternResult("Gravestone Doji", "SHORT", min(confidence, 85),
                             f"Gravestone Doji: long upper wick, strong rejection of highs — SHORT reversal")

    # ─────────────────────────────────────────────────────────
    # NEW CHART PATTERNS
    # ─────────────────────────────────────────────────────────

    def detect_rectangle_pattern(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 20:
            return None
        recent = df.iloc[-20:]
        resistance = recent["high"].max()
        support = recent["low"].min()
        price_range = resistance - support
        if price_range <= 0:
            return None
        # Flat S/R: highs within 1% of resistance, lows within 1% of support
        high_spread = (recent["high"].max() - recent["high"].mean()) / resistance
        low_spread = (recent["low"].mean() - recent["low"].min()) / max(abs(support), 0.01)
        if high_spread > 0.015 or low_spread > 0.015:
            return None
        curr = df.iloc[-1]["close"]
        # Breakout or breakdown
        if curr > resistance * 1.001 and ind.volume_ratio >= 1.5:
            confidence = min(65 + ind.volume_ratio * 8, 85)
            return PatternResult("Rectangle Breakout", "LONG", confidence,
                                 f"Rectangle breakout above ${resistance:.2f} — vol={ind.volume_ratio:.1f}x")
        if curr < support * 0.999 and ind.volume_ratio >= 1.5:
            confidence = min(65 + ind.volume_ratio * 8, 85)
            return PatternResult("Rectangle Breakdown", "SHORT", confidence,
                                 f"Rectangle breakdown below ${support:.2f} — vol={ind.volume_ratio:.1f}x")
        return None

    def detect_triple_top(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 40:
            return None
        highs = df["high"].values[-40:]
        closes = df["close"].values
        # Find three peaks within 1% of each other, each separated by at least 5 bars
        peak_indices = []
        for i in range(3, len(highs) - 3):
            if highs[i] == highs[max(0, i-3):i+4].max():
                peak_indices.append(i)
        if len(peak_indices) < 3:
            return None
        # Use last three peaks
        p1, p2, p3 = peak_indices[-3], peak_indices[-2], peak_indices[-1]
        if p2 - p1 < 5 or p3 - p2 < 5:
            return None
        h1, h2, h3 = highs[p1], highs[p2], highs[p3]
        avg_high = (h1 + h2 + h3) / 3
        if max(h1, h2, h3) / avg_high > 1.01:
            return None
        # Neckline: minimum low between peaks
        neckline = df["low"].values[-40:][p1:p3+1].min()
        if closes[-1] > neckline * 1.002:
            return None
        confidence = min(70 + ind.volume_ratio * 5, 85)
        return PatternResult("Triple Top", "SHORT", confidence,
                             f"Triple top at ${avg_high:.2f} — breakdown below neckline ${neckline:.2f}")

    def detect_triple_bottom(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 40:
            return None
        lows = df["low"].values[-40:]
        closes = df["close"].values
        # Find three troughs within 1% of each other
        trough_indices = []
        for i in range(3, len(lows) - 3):
            if lows[i] == lows[max(0, i-3):i+4].min():
                trough_indices.append(i)
        if len(trough_indices) < 3:
            return None
        t1, t2, t3 = trough_indices[-3], trough_indices[-2], trough_indices[-1]
        if t2 - t1 < 5 or t3 - t2 < 5:
            return None
        l1, l2, l3 = lows[t1], lows[t2], lows[t3]
        avg_low = (l1 + l2 + l3) / 3
        if avg_low / min(l1, l2, l3) > 1.01:
            return None
        # Neckline: maximum high between troughs
        neckline = df["high"].values[-40:][t1:t3+1].max()
        if closes[-1] < neckline * 0.998:
            return None
        confidence = min(70 + ind.volume_ratio * 5, 85)
        return PatternResult("Triple Bottom", "LONG", confidence,
                             f"Triple bottom at ${avg_low:.2f} — breakout above neckline ${neckline:.2f}")

    def detect_quasimodo(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 20:
            return None
        highs = df["high"].values[-20:]
        lows = df["low"].values[-20:]
        closes = df["close"].values
        # Bearish QM: left shoulder → head (highest) → right shoulder (lower than head but higher than left)
        # then breaks below the neckline (trough between head and right shoulder)
        head_idx = int(np.argmax(highs))
        if head_idx < 3 or head_idx > len(highs) - 3:
            return None
        left_sh = highs[:head_idx].max()
        right_sh_arr = highs[head_idx+1:]
        if len(right_sh_arr) == 0:
            return None
        right_sh = right_sh_arr.max()
        if not (highs[head_idx] > left_sh and highs[head_idx] > right_sh and right_sh > left_sh):
            return None
        # Neckline: lowest low between head and right shoulder
        neckline = lows[head_idx:].min()
        if closes[-1] > neckline * 1.002:
            return None
        confidence = 72
        if ind.rsi > 55:
            confidence += 5
        return PatternResult("Quasimodo Bearish", "SHORT", min(confidence, 82),
                             f"QM pattern: head=${highs[head_idx]:.2f}, neckline=${neckline:.2f} broken")

    # ─────────────────────────────────────────────────────────
    # NEW ICT / SMART MONEY CONCEPTS
    # ─────────────────────────────────────────────────────────

    def detect_breaker_block(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 15:
            return None
        atr = ind.atr if ind.atr > 0 else 1
        curr_close = df.iloc[-1]["close"]
        # Bullish Breaker: a prior bearish OB that was broken (price moved strongly through it)
        # and now price returns to it as support
        for i in range(-12, -4):
            ob = df.iloc[i]
            if ob["close"] >= ob["open"]:
                continue  # Need a bearish candle as the original OB
            # Was there a strong bullish impulse after that broke through this zone?
            subsequent = df.iloc[i+1:i+5]
            if len(subsequent) < 3:
                continue
            max_close_after = subsequent["close"].max()
            if max_close_after < ob["high"] * 1.002:
                continue  # Didn't break through
            # Price now returning to the broken OB zone (now support)
            ob_high = float(ob["high"])
            ob_low = float(ob["low"])
            if ob_low * 0.997 <= curr_close <= ob_high * 1.005:
                confidence = 82
                if ind.ema9 > ind.ema21:
                    confidence = min(confidence + 5, 90)
                return PatternResult("Breaker Block Bullish", "LONG", confidence,
                                     f"Bullish breaker block: broken bearish OB now support at ${ob_low:.2f}–${ob_high:.2f}")
        # Bearish Breaker: a prior bullish OB broken to the downside, now acting as resistance
        for i in range(-12, -4):
            ob = df.iloc[i]
            if ob["close"] <= ob["open"]:
                continue
            subsequent = df.iloc[i+1:i+5]
            if len(subsequent) < 3:
                continue
            min_close_after = subsequent["close"].min()
            if min_close_after > ob["low"] * 0.998:
                continue
            ob_high = float(ob["high"])
            ob_low = float(ob["low"])
            if ob_low * 0.995 <= curr_close <= ob_high * 1.003:
                confidence = 82
                if ind.ema9 < ind.ema21:
                    confidence = min(confidence + 5, 90)
                return PatternResult("Breaker Block Bearish", "SHORT", confidence,
                                     f"Bearish breaker block: broken bullish OB now resistance at ${ob_low:.2f}–${ob_high:.2f}")
        return None

    def detect_liquidity_pool(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 20:
            return None
        curr = df.iloc[-1]
        # Equal highs = stop cluster above (sell-side liquidity)
        recent_highs = df["high"].iloc[-20:-1]
        max_high = recent_highs.max()
        # Count highs within 0.2% of the maximum
        equal_highs = (recent_highs >= max_high * 0.998).sum()
        if equal_highs >= 2 and curr["close"] > max_high * 1.001:
            # Price swept above equal highs then could reverse
            confidence = 75 + min(equal_highs * 3, 12)
            if ind.rsi > 65:
                confidence = min(confidence + 8, 90)
            return PatternResult("Liquidity Sweep Bearish", "SHORT", min(confidence, 88),
                                 f"Buy-side liquidity swept: {equal_highs} equal highs at ${max_high:.2f} breached — reversal likely")
        # Equal lows = stop cluster below (buy-side liquidity)
        recent_lows = df["low"].iloc[-20:-1]
        min_low = recent_lows.min()
        equal_lows = (recent_lows <= min_low * 1.002).sum()
        if equal_lows >= 2 and curr["close"] < min_low * 0.999:
            confidence = 75 + min(equal_lows * 3, 12)
            if ind.rsi < 35:
                confidence = min(confidence + 8, 90)
            return PatternResult("Liquidity Sweep Bullish", "LONG", min(confidence, 88),
                                 f"Sell-side liquidity swept: {equal_lows} equal lows at ${min_low:.2f} breached — reversal likely")
        return None

    def detect_kill_zone(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 5:
            return None
        try:
            from utils import get_current_ist_time
            et_now = get_current_ist_time()  # aliased to ET
            et_hour = et_now.hour
            et_minute = et_now.minute
            # NY Open Kill Zone: 9:30–10:00 ET
            ny_open_kz = (et_hour == 9 and 30 <= et_minute <= 59) or (et_hour == 10 and et_minute <= 30)
            # London Close Kill Zone: 11:00–12:00 ET
            london_close_kz = (et_hour == 11) or (et_hour == 12 and et_minute == 0)
            # London Open Kill Zone: 2:00–5:00 AM ET (pre-market, skip for intraday)
            if not (ny_open_kz or london_close_kz):
                return None
            zone_name = "NY Open" if ny_open_kz else "London Close"
            curr = df.iloc[-1]
            direction = "LONG" if curr["close"] > curr["open"] else "SHORT"
            confidence = 78
            if ind.volume_ratio >= 1.5:
                confidence = min(confidence + 8, 88)
            # Name matches ICT_PATTERNS scoring set
            pattern_name = f"Kill Zone {direction.title()}"  # "Kill Zone Bullish" or "Kill Zone Bearish"
            return PatternResult(pattern_name, direction, confidence,
                                 f"ICT {zone_name} kill zone — high-probability institutional move window")
        except Exception:
            return None

    def detect_judas_swing(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 12:
            return None
        # First 6 bars form the early session
        early = df.iloc[:6]
        # Subsequent bars
        later = df.iloc[6:]
        if len(later) < 3:
            return None
        early_high = early["high"].max()
        early_low = early["low"].min()
        early_range = early_high - early_low
        if early_range <= 0:
            return None
        atr = ind.atr if ind.atr > 0 else 1
        # Bullish Judas: spike into the first 2 bars' low (the false sweep), then recovery above early high
        # early_spike_low = extreme of the first 2 bars (the false breakdown candle)
        early_spike_low = early.iloc[:2]["low"].min()
        later_close = df.iloc[-1]["close"]
        if early_spike_low < early_low * 0.998 and later_close > early_high:
            if (later_close - early_spike_low) > atr:
                confidence = 80
                if ind.volume_ratio >= 1.5:
                    confidence = min(confidence + 7, 90)
                return PatternResult("Judas Swing Bullish", "LONG", confidence,
                                     f"Judas Swing: early false breakdown below ${early_spike_low:.2f}, "
                                     f"now reversed above ${early_high:.2f}")
        # Bearish Judas: spike into the first 2 bars' high (the false breakout), then rejection
        early_spike_high = early.iloc[:2]["high"].max()
        if early_spike_high > early_high * 1.002 and later_close < early_low:
            if (early_spike_high - later_close) > atr:
                confidence = 80
                if ind.volume_ratio >= 1.5:
                    confidence = min(confidence + 7, 90)
                return PatternResult("Judas Swing Bearish", "SHORT", confidence,
                                     f"Judas Swing: early false breakup to ${early_spike_high:.2f}, "
                                     f"now reversed below ${early_low:.2f}")
        return None

    def detect_premium_discount(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 20:
            return None
        recent_high = df["high"].iloc[-20:].max()
        recent_low = df["low"].iloc[-20:].min()
        equilibrium = (recent_high + recent_low) / 2
        curr_close = df.iloc[-1]["close"]
        range_size = recent_high - recent_low
        if range_size <= 0:
            return None
        pct_from_eq = (curr_close - equilibrium) / range_size
        # Discount zone: price below equilibrium (below 40% of range)
        if pct_from_eq < -0.1:
            depth = abs(pct_from_eq) * 100
            confidence = min(60 + depth * 0.5, 78)
            if ind.rsi < 45:
                confidence = min(confidence + 8, 82)
            return PatternResult("Discount Zone (Long Bias)", "LONG", confidence,
                                 f"Price at discount ({depth:.1f}% below equilibrium ${equilibrium:.2f}) — long bias")
        # Premium zone: price above equilibrium (above 60% of range)
        if pct_from_eq > 0.1:
            height = pct_from_eq * 100
            confidence = min(60 + height * 0.5, 78)
            if ind.rsi > 55:
                confidence = min(confidence + 8, 82)
            return PatternResult("Premium Zone (Short Bias)", "SHORT", confidence,
                                 f"Price at premium ({height:.1f}% above equilibrium ${equilibrium:.2f}) — short bias")
        return None

    def detect_fibonacci_retracement(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 20:
            return None
        # Find recent swing high and low
        recent = df.iloc[-20:]
        swing_high = recent["high"].max()
        swing_low = recent["low"].min()
        swing_range = swing_high - swing_low
        if swing_range <= 0:
            return None
        curr_close = df.iloc[-1]["close"]
        # Fib levels
        fib_382 = swing_high - 0.382 * swing_range
        fib_500 = swing_high - 0.500 * swing_range
        fib_618 = swing_high - 0.618 * swing_range
        tolerance = swing_range * 0.02  # 2% tolerance
        # Bullish: upswing retracing, price near fib support
        high_idx = recent["high"].idxmax()
        low_idx = recent["low"].idxmin()
        # Determine if uptrend or downtrend based on which came first
        try:
            high_pos = recent.index.get_loc(high_idx)
            low_pos = recent.index.get_loc(low_idx)
        except Exception:
            return None
        if low_pos < high_pos:
            # Uptrend: look for retracement to fib support levels
            for fib_level, fib_name in [(fib_618, "61.8%"), (fib_500, "50%"), (fib_382, "38.2%")]:
                if abs(curr_close - fib_level) <= tolerance:
                    confidence = 68 if fib_name == "61.8%" else (65 if fib_name == "50%" else 62)
                    if ind.rsi < 50:
                        confidence = min(confidence + 8, 82)
                    return PatternResult(f"Fib Retracement {fib_name} Support", "LONG", confidence,
                                         f"Price at Fib {fib_name} retracement (${fib_level:.2f}) of upswing — long entry")
        else:
            # Downtrend: look for retracement to fib resistance levels
            fib_382_dn = swing_low + 0.382 * swing_range
            fib_500_dn = swing_low + 0.500 * swing_range
            fib_618_dn = swing_low + 0.618 * swing_range
            for fib_level, fib_name in [(fib_618_dn, "61.8%"), (fib_500_dn, "50%"), (fib_382_dn, "38.2%")]:
                if abs(curr_close - fib_level) <= tolerance:
                    confidence = 68 if fib_name == "61.8%" else (65 if fib_name == "50%" else 62)
                    if ind.rsi > 50:
                        confidence = min(confidence + 8, 82)
                    return PatternResult(f"Fib Retracement {fib_name} Resistance", "SHORT", confidence,
                                         f"Price at Fib {fib_name} retracement (${fib_level:.2f}) of downswing — short entry")
        return None

    # ─────────────────────────────────────────────────────────
    # NEW INDICATOR-BASED SIGNALS
    # ─────────────────────────────────────────────────────────

    def detect_adx_trend(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 15:
            return None
        if ind.adx < 20:
            return None  # Choppy market — avoid
        if ind.adx < 25:
            return None  # Not strong enough
        plus_di = ind.adx_plus_di if ind.adx_plus_di > 0 else ind.plus_di
        minus_di = ind.adx_minus_di if ind.adx_minus_di > 0 else ind.minus_di
        confidence = min(60 + ind.adx * 0.8, 88)
        if plus_di > minus_di:
            return PatternResult("ADX Trend Long", "LONG", confidence,
                                 f"ADX={ind.adx:.1f} (trending), +DI({plus_di:.1f}) > -DI({minus_di:.1f}) — bullish trend")
        elif minus_di > plus_di:
            return PatternResult("ADX Trend Short", "SHORT", confidence,
                                 f"ADX={ind.adx:.1f} (trending), -DI({minus_di:.1f}) > +DI({plus_di:.1f}) — bearish trend")
        return None

    def detect_ichimoku_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 52:
            return None
        if ind.ichimoku_tenkan == 0 or ind.ichimoku_kijun == 0:
            return None
        curr_close = df.iloc[-1]["close"]
        cloud_top = max(ind.ichimoku_senkou_a, ind.ichimoku_senkou_b)
        cloud_bot = min(ind.ichimoku_senkou_a, ind.ichimoku_senkou_b)
        # Bullish: price above cloud, Tenkan > Kijun
        if curr_close > cloud_top and ind.ichimoku_tenkan > ind.ichimoku_kijun:
            cloud_dist = (curr_close - cloud_top) / max(cloud_top, 0.01) * 100
            confidence = min(70 + cloud_dist * 2, 88)
            return PatternResult("Ichimoku Bull", "LONG", confidence,
                                 f"Ichimoku: price above cloud, Tenkan({ind.ichimoku_tenkan:.2f}) > Kijun({ind.ichimoku_kijun:.2f})")
        # Bearish: price below cloud, Tenkan < Kijun
        if curr_close < cloud_bot and ind.ichimoku_tenkan < ind.ichimoku_kijun:
            cloud_dist = (cloud_bot - curr_close) / max(cloud_bot, 0.01) * 100
            confidence = min(70 + cloud_dist * 2, 88)
            return PatternResult("Ichimoku Bear", "SHORT", confidence,
                                 f"Ichimoku: price below cloud, Tenkan({ind.ichimoku_tenkan:.2f}) < Kijun({ind.ichimoku_kijun:.2f})")
        return None

    def detect_keltner_squeeze(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 25:
            return None
        if ind.keltner_upper == 0 or ind.bb_upper == 0:
            return None
        curr_close = df.iloc[-1]["close"]
        # Squeeze released: prior bar had BB inside KC, current bar BB expands beyond KC
        curr_bb_in_kc = ind.bb_upper <= ind.keltner_upper and ind.bb_lower >= ind.keltner_lower
        # Check if squeeze was active in prior bars (last 1-5 bars had BB inside KC)
        squeeze_was_active = False
        if "bb_upper" in df.columns and "_kc_upper" in df.columns:
            for lookback_i in range(1, 6):
                if lookback_i >= len(df):
                    break
                prior = df.iloc[-lookback_i - 1]
                p_bb_upper = float(prior.get("bb_upper", ind.bb_upper + 1))
                p_kc_upper = float(prior.get("_kc_upper", ind.keltner_upper))
                p_bb_lower = float(prior.get("bb_lower", ind.bb_lower - 1))
                p_kc_lower = float(prior.get("_kc_lower", ind.keltner_lower))
                if p_bb_upper <= p_kc_upper and p_bb_lower >= p_kc_lower:
                    squeeze_was_active = True
                    break
        else:
            # Without column data, assume squeeze was active if current bands are tight
            band_width = ind.bb_upper - ind.bb_lower
            kc_width = ind.keltner_upper - ind.keltner_lower
            squeeze_was_active = band_width < kc_width * 1.1
        if not squeeze_was_active:
            return None
        # Now current bar should be breaking OUT of the squeeze (BB expanding beyond KC)
        breaking_up   = (not curr_bb_in_kc) and curr_close > ind.keltner_upper
        breaking_down = (not curr_bb_in_kc) and curr_close < ind.keltner_lower
        # Also fire if price closes beyond BB bands while squeeze was just released
        if not (breaking_up or breaking_down):
            breaking_up   = curr_close > ind.bb_upper and not curr_bb_in_kc
            breaking_down = curr_close < ind.bb_lower and not curr_bb_in_kc
        if breaking_up:
            confidence = 80 + min(ind.rvol * 3, 10)
            return PatternResult("Keltner Squeeze Breakout Long", "LONG", min(confidence, 90),
                                 f"Keltner squeeze breakout LONG: squeeze released, BB expanding above KC")
        elif breaking_down:
            confidence = 80 + min(ind.rvol * 3, 10)
            return PatternResult("Keltner Squeeze Breakout Short", "SHORT", min(confidence, 90),
                                 f"Keltner squeeze breakout SHORT: squeeze released, BB expanding below KC")
        return None

    def detect_donchian_breakout(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 21:
            return None
        if ind.donchian_upper == 0:
            return None
        curr_close = df.iloc[-1]["close"]
        prev_close = df.iloc[-2]["close"]
        # Breakout above Donchian upper
        if curr_close > ind.donchian_upper and prev_close <= ind.donchian_upper:
            confidence = min(68 + ind.volume_ratio * 6, 85)
            return PatternResult("Donchian Breakout Long", "LONG", confidence,
                                 f"Donchian 20-bar breakout above ${ind.donchian_upper:.2f}")
        # Breakdown below Donchian lower
        if curr_close < ind.donchian_lower and prev_close >= ind.donchian_lower:
            confidence = min(68 + ind.volume_ratio * 6, 85)
            return PatternResult("Donchian Breakout Short", "SHORT", confidence,
                                 f"Donchian 20-bar breakdown below ${ind.donchian_lower:.2f}")
        return None

    def detect_chandelier_exit_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 23:
            return None
        if ind.chandelier_long == 0:
            return None
        curr_close = df.iloc[-1]["close"]
        prev_close = df.iloc[-2]["close"]
        # Long position exit: price drops below chandelier_long
        if curr_close < ind.chandelier_long and prev_close >= ind.chandelier_long:
            return PatternResult("Chandelier Exit Long", "SHORT", 72,
                                 f"Chandelier exit: close ${curr_close:.2f} dropped below long stop ${ind.chandelier_long:.2f}")
        # Short position exit: price rises above chandelier_short
        if ind.chandelier_short > 0 and curr_close > ind.chandelier_short and prev_close <= ind.chandelier_short:
            return PatternResult("Chandelier Exit Short", "LONG", 72,
                                 f"Chandelier exit: close ${curr_close:.2f} rose above short stop ${ind.chandelier_short:.2f}")
        return None

    def detect_stoch_rsi_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 20:
            return None
        if "_srsi_k" not in df.columns:
            return None
        prev_k = float(df["_srsi_k"].iloc[-2] or 50)
        curr_k = ind.stoch_rsi_k
        # Cross above 20 from below = LONG
        if prev_k < 20 and curr_k >= 20:
            confidence = min(65 + (curr_k - prev_k) * 0.5, 80)
            return PatternResult("StochRSI Cross Long", "LONG", confidence,
                                 f"StochRSI crossed above 20 ({prev_k:.1f}→{curr_k:.1f}) — oversold reversal")
        # Cross below 80 from above = SHORT
        if prev_k > 80 and curr_k <= 80:
            confidence = min(65 + (prev_k - curr_k) * 0.5, 80)
            return PatternResult("StochRSI Cross Short", "SHORT", confidence,
                                 f"StochRSI crossed below 80 ({prev_k:.1f}→{curr_k:.1f}) — overbought reversal")
        return None

    def detect_cci_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 22:
            return None
        if "_cci" not in df.columns:
            return None
        prev_cci = float(df["_cci"].iloc[-2] or 0)
        curr_cci = ind.cci
        # Cross above -100 from below = LONG
        if prev_cci < -100 and curr_cci >= -100:
            confidence = min(65 + abs(curr_cci - prev_cci) * 0.1, 80)
            return PatternResult("CCI Cross Long", "LONG", confidence,
                                 f"CCI crossed above -100 ({prev_cci:.0f}→{curr_cci:.0f}) — bullish momentum")
        # Cross below +100 from above = SHORT
        if prev_cci > 100 and curr_cci <= 100:
            confidence = min(65 + abs(prev_cci - curr_cci) * 0.1, 80)
            return PatternResult("CCI Cross Short", "SHORT", confidence,
                                 f"CCI crossed below +100 ({prev_cci:.0f}→{curr_cci:.0f}) — bearish momentum")
        return None

    def detect_williams_r_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 15:
            return None
        if "_willr" not in df.columns:
            return None
        prev_wr = float(df["_willr"].iloc[-2] or -50)
        curr_wr = ind.williams_r
        # Cross above -80 from below = LONG (leaving oversold)
        if prev_wr < -80 and curr_wr >= -80:
            confidence = min(65 + abs(curr_wr - prev_wr) * 0.3, 80)
            return PatternResult("Williams %R Long", "LONG", confidence,
                                 f"Williams %R crossed above -80 ({prev_wr:.1f}→{curr_wr:.1f}) — oversold exit")
        # Cross below -20 from above = SHORT (leaving overbought)
        if prev_wr > -20 and curr_wr <= -20:
            confidence = min(65 + abs(prev_wr - curr_wr) * 0.3, 80)
            return PatternResult("Williams %R Short", "SHORT", confidence,
                                 f"Williams %R crossed below -20 ({prev_wr:.1f}→{curr_wr:.1f}) — overbought exit")
        return None

    def detect_pivot_bounce(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 5 or ind.pivot_pp == 0:
            return None
        curr_close = df.iloc[-1]["close"]
        curr = df.iloc[-1]
        atr = ind.atr if ind.atr > 0 else 1
        tol = atr * 0.3  # Tolerance for "near" pivot
        # Bullish: near S1 or S2 with bullish candle
        for level, name in [(ind.pivot_s1, "S1"), (ind.pivot_s2, "S2")]:
            if level > 0 and abs(curr_close - level) <= tol:
                if curr["close"] > curr["open"]:
                    confidence = 68 if name == "S1" else 72
                    if ind.rsi < 45:
                        confidence = min(confidence + 7, 82)
                    return PatternResult(f"Pivot {name} Bounce Long", "LONG", confidence,
                                         f"Bullish bounce at pivot {name} (${level:.2f})")
        # Bearish: near R1 or R2 with bearish candle
        for level, name in [(ind.pivot_r1, "R1"), (ind.pivot_r2, "R2")]:
            if level > 0 and abs(curr_close - level) <= tol:
                if curr["close"] < curr["open"]:
                    confidence = 68 if name == "R1" else 72
                    if ind.rsi > 55:
                        confidence = min(confidence + 7, 82)
                    return PatternResult(f"Pivot {name} Rejection Short", "SHORT", confidence,
                                         f"Bearish rejection at pivot {name} (${level:.2f})")
        return None

    def detect_vwap_band_signal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 5 or ind.vwap_upper_2 == 0:
            return None
        curr_close = df.iloc[-1]["close"]
        atr = ind.atr if ind.atr > 0 else 1
        tol = atr * 0.3
        # Price at VWAP + 2std = SHORT (overbought vs VWAP)
        if abs(curr_close - ind.vwap_upper_2) <= tol:
            confidence = 72
            if ind.rsi > 65:
                confidence = min(confidence + 8, 82)
            return PatternResult("VWAP +2σ Rejection", "SHORT", confidence,
                                 f"Price at VWAP+2σ (${ind.vwap_upper_2:.2f}) — mean-reversion SHORT")
        # Price at VWAP - 2std = LONG (oversold vs VWAP)
        if ind.vwap_lower_2 > 0 and abs(curr_close - ind.vwap_lower_2) <= tol:
            confidence = 72
            if ind.rsi < 35:
                confidence = min(confidence + 8, 82)
            return PatternResult("VWAP -2σ Bounce", "LONG", confidence,
                                 f"Price at VWAP-2σ (${ind.vwap_lower_2:.2f}) — mean-reversion LONG")
        return None

    def detect_poc_reaction(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 10 or ind.poc == 0:
            return None
        curr_close = df.iloc[-1]["close"]
        atr = ind.atr if ind.atr > 0 else 1
        tol = atr * 0.4
        if abs(curr_close - ind.poc) <= tol:
            # Direction based on price vs prior bar
            prev_close = df.iloc[-2]["close"]
            direction = "LONG" if curr_close > prev_close else "SHORT"
            return PatternResult("POC Reaction", direction, 65,
                                 f"Price at POC ${ind.poc:.2f} (high-volume node) — expect reaction or magnet")
        return None

    def detect_rvol_confirmation(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        if len(df) < 5:
            return None
        if ind.rvol < 2.0:
            return None
        curr = df.iloc[-1]
        body = abs(curr["close"] - curr["open"])
        atr = ind.atr if ind.atr > 0 else 1
        if body < 0.5 * atr:
            return None  # Need a meaningful candle with the surge
        direction = "LONG" if curr["close"] > curr["open"] else "SHORT"
        confidence = min(65 + (ind.rvol - 2.0) * 8, 88)
        return PatternResult("RVOL Momentum Confirmed", direction, confidence,
                             f"RVOL={ind.rvol:.1f}x with directional candle — momentum confirmed")

    # ─────────────────────────────────────────────────────────
    # HARMONIC + SMART MONEY INTEGRATION
    # ─────────────────────────────────────────────────────────

    def detect_gap_and_go(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Gap & Go: stock gaps up/down at open and first bar continues in gap direction.
        High-probability US intraday momentum pattern — trade WITH the gap.
        """
        if len(df) < 3:
            return None
        try:
            prev_close = float(df["close"].iloc[-3])
            open_price = float(df["open"].iloc[-2])   # first bar of session proxy
            first_close = float(df["close"].iloc[-2])
            gap_pct = (open_price - prev_close) / prev_close * 100

            if gap_pct > 0.75 and first_close > open_price:   # Gap up + bullish first bar
                conf = min(70 + abs(gap_pct) * 5, 90)
                return PatternResult(
                    "Gap & Go Long",
                    "LONG",
                    round(conf),
                    f"Gap up {gap_pct:+.1f}% at open — first bar confirms momentum, trade long"
                )
            elif gap_pct < -0.75 and first_close < open_price:  # Gap down + bearish first bar
                conf = min(70 + abs(gap_pct) * 5, 90)
                return PatternResult(
                    "Gap & Go Short",
                    "SHORT",
                    round(conf),
                    f"Gap down {gap_pct:+.1f}% at open — first bar confirms momentum, trade short"
                )
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
        return None

    def detect_gap_fill(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Gap Fill: stock gaps up but first bar fails (closes below open) or vice versa.
        Mean-reversion play — price tends to fill the gap back to prior close.
        """
        if len(df) < 3:
            return None
        try:
            prev_close = float(df["close"].iloc[-3])
            open_price = float(df["open"].iloc[-2])
            first_close = float(df["close"].iloc[-2])
            current    = float(df["close"].iloc[-1])
            gap_pct = (open_price - prev_close) / prev_close * 100

            # Gap up but first bar fails → fill down toward prev close
            if gap_pct > 1.0 and first_close < open_price * 0.998 and current < first_close:
                conf = min(65 + abs(gap_pct) * 4, 85)
                return PatternResult(
                    "Gap Fill Short",
                    "SHORT",
                    round(conf),
                    f"Gap up {gap_pct:+.1f}% failed — expect fill toward ${prev_close:.2f}"
                )
            # Gap down but first bar fails → fill up toward prev close
            elif gap_pct < -1.0 and first_close > open_price * 1.002 and current > first_close:
                conf = min(65 + abs(gap_pct) * 4, 85)
                return PatternResult(
                    "Gap Fill Long",
                    "LONG",
                    round(conf),
                    f"Gap down {gap_pct:+.1f}% failed — expect fill toward ${prev_close:.2f}"
                )
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
        return None

    def _run_harmonic_patterns(self, df: pd.DataFrame) -> List[PatternResult]:
        """Run harmonic pattern detector and convert results."""
        results = []
        try:
            from harmonic_patterns import get_harmonic_detector
            hd = get_harmonic_detector()
            for hr in hd.scan_all(df):
                results.append(hr.to_pattern_result())
        except Exception as e:
            logger.debug(f"Harmonic patterns failed: {e}")
        return results

    def _run_smart_money_advanced(self, df: pd.DataFrame) -> List[PatternResult]:
        """Run institutional smart money detector."""
        results = []
        try:
            from smart_money_advanced import get_smart_money_scanner
            scanner = get_smart_money_scanner()
            current_price = float(df["close"].values[-1]) if len(df) else 0
            results = scanner.scan(df, current_price=current_price)
        except Exception as e:
            logger.debug(f"Smart money advanced failed: {e}")
        return results

    # ─────────────────────────────────────────────────────────
    # SMT DIVERGENCE (Smart Money Technique)
    # ─────────────────────────────────────────────────────────

    def detect_smt_divergence(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        SMT-style divergence: price makes new high/low but OBV diverges.
        Institutional confirmation or warning sign.
        """
        if len(df) < 20:
            return None
        try:
            c = df["close"]
            # Compute OBV series from close/volume
            obv_s = (df["volume"] * df["close"].diff().apply(
                lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
            )).cumsum()

            recent = 15
            price_high = c.iloc[-recent:].max()
            price_low  = c.iloc[-recent:].min()
            obv_high   = obv_s.iloc[-recent:].max()
            obv_low    = obv_s.iloc[-recent:].min()
            price_now  = float(c.iloc[-1])
            obv_now    = float(obv_s.iloc[-1])

            # Bearish SMT: price near recent high but OBV diverging down
            near_high = price_now >= price_high * 0.995
            obv_diverging_down = obv_now < obv_high * 0.97
            if near_high and obv_diverging_down:
                return PatternResult(
                    "SMT Divergence Bearish", "SHORT", 72,
                    "Price near high but OBV diverging — institutional distribution signal"
                )

            # Bullish SMT: price near recent low but OBV diverging up
            near_low = price_now <= price_low * 1.005
            obv_diverging_up = obv_now > obv_low * 1.03
            if near_low and obv_diverging_up:
                return PatternResult(
                    "SMT Divergence Bullish", "LONG", 72,
                    "Price near low but OBV diverging — institutional accumulation signal"
                )
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
        return None

    # --------------------------------------------------------
    # COMPOSITE SCORE
    # --------------------------------------------------------

    def _compute_composite_score(
        self, patterns: List[PatternResult], ind: IndicatorSet
    ) -> Dict:
        """
        Composite score following Grok's recommended category weights:
          ICT/SMC patterns  : 35 pts max
          Chart patterns    : 20 pts max
          Momentum/Volume   : 25 pts max
          (Earnings/RS bonus: handled in signal_generator.py — 20 pts max)
        Total base max = 80 pts → normalized to 100 by ×1.25.
        HVN/LVN proximity adds a bonus on top (fast-move / S&R confirmation).
        """

        # ── ICT / Smart Money pattern set (35 pts max) ────────────────────
        ICT_PATTERNS = {
            "Bullish FVG", "Bearish FVG",
            "Bullish Order Block", "Bearish Order Block",
            "BOS — Higher High (Trend Continues)", "BOS — Lower Low (Trend Continues)",
            "ChoCH — Bullish Reversal", "ChoCH — Bearish Reversal",
            "Breaker Block Bullish", "Breaker Block Bearish",
            "Bullish Liquidity Sweep", "Bearish Liquidity Sweep",
            "Liquidity Sweep Bullish", "Liquidity Sweep Bearish",
            "Judas Swing Bullish", "Judas Swing Bearish",
            "OTE Bullish (ICT)", "OTE Bearish (ICT)",
            "Power of 3 — Bullish Distribution", "Power of 3 — Bearish Distribution",
            "Wyckoff Spring (Bullish)", "Wyckoff Upthrust (Bearish)",
            "Wyckoff Accumulation Base", "Wyckoff Distribution Top",
            "Liquidity Pool Bullish", "Liquidity Pool Bearish",
            "SMT Divergence Bullish", "SMT Divergence Bearish",
            "Kill Zone Bullish", "Kill Zone Bearish",
            "Gap & Go Long", "Gap & Go Short",
            "Gamma Squeeze Setup (Bullish)", "Gamma Squeeze Setup (Bearish)",
            "Demand Zone (Long Bias)", "Supply Zone (Short Bias)",
            "Mitigation Block Bullish", "Mitigation Block Bearish",
        }

        # ── Chart / Technical patterns (20 pts max) ───────────────────────
        CHART_PATTERNS = {
            "Double Bottom", "Double Top",
            "Triple Bottom", "Triple Top",
            "Head & Shoulders", "Inverse Head & Shoulders",
            "Cup and Handle",
            "Ascending Triangle", "Descending Triangle", "Symmetrical Triangle",
            "Bull Flag", "Bear Flag",
            "Rectangle Breakout", "Rectangle Breakdown", "Rounding Bottom",
            "Quasimodo Bullish",
            "Volume Climax Reversal Long", "Volume Climax Reversal Short",
            "Broadening Formation Long", "Broadening Formation Short",
            "Bullish Engulfing", "Bearish Engulfing",
            "Doji Reversal", "Hammer", "Shooting Star",
            "Inverted Hammer", "Hanging Man",
            "Bullish Marubozu", "Bearish Marubozu",
            "Dragonfly Doji", "Gravestone Doji", "Spinning Top",
            "Three Inside Up", "Three Inside Down",
            "Bullish Abandoned Baby", "Bearish Abandoned Baby",
            "Bullish Kicker", "Bearish Kicker",
            "Mat Hold (Bullish)",
            "Full EMA Stack Bullish", "Full EMA Stack Bearish",
            "Heikin Ashi Bull Trend", "Heikin Ashi Bear Trend",
            "Bat Bullish", "Bat Bearish",
            "Gartley Bullish (222)", "Gartley Bearish (222)",
            "Butterfly Bullish", "Butterfly Bearish",
            "Crab Bullish", "Crab Bearish",
            "Three Drives Bullish", "Three Drives Bearish",
            "Quasimodo Bullish", "Quasimodo Bearish",
            "Gap Fill Long", "Gap Fill Short",
            "Keltner Squeeze Breakout Long", "Keltner Squeeze Breakout Short",
            "Donchian Breakout Long", "Donchian Breakout Short",
            "ADX Trend Long", "ADX Trend Short",
            "Ichimoku Bull", "Ichimoku Bear",
        }

        # ADX quality multiplier: choppy → reduce, trending → boost
        adx_mult = 0.7 if ind.adx < 20 else (1.1 if ind.adx > 30 else 1.0)

        # ── Category 1: ICT/SMC patterns (cap 35) ─────────────────────────
        ict_long = 0.0
        ict_short = 0.0

        # ── Category 2: Chart patterns (cap 20) ───────────────────────────
        chart_long = 0.0
        chart_short = 0.0

        for p in patterns:
            conf_norm = (p.confidence * adx_mult) / 100.0   # 0.0–1.1 range
            if p.name in ICT_PATTERNS:
                contrib = conf_norm * 35
                if p.direction == "LONG":
                    ict_long += contrib
                elif p.direction == "SHORT":
                    ict_short += contrib
            elif p.name in CHART_PATTERNS:
                contrib = conf_norm * 10   # Grok: chart patterns = 10 pts max
                if p.direction == "LONG":
                    chart_long += contrib
                elif p.direction == "SHORT":
                    chart_short += contrib
            else:
                # Unclassified (e.g. RVOL Momentum Confirmed) → half-weight chart bucket
                contrib = conf_norm * 5
                if p.direction == "LONG":
                    chart_long += contrib
                elif p.direction == "SHORT":
                    chart_short += contrib

        ict_long  = min(ict_long,  35.0)
        ict_short = min(ict_short, 35.0)
        chart_long  = min(chart_long,  10.0)
        chart_short = min(chart_short, 10.0)

        # ── Category 3: Momentum / Volume indicators (cap 25) ─────────────
        rvol    = getattr(ind, "rvol", ind.volume_ratio)
        stoch_k = getattr(ind, "stoch_rsi_k", 50)
        cci     = getattr(ind, "cci", 0)
        wr      = getattr(ind, "williams_r", -50)
        bb_pb   = getattr(ind, "bb_pct_b", 0.5)
        plus_di  = getattr(ind, "adx_plus_di", getattr(ind, "plus_di",  0))
        minus_di = getattr(ind, "adx_minus_di", getattr(ind, "minus_di", 0))
        tenkan   = getattr(ind, "ichimoku_tenkan",   0)
        kijun    = getattr(ind, "ichimoku_kijun",    0)
        senkou_a = getattr(ind, "ichimoku_senkou_a", 0)
        senkou_b = getattr(ind, "ichimoku_senkou_b", 0)

        mom_long = 0.0
        mom_short = 0.0

        # RVOL: up to 8 pts (directional towards dominant side)
        if rvol >= 2.0:
            if ict_long + chart_long >= ict_short + chart_short:
                mom_long += 8
            else:
                mom_short += 8
        elif rvol >= 1.5:
            if ict_long + chart_long >= ict_short + chart_short:
                mom_long += 5
            else:
                mom_short += 5

        # RSI: up to 5 pts
        if ind.rsi < 35:
            mom_long += 5
        elif ind.rsi > 65:
            mom_short += 5

        # MACD histogram: up to 4 pts
        if ind.macd_hist > 0:
            mom_long += 4
        elif ind.macd_hist < 0:
            mom_short += 4

        # StochRSI: up to 5 pts
        if stoch_k < 25:
            mom_long += 5
        elif stoch_k > 75:
            mom_short += 5

        # CCI: up to 4 pts
        if cci < -100:
            mom_long += 4
        elif cci > 100:
            mom_short += 4

        # Williams %R: up to 4 pts
        if wr < -80:
            mom_long += 4
        elif wr > -20:
            mom_short += 4

        # Ichimoku cloud position: up to 5 pts
        if tenkan and kijun and senkou_a and senkou_b:
            cloud_top = max(senkou_a, senkou_b)
            cloud_bot = min(senkou_a, senkou_b)
            if tenkan > cloud_top and tenkan > kijun:
                mom_long += 5
            elif tenkan < cloud_bot and tenkan < kijun:
                mom_short += 5

        # BB %B extremes: up to 3 pts
        if bb_pb > 1.0:
            mom_short += 3
        elif bb_pb < 0.0:
            mom_long += 3

        mom_long  = min(mom_long,  25.0)
        mom_short = min(mom_short, 25.0)

        # ── Category 4: Regime alignment (Grok: 10 pts max) ──────────────
        # EMA full-stack alignment: 4 pts
        reg_long = 0.0
        reg_short = 0.0
        if ind.ema9 > ind.ema21 > ind.ema50:
            reg_long += 4
        elif ind.ema9 < ind.ema21 < ind.ema50:
            reg_short += 4

        # Supertrend direction: 3 pts
        if ind.supertrend_dir == 1:
            reg_long += 3
        else:
            reg_short += 3

        # ADX trending (not choppy): 3 pts for direction confirmation
        if ind.adx > 25:
            if plus_di > minus_di:
                reg_long += 3
            else:
                reg_short += 3

        reg_long  = min(reg_long,  10.0)
        reg_short = min(reg_short, 10.0)

        # ── Multi-factor ICT confluence bonus (adds within ICT 35 cap) ────
        elite_names = {p.name for p in patterns}
        has_ict_bull = bool({"Bullish FVG", "Bullish Order Block", "Breaker Block Bullish"} & elite_names)
        has_ict_bear = bool({"Bearish FVG", "Bearish Order Block", "Breaker Block Bearish"} & elite_names)
        high_rvol = rvol >= 1.8
        adx_rising = ind.adx > 25
        if has_ict_bull and adx_rising and high_rvol:
            ict_long  = min(ict_long  + 10, 35.0)
        if has_ict_bear and adx_rising and high_rvol:
            ict_short = min(ict_short + 10, 35.0)

        # Kill zone timing bonus (adds within chart 10 cap)
        has_kz = any("Kill Zone" in p.name for p in patterns)
        if has_kz:
            if ict_long + mom_long + reg_long >= ict_short + mom_short + reg_short:
                chart_long  = min(chart_long  + 3, 10.0)
            else:
                chart_short = min(chart_short + 3, 10.0)

        # ── HVN / LVN proximity bonus ──────────────────────────────────────
        # at_lvn: price in a low-volume node → fast move incoming (+6)
        # at_hvn: price at a high-volume node = strong S/R reaction
        at_hvn = getattr(ind, "at_hvn", False)
        at_lvn = getattr(ind, "at_lvn", False)

        hvn_long = hvn_short = 0.0
        if at_lvn:
            # LVN = fast move zone: boost the dominant side
            if ict_long + chart_long + mom_long + reg_long >= ict_short + chart_short + mom_short + reg_short:
                hvn_long = 5.0
            else:
                hvn_short = 5.0
        if at_hvn:
            # HVN = strong S/R: boost if momentum is coming FROM this level
            if ict_long + chart_long + mom_long + reg_long > ict_short + chart_short + mom_short + reg_short:
                hvn_long = 3.0   # bouncing off HVN support
            else:
                hvn_short = 3.0  # rejecting at HVN resistance

        # ── Totals: ICT(35) + Mom(25) + Chart(10) + Regime(10) = 80 max ──
        # ×1.25 maps 80 → 100; HVN/LVN bonus can push slightly above before cap
        raw_long  = ict_long  + chart_long  + mom_long  + reg_long  + hvn_long
        raw_short = ict_short + chart_short + mom_short + reg_short + hvn_short

        long_norm  = min(raw_long  * 1.25, 100.0)
        short_norm = min(raw_short * 1.25, 100.0)

        direction = "NEUTRAL"
        if long_norm > short_norm and long_norm >= 55:
            direction = "LONG"
        elif short_norm > long_norm and short_norm >= 55:
            direction = "SHORT"

        return {
            "long": round(long_norm, 1),
            "short": round(short_norm, 1),
            "direction": direction,
            "dominant": max(long_norm, short_norm),
        }

    # ─────────────────────────────────────────────────────────────────────
    # SUPPLY AND DEMAND ZONES
    # ─────────────────────────────────────────────────────────────────────
    def detect_supply_demand_zone(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Supply/Demand Zones: origin candle identification.
        - Demand Zone: base candle (small body, low volume) immediately before
          a strong bullish impulse leg. Price returning to that base = buy zone.
        - Supply Zone: base candle before a strong bearish impulse. Return = sell zone.
        """
        if len(df) < 20:
            return None
        try:
            closes = df["close"].values
            opens  = df["open"].values
            highs  = df["high"].values
            lows   = df["low"].values
            vols   = df["volume"].values
            atr = ind.atr if ind.atr > 0 else (closes[-1] * 0.005)
            curr_close = closes[-1]

            for i in range(-20, -4):
                body   = abs(closes[i] - opens[i])
                move   = abs(closes[i+1] - opens[i+1])
                # Impulse candle: body > 2× current ATR and > 2× base candle body
                if move < 2 * atr or move < 2 * max(body, 0.001):
                    continue

                zone_high = highs[i]
                zone_low  = lows[i]
                zone_mid  = (zone_high + zone_low) / 2

                # Demand Zone: impulse is bullish (up move), price returning from above
                if closes[i+1] > opens[i+1] and curr_close <= zone_high and curr_close >= zone_low:
                    # Price is retesting the demand zone
                    zone_width = zone_high - zone_low
                    confidence = min(72 + (move / atr) * 4, 86)
                    if ind.rsi < 50:
                        confidence = min(confidence + 5, 88)
                    return PatternResult(
                        "Demand Zone (Long Bias)", "LONG", confidence,
                        f"Demand Zone ${zone_low:.2f}–${zone_high:.2f}: "
                        f"base before {move/atr:.1f}× ATR impulse — retest buy"
                    )

                # Supply Zone: impulse is bearish (down move), price returning from below
                if closes[i+1] < opens[i+1] and curr_close >= zone_low and curr_close <= zone_high:
                    confidence = min(72 + (move / atr) * 4, 86)
                    if ind.rsi > 50:
                        confidence = min(confidence + 5, 88)
                    return PatternResult(
                        "Supply Zone (Short Bias)", "SHORT", confidence,
                        f"Supply Zone ${zone_low:.2f}–${zone_high:.2f}: "
                        f"base before {move/atr:.1f}× ATR impulse — retest sell"
                    )
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────────────────────────────
    # QUASIMODO BULLISH (inverse of the bearish already implemented)
    # ─────────────────────────────────────────────────────────────────────
    def detect_quasimodo_bullish(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Bullish Quasimodo (Left Shoulder > Right Shoulder > Head lowest).
        - Head: lowest low in the range
        - Left Shoulder: higher low before head
        - Right Shoulder: higher low after head (higher than head, lower than left shoulder)
        - Entry: when price breaks back above the neckline (local resistance between shoulders)
        """
        if len(df) < 25:
            return None
        try:
            lows  = df["low"].values
            highs = df["high"].values
            closes = df["close"].values
            n = len(lows)

            head_idx = int(np.argmin(lows[-20:]))  # deepest low in last 20 bars
            head_idx = n - 20 + head_idx

            if head_idx < 5 or head_idx > n - 4:
                return None

            left_shoulder_low  = lows[head_idx-5:head_idx].min()
            right_shoulder_low = lows[head_idx+1:].min()

            # Right shoulder must be higher than head but lower than left shoulder
            head_low = lows[head_idx]
            if not (head_low < right_shoulder_low < left_shoulder_low):
                return None

            # Neckline = local high between head and right shoulder (resistance to break)
            neckline = highs[head_idx:].max()
            # Price must break ABOVE neckline to confirm
            if closes[-1] < neckline * 0.998:
                return None

            confidence = 72
            if ind.rsi < 45:
                confidence += 5
            return PatternResult(
                "Quasimodo Bullish", "LONG", min(confidence, 82),
                f"Bullish QM: head=${head_low:.2f}, neckline=${neckline:.2f} broken above"
            )
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────────────────────────────
    # VOLUME CLIMAX REVERSAL
    # ─────────────────────────────────────────────────────────────────────
    def detect_volume_climax_reversal(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Volume Climax: extreme volume (≥4× avg) on a bar with a small body.
        Signals professional absorption — expect reversal or strong base.
        """
        if len(df) < 20:
            return None
        try:
            vols   = df["volume"].values
            closes = df["close"].values
            opens  = df["open"].values
            highs  = df["high"].values
            lows   = df["low"].values

            avg_vol = np.mean(vols[-20:-1])
            if avg_vol <= 0:
                return None

            curr_vol   = vols[-1]
            curr_body  = abs(closes[-1] - opens[-1])
            curr_range = highs[-1] - lows[-1]

            if curr_vol < 4 * avg_vol:
                return None  # not a climax volume bar

            # Small body relative to range = absorption (professionals absorbing)
            if curr_range <= 0 or curr_body / curr_range > 0.4:
                return None  # body too large — momentum bar, not absorption

            # Direction of climax: if prior trend was up, this is topping climax
            prior_trend_up = closes[-5] < closes[-2]
            confidence = min(72 + (curr_vol / avg_vol) * 2, 85)

            if prior_trend_up:
                return PatternResult(
                    "Volume Climax Reversal Short", "SHORT", confidence,
                    f"Climax volume {curr_vol/avg_vol:.1f}× avg + small body — exhaustion top"
                )
            else:
                return PatternResult(
                    "Volume Climax Reversal Long", "LONG", confidence,
                    f"Climax volume {curr_vol/avg_vol:.1f}× avg + small body — exhaustion bottom"
                )
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────────────────────────────
    # BROADENING FORMATION (MEGAPHONE) — Distribution / Expanding Volatility
    # ─────────────────────────────────────────────────────────────────────
    def detect_broadening_formation(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Broadening Formation (Megaphone): successive higher highs AND lower lows.
        Each swing is wider than the last → institutional distribution / indecision.
        5th touch of the upper/lower trendline is the highest-probability trade.
        """
        if len(df) < 20:
            return None
        try:
            highs  = df["high"].values[-20:]
            lows   = df["low"].values[-20:]
            closes = df["close"].values

            # Find swing highs/lows using pure numpy (order=2 neighbor comparison)
            hi_idx = [i for i in range(2, len(highs)-2)
                      if highs[i] >= highs[i-1] and highs[i] >= highs[i-2]
                      and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]]
            lo_idx = [i for i in range(2, len(lows)-2)
                      if lows[i] <= lows[i-1] and lows[i] <= lows[i-2]
                      and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]]

            if len(hi_idx) < 2 or len(lo_idx) < 2:
                return None

            # Expanding highs: each successive high is higher
            sw_highs = np.array([highs[i] for i in hi_idx])
            sw_lows  = np.array([lows[i]  for i in lo_idx])
            highs_expanding = all(sw_highs[i] < sw_highs[i+1] for i in range(len(sw_highs)-1))
            lows_expanding  = all(sw_lows[i]  > sw_lows[i+1]  for i in range(len(sw_lows)-1))

            if not (highs_expanding and lows_expanding):
                return None

            curr_close = closes[-1]
            last_sw_high = sw_highs[-1]
            last_sw_low  = sw_lows[-1]

            # 5th touch trade: at top of pattern → short; at bottom → long
            if abs(curr_close - last_sw_high) / last_sw_high < 0.005:
                confidence = 74
                if ind.rsi > 60:
                    confidence += 5
                return PatternResult(
                    "Broadening Formation Short", "SHORT", min(confidence, 82),
                    f"Megaphone: 5th touch upper bound ${last_sw_high:.2f} — short reversal"
                )
            if abs(curr_close - last_sw_low) / last_sw_low < 0.005:
                confidence = 74
                if ind.rsi < 40:
                    confidence += 5
                return PatternResult(
                    "Broadening Formation Long", "LONG", min(confidence, 82),
                    f"Megaphone: 5th touch lower bound ${last_sw_low:.2f} — long reversal"
                )
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────────────────────────────
    # MITIGATION BLOCK — Partially-tested Order Block acting as magnet
    # ─────────────────────────────────────────────────────────────────────
    def detect_mitigation_block(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Mitigation Block: an Order Block that was partially tested (price entered
        the zone but did NOT fully breach it) and then left. Price returning for
        a second test to "mitigate" remaining orders → high-probability continuation.
        """
        if len(df) < 25:
            return None
        try:
            closes = df["close"].values
            opens  = df["open"].values
            highs  = df["high"].values
            lows   = df["low"].values
            atr    = ind.atr if ind.atr > 0 else closes[-1] * 0.005
            curr   = closes[-1]

            for i in range(-25, -8):
                # Bullish OB: bearish candle before big up move
                if closes[i] < opens[i]:
                    ob_high = highs[i]
                    ob_low  = lows[i]
                    # Subsequent impulse: at least 1.5× ATR up
                    subsequent = closes[i+1:i+5]
                    if len(subsequent) < 2 or max(subsequent) - ob_high < 1.5 * atr:
                        continue
                    # Was the OB partially tested (price dipped into zone but didn't close below)?
                    later = df.iloc[i+3:]
                    if len(later) < 3:
                        continue
                    partial_test = any(
                        (row["low"] <= ob_high and row["close"] > ob_low)
                        for _, row in later.iloc[:-3].iterrows()
                    )
                    if not partial_test:
                        continue
                    # Current price returning to the OB zone
                    if ob_low <= curr <= ob_high:
                        confidence = min(75 + ind.volume_ratio * 3, 86)
                        return PatternResult(
                            "Mitigation Block Bullish", "LONG", confidence,
                            f"Mitigation Block ${ob_low:.2f}–${ob_high:.2f}: "
                            f"partial OB test, returning for mitigation — long"
                        )

                # Bearish OB: bullish candle before big down move
                if closes[i] > opens[i]:
                    ob_high = highs[i]
                    ob_low  = lows[i]
                    subsequent = closes[i+1:i+5]
                    if len(subsequent) < 2 or ob_low - min(subsequent) < 1.5 * atr:
                        continue
                    later = df.iloc[i+3:]
                    if len(later) < 3:
                        continue
                    partial_test = any(
                        (row["high"] >= ob_low and row["close"] < ob_high)
                        for _, row in later.iloc[:-3].iterrows()
                    )
                    if not partial_test:
                        continue
                    if ob_low <= curr <= ob_high:
                        confidence = min(75 + ind.volume_ratio * 3, 86)
                        return PatternResult(
                            "Mitigation Block Bearish", "SHORT", confidence,
                            f"Mitigation Block ${ob_low:.2f}–${ob_high:.2f}: "
                            f"partial OB test, returning for mitigation — short"
                        )
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────────────────────────────
    # ROUNDING BOTTOM — Gradual accumulation leading to breakout
    # ─────────────────────────────────────────────────────────────────────
    def detect_rounding_bottom(self, df: pd.DataFrame, ind: IndicatorSet) -> Optional[PatternResult]:
        """
        Rounding Bottom (Saucer): gradual U-shaped base.
        Left side declining, base, right side recovering with increasing volume.
        """
        if len(df) < 30:
            return None
        try:
            closes = df["close"].values[-30:]
            vols   = df["volume"].values[-30:]
            n = len(closes)

            # Divide into 3 thirds
            third = n // 3
            left   = closes[:third]
            bottom = closes[third:2*third]
            right  = closes[2*third:]

            left_mean   = np.mean(left)
            bottom_mean = np.mean(bottom)
            right_mean  = np.mean(right)

            # U-shape: left > bottom, right > bottom, right approaching left
            if not (left_mean > bottom_mean and right_mean > bottom_mean):
                return None
            if right_mean < left_mean * 0.90:
                return None  # right side not recovering enough

            # Volume should increase in right portion (accumulation complete)
            left_vol   = np.mean(vols[:third])
            right_vol  = np.mean(vols[2*third:])
            if right_vol < left_vol * 0.8:
                return None  # volume not expanding on right

            # Price should be above the midpoint of the base
            curr_close = closes[-1]
            base_level = bottom_mean
            if curr_close < base_level:
                return None

            confidence = 72
            if right_vol > left_vol * 1.2:
                confidence += 5
            if ind.rsi > 45 and ind.rsi < 65:
                confidence += 3

            return PatternResult(
                "Rounding Bottom", "LONG", min(confidence, 82),
                f"Rounding Bottom: U-shape base ${base_level:.2f}, right volume expanding — accumulation breakout"
            )
        except Exception:
            pass
        return None
