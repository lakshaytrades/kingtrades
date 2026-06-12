#!/bin/bash
# stop.sh — Cleanly stop the SataVector bot

SESSION="kingtrades"

if screen -list | grep -q "$SESSION"; then
    echo "Stopping bot..."
    screen -S "$SESSION" -X quit
    sleep 2
    echo "Bot stopped."
else
    echo "Bot is not running."
fi
