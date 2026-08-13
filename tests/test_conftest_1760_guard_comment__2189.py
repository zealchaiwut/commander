"""Tests for issue #2189: Strengthen #1760 conftest inline comment assertion.

The original test (`test_1760_guard_has_inline_comment`) in test_2018__conftest_1760_live_server_guard.py
only checked for presence of '#' character, violating CLAUDE.md #1746 (AC tests must exercise behavior,
not source text patterns). This test suite verifies that the improved assertion validates meaningful
comment content instead of just checking for a '#' character.

Acceptance Criteria:
1. AC1: The 1760 entry in conftest.py contains an inline comment
2. AC2: The inline comment contains substantive rationale keywords ('self-skip', 'BASE_URL', or 'ConnectError')
3. AC3: The test_1760_guard_has_inline_comment function properly enforces this stricter assertion
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import httpx

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

REPO_ROOT = Path(__file__).parent.parent
CONFTEST_FILE = REPO_ROOT / "conftest.py"
TEST_FILE = "tests/test_2018__conftest_1760_live_server_guard.py"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria Tests ---

def test_conftest_1760_entry_has_inline_comment():
    """AC1: The 1760 entry in conftest.py _LIVE_SERVER_TEST_MODULES contains an inline comment."""
    source = CONFTEST_FILE.read_text()
    found = False
    for line in source.splitlines():
        if "test_bulk_move_new_sprint_clear_selection__1760" in line and "__1760" in line:
            if "_PERMANENTLY_DESELECTED" in line:
                continue  # skip the deselected-nodeids entry; only check the modules set
            assert "#" in line, (
                "The 1760 entry in _LIVE_SERVER_TEST_MODULES must have an inline comment. "
                f"Got: {line!r}"
            )
            found = True
            break
    assert found, "1760 entry not found in conftest.py _LIVE_SERVER_TEST_MODULES"


def test_conftest_1760_comment_contains_substantive_rationale():
    """AC2: The inline comment contains substantive keywords explaining why the module cannot self-skip."""
    source = CONFTEST_FILE.read_text()
    for line in source.splitlines():
        if "test_bulk_move_new_sprint_clear_selection__1760" in line and "__1760" in line:
            if "_PERMANENTLY_DESELECTED" in line:
                continue  # skip the deselected-nodeids entry; only check the modules set
            assert "#" in line, "1760 entry must have an inline comment"
            comment = line[line.index("#"):]
            # Check for substantive keywords that explain the rationale
            has_rationale = any(kw in comment for kw in ("self-skip", "BASE_URL", "ConnectError"))
            assert has_rationale, (
                "The inline comment must contain substantive rationale (expected 'self-skip', 'BASE_URL', "
                f"or 'ConnectError'). Got: {comment!r}"
            )
            return
    raise AssertionError("1760 entry not found in conftest.py _LIVE_SERVER_TEST_MODULES")


def test_improved_1760_guard_assertion_passes():
    """AC3: The improved test_1760_guard_has_inline_comment function (with stricter assertion) passes."""
    # Run the actual test from test_2018__conftest_1760_live_server_guard.py
    result = subprocess.run(
        [sys.executable, "-m", "pytest", f"{TEST_FILE}::test_1760_guard_has_inline_comment", "-v"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"The improved test_1760_guard_has_inline_comment must pass:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
