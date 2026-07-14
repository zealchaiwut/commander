"""Tests for issue #1907: Consolidate duplicate test files and verify naming convention.

Acceptance Criteria (derived from issue context):
1. test_1749__fix_sqlite_fd_leak.py and test_sqlite_fd_leak__1749.py are consolidated
   into a single file named test_sqlite_fd_leak__1749.py (following test_<feature>__<criterion>)
2. test_1898__merge_conflict_auto_resolve.py and test_merge_conflict_auto_resolve__1898.py
   are consolidated into a single file named test_merge_conflict_auto_resolve__1898.py
3. Behavioral (non-source-regex) assertions from both files are retained
4. Old duplicate files are deleted
5. All tests still pass after consolidation
"""
import os
import subprocess
from pathlib import Path


TESTS_DIR = Path(__file__).parent


def test_consolidate_test_files__1749_files_exist():
    """Verify: After consolidation, only test_sqlite_fd_leak__1749.py exists (not duplicate)."""
    # The consolidated file should exist
    consolidated = TESTS_DIR / "test_sqlite_fd_leak__1749.py"
    assert consolidated.exists(), f"Consolidated file {consolidated} does not exist"

    # The old coder-authored file should be deleted
    old_coder = TESTS_DIR / "test_1749__fix_sqlite_fd_leak.py"
    assert not old_coder.exists(), (
        f"Old coder-authored duplicate {old_coder} still exists. "
        "It should be consolidated into test_sqlite_fd_leak__1749.py."
    )


def test_consolidate_test_files__1898_files_exist():
    """Verify: After consolidation, only test_merge_conflict_auto_resolve__1898.py exists."""
    # The consolidated file should exist
    consolidated = TESTS_DIR / "test_merge_conflict_auto_resolve__1898.py"
    assert consolidated.exists(), f"Consolidated file {consolidated} does not exist"

    # The old coder-authored file should be deleted
    old_coder = TESTS_DIR / "test_1898__merge_conflict_auto_resolve.py"
    assert not old_coder.exists(), (
        f"Old coder-authored duplicate {old_coder} still exists. "
        "It should be consolidated into test_merge_conflict_auto_resolve__1898.py."
    )


def test_consolidate_test_files__1749_naming_convention():
    """Verify: test_sqlite_fd_leak__1749.py follows test_<feature>__<criterion> convention."""
    consolidated = TESTS_DIR / "test_sqlite_fd_leak__1749.py"
    assert consolidated.exists()

    # Name should follow pattern: test_<feature>__<issue>
    # Feature: sqlite_fd_leak (from the issue title)
    # Issue: 1749
    filename = consolidated.name
    assert filename.startswith("test_"), f"File {filename} should start with 'test_'"
    assert "__" in filename, f"File {filename} should use double underscore separator"
    assert filename.endswith("__1749.py"), f"File {filename} should end with '__1749.py'"


def test_consolidate_test_files__1898_naming_convention():
    """Verify: test_merge_conflict_auto_resolve__1898.py follows test_<feature>__<criterion> convention."""
    consolidated = TESTS_DIR / "test_merge_conflict_auto_resolve__1898.py"
    assert consolidated.exists()

    # Name should follow pattern: test_<feature>__<issue>
    # Feature: merge_conflict_auto_resolve
    # Issue: 1898
    filename = consolidated.name
    assert filename.startswith("test_"), f"File {filename} should start with 'test_'"
    assert "__" in filename, f"File {filename} should use double underscore separator"
    assert filename.endswith("__1898.py"), f"File {filename} should end with '__1898.py'"


def test_consolidate_test_files__1749_behavioral_assertions_retained():
    """Verify: Consolidated test_sqlite_fd_leak__1749.py retains behavioral assertions."""
    consolidated = TESTS_DIR / "test_sqlite_fd_leak__1749.py"
    content = consolidated.read_text()

    # From coder-authored AC tests:
    # - AC1: context manager closes connections
    # - AC2: existing call sites work
    # - AC3: no fd growth
    # - AC4: regression check on db helpers
    behavioral_markers = [
        "context manager",
        "close",
        "get_conn",
        "with",
    ]
    for marker in behavioral_markers:
        assert marker.lower() in content.lower(), (
            f"Behavioral assertion marker '{marker}' not found in consolidated file. "
            f"Coder-authored AC tests may not be retained."
        )

    # From tester-authored tests:
    # - Tests should use httpx or import db directly (UAT-style)
    assert ("httpx" in content or "from apps.dashboard import db" in content), (
        "Tester-authored UAT patterns not found. Tests may not be properly consolidated."
    )


def test_consolidate_test_files__1898_behavioral_assertions_retained():
    """Verify: Consolidated test_merge_conflict_auto_resolve__1898.py retains behavioral assertions."""
    consolidated = TESTS_DIR / "test_merge_conflict_auto_resolve__1898.py"
    content = consolidated.read_text()

    # From coder-authored AC tests:
    # - AC1: union merge behavior
    # - AC2: 409 structured response
    # - AC3: conflict-status endpoint
    # - AC4: loop-driver contract
    behavioral_markers = [
        "merge",
        "conflict",
        "409",
        "auto",
    ]
    for marker in behavioral_markers:
        assert marker.lower() in content.lower(), (
            f"Behavioral assertion marker '{marker}' not found in consolidated file. "
            f"Coder-authored AC tests may not be retained."
        )

    # From tester-authored tests (if present in consolidation):
    # Tests reference httpx client or pytest.skip for manual verification
    assert ("pytest.skip" in content or "httpx" in content), (
        "Tester-authored test patterns not found. UAT tests may not be properly included."
    )


def test_consolidate_test_files__no_syntax_errors():
    """Verify: Consolidated files have valid Python syntax."""
    for test_file in [
        TESTS_DIR / "test_sqlite_fd_leak__1749.py",
        TESTS_DIR / "test_merge_conflict_auto_resolve__1898.py",
    ]:
        assert test_file.exists(), f"Test file {test_file} does not exist"

        try:
            compile(test_file.read_text(), str(test_file), "exec")
        except SyntaxError as e:
            raise AssertionError(
                f"Consolidated file {test_file} has syntax errors: {e}"
            ) from e


def test_consolidate_test_files__all_old_files_deleted():
    """Verify: No old test_<issue>__<feature>.py files remain."""
    old_patterns = [
        "test_1749__*.py",
        "test_1898__*.py",
    ]

    for pattern in old_patterns:
        # Skip the consolidated files that match this pattern
        old_files = [
            f for f in TESTS_DIR.glob(pattern)
            if f.name not in [
                "test_sqlite_fd_leak__1749.py",
                "test_merge_conflict_auto_resolve__1898.py",
            ]
        ]
        assert not old_files, (
            f"Old test files matching {pattern} still exist: {old_files}. "
            "They should be consolidated."
        )
