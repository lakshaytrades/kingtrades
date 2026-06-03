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

if [ "$BEFORE" = "$AFTER" ]; then
    # No new code — only restart if the service crashed (do NOT kill a healthy bot)
    if ! systemctl is-active --quiet "$SERVICE"; then
        systemctl restart "$SERVICE" 2>/dev/null || true
    fi
else
    # New code detected — safe to kill and redeploy
    pkill -f "python3 main.py" 2>/dev/null || true
    pkill -f "python3 -m main" 2>/dev/null || true
    rm -f "$INSTALL_DIR/logs/kingtrades.pid" 2>/dev/null || true
    sleep 2

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
fi

# ── Push live status to vps-status branch for remote monitoring ──────────
# This runs every 5 min regardless of code changes.
# External monitor reads live_status.txt from vps-status branch via GitHub API.
_TODAY_LOG="$INSTALL_DIR/logs/trading_$(TZ='America/New_York' date +%Y-%m-%d).log"
_TS_ET=$(TZ='America/New_York' date '+%Y-%m-%d %H:%M:%S ET')
_ET_HOUR=$(TZ='America/New_York' date '+%H')

# Only push status during market hours (8 AM - 5 PM ET) to save API calls
if [ "$_ET_HOUR" -ge 8 ] && [ "$_ET_HOUR" -le 17 ]; then
    if [ -f "$_TODAY_LOG" ]; then
        # Extract last 60 lines, keep only ERROR/WARNING/CRITICAL/FILLED/SIGNAL/BLOCKED lines
        _ERRORS=$(tail -200 "$_TODAY_LOG" 2>/dev/null | grep -E "ERROR|WARNING|CRITICAL|BLOCKED|REJECTED|failed|exception" | tail -20 || true)
        _SIGNALS=$(tail -200 "$_TODAY_LOG" 2>/dev/null | grep -E "FILLED|SCALP|SIGNAL|BarCache refreshed|PAPER|BRACKET" | tail -15 || true)
        _STATUS_CONTENT="=== KingTrades Live Status ===
Timestamp: $_TS_ET
Log: $_TODAY_LOG

=== ERRORS/WARNINGS (last 20) ===
${_ERRORS:-none}

=== SIGNALS/FILLS (last 15) ===
${_SIGNALS:-none}

=== LAST 30 LOG LINES ===
$(tail -30 "$_TODAY_LOG" 2>/dev/null || echo 'log not found')"
    else
        _STATUS_CONTENT="=== KingTrades Live Status ===
Timestamp: $_TS_ET
Log file not found: $_TODAY_LOG
Bot may not have started yet or log dir missing."
    fi

    # Push live_status.txt via GitHub Contents API (same token as git pull, no push-creds needed)
    cd "$INSTALL_DIR" || exit 0

    # Extract GitHub PAT from remote URL (https://TOKEN@github.com/... or https://TOKEN:x-oauth-basic@...)
    _REMOTE_URL=$(git remote get-url origin 2>/dev/null || true)
    _GH_TOKEN=$(printf '%s' "$_REMOTE_URL" | sed -n 's|https://\([^:@]*\)[^@]*@github\.com.*|\1|p' 2>/dev/null || true)

    # Fallback: read from ~/.git-credentials
    if [ -z "$_GH_TOKEN" ] && [ -f "$HOME/.git-credentials" ]; then
        _GH_TOKEN=$(grep "github\.com" "$HOME/.git-credentials" 2>/dev/null \
            | sed -n 's|.*://\([^:@]*\)[^@]*@.*|\1|p' | head -1 || true)
    fi

    if [ -n "$_GH_TOKEN" ]; then
        # Base64-encode content (no line wrapping)
        _B64=$(printf '%s' "$_STATUS_CONTENT" | base64 | tr -d '\n' 2>/dev/null || true)
        # Get current file SHA for update (empty on first create)
        _FILE_SHA=$(curl -sf \
            -H "Authorization: token $_GH_TOKEN" \
            "https://api.github.com/repos/lakshaytrades/kingtrades/contents/live_status.txt?ref=vps-status" \
            2>/dev/null | grep '"sha"' | head -1 | sed 's/.*"sha": "\([^"]*\)".*/\1/' || true)
        if [ -n "$_FILE_SHA" ]; then
            _API_BODY="{\"message\":\"status: $_TS_ET\",\"content\":\"$_B64\",\"sha\":\"$_FILE_SHA\",\"branch\":\"vps-status\"}"
        else
            _API_BODY="{\"message\":\"status: $_TS_ET\",\"content\":\"$_B64\",\"branch\":\"vps-status\"}"
        fi
        curl -sf -X PUT \
            -H "Authorization: token $_GH_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$_API_BODY" \
            "https://api.github.com/repos/lakshaytrades/kingtrades/contents/live_status.txt" \
            > /dev/null 2>&1 \
            && echo "[$_TS_ET] Status pushed via API OK" >> "$LOG" \
            || echo "[$_TS_ET] Status API push FAILED" >> "$LOG"
    else
        echo "[$_TS_ET] Status push skipped: no GitHub token found in remote URL" >> "$LOG"
    fi
fi
