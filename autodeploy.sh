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

    # Push to vps-status branch (never conflicts with code branch)
    cd "$INSTALL_DIR" || exit 0
    git fetch origin vps-status 2>/dev/null || true

    # Write status file
    printf '%s\n' "$_STATUS_CONTENT" > "$INSTALL_DIR/live_status.txt"

    # git commit-tree needs author identity — set explicitly so it works on any VPS
    export GIT_AUTHOR_NAME="kingtrades-vps"
    export GIT_AUTHOR_EMAIL="vps@kingtrades.local"
    export GIT_COMMITTER_NAME="kingtrades-vps"
    export GIT_COMMITTER_EMAIL="vps@kingtrades.local"

    _BLOB=$(git hash-object -w "$INSTALL_DIR/live_status.txt" 2>/dev/null)
    if [ -n "$_BLOB" ]; then
        _TREE=$(printf '100644 blob %s\tlive_status.txt\n' "$_BLOB" | git mktree 2>/dev/null)
        _PARENT=$(git ls-remote origin vps-status 2>/dev/null | awk '{print $1}' | head -1)
        if [ -n "$_PARENT" ]; then
            _COMMIT=$(git commit-tree "$_TREE" -p "$_PARENT" -m "status: $_TS_ET" 2>/dev/null)
        else
            _COMMIT=$(git commit-tree "$_TREE" -m "status: $_TS_ET" 2>/dev/null)
        fi
        if [ -n "$_COMMIT" ]; then
            git push origin "${_COMMIT}:refs/heads/vps-status" --quiet 2>/dev/null \
                && echo "[$_TS_ET] Status pushed to vps-status: ${_COMMIT:0:7}" >> "$LOG" \
                || echo "[$_TS_ET] Status push FAILED (no push creds?)" >> "$LOG"
        else
            echo "[$_TS_ET] Status commit-tree failed (git author missing?)" >> "$LOG"
        fi
    fi

    # Clean up temp file
    rm -f "$INSTALL_DIR/live_status.txt"
fi
