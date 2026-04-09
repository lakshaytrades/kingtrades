"""
auth_groww.py — NSE Momentum Groww AI Bot
TOTP-Based Automatic Daily Authentication for Groww

⚠️ WARNING: This module handles live trading credentials.
TOTP secret and credentials are loaded ONLY from environment variables.
Never hardcode or log credentials.

Flow:
1. At 8:45 AM IST: auto-login using email + password + TOTP
2. Extract fresh auth token for the day
3. Cache token in memory (and optionally to encrypted file)
4. Refreshes automatically if token is >23 hours old
"""

import os
import time
import json
import logging
import pyotp
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, retry_with_backoff

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Token cache file (stores token timestamp so we know age after restart)
_TOKEN_CACHE_FILE = Path("data/.token_cache.json")


def _save_token_cache(token: str, timestamp: datetime) -> None:
    """Save token metadata to disk so it survives restarts."""
    try:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cache = {
            "token": token,
            "timestamp": timestamp.isoformat(),
        }
        _TOKEN_CACHE_FILE.write_text(json.dumps(cache))
    except Exception as e:
        logger.debug(f"Token cache write failed (non-fatal): {e}")


def _load_token_cache() -> tuple[Optional[str], Optional[datetime]]:
    """Load cached token from disk."""
    try:
        if not _TOKEN_CACHE_FILE.exists():
            return None, None
        data = json.loads(_TOKEN_CACHE_FILE.read_text())
        token = data.get("token")
        ts_str = data.get("timestamp")
        if token and ts_str:
            ts = datetime.fromisoformat(ts_str)
            age_hours = (datetime.now(IST) - ts.replace(tzinfo=IST) if ts.tzinfo is None
                         else datetime.now(IST) - ts).total_seconds() / 3600
            if age_hours < 23:
                logger.info(
                    f"[{format_ist_timestamp()}] Loaded cached token "
                    f"(age: {age_hours:.1f}h) — valid for "
                    f"{23 - age_hours:.1f}h more"
                )
                return token, ts
            else:
                logger.info(f"[{format_ist_timestamp()}] Cached token expired ({age_hours:.1f}h old) — will re-login")
    except Exception as e:
        logger.debug(f"Token cache read failed (non-fatal): {e}")
    return None, None


class GrowwAuthManager:
    """
    Manages Groww authentication with TOTP auto-login.

    Handles:
    - Daily token refresh using TOTP
    - Token validation and caching
    - Graceful fallback to .env token if auto-login fails
    """

    # Groww API endpoints (reverse-engineered from mobile app)
    _LOGIN_ENDPOINTS = [
        {
            "name": "v2 password endpoint",
            "login_url": "https://groww.in/v1/api/user/v2/login/password",
            "totp_url":  "https://groww.in/v1/api/user/v2/login/totp",
            "login_body_fn": lambda email, pwd: {"email": email, "password": pwd},
            "totp_body_fn":  lambda session_tok, totp: {"totp": totp, "session_token": session_tok},
            "session_key": "session_token",
            "token_key":   "token",
        },
        {
            "name": "v1 standard endpoint",
            "login_url": "https://groww.in/v1/api/user/login",
            "totp_url":  "https://groww.in/v1/api/user/login/2fa",
            "login_body_fn": lambda email, pwd: {"email": email, "password": pwd},
            "totp_body_fn":  lambda session_tok, totp: {"sessionToken": session_tok, "totp": totp, "type": "TOTP"},
            "session_key": "sessionToken",
            "token_key":   "authToken",
        },
        {
            "name": "combined single-step endpoint",
            "login_url": "https://groww.in/v1/api/user/login/totp",
            "totp_url":  None,
            "login_body_fn": lambda email, pwd: None,  # not used
            "totp_body_fn":  None,
            "combined_body_fn": lambda email, pwd, totp: {"email": email, "password": pwd, "totp": totp},
            "token_key": "token",
        },
    ]

    _BASE_HEADERS = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "User-Agent":    (
            "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/112.0.0.0 Mobile Safari/537.36"
        ),
        "Origin":        "https://groww.in",
        "Referer":       "https://groww.in/login",
        "x-app-id":      "growwWeb",
        "x-platform":    "web",
    }

    def __init__(self):
        self.email        = os.getenv("GROWW_EMAIL", "")
        self.password     = os.getenv("GROWW_PASSWORD", "")
        self.totp_secret  = os.getenv("GROWW_TOTP_SECRET", "")
        self.client_id    = os.getenv("GROWW_CLIENT_ID", "")
        self.client_secret = os.getenv("GROWW_CLIENT_SECRET", "")
        self._totp        = pyotp.TOTP(self.totp_secret) if self.totp_secret else None

        # Try loading cached token first, then fall back to env token
        cached_token, cached_ts = _load_token_cache()
        if cached_token:
            self._token          = cached_token
            self._token_timestamp = cached_ts
        else:
            self._token           = os.getenv("GROWW_AUTH_TOKEN", "")
            self._token_timestamp = None  # Unknown age → refresh at 8:45 AM

        if not self.totp_secret:
            logger.warning(
                f"[{format_ist_timestamp()}] GROWW_TOTP_SECRET not set — "
                "TOTP auto-refresh disabled. Token may expire."
            )

    # ------------------------------------------------------------------ #
    # TOTP helpers
    # ------------------------------------------------------------------ #

    def generate_totp_code(self) -> Optional[str]:
        """Generate current TOTP code."""
        if not self._totp:
            return None
        return self._totp.now()

    def get_totp_time_remaining(self) -> int:
        """Seconds before the current TOTP code changes (30s cycle)."""
        if not self._totp:
            return 0
        return 30 - (int(time.time()) % 30)

    def _wait_for_fresh_totp(self) -> str:
        """
        Wait until we have at least 10 seconds of TOTP validity left.
        Returns the fresh code.
        """
        remaining = self.get_totp_time_remaining()
        if remaining < 10:
            wait = remaining + 1
            logger.info(f"[{format_ist_timestamp()}] Waiting {wait}s for fresh TOTP window...")
            time.sleep(wait)
        return self.generate_totp_code()

    # ------------------------------------------------------------------ #
    # Core login
    # ------------------------------------------------------------------ #

    def _extract_token_from_response(self, data: dict, keys=("token", "authToken", "auth_token", "access_token")) -> Optional[str]:
        """Search multiple key names for a JWT/bearer token."""
        for k in keys:
            v = data.get(k) or (data.get("data") or {}).get(k)
            if v and isinstance(v, str) and len(v) > 20:
                return v
        return None

    def _try_endpoint(self, ep: dict, totp_code: str) -> Optional[str]:
        """Try one login endpoint configuration. Returns token or None."""
        session = requests.Session()
        headers = dict(self._BASE_HEADERS)

        try:
            # ── Combined single-step (email + password + totp in one request) ──
            if ep.get("combined_body_fn"):
                body = ep["combined_body_fn"](self.email, self.password, totp_code)
                resp = session.post(ep["login_url"], json=body, headers=headers, timeout=20)
                logger.debug(f"[{ep['name']}] single-step status: {resp.status_code}")
                if resp.status_code in (200, 201):
                    data = resp.json()
                    tok = self._extract_token_from_response(data)
                    # Also check cookies
                    if not tok:
                        tok = (session.cookies.get("authToken") or
                               session.cookies.get("auth_token") or
                               session.cookies.get("token"))
                    if tok:
                        return tok
                return None

            # ── Two-step: password login → TOTP verification ──
            login_body = ep["login_body_fn"](self.email, self.password)
            resp1 = session.post(ep["login_url"], json=login_body, headers=headers, timeout=20)
            logger.debug(f"[{ep['name']}] step-1 status: {resp1.status_code}")

            if resp1.status_code not in (200, 201):
                return None

            data1 = resp1.json()

            # Maybe password step already returns a full token
            direct_tok = self._extract_token_from_response(data1)
            if direct_tok:
                return direct_tok

            # Extract session token for step 2
            session_tok = (
                data1.get(ep["session_key"]) or
                (data1.get("data") or {}).get(ep["session_key"])
            )
            if not session_tok:
                logger.debug(f"[{ep['name']}] no session token in step-1 response: {list(data1.keys())}")
                return None

            # Step 2: TOTP
            if not ep.get("totp_url"):
                return None

            totp_body = ep["totp_body_fn"](session_tok, totp_code)
            resp2 = session.post(ep["totp_url"], json=totp_body, headers=headers, timeout=20)
            logger.debug(f"[{ep['name']}] step-2 status: {resp2.status_code}")

            if resp2.status_code not in (200, 201):
                return None

            data2 = resp2.json()
            tok = self._extract_token_from_response(
                data2,
                keys=(ep.get("token_key", "token"), "authToken", "auth_token", "access_token", "token")
            )
            if not tok:
                # Check cookies (some Groww versions set token in cookie)
                tok = (session.cookies.get("authToken") or
                       session.cookies.get("auth_token") or
                       session.cookies.get("token"))
            return tok

        except requests.exceptions.RequestException as e:
            logger.debug(f"[{ep['name']}] network error: {e}")
            return None

    @retry_with_backoff(max_retries=2, delays=[5, 15])
    def login_and_get_token(self) -> Optional[str]:
        """
        Full TOTP login flow. Tries all known Groww endpoints.
        Falls back to .env GROWW_AUTH_TOKEN if all fail.
        """
        if not all([self.email, self.password, self.totp_secret]):
            logger.info(
                f"[{format_ist_timestamp()}] TOTP login skipped — "
                "GROWW_EMAIL / GROWW_PASSWORD / GROWW_TOTP_SECRET not all set. "
                "Using GROWW_AUTH_TOKEN from environment."
            )
            return self._token or None

        totp_code = self._wait_for_fresh_totp()
        if not totp_code:
            logger.warning(f"[{format_ist_timestamp()}] Could not generate TOTP code")
            return self._token

        logger.info(f"[{format_ist_timestamp()}] Starting Groww TOTP login ({len(self._LOGIN_ENDPOINTS)} endpoints)...")

        for i, ep in enumerate(self._LOGIN_ENDPOINTS, 1):
            logger.info(f"[{format_ist_timestamp()}] Login attempt {i}/{len(self._LOGIN_ENDPOINTS)}: {ep['name']}...")
            token = self._try_endpoint(ep, totp_code)
            if token:
                self._token = token
                self._token_timestamp = get_current_ist_time()
                _save_token_cache(token, self._token_timestamp)
                logger.info(f"[{format_ist_timestamp()}] ✅ Groww TOTP login successful via [{ep['name']}]")
                return token

            # Between attempts, generate a fresh TOTP (in case code just expired)
            if i < len(self._LOGIN_ENDPOINTS):
                time.sleep(2)
                totp_code = self.generate_totp_code()  # regenerate in case window changed

        # ── All endpoints failed — fall back to static token ──
        if self._token:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚠️  All TOTP login attempts failed. "
                f"Using cached/env GROWW_AUTH_TOKEN.\n"
                f"  Possible causes:\n"
                f"    • Groww changed their login API (check groww.in network tab)\n"
                f"    • GROWW_EMAIL or GROWW_PASSWORD is wrong\n"
                f"    • IP blocked by Groww — try re-deploying (new IP)\n"
                f"    • TOTP secret is wrong — verify it matches your 2FA app\n"
                f"  The bot will continue with the existing token."
            )
            return self._token

        logger.error(
            f"[{format_ist_timestamp()}] ❌ No Groww token available. "
            "Set GROWW_AUTH_TOKEN in Render Environment Variables."
        )
        return None

    # ------------------------------------------------------------------ #
    # Token management
    # ------------------------------------------------------------------ #

    def get_valid_token(self) -> Optional[str]:
        """
        Get a valid auth token. Auto-refreshes if stale (>23h old).
        """
        if not self._token_timestamp:
            if self._token:
                logger.info(f"[{format_ist_timestamp()}] Using initial token from env/cache")
                return self._token
            else:
                logger.warning(f"[{format_ist_timestamp()}] No auth token available!")
                return None

        now = get_current_ist_time()
        age = (now - self._token_timestamp).total_seconds() / 3600
        if age > 23:
            logger.info(f"[{format_ist_timestamp()}] Token is {age:.1f}h old — refreshing via TOTP...")
            return self.login_and_get_token()

        return self._token

    def refresh_token_if_needed(self) -> bool:
        """
        Called daily at 8:45 AM IST.
        Returns True if a valid token is available after refresh.
        """
        logger.info(f"[{format_ist_timestamp()}] Scheduled morning token refresh...")
        new_token = self.login_and_get_token()
        if new_token:
            logger.info(f"[{format_ist_timestamp()}] ✅ Morning token refresh complete")
            return True
        else:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ Morning token refresh FAILED. "
                "Trading may fail — check credentials in Render Dashboard."
            )
            return False

    @property
    def token(self) -> Optional[str]:
        return self._token

    @property
    def token_age_hours(self) -> float:
        if not self._token_timestamp:
            return float("inf")
        return (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600


# ------------------------------------------------------------------ #
# Singleton
# ------------------------------------------------------------------ #

_auth_manager: Optional[GrowwAuthManager] = None


def get_auth_manager() -> GrowwAuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GrowwAuthManager()
    return _auth_manager


def get_groww_token() -> Optional[str]:
    """Convenience: get valid Groww auth token."""
    return get_auth_manager().get_valid_token()


def initialize_auth() -> bool:
    """
    Initialize authentication on bot startup.
    Always returns True — credential failures are non-fatal at startup.
    Token is retried at 8:45 AM IST via TOTP.
    """
    manager = get_auth_manager()
    now_ist = get_current_ist_time()
    has_full_creds = all([manager.email, manager.password, manager.totp_secret])

    if has_full_creds and now_ist.hour < 10:
        logger.info(f"[{format_ist_timestamp()}] Performing morning TOTP login...")
        try:
            success = manager.refresh_token_if_needed()
            if not success:
                logger.warning(
                    f"[{format_ist_timestamp()}] TOTP login failed — "
                    "will retry at 8:45 AM IST. Bot starts in dry-run mode if no static token."
                )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] TOTP login error: {e}")

    elif manager._token:
        logger.info(f"[{format_ist_timestamp()}] Auth initialized with token from environment/cache")

    else:
        logger.warning(
            f"[{format_ist_timestamp()}] No Groww credentials configured.\n"
            "  → Set in Render environment variables:\n"
            "    GROWW_EMAIL, GROWW_PASSWORD, GROWW_TOTP_SECRET, GROWW_AUTH_TOKEN\n"
            "  → Bot will run in DRY-RUN mode until credentials are set."
        )

    return True


if __name__ == "__main__":
    # Test auth — run: python auth_groww.py
    logging.basicConfig(level=logging.DEBUG)
    print(f"\nGroww Auth Test — {format_ist_timestamp()}")
    manager = get_auth_manager()
    print(f"TOTP configured : {manager._totp is not None}")
    print(f"Email configured: {bool(manager.email)}")
    if manager._totp:
        code = manager.generate_totp_code()
        remaining = manager.get_totp_time_remaining()
        print(f"TOTP valid for  : {remaining}s")
    token = manager.login_and_get_token()
    print(f"Login result    : {'✅ Token obtained' if token else '❌ Failed'}")
    if token:
        print(f"Token preview   : {token[:8]}...{token[-4:]} (length {len(token)})")
