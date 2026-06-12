#!/usr/bin/env bash
# install_agent_skills.sh — caveman skill + code-review-graph MCP/skills/graph
# for every Commander clone under the project directory.
#
# Usage:
#   bash scripts/install_agent_skills.sh              # idempotent install
#   bash scripts/install_agent_skills.sh --force      # re-run npx + CRG install + build
#   bash scripts/install_agent_skills.sh --clone uat  # single clone only
#
# Env:
#   COMMANDER_PROJECT_DIR   default ~/dev/commander
#   SETUP_MACHINE_DRY_RUN=1 echo commands only
#   SETUP_MACHINE_SKIP_NPM=1 skip caveman (npx skills add)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${COMMANDER_PROJECT_DIR:-$HOME/dev/commander}"
CLONES=(prd uat coder tester)
FORCE=0
ONLY_CLONE=""

CP="/bin/cp"
CAVEman_REPO="https://github.com/mattpocock/skills"
SKILL_NAMES=(caveman debug-issue explore-codebase refactor-safely review-changes)

run_step() {
    if [ "${SETUP_MACHINE_DRY_RUN:-}" = "1" ]; then
        echo "DRY-RUN: $*"
    else
        "$@"
    fi
}

usage() {
    cat <<'EOF'
install_agent_skills.sh — caveman + code-review-graph for Commander clones.

Usage:
  bash scripts/install_agent_skills.sh              Install (skip if present).
  bash scripts/install_agent_skills.sh --force     Reinstall skills + rebuild graphs.
  bash scripts/install_agent_skills.sh --clone uat  Target one clone under ~/dev/commander/.
  bash scripts/install_agent_skills.sh --help

Requires: Node/npm (caveman), code-review-graph in a clone venv (pip install -r requirements.txt).
EOF
}

find_crg_bin() {
    local root v
    for root in "$PROJECT_DIR/uat" "$PROJECT_DIR/prd" "$SCRIPT_DIR/.."; do
        v="$root/venv/bin/code-review-graph"
        if [ -x "$v" ]; then
            echo "$v"
            return 0
        fi
    done
    return 1
}

install_caveman() {
    local clone_root="$1"
    if [ "${SETUP_MACHINE_SKIP_NPM:-}" = "1" ]; then
        echo "  [caveman] SETUP_MACHINE_SKIP_NPM=1 — skipping."
        return 0
    fi
    if ! command -v npx >/dev/null 2>&1; then
        echo "  [caveman] npx not found — install Node.js or set SETUP_MACHINE_SKIP_NPM=1." >&2
        return 0
    fi
    local skill_md="$clone_root/.claude/skills/caveman/SKILL.md"
    local agents_md="$clone_root/.agents/skills/caveman/SKILL.md"
    if [ "$FORCE" = "0" ] && [ -f "$skill_md" ]; then
        echo "  [caveman] already present — skipping (use --force to reinstall)."
        return 0
    fi
    echo "  [caveman] npx skills add …"
    if [ "${SETUP_MACHINE_DRY_RUN:-}" = "1" ]; then
        echo "DRY-RUN: (cd $clone_root && npx --yes skills add $CAVEman_REPO --skill caveman -y)"
    else
        ( cd "$clone_root" && npx --yes skills add "$CAVEman_REPO" --skill caveman -y )
    fi
    if [ -f "$agents_md" ]; then
        run_step mkdir -p "$clone_root/.claude/skills/caveman"
        run_step "$CP" -f "$agents_md" "$skill_md"
    fi
}

install_crg() {
    local clone_root="$1"
    local crg="$2"
    echo "  [crg] install --platform claude-code …"
    run_step bash -c "cd \"$clone_root\" && \"$crg\" install --platform claude-code"
    if [ "$(basename "$clone_root")" = "uat" ]; then
        echo "  [crg] install --platform cursor …"
        run_step bash -c "cd \"$clone_root\" && \"$crg\" install --platform cursor" || true
    fi
    echo "  [crg] build …"
    run_step bash -c "cd \"$clone_root\" && \"$crg\" build"
}

install_clone() {
    local name="$1"
    local crg="$2"
    local root="$PROJECT_DIR/$name"
    if [ ! -d "$root" ]; then
        echo "[$name] skip — $root not found."
        return 0
    fi
    echo "=== $name ($root) ==="
    install_caveman "$root"
    install_crg "$root" "$crg"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --force) FORCE=1; shift ;;
        --clone) ONLY_CLONE="${2:?--clone requires a name}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

CRG_BIN=""
if CRG_BIN="$(find_crg_bin)"; then
    :
else
    echo "ERROR: code-review-graph not found in any clone venv." >&2
    echo "Run: python3.12 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

echo "=== Agent skills install (CRG: $CRG_BIN) ==="

if [ -n "$ONLY_CLONE" ]; then
    install_clone "$ONLY_CLONE" "$CRG_BIN"
else
    for c in "${CLONES[@]}"; do
        install_clone "$c" "$CRG_BIN"
    done
fi

echo ""
echo "Agent skills install complete. Restart Claude Code / Cursor so MCP picks up changes."
