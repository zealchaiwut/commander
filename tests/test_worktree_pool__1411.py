"""Tests for issue #1411: Add warm git worktree pool for concurrent coder dispatch.

AC items verified:
  AC-1  At sprint start, K worktrees are created under .commander/runtime/worktree-pool/
        branched from sprint base branch, where K = max_coder_slots (default 2, capped at 4).
  AC-2  Each worktree receives its own fresh virtualenv via python -m venv + pip install;
        venvs are never copied or shared.
  AC-3  Each concurrent coder dispatch is assigned exactly one free worktree; in-use
        worktrees are not assigned to a second coder.
  AC-4  On ticket completion, assigned worktree is reset with git clean -fdx,
        checked out to base branch, then returned to free pool.
  AC-5  At sprint end, all worktrees are removed; no stray worktrees remain under
        .commander/runtime/worktree-pool/.
  AC-6  On startup, orphaned worktrees left by crash are detected, safely pruned,
        and pool re-initialized to clean state before dispatch proceeds.
  AC-7  When all K slots are occupied, additional dispatch requests wait/queue
        rather than proceeding without isolated worktree.
  AC-8  Two coders dispatched simultaneously produce no working-tree interference;
        file writes by coder A are not visible in coder B's worktree during execution.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.sprint_manager.worktree_pool import (
    WorktreePool,
    DEFAULT_SLOTS,
    MAX_SLOTS,
    SLOT_PREFIX,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _success(*_args, **_kwargs):
    """Stub returning a successful subprocess result."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


def _fail(*_args, **_kwargs):
    """Stub returning a failed subprocess result."""
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "error"
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWorktreePoolCreation:
    """AC-1: Pool creation with K slots"""

    def test_pool_creates_k_worktrees_at_default_slots(self, tmp_path):
        """Create pool with default slots (2) and verify worktrees created."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=DEFAULT_SLOTS,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        # Verify pool initialized with K free slots
        assert len(pool._free) == DEFAULT_SLOTS
        assert len(pool._in_use) == 0
        assert pool_dir.exists()

    def test_pool_creates_custom_slots_under_cap(self, tmp_path):
        """Create pool with 3 slots (custom, under hard cap)."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=3,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        assert len(pool._free) == 3

    def test_pool_clamps_slots_to_hard_cap(self, tmp_path, capsys):
        """AC-1: Requesting > 4 slots is clamped to 4 with warning logged."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=5,  # Above hard cap
        )

        # Verify clamping occurred at construction time
        assert pool.slots == MAX_SLOTS

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        # Check warning was logged
        captured = capsys.readouterr()
        assert "hard cap" in captured.out

    def test_git_worktree_add_called_for_each_slot(self, tmp_path):
        """AC-1: git worktree add is called for each slot."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=2,
        )

        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.create()

        # Find git worktree add calls
        worktree_add_calls = [
            c for c in mock_run.call_args_list
            if c[0][0][:3] == ["git", "worktree", "add"]
        ]
        assert len(worktree_add_calls) == 2


class TestWorktreeVenv:
    """AC-2: Fresh virtualenv per worktree"""

    def test_venv_create_called_per_slot(self, tmp_path):
        """AC-2: python -m venv called for each slot."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=2,
        )

        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.create()

        # Find venv create calls
        venv_calls = [
            c for c in mock_run.call_args_list
            if len(c[0][0]) > 2 and "-m" in c[0][0] and "venv" in c[0][0]
        ]
        assert len(venv_calls) == 2

    def test_venv_paths_are_distinct(self, tmp_path):
        """AC-2: Each venv is created at a distinct path."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=3,
        )

        venv_paths = []

        def capture_venv_paths(*args, **kwargs):
            cmd = args[0] if args else []
            if len(cmd) > 2 and "-m" in cmd and "venv" in cmd:
                venv_paths.append(cmd[-1])
            return _success()

        with patch("subprocess.run", side_effect=capture_venv_paths):
            pool.create()

        # All venv paths should be distinct
        assert len(set(venv_paths)) == 3

    def test_pip_install_called_when_requirements_present(self, tmp_path):
        """AC-2: pip install called for requirements file."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        req_file = tmp_path / "requirements.txt"
        req_file.write_text("pytest==7.0.0\n")

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=2,
            requirements_file=req_file,
        )

        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.create()

        # Find pip install calls
        pip_calls = [
            c for c in mock_run.call_args_list
            if len(c[0][0]) > 0 and "pip" in str(c[0][0]) and "install" in c[0][0]
        ]
        assert len(pip_calls) == 2


class TestWorktreeAcquisition:
    """AC-3: Slot assignment and mutual exclusion"""

    def test_acquire_returns_free_slot(self, tmp_path):
        """AC-3: acquire() returns a free worktree path."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=2,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        wt = pool.acquire()
        assert wt.parent == pool_dir
        assert len(pool._free) == 1
        assert len(pool._in_use) == 1

    def test_acquire_blocks_when_all_slots_in_use(self, tmp_path):
        """AC-7: acquire() blocks when no free slots available."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=1,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        wt1 = pool.acquire()
        acquired_2 = []

        def try_acquire():
            wt2 = pool.acquire()
            acquired_2.append(wt2)

        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=0.5)

        # Thread should still be blocked
        assert t.is_alive()

        # Release and thread should unblock
        pool.release(wt1)
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert len(acquired_2) == 1

    def test_same_worktree_not_assigned_twice_concurrently(self, tmp_path):
        """AC-3: In-use worktrees are not assigned to a second coder."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=2,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        wt1 = pool.acquire()
        wt2 = pool.acquire()

        # Should be different paths
        assert wt1 != wt2
        assert len(pool._free) == 0
        assert len(pool._in_use) == 2


class TestWorktreeRelease:
    """AC-4: Worktree reset and release"""

    def test_release_runs_git_clean_and_checkout(self, tmp_path):
        """AC-4: release() runs git clean -fdx and checks out base branch."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=1,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        wt = pool.acquire()

        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.release(wt)

        # Check git clean and git checkout were called
        clean_calls = [
            c for c in mock_run.call_args_list
            if c[0][0][:3] == ["git", "clean", "-fdx"]
        ]
        checkout_calls = [
            c for c in mock_run.call_args_list
            if len(c[0][0]) >= 3 and c[0][0][:2] == ["git", "checkout"]
        ]
        assert len(clean_calls) == 1
        assert len(checkout_calls) == 1

    def test_release_returns_worktree_to_free_pool(self, tmp_path):
        """AC-4: After release, worktree can be acquired again."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=1,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        wt1 = pool.acquire()
        assert len(pool._free) == 0

        with patch("subprocess.run", side_effect=_success):
            pool.release(wt1)

        assert len(pool._free) == 1
        assert len(pool._in_use) == 0

        wt2 = pool.acquire()
        assert wt1 == wt2


class TestWorktreeTeardown:
    """AC-5: Pool cleanup at sprint end"""

    def test_teardown_removes_all_worktrees(self, tmp_path):
        """AC-5: teardown() removes all worktrees from the pool."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=2,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        # Create actual slot directories so we can verify they're removed
        for i in range(2):
            (pool_dir / f"slot-{i}").mkdir(parents=True, exist_ok=True)

        # Acquire one to test teardown removes in-use slots too
        wt = pool.acquire()

        with patch("subprocess.run", side_effect=_success):
            pool.teardown()

        # Pool directory should be removed
        assert not pool_dir.exists()

    def test_git_worktree_prune_called_on_teardown(self, tmp_path):
        """AC-5: git worktree prune is called during teardown."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=2,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.teardown()

        # Check git worktree prune was called
        prune_calls = [
            c for c in mock_run.call_args_list
            if c[0][0][:3] == ["git", "worktree", "prune"]
        ]
        assert len(prune_calls) >= 1


class TestWorktreeReconciliation:
    """AC-6: Startup orphan reconciliation"""

    def test_reconcile_orphans_detects_and_removes(self, tmp_path):
        """AC-6: reconcile_orphans() detects and removes orphaned worktrees."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        # Create orphaned worktree directory
        pool_dir.mkdir(parents=True, exist_ok=True)
        (pool_dir / "slot-0").mkdir(parents=True)

        with patch("subprocess.run", side_effect=_success) as mock_run:
            WorktreePool.reconcile_orphans(pool_dir, repo_root)

        # Check git worktree remove was called
        remove_calls = [
            c for c in mock_run.call_args_list
            if len(c[0][0]) >= 3 and c[0][0][:3] == ["git", "worktree", "remove"]
        ]
        assert len(remove_calls) >= 1

    def test_reconcile_orphans_is_idempotent(self, tmp_path):
        """AC-6: reconcile_orphans() is safe to call multiple times."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        # Call on non-existent pool
        WorktreePool.reconcile_orphans(pool_dir, repo_root)
        assert not pool_dir.exists()

        # Call again
        WorktreePool.reconcile_orphans(pool_dir, repo_root)
        assert not pool_dir.exists()

    def test_reconcile_orphans_prunes_git_worktree(self, tmp_path):
        """AC-6: git worktree prune is called during reconciliation."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool_dir.mkdir(parents=True, exist_ok=True)
        (pool_dir / "slot-0").mkdir(parents=True)

        with patch("subprocess.run", side_effect=_success) as mock_run:
            WorktreePool.reconcile_orphans(pool_dir, repo_root)

        # Check git worktree prune was called
        prune_calls = [
            c for c in mock_run.call_args_list
            if c[0][0][:3] == ["git", "worktree", "prune"]
        ]
        assert len(prune_calls) >= 1


class TestWorktreeIsolation:
    """AC-8: No interference between concurrent coders"""

    def test_free_pool_isolation_per_slot(self, tmp_path):
        """AC-8: Each acquired slot is tracked separately in _in_use."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=2,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        wt_a = pool.acquire()
        wt_b = pool.acquire()

        # Verify they're in separate slot directories
        assert wt_a.name != wt_b.name
        assert len(pool._in_use) == 2
        assert len(pool._free) == 0

    def test_concurrent_acquire_operations_safe(self, tmp_path):
        """AC-8: Multiple threads can safely acquire slots concurrently."""
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        pool = WorktreePool(
            pool_dir=pool_dir,
            repo_root=repo_root,
            base_branch="sprint/sprint-1",
            slots=3,
        )

        with patch("subprocess.run", side_effect=_success):
            pool.create()

        acquired = []

        def acquire_and_store():
            wt = pool.acquire()
            acquired.append(wt)

        threads = [threading.Thread(target=acquire_and_store) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 3 slots acquired without duplicates
        assert len(acquired) == 3
        assert len(set(acquired)) == 3  # All unique
