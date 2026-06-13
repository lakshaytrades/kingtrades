"""
elite_tracker_india.py — Self-Learning Elite Signal Tracker (v2: Bayesian)

Renaissance-style feedback loop: track which specific signal combinations
(pattern sets) actually win vs lose, using Bayesian Beta-distribution updating.

v2 enhancement: Beta(alpha, beta) posterior instead of simple WR average.
  Prior: Beta(2, 2) — uninformative prior centered at 50% WR
  Each win: alpha += 1;  each loss: beta += 1
  Posterior mean: alpha / (alpha + beta)
  Only fires signal when P(WR > 55%) > 0.80 posterior (high confidence)

This prevents false convergence on small samples and ignores noisy patterns.

Storage: data/elite_tracker.json  (persists across sessions)
         data/elite_bayes.json    (Bayesian state, separate for clean upgrade)
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("elite_tracker")
IST = ZoneInfo("Asia/Kolkata")

_TRACKER_FILE = Path(__file__).parent.parent / "data" / "elite_tracker.json"
_BAYES_FILE   = Path(__file__).parent.parent / "data" / "elite_bayes.json"
_MIN_SAMPLES  = 5    # Bayesian needs fewer samples than frequentist
_NEUTRAL_WR   = 0.55
_CONF_THRESH  = 0.80  # P(WR > 55%) must exceed this to fire

# Frequentist state (legacy, kept for backward compat)
_pattern_stats: Dict[str, dict] = {}
_loaded = False

# Bayesian state: {pattern_key: {"alpha": float, "beta_": float, "n": int}}
_bayes_state: Dict[str, dict] = {}
_bayes_loaded = False


# ── Bayesian helpers ──────────────────────────────────────────────────────────

def _beta_mean(alpha: float, beta_: float) -> float:
    return alpha / (alpha + beta_)


def _prob_wr_above(alpha: float, beta_: float, threshold: float = 0.55) -> float:
    """P(WR > threshold) using normal approximation of Beta distribution."""
    n = alpha + beta_
    mean = alpha / n
    std  = math.sqrt(mean * (1.0 - mean) / n) + 1e-9
    z = (threshold - mean) / std
    # Abramowitz & Stegun complementary CDF approximation
    if z > 6:
        return 0.0
    if z < -6:
        return 1.0
    t = 1.0 / (1.0 + 0.2316419 * abs(z))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 +
           t * (-1.821255978 + t * 1.330274429))))
    pdf  = 0.39894228 * math.exp(-0.5 * z * z)
    p_above = pdf * poly
    return p_above if z < 0 else 1.0 - p_above


def _beta_ci(alpha: float, beta_: float, conf: float = 0.95) -> Tuple[float, float]:
    """Approximate 95% credible interval using normal approximation."""
    n    = alpha + beta_
    mean = alpha / n
    std  = math.sqrt(mean * (1.0 - mean) / n) + 1e-9
    z    = 1.96
    return max(0.0, mean - z * std), min(1.0, mean + z * std)


def _load_bayes():
    global _bayes_state, _bayes_loaded
    if _bayes_loaded:
        return
    _bayes_loaded = True
    try:
        if _BAYES_FILE.exists():
            _bayes_state.update(json.loads(_BAYES_FILE.read_text()))
    except Exception:
        pass


def _save_bayes():
    try:
        _BAYES_FILE.parent.mkdir(exist_ok=True)
        _BAYES_FILE.write_text(json.dumps(_bayes_state, indent=2))
    except Exception:
        pass


def _load():
    global _pattern_stats, _loaded
    if _loaded:
        return
    try:
        if _TRACKER_FILE.exists():
            _pattern_stats = json.loads(_TRACKER_FILE.read_text())
    except Exception as e:
        logger.debug(f"elite_tracker load: {e}")
    _loaded = True


def _save():
    try:
        _TRACKER_FILE.parent.mkdir(exist_ok=True)
        _TRACKER_FILE.write_text(json.dumps(_pattern_stats, indent=2))
    except Exception as e:
        logger.debug(f"elite_tracker save: {e}")


def _pattern_key(patterns: List[str], direction: str) -> str:
    """Create a canonical key from a set of patterns + direction."""
    sorted_p = sorted(set(p.upper() for p in patterns if p))
    # Use top 3 patterns for the key to avoid combinatorial explosion
    top3 = sorted_p[:3]
    return f"{direction}|{'_'.join(top3)}" if top3 else f"{direction}|GENERIC"


def record_trade_outcome(patterns: List[str], direction: str,
                          signal_score: float, grade: str, win: bool):
    """Record the outcome of a completed trade for Bayesian + frequentist learning."""
    _load()
    _load_bayes()
    key = _pattern_key(patterns, direction)

    # Frequentist (legacy)
    if key not in _pattern_stats:
        _pattern_stats[key] = {"wins": 0, "losses": 0, "last_seen": ""}
    if win:
        _pattern_stats[key]["wins"] += 1
    else:
        _pattern_stats[key]["losses"] += 1
    _pattern_stats[key]["last_seen"] = datetime.now(IST).strftime("%Y-%m-%d")
    _save()

    # Bayesian update: Beta(alpha+win, beta+loss)
    if key not in _bayes_state:
        _bayes_state[key] = {"alpha": 2.0, "beta_": 2.0, "n": 0}
    bs = _bayes_state[key]
    if win:
        bs["alpha"] += 1
    else:
        bs["beta_"] += 1
    bs["n"] += 1
    _save_bayes()

    mean_wr = _beta_mean(bs["alpha"], bs["beta_"])
    prob    = _prob_wr_above(bs["alpha"], bs["beta_"], _NEUTRAL_WR)
    logger.info(
        f"Elite tracker v2: {key} {'WIN' if win else 'LOSS'} "
        f"n={bs['n']} WR={mean_wr:.1%} P(WR>55%)={prob:.2f}"
    )


def get_elite_score(patterns: List[str], direction: str,
                    signal_score: float) -> Tuple[int, str]:
    """
    Return (score_adj, reason) using Bayesian posterior.
    Only fires when P(WR > 55%) > 0.80 (high confidence) or P(WR < 45%) > 0.80 (avoid).
    Falls back to Wilson CI if Bayesian state not available.
    """
    _load()
    _load_bayes()
    try:
        key = _pattern_key(patterns, direction)

        # ── Bayesian path (preferred) ─────────────────────────────────────
        bs = _bayes_state.get(key)
        if bs and bs.get("n", 0) >= _MIN_SAMPLES:
            alpha  = float(bs["alpha"])
            beta_p = float(bs["beta_"])
            n      = int(bs["n"])
            mean_wr  = _beta_mean(alpha, beta_p)
            prob_good = _prob_wr_above(alpha, beta_p, _NEUTRAL_WR)    # P(WR>55%)
            prob_bad  = 1.0 - _prob_wr_above(alpha, beta_p, 0.45)    # P(WR<45%)
            ci_lo, ci_hi = _beta_ci(alpha, beta_p)

            if prob_good > _CONF_THRESH:
                adj = 10 if prob_good > 0.92 else 6
                return (adj, f"ELITE_BAYES WR={mean_wr:.0%} P>55%={prob_good:.2f} n={n}")
            elif prob_bad > _CONF_THRESH:
                adj = -8 if prob_bad > 0.92 else -5
                return (adj, f"ELITE_BAYES_AVOID WR={mean_wr:.0%} n={n}")
            else:
                return (0, "")   # uncertain — don't adjust

        # ── Frequentist fallback (Wilson CI) ──────────────────────────────
        stats = _pattern_stats.get(key)
        if stats is None:
            return (0, "")
        total = stats["wins"] + stats["losses"]
        if total < _MIN_SAMPLES * 3:   # frequentist needs 3× more samples
            return (0, "")

        wr = stats["wins"] / total
        z = 1.96
        denominator  = 1 + z**2 / total
        centre       = (wr + z**2 / (2 * total)) / denominator
        margin       = (z * math.sqrt(wr * (1 - wr) / total + z**2 / (4 * total**2))) / denominator
        wilson_lower = centre - margin
        delta = wilson_lower - _NEUTRAL_WR

        if delta > 0.12:
            return (8, f"ELITE_WIN WR{wr*100:.0f}%n{total}")
        elif delta > 0.06:
            return (5, f"ELITE_STRONG WR{wr*100:.0f}%n{total}")
        elif delta < -0.12:
            return (-6, f"ELITE_LOW_WR WR{wr*100:.0f}%n{total}")
        elif delta < -0.06:
            return (-4, f"ELITE_WEAK WR{wr*100:.0f}%n{total}")
        return (0, "")

    except Exception as e:
        logger.debug(f"get_elite_score: {e}")
        return (0, "")


def get_stats_summary() -> str:
    """Return a brief summary of the tracked pattern performance."""
    _load()
    _load_bayes()
    if not _pattern_stats and not _bayes_state:
        return "No pattern data yet"
    total_patterns = max(len(_pattern_stats), len(_bayes_state))
    total_trades   = sum(v.get("n", 0) for v in _bayes_state.values()) or sum(
        v["wins"] + v["losses"] for v in _pattern_stats.values())
    conv = get_convergence_report()
    return (f"{total_patterns} patterns | {total_trades} trades | "
            f"converged_good={conv['converged_good']} "
            f"converged_bad={conv['converged_bad']} "
            f"uncertain={conv['uncertain']}")


def get_convergence_report() -> dict:
    """Return Bayesian convergence statistics across all tracked patterns."""
    _load_bayes()
    converged_good = 0
    converged_bad  = 0
    uncertain      = 0
    top_patterns   = []

    for key, bs in _bayes_state.items():
        n = bs.get("n", 0)
        if n < _MIN_SAMPLES:
            continue
        alpha  = float(bs["alpha"])
        beta_p = float(bs["beta_"])
        mean_wr   = _beta_mean(alpha, beta_p)
        prob_good = _prob_wr_above(alpha, beta_p, _NEUTRAL_WR)
        prob_bad  = 1.0 - _prob_wr_above(alpha, beta_p, 0.45)

        if prob_good > _CONF_THRESH:
            converged_good += 1
            top_patterns.append({"key": key, "mean_wr": mean_wr, "n": n, "prob": prob_good})
        elif prob_bad > _CONF_THRESH:
            converged_bad += 1
        else:
            uncertain += 1

    top_patterns.sort(key=lambda x: x["prob"], reverse=True)
    avg_n = (sum(v.get("n", 0) for v in _bayes_state.values()) / max(len(_bayes_state), 1))

    return {
        "total_patterns":     len(_bayes_state),
        "converged_good":     converged_good,
        "converged_bad":      converged_bad,
        "uncertain":          uncertain,
        "avg_n_per_pattern":  round(avg_n, 1),
        "top_patterns":       top_patterns[:5],
    }
