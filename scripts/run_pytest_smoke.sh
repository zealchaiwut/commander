#!/usr/bin/env bash
# Fast smoke subset for CI and local pre-push checks.
# Full suite: pytest tests/ -m "not selenium" --ignore=tests/integration
set -euo pipefail
cd "$(dirname "$0")/.."

export COMMANDER_DISABLE_NEON="${COMMANDER_DISABLE_NEON:-1}"

# Gate: no live HTTP in AC tests (fast, no server needed)
python3 scripts/check_no_live_http_in_tests.py tests/

python3 -m pytest \
  tests/test_508__state_machine.py \
  tests/test_507__explicit_sprint_state.py \
  tests/test_443__dag_builder.py \
  tests/test_450__retry_backoff.py \
  tests/test_doctor__828_exit_codes.py \
  tests/test_doctor__828_pass_fail_labels.py \
  tests/test_doctor__828_runtime.py \
  tests/test_doctor__828_script_exists_executable.py \
  tests/test_doctor__828_complements_789.py \
  tests/test_663__symlink_escape_fix.py \
  tests/test_664__git_repo_validation.py \
  tests/test_git_rev_parse_timeout__693.py \
  tests/integration/test_sandbox_isolation.py \
  tests/test_2312__no_live_http_check.py \
  -m "not agent_browser" \
  -q --tb=short "$@"
