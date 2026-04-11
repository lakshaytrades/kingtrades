"""
auth_groww.py — NSE Momentum Groww AI Bot
Fully Automatic TOTP Token Refresh — Zero Manual Intervention

─── HOW GROWW TOKENS WORK ───────────────────────────────────────────────────
Groww issues two kinds of credentials from developer.groww.in:

  1. GROWW_AUTH_TOKEN  — The daily trading JWT. Groww invalidates ALL JWTs
                         at exactly 6:00 AM IST every morning (their day reset).
                         You manually copy this once from developer.groww.in.

  2. GROWW_TOTP_SECRET — Your permanent TOTP base32 secret (never expires).
                         Used to generate 6-digit TOTP codes.

─── WHAT THE BOT DOES AUTOMATICALLY ────────────────────────────────────────
  At 5:50 AM IST every morning (10 min BEFORE the 6 AM expiry):
    totp_code    = pyotp.TOTP(GROWW_TOTP_SECRET).now()
    access_token = GrowwAPI.get_access_token(api_key=CURRENT_JWT, totp=totp_code)
    → fresh JWT valid until next 6:00 AM IST

  The bot uses the STILL-VALID current JWT to authenticate, then swaps it
  for the new one. This is why 5:50 AM is critical — at 6:05 AM the old
  JWT is already dead and the exchange fails.

  Fallback: If the SDK call fails, a direct HTTPS request to api.groww.in
  is tried (same endpoint, no Cloudflare block — api.groww.in is the API
  domain, not groww.in which is the web UI).

─── SETUP (ONE TIME ONLY) ───────────────────────────────────────────────────
  Set in Render → kingtrades-bot → Environment Variables:
    GROWW_AUTH_TOKEN  = current JWT from developer.groww.in → API Keys
    GROWW_TOTP_SECRET = TOTP secret from developer.groww.in → API Keys

  After this, the bot refreshes automatically forever. You never manually
  update GROWW_AUTH_TOKEN again — the bot keeps it fresh at 5:50 AM IST daily.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import requests

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

        if age_h < 20 and access_token and len(access_token) > 20:
            logger.info(
                f"[{format_ist_timestamp()}] Cache hit: access_token age {age_h:.1f}h "
                f"(valid for {20 - age_h:.1f}h more)"
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

def _extract_token_from_response(body: dict) -> Optional[str]:
    """Pull the JWT from any known key in a Groww API response dict."""
    for key in ("token", "authToken", "auth_token", "access_token",
                "jwtToken", "jwt_token", "userToken", "user_token"):
        val = body.get(key) or (body.get("data") or {}).get(key)
        if val and isinstance(val, str) and len(val) > 20:
            return val
    return None


def _refresh_via_direct_http(api_key: str, totp_code: str) -> Optional[str]:
    """
    Direct HTTPS call to api.groww.in — used when the SDK is not installed
    or when the SDK call fails.  api.groww.in is NOT behind Cloudflare so it
    works from Render (unlike groww.in/v1/api/... which is blocked).

    Tries every known token-generation endpoint pattern.
    """
    headers = {
        "Authorization":  f"Bearer {api_key}",
        "Content-Type":   "application/json",
        "Accept":         "application/json",
        "User-Agent":     "growwapi-python/1.0",
        "X-Api-Version":  "1",
    }
    endpoints = [
        # Official SDK endpoint (most likely)
        ("POST", "https://api.groww.in/v1/user/generate_token",
         {"api_key": api_key, "totp": totp_code}),
        # Alternate patterns used by various SDK versions
        ("POST", "https://api.groww.in/v1/user/session/generate_token",
         {"api_key": api_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/auth/access-token",
         {"api_key": api_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/auth/token",
         {"apiKey": api_key, "totp": totp_code}),
        # With current token in Authorization header only
        ("POST", "https://api.groww.in/v1/user/token/refresh",
         {"totp": totp_code}),
    ]

    for method, url, body in endpoints:
        try:
            resp = requests.request(
                method, url, json=body, headers=headers, timeout=20
            )
            label = url.split("/")[-1]
            if resp.status_code in (200, 201):
                try:
                    tok = _extract_token_from_response(resp.json())
                    if tok:
                        logger.info(
                            f"[{format_ist_timestamp()}] ✅ Direct HTTP token via {label} "
                            f"(HTTP {resp.status_code})"
                        )
                        return tok
                    logger.debug(f"  [{label}] HTTP 200 but no token — keys: {list(resp.json().keys())}")
                except Exception:
                    pass
            elif resp.status_code == 401:
                logger.debug(f"  [{label}] HTTP 401 — api_key rejected")
            elif resp.status_code == 404:
                logger.debug(f"  [{label}] HTTP 404 — endpoint not found (trying next)")
            else:
                logger.debug(f"  [{label}] HTTP {resp.status_code}")
        except requests.exceptions.ConnectionError:
            logger.debug(f"  [{url.split('/')[2]}] Connection error — not reachable")
        except Exception as e:
            logger.debug(f"  Direct HTTP {url.split('/')[-1]}: {e}")
    return None


def _refresh_access_token(api_key: str, totp_secret: str, attempt: int = 1) -> Optional[str]:
    """
    Refresh the Groww access token using the current JWT + TOTP.

    TIMING IS CRITICAL:
      - Call this at 5:50 AM IST — the current JWT is still valid (expires at 6:00 AM)
      - The fresh JWT is valid until next 6:00 AM IST
      - After 6:00 AM the old JWT is dead and this call fails

    Try order:
      1. growwapi SDK class method (GrowwAPI.get_access_token)
      2. growwapi SDK instance method (older SDK versions)
      3. Direct HTTPS to api.groww.in (no SDK needed, not Cloudflare-blocked)

    api_key     = GROWW_AUTH_TOKEN (the current valid JWT — refreshed daily)
    totp_secret = GROWW_TOTP_SECRET (permanent base32 secret — never changes)
    """
    if not api_key or not totp_secret:
        logger.error(
            f"[{format_ist_timestamp()}] Cannot refresh: "
            f"api_key={'set' if api_key else 'MISSING'}, "
            f"totp_secret={'set' if totp_secret else 'MISSING'}\n"
            "  → Set GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET in Render Environment."
        )
        return None

    # Wait for a TOTP window with ≥ 5s of life left (avoids code expiring mid-call)
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 5:
        wait = remaining + 1
        logger.info(f"[{format_ist_timestamp()}] Waiting {wait}s for fresh TOTP window...")
        time.sleep(wait)

    totp_code = pyotp.TOTP(totp_secret).now()
    window_left = 30 - (int(time.time()) % 30)
    logger.info(
        f"[{format_ist_timestamp()}] Token refresh attempt {attempt}/3 | "
        f"TOTP={totp_code} | window={window_left}s"
    )

    # ── Path 1: growwapi SDK class method ────────────────────────────────
    try:
        from growwapi import GrowwAPI
        access_token = GrowwAPI.get_access_token(api_key=api_key, totp=totp_code)
        if isinstance(access_token, str) and len(access_token) > 20:
            logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.get_access_token()")
            return access_token
        if isinstance(access_token, dict):
            tok = _extract_token_from_response(access_token)
            if tok:
                logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.get_access_token() [dict]")
                return tok
        logger.debug(f"SDK returned: {str(access_token)[:100]}")

    except AttributeError:
        # Older SDK — try instance method
        try:
            from growwapi import GrowwAPI
            tmp = GrowwAPI(api_key)
            for mname in ("get_access_token", "refresh_token", "generate_session"):
                fn = getattr(tmp, mname, None)
                if not fn:
                    continue
                try:
                    result = fn(totp=totp_code)
                    if result and isinstance(result, str) and len(result) > 20:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK instance.{mname}()")
                        return result
                except TypeError:
                    pass
        except Exception as e2:
            logger.debug(f"SDK instance fallback: {e2}")

    except ImportError:
        logger.debug("growwapi SDK not installed — using direct HTTP")

    except Exception as e:
        logger.warning(f"[{format_ist_timestamp()}] SDK error: {e}")

    # ── Path 2: Direct HTTPS to api.groww.in (no SDK needed) ─────────────
    logger.info(f"[{format_ist_timestamp()}] Trying direct HTTPS to api.groww.in...")
    tok = _refresh_via_direct_http(api_key, totp_code)
    if tok:
        return tok

    logger.warning(
        f"[{format_ist_timestamp()}] Attempt {attempt}/3 failed.\n"
        "  Cause: Either the JWT in GROWW_AUTH_TOKEN expired before 5:50 AM refresh,\n"
        "  or api.groww.in is temporarily unreachable from Render."
    )
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
        Get a fresh access_token using the permanent api_key + live TOTP code.
        Matches exactly what developer.groww.in docs show:

            totp_code    = pyotp.TOTP(totp_secret).now()
            access_token = GrowwAPI.get_access_token(api_key=api_key, totp=totp_code)
            groww        = GrowwAPI(access_token)

        api_key is PERMANENT — it never changes, never expires.
        access_token is DAILY — refreshed automatically at 5:50 AM IST.
        Retries 3 times with 30s spacing on transient failures.
        """
        for attempt in range(1, 4):
            token = _refresh_access_token(self._api_key, self.totp_secret, attempt)
            if token:
                self._token           = token
                self._token_timestamp = get_current_ist_time()
                # Save api_key (permanent) + access_token (daily) separately.
                # ⚠️  DO NOT update self._api_key here — it is permanent.
                _save_token_cache(self._api_key, token, self._token_timestamp)
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ Fresh access_token saved "
                    f"(attempt {attempt}/3) — valid until next 6:00 AM IST"
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
        Return a valid access_token, refreshing if needed.

        Because api_key is PERMANENT, we can call get_access_token() at any time
        of day — not just at 5:50 AM. So we refresh whenever the token is stale
        (>20h old) rather than only in a pre-expiry window.

        Called before every GrowwAPI() instantiation.
        """
        if self._token and self._token_timestamp:
            age_h = (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600
            if age_h < 20:
                return self._token
            logger.info(
                f"[{format_ist_timestamp()}] access_token {age_h:.1f}h old — refreshing..."
            )
        elif not self._token:
            logger.info(f"[{format_ist_timestamp()}] No access_token yet — fetching now...")

        # Fallback: if refresh fails, return old token (may still work for a bit)
        return self._do_refresh_with_retry() or self._token

    def refresh_token_if_needed(self) -> bool:
        """
        Force a fresh access_token via TOTP.
        Called at 5:50 AM IST daily and on demand.
        """
        logger.info(
            f"[{format_ist_timestamp()}] TOTP refresh: "
            f"pyotp.TOTP(secret).now() → GrowwAPI.get_access_token(api_key, totp)..."
        )
        token = self._do_refresh_with_retry()
        if token:
            logger.info(f"[{format_ist_timestamp()}] ✅ access_token refreshed — bot ready")
            return True
        logger.error(
            f"[{format_ist_timestamp()}] ❌ Refresh failed.\n"
            "  Verify in Render env vars:\n"
            "  • GROWW_AUTH_TOKEN  = TOTP Token from developer.groww.in → API Keys\n"
            "  • GROWW_TOTP_SECRET = TOTP Secret from developer.groww.in → API Keys"
        )
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

    Refresh policy:
    • Pre-expiry window (5:45–5:59 AM IST): ALWAYS refresh — this is the primary window
    • Token stale (>10h): refresh even mid-day (handles bot restarts after 6 AM)
    • Otherwise: use existing token as-is
    """
    manager = get_auth_manager()
    now_ist = get_current_ist_time()
    h, m = now_ist.hour, now_ist.minute

    token_age_h      = manager.token_age_hours
    pre_expiry_window = (h == 5 and m >= 45) or (h == 5 and m < 60)  # 5:45–5:59 AM
    token_stale       = token_age_h > 10   # refresh if >10h old (safe margin)

    if pre_expiry_window:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: pre-expiry window "
            f"(5:45–5:59 AM IST) — refreshing now..."
        )
        manager.refresh_token_if_needed()
    elif token_stale:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: token {token_age_h:.1f}h old — refreshing..."
        )
        manager.refresh_token_if_needed()
    else:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: token fresh "
            f"({token_age_h:.1f}h old) — no refresh needed"
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
