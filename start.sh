#!/bin/bash
# ============================================================
# start.sh — KingTrades Bot Launcher
# Uses 'screen' so bot keeps running after you close SSH
# ============================================================

BOT_DIR="/home/user/kingtrades"
SESSION="kingtrades"
LOG_FILE="$BOT_DIR/logs/bot_output.log"

mkdir -p "$BOT_DIR/logs"
cd "$BOT_DIR"

# Check if already running
if screen -list | grep -q "$SESSION"; then
    echo "Bot is already running in screen session '$SESSION'"
    echo ""
    echo "  View it live:  screen -r $SESSION"
    echo "  Stop it:       bash stop.sh"
    exit 1
fi

# Safety check
LIVE=$(grep "^LIVE_TRADING_ENABLED" .env | cut -d= -f2 | tr -d '[:space:]')
if [ "$LIVE" = "True" ]; then
    echo ""
    echo "⚡⚡⚡ WARNING: LIVE TRADING = REAL MONEY ⚡⚡⚡"
    echo "Type 'yes' to confirm you want real-money trading:"
    read -r CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "Aborted."
        exit 1
    fi
fi

echo "Starting KingTrades bot..."
echo ""

# Start in screen with auto-restart loop
screen -dmS "$SESSION" bash -c '
cd /home/user/kingtrades
RESTARTS=0
while true; do
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Bot starting (attempt $((RESTARTS+1)))..."
    python3 main.py 2>&1 | tee -a logs/bot_output.log
    CODE=${PIPESTATUS[0]}
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Bot exited (code $CODE)"
    # Exit code 0 = clean shutdown (/kill or Ctrl+C) — do not restart
    if [ $CODE -eq 0 ] || [ $CODE -eq 130 ]; then
        echo "Clean shutdown. Bye."
        break
    fi
    RESTARTS=$((RESTARTS+1))
    if [ $RESTARTS -ge 5 ]; then
        echo "5 crashes in a row — stopping auto-restart."
        break
    fi
    echo "Restarting in 30 seconds... (restart $RESTARTS of 5)"
    sleep 30
done
'

sleep 2
if screen -list | grep -q "$SESSION"; then
    echo "✅ Bot is running in background (screen session: $SESSION)"
    echo ""
    echo "  Watch live:     screen -r $SESSION"
    echo "  Detach screen:  Ctrl+A then D"
    echo "  Check status:   bash status.sh"
    echo "  View logs:      tail -f logs/bot_output.log"
    echo "  Stop bot:       bash stop.sh"
    echo ""
    echo "Check Telegram for startup message in 30 seconds."
else
    echo "❌ Failed to start. Check logs: tail -f logs/bot_output.log"
fi
