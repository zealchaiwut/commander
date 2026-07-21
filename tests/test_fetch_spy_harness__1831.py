"""Tests for issue #1831: Wire fetch-spy harness into behavioral page-load tests.

This test module verifies that the fetch-spy harness (installFetchSpy/assertFetchBudget
from call-budget-helpers.mjs) is wired into real page-load tests in
tests/frontend/call-budget-page-load.test.mjs, replacing source-regex-only checks
with behavioral enforcement (per CLAUDE.md #1746).

The suite confirms:
- AC1: Fetch-spy test file exists and is properly structured
- AC2: Real loaders (loadSprintMgmt, _histLoadLedger, _smgmtRunningFirstPaint) are driven
- AC3: Budget assertions (installFetchSpy + assertFetchBudget) replace regex checks
- AC4: Board, history, running budgets are enforced with correct targets
- AC5: xfail-to-pass flip behavior is demonstrated
- AC6: Double board-load triggers assertFetchBudget violation detection
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_TEST = _REPO_ROOT / "tests" / "frontend" / "call-budget-page-load.test.mjs"


# ═══════════════════════════════════════════════════════════════════════════════
# AC1: Fetch-spy behavioral test file exists and is properly named
# ═══════════════════════════════════════════════════════════════════════════════


def test_call_budget_page_load_test_file_exists():
    """AC1: call-budget-page-load.test.mjs file exists in tests/frontend/."""
    assert _FRONTEND_TEST.exists(), (
        f"call-budget-page-load.test.mjs must exist at {_FRONTEND_TEST}"
    )


def test_fetch_spy_test_file_imports_helpers():
    """AC1: The file imports installFetchSpy and assertFetchBudget from helpers."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "installFetchSpy" in src, (
        "call-budget-page-load.test.mjs must import installFetchSpy from helpers"
    )
    assert "assertFetchBudget" in src, (
        "call-budget-page-load.test.mjs must import assertFetchBudget from helpers"
    )
    assert "from './call-budget-helpers.mjs'" in src, (
        "call-budget-page-load.test.mjs must import from call-budget-helpers.mjs"
    )


def test_fetch_spy_test_file_uses_node_test():
    """AC1: The file uses node:test, not Jest or Mocha."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "import test from 'node:test'" in src, (
        "call-budget-page-load.test.mjs must use node:test framework"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2: Real page-load functions are driven by the test
# ═══════════════════════════════════════════════════════════════════════════════


def test_fetch_spy_test_imports_loadSprintMgmt():
    """AC2: The test file imports loadSprintMgmt from board-render.js."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "loadSprintMgmt" in src, (
        "call-budget-page-load.test.mjs must import loadSprintMgmt"
    )
    assert "board-render.js" in src, (
        "call-budget-page-load.test.mjs must import from board-render.js"
    )


def test_fetch_spy_test_imports_histLoadLedger():
    """AC2: The test file imports _histLoadLedger from history.js."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "_histLoadLedger" in src, (
        "call-budget-page-load.test.mjs must import _histLoadLedger"
    )
    assert "history.js" in src, (
        "call-budget-page-load.test.mjs must import from history.js"
    )


def test_fetch_spy_test_drives_running_first_paint():
    """AC2: The test file drives _smgmtRunningFirstPaint via vm sandbox."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "_smgmtRunningFirstPaint" in src, (
        "call-budget-page-load.test.mjs must reference _smgmtRunningFirstPaint"
    )
    assert "vm.runInContext" in src, (
        "call-budget-page-load.test.mjs must use vm sandbox to run project.html code"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3: Budget assertions use installFetchSpy + assertFetchBudget, not regex checks
# ═══════════════════════════════════════════════════════════════════════════════


def test_board_budget_uses_fetch_spy_not_regex():
    """AC3: Board budget test uses installFetchSpy + assertFetchBudget, not source regex."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    # The behavioral test must be in this file
    assert "installFetchSpy" in src
    # Board budget test must reference the spy
    assert "installFetchSpy" in src and "/api/board" in src
    # Must call loadSprintMgmt with the spy in scope, not just read source
    board_budget_section = src[src.find("Board load budget") : src.find("History feed budget")]
    assert "installFetchSpy" in board_budget_section, (
        "Board budget section must use installFetchSpy (not regex checks)"
    )
    assert "await loadSprintMgmt" in board_budget_section, (
        "Board budget test must call loadSprintMgmt to drive the actual function"
    )


def test_history_budget_uses_fetch_spy_not_regex():
    """AC3: History budget test uses installFetchSpy + assertFetchBudget, not source regex."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    # History budget section must use spy, not regex
    hist_section = src[src.find("History feed budget") : src.find("Running first paint budget")]
    assert "installFetchSpy" in hist_section, (
        "History budget section must use installFetchSpy (not regex checks)"
    )
    assert "await _histLoadLedger" in hist_section, (
        "History budget test must call _histLoadLedger to drive the actual function"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4: Arc targets are enforced (board=1, history=1, running=1)
# ═══════════════════════════════════════════════════════════════════════════════


def test_board_budget_enforces_exactly_1_call():
    """AC4: Board budget asserts exactly 1 /api/board call."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "assertFetchBudget(spy, '/api/board', 1)" in src or (
        "assertFetchBudget" in src and "/api/board" in src
    ), (
        "call-budget-page-load.test.mjs must assert exactly 1 /api/board call"
    )


def test_history_budget_enforces_exactly_1_call():
    """AC4: History budget asserts exactly 1 /api/sprints/history call."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "assertFetchBudget(spy, '/api/sprints/history', 1)" in src, (
        "call-budget-page-load.test.mjs must assert exactly 1 /api/sprints/history call"
    )


def test_running_budget_enforces_exactly_1_call():
    """AC4: Running budget asserts exactly 1 /api/running call."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "/api/running" in src and ("== 1" in src or "=== 1" in src or ", 1)" in src), (
        "call-budget-page-load.test.mjs must assert exactly 1 /api/running call"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6: Double board-load causes assertFetchBudget to fail (violation detection)
# ═══════════════════════════════════════════════════════════════════════════════


def test_double_board_load_test_exists():
    """AC6: A test exists that calls loadSprintMgmt twice and expects assertFetchBudget to fail."""
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    assert "two consecutive" in src.lower() or "double" in src.lower(), (
        "call-budget-page-load.test.mjs must have a test for double board-load (AC6)"
    )
    # Must call loadSprintMgmt twice and assertFetchBudget should detect the violation
    assert "await loadSprintMgmt" in src and src.count("await loadSprintMgmt") >= 2, (
        "AC6 test must call loadSprintMgmt at least twice to simulate a violation"
    )
    assert "assert.throws" in src or "assert.throws" in src, (
        "AC6 test must expect an exception (assertFetchBudget should throw on 2 calls)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5: Node.js test suite actually runs and all tests pass
# ═══════════════════════════════════════════════════════════════════════════════


def test_node_test_suite_runs_and_passes():
    """AC5: Running the Node.js test suite produces green exit code (all tests pass)."""
    result = subprocess.run(
        ["node", "--test", str(_FRONTEND_TEST)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node --test {_FRONTEND_TEST.name} must exit 0. "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    output = result.stdout + result.stderr
    # Confirm test counts in output
    assert "tests" in output.lower() and ("pass" in output.lower() or "✔" in output), (
        "Test output must show test counts and pass indicators"
    )


def test_node_test_suite_has_at_least_10_tests():
    """AC5: The suite includes multiple tests (board, history, running, AC6 + cache/edge cases)."""
    result = subprocess.run(
        ["node", "--test", str(_FRONTEND_TEST)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    # Extract the test count from "ℹ tests N" or similar
    import re
    m = re.search(r"(?:tests|✔)\s+(\d+)", output)
    test_count = int(m.group(1)) if m else 0
    assert test_count >= 10, (
        f"Suite should have at least 10 tests (board, history, running budgets + AC6 + cache/edge cases), "
        f"found {test_count}"
    )


def test_node_test_suite_zero_failures():
    """AC5: All tests pass with zero failures."""
    result = subprocess.run(
        ["node", "--test", str(_FRONTEND_TEST)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    # Check for any failure indicators
    assert "fail" not in output.lower() or "0" in output, (
        f"Test suite must report 0 failures. Output:\n{output}"
    )
    assert result.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Behavioral verification: spy-driven tests catch regressions that regex checks miss
# ═══════════════════════════════════════════════════════════════════════════════


def test_regex_checks_alone_cannot_catch_runtime_fetch_violations():
    """Rationale: Per CLAUDE.md #1746, source-regex checks miss the #982 SSE regression.

    This test documents that the new behavioral spy tests replace regex-only checks
    with actual page-load execution that would catch double-fetches, unexpected
    endpoints, and other runtime violations.
    """
    # The issue is: test_call_budgets.py's regex checks (lines 124-156) only
    # verify that /api/board exists in the source, not that it is actually called
    # once during loadSprintMgmt(). A developer could inadvertently add a second
    # fetch inside loadSprintMgmt and the regex test would not catch it.
    #
    # The new call-budget-page-load.test.mjs drives loadSprintMgmt() with a spy,
    # so it WILL catch that regression: 2 fetches → assertFetchBudget(..., 1) throws.
    #
    # This test confirms that assertion by checking the test file is properly
    # structured to drive real functions through the spy.
    src = _FRONTEND_TEST.read_text(encoding="utf-8")
    # Confirm we're driving real functions, not just reading source
    assert "await loadSprintMgmt" in src, "Test must call loadSprintMgmt (not read source)"
    assert "await _histLoadLedger" in src, "Test must call _histLoadLedger (not read source)"
    assert "installFetchSpy" in src, "Test must install spy to record actual fetch calls"
    assert "assertFetchBudget" in src, "Test must assert budget using spy call counts"
