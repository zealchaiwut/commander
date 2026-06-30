"""Tests for issue #1438: concurrent test tasks share a single tester worktree.

Context: role-flexible slots (#1413) allow two `test_fn` tasks to run
concurrently, but `_tester_stage` -> `_dispatch_tester` operate in the single
shared `cfg.worktree_tester` clone — there is no tester worktree pool. Concurrent
checkout / rebase / pytest in one worktree can collide (git index.lock, wrong
branch checked out, cross-contaminated results).

The chosen fix is the issue's suggested fallback: constrain the scheduler to one
active tester task at a time via a process-local serialization guard
(`tester_worktree_guard`), mirroring the existing `develop_merge_guard` (#738).

Each test is anchored to a specific acceptance criterion (AC) from the issue:

AC1  Concurrent `test_fn` tasks each run in isolation (here: only one test task
     runs at a time), preventing index.lock errors and branch collisions.
AC2  No concurrent tester task can check out / rebase in a worktree another
     active tester is using — the guard provides strict mutual exclusion.
AC3  Test results from one tester slot cannot contaminate another — each tester
     completes (records its own result) before the next begins.
AC4  The chosen fix is the concurrency constraint, not a worktree pool, so the
     pool-initialisation precondition is N/A; the constraint is a serialization
     lock, and the production tester stage acquires it.
AC5  The second test task queues and starts only after the first completes,
     without deadlocking or dropping the task.
AC6  `develop_merge_guard` serialization of the merge step continues to function
     and is not broken by the new tester-worktree isolation (nests safely).
AC7  A sprint with role-flexible slots completes all ticket test runs and merges
     with no errors under the constraint.
"""
from __future__ import annotations

import inspect
import threading
import time

import pytest

from services.sprint_manager.pipeline import StageResult
from services.sprint_manager.concurrent_scheduler import run_concurrent_level
# Imported under a non-``test``-prefixed alias so pytest does not mis-collect the
# context manager itself as a test function.
from services.sprint_manager.serialization import (
    develop_merge_guard,
    tester_worktree_guard as wt_guard,
)


# ── instrumentation ───────────────────────────────────────────────────────────

class _TesterTracker:
    """Records peak concurrency of tester tasks and per-ticket result scope.

    Each tester wraps its body in the production `tester_worktree_guard`. If the
    guard fails to serialize, `max_concurrent_test` will exceed 1 and the test
    fails. `results` records the ticket each tester observed while inside the
    guard, so an interleave (contamination) would be visible.
    """

    def __init__(self, work_secs: float = 0.0):
        self.work_secs = work_secs
        self._lock = threading.Lock()
        self.active_test = 0
        self.max_concurrent_test = 0
        self.results: list = []

    def test(self, ticket, attempt):
        with wt_guard():
            with self._lock:
                self.active_test += 1
                self.max_concurrent_test = max(
                    self.max_concurrent_test, self.active_test
                )
                # The ticket whose worktree we "own" right now. If another tester
                # were concurrently active, the snapshot below would show it.
                snapshot_active = self.active_test
            time.sleep(self.work_secs)
            with self._lock:
                self.results.append((ticket, snapshot_active))
                self.active_test -= 1
        return StageResult.PASS


def _pass_code(ticket, attempt):
    return StageResult.PASS


# ── AC1: concurrent test tasks run isolated (one at a time) ───────────────────

def test_ac1_concurrent_testers_never_overlap():
    """With multiple slots and several test tasks, at most one tester is active.

    Prevents the shared-worktree index.lock / branch-collision class of bug.
    """
    tracker = _TesterTracker(work_secs=0.02)
    tickets = [1, 2, 3, 4]
    file_map = {t: {f"file_{t}.py"} for t in tickets}

    res = run_concurrent_level(
        tickets, _pass_code, tracker.test,
        max_coder_slots=4,
        file_map=file_map,
    )

    assert sorted(res.merged) == tickets
    assert tracker.max_concurrent_test == 1, (
        "two tester tasks ran concurrently in the shared worktree"
    )


# ── AC2: strict mutual exclusion of the tester worktree guard ─────────────────

def test_ac2_guard_provides_strict_mutual_exclusion():
    """Direct stress of the primitive: never two holders inside the guard."""
    active = 0
    peak = 0
    state_lock = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        nonlocal active, peak
        start.wait()
        for _ in range(50):
            with wt_guard():
                with state_lock:
                    active += 1
                    peak = max(peak, active)
                # brief critical-section work to widen the race window
                time.sleep(0.0005)
                with state_lock:
                    active -= 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1


# ── AC3: results are not cross-contaminated between slots ─────────────────────

def test_ac3_each_tester_result_scoped_to_own_ticket():
    """Every tester observes exactly itself active — no overlap → no contamination."""
    tracker = _TesterTracker(work_secs=0.01)
    tickets = [10, 20, 30]
    file_map = {t: {f"f{t}.py"} for t in tickets}

    res = run_concurrent_level(
        tickets, _pass_code, tracker.test,
        max_coder_slots=3,
        file_map=file_map,
    )

    assert sorted(res.merged) == tickets
    # Each result was recorded while exactly one tester (itself) was active.
    assert all(active == 1 for _ticket, active in tracker.results)
    assert sorted(t for t, _ in tracker.results) == tickets


# ── AC4: chosen fix is the concurrency constraint, not a worktree pool ────────

def test_ac4_fix_is_concurrency_constraint_serialization_lock():
    """The guard is a serialization primitive (constraint approach).

    Because the constraint approach is chosen, the worktree-pool-initialisation
    precondition (AC4's "if the chosen fix is a worktree pool") does not apply.
    Verify the primitive exists and behaves as a non-reentrant mutual-exclusion
    guard rather than a per-slot pool.
    """
    # It is a context manager.
    cm = wt_guard()
    assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__")

    # It is non-reentrant: a second acquire on the same thread would block, so we
    # confirm a separate thread cannot enter while the main thread holds it.
    entered = threading.Event()
    second_blocked = threading.Event()

    def contender():
        # Should not be able to enter until the main thread releases.
        acquired_quickly = [False]

        def try_enter():
            with wt_guard():
                acquired_quickly[0] = True

        th = threading.Thread(target=try_enter)
        with wt_guard():
            th.start()
            time.sleep(0.05)
            # While we hold the guard, the contender must NOT have entered.
            second_blocked.set()
            assert acquired_quickly[0] is False
        th.join()
        entered.set()

    t = threading.Thread(target=contender)
    t.start()
    t.join(timeout=2)
    assert second_blocked.is_set()
    assert entered.is_set()


# ── AC4 / AC1 wiring: the production tester stage acquires the guard ──────────

def test_production_tester_stage_uses_guard():
    """`_tester_stage` in pipeline.py wraps its worktree work in the guard.

    The guard is useless unless the production concurrent test path actually
    acquires it around the dispatch/gate/merge span.
    """
    from services.sprint_manager import pipeline

    src = inspect.getsource(pipeline)
    # The guard is imported into the production module.
    assert "tester_worktree_guard" in src

    # Locate the `_tester_stage` function body and confirm the guard wraps the
    # tester dispatch + post-tester (merge) span.
    start = src.index("def _tester_stage(")
    end = src.index("\n    def ", start + 1) if "\n    def " in src[start + 1:] else len(src)
    # extend `end` properly: find the next top-level-in-closure def after start
    nxt = src.find("\n    def ", start + 1)
    body = src[start: nxt if nxt != -1 else len(src)]
    assert "tester_worktree_guard()" in body, (
        "_tester_stage must acquire tester_worktree_guard around its worktree work"
    )
    g = body.index("tester_worktree_guard()")
    d = body.index("_dispatch_tester(")
    h = body.index("handle_post_tester(")
    assert g < d < h, (
        "guard must be entered before _dispatch_tester and handle_post_tester"
    )


# ── AC5: second test task queues, starts after first; no drop / deadlock ──────

def test_ac5_second_test_task_queues_no_drop_no_deadlock():
    """All test tasks complete (none dropped) even though they serialize."""
    order: list = []
    order_lock = threading.Lock()

    def test(ticket, attempt):
        with wt_guard():
            with order_lock:
                order.append(("enter", ticket))
            time.sleep(0.01)
            with order_lock:
                order.append(("exit", ticket))
        return StageResult.PASS

    tickets = [1, 2, 3]
    file_map = {t: {f"x{t}.py"} for t in tickets}

    # The whole level must finish (no deadlock) and merge every ticket (no drop).
    res = run_concurrent_level(
        tickets, _pass_code, test,
        max_coder_slots=3,
        file_map=file_map,
    )

    assert sorted(res.merged) == tickets
    assert res.dropped == []
    # Serialized: each enter is immediately followed by its own exit (no
    # interleaving enter/enter), proving one-at-a-time scheduling.
    for i in range(0, len(order), 2):
        assert order[i][0] == "enter"
        assert order[i + 1] == ("exit", order[i][1])


# ── AC6: develop_merge_guard still serializes and nests safely ────────────────

def test_ac6_merge_guard_nested_inside_tester_guard_no_deadlock():
    """tester_worktree_guard (outer) + develop_merge_guard (inner) never deadlock.

    This mirrors the production order: handle_post_tester acquires
    develop_merge_guard while the tester stage already holds the worktree guard.
    """
    merge_active = 0
    merge_peak = 0
    m_lock = threading.Lock()

    def tester(ticket, attempt):
        with wt_guard():
            # ... worktree work ...
            with develop_merge_guard():  # the production merge step
                nonlocal merge_active, merge_peak
                with m_lock:
                    merge_active += 1
                    merge_peak = max(merge_peak, merge_active)
                time.sleep(0.005)
                with m_lock:
                    merge_active -= 1
        return StageResult.PASS

    tickets = [1, 2, 3, 4]
    file_map = {t: {f"m{t}.py"} for t in tickets}

    res = run_concurrent_level(
        tickets, _pass_code, tester,
        max_coder_slots=4,
        file_map=file_map,
    )

    assert sorted(res.merged) == tickets
    # develop_merge_guard still serializes merges (never two at once).
    assert merge_peak == 1


def test_ac6_develop_merge_guard_unchanged_standalone():
    """develop_merge_guard alone still serializes concurrent merges (no regression)."""
    active = 0
    peak = 0
    lock = threading.Lock()
    start = threading.Barrier(6)

    def merge():
        nonlocal active, peak
        start.wait()
        for _ in range(30):
            with develop_merge_guard():
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.0003)
                with lock:
                    active -= 1

    threads = [threading.Thread(target=merge) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1


# ── AC7: full role-flexible level completes all merges with no errors ─────────

def test_ac7_full_concurrent_level_completes_with_constraint():
    """A mixed code+test level under the constraint merges every ticket."""
    tracker = _TesterTracker(work_secs=0.005)

    def code(ticket, attempt):
        time.sleep(0.005)
        return StageResult.PASS

    tickets = [1, 2, 3, 4, 5]
    file_map = {t: {f"c{t}.py"} for t in tickets}

    res = run_concurrent_level(
        tickets, code, tracker.test,
        max_coder_slots=3,
        file_map=file_map,
    )

    assert sorted(res.merged) == tickets
    assert res.dropped == []
    assert res.needs_rework == []
    # Constraint held throughout the whole sprint level.
    assert tracker.max_concurrent_test == 1
