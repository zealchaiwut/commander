"""
Comprehensive tests for issue #1922 — verify all acceptance criteria.

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

    # Run the test to verify it passes
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


def test_1922__ac3_gitignore_assertion_uses_bare_venv():
    """AC3: The .gitignore assertion in the surviving test uses bare 'venv' (not 'venv/')."""
    test_file = REPO_ROOT / "tests" / "test_1825__remove_venv_symlink.py"
    content = test_file.read_text()

    # Verify the test correctly asserts for bare 'venv' pattern
    # The fix should look for bare entries without trailing slash
    assert "bare_entries = [ln.strip() for ln in lines if ln.strip() in (\"venv\", \"/venv\")]" in content, (
        "test_gitignore_covers_venv_symlink should check for bare 'venv' entries "
        "that match both symlinks and directories"
    )

    # Verify the incorrect 'venv/' substring check is not the main assertion
    lines = content.split('\n')
    in_gitignore_func = False
    for i, line in enumerate(lines):
        if 'def test_gitignore_covers_venv_symlink' in line:
            in_gitignore_func = True
        if in_gitignore_func and 'def test_' in line and i > 0:
            in_gitignore_func = False
        if in_gitignore_func and 'assert "venv/" in gitignore_content' in line:
            # This should not appear as the main assertion
            assert False, (
                f"Line {i+1}: incorrect assertion 'assert \"venv/\" in gitignore_content'. "
                "Should check for bare 'venv' pattern that matches symlinks too."
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

    # Verify all three test functions from #1825 are present and passed
    assert "test_venv_not_tracked_by_git" in result.stdout, (
        "test_venv_not_tracked_by_git (AC1) should be present"
    )
    assert "test_gitignore_covers_venv_symlink" in result.stdout, (
        "test_gitignore_covers_venv_symlink (AC2) should be present"
    )
    assert "test_gitignore_covers_runtime_venv_cache" in result.stdout, (
        "test_gitignore_covers_runtime_venv_cache (AC2 runtime) should be present"
    )

    # All should have passed (no FAILED in output)
    assert "FAILED" not in result.stdout, (
        "All test_1825 tests should pass"
    )
