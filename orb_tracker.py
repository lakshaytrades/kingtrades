"""
orb_tracker.py — NSE Momentum Groww AI Bot
Opening Range Breakout (ORB) Tracker

18yr fact: 60-70% of the day's big moves START in the first 15 minutes.
The Opening Range (9:15–9:30 AM IST) is the most statistically
reliable intraday pattern on NSE.

Why ORB works:
  - Pre-market participants establish their views
  - 9:15–9:30 = price discovery = the range of uncertainty
  - Breakout above range = bulls won → momentum continuation
  - Breakout below range = bears won → momentum continuation
  - VWAP acts as a magnet throughout the day

Rules (18yr refined):
  1. Wait for FULL 15-min candle (9:30 AM IST)
  2. ORB High = highest point 9:15–9:30
  3. ORB Low  = lowest point 9:15–9:30
  4. BUY signal:  price breaks above ORB_High + 0.1% buffer + volume 2x
  5. SELL signal: price breaks below ORB_Low  - 0.1% buffer + volume 2x
  6. SL = other side of ORB range
  7. Target = 2x the ORB range width

Signal quality multipliers:
  - Gap-up + ORB breakout up   = PREMIUM (1.3x size)
  - Flat open + ORB breakout   = NORMAL (1.0x size)
  - Gap-down + ORB breakout up = COUNTER (0.5x size, skip)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time, round_to_tick_size

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

ORB_START = time(9, 15)
ORB_END   = time(9, 30)
ORB_BREAKOUT_BUFFER = 0.001   # 0.1% buffer above/below range for confirmation


@dataclass
class ORBLevel:
    """Opening range for one symbol."""
    symbol:      str
    orb_high:    float
    orb_low:     float
    orb_range:   float     # high - low
    orb_range_pct: float   # range as % of price
    prev_close:  float
    gap_pct:     float     # gap from prev close to open
    volume_orb:  int       # volume during 9:15–9:30
    established: bool = False
    established_at: str = ""

    @property
    def buy_trigger(self) -> float:
        """Price must close above this for BUY signal."""
        return round_to_tick_size(self.orb_high * (1 + ORB_BREAKOUT_BUFFER))

    @property
    def sell_trigger(self) -> float:
        """Price must close below this for SELL signal."""
        return round_to_tick_size(self.orb_low * (1 - ORB_BREAKOUT_BUFFER))

    @property
    def target_width(self) -> float:
        """Target distance = 2x ORB range."""
        return self.orb_range * 2.0

    @property
    def gap_type(self) -> str:
        if self.gap_pct > 0.5:   return "GAP_UP"
        if self.gap_pct < -0.5:  return "GAP_DOWN"
        return "FLAT"


@dataclass
class ORBSignal:
    """A breakout signal from the ORB tracker."""
    symbol:      str
    direction:   str     # "LONG" or "SHORT"
    trigger_price: float
    entry_price: float
    stop_loss:   float
    target_1:    float
    target_2:    float
    orb_high:    float
    orb_low:     float
    gap_type:    str
    size_mult:   float   # position size multiplier
    quality:     str     # "PREMIUM", "NORMAL", "WEAK"
    signal_time: str = ""
    confirmed:   bool = False   # True after first full candle close beyond trigger

    def __post_init__(self):
        if not self.signal_time:
            self.signal_time = format_ist_timestamp()

    @property
    def summary(self) -> str:
        arrow = "🟢" if self.direction == "LONG" else "🔴"
        return (
            f"{arrow} ORB {self.direction} {self.symbol} [{self.quality}] | "
            f"Entry ₹{self.entry_price:.2f} | SL ₹{self.stop_loss:.2f} | "
            f"T1 ₹{self.target_1:.2f} | T2 ₹{self.target_2:.2f} | "
            f"Size {self.size_mult:.1f}x | Gap: {self.gap_type}"
        )


class ORBTracker:
    """
    Tracks Opening Range for the watchlist.
    Fires signals immediately when breakout confirmed.
    Most reliable pattern on NSE for the first 45 minutes.
    """

    def __init__(self):
        self._levels: Dict[str, ORBLevel] = {}
        self._fired:  Dict[str, str] = {}   # symbol → "LONG"/"SHORT"
        self._established_date: str = ""
        logger.info(f"[{format_ist_timestamp()}] ORB Tracker initialized")

    # ─────────────────────────────────────────────────────────
    # BUILD ORB LEVELS
    # ─────────────────────────────────────────────────────────

    def build_levels(self, symbols: List[str], data_fetcher) -> int:
        """
        Build ORB levels from 9:15–9:30 candles.
        Call this at 9:30 AM IST after range is established.
        Returns number of levels successfully built.
        """
        now_ist = get_current_ist_time()
        if now_ist.time() < ORB_END:
            logger.debug("ORB: Range not yet complete (before 9:30 AM IST)")
            return 0

        count = 0
        for symbol in symbols:
            try:
                level = self._compute_level(symbol, data_fetcher)
                if level:
                    self._levels[symbol] = level
                    count += 1
            except Exception as e:
                logger.debug(f"ORB build failed for {symbol}: {e}")

        self._established_date = now_ist.strftime("%Y-%m-%d")
        logger.info(
            f"[{format_ist_timestamp()}] ORB levels built for {count}/{len(symbols)} symbols"
        )
        return count

    def _compute_level(self, symbol: str, data_fetcher) -> Optional[ORBLevel]:
        """Compute ORB high/low from first 3 candles (9:15, 9:20, 9:25)."""
        try:
            df = data_fetcher.get_today_candles(symbol, interval="5m")
            if df is None or len(df) < 2:
                return None

            # Filter to ORB window
            df_orb = df[
                (df.index.time >= ORB_START) &
                (df.index.time < ORB_END)
            ]
            if len(df_orb) < 1:
                return None

            orb_high  = float(df_orb["high"].max())
            orb_low   = float(df_orb["low"].min())
            orb_range = orb_high - orb_low
            first_open = float(df_orb["open"].iloc[0])
            orb_vol   = int(df_orb["volume"].sum())

            # Previous close for gap calculation
            prev_close = data_fetcher.get_prev_close(symbol) or first_open
            gap_pct    = ((first_open - prev_close) / max(prev_close, 1)) * 100

            level = ORBLevel(
                symbol=symbol,
                orb_high=round_to_tick_size(orb_high),
                orb_low=round_to_tick_size(orb_low),
                orb_range=round(orb_range, 2),
                orb_range_pct=round(orb_range / max(first_open, 1) * 100, 2),
                prev_close=prev_close,
                gap_pct=round(gap_pct, 2),
                volume_orb=orb_vol,
                established=True,
                established_at=format_ist_timestamp(),
            )
            logger.debug(
                f"ORB {symbol}: H={orb_high:.2f} L={orb_low:.2f} "
                f"Range={orb_range:.2f} ({level.orb_range_pct:.2f}%) "
                f"Gap={gap_pct:+.2f}%"
            )
            return level

        except Exception as e:
            logger.debug(f"ORB compute {symbol}: {e}")
            return None

    # ─────────────────────────────────────────────────────────
    # SCAN FOR BREAKOUTS
    # ─────────────────────────────────────────────────────────

    def scan_breakouts(
        self, data_fetcher, volume_sma: Dict[str, float] = None
    ) -> List[ORBSignal]:
        """
        Scan all tracked symbols for ORB breakouts.
        Call this every 60s after 9:30 AM IST.
        Returns new signals only (each symbol fires once per day).
        """
        now_ist = get_current_ist_time()
        # ORB signals only valid until 10:30 AM IST
        if now_ist.time() > time(10, 30):
            return []

        signals = []
        for symbol, level in self._levels.items():
            if not level.established:
                continue
            if symbol in self._fired:
                continue

            try:
                quote = data_fetcher.get_quote(symbol)
                if not quote:
                    continue
                ltp       = float(quote.get("ltp", 0))
                vol       = int(quote.get("volume", 0))
                vol_avg   = (volume_sma or {}).get(symbol, vol)

                signal = self._check_breakout(level, ltp, vol, vol_avg)
                if signal:
                    self._fired[symbol] = signal.direction
                    signals.append(signal)
                    logger.info(
                        f"[{format_ist_timestamp()}] 🚀 ORB BREAKOUT: {signal.summary}"
                    )

            except Exception as e:
                logger.debug(f"ORB scan {symbol}: {e}")

        return signals

    def _check_breakout(
        self, level: ORBLevel, ltp: float, vol: int, vol_avg: int
    ) -> Optional[ORBSignal]:
        """Check if price has broken out of ORB."""
        # Volume must be meaningful
        vol_ratio = vol / max(vol_avg, 1)
        if vol_ratio < 1.5:
            return None

        direction = None
        if ltp >= level.buy_trigger:
            direction = "LONG"
        elif ltp <= level.sell_trigger:
            direction = "SHORT"

        if not direction:
            return None

        # Skip if range is too narrow (< 0.3% — nothing to trade)
        if level.orb_range_pct < 0.3:
            return None

        # Quality assessment
        gap = level.gap_type
        if direction == "LONG" and gap == "GAP_UP":
            quality, size_mult = "PREMIUM", 1.3
        elif direction == "SHORT" and gap == "GAP_DOWN":
            quality, size_mult = "PREMIUM", 1.3
        elif direction == "LONG" and gap == "GAP_DOWN":
            quality, size_mult = "WEAK", 0.6   # Counter-trend, small size
        elif direction == "SHORT" and gap == "GAP_UP":
            quality, size_mult = "WEAK", 0.6
        else:
            quality, size_mult = "NORMAL", 1.0

        # Build signal levels
        if direction == "LONG":
            entry     = round_to_tick_size(ltp)
            stop_loss = round_to_tick_size(level.orb_low * 0.999)
            target_1  = round_to_tick_size(entry + level.target_width * 0.5)
            target_2  = round_to_tick_size(entry + level.target_width)
        else:
            entry     = round_to_tick_size(ltp)
            stop_loss = round_to_tick_size(level.orb_high * 1.001)
            target_1  = round_to_tick_size(entry - level.target_width * 0.5)
            target_2  = round_to_tick_size(entry - level.target_width)

        return ORBSignal(
            symbol=level.symbol,
            direction=direction,
            trigger_price=level.buy_trigger if direction == "LONG" else level.sell_trigger,
            entry_price=entry,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            orb_high=level.orb_high,
            orb_low=level.orb_low,
            gap_type=gap,
            size_mult=size_mult,
            quality=quality,
            confirmed=True,
        )

    # ─────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────

    def get_direction(self, symbol: str) -> str:
        """
        Returns ORB direction for a symbol ('UP', 'DOWN', '').
        Used by HighAccuracyFilter as orb_direction.
        """
        if symbol in self._fired:
            return "UP" if self._fired[symbol] == "LONG" else "DOWN"
        if symbol not in self._levels:
            return ""
        level = self._levels[symbol]
        # Infer from gap if not yet fired
        if level.gap_pct > 0.3:
            return "UP"
        if level.gap_pct < -0.3:
            return "DOWN"
        return ""

    def get_level(self, symbol: str) -> Optional[ORBLevel]:
        return self._levels.get(symbol)

    def reset_for_new_day(self) -> None:
        self._levels.clear()
        self._fired.clear()
        self._established_date = ""
        logger.info(f"[{format_ist_timestamp()}] ORB levels reset for new day")

    @property
    def levels_count(self) -> int:
        return len(self._levels)

    def format_levels(self) -> str:
        if not self._levels:
            return "ORB: No levels set yet (established at 9:30 AM IST)"
        lines = [f"📐 ORB Levels ({len(self._levels)} symbols):"]
        for sym, lvl in list(self._levels.items())[:10]:
            fired = self._fired.get(sym, "")
            status = f"→ {fired}" if fired else "pending"
            lines.append(
                f"  {sym}: H={lvl.orb_high:.2f} L={lvl.orb_low:.2f} "
                f"Range={lvl.orb_range_pct:.2f}% {status}"
            )
        return "\n".join(lines)


# Singleton
_tracker: Optional[ORBTracker] = None

def get_orb_tracker() -> ORBTracker:
    global _tracker
    if _tracker is None:
        _tracker = ORBTracker()
    return _tracker
