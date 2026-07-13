"""
supervisor.py — SataVector Autonomous Supervisor Agent

Runs alongside the trading bot as a separate process. Responsibilities:
  1. Process watchdog   — restart bot if it crashes (circuit breaker: 5 restarts/hr)
  2. Log error monitor  — detect known error patterns, classify severity
  3. Performance monitor — track P&L, win rate, drawdown; alert if drifting
  4. Health checks       — Alpaca API reachability, data freshness, disk space
  5. Telegram reporter   — push supervisor events to the same Telegram chat

Auto-restart is safe: kills the old PID, relaunches `python main.py`.
Risk-parameter and strategy changes go to Telegram ONLY — never auto-applied.

Run:
    python supervisor.py          # foreground
    nohup python supervisor.py &  # background on VPS
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

# ── Config ──────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_SCRIPT      = "main.py"
LOG_DIR         = Path(os.getenv("LOG_DIR", "logs"))
STATE_FILE      = Path("data/supervisor_state.json")
CAPITAL_FILE    = Path("data/capital.json")

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

MAX_RESTARTS_PER_HOUR = 5     # circuit breaker
RESTART_COOLDOWN_S    = 45    # seconds to wait before restarting
HEALTH_INTERVAL_S     = 60    # how often to run health checks
PERF_INTERVAL_S       = 900   # 15-min performance report interval
LOG_POLL_S            = 5     # tail poll interval
DISK_WARN_MB          = 500   # warn if free disk < 500 MB

# P&L alert thresholds (% of daily capital)
PNL_DANGER_PCT  = -1.5   # alert at -1.5% (loss limit is -2%)
PNL_GREAT_PCT   = 2.0    # celebrate at +2% (on track for >10% month)

# Known error patterns → severity
ERROR_PATTERNS = [
    # (regex, severity, description)
    (r"CRITICAL",                         "CRITICAL", "Critical error in bot"),
    (r"Traceback \(most recent call",     "CRITICAL", "Python exception / crash"),
    (r"ConnectionRefusedError",           "HIGH",     "API connection refused"),
    (r"TimeoutError|ReadTimeout",         "HIGH",     "API timeout"),
    (r"401|Unauthorized",                 "HIGH",     "Auth token expired"),
    (r"qty must be > 0",                  "MEDIUM",   "Zero-qty order attempted"),
    (r"stop price must be",               "MEDIUM",   "Invalid SL price"),
    (r"insufficient funds\|margin",       "HIGH",     "Insufficient funds"),
    (r"market is closed",                 "LOW",      "Traded outside market hours"),
    (r"rate.?limit",                      "MEDIUM",   "API rate limit hit"),
    (r"OSError|PermissionError",          "HIGH",     "Filesystem error"),
]

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [SUPERVISOR] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("supervisor")


# ── Telegram helper ──────────────────────────────────────────────────────────

def tg(msg: str, emoji: str = "🤖") -> None:
    """Send message to Telegram (supervisor channel — same chat as bot)."""
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("Telegram not configured — skipping alert")
        return
    text = f"{emoji} <b>[SUPERVISOR]</b>\n{msg}"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if not resp.ok:
            logger.warning(f"Telegram send failed: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"Telegram error: {e}")


# ── State persistence ────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {"restart_times": [], "total_restarts": 0, "errors_seen": []}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Log file discovery ────────────────────────────────────────────────────────

def _current_log_file() -> Optional[Path]:
    """Return today's trading log file (ET date, same as bot)."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        date_str = now_et.strftime("%Y-%m-%d")
    except Exception:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    candidates = sorted(LOG_DIR.glob(f"trading_{date_str}*.log"), reverse=True)
    return candidates[0] if candidates else None


# ── Process management ───────────────────────────────────────────────────────

class ProcessWatcher:
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.state = _load_state()

    def _restarts_last_hour(self) -> int:
        cutoff = time.time() - 3600
        self.state["restart_times"] = [t for t in self.state["restart_times"] if t > cutoff]
        return len(self.state["restart_times"])

    def start(self) -> None:
        logger.info(f"Starting bot: python {BOT_SCRIPT}")
        self.proc = subprocess.Popen(
            [sys.executable, BOT_SCRIPT],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        logger.info(f"Bot PID: {self.proc.pid}")

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def restart(self, reason: str) -> bool:
        """Restart bot with circuit breaker. Returns True if restarted."""
        if self._restarts_last_hour() >= MAX_RESTARTS_PER_HOUR:
            msg = (
                f"⛔ <b>Circuit breaker tripped!</b>\n"
                f"{MAX_RESTARTS_PER_HOUR} restarts in the last hour.\n"
                f"Reason for latest crash: {reason}\n"
                f"<b>Manual intervention required.</b>"
            )
            logger.critical(msg)
            tg(msg, "🚨")
            return False

        # Kill old process cleanly
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=15)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

            # Verify the process is actually dead before starting a new one.
            # If kill() was swallowed (e.g. PermissionError on an adopted process),
            # starting a new instance causes duplicate live orders.
            if self.proc.poll() is None:
                logger.error(
                    "Cannot kill existing bot process — NOT starting a new instance "
                    "to prevent duplicate live orders. Manual intervention required."
                )
                tg("❌ Bot restart ABORTED — old process still alive. Manual kill required.", "🚨")
                return False

        logger.info(f"Restarting bot in {RESTART_COOLDOWN_S}s — reason: {reason}")
        tg(
            f"🔄 Bot restarting in {RESTART_COOLDOWN_S}s\n"
            f"<b>Reason:</b> {reason}\n"
            f"Restarts this hour: {self._restarts_last_hour() + 1}/{MAX_RESTARTS_PER_HOUR}",
            "⚠️"
        )
        time.sleep(RESTART_COOLDOWN_S)
        self.start()

        self.state["restart_times"].append(time.time())
        self.state["total_restarts"] = self.state.get("total_restarts", 0) + 1
        _save_state(self.state)
        logger.info(f"Bot restarted (PID {self.proc.pid})")
        return True

    def exit_code(self) -> Optional[int]:
        return self.proc.poll() if self.proc else None


# ── Log watcher ─────────────────────────────────────────────────────────────

class LogWatcher:
    def __init__(self):
        self._file: Optional[Path] = None
        self._pos: int = 0
        self._recent_errors: deque = deque(maxlen=50)
        self._last_critical_ts: float = 0

    def _open(self) -> None:
        f = _current_log_file()
        if f and f != self._file:
            self._file = f
            self._pos = f.stat().st_size  # start at end (don't replay old logs)
            logger.info(f"Watching log: {f}")

    def poll(self) -> list[tuple[str, str, str]]:
        """Return list of (severity, pattern_desc, raw_line) for new errors."""
        self._open()
        if not self._file or not self._file.exists():
            return []

        found = []
        try:
            with open(self._file, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._pos)
                new_data = fh.read()
                self._pos = fh.tell()
        except Exception:
            return []

        for line in new_data.splitlines():
            for pattern, severity, desc in ERROR_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    found.append((severity, desc, line.strip()))
                    self._recent_errors.append((time.time(), severity, line.strip()))
                    break  # one match per line

        return found

    def critical_count_last_minute(self) -> int:
        cutoff = time.time() - 60
        return sum(1 for ts, sev, _ in self._recent_errors
                   if ts > cutoff and sev == "CRITICAL")


# ── Performance monitor ───────────────────────────────────────────────────────

class PerfMonitor:
    def __init__(self):
        self._last_report = 0.0
        self._warned_danger = False

    def _read_capital(self) -> dict:
        try:
            if CAPITAL_FILE.exists():
                return json.loads(CAPITAL_FILE.read_text())
        except Exception:
            pass
        return {}

    def check(self) -> None:
        now = time.time()
        if now - self._last_report < PERF_INTERVAL_S:
            return
        self._last_report = now

        cap = self._read_capital()
        if not cap:
            return

        daily_capital = cap.get("daily_capital", 0)
        daily_pnl     = cap.get("daily_pnl", 0)
        daily_trades  = cap.get("daily_trades", 0)
        wins          = cap.get("wins", 0)
        losses        = cap.get("losses", 0)

        if daily_capital <= 0:
            return

        pnl_pct = (daily_pnl / daily_capital) * 100
        wr = (wins / max(wins + losses, 1)) * 100

        pnl_sign = "+" if daily_pnl >= 0 else ""
        emoji = "🟢" if daily_pnl >= 0 else "🔴"

        # P&L danger alert (approaching daily loss limit)
        if pnl_pct <= PNL_DANGER_PCT and not self._warned_danger:
            tg(
                f"⚠️ <b>P&amp;L approaching daily loss limit!</b>\n"
                f"Current: {pnl_sign}${daily_pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)\n"
                f"Limit: -2.0% — bot will stop at limit.\n"
                f"Trades: {daily_trades} | W/L: {wins}/{losses}",
                "⚠️"
            )
            self._warned_danger = True

        # Great day alert
        if pnl_pct >= PNL_GREAT_PCT:
            tg(
                f"🎯 <b>On track for target returns!</b>\n"
                f"P&amp;L: +${daily_pnl:.2f} ({pnl_pct:+.2f}%)\n"
                f"Win Rate: {wr:.0f}% | Trades: {daily_trades}",
                "💰"
            )
            self._warned_danger = False  # reset for next threshold

        # 15-min performance heartbeat (logger only, not Telegram to avoid spam)
        logger.info(
            f"PERF | P&L: {pnl_sign}${daily_pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%) | "
            f"Trades: {daily_trades} | W/L: {wins}/{losses} | WR: {wr:.0f}%"
        )

    def reset_day_flags(self) -> None:
        self._warned_danger = False
        self._last_report = 0.0


# ── Health checker ────────────────────────────────────────────────────────────

class HealthChecker:
    def __init__(self):
        self._last_check = 0.0
        self._alpaca_warned = False
        self._disk_warned   = False

    def check(self) -> list[str]:
        """Run health checks. Returns list of problem descriptions."""
        now = time.time()
        if now - self._last_check < HEALTH_INTERVAL_S:
            return []
        self._last_check = now
        problems = []

        # 1. Alpaca API reachability
        try:
            r = requests.get("https://api.alpaca.markets/v2/clock", timeout=5)
            if r.status_code == 401:
                problems.append("Alpaca API auth failed (401) — token may be expired")
                self._alpaca_warned = True
            else:
                self._alpaca_warned = False
        except Exception as e:
            problems.append(f"Alpaca API unreachable: {e}")
            self._alpaca_warned = True

        # 2. Log file freshness (should be written every ~30s during market hours)
        log = _current_log_file()
        if log and log.exists():
            age = time.time() - log.stat().st_mtime
            try:
                from zoneinfo import ZoneInfo
                now_et = datetime.now(ZoneInfo("America/New_York"))
                mkt_open = now_et.hour >= 9 and now_et.hour < 16
            except Exception:
                mkt_open = True
            if mkt_open and age > 300:  # 5 minutes stale during market hours
                problems.append(f"Log file stale — last write {age/60:.1f} min ago")

        # 3. Disk space
        try:
            import shutil
            free_mb = shutil.disk_usage(".").free / (1024 * 1024)
            if free_mb < DISK_WARN_MB and not self._disk_warned:
                problems.append(f"Low disk space: {free_mb:.0f} MB free")
                self._disk_warned = True
            elif free_mb >= DISK_WARN_MB:
                self._disk_warned = False
        except Exception:
            pass

        return problems


# ── Main supervisor loop ──────────────────────────────────────────────────────

OPTIMIZER_INTERVAL_S = 3600   # run optimizer every hour
EOD_REPORT_HOUR_ET   = 16     # send EOD decision log report at 4 PM ET


class BotSupervisor:
    def __init__(self):
        self.watcher = ProcessWatcher()
        self.log_mon = LogWatcher()
        self.perf    = PerfMonitor()
        self.health  = HealthChecker()
        self._running = True
        self._start_time = time.time()
        self._last_optimizer_run = 0.0
        self._eod_report_sent_date = ""

        signal.signal(signal.SIGINT,  self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, signum, frame):
        logger.info("Supervisor stopping — sending SIGTERM to bot...")
        tg("Supervisor stopped. Bot process will also be terminated.", "🛑")
        if self.watcher.is_alive():
            self.watcher.proc.terminate()
        self._running = False

    def _handle_log_errors(self, errors: list) -> bool:
        """Process log errors. Returns True if a restart is warranted."""
        if not errors:
            return False

        restart_needed = False
        for severity, desc, line in errors:
            logger.warning(f"[{severity}] {desc}: {line[:120]}")

            if severity == "CRITICAL":
                restart_needed = True
                tg(
                    f"💥 <b>CRITICAL error detected</b>\n"
                    f"{desc}\n<code>{line[:200]}</code>",
                    "🚨"
                )
            elif severity == "HIGH":
                tg(
                    f"<b>HIGH severity error</b>\n"
                    f"{desc}\n<code>{line[:150]}</code>",
                    "⚠️"
                )
            # MEDIUM and LOW — log only, no Telegram (prevent spam)

        # Multiple CRITICALs in 60s = definitely restart
        if self.log_mon.critical_count_last_minute() >= 3:
            restart_needed = True

        return restart_needed

    def run(self) -> None:
        logger.info("=" * 60)
        logger.info("SataVector Autonomous Supervisor started")
        logger.info(f"Monitoring: {BOT_SCRIPT}")
        logger.info(f"Circuit breaker: max {MAX_RESTARTS_PER_HOUR} restarts/hr")
        logger.info("=" * 60)

        tg(
            f"Supervisor online\n"
            f"Watching: <code>{BOT_SCRIPT}</code>\n"
            f"Circuit breaker: {MAX_RESTARTS_PER_HOUR} restarts/hr max",
            "🟢"
        )

        # Start the bot
        self.watcher.start()

        last_alive_check = time.time()

        while self._running:
            time.sleep(LOG_POLL_S)

            # 1. Check if bot is still alive
            now = time.time()
            if now - last_alive_check >= 10:
                last_alive_check = now
                if not self.watcher.is_alive():
                    code = self.watcher.exit_code()
                    reason = f"Bot exited with code {code}"
                    logger.error(reason)
                    ok = self.watcher.restart(reason)
                    if not ok:
                        logger.critical("Circuit breaker active — supervisor sleeping, bot NOT running")
                        # Keep looping so we can still handle SIGTERM
                        time.sleep(60)
                        continue

            # 2. Scan new log lines for errors
            errors = self.log_mon.poll()
            restart_needed = self._handle_log_errors(errors)
            if restart_needed and self.watcher.is_alive():
                self.watcher.restart("Critical error detected in logs")

            # 3. Health checks
            problems = self.health.check()
            for problem in problems:
                logger.warning(f"HEALTH: {problem}")
                tg(f"<b>Health check failed:</b> {problem}", "⚠️")

            # 4. Performance monitoring
            self.perf.check()

            # 5. Autonomous optimizer — adjust parameters hourly
            now = time.time()
            if now - self._last_optimizer_run >= OPTIMIZER_INTERVAL_S:
                self._last_optimizer_run = now
                self._run_optimizer()

            # 6. EOD decision log report — sent once after 4 PM ET
            self._maybe_send_eod_report()


    def _run_optimizer(self) -> None:
        """Run parameter optimizer and send Telegram update."""
        try:
            from autonomous_optimizer import analyze_and_adjust
            _cfg, report = analyze_and_adjust()
            logger.info(f"Optimizer ran — report:\n{report}")
            tg(report, "🤖")
        except Exception as e:
            logger.warning(f"Optimizer failed: {e}")

    def _maybe_send_eod_report(self) -> None:
        """Send comprehensive EOD decision log report once per day after market close."""
        try:
            from zoneinfo import ZoneInfo
            now_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now_et = datetime.utcnow()

        today = now_et.strftime("%Y-%m-%d")
        if now_et.hour < EOD_REPORT_HOUR_ET:
            return
        if self._eod_report_sent_date == today:
            return

        self._eod_report_sent_date = today
        try:
            from decision_log import build_daily_report
            from autonomous_optimizer import get_monthly_progress, _read_capital
            report = build_daily_report(today)

            # Add monthly progress tracker
            cap = _read_capital()
            daily_capital = cap.get("daily_capital", 0)
            month_pnl = cap.get("month_pnl", cap.get("daily_pnl", 0))
            if daily_capital > 0:
                progress = get_monthly_progress(daily_capital, month_pnl)
                pace_emoji = "🟢" if progress["on_track"] else "🟡"
                report += (
                    f"\n\n🎯 <b>Monthly Target Progress (13%)</b>\n"
                    f"  Current: {progress['current_pct']:+.2f}%\n"
                    f"  On-pace: {pace_emoji} {progress['on_pace_pct']:+.2f}%\n"
                    f"  Days elapsed: {progress['days_elapsed']}/{22}\n"
                    f"  Need per remaining day: ${progress['needed_per_day']:+.2f}"
                )

            tg(report, "📊")
            logger.info("EOD decision report sent via Telegram")
        except Exception as e:
            logger.warning(f"EOD report failed: {e}")


def main():
    Path("data").mkdir(exist_ok=True)
    sup = BotSupervisor()
    sup.run()


if __name__ == "__main__":
    main()
