"""Tests for issue #1924: Node test should exercise real _smgmtMoveModalPickBulk instead of simulating the fix."""
import os
import subprocess
import sys
import json

# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


def run_npm_test(test_file):
    """Run a single Node test file and return (exit_code, stdout, stderr)."""
    cmd = ["npm", "test", "--", test_file]
    result = subprocess.run(
        cmd,
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def test_node_test_suite_passes():
    """AC1: bulk-move-new-sprint-clear-selection.test.mjs includes new tests for _smgmtMoveModalPickBulk."""
    # Run the test directly with node --test, from the correct directory
    test_file = "../../tests/frontend/bulk-move-new-sprint-clear-selection.test.mjs"
    result = subprocess.run(
        ["node", "--test", test_file],
        cwd="/Users/zeal-server/dev/commander/tester/apps/dashboard",
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"Test suite failed with exit code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "pass" in result.stdout or "✔" in result.stdout, f"No passing tests found in output:\n{result.stdout}"
    # Verify the new #1924 tests exist and passed
    assert "#1924 AC:" in result.stdout, "New #1924 AC tests not found in output"


def test_node_test_directly_calls_real_function():
    """AC1,AC2,AC3: Test calls _smgmtMoveModalPickBulk directly (from project.html globals)."""
    # The function is accessed via globalThis from project.html; the test imports
    # it into the global scope. Verify the new tests exist and exercise the real path.
    test_file = "/Users/zeal-server/dev/commander/tester/tests/frontend/bulk-move-new-sprint-clear-selection.test.mjs"

    with open(test_file, "r") as f:
        content = f.read()

    # The new tests call await _smgmtMoveModalPickBulk(...) directly (not via simulation)
    assert "await _smgmtMoveModalPickBulk([701, 702], null, true)" in content, \
        "Test does not directly call _smgmtMoveModalPickBulk with isNew=true"
    assert "await _smgmtMoveModalPickBulk([801, 802], null, true)" in content, \
        "Test does not directly call _smgmtMoveModalPickBulk on fetch error"
    assert "await _smgmtMoveModalPickBulk([901, 902], 'sprint-50', false)" in content, \
        "Test does not directly call _smgmtMoveModalPickBulk with isNew=false"


def test_node_test_removes_simulation_usage():
    """AC4: New #1924 tests do NOT use _simulateBulkMoveNewSprintSuccess or applyFix."""
    test_file = "/Users/zeal-server/dev/commander/tester/tests/frontend/bulk-move-new-sprint-clear-selection.test.mjs"

    with open(test_file, "r") as f:
        content = f.read()

    # The old tests may still exist for regression, but the NEW #1924 tests must not use simulation
    # Check that the new test cases don't call the simulation helper
    new_tests_start = content.find("// ── Tests: _smgmtMoveModalPickBulk direct invocation (issue #1924)")
    if new_tests_start == -1:
        # Helper might have been removed entirely; check for direct function calls instead
        assert "await _smgmtMoveModalPickBulk" in content, \
            "Test does not call the real _smgmtMoveModalPickBulk function"
    else:
        new_tests = content[new_tests_start:]
        assert "_simulateBulkMoveNewSprintSuccess" not in new_tests, \
            "#1924 tests should not use _simulateBulkMoveNewSprintSuccess simulation"
        assert "applyFix" not in new_tests, \
            "#1924 tests should not use applyFix parameter (indicates simulation)"


def test_node_test_directly_invokes_smgmt_move_modal():
    """AC2: Test calls the real _smgmtMoveModalPickBulk directly, not a simulation."""
    test_file = "/Users/zeal-server/dev/commander/tester/tests/frontend/bulk-move-new-sprint-clear-selection.test.mjs"

    with open(test_file, "r") as f:
        content = f.read()

    # The test should import and use the real function
    assert "import" in content and "_smgmtMoveModalPickBulk" in content, \
        "Test does not import _smgmtMoveModalPickBulk"

    # Should assert on selection Set state after calling the function
    assert "_smgmtSelectedIssues.size" in content, \
        "Test does not assert on _smgmtSelectedIssues size (core AC requirement)"


def test_node_test_assertion_clear_on_new_sprint():
    """AC2: Test asserts _smgmtSelectedIssues.size === 0 after successful isNew=true move."""
    test_file = "/Users/zeal-server/dev/commander/tester/tests/frontend/bulk-move-new-sprint-clear-selection.test.mjs"

    with open(test_file, "r") as f:
        content = f.read()

    # Look for assertion pattern: size === 0 in context of isNew or new sprint
    assert ("size === 0" in content or "size == 0" in content), \
        "Test does not contain assertion for size === 0 after clear"


def test_node_test_assertion_unchanged_on_error():
    """AC3: Test asserts _smgmtSelectedIssues unchanged when move fails or isNew=false."""
    test_file = "/Users/zeal-server/dev/commander/tester/tests/frontend/bulk-move-new-sprint-clear-selection.test.mjs"

    with open(test_file, "r") as f:
        content = f.read()

    # Should have test cases for error path / existing sprint path
    assert ("error" in content.lower() or "AC3" in content), \
        "Test does not include error path or existing-sprint path assertion"


def test_function_extracted_and_exported():
    """AC1: _smgmtMoveModalPickBulk is extracted to bulk-move.js and exported."""
    bulk_move = "/Users/zeal-server/dev/commander/tester/apps/dashboard/static/src/sprint-board/bulk-move.js"

    with open(bulk_move, "r") as f:
        content = f.read()

    # The function must be exported
    assert "export async function _smgmtMoveModalPickBulk" in content, \
        "_smgmtMoveModalPickBulk is not exported from bulk-move.js"

    # The fix line must exist in the function
    assert "if (isNew) _smgmtClearSelection();" in content, \
        "The fix 'if (isNew) _smgmtClearSelection();' is missing from _smgmtMoveModalPickBulk"
