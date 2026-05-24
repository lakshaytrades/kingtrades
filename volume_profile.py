"""
volume_profile.py — NSE Momentum Groww AI Bot
Volume Profile Analysis: VPOC, VAH, VAL, HVN, LVN

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).

Volume Profile is the institutional edge. Every major market maker,
prop desk, and hedge fund uses volume profile to identify:
  • VPOC (Volume Point of Control): Price with highest traded volume = magnetic level
  • VAH (Value Area High): Top of the 70% volume zone = resistance
  • VAL (Value Area Low): Bottom of the 70% volume zone = support
  • HVN (High Volume Node): Price levels where institutions transact heavily = S/R
  • LVN (Low Volume Node): Price gaps in volume = fast travel zones (breakouts)

18 years of experience: "Volume profile doesn't lie. Price returns to VPOC.
Price breaks through LVN quickly. Price stalls at HVN."

What this module provides:
  1. Session Volume Profile (today's 5m candles → intraday VPOC/VAH/VAL)
  2. Multi-day Volume Profile (5-20 day composite → key institutional levels)
  3. Level-based trade bias (is price above/below VPOC? Near VAH/VAL?)
  4. HVN/LVN detection for entry quality assessment
  5. Volume-weighted price targets

Usage:
  from volume_profile import VolumeProfileAnalyzer
  vp  = VolumeProfileAnalyzer()
  res = vp.analyze(candles_df)  # pass 5m candle DataFrame
  print(res.vpoc, res.vah, res.val)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

# Volume area covers 70% of total volume (standard profile definition)
VALUE_AREA_VOLUME_PCT = 0.70
DEFAULT_NUM_BINS      = 100   # Price levels to slice into


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class VolumeNode:
    """A single price level in the volume profile."""
    price:       float
    volume:      int
    volume_pct:  float    # % of total session volume
    node_type:   str      # "HVN", "LVN", "NEUTRAL"

    @property
    def is_hvn(self) -> bool:
        return self.node_type == "HVN"

    @property
    def is_lvn(self) -> bool:
        return self.node_type == "LVN"


@dataclass
class VolumeProfileResult:
    """Complete volume profile analysis for a session or date range."""
    symbol:         str
    session_date:   str
    vpoc:           float          # Highest volume price level
    vah:            float          # Value Area High (70% volume top boundary)
    val:            float          # Value Area Low (70% volume bottom boundary)
    value_area_pct: float          # Actual % of volume within VAH-VAL (should be ~70%)
    session_high:   float
    session_low:    float
    hvn_levels:     List[float]    # High Volume Nodes (strong S/R)
    lvn_levels:     List[float]    # Low Volume Nodes (fast travel zones)
    total_volume:   int
    profile:        List[VolumeNode]  # Full price-volume distribution
    num_candles:    int
    timestamp:      str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_ist_timestamp()

    @property
    def value_area_width(self) -> float:
        return self.vah - self.val

    @property
    def value_area_width_pct(self) -> float:
        return (self.value_area_width / self.vpoc * 100) if self.vpoc > 0 else 0

    def price_location(self, price: float) -> str:
        """Describe where `price` sits relative to the value area."""
        if price > self.vah:
            return "ABOVE_VALUE_AREA"
        if price < self.val:
            return "BELOW_VALUE_AREA"
        if abs(price - self.vpoc) / self.vpoc < 0.002:
            return "AT_VPOC"
        if abs(price - self.vah) / self.vah < 0.003:
            return "NEAR_VAH"
        if abs(price - self.val) / self.val < 0.003:
            return "NEAR_VAL"
        return "INSIDE_VALUE_AREA"

    def get_bias(self, current_price: float) -> str:
        """
        Simple bias based on price vs VPOC:
        Above VPOC → bullish (buying side controls)
        Below VPOC → bearish (selling side controls)
        At VPOC → neutral/balanced
        """
        if current_price > self.vpoc * 1.003:
            return "BULLISH"
        if current_price < self.vpoc * 0.997:
            return "BEARISH"
        return "NEUTRAL"

    def nearest_hvn(self, price: float, max_dist_pct: float = 1.0) -> Optional[float]:
        """Find the nearest High Volume Node within max_dist_pct % of price."""
        best, best_dist = None, float("inf")
        for lvl in self.hvn_levels:
            dist = abs(price - lvl) / price * 100
            if dist <= max_dist_pct and dist < best_dist:
                best, best_dist = lvl, dist
        return best

    def nearest_lvn(self, price: float, max_dist_pct: float = 1.5) -> Optional[float]:
        """Find the nearest Low Volume Node (expect fast move through)."""
        best, best_dist = None, float("inf")
        for lvl in self.lvn_levels:
            dist = abs(price - lvl) / price * 100
            if dist <= max_dist_pct and dist < best_dist:
                best, best_dist = lvl, dist
        return best

    def summary(self) -> str:
        return (
            f"VolumeProfile {self.symbol} ({self.session_date}) | "
            f"VPOC=${self.vpoc:.2f} | VAH=${self.vah:.2f} | VAL=${self.val:.2f} | "
            f"VA Width={self.value_area_width_pct:.1f}% | "
            f"HVNs={len(self.hvn_levels)} LVNs={len(self.lvn_levels)}"
        )


# ============================================================
# MAIN ANALYZER
# ============================================================

class VolumeProfileAnalyzer:
    """
    Volume Profile Analysis Engine.

    Converts raw OHLCV candle data into institutional price-volume maps.
    Used for identifying key support/resistance and trade quality assessment.
    """

    def __init__(self, num_bins: int = DEFAULT_NUM_BINS):
        self.num_bins = num_bins
        self._cache: Dict[str, VolumeProfileResult] = {}

    # ──────────────────────────────────────────────────────
    # MAIN ANALYSIS
    # ──────────────────────────────────────────────────────

    def analyze(
        self,
        candles: pd.DataFrame,
        symbol: str = "UNKNOWN",
        session_date: Optional[str] = None,
    ) -> Optional[VolumeProfileResult]:
        """
        Build complete volume profile from OHLCV candle DataFrame.

        Args:
            candles: DataFrame with columns [open, high, low, close, volume]
                     indexed by datetime (IST-aware)
            symbol:  Stock/index symbol for labelling
            session_date: Date string (defaults to today IST)

        Returns:
            VolumeProfileResult with VPOC, VAH, VAL, HVN/LVN levels
        """
        if candles is None or candles.empty:
            return None

        session_date = session_date or str(get_current_ist_time().date())
        cache_key    = f"{symbol}_{session_date}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            result = self._build_profile(candles, symbol, session_date)
            if result:
                self._cache[cache_key] = result
                logger.debug(f"[{format_ist_timestamp()}] {result.summary()}")
            return result
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] VP analysis error for {symbol}: {e}")
            return None

    def _build_profile(
        self, df: pd.DataFrame, symbol: str, session_date: str
    ) -> Optional[VolumeProfileResult]:
        """Core volume profile computation."""
        # Normalise column names
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        required = {"high", "low", "close", "volume"}
        if not required.issubset(set(df.columns)):
            logger.warning(f"Volume profile: missing columns. Have: {df.columns.tolist()}")
            return None

        df = df.dropna(subset=["high", "low", "close", "volume"])
        if len(df) < 5:
            return None

        # Build price range
        session_high = float(df["high"].max())
        session_low  = float(df["low"].min())
        price_range  = session_high - session_low

        if price_range < 0.01:
            return None

        # Create price bins
        price_levels = np.linspace(session_low, session_high, self.num_bins + 1)
        bin_volume   = np.zeros(self.num_bins)

        # Distribute each candle's volume across its high-low range
        # (Typical Price method: each candle votes proportionally)
        for _, row in df.iterrows():
            try:
                h = float(row["high"])
                l = float(row["low"])
                v = float(row["volume"])
                if h <= l or v == 0:
                    continue
                # Find bins covered by this candle's range
                lo_bin = max(0, int((l - session_low) / price_range * self.num_bins))
                hi_bin = min(self.num_bins - 1, int((h - session_low) / price_range * self.num_bins))
                n_bins_covered = max(1, hi_bin - lo_bin + 1)
                vol_per_bin    = v / n_bins_covered
                for b in range(lo_bin, hi_bin + 1):
                    bin_volume[b] += vol_per_bin
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
                continue

        total_volume = float(bin_volume.sum())
        if total_volume == 0:
            return None

        # VPOC: bin with max volume
        vpoc_idx  = int(np.argmax(bin_volume))
        vpoc_price = float((price_levels[vpoc_idx] + price_levels[vpoc_idx + 1]) / 2)

        # Value Area: expand from VPOC until 70% of volume is captured
        vah_idx, val_idx = self._calc_value_area(bin_volume, vpoc_idx, total_volume)
        vah_price = float((price_levels[vah_idx] + price_levels[min(vah_idx + 1, self.num_bins)]) / 2)
        val_price = float((price_levels[val_idx] + price_levels[val_idx + 1]) / 2)
        va_volume_pct = float(bin_volume[val_idx:vah_idx + 1].sum() / total_volume * 100)

        # Build VolumeNode list with HVN/LVN classification
        mean_vol = float(np.mean(bin_volume[bin_volume > 0]))
        std_vol  = float(np.std(bin_volume[bin_volume > 0]))
        hvn_threshold = mean_vol + std_vol * 0.5   # HVN: above 0.5 std
        lvn_threshold = max(0, mean_vol - std_vol * 1.0)  # LVN: below 1 std

        nodes   = []
        hvn_lvl = []
        lvn_lvl = []
        for i in range(self.num_bins):
            p    = float((price_levels[i] + price_levels[i + 1]) / 2)
            v    = int(bin_volume[i])
            pct  = v / total_volume * 100
            if bin_volume[i] >= hvn_threshold:
                ntype = "HVN"
                hvn_lvl.append(p)
            elif bin_volume[i] <= lvn_threshold and bin_volume[i] > 0:
                ntype = "LVN"
                lvn_lvl.append(p)
            else:
                ntype = "NEUTRAL"
            nodes.append(VolumeNode(price=p, volume=v, volume_pct=pct, node_type=ntype))

        # Consolidate nearby HVN/LVN levels (merge within 0.3% of each other)
        hvn_lvl = self._consolidate_levels(hvn_lvl, tolerance_pct=0.3)
        lvn_lvl = self._consolidate_levels(lvn_lvl, tolerance_pct=0.3)

        return VolumeProfileResult(
            symbol=symbol,
            session_date=session_date,
            vpoc=round(vpoc_price, 2),
            vah=round(vah_price, 2),
            val=round(val_price, 2),
            value_area_pct=round(va_volume_pct, 1),
            session_high=round(session_high, 2),
            session_low=round(session_low, 2),
            hvn_levels=[round(l, 2) for l in hvn_lvl],
            lvn_levels=[round(l, 2) for l in lvn_lvl],
            total_volume=int(total_volume),
            profile=nodes,
            num_candles=len(df),
        )

    def _calc_value_area(
        self, bin_volume: np.ndarray, vpoc_idx: int, total_volume: float
    ) -> Tuple[int, int]:
        """
        Expand from VPOC outward (adding highest adjacent bin each step)
        until 70% of total volume is captured.
        Returns (vah_idx, val_idx).
        """
        target     = total_volume * VALUE_AREA_VOLUME_PCT
        accumulated = bin_volume[vpoc_idx]
        hi_idx     = vpoc_idx
        lo_idx     = vpoc_idx
        n          = len(bin_volume)

        while accumulated < target:
            can_up   = hi_idx + 1 < n
            can_down = lo_idx - 1 >= 0

            if not can_up and not can_down:
                break

            add_up   = bin_volume[hi_idx + 1] if can_up   else -1
            add_down = bin_volume[lo_idx - 1] if can_down else -1

            if add_up >= add_down:
                hi_idx    += 1
                accumulated += bin_volume[hi_idx]
            else:
                lo_idx    -= 1
                accumulated += bin_volume[lo_idx]

        return hi_idx, lo_idx

    def _consolidate_levels(self, levels: List[float], tolerance_pct: float = 0.3) -> List[float]:
        """Merge nearby levels within tolerance_pct% of each other."""
        if not levels:
            return []
        levels = sorted(levels)
        merged = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl - merged[-1]) / merged[-1] * 100 <= tolerance_pct:
                merged[-1] = (merged[-1] + lvl) / 2  # Average
            else:
                merged.append(lvl)
        return merged

    # ──────────────────────────────────────────────────────
    # SIGNAL HELPERS
    # ──────────────────────────────────────────────────────

    def get_signal_context(
        self, result: VolumeProfileResult, current_price: float, direction: str
    ) -> Dict:
        """
        Evaluate how the volume profile supports or opposes a trade signal.

        Returns dict with:
          - location: where price is relative to value area
          - bias: BULLISH/BEARISH/NEUTRAL
          - score_adjustment: -15 to +15 for signal_generator
          - notes: list of observations
        """
        if not result:
            return {"score_adjustment": 0, "notes": []}

        location     = result.price_location(current_price)
        vp_bias      = result.get_bias(current_price)
        adjustment   = 0.0
        notes        = []
        nearby_hvn   = result.nearest_hvn(current_price, max_dist_pct=0.5)
        nearby_lvn   = result.nearest_lvn(current_price, max_dist_pct=1.0)

        if direction == "LONG":
            if location == "ABOVE_VALUE_AREA":
                adjustment += 8
                notes.append(f"Price ABOVE value area ({current_price:.2f}>{result.vah:.2f}) — LONG in acceptance zone")
            elif location == "AT_VPOC":
                adjustment += 5
                notes.append(f"Price at VPOC ${result.vpoc:.2f} — balanced, slight LONG edge")
            elif location == "NEAR_VAL":
                adjustment += 10
                notes.append(f"LONG from VAL ${result.val:.2f} — value area support, high R:R")
            elif location == "BELOW_VALUE_AREA":
                adjustment -= 10
                notes.append(f"Price BELOW value area ${result.val:.2f} — risk of continuation down, avoid LONG")
            elif location == "NEAR_VAH":
                adjustment -= 5
                notes.append(f"LONG near VAH ${result.vah:.2f} — resistance overhead, tight stop needed")

            if nearby_hvn:
                dist = abs(current_price - nearby_hvn) / current_price * 100
                if nearby_hvn > current_price:
                    adjustment += 3
                    notes.append(f"HVN above at ${nearby_hvn:.2f} ({dist:.1f}% away) — possible target")
                else:
                    adjustment += 5
                    notes.append(f"HVN below at ${nearby_hvn:.2f} — strong support under trade")

            if nearby_lvn and nearby_lvn > current_price:
                notes.append(f"LVN at ${nearby_lvn:.2f} — price may travel fast through this zone")
                adjustment += 4

        else:  # SHORT
            if location == "BELOW_VALUE_AREA":
                adjustment += 8
                notes.append(f"Price BELOW value area ${result.val:.2f} — SHORT in distribution")
            elif location == "AT_VPOC":
                adjustment += 5
                notes.append(f"Price at VPOC ${result.vpoc:.2f} — SHORT balanced entry")
            elif location == "NEAR_VAH":
                adjustment += 10
                notes.append(f"SHORT from VAH ${result.vah:.2f} — value area resistance, high R:R")
            elif location == "ABOVE_VALUE_AREA":
                adjustment -= 10
                notes.append(f"Price ABOVE value area ${result.vah:.2f} — breakout risk, avoid SHORT")
            elif location == "NEAR_VAL":
                adjustment -= 5
                notes.append(f"SHORT near VAL ${result.val:.2f} — support below, tight stop needed")

            if nearby_hvn and nearby_hvn < current_price:
                adjustment += 5
                notes.append(f"HVN below at ${nearby_hvn:.2f} — strong overhead supply for SHORT")

        # VPOC magnet effect
        dist_to_vpoc = abs(current_price - result.vpoc) / result.vpoc * 100
        if dist_to_vpoc > 1.5:
            notes.append(f"VPOC magnet at ${result.vpoc:.2f} ({dist_to_vpoc:.1f}% away) — expect reversion")

        return {
            "location":        location,
            "bias":            vp_bias,
            "score_adjustment": round(min(max(adjustment, -15), 15), 1),
            "vpoc":            result.vpoc,
            "vah":             result.vah,
            "val":             result.val,
            "notes":           notes,
        }

    # ──────────────────────────────────────────────────────
    # MULTI-DAY COMPOSITE
    # ──────────────────────────────────────────────────────

    def analyze_composite(
        self,
        candles_list: List[pd.DataFrame],
        symbol: str = "UNKNOWN",
        label: str = "5D",
    ) -> Optional[VolumeProfileResult]:
        """
        Build a composite volume profile across multiple sessions.
        Concatenates all candle data and runs profile on the merged set.
        Useful for identifying institutional S/R across multiple days.
        """
        if not candles_list:
            return None
        try:
            combined = pd.concat(
                [df for df in candles_list if df is not None and not df.empty],
                axis=0,
            ).sort_index()
            return self.analyze(combined, symbol=symbol, session_date=label)
        except Exception as e:
            logger.error(f"VP composite error: {e}")
            return None

    # ──────────────────────────────────────────────────────
    # TELEGRAM FORMAT
    # ──────────────────────────────────────────────────────

    def format_telegram(
        self, result: VolumeProfileResult, current_price: float
    ) -> str:
        """Format volume profile for Telegram signal alert."""
        if not result:
            return "Volume Profile: N/A"
        location = result.price_location(current_price)
        bias     = result.get_bias(current_price)
        bias_emj = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias, "🟡")
        return (
            f"📊 *Volume Profile*\n"
            f"VPOC: `${result.vpoc:,.2f}` | VAH: `${result.vah:,.2f}` | VAL: `${result.val:,.2f}`\n"
            f"Width: `{result.value_area_width_pct:.1f}%` | HVNs: `{len(result.hvn_levels)}` | LVNs: `{len(result.lvn_levels)}`\n"
            f"Price Location: `{location}` | {bias_emj} Bias: *{bias}*"
        )


# ──────────────────────────────────────────────────────────────
# SINGLETON
# ──────────────────────────────────────────────────────────────

_vp_analyzer: Optional[VolumeProfileAnalyzer] = None

def get_vp_analyzer() -> VolumeProfileAnalyzer:
    global _vp_analyzer
    if _vp_analyzer is None:
        _vp_analyzer = VolumeProfileAnalyzer()
    return _vp_analyzer


if __name__ == "__main__":
    import pandas as pd, numpy as np, logging
    logging.basicConfig(level=logging.INFO)

    # Generate synthetic 5-min candle data for testing
    np.random.seed(42)
    n = 75  # 6.25 hours of 5m candles
    base = 22000.0
    prices = base + np.cumsum(np.random.randn(n) * 50)
    df = pd.DataFrame({
        "open":   prices,
        "high":   prices + abs(np.random.randn(n) * 30),
        "low":    prices - abs(np.random.randn(n) * 30),
        "close":  prices + np.random.randn(n) * 10,
        "volume": np.random.randint(100000, 500000, n),
    }, index=pd.date_range("2026-04-09 09:15", periods=n, freq="5min"))

    vp = VolumeProfileAnalyzer()
    result = vp.analyze(df, symbol="NIFTY_TEST")
    if result:
        print(result.summary())
        print(f"\nHVN levels: {result.hvn_levels[:5]}")
        print(f"LVN levels: {result.lvn_levels[:5]}")
        current = float(df["close"].iloc[-1])
        ctx = vp.get_signal_context(result, current, "LONG")
        print(f"\nSignal context for LONG @ ${current:.2f}:")
        print(f"  Location: {ctx['location']}")
        print(f"  Score adjustment: {ctx['score_adjustment']:+.1f}")
        for note in ctx["notes"]:
            print(f"  → {note}")
