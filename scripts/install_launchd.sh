#!/usr/bin/env bash
# install_launchd.sh — Install the Commander dashboard as a macOS LaunchAgent.
#
# What it does:
#   1. Checks for a port-8000 conflict (tmux or any other process).
#   2. Substitutes real paths into the plist template.
#   3. Copies the plist to ~/Library/LaunchAgents/ with 644 permissions.
#   4. Loads the service with launchctl.
#   5. Verifies the service is listed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_TEMPLATE="$SCRIPT_DIR/com.commander.dashboard.plist"
PLIST_LABEL="com.commander.dashboard"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
VENV_BIN="$REPO_ROOT/venv/bin"
DASHBOARD_DIR="$REPO_ROOT/apps/dashboard"
PORT=8000

echo "=== Commander LaunchAgent Installer ==="
echo "Repo root : $REPO_ROOT"
echo "Venv bin  : $VENV_BIN"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -f "$PLIST_TEMPLATE" ]; then
  echo "ERROR: plist template not found at $PLIST_TEMPLATE"
  exit 1
fi

if [ ! -f "$VENV_BIN/uvicorn" ]; then
  echo "ERROR: uvicorn not found at $VENV_BIN/uvicorn — set up the venv first."
  exit 1
fi

if [ ! -d "$DASHBOARD_DIR" ]; then
  echo "ERROR: dashboard directory not found at $DASHBOARD_DIR"
  exit 1
fi

# ── Port conflict check ────────────────────────────────────────────────────────
# Detect any process already listening on port 8000.
CONFLICT_PID=""
if command -v lsof >/dev/null 2>&1; then
  CONFLICT_PID=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
fi

if [ -n "$CONFLICT_PID" ]; then
  # Check whether any of those PIDs belong to a tmux session.
  TMUX_PIDS=""
  if command -v tmux >/dev/null 2>&1; then
    TMUX_PIDS=$(tmux list-panes -a -F "#{pane_pid}" 2>/dev/null || true)
  fi

  IS_TMUX=false
  for pid in $CONFLICT_PID; do
    if echo "$TMUX_PIDS" | grep -q "^${pid}$"; then
      IS_TMUX=true
      break
    fi
    # Also walk the parent chain — the process listening may be a child of tmux.
    ppid=$pid
    for _ in 1 2 3 4 5; do
      ppid=$(ps -o ppid= -p "$ppid" 2>/dev/null | tr -d ' ' || true)
      [ -z "$ppid" ] || [ "$ppid" = "0" ] || [ "$ppid" = "1" ] && break
      if echo "$TMUX_PIDS" | grep -q "^${ppid}$"; then
        IS_TMUX=true
        break
      fi
    done
    $IS_TMUX && break
  done

  if $IS_TMUX; then
    echo ""
    echo "WARNING: A process managed by tmux is already listening on port $PORT."
    echo "         Installing launchd alongside an active tmux session can cause a"
    echo "         port conflict on next reboot or crash-restart."
    echo ""
    echo "         To proceed: stop the tmux session first, then re-run this script."
    echo "         Exiting without installing."
    exit 1
  else
    echo ""
    echo "WARNING: Another process (PID(s): $CONFLICT_PID) is already listening on"
    echo "         port $PORT. Installing launchd now may cause a conflict."
    echo ""
    echo "         Stop that process first, then re-run this script."
    echo "         Exiting without installing."
    exit 1
  fi
fi

# ── Already installed? ────────────────────────────────────────────────────────
if launchctl list 2>/dev/null | grep -q "$PLIST_LABEL"; then
  echo "Service '$PLIST_LABEL' is already loaded. Unload it first with:"
  echo "  bash scripts/uninstall_launchd.sh"
  exit 1
fi

# ── Substitute paths in plist template ────────────────────────────────────────
echo "Generating plist from template..."
mkdir -p "$HOME/Library/LaunchAgents"

sed \
  -e "s|__COMMANDER_ROOT__|$DASHBOARD_DIR|g" \
  -e "s|__VENV_BIN__|$VENV_BIN|g" \
  -e "s|__HOME__|$HOME|g" \
  "$PLIST_TEMPLATE" > "$PLIST_DEST"

chmod 644 "$PLIST_DEST"
echo "Plist installed to: $PLIST_DEST (permissions: 644)"

# ── Load the service ──────────────────────────────────────────────────────────
echo "Loading service with launchctl..."
launchctl load "$PLIST_DEST"

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
echo "Verifying service registration..."
if launchctl list | grep -q "$PLIST_LABEL"; then
  echo "SUCCESS: Service '$PLIST_LABEL' is loaded and running."
  echo ""
  launchctl list | grep "$PLIST_LABEL"
  echo ""
  echo "Logs:"
  echo "  stdout: $HOME/Library/Logs/commander-dashboard.out.log"
  echo "  stderr: $HOME/Library/Logs/commander-dashboard.err.log"
  echo ""
  echo "To uninstall: bash scripts/uninstall_launchd.sh"
else
  echo "FAILURE: Service does not appear in launchctl list."
  echo "Check $HOME/Library/Logs/commander-dashboard.err.log for errors."
  exit 1
fi
