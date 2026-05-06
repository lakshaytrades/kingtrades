"""
auth_groww.py — Fully Autonomous Groww Authentication
Zero manual intervention. Bot handles everything by itself.

Auth flow (tried in order):
  1. Groww Trade API  → POST /v1/token/api/access  with Cloud API Key + TOTP
  2. growwapi SDK     → GrowwAPI.get_access_token(api_key, totp)
  3. Two-step web     → POST /login/password → POST /login/verify-otp
  4. One-step web     → POST various endpoints with email+password+OTP

Required .env:
  GROWW_CLIENT_ID   = Cloud API Key from groww.in → Trade API → Cloud API Keys
                      (UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
  GROWW_TOTP_SECRET = TOTP secret for that API Key (NOT your account login TOTP)
  GROWW_EMAIL       = your Groww email (backup method)
  GROWW_PASSWORD    = your Groww password (backup method)
"""

import base64
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
# JWT / response helpers
# ─────────────────────────────────────────────────────────────────────────────

def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def _extract_vendor_key_from_jwt(token: str) -> Optional[str]:
    try:
        payload = _decode_jwt_payload(token)
        sub = payload.get("sub", "")
        if isinstance(sub, str) and sub.startswith("{"):
            key = json.loads(sub).get("vendorIntegrationKey", "")
            if key and len(key) > 8:
                return key
        key = payload.get("vendorIntegrationKey", "")
        if key and len(key) > 8:
            return key
    except Exception:
        pass
    return None


def _extract_token_from_response(body: dict) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    data = body.get("data") or {}
    for k in ("token", "authToken", "auth_token", "access_token",
              "jwtToken", "jwt_token", "userToken", "user_token",
              "accessToken", "Authorization"):
        for src in (body, data):
            val = src.get(k, "")
            if val and isinstance(val, str) and len(val) > 20:
                return val.removeprefix("Bearer ")
    return None


def _looks_like_vendor_key(s: str) -> bool:
    return bool(s) and len(s) < 64 and "." not in s


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
        vk  = data.get("vendor_key", "") or ""
        tok = data.get("access_token", "") or None
        ts  = data.get("timestamp", "")
        if not ts:
            return vk, tok, None
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return vk, tok, dt
    except Exception:
        pass
    return "", None, None


# ─────────────────────────────────────────────────────────────────────────────
# 6 AM IST reset check
# ─────────────────────────────────────────────────────────────────────────────

def _is_expired_by_6am_reset(token_ts: Optional[datetime]) -> bool:
    if token_ts is None:
        return True
    now = get_current_ist_time()
    if token_ts.tzinfo is None:
        token_ts = token_ts.replace(tzinfo=IST)
    reset_today = now.replace(hour=6, minute=0, second=0, microsecond=0)
    return token_ts < reset_today and now >= reset_today


# ─────────────────────────────────────────────────────────────────────────────
# SDK diagnostic (runs once per process)
# ─────────────────────────────────────────────────────────────────────────────

_sdk_inspected = False


def _inspect_sdk_once():
    global _sdk_inspected
    if _sdk_inspected:
        return
    _sdk_inspected = True
    try:
        import importlib.util
        import pathlib
        import inspect
        import io

        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            from growwapi import GrowwAPI
        finally:
            sys.stdout = old

        methods = [m for m in dir(GrowwAPI) if not m.startswith("__")]
        logger.info(f"[SDK] methods: {methods}")

        for name in methods:
            fn = getattr(GrowwAPI, name, None)
            if callable(fn):
                try:
                    sig = str(inspect.signature(fn))
                    logger.info(f"[SDK] {name}{sig}")
                except Exception:
                    pass

        spec = importlib.util.find_spec("growwapi")
        if spec and spec.origin:
            pkg_dir = pathlib.Path(spec.origin).parent
            logger.info(f"[SDK] package dir: {pkg_dir}")
            for py_file in sorted(pkg_dir.rglob("*.py")):
                try:
                    content = py_file.read_text(errors="replace")
                    logger.info(f"[SDK FILE] {py_file.name} ({len(content)} bytes):\n{content}")
                except Exception as e:
                    logger.info(f"[SDK FILE] {py_file.name}: read error {e}")

    except Exception as e:
        logger.info(f"[SDK] inspect failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Core: single TOTP attempt — tries all known Groww auth methods
# ─────────────────────────────────────────────────────────────────────────────

def _single_totp_attempt(vendor_key: str, totp_secret: str,
                          attempt_label: str = "") -> Optional[str]:
    import hashlib
    _inspect_sdk_once()

    remaining = 30 - (int(time.time()) % 30)
    if remaining < 5:
        logger.debug(f"Waiting {remaining + 1}s for fresh TOTP window...")
        time.sleep(remaining + 1)

    totp_code   = pyotp.TOTP(totp_secret).now()
    window_left = 30 - (int(time.time()) % 30)
    label       = f"[{attempt_label}] " if attempt_label else ""

    raw_token     = os.getenv("GROWW_AUTH_TOKEN", "")
    email         = os.getenv("GROWW_EMAIL", "")
    password      = os.getenv("GROWW_PASSWORD", "")
    client_id     = os.getenv("GROWW_CLIENT_ID", "")
    client_secret = os.getenv("GROWW_CLIENT_SECRET", "")

    # Priority: GROWW_CLIENT_ID (Cloud API Key) → vendor key → raw JWT token
    keys_to_try = []
    if client_id:
        keys_to_try.append(("client_id", client_id))
    if vendor_key and vendor_key != client_id:
        keys_to_try.append(("vendor_key", vendor_key))
    if raw_token and raw_token not in (vendor_key, client_id):
        keys_to_try.append(("raw_auth_token", raw_token))

    logger.info(
        f"[{format_ist_timestamp()}] {label}TOTP={totp_code} window={window_left}s "
        f"| keys={[k for k,_ in keys_to_try]} email={'yes' if email else 'no'}"
    )

    ts = int(time.time())

    # ── Method 1: Groww Trade API endpoint ────────────────────────────────
    # POST https://api.groww.in/v1/token/api/access
    # Authorization: Bearer <Cloud-API-Key>  (UUID format from Trade API portal)
    # Body: {"totp": "123456"}
    for key_label, key_val in keys_to_try:
        base_hdrs = {
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "Authorization": f"Bearer {key_val}",
            "User-Agent":    "growwapi-python/1.5.0",
        }
        bodies_to_try = [
            {"totp": totp_code},
            {"totp": str(totp_code)},
            {"otp": totp_code},
        ]
        if client_secret:
            checksum = hashlib.sha256(f"{client_secret}{ts}".encode()).hexdigest()
            bodies_to_try.append({
                "key_type": "approval",
                "checksum": checksum,
                "timestamp": ts,
            })
        for body in bodies_to_try:
            try:
                resp = requests.post(
                    "https://api.groww.in/v1/token/api/access",
                    json=body, headers=base_hdrs, timeout=15,
                )
                logger.info(
                    f"  [Trade-API/token] ({key_label}) HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
                if resp.status_code in (200, 201):
                    tok = _extract_token_from_response(resp.json())
                    if tok:
                        logger.info(f"[{format_ist_timestamp()}] Token via Trade API ({key_label})")
                        return tok
            except Exception as e:
                logger.info(f"  [Trade-API/token] ({key_label}): {e}")

    # ── Method 2: growwapi SDK ─────────────────────────────────────────────
    try:
        import io
        from growwapi import GrowwAPI

        def _quiet_groww(key_val):
            buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
            try:
                return GrowwAPI(key_val)
            finally:
                sys.stdout = old

        for key_label, key_val in keys_to_try:
            try:
                result = GrowwAPI.get_access_token(api_key=key_val, totp=totp_code)
                logger.info(f"  SDK.get_access_token({key_label}): {str(result)[:100]}")
                if isinstance(result, str) and len(result) > 20:
                    logger.info(f"[{format_ist_timestamp()}] Token via SDK ({key_label})")
                    return result
                if isinstance(result, dict):
                    tok = _extract_token_from_response(result)
                    if tok:
                        return tok
            except Exception as e:
                logger.info(f"  SDK.get_access_token({key_label}): {e}")

            for method_name in ("get_access_token", "refresh_token", "generate_session"):
                try:
                    obj = _quiet_groww(key_val)
                    fn  = getattr(obj, method_name, None)
                    if not fn:
                        continue
                    r = fn(totp=totp_code)
                    if isinstance(r, str) and len(r) > 20:
                        logger.info(f"[{format_ist_timestamp()}] Token via SDK.{method_name}() ({key_label})")
                        return r
                except Exception as e:
                    logger.info(f"  SDK.{method_name}({key_label}): {e}")

        if email and password:
            for key_label, key_val in [("client_id", client_id), ("vendor_key", vendor_key)]:
                if not key_val:
                    continue
                try:
                    obj = _quiet_groww(key_val)
                    for mname in ("login", "authenticate", "login_with_totp"):
                        fn = getattr(obj, mname, None)
                        if not fn:
                            continue
                        try:
                            r = fn(email=email, password=password, totp=totp_code)
                            if isinstance(r, str) and len(r) > 20:
                                logger.info(f"[{format_ist_timestamp()}] Token via SDK.{mname}()")
                                return r
                            if isinstance(r, dict):
                                tok = _extract_token_from_response(r)
                                if tok:
                                    return tok
                        except Exception as e:
                            logger.info(f"  SDK.{mname}({key_label}): {e}")
                except Exception as e:
                    logger.info(f"  SDK email+pwd ({key_label}): {e}")

    except ImportError:
        logger.info("growwapi SDK not installed — skipping SDK methods")
    except Exception as e:
        logger.info(f"SDK outer error: {e}")

    # ── Method 3: Two-step web login ──────────────────────────────────────
    # Step 1: POST email+password → get sessionToken
    # Step 2: POST sessionToken+OTP → get auth token
    # This mimics the Groww mobile app login flow.
    if email and password:
        mobile_hdrs = {
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "okhttp/4.9.0",
            "x-app-version": "10.2.0",
            "x-platform":    "android",
            "Origin":        "https://groww.in",
            "Referer":       "https://groww.in/login",
        }

        session = requests.Session()
        session.headers.update(mobile_hdrs)

        # Step 1 candidates — password endpoints
        step1_attempts = [
            ("https://groww.in/v1/api/login/password",
             {"loginId": email, "password": password}),
            ("https://groww.in/v1/api/login_password",
             {"loginId": email, "password": password}),
            ("https://groww.in/v1/api/user/login",
             {"loginId": email, "password": password}),
            ("https://api.groww.in/v1/user/login",
             {"email": email, "password": password}),
            ("https://api.groww.in/v1/auth/login",
             {"email": email, "password": password}),
        ]

        for s1_url, s1_body in step1_attempts:
            ep1 = s1_url.rsplit("/", 1)[-1]
            try:
                r1 = session.post(s1_url, json=s1_body, timeout=15)
                logger.info(f"  [step1/{ep1}] HTTP {r1.status_code}: {r1.text[:200]}")
                if r1.status_code not in (200, 201):
                    continue
                try:
                    j1 = r1.json()
                except Exception:
                    continue

                # Check if we already got a full token in step 1
                tok = _extract_token_from_response(j1)
                if tok and len(tok) > 50:
                    logger.info(f"[{format_ist_timestamp()}] Token via single-step web [{ep1}]")
                    return tok

                # Look for session token to use in step 2
                data1 = j1.get("data") or {}
                session_token = (
                    data1.get("sessionToken") or
                    data1.get("session_token") or
                    data1.get("tempToken") or
                    data1.get("temp_token") or
                    j1.get("sessionToken") or
                    j1.get("tempToken") or
                    ""
                )
                otp_required = (
                    data1.get("otpRequired") or
                    data1.get("otp_required") or
                    j1.get("otpRequired") or
                    bool(session_token)
                )

                if session_token or otp_required:
                    # Step 2: verify OTP
                    step2_attempts = [
                        ("https://groww.in/v1/api/login/verify-otp",
                         {"sessionToken": session_token, "otp": totp_code}),
                        ("https://groww.in/v1/api/login/verify-otp",
                         {"sessionToken": session_token, "totp": totp_code}),
                        ("https://groww.in/v1/api/login/otp",
                         {"sessionToken": session_token, "otp": totp_code}),
                        ("https://groww.in/v1/api/user/verify-otp",
                         {"sessionToken": session_token, "otp": totp_code}),
                        ("https://groww.in/v1/api/login_otp",
                         {"sessionToken": session_token, "otp": totp_code}),
                    ]
                    for s2_url, s2_body in step2_attempts:
                        ep2 = s2_url.rsplit("/", 1)[-1]
                        try:
                            r2 = session.post(s2_url, json=s2_body, timeout=15)
                            logger.info(f"  [step2/{ep2}] HTTP {r2.status_code}: {r2.text[:200]}")
                            if r2.status_code in (200, 201):
                                try:
                                    j2 = r2.json()
                                    tok = _extract_token_from_response(j2)
                                    if tok:
                                        logger.info(f"[{format_ist_timestamp()}] Token via two-step web [{ep1}→{ep2}]")
                                        return tok
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.info(f"  [step2/{ep2}]: {e}")

            except Exception as e:
                logger.info(f"  [step1/{ep1}]: {e}")

        # ── Method 4: Single-step web endpoints with inline OTP ───────────
        web_hdrs = dict(mobile_hdrs)
        web_hdrs["User-Agent"] = (
            "Mozilla/5.0 (Linux; Android 13; SM-G991B) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/112.0.0.0 Mobile Safari/537.36"
        )
        web_attempts = [
            ("https://groww.in/v1/api/login_password_otp",
             {"loginId": email, "password": password, "otp": str(totp_code)}, mobile_hdrs),
            ("https://groww.in/v1/api/login_password_otp",
             {"loginId": email, "password": password, "otp": str(totp_code)}, web_hdrs),
            ("https://groww.in/v1/api/login",
             {"loginId": email, "password": password, "otp": str(totp_code)}, mobile_hdrs),
            ("https://groww.in/v1/api/login",
             {"email": email, "password": password, "totp": str(totp_code)}, web_hdrs),
            ("https://api.groww.in/v1/accounts/login",
             {"email": email, "password": password, "totp": str(totp_code)}, web_hdrs),
            ("https://api.groww.in/v1/auth/login",
             {"loginId": email, "password": password, "otp": str(totp_code)}, web_hdrs),
        ]
        for url, body, hdrs in web_attempts:
            ep = url.rsplit("/", 1)[-1]
            try:
                resp = session.post(url, json=body, headers=hdrs, timeout=15)
                logger.info(f"  [web/{ep}] HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code in (200, 201):
                    try:
                        j = resp.json()
                        tok = _extract_token_from_response(j)
                        if tok:
                            logger.info(f"[{format_ist_timestamp()}] Token via web [{ep}]")
                            return tok
                        logger.info(f"  [web/{ep}] 200 but no token. keys={list(j.keys()) if isinstance(j, dict) else type(j)}")
                    except Exception as je:
                        logger.info(f"  [web/{ep}] JSON parse: {je}")
            except Exception as e:
                logger.info(f"  [web/{ep}]: {e}")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Auth Manager
# ─────────────────────────────────────────────────────────────────────────────

class GrowwAuthManager:
    """
    Fully autonomous Groww authentication.
    Retries TOTP login automatically — zero manual action needed.
    """

    def __init__(self):
        self.totp_secret = os.getenv("GROWW_TOTP_SECRET", "")
        self._vendor_key:      str               = ""
        self._token:           Optional[str]     = None
        self._token_timestamp: Optional[datetime] = None
        self._attempt_count:   int               = 0
        self._retry_alert_date: str              = ""

        cached_vk, cached_tok, cached_ts = _load_token_cache()

        self._vendor_key = (
            os.getenv("GROWW_VENDOR_KEY", "")
            or cached_vk
            or self._bootstrap_vendor_key()
        )

        if cached_tok and not _is_expired_by_6am_reset(cached_ts):
            self._token           = cached_tok
            self._token_timestamp = cached_ts
            logger.info(f"[{format_ist_timestamp()}] Cached token valid")

        if self._vendor_key:
            logger.info(
                f"[{format_ist_timestamp()}] Vendor key: "
                f"{self._vendor_key[:8]}... | TOTP: "
                f"{'set' if self.totp_secret else 'MISSING'}"
            )
        else:
            logger.warning(
                f"[{format_ist_timestamp()}] No vendor key found. "
                "Set GROWW_CLIENT_ID or GROWW_VENDOR_KEY in .env"
            )

        if not self.totp_secret:
            logger.error(
                f"[{format_ist_timestamp()}] GROWW_TOTP_SECRET not set in .env"
            )

    def _bootstrap_vendor_key(self) -> str:
        raw = os.getenv("GROWW_AUTH_TOKEN", "")
        if not raw:
            return ""
        extracted = _extract_vendor_key_from_jwt(raw)
        if extracted:
            logger.info(
                f"[{format_ist_timestamp()}] Vendor key from GROWW_AUTH_TOKEN: {extracted[:8]}..."
            )
            return extracted
        if _looks_like_vendor_key(raw):
            logger.info(
                f"[{format_ist_timestamp()}] Using GROWW_AUTH_TOKEN as vendor key: {raw[:8]}..."
            )
            return raw
        return ""

    def refresh_token_if_needed(self) -> bool:
        if not self.totp_secret:
            logger.error(f"[{format_ist_timestamp()}] Cannot refresh — GROWW_TOTP_SECRET missing")
            return False

        # Groww API maintenance window: 2–7 AM IST (servers offline)
        now_ist = get_current_ist_time()
        if 2 <= now_ist.hour < 7:
            logger.info(
                f"[{format_ist_timestamp()}] Skipping TOTP — Groww maintenance (2–7 AM IST)"
            )
            return False

        for attempt in range(1, 7):
            self._attempt_count += 1
            tok = _single_totp_attempt(
                self._vendor_key, self.totp_secret,
                attempt_label=f"{attempt}/6"
            )
            if tok:
                self._token           = tok
                self._token_timestamp = get_current_ist_time()
                fresh_vk = _extract_vendor_key_from_jwt(tok)
                if fresh_vk:
                    self._vendor_key = fresh_vk
                _save_token_cache(self._vendor_key, tok, self._token_timestamp)
                logger.info(
                    f"[{format_ist_timestamp()}] Groww login OK "
                    f"(attempt {attempt}/6, total #{self._attempt_count})"
                )
                return True

            if attempt < 6:
                wait = 30 - (int(time.time()) % 30) + 2
                logger.warning(
                    f"[{format_ist_timestamp()}] Attempt {attempt}/6 failed — "
                    f"next TOTP window in {wait}s..."
                )
                time.sleep(wait)

        logger.error(
            f"[{format_ist_timestamp()}] All 6 TOTP attempts failed. Auto-retry in 5 min."
        )

        today = get_current_ist_time().strftime("%Y-%m-%d")
        if self._retry_alert_date != today:
            self._retry_alert_date = today
            has_client_id = bool(os.getenv("GROWW_CLIENT_ID", ""))
            if not has_client_id:
                self._send_alert(
                    "⚠️ <b>Groww Login Failed — Fix Needed</b>\n\n"
                    "<b>Missing:</b> GROWW_CLIENT_ID (Cloud API Key)\n\n"
                    "<b>Get it (2 min):</b>\n"
                    "1. Open Groww app → Profile → Trade API → Cloud API Keys\n"
                    "2. Your API Key looks like: <code>xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx</code>\n"
                    "3. On VPS run:\n"
                    "<code>echo 'GROWW_CLIENT_ID=paste_uuid_here' >> /opt/kingtrades/.env\n"
                    "systemctl restart kingtrades</code>\n\n"
                    "<i>Retrying every 5 min until 4 PM IST</i>"
                )
            else:
                self._send_alert(
                    "⚠️ <b>Groww Login Failing — API Key Mismatch</b>\n\n"
                    "TOTP codes generate correctly but Groww rejects them.\n\n"
                    "<b>Most likely cause:</b>\n"
                    "GROWW_CLIENT_ID is wrong format. It must be the UUID from\n"
                    "groww.in → Trade API → Cloud API Keys\n"
                    "(looks like: <code>xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx</code>)\n\n"
                    "<b>Also check:</b> GROWW_TOTP_SECRET must be the TOTP secret\n"
                    "shown on THAT API Key page — NOT your account login TOTP.\n\n"
                    "On VPS check your .env:\n"
                    "<code>grep GROWW /opt/kingtrades/.env</code>\n\n"
                    "<i>Retrying every 5 min until 4 PM IST</i>"
                )
        return False

    def get_valid_token(self) -> Optional[str]:
        try:
            if self._token and not _is_expired_by_6am_reset(self._token_timestamp):
                return self._token

            now_ist = get_current_ist_time()

            # Only attempt TOTP refresh during trading hours (7 AM – 5 PM IST)
            if not (7 <= now_ist.hour < 17):
                return self._token

            reason = "Token expired (6 AM reset)" if self._token else "No token"
            logger.info(f"[{format_ist_timestamp()}] {reason} — refreshing via TOTP...")
            self.refresh_token_if_needed()
            return self._token
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] get_valid_token error: {e}")
            return self._token

    def set_token_manually(self, token: str) -> bool:
        """Set token manually (e.g. via Telegram /token command)."""
        if not token or len(token) < 20:
            return False
        self._token           = token
        self._token_timestamp = get_current_ist_time()
        fresh_vk = _extract_vendor_key_from_jwt(token)
        if fresh_vk:
            self._vendor_key = fresh_vk
        _save_token_cache(self._vendor_key or "", token, self._token_timestamp)
        logger.info(f"[{format_ist_timestamp()}] Token set manually ({len(token)} chars)")
        return True

    def force_refresh(self) -> bool:
        logger.info(f"[{format_ist_timestamp()}] Force refresh requested...")
        return self.refresh_token_if_needed()

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

    def _send_alert(self, text: str) -> None:
        try:
            import config
            if not getattr(config, "TELEGRAM_BOT_TOKEN", "") or \
               not getattr(config, "TELEGRAM_CHAT_ID", ""):
                return
            requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "parse_mode": "HTML", "text": text},
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
    return get_auth_manager().get_valid_token()


def initialize_auth() -> bool:
    manager = get_auth_manager()
    expired = _is_expired_by_6am_reset(manager._token_timestamp)
    if expired:
        logger.info(f"[{format_ist_timestamp()}] Auth init: token expired — will refresh at 8:30 AM IST")
    else:
        logger.info(f"[{format_ist_timestamp()}] Auth init: token valid")
    return bool(manager.totp_secret)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli_extract_key():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    jwt = os.getenv("GROWW_AUTH_TOKEN", "")
    if not jwt:
        print("GROWW_AUTH_TOKEN not set in .env")
        sys.exit(1)
    key = _extract_vendor_key_from_jwt(jwt)
    if not key and _looks_like_vendor_key(jwt):
        key = jwt
    if not key:
        print("Could not extract vendor key from GROWW_AUTH_TOKEN")
        sys.exit(1)
    print(f"\nVendor key: {key}\n")
    env = Path(".env")
    if env.exists() and "GROWW_VENDOR_KEY" not in env.read_text():
        env.write_text(env.read_text().rstrip() + f"\nGROWW_VENDOR_KEY={key}\n")
        print(f"Auto-added GROWW_VENDOR_KEY to {env.resolve()}")


def _cli_test():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    manager = GrowwAuthManager()
    client_id = os.getenv("GROWW_CLIENT_ID", "")
    print(f"\nclient_id:   {'set → ' + client_id[:12] + '...' if client_id else 'MISSING — add to .env'}")
    print(f"vendor_key:  {'set → ' + manager._vendor_key[:12] + '...' if manager._vendor_key else 'not set'}")
    print(f"totp_secret: {'set' if manager.totp_secret else 'MISSING'}")
    print(f"cached token: {'valid' if manager._token else 'none'}\n")
    tok = manager.get_valid_token()
    if tok:
        print(f"Token: {tok[:40]}...{tok[-10:]}")
    else:
        print("Failed — check .env and try again at 8:30 AM IST")
        sys.exit(1)


if __name__ == "__main__":
    if "--extract-key" in sys.argv:
        _cli_extract_key()
    else:
        _cli_test()
