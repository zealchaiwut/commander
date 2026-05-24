#!/usr/bin/env bash
# setup_uat_env.sh — Clones the repo into ~/commander/dashboard-uat, checks out
# develop, installs a Python venv, writes .env, and initialises the UAT database.
#
# Run this once to set up the UAT environment.  Safe to re-run (idempotent).

set -euo pipefail

REPO_URL="https://github.com/zealchaiwut/commander.git"
UAT_DIR="$HOME/commander/dashboard-uat"
UAT_DASHBOARD="$UAT_DIR/dashboard"

echo "=== Commander UAT environment setup ==="

# ── 1. Clone or update ────────────────────────────────────────────────────────
if [ -d "$UAT_DIR/.git" ]; then
    echo "[1/5] $UAT_DIR already exists — skipping clone."
else
    echo "[1/5] Cloning $REPO_URL into $UAT_DIR …"
    git clone "$REPO_URL" "$UAT_DIR"
fi

# ── 2. Checkout develop ───────────────────────────────────────────────────────
echo "[2/5] Checking out develop branch …"
git -C "$UAT_DIR" fetch origin
git -C "$UAT_DIR" checkout develop
git -C "$UAT_DIR" pull origin develop

# ── 3. Python venv ────────────────────────────────────────────────────────────
VENV_DIR="$UAT_DASHBOARD/venv"
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/pip" ]; then
    echo "[3/5] venv already exists at $VENV_DIR — skipping creation."
else
    if [ -d "$VENV_DIR" ]; then
        echo "[3/5] venv directory exists but is broken (bin/pip missing or not executable) — removing and recreating …"
        rm -rf "$VENV_DIR"
    else
        echo "[3/5] Creating Python venv at $VENV_DIR …"
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
    echo "[4/5] .env already exists — ensuring DB_PATH is set …"
    if ! grep -q '^DB_PATH=' "$ENV_FILE"; then
        echo "DB_PATH=./commander-uat.db" >> "$ENV_FILE"
        echo "      Added DB_PATH=./commander-uat.db to $ENV_FILE."
    fi
else
    echo "[4/5] Writing $ENV_FILE …"
    cat > "$ENV_FILE" <<'ENVEOF'
PORT=8001
ENVIRONMENT=uat
DB_PATH=./commander-uat.db
ENVEOF
fi

# ── 5. Initialise the UAT database ───────────────────────────────────────────
DB_PATH_VAL="./commander-uat.db"
DB_ABS="$UAT_DASHBOARD/commander-uat.db"
if [ -f "$DB_ABS" ]; then
    echo "[5/5] UAT database already exists at $DB_ABS — skipping init."
else
    echo "[5/5] Initialising UAT database at $DB_ABS …"
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
echo ""
echo "Start with: bash ~/commander/work-coder/dashboard/scripts/start_uat.sh"
