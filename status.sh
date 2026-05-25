#!/bin/bash
# status.sh — Check bot status

SESSION="kingtrades"
LOG_FILE="/home/user/kingtrades/logs/bot_output.log"

echo "================================"
echo "   KingTrades Bot Status"
echo "================================"

if screen -list | grep -q "$SESSION"; then
    echo "Status:  ✅ RUNNING"
    echo "Session: screen -r $SESSION (to view)"
else
    echo "Status:  ❌ NOT RUNNING"
    echo "Start:   bash start.sh"
fi

LIVE=$(grep "^LIVE_TRADING_ENABLED" /home/user/kingtrades/.env | cut -d= -f2 | tr -d '[:space:]')
[ "$LIVE" = "True" ] && echo "Mode:    ⚡ LIVE (real money)" || echo "Mode:    📄 PAPER (no real money)"

echo ""
echo "--- Last 25 log lines ---"
[ -f "$LOG_FILE" ] && tail -25 "$LOG_FILE" || echo "No logs yet."
