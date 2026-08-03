"""Tests for the warm git worktree pool (issue #1411).

Each test function maps to one acceptance criterion.

Rewritten in issue #2035 to match the shared-venv-cache design: the pool builds
ONE persistent venv under venv-cache/ (keyed by requirements hash) and symlinks
each slot to it, instead of creating a separate venv per slot.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(tmp_path, slots=2, base_branch="sprint/sprint-1", requirements=None):
    """Return a WorktreePool wired to tmp_path with mocked git operations."""
    from services.sprint_manager.worktree_pool import WorktreePool
    pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    return WorktreePool(
        pool_dir=pool_dir,
        repo_root=repo_root,
        base_branch=base_branch,
        slots=slots,
        requirements_file=requirements,
    )


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


def _stamp_slots(pool):
    """Create slot dirs + .git files on disk so _slot_healthy() returns True.

    acquire() added a health-check guard (issue #2032) that requires each slot
    directory to exist with a .git file before dispatch. Since subprocess is
    mocked in tests the real `git worktree add` never runs, so we stamp the
    minimal on-disk structure here instead.
    """
    for i in range(pool.slots):
        slot = pool.pool_dir / f"slot-{i}"
        slot.mkdir(parents=True, exist_ok=True)
        (slot / ".git").write_text("gitdir: ../../.git/worktrees/slot-0\n")


# ---------------------------------------------------------------------------
# AC1 — K worktrees created under worktree-pool/ at sprint start
# ---------------------------------------------------------------------------

class TestAC1WorktreesCreated:
    """AC1: At sprint start, K worktrees are created under .commander/runtime/worktree-pool/
    where K = max_coder_slots (default 2, clamped to hard cap of 4).
    """

    def test_default_two_slots_created(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        assert (tmp_path / ".commander" / "runtime" / "worktree-pool").exists()
        assert len(pool._free) == 2

    def test_slot_dirs_are_under_pool_dir(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        # Each free slot path must be a direct child of pool_dir
        for wt in pool._free:
            assert wt.parent == pool.pool_dir

    def test_one_slot(self, tmp_path):
        pool = _make_pool(tmp_path, slots=1)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        assert len(pool._free) == 1

    def test_four_slots(self, tmp_path):
        pool = _make_pool(tmp_path, slots=4)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        assert len(pool._free) == 4

    def test_max_coder_slots_default_is_two(self, tmp_path):
        from services.sprint_manager.worktree_pool import DEFAULT_SLOTS
        assert DEFAULT_SLOTS == 2

    def test_git_worktree_add_called_for_each_slot(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.create()
        worktree_add_calls = [
            c for c in mock_run.call_args_list
            if c.args and len(c.args[0]) >= 3
            and c.args[0][0] == "git"
            and "worktree" in c.args[0]
            and "add" in c.args[0]
        ]
        assert len(worktree_add_calls) == 2


# ---------------------------------------------------------------------------
# AC2 — One shared venv built once under venv-cache/ and reused across slots
# ---------------------------------------------------------------------------

class TestAC2FreshVenvPerWorktree:
    """AC2: One persistent shared venv is created once under venv-cache/ and
    reused across all slots via a symlink.  The old per-slot venv design (one
    `python -m venv` + `pip install` per slot) was replaced with a shared cache
    to eliminate the 2-3 min latency on every sprint run (issue #2035 rewrite).
    """

    def test_shared_venv_created_once(self, tmp_path):
        """Shared venv is built once regardless of slot count."""
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.create()
        venv_calls = [
            c for c in mock_run.call_args_list
            if c.args and "-m" in c.args[0] and "venv" in c.args[0]
        ]
        assert len(venv_calls) == 1, (
            f"Expected exactly 1 shared-venv creation call, got {len(venv_calls)}"
        )

    def test_shared_venv_path_is_outside_pool_dir(self, tmp_path):
        """Shared venv lives under venv-cache/, not inside individual slot dirs."""
        pool = _make_pool(tmp_path, slots=3)
        venv_paths = []
        def _capture(*args, **kwargs):
            cmd = args[0] if args else []
            if "-m" in cmd and "venv" in cmd:
                venv_paths.append(cmd[-1])
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m
        with patch("subprocess.run", side_effect=_capture):
            pool.create()
        assert len(venv_paths) == 1, "One shared venv, not per-slot"
        assert str(pool._venv_cache) in venv_paths[0], (
            "Shared venv path must be under venv-cache/, not inside a slot dir"
        )

    def test_pip_install_called_once_for_shared_venv(self, tmp_path):
        """pip install runs once for the shared venv, not once per slot."""
        req = tmp_path / "requirements.txt"
        req.write_text("fastapi\n")
        pool = _make_pool(tmp_path, slots=2, requirements=req)
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.create()
        pip_calls = [
            c for c in mock_run.call_args_list
            if c.args and "pip" in str(c.args[0]) and "install" in c.args[0]
        ]
        assert len(pip_calls) == 1, (
            "pip install must run once for the shared venv, not once per slot"
        )

    def test_pip_install_not_called_without_requirements(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2, requirements=None)
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.create()
        pip_calls = [
            c for c in mock_run.call_args_list
            if c.args and "pip" in str(c.args[0]) and "install" in c.args[0]
        ]
        assert len(pip_calls) == 0

    def test_pip_install_uses_venv_pip_not_system(self, tmp_path):
        """pip install must use the shared venv's pip binary, not system pip."""
        req = tmp_path / "requirements.txt"
        req.write_text("fastapi\n")
        pool = _make_pool(tmp_path, slots=1, requirements=req)
        pip_cmds = []
        def _capture(*args, **kwargs):
            cmd = args[0] if args else []
            if "pip" in str(cmd) and "install" in cmd:
                pip_cmds.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m
        with patch("subprocess.run", side_effect=_capture):
            pool.create()
        assert pip_cmds, "pip install was not called"
        # pip binary must be inside the shared venv dir under venv-cache/
        assert "venv" in str(pip_cmds[0][0]), "pip must come from the shared venv"


# ---------------------------------------------------------------------------
# AC3 — Each dispatch gets exactly one free worktree; no double-assignment
# ---------------------------------------------------------------------------

class TestAC3ExclusiveAssignment:
    """AC3: Each concurrent coder dispatch is assigned exactly one free worktree;
    a worktree in use is not assigned to a second coder.
    """

    def test_acquire_returns_different_paths_for_two_threads(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)

        acquired = []
        lock = threading.Lock()

        def _worker():
            wt = pool.acquire()
            with lock:
                acquired.append(wt)
            time.sleep(0.05)
            pool.release(wt)

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(acquired) == 2
        assert acquired[0] != acquired[1], "Both threads got the same worktree"

    def test_acquire_removes_from_free_pool(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        wt = pool.acquire()
        assert wt not in pool._free
        assert wt in pool._in_use
        pool.release(wt)

    def test_in_use_worktree_not_reassigned(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        wt1 = pool.acquire()
        wt2 = pool.acquire()
        assert wt1 != wt2
        pool.release(wt1)
        pool.release(wt2)


# ---------------------------------------------------------------------------
# AC4 — Worktree reset on ticket completion before returning to pool
# ---------------------------------------------------------------------------

class TestAC4WorktreeReset:
    """AC4: On ticket completion the assigned worktree is reset with
    git clean -fdx and checked out to the sprint base branch before being
    returned to the free pool.
    """

    def test_git_clean_called_on_release(self, tmp_path):
        pool = _make_pool(tmp_path, slots=1, base_branch="sprint/test")
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        wt = pool.acquire()
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.release(wt)
        clean_calls = [
            c for c in mock_run.call_args_list
            if c.args and "git" in c.args[0] and "clean" in c.args[0]
            and "-fdx" in c.args[0]
        ]
        assert clean_calls, "git clean -fdx was not called on release"

    def test_git_checkout_base_branch_on_release(self, tmp_path):
        pool = _make_pool(tmp_path, slots=1, base_branch="sprint/test")
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        wt = pool.acquire()
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.release(wt)
        checkout_calls = [
            c for c in mock_run.call_args_list
            if c.args and "git" in c.args[0] and "checkout" in c.args[0]
            and "sprint/test" in c.args[0]
        ]
        assert checkout_calls, "git checkout <base_branch> was not called on release"

    def test_released_worktree_returns_to_free_pool(self, tmp_path):
        pool = _make_pool(tmp_path, slots=1)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        wt = pool.acquire()
        assert wt not in pool._free
        with patch("subprocess.run", side_effect=_success):
            pool.release(wt)
        assert wt in pool._free
        assert wt not in pool._in_use

    def test_clean_before_checkout(self, tmp_path):
        """git clean must happen before git checkout."""
        pool = _make_pool(tmp_path, slots=1, base_branch="sprint/test")
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        wt = pool.acquire()
        call_order = []
        def _record(*args, **kwargs):
            cmd = args[0] if args else []
            if "git" in cmd and "clean" in cmd:
                call_order.append("clean")
            elif "git" in cmd and "checkout" in cmd:
                call_order.append("checkout")
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m
        with patch("subprocess.run", side_effect=_record):
            pool.release(wt)
        assert call_order.index("clean") < call_order.index("checkout")


# ---------------------------------------------------------------------------
# AC5 — All worktrees removed at sprint end
# ---------------------------------------------------------------------------

class TestAC5TeardownRemovesAll:
    """AC5: At sprint end, all worktrees in the pool are removed and no stray
    worktrees remain under .commander/runtime/worktree-pool/.
    """

    def test_teardown_calls_worktree_remove_for_each_slot(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.teardown()
        remove_calls = [
            c for c in mock_run.call_args_list
            if c.args and "git" in c.args[0] and "worktree" in c.args[0]
            and "remove" in c.args[0]
        ]
        assert len(remove_calls) == 2

    def test_teardown_calls_worktree_prune(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.teardown()
        prune_calls = [
            c for c in mock_run.call_args_list
            if c.args and "git" in c.args[0] and "worktree" in c.args[0]
            and "prune" in c.args[0]
        ]
        assert prune_calls, "git worktree prune was not called on teardown"

    def test_teardown_removes_pool_dir(self, tmp_path):
        pool = _make_pool(tmp_path, slots=1)
        pool.pool_dir.mkdir(parents=True, exist_ok=True)
        # Create a fake slot dir to simulate an existing worktree
        (pool.pool_dir / "slot-0").mkdir()
        with patch("subprocess.run", side_effect=_success), \
             patch("shutil.rmtree") as mock_rm:
            pool._free = [pool.pool_dir / "slot-0"]
            pool.teardown()
        # Pool dir should be removed
        assert mock_rm.called or not pool.pool_dir.exists()

    def test_teardown_also_removes_in_use_worktrees(self, tmp_path):
        """Teardown includes worktrees currently marked in-use."""
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        _wt = pool.acquire()  # move one to in-use
        with patch("subprocess.run", side_effect=_success) as mock_run:
            pool.teardown()
        remove_calls = [
            c for c in mock_run.call_args_list
            if c.args and "git" in c.args[0] and "worktree" in c.args[0]
            and "remove" in c.args[0]
        ]
        assert len(remove_calls) == 2, "All 2 worktrees (free + in-use) should be removed"


# ---------------------------------------------------------------------------
# AC6 — Orphan reconciliation on startup
# ---------------------------------------------------------------------------

class TestAC6OrphanReconciliation:
    """AC6: On service startup, orphaned worktrees under .commander/runtime/worktree-pool/
    left by a previous crash are detected, safely pruned, and the pool is
    re-initialised to a clean state before any dispatch proceeds.
    """

    def test_reconcile_detects_orphans(self, tmp_path, capsys):
        from services.sprint_manager.worktree_pool import WorktreePool
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        pool_dir.mkdir(parents=True)
        (pool_dir / "slot-0").mkdir()
        (pool_dir / "slot-1").mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        with patch("subprocess.run", side_effect=_success):
            WorktreePool.reconcile_orphans(pool_dir, repo_root)

        out = capsys.readouterr().out
        assert "orphan" in out.lower() or "reconcil" in out.lower()

    def test_reconcile_prunes_each_orphan_with_git(self, tmp_path):
        from services.sprint_manager.worktree_pool import WorktreePool
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        pool_dir.mkdir(parents=True)
        (pool_dir / "slot-0").mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        with patch("subprocess.run", side_effect=_success) as mock_run:
            WorktreePool.reconcile_orphans(pool_dir, repo_root)

        remove_calls = [
            c for c in mock_run.call_args_list
            if c.args and "git" in c.args[0] and "worktree" in c.args[0]
            and "remove" in c.args[0]
        ]
        assert remove_calls, "git worktree remove not called for orphan"

    def test_reconcile_calls_prune_after_removal(self, tmp_path):
        from services.sprint_manager.worktree_pool import WorktreePool
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        pool_dir.mkdir(parents=True)
        (pool_dir / "slot-0").mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        with patch("subprocess.run", side_effect=_success) as mock_run:
            WorktreePool.reconcile_orphans(pool_dir, repo_root)

        prune_calls = [
            c for c in mock_run.call_args_list
            if c.args and "git" in c.args[0] and "prune" in c.args[0]
        ]
        assert prune_calls, "git worktree prune was not called after orphan removal"

    def test_reconcile_noop_when_pool_dir_absent(self, tmp_path):
        from services.sprint_manager.worktree_pool import WorktreePool
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        # Should not raise
        with patch("subprocess.run", side_effect=_success) as mock_run:
            WorktreePool.reconcile_orphans(pool_dir, repo_root)
        assert not mock_run.called

    def test_reconcile_noop_when_pool_dir_empty(self, tmp_path):
        from services.sprint_manager.worktree_pool import WorktreePool
        pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
        pool_dir.mkdir(parents=True)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        with patch("subprocess.run", side_effect=_success) as mock_run:
            WorktreePool.reconcile_orphans(pool_dir, repo_root)
        remove_calls = [
            c for c in mock_run.call_args_list
            if c.args and "worktree" in c.args[0] and "remove" in c.args[0]
        ]
        assert not remove_calls


# ---------------------------------------------------------------------------
# AC7 — Additional dispatches queue/wait when all K slots are occupied
# ---------------------------------------------------------------------------

class TestAC7WaitWhenFull:
    """AC7: When all K worktree slots are occupied, additional dispatch requests
    wait or are queued rather than proceeding without an isolated worktree.
    """

    def test_third_acquire_blocks_until_slot_freed(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)

        wt1 = pool.acquire()
        wt2 = pool.acquire()

        acquired_third = []
        barrier = threading.Event()

        def _waiter():
            # This should block until one slot is released
            barrier.set()
            wt = pool.acquire()
            acquired_third.append(wt)
            pool.release(wt)

        t = threading.Thread(target=_waiter)
        t.start()
        barrier.wait()
        time.sleep(0.05)  # confirm it's still waiting
        assert not acquired_third, "Third acquire returned before a slot was freed"

        # Release one slot — the waiter should unblock
        with patch("subprocess.run", side_effect=_success):
            pool.release(wt1)
        t.join(timeout=2)
        assert acquired_third, "Third acquire never unblocked after slot was freed"
        with patch("subprocess.run", side_effect=_success):
            pool.release(wt2)

    def test_acquire_returns_immediately_when_slot_free(self, tmp_path):
        pool = _make_pool(tmp_path, slots=1)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        start = time.monotonic()
        wt = pool.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, "acquire blocked when a slot should have been immediately free"
        with patch("subprocess.run", side_effect=_success):
            pool.release(wt)


# ---------------------------------------------------------------------------
# AC8 — Two coders have no working-tree interference
# ---------------------------------------------------------------------------

class TestAC8Isolation:
    """AC8: Two coders dispatched simultaneously produce no working-tree
    interference (file writes by coder A are not visible in coder B's worktree).
    """

    def test_acquired_paths_are_distinct_directories(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        wt_a = pool.acquire()
        wt_b = pool.acquire()
        assert wt_a != wt_b, "Two coders received the same worktree path"
        with patch("subprocess.run", side_effect=_success):
            pool.release(wt_a)
            pool.release(wt_b)

    def test_slots_are_siblings_not_nested(self, tmp_path):
        """All slot directories are siblings under pool_dir — none nests another."""
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        _stamp_slots(pool)
        wt_a = pool.acquire()
        wt_b = pool.acquire()
        # wt_a must not be an ancestor of wt_b and vice-versa
        assert not str(wt_b).startswith(str(wt_a))
        assert not str(wt_a).startswith(str(wt_b))
        with patch("subprocess.run", side_effect=_success):
            pool.release(wt_a)
            pool.release(wt_b)


# ---------------------------------------------------------------------------
# AC1 (cap) — Hard cap of 4 worktrees even when max_coder_slots > 4
# ---------------------------------------------------------------------------

class TestAC1HardCap:
    """AC1 (cap): Hard cap of 4 worktrees regardless of max_coder_slots setting."""

    def test_slots_clamped_to_four_when_above_cap(self, tmp_path):
        from services.sprint_manager.worktree_pool import MAX_SLOTS
        assert MAX_SLOTS == 4
        pool = _make_pool(tmp_path, slots=5)
        assert pool.slots == 4, "slots must be clamped to MAX_SLOTS=4"

    def test_cap_warning_logged_when_above_cap(self, tmp_path, capsys):
        _make_pool(tmp_path, slots=5)
        out = capsys.readouterr().out
        assert "cap" in out.lower() or "clamp" in out.lower(), (
            "No warning logged when max_coder_slots exceeded hard cap"
        )

    def test_four_slots_created_for_cap_five(self, tmp_path):
        pool = _make_pool(tmp_path, slots=5)
        with patch("subprocess.run", side_effect=_success):
            pool.create()
        assert len(pool._free) == 4


# ---------------------------------------------------------------------------
# max_coder_slots in SprintConfig
# ---------------------------------------------------------------------------

class TestMaxCoderSlotsConfig:
    """max_coder_slots is a SprintConfig field (Optional[int], default None)."""

    def test_sprint_config_has_max_coder_slots(self):
        from services.sprint_manager.config import SprintConfig
        cfg = SprintConfig()
        assert hasattr(cfg, "max_coder_slots")

    def test_sprint_config_max_coder_slots_defaults_none(self):
        """SprintConfig.max_coder_slots is Optional[int] with Python default None.

        None means "not set in sprint.yaml" — sprint_manager falls back to 1
        (or DEFAULT_SLOTS when the pool is constructed directly).  The
        documentation default of 2 lives in settings_schema.KNOWN_FIELDS.
        """
        from services.sprint_manager.config import SprintConfig
        cfg = SprintConfig()
        assert cfg.max_coder_slots is None

    def test_max_coder_slots_in_settings_schema(self):
        from services.sprint_manager.settings_schema import KNOWN_FIELDS
        assert "max_coder_slots" in KNOWN_FIELDS
        assert KNOWN_FIELDS["max_coder_slots"]["default"] == 2
        assert KNOWN_FIELDS["max_coder_slots"]["secret"] is False
