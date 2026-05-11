#!/bin/bash
# Stop the Moomoo Alert Monitor gracefully.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/monitor.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping monitor (PID $PID)..."
        kill "$PID"
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            echo "Force killing..."
            kill -9 "$PID" 2>/dev/null || true
        fi
        echo "Monitor stopped."
    else
        echo "Monitor not running (stale PID file)."
    fi
    rm -f "$PID_FILE"
else
    # Try finding by process name
    PIDS=$(pgrep -f "python3 monitor.py" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "Killing monitor processes: $PIDS"
        kill $PIDS 2>/dev/null || true
    else
        echo "No monitor running."
    fi
fi
