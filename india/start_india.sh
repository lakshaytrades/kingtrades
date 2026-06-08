#!/bin/bash
# ============================================================
# start_india.sh — KingTrades India Bot Launcher (autonomous-safe)
# Pass --cron to skip interactive prompts (for crontab use).
# ============================================================

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$BOT_DIR")"
SESSION="kingtrades-india"
CRON_MODE=0
[[ "$*" == *"--cron"* ]] && CRON_MODE=1

mkdir -p "$ROOT_DIR/logs"
cd "$BOT_DIR"

# Dependency preflight: the dhanhq SDK is mandatory for live data + orders.
# Without it the bot silently falls back to paper mode with no market data,
# so install requirements before launching if it's missing.
if ! python3 -c "import dhanhq" >/dev/null 2>&1; then
    echo "Installing India bot dependencies (dhanhq missing)..."
    python3 -m pip install -q -r "$BOT_DIR/requirements_india.txt" 2>&1 | tail -3
    if ! python3 -c "import dhanhq" >/dev/null 2>&1; then
        echo "❌ dhanhq still not importable after install. Fix manually:"
        echo "   python3 -m pip install dhanhq"
        echo "   (the bot will run in PAPER mode with no live data until this is fixed)"
    fi
fi

# If screen session exists and is alive, do nothing
if screen -list 2>/dev/null | grep -q "$SESSION"; then
    if [ "$CRON_MODE" -eq 0 ]; then
        echo "India bot is already running in screen session '$SESSION'"
        echo "  View it live:  screen -r $SESSION"
        echo "  Stop it:       bash stop_india.sh"
    fi
    exit 0
fi

# Safety check for live trading (skip in cron mode)
if [ "$CRON_MODE" -eq 0 ] && [ -f "$ROOT_DIR/.env" ]; then
    LIVE=$(grep "^INDIA_LIVE_TRADING_ENABLED" "$ROOT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]')
    if [ "$LIVE" = "True" ]; then
        echo ""
        echo "⚡⚡⚡ WARNING: INDIA LIVE TRADING = REAL MONEY (Dhan/NSE) ⚡⚡⚡"
        echo "Type 'yes' to confirm:"
        read -r CONFIRM
        if [ "$CONFIRM" != "yes" ]; then echo "Aborted."; exit 1; fi
    fi
fi

# Clean stale PID so instance guard doesn't block
rm -f "$ROOT_DIR/logs/india_bot.pid" 2>/dev/null

[ "$CRON_MODE" -eq 0 ] && echo "Starting KingTrades India Bot (Dhan / NSE)..."

# Runner script (handles crash loop with backoff)
RUNNER="$ROOT_DIR/logs/.runner_india.sh"
cat > "$RUNNER" << 'RUNNER_EOF'
#!/bin/bash
BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../india" && pwd)"
ROOT_DIR="$(dirname "$BOT_DIR")"
cd "$BOT_DIR"
RESTARTS=0
while true; do
    echo "[$(date +'%Y-%m-%d %H:%M:%S IST')] India bot starting (attempt $((RESTARTS+1)))..."
    python3 main_india.py >> "$ROOT_DIR/logs/india_output.log" 2>&1
    CODE=$?
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] India bot exited (code $CODE)"
    if [ "$CODE" -eq 0 ] || [ "$CODE" -eq 130 ]; then
        echo "Clean shutdown."
        break
    fi
    RESTARTS=$((RESTARTS+1))
    if [ "$RESTARTS" -ge 5 ]; then
        echo "5 crashes — stopping auto-restart. Check Telegram."
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

# Start India watchdog
WATCHDOG_PID_FILE="$ROOT_DIR/logs/india_watchdog.pid"
if [ -f "$WATCHDOG_PID_FILE" ] && kill -0 "$(cat "$WATCHDOG_PID_FILE")" 2>/dev/null; then
    :  # already running
else
    rm -f "$WATCHDOG_PID_FILE"
    nohup python3 "$BOT_DIR/watchdog_india.py" >> "$ROOT_DIR/logs/india_watchdog.log" 2>&1 &
    echo $! > "$WATCHDOG_PID_FILE"
fi

if [ "$CRON_MODE" -eq 0 ]; then
    sleep 2
    if screen -list | grep -q "$SESSION"; then
        echo "✅ India Bot running (screen: $SESSION)"
        echo "  Live logs:  tail -f $ROOT_DIR/logs/india_output.log"
        echo "  Watchdog:   tail -f $ROOT_DIR/logs/india_watchdog.log"
        echo "  Attach:     screen -r $SESSION  (detach: Ctrl+A D)"
        echo "  Stop:       bash stop_india.sh"
    else
        echo "❌ Failed. Check: tail -f $ROOT_DIR/logs/india_output.log"
    fi
fi
