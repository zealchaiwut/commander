#!/usr/bin/env bash
# Snapshot gitignored local config files from all 4 clones into /tmp/commander-sync/.
# Run from anywhere. Override PROJECT_ROOT env var if needed.

set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$HOME/dev/commander}"
STAGING="/tmp/commander-sync"
CLONES=(prd uat coder tester)

echo "Project root : $PROJECT_ROOT"
echo "Staging dir  : $STAGING"
echo ""

rm -rf "$STAGING"
mkdir -p "$STAGING"

# --- Root-level sprint.yaml (shared across all clones) ---
src="$PROJECT_ROOT/.commander/sprint.yaml"
if [[ -f "$src" ]]; then
  mkdir -p "$STAGING/root/.commander"
  cp "$src" "$STAGING/root/.commander/sprint.yaml"
  echo "[root]  .commander/sprint.yaml → $STAGING/root/.commander/sprint.yaml"
else
  echo "[root]  .commander/sprint.yaml — NOT FOUND (skipping)"
fi

# --- Per-clone files ---
for clone in "${CLONES[@]}"; do
  src_dir="$PROJECT_ROOT/$clone"
  dst_dir="$STAGING/$clone"
  echo ""
  echo "[$clone]"

  # apps/dashboard/.env
  src_file="$src_dir/apps/dashboard/.env"
  if [[ -f "$src_file" ]]; then
    mkdir -p "$dst_dir/apps/dashboard"
    cp "$src_file" "$dst_dir/apps/dashboard/.env"
    echo "  apps/dashboard/.env → $dst_dir/apps/dashboard/.env"
  else
    echo "  apps/dashboard/.env — NOT FOUND (skipping)"
  fi

  # apps/dashboard/projects.json
  src_file="$src_dir/apps/dashboard/projects.json"
  if [[ -f "$src_file" ]]; then
    mkdir -p "$dst_dir/apps/dashboard"
    cp "$src_file" "$dst_dir/apps/dashboard/projects.json"
    echo "  apps/dashboard/projects.json → $dst_dir/apps/dashboard/projects.json"
  else
    echo "  apps/dashboard/projects.json — NOT FOUND (skipping)"
  fi
done

echo ""
echo "Done. All files staged in $STAGING"
