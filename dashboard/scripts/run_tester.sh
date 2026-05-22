#!/usr/bin/env bash
# Convenience wrapper: run the /tester agent against a specific issue.
# Usage: ./scripts/run_tester.sh 42
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <issue-number>" >&2
  exit 1
fi

ISSUE_NUM="$1"
cd "$HOME/commander/dashboard"
claude "/tester verify issue ${ISSUE_NUM}"
