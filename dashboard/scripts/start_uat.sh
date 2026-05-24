#!/usr/bin/env bash
# start_uat.sh — Start the UAT dashboard on port 8001 using ~/commander/dashboard-uat (develop branch).
#
# Writes a PID file to ~/commander/dashboard-uat/dashboard/uat.pid.
# Logs are written to ~/commander/dashboard-uat/dashboard/uat.log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMANDER_ROOT="$(cd "$DASHBOARD_DIR/.." && pwd)"

UAT_DIR="$COMMANDER_ROOT/dashboard-uat"
UAT_DASHBOARD="$UAT_DIR/dashboard"
VENV="$UAT_DASHBOARD/venv"
PID_FILE="$UAT_DASHBOARD/uat.pid"
LOG_FILE="$UAT_DASHBOARD/uat.log"
PORT=8001

echo "=== Starting UAT dashboard (port $PORT) ==="

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -d "$UAT_DIR" ]; then
    echo "ERROR: UAT directory not found: $UAT_DIR"
    echo "Run setup_uat_env.sh first."
    exit 1
fi

if [ ! -f "$VENV/bin/uvicorn" ]; then
    echo "ERROR: venv not found at $VENV — run setup_uat_env.sh first."
    exit 1
fi

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "UAT is already running (PID $OLD_PID). Use stop_all.sh to stop it first."
        exit 0
    else
        echo "Stale PID file found — removing."
        rm -f "$PID_FILE"
    fi
fi

# ── Launch uvicorn in background ──────────────────────────────────────────────
cd "$UAT_DASHBOARD"
ENVIRONMENT=uat "$VENV/bin/uvicorn" server:app --host 0.0.0.0 --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "UAT dashboard started (PID $PID, port $PORT)."
echo "Logs: $LOG_FILE"
echo "PID file: $PID_FILE"
