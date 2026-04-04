"""
auth_groww.py — NSE Momentum Groww AI Bot
TOTP-Based Automatic Daily Authentication for Groww

⚠️ WARNING: This module handles live trading credentials.
TOTP secret and credentials are loaded ONLY from environment variables.
Never hardcode or log credentials.

Flow:
1. At 8:45 AM IST: auto-login using email + password + TOTP
2. Extract fresh auth token for the day
3. Store token in memory only (never written to disk)
4. Refresh mid-day if token expires
"""

import os
import time
import logging
import pyotp
import requests
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, retry_with_backoff

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# In-memory token store (never persisted to disk)
_auth_token: Optional[str] = None
_token_expiry: Optional[datetime] = None
_token_refreshed_today: bool = False


class GrowwAuthManager:
    """
    Manages Groww authentication with TOTP auto-login.

    Handles:
    - Daily token refresh using TOTP
    - Token validation
    - Graceful fallback to .env token if auto-login fails
    """

    def __init__(self):
        self.email = os.getenv("GROWW_EMAIL", "")
        self.password = os.getenv("GROWW_PASSWORD", "")
        self.totp_secret = os.getenv("GROWW_TOTP_SECRET", "")
        self.client_id = os.getenv("GROWW_CLIENT_ID", "")
        self.client_secret = os.getenv("GROWW_CLIENT_SECRET", "")
        self._token: Optional[str] = os.getenv("GROWW_AUTH_TOKEN", "")
        self._token_timestamp: Optional[datetime] = None
        self._totp = pyotp.TOTP(self.totp_secret) if self.totp_secret else None

        if not self.totp_secret:
            logger.warning(
                f"[{format_ist_timestamp()}] GROWW_TOTP_SECRET not set. "
                "TOTP auto-refresh disabled — token may expire mid-day."
            )

    def generate_totp_code(self) -> Optional[str]:
        """Generate current TOTP code for Groww 2FA."""
        if not self._totp:
            logger.error(f"[{format_ist_timestamp()}] TOTP not configured")
            return None
        code = self._totp.now()
        logger.debug(f"[{format_ist_timestamp()}] TOTP code generated (not logged for security)")
        return code

    def get_totp_time_remaining(self) -> int:
        """Returns seconds remaining before TOTP code changes (30s cycle)."""
        if not self._totp:
            return 0
        return 30 - (int(time.time()) % 30)

    @retry_with_backoff(max_retries=2, delays=[3, 8])
    def login_and_get_token(self) -> Optional[str]:
        """
        Headless TOTP login for Groww — works on Render/VPS (no browser needed).
        Tries multiple API endpoint patterns. Falls back to static token.
        """
        if not all([self.email, self.password, self.totp_secret]):
            logger.info(
                f"[{format_ist_timestamp()}] TOTP login skipped — "
                "GROWW_EMAIL / GROWW_PASSWORD / GROWW_TOTP_SECRET not all set. "
                "Using GROWW_AUTH_TOKEN from environment."
            )
            return self._token or None

        # Wait for a stable TOTP window (avoid codes about to expire)
        remaining = self.get_totp_time_remaining()
        if remaining < 5:
            wait = remaining + 1
            logger.info(f"[{format_ist_timestamp()}] Waiting {wait}s for fresh TOTP code...")
            time.sleep(wait)

        totp_code = self.generate_totp_code()
        if not totp_code:
            logger.warning(f"[{format_ist_timestamp()}] Could not generate TOTP code")
            return self._token

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Origin": "https://groww.in",
            "Referer": "https://groww.in/",
        }

        # --- ATTEMPT 1: Standard Groww API v1 login ---
        try:
            logger.info(f"[{format_ist_timestamp()}] TOTP login attempt 1/2...")
            session = requests.Session()

            # Step 1: Password login
            login_resp = session.post(
                "https://api.groww.in/v1/user/login",
                json={"email": self.email, "password": self.password},
                headers=headers, timeout=20
            )
            logger.debug(f"Login response: {login_resp.status_code}")

            if login_resp.status_code in (200, 201):
                data = login_resp.json()
                # Extract session / interim token
                session_token = (
                    data.get("session_token") or data.get("sessionToken") or
                    data.get("token") or data.get("data", {}).get("session_token")
                )

                # If direct token returned (some API versions skip TOTP step)
                direct_token = (
                    data.get("auth_token") or data.get("authToken") or
                    data.get("access_token") or data.get("data", {}).get("auth_token")
                )
                if direct_token and len(direct_token) > 20:
                    self._token = direct_token
                    self._token_timestamp = get_current_ist_time()
                    logger.info(f"[{format_ist_timestamp()}] ✅ Groww login successful (direct token)")
                    return direct_token

                if session_token:
                    # Step 2: TOTP verification
                    totp_resp = session.post(
                        "https://api.groww.in/v1/user/login/2fa",
                        json={"sessionToken": session_token, "totp": totp_code, "type": "TOTP"},
                        headers=headers, timeout=20
                    )
                    if totp_resp.status_code in (200, 201):
                        tdata = totp_resp.json()
                        new_token = (
                            tdata.get("auth_token") or tdata.get("authToken") or
                            tdata.get("access_token") or tdata.get("token") or
                            tdata.get("data", {}).get("auth_token")
                        )
                        if new_token and len(new_token) > 20:
                            self._token = new_token
                            self._token_timestamp = get_current_ist_time()
                            logger.info(f"[{format_ist_timestamp()}] ✅ Groww TOTP login successful")
                            return new_token
                        logger.debug(f"TOTP response keys: {list(tdata.keys())}")
                    else:
                        logger.debug(f"TOTP verify status: {totp_resp.status_code}")

        except requests.exceptions.RequestException as e:
            logger.debug(f"Login attempt 1 network error: {e}")

        # --- ATTEMPT 2: Alternative endpoint ---
        try:
            logger.info(f"[{format_ist_timestamp()}] TOTP login attempt 2/2 (alt endpoint)...")
            session2 = requests.Session()
            login_resp2 = session2.post(
                "https://api.groww.in/v1/login",
                json={"email": self.email, "password": self.password, "totp": totp_code},
                headers=headers, timeout=20
            )
            if login_resp2.status_code in (200, 201):
                d = login_resp2.json()
                tok = (
                    d.get("auth_token") or d.get("authToken") or
                    d.get("access_token") or d.get("token") or
                    (d.get("data") or {}).get("auth_token")
                )
                if tok and len(tok) > 20:
                    self._token = tok
                    self._token_timestamp = get_current_ist_time()
                    logger.info(f"[{format_ist_timestamp()}] ✅ Groww login via alt endpoint")
                    return tok
        except requests.exceptions.RequestException as e:
            logger.debug(f"Login attempt 2 network error: {e}")

        # --- FALLBACK: Use static token from environment ---
        if self._token:
            logger.warning(
                f"[{format_ist_timestamp()}] TOTP login failed — using GROWW_AUTH_TOKEN from env. "
                "Check GROWW_EMAIL / GROWW_PASSWORD / GROWW_TOTP_SECRET on Render."
            )
            return self._token

        logger.error(
            f"[{format_ist_timestamp()}] ❌ No Groww token available. "
            "Set GROWW_AUTH_TOKEN in Render Environment Variables."
        )
        return None

    def get_valid_token(self) -> Optional[str]:
        """
        Get a valid auth token.
        Auto-refreshes via TOTP if token is stale (>23 hours old).
        """
        if not self._token_timestamp:
            # First call — use .env token but schedule refresh
            if self._token:
                logger.info(
                    f"[{format_ist_timestamp()}] Using initial token from .env"
                )
                return self._token
            else:
                logger.warning(
                    f"[{format_ist_timestamp()}] No auth token available!"
                )
                return None

        # Check if token is older than 23 hours
        now = get_current_ist_time()
        age = (now - self._token_timestamp).total_seconds() / 3600
        if age > 23:
            logger.info(
                f"[{format_ist_timestamp()}] Token is {age:.1f}h old — refreshing..."
            )
            return self.login_and_get_token()

        return self._token

    def refresh_token_if_needed(self) -> bool:
        """
        Called daily at 8:45 AM IST.
        Returns True if refresh was successful.
        """
        logger.info(f"[{format_ist_timestamp()}] Scheduled morning token refresh...")
        new_token = self.login_and_get_token()
        if new_token:
            logger.info(f"[{format_ist_timestamp()}] ✅ Morning token refresh complete")
            return True
        else:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ Morning token refresh FAILED. "
                "Trading may fail — check credentials!"
            )
            return False

    @property
    def token(self) -> Optional[str]:
        """Current auth token."""
        return self._token

    @property
    def token_age_hours(self) -> float:
        """Age of current token in hours."""
        if not self._token_timestamp:
            return float('inf')
        now = get_current_ist_time()
        return (now - self._token_timestamp).total_seconds() / 3600


# Singleton instance
_auth_manager: Optional[GrowwAuthManager] = None


def get_auth_manager() -> GrowwAuthManager:
    """Get or create the singleton auth manager."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GrowwAuthManager()
    return _auth_manager


def get_groww_token() -> Optional[str]:
    """
    Convenience function to get valid Groww auth token.
    Use this in other modules instead of directly accessing config.
    """
    return get_auth_manager().get_valid_token()


def initialize_auth() -> bool:
    """
    Initialize authentication on bot startup.
    Always returns True — credential failures are non-fatal at startup.
    The bot runs in dry-run mode until credentials are available.
    Token is retried at 8:45 AM IST via TOTP.
    """
    manager = get_auth_manager()

    # If TOTP is fully configured and it's early morning, try fresh login
    now_ist = get_current_ist_time()
    has_full_creds = all([manager.email, manager.password, manager.totp_secret])

    if has_full_creds and now_ist.hour < 10:
        logger.info(f"[{format_ist_timestamp()}] Performing morning TOTP login...")
        try:
            success = manager.refresh_token_if_needed()
            if success:
                logger.info(f"[{format_ist_timestamp()}] ✅ TOTP login successful")
            else:
                logger.warning(
                    f"[{format_ist_timestamp()}] TOTP login failed — "
                    "will retry at 8:45 AM IST. Bot starts in dry-run mode."
                )
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] TOTP login error: {e}")

    elif manager._token:
        logger.info(f"[{format_ist_timestamp()}] Auth initialized with token from environment")

    else:
        logger.warning(
            f"[{format_ist_timestamp()}] No Groww credentials configured.\n"
            "  → Set in Render environment variables:\n"
            "    GROWW_EMAIL, GROWW_PASSWORD, GROWW_TOTP_SECRET, GROWW_AUTH_TOKEN\n"
            "  → Bot will run in DRY-RUN mode (no real trades) until credentials are set."
        )

    # Always return True — bot starts regardless, trades disabled without token
    return True


if __name__ == "__main__":
    # Test auth setup
    logging.basicConfig(level=logging.INFO)
    print(f"Auth test at {format_ist_timestamp()}")
    manager = get_auth_manager()
    print(f"TOTP configured: {manager._totp is not None}")
    print(f"Email configured: {bool(manager.email)}")
    if manager._totp:
        code = manager.generate_totp_code()
        remaining = manager.get_totp_time_remaining()
        print(f"TOTP code generated, expires in {remaining}s")
    token = manager.get_valid_token()
    print(f"Token available: {bool(token)}")
