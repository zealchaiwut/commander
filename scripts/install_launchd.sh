#!/usr/bin/env bash
# install_launchd.sh — Install a uvicorn dashboard as a macOS LaunchAgent.
#
# Generalized (issue #724): any project environment can register a managed
# service by passing its own label / working dir / uvicorn path / port /
# ENVIRONMENT value. With no parameters the script reproduces the original
# commander dashboard service, so existing commander usage is unchanged.
#
# What it does:
#   1. Renders a .plist for the requested service (label-specific log dir).
#   2. Checks for a port conflict on the requested port (tmux or otherwise).
#   3. Copies the plist to ~/Library/LaunchAgents/ with 644 permissions.
#   4. Loads the service with launchctl and verifies it is listed.
#
# GH_TOKEN (issue #772): a process launched by launchd is detached from the
# login session and cannot read the macOS keychain where `gh` stores creds, so
# the startup repo check fails with "does not exist or is inaccessible". `gh`
# prefers GH_TOKEN over the keychain, so a token is injected into both the plist
# EnvironmentVariables block and the agent .env. The value is supplied at install
# time (--gh-token, or $GH_TOKEN / $GITHUB_TOKEN) — never hard-coded in the repo.
#
# CLAUDE_CODE_OAUTH_TOKEN (issue #827): the same detachment means the `claude`
# CLI cannot read the subscription auth stored in the login keychain / shell rc,
# so every agent dispatch from the launchd-run dashboard fails to authenticate.
# A CLAUDE_CODE_OAUTH_TOKEN is injected into the plist EnvironmentVariables block
# so the claude subprocess inherits it. Supplied at install time (--claude-token
# or $CLAUDE_CODE_OAUTH_TOKEN) — never hard-coded in the repo.
#
# Obtaining the tokens:
#   CLAUDE_CODE_OAUTH_TOKEN  ->  run `claude setup-token`
#   GH_TOKEN                 ->  run `gh auth token`
# Both tokens are written ONLY to the per-user plist (chmod 600) and, for
# GH_TOKEN, the agent .env — never echoed to stdout/stderr or committed.
#
# Usage:
#   install_launchd.sh [--label L] [--working-dir D] [--uvicorn-path P]
#                      [--port N] [--environment E] [--server-app A]
#                      [--claude-token T] [--gh-token T] [--env-file F]
#                      [--print-plist] [--write-env-only]
#
#   --claude-token  Claude Code OAuth token for headless agent dispatch (mint
#                   with `claude setup-token`). If omitted, read from the
#                   $CLAUDE_CODE_OAUTH_TOKEN env var, or prompted for (no echo)
#                   when run interactively.
#   --gh-token      GitHub token for headless `gh` auth (mint with
#                   `gh auth token`). If omitted, read from the $GH_TOKEN or
#                   $GITHUB_TOKEN env var, or prompted for (no echo) when run
#                   interactively.
#   --env-file      Path to the agent .env that receives GH_TOKEN
#                   (default: <working-dir>/.env).
#   --print-plist   Render the plist to stdout and exit 0 WITHOUT touching
#                   launchctl or any port/process (used by tests / dry runs).
#   --write-env-only  Write/update GH_TOKEN in --env-file and exit 0 WITHOUT
#                   touching launchctl or rendering the plist (tests / dry runs).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Defaults reproduce the commander dashboard service ────────────────────────
LABEL="com.commander.dashboard"
WORKING_DIR="$REPO_ROOT/apps/dashboard"
UVICORN_PATH="$REPO_ROOT/venv/bin/uvicorn"
PORT=8000
ENVIRONMENT="prd"
SERVER_APP="server:app"
PRINT_PLIST=false
WRITE_ENV_ONLY=false
GH_TOKEN_ARG=""
CLAUDE_TOKEN_ARG=""
ENV_FILE=""
# Plist EnvironmentVariables fragments — populated only when a token is present,
# so a tokenless install renders the pre-token plist (backward compatible).
GH_TOKEN_PLIST_BLOCK=""
CLAUDE_TOKEN_PLIST_BLOCK=""

# ── Parse args ────────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --label)          LABEL="$2"; shift 2 ;;
    --working-dir)    WORKING_DIR="$2"; shift 2 ;;
    --uvicorn-path)   UVICORN_PATH="$2"; shift 2 ;;
    --port)           PORT="$2"; shift 2 ;;
    --environment)    ENVIRONMENT="$2"; shift 2 ;;
    --server-app)     SERVER_APP="$2"; shift 2 ;;
    --gh-token)       GH_TOKEN_ARG="$2"; shift 2 ;;
    --claude-token)   CLAUDE_TOKEN_ARG="$2"; shift 2 ;;
    --env-file)       ENV_FILE="$2"; shift 2 ;;
    --print-plist)    PRINT_PLIST=true; shift ;;
    --write-env-only) WRITE_ENV_ONLY=true; shift ;;
    -h|--help)
      grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 2 ;;
  esac
done

PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/${LABEL}"
UVICORN_BIN_DIR="$(dirname "$UVICORN_PATH")"
[ -n "$ENV_FILE" ] || ENV_FILE="$WORKING_DIR/.env"

# ── Resolve the token values (issues #772, #827) ──────────────────────────────
# GH_TOKEN precedence:  explicit --gh-token > $GH_TOKEN > $GITHUB_TOKEN.
# CLAUDE precedence:     explicit --claude-token > $CLAUDE_CODE_OAUTH_TOKEN.
# Empty if none — tokens are never hard-coded, so an absent value is simply
# omitted from the rendered plist.
GH_TOKEN_VALUE="${GH_TOKEN_ARG:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
CLAUDE_TOKEN_VALUE="${CLAUDE_TOKEN_ARG:-${CLAUDE_CODE_OAUTH_TOKEN:-}}"

# write_env_token <env-file> <token> — set/replace GH_TOKEN in the agent .env.
# Idempotent: an existing GH_TOKEN line is rewritten in place (no duplicate);
# otherwise the key is appended. Other keys are left untouched.
write_env_token() {
  local env_file="$1" token="$2"
  mkdir -p "$(dirname "$env_file")"
  touch "$env_file"
  if grep -q '^GH_TOKEN=' "$env_file" 2>/dev/null; then
    # Use a tmp file so this works the same on macOS/BSD and GNU sed.
    grep -v '^GH_TOKEN=' "$env_file" > "${env_file}.tmp"
    printf 'GH_TOKEN=%s\n' "$token" >> "${env_file}.tmp"
    mv "${env_file}.tmp" "$env_file"
  else
    printf 'GH_TOKEN=%s\n' "$token" >> "$env_file"
  fi
}

# ── Render the plist ──────────────────────────────────────────────────────────
# Built inline (no commander-specific template) so every value is parametrized.
render_plist() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>

  <!-- Service identity -->
  <key>Label</key>
  <string>${LABEL}</string>

  <!-- Run on user login -->
  <key>RunAtLoad</key>
  <true/>

  <!-- Restart only on non-zero exit (crash / kill -9), not on clean shutdown -->
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>

  <!-- Working directory -->
  <key>WorkingDirectory</key>
  <string>${WORKING_DIR}</string>

  <!-- Launch command -->
  <key>ProgramArguments</key>
  <array>
    <string>${UVICORN_PATH}</string>
    <string>${SERVER_APP}</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>${PORT}</string>
  </array>

  <!-- Environment: dynamically resolved tool dirs on PATH + the ENVIRONMENT selector -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PATH_VALUE}</string>
    <key>ENVIRONMENT</key>
    <string>${ENVIRONMENT}</string>${GH_TOKEN_PLIST_BLOCK}${CLAUDE_TOKEN_PLIST_BLOCK}
  </dict>

  <!-- Log files under ~/Library/Logs/<label>/ -->
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/stderr.log</string>

</dict>
</plist>
PLIST
}

# ── Dry run: write GH_TOKEN to the agent .env and exit (no launchctl) ──────────
# Handled before tool resolution: writing the token to .env is independent of
# whether claude/gh are installed, so it must not require them.
if $WRITE_ENV_ONLY; then
  if [ -z "$GH_TOKEN_VALUE" ]; then
    echo "ERROR: --write-env-only needs a token (--gh-token, \$GH_TOKEN, or \$GITHUB_TOKEN)." >&2
    exit 1
  fi
  write_env_token "$ENV_FILE" "$GH_TOKEN_VALUE"
  echo "Wrote GH_TOKEN to $ENV_FILE"
  exit 0
fi

# ── Interactive prompts + missing-token warnings (issues #772, #827) ──────────
# When a token is missing and we have a terminal, prompt for it with echo off
# (`read -rs`) so the value never shows on screen or in scrollback. In a
# non-interactive run (CI, sprint dispatch) we skip the prompt and only warn.
if [ -z "$CLAUDE_TOKEN_VALUE" ] && [ -t 0 ]; then
  read -rs -p "CLAUDE_CODE_OAUTH_TOKEN (claude setup-token; leave blank to skip): " CLAUDE_TOKEN_VALUE
  echo >&2
fi
if [ -z "$GH_TOKEN_VALUE" ] && [ -t 0 ]; then
  read -rs -p "GH_TOKEN (gh auth token; leave blank to skip): " GH_TOKEN_VALUE
  echo >&2
fi

# Named warnings so the operator knows exactly what will break. Routed to stderr
# so they never contaminate --print-plist stdout. The token VALUES are never
# printed — only the fact that they are missing.
if [ -z "$CLAUDE_TOKEN_VALUE" ]; then
  echo "WARNING: CLAUDE_CODE_OAUTH_TOKEN not supplied — agent dispatch will fail;" >&2
  echo "         the launchd-detached claude CLI cannot authenticate. Mint one" >&2
  echo "         with: claude setup-token" >&2
fi
if [ -z "$GH_TOKEN_VALUE" ]; then
  echo "WARNING: GH_TOKEN not supplied — gh API calls will fail under launchd" >&2
  echo "         (no keychain access). Mint one with: gh auth token" >&2
fi

# Build the optional plist fragments only when a token is present, so a
# tokenless install renders exactly the pre-token plist (backward compatible).
if [ -n "$GH_TOKEN_VALUE" ]; then
  GH_TOKEN_PLIST_BLOCK=$'\n    <key>GH_TOKEN</key>\n    <string>'"${GH_TOKEN_VALUE}"$'</string>'
fi
if [ -n "$CLAUDE_TOKEN_VALUE" ]; then
  CLAUDE_TOKEN_PLIST_BLOCK=$'\n    <key>CLAUDE_CODE_OAUTH_TOKEN</key>\n    <string>'"${CLAUDE_TOKEN_VALUE}"$'</string>'
fi

# ── Resolve tool directories dynamically (issues #826, #852) ──────────────────
# A launchd service is detached from the login shell, so its PATH must contain
# the real on-disk dirs of `claude` and `gh`. Hardcoding those dirs broke on
# machines where the binaries live outside the standard locations (e.g.
# ~/.local/bin), making every agent dispatch fail with "claude CLI not found".
# Resolve them at install time with `command -v` and add their parent dirs (plus
# the project venv bin/, resolved from the repo root) to the plist PATH. If
# either tool is missing we abort BEFORE rendering or writing any plist.
#
# Contracted PATH guarantees (issue #852): resolved tool dirs come first, the
# standard system dirs follow as a trailing baseline, every directory appears at
# most once (no duplicates), and each run rebuilds the value from scratch so a
# fresh install fully overwrites any plist left by a prior (hardcoded) install.
abs_parent_dir() {
  # Print the absolute parent directory of "$1", resolving relative paths.
  cd "$(dirname "$1")" >/dev/null 2>&1 && pwd
}

CLAUDE_PATH="$(command -v claude 2>/dev/null || true)"
if [ -z "$CLAUDE_PATH" ]; then
  echo "ERROR: 'claude' CLI not found in PATH. Aborting." >&2
  exit 1
fi
CLAUDE_BIN_DIR="$(abs_parent_dir "$CLAUDE_PATH")"

GH_PATH="$(command -v gh 2>/dev/null || true)"
if [ -z "$GH_PATH" ]; then
  echo "ERROR: 'gh' CLI not found in PATH. Aborting." >&2
  exit 1
fi
GH_BIN_DIR="$(abs_parent_dir "$GH_PATH")"

# Build the plist PATH from the resolved dirs (venv bin, claude, gh) plus the
# standard system dirs, de-duplicated so each directory appears at most once.
PATH_VALUE=""
_append_path_dir() {
  local dir="$1"
  [ -n "$dir" ] || return 0
  case ":$PATH_VALUE:" in
    *":$dir:"*) ;;                                   # already present
    *) PATH_VALUE="${PATH_VALUE:+$PATH_VALUE:}$dir" ;;
  esac
}
for _dir in "$UVICORN_BIN_DIR" "$CLAUDE_BIN_DIR" "$GH_BIN_DIR" \
            /usr/local/bin /opt/homebrew/bin /usr/bin /bin /usr/sbin /sbin; do
  _append_path_dir "$_dir"
done

# ── Dry run: print and exit before any side effects ───────────────────────────
if $PRINT_PLIST; then
  render_plist
  exit 0
fi

echo "=== LaunchAgent Installer ==="
echo "Label       : $LABEL"
echo "Working dir : $WORKING_DIR"
echo "Uvicorn     : $UVICORN_PATH"
echo "Port        : $PORT"
echo "Environment : $ENVIRONMENT"
echo "claude bin  : $CLAUDE_BIN_DIR"
echo "gh bin      : $GH_BIN_DIR"
echo "venv bin    : $UVICORN_BIN_DIR"
if [ -n "$CLAUDE_TOKEN_VALUE" ]; then
  echo "CLAUDE_TOKEN: set (headless agent dispatch) -> plist"
else
  echo "CLAUDE_TOKEN: not supplied — agent dispatch will fail under launchd"
fi
if [ -n "$GH_TOKEN_VALUE" ]; then
  echo "GH_TOKEN    : set (headless gh auth) -> plist + $ENV_FILE"
else
  echo "GH_TOKEN    : not supplied — gh will fall back to the keychain (may fail under launchd)"
fi

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -x "$UVICORN_PATH" ] && [ ! -f "$UVICORN_PATH" ]; then
  echo "ERROR: uvicorn not found at $UVICORN_PATH — set up the venv first."
  exit 1
fi

if [ ! -d "$WORKING_DIR" ]; then
  echo "ERROR: working directory not found at $WORKING_DIR"
  exit 1
fi

# ── Port conflict check ───────────────────────────────────────────────────────
CONFLICT_PID=""
if command -v lsof >/dev/null 2>&1; then
  CONFLICT_PID=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
fi

if [ -n "$CONFLICT_PID" ]; then
  TMUX_PIDS=""
  if command -v tmux >/dev/null 2>&1; then
    TMUX_PIDS=$(tmux list-panes -a -F "#{pane_pid}" 2>/dev/null || true)
  fi

  IS_TMUX=false
  for pid in $CONFLICT_PID; do
    if echo "$TMUX_PIDS" | grep -q "^${pid}$"; then
      IS_TMUX=true
      break
    fi
    ppid=$pid
    for _ in 1 2 3 4 5; do
      ppid=$(ps -o ppid= -p "$ppid" 2>/dev/null | tr -d ' ' || true)
      [ -z "$ppid" ] || [ "$ppid" = "0" ] || [ "$ppid" = "1" ] && break
      if echo "$TMUX_PIDS" | grep -q "^${ppid}$"; then
        IS_TMUX=true
        break
      fi
    done
    $IS_TMUX && break
  done

  if $IS_TMUX; then
    echo ""
    echo "WARNING: A process managed by tmux is already listening on port $PORT."
    echo "         Installing launchd alongside an active tmux session can cause a"
    echo "         port conflict on next reboot or crash-restart."
    echo ""
    echo "         To proceed: stop the tmux session first, then re-run this script."
    echo "         Exiting without installing."
    exit 1
  else
    echo ""
    echo "WARNING: Another process (PID(s): $CONFLICT_PID) is already listening on"
    echo "         port $PORT. Installing launchd now may cause a conflict."
    echo ""
    echo "         Stop that process first, then re-run this script."
    echo "         Exiting without installing."
    exit 1
  fi
fi

# ── Already installed? ────────────────────────────────────────────────────────
if launchctl list 2>/dev/null | grep -q "$LABEL"; then
  echo "Service '$LABEL' is already loaded. Unload it first with:"
  echo "  bash scripts/uninstall_launchd.sh --label $LABEL"
  exit 1
fi

# ── Install plist + log dir ───────────────────────────────────────────────────
echo "Generating plist..."
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$LOG_DIR"

# Create the file with a restrictive umask, then chmod 600, so the token values
# in EnvironmentVariables are never world/group-readable at rest (issue #827).
( umask 077; render_plist > "$PLIST_DEST" )
chmod 600 "$PLIST_DEST"
echo "Plist installed to: $PLIST_DEST (permissions: 600)"

# Mirror the token into the agent .env so anything that sources .env at runtime
# (not just the launchd environment) sees the same GH_TOKEN (issue #772).
if [ -n "$GH_TOKEN_VALUE" ]; then
  write_env_token "$ENV_FILE" "$GH_TOKEN_VALUE"
  echo "GH_TOKEN written to: $ENV_FILE"
fi

# ── Load the service ──────────────────────────────────────────────────────────
echo "Loading service with launchctl..."
launchctl load "$PLIST_DEST"

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
echo "Verifying service registration..."
if launchctl list | grep -q "$LABEL"; then
  echo "SUCCESS: Service '$LABEL' is loaded and running."
  echo ""
  launchctl list | grep "$LABEL"
  echo ""
  echo "Logs:"
  echo "  stdout: $LOG_DIR/stdout.log"
  echo "  stderr: $LOG_DIR/stderr.log"
  echo ""
  echo "To uninstall: bash scripts/uninstall_launchd.sh --label $LABEL"
else
  echo "FAILURE: Service does not appear in launchctl list."
  echo "Check $LOG_DIR/stderr.log for errors."
  exit 1
fi
