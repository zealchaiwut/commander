"""Tests for issue #1825: Remove committed machine-specific venv symlink from repo root (runs against UAT)"""
import os
import subprocess
import pathlib
import pytest


# UAT resolution — these are set by the tester skill Step 0.
# Default fallback only if not already exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


def get_repo_root():
    """Get the tester repo root (main dev clone, not UAT)."""
    return pathlib.Path(__file__).parent.parent


def run_git_command(cmd_list, cwd=None):
    """Run a git command and return stdout."""
    if cwd is None:
        cwd = get_repo_root()
    result = subprocess.run(cmd_list, cwd=cwd, capture_output=True, text=True, check=False)
    return result.stdout, result.stderr, result.returncode


def test_remove_venv_symlink__venv_symlink_removed():
    """AC: The venv symlink should be removed from the repository root."""
    repo_root = get_repo_root()
    venv_path = repo_root / "venv"

    # After the fix, the symlink should not exist in the working tree
    assert not venv_path.is_symlink(), "venv symlink should be removed from working tree"
    assert not venv_path.exists(), "venv path should not exist"


def test_remove_venv_symlink__symlink_removed_from_git_index():
    """AC: The venv symlink should no longer be tracked by git."""
    repo_root = get_repo_root()

    # Check that 'venv' is not in git's tracked files
    stdout, stderr, rc = run_git_command(["git", "ls-files"], cwd=repo_root)
    tracked_files = stdout.strip().split("\n")

    assert "venv" not in tracked_files, "venv should not be in git's tracked files"


def test_remove_venv_symlink__gitignore_includes_venv():
    """AC: .gitignore should include venv/ to prevent re-commitment."""
    repo_root = get_repo_root()
    gitignore_path = repo_root / ".gitignore"

    assert gitignore_path.exists(), ".gitignore should exist"

    with open(gitignore_path) as f:
        gitignore_content = f.read()

    # Check that venv patterns are present
    assert "venv/" in gitignore_content, ".gitignore should include 'venv/' pattern"


def test_remove_venv_symlink__runtime_cache_protected():
    """AC: .gitignore should protect the .commander/runtime/ venv-cache path."""
    repo_root = get_repo_root()
    gitignore_path = repo_root / ".gitignore"

    with open(gitignore_path) as f:
        gitignore_content = f.read()

    # .commander/runtime/ should be ignored (contains venv-cache)
    assert ".commander/runtime/" in gitignore_content, ".gitignore should include '.commander/runtime/' pattern"
