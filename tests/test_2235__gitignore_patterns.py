"""
Tests for AC #2235: .gitignore patterns widened to catch dated/rotated file variants.

AC1 — patterns widened to *.db*, *.log.*, *.bak-*, *.corrupt-*
AC3 — apps/dashboard/commander.db.corrupt-20260731* is ignored (not committed)
AC5 — git check-ignore treats the stale file types as ignored
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _is_ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return result.returncode == 0


# AC1: *.db* covers dated bak and corrupt variants

def test_db_bak_dated_ignored():
    """commander.db.bak-<date>-<time> is ignored (was untracked before *.db* fix)."""
    assert _is_ignored("commander.db.bak-20260621-182552"), (
        "commander.db.bak-* must be ignored — add *.db* to .gitignore"
    )


def test_db_corrupt_dated_ignored():
    """commander.db.corrupt-<date> is ignored (*.db* or *.corrupt-* pattern)."""
    assert _is_ignored("commander.db.corrupt-20260731"), (
        "commander.db.corrupt-* must be ignored — add *.db* or *.corrupt-* to .gitignore"
    )


def test_dashboard_db_corrupt_ignored():
    """apps/dashboard/commander.db.corrupt-<date> is ignored."""
    assert _is_ignored("apps/dashboard/commander.db.corrupt-20260731"), (
        "apps/dashboard/commander.db.corrupt-* must be ignored by .gitignore"
    )


# AC1: *.log.* covers rotated log files like prd.log.1

def test_rotated_log_n_ignored():
    """prd.log.1 (and .2-.5) are ignored (*.log.* pattern)."""
    for n in range(1, 6):
        assert _is_ignored(f"apps/dashboard/prd.log.{n}"), (
            f"apps/dashboard/prd.log.{n} must be ignored — add *.log.* to .gitignore"
        )


# AC1: *.bak-* covers non-db bak files with date suffix

def test_json_bak_dated_ignored():
    """projects.json.bak-<date> is ignored (*.bak-* pattern)."""
    assert _is_ignored("apps/dashboard/projects.json.bak-2026-07-17"), (
        "*.bak-<date> files must be ignored — add *.bak-* to .gitignore"
    )


# AC3: the preserved post-mortem artifact is ignored (not tracked / not untracked-clutter)

def test_corrupt_artifact_is_gitignored():
    """The #2037 artifact commander.db.corrupt-20260731 is gitignored, not committed."""
    assert _is_ignored("apps/dashboard/commander.db.corrupt-20260731"), (
        "commander.db.corrupt-20260731 must be gitignored so it stays on disk without "
        "showing as untracked — it must NOT be deleted or committed"
    )
