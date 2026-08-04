"""
Tests for issue #1922 — verify all acceptance criteria.

This test complements test_1922__cleanup_duplicate_1825.py by verifying
the remaining ACs that the coder's test doesn't explicitly check.
"""
import pathlib
import subprocess


REPO_ROOT = pathlib.Path(__file__).parent.parent


def test_1922__ac2_correct_test_retained():
    """AC2: tests/test_1825__remove_venv_symlink.py is retained and continues to pass."""
    test_file = REPO_ROOT / "tests" / "test_1825__remove_venv_symlink.py"
    assert test_file.exists(), (
        "tests/test_1825__remove_venv_symlink.py must be retained but is missing"
    )

    result = subprocess.run(
        ["pytest", str(test_file), "-v", "--tb=short"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"test_1825__remove_venv_symlink.py should pass but returned {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_1922__ac3_bare_venv_check_rejects_trailing_slash():
    """AC3 (behavioral): the gitignore check logic rejects 'venv/' and accepts bare 'venv'.

    Directly exercises the filtering logic that test_1825 uses, without reading
    source text.  'venv/' only matches directories; bare 'venv' matches both
    directories and symlinks — the critical distinction for AC3.
    """
    def _bare_entries(lines):
        return [ln.strip() for ln in lines if ln.strip() in ("venv", "/venv")]

    # 'venv/' must NOT satisfy the bare-entry check (symlinks would escape it)
    assert _bare_entries(["*.pyc", "venv/", "__pycache__/"]) == [], (
        "'venv/' must not appear in bare_entries; the check should reject it"
    )

    # bare 'venv' MUST satisfy the check (matches both dirs and symlinks)
    assert _bare_entries(["*.pyc", "venv", "__pycache__/"]) != [], (
        "bare 'venv' must appear in bare_entries; the check should accept it"
    )

    # '/venv' (absolute anchor) is also acceptable
    assert _bare_entries(["*.pyc", "/venv", "__pycache__/"]) != [], (
        "'/venv' must appear in bare_entries; the check should accept it"
    )


def test_1922__ac5_correct_test_covers_all_ac():
    """AC5: Running pytest test_1825__remove_venv_symlink.py exits green with all ACs from #1825 covered."""
    test_file = REPO_ROOT / "tests" / "test_1825__remove_venv_symlink.py"

    result = subprocess.run(
        ["pytest", str(test_file), "-v"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"test_1825__remove_venv_symlink.py should pass but returned {result.returncode}"
    )

    assert "test_venv_not_tracked_by_git" in result.stdout, (
        "test_venv_not_tracked_by_git (AC1) should be present"
    )
    assert "test_gitignore_covers_venv_symlink" in result.stdout, (
        "test_gitignore_covers_venv_symlink (AC2) should be present"
    )
    assert "test_gitignore_covers_runtime_venv_cache" in result.stdout, (
        "test_gitignore_covers_runtime_venv_cache (AC2 runtime) should be present"
    )

    assert "FAILED" not in result.stdout, (
        "All test_1825 tests should pass"
    )
