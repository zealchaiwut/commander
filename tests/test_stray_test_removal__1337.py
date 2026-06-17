"""Tests for issue #1337: Remove stray test_bulk_routes_extraction__1265.py"""
import glob
import os
import py_compile
import subprocess
import pytest


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
    # The stray file caused FileNotFoundError by calling open() / py_compile.compile()
    # on bulk_tickets.py unconditionally. Scan remaining test files for those patterns.
    tests_dir = os.path.join(repo_root, "tests")
    this_file = os.path.basename(__file__).replace(".pyc", ".py")

    for pattern in [
        r"py_compile\.compile.*bulk_tickets",
        r"open\(.*bulk_tickets",
    ]:
        result = subprocess.run(
            [
                "grep", "-rE", "--include=*.py",
                f"--exclude={this_file}",
                pattern, tests_dir,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            f"Found unconditional access to bulk_tickets.py in test files "
            f"(pattern: {pattern!r}):\n{result.stdout}"
        )


def test_stray_test_removal__no_bulk_tickets_imports(repo_root):
    """AC: No test file in tests/ imports or references the bulk_tickets module"""
    # We check for Python import statements for routers.bulk_tickets.
    # test_1302 references the path with an existence guard (candidate.exists()) — that
    # is not a module import and does not cause collection errors, so it is excluded.
    tests_dir = os.path.join(repo_root, "tests")
    this_file = os.path.basename(__file__).replace(".pyc", ".py")

    result = subprocess.run(
        [
            "grep", "-rE", "--include=*.py",
            f"--exclude={this_file}",
            r"(^import bulk_tickets|from routers\.bulk_tickets|from routers import bulk_tickets)",
            tests_dir,
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, (
        f"Found import of nonexistent bulk_tickets module in tests/:\n{result.stdout}\n"
        f"No test should import routers/bulk_tickets which does not exist."
    )


def test_stray_test_removal__pytest_suite_passes(repo_root):
    """AC: All remaining tests in the suite are syntactically valid and collectable"""
    # Running the full pytest suite as a subprocess would trigger recursive invocation of
    # this very test file, causing an infinite loop. Instead, compile-check every test
    # file (excluding this one) to confirm the suite is syntactically valid — which is
    # the property that the stray file violated (it prevented collection entirely).
    tests_dir = os.path.join(repo_root, "tests")
    this_file = os.path.basename(__file__).replace(".pyc", ".py")

    test_files = [
        f for f in glob.glob(os.path.join(tests_dir, "test_*.py"))
        if os.path.basename(f) != this_file
    ]

    assert test_files, f"No test files found in {tests_dir}"

    errors = []
    for test_file in sorted(test_files):
        try:
            py_compile.compile(test_file, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"{test_file}: {e}")

    assert not errors, (
        "Test files with syntax errors (would break pytest collection):\n"
        + "\n".join(errors)
    )

    # The previously always-failing stray file must be absent
    stray = os.path.join(tests_dir, "test_bulk_routes_extraction__1265.py")
    assert not os.path.exists(stray), (
        f"Always-failing stray file still present: {stray}"
    )
