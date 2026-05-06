#!/bin/bash
# ============================================================
# NSE Momentum Bot — Hostinger VPS Setup Script
# Run once on a fresh Hostinger Ubuntu VPS (as root)
# Usage: bash hostinger_setup.sh
# ============================================================
set -e

INSTALL_DIR="/opt/kingtrades"
BRANCH="claude/nse-momentum-groww-bot-hvkv9"
REPO="https://github.com/lakshaytrades/kingtrades.git"
SERVICE_NAME="kingtrades"

echo ""
echo "=================================================="
echo "  NSE Momentum Bot — Hostinger VPS Setup"
echo "=================================================="
echo ""

# ── System packages ─────────────────────────────────────
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv python3-dev \
    build-essential git curl wget libssl-dev libffi-dev 2>/dev/null
echo "  System packages OK"

# ── Clone / update repo ──────────────────────────────────
echo "[2/7] Setting up code at $INSTALL_DIR..."
if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
    echo "  Code updated"
else
    mkdir -p "$INSTALL_DIR"
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    git checkout "$BRANCH"
    echo "  Code cloned"
fi

cd "$INSTALL_DIR"

# ── Python virtualenv ────────────────────────────────────
echo "[3/7] Setting up Python virtualenv..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet
echo "  Virtualenv OK"

# ── Dependencies ─────────────────────────────────────────
echo "[4/7] Installing Python dependencies (may take 2-3 min)..."
pip install -r requirements.txt --quiet
echo "  Dependencies OK"

# ── Directories ──────────────────────────────────────────
echo "[5/7] Creating data directories..."
mkdir -p logs/trades logs/performance data charts
echo "  Directories OK"

# ── .env file ────────────────────────────────────────────
echo "[6/7] Checking .env..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo ""
    echo "  .env created from template."
    echo "  IMPORTANT: Edit it now: nano $INSTALL_DIR/.env"
    echo "  Add your Groww and Telegram credentials."
    echo ""
else
    echo "  .env already exists — skipping"
fi

# ── systemd service ──────────────────────────────────────
echo "[7/7] Installing systemd service..."
cp "$INSTALL_DIR/kingtrades.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

# Only (re)start if .env is populated
if grep -q "GROWW_TOTP_SECRET=" "$INSTALL_DIR/.env" && \
   ! grep -q "GROWW_TOTP_SECRET=$" "$INSTALL_DIR/.env"; then
    systemctl restart "$SERVICE_NAME"
    echo "  Service started"
else
    echo "  Service NOT started — fill in .env first, then run:"
    echo "  systemctl start $SERVICE_NAME"
fi

echo ""
echo "=================================================="
echo "  Setup complete!"
echo "=================================================="
echo ""
echo "  Useful commands:"
echo "  View logs:    journalctl -u $SERVICE_NAME -f"
echo "  Status:       systemctl status $SERVICE_NAME"
echo "  Restart:      systemctl restart $SERVICE_NAME"
echo "  Stop:         systemctl stop $SERVICE_NAME"
echo "  Edit .env:    nano $INSTALL_DIR/.env"
echo ""
echo "  After editing .env:"
echo "  systemctl restart $SERVICE_NAME"
echo ""
