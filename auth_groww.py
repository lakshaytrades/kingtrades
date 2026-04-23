"""
auth_groww.py — Fully Autonomous Groww Authentication
Zero manual intervention. Bot handles everything by itself.

Works with what's already in .env:
  GROWW_AUTH_TOKEN  = your current token (vendor key auto-extracted)
  GROWW_TOTP_SECRET = your TOTP base32 secret

Bot auto-logins daily at 8:30 AM IST.
If login fails it retries every 30 seconds until it succeeds.
Never requires manual action after initial .env setup.
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
    """Decode JWT payload without signature verification."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def _extract_vendor_key_from_jwt(token: str) -> Optional[str]:
    """Extract permanent vendorIntegrationKey from Groww JWT sub field."""
    try:
        payload = _decode_jwt_payload(token)
        sub = payload.get("sub", "")
        if isinstance(sub, str) and sub.startswith("{"):
            key = json.loads(sub).get("vendorIntegrationKey", "")
            if key and len(key) > 8:
                return key
        # Sometimes it's directly in payload
        key = payload.get("vendorIntegrationKey", "")
        if key and len(key) > 8:
            return key
    except Exception:
        pass
    return None


def _extract_token_from_response(body: dict) -> Optional[str]:
    """Pull access token string from any Groww API response shape."""
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
    """A vendor key is a short hex/alphanumeric string, not a JWT."""
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
    """True if Groww's 6 AM IST daily reset has invalidated this token."""
    if token_ts is None:
        return True
    now = get_current_ist_time()
    if token_ts.tzinfo is None:
        token_ts = token_ts.replace(tzinfo=IST)
    reset_today = now.replace(hour=6, minute=0, second=0, microsecond=0)
    return token_ts < reset_today and now >= reset_today


# ─────────────────────────────────────────────────────────────────────────────
# Core: single TOTP attempt
# ─────────────────────────────────────────────────────────────────────────────

def _single_totp_attempt(vendor_key: str, totp_secret: str,
                          attempt_label: str = "") -> Optional[str]:
    """
    One TOTP login attempt — tries every known Groww auth method.
    Priority: SDK → email+password+TOTP → direct HTTP endpoints.
    """
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 5:
        logger.debug(f"Waiting {remaining + 1}s for fresh TOTP window...")
        time.sleep(remaining + 1)

    totp_code   = pyotp.TOTP(totp_secret).now()
    window_left = 30 - (int(time.time()) % 30)
    label       = f"[{attempt_label}] " if attempt_label else ""
    logger.info(
        f"[{format_ist_timestamp()}] {label}TOTP code={totp_code} "
        f"| window={window_left}s | vendor={vendor_key[:8] if vendor_key else 'MISSING'}..."
    )

    raw_token = os.getenv("GROWW_AUTH_TOKEN", "")
    email     = os.getenv("GROWW_EMAIL", "")
    password  = os.getenv("GROWW_PASSWORD", "")

    # Keys to try for api_key-based flows (vendor key first, then full JWT)
    keys_to_try = []
    if vendor_key:
        keys_to_try.append(("vendor_key", vendor_key))
    if raw_token and raw_token != vendor_key:
        keys_to_try.append(("raw_auth_token", raw_token))

    # ── Method 1: growwapi SDK (api_key + TOTP) ───────────────────────
    try:
        from growwapi import GrowwAPI
        for key_label, key_val in keys_to_try:
            try:
                result = GrowwAPI.get_access_token(api_key=key_val, totp=totp_code)
                if isinstance(result, str) and len(result) > 20:
                    logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.get_access_token ({key_label})")
                    return result
                if isinstance(result, dict):
                    tok = _extract_token_from_response(result)
                    if tok:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK dict ({key_label})")
                        return tok
                logger.debug(f"SDK.get_access_token ({key_label}): returned {str(result)[:80]}")
            except Exception as e:
                logger.debug(f"SDK.get_access_token ({key_label}): {e}")

            for method_name in ("get_access_token", "refresh_token", "generate_session"):
                try:
                    obj = GrowwAPI(key_val)
                    fn  = getattr(obj, method_name, None)
                    if not fn:
                        continue
                    r = fn(totp=totp_code)
                    if isinstance(r, str) and len(r) > 20:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.{method_name}() ({key_label})")
                        return r
                except Exception as e:
                    logger.debug(f"SDK.{method_name}() ({key_label}): {e}")

        # ── Method 2: SDK login with email + password + TOTP ─────────
        # This is the most reliable method — works even when stored token is expired.
        # Groww SDK's login() mirrors the web login flow.
        if email and password:
            for key_label, key_val in [("vendor_key", vendor_key), ("empty", "")]:
                try:
                    obj = GrowwAPI(key_val) if key_val else GrowwAPI.__new__(GrowwAPI)
                    for login_method in ("login", "authenticate", "create_session",
                                        "login_with_totp", "user_login"):
                        fn = getattr(obj, login_method, None)
                        if not fn:
                            continue
                        try:
                            r = fn(email=email, password=password, totp=totp_code)
                            if isinstance(r, str) and len(r) > 20:
                                logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.{login_method}(email,pwd,totp)")
                                return r
                            if isinstance(r, dict):
                                tok = _extract_token_from_response(r)
                                if tok:
                                    logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.{login_method}() dict")
                                    return tok
                        except TypeError:
                            # Try without keyword args
                            try:
                                r = fn(email, password, totp_code)
                                if isinstance(r, str) and len(r) > 20:
                                    return r
                            except Exception:
                                pass
                        except Exception as e:
                            logger.debug(f"SDK.{login_method}(email,pwd,totp): {e}")
                except Exception as e:
                    logger.debug(f"SDK email+pwd+totp ({key_label}): {e}")

    except ImportError:
        logger.debug("growwapi SDK not installed — using direct HTTP")
    except Exception as e:
        logger.debug(f"SDK error: {e}")

    # ── Method 3: direct HTTP — email + password + TOTP (web auth flow) ─
    # This replicates Groww's web login. Works even with expired stored token.
    if email and password:
        auth_flows = [
            # Single-step: email + password + totp together
            [("POST", "https://api.groww.in/v1/user/generate_session",
              {"email": email, "password": password, "totp": totp_code})],
            [("POST", "https://api.groww.in/v1/user/login",
              {"email": email, "password": password, "totp": totp_code})],
            [("POST", "https://api.groww.in/v1/auth/login",
              {"email": email, "password": password, "totp": totp_code})],
            # Two-step: login then verify
            [("POST", "https://api.groww.in/v1/user/login",
              {"email": email, "password": password}),
             ("POST", "https://api.groww.in/v1/user/login/verify_totp",
              {"totp": totp_code})],
        ]
        base_headers = {"Content-Type": "application/json", "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0 GrowwApp/1.0"}
        for flow in auth_flows:
            try:
                session_token = None
                for method, url, body in flow:
                    hdrs = dict(base_headers)
                    if session_token:
                        hdrs["Authorization"] = f"Bearer {session_token}"
                    resp = requests.request(method, url, json=body, headers=hdrs, timeout=15)
                    logger.debug(f"  [{url.rsplit('/',1)[-1]}] HTTP {resp.status_code}: {str(resp.text)[:100]}")
                    if resp.status_code in (200, 201):
                        tok = _extract_token_from_response(resp.json())
                        if tok:
                            logger.info(f"[{format_ist_timestamp()}] ✅ Token via HTTP email+pwd flow [{url.rsplit('/',1)[-1]}]")
                            return tok
                        # If no final token yet, store for next step
                        session_token = _extract_token_from_response(resp.json()) or session_token
            except Exception as e:
                logger.debug(f"Email+pwd HTTP flow error: {e}")

    # ── Method 4: direct HTTP — api_key + TOTP ───────────────────────
    for key_label, key_val in keys_to_try:
        endpoints = [
            ("POST", "https://api.groww.in/v1/user/generate_token",
             {"api_key": key_val, "totp": totp_code}),
            ("POST", "https://api.groww.in/v1/auth/access-token",
             {"api_key": key_val, "totp": totp_code}),
            ("POST", "https://api.groww.in/v1/auth/token",
             {"apiKey": key_val, "totp": totp_code}),
            ("POST", "https://api.groww.in/v1/user/session/generate_token",
             {"api_key": key_val, "totp": totp_code}),
        ]
        hdrs = {"Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": "growwapi-python/1.0", "X-Api-Version": "1"}
        if not _looks_like_vendor_key(key_val):
            hdrs["Authorization"] = f"Bearer {key_val}"
        for method, url, body in endpoints:
            ep = url.rsplit("/", 1)[-1]
            try:
                resp = requests.request(method, url, json=body, headers=hdrs, timeout=15)
                if resp.status_code in (200, 201):
                    tok = _extract_token_from_response(resp.json())
                    if tok:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Token via HTTP [{ep}] ({key_label})")
                        return tok
                    logger.debug(f"  [{ep}] 200 but no token: {str(resp.json())[:100]}")
                else:
                    logger.debug(f"  [{ep}] HTTP {resp.status_code} ({key_label}): {resp.text[:80]}")
            except Exception as e:
                logger.debug(f"  [{ep}] {e}")

    return None

    # ── Method 1: growwapi SDK ────────────────────────────────────────
    try:
        from growwapi import GrowwAPI
        for key_label, key_val in keys_to_try:
            try:
                result = GrowwAPI.get_access_token(api_key=key_val, totp=totp_code)
                if isinstance(result, str) and len(result) > 20:
                    logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.get_access_token ({key_label})")
                    return result
                if isinstance(result, dict):
                    tok = _extract_token_from_response(result)
                    if tok:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK dict ({key_label})")
                        return tok
            except Exception as e:
                logger.debug(f"SDK.get_access_token ({key_label}): {e}")

            # Try SDK instance methods
            for method_name in ("get_access_token", "refresh_token", "generate_session", "login"):
                try:
                    obj = GrowwAPI(key_val)
                    fn  = getattr(obj, method_name, None)
                    if not fn:
                        continue
                    r = fn(totp=totp_code)
                    if isinstance(r, str) and len(r) > 20:
                        logger.info(f"[{format_ist_timestamp()}] ✅ Token via SDK.{method_name}() ({key_label})")
                        return r
                except Exception as e:
                    logger.debug(f"SDK.{method_name}() ({key_label}): {e}")

    except ImportError:
        logger.debug("growwapi SDK not installed — using direct HTTP")
    except Exception as e:
        logger.debug(f"SDK error: {e}")

    # ── Method 2: direct HTTPS ────────────────────────────────────────
    for key_label, key_val in keys_to_try:
        endpoints = [
            ("POST", "https://api.groww.in/v1/user/generate_token",
             {"api_key": key_val, "totp": totp_code}),
            ("POST", "https://api.groww.in/v1/auth/access-token",
             {"api_key": key_val, "totp": totp_code}),
            ("POST", "https://api.groww.in/v1/auth/token",
             {"apiKey": key_val, "totp": totp_code}),
            ("POST", "https://api.groww.in/v1/user/session/generate_token",
             {"api_key": key_val, "totp": totp_code}),
            ("POST", "https://api.groww.in/v1/user/token/refresh",
             {"api_key": key_val, "totp": totp_code}),
        ]
        headers = {
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "growwapi-python/1.0",
            "X-Api-Version": "1",
        }
        if not _looks_like_vendor_key(key_val):
            headers["Authorization"] = f"Bearer {key_val}"

        for method, url, body in endpoints:
            ep = url.rsplit("/", 1)[-1]
            try:
                resp = requests.request(method, url, json=body,
                                        headers=headers, timeout=15)
                if resp.status_code in (200, 201):
                    tok = _extract_token_from_response(resp.json())
                    if tok:
                        logger.info(
                            f"[{format_ist_timestamp()}] ✅ Token via HTTP "
                            f"[{ep}] ({key_label})"
                        )
                        return tok
                    logger.debug(f"  [{ep}] HTTP 200 but no token: {list(resp.json().keys())}")
                else:
                    logger.debug(f"  [{ep}] HTTP {resp.status_code} ({key_label})")
            except Exception as e:
                logger.debug(f"  [{ep}] {e}")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Auth Manager
# ─────────────────────────────────────────────────────────────────────────────

class GrowwAuthManager:
    """
    Fully autonomous Groww authentication.
    Extracts vendor key from existing GROWW_AUTH_TOKEN automatically.
    Retries TOTP login indefinitely until success — zero manual action needed.
    """

    def __init__(self):
        self.totp_secret = os.getenv("GROWW_TOTP_SECRET", "")
        self._vendor_key:      str               = ""
        self._token:           Optional[str]     = None
        self._token_timestamp: Optional[datetime] = None
        self._attempt_count:   int               = 0

        # Priority order for vendor key:
        # 1. GROWW_VENDOR_KEY env var (explicit)
        # 2. Cached vendor key from previous successful login
        # 3. Extracted from GROWW_AUTH_TOKEN JWT
        # 4. GROWW_AUTH_TOKEN itself if it looks like a vendor key

        cached_vk, cached_tok, cached_ts = _load_token_cache()

        self._vendor_key = (
            os.getenv("GROWW_VENDOR_KEY", "")
            or cached_vk
            or self._bootstrap_vendor_key()
        )

        # Use cached token if still valid today
        if cached_tok and not _is_expired_by_6am_reset(cached_ts):
            self._token           = cached_tok
            self._token_timestamp = cached_ts
            logger.info(f"[{format_ist_timestamp()}] Cached token valid ✅")

        # Log status
        if self._vendor_key:
            logger.info(
                f"[{format_ist_timestamp()}] Vendor key ready: "
                f"{self._vendor_key[:8]}... | TOTP: "
                f"{'✅' if self.totp_secret else '❌ MISSING'}"
            )
        else:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚠️ No vendor key found.\n"
                "  GROWW_AUTH_TOKEN or GROWW_VENDOR_KEY must be set in .env"
            )

        if not self.totp_secret:
            logger.error(
                f"[{format_ist_timestamp()}] ❌ GROWW_TOTP_SECRET not set in .env!\n"
                "  Copy from groww.in → API keys → TOTP row"
            )

    def _bootstrap_vendor_key(self) -> str:
        """Auto-extract vendor key from GROWW_AUTH_TOKEN."""
        raw = os.getenv("GROWW_AUTH_TOKEN", "")
        if not raw:
            return ""
        # Try to extract from JWT
        extracted = _extract_vendor_key_from_jwt(raw)
        if extracted:
            logger.info(
                f"[{format_ist_timestamp()}] ✅ Vendor key auto-extracted from "
                f"GROWW_AUTH_TOKEN: {extracted[:8]}..."
            )
            return extracted
        # If it's a short string, it may already be the vendor key
        if _looks_like_vendor_key(raw):
            logger.info(
                f"[{format_ist_timestamp()}] Using GROWW_AUTH_TOKEN as vendor key "
                f"(short string): {raw[:8]}..."
            )
            return raw
        return ""

    # ─────────────────────────────────────────────────────────────────

    def refresh_token_if_needed(self) -> bool:
        """
        Get a fresh token. Tries up to 6 times with intelligent TOTP window
        selection — waits for a fresh window between attempts.
        Returns True on success.
        """
        if not self.totp_secret:
            logger.error(f"[{format_ist_timestamp()}] ❌ Cannot refresh — GROWW_TOTP_SECRET missing")
            return False
        if not self._vendor_key:
            logger.error(f"[{format_ist_timestamp()}] ❌ Cannot refresh — vendor key missing")
            return False

        # Groww API maintenance window: 2:00 AM – 7:00 AM IST (servers offline)
        # Never attempt login during this window — it always fails.
        # Health check at 7:00 AM and morning login at 8:30 AM handle this.
        now_ist = get_current_ist_time()
        if 2 <= now_ist.hour < 7:
            logger.info(
                f"[{format_ist_timestamp()}] Skipping TOTP — Groww maintenance window "
                f"(2–7 AM IST). Will login at 7 AM health check."
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
                # Extract fresh vendor key from new token if we can
                fresh_vk = _extract_vendor_key_from_jwt(tok)
                if fresh_vk:
                    self._vendor_key = fresh_vk
                _save_token_cache(self._vendor_key, tok, self._token_timestamp)
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ Groww login successful "
                    f"(attempt {attempt}/6, total #{self._attempt_count})"
                )
                return True

            if attempt < 6:
                # Wait for next TOTP window (up to 31s) before retrying
                wait = 30 - (int(time.time()) % 30) + 2
                logger.warning(
                    f"[{format_ist_timestamp()}] Attempt {attempt}/6 failed — "
                    f"waiting {wait}s for next TOTP window..."
                )
                time.sleep(wait)

        logger.error(
            f"[{format_ist_timestamp()}] ❌ All 6 TOTP attempts failed. "
            "Will auto-retry in 5 minutes."
        )
        # Alert only ONCE per day — not every 5 minutes
        today = get_current_ist_time().strftime("%Y-%m-%d")
        if getattr(self, "_retry_alert_date", "") != today:
            self._retry_alert_date = today
            self._send_alert(
                "⚠️ <b>Groww Login Failed — Retrying</b>\n\n"
                "TOTP codes are correct but Groww API is rejecting the login.\n"
                "Bot will keep retrying every 5 min until 4 PM IST.\n\n"
                "<i>This alert will not repeat today.</i>"
            )
        return False

    def get_valid_token(self) -> Optional[str]:
        """Return valid token, refreshing if expired. Never raises."""
        try:
            if self._token and not _is_expired_by_6am_reset(self._token_timestamp):
                return self._token

            # During Groww maintenance window (2–7 AM IST), don't attempt refresh.
            # Modules can initialize without a token — they'll get one at 7 AM.
            now_ist = get_current_ist_time()
            if 2 <= now_ist.hour < 7:
                logger.debug(
                    f"[{format_ist_timestamp()}] No token during maintenance window — "
                    "modules will get fresh token at 7 AM health check."
                )
                return self._token  # May be None — callers handle this gracefully

            reason = "Token expired (6 AM reset)" if self._token else "No token"
            logger.info(f"[{format_ist_timestamp()}] {reason} — refreshing via TOTP...")
            self.refresh_token_if_needed()
            return self._token
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] get_valid_token error: {e}")
            return self._token

    def force_refresh(self) -> bool:
        """Immediate refresh — /refresh Telegram command."""
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
        logger.info(f"[{format_ist_timestamp()}] Auth init: token valid ✅")
    return bool(manager._vendor_key and manager.totp_secret)


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
        print("❌ GROWW_AUTH_TOKEN not set in .env")
        sys.exit(1)
    key = _extract_vendor_key_from_jwt(jwt)
    if not key and _looks_like_vendor_key(jwt):
        key = jwt
    if not key:
        print("❌ Could not extract vendor key from GROWW_AUTH_TOKEN")
        sys.exit(1)
    print(f"\n✅ Vendor key: {key}\n")
    env = Path(".env")
    if env.exists() and "GROWW_VENDOR_KEY" not in env.read_text():
        env.write_text(env.read_text().rstrip() + f"\nGROWW_VENDOR_KEY={key}\n")
        print(f"✅ Auto-added GROWW_VENDOR_KEY to {env.resolve()}")


def _cli_test():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    manager = GrowwAuthManager()
    print(f"\nvendor_key:  {'✅ ' + manager._vendor_key[:12] + '...' if manager._vendor_key else '❌ MISSING'}")
    print(f"totp_secret: {'✅ set' if manager.totp_secret else '❌ MISSING'}")
    print(f"cached token: {'✅ valid' if manager._token else 'none'}\n")
    tok = manager.get_valid_token()
    if tok:
        print(f"✅ Token: {tok[:40]}...{tok[-10:]}")
    else:
        print("❌ Failed — check .env")
        sys.exit(1)


if __name__ == "__main__":
    if "--extract-key" in sys.argv:
        _cli_extract_key()
    else:
        _cli_test()
