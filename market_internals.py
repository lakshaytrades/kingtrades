"""
market_internals.py — US Market Internals & Breadth Engine

Provides institutional-flow visibility via a composite BREADTH SCORE (0-100)
and go/no-go verdicts for LONG/SHORT entries.

A stock can look perfect technically, but if the broader market is secretly
deteriorating the trade fails. This module detects that hidden deterioration
before it costs capital.

Components:
  1. Sector Breadth    — 11 SPDR sector ETFs, EMA9/21 alignment
  2. SPY Momentum      — consecutive bars + VWAP position (TICK proxy)
  3. Volatility Regime — UVXY fear-spike detection
  4. Advance/Decline   — SPY/QQQ/DIA above their 20-day SMA (bull/bear regime)

Cache TTL: 5 minutes. Fail-open — returns neutral breadth on any data error
so the trading engine is never blocked by an unavailability here.
"""

import logging
import time as _time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SECTORS: List[str] = [
    "XLK",   # Technology
    "XLF",   # Financials
    "XLE",   # Energy
    "XLY",   # Consumer Discretionary
    "XLI",   # Industrials
    "XLB",   # Materials
    "XLV",   # Health Care
    "XLU",   # Utilities
    "XLRE",  # Real Estate
    "XLC",   # Communication Services
    "XLP",   # Consumer Staples
]

# Thresholds
# Wide neutral zone (45-55) was blocking ALL trades on mixed market days.
# Fixed: LONGs allowed >= 42, SHORTs allowed <= 58. Hard stop only at extremes.
LONG_OK_THRESHOLD  = 42.0   # breadth >= 42 → longs OK (reduced size 42-54)
SHORT_OK_THRESHOLD = 58.0   # breadth <= 58 → shorts OK (reduced size 46-58)
SIZE_BOOST_HIGH    = 70.0   # >= 70 → 1.2x (strong bull breadth)
SIZE_NORMAL_LOW    = 54.0   # 54–69 → 1.0x
SIZE_NEUTRAL_LOW   = 42.0   # 42–53 → 0.75x (cautious/mixed)
                             # < 42  → 0.4x for longs (bearish breadth)

UVXY_FEAR_THRESHOLD = 10.0  # UVXY 5-day change % that signals fear spike

# Neutral fallback returned when data is unavailable
_NEUTRAL_RESULT: Dict = {
    "breadth_score":    50.0,
    "sector_score":     0,
    "bullish_sectors":  0,
    "bearish_sectors":  0,
    "spy_momentum":     0,
    "volatility_flag":  False,
    "ad_regime":        0,
    "bias":             "NEUTRAL",
    "long_ok":          True,
    "short_ok":         True,
    "reason":           "Data unavailable — neutral breadth assumed",
    "sectors":          {},
    "ts":               None,
}


# ─────────────────────────────────────────────────────────────────────────────
# EMA HELPERS (pure numpy — no pandas required for the core math)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(closes: np.ndarray, period: int) -> np.ndarray:
    """Compute EMA using standard multiplier. Returns array of same length."""
    if len(closes) < period:
        return np.full(len(closes), np.nan)
    k = 2.0 / (period + 1)
    out = np.empty(len(closes))
    out[:] = np.nan
    # seed with SMA of first `period` values
    out[period - 1] = np.mean(closes[:period])
    for i in range(period, len(closes)):
        out[i] = closes[i] * k + out[i - 1] * (1.0 - k)
    return out


def _vwap_from_df(df: pd.DataFrame) -> float:
    """Compute today's VWAP from an intraday OHLCV DataFrame."""
    if df.empty:
        return 0.0
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_tpv = (typical * df["volume"]).sum()
    cum_vol = df["volume"].sum()
    if cum_vol == 0:
        return float(df["close"].iloc[-1])
    return float(cum_tpv / cum_vol)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────────────────────────────────────

class MarketInternals:
    """
    Computes a composite US market breadth score (0-100) and provides
    go/no-go verdicts for intraday LONG and SHORT entries.

    Usage:
        mi = get_market_internals()
        data = mi.get_breadth()
        ok, reason = mi.is_long_ok()
        mult = mi.get_size_multiplier()
    """

    CACHE_TTL = 300  # seconds (5 minutes)

    def __init__(self):
        self._cache: Dict = {}
        self._cache_time: Optional[float] = None

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────

    def get_breadth(self) -> Dict:
        """
        Returns the full breadth snapshot. Cached for CACHE_TTL seconds.

        Keys:
            breadth_score   float 0-100
            sector_score    int -11 to +11
            bullish_sectors int
            bearish_sectors int
            spy_momentum    int (-3 to +3)
            volatility_flag bool  (True = fear spike detected)
            ad_regime       int   (-1, 0, +1)
            bias            str   "BULL" | "BEAR" | "NEUTRAL"
            long_ok         bool
            short_ok        bool
            reason          str
            sectors         Dict[str, str]  {"XLK": "BULL", ...}
            ts              str  ET timestamp of computation
        """
        now = _time.monotonic()
        if self._cache and self._cache_time is not None:
            age = now - self._cache_time
            if age < self.CACHE_TTL:
                return self._cache

        result = self._compute_breadth()
        self._cache = result
        self._cache_time = now
        return result

    def is_long_ok(self) -> Tuple[bool, str]:
        """
        Long entries OK when breadth >= 42. Hard block only below 42
        (was 55, which created a dead zone where no trades were possible).
        """
        data = self.get_breadth()
        score = data["breadth_score"]
        if score >= LONG_OK_THRESHOLD:
            caution = " (cautious size — mixed breadth)" if score < 54 else ""
            reason = (
                f"Breadth {score:.1f}/100 — {data['bullish_sectors']} sectors bullish."
                f" LONG OK{caution}."
            )
            return True, reason
        else:
            reason = (
                f"Breadth {score:.1f}/100 below {LONG_OK_THRESHOLD} — "
                f"only {data['bullish_sectors']} sectors bullish. LONG blocked."
            )
            return False, reason

    def is_short_ok(self) -> Tuple[bool, str]:
        """
        Short entries OK when breadth <= 58. Hard block only above 58
        (was 45, which created a dead zone where no trades were possible).
        """
        data = self.get_breadth()
        score = data["breadth_score"]
        if score <= SHORT_OK_THRESHOLD:
            caution = " (cautious size — mixed breadth)" if score > 46 else ""
            reason = (
                f"Breadth {score:.1f}/100 — {data['bearish_sectors']} sectors bearish."
                f" SHORT OK{caution}."
            )
            return True, reason
        else:
            reason = (
                f"Breadth {score:.1f}/100 above {SHORT_OK_THRESHOLD} — "
                f"only {data['bearish_sectors']} sectors bearish. SHORT blocked."
            )
            return False, reason

    def get_size_multiplier(self, direction: str = "LONG") -> float:
        """
        Returns position-size multiplier based on breadth score and direction.
        Accepts direction arg (was missing, causing silent failures in signal_generator).
        """
        score = self.get_breadth()["breadth_score"]
        if direction == "LONG":
            if score >= SIZE_BOOST_HIGH:
                return 1.2
            elif score >= SIZE_NORMAL_LOW:
                return 1.0
            elif score >= SIZE_NEUTRAL_LOW:
                return 0.75   # cautious in mixed breadth
            else:
                return 0.4    # bearish breadth — small longs only
        else:  # SHORT
            # Inverse: low breadth = good for shorts
            if score <= (100 - SIZE_BOOST_HIGH):
                return 1.2
            elif score <= (100 - SIZE_NORMAL_LOW):
                return 1.0
            elif score <= (100 - SIZE_NEUTRAL_LOW):
                return 0.75
            else:
                return 0.4

    # ─────────────────────────────────────────────────────────────────────
    # INTERNAL COMPUTATION
    # ─────────────────────────────────────────────────────────────────────

    def _compute_breadth(self) -> Dict:
        """Run all sub-components and assemble the final breadth score."""
        ts = format_ist_timestamp()
        logger.info(f"[{ts}] MarketInternals: computing breadth snapshot")

        # --- 1. Sector breadth -------------------------------------------
        sector_score, bullish, bearish, sector_labels = self._sector_breadth()

        # --- 2. SPY momentum (TICK proxy) --------------------------------
        spy_momentum = self._spy_momentum()

        # --- 3. Volatility regime (UVXY) ---------------------------------
        volatility_flag = self._volatility_regime()

        # --- 4. Advance / Decline proxy ----------------------------------
        ad_regime = self._ad_proxy()

        # --- 5. Assemble BREADTH_SCORE (0-100) ---------------------------
        # Map sector_score (-11 to +11) → raw base (0–100)
        base = ((sector_score + len(SECTORS)) / (2 * len(SECTORS))) * 100.0  # 0–100

        # Adjustments (each ±5 points max to avoid overwhelming the base)
        spy_adj  = spy_momentum * 2.5   # range: -7.5 to +7.5 (3 steps × 2.5)
        ad_adj   = ad_regime   * 5.0    # range: -5 to +5
        vix_adj  = -5.0 if volatility_flag else 0.0   # fear spike penalty

        raw_score = base + spy_adj + ad_adj + vix_adj
        breadth_score = float(max(0.0, min(100.0, raw_score)))

        # Bias label
        if breadth_score >= LONG_OK_THRESHOLD:
            bias = "BULL"
        elif breadth_score <= SHORT_OK_THRESHOLD:
            bias = "BEAR"
        else:
            bias = "NEUTRAL"

        long_ok  = breadth_score >= LONG_OK_THRESHOLD
        short_ok = breadth_score <= SHORT_OK_THRESHOLD

        # Human-readable reason
        parts = [
            f"Score {breadth_score:.1f}/100",
            f"sectors {bullish}↑/{bearish}↓",
            f"SPY_mom={spy_momentum:+d}",
        ]
        if volatility_flag:
            parts.append("UVXY fear spike")
        if ad_regime != 0:
            parts.append(f"AD_regime={ad_regime:+d}")
        reason = " | ".join(parts)

        result = {
            "breadth_score":    round(breadth_score, 2),
            "sector_score":     sector_score,
            "bullish_sectors":  bullish,
            "bearish_sectors":  bearish,
            "spy_momentum":     spy_momentum,
            "volatility_flag":  volatility_flag,
            "ad_regime":        ad_regime,
            "bias":             bias,
            "long_ok":          long_ok,
            "short_ok":         short_ok,
            "reason":           reason,
            "sectors":          sector_labels,
            "ts":               ts,
        }

        logger.info(
            f"[{ts}] Breadth: {breadth_score:.1f}/100 ({bias}) | "
            f"sectors {bullish}↑/{bearish}↓ | SPY_mom={spy_momentum:+d} | "
            f"volatility_spike={volatility_flag} | AD={ad_regime:+d}"
        )
        return result

    # ─────────────────────────────────────────────────────────────────────
    # COMPONENT 1: SECTOR BREADTH
    # ─────────────────────────────────────────────────────────────────────

    def _sector_breadth(self) -> Tuple[int, int, int, Dict[str, str]]:
        """
        For each of the 11 SPDR sector ETFs:
          - Fetch last 20 × 5-min bars via Alpaca
          - Compute EMA9 and EMA21
          - Score: +1 (bullish) if EMA9 > EMA21 AND close > EMA9
                   -1 (bearish) if EMA9 < EMA21 AND close < EMA9
                    0 (neutral) otherwise

        Returns (sector_score, bullish_count, bearish_count, labels_dict)
        """
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()
        except Exception as exc:
            logger.warning(f"Alpaca data fetcher unavailable — sector breadth skipped: {exc}")
            return 0, 0, 0, {}

        sector_score = 0
        bullish_count = 0
        bearish_count = 0
        labels: Dict[str, str] = {}

        for etf in SECTORS:
            try:
                df = fetcher.get_ohlcv(etf, interval="5minute", lookback_days=1)
                label, score = self._score_sector_etf(df, etf)
                labels[etf] = label
                sector_score += score
                if score == 1:
                    bullish_count += 1
                elif score == -1:
                    bearish_count += 1
            except Exception as exc:
                logger.debug(f"Sector {etf} scoring failed: {exc}")
                labels[etf] = "NEUTRAL"

        return sector_score, bullish_count, bearish_count, labels

    def _score_sector_etf(self, df: pd.DataFrame, symbol: str) -> Tuple[str, int]:
        """
        Score a sector ETF DataFrame -1 / 0 / +1.

        df is an OHLCV DataFrame with lowercase columns (open, high, low, close,
        volume) as returned by data_fetch_alpaca fetcher.get_ohlcv().
        Returns (label_str, score_int).
        """
        if df is None or df.empty or len(df) < 10:
            return "NEUTRAL", 0

        closes = df["close"].values.astype(float)

        # Trim to last 20 bars
        closes = closes[-20:]

        ema9  = _ema(closes, 9)
        ema21 = _ema(closes, min(21, len(closes)))

        last_close = closes[-1]
        last_ema9  = ema9[-1]
        last_ema21 = ema21[-1]

        if np.isnan(last_ema9) or np.isnan(last_ema21):
            return "NEUTRAL", 0

        if last_ema9 > last_ema21 and last_close > last_ema9:
            return "BULL", 1
        elif last_ema9 < last_ema21 and last_close < last_ema9:
            return "BEAR", -1
        else:
            return "NEUTRAL", 0

    # ─────────────────────────────────────────────────────────────────────
    # COMPONENT 2: SPY MOMENTUM (TICK PROXY)
    # ─────────────────────────────────────────────────────────────────────

    def _spy_momentum(self) -> int:
        """
        SPY momentum score using Alpaca bars.

        Bar score:
          3 consecutive green bars → +2
          3 consecutive red bars   → -2
          else                     → 0

        VWAP position:
          SPY above VWAP → +1
          SPY below VWAP → -1

        Total range: -3 to +3.
        """
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()
            df = fetcher.get_ohlcv("SPY", interval="5minute", lookback_days=1)
            if df is not None and not df.empty and len(df) >= 3:
                return self._score_spy_df(df)
        except Exception as exc:
            logger.debug(f"SPY momentum via Alpaca failed: {exc}")

        return 0  # neutral fallback

    def _score_spy_df(self, df: pd.DataFrame) -> int:
        """Score SPY momentum from an OHLCV DataFrame."""
        closes = df["close"].values.astype(float)
        opens  = df["open"].values.astype(float)

        # Consecutive bar direction (last 3 bars)
        last3_green = all(closes[i] > opens[i] for i in [-3, -2, -1])
        last3_red   = all(closes[i] < opens[i] for i in [-3, -2, -1])

        bar_score = 0
        if last3_green:
            bar_score = 2
        elif last3_red:
            bar_score = -2

        # VWAP position
        vwap = _vwap_from_df(df)
        last_close = float(closes[-1])
        vwap_score = 1 if (vwap > 0 and last_close >= vwap) else -1

        return bar_score + vwap_score

    # ─────────────────────────────────────────────────────────────────────
    # COMPONENT 3: VOLATILITY REGIME (UVXY)
    # ─────────────────────────────────────────────────────────────────────

    def _volatility_regime(self) -> bool:
        """
        Returns True if a fear spike is detected in UVXY.

        Checks two conditions via Alpaca daily bars (lookback_days=7):
          - Single-day change > UVXY_FEAR_THRESHOLD (10%)
          - 5-day change > UVXY_FEAR_THRESHOLD (10%)

        Returns False on any data error.
        """
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()
            df = fetcher.get_ohlcv("UVXY", interval="day", lookback_days=7)

            if df is None or df.empty or len(df) < 2:
                logger.debug("UVXY: insufficient daily bars for volatility check")
                return False

            closes = df["close"].values.astype(float)

            # Single-day change: last close vs second-to-last close
            day_change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100.0
            if day_change_pct > UVXY_FEAR_THRESHOLD:
                logger.info(
                    f"[{format_ist_timestamp()}] UVXY fear spike: "
                    f"{day_change_pct:.1f}% day change"
                )
                return True

            # 5-day change: last close vs oldest available close
            change_5d = (closes[-1] - closes[0]) / closes[0] * 100.0
            if change_5d > UVXY_FEAR_THRESHOLD:
                logger.info(
                    f"[{format_ist_timestamp()}] UVXY 5-day fear spike: "
                    f"{change_5d:.1f}%"
                )
                return True

        except Exception as exc:
            logger.debug(f"UVXY volatility check failed: {exc}")

        return False

    # ─────────────────────────────────────────────────────────────────────
    # COMPONENT 4: ADVANCE / DECLINE PROXY
    # ─────────────────────────────────────────────────────────────────────

    def _ad_proxy(self) -> int:
        """
        Proxy for broad market advance/decline using SPY, QQQ, and DIA
        vs their 20-day SMA on daily bars.

        Returns:
          +1  if SPY and QQQ are both above their 20-day SMA (bull regime)
          -1  if both are below (bear regime)
           0  otherwise (mixed)
        """
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()

            proxies = ["SPY", "QQQ", "DIA"]
            above_sma: Dict[str, bool] = {}

            for sym in proxies:
                try:
                    df = fetcher.get_ohlcv(sym, interval="day", lookback_days=30)
                    if df is None or len(df) < 20:
                        logger.debug(f"AD proxy {sym}: insufficient daily bars (got {len(df) if df is not None else 0})")
                        continue
                    closes  = df["close"].values.astype(float)
                    sma20   = float(np.mean(closes[-20:]))
                    last_px = float(closes[-1])
                    above_sma[sym] = last_px > sma20
                except Exception as exc:
                    logger.debug(f"AD proxy {sym} failed: {exc}")

            # Need SPY and QQQ at minimum for the verdict
            if "SPY" not in above_sma or "QQQ" not in above_sma:
                return 0

            spy_bull = above_sma["SPY"]
            qqq_bull = above_sma["QQQ"]

            if spy_bull and qqq_bull:
                return 1    # bull regime
            elif not spy_bull and not qqq_bull:
                return -1   # bear regime
            else:
                return 0    # mixed

        except Exception as exc:
            logger.debug(f"AD proxy computation failed: {exc}")
            return 0


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_market_internals_instance: Optional[MarketInternals] = None


def get_market_internals() -> MarketInternals:
    """Return the singleton MarketInternals instance."""
    global _market_internals_instance
    if _market_internals_instance is None:
        _market_internals_instance = MarketInternals()
        logger.info(
            f"[{format_ist_timestamp()}] MarketInternals singleton initialized"
        )
    return _market_internals_instance


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from utils import setup_logging

    setup_logging(level="INFO")

    print("=== Market Internals Breadth Engine ===")
    mi = get_market_internals()

    print("Fetching breadth data (may take 15-30 seconds)...")
    data = mi.get_breadth()

    print(f"\nBREADTH SCORE : {data['breadth_score']:.1f} / 100")
    print(f"BIAS          : {data['bias']}")
    print(f"Sector Score  : {data['sector_score']} ({data['bullish_sectors']} bull / {data['bearish_sectors']} bear)")
    print(f"SPY Momentum  : {data['spy_momentum']:+d}")
    print(f"Volatility    : {'FEAR SPIKE' if data['volatility_flag'] else 'Normal'}")
    print(f"A/D Regime    : {data['ad_regime']:+d}")
    print(f"Reason        : {data['reason']}")
    print(f"Timestamp     : {data['ts']}")

    print("\nSector labels:")
    for sym, label in data["sectors"].items():
        print(f"  {sym:6s}  {label}")

    ok_long,  r_long  = mi.is_long_ok()
    ok_short, r_short = mi.is_short_ok()
    size_mult = mi.get_size_multiplier()

    print(f"\nLONG  OK? {ok_long}  — {r_long}")
    print(f"SHORT OK? {ok_short}  — {r_short}")
    print(f"Size multiplier: {size_mult}x")
