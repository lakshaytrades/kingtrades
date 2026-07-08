#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_swing_cron.sh — one command to schedule the SWING pilot on the VPS.
#
#   bash india/setup_swing_cron.sh              # schedules PAPER daily run
#   SWING_MODE=live bash india/setup_swing_cron.sh   # schedules LIVE daily run
#
# Installs (idempotent):
#   * weekday 09:40 UTC (= 15:10 IST): run_swing.sh — token refresh + one swing
#     run (manage exits + scan for a new -5% entry). Flat by Friday, no weekends.
#   * @reboot: telegram token bot (if not already scheduled).
#
# LIVE only actually trades if INDIA_SWING_VALIDATED=true is ALSO set in .env —
# do not set that until swing_validate.py + a paper pilot confirm the edge.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT" || exit 1
mkdir -p logs
MODE="${SWING_MODE:-paper}"

# ── INDIAN TIME, END TO END ──────────────────────────────────────────────────
# Set the SERVER clock to Asia/Kolkata so cron, logs and `date` all speak IST —
# the schedule below is written directly as 15:10 INDIAN time (market hours).
# cron caches its timezone, so it must be restarted after the change.
if command -v timedatectl >/dev/null 2>&1; then
  CURRENT_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  if [ "$CURRENT_TZ" != "Asia/Kolkata" ]; then
    timedatectl set-timezone Asia/Kolkata \
      && echo "  + server timezone set to Asia/Kolkata (was: ${CURRENT_TZ:-unknown})" \
      || echo "  ! could not set timezone (need root) — cron line below assumes IST clock"
    (systemctl restart cron 2>/dev/null || systemctl restart crond 2>/dev/null \
      || service cron restart 2>/dev/null) && echo "  + cron restarted for the new timezone"
  else
    echo "  = server already on Asia/Kolkata (IST)"
  fi
else
  echo "  ! timedatectl not found — ensure the server clock is IST, else adjust the hour"
fi

# 15:10 IST, Monday-Friday — 20 min before the NSE close, when the day's move
# is known and delivery orders still fill. The bot ALSO verifies IST itself
# (14:45-15:25 window guard) so a wrong clock can never cause a wrong-time trade.
SWING_LINE="10 15 * * 1-5 cd $ROOT && bash india/run_swing.sh $MODE >> logs/cron_swing.log 2>&1"
TG_LINE="@reboot cd $ROOT && nohup python3 india/telegram_control.py >> logs/telegram_control.log 2>&1 &"

CUR="$(crontab -l 2>/dev/null || true)"
# drop any prior swing line (so mode changes cleanly) AND ALL RETIRED INTRADAY
# schedules (run_pilot.sh morning launch + 15:03 squareoff watchdog) — the
# intraday strategy failed validation and must never auto-start again.
NEW="$(printf '%s\n' "$CUR" | grep -v 'run_swing.sh' \
       | grep -v 'run_pilot.sh' | grep -v 'squareoff_watchdog.py' || true)"
NEW="$NEW
$SWING_LINE"
if ! printf '%s\n' "$CUR" | grep -Fq 'telegram_control.py'; then
  NEW="$NEW
$TG_LINE"
fi
printf '%s\n' "$NEW" | sed '/^$/d' | crontab -
if ! pgrep -f telegram_control.py >/dev/null 2>&1; then
  nohup python3 india/telegram_control.py >> logs/telegram_control.log 2>&1 &
fi

echo "  Swing cron installed (mode=$MODE). Crontab now:"
crontab -l | sed 's/^/    /'
echo
echo "  Server time now : $(date '+%A %H:%M %Z')"
echo "  Daily: 15:10 IST (Indian time, directly) — manages exits + scans for a"
echo "  -5% down-day entry. LIVE trades only if .env has INDIA_SWING_VALIDATED=true."
