#!/usr/bin/env bash
# start_uat.sh — Start the UAT dashboard on port 8001.
#
# UAT_DIR is derived from this script's location:
#   SCRIPT_DIR   = <project-dir>/prd/scripts/
#   REPO_ROOT    = <project-dir>/prd/
#   PROJECT_DIR  = <project-dir>/
#   UAT_DIR      = <project-dir>/uat/apps/dashboard
#
# Standard layout:
#   ~/dev/commander/               ← PROJECT_DIR
#     prd/                         ← PRD clone (REPO_ROOT)
#       scripts/                   ← SCRIPT_DIR (this script lives here)
#     uat/
#       apps/dashboard/            ← UAT_DIR
#
# Writes a PID file to <UAT_DIR>/uat.pid.
# Logs are written to <UAT_DIR>/uat.log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Nested layout: scripts may live in prd/scripts and target ../uat, or in the
# uat clone itself when the Deploy card's working_dir is the uat checkout.
if [ -d "$REPO_ROOT/../uat/apps/dashboard" ] && [ "$(basename "$REPO_ROOT")" = "prd" ]; then
    PROJECT_DIR="$(cd "$REPO_ROOT/.." && pwd)"
    UAT_REPO="$PROJECT_DIR/uat"
    UAT_DIR="$UAT_REPO/apps/dashboard"
else
    UAT_REPO="$REPO_ROOT"
    UAT_DIR="$REPO_ROOT/apps/dashboard"
fi

VENV=""
for candidate in "$UAT_REPO/venv" "$UAT_DIR/venv" "$(cd "$REPO_ROOT/.." 2>/dev/null && pwd)/venv"; do
    if [ -n "$candidate" ] && [ -f "$candidate/bin/uvicorn" ]; then
        VENV="$candidate"
        break
    fi
done
VENV="${VENV:-$UAT_REPO/venv}"

PID_FILE="$UAT_DIR/uat.pid"
LOG_FILE="$UAT_DIR/uat.log"
PORT="${PORT:-8001}"

echo "=== Starting UAT dashboard (port $PORT) ==="

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -d "$UAT_DIR" ]; then
    echo "ERROR: UAT directory not found at $UAT_DIR. Create it via:"
    echo "  cd $PROJECT_DIR && git clone https://github.com/zealchaiwut/commander.git uat && cd uat && git checkout develop"
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

# ── Sync pip requirements ─────────────────────────────────────────────────────
echo "Syncing pip requirements…"
"$VENV/bin/pip" install --quiet -r "$UAT_REPO/requirements.txt"

# ── Ensure ENVIRONMENT=uat in .env ────────────────────────────────────────────
UAT_ENV_FILE="$UAT_DIR/.env"
if [ ! -f "$UAT_ENV_FILE" ]; then
    echo "ENVIRONMENT=uat" > "$UAT_ENV_FILE"
    echo "PORT=$PORT" >> "$UAT_ENV_FILE"
else
    if grep -q '^ENVIRONMENT=' "$UAT_ENV_FILE"; then
        sed -i.bak 's/^ENVIRONMENT=.*/ENVIRONMENT=uat/' "$UAT_ENV_FILE" && rm -f "${UAT_ENV_FILE}.bak"
    else
        echo "ENVIRONMENT=uat" >> "$UAT_ENV_FILE"
    fi
    if ! grep -q '^PORT=' "$UAT_ENV_FILE"; then
        echo "PORT=$PORT" >> "$UAT_ENV_FILE"
    fi
fi

# ── Launch uvicorn in background ──────────────────────────────────────────────
cd "$UAT_DIR"
ENVIRONMENT=uat "$VENV/bin/uvicorn" server:app --host 0.0.0.0 --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "UAT dashboard started (PID $PID, port $PORT)."
echo "Logs: $LOG_FILE"
echo "PID file: $PID_FILE"
