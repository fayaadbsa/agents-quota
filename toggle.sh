#!/usr/bin/env bash
PIDFILE="/tmp/claude_limits_popup.pid"

if [ -f "$PIDFILE" ]; then
    pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null
        rm -f "$PIDFILE"
        exit 0
    fi
    rm -f "$PIDFILE"
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$DIR/popup_card.py" >/dev/null 2>&1 &
echo $! > "$PIDFILE"
