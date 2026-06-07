"""
Regime Pattern Weights — dynamic scoring multipliers per market regime.

18yr truth: "A breakout pattern in a trending market is gold.
The SAME pattern in a choppy/ranging market is a trap."

These weights adjust pattern scores based on the current market regime.
They are multiplied INTO the base pattern score.
"""

from typing import Dict

# Pattern categories and their regime multipliers
# Format: pattern_name_fragment → {regime: multiplier}
# A pattern score is multiplied by its regime weight
# Weights > 1.0 = boost, < 1.0 = reduce

REGIME_WEIGHTS: Dict[str, Dict[str, float]] = {
    # ─── BREAKOUT / MOMENTUM patterns ─── #
    # Work best in TRENDING markets, dangerous in RANGING
    "BREAKOUT": {
        "TRENDING_BULL": 1.40,
        "TRENDING_BEAR": 0.60,  # Don't buy breakouts in downtrend
        "RANGING":       0.55,  # Breakout = trap in ranging
        "VOLATILE":      0.70,
        "CRISIS":        0.30,
    },
    "MOMENTUM": {
        "TRENDING_BULL": 1.35,
        "TRENDING_BEAR": 1.30,
        "RANGING":       0.60,
        "VOLATILE":      0.80,
        "CRISIS":        0.40,
    },
    "BULL_FLAG": {
        "TRENDING_BULL": 1.45,
        "TRENDING_BEAR": 0.50,
        "RANGING":       0.65,
        "VOLATILE":      0.75,
        "CRISIS":        0.35,
    },
    "BEAR_FLAG": {
        "TRENDING_BULL": 0.55,
        "TRENDING_BEAR": 1.45,
        "RANGING":       0.65,
        "VOLATILE":      0.80,
        "CRISIS":        0.80,
    },
    "ORB": {  # Opening Range Breakout
        "TRENDING_BULL": 1.40,
        "TRENDING_BEAR": 1.30,
        "RANGING":       0.55,  # ORB fails badly in ranging
        "VOLATILE":      1.20,  # ORB works in volatility IF volume confirms
        "CRISIS":        0.50,
    },
    "PENNANT": {
        "TRENDING_BULL": 1.35,
        "TRENDING_BEAR": 1.25,
        "RANGING":       0.60,
        "VOLATILE":      0.85,
        "CRISIS":        0.45,
    },
    # ─── REVERSAL / MEAN-REVERSION patterns ─── #
    # Work best in RANGING/VOLATILE markets
    "VWAP": {
        "TRENDING_BULL": 0.85,  # VWAP reversion works less in strong trends
        "TRENDING_BEAR": 0.85,
        "RANGING":       1.40,  # VWAP reversion is king in ranging
        "VOLATILE":      1.20,
        "CRISIS":        1.10,
    },
    "ENGULFING": {
        "TRENDING_BULL": 0.90,
        "TRENDING_BEAR": 1.10,
        "RANGING":       1.35,  # Engulfing = strong reversal signal in ranging
        "VOLATILE":      1.20,
        "CRISIS":        0.80,
    },
    "HAMMER": {
        "TRENDING_BULL": 0.85,
        "TRENDING_BEAR": 1.30,  # Hammer at bottom of bear move = bullish
        "RANGING":       1.25,
        "VOLATILE":      1.15,
        "CRISIS":        0.90,
    },
    "SHOOTING_STAR": {
        "TRENDING_BULL": 1.25,  # Shooting star at top of bull = bearish
        "TRENDING_BEAR": 0.85,
        "RANGING":       1.20,
        "VOLATILE":      1.10,
        "CRISIS":        0.85,
    },
    "DOJI": {
        "TRENDING_BULL": 1.15,  # Doji in uptrend = warning
        "TRENDING_BEAR": 1.15,
        "RANGING":       0.75,  # Doji meaningless in ranging
        "VOLATILE":      1.00,
        "CRISIS":        0.70,
    },
    # ─── SUPPORT/RESISTANCE patterns ─── #
    "SUPPORT": {
        "TRENDING_BULL": 1.30,
        "TRENDING_BEAR": 0.65,  # Support breaks in downtrend
        "RANGING":       1.40,  # Support/resistance are key in ranging
        "VOLATILE":      1.00,
        "CRISIS":        0.60,
    },
    "RESISTANCE": {
        "TRENDING_BULL": 0.70,  # Resistance breaks in uptrend
        "TRENDING_BEAR": 1.35,
        "RANGING":       1.40,
        "VOLATILE":      1.00,
        "CRISIS":        0.60,
    },
    # ─── DIVERGENCE patterns ─── #
    "DIVERGENCE": {
        "TRENDING_BULL": 0.80,
        "TRENDING_BEAR": 0.80,
        "RANGING":       1.30,
        "VOLATILE":      1.20,
        "CRISIS":        0.70,
    },
    "RSI": {
        "TRENDING_BULL": 0.90,
        "TRENDING_BEAR": 0.90,
        "RANGING":       1.25,
        "VOLATILE":      1.10,
        "CRISIS":        0.75,
    },
    # ─── HARMONIC patterns ─── #
    # Work best in all regimes except CRISIS
    "HARMONIC": {
        "TRENDING_BULL": 1.10,
        "TRENDING_BEAR": 1.10,
        "RANGING":       1.30,
        "VOLATILE":      1.05,
        "CRISIS":        0.50,
    },
    "GARTLEY": {
        "TRENDING_BULL": 1.10,
        "TRENDING_BEAR": 1.10,
        "RANGING":       1.35,
        "VOLATILE":      1.00,
        "CRISIS":        0.45,
    },
    "BUTTERFLY": {
        "TRENDING_BULL": 1.05,
        "TRENDING_BEAR": 1.05,
        "RANGING":       1.30,
        "VOLATILE":      1.00,
        "CRISIS":        0.40,
    },
    # ─── ICHIMOKU signals ─── #
    "ICHIMOKU": {
        "TRENDING_BULL": 1.40,  # Ichimoku shines in trends
        "TRENDING_BEAR": 1.40,
        "RANGING":       0.65,  # Ichimoku meaningless in ranging
        "VOLATILE":      0.85,
        "CRISIS":        0.50,
    },
    "CLOUD": {
        "TRENDING_BULL": 1.35,
        "TRENDING_BEAR": 1.35,
        "RANGING":       0.65,
        "VOLATILE":      0.80,
        "CRISIS":        0.45,
    },
    # ─── WYCKOFF signals ─── #
    "SPRING": {
        "TRENDING_BULL": 0.90,
        "TRENDING_BEAR": 1.50,  # Spring in downtrend = highest conviction reversal
        "RANGING":       1.40,
        "VOLATILE":      1.20,
        "CRISIS":        0.80,
    },
    "MARKUP": {
        "TRENDING_BULL": 1.40,
        "TRENDING_BEAR": 0.50,
        "RANGING":       1.10,
        "VOLATILE":      0.90,
        "CRISIS":        0.40,
    },
    # ─── VOLUME signals ─── #
    "VOLUME": {
        "TRENDING_BULL": 1.25,
        "TRENDING_BEAR": 1.25,
        "RANGING":       1.15,
        "VOLATILE":      1.35,
        "CRISIS":        1.20,  # Volume always important
    },
    # Default for unrecognized patterns
    "DEFAULT": {
        "TRENDING_BULL": 1.10,
        "TRENDING_BEAR": 1.05,
        "RANGING":       0.90,
        "VOLATILE":      0.95,
        "CRISIS":        0.60,
    },
}


def get_weight(pattern_name: str, regime: str) -> float:
    """Get the regime-specific weight multiplier for a pattern name."""
    regime = regime.upper() if regime else "UNKNOWN"
    pattern_upper = (pattern_name or "").upper()

    # Match by substring — find the longest matching key
    best_key = None
    best_len = 0
    for key in REGIME_WEIGHTS:
        if key in pattern_upper and len(key) > best_len:
            best_key = key
            best_len = len(key)

    weights = REGIME_WEIGHTS.get(best_key or "DEFAULT", REGIME_WEIGHTS["DEFAULT"])
    return weights.get(regime, 1.0)


def apply_regime_weights(signals: list, regime: str) -> list:
    """
    Apply regime weights to a list of signal dicts.
    Each signal dict should have 'pattern' and 'score' keys.
    Returns modified list with adjusted scores.
    """
    if not regime or regime in ("UNKNOWN", "CRISIS"):
        if regime == "CRISIS":
            # In crisis, reduce all scores by 40%
            return [{**s, "score": s.get("score", 0) * 0.60} for s in signals]
        return signals

    result = []
    for sig in signals:
        pattern = sig.get("pattern", sig.get("signal_type", ""))
        score = sig.get("score", sig.get("ai_score", 0))
        weight = get_weight(pattern, regime)
        adjusted = {**sig, "score": score * weight}
        if "ai_score" in sig:
            adjusted["ai_score"] = sig["ai_score"] * weight
        result.append(adjusted)
    return result
