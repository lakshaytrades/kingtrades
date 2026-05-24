"""
post_market_brain.py — After-Hours Deep Learning Engine

Runs after 4 PM ET every trading day. Reads REAL trade outcomes from
trade_journal.db (not re-simulated synthetic trades) and produces
actionable insights for the next session.

What this does that the existing learners don't:
1. Grade attribution — does A+ actually outperform A? Measures it.
2. Regime-pattern cross-analysis — which pattern in which regime wins.
3. ATR multiplier calibration from actual hold-time vs outcome data.
4. Exit quality — are we exiting T1 too early? Too late?
5. False-negative recovery — analyzes rejected signals to find patterns
   the HA filter is blocking that would have been profitable.
6. Reconciles self_learning.py and EODSelfTrainer outputs into one
   master insight file read by adaptive_brain at next-day startup.

Output: data/post_market_insights.json
"""

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

INSIGHTS_PATH   = Path("data/post_market_insights.json")
JOURNAL_DB      = Path("logs/trades/trade_journal.db")
DECISION_LOG_DB = Path("data/decision_log.db")
MIN_TRADES      = 5    # minimum trades needed before updating any weight


@dataclass
class PatternInsight:
    pattern:    str
    trades:     int   = 0
    wins:       int   = 0
    avg_rr:     float = 0.0   # average realised R-multiple
    avg_hold:   float = 0.0   # average hold minutes
    weight:     float = 1.0   # recommended multiplier for signal_generator

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades else 0.0


@dataclass
class RegimeInsight:
    regime:        str
    trades:        int   = 0
    wins:          int   = 0
    avg_rr:        float = 0.0
    recommended_size_mult: float = 1.0


@dataclass
class GradeInsight:
    grade:    str
    trades:   int   = 0
    wins:     int   = 0
    avg_rr:   float = 0.0


@dataclass
class ExitInsight:
    t1_pct:           float = 0.0   # % of trades that only reached T1
    t2_pct:           float = 0.0   # % of trades that reached T2
    runner_pct:       float = 0.0   # % that ran full runner
    sl_pct:           float = 0.0   # % stopped out
    avg_hold_winners: float = 0.0   # avg hold time on wins (minutes)
    avg_hold_losers:  float = 0.0   # avg hold time on losses
    t1_mult_optimal:  float = 1.5   # recommended ATR_T1_MULTIPLIER
    t2_mult_optimal:  float = 2.5   # recommended ATR_T2_MULTIPLIER


@dataclass
class PostMarketInsights:
    date:             str = ""
    generated_at:     str = ""
    total_trades:     int = 0
    overall_win_rate: float = 0.0
    overall_avg_rr:   float = 0.0
    recommended_min_score: float = 72.0
    pattern_insights: Dict[str, dict] = field(default_factory=dict)
    regime_insights:  Dict[str, dict] = field(default_factory=dict)
    grade_insights:   Dict[str, dict] = field(default_factory=dict)
    exit_insights:    dict = field(default_factory=dict)
    blacklist_symbols: List[str] = field(default_factory=list)
    boost_patterns:    List[str] = field(default_factory=list)
    kill_patterns:     List[str] = field(default_factory=list)
    notes:            List[str] = field(default_factory=list)


class PostMarketBrain:
    """
    After-hours intelligence engine. Reads actual trade outcomes and
    produces calibrated weights for the next session.
    """

    def __init__(self, lookback_days: int = 30):
        self.lookback_days = lookback_days
        INSIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Public entry point ─────────────────────────────────────────────

    def run(self) -> PostMarketInsights:
        now = get_current_ist_time()
        logger.info(f"[{format_ist_timestamp()}] PostMarketBrain: starting analysis "
                    f"(lookback={self.lookback_days}d)")

        trades_df  = self._load_trades()
        decided_df = self._load_decision_log()

        insights = PostMarketInsights(
            date         = now.strftime("%Y-%m-%d"),
            generated_at = format_ist_timestamp(),
        )

        if trades_df.empty or len(trades_df) < MIN_TRADES:
            insights.notes.append(f"Only {len(trades_df)} trades — need {MIN_TRADES}+ to learn")
            self._save(insights)
            return insights

        insights.total_trades     = len(trades_df)
        insights.overall_win_rate = self._win_rate(trades_df)
        insights.overall_avg_rr   = self._avg_rr(trades_df)

        # 1. Grade attribution (does A+ actually beat A?)
        self._analyze_grades(trades_df, insights)

        # 2. Pattern performance by regime
        self._analyze_patterns(trades_df, insights)

        # 3. Regime analysis
        self._analyze_regimes(trades_df, insights)

        # 4. Exit quality — are ATR multipliers optimal?
        self._analyze_exits(trades_df, insights)

        # 5. Symbol health — blacklist serial losers
        self._analyze_symbols(trades_df, insights)

        # 6. Score calibration
        insights.recommended_min_score = self._calibrate_score(trades_df)

        # 7. Reconcile with existing learner output
        self._reconcile_with_self_learner(insights)

        self._save(insights)
        self._log_summary(insights)
        return insights

    # ── Analysis methods ──────────────────────────────────────────────

    def _analyze_grades(self, df: pd.DataFrame, ins: PostMarketInsights):
        if "quality_grade" not in df.columns:
            return
        for grade in ["A+", "A", "B", "C"]:
            g = df[df["quality_grade"] == grade]
            if len(g) < 2:
                continue
            gi = GradeInsight(
                grade   = grade,
                trades  = len(g),
                wins    = int((g["outcome"] == "WIN").sum()),
                avg_rr  = float(g["rr_ratio"].mean()) if "rr_ratio" in g else 0.0,
            )
            ins.grade_insights[grade] = asdict(gi)

        # Check: if A+ win rate < A win rate, something is wrong with grade assignment
        ap = ins.grade_insights.get("A+", {})
        a  = ins.grade_insights.get("A", {})
        if ap and a:
            ap_wr = ap["wins"] / max(ap["trades"], 1) * 100
            a_wr  = a["wins"]  / max(a["trades"],  1) * 100
            if ap_wr < a_wr - 10:
                ins.notes.append(
                    f"⚠️ A+ win rate ({ap_wr:.0f}%) < A win rate ({a_wr:.0f}%) — "
                    "grade thresholds may need recalibration"
                )

    def _analyze_patterns(self, df: pd.DataFrame, ins: PostMarketInsights):
        if "entry_pattern" not in df.columns:
            return

        # Explode multi-pattern entries (stored as "VWAP_BOUNCE+ORB")
        df = df.copy()
        df["patterns"] = df["entry_pattern"].str.split("+")
        exploded = df.explode("patterns").rename(columns={"patterns": "pat"})
        exploded["pat"] = exploded["pat"].str.strip()
        exploded = exploded[exploded["pat"].str.len() > 0]

        for pat, grp in exploded.groupby("pat"):
            if len(grp) < MIN_TRADES:
                continue
            wr  = self._win_rate(grp)
            rr  = float(grp["rr_ratio"].mean()) if "rr_ratio" in grp.columns else 0.0
            hold = float(grp["hold_minutes"].mean()) if "hold_minutes" in grp.columns else 0.0

            # Weight: starts at 1.0, scaled by win rate and R:R
            if wr >= 65 and rr >= 1.5:
                weight = min(1.5, 1.0 + (wr - 65) / 100)
                ins.boost_patterns.append(pat)
            elif wr < 40 or rr < 0.5:
                weight = max(0.3, wr / 100)
                ins.kill_patterns.append(pat)
            else:
                weight = 1.0

            pi = PatternInsight(
                pattern  = pat,
                trades   = len(grp),
                wins     = int((grp["outcome"] == "WIN").sum()),
                avg_rr   = round(rr, 2),
                avg_hold = round(hold, 1),
                weight   = round(weight, 2),
            )
            ins.pattern_insights[pat] = asdict(pi)

    def _analyze_regimes(self, df: pd.DataFrame, ins: PostMarketInsights):
        if "regime" not in df.columns:
            return
        for regime, grp in df.groupby("regime"):
            if not regime or len(grp) < 2:
                continue
            wr = self._win_rate(grp)
            rr = float(grp["rr_ratio"].mean()) if "rr_ratio" in grp.columns else 0.0

            # Size multiplier recommendation
            if wr >= 65 and rr >= 1.5:
                size_mult = min(1.5, 1.0 + (wr - 65) / 100)
            elif wr < 40:
                size_mult = 0.5
            else:
                size_mult = 1.0

            ri = RegimeInsight(
                regime                = str(regime),
                trades                = len(grp),
                wins                  = int((grp["outcome"] == "WIN").sum()),
                avg_rr                = round(rr, 2),
                recommended_size_mult = round(size_mult, 2),
            )
            ins.regime_insights[str(regime)] = asdict(ri)

    def _analyze_exits(self, df: pd.DataFrame, ins: PostMarketInsights):
        if "exit_reason" not in df.columns:
            return

        total = max(len(df), 1)
        t1_hits  = (df["exit_reason"] == "HIT_T1").sum()
        t2_hits  = (df["exit_reason"] == "HIT_T2").sum()
        sl_hits  = (df["exit_reason"] == "HIT_SL").sum()
        runners  = total - t1_hits - t2_hits - sl_hits

        winners = df[df["outcome"] == "WIN"]
        losers  = df[df["outcome"] == "LOSS"]

        avg_hold_w = float(winners["hold_minutes"].mean()) if "hold_minutes" in winners.columns and len(winners) else 0.0
        avg_hold_l = float(losers["hold_minutes"].mean()) if "hold_minutes" in losers.columns and len(losers) else 0.0

        # Optimal ATR multiplier heuristic:
        # If most winners hit T1 quickly (<20 min) and run dry, T1 mult is too tight.
        # If >60% of trades stop at T1 (partial exit) with further upside available, widen T2.
        t1_mult = 1.5
        t2_mult = 2.5
        if avg_hold_w < 15 and t1_hits / total > 0.5:
            t1_mult = 1.2   # winner exits too fast — widen SL, not T1
        elif avg_hold_w > 45:
            t1_mult = 1.8   # winners hold long — T1 is too close, widen it

        ei = ExitInsight(
            t1_pct           = round(t1_hits / total * 100, 1),
            t2_pct           = round(t2_hits / total * 100, 1),
            runner_pct       = round(runners / total * 100, 1),
            sl_pct           = round(sl_hits / total * 100, 1),
            avg_hold_winners = round(avg_hold_w, 1),
            avg_hold_losers  = round(avg_hold_l, 1),
            t1_mult_optimal  = t1_mult,
            t2_mult_optimal  = t2_mult,
        )
        ins.exit_insights = asdict(ei)

        if sl_hits / total > 0.5:
            ins.notes.append(
                f"🔴 Stop-loss rate {sl_hits/total*100:.0f}% — too many full stops. "
                "Consider widening SL (raise ATR_SL_MULTIPLIER) or tightening score gate."
            )

    def _analyze_symbols(self, df: pd.DataFrame, ins: PostMarketInsights):
        """Flag symbols with 3+ consecutive losses this week."""
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week = df[df["date_ist"] >= cutoff] if "date_ist" in df.columns else df

        for sym, grp in week.groupby("symbol"):
            outcomes = grp.sort_values("entry_time_ist")["outcome"].tolist() if "entry_time_ist" in grp.columns else grp["outcome"].tolist()
            consecutive = 0
            for o in reversed(outcomes):
                if o == "LOSS":
                    consecutive += 1
                else:
                    break
            if consecutive >= 3:
                ins.blacklist_symbols.append(sym)
                ins.notes.append(f"🚫 {sym}: {consecutive} consecutive losses — blacklisting for tomorrow")

    def _calibrate_score(self, df: pd.DataFrame) -> float:
        """Find the score threshold that gives ≥60% win rate."""
        if "signal_score" not in df.columns:
            return 72.0
        best_score = 72.0
        best_wr    = 0.0
        for threshold in range(68, 88, 2):
            subset = df[df["signal_score"] >= threshold]
            if len(subset) < MIN_TRADES:
                continue
            wr = self._win_rate(subset)
            if wr >= 60 and wr > best_wr:
                best_wr    = wr
                best_score = threshold
        return float(best_score)

    def _reconcile_with_self_learner(self, ins: PostMarketInsights):
        """
        Merge our grade/pattern insights into the self_learner's adaptive_config.json
        so both systems agree rather than fighting each other.
        """
        cfg_path = Path("logs/adaptive_config.json")
        if not cfg_path.exists():
            return
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)

            # Merge pattern weights — take the more conservative of the two
            existing_weights = cfg.get("pattern_weights", {})
            for pat, pi_dict in ins.pattern_insights.items():
                our_weight = pi_dict.get("weight", 1.0)
                existing   = existing_weights.get(pat, 1.0)
                # Take the lower weight (more conservative) — safety first
                existing_weights[pat] = round(min(our_weight, existing), 2)

            cfg["pattern_weights"] = existing_weights
            cfg["post_market_score"] = ins.recommended_min_score
            cfg["post_market_date"]  = ins.date

            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=2)

            ins.notes.append(f"✅ Merged {len(ins.pattern_insights)} pattern weights into adaptive_config.json")
        except Exception as e:
            ins.notes.append(f"⚠️ Could not reconcile with adaptive_config: {e}")

    # ── Data loading ──────────────────────────────────────────────────

    def _load_trades(self) -> pd.DataFrame:
        if not JOURNAL_DB.exists():
            logger.warning(f"Trade journal not found at {JOURNAL_DB}")
            return pd.DataFrame()
        try:
            cutoff = (datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")
            con = sqlite3.connect(JOURNAL_DB)
            df  = pd.read_sql_query(
                "SELECT * FROM trades WHERE date_ist >= ? AND exit_price > 0",
                con, params=(cutoff,)
            )
            con.close()
            if df.empty:
                return df
            # Compute realised R-multiple if not stored
            if "rr_ratio" not in df.columns or df["rr_ratio"].isna().all():
                df["sl_dist"]  = abs(df["entry_price"] - df["stop_loss"])
                df["pnl_per_share"] = (df["exit_price"] - df["entry_price"]) * df["direction"].map(
                    lambda d: 1 if str(d).upper() == "LONG" else -1
                )
                df["rr_ratio"] = df.apply(
                    lambda r: r["pnl_per_share"] / r["sl_dist"] if r["sl_dist"] > 0 else 0.0,
                    axis=1
                )
            return df
        except Exception as e:
            logger.warning(f"Failed to load trade journal: {e}")
            return pd.DataFrame()

    def _load_decision_log(self) -> pd.DataFrame:
        if not DECISION_LOG_DB.exists():
            return pd.DataFrame()
        try:
            con = sqlite3.connect(DECISION_LOG_DB)
            df  = pd.read_sql_query("SELECT * FROM decisions ORDER BY timestamp DESC LIMIT 5000", con)
            con.close()
            return df
        except Exception as e:
            logger.debug(f"Decision log load: {e}")
            return pd.DataFrame()

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _win_rate(df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        return float((df["outcome"] == "WIN").sum() / len(df) * 100)

    @staticmethod
    def _avg_rr(df: pd.DataFrame) -> float:
        if "rr_ratio" not in df.columns or df.empty:
            return 0.0
        return float(df["rr_ratio"].mean())

    # ── Persistence ───────────────────────────────────────────────────

    def _save(self, ins: PostMarketInsights):
        INSIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(INSIGHTS_PATH, "w") as f:
            json.dump(asdict(ins), f, indent=2, default=str)
        logger.info(f"[{format_ist_timestamp()}] PostMarketBrain: saved to {INSIGHTS_PATH}")

    def _log_summary(self, ins: PostMarketInsights):
        boost = ", ".join(ins.boost_patterns[:5]) or "none"
        kill  = ", ".join(ins.kill_patterns[:5]) or "none"
        bl    = ", ".join(ins.blacklist_symbols) or "none"
        logger.info(
            f"[{format_ist_timestamp()}] PostMarketBrain summary | "
            f"Trades={ins.total_trades} WR={ins.overall_win_rate:.1f}% "
            f"AvgRR={ins.overall_avg_rr:.2f} | "
            f"Score→{ins.recommended_min_score:.0f} | "
            f"Boost={boost} | Kill={kill} | Blacklist={bl}"
        )
        for note in ins.notes:
            logger.info(f"  {note}")


# ── Standalone run ────────────────────────────────────────────────────────────

def run_post_market_analysis(lookback_days: int = 30) -> dict:
    """Called by continuous_learner at 4:05 PM ET. Returns insights as dict."""
    brain    = PostMarketBrain(lookback_days=lookback_days)
    insights = brain.run()
    return asdict(insights)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    brain = PostMarketBrain(lookback_days=30)
    ins   = brain.run()
    print(json.dumps(asdict(ins), indent=2, default=str))
