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

    @retry_with_backoff(max_retries=3, delays=[2, 5, 10])
    def login_and_get_token(self) -> Optional[str]:
        """
        Perform full Groww login using email + password + TOTP.
        Returns fresh auth token or None if login fails.

        This uses the Groww API login endpoint with TOTP.
        """
        if not all([self.email, self.password, self.totp_secret]):
            logger.warning(
                f"[{format_ist_timestamp()}] Cannot auto-login: missing credentials. "
                "Using existing token from .env"
            )
            return self._token

        logger.info(f"[{format_ist_timestamp()}] Starting Groww auto-login...")

        # Wait for a fresh TOTP code (avoid using one about to expire)
        remaining = self.get_totp_time_remaining()
        if remaining < 5:
            logger.info(f"[{format_ist_timestamp()}] Waiting {remaining}s for fresh TOTP code...")
            time.sleep(remaining + 1)

        totp_code = self.generate_totp_code()
        if not totp_code:
            return self._token

        # Step 1: Login with email + password
        login_url = "https://api.groww.in/v1/login"
        login_payload = {
            "email": self.email,
            "password": self.password,
        }

        try:
            login_resp = requests.post(
                login_url,
                json=login_payload,
                headers={"Content-Type": "application/json"},
                timeout=15
            )

            if login_resp.status_code != 200:
                logger.error(
                    f"[{format_ist_timestamp()}] Groww login failed: "
                    f"HTTP {login_resp.status_code}"
                )
                return self._token

            login_data = login_resp.json()

            # Step 2: Submit TOTP for 2FA verification
            # Get session token from login response
            session_token = login_data.get("session_token") or login_data.get("token")

            if session_token:
                totp_url = "https://api.groww.in/v1/login/verify-otp"
                totp_payload = {
                    "session_token": session_token,
                    "otp": totp_code,
                    "otp_type": "TOTP"
                }
                totp_resp = requests.post(
                    totp_url,
                    json=totp_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=15
                )

                if totp_resp.status_code == 200:
                    token_data = totp_resp.json()
                    new_token = (
                        token_data.get("auth_token") or
                        token_data.get("access_token") or
                        token_data.get("token")
                    )

                    if new_token:
                        self._token = new_token
                        self._token_timestamp = get_current_ist_time()
                        logger.info(
                            f"[{format_ist_timestamp()}] ✅ Groww token refreshed via TOTP. "
                            f"Valid from: {format_ist_timestamp(self._token_timestamp)}"
                        )
                        return new_token
                    else:
                        logger.warning(
                            f"[{format_ist_timestamp()}] TOTP verified but no token in response. "
                            f"Response keys: {list(token_data.keys())}"
                        )
                else:
                    logger.error(
                        f"[{format_ist_timestamp()}] TOTP verification failed: "
                        f"HTTP {totp_resp.status_code}"
                    )
            else:
                # Some Groww API versions return token directly on password login
                direct_token = login_data.get("auth_token") or login_data.get("access_token")
                if direct_token:
                    self._token = direct_token
                    self._token_timestamp = get_current_ist_time()
                    logger.info(f"[{format_ist_timestamp()}] ✅ Groww token from direct login")
                    return direct_token

        except requests.exceptions.RequestException as e:
            logger.error(f"[{format_ist_timestamp()}] Groww login request failed: {e}")

        logger.warning(
            f"[{format_ist_timestamp()}] Auto-login failed. Using existing .env token."
        )
        return self._token

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
