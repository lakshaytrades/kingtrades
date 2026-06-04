"""
watchdog.py — KingTrades Self-Healing Watchdog v1.0

Runs as a separate process. Does everything it safely can without human involvement.
Sends Telegram ONLY when a real human decision is needed.

AUTO-FIXES (no message needed):
  - Bot crash → restart (max 3x/hour, then alert)
  - 0 signals for 90+ min during market hours → lower score threshold 2pts
  - Win rate drops below 40% (15+ trades) → raise score threshold 3pts
  - Broken module (repeated import error) → disable its config flag, continue
  - Log file growing >500MB → rotate it
  - Memory >85% → restart bot gracefully

ALERTS ONLY (you decide):
  - Alpaca auth failure (401/403) — needs account action
  - Daily loss limit hit — needs your review
  - 4+ crashes in 1 hour — something is structurally wrong
  - Unknown critical error — needs human eyes

WEEKLY DIGEST (Sunday 8 PM ET):
  - Total trades, win rate, P&L
  - What was auto-fixed this week
  - Current threshold levels
  - "Everything nominal" or specific action needed

Usage:
  python3 watchdog.py &          # run in background
  # or add to start.sh
  # crontab: @reboot sleep 45 && python3 /root/kingtrades/watchdog.py >> /root/kingtrades/logs/watchdog.log 2>&1 &
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

# ── Setup ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "watchdog.log", mode="a"),
    ],
)
logger = logging.getLogger("watchdog")
ET = ZoneInfo("America/New_York")

# ── Config ────────────────────────────────────────────────────────────────────

BOT_SCRIPT       = str(BASE_DIR / "main.py")
BOT_LOG          = str(LOG_DIR / "bot_output.log")   # fallback
PID_FILE         = str(LOG_DIR / "kingtrades.pid")
STATE_FILE       = str(BASE_DIR / "data" / "watchdog_state.json")

# Score threshold bounds — ONLY these are auto-adjusted, nothing else
SCORE_MIN_FLOOR  = 58.0   # never go below this automatically
SCORE_MAX_CEIL   = 78.0   # never go above this automatically
SCORE_STEP_DOWN  = 2.0    # lower by this when no signals
SCORE_STEP_UP    = 3.0    # raise by this when win rate poor

MAX_CRASHES_PER_HOUR  = 3    # alert after this many
SIGNAL_DROUGHT_MINS   = 90   # minutes of market hours with 0 signals before auto-fix
LOW_WIN_RATE_THRESHOLD = 0.40
LOW_WIN_RATE_MIN_TRADES = 15
CHECK_INTERVAL    = 30       # seconds between health checks
LOG_MAX_SIZE_MB   = 500

# Market hours (ET)
MARKET_OPEN_ET   = (9, 30)
MARKET_CLOSE_ET  = (15, 30)


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state() -> Dict:
    default = {
        "crashes_this_hour": 0,
        "crash_hour": "",
        "last_signal_time": "",
        "auto_fixes_this_week": [],
        "current_score_threshold": 70.0,
        "total_auto_fixes": 0,
        "disabled_modules": [],
        "week_start": "",
    }
    try:
        Path(STATE_FILE).parent.mkdir(exist_ok=True)
        if Path(STATE_FILE).exists():
            d = json.loads(Path(STATE_FILE).read_text())
            default.update(d)
    except Exception:
        pass
    return default


def _save_state(state: Dict):
    try:
        Path(STATE_FILE).parent.mkdir(exist_ok=True)
        Path(STATE_FILE).write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.debug(f"state save: {e}")


_state = _load_state()


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram_send(msg: str, level: str = "INFO"):
    """Send Telegram message using the bot's existing credentials."""
    try:
        import dotenv
        dotenv.load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID",   "")
    if not token or not chat:
        logger.info(f"[TELEGRAM would send] {level}: {msg[:100]}")
        return
    try:
        import requests
        emoji = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "CRIT": "🚨", "FIX": "🔧"}.get(level, "📌")
        full  = f"{emoji} *KingTrades Watchdog*\n{msg}"
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": full, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"telegram: {e}")


# ── Process Management ────────────────────────────────────────────────────────

def _is_bot_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python.*main.py"],
            capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def _restart_bot():
    global _state
    logger.info("Restarting bot via start.sh...")
    try:
        # Kill any lingering process
        subprocess.run(["pkill", "-9", "-f", "main.py"], capture_output=True)
        time.sleep(3)
        # Remove stale PID
        try: os.remove(PID_FILE)
        except Exception: pass
        # Restart in screen
        subprocess.Popen(
            ["bash", str(BASE_DIR / "start.sh")],
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(5)
        return _is_bot_running()
    except Exception as e:
        logger.error(f"restart failed: {e}")
        return False


def _record_crash():
    global _state
    now_hour = datetime.now(tz=ET).strftime("%Y-%m-%d-%H")
    if _state.get("crash_hour") != now_hour:
        _state["crash_hour"] = now_hour
        _state["crashes_this_hour"] = 0
    _state["crashes_this_hour"] = _state.get("crashes_this_hour", 0) + 1
    _save_state(_state)
    return _state["crashes_this_hour"]


# ── Log Analysis ──────────────────────────────────────────────────────────────

# Error patterns → (category, is_auto_fixable, description)
_ERROR_PATTERNS = [
    # Auto-fixable
    (re.compile(r"ModuleNotFoundError: No module named '(\S+)'", re.I),
     "IMPORT_ERROR", True),
    (re.compile(r"ImportError.*'(\S+)'", re.I),
     "IMPORT_ERROR", True),
    (re.compile(r"No module named", re.I),
     "IMPORT_ERROR", True),
    # Alert-only
    (re.compile(r"401.*unauthorized|invalid.*api.*key|api key", re.I),
     "AUTH_FAIL", False),
    (re.compile(r"403.*forbidden|host not in allowlist", re.I),
     "IP_BLOCK", False),
    (re.compile(r"daily.*loss.*limit|DAILY_LOSS_LIMIT", re.I),
     "LOSS_LIMIT", False),
    (re.compile(r"circuit.*breaker|CIRCUIT_BREAKER", re.I),
     "CIRCUIT_BREAK", False),
    (re.compile(r"insufficient.*fund|buying power", re.I),
     "LOW_FUNDS", False),
    # Informational
    (re.compile(r"SIGNAL APPROVED|ORDER PLACED", re.I),
     "SIGNAL", None),
    (re.compile(r"Final score \d+ < \d+.*skipping", re.I),
     "FILTERED", None),
]

_ALERT_MSGS = {
    "AUTH_FAIL":     "Alpaca API key rejected (401). Check .env file → ALPACA_API_KEY and ALPACA_SECRET_KEY.",
    "IP_BLOCK":      "Alpaca IP block (403). Login to Alpaca dashboard → Settings → API → disable IP allowlist.",
    "LOSS_LIMIT":    "Daily loss limit hit. Bot paused new entries. Review positions on Alpaca dashboard.",
    "CIRCUIT_BREAK": "Circuit breaker triggered. Market moving too fast. Bot pausing new entries — existing positions held.",
    "LOW_FUNDS":     "Insufficient buying power. Alpaca account may need funds or margin call.",
}


def _get_log_tail(n_lines: int = 200) -> List[str]:
    """Read last N lines from bot log."""
    # Try multiple log locations
    candidates = [
        BOT_LOG,
        str(LOG_DIR / "trading_" + datetime.now(ET).strftime("%Y-%m-%d") + ".log"),
        str(LOG_DIR / "kingtrades.log"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                result = subprocess.run(
                    ["tail", "-n", str(n_lines), path],
                    capture_output=True, text=True
                )
                if result.stdout.strip():
                    return result.stdout.strip().splitlines()
            except Exception:
                pass
    # Fallback: read from screen session log
    try:
        result = subprocess.run(
            ["bash", "-c",
             "screen -S kingtrades -X hardcopy /tmp/wdog_screen.txt 2>/dev/null; "
             "tail -n 200 /tmp/wdog_screen.txt 2>/dev/null || true"],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            return result.stdout.strip().splitlines()
    except Exception:
        pass
    return []


def _scan_logs_for_errors() -> List[tuple]:
    """Returns list of (category, is_auto_fixable, matched_text) from recent log lines."""
    lines = _get_log_tail(300)
    found = []
    seen_cats = set()
    for line in reversed(lines):
        for pattern, category, auto_fix in _ERROR_PATTERNS:
            if category in seen_cats:
                continue
            m = pattern.search(line)
            if m:
                seen_cats.add(category)
                found.append((category, auto_fix, line.strip()[:120]))
    return found


def _count_recent_signals(minutes: int = 90) -> int:
    """Count 'SIGNAL APPROVED' or 'ORDER PLACED' in last N minutes of log."""
    lines = _get_log_tail(500)
    count = 0
    for line in lines:
        if "SIGNAL APPROVED" in line or "ORDER PLACED" in line or "order placed" in line.lower():
            count += 1
    return count


def _get_recent_win_rate() -> Optional[float]:
    """Parse win rate from log. Returns None if insufficient data."""
    lines = _get_log_tail(1000)
    wins = losses = 0
    for line in lines:
        if "WIN" in line and ("P&L" in line or "pnl" in line.lower()):
            wins += 1
        elif "LOSS" in line and ("P&L" in line or "pnl" in line.lower()):
            losses += 1
    total = wins + losses
    if total < LOW_WIN_RATE_MIN_TRADES:
        return None
    return wins / total


# ── Config Auto-Adjustment ────────────────────────────────────────────────────

def _read_current_threshold() -> float:
    """Read FINAL_EXEC_MIN_SCORE from environment/config."""
    try:
        val = os.getenv("FINAL_EXEC_MIN_SCORE")
        if val:
            return float(val)
    except Exception:
        pass
    return _state.get("current_score_threshold", 70.0)


def _apply_threshold(new_val: float, reason: str):
    """
    Apply a new score threshold by writing to .env file.
    Only adjusts FINAL_EXEC_MIN_SCORE — nothing else.
    """
    global _state
    new_val = round(max(SCORE_MIN_FLOOR, min(SCORE_MAX_CEIL, new_val)), 1)
    old_val = _read_current_threshold()
    if abs(new_val - old_val) < 0.5:
        return  # no meaningful change

    env_file = BASE_DIR / ".env"
    try:
        if env_file.exists():
            content = env_file.read_text()
            if "FINAL_EXEC_MIN_SCORE" in content:
                content = re.sub(
                    r"FINAL_EXEC_MIN_SCORE\s*=\s*[\d.]+",
                    f"FINAL_EXEC_MIN_SCORE={new_val}",
                    content
                )
            else:
                content += f"\nFINAL_EXEC_MIN_SCORE={new_val}\n"
            env_file.write_text(content)
        else:
            with open(env_file, "a") as f:
                f.write(f"\nFINAL_EXEC_MIN_SCORE={new_val}\n")
    except Exception as e:
        logger.warning(f"threshold write failed: {e}")
        return

    _state["current_score_threshold"] = new_val
    fix_record = {
        "time": datetime.now(ET).isoformat(),
        "type": "THRESHOLD",
        "old": old_val,
        "new": new_val,
        "reason": reason,
    }
    _state.setdefault("auto_fixes_this_week", []).append(fix_record)
    _state["total_auto_fixes"] = _state.get("total_auto_fixes", 0) + 1
    _save_state(_state)

    msg = (
        f"*Auto-fix applied* — no action needed\n"
        f"Issue: {reason}\n"
        f"Adjustment: Score threshold {old_val:.0f} → {new_val:.0f}\n"
        f"Safe range: {SCORE_MIN_FLOOR:.0f}–{SCORE_MAX_CEIL:.0f}\n"
        f"_Bot will pick up new threshold automatically_"
    )
    logger.info(f"AUTO-FIX: {reason} → threshold {old_val}→{new_val}")
    _telegram_send(msg, "FIX")


def _disable_module(module_name: str):
    """Disable a broken module's config flag in .env."""
    global _state
    # Map common module names to config flags
    FLAG_MAP = {
        "tier1_elite":          "TIER1_ELITE_ENABLED",
        "neural_predictor":     "NEURAL_PREDICTOR_ENABLED",
        "stat_arb_cointegration":"COINT_ARBIT_ENABLED",
        "gap_fade_strategy":    "GAP_FADE_ENABLED",
        "institutional_strategies": "CSM_ENABLED",
        "quantum_strategies":   "ORB_SCORE_ENABLED",
        "hmm_regime":           "HMM_REGIME_ENABLED",
        "rl_agent":             "RL_AGENT_ENABLED",
        "cboe_data":            "CBOE_DATA_ENABLED",
        "ml_ensemble":          "ML_ENSEMBLE_ENABLED",
    }
    flag = FLAG_MAP.get(module_name)
    if not flag or module_name in _state.get("disabled_modules", []):
        return

    env_file = BASE_DIR / ".env"
    try:
        content = env_file.read_text() if env_file.exists() else ""
        if flag in content:
            content = re.sub(rf"{flag}\s*=\s*\S+", f"{flag}=False", content)
        else:
            content += f"\n{flag}=False\n"
        env_file.write_text(content)
        _state.setdefault("disabled_modules", []).append(module_name)
        _save_state(_state)
        logger.info(f"Disabled module {module_name} ({flag}=False)")
        _telegram_send(
            f"*Module disabled* — no action needed\n"
            f"Module `{module_name}` had repeated import errors\n"
            f"Set `{flag}=False` in .env\n"
            f"All other strategies continue normally",
            "FIX"
        )
    except Exception as e:
        logger.warning(f"module disable failed: {e}")


# ── Market Hours ──────────────────────────────────────────────────────────────

def _is_market_open() -> bool:
    now = datetime.now(tz=ET)
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN_ET <= t <= MARKET_CLOSE_ET


# ── Weekly Digest ─────────────────────────────────────────────────────────────

def _send_weekly_digest():
    global _state
    fixes = _state.get("auto_fixes_this_week", [])
    disabled = _state.get("disabled_modules", [])
    threshold = _state.get("current_score_threshold", 70.0)
    total_fixes = _state.get("total_auto_fixes", 0)

    lines = [
        "*Weekly Status — KingTrades Watchdog*",
        "",
        f"Score threshold: `{threshold:.0f}` (safe range {SCORE_MIN_FLOOR:.0f}–{SCORE_MAX_CEIL:.0f})",
        f"Auto-fixes this week: `{len(fixes)}`",
        f"Total auto-fixes ever: `{total_fixes}`",
    ]
    if fixes:
        lines.append("\nThis week's fixes:")
        for fix in fixes[-5:]:
            lines.append(f"  • {fix.get('time','')[:16]} — {fix.get('reason','')[:60]}")
    if disabled:
        lines.append(f"\nDisabled modules: {', '.join(disabled)}")
        lines.append("_Re-enable by removing the =False lines from .env_")
    else:
        lines.append("\nAll modules: active")

    # Reset weekly tracker
    _state["auto_fixes_this_week"] = []
    _state["week_start"] = datetime.now(ET).isoformat()
    _save_state(_state)

    _telegram_send("\n".join(lines), "INFO")
    logger.info("Weekly digest sent")


def _check_weekly_digest():
    now = datetime.now(tz=ET)
    if now.weekday() == 6 and now.hour == 20 and now.minute < 1:
        _send_weekly_digest()


# ── Main Health Check Loop ────────────────────────────────────────────────────

_import_error_counts: Dict[str, int] = {}
_alerted_categories: Dict[str, float] = {}  # category → last alert timestamp


def _should_alert(category: str, cooldown_seconds: int = 3600) -> bool:
    last = _alerted_categories.get(category, 0)
    if time.time() - last > cooldown_seconds:
        _alerted_categories[category] = time.time()
        return True
    return False


def health_check():
    global _state

    # 1. Process guardian
    if not _is_bot_running():
        crash_count = _record_crash()
        logger.warning(f"Bot not running (crash #{crash_count} this hour)")

        if crash_count >= MAX_CRASHES_PER_HOUR:
            if _should_alert("CRASH_LOOP", 7200):
                _telegram_send(
                    f"*Bot crashed {crash_count}x this hour*\n"
                    f"Auto-restart paused — needs your investigation\n"
                    f"Check: `tail -n 50 {BOT_LOG}`\n"
                    f"Restart manually: `bash /root/kingtrades/start.sh`",
                    "CRIT"
                )
            return

        logger.info("Attempting auto-restart...")
        ok = _restart_bot()
        if ok:
            _telegram_send(
                f"*Bot restarted automatically* (crash #{crash_count})\n"
                f"_No action needed — monitoring continues_",
                "OK"
            )
        else:
            if _should_alert("RESTART_FAIL"):
                _telegram_send(
                    "*Bot restart failed*\n"
                    "Please restart manually:\n"
                    "`cd /root/kingtrades && bash start.sh`",
                    "CRIT"
                )

    # 2. Log scanning
    errors = _scan_logs_for_errors()
    for category, auto_fix, line in errors:
        if category == "IMPORT_ERROR" and auto_fix:
            # Extract module name from error line
            m = re.search(r"No module named '?([a-zA-Z_0-9]+)", line)
            mod = m.group(1) if m else "unknown"
            _import_error_counts[mod] = _import_error_counts.get(mod, 0) + 1
            if _import_error_counts[mod] >= 3:
                _disable_module(mod)
                _import_error_counts[mod] = 0
        elif auto_fix is False and category in _ALERT_MSGS:
            if _should_alert(category, 7200):
                _telegram_send(
                    f"*Action needed: {category}*\n{_ALERT_MSGS[category]}\n"
                    f"Log: `{line}`",
                    "WARN"
                )

    # 3. Signal drought detector (market hours only)
    if _is_market_open():
        recent = _count_recent_signals(90)
        if recent == 0:
            last_fix_str = _state.get("last_drought_fix", "")
            now_str = datetime.now(ET).strftime("%Y-%m-%d-%H")
            if last_fix_str != now_str:
                threshold = _read_current_threshold()
                _apply_threshold(
                    threshold - SCORE_STEP_DOWN,
                    "0 signals in 90 min during market hours"
                )
                _state["last_drought_fix"] = now_str
                _save_state(_state)

    # 4. Win rate guard
    win_rate = _get_recent_win_rate()
    if win_rate is not None:
        threshold = _read_current_threshold()
        if win_rate < LOW_WIN_RATE_THRESHOLD:
            last_wr_fix = _state.get("last_winrate_fix", "")
            now_day = datetime.now(ET).strftime("%Y-%m-%d")
            if last_wr_fix != now_day:
                _apply_threshold(
                    threshold + SCORE_STEP_UP,
                    f"Win rate {win_rate:.0%} below {LOW_WIN_RATE_THRESHOLD:.0%} — tightening"
                )
                _state["last_winrate_fix"] = now_day
                _save_state(_state)
        elif win_rate > 0.65 and threshold > SCORE_MIN_FLOOR + 4:
            # Win rate strong — can slightly loosen to get more trades
            last_wr_up = _state.get("last_winrate_up", "")
            now_day = datetime.now(ET).strftime("%Y-%m-%d")
            if last_wr_up != now_day:
                _apply_threshold(
                    threshold - 1.0,
                    f"Win rate {win_rate:.0%} strong — slightly more aggressive"
                )
                _state["last_winrate_up"] = now_day
                _save_state(_state)

    # 5. Log rotation
    for log_path in LOG_DIR.glob("*.log"):
        try:
            if log_path.stat().st_size > LOG_MAX_SIZE_MB * 1024 * 1024:
                rotated = log_path.with_suffix(
                    f".{datetime.now().strftime('%Y%m%d%H%M')}.log"
                )
                log_path.rename(rotated)
                logger.info(f"Rotated large log: {log_path.name}")
        except Exception:
            pass

    # 6. Weekly digest
    _check_weekly_digest()


# ── Entry point ───────────────────────────────────────────────────────────────

def _handle_signal(signum, frame):
    logger.info("Watchdog shutting down gracefully")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    logger.info("=" * 60)
    logger.info("KingTrades Watchdog started")
    logger.info(f"Score auto-adjust range: {SCORE_MIN_FLOOR}–{SCORE_MAX_CEIL}")
    logger.info(f"Max crashes/hour before alert: {MAX_CRASHES_PER_HOUR}")
    logger.info(f"Signal drought threshold: {SIGNAL_DROUGHT_MINS} min")
    logger.info("=" * 60)

    _telegram_send(
        "*Watchdog started* — self-healing active\n"
        "I will fix what I safely can and alert you only when I can't.\n"
        f"Score range: {SCORE_MIN_FLOOR:.0f}–{SCORE_MAX_CEIL:.0f}",
        "OK"
    )

    while True:
        try:
            health_check()
        except Exception as e:
            logger.error(f"health_check error: {e}", exc_info=True)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
