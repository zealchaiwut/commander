#!/usr/bin/env bash
# setup_cline.sh — Install Cline CLI and verify the coder worktree for sprint dispatch.
#
# Cline is an optional coder backend (see docs/features/coder-backends.md).
# Two sprint.yaml routes:
#   agent_config.coder.backend: cline     → every coder dispatch uses Cline
#   agent_config.use_cline_followups: true → Cline only for follow-up tickets (.clinerules required)
#
# Auth: pick ONE billing surface for Cline:
#   - Company subscription:  cd ~/dev/commander/coder && cline auth
#   - Metered API:           export ANTHROPIC_API_KEY=sk-ant-...  (kept in coder subprocess env only)
# Do not set both — risk of double billing.
#
# Usage:
#   bash scripts/setup_cline.sh                     # install (if needed) + doctor
#   bash scripts/setup_cline.sh --doctor-only       # checks only
#   bash scripts/setup_cline.sh --enable-always     # sprint.yaml → coder.backend: cline
#   bash scripts/setup_cline.sh --enable-followups  # sprint.yaml → use_cline_followups: true
#   bash scripts/setup_cline.sh --disable           # revert to claude-code defaults
#   bash scripts/setup_cline.sh --dry-run           # print actions, no writes
#   bash scripts/setup_cline.sh --help
#
# Environment:
#   COMMANDER_PROJECT_DIR   project root with .commander/  (default ~/dev/commander)
#   CLINE_SKIP_INSTALL=1    skip npm install -g cline
#   CLINE_NPM_PACKAGE=cline override npm package (default: cline)
#
# Remote Mac mini: run after scripts/setup_machine.sh on that host. See
# docs/machine-onboarding.md § "Cline coder backend (migration)".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="${COMMANDER_PROJECT_DIR:-$HOME/dev/commander}"
SPRINT_YAML="${COMMANDER_SPRINT_YAML:-$PROJECT_DIR/.commander/sprint.yaml}"
CODER_DIR="${COMMANDER_CODER_DIR:-$PROJECT_DIR/coder}"
CLINE_PKG="${CLINE_NPM_PACKAGE:-cline}"

MODE="install"   # install | doctor
ENABLE_FLAG=""
DRY_RUN=0

run_step() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY-RUN: $*"
  else
    "$@"
  fi
}

usage() {
  cat <<'EOF'
setup_cline.sh — Cline CLI install + coder-worktree checks for sprint dispatch.

  bash scripts/setup_cline.sh                     Install (if needed) + doctor
  bash scripts/setup_cline.sh --doctor-only       Doctor checks only
  bash scripts/setup_cline.sh --enable-always     Set agent_config.coder.backend: cline
  bash scripts/setup_cline.sh --enable-followups  Set agent_config.use_cline_followups: true
  bash scripts/setup_cline.sh --disable           Revert to claude-code (default)
  bash scripts/setup_cline.sh --dry-run           Echo side effects only

After install, authenticate once in the coder worktree:
  cd ~/dev/commander/coder && cline auth

Manual dry-run (no writes):
  cd ~/dev/commander/coder
  cline -y -m claude-sonnet-4-6 \
    "Read CLAUDE.md and .claude/agents/coder.md. Dry-run only: explain the coder workflow for issue #<N> without making changes."

See docs/features/coder-backends.md and docs/machine-onboarding.md.
EOF
}

_row() { printf '  %-6s %-28s %s\n' "[$1]" "$2" "$3"; }

cline_version_ok() {
  command -v cline >/dev/null 2>&1 || return 1
  cline --version >/dev/null 2>&1 || cline version >/dev/null 2>&1
}

install_cline() {
  if [ "${CLINE_SKIP_INSTALL:-}" = "1" ]; then
    echo "[cline] CLINE_SKIP_INSTALL=1 — skipping npm install."
    return 0
  fi
  if cline_version_ok; then
    echo "[cline] $(cline --version 2>/dev/null || cline version 2>/dev/null) already on PATH — skipping install."
    return 0
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "[cline] ERROR: npm not found. Install Node.js 20+ then re-run." >&2
    return 1
  fi
  echo "[cline] Installing $CLINE_PKG globally (npm install -g $CLINE_PKG) …"
  run_step npm install -g "$CLINE_PKG"
  if ! cline_version_ok; then
    echo "[cline] ERROR: install finished but 'cline --version' still fails." >&2
    return 1
  fi
  echo "[cline] Installed: $(cline --version 2>/dev/null || cline version 2>/dev/null)"
}

run_doctor() {
  local fails=0
  echo ""
  echo "=== Cline doctor ==="
  printf '  %-6s %-28s %s\n' "RESULT" "CHECK" "HINT"

  if command -v node >/dev/null 2>&1; then
    _row PASS "node present" "$(node --version 2>/dev/null || true)"
  else
    _row FAIL "node present" "install Node.js 20+ (22 recommended)"
    fails=$((fails + 1))
  fi

  if command -v npm >/dev/null 2>&1; then
    _row PASS "npm present" ""
  else
    _row FAIL "npm present" "needed for: npm install -g cline"
    fails=$((fails + 1))
  fi

  if cline_version_ok; then
    _row PASS "cline CLI" "$(cline --version 2>/dev/null || cline version 2>/dev/null)"
  else
    _row FAIL "cline CLI" "run: bash scripts/setup_cline.sh"
    fails=$((fails + 1))
  fi

  if [ -d "$CODER_DIR" ]; then
    _row PASS "coder worktree" "$CODER_DIR"
  else
    _row FAIL "coder worktree" "git clone … $CODER_DIR && git checkout develop"
    fails=$((fails + 1))
  fi

  if [ -f "$CODER_DIR/.clinerules" ]; then
    _row PASS ".clinerules in coder" ""
  else
    _row WARN ".clinerules in coder" "Cline won't load Commander invariants — merge #916+ or copy .clinerules"
  fi

  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    _row INFO "ANTHROPIC_API_KEY" "set — metered API path (skip if using cline auth)"
  else
    _row INFO "ANTHROPIC_API_KEY" "unset — use 'cline auth' in coder worktree if not on metered API"
  fi

  if [ -f "$SPRINT_YAML" ]; then
    _row PASS "sprint.yaml" "$SPRINT_YAML"
    if command -v python3 >/dev/null 2>&1; then
      python3 - "$SPRINT_YAML" <<'PY' || true
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("  (install PyYAML in venv to read agent_config flags)")
    sys.exit(0)
p = Path(sys.argv[1])
data = yaml.safe_load(p.read_text()) or {}
ac = data.get("agent_config") or {}
backend = (ac.get("coder") or {}).get("backend", "claude-code")
followups = ac.get("use_cline_followups", False)
model = ac.get("coder_model", "(default claude-sonnet-4-6)")
print(f"  agent_config.coder.backend = {backend}")
print(f"  agent_config.use_cline_followups = {followups}")
print(f"  agent_config.coder_model = {model}")
PY
    fi
  else
    _row WARN "sprint.yaml" "missing at $SPRINT_YAML — run .commander/setup.sh first"
  fi

  echo ""
  if [ "$fails" -gt 0 ]; then
    echo "$fails Cline doctor check(s) FAILED."
    return 1
  fi
  echo "Cline doctor checks PASSED (auth: run 'cline auth' in coder worktree if not using API key)."
  return 0
}

patch_sprint_yaml() {
  local flag="$1"
  if [ ! -f "$SPRINT_YAML" ]; then
    echo "[cline] ERROR: $SPRINT_YAML not found — create it first (.commander/setup.sh)." >&2
    return 1
  fi
  local py="$REPO_ROOT/venv/bin/python"
  if [ ! -x "$py" ]; then
    py="python3"
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY-RUN: patch sprint.yaml ($flag) at $SPRINT_YAML"
    return 0
  fi
  "$py" - "$SPRINT_YAML" "$flag" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
mode = sys.argv[2]
data = yaml.safe_load(path.read_text()) or {}
ac = data.setdefault("agent_config", {})
ac.setdefault("coder_model", "claude-sonnet-4-6")
coder = ac.setdefault("coder", {})

if mode == "always":
    coder["backend"] = "cline"
    ac["use_cline_followups"] = False
    print(f"Set agent_config.coder.backend: cline (use_cline_followups: false)")
elif mode == "followups":
    ac["use_cline_followups"] = True
    if coder.get("backend") == "cline":
        coder["backend"] = "claude-code"
    print("Set agent_config.use_cline_followups: true (initial dispatch stays claude-code)")
elif mode == "disable":
    coder["backend"] = "claude-code"
    ac["use_cline_followups"] = False
    print("Reverted to claude-code defaults")
else:
    raise SystemExit(f"unknown patch mode: {mode}")

path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
print(f"Wrote {path}")
PY
}

print_test_hints() {
  cat <<EOF

--- Next steps ---
1. Authenticate (pick one):
     cd $CODER_DIR && cline auth
   OR export ANTHROPIC_API_KEY=sk-ant-... for metered API (not both).

2. Enable in sprint.yaml (if not done):
     bash scripts/setup_cline.sh --enable-followups   # safer
     bash scripts/setup_cline.sh --enable-always      # full Cline coder

3. Dry-run in coder worktree (no writes):
     cd $CODER_DIR
     cline -y -m claude-sonnet-4-6 \\
       "Read CLAUDE.md and .claude/agents/coder.md. Dry-run only: explain the coder workflow for issue #<N> without making changes."

4. Single-ticket dispatch test (WRITES — use a throwaway issue):
     cd $REPO_ROOT
     COMMANDER_PROJECT=zealchaiwut/commander python3 -c "
from services.sprint_manager.sprint_manager import _dispatch_coder, load_config
cfg = load_config()
_dispatch_coder(<N>, cfg=cfg, coder_backend_override='cline')
"
   Watch logs under $PROJECT_DIR/.commander/logs/

Caveats: no Cline telemetry in dashboard; MCP not replicated — agent uses gh/grep fallback.
EOF
}

# ── argument parsing ────────────────────────────────────────────────────────

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --doctor-only) MODE="doctor"; shift ;;
    --enable-always) ENABLE_FLAG="always"; shift ;;
    --enable-followups) ENABLE_FLAG="followups"; shift ;;
    --disable) ENABLE_FLAG="disable"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

echo "=== Commander Cline setup ==="
echo "Project:  $PROJECT_DIR"
echo "Coder:    $CODER_DIR"
echo "Sprint:   $SPRINT_YAML"

if [ -n "$ENABLE_FLAG" ]; then
  patch_sprint_yaml "$ENABLE_FLAG"
fi

DOCTOR_RC=0
if [ "$MODE" = "doctor" ]; then
  run_doctor || DOCTOR_RC=$?
else
  install_cline || exit 1
  run_doctor || DOCTOR_RC=$?
fi

print_test_hints
exit "$DOCTOR_RC"
