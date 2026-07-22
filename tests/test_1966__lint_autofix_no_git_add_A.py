"""Tests for issue #1966 — harden lint auto-fix staging to avoid git add -A.

AC: _lint_autofix_commit must not call `git add -A`; it must stage only the
tracked files actually modified by ruff/prettier (via `git diff --name-only`).
Untracked files present in the worktree must never be swept into the commit.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

import gates  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_run_timed(
    *,
    tracked_modified: str = "apps/server.py\n",
    porcelain_status: str = " M apps/server.py\n?? tests/untracked.py\n",
    branch: str = "feature/1966-test",
    base_branch: str = "develop",
    git_root: str = "/fake/root",
    commit_rc: int = 0,
    push_rc: int = 0,
):
    """Return a fake _run_timed that simulates git/ruff commands.

    Records every (git add) call so tests can assert what was staged.
    """
    add_calls: list[tuple] = []

    def fake_run_timed(*args, cwd=None):
        cmd = tuple(args)

        # git rev-parse --show-toplevel
        if cmd[:3] == ("git", "rev-parse", "--show-toplevel"):
            return (0, git_root, "")

        # _changed_py_files: git diff <base> --name-only --diff-filter=ACM
        if len(cmd) >= 4 and cmd[0] == "git" and cmd[1] == "diff" and "--diff-filter=ACM" in cmd:
            return (0, "apps/server.py\n", "")

        # ruff check --fix (any ruff invocation)
        if "ruff" in cmd[0]:
            return (0, "", "")

        # git status --porcelain
        if cmd == ("git", "status", "--porcelain"):
            return (0, porcelain_status, "")

        # git rev-parse --abbrev-ref HEAD
        if cmd == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            return (0, branch, "")

        # git diff --name-only  (only tracked modified files)
        if cmd == ("git", "diff", "--name-only"):
            return (0, tracked_modified, "")

        # git add <anything>
        if cmd[:2] == ("git", "add"):
            add_calls.append(cmd)
            return (0, "", "")

        # git commit
        if cmd[:2] == ("git", "commit"):
            return (commit_rc, "", "")

        # git push
        if cmd == ("git", "push", "origin", "HEAD"):
            return (push_rc, "", "")

        # git reset --soft (push rollback path)
        if cmd[:3] == ("git", "reset", "--soft"):
            return (0, "", "")

        return (0, "", "")

    return fake_run_timed, add_calls


# ── AC1: git add -A is never called ───────────────────────────────────────────

def test_git_add_A_never_called(tmp_path):
    """AC1: _lint_autofix_commit must not call `git add -A` even when untracked
    files are present in the worktree alongside lint-modified tracked files."""
    fake, add_calls = _make_run_timed()

    with patch.object(gates, "_run_timed", side_effect=fake), \
         patch.object(gates, "_try", return_value=(True, "/usr/bin/ruff", "")):
        gates._lint_autofix_commit(
            issue_num=1966,
            worktester_dashboard=tmp_path,
            base_branch="develop",
            gate_scope="changed",
            gate_frontend_lint=False,
        )

    bad = [c for c in add_calls if "-A" in c]
    assert not bad, (
        f"`git add -A` was called — untracked files would be swept in. "
        f"All git add calls: {add_calls}"
    )


# ── AC2: only tracked lint-modified files are staged ──────────────────────────

def test_only_tracked_modified_files_staged(tmp_path):
    """AC2: when ruff modifies apps/server.py and tests/untracked.py is present
    but untracked, only apps/server.py must be staged."""
    fake, add_calls = _make_run_timed(
        tracked_modified="apps/server.py\n",
        porcelain_status=" M apps/server.py\n?? tests/untracked.py\n",
    )

    with patch.object(gates, "_run_timed", side_effect=fake), \
         patch.object(gates, "_try", return_value=(True, "/usr/bin/ruff", "")):
        gates._lint_autofix_commit(
            issue_num=1966,
            worktester_dashboard=tmp_path,
            base_branch="develop",
            gate_scope="changed",
            gate_frontend_lint=False,
        )

    staged = [arg for call in add_calls for arg in call[2:]]  # args after "git add"
    assert "apps/server.py" in staged, (
        f"Expected apps/server.py to be staged. git add calls: {add_calls}"
    )
    assert "tests/untracked.py" not in staged, (
        f"Untracked file tests/untracked.py must not be staged. "
        f"git add calls: {add_calls}"
    )


# ── AC3: no commit when only untracked files are present ──────────────────────

def test_no_commit_when_only_untracked_files_present(tmp_path):
    """AC3: if ruff makes no tracked changes but untracked files exist,
    no git commit should be made (return early after git diff --name-only)."""
    commits: list[tuple] = []

    def fake_run_timed(*args, cwd=None):
        cmd = tuple(args)
        if cmd[:3] == ("git", "rev-parse", "--show-toplevel"):
            return (0, str(tmp_path), "")
        if "--diff-filter=ACM" in cmd:
            return (0, "apps/server.py\n", "")
        if "ruff" in cmd[0]:
            return (0, "", "")
        if cmd == ("git", "status", "--porcelain"):
            # untracked file only — ruff made no changes
            return (0, "?? tests/stray.py\n", "")
        if cmd == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            return (0, "feature/1966-test", "")
        if cmd == ("git", "diff", "--name-only"):
            return (0, "", "")  # no tracked files modified
        if cmd[:2] == ("git", "commit"):
            commits.append(cmd)
            return (0, "", "")
        return (0, "", "")

    with patch.object(gates, "_run_timed", side_effect=fake_run_timed), \
         patch.object(gates, "_try", return_value=(True, "/usr/bin/ruff", "")):
        gates._lint_autofix_commit(
            issue_num=1966,
            worktester_dashboard=tmp_path,
            base_branch="develop",
            gate_scope="changed",
            gate_frontend_lint=False,
        )

    assert not commits, (
        "No commit should be made when only untracked files are present. "
        f"Got commits: {commits}"
    )


# ── AC4: multiple tracked modified files are all staged ───────────────────────

def test_multiple_tracked_files_all_staged(tmp_path):
    """AC4: when ruff modifies multiple tracked files, all of them are staged."""
    fake, add_calls = _make_run_timed(
        tracked_modified="apps/server.py\napps/dashboard/routers/auth.py\n",
        porcelain_status=(
            " M apps/server.py\n"
            " M apps/dashboard/routers/auth.py\n"
            "?? tests/untracked.py\n"
        ),
    )

    with patch.object(gates, "_run_timed", side_effect=fake), \
         patch.object(gates, "_try", return_value=(True, "/usr/bin/ruff", "")):
        gates._lint_autofix_commit(
            issue_num=1966,
            worktester_dashboard=tmp_path,
            base_branch="develop",
            gate_scope="changed",
            gate_frontend_lint=False,
        )

    staged = [arg for call in add_calls for arg in call[2:]]
    assert "apps/server.py" in staged, \
        f"apps/server.py missing from staged files. git add calls: {add_calls}"
    assert "apps/dashboard/routers/auth.py" in staged, \
        f"auth.py missing from staged files. git add calls: {add_calls}"
    assert "tests/untracked.py" not in staged, \
        f"Untracked file must not be staged. git add calls: {add_calls}"
