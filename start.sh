#!/bin/bash
# ============================================================
# start.sh — KingTrades Bot Launcher
# Works from any directory — path is auto-detected.
# ============================================================

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="kingtrades"

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

# Safety check for live trading
if [ -f "$BOT_DIR/.env" ]; then
    LIVE=$(grep "^LIVE_TRADING_ENABLED" "$BOT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]')
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
fi

echo "Starting KingTrades bot..."
echo ""

# Write a self-contained runner script so screen has no quoting issues
RUNNER="$BOT_DIR/logs/.runner.sh"
cat > "$RUNNER" << RUNNER_EOF
#!/bin/bash
cd "$BOT_DIR"
RESTARTS=0
while true; do
    echo "[\$(date +'%Y-%m-%d %H:%M:%S')] Bot starting (attempt \$((RESTARTS+1)))..."
    python3 main.py 2>&1 | tee -a logs/bot_output.log
    CODE=\${PIPESTATUS[0]}
    echo "[\$(date +'%Y-%m-%d %H:%M:%S')] Bot exited (code \$CODE)"
    if [ "\$CODE" -eq 0 ] || [ "\$CODE" -eq 130 ]; then
        echo "Clean shutdown. Bye."
        break
    fi
    RESTARTS=\$((RESTARTS+1))
    if [ "\$RESTARTS" -ge 5 ]; then
        echo "5 crashes in a row — stopping auto-restart."
        break
    fi
    echo "Restarting in 30 seconds... (restart \$RESTARTS of 5)"
    sleep 30
done
RUNNER_EOF
chmod +x "$RUNNER"

screen -dmS "$SESSION" bash "$RUNNER"

# Launch watchdog as background process (survives independently of screen)
WATCHDOG_PID_FILE="$BOT_DIR/logs/watchdog.pid"
if [ -f "$WATCHDOG_PID_FILE" ] && kill -0 "$(cat "$WATCHDOG_PID_FILE")" 2>/dev/null; then
    echo "Watchdog already running (PID $(cat "$WATCHDOG_PID_FILE"))"
else
    nohup python3 "$BOT_DIR/watchdog.py" >> "$BOT_DIR/logs/watchdog.log" 2>&1 &
    echo $! > "$WATCHDOG_PID_FILE"
    echo "✅ Watchdog started (PID $!)"
fi

sleep 2
if screen -list | grep -q "$SESSION"; then
    echo "✅ Bot is running in background (screen session: $SESSION)"
    echo ""
    echo "  Watch live:     screen -r $SESSION"
    echo "  Detach screen:  Ctrl+A then D"
    echo "  Check status:   bash status.sh"
    echo "  View logs:      tail -f $BOT_DIR/logs/bot_output.log"
    echo "  Stop bot:       bash stop.sh"
    echo ""
    echo "Check Telegram for startup message in ~30 seconds."
    echo ""
    echo "  Watchdog log:   tail -f $BOT_DIR/logs/watchdog.log"
else
    echo "❌ Failed to start. Check logs: tail -f $BOT_DIR/logs/bot_output.log"
fi
