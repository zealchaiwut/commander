#!/usr/bin/env bash
# sync_uat.sh — Pull the latest develop commits into the UAT clone.
#
# UAT_DIR is derived from this script's location via git rev-parse:
#   <project_dir>/uat/<repo_name>
# where <project_dir> is the parent of the main repo clone and
# <repo_name> is the basename of the main repo root.
#
# Standard layout:
#   ~/dev/<project>/               ← PROJECT_DIR
#     <repo_name>/                 ← MAIN_REPO  (PRD clone, master branch)
#       dashboard/scripts/         ← this script lives here
#     uat/
#       <repo_name>/               ← UAT_DIR (UAT clone, develop branch)
#
# Run this to bring the UAT environment up to date with origin/develop.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
PROJECT_DIR="$(dirname "$MAIN_REPO")"
REPO_NAME="$(basename "$MAIN_REPO")"

UAT_DIR="$PROJECT_DIR/uat/$REPO_NAME"

echo "=== Syncing UAT environment with develop ==="

if [ ! -d "$UAT_DIR/.git" ]; then
    echo "ERROR: UAT directory not found at $UAT_DIR. Create it via:"
    echo "  cd $PROJECT_DIR && git clone https://github.com/zealchaiwut/commander.git uat/$REPO_NAME && cd uat/$REPO_NAME && git checkout develop"
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
