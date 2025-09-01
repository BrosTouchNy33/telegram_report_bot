#!/usr/bin/env bash
set -euo pipefail

# Use the project’s venv Python if available, otherwise fall back to system python3
if [[ -x ".venv/bin/python" ]]; then
  PY="./.venv/bin/python"
else
  PY="$(command -v python3)"
fi

# Resolve this script’s directory, then point to webapp/app.py absolutely
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "Starting web UI on http://127.0.0.1:8080 …"
exec "$PY" "$ROOT/webapp/app.py"
