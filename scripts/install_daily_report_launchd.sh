#!/usr/bin/env bash
# install_daily_report_launchd.sh — Install the Commander daily report job as a macOS LaunchAgent.
#
# Fires at 23:55 local time every day, writing .commander/reports/YYYY-MM-DD.md.
# To change the fire time, edit com.commander.daily-report.plist and re-run this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_TEMPLATE="$SCRIPT_DIR/com.commander.daily-report.plist"
PLIST_LABEL="com.commander.daily-report"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
VENV_BIN="$REPO_ROOT/venv/bin"
DASHBOARD_DIR="$REPO_ROOT/apps/dashboard"

echo "=== Commander Daily Report LaunchAgent Installer ==="
echo "Repo root     : $REPO_ROOT"
echo "Dashboard dir : $DASHBOARD_DIR"
echo "Venv bin      : $VENV_BIN"

if [ ! -f "$PLIST_TEMPLATE" ]; then
  echo "ERROR: plist template not found at $PLIST_TEMPLATE"
  exit 1
fi

if [ ! -f "$VENV_BIN/python3" ]; then
  echo "ERROR: python3 not found at $VENV_BIN/python3 — set up the venv first."
  exit 1
fi

if [ ! -f "$REPO_ROOT/scripts/generate_daily_report.py" ]; then
  echo "ERROR: generate_daily_report.py not found at $REPO_ROOT/scripts/"
  exit 1
fi

if launchctl list 2>/dev/null | grep -q "$PLIST_LABEL"; then
  echo "Service '$PLIST_LABEL' is already loaded. Unloading first..."
  launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

echo "Generating plist from template..."
mkdir -p "$HOME/Library/LaunchAgents"

sed \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__DASHBOARD_DIR__|$DASHBOARD_DIR|g" \
  -e "s|__VENV_BIN__|$VENV_BIN|g" \
  -e "s|__HOME__|$HOME|g" \
  "$PLIST_TEMPLATE" > "$PLIST_DEST"

chmod 644 "$PLIST_DEST"
echo "Plist installed to: $PLIST_DEST"

echo "Loading service with launchctl..."
launchctl load "$PLIST_DEST"

echo ""
if launchctl list | grep -q "$PLIST_LABEL"; then
  echo "SUCCESS: Service '$PLIST_LABEL' is loaded."
  echo "  Fires at: 23:55 local time daily"
  echo "  Stdout:   $HOME/Library/Logs/commander-daily-report.out.log"
  echo "  Stderr:   $HOME/Library/Logs/commander-daily-report.err.log"
  echo ""
  echo "To uninstall: launchctl unload '$PLIST_DEST' && rm '$PLIST_DEST'"
else
  echo "FAILURE: Service not found in launchctl list."
  echo "Check $HOME/Library/Logs/commander-daily-report.err.log for errors."
  exit 1
fi
