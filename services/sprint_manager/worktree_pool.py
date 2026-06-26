"""Warm git worktree pool for concurrent coder dispatch (issue #1411).

Creates K isolated git worktrees under .commander/runtime/worktree-pool/,
each with its own fresh virtualenv. Coders acquire a slot before dispatch
and release it after completion. The pool is torn down at sprint end.
On startup, orphaned worktrees are reconciled before new dispatch.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

MAX_SLOTS = 4
DEFAULT_SLOTS = 2
SLOT_PREFIX = "slot-"


def _run(
    args: list,
    *,
    cwd: Path,
    timeout: int = 300,
) -> tuple[bool, str, str]:
    """Run a subprocess in *cwd* and return (success, stdout, stderr)."""
    try:
        r = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as exc:
        return False, "", str(exc)


class WorktreePool:
    """Pool of K isolated git worktrees for concurrent coder dispatch.

    Lifecycle:
        pool = WorktreePool(pool_dir, repo_root, base_branch, slots)
        pool.create()          # sprint start
        wt = pool.acquire()    # before dispatch
        ...dispatch coder...
        pool.release(wt)       # after dispatch
        pool.teardown()        # sprint end

    Class method:
        WorktreePool.reconcile_orphans(pool_dir, repo_root)  # on startup
    """

    def __init__(
        self,
        pool_dir: Path,
        repo_root: Path,
        base_branch: str,
        slots: int,
        requirements_file: Optional[Path] = None,
    ) -> None:
        if slots > MAX_SLOTS:
            sys.stdout.write(
                f"  [worktree-pool] max_coder_slots={slots} exceeds hard cap {MAX_SLOTS};"
                f" clamping to {MAX_SLOTS}\n"
            )
            slots = MAX_SLOTS
        self.pool_dir = pool_dir
        self.repo_root = repo_root
        self.base_branch = base_branch
        self.slots = slots
        self.requirements_file = requirements_file

        self._free: list[Path] = []
        self._in_use: set[Path] = set()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    def create(self) -> None:
        """Create all K worktrees and their virtualenvs."""
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        for i in range(self.slots):
            wt_path = self.pool_dir / f"{SLOT_PREFIX}{i}"
            self._create_slot(wt_path)
            if wt_path.is_dir():
                created.append(wt_path)
        with self._cond:
            self._free = created[:]
        sys.stdout.write(
            f"  [worktree-pool] Pool ready: {len(created)} slot(s) under {self.pool_dir}\n"
        )

    def teardown(self) -> None:
        """Remove all worktrees (free and in-use) and clean the pool dir."""
        with self._lock:
            all_paths = list(self._free) + list(self._in_use)

        for wt_path in all_paths:
            self._remove_worktree(wt_path)

        _run(["git", "worktree", "prune"], cwd=self.repo_root)

        if self.pool_dir.exists():
            shutil.rmtree(self.pool_dir, ignore_errors=True)

        sys.stdout.write("  [worktree-pool] Pool torn down.\n")

    # ------------------------------------------------------------------
    # Slot management
    # ------------------------------------------------------------------

    def acquire(self) -> Path:
        """Block until a free slot is available, mark it in-use, and return it."""
        with self._cond:
            while not self._free:
                self._cond.wait()
            wt = self._free.pop(0)
            self._in_use.add(wt)
        if not self._ensure_slot_ready(wt):
            with self._cond:
                self._in_use.discard(wt)
                if wt not in self._free:
                    self._free.append(wt)
                self._cond.notify_all()
            raise RuntimeError(
                f"worktree pool slot {wt.name} is missing and could not be recreated"
            )
        return wt

    def release(self, worktree: Path) -> None:
        """Reset worktree to clean base state and return it to the free pool."""
        if worktree.is_dir():
            _run(["git", "clean", "-fdx"], cwd=worktree)
            _run(["git", "checkout", self.base_branch], cwd=worktree)
        else:
            sys.stdout.write(
                f"  [worktree-pool] WARNING: slot {worktree.name} missing on release"
                f" — recreating\n"
            )
            self._ensure_slot_ready(worktree)
        with self._cond:
            self._in_use.discard(worktree)
            if worktree not in self._free:
                self._free.append(worktree)
            self._cond.notify_all()

    # ------------------------------------------------------------------
    # Startup reconciliation
    # ------------------------------------------------------------------

    @staticmethod
    def reconcile_orphans(pool_dir: Path, repo_root: Path) -> None:
        """Detect and prune orphaned worktrees left by a prior crash.

        Safe to call even if pool_dir does not exist.
        """
        if not pool_dir.exists():
            return

        orphans = sorted(
            p for p in pool_dir.iterdir()
            if p.is_dir() and p.name.startswith(SLOT_PREFIX)
        )
        if not orphans:
            return

        sys.stdout.write(
            f"  [worktree-pool] Reconciling {len(orphans)} orphaned"
            f" worktree(s) from prior crash...\n"
        )
        for wt_path in orphans:
            ok, _, _ = _run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=repo_root,
            )
            if not ok and wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)
            sys.stdout.write(f"  [worktree-pool]   Removed orphan {wt_path.name}\n")

        _run(["git", "worktree", "prune"], cwd=repo_root)
        sys.stdout.write("  [worktree-pool] Orphan reconciliation complete.\n")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _slot_healthy(self, wt_path: Path) -> bool:
        """True when the slot directory looks like a usable git worktree."""
        return wt_path.is_dir() and (wt_path / ".git").exists()

    def _ensure_slot_ready(self, wt_path: Path) -> bool:
        """Recreate a pool slot when its directory was removed mid-sprint.

        Fix-loop retries reuse the same slot path; a coder agent (or a failed
        ``git worktree remove``) can delete the directory between release and
        the next acquire. Without recreation, hygiene crashes on ``git fetch``.
        """
        if self._slot_healthy(wt_path):
            return True
        sys.stdout.write(
            f"  [worktree-pool] Recreating missing slot {wt_path.name}...\n"
        )
        self._create_slot(wt_path)
        if not self._slot_healthy(wt_path):
            sys.stdout.write(
                f"  [worktree-pool] ERROR: could not recreate slot {wt_path.name}\n"
            )
            return False
        return True

    def _create_slot(self, wt_path: Path) -> None:
        """Create one worktree slot with a fresh virtualenv."""
        if wt_path.exists():
            _run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=self.repo_root,
            )
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)

        ok, _, err = _run(
            ["git", "worktree", "add", str(wt_path), self.base_branch],
            cwd=self.repo_root,
        )
        if not ok:
            sys.stdout.write(
                f"  [worktree-pool] WARNING: failed to create worktree"
                f" {wt_path}: {err}\n"
            )
            return

        # Create fresh venv — never copy or reuse another slot's venv.
        venv_path = wt_path / "venv"
        ok, _, err = _run(
            [sys.executable, "-m", "venv", str(venv_path)],
            cwd=wt_path,
        )
        if not ok:
            sys.stdout.write(
                f"  [worktree-pool] WARNING: venv create failed for"
                f" {wt_path}: {err}\n"
            )
            return

        if self.requirements_file and self.requirements_file.exists():
            pip = venv_path / "bin" / "pip"
            ok, _, err = _run(
                [str(pip), "install", "-r", str(self.requirements_file), "-q"],
                cwd=wt_path,
            )
            if not ok:
                sys.stdout.write(
                    f"  [worktree-pool] WARNING: pip install failed for"
                    f" {wt_path}: {err}\n"
                )

        sys.stdout.write(f"  [worktree-pool] Created {wt_path.name}\n")

    def _remove_worktree(self, wt_path: Path) -> None:
        """Remove a single worktree path unconditionally."""
        ok, _, _ = _run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=self.repo_root,
        )
        if not ok and wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
