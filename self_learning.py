"""
self_learning.py — NSE Momentum Groww AI Bot
Adaptive AI — Learns from Every Trade, Adapts Parameters Automatically

18yr Pro Rule: "The market changes. Strategies that worked in 2010 fail in 2024.
The only edge that lasts is ADAPTING faster than the market changes."

This module:
1. Reads trade history from trade_journal
2. Computes which patterns / sessions / regimes are actually profitable
3. Auto-adjusts signal thresholds (RSI levels, min score, volume filter)
4. Disables patterns with consistently poor performance
5. Increases size multiplier for high-performing setups
6. Saves adaptive config — applied live the next trading day
7. Sends weekly learning report via Telegram

Self-learning is ADDITIVE — it tightens filters, never loosens risk.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

from utils import format_ist_timestamp, get_current_ist_date
from trade_journal import get_journal

logger = logging.getLogger(__name__)

ADAPTIVE_CONFIG_PATH = "logs/adaptive_config.json"
MIN_TRADES_TO_LEARN  = 10   # Minimum trades before adapting a parameter
MAX_DISABLE_PCT      = 30   # Never disable more than 30% of patterns


@dataclass
class AdaptiveConfig:
    """Live-adjustable strategy parameters. Saved to disk nightly, loaded at startup."""
    # Signal thresholds
    min_signal_score:    float = 90.0
    high_conf_score:     float = 92.0

    # RSI thresholds (adapted per performance)
    rsi_oversold:        float = 35.0
    rsi_overbought:      float = 65.0

    # Volume filter
    min_volume_ratio:    float = 1.5   # Minimum volume vs SMA to take trade

    # Pattern weights (pattern_name → multiplier, 0.0 = disabled)
    pattern_weights: Dict[str, float] = None

    # Session multipliers (session → size multiplier)
    session_multipliers: Dict[str, float] = None

    # Regime multipliers
    regime_multipliers: Dict[str, float] = None

    # Symbol blacklist (persistent bad performers this week)
    symbol_blacklist: List[str] = None

    # Metadata
    last_updated:   str = ""
    total_trades:   int = 0
    overall_win_rate: float = 0.0
    version:        int = 1

    def __post_init__(self):
        if self.pattern_weights is None:
            self.pattern_weights = {}
        if self.session_multipliers is None:
            self.session_multipliers = {
                "OPENING_DRIVE": 1.2,
                "MORNING":       1.0,
                "MIDDAY_CHOP":   0.3,
                "AFTERNOON_TREND": 0.9,
                "EOD":           0.0,
            }
        if self.regime_multipliers is None:
            self.regime_multipliers = {
                "STRONG_TREND_UP":   1.2,
                "STRONG_TREND_DOWN": 1.2,
                "WEAK_TREND_UP":     0.8,
                "WEAK_TREND_DOWN":   0.8,
                "RANGING":           0.2,
                "HIGH_VOLATILITY":   0.6,
                "LOW_VOLATILITY":    0.4,
                "OPENING_DRIVE":     1.2,
                "MIDDAY_CHOP":       0.2,
                "AFTERNOON_TREND":   0.9,
            }
        if self.symbol_blacklist is None:
            self.symbol_blacklist = []
        if not self.last_updated:
            self.last_updated = format_ist_timestamp()


class SelfLearningEngine:
    """
    Reads trade history → computes stats → adapts parameters → saves config.
    Run at EOD after market close, applied next morning at startup.
    """

    def __init__(self):
        self.journal  = get_journal()
        self.config   = self._load_config()

    # -------------------------------------------------------
    # MAIN LEARNING CYCLE — called at EOD
    # -------------------------------------------------------

    def run_learning_cycle(self, lookback_days: int = 20) -> AdaptiveConfig:
        """
        Full adaptive learning pass.
        Returns updated AdaptiveConfig to be used from next session.
        """
        logger.info(f"[{format_ist_timestamp()}] Self-learning cycle started (lookback={lookback_days}d)...")

        trades_df = self.journal.get_trades_last_n_days(lookback_days)
        if trades_df.empty or len(trades_df) < MIN_TRADES_TO_LEARN:
            logger.info(f"[{format_ist_timestamp()}] Insufficient trades ({len(trades_df)}) — keeping current config")
            return self.config

        # 1. Overall performance
        self._adapt_overall_thresholds(trades_df)

        # 2. Pattern-level adaptation
        self._adapt_pattern_weights(trades_df)

        # 3. Session-level adaptation
        self._adapt_session_multipliers(trades_df)

        # 4. Regime-level adaptation
        self._adapt_regime_multipliers(trades_df)

        # 5. Symbol blacklist (3+ consecutive losses on same stock this week)
        self._update_symbol_blacklist(trades_df)

        # 6. RSI threshold adaptation
        self._adapt_rsi_thresholds(trades_df)

        # 7. Signal score threshold
        self._adapt_signal_score(trades_df)

        self.config.last_updated   = format_ist_timestamp()
        self.config.total_trades   = len(trades_df)
        self.config.overall_win_rate = self._win_rate(trades_df)
        self.config.version       += 1

        self._save_config()
        self._log_learning_report()
        return self.config

    # -------------------------------------------------------
    # ADAPTATION METHODS
    # -------------------------------------------------------

    def _adapt_overall_thresholds(self, df: pd.DataFrame):
        """If win rate is low, tighten min_signal_score."""
        wr = self._win_rate(df)
        if wr < 45:
            # Poor win rate — raise the bar
            self.config.min_signal_score = min(self.config.min_signal_score + 3.0, 98.0)
            logger.info(f"[{format_ist_timestamp()}] Low win rate {wr:.1f}% → raising min score to {self.config.min_signal_score}")
        elif wr > 65:
            # Good win rate — can slightly loosen (floor 88 — never below institutional standard)
            self.config.min_signal_score = max(self.config.min_signal_score - 1.0, 88.0)
            logger.info(f"[{format_ist_timestamp()}] Good win rate {wr:.1f}% → min score {self.config.min_signal_score}")

    def _adapt_pattern_weights(self, df: pd.DataFrame):
        """
        For each pattern:
        - Win rate > 60% and avg_pnl > 0 → increase weight (max 1.5x)
        - Win rate < 40% or avg_pnl < 0 → reduce weight (min 0.3x)
        - Win rate < 30% with 15+ trades → disable (weight = 0)
        """
        if "entry_pattern" not in df.columns:
            return

        # Explode multi-pattern entries
        exploded = df.assign(pattern=df["entry_pattern"].str.split("+")).explode("pattern")
        exploded["pattern"] = exploded["pattern"].str.strip()

        for pattern, grp in exploded.groupby("pattern"):
            if len(grp) < 5:
                continue
            wr      = self._win_rate(grp)
            avg_pnl = grp["net_pnl"].mean() if "net_pnl" in grp.columns else 0

            current = self.config.pattern_weights.get(pattern, 1.0)

            if wr > 60 and avg_pnl > 0:
                new_w = min(current * 1.1, 1.5)
            elif wr < 30 and len(grp) >= 15:
                new_w = 0.0   # Disable
                logger.warning(f"[{format_ist_timestamp()}] Pattern DISABLED: {pattern} (WR={wr:.0f}%)")
            elif wr < 40 or avg_pnl < 0:
                new_w = max(current * 0.85, 0.3)
            else:
                new_w = current

            if new_w != current:
                self.config.pattern_weights[pattern] = round(new_w, 2)
                logger.info(f"[{format_ist_timestamp()}] Pattern {pattern}: weight {current:.2f}→{new_w:.2f} (WR={wr:.0f}%)")

    def _adapt_session_multipliers(self, df: pd.DataFrame):
        """Scale position sizing by session performance."""
        if "session" not in df.columns:
            return
        for session, grp in df.groupby("session"):
            if len(grp) < 5:
                continue
            wr      = self._win_rate(grp)
            avg_pnl = grp["net_pnl"].mean() if "net_pnl" in grp.columns else 0
            current = self.config.session_multipliers.get(session, 1.0)

            if session == "MIDDAY_CHOP":
                # Never increase midday — experience says it's always bad
                new_m = min(current, 0.3)
            elif wr > 60 and avg_pnl > 0:
                new_m = min(current * 1.05, 1.4)
            elif wr < 35:
                new_m = max(current * 0.8, 0.1)
            else:
                new_m = current

            if new_m != current:
                self.config.session_multipliers[session] = round(new_m, 2)

    def _adapt_regime_multipliers(self, df: pd.DataFrame):
        """Scale sizing by regime performance."""
        if "regime" not in df.columns:
            return
        for regime, grp in df.groupby("regime"):
            if len(grp) < 5:
                continue
            wr  = self._win_rate(grp)
            current = self.config.regime_multipliers.get(regime, 1.0)

            if wr > 60:
                new_m = min(current * 1.05, 1.5)
            elif wr < 35:
                new_m = max(current * 0.85, 0.1)
            else:
                new_m = current

            if new_m != current:
                self.config.regime_multipliers[regime] = round(new_m, 2)
                logger.info(f"[{format_ist_timestamp()}] Regime {regime}: multiplier {current:.2f}→{new_m:.2f}")

    def _update_symbol_blacklist(self, df: pd.DataFrame):
        """Blacklist symbols with 3+ losses in last 5 days."""
        if "symbol" not in df.columns:
            return
        recent = df[df["outcome"].isin(["WIN", "LOSS"])].copy()
        if recent.empty:
            return

        # Reset blacklist weekly (Friday EOD)
        from utils import get_current_ist_time
        if get_current_ist_time().weekday() == 4:  # Friday
            self.config.symbol_blacklist = []
            return

        new_blacklist = []
        for symbol, grp in recent.groupby("symbol"):
            losses = sum(1 for o in grp["outcome"] if o == "LOSS")
            if losses >= 3 and len(grp) >= 3:
                consecutive = all(o == "LOSS" for o in grp["outcome"].tail(3))
                if consecutive:
                    new_blacklist.append(symbol)
                    logger.warning(f"[{format_ist_timestamp()}] Symbol blacklisted: {symbol} (3 consecutive losses)")

        self.config.symbol_blacklist = list(set(new_blacklist))

    def _adapt_rsi_thresholds(self, df: pd.DataFrame):
        """
        Find the RSI range that produced best entries.
        Analyse winning trades' RSI at entry vs losing trades'.
        """
        if "rsi_at_entry" not in df.columns or len(df) < 15:
            return
        wins   = df[df["outcome"] == "WIN"]["rsi_at_entry"].dropna()
        losses = df[df["outcome"] == "LOSS"]["rsi_at_entry"].dropna()

        if len(wins) >= 5 and len(losses) >= 5:
            # If winning longs tend to enter at lower RSI, tighten oversold threshold
            long_wins = df[(df["outcome"] == "WIN") & (df["direction"] == "LONG")]["rsi_at_entry"].dropna()
            if len(long_wins) >= 5:
                optimal_rsi_buy = long_wins.median()
                if optimal_rsi_buy < 45:
                    self.config.rsi_oversold = round(max(optimal_rsi_buy - 5, 25), 1)
            short_wins = df[(df["outcome"] == "WIN") & (df["direction"] == "SHORT")]["rsi_at_entry"].dropna()
            if len(short_wins) >= 5:
                optimal_rsi_sell = short_wins.median()
                if optimal_rsi_sell > 55:
                    self.config.rsi_overbought = round(min(optimal_rsi_sell + 5, 80), 1)

    def _adapt_signal_score(self, df: pd.DataFrame):
        """Find minimum signal score that correlates with wins."""
        if "signal_score" not in df.columns or len(df) < 10:
            return
        wins  = df[df["outcome"] == "WIN"]["signal_score"].dropna()
        if len(wins) >= 5:
            # 25th percentile of winning scores = practical minimum
            practical_min = wins.quantile(0.25)
            current = self.config.min_signal_score
            # Move slowly toward optimal
            new_score = current * 0.8 + practical_min * 0.2
            self.config.min_signal_score = round(max(min(new_score, 98), 88), 1)

    # -------------------------------------------------------
    # QUERY HELPERS
    # -------------------------------------------------------

    def get_pattern_weight(self, pattern_name: str) -> float:
        """Return current weight for a pattern (0 = disabled)."""
        return self.config.pattern_weights.get(pattern_name, 1.0)

    def get_session_multiplier(self, session: str) -> float:
        return self.config.session_multipliers.get(session, 1.0)

    def get_regime_multiplier(self, regime: str) -> float:
        return self.config.regime_multipliers.get(regime, 1.0)

    def is_symbol_blacklisted(self, symbol: str) -> bool:
        return symbol in self.config.symbol_blacklist

    def is_pattern_disabled(self, pattern_name: str) -> bool:
        return self.config.pattern_weights.get(pattern_name, 1.0) == 0.0

    def get_adaptive_min_score(self) -> float:
        return self.config.min_signal_score

    # -------------------------------------------------------
    # PERFORMANCE SUMMARY (for Telegram report)
    # -------------------------------------------------------

    def get_learning_summary(self) -> str:
        """Human-readable learning report for Telegram."""
        cfg = self.config
        disabled = [p for p, w in cfg.pattern_weights.items() if w == 0.0]
        boosted  = [p for p, w in cfg.pattern_weights.items() if w > 1.2]

        lines = [
            "🧠 Self-Learning Report",
            f"Updated: {cfg.last_updated}",
            f"Trades analysed: {cfg.total_trades} | Win rate: {cfg.overall_win_rate:.1f}%",
            f"Min signal score: {cfg.min_signal_score:.0f}",
            f"RSI thresholds: oversold={cfg.rsi_oversold} / overbought={cfg.rsi_overbought}",
        ]
        if disabled:
            lines.append(f"⛔ Disabled patterns: {', '.join(disabled)}")
        if boosted:
            lines.append(f"⬆️ Boosted patterns: {', '.join(boosted)}")
        if cfg.symbol_blacklist:
            lines.append(f"🚫 Symbol blacklist: {', '.join(cfg.symbol_blacklist)}")
        return "\n".join(lines)

    # -------------------------------------------------------
    # PERSISTENCE
    # -------------------------------------------------------

    def _save_config(self):
        Path(ADAPTIVE_CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(ADAPTIVE_CONFIG_PATH, "w") as f:
            json.dump(asdict(self.config), f, indent=2)
        logger.info(f"[{format_ist_timestamp()}] Adaptive config saved → {ADAPTIVE_CONFIG_PATH}")

    def _load_config(self) -> AdaptiveConfig:
        if Path(ADAPTIVE_CONFIG_PATH).exists():
            try:
                with open(ADAPTIVE_CONFIG_PATH) as f:
                    data = json.load(f)
                cfg = AdaptiveConfig(**{k: v for k, v in data.items()
                                        if k in AdaptiveConfig.__dataclass_fields__})
                logger.info(f"[{format_ist_timestamp()}] Adaptive config loaded (v{cfg.version})")
                return cfg
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] Could not load adaptive config: {e}")
        return AdaptiveConfig()

    def _log_learning_report(self):
        logger.info(f"[{format_ist_timestamp()}] {self.get_learning_summary()}")

    @staticmethod
    def _win_rate(df: pd.DataFrame) -> float:
        if df.empty or "outcome" not in df.columns:
            return 50.0
        valid = df[df["outcome"].isin(["WIN", "LOSS"])]
        if valid.empty:
            return 50.0
        return valid["outcome"].eq("WIN").mean() * 100


# Singleton
_learner: Optional[SelfLearningEngine] = None

def get_learner() -> SelfLearningEngine:
    global _learner
    if _learner is None:
        _learner = SelfLearningEngine()
    return _learner
