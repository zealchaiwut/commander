"""Tests for issue #1966: [follow-up] Remove stray unrelated test files and harden lint auto-fix.

Acceptance Criteria:
1. Stray test files from #1959 (#1944 and #1946) are removed from the diff
2. gates._lint_autofix_commit uses git diff --name-only (not git add -A) for staging
3. Only tracked modified files are staged; untracked files are never swept in
"""
import os
import sys
from pathlib import Path

# Verify the stray test files are removed
def test_stray_test_files_removed():
    """AC1: test_1944__coder_no_test_edits_uat.py and test_trigger_owner_overnight_mode__1946.py are removed."""
    repo_root = Path(__file__).parent.parent
    tests_dir = repo_root / "tests"

    stray_files = [
        tests_dir / "test_1944__coder_no_test_edits_uat.py",
        tests_dir / "test_trigger_owner_overnight_mode__1946.py",
    ]

    for f in stray_files:
        assert not f.exists(), f"Stray file {f.name} should be removed but still exists"


# Verify gates.py uses git diff --name-only (not git add -A)
def test_gates_lint_autofix_uses_tracked_files_only():
    """AC2: gates._lint_autofix_commit stages only tracked files via git diff --name-only."""
    repo_root = Path(__file__).parent.parent
    gates_file = repo_root / "services" / "sprint_manager" / "gates.py"

    content = gates_file.read_text()

    # Verify git diff --name-only is used
    assert 'git diff --name-only' in content, \
        "gates.py must use 'git diff --name-only' to get only tracked modified files"

    # Verify git add -A is NOT used in the lint autofix context
    # Note: we allow git add -A elsewhere, but within _lint_autofix_commit it should not appear
    lines = content.split('\n')
    in_lint_autofix = False
    found_git_add_A = False

    for i, line in enumerate(lines):
        if 'def _lint_autofix_commit' in line:
            in_lint_autofix = True
        elif in_lint_autofix and line.strip().startswith('def '):
            # Next function definition — end of _lint_autofix_commit
            in_lint_autofix = False

        if in_lint_autofix and 'git add -A' in line:
            found_git_add_A = True
            break

    assert not found_git_add_A, \
        "_lint_autofix_commit must not call 'git add -A' — it should use git diff --name-only"


# Verify new test file exists and covers the hardened behavior
def test_lint_autofix_test_file_exists():
    """AC3: New test file test_1966__lint_autofix_no_git_add_A.py exists and is well-formed."""
    repo_root = Path(__file__).parent.parent
    test_file = repo_root / "tests" / "test_1966__lint_autofix_no_git_add_A.py"

    assert test_file.exists(), \
        "test_1966__lint_autofix_no_git_add_A.py should exist"

    content = test_file.read_text()

    # Verify it tests git add -A is never called
    assert 'git add -A' in content, \
        "Test file should verify that git add -A is never called"

    # Verify it tests only tracked files are staged
    assert 'tracked' in content.lower(), \
        "Test file should verify only tracked files are staged"

    # Verify it has multiple test functions
    test_count = content.count('def test_')
    assert test_count >= 3, \
        f"Test file should have multiple test functions (expected >=3, got {test_count})"
