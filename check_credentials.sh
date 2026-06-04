#!/bin/bash
# ============================================================
# check_credentials.sh — Verify all credentials before trading
# Run this FIRST to confirm everything is set correctly.
# ============================================================

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BOT_DIR"

PASS=0
FAIL=0

check() {
    local NAME="$1"
    local VALUE="$2"
    local PLACEHOLDER="$3"
    if [ -z "$VALUE" ] || echo "$VALUE" | grep -qi "$PLACEHOLDER"; then
        echo "  ❌ $NAME — NOT SET (still placeholder)"
        FAIL=$((FAIL+1))
    else
        echo "  ✅ $NAME — set (${VALUE:0:8}...)"
        PASS=$((PASS+1))
    fi
}

echo ""
echo "======================================"
echo "  KingTrades Credential Check"
echo "======================================"
echo ""

if [ ! -f "$BOT_DIR/.env" ]; then
    echo "❌ .env file missing! Copy .env.example to .env and fill in your keys."
    exit 1
fi

# Load .env
set -a
source "$BOT_DIR/.env"
set +a

echo "── Alpaca ──────────────────────────────"
check "ALPACA_API_KEY"    "$ALPACA_API_KEY"    "YOUR_"
check "ALPACA_SECRET_KEY" "$ALPACA_SECRET_KEY" "YOUR_"
echo "  Mode: ALPACA_PAPER=$ALPACA_PAPER"
echo ""
echo "── Telegram ────────────────────────────"
check "TELEGRAM_BOT_TOKEN" "$TELEGRAM_BOT_TOKEN" "YOUR_"
check "TELEGRAM_CHAT_ID"   "$TELEGRAM_CHAT_ID"   "YOUR_"
echo ""
echo "── Capital ─────────────────────────────"
echo "  MAX_DAILY_CAPITAL=\$$MAX_DAILY_CAPITAL"
echo "  LIVE_TRADING_ENABLED=$LIVE_TRADING_ENABLED"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "======================================"
    echo "  ❌ $FAIL credential(s) missing"
    echo "======================================"
    echo ""
    echo "Fix with:  nano $BOT_DIR/.env"
    echo ""
    echo "ALPACA keys: https://app.alpaca.markets → Paper Trading → API Keys"
    echo "Telegram:    Message @BotFather → /newbot"
    echo "Chat ID:     https://api.telegram.org/bot<TOKEN>/getUpdates"
    echo ""
    exit 1
fi

echo "======================================"
echo "  Testing Alpaca connection..."
echo "======================================"
python3 - <<'PYEOF'
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()
import urllib.request, ssl

key    = os.getenv('ALPACA_API_KEY','')
secret = os.getenv('ALPACA_SECRET_KEY','')
paper  = os.getenv('ALPACA_PAPER','true').lower() == 'true'
url    = 'https://paper-api.alpaca.markets/v2/account' if paper else 'https://api.alpaca.markets/v2/account'

try:
    req = urllib.request.Request(url, headers={
        'APCA-API-KEY-ID': key,
        'APCA-API-SECRET-KEY': secret,
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read())
        pv = float(d.get('portfolio_value',0))
        bp = float(d.get('buying_power',0))
        print(f"  ✅ Alpaca connected!")
        print(f"  Portfolio value:  ${pv:>12,.2f}")
        print(f"  Buying power:     ${bp:>12,.2f}")
        print(f"  Account status:   {d.get('status','?')}")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"  ❌ Alpaca HTTP {e.code}: {body}")
    if 'allowlist' in body.lower():
        print()
        print("  ACTION REQUIRED: Go to app.alpaca.markets")
        print("  → Account → API Keys → check IP restrictions")
        print("  → If IP restriction is ON, add your VPS IP or disable it")
    sys.exit(1)
except Exception as e:
    print(f"  ❌ Alpaca error: {e}")
    sys.exit(1)
PYEOF

if [ $? -ne 0 ]; then
    exit 1
fi

echo ""
echo "======================================"
echo "  ✅ All checks passed!"
echo "  Run:  bash start.sh"
echo "======================================"
echo ""
