"""
premium_data_proxies.py — v15.0
Free-data duplicates of 8 institutional paid tools that hedge funds pay millions for.

Proxies implemented:
  1. L2OrderBookProxy     — Synthetic Level 2 via Lee-Ready tick classification + Kyle's lambda
  2. DarkPoolProxy        — Block trade detection from OHLCV microstructure
  3. OptionsFlowProxy     — P/C ratio + unusual OI from yfinance options chain
  4. TickDataProxy        — VPIN + cumulative delta from 5-min bars
  5. EarningsNLPProxy     — EPS surprise scoring (yfinance earnings_dates)
  6. AltDataProxy         — Google Trends slope + Wikipedia page views
  7. NewsWireProxy        — SEC EDGAR RSS real-time filings + analyst recs
  8. CoLocationProxy      — Optimal entry timing windows (ET time-based)

All classes are thread-safe singletons.
All fail-open: try/except everywhere, return (0.0, reason) on any error.
"""

from __future__ import annotations

import logging
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

# ── Optional dependencies — guarded imports ──────────────────────────────────
try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _requests = None  # type: ignore
    _REQUESTS_AVAILABLE = False

try:
    import yfinance as _yf
    _YFINANCE_AVAILABLE = True
except ImportError:
    _yf = None  # type: ignore
    _YFINANCE_AVAILABLE = False

try:
    from pytrends.request import TrendReq as _TrendReq
    _PYTRENDS_AVAILABLE = True
except Exception:
    _TrendReq = None  # type: ignore
    _PYTRENDS_AVAILABLE = False

# ── Timezone constants ────────────────────────────────────────────────────────
_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TTL Cache helper
# ─────────────────────────────────────────────────────────────────────────────

class _TTLCache:
    """Simple thread-safe TTL cache."""

    def __init__(self, ttl_seconds: int = 900) -> None:
        self._ttl = ttl_seconds
        self._store: Dict[str, Tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, val = entry
            if (_time.monotonic() - ts) > self._ttl:
                del self._store[key]
                return None
            return val

    def set(self, key: str, value) -> None:
        with self._lock:
            self._store[key] = (_time.monotonic(), value)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton mixin
# ─────────────────────────────────────────────────────────────────────────────

class _SingletonMeta(type):
    _instances: Dict[type, object] = {}
    _lock: threading.Lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


# ─────────────────────────────────────────────────────────────────────────────
# 1. L2OrderBookProxy
# ─────────────────────────────────────────────────────────────────────────────

class L2OrderBookProxy(metaclass=_SingletonMeta):
    """
    Synthetic Level 2 via Lee-Ready tick classification + Kyle's lambda.

    Lee-Ready: classify each OHLCV bar as buy (close>mid) or sell (close<mid)
    imbalance = (buy_vol - sell_vol) / total_vol
    Kyle's lambda = price impact per sqrt(volume)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get_order_imbalance_score(
        self,
        symbol: str,
        df_bars,
        direction: str,
    ) -> Tuple[float, str]:
        """
        Returns (score_delta, reason_str).
        Fails open: returns (0.0, reason) on any error.
        """
        try:
            if df_bars is None:
                return 0.0, "L2Proxy: no bars"
            import pandas as pd
            df = df_bars.copy() if hasattr(df_bars, "copy") else df_bars
            if len(df) < 5:
                return 0.0, "L2Proxy: insufficient bars"

            # Ensure required columns
            for col in ("open", "high", "low", "close", "volume"):
                if col not in df.columns:
                    return 0.0, "L2Proxy: missing OHLCV columns"

            # Lee-Ready classification
            mid = (df["high"] + df["low"]) / 2.0
            buy_mask = df["close"] > mid
            sell_mask = df["close"] < mid
            # neutral when close == mid → split 50/50

            buy_vol = np.where(buy_mask, df["volume"], np.where(~sell_mask, df["volume"] * 0.5, 0.0)).sum()
            sell_vol = np.where(sell_mask, df["volume"], np.where(~buy_mask & ~sell_mask, df["volume"] * 0.5, 0.0)).sum()
            total_vol = float(df["volume"].sum())

            if total_vol <= 0:
                return 0.0, "L2Proxy: zero volume"

            imbalance = (buy_vol - sell_vol) / total_vol  # range: -1 to +1

            # Kyle's lambda: price impact per unit of sqrt(volume)
            price_changes = df["close"].diff().abs().fillna(0)
            sqrt_vols = np.sqrt(df["volume"].clip(lower=1))
            kylambda = float((price_changes / sqrt_vols).mean())

            # Scoring
            score = 0.0
            reason_parts = []

            if direction == "LONG":
                if imbalance > 0.5:
                    score += 10.0
                    reason_parts.append(f"L2 heavy buy imbal={imbalance:.2f}")
                elif imbalance > 0.25:
                    score += 6.0
                    reason_parts.append(f"L2 buy imbal={imbalance:.2f}")
                elif imbalance < -0.25:
                    score -= 6.0
                    reason_parts.append(f"L2 sell imbal={imbalance:.2f}")
                if kylambda > 0.005:
                    score += 2.0
                    reason_parts.append(f"Kyle λ={kylambda:.4f} (informed flow)")
            else:  # SHORT
                if imbalance < -0.5:
                    score += 10.0
                    reason_parts.append(f"L2 heavy sell imbal={imbalance:.2f}")
                elif imbalance < -0.25:
                    score += 6.0
                    reason_parts.append(f"L2 sell imbal={imbalance:.2f}")
                elif imbalance > 0.25:
                    score -= 6.0
                    reason_parts.append(f"L2 buy imbal={imbalance:.2f}")
                if kylambda > 0.005:
                    score += 2.0
                    reason_parts.append(f"Kyle λ={kylambda:.4f}")

            reason = "; ".join(reason_parts) if reason_parts else "L2Proxy: neutral"
            return float(score), reason

        except Exception as e:
            logger.debug(f"[suppressed] L2OrderBookProxy: {e}")
            return 0.0, f"L2Proxy: error ({e})"


# ─────────────────────────────────────────────────────────────────────────────
# 2. DarkPoolProxy
# ─────────────────────────────────────────────────────────────────────────────

class DarkPoolProxy(metaclass=_SingletonMeta):
    """
    Block trade detection from OHLCV microstructure.

    Dark pool signature: vol > 2.5× avg AND (high-low)/close < 0.008
    Track net direction of all prints, weight recent ones more.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get_dark_pool_score(
        self,
        symbol: str,
        df_bars,
        direction: str,
    ) -> Tuple[float, str]:
        try:
            if df_bars is None:
                return 0.0, "DPProxy: no bars"
            df = df_bars.copy() if hasattr(df_bars, "copy") else df_bars
            if len(df) < 10:
                return 0.0, "DPProxy: insufficient bars"

            for col in ("open", "high", "low", "close", "volume"):
                if col not in df.columns:
                    return 0.0, "DPProxy: missing OHLCV columns"

            vol_avg = float(df["volume"].rolling(20, min_periods=5).mean().iloc[-1])
            if vol_avg <= 0:
                return 0.0, "DPProxy: zero avg volume"

            spread_pct = (df["high"] - df["low"]) / df["close"].clip(lower=0.01)

            # Identify dark pool prints
            dp_mask = (df["volume"] > 2.5 * vol_avg) & (spread_pct < 0.008)
            dp_bars = df[dp_mask].copy()

            if len(dp_bars) == 0:
                return 0.0, "DPProxy: no block prints detected"

            # Determine direction of each print
            # Positive if close > open (buy), negative if close < open (sell)
            dp_bars["print_dir"] = np.where(
                dp_bars["close"] > dp_bars["open"], 1.0,
                np.where(dp_bars["close"] < dp_bars["open"], -1.0, 0.0)
            )

            # Weight recent prints more (linear decay from oldest to newest)
            n = len(dp_bars)
            weights = np.linspace(0.5, 1.5, n)
            net_direction = float((dp_bars["print_dir"].values * weights).sum())

            score = 0.0
            reason_parts = [f"DPool: {n} block prints net={net_direction:.1f}"]

            if direction == "LONG":
                if net_direction > 3.0:
                    score += 12.0
                    reason_parts.append("strong institutional buying")
                elif net_direction > 1.0:
                    score += 7.0
                    reason_parts.append("institutional buy flow")
                elif net_direction < -1.0:
                    score -= 8.0
                    reason_parts.append("institutional selling")
            else:  # SHORT
                if net_direction < -3.0:
                    score += 12.0
                    reason_parts.append("strong institutional selling")
                elif net_direction < -1.0:
                    score += 7.0
                    reason_parts.append("institutional sell flow")
                elif net_direction > 1.0:
                    score -= 8.0
                    reason_parts.append("institutional buying")

            reason = "; ".join(reason_parts)
            return float(score), reason

        except Exception as e:
            logger.debug(f"[suppressed] DarkPoolProxy: {e}")
            return 0.0, f"DPProxy: error ({e})"


# ─────────────────────────────────────────────────────────────────────────────
# 3. OptionsFlowProxy
# ─────────────────────────────────────────────────────────────────────────────

class OptionsFlowProxy(metaclass=_SingletonMeta):
    """
    P/C ratio + unusual OI from yfinance options chain.
    15-min TTL cache (matches yfinance delay).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache = _TTLCache(ttl_seconds=900)  # 15 min

    def get_options_score(
        self,
        symbol: str,
        direction: str,
    ) -> Tuple[float, str]:
        try:
            cache_key = f"{symbol}:{direction}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

            result = self._compute_options_score(symbol, direction)
            self._cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.debug(f"[suppressed] OptionsFlowProxy: {e}")
            return 0.0, f"OptProxy: error ({e})"

    def _compute_options_score(
        self,
        symbol: str,
        direction: str,
    ) -> Tuple[float, str]:
        if not _YFINANCE_AVAILABLE:
            return 0.0, "OptProxy: yfinance not available"

        ticker = _yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            return 0.0, "OptProxy: no options data"

        # Find nearest expiry >= 5 days out
        today = datetime.now(tz=_UTC).date()
        target_exp = None
        for exp_str in expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                if (exp_date - today).days >= 5:
                    target_exp = exp_str
                    break
            except ValueError:
                continue

        if target_exp is None:
            return 0.0, "OptProxy: no expiry >= 5 days"

        chain = ticker.option_chain(target_exp)
        calls = chain.calls
        puts = chain.puts

        if calls is None or puts is None or len(calls) == 0 or len(puts) == 0:
            return 0.0, "OptProxy: empty chain"

        # P/C volume ratio
        total_call_vol = float(calls["volume"].fillna(0).sum())
        total_put_vol = float(puts["volume"].fillna(0).sum())

        score = 0.0
        reason_parts = []

        if total_call_vol + total_put_vol > 0:
            pc_ratio = total_put_vol / max(total_call_vol, 1.0)
            reason_parts.append(f"P/C={pc_ratio:.2f}")

            if direction == "LONG":
                if pc_ratio < 0.5:
                    score += 8.0
                    reason_parts.append("bullish P/C ratio")
                elif pc_ratio > 1.5:
                    score -= 8.0
                    reason_parts.append("bearish P/C ratio")
            else:  # SHORT
                if pc_ratio > 1.5:
                    score += 8.0
                    reason_parts.append("bearish P/C ratio")
                elif pc_ratio < 0.5:
                    score -= 8.0
                    reason_parts.append("bullish P/C ratio")

        # Unusual OI: find strikes with OI > 3x average OI
        current_price = None
        try:
            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                current_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass

        if current_price is not None:
            avg_call_oi = float(calls["openInterest"].fillna(0).mean())
            avg_put_oi = float(puts["openInterest"].fillna(0).mean())

            if avg_call_oi > 0:
                unusual_calls = calls[
                    (calls["openInterest"].fillna(0) > 3 * avg_call_oi) &
                    (calls["strike"] > current_price)
                ]
                if len(unusual_calls) > 0:
                    if direction == "LONG":
                        score += 8.0
                        reason_parts.append(f"unusual calls above price ({len(unusual_calls)} strikes)")
                    else:
                        score -= 4.0
                        reason_parts.append("unusual calls (bearish for SHORT)")

            if avg_put_oi > 0:
                unusual_puts = puts[
                    (puts["openInterest"].fillna(0) > 3 * avg_put_oi) &
                    (puts["strike"] < current_price)
                ]
                if len(unusual_puts) > 0:
                    if direction == "LONG":
                        score -= 6.0
                        reason_parts.append(f"unusual puts below price ({len(unusual_puts)} strikes)")
                    else:
                        score += 6.0
                        reason_parts.append("unusual puts (bearish flow)")

        reason = "; ".join(reason_parts) if reason_parts else "OptProxy: neutral"
        return float(score), reason


# ─────────────────────────────────────────────────────────────────────────────
# 4. TickDataProxy
# ─────────────────────────────────────────────────────────────────────────────

class TickDataProxy(metaclass=_SingletonMeta):
    """
    VPIN + cumulative delta from 5-min bars.

    Lee-Ready classify: buy_frac = 0.75 if close>mid, 0.25 if close<mid, 0.5 neutral
    cumulative_delta_pct = sum(buy-sell)/total_vol → -1 to +1
    VPIN simplified: per volume-bucket imbalance avg (n_buckets=50)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get_tape_score(
        self,
        symbol: str,
        df_bars,
        direction: str,
    ) -> Tuple[float, str]:
        try:
            if df_bars is None:
                return 0.0, "TickProxy: no bars"
            df = df_bars.copy() if hasattr(df_bars, "copy") else df_bars
            if len(df) < 10:
                return 0.0, "TickProxy: insufficient bars"

            for col in ("open", "high", "low", "close", "volume"):
                if col not in df.columns:
                    return 0.0, "TickProxy: missing OHLCV columns"

            # Lee-Ready classification
            mid = (df["high"] + df["low"]) / 2.0
            buy_frac = np.where(
                df["close"] > mid, 0.75,
                np.where(df["close"] < mid, 0.25, 0.5)
            )

            buy_vol = (buy_frac * df["volume"].values).sum()
            sell_vol = ((1.0 - buy_frac) * df["volume"].values).sum()
            total_vol = float(df["volume"].sum())

            if total_vol <= 0:
                return 0.0, "TickProxy: zero volume"

            # Cumulative delta percentage
            cdp = (buy_vol - sell_vol) / total_vol  # -1 to +1

            # VPIN (simplified): divide into volume buckets, compute imbalance per bucket
            n_buckets = min(50, max(5, len(df) // 2))
            bucket_size = total_vol / n_buckets
            imbalances = []
            acc_buy = 0.0
            acc_sell = 0.0
            acc_vol = 0.0

            for i in range(len(df)):
                b = float(buy_frac[i]) * float(df["volume"].iloc[i])
                s = (1.0 - float(buy_frac[i])) * float(df["volume"].iloc[i])
                acc_buy += b
                acc_sell += s
                acc_vol += b + s
                if acc_vol >= bucket_size:
                    bucket_vol = acc_buy + acc_sell
                    if bucket_vol > 0:
                        imbalances.append(abs(acc_buy - acc_sell) / bucket_vol)
                    acc_buy = acc_sell = acc_vol = 0.0

            vpin = float(np.mean(imbalances)) if imbalances else 0.5
            vpin_factor = 1.5 if vpin > 0.6 else 1.0

            # Acceleration: check if last 25% of bars has higher cdp than overall
            quarter = max(1, len(df) // 4)
            recent_df = df.iloc[-quarter:]
            recent_mid = (recent_df["high"] + recent_df["low"]) / 2.0
            recent_bf = np.where(
                recent_df["close"].values > recent_mid.values, 0.75,
                np.where(recent_df["close"].values < recent_mid.values, 0.25, 0.5)
            )
            recent_buy = (recent_bf * recent_df["volume"].values).sum()
            recent_sell = ((1.0 - recent_bf) * recent_df["volume"].values).sum()
            recent_total = recent_buy + recent_sell
            recent_cdp = (recent_buy - recent_sell) / max(recent_total, 1.0)
            accelerating = (
                (direction == "LONG" and recent_cdp > cdp + 0.1) or
                (direction == "SHORT" and recent_cdp < cdp - 0.1)
            )

            score = 0.0
            reason_parts = [f"TickProxy: CDP={cdp:.2f} VPIN={vpin:.2f}"]

            if direction == "LONG":
                if cdp > 0.3:
                    raw_boost = min(10.0, 8.0 * cdp * vpin_factor)
                    score += raw_boost
                    reason_parts.append(f"bullish delta boost={raw_boost:.1f}")
                elif cdp < -0.3:
                    score -= 6.0
                    reason_parts.append("bearish cumulative delta")
                if accelerating:
                    score += 3.0
                    reason_parts.append("delta accelerating up")
            else:  # SHORT
                if cdp < -0.3:
                    raw_boost = min(10.0, 8.0 * abs(cdp) * vpin_factor)
                    score += raw_boost
                    reason_parts.append(f"bearish delta boost={raw_boost:.1f}")
                elif cdp > 0.3:
                    score -= 6.0
                    reason_parts.append("bullish cumulative delta")
                if accelerating:
                    score += 3.0
                    reason_parts.append("delta accelerating down")

            reason = "; ".join(reason_parts)
            return float(score), reason

        except Exception as e:
            logger.debug(f"[suppressed] TickDataProxy: {e}")
            return 0.0, f"TickProxy: error ({e})"


# ─────────────────────────────────────────────────────────────────────────────
# 5. EarningsNLPProxy
# ─────────────────────────────────────────────────────────────────────────────

class EarningsNLPProxy(metaclass=_SingletonMeta):
    """
    EPS surprise scoring via yfinance earnings_dates.
    1-hour TTL cache.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache = _TTLCache(ttl_seconds=3600)  # 1 hour

    def get_earnings_score(
        self,
        symbol: str,
        direction: str,
    ) -> Tuple[float, str]:
        try:
            cache_key = f"earnings:{symbol}:{direction}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

            result = self._compute_earnings_score(symbol, direction)
            self._cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.debug(f"[suppressed] EarningsNLPProxy: {e}")
            return 0.0, f"EarningsProxy: error ({e})"

    def _compute_earnings_score(
        self,
        symbol: str,
        direction: str,
    ) -> Tuple[float, str]:
        if not _YFINANCE_AVAILABLE:
            return 0.0, "EarningsProxy: yfinance not available"

        ticker = _yf.Ticker(symbol)

        try:
            earnings_dates = ticker.earnings_dates
        except Exception:
            return 0.0, "EarningsProxy: no earnings_dates"

        if earnings_dates is None or len(earnings_dates) == 0:
            return 0.0, "EarningsProxy: no earnings data"

        # Normalize column names (yfinance uses 'EPS Estimate' and 'Reported EPS')
        cols = earnings_dates.columns.tolist()
        est_col = None
        rep_col = None
        for c in cols:
            cl = c.lower()
            if "estimate" in cl:
                est_col = c
            if "reported" in cl:
                rep_col = c

        if est_col is None or rep_col is None:
            return 0.0, "EarningsProxy: missing EPS columns"

        today = datetime.now(tz=_UTC).date()

        # Find most recent past earnings with both values available
        best_score = 0.0
        best_reason = "EarningsProxy: no recent valid earnings"

        for idx in earnings_dates.index:
            try:
                row = earnings_dates.loc[idx]
                estimate = float(row[est_col]) if row[est_col] is not None and str(row[est_col]) not in ("nan", "None", "") else None
                reported = float(row[rep_col]) if row[rep_col] is not None and str(row[rep_col]) not in ("nan", "None", "") else None

                if estimate is None or reported is None:
                    continue
                if abs(estimate) < 1e-9:
                    continue

                # Convert index to date
                if hasattr(idx, "date"):
                    earn_date = idx.date()
                else:
                    earn_date = idx

                days_ago = (today - earn_date).days
                if days_ago < 0:  # future earnings — skip
                    continue

                surprise_pct = (reported - estimate) / abs(estimate) * 100.0

                score = 0.0
                reason = ""

                if direction == "LONG":
                    if surprise_pct > 10.0 and days_ago <= 30:
                        score = 12.0
                        reason = f"EPS beat {surprise_pct:.1f}% within 30d"
                    elif surprise_pct > 5.0 and days_ago <= 60:
                        score = 8.0
                        reason = f"EPS beat {surprise_pct:.1f}% within 60d"
                    elif surprise_pct < -10.0 and days_ago <= 30:
                        score = -15.0
                        reason = f"EPS miss {surprise_pct:.1f}% within 30d"
                else:  # SHORT
                    if surprise_pct < -10.0 and days_ago <= 30:
                        score = 12.0
                        reason = f"EPS miss {surprise_pct:.1f}% within 30d"
                    elif surprise_pct < -5.0 and days_ago <= 60:
                        score = 8.0
                        reason = f"EPS miss {surprise_pct:.1f}% within 60d"
                    elif surprise_pct > 10.0 and days_ago <= 30:
                        score = -15.0
                        reason = f"EPS beat {surprise_pct:.1f}% within 30d"

                if abs(score) > abs(best_score):
                    best_score = score
                    best_reason = reason

            except Exception:
                continue

        return float(best_score), best_reason


# ─────────────────────────────────────────────────────────────────────────────
# 6. AltDataProxy
# ─────────────────────────────────────────────────────────────────────────────

# Symbol → Wikipedia article name mapping
_WIKI_MAP: Dict[str, str] = {
    "AAPL":  "Apple_Inc.",
    "MSFT":  "Microsoft",
    "NVDA":  "Nvidia",
    "AMZN":  "Amazon_(company)",
    "GOOGL": "Alphabet_Inc.",
    "GOOG":  "Alphabet_Inc.",
    "META":  "Meta_Platforms",
    "TSLA":  "Tesla%2C_Inc.",
    "AMD":   "Advanced_Micro_Devices",
    "INTC":  "Intel",
    "QCOM":  "Qualcomm",
    "NFLX":  "Netflix",
    "COIN":  "Coinbase",
    "PLTR":  "Palantir_Technologies",
    "MSTR":  "MicroStrategy",
    "CRWD":  "CrowdStrike",
    "UBER":  "Uber",
    "SHOP":  "Shopify",
    "RIVN":  "Rivian",
    "LCID":  "Lucid_Motors",
    "NIO":   "Nio_Inc.",
    "SQ":    "Block%2C_Inc.",
    "PYPL":  "PayPal",
    "SOFI":  "SoFi",
    "HOOD":  "Robinhood_Markets",
}


class AltDataProxy(metaclass=_SingletonMeta):
    """
    Google Trends slope + Wikipedia page views.
    1-hour TTL cache.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache = _TTLCache(ttl_seconds=3600)  # 1 hour

    def get_alt_data_score(
        self,
        symbol: str,
        direction: str,
    ) -> Tuple[float, str]:
        try:
            cache_key = f"altdata:{symbol}:{direction}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

            result = self._compute_alt_data_score(symbol, direction)
            self._cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.debug(f"[suppressed] AltDataProxy: {e}")
            return 0.0, f"AltProxy: error ({e})"

    def _compute_alt_data_score(
        self,
        symbol: str,
        direction: str,
    ) -> Tuple[float, str]:
        trends_slope = 0.0
        wiki_slope = 0.0
        sources = []

        # ── Google Trends ─────────────────────────────────────────────────────
        if _PYTRENDS_AVAILABLE:
            try:
                pytrends = _TrendReq(hl="en-US", tz=0, timeout=(5, 10))
                pytrends.build_payload([symbol], timeframe="now 7-d")
                df_trends = pytrends.interest_over_time()
                if not df_trends.empty and symbol in df_trends.columns:
                    vals = df_trends[symbol].values.astype(float)
                    if len(vals) >= 3 and vals.max() > 0:
                        # Normalize to 0-1
                        vals_norm = (vals - vals.min()) / (vals.max() - vals.min() + 1e-9)
                        x = np.arange(len(vals_norm))
                        slope = float(np.polyfit(x, vals_norm, 1)[0])
                        # Normalize slope to -1..+1
                        trends_slope = float(np.clip(slope * len(vals_norm), -1.0, 1.0))
                        sources.append(f"Google Trends slope={trends_slope:.2f}")
            except Exception as te:
                logger.debug(f"[suppressed] AltData Google Trends: {te}")

        # ── Wikipedia page views ──────────────────────────────────────────────
        if _REQUESTS_AVAILABLE:
            try:
                article = _WIKI_MAP.get(symbol, symbol)
                end_date = datetime.now(tz=_UTC)
                start_date = end_date - timedelta(days=14)
                start_str = start_date.strftime("%Y%m%d")
                end_str = end_date.strftime("%Y%m%d")
                url = (
                    f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
                    f"/en.wikipedia/all-access/all-agents/{article}/daily/{start_str}/{end_str}"
                )
                resp = _requests.get(url, timeout=8, headers={"User-Agent": "KingTrades/15.0"})
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if len(items) >= 5:
                        views = np.array([float(item["views"]) for item in items])
                        vmax = views.max()
                        if vmax > 0:
                            views_norm = (views - views.min()) / (vmax - views.min() + 1e-9)
                            x = np.arange(len(views_norm))
                            slope = float(np.polyfit(x, views_norm, 1)[0])
                            wiki_slope = float(np.clip(slope * len(views_norm), -1.0, 1.0))
                            sources.append(f"Wiki slope={wiki_slope:.2f}")
            except Exception as we:
                logger.debug(f"[suppressed] AltData Wikipedia: {we}")

        if not sources:
            return 0.0, "AltProxy: no data sources available"

        # Composite score
        if _PYTRENDS_AVAILABLE and sources:
            composite = trends_slope * 0.6 + wiki_slope * 0.4
        else:
            composite = wiki_slope  # only wiki available

        score = 0.0
        reason_parts = list(sources)

        if direction == "LONG":
            if composite > 0.5:
                score += 8.0
                reason_parts.append(f"Alt data bullish composite={composite:.2f}")
            elif composite < -0.5:
                score -= 6.0
                reason_parts.append(f"Alt data bearish composite={composite:.2f}")
        else:  # SHORT
            if composite < -0.5:
                score += 6.0
                reason_parts.append(f"Alt data bearish composite={composite:.2f}")
            elif composite > 0.5:
                score -= 8.0
                reason_parts.append(f"Alt data bullish composite={composite:.2f}")

        reason = "; ".join(reason_parts)
        return float(score), reason


# ─────────────────────────────────────────────────────────────────────────────
# 7. NewsWireProxy
# ─────────────────────────────────────────────────────────────────────────────

# EDGAR filing title keywords → score impact for LONG direction
_EDGAR_KEYWORDS: List[Tuple[str, float]] = [
    # dilution signals
    ("S-3",             -15.0),
    ("dilution",        -12.0),
    ("shelf registrat", -10.0),
    # buyback signals
    ("buyback",          12.0),
    ("repurchase",       10.0),
    ("SC 13G",           12.0),
    ("SC13G",            12.0),
    # going concern
    ("going concern",   -12.0),
    # M&A
    ("merger",           10.0),
    ("acquisition",       8.0),
    ("takeover",         10.0),
    # insider buying
    ("Form 4",            4.0),  # can be either direction — default neutral
]


class NewsWireProxy(metaclass=_SingletonMeta):
    """
    SEC EDGAR RSS real-time filings + analyst recommendations.
    10-min TTL cache.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache = _TTLCache(ttl_seconds=600)  # 10 min

    def get_news_score(
        self,
        symbol: str,
        direction: str,
    ) -> Tuple[float, str]:
        try:
            cache_key = f"news:{symbol}:{direction}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

            result = self._compute_news_score(symbol, direction)
            self._cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.debug(f"[suppressed] NewsWireProxy: {e}")
            return 0.0, f"NewsProxy: error ({e})"

    def _compute_news_score(
        self,
        symbol: str,
        direction: str,
    ) -> Tuple[float, str]:
        edgar_score = 0.0
        rec_score = 0.0
        reason_parts = []

        # ── SEC EDGAR RSS ─────────────────────────────────────────────────────
        if _REQUESTS_AVAILABLE:
            try:
                url = (
                    f"https://www.sec.gov/cgi-bin/browse-edgar"
                    f"?action=getcompany&CIK={symbol}&type=&dateb=&owner=include"
                    f"&count=10&search_text=&output=atom"
                )
                resp = _requests.get(
                    url,
                    timeout=8,
                    headers={"User-Agent": "KingTrades/15.0 contact@kingtrades.ai"},
                )
                if resp.status_code == 200:
                    xml_text = resp.text

                    # Parse titles from atom feed
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(xml_text)
                    ns = {"atom": "http://www.w3.org/2005/Atom"}
                    entries = root.findall("atom:entry", ns)
                    if not entries:
                        # Try without namespace
                        entries = root.findall(".//entry")

                    filing_score = 0.0
                    filing_signals = []

                    for entry in entries[:10]:
                        title_el = entry.find("atom:title", ns)
                        if title_el is None:
                            title_el = entry.find("title")
                        if title_el is None or title_el.text is None:
                            continue
                        title = title_el.text.strip()

                        for kw, kw_score in _EDGAR_KEYWORDS:
                            if kw.lower() in title.lower():
                                long_score = kw_score
                                if direction == "SHORT":
                                    long_score = -kw_score
                                filing_score += long_score
                                filing_signals.append(f"'{kw}' → {long_score:+.0f}")

                    if filing_signals:
                        edgar_score = float(np.clip(filing_score, -15.0, 15.0))
                        reason_parts.append(f"EDGAR: {'; '.join(filing_signals[:3])}")

            except Exception as edgar_e:
                logger.debug(f"[suppressed] NewsProxy EDGAR: {edgar_e}")

        # ── yfinance analyst recommendations ─────────────────────────────────
        if _YFINANCE_AVAILABLE:
            try:
                ticker = _yf.Ticker(symbol)
                recs = ticker.recommendations
                if recs is not None and len(recs) > 0:
                    recent = recs.tail(5)
                    buy_cols = [c for c in recent.columns if any(w in c.lower() for w in ("strongbuy", "buy"))]
                    sell_cols = [c for c in recent.columns if any(w in c.lower() for w in ("strongsell", "sell"))]

                    total_buys = 0
                    total_sells = 0

                    for col in buy_cols:
                        try:
                            total_buys += int(recent[col].fillna(0).sum())
                        except Exception:
                            pass
                    for col in sell_cols:
                        try:
                            total_sells += int(recent[col].fillna(0).sum())
                        except Exception:
                            pass

                    if direction == "LONG":
                        if total_buys >= 3:
                            rec_score += 6.0
                            reason_parts.append(f"Analyst {total_buys} buys")
                        elif total_sells >= 3:
                            rec_score -= 6.0
                            reason_parts.append(f"Analyst {total_sells} sells")
                    else:  # SHORT
                        if total_sells >= 3:
                            rec_score += 6.0
                            reason_parts.append(f"Analyst {total_sells} sells")
                        elif total_buys >= 3:
                            rec_score -= 6.0
                            reason_parts.append(f"Analyst {total_buys} buys")

            except Exception as rec_e:
                logger.debug(f"[suppressed] NewsProxy recs: {rec_e}")

        total_score = float(np.clip(edgar_score + rec_score, -15.0, 15.0))
        reason = "; ".join(reason_parts) if reason_parts else "NewsProxy: no signals"
        return total_score, reason


# ─────────────────────────────────────────────────────────────────────────────
# 8. CoLocationProxy
# ─────────────────────────────────────────────────────────────────────────────

class CoLocationProxy(metaclass=_SingletonMeta):
    """
    Optimal entry timing windows based on ET time-of-day.
    No cache needed — just reads current time.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get_timing_score(
        self,
        direction: str,
        current_score: float,
    ) -> Tuple[float, str]:
        try:
            now_et = datetime.now(tz=_ET)
            h = now_et.hour
            m = now_et.minute
            total_minutes = h * 60 + m

            # ── BEST windows ──────────────────────────────────────────────────
            # 09:35–09:45 → +8 (ORB confirmation)
            if 9 * 60 + 35 <= total_minutes <= 9 * 60 + 45:
                return 8.0, "CoLoc: ORB confirmation window +8"
            # 15:00–15:25 → +8 (power hour)
            if 15 * 60 <= total_minutes <= 15 * 60 + 25:
                return 8.0, "CoLoc: power hour window +8"
            # 10:30–11:30 → +5
            if 10 * 60 + 30 <= total_minutes <= 11 * 60 + 30:
                return 5.0, "CoLoc: morning prime window +5"
            # 14:00–14:30 → +5
            if 14 * 60 <= total_minutes <= 14 * 60 + 30:
                return 5.0, "CoLoc: afternoon prime window +5"

            # ── AVOID windows ─────────────────────────────────────────────────
            # 09:15–09:35 → -10 (open chaos)
            if 9 * 60 + 15 <= total_minutes < 9 * 60 + 35:
                return -10.0, "CoLoc: open chaos window -10"
            # 12:00–13:30 → -8 (lunch)
            if 12 * 60 <= total_minutes < 13 * 60 + 30:
                return -8.0, "CoLoc: lunch chop window -8"
            # 15:25–16:00 → -12 (forced close)
            if 15 * 60 + 25 <= total_minutes <= 16 * 60:
                return -12.0, "CoLoc: forced close window -12"

            return 0.0, "CoLoc: neutral timing"

        except Exception as e:
            logger.debug(f"[suppressed] CoLocationProxy: {e}")
            return 0.0, f"CoLoc: error ({e})"


# ─────────────────────────────────────────────────────────────────────────────
# Master aggregator
# ─────────────────────────────────────────────────────────────────────────────

def get_all_proxy_scores(
    symbol: str,
    direction: str,
    df_bars=None,
    current_score: float = 65.0,
) -> Tuple[float, List[str]]:
    """
    Run all 8 proxies and return (total_delta, [reasons]).
    Thread-safe, fail-open.
    """
    import config as _config

    total_delta = 0.0
    reasons: List[str] = []

    def _safe_add(delta: float, reason: str, label: str) -> None:
        nonlocal total_delta
        if delta != 0.0:
            total_delta += delta
            reasons.append(f"{label}: {reason} ({delta:+.1f})")

    # 1. L2 Order Book Proxy
    if getattr(_config, "L2_PROXY_ENABLED", True):
        try:
            d, r = L2OrderBookProxy().get_order_imbalance_score(symbol, df_bars, direction)
            _safe_add(d, r, "L2")
        except Exception as e:
            logger.debug(f"[suppressed] get_all_proxy_scores L2: {e}")

    # 2. Dark Pool Proxy
    if getattr(_config, "DARK_POOL_PROXY_ENABLED", True):
        try:
            d, r = DarkPoolProxy().get_dark_pool_score(symbol, df_bars, direction)
            _safe_add(d, r, "DPool")
        except Exception as e:
            logger.debug(f"[suppressed] get_all_proxy_scores DPool: {e}")

    # 3. Options Flow Proxy
    if getattr(_config, "OPTIONS_PROXY_ENABLED", True):
        try:
            d, r = OptionsFlowProxy().get_options_score(symbol, direction)
            _safe_add(d, r, "Opts")
        except Exception as e:
            logger.debug(f"[suppressed] get_all_proxy_scores Opts: {e}")

    # 4. Tick Data Proxy
    if getattr(_config, "TICK_PROXY_ENABLED", True):
        try:
            d, r = TickDataProxy().get_tape_score(symbol, df_bars, direction)
            _safe_add(d, r, "Tick")
        except Exception as e:
            logger.debug(f"[suppressed] get_all_proxy_scores Tick: {e}")

    # 5. Earnings NLP Proxy
    if getattr(_config, "EARNINGS_PROXY_ENABLED", True):
        try:
            d, r = EarningsNLPProxy().get_earnings_score(symbol, direction)
            _safe_add(d, r, "Earn")
        except Exception as e:
            logger.debug(f"[suppressed] get_all_proxy_scores Earn: {e}")

    # 6. Alt Data Proxy
    if getattr(_config, "ALT_DATA_PROXY_ENABLED", True):
        try:
            d, r = AltDataProxy().get_alt_data_score(symbol, direction)
            _safe_add(d, r, "Alt")
        except Exception as e:
            logger.debug(f"[suppressed] get_all_proxy_scores Alt: {e}")

    # 7. News Wire Proxy
    if getattr(_config, "NEWS_PROXY_ENABLED", True):
        try:
            d, r = NewsWireProxy().get_news_score(symbol, direction)
            _safe_add(d, r, "News")
        except Exception as e:
            logger.debug(f"[suppressed] get_all_proxy_scores News: {e}")

    # 8. CoLocation Proxy
    if getattr(_config, "COLOC_PROXY_ENABLED", True):
        try:
            d, r = CoLocationProxy().get_timing_score(direction, current_score)
            _safe_add(d, r, "CoLoc")
        except Exception as e:
            logger.debug(f"[suppressed] get_all_proxy_scores CoLoc: {e}")

    return float(total_delta), reasons
