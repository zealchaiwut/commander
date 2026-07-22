"""Tests for issue #1965: Remove committed SQLite WAL/shm sidecar files and gitignore them"""
import subprocess
import os


def run_git_command(cmd):
    """Helper to run git commands and return output + exit code"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=os.path.dirname(__file__) + "/..",
            capture_output=True,
            text=True,
        )
        return result.stdout, result.returncode
    except Exception as e:
        return str(e), 1


def test_remove_sqlite_sidecar__wal_shm_untracked():
    """AC: db-wal and db-shm files are removed from git tracking"""
    output, exit_code = run_git_command(
        "git ls-files apps/dashboard/dashboard.db-wal apps/dashboard/dashboard.db-shm"
    )
    assert exit_code == 0, f"git ls-files failed: {output}"
    assert output.strip() == "", f"Expected no tracked files, but got: {output}"


def test_remove_sqlite_sidecar__gitignore_entries():
    """AC: .gitignore contains entries for db-wal, db-shm, and db-journal"""
    repo_root = os.path.dirname(__file__) + "/.."

    # Check root .gitignore
    with open(os.path.join(repo_root, ".gitignore"), "r") as f:
        root_gitignore = f.read()

    assert "*.db-wal" in root_gitignore, "*.db-wal not in root .gitignore"
    assert "*.db-shm" in root_gitignore, "*.db-shm not in root .gitignore"
    assert "*.db-journal" in root_gitignore, "*.db-journal not in root .gitignore"

    # Check apps/dashboard/.gitignore
    with open(os.path.join(repo_root, "apps/dashboard/.gitignore"), "r") as f:
        dashboard_gitignore = f.read()

    assert "*.db-wal" in dashboard_gitignore, "*.db-wal not in apps/dashboard/.gitignore"
    assert "*.db-shm" in dashboard_gitignore, "*.db-shm not in apps/dashboard/.gitignore"
    assert (
        "*.db-journal" in dashboard_gitignore
    ), "*.db-journal not in apps/dashboard/.gitignore"


def test_remove_sqlite_sidecar__no_other_tracked_sidecars():
    """AC: No other .db-* sidecar files are tracked in the repository"""
    repo_root = os.path.dirname(__file__) + "/.."
    result = subprocess.run(
        "git ls-files",
        shell=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"git ls-files failed: {result.stderr}"

    # Check if any .db-* files are in the tracked list
    for line in result.stdout.strip().split("\n"):
        assert ".db-" not in line, f"Found tracked .db-* file: {line}"


def test_remove_sqlite_sidecar__git_add_no_restageing():
    """AC: A subsequent git add -A does not re-stage *.db-wal, *.db-shm, or *.db-journal files"""
    repo_root = os.path.dirname(__file__) + "/.."

    # Run git add -A
    result = subprocess.run(
        "git add -A",
        shell=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"git add -A failed: {result.stderr}"

    # Check git status for WAL/shm/journal files
    output, exit_code = run_git_command("git status --short")
    assert exit_code == 0, f"git status failed: {output}"

    # Verify no WAL/shm/journal files in staged changes
    lines = output.strip().split("\n") if output.strip() else []
    for line in lines:
        assert (
            "db-wal" not in line
        ), f"*.db-wal file appears in staged changes: {line}"
        assert (
            "db-shm" not in line
        ), f"*.db-shm file appears in staged changes: {line}"
        assert (
            "db-journal" not in line
        ), f"*.db-journal file appears in staged changes: {line}"
