#!/usr/bin/env bash
# setup_uat_env.sh — Clones the repo into <commander-root>/uat, checks
# out develop, installs a Python venv, writes .env, configures
# .claude/settings.json with the UAT hook target, and initialises the UAT
# database.
#
# Paths are derived from this script's location:
#   SCRIPT_DIR   = <project-dir>/prd/scripts/
#   REPO_ROOT    = <project-dir>/prd/
#   DASHBOARD_DIR= <project-dir>/prd/apps/dashboard/
#   PROJECT_DIR  = <project-dir>/
#   UAT_DIR      = <project-dir>/uat
#   UAT_DASHBOARD= <project-dir>/uat/apps/dashboard
#
# Standard layout:
#   ~/dev/commander/               ← PROJECT_DIR
#     prd/                         ← PRD clone (REPO_ROOT)
#       scripts/                   ← SCRIPT_DIR (this script lives here)
#       apps/dashboard/            ← DASHBOARD_DIR
#     uat/                         ← UAT_DIR (UAT clone, develop branch)
#       apps/dashboard/            ← UAT_DASHBOARD
#
# Run this once to set up the UAT environment.  Safe to re-run (idempotent).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARD_DIR="$REPO_ROOT/apps/dashboard"
PROJECT_DIR="$(cd "$REPO_ROOT/.." && pwd)"

REPO_URL="https://github.com/zealchaiwut/commander.git"
UAT_DIR="$PROJECT_DIR/uat"
UAT_DASHBOARD="$UAT_DIR/apps/dashboard"

echo "=== Commander UAT environment setup ==="

# ── 1. Clone or update ────────────────────────────────────────────────────────
if [ -d "$UAT_DIR/.git" ]; then
    echo "[1/6] $UAT_DIR already exists — skipping clone."
else
    echo "[1/6] Cloning $REPO_URL into $UAT_DIR …"
    git clone "$REPO_URL" "$UAT_DIR"
fi

# ── 2. Checkout develop ───────────────────────────────────────────────────────
echo "[2/6] Checking out develop branch …"
git -C "$UAT_DIR" fetch origin
git -C "$UAT_DIR" checkout develop
git -C "$UAT_DIR" pull origin develop

# ── 3. Python venv ────────────────────────────────────────────────────────────
VENV_DIR="$UAT_DASHBOARD/venv"
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/pip" ]; then
    echo "[3/6] venv already exists at $VENV_DIR — skipping creation."
else
    if [ -d "$VENV_DIR" ]; then
        echo "[3/6] venv directory exists but is broken (bin/pip missing or not executable) — removing and recreating …"
        rm -rf "$VENV_DIR"
    else
        echo "[3/6] Creating Python venv at $VENV_DIR …"
    fi
    if ! python3.12 -m venv "$VENV_DIR" 2>/dev/null && ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        echo "ERROR: Failed to create Python venv at $VENV_DIR." >&2
        echo "       Ensure python3.12 or python3 is available and try again." >&2
        exit 1
    fi
fi

echo "      Installing/upgrading requirements …"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$UAT_DASHBOARD/requirements.txt"

# ── 4. Write .env ─────────────────────────────────────────────────────────────
ENV_FILE="$UAT_DASHBOARD/.env"
if [ -f "$ENV_FILE" ]; then
    echo "[4/6] .env already exists — ensuring DB_PATH is set …"
    if ! grep -q '^DB_PATH=' "$ENV_FILE"; then
        echo "DB_PATH=./commander-uat.db" >> "$ENV_FILE"
        echo "      Added DB_PATH=./commander-uat.db to $ENV_FILE."
    else
        echo "      DB_PATH already set."
    fi
else
    echo "[4/6] Writing $ENV_FILE …"
    cat > "$ENV_FILE" <<'ENVEOF'
PORT=8001
ENVIRONMENT=uat
DB_PATH=./commander-uat.db
ENVEOF
fi

# ── 5. Configure .claude/settings.json with UAT hook target ──────────────────
CLAUDE_DIR="$UAT_DIR/.claude"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
mkdir -p "$CLAUDE_DIR"

if [ -f "$SETTINGS_FILE" ] && grep -q '"HOOK_POST_TARGET"' "$SETTINGS_FILE"; then
    echo "[5/6] .claude/settings.json already has HOOK_POST_TARGET — skipping."
else
    echo "[5/6] Writing UAT .claude/settings.json with HOOK_POST_TARGET …"
    # Use python3 to merge the env block into existing settings (or create fresh)
    PRD_SETTINGS="$REPO_ROOT/.claude/settings.json"
    python3 - "$PRD_SETTINGS" "$SETTINGS_FILE" <<'PYEOF'
import json, sys, os

src_path, dst_path = sys.argv[1], sys.argv[2]

# Load existing dst if present, else use src as base
try:
    with open(dst_path) as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    try:
        with open(src_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

# Inject the env block so UAT agents POST hooks to port 8001
data["env"] = {
    "HOOK_POST_TARGET": "http://localhost:8001/api/agent-event"
}

with open(dst_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

print(f"      Written {dst_path}")
PYEOF
fi

# ── 6. Initialise the UAT database ───────────────────────────────────────────
DB_PATH_VAL="./commander-uat.db"
DB_ABS="$UAT_DASHBOARD/commander-uat.db"
if [ -f "$DB_ABS" ]; then
    echo "[6/6] UAT database already exists at $DB_ABS — skipping init."
else
    echo "[6/6] Initialising UAT database at $DB_ABS …"
    # Run init_db() from the UAT dashboard directory so relative DB_PATH resolves correctly
    (
        cd "$UAT_DASHBOARD"
        DB_PATH="$DB_PATH_VAL" "$VENV_DIR/bin/python" -c "import db; db.init_db()"
    )
    echo "      Database created."
fi

echo ""
echo "=== UAT environment ready ==="
echo "  Directory : $UAT_DIR"
echo "  Branch    : develop"
echo "  Port      : 8001"
echo "  Database  : $DB_ABS"
echo "  Hook target: http://localhost:8001/api/agent-event"
echo ""
echo "Start with: bash $SCRIPT_DIR/start_uat.sh"
