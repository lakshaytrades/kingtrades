"""
set_token.py — paste ONLY your Upstox access token. Nothing else.

It saves the token to .env and (optionally) starts the pilot right away. No redirect
URLs, no code exchange, no browser dance — just paste the token string and go.

  python3 india/set_token.py
"""
from __future__ import annotations
import os, re, subprocess, sys
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_ENV  = _ROOT / ".env"


def main():
    print("=" * 60)
    print("  Paste your Upstox ACCESS TOKEN below, then press Enter:")
    print("  (just the long token string — nothing else)")
    print("=" * 60)
    try:
        token = sys.stdin.readline().strip().strip('"').strip("'").strip()
    except KeyboardInterrupt:
        print("\nCancelled."); return
    if len(token) < 20:
        print("That doesn't look like a token (too short). Nothing saved.")
        return

    txt = _ENV.read_text() if _ENV.exists() else ""
    line = f"UPSTOX_ACCESS_TOKEN={token}"
    if re.search(r"^UPSTOX_ACCESS_TOKEN\s*=.*$", txt, re.MULTILINE):
        txt = re.sub(r"^UPSTOX_ACCESS_TOKEN\s*=.*$", line, txt, flags=re.MULTILINE)
    else:
        txt = txt.rstrip("\n") + f"\n{line}\n"
    tmp = _ENV.with_suffix(".env.tmp")
    tmp.write_text(txt); tmp.replace(_ENV)
    print(f"\n  ✓ Token saved to .env  ({token[:10]}...). Valid until ~03:30 IST.")

    ans = input("\n  Start the live pilot now? [y/N]: ").strip().lower()
    if ans == "y":
        os.environ["INDIA_LIVE_TRADING_ENABLED"] = "true"
        print("  Starting live pilot (₹5k, validated config) ...\n")
        subprocess.run([sys.executable, str(_HERE / "live_pilot.py")])
    else:
        print("\n  Token is set. Start the pilot whenever with:")
        print("     bash india/run_pilot.sh live")


if __name__ == "__main__":
    main()
