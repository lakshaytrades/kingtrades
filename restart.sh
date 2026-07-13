#!/bin/bash
BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BOT_DIR"

echo "Stopping bot..."
bash "$BOT_DIR/stop.sh" 2>/dev/null
sleep 3

echo "Starting bot..."
bash "$BOT_DIR/start.sh"
