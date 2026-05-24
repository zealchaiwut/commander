#!/usr/bin/env bash
# start_prd.sh — Start the PRD dashboard on port 8000 using ~/commander/dashboard (master branch).
#
# Writes a PID file to ~/commander/dashboard/prd.pid.
# Logs are written to ~/commander/dashboard/prd.log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMANDER_ROOT="$(cd "$DASHBOARD_DIR/.." && pwd)"

PRD_DIR="$DASHBOARD_DIR"
VENV="$PRD_DIR/venv"
PID_FILE="$PRD_DIR/prd.pid"
LOG_FILE="$PRD_DIR/prd.log"
PORT=8000

echo "=== Starting PRD dashboard (port $PORT) ==="

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -d "$PRD_DIR" ]; then
    echo "ERROR: PRD directory not found: $PRD_DIR"
    exit 1
fi

if [ ! -f "$VENV/bin/uvicorn" ]; then
    echo "ERROR: venv not found at $VENV — run the venv setup first."
    exit 1
fi

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "PRD is already running (PID $OLD_PID). Use stop_all.sh to stop it first."
        exit 0
    else
        echo "Stale PID file found — removing."
        rm -f "$PID_FILE"
    fi
fi

# ── Ensure ENVIRONMENT=prd in .env ────────────────────────────────────────────
ENV_FILE="$PRD_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ENVIRONMENT=prd" > "$ENV_FILE"
    echo "PORT=$PORT"      >> "$ENV_FILE"
else
    # Ensure ENVIRONMENT is set to prd
    if grep -q '^ENVIRONMENT=' "$ENV_FILE"; then
        sed -i.bak 's/^ENVIRONMENT=.*/ENVIRONMENT=prd/' "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
    else
        echo "ENVIRONMENT=prd" >> "$ENV_FILE"
    fi
fi

# ── Launch uvicorn in background ──────────────────────────────────────────────
cd "$PRD_DIR"
ENVIRONMENT=prd "$VENV/bin/uvicorn" server:app --host 0.0.0.0 --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "PRD dashboard started (PID $PID, port $PORT)."
echo "Logs: $LOG_FILE"
echo "PID file: $PID_FILE"
