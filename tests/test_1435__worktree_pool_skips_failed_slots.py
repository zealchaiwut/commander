"""Tests for issue #1435: Worktree pool enqueues failed slots into the free pool.

Root cause (worktree_pool.py): in ``create()``, a slot's path was enqueued into
the free pool whenever the slot directory merely existed, even though
``_create_slot()`` only emitted a warning and returned on a failed
``git worktree add``, ``venv`` create, or ``pip install``. A broken/half-built
slot could therefore be handed to a coder by ``acquire()``, pointing the coder
subprocess ``cwd`` at a missing or unusable directory.

Fix: ``_create_slot()`` returns a success flag and ``create()`` appends the path
to the free pool only when that flag is truthy.

AC items verified:
  AC-1  _create_slot() returns a boolean success flag (True on full success;
        False when worktree add / venv / pip install fails).
  AC-2  In create(), a slot's path is enqueued only when _create_slot() == True.
  AC-3  A failed `git worktree add` slot is absent from the free pool.
  AC-4  A failed venv create OR pip install slot is absent from the free pool.
  AC-5  A failed slot logs a warning and create() keeps building remaining slots.
  AC-6  acquire() never returns a path whose directory does not exist on disk.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from services.sprint_manager.worktree_pool import WorktreePool, SLOT_PREFIX


# ---------------------------------------------------------------------------
# subprocess.run stubs
# ---------------------------------------------------------------------------

def _result(returncode: int, stderr: str = ""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = ""
    m.stderr = stderr
    return m


def _all_success(*_args, **_kwargs):
    return _result(0)


def _make_selective_failure(fail_predicate):
    """Return a subprocess.run side_effect that fails when *fail_predicate(cmd)*.

    The predicate receives the command list (first positional arg). When it
    returns True the call is reported as a non-zero exit; otherwise success.
    Successful ``git worktree add`` calls also create the slot directory so the
    on-disk reality matches what real git would do (AC-6 needs a real dir).
    """
    def _side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if fail_predicate(cmd):
            return _result(1, stderr="simulated failure")
        # Mirror a real `git worktree add <path> <branch>` by making the dir.
        if cmd[:3] == ["git", "worktree", "add"]:
            Path(cmd[3]).mkdir(parents=True, exist_ok=True)
        return _result(0)
    return _side_effect


def _pool(tmp_path, *, slots: int, with_requirements: bool = False) -> WorktreePool:
    pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    req_file = None
    if with_requirements:
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("pytest\n")
    return WorktreePool(
        pool_dir=pool_dir,
        repo_root=repo_root,
        base_branch="sprint/sprint-1",
        slots=slots,
        requirements_file=req_file,
    )


# ---------------------------------------------------------------------------
# AC-1: _create_slot() returns a boolean success flag
# ---------------------------------------------------------------------------

class TestCreateSlotReturnsFlag:
    def test_returns_true_on_full_success(self, tmp_path):
        """AC-1: full success (add + venv + pip) returns True."""
        pool = _pool(tmp_path, slots=1, with_requirements=True)
        pool.pool_dir.mkdir(parents=True, exist_ok=True)
        wt = pool.pool_dir / f"{SLOT_PREFIX}0"
        with patch("subprocess.run", side_effect=_all_success):
            assert pool._create_slot(wt) is True

    def test_returns_false_on_worktree_add_failure(self, tmp_path):
        """AC-1: failed `git worktree add` returns False."""
        pool = _pool(tmp_path, slots=1)
        pool.pool_dir.mkdir(parents=True, exist_ok=True)
        wt = pool.pool_dir / f"{SLOT_PREFIX}0"
        fail = _make_selective_failure(
            lambda cmd: cmd[:3] == ["git", "worktree", "add"]
        )
        with patch("subprocess.run", side_effect=fail):
            assert pool._create_slot(wt) is False

    def test_returns_false_on_venv_failure(self, tmp_path):
        """AC-1: failed venv create returns False."""
        pool = _pool(tmp_path, slots=1)
        pool.pool_dir.mkdir(parents=True, exist_ok=True)
        wt = pool.pool_dir / f"{SLOT_PREFIX}0"
        fail = _make_selective_failure(
            lambda cmd: "-m" in cmd and "venv" in cmd
        )
        with patch("subprocess.run", side_effect=fail):
            assert pool._create_slot(wt) is False

    def test_returns_false_on_pip_install_failure(self, tmp_path):
        """AC-1: failed pip install returns False."""
        pool = _pool(tmp_path, slots=1, with_requirements=True)
        pool.pool_dir.mkdir(parents=True, exist_ok=True)
        wt = pool.pool_dir / f"{SLOT_PREFIX}0"
        fail = _make_selective_failure(
            lambda cmd: "pip" in str(cmd) and "install" in cmd
        )
        with patch("subprocess.run", side_effect=fail):
            assert pool._create_slot(wt) is False


# ---------------------------------------------------------------------------
# AC-2: create() enqueues only successful slots
# ---------------------------------------------------------------------------

class TestCreateGatesOnFlag:
    def test_all_success_enqueues_every_slot(self, tmp_path):
        """AC-2: every successful slot is enqueued into the free pool."""
        pool = _pool(tmp_path, slots=2)
        with patch("subprocess.run", side_effect=_all_success):
            pool.create()
        assert len(pool._free) == 2

    def test_append_is_gated_on_create_slot_return_value(self, tmp_path):
        """AC-2: when _create_slot() returns False, nothing is enqueued."""
        pool = _pool(tmp_path, slots=2)
        with patch.object(pool, "_create_slot", return_value=False) as mock_create:
            pool.create()
        assert mock_create.call_count == 2
        assert pool._free == []


# ---------------------------------------------------------------------------
# AC-3: failed `git worktree add` slot absent from the free pool
# ---------------------------------------------------------------------------

class TestWorktreeAddFailureExcluded:
    def test_failed_worktree_add_path_not_in_pool(self, tmp_path):
        """AC-3: slot whose `git worktree add` failed is not in the free pool."""
        pool = _pool(tmp_path, slots=2)
        failed = pool.pool_dir / f"{SLOT_PREFIX}0"
        fail = _make_selective_failure(
            lambda cmd: cmd[:3] == ["git", "worktree", "add"]
            and cmd[3] == str(failed)
        )
        with patch("subprocess.run", side_effect=fail):
            pool.create()
        assert failed not in pool._free


# ---------------------------------------------------------------------------
# AC-4: failed venv create / pip install slot absent from the free pool
# ---------------------------------------------------------------------------

class TestVenvOrPipFailureExcluded:
    def test_failed_venv_path_not_in_pool(self, tmp_path):
        """AC-4: slot whose venv create failed is not in the free pool."""
        pool = _pool(tmp_path, slots=2)
        failed = pool.pool_dir / f"{SLOT_PREFIX}1"

        def predicate(cmd):
            return (
                "-m" in cmd
                and "venv" in cmd
                and str(failed / "venv") in cmd
            )

        with patch("subprocess.run", side_effect=_make_selective_failure(predicate)):
            pool.create()
        assert failed not in pool._free
        assert (pool.pool_dir / f"{SLOT_PREFIX}0") in pool._free

    def test_failed_pip_install_path_not_in_pool(self, tmp_path):
        """AC-4: slot whose pip install failed is not in the free pool.

        Note: the slot directory DOES exist on disk (git worktree add
        succeeded), so the old `is_dir()` gate would have wrongly enqueued it.
        Only the success flag keeps it out.
        """
        pool = _pool(tmp_path, slots=2, with_requirements=True)
        failed = pool.pool_dir / f"{SLOT_PREFIX}0"

        def predicate(cmd):
            return (
                "pip" in str(cmd)
                and "install" in cmd
                and str(failed) in str(cmd)
            )

        with patch("subprocess.run", side_effect=_make_selective_failure(predicate)):
            pool.create()
        # Directory exists on disk yet the slot must be excluded.
        assert failed.is_dir()
        assert failed not in pool._free


# ---------------------------------------------------------------------------
# AC-5: failed slot warns and create() continues with remaining slots
# ---------------------------------------------------------------------------

class TestFailedSlotWarnsAndContinues:
    def test_one_failure_one_success_pool_has_only_success(self, tmp_path, capsys):
        """AC-5: mix of one failing and one succeeding slot -> exactly the good one."""
        pool = _pool(tmp_path, slots=2)
        failed = pool.pool_dir / f"{SLOT_PREFIX}0"
        good = pool.pool_dir / f"{SLOT_PREFIX}1"
        fail = _make_selective_failure(
            lambda cmd: cmd[:3] == ["git", "worktree", "add"]
            and cmd[3] == str(failed)
        )
        with patch("subprocess.run", side_effect=fail):
            pool.create()

        assert pool._free == [good]
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "1 slot(s)" in out


# ---------------------------------------------------------------------------
# AC-6: acquire() never returns a path whose directory does not exist
# ---------------------------------------------------------------------------

class TestAcquireReturnsExistingDir:
    def test_acquire_returns_existing_directory(self, tmp_path):
        """AC-6: a pool populated only with valid slots hands back a real dir.

        The failing slot's `git worktree add` errors, so it is excluded from the
        pool; acquire() then returns only the surviving good slot, whose
        directory (and `.git` marker) exists on disk.
        """
        pool = _pool(tmp_path, slots=2)
        failed = pool.pool_dir / f"{SLOT_PREFIX}0"

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd[:3] == ["git", "worktree", "add"]:
                if cmd[3] == str(failed):
                    return _result(1, stderr="simulated failure")
                wt = Path(cmd[3])
                wt.mkdir(parents=True, exist_ok=True)
                (wt / ".git").write_text("gitdir: fake\n", encoding="utf-8")
            return _result(0)

        with patch("subprocess.run", side_effect=side_effect):
            pool.create()
            assert failed not in pool._free
            acquired = pool.acquire()

        assert acquired.is_dir()
        assert acquired != failed
