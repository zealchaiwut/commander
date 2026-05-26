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

if [ -f "$TARGET" ]; then
  echo "$TARGET already exists. Remove it first if you want to regenerate."
  exit 1
fi

sed "s|/Users/USER|$HOME|g" "$EXAMPLE" > "$TARGET"
echo "✓ Created $TARGET — review and edit if needed."
