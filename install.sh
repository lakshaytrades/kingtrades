#!/bin/bash
# SataVector — One-command fresh install
set -e

REPO="https://github.com/lakshaytrades/kingtrades"
BRANCH="claude/nse-momentum-groww-bot-hvkv9"
INSTALL_DIR="/home/user/kingtrades"

echo "=== SataVector Installer ==="

# 1. Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "[1/4] Updating existing repo..."
    cd "$INSTALL_DIR"
    git fetch origin "$BRANCH"
    git reset --hard origin/"$BRANCH"
else
    echo "[1/4] Cloning repo..."
    rm -rf "$INSTALL_DIR"
    git clone -b "$BRANCH" "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 2. Install Python packages
echo "[2/4] Installing Python packages..."
pip3 install --break-system-packages -r requirements.txt --quiet 2>/dev/null \
    || pip3 install -r requirements.txt --quiet 2>/dev/null \
    || python3 -m pip install -r requirements.txt --break-system-packages --quiet 2>/dev/null \
    || { echo "Trying venv..."; python3 -m venv venv && venv/bin/pip install -r requirements.txt --quiet; }

# 3. Create .env if missing
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "[3/4] Creating .env from template..."
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo ">>> EDIT YOUR .env: nano $INSTALL_DIR/.env"
else
    echo "[3/4] .env already exists — skipping"
fi

# 4. Create logs dir
mkdir -p "$INSTALL_DIR/logs" "$INSTALL_DIR/data"
echo "[4/4] Done."

echo ""
echo "=============================="
echo "  SataVector installed OK"
echo "=============================="
echo ""
echo "Next: fill in your credentials:"
echo "  nano $INSTALL_DIR/.env"
echo ""
echo "Then start the bot:"
echo "  cd $INSTALL_DIR && bash start.sh"
