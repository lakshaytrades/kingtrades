"""
auth_groww.py — NSE Momentum Groww AI Bot
Fully Automatic TOTP Token Refresh — Zero Manual Intervention

How it works (set these two env vars ONCE, never touch them again):
  GROWW_AUTH_TOKEN  = The permanent JWT API key from developer.groww.in
                      (Manage → API Keys → Generate API Key / Copy Key)
                      This does NOT expire daily — it's your permanent credential.
  GROWW_TOTP_SECRET = Your TOTP secret from the QR code at developer.groww.in
                      (the 32-character base32 string, same as Google Authenticator)

What the bot does automatically every morning at 6:05 AM IST:
  access_token = GrowwAPI.get_access_token(api_key=GROWW_AUTH_TOKEN, totp=<live code>)
  groww = GrowwAPI(access_token)

The access_token is short-lived (hours). GROWW_AUTH_TOKEN never changes.
Bot retries 3 times with backoff if the SDK call has a transient hiccup.

⚠️  NEVER pass the access_token back in as api_key — that's the bug we fixed.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import pyotp

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_TOKEN_CACHE_FILE = Path("data/.token_cache.json")

# ────────────────────────────────────────────────────────────────────────────
# Token cache — stores api_key and access_token SEPARATELY
# ────────────────────────────────────────────────────────────────────────────

def _save_token_cache(api_key: str, access_token: str, timestamp: datetime) -> None:
    """
    Save both the permanent api_key and today's access_token.
    api_key is stored so the bot can survive a Render restart without re-reading env.
    access_token is stored to avoid unnecessary re-auth on restarts within the same day.
    """
    try:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE_FILE.write_text(json.dumps({
            "api_key":      api_key,
            "access_token": access_token,
            "timestamp":    timestamp.isoformat(),
        }))
    except Exception as e:
        logger.debug(f"Token cache write failed: {e}")


def _load_token_cache() -> Tuple[Optional[str], Optional[str], Optional[datetime]]:
    """
    Returns (api_key, access_token, timestamp).
    access_token is only returned if it's <12h old (Groww access tokens last ~24h but we
    refresh at 6h to be safe). api_key is always returned if present.
    """
    try:
        if not _TOKEN_CACHE_FILE.exists():
            return None, None, None
        data = json.loads(_TOKEN_CACHE_FILE.read_text())

        api_key      = data.get("api_key", "")
        access_token = data.get("access_token", "")
        ts_str       = data.get("timestamp", "")

        if not ts_str:
            return api_key or None, None, None

        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)

        age_h = (datetime.now(IST) - ts).total_seconds() / 3600

        if age_h < 12 and access_token and len(access_token) > 20:
            logger.info(
                f"[{format_ist_timestamp()}] Cache hit: access_token age {age_h:.1f}h "
                f"(valid for {12 - age_h:.1f}h more)"
            )
            return api_key or None, access_token, ts

        logger.info(
            f"[{format_ist_timestamp()}] Cache: access_token {age_h:.1f}h old — "
            "will refresh (api_key retained)"
        )
        return api_key or None, None, ts

    except Exception as e:
        logger.debug(f"Token cache read failed: {e}")
    return None, None, None


# ────────────────────────────────────────────────────────────────────────────
# Core refresh — uses permanent api_key, generates fresh access_token via TOTP
# ────────────────────────────────────────────────────────────────────────────

def _refresh_access_token(api_key: str, totp_secret: str, attempt: int = 1) -> Optional[str]:
    """
    Call GrowwAPI.get_access_token(api_key=<permanent JWT>, totp=<live code>).

    This is the ONLY token refresh path. No email, no password, no client_id/secret.
    Retried up to 3 times with 30s spacing on transient failures.

    api_key    = GROWW_AUTH_TOKEN (permanent, from developer.groww.in portal)
    totp_secret = GROWW_TOTP_SECRET (the base32 TOTP secret, set once)

    Returns the fresh access_token string, or None if all attempts fail.
    """
    if not api_key or not totp_secret:
        logger.error(
            f"[{format_ist_timestamp()}] Cannot refresh: "
            f"api_key={'set' if api_key else 'MISSING'}, "
            f"totp_secret={'set' if totp_secret else 'MISSING'}\n"
            "  → Set GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET in Render Environment."
        )
        return None

    # Wait for a TOTP window with ≥ 5s of life left (avoids using a dying code)
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 5:
        wait = remaining + 1
        logger.info(f"[{format_ist_timestamp()}] Waiting {wait}s for fresh TOTP window...")
        time.sleep(wait)

    totp_code = pyotp.TOTP(totp_secret).now()
    window_left = 30 - (int(time.time()) % 30)
    logger.info(
        f"[{format_ist_timestamp()}] TOTP refresh attempt {attempt}/3 | "
        f"code={totp_code} | window={window_left}s remaining"
    )

    try:
        from growwapi import GrowwAPI

        # Official Groww Cloud API method (from developer.groww.in docs):
        #   api_key = YOUR_PERMANENT_JWT  (GROWW_AUTH_TOKEN — never expires)
        #   totp    = pyotp.TOTP(TOTP_SECRET).now()
        #   access_token = GrowwAPI.get_access_token(api_key=api_key, totp=totp)
        #   groww   = GrowwAPI(access_token)
        access_token = GrowwAPI.get_access_token(
            api_key=api_key,
            totp=totp_code,
        )

        # SDK may return a plain string or a dict
        if isinstance(access_token, str) and len(access_token) > 20:
            logger.info(f"[{format_ist_timestamp()}] ✅ Access token refreshed via GrowwAPI.get_access_token()")
            return access_token

        if isinstance(access_token, dict):
            for key in ("token", "authToken", "auth_token", "access_token",
                        "jwtToken", "jwt_token", "userToken"):
                val = access_token.get(key) or (access_token.get("data") or {}).get(key)
                if val and isinstance(val, str) and len(val) > 20:
                    logger.info(f"[{format_ist_timestamp()}] ✅ Access token from dict[{key}]")
                    return val

        logger.warning(
            f"[{format_ist_timestamp()}] get_access_token() returned unexpected value: "
            f"type={type(access_token)} val={str(access_token)[:120]}"
        )

    except AttributeError:
        # Some SDK versions don't have the class method — try instance method
        logger.info(f"[{format_ist_timestamp()}] Class method missing — trying instance.get_access_token()...")
        try:
            from growwapi import GrowwAPI
            tmp = GrowwAPI(api_key)
            for method_name in ("get_access_token", "refresh_token", "generate_session"):
                fn = getattr(tmp, method_name, None)
                if not fn:
                    continue
                try:
                    result = fn(totp=totp_code)
                    if result and isinstance(result, str) and len(result) > 20:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Access token via instance.{method_name}()")
                        return result
                except TypeError:
                    pass  # wrong signature
        except Exception as e2:
            logger.debug(f"Instance method fallback: {e2}")

    except Exception as e:
        logger.warning(f"[{format_ist_timestamp()}] GrowwAPI.get_access_token() error: {e}")

    return None


# ────────────────────────────────────────────────────────────────────────────
# Auth Manager
# ────────────────────────────────────────────────────────────────────────────

class GrowwAuthManager:
    """
    Fully automatic Groww authentication manager.

    Two permanent env vars (set ONCE in Render, never touch again):
      GROWW_AUTH_TOKEN  = permanent API key JWT from developer.groww.in
      GROWW_TOTP_SECRET = base32 TOTP secret from the same page

    The manager:
      - Loads api_key from env (permanent — never overwritten)
      - Generates a fresh access_token via TOTP every morning at 6:05 AM IST
      - Auto-retries 3x with 30s backoff on transient failures
      - Caches access_token on disk (survives bot restarts within the same day)
      - Notifies via Telegram if TOTP secret or api_key is missing
    """

    def __init__(self):
        # ── Permanent credentials (set ONCE, env vars never change) ───────
        self._api_key    = os.getenv("GROWW_AUTH_TOKEN", "")   # permanent JWT
        self.totp_secret = os.getenv("GROWW_TOTP_SECRET", "")  # TOTP base32 secret

        # Kept for possible future use / web login fallback
        self.email    = os.getenv("GROWW_EMAIL", "")
        self.password = os.getenv("GROWW_PASSWORD", "")

        # ── Runtime state ─────────────────────────────────────────────────
        self._token:           Optional[str]      = None   # daily access token
        self._token_timestamp: Optional[datetime] = None

        # ── Load cache first ──────────────────────────────────────────────
        cached_api_key, cached_access, cached_ts = _load_token_cache()

        # If cache has a valid api_key and env has none, use cache's api_key
        if not self._api_key and cached_api_key:
            self._api_key = cached_api_key
            logger.info(f"[{format_ist_timestamp()}] api_key restored from cache")

        # If cache has a fresh access_token, use it (skip refresh until it's old)
        if cached_access:
            self._token           = cached_access
            self._token_timestamp = cached_ts
            logger.info(f"[{format_ist_timestamp()}] Using cached access_token")

        # ── Validate config ───────────────────────────────────────────────
        if not self._api_key:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_AUTH_TOKEN not set!\n"
                "  1. Go to developer.groww.in → Manage → API Keys\n"
                "  2. Copy your permanent JWT\n"
                "  3. Render → kingtrades-bot → Environment → GROWW_AUTH_TOKEN → paste → Save"
            )
        if not self.totp_secret:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_TOTP_SECRET not set!\n"
                "  1. Go to developer.groww.in → Manage → API Keys\n"
                "  2. Find 'TOTP Secret' (base32 string) → copy it\n"
                "  3. Render → kingtrades-bot → Environment → GROWW_TOTP_SECRET → paste → Save"
            )

        if self._api_key and self.totp_secret:
            logger.info(f"[{format_ist_timestamp()}] ✅ Auth manager ready (api_key + TOTP set)")

    # ──────────────────────────────────────────────────────────────────────
    # Token refresh with 3-attempt retry
    # ──────────────────────────────────────────────────────────────────────

    def _do_refresh_with_retry(self) -> Optional[str]:
        """
        Try to get a fresh access_token up to 3 times.
        Waits 30s between attempts (handles transient Groww API issues).
        """
        for attempt in range(1, 4):
            token = _refresh_access_token(self._api_key, self.totp_secret, attempt)
            if token:
                self._token           = token
                self._token_timestamp = get_current_ist_time()
                _save_token_cache(self._api_key, token, self._token_timestamp)
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ Access token saved "
                    f"(attempt {attempt}/3) — valid ~12h"
                )
                return token

            if attempt < 3:
                logger.warning(
                    f"[{format_ist_timestamp()}] Attempt {attempt}/3 failed — "
                    "retrying in 30s..."
                )
                time.sleep(30)

        # All 3 attempts failed
        logger.error(
            f"[{format_ist_timestamp()}] ❌ All 3 refresh attempts failed.\n"
            "  Bot will continue with existing token until it expires.\n"
            "  Check: GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET are correct in Render env vars."
        )
        self._send_refresh_failed_alert()
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def get_valid_token(self) -> Optional[str]:
        """
        Return the current access_token.
        Auto-refreshes if it's >12h old (proactive refresh before expiry).
        Called before every GrowwAPI() instantiation.
        """
        if self._token and self._token_timestamp:
            age_h = (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600
            if age_h < 12:
                return self._token
            logger.info(
                f"[{format_ist_timestamp()}] Access token {age_h:.1f}h old — refreshing..."
            )

        return self._do_refresh_with_retry() or self._token   # fallback to old token if refresh fails

    def refresh_token_if_needed(self) -> bool:
        """
        Called automatically at 6:05 AM IST by the scheduler.
        Always forces a fresh access_token — this is the daily refresh.
        """
        logger.info(f"[{format_ist_timestamp()}] Daily TOTP refresh (6:05 AM IST)...")
        token = self._do_refresh_with_retry()
        if token:
            logger.info(f"[{format_ist_timestamp()}] ✅ Daily refresh complete — ready for trading")
            return True
        logger.error(f"[{format_ist_timestamp()}] ❌ Daily refresh FAILED — check env vars")
        return False

    def force_refresh(self) -> bool:
        """Force an immediate refresh (called by /refresh Telegram command or watchdog)."""
        logger.info(f"[{format_ist_timestamp()}] Forced token refresh requested...")
        token = self._do_refresh_with_retry()
        return bool(token)

    def login_and_get_token(self) -> Optional[str]:
        """Compatibility shim — calls get_valid_token()."""
        return self.get_valid_token()

    @property
    def token(self) -> Optional[str]:
        return self._token

    @property
    def token_age_hours(self) -> float:
        if not self._token_timestamp:
            return float("inf")
        return (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600

    # ──────────────────────────────────────────────────────────────────────
    # Telegram alerts
    # ──────────────────────────────────────────────────────────────────────

    def _send_refresh_failed_alert(self) -> None:
        """Telegram alert when all 3 refresh attempts fail."""
        try:
            import config, requests as req
            if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
                return
            req.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id":    config.TELEGRAM_CHAT_ID,
                    "parse_mode": "HTML",
                    "text": (
                        "🔴 <b>Groww Token Refresh Failed</b>\n\n"
                        "All 3 TOTP refresh attempts failed.\n\n"
                        "<b>Check these in Render env vars:</b>\n"
                        "• <code>GROWW_AUTH_TOKEN</code> — permanent JWT from "
                        "developer.groww.in → API Keys\n"
                        "• <code>GROWW_TOTP_SECRET</code> — base32 TOTP secret "
                        "from same page\n\n"
                        "Bot is running on old token — may fail soon.\n"
                        "<i>Once you fix env vars, bot auto-retries next morning.</i>"
                    ),
                },
                timeout=10,
            )
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────────
# Singleton
# ────────────────────────────────────────────────────────────────────────────

_auth_manager: Optional[GrowwAuthManager] = None


def get_auth_manager() -> GrowwAuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GrowwAuthManager()
    return _auth_manager


def get_groww_token() -> Optional[str]:
    """Get current valid access_token (auto-refreshes if needed)."""
    return get_auth_manager().get_valid_token()


def initialize_auth() -> bool:
    """
    Called at bot startup and by watchdog before each daily launch.
    Refreshes the access_token if it's stale (>12h) or it's the morning window.
    """
    manager = get_auth_manager()
    now_ist = get_current_ist_time()

    token_age_h  = manager.token_age_hours
    morning_window = (5 <= now_ist.hour < 10)   # 5 AM–10 AM IST: always refresh
    token_stale    = token_age_h > 12

    if morning_window or token_stale:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: refreshing token "
            f"(age={token_age_h:.1f}h, morning={morning_window})..."
        )
        manager.refresh_token_if_needed()
    else:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: token fresh ({token_age_h:.1f}h old) — no refresh needed"
        )

    return bool(manager._token or manager._api_key)


# ────────────────────────────────────────────────────────────────────────────
# Standalone test — run: python auth_groww.py
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Testing Groww TOTP auth...")
    manager = GrowwAuthManager()
    print(f"api_key set: {bool(manager._api_key)}")
    print(f"totp_secret set: {bool(manager.totp_secret)}")
    token = manager.get_valid_token()
    if token:
        print(f"✅ Access token obtained: {token[:30]}...{token[-10:]}")
    else:
        print("❌ Failed to get token — check GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET")
