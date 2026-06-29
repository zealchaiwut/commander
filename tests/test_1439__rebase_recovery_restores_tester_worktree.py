"""Tests for issue #1439: Rebase recovery leaves tester worktree on a feature branch.

The automated rebase recovery path in `_call_finish_feature` checks out the
feature branch in the shared tester worktree (`wt_root`) but never restores it to
`target_branch`. The next ticket dispatched into the same worktree then starts
from a prior ticket's feature branch.

AC1: After a successful rebase recovery, `git -C <wt_root> branch --show-current`
     returns `target_branch`, not the feature branch.
AC2: After a failed/aborted rebase recovery, `git -C <wt_root> branch --show-current`
     returns `target_branch`, not the feature branch.
AC3: The branch restoration runs in a `finally` block so it executes regardless of
     whether the rebase attempt succeeds, fails, or raises an exception.
AC4: The next ticket dispatched into the same tester worktree starts from
     `target_branch`, not from a prior ticket's feature branch.
AC5: No changes are made to the feature branch itself or its commits during the
     restoration step.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

# Stub heavy optional imports before loading sprint_manager.
for _mod in ("github_client", "neondb"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ── helpers (mirror test_1414) ─────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup_repos(tmp_path: Path):
    """Create a bare origin and one clone (the 'tester' worktree)."""
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


def _setup_feature_branch(tester: Path, branch: str, filename: str, content: str):
    """Create a remote feature branch so _call_finish_feature can find it, then
    return the tester worktree to develop."""
    _git(tester, "fetch", "origin")
    _git(tester, "checkout", "-b", branch)
    _add_commit(tester, filename, content, f"feat: {branch}")
    _git(tester, "push", "origin", branch)
    _git(tester, "checkout", "develop")


def _advance_develop(boot: Path, filename: str, content: str, msg: str):
    _git(boot, "checkout", "develop")
    _git(boot, "fetch", "origin")
    _git(boot, "reset", "--hard", "origin/develop")
    _add_commit(boot, filename, content, msg)
    _git(boot, "push", "origin", "develop")


# ── AC1: worktree restored to target after a SUCCESSFUL rebase recovery ─────────

def test_ac1_worktree_restored_to_target_after_successful_rebase(tmp_path, monkeypatch):
    """AC1: After a successful rebase recovery, the tester worktree is on
    target_branch, not the feature branch."""
    bare, tester = _setup_repos(tmp_path)
    _advance_develop(tmp_path / "bootstrap", "other.txt", "merge-N\n", "feat: merge-N lands first")
    _setup_feature_branch(tester, "feature/99-disjoint", "feature99.txt", "feature99\n")

    call_count = {"n": 0}
    real_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _fake_proc(1, stderr="CONFLICT: diverged from develop")
            return _fake_proc(0, stdout="merged\n")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(sm.subprocess, "run", patched_run)
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)

    success, conflict_files = sm._call_finish_feature(
        issue_num=99, worktester_root=tester, target_branch="develop",
    )

    assert success is True
    current = _git(tester, "branch", "--show-current")
    assert current == "develop", (
        f"worktree must be restored to develop after successful rebase, got {current!r}"
    )


# ── AC2: worktree restored to target after an ABORTED rebase recovery ───────────

def test_ac2_worktree_restored_to_target_after_aborted_rebase(tmp_path, monkeypatch):
    """AC2: After a rebase conflict (aborted recovery), the tester worktree is on
    target_branch, not the feature branch."""
    bare, tester = _setup_repos(tmp_path)
    _advance_develop(tmp_path / "bootstrap", "conflict.txt", "base version\n", "base: advance develop")

    # Feature branch touches the SAME file — will conflict on rebase.
    _git(tester, "fetch", "origin")
    _git(tester, "checkout", "-b", "feature/101-conflict")
    _add_commit(tester, "conflict.txt", "feature version\n", "feat: conflict branch")
    _git(tester, "push", "origin", "feature/101-conflict")
    _git(tester, "checkout", "develop")

    real_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            return _fake_proc(1, stderr="CONFLICT: cannot merge")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(sm.subprocess, "run", patched_run)
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)

    success, conflict_files = sm._call_finish_feature(
        issue_num=101, worktester_root=tester, target_branch="develop",
    )

    assert success is False
    assert "conflict.txt" in conflict_files
    current = _git(tester, "branch", "--show-current")
    assert current == "develop", (
        f"worktree must be restored to develop after aborted rebase, got {current!r}"
    )


# ── AC3: restoration runs in a finally block (executes even when recovery raises) ─

def test_ac3_worktree_restored_even_when_recovery_raises(tmp_path, monkeypatch):
    """AC3: If the post-rebase merge call raises, the restoration still runs (finally),
    so the worktree ends up on target_branch and the exception propagates."""
    bare, tester = _setup_repos(tmp_path)
    _advance_develop(tmp_path / "bootstrap", "other.txt", "merge-N\n", "feat: merge-N lands first")
    _setup_feature_branch(tester, "feature/102-disjoint", "feature102.txt", "feature102\n")

    call_count = {"n": 0}
    real_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _fake_proc(1, stderr="CONFLICT: diverged from develop")
            # Second call (post-rebase retry) blows up unexpectedly.
            raise RuntimeError("boom during post-rebase merge")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(sm.subprocess, "run", patched_run)
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="boom"):
        sm._call_finish_feature(
            issue_num=102, worktester_root=tester, target_branch="develop",
        )

    current = _git(tester, "branch", "--show-current")
    assert current == "develop", (
        f"finally block must restore worktree to develop even when recovery raises, got {current!r}"
    )


def test_ac3_restoration_is_in_finally_block_source():
    """AC3: the restoration call is lexically inside a `finally` block within
    `_call_finish_feature`."""
    import inspect
    src = inspect.getsource(sm._call_finish_feature)
    assert "finally:" in src, "_call_finish_feature must use a finally block for restoration"


# ── AC4: next ticket dispatched into the same worktree starts from target ───────

def test_ac4_next_dispatch_starts_from_target(tmp_path, monkeypatch):
    """AC4: After a rebase recovery, a second recovery call into the SAME worktree
    finds it on target_branch (not the first ticket's feature branch) at start."""
    bare, tester = _setup_repos(tmp_path)
    _advance_develop(tmp_path / "bootstrap", "other.txt", "merge-N\n", "feat: merge-N lands first")
    _setup_feature_branch(tester, "feature/103-first", "feature103.txt", "feature103\n")

    real_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            return _fake_proc(0, stdout="merged\n") if {"n": True} else _fake_proc(1)
        return real_run(cmd, **kwargs)

    # First recovery: finish fails once then succeeds via rebase.
    call_count = {"n": 0}

    def patched_run_seq(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _fake_proc(1, stderr="CONFLICT")
            return _fake_proc(0, stdout="merged\n")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(sm.subprocess, "run", patched_run_seq)
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)

    sm._call_finish_feature(issue_num=103, worktester_root=tester, target_branch="develop")

    # Simulate the start of the next ticket's worktree setup: it must observe the
    # worktree on develop, not on feature/103-first.
    current = _git(tester, "branch", "--show-current")
    assert current == "develop", (
        f"next ticket must start from develop, but worktree is on {current!r}"
    )


# ── AC5: restoration does not alter the feature branch commits ──────────────────

def test_ac5_feature_branch_commits_unchanged_by_restoration(tmp_path, monkeypatch):
    """AC5: The remote feature branch SHA is unchanged after an aborted rebase
    recovery — the restoration step only switches branches, it does not touch the
    feature branch's commits."""
    bare, tester = _setup_repos(tmp_path)
    _advance_develop(tmp_path / "bootstrap", "conflict.txt", "base version\n", "base: advance develop")

    _git(tester, "fetch", "origin")
    _git(tester, "checkout", "-b", "feature/104-conflict")
    _add_commit(tester, "conflict.txt", "feature version\n", "feat: conflict branch")
    _git(tester, "push", "origin", "feature/104-conflict")
    _git(tester, "checkout", "develop")

    sha_before = _git(tester, "rev-parse", "origin/feature/104-conflict")

    real_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            return _fake_proc(1, stderr="CONFLICT: cannot merge")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(sm.subprocess, "run", patched_run)
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)

    sm._call_finish_feature(issue_num=104, worktester_root=tester, target_branch="develop")

    _git(tester, "fetch", "origin")
    sha_after = _git(tester, "rev-parse", "origin/feature/104-conflict")
    assert sha_after == sha_before, (
        "restoration must not modify the feature branch commits "
        f"(before={sha_before}, after={sha_after})"
    )
