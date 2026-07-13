#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_cron.sh — ONE command to make the pilot fully automatic on the VPS.
#
#   bash india/setup_cron.sh
#
# Installs (idempotent — safe to run again, never duplicates):
#   1. Weekday 03:35 UTC (= 09:05 IST) : run_pilot.sh live
#        -> refreshes the Upstox token automatically, then starts the LIVE pilot.
#           The pilot waits for 9:30 IST itself, trades, squares off 15:25, exits.
#   2. @reboot : telegram_control.py
#        -> the token bot (/token /settoken /run) survives VPS restarts.
#   3. Starts telegram_control.py right now if it isn't already running.
#
# The VPS clock is UTC; all trading logic inside the bot is IST (Asia/Kolkata).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
mkdir -p logs

# RETIRED (2026-07-08): this schedules the intraday pilot (run_pilot.sh ->
# live_pilot.py), which failed honest-fill validation and now refuses to trade
# real money on its own (see live_pilot.py's retirement gate). The current live
# system is the swing pilot -- use setup_swing_cron.sh instead, which also
# strips any leftover run_pilot.sh/squareoff_watchdog.py cron lines this script
# may have installed previously.
if [ "${INDIA_ALLOW_RETIRED_INTRADAY:-}" != "true" ]; then
  echo "setup_cron.sh is RETIRED -- it schedules the intraday pilot, which failed"
  echo "honest validation and must not run unattended. Use setup_swing_cron.sh"
  echo "instead (the current live system)."
  echo "Research override: INDIA_ALLOW_RETIRED_INTRADAY=true bash india/setup_cron.sh"
  exit 1
fi

PILOT_LINE="35 3 * * 1-5 cd $ROOT && bash india/run_pilot.sh live >> logs/cron_pilot.log 2>&1"
TG_LINE="@reboot cd $ROOT && nohup python3 india/telegram_control.py >> logs/telegram_control.log 2>&1 &"
# 15:03 IST safety net: flatten anything the pilot failed to square at 15:00,
# BEFORE Upstox's ~15:12 order cutoff / ~15:15 RMS auto-square (which CHARGES).
WD_LINE="33 9 * * 1-5 cd $ROOT && python3 india/squareoff_watchdog.py >> logs/cron_watchdog.log 2>&1"

CURRENT="$(crontab -l 2>/dev/null || true)"
NEW="$CURRENT"

if ! printf '%s\n' "$CURRENT" | grep -Fq "run_pilot.sh live"; then
  NEW="$NEW
$PILOT_LINE"
  echo "  + added: weekday 09:05 IST -> token refresh + LIVE pilot"
else
  echo "  = already present: morning pilot launch"
fi

if ! printf '%s\n' "$CURRENT" | grep -Fq "squareoff_watchdog.py"; then
  NEW="$NEW
$WD_LINE"
  echo "  + added: weekday 15:03 IST -> square-off watchdog (anti-charge safety net)"
else
  echo "  = already present: square-off watchdog"
fi

if ! printf '%s\n' "$CURRENT" | grep -Fq "telegram_control.py"; then
  NEW="$NEW
$TG_LINE"
  echo "  + added: @reboot -> telegram token bot"
else
  echo "  = already present: telegram bot @reboot"
fi

printf '%s\n' "$NEW" | sed '/^$/d' | crontab - || { echo "ERROR: crontab install failed"; exit 1; }

# start the telegram bot now if it isn't running (has its own single-instance lock)
if ! pgrep -f telegram_control.py >/dev/null 2>&1; then
  nohup python3 india/telegram_control.py >> logs/telegram_control.log 2>&1 &
  echo "  + telegram token bot started (pid $!)"
else
  echo "  = telegram token bot already running"
fi

echo
echo "  DONE. Your crontab is now:"
crontab -l | sed 's/^/    /'
echo
echo "  Daily life: nothing. Cron refreshes the token and starts the pilot each"
echo "  weekday at 09:05 IST. If auto-login ever fails, Telegram: /token, or"
echo "  /settoken <redirect-url>, then /run."
