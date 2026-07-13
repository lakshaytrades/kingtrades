"""
Orient Layer — market structure analysis.
Wyckoff phases, Elliott Wave, VSA, Market Profile, and RegimeOracle.
All methods accept numpy arrays or pandas Series.
"""

import logging
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)


class WyckoffAnalyzer:
    """Detect Wyckoff accumulation/markup/distribution/markdown phases."""

    PHASES = ["UNKNOWN", "ACCUMULATION", "MARKUP", "DISTRIBUTION", "MARKDOWN",
              "SPRING", "UTAD", "SELLING_CLIMAX", "AUTOMATIC_RALLY"]

    def detect_phase(self, closes: np.ndarray, volumes: np.ndarray) -> str:
        if closes is None or len(closes) < 30:
            return "UNKNOWN"
        try:
            closes = np.asarray(closes, dtype=float)
            volumes = np.asarray(volumes, dtype=float) if volumes is not None else np.ones_like(closes)

            recent_30 = closes[-30:]
            recent_vol = volumes[-30:]

            rng = recent_30.max() - recent_30.min()
            if rng == 0:
                return "UNKNOWN"

            # Range position: where is current price in 30-bar range?
            pos = (closes[-1] - recent_30.min()) / rng  # 0=bottom, 1=top

            # Volume trend: is volume increasing or decreasing?
            vol_first_half = np.mean(recent_vol[:15])
            vol_second_half = np.mean(recent_vol[15:])
            vol_expanding = vol_second_half > vol_first_half * 1.2
            vol_contracting = vol_second_half < vol_first_half * 0.8

            # Price momentum: last 5 vs prior 10
            mom_5 = closes[-1] / closes[-5] - 1 if len(closes) >= 5 else 0
            mom_10 = closes[-5] / closes[-15] - 1 if len(closes) >= 15 else 0

            # Selling climax: sharp drop + very high volume in recent bars
            max_vol_idx = np.argmax(recent_vol)
            at_climax_vol = recent_vol[max_vol_idx] > np.mean(recent_vol) * 2.5
            price_at_climax = (recent_30[max_vol_idx] - recent_30.min()) / rng
            if at_climax_vol and price_at_climax < 0.25 and mom_5 > 0:
                return "SPRING"  # high vol at lows then bounce = spring
            if at_climax_vol and price_at_climax < 0.25:
                return "SELLING_CLIMAX"

            # UTAD (Upthrust After Distribution): spike above range then fail
            if at_climax_vol and price_at_climax > 0.85 and mom_5 < -0.005:
                return "UTAD"

            # Markup: price in upper 40%, vol expanding, positive momentum
            if pos > 0.60 and mom_5 > 0.005 and (vol_expanding or mom_10 > 0):
                return "MARKUP"

            # Markdown: price in lower 40%, vol expanding, negative momentum
            if pos < 0.40 and mom_5 < -0.005 and (vol_expanding or mom_10 < 0):
                return "MARKDOWN"

            # Distribution: price in upper zone but vol contracting + momentum stalling
            if pos > 0.60 and vol_contracting and abs(mom_5) < 0.003:
                return "DISTRIBUTION"

            # Accumulation: price in lower zone, vol contracting, momentum stalling
            if pos < 0.40 and vol_contracting and abs(mom_5) < 0.003:
                return "ACCUMULATION"

            return "UNKNOWN"
        except Exception as e:
            logger.debug(f"Wyckoff error: {e}")
            return "UNKNOWN"


class ElliottWaveCounter:
    """Detect Elliott Wave structure: 5-wave impulse or ABC correction."""

    def detect_wave(self, closes: np.ndarray) -> str:
        if closes is None or len(closes) < 20:
            return "UNKNOWN"
        try:
            closes = np.asarray(closes, dtype=float)
            pivots = self._zigzag_pivots(closes, pct_threshold=0.01)
            if len(pivots) < 5:
                return "UNKNOWN"

            # Check last 5 pivots for impulse wave pattern
            pts = [closes[i] for i in pivots[-5:]]
            directions = [1 if pts[i] > pts[i-1] else -1 for i in range(1, len(pts))]

            # 5-wave bullish impulse: up-down-up-down-up (alternating starting up)
            if directions == [1, -1, 1, -1, 1]:
                # Fibonacci validation: wave 3 > wave 1, wave 2 < 0.618 of wave 1
                w1 = abs(pts[1] - pts[0])
                w2 = abs(pts[2] - pts[1])
                w3 = abs(pts[3] - pts[2])
                if w3 > w1 and w2 < w1 * 0.618:
                    return "WAVE5_BULL_IMPULSE"
                return "WAVE_BULL_IMPULSE"

            # 5-wave bearish impulse
            if directions == [-1, 1, -1, 1, -1]:
                return "WAVE_BEAR_IMPULSE"

            # ABC correction: 3 swings
            if len(pivots) >= 3:
                last3 = [closes[i] for i in pivots[-3:]]
                d3 = [1 if last3[i] > last3[i-1] else -1 for i in range(1, len(last3))]
                if d3 == [1, -1]:
                    return "ABC_BULL_CORRECTION"
                if d3 == [-1, 1]:
                    return "ABC_BEAR_CORRECTION"

            return "UNKNOWN"
        except Exception as e:
            logger.debug(f"Elliott error: {e}")
            return "UNKNOWN"

    def _zigzag_pivots(self, data: np.ndarray, pct_threshold: float = 0.01) -> list:
        """ZigZag pivot detection: returns indices of significant swing highs/lows."""
        pivots = [0]
        direction = 0  # 1=up, -1=down
        last_pivot_val = data[0]

        for i in range(1, len(data)):
            chg = (data[i] - last_pivot_val) / last_pivot_val if last_pivot_val != 0 else 0
            if direction == 0:
                if abs(chg) >= pct_threshold:
                    direction = 1 if chg > 0 else -1
            elif direction == 1:
                if chg <= -pct_threshold:
                    pivots.append(i - 1)
                    direction = -1
                    last_pivot_val = data[i - 1]
            else:  # direction == -1
                if chg >= pct_threshold:
                    pivots.append(i - 1)
                    direction = 1
                    last_pivot_val = data[i - 1]

        pivots.append(len(data) - 1)
        return pivots


class VSAAnalyzer:
    """Volume Spread Analysis: effort vs result pattern detection."""

    def analyze(self, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                volumes: np.ndarray) -> str:
        if closes is None or len(closes) < 5:
            return "NEUTRAL"
        try:
            closes = np.asarray(closes, dtype=float)
            highs = np.asarray(highs, dtype=float)
            lows = np.asarray(lows, dtype=float)
            volumes = np.asarray(volumes, dtype=float)

            # Use last 5 bars
            c = closes[-5:]
            h = highs[-5:]
            lo = lows[-5:]
            v = volumes[-5:]

            spreads = h - lo
            avg_spread = np.mean(spreads[:-1]) if len(spreads) > 1 else spreads[-1]
            avg_vol = np.mean(v[:-1]) if len(v) > 1 else v[-1]
            last_close = c[-1]
            last_high = h[-1]
            last_low = lo[-1]
            last_vol = v[-1]
            last_spread = spreads[-1]
            prev_close = c[-2] if len(c) >= 2 else c[-1]

            high_vol = last_vol > avg_vol * 1.8
            low_vol = last_vol < avg_vol * 0.5
            wide_spread = last_spread > avg_spread * 1.5
            narrow_spread = last_spread < avg_spread * 0.5
            close_upper = (last_close - last_low) / last_spread > 0.67 if last_spread > 0 else False
            close_lower = (last_close - last_low) / last_spread < 0.33 if last_spread > 0 else False
            up_bar = last_close > prev_close
            down_bar = last_close < prev_close

            # Stopping Volume: high vol, down bar, but closes near top — absorption
            if high_vol and down_bar and close_upper and wide_spread:
                return "STOPPING_VOLUME_BULL"

            # Upthrust: high vol, up bar, wide spread, closes near low — distribution
            if high_vol and up_bar and close_lower and wide_spread:
                return "UPTHRUST_BEAR"

            # No Demand: up bar, narrow spread, low vol — weak buying
            if low_vol and up_bar and narrow_spread:
                return "NO_DEMAND_BEAR"

            # No Supply: down bar, narrow spread, low vol — weak selling
            if low_vol and down_bar and narrow_spread:
                return "NO_SUPPLY_BULL"

            # Exhaustion: very high vol, wide spread, but closes mid — trend ending
            if high_vol and wide_spread and 0.3 < (last_close - last_low) / last_spread < 0.7:
                return "EXHAUSTION"

            return "NEUTRAL"
        except Exception as e:
            logger.debug(f"VSA error: {e}")
            return "NEUTRAL"


class MarketProfileAnalyzer:
    """Market Profile: POC, VAH, VAL, poor high/low detection."""

    def analyze(self, closes: np.ndarray, volumes: np.ndarray,
                highs: np.ndarray = None, lows: np.ndarray = None) -> dict:
        result = {"poc": 0.0, "vah": 0.0, "val": 0.0, "signal": "NEUTRAL", "poor_high": False, "poor_low": False}
        if closes is None or len(closes) < 20:
            return result
        try:
            closes = np.asarray(closes, dtype=float)
            volumes = np.asarray(volumes, dtype=float)

            # Build volume-at-price profile using 50 buckets
            price_min = closes.min()
            price_max = closes.max()
            if price_max == price_min:
                return result

            n_buckets = 50
            bucket_size = (price_max - price_min) / n_buckets
            vol_profile = np.zeros(n_buckets)

            for i, (price, vol) in enumerate(zip(closes, volumes)):
                bucket = min(int((price - price_min) / bucket_size), n_buckets - 1)
                vol_profile[bucket] += vol

            # POC = highest volume bucket
            poc_bucket = np.argmax(vol_profile)
            poc_price = price_min + poc_bucket * bucket_size + bucket_size / 2

            # Value area = 70% of total volume around POC
            total_vol = vol_profile.sum()
            va_threshold = total_vol * 0.70
            va_vol = vol_profile[poc_bucket]
            lo_idx = hi_idx = poc_bucket

            while va_vol < va_threshold and (lo_idx > 0 or hi_idx < n_buckets - 1):
                add_lo = vol_profile[lo_idx - 1] if lo_idx > 0 else 0
                add_hi = vol_profile[hi_idx + 1] if hi_idx < n_buckets - 1 else 0
                if add_hi >= add_lo and hi_idx < n_buckets - 1:
                    hi_idx += 1
                    va_vol += add_hi
                elif lo_idx > 0:
                    lo_idx -= 1
                    va_vol += add_lo
                else:
                    break

            vah = price_min + hi_idx * bucket_size + bucket_size
            val = price_min + lo_idx * bucket_size

            result["poc"] = poc_price
            result["vah"] = vah
            result["val"] = val

            current = closes[-1]
            # Signal based on position relative to value area
            if current > vah:
                result["signal"] = "ABOVE_VA_BULL"
            elif current < val:
                result["signal"] = "BELOW_VA_BEAR"
            elif current > poc_price:
                result["signal"] = "ABOVE_POC_BULL"
            else:
                result["signal"] = "BELOW_POC_BEAR"

            # Poor high: price just below prior high with low volume (unsupported)
            if highs is not None:
                highs = np.asarray(highs, dtype=float)
                rolling_high = highs[-20:].max()
                if closes[-1] / rolling_high > 0.97 and volumes[-1] < np.mean(volumes[-10:]) * 0.6:
                    result["poor_high"] = True

            return result
        except Exception as e:
            logger.debug(f"MarketProfile error: {e}")
            return result


class RegimeOracle:
    """Classify market regime from price + volume data."""

    REGIMES = ["TRENDING_BULL", "TRENDING_BEAR", "RANGING", "VOLATILE", "CRISIS"]

    def get_regime(self, closes: np.ndarray, volumes: np.ndarray = None) -> tuple:
        """Returns (regime_str, confidence_float)."""
        if closes is None or len(closes) < 20:
            return "UNKNOWN", 0.5
        try:
            closes = np.asarray(closes, dtype=float)

            # ADX proxy: directional movement strength
            if len(closes) >= 14:
                highs = closes  # approximate when only closes available
                lows = closes
                tr_vals = np.abs(np.diff(closes))
                atr = np.mean(tr_vals[-14:])
                dm_pos = np.maximum(0, np.diff(closes))
                dm_neg = np.maximum(0, -np.diff(closes))
                adx_proxy = (np.mean(dm_pos[-14:]) - np.mean(dm_neg[-14:])) / (atr + 1e-10)
            else:
                adx_proxy = 0.0

            # Bollinger Band width (normalized volatility)
            window = min(20, len(closes))
            ma = np.mean(closes[-window:])
            std = np.std(closes[-window:])
            bb_width = (2 * std) / ma if ma > 0 else 0

            # EMA trend
            ema20 = self._ema(closes, 20)
            ema50 = self._ema(closes, 50) if len(closes) >= 50 else ema20
            current = closes[-1]

            trending_up = current > ema20 and ema20 > ema50 and adx_proxy > 0.3
            trending_down = current < ema20 and ema20 < ema50 and adx_proxy < -0.3
            high_vol = bb_width > 0.06
            crisis = bb_width > 0.12

            # Volume spike check
            if volumes is not None and len(volumes) >= 5:
                volumes = np.asarray(volumes, dtype=float)
                vol_ratio = volumes[-1] / np.mean(volumes[-5:]) if np.mean(volumes[-5:]) > 0 else 1
                if vol_ratio > 3 and high_vol:
                    crisis = True

            if crisis:
                return "CRISIS", 0.85
            if trending_up:
                conf = min(0.9, 0.5 + abs(adx_proxy) * 0.5)
                return "TRENDING_BULL", conf
            if trending_down:
                conf = min(0.9, 0.5 + abs(adx_proxy) * 0.5)
                return "TRENDING_BEAR", conf
            if high_vol:
                return "VOLATILE", 0.70
            return "RANGING", 0.65
        except Exception as e:
            logger.debug(f"RegimeOracle error: {e}")
            return "UNKNOWN", 0.5

    def _ema(self, data: np.ndarray, period: int) -> float:
        if len(data) < period:
            return float(data[-1])
        alpha = 2.0 / (period + 1)
        ema = float(data[-period])
        for val in data[-period + 1:]:
            ema = alpha * float(val) + (1 - alpha) * ema
        return ema


class OrientLayer:
    """Top-level orient layer — coordinates all sub-analyzers."""

    def __init__(self):
        self.wyckoff = WyckoffAnalyzer()
        self.elliott = ElliottWaveCounter()
        self.vsa = VSAAnalyzer()
        self.profile = MarketProfileAnalyzer()
        self.regime = RegimeOracle()

    def analyze(self, symbol: str, closes: np.ndarray, volumes: np.ndarray = None,
                highs: np.ndarray = None, lows: np.ndarray = None) -> dict:
        result = {
            "regime": "UNKNOWN",
            "regime_confidence": 0.5,
            "wyckoff_phase": "UNKNOWN",
            "elliott_wave": "UNKNOWN",
            "vsa_signal": "NEUTRAL",
            "market_profile": {},
            "pattern_confluence": 0.0,
        }

        closes = np.asarray(closes, dtype=float) if closes is not None else None
        if closes is None or len(closes) < 5:
            return result

        # Use closes as proxy for highs/lows if not provided
        h = np.asarray(highs, dtype=float) if highs is not None else closes
        lo = np.asarray(lows, dtype=float) if lows is not None else closes
        v = np.asarray(volumes, dtype=float) if volumes is not None else np.ones_like(closes)

        try:
            regime_str, regime_conf = self.regime.get_regime(closes, v)
            result["regime"] = regime_str
            result["regime_confidence"] = regime_conf
        except Exception as e:
            logger.debug(f"Regime error for {symbol}: {e}")

        try:
            result["wyckoff_phase"] = self.wyckoff.detect_phase(closes, v)
        except Exception as e:
            logger.debug(f"Wyckoff error for {symbol}: {e}")

        try:
            result["elliott_wave"] = self.elliott.detect_wave(closes)
        except Exception as e:
            logger.debug(f"Elliott error for {symbol}: {e}")

        try:
            result["vsa_signal"] = self.vsa.analyze(closes, h, lo, v)
        except Exception as e:
            logger.debug(f"VSA error for {symbol}: {e}")

        try:
            result["market_profile"] = self.profile.analyze(closes, v, h, lo)
        except Exception as e:
            logger.debug(f"MarketProfile error for {symbol}: {e}")

        # Compute pattern confluence score [-10, +10]
        score = 0.0
        w = result["wyckoff_phase"]
        if w in ("MARKUP", "SPRING"):
            score += 3
        elif w in ("MARKDOWN", "UTAD", "DISTRIBUTION"):
            score -= 3

        e = result["elliott_wave"]
        if "BULL_IMPULSE" in e:
            score += 3
        elif "BEAR_IMPULSE" in e:
            score -= 3
        elif "BULL_CORRECTION" in e:
            score += 1
        elif "BEAR_CORRECTION" in e:
            score -= 1

        vsa = result["vsa_signal"]
        if "BULL" in vsa:
            score += 2
        elif "BEAR" in vsa:
            score -= 2
        elif vsa == "EXHAUSTION":
            score -= 1

        mp_sig = result["market_profile"].get("signal", "NEUTRAL")
        if "BULL" in mp_sig:
            score += 1.5
        elif "BEAR" in mp_sig:
            score -= 1.5

        result["pattern_confluence"] = max(-10.0, min(10.0, score))
        return result
