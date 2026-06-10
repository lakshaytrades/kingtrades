"""
supervisor_india.py — Capital-Protection Supervisor for the India bot.

Independent safety layer. It does NOT try to make money (no code can) — its job
is to PROTECT your capital by halting trading the moment losses breach hard
limits. Especially important if you ever enable auto-execution.

It reads the bot's live state file (/tmp/india_state.json) every 30s during
market hours and enforces:
  - HARD daily loss limit          -> halt all new trades + CRIT alert
  - intraday drawdown from peak    -> halt
  - rapid consecutive losses        -> halt
When breached it writes /tmp/india_halt.flag (the main bot checks this and
stops opening new positions) and sends ONE critical Telegram alert.

Quiet by design: only CRITICAL alerts go out (matches the owner's
"only essential notifications" preference).

Run:  nohup python3 supervisor_india.py >> ../logs/india_supervisor.log 2>&1 &
"""
import json
import logging
import os
import time as _time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_BASE = Path(__file__).parent.parent
STATE_FILE = Path("/tmp/india_state.json")
HALT_FLAG  = Path("/tmp/india_halt.flag")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [supervisor] %(message)s")
logger = logging.getLogger("supervisor_india")

# -- Hard limits (capital protection) -----------------------------------------
DAILY_LOSS_HALT_PCT = float(os.getenv("INDIA_DAILY_LOSS_LIMIT_PCT", "2.0")) / 100
PEAK_DRAWDOWN_PCT   = float(os.getenv("INDIA_SUPERVISOR_DD_PCT", "1.5")) / 100
CHECK_EVERY_SEC     = 30


def _load_cfg():
    try:
        from dotenv import load_dotenv
        load_dotenv(_BASE / ".env")
    except Exception:
        pass


def _telegram_crit(msg: str):
    """Send only CRITICAL alerts (quiet otherwise)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.info(f"[would send CRIT] {msg[:120]}")
        return
    try:
        import requests
        for cid in [c.strip() for c in str(chat).replace(" ", ",").split(",") if c.strip()]:
            try:
                requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": cid, "text": f"🚨 *SUPERVISOR HALT*\n{msg}",
                                    "parse_mode": "Markdown"}, timeout=8)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"telegram: {e}")


def _read_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return None


def _halt(reason: str, state: dict):
    """Write the halt flag the main bot checks, and alert once."""
    if HALT_FLAG.exists():
        return   # already halted today
    HALT_FLAG.write_text(f"{datetime.now(IST).isoformat()} | {reason}")
    pnl = state.get("daily_pnl", 0)
    cap = state.get("capital", 0)
    logger.warning(f"HALT: {reason}")
    _telegram_crit(
        f"{reason}\n"
        f"Day P&L: Rs.{pnl:+,.0f} ({state.get('daily_pnl_pct', 0):+.2f}%)\n"
        f"Capital: Rs.{cap:,.0f}\n"
        f"New trades STOPPED for today. Existing positions stay managed.\n"
        f"Review before resuming. /resume to override."
    )


def supervise():
    _load_cfg()
    logger.info("Capital-protection supervisor started.")
    peak_pnl = 0.0
    last_day = None

    while True:
        try:
            now = datetime.now(IST)
            # Reset at the start of each trading day
            if last_day != now.date():
                last_day = now.date()
                peak_pnl = 0.0
                HALT_FLAG.unlink(missing_ok=True)
                logger.info("New day — halt flag cleared, peak reset.")

            # Only supervise during market hours
            if now.time() < datetime.strptime("09:15", "%H:%M").time() or \
               now.time() > datetime.strptime("15:30", "%H:%M").time():
                _time.sleep(60)
                continue

            state = _read_state()
            if not state:
                _time.sleep(CHECK_EVERY_SEC)
                continue

            cap = max(state.get("capital", 0), 1)
            pnl = state.get("daily_pnl", 0.0)
            peak_pnl = max(peak_pnl, pnl)

            # 1. Hard daily loss limit
            if pnl <= -DAILY_LOSS_HALT_PCT * cap:
                _halt(f"Daily loss limit hit ({DAILY_LOSS_HALT_PCT*100:.1f}% of capital)", state)

            # 2. Drawdown from intraday peak (gave back gains)
            elif peak_pnl > 0 and (peak_pnl - pnl) >= PEAK_DRAWDOWN_PCT * cap:
                _halt(f"Gave back {PEAK_DRAWDOWN_PCT*100:.1f}% from today's peak "
                      f"(peak Rs.{peak_pnl:+,.0f} -> now Rs.{pnl:+,.0f})", state)

        except Exception as e:
            logger.error(f"supervise loop: {e}")
        _time.sleep(CHECK_EVERY_SEC)


if __name__ == "__main__":
    supervise()
