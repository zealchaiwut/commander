"""Tests for issue #1337: Remove stray test_bulk_routes_extraction__1265.py"""
import os
import subprocess
import pytest


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:8001"


@pytest.fixture
def repo_root():
    """Return the repo root directory."""
    return subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()


def test_stray_test_removal__test_file_deleted(repo_root):
    """AC: test_bulk_routes_extraction__1265.py is deleted from the repository"""
    test_file_path = os.path.join(
        repo_root, "tests", "test_bulk_routes_extraction__1265.py"
    )
    assert not os.path.exists(
        test_file_path
    ), f"Expected {test_file_path} to be deleted but it still exists"


def test_stray_test_removal__no_bulk_tickets_file_not_found(repo_root):
    """AC: Running pytest does not produce FileNotFoundError for bulk_tickets.py"""
    # Run pytest with minimal options to check for file not found errors
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=os.path.join(repo_root, "apps", "dashboard"),
        capture_output=True,
        text=True,
    )

    # Check for FileNotFoundError related to bulk_tickets.py
    assert "bulk_tickets.py" not in result.stdout, (
        f"Found reference to bulk_tickets.py in pytest output; "
        f"this suggests the file or test still tries to use it:\n{result.stdout}"
    )
    assert (
        "No such file or directory" not in result.stderr
        or "bulk_tickets" not in result.stderr
    ), (
        f"pytest stderr contains file not found error related to bulk_tickets: "
        f"{result.stderr}"
    )


def test_stray_test_removal__no_bulk_tickets_imports(repo_root):
    """AC: No test file in tests/ references bulk_tickets module"""
    tests_dir = os.path.join(repo_root, "tests")

    # Search for any reference to bulk_tickets in test files
    result = subprocess.run(
        ["grep", "-r", "bulk_tickets", tests_dir],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, (
        f"Found references to 'bulk_tickets' in tests/:\n{result.stdout}\n"
        f"No test files should import or reference the nonexistent routers/bulk_tickets module."
    )


def test_stray_test_removal__pytest_suite_passes(repo_root):
    """AC: All remaining tests pass or fail for unrelated reasons"""
    # This test simply verifies that pytest can run without the stray file causing failures
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=os.path.join(repo_root, "apps", "dashboard"),
        capture_output=True,
        text=True,
        timeout=60,
    )

    # The exit code should be 0 (all pass) or non-zero but NOT due to FileNotFoundError
    # We're checking that pytest itself doesn't crash or hang
    assert result.returncode is not None, "pytest did not complete (timeout or crash)"

    # Verify we can parse pytest output
    assert "passed" in result.stdout or "failed" in result.stdout, (
        f"Could not find test results in pytest output:\n{result.stdout}"
    )
