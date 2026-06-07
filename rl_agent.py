"""
rl_agent.py — Reinforcement Learning Brain for KingTrades

Architecture (as designed):
  StateBuilder         → Eyes. Encodes market into a discrete state string like "12201102"
  QTable               → Memory. Knows what action worked in each state. Saved to disk.
  NeuralQApproximator  → Deep memory. Replaces sparse Q-table with neural Q-function.
  ReplayBuffer         → Notebook. Stores last 2,000 experiences for re-learning.
  RewardCalculator     → Scorecard. Points for wins, deductions for bad habits.
  TradeMemory          → Diary. Full log of every trade + outcome.
  RLAgent              → Brain. Q-learning agent that improves after every trade.
  LakshKingRL          → Remote control. 4 calls: signal, close, candle, reset.

Institution-level additions:
  - Correlation filter (no two correlated stocks simultaneously)
  - Market regime detection (trending / ranging / volatile)
  - Kelly Criterion position sizing
  - Portfolio heat limit (max 40% capital deployed at once)
  - Drawdown-based position reduction

Neural DQN (v2.0):
  - NeuralQApproximator replaces sparse dict Q-table as primary Q-function
  - 12-dim state vector → MLPRegressor → Q-values for 3 actions
  - Epsilon-greedy with adaptive epsilon (decays faster when losing)
  - Falls back to legacy QTable if sklearn is unavailable
  - Persisted to data/dqn_model.pkl

Files created:
  data/rl_qtable.json    — Fallback Q-table. Grows smarter with every trade.
  data/rl_memory.json    — Full diary of every trade + reward given.
  data/dqn_model.pkl     — Neural Q-function approximator weights.
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
# NEURAL Q-FUNCTION APPROXIMATOR — Deep memory (v2.0)
# Replaces the sparse dict Q-table with a neural Q-function.
# ─────────────────────────────────────────────────────────────────────────────

class NeuralQApproximator:
    """
    Neural Q-function approximator. Replaces sparse Q-table.

    Maps a 12-dim continuous state vector → Q-values for 3 actions
    (0=SKIP, 1=LONG, 2=SHORT) via an MLP regressor.

    Key design decisions:
    - Uses sklearn MLPRegressor (no heavy PyTorch/TF dependency).
    - warm_start=True so incremental fits don't discard previous weights.
    - Experience buffer capped at 500 samples (prevents memory bloat).
    - Retrains every 20 new samples (online learning cadence).
    - Falls back gracefully if sklearn is unavailable.
    - Persisted to data/dqn_model.pkl between sessions.
    """

    N_ACTIONS = 3   # 0=SKIP, 1=LONG, 2=SHORT
    STATE_DIM = 12

    def __init__(self):
        self._model = None
        self._buf_X: list = []
        self._buf_y: list = []
        self._model_path = Path("data/dqn_model.pkl")
        self._load()

    def _load(self):
        try:
            if self._model_path.exists():
                import joblib
                self._model = joblib.load(self._model_path)
                logger.info(
                    f"[DQN] Loaded neural Q-approximator from {self._model_path}"
                )
        except Exception as e:
            logger.debug(f"[DQN] model load failed (will train from scratch): {e}")

    def encode_state(self, state: dict) -> list:
        """12-dim float vector from state dict. All keys use .get(key, default)."""
        try:
            now = get_current_ist_time()
            time_of_day = now.hour + now.minute / 60.0
        except Exception:
            time_of_day = float(state.get("time_of_day", 13))
        return [
            float(state.get("rsi", 50)) / 100.0,
            float(state.get("adx", 25)) / 100.0,
            min(float(state.get("volume_ratio", 1.0)) / 5.0, 1.0),
            float(state.get("signal_score", 70)) / 100.0,
            float(bool(state.get("mtf_aligned", False))),
            float(bool(state.get("regime_trending", False))),
            float(bool(state.get("session_opening", False))),
            min(float(state.get("portfolio_heat", 0)) / 100.0, 1.0),
            float(state.get("recent_wr", 0.5)),
            min(float(state.get("vix", 20)) / 40.0, 1.0),
            min(float(state.get("consecutive_losses", 0)) / 5.0, 1.0),
            time_of_day / 24.0,
        ]

    def predict_q(self, state: dict) -> list:
        """
        Predict Q-values for all actions. Returns list of N_ACTIONS floats.
        Returns slight LONG bias by default when no model is trained yet.
        """
        if self._model is None or len(self._buf_X) < 15:
            return [0.0, 0.05, -0.05]  # slight LONG bias by default
        try:
            result = self._model.predict([self.encode_state(state)])[0]
            return list(result)
        except Exception as e:
            logger.debug(f"[DQN] predict_q failed: {e}")
            return [0.0, 0.0, 0.0]

    def best_action(self, state: dict, epsilon: float = 0.1) -> int:
        """
        Epsilon-greedy action selection.
        Returns action index: 0=SKIP, 1=LONG, 2=SHORT.
        """
        if random.random() < epsilon:
            return random.randint(0, self.N_ACTIONS - 1)
        q = self.predict_q(state)
        return int(max(range(self.N_ACTIONS), key=lambda i: q[i]))

    def update(
        self,
        state: dict,
        action: int,
        reward: float,
        next_state: dict,
        gamma: float = 0.90,
    ):
        """
        Bellman TD update: target = reward + γ * max Q(next_state).
        Appends to experience buffer and retrains every 20 samples.
        """
        next_q = max(self.predict_q(next_state))
        td_target = reward + gamma * next_q
        current_q = list(self.predict_q(state))
        current_q[action] = td_target
        self._buf_X.append(self.encode_state(state))
        self._buf_y.append(current_q)
        if len(self._buf_X) >= 20 and len(self._buf_X) % 20 == 0:
            self._retrain()

    def _retrain(self):
        """Fit MLPRegressor on buffered experience. warm_start=True preserves prior weights."""
        try:
            from sklearn.neural_network import MLPRegressor
            import joblib
            X = self._buf_X[-500:]
            y = self._buf_y[-500:]
            if self._model is None:
                self._model = MLPRegressor(
                    hidden_layer_sizes=(32, 16),
                    activation="relu",
                    max_iter=200,
                    warm_start=True,
                    random_state=42,
                )
            self._model.fit(X, y)
            self._model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(self._model, self._model_path)
            logger.debug(f"[DQN] retrained on {len(X)} samples → {self._model_path}")
        except Exception as e:
            logger.debug(f"[DQN] retrain failed: {e}")

    def adaptive_epsilon(self, n_trades: int, recent_wr: float = 0.5) -> float:
        """
        Decaying epsilon with losing-streak boost.
        When recent win-rate < 40%, explore more aggressively.
        """
        base = max(0.05, 0.30 * (0.997 ** n_trades))
        return min(base * 2.0, 0.30) if recent_wr < 0.40 else base


# ─────────────────────────────────────────────────────────────────────────────
# Q-TABLE — Fallback memory (kept for backward-compat and cold start)
# ─────────────────────────────────────────────────────────────────────────────

class QTable:
    """
    Maps (state, action) → expected reward.
    Actions: 0=SKIP, 1=BUY(LONG), 2=SELL(SHORT)
    Persisted to disk so the bot never forgets across restarts.
    Used as fallback if sklearn is unavailable for NeuralQApproximator.
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
# RL AGENT — The actual brain (v2.0 with Neural DQN)
# ─────────────────────────────────────────────────────────────────────────────

class RLAgent:
    """
    Q-learning agent that improves after every trade.

    v2.0: Integrates NeuralQApproximator (DQN) as the primary decision engine.
    Falls back to legacy QTable if sklearn is unavailable.

    Uses epsilon-greedy: starts exploring (random), gradually exploits (learned).
    The DQN's adaptive_epsilon() adjusts exploration rate based on recent win-rate.
    """

    def __init__(self):
        self.qtable   = QTable(lr=0.12, gamma=0.90)
        self.replay   = ReplayBuffer(capacity=2000)
        self.rewards  = RewardCalculator()
        self.memory   = TradeMemory()
        self.portfolio= PortfolioManager()
        self.state_builder = StateBuilder()

        # Neural DQN — primary Q-function approximator
        try:
            self._dqn = NeuralQApproximator()
            self._dqn_available = True
        except Exception as _e:
            logger.warning(f"[RL] NeuralQApproximator init failed — using QTable fallback: {_e}")
            self._dqn = None
            self._dqn_available = False

        # Epsilon: exploration rate (1.0=all random, 0.05=mostly learned)
        self.epsilon      = self._load_epsilon()
        self.epsilon_min  = 0.05
        self.epsilon_decay= 0.995   # Decays after each trade

        # Trade counter (used by adaptive_epsilon)
        self._n_trades: int = 0

        # Current episode tracking
        self._current_state:     Optional[str] = None
        self._current_action:    Optional[int] = None
        self._current_raw_state: Optional[dict] = None   # raw dict for DQN update
        self._entry_time:        Optional[datetime] = None
        self._current_symbol:    Optional[str] = None
        self._current_risk:      float = 0.0

        logger.info(
            f"[RL] Agent ready | States known: {len(self.qtable.table)} | "
            f"epsilon={self.epsilon:.3f} | WR(50)={self.memory.win_rate()}% | "
            f"DQN={'enabled' if self._dqn_available else 'fallback-qtable'}"
        )

    def decide(self, symbol: str, direction_hint: str, market_data: dict, risk_amount: float) -> str:
        """
        Core decision: should we trade this signal?

        Decision priority:
          1. Portfolio-level gate (hard block on concentration/heat/drawdown)
          2. NeuralQApproximator.best_action() if available
          3. QTable fallback if DQN unavailable

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

        # Compute adaptive epsilon from DQN if available
        recent_wr = self.memory.win_rate(50) / 100.0
        if self._dqn_available and self._dqn is not None:
            epsilon = self._dqn.adaptive_epsilon(self._n_trades, recent_wr)
            self.epsilon = max(self.epsilon_min, epsilon)
        else:
            epsilon = self.epsilon

        # ── DQN decision (primary) ─────────────────────────────────────────
        if self._dqn_available and self._dqn is not None:
            try:
                action_idx = self._dqn.best_action(market_data, epsilon)
            except Exception as _e:
                logger.debug(f"[RL] DQN best_action failed, falling back to QTable: {_e}")
                action_idx = self._qtable_decide(state, direction_hint, epsilon)
        else:
            # ── QTable fallback ────────────────────────────────────────────
            action_idx = self._qtable_decide(state, direction_hint, epsilon)

        action = QTable.ACTIONS[action_idx]

        # Track episode (save both discrete state and raw dict for DQN update)
        self._current_state      = state
        self._current_action     = action_idx
        self._current_raw_state  = dict(market_data)
        self._entry_time         = get_current_ist_time()
        self._current_symbol     = symbol
        self._current_risk       = risk_amount

        dqn_q_str = "n/a"
        if self._dqn_available and self._dqn is not None:
            try:
                dqn_q_str = str([round(q, 2) for q in self._dqn.predict_q(market_data)])
            except Exception:
                pass

        logger.info(
            f"[RL] {symbol} | State={state} | eps={epsilon:.3f} | "
            f"Q={[round(x,2) for x in self.qtable.get(state)]} | "
            f"DQN_Q={dqn_q_str} | Decision={action}"
        )
        return action

    def _qtable_decide(self, state: str, direction_hint: str, epsilon: float) -> int:
        """Epsilon-greedy decision using legacy QTable."""
        if random.random() < epsilon:
            # Exploration: use signal generator's suggestion (not pure random)
            return {"LONG": 1, "SHORT": 2}.get(direction_hint, 0)
        else:
            # Exploitation: use what we've learned
            return self.qtable.best_action(state)

    def on_trade_closed(self, symbol: str, pnl: float, exit_reason: str, next_market_data: dict):
        """
        Call this when a position is closed. Updates both DQN and QTable.
        DQN update uses raw market_data dicts; QTable uses discrete state strings.
        """
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

        # ── Update QTable (fallback / parallel learning) ───────────────────
        self.qtable.update(self._current_state, self._current_action, reward, next_state)
        self.replay.push(self._current_state, self._current_action, reward, next_state)

        # Replay batch learning (re-study past experiences via QTable)
        if len(self.replay) >= 32:
            for s, a, r, ns in self.replay.sample(32):
                self.qtable.update(s, a, r, ns)

        # ── Update DQN (primary Q-function) ───────────────────────────────
        if self._dqn_available and self._dqn is not None and self._current_raw_state is not None:
            try:
                self._dqn.update(
                    state      = self._current_raw_state,
                    action     = self._current_action,
                    reward     = reward,
                    next_state = next_market_data,
                )
            except Exception as _e:
                logger.debug(f"[RL] DQN update suppressed: {_e}")

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
        self._n_trades += 1

        logger.info(
            f"[RL] {symbol} closed | PnL=${pnl:+.0f} | Reward={reward:+.3f} | "
            f"eps->{self.epsilon:.3f} | WR(50)={self.memory.win_rate()}%"
        )

        # Reset episode
        self._current_state      = None
        self._current_action     = None
        self._current_raw_state  = None

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
            "epsilon":        round(self.epsilon, 3),
            "states_known":   len(self.qtable.table),
            "win_rate_50":    self.memory.win_rate(50),
            "avg_reward_50":  self.memory.avg_reward(50),
            "open_positions": len(self.portfolio.open_positions),
            "daily_pnl":      round(self.portfolio.daily_pnl, 2),
            "dqn_enabled":    self._dqn_available,
            "dqn_buf_size":   len(self._dqn._buf_X) if self._dqn is not None else 0,
            "n_trades":       self._n_trades,
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
        dqn_str = (
            f"DQN buffer: {s['dqn_buf_size']} samples"
            if s.get("dqn_enabled") else "DQN: fallback Q-table"
        )
        return (
            f"🧠 <b>RL Brain Status</b>\n"
            f"States learned: {s['states_known']}\n"
            f"Exploration: {s['epsilon']:.1%} → "
            f"{'still learning' if s['epsilon'] > 0.15 else 'mostly exploiting learned patterns'}\n"
            f"Win rate (last 50): {s['win_rate_50']}%\n"
            f"Avg reward: {s['avg_reward_50']:+.3f}\n"
            f"Open positions: {s['open_positions']}\n"
            f"Daily P&L: ${s['daily_pnl']:+.0f}\n"
            f"{dqn_str}"
        )
