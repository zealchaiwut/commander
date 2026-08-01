"""Tests for issue #2054: resync_issues_mirror.py has no argparse (runs against UAT)"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _script_path() -> Path:
    """Resolve the path to resync_issues_mirror.py"""
    return Path(__file__).resolve().parent.parent / "scripts" / "resync_issues_mirror.py"


def _run_script(*args, **kwargs) -> tuple[int, str, str]:
    """Run the script with given args; return (exit_code, stdout, stderr)"""
    script = _script_path()
    cmd = [sys.executable, str(script)] + list(args)
    env = os.environ.copy()

    # Ensure a temp DB_PATH is set to avoid mutating the real database
    with tempfile.TemporaryDirectory() as tmpdir:
        env["DB_PATH"] = str(Path(tmpdir) / "test.db")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    return result.returncode, result.stdout, result.stderr


# --- Acceptance Criteria ---


def test_resync_issues_mirror__help_shows_usage():
    """AC 1: --help prints usage and exits WITHOUT touching GitHub or the database"""
    # --help invocation must not make GitHub calls or touch the database
    exit_code, stdout, stderr = _run_script("--help")

    # Must exit cleanly (0)
    assert exit_code == 0, f"--help should exit 0, got {exit_code}"

    # Usage text must appear
    assert "resync_issues_mirror" in stdout or "usage" in stdout, \
        f"--help output missing usage text: {stdout}"

    # The output should mention --yes/--force as the confirmation gate
    assert "--yes" in stdout or "--force" in stdout, \
        f"--help output should mention --yes or --force requirement: {stdout}"


def test_resync_issues_mirror__help_zero_github_calls():
    """AC 5: --help makes zero GitHub calls (no rate-limit consumption)"""
    # --help should exit instantly without making network calls.
    # Assert exit code is 0 and output is just usage text.
    exit_code, stdout, stderr = _run_script("--help")

    # --help exits 0 immediately
    assert exit_code == 0, f"--help should exit 0, got {exit_code}"

    # Output should be usage text only (short and direct)
    # If it were making GitHub calls, it would take much longer or error out
    assert "resync_issues_mirror" in stdout or "usage" in stdout, \
        f"--help output missing usage text: {stdout}"

    # No network errors or API failures mentioned
    assert "rate limit" not in stdout.lower(), \
        f"--help should not attempt GitHub calls: {stdout}"
    assert "connection" not in stderr.lower(), \
        f"--help should not attempt network calls: {stderr}"


def test_resync_issues_mirror__help_zero_db_writes():
    """AC 5: --help makes zero database writes (DB mtime unchanged)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test.db"

        # Create an empty db file and record its mtime
        db_file.touch()
        mtime_before = db_file.stat().st_mtime_ns

        # Run --help with this DB path
        env = os.environ.copy()
        env["DB_PATH"] = str(db_file)

        script = _script_path()
        cmd = [sys.executable, str(script), "--help"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

        # Check DB was not modified
        mtime_after = db_file.stat().st_mtime_ns
        assert mtime_before == mtime_after, \
            f"--help modified the database (mtime changed: {mtime_before} → {mtime_after})"


def test_resync_issues_mirror__no_args_dry_run():
    """AC 2: Without --yes, prints what it would do and exits 1 (safe dry-run)"""
    exit_code, stdout, stderr = _run_script()

    # Must exit non-zero (1) without --yes
    assert exit_code == 1, f"Without --yes should exit 1, got {exit_code}"

    # Must print dry-run summary (what repos would be resynced)
    assert "Would resync" in stdout or "would" in stdout.lower(), \
        f"Dry-run output missing 'Would resync': {stdout}"

    # Must mention --yes requirement
    assert "--yes" in stdout or "--force" in stdout, \
        f"Output should mention --yes requirement: {stdout}"


def test_resync_issues_mirror__no_args_zero_github_calls():
    """AC 5: Without --yes, makes zero GitHub calls (safe to probe)"""
    # Dry-run (no --yes) should print summary and exit 1 without network calls
    exit_code, stdout, stderr = _run_script()

    # Must exit 1 without performing the resync
    assert exit_code == 1, f"Dry-run should exit 1, got {exit_code}"

    # Should print summary of what *would* happen, not attempting GitHub
    assert "Would resync" in stdout or "would" in stdout.lower(), \
        f"Dry-run should show summary, got: {stdout}"

    # No network errors or API failures mentioned
    assert "error" not in stderr.lower(), \
        f"Dry-run should not attempt network calls: {stderr}"


def test_resync_issues_mirror__no_args_zero_db_writes():
    """AC 5: Without --yes, makes zero database writes"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test.db"
        db_file.touch()
        mtime_before = db_file.stat().st_mtime_ns

        env = os.environ.copy()
        env["DB_PATH"] = str(db_file)

        script = _script_path()
        cmd = [sys.executable, str(script)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

        # DB should not be modified
        mtime_after = db_file.stat().st_mtime_ns
        assert mtime_before == mtime_after, \
            f"Dry-run modified the database (mtime changed)"


def test_resync_issues_mirror__repo_flag_filters():
    """AC 3: --repo OWNER/REPO scopes resync to a single repository"""
    exit_code, stdout, stderr = _run_script("--repo", "test-owner/test-repo")

    # Dry-run (no --yes) should print the scoped repo
    assert "test-owner/test-repo" in stdout, \
        f"--repo flag should appear in output: {stdout}"

    # Should indicate 1 repo instead of multiple
    assert "1 repo" in stdout, \
        f"With --repo, output should indicate 1 repo: {stdout}"


def test_resync_issues_mirror__summary_output():
    """AC 4: Output includes summary (repos touched, rows changed) with clear header"""
    exit_code, stdout, stderr = _run_script()

    # Dry-run output should have clear summary section
    assert "Would resync" in stdout, \
        f"Summary missing 'Would resync': {stdout}"

    # No confusing per-repo label-count dicts — should be a simple list
    # (The new output shows "Would resync N repo(s):" then lists them)
    lines = stdout.strip().split("\n")
    assert any("Would resync" in line for line in lines), \
        f"Summary line missing: {stdout}"
