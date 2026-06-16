"""Tests for the rebase-conflict self-heal in _worktree_hygiene (#1073).

A rerun-child sub-sprint dispatches the coder with is_retry=True, which rebases
the existing feature branch onto the new sprint base. When that branch overlaps
files already merged from another ticket the rebase conflicts EVERY time, and
the old behaviour aborted the ticket as class=merge — so the coder was never
dispatched and the ticket could never progress (sprint 81/81.1/81.2 #1073).

With recover_on_rebase_conflict=True (coder path) the conflict is no longer
fatal: the stale branch is deleted and the worktree reset to base so the coder
rebuilds fresh. The tester path keeps the old fatal behaviour (default False).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup_repos(tmp_path: Path):
    bare = tmp_path / "origin.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=bare, check=True, capture_output=True)

    boot = tmp_path / "bootstrap"
    boot.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=boot, check=True, capture_output=True)
    _git(boot, "config", "user.email", "test@test.com")
    _git(boot, "config", "user.name", "Test")
    _git(boot, "remote", "add", "origin", str(bare))
    (boot / "README.md").write_text("init\n")
    _git(boot, "add", ".")
    _git(boot, "commit", "-m", "init")
    _git(boot, "push", "origin", "main")

    clone = tmp_path / "worktree"
    subprocess.run(["git", "clone", str(bare), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "user.email", "test@test.com")
    _git(clone, "config", "user.name", "Test")
    return bare, clone


def _add_commit(repo: Path, filename: str, content: str, msg: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _make_conflicting_feature(clone: Path, branch: str) -> None:
    """Create a feature branch that conflicts with an advanced origin/main."""
    _git(clone, "checkout", "-b", branch)
    _add_commit(clone, "conflict.txt", "feature version\n", "feature: change")
    _git(clone, "checkout", "main")
    _add_commit(clone, "conflict.txt", "base version\n", "base: change")
    _git(clone, "push", "origin", "main")
    # Hygiene runs with the base branch checked out (the feature branch exists
    # but is not HEAD); step-4 reset would otherwise clobber the feature ref.


def test_coder_recovers_from_rebase_conflict(tmp_path):
    """recover_on_rebase_conflict=True: conflict returns no error, branch reset."""
    bare, clone = _setup_repos(tmp_path)
    _make_conflicting_feature(clone, "feature/1073-conflict")
    base_sha = _git(clone, "rev-parse", "origin/main")

    wt_sha, ret_base, err = sm._worktree_hygiene(
        worktree=clone,
        ticket_id=1073,
        merge_target="main",
        is_retry=True,
        repo_root=tmp_path,
        recover_on_rebase_conflict=True,
    )

    assert err is None, f"coder recovery must clear the error, got {err!r}"
    # Stale local feature branch deleted so the coder rebuilds fresh.
    branches = _git(clone, "branch", "--list", "feature/1073-conflict")
    assert branches == "", f"stale feature branch must be deleted, got {branches!r}"
    # Worktree reset to base.
    assert _git(clone, "rev-parse", "HEAD") == base_sha
    assert wt_sha == base_sha and ret_base == base_sha
    # No merge sidecar written.
    sidecar = tmp_path / ".commander" / "runtime" / "last-failure-1073.json"
    assert not sidecar.exists(), "recovery must NOT write a merge failure sidecar"


def test_tester_path_still_fatal_on_conflict(tmp_path):
    """Default recover_on_rebase_conflict=False keeps the class=merge behaviour."""
    bare, clone = _setup_repos(tmp_path)
    _make_conflicting_feature(clone, "feature/1073-conflict")

    _, _, err = sm._worktree_hygiene(
        worktree=clone,
        ticket_id=1073,
        merge_target="main",
        is_retry=True,
        repo_root=tmp_path,
        # recover_on_rebase_conflict defaults to False (tester path)
    )

    assert err == "merge", f"tester path must still return 'merge', got {err!r}"
    # Feature branch preserved (not deleted) for the tester path.
    branches = _git(clone, "branch", "--list", "feature/1073-conflict")
    assert "feature/1073-conflict" in branches


def test_recovery_reset_failure_falls_through_to_merge(tmp_path, monkeypatch):
    """If the recovery `git reset --hard` fails, the worktree is NOT on base — the
    coder must NOT be dispatched into it. Hygiene falls through to a merge failure
    instead of returning a clean recovery with a stale SHA."""
    bare, clone = _setup_repos(tmp_path)
    _make_conflicting_feature(clone, "feature/1073-conflict")

    real_try = sm._try

    def flaky_try(*cmd, cwd=None):
        # Force only the recovery reset-to-base to fail; everything else is real.
        if cmd[:3] == ("git", "reset", "--hard") and "origin/main" in cmd:
            return (False, "", "simulated reset failure")
        return real_try(*cmd, cwd=cwd)

    monkeypatch.setattr(sm, "_try", flaky_try)

    _, _, err = sm._worktree_hygiene(
        worktree=clone,
        ticket_id=1073,
        merge_target="main",
        is_retry=True,
        repo_root=tmp_path,
        recover_on_rebase_conflict=True,
    )

    assert err == "merge", f"failed recovery must fall through to 'merge', got {err!r}"
    sidecar = tmp_path / ".commander" / "runtime" / "last-failure-1073.json"
    assert sidecar.exists(), "a failed recovery must record the merge failure sidecar"
