"""
ai_supervisor.py — Autonomous Claude-powered supervisor for SataVector bots.

Runs alongside watchdog.py. Every 5 minutes (market hours) it reads recent
bot logs, detects novel errors, calls Claude to diagnose and fix them, then
applies safe changes and signals the watchdog to restart if needed.

Every 30 minutes it checks win rate and calls Claude Sonnet to propose
threshold/config adjustments when performance degrades.

Safety guarantees:
  - Never touches execution_alpaca.py, execution_groww.py, risk_manager.py
  - Max 8 code/config fixes per day
  - 10-minute cooldown between any two fixes
  - Always backs up before editing
  - Syntax-checks every code change; reverts on failure
  - Reverts if bot crashes within 5 minutes of a fix
  - Only active during extended market hours (9:00 AM–4:30 PM ET or IST)
"""

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# ── Bootstrap env vars ────────────────────────────────────────────────────────
try:
    import dotenv
    dotenv.load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

# ── Logging ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# File-only logging — nohup already redirects stdout to the same file,
# so a StreamHandler would double every line.
_log_handler = logging.FileHandler(LOG_DIR / "ai_supervisor.log", mode="a")
_log_handler.setFormatter(logging.Formatter("%(asctime)s [AI-SUPERVISOR] %(levelname)s %(message)s"))
logging.root.setLevel(logging.INFO)
logging.root.handlers = [_log_handler]
logger = logging.getLogger("ai_supervisor")

# ── Timezone constants ────────────────────────────────────────────────────────
ET  = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")

# ── Configuration ─────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECS     = 300    # 5 minutes between scans
WIN_RATE_INTERVAL_SECS = 1800   # 30 minutes between win-rate checks
MAX_FIXES_PER_DAY      = 8
FIX_COOLDOWN_SECS      = 600    # 10 minutes between any two fixes
CRASH_WATCH_SECS       = 300    # 5 minutes post-fix crash watch window
LOG_TAIL_LINES         = 200    # lines to read from each bot log
WIN_RATE_TRADE_WINDOW  = 10     # last N trades for WR calculation
WIN_RATE_THRESHOLD     = 0.45   # below this → trigger Sonnet analysis
SCORE_STEP             = 2      # ±2 per threshold adjustment

# ── File paths ────────────────────────────────────────────────────────────────
US_BOT_LOG    = LOG_DIR / "bot_output.log"
INDIA_BOT_LOG = LOG_DIR / "india_output.log"
STATE_FILE    = BASE_DIR / "data" / "supervisor_state.json"
BACKUP_DIR    = LOG_DIR / "supervisor_backups"

# ── Safe-to-modify files (everything else is forbidden) ───────────────────────
SAFE_TO_MODIFY = {
    "config.py",
    "signal_generator.py",
    "india/signal_generator_india.py",
    "india/watchlist_india.py",
    "india/orb_strategy_india.py",
    "scalping_engine.py",
    "momentum_burst.py",
}

FORBIDDEN_FILES = {
    "execution_alpaca.py",
    "execution_groww.py",
    "execution_dhan.py",
    "risk_manager.py",
}

# ── Known-safe error patterns (no API call needed) ───────────────────────────
# Each entry: (compiled_regex, description, fix_type)
#   fix_type: "restart" | "ignore"
KNOWN_SAFE_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"ConnectionRefusedError|ConnectionResetError|RemoteDisconnected", re.I),
     "Transient network disconnect", "restart"),
    (re.compile(r"ReadTimeout|ConnectTimeout|requests\.exceptions\.Timeout", re.I),
     "HTTP request timeout", "restart"),
    (re.compile(r"JSONDecodeError|json\.decoder", re.I),
     "Malformed JSON from API (transient)", "restart"),
    (re.compile(r"OSError: \[Errno 28\]|No space left on device", re.I),
     "Disk full — cannot auto-fix", "ignore"),
    (re.compile(r"BrokenPipeError", re.I),
     "Broken pipe (transient)", "restart"),
    (re.compile(r"ssl\.SSLError|ssl\.CertificateError", re.I),
     "SSL error (transient)", "restart"),
]

# ── Anthropic client ──────────────────────────────────────────────────────────
_anthropic_client = None
_api_available    = False


def _init_anthropic() -> bool:
    """Initialise Anthropic client. Returns True if available."""
    global _anthropic_client, _api_available
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning(
            "ANTHROPIC_API_KEY not set — running in Telegram-alerts-only mode"
        )
        _api_available = False
        return False
    try:
        from anthropic import Anthropic
        _anthropic_client = Anthropic(api_key=api_key)
        _api_available = True
        logger.info("Anthropic client initialised (Haiku + Sonnet available)")
        return True
    except ImportError:
        logger.warning("anthropic package not installed — alerts-only mode")
        _api_available = False
        return False
    except Exception as exc:
        logger.warning(f"Anthropic init failed: {exc} — alerts-only mode")
        _api_available = False
        return False


# ── State management ─────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
    default: Dict[str, Any] = {
        "fixes_today": 0,
        "fix_date": "",
        "last_fix_time": 0,
        "seen_errors": [],
        "applied_fixes": [],
    }
    try:
        STATE_FILE.parent.mkdir(exist_ok=True)
        if STATE_FILE.exists():
            loaded = json.loads(STATE_FILE.read_text())
            default.update(loaded)
    except Exception as exc:
        logger.debug(f"State load error: {exc}")
    return default


def _save_state(state: Dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        logger.debug(f"State save error: {exc}")


def _today_str() -> str:
    return datetime.now(tz=ET).strftime("%Y-%m-%d")


def _reset_daily_counter_if_needed(state: Dict[str, Any]) -> Dict[str, Any]:
    today = _today_str()
    if state.get("fix_date") != today:
        state["fixes_today"] = 0
        state["fix_date"]    = today
    return state


# ── Telegram ──────────────────────────────────────────────────────────────────

def _telegram_send(msg: str, level: str = "INFO") -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID",   "")
    if not token or not chat:
        logger.info(f"[TELEGRAM would send | {level}]: {msg[:120]}")
        return
    try:
        import requests
        emoji_map = {
            "INFO":   "ℹ️",
            "OK":     "✅",
            "WARN":   "⚠️",
            "CRIT":   "🚨",
            "FIX":    "🔧",
            "REVERT": "↩️",
            "WR":     "📊",
        }
        emoji = emoji_map.get(level, "📌")
        full  = f"{emoji} *AI Supervisor*\n{msg}"
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": full, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        logger.debug(f"Telegram send error: {exc}")


# ── Market hours ──────────────────────────────────────────────────────────────

def _is_us_extended_hours() -> bool:
    """9:00 AM–4:30 PM ET on weekdays."""
    now = datetime.now(tz=ET)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return (h, m) >= (9, 0) and (h, m) <= (16, 30)


def _is_india_extended_hours() -> bool:
    """9:00 AM–4:00 PM IST on weekdays."""
    now = datetime.now(tz=IST)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return (h, m) >= (9, 0) and (h, m) <= (16, 0)


def _is_active_hours() -> bool:
    return _is_us_extended_hours() or _is_india_extended_hours()


# ── Log reading ───────────────────────────────────────────────────────────────

def _read_log_tail(log_path: Path, n_lines: int = LOG_TAIL_LINES) -> List[str]:
    if not log_path.exists():
        return []
    try:
        result = subprocess.run(
            ["tail", "-n", str(n_lines), str(log_path)],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip().splitlines() if result.stdout.strip() else []
    except Exception as exc:
        logger.debug(f"Log read error ({log_path}): {exc}")
        return []


def _extract_error_lines(lines: List[str]) -> List[str]:
    """Return lines that contain real ERROR/WARNING/TRACEBACK keywords.
    Use bracketed or space-bounded matches to avoid false positives like
    'warnings=0', 'SCORE_GATE', or 'HEALTH OK — checks=[...]'.
    """
    out = []
    for line in lines:
        u = line.upper()
        if ("[ERROR]" in u or " ERROR " in u or
                "[WARNING]" in u or " WARNING " in u or
                "TRACEBACK" in u or "EXCEPTION" in u or
                "CRITICAL" in u):
            out.append(line.strip())
    return out


def _hash_line(line: str) -> str:
    return hashlib.md5(line.encode()).hexdigest()[:16]


def _get_new_errors(
    error_lines: List[str],
    seen_hashes: List[str],
) -> Tuple[List[str], List[str]]:
    """Return (new_error_lines, updated_seen_hashes)."""
    new_errors: List[str] = []
    updated = list(seen_hashes)
    for line in error_lines:
        h = _hash_line(line)
        if h not in updated:
            new_errors.append(line)
            updated.append(h)
    # Keep bounded to last 2000 entries
    if len(updated) > 2000:
        updated = updated[-2000:]
    return new_errors, updated


# ── Known-safe pattern handler ────────────────────────────────────────────────

def _check_known_safe(error_line: str) -> Optional[Tuple[str, str]]:
    """Returns (description, fix_type) if error matches a known-safe pattern."""
    for pattern, description, fix_type in KNOWN_SAFE_PATTERNS:
        if pattern.search(error_line):
            return description, fix_type
    return None


# ── Claude Haiku — error diagnosis ───────────────────────────────────────────

def _diagnose_with_haiku(error_line: str, context: str) -> Optional[Dict[str, Any]]:
    """
    Call Claude Haiku to diagnose an error.
    Returns parsed JSON dict or None on failure.
    """
    if not _api_available or _anthropic_client is None:
        return None
    prompt = (
        f"Error in trading bot:\n{error_line}\n\n"
        f"Recent context:\n{context}\n\n"
        'Return JSON: {"severity": "LOW|MEDIUM|HIGH", "cause": "...", '
        '"fix_type": "config|code|restart|none", "fix_detail": "...", '
        '"target_file": "filename or empty string"}'
    )
    try:
        response = _anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=(
                "You are a Python trading bot debugger. "
                "Diagnose errors concisely. Always respond with valid JSON only."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as exc:
        logger.warning(f"Haiku diagnosis failed: {exc}")
    return None


# ── Claude Sonnet — code fix generation ──────────────────────────────────────

def _generate_code_fix(
    filename: str,
    error: str,
    code_context: str,
) -> Optional[Tuple[str, str]]:
    """
    Call Claude Sonnet to generate a code fix.
    Returns (old_string, new_string) or None.
    """
    if not _api_available or _anthropic_client is None:
        return None
    prompt = (
        f"File: {filename}\n"
        f"Error: {error}\n"
        f"Relevant code:\n{code_context}\n\n"
        "Provide the exact old_string and new_string for a string replacement fix.\n"
        "Format:\nOLD:\n```\n...\n```\nNEW:\n```\n...\n```"
    )
    try:
        response = _anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=(
                "You are fixing a bug in a Python trading bot. "
                "Output ONLY the fixed code block with OLD/NEW format, no explanation. "
                "The replacement must be a minimal, targeted change."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        old_m = re.search(r"OLD:\s*```(?:python)?\s*(.*?)```", text, re.DOTALL)
        new_m = re.search(r"NEW:\s*```(?:python)?\s*(.*?)```", text, re.DOTALL)
        if old_m and new_m:
            return old_m.group(1).strip(), new_m.group(1).strip()
        logger.warning(f"Could not parse OLD/NEW from Sonnet response: {text[:200]}")
    except Exception as exc:
        logger.warning(f"Sonnet code fix failed: {exc}")
    return None


# ── Claude Sonnet — win rate analysis ────────────────────────────────────────

def _analyze_win_rate(
    win_rate: float,
    trade_lines: List[str],
    current_config_snippet: str,
) -> Optional[Dict[str, Any]]:
    """
    Call Claude Sonnet to analyse low win rate and suggest threshold fixes.
    Returns dict with keys: analysis, fix_key, old_val, new_val, reason
    """
    if not _api_available or _anthropic_client is None:
        return None
    wins   = sum(1 for l in trade_lines if "WIN" in l.upper())
    losses = len(trade_lines) - wins
    trades_summary = (
        f"{wins}W/{losses}L = {win_rate:.0%} WR "
        f"(last {len(trade_lines)} trades)"
    )
    prompt = (
        f"Trading bot performance alert:\n"
        f"Last {len(trade_lines)} trades: {trades_summary}\n\n"
        f"Recent trade log:\n" + "\n".join(trade_lines[-20:]) + "\n\n"
        f"Current config snippet:\n{current_config_snippet}\n\n"
        "Suggest ONE targeted config threshold adjustment to improve win rate. "
        "Only suggest changes to: score thresholds (FINAL_EXEC_MIN_SCORE, MIN_SIGNAL_SCORE), "
        "volume filters (VOLUME_SURGE_MULTIPLIER), or boolean feature flags.\n"
        'Return JSON: {"analysis": "...", "fix_key": "CONFIG_VAR_NAME", '
        '"old_val": "current_value_as_string", "new_val": "suggested_value_as_string", '
        '"reason": "..."}'
    )
    try:
        response = _anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=(
                "You are a quantitative trading strategy optimizer. "
                "Respond with valid JSON only. "
                "Be conservative — suggest small incremental changes."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as exc:
        logger.warning(f"Win rate Sonnet analysis failed: {exc}")
    return None


# ── Backup helpers ────────────────────────────────────────────────────────────

def _backup_file(filepath: Path) -> Optional[Path]:
    """Create timestamped backup. Returns backup path or None."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=ET).strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"{ts}_{filepath.name}.bak"
    try:
        shutil.copy2(str(filepath), str(backup_path))
        logger.info(f"Backup created: {backup_path}")
        return backup_path
    except Exception as exc:
        logger.error(f"Backup failed for {filepath}: {exc}")
        return None


def _restore_backup(filepath: Path, backup_path: Path) -> bool:
    """Restore a file from its backup."""
    try:
        shutil.copy2(str(backup_path), str(filepath))
        logger.info(f"Restored {filepath} from {backup_path}")
        return True
    except Exception as exc:
        logger.error(f"Restore failed: {exc}")
        return False


# ── Syntax check ──────────────────────────────────────────────────────────────

def _syntax_check(filepath: Path) -> bool:
    """Return True if file passes py_compile check."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(filepath)],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.error(f"Syntax check error: {exc}")
        return False


# ── File permission helpers ───────────────────────────────────────────────────

def _is_file_safe_to_modify(filename: str) -> bool:
    """Return True only if filename is in SAFE_TO_MODIFY and not in FORBIDDEN_FILES."""
    basename = Path(filename).name
    for forbidden in FORBIDDEN_FILES:
        if basename == Path(forbidden).name or filename.endswith(forbidden):
            return False
    for safe in SAFE_TO_MODIFY:
        if filename.endswith(safe) or basename == Path(safe).name:
            return True
    return False


# ── File modification helpers ─────────────────────────────────────────────────

def _apply_code_fix(
    filepath: Path,
    old_string: str,
    new_string: str,
    state: Dict[str, Any],
    reason: str,
    backup_path: Path,
) -> bool:
    """
    Apply old→new string replacement, syntax-check, revert on failure.
    Returns True on success.
    """
    try:
        content = filepath.read_text()
    except Exception as exc:
        logger.error(f"Could not read {filepath}: {exc}")
        return False

    if old_string not in content:
        logger.warning(f"old_string not found in {filepath} — skipping fix")
        return False

    new_content = content.replace(old_string, new_string, 1)
    try:
        filepath.write_text(new_content)
    except Exception as exc:
        logger.error(f"Could not write {filepath}: {exc}")
        return False

    if not _syntax_check(filepath):
        logger.warning(f"Syntax check failed after edit — reverting {filepath}")
        _restore_backup(filepath, backup_path)
        _telegram_send(
            f"*AI Supervisor — syntax check failed, fix reverted*\n"
            f"File: `{filepath.name}`\n"
            f"Reason attempted: {reason}\n"
            f"Backup intact at: `{backup_path.name}`",
            "WARN",
        )
        return False

    logger.info(f"Code fix applied successfully: {filepath}")
    return True


def _apply_config_fix(
    config_file: Path,
    fix_key: str,
    old_val: str,
    new_val: str,
    state: Dict[str, Any],
    reason: str,
    backup_path: Path,
) -> bool:
    """
    Apply a config key=value change in .env or config.py.
    Returns True on success.
    """
    try:
        content = config_file.read_text()
    except Exception as exc:
        logger.error(f"Could not read {config_file}: {exc}")
        return False

    # Try env-style: KEY=VALUE (single-line)
    env_pattern = re.compile(
        rf"^({re.escape(fix_key)}\s*=\s*)(.+)$", re.MULTILINE
    )
    m = env_pattern.search(content)
    if m:
        new_content = env_pattern.sub(rf"\g<1>{new_val}", content)
    else:
        # Try Python assignment (with optional type annotation)
        py_pattern = re.compile(
            rf"^({re.escape(fix_key)}\s*(?::\s*\w+\s*)?=\s*)(.+)$",
            re.MULTILINE,
        )
        m2 = py_pattern.search(content)
        if m2:
            new_content = py_pattern.sub(rf"\g<1>{new_val}", content)
        else:
            # Append as new entry
            new_content = content.rstrip() + f"\n{fix_key}={new_val}\n"

    try:
        config_file.write_text(new_content)
    except Exception as exc:
        logger.error(f"Could not write {config_file}: {exc}")
        return False

    # Only syntax-check .py files
    if config_file.suffix == ".py" and not _syntax_check(config_file):
        logger.warning("Syntax check failed on config fix — reverting")
        _restore_backup(config_file, backup_path)
        _telegram_send(
            f"*AI Supervisor — config syntax check failed, reverted*\n"
            f"Key: `{fix_key}` in `{config_file.name}`\n"
            f"Backup: `{backup_path.name}`",
            "WARN",
        )
        return False

    logger.info(f"Config fix applied: {fix_key} = {new_val} in {config_file}")
    return True


# ── Bot restart signalling ────────────────────────────────────────────────────

def _signal_bot_restart(bot: str = "us") -> None:
    """Send SIGTERM to the bot process; the watchdog runner will restart it."""
    pattern = "python.*main_india.py" if bot == "india" else "python.*main\\.py"
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().split():
            pid_str = pid_str.strip()
            if pid_str:
                try:
                    os.kill(int(pid_str), signal.SIGTERM)
                    logger.info(f"Sent SIGTERM to {bot} bot PID {pid_str}")
                except ProcessLookupError:
                    pass
    except Exception as exc:
        logger.debug(f"Bot restart signal error: {exc}")


def _is_bot_running(bot: str = "us") -> bool:
    pattern = "python.*main_india.py" if bot == "india" else "python.*main\\.py"
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Win rate parsing ──────────────────────────────────────────────────────────

def _parse_recent_trades(log_path: Path, n: int = 20) -> List[str]:
    """Return lines containing trade outcomes (WIN/LOSS with P&L)."""
    lines = _read_log_tail(log_path, 1000)
    trade_lines = [
        ln for ln in lines
        if ("WIN" in ln.upper() or "LOSS" in ln.upper())
        and (
            "P&L" in ln
            or "pnl" in ln.lower()
            or "profit" in ln.lower()
            or "loss" in ln.lower()
        )
    ]
    return trade_lines[-n:]


def _calculate_win_rate(trade_lines: List[str]) -> Optional[float]:
    if not trade_lines:
        return None
    wins   = sum(1 for ln in trade_lines if "WIN" in ln.upper())
    losses = sum(1 for ln in trade_lines if "LOSS" in ln.upper())
    total  = wins + losses
    if total < WIN_RATE_TRADE_WINDOW:
        return None
    return wins / total


# ── Config snippet helper ─────────────────────────────────────────────────────

def _get_config_snippet(is_india: bool = False) -> str:
    cfg_path = (
        BASE_DIR / "india" / "config_india.py"
        if is_india
        else BASE_DIR / "config.py"
    )
    try:
        relevant = [
            ln for ln in cfg_path.read_text().splitlines()
            if any(
                k in ln
                for k in [
                    "FINAL_EXEC_MIN_SCORE",
                    "MIN_SIGNAL_SCORE",
                    "GRAND_SLAM",
                    "VOLUME_SURGE_MULTIPLIER",
                    "FALSE_BREAKOUT",
                    "ENABLED",
                ]
            )
        ]
        return "\n".join(relevant[:30])
    except Exception:
        return ""


# ── Core supervisor class ─────────────────────────────────────────────────────

class AISupervisor:
    def __init__(self) -> None:
        self.state = _load_state()
        self._reset_daily()
        self._last_win_rate_check: float = 0.0
        # Crash-watch tracking
        self._fix_times:   List[float]           = []
        self._fix_bots:    List[str]             = []
        self._fix_backups: List[Optional[Path]]  = []
        self._fix_files:   List[Path]            = []

    def _reset_daily(self) -> None:
        self.state = _reset_daily_counter_if_needed(self.state)
        _save_state(self.state)

    def _can_fix(self) -> Tuple[bool, str]:
        """Returns (allowed, reason_if_blocked)."""
        self._reset_daily()
        if self.state.get("fixes_today", 0) >= MAX_FIXES_PER_DAY:
            return False, f"Daily fix limit reached ({MAX_FIXES_PER_DAY}/day)"
        elapsed = time.time() - self.state.get("last_fix_time", 0)
        if elapsed < FIX_COOLDOWN_SECS:
            remaining = int(FIX_COOLDOWN_SECS - elapsed)
            return False, f"Fix cooldown active ({remaining}s remaining)"
        return True, ""

    def _record_fix(
        self,
        filepath: Path,
        reason: str,
        backup_path: Optional[Path],
        bot: str = "us",
    ) -> None:
        now = time.time()
        self.state["fixes_today"]    = self.state.get("fixes_today", 0) + 1
        self.state["last_fix_time"]  = now
        fix_record = {
            "time":     datetime.now(tz=ET).isoformat(),
            "file":     str(filepath),
            "reason":   reason,
            "backup":   str(backup_path) if backup_path else "",
            "reverted": False,
        }
        self.state.setdefault("applied_fixes", []).append(fix_record)
        _save_state(self.state)
        # Register for crash-watch
        self._fix_times.append(now)
        self._fix_bots.append(bot)
        self._fix_backups.append(backup_path)
        self._fix_files.append(filepath)

    def _check_crash_after_fix(self) -> None:
        """
        For fixes applied in the last CRASH_WATCH_SECS window,
        check whether the bot is still running. Revert if it crashed.
        """
        now = time.time()
        to_revert: List[int] = []

        for i, (fix_time, bot, backup, filepath) in enumerate(
            zip(self._fix_times, self._fix_bots, self._fix_backups, self._fix_files)
        ):
            elapsed = now - fix_time
            if 0 < elapsed < CRASH_WATCH_SECS and not _is_bot_running(bot):
                to_revert.append(i)

        for i in reversed(to_revert):
            fix_time  = self._fix_times[i]
            bot       = self._fix_bots[i]
            backup    = self._fix_backups[i]
            filepath  = self._fix_files[i]
            elapsed_s = int(time.time() - fix_time)
            fix_label = datetime.fromtimestamp(fix_time, tz=ET).strftime("%H:%M")

            logger.warning(
                f"Bot '{bot}' crashed {elapsed_s}s after fix to "
                f"{filepath.name} — reverting"
            )
            reverted = False
            if backup and backup.exists():
                reverted = _restore_backup(filepath, backup)

            # Mark reverted in persistent state
            for fix_record in self.state.get("applied_fixes", []):
                if fix_record.get("file") == str(filepath):
                    fix_record["reverted"] = True
            _save_state(self.state)

            _telegram_send(
                f"*AI Supervisor Reverted Fix*\n"
                f"Fix from {fix_label} caused bot crash within {elapsed_s}s\n"
                f"Reverted: `{filepath.name}` → "
                f"{'backup restored' if reverted else 'RESTORE FAILED'}\n"
                f"Manual review needed: check `logs/ai_supervisor.log`",
                "REVERT",
            )

            self._fix_times.pop(i)
            self._fix_bots.pop(i)
            self._fix_backups.pop(i)
            self._fix_files.pop(i)

        # Prune entries older than crash-watch window
        cutoff = now - CRASH_WATCH_SECS
        while self._fix_times and self._fix_times[0] < cutoff:
            self._fix_times.pop(0)
            self._fix_bots.pop(0)
            self._fix_backups.pop(0)
            self._fix_files.pop(0)

    # ── Error processing ──────────────────────────────────────────────────────

    def _process_error(
        self,
        error_line: str,
        context_lines: List[str],
        bot: str,
    ) -> None:
        """
        Decide how to handle one novel error line.
          1. Match known-safe pattern → instant fix (no API call)
          2. No API → Telegram alert only
          3. Call Haiku to diagnose
          4. If code fix recommended → call Sonnet for patch
        """
        can_fix, block_reason = self._can_fix()

        # 1. Known safe pattern?
        known = _check_known_safe(error_line)
        if known:
            description, fix_type = known
            logger.info(f"Known-safe pattern: {description} ({fix_type})")
            if fix_type == "restart" and can_fix:
                _signal_bot_restart(bot)
                dummy_path = BASE_DIR / (
                    "india/main_india.py" if bot == "india" else "main.py"
                )
                self._record_fix(
                    dummy_path,
                    f"Known-safe transient error: {description}",
                    None,
                    bot,
                )
                _telegram_send(
                    f"*AI Supervisor Fixed*\n"
                    f"Error: `{error_line[:80]}`\n"
                    f"Cause: {description} (known transient)\n"
                    f"Fix: Bot restart scheduled\n"
                    f"Bot: restarting in 30s",
                    "FIX",
                )
            elif fix_type == "ignore":
                logger.info(f"Ignoring known-safe non-actionable: {description}")
            return

        # 2. No API available
        if not _api_available:
            logger.info(
                f"Novel error (no API — alerting only): {error_line[:100]}"
            )
            _telegram_send(
                f"*AI Supervisor — Novel Error Detected*\n"
                f"Bot: {bot}\n"
                f"Error: `{error_line[:150]}`\n"
                f"_No API key — manual review needed_",
                "WARN",
            )
            return

        if not can_fix:
            logger.info(
                f"Novel error but fix blocked ({block_reason}): {error_line[:80]}"
            )
            return

        # 3. Diagnose with Haiku
        context = "\n".join(context_lines[-15:])
        logger.info(f"Calling Haiku to diagnose: {error_line[:80]}")
        diagnosis = _diagnose_with_haiku(error_line, context)
        if not diagnosis:
            logger.warning("Haiku diagnosis returned no result")
            return

        severity    = diagnosis.get("severity", "LOW")
        cause       = diagnosis.get("cause", "unknown")
        fix_type    = diagnosis.get("fix_type", "none")
        fix_detail  = diagnosis.get("fix_detail", "")
        target_file = diagnosis.get("target_file", "")

        logger.info(
            f"Haiku: severity={severity} fix_type={fix_type} cause={cause}"
        )

        if fix_type == "none" or severity == "LOW":
            logger.info(f"Diagnosis: {severity}/{fix_type} — no action needed")
            return

        # ── Restart recommendation ────────────────────────────────────────────
        if fix_type == "restart":
            _signal_bot_restart(bot)
            dummy_path = BASE_DIR / (
                "india/main_india.py" if bot == "india" else "main.py"
            )
            self._record_fix(
                dummy_path,
                f"Haiku restart recommendation: {cause}",
                None,
                bot,
            )
            _telegram_send(
                f"*AI Supervisor Fixed*\n"
                f"Error: `{error_line[:80]}`\n"
                f"Cause: {cause}\n"
                f"Fix: Bot restart (severity: {severity})\n"
                f"Bot: restarting in 30s",
                "FIX",
            )
            return

        # ── Config fix ────────────────────────────────────────────────────────
        if fix_type == "config":
            kv_match = re.search(r"([\w_]+)\s*[=:]\s*(.+)", fix_detail)
            if not kv_match:
                logger.warning(
                    f"Config fix_detail unparseable: {fix_detail}"
                )
                return
            fix_key = kv_match.group(1).strip()
            new_val = kv_match.group(2).strip()

            target_cfg = BASE_DIR / ".env" if (BASE_DIR / ".env").exists() else (
                BASE_DIR / "india" / "config_india.py"
                if bot == "india"
                else BASE_DIR / "config.py"
            )
            backup_path = _backup_file(target_cfg)
            if not backup_path:
                return

            try:
                content   = target_cfg.read_text()
                old_match = re.search(
                    rf"{re.escape(fix_key)}\s*[=:]\s*(\S+)", content
                )
                old_val   = old_match.group(1) if old_match else "unknown"
            except Exception:
                old_val = "unknown"

            success = _apply_config_fix(
                target_cfg, fix_key, old_val, new_val,
                self.state, cause, backup_path,
            )
            if success:
                self._record_fix(
                    target_cfg,
                    f"Config fix: {cause}",
                    backup_path,
                    bot,
                )
                _signal_bot_restart(bot)
                _telegram_send(
                    f"*AI Supervisor Fixed*\n"
                    f"Error: `{error_line[:80]}`\n"
                    f"Cause: {cause}\n"
                    f"Fix: Set `{fix_key}` {old_val}→{new_val} "
                    f"in `{target_cfg.name}`\n"
                    f"Backup: `{backup_path.name}`\n"
                    f"Bot: restarting in 30s",
                    "FIX",
                )
            return

        # ── Code fix ─────────────────────────────────────────────────────────
        if fix_type == "code":
            if not target_file:
                logger.info(
                    "Haiku gave code fix_type but no target_file — skipping"
                )
                return

            # Resolve the path
            filepath = BASE_DIR / target_file
            if not filepath.exists():
                filepath = BASE_DIR / "india" / target_file
            if not filepath.exists():
                logger.warning(f"Target file not found: {target_file}")
                return

            rel = str(filepath.relative_to(BASE_DIR))
            if not _is_file_safe_to_modify(rel):
                logger.warning(f"File not safe to modify: {filepath}")
                return

            try:
                code_context = "\n".join(filepath.read_text().splitlines()[:80])
            except Exception:
                code_context = ""

            logger.info(f"Calling Sonnet for code fix on {filepath.name}")
            fix_pair = _generate_code_fix(
                filepath.name, error_line, code_context
            )
            if not fix_pair:
                logger.warning("Sonnet did not return a valid fix pair")
                return

            old_string, new_string = fix_pair
            backup_path = _backup_file(filepath)
            if not backup_path:
                return

            success = _apply_code_fix(
                filepath, old_string, new_string,
                self.state, cause, backup_path,
            )
            if success:
                self._record_fix(
                    filepath,
                    f"Code fix: {cause}",
                    backup_path,
                    bot,
                )
                _signal_bot_restart(bot)
                _telegram_send(
                    f"*AI Supervisor Fixed*\n"
                    f"Error: `{error_line[:80]}`\n"
                    f"Cause: {cause}\n"
                    f"Fix: Code patch applied to `{filepath.name}`\n"
                    f"Backup: `{backup_path.name}`\n"
                    f"Bot: restarting in 30s",
                    "FIX",
                )

    # ── Scan loop ─────────────────────────────────────────────────────────────

    def scan_logs(self) -> None:
        """Read both bot logs, extract new errors, process up to 5 per scan."""
        for bot, log_path in [("us", US_BOT_LOG), ("india", INDIA_BOT_LOG)]:
            lines = _read_log_tail(log_path)
            if not lines:
                continue
            error_lines = _extract_error_lines(lines)
            if not error_lines:
                continue
            new_errors, updated = _get_new_errors(
                error_lines,
                self.state.get("seen_errors", []),
            )
            self.state["seen_errors"] = updated
            _save_state(self.state)

            for error_line in new_errors[:5]:
                logger.info(
                    f"[{bot}] New error detected: {error_line[:100]}"
                )
                self._process_error(error_line, lines, bot)

    # ── Win rate check ────────────────────────────────────────────────────────

    def check_win_rate(self) -> None:
        """Parse recent trades; trigger Sonnet analysis if WR falls below threshold."""
        for bot, log_path in [("us", US_BOT_LOG), ("india", INDIA_BOT_LOG)]:
            trade_lines = _parse_recent_trades(log_path, WIN_RATE_TRADE_WINDOW * 2)
            wr = _calculate_win_rate(trade_lines[-WIN_RATE_TRADE_WINDOW:])
            if wr is None:
                logger.debug(
                    f"[{bot}] Not enough trade data for WR calculation"
                )
                continue

            logger.info(
                f"[{bot}] Win rate (last {WIN_RATE_TRADE_WINDOW} trades): {wr:.1%}"
            )

            if wr >= WIN_RATE_THRESHOLD:
                continue  # performance is acceptable

            can_fix, block_reason = self._can_fix()
            wins   = int(wr * WIN_RATE_TRADE_WINDOW)
            losses = WIN_RATE_TRADE_WINDOW - wins

            if not can_fix:
                logger.info(f"Win rate low but fix blocked: {block_reason}")
                _telegram_send(
                    f"*AI Win Rate Alert*\n"
                    f"Bot: {bot} | Last {WIN_RATE_TRADE_WINDOW} trades: "
                    f"{wins}W/{losses}L = {wr:.0%} WR\n"
                    f"Fix blocked: {block_reason} — manual review suggested",
                    "WR",
                )
                continue

            if not _api_available:
                _telegram_send(
                    f"*AI Win Rate Alert*\n"
                    f"Bot: {bot}\n"
                    f"Last {WIN_RATE_TRADE_WINDOW} trades: "
                    f"{wins}W/{losses}L = {wr:.0%} WR\n"
                    f"_No API key — cannot auto-analyse. "
                    f"Manual review recommended._",
                    "WR",
                )
                continue

            is_india       = (bot == "india")
            config_snippet = _get_config_snippet(is_india)

            logger.info(f"[{bot}] Calling Sonnet for win-rate analysis")
            result = _analyze_win_rate(
                wr,
                trade_lines[-WIN_RATE_TRADE_WINDOW:],
                config_snippet,
            )
            if not result:
                logger.warning("Sonnet win-rate analysis returned no result")
                continue

            analysis    = result.get("analysis", "")
            fix_key     = result.get("fix_key", "")
            old_val     = result.get("old_val", "")
            new_val     = result.get("new_val", "")
            reason_txt  = result.get("reason", "")

            logger.info(
                f"Sonnet WR suggestion: {fix_key} {old_val}→{new_val} — {reason_txt}"
            )

            if not fix_key or not new_val:
                logger.info("Sonnet did not produce an actionable fix")
                continue

            # Clamp numeric changes to ±SCORE_STEP*3 for safety
            try:
                old_f = float(old_val)
                new_f = float(new_val)
                max_delta = SCORE_STEP * 3
                if abs(new_f - old_f) > max_delta:
                    logger.warning(
                        f"Sonnet suggested change too large "
                        f"({old_f}→{new_f}) — clamping to ±{max_delta}"
                    )
                    new_f = (
                        old_f + max_delta
                        if new_f > old_f
                        else old_f - max_delta
                    )
                    new_val = str(round(new_f, 1))
            except ValueError:
                pass  # boolean or non-numeric value — allow as-is

            target_cfg = (
                BASE_DIR / ".env"
                if (BASE_DIR / ".env").exists()
                else (
                    BASE_DIR / "india" / "config_india.py"
                    if is_india
                    else BASE_DIR / "config.py"
                )
            )

            backup_path = _backup_file(target_cfg)
            if not backup_path:
                continue

            success = _apply_config_fix(
                target_cfg,
                fix_key,
                old_val,
                new_val,
                self.state,
                f"Win rate optimisation: {reason_txt}",
                backup_path,
            )
            if success:
                self._record_fix(
                    target_cfg,
                    f"WR optimisation: {reason_txt}",
                    backup_path,
                    bot,
                )
                _telegram_send(
                    f"*AI Win Rate Alert*\n"
                    f"Last {WIN_RATE_TRADE_WINDOW} trades: "
                    f"{wins}W/{losses}L = {wr:.0%} WR\n"
                    f"Analysis: {analysis}\n"
                    f"Fix: Raised `{fix_key}` {old_val}→{new_val} "
                    f"in `{target_cfg.name}`\n"
                    f"Expected: {reason_txt}",
                    "WR",
                )

    # ── Main cycle ────────────────────────────────────────────────────────────

    def run_once(self) -> None:
        """One full supervisor cycle."""
        self._check_crash_after_fix()

        if not _is_active_hours():
            logger.debug("Outside active hours — skipping scan")
            return

        try:
            self.scan_logs()
        except Exception as exc:
            logger.error(f"scan_logs error: {exc}", exc_info=True)

        if time.time() - self._last_win_rate_check >= WIN_RATE_INTERVAL_SECS:
            try:
                self.check_win_rate()
            except Exception as exc:
                logger.error(f"check_win_rate error: {exc}", exc_info=True)
            self._last_win_rate_check = time.time()

    def run(self) -> None:
        """Main supervisor loop — runs forever."""
        logger.info("=" * 60)
        logger.info("AI Supervisor started")
        logger.info(f"API available:       {_api_available}")
        logger.info(f"Max fixes/day:       {MAX_FIXES_PER_DAY}")
        logger.info(f"Scan interval:       {SCAN_INTERVAL_SECS}s")
        logger.info(f"Win rate interval:   {WIN_RATE_INTERVAL_SECS}s")
        logger.info(f"Fix cooldown:        {FIX_COOLDOWN_SECS}s")
        logger.info(f"Crash watch window:  {CRASH_WATCH_SECS}s")
        logger.info("=" * 60)

        _telegram_send(
            "*AI Supervisor started*\n"
            f"Claude API: "
            f"{'active' if _api_available else 'alerts-only mode (no API key)'}\n"
            f"Max fixes/day: {MAX_FIXES_PER_DAY} | "
            f"Cooldown: {FIX_COOLDOWN_SECS // 60}min\n"
            "_Monitoring US + India bots_",
            "OK",
        )

        def _handle_exit(signum, frame):  # type: ignore[misc]
            logger.info("AI Supervisor shutting down gracefully")
            sys.exit(0)

        signal.signal(signal.SIGTERM, _handle_exit)
        signal.signal(signal.SIGINT,  _handle_exit)

        while True:
            try:
                self.run_once()
            except Exception as exc:
                logger.error(f"Supervisor cycle error: {exc}", exc_info=True)
            time.sleep(SCAN_INTERVAL_SECS)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    _init_anthropic()
    supervisor = AISupervisor()
    supervisor.run()


if __name__ == "__main__":
    main()
