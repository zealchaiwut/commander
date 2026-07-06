"""Tests for issue #1653: Remove redundant rebase --abort in _call_finish_feature conflict path.

AC1: The inner _try("git", "rebase", "--abort") is removed from the conflict branch
     inside the try block of _call_finish_feature (approximately line 1645).
AC2: The finally block still calls _restore_worktree_branch, which issues rebase --abort
     unconditionally.
AC3: No other change to _call_finish_feature — single-line removal only.
AC4: When a rebase conflict occurs, only one git rebase --abort subprocess call is made
     (via _restore_worktree_branch in finally).
AC5: All existing tests for _call_finish_feature continue to pass.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

for _mod in ("github_client", "neondb"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup_repos(tmp_path: Path):
    bare = tmp_path / "origin.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "develop"], cwd=bare, check=True, capture_output=True)

    boot = tmp_path / "bootstrap"
    boot.mkdir()
    subprocess.run(["git", "init", "-b", "develop"], cwd=boot, check=True, capture_output=True)
    _git(boot, "config", "user.email", "test@test.com")
    _git(boot, "config", "user.name", "Test")
    _git(boot, "remote", "add", "origin", str(bare))
    (boot / "README.md").write_text("init\n")
    _git(boot, "add", ".")
    _git(boot, "commit", "-m", "init")
    _git(boot, "push", "origin", "develop")

    tester = tmp_path / "tester"
    subprocess.run(["git", "clone", str(bare), str(tester)], check=True, capture_output=True)
    _git(tester, "config", "user.email", "test@test.com")
    _git(tester, "config", "user.name", "Test")

    return bare, tester


def _add_commit(repo: Path, filename: str, content: str, msg: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _fake_proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    r = subprocess.CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = stderr
    return r


# ── AC1: no rebase --abort inside try block's conflict branch ─────────────────

def test_ac1_no_abort_call_inside_try_conflict_branch():
    """AC1: The inner _try('git', 'rebase', '--abort') is absent from the
    conflict branch of the try block in _call_finish_feature."""
    src = inspect.getsource(sm._call_finish_feature)

    # Locate the if-not-ok_rb conflict block
    # It starts at "if not ok_rb:" and ends before "# Rebase succeeded"
    try_start = src.find("try:")
    assert try_start >= 0, "_call_finish_feature must have a try block"

    conflict_start = src.find("if not ok_rb:", try_start)
    assert conflict_start >= 0, "conflict branch (if not ok_rb:) must exist in try block"

    # The conflict branch ends at the "# Rebase succeeded" comment or "return False, conflict_files"
    rebase_succeeded_comment = src.find("# Rebase succeeded", conflict_start)
    assert rebase_succeeded_comment >= 0, "'# Rebase succeeded' comment must exist"

    conflict_branch_src = src[conflict_start:rebase_succeeded_comment]

    # AC1: no rebase --abort call inside the conflict branch
    assert '"rebase", "--abort"' not in conflict_branch_src, (
        "The inner _try('git', 'rebase', '--abort') must be removed from the "
        "conflict branch of the try block (AC1). Found it in:\n" + conflict_branch_src
    )


# ── AC2: finally block still calls _restore_worktree_branch ───────────────────

def test_ac2_finally_still_calls_restore_worktree_branch():
    """AC2: The finally block in _call_finish_feature still calls
    _restore_worktree_branch, which issues git rebase --abort unconditionally."""
    src = inspect.getsource(sm._call_finish_feature)

    finally_pos = src.find("finally:")
    assert finally_pos >= 0, "_call_finish_feature must have a finally block"

    finally_src = src[finally_pos:]
    assert "_restore_worktree_branch(" in finally_src, (
        "_restore_worktree_branch must still be called in the finally block (AC2)"
    )


def test_ac2_restore_worktree_branch_still_aborts():
    """AC2: _restore_worktree_branch still issues _try('git', 'rebase', '--abort')."""
    src = inspect.getsource(sm._restore_worktree_branch)
    assert '"rebase", "--abort"' in src, (
        "_restore_worktree_branch must still call _try('git', 'rebase', '--abort') (AC2)"
    )


# ── AC3: no other logic changed — only the redundant abort line removed ────────

def test_ac3_finally_block_unchanged():
    """AC3: The finally block delegates exclusively to _restore_worktree_branch
    — no other change around the finally."""
    src = inspect.getsource(sm._call_finish_feature)
    finally_pos = src.find("finally:")
    finally_src = src[finally_pos:]
    assert "_restore_worktree_branch(wt_root, target_branch)" in finally_src, (
        "finally block must still call _restore_worktree_branch(wt_root, target_branch) unchanged (AC3)"
    )


def test_ac3_conflict_section_core_logic_intact():
    """AC3: Conflict branch still contains file extraction, logging, and return — only the
    redundant abort was removed, nothing else."""
    src = inspect.getsource(sm._call_finish_feature)
    conflict_start = src.find("if not ok_rb:")
    assert conflict_start >= 0, "conflict branch (if not ok_rb:) must exist"
    finally_pos = src.find("finally:", conflict_start)
    conflict_src = src[conflict_start:finally_pos]
    assert "_extract_rebase_conflict_files" in conflict_src, (
        "_extract_rebase_conflict_files call removed from conflict branch (AC3)"
    )
    assert "sys.stdout.write" in conflict_src, (
        "sys.stdout.write logging removed from conflict branch (AC3)"
    )
    assert "return False" in conflict_src, (
        "return False removed from conflict branch (AC3)"
    )


# ── AC4: exactly one rebase --abort call when a conflict occurs ───────────────

def test_ac4_only_one_rebase_abort_on_conflict(tmp_path, monkeypatch):
    """AC4: When a rebase conflict occurs in _call_finish_feature, only one
    'git rebase --abort' subprocess is executed (via _restore_worktree_branch
    in the finally block), not two."""
    bare, tester = _setup_repos(tmp_path)

    # Advance develop so the rebase has something to land on.
    boot = tmp_path / "bootstrap"
    _git(boot, "checkout", "develop")
    _add_commit(boot, "conflict.txt", "base version\n", "base: advance develop")
    _git(boot, "push", "origin", "develop")

    # Set up a conflicting feature branch.
    _git(tester, "fetch", "origin")
    _git(tester, "checkout", "-b", "feature/1653-conflict")
    _add_commit(tester, "conflict.txt", "feature version\n", "feat: conflict branch")
    _git(tester, "push", "origin", "feature/1653-conflict")
    _git(tester, "checkout", "develop")

    abort_calls = []
    real_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and list(cmd[:3]) == ["git", "rebase", "--abort"]:
            abort_calls.append(list(cmd))
            # Delegate to real git so branch state stays valid.
            return real_run(cmd, **kwargs)
        if isinstance(cmd, (list, tuple)) and "finish_feature" in " ".join(str(c) for c in cmd):
            return _fake_proc(1, stderr="CONFLICT: cannot merge")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(sm.subprocess, "run", patched_run)
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)

    success, conflict_files = sm._call_finish_feature(
        issue_num=1653, worktester_root=tester, target_branch="develop",
    )

    assert success is False
    assert len(abort_calls) == 1, (
        f"Expected exactly 1 'git rebase --abort' call on conflict, got {len(abort_calls)}. "
        f"Calls: {abort_calls}. "
        "The redundant inner abort in the conflict branch must be removed (AC4)."
    )
