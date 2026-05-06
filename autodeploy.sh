#!/bin/bash
# autodeploy.sh — Auto-pull latest code from GitHub, restart bot if changed
# Runs every 5 minutes via systemd timer.
# Zero manual work — any code fix I push is live on your VPS within 5 minutes.

INSTALL_DIR="/opt/kingtrades"
BRANCH="claude/nse-momentum-groww-bot-hvkv9"
SERVICE="kingtrades"
LOG="/var/log/kingtrades-deploy.log"

cd "$INSTALL_DIR" || exit 1

BEFORE=$(git rev-parse HEAD 2>/dev/null)

# Pull latest silently
git fetch origin "$BRANCH" --quiet 2>/dev/null
git reset --hard "origin/$BRANCH" --quiet 2>/dev/null

AFTER=$(git rev-parse HEAD 2>/dev/null)

if [ "$BEFORE" = "$AFTER" ]; then
    exit 0  # No changes — nothing to do
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S IST' --date='TZ="Asia/Kolkata"' 2>/dev/null || date '+%Y-%m-%d %H:%M:%S')
SHORT_HASH=$(echo "$AFTER" | cut -c1-7)
echo "[$TIMESTAMP] Deployed: $SHORT_HASH (was $( echo "$BEFORE" | cut -c1-7))" >> "$LOG"

# Install any new/changed dependencies quietly
if source venv/bin/activate 2>/dev/null; then
    pip install -r requirements.txt --quiet --no-input 2>/dev/null
fi

# Restart the trading bot
systemctl restart "$SERVICE"
echo "[$TIMESTAMP] Service restarted OK" >> "$LOG"

# Telegram notification
if [ -f "$INSTALL_DIR/.env" ]; then
    BOT_TOKEN=$(grep "^TELEGRAM_BOT_TOKEN=" "$INSTALL_DIR/.env" | cut -d= -f2 | tr -d '"'"'"' ')
    CHAT_ID=$(grep "^TELEGRAM_CHAT_ID=" "$INSTALL_DIR/.env" | cut -d= -f2 | tr -d '"'"'"' ')
    if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
        MSG=$(printf "✅ <b>Bot auto-updated</b>\nNew code deployed: <code>%s</code>\nBot restarted at %s" "$SHORT_HASH" "$TIMESTAMP")
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CHAT_ID}" \
            -d "parse_mode=HTML" \
            -d "text=${MSG}" \
            > /dev/null 2>&1
    fi
fi
