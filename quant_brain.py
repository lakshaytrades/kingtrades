"""
quant_brain.py — NSE Momentum Groww AI Bot
500-PhD Meta-Intelligence: 8 Specialist Councils + Bayesian Optimizer + Knowledge Base

This is the centerpiece AI meta-brain of the trading system. It runs continuously,
surfacing quantitative insights that no single module can see alone.

8 Specialist Councils:
  1. AlphaQuant         — pattern win-rate analysis, score threshold optimization
  2. RiskOfficer        — loss streak detection, time-of-day clustering, risk efficiency
  3. MLResearcher       — ML/RL prediction accuracy tracking
  4. MicrostructureAnalyst — session performance, holding time quality
  5. MacroEconomist     — regime performance, VIX sensitivity
  6. BehavioralExpert   — overtrading detection, revenge trading patterns
  7. PerformanceAttributor — P&L attribution by pattern/session/regime/grade/direction
  8. BayesianOptimizer  — Optuna-based parameter optimization

Knowledge Base: SQLite at data/quant_brain.db
Output: findings, hypotheses, optimized params (data/adaptive_params.json)
Telegram: /brain /quant /council commands
"""

import json
import logging
import math
import sqlite3
import threading
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, date
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Optional, Tuple, Any

from utils import get_current_ist_time, format_ist_timestamp
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ── Paths ────────────────────────────────────────────────────────────────────
BRAIN_DB          = Path("data/quant_brain.db")
JOURNAL_DB        = Path("logs/trades/trade_journal.db")
ADAPTIVE_PARAMS   = Path("data/adaptive_params.json")

BRAIN_DB.parent.mkdir(parents=True, exist_ok=True)
ADAPTIVE_PARAMS.parent.mkdir(parents=True, exist_ok=True)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class Hypothesis:
    council:        str
    description:    str
    condition_text: str
    prediction:     str
    status:         str = "PENDING"
    sample_n:       int = 0
    wins:           int = 0
    losses:         int = 0
    expected_edge:  float = 0.0
    measured_edge:  float = 0.0


# ── Database Schema ───────────────────────────────────────────────────────────

def _init_brain_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hypotheses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            council         TEXT,
            description     TEXT,
            condition_text  TEXT,
            prediction      TEXT,
            status          TEXT DEFAULT 'PENDING',
            sample_n        INTEGER DEFAULT 0,
            wins            INTEGER DEFAULT 0,
            losses          INTEGER DEFAULT 0,
            expected_edge   REAL DEFAULT 0.0,
            measured_edge   REAL DEFAULT 0.0,
            created         TEXT,
            resolved        TEXT
        );

        CREATE TABLE IF NOT EXISTS knowledge (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            description      TEXT,
            category         TEXT,
            implementation   TEXT,
            measured_impact  REAL DEFAULT 0.0,
            confidence       REAL DEFAULT 0.0,
            sample_n         INTEGER DEFAULT 0,
            date_adopted     TEXT,
            is_active        INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS daily_insights (
            date                TEXT PRIMARY KEY,
            best_pattern        TEXT,
            worst_pattern       TEXT,
            best_session        TEXT,
            worst_session       TEXT,
            best_regime         TEXT,
            win_rate            REAL,
            avg_r_multiple      REAL,
            sharpe_today        REAL,
            findings_json       TEXT,
            recommendations_json TEXT
        );

        CREATE TABLE IF NOT EXISTS pattern_stats (
            pattern         TEXT PRIMARY KEY,
            total_trades    INTEGER DEFAULT 0,
            wins            INTEGER DEFAULT 0,
            total_pnl       REAL DEFAULT 0.0,
            avg_r_multiple  REAL DEFAULT 0.0,
            best_regime     TEXT,
            best_session    TEXT,
            last_updated    TEXT
        );

        CREATE TABLE IF NOT EXISTS param_history (
            ts              TEXT,
            param_name      TEXT,
            old_value       REAL,
            new_value       REAL,
            reason          TEXT,
            measured_impact REAL DEFAULT 0.0
        );
    """)
    conn.commit()


def _get_brain_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(BRAIN_DB))
    conn.row_factory = sqlite3.Row
    _init_brain_db(conn)
    return conn


# ── Trade Loader ──────────────────────────────────────────────────────────────

def _load_trades(lookback_days: int = 30) -> List[dict]:
    """
    Load closed trades from trade_journal.db.
    Returns [] gracefully if DB is missing or empty.
    """
    if not JOURNAL_DB.exists():
        return []
    try:
        cutoff = (datetime.now(IST) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(str(JOURNAL_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE date_ist >= ? AND exit_price > 0 ORDER BY entry_time_ist ASC",
            (cutoff,)
        ).fetchall()
        conn.close()
        result = []
        for row in rows:
            d = dict(row)
            d["outcome"]      = d.get("outcome", "")
            d["net_pnl"]      = float(d.get("net_pnl", 0) or 0)
            d["signal_score"] = float(d.get("signal_score", 0) or 0)
            d["rr_ratio"]     = float(d.get("rr_ratio", 0) or 0)
            d["hold_minutes"] = float(d.get("hold_minutes", 0) or 0)
            result.append(d)
        return result
    except Exception as e:
        logger.warning(f"[QuantBrain] trade load failed: {e}")
        return []


# ── Shared Helpers ────────────────────────────────────────────────────────────

def _win_rate(trades: List[dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get("outcome") == "WIN")
    return wins / len(trades) * 100


def _sharpe(trades: List[dict]) -> float:
    """Trade-level Sharpe (annualised, capped at +-10)."""
    if len(trades) < 2:
        return 0.0
    pnls = [t.get("net_pnl", 0) for t in trades]
    try:
        m = mean(pnls)
        s = stdev(pnls)
        if s < 1e-6:
            return 0.0
        sh = (m / s) * math.sqrt(252)
        return max(-10.0, min(sh, 10.0))
    except Exception:
        return 0.0


def _avg_r(trades: List[dict]) -> float:
    if not trades:
        return 0.0
    rrs = [t.get("rr_ratio", 0) for t in trades]
    return mean(rrs) if rrs else 0.0


# ===============================================================================
# 8 SPECIALIST COUNCILS
# ===============================================================================

class AlphaQuant:
    """
    Pattern win-rate analyst. Hunts alpha: identifies which patterns
    are generating edge and which are destroying it.
    """
    name = "AlphaQuant"

    def analyze(self, trades: List[dict]) -> Tuple[List[str], List[Hypothesis]]:
        findings: List[str] = []
        hypotheses: List[Hypothesis] = []

        if not trades:
            return ["No trades to analyze."], []

        # Explode multi-pattern entries (stored as "VWAP_BOUNCE+ORB")
        pattern_trades: Dict[str, List[dict]] = defaultdict(list)
        for t in trades:
            pats = str(t.get("entry_pattern", "") or "").split("+")
            for p in pats:
                p = p.strip()
                if p:
                    pattern_trades[p].append(t)

        wins_by_score   = [t for t in trades if t.get("outcome") == "WIN"]
        losses_by_score = [t for t in trades if t.get("outcome") == "LOSS"]

        # Score gap: do winners score higher?
        if wins_by_score and losses_by_score:
            win_score_avg  = mean(t.get("signal_score", 0) for t in wins_by_score)
            loss_score_avg = mean(t.get("signal_score", 0) for t in losses_by_score)
            if win_score_avg - loss_score_avg >= 5:
                findings.append(
                    f"Winners score avg {win_score_avg:.1f} vs losers {loss_score_avg:.1f} "
                    f"(+{win_score_avg - loss_score_avg:.1f}) — raise min_score threshold"
                )
                hypotheses.append(Hypothesis(
                    council        = self.name,
                    description    = "Higher signal_score correlates with better outcomes",
                    condition_text = f"signal_score >= {win_score_avg:.0f}",
                    prediction     = "Win rate improves by 5-10% if score threshold raised",
                    expected_edge  = 0.07,
                ))

        # Per-pattern analysis
        for pattern, ptrades in pattern_trades.items():
            if len(ptrades) < 5:
                continue
            wr = _win_rate(ptrades)
            avg_pnl = mean(t.get("net_pnl", 0) for t in ptrades)

            if wr < 40:
                findings.append(
                    f"Disable {pattern}: WR={wr:.1f}% over {len(ptrades)} trades "
                    f"(avg P&L: ${avg_pnl:+.0f})"
                )
                hypotheses.append(Hypothesis(
                    council        = self.name,
                    description    = f"Pattern {pattern} has sub-40% WR",
                    condition_text = f"entry_pattern contains {pattern}",
                    prediction     = "Filtering out this pattern improves overall WR",
                    expected_edge  = -wr / 100,
                ))
            elif wr > 65:
                findings.append(
                    f"Boost {pattern}: WR={wr:.1f}% over {len(ptrades)} trades "
                    f"(avg P&L: ${avg_pnl:+.0f})"
                )
                hypotheses.append(Hypothesis(
                    council        = self.name,
                    description    = f"Pattern {pattern} has above-65% WR",
                    condition_text = f"entry_pattern contains {pattern}",
                    prediction     = "Increase position sizing on this pattern by 20%",
                    expected_edge  = wr / 100 - 0.5,
                ))

        if not findings:
            findings.append("AlphaQuant: All patterns within normal WR range (40-65%).")

        return findings, hypotheses


class RiskOfficer:
    """
    Consecutive loss streak detection, time-of-day clustering, risk efficiency.
    """
    name = "RiskOfficer"

    def analyze(self, trades: List[dict]) -> List[str]:
        findings: List[str] = []
        if not trades:
            return ["No trades to analyze."]

        # Consecutive loss streak
        max_streak = cur_streak = 0
        for t in trades:
            if t.get("outcome") == "LOSS":
                cur_streak += 1
                max_streak = max(max_streak, cur_streak)
            else:
                cur_streak = 0

        if max_streak >= 4:
            findings.append(
                f"Max consecutive loss streak: {max_streak} — "
                "Reduce size to 50% after 2nd consecutive loss"
            )
        elif max_streak >= 2:
            findings.append(
                f"Streak alert: {max_streak} consecutive losses observed — monitor closely"
            )

        # Hour-of-day loss clustering
        hour_losses: Dict[int, int] = defaultdict(int)
        hour_total:  Dict[int, int] = defaultdict(int)
        for t in trades:
            entry_time = str(t.get("entry_time_ist", "") or "")
            try:
                time_part = entry_time.split(" ")[-1] if " " in entry_time else entry_time
                hour = int(time_part[:2])
                hour_total[hour] += 1
                if t.get("outcome") == "LOSS":
                    hour_losses[hour] += 1
            except Exception:
                pass

        for hour, losses in hour_losses.items():
            if losses >= 3:
                total = hour_total.get(hour, losses)
                loss_wr = losses / total * 100 if total else 100
                findings.append(
                    f"Hour {hour:02d}:00 IST: {losses} losses / {total} trades "
                    f"({loss_wr:.0f}% loss rate) — avoid new entries in this window"
                )

        # Portfolio efficiency
        total_pnl  = sum(t.get("net_pnl", 0) for t in trades)
        total_risk = sum(
            abs(t.get("entry_price", 0) - t.get("stop_loss", 0)) * t.get("entry_qty", 1)
            for t in trades
            if t.get("entry_price") and t.get("stop_loss")
        )

        if total_risk > 0:
            efficiency = total_pnl / total_risk
            if efficiency < 0:
                findings.append(
                    f"Portfolio risk efficiency is negative ({efficiency:.3f}) — "
                    "total P&L negative relative to total risk taken"
                )
            else:
                findings.append(f"Risk efficiency: {efficiency:.3f} (P&L / total risk)")

        if not findings:
            findings.append("RiskOfficer: Risk metrics within acceptable bounds.")

        return findings


class MLResearcher:
    """ML prediction accuracy and RL effectiveness tracker."""
    name = "MLResearcher"

    def analyze(self, trades: List[dict]) -> List[str]:
        findings: List[str] = []
        if not trades:
            return ["No trades to analyze."]

        # ML prediction accuracy
        ml_trades = [t for t in trades if t.get("ml_prediction") is not None]
        if ml_trades:
            correct = 0
            for t in ml_trades:
                pred    = str(t.get("ml_prediction", "")).upper()
                outcome = str(t.get("outcome", "")).upper()
                if (pred in ("BUY", "LONG", "WIN") and outcome == "WIN") or \
                   (pred in ("SELL", "SHORT", "LOSS") and outcome == "LOSS"):
                    correct += 1
            accuracy = correct / len(ml_trades) * 100
            findings.append(
                f"ML accuracy: {accuracy:.1f}% over {len(ml_trades)} trades"
            )
            if accuracy < 52:
                findings.append(
                    "ML accuracy < 52% — consider disabling ML filter or retraining"
                )

        # RL-guided trade performance
        rl_trades = [t for t in trades if t.get("rl_guided") or t.get("rl_action")]
        if rl_trades:
            rl_wr = _win_rate(rl_trades)
            findings.append(
                f"RL-guided trades: {len(rl_trades)} trades, WR={rl_wr:.1f}%"
            )
            if rl_wr < 50:
                findings.append(
                    "RL-guided WR below 50% — RL agent may need more training data"
                )

        if not findings:
            findings.append(
                "MLResearcher: No ML/RL tagged trades found in this window."
            )

        return findings


class MicrostructureAnalyst:
    """Session performance and holding time quality analysis."""
    name = "MicrostructureAnalyst"

    def analyze(self, trades: List[dict]) -> List[str]:
        findings: List[str] = []
        if not trades:
            return ["No trades to analyze."]

        # Session breakdown
        session_trades: Dict[str, List[dict]] = defaultdict(list)
        for t in trades:
            session = str(t.get("session", "UNKNOWN") or "UNKNOWN").upper()
            session_trades[session].append(t)

        best_session = worst_session = ""
        best_wr      = -1.0
        worst_wr     = 101.0

        for session, strades in session_trades.items():
            if len(strades) < 3:
                continue
            wr      = _win_rate(strades)
            avg_pnl = mean(t.get("net_pnl", 0) for t in strades)
            findings.append(
                f"Session {session}: {len(strades)} trades, WR={wr:.1f}%, "
                f"avg P&L=${avg_pnl:+.0f}"
            )
            if wr > best_wr:
                best_wr, best_session = wr, session
            if wr < worst_wr:
                worst_wr, worst_session = wr, session

        if best_session:
            findings.append(f"Best session: {best_session} ({best_wr:.1f}% WR)")
        if worst_session and worst_session != best_session:
            findings.append(f"Worst session: {worst_session} ({worst_wr:.1f}% WR)")

        # Holding time: do losers hold too long?
        winners = [t for t in trades if t.get("outcome") == "WIN"]
        losers  = [t for t in trades if t.get("outcome") == "LOSS"]

        if winners and losers:
            avg_win_hold  = mean(t.get("hold_minutes", 0) for t in winners)
            avg_loss_hold = mean(t.get("hold_minutes", 0) for t in losers)
            if avg_loss_hold > avg_win_hold * 1.5:
                findings.append(
                    f"Cut losers faster: winners held {avg_win_hold:.0f}m avg, "
                    f"losers held {avg_loss_hold:.0f}m avg "
                    f"({avg_loss_hold / max(avg_win_hold, 1):.1f}x longer)"
                )

        if not findings:
            findings.append("MicrostructureAnalyst: Session stats look balanced.")

        return findings


class MacroEconomist:
    """Regime performance and VIX sensitivity analysis."""
    name = "MacroEconomist"

    def analyze(self, trades: List[dict]) -> List[str]:
        findings: List[str] = []
        if not trades:
            return ["No trades to analyze."]

        # Regime breakdown
        regime_trades: Dict[str, List[dict]] = defaultdict(list)
        for t in trades:
            regime = str(t.get("regime", "UNKNOWN") or "UNKNOWN").upper()
            regime_trades[regime].append(t)

        best_regime  = worst_regime = ""
        best_wr      = -1.0
        worst_wr     = 101.0

        for regime, rtrades in regime_trades.items():
            if len(rtrades) < 3:
                continue
            wr      = _win_rate(rtrades)
            avg_pnl = mean(t.get("net_pnl", 0) for t in rtrades)
            findings.append(
                f"Regime {regime}: {len(rtrades)} trades, WR={wr:.1f}%, "
                f"avg P&L=${avg_pnl:+.0f}"
            )
            if wr > best_wr:
                best_wr, best_regime = wr, regime
            if wr < worst_wr:
                worst_wr, worst_regime = wr, regime

        if best_regime:
            findings.append(f"Best regime: {best_regime} ({best_wr:.1f}% WR)")
        if worst_regime and worst_regime != best_regime:
            findings.append(f"Worst regime: {worst_regime} ({worst_wr:.1f}% WR)")

        # VIX sensitivity
        high_vix_trades = [
            t for t in trades if float(t.get("vix", 0) or 0) > 30
        ]
        if high_vix_trades:
            vix_wr = _win_rate(high_vix_trades)
            if vix_wr < 40:
                findings.append(
                    f"High-VIX (>30) trades: {len(high_vix_trades)} trades, "
                    f"WR={vix_wr:.1f}% — reduce size in high-VIX environment"
                )
            else:
                findings.append(
                    f"High-VIX trades: {len(high_vix_trades)} trades, WR={vix_wr:.1f}%"
                )

        if not findings:
            findings.append("MacroEconomist: Regime data not yet available in trades.")

        return findings


class BehavioralExpert:
    """Overtrading detection and revenge trading pattern identification."""
    name = "BehavioralExpert"

    def analyze(self, trades: List[dict]) -> List[str]:
        findings: List[str] = []
        if not trades:
            return ["No trades to analyze."]

        # Group by date
        date_trades: Dict[str, List[dict]] = defaultdict(list)
        for t in trades:
            day = str(t.get("date_ist", "") or "")[:10]
            if day:
                date_trades[day].append(t)

        # Overtrading per day
        for day, dtrades in date_trades.items():
            if len(dtrades) > 20:
                findings.append(
                    f"Possible overtrading on {day}: {len(dtrades)} trades in one session"
                )

        # Revenge trading: 3+ losses followed by a larger position size
        outcomes = [(t.get("outcome", ""), t.get("entry_qty", 0) or 0) for t in trades]
        loss_streak = 0
        for i, (outcome, qty) in enumerate(outcomes):
            if outcome == "LOSS":
                loss_streak += 1
            else:
                if loss_streak >= 3 and i > 0:
                    prev_qty = outcomes[i - 1][1]
                    if qty > prev_qty * 1.2:
                        findings.append(
                            f"Revenge trading pattern detected: {loss_streak} losses "
                            f"then size jumped from {prev_qty} to {qty} shares"
                        )
                loss_streak = 0

        # Average daily frequency
        total_days = len(date_trades)
        if total_days > 0:
            avg_per_day = len(trades) / total_days
            if avg_per_day > 15:
                findings.append(
                    f"High avg trade frequency: {avg_per_day:.1f} trades/day — "
                    "quality over quantity"
                )

        if not findings:
            findings.append(
                "BehavioralExpert: No overtrading or revenge patterns detected."
            )

        return findings


class PerformanceAttributor:
    """P&L attribution by pattern, session, regime, grade, and direction."""
    name = "PerformanceAttributor"

    def analyze(self, trades: List[dict]) -> List[str]:
        findings: List[str] = []
        if not trades:
            return ["No trades to analyze."]

        def _attr(group_key: str, label: str):
            groups: Dict[str, float] = defaultdict(float)
            counts: Dict[str, int]   = defaultdict(int)
            for t in trades:
                k = str(t.get(group_key, "UNKNOWN") or "UNKNOWN")
                groups[k] += t.get("net_pnl", 0)
                counts[k] += 1
            if not groups:
                return
            best_k  = max(groups, key=groups.get)
            worst_k = min(groups, key=groups.get)
            findings.append(
                f"  {label}: Best={best_k} (${groups[best_k]:+.0f}) | "
                f"Worst={worst_k} (${groups[worst_k]:+.0f})"
            )

        findings.append("P&L Attribution:")
        _attr("entry_pattern", "Pattern ")
        _attr("session",       "Session ")
        _attr("regime",        "Regime  ")
        _attr("direction",     "Direction")

        # Grade attribution
        grades: Dict[str, float] = defaultdict(float)
        grade_counts: Dict[str, int] = defaultdict(int)
        for t in trades:
            g = str(t.get("quality_grade", "") or "")
            if g:
                grades[g] += t.get("net_pnl", 0)
                grade_counts[g] += 1
        if grades:
            for g, pnl in sorted(grades.items()):
                n = grade_counts[g]
                findings.append(f"  Grade {g}: {n} trades, total P&L=${pnl:+.0f}")

        return findings


class BayesianOptimizer:
    """
    Optuna-based parameter optimization.
    Falls back to grid search if Optuna is unavailable.
    Saves results to data/adaptive_params.json.
    """
    name = "BayesianOptimizer"

    PARAM_BOUNDS = {
        "min_score": (60.0, 85.0),
        "atr_sl":    (0.5,  2.5),
        "atr_t1":    (1.0,  4.0),
        "risk_pct":  (0.3,  1.2),
    }

    def _objective(self, params: dict, trades: List[dict]) -> float:
        """Simulate filtered trades and compute Sharpe ratio."""
        if not trades:
            return -999.0
        min_score = params.get("min_score", 70)
        filtered  = [t for t in trades if t.get("signal_score", 0) >= min_score]
        if len(filtered) < 5:
            return -999.0
        return _sharpe(filtered)

    def optimize(self, trades: List[dict]) -> dict:
        """Run Optuna optimization; fall back to grid search if unavailable."""
        if len(trades) < 10:
            return {}

        best_params: dict = {}

        # Try Optuna first
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)

            def _trial(trial):
                params = {
                    "min_score": trial.suggest_float("min_score", 60.0, 85.0, step=1.0),
                    "atr_sl":    trial.suggest_float("atr_sl",    0.5,  2.5,  step=0.1),
                    "atr_t1":    trial.suggest_float("atr_t1",    1.0,  4.0,  step=0.1),
                    "risk_pct":  trial.suggest_float("risk_pct",  0.3,  1.2,  step=0.05),
                }
                return self._objective(params, trades)

            study = optuna.create_study(direction="maximize")
            study.optimize(_trial, n_trials=40, show_progress_bar=False)
            best_params = study.best_params
            logger.info(
                f"[QuantBrain] Optuna best: {best_params} | "
                f"Sharpe={study.best_value:.3f}"
            )

        except ImportError:
            logger.info("[QuantBrain] Optuna not available — falling back to grid search")
            best_sharpe = -999.0
            for min_score in [65, 68, 70, 72, 75, 78, 80]:
                for atr_sl in [0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5]:
                    for atr_t1 in [1.5, 2.0, 2.5, 3.0, 3.5]:
                        for risk_pct in [0.3, 0.5, 0.7, 1.0]:
                            params = {
                                "min_score": min_score,
                                "atr_sl":    atr_sl,
                                "atr_t1":    atr_t1,
                                "risk_pct":  risk_pct,
                            }
                            sh = self._objective(params, trades)
                            if sh > best_sharpe:
                                best_sharpe = sh
                                best_params = params
            logger.info(
                f"[QuantBrain] Grid search best: {best_params} | Sharpe={best_sharpe:.3f}"
            )
        except Exception as e:
            logger.warning(f"[QuantBrain] Optimizer error: {e}")
            return {}

        if not best_params:
            return {}

        # Merge with existing adaptive_params.json (never wipe existing keys)
        existing: dict = {}
        try:
            if ADAPTIVE_PARAMS.exists():
                existing = json.loads(ADAPTIVE_PARAMS.read_text())
        except Exception:
            pass

        existing.update({
            "date":      get_current_ist_time().strftime("%Y-%m-%d"),
            "min_score": round(float(best_params.get("min_score", 70)), 1),
            "atr_sl":    round(float(best_params.get("atr_sl",    1.5)), 2),
            "atr_t1":    round(float(best_params.get("atr_t1",    2.5)), 2),
            "risk_pct":  round(float(best_params.get("risk_pct",  0.5)), 2),
            "source":    "quant_brain_bayesian",
        })

        try:
            ADAPTIVE_PARAMS.write_text(json.dumps(existing, indent=2))
            logger.info(f"[QuantBrain] Saved optimized params to {ADAPTIVE_PARAMS}")
        except Exception as e:
            logger.warning(f"[QuantBrain] Could not save params: {e}")

        # Log to param_history table
        try:
            conn = _get_brain_conn()
            ts = format_ist_timestamp()
            for k, v in best_params.items():
                old_v = float(existing.get(k, 0))
                conn.execute(
                    "INSERT INTO param_history (ts, param_name, old_value, new_value, reason) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ts, k, old_v, round(float(v), 3), "BayesianOptimizer auto-tune")
                )
            conn.commit()
            conn.close()
        except Exception:
            pass

        return existing


# ===============================================================================
# QUANT BRAIN — Main Orchestrator
# ===============================================================================

class QuantBrain:
    """
    The meta-intelligence engine. Coordinates all 8 specialist councils,
    manages the knowledge base, and provides Telegram integration.
    """

    def __init__(self, modules: dict = None):
        self._modules = modules or {}
        # Councils
        self._alpha_quant   = AlphaQuant()
        self._risk_officer  = RiskOfficer()
        self._ml_researcher = MLResearcher()
        self._micro_analyst = MicrostructureAnalyst()
        self._macro_econ    = MacroEconomist()
        self._behavioral    = BehavioralExpert()
        self._attributor    = PerformanceAttributor()
        self._bay_optimizer = BayesianOptimizer()

    def _load_trades(self, lookback_days: int = 30) -> List[dict]:
        return _load_trades(lookback_days)

    def pulse_check(self) -> List[str]:
        """
        Quick anomaly detection. Called every 30 min during market hours.
        Checks: recent 5-trade loss streak, daily trade count > 20, daily P&L < -$500.
        Returns list of anomaly strings (empty = all clear).
        """
        anomalies: List[str] = []
        try:
            trades = self._load_trades(lookback_days=1)
            if not trades:
                return []

            # Recent 5-trade window loss streak
            recent = trades[-5:]
            if len(recent) == 5 and all(t.get("outcome") == "LOSS" for t in recent):
                anomalies.append(
                    "Last 5 trades all LOSS — circuit breaker review recommended"
                )

            # Daily trade count
            today = get_current_ist_time().strftime("%Y-%m-%d")
            today_trades = [t for t in trades if str(t.get("date_ist", ""))[:10] == today]
            if len(today_trades) > 20:
                anomalies.append(
                    f"Overtrading: {len(today_trades)} trades today — quality > quantity"
                )

            # Daily P&L threshold
            daily_pnl = sum(t.get("net_pnl", 0) for t in today_trades)
            if daily_pnl < -500:
                anomalies.append(
                    f"Daily P&L alert: ${daily_pnl:+.0f} — check daily loss limit"
                )

        except Exception as e:
            logger.warning(f"[QuantBrain] pulse_check error: {e}")

        return anomalies

    def deep_analysis(self, lookback_days: int = 30) -> dict:
        """
        Full 8-council analysis.
        Returns insight dict: findings, recommendations, win_rate, sharpe,
        best_pattern, worst_pattern.
        """
        trades = self._load_trades(lookback_days)
        all_findings: List[str] = []

        # Run each council
        try:
            aq_findings, hypotheses = self._alpha_quant.analyze(trades)
            all_findings.extend(aq_findings)
            self._save_hypotheses(hypotheses)
        except Exception as e:
            logger.warning(f"[QuantBrain] AlphaQuant failed: {e}")

        for council in [
            self._risk_officer,
            self._ml_researcher,
            self._micro_analyst,
            self._macro_econ,
            self._behavioral,
        ]:
            try:
                council_findings = council.analyze(trades)
                all_findings.extend(council_findings)
            except Exception as e:
                logger.warning(f"[QuantBrain] {council.name} failed: {e}")

        try:
            attr_findings = self._attributor.analyze(trades)
            all_findings.extend(attr_findings)
        except Exception as e:
            logger.warning(f"[QuantBrain] PerformanceAttributor failed: {e}")

        # Extract actionable recommendations
        action_keywords = [
            "disable", "boost", "raise", "reduce", "cut", "consider",
            "avoid", "increase", "recommend", "faster", "review", "retraining"
        ]
        recommendations = [
            f for f in all_findings
            if any(kw in f.lower() for kw in action_keywords)
        ]

        # Overall stats
        overall_wr     = _win_rate(trades)
        overall_sharpe = _sharpe(trades)
        avg_r          = _avg_r(trades)

        # Best/worst patterns
        pattern_wr: Dict[str, List[int]] = defaultdict(lambda: [0, 0])  # [wins, total]
        for t in trades:
            for p in str(t.get("entry_pattern", "") or "").split("+"):
                p = p.strip()
                if not p:
                    continue
                pattern_wr[p][1] += 1
                if t.get("outcome") == "WIN":
                    pattern_wr[p][0] += 1

        best_pattern  = ""
        worst_pattern = ""
        best_wr_val   = -1.0
        worst_wr_val  = 101.0
        for pat, (w, n) in pattern_wr.items():
            if n < 3:
                continue
            wr = w / n * 100
            if wr > best_wr_val:
                best_wr_val, best_pattern = wr, pat
            if wr < worst_wr_val:
                worst_wr_val, worst_pattern = wr, pat

        insight = {
            "date":            get_current_ist_time().strftime("%Y-%m-%d"),
            "total_trades":    len(trades),
            "win_rate":        round(overall_wr, 1),
            "sharpe":          round(overall_sharpe, 3),
            "avg_r_multiple":  round(avg_r, 2),
            "best_pattern":    best_pattern,
            "worst_pattern":   worst_pattern,
            "findings":        all_findings,
            "recommendations": recommendations,
        }

        # Persist to daily_insights table
        try:
            conn = _get_brain_conn()
            conn.execute("""
                INSERT OR REPLACE INTO daily_insights
                (date, best_pattern, worst_pattern, best_session, worst_session,
                 best_regime, win_rate, avg_r_multiple, sharpe_today,
                 findings_json, recommendations_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                insight["date"],
                best_pattern,
                worst_pattern,
                "",
                "",
                "",
                round(overall_wr, 2),
                round(avg_r, 2),
                round(overall_sharpe, 3),
                json.dumps(all_findings),
                json.dumps(recommendations),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[QuantBrain] daily_insights save failed: {e}")

        logger.info(
            f"[QuantBrain] Deep analysis complete: {len(trades)} trades, "
            f"WR={overall_wr:.1f}%, Sharpe={overall_sharpe:.3f}, "
            f"{len(all_findings)} findings"
        )

        return insight

    def optimize_parameters(self, lookback_days: int = 30) -> dict:
        """
        Bayesian optimization → saves to adaptive_params.json → returns best params.
        """
        trades = self._load_trades(lookback_days)
        logger.info(
            f"[QuantBrain] Parameter optimization starting: {len(trades)} trades"
        )
        result = self._bay_optimizer.optimize(trades)
        if result:
            logger.info(
                f"[QuantBrain] Optimization complete: "
                f"min_score={result.get('min_score')}, "
                f"atr_sl={result.get('atr_sl')}, "
                f"risk_pct={result.get('risk_pct')}"
            )
        else:
            logger.info("[QuantBrain] Optimization returned no results (insufficient data)")
        return result

    def generate_weekly_report(self) -> str:
        """
        Telegram-formatted weekly report with all council findings + optimized params.
        """
        insight = self.deep_analysis(lookback_days=7)
        params  = {}
        try:
            if ADAPTIVE_PARAMS.exists():
                params = json.loads(ADAPTIVE_PARAMS.read_text())
        except Exception:
            pass

        sep   = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        lines = [
            f"🧠 <b>QuantBrain Weekly Report</b> — {insight.get('date', '')}",
            sep,
            f"  Trades:      <code>{insight.get('total_trades', 0)}</code>",
            f"  Win Rate:    <code>{insight.get('win_rate', 0):.1f}%</code>",
            f"  Sharpe:      <code>{insight.get('sharpe', 0):.3f}</code>",
            f"  Avg R:       <code>{insight.get('avg_r_multiple', 0):.2f}</code>",
            f"  Best Pat:    <code>{insight.get('best_pattern', '—')}</code>",
            f"  Worst Pat:   <code>{insight.get('worst_pattern', '—')}</code>",
            sep,
            "  <b>Top Council Findings:</b>",
        ]

        findings = insight.get("findings", [])
        for f in findings[:8]:
            lines.append(f"  • {f[:90]}")

        if params:
            lines += [
                sep,
                "  <b>Bayesian Optimized Params:</b>",
                f"  min_score: <code>{params.get('min_score', '—')}</code>",
                f"  atr_sl:    <code>{params.get('atr_sl', '—')}</code>",
                f"  atr_t1:    <code>{params.get('atr_t1', '—')}</code>",
                f"  risk_pct:  <code>{params.get('risk_pct', '—')}%</code>",
                f"  (updated: {params.get('date', 'N/A')})",
            ]

        recs = insight.get("recommendations", [])
        if recs:
            lines += [sep, "  <b>Action Items:</b>"]
            for r in recs[:5]:
                lines.append(f"  > {r[:90]}")

        lines += [
            sep,
            f"  🤖 8 councils | Bayesian optimizer | {format_ist_timestamp()}",
        ]

        return "\n".join(lines)

    def run_once(self):
        """
        Single pass:
        - Pulse check during market hours (9:15-15:30 IST)
        - Deep analysis post-market (17:00 IST)
        - Parameter optimization at 21:00 IST
        - Weekly report Sunday 20:00 IST
        """
        now     = get_current_ist_time()
        hour    = now.hour
        minute  = now.minute
        weekday = now.weekday()  # 0=Mon, 6=Sun

        alerter = self._modules.get("alerter")

        # Market hours pulse check
        if (hour == 9 and minute >= 15) or (10 <= hour <= 15) or (hour == 15 and minute <= 30):
            anomalies = self.pulse_check()
            if anomalies and alerter:
                try:
                    alerter.send_text("🧠 Brain:\n" + "\n".join(anomalies[:4]))
                except Exception as e:
                    logger.warning(f"[QuantBrain] pulse alerter failed: {e}")

        # Post-market deep analysis at 17:00
        elif hour == 17 and minute < 30:
            insight = self.deep_analysis()
            if alerter and insight.get("findings"):
                try:
                    alerter.send_text(
                        "🧠 Brain Post-Market:\n" +
                        "\n".join(insight["findings"][:5])
                    )
                except Exception as e:
                    logger.warning(f"[QuantBrain] post-market alert failed: {e}")

        # Parameter optimization at 21:00 IST
        elif hour == 21 and minute < 5:
            self.optimize_parameters()

        # Weekly report on Sunday at 20:00 IST
        elif weekday == 6 and hour == 20 and minute < 5:
            report = self.generate_weekly_report()
            if alerter:
                try:
                    alerter.send_text(report)
                except Exception as e:
                    logger.warning(f"[QuantBrain] weekly report send failed: {e}")

    def start_background(self):
        """Start QuantBrain in a daemon thread with 30-minute cadence."""
        import time as _time

        def _loop():
            logger.info("[QuantBrain] Background thread started (30-min cadence)")
            while True:
                try:
                    self.run_once()
                except Exception as e:
                    logger.warning(f"[QuantBrain] run_once error: {e}")
                _time.sleep(1800)  # 30 minutes

        t = threading.Thread(target=_loop, daemon=True, name="QuantBrain")
        t.start()
        return t

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _save_hypotheses(self, hypotheses: List[Hypothesis]):
        if not hypotheses:
            return
        try:
            conn = _get_brain_conn()
            ts   = format_ist_timestamp()
            for h in hypotheses:
                conn.execute("""
                    INSERT INTO hypotheses
                    (council, description, condition_text, prediction, status,
                     sample_n, wins, losses, expected_edge, measured_edge, created)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    h.council, h.description, h.condition_text, h.prediction,
                    h.status, h.sample_n, h.wins, h.losses,
                    h.expected_edge, h.measured_edge, ts,
                ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[QuantBrain] _save_hypotheses failed: {e}")

    def adopt_knowledge(self, description: str, category: str,
                        implementation: str, measured_impact: float,
                        confidence: float, sample_n: int = 0):
        """Persist a validated insight to the permanent knowledge base."""
        try:
            conn = _get_brain_conn()
            conn.execute("""
                INSERT INTO knowledge
                (description, category, implementation, measured_impact,
                 confidence, sample_n, date_adopted, is_active)
                VALUES (?,?,?,?,?,?,?,1)
            """, (
                description, category, implementation,
                measured_impact, confidence, sample_n,
                get_current_ist_time().strftime("%Y-%m-%d"),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[QuantBrain] adopt_knowledge failed: {e}")


# ── Standalone entry point ────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    brain = QuantBrain()

    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "deep"

    if cmd == "pulse":
        anomalies = brain.pulse_check()
        print("\n".join(anomalies) if anomalies else "All clear.")

    elif cmd == "optimize":
        result = brain.optimize_parameters()
        print(json.dumps(result, indent=2))

    elif cmd == "report":
        print(brain.generate_weekly_report())

    else:
        insight = brain.deep_analysis()
        print(f"\nWin Rate: {insight['win_rate']}%")
        print(f"Sharpe:   {insight['sharpe']}")
        print(f"Findings ({len(insight['findings'])}):")
        for f in insight["findings"]:
            print(f"  {f}")


if __name__ == "__main__":
    main()
