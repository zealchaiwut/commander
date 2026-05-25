#!/usr/bin/env bash
# sync_uat.sh — Pull the latest develop commits into the UAT clone.
#
# UAT_DIR is derived from this script's location via three-level directory
# traversal (no git commands):
#   SCRIPT_DIR   = <project-dir>/prd/dashboard/scripts/
#   DASHBOARD_DIR= <project-dir>/prd/dashboard/
#   REPO_ROOT    = <project-dir>/prd/
#   PROJECT_DIR  = <project-dir>/
#   UAT_DIR      = <project-dir>/uat
#
# Standard layout:
#   ~/dev/commander/               ← PROJECT_DIR
#     prd/                         ← PRD clone (REPO_ROOT)
#       dashboard/                 ← DASHBOARD_DIR
#         scripts/                 ← SCRIPT_DIR (this script lives here)
#     uat/                         ← UAT_DIR (UAT clone, develop branch)
#
# Run this to bring the UAT environment up to date with origin/develop.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DASHBOARD_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$REPO_ROOT/.." && pwd)"

UAT_DIR="$PROJECT_DIR/uat"

echo "=== Syncing UAT environment with develop ==="

if [ ! -d "$UAT_DIR/.git" ]; then
    echo "ERROR: UAT directory not found at $UAT_DIR. Create it via:"
    echo "  cd $PROJECT_DIR && git clone https://github.com/zealchaiwut/commander.git uat && cd uat && git checkout develop"
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
