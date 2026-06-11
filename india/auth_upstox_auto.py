"""
auth_upstox_auto.py — Automatic daily Upstox token via TOTP (Option A).

Logs into Upstox using your stored credentials + TOTP and generates a fresh
LIVE-trading access token each morning — no manual step. Run by cron before
market open. Saves the token to .env (UPSTOX_ACCESS_TOKEN).

⚠️ SECURITY: this requires your Upstox MOBILE + PIN + TOTP_SECRET stored in
.env. Anyone with VPS access could use them. You accepted this tradeoff for a
small, order-capped account.

⚠️ BEST-EFFORT: Upstox's login flow is not officially built for automation.
These endpoints can change; if Upstox updates them this script may need a fix.
If it fails, the bot falls back to the manual /upstox_login (Telegram) and to
Yahoo data — nothing breaks, you just refresh manually that day.

SETUP (one time):
  1. Enable TOTP 2FA on Upstox (Profile → Security → enable Authenticator).
     Save the TOTP SECRET it shows (the base32 string behind the QR).
  2. Add to .env:
       UPSTOX_MOBILE=9XXXXXXXXX        (10 digits, no +91)
       UPSTOX_PIN=123456               (your 6-digit Upstox PIN)
       UPSTOX_TOTP_SECRET=XXXX...      (base32 secret from step 1)
     (UPSTOX_API_KEY / SECRET / REDIRECT_URI already set for the app)
  3. pip install pyotp
  4. Test:  python3 india/auth_upstox_auto.py
  5. Cron auto-runs it each morning (added by setup below).
"""
import json
import logging
import re
import sys
import time as _time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [upstox_auto] %(message)s")
logger = logging.getLogger("auth_upstox_auto")

_BASE = Path(__file__).parent.parent

# --- Endpoints (edit here if Upstox changes them) ----------------------------
DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL  = "https://api.upstox.com/v2/login/authorization/token"
SVC_1FA    = "https://service.upstox.com/login/open/v6/auth/1fa/otp/generate"
SVC_TOTP   = "https://service.upstox.com/login/open/v6/auth/1fa/otp-totp/verify"
SVC_2FA    = "https://service.upstox.com/login/open/v6/auth/2fa"
_HDR = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json",
        "Accept": "application/json"}


def _cfg():
    try:
        from dotenv import load_dotenv
        load_dotenv(_BASE / ".env")
    except Exception:
        pass
    import os
    return {
        "api_key":  os.getenv("UPSTOX_API_KEY", ""),
        "secret":   os.getenv("UPSTOX_API_SECRET", ""),
        "redirect": os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1"),
        "mobile":   os.getenv("UPSTOX_MOBILE", ""),
        "pin":      os.getenv("UPSTOX_PIN", ""),
        "totp":     os.getenv("UPSTOX_TOTP_SECRET", ""),
    }


def _save_token(token: str):
    env = _BASE / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.strip().startswith("UPSTOX_ACCESS_TOKEN="):
            out.append(f"UPSTOX_ACCESS_TOKEN={token}"); found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"UPSTOX_ACCESS_TOKEN={token}")
    env.write_text("\n".join(out) + "\n")


def _telegram(msg: str):
    try:
        import os, requests
        t = (os.getenv("TELEGRAM_BOT_TOKEN") or "").split(",")[0].strip()
        c = os.getenv("TELEGRAM_CHAT_ID") or ""
        if t and c:
            for cid in c.replace(" ", ",").split(","):
                if cid.strip():
                    requests.post(f"https://api.telegram.org/bot{t}/sendMessage",
                                  json={"chat_id": cid.strip(), "text": msg}, timeout=8)
    except Exception:
        pass


def auto_login() -> bool:
    """Full TOTP login → token. Returns True on success."""
    import requests
    import pyotp
    c = _cfg()
    missing = [k for k in ("api_key", "secret", "mobile", "pin", "totp") if not c[k]]
    if missing:
        logger.error(f"Missing in .env: {missing}")
        return False

    s = requests.Session()
    s.headers.update(_HDR)
    try:
        # 1FA — send/initiate with mobile
        r1 = s.post(SVC_1FA, json={"data": {"mobileNumber": c["mobile"]}}, timeout=15)
        logger.info(f"1FA: HTTP {r1.status_code}")

        # TOTP verify
        code = pyotp.TOTP(c["totp"]).now()
        r2 = s.post(SVC_TOTP, json={"data": {"otp": code, "mobileNumber": c["mobile"]}},
                    timeout=15)
        logger.info(f"TOTP: HTTP {r2.status_code}")

        # 2FA — PIN
        r3 = s.post(SVC_2FA, json={"data": {"twoFAMethod": "SECRET_PIN",
                                            "inputText": c["pin"]}}, timeout=15)
        logger.info(f"PIN: HTTP {r3.status_code}")

        # Authorize dialog → redirect carries ?code=
        params = {"response_type": "code", "client_id": c["api_key"],
                  "redirect_uri": c["redirect"]}
        r4 = s.get(DIALOG_URL, params=params, allow_redirects=False, timeout=15)
        loc = r4.headers.get("Location", "")
        auth_code = None
        m = re.search(r"code=([^&]+)", loc)
        if m:
            auth_code = m.group(1)
        if not auth_code:
            # follow one more redirect if needed
            r4b = s.get(DIALOG_URL, params=params, allow_redirects=True, timeout=15)
            m = re.search(r"code=([^&]+)", r4b.url)
            if m:
                auth_code = m.group(1)
        if not auth_code:
            logger.error("Could not obtain auth code — Upstox login flow may have changed.")
            return False

        # Exchange code → token
        rt = requests.post(TOKEN_URL,
            headers={"accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"code": auth_code, "client_id": c["api_key"],
                  "client_secret": c["secret"], "redirect_uri": c["redirect"],
                  "grant_type": "authorization_code"}, timeout=15)
        tok = rt.json().get("access_token")
        if not tok:
            logger.error(f"Token exchange failed: {rt.text[:200]}")
            return False
        _save_token(tok)
        logger.info("✅ Upstox token refreshed automatically.")
        return True
    except Exception as e:
        logger.error(f"auto_login error: {e}")
        return False


def main():
    ok = auto_login()
    if ok:
        _telegram("✅ Upstox token auto-refreshed for today — live trading ready.")
    else:
        _telegram("⚠️ Upstox auto-login failed. Run /upstox_login on Telegram to "
                  "refresh manually (bot still has Yahoo data meanwhile).")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
