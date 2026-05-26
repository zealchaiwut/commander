#!/usr/bin/env bash
# Convenience wrapper: run the /tester agent against a specific issue.
# Usage: ./scripts/run_tester.sh 42
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMANDER_ROOT="$(cd "$DASHBOARD_DIR/.." && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <issue-number>" >&2
  exit 1
fi

ISSUE_NUM="$1"
cd "$DASHBOARD_DIR"
claude "/tester verify issue ${ISSUE_NUM}"
