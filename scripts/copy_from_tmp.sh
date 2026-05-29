#!/usr/bin/env bash
# Restore gitignored local config files from /tmp/commander-sync/ into the current clone.
# Auto-detects which clone (prd/uat/coder/tester) from the current directory.
# Run from inside any subdirectory of a clone, e.g.: cd ~/dev/commander/uat && bash scripts/copy_from_tmp.sh

set -euo pipefail

STAGING="/tmp/commander-sync"
CLONES=(prd uat coder tester)

if [[ ! -d "$STAGING" ]]; then
  echo "ERROR: $STAGING not found. Run copy_to_tmp.sh first."
  exit 1
fi

# Walk up from pwd to find the clone root (a directory named prd/uat/coder/tester)
REPO_ROOT="$(pwd)"
CLONE=""
while [[ "$REPO_ROOT" != "/" ]]; do
  base="$(basename "$REPO_ROOT")"
  for c in "${CLONES[@]}"; do
    if [[ "$base" == "$c" ]]; then
      CLONE="$c"
      break 2
    fi
  done
  REPO_ROOT="$(dirname "$REPO_ROOT")"
done

if [[ -z "$CLONE" ]]; then
  echo "ERROR: Could not detect clone (prd/uat/coder/tester) from current directory: $(pwd)"
  echo "Run this script from inside one of the 4 clone directories."
  exit 1
fi

PROJECT_ROOT="$(dirname "$REPO_ROOT")"

echo "Detected clone : $CLONE"
echo "Repo root      : $REPO_ROOT"
echo "Project root   : $PROJECT_ROOT"
echo ""

# --- Per-clone files ---
src_dir="$STAGING/$CLONE"
echo "[$CLONE]"

# apps/dashboard/.env
src_file="$src_dir/apps/dashboard/.env"
if [[ -f "$src_file" ]]; then
  mkdir -p "$REPO_ROOT/apps/dashboard"
  cp "$src_file" "$REPO_ROOT/apps/dashboard/.env"
  echo "  $src_file → $REPO_ROOT/apps/dashboard/.env"
else
  echo "  apps/dashboard/.env — NOT FOUND in staging (skipping)"
fi

# apps/dashboard/projects.json
src_file="$src_dir/apps/dashboard/projects.json"
if [[ -f "$src_file" ]]; then
  mkdir -p "$REPO_ROOT/apps/dashboard"
  cp "$src_file" "$REPO_ROOT/apps/dashboard/projects.json"
  echo "  $src_file → $REPO_ROOT/apps/dashboard/projects.json"
else
  echo "  apps/dashboard/projects.json — NOT FOUND in staging (skipping)"
fi

# --- Root-level sprint.yaml ---
echo ""
echo "[root]"
src_file="$STAGING/root/.commander/sprint.yaml"
if [[ -f "$src_file" ]]; then
  mkdir -p "$PROJECT_ROOT/.commander"
  cp "$src_file" "$PROJECT_ROOT/.commander/sprint.yaml"
  echo "  $src_file → $PROJECT_ROOT/.commander/sprint.yaml"
else
  echo "  .commander/sprint.yaml — NOT FOUND in staging (skipping)"
fi

echo ""
echo "Done. Files restored to $REPO_ROOT"
