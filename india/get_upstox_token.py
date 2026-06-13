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
import urllib.parse
import urllib.request
import json
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
    """Write/update a key in the .env file."""
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


def main():
    print("=" * 60)
    print("  Upstox Daily Token Generator")
    print("=" * 60)
    print()

    api_key    = _read_env("UPSTOX_API_KEY",    "API Key (client_id)")
    api_secret = _read_env("UPSTOX_API_SECRET", "API Secret (client_secret)")
    redirect   = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1/callback").strip()

    if not api_key or not api_secret:
        print("ERROR: UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env")
        sys.exit(1)

    # Step 1 — Build login URL
    auth_url = (
        "https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code"
        f"&client_id={urllib.parse.quote(api_key)}"
        f"&redirect_uri={urllib.parse.quote(redirect)}"
    )

    print("STEP 1 — Open this URL in your browser and login:")
    print()
    print(f"  {auth_url}")
    print()
    print("STEP 2 — After login you'll be redirected to a URL like:")
    print(f"  {redirect}?code=XXXXXXXXXXXXXXXX")
    print()

    redirect_url = input("Paste the full redirect URL here: ").strip()
    if not redirect_url:
        print("Aborted.")
        sys.exit(1)

    # Extract code from redirect URL
    parsed = urllib.parse.urlparse(redirect_url)
    params = urllib.parse.parse_qs(parsed.query)
    code   = params.get("code", [""])[0]
    if not code:
        # Maybe user pasted just the code
        code = redirect_url.strip()

    if not code:
        print("ERROR: Could not find authorization code in the URL.")
        sys.exit(1)

    print(f"\nExtracted code: {code[:12]}...")
    print("Exchanging for access token ...")

    # Step 2 — Exchange code for token
    payload = urllib.parse.urlencode({
        "code":          code,
        "client_id":     api_key,
        "client_secret": api_secret,
        "redirect_uri":  redirect,
        "grant_type":    "authorization_code",
    }).encode()

    req = urllib.request.Request(
        "https://api.upstox.com/v2/login/authorization/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
        print(f"\nHTTP {e.code} Error:")
        print(json.dumps(body, indent=2))
        sys.exit(1)

    token = body.get("access_token", "")
    if not token:
        print("\nERROR: No access_token in response:")
        print(json.dumps(body, indent=2))
        sys.exit(1)

    # Save to .env
    _update_env("UPSTOX_ACCESS_TOKEN", token)
    print()
    print("=" * 60)
    print(f"  SUCCESS! Token saved to .env")
    print(f"  Token prefix: {token[:12]}...")
    print(f"  Length: {len(token)} chars")
    print(f"  Valid until: ~03:30 IST tomorrow")
    print("=" * 60)
    print()
    print("Now run the backtest:")
    print("  python3 india/backtest_replay_india.py --days 30 --capital 500000")
    print()
    print("Or start the live bot:")
    print("  python3 india/main_india.py")


if __name__ == "__main__":
    main()
