"""
auth_groww.py — NSE Momentum Groww AI Bot
Fully Automatic TOTP Login — Zero Manual Intervention After Setup

─── ONE-TIME SETUP (permanent — never change again) ─────────────────────────
  Add TWO lines to /opt/kingtrades/.env:

    GROWW_VENDOR_KEY  = <your permanent vendor integration key>
    GROWW_TOTP_SECRET = <your TOTP base32 secret from groww.in>

  Don't know your GROWW_VENDOR_KEY? Run this on your VPS:
    cd /opt/kingtrades && python3 auth_groww.py --extract-key

  That's it. Bot logs in automatically every day. No daily hustle.

─── HOW IT WORKS ────────────────────────────────────────────────────────────
  Groww resets ALL access tokens at 6:00 AM IST daily.

  What the bot does automatically:
    1. At 5:50 AM IST — TOTP generates fresh token before 6 AM reset
    2. On startup after 6 AM — refreshes immediately via TOTP
    3. Caches token to disk — survives service restarts within same day

  TOTP flow:
    code  = pyotp.TOTP(GROWW_TOTP_SECRET).now()
    token = GrowwAPI.get_access_token(api_key=GROWW_VENDOR_KEY, totp=code)

─── MIGRATION FROM OLD SETUP ────────────────────────────────────────────────
  Had GROWW_AUTH_TOKEN before? Run:
    cd /opt/kingtrades && python3 auth_groww.py --extract-key
  This auto-extracts your vendor key and prints the line to add to .env.
  After that, GROWW_AUTH_TOKEN is no longer needed.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import pyotp
import requests

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_TOKEN_CACHE_FILE = Path("data/.token_cache.json")


# ─────────────────────────────────────────────────────────────────────────────
# JWT helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_vendor_key_from_jwt(jwt_token: str) -> Optional[str]:
    """Extract permanent vendorIntegrationKey from Groww JWT payload sub field."""
    try:
        import base64, json as _j
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = _j.loads(base64.urlsafe_b64decode(padded))
        sub = payload.get("sub", "")
        if isinstance(sub, str) and sub.startswith("{"):
            key = _j.loads(sub).get("vendorIntegrationKey", "")
            if key and len(key) > 8:
                return key
    except Exception:
        pass
    return None


def _extract_token_from_response(body: dict) -> Optional[str]:
    """Pull access token string out of a Groww API response dict."""
    for k in ("token", "authToken", "auth_token", "access_token",
              "jwtToken", "jwt_token", "userToken", "user_token"):
        val = body.get(k) or (body.get("data") or {}).get(k)
        if val and isinstance(val, str) and len(val) > 20:
            return val
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Token cache
# ─────────────────────────────────────────────────────────────────────────────

def _save_token_cache(vendor_key: str, access_token: str, timestamp: datetime) -> None:
    try:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE_FILE.write_text(json.dumps({
            "vendor_key":   vendor_key,
            "access_token": access_token,
            "timestamp":    timestamp.isoformat(),
        }))
    except Exception as e:
        logger.debug(f"Token cache write failed: {e}")


def _load_token_cache() -> Tuple[str, Optional[str], Optional[datetime]]:
    try:
        if not _TOKEN_CACHE_FILE.exists():
            return "", None, None
        data = json.loads(_TOKEN_CACHE_FILE.read_text())
        vendor_key   = data.get("vendor_key", "") or ""
        access_token = data.get("access_token", "") or None
        ts_str       = data.get("timestamp", "")
        if not ts_str:
            return vendor_key, None, None
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        return vendor_key, access_token, ts
    except Exception as e:
        logger.debug(f"Token cache read failed: {e}")
    return "", None, None


# ─────────────────────────────────────────────────────────────────────────────
# 6 AM IST daily reset check
# ─────────────────────────────────────────────────────────────────────────────

def _is_expired_by_6am_reset(token_ts: Optional[datetime]) -> bool:
    """True if Groww's 6 AM IST daily reset has invalidated this token."""
    if token_ts is None:
        return True
    now = get_current_ist_time()
    if token_ts.tzinfo is None:
        token_ts = token_ts.replace(tzinfo=IST)
    reset_today = now.replace(hour=6, minute=0, second=0, microsecond=0)
    return token_ts < reset_today and now >= reset_today


# ─────────────────────────────────────────────────────────────────────────────
# Core TOTP refresh
# ─────────────────────────────────────────────────────────────────────────────

def _totp_refresh(vendor_key: str, totp_secret: str, attempt: int = 1) -> Optional[str]:
    """
    Get a fresh Groww access token using TOTP.
    vendor_key  = permanent GROWW_VENDOR_KEY (never changes)
    totp_secret = permanent GROWW_TOTP_SECRET (never changes)
    """
    if not vendor_key or not totp_secret:
        logger.error(
            f"[{format_ist_timestamp()}] Cannot refresh — missing credentials:\n"
            f"  GROWW_VENDOR_KEY:  {'set' if vendor_key else '❌ MISSING'}\n"
            f"  GROWW_TOTP_SECRET: {'set' if totp_secret else '❌ MISSING'}\n"
            "  See auth_groww.py docstring for setup instructions."
        )
        return None

    # Wait if TOTP window has < 5s left (avoids code expiring mid-call)
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 5:
        logger.info(f"[{format_ist_timestamp()}] Waiting {remaining + 1}s for fresh TOTP window...")
        time.sleep(remaining + 1)

    totp_code   = pyotp.TOTP(totp_secret).now()
    window_left = 30 - (int(time.time()) % 30)
    logger.info(
        f"[{format_ist_timestamp()}] TOTP refresh attempt {attempt}/3 | "
        f"code={totp_code} | window={window_left}s left"
    )

    # ── SDK path ─────────────────────────────────────────────────────────
    try:
        from growwapi import GrowwAPI
        try:
            result = GrowwAPI.get_access_token(api_key=vendor_key, totp=totp_code)
            if isinstance(result, str) and len(result) > 20:
                logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK (static method)")
                return result
            if isinstance(result, dict):
                tok = _extract_token_from_response(result)
                if tok:
                    logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK (static, dict response)")
                    return tok
            logger.debug(f"SDK static: unexpected response {str(result)[:120]}")
        except Exception as e:
            logger.debug(f"SDK static get_access_token: {e}")

        # Some SDK versions use instance methods
        try:
            obj = GrowwAPI(vendor_key)
            for method_name in ("get_access_token", "refresh_token", "generate_session"):
                fn = getattr(obj, method_name, None)
                if not fn:
                    continue
                try:
                    r = fn(totp=totp_code)
                    if isinstance(r, str) and len(r) > 20:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK instance.{method_name}()")
                        return r
                except (TypeError, Exception):
                    pass
        except Exception:
            pass

    except ImportError:
        logger.debug("growwapi SDK not installed — trying direct HTTP")
    except Exception as e:
        logger.warning(f"[{format_ist_timestamp()}] SDK error: {e}")

    # ── Direct HTTP path ─────────────────────────────────────────────────
    headers = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "User-Agent":    "growwapi-python/1.0",
        "X-Api-Version": "1",
    }
    endpoints = [
        ("POST", "https://api.groww.in/v1/user/generate_token",
         {"api_key": vendor_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/user/session/generate_token",
         {"api_key": vendor_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/auth/access-token",
         {"api_key": vendor_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/auth/token",
         {"apiKey": vendor_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/user/token/refresh",
         {"api_key": vendor_key, "totp": totp_code}),
    ]
    for method, url, body in endpoints:
        label = url.rsplit("/", 1)[-1]
        try:
            resp = requests.request(method, url, json=body, headers=headers, timeout=20)
            if resp.status_code in (200, 201):
                tok = _extract_token_from_response(resp.json())
                if tok:
                    logger.info(f"[{format_ist_timestamp()}] ✅ Token via HTTP [{label}]")
                    return tok
                logger.debug(f"  [{label}] HTTP 200 but no token in: {list(resp.json().keys())}")
            else:
                logger.debug(f"  [{label}] HTTP {resp.status_code}")
        except Exception as e:
            logger.debug(f"  [{label}] {e}")

    logger.warning(f"[{format_ist_timestamp()}] Attempt {attempt}/3 — all TOTP methods exhausted")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Auth Manager
# ─────────────────────────────────────────────────────────────────────────────

class GrowwAuthManager:
    """
    Fully automatic Groww auth — two permanent env vars, zero daily effort.

    Required in /opt/kingtrades/.env (set once, never change):
      GROWW_VENDOR_KEY  = permanent vendor integration key
      GROWW_TOTP_SECRET = TOTP base32 secret

    Run `python3 auth_groww.py --extract-key` to get your GROWW_VENDOR_KEY
    from an existing GROWW_AUTH_TOKEN JWT.
    """

    def __init__(self):
        self.totp_secret = os.getenv("GROWW_TOTP_SECRET", "")

        # Prefer GROWW_VENDOR_KEY; fall back to extracting from old JWT
        self._vendor_key = os.getenv("GROWW_VENDOR_KEY", "")
        self._token:           Optional[str]      = None
        self._token_timestamp: Optional[datetime] = None

        # ── Try cache ────────────────────────────────────────────────────
        cached_vk, cached_tok, cached_ts = _load_token_cache()

        if not self._vendor_key and cached_vk:
            self._vendor_key = cached_vk
            logger.info(f"[{format_ist_timestamp()}] Vendor key loaded from cache")

        # ── Bootstrap vendor key from old GROWW_AUTH_TOKEN if needed ────
        if not self._vendor_key:
            old_jwt = os.getenv("GROWW_AUTH_TOKEN", "")
            if old_jwt:
                extracted = _extract_vendor_key_from_jwt(old_jwt)
                if extracted:
                    self._vendor_key = extracted
                    logger.info(
                        f"[{format_ist_timestamp()}] ✅ Vendor key auto-extracted from GROWW_AUTH_TOKEN: "
                        f"{extracted[:8]}...\n"
                        f"  Add to .env for permanent setup: GROWW_VENDOR_KEY={extracted}"
                    )
                else:
                    # GROWW_AUTH_TOKEN may itself be a vendor key (short string, not JWT)
                    if len(old_jwt) < 100:
                        self._vendor_key = old_jwt
                        logger.info(
                            f"[{format_ist_timestamp()}] Using GROWW_AUTH_TOKEN as vendor key "
                            f"(short string, not JWT)"
                        )

        # ── Use cached token if still valid ─────────────────────────────
        if cached_tok and not _is_expired_by_6am_reset(cached_ts):
            self._token           = cached_tok
            self._token_timestamp = cached_ts
            logger.info(f"[{format_ist_timestamp()}] Using cached token (valid today)")

        # ── Validate ─────────────────────────────────────────────────────
        if not self._vendor_key:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_VENDOR_KEY not set!\n"
                "  Run: cd /opt/kingtrades && python3 auth_groww.py --extract-key\n"
                "  Then add GROWW_VENDOR_KEY=<value> to .env"
            )
        if not self.totp_secret:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_TOTP_SECRET not set!\n"
                "  Copy TOTP secret from groww.in → API keys → TOTP row\n"
                "  Add to .env: GROWW_TOTP_SECRET=<base32 secret>"
            )
        if self._vendor_key and self.totp_secret:
            logger.info(
                f"[{format_ist_timestamp()}] ✅ Auth ready — "
                f"vendor key: {self._vendor_key[:8]}... | "
                "TOTP auto-refresh at 5:50 AM IST daily"
            )

    # ─────────────────────────────────────────────────────────────────────

    def _do_refresh_with_retry(self) -> Optional[str]:
        """Refresh via TOTP, up to 3 attempts with 30s wait between."""
        for attempt in range(1, 4):
            token = _totp_refresh(self._vendor_key, self.totp_secret, attempt)
            if token:
                self._token           = token
                self._token_timestamp = get_current_ist_time()
                _save_token_cache(self._vendor_key, token, self._token_timestamp)
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ Fresh token cached (attempt {attempt}/3) "
                    "— valid until 6:00 AM IST tomorrow"
                )
                return token
            if attempt < 3:
                logger.warning(
                    f"[{format_ist_timestamp()}] Attempt {attempt}/3 failed — retrying in 30s..."
                )
                time.sleep(30)

        logger.error(
            f"[{format_ist_timestamp()}] ❌ All 3 TOTP attempts failed.\n"
            "  Verify GROWW_VENDOR_KEY and GROWW_TOTP_SECRET in .env\n"
            "  Run: python3 auth_groww.py --extract-key  (to re-check vendor key)"
        )
        self._send_refresh_failed_alert()
        return None

    # ─────────────────────────────────────────────────────────────────────

    def get_valid_token(self) -> Optional[str]:
        """Return a valid token, refreshing via TOTP if Groww's 6 AM reset expired it."""
        if self._token and not _is_expired_by_6am_reset(self._token_timestamp):
            return self._token

        reason = "Token expired at 6 AM reset" if self._token else "No token"
        logger.info(f"[{format_ist_timestamp()}] {reason} — refreshing via TOTP...")
        return self._do_refresh_with_retry() or self._token

    def refresh_token_if_needed(self) -> bool:
        """Force TOTP refresh. Called at 5:50 AM IST and on demand."""
        logger.info(f"[{format_ist_timestamp()}] TOTP refresh triggered...")
        token = self._do_refresh_with_retry()
        if token:
            logger.info(f"[{format_ist_timestamp()}] ✅ Token refreshed — ready for trading")
            return True
        logger.error(f"[{format_ist_timestamp()}] ❌ Refresh failed")
        return False

    def force_refresh(self) -> bool:
        """Immediate refresh — called by /refresh Telegram command."""
        logger.info(f"[{format_ist_timestamp()}] Force refresh requested...")
        return bool(self._do_refresh_with_retry())

    def login_and_get_token(self) -> Optional[str]:
        return self.get_valid_token()

    @property
    def token(self) -> Optional[str]:
        return self._token

    @property
    def token_age_hours(self) -> float:
        if not self._token_timestamp:
            return float("inf")
        return (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600

    def _is_past_6am_reset(self, ts: Optional[datetime]) -> bool:
        return _is_expired_by_6am_reset(ts)

    # ─────────────────────────────────────────────────────────────────────

    def _send_refresh_failed_alert(self) -> None:
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
                        "🔴 <b>Groww TOTP Refresh Failed</b>\n\n"
                        "3 attempts failed. Check your VPS:\n\n"
                        "<code>cd /opt/kingtrades\n"
                        "cat .env | grep GROWW</code>\n\n"
                        "Make sure GROWW_VENDOR_KEY and GROWW_TOTP_SECRET are set.\n"
                        "Run <code>python3 auth_groww.py --extract-key</code> to fix."
                    ),
                },
                timeout=10,
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_auth_manager: Optional[GrowwAuthManager] = None


def get_auth_manager() -> GrowwAuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GrowwAuthManager()
    return _auth_manager


def get_groww_token() -> Optional[str]:
    """Get valid access token, auto-refreshing via TOTP if needed."""
    return get_auth_manager().get_valid_token()


def initialize_auth() -> bool:
    """Called at bot startup. Refreshes if token expired or in 5:45–5:59 AM window."""
    manager = get_auth_manager()
    now_ist = get_current_ist_time()
    h, m    = now_ist.hour, now_ist.minute

    pre_expiry = (h == 5 and 45 <= m <= 59)
    expired    = _is_expired_by_6am_reset(manager._token_timestamp)

    if pre_expiry:
        logger.info(f"[{format_ist_timestamp()}] Auth init: 5:45 AM window — refreshing before 6 AM...")
        manager.refresh_token_if_needed()
    elif expired:
        logger.info(f"[{format_ist_timestamp()}] Auth init: token expired — refreshing via TOTP...")
        manager.refresh_token_if_needed()
    else:
        logger.info(f"[{format_ist_timestamp()}] Auth init: token valid ✅ (refreshes at 5:50 AM IST)")

    return bool(manager._token or manager._vendor_key)


# ─────────────────────────────────────────────────────────────────────────────
# CLI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cli_extract_key():
    """Extract GROWW_VENDOR_KEY from GROWW_AUTH_TOKEN and print .env line."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    jwt = os.getenv("GROWW_AUTH_TOKEN", "")
    if not jwt:
        print("❌ GROWW_AUTH_TOKEN not set in .env — nothing to extract from.")
        print("   If you already know your vendor key, add directly:")
        print("   GROWW_VENDOR_KEY=<your_key>")
        sys.exit(1)

    key = _extract_vendor_key_from_jwt(jwt)
    if not key:
        # Maybe GROWW_AUTH_TOKEN IS the vendor key (short, not a JWT)
        if len(jwt) < 100:
            print(f"✅ GROWW_AUTH_TOKEN looks like a vendor key already (not a JWT):")
            print(f"\n   GROWW_VENDOR_KEY={jwt}\n")
            print("Add that line to /opt/kingtrades/.env")
        else:
            print("❌ Could not extract vendorIntegrationKey from your JWT.")
            print("   The JWT may have an unexpected format.")
            print("   Try: groww.in → API keys → copy the short API key (not the long JWT)")
        sys.exit(1)

    print(f"\n✅ Vendor key found: {key}\n")
    print("Add this line to /opt/kingtrades/.env:")
    print(f"\n   GROWW_VENDOR_KEY={key}\n")

    # Auto-append to .env if it exists
    env_path = Path(".env")
    if env_path.exists():
        content = env_path.read_text()
        if "GROWW_VENDOR_KEY" not in content:
            env_path.write_text(content.rstrip() + f"\nGROWW_VENDOR_KEY={key}\n")
            print(f"✅ Auto-added to {env_path.resolve()}")
            print("   You can now remove GROWW_AUTH_TOKEN from .env (optional)")
        else:
            print("   .env already has GROWW_VENDOR_KEY — no change made")


def _cli_test():
    """Test TOTP auth and show result."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    print("Testing Groww TOTP auth...\n")
    manager = GrowwAuthManager()
    print(f"vendor_key:   {'✅ ' + manager._vendor_key[:12] + '...' if manager._vendor_key else '❌ MISSING'}")
    print(f"totp_secret:  {'✅ set' if manager.totp_secret else '❌ MISSING'}")
    print(f"cached token: {'✅ valid' if manager._token else 'none (will fetch)'}\n")

    token = manager.get_valid_token()
    if token:
        print(f"✅ Token obtained: {token[:40]}...{token[-10:]}")
        print("   Auth is working correctly.")
    else:
        print("❌ Failed to get token.")
        print("   Check GROWW_VENDOR_KEY and GROWW_TOTP_SECRET in .env")
        sys.exit(1)


if __name__ == "__main__":
    if "--extract-key" in sys.argv:
        _cli_extract_key()
    else:
        _cli_test()
