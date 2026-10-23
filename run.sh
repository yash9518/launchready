#!/usr/bin/env bash
# One-command start on macOS / Linux: creates a virtual environment on first run, then starts the app.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
echo "LaunchReady is starting on http://localhost:8000"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
