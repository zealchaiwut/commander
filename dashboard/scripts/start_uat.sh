#!/usr/bin/env bash
# start_uat.sh — Start the UAT dashboard on port 8001.
#
# UAT_DIR is derived from this script's location via git rev-parse:
#   <project_dir>/uat/<repo_name>/dashboard
# where <project_dir> is the parent of the main repo clone and
# <repo_name> is the basename of the main repo root.
#
# Standard layout:
#   ~/dev/<project>/               ← PROJECT_DIR
#     <repo_name>/                 ← MAIN_REPO  (PRD clone, master branch)
#       dashboard/scripts/         ← this script lives here
#     uat/
#       <repo_name>/               ← UAT clone  (develop branch)
#         dashboard/               ← UAT_DIR
#
# Writes a PID file to <UAT_DIR>/uat.pid.
# Logs are written to <UAT_DIR>/uat.log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
PROJECT_DIR="$(dirname "$MAIN_REPO")"
REPO_NAME="$(basename "$MAIN_REPO")"

UAT_DIR="$PROJECT_DIR/uat/$REPO_NAME/dashboard"
VENV="$UAT_DIR/venv"
PID_FILE="$UAT_DIR/uat.pid"
LOG_FILE="$UAT_DIR/uat.log"
PORT=8001

echo "=== Starting UAT dashboard (port $PORT) ==="

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -d "$UAT_DIR" ]; then
    echo "ERROR: UAT directory not found at $UAT_DIR. Create it via:"
    echo "  cd $PROJECT_DIR && git clone https://github.com/zealchaiwut/commander.git uat/$REPO_NAME && cd uat/$REPO_NAME && git checkout develop"
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
cd "$UAT_DIR"
ENVIRONMENT=uat "$VENV/bin/uvicorn" server:app --host 0.0.0.0 --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "UAT dashboard started (PID $PID, port $PORT)."
echo "Logs: $LOG_FILE"
echo "PID file: $PID_FILE"
