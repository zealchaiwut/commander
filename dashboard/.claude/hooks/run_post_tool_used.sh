#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
exec "$DASHBOARD_DIR/venv/bin/python3" "$DASHBOARD_DIR/hooks/post_tool_used.py" "$@"
