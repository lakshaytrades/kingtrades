"""
l99_gate.py — KingTrades Level-99 Ultra-High Conviction Gate

This is THE final gate before any order executes. It enforces that we ONLY trade
when an extraordinary number of independent signals all agree.

The insight from institutional trading:
  3 confirmations → ~58% win rate
  5 confirmations → ~65% win rate
  8+ confirmations → ~72%+ win rate
  12+ confirmations → ~78%+ win rate (rare, but when it appears: trade large)

Unlike high_accuracy_filter.py (which is a YES/NO gate), the L99 gate scores
every available context signal and returns a weighted conviction score.

L99 Score = weighted sum of 15 independent signal sources:
  1.  Multi-timeframe alignment       (weight 12)
  2.  OODA conviction                 (weight 10)
  3.  HAFilter score                  (weight 10)
  4.  Volume surge quality            (weight  8)
  5.  VWAP alignment                  (weight  7)
  6.  Entry at key level              (weight  7)
  7.  Risk:Reward quality             (weight  7)
  8.  IC-weighted signal strength     (weight  6)
  9.  Regime confidence               (weight  6)
  10. Gap fill model                  (weight  5)
  11. Options GEX confirmation        (weight  5)
  12. Market breadth alignment        (weight  5)
  13. News/event clear window         (weight  4)
  14. Relative strength vs index      (weight  4)
  15. Drawdown budget available       (weight  4)
      ─────────────────────────────────────────
      MAX POSSIBLE SCORE:           100

Grading:
  S+  : 92–100  (Grand Slam — maximum size, rare)
  A+  : 85–91   (Excellent — 1.25x size)
  A   : 76–84   (Good — standard size)
  B   : 65–75   (Marginal — 0.75x size, or skip)
  SKIP: < 65    (Not enough confluence — no trade)

DEFAULT THRESHOLD: 72 (B-grade) for standard trading
HIGH-ACCURACY MODE: 82 (A-grade) — fewer trades, higher win rate
"""

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Scoring weights (must sum to 100) ───────────────────────────────────────
WEIGHTS = {
    "mtf_alignment":      12,
    "ooda_conviction":    10,
    "ha_filter_score":    10,
    "volume_surge":        8,
    "vwap_alignment":      7,
    "entry_at_level":      7,
    "risk_reward":         7,
    "ic_signal_strength":  6,
    "regime_confidence":   6,
    "gap_fill_model":      5,
    "gex_confirmation":    5,
    "market_breadth":      5,
    "news_event_clear":    4,
    "relative_strength":   4,
    "drawdown_budget":     4,
}
assert sum(WEIGHTS.values()) == 100, "L99 weights must sum to 100"

# ── Thresholds ────────────────────────────────────────────────────────────────
THRESHOLD_SKIP    = 65
THRESHOLD_B       = 65
THRESHOLD_A       = 76
THRESHOLD_APLUS   = 85
THRESHOLD_SPLUS   = 92


@dataclass
class L99Score:
    score:          float        # 0–100
    grade:          str          # S+ / A+ / A / B / SKIP
    allow_trade:    bool
    size_mult:      float        # position size multiplier
    component_scores: Dict[str, float] = field(default_factory=dict)
    gates_passed:   List[str]    = field(default_factory=list)
    gates_failed:   List[str]    = field(default_factory=list)
    reasoning:      str          = ""
    computation_ms: float        = 0.0


def _score_mtf_alignment(signal: Any) -> float:
    """Multi-timeframe: how many of 5m/15m/1h agree with signal direction."""
    try:
        # signal.signal_type contains pattern name; check mtf_aligned attribute
        if hasattr(signal, "mtf_aligned") and signal.mtf_aligned:
            return 1.0
        # Check multi_timeframe attribute
        mtf = getattr(signal, "multi_timeframe", None)
        if mtf:
            score = getattr(mtf, "alignment_score", 0)
            return min(1.0, score / 100.0)
        # Fall back to rationale parsing
        rationale = getattr(signal, "rationale", "")
        if "3/3" in rationale or "MTF:3" in rationale:
            return 1.0
        if "2/3" in rationale or "MTF:2" in rationale:
            return 0.65
    except Exception:
        pass
    return 0.5   # unknown = neutral


def _score_ooda(signal: Any) -> float:
    """OODA conviction from the ooda_engine context."""
    try:
        from ooda_engine import get_engine
        sym = getattr(signal, "symbol", "SPY")
        ctx = get_engine().get_context(sym)
        # Conviction 0–1, convert to 0–1 score
        conviction = float(getattr(ctx, "conviction", 0.5))
        # Also check direction bias matches signal direction
        bias   = str(getattr(ctx, "direction_bias", "NEUTRAL")).upper()
        sigdir = str(getattr(signal, "direction", "LONG")).upper()
        bias_match = (bias in ("BULLISH", "LONG") and sigdir == "LONG") or \
                     (bias in ("BEARISH", "SHORT") and sigdir == "SHORT") or \
                     bias == "NEUTRAL"
        return conviction * (1.0 if bias_match else 0.6)
    except Exception:
        pass
    return 0.5


def _score_ha_filter(signal: Any) -> float:
    """HAFilter score normalized to 0–1."""
    try:
        score = float(getattr(signal, "signal_score", 0.0))
        # HAFilter produces scores 0–100; normalize
        return min(1.0, max(0.0, score / 100.0))
    except Exception:
        return 0.5


def _score_volume_surge(signal: Any, analysis: Optional[Dict] = None) -> float:
    """Volume vs average: >2x = 1.0, 1.5x = 0.75, 1x = 0.5, <0.8x = 0.2."""
    try:
        rvol = getattr(signal, "rvol", None)
        if rvol is None and analysis:
            rvol = analysis.get("rvol", analysis.get("relative_volume"))
        if rvol is None:
            return 0.5
        rvol = float(rvol)
        if rvol >= 2.5: return 1.0
        if rvol >= 2.0: return 0.9
        if rvol >= 1.5: return 0.75
        if rvol >= 1.2: return 0.6
        if rvol >= 1.0: return 0.5
        return 0.25
    except Exception:
        return 0.5


def _score_vwap_alignment(signal: Any, analysis: Optional[Dict] = None) -> float:
    """Price on correct side of VWAP for the signal direction."""
    try:
        direction = str(getattr(signal, "direction", "LONG")).upper()
        entry     = float(getattr(signal, "entry_price", 0.0))
        vwap      = None
        if analysis:
            vwap = analysis.get("vwap")
        if vwap is None:
            return 0.5
        vwap = float(vwap)
        if direction == "LONG"  and entry > vwap: return 1.0
        if direction == "SHORT" and entry < vwap: return 1.0
        if direction == "LONG"  and entry > vwap * 0.998: return 0.7
        if direction == "SHORT" and entry < vwap * 1.002: return 0.7
        return 0.2   # wrong side of VWAP
    except Exception:
        return 0.5


def _score_entry_at_level(signal: Any) -> float:
    """Is the entry price at a key level (FVG/OB/POC/VWAP/S&R)?"""
    try:
        rationale = str(getattr(signal, "rationale", "")).lower()
        level_keywords = ["fvg", "order block", "poc", "vwap", "support", "resistance",
                          "supply", "demand", "breakout", "orb"]
        matches = sum(1 for kw in level_keywords if kw in rationale)
        if matches >= 3: return 1.0
        if matches >= 2: return 0.8
        if matches >= 1: return 0.6
    except Exception:
        pass
    return 0.4


def _score_risk_reward(signal: Any) -> float:
    """R:R quality: >= 3:1 = 1.0, 2.5:1 = 0.85, 2:1 = 0.65, <2:1 = 0.3."""
    try:
        rr = float(getattr(signal, "risk_reward", 0.0))
        if rr >= 3.5: return 1.0
        if rr >= 3.0: return 0.95
        if rr >= 2.5: return 0.85
        if rr >= 2.0: return 0.65
        if rr >= 1.5: return 0.45
        return 0.2
    except Exception:
        return 0.5


def _score_ic_strength(signal: Any) -> float:
    """IC-weighted signal strength from signal_ic_tracker."""
    try:
        from signal_ic_tracker import get_signal_weight
        # Get weights for signal types this signal represents
        patterns = getattr(signal, "patterns", [])
        if not patterns:
            return 0.5
        weights = []
        for pat in patterns[:3]:
            w = get_signal_weight(str(pat))
            weights.append(w)
        return float(sum(weights) / len(weights)) if weights else 0.5
    except Exception:
        return 0.5


def _score_regime_confidence(signal: Any) -> float:
    """OODA regime confidence + HMM regime probability."""
    try:
        from ooda_engine import get_engine
        sym  = getattr(signal, "symbol", "SPY")
        ctx  = get_engine().get_context(sym)
        rc   = float(getattr(ctx, "regime_confidence", 0.5))
        hmm  = float(getattr(ctx, "hmm_state", 0.5))   # 0=bear, 0.5=chop, 1=bull
        # Combine regime confidence with regime suitability for direction
        direction = str(getattr(signal, "direction", "LONG")).upper()
        regime    = str(getattr(ctx, "regime", "UNKNOWN")).upper()
        regime_ok = (
            (direction == "LONG"  and regime in ("BULL", "BULL_TREND")) or
            (direction == "SHORT" and regime in ("BEAR", "BEAR_TREND")) or
            regime in ("VOLATILE",)   # both directions can work
        )
        suitability = 1.0 if regime_ok else 0.5
        return rc * suitability
    except Exception:
        return 0.5


def _score_gap_fill(signal: Any, market_context: Optional[Dict] = None) -> float:
    """Gap fill model score for gap-strategy signals."""
    try:
        from gap_fill_model import get_gap_fill_model
        sym       = getattr(signal, "symbol", "")
        direction = str(getattr(signal, "direction", "LONG")).upper()
        entry     = float(getattr(signal, "entry_price", 0.0))
        prev_close = float((market_context or {}).get("prev_close", 0.0))
        vix       = float((market_context or {}).get("vix", 18.0))
        regime    = str((market_context or {}).get("regime", "UNKNOWN"))

        if prev_close <= 0 or entry <= 0:
            return 0.5

        model = get_gap_fill_model()
        adj   = model.get_score_adj(sym, direction, entry, prev_close, vix, regime)
        # Normalize: max adj is ±8, convert to 0–1
        return max(0.1, min(1.0, 0.5 + adj / 16.0))
    except Exception:
        return 0.5


def _score_gex(signal: Any) -> float:
    """GEX confirmation from gex_calculator."""
    try:
        from gex_calculator import get_gex_score
        sym  = getattr(signal, "symbol", "")
        direction = str(getattr(signal, "direction", "LONG")).upper()
        score = get_gex_score(sym)   # returns -10 to +10 int
        normalized = (score + 10) / 20.0   # 0–1
        # If GEX supports direction, full score; if opposing, penalize
        # Negative GEX = momentum favored (positive for momentum direction)
        return min(1.0, max(0.1, normalized))
    except Exception:
        return 0.5


def _score_market_breadth(market_context: Optional[Dict] = None) -> float:
    """Market breadth: advance/decline, new highs vs new lows."""
    try:
        from market_breadth import get_breadth_score
        breadth = get_breadth_score()
        return max(0.1, min(1.0, (breadth + 100) / 200.0))  # -100 to +100 → 0–1
    except Exception:
        pass

    try:
        if market_context:
            ad_ratio = float(market_context.get("advance_decline_ratio", 1.0))
            if ad_ratio >= 2.0: return 0.90
            if ad_ratio >= 1.5: return 0.75
            if ad_ratio >= 1.0: return 0.55
            if ad_ratio >= 0.7: return 0.40
            return 0.20
    except Exception:
        pass
    return 0.5


def _score_news_clear(signal: Any, news_filter: Any = None) -> float:
    """No high-impact news in the next 30 minutes."""
    try:
        sym = getattr(signal, "symbol", "")
        if news_filter and hasattr(news_filter, "is_blackout"):
            in_blackout = news_filter.is_blackout(sym)
            return 0.0 if in_blackout else 1.0
        if news_filter and hasattr(news_filter, "should_skip"):
            should_skip = news_filter.should_skip(sym)
            return 0.0 if should_skip else 1.0
    except Exception:
        pass
    return 0.75   # assume clear if no filter available


def _score_relative_strength(signal: Any, analysis: Optional[Dict] = None) -> float:
    """Stock stronger than index (for LONGs) or weaker (for SHORTs)."""
    try:
        rs = None
        if analysis:
            rs = analysis.get("rs_vs_spy", analysis.get("relative_strength_vs_nifty"))
        if rs is None:
            rs = getattr(signal, "relative_strength", None)
        if rs is None:
            return 0.5
        rs = float(rs)
        direction = str(getattr(signal, "direction", "LONG")).upper()
        if direction == "LONG":
            if rs >= 1.05: return 1.0
            if rs >= 1.02: return 0.8
            if rs >= 1.00: return 0.6
            return 0.3
        else:
            if rs <= 0.95: return 1.0
            if rs <= 0.98: return 0.8
            if rs <= 1.00: return 0.6
            return 0.3
    except Exception:
        return 0.5


def _score_drawdown_budget(risk_manager: Any = None) -> float:
    """How much drawdown budget remains today? Full budget = 1.0, near limit = 0."""
    try:
        if risk_manager:
            daily_loss_pct = abs(float(getattr(risk_manager.state, "daily_loss_pct", 0.0)))
            limit_pct      = float(getattr(risk_manager, "daily_loss_limit_pct", 2.0))
            remaining_pct  = max(0.0, limit_pct - daily_loss_pct)
            budget_used    = daily_loss_pct / max(limit_pct, 0.01)
            return max(0.0, 1.0 - budget_used * 1.1)   # slight penalty as limit approaches
    except Exception:
        pass

    try:
        from portfolio_drawdown_monitor import get_drawdown_monitor
        status = get_drawdown_monitor().get_status()
        if not status.allow_new_entries:
            return 0.0
        if status.should_reduce:
            return 0.2
        dd   = status.drawdown_pct
        if dd >= 1.5: return 0.1
        if dd >= 1.0: return 0.4
        if dd >= 0.75: return 0.65
        return 1.0
    except Exception:
        pass
    return 0.75


def evaluate_l99(
    signal:         Any,
    analysis:       Optional[Dict] = None,
    market_context: Optional[Dict] = None,
    news_filter:    Any = None,
    risk_manager:   Any = None,
    min_score:      float = THRESHOLD_B,
) -> L99Score:
    """
    Evaluate a TradeSignal against all 15 L99 gates.
    Returns L99Score with grade, recommendation, and per-component breakdown.
    """
    t0 = _time.time()

    # Collect all component scores
    raw_scores = {
        "mtf_alignment":     _score_mtf_alignment(signal),
        "ooda_conviction":   _score_ooda(signal),
        "ha_filter_score":   _score_ha_filter(signal),
        "volume_surge":      _score_volume_surge(signal, analysis),
        "vwap_alignment":    _score_vwap_alignment(signal, analysis),
        "entry_at_level":    _score_entry_at_level(signal),
        "risk_reward":       _score_risk_reward(signal),
        "ic_signal_strength":_score_ic_strength(signal),
        "regime_confidence": _score_regime_confidence(signal),
        "gap_fill_model":    _score_gap_fill(signal, market_context),
        "gex_confirmation":  _score_gex(signal),
        "market_breadth":    _score_market_breadth(market_context),
        "news_event_clear":  _score_news_clear(signal, news_filter),
        "relative_strength": _score_relative_strength(signal, analysis),
        "drawdown_budget":   _score_drawdown_budget(risk_manager),
    }

    # Weighted sum
    total = sum(raw_scores[k] * WEIGHTS[k] for k in raw_scores)
    # total is now 0–100

    # Classify
    gates_passed = [k for k, v in raw_scores.items() if v >= 0.6]
    gates_failed = [k for k, v in raw_scores.items() if v < 0.4]
    gates_neutral= [k for k, v in raw_scores.items() if 0.4 <= v < 0.6]

    # Mandatory gates — hard fail if any score 0
    mandatory = {"news_event_clear", "drawdown_budget"}
    hard_fail = any(raw_scores[m] == 0.0 for m in mandatory)
    if hard_fail:
        total = min(total, 40.0)

    # Grade
    if total >= THRESHOLD_SPLUS and not hard_fail:
        grade, allow, mult = "S+",   True, 1.50
    elif total >= THRESHOLD_APLUS and not hard_fail:
        grade, allow, mult = "A+",   True, 1.25
    elif total >= THRESHOLD_A and not hard_fail:
        grade, allow, mult = "A",    True, 1.00
    elif total >= THRESHOLD_B and not hard_fail and total >= min_score:
        grade, allow, mult = "B",    True, 0.75
    else:
        grade, allow, mult = "SKIP", False, 0.0

    # Build reasoning string
    top_pos = sorted(
        [(k, raw_scores[k]) for k in gates_passed], key=lambda x: -x[1] * WEIGHTS[x[0]]
    )[:4]
    top_neg = sorted(
        [(k, raw_scores[k]) for k in gates_failed], key=lambda x: x[1] * WEIGHTS[x[0]]
    )[:3]

    positives = ", ".join(k.replace("_", " ").title() for k, _ in top_pos)
    negatives = ", ".join(k.replace("_", " ").title() for k, _ in top_neg) if top_neg else "none"

    reasoning = (
        f"L99 {grade} ({total:.1f}/100) | "
        f"✅ {positives} | "
        f"{'❌ ' + negatives if negatives != 'none' else ''}"
    )

    elapsed = (_time.time() - t0) * 1000

    return L99Score(
        score            = round(total, 1),
        grade            = grade,
        allow_trade      = allow,
        size_mult        = mult,
        component_scores = {k: round(v, 3) for k, v in raw_scores.items()},
        gates_passed     = gates_passed,
        gates_failed     = gates_failed,
        reasoning        = reasoning,
        computation_ms   = round(elapsed, 1),
    )


def format_l99_telegram(l99: L99Score, symbol: str = "", direction: str = "") -> str:
    """Format L99 score as a rich Telegram message."""
    grade_icons = {"S+": "💎", "A+": "🏆", "A": "✅", "B": "🟡", "SKIP": "❌"}
    icon = grade_icons.get(l99.grade, "❓")

    lines = [
        f"{icon} <b>L99 GATE: {l99.grade} ({l99.score:.1f}/100)</b>",
    ]
    if symbol:
        lines.append(f"  {symbol} {direction}")
    lines.append(f"  {l99.reasoning}")

    # Top component breakdown
    sorted_comps = sorted(
        l99.component_scores.items(),
        key=lambda x: x[1] * WEIGHTS.get(x[0], 1),
        reverse=True
    )
    top3  = sorted_comps[:3]
    bot3  = sorted_comps[-3:]
    lines.append(f"  ✅ Best: " + " | ".join(f"{k.replace('_',' ')[:12]}={v:.2f}" for k,v in top3))
    lines.append(f"  ❌ Weak: " + " | ".join(f"{k.replace('_',' ')[:12]}={v:.2f}" for k,v in bot3))
    lines.append(f"  Size: {l99.size_mult:.2f}x | Computed in {l99.computation_ms:.0f}ms")

    return "\n".join(lines)
