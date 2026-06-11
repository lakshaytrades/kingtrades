"""
watchdog_india.py — Self-healing monitor for KingTrades India Bot
India specialist: watches main_india.py, IST market hours, INR thresholds.

Auto-fixes (no human needed):
  - Bot crash → restart up to 3x/hour
  - No signals in 90 min → loosen INDIA_FINAL_EXEC_MIN_SCORE by 2 pts
  - Win rate < 40% → tighten score by 3 pts
  - Module error → disable that module flag in .env

Alerts only (requires human):
  - Dhan auth failure (DHAN_ACCESS_TOKEN expired)
  - 4+ crashes in an hour
  - Daily loss limit hit

Runs independently: nohup python3 watchdog_india.py >> logs/india_watchdog.log &
"""
import json
import logging
import os
import signal
import subprocess
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

BASE_DIR   = Path(__file__).parent.parent
LOG_DIR    = BASE_DIR / "logs"
STATE_FILE = BASE_DIR / "data" / "india_watchdog_state.json"
IST        = ZoneInfo("Asia/Kolkata")

# Server runs in UTC — emit IST timestamps in logs (see main_india for rationale).
def _ist_log_converter(*args):
    return datetime.fromtimestamp(args[-1], IST).timetuple()
logging.Formatter.converter = staticmethod(_ist_log_converter)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [IST] [INDIA-WATCHDOG] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "india_watchdog.log"),
    ],
)
logger = logging.getLogger("india_watchdog")

SCORE_MIN_FLOOR = 58.0
SCORE_MAX_CEIL  = 78.0
SCORE_STEP_DOWN = 2.0
SCORE_STEP_UP   = 3.0

MAX_CRASHES_PER_HOUR   = 3
SIGNAL_DROUGHT_MINS    = 90
LOW_WIN_RATE_THRESHOLD = 0.40
CHECK_INTERVAL         = 30   # seconds

MARKET_OPEN_IST  = (9, 15)
MARKET_CLOSE_IST = (15, 30)
WEEKLY_DIGEST_TIME_IST = (20, 0)   # 8 PM IST Sunday


# ── State ─────────────────────────────────────────────────────────────────────

def _load_state() -> Dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {
        "crashes_this_hour": [],
        "last_signal_time": None,
        "last_alert": {},
        "disabled_modules": [],
        "auto_fixes_this_week": [],
    }


def _save_state(state: Dict):
    try:
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str))
    except Exception as e:
        logger.debug(f"state save: {e}")


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram_send(msg: str, level: str = "INFO"):
    try:
        import config_india as cfg
        import os as _os
        import requests
        token = cfg.TELEGRAM_BOT_TOKEN
        chat  = cfg.TELEGRAM_CHAT_ID
        if not token or not chat:
            logger.info(f"[TG-{level}] {msg[:120]}")
            return
        # Quiet by default: only CRIT escapes unless verbose. Filter benign
        # manual/paper-mode notices that are normal, not errors.
        verbose = getattr(cfg, "TELEGRAM_VERBOSE", False) or \
                  _os.getenv("TELEGRAM_VERBOSE", "False") == "True"
        low = msg.lower()
        _benign = ("dhan not connected", "paper mode", "no api key",
                   "not set in .env", "manual review", "novel error")
        if not verbose and (level not in ("CRIT",) or any(b in low for b in _benign)):
            logger.info(f"[india-watchdog quiet | {level}] {msg[:100]}")
            return
        prefix = {"INFO": "🔵", "WARN": "⚠️", "CRIT": "🚨"}.get(level, "🔵")
        chat_ids = [c.strip() for c in str(chat).replace(" ", ",").split(",") if c.strip()]
        for cid in chat_ids:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": cid,
                          "text": f"{prefix} *India Watchdog*\n{msg}",
                          "parse_mode": "Markdown"},
                    timeout=8,
                )
            except Exception as _ce:
                logger.debug(f"telegram chat {cid}: {_ce}")
    except Exception as e:
        logger.debug(f"telegram: {e}")


def _should_alert(state: Dict, category: str, cooldown: int = 3600) -> bool:
    last = state.get("last_alert", {}).get(category, 0)
    return (_time.time() - last) > cooldown


def _mark_alerted(state: Dict, category: str):
    state.setdefault("last_alert", {})[category] = _time.time()


# ── Bot process ───────────────────────────────────────────────────────────────

def _is_bot_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "main_india.py"],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _restart_bot():
    try:
        script = Path(__file__).parent / "start_india.sh"
        subprocess.Popen(["bash", str(script)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("India bot restart triggered")
        _telegram_send("India bot restarted automatically", "INFO")
    except Exception as e:
        logger.error(f"Restart failed: {e}")


def _record_crash(state: Dict):
    now = _time.time()
    # Keep only last hour
    state["crashes_this_hour"] = [t for t in state["crashes_this_hour"]
                                  if now - t < 3600]
    state["crashes_this_hour"].append(now)


# ── Threshold management ──────────────────────────────────────────────────────

def _read_current_threshold() -> float:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("INDIA_FINAL_EXEC_MIN_SCORE="):
                try:
                    return float(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
    return 70.0


def _write_env_key(key: str, value: str):
    """
    Atomically update or append a key in .env.
    Write to a temp file then rename to avoid partial-write corruption.
    """
    import re
    env_path = BASE_DIR / ".env"
    tmp_path  = env_path.with_suffix(".env.tmp")
    content = env_path.read_text() if env_path.exists() else ""
    pattern = rf"^{re.escape(key)}\s*=.*$"
    if re.search(pattern, content, flags=re.MULTILINE):
        content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{key}={value}\n"
    tmp_path.write_text(content)
    tmp_path.replace(env_path)   # atomic on POSIX


def _apply_threshold(new_val: float, reason: str, state: Dict):
    new_val = round(max(SCORE_MIN_FLOOR, min(SCORE_MAX_CEIL, new_val)), 1)
    try:
        _write_env_key("INDIA_FINAL_EXEC_MIN_SCORE", str(new_val))
        logger.info(f"Threshold → {new_val}: {reason}")
        state.setdefault("auto_fixes_this_week", []).append(
            {"fix": f"INDIA_FINAL_EXEC_MIN_SCORE={new_val}", "reason": reason,
             "ts": datetime.now(IST).isoformat()}
        )
        _telegram_send(f"Auto-adjusted `INDIA_FINAL_EXEC_MIN_SCORE` → `{new_val}`\n_{reason}_", "INFO")
    except Exception as e:
        logger.error(f"Threshold write failed: {e}")


def _disable_module(module_name: str, state: Dict):
    key = f"INDIA_{module_name.upper()}_ENABLED"
    try:
        _write_env_key(key, "False")
        state.setdefault("disabled_modules", []).append(module_name)
        logger.warning(f"Disabled module: {key}")
        _telegram_send(f"Disabled `{key}` due to repeated errors\nRe-enable: remove the line from .env", "WARN")
    except Exception as e:
        logger.error(f"Disable module failed: {e}")


# ── Log scanning ─────────────────────────────────────────────────────────────

def _get_log_tail(n: int = 300) -> List[str]:
    log_file = LOG_DIR / f"india_{datetime.now(IST).strftime('%Y-%m-%d')}.log"
    if not log_file.exists():
        log_file = LOG_DIR / "india_output.log"
    if not log_file.exists():
        return []
    try:
        result = subprocess.run(["tail", "-n", str(n), str(log_file)],
                                capture_output=True, text=True)
        return result.stdout.splitlines()
    except Exception:
        return []


def _count_recent_signals(minutes: int = 90) -> int:
    lines = _get_log_tail(500)
    cutoff = datetime.now(IST) - timedelta(minutes=minutes)
    count = 0
    for line in lines:
        if "SIGNAL:" in line or "Trade Entered" in line:
            count += 1
    return count


def _get_recent_win_rate() -> Optional[float]:
    lines = _get_log_tail(500)
    wins = sum(1 for l in lines if "CLOSED" in l and "P&L ₹+" in l)
    losses = sum(1 for l in lines if "CLOSED" in l and "P&L ₹-" in l)
    total = wins + losses
    return wins / total if total >= 5 else None


def _is_market_open_ist() -> bool:
    now = datetime.now(IST).time()
    from datetime import time
    return time(*MARKET_OPEN_IST) <= now < time(*MARKET_CLOSE_IST)


# ── Weekly digest ─────────────────────────────────────────────────────────────

def _send_weekly_digest(state: Dict):
    fixes = state.get("auto_fixes_this_week", [])
    disabled = state.get("disabled_modules", [])
    threshold = _read_current_threshold()

    msg_lines = [
        "*India Watchdog — Weekly Digest*",
        f"Week ending: {datetime.now(IST).strftime('%d %b %Y')}",
        f"Current threshold: `INDIA_FINAL_EXEC_MIN_SCORE={threshold}`",
        "",
        f"*Auto-fixes this week:* {len(fixes)}",
    ]
    for fix in fixes[-5:]:
        msg_lines.append(f"  • {fix.get('fix')} — {fix.get('reason', '')[:50]}")

    if disabled:
        msg_lines += ["", f"*Disabled modules:* {', '.join(disabled)}",
                      "Re-enable: remove INDIA_*=False from .env"]

    msg_lines += ["", "_Auto-improver runs Sunday 9 PM IST for deeper AI analysis_"]
    _telegram_send("\n".join(msg_lines), "INFO")

    # Reset weekly counters
    state["auto_fixes_this_week"] = []
    state["disabled_modules"] = []


# ── Main health check ─────────────────────────────────────────────────────────

_last_digest_day: Optional[str] = None

def health_check():
    global _last_digest_day
    state = _load_state()

    # ── Weekly digest (Sunday 8 PM IST) ──────────────────────────────────────
    now = datetime.now(IST)
    if (now.weekday() == 6 and
            now.hour == WEEKLY_DIGEST_TIME_IST[0] and
            now.minute < 5):
        today_str = now.strftime("%Y-%m-%d")
        if _last_digest_day != today_str:
            _send_weekly_digest(state)
            _last_digest_day = today_str

    # ── Only monitor during market hours ─────────────────────────────────────
    if not _is_market_open_ist():
        _save_state(state)
        return

    # ── Bot process check ─────────────────────────────────────────────────────
    if not _is_bot_running():
        _record_crash(state)
        crashes = len(state["crashes_this_hour"])
        logger.warning(f"India bot not running! (crash #{crashes} this hour)")

        if crashes >= MAX_CRASHES_PER_HOUR:
            if _should_alert(state, "too_many_crashes"):
                _telegram_send(
                    f"🚨 India bot crashed {crashes}x this hour — NOT auto-restarting.\n"
                    f"Check logs: `tail -f /root/kingtrades/logs/india_output.log`",
                    "CRIT"
                )
                _mark_alerted(state, "too_many_crashes")
        else:
            _restart_bot()

    # ── Signal drought ────────────────────────────────────────────────────────
    # DISABLED by owner: auto-loosening the quality gate to force trades is the
    # exact behavior that causes low-quality losing trades. No signals usually
    # means no good setups (or data issue) — the fix is NOT to lower standards.
    # Re-enable only with INDIA_WATCHDOG_AUTOLOOSEN=True (not recommended).
    if os.getenv("INDIA_WATCHDOG_AUTOLOOSEN", "False") == "True":
        if _count_recent_signals(SIGNAL_DROUGHT_MINS) == 0:
            threshold = _read_current_threshold()
            if threshold > SCORE_MIN_FLOOR + SCORE_STEP_DOWN:
                new_thresh = threshold - SCORE_STEP_DOWN
                _apply_threshold(new_thresh,
                                 f"no signals in {SIGNAL_DROUGHT_MINS}min — loosening gate",
                                 state)

    # ── Low win rate ──────────────────────────────────────────────────────────
    win_rate = _get_recent_win_rate()
    if win_rate is not None and win_rate < LOW_WIN_RATE_THRESHOLD:
        threshold = _read_current_threshold()
        if threshold < SCORE_MAX_CEIL - SCORE_STEP_UP:
            new_thresh = threshold + SCORE_STEP_UP
            _apply_threshold(new_thresh,
                             f"win rate {win_rate:.0%} below threshold — tightening",
                             state)

    # ── Dhan auth failure alert ───────────────────────────────────────────────
    lines = _get_log_tail(100)
    if any("auth" in l.lower() and "failed" in l.lower() for l in lines):
        if _should_alert(state, "auth_fail"):
            _telegram_send(
                "🇮🇳 Dhan auth failure detected.\n"
                "Send `/newtoken YOUR_TOKEN` here to fix without VPS.\n"
                "Get token: https://dhanhq.co → API → Generate Token",
                "CRIT"
            )
            _mark_alerted(state, "auth_fail")

    _save_state(state)


# ── Signal handler + main ─────────────────────────────────────────────────────

_running = True
_last_expiry_check_day: Optional[str] = None


def _handle_signal(signum, frame):
    global _running
    logger.info("India watchdog stopping")
    _running = False


def _restart_from_token_update():
    """Restart the India bot after a successful token update via Telegram."""
    logger.info("Restarting India bot after token update")
    try:
        subprocess.run(["pkill", "-f", "main_india.py"], capture_output=True)
        _time.sleep(3)
        _restart_bot()
    except Exception as e:
        logger.error(f"Restart after token update failed: {e}")


def main():
    global _last_expiry_check_day

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    # PID file
    pid_file = LOG_DIR / "india_watchdog.pid"
    pid_file.write_text(str(os.getpid()))

    try:
        from auth_dhan import get_days_until_expiry
        days_left = get_days_until_expiry()
        expiry_info = f" | Token expires in ~{days_left:.0f}d" if days_left >= 0 else ""
    except Exception:
        expiry_info = ""

    _telegram_send(
        f"🇮🇳 India watchdog started\n"
        f"Monitoring: `main_india.py`\n"
        f"Score range: {SCORE_MIN_FLOOR:.0f}–{SCORE_MAX_CEIL:.0f}\n"
        f"Telegram `/newtoken TOKEN` supported{expiry_info}",
        "INFO"
    )
    logger.info(f"India watchdog running (PID {os.getpid()})")

    loop_count = 0
    while _running:
        try:
            # ── Poll Telegram for /newtoken command (every loop = 30s) ─────────
            try:
                from auth_dhan import poll_telegram_commands
                poll_telegram_commands(restart_callback=_restart_from_token_update)
            except Exception as e:
                logger.debug(f"telegram poll: {e}")

            # ── Daily token expiry check (once per day at 8 AM IST) ───────────
            now = datetime.now(IST)
            today_str = now.strftime("%Y-%m-%d")
            if now.hour == 8 and now.minute < 1 and _last_expiry_check_day != today_str:
                try:
                    from auth_dhan import check_token_expiry_and_warn
                    check_token_expiry_and_warn()
                    _last_expiry_check_day = today_str
                except Exception as e:
                    logger.debug(f"expiry check: {e}")

            health_check()
        except Exception as e:
            logger.error(f"health_check error: {e}")
        _time.sleep(CHECK_INTERVAL)

    pid_file.unlink(missing_ok=True)
    logger.info("India watchdog stopped")


if __name__ == "__main__":
    main()
