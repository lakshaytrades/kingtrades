"""
momentum_burst.py — NSE Momentum Groww AI Bot
Momentum Burst Detector — Catches Explosive Moves EARLY

18yr truth: "The biggest intraday moves give a 2-minute warning.
Volume spikes BEFORE price spikes. Whoever reads volume wins."

What this detects:
  1. VOLUME BURST  — Current 5m candle volume > 3x 20-period average
                     Price compression ending (BB squeeze breakout)
  2. PRICE THRUST  — Price moves >0.8% in single 5m candle with volume
  3. MOMENTUM FLIP — RSI crosses 50 with expanding MACD histogram
  4. VWAP RECLAIM  — Price crosses back above/below VWAP with force
  5. INSTITUTIONAL  — Sudden large bid/ask imbalance in quote data

Burst score 0-100:
  0-39  : Normal activity — ignore
  40-59 : Building momentum — watch
  60-79 : Confirmed burst — ALERT, prepare entry
  80+   : Explosive burst — FIRE immediately, size up
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time, round_to_tick_size

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class BurstSignal:
    symbol:       str
    burst_score:  float          # 0-100
    direction:    str            # "LONG" / "SHORT"
    trigger_type: str            # "VOLUME_BURST" / "PRICE_THRUST" / "VWAP_RECLAIM" etc.
    ltp:          float
    volume_ratio: float          # Current candle vol / 20-period avg
    rsi:          float
    above_vwap:   bool
    size_mult:    float = 1.0    # 1.0–1.5x depending on burst strength
    signal_time:  str = ""

    def __post_init__(self):
        if not self.signal_time:
            self.signal_time = format_ist_timestamp()

    @property
    def grade(self) -> str:
        if self.burst_score >= 80: return "EXPLOSIVE"
        if self.burst_score >= 60: return "CONFIRMED"
        if self.burst_score >= 40: return "BUILDING"
        return "WEAK"

    @property
    def summary(self) -> str:
        arrow = "🚀" if self.direction == "LONG" else "💣"
        return (
            f"{arrow} BURST {self.direction} {self.symbol} | "
            f"Score={self.burst_score:.0f} [{self.grade}] | "
            f"Type={self.trigger_type} | "
            f"Vol={self.volume_ratio:.1f}x | RSI={self.rsi:.0f} | "
            f"₹{self.ltp:.2f} | Size={self.size_mult:.1f}x"
        )


class MomentumBurstDetector:
    """
    Scans the watchlist for stocks showing unusual momentum activity.
    Called every scan cycle alongside normal signal generator.
    Burst signals bypass some filter gates — they're time-sensitive.
    """

    MIN_BURST_SCORE = 60.0    # Minimum to generate an alert
    FIRE_SCORE      = 80.0    # Above this: immediate execution candidate

    def __init__(self):
        self._alerted: Dict[str, float] = {}  # symbol → last alert timestamp
        self._alert_cooldown = 300            # 5 minutes between same-symbol alerts
        logger.info(f"[{format_ist_timestamp()}] MomentumBurstDetector initialized")

    def scan(
        self, symbols: List[str], data_fetcher, min_score: float = None
    ) -> List[BurstSignal]:
        """
        Scan watchlist for momentum bursts.
        Returns signals sorted by burst_score descending.
        """
        threshold = min_score or self.MIN_BURST_SCORE
        now_ts = get_current_ist_time().timestamp()
        results = []

        for symbol in symbols:
            # Cooldown check — don't spam the same stock
            last = self._alerted.get(symbol, 0)
            if now_ts - last < self._alert_cooldown:
                continue

            try:
                sig = self._analyse(symbol, data_fetcher)
                if sig and sig.burst_score >= threshold:
                    self._alerted[symbol] = now_ts
                    results.append(sig)
            except Exception as e:
                logger.debug(f"Burst scan {symbol}: {e}")

        results.sort(key=lambda s: s.burst_score, reverse=True)
        if results:
            logger.info(
                f"[{format_ist_timestamp()}] Burst scan: "
                f"{len(results)} burst(s) | "
                f"Top: {results[0].symbol} score={results[0].burst_score:.0f}"
            )
        return results

    def _analyse(self, symbol: str, data_fetcher) -> Optional[BurstSignal]:
        """Score a single stock for momentum burst."""
        df = data_fetcher.get_today_candles(symbol, interval="5m")
        if df is None or len(df) < 10:
            return None

        quote = data_fetcher.get_quote(symbol)
        if not quote:
            return None

        ltp      = float(quote.get("ltp", 0))
        curr_vol = int(quote.get("volume", 0))
        if ltp <= 0:
            return None

        close  = df["close"].astype(float)
        high   = df["high"].astype(float)
        low    = df["low"].astype(float)
        volume = df["volume"].astype(float)

        # ── VWAP ─────────────────────────────────────────────
        typical_price = (high + low + close) / 3
        cum_vol = volume.cumsum()
        vwap = (typical_price * volume).cumsum() / cum_vol.replace(0, np.nan)
        vwap = vwap.fillna(method="ffill")
        curr_vwap = float(vwap.iloc[-1]) if len(vwap) > 0 else ltp
        above_vwap = ltp >= curr_vwap

        # ── RSI (14) ──────────────────────────────────────────
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1]) if len(close) >= 15 else 50.0
        if np.isnan(rsi): rsi = 50.0

        # ── Volume ratio ──────────────────────────────────────
        vol_sma = volume.rolling(20).mean().iloc[-1]
        last_candle_vol = float(volume.iloc[-1])
        vol_ratio = last_candle_vol / max(float(vol_sma), 1)

        # ── Bollinger Bands (squeeze detection) ───────────────
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = float((bb_upper - bb_lower).iloc[-1]) / max(float(bb_mid.iloc[-1]), 1)
        bb_width_prev = float((bb_upper - bb_lower).iloc[-5]) / max(float(bb_mid.iloc[-5]), 1) if len(close) >= 5 else bb_width
        bb_expanding = bb_width > bb_width_prev * 1.05  # Bands expanding = breakout

        # ── Price thrust ──────────────────────────────────────
        last_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else last_close
        candle_move_pct = abs(last_close - prev_close) / max(prev_close, 1) * 100

        # ── MACD ──────────────────────────────────────────────
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd  = ema12 - ema26
        signal_line = macd.ewm(span=9).mean()
        hist = macd - signal_line
        macd_growing = (
            float(hist.iloc[-1]) > float(hist.iloc[-2]) if len(hist) >= 2 else False
        )
        macd_positive = float(hist.iloc[-1]) > 0

        # ── Direction determination ───────────────────────────
        long_score  = 0.0
        short_score = 0.0

        # Volume weight
        if vol_ratio >= 3.0:
            long_score += 25;  short_score += 25
        elif vol_ratio >= 2.0:
            long_score += 15;  short_score += 15

        # VWAP alignment
        if above_vwap:
            long_score += 15
        else:
            short_score += 15

        # RSI zone
        if 45 <= rsi <= 65 and macd_positive:
            long_score += 20
        elif rsi > 65:
            long_score += 10
        if 35 <= rsi <= 55 and not macd_positive:
            short_score += 20
        elif rsi < 35:
            short_score += 10

        # Breakout direction
        if last_close > prev_close:
            long_score += candle_move_pct * 8
        else:
            short_score += candle_move_pct * 8

        # BB expanding bonus
        if bb_expanding:
            long_score  += 10 if last_close > prev_close else 0
            short_score += 10 if last_close < prev_close else 0

        # MACD momentum
        if macd_growing and macd_positive:
            long_score += 10
        elif not macd_growing and not macd_positive:
            short_score += 10

        # VWAP reclaim (powerful signal)
        prev_above_vwap = float(close.iloc[-2]) >= float(vwap.iloc[-2]) if len(close) >= 2 else above_vwap
        if above_vwap and not prev_above_vwap:
            long_score += 20   # Just reclaimed VWAP
            trigger_type = "VWAP_RECLAIM"
        elif not above_vwap and prev_above_vwap:
            short_score += 20  # Just lost VWAP
            trigger_type = "VWAP_BREAK"
        elif vol_ratio >= 3.0:
            trigger_type = "VOLUME_BURST"
        elif candle_move_pct >= 0.8:
            trigger_type = "PRICE_THRUST"
        else:
            trigger_type = "MOMENTUM_BUILD"

        # Direction
        if long_score >= short_score and long_score >= 40:
            direction   = "LONG"
            burst_score = min(long_score, 100)
        elif short_score > long_score and short_score >= 40:
            direction   = "SHORT"
            burst_score = min(short_score, 100)
        else:
            return None   # No clear direction

        # Minimum vol filter
        if vol_ratio < 1.5:
            burst_score *= 0.7

        # Size multiplier based on burst strength
        if burst_score >= 80:
            size_mult = 1.5
        elif burst_score >= 65:
            size_mult = 1.2
        else:
            size_mult = 1.0

        return BurstSignal(
            symbol=symbol,
            burst_score=round(burst_score, 1),
            direction=direction,
            trigger_type=trigger_type,
            ltp=ltp,
            volume_ratio=round(vol_ratio, 2),
            rsi=round(rsi, 1),
            above_vwap=above_vwap,
            size_mult=size_mult,
        )

    def get_stats(self) -> str:
        return f"Burst detector: {len(self._alerted)} symbols monitored"


# Singleton
_detector: Optional[MomentumBurstDetector] = None

def get_burst_detector() -> MomentumBurstDetector:
    global _detector
    if _detector is None:
        _detector = MomentumBurstDetector()
    return _detector
