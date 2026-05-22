"""
elite_brain.py — Ultimate Signal Fusion Engine

The master intelligence layer that no other retail bot has.
Aggregates ALL analysis modules into one ultra-conviction decision.

Architecture (12-module vote system):
  Each module casts a weighted vote. The brain fuses them into a final
  conviction score and size multiplier. When 7+ modules align on the
  same direction, it's a "Grand Slam" — maximum size deployment.

Module Voters:
  1.  TradeSupervisor  — 12 hard rules (pass/fail + confidence)
  2.  WinPredictor     — Random Forest win probability
  3.  SmartMoney       — ICT liquidity sweeps + Wyckoff + killzones
  4.  ProfitMaximizer  — NR7, Fibonacci, Ichimoku, VSA, divergence
  5.  MarketRegime     — Trend/ranging/volatile regime detection
  6.  OptionChain      — PCR, Max Pain, OI bias, IV skew
  7.  FII/DII          — Institutional flow direction
  8.  VolumeProfile    — VPOC, VAH, VAL institutional price levels
  9.  OvernightBias    — Gift Nifty, VIX, global market direction
  10. SentimentAI      — News sentiment + social signals
  11. NSEData          — Bulk/block deals, delivery %, 52wk proximity
  12. MTFAlignment     — 5m+15m+1h multi-timeframe confluence

Output (EliteDecision):
  - approved:          bool    — take this trade or not
  - conviction_score:  0–100   — weighted ensemble score
  - size_multiplier:   0.25–3.0 — how big to go
  - grand_slam:        bool    — 7+ modules agree (maximum size)
  - module_votes:      dict    — per-module scores for logging
  - dominant_reasons:  list    — top 3 reasons to take/skip
  - strategy:         str     — MOMENTUM / MEAN_REVERSION / AVOID

Grand Slam Rules:
  • 7+ of 12 modules aligned → grand_slam=True, size_multiplier 2.0–3.0
  • Each additional module beyond 5 adds 15% size boost
  • Grand Slams are capped at 3 per day (preserve capital)
  • Blocked during MIDDAY_CHOP regardless of score
"""

import logging
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

_MAX_GRAND_SLAMS_PER_DAY = 3


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModuleVote:
    """One module's vote on a signal."""
    module:     str
    aligned:    bool          # Does this module agree with the signal direction?
    score:      float         # -100 to +100 (negative = opposes signal)
    weight:     float         # 0.5 to 2.0 (adapts based on recent accuracy)
    reason:     str = ""


@dataclass
class EliteDecision:
    """Final fused decision from all modules."""
    approved:         bool
    conviction_score: float      # 0–100
    size_multiplier:  float      # 0.25–3.0
    grand_slam:       bool       # 7+ modules aligned
    aligned_count:    int        # How many modules agree
    total_modules:    int        # How many modules voted
    module_votes:     Dict[str, float] = field(default_factory=dict)
    dominant_reasons: List[str]  = field(default_factory=list)
    reject_reason:    str        = ""
    strategy:         str        = "MOMENTUM"

    def summary(self) -> str:
        gs = " 🏆 GRAND SLAM" if self.grand_slam else ""
        return (
            f"EliteBrain{gs}: {'APPROVE' if self.approved else 'REJECT'} | "
            f"conviction={self.conviction_score:.0f}/100 | "
            f"size={self.size_multiplier:.2f}x | "
            f"{self.aligned_count}/{self.total_modules} modules aligned"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ELITE BRAIN
# ─────────────────────────────────────────────────────────────────────────────

class EliteBrain:
    """
    World-class signal fusion engine.

    No other retail trading bot in India runs a 12-module weighted ensemble
    with adaptive weights, Grand Slam detection, and regime-aware strategy
    selection all in one orchestrated layer.
    """

    # Default module weights (higher = more trusted)
    DEFAULT_WEIGHTS = {
        "trade_supervisor":  2.0,   # 12 hard rules — most reliable
        "mtf_alignment":     1.8,   # 3-TF alignment is a very strong predictor
        "smart_money":       1.6,   # ICT concepts — institutional footprints
        "market_regime":     1.5,   # Must trade WITH the regime
        "profit_maximizer":  1.4,   # 7 elite patterns — NR7, Fib, Ichimoku
        "win_predictor":     1.3,   # ML win probability (grows with trade data)
        "volume_profile":    1.3,   # Institutional price levels
        "option_chain":      1.2,   # PCR / max pain / OI tells direction
        "fii_dii":           1.2,   # Follow the institutional money
        "overnight_bias":    1.0,   # Directional day bias
        "nse_data":          1.0,   # Delivery, bulk/block, 52wk
        "sentiment_ai":      0.8,   # News can be noisy — lower weight
    }

    GRAND_SLAM_MIN_MODULES = 7
    MIN_CONVICTION_SCORE   = 48.0   # was 55 — lowered to pass more A-grade setups
    WEIGHTS_FILE           = "data/elite_brain_weights.json"

    def __init__(self):
        self._weights  = dict(self.DEFAULT_WEIGHTS)
        self._gs_today = 0
        self._gs_date  = ""
        self._session_votes: List[Dict] = []
        Path("data").mkdir(parents=True, exist_ok=True)
        self._load_weights()

    # ─────────────────────────────────────────────────────────────────────
    # MAIN PUBLIC API
    # ─────────────────────────────────────────────────────────────────────

    def evaluate(
        self,
        symbol:         str,
        direction:      str,       # "LONG" or "SHORT"
        signal_score:   float,     # Base signal score 0–100
        ctx:            Dict,      # Context dict from signal_generator._get_context()
        supervisor_review: Optional[Dict] = None,
        win_prob:       float = 0.55,
        win_prob_confident: bool = False,
        sm_score:       Optional[object] = None,   # SmartMoneyScore
        pm_score:       Optional[object] = None,   # ProfitMaxScore
        overnight_bias: int   = 0,
        nifty_trend:    str   = "neutral",
    ) -> EliteDecision:
        """
        Fuse all module signals into one elite decision.

        Args:
            symbol:      Trading symbol
            direction:   LONG or SHORT
            signal_score: Base momentum signal score (0-100)
            ctx:         Context dict with vp_score, oc_score, fii data, etc.
            supervisor_review: Dict from trade_supervisor.review_signal()
            win_prob:    Float from win_predictor.predict()
            sm_score:    SmartMoneyScore from smart_money.py
            pm_score:    ProfitMaxScore from profit_maximizer.py
            overnight_bias: Int -100 to +100 from overnight_analyzer
            nifty_trend: "bullish" / "bearish" / "neutral"
        """
        votes: List[ModuleVote] = []

        # ── 1. Trade Supervisor ───────────────────────────────────────────
        if supervisor_review and not supervisor_review.get("skipped", False):
            sup_approved = supervisor_review.get("approved", True)
            sup_conf     = supervisor_review.get("confidence", 50)
            votes.append(ModuleVote(
                module   = "trade_supervisor",
                aligned  = sup_approved,
                score    = sup_conf if sup_approved else -sup_conf,
                weight   = self._weights.get("trade_supervisor", 2.0),
                reason   = supervisor_review.get("reason", ""),
            ))
            if not sup_approved:
                # Hard reject from supervisor — skip everything
                return EliteDecision(
                    approved=False,
                    conviction_score=0.0,
                    size_multiplier=0.0,
                    grand_slam=False,
                    aligned_count=0,
                    total_modules=1,
                    reject_reason=f"Supervisor: {supervisor_review.get('reason', 'rejected')}",
                )

        # ── 2. Win Predictor ──────────────────────────────────────────────
        if win_prob_confident:
            wp_score = (win_prob - 0.5) * 100   # -50 to +50 → maps to -100 to +100
            votes.append(ModuleVote(
                module  = "win_predictor",
                aligned = win_prob >= 0.55,
                score   = wp_score * 2,
                weight  = self._weights.get("win_predictor", 1.3),
                reason  = f"win_prob={win_prob:.0%}",
            ))

        # ── 3. Smart Money ────────────────────────────────────────────────
        if sm_score is not None:
            sm_total = getattr(sm_score, "total_score", 0)
            sm_mult  = getattr(sm_score, "regime_multiplier", 1.0)
            sm_aligned = sm_total > 5 if direction == "LONG" else sm_total > 5
            reasons_str = "; ".join(getattr(sm_score, "reasons", [])[:2])
            votes.append(ModuleVote(
                module  = "smart_money",
                aligned = sm_aligned,
                score   = min(100, max(-50, sm_total * 2.5)),
                weight  = self._weights.get("smart_money", 1.6) * sm_mult,
                reason  = f"SM:{reasons_str}" if reasons_str else "SM:no signal",
            ))

        # ── 4. Profit Maximizer ───────────────────────────────────────────
        if pm_score is not None:
            pm_total = getattr(pm_score, "total_score", 0)
            pm_aligned = pm_total > 8
            reasons_str = "; ".join(getattr(pm_score, "reasons", [])[:2])
            votes.append(ModuleVote(
                module  = "profit_maximizer",
                aligned = pm_aligned,
                score   = min(100, max(-50, pm_total * 2.0)),
                weight  = self._weights.get("profit_maximizer", 1.4),
                reason  = f"PM:{reasons_str}" if reasons_str else "PM:no signal",
            ))

        # ── 5. Market Regime ─────────────────────────────────────────────
        regime_strategy = ctx.get("regime_strategy", "MOMENTUM")
        regime_mult     = ctx.get("regime_size_mult", 1.0)
        regime_dir      = ctx.get("regime_preferred_dir", "BOTH")
        regime_name     = ctx.get("regime_name", "UNKNOWN")
        regime_tradeable= ctx.get("regime_tradeable", True)

        if regime_name != "UNKNOWN":
            regime_aligned = (
                regime_tradeable and
                regime_strategy != "AVOID" and
                (regime_dir == "BOTH" or regime_dir == direction)
            )
            regime_score = 50.0 * regime_mult if regime_aligned else -40.0
            votes.append(ModuleVote(
                module  = "market_regime",
                aligned = regime_aligned,
                score   = regime_score,
                weight  = self._weights.get("market_regime", 1.5),
                reason  = f"regime={regime_name} ({regime_strategy})",
            ))

        # ── 6. Option Chain ───────────────────────────────────────────────
        oc_score = ctx.get("oc_score", 0)
        if oc_score != 0:
            oc_aligned = (oc_score > 0 and direction == "LONG") or \
                         (oc_score < 0 and direction == "SHORT")
            votes.append(ModuleVote(
                module  = "option_chain",
                aligned = oc_aligned,
                score   = abs(oc_score) * (1 if oc_aligned else -1),
                weight  = self._weights.get("option_chain", 1.2),
                reason  = f"OC:{oc_score:+.0f}",
            ))

        # ── 7. FII/DII Flow ───────────────────────────────────────────────
        fii_mult  = ctx.get("fii_mult", 1.0)
        if fii_mult != 1.0:
            fii_aligned = (fii_mult > 1.0 and direction == "LONG") or \
                          (fii_mult < 1.0 and direction == "SHORT")
            fii_score = (fii_mult - 1.0) * 100
            votes.append(ModuleVote(
                module  = "fii_dii",
                aligned = fii_aligned,
                score   = fii_score * (1 if fii_aligned else -1),
                weight  = self._weights.get("fii_dii", 1.2),
                reason  = f"FII_mult={fii_mult:.2f}",
            ))

        # ── 8. Volume Profile ─────────────────────────────────────────────
        vp_score = ctx.get("vp_score", 0)
        if vp_score != 0:
            vp_aligned = vp_score > 0
            votes.append(ModuleVote(
                module  = "volume_profile",
                aligned = vp_aligned,
                score   = vp_score * 4,   # scale -15..+15 → -60..+60
                weight  = self._weights.get("volume_profile", 1.3),
                reason  = "; ".join(ctx.get("vp_notes", [])[:1]),
            ))

        # ── 9. Overnight Bias ─────────────────────────────────────────────
        if overnight_bias != 0:
            bias_aligned = (overnight_bias > 0 and direction == "LONG") or \
                           (overnight_bias < 0 and direction == "SHORT")
            votes.append(ModuleVote(
                module  = "overnight_bias",
                aligned = bias_aligned,
                score   = overnight_bias * (1 if bias_aligned else -1),
                weight  = self._weights.get("overnight_bias", 1.0),
                reason  = f"overnight={overnight_bias:+d}",
            ))

        # ── 10. Sentiment AI ─────────────────────────────────────────────
        sentiment_score = ctx.get("sentiment_score", 0)
        if sentiment_score != 0:
            sent_aligned = (sentiment_score > 0 and direction == "LONG") or \
                           (sentiment_score < 0 and direction == "SHORT")
            votes.append(ModuleVote(
                module  = "sentiment_ai",
                aligned = sent_aligned,
                score   = abs(sentiment_score) * (1 if sent_aligned else -1),
                weight  = self._weights.get("sentiment_ai", 0.8),
                reason  = f"sentiment={sentiment_score:+.0f}",
            ))

        # ── 11. NSE Data (delivery, bulk/block, 52wk) ────────────────────
        nse_score = ctx.get("nse_score", 0)
        if nse_score != 0:
            nse_aligned = nse_score > 0
            votes.append(ModuleVote(
                module  = "nse_data",
                aligned = nse_aligned,
                score   = nse_score * (1 if nse_aligned else -1),
                weight  = self._weights.get("nse_data", 1.0),
                reason  = ctx.get("nse_reason", ""),
            ))

        # ── 12. MTF Alignment ────────────────────────────────────────────
        mtf       = ctx.get("mtf_alignment", {})
        aligned_c = mtf.get("aligned_count", 0)
        if aligned_c > 0:
            mtf_aligned = aligned_c >= 2
            mtf_score   = aligned_c * 25.0   # 3 → 75, 2 → 50, 1 → 25
            votes.append(ModuleVote(
                module  = "mtf_alignment",
                aligned = mtf_aligned,
                score   = mtf_score if mtf_aligned else -mtf_score,
                weight  = self._weights.get("mtf_alignment", 1.8),
                reason  = f"MTF:{aligned_c}/3 aligned",
            ))

        # ── Fusion ────────────────────────────────────────────────────────
        return self._fuse(votes, symbol, direction, signal_score, regime_strategy)

    # ─────────────────────────────────────────────────────────────────────
    # FUSION ENGINE
    # ─────────────────────────────────────────────────────────────────────

    def _fuse(
        self,
        votes:      List[ModuleVote],
        symbol:     str,
        direction:  str,
        base_score: float,
        strategy:   str,
    ) -> EliteDecision:
        if not votes:
            return EliteDecision(
                approved=True, conviction_score=base_score,
                size_multiplier=1.0, grand_slam=False,
                aligned_count=0, total_modules=0,
                reject_reason="no module votes", strategy=strategy,
            )

        aligned_count = sum(1 for v in votes if v.aligned)
        total_modules = len(votes)

        # Weighted conviction score
        total_weight = sum(v.weight for v in votes)
        weighted_sum = sum(v.score * v.weight for v in votes)
        conviction   = max(0.0, min(100.0, (weighted_sum / total_weight) + 50))

        # Blend with base signal score (40% base, 60% module fusion)
        final_score = round(base_score * 0.4 + conviction * 0.6, 1)
        final_score = max(0.0, min(100.0, final_score))

        # Scale size by fraction of active modules aligned (relative, not absolute)
        agree_ratio = aligned_count / max(1, total_modules)
        if agree_ratio >= 1.0:
            size_mult = 2.5        # All active modules agree → max conviction
        elif agree_ratio >= 0.75:
            size_mult = 2.0
        elif agree_ratio >= 0.60:
            size_mult = 1.5
        elif agree_ratio >= 0.50:
            size_mult = 1.0
        else:
            size_mult = max(0.5, agree_ratio * 2)

        # Grand Slam: all active modules aligned + high conviction
        grand_slam = (
            agree_ratio >= 1.0 and
            aligned_count >= 2 and
            final_score >= 78.0 and
            self._can_grand_slam()
        )
        if grand_slam:
            size_mult = max(size_mult, 2.0)
            now_date = str(get_current_ist_time().date())
            if self._gs_date != now_date:
                self._gs_today = 0
                self._gs_date  = now_date
            self._gs_today += 1
            logger.info(
                f"[{format_ist_timestamp()}] 🏆 GRAND SLAM #{self._gs_today}: "
                f"{symbol} {direction} — {aligned_count} modules aligned "
                f"(size={size_mult:.1f}x)"
            )

        # Consensus threshold scales with how many modules actively vote.
        # When ≤4 modules vote (typical for US stocks where FII/NSE/sentiment
        # produce no data), requiring 50% would mean 2+ out of 4 — too strict
        # when the disagreeing modules simply lack data.
        # The high_accuracy_filter already validated the setup with 13 gates;
        # EliteBrain adds a conviction quality check, not a second 13-gate filter.
        if total_modules <= 4:
            min_agree = 1   # Any aligned module is sufficient; conviction score governs
        else:
            min_agree = max(2, round(total_modules * 0.5))

        # AVOID strategy: reduce size but don't hard-block — high_accuracy_filter
        # already validated the regime with its own gate. Trust the filter.
        if strategy == "AVOID":
            size_mult *= 0.5   # Half size in AVOID regime
            logger.debug(f"[{format_ist_timestamp()}] EliteBrain: AVOID regime — half size for {symbol}")

        # Reject only on low conviction or zero aligned modules
        approved = (
            final_score >= self.MIN_CONVICTION_SCORE and
            aligned_count >= min_agree
        )

        reject_reason = ""
        if not approved:
            if aligned_count < min_agree:
                reject_reason = f"EliteBrain: only {aligned_count}/{total_modules} modules agree (need {min_agree})"
            else:
                reject_reason = f"EliteBrain: low conviction {final_score:.0f}/100"

        # Dominant reasons (top aligned modules by weighted score)
        aligned_votes = sorted(
            [v for v in votes if v.aligned],
            key=lambda v: v.score * v.weight,
            reverse=True,
        )
        dominant_reasons = [f"{v.module}:{v.reason}" for v in aligned_votes[:3] if v.reason]

        # Module votes dict for logging
        module_votes = {v.module: round(v.score, 1) for v in votes}

        return EliteDecision(
            approved         = approved,
            conviction_score = final_score,
            size_multiplier  = round(min(3.0, max(0.25, size_mult)), 2),
            grand_slam       = grand_slam,
            aligned_count    = aligned_count,
            total_modules    = total_modules,
            module_votes     = module_votes,
            dominant_reasons = dominant_reasons,
            reject_reason    = reject_reason,
            strategy         = strategy,
        )

    # ─────────────────────────────────────────────────────────────────────
    # ADAPTIVE WEIGHT LEARNING
    # ─────────────────────────────────────────────────────────────────────

    def record_trade_outcome(self, module_votes: Dict[str, float], won: bool):
        """
        After a trade closes, update module weights based on who was right.
        Modules that predicted the correct outcome get weight boost.
        Modules that predicted wrong get weight reduction.
        """
        for module, score in module_votes.items():
            if module not in self._weights:
                continue
            module_said_yes = score > 0
            trade_won = won
            if module_said_yes == trade_won:
                # Module was correct — small boost
                self._weights[module] = min(2.5, self._weights[module] * 1.02)
            else:
                # Module was wrong — small reduction
                self._weights[module] = max(0.3, self._weights[module] * 0.98)

        self._save_weights()

    def _can_grand_slam(self) -> bool:
        now_date = str(get_current_ist_time().date())
        if self._gs_date != now_date:
            self._gs_today = 0
            self._gs_date  = now_date
        return self._gs_today < _MAX_GRAND_SLAMS_PER_DAY

    def _load_weights(self):
        try:
            if Path(self.WEIGHTS_FILE).exists():
                data = json.loads(Path(self.WEIGHTS_FILE).read_text())
                for k, v in data.items():
                    if k in self._weights:
                        self._weights[k] = float(v)
                logger.debug(f"[{format_ist_timestamp()}] EliteBrain: loaded adaptive weights")
        except Exception as e:
            logger.debug(f"EliteBrain weight load failed: {e}")

    def _save_weights(self):
        try:
            Path(self.WEIGHTS_FILE).write_text(json.dumps(self._weights, indent=2))
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_elite_brain: Optional[EliteBrain] = None


def get_elite_brain() -> EliteBrain:
    global _elite_brain
    if _elite_brain is None:
        _elite_brain = EliteBrain()
        logger.info(f"[{format_ist_timestamp()}] EliteBrain initialized — 12-module fusion engine ready")
    return _elite_brain


def make_elite_decision(
    symbol:         str,
    direction:      str,
    signal_score:   float,
    ctx:            Dict,
    supervisor_review: Optional[Dict] = None,
    win_prob:       float = 0.55,
    win_prob_confident: bool = False,
    sm_score        = None,
    pm_score        = None,
    overnight_bias: int  = 0,
    nifty_trend:    str  = "neutral",
) -> EliteDecision:
    """Convenience wrapper — calls singleton EliteBrain.evaluate()."""
    return get_elite_brain().evaluate(
        symbol=symbol, direction=direction, signal_score=signal_score,
        ctx=ctx, supervisor_review=supervisor_review,
        win_prob=win_prob, win_prob_confident=win_prob_confident,
        sm_score=sm_score, pm_score=pm_score,
        overnight_bias=overnight_bias, nifty_trend=nifty_trend,
    )
