#!/usr/bin/env bash
# gh_reauth.sh — re-authenticate gh and propagate the token everywhere the
# headless dashboard reads it, in one command.
#
# A launchd-detached process can't read the macOS keychain, so the GitHub token
# lives in THREE places that must stay in sync:
#   1. the macOS keychain        (interactive `gh`)
#   2. apps/dashboard/.env        (authoritative for the dashboard process)
#   3. the launchd plist          (EnvironmentVariables → headless gh/subprocess)
# When the token expires, all three break. The token-validity doctor only
# DETECTS this; this script fixes it.
#
# RECURRENCE: browser/device `gh auth login` mints a SHORT-LIVED token, so this
# keeps breaking. Pass a long-lived PAT (classic no-expiry, or fine-grained ~1yr,
# scope: repo) to stop the fire-drill:
#     scripts/gh_reauth.sh --token <PAT>
#
# Usage:
#   scripts/gh_reauth.sh                 # interactive `gh auth login`
#   scripts/gh_reauth.sh --token <PAT>   # set a long-lived PAT (recommended)
#   scripts/gh_reauth.sh --no-restart    # update creds only; don't restart dashboard
#
# The token is never echoed to stdout/stderr.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.commander.dashboard"
ENV_FILE="$REPO_ROOT/apps/dashboard/.env"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
TOKEN=""
RESTART=true

while [ $# -gt 0 ]; do
  case "$1" in
    --token)      TOKEN="$2"; shift 2 ;;
    --label)      LABEL="$2"; PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"; shift 2 ;;
    --env-file)   ENV_FILE="$2"; shift 2 ;;
    --no-restart) RESTART=false; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# The invalid GH_TOKEN in the environment overrides the keychain AND blocks
# `gh auth login` — clear it for this script's process only.
unset GH_TOKEN GITHUB_TOKEN

echo "==> Authenticating gh…"
if [ -n "$TOKEN" ]; then
  printf '%s' "$TOKEN" | gh auth login -h github.com --with-token
else
  gh auth login -h github.com   # interactive (needs a TTY)
fi

echo "==> Verifying…"
gh auth status -h github.com >/dev/null 2>&1 || { echo "ERROR: gh still not authenticated." >&2; exit 1; }
NEW="$(gh auth token -h github.com)"
[ -n "$NEW" ] || { echo "ERROR: could not read 'gh auth token'." >&2; exit 1; }

echo "==> Writing GH_TOKEN to $ENV_FILE (authoritative for the dashboard)…"
bash "$REPO_ROOT/scripts/install_launchd.sh" --write-env-only --gh-token "$NEW" --env-file "$ENV_FILE" >/dev/null

if [ -f "$PLIST" ]; then
  echo "==> Patching launchd plist EnvironmentVariables…"
  /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:GH_TOKEN $NEW" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:GH_TOKEN string $NEW" "$PLIST" 2>/dev/null \
    || echo "   (plist patch skipped — .env is authoritative, so the dashboard still picks up the new token)"
fi

if $RESTART; then
  echo "==> Restarting dashboard…"
  if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
    # launchd is the authoritative runner — kickstart picks up the patched plist + .env
    launchctl kickstart -k "gui/$(id -u)/${LABEL}"
  else
    # fallback: manual runner
    kill -9 "$(cat "$REPO_ROOT/apps/dashboard/prd.pid" 2>/dev/null)" 2>/dev/null || true
    rm -f "$REPO_ROOT/apps/dashboard/prd.pid"
    lsof -i :8000 -sTCP:LISTEN -t 2>/dev/null | xargs -r kill -9 2>/dev/null || true
    bash "$REPO_ROOT/scripts/start_prd.sh"
  fi
else
  echo "==> --no-restart: restart the dashboard yourself to load the new token."
fi

echo "==> Done. Confirm with:  gh auth status   and the dashboard's GitHub doctor."
