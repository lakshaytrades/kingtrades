#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_swing.sh — daily launcher for the SWING pilot (rev:buy-5%-day).
#
#   bash india/run_swing.sh            # PAPER (safe) — default
#   bash india/run_swing.sh live       # LIVE (only if INDIA_SWING_VALIDATED=true)
#
# Runs ONCE near the close: (1) refresh the Upstox token, (2) run swing_pilot.py
# which manages held positions (exit on target/stop/Friday) and scans for a new
# -5% down-day entry. Delivery orders, flat by Friday, no weekend holds.
#
# Cron (installed by setup_swing_cron.sh): 10 15 * * 1-5 in INDIAN time (the
# installer sets the server clock to Asia/Kolkata) = 15:10 IST daily.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 1
mkdir -p logs
LOG="logs/swing_launch_$(date +%F).log"
MODE="${1:-paper}"

{
  echo "=== swing launch $(date -u '+%F %H:%M:%S') UTC | mode=$MODE ==="
  python3 india/auto_login_totp.py \
    || python3 india/auto_login_upstox.py \
    || echo "AUTO-LOGIN FAILED — send /token on Telegram or run get_upstox_token.py"
} >> "$LOG" 2>&1

if [ "$MODE" = "live" ]; then
  export INDIA_LIVE_TRADING_ENABLED=true
  echo ">>> LIVE swing (needs INDIA_SWING_VALIDATED=true too, else stays PAPER)" | tee -a "$LOG"
else
  # AUDIT FIX: explicitly force paper — a stale INDIA_LIVE_TRADING_ENABLED=true
  # left in .env must never make the paper-scheduled cron trade real money
  # (exported env beats .env because load_dotenv does not override).
  export INDIA_LIVE_TRADING_ENABLED=false
  echo ">>> PAPER swing — no orders placed" | tee -a "$LOG"
fi

echo ">>> running swing_pilot.py …" | tee -a "$LOG"
python3 india/swing_pilot.py >> "$LOG" 2>&1
echo ">>> swing run done $(date -u '+%H:%M:%S') UTC" | tee -a "$LOG"
