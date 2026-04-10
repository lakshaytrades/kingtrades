"""
update_token.py — Quick Token Updater for KingTrades Bot

Run this on your LOCAL computer (not Render) when the token expires:
    python update_token.py

It will:
1. Show you exactly where to find the token in Chrome
2. Accept the token you paste
3. Update data/.token_cache.json so the bot picks it up next restart
4. Show you the Render env var command to update permanently

Usage:
    python update_token.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════╗
║           HOW TO GET YOUR GROWW AUTH TOKEN                   ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  1. Open Chrome → go to groww.in                             ║
║  2. Press F12 → click "Network" tab                          ║
║  3. Log in to Groww with your email + password + TOTP        ║
║  4. In Network tab, look for a request called:               ║
║       "validate" or "login" (filter: Fetch/XHR)              ║
║  5. Click that request → Headers tab                         ║
║  6. Look for:                                                ║
║       • Response header "Authorization: Bearer eyJ..."       ║
║       • OR Cookie "user-token=eyJ..." or "authToken=eyJ..."  ║
║       • OR in Response body: look for "token": "eyJ..."      ║
║                                                              ║
║  Alternative (easier):                                       ║
║  After logging in to groww.in, open Console tab (F12)        ║
║  and paste this:                                             ║
║                                                              ║
║  document.cookie.split(';')                                  ║
║    .map(c=>c.trim())                                         ║
║    .find(c=>c.startsWith('user-token')||                     ║
║             c.startsWith('authToken')||                       ║
║             c.startsWith('token='))                          ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""

def main():
    print(INSTRUCTIONS)
    print("\nPaste your Groww auth token below (starts with 'eyJ' usually):")
    print("(Press Enter twice when done)\n")

    token = input("> ").strip()

    if not token:
        print("No token entered. Exiting.")
        sys.exit(1)

    if len(token) < 20:
        print(f"Token too short ({len(token)} chars). Looks wrong.")
        sys.exit(1)

    # Save to cache file
    cache_file = Path("data/.token_cache.json")
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(IST)
    cache = {
        "token": token,
        "timestamp": now.isoformat(),
    }
    cache_file.write_text(json.dumps(cache, indent=2))
    print(f"\n✅ Token saved to {cache_file}")
    print(f"   Length  : {len(token)} chars")
    print(f"   Preview : {token[:12]}...{token[-6:]}")
    print(f"   Saved at: {now.strftime('%Y-%m-%d %H:%M IST')}")
    print(f"   Valid for: ~24 hours")

    print("\n" + "="*60)
    print("NEXT STEP — Update Render environment variable:")
    print("="*60)
    print("\n  1. Go to: dashboard.render.com")
    print("  2. Click kingtrades-bot → Environment")
    print("  3. Find GROWW_AUTH_TOKEN → paste token → Save")
    print("  4. Bot auto-redeploys with new token")
    print("\nOR if the bot is already running, just restart it:")
    print("  Render Dashboard → kingtrades-bot → Manual Deploy → Deploy")
    print("\n" + "="*60)

if __name__ == "__main__":
    main()
