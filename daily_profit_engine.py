"""
daily_profit_engine.py — ₹5,000-₹10,000 Daily Profit Management System

The gap between an average trader and a profitable trader is NOT the strategy —
it is capital deployment, profit protection, and intraday compounding.

This engine does 5 things that make ₹5-10k/day achievable:

1. CAPITAL DEPLOYMENT OPTIMIZER
   - A+ signal: Deploy 25% of capital (full MIS leverage = 5× exposure)
   - A  signal: Deploy 18% of capital
   - B  signal: Deploy 12% of capital
   - C  signal: Deploy 8%  of capital (reduced)

2. INTRADAY PROFIT COMPOUNDING
   - First trade hits T1: 50% booking → lock profit → SIZE UP next trade
   - Morning profitable (>₹1k): Increase afternoon position sizes by 20%
   - Win streak (3+): Allow 1 extra concurrent position

3. DAILY PROFIT TARGET MANAGEMENT
   - Target: ₹5,000 (default, configurable)
   - Stretch: ₹7,500
   - Max:     ₹10,000
   - At ₹5,000: Switch to PROTECTION mode — only A+ entries
   - At ₹7,500: Switch to LOCK mode — 60% size, only A+ entries
   - At ₹10,000: STOP trading. Protect the profit. Done for the day.

4. LOSS PROTECTION ESCALATION
   - -₹1,000: Caution mode — A-grade minimum
   - -₹2,000: Defensive mode — only A+ signals, 60% size
   - -₹3,000: Emergency stop — no new entries, exit at T1

5. INTRADAY PERFORMANCE SCORING
   - Scores bot's real-time performance vs expected
   - Adjusts aggressiveness dynamically throughout the day
   - "If you're running hot, press harder. If running cold, step back."

"Know your number. Hit your number. Stop." — every great prop trader.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, format_currency

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ─── Directory for persistence ────────────────────────────────────────────────
_DATA_DIR = Path("data")
_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProfitEngineConfig:
    # Daily targets ($)
    daily_target:          float = 200.0
    daily_stretch_target:  float = 300.0
    daily_max_target:      float = 500.0

    # Loss stops ($ absolute loss = STOP)
    daily_loss_limit:      float = 100.0   # Hard stop — no new entries
    caution_loss:          float = 25.0    # Switch to A-grade only
    defensive_loss:        float = 50.0    # Switch to A+ only, 60% size

    # Capital deployment (% of available capital per trade)
    a_plus_capital_pct:    float = 30.0      # A+ setup — bigger size for highest conviction
    a_capital_pct:         float = 23.0      # A  setup
    b_capital_pct:         float = 16.0      # B  setup
    c_capital_pct:         float = 10.0      # C  setup

    # Compounding
    compound_bonus_pct:    float = 20.0      # After T1 hit: 20% bigger next trade
    win_streak_bonus_pct:  float = 10.0      # Per-trade bonus during win streak (cap 3)

    # Leverage (Alpaca paper — no margin leverage by default)
    mis_leverage:          float = 1.0

    # Min signal scores by mode
    score_normal:          float = 68.0
    score_caution:         float = 73.0
    score_protection:      float = 78.0
    score_lock:            float = 82.0


# ─────────────────────────────────────────────────────────────────────────────
# Trading Mode
# ─────────────────────────────────────────────────────────────────────────────

class TradingMode:
    AGGRESSIVE  = "AGGRESSIVE"   # Morning, winning, below target
    NORMAL      = "NORMAL"       # Standard mode
    CAUTION     = "CAUTION"      # -$25 loss — A grade minimum
    PROTECTION  = "PROTECTION"   # Target hit $200 — A+ only
    LOCK        = "LOCK"         # $300 hit — 60% size, A+ only
    DEFENSIVE   = "DEFENSIVE"    # -$50 loss — A+ only, 60% size
    STOP        = "STOP"         # $500 hit OR -$100 loss — no new entries

    DESCRIPTIONS = {
        AGGRESSIVE: "🚀 AGGRESSIVE — Full size, hot streak",
        NORMAL:     "✅ NORMAL — Standard risk",
        CAUTION:    "⚠️ CAUTION — A-grade minimum (down $25)",
        PROTECTION: "🛡️ PROTECTION — Target hit! A+ only, banking profits",
        LOCK:       "🔒 LOCK — $300 secured, 60% size only",
        DEFENSIVE:  "🔴 DEFENSIVE — A+ only, down $50",
        STOP:       "🛑 STOP — Daily limit reached. No new trades.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Intraday State
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IntradayState:
    date: str = ""
    mode: str = TradingMode.NORMAL
    realised_pnl: float = 0.0        # Closed trades P&L today
    unrealised_pnl: float = 0.0      # Open positions P&L
    peak_pnl: float = 0.0            # Highest P&L reached today
    trades_taken: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    t1_exits: int = 0                # Times T1 was booked
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    compounding_active: bool = False
    compounding_bonus: float = 1.0   # Multiplier (1.0 = no bonus, 1.2 = +20%)
    capital_deployed_today: float = 0.0
    morning_pnl: float = 0.0         # P&L 9:15–12:00
    afternoon_pnl: float = 0.0       # P&L 12:00–15:30

    def __post_init__(self):
        if not self.date:
            self.date = get_current_ist_time().strftime("%Y-%m-%d")

    @property
    def total_pnl(self) -> float:
        return self.realised_pnl + self.unrealised_pnl

    @property
    def win_rate(self) -> float:
        total = self.winning_trades + self.losing_trades
        return self.winning_trades / total if total > 0 else 0.0

    @property
    def target_pct(self) -> float:
        """How far towards daily target (0–1+)."""
        return self.realised_pnl / 5000.0  # Base reference


# ─────────────────────────────────────────────────────────────────────────────
# Daily Profit Engine
# ─────────────────────────────────────────────────────────────────────────────

class DailyProfitEngine:
    """
    Manages daily P&L targets, capital deployment, and intraday compounding.

    Usage (from main.py / RiskManager):
      engine = get_profit_engine()
      engine.initialize(available_balance=9634.0, daily_target=5000.0)

      # Before each trade:
      deployment = engine.get_capital_deployment(quality_grade="A+", signal_score=88)
      capital_to_use = deployment.capital_rupees

      # After T1 exit:
      engine.record_partial_exit(symbol, profit)

      # After full exit:
      engine.record_trade_closed(symbol, pnl)
    """

    def __init__(self, config: Optional[ProfitEngineConfig] = None):
        self.cfg = config or ProfitEngineConfig()
        self.state = IntradayState()
        self._available_balance: float = 0.0
        self._daily_target: float = self.cfg.daily_target
        self._state_file = _DATA_DIR / "profit_engine_state.json"

    def initialize(
        self,
        available_balance: float,
        daily_target: float = 200.0,
    ) -> None:
        """Call once at 9:15 AM market open."""
        today = get_current_ist_time().strftime("%Y-%m-%d")
        self._available_balance = available_balance

        # ── Monthly pacing: adjust today's target to stay on 13%/month pace ─
        daily_target = self._apply_monthly_catchup(daily_target, available_balance, today)

        self._daily_target = daily_target
        self.cfg.daily_target          = daily_target
        self.cfg.daily_stretch_target  = daily_target * 2.0   # 2× = LOCK mode
        self.cfg.daily_max_target      = daily_target * 3.0   # 3× = STOP for the day

        # Percentage-based loss thresholds aligned with DAILY_LOSS_LIMIT_PCT
        # Tight limits were killing trades after 1 bad scalp on small accounts
        self.cfg.caution_loss   = max(available_balance * 0.005, 3.0)   # 0.5% — 1 full loss
        self.cfg.defensive_loss = max(available_balance * 0.010, 5.0)   # 1.0% — 2 losses
        self.cfg.daily_loss_limit = max(available_balance * 0.020, 10.0) # 2.0% — hard stop

        # Load or reset state
        saved = self._load_state()
        if saved and saved.date == today:
            self.state = saved
            logger.info(
                f"[{format_ist_timestamp()}] ProfitEngine: loaded today's state "
                f"P&L=${self.state.realised_pnl:,.2f} mode={self.state.mode}"
            )
        else:
            self.state = IntradayState(date=today)
            logger.info(
                f"[{format_ist_timestamp()}] ProfitEngine: fresh day | "
                f"Balance: ${available_balance:,.2f} | "
                f"Target: ${daily_target:,.2f} | "
                f"Buying power: ${available_balance:,.2f}"
            )
        self._update_mode()

    # ─────────────────────────────────────────────
    # MONTHLY PACING — CATCH-UP ENGINE
    # ─────────────────────────────────────────────

    _MONTHLY_FILE = _DATA_DIR / "monthly_pnl.json"

    def _apply_monthly_catchup(
        self, base_target: float, balance: float, today: str
    ) -> float:
        """
        Read monthly P&L history and boost today's target if behind 13%/month pace.
        Reduces target slightly when already well ahead (protect the month).
        """
        try:
            import json as _json
            from pathlib import Path as _Path
            now = get_current_ist_time()
            month_key = now.strftime("%Y-%m")

            data: dict = {}
            if self._MONTHLY_FILE.exists():
                data = _json.loads(self._MONTHLY_FILE.read_text())

            month_data = data.get(month_key, {})
            monthly_pnl = sum(month_data.values())  # sum of all daily P&Ls this month

            # How many trading days have elapsed this month (rough: count recorded days)
            days_elapsed = max(len(month_data), 1)
            # Target pace: 13% monthly = monthly_target_pct × balance
            try:
                import config as _cfg
                monthly_target_pct = _cfg.MONTHLY_TARGET_PCT
            except Exception:
                monthly_target_pct = 13.0
            monthly_target = balance * monthly_target_pct / 100

            # Remaining trading days estimate: 22 total - days elapsed
            trading_days_left = max(22 - days_elapsed, 1)
            remaining_needed  = monthly_target - monthly_pnl

            # Pace-based daily target
            pace_target = remaining_needed / trading_days_left

            # Only boost target when BEHIND pace — never reduce (base target is already the floor)
            pct_of_base = pace_target / base_target if base_target > 0 else 1.0
            if pct_of_base > 1.2 and monthly_pnl < monthly_target * 0.5:
                adjusted = base_target * 1.2   # max 20% boost — don't over-risk
                logger.info(
                    f"[{format_ist_timestamp()}] Monthly catchup: behind pace "
                    f"(monthly_pnl=${monthly_pnl:+.2f}, need ${remaining_needed:.2f} in {trading_days_left} days) "
                    f"→ target boosted ${base_target:.2f}→${adjusted:.2f}"
                )
            else:
                adjusted = base_target   # on pace or ahead — keep base target

            return round(adjusted, 2)

        except Exception as e:
            logger.debug(f"Monthly catchup calc failed: {e}")
            return base_target

    def record_eod_pnl(self, date_str: str, daily_pnl: float) -> None:
        """Call at end of day to persist daily P&L for monthly tracking."""
        try:
            import json as _json
            month_key = date_str[:7]  # "2026-05"
            data: dict = {}
            if self._MONTHLY_FILE.exists():
                data = _json.loads(self._MONTHLY_FILE.read_text())
            if month_key not in data:
                data[month_key] = {}
            data[month_key][date_str] = round(daily_pnl, 4)
            # Keep only last 3 months
            keys = sorted(data.keys())
            if len(keys) > 3:
                for old in keys[:-3]:
                    del data[old]
            self._MONTHLY_FILE.write_text(_json.dumps(data, indent=2))
            logger.info(
                f"[{format_ist_timestamp()}] Monthly tracker: {date_str} P&L=${daily_pnl:+.2f} saved"
            )
        except Exception as e:
            logger.debug(f"record_eod_pnl failed: {e}")

    def update_balance(self, balance: float) -> None:
        if balance > 0:
            self._available_balance = balance

    def update_unrealised(self, unrealised_pnl: float) -> None:
        """Update open position P&L — call every scan cycle."""
        self.state.unrealised_pnl = unrealised_pnl
        self.state.peak_pnl = max(self.state.peak_pnl, self.state.total_pnl)
        self._update_mode()

    # ─────────────────────────────────────────────
    # CAPITAL DEPLOYMENT OPTIMIZER
    # ─────────────────────────────────────────────

    def get_capital_deployment(
        self,
        quality_grade: str,
        signal_score: float,
        size_multiplier: float = 1.0,
    ) -> "DeploymentPlan":
        """
        Given a signal's grade, returns exactly how much capital to deploy.
        Accounts for current mode, compounding bonus, and win streak.

        Returns DeploymentPlan with:
          - capital_rupees: how much $ to allocate from account balance
          - effective_buying_power: capital (no leverage)
          - blocked: True if mode says STOP trading
          - reason: explanation
        """
        if self.state.mode == TradingMode.STOP:
            return DeploymentPlan(
                capital_rupees=0, effective_buying_power=0,
                blocked=True, reason=TradingMode.DESCRIPTIONS[TradingMode.STOP]
            )

        # Grade-based base deployment %
        base_pct_map = {
            "A+": self.cfg.a_plus_capital_pct,
            "A":  self.cfg.a_capital_pct,
            "B":  self.cfg.b_capital_pct,
            "C":  self.cfg.c_capital_pct,
        }
        base_pct = base_pct_map.get(quality_grade, self.cfg.b_capital_pct)

        # Mode-based restrictions
        min_grade = self._get_min_grade_for_mode()
        grade_rank = {"A+": 4, "A": 3, "B": 2, "C": 1}
        if grade_rank.get(quality_grade, 0) < grade_rank.get(min_grade, 0):
            return DeploymentPlan(
                capital_rupees=0, effective_buying_power=0,
                blocked=True,
                reason=f"Mode {self.state.mode}: requires {min_grade}+ grade"
            )

        # Mode size reducer
        mode_mult = self._get_mode_size_multiplier()

        # Compounding bonus (after T1 hits)
        comp_mult = self.state.compounding_bonus if self.state.compounding_active else 1.0

        # Win streak bonus (up to +30%)
        streak_bonus = min(self.state.consecutive_wins * self.cfg.win_streak_bonus_pct / 100, 0.30)

        # Score bonus: 85+ score → +10% size
        score_bonus = 0.10 if signal_score >= 85 else (0.05 if signal_score >= 78 else 0.0)

        # External size_multiplier from filter
        total_mult = mode_mult * comp_mult * (1 + streak_bonus + score_bonus) * size_multiplier

        # Final % of capital to deploy
        final_pct = base_pct * total_mult / 100.0
        final_pct = max(0.04, min(final_pct, 0.35))  # 4%–35% hard limits

        capital_usd = self._available_balance * final_pct
        # Minimum trade: $10
        capital_usd = max(capital_usd, 10.0)

        buying_power = capital_usd * self.cfg.mis_leverage

        return DeploymentPlan(
            capital_rupees=round(capital_usd, 2),
            effective_buying_power=round(buying_power, 2),
            blocked=False,
            base_pct=base_pct,
            mode_mult=mode_mult,
            comp_mult=comp_mult,
            streak_bonus=streak_bonus,
            score_bonus=score_bonus,
            reason=(
                f"Grade {quality_grade} | Mode {self.state.mode} | "
                f"Base {base_pct:.0f}% × {total_mult:.2f}x mult = "
                f"${capital_usd:,.2f}"
            )
        )

    # ─────────────────────────────────────────────
    # TRADE RECORDING (P&L tracking)
    # ─────────────────────────────────────────────

    def record_t1_exit(self, symbol: str, profit: float) -> None:
        """Called when T1 (50% partial exit) is taken."""
        self.state.realised_pnl += profit
        self.state.t1_exits += 1
        # Activate compounding after first T1
        if profit > 0 and not self.state.compounding_active:
            self.state.compounding_active = True
            self.state.compounding_bonus = 1 + self.cfg.compound_bonus_pct / 100
            logger.info(
                f"[{format_ist_timestamp()}] COMPOUNDING ACTIVATED — "
                f"T1 profit ${profit:,.2f} | Next trades +{self.cfg.compound_bonus_pct:.0f}% size"
            )
        self._update_mode()
        self._save_state()

    def record_trade_closed(self, symbol: str, pnl: float, was_partial: bool = False) -> None:
        """Called when a full position is closed."""
        if not was_partial:
            self.state.realised_pnl += pnl
        self.state.trades_taken += 1

        now_ist = get_current_ist_time()
        if now_ist.hour < 12:
            self.state.morning_pnl += pnl
        else:
            self.state.afternoon_pnl += pnl

        if pnl > 0:
            self.state.winning_trades += 1
            self.state.consecutive_wins += 1
            self.state.consecutive_losses = 0
        else:
            self.state.losing_trades += 1
            self.state.consecutive_losses += 1
            self.state.consecutive_wins = 0
            # Reduce compounding if losing
            if self.state.compounding_active and self.state.consecutive_losses >= 2:
                self.state.compounding_active = False
                self.state.compounding_bonus = 1.0
                logger.info(
                    f"[{format_ist_timestamp()}] Compounding paused — "
                    f"{self.state.consecutive_losses} consecutive losses"
                )

        self._update_mode()
        self._save_state()
        self._log_pnl_status()

    # ─────────────────────────────────────────────
    # DAILY TARGET STATUS
    # ─────────────────────────────────────────────

    def get_status_message(self) -> str:
        """For Telegram /status command."""
        s = self.state
        target = self._daily_target
        pct = (s.realised_pnl / target * 100) if target > 0 else 0

        progress_bar = self._make_progress_bar(min(pct, 100))
        mode_desc = TradingMode.DESCRIPTIONS.get(s.mode, s.mode)

        return (
            f"💰 *Daily P&L Engine*\n\n"
            f"Realised: `${s.realised_pnl:+,.2f}`\n"
            f"Unrealised: `${s.unrealised_pnl:+,.2f}`\n"
            f"Total: `${s.total_pnl:+,.2f}`\n\n"
            f"Target: `${target:,.2f}` ({pct:.0f}%)\n"
            f"{progress_bar}\n\n"
            f"Mode: {mode_desc}\n"
            f"Trades: {s.trades_taken} | W/L: {s.winning_trades}/{s.losing_trades}\n"
            f"Win Rate: {s.win_rate*100:.0f}%\n"
            f"Streak: {'🔥 ' + str(s.consecutive_wins) + ' wins' if s.consecutive_wins >= 2 else ('❄️ ' + str(s.consecutive_losses) + ' losses' if s.consecutive_losses >= 2 else 'neutral')}\n"
            f"Compounding: {'✅ +' + str(int((s.compounding_bonus-1)*100)) + '%' if s.compounding_active else '⏸ off'}"
        )

    def should_take_trade(self) -> Tuple[bool, str]:
        """Quick check: is trading allowed right now?"""
        if self.state.mode == TradingMode.STOP:
            return False, TradingMode.DESCRIPTIONS[TradingMode.STOP]
        return True, self.state.mode

    def get_min_signal_score(self) -> float:
        """Minimum signal score to consider based on current mode."""
        score_map = {
            TradingMode.AGGRESSIVE:  self.cfg.score_normal - 2,  # 66
            TradingMode.NORMAL:      self.cfg.score_normal,       # 68
            TradingMode.CAUTION:     self.cfg.score_caution,      # 73
            TradingMode.PROTECTION:  self.cfg.score_protection,   # 78
            TradingMode.LOCK:        self.cfg.score_lock,         # 82
            TradingMode.DEFENSIVE:   self.cfg.score_protection,   # 78
            TradingMode.STOP:        999,                         # No trades
        }
        return score_map.get(self.state.mode, self.cfg.score_normal)

    # ─────────────────────────────────────────────
    # INTERNAL HELPERS
    # ─────────────────────────────────────────────

    def _update_mode(self) -> None:
        """Re-evaluate trading mode based on P&L."""
        pnl = self.state.realised_pnl
        prev_mode = self.state.mode

        if pnl >= self.cfg.daily_max_target:
            self.state.mode = TradingMode.STOP
        elif pnl >= self.cfg.daily_stretch_target:
            self.state.mode = TradingMode.LOCK
        elif pnl >= self.cfg.daily_target:
            self.state.mode = TradingMode.PROTECTION
        elif pnl <= -self.cfg.daily_loss_limit:
            self.state.mode = TradingMode.STOP
        elif pnl <= -self.cfg.defensive_loss:
            self.state.mode = TradingMode.DEFENSIVE
        elif pnl <= -self.cfg.caution_loss:
            self.state.mode = TradingMode.CAUTION
        else:
            # Below target — check if aggressive conditions met
            now_ist = get_current_ist_time()
            et_hour = (now_ist.hour - 4) % 24  # IST→ET rough conversion (IST-9:30 = ET)
            behind_at_afternoon = (
                pnl < self._daily_target * 0.5
                and et_hour >= 13
                and et_hour < 15
            )
            hot_streak = (
                self.state.consecutive_wins >= 1
                and et_hour < 12
                and pnl >= 0
            )
            if behind_at_afternoon or hot_streak:
                self.state.mode = TradingMode.AGGRESSIVE
            else:
                self.state.mode = TradingMode.NORMAL

        if self.state.mode != prev_mode:
            logger.info(
                f"[{format_ist_timestamp()}] MODE CHANGE: {prev_mode} → {self.state.mode} "
                f"| P&L: ${pnl:+,.2f} | "
                + TradingMode.DESCRIPTIONS.get(self.state.mode, "")
            )

    def _get_min_grade_for_mode(self) -> str:
        # PROTECTION: keep trading with A-grade — bot earned the right to hunt more
        # LOCK: only A+ — preserve 2× day target, don't give it back
        return {
            TradingMode.AGGRESSIVE:  "B",
            TradingMode.NORMAL:      "B",
            TradingMode.CAUTION:     "A",
            TradingMode.PROTECTION:  "A",    # was A+ — now keeps taking A-grade setups
            TradingMode.LOCK:        "A+",
            TradingMode.DEFENSIVE:   "A+",
            TradingMode.STOP:        "NONE",
        }.get(self.state.mode, "B")

    def _get_mode_size_multiplier(self) -> float:
        return {
            TradingMode.AGGRESSIVE:  1.30,   # hot streak: press harder
            TradingMode.NORMAL:      1.00,
            TradingMode.CAUTION:     0.80,
            TradingMode.PROTECTION:  1.00,   # target hit but keep full size — push to 4%
            TradingMode.LOCK:        0.70,   # 2× target hit — slight reduction, not full stop
            TradingMode.DEFENSIVE:   0.60,
            TradingMode.STOP:        0.00,
        }.get(self.state.mode, 1.00)

    def _log_pnl_status(self) -> None:
        pnl = self.state.realised_pnl
        pct = pnl / self._daily_target * 100 if self._daily_target > 0 else 0
        logger.info(
            f"[{format_ist_timestamp()}] P&L: ${pnl:+,.2f} ({pct:.0f}% of ${self._daily_target:,.2f} target) "
            f"| Mode: {self.state.mode} | W/L: {self.state.winning_trades}/{self.state.losing_trades} "
            f"| Compound: {'ON' if self.state.compounding_active else 'off'}"
        )

    @staticmethod
    def _make_progress_bar(pct: float, width: int = 10) -> str:
        filled = int(pct / 100 * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}] {pct:.0f}%"

    def _save_state(self) -> None:
        try:
            data = {
                "date": self.state.date,
                "mode": self.state.mode,
                "realised_pnl": self.state.realised_pnl,
                "peak_pnl": self.state.peak_pnl,
                "trades_taken": self.state.trades_taken,
                "winning_trades": self.state.winning_trades,
                "losing_trades": self.state.losing_trades,
                "t1_exits": self.state.t1_exits,
                "consecutive_wins": self.state.consecutive_wins,
                "consecutive_losses": self.state.consecutive_losses,
                "compounding_active": self.state.compounding_active,
                "compounding_bonus": self.state.compounding_bonus,
            }
            self._state_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug(f"DailyProfitEngine save state: {e}")

    def _load_state(self) -> Optional[IntradayState]:
        try:
            if not self._state_file.exists():
                return None
            data = json.loads(self._state_file.read_text())
            s = IntradayState(date=data["date"])
            s.mode = data.get("mode", TradingMode.NORMAL)
            s.realised_pnl = data.get("realised_pnl", 0.0)
            s.peak_pnl = data.get("peak_pnl", 0.0)
            s.trades_taken = data.get("trades_taken", 0)
            s.winning_trades = data.get("winning_trades", 0)
            s.losing_trades = data.get("losing_trades", 0)
            s.t1_exits = data.get("t1_exits", 0)
            s.consecutive_wins = data.get("consecutive_wins", 0)
            s.consecutive_losses = data.get("consecutive_losses", 0)
            s.compounding_active = data.get("compounding_active", False)
            s.compounding_bonus = data.get("compounding_bonus", 1.0)
            return s
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Deployment Plan
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DeploymentPlan:
    capital_rupees: float        # ₹ to allocate from account balance
    effective_buying_power: float  # capital × 5x MIS leverage
    blocked: bool = False
    reason: str = ""
    base_pct: float = 0.0
    mode_mult: float = 1.0
    comp_mult: float = 1.0
    streak_bonus: float = 0.0
    score_bonus: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Expanded NSE Watchlist — Top 50 liquid momentum stocks
# ─────────────────────────────────────────────────────────────────────────────

NSE_TOP50_WATCHLIST = [
    # NIFTY 50 / Large Cap — institutional liquidity guaranteed
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT",
    "WIPRO", "HCLTECH", "AXISBANK", "MARUTI", "SUNPHARMA",
    "TATAMOTORS", "BAJFINANCE", "ADANIENT", "ULTRACEMCO", "TITAN",
    "NTPC", "POWERGRID", "ONGC", "BPCL", "IOC",
    "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP", "HINDUNILVR",

    # High-Beta NSE intraday favorites (best for momentum)
    "INDUSINDBK", "BANDHANBNK", "FEDERALBNK", "PNB", "BANKBARODA",
    "TATASTEEL", "JSWSTEEL", "HINDALCO", "SAIL", "NMDC",
    "ADANIPORTS", "ADANIGREEN", "TATAPOWER", "GAIL", "COALINDIA",
    "ZOMATO", "PAYTM", "NYKAA", "DMART", "IRCTC",

    # Mid-cap movers with good liquidity
    "MUTHOOTFIN", "CHOLAFIN", "BAJAJFINSV", "SBICARD", "HDFCLIFE",
]

# Daily bias watchlist (top 15 for concentrated bets on best day)
NSE_DAILY_BIAS_WATCHLIST = [
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",     # Banking — highest liquidity
    "RELIANCE", "BHARTIARTL", "ITC",                  # Large cap momentum
    "TATAMOTORS", "BAJFINANCE", "INDUSINDBK",          # High beta
    "INFY", "TCS", "WIPRO",                            # IT sector
    "ADANIENT", "TITAN",                               # Volatile movers
]


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_engine: Optional[DailyProfitEngine] = None


def get_profit_engine() -> DailyProfitEngine:
    global _engine
    if _engine is None:
        _engine = DailyProfitEngine()
    return _engine
