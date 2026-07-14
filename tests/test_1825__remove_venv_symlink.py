"""
Tests for issue #1825 — Remove committed machine-specific venv symlink from repo root.

AC1: The `venv` symlink is no longer tracked by git (git ls-files venv returns empty).
AC2: .gitignore contains a pattern that prevents re-committing venv (symlink or dir).
"""
import subprocess
import pathlib


REPO_ROOT = pathlib.Path(__file__).parent.parent


def test_venv_not_tracked_by_git():
    """AC1: git ls-files venv returns empty — venv is not in the index."""
    result = subprocess.run(
        ["git", "ls-files", "venv"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "", (
        "venv symlink is still tracked by git; run `git rm venv` to remove it"
    )


def test_gitignore_covers_venv_symlink():
    """AC2: .gitignore must have a bare 'venv' entry (no trailing slash) so
    both symlinks and directories named venv are ignored."""
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.exists(), ".gitignore not found"
    lines = gitignore.read_text().splitlines()
    # Bare 'venv' (without trailing slash) matches files, symlinks, AND dirs.
    # 'venv/' only matches directories — insufficient for symlinks.
    bare_entries = [l.strip() for l in lines if l.strip() in ("venv", "/venv")]
    assert bare_entries, (
        ".gitignore must include 'venv' (without trailing slash) to prevent "
        "re-committing a venv symlink. Currently only 'venv/' may be present, "
        "which does not match symlinks."
    )


def test_gitignore_covers_runtime_venv_cache():
    """AC2 (runtime path): .commander/runtime/ entry covers the venv-cache path."""
    gitignore = REPO_ROOT / ".gitignore"
    lines = gitignore.read_text().splitlines()
    stripped = [l.strip() for l in lines]
    # The runtime venv-cache lives under .commander/runtime/; that dir is already
    # expected to be in .gitignore per project conventions.
    assert ".commander/runtime/" in stripped, (
        ".gitignore should contain '.commander/runtime/' to cover the venv-cache path"
    )
