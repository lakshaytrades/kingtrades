from dataclasses import dataclass
from datetime import datetime, time, date
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import logging

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
    OR_CANDLES          = 3
    MAX_OR_WIDTH_PCT    = 3.0
    MAX_GAP_PCT         = 3.0
    MIN_VOLUME_RATIO    = 1.5
    RR_RATIO            = 2.5
    VALID_UNTIL_MINUTES = 30

    def __init__(self):
        # symbol -> (ORBSetup, date) — prevents recalculating the same symbol twice per day
        self._cache: Dict[str, Tuple[ORBSetup, date]] = {}

    def is_orb_time(self) -> bool:
        t = get_current_ist_time().time()
        return _ORB_WINDOW_START <= t <= _ORB_WINDOW_END

    def scan_symbols(self, symbols: List[str], data_fetcher) -> List[ORBSetup]:
        if not self.is_orb_time():
            logger.debug(
                f"[{format_ist_timestamp()}] ORB scan skipped — outside 9:31–9:45 window"
            )
            return []

        results: List[ORBSetup] = []
        for sym in symbols:
            try:
                setup = self.analyze_symbol(sym, data_fetcher)
                if setup and setup.is_valid and setup.breakout_direction != "NONE":
                    results.append(setup)
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] ORB scan error {sym}: {e}")

        results.sort(key=lambda s: s.confidence, reverse=True)
        logger.info(
            f"[{format_ist_timestamp()}] ORB scan: "
            f"{len(results)} valid setups from {len(symbols)} symbols"
        )
        return results

    def analyze_symbol(self, symbol: str, data_fetcher) -> Optional[ORBSetup]:
        today = get_current_ist_time().date()
        cached_setup, cached_date = self._cache.get(symbol, (None, None))
        if cached_setup is not None and cached_date == today:
            return cached_setup

        df = self._fetch_5m_data(symbol, data_fetcher)
        if df is None or len(df) < self.OR_CANDLES + 1:
            logger.debug(f"{symbol}: insufficient candles for ORB analysis")
            return None

        df = self._normalize_columns(df)
        df = self._filter_today(df)
        if df is None or len(df) < self.OR_CANDLES + 1:
            logger.debug(f"{symbol}: insufficient today candles after filtering")
            return None

        or_high, or_low, avg_volume = self._calculate_or(df)
        if or_high <= 0 or or_low <= 0 or or_high <= or_low:
            return None

        or_width_pct = (or_high - or_low) / or_low * 100

        def _invalid_setup(reason: str) -> ORBSetup:
            logger.debug(f"{symbol}: ORB invalid — {reason}")
            s = ORBSetup(
                symbol=symbol, or_high=or_high, or_low=or_low,
                or_width_pct=or_width_pct, breakout_direction="NONE",
                breakout_price=0.0, entry_price=0.0, stop_loss=0.0,
                target=0.0, volume_ratio=0.0, confidence=0.0,
                candles_used=self.OR_CANDLES, formed_at=format_ist_timestamp(),
                is_valid=False,
            )
            self._cache[symbol] = (s, today)
            return s

        if or_width_pct > self.MAX_OR_WIDTH_PCT:
            return _invalid_setup(f"OR width {or_width_pct:.2f}% > {self.MAX_OR_WIDTH_PCT}% (choppy)")

        # Gap check: compare first candle open vs previous close approximation.
        # yfinance/Groww returns today's first open; prior close is not always available,
        # so we approximate gap as |open - or_low| / or_low when or candle count == 1,
        # or use the first candle open vs the first close of OR window directly.
        first_open  = float(df.iloc[0]["open"])
        first_close = float(df.iloc[0]["close"])
        # "gap" for ORB purposes = how far today opened from previous session anchor.
        # Without prev_close readily available we use intra-candle price displacement.
        # When prev_close IS available in df columns, prefer it.
        if "prev_close" in df.columns and float(df.iloc[0].get("prev_close", 0)) > 0:
            prev_close = float(df.iloc[0]["prev_close"])
            gap_pct = abs(first_open - prev_close) / prev_close * 100
        else:
            # Fallback: use OR width as a proxy; gap is already penalised via OR width check.
            gap_pct = abs(first_open - first_close) / first_close * 100 if first_close > 0 else 0.0

        if gap_pct > self.MAX_GAP_PCT:
            return _invalid_setup(f"gap {gap_pct:.2f}% > {self.MAX_GAP_PCT}% (gap traders dominate)")

        direction, breakout_price, volume_ratio = self._check_breakout(
            df, or_high, or_low, avg_volume
        )

        if direction == "NONE":
            setup = ORBSetup(
                symbol=symbol, or_high=or_high, or_low=or_low,
                or_width_pct=or_width_pct, breakout_direction="NONE",
                breakout_price=breakout_price, entry_price=0.0, stop_loss=0.0,
                target=0.0, volume_ratio=volume_ratio, confidence=0.0,
                candles_used=self.OR_CANDLES, formed_at=format_ist_timestamp(),
                is_valid=True,
            )
            self._cache[symbol] = (setup, today)
            return setup

        sl_dist: float
        if direction == "LONG":
            entry_price = breakout_price
            stop_loss   = or_low
            sl_dist     = entry_price - stop_loss
            target      = round(entry_price + self.RR_RATIO * sl_dist, 2)
        else:
            entry_price = breakout_price
            stop_loss   = or_high
            sl_dist     = stop_loss - entry_price
            target      = round(entry_price - self.RR_RATIO * sl_dist, 2)

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
            f"Entry: ₹{entry_price:.2f} | SL: ₹{stop_loss:.2f} | "
            f"Target: ₹{target:.2f} | Vol: {volume_ratio:.1f}x | Conf: {confidence:.0f}%"
        )
        return setup

    def to_trade_signal(self, setup: ORBSetup) -> Optional["TradeSignal"]:
        if not setup.is_valid or setup.breakout_direction == "NONE":
            return None

        try:
            from signal_generator import TradeSignal
        except ImportError:
            logger.error("signal_generator.TradeSignal import failed")
            return None

        sl_dist = abs(setup.entry_price - setup.stop_loss)
        if sl_dist <= 0:
            return None

        risk_reward = abs(setup.target - setup.entry_price) / sl_dist

        # Extended target at RR + 0.5 for target_2
        if setup.breakout_direction == "LONG":
            target_2 = round(setup.entry_price + (self.RR_RATIO + 0.5) * sl_dist, 2)
        else:
            target_2 = round(setup.entry_price - (self.RR_RATIO + 0.5) * sl_dist, 2)

        return TradeSignal(
            symbol=setup.symbol,
            direction=setup.breakout_direction,
            signal_score=setup.confidence,
            entry_price=setup.entry_price,
            stop_loss=setup.stop_loss,
            target_1=setup.target,
            target_2=target_2,
            risk_reward=round(risk_reward, 2),
            atr=round(setup.or_high - setup.or_low, 2),
            patterns=["ORB_BREAKOUT"],
            timeframe_alignment={
                "5m": setup.breakout_direction,
                "15m": "NEUTRAL",
                "1h": "NEUTRAL",
            },
            signal_time=setup.formed_at,
            rationale=(
                f"ORB {setup.breakout_direction} | "
                f"OR: ₹{setup.or_low:.2f}–₹{setup.or_high:.2f} "
                f"({setup.or_width_pct:.2f}%) | "
                f"Vol: {setup.volume_ratio:.1f}x surge | "
                f"Confidence: {setup.confidence:.0f}%"
            ),
            quality_grade="A" if setup.confidence >= 80 else "B",
            size_multiplier=1.0,
        )

    def format_telegram_alert(self, setup: ORBSetup) -> str:
        arrow = "🟢" if setup.breakout_direction == "LONG" else "🔴"
        breakout_desc = (
            f"Closed above ₹{setup.or_high:,.2f}"
            if setup.breakout_direction == "LONG"
            else f"Closed below ₹{setup.or_low:,.2f}"
        )
        rr = f"{self.RR_RATIO:.1f}:1"
        return (
            f"🎯 ORB BREAKOUT — {setup.symbol} ({setup.breakout_direction}) {arrow}\n"
            f"OR Range: ₹{setup.or_low:,.2f} – ₹{setup.or_high:,.2f} "
            f"({setup.or_width_pct:.1f}% wide)\n"
            f"Breakout: {breakout_desc}\n"
            f"Entry: ₹{setup.entry_price:,.2f} | "
            f"SL: ₹{setup.stop_loss:,.2f} | "
            f"Target: ₹{setup.target:,.2f}\n"
            f"R:R {rr} | Volume: {setup.volume_ratio:.1f}x surge\n"
            f"Confidence: {setup.confidence:.0f}%\n"
            f"⚡ OPENING RANGE — First 30 min setup"
        )

    def _calculate_or(self, df: pd.DataFrame) -> Tuple[float, float, float]:
        or_candles = df.head(self.OR_CANDLES)
        or_high    = float(or_candles["high"].max())
        or_low     = float(or_candles["low"].min())
        avg_volume = (
            float(or_candles["volume"].mean())
            if "volume" in or_candles.columns
            else 0.0
        )
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

        # Use the most recent post-OR candle (the one that may have confirmed)
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
        if data_fetcher is not None and hasattr(data_fetcher, "get_ohlcv"):
            try:
                df = data_fetcher.get_ohlcv(symbol, interval="5m", days=1)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.debug(f"Alpaca fetch failed for {symbol}: {e}")

        try:
            import yfinance as yf
            df = yf.download(
                symbol,
                period="1d",
                interval="5m",
                progress=False,
                auto_adjust=True,
            )
            if df is not None and not df.empty:
                if isinstance(df.columns, __import__('pandas').MultiIndex):
                    df.columns = df.columns.droplevel(1)
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
                filtered = df[df.index.date == today_ist]
                return filtered if not filtered.empty else None
            return df
        except Exception as e:
            logger.debug(f"_filter_today error: {e}")
            return df

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        # yfinance MultiIndex columns appear as tuples — flatten them
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower()
                          for col in df.columns]
        col_map = {
            col: col.lower()
            for col in df.columns
            if col.lower() in ("open", "high", "low", "close", "volume")
        }
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

        # Tighter OR = higher-quality breakout
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

        # Small gap = cleaner price discovery on breakout
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
