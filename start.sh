#!/bin/bash
# ============================================================
# start.sh — KingTrades US Bot Launcher (autonomous-safe)
# Pass --cron to skip interactive prompts (for crontab use).
# ============================================================

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="kingtrades"
CRON_MODE=0
[[ "$*" == *"--cron"* ]] && CRON_MODE=1

mkdir -p "$BOT_DIR/logs"
cd "$BOT_DIR"

# Dependency preflight: alpaca-py (broker + execution) and yfinance (data
# supplement) are mandatory. Without them the bot gets no market data
# (0 signals, 0 trades) and can place no orders. Install if missing.
if ! python3 -c "import alpaca, yfinance" >/dev/null 2>&1; then
    echo "Installing US bot dependencies (alpaca-py / yfinance missing)..."
    python3 -m pip install -q -r "$BOT_DIR/requirements.txt" 2>&1 | tail -3
    if ! python3 -c "import alpaca, yfinance" >/dev/null 2>&1; then
        echo "❌ alpaca-py / yfinance still not importable after install. Fix manually:"
        echo "   python3 -m pip install alpaca-py yfinance"
        echo "   (without them: 0 data, 0 signals, 0 orders)"
    fi
fi

# If screen session exists and is alive, do nothing
if screen -list 2>/dev/null | grep -q "$SESSION"; then
    if [ "$CRON_MODE" -eq 0 ]; then
        echo "Bot is already running in screen session '$SESSION'"
        echo "  View it live:  screen -r $SESSION"
        echo "  Stop it:       bash stop.sh"
    fi
    exit 0
fi

# Safety check for live trading (skip in cron mode — paper trading is default)
if [ "$CRON_MODE" -eq 0 ] && [ -f "$BOT_DIR/.env" ]; then
    LIVE=$(grep "^LIVE_TRADING_ENABLED" "$BOT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]')
    if [ "$LIVE" = "True" ]; then
        echo ""
        echo "⚡⚡⚡ WARNING: LIVE TRADING = REAL MONEY ⚡⚡⚡"
        echo "Type 'yes' to confirm:"
        read -r CONFIRM
        if [ "$CONFIRM" != "yes" ]; then echo "Aborted."; exit 1; fi
    fi
fi

# Clean up stale PID file so single-instance guard doesn't block
rm -f "$BOT_DIR/logs/kingtrades.pid" 2>/dev/null

[ "$CRON_MODE" -eq 0 ] && echo "Starting KingTrades US bot..."

# Runner script (handles crash loop with backoff)
RUNNER="$BOT_DIR/logs/.runner.sh"
cat > "$RUNNER" << 'RUNNER_EOF'
#!/bin/bash
BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BOT_DIR"
RESTARTS=0
while true; do
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Bot starting (attempt $((RESTARTS+1)))..."
    python3 main.py >> logs/bot_output.log 2>&1
    CODE=$?
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Bot exited (code $CODE)"
    # Clean exit or Ctrl+C — stop restarting
    if [ "$CODE" -eq 0 ] || [ "$CODE" -eq 130 ]; then
        echo "Clean shutdown."
        break
    fi
    RESTARTS=$((RESTARTS+1))
    if [ "$RESTARTS" -ge 5 ]; then
        echo "5 crashes in a row — stopping auto-restart. Check Telegram."
        break
    fi
    DELAY=$((30 * RESTARTS))
    echo "Crash #$RESTARTS — restarting in ${DELAY}s..."
    sleep "$DELAY"
done
RUNNER_EOF
chmod +x "$RUNNER"

screen -dmS "$SESSION" bash "$RUNNER"
sleep 1

# Start watchdog (independent of screen — survives bot restarts)
WATCHDOG_PID_FILE="$BOT_DIR/logs/watchdog.pid"
if [ -f "$WATCHDOG_PID_FILE" ] && kill -0 "$(cat "$WATCHDOG_PID_FILE")" 2>/dev/null; then
    :  # already running
else
    rm -f "$WATCHDOG_PID_FILE"
    nohup python3 "$BOT_DIR/watchdog.py" >> "$BOT_DIR/logs/watchdog.log" 2>&1 &
    echo $! > "$WATCHDOG_PID_FILE"
fi

# Start AI supervisor (Claude-powered autonomous fixer)
AI_PID_FILE="$BOT_DIR/logs/ai_supervisor.pid"
if [ -f "$AI_PID_FILE" ] && kill -0 "$(cat "$AI_PID_FILE")" 2>/dev/null; then
    :  # already running
else
    rm -f "$AI_PID_FILE"
    nohup python3 "$BOT_DIR/ai_supervisor.py" >> "$BOT_DIR/logs/ai_supervisor.log" 2>&1 &
    echo $! > "$AI_PID_FILE"
fi

if [ "$CRON_MODE" -eq 0 ]; then
    sleep 2
    if screen -list | grep -q "$SESSION"; then
        echo "✅ US Bot running (screen: $SESSION)"
        echo "  Live logs:  tail -f $BOT_DIR/logs/bot_output.log"
        echo "  Watchdog:   tail -f $BOT_DIR/logs/watchdog.log"
        echo "  AI Supervisor: tail -f $BOT_DIR/logs/ai_supervisor.log"
        echo "  Attach:     screen -r $SESSION  (detach: Ctrl+A D)"
        echo "  Stop:       bash stop.sh"
    else
        echo "❌ Failed to start. Check: tail -f $BOT_DIR/logs/bot_output.log"
    fi
fi
