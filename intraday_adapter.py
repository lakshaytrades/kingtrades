"""
Intraday Adapter — real-time 30-minute parameter adaptation during trading hours.

The core insight: markets have "sessions within sessions". The morning session
(9:15-11:00) often sets the tone. The mid-session (11:00-13:00) is often choppy.
The power hour (13:00-15:20) is high-volume directional.

This module:
1. Tracks the current session's P&L, WR, and signal quality every 30 minutes
2. Detects the intraday regime (trending / choppy / reversing)
3. Adapts min_score, position_size_mult, and SL/TP multipliers accordingly
4. Reports to Telegram every hour if anything significant changed

The goal: never fight the tape. When the market is trending, ride it with bigger
size. When it's choppy, sit on hands with higher quality bar.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, date
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

import numpy as np

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class SessionState:
    session_date: str = ""
    session_pnl: float = 0.0
    session_trades: int = 0
    session_wins: int = 0
    session_losses: int = 0
    session_wr: float = 0.50
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    intraday_regime: str = "UNKNOWN"    # TRENDING_UP / TRENDING_DOWN / CHOPPY / REVERSAL
    regime_confidence: float = 0.5
    # Current adapted parameters
    adapted_min_score: float = 75.0
    adapted_position_mult: float = 1.0
    adapted_sl_mult: float = 1.5
    adapted_tp_mult: float = 3.0
    last_update_time: str = ""
    cycle_count: int = 0


class IntradaySessionTracker:
    """Tracks real-time session performance from trade outcomes."""

    def __init__(self):
        self._state = SessionState(session_date=str(date.today()))
        self._lock = threading.Lock()
        self._price_history: list = []  # list of (timestamp, nifty_price)
        self._signal_history: list = []  # list of (timestamp, score, passed)

    def record_trade(self, pnl: float, win: bool):
        with self._lock:
            self._state.session_pnl += pnl
            self._state.session_trades += 1
            if win:
                self._state.session_wins += 1
                self._state.consecutive_wins += 1
                self._state.consecutive_losses = 0
            else:
                self._state.session_losses += 1
                self._state.consecutive_losses += 1
                self._state.consecutive_wins = 0
            total = self._state.session_trades
            self._state.session_wr = self._state.session_wins / total if total > 0 else 0.5

    def record_price(self, price: float):
        with self._lock:
            self._price_history.append((time.time(), price))
            # Keep last 60 minutes of prices (assuming 1-min samples)
            cutoff = time.time() - 3600
            self._price_history = [(t, p) for t, p in self._price_history if t > cutoff]

    def get_state(self) -> SessionState:
        with self._lock:
            return self._state

    def detect_intraday_regime(self) -> tuple:
        """Detect current intraday regime from recent price action."""
        with self._lock:
            prices = [p for _, p in self._price_history[-30:]]  # last 30 min

        if len(prices) < 10:
            return "UNKNOWN", 0.5

        prices = np.array(prices, dtype=float)
        denom = np.where(prices[:-1] != 0, prices[:-1], 1e-8)
        returns = np.diff(prices) / denom

        # Trend strength: sum of directional returns
        up_rets = np.sum(returns[returns > 0])
        dn_rets = abs(np.sum(returns[returns < 0]))
        total_range = prices.max() - prices.min()
        price_scale = prices.mean()

        range_pct = total_range / price_scale if price_scale > 0 else 0
        directional = (up_rets - dn_rets) / (up_rets + dn_rets + 1e-9)
        volatility = np.std(returns)

        # High volatility + strong direction = TRENDING
        if abs(directional) > 0.5 and range_pct > 0.005:
            if directional > 0:
                return "TRENDING_UP", min(0.9, 0.5 + abs(directional))
            else:
                return "TRENDING_DOWN", min(0.9, 0.5 + abs(directional))

        # Low volatility + no direction = CHOPPY
        if range_pct < 0.002 and abs(directional) < 0.3:
            return "CHOPPY", 0.75

        # High volatility + direction reversals = REVERSAL
        if volatility > 0.003 and abs(directional) < 0.25:
            return "REVERSAL", 0.65

        return "NEUTRAL", 0.5


class IntradayParamAdapter:
    """
    Adapts trading parameters based on session performance and intraday regime.

    The adaptation logic (based on 18 years of intraday experience):
    - First hour losing: RAISE bar significantly (market not cooperating today)
    - Consecutive losses: RAISE bar (bad luck or bad conditions)
    - Session WR > 70%: Slight relaxation (market flowing, take more)
    - TRENDING regime: Lower sl_mult (tighter stop), higher position size
    - CHOPPY regime: Higher min_score, lower position size
    - REVERSAL regime: Flip strategy bias, tighten everything
    """

    # Base parameters (will be loaded from config if available)
    BASE_MIN_SCORE = 75.0
    BASE_POSITION_MULT = 1.0
    BASE_SL_MULT = 1.5
    BASE_TP_MULT = 3.0

    def __init__(self):
        self._load_base_from_config()

    def _load_base_from_config(self):
        try:
            import config
            self.BASE_MIN_SCORE = getattr(config, "MIN_SIGNAL_SCORE", 75.0)
            self.BASE_SL_MULT = getattr(config, "ATR_SL_MULTIPLIER", 1.5)
            self.BASE_TP_MULT = getattr(config, "ATR_TP_MULTIPLIER", 3.0)
        except Exception:
            pass

    def compute_adaptations(self, state: SessionState, regime: str,
                             regime_conf: float) -> dict:
        """
        Returns dict of adapted parameters.
        All adaptations are multiplicative adjustments to base values.
        """
        min_score = self.BASE_MIN_SCORE
        pos_mult = self.BASE_POSITION_MULT
        sl_mult = self.BASE_SL_MULT
        tp_mult = self.BASE_TP_MULT
        reasons = []

        # === Session P&L based adjustments ===
        if state.session_trades >= 3:
            if state.session_wr < 0.40:
                # Losing session: raise bar significantly
                min_score += 8
                pos_mult *= 0.65
                reasons.append(f"LOW_WR({state.session_wr:.0%})+8pts-35%size")
            elif state.session_wr < 0.50:
                min_score += 4
                pos_mult *= 0.80
                reasons.append(f"BELOW_AVG_WR({state.session_wr:.0%})+4pts")
            elif state.session_wr > 0.70:
                # Winning session: slight relaxation (not too much — avoid winner's curse)
                min_score -= 3
                pos_mult *= 1.15
                reasons.append(f"HIGH_WR({state.session_wr:.0%})-3pts+15%size")

        # === Consecutive loss protection ===
        if state.consecutive_losses >= 3:
            min_score += 10
            pos_mult *= 0.50
            reasons.append(f"3+CONSEC_LOSSES(+10pts,-50%size)")
        elif state.consecutive_losses == 2:
            min_score += 5
            pos_mult *= 0.75
            reasons.append(f"2_CONSEC_LOSSES(+5pts,-25%size)")
        elif state.consecutive_losses == 1 and state.session_trades >= 2:
            min_score += 2
            reasons.append("1_CONSEC_LOSS(+2pts)")

        # === Consecutive win momentum ===
        if state.consecutive_wins >= 3:
            # Market flowing, but don't get overconfident
            pos_mult = min(pos_mult * 1.20, 1.40)
            reasons.append(f"3+CONSEC_WINS(+20%size)")

        # === Session P&L absolute ===
        if state.session_pnl < -0.015:  # lost >1.5% of capital today
            min_score = max(min_score, self.BASE_MIN_SCORE + 15)
            pos_mult *= 0.40
            reasons.append("SESSION_DEEP_LOSS(+15pts,-60%size)")

        # === Regime-based adjustments ===
        if regime == "TRENDING_UP":
            # Trending up: lower stop (trend should not retrace much), larger size
            sl_mult *= 0.85
            pos_mult = min(pos_mult * 1.20, 1.50)
            min_score -= 2  # Accept slightly lower bar in clear trend
            reasons.append("TRENDING_UP(sl-15%,size+20%,-2pts)")
        elif regime == "TRENDING_DOWN":
            # Trending down: be cautious with longs, great for shorts
            sl_mult *= 0.85
            pos_mult = min(pos_mult * 1.10, 1.30)
            min_score -= 1
            reasons.append("TRENDING_DOWN(sl-15%,size+10%,-1pts)")
        elif regime == "CHOPPY":
            # Choppy: raise bar significantly, reduce size
            min_score += 6
            pos_mult *= 0.60
            tp_mult *= 0.75  # Take profits faster in choppy market
            reasons.append("CHOPPY(+6pts,-40%size,tp-25%)")
        elif regime == "REVERSAL":
            # Reversal: tightest parameters, smallest size
            min_score += 8
            pos_mult *= 0.50
            sl_mult *= 1.20  # Wider stop in reversal
            reasons.append("REVERSAL(+8pts,-50%size,sl+20%)")

        # === Hard bounds ===
        min_score = max(65.0, min(95.0, min_score))
        pos_mult = max(0.25, min(1.75, pos_mult))
        sl_mult = max(1.0, min(2.5, sl_mult))
        tp_mult = max(2.0, min(5.0, tp_mult))

        return {
            "min_score": round(min_score, 1),
            "position_size_mult": round(pos_mult, 3),
            "sl_multiplier": round(sl_mult, 3),
            "tp_multiplier": round(tp_mult, 3),
            "reasons": reasons,
            "intraday_regime": regime,
            "session_wr": round(state.session_wr, 3),
        }


class IntradayAdapter:
    """
    Main intraday adapter: orchestrates tracking and adaptation.
    Runs a background thread that fires every 30 minutes during market hours.
    """

    CYCLE_INTERVAL_SECS = 1800  # 30 minutes
    OUTPUT_FILE = "data/intraday_params.json"

    def __init__(self):
        self.tracker = IntradaySessionTracker()
        self.adapter = IntradayParamAdapter()
        self._current_params: dict = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        os.makedirs("data", exist_ok=True)

    def start_background(self):
        """Start the background adaptation loop."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="IntradayAdapter"
        )
        self._thread.start()
        logger.info("[IntradayAdapter] Background adaptation loop started (30-min cycle)")

    def stop(self):
        self._stop.set()

    def record_trade(self, pnl: float, win: bool):
        """Called by main.py after each trade closes."""
        self.tracker.record_trade(pnl, win)

    def record_price(self, price: float):
        """Called periodically with Nifty/market index price."""
        self.tracker.record_price(price)

    def get_current_params(self) -> dict:
        """Returns latest adapted parameters. Thread-safe."""
        return dict(self._current_params)

    def _run_loop(self):
        while not self._stop.is_set():
            try:
                self._run_cycle()
            except Exception as e:
                logger.warning(f"[IntradayAdapter] Cycle error: {e}")
            self._stop.wait(self.CYCLE_INTERVAL_SECS)

    def _run_cycle(self):
        now_ist = datetime.now(IST)
        state = self.tracker.get_state()
        state.cycle_count += 1
        state.last_update_time = now_ist.strftime("%H:%M IST")

        # Detect regime
        regime, regime_conf = self.tracker.detect_intraday_regime()
        state.intraday_regime = regime
        state.regime_confidence = regime_conf

        # Compute adaptations
        params = self.adapter.compute_adaptations(state, regime, regime_conf)

        # Update state
        state.adapted_min_score = params["min_score"]
        state.adapted_position_mult = params["position_size_mult"]
        state.adapted_sl_mult = params["sl_multiplier"]
        state.adapted_tp_mult = params["tp_multiplier"]

        self._current_params = params

        # Persist to file for other modules to read
        try:
            with open(self.OUTPUT_FILE, "w") as f:
                json.dump(params, f, indent=2)
        except Exception as e:
            logger.debug(f"[IntradayAdapter] File write error: {e}")

        # Log the adaptation
        logger.info(
            f"[IntradayAdapter] Cycle {state.cycle_count} | {now_ist.strftime('%H:%M')} IST | "
            f"Regime={regime}({regime_conf:.0%}) | "
            f"Session: {state.session_trades}T {state.session_wr:.0%}WR ₹{state.session_pnl:+.0f} | "
            f"→ min_score={params['min_score']} size_mult={params['position_size_mult']:.2f} | "
            f"Reasons: {', '.join(params['reasons']) if params['reasons'] else 'baseline'}"
        )

        # Telegram alert on significant changes (only every other cycle to avoid spam)
        if state.cycle_count % 2 == 0 or params["reasons"]:
            self._send_telegram_update(state, params)

    def _send_telegram_update(self, state: SessionState, params: dict):
        try:
            from alerts_telegram import TelegramAlerts
            alert = TelegramAlerts()
            regime_emoji = {
                "TRENDING_UP": "\U0001f680",
                "TRENDING_DOWN": "\U0001f4c9",
                "CHOPPY": "\U0001f500",
                "REVERSAL": "\U0001f504",
            }.get(params["intraday_regime"], "\U0001f4ca")

            msg = (
                f"{regime_emoji} <b>INTRADAY ADAPT [{state.last_update_time}]</b>\n"
                f"Regime: <code>{params['intraday_regime']}</code>\n"
                f"Session: {state.session_trades}T | WR: {state.session_wr:.0%} | "
                f"P&L: ₹{state.session_pnl:+.0f}\n"
                f"MinScore: <b>{params['min_score']}</b> | "
                f"SizeMult: <b>{params['position_size_mult']:.2f}x</b>\n"
                f"SL: {params['sl_multiplier']:.2f}x | TP: {params['tp_multiplier']:.2f}x\n"
                f"{'⚠️ ' + ' | '.join(params['reasons']) if params['reasons'] else '✅ Baseline params'}"
            )
            alert.send_message(msg)
        except Exception as e:
            logger.debug(f"[IntradayAdapter] Telegram error: {e}")

    @classmethod
    def load_params_from_file(cls) -> dict:
        """Static method: load latest params from file (for other modules)."""
        try:
            with open(cls.OUTPUT_FILE) as f:
                return json.load(f)
        except Exception:
            return {}


# Module-level singleton
_adapter: Optional[IntradayAdapter] = None

def get_adapter() -> IntradayAdapter:
    global _adapter
    if _adapter is None:
        _adapter = IntradayAdapter()
    return _adapter
