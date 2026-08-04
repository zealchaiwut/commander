"""
Tests for issue #1994: Remove stray apps/dashboard/test.db-wal and gitignore db artifacts.

AC1: apps/dashboard/test.db-wal is NOT tracked by git (git ls-files returns nothing for it).
AC2: Root .gitignore contains entries for *.db-wal, *.db-shm, and test.db.
AC3: apps/dashboard/.gitignore contains entries for *.db-wal and *.db-shm.
"""

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def test_ac1_test_db_wal_not_tracked():
    """AC1: apps/dashboard/test.db-wal must not appear in git ls-files."""
    root = _repo_root()
    result = subprocess.run(
        ["git", "ls-files", "apps/dashboard/test.db-wal"],
        capture_output=True, text=True, check=True,
        cwd=root,
    )
    assert result.stdout.strip() == "", (
        "apps/dashboard/test.db-wal is still tracked by git; "
        "run `git rm --cached apps/dashboard/test.db-wal`"
    )


def test_ac2_root_gitignore_has_db_patterns():
    """AC2: Root .gitignore must cover *.db-wal, *.db-shm, and test.db (or *.db)."""
    root = _repo_root()
    gitignore = (root / ".gitignore").read_text()

    assert "*.db-wal" in gitignore, "Root .gitignore missing *.db-wal"
    assert "*.db-shm" in gitignore, "Root .gitignore missing *.db-shm"
    # *.db covers test.db; both are acceptable
    assert ("test.db" in gitignore or "*.db" in gitignore), (
        "Root .gitignore missing test.db or *.db"
    )


def test_ac3_dashboard_gitignore_has_db_wal_patterns():
    """AC3: apps/dashboard/.gitignore must cover *.db-wal and *.db-shm."""
    root = _repo_root()
    gi_path = root / "apps" / "dashboard" / ".gitignore"
    assert gi_path.exists(), "apps/dashboard/.gitignore does not exist"
    gitignore = gi_path.read_text()

    assert "*.db-wal" in gitignore, "apps/dashboard/.gitignore missing *.db-wal"
    assert "*.db-shm" in gitignore, "apps/dashboard/.gitignore missing *.db-shm"
