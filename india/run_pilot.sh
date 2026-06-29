#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_pilot.sh — daily launcher for the ₹5,000 live measurement pilot.
#
# Usage:
#   bash india/run_pilot.sh            # PAPER (no orders) — default, safe
#   bash india/run_pilot.sh live       # LIVE ₹5k real orders (measures slippage)
#
# It (1) optionally auto-renews the Upstox token if TOTP creds are in .env,
# (2) starts live_pilot.py, (3) logs everything to logs/pilot_launch_<date>.log.
# The pilot itself enforces IST market hours, the ₹5k cap, and 15:25 squareoff,
# so it's safe to start early — it waits for 9:30 IST on its own.
#
# NOTE: the VPS clock is UTC. 9:15 IST = 03:45 UTC. For a cron schedule use UTC
# (see the README line printed at the end).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 1
mkdir -p logs
LOG="logs/pilot_launch_$(date +%F).log"
MODE="${1:-paper}"

{
  echo "================================================================"
  echo "=== pilot launch $(date -u '+%Y-%m-%d %H:%M:%S') UTC | mode=$MODE ==="
  echo "================================================================"
  # Optional Upstox token auto-renew (no-op if TOTP creds are absent).
  python3 - <<'PY' || true
import sys
sys.path.insert(0, "india")
try:
    from auth_upstox import auto_renew_token
    ok, msg = auto_renew_token()
    print(("token OK: " if ok else "token renew skipped/failed: ") + str(msg))
except Exception as e:
    print("token step error:", e)
PY
} >> "$LOG" 2>&1

if [ "$MODE" = "live" ]; then
  export INDIA_LIVE_TRADING_ENABLED=true
  echo ">>> LIVE mode — REAL orders, hard ₹5,000 cap, no leverage" | tee -a "$LOG"
else
  echo ">>> PAPER mode — no orders placed (logs signals only)" | tee -a "$LOG"
fi

echo ">>> starting live_pilot.py … (logs -> $LOG)" | tee -a "$LOG"
python3 india/live_pilot.py >> "$LOG" 2>&1
echo ">>> pilot exited $(date -u '+%H:%M:%S') UTC" | tee -a "$LOG"
