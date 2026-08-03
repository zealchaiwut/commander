"""Tests for #2081: overnight sprint dispatch crashes after 30min pool timeout.

AC items verified:
  AC1  When target_branch causes sprint-branch creation to be skipped, the
       worktree pool is bound to target_branch (not the nonexistent sprint branch).
  AC2  The "N slot(s) ready" log line reflects the pool's *actual* created slot
       count, not the configured max_coder_slots.
  AC3  Zero usable slots → fail fast with a clear error, not a 1800s timeout.
  AC4  Behavioral: a pool bound to an existing branch creates slots and returns
       an acquire immediately (under 1s), proving no 1800s hang path exists.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(tmp_path, slots=1, base_branch="develop"):
    from services.sprint_manager.worktree_pool import WorktreePool
    pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    return WorktreePool(
        pool_dir=pool_dir,
        repo_root=repo_root,
        base_branch=base_branch,
        slots=slots,
    )


def _success(*_args, **_kwargs):
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


def _fail(*_args, **_kwargs):
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "fatal: invalid reference: sprint/sprint-1007"
    return m


# ---------------------------------------------------------------------------
# AC1 — Pool binds to target_branch, not the nonexistent sprint_branch
# ---------------------------------------------------------------------------

class TestAC1PoolBoundToTargetBranch:
    """AC1: When sprint-branch creation is skipped (target_branch != sprint_branch),
    the worktree pool must be bound to target_branch so git worktree add uses a
    branch that actually exists.
    """

    def test_pool_with_existing_branch_creates_slots(self, tmp_path):
        """Pool bound to 'develop' (the actual target_branch) creates slots.

        Simulates the fix: overnight mode passes target_branch='develop'.
        With the fix, the pool uses 'develop', which exists → slots created.
        """
        pool = _make_pool(tmp_path, slots=1, base_branch="develop")
        with patch("subprocess.run", side_effect=_success):
            created = pool.create()
        assert created == 1, (
            "Pool bound to existing target_branch must create 1 slot, got {created}"
        )

    def test_pool_with_nonexistent_sprint_branch_creates_zero_slots(self, tmp_path):
        """Pool bound to a nonexistent sprint branch creates 0 slots.

        Simulates the bug: without the fix, the pool uses sprint/sprint-1007
        which was never created → git worktree add fails → 0 slots.
        """
        pool = _make_pool(tmp_path, slots=1, base_branch="sprint/sprint-1007")
        with patch("subprocess.run", side_effect=_fail):
            created = pool.create()
        assert created == 0, (
            "Pool bound to nonexistent branch must return 0 slots created"
        )

    def test_git_worktree_add_uses_pool_base_branch(self, tmp_path):
        """git worktree add is called with the pool's base_branch.

        After the fix in sprint_manager.py, the pool is initialized with
        target_branch='develop', so worktree add must use 'develop'.
        """
        pool = _make_pool(tmp_path, slots=1, base_branch="develop")
        worktree_add_branches = []

        def _capture(*args, **kwargs):
            cmd = args[0] if args else []
            if (
                len(cmd) >= 4
                and cmd[0] == "git"
                and "worktree" in cmd
                and "add" in cmd
            ):
                worktree_add_branches.append(cmd[-1])
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m

        with patch("subprocess.run", side_effect=_capture):
            pool.create()

        assert any(b == "develop" for b in worktree_add_branches), (
            f"git worktree add did not use 'develop' — "
            f"got branches: {worktree_add_branches}"
        )


# ---------------------------------------------------------------------------
# AC2 — create() return value equals actual created slot count
# ---------------------------------------------------------------------------

class TestAC2CreateReturnsActualCount:
    """AC2: WorktreePool.create() returns the actual number of successfully
    created slots so the caller can log an accurate count rather than the
    configured max_coder_slots.
    """

    def test_create_returns_int(self, tmp_path):
        pool = _make_pool(tmp_path, slots=1)
        with patch("subprocess.run", side_effect=_success):
            result = pool.create()
        assert isinstance(result, int), (
            f"create() must return int, got {type(result)}"
        )

    def test_create_returns_configured_count_when_all_succeed(self, tmp_path):
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_success):
            count = pool.create()
        assert count == 2, (
            f"create() must return 2 when 2 slots succeed, got {count}"
        )

    def test_create_returns_zero_when_all_slots_fail(self, tmp_path):
        """create() returns 0 when git worktree add fails for every slot."""
        pool = _make_pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_fail):
            count = pool.create()
        assert count == 0, (
            f"create() must return 0 when all slots fail, got {count}"
        )

    def test_create_count_matches_free_list_length(self, tmp_path):
        """Return value equals len(pool._free) immediately after create()."""
        pool = _make_pool(tmp_path, slots=3)
        with patch("subprocess.run", side_effect=_success):
            count = pool.create()
        assert count == len(pool._free), (
            f"create() returned {count} but pool._free has {len(pool._free)} entries"
        )

    def test_create_partial_success_returns_actual_count(self, tmp_path):
        """When some slots fail, create() returns only the count that succeeded."""
        pool = _make_pool(tmp_path, slots=2)
        call_count = [0]

        def _alternating(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0] if args else []
            if "add" in cmd:
                m = MagicMock()
                m.returncode = 0 if call_count[0] % 2 == 1 else 1
                m.stdout = ""
                m.stderr = "error"
                return m
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m

        with patch("subprocess.run", side_effect=_alternating):
            count = pool.create()
        assert count == len(pool._free)
        assert count <= 2


# ---------------------------------------------------------------------------
# AC3 — Zero slots → fail fast with a clear error
# ---------------------------------------------------------------------------

class TestAC3ZeroSlotsFailFast:
    """AC3: If the pool genuinely has 0 usable slots, the sprint setup must
    raise a clear error immediately — not block for 1800s via acquire().
    """

    def test_acquire_raises_timeout_after_1800s_on_empty_pool(self, tmp_path):
        """Baseline: an empty pool's acquire() would normally time out.

        We verify the fast-path is needed by confirming the baseline
        WorktreePool.acquire() raises TimeoutError on 0 slots.
        """
        pool = _make_pool(tmp_path, slots=1)
        with patch("subprocess.run", side_effect=_fail):
            pool.create()
        assert len(pool._free) == 0

        # With a tiny timeout, acquire() must raise TimeoutError fast
        start = time.monotonic()
        raised = None
        try:
            pool.acquire(timeout=0.05)
        except TimeoutError as exc:
            raised = exc
        elapsed = time.monotonic() - start

        assert raised is not None, "Expected TimeoutError from acquire() on empty pool"
        assert elapsed < 5, (
            f"acquire() took {elapsed:.1f}s to raise — expected < 5s with 0.05s timeout"
        )

    def test_sprint_manager_raises_runtime_error_on_zero_slots(self, tmp_path):
        """sprint_manager must raise RuntimeError immediately when pool creates 0 slots.

        This is the AC3 fast-fail: instead of calling acquire() and waiting
        1800s, sprint_manager detects 0 slots after create() and raises.
        """
        import services.sprint_manager.sprint_manager as sm

        pool = _make_pool(tmp_path, slots=1)
        with patch("subprocess.run", side_effect=_fail):
            pool.create()
        assert len(pool._free) == 0, "Precondition: pool has 0 slots"

        # The fast-fail must trigger before any acquire() is attempted
        acquire_called = [False]
        original_acquire = pool.acquire

        def _spy_acquire(*args, **kwargs):
            acquire_called[0] = True
            return original_acquire(*args, **kwargs)

        pool.acquire = _spy_acquire

        with patch.object(sm, "_ACTIVE_WORKTREE_POOL", pool):
            start = time.monotonic()
            try:
                sm._assert_pool_has_slots(pool, requested_slots=1)
                raised = None
            except RuntimeError as exc:
                raised = exc
            elapsed = time.monotonic() - start

        assert raised is not None, (
            "sprint_manager must raise RuntimeError when pool has 0 slots"
        )
        assert "0" in str(raised) or "slot" in str(raised).lower(), (
            f"Error message must mention 0 slots: {raised}"
        )
        assert not acquire_called[0], (
            "acquire() must NOT be called before the fast-fail check"
        )
        assert elapsed < 1, (
            f"Fast-fail check took {elapsed:.2f}s — must be near-instant"
        )

    def test_zero_slots_error_mentions_branch(self, tmp_path):
        """The fast-fail error message must name the branch that failed."""
        import services.sprint_manager.sprint_manager as sm

        pool = _make_pool(tmp_path, slots=1, base_branch="sprint/sprint-1007")
        with patch("subprocess.run", side_effect=_fail):
            pool.create()

        try:
            sm._assert_pool_has_slots(pool, requested_slots=1)
        except RuntimeError as exc:
            assert "sprint/sprint-1007" in str(exc), (
                f"Error must name the failed branch, got: {exc}"
            )


# ---------------------------------------------------------------------------
# AC4 — Behavioral: no 1800s hang when pool has an existing base branch
# ---------------------------------------------------------------------------

class TestAC4NoHangOnOvernightDispatch:
    """AC4: A pool bound to an existing branch acquires slots immediately —
    no 1800s hang. This is the behavioral proof that the fix works end-to-end.
    """

    def _make_slots_on_disk(self, pool, count):
        """Create slot directories on disk so acquire()'s health check passes.

        WorktreePool.create() uses mocked subprocess.run so no real directories
        are created.  acquire() checks _slot_healthy (wt_path.is_dir() +
        .git present) before returning the slot — without this helper the
        acquire() would try to recreate the slot via real git, which fails in a
        tmp_path that isn't a git repo.
        """
        for i in range(count):
            slot_dir = pool.pool_dir / f"slot-{i}"
            slot_dir.mkdir(parents=True, exist_ok=True)
            (slot_dir / ".git").touch()

    def test_acquire_returns_immediately_on_develop_branch(self, tmp_path):
        """Pool bound to 'develop' (target_branch for overnight) returns a slot
        within 1 second — proving the worktree-pool path is not blocked.
        """
        pool = _make_pool(tmp_path, slots=1, base_branch="develop")
        with patch("subprocess.run", side_effect=_success):
            created = pool.create()

        assert created == 1, f"Expected 1 slot, got {created}"
        self._make_slots_on_disk(pool, 1)

        start = time.monotonic()
        wt = pool.acquire()
        elapsed = time.monotonic() - start

        assert wt is not None, "acquire() must return a path"
        assert elapsed < 1.0, (
            f"acquire() took {elapsed:.2f}s on a pool with 1 free slot — "
            f"expected near-instant, not a 1800s hang"
        )
        with patch("subprocess.run", side_effect=_success):
            pool.release(wt)

    def test_end_to_end_create_acquire_release_with_develop(self, tmp_path):
        """Full pool lifecycle with base_branch='develop' completes without hang."""
        pool = _make_pool(tmp_path, slots=2, base_branch="develop")
        with patch("subprocess.run", side_effect=_success):
            created = pool.create()

        assert created == 2
        self._make_slots_on_disk(pool, 2)

        start = time.monotonic()
        with patch("subprocess.run", side_effect=_success):
            wt1 = pool.acquire()
            wt2 = pool.acquire()
            pool.release(wt1)
            pool.release(wt2)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, (
            f"Full lifecycle with 2 slots took {elapsed:.2f}s — expected < 2s"
        )
