#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Find running indicator.py PID
pid=$(pgrep -f "python3.*${DIR}/indicator.py" | head -n 1 || true)
if [ -z "$pid" ]; then
    pid=$(pgrep -f "indicator.py" | head -n 1 || true)
fi

if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    # Indicator is running: trigger popup window
    kill -USR1 "$pid"
else
    # Not running: start indicator in background
    nohup /usr/bin/python3 "$DIR/indicator.py" >/dev/null 2>&1 &
    sleep 0.5
    new_pid=$!
    kill -USR1 "$new_pid" 2>/dev/null || true
fi
