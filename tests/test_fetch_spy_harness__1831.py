"""Tests for issue #1831: Wire fetch-spy harness into behavioral page-load tests.

Runs the Node.js call-budget-page-load.test.mjs suite and asserts it passes.
Structural/import checks removed per CLAUDE.md #1746 (issue #1995) — source-regex
assertions add false confidence and cannot catch runtime regressions (the #982 miss).
Behavioral coverage lives entirely in the .mjs suite itself.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_TEST = _REPO_ROOT / "tests" / "frontend" / "call-budget-page-load.test.mjs"


def _parse_tap_fail_count(output: str) -> int:
    """Return the failure count from Node's TAP '# fail N' summary line, or 0 if absent."""
    m = re.search(r"# fail\s+(\d+)", output)
    return int(m.group(1)) if m else 0


def test_call_budget_page_load_test_file_exists():
    """call-budget-page-load.test.mjs must exist in tests/frontend/."""
    assert _FRONTEND_TEST.exists(), (
        f"call-budget-page-load.test.mjs must exist at {_FRONTEND_TEST}"
    )


def test_node_test_suite_runs_and_passes():
    """Running the Node.js test suite must exit 0 (all tests pass)."""
    result = subprocess.run(
        ["node", "--test", str(_FRONTEND_TEST)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node --test {_FRONTEND_TEST.name} must exit 0.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_node_test_suite_has_at_least_10_tests():
    """Suite must include at least 10 behavioral tests (board, history, running, AC6 + cache/edge cases)."""
    result = subprocess.run(
        ["node", "--test", str(_FRONTEND_TEST)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    m = re.search(r"tests\s+(\d+)", output)
    test_count = int(m.group(1)) if m else 0
    assert test_count >= 10, (
        f"Suite should have at least 10 tests, found {test_count}. Output:\n{output}"
    )


def test_node_test_suite_zero_failures():
    """All node tests must pass with zero failures."""
    result = subprocess.run(
        ["node", "--test", str(_FRONTEND_TEST)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node --test {_FRONTEND_TEST.name} must exit 0.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    output = result.stdout + result.stderr
    failure_count = _parse_tap_fail_count(output)
    assert failure_count == 0, (
        f"Test suite must report 0 failures, got {failure_count}. Output:\n{output}"
    )
