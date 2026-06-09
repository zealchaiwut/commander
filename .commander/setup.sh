#!/usr/bin/env bash
# Bootstrap .commander/sprint.yaml from .commander/sprint.yaml.example
# Replaces /Users/USER with the current user's home directory.

set -e

EXAMPLE=".commander/sprint.yaml.example"
TARGET=".commander/sprint.yaml"

if [ ! -f "$EXAMPLE" ]; then
  echo "Error: $EXAMPLE not found. Run from repo root."
  exit 1
fi

# ── Impeccable design skill pack (issue #713) ────────────────────────────────
# Frontend agents (BA, coder, tester) load design rules via context.mjs, so the
# file-based skill pack must exist after setup. Idempotent: skip install when the
# pack is already present, then verify and fail loudly if context.mjs is missing.
IMPECCABLE_CONTEXT_MJS=".github/skills/impeccable/scripts/context.mjs"
if [ ! -f "$IMPECCABLE_CONTEXT_MJS" ]; then
  echo "Installing impeccable skills (npx impeccable skills install) ..."
  npx --yes impeccable skills install || true
fi
if [ ! -f "$IMPECCABLE_CONTEXT_MJS" ]; then
  echo "Error: $IMPECCABLE_CONTEXT_MJS missing after 'npx impeccable skills install'." >&2
  echo "       Design skill pack is required for frontend tickets. Setup failed." >&2
  exit 1
fi
echo "✓ impeccable skills present ($IMPECCABLE_CONTEXT_MJS)"

if [ -f "$TARGET" ]; then
  echo "$TARGET already exists. Remove it first if you want to regenerate."
  exit 1
fi

sed "s|/Users/USER|$HOME|g" "$EXAMPLE" > "$TARGET"
echo "✓ Created $TARGET — review and edit if needed."
