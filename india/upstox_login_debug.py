"""
upstox_login_debug.py — DIAGNOSTIC for the auto-login flow.

Runs each Upstox login step and prints the response STRUCTURE (status, JSON
keys, error messages) so we can see exactly what Upstox expects. Sensitive
values (tokens) are truncated. Run it, paste the output back.

  python3 india/upstox_login_debug.py
"""
import json
import sys
from pathlib import Path

_BASE = Path(__file__).parent.parent


def _cfg():
    try:
        from dotenv import load_dotenv
        load_dotenv(_BASE / ".env")
    except Exception:
        pass
    import os
    return {
        "api_key":  os.getenv("UPSTOX_API_KEY", ""),
        "redirect": os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1"),
        "mobile":   os.getenv("UPSTOX_MOBILE", ""),
        "pin":      os.getenv("UPSTOX_PIN", ""),
        "totp":     os.getenv("UPSTOX_TOTP_SECRET", ""),
    }


def _show(label, r):
    print(f"\n===== {label} =====")
    print(f"HTTP {r.status_code}")
    try:
        j = r.json()
        # redact long token-like strings, keep structure + messages
        def red(o):
            if isinstance(o, dict):
                return {k: red(v) for k, v in o.items()}
            if isinstance(o, list):
                return [red(x) for x in o[:3]]
            if isinstance(o, str) and len(o) > 25:
                return o[:8] + "...(" + str(len(o)) + " chars)"
            return o
        print(json.dumps(red(j), indent=2)[:1500])
    except Exception:
        print("(non-JSON body):", r.text[:400])
    # show any token-ish headers/cookies (names only)
    cks = list(r.cookies.keys())
    if cks:
        print("cookies set:", cks)


def main():
    import requests, pyotp
    c = _cfg()
    if not all([c["api_key"], c["mobile"], c["pin"], c["totp"]]):
        print("❌ Fill UPSTOX_API_KEY, UPSTOX_MOBILE, UPSTOX_PIN, UPSTOX_TOTP_SECRET in .env first.")
        return
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0",
                      "Content-Type": "application/json", "Accept": "application/json"})

    # 1FA
    r1 = s.post("https://service.upstox.com/login/open/v6/auth/1fa",
                json={"data": {"mobileNumber": c["mobile"]}}, timeout=15)
    _show("STEP 1 — 1FA (mobile)", r1)

    # capture any token from 1FA response to pass forward
    tok = None
    try:
        d = r1.json().get("data", {})
        tok = d.get("validateOTPToken") or d.get("validateOtpToken") or d.get("token") \
              or d.get("userId") or d.get("sessionId")
        print("\n[1FA token candidate captured]:", "yes" if tok else "no",
              f"(keys: {list(d.keys())})")
    except Exception as e:
        print("1FA parse error:", e)

    # TOTP verify — try passing the captured token both as header and body
    code = pyotp.TOTP(c["totp"]).now()
    print(f"\n[Generated TOTP code]: {code}")
    hdrs = dict(s.headers)
    if tok:
        hdrs["Authorization"] = f"Bearer {tok}"
    r2 = s.post("https://service.upstox.com/login/open/v6/auth/1fa/otp-totp/verify",
                headers=hdrs,
                json={"data": {"otp": code, "validateOTPToken": tok,
                               "mobileNumber": c["mobile"]}}, timeout=15)
    _show("STEP 2 — TOTP verify", r2)

    # 2FA PIN
    r3 = s.post("https://service.upstox.com/login/open/v6/auth/2fa",
                json={"data": {"twoFAMethod": "SECRET_PIN", "inputText": c["pin"]}},
                timeout=15)
    _show("STEP 3 — PIN", r3)

    # Authorize dialog
    r4 = s.get("https://api.upstox.com/v2/login/authorization/dialog",
               params={"response_type": "code", "client_id": c["api_key"],
                       "redirect_uri": c["redirect"]},
               allow_redirects=False, timeout=15)
    print("\n===== STEP 4 — Authorize dialog =====")
    print(f"HTTP {r4.status_code}")
    print("Location:", r4.headers.get("Location", "(none)")[:200])


if __name__ == "__main__":
    main()
