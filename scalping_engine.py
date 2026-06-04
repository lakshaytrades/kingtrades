from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import logging

import numpy as np
import pandas as pd

from utils import get_current_ist_time, format_ist_timestamp

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# Session windows (ET)
_OPENING_DRIVE_START = time(9, 30)
_OPENING_DRIVE_END   = time(10, 30)
_AFTERNOON_START     = time(14, 30)
_AFTERNOON_END       = time(15, 30)


@dataclass
class ScalpSignal:
    symbol: str
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float        # 0.2% from entry
    target: float           # 0.4% from entry (2:1)
    momentum_pct: float     # % burst that triggered this
    volume_ratio: float
    rsi: float
    max_hold_minutes: int = 15
    confidence: float = 0.0
    reason: str = ""


class ScalpingEngine:
    SCALP_TARGET_PCT  = 0.40
    SCALP_STOP_PCT    = 0.20
    MIN_MOMENTUM_PCT  = 0.30
    MIN_VOLUME_RATIO  = 2.0
    RSI_LONG_MIN      = 55.0
    RSI_LONG_MAX      = 75.0
    RSI_SHORT_MIN     = 25.0
    RSI_SHORT_MAX     = 45.0
    MAX_HOLD_MINUTES  = 15
    MAX_SCALP_POSITIONS = 3

    def __init__(self):
        # symbol -> entry_time for tracking active scalps
        self._active_scalps: Dict[str, datetime] = {}
        # Symbols auto-expired in the most recent scan — caller (main.py) should force-close these
        self.expired_symbols: List[str] = []

    def is_scalp_time(self) -> bool:
        from datetime import datetime as _dt
        t = _dt.now(ET).time()
        return (
            _OPENING_DRIVE_START <= t <= _OPENING_DRIVE_END
            or _AFTERNOON_START <= t <= _AFTERNOON_END
        )

    def scan(
        self,
        symbols: List[str],
        data_fetcher,
        nifty_change_pct: float = 0.0,
    ) -> List[ScalpSignal]:
        self._expire_stale_scalps()

        if not self.is_scalp_time():
            logger.debug(
                f"[{format_ist_timestamp()}] Scalp scan skipped — "
                "outside Opening Drive / Afternoon Drive window"
            )
            return []

        active_count = len(self._active_scalps)
        if active_count >= self.MAX_SCALP_POSITIONS:
            logger.debug(
                f"[{format_ist_timestamp()}] Scalp scan skipped — "
                f"{active_count} active scalp(s) at limit ({self.MAX_SCALP_POSITIONS})"
            )
            return []

        slots_available = self.MAX_SCALP_POSITIONS - active_count
        signals: List[ScalpSignal] = []

        for sym in symbols:
            if sym in self._active_scalps:
                continue
            try:
                sig = self.analyze_symbol(sym, data_fetcher, nifty_change_pct)
                if sig is not None:
                    signals.append(sig)
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] Scalp scan error {sym}: {e}")

        signals.sort(key=lambda s: s.confidence, reverse=True)
        top = signals[:slots_available]

        if top:
            logger.info(
                f"[{format_ist_timestamp()}] Scalp scan: "
                f"{len(top)} signal(s) from {len(symbols)} symbols"
            )
        return top

    def analyze_symbol(
        self,
        symbol: str,
        data_fetcher,
        nifty_change_pct: float,
    ) -> Optional[ScalpSignal]:
        df = self._fetch_5m_data(symbol, data_fetcher)
        if df is None or len(df) < 22:
            return None

        df = self._normalize_columns(df)
        df = self._filter_today(df)
        if df is None or len(df) < 5:
            return None

        momentum_pct, volume_ratio = self._calculate_momentum(df)
        if abs(momentum_pct) < self.MIN_MOMENTUM_PCT:
            return None
        if volume_ratio < self.MIN_VOLUME_RATIO:
            return None

        rsi = self._calculate_rsi(df["close"])
        vwap = self._calculate_vwap(df)
        current_price = float(df["close"].iloc[-1])

        direction: Optional[str] = None

        if momentum_pct > 0:
            long_ok = (
                self.RSI_LONG_MIN <= rsi <= self.RSI_LONG_MAX
                and current_price > vwap
                and nifty_change_pct >= -0.5   # don't scalp long in a falling market
            )
            if long_ok:
                direction = "LONG"
        else:
            short_ok = (
                self.RSI_SHORT_MIN <= rsi <= self.RSI_SHORT_MAX
                and current_price < vwap
                and nifty_change_pct <= 0.5
            )
            if short_ok:
                direction = "SHORT"

        if direction is None:
            return None

        if direction == "LONG":
            stop_loss = round(current_price * (1 - self.SCALP_STOP_PCT / 100), 2)
            target    = round(current_price * (1 + self.SCALP_TARGET_PCT / 100), 2)
        else:
            stop_loss = round(current_price * (1 + self.SCALP_STOP_PCT / 100), 2)
            target    = round(current_price * (1 - self.SCALP_TARGET_PCT / 100), 2)

        confidence = self._score_confidence(
            momentum_pct=momentum_pct,
            volume_ratio=volume_ratio,
            rsi=rsi,
            above_vwap=(current_price > vwap),
            direction=direction,
        )

        above_vwap_str = "YES" if current_price > vwap else "NO"
        reason = (
            f"Burst +{abs(momentum_pct):.2f}% | "
            f"Vol {volume_ratio:.1f}x | "
            f"RSI {rsi:.1f} | "
            f"Above VWAP: {above_vwap_str} | "
            f"Nifty: {nifty_change_pct:+.2f}%"
        )

        logger.info(
            f"[{format_ist_timestamp()}] SCALP {direction} {symbol} | "
            f"Entry: ${current_price:.2f} | SL: ${stop_loss:.2f} | "
            f"Target: ${target:.2f} | {reason}"
        )
        return ScalpSignal(
            symbol=symbol,
            direction=direction,
            entry_price=current_price,
            stop_loss=stop_loss,
            target=target,
            momentum_pct=momentum_pct,
            volume_ratio=volume_ratio,
            rsi=rsi,
            max_hold_minutes=self.MAX_HOLD_MINUTES,
            confidence=confidence,
            reason=reason,
        )

    def _calculate_momentum(self, df: pd.DataFrame) -> Tuple[float, float]:
        recent = df.tail(2)
        if len(recent) < 2:
            return 0.0, 0.0

        prev_close    = float(recent["close"].iloc[-2])
        current_close = float(recent["close"].iloc[-1])
        if prev_close <= 0:
            return 0.0, 0.0

        momentum_pct = (current_close - prev_close) / prev_close * 100

        avg_volume = df["volume"].rolling(20).mean().iloc[-1] if "volume" in df.columns else 0.0
        current_vol = float(recent["volume"].iloc[-1]) if "volume" in df.columns else 0.0
        volume_ratio = current_vol / avg_volume if avg_volume > 0 else 1.0

        return momentum_pct, volume_ratio

    def _calculate_rsi(self, close: pd.Series, period: int = 14) -> float:
        if len(close) < period + 1:
            return 50.0
        try:
            delta  = close.diff()
            gain   = delta.where(delta > 0, 0.0)
            loss   = -delta.where(delta < 0, 0.0)
            avg_gain = gain.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
            avg_loss = loss.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(100.0 - (100.0 / (1.0 + rs)), 2)
        except Exception:
            return 50.0

    def _calculate_vwap(self, df: pd.DataFrame) -> float:
        try:
            required = {"high", "low", "close", "volume"}
            if not required.issubset(set(df.columns)):
                return float(df["close"].iloc[-1]) if "close" in df.columns else 0.0
            typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
            vol           = df["volume"].replace(0, np.nan).fillna(1)
            vwap          = (typical_price * vol).cumsum() / vol.cumsum()
            return float(vwap.iloc[-1])
        except Exception:
            return float(df["close"].iloc[-1]) if "close" in df.columns else 0.0

    def format_telegram_alert(self, sig: ScalpSignal) -> str:
        arrow = "🟢" if sig.direction == "LONG" else "🔴"
        stop_pct = self.SCALP_STOP_PCT
        target_pct = self.SCALP_TARGET_PCT
        above_vwap = "YES" if "Above VWAP: YES" in sig.reason else "NO"
        return (
            f"⚡ SCALP SIGNAL — {sig.symbol} ({sig.direction}) {arrow}\n"
            f"Entry: ${sig.entry_price:,.2f} | "
            f"SL: ${sig.stop_loss:,.2f} (-{stop_pct:.1f}%)\n"
            f"Target: ${sig.target:,.2f} (+{target_pct:.1f}%) | "
            f"Max hold: {sig.max_hold_minutes} min\n"
            f"Momentum: {sig.momentum_pct:+.2f}% burst | "
            f"Volume: {sig.volume_ratio:.1f}x\n"
            f"RSI: {sig.rsi:.1f} | Above VWAP: {above_vwap}\n"
            f"Quick trade — take profit fast"
        )

    def should_exit_scalp(
        self,
        symbol: str,
        current_price: float,
        entry_price: float,
        direction: str,
        entry_time: datetime,
    ) -> Tuple[bool, str]:
        now_et = datetime.now(ET)
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=ET)

        elapsed_minutes = (now_et - entry_time).total_seconds() / 60

        if direction == "LONG":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        if pnl_pct >= self.SCALP_TARGET_PCT:
            return True, f"TARGET HIT +{pnl_pct:.2f}%"
        if pnl_pct <= -self.SCALP_STOP_PCT:
            return True, f"STOP HIT {pnl_pct:.2f}%"
        if elapsed_minutes >= self.MAX_HOLD_MINUTES:
            return True, f"TIME LIMIT {elapsed_minutes:.0f}min — forced exit"

        return False, ""

    def register_scalp(self, symbol: str, entry_time: Optional[datetime] = None) -> None:
        t = entry_time or datetime.now(ET)
        self._active_scalps[symbol] = t
        logger.debug(f"[{format_ist_timestamp()}] Scalp registered: {symbol} at {t}")

    def close_scalp(self, symbol: str) -> None:
        if symbol in self._active_scalps:
            del self._active_scalps[symbol]
            logger.debug(f"[{format_ist_timestamp()}] Scalp closed: {symbol}")

    def active_scalp_count(self) -> int:
        return len(self._active_scalps)

    def _expire_stale_scalps(self) -> List[str]:
        now_et = datetime.now(ET)
        stale = [
            sym for sym, entry_time in self._active_scalps.items()
            if (now_et - (entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=ET))
                ).total_seconds() / 60 >= self.MAX_HOLD_MINUTES
        ]
        for sym in stale:
            logger.info(
                f"[{format_ist_timestamp()}] Auto-expiring stale scalp: {sym} "
                f"(>{self.MAX_HOLD_MINUTES}min)"
            )
            del self._active_scalps[sym]
        self.expired_symbols.extend(stale)   # accumulate; main.py clears after consuming
        return stale

    def _fetch_5m_data(self, symbol: str, data_fetcher) -> Optional[pd.DataFrame]:
        if data_fetcher is not None and hasattr(data_fetcher, "get_ohlcv"):
            try:
                df = data_fetcher.get_ohlcv(symbol, interval="5minute", lookback_days=1)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.debug(f"Alpaca fetch failed for {symbol}: {e}")

        # Fallback: try via data_fetch_alpaca singleton
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()
            df = fetcher.get_ohlcv(symbol, interval="5minute", lookback_days=1)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug(f"Alpaca singleton fetch failed for {symbol}: {e}")

        return None

    def _filter_today(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        try:
            today_et = get_current_ist_time().date()
            if isinstance(df.index, pd.DatetimeIndex):
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                df.index = df.index.tz_convert(ET)
                filtered = df[df.index.date == today_et]
                return filtered if not filtered.empty else None
            return df
        except Exception as e:
            logger.debug(f"_filter_today error: {e}")
            return df

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
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
        momentum_pct: float,
        volume_ratio: float,
        rsi: float,
        above_vwap: bool,
        direction: str,
    ) -> float:
        score = 40.0

        # Momentum strength
        abs_mom = abs(momentum_pct)
        if abs_mom >= 0.6:
            score += 20
        elif abs_mom >= 0.45:
            score += 14
        elif abs_mom >= self.MIN_MOMENTUM_PCT:
            score += 8

        # Volume conviction
        if volume_ratio >= 3.5:
            score += 20
        elif volume_ratio >= 2.5:
            score += 14
        elif volume_ratio >= self.MIN_VOLUME_RATIO:
            score += 8

        # RSI in optimal zone (not at extreme)
        if direction == "LONG":
            if 60 <= rsi <= 70:
                score += 12
            elif self.RSI_LONG_MIN <= rsi < 60:
                score += 6
        else:
            if 30 <= rsi <= 40:
                score += 12
            elif 40 < rsi <= self.RSI_SHORT_MAX:
                score += 6

        # VWAP alignment
        if (direction == "LONG" and above_vwap) or (direction == "SHORT" and not above_vwap):
            score += 8

        return round(min(max(score, 0.0), 100.0), 1)


    def to_trade_signal(self, sig: ScalpSignal):
        """Convert ScalpSignal to TradeSignal for execution."""
        try:
            from signal_generator import TradeSignal
            from pattern_recognition import IndicatorSet
            ind = IndicatorSet(rsi=sig.rsi, volume_ratio=sig.volume_ratio)
            # T1 = 0.4% (2:1), T2 = 0.6% (3:1) — different targets so exits don't fire simultaneously
            t2_pct = self.SCALP_TARGET_PCT * 1.5 / 100
            if sig.direction == "LONG":
                target_2 = round(sig.entry_price * (1 + t2_pct), 2)
            else:
                target_2 = round(sig.entry_price * (1 - t2_pct), 2)
            sl_dist = abs(sig.entry_price - sig.stop_loss) or 0.01
            tgt_dist = abs(sig.target - sig.entry_price) or sl_dist * 2
            ts = TradeSignal(
                symbol=sig.symbol,
                direction=sig.direction,
                entry_price=sig.entry_price,
                stop_loss=sig.stop_loss,
                target_1=sig.target,
                target_2=target_2,
                signal_score=sig.confidence,
                risk_reward=round(tgt_dist / sl_dist, 2),
                atr=round(sl_dist, 4),
                patterns=["SCALP_MOMENTUM"],
                indicators=ind,
                timeframe_alignment={"aligned_count": 2},
                size_multiplier=0.5,   # half size for scalps — tight stop, fast exit
            )
            return ts
        except Exception as e:
            logger.warning(f"ScalpSignal → TradeSignal conversion failed: {e}")
            return None


_scalping_instance: Optional[ScalpingEngine] = None


def get_scalping_engine() -> ScalpingEngine:
    global _scalping_instance
    if _scalping_instance is None:
        _scalping_instance = ScalpingEngine()
    return _scalping_instance
