"""
update_token.py — Auth Diagnostic & Manual Override for KingTrades Bot

The bot is now FULLY AUTOMATIC. You should NEVER need to run this.
  • GROWW_AUTH_TOKEN  = permanent JWT from developer.groww.in (set once, never changes)
  • GROWW_TOTP_SECRET = base32 TOTP secret (set once, never changes)
  • The bot refreshes its access_token every morning at 6:05 AM IST automatically.

Run this ONLY for diagnostics or if auto-refresh is broken:
    python update_token.py          — test auto-refresh
    python update_token.py --test   — same as above
    python update_token.py --status — show current token age / cache info
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST  = ZoneInfo("Asia/Kolkata")
CACHE = Path("data/.token_cache.json")


def show_status():
    """Show current token cache status."""
    print("\n── Token Cache Status ───────────────────────────────────")
    if not CACHE.exists():
        print("  No cache file found at data/.token_cache.json")
    else:
        try:
            data = json.loads(CACHE.read_text())
            api_key      = data.get("api_key", "")
            access_token = data.get("access_token", "")
            ts_str       = data.get("timestamp", "")
            ts = datetime.fromisoformat(ts_str).replace(tzinfo=IST) if ts_str else None
            age_h = ((datetime.now(IST) - ts).total_seconds() / 3600) if ts else None
            print(f"  api_key set:      {'✅ YES (' + api_key[:20] + '...)' if api_key else '❌ NO'}")
            print(f"  access_token set: {'✅ YES (' + access_token[:20] + '...)' if access_token else '❌ NO'}")
            print(f"  timestamp:        {ts_str or 'not set'}")
            if age_h is not None:
                status = "✅ VALID" if age_h < 12 else "⚠️  STALE (>12h)"
                print(f"  token age:        {age_h:.1f}h  {status}")
        except Exception as e:
            print(f"  Cache read error: {e}")

    print("\n── Environment Variables ────────────────────────────────")
    api_key = os.getenv("GROWW_AUTH_TOKEN", "")
    totp    = os.getenv("GROWW_TOTP_SECRET", "")
    print(f"  GROWW_AUTH_TOKEN:  {'✅ SET' if api_key else '❌ NOT SET'}")
    print(f"  GROWW_TOTP_SECRET: {'✅ SET' if totp else '❌ NOT SET'}")
    print()


def test_auto_refresh():
    """Test the automatic TOTP refresh and show the result."""
    print("\n── Testing Automatic TOTP Refresh ───────────────────────")
    print("This simulates what the bot does every morning at 6:05 AM IST.\n")

    try:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from auth_groww import GrowwAuthManager

        mgr = GrowwAuthManager()
        print(f"  api_key loaded:      {'✅ YES' if mgr._api_key else '❌ NO — set GROWW_AUTH_TOKEN'}")
        print(f"  totp_secret loaded:  {'✅ YES' if mgr.totp_secret else '❌ NO — set GROWW_TOTP_SECRET'}\n")

        if not mgr._api_key or not mgr.totp_secret:
            print("Cannot test: missing env vars. Set them in Render Environment Variables.")
            print("  GROWW_AUTH_TOKEN  = permanent JWT from developer.groww.in")
            print("  GROWW_TOTP_SECRET = TOTP base32 secret from same page")
            return False

        print("Calling GrowwAPI.get_access_token()...")
        token = mgr.get_valid_token()

        if token:
            print(f"\n✅ SUCCESS! Access token obtained:")
            print(f"   {token[:30]}...{token[-10:]}")
            print(f"   Length: {len(token)} chars")
            print("\nThe bot will refresh this automatically every morning.")
            print("You never need to manually update any token.")
            return True
        else:
            print("\n❌ FAILED to get access token.")
            print("\nCheck:")
            print("  1. GROWW_AUTH_TOKEN = permanent API key from developer.groww.in → API Keys")
            print("     (NOT the daily token — the permanent key shown on that page)")
            print("  2. GROWW_TOTP_SECRET = base32 TOTP secret from developer.groww.in → API Keys")
            print("  3. Both set correctly in Render → kingtrades-bot → Environment")
            return False

    except ImportError as e:
        print(f"Import error: {e}")
        print("Run from the kingtrades/ directory: python update_token.py")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "--test"

    if arg == "--status":
        show_status()
    else:
        show_status()
        success = test_auto_refresh()
        sys.exit(0 if success else 1)
