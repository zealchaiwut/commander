#!/usr/bin/env bash
# sync_uat.sh — Pull the latest develop commits into ~/commander/dashboard-uat.
#
# Run this to bring the UAT environment up to date with origin/develop.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMANDER_ROOT="$(cd "$DASHBOARD_DIR/.." && pwd)"

UAT_DIR="$COMMANDER_ROOT/dashboard-uat"

echo "=== Syncing UAT environment with develop ==="

if [ ! -d "$UAT_DIR/.git" ]; then
    echo "ERROR: UAT directory not found or not a git repo: $UAT_DIR"
    echo "Run setup_uat_env.sh first."
    exit 1
fi

# Ensure we're on develop
CURRENT_BRANCH=$(git -C "$UAT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
if [ "$CURRENT_BRANCH" != "develop" ]; then
    echo "WARNING: UAT repo is on branch '$CURRENT_BRANCH', not 'develop'."
    echo "Switching to develop …"
    git -C "$UAT_DIR" checkout develop
fi

echo "Pulling origin/develop …"
git -C "$UAT_DIR" pull origin develop

BRANCH=$(git -C "$UAT_DIR" rev-parse --abbrev-ref HEAD)
COMMIT=$(git -C "$UAT_DIR" rev-parse --short HEAD)
echo ""
echo "=== UAT sync complete ==="
echo "  Branch : $BRANCH"
echo "  Commit : $COMMIT"
echo ""
echo "Restart the UAT server to pick up changes:"
echo "  bash $(dirname "$0")/stop_all.sh && bash $(dirname "$0")/start_uat.sh"
