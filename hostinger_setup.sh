#!/bin/bash
# ============================================================
# NSE Momentum Bot — Hostinger VPS Setup Script
# Run ONCE on a fresh Hostinger Ubuntu VPS as root.
# After this: bot runs forever, auto-updates, auto-heals.
#
# Usage: bash hostinger_setup.sh
# ============================================================
set -e

INSTALL_DIR="/opt/kingtrades"
BRANCH="claude/nse-momentum-groww-bot-hvkv9"
REPO="https://github.com/lakshaytrades/kingtrades.git"

echo ""
echo "=================================================="
echo "  NSE Momentum Bot — Full Hostinger VPS Setup"
echo "=================================================="
echo ""

# ── 1. System packages ───────────────────────────────────
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv python3-dev \
    build-essential git curl wget libssl-dev libffi-dev 2>/dev/null
echo "  OK"

# ── 2. Clone / update code ───────────────────────────────
echo "[2/8] Setting up code at $INSTALL_DIR..."
if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git reset --hard "origin/$BRANCH"
    echo "  Updated to latest"
else
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    git checkout "$BRANCH"
    echo "  Cloned"
fi
cd "$INSTALL_DIR"

# ── 3. Python virtualenv ─────────────────────────────────
echo "[3/8] Setting up Python virtualenv..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet
echo "  OK"

# ── 4. Install dependencies ──────────────────────────────
echo "[4/8] Installing Python dependencies (2-3 min)..."
pip install -r requirements.txt --quiet
echo "  OK"

# ── 5. Create required directories ──────────────────────
echo "[5/8] Creating data directories..."
mkdir -p logs/trades logs/performance data charts
chmod 755 autodeploy.sh healthcheck.sh
echo "  OK"

# ── 6. .env file ─────────────────────────────────────────
echo "[6/8] Checking .env..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo ""
    echo "  !! .env created from template."
    echo "  !! Edit it now: nano $INSTALL_DIR/.env"
    echo "  !! Required: GROWW_CLIENT_ID, GROWW_TOTP_SECRET, TELEGRAM_*"
    echo ""
else
    echo "  .env already exists"
fi

# ── 7. Install all systemd services ──────────────────────
echo "[7/8] Installing systemd services..."

# Main trading bot
cp "$INSTALL_DIR/kingtrades.service"   /etc/systemd/system/

# Auto-deploy (pulls GitHub every 5 min)
cp "$INSTALL_DIR/autodeploy.service"   /etc/systemd/system/
cp "$INSTALL_DIR/autodeploy.timer"     /etc/systemd/system/

# Health check (restarts if crashed, every 15 min)
cp "$INSTALL_DIR/healthcheck.service"  /etc/systemd/system/
cp "$INSTALL_DIR/healthcheck.timer"    /etc/systemd/system/

systemctl daemon-reload

# Enable timers (auto-start on boot)
systemctl enable autodeploy.timer
systemctl enable healthcheck.timer

# Start timers now
systemctl start autodeploy.timer
systemctl start healthcheck.timer

echo "  Services installed and timers running"

# ── 8. Start main bot ────────────────────────────────────
echo "[8/8] Starting trading bot..."
systemctl enable kingtrades

ENV_OK=false
if [ -f "$INSTALL_DIR/.env" ]; then
    HAS_TOTP=$(grep -c "^GROWW_TOTP_SECRET=." "$INSTALL_DIR/.env" 2>/dev/null || echo "0")
    HAS_TG=$(grep -c "^TELEGRAM_BOT_TOKEN=." "$INSTALL_DIR/.env" 2>/dev/null || echo "0")
    if [ "$HAS_TOTP" -gt 0 ] && [ "$HAS_TG" -gt 0 ]; then
        ENV_OK=true
    fi
fi

if [ "$ENV_OK" = "true" ]; then
    systemctl restart kingtrades
    sleep 3
    if systemctl is-active --quiet kingtrades; then
        echo "  Bot started successfully"
    else
        echo "  Bot failed to start — check: journalctl -u kingtrades -n 30"
    fi
else
    echo "  Bot NOT started — fill in .env first:"
    echo "    nano $INSTALL_DIR/.env"
    echo "    systemctl start kingtrades"
fi

echo ""
echo "=================================================="
echo "  Setup complete!"
echo "=================================================="
echo ""
echo "  What's running:"
echo "  • kingtrades.service   — the trading bot"
echo "  • autodeploy.timer     — checks GitHub every 5 min, auto-updates"
echo "  • healthcheck.timer    — checks health every 15 min, auto-restarts"
echo ""
echo "  Commands:"
echo "  Watch logs:     journalctl -u kingtrades -f"
echo "  Bot status:     systemctl status kingtrades"
echo "  Deploy status:  journalctl -u kingtrades-deploy -n 20"
echo "  Health log:     tail -f /var/log/kingtrades-health.log"
echo "  Restart bot:    systemctl restart kingtrades"
echo "  Edit .env:      nano $INSTALL_DIR/.env"
echo ""
echo "  Auto-deploy: Any code I push to GitHub is live within 5 minutes."
echo "  Auto-heal:   Bot restarts itself if it crashes."
echo ""
