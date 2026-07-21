"""
Tests for issue #1922 — Delete duplicate test file for #1825.

AC1: tests/test_remove_venv_symlink__1825.py is deleted.
AC4: No test in the suite references test_remove_venv_symlink__1825.py as an import or dependency.
"""
import pathlib
import subprocess


REPO_ROOT = pathlib.Path(__file__).parent.parent
DUPLICATE_FILE = REPO_ROOT / "tests" / "test_remove_venv_symlink__1825.py"


def test_duplicate_file_deleted():
    """AC1: The duplicate test file must not exist in the repository."""
    assert not DUPLICATE_FILE.exists(), (
        "tests/test_remove_venv_symlink__1825.py still exists; delete it."
    )


def test_no_references_to_deleted_file():
    """AC4: No other test file imports or references the deleted file by name."""
    result = subprocess.run(
        [
            "grep", "-r", "--include=*.py",
            "--exclude=test_1922__cleanup_duplicate_1825.py",
            "test_remove_venv_symlink__1825",
            "tests/",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", (
        f"Found references to deleted file in other test files:\n{result.stdout}"
    )
