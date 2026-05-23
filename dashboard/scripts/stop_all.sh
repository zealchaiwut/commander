#!/usr/bin/env bash
# stop_all.sh — Terminate any processes bound to ports 8000 and 8001.
#
# Also cleans up PID files if present.

set -uo pipefail

PRD_PID_FILE="$HOME/commander/dashboard/prd.pid"
UAT_PID_FILE="$HOME/commander/dashboard-uat/dashboard/uat.pid"

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
_kill_port 8000 "PRD" "$PRD_PID_FILE"
_kill_port 8001 "UAT" "$UAT_PID_FILE"
echo "=== Done ==="
