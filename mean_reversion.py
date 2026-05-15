"""
mean_reversion.py — NSE Momentum Groww AI Bot
Mean-Reversion Trading Engine — Activated in RANGING/CHOPPY Regimes

18+ years trading insight:
  "Momentum strategies destroy capital in ranging markets (35% of days).
   When ADX < 20 and price oscillates around VWAP, FADE the extremes —
   don't chase them. This single insight separates professionals from amateurs."

Strategy: Fade extreme price moves back to VWAP/equilibrium.
Works best in: RANGING, LOW_VOLATILITY, MIDDAY_CHOP, HIGH_VOLATILITY regimes.

4 Detectors:
  1. VWAP_REVERSION  — price stretched > 1.5σ from VWAP, fade back
  2. BB_FADE         — price outside Bollinger Band, fade to midline
  3. RSI_EXTREME     — RSI at extreme at key S/R level
  4. SUPPORT_BOUNCE  — price at pivot level with participation

Singleton pattern: use get_mean_reversion_engine() everywhere.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# REGIME GATE — only activate mean-reversion in these regimes
# ─────────────────────────────────────────────────────────────────────────────

MEAN_REVERSION_REGIMES = {"RANGING", "LOW_VOLATILITY", "MIDDAY_CHOP", "HIGH_VOLATILITY"}

# Hard filters — reject signal if triggered
ADX_TREND_THRESHOLD   = 25.0   # ADX > 25 → trending, use momentum instead
VOLUME_BREAKOUT_RATIO = 2.0    # Volume > 2x avg → breakout, not a fade
MAX_GAP_PCT           = 2.0    # Gap > 2% → skip (volatile open)

# Score caps per detector
SCORE_CAP_VWAP    = 85
SCORE_CAP_BB      = 80
SCORE_CAP_RSI     = 75
SCORE_CAP_SUPPORT = 78
SCORE_MULTI_CAP   = 88         # Cap when multiple detectors fire same direction

# Regime score boosts
MIDDAY_CHOP_BOOST = 8
LOW_ADX_BOOST     = 5          # ADX < 15
LOW_ADX_THRESHOLD = 15.0

# Minimum score to emit a signal
MIN_SCORE = 45.0


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MeanReversionSignal:
    """A single mean-reversion trade signal."""
    symbol:      str
    direction:   str    # "LONG" (buy dip) or "SHORT" (fade rally)
    entry_price: float
    stop_loss:   float
    target_1:    float  # Nearer target (VWAP / BB midline / pivot)
    target_2:    float  # Farther target (opposite band / next pivot)
    score:       float  # 0–100 composite confidence
    reason:      str    # Human-readable signal rationale
    strategy:    str    # "VWAP_REVERSION" | "BB_FADE" | "RSI_EXTREME" | "SUPPORT_BOUNCE"
    # Derived fields (populated automatically)
    rr_ratio:    float = field(default=0.0)   # Risk:reward ratio
    adx:         float = field(default=0.0)
    rsi:         float = field(default=50.0)
    volume_ratio: float = field(default=1.0)
    timestamp:   str = field(default="")

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_ist_timestamp()
        sl_dist = abs(self.entry_price - self.stop_loss)
        tp1_dist = abs(self.target_1 - self.entry_price)
        self.rr_ratio = round(tp1_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"MRSignal({self.symbol} {self.direction} {self.strategy} "
            f"entry={self.entry_price:.2f} sl={self.stop_loss:.2f} "
            f"tp1={self.target_1:.2f} tp2={self.target_2:.2f} "
            f"score={self.score:.0f} RR={self.rr_ratio})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR COMPUTATION (standalone — no pattern_recognition import)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Standard 14-period Wilder-smoothed RSI."""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    # Wilder smoothing (EWM with alpha = 1/period)
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def _compute_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Daily-reset VWAP = cumsum(typical_price * volume) / cumsum(volume).
    Works on intraday DataFrame.  If DataFrame has no date-level grouping,
    treats the entire series as one session (acceptable for intraday data).
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol    = df["volume"].cumsum().replace(0, np.nan)
    return cum_tp_vol / cum_vol


def _compute_vwap_std(df: pd.DataFrame, vwap: pd.Series) -> pd.Series:
    """Rolling standard deviation of (typical_price - VWAP) for bands."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    deviation = typical_price - vwap
    # Use expanding window so each bar has a meaningful std
    return deviation.expanding(min_periods=5).std().fillna(0.0)


def _compute_bollinger(close: pd.Series, period: int = 20,
                       num_std: float = 2.0) -> tuple:
    """Returns (upper, mid, lower) Bollinger Bands."""
    mid   = close.rolling(period).mean()
    std   = close.rolling(period).std(ddof=1)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Wilder-smoothed ADX.  Returns a Series aligned with df.index.
    Falls back to column 'adx' if already present in df.
    """
    if "adx" in df.columns:
        return df["adx"].fillna(20.0)

    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    # True range
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    # Directional movement
    plus_dm  = (high - high.shift(1)).clip(lower=0)
    minus_dm = (low.shift(1) - low).clip(lower=0)
    # Zero out where the opposing move is larger
    plus_dm  = plus_dm.where(plus_dm > minus_dm, 0.0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0.0)

    alpha = 1.0 / period
    tr_s     = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_s   = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    minus_s  = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    plus_di  = 100.0 * plus_s  / tr_s.replace(0, np.nan)
    minus_di = 100.0 * minus_s / tr_s.replace(0, np.nan)

    dx  = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False).mean().fillna(20.0)
    return adx


def _compute_pivots(prev_high: float, prev_low: float,
                    prev_close: float) -> Dict[str, float]:
    """
    Classic floor pivot points from previous bar's HLC.
    PP = (H+L+C)/3
    R1 = 2*PP - L   S1 = 2*PP - H
    R2 = PP + (H-L) S2 = PP - (H-L)
    """
    pp = (prev_high + prev_low + prev_close) / 3.0
    hl_range = prev_high - prev_low
    return {
        "pp": pp,
        "r1": 2.0 * pp - prev_low,
        "r2": pp + hl_range,
        "s1": 2.0 * pp - prev_high,
        "s2": pp - hl_range,
    }


def _compute_volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Current volume / 20-bar SMA of volume."""
    vol_sma = volume.rolling(period).mean()
    return (volume / vol_sma.replace(0, np.nan)).fillna(1.0)


def _ensure_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all indicators needed by the 4 detectors.
    Returns a copy with added columns (non-destructive).
    """
    df = df.copy()

    df["rsi"]          = _compute_rsi(df["close"])
    df["vwap"]         = _compute_vwap(df)
    df["vwap_std"]     = _compute_vwap_std(df, df["vwap"])
    df["volume_ratio"] = _compute_volume_ratio(df["volume"])
    df["adx"]          = _compute_adx(df)

    bb_upper, bb_mid, bb_lower = _compute_bollinger(df["close"])
    df["bb_upper"] = bb_upper
    df["bb_mid"]   = bb_mid
    df["bb_lower"] = bb_lower

    return df


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class MeanReversionEngine:
    """
    Mean-reversion engine that fades extreme price moves in ranging markets.

    Usage:
        engine = get_mean_reversion_engine()
        signals = engine.scan(symbols, fetcher, regime="RANGING")
    """

    def __init__(self):
        self._near_earnings_cache: Dict[str, bool] = {}

    # ──────────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────────

    def scan(self, symbols: List[str], fetcher, regime: str) -> List[MeanReversionSignal]:
        """
        Scan a list of symbols and return mean-reversion signals.

        Args:
            symbols: List of ticker/symbol strings.
            fetcher:  Data fetcher with get_candles(symbol, interval, bars)
                      and get_ltp(symbol) methods (same interface as
                      GrowwDataFetcher / AlpacaDataFetcher).
            regime:   Current market regime string from MarketRegimeDetector.
                      Engine silently returns [] if regime is not in
                      MEAN_REVERSION_REGIMES.

        Returns:
            List of MeanReversionSignal sorted by score descending (best first).
        """
        if regime not in MEAN_REVERSION_REGIMES:
            logger.debug(
                f"[MR] regime={regime} — mean-reversion engine inactive. "
                f"Active regimes: {MEAN_REVERSION_REGIMES}"
            )
            return []

        logger.info(
            f"[{format_ist_timestamp()}] [MR] Scanning {len(symbols)} symbols "
            f"in regime={regime}"
        )

        signals: List[MeanReversionSignal] = []

        for symbol in symbols:
            try:
                sig = self._scan_symbol(symbol, fetcher, regime)
                if sig is not None:
                    signals.append(sig)
            except Exception as exc:
                logger.warning(f"[MR] {symbol}: scan error — {exc}", exc_info=False)

        signals.sort(key=lambda s: s.score, reverse=True)
        logger.info(
            f"[{format_ist_timestamp()}] [MR] Found {len(signals)} signals "
            f"(regime={regime})"
        )
        return signals

    # ──────────────────────────────────────────────────────────────
    # PER-SYMBOL ANALYSIS
    # ──────────────────────────────────────────────────────────────

    def _scan_symbol(self, symbol: str, fetcher, regime: str) -> Optional[MeanReversionSignal]:
        """Fetch data, run hard filters, then run all 4 detectors."""
        # ── 1. Fetch OHLCV (5-min, last 60 bars ≈ 5 trading hours) ──
        try:
            df = fetcher.get_candles(symbol, interval="5min", bars=60)
        except Exception as exc:
            logger.debug(f"[MR] {symbol}: candle fetch failed — {exc}")
            return None

        if df is None or len(df) < 25:
            logger.debug(f"[MR] {symbol}: insufficient data ({len(df) if df is not None else 0} bars)")
            return None

        # Normalise column names to lowercase
        df.columns = [c.lower() for c in df.columns]
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            logger.debug(f"[MR] {symbol}: missing columns {required - set(df.columns)}")
            return None

        # ── 2. Current price ──
        try:
            ltp = float(fetcher.get_ltp(symbol))
        except Exception:
            ltp = float(df["close"].iloc[-1])

        if ltp <= 0:
            return None

        # ── 3. Hard filters ──
        if not self._passes_hard_filters(symbol, df, ltp):
            return None

        # ── 4. Compute indicators ──
        df = _ensure_indicators(df)

        # ── 5. Run detectors and collect candidates ──
        signal = self._analyze_symbol(symbol, df, ltp, regime)
        return signal

    def _analyze_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        ltp: float,
        regime: str = "RANGING",
    ) -> Optional[MeanReversionSignal]:
        """
        Run all 4 detectors.  Apply regime boosts.
        If multiple detectors fire in the same direction, combine scores.
        If conflicting signals, take none.
        Returns the highest-scoring valid signal, or None.
        """
        candidates: List[MeanReversionSignal] = []

        for detector in (
            self._detect_vwap_reversion,
            self._detect_bb_fade,
            self._detect_rsi_extreme,
            self._detect_support_bounce,
        ):
            try:
                sig = detector(symbol, df, ltp)
                if sig is not None:
                    candidates.append(sig)
            except Exception as exc:
                logger.debug(f"[MR] {symbol}: detector {detector.__name__} error — {exc}")

        if not candidates:
            return None

        # Apply regime boosts before conflict resolution
        adx_val = float(df["adx"].iloc[-1])
        for c in candidates:
            c.score = self._apply_regime_boosts(c.score, regime, adx_val)

        # Conflict resolution: group by direction
        long_sigs  = [c for c in candidates if c.direction == "LONG"]
        short_sigs = [c for c in candidates if c.direction == "SHORT"]

        if long_sigs and short_sigs:
            # Conflicting signals — take none
            logger.debug(
                f"[MR] {symbol}: conflicting signals "
                f"(LONG×{len(long_sigs)}, SHORT×{len(short_sigs)}) — skipping"
            )
            return None

        active_group = long_sigs if long_sigs else short_sigs

        if len(active_group) == 1:
            best = active_group[0]
        else:
            # Multiple detectors same direction — combine scores, use best signal's levels
            best = max(active_group, key=lambda s: s.score)
            combined_score = sum(s.score for s in active_group)
            best.score = min(combined_score, SCORE_MULTI_CAP)
            best.reason = " | ".join(s.reason for s in active_group)

        if best.score < MIN_SCORE:
            return None

        # Attach diagnostics
        best.adx          = adx_val
        best.rsi          = float(df["rsi"].iloc[-1])
        best.volume_ratio = float(df["volume_ratio"].iloc[-1])
        best.timestamp    = format_ist_timestamp()

        logger.info(
            f"[{format_ist_timestamp()}] [MR] {symbol}: {best}"
        )
        return best

    # ──────────────────────────────────────────────────────────────
    # HARD FILTERS
    # ──────────────────────────────────────────────────────────────

    def _passes_hard_filters(self, symbol: str, df: pd.DataFrame, ltp: float) -> bool:
        """
        Return False if any hard filter is triggered.

        Filters:
          - ADX > 25  (trending — use momentum engine instead)
          - Volume ratio > 2.0x  (breakout, not a fade)
          - Gap > 2%  (volatile open)
          - Near earnings (within 3 days)  [uses cache; skip if unavailable]
        """
        # ADX filter
        adx_series = _compute_adx(df)
        adx_val = float(adx_series.iloc[-1])
        if adx_val > ADX_TREND_THRESHOLD:
            logger.debug(f"[MR] {symbol}: ADX={adx_val:.1f} > {ADX_TREND_THRESHOLD} — trending, skip")
            return False

        # Volume ratio filter
        vol_ratio = _compute_volume_ratio(df["volume"])
        curr_vol_ratio = float(vol_ratio.iloc[-1])
        if curr_vol_ratio > VOLUME_BREAKOUT_RATIO:
            logger.debug(f"[MR] {symbol}: volume_ratio={curr_vol_ratio:.1f} > {VOLUME_BREAKOUT_RATIO} — breakout, skip")
            return False

        # Gap filter: compare today's open to previous bar close
        if len(df) >= 2:
            prev_close = float(df["close"].iloc[-2])
            curr_open  = float(df["open"].iloc[-1])
            if prev_close > 0:
                gap_pct = abs((curr_open - prev_close) / prev_close) * 100.0
                if gap_pct > MAX_GAP_PCT:
                    logger.debug(f"[MR] {symbol}: gap={gap_pct:.1f}% > {MAX_GAP_PCT}% — skip")
                    return False

        # Earnings filter (best-effort; failure is non-fatal)
        if self._is_near_earnings(symbol):
            logger.debug(f"[MR] {symbol}: near earnings — skip")
            return False

        return True

    def _is_near_earnings(self, symbol: str) -> bool:
        """
        Return True if symbol has earnings within 3 days.
        Uses a simple cache.  Returns False (don't skip) if data unavailable.
        """
        if symbol in self._near_earnings_cache:
            return self._near_earnings_cache[symbol]
        # Default: no earnings data → don't block the signal
        return False

    def set_earnings_calendar(self, earnings_map: Dict[str, bool]) -> None:
        """
        Inject an earnings proximity map from external source.
        earnings_map: {symbol: True/False} where True = near earnings.
        Call from main.py after fetching the economic calendar.
        """
        self._near_earnings_cache.update(earnings_map)

    # ──────────────────────────────────────────────────────────────
    # REGIME BOOSTS
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_regime_boosts(score: float, regime: str, adx: float) -> float:
        """Apply score boosts based on regime and ADX."""
        if regime == "MIDDAY_CHOP":
            score += MIDDAY_CHOP_BOOST   # Most reliable time for reversion
        if adx < LOW_ADX_THRESHOLD:
            score += LOW_ADX_BOOST       # Very choppy — reversion high probability
        return score

    # ──────────────────────────────────────────────────────────────
    # DETECTOR 1: VWAP REVERSION (score up to 85)
    # ──────────────────────────────────────────────────────────────

    def _detect_vwap_reversion(
        self, symbol: str, df: pd.DataFrame, ltp: float
    ) -> Optional[MeanReversionSignal]:
        """
        Entry: price stretched > 1.5σ from VWAP AND RSI confirms oversold/overbought.
        Requires: volume_ratio < 1.5x (fade, not a breakout).
        SL: beyond 2.5σ from VWAP.
        TP1: VWAP, TP2: 0.5σ opposite side.
        """
        last       = df.iloc[-1]
        vwap       = float(last["vwap"])
        vwap_std   = float(last["vwap_std"])
        rsi        = float(last["rsi"])
        vol_ratio  = float(last["volume_ratio"])

        if vwap <= 0 or vwap_std <= 0:
            return None

        # Volume guard: must not be a breakout
        if vol_ratio >= 1.5:
            return None

        deviation  = ltp - vwap
        dev_sigma  = deviation / vwap_std   # positive = price above VWAP

        # ── LONG signal: price > 1.5σ below VWAP, RSI oversold ──
        if dev_sigma <= -1.5 and 20.0 <= rsi <= 35.0:
            sl_price  = vwap - 2.5 * vwap_std
            tp1_price = vwap
            tp2_price = vwap + 0.5 * vwap_std

            # Score: base 60 + deviation bonus + RSI bonus
            base_score = 60.0
            base_score += min(abs(dev_sigma) - 1.5, 1.5) * 8.0   # extra stretch bonus
            base_score += max(0.0, 35.0 - rsi) * 0.5              # deeper oversold
            score = min(base_score, SCORE_CAP_VWAP)

            return MeanReversionSignal(
                symbol=symbol,
                direction="LONG",
                entry_price=round(ltp, 2),
                stop_loss=round(sl_price, 2),
                target_1=round(tp1_price, 2),
                target_2=round(tp2_price, 2),
                score=round(score, 1),
                reason=(
                    f"VWAP reversion LONG: price {abs(dev_sigma):.1f}σ below VWAP, "
                    f"RSI={rsi:.0f} (oversold), vol_ratio={vol_ratio:.1f}x"
                ),
                strategy="VWAP_REVERSION",
            )

        # ── SHORT signal: price > 1.5σ above VWAP, RSI overbought ──
        if dev_sigma >= 1.5 and 65.0 <= rsi <= 80.0:
            sl_price  = vwap + 2.5 * vwap_std
            tp1_price = vwap
            tp2_price = vwap - 0.5 * vwap_std

            base_score = 60.0
            base_score += min(dev_sigma - 1.5, 1.5) * 8.0
            base_score += max(0.0, rsi - 65.0) * 0.5
            score = min(base_score, SCORE_CAP_VWAP)

            return MeanReversionSignal(
                symbol=symbol,
                direction="SHORT",
                entry_price=round(ltp, 2),
                stop_loss=round(sl_price, 2),
                target_1=round(tp1_price, 2),
                target_2=round(tp2_price, 2),
                score=round(score, 1),
                reason=(
                    f"VWAP reversion SHORT: price {dev_sigma:.1f}σ above VWAP, "
                    f"RSI={rsi:.0f} (overbought), vol_ratio={vol_ratio:.1f}x"
                ),
                strategy="VWAP_REVERSION",
            )

        return None

    # ──────────────────────────────────────────────────────────────
    # DETECTOR 2: BB FADE (score up to 80)
    # ──────────────────────────────────────────────────────────────

    def _detect_bb_fade(
        self, symbol: str, df: pd.DataFrame, ltp: float
    ) -> Optional[MeanReversionSignal]:
        """
        Entry: price closes outside BB band AND RSI confirms extreme.
        Score boost if candle shows rejection wick (wick > body).
        SL: 0.5% beyond the band touch.
        TP1: BB midline (SMA20), TP2: opposite band.
        """
        last      = df.iloc[-1]
        bb_upper  = float(last["bb_upper"])
        bb_mid    = float(last["bb_mid"])
        bb_lower  = float(last["bb_lower"])
        rsi       = float(last["rsi"])
        close     = float(last["close"])
        open_     = float(last["open"])
        high      = float(last["high"])
        low       = float(last["low"])

        if pd.isna(bb_upper) or pd.isna(bb_lower) or bb_upper == bb_lower:
            return None

        # Rejection wick detection: wick > body
        body_size = abs(close - open_)
        upper_wick = high - max(close, open_)
        lower_wick = min(close, open_) - low
        has_rejection_wick_bull = lower_wick > body_size and lower_wick > 0
        has_rejection_wick_bear = upper_wick > body_size and upper_wick > 0

        # ── LONG: price at/below BB lower, RSI < 35 ──
        if close <= bb_lower and rsi < 35.0:
            sl_pct    = 0.005  # 0.5%
            sl_price  = bb_lower * (1.0 - sl_pct)
            tp1_price = bb_mid
            tp2_price = bb_upper

            base_score = 60.0
            # Deeper below lower band → more stretched → better fade
            band_width = bb_upper - bb_lower
            excess = (bb_lower - close) / band_width if band_width > 0 else 0.0
            base_score += min(excess * 20.0, 10.0)
            base_score += max(0.0, 35.0 - rsi) * 0.3
            if has_rejection_wick_bull:
                base_score += 5.0   # Candle rejecting the low — stronger signal
            score = min(base_score, SCORE_CAP_BB)

            return MeanReversionSignal(
                symbol=symbol,
                direction="LONG",
                entry_price=round(ltp, 2),
                stop_loss=round(sl_price, 2),
                target_1=round(tp1_price, 2),
                target_2=round(tp2_price, 2),
                score=round(score, 1),
                reason=(
                    f"BB fade LONG: close={close:.2f} at/below lower={bb_lower:.2f}, "
                    f"RSI={rsi:.0f}"
                    + (" [rejection wick]" if has_rejection_wick_bull else "")
                ),
                strategy="BB_FADE",
            )

        # ── SHORT: price at/above BB upper, RSI > 65 ──
        if close >= bb_upper and rsi > 65.0:
            sl_pct    = 0.005
            sl_price  = bb_upper * (1.0 + sl_pct)
            tp1_price = bb_mid
            tp2_price = bb_lower

            base_score = 60.0
            band_width = bb_upper - bb_lower
            excess = (close - bb_upper) / band_width if band_width > 0 else 0.0
            base_score += min(excess * 20.0, 10.0)
            base_score += max(0.0, rsi - 65.0) * 0.3
            if has_rejection_wick_bear:
                base_score += 5.0
            score = min(base_score, SCORE_CAP_BB)

            return MeanReversionSignal(
                symbol=symbol,
                direction="SHORT",
                entry_price=round(ltp, 2),
                stop_loss=round(sl_price, 2),
                target_1=round(tp1_price, 2),
                target_2=round(tp2_price, 2),
                score=round(score, 1),
                reason=(
                    f"BB fade SHORT: close={close:.2f} at/above upper={bb_upper:.2f}, "
                    f"RSI={rsi:.0f}"
                    + (" [rejection wick]" if has_rejection_wick_bear else "")
                ),
                strategy="BB_FADE",
            )

        return None

    # ──────────────────────────────────────────────────────────────
    # DETECTOR 3: RSI EXTREME (score up to 75)
    # ──────────────────────────────────────────────────────────────

    def _detect_rsi_extreme(
        self, symbol: str, df: pd.DataFrame, ltp: float
    ) -> Optional[MeanReversionSignal]:
        """
        Entry: RSI at extreme AND price at/near 20-bar high/low.
        SL: 0.7% beyond support/resistance.
        TP1: RSI 50 level (price equilibrium = VWAP or midprice).
        TP2: RSI 60 (long) / RSI 40 (short) target.
        """
        last     = df.iloc[-1]
        rsi      = float(last["rsi"])
        close    = float(last["close"])
        vwap     = float(last["vwap"])

        if len(df) < 20:
            return None

        recent_20 = df.tail(20)
        rolling_low  = float(recent_20["low"].min())
        rolling_high = float(recent_20["high"].max())

        proximity_pct = 0.003  # 0.3% tolerance

        # ── LONG: RSI < 25 AND price at/near 20-bar low ──
        if rsi < 25.0 and close > 0:
            near_support = abs(close - rolling_low) / close <= proximity_pct
            if near_support:
                sl_price  = rolling_low * (1.0 - 0.007)
                tp1_price = vwap if vwap > close else close * 1.01   # equilibrium
                tp2_price = tp1_price * 1.005                         # RSI-60 proxy

                base_score = 50.0
                base_score += max(0.0, 25.0 - rsi) * 1.0   # deeper extreme = better
                proximity_bonus = max(0.0, proximity_pct - abs(close - rolling_low) / close)
                base_score += proximity_bonus / proximity_pct * 10.0
                score = min(base_score, SCORE_CAP_RSI)

                return MeanReversionSignal(
                    symbol=symbol,
                    direction="LONG",
                    entry_price=round(ltp, 2),
                    stop_loss=round(sl_price, 2),
                    target_1=round(tp1_price, 2),
                    target_2=round(tp2_price, 2),
                    score=round(score, 1),
                    reason=(
                        f"RSI extreme LONG: RSI={rsi:.0f} (<25) near 20-bar low "
                        f"{rolling_low:.2f} (dist={abs(close - rolling_low)/close*100:.2f}%)"
                    ),
                    strategy="RSI_EXTREME",
                )

        # ── SHORT: RSI > 75 AND price at/near 20-bar high ──
        if rsi > 75.0 and close > 0:
            near_resistance = abs(close - rolling_high) / close <= proximity_pct
            if near_resistance:
                sl_price  = rolling_high * (1.0 + 0.007)
                tp1_price = vwap if vwap < close else close * 0.99
                tp2_price = tp1_price * 0.995

                base_score = 50.0
                base_score += max(0.0, rsi - 75.0) * 1.0
                proximity_bonus = max(0.0, proximity_pct - abs(close - rolling_high) / close)
                base_score += proximity_bonus / proximity_pct * 10.0
                score = min(base_score, SCORE_CAP_RSI)

                return MeanReversionSignal(
                    symbol=symbol,
                    direction="SHORT",
                    entry_price=round(ltp, 2),
                    stop_loss=round(sl_price, 2),
                    target_1=round(tp1_price, 2),
                    target_2=round(tp2_price, 2),
                    score=round(score, 1),
                    reason=(
                        f"RSI extreme SHORT: RSI={rsi:.0f} (>75) near 20-bar high "
                        f"{rolling_high:.2f} (dist={abs(close - rolling_high)/close*100:.2f}%)"
                    ),
                    strategy="RSI_EXTREME",
                )

        return None

    # ──────────────────────────────────────────────────────────────
    # DETECTOR 4: SUPPORT/RESISTANCE BOUNCE (score up to 78)
    # ──────────────────────────────────────────────────────────────

    def _detect_support_bounce(
        self, symbol: str, df: pd.DataFrame, ltp: float
    ) -> Optional[MeanReversionSignal]:
        """
        Entry: price at classic pivot level (PP/S1/S2 or R1/R2) within 0.2%.
        AND RSI in mid-zone (35–65) — mid-zone bounce, not extreme.
        AND volume_ratio > 0.8x (some participation).
        SL: 0.5% beyond pivot.
        TP1: next pivot level.
        """
        if len(df) < 2:
            return None

        last       = df.iloc[-1]
        prev       = df.iloc[-2]
        rsi        = float(last["rsi"])
        vol_ratio  = float(last["volume_ratio"])
        close      = float(last["close"])

        # RSI mid-zone gate
        if not (35.0 <= rsi <= 65.0):
            return None

        # Volume participation gate
        if vol_ratio < 0.8:
            return None

        # Compute pivots from PREVIOUS bar's HLC
        pivots = _compute_pivots(
            prev_high=float(prev["high"]),
            prev_low=float(prev["low"]),
            prev_close=float(prev["close"]),
        )

        proximity_pct = 0.002  # 0.2%

        # ── Check support pivots for LONG ──
        for support_key, next_key in [("s1", "pp"), ("s2", "s1"), ("pp", "r1")]:
            pivot_price = pivots[support_key]
            if pivot_price <= 0:
                continue
            dist = abs(close - pivot_price) / close
            if dist <= proximity_pct:
                sl_price  = pivot_price * (1.0 - 0.005)
                tp1_price = pivots[next_key]

                base_score = 55.0
                # Tighter proximity → stronger signal
                base_score += (1.0 - dist / proximity_pct) * 10.0
                base_score += (vol_ratio - 0.8) * 5.0     # more volume = better bounce
                score = min(base_score, SCORE_CAP_SUPPORT)

                return MeanReversionSignal(
                    symbol=symbol,
                    direction="LONG",
                    entry_price=round(ltp, 2),
                    stop_loss=round(sl_price, 2),
                    target_1=round(tp1_price, 2),
                    target_2=round(pivots.get(next_key, tp1_price * 1.005), 2),
                    score=round(score, 1),
                    reason=(
                        f"Support bounce LONG: close={close:.2f} at {support_key.upper()}={pivot_price:.2f} "
                        f"(dist={dist*100:.2f}%), RSI={rsi:.0f}, vol={vol_ratio:.1f}x"
                    ),
                    strategy="SUPPORT_BOUNCE",
                )

        # ── Check resistance pivots for SHORT ──
        for resist_key, next_key in [("r1", "pp"), ("r2", "r1")]:
            pivot_price = pivots[resist_key]
            if pivot_price <= 0:
                continue
            dist = abs(close - pivot_price) / close
            if dist <= proximity_pct:
                sl_price  = pivot_price * (1.0 + 0.005)
                tp1_price = pivots[next_key]

                base_score = 55.0
                base_score += (1.0 - dist / proximity_pct) * 10.0
                base_score += (vol_ratio - 0.8) * 5.0
                score = min(base_score, SCORE_CAP_SUPPORT)

                return MeanReversionSignal(
                    symbol=symbol,
                    direction="SHORT",
                    entry_price=round(ltp, 2),
                    stop_loss=round(sl_price, 2),
                    target_1=round(tp1_price, 2),
                    target_2=round(pivots.get(next_key, tp1_price * 0.995), 2),
                    score=round(score, 1),
                    reason=(
                        f"Resistance fade SHORT: close={close:.2f} at {resist_key.upper()}={pivot_price:.2f} "
                        f"(dist={dist*100:.2f}%), RSI={rsi:.0f}, vol={vol_ratio:.1f}x"
                    ),
                    strategy="SUPPORT_BOUNCE",
                )

        return None


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_engine_instance: Optional[MeanReversionEngine] = None


def get_mean_reversion_engine() -> MeanReversionEngine:
    """Return the singleton MeanReversionEngine instance."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = MeanReversionEngine()
        logger.info("[MR] MeanReversionEngine singleton created")
    return _engine_instance


# ─────────────────────────────────────────────────────────────────────────────
# QUICK SELF-TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=== MeanReversionEngine — self-test ===")
    print(f"Active regimes: {MEAN_REVERSION_REGIMES}")

    # Build a synthetic 60-bar OHLCV DataFrame with a clear stretched move
    np.random.seed(42)
    n = 60
    base_price = 500.0
    prices = [base_price]
    for _ in range(n - 1):
        prices.append(prices[-1] + np.random.randn() * 1.5)

    # Force last bar to extreme (oversold dip)
    prices[-1] = prices[0] * 0.975   # 2.5% dip from start

    df_test = pd.DataFrame({
        "open":   [p - abs(np.random.randn() * 0.3) for p in prices],
        "high":   [p + abs(np.random.randn() * 0.8) for p in prices],
        "low":    [p - abs(np.random.randn() * 0.8) for p in prices],
        "close":  prices,
        "volume": np.random.randint(50_000, 200_000, n).tolist(),
    })
    # Ensure OHLCV consistency
    df_test["high"] = df_test[["open", "high", "close"]].max(axis=1)
    df_test["low"]  = df_test[["open", "low",  "close"]].min(axis=1)

    engine = get_mean_reversion_engine()

    # Test indicator computation
    df_ind = _ensure_indicators(df_test)
    print(f"\nIndicator columns: {list(df_ind.columns)}")
    print(f"Last RSI:  {df_ind['rsi'].iloc[-1]:.1f}")
    print(f"Last VWAP: {df_ind['vwap'].iloc[-1]:.2f}")
    print(f"Last ADX:  {df_ind['adx'].iloc[-1]:.1f}")
    print(f"BB upper/mid/lower: "
          f"{df_ind['bb_upper'].iloc[-1]:.2f} / "
          f"{df_ind['bb_mid'].iloc[-1]:.2f} / "
          f"{df_ind['bb_lower'].iloc[-1]:.2f}")

    # Test regime gate
    class MockFetcher:
        def get_candles(self, symbol, interval="5min", bars=60):
            return df_test.copy()
        def get_ltp(self, symbol):
            return df_test["close"].iloc[-1]

    fetcher = MockFetcher()

    print("\n--- Test: regime=STRONG_TREND_UP (should return []) ---")
    sigs = engine.scan(["TEST"], fetcher, regime="STRONG_TREND_UP")
    print(f"Signals: {sigs}")
    assert sigs == [], "Expected no signals in trending regime"

    print("\n--- Test: regime=RANGING ---")
    sigs = engine.scan(["TEST"], fetcher, regime="RANGING")
    print(f"Signals found: {len(sigs)}")
    for s in sigs:
        print(f"  {s}")

    print("\n--- Test: regime=MIDDAY_CHOP ---")
    sigs2 = engine.scan(["TEST"], fetcher, regime="MIDDAY_CHOP")
    print(f"Signals found: {len(sigs2)}")
    for s in sigs2:
        print(f"  {s}")

    # Test singleton identity
    e1 = get_mean_reversion_engine()
    e2 = get_mean_reversion_engine()
    assert e1 is e2, "Singleton broken — two different instances returned"
    print("\nSingleton OK: same instance returned twice")

    print("\n=== All self-tests passed ===")
    sys.exit(0)
