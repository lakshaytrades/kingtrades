"""
get_upstox_token.py — One-shot Upstox daily token generator

Run this ONCE each morning before starting the bot.
It opens the Upstox OAuth URL, you login, paste the redirect URL,
and it saves the new token to .env automatically.

Usage:
    python3 india/get_upstox_token.py
"""
import os
import re
import sys
import json
import urllib.parse
from pathlib import Path

_BASE     = Path(__file__).parent.parent
_ENV_PATH = _BASE / ".env"

try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_PATH)
except ImportError:
    pass


def _read_env(key: str, prompt_label: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        val = input(f"Enter your Upstox {prompt_label}: ").strip()
    return val


def _update_env(key: str, value: str):
    env_text = _ENV_PATH.read_text() if _ENV_PATH.exists() else ""
    pattern = rf"^{re.escape(key)}\s*=.*$"
    if re.search(pattern, env_text, re.MULTILINE):
        env_text = re.sub(pattern, f"{key}={value}", env_text, re.MULTILINE)
    else:
        env_text = env_text.rstrip("\n") + f"\n{key}={value}\n"
    tmp = _ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text(env_text)
    tmp.replace(_ENV_PATH)
    os.environ[key] = value


def _exchange_code(api_key: str, api_secret: str, code: str, redirect: str) -> dict:
    """POST token exchange using requests with browser-like headers."""
    import requests
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept":       "application/json",
        "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Origin":       "https://api.upstox.com",
        "Referer":      "https://api.upstox.com/",
    }
    data = {
        "code":          code,
        "client_id":     api_key,
        "client_secret": api_secret,
        "redirect_uri":  redirect,
        "grant_type":    "authorization_code",
    }
    resp = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        data=data,
        headers=headers,
        timeout=20,
    )
    return resp.status_code, resp.json()


def main():
    print("=" * 60)
    print("  Upstox Daily Token Generator")
    print("=" * 60)
    print()

    api_key    = _read_env("UPSTOX_API_KEY",    "API Key (client_id from dev portal)")
    api_secret = _read_env("UPSTOX_API_SECRET", "API Secret (client_secret)")
    redirect   = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1/callback").strip()

    if not api_key or not api_secret:
        print("ERROR: UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env")
        print()
        print("Get them from: https://developer.upstox.com/apps")
        print("  → Your App → API Key / API Secret")
        sys.exit(1)

    # Step 1 — Build login URL
    auth_url = (
        "https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code"
        f"&client_id={urllib.parse.quote(api_key)}"
        f"&redirect_uri={urllib.parse.quote(redirect)}"
    )

    print("STEP 1 — Open this URL in your browser/phone and login to Upstox:")
    print()
    print(f"  {auth_url}")
    print()
    print("STEP 2 — After login, browser redirects to a URL like:")
    print(f"  https://127.0.0.1/callback?code=AbCdEfGhIjKl12345")
    print("  (The page will show an error — that's normal, just copy the URL)")
    print()

    redirect_url = input("Paste the FULL redirect URL (or just the code): ").strip()
    if not redirect_url:
        print("Aborted.")
        sys.exit(1)

    # Extract code
    if "?" in redirect_url or "code=" in redirect_url:
        parsed = urllib.parse.urlparse(redirect_url)
        params = urllib.parse.parse_qs(parsed.query)
        code   = params.get("code", [""])[0]
    else:
        code = redirect_url.strip()

    if not code:
        print("ERROR: Could not extract authorization code.")
        sys.exit(1)

    print(f"\nExtracted code: {code[:15]}...")
    print("Exchanging for access token (using browser headers) ...")

    try:
        status, body = _exchange_code(api_key, api_secret, code, redirect)
    except Exception as e:
        print(f"\nRequest failed: {e}")
        print("\nTry pasting the token manually instead:")
        token = input("Paste your UPSTOX_ACCESS_TOKEN directly (or press Enter to exit): ").strip()
        if token and len(token) > 20:
            _update_env("UPSTOX_ACCESS_TOKEN", token)
            print(f"\nToken saved! ({token[:12]}...)")
        sys.exit(0)

    if status != 200 or "access_token" not in body:
        print(f"\nHTTP {status} Error:")
        print(json.dumps(body, indent=2))
        print()
        print("Common fixes:")
        print("  1. Make sure redirect_uri in your Upstox app settings matches exactly:")
        print(f"     {redirect}")
        print("  2. The auth code is single-use — re-run the script and get a fresh code")
        print("  3. Or paste your token manually:")
        token = input("\nPaste UPSTOX_ACCESS_TOKEN directly (or Enter to exit): ").strip()
        if token and len(token) > 20:
            _update_env("UPSTOX_ACCESS_TOKEN", token)
            print(f"\nToken saved! ({token[:12]}...)")
        sys.exit(0)

    token = body["access_token"]
    _update_env("UPSTOX_ACCESS_TOKEN", token)

    print()
    print("=" * 60)
    print(f"  SUCCESS! Token saved to .env")
    print(f"  Prefix : {token[:15]}...")
    print(f"  Length : {len(token)} chars")
    print(f"  Expires: ~03:30 IST tomorrow")
    print("=" * 60)
    print()
    print("Run the real backtest now:")
    print("  python3 india/backtest_replay_india.py --days 30 --capital 500000")
    print()
    print("Or start the live bot:")
    print("  python3 india/main_india.py")


if __name__ == "__main__":
    main()
