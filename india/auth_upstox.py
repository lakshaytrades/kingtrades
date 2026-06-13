"""
auth_upstox.py — Upstox API v2 authentication with daily-token tracking

Upstox access tokens expire DAILY at ~03:30 AM IST (unlike Dhan's 30-day token).
This module:
  - Tracks token age in data/upstox_token_meta.json
  - Warns via Telegram when the token is stale / a new day has started
  - Supports /newtoken TOKEN command from Telegram to update .env + restart
  - Zero manual VPS access needed — all managed via Telegram

Token renewal (60 seconds, no VPS):
  1. Generate today's access token from your Upstox developer app
     (OAuth login → authorization code → access token), or use the
     Upstox token-generation helper / your existing daily-login script.
  2. Send to Telegram: /newtoken YOUR_NEW_ACCESS_TOKEN
  3. Bot auto-updates .env, confirms, and restarts.

Env vars (group: trades secret):
  UPSTOX_ACCESS_TOKEN   — today's access token (required)
  UPSTOX_API_KEY        — developer app api key / client id (for regeneration)
  UPSTOX_API_SECRET     — developer app secret (for regeneration)
"""
import json
import logging
import os
import re
import time as _time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("auth_upstox")
IST = ZoneInfo("Asia/Kolkata")

_BASE        = Path(__file__).parent.parent
_TOKEN_META  = _BASE / "data" / "upstox_token_meta.json"
_ENV_PATH    = _BASE / ".env"
_TOKEN_EXPIRY_HOUR = 6   # Upstox tokens expire ~03:30; warn after 06:00 IST next day


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
    now = datetime.now(IST)
    meta["token_set_at"] = now.isoformat()
    meta["token_prefix"] = token[:8] + "***" if len(token) > 8 else "***"
    # Upstox tokens die at ~03:30 IST the next calendar day
    nxt = (now + timedelta(days=1)).replace(hour=3, minute=30, second=0, microsecond=0)
    meta["expires_approx"] = nxt.isoformat()
    _save_meta(meta)


def get_token_age_days() -> float:
    """Return days since the current token was set. -1 if unknown."""
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
    """
    Days until token expiry. Upstox tokens expire at ~03:30 IST next day.
    Negative means already past expiry (stale — needs renewal today).
    """
    meta = _load_meta()
    exp = meta.get("expires_approx")
    if not exp:
        return 1.0   # unknown — assume valid for today
    try:
        dt = datetime.fromisoformat(exp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return (dt - datetime.now(IST)).total_seconds() / 86400
    except Exception:
        return 1.0


def is_token_stale() -> bool:
    """True if the token was set on a prior day (Upstox = daily token)."""
    meta = _load_meta()
    set_at = meta.get("token_set_at")
    if not set_at:
        return False
    try:
        dt = datetime.fromisoformat(set_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        now = datetime.now(IST)
        # Stale if set before today's 03:30 expiry boundary
        today_expiry = now.replace(hour=3, minute=30, second=0, microsecond=0)
        return dt < today_expiry and now >= today_expiry
    except Exception:
        return False


# ── Token update from Telegram ────────────────────────────────────────────────

def update_token_in_env(new_token: str) -> Tuple[bool, str]:
    """
    Write new UPSTOX_ACCESS_TOKEN to .env file (atomic).
    Returns (success, message). Called on /newtoken TOKEN via Telegram.
    """
    new_token = new_token.strip()
    if len(new_token) < 20:
        return False, "Token too short — looks invalid"
    try:
        key      = "UPSTOX_ACCESS_TOKEN"
        env_text = _ENV_PATH.read_text() if _ENV_PATH.exists() else ""
        pattern  = rf"^{re.escape(key)}\s*=.*$"
        if re.search(pattern, env_text, flags=re.MULTILINE):
            env_text = re.sub(pattern, f"{key}={new_token}", env_text, flags=re.MULTILINE)
        else:
            env_text = env_text.rstrip("\n") + f"\n{key}={new_token}\n"
        tmp = _ENV_PATH.with_suffix(".env.tmp")
        tmp.write_text(env_text)
        tmp.replace(_ENV_PATH)
        _record_token_set(new_token)
        os.environ["UPSTOX_ACCESS_TOKEN"] = new_token
        logger.info(f"UPSTOX_ACCESS_TOKEN updated via Telegram ({new_token[:8]}***)")
        return True, f"Token updated ({new_token[:8]}***). Bot restarting."
    except Exception as e:
        logger.error(f"Token update failed: {e}")
        return False, f"Update failed: {e}"


# ── Expiry check & Telegram warning ──────────────────────────────────────────

def check_token_expiry_and_warn() -> bool:
    """
    If the token is stale (new day) send a Telegram renewal alert.
    Returns True if token needs renewal. Call daily from watchdog.
    """
    if not is_token_stale():
        return False

    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except Exception:
        pass

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.warning("Upstox token stale (daily expiry) — no Telegram configured")
        return True

    msg = (
        f"🇮🇳 🚨 *Upstox Token Expired (daily)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Upstox access tokens expire every day at ~03:30 IST.\n\n"
        f"*Renew in 60 seconds — no VPS needed:*\n"
        f"1️⃣ Generate today's token from your Upstox app\n"
        f"   (OAuth login → auth code → access token)\n"
        f"2️⃣ Send here: `/newtoken YOUR_TOKEN`\n\n"
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
        logger.info("Upstox token renewal warning sent")
    except Exception as e:
        logger.debug(f"telegram expiry warn: {e}")
    return True


# ── Telegram command polling ──────────────────────────────────────────────────

_UPDATE_ID_FILE = _TOKEN_META.parent / "upstox_tg_update_id.json"


def _load_last_update_id() -> int:
    try:
        if _UPDATE_ID_FILE.exists():
            return int(json.loads(_UPDATE_ID_FILE.read_text()).get("last_update_id", 0))
    except Exception:
        pass
    return 0


def _save_last_update_id(uid: int):
    try:
        _UPDATE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _UPDATE_ID_FILE.write_text(json.dumps({"last_update_id": uid}))
    except Exception:
        pass


def poll_telegram_commands(restart_callback=None) -> Optional[str]:
    """
    Poll Telegram for /newtoken command. Returns the new token if found.
    update_id persisted to disk — old commands never replayed after restart.
    """
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
        last_uid = _load_last_update_id()
        resp = requests.get(
            f"https://api.telegram.org/bot{tg_token}/getUpdates",
            params={"offset": last_uid + 1, "timeout": 5, "limit": 10},
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            return None

        for update in data.get("result", []):
            uid = update.get("update_id", 0)
            if uid > last_uid:
                last_uid = uid
                _save_last_update_id(uid)

            msg = update.get("message", {})
            if not msg:
                continue
            if str(msg.get("chat", {}).get("id", "")) != chat_id:
                continue

            text = (msg.get("text") or "").strip()
            if not text.lower().startswith("/newtoken"):
                continue

            parts = text.split(None, 1)
            if len(parts) < 2:
                _tg_reply(tg_token, chat_id,
                          "🇮🇳 Usage: `/newtoken YOUR_UPSTOX_ACCESS_TOKEN`")
                continue

            new_token = parts[1].strip()
            ok, message = update_token_in_env(new_token)
            if ok:
                _tg_reply(tg_token, chat_id,
                          f"🇮🇳 ✅ *Upstox Token Updated*\n"
                          f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                          f"New token: `{new_token[:8]}***`\n"
                          f"Valid until: ~03:30 IST tomorrow\n"
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


# ── Upstox client wrapper ─────────────────────────────────────────────────────

class UpstoxClient:
    """
    Thin wrapper around upstox_client.ApiClient exposing the API sub-clients
    used by the bot. Built once at startup and shared across data/execution.
    """
    def __init__(self, api_client, access_token: str):
        self._api_client = api_client
        self.access_token = access_token
        self._market = None
        self._history = None
        self._order = None
        self._portfolio = None
        self._user = None

    @property
    def market_quote(self):
        if self._market is None:
            import upstox_client
            self._market = upstox_client.MarketQuoteApi(self._api_client)
        return self._market

    @property
    def history(self):
        if self._history is None:
            import upstox_client
            self._history = upstox_client.HistoryApi(self._api_client)
        return self._history

    @property
    def order(self):
        if self._order is None:
            import upstox_client
            self._order = upstox_client.OrderApi(self._api_client)
        return self._order

    @property
    def portfolio(self):
        if self._portfolio is None:
            import upstox_client
            self._portfolio = upstox_client.PortfolioApi(self._api_client)
        return self._portfolio

    @property
    def user(self):
        if self._user is None:
            import upstox_client
            self._user = upstox_client.UserApi(self._api_client)
        return self._user


def get_upstox_client():
    """
    Initialize and return an UpstoxClient. Reads UPSTOX_ACCESS_TOKEN from env.
    Records token timestamp on first run if no metadata exists.
    """
    access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not access_token:
        logger.error("UPSTOX_ACCESS_TOKEN not set in .env")
        return None

    meta = _load_meta()
    if not meta.get("token_set_at"):
        _record_token_set(access_token)
        logger.info("Upstox token age tracking started (assuming token is fresh)")

    try:
        import upstox_client
        config = upstox_client.Configuration()
        config.access_token = access_token
        api_client = upstox_client.ApiClient(config)
        client = UpstoxClient(api_client, access_token)
        days_left = get_days_until_expiry()
        logger.info(f"Upstox client ready | token valid ~{max(0, days_left):.1f}d "
                    f"(expires ~03:30 IST)")
        return client
    except ImportError:
        logger.error("upstox_client not installed — run: pip install upstox-python-sdk")
        return None
    except Exception as e:
        logger.error(f"Upstox client init failed: {e}")
        return None


def verify_connection(client) -> bool:
    """Ping Upstox API to confirm the access token is valid.

    Falls back to profile API outside market hours (9:30 AM–midnight IST),
    since the Funds API returns HTTP 423 when the service is closed.
    """
    if client is None:
        return False

    # Check if we're in market-hours window (Funds API available 9:30–00:00 IST)
    now_ist = datetime.now(IST)
    in_funds_window = dtime(9, 30) <= now_ist.time() <= dtime(23, 59)

    if in_funds_window:
        try:
            resp = client.user.get_user_fund_margin(api_version="2.0")
            data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
            if data:
                equity = data.get("equity") if isinstance(data, dict) else getattr(data, "equity", None)
                avail = 0.0
                if equity is not None:
                    avail = (equity.get("available_margin") if isinstance(equity, dict)
                             else getattr(equity, "available_margin", 0)) or 0.0
                logger.info(f"Upstox connected — available margin: ₹{float(avail):,.2f}")
                return True
            # 423 or empty response: funds service closed, try profile instead
        except Exception as e:
            err_str = str(e)
            if "423" not in err_str and "locked" not in err_str.lower():
                logger.error(f"Upstox connection verify failed: {e}")
                return False
            logger.info("Upstox Funds API locked (outside hours) — verifying via profile")

    # Outside hours or Funds API locked: verify via profile endpoint instead
    try:
        profile = client.user.get_profile(api_version="2.0")
        data = getattr(profile, "data", None) or (profile.get("data") if isinstance(profile, dict) else None)
        if data:
            name = (data.get("name") if isinstance(data, dict) else getattr(data, "name", "")) or ""
            logger.info(f"Upstox connected (profile) — user: {name}")
            return True
        logger.warning("Upstox profile returned no data")
        return False
    except Exception as e2:
        logger.error(f"Upstox connection verify failed (profile fallback): {e2}")
        return False

