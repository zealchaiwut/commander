#!/usr/bin/env bash
# uninstall_launchd.sh — Unload and remove the Commander dashboard LaunchAgent.
#
# What it does:
#   1. Unloads the service with launchctl (stops the process if running).
#   2. Removes the plist from ~/Library/LaunchAgents/.

set -euo pipefail

# Generalized (issue #724): pass --label to uninstall any managed service.
# Defaults to the commander dashboard so existing usage is unchanged.
PLIST_LABEL="com.commander.dashboard"
while [ $# -gt 0 ]; do
  case "$1" in
    --label) PLIST_LABEL="$2"; shift 2 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

echo "=== LaunchAgent Uninstaller ($PLIST_LABEL) ==="

# ── Unload (stop the service if running) ──────────────────────────────────────
if launchctl list 2>/dev/null | grep -q "$PLIST_LABEL"; then
  echo "Unloading service '$PLIST_LABEL'..."
  launchctl unload "$PLIST_DEST" 2>/dev/null || true
  echo "Service unloaded."
else
  echo "Service '$PLIST_LABEL' is not currently loaded (may already be stopped)."
fi

# ── Remove plist file ─────────────────────────────────────────────────────────
if [ -f "$PLIST_DEST" ]; then
  rm -f "$PLIST_DEST"
  echo "Plist removed: $PLIST_DEST"
else
  echo "Plist not found at $PLIST_DEST (already removed or never installed)."
fi

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
if launchctl list 2>/dev/null | grep -q "$PLIST_LABEL"; then
  echo "WARNING: Service still appears in launchctl list — manual cleanup may be needed."
  exit 1
else
  echo "SUCCESS: '$PLIST_LABEL' is no longer registered with launchd."
fi
