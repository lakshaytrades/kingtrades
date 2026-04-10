"""
auth_groww.py — NSE Momentum Groww AI Bot
TOTP-Based Automatic Daily Authentication for Groww

Flow:
1. Visit groww.in homepage → get cookies (csrftoken, etc.)
2. POST /v1/api/user/v2/login/password with loginId+password → get requestId
3. POST /v1/api/user/v2/login/validate with requestId+TOTP → get auth token
4. Token stored in memory + cached to disk (survives restarts)
5. Auto-refreshes at 8:45 AM IST daily

If TOTP fails → falls back to GROWW_AUTH_TOKEN from env.
"""

import json
import logging
import os
import re
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

# ── Browser-like headers Groww expects ──────────────────────────────────────
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type":    "application/json",
    "Origin":          "https://groww.in",
    "Referer":         "https://groww.in/login",
    "sec-ch-ua":       '"Chromium";v="124","Google Chrome";v="124"',
    "sec-ch-ua-mobile":"?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":  "empty",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-origin",
}


# ────────────────────────────────────────────────────────────────────────────
# Token cache (disk)
# ────────────────────────────────────────────────────────────────────────────

def _save_token_cache(token: str, timestamp: datetime) -> None:
    try:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE_FILE.write_text(json.dumps({
            "token": token,
            "timestamp": timestamp.isoformat(),
        }))
    except Exception as e:
        logger.debug(f"Token cache write failed: {e}")


def _load_token_cache() -> Tuple[Optional[str], Optional[datetime]]:
    try:
        if not _TOKEN_CACHE_FILE.exists():
            return None, None
        data = json.loads(_TOKEN_CACHE_FILE.read_text())
        token = data.get("token", "")
        ts_str = data.get("timestamp", "")
        if not token or not ts_str:
            return None, None
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        age_h = (datetime.now(IST) - ts).total_seconds() / 3600
        if age_h < 23:
            logger.info(
                f"[{format_ist_timestamp()}] Loaded cached token "
                f"(age {age_h:.1f}h, valid for {23 - age_h:.1f}h more)"
            )
            return token, ts
        logger.info(f"[{format_ist_timestamp()}] Cached token expired ({age_h:.1f}h) — will re-login")
    except Exception as e:
        logger.debug(f"Token cache read failed: {e}")
    return None, None


# ────────────────────────────────────────────────────────────────────────────
# Core TOTP login  (Groww v2 flow)
# ────────────────────────────────────────────────────────────────────────────

def _extract_token(resp_json: dict, cookies: dict) -> Optional[str]:
    """
    Groww returns the auth token in various places depending on API version.
    Try all known locations.
    """
    # 1. Response body — common key names
    for key in ("token", "authToken", "auth_token", "access_token",
                "jwtToken", "jwt_token", "userToken", "user_token"):
        val = resp_json.get(key) or (resp_json.get("data") or {}).get(key)
        if val and isinstance(val, str) and len(val) > 20:
            return val

    # 2. Nested under "data" → "user" or "userInfo"
    data = resp_json.get("data") or {}
    for subkey in ("user", "userInfo", "userData"):
        sub = data.get(subkey) or {}
        for key in ("token", "authToken", "jwtToken"):
            if sub.get(key) and len(str(sub[key])) > 20:
                return str(sub[key])

    # 3. Cookies
    for name in ("user-token", "authToken", "auth-token", "token",
                 "userToken", "jwtToken", "groww-token"):
        val = cookies.get(name, "")
        if val and len(val) > 20:
            return val

    return None


def _groww_cloud_token_refresh(totp_secret: str, current_token: str) -> Optional[str]:
    """
    Groww Cloud API token refresh using TOTP.
    Keys reset daily at 6 AM IST. This runs at 6:05 AM to get a fresh token.

    The Groww Cloud API uses the TOTP secret to generate a 6-digit code,
    which is submitted to get a fresh JWT access token.
    """
    totp = pyotp.TOTP(totp_secret)

    # Wait for a fresh TOTP window
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 8:
        logger.info(f"[{format_ist_timestamp()}] Waiting {remaining + 1}s for fresh TOTP window...")
        time.sleep(remaining + 1)
    totp_code = totp.now()
    logger.info(f"[{format_ist_timestamp()}] Groww Cloud TOTP code ready ({30 - (int(time.time()) % 30)}s remaining)")

    session = requests.Session()
    session.headers.update(_HEADERS)

    # Groww Cloud API token refresh endpoints
    attempts = [
        {
            "url":  "https://api.groww.in/v1/login/totp",
            "body": {"totp": totp_code},
            "auth": f"Bearer {current_token}",
        },
        {
            "url":  "https://api.groww.in/v1/login",
            "body": {"totp": totp_code},
            "auth": f"Bearer {current_token}",
        },
        {
            "url":  "https://api.groww.in/v1/auth/token",
            "body": {"totp": totp_code},
            "auth": f"Bearer {current_token}",
        },
        {
            "url":  "https://api.groww.in/v1/login/token/generate",
            "body": {"totpCode": totp_code},
            "auth": f"Bearer {current_token}",
        },
        {
            "url":  "https://groww.in/v1/api/login/totp/token",
            "body": {"totp": totp_code},
            "auth": f"Bearer {current_token}",
        },
    ]

    for attempt in attempts:
        try:
            headers = dict(_HEADERS)
            headers["Authorization"] = attempt["auth"]
            r = session.post(
                attempt["url"],
                json=attempt["body"],
                headers=headers,
                timeout=20,
            )
            logger.warning(
                f"  [cloud-refresh] {attempt['url'].split('/')[-1]} "
                f"HTTP {r.status_code} | body={r.text[:300]}"
            )
            if r.status_code in (200, 201):
                d = r.json()
                tok = _extract_token(d, dict(r.cookies))
                if tok:
                    logger.info(f"[{format_ist_timestamp()}] ✅ Groww Cloud token refreshed via {attempt['url'].split('/')[-1]}")
                    return tok
        except Exception as e:
            logger.warning(f"  [cloud-refresh] {attempt['url'].split('/')[-1]}: {e}")

    return None


def _log_response(label: str, resp: requests.Response) -> dict:
    """Log HTTP response prominently so it shows in Render logs."""
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:300]}
    cookies = {k: v[:20] + "..." if len(v) > 20 else v
               for k, v in resp.cookies.items()}
    logger.warning(
        f"  [{label}] HTTP {resp.status_code} | "
        f"body_keys={list(body.keys())} | "
        f"cookies={list(cookies.keys())} | "
        f"body_preview={str(body)[:400]}"
    )
    return body


def _groww_totp_login(email: str, password: str, totp_secret: str) -> Optional[str]:
    """
    Full Groww TOTP login with detailed response logging.
    All HTTP responses are logged at WARNING level so they appear in Render logs.
    """
    totp = pyotp.TOTP(totp_secret)

    # Wait for TOTP code with ≥ 8 seconds of life left
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 8:
        logger.info(f"[{format_ist_timestamp()}] Waiting {remaining + 1}s for fresh TOTP window...")
        time.sleep(remaining + 1)
    totp_code = totp.now()
    logger.warning(f"  TOTP code generated, window has {30 - (int(time.time()) % 30)}s remaining")

    session = requests.Session()
    session.headers.update(_HEADERS)

    # Step 0: visit login page to collect cookies
    try:
        home = session.get("https://groww.in/login", timeout=15)
        logger.warning(
            f"  [homepage] HTTP {home.status_code} | "
            f"cookies={list(session.cookies.keys())}"
        )
    except Exception as e:
        logger.warning(f"  [homepage] failed (non-fatal): {e}")

    csrf = session.cookies.get("csrftoken", "")
    if csrf:
        session.headers["x-csrftoken"] = csrf

    # ═══════════════════════════════════════════════════════════════════════
    # ATTEMPT 1 — v2 loginId + /validate
    # ═══════════════════════════════════════════════════════════════════════
    logger.info(f"[{format_ist_timestamp()}] TOTP attempt 1/3 — v2 loginId+validate...")
    try:
        r1 = session.post(
            "https://groww.in/v1/api/user/v2/login/password",
            json={"loginId": email, "password": password},
            timeout=20,
        )
        d1 = _log_response("attempt1 step1", r1)

        if r1.status_code in (200, 201):
            direct = _extract_token(d1, dict(session.cookies))
            if direct:
                logger.info(f"[{format_ist_timestamp()}] ✅ Token from password step")
                return direct

            request_id = (
                (d1.get("data") or {}).get("requestId") or d1.get("requestId") or
                (d1.get("data") or {}).get("request_id") or d1.get("request_id")
            )
            logger.warning(f"  requestId found: {bool(request_id)} — value: {request_id}")

            if request_id:
                if 30 - (int(time.time()) % 30) < 3:
                    time.sleep(4)
                    totp_code = totp.now()

                for validate_url, body in [
                    ("https://groww.in/v1/api/user/v2/login/validate",
                     {"requestId": request_id, "otp": totp_code, "otpType": "TOTP"}),
                    ("https://groww.in/v1/api/user/v2/login/validate/totp",
                     {"requestId": request_id, "totp": totp_code}),
                    ("https://groww.in/v1/api/user/v2/login/totp",
                     {"requestId": request_id, "otp": totp_code, "otpType": "TOTP"}),
                ]:
                    r2 = session.post(validate_url, json=body, timeout=20)
                    d2 = _log_response(f"attempt1 {validate_url.split('/')[-1]}", r2)
                    if r2.status_code in (200, 201):
                        tok = _extract_token(d2, dict(session.cookies))
                        if tok:
                            logger.info(f"[{format_ist_timestamp()}] ✅ TOTP login via v2 loginId")
                            return tok
    except Exception as e:
        logger.warning(f"  attempt 1 exception: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # ATTEMPT 2 — v2 email key
    # ═══════════════════════════════════════════════════════════════════════
    logger.info(f"[{format_ist_timestamp()}] TOTP attempt 2/3 — v2 email+validate...")
    try:
        s2 = requests.Session()
        s2.headers.update(_HEADERS)
        try:
            s2.get("https://groww.in/login", timeout=10)
        except Exception:
            pass

        r1 = s2.post(
            "https://groww.in/v1/api/user/v2/login/password",
            json={"email": email, "password": password},
            timeout=20,
        )
        d1 = _log_response("attempt2 step1", r1)

        if r1.status_code in (200, 201):
            request_id = (d1.get("data") or {}).get("requestId") or d1.get("requestId")
            if request_id:
                if 30 - (int(time.time()) % 30) < 3:
                    time.sleep(4)
                    totp_code = totp.now()
                r2 = s2.post(
                    "https://groww.in/v1/api/user/v2/login/validate",
                    json={"requestId": request_id, "otp": totp_code, "otpType": "TOTP"},
                    timeout=20,
                )
                d2 = _log_response("attempt2 validate", r2)
                if r2.status_code in (200, 201):
                    tok = _extract_token(d2, dict(s2.cookies))
                    if tok:
                        logger.info(f"[{format_ist_timestamp()}] ✅ TOTP login via v2 email")
                        return tok
    except Exception as e:
        logger.warning(f"  attempt 2 exception: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # ATTEMPT 3 — v1 legacy single-step
    # ═══════════════════════════════════════════════════════════════════════
    logger.info(f"[{format_ist_timestamp()}] TOTP attempt 3/3 — v1 legacy...")
    try:
        s3 = requests.Session()
        s3.headers.update(_HEADERS)
        if 30 - (int(time.time()) % 30) < 3:
            time.sleep(4)
            totp_code = totp.now()

        for url, body in [
            ("https://groww.in/v1/api/user/login",
             {"email": email, "password": password, "otp": totp_code, "otpType": "TOTP"}),
            ("https://groww.in/v1/api/user/login",
             {"loginId": email, "password": password, "totp": totp_code}),
        ]:
            r = s3.post(url, json=body, timeout=20)
            d = _log_response(f"attempt3 {body.get('otpType','v2')}", r)
            if r.status_code in (200, 201):
                tok = _extract_token(d, dict(s3.cookies))
                if tok:
                    logger.info(f"[{format_ist_timestamp()}] ✅ TOTP login via v1 legacy")
                    return tok
    except Exception as e:
        logger.warning(f"  attempt 3 exception: {e}")

    return None


# ────────────────────────────────────────────────────────────────────────────
# Auth Manager
# ────────────────────────────────────────────────────────────────────────────

class GrowwAuthManager:
    """
    Manages Groww authentication with automatic TOTP daily refresh.
    Falls back to GROWW_AUTH_TOKEN from env if TOTP fails.
    """

    def __init__(self):
        self.email        = os.getenv("GROWW_EMAIL", "")
        self.password     = os.getenv("GROWW_PASSWORD", "")
        self.totp_secret  = os.getenv("GROWW_TOTP_SECRET", "")
        self._totp        = pyotp.TOTP(self.totp_secret) if self.totp_secret else None

        # Load from cache first, then fallback to env token
        cached_token, cached_ts = _load_token_cache()
        if cached_token:
            self._token           = cached_token
            self._token_timestamp = cached_ts
        else:
            self._token           = os.getenv("GROWW_AUTH_TOKEN", "")
            self._token_timestamp = None

        if not self.totp_secret:
            logger.warning(
                f"[{format_ist_timestamp()}] GROWW_TOTP_SECRET not set — "
                "TOTP disabled, using static GROWW_AUTH_TOKEN"
            )

    def login_and_get_token(self) -> Optional[str]:
        """Run TOTP login. Falls back to env token on failure."""
        if not all([self.email, self.password, self.totp_secret]):
            logger.info(
                f"[{format_ist_timestamp()}] Credentials incomplete — "
                "using GROWW_AUTH_TOKEN from env"
            )
            return self._token or None

        logger.info(f"[{format_ist_timestamp()}] Starting Groww TOTP login...")
        try:
            token = _groww_totp_login(self.email, self.password, self.totp_secret)
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] TOTP login exception: {e}")
            token = None

        if token:
            self._token           = token
            self._token_timestamp = get_current_ist_time()
            _save_token_cache(token, self._token_timestamp)
            logger.info(f"[{format_ist_timestamp()}] ✅ Token saved (valid ~24h)")
            return token

        # All attempts failed
        # Try Groww Cloud API token refresh (correct flow for Cloud API keys)
        if self.totp_secret and self._token:
            logger.info(f"[{format_ist_timestamp()}] Trying Groww Cloud token refresh...")
            cloud_token = _groww_cloud_token_refresh(self.totp_secret, self._token)
            if cloud_token:
                self._token           = cloud_token
                self._token_timestamp = get_current_ist_time()
                _save_token_cache(cloud_token, self._token_timestamp)
                return cloud_token

        logger.warning(
            f"[{format_ist_timestamp()}] ⚠️  All TOTP attempts failed.\n"
            "  Possible causes:\n"
            "    • GROWW_EMAIL or GROWW_PASSWORD is wrong\n"
            "    • GROWW_TOTP_SECRET doesn't match your 2FA app\n"
            "    • Groww changed their login API again\n"
            "  Falling back to GROWW_AUTH_TOKEN from Render env.\n"
            "  → Run LOGIN DEBUG: python auth_groww.py  (check Render shell)"
        )
        if self._token:
            return self._token

        logger.error(
            f"[{format_ist_timestamp()}] ❌ No token available — "
            "set GROWW_AUTH_TOKEN in Render environment"
        )
        return None

    def get_valid_token(self) -> Optional[str]:
        """Return current token; trigger refresh if >23h old."""
        if self._token_timestamp:
            age_h = (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600
            if age_h > 23:
                logger.info(f"[{format_ist_timestamp()}] Token {age_h:.1f}h old — refreshing...")
                return self.login_and_get_token()
        elif not self._token:
            return self.login_and_get_token()
        else:
            logger.info(f"[{format_ist_timestamp()}] Using initial token from env/cache")

        return self._token

    def force_refresh(self) -> bool:
        """Force a fresh TOTP login regardless of token age."""
        logger.info(f"[{format_ist_timestamp()}] Forced token refresh...")
        tok = self.login_and_get_token()
        return bool(tok)

    def refresh_token_if_needed(self) -> bool:
        """Called at 8:45 AM IST. Always tries TOTP login."""
        logger.info(f"[{format_ist_timestamp()}] Morning TOTP login...")
        tok = self.login_and_get_token()
        if tok:
            logger.info(f"[{format_ist_timestamp()}] ✅ Morning token refresh complete")
            return True
        logger.error(f"[{format_ist_timestamp()}] ❌ Morning token refresh FAILED")
        return False

    @property
    def token(self) -> Optional[str]:
        return self._token

    @property
    def token_age_hours(self) -> float:
        if not self._token_timestamp:
            return float("inf")
        return (get_current_ist_time() - self._token_timestamp).total_seconds() / 3600


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
    return get_auth_manager().get_valid_token()


def initialize_auth() -> bool:
    manager = get_auth_manager()
    now_ist = get_current_ist_time()
    has_creds = all([manager.email, manager.password, manager.totp_secret])

    # Refresh if: it's between 6:00-9:00 AM IST (after Groww key reset, before market open)
    # OR if the cached token is older than 20 hours (approaching 24h expiry)
    token_age_h = manager.token_age_hours
    needs_refresh = (6 <= now_ist.hour < 9) or (token_age_h > 20)

    if has_creds and needs_refresh:
        logger.info(f"[{format_ist_timestamp()}] Running TOTP login (age={token_age_h:.1f}h)...")
        try:
            manager.refresh_token_if_needed()
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] TOTP login error: {e}")
    elif manager._token:
        logger.info(f"[{format_ist_timestamp()}] Auth ready (token from cache/env)")
    else:
        logger.warning(
            f"[{format_ist_timestamp()}] No Groww credentials — set env vars:\n"
            "  GROWW_EMAIL, GROWW_PASSWORD, GROWW_TOTP_SECRET, GROWW_AUTH_TOKEN"
        )
    return True


# ────────────────────────────────────────────────────────────────────────────
# Standalone test — run: python auth_groww.py
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    print("\n" + "="*60)
    print("Groww TOTP Login Debug")
    print("="*60)

    email       = os.getenv("GROWW_EMAIL", "")
    password    = os.getenv("GROWW_PASSWORD", "")
    totp_secret = os.getenv("GROWW_TOTP_SECRET", "")

    print(f"Email     : {'SET (' + email[:4] + '...)' if email else 'NOT SET'}")
    print(f"Password  : {'SET' if password else 'NOT SET'}")
    print(f"TOTP      : {'SET (' + totp_secret[:4] + '...)' if totp_secret else 'NOT SET'}")

    if totp_secret:
        t = pyotp.TOTP(totp_secret)
        remaining = 30 - (int(time.time()) % 30)
        print(f"TOTP code : {t.now()} (expires in {remaining}s)")

    if not all([email, password, totp_secret]):
        print("\n❌ Missing credentials — set GROWW_EMAIL, GROWW_PASSWORD, GROWW_TOTP_SECRET")
        sys.exit(1)

    print("\nAttempting TOTP login (DEBUG mode — all responses shown)...")
    token = _groww_totp_login(email, password, totp_secret)

    print("\n" + "="*60)
    if token:
        print(f"✅ SUCCESS — Token obtained")
        print(f"   Length  : {len(token)} chars")
        print(f"   Preview : {token[:12]}...{token[-6:]}")
        print(f"\nCopy this into Render → Environment → GROWW_AUTH_TOKEN")
    else:
        print("❌ FAILED — Check DEBUG logs above to see HTTP response details")
        print("   Share the log output to diagnose the exact failure point")
    print("="*60)
