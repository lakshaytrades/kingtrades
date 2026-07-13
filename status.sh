#!/bin/bash
# status.sh — Check bot status

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="kingtrades"
LOG_FILE="$BOT_DIR/logs/bot_output.log"

echo "================================"
echo "   SataVector Bot Status"
echo "================================"

if screen -list | grep -q "$SESSION"; then
    echo "Status:  ✅ RUNNING"
    echo "Session: screen -r $SESSION (to view)"
else
    echo "Status:  ❌ NOT RUNNING"
    echo "Start:   bash start.sh"
fi

if [ -f "$BOT_DIR/.env" ]; then
    LIVE=$(grep "^LIVE_TRADING_ENABLED" "$BOT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]')
    [ "$LIVE" = "True" ] && echo "Mode:    ⚡ LIVE (real money)" || echo "Mode:    📄 PAPER (no real money)"
fi

echo ""
echo "--- Last 25 log lines ---"
[ -f "$LOG_FILE" ] && tail -25 "$LOG_FILE" || echo "No logs yet."
