"""
auto_login_totp.py — FULLY AUTOMATIC daily Upstox token, NO browser at all.

WHY THIS WORKS WHERE PLAYWRIGHT FAILED
--------------------------------------
The old auto_login_upstox.py drove Upstox's login PAGE in headless Chromium and got
blocked by bot detection (blank page). This script skips the page entirely and calls
the same login API endpoints the page itself calls, using curl_cffi to present a real
Chrome TLS fingerprint — there is no browser to detect. (Flow mirrors the open-source
`upstox-totp` package, vendored here so it runs on Python 3.9+ instead of 3.12+.)

FLOW (all plain HTTPS)
  1. GET  /v2/login/authorization/dialog        -> user_id (via redirect params)
  2. POST /login/open/v6/auth/1fa/otp/generate  -> validateOTPToken
  3. POST /login/open/v4/auth/1fa/otp-totp/verify  (TOTP from your secret)
  4. POST /login/open/v3/auth/2fa               (your PIN, base64)
  5. POST /login/v2/oauth/authorize             -> redirect with ?code=
  6. POST /v2/login/authorization/token         -> access_token -> saved to .env

ONE-TIME SETUP (VPS)
  pip install curl_cffi pyotp python-dotenv --break-system-packages
  .env needs (same names the old script used):
    UPSTOX_MOBILE=10-digit-number
    UPSTOX_PIN=your-6-digit-PIN
    UPSTOX_TOTP_SECRET=secret from Upstox app -> Account -> Security -> TOTP
    UPSTOX_API_KEY=...   UPSTOX_API_SECRET=...   UPSTOX_REDIRECT_URI=...
  Then test:  python3 india/auto_login_totp.py
  Cron + run_pilot.sh make it fully hands-off every morning.

HONEST CAVEATS
  * These are Upstox's INTERNAL login endpoints (undocumented). They can change
    without notice — if this breaks, get_upstox_token.py (60s manual) always works.
  * Your PIN + TOTP secret sit in .env: keep it chmod 600, never commit it.
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import string
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_ENV = _ROOT / ".env"

API = "https://api.upstox.com"
SERVICE = "https://service.upstox.com"
LOGIN = "https://login.upstox.com"
# Upstox's own internal redirect used by the web login flow (not your app's URI)
REDIRECT_INTERNAL = "https://api-v2.upstox.com/login/authorization/redirect"
SLEEP_S = 1.0          # polite gap between steps, like the login page itself


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV)
    except Exception:
        pass


def _save_token(token: str):
    txt = _ENV.read_text() if _ENV.exists() else ""
    line = f"UPSTOX_ACCESS_TOKEN={token}"
    if re.search(r"^UPSTOX_ACCESS_TOKEN\s*=.*$", txt, re.MULTILINE):
        txt = re.sub(r"^UPSTOX_ACCESS_TOKEN\s*=.*$", line, txt, flags=re.MULTILINE)
    else:
        txt = txt.rstrip("\n") + f"\n{line}\n"
    tmp = _ENV.with_suffix(".tmp")
    tmp.write_text(txt)
    tmp.replace(_ENV)
    try:
        os.chmod(_ENV, 0o600)
    except Exception:
        pass
    os.environ["UPSTOX_ACCESS_TOKEN"] = token


def _mk_session():
    """curl_cffi session with a real-Chrome TLS fingerprint + browser headers."""
    from curl_cffi import requests as cffi_requests
    req_id = "WPRO-" + "".join(random.choices(string.ascii_letters + string.digits, k=10))
    headers = {
        "accept": "*/*",
        "accept-language": "en-GB,en;q=0.9",
        "content-type": "application/json",
        "origin": LOGIN,
        "referer": LOGIN,
        "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/140.0.0.0 Safari/537.36"),
        "x-request-id": req_id,
    }
    last_err = None
    for imp in ("chrome131", "chrome124", "chrome120", "chrome"):
        try:
            s = cffi_requests.Session(impersonate=imp, headers=headers)
            return s, req_id
        except Exception as e:            # older curl_cffi may not know newer versions
            last_err = e
    raise RuntimeError(f"curl_cffi couldn't create an impersonated session: {last_err}")


def _jpost(sess, url, payload, params=None, step=""):
    r = sess.post(url, params=params, json=payload, allow_redirects=True, timeout=30)
    time.sleep(SLEEP_S)
    try:
        body = r.json()
    except Exception:
        raise RuntimeError(f"{step}: HTTP {r.status_code}, non-JSON response "
                           f"(Upstox may have changed this endpoint): {r.text[:200]}")
    if isinstance(body, dict) and body.get("success") is False:
        raise RuntimeError(f"{step}: {json.dumps(body.get('error') or body)[:300]}")
    data = body.get("data", body) if isinstance(body, dict) else {}
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(f"{step}: {json.dumps(data)[:300]}")
    return data


def get_token(mobile, pin, totp_secret, api_key, api_secret, redirect_uri, verbose=True):
    import pyotp
    sess, req_id = _mk_session()

    def say(msg):
        if verbose:
            print(f"  {msg}", flush=True)

    # 1) authorization dialog -> follow redirects -> user_id in final URL
    say("1/6 authorization dialog ...")
    r = sess.get(f"{API}/v2/login/authorization/dialog",
                 params={"response_type": "code", "client_id": api_key,
                         "redirect_uri": redirect_uri},
                 allow_redirects=True, timeout=30)
    time.sleep(SLEEP_S)
    q = parse_qs(urlparse(str(r.url)).query)
    user_id = (q.get("user_id") or [None])[0]
    dialog_client_id = (q.get("client_id") or [api_key])[0]
    if not user_id:
        raise RuntimeError(
            f"step 1: no user_id in redirect (url={str(r.url)[:160]}). Check "
            f"UPSTOX_API_KEY / UPSTOX_REDIRECT_URI exactly match your Upstox app.")

    # 2) generate OTP session
    say("2/6 request OTP token ...")
    d = _jpost(sess, f"{SERVICE}/login/open/v6/auth/1fa/otp/generate",
               {"data": {"mobileNumber": mobile, "userId": user_id}}, step="step 2 (otp/generate)")
    validate_token = d.get("validateOTPToken")
    if not validate_token:
        raise RuntimeError(f"step 2: no validateOTPToken in response: {json.dumps(d)[:200]}")

    # 3) verify with TOTP from your secret
    say("3/6 verify TOTP ...")
    _jpost(sess, f"{SERVICE}/login/open/v4/auth/1fa/otp-totp/verify",
           {"data": {"otp": pyotp.TOTP(totp_secret).now(),
                     "validateOtpToken": validate_token}},
           step="step 3 (totp/verify — wrong UPSTOX_TOTP_SECRET?)")

    # 4) submit PIN (2FA)
    say("4/6 submit PIN ...")
    _jpost(sess, f"{SERVICE}/login/open/v3/auth/2fa",
           {"data": {"twoFAMethod": "SECRET_PIN",
                     "inputText": base64.b64encode(pin.encode()).decode()}},
           params={"client_id": dialog_client_id, "redirect_uri": REDIRECT_INTERNAL},
           step="step 4 (2fa — wrong UPSTOX_PIN?)")

    # 5) approve OAuth -> redirect URI containing ?code=
    say("5/6 OAuth approval ...")
    d = _jpost(sess, f"{SERVICE}/login/v2/oauth/authorize",
               {"data": {"userOAuthApproval": True}},
               params={"client_id": dialog_client_id, "redirect_uri": REDIRECT_INTERNAL,
                       "requestId": req_id, "response_type": "code"},
               step="step 5 (oauth/authorize)")
    redirect = d.get("redirectUri") or ""
    code = (parse_qs(urlparse(redirect).query).get("code") or [None])[0]
    if not code:
        raise RuntimeError(f"step 5: no ?code= in redirectUri: {redirect[:200]}")

    # 6) exchange code for the day's access token (fresh, plain request)
    say("6/6 exchange code for token ...")
    sess.headers.clear()
    sess.cookies.clear()
    r = sess.post(f"{API}/v2/login/authorization/token",
                  data=(f"code={code}&client_id={api_key}&client_secret={api_secret}"
                        f"&redirect_uri={redirect_uri}&grant_type=authorization_code"),
                  headers={"accept": "application/json",
                           "content-type": "application/x-www-form-urlencoded"},
                  timeout=30)
    body = r.json()
    token = body.get("access_token")
    if not token:
        raise RuntimeError(f"step 6: token exchange failed HTTP {r.status_code}: "
                           f"{json.dumps(body)[:300]}")
    return token


def main():
    _load_env()
    mobile = os.getenv("UPSTOX_MOBILE", os.getenv("UPSTOX_USERNAME", "")).strip()
    pin = os.getenv("UPSTOX_PIN", os.getenv("UPSTOX_PIN_CODE", "")).strip()
    secret = os.getenv("UPSTOX_TOTP_SECRET", "").replace(" ", "").strip()
    api_key = os.getenv("UPSTOX_API_KEY", os.getenv("UPSTOX_CLIENT_ID", "")).strip()
    api_sec = os.getenv("UPSTOX_API_SECRET", os.getenv("UPSTOX_CLIENT_SECRET", "")).strip()
    redir = os.getenv("UPSTOX_REDIRECT_URI", "").strip()
    missing = [k for k, v in {
        "UPSTOX_MOBILE": mobile, "UPSTOX_PIN": pin, "UPSTOX_TOTP_SECRET": secret,
        "UPSTOX_API_KEY": api_key, "UPSTOX_API_SECRET": api_sec,
        "UPSTOX_REDIRECT_URI": redir}.items() if not v]
    if missing:
        print(f"ERROR: missing in .env: {', '.join(missing)}")
        sys.exit(1)
    try:
        import curl_cffi  # noqa: F401
        import pyotp      # noqa: F401
    except ImportError as e:
        print(f"ERROR: {e}\nInstall: pip install curl_cffi pyotp --break-system-packages")
        sys.exit(1)

    print("Upstox auto-login (no browser, TLS-impersonated API flow) ...")
    try:
        token = get_token(mobile, pin, secret, api_key, api_sec, redir)
    except Exception as e:
        print(f"ERROR: {e}")
        print("Fallback: python3 india/get_upstox_token.py  (60-second manual login)")
        sys.exit(2)

    _save_token(token)
    print(f"  SUCCESS — token saved to .env ({token[:12]}...). Valid until ~03:30 IST tomorrow.")

    # verify it actually works against the real API
    try:
        from curl_cffi import requests as cr
        r = cr.get(f"{API}/v2/user/profile",
                   headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
                   timeout=20)
        name = (r.json().get("data") or {}).get("user_name")
        print(f"  VERIFIED — profile OK{f' ({name})' if name else ''}.")
    except Exception as e:
        print(f"  (verify skipped: {e})")


if __name__ == "__main__":
    main()
