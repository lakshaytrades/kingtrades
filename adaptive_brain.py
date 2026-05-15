"""
adaptive_brain.py — US Momentum Alpaca AI Bot
Intraday Adaptive Intelligence Engine

Learns and adapts IN REAL-TIME during the trading session — not just EOD.
Every closed trade immediately updates the brain's view of what's working.

Adaptation rules (intraday):
  - 2 consecutive losses        → raise min_score +5, reduce size 20%
  - 3 consecutive wins          → confidence boost, maintain score
  - Day P&L < -1%               → go defensive (min_score 95, size 50%)
  - Day P&L > +2%               → lock 50% gains, reduce risk
  - SPY ADX < 15                → pure chop, pause all new entries
  - VIX > 30 and rising         → high fear, skip all but A+ setups
  - Pattern failing > 60%       → disable pattern until EOD reset
  - Pattern winning > 70%       → boost that pattern's weight +0.3

EOD (self_learning cycle) handles multi-day adaptation.
This handles the same-day fast loop.
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

BRAIN_STATE_FILE = Path("data/adaptive_brain_state.json")
MIN_SCORE_FLOOR  = 72.0    # Never go below this (DOW Wednesday/Tuesday floor)
MIN_SCORE_CEIL   = 92.0    # Never above this (reachable ceiling)
DEFAULT_SCORE    = 78.0    # Day-start score (matches Friday DOW_MIN_SCORE)


@dataclass
class TradeOutcome:
    symbol:      str
    direction:   str
    pattern:     str
    entry_score: float
    pnl:         float
    win:         bool
    regime:      str
    session:     str
    timestamp:   str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = format_ist_timestamp()


@dataclass
class BrainState:
    date:                str   = ""
    consecutive_wins:    int   = 0
    consecutive_losses:  int   = 0
    day_pnl:             float = 0.0
    day_capital:         float = 0.0
    trades_today:        int   = 0
    wins_today:          int   = 0
    current_min_score:   float = DEFAULT_SCORE
    size_multiplier:     float = 1.0
    paused:              bool  = False
    pause_reason:        str   = ""
    pattern_today:       Dict[str, List[bool]] = field(default_factory=dict)
    last_update:         str   = ""
    # Pre-calibrated weights from historical backtest warm-up
    pattern_boosts:      Dict[str, float] = field(default_factory=dict)
    disabled_patterns:   set              = field(default_factory=set)


class AdaptiveBrain:
    """
    Real-time intraday adaptation engine.
    Call record_trade() after every closed position.
    Call get_min_score() before every scan cycle.
    Call check_market_health() once per minute.
    """

    def __init__(self, signal_gen=None):
        self._signal_gen = signal_gen
        self._state      = self._load_or_init()
        self._vix_history: List[float] = []

    # ── PUBLIC API ────────────────────────────────────────────────────────────

    def set_signal_gen(self, signal_gen) -> None:
        self._signal_gen = signal_gen

    def record_trade(self, outcome: TradeOutcome) -> str:
        """
        Call immediately when a position closes.
        Updates streaks, pattern weights, and score threshold instantly.
        Returns a brief adaptation message for the log/Telegram.
        """
        s = self._state
        s.trades_today += 1
        s.day_pnl      += outcome.pnl
        s.last_update   = format_ist_timestamp()

        if outcome.win:
            s.wins_today       += 1
            s.consecutive_wins += 1
            s.consecutive_losses = 0
        else:
            s.consecutive_losses += 1
            s.consecutive_wins   = 0

        # Track pattern performance intraday
        pat = outcome.pattern or "UNKNOWN"
        s.pattern_today.setdefault(pat, []).append(outcome.win)

        # Adapt immediately
        msg = self._adapt(outcome)
        self._push_to_signal_gen()
        self._save()

        logger.info(f"[{format_ist_timestamp()}] AdaptiveBrain: {msg}")
        return msg

    def check_market_health(
        self,
        spy_adx:     float = 0.0,
        vix:         float = 0.0,
        spy_change:  float = 0.0,
    ) -> Dict:
        """
        Call once per minute. Returns dict with:
          {"healthy": bool, "reason": str, "score_override": float or None}
        """
        s = self._state

        # VIX spike detection
        if vix > 0:
            self._vix_history.append(vix)
            if len(self._vix_history) > 10:
                self._vix_history.pop(0)

        # Pure chop — ADX too low
        if spy_adx > 0 and spy_adx < 15:
            s.paused      = True
            s.pause_reason = f"SPY ADX {spy_adx:.0f} < 15 — pure chop, no entries"
            self._save()
            return {"healthy": False, "reason": s.pause_reason, "score_override": None}

        # Fear spike — VIX > 30 and rising
        if vix > 30 and len(self._vix_history) >= 3:
            vix_rising = self._vix_history[-1] > self._vix_history[-3]
            if vix_rising:
                override = min(self._state.current_min_score + 5, MIN_SCORE_CEIL)
                return {
                    "healthy":        True,
                    "reason":         f"VIX {vix:.0f} rising — A+ only",
                    "score_override": override,
                }

        # Day P&L checks
        if s.day_capital > 0:
            day_pct = (s.day_pnl / s.day_capital) * 100
            if day_pct <= -2.0:
                s.paused      = True
                s.pause_reason = f"Day P&L {day_pct:.1f}% — daily loss limit hit"
                self._save()
                return {"healthy": False, "reason": s.pause_reason, "score_override": None}

        # Resume if previously paused by ADX (ADX recovered)
        if s.paused and "ADX" in s.pause_reason and spy_adx >= 20:
            s.paused      = False
            s.pause_reason = ""
            self._save()
            logger.info(f"[{format_ist_timestamp()}] AdaptiveBrain: ADX recovered to {spy_adx:.0f} — resumed")

        return {"healthy": not s.paused, "reason": s.pause_reason, "score_override": None}

    def get_min_score(self) -> float:
        return self._state.current_min_score

    def get_size_multiplier(self) -> float:
        return self._state.size_multiplier

    def is_paused(self) -> bool:
        return self._state.paused

    def set_day_capital(self, capital: float) -> None:
        self._state.day_capital = capital

    def get_status(self) -> str:
        s = self._state
        wr = round(s.wins_today / max(s.trades_today, 1) * 100)
        return (
            f"AdaptiveBrain | Score={s.current_min_score:.0f} | "
            f"Size={s.size_multiplier:.1f}x | "
            f"WR={wr}% ({s.wins_today}/{s.trades_today}) | "
            f"P&L=${s.day_pnl:+.0f} | "
            f"Streak={'W'+str(s.consecutive_wins) if s.consecutive_wins else 'L'+str(s.consecutive_losses)} | "
            f"{'PAUSED: '+s.pause_reason if s.paused else 'ACTIVE'}"
        )

    def reset_day(self) -> None:
        """Call at market open to reset daily counters."""
        today = str(date.today())
        self._state = BrainState(
            date               = today,
            current_min_score  = DEFAULT_SCORE,
            size_multiplier    = 1.0,
        )
        self._save()
        logger.info(f"[{format_ist_timestamp()}] AdaptiveBrain: day reset — score={DEFAULT_SCORE}")

    # ── HISTORICAL WARM-UP ────────────────────────────────────────────────────

    def warm_up_from_history(self) -> bool:
        """
        Pre-calibrate pattern weights from historical backtest results.
        Returns True if warm-up succeeded, False if no backtest data available.
        """
        try:
            from historical_backtester import HistoricalBacktester
            bt = HistoricalBacktester()
            results = bt.load_results()
            if results is None:
                logger.info("AdaptiveBrain: no backtest data for warm-up")
                return False

            # Pre-set pattern weights based on historical win rates
            for pattern, stats in results.pattern_stats.items():
                wr     = stats.get("win_rate", 50.0)
                trades = stats.get("trades", 0)
                if trades < 10:
                    continue  # Not enough data
                if wr > 65:
                    self._state.pattern_boosts[pattern] = min(wr / 50.0, 1.5)
                elif wr < 40:
                    self._state.disabled_patterns.add(pattern)

            # Adjust min_score based on overall system win rate
            overall_wr = results.win_rate
            if overall_wr >= 60:
                self._state.current_min_score = max(
                    MIN_SCORE_FLOOR, self._state.current_min_score - 2
                )
            elif overall_wr < 50:
                self._state.current_min_score = min(
                    MIN_SCORE_CEIL, self._state.current_min_score + 3
                )

            logger.info(
                f"[{format_ist_timestamp()}] AdaptiveBrain warm-up: "
                f"win_rate={overall_wr:.1f}% from {results.total_trades} historical trades | "
                f"min_score adjusted to {self._state.current_min_score:.0f}"
            )
            return True
        except Exception as e:
            logger.debug(f"AdaptiveBrain warm-up failed: {e}")
            return False

    # ── ADAPTATION LOGIC ─────────────────────────────────────────────────────

    def _adapt(self, outcome: TradeOutcome) -> str:
        s   = self._state
        msg = []

        # ── Streak-based score adaptation ─────────────────────────────────
        if s.consecutive_losses >= 3:
            # 3 losses in a row — raise bar hard, reduce size
            new_score = min(s.current_min_score + 4.0, MIN_SCORE_CEIL)
            new_size  = max(s.size_multiplier * 0.7, 0.5)
            if new_score != s.current_min_score or new_size != s.size_multiplier:
                s.current_min_score = new_score
                s.size_multiplier   = new_size
                msg.append(f"3 losses streak → score↑{new_score:.0f} size↓{new_size:.1f}x")

        elif s.consecutive_losses == 2:
            # 2 losses — raise score, hold size
            new_score = min(s.current_min_score + 2.0, MIN_SCORE_CEIL)
            if new_score != s.current_min_score:
                s.current_min_score = new_score
                msg.append(f"2 loss streak → score↑{new_score:.0f}")

        elif s.consecutive_wins >= 5:
            # 5+ wins — confidence, soften score and restore size
            new_score = max(s.current_min_score - 3.0, MIN_SCORE_FLOOR)
            new_size  = min(s.size_multiplier * 1.15, 1.5)
            s.current_min_score = new_score
            s.size_multiplier   = new_size
            msg.append(f"5 win streak → score↓{new_score:.0f} size↑{new_size:.1f}x")

        elif s.consecutive_wins >= 3:
            # 3 wins — restore score toward default, increase size
            new_score = max(s.current_min_score - 2.0, DEFAULT_SCORE)
            new_size  = min(s.size_multiplier * 1.1, 1.3)
            if new_score != s.current_min_score or new_size != s.size_multiplier:
                s.current_min_score = new_score
                s.size_multiplier   = new_size
                msg.append(f"3 win streak → score↓{new_score:.0f} size↑{new_size:.1f}x")

        elif s.consecutive_wins >= 1:
            # Any win after a loss streak — start recovering immediately
            new_score = max(s.current_min_score - 3.0, DEFAULT_SCORE)
            if new_score != s.current_min_score:
                s.current_min_score = new_score
                msg.append(f"win after losses → score↓{new_score:.0f} (recovering)")

        # ── Day P&L protection ────────────────────────────────────────────
        if s.day_capital > 0:
            day_pct = (s.day_pnl / s.day_capital) * 100

            if day_pct <= -1.5:
                s.current_min_score = MIN_SCORE_CEIL  # Max tightness
                s.size_multiplier   = 0.5
                msg.append(f"Day P&L {day_pct:.1f}% → defensive mode score={MIN_SCORE_CEIL:.0f} size=0.5x")

            elif day_pct <= -1.0:
                s.current_min_score = max(s.current_min_score, DEFAULT_SCORE + 5.0)
                s.size_multiplier   = max(s.size_multiplier * 0.7, 0.5)
                msg.append(f"Day P&L {day_pct:.1f}% → raising bar score≥{s.current_min_score:.0f}")

            elif day_pct >= 2.0:
                # Locking profit — reduce risk
                s.size_multiplier = min(s.size_multiplier, 0.7)
                msg.append(f"Day P&L +{day_pct:.1f}% target hit → locking gains size=0.7x")

        # ── Pattern-level adaptation ──────────────────────────────────────
        pat = outcome.pattern or "UNKNOWN"
        results = self._state.pattern_today.get(pat, [])
        if len(results) >= 3:
            pat_wr = sum(results) / len(results)
            if pat_wr < 0.35 and not outcome.win:
                # Pattern failing badly intraday — disable until EOD
                if self._signal_gen and hasattr(self._signal_gen, '_learner') and self._signal_gen._learner:
                    self._signal_gen._learner.config.pattern_weights[pat] = 0.0
                    msg.append(f"Pattern {pat} WR={pat_wr:.0%} → disabled today")
            elif pat_wr > 0.75 and outcome.win:
                # Pattern crushing it — boost weight
                if self._signal_gen and hasattr(self._signal_gen, '_learner') and self._signal_gen._learner:
                    cur = self._signal_gen._learner.config.pattern_weights.get(pat, 1.0)
                    self._signal_gen._learner.config.pattern_weights[pat] = min(cur + 0.2, 1.5)
                    msg.append(f"Pattern {pat} WR={pat_wr:.0%} → boosted")

        return " | ".join(msg) if msg else f"Trade recorded ({'WIN' if outcome.win else 'LOSS'} ${outcome.pnl:+.0f})"

    def _push_to_signal_gen(self) -> None:
        """Push current min_score to signal generator immediately."""
        if self._signal_gen:
            self._signal_gen.min_score = self._state.current_min_score

    # ── PERSISTENCE ──────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            BRAIN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state = self._state
            data = {
                "date":               state.date,
                "consecutive_wins":   state.consecutive_wins,
                "consecutive_losses": state.consecutive_losses,
                "day_pnl":            state.day_pnl,
                "day_capital":        state.day_capital,
                "trades_today":       state.trades_today,
                "wins_today":         state.wins_today,
                "current_min_score":  state.current_min_score,
                "size_multiplier":    state.size_multiplier,
                "paused":             state.paused,
                "pause_reason":       state.pause_reason,
                "pattern_today":      state.pattern_today,
                "last_update":        state.last_update,
                # New fields — serialize set as sorted list for JSON
                "pattern_boosts":     state.pattern_boosts,
                "disabled_patterns":  sorted(list(state.disabled_patterns)),
            }
            BRAIN_STATE_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug(f"AdaptiveBrain save error: {e}")

    def _load_or_init(self) -> BrainState:
        today = str(date.today())
        try:
            if BRAIN_STATE_FILE.exists():
                data = json.loads(BRAIN_STATE_FILE.read_text())
                if data.get("date") == today:
                    # Filter to known fields, then fix set deserialization
                    known = {k: v for k, v in data.items()
                             if k in BrainState.__dataclass_fields__}
                    # disabled_patterns is stored as a list in JSON — restore to set
                    if "disabled_patterns" in known and isinstance(known["disabled_patterns"], list):
                        known["disabled_patterns"] = set(known["disabled_patterns"])
                    s = BrainState(**known)
                    logger.info(f"[{format_ist_timestamp()}] AdaptiveBrain: resumed today's state "
                                f"score={s.current_min_score:.0f} trades={s.trades_today}")
                    return s
        except Exception as e:
            logger.debug(f"AdaptiveBrain load error: {e}")
        return BrainState(date=today, current_min_score=DEFAULT_SCORE)


# ── AUTO-UPDATE (git pull on startup) ─────────────────────────────────────────

def auto_update_code() -> str:
    """
    Pull latest code from git on startup so the bot is always current.
    Safe — only fast-forward merges, never force-pushes.
    """
    try:
        repo_dir = Path(__file__).parent
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "claude/nse-momentum-groww-bot-hvkv9"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout.strip()
        if "Already up to date" in out:
            return "Code: already up to date"
        if result.returncode == 0:
            logger.info(f"[{format_ist_timestamp()}] Auto-update: {out}")
            return f"Code updated: {out[:80]}"
        else:
            logger.warning(f"[{format_ist_timestamp()}] Auto-update failed: {result.stderr.strip()}")
            return f"Update skipped (safe): {result.stderr.strip()[:60]}"
    except Exception as e:
        return f"Auto-update skipped: {e}"


# ── SINGLETON ─────────────────────────────────────────────────────────────────

_brain: Optional[AdaptiveBrain] = None


def get_adaptive_brain(signal_gen=None) -> AdaptiveBrain:
    global _brain
    if _brain is None:
        _brain = AdaptiveBrain(signal_gen)
    elif signal_gen and _brain._signal_gen is None:
        _brain.set_signal_gen(signal_gen)
    return _brain
