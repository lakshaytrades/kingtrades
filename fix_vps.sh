#!/usr/bin/env bash
# fix_vps.sh — One-shot VPS repair script for KingTrades bot
# Run once as root (or with sudo) to reset git, install deps,
# create/fix systemd services, and send a Telegram confirmation.
#
# Usage: bash fix_vps.sh

set -euo pipefail

# ─────────────────────────────────────────────────────────────
# 1. Find the bot directory
# ─────────────────────────────────────────────────────────────
BOT_DIR=""
for candidate in /opt/kingtrades /root/kingtrades ~/kingtrades; do
    if [ -d "$candidate" ] && [ -f "$candidate/main.py" ]; then
        BOT_DIR="$candidate"
        break
    fi
done

if [ -z "$BOT_DIR" ]; then
    echo "[fix_vps] ERROR: Cannot find bot directory (tried /opt/kingtrades /root/kingtrades ~/kingtrades)"
    echo "[fix_vps] Please cd to your bot directory and re-run."
    exit 1
fi

echo "[fix_vps] Bot directory: $BOT_DIR"
cd "$BOT_DIR"

# ─────────────────────────────────────────────────────────────
# 2. Git fetch and reset to origin branch
# ─────────────────────────────────────────────────────────────
TARGET_BRANCH="claude/nse-momentum-groww-bot-hvkv9"
echo "[fix_vps] Fetching latest code from origin..."
git fetch origin

if git ls-remote --exit-code origin "$TARGET_BRANCH" > /dev/null 2>&1; then
    echo "[fix_vps] Resetting to origin/$TARGET_BRANCH..."
    git checkout -B "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
    git reset --hard "origin/$TARGET_BRANCH"
else
    echo "[fix_vps] Branch $TARGET_BRANCH not found on origin — staying on current branch"
    git reset --hard
fi
echo "[fix_vps] Git reset complete."

# ─────────────────────────────────────────────────────────────
# 3. Python dependencies
# ─────────────────────────────────────────────────────────────
echo "[fix_vps] Installing/upgrading Python dependencies..."
# Always use --break-system-packages on this VPS (Python 3.12 Debian externally-managed)
PIP="pip3 --quiet --break-system-packages"
$PIP install --upgrade pip 2>/dev/null || true
if ! python3 -c "import pandas_ta" 2>/dev/null; then
    echo "[fix_vps] Installing pandas-ta..."
    $PIP install pandas-ta
fi
if [ -f "$BOT_DIR/requirements.txt" ]; then
    $PIP install -r "$BOT_DIR/requirements.txt" && echo "[fix_vps] requirements.txt OK."
fi

# ─────────────────────────────────────────────────────────────
# 4. Systemd main service
# ─────────────────────────────────────────────────────────────
SERVICE_FILE="/etc/systemd/system/kingtrades.service"
if [ ! -f "$SERVICE_FILE" ]; then
    echo "[fix_vps] Creating $SERVICE_FILE..."
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=KingTrades US Momentum Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/python3 $BOT_DIR/main.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    echo "[fix_vps] kingtrades.service created."
else
    echo "[fix_vps] kingtrades.service already exists — skipping."
fi

# ─────────────────────────────────────────────────────────────
# 5. Autodeploy timer and service
# ─────────────────────────────────────────────────────────────
AUTODEPLOY_TIMER="/etc/systemd/system/kingtrades-deploy.timer"
AUTODEPLOY_SERVICE="/etc/systemd/system/kingtrades-deploy.service"

if [ ! -f "$AUTODEPLOY_TIMER" ]; then
    echo "[fix_vps] Creating $AUTODEPLOY_TIMER..."
    cat > "$AUTODEPLOY_TIMER" << EOF
[Unit]
Description=KingTrades Auto-Deploy Timer (hourly code update check)

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Unit=kingtrades-deploy.service

[Install]
WantedBy=timers.target
EOF
    echo "[fix_vps] kingtrades-deploy.timer created."
else
    echo "[fix_vps] kingtrades-deploy.timer already exists — skipping."
fi

if [ ! -f "$AUTODEPLOY_SERVICE" ]; then
    echo "[fix_vps] Creating $AUTODEPLOY_SERVICE..."
    cat > "$AUTODEPLOY_SERVICE" << EOF
[Unit]
Description=KingTrades Auto-Deploy (git pull + restart)
After=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=$BOT_DIR
ExecStart=/bin/bash $BOT_DIR/autodeploy.sh
StandardOutput=journal
StandardError=journal
EOF
    echo "[fix_vps] kingtrades-deploy.service created."
else
    echo "[fix_vps] kingtrades-deploy.service already exists — skipping."
fi

# ─────────────────────────────────────────────────────────────
# 6. Enable and start services
# ─────────────────────────────────────────────────────────────
echo "[fix_vps] Reloading systemd daemon..."
systemctl daemon-reload

echo "[fix_vps] Enabling kingtrades.service..."
systemctl enable kingtrades.service

echo "[fix_vps] Enabling kingtrades-deploy.timer..."
systemctl enable kingtrades-deploy.timer

echo "[fix_vps] Starting/restarting kingtrades.service..."
systemctl restart kingtrades.service

echo "[fix_vps] Starting kingtrades-deploy.timer..."
systemctl start kingtrades-deploy.timer

echo "[fix_vps] Services started."

# ─────────────────────────────────────────────────────────────
# 7. Show last 20 log lines
# ─────────────────────────────────────────────────────────────
echo ""
echo "[fix_vps] ===== Last 20 journal lines for kingtrades.service ====="
journalctl -u kingtrades.service -n 20 --no-pager 2>/dev/null || true
echo "[fix_vps] ========================================================="

# ─────────────────────────────────────────────────────────────
# 8. Send Telegram confirmation (reads credentials from .env via python3-dotenv)
# ─────────────────────────────────────────────────────────────
echo "[fix_vps] Sending Telegram confirmation..."
python3 - << 'PYEOF'
import os
import sys

# Load .env without printing any credentials
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        # Try parent and current directory
        for _p in [".env", os.path.expanduser("~/.env")]:
            if os.path.exists(_p):
                env_path = _p
                break
    load_dotenv(env_path, override=False)
except ImportError:
    # dotenv not installed — try reading manually (no credential printing)
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

if not token or not chat_id:
    print("[fix_vps] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping notification")
    sys.exit(0)

try:
    import urllib.request
    import urllib.parse
    import json
    import subprocess

    # Get git commit hash for the message
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        commit = "unknown"

    msg = (
        "✅ <b>fix_vps.sh ran successfully</b>\n"
        f"Bot directory fixed and services restarted.\n"
        f"Git commit: <code>{commit}</code>\n"
        "kingtrades.service: restarted\n"
        "kingtrades-deploy.timer: active\n"
        "Dependencies: verified ✓"
    )
    payload = json.dumps({
        "chat_id":    chat_id,
        "text":       msg,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        if result.get("ok"):
            print("[fix_vps] Telegram notification sent successfully.")
        else:
            print(f"[fix_vps] Telegram API error: {result.get('description', 'unknown')}")
except Exception as e:
    print(f"[fix_vps] Telegram notification failed: {e}")
PYEOF

echo ""
echo "[fix_vps] Done. Bot is running. Check status with: systemctl status kingtrades.service"
