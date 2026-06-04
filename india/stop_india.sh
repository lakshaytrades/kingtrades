#!/bin/bash
SESSION="kingtrades-india"
ROOT_DIR="$(dirname "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"

echo "Stopping KingTrades India Bot..."

if screen -list | grep -q "$SESSION"; then
    screen -X -S "$SESSION" quit
    echo "✅ Screen session '$SESSION' terminated"
else
    echo "No active screen session found for '$SESSION'"
fi

# Kill any stray processes
pkill -f "main_india.py" 2>/dev/null && echo "✅ main_india.py processes killed"

# Clean up PID file
rm -f "$ROOT_DIR/logs/india_bot.pid"
echo "Done."
