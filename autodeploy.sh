#!/bin/bash
# autodeploy.sh — Auto-pull latest code from GitHub, restart bot if changed
# Runs every 5 minutes via systemd timer.
# Zero manual work — any code fix I push is live on your VPS within 5 minutes.

# Find bot directory — works whether installed at /opt or /root
INSTALL_DIR=""
for _d in /opt/kingtrades /root/kingtrades ~/kingtrades; do
    if [ -d "$_d" ] && [ -f "$_d/main.py" ]; then
        INSTALL_DIR="$_d"
        break
    fi
done
[ -z "$INSTALL_DIR" ] && exit 1

BRANCH="claude/nse-momentum-groww-bot-hvkv9"
SERVICE="kingtrades"
LOG="/var/log/kingtrades-deploy.log"

cd "$INSTALL_DIR" || exit 1

BEFORE=$(git rev-parse HEAD 2>/dev/null)

# Pull latest silently
git fetch origin "$BRANCH" --quiet 2>/dev/null
git reset --hard "origin/$BRANCH" --quiet 2>/dev/null

AFTER=$(git rev-parse HEAD 2>/dev/null)

# Always kill orphan processes and clean PID — prevents "another instance running" loop
pkill -f "python3 main.py" 2>/dev/null || true
pkill -f "python3 -m main" 2>/dev/null || true
rm -f "$INSTALL_DIR/logs/kingtrades.pid" 2>/dev/null || true
sleep 2

if [ "$BEFORE" = "$AFTER" ]; then
    # No new code — but still make sure the service is running
    if ! systemctl is-active --quiet "$SERVICE"; then
        systemctl restart "$SERVICE" 2>/dev/null || true
    fi
    exit 0
fi

TIMESTAMP=$(TZ="America/New_York" date '+%Y-%m-%d %H:%M:%S ET')
SHORT_HASH=$(echo "$AFTER" | cut -c1-7)
echo "[$TIMESTAMP] Deployed: $SHORT_HASH (was $(echo "$BEFORE" | cut -c1-7))" >> "$LOG"

# Install any new/changed dependencies quietly
pip3 --quiet --break-system-packages install -r requirements.txt 2>/dev/null \
    || pip3 --quiet install -r requirements.txt 2>/dev/null \
    || true

# Restart the trading bot via systemd
systemctl restart "$SERVICE" 2>/dev/null || true
echo "[$TIMESTAMP] Service restarted OK" >> "$LOG"

# Telegram notification
if [ -f "$INSTALL_DIR/.env" ]; then
    BOT_TOKEN=$(grep "^TELEGRAM_BOT_TOKEN=" "$INSTALL_DIR/.env" | cut -d= -f2 | tr -d '"'"'"' ')
    CHAT_ID=$(grep "^TELEGRAM_CHAT_ID=" "$INSTALL_DIR/.env" | cut -d= -f2 | tr -d '"'"'"' ')
    if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
        MSG=$(printf "✅ <b>Bot auto-updated</b>\nCode: <code>%s</code>\nRestarted: %s" "$SHORT_HASH" "$TIMESTAMP")
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CHAT_ID}" \
            -d "parse_mode=HTML" \
            -d "text=${MSG}" \
            > /dev/null 2>&1
    fi
fi
