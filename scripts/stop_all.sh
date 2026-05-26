#!/usr/bin/env bash
# stop_all.sh — Terminate processes bound to port 8000 (PRD), 8001 (UAT), or both.
#
# Usage:
#   stop_all.sh          — stop both PRD and UAT
#   stop_all.sh prd      — stop PRD only (port 8000)
#   stop_all.sh uat      — stop UAT only (port 8001)
#
# Also cleans up PID files if present.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARD_DIR="$REPO_ROOT/apps/dashboard"
PROJECT_DIR="$(cd "$REPO_ROOT/.." && pwd)"

PRD_PID_FILE="$DASHBOARD_DIR/prd.pid"
UAT_PID_FILE="$PROJECT_DIR/uat/apps/dashboard/uat.pid"

# Parse optional argument: prd | uat | (none = both)
TARGET="${1:-all}"

_kill_port() {
    local port="$1"
    local label="$2"
    local pid_file="$3"

    echo "--- $label (port $port) ---"

    # Try the PID file first
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  Killing PID $pid (from $pid_file) …"
            kill "$pid" && echo "  Sent SIGTERM to $pid."
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" && echo "  Sent SIGKILL to $pid."
            fi
        else
            echo "  PID $pid from file is not running."
        fi
        rm -f "$pid_file"
        echo "  Removed $pid_file."
    fi

    # Also kill anything still bound to the port (belt-and-suspenders)
    local port_pids
    port_pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$port_pids" ]; then
        echo "  Found additional PIDs on port $port: $port_pids"
        # shellcheck disable=SC2086
        kill $port_pids 2>/dev/null || true
        sleep 0.5
        port_pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
        if [ -n "$port_pids" ]; then
            # shellcheck disable=SC2086
            kill -9 $port_pids 2>/dev/null || true
        fi
        echo "  Killed remaining processes on port $port."
    else
        echo "  No additional processes found on port $port."
    fi
}

echo "=== Stopping Commander servers ==="
case "$TARGET" in
    prd)
        _kill_port 8000 "PRD" "$PRD_PID_FILE"
        ;;
    uat)
        _kill_port 8001 "UAT" "$UAT_PID_FILE"
        ;;
    *)
        _kill_port 8000 "PRD" "$PRD_PID_FILE"
        _kill_port 8001 "UAT" "$UAT_PID_FILE"
        ;;
esac
echo "=== Done ==="
