"""
advanced_sizing_india.py — Calmar + Omega Ratio Position Sizing

Renaissance and top quant funds scale position sizes dynamically based on
risk-adjusted performance metrics, not just Kelly criterion alone.

Two metrics:
1. Calmar Ratio = annualized_return / max_drawdown
   - Calmar > 2.5: exceptional → scale up to 1.3x Kelly
   - Calmar 1.5-2.5: good → 1.1x Kelly
   - Calmar 0.8-1.5: normal → 1.0x Kelly
   - Calmar 0.3-0.8: degrading → 0.8x Kelly
   - Calmar < 0.3: poor → 0.6x Kelly (defense mode)

2. Omega Ratio = sum(gains above threshold) / sum(losses below threshold)
   Threshold = 0 (break-even). Omega > 1.5 is very good.
   - Omega > 2.0: 1.2x Kelly
   - Omega 1.5-2.0: 1.1x Kelly
   - Omega 1.0-1.5: 1.0x Kelly
   - Omega < 1.0: 0.8x Kelly

Combined multiplier = min(calmar_mult * omega_mult, 1.5)  # cap at 1.5x

Also provides max_positions scaling:
- Combined_mult > 1.2: max_positions = 7
- Combined_mult 0.9-1.2: max_positions = 5
- Combined_mult < 0.9: max_positions = 3

State: stored in JSONL file at logs/india_sizing_state.jsonl
Resets at start of each new trading day (but not between signals).
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger("advanced_sizing")

_STATE_FILE = Path(__file__).parent.parent / "logs" / "india_sizing_state.jsonl"

# Module-level state cache
_trades: list = []         # list of dicts: {pnl_pct, peak_capital, current_capital, ts, date}
_peak_today: float = 0.0
_last_date: str = ""


def _ensure_logs_dir() -> None:
    """Create logs directory if it doesn't exist."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _load_state() -> None:
    """Load trade history from JSONL file into module-level cache."""
    global _trades, _peak_today, _last_date
    try:
        _ensure_logs_dir()
        if not _STATE_FILE.exists():
            return
        today = _today_ist()
        loaded: list = []
        with open(_STATE_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    # Only load today's trades
                    if rec.get("date") == today:
                        loaded.append(rec)
                except Exception:
                    continue
        _trades = loaded
        _last_date = today
        # Restore peak from today's records
        if loaded:
            _peak_today = max(r.get("peak_capital", 0.0) for r in loaded)
    except Exception as e:
        logger.debug(f"advanced_sizing._load_state: {e}")


def _save_trade(rec: dict) -> None:
    """Append a single trade record to the JSONL file."""
    try:
        _ensure_logs_dir()
        with open(_STATE_FILE, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        logger.debug(f"advanced_sizing._save_trade: {e}")


def record_trade(pnl_pct: float, peak_capital: float, current_capital: float) -> None:
    """
    Record a completed trade's outcome for Calmar/Omega tracking.

    Args:
        pnl_pct: Trade P&L as a fraction of trade value (e.g. 0.012 = +1.2%, -0.008 = -0.8%)
        peak_capital: Peak capital achieved today (for drawdown tracking)
        current_capital: Current capital after this trade
    """
    global _trades, _peak_today, _last_date
    try:
        today = _today_ist()
        # Reset if new day
        if _last_date != today:
            reset_daily_state()
            _last_date = today

        # Update peak
        if current_capital > _peak_today:
            _peak_today = current_capital
        # Use provided peak if higher
        if peak_capital > _peak_today:
            _peak_today = peak_capital

        rec = {
            "pnl_pct": float(pnl_pct),
            "peak_capital": float(_peak_today),
            "current_capital": float(current_capital),
            "ts": datetime.now(IST).isoformat(),
            "date": today,
        }
        _trades.append(rec)
        _save_trade(rec)
        logger.debug(
            f"advanced_sizing: recorded trade pnl={pnl_pct:.3%} "
            f"peak={_peak_today:,.0f} current={current_capital:,.0f}"
        )
    except Exception as e:
        logger.debug(f"advanced_sizing.record_trade: {e}")


def get_calmar_ratio(n_trades: int = 20) -> float:
    """
    Rolling Calmar ratio from last n_trades.

    Calmar = annualised_return / max_drawdown_pct

    For intraday, we use:
      - annualised_return: sum of pnl_pct scaled to ~1000 trades/year
      - max_drawdown: peak-to-trough drawdown seen in recent capital series

    Returns 1.0 (neutral) if fewer than 5 trades are available.
    """
    global _trades
    try:
        # Ensure state is loaded
        if not _trades and _last_date == "":
            _load_state()

        recent = _trades[-n_trades:] if len(_trades) >= n_trades else _trades[:]
        if len(recent) < 5:
            return 1.0

        returns = [r["pnl_pct"] for r in recent]
        capital_series = [r["current_capital"] for r in recent]
        peak_series = [r["peak_capital"] for r in recent]

        # Annualised return: scale sample sum to 1000 trades/year
        total_ret = sum(returns)
        n = len(returns)
        annualised_ret = total_ret * (1000.0 / max(n, 1))

        # Max drawdown: peak to trough over the capital series
        max_dd = 0.0
        for i, cap in enumerate(capital_series):
            peak = peak_series[i]
            if peak > 0:
                dd = (peak - cap) / peak
                if dd > max_dd:
                    max_dd = dd

        if max_dd <= 0.0001:
            # No drawdown: excellent performance
            return 5.0

        calmar = annualised_ret / max_dd
        return max(0.0, calmar)

    except Exception as e:
        logger.debug(f"advanced_sizing.get_calmar_ratio: {e}")
        return 1.0


def get_omega_ratio(n_trades: int = 20, threshold: float = 0.0) -> float:
    """
    Rolling Omega ratio from last n_trades.

    Omega = sum(max(r - threshold, 0)) / sum(max(threshold - r, 0))

    Threshold = 0 (break-even). Omega > 1.5 is very good.
    Returns 1.0 (neutral) if fewer than 5 trades are available.
    """
    global _trades
    try:
        # Ensure state is loaded
        if not _trades and _last_date == "":
            _load_state()

        recent = _trades[-n_trades:] if len(_trades) >= n_trades else _trades[:]
        if len(recent) < 5:
            return 1.0

        returns = [r["pnl_pct"] for r in recent]

        gains = sum(max(r - threshold, 0.0) for r in returns)
        losses = sum(max(threshold - r, 0.0) for r in returns)

        if losses <= 1e-10:
            # No losses above threshold: perfect ratio
            return 5.0 if gains > 0 else 1.0

        omega = gains / losses
        return max(0.0, omega)

    except Exception as e:
        logger.debug(f"advanced_sizing.get_omega_ratio: {e}")
        return 1.0


def get_sizing_multiplier() -> Tuple[float, int, str]:
    """
    Compute combined Calmar + Omega sizing multiplier.

    Returns:
        Tuple of (kelly_multiplier, max_positions, reason_string)

    Fail-open: returns (1.0, 5, "DEFAULT") on any error.
    """
    try:
        # Ensure state is loaded
        if not _trades and _last_date == "":
            _load_state()

        # Not enough data: return neutral
        if len(_trades) < 5:
            return (1.0, 5, "DEFAULT (insufficient data)")

        calmar = get_calmar_ratio()
        omega = get_omega_ratio()

        # Calmar multiplier
        if calmar > 2.5:
            calmar_mult = 1.3
            calmar_label = f"Calmar={calmar:.2f} EXCEPTIONAL"
        elif calmar >= 1.5:
            calmar_mult = 1.1
            calmar_label = f"Calmar={calmar:.2f} GOOD"
        elif calmar >= 0.8:
            calmar_mult = 1.0
            calmar_label = f"Calmar={calmar:.2f} NORMAL"
        elif calmar >= 0.3:
            calmar_mult = 0.8
            calmar_label = f"Calmar={calmar:.2f} DEGRADING"
        else:
            calmar_mult = 0.6
            calmar_label = f"Calmar={calmar:.2f} DEFENSE"

        # Omega multiplier
        if omega > 2.0:
            omega_mult = 1.2
            omega_label = f"Omega={omega:.2f} STRONG"
        elif omega >= 1.5:
            omega_mult = 1.1
            omega_label = f"Omega={omega:.2f} GOOD"
        elif omega >= 1.0:
            omega_mult = 1.0
            omega_label = f"Omega={omega:.2f} NEUTRAL"
        else:
            omega_mult = 0.8
            omega_label = f"Omega={omega:.2f} WEAK"

        # Combined multiplier capped at 1.5x
        combined = min(calmar_mult * omega_mult, 1.5)

        # Max positions based on combined multiplier
        if combined > 1.2:
            max_positions = 7
        elif combined >= 0.9:
            max_positions = 5
        else:
            max_positions = 3

        reason = f"{calmar_label} | {omega_label} -> {combined:.2f}x"
        return (round(combined, 4), max_positions, reason)

    except Exception as e:
        logger.debug(f"advanced_sizing.get_sizing_multiplier: {e}")
        return (1.0, 5, "DEFAULT")


def get_daily_drawdown_pct(capital: float, peak_capital: float) -> float:
    """
    Current drawdown percentage from peak today.

    Args:
        capital: Current capital value
        peak_capital: Peak capital achieved today

    Returns:
        Drawdown as a positive percentage (e.g. 2.5 = 2.5% drawdown)
    """
    try:
        if peak_capital <= 0:
            return 0.0
        dd = (peak_capital - capital) / peak_capital * 100.0
        return max(0.0, dd)
    except Exception:
        return 0.0


def reset_daily_state() -> None:
    """
    Reset intraday state at market open.
    Called once per trading day before the first scan.
    Clears the in-memory trade list and peak tracking for the new session.
    The JSONL file is preserved for historical reference but today's data restarts.
    """
    global _trades, _peak_today, _last_date
    try:
        _trades = []
        _peak_today = 0.0
        _last_date = _today_ist()
        logger.info("advanced_sizing: daily state reset for %s", _last_date)
    except Exception as e:
        logger.debug(f"advanced_sizing.reset_daily_state: {e}")
