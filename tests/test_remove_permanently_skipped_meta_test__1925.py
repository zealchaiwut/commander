"""Tests for issue #1925: permanently-skipped meta-test removed from pytest output.

Behavioral tests: verify that test_bulk_move_new_sprint__node_tests_pass is no longer
collected by pytest (deselected via conftest.py hook) and that the two real behavioral
tests in the 1760 suite are still present in the collection.

AC1: test_bulk_move_new_sprint__node_tests_pass must not appear in pytest collection
AC2: the two HTTP integration tests remain in the collection
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_TARGET_FILE = "tests/test_bulk_move_new_sprint_clear_selection__1760.py"


def _collect_output() -> str:
    """Run pytest --collect-only on the 1760 suite and return stdout."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", _TARGET_FILE, "--collect-only", "-q", "--no-header"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    return result.stdout


def test_meta_test_not_collected():
    """AC1: the permanently-skipped meta-test must not appear in pytest collection."""
    assert "test_bulk_move_new_sprint__node_tests_pass" not in _collect_output()


def test_remaining_behavioral_tests_collected():
    """AC2: the two HTTP integration tests are still collected by pytest."""
    out = _collect_output()
    assert "test_bulk_move_new_sprint__dashboard_loads" in out
    assert "test_bulk_move_new_sprint__bundle_includes_fix" in out
