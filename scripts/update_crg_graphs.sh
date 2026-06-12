#!/usr/bin/env bash
# update_crg_graphs.sh — refresh code-review-graph DBs in agent worktrees.
#
# Each clone (coder, tester, uat, prd) has its own .code-review-graph/ — they do
# not share. Headless claude -p sprints do not reliably fire CRG file hooks, so
# refresh graphs before overnight runs and after merging develop.
#
# Usage:
#   bash scripts/update_crg_graphs.sh              # incremental update: coder + tester
#   bash scripts/update_crg_graphs.sh --all          # incremental update: all clones
#   bash scripts/update_crg_graphs.sh --full         # full rebuild: coder + tester
#   bash scripts/update_crg_graphs.sh --clone coder  # one clone only
#
# Env:
#   COMMANDER_PROJECT_DIR   default ~/dev/commander

set -euo pipefail

PROJECT_DIR="${COMMANDER_PROJECT_DIR:-$HOME/dev/commander}"
MODE="update"
CLONES=(coder tester)
ONLY_CLONE=""

run_step() {
    if [ "${SETUP_MACHINE_DRY_RUN:-}" = "1" ]; then
        echo "DRY-RUN: $*"
    else
        "$@"
    fi
}

find_crg_bin() {
    local v
    for v in "$PROJECT_DIR/uat/venv/bin/code-review-graph" \
             "$PROJECT_DIR/prd/venv/bin/code-review-graph"; do
        if [ -x "$v" ]; then
            echo "$v"
            return 0
        fi
    done
    if command -v code-review-graph >/dev/null 2>&1; then
        command -v code-review-graph
        return 0
    fi
    return 1
}

usage() {
    cat <<'EOF'
update_crg_graphs.sh — refresh CRG graphs in agent worktrees.

  bash scripts/update_crg_graphs.sh              update coder + tester (default)
  bash scripts/update_crg_graphs.sh --all        update prd + uat + coder + tester
  bash scripts/update_crg_graphs.sh --full         full rebuild coder + tester
  bash scripts/update_crg_graphs.sh --clone uat    single clone

Run before overnight sprints. Sprint manager also runs a best-effort update
before each coder/tester dispatch when CRG is installed.
EOF
}

refresh_clone() {
    local name="$1"
    local crg="$2"
    local root="$PROJECT_DIR/$name"
    local subcmd="update"
    if [ "$MODE" = "full" ]; then
        subcmd="build"
    fi
    if [ ! -d "$root" ]; then
        echo "[$name] skip — $root not found."
        return 0
    fi
    if [ ! -d "$root/.code-review-graph" ] && [ "$MODE" = "update" ]; then
        echo "[$name] no graph yet — running build …"
        subcmd="build"
    fi
    echo "=== $name: code-review-graph $subcmd ==="
    run_step bash -c "cd \"$root\" && \"$crg\" $subcmd"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --all) CLONES=(prd uat coder tester); shift ;;
        --full) MODE="full"; shift ;;
        --clone) ONLY_CLONE="${2:?--clone requires a name}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

CRG_BIN=""
if CRG_BIN="$(find_crg_bin)"; then
    :
else
    echo "ERROR: code-review-graph not found. pip install -r requirements.txt first." >&2
    exit 1
fi

echo "=== CRG graph refresh (mode=$MODE, bin=$CRG_BIN) ==="

if [ -n "$ONLY_CLONE" ]; then
    refresh_clone "$ONLY_CLONE" "$CRG_BIN"
else
    for c in "${CLONES[@]}"; do
        refresh_clone "$c" "$CRG_BIN"
    done
fi

echo ""
echo "CRG refresh complete."
