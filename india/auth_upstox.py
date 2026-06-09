"""
auth_upstox.py — Upstox daily access-token helper.

Upstox tokens expire daily (~3:30 AM IST), so you regenerate one each morning.
This makes it a 2-step copy-paste instead of fighting the API.

ONE-TIME SETUP (create the app):
  1. https://account.upstox.com/developer/apps  → Create App
  2. Set Redirect URI to:  https://127.0.0.1   (or whatever you put in .env)
  3. Copy the API Key + API Secret into .env:
        UPSTOX_API_KEY=your_api_key
        UPSTOX_API_SECRET=your_api_secret
        UPSTOX_REDIRECT_URI=https://127.0.0.1

DAILY USAGE (takes ~30 seconds):
  Step 1 — print the login link:
        python3 auth_upstox.py
     Open the link, log in to Upstox. Your browser lands on
     https://127.0.0.1/?code=XXXXXX  (it'll show "can't reach site" — that's
     fine, just copy the code=... value from the address bar).

  Step 2 — exchange the code for a token (auto-saves to .env):
        python3 auth_upstox.py YOUR_CODE_HERE

  Then restart the bot:  bash stop.sh && bash india/start_india.sh
"""
import sys
import re
from pathlib import Path

_BASE = Path(__file__).parent.parent


def _load_cfg():
    try:
        from dotenv import load_dotenv
        load_dotenv(_BASE / ".env")
    except Exception:
        pass
    import os
    return (os.getenv("UPSTOX_API_KEY", ""),
            os.getenv("UPSTOX_API_SECRET", ""),
            os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1"))


def _login_url(api_key, redirect):
    return (f"https://api.upstox.com/v2/login/authorization/dialog"
            f"?response_type=code&client_id={api_key}&redirect_uri={redirect}")


def _exchange(code, api_key, api_secret, redirect):
    import requests
    r = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={"accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"code": code, "client_id": api_key, "client_secret": api_secret,
              "redirect_uri": redirect, "grant_type": "authorization_code"},
        timeout=15,
    )
    return r.json()


def _save_token(token):
    """Write/replace UPSTOX_ACCESS_TOKEN in .env without touching other lines."""
    env = _BASE / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.strip().startswith("UPSTOX_ACCESS_TOKEN="):
            out.append(f"UPSTOX_ACCESS_TOKEN={token}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"UPSTOX_ACCESS_TOKEN={token}")
    env.write_text("\n".join(out) + "\n")


def main():
    api_key, api_secret, redirect = _load_cfg()
    if not api_key or not api_secret:
        print("❌ Set UPSTOX_API_KEY and UPSTOX_API_SECRET in .env first.")
        print("   Create an app at https://account.upstox.com/developer/apps")
        return

    if len(sys.argv) < 2:
        # Step 1: print login link
        print("\nSTEP 1 — open this link, log in to Upstox:\n")
        print("  " + _login_url(api_key, redirect))
        print("\nAfter login your browser goes to a URL like:")
        print("  " + redirect + "/?code=ABC123...")
        print("(the page may say 'can't reach site' — that's fine)")
        print("\nSTEP 2 — copy the code and run:")
        print("  python3 auth_upstox.py ABC123...\n")
        return

    # Step 2: exchange code (strip a full pasted URL down to the code if needed)
    code = sys.argv[1].strip()
    m = re.search(r"code=([^&\s]+)", code)
    if m:
        code = m.group(1)

    resp = _exchange(code, api_key, api_secret, redirect)
    token = resp.get("access_token")
    if not token:
        print(f"❌ Token exchange failed: {resp}")
        return
    _save_token(token)
    print("✅ Upstox access token saved to .env (UPSTOX_ACCESS_TOKEN).")
    print("   Now restart: bash stop.sh && bash india/start_india.sh")


if __name__ == "__main__":
    main()
