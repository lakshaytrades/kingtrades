#!/bin/bash
# ============================================================
# go.sh — One-command launcher for the India bot (safe paper mode).
# After you've put your tokens in .env, just run:  bash india/go.sh
# It: stops old/US processes, updates code, checks .env, verifies,
# confirms PAPER mode, and starts the fully-automatic bot.
# ============================================================
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

echo "════════════════════════════════════════════"
echo " KINGTRADES INDIA — one-command launch"
echo "════════════════════════════════════════════"

# 1. Stop old / US processes (safety)
echo "==> Stopping any old or US processes..."
pkill -f "main.py" 2>/dev/null
pkill -f "ai_supervisor.py" 2>/dev/null
pkill -f "watchdog.py" 2>/dev/null
sleep 1

# 2. Update code
echo "==> Pulling latest code..."
git pull --quiet 2>/dev/null || echo "   (git pull skipped/failed — continuing)"

# 3. Check .env exists
if [ ! -f "$ROOT_DIR/.env" ]; then
    echo "❌ No .env file found. Create it first — see india/SETUP_GUIDE.md"
    exit 1
fi

# 4. Check required keys are filled
MISSING=0
check_key() {
    local v
    v=$(grep "^$1=" "$ROOT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '[:space:]')
    if [ -z "$v" ]; then
        echo "❌ $1 is empty in .env — fill it in."
        MISSING=1
    else
        echo "   ✓ $1 set"
    fi
}
echo "==> Checking .env keys..."
check_key TELEGRAM_BOT_TOKEN
check_key TELEGRAM_CHAT_ID
# Upstox optional (falls back to free Yahoo)
UPX=$(grep "^UPSTOX_ACCESS_TOKEN=" "$ROOT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '[:space:]')
[ -z "$UPX" ] && echo "   ⚠ UPSTOX_ACCESS_TOKEN empty — will use free Yahoo data (fine)"

# 5. SAFETY: refuse to start in live mode unless explicitly forced
if grep -q "^INDIA_LIVE_TRADING_ENABLED=True" "$ROOT_DIR/.env" || \
   grep -q "^LIVE_TRADING_ENABLED=True" "$ROOT_DIR/.env"; then
    echo "🚨 LIVE TRADING is ON in .env — this places REAL orders."
    echo "   For safe paper mode set INDIA_LIVE_TRADING_ENABLED=False."
    echo "   Aborting for your safety. (Set it False, then re-run.)"
    exit 1
fi
echo "   ✓ Paper mode (no real orders)"

[ "$MISSING" = "1" ] && { echo "Fix the empty keys in .env, then re-run: bash india/go.sh"; exit 1; }

# 6. Verify everything
echo "==> Verifying setup (data, Telegram, engine)..."
python3 "$ROOT_DIR/india/verify_setup.py"

# 7. Launch
echo "==> Starting the bot (fully automatic, paper mode)..."
bash "$ROOT_DIR/india/start_india.sh"

echo "════════════════════════════════════════════"
echo " Done. The bot now runs automatically."
echo " In Telegram: /data  /today  /proof"
echo "════════════════════════════════════════════"
