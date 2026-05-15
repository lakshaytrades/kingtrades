"""
save_token.py — Manually inject a Groww auth token from Safari/Chrome.

HOW TO GET YOUR TOKEN FROM SAFARI:
1. Open Safari on your Mac → go to groww.in (make sure you're logged in)
2. Safari menu → Develop → Show Web Inspector  (if Develop menu missing:
   Safari → Settings → Advanced → tick "Show Develop menu in menu bar")
3. Click the "Console" tab
4. Paste this JavaScript and press Enter:

   (function(){
     for(let k of Object.keys(localStorage)){
       let v=localStorage.getItem(k);
       if(v&&v.length>50&&(k.toLowerCase().includes('token')||k.toLowerCase().includes('auth')||v.startsWith('eyJ'))){
         console.log('KEY:',k,'VALUE:',v);
       }
     }
     document.cookie.split(';').forEach(c=>{
       let [k,v]=(c||'').trim().split('=');
       if(v&&v.length>50&&(k||'').toLowerCase().includes('token'))
         console.log('COOKIE:',k,'VALUE:',v);
     });
   })()

5. Copy the VALUE (long string starting with "eyJ..." or similar)
6. Run:  python3 save_token.py
7. Paste the token when prompted

The bot will use this token automatically. It usually lasts 24 hours.
Next morning the bot will auto-login via Chrome to get a fresh token.
"""

import json
import sys
from pathlib import Path

TOKEN_FILE = Path("data/.token_cache.json")
MANUAL_TOKEN_FILE = Path("data/.manual_token.txt")


def main():
    print()
    print("=" * 60)
    print("  Groww Token Injector")
    print("=" * 60)
    print()
    print("Paste your Groww auth token below (from Safari DevTools).")
    print("It should be a long string — press Enter when done.")
    print()

    try:
        token = input("Token: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(0)

    token = token.removeprefix("Bearer ").strip()

    if not token or len(token) < 20:
        print("ERROR: Token too short — make sure you copied the full value.")
        sys.exit(1)

    # Save as manual token file (read by auth_groww.py on startup)
    MANUAL_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANUAL_TOKEN_FILE.write_text(token)

    # Also write into the token cache so auth_groww.py picks it up immediately
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    TOKEN_FILE.write_text(json.dumps({
        "vendor_key": "",
        "access_token": token,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "manual_safari_export",
    }, indent=2))

    print()
    print(f"Saved! Token length: {len(token)} characters")
    print()
    print("Now start/restart the bot:")
    print("  systemctl restart kingtrades")
    print()
    print("Or test it immediately:")
    print("  python3 -c \"from auth_groww import load_cached_token; print(load_cached_token())\"")
    print()


if __name__ == "__main__":
    main()
