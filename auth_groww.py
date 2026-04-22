"""
auth_groww.py — NSE Momentum Groww AI Bot
Fully Automatic TOTP Login — Zero Manual Intervention After Setup

─── HOW IT WORKS ────────────────────────────────────────────────────────────
  Groww resets ALL access tokens at 6:00 AM IST daily.

  Two permanent env vars (set ONCE in .env, never change):
    GROWW_AUTH_TOKEN  = your current access token (copy from groww.in today)
    GROWW_TOTP_SECRET = your TOTP base32 secret (copy from groww.in → TOTP row)

  What the bot does automatically:
    1. At 5:50 AM IST — uses TOTP to generate a fresh token before 6 AM reset
    2. On startup — if token already expired (past 6 AM), uses TOTP immediately
    3. Caches token to disk — survives service restarts within same day

  TOTP flow (fully automatic):
    totp_code    = pyotp.TOTP(GROWW_TOTP_SECRET).now()       ← 6-digit code
    access_token = GrowwAPI.get_access_token(                 ← fresh token
                       api_key=GROWW_AUTH_TOKEN,
                       totp=totp_code
                   )

─── ONE-TIME SETUP ──────────────────────────────────────────────────────────
  1. Go to groww.in → API keys
  2. Copy the ACCESS TOKEN → set as GROWW_AUTH_TOKEN in .env
  3. Copy the TOTP secret  → set as GROWW_TOTP_SECRET in .env
  4. Restart bot → fully automatic forever after this
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
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
# Token cache
# ─────────────────────────────────────────────────────────────────────────────

def _save_token_cache(api_key: str, access_token: str, timestamp: datetime,
                      vendor_key: str = "") -> None:
    try:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE_FILE.write_text(json.dumps({
            "api_key":      api_key,
            "access_token": access_token,
            "timestamp":    timestamp.isoformat(),
            "vendor_key":   vendor_key,
        }))
    except Exception as e:
        logger.debug(f"Token cache write failed: {e}")


def _load_token_cache() -> Tuple[Optional[str], Optional[str], Optional[datetime], str]:
    try:
        if not _TOKEN_CACHE_FILE.exists():
            return None, None, None, ""
        data = json.loads(_TOKEN_CACHE_FILE.read_text())
        api_key      = data.get("api_key", "") or None
        access_token = data.get("access_token", "") or None
        vendor_key   = data.get("vendor_key", "") or ""
        ts_str       = data.get("timestamp", "")
        if not ts_str:
            return api_key, None, None, vendor_key
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        return api_key, access_token, ts, vendor_key
    except Exception as e:
        logger.debug(f"Token cache read failed: {e}")
    return None, None, None, ""


# ─────────────────────────────────────────────────────────────────────────────
# 6 AM IST reset check
# ─────────────────────────────────────────────────────────────────────────────

def _is_expired_by_6am_reset(token_ts: Optional[datetime]) -> bool:
    """
    Returns True if Groww's 6 AM IST daily reset has invalidated this token.
    A token issued before today's 6 AM is expired if it's now past 6 AM.
    """
    if token_ts is None:
        return True
    now = get_current_ist_time()
    if token_ts.tzinfo is None:
        token_ts = token_ts.replace(tzinfo=IST)
    reset_today = now.replace(hour=6, minute=0, second=0, microsecond=0)
    return token_ts < reset_today and now >= reset_today


# ─────────────────────────────────────────────────────────────────────────────
# Core: get fresh token via TOTP
# ─────────────────────────────────────────────────────────────────────────────

def _extract_vendor_key(jwt_token: str) -> Optional[str]:
    """
    Extract the permanent vendorIntegrationKey from a Groww access token JWT.

    Groww embeds the permanent vendor key inside the JWT payload's 'sub' field.
    GrowwAPI.get_access_token() expects THIS key as api_key — not the full JWT.
    The vendor key never expires; only the JWT wrapper does at 6 AM IST.
    """
    try:
        import base64, json as _json
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        sub = payload.get("sub", "")
        if isinstance(sub, str) and sub.startswith("{"):
            sub_data = _json.loads(sub)
            key = sub_data.get("vendorIntegrationKey")
            if key and len(key) > 8:
                return key
    except Exception as e:
        logger.debug(f"JWT vendor key extraction failed: {e}")
    return None


def _extract_token(body: dict) -> Optional[str]:
    """Extract access token string from a Groww API response dict."""
    for key in ("token", "authToken", "auth_token", "access_token",
                "jwtToken", "jwt_token", "userToken", "user_token"):
        val = body.get(key) or (body.get("data") or {}).get(key)
        if val and isinstance(val, str) and len(val) > 20:
            return val
    return None


def _totp_refresh(api_key: str, totp_secret: str, attempt: int = 1,
                  vendor_key: str = "") -> Optional[str]:
    """
    Get a fresh Groww access token using TOTP.

    api_key    = GROWW_AUTH_TOKEN (today's JWT — used to extract vendor key)
    vendor_key = permanent vendorIntegrationKey from JWT (what SDK needs as api_key)
    totp_secret = GROWW_TOTP_SECRET (permanent base32 — never changes)

    GrowwAPI.get_access_token(api_key=vendor_key, totp=code) → fresh JWT
    """
    if not api_key or not totp_secret:
        logger.error(
            f"[{format_ist_timestamp()}] Cannot refresh — missing credentials:\n"
            f"  GROWW_AUTH_TOKEN:  {'set' if api_key else '❌ MISSING'}\n"
            f"  GROWW_TOTP_SECRET: {'set' if totp_secret else '❌ MISSING'}\n"
            "  Copy both from groww.in → API keys page"
        )
        return None

    # Wait for TOTP window with ≥ 5s remaining (avoids code expiring mid-call)
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 5:
        wait = remaining + 1
        logger.info(f"[{format_ist_timestamp()}] Waiting {wait}s for fresh TOTP window...")
        time.sleep(wait)

    totp_code   = pyotp.TOTP(totp_secret).now()
    window_left = 30 - (int(time.time()) % 30)
    logger.info(
        f"[{format_ist_timestamp()}] TOTP refresh attempt {attempt}/3 | "
        f"code={totp_code} | window={window_left}s left"
    )

    # ── Method 1: growwapi SDK ────────────────────────────────────────────
    # Groww SDK expects the permanent vendorIntegrationKey as api_key,
    # NOT the full expiring JWT. We try vendor_key first, then full JWT.
    sdk_keys_to_try = []
    if vendor_key:
        sdk_keys_to_try.append(("vendor_key", vendor_key))
    sdk_keys_to_try.append(("full_jwt", api_key))

    try:
        from growwapi import GrowwAPI
        for key_label, key_val in sdk_keys_to_try:
            try:
                result = GrowwAPI.get_access_token(api_key=key_val, totp=totp_code)
                if isinstance(result, str) and len(result) > 20:
                    logger.info(
                        f"[{format_ist_timestamp()}] ✅ Token via SDK "
                        f"({key_label}, get_access_token)"
                    )
                    return result
                if isinstance(result, dict):
                    tok = _extract_token(result)
                    if tok:
                        logger.info(
                            f"[{format_ist_timestamp()}] ✅ Token via SDK "
                            f"({key_label}, get_access_token) [dict]"
                        )
                        return tok
                logger.debug(f"SDK ({key_label}): unexpected response {str(result)[:100]}")
            except Exception as inner_e:
                logger.debug(f"SDK ({key_label}): {inner_e}")

        # Older SDK — instance methods
        for key_label, key_val in sdk_keys_to_try:
            try:
                tmp = GrowwAPI(key_val)
                for mname in ("get_access_token", "refresh_token", "generate_session"):
                    fn = getattr(tmp, mname, None)
                    if fn:
                        try:
                            r = fn(totp=totp_code)
                            if r and isinstance(r, str) and len(r) > 20:
                                logger.info(
                                    f"[{format_ist_timestamp()}] ✅ Token via SDK "
                                    f"instance.{mname}() ({key_label})"
                                )
                                return r
                        except TypeError:
                            pass
            except Exception:
                pass

    except ImportError:
        logger.debug("growwapi not installed — trying direct HTTP")
    except Exception as e:
        logger.warning(f"[{format_ist_timestamp()}] SDK error: {e}")

    # ── Method 2: direct HTTPS (no SDK dependency) ───────────────────────
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "User-Agent":    "growwapi-python/1.0",
        "X-Api-Version": "1",
    }
    endpoints = [
        ("POST", "https://api.groww.in/v1/user/generate_token",
         {"api_key": api_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/user/session/generate_token",
         {"api_key": api_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/auth/access-token",
         {"api_key": api_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/auth/token",
         {"apiKey": api_key, "totp": totp_code}),
        ("POST", "https://api.groww.in/v1/user/token/refresh",
         {"totp": totp_code}),
    ]
    for method, url, body in endpoints:
        label = url.split("/")[-1]
        try:
            resp = requests.request(method, url, json=body, headers=headers, timeout=20)
            if resp.status_code in (200, 201):
                tok = _extract_token(resp.json())
                if tok:
                    logger.info(
                        f"[{format_ist_timestamp()}] ✅ Token via direct HTTP [{label}]"
                    )
                    return tok
                logger.debug(f"  [{label}] HTTP 200 but no token — keys: {list(resp.json().keys())}")
            elif resp.status_code == 401:
                logger.debug(f"  [{label}] 401 — api_key rejected (may be expired)")
            elif resp.status_code == 404:
                logger.debug(f"  [{label}] 404 — endpoint not found")
            else:
                logger.debug(f"  [{label}] HTTP {resp.status_code}")
        except requests.exceptions.ConnectionError:
            logger.debug(f"  [{label}] Connection error")
        except Exception as e:
            logger.debug(f"  [{label}] {e}")

    logger.warning(
        f"[{format_ist_timestamp()}] Attempt {attempt}/3 failed — "
        "all TOTP methods exhausted"
    )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Auth Manager
# ─────────────────────────────────────────────────────────────────────────────

class GrowwAuthManager:
    """
    Fully automatic Groww authentication.

    Set ONCE in /opt/kingtrades/.env:
      GROWW_AUTH_TOKEN  = today's access token from groww.in → API keys
      GROWW_TOTP_SECRET = TOTP base32 secret from groww.in → TOTP row

    Bot auto-refreshes at 5:50 AM IST before Groww's 6 AM daily reset.
    If started after 6 AM (token already expired), refreshes immediately.
    """

    def __init__(self):
        self._api_key    = os.getenv("GROWW_AUTH_TOKEN", "")
        self.totp_secret = os.getenv("GROWW_TOTP_SECRET", "")
        self.email       = os.getenv("GROWW_EMAIL", "")
        self.password    = os.getenv("GROWW_PASSWORD", "")

        self._token:           Optional[str]      = None
        self._token_timestamp: Optional[datetime] = None
        self._vendor_key:      str                = ""

        # ── Try cache first ───────────────────────────────────────────────
        cached_key, cached_tok, cached_ts, cached_vk = _load_token_cache()

        if not self._api_key and cached_key:
            self._api_key = cached_key
            logger.info(f"[{format_ist_timestamp()}] api_key restored from cache")

        # Extract permanent vendor key from current JWT (or use cached)
        if self._api_key:
            self._vendor_key = _extract_vendor_key(self._api_key) or cached_vk or ""
        else:
            self._vendor_key = cached_vk or ""

        if self._vendor_key:
            logger.info(
                f"[{format_ist_timestamp()}] Vendor key: {self._vendor_key[:8]}... "
                "(permanent — used for TOTP refresh)"
            )
        else:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚠️  Vendor key not found in JWT — "
                "TOTP refresh will try full JWT as fallback"
            )

        if cached_tok and not _is_expired_by_6am_reset(cached_ts):
            self._token           = cached_tok
            self._token_timestamp = cached_ts
            logger.info(f"[{format_ist_timestamp()}] Using cached access_token (still valid)")
        elif self._api_key and not _is_expired_by_6am_reset(None):
            # Token from env is still valid today (before 6 AM reset)
            self._token           = self._api_key
            self._token_timestamp = get_current_ist_time()
            logger.info(f"[{format_ist_timestamp()}] Using today's GROWW_AUTH_TOKEN")
        else:
            logger.info(
                f"[{format_ist_timestamp()}] Token expired (past 6 AM reset) — "
                "will refresh via TOTP on first use"
            )

        # ── Validate ──────────────────────────────────────────────────────
        if not self._api_key:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_AUTH_TOKEN not set!\n"
                "  1. Go to groww.in → API keys\n"
                "  2. Copy ACCESS TOKEN\n"
                "  3. Add to /opt/kingtrades/.env: GROWW_AUTH_TOKEN=<paste>"
            )
        if not self.totp_secret:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_TOTP_SECRET not set!\n"
                "  1. Go to groww.in → API keys\n"
                "  2. Copy TOTP secret (base32 string from TOTP row)\n"
                "  3. Add to /opt/kingtrades/.env: GROWW_TOTP_SECRET=<paste>"
            )
        if self._api_key and self.totp_secret:
            logger.info(
                f"[{format_ist_timestamp()}] ✅ Auth ready — "
                "TOTP auto-refresh at 5:50 AM IST daily"
            )

    # ─────────────────────────────────────────────────────────────────────
    # TOTP refresh with retry
    # ─────────────────────────────────────────────────────────────────────

    def _do_refresh_with_retry(self) -> Optional[str]:
        """Refresh via TOTP, retrying 3 times with 30s between attempts."""
        for attempt in range(1, 4):
            token = _totp_refresh(
                self._api_key, self.totp_secret, attempt,
                vendor_key=self._vendor_key,
            )
            if token:
                self._token           = token
                self._token_timestamp = get_current_ist_time()
                # Extract fresh vendor key from new token if we didn't have one
                if not self._vendor_key:
                    self._vendor_key = _extract_vendor_key(token) or ""
                _save_token_cache(
                    self._api_key, token, self._token_timestamp,
                    vendor_key=self._vendor_key,
                )
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ Fresh token saved (attempt {attempt}/3) "
                    "— valid until 6:00 AM IST tomorrow"
                )
                return token
            if attempt < 3:
                logger.warning(
                    f"[{format_ist_timestamp()}] Attempt {attempt}/3 failed — retrying in 30s..."
                )
                time.sleep(30)

        logger.error(
            f"[{format_ist_timestamp()}] ❌ All 3 TOTP refresh attempts failed.\n"
            "  Check /opt/kingtrades/.env:\n"
            "    GROWW_AUTH_TOKEN  = today's access token from groww.in\n"
            "    GROWW_TOTP_SECRET = TOTP secret from groww.in (permanent)"
        )
        self._send_refresh_failed_alert()
        return None

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def get_valid_token(self) -> Optional[str]:
        """Return a valid access token, refreshing via TOTP if expired."""
        # Check if 6 AM reset has expired the token
        if self._token and not _is_expired_by_6am_reset(self._token_timestamp):
            return self._token

        if self._token:
            logger.info(
                f"[{format_ist_timestamp()}] Token expired at 6 AM IST reset — "
                "refreshing via TOTP now..."
            )
        else:
            logger.info(f"[{format_ist_timestamp()}] No token — fetching via TOTP...")

        return self._do_refresh_with_retry() or self._token

    def refresh_token_if_needed(self) -> bool:
        """Force TOTP refresh. Called at 5:50 AM IST and on demand."""
        logger.info(f"[{format_ist_timestamp()}] TOTP refresh triggered...")
        token = self._do_refresh_with_retry()
        if token:
            logger.info(f"[{format_ist_timestamp()}] ✅ Token refreshed — ready for trading")
            return True
        logger.error(
            f"[{format_ist_timestamp()}] ❌ Refresh failed.\n"
            "  GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET must both be set in .env"
        )
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
    # Telegram alerts
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
                        "🔴 <b>Groww Token Refresh Failed</b>\n\n"
                        "TOTP auto-refresh failed 3 times.\n\n"
                        "<b>Action required — on your VPS:</b>\n"
                        "1. Open groww.in → API keys\n"
                        "2. Generate new access token\n"
                        "3. Run:\n"
                        "<code>python3 &lt;&lt; 'EOF'\n"
                        "with open('/opt/kingtrades/.env') as f: lines=f.readlines()\n"
                        "# paste new token command\n"
                        "EOF</code>\n\n"
                        "Or send /refresh once TOTP secret is correct."
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
    """Get valid access token (auto-refreshes via TOTP if expired)."""
    return get_auth_manager().get_valid_token()


def initialize_auth() -> bool:
    """
    Called at bot startup. Refreshes token if expired or in 5:45-5:59 AM window.
    """
    manager = get_auth_manager()
    now_ist = get_current_ist_time()
    h, m    = now_ist.hour, now_ist.minute

    pre_expiry = (h == 5 and 45 <= m <= 59)
    expired    = _is_expired_by_6am_reset(manager._token_timestamp)

    if pre_expiry:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: 5:45 AM window — "
            "refreshing before 6 AM reset..."
        )
        manager.refresh_token_if_needed()
    elif expired:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: token expired — "
            "refreshing via TOTP now..."
        )
        manager.refresh_token_if_needed()
    else:
        logger.info(
            f"[{format_ist_timestamp()}] Auth init: token valid ✅ "
            "(refreshes at 5:50 AM IST)"
        )

    return bool(manager._token or manager._api_key)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Testing Groww TOTP auth...")
    manager = GrowwAuthManager()
    print(f"api_key set:    {bool(manager._api_key)}")
    print(f"totp_secret set:{bool(manager.totp_secret)}")
    token = manager.get_valid_token()
    if token:
        print(f"✅ Token: {token[:40]}...{token[-10:]}")
    else:
        print("❌ Failed — check GROWW_AUTH_TOKEN and GROWW_TOTP_SECRET in .env")
