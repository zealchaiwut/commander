#!/usr/bin/env bash
# install_launchd.sh — Install a uvicorn dashboard as a macOS LaunchAgent.
#
# Generalized (issue #724): any project environment can register a managed
# service by passing its own label / working dir / uvicorn path / port /
# ENVIRONMENT value. With no parameters the script reproduces the original
# commander dashboard service, so existing commander usage is unchanged.
#
# What it does:
#   1. Renders a .plist for the requested service (label-specific log dir).
#   2. Checks for a port conflict on the requested port (tmux or otherwise).
#   3. Copies the plist to ~/Library/LaunchAgents/ with 644 permissions.
#   4. Loads the service with launchctl and verifies it is listed.
#
# Usage:
#   install_launchd.sh [--label L] [--working-dir D] [--uvicorn-path P]
#                      [--port N] [--environment E] [--server-app A]
#                      [--print-plist]
#
#   --print-plist   Render the plist to stdout and exit 0 WITHOUT touching
#                   launchctl or any port/process (used by tests / dry runs).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Defaults reproduce the commander dashboard service ────────────────────────
LABEL="com.commander.dashboard"
WORKING_DIR="$REPO_ROOT/apps/dashboard"
UVICORN_PATH="$REPO_ROOT/venv/bin/uvicorn"
PORT=8000
ENVIRONMENT="prd"
SERVER_APP="server:app"
PRINT_PLIST=false

# ── Parse args ────────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --label)         LABEL="$2"; shift 2 ;;
    --working-dir)   WORKING_DIR="$2"; shift 2 ;;
    --uvicorn-path)  UVICORN_PATH="$2"; shift 2 ;;
    --port)          PORT="$2"; shift 2 ;;
    --environment)   ENVIRONMENT="$2"; shift 2 ;;
    --server-app)    SERVER_APP="$2"; shift 2 ;;
    --print-plist)   PRINT_PLIST=true; shift ;;
    -h|--help)
      grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 2 ;;
  esac
done

PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/${LABEL}"
UVICORN_BIN_DIR="$(dirname "$UVICORN_PATH")"

# ── Render the plist ──────────────────────────────────────────────────────────
# Built inline (no commander-specific template) so every value is parametrized.
render_plist() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>

  <!-- Service identity -->
  <key>Label</key>
  <string>${LABEL}</string>

  <!-- Run on user login -->
  <key>RunAtLoad</key>
  <true/>

  <!-- Restart only on non-zero exit (crash / kill -9), not on clean shutdown -->
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>

  <!-- Working directory -->
  <key>WorkingDirectory</key>
  <string>${WORKING_DIR}</string>

  <!-- Launch command -->
  <key>ProgramArguments</key>
  <array>
    <string>${UVICORN_PATH}</string>
    <string>${SERVER_APP}</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>${PORT}</string>
  </array>

  <!-- Environment: venv bin on PATH + the ENVIRONMENT selector -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${UVICORN_BIN_DIR}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>ENVIRONMENT</key>
    <string>${ENVIRONMENT}</string>
  </dict>

  <!-- Log files under ~/Library/Logs/<label>/ -->
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/stderr.log</string>

</dict>
</plist>
PLIST
}

# ── Dry run: print and exit before any side effects ───────────────────────────
if $PRINT_PLIST; then
  render_plist
  exit 0
fi

echo "=== LaunchAgent Installer ==="
echo "Label       : $LABEL"
echo "Working dir : $WORKING_DIR"
echo "Uvicorn     : $UVICORN_PATH"
echo "Port        : $PORT"
echo "Environment : $ENVIRONMENT"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -x "$UVICORN_PATH" ] && [ ! -f "$UVICORN_PATH" ]; then
  echo "ERROR: uvicorn not found at $UVICORN_PATH — set up the venv first."
  exit 1
fi

if [ ! -d "$WORKING_DIR" ]; then
  echo "ERROR: working directory not found at $WORKING_DIR"
  exit 1
fi

# ── Port conflict check ───────────────────────────────────────────────────────
CONFLICT_PID=""
if command -v lsof >/dev/null 2>&1; then
  CONFLICT_PID=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
fi

if [ -n "$CONFLICT_PID" ]; then
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
if launchctl list 2>/dev/null | grep -q "$LABEL"; then
  echo "Service '$LABEL' is already loaded. Unload it first with:"
  echo "  bash scripts/uninstall_launchd.sh --label $LABEL"
  exit 1
fi

# ── Install plist + log dir ───────────────────────────────────────────────────
echo "Generating plist..."
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$LOG_DIR"

render_plist > "$PLIST_DEST"
chmod 644 "$PLIST_DEST"
echo "Plist installed to: $PLIST_DEST (permissions: 644)"

# ── Load the service ──────────────────────────────────────────────────────────
echo "Loading service with launchctl..."
launchctl load "$PLIST_DEST"

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
echo "Verifying service registration..."
if launchctl list | grep -q "$LABEL"; then
  echo "SUCCESS: Service '$LABEL' is loaded and running."
  echo ""
  launchctl list | grep "$LABEL"
  echo ""
  echo "Logs:"
  echo "  stdout: $LOG_DIR/stdout.log"
  echo "  stderr: $LOG_DIR/stderr.log"
  echo ""
  echo "To uninstall: bash scripts/uninstall_launchd.sh --label $LABEL"
else
  echo "FAILURE: Service does not appear in launchctl list."
  echo "Check $LOG_DIR/stderr.log for errors."
  exit 1
fi
