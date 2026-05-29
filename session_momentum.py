"""
session_momentum.py — Intraday Session Performance Tracker

Tracks what's working THIS session in real-time.
When the bot is aligned with the market's dominant trend, win rates surge.
When it's fighting the market, even good setups fail.

Session signals:
  - 3+ trades: activate session learning
  - Session WR > 66%: strategy is working — normal parameters
  - Session WR < 33%: market conditions hostile — raise min_score by 5
  - Session WR = 0% after 4 trades: pause 30 min (likely regime mismatch)
  - Streak of 3+ wins: mild size boost (0.1x)
  - Streak of 3+ losses: size reduction (0.25x) + score raise

Reset at market open each day.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from utils import get_current_ist_time

logger = logging.getLogger(__name__)


class SessionMomentum:
    """Tracks intraday session performance and adapts parameters."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._trades: List[Dict] = []
        self._session_date = ""
        self._streak = 0  # positive = win streak, negative = loss streak
        self._pause_until: Optional[datetime] = None

    def _check_date_reset(self):
        today = get_current_ist_time().strftime("%Y-%m-%d")
        if self._session_date != today:
            self.reset()
            self._session_date = today

    def record(self, symbol: str, win: bool, pnl: float = 0.0) -> None:
        self._check_date_reset()
        self._trades.append({"symbol": symbol, "win": win, "pnl": pnl,
                              "time": get_current_ist_time().strftime("%H:%M")})
        if win:
            self._streak = max(0, self._streak) + 1
        else:
            self._streak = min(0, self._streak) - 1

    def get_score_adjustment(self) -> float:
        """
        Returns score threshold adjustment in pts.
        Positive = raise the bar (harder conditions).
        Negative = lower the bar (conditions favorable).
        """
        self._check_date_reset()
        n = len(self._trades)
        if n < 3:
            return 0.0  # not enough data

        wins = sum(1 for t in self._trades if t["win"])
        wr   = wins / n

        if wr >= 0.66:
            return -2.0   # strategy working well — mild ease
        if wr <= 0.33:
            return +5.0   # hostile conditions — raise the bar
        if wr == 0.0 and n >= 4:
            return +8.0   # nothing working — much harder bar
        return 0.0

    def get_size_multiplier(self) -> float:
        """Multiplier on position size based on current session streak."""
        self._check_date_reset()
        if self._streak >= 3:
            return 1.10   # win streak: mild size boost
        if self._streak <= -3:
            return 0.75   # loss streak: size reduction
        if self._streak <= -2:
            return 0.85   # 2 consecutive losses: slight reduction
        return 1.0

    def should_pause(self) -> bool:
        """
        True during the 30-minute recovery window after 4 consecutive losses.
        Automatically clears when the window expires — not a permanent block.
        """
        self._check_date_reset()
        now = get_current_ist_time()

        # Still within an active pause window?
        if self._pause_until is not None:
            if now < self._pause_until:
                remaining = int((self._pause_until - now).total_seconds() / 60)
                logger.debug(f"[SessionMomentum] Pause active — {remaining} min remaining")
                return True
            else:
                self._pause_until = None  # window expired, resume

        n = len(self._trades)
        if n < 4:
            return False
        recent = self._trades[-4:]
        wins = sum(1 for t in recent if t["win"])
        if wins == 0:
            # Trigger 30-minute pause — not a session-ending block
            self._pause_until = now + timedelta(minutes=30)
            logger.info(
                f"[SessionMomentum] 4 consecutive losses — 30-min pause until "
                f"{self._pause_until.strftime('%H:%M')}"
            )
            return True
        return False

    def get_status(self) -> Dict:
        self._check_date_reset()
        n = len(self._trades)
        wins = sum(1 for t in self._trades if t["win"])
        total_pnl = sum(t.get("pnl", 0) for t in self._trades)
        return {
            "n": n,
            "wins": wins,
            "wr_pct": round(wins / n * 100, 1) if n > 0 else 0,
            "streak": self._streak,
            "total_pnl": round(total_pnl, 2),
            "score_adj": self.get_score_adjustment(),
            "size_mult": self.get_size_multiplier(),
            "pause": self.should_pause(),
        }


_session_instance: SessionMomentum = None


def get_session_momentum() -> SessionMomentum:
    global _session_instance
    if _session_instance is None:
        _session_instance = SessionMomentum()
    return _session_instance
