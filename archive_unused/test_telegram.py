"""
test_telegram.py — Verify Telegram alerts work.
Reads credentials from .env — no typing of tokens or URLs needed.
Run: python3 test_telegram.py
"""
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

if not token:
    print("FAIL: TELEGRAM_BOT_TOKEN not in .env")
    raise SystemExit(1)
if not chat_id:
    print("FAIL: TELEGRAM_CHAT_ID not in .env")
    raise SystemExit(1)

# Build URL in fragments — prevents angle-bracket injection from terminals
_h = "api" + ".telegr" + "am.org"
_u = "https://" + _h + "/bot" + token + "/sendMessage"

try:
    r = requests.post(_u, json={"chat_id": chat_id, "text": "SataVector bot is LIVE and connected!"}, timeout=10)
    if r.ok:
        print("SUCCESS — check your Telegram now!")
    else:
        print(f"FAIL: {r.status_code} {r.text[:200]}")
except Exception as e:
    print(f"ERROR: {e}")
