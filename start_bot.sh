#!/bin/bash
set -euo pipefail

# --- EDIT if your path is different ---
APP_DIR="/Users/macos/Downloads/telegram_report_bot"
PY="/Users/macos/Downloads/telegram_report_bot/.venv/bin/python"
LOG_DIR="$APP_DIR"
PID_DIR="$APP_DIR/run"
PID_FILE="$PID_DIR/auto-sam.pid"
OUT_LOG="$LOG_DIR/auto-sam.cron.out.log"
ERR_LOG="$LOG_DIR/auto-sam.cron.err.log"

# Cron has a tiny PATH; include Homebrew paths
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="en_US.UTF-8"

mkdir -p "$PID_DIR"

cd "$APP_DIR"

# If already running, exit
if [ -f "$PID_FILE" ] && ps -p "$(cat "$PID_FILE")" > /dev/null 2>&1; then
  echo "$(date '+%F %T') Already running with PID $(cat "$PID_FILE")" >> "$OUT_LOG"
  exit 0
fi

# Start in background
nohup "$PY" "$APP_DIR/bot.py" >>"$OUT_LOG" 2>>"$ERR_LOG" &
echo $! > "$PID_FILE"
echo "$(date '+%F %T') Started Auto SAM with PID $(cat "$PID_FILE")" >> "$OUT_LOG"
