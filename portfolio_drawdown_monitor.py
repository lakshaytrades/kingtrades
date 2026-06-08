"""
portfolio_drawdown_monitor.py — Real-Time Equity Curve Guardian

The daily circuit breaker only fires AFTER the daily loss limit is hit.
This monitor provides EARLY WARNING by tracking the intraday equity curve
continuously and alerting at smaller drawdown thresholds — before the position
becomes a full loss.

Monitors:
  - Current equity vs session open equity
  - Rolling peak equity (watermark)
  - Drawdown from peak (%)
  - Drawdown velocity (is it accelerating or slowing?)
  - Consecutive down-periods (degrading setup detection)

Alert thresholds:
  0.75% from peak → WARNING: Tighten stops
  1.00% from peak → CAUTION: Stop new entries
  1.50% from peak → DANGER: Consider reducing positions
  2.00% from peak → EMERGENCY: Same as daily circuit breaker (but caught earlier)

Recovery:
  When equity recovers above 0.5% from peak → resume normal trading

Usage:
  monitor = get_drawdown_monitor()
  monitor.start(risk_manager)           # starts background thread
  status = monitor.get_status()         # DrawdownStatus
  monitor.update_equity(current_equity) # can be called from trading loop too
"""

import logging
import os
import threading
import time as _time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SEC = 60    # check every minute
_WARN_PCT           = 0.75  # % drawdown → warning
_CAUTION_PCT        = 1.00  # % drawdown → stop new entries
_DANGER_PCT         = 1.50  # % drawdown → reduce positions
_EMERGENCY_PCT      = 2.00  # % drawdown → kill switch
_RECOVERY_PCT       = 0.50  # % from peak below which we resume normal
_ALERT_COOLDOWN_SEC = 600   # don't spam the same level for 10 min


@dataclass
class DrawdownStatus:
    current_equity:      float = 0.0
    session_open_equity: float = 0.0
    peak_equity:         float = 0.0
    drawdown_pct:        float = 0.0     # % below peak
    pnl_pct:             float = 0.0     # % vs session open
    velocity:            float = 0.0     # change in drawdown % per minute
    level:               str   = "SAFE"  # SAFE / WARNING / CAUTION / DANGER / EMERGENCY
    allow_new_entries:   bool  = True
    should_reduce:       bool  = False
    should_emergency:    bool  = False
    consecutive_down:    int   = 0
    message:             str   = ""


class DrawdownMonitor:

    def __init__(self):
        self._lock              = threading.Lock()
        self._status            = DrawdownStatus()
        self._thread: Optional[threading.Thread] = None
        self._running           = False
        self._risk_manager      = None

        self._session_open      = 0.0
        self._peak              = 0.0
        self._prev_equity       = 0.0
        self._prev_dd           = 0.0
        self._consecutive_down  = 0
        self._last_alert_level  = "SAFE"
        self._last_alert_ts:    dict = {}   # level → timestamp

    def start(self, risk_manager=None) -> None:
        """Start the background monitoring thread."""
        self._risk_manager = risk_manager
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="drawdown-monitor",
        )
        self._thread.start()
        logger.info("[DrawdownMonitor] Started — checking every 60s")

    def stop(self) -> None:
        self._running = False

    def update_equity(self, current_equity: float) -> DrawdownStatus:
        """Push current equity directly (called from trading loop if available)."""
        with self._lock:
            return self._compute_status(current_equity)

    def get_status(self) -> DrawdownStatus:
        with self._lock:
            return self._status

    def _get_current_equity(self) -> float:
        """Pull equity from risk manager or execution engine."""
        try:
            if self._risk_manager:
                rm = self._risk_manager
                cap   = rm.state.daily_capital
                pnl   = rm.state.daily_pnl
                return float(cap + pnl)
        except Exception:
            pass
        return 0.0

    def _compute_status(self, equity: float) -> DrawdownStatus:
        if equity <= 0:
            return self._status

        # Initialize session open on first update
        if self._session_open <= 0:
            self._session_open = equity
            self._peak = equity
            self._prev_equity = equity
            logger.info(f"[DrawdownMonitor] Session open: ${equity:,.2f}")

        # Update peak
        if equity > self._peak:
            self._peak = equity

        dd_pct   = (self._peak - equity) / self._peak * 100 if self._peak > 0 else 0.0
        pnl_pct  = (equity - self._session_open) / self._session_open * 100 if self._session_open > 0 else 0.0
        velocity = dd_pct - self._prev_dd   # + means drawdown accelerating

        # Consecutive down tracking
        if equity < self._prev_equity:
            self._consecutive_down += 1
        else:
            self._consecutive_down = max(0, self._consecutive_down - 1)

        self._prev_equity = equity
        self._prev_dd     = dd_pct

        # Classify level
        if dd_pct >= _EMERGENCY_PCT:
            level = "EMERGENCY"
        elif dd_pct >= _DANGER_PCT:
            level = "DANGER"
        elif dd_pct >= _CAUTION_PCT:
            level = "CAUTION"
        elif dd_pct >= _WARN_PCT:
            level = "WARNING"
        else:
            level = "SAFE"

        allow_new  = level not in ("CAUTION", "DANGER", "EMERGENCY")
        reduce     = level in ("DANGER", "EMERGENCY")
        emergency  = level == "EMERGENCY"

        msgs = {
            "SAFE":      "✅ Equity healthy",
            "WARNING":   f"⚠️ Drawdown {dd_pct:.2f}% — tighten stops",
            "CAUTION":   f"🟡 Drawdown {dd_pct:.2f}% — NO NEW ENTRIES",
            "DANGER":    f"🔴 Drawdown {dd_pct:.2f}% — REDUCE POSITIONS",
            "EMERGENCY": f"🆘 Drawdown {dd_pct:.2f}% — EMERGENCY CLOSE",
        }

        self._status = DrawdownStatus(
            current_equity      = round(equity, 2),
            session_open_equity = round(self._session_open, 2),
            peak_equity         = round(self._peak, 2),
            drawdown_pct        = round(dd_pct, 3),
            pnl_pct             = round(pnl_pct, 3),
            velocity            = round(velocity, 4),
            level               = level,
            allow_new_entries   = allow_new,
            should_reduce       = reduce,
            should_emergency    = emergency,
            consecutive_down    = self._consecutive_down,
            message             = msgs[level],
        )

        # Send alert if level changed or cooldown expired
        self._maybe_alert(level, dd_pct, equity, pnl_pct)
        return self._status

    def _maybe_alert(self, level: str, dd_pct: float, equity: float, pnl_pct: float) -> None:
        if level == "SAFE":
            if self._last_alert_level not in ("SAFE", ""):
                # Recovery alert
                self._send_telegram(
                    f"✅ <b>DrawdownMonitor: RECOVERED</b>\n"
                    f"Equity: ${equity:,.2f} (+{pnl_pct:+.2f}% session)\n"
                    f"Drawdown reduced to {dd_pct:.2f}% — normal trading resumed"
                )
            self._last_alert_level = "SAFE"
            return

        now = _time.time()
        last = self._last_alert_ts.get(level, 0)
        if now - last < _ALERT_COOLDOWN_SEC and level == self._last_alert_level:
            return  # same level, still in cooldown

        self._last_alert_ts[level] = now
        self._last_alert_level = level

        icons = {"WARNING": "⚠️", "CAUTION": "🟡", "DANGER": "🔴", "EMERGENCY": "🆘"}
        icon  = icons.get(level, "❌")
        self._send_telegram(
            f"{icon} <b>DrawdownMonitor: {level}</b>\n"
            f"Equity: ${equity:,.2f} | Session: {pnl_pct:+.2f}%\n"
            f"Drawdown from peak: {dd_pct:.2f}%\n"
            f"Velocity: {self._status.velocity:+.3f}%/min\n"
            f"Consecutive down periods: {self._consecutive_down}\n"
            f"{'🛑 NEW ENTRIES BLOCKED' if not self._status.allow_new_entries else ''}"
            f"{'🔥 REDUCE POSITIONS NOW' if self._status.should_reduce else ''}"
        )

        if level == "EMERGENCY":
            logger.critical(f"[DrawdownMonitor] EMERGENCY: {dd_pct:.2f}% drawdown from peak")

    def _send_telegram(self, text: str) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat  = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception:
            pass

    def _monitor_loop(self) -> None:
        while self._running:
            try:
                equity = self._get_current_equity()
                if equity > 0:
                    with self._lock:
                        self._compute_status(equity)
            except Exception as e:
                logger.debug(f"[DrawdownMonitor] loop error: {e}")
            _time.sleep(_CHECK_INTERVAL_SEC)


_MONITOR: Optional[DrawdownMonitor] = None


def get_drawdown_monitor() -> DrawdownMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = DrawdownMonitor()
    return _MONITOR
