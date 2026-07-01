"""
auto_login_upstox.py — hands-off daily Upstox token via headless browser + TOTP.

Upstox has NO official credential/TOTP login API, so the only way to fully automate
the daily token is to drive the real login page in a headless browser (Playwright),
typing your mobile + TOTP + PIN for you, catching the redirect ?code=, and exchanging
it for the access token — which it writes to .env. After a one-time setup you never log
in by hand again.

HONEST CAVEAT: browser automation of a broker login is fragile. Upstox can change their
page and break the selectors; run with --show the FIRST time to watch it and confirm.
If it ever breaks, fall back to the 60-second manual flow (get_upstox_token.py).

ONE-TIME SETUP
  1. Upstox app -> Security -> 2FA -> switch to Authenticator (TOTP); save the secret.
  2. Put these in .env:
       UPSTOX_MOBILE=10-digit-number
       UPSTOX_PIN=your-6-digit-PIN
       UPSTOX_TOTP_SECRET=the-secret-from-step-1
       UPSTOX_API_KEY=...        UPSTOX_API_SECRET=...
       UPSTOX_REDIRECT_URI=https://127.0.0.1/callback   (must match your Upstox app)
  3. Install deps on the VPS:  pip install playwright pyotp requests
                               python3 -m playwright install chromium
  4. Test once, WATCHING it:   python3 india/auto_login_upstox.py --show
     Once it works headless:   python3 india/auto_login_upstox.py

The launcher (run_pilot.sh) calls this automatically, so cron => fully hands-off.
"""
from __future__ import annotations
import argparse, os, re, sys, time, urllib.parse
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_ENV  = _ROOT / ".env"


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
    tmp = _ENV.with_suffix(".env.tmp")
    tmp.write_text(txt); tmp.replace(_ENV)
    os.environ["UPSTOX_ACCESS_TOKEN"] = token


def _exchange_code(api_key, api_secret, code, redirect):
    import requests
    r = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        data={"code": code, "client_id": api_key, "client_secret": api_secret,
              "redirect_uri": redirect, "grant_type": "authorization_code"},
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        timeout=20,
    )
    return r.status_code, r.json()


# candidate selectors (Upstox changes these; we try several + screenshot on miss)
SEL_MOBILE = ["input[type='tel']", "input#mobileNum", "input[name='mobileNum']",
              "input[placeholder*='obile']", "input[type='text']"]
SEL_GETOTP = ["#getOtp", "button:has-text('Get OTP')", "button:has-text('Continue')",
              "button:has-text('Proceed')", "button[type='submit']"]
SEL_OTP    = ["input#otpNum", "input[name='otpNum']", "input[type='tel']",
              "input[placeholder*='OTP']", "input[autocomplete='one-time-code']"]
SEL_OTPGO  = ["#continueBtn", "button:has-text('Continue')", "button:has-text('Verify')",
              "button[type='submit']"]
SEL_PIN    = ["input#pinCode", "input[name='pinCode']", "input[type='password']",
              "input[placeholder*='PIN']", "input[type='tel']"]
SEL_PINGO  = ["#pinContinueBtn", "button:has-text('Continue')", "button:has-text('Login')",
              "button[type='submit']"]


def _fill_first(page, selectors, value, debugname, shots):
    for s in selectors:
        try:
            el = page.locator(s).first
            if el.count() and el.is_visible():
                el.fill(value)
                return True
        except Exception:
            continue
    if shots:
        page.screenshot(path=str(_ROOT / "logs" / f"login_fail_{debugname}.png"))
    return False


def _click_first(page, selectors, debugname, shots):
    for s in selectors:
        try:
            el = page.locator(s).first
            if el.count() and el.is_visible():
                el.click()
                return True
        except Exception:
            continue
    if shots:
        page.screenshot(path=str(_ROOT / "logs" / f"click_fail_{debugname}.png"))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="headed mode (watch it) for first-run debug")
    args = ap.parse_args()
    _load_env()
    (_ROOT / "logs").mkdir(exist_ok=True)

    mobile  = os.getenv("UPSTOX_MOBILE", "").strip()
    pin     = os.getenv("UPSTOX_PIN", os.getenv("UPSTOX_PASSWORD", "")).strip()
    secret  = os.getenv("UPSTOX_TOTP_SECRET", "").replace(" ", "").strip()
    api_key = os.getenv("UPSTOX_API_KEY", "").strip()
    api_sec = os.getenv("UPSTOX_API_SECRET", "").strip()
    redir   = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1/callback").strip()
    missing = [k for k, v in {"UPSTOX_MOBILE": mobile, "UPSTOX_PIN": pin,
               "UPSTOX_TOTP_SECRET": secret, "UPSTOX_API_KEY": api_key,
               "UPSTOX_API_SECRET": api_sec}.items() if not v]
    if missing:
        print(f"ERROR: missing in .env: {', '.join(missing)}"); sys.exit(1)

    try:
        import pyotp
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(f"ERROR: {e}\nInstall: pip install playwright pyotp requests && "
              f"python3 -m playwright install chromium"); sys.exit(1)

    auth_url = ("https://api.upstox.com/v2/login/authorization/dialog"
                f"?response_type=code&client_id={urllib.parse.quote(api_key)}"
                f"&redirect_uri={urllib.parse.quote(redir)}")
    code_box = {"code": None}

    with sync_playwright() as pw:
        launch = {"headless": not args.show}
        exe = os.getenv("PLAYWRIGHT_CHROMIUM") or "/opt/pw-browsers/chromium"
        if Path(exe).exists():
            launch["executable_path"] = exe
        try:
            browser = pw.chromium.launch(**launch)
        except Exception:
            browser = pw.chromium.launch(headless=not args.show)
        page = browser.new_page()

        # capture the redirect ?code= the moment Upstox sends it
        def _on_req(req):
            if redir.split("//")[-1].split("/")[0] in req.url and "code=" in req.url:
                q = urllib.parse.urlparse(req.url).query
                code_box["code"] = urllib.parse.parse_qs(q).get("code", [None])[0]
        page.on("request", _on_req)

        print("  opening Upstox login ...")
        page.goto(auth_url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)

        if not _fill_first(page, SEL_MOBILE, mobile, "mobile", True):
            print("ERROR: couldn't find the mobile field (Upstox layout changed?). "
                  "See logs/login_fail_mobile.png. Use get_upstox_token.py for now.")
            browser.close(); sys.exit(2)
        _click_first(page, SEL_GETOTP, "getotp", True); time.sleep(3)

        otp = pyotp.TOTP(secret).now()
        _fill_first(page, SEL_OTP, otp, "otp", True)
        _click_first(page, SEL_OTPGO, "otpgo", True); time.sleep(3)

        _fill_first(page, SEL_PIN, pin, "pin", True)
        _click_first(page, SEL_PINGO, "pingo", True)

        # wait for the redirect code to arrive
        for _ in range(20):
            if code_box["code"]:
                break
            time.sleep(1)
            if "code=" in (page.url or ""):
                q = urllib.parse.urlparse(page.url).query
                code_box["code"] = urllib.parse.parse_qs(q).get("code", [None])[0]
        if not code_box["code"]:
            # save the final page so we can see where it got stuck (headless debugging)
            try:
                page.screenshot(path=str(_ROOT / "logs" / "login_final_state.png"))
                (_ROOT / "logs" / "login_final_url.txt").write_text(page.url or "")
            except Exception:
                pass
        browser.close()

    code = code_box["code"]
    if not code:
        print("ERROR: login did not produce an auth code. Run with --show to watch it, "
              "check logs/*.png, verify mobile/PIN/TOTP and the redirect URL match Upstox.")
        sys.exit(3)

    print("  got auth code, exchanging for token ...")
    status, body = _exchange_code(api_key, api_sec, code, redir)
    token = body.get("access_token") if isinstance(body, dict) else None
    if status != 200 or not token:
        print(f"ERROR: token exchange failed HTTP {status}: {body}"); sys.exit(4)
    _save_token(token)
    print(f"  SUCCESS — token saved to .env ({token[:12]}...). Valid until ~03:30 IST.")


if __name__ == "__main__":
    main()
