#!/bin/bash
set -euo pipefail
APP_DIR="/Users/macos/Downloads/telegram_report_bot"
PID_FILE="$APP_DIR/run/auto-sam.pid"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if ps -p "$PID" >/dev/null 2>&1; then
    kill "$PID" || true
    sleep 1
  fi
  rm -f "$PID_FILE"
  echo "Stopped."
else
  # fallback: kill by pattern if PID missing
  pkill -f "$APP_DIR/bot.py" || true
  echo "Stopped (best-effort)."
fi
