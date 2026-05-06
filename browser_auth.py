"""
browser_auth.py — Headless Chrome login to Groww
Uses Playwright to automate the exact same login flow a human does.
No Cloud API Key needed — just email + password + TOTP.

Requires:
  pip install playwright
  playwright install chromium
"""

import logging
import os
import time
from typing import Optional
from zoneinfo import ZoneInfo

import pyotp

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _get_totp(secret: str) -> str:
    remaining = 30 - (int(time.time()) % 30)
    if remaining < 4:
        time.sleep(remaining + 1)
    return pyotp.TOTP(secret).now()


def login_via_browser(
    email: str,
    password: str,
    totp_secret: str,
    headless: bool = True,
    timeout_ms: int = 60000,
) -> Optional[str]:
    """
    Log into groww.in via headless Chrome and return the JWT access token.

    Returns token string on success, None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.warning(
            f"[{format_ist_timestamp()}] playwright not installed — "
            "run: pip install playwright && playwright install chromium"
        )
        return None

    if not email or not password or not totp_secret:
        logger.warning(f"[{format_ist_timestamp()}] Browser auth: missing email/password/TOTP")
        return None

    logger.info(f"[{format_ist_timestamp()}] Browser auth: launching headless Chrome...")

    token = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )

            # Capture token from XHR responses
            captured_token: list = []

            def _on_response(resp):
                try:
                    if "token" in resp.url.lower() or "login" in resp.url.lower() or "auth" in resp.url.lower():
                        try:
                            body = resp.json()
                            for key in ("token", "authToken", "auth_token", "access_token",
                                        "jwtToken", "userToken", "accessToken"):
                                val = (body.get(key) or (body.get("data") or {}).get(key, ""))
                                if val and isinstance(val, str) and len(val) > 30:
                                    captured_token.append(val.removeprefix("Bearer "))
                                    logger.info(
                                        f"[{format_ist_timestamp()}] Browser: captured token from "
                                        f"response to {resp.url.split('?')[0]} ({key})"
                                    )
                        except Exception:
                            pass
                except Exception:
                    pass

            page = ctx.new_page()
            page.on("response", _on_response)

            # ── Step 1: Navigate to Groww login ──────────────────────────────
            logger.info(f"[{format_ist_timestamp()}] Browser: navigating to groww.in/login")
            page.goto("https://groww.in/login", timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=15000)

            # ── Step 2: Enter email ──────────────────────────────────────────
            email_selectors = [
                'input[type="email"]',
                'input[placeholder*="email" i]',
                'input[placeholder*="Email" i]',
                'input[name="loginId"]',
                'input[name="email"]',
                'input[id*="email" i]',
            ]
            email_field = None
            for sel in email_selectors:
                try:
                    page.wait_for_selector(sel, timeout=5000)
                    email_field = sel
                    break
                except PWTimeout:
                    continue

            if not email_field:
                logger.error(f"[{format_ist_timestamp()}] Browser: could not find email field")
                page.screenshot(path="data/login_debug_email.png")
                browser.close()
                return None

            page.fill(email_field, email)
            logger.info(f"[{format_ist_timestamp()}] Browser: entered email")

            # Some sites have a Continue button before password
            try:
                continue_btn = page.query_selector('button:has-text("Continue")')
                if not continue_btn:
                    continue_btn = page.query_selector('button[type="submit"]:first-of-type')
                if continue_btn:
                    continue_btn.click()
                    page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            # ── Step 3: Enter password ────────────────────────────────────────
            pwd_selectors = [
                'input[type="password"]',
                'input[placeholder*="password" i]',
                'input[name="password"]',
            ]
            pwd_field = None
            for sel in pwd_selectors:
                try:
                    page.wait_for_selector(sel, timeout=8000)
                    pwd_field = sel
                    break
                except PWTimeout:
                    continue

            if not pwd_field:
                logger.error(f"[{format_ist_timestamp()}] Browser: could not find password field")
                page.screenshot(path="data/login_debug_pwd.png")
                browser.close()
                return None

            page.fill(pwd_field, password)
            logger.info(f"[{format_ist_timestamp()}] Browser: entered password")

            # Click login/submit
            submit_selectors = [
                'button[type="submit"]',
                'button:has-text("Login")',
                'button:has-text("Sign in")',
                'button:has-text("Continue")',
                'button:has-text("Next")',
            ]
            for sel in submit_selectors:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        break
                except Exception:
                    continue

            page.wait_for_load_state("networkidle", timeout=10000)

            # ── Step 4: Enter TOTP / OTP ──────────────────────────────────────
            otp_selectors = [
                'input[placeholder*="OTP" i]',
                'input[placeholder*="otp" i]',
                'input[placeholder*="TOTP" i]',
                'input[placeholder*="code" i]',
                'input[placeholder*="verification" i]',
                'input[type="number"][maxlength="6"]',
                'input[maxlength="6"]',
                'input[name="otp"]',
                'input[name="totp"]',
            ]
            otp_field = None
            for sel in otp_selectors:
                try:
                    page.wait_for_selector(sel, timeout=10000)
                    otp_field = sel
                    break
                except PWTimeout:
                    continue

            if otp_field:
                totp_code = _get_totp(totp_secret)
                page.fill(otp_field, totp_code)
                logger.info(f"[{format_ist_timestamp()}] Browser: entered TOTP code {totp_code}")

                # Submit OTP
                for sel in submit_selectors:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible():
                            btn.click()
                            break
                    except Exception:
                        continue

                page.wait_for_load_state("networkidle", timeout=15000)
            else:
                logger.warning(f"[{format_ist_timestamp()}] Browser: OTP field not found — may not be required")

            # ── Step 5: Wait for dashboard / extract token ────────────────────
            try:
                page.wait_for_url("**/dashboard**", timeout=20000)
                logger.info(f"[{format_ist_timestamp()}] Browser: logged in — on dashboard")
            except PWTimeout:
                logger.info(f"[{format_ist_timestamp()}] Browser: not on dashboard URL, checking for token anyway")

            # Check captured tokens from API responses first
            if captured_token:
                token = captured_token[-1]
                logger.info(f"[{format_ist_timestamp()}] Browser: token from API response ({len(token)} chars)")
            else:
                # Extract from localStorage
                for ls_key in ("authToken", "token", "auth_token", "access_token",
                               "userToken", "jwtToken", "groww-auth", "AUTH_TOKEN"):
                    val = page.evaluate(f"() => localStorage.getItem('{ls_key}')")
                    if val and len(val) > 30:
                        token = val.removeprefix("Bearer ")
                        logger.info(f"[{format_ist_timestamp()}] Browser: token from localStorage[{ls_key}]")
                        break

                # Extract from sessionStorage
                if not token:
                    for ss_key in ("authToken", "token", "access_token", "userToken"):
                        val = page.evaluate(f"() => sessionStorage.getItem('{ss_key}')")
                        if val and len(val) > 30:
                            token = val.removeprefix("Bearer ")
                            logger.info(f"[{format_ist_timestamp()}] Browser: token from sessionStorage[{ss_key}]")
                            break

                # Extract from cookies
                if not token:
                    cookies = ctx.cookies()
                    for c in cookies:
                        if any(k in c["name"].lower() for k in ("token", "auth", "jwt", "session")):
                            if len(c["value"]) > 30:
                                token = c["value"].removeprefix("Bearer ")
                                logger.info(f"[{format_ist_timestamp()}] Browser: token from cookie {c['name']}")
                                break

                # Last resort: scan all localStorage keys
                if not token:
                    all_ls = page.evaluate("""() => {
                        let out = {};
                        for (let i = 0; i < localStorage.length; i++) {
                            let k = localStorage.key(i);
                            out[k] = localStorage.getItem(k);
                        }
                        return out;
                    }""")
                    if isinstance(all_ls, dict):
                        for k, v in all_ls.items():
                            if v and isinstance(v, str) and len(v) > 50 and "." in v:
                                token = v.removeprefix("Bearer ")
                                logger.info(f"[{format_ist_timestamp()}] Browser: token from localStorage scan [{k}]")
                                break

            if not token:
                page.screenshot(path="data/login_debug_final.png")
                logger.error(f"[{format_ist_timestamp()}] Browser: logged in but could not find token in page")
            else:
                logger.info(f"[{format_ist_timestamp()}] Browser auth SUCCESS ({len(token)} char token)")

            browser.close()

    except Exception as e:
        logger.error(f"[{format_ist_timestamp()}] Browser auth error: {e}")

    return token


# ─────────────────────────────────────────────────────────────────────────────
# CLI test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    email    = os.getenv("GROWW_EMAIL", "")
    password = os.getenv("GROWW_PASSWORD", "")
    totp_sec = os.getenv("GROWW_TOTP_SECRET", "")

    if not email or not password or not totp_sec:
        print("Set GROWW_EMAIL, GROWW_PASSWORD, GROWW_TOTP_SECRET in .env")
        sys.exit(1)

    print(f"Logging in as {email}...")
    tok = login_via_browser(email, password, totp_sec)
    if tok:
        print(f"\nSUCCESS: {tok[:40]}...{tok[-10:]}\n")
    else:
        print("\nFAILED — check data/login_debug_*.png for screenshots\n")
        sys.exit(1)
