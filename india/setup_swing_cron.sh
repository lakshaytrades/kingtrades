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

SWING_LINE="40 9 * * 1-5 cd $ROOT && bash india/run_swing.sh $MODE >> logs/cron_swing.log 2>&1"
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
echo "  Daily: 15:10 IST it manages exits + looks for one -5% down-day entry."
echo "  LIVE trades only if .env has INDIA_SWING_VALIDATED=true (gate stays until"
echo "  swing_validate.py + a paper pilot confirm)."
