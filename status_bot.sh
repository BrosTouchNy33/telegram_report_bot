#!/bin/bash
APP_DIR="/Users/macos/Downloads/telegram_report_bot"
PID_FILE="$APP_DIR/run/auto-sam.pid"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if ps -p "$PID" >/dev/null 2>&1; then
    echo "Running (PID $PID)"
    exit 0
  fi
fi

# Fallback check
if pgrep -f "$APP_DIR/bot.py" >/dev/null 2>&1; then
  echo "Running (pgrep match)"
  exit 0
fi

echo "Not running"
exit 1
