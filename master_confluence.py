"""
master_confluence.py — World-Class Signal Confluence Engine

Aggregates scores from ALL premium modules into a single "master conviction score".
Requires minimum confluence (3+ confirming signals) before approving trade.

Professional firms (Citadel, Two Sigma, AQR) use ensemble methods:
  - Weight each signal by its historical Sharpe contribution
  - Require minimum N confirming signals (confluence gate)
  - Penalize conflicting signals
  - Produce a single confidence-weighted final score

This replaces the current additive score system with a weighted ensemble.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class SignalVote:
    source: str          # e.g. "ORB", "VWAP_RECLAIM", "WYCKOFF"
    direction: str       # "LONG", "SHORT", "NEUTRAL"
    score_delta: float   # pts added/removed
    confidence: float    # 0-100 confidence of this signal
    category: str        # "momentum", "reversion", "structure", "flow", "regime"

@dataclass
class ConfluenceResult:
    final_score: float
    approved: bool          # True = take the trade
    confluence_count: int   # how many signals agreed
    conflict_count: int     # how many signals disagreed
    confidence_pct: float   # 0-100 overall conviction
    votes: List[SignalVote] = field(default_factory=list)
    breakdown: str = ""     # human-readable summary
    size_multiplier: float = 1.0

# Signal weights by category (higher = more trusted historically)
CATEGORY_WEIGHTS = {
    "structure":  1.4,   # Wyckoff, harmonic, support/resistance — highest trust
    "momentum":   1.2,   # ORB, PEAD, cross-sectional mom
    "flow":       1.3,   # Order flow, dark pool, options flow — institutional money
    "reversion":  1.0,   # Z-score, pairs convergence — good but lower weight
    "regime":     1.1,   # VIX, sector rotation, market internals
    "ml":         1.5,   # ML win probability — highest weight
}

# Minimum confluence requirements
MIN_AGREEING_SIGNALS    = 2     # at least 2 signals must agree with direction
MIN_CONFLUENCE_SCORE    = 55.0  # after all boosts, final score must be ≥55
MAX_CONFLICT_RATIO      = 0.40  # if >40% of signals conflict → reduce size


def compute_confluence(
    direction: str,
    votes: List[SignalVote],
    base_score: float,
    min_agree: int = MIN_AGREEING_SIGNALS,
) -> ConfluenceResult:
    """
    Evaluate all signal votes and compute a confluence-weighted final score.

    Args:
        direction: "LONG" or "SHORT"
        votes: List of SignalVote from each module
        base_score: Starting score (from HAF gate)
        min_agree: Minimum agreeing signals required

    Returns:
        ConfluenceResult with final_score, approved, breakdown
    """
    if not votes:
        return ConfluenceResult(
            final_score=base_score, approved=base_score >= MIN_CONFLUENCE_SCORE,
            confluence_count=0, conflict_count=0, confidence_pct=50.0,
            breakdown="No votes — using base score"
        )

    agreeing    = [v for v in votes if v.direction == direction and v.score_delta > 0]
    conflicting = [v for v in votes if v.direction != direction and v.direction != "NEUTRAL" and v.score_delta > 0]
    neutral     = [v for v in votes if v.direction == "NEUTRAL" or v.score_delta == 0]

    # Weighted score from agreeing signals
    agree_score = sum(
        v.score_delta * CATEGORY_WEIGHTS.get(v.category, 1.0)
        for v in agreeing
    )
    # Weighted penalty from conflicting signals
    conflict_penalty = sum(
        abs(v.score_delta) * CATEGORY_WEIGHTS.get(v.category, 1.0) * 0.5
        for v in conflicting
    )

    final_score = min(100.0, max(0.0, base_score + agree_score - conflict_penalty))

    # Confluence multiplier: more agreeing signals = higher confidence
    n_agree    = len(agreeing)
    n_conflict = len(conflicting)
    total_sig  = max(n_agree + n_conflict, 1)
    conflict_ratio = n_conflict / total_sig

    confidence_pct = min(99.0, 50.0 + n_agree * 8.0 - n_conflict * 5.0)
    confidence_pct = max(10.0, confidence_pct)

    # Approve if: enough agreeing signals AND score ≥ threshold AND conflict not dominant
    approved = (
        n_agree >= min_agree
        and final_score >= MIN_CONFLUENCE_SCORE
        and conflict_ratio <= MAX_CONFLICT_RATIO
    )

    # Size multiplier: 3+ signals = 1.2x, 5+ = 1.4x, 1 signal = 0.7x
    if n_agree >= 5:
        size_mult = 1.4
    elif n_agree >= 3:
        size_mult = 1.2
    elif n_agree >= 2:
        size_mult = 1.0
    else:
        size_mult = 0.7

    if conflict_ratio > 0.30:
        size_mult *= 0.8  # reduce size when conflicted

    # Breakdown text
    agree_names    = [f"{v.source}(+{v.score_delta:.0f})" for v in agreeing[:5]]
    conflict_names = [f"{v.source}(-{abs(v.score_delta):.0f})" for v in conflicting[:3]]
    breakdown = (
        f"✅{n_agree} agree: {', '.join(agree_names)}"
        + (f" | ❌{n_conflict} conflict: {', '.join(conflict_names)}" if conflicting else "")
        + f" | Score: {final_score:.0f} | Conf: {confidence_pct:.0f}%"
    )

    return ConfluenceResult(
        final_score=final_score,
        approved=approved,
        confluence_count=n_agree,
        conflict_count=n_conflict,
        confidence_pct=confidence_pct,
        votes=votes,
        breakdown=breakdown,
        size_multiplier=size_mult,
    )


def make_vote(source: str, direction: str, score_delta: float, confidence: float, category: str) -> SignalVote:
    """
    Helper to create a SignalVote.

    If score_delta > 0, the vote direction is taken as-is (agreeing with direction).
    If score_delta < 0, the vote direction is flipped to represent opposition.
    """
    if score_delta >= 0:
        vote_direction = direction
    else:
        vote_direction = "SHORT" if direction == "LONG" else "LONG"
    return SignalVote(
        source=source,
        direction=vote_direction,
        score_delta=abs(score_delta),
        confidence=confidence,
        category=category,
    )
