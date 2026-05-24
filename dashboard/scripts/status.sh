#!/usr/bin/env bash
# status.sh — Print which Commander server (if any) is running on each port,
# along with its PID and the active git branch.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMANDER_ROOT="$(cd "$DASHBOARD_DIR/.." && pwd)"

PRD_DIR="$DASHBOARD_DIR"
UAT_DIR="$COMMANDER_ROOT/dashboard-uat"

_status_port() {
    local port="$1"
    local label="$2"
    local git_dir="$3"
    local pid_file="$4"

    printf "%-6s (port %s): " "$label" "$port"

    # Detect process on port
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)

    if [ -z "$pids" ]; then
        echo "STOPPED"
    else
        # Use PID file if it matches; otherwise use any lsof PID
        local pid
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if ! kill -0 "$pid" 2>/dev/null; then
                pid=$(echo "$pids" | head -1)
            fi
        else
            pid=$(echo "$pids" | head -1)
        fi

        # Detect git branch
        local branch="unknown"
        if [ -d "$git_dir/.git" ] || git -C "$git_dir" rev-parse --git-dir >/dev/null 2>&1; then
            branch=$(git -C "$git_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        fi

        echo "RUNNING (PID $pid, branch: $branch)"
    fi
}

echo "=== Commander server status ==="
_status_port 8000 "PRD" "$PRD_DIR"      "$PRD_DIR/prd.pid"
_status_port 8001 "UAT" "$UAT_DIR"      "$UAT_DIR/dashboard/uat.pid"
echo "================================"
