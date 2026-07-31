"""Tests for issue #2032: Worktree pool self-heal on missing slot directory.

AC-1: On a missing/corrupt worktree slot the pool recreates/prunes the slot
      automatically and continues, instead of raising RuntimeError.
AC-2: Behavioral test — simulate a missing slot dir → assert the pool
      self-heals (recreates) and the run proceeds; no unhandled RuntimeError.

The tests exercise the real code paths with mocked subprocess boundaries.
``assert "symbol" in src`` / regex-over-source checks are explicitly avoided
per CLAUDE.md issue #1746.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.sprint_manager.worktree_pool import WorktreePool, SLOT_PREFIX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pool(tmp_path: Path, *, slots: int = 1) -> WorktreePool:
    pool_dir = tmp_path / ".commander" / "runtime" / "worktree-pool"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    return WorktreePool(
        pool_dir=pool_dir,
        repo_root=repo_root,
        base_branch="develop",
        slots=slots,
    )


class _GitStateMock:
    """Stateful subprocess.run mock that models git's worktree registration.

    Real git stores a worktree registration in .git/worktrees/<name> even
    after the slot directory is externally deleted.  A bare ``git worktree
    add`` then fails with "already registered at this path".  The fix
    (issue #2032) calls ``git worktree remove --force`` (to deregister) and
    ``git worktree prune`` (to drop stale entries) before ``git worktree
    add``.

    This mock reproduces that stateful failure faithfully so the test is
    behavioral (real code path, mocked boundary) rather than source-regex.
    """

    def __init__(self) -> None:
        # path-string → True when git has an active registration for it
        self._registered: dict[str, bool] = {}
        # Every command list invoked, for post-test assertions
        self.calls: list[list[str]] = []

    def __call__(self, *args, **kwargs):
        cmd = list(args[0]) if args else list(kwargs.get("args", []))
        self.calls.append(cmd)

        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""

        if cmd[:3] == ["git", "worktree", "add"]:
            path_str = cmd[3] if len(cmd) > 3 else ""
            if self._registered.get(path_str):
                # Stale registration — exactly what git would reject
                m.returncode = 128
                m.stderr = (
                    f"fatal: '{path_str}' is already registered as a worktree"
                )
            else:
                # Successful add: register path and materialise the directory
                self._registered[path_str] = True
                p = Path(path_str)
                p.mkdir(parents=True, exist_ok=True)
                # Write a .git file so _slot_healthy() considers it a worktree
                (p / ".git").write_text("gitdir: ../fake\n", encoding="utf-8")

        elif cmd[:4] == ["git", "worktree", "remove", "--force"]:
            path_str = cmd[4] if len(cmd) > 4 else ""
            # Deregister — mirrors what git does for both existing and already-
            # gone directories when --force is supplied
            self._registered.pop(path_str, None)

        elif cmd[:3] == ["git", "worktree", "prune"]:
            # Drop registrations whose directories no longer exist
            stale = [p for p in self._registered if not Path(p).exists()]
            for p in stale:
                self._registered.pop(p, None)

        return m


# ---------------------------------------------------------------------------
# AC-1 + AC-2: self-heal on missing slot directory
# ---------------------------------------------------------------------------

class TestSelfHealMissingSlot:

    def test_acquire_self_heals_after_slot_dir_deleted(self, tmp_path):
        """AC-1 + AC-2: pool recreates a missing slot and returns it without RuntimeError.

        Flow:
          1. Pool created → slot dir exists, git registration active.
          2. External deletion removes the slot dir; git registration stays
             (stale .git/worktrees/ entry).
          3. release() detects missing dir → calls _create_slot().
          4. _create_slot() (fixed) runs ``git worktree remove --force``
             (clears stale registration), then ``git worktree prune``, then
             ``git worktree add`` → directory recreated → slot healthy.
          5. acquire() → _slot_healthy() → True → slot returned, no RuntimeError.
        """
        pool = _pool(tmp_path)
        git = _GitStateMock()

        with patch("subprocess.run", side_effect=git):
            pool.create()
            slot = pool.acquire()

            assert slot.is_dir(), "slot must exist after acquire()"

            # ---- simulate external deletion ----
            shutil.rmtree(slot)
            assert not slot.exists(), "setup: slot dir must be absent"
            assert git._registered.get(str(slot)), (
                "setup: stale git registration must still exist after dir deletion"
            )

            # release() detects missing dir, calls _ensure_slot_ready()
            pool.release(slot)

            # Pool must self-heal — no RuntimeError
            healed = pool.acquire()

        assert healed == slot, "same slot path must be returned after healing"
        assert healed.is_dir(), "healed slot directory must exist on disk"

        # Confirm git worktree add was called at least twice:
        # once for initial create() and once for the self-heal recreation
        add_calls = [
            c for c in git.calls
            if c[:3] == ["git", "worktree", "add"] and len(c) > 3 and c[3] == str(slot)
        ]
        assert len(add_calls) >= 2, (
            "git worktree add must be called at least twice (initial + self-heal); "
            f"calls recorded: {git.calls}"
        )

    def test_self_heal_logs_clearly(self, tmp_path, capsys):
        """AC-1: self-heal emits a clear log message — error is not silently swallowed."""
        pool = _pool(tmp_path)
        git = _GitStateMock()

        with patch("subprocess.run", side_effect=git):
            pool.create()
            slot = pool.acquire()
            shutil.rmtree(slot)
            pool.release(slot)
            pool.acquire()

        out = capsys.readouterr().out
        assert any(kw in out.lower() for kw in ("missing", "recreat")), (
            f"expected a 'missing' or 'recreating' log line; captured output:\n{out}"
        )

    def test_genuine_failure_still_surfaces_runtimeerror(self, tmp_path):
        """AC-1 (genuine-failure path): RuntimeError raised when recreation itself fails.

        The self-heal must not silently swallow errors when git worktree add
        fails consistently even after prune/remove.  This guards against over-
        broad exception suppression introduced alongside the fix.
        """
        pool = _pool(tmp_path)
        add_count: list[int] = [0]

        def side_effect(*args, **kwargs):
            cmd = list(args[0]) if args else list(kwargs.get("args", []))
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""

            if cmd[:3] == ["git", "worktree", "add"]:
                add_count[0] += 1
                if add_count[0] == 1:
                    # First call (pool.create()) succeeds; materialise the slot
                    p = Path(cmd[3])
                    p.mkdir(parents=True, exist_ok=True)
                    (p / ".git").write_text("gitdir: ../fake\n", encoding="utf-8")
                else:
                    # All subsequent recreation attempts fail unrecoverably
                    m.returncode = 128
                    m.stderr = "fatal: unrecoverable"
                    # Ensure the directory does not exist (recreation truly failed)
                    p = Path(cmd[3])
                    if p.exists():
                        shutil.rmtree(p, ignore_errors=True)
            return m

        with patch("subprocess.run", side_effect=side_effect):
            pool.create()
            slot = pool.acquire()

            # Simulate external deletion
            shutil.rmtree(slot)
            pool.release(slot)  # tries to recreate — will fail silently in finally

            # acquire() should surface an error because recreation keeps failing
            with pytest.raises(RuntimeError, match="missing and could not be recreated"):
                pool.acquire()
