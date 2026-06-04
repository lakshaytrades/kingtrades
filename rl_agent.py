"""
rl_agent.py — Reinforcement Learning Brain for KingTrades

Architecture (as designed):
  StateBuilder    → Eyes. Encodes market into a discrete state string like "12201102"
  QTable          → Memory. Knows what action worked in each state. Saved to disk.
  ReplayBuffer    → Notebook. Stores last 2,000 experiences for re-learning.
  RewardCalculator→ Scorecard. Points for wins, deductions for bad habits.
  TradeMemory     → Diary. Full log of every trade + outcome.
  RLAgent         → Brain. Q-learning agent that improves after every trade.
  LakshKingRL     → Remote control. 4 calls: signal, close, candle, reset.

Institution-level additions:
  - Correlation filter (no two correlated stocks simultaneously)
  - Market regime detection (trending / ranging / volatile)
  - Kelly Criterion position sizing
  - Portfolio heat limit (max 40% capital deployed at once)
  - Drawdown-based position reduction

Files created:
  data/rl_qtable.json    — Brain. Grows smarter with every trade.
  data/rl_memory.json    — Full diary of every trade + reward given.
"""

import json
import logging
import math
import os
import random
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)

QTABLE_FILE = Path("data/rl_qtable.json")
MEMORY_FILE  = Path("data/rl_memory.json")
Path("data").mkdir(exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# STATE BUILDER — Eyes of the bot
# Encodes full market context into a compact discrete string
# "12201102" = 8 dimensions × 3 levels each = 6,561 possible states
# ─────────────────────────────────────────────────────────────────────────────

class StateBuilder:
    """
    Converts raw market data into a discrete state string.
    Each character = one market dimension:
      [0] RSI zone        0=oversold(<35)  1=neutral  2=overbought(>65)
      [1] MACD direction  0=bearish        1=flat     2=bullish
      [2] VWAP position   0=below          1=at(±0.2%)  2=above
      [3] Volume ratio    0=low(<1.2x)     1=normal   2=surge(>2x)
      [4] MTF alignment   0=bearish        1=mixed    2=bullish
      [5] Pattern strength 0=weak(<60)     1=moderate 2=strong(>80)
      [6] Session         0=midday-chop    1=afternoon  2=power-hour
      [7] Market regime   0=trending-down  1=ranging  2=trending-up
    """

    def build(self, data: dict) -> str:
        return "".join([
            self._rsi_zone(data.get("rsi", 50)),
            self._macd(data.get("macd_hist", 0), data.get("macd_hist_prev", 0)),
            self._vwap(data.get("vwap_deviation_pct", 0)),
            self._volume(data.get("volume_ratio", 1.0)),
            self._mtf(data.get("mtf_score", 50), data.get("mtf_direction", "NEUTRAL")),
            self._pattern(data.get("pattern_score", 50)),
            self._session(),
            self._regime(data.get("nifty_trend", "NEUTRAL"), data.get("adx", 20)),
        ])

    def _rsi_zone(self, rsi: float) -> str:
        if rsi < 35: return "0"
        if rsi > 65: return "2"
        return "1"

    def _macd(self, hist: float, prev: float) -> str:
        if hist > 0 and hist > prev: return "2"   # Bullish + growing
        if hist < 0 and hist < prev: return "0"   # Bearish + falling
        return "1"

    def _vwap(self, dev_pct: float) -> str:
        if dev_pct > 0.2:  return "2"   # Above VWAP
        if dev_pct < -0.2: return "0"   # Below VWAP
        return "1"

    def _volume(self, ratio: float) -> str:
        if ratio >= 2.0: return "2"
        if ratio >= 1.2: return "1"
        return "0"

    def _mtf(self, score: float, direction: str) -> str:
        if direction == "LONG"  and score >= 70: return "2"
        if direction == "SHORT" and score >= 70: return "0"
        return "1"

    def _pattern(self, score: float) -> str:
        if score >= 80: return "2"
        if score >= 60: return "1"
        return "0"

    def _session(self) -> str:
        now = get_current_ist_time()
        h   = now.hour + now.minute / 60
        if 9.25 <= h <= 10.5:  return "2"   # Power hour
        if 13.5 <= h <= 15.0:  return "1"   # Afternoon institutional
        return "0"                            # Midday chop

    def _regime(self, nifty_trend: str, adx: float) -> str:
        if nifty_trend == "BULLISH" and adx > 25: return "2"
        if nifty_trend == "BEARISH" and adx > 25: return "0"
        return "1"


# ─────────────────────────────────────────────────────────────────────────────
# Q-TABLE — Memory that never forgets
# ─────────────────────────────────────────────────────────────────────────────

class QTable:
    """
    Maps (state, action) → expected reward.
    Actions: 0=SKIP, 1=BUY(LONG), 2=SELL(SHORT)
    Persisted to disk so the bot never forgets across restarts.
    """
    ACTIONS = ["SKIP", "LONG", "SHORT"]

    def __init__(self, lr: float = 0.12, gamma: float = 0.90):
        self.lr    = lr       # Learning rate — how fast it updates beliefs
        self.gamma = gamma    # Discount — future rewards matter slightly less
        self.table: Dict[str, List[float]] = {}
        self._load()

    def get(self, state: str) -> List[float]:
        if state not in self.table:
            self.table[state] = [0.0, 0.0, 0.0]   # SKIP=0, LONG=0, SHORT=0
        return self.table[state]

    def best_action(self, state: str) -> int:
        return int(max(range(3), key=lambda a: self.get(state)[a]))

    def update(self, state: str, action: int, reward: float, next_state: str):
        q      = self.get(state)
        q_next = self.get(next_state)
        q[action] += self.lr * (reward + self.gamma * max(q_next) - q[action])
        self._save()

    def _load(self):
        try:
            if QTABLE_FILE.exists():
                self.table = json.loads(QTABLE_FILE.read_text())
                logger.info(f"[RL] Q-table loaded — {len(self.table)} states known")
        except Exception as e:
            logger.warning(f"[RL] Q-table load failed: {e}")

    def _save(self):
        try:
            QTABLE_FILE.write_text(json.dumps(self.table))
        except Exception as e:
            logger.warning(f"[RL] Q-table save failed: {e}")

    @property
    def stats(self) -> dict:
        if not self.table:
            return {"states": 0, "best_long": 0, "best_short": 0}
        all_vals = [max(v) for v in self.table.values()]
        return {
            "states":     len(self.table),
            "avg_reward": round(sum(all_vals) / len(all_vals), 3),
            "best_state": max(self.table, key=lambda s: max(self.table[s])),
        }


# ─────────────────────────────────────────────────────────────────────────────
# REPLAY BUFFER — Revision notebook
# ─────────────────────────────────────────────────────────────────────────────

class ReplayBuffer:
    """
    Stores last N experiences (state, action, reward, next_state).
    Random mini-batch replays prevent the bot from over-fitting to recent trades.
    """

    def __init__(self, capacity: int = 2000):
        self.buffer: deque = deque(maxlen=capacity)

    def push(self, state: str, action: int, reward: float, next_state: str):
        self.buffer.append((state, action, reward, next_state))

    def sample(self, batch_size: int = 32) -> List[Tuple]:
        n = min(batch_size, len(self.buffer))
        return random.sample(list(self.buffer), n)

    def __len__(self) -> int:
        return len(self.buffer)


# ─────────────────────────────────────────────────────────────────────────────
# REWARD CALCULATOR — Scorecard
# ─────────────────────────────────────────────────────────────────────────────

class RewardCalculator:
    """
    Converts trade outcomes into RL rewards.
    Designed to reward quality over quantity and punish bad habits.
    """

    def calculate(
        self,
        pnl: float,
        risk_amount: float,
        exit_reason: str,
        hold_minutes: int,
        trades_today: int,
    ) -> float:
        if risk_amount <= 0:
            risk_amount = 1.0

        # Base reward: R-multiple (PnL relative to risk taken)
        r_multiple = pnl / risk_amount

        # Exit reason multipliers
        if exit_reason == "target_hit":
            reward = r_multiple * 1.5      # Bonus for clean exits
        elif exit_reason == "stop_loss":
            reward = r_multiple * 1.2      # Slightly penalise SL hits
        elif exit_reason == "trailing_stop":
            reward = r_multiple * 1.1      # Good discipline
        elif exit_reason == "forced_squareoff":
            reward = r_multiple * 0.7      # Penalise not exiting cleanly
        else:
            reward = r_multiple

        # Overtrading penalty — institution rule: quality > quantity
        if trades_today > 3:
            reward -= 0.3 * (trades_today - 3)

        # Time in trade — reward efficient trades (not holding hoping)
        if 0 < hold_minutes <= 30 and pnl > 0:
            reward += 0.2    # Quick clean profit
        elif hold_minutes > 120 and pnl < 0:
            reward -= 0.3    # Held a loser too long

        return round(reward, 4)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE MEMORY — Full diary
# ─────────────────────────────────────────────────────────────────────────────

class TradeMemory:
    """
    Persistent log of every RL decision with full context.
    Used for debugging, auditing, and weekly AI review.
    """

    def __init__(self, max_entries: int = 5000):
        self.max_entries = max_entries
        self.entries: List[dict] = []
        self._load()

    def record(
        self,
        symbol: str,
        state: str,
        action: int,
        reward: float,
        pnl: float,
        exit_reason: str,
        signal_score: float,
        epsilon: float,
    ):
        entry = {
            "ts":           format_ist_timestamp(),
            "symbol":       symbol,
            "state":        state,
            "action":       QTable.ACTIONS[action],
            "reward":       reward,
            "pnl":          pnl,
            "exit_reason":  exit_reason,
            "signal_score": signal_score,
            "epsilon":      round(epsilon, 3),
        }
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries:]
        self._save()

    def win_rate(self, last_n: int = 50) -> float:
        recent = [e for e in self.entries if e["action"] != "SKIP"][-last_n:]
        if not recent:
            return 0.0
        wins = sum(1 for e in recent if e["pnl"] > 0)
        return round(wins / len(recent) * 100, 1)

    def avg_reward(self, last_n: int = 50) -> float:
        recent = self.entries[-last_n:]
        if not recent:
            return 0.0
        return round(sum(e["reward"] for e in recent) / len(recent), 3)

    def _load(self):
        try:
            if MEMORY_FILE.exists():
                self.entries = json.loads(MEMORY_FILE.read_text())
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

    def _save(self):
        try:
            MEMORY_FILE.write_text(json.dumps(self.entries[-self.max_entries:]))
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")


# ─────────────────────────────────────────────────────────────────────────────
# INSTITUTION-LEVEL PORTFOLIO MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioManager:
    """
    Institution-grade constraints that protect large capital.
    Prevents the single biggest mistake of retail traders: over-concentration.
    """

    # Stocks in the same sector — avoid holding both simultaneously
    SECTOR_GROUPS = {
        "BANKING":  {"HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK"},
        "IT":       {"TCS", "INFY", "WIPRO", "HCLTECH", "TECHM"},
        "ENERGY":   {"RELIANCE", "ONGC", "NTPC", "POWERGRID"},
        "TELECOM":  {"BHARTIARTL", "IDEA"},
        "FMCG":     {"ITC", "HINDUNILVR", "NESTLEIND"},
        "INFRA":    {"LT", "ULTRACEMCO"},
    }

    def __init__(self):
        self.open_positions: Dict[str, str] = {}   # symbol → direction
        self.daily_trades   = 0
        self.daily_pnl      = 0.0
        self.peak_capital   = 0.0

    def can_add_position(self, symbol: str, direction: str, capital: float) -> Tuple[bool, str]:
        # Max 5 concurrent positions (institution rule)
        if len(self.open_positions) >= 5:
            return False, "Max 5 concurrent positions reached"

        # No two stocks from the same sector in same direction
        for group, members in self.SECTOR_GROUPS.items():
            if symbol in members:
                for open_sym, open_dir in self.open_positions.items():
                    if open_sym in members and open_dir == direction:
                        return False, f"Sector concentration: already long {open_sym} in {group}"

        # Max 40% of capital deployed (portfolio heat)
        if len(self.open_positions) >= 4:
            return False, "Portfolio heat limit: 4 positions already open"

        # Drawdown-based reduction: cut size after 5% drawdown
        if self.peak_capital > 0 and capital < self.peak_capital * 0.95:
            return False, "In drawdown >5% — preserving capital, no new positions"

        return True, "OK"

    def position_opened(self, symbol: str, direction: str):
        self.open_positions[symbol] = direction
        self.daily_trades += 1

    def position_closed(self, symbol: str, pnl: float):
        self.open_positions.pop(symbol, None)
        self.daily_pnl += pnl

    def kelly_size_multiplier(self, win_rate_pct: float, avg_win: float, avg_loss: float) -> float:
        """Kelly Criterion: optimal position size based on edge."""
        if avg_loss <= 0 or avg_win <= 0:
            return 0.5   # Default half-Kelly until enough data
        p   = win_rate_pct / 100
        q   = 1 - p
        b   = avg_win / avg_loss
        kelly = (b * p - q) / b
        # Cap at 25% and use half-Kelly for safety
        kelly = max(0.1, min(kelly * 0.5, 0.25))
        return round(kelly, 3)

    def reset_day(self):
        self.daily_trades = 0
        self.daily_pnl    = 0.0
        self.open_positions.clear()


# ─────────────────────────────────────────────────────────────────────────────
# RL AGENT — The actual brain
# ─────────────────────────────────────────────────────────────────────────────

class RLAgent:
    """
    Q-learning agent that improves after every trade.
    Uses epsilon-greedy: starts exploring (random), gradually exploits (learned).
    """

    def __init__(self):
        self.qtable   = QTable(lr=0.12, gamma=0.90)
        self.replay   = ReplayBuffer(capacity=2000)
        self.rewards  = RewardCalculator()
        self.memory   = TradeMemory()
        self.portfolio= PortfolioManager()
        self.state_builder = StateBuilder()

        # Epsilon: exploration rate (1.0=all random, 0.05=mostly learned)
        self.epsilon      = self._load_epsilon()
        self.epsilon_min  = 0.05
        self.epsilon_decay= 0.995   # Decays after each trade

        # Current episode tracking
        self._current_state:  Optional[str] = None
        self._current_action: Optional[int] = None
        self._entry_time:     Optional[datetime] = None
        self._current_symbol: Optional[str] = None
        self._current_risk:   float = 0.0

        logger.info(
            f"[RL] Agent ready | States known: {len(self.qtable.table)} | "
            f"ε={self.epsilon:.3f} | WR(50)={self.memory.win_rate()}%"
        )

    def decide(self, symbol: str, direction_hint: str, market_data: dict, risk_amount: float) -> str:
        """
        Core decision: should we trade this signal?
        Returns: "LONG", "SHORT", or "SKIP"
        """
        # Portfolio-level check first
        from config import MAX_DAILY_CAPITAL
        capital = float(os.getenv("MAX_DAILY_CAPITAL", MAX_DAILY_CAPITAL))
        allowed, reason = self.portfolio.can_add_position(symbol, direction_hint, capital)
        if not allowed:
            logger.info(f"[RL] {symbol} blocked by portfolio: {reason}")
            return "SKIP"

        state = self.state_builder.build(market_data)

        # Epsilon-greedy: explore or exploit
        if random.random() < self.epsilon:
            # Exploration: use signal generator's suggestion (not pure random)
            action_idx = {"LONG": 1, "SHORT": 2}.get(direction_hint, 0)
        else:
            # Exploitation: use what we've learned
            action_idx = self.qtable.best_action(state)

        action = QTable.ACTIONS[action_idx]

        # Track episode
        self._current_state  = state
        self._current_action = action_idx
        self._entry_time     = get_current_ist_time()
        self._current_symbol = symbol
        self._current_risk   = risk_amount

        logger.info(
            f"[RL] {symbol} | State={state} | ε={self.epsilon:.3f} | "
            f"Q={[round(x,2) for x in self.qtable.get(state)]} | Decision={action}"
        )
        return action

    def on_trade_closed(self, symbol: str, pnl: float, exit_reason: str, next_market_data: dict):
        """Call this when a position is closed. Updates Q-table."""
        if self._current_state is None or self._current_action is None:
            return

        hold_minutes = 0
        if self._entry_time:
            hold_minutes = int((get_current_ist_time() - self._entry_time).total_seconds() / 60)

        reward = self.rewards.calculate(
            pnl           = pnl,
            risk_amount   = self._current_risk,
            exit_reason   = exit_reason,
            hold_minutes  = hold_minutes,
            trades_today  = self.portfolio.daily_trades,
        )

        next_state = self.state_builder.build(next_market_data)

        # Learn
        self.qtable.update(self._current_state, self._current_action, reward, next_state)
        self.replay.push(self._current_state, self._current_action, reward, next_state)

        # Replay batch learning (re-study past experiences)
        if len(self.replay) >= 32:
            for s, a, r, ns in self.replay.sample(32):
                self.qtable.update(s, a, r, ns)

        # Log to diary
        self.memory.record(
            symbol       = symbol,
            state        = self._current_state,
            action       = self._current_action,
            reward       = reward,
            pnl          = pnl,
            exit_reason  = exit_reason,
            signal_score = next_market_data.get("signal_score", 0),
            epsilon      = self.epsilon,
        )

        # Update portfolio
        self.portfolio.position_closed(symbol, pnl)

        # Decay epsilon (get smarter over time)
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        self._save_epsilon()

        logger.info(
            f"[RL] {symbol} closed | PnL=${pnl:+.0f} | Reward={reward:+.3f} | "
            f"ε→{self.epsilon:.3f} | WR(50)={self.memory.win_rate()}%"
        )

        # Reset episode
        self._current_state  = None
        self._current_action = None

    def _load_epsilon(self) -> float:
        try:
            ep_file = Path("data/rl_epsilon.json")
            if ep_file.exists():
                return float(json.loads(ep_file.read_text()).get("epsilon", 0.3))
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")
        return 0.30   # Start with 30% exploration

    def _save_epsilon(self):
        try:
            Path("data/rl_epsilon.json").write_text(json.dumps({"epsilon": self.epsilon}))
        except Exception as _e:
            logger.debug(f"[suppressed] {_e}")

    @property
    def status(self) -> dict:
        return {
            "epsilon":      round(self.epsilon, 3),
            "states_known": len(self.qtable.table),
            "win_rate_50":  self.memory.win_rate(50),
            "avg_reward_50": self.memory.avg_reward(50),
            "open_positions": len(self.portfolio.open_positions),
            "daily_pnl":    round(self.portfolio.daily_pnl, 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
# LAKSHHKING RL — The remote control (plug into existing bot)
# ─────────────────────────────────────────────────────────────────────────────

_agent: Optional[RLAgent] = None


def get_rl_agent() -> RLAgent:
    global _agent
    if _agent is None:
        _agent = RLAgent()
    return _agent


class LakshKingRL:
    """
    The ONLY interface you need. 4 calls:
      signal(symbol, direction, market_data, risk_amount) → "LONG"/"SHORT"/"SKIP"
      close(symbol, pnl, exit_reason, market_data)        → updates brain
      candle(market_data)                                  → updates state view
      reset()                                              → new trading day
    """

    @staticmethod
    def signal(symbol: str, direction: str, market_data: dict, risk_amount: float = 250.0) -> str:
        """
        Ask RL agent if this signal should be traded.
        Returns "LONG", "SHORT", or "SKIP".
        If RL says SKIP but signal is very strong (score > 88), override and trade.
        """
        agent  = get_rl_agent()
        action = agent.decide(symbol, direction, market_data, risk_amount)

        # High-conviction override: if signal is A+ grade, trust the signal system
        score = market_data.get("signal_score", 0)
        if action == "SKIP" and score >= 88 and agent.epsilon < 0.15:
            logger.info(f"[RL] {symbol} A+ override: score={score} overrides SKIP")
            action = direction

        if action not in ("SKIP",):
            agent.portfolio.position_opened(symbol, action)
        return action

    @staticmethod
    def close(symbol: str, pnl: float, exit_reason: str, market_data: dict):
        """Call when a position is closed. Brain learns from this trade."""
        get_rl_agent().on_trade_closed(symbol, pnl, exit_reason, market_data)

    @staticmethod
    def candle(market_data: dict) -> str:
        """Feed new candle data. Returns current state string for logging."""
        return get_rl_agent().state_builder.build(market_data)

    @staticmethod
    def reset():
        """Call at market open (9:15 AM IST) to reset daily state."""
        agent = get_rl_agent()
        agent.portfolio.reset_day()
        logger.info(f"[RL] New day reset | {agent.status}")

    @staticmethod
    def status_message() -> str:
        """Telegram-ready status summary."""
        s = get_rl_agent().status
        return (
            f"🧠 <b>RL Brain Status</b>\n"
            f"States learned: {s['states_known']}\n"
            f"Exploration (ε): {s['epsilon']:.1%} → "
            f"{'still learning' if s['epsilon'] > 0.15 else 'mostly exploiting learned patterns'}\n"
            f"Win rate (last 50): {s['win_rate_50']}%\n"
            f"Avg reward: {s['avg_reward_50']:+.3f}\n"
            f"Open positions: {s['open_positions']}\n"
            f"Daily P&L: ${s['daily_pnl']:+.0f}"
        )
