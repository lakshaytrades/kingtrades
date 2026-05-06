#!/bin/bash
# healthcheck.sh — Monitor bot health, auto-restart if down, alert Telegram
# Runs every 15 minutes via systemd timer.

INSTALL_DIR="/opt/kingtrades"
SERVICE="kingtrades"
LOG="/var/log/kingtrades-health.log"
MAX_RESTART_ATTEMPTS=3
RESTART_COUNT_FILE="/tmp/kingtrades_restart_count"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Load Telegram credentials
BOT_TOKEN=""
CHAT_ID=""
if [ -f "$INSTALL_DIR/.env" ]; then
    BOT_TOKEN=$(grep "^TELEGRAM_BOT_TOKEN=" "$INSTALL_DIR/.env" | cut -d= -f2 | tr -d '"'"'"' ')
    CHAT_ID=$(grep "^TELEGRAM_CHAT_ID=" "$INSTALL_DIR/.env" | cut -d= -f2 | tr -d '"'"'"' ')
fi

send_tg() {
    if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CHAT_ID}" \
            -d "parse_mode=HTML" \
            -d "text=$1" > /dev/null 2>&1
    fi
}

# Reset restart count at midnight
HOUR=$(date +%H)
if [ "$HOUR" = "00" ]; then
    echo "0" > "$RESTART_COUNT_FILE"
fi

RESTART_COUNT=$(cat "$RESTART_COUNT_FILE" 2>/dev/null || echo "0")

# ── Check 1: Is the service running? ─────────────────────────────────────────
if ! systemctl is-active --quiet "$SERVICE"; then
    echo "[$TIMESTAMP] ALERT: $SERVICE is DOWN" >> "$LOG"

    if [ "$RESTART_COUNT" -ge "$MAX_RESTART_ATTEMPTS" ]; then
        send_tg "🔴 <b>Bot is DOWN — not restarting</b>\nAlready restarted $RESTART_COUNT times today.\nManual check needed:\n<code>journalctl -u $SERVICE -n 50</code>"
        echo "[$TIMESTAMP] Max restarts reached — not restarting" >> "$LOG"
        exit 1
    fi

    send_tg "⚠️ <b>Bot was DOWN — restarting now...</b>"
    systemctl restart "$SERVICE"
    sleep 10

    NEW_COUNT=$((RESTART_COUNT + 1))
    echo "$NEW_COUNT" > "$RESTART_COUNT_FILE"

    if systemctl is-active --quiet "$SERVICE"; then
        echo "[$TIMESTAMP] Restarted OK (attempt $NEW_COUNT)" >> "$LOG"
        send_tg "✅ <b>Bot restarted successfully</b> (auto-restart #$NEW_COUNT today)"
    else
        echo "[$TIMESTAMP] Restart FAILED" >> "$LOG"
        LAST_LOG=$(journalctl -u "$SERVICE" -n 20 --no-pager 2>/dev/null | tail -10)
        send_tg "❌ <b>Bot restart FAILED</b>\nLast log lines:\n<pre>${LAST_LOG}</pre>"
    fi
    exit 0
fi

# ── Check 2: Is the bot stuck (no log output for 30+ min during market hours)? ─
IST_HOUR=$(TZ="Asia/Kolkata" date +%H)
IST_MIN=$(TZ="Asia/Kolkata" date +%M)
IST_DOW=$(TZ="Asia/Kolkata" date +%u)  # 1=Mon ... 7=Sun

# Only check for stuck bot during market hours (9:15–15:30 IST, Mon–Fri)
if [ "$IST_DOW" -le 5 ] && \
   [ "$IST_HOUR" -ge 9 ] && [ "$IST_HOUR" -le 15 ]; then

    LAST_LOG_AGE=$(( $(date +%s) - $(journalctl -u "$SERVICE" -n 1 --no-pager --output=short-unix 2>/dev/null | awk '{print $1}' | head -1 || echo "0") ))

    if [ "$LAST_LOG_AGE" -gt 1800 ]; then  # 30 minutes
        echo "[$TIMESTAMP] WARNING: No log output for ${LAST_LOG_AGE}s — possible stuck bot" >> "$LOG"
        send_tg "⚠️ <b>Bot may be stuck</b>\nNo log output for 30+ minutes during market hours.\nRestarting as precaution..."
        systemctl restart "$SERVICE"
        echo "[$TIMESTAMP] Restarted (stuck)" >> "$LOG"
    fi
fi

echo "[$TIMESTAMP] Health check OK — service running" >> "$LOG"
exit 0
