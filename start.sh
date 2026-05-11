#!/bin/bash
# Moomoo Alert Monitor — Auto-start script
# Launches OpenD (if needed) and starts the monitor.
# Designed to run via crontab at 9:00 AM EST on weekdays.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/monitor_$(date +%Y-%m-%d).log"
PID_FILE="$SCRIPT_DIR/monitor.pid"
OPEND_APP="/Applications/moomoo_OpenD.app"

mkdir -p "$LOG_DIR"

echo "$(date) — Starting Moomoo Alert System..." >> "$LOG_FILE"

# ─── Kill stale monitor if running ────────────────────────────
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "$(date) — Killing stale monitor (PID $OLD_PID)" >> "$LOG_FILE"
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
    fi
    rm -f "$PID_FILE"
fi

# ─── Start OpenD if not running ───────────────────────────────
if ! pgrep -f "moomoo_OpenD" > /dev/null; then
    echo "$(date) — Starting OpenD..." >> "$LOG_FILE"
    open "$OPEND_APP"
    # Wait for OpenD to be ready (up to 30s)
    for i in $(seq 1 30); do
        if nc -z 127.0.0.1 11111 2>/dev/null; then
            echo "$(date) — OpenD ready after ${i}s" >> "$LOG_FILE"
            break
        fi
        sleep 1
    done
    if ! nc -z 127.0.0.1 11111 2>/dev/null; then
        echo "$(date) — ❌ OpenD failed to start!" >> "$LOG_FILE"
        exit 1
    fi
else
    echo "$(date) — OpenD already running" >> "$LOG_FILE"
fi

# ─── Start monitor ────────────────────────────────────────────
echo "$(date) — Starting monitor..." >> "$LOG_FILE"

cd "$SCRIPT_DIR"
# Use venv python (Mac Mini setup)
PYTHON3="$SCRIPT_DIR/venv/bin/python3"
nohup "$PYTHON3" monitor.py >> "$LOG_FILE" 2>&1 &
MONITOR_PID=$!
echo "$MONITOR_PID" > "$PID_FILE"

echo "$(date) — Monitor started (PID $MONITOR_PID)" >> "$LOG_FILE"
echo "Monitor PID: $MONITOR_PID | Log: $LOG_FILE"
