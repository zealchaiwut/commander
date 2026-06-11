#!/usr/bin/env bash
# start_prd.sh — Start the PRD dashboard on port 8000 using apps/dashboard/ (master branch).
#
# Writes a PID file to apps/dashboard/prd.pid.
# Logs are written to apps/dashboard/prd.log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARD_DIR="$REPO_ROOT/apps/dashboard"
COMMANDER_ROOT="$REPO_ROOT"

PRD_DIR="$DASHBOARD_DIR"
VENV="$COMMANDER_ROOT/venv"
PID_FILE="$PRD_DIR/prd.pid"
LOG_FILE="$PRD_DIR/prd.log"
PORT="${PORT:-8000}"

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

# ── Agent tool PATH ───────────────────────────────────────────────────────────
# The dashboard dispatches `claude` subprocesses; the pre-dispatch doctor
# (issue #789) blocks every ticket when the CLI isn't on the server's PATH.
# Restarts from minimal shells (launch agents, CI, tool sandboxes) often lack
# the user-level install dirs, so make sure they're present before launch.
for tooldir in "$HOME/.local/bin" "$HOME/.claude/local"; do
    if [ -d "$tooldir" ] && [[ ":$PATH:" != *":$tooldir:"* ]]; then
        export PATH="$PATH:$tooldir"
    fi
done
if ! command -v claude >/dev/null 2>&1; then
    echo "WARNING: claude CLI not found in PATH — sprint dispatch will be blocked."
fi

# ── Sync pip requirements ─────────────────────────────────────────────────────
echo "Syncing pip requirements…"
"$VENV/bin/pip" install --quiet -r "$REPO_ROOT/requirements.txt"

# ── Launch uvicorn in background ──────────────────────────────────────────────
cd "$PRD_DIR"
ENVIRONMENT=prd "$VENV/bin/uvicorn" server:app --host 0.0.0.0 --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "PRD dashboard started (PID $PID, port $PORT)."
echo "Logs: $LOG_FILE"
echo "PID file: $PID_FILE"
