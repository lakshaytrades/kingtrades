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

PILOT_LINE="35 3 * * 1-5 cd $ROOT && bash india/run_pilot.sh live >> logs/cron_pilot.log 2>&1"
TG_LINE="@reboot cd $ROOT && nohup python3 india/telegram_control.py >> logs/telegram_control.log 2>&1 &"

CURRENT="$(crontab -l 2>/dev/null || true)"
NEW="$CURRENT"

if ! printf '%s\n' "$CURRENT" | grep -Fq "run_pilot.sh live"; then
  NEW="$NEW
$PILOT_LINE"
  echo "  + added: weekday 09:05 IST -> token refresh + LIVE pilot"
else
  echo "  = already present: morning pilot launch"
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
