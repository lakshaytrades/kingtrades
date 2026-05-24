"""
harmonic_patterns.py — Harmonic Price Pattern Detector

Harmonic patterns use precise Fibonacci ratios between swing points (X, A, B, C, D)
to identify high-probability reversal zones (Potential Reversal Zone = PRZ).

Big players actively trade these because:
  1. The entry is at a specific price (not "roughly here")
  2. The stop is small (just beyond PRZ)
  3. The R:R is 2:1 to 5:1 built in
  4. Works on every timeframe, every market

Patterns implemented:
  ABCD      — simplest harmonic (BC = 61.8-78.6% of AB, CD = 127.2-161.8% of BC)
  Gartley   — first harmonic (1935), B=0.618 of XA
  Butterfly  — extreme extension (D beyond X), B=0.786 of XA
  Bat        — deep retracement (B=0.382-0.5 of XA), tight PRZ
  Crab       — widest extension (D=1.618 of XA), highest R:R
  Shark      — 5-0 pattern complement
  Cypher     — alternate structure (C=1.13-1.414 of XA)
  Three Drives — 3 equal measured moves (fibonacci extension based)
  OTE        — Optimal Trade Entry (ICT): buy/sell into 62-79% retracement

All patterns return:
  PatternResult with direction, confidence, PRZ (entry zone), SL, TP

Tolerance: ±3% on each Fibonacci ratio (real-world price isn't perfect)
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TOL = 0.03   # ±3% tolerance on Fibonacci ratios


def fib_ok(ratio: float, target: float, tol: float = TOL) -> bool:
    """Check if ratio is within tolerance of Fibonacci target."""
    return abs(ratio - target) <= tol


def swing_highs_lows(df: pd.DataFrame, window: int = 5) -> Tuple[List, List]:
    """
    Find local swing highs and lows using a rolling window.
    Returns (highs, lows) as lists of (index, price) tuples.
    """
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    n = len(h)
    for i in range(window, n - window):
        if h[i] == max(h[i-window:i+window+1]):
            highs.append((i, h[i]))
        if l[i] == min(l[i-window:i+window+1]):
            lows.append((i, l[i]))
    return highs, lows


def find_xabcd_points(df: pd.DataFrame, n_swings: int = 6) -> List[List[Tuple]]:
    """
    Build candidate XABCD point sets from recent swing data.
    Returns list of [X, A, B, C, D] price-tuples (index, price).
    """
    highs, lows = swing_highs_lows(df, window=3)
    # Merge and sort all swings by index
    all_swings = sorted(highs + lows, key=lambda x: x[0])
    # Deduplicate close swings
    merged = [all_swings[0]] if all_swings else []
    for sw in all_swings[1:]:
        if sw[0] - merged[-1][0] > 2:
            merged.append(sw)

    if len(merged) < 5:
        return []

    # Take last n swings and build alternating candidates
    recent = merged[-n_swings:]
    candidates = []
    for i in range(len(recent) - 4):
        group = recent[i:i+5]
        # Ensure alternating (each consecutive pair alternates direction)
        prices = [g[1] for g in group]
        alternating = all(
            (prices[j] > prices[j-1]) != (prices[j+1] > prices[j])
            for j in range(1, 4)
        )
        if alternating:
            candidates.append(group)
    return candidates


@dataclass
class HarmonicResult:
    """Result of a harmonic pattern detection."""
    name:       str
    direction:  str       # "LONG" or "SHORT"
    confidence: float     # 0-100
    prz_low:    float     # entry zone bottom
    prz_high:   float     # entry zone top
    stop_loss:  float
    target1:    float
    target2:    float
    ratios:     Dict[str, float] = field(default_factory=dict)
    description: str = ""

    def to_pattern_result(self):
        """Convert to PatternResult for compatibility with pattern_recognition."""
        from pattern_recognition import PatternResult
        desc = (
            f"{self.name} PRZ={self.prz_low:.2f}-{self.prz_high:.2f} "
            f"SL={self.stop_loss:.2f} TP1={self.target1:.2f}"
        )
        return PatternResult(
            name       = self.name,
            direction  = self.direction,
            confidence = self.confidence,
            description= desc,
        )


class HarmonicPatternDetector:
    """
    Detects all major harmonic price patterns from OHLCV data.
    Uses recent swing highs/lows with ±3% Fibonacci tolerance.
    """

    # ─────────────────────────────────────────────────────────
    # ABCD PATTERN (simplest, highest frequency)
    # ─────────────────────────────────────────────────────────

    def detect_abcd(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        ABCD: Two equal measured moves.
        Bullish: A high → B low → C high → D low  (D = entry for long)
        Bearish: A low  → B high → C low  → D high (D = entry for short)

        Fibonacci rules:
          BC = 61.8–78.6% of AB
          CD = 127.2–161.8% of BC  (or equal to AB = classical)
        """
        candidates = find_xabcd_points(df, n_swings=5)
        best: Optional[HarmonicResult] = None
        best_score = 0.0

        for pts in candidates:
            # ABCD uses only last 4 swings
            a_i, a_p = pts[1]
            b_i, b_p = pts[2]
            c_i, c_p = pts[3]
            d_i, d_p = pts[4]

            ab = abs(b_p - a_p)
            bc = abs(c_p - b_p)
            cd = abs(d_p - c_p)

            if ab < 0.001 or bc < 0.001:
                continue

            bc_ab = bc / ab
            cd_bc = cd / bc

            # Bullish ABCD: A(high) B(low) C(high) D(low)
            if a_p > b_p and c_p > b_p and d_p < c_p:
                if (fib_ok(bc_ab, 0.618) or fib_ok(bc_ab, 0.786)) and \
                   (fib_ok(cd_bc, 1.272) or fib_ok(cd_bc, 1.618) or fib_ok(cd / ab, 1.0)):
                    score = 70 - abs(bc_ab - 0.618) * 100 - abs(cd_bc - 1.272) * 50
                    if score > best_score:
                        best_score = score
                        prz = d_p
                        sl  = d_p - ab * 0.15
                        t1  = d_p + ab * 0.618
                        t2  = d_p + ab
                        best = HarmonicResult(
                            "ABCD Bullish", "LONG",
                            min(max(score, 58), 82),
                            prz * 0.998, prz * 1.002, sl, t1, t2,
                            {"BC/AB": round(bc_ab, 3), "CD/BC": round(cd_bc, 3)},
                        )

            # Bearish ABCD: A(low) B(high) C(low) D(high)
            if a_p < b_p and c_p < b_p and d_p > c_p:
                if (fib_ok(bc_ab, 0.618) or fib_ok(bc_ab, 0.786)) and \
                   (fib_ok(cd_bc, 1.272) or fib_ok(cd_bc, 1.618)):
                    score = 70 - abs(bc_ab - 0.618) * 100 - abs(cd_bc - 1.272) * 50
                    if score > best_score:
                        best_score = score
                        prz = d_p
                        sl  = d_p + ab * 0.15
                        t1  = d_p - ab * 0.618
                        t2  = d_p - ab
                        best = HarmonicResult(
                            "ABCD Bearish", "SHORT",
                            min(max(score, 58), 82),
                            prz * 0.998, prz * 1.002, sl, t1, t2,
                            {"BC/AB": round(bc_ab, 3), "CD/BC": round(cd_bc, 3)},
                        )
        return best

    # ─────────────────────────────────────────────────────────
    # GARTLEY (1935) — The original harmonic
    # ─────────────────────────────────────────────────────────

    def detect_gartley(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        Bullish Gartley: XA down, AB up 61.8%, BC down 38.2-88.6%,
        CD up to 78.6% of XA.
        The most common harmonic — occurs at significant retracements.
        """
        candidates = find_xabcd_points(df)
        best: Optional[HarmonicResult] = None
        best_conf = 0.0

        for pts in candidates:
            x_i, x_p = pts[0]
            a_i, a_p = pts[1]
            b_i, b_p = pts[2]
            c_i, c_p = pts[3]
            d_i, d_p = pts[4]

            xa = abs(a_p - x_p)
            ab = abs(b_p - a_p)
            bc = abs(c_p - b_p)
            cd = abs(d_p - c_p)
            if xa < 0.001:
                continue

            xb = abs(b_p - x_p)
            xd = abs(d_p - x_p)
            ab_xa = ab / xa
            xb_xa = xb / xa
            xd_xa = xd / xa
            bc_ab = bc / ab if ab > 0 else 0
            cd_bc = cd / bc if bc > 0 else 0

            # Bullish Gartley: X(high) A(low) B(high) C(low) D(low)
            if x_p > a_p and b_p > a_p and c_p < b_p and d_p < b_p:
                b_ok  = fib_ok(xb_xa, 0.618, 0.04)
                bc_ok = 0.382 - TOL <= bc_ab <= 0.886 + TOL
                xd_ok = fib_ok(xd_xa, 0.786, 0.04)
                cd_ok = fib_ok(cd_bc, 1.272, 0.06) or fib_ok(cd_bc, 1.618, 0.06)

                score = sum([b_ok * 30, bc_ok * 20, xd_ok * 35, cd_ok * 15])
                if score >= 60 and score > best_conf:
                    best_conf = score
                    conf = min(60 + score * 0.3, 88)
                    prz  = d_p
                    sl   = d_p - xa * 0.05   # just below D (entry point)
                    t1   = d_p + xa * 0.382
                    t2   = d_p + xa * 0.618
                    best = HarmonicResult(
                        "Gartley Bullish (222)", "LONG", conf,
                        prz * 0.997, prz * 1.003, sl, t1, t2,
                        {"XB/XA": round(xb_xa,3), "XD/XA": round(xd_xa,3)},
                        "Classic Gartley 222 — buy at 78.6% XA retracement"
                    )

            # Bearish Gartley
            if x_p < a_p and b_p < a_p and c_p > b_p and d_p > b_p:
                b_ok  = fib_ok(xb_xa, 0.618, 0.04)
                bc_ok = 0.382 - TOL <= bc_ab <= 0.886 + TOL
                xd_ok = fib_ok(xd_xa, 0.786, 0.04)
                cd_ok = fib_ok(cd_bc, 1.272, 0.06) or fib_ok(cd_bc, 1.618, 0.06)

                score = sum([b_ok * 30, bc_ok * 20, xd_ok * 35, cd_ok * 15])
                if score >= 60 and score > best_conf:
                    best_conf = score
                    conf = min(60 + score * 0.3, 88)
                    prz  = d_p
                    sl   = d_p + xa * 0.05   # just above D (entry point)
                    t1   = d_p - xa * 0.382
                    t2   = d_p - xa * 0.618
                    best = HarmonicResult(
                        "Gartley Bearish (222)", "SHORT", conf,
                        prz * 0.997, prz * 1.003, sl, t1, t2,
                        {"XB/XA": round(xb_xa,3), "XD/XA": round(xd_xa,3)},
                    )
        return best

    # ─────────────────────────────────────────────────────────
    # BUTTERFLY — Deep extension beyond X
    # ─────────────────────────────────────────────────────────

    def detect_butterfly(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        Butterfly: B at 78.6% of XA. D extends BEYOND X at 127.2-161.8%.
        The deepest harmonic — D makes new extreme, then reverses hard.
        Big players use this to absorb stops hunted below/above X.
        """
        candidates = find_xabcd_points(df)
        best: Optional[HarmonicResult] = None
        best_conf = 0.0

        for pts in candidates:
            x_i, x_p = pts[0]
            a_i, a_p = pts[1]
            b_i, b_p = pts[2]
            c_i, c_p = pts[3]
            d_i, d_p = pts[4]

            xa = abs(a_p - x_p)
            ab = abs(b_p - a_p)
            bc = abs(c_p - b_p)
            cd = abs(d_p - c_p)
            if xa < 0.001:
                continue

            xb_xa = ab / xa if xa else 0
            xd_xa = abs(d_p - x_p) / xa
            bc_ab = bc / ab if ab else 0
            cd_bc = cd / bc if bc else 0

            # Bullish Butterfly: D below X (new low beyond X)
            if x_p > a_p and b_p > a_p and d_p < x_p:
                b_ok  = fib_ok(xb_xa, 0.786, 0.04)
                bc_ok = 0.382 - TOL <= bc_ab <= 0.886 + TOL
                xd_ok = fib_ok(xd_xa, 1.272, 0.06) or fib_ok(xd_xa, 1.618, 0.06)
                cd_ok = fib_ok(cd_bc, 1.618, 0.08) or fib_ok(cd_bc, 2.618, 0.1)

                score = sum([b_ok * 30, bc_ok * 20, xd_ok * 35, cd_ok * 15])
                if score >= 55 and score > best_conf:
                    best_conf = score
                    conf = min(62 + score * 0.28, 90)
                    prz  = d_p
                    sl   = d_p - xa * 0.08
                    t1   = d_p + xa * 0.382
                    t2   = d_p + xa * 0.786
                    best = HarmonicResult(
                        "Butterfly Bullish", "LONG", conf,
                        prz * 0.996, prz * 1.004, sl, t1, t2,
                        {"XB/XA": round(xb_xa,3), "XD/XA": round(xd_xa,3)},
                        "Butterfly: D beyond X, extreme reversal zone"
                    )

            # Bearish Butterfly: D above X (new high beyond X)
            if x_p < a_p and b_p < a_p and d_p > x_p:
                b_ok  = fib_ok(xb_xa, 0.786, 0.04)
                bc_ok = 0.382 - TOL <= bc_ab <= 0.886 + TOL
                xd_ok = fib_ok(xd_xa, 1.272, 0.06) or fib_ok(xd_xa, 1.618, 0.06)
                cd_ok = fib_ok(cd_bc, 1.618, 0.08) or fib_ok(cd_bc, 2.618, 0.1)

                score = sum([b_ok * 30, bc_ok * 20, xd_ok * 35, cd_ok * 15])
                if score >= 55 and score > best_conf:
                    best_conf = score
                    conf = min(62 + score * 0.28, 90)
                    prz  = d_p
                    sl   = d_p + xa * 0.08
                    t1   = d_p - xa * 0.382
                    t2   = d_p - xa * 0.786
                    best = HarmonicResult(
                        "Butterfly Bearish", "SHORT", conf,
                        prz * 0.996, prz * 1.004, sl, t1, t2,
                        {"XB/XA": round(xb_xa,3), "XD/XA": round(xd_xa,3)},
                    )
        return best

    # ─────────────────────────────────────────────────────────
    # BAT — Tightest PRZ, best R:R
    # ─────────────────────────────────────────────────────────

    def detect_bat(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        Bat: B at 38.2-50% of XA (shallow), D at 88.6% of XA.
        The tightest harmonic PRZ → smallest stop → best R:R.
        Favourite of institutional traders for its precision.
        """
        candidates = find_xabcd_points(df)
        best: Optional[HarmonicResult] = None
        best_conf = 0.0

        for pts in candidates:
            x_i, x_p = pts[0]
            a_i, a_p = pts[1]
            b_i, b_p = pts[2]
            c_i, c_p = pts[3]
            d_i, d_p = pts[4]

            xa = abs(a_p - x_p)
            ab = abs(b_p - a_p)
            bc = abs(c_p - b_p)
            cd = abs(d_p - c_p)
            if xa < 0.001:
                continue

            xb_xa = ab / xa
            xd_xa = abs(d_p - x_p) / xa
            bc_ab = bc / ab if ab else 0
            cd_bc = cd / bc if bc else 0

            # Bullish Bat
            if x_p > a_p and b_p > a_p and d_p < b_p:
                b_ok  = (0.382 - TOL <= xb_xa <= 0.500 + TOL)
                bc_ok = (0.382 - TOL <= bc_ab <= 0.886 + TOL)
                xd_ok = fib_ok(xd_xa, 0.886, 0.04)
                cd_ok = fib_ok(cd_bc, 1.618, 0.08) or fib_ok(cd_bc, 2.618, 0.1)

                score = sum([b_ok * 30, bc_ok * 20, xd_ok * 40, cd_ok * 10])
                if score >= 60 and score > best_conf:
                    best_conf = score
                    conf = min(65 + score * 0.28, 91)
                    prz  = d_p
                    sl   = d_p - xa * 0.05   # just below D (entry point)
                    t1   = d_p + xa * 0.382
                    t2   = d_p + xa * 0.618
                    best = HarmonicResult(
                        "Bat Bullish", "LONG", conf,
                        prz * 0.997, prz * 1.003, sl, t1, t2,
                        {"XB/XA": round(xb_xa,3), "XD/XA": round(xd_xa,3)},
                        "Bat: tightest PRZ, best R:R harmonic"
                    )

            # Bearish Bat
            if x_p < a_p and b_p < a_p and d_p > b_p:
                b_ok  = (0.382 - TOL <= xb_xa <= 0.500 + TOL)
                bc_ok = (0.382 - TOL <= bc_ab <= 0.886 + TOL)
                xd_ok = fib_ok(xd_xa, 0.886, 0.04)
                cd_ok = fib_ok(cd_bc, 1.618, 0.08) or fib_ok(cd_bc, 2.618, 0.1)

                score = sum([b_ok * 30, bc_ok * 20, xd_ok * 40, cd_ok * 10])
                if score >= 60 and score > best_conf:
                    best_conf = score
                    conf = min(65 + score * 0.28, 91)
                    prz  = d_p
                    sl   = d_p + xa * 0.05   # just above D (entry point)
                    t1   = d_p - xa * 0.382
                    t2   = d_p - xa * 0.618
                    best = HarmonicResult(
                        "Bat Bearish", "SHORT", conf,
                        prz * 0.997, prz * 1.003, sl, t1, t2,
                        {"XB/XA": round(xb_xa,3), "XD/XA": round(xd_xa,3)},
                    )
        return best

    # ─────────────────────────────────────────────────────────
    # CRAB — Most extreme extension, highest R:R
    # ─────────────────────────────────────────────────────────

    def detect_crab(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        Crab: B at 38.2-61.8% of XA, D extends to 161.8% of XA.
        The most extreme harmonic — D makes biggest extension.
        Highest R:R but least frequent. Favourite of Scott Carney.
        """
        candidates = find_xabcd_points(df)
        best: Optional[HarmonicResult] = None
        best_conf = 0.0

        for pts in candidates:
            x_i, x_p = pts[0]
            a_i, a_p = pts[1]
            b_i, b_p = pts[2]
            c_i, c_p = pts[3]
            d_i, d_p = pts[4]

            xa = abs(a_p - x_p)
            ab = abs(b_p - a_p)
            bc = abs(c_p - b_p)
            cd = abs(d_p - c_p)
            if xa < 0.001:
                continue

            xb_xa = ab / xa
            xd_xa = abs(d_p - x_p) / xa
            bc_ab = bc / ab if ab else 0
            cd_bc = cd / bc if bc else 0

            # Bullish Crab
            if x_p > a_p and b_p > a_p and d_p < a_p:
                b_ok  = (0.382 - TOL <= xb_xa <= 0.618 + TOL)
                bc_ok = (0.382 - TOL <= bc_ab <= 0.886 + TOL)
                xd_ok = fib_ok(xd_xa, 1.618, 0.06)
                cd_ok = fib_ok(cd_bc, 2.618, 0.12) or fib_ok(cd_bc, 3.618, 0.15)

                score = sum([b_ok * 25, bc_ok * 20, xd_ok * 40, cd_ok * 15])
                if score >= 55 and score > best_conf:
                    best_conf = score
                    conf = min(65 + score * 0.25, 90)
                    prz  = d_p
                    sl   = d_p - xa * 0.06
                    t1   = d_p + xa * 0.382
                    t2   = d_p + xa
                    best = HarmonicResult(
                        "Crab Bullish", "LONG", conf,
                        prz * 0.995, prz * 1.005, sl, t1, t2,
                        {"XB/XA": round(xb_xa,3), "XD/XA": round(xd_xa,3)},
                        "Deep Crab: 161.8% extension — extreme reversal zone, highest R:R"
                    )

            # Bearish Crab
            if x_p < a_p and b_p < a_p and d_p > a_p:
                b_ok  = (0.382 - TOL <= xb_xa <= 0.618 + TOL)
                bc_ok = (0.382 - TOL <= bc_ab <= 0.886 + TOL)
                xd_ok = fib_ok(xd_xa, 1.618, 0.06)
                cd_ok = fib_ok(cd_bc, 2.618, 0.12) or fib_ok(cd_bc, 3.618, 0.15)

                score = sum([b_ok * 25, bc_ok * 20, xd_ok * 40, cd_ok * 15])
                if score >= 55 and score > best_conf:
                    best_conf = score
                    conf = min(65 + score * 0.25, 90)
                    prz  = d_p
                    sl   = d_p + xa * 0.06
                    t1   = d_p - xa * 0.382
                    t2   = d_p - xa
                    best = HarmonicResult(
                        "Crab Bearish", "SHORT", conf,
                        prz * 0.995, prz * 1.005, sl, t1, t2,
                        {"XB/XA": round(xb_xa,3), "XD/XA": round(xd_xa,3)},
                    )
        return best

    # ─────────────────────────────────────────────────────────
    # OTE — Optimal Trade Entry (ICT concept)
    # ─────────────────────────────────────────────────────────

    def detect_ote(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        OTE (Optimal Trade Entry) — ICT Inner Circle Trader concept.

        After an impulsive move (leg A→B), price retraces into the
        62.0–79.0% Fibonacci zone (OTE zone).
        Enter the PULLBACK in the direction of the original impulse.

        This is how smart money gets their institutional price fills
        — they let price come to them at a discount/premium.

        Rules:
          1. Identify clear impulse move (AB) — minimum 1.5× ATR
          2. Price retraces into 62–79% zone (C point)
          3. Volume contracts during retracement (institutional absorption)
          4. Enter as price shows rejection at the OTE zone
        """
        if len(df) < 20:
            return None

        closes = df["close"].values
        highs  = df["high"].values
        lows   = df["low"].values
        vols   = df["volume"].values

        # Find the most recent major impulse over last 20 bars
        recent_high = max(highs[-20:-3])
        recent_low  = min(lows[-20:-3])
        current     = closes[-1]

        if recent_high <= recent_low:
            return None

        swing_range = recent_high - recent_low

        # ATR check: impulse must be significant (>1% of price)
        if swing_range / max(recent_low, 0.01) < 0.01:
            return None

        # Determine direction from most recent 5 bars vs 15 bars ago
        trend_direction = "LONG" if closes[-5:].mean() > closes[-15:-10].mean() else "SHORT"

        # OTE zone: 62-79% retracement from impulse
        if trend_direction == "LONG":
            # Bullish impulse was up. OTE = buy the pullback at 62-79% retrace
            ote_top    = recent_high - swing_range * 0.620
            ote_bottom = recent_high - swing_range * 0.790
            in_zone    = ote_bottom <= current <= ote_top
            if not in_zone:
                return None

            # Volume contraction check (retracement should be on lower volume)
            avg_vol_impulse = vols[-15:-8].mean()
            avg_vol_retrace = vols[-8:].mean()
            vol_contracting = avg_vol_retrace < avg_vol_impulse * 0.85

            conf = 68
            if vol_contracting:
                conf += 10
            # Price near bottom of OTE zone (best entry)
            if current <= ote_bottom + (ote_top - ote_bottom) * 0.35:
                conf += 8

            prz    = (ote_top + ote_bottom) / 2
            sl     = ote_bottom - swing_range * 0.10
            t1     = recent_high
            t2     = recent_high + swing_range * 0.618

            return HarmonicResult(
                "OTE Bullish (ICT)", "LONG", min(conf, 88),
                ote_bottom, ote_top, sl, t1, t2,
                {"retrace_pct": round((recent_high - current) / swing_range * 100, 1)},
                f"Optimal Trade Entry: price in 62-79% discount zone "
                f"(vol_contracting={vol_contracting})"
            )

        else:
            # Bearish OTE: sell the bounce at 62-79% retrace of down impulse
            ote_bottom = recent_low + swing_range * 0.620
            ote_top    = recent_low + swing_range * 0.790
            in_zone    = ote_bottom <= current <= ote_top
            if not in_zone:
                return None

            avg_vol_impulse = vols[-15:-8].mean()
            avg_vol_retrace = vols[-8:].mean()
            vol_contracting = avg_vol_retrace < avg_vol_impulse * 0.85

            conf = 68
            if vol_contracting:
                conf += 10
            if current >= ote_bottom + (ote_top - ote_bottom) * 0.65:
                conf += 8

            prz = (ote_top + ote_bottom) / 2
            sl  = ote_top + swing_range * 0.10
            t1  = recent_low
            t2  = recent_low - swing_range * 0.618

            return HarmonicResult(
                "OTE Bearish (ICT)", "SHORT", min(conf, 88),
                ote_bottom, ote_top, sl, t1, t2,
                {"retrace_pct": round((current - recent_low) / swing_range * 100, 1)},
                f"Optimal Trade Entry: price in 62-79% premium zone"
            )

    # ─────────────────────────────────────────────────────────
    # THREE DRIVES — Three equal measured moves
    # ─────────────────────────────────────────────────────────

    def detect_three_drives(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        Three Drives: Three equal Fibonacci extensions.
        Bullish: Three equal falling drives (exhaustion bottom).
        Bearish: Three equal rising drives (exhaustion top).

        Fibonacci rules:
          Drive 2 = 127.2–161.8% of Drive 1
          Drive 3 = 127.2–161.8% of Drive 2
          Retracements between drives = 61.8–78.6%
        """
        if len(df) < 30:
            return None

        highs, lows = swing_highs_lows(df, window=4)
        all_swings  = sorted(highs + lows, key=lambda x: x[0])

        if len(all_swings) < 7:
            return None

        recent = all_swings[-7:]
        prices = [p for _, p in recent]

        # Bearish Three Drives (three rising peaks)
        if prices[0] < prices[2] < prices[4] and prices[4] == max(prices):
            d1 = abs(prices[2] - prices[1])
            d2 = abs(prices[4] - prices[3])
            d3_proj = prices[4] + d2 * 1.272   # projected 3rd drive extension

            r1 = abs(prices[1] - prices[0]) / d1 if d1 else 0
            r2 = abs(prices[3] - prices[2]) / d2 if d2 else 0
            d2_d1 = d2 / d1 if d1 else 0

            r1_ok = fib_ok(r1, 0.618, 0.08) or fib_ok(r1, 0.786, 0.08)
            r2_ok = fib_ok(r2, 0.618, 0.08) or fib_ok(r2, 0.786, 0.08)
            ext_ok = fib_ok(d2_d1, 1.272, 0.10) or fib_ok(d2_d1, 1.618, 0.10)

            if r1_ok and r2_ok and ext_ok:
                current = df["close"].values[-1]
                conf = 65 + r1_ok * 10 + r2_ok * 10 + ext_ok * 10
                return HarmonicResult(
                    "Three Drives Bearish", "SHORT", min(conf, 85),
                    prices[4] * 0.998, prices[4] * 1.002,
                    prices[4] + d2 * 0.15, prices[4] - d2 * 0.618, prices[4] - d2,
                    {"D2/D1": round(d2_d1, 3), "R1": round(r1, 3), "R2": round(r2, 3)},
                    "Three equal rising drives — exhaustion top"
                )

        # Bullish Three Drives (three falling troughs)
        if prices[0] > prices[2] > prices[4] and prices[4] == min(prices):
            d1 = abs(prices[0] - prices[1])
            d2 = abs(prices[2] - prices[3])

            r1 = abs(prices[2] - prices[1]) / d1 if d1 else 0
            r2 = abs(prices[4] - prices[3]) / d2 if d2 else 0
            d2_d1 = d2 / d1 if d1 else 0

            r1_ok = fib_ok(r1, 0.618, 0.08) or fib_ok(r1, 0.786, 0.08)
            r2_ok = fib_ok(r2, 0.618, 0.08) or fib_ok(r2, 0.786, 0.08)
            ext_ok = fib_ok(d2_d1, 1.272, 0.10) or fib_ok(d2_d1, 1.618, 0.10)

            if r1_ok and r2_ok and ext_ok:
                conf = 65 + r1_ok * 10 + r2_ok * 10 + ext_ok * 10
                return HarmonicResult(
                    "Three Drives Bullish", "LONG", min(conf, 85),
                    prices[4] * 0.998, prices[4] * 1.002,
                    prices[4] - d2 * 0.15, prices[4] + d2 * 0.618, prices[4] + d2,
                    {"D2/D1": round(d2_d1, 3), "R1": round(r1, 3), "R2": round(r2, 3)},
                    "Three equal falling drives — exhaustion bottom"
                )
        return None

    # ─────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────
    # SHARK — 5-0 pattern complement (O,X,A,B,C)
    # ─────────────────────────────────────────────────────────

    def detect_shark(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        Shark (5-0 complement): OX leg followed by XA, AB, BC.
        Key ratios:
          XB/XO = 1.13–1.618 (extreme OB retracement)
          XC/XA = 1.618–2.24
          BC/AB = 1.618–2.24
        Traded at Point C (the PRZ).
        """
        candidates = find_xabcd_points(df)
        best: Optional[HarmonicResult] = None
        best_conf = 0.0

        for pts in candidates:
            x_i, x_p = pts[0]
            a_i, a_p = pts[1]
            b_i, b_p = pts[2]
            c_i, c_p = pts[3]
            d_i, d_p = pts[4]

            xa = abs(a_p - x_p)
            ab = abs(b_p - a_p)
            bc = abs(c_p - b_p)
            xb = abs(b_p - x_p)
            xc = abs(c_p - x_p)
            if xa < 0.001:
                continue

            xb_xa = xb / xa
            xc_xa = xc / xa
            bc_ab = bc / ab if ab else 0

            # Bullish Shark: downtrend X>A, B retraces deep, C extends beyond X
            if x_p > a_p and b_p < x_p and c_p > x_p:
                b_ok  = 1.13 - TOL <= xb_xa <= 1.618 + TOL
                xc_ok = 1.618 - TOL <= xc_xa <= 2.24 + TOL
                bc_ok = 1.618 - TOL <= bc_ab <= 2.24 + TOL
                score = sum([b_ok * 30, xc_ok * 40, bc_ok * 30])
                if score >= 60 and score > best_conf:
                    best_conf = score
                    conf = min(62 + score * 0.25, 88)
                    sl   = d_p - xa * 0.05
                    t1   = d_p + xa * 0.382
                    t2   = d_p + xa * 0.618
                    best = HarmonicResult(
                        "Shark Bullish", "LONG", conf,
                        d_p * 0.995, d_p * 1.005, sl, t1, t2,
                        {"XB/XA": round(xb_xa, 3), "XC/XA": round(xc_xa, 3)},
                        "Shark pattern: 5-0 complement — deep OB retracement → continuation"
                    )

            # Bearish Shark: uptrend X<A, B retraces deep above X, C extends below X
            if x_p < a_p and b_p > x_p and c_p < x_p:
                b_ok  = 1.13 - TOL <= xb_xa <= 1.618 + TOL
                xc_ok = 1.618 - TOL <= xc_xa <= 2.24 + TOL
                bc_ok = 1.618 - TOL <= bc_ab <= 2.24 + TOL
                score = sum([b_ok * 30, xc_ok * 40, bc_ok * 30])
                if score >= 60 and score > best_conf:
                    best_conf = score
                    conf = min(62 + score * 0.25, 88)
                    sl   = d_p + xa * 0.05
                    t1   = d_p - xa * 0.382
                    t2   = d_p - xa * 0.618
                    best = HarmonicResult(
                        "Shark Bearish", "SHORT", conf,
                        d_p * 0.995, d_p * 1.005, sl, t1, t2,
                        {"XB/XA": round(xb_xa, 3), "XC/XA": round(xc_xa, 3)},
                        "Bearish Shark: 5-0 complement — deep OS extension → reversal"
                    )
        return best

    # ─────────────────────────────────────────────────────────
    # CYPHER — Alternate harmonic (B=0.382–0.618 of XA, C=1.13–1.414)
    # ─────────────────────────────────────────────────────────

    def detect_cypher(self, df: pd.DataFrame) -> Optional[HarmonicResult]:
        """
        Cypher: B at 38.2–61.8% of XA, C at 113–141.4% of XA (key distinction),
        D at 78.6% retracement of XC.
        Discovered by Darren Oglesbee. Medium-frequency, high accuracy.
        """
        candidates = find_xabcd_points(df)
        best: Optional[HarmonicResult] = None
        best_conf = 0.0

        for pts in candidates:
            x_i, x_p = pts[0]
            a_i, a_p = pts[1]
            b_i, b_p = pts[2]
            c_i, c_p = pts[3]
            d_i, d_p = pts[4]

            xa = abs(a_p - x_p)
            ab = abs(b_p - a_p)
            bc = abs(c_p - b_p)
            xc = abs(c_p - x_p)
            xd = abs(d_p - x_p)
            if xa < 0.001:
                continue

            xb_xa = ab / xa
            xc_xa = xc / xa
            xd_xc = xd / xc if xc else 0

            # Bullish Cypher: X>A (falling XA), D at 78.6% of XC
            if x_p > a_p and d_p < c_p:
                b_ok  = 0.382 - TOL <= xb_xa <= 0.618 + TOL
                xc_ok = 1.13 - TOL  <= xc_xa <= 1.414 + TOL
                xd_ok = fib_ok(xd_xc, 0.786, 0.05)
                score = sum([b_ok * 25, xc_ok * 40, xd_ok * 35])
                if score >= 60 and score > best_conf:
                    best_conf = score
                    conf = min(63 + score * 0.25, 88)
                    sl   = d_p - xa * 0.05
                    t1   = d_p + xa * 0.382
                    t2   = d_p + xa * 0.618
                    best = HarmonicResult(
                        "Cypher Bullish", "LONG", conf,
                        d_p * 0.995, d_p * 1.005, sl, t1, t2,
                        {"XB/XA": round(xb_xa, 3), "XC/XA": round(xc_xa, 3), "XD/XC": round(xd_xc, 3)},
                        "Cypher pattern: C beyond XA (1.13–1.414), D at 78.6% of XC"
                    )

            # Bearish Cypher: X<A (rising XA), D at 78.6% of XC
            if x_p < a_p and d_p > c_p:
                b_ok  = 0.382 - TOL <= xb_xa <= 0.618 + TOL
                xc_ok = 1.13 - TOL  <= xc_xa <= 1.414 + TOL
                xd_ok = fib_ok(xd_xc, 0.786, 0.05)
                score = sum([b_ok * 25, xc_ok * 40, xd_ok * 35])
                if score >= 60 and score > best_conf:
                    best_conf = score
                    conf = min(63 + score * 0.25, 88)
                    sl   = d_p + xa * 0.05
                    t1   = d_p - xa * 0.382
                    t2   = d_p - xa * 0.618
                    best = HarmonicResult(
                        "Cypher Bearish", "SHORT", conf,
                        d_p * 0.995, d_p * 1.005, sl, t1, t2,
                        {"XB/XA": round(xb_xa, 3), "XC/XA": round(xc_xa, 3), "XD/XC": round(xd_xc, 3)},
                        "Bearish Cypher: C beyond XA → D retraces 78.6% of XC"
                    )
        return best

    # MAIN SCAN
    # ─────────────────────────────────────────────────────────

    def scan_all(self, df: pd.DataFrame) -> List[HarmonicResult]:
        """Run all harmonic detectors and return found patterns."""
        results = []
        for detector in [
            self.detect_abcd,
            self.detect_gartley,
            self.detect_butterfly,
            self.detect_bat,
            self.detect_crab,
            self.detect_shark,
            self.detect_cypher,
            self.detect_ote,
            self.detect_three_drives,
        ]:
            try:
                r = detector(df)
                if r and r.confidence >= 58:
                    results.append(r)
            except Exception as e:
                logger.debug(f"Harmonic {detector.__name__}: {e}")
        return results


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_detector: Optional[HarmonicPatternDetector] = None


def get_harmonic_detector() -> HarmonicPatternDetector:
    global _detector
    if _detector is None:
        _detector = HarmonicPatternDetector()
    return _detector
