"""
watchdog.py — NSE Momentum Groww AI Bot
Fully Automatic Process Watchdog

This is the ONLY file you need to run. It handles everything:
  ✅ Starts main.py automatically
  ✅ Restarts main.py if it crashes
  ✅ Skips weekends (no market = no bot)
  ✅ Skips NSE holidays automatically
  ✅ Sends Telegram alert if bot crashes
  ✅ Runs 24/7 in background — main.py manages its own schedule
  ✅ Logs everything to logs/watchdog.log

Usage:
  python watchdog.py          # Start the watchdog (keeps running forever)
  python watchdog.py --once   # Run main.py once without auto-restart (testing)
  python watchdog.py --status # Show current bot status
"""

import argparse
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Paths ─────────────────────────────────────────────────────────
BOT_DIR   = Path(__file__).parent.resolve()
MAIN_PY   = BOT_DIR / "main.py"
VENV_PY   = (
    BOT_DIR / "venv" / "Scripts" / "python.exe"  # Windows
    if platform.system() == "Windows"
    else BOT_DIR / "venv" / "bin" / "python"      # Linux/Mac
)
PYTHON    = str(VENV_PY) if VENV_PY.exists() else sys.executable
LOGS_DIR  = BOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
STATUS_FILE = BOT_DIR / "data" / "watchdog_status.json"
STATUS_FILE.parent.mkdir(exist_ok=True)

IST = ZoneInfo("Asia/Kolkata")

# ── NSE Holidays 2025-2026 (add more as announced) ────────────────
NSE_HOLIDAYS = {
    # 2025
    "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31",
    "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
    "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-02",
    "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25",
    # 2026
    "2026-01-26", "2026-03-02", "2026-03-20", "2026-04-03",
    "2026-04-14", "2026-04-17", "2026-05-01", "2026-07-17",
    "2026-08-15", "2026-09-04", "2026-10-01", "2026-10-09",
    "2026-10-28", "2026-11-06", "2026-12-25",
}

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOGS_DIR / "watchdog.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("watchdog")


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def ist_now() -> datetime:
    return datetime.now(tz=IST)


def is_trading_day() -> bool:
    """True if today is a weekday and not an NSE holiday."""
    now = ist_now()
    if now.weekday() >= 5:           # Saturday=5, Sunday=6
        return False
    date_str = now.strftime("%Y-%m-%d")
    return date_str not in NSE_HOLIDAYS


def seconds_until(target_hour: int, target_min: int = 0) -> float:
    """Seconds until target time TODAY in IST (negative if already past)."""
    now = ist_now()
    target = now.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
    return (target - now).total_seconds()


def seconds_until_next_weekday_morning() -> float:
    """Seconds until 8:40 AM IST on the next trading day."""
    now = ist_now()
    candidate = now + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
        candidate += timedelta(days=1)
    target = candidate.replace(hour=8, minute=40, second=0, microsecond=0)
    return (target - now).total_seconds()


def send_telegram(message: str) -> None:
    """Send a Telegram message without importing the full bot stack."""
    try:
        from dotenv import load_dotenv
        import requests as req
        load_dotenv(BOT_DIR / ".env")
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass  # Never let Telegram failure break the watchdog


def save_status(state: str, pid: int = 0, crashes: int = 0, last_start: str = "") -> None:
    data = {
        "state":      state,
        "pid":        pid,
        "crashes":    crashes,
        "last_start": last_start,
        "updated":    ist_now().strftime("%Y-%m-%d %H:%M:%S IST"),
    }
    try:
        STATUS_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def load_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────
# MAIN WATCHDOG LOOP
# ─────────────────────────────────────────────────────────────────

class Watchdog:
    MAX_CRASH_BACKOFF = 300   # Max 5-minute wait between restarts
    CRASH_ALERT_THRESHOLD = 3 # Alert on Telegram after 3 crashes

    def __init__(self, once: bool = False):
        self.once       = once
        self.crashes    = 0
        self.running    = True
        self.process: subprocess.Popen | None = None
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT,  self._handle_signal)

    def _handle_signal(self, signum, frame):
        log.info(f"Watchdog received signal {signum} — shutting down gracefully")
        self.running = False
        if self.process and self.process.poll() is None:
            log.info("Terminating main.py process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
        save_status("STOPPED")
        sys.exit(0)

    def _wait_if_weekend_or_holiday(self) -> None:
        """Sleep until next trading day if needed."""
        if is_trading_day():
            return
        now = ist_now()
        secs = seconds_until_next_weekday_morning()
        wake = ist_now() + timedelta(seconds=secs)
        day_type = "weekend" if now.weekday() >= 5 else "NSE holiday"
        log.info(
            f"Today ({now.strftime('%A %Y-%m-%d')}) is a {day_type}. "
            f"Sleeping until {wake.strftime('%A %Y-%m-%d %H:%M')} IST "
            f"({secs/3600:.1f} hours)"
        )
        save_status(f"SLEEPING — {day_type}")
        send_telegram(
            f"🌙 <b>KingTrades Watchdog</b>\n"
            f"Today is a {day_type}. Bot will resume on\n"
            f"<b>{wake.strftime('%A, %d %b %Y at %H:%M IST')}</b>"
        )
        # Sleep in 1-hour chunks so we re-check (in case holiday list updates)
        while secs > 0 and self.running:
            sleep_chunk = min(3600, secs)
            time.sleep(sleep_chunk)
            secs -= sleep_chunk
            if not is_trading_day():
                secs = seconds_until_next_weekday_morning()

    def _wait_for_trading_time(self) -> None:
        """If it's before 8:40 AM IST, wait."""
        secs = seconds_until(8, 40)
        if secs > 0:
            wake = ist_now() + timedelta(seconds=secs)
            log.info(
                f"Market prep time (8:40 AM IST) is "
                f"{secs/60:.0f} minutes away. "
                f"Sleeping until {wake.strftime('%H:%M IST')}"
            )
            save_status("WAITING — pre-market")
            time.sleep(secs)

    def _wait_after_market_close(self) -> None:
        """After market close, wait until next trading day."""
        secs = seconds_until_next_weekday_morning()
        wake = ist_now() + timedelta(seconds=secs)
        log.info(
            f"Market day complete. Sleeping until "
            f"{wake.strftime('%A %Y-%m-%d %H:%M IST')} "
            f"({secs/3600:.1f} hours)"
        )
        save_status("SLEEPING — post-market")
        # Sleep in chunks
        while secs > 0 and self.running:
            time.sleep(min(3600, secs))
            secs -= 3600

    def _preflight_check(self) -> bool:
        """
        Quick sanity checks before launching main.py:
        1. Token cache age — if >20h, trigger a pre-emptive refresh so main.py
           doesn't start with a stale token.
        2. Connectivity — light HTTP check to api.groww.in.

        Always returns True (non-fatal) — main.py handles failures gracefully.
        Problems are logged so user sees them in Render logs.
        """
        import json as _json
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI

        _ist = _ZI("Asia/Kolkata")

        # ── 1. Token cache age ───────────────────────────────────────────────
        token_cache = BOT_DIR / "data" / ".token_cache.json"
        token_age_h = float("inf")
        if token_cache.exists():
            try:
                data = _json.loads(token_cache.read_text())
                ts_str = data.get("timestamp", "")
                if ts_str:
                    ts = _dt.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_ist)
                    token_age_h = (_dt.now(tz=_ist) - ts).total_seconds() / 3600
                    log.info(f"Pre-flight: token cache age {token_age_h:.1f}h")
            except Exception as e:
                log.warning(f"Pre-flight: token cache read error: {e}")
        else:
            log.warning("Pre-flight: no token cache found — main.py will use env token")

        if token_age_h > 20:
            log.warning(
                f"Pre-flight: token is {token_age_h:.1f}h old — "
                "running pre-emptive token refresh before starting main.py..."
            )
            try:
                result = subprocess.run(
                    [PYTHON, "-c",
                     "import logging; logging.basicConfig(level=logging.WARNING); "
                     "from auth_groww import initialize_auth; initialize_auth()"],
                    cwd=str(BOT_DIR),
                    timeout=90,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    log.info("Pre-flight: token refresh subprocess OK")
                else:
                    log.warning(
                        f"Pre-flight: token refresh subprocess returned {result.returncode}: "
                        f"{result.stderr[:300]}"
                    )
            except subprocess.TimeoutExpired:
                log.warning("Pre-flight: token refresh timed out (main.py will retry)")
            except Exception as e:
                log.warning(f"Pre-flight: token refresh error: {e}")

        # ── 2. Basic connectivity check ──────────────────────────────────────
        try:
            import urllib.request as _urlreq
            _urlreq.urlopen("https://api.groww.in", timeout=10)
            log.info("Pre-flight: Groww API reachable ✅")
        except Exception as e:
            # Non-fatal — Render occasionally has slow cold starts
            log.warning(f"Pre-flight: Groww API connectivity: {e} (continuing anyway)")

        return True

    def _run_main_once(self) -> int:
        """Launch main.py and wait for it to finish. Returns exit code."""
        cmd = [PYTHON, str(MAIN_PY)]
        start_time = ist_now().strftime("%Y-%m-%d %H:%M:%S IST")
        log.info(f"Starting main.py | Python: {PYTHON} | Time: {start_time}")
        save_status("RUNNING", last_start=start_time)

        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(BOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,          # Line-buffered
                encoding="utf-8",
                errors="replace",
            )
            save_status("RUNNING", pid=self.process.pid, crashes=self.crashes, last_start=start_time)

            # Stream output to watchdog log in real time
            log_file_path = LOGS_DIR / f"trading_{ist_now().strftime('%Y-%m-%d')}.log"
            with open(log_file_path, "a", encoding="utf-8") as log_file:
                for line in self.process.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    log_file.write(line)
                    log_file.flush()

            self.process.wait()
            return self.process.returncode

        except FileNotFoundError:
            log.critical(
                f"Cannot find Python at: {PYTHON}\n"
                "Run: python setup_autostart.py — to fix the path"
            )
            return -1
        except Exception as e:
            log.error(f"Failed to start main.py: {e}")
            return -1

    def run(self) -> None:
        """Main watchdog loop."""
        log.info("=" * 60)
        log.info("  KingTrades Watchdog starting")
        log.info(f"  Bot dir:  {BOT_DIR}")
        log.info(f"  Python:   {PYTHON}")
        log.info(f"  IST time: {ist_now().strftime('%Y-%m-%d %H:%M:%S')}")
        log.info("=" * 60)

        send_telegram(
            f"🤖 <b>KingTrades Watchdog Started</b>\n"
            f"Time: {ist_now().strftime('%d %b %Y %H:%M IST')}\n"
            f"Will trade on next market day automatically."
        )

        while self.running:
            # 1. Skip weekends and holidays
            self._wait_if_weekend_or_holiday()
            if not self.running:
                break

            # 2. Wait until 8:40 AM IST
            self._wait_for_trading_time()
            if not self.running:
                break

            # 3. Pre-flight: token freshness + connectivity
            self._preflight_check()

            # 4. Run main.py for the day
            log.info(f"Launching main.py for {ist_now().strftime('%A %d %b %Y')}")
            exit_code = self._run_main_once()

            if self.once:
                log.info("--once flag: exiting after single run")
                break

            # 4. Handle exit
            if exit_code == 0:
                # Clean shutdown (normal EOD)
                log.info("main.py exited cleanly (EOD shutdown)")
                self.crashes = 0
                self._wait_after_market_close()

            elif exit_code == 42:
                # Special code: bot sent kill-switch — do NOT restart today
                log.warning("Kill-switch exit (code 42) — not restarting today")
                save_status("KILLED — manual stop")
                send_telegram(
                    "🛑 <b>KingTrades KILL-SWITCH activated</b>\n"
                    "Bot will not restart until next trading day."
                )
                self._wait_after_market_close()

            else:
                # Crash — restart with backoff
                self.crashes += 1
                backoff = min(30 * (2 ** (self.crashes - 1)), self.MAX_CRASH_BACKOFF)
                now_ist = ist_now()
                log.error(
                    f"main.py crashed (exit code {exit_code}) | "
                    f"Crash #{self.crashes} | Restarting in {backoff}s"
                )
                save_status(f"CRASHED #{self.crashes} — restarting", crashes=self.crashes)

                if self.crashes >= self.CRASH_ALERT_THRESHOLD:
                    send_telegram(
                        f"⚠️ <b>KingTrades Crash Alert</b>\n"
                        f"Crash #{self.crashes} at {now_ist.strftime('%H:%M IST')}\n"
                        f"Exit code: {exit_code}\n"
                        f"Restarting in {backoff}s...\n"
                        f"Check logs/watchdog.log if this repeats."
                    )

                # Don't restart if market is already closed (after 3:35 PM IST)
                after_market = seconds_until(15, 35) < 0
                if after_market:
                    log.info("Market closed — not restarting today, waiting for tomorrow")
                    self.crashes = 0
                    self._wait_after_market_close()
                else:
                    time.sleep(backoff)

        save_status("STOPPED")
        log.info("Watchdog stopped.")


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def print_status() -> None:
    s = load_status()
    if not s:
        print("No status file found. Has the bot run yet?")
        return
    print("\nKingTrades Bot Status")
    print("─" * 40)
    for k, v in s.items():
        print(f"  {k:<12}: {v}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="KingTrades Watchdog")
    parser.add_argument("--once",   action="store_true", help="Run once, no auto-restart")
    parser.add_argument("--status", action="store_true", help="Show bot status and exit")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    wd = Watchdog(once=args.once)
    wd.run()


if __name__ == "__main__":
    main()
