#!/bin/bash
# ============================================================
# start_india.sh — KingTrades India Bot Launcher (Dhan/NSE)
# Runs independently of the US bot — separate screen session.
# ============================================================

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$BOT_DIR")"
SESSION="kingtrades-india"

mkdir -p "$ROOT_DIR/logs"
cd "$BOT_DIR"

# Check if already running
if screen -list | grep -q "$SESSION"; then
    echo "India bot is already running in screen session '$SESSION'"
    echo ""
    echo "  View it live:  screen -r $SESSION"
    echo "  Stop it:       bash stop_india.sh"
    exit 1
fi

# Safety check for live trading
if [ -f "$ROOT_DIR/.env" ]; then
    LIVE=$(grep "^INDIA_LIVE_TRADING_ENABLED" "$ROOT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]')
    if [ "$LIVE" = "True" ]; then
        echo ""
        echo "⚡⚡⚡ WARNING: INDIA LIVE TRADING = REAL MONEY (Dhan/NSE) ⚡⚡⚡"
        echo "Type 'yes' to confirm real-money trading:"
        read -r CONFIRM
        if [ "$CONFIRM" != "yes" ]; then
            echo "Aborted."
            exit 1
        fi
    fi
fi

echo "Starting KingTrades India Bot (Dhan / NSE)..."
echo ""

RUNNER="$ROOT_DIR/logs/.runner_india.sh"
cat > "$RUNNER" << RUNNER_EOF
#!/bin/bash
cd "$BOT_DIR"
RESTARTS=0
while true; do
    echo "[\$(date +'%Y-%m-%d %H:%M:%S IST')] India bot starting (attempt \$((RESTARTS+1)))..."
    python3 main_india.py 2>&1 | tee -a $ROOT_DIR/logs/india_output.log
    CODE=\${PIPESTATUS[0]}
    echo "[\$(date +'%Y-%m-%d %H:%M:%S')] India bot exited (code \$CODE)"
    if [ "\$CODE" -eq 0 ] || [ "\$CODE" -eq 130 ]; then
        echo "Clean shutdown."
        break
    fi
    RESTARTS=\$((RESTARTS+1))
    if [ "\$RESTARTS" -ge 5 ]; then
        echo "5 crashes — stopping auto-restart."
        break
    fi
    echo "Restarting in 30 seconds... (\$RESTARTS of 5)"
    sleep 30
done
RUNNER_EOF
chmod +x "$RUNNER"

screen -dmS "$SESSION" bash "$RUNNER"

# Launch India watchdog as independent background process
WATCHDOG_PID_FILE="$ROOT_DIR/logs/india_watchdog.pid"
if [ -f "$WATCHDOG_PID_FILE" ] && kill -0 "$(cat "$WATCHDOG_PID_FILE")" 2>/dev/null; then
    echo "India watchdog already running (PID $(cat "$WATCHDOG_PID_FILE"))"
else
    nohup python3 "$BOT_DIR/watchdog_india.py" >> "$ROOT_DIR/logs/india_watchdog.log" 2>&1 &
    echo $! > "$WATCHDOG_PID_FILE"
    echo "✅ India watchdog started (PID $!)"
fi

sleep 2

if screen -list | grep -q "$SESSION"; then
    echo "✅ India bot running (screen: $SESSION)"
    echo ""
    echo "  Watch live:   screen -r $SESSION"
    echo "  Detach:       Ctrl+A then D"
    echo "  Logs:         tail -f $ROOT_DIR/logs/india_output.log"
    echo "  Watchdog:     tail -f $ROOT_DIR/logs/india_watchdog.log"
    echo "  Stop:         bash stop_india.sh"
    echo ""
    echo "Telegram alert expected in ~30 seconds."
else
    echo "❌ Failed to start. Check: tail -f $ROOT_DIR/logs/india_output.log"
fi
