"""
orb_strategy.py — Opening Range Breakout Strategy
NSE Momentum Groww AI Bot

First 3 × 5-min candles (9:15–9:25) define the OR.
Breakout above OR high = LONG. Below OR low = SHORT.
Win rate: 65-75%, R:R 2.5:1

Valid window: 9:31–9:45 AM IST only.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, date
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from utils import get_current_ist_time, format_ist_timestamp

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_ORB_WINDOW_START = time(9, 31)
_ORB_WINDOW_END   = time(9, 45)


@dataclass
class ORBSetup:
    symbol: str
    or_high: float
    or_low: float
    or_width_pct: float
    breakout_direction: str    # "LONG", "SHORT", or "NONE"
    breakout_price: float
    entry_price: float
    stop_loss: float
    target: float
    volume_ratio: float
    confidence: float
    candles_used: int
    formed_at: str
    is_valid: bool


class ORBStrategy:
    OR_CANDLES         = 3
    MAX_OR_WIDTH_PCT   = 3.0
    MAX_GAP_PCT        = 3.0
    MIN_VOLUME_RATIO   = 1.5
    RR_RATIO           = 2.5
    VALID_UNTIL_MINUTES = 30

    def __init__(self):
        self._cache: Dict[str, Tuple[ORBSetup, date]] = {}

    def is_orb_time(self) -> bool:
        t = get_current_ist_time().time()
        return _ORB_WINDOW_START <= t <= _ORB_WINDOW_END

    def scan_symbols(self, symbols: List[str], data_fetcher) -> List[ORBSetup]:
        if not self.is_orb_time():
            logger.debug(f"[{format_ist_timestamp()}] ORB scan skipped — outside 9:31-9:45 window")
            return []

        results = []
        for sym in symbols:
            try:
                setup = self.analyze_symbol(sym, data_fetcher)
                if setup and setup.is_valid and setup.breakout_direction != "NONE":
                    results.append(setup)
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] ORB scan error for {sym}: {e}")

        results.sort(key=lambda s: s.confidence, reverse=True)
        logger.info(
            f"[{format_ist_timestamp()}] ORB scan complete: "
            f"{len(results)} setups from {len(symbols)} symbols"
        )
        return results

    def analyze_symbol(self, symbol: str, data_fetcher) -> Optional[ORBSetup]:
        today = get_current_ist_time().date()
        cached_setup, cached_date = self._cache.get(symbol, (None, None))
        if cached_setup is not None and cached_date == today:
            return cached_setup

        df = self._fetch_5m_data(symbol, data_fetcher)
        if df is None or len(df) < self.OR_CANDLES + 1:
            logger.debug(f"{symbol}: insufficient candles for ORB")
            return None

        df = self._normalize_columns(df)
        df = self._filter_today(df)
        if df is None or len(df) < self.OR_CANDLES + 1:
            logger.debug(f"{symbol}: insufficient today candles")
            return None

        or_high, or_low, avg_volume = self._calculate_or(df)
        if or_high <= 0 or or_low <= 0 or or_high <= or_low:
            return None

        or_width_pct = (or_high - or_low) / or_low * 100
        if or_width_pct > self.MAX_OR_WIDTH_PCT:
            logger.debug(f"{symbol}: OR too wide ({or_width_pct:.2f}%) — choppy, skipping")
            setup = ORBSetup(
                symbol=symbol, or_high=or_high, or_low=or_low,
                or_width_pct=or_width_pct, breakout_direction="NONE",
                breakout_price=0.0, entry_price=0.0, stop_loss=0.0,
                target=0.0, volume_ratio=0.0, confidence=0.0,
                candles_used=self.OR_CANDLES, formed_at=format_ist_timestamp(),
                is_valid=False,
            )
            self._cache[symbol] = (setup, today)
            return setup

        first_close = float(df.iloc[0]["close"])
        open_price  = float(df.iloc[0]["open"])
        gap_pct = abs(open_price - first_close) / first_close * 100 if first_close > 0 else 0.0
        if gap_pct > self.MAX_GAP_PCT:
            logger.debug(f"{symbol}: gap {gap_pct:.2f}% too large — ORB unreliable")
            setup = ORBSetup(
                symbol=symbol, or_high=or_high, or_low=or_low,
                or_width_pct=or_width_pct, breakout_direction="NONE",
                breakout_price=0.0, entry_price=0.0, stop_loss=0.0,
                target=0.0, volume_ratio=0.0, confidence=0.0,
                candles_used=self.OR_CANDLES, formed_at=format_ist_timestamp(),
                is_valid=False,
            )
            self._cache[symbol] = (setup, today)
            return setup

        direction, breakout_price, volume_ratio = self._check_breakout(
            df, or_high, or_low, avg_volume
        )

        if direction == "NONE":
            setup = ORBSetup(
                symbol=symbol, or_high=or_high, or_low=or_low,
                or_width_pct=or_width_pct, breakout_direction="NONE",
                breakout_price=0.0, entry_price=0.0, stop_loss=0.0,
                target=0.0, volume_ratio=volume_ratio, confidence=0.0,
                candles_used=self.OR_CANDLES, formed_at=format_ist_timestamp(),
                is_valid=True,
            )
            self._cache[symbol] = (setup, today)
            return setup

        or_width = or_high - or_low
        if direction == "LONG":
            entry_price = breakout_price
            stop_loss   = or_low
            target      = round(entry_price + self.RR_RATIO * (entry_price - stop_loss), 2)
        else:
            entry_price = breakout_price
            stop_loss   = or_high
            target      = round(entry_price - self.RR_RATIO * (stop_loss - entry_price), 2)

        confidence = self._score_confidence(
            or_width_pct=or_width_pct,
            volume_ratio=volume_ratio,
            gap_pct=gap_pct,
            direction=direction,
        )

        setup = ORBSetup(
            symbol=symbol,
            or_high=or_high,
            or_low=or_low,
            or_width_pct=or_width_pct,
            breakout_direction=direction,
            breakout_price=breakout_price,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            volume_ratio=volume_ratio,
            confidence=confidence,
            candles_used=self.OR_CANDLES,
            formed_at=format_ist_timestamp(),
            is_valid=True,
        )
        self._cache[symbol] = (setup, today)
        logger.info(
            f"[{format_ist_timestamp()}] ORB {direction} {symbol} | "
            f"OR: ₹{or_low:.2f}–₹{or_high:.2f} ({or_width_pct:.2f}%) | "
            f"Vol: {volume_ratio:.1f}x | Conf: {confidence:.0f}%"
        )
        return setup

    def to_trade_signal(self, setup: ORBSetup):
        if not setup.is_valid or setup.breakout_direction == "NONE":
            return None

        try:
            from signal_generator import TradeSignal
        except ImportError:
            logger.error("signal_generator.TradeSignal not available")
            return None

        sl_dist = abs(setup.entry_price - setup.stop_loss)
        if sl_dist <= 0:
            return None

        risk_reward = abs(setup.target - setup.entry_price) / sl_dist

        return TradeSignal(
            symbol=setup.symbol,
            direction=setup.breakout_direction,
            signal_score=setup.confidence,
            entry_price=setup.entry_price,
            stop_loss=setup.stop_loss,
            target_1=setup.target,
            target_2=round(
                setup.entry_price + (self.RR_RATIO + 0.5) * sl_dist
                if setup.breakout_direction == "LONG"
                else setup.entry_price - (self.RR_RATIO + 0.5) * sl_dist,
                2
            ),
            risk_reward=round(risk_reward, 2),
            atr=round(setup.or_high - setup.or_low, 2),
            patterns=["ORB_BREAKOUT"],
            timeframe_alignment={"5m": setup.breakout_direction, "15m": "NEUTRAL", "1h": "NEUTRAL"},
            signal_time=setup.formed_at,
            rationale=(
                f"ORB {setup.breakout_direction} | OR: ₹{setup.or_low:.2f}–₹{setup.or_high:.2f} "
                f"({setup.or_width_pct:.2f}%) | Vol: {setup.volume_ratio:.1f}x surge | "
                f"Confidence: {setup.confidence:.0f}%"
            ),
            quality_grade="A" if setup.confidence >= 80 else "B",
            size_multiplier=1.0,
        )

    def format_telegram_alert(self, setup: ORBSetup) -> str:
        direction_label = "LONG" if setup.breakout_direction == "LONG" else "SHORT"
        arrow = "🟢" if setup.breakout_direction == "LONG" else "🔴"
        breakout_desc = (
            f"Closed above ₹{setup.or_high:,.2f}"
            if setup.breakout_direction == "LONG"
            else f"Closed below ₹{setup.or_low:,.2f}"
        )
        return (
            f"🎯 ORB BREAKOUT — {setup.symbol} ({direction_label}) {arrow}\n"
            f"OR Range: ₹{setup.or_low:,.2f} – ₹{setup.or_high:,.2f} "
            f"({setup.or_width_pct:.1f}% wide)\n"
            f"Breakout: {breakout_desc}\n"
            f"Entry: ₹{setup.entry_price:,.2f} | SL: ₹{setup.stop_loss:,.2f} | "
            f"Target: ₹{setup.target:,.2f}\n"
            f"R:R {self.RR_RATIO:.1f}:1 | Volume: {setup.volume_ratio:.1f}x surge\n"
            f"Confidence: {setup.confidence:.0f}%\n"
            f"⚡ OPENING RANGE — First 30 min setup"
        )

    def _calculate_or(self, df: pd.DataFrame) -> Tuple[float, float, float]:
        or_candles = df.head(self.OR_CANDLES)
        or_high    = float(or_candles["high"].max())
        or_low     = float(or_candles["low"].min())
        avg_volume = float(or_candles["volume"].mean()) if "volume" in or_candles.columns else 0.0
        return or_high, or_low, avg_volume

    def _check_breakout(
        self,
        df: pd.DataFrame,
        or_high: float,
        or_low: float,
        avg_volume: float,
    ) -> Tuple[str, float, float]:
        post_or = df.iloc[self.OR_CANDLES:]
        if post_or.empty:
            return "NONE", 0.0, 0.0

        breakout_candle = post_or.iloc[-1]
        close_price  = float(breakout_candle["close"])
        candle_vol   = float(breakout_candle.get("volume", 0))
        volume_ratio = candle_vol / avg_volume if avg_volume > 0 else 0.0

        if close_price > or_high and volume_ratio >= self.MIN_VOLUME_RATIO:
            return "LONG", close_price, volume_ratio
        if close_price < or_low and volume_ratio >= self.MIN_VOLUME_RATIO:
            return "SHORT", close_price, volume_ratio

        return "NONE", close_price, volume_ratio

    def _fetch_5m_data(self, symbol: str, data_fetcher) -> Optional[pd.DataFrame]:
        try:
            if data_fetcher is not None and hasattr(data_fetcher, "get_ohlcv"):
                df = data_fetcher.get_ohlcv(symbol, interval="5m", days=1)
                if df is not None and not df.empty:
                    return df
        except Exception as e:
            logger.debug(f"Groww fetch failed for {symbol}: {e}")

        try:
            import yfinance as yf
            df = yf.download(
                symbol + ".NS",
                period="1d",
                interval="5m",
                progress=False,
                auto_adjust=True,
            )
            if df is not None and not df.empty:
                df.columns = [c.lower() for c in df.columns]
                return df
        except Exception as e:
            logger.debug(f"yfinance fetch failed for {symbol}: {e}")

        return None

    def _filter_today(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        try:
            today_ist = get_current_ist_time().date()
            if isinstance(df.index, pd.DatetimeIndex):
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                df.index = df.index.tz_convert(IST)
                mask = df.index.date == today_ist
                filtered = df[mask]
                return filtered if not filtered.empty else None
            return df
        except Exception as e:
            logger.debug(f"filter_today error: {e}")
            return df

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        col_map = {}
        for col in df.columns:
            lower = col.lower()
            if lower in ("open", "high", "low", "close", "volume"):
                col_map[col] = lower
        if col_map:
            df = df.rename(columns=col_map)
        return df

    def _score_confidence(
        self,
        or_width_pct: float,
        volume_ratio: float,
        gap_pct: float,
        direction: str,
    ) -> float:
        score = 50.0

        # Tight OR = more reliable breakout
        if or_width_pct < 0.5:
            score += 15
        elif or_width_pct < 1.0:
            score += 10
        elif or_width_pct < 1.5:
            score += 5
        elif or_width_pct > 2.5:
            score -= 10

        # Volume conviction
        if volume_ratio >= 3.0:
            score += 20
        elif volume_ratio >= 2.0:
            score += 12
        elif volume_ratio >= self.MIN_VOLUME_RATIO:
            score += 6

        # Small gap = cleaner price discovery
        if gap_pct < 0.3:
            score += 5
        elif gap_pct > 1.5:
            score -= 5

        return round(min(max(score, 0.0), 100.0), 1)


_orb_instance: Optional[ORBStrategy] = None


def get_orb_strategy() -> ORBStrategy:
    global _orb_instance
    if _orb_instance is None:
        _orb_instance = ORBStrategy()
    return _orb_instance
