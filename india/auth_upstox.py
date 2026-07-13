"""
auth_upstox.py — Upstox API v2 authentication with daily-token tracking

Upstox access tokens expire DAILY at ~03:30 AM IST (unlike Dhan's 30-day token).
This module:
  - Tracks token age in data/upstox_token_meta.json
  - Warns via Telegram when the token is stale / a new day has started
  - Supports /newtoken TOKEN command from Telegram to update .env + restart
  - TOTP auto-renewal at 8:30 AM IST daily (no manual intervention needed)
  - Zero manual VPS access needed — all managed via Telegram

Token renewal (60 seconds, no VPS):
  1. Generate today's access token from your Upstox developer app
     (OAuth login → authorization code → access token), or use the
     Upstox token-generation helper / your existing daily-login script.
  2. Send to Telegram: /newtoken YOUR_NEW_ACCESS_TOKEN
  3. Bot auto-updates .env, confirms, and restarts.

TOTP Auto-Renewal (recommended — no manual steps):
  Set UPSTOX_EMAIL, UPSTOX_PASSWORD, UPSTOX_TOTP_SECRET in .env.
  Call schedule_auto_renewal() once at bot startup.

Env vars (group: trades secret):
  UPSTOX_ACCESS_TOKEN   — today's access token (required)
  UPSTOX_API_KEY        — developer app api key / client id (for regeneration)
  UPSTOX_API_SECRET     — developer app secret (for regeneration)
  UPSTOX_EMAIL          — Upstox login email (TOTP auto-renewal)
  UPSTOX_PASSWORD       — Upstox login password (TOTP auto-renewal)
  UPSTOX_TOTP_SECRET    — TOTP secret Base32 from Upstox 2FA setup (TOTP auto-renewal)
  UPSTOX_REDIRECT_URI   — registered redirect URI (default: https://127.0.0.1/callback)

Circuit breaker env vars (optional — override defaults):
  DAILY_LOSS_HALT_PCT   — daily loss % to halt all trading (default: 0.03 = 3%)
  CONSEC_LOSS_HALT      — consecutive losses before pause (default: 5)
  CONSEC_LOSS_PAUSE_MIN — pause duration in minutes (default: 30)
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

# Guard against double-registration of the renewal thread
_renewal_thread: Optional["threading.Thread"] = None

# ── Circuit breaker thresholds (read from env if set, else use defaults) ──────
DAILY_LOSS_HALT_PCT   = float(os.getenv("DAILY_LOSS_HALT_PCT", "0.03"))   # 3% daily loss → halt
CONSEC_LOSS_HALT      = int(os.getenv("CONSEC_LOSS_HALT", "5"))            # 5 consecutive losses → pause
CONSEC_LOSS_PAUSE_MIN = int(os.getenv("CONSEC_LOSS_PAUSE_MIN", "30"))      # pause duration in minutes

_circuit_breaker_state = {
    "halted": False,
    "halt_reason": "",
    "halt_until": None,
    "consecutive_losses": 0,
    "daily_pnl_pct": 0.0,
}


def check_circuit_breaker(daily_pnl_pct: float, consecutive_losses: int) -> tuple:
    """
    Check if trading should be halted. Returns (should_halt: bool, reason: str).
    Call before each new trade entry.

    - Halts all day if daily_pnl_pct <= -DAILY_LOSS_HALT_PCT (default -3%)
    - Pauses CONSEC_LOSS_PAUSE_MIN minutes if consecutive_losses >= CONSEC_LOSS_HALT (default 5)
    - Expired timed halts are auto-cleared before each check
    """
    state = _circuit_breaker_state
    # Keep state dict in sync with caller-provided values so monitoring can read them
    state["daily_pnl_pct"]     = daily_pnl_pct
    state["consecutive_losses"] = consecutive_losses

    # Auto-clear timed halt if expiry has passed
    if state["halt_until"] and datetime.now(IST) >= state["halt_until"]:
        state["halted"] = False
        state["halt_until"] = None
        state["halt_reason"] = ""

    if state["halted"]:
        return (True, state["halt_reason"])

    # Daily loss check — permanent halt for the rest of the session
    if daily_pnl_pct <= -DAILY_LOSS_HALT_PCT:
        state["halted"] = True
        state["halt_reason"] = f"DAILY_LOSS_LIMIT ({daily_pnl_pct:.2%})"
        return (True, state["halt_reason"])

    # Consecutive loss check — timed pause
    if consecutive_losses >= CONSEC_LOSS_HALT:
        state["halted"] = True
        state["halt_until"] = datetime.now(IST) + timedelta(minutes=CONSEC_LOSS_PAUSE_MIN)
        state["halt_reason"] = f"CONSEC_LOSSES_{consecutive_losses}"
        return (True, state["halt_reason"])

    return (False, "")


def reset_circuit_breaker_daily():
    """
    Reset daily circuit breaker state. Call at market open (09:15 IST).
    Note: consecutive_losses intentionally NOT reset daily (carries over session to session).
    """
    _circuit_breaker_state["daily_pnl_pct"] = 0.0
    _circuit_breaker_state["halted"] = False
    _circuit_breaker_state["halt_until"] = None
    _circuit_breaker_state["halt_reason"] = ""

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


# ── TOTP auto-renewal ─────────────────────────────────────────────────────────

def auto_renew_token() -> Tuple[bool, str]:
    """
    Automatically generate a new Upstox access token using TOTP + OAuth2 flow.

    Upstox does not expose a direct password+TOTP endpoint — the standard
    flow requires an OAuth2 authorization_code exchange. This function uses
    the Upstox v2 login API (same as browser-based login) via requests:
      1. POST /v2/login/authorization/dialog  — initiate login session
      2. POST /v2/login/authorization/totp    — submit TOTP code
      3. GET  /v2/login/authorization/dialog  — get redirect with auth code
      4. POST /v2/login/authorization/token   — exchange code for access token

    Note: Upstox's undocumented mobile/web login API endpoints are used here,
    matching the approach used by the community (dhruvan, upstox-python-sdk
    unofficial wrappers). If Upstox changes these endpoints, fall back to
    Telegram /newtoken command.

    Requires env vars:
        UPSTOX_EMAIL         — Upstox login email
        UPSTOX_PASSWORD      — Upstox login password
        UPSTOX_TOTP_SECRET   — TOTP secret (Base32) from Upstox 2FA setup
        UPSTOX_API_KEY       — developer app client_id
        UPSTOX_API_SECRET    — developer app client_secret
        UPSTOX_REDIRECT_URI  — registered redirect URI (e.g. https://127.0.0.1/callback)

    Returns (success, message).
    """
    try:
        import pyotp
        import requests as _req
    except ImportError as e:
        return False, f"Missing dependency: {e}. Run: pip install pyotp requests"

    email        = os.getenv("UPSTOX_EMAIL", "").strip()
    password     = os.getenv("UPSTOX_PASSWORD", "").strip()
    totp_secret  = os.getenv("UPSTOX_TOTP_SECRET", "").strip()
    api_key      = os.getenv("UPSTOX_API_KEY", "").strip()
    api_secret   = os.getenv("UPSTOX_API_SECRET", "").strip()
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1/callback").strip()

    if not all([email, password, totp_secret, api_key, api_secret]):
        missing = [k for k, v in {
            "UPSTOX_EMAIL": email,
            "UPSTOX_PASSWORD": password,
            "UPSTOX_TOTP_SECRET": totp_secret,
            "UPSTOX_API_KEY": api_key,
            "UPSTOX_API_SECRET": api_secret,
        }.items() if not v]
        return False, (
            f"TOTP auto-renewal disabled — missing env vars: {', '.join(missing)}. "
            "Use Telegram /newtoken as fallback."
        )

    session = _req.Session()
    # Browser-like headers required by Upstox login API
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
    })

    try:
        # Step 1: Initiate login — get session cookie
        init_resp = session.post(
            "https://api.upstox.com/v2/login/authorization/dialog",
            json={
                "client_id": api_key,
                "redirect_uri": redirect_uri,
                "response_type": "code",
            },
            timeout=15,
            allow_redirects=True,
        )
        logger.debug(f"Upstox login init: HTTP {init_resp.status_code}")
    except Exception as e:
        logger.debug(f"Upstox login init step error (non-fatal): {e}")

    try:
        # Step 2: Submit email + password
        login_resp = session.post(
            "https://api.upstox.com/v2/login/authorization/users",
            json={"client_id": api_key, "email": email, "password": password},
            timeout=15,
        )
        if login_resp.status_code not in (200, 201, 302):
            # Credential failure is a hard stop — Step 3 TOTP would be meaningless
            return False, (
                f"Upstox credential login failed (HTTP {login_resp.status_code}): "
                f"{login_resp.text[:200]}. Check UPSTOX_EMAIL / UPSTOX_PASSWORD."
            )
    except Exception as e:
        return False, f"Upstox credential login error: {e}"

    try:
        # Step 3: Submit TOTP code
        # Generate fresh code right before submitting (valid for 30s window)
        totp_normalized = totp_secret.replace(" ", "").upper()
        totp_code = pyotp.TOTP(totp_normalized).now()
        totp_resp = session.post(
            "https://api.upstox.com/v2/login/authorization/totp",
            json={"client_id": api_key, "totp": totp_code},
            timeout=15,
        )
        logger.debug(f"Upstox TOTP step: HTTP {totp_resp.status_code}")
        if totp_resp.status_code not in (200, 201, 302):
            logger.warning(
                f"Upstox TOTP step returned HTTP {totp_resp.status_code}: "
                f"{totp_resp.text[:200]}"
            )
    except Exception as e:
        logger.warning(f"Upstox TOTP submit error: {e}")

    try:
        # Step 4: Get authorization code via redirect
        import urllib.parse
        auth_url = (
            f"https://api.upstox.com/v2/login/authorization/dialog"
            f"?response_type=code"
            f"&client_id={urllib.parse.quote(api_key)}"
            f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        )
        code_resp = session.get(auth_url, timeout=15, allow_redirects=False)
        # requests.Response.headers is CaseInsensitiveDict — single lookup suffices
        location = code_resp.headers.get("Location", "")

        if "code=" not in location:
            return False, (
                f"TOTP flow completed but no auth code received "
                f"(HTTP {code_resp.status_code}). "
                "Upstox may have changed their login API. "
                "Use Telegram /newtoken as fallback."
            )

        parsed = urllib.parse.urlparse(location)
        auth_code = urllib.parse.parse_qs(parsed.query).get("code", [""])[0]
        if not auth_code:
            return False, "Could not parse authorization code from redirect URL."

        logger.debug(f"Upstox auth code obtained: {auth_code[:10]}...")
    except Exception as e:
        logger.error(f"Upstox auth code retrieval error: {e}")
        return False, f"TOTP auth code step failed: {e}"

    try:
        # Step 5: Exchange authorization code for access token (standard OAuth2).
        # Uses session.post() so any session cookies from Steps 1-3 are included.
        token_resp = session.post(
            "https://api.upstox.com/v2/login/authorization/token",
            data={
                "code":          auth_code,
                "client_id":     api_key,
                "client_secret": api_secret,
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept":       "application/json",
                "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36",
                "Origin":       "https://api.upstox.com",
                "Referer":      "https://api.upstox.com/",
            },
            timeout=20,
        )

        if token_resp.status_code != 200:
            return False, (
                f"Token exchange failed (HTTP {token_resp.status_code}): "
                f"{token_resp.text[:300]}"
            )

        body  = token_resp.json()
        token = body.get("access_token") or body.get("data", {}).get("access_token", "")
        if not token:
            return False, f"Token exchange response has no access_token: {body}"

        ok, msg = update_token_in_env(token)
        if ok:
            logger.info(f"TOTP auto-renewal successful ({token[:8]}***)")
            return True, f"Token auto-renewed via TOTP ({token[:8]}***)"
        return False, f"Token obtained but save failed: {msg}"

    except Exception as e:
        logger.error(f"TOTP token exchange error: {e}")
        return False, f"TOTP token exchange failed: {e}"


def _auto_renew_with_retry(max_attempts: int = 4) -> bool:
    """
    Attempt token renewal with exponential backoff. Sends Telegram alert on failure.
    Returns True if renewal succeeded.

    Backoff schedule: 2s after attempt 1, 4s after attempt 2, 8s after attempt 3.
    """
    for attempt in range(max_attempts):
        try:
            ok, msg = auto_renew_token()
            if ok:
                logger.info(f"Token renewal succeeded on attempt {attempt + 1}: {msg}")
                return True
            logger.warning(f"Token renewal attempt {attempt + 1}/{max_attempts} failed: {msg}")
        except Exception as e:
            logger.warning(f"Token renewal attempt {attempt + 1}/{max_attempts} error: {e}")
        if attempt < max_attempts - 1:
            wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
            logger.info(f"Retrying token renewal in {wait}s...")
            _time.sleep(wait)

    # All attempts failed — notify via Telegram
    logger.error("All token renewal attempts failed — sending Telegram alert")
    try:
        _notify_telegram_for_token()
    except Exception:
        pass
    return False


def _notify_telegram_for_token() -> None:
    """Send Telegram alert asking user to renew token manually."""
    try:
        import requests as _req
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
        if bot_token and chat_id:
            msg = (
                "🇮🇳 ⚠️ *Upstox TOTP auto-renewal failed*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Automatic token renewal could not complete.\n\n"
                "*Manual renewal (60 seconds):*\n"
                "1️⃣ Get today's token from Upstox app\n"
                "2️⃣ Send: `/newtoken YOUR_TOKEN`\n\n"
                "_Bot will auto-update and restart._"
            )
            _req.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10,
            )
    except Exception:
        pass


def schedule_auto_renewal() -> "threading.Thread":
    """
    Start a daemon thread that auto-renews the Upstox token daily at 08:30 IST.
    Call this once at bot startup. Thread runs forever in background.

    Uses pure IST datetime check (NOT the `schedule` library, which fires at UTC
    server time = 14:00 IST on a UK server). Fires once per calendar day in the
    08:30–08:35 IST window, tracked by date to prevent double-firing.

    No-op (returns None) if UPSTOX_AUTO_RENEW_ENABLED=false or UPSTOX_TOTP_SECRET
    is not set. Returns the existing thread if already running (idempotent).
    """
    import threading
    global _renewal_thread

    # Idempotency guard — never register twice
    if _renewal_thread is not None and _renewal_thread.is_alive():
        logger.debug("Upstox TOTP renewal thread already running — skipping registration")
        return _renewal_thread

    # Feature flag check
    if os.getenv("UPSTOX_AUTO_RENEW_ENABLED", "true").lower() != "true":
        logger.info("UPSTOX_AUTO_RENEW_ENABLED=false — TOTP auto-renewal disabled")
        return None

    if not os.getenv("UPSTOX_TOTP_SECRET", "").strip():
        logger.info(
            "UPSTOX_TOTP_SECRET not set — TOTP auto-renewal disabled. "
            "Use Telegram /newtoken for daily renewal."
        )
        return None

    def _renewal_job():
        """Pure IST time check every 30s — fires once per day at 08:30 IST."""
        _last_renewal_date = None
        while True:
            try:
                now_ist = datetime.now(IST)
                if (now_ist.hour == 8 and 30 <= now_ist.minute < 35
                        and _last_renewal_date != now_ist.date()):
                    logger.info(
                        f"Firing TOTP token renewal at {now_ist.strftime('%H:%M')} IST "
                        f"on {now_ist.date()}"
                    )
                    _auto_renew_with_retry()
                    _last_renewal_date = now_ist.date()
                    # Skip past the 5-minute firing window to avoid re-triggering
                    _time.sleep(360)
                    continue
            except Exception as e:
                logger.error(f"TOTP renewal scheduler loop error: {e}")
            _time.sleep(30)

    _renewal_thread = threading.Thread(
        target=_renewal_job, daemon=True, name="upstox-token-renewal"
    )
    _renewal_thread.start()
    logger.info("Upstox TOTP auto-renewal scheduler started (daemon thread, fires 08:30 IST)")
    return _renewal_thread


def ensure_token_fresh(max_age_hours: float = 20.0) -> bool:
    """
    Check if current token is fresh. Renew if older than max_age_hours.
    Call at start of each trading session or hourly during trading.
    Returns True if token is valid (existing or just renewed).

    Uses last_renewed timestamp from upstox_token_meta.json for the age check.
    Falls back to is_token_stale() if metadata is missing or malformed.

    Note: is_token_stale() returns False when no metadata exists (unknown-age
    token). In that case this function also returns True (assumes fresh) to
    preserve backward-compatibility for bots that don't use token metadata.
    If you need stricter checking, call auto_renew_token() unconditionally
    at startup when UPSTOX_TOTP_SECRET is configured.
    """
    try:
        meta = _load_meta()
        # Support both "last_renewed" (new field) and "token_set_at" (legacy field)
        last_renewed_str = meta.get("last_renewed") or meta.get("token_set_at", "")
        if last_renewed_str:
            last_renewed = datetime.fromisoformat(last_renewed_str)
            if last_renewed.tzinfo is None:
                last_renewed = last_renewed.replace(tzinfo=IST)
            age_hours = (datetime.now(IST) - last_renewed).total_seconds() / 3600
            if age_hours < max_age_hours:
                logger.debug(f"Token is fresh (age={age_hours:.1f}h < {max_age_hours}h)")
                return True  # Token is fresh enough
            logger.warning(
                f"Upstox token is stale (age={age_hours:.1f}h >= {max_age_hours}h) "
                "— attempting renewal..."
            )
            return _auto_renew_with_retry()
    except Exception as e:
        logger.debug(f"ensure_token_fresh metadata check error: {e}")

    # Metadata missing or malformed — fall back to date-boundary staleness check
    no_metadata = not _load_meta().get("token_set_at")
    if no_metadata:
        if os.getenv("UPSTOX_TOTP_SECRET", "").strip():
            logger.info("No token metadata found — attempting proactive TOTP renewal.")
            return _auto_renew_with_retry()
        return True  # No metadata + no TOTP → assume token is fresh (legacy behavior)

    if not is_token_stale():
        return True

    logger.warning("Upstox token is stale — attempting TOTP auto-renewal...")
    return _auto_renew_with_retry()


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

