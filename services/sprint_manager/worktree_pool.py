"""Warm git worktree pool for concurrent coder dispatch (issue #1411).

Creates K isolated git worktrees under .commander/runtime/worktree-pool/.
Each slot is linked to a PERSISTENT shared virtualenv (keyed by the
requirements.txt hash, stored under .commander/runtime/venv-cache/) via a
symlink — built once and reused across every sprint run and slot. This removes
the per-run `python -m venv` + `pip install` that previously cost 2-3 minutes on
every Run before the first coder dispatch. Coders acquire a slot before dispatch
and release it after. The pool's worktrees are torn down at sprint end; the
shared venv-cache is kept for the next run. On startup, orphaned worktrees are
reconciled before new dispatch.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

MAX_SLOTS = 4
DEFAULT_SLOTS = 2
SLOT_PREFIX = "slot-"
# acquire() waits for a FREE SLOT, not for a coder's own run — a slot should
# free up quickly under normal operation. If none frees within this window,
# something is genuinely stuck (e.g. release() lost a slot) and acquire()
# raises instead of blocking forever (issue: sprint-102 hung 3+ hours with
# zero children, zero CPU, and no error — a leaked slot from a release()
# failure left every subsequent acquire() waiting on a condition nothing would
# ever notify).
ACQUIRE_TIMEOUT_SECONDS = 1800


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
        # Persistent shared-venv cache, OUTSIDE pool_dir so it survives teardown
        # (which rmtrees pool_dir). Keyed by requirements hash; built once.
        self._venv_cache = pool_dir.parent / "venv-cache"

        self._free: list[Path] = []
        self._in_use: set[Path] = set()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    def _req_hash(self) -> str:
        """Short hash identifying the requirements set the shared venv is built for."""
        if not (self.requirements_file and self.requirements_file.exists()):
            return "norequirements"
        try:
            return hashlib.sha256(self.requirements_file.read_bytes()).hexdigest()[:16]
        except OSError:
            return "norequirements"

    def _shared_venv(self) -> Path:
        """Path to the persistent shared venv for the current requirements hash."""
        return self._venv_cache / self._req_hash() / "venv"

    def _ensure_shared_venv(self) -> Optional[Path]:
        """Build the persistent shared venv ONCE per requirements hash; reuse after.

        This is the fix for the 2-3 min Run→dispatch latency: previously every Run
        rebuilt a per-slot venv + `pip install`. Now the venv is built once, lives
        outside the worktrees, and is reused across runs and slots. Returns the
        venv path, or None on failure (slots then run without a venv, as before
        for the no-requirements case).
        """
        venv_path = self._shared_venv()
        if (venv_path / "bin" / "python").exists():
            return venv_path  # already built for this requirements hash — reuse
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        ok, _, err = _run(
            [sys.executable, "-m", "venv", str(venv_path)],
            cwd=self._venv_cache, timeout=300,
        )
        if not ok:
            sys.stdout.write(
                f"  [worktree-pool] WARNING: shared venv create failed: {err}\n"
            )
            return None
        if self.requirements_file and self.requirements_file.exists():
            pip = venv_path / "bin" / "pip"
            ok, _, err = _run(
                [str(pip), "install", "-r", str(self.requirements_file), "-q"],
                cwd=self._venv_cache, timeout=600,
            )
            if not ok:
                sys.stdout.write(
                    f"  [worktree-pool] WARNING: shared venv pip install failed: {err}\n"
                )
        sys.stdout.write(
            f"  [worktree-pool] Shared venv ready ({self._req_hash()}) — reused across runs\n"
        )
        return venv_path

    def _link_venv(self, wt_path: Path) -> None:
        """(Re)create the slot's `venv` symlink to the persistent shared venv.

        Called on slot creation and after each release (git clean removes the
        untracked symlink). A symlink — not a copy — keeps the venv's real prefix
        valid. No-op (with a warning) if the shared venv isn't available.
        """
        shared = self._shared_venv()
        slot_venv = wt_path / "venv"
        if not (shared / "bin" / "python").exists():
            sys.stdout.write(
                f"  [worktree-pool] WARNING: shared venv unavailable; slot"
                f" {wt_path.name} has no venv\n"
            )
            return
        try:
            if slot_venv.is_symlink():
                if slot_venv.resolve() == shared.resolve():
                    return  # already linked correctly
                slot_venv.unlink()
            elif slot_venv.exists():
                shutil.rmtree(slot_venv, ignore_errors=True)
            slot_venv.symlink_to(shared, target_is_directory=True)
        except OSError as e:
            sys.stdout.write(
                f"  [worktree-pool] WARNING: venv symlink failed for {wt_path}: {e}\n"
            )

    def create(self) -> int:
        """Create all K worktrees, each symlinked to the persistent shared venv.

        Returns the number of slots actually created so callers can log an
        accurate count and detect the 0-slot fast-fail case (issue #2081).
        """
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        # Build the shared venv once (slow only the first time / on requirements change).
        self._ensure_shared_venv()
        created: list[Path] = []
        for i in range(self.slots):
            wt_path = self.pool_dir / f"{SLOT_PREFIX}{i}"
            # Enqueue a slot only when it was fully built (worktree + venv +
            # pip install all succeeded). A slot that failed partway emits a
            # warning inside _create_slot() and is skipped, so acquire() can
            # never hand a coder a broken/missing worktree path (issue #1435).
            if self._create_slot(wt_path):
                created.append(wt_path)
        with self._cond:
            self._free = created[:]
        sys.stdout.write(
            f"  [worktree-pool] Pool ready: {len(created)} slot(s) under {self.pool_dir}\n"
        )
        return len(created)

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

    def acquire(self, timeout: float = ACQUIRE_TIMEOUT_SECONDS) -> Path:
        """Block until a free slot is available, mark it in-use, and return it.

        Raises TimeoutError if no slot frees up within `timeout` seconds — this
        is a bounded wait for an existing slot to be RELEASED, not for a
        coder's own run, so it should normally return almost immediately.
        A silent infinite wait here previously manifested as a sprint hanging
        for hours with zero CPU and zero child processes and no operator-visible
        error (a slot leaked by a failing release() is never freed again).
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._free:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"worktree pool: no free slot within {timeout}s "
                        f"({len(self._in_use)} slot(s) stuck in-use) — a prior "
                        f"release() likely failed to return its slot"
                    )
                self._cond.wait(timeout=remaining)
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
        """Reset worktree to clean base state and return it to the free pool.

        The slot-return (the `with self._cond` block) runs in `finally` so an
        unexpected exception anywhere in the cleanup can NEVER leak a slot out
        of circulation — a lost slot means every subsequent acquire() for that
        slot count blocks, and previously had no timeout to surface it.
        """
        try:
            if worktree.is_dir():
                # Preserve the venv symlink across the clean (-e venv); re-link
                # defensively in case it was removed, so the next acquire keeps
                # the shared venv (no rebuild).
                _run(["git", "clean", "-fdx", "-e", "venv"], cwd=worktree)
                _run(["git", "checkout", self.base_branch], cwd=worktree)
                self._link_venv(worktree)
            else:
                sys.stdout.write(
                    f"  [worktree-pool] WARNING: slot {worktree.name} missing on release"
                    f" — recreating\n"
                )
                self._ensure_slot_ready(worktree)
        finally:
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
            self._link_venv(wt_path)  # cheap: ensure the shared-venv symlink is present
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

    def _create_slot(self, wt_path: Path) -> bool:
        """Create one worktree slot with a fresh virtualenv.

        Returns True only when the worktree, its venv, and (when a
        requirements file is configured) the pip install all complete
        successfully. Any failed step emits a warning and returns False so the
        caller can keep the broken/half-built path out of the free pool
        (issue #1435).

        Self-heal (issue #2032): the remove is unconditional — NOT gated on
        wt_path.exists() — because git keeps a registration entry in
        .git/worktrees/<name> even after the directory is deleted externally.
        Without clearing that entry first, `git worktree add` fails with
        "already registered".  Errors from remove/prune are benign (the path
        may not be registered at all) and are intentionally ignored.
        """
        # Always attempt removal first — clears git's stale worktree
        # registration even when the directory is already gone.
        _run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=self.repo_root,
        )
        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
        # Belt-and-suspenders: prune drops any remaining stale metadata for
        # directories that no longer exist (e.g. never-removed entry).
        _run(["git", "worktree", "prune"], cwd=self.repo_root)

        ok, _, err = _run(
            ["git", "worktree", "add", str(wt_path), self.base_branch],
            cwd=self.repo_root,
        )
        if not ok:
            sys.stdout.write(
                f"  [worktree-pool] WARNING: failed to create worktree"
                f" {wt_path}: {err}\n"
            )
            return False

        # Link the slot to the PERSISTENT shared venv (built once per requirements
        # hash, outside the worktree). Replaces the per-slot `python -m venv` +
        # `pip install` that cost 2-3 min on every Run.
        self._link_venv(wt_path)

        sys.stdout.write(f"  [worktree-pool] Created {wt_path.name}\n")
        return True

    def _remove_worktree(self, wt_path: Path) -> None:
        """Remove a single worktree path unconditionally."""
        ok, _, _ = _run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=self.repo_root,
        )
        if not ok and wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
