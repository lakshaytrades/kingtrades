"""
autonomous_optimizer.py — KingTrades Self-Optimizing Parameter Engine

Reads decision_log + trade_outcomes, identifies bottlenecks, and writes
adjusted parameters to data/optimizer_config.json which main.py reads
at the start of each trading day.

TARGET: 13% per month ≈ 0.59% per trading day (22 trading days).

Safe adjustment bounds — NEVER crosses these hard limits:
  min_score:      68–80   (never trade blind, never miss good setups)
  volume_ratio:   0.3–1.0 (0.3 = absolute minimum liquidity)
  max_positions:  3–10
  risk_per_trade: 0.3%–1.5% of capital
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except ImportError:
    from pytz import timezone as ZoneInfo
    _ET = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

CONFIG_FILE   = Path("data/optimizer_config.json")
CAPITAL_FILE  = Path("data/capital.json")
TRADING_DAYS_PER_MONTH = 22

# ── Target tracking ────────────────────────────────────────────────────────
MONTHLY_TARGET_PCT = 13.0
DAILY_TARGET_PCT   = MONTHLY_TARGET_PCT / TRADING_DAYS_PER_MONTH  # ~0.59%

# ── Safe bounds for each adjustable parameter ──────────────────────────────
BOUNDS: Dict[str, Tuple[float, float]] = {
    "min_score":         (68.0, 80.0),
    "volume_ratio_min":  (0.3,  1.0),
    "max_positions":     (3,    10),
    "risk_per_trade_pct":(0.3,  1.5),
}

# ── Step sizes for each adjustment (small, conservative) ──────────────────
STEPS: Dict[str, float] = {
    "min_score":         1.0,
    "volume_ratio_min":  0.05,
    "max_positions":     1,
    "risk_per_trade_pct":0.1,
}

# ── Defaults (these are what the bot uses when no optimizer_config.json exists)
DEFAULTS: Dict[str, float] = {
    "min_score":         72.0,
    "volume_ratio_min":  0.5,
    "max_positions":     7,
    "risk_per_trade_pct":0.75,
}


def _clamp(val: float, key: str) -> float:
    lo, hi = BOUNDS[key]
    return max(lo, min(hi, val))


def _load_config() -> Dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except Exception:
        pass
    return dict(DEFAULTS)


def _save_config(cfg: Dict, changes: Dict) -> None:
    CONFIG_FILE.parent.mkdir(exist_ok=True)
    cfg["last_updated"] = datetime.now(_ET).isoformat()
    cfg["changes_log"]  = cfg.get("changes_log", [])
    if changes:
        cfg["changes_log"].append({
            "ts":      cfg["last_updated"],
            "changes": changes,
        })
        cfg["changes_log"] = cfg["changes_log"][-30:]  # keep last 30 changes
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def _read_capital() -> Dict:
    try:
        if CAPITAL_FILE.exists():
            return json.loads(CAPITAL_FILE.read_text())
    except Exception:
        pass
    return {}


def _get_daily_stats(days: int = 5) -> Dict:
    """Pull stats from decision_log for last N days."""
    try:
        from decision_log import get_gate_block_rates, get_today_stats
        block_rates = get_gate_block_rates(days=days)
        today = get_today_stats()
        return {"block_rates": block_rates, "today": today}
    except Exception as e:
        logger.debug(f"Stats read failed: {e}")
        return {}


def analyze_and_adjust() -> Tuple[Dict, str]:
    """
    Core optimizer: analyze recent performance → adjust parameters.
    Returns (new_config, report_text).
    """
    cfg     = _load_config()
    cap     = _read_capital()
    stats   = _get_daily_stats(days=5)
    changes = {}
    notes   = []

    daily_pnl     = cap.get("daily_pnl", 0)
    # total_equity = actual Alpaca account balance (set by main.py at EOD)
    total_equity  = cap.get("total_equity", cap.get("daily_capital", 1))
    daily_capital = total_equity   # track ROI on actual account equity
    daily_trades  = cap.get("daily_trades", 0)
    wins          = cap.get("wins", 0)
    losses        = cap.get("losses", 0)
    win_rate      = wins / max(wins + losses, 1)
    pnl_pct       = (daily_pnl / max(daily_capital, 1)) * 100
    # Dynamic daily target: 13% of actual equity / 22 trading days
    _dynamic_daily_target_pct = MONTHLY_TARGET_PCT / TRADING_DAYS_PER_MONTH

    block_rates = stats.get("block_rates", {})
    today_stats = stats.get("today", {})
    total_scanned = today_stats.get("total_scanned", 0)
    taken         = today_stats.get("taken", 0)
    pass_rate     = taken / max(total_scanned, 1)

    # ── Rule 1: Too few trades (pass rate < 10% and < 2 trades today) ──────
    # Problem: filters too strict → missing opportunities
    if total_scanned >= 10 and pass_rate < 0.10 and taken < 2:
        # Which gate is blocking the most?
        dominant_block = max(block_rates, key=lambda k: block_rates.get(k, 0), default=None)

        if dominant_block == "score" and block_rates.get("score", 0) > 30:
            old = cfg.get("min_score", DEFAULTS["min_score"])
            new = _clamp(old - STEPS["min_score"], "min_score")
            if new < old:
                cfg["min_score"] = new
                changes["min_score"] = {"from": old, "to": new}
                notes.append(f"Score gate blocking {block_rates['score']:.0f}% of signals → lowered threshold {old:.0f}→{new:.0f}")

        if dominant_block == "haf" and block_rates.get("haf", 0) > 25:
            old = cfg.get("volume_ratio_min", DEFAULTS["volume_ratio_min"])
            new = _clamp(old - STEPS["volume_ratio_min"], "volume_ratio_min")
            if new < old:
                cfg["volume_ratio_min"] = new
                changes["volume_ratio_min"] = {"from": old, "to": new}
                notes.append(f"HAF blocking {block_rates['haf']:.0f}% → relaxed volume min {old:.2f}→{new:.2f}x")

    # ── Rule 2: Win rate < 45% → tighten score threshold ───────────────────
    # Problem: taking too many low-quality trades
    if (wins + losses) >= 5 and win_rate < 0.45:
        old = cfg.get("min_score", DEFAULTS["min_score"])
        new = _clamp(old + STEPS["min_score"], "min_score")
        if new > old:
            cfg["min_score"] = new
            changes["min_score"] = {"from": old, "to": new}
            notes.append(f"Win rate {win_rate*100:.0f}% < 45% → raised score threshold {old:.0f}→{new:.0f}")

    # ── Rule 3: Win rate > 65% → we can be more aggressive (more trades) ───
    if (wins + losses) >= 5 and win_rate > 0.65 and daily_trades < 4:
        old = cfg.get("max_positions", DEFAULTS["max_positions"])
        new = int(_clamp(old + STEPS["max_positions"], "max_positions"))
        if new > old:
            cfg["max_positions"] = new
            changes["max_positions"] = {"from": old, "to": new}
            notes.append(f"WR {win_rate*100:.0f}%+ and low trade count → max_positions {old}→{new}")

    # ── Rule 4: P&L behind target → slightly increase risk per trade ────────
    # Only if win rate is healthy (> 55%)
    if win_rate > 0.55 and pnl_pct < _dynamic_daily_target_pct * 0.5 and daily_trades >= 2:
        old = cfg.get("risk_per_trade_pct", DEFAULTS["risk_per_trade_pct"])
        new = _clamp(old + STEPS["risk_per_trade_pct"], "risk_per_trade_pct")
        if new > old:
            cfg["risk_per_trade_pct"] = new
            changes["risk_per_trade_pct"] = {"from": old, "to": new}
            notes.append(f"P&L {pnl_pct:.2f}% behind {DAILY_TARGET_PCT:.2f}% target → risk/trade {old:.1f}→{new:.1f}%")

    # ── Rule 5: P&L ahead of target + losing streak → reduce risk ───────────
    consecutive_losses = cap.get("consecutive_losses", 0)
    if consecutive_losses >= 3:
        old = cfg.get("risk_per_trade_pct", DEFAULTS["risk_per_trade_pct"])
        new = _clamp(old - STEPS["risk_per_trade_pct"], "risk_per_trade_pct")
        if new < old:
            cfg["risk_per_trade_pct"] = new
            changes["risk_per_trade_pct"] = {"from": old, "to": new}
            notes.append(f"{consecutive_losses} consecutive losses → reduced risk/trade {old:.1f}→{new:.1f}%")

    # ── Rule 6: Great P&L day → lock it in (tighten risk for rest of day) ──
    if pnl_pct >= _dynamic_daily_target_pct * 2 and daily_trades >= 3:
        old = cfg.get("risk_per_trade_pct", DEFAULTS["risk_per_trade_pct"])
        new = _clamp(old - STEPS["risk_per_trade_pct"] * 2, "risk_per_trade_pct")
        if new < old:
            cfg["risk_per_trade_pct"] = new
            changes["risk_per_trade_pct"] = {"from": old, "to": new}
            notes.append(f"Great day {pnl_pct:.2f}% → protecting gains, risk/trade {old:.1f}→{new:.1f}%")

    _save_config(cfg, changes)

    # Build report text
    report_lines = ["🤖 <b>Autonomous Optimizer Update</b>"]
    if changes:
        report_lines.append(f"✏️ <b>{len(changes)} parameter(s) adjusted:</b>")
        for note in notes:
            report_lines.append(f"  • {note}")
    else:
        report_lines.append("✅ All parameters within optimal range — no changes needed")

    daily_target_usd = daily_capital * _dynamic_daily_target_pct / 100
    month_pnl = cap.get("month_pnl", daily_pnl)
    monthly_target_usd = daily_capital * MONTHLY_TARGET_PCT / 100
    _days_elapsed = max(1, int(datetime.now(_ET).day * 5 / 7))
    monthly_pace_pct = (month_pnl / max(daily_capital, 1)) * 100 * (TRADING_DAYS_PER_MONTH / _days_elapsed)
    report_lines += [
        f"\n<b>Current params:</b>",
        f"  Min score:    {cfg.get('min_score', DEFAULTS['min_score']):.0f}",
        f"  Vol min:      {cfg.get('volume_ratio_min', DEFAULTS['volume_ratio_min']):.2f}x",
        f"  Max positions:{int(cfg.get('max_positions', DEFAULTS['max_positions']))}",
        f"  Risk/trade:   {cfg.get('risk_per_trade_pct', DEFAULTS['risk_per_trade_pct']):.2f}%",
        f"\n<b>Today vs 13% target:</b>",
        f"  Capital:     ${daily_capital:,.0f}",
        f"  Daily target:${daily_target_usd:,.2f} ({_dynamic_daily_target_pct:.2f}%/day)",
        f"  Today P&L:   {'+' if daily_pnl >= 0 else ''}{daily_pnl:.2f} ({pnl_pct:+.2f}%)",
        f"  Month P&L:   ${month_pnl:+,.2f} / target ${monthly_target_usd:+,.0f}",
        f"  WR: {win_rate*100:.0f}%  Trades: {daily_trades}  Pass rate: {pass_rate*100:.0f}%",
    ]

    return cfg, "\n".join(report_lines)


def load_optimizer_config() -> Dict:
    """Called by main.py at startup to apply optimized parameters."""
    cfg = _load_config()
    logger.info(
        f"[Optimizer] Loaded: min_score={cfg.get('min_score', DEFAULTS['min_score']):.0f} "
        f"vol_min={cfg.get('volume_ratio_min', DEFAULTS['volume_ratio_min']):.2f}x "
        f"max_pos={cfg.get('max_positions', DEFAULTS['max_positions'])} "
        f"risk={cfg.get('risk_per_trade_pct', DEFAULTS['risk_per_trade_pct']):.2f}%"
    )
    return cfg


def get_monthly_progress(daily_capital: float, current_month_pnl: float) -> Dict:
    """How far along are we to 13% monthly target?"""
    now_et = datetime.now(_ET)
    # Estimate trading days elapsed this month (approx weekdays)
    day_of_month = now_et.day
    trading_days_elapsed = max(1, int(day_of_month * 5 / 7))
    trading_days_remaining = max(0, TRADING_DAYS_PER_MONTH - trading_days_elapsed)

    target_pnl  = daily_capital * (MONTHLY_TARGET_PCT / 100)
    current_pct = (current_month_pnl / max(daily_capital, 1)) * 100
    on_pace_pct = (current_month_pnl / max(trading_days_elapsed, 1)) * TRADING_DAYS_PER_MONTH / max(daily_capital, 1) * 100

    return {
        "target_pnl":         round(target_pnl, 2),
        "current_pnl":        round(current_month_pnl, 2),
        "current_pct":        round(current_pct, 2),
        "on_pace_pct":        round(on_pace_pct, 2),
        "days_elapsed":       trading_days_elapsed,
        "days_remaining":     trading_days_remaining,
        "needed_per_day":     round((target_pnl - current_month_pnl) / max(trading_days_remaining, 1), 2),
        "on_track":           on_pace_pct >= MONTHLY_TARGET_PCT * 0.85,  # within 15% of pace
    }
