#!/bin/bash
cd /home/user/kingtrades

echo "Killing all python processes..."
pkill -9 -f python3 2>/dev/null
pkill -9 -f python 2>/dev/null
sleep 5

echo "Verifying nothing is running..."
RUNNING=$(ps aux | grep -E "python|main\.py" | grep -v grep | wc -l)
if [ "$RUNNING" -gt 0 ]; then
    echo "WARNING: $RUNNING process(es) still running, force killing..."
    ps aux | grep -E "python|main\.py" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
    sleep 3
fi

echo "Starting bot..."
nohup python3 main.py >> logs/bot.log 2>&1 &
echo "Bot started with PID $!"
echo "Run: tail -f /home/user/kingtrades/logs/bot.log"
