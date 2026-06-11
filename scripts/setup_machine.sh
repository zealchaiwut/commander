#!/usr/bin/env bash
# setup_machine.sh — One-command bootstrap + preflight doctor for a fresh
# Commander clone (issue #763).
#
# A bare clone needs manual archaeology to become runnable: no venv, no .env,
# no nested prd/uat layout, manual gist restore, per-machine auth. This script
# makes a bare clone self-bootstrapping in one command, and doubles as a
# preflight doctor so broken environments are caught before runtime failures.
#
# Usage:
#   bash scripts/setup_machine.sh                 # full bootstrap + doctor
#   bash scripts/setup_machine.sh --setup-only    # bootstrap steps, no doctor
#   bash scripts/setup_machine.sh --doctor        # doctor checks only
#   bash scripts/setup_machine.sh --restore-gist <id>   # restore config via backup.py
#   bash scripts/setup_machine.sh --restore-db <source> # restore DB via backup.py
#   bash scripts/setup_machine.sh --help
#
# Idempotent: re-running is safe — present venv/.env/clones are skipped.
#
# Path overrides (mainly for tests; default to the standard layout):
#   COMMANDER_DASHBOARD_DIR   where .env lives        (default <repo>/apps/dashboard)
#   COMMANDER_VENV_DIR        venv location           (default <repo>/venv)
#   COMMANDER_PROJECT_DIR     prd/uat parent          (default ~/dev/commander)
#   PORT                      port the doctor checks  (default 8000)
#
# Behaviour flags:
#   SETUP_MACHINE_DRY_RUN=1   echo side-effecting commands instead of running
#   SETUP_MACHINE_SKIP_PIP=1  skip the pip install step
#   SETUP_MACHINE_SKIP_NPM=1  skip the frontend build step (npm install / build)
#
# Frontend build (issue #796): the dashboard JS is bundled with esbuild from
# apps/dashboard/static/src into apps/dashboard/static/dist/bundle.js. Bootstrap
# runs `npm install` then `npm run build` when npm is available.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DASHBOARD_DIR="${COMMANDER_DASHBOARD_DIR:-$REPO_ROOT/apps/dashboard}"
VENV_DIR="${COMMANDER_VENV_DIR:-$REPO_ROOT/venv}"
PROJECT_DIR="${COMMANDER_PROJECT_DIR:-$HOME/dev/commander}"
PORT="${PORT:-8000}"
REPO_URL="https://github.com/zealchaiwut/commander.git"

# Python used for restore dispatch (venv python if present, else system).
if [ -x "$VENV_DIR/bin/python" ]; then
    PYTHON="$VENV_DIR/bin/python"
else
    PYTHON="python3"
fi

# ── helpers ───────────────────────────────────────────────────────────────────

# run_step: execute a side-effecting command, or echo it under dry-run.
run_step() {
    if [ "${SETUP_MACHINE_DRY_RUN:-}" = "1" ]; then
        echo "DRY-RUN: $*"
    else
        "$@"
    fi
}

usage() {
    cat <<'EOF'
setup_machine.sh — bootstrap a fresh Commander clone + run preflight doctor.

Usage:
  bash scripts/setup_machine.sh                 Full bootstrap, then doctor.
  bash scripts/setup_machine.sh --setup-only    Bootstrap steps only (no doctor).
  bash scripts/setup_machine.sh --doctor        Doctor checks only.
  bash scripts/setup_machine.sh --restore-gist <id>    Restore config from gist via backup.py.
  bash scripts/setup_machine.sh --restore-db <source>  Restore DB from repo/path via backup.py.
  bash scripts/setup_machine.sh --help          Show this help.

The doctor section prints a PASS/FAIL table for gh auth, the claude CLI,
tailscale, the dashboard port, and sqlite3, and exits nonzero if any check
FAILs. For launchd service issues it points you at scripts/install_launchd.sh.
EOF
}

# ── 1. venv + requirements ────────────────────────────────────────────────────

setup_venv() {
    if [ -x "$VENV_DIR/bin/pip" ]; then
        echo "[venv] $VENV_DIR already present — skipping creation."
    else
        if [ -d "$VENV_DIR" ]; then
            echo "[venv] $VENV_DIR exists but is broken — recreating."
            run_step rm -rf "$VENV_DIR"
        fi
        echo "[venv] Creating Python venv at $VENV_DIR …"
        run_step python3.12 -m venv "$VENV_DIR" \
            || run_step python3 -m venv "$VENV_DIR"
    fi

    if [ "${SETUP_MACHINE_SKIP_PIP:-}" = "1" ]; then
        echo "[venv] SETUP_MACHINE_SKIP_PIP=1 — skipping pip install."
    else
        echo "[venv] Installing requirements (pip install -r requirements.txt) …"
        run_step "$VENV_DIR/bin/pip" install --quiet --upgrade pip
        run_step "$VENV_DIR/bin/pip" install --quiet -r "$REPO_ROOT/requirements.txt"
    fi
}

# ── 1b. frontend build (esbuild pipeline, issue #796) ─────────────────────────
#
# The dashboard JS source lives in apps/dashboard/static/src and is bundled to
# apps/dashboard/static/dist/bundle.js by esbuild. Production serves static
# files from disk with no build step, so the committed bundle already works —
# but a fresh dev clone should install deps and rebuild so `npm run watch`
# works and the bundle stays in sync with source edits.
#
#   npm install        # install esbuild + lint toolchain
#   npm run build      # emit static/dist/bundle.js (+ .map)
setup_frontend() {
    if [ "${SETUP_MACHINE_SKIP_NPM:-}" = "1" ]; then
        echo "[frontend] SETUP_MACHINE_SKIP_NPM=1 — skipping npm install/build."
        return 0
    fi
    if ! command -v npm >/dev/null 2>&1; then
        echo "[frontend] npm not found — install Node.js to build the dashboard"
        echo "[frontend]   bundle. The committed static/dist/bundle.js still"
        echo "[frontend]   works at runtime; rebuild later with: npm install && npm run build"
        return 0
    fi
    echo "[frontend] Installing JS build deps (npm install) …"
    run_step npm --prefix "$REPO_ROOT" install
    echo "[frontend] Building dashboard bundle (npm run build) …"
    run_step npm --prefix "$REPO_ROOT" run build
}

# ── 2. .env from .env.example (+ prompt for secret keys, never echoed) ────────

# _set_env_key FILE KEY VALUE — set/replace KEY=VALUE in FILE without echoing
# VALUE (passed via env, not argv-visible stdout).
_set_env_key() {
    SM_KEY="$2" SM_VAL="$3" python3 - "$1" <<'PY'
import os, sys
path = sys.argv[1]
key = os.environ["SM_KEY"]
val = os.environ["SM_VAL"]
try:
    lines = open(path).read().splitlines()
except FileNotFoundError:
    lines = []
out, done = [], False
for ln in lines:
    stripped = ln.lstrip("#").lstrip()
    if not done and stripped.startswith(key + "="):
        out.append(f"{key}={val}")
        done = True
    else:
        out.append(ln)
if not done:
    out.append(f"{key}={val}")
open(path, "w").write("\n".join(out) + "\n")
PY
}

# prompt_secret KEY LABEL FILE — silently read a secret and store it in FILE.
prompt_secret() {
    local key="$1" label="$2" file="$3" val=""
    # Dry-run never prompts (keeps non-interactive runs from blocking).
    if [ "${SETUP_MACHINE_DRY_RUN:-}" = "1" ]; then
        echo "  [env] would prompt for $key" >&2
        return 0
    fi
    printf '  Enter %s (input hidden, blank to skip): ' "$label" >&2
    read -rs val || true
    printf '\n' >&2
    if [ -n "$val" ]; then
        _set_env_key "$file" "$key" "$val"
        echo "  [env] $key set (value hidden)." >&2
    else
        echo "  [env] $key left unset." >&2
    fi
}

setup_env() {
    local env_file="$DASHBOARD_DIR/.env"
    local example="$DASHBOARD_DIR/.env.example"
    if [ -f "$env_file" ]; then
        echo "[env] $env_file already present — skipping."
        return 0
    fi
    if [ ! -f "$example" ]; then
        echo "[env] WARNING: $example not found — cannot create .env." >&2
        return 0
    fi
    echo "[env] Creating $env_file from .env.example …"
    run_step cp "$example" "$env_file"
    # Prompt for required secret keys. Values are read silently and never echoed.
    if [ "${SETUP_MACHINE_DRY_RUN:-}" != "1" ]; then
        prompt_secret GH_TOKEN "GitHub token for headless gh (GH_TOKEN)" "$env_file"
    fi
}

# ── 3. prd/uat clone layout + coder/tester worktrees ──────────────────────────

setup_layout() {
    run_step mkdir -p "$PROJECT_DIR"
    local d
    for name in prd uat; do
        d="$PROJECT_DIR/$name"
        if [ -d "$d" ]; then
            echo "[layout] $d already present — skipping clone."
        else
            echo "[layout] Creating $name clone at $d …"
            run_step git clone "$REPO_URL" "$d"
        fi
    done
    echo "[layout] Agent worktrees (coder/tester) — create if you run agents locally:"
    for name in coder tester; do
        d="$PROJECT_DIR/$name"
        if [ -d "$d" ]; then
            echo "  $d already present."
        else
            echo "  $name: git clone $REPO_URL $d && git -C $d checkout develop"
        fi
    done
}

# ── 4. restore hooks into backup.py ───────────────────────────────────────────

restore_gist() {
    local gist_id="$1"
    echo "[restore] Restoring config from gist $gist_id via backup.py …"
    if [ "${SETUP_MACHINE_DRY_RUN:-}" = "1" ]; then
        echo "DRY-RUN: (cd $REPO_ROOT && $PYTHON -m services.sprint_manager.backup restore --gist-id $gist_id --target-dir $REPO_ROOT)"
    else
        ( cd "$REPO_ROOT" && "$PYTHON" -m services.sprint_manager.backup \
            restore --gist-id "$gist_id" --target-dir "$REPO_ROOT" )
    fi
}

restore_db() {
    local source="$1"
    local target="$DASHBOARD_DIR/commander.db"
    echo "[restore] Restoring DB from $source via backup.py …"
    if [ "${SETUP_MACHINE_DRY_RUN:-}" = "1" ]; then
        echo "DRY-RUN: (cd $REPO_ROOT && $PYTHON -m services.sprint_manager.backup restore-db --from $source --target $target)"
    else
        ( cd "$REPO_ROOT" && "$PYTHON" -m services.sprint_manager.backup \
            restore-db --from "$source" --target "$target" )
    fi
}

# ── 5. doctor ─────────────────────────────────────────────────────────────────

_row() { printf '  %-6s %-22s %s\n' "[$1]" "$2" "$3"; }

_claude_authed() {
    [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && return 0
    [ -n "${ANTHROPIC_API_KEY:-}" ] && return 0
    [ -f "$HOME/.claude/.credentials.json" ] && return 0
    [ -f "$HOME/.claude.json" ] && return 0
    return 1
}

_port_free() {
    if command -v lsof >/dev/null 2>&1; then
        ! lsof -i :"$1" -sTCP:LISTEN -t >/dev/null 2>&1
    else
        return 0
    fi
}

run_doctor() {
    local fails=0
    echo ""
    echo "=== Doctor checks ==="
    printf '  %-6s %-22s %s\n' "RESULT" "CHECK" "HINT"

    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        _row PASS "gh auth status" ""
    else
        _row FAIL "gh auth status" "run: gh auth login"
        fails=$((fails + 1))
    fi

    if command -v claude >/dev/null 2>&1 && _claude_authed; then
        _row PASS "claude CLI" ""
    else
        _row FAIL "claude CLI" "run: claude  (then complete the login)"
        fails=$((fails + 1))
    fi

    if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
        _row PASS "tailscale up" ""
    else
        _row FAIL "tailscale up" "run: tailscale up"
        fails=$((fails + 1))
    fi

    if _port_free "$PORT"; then
        _row PASS "port $PORT free" ""
    else
        _row FAIL "port $PORT free" "port in use; if a stale launchd worker, see scripts/install_launchd.sh"
        fails=$((fails + 1))
    fi

    if command -v sqlite3 >/dev/null 2>&1; then
        _row PASS "sqlite3 present" ""
    else
        _row FAIL "sqlite3 present" "install sqlite3"
        fails=$((fails + 1))
    fi

    # Node/npm power the esbuild frontend pipeline (issue #796). Informational
    # only — the committed static/dist/bundle.js runs without a build step, so a
    # missing toolchain must not fail provisioning; it only blocks rebuilds.
    if command -v npm >/dev/null 2>&1; then
        _row PASS "npm present" "frontend: npm install && npm run build"
    else
        _row INFO "npm present" "optional; needed only to rebuild the dashboard bundle"
    fi

    if [ "$fails" -gt 0 ]; then
        echo ""
        echo "$fails doctor check(s) FAILED."
        echo "For launchd service issues, see scripts/install_launchd.sh"
        return 1
    fi
    echo ""
    echo "All doctor checks PASSED."
    return 0
}

# ── argument parsing ──────────────────────────────────────────────────────────

MODE="full"          # full | setup-only | doctor
RESTORE_GIST=""
RESTORE_DB=""

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --doctor|--doctor-only)
            MODE="doctor"
            shift
            ;;
        --setup-only)
            MODE="setup-only"
            shift
            ;;
        --restore-gist)
            RESTORE_GIST="${2:?--restore-gist requires a gist id}"
            shift 2
            ;;
        --restore-db)
            RESTORE_DB="${2:?--restore-db requires a source repo/path}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

echo "=== Commander machine setup ==="

# Restore flags run standalone (no prompts, no full bootstrap).
if [ -n "$RESTORE_GIST" ] || [ -n "$RESTORE_DB" ]; then
    [ -n "$RESTORE_GIST" ] && restore_gist "$RESTORE_GIST"
    [ -n "$RESTORE_DB" ] && restore_db "$RESTORE_DB"
    exit 0
fi

if [ "$MODE" = "doctor" ]; then
    DOCTOR_RC=0
    run_doctor || DOCTOR_RC=$?
    exit "$DOCTOR_RC"
fi

# Bootstrap steps (full + setup-only).
setup_venv
setup_frontend
setup_env
setup_layout

if [ "$MODE" = "setup-only" ]; then
    echo ""
    echo "=== Setup steps complete (doctor skipped) ==="
    exit 0
fi

DOCTOR_RC=0
run_doctor || DOCTOR_RC=$?
exit "$DOCTOR_RC"
