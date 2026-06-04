"""
auth_dhan.py — Dhan API authentication with auto-expiry tracking

Dhan access tokens last ~30 days. This module:
  - Tracks token age in data/dhan_token_meta.json
  - Warns via Telegram at day 25 (+ daily after)
  - Supports /newtoken TOKEN command from Telegram to update .env + restart
  - Zero manual VPS access needed — all managed via Telegram

Token renewal process (60 seconds, no VPS):
  1. Go to https://dhanhq.co → My Account → API → Generate Token
  2. Copy the new access token
  3. Send to Telegram: /newtoken YOUR_NEW_TOKEN
  4. Bot auto-updates .env, sends confirmation, and restarts
"""
import json
import logging
import os
import re
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("auth_dhan")
IST = ZoneInfo("Asia/Kolkata")

_BASE          = Path(__file__).parent.parent
_TOKEN_META    = _BASE / "data" / "dhan_token_meta.json"
_ENV_PATH      = _BASE / ".env"
_TOKEN_TTL_DAYS = 30   # Dhan tokens expire in 30 days
_WARN_DAYS      = 5    # warn 5 days before expiry


# ── Token metadata persistence ────────────────────────────────────────────────

def _load_meta() -> dict:
    try:
        if _TOKEN_META.exists():
            return json.loads(_TOKEN_META.read_text())
    except Exception:
        pass
    return {}


def _save_meta(meta: dict):
    try:
        _TOKEN_META.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_META.write_text(json.dumps(meta, indent=2, default=str))
    except Exception as e:
        logger.debug(f"token meta save: {e}")


def _record_token_set(token: str):
    """Call whenever a new token is written — records the timestamp."""
    meta = _load_meta()
    meta["token_set_at"]   = datetime.now(IST).isoformat()
    meta["token_prefix"]   = token[:8] + "***" if len(token) > 8 else "***"
    meta["expires_approx"] = (datetime.now(IST) + timedelta(days=_TOKEN_TTL_DAYS)).isoformat()
    _save_meta(meta)


def get_token_age_days() -> float:
    """Return how many days since the current token was set. -1 if unknown."""
    meta = _load_meta()
    set_at = meta.get("token_set_at")
    if not set_at:
        return -1.0
    try:
        dt = datetime.fromisoformat(set_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return (datetime.now(IST) - dt).total_seconds() / 86400
    except Exception:
        return -1.0


def get_days_until_expiry() -> float:
    """Return days remaining before token expires. Negative = already expired."""
    age = get_token_age_days()
    if age < 0:
        return _TOKEN_TTL_DAYS   # unknown — assume fresh
    return _TOKEN_TTL_DAYS - age


# ── Token update from Telegram ────────────────────────────────────────────────

def update_token_in_env(new_token: str) -> Tuple[bool, str]:
    """
    Write new DHAN_ACCESS_TOKEN to .env file.
    Returns (success, message).
    Called when user sends /newtoken TOKEN via Telegram.
    """
    new_token = new_token.strip()
    if len(new_token) < 20:
        return False, "Token too short — looks invalid"

    try:
        env_text = _ENV_PATH.read_text() if _ENV_PATH.exists() else ""
        key = "DHAN_ACCESS_TOKEN"
        if key in env_text:
            env_text = re.sub(rf"^{key}\s*=.*$", f"{key}={new_token}",
                              env_text, flags=re.MULTILINE)
        else:
            env_text += f"\n{key}={new_token}\n"
        _ENV_PATH.write_text(env_text)
        _record_token_set(new_token)
        os.environ["DHAN_ACCESS_TOKEN"] = new_token
        logger.info(f"DHAN_ACCESS_TOKEN updated via Telegram ({new_token[:8]}***)")
        return True, f"Token updated ({new_token[:8]}***). Bot restarting."
    except Exception as e:
        logger.error(f"Token update failed: {e}")
        return False, f"Update failed: {e}"


# ── Expiry check & Telegram warning ──────────────────────────────────────────

def check_token_expiry_and_warn() -> bool:
    """
    Check token age. If within warning window, send Telegram alert.
    Returns True if token needs renewal (< 2 days left).
    Call this daily from watchdog.
    """
    days_left = get_days_until_expiry()

    if days_left > _WARN_DAYS:
        return False   # plenty of time

    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except Exception:
        pass

    token  = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat   = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.warning(f"Dhan token expires in {days_left:.0f} days — no Telegram configured")
        return days_left < 2

    urgency = "🚨 *URGENT*" if days_left < 2 else ("⚠️ *Warning*" if days_left < 4 else "⏰ *Reminder*")
    msg = (
        f"🇮🇳 {urgency} — Dhan Token Expiring\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Days remaining: `{max(0, days_left):.0f}`\n\n"
        f"*Renew in 60 seconds — no VPS needed:*\n"
        f"1️⃣ Go to: `https://dhanhq.co`\n"
        f"2️⃣ My Account → API → Generate Token\n"
        f"3️⃣ Copy the new token\n"
        f"4️⃣ Send here: `/newtoken YOUR_TOKEN`\n\n"
        f"_Bot auto-updates and restarts. No VPS access needed._\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "Markdown"},
            timeout=8,
        )
        logger.info(f"Token expiry warning sent — {days_left:.0f} days left")
    except Exception as e:
        logger.debug(f"telegram expiry warn: {e}")

    return days_left < 2


# ── Telegram command polling ──────────────────────────────────────────────────

_last_update_id: int = 0


def poll_telegram_commands(restart_callback=None) -> Optional[str]:
    """
    Poll Telegram for /newtoken command.
    Returns the new token string if found, else None.
    Call this in a background loop (every 30s in watchdog).

    If restart_callback is provided, calls it after successful token update.
    """
    global _last_update_id
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except Exception:
        pass

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id  = str(os.getenv("TELEGRAM_CHAT_ID", ""))
    if not tg_token or not chat_id:
        return None

    try:
        import requests
        resp = requests.get(
            f"https://api.telegram.org/bot{tg_token}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 5, "limit": 10},
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            return None

        for update in data.get("result", []):
            uid = update.get("update_id", 0)
            if uid > _last_update_id:
                _last_update_id = uid

            msg = update.get("message", {})
            if not msg:
                continue

            # Only process messages from the configured chat
            if str(msg.get("chat", {}).get("id", "")) != chat_id:
                continue

            text = (msg.get("text") or "").strip()
            if not text.lower().startswith("/newtoken"):
                continue

            parts = text.split(None, 1)
            if len(parts) < 2:
                _tg_reply(tg_token, chat_id,
                          "🇮🇳 Usage: `/newtoken YOUR_DHAN_ACCESS_TOKEN`")
                continue

            new_token = parts[1].strip()
            ok, message = update_token_in_env(new_token)

            if ok:
                _tg_reply(tg_token, chat_id,
                          f"🇮🇳 ✅ *Token Updated Successfully*\n"
                          f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                          f"New token: `{new_token[:8]}***`\n"
                          f"Expires in: ~{_TOKEN_TTL_DAYS} days\n"
                          f"Bot is restarting now...")
                logger.info("Token updated via Telegram command")
                if restart_callback:
                    _time.sleep(2)
                    restart_callback()
                return new_token
            else:
                _tg_reply(tg_token, chat_id, f"🇮🇳 ❌ Token update failed: {message}")

    except Exception as e:
        logger.debug(f"poll_telegram_commands: {e}")

    return None


def _tg_reply(token: str, chat_id: str, msg: str):
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=8,
        )
    except Exception:
        pass


# ── Main client init ──────────────────────────────────────────────────────────

def get_dhan_client():
    """
    Initialize and return a DhanHQ client instance.
    Reads token from env. Records token age on first run if no metadata exists.
    """
    client_id    = os.getenv("DHAN_CLIENT_ID", "")
    access_token = os.getenv("DHAN_ACCESS_TOKEN", "")

    if not client_id or not access_token:
        logger.error("DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN not set in .env")
        return None

    # Record token timestamp if first time seeing this token
    meta = _load_meta()
    if not meta.get("token_set_at"):
        _record_token_set(access_token)
        logger.info("Token age tracking started (assuming token is fresh)")

    try:
        from dhanhq import dhanhq
        client = dhanhq(client_id, access_token)
        days_left = get_days_until_expiry()
        age_info  = f" | expires in ~{days_left:.0f}d" if days_left >= 0 else ""
        logger.info(f"Dhan client ready (client_id: {client_id[:6]}***{age_info})")
        return client
    except ImportError:
        logger.error("dhanhq not installed — run: pip install dhanhq")
        return None
    except Exception as e:
        logger.error(f"Dhan client init failed: {e}")
        return None


def verify_connection(client) -> bool:
    """Ping Dhan API to confirm credentials are valid."""
    if client is None:
        return False
    try:
        result = client.get_fund_limits()
        if result and result.get("status") == "success":
            limits  = result.get("data", {})
            balance = limits.get("availabelBalance", 0)
            logger.info(f"Dhan connected — available balance: ₹{balance:,.2f}")
            return True
        logger.warning(f"Dhan fund_limits unexpected response: {result}")
        return False
    except Exception as e:
        logger.error(f"Dhan connection verify failed: {e}")
        return False
