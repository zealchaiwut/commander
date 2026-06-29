"""Tests for issue #1436: concurrent scheduler mutates shared SprintState without a lock.

Context: sprint-92 review of #1412 flagged that the concurrent worker threads
spawned by ``run_concurrent_level`` execute the coder/tester stage closures
(``_coder_stage`` / ``_tester_stage`` in ``pipeline.py``) *outside* the
scheduler's own lock. Those closures perform non-atomic read-modify-writes on
``state.total_tokens_in`` / ``state.total_tokens_out`` and call
``state.save(state_path)`` — so concurrent workers can lose token counts and
interleave writes to the single state file.

The fix introduces ``ConcurrentStateGuard`` (services/sprint_manager/
concurrent_scheduler.py) which serializes both the counter RMW and the
``state.save()`` file write under one lock, but only when running in concurrent
mode (``max_coder_slots > 1``). ``pipeline._run_pipeline_dispatch`` routes every
worker-thread counter mutation and state save through this guard.

Each test is anchored to a specific acceptance criterion (AC).

AC1  A threading.Lock is introduced and held for every RMW on
     state.total_tokens_in (and total_tokens_out) mutated in worker threads.
AC2  Every state.save() from within worker threads is guarded by the SAME lock,
     preventing interleaved file writes.
AC3  The lock is only acquired in concurrent mode (max_coder_slots > 1);
     single-slot runs are unaffected (no lock acquisition).
AC4  After a concurrent run, state.total_tokens_in equals the arithmetic sum of
     all individual worker token values (no counts silently dropped).
AC5  The state file written at the end of a concurrent run is valid JSON with no
     corruption caused by interleaved writes.
"""
from __future__ import annotations

import json
import threading

from services.sprint_manager.concurrent_scheduler import (
    ConcurrentStateGuard,
    run_concurrent_level,
)
from services.sprint_manager.pipeline import StageResult
from services.sprint_manager.state import SprintState


# ── helpers ──────────────────────────────────────────────────────────────────


class _TrackingLock:
    """Wraps a real lock and counts how many times it is entered."""

    def __init__(self):
        self._lock = threading.Lock()
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self._lock.__enter__()

    def __exit__(self, *exc):
        return self._lock.__exit__(*exc)

    # acquire/release so it is a drop-in for threading.Lock where needed.
    def acquire(self, *a, **k):
        self.enter_count += 1
        return self._lock.acquire(*a, **k)

    def release(self):
        return self._lock.release()


def _new_state():
    return SprintState(sprint_label="sprint-test", sprint_number=1)


# ── AC1: a threading.Lock guards every counter RMW ───────────────────────────


def test_ac1_guard_exposes_a_threading_lock():
    """ConcurrentStateGuard owns a real lock object usable as a context manager."""
    guard = ConcurrentStateGuard(_new_state(), None, concurrent=True)
    # The lock must support the context-manager protocol (with-statement).
    assert hasattr(guard.lock, "__enter__")
    assert hasattr(guard.lock, "__exit__")
    with guard.lock:
        pass  # acquiring/releasing must not raise


def test_ac1_concurrent_counter_rmw_holds_the_lock():
    """In concurrent mode, add_tokens acquires the guard lock for the RMW."""
    state = _new_state()
    guard = ConcurrentStateGuard(state, None, concurrent=True)
    tracker = _TrackingLock()
    guard._lock = tracker  # observe acquisitions

    guard.add_tokens(10, 5)

    assert tracker.enter_count == 1
    assert state.total_tokens_in == 10
    assert state.total_tokens_out == 5


def test_ac1_concurrent_rmw_is_atomic_under_thread_contention():
    """Many threads hammering add_tokens never lose an update (RMW is locked).

    A bare ``state.total_tokens_in += 1`` is a load-add-store that the GIL can
    interleave; with the lock the final total must be exact.
    """
    state = _new_state()
    guard = ConcurrentStateGuard(state, None, concurrent=True)
    n_threads, per_thread = 16, 2000

    def hammer():
        for _ in range(per_thread):
            guard.add_tokens(1, 1)

    threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state.total_tokens_in == n_threads * per_thread
    assert state.total_tokens_out == n_threads * per_thread


# ── AC2: state.save() guarded by the SAME lock ───────────────────────────────


def test_ac2_save_uses_the_same_lock_as_counter_rmw(tmp_path):
    """save() and add_tokens() share one lock so writes never interleave with RMW."""
    state_path = tmp_path / "state.json"
    state = _new_state()
    guard = ConcurrentStateGuard(state, state_path, concurrent=True)
    tracker = _TrackingLock()
    guard._lock = tracker

    guard.add_tokens(1, 1)   # +1 acquisition
    guard.save()             # +1 acquisition — same lock

    assert tracker.enter_count == 2
    assert state_path.exists()


def test_ac2_concurrent_saves_never_corrupt_the_file(tmp_path):
    """Concurrent guard.save() writers never interleave: the file is valid JSON
    at every serialization point the guard mediates.

    Each thread mutates a counter and saves, then re-reads the file *through the
    same guard lock* — modelling the contract that all access to the shared
    state file is mediated by the guard. Because writers and the guarded read
    share one lock, a reader can never observe a half-written file. Without the
    lock (single-slot mode) writers would interleave their multi-syscall writes
    and the read could catch a truncated document.
    """
    from services.sprint_manager.state import IssueState

    state_path = tmp_path / "state.json"
    state = _new_state()
    # Make the serialized payload non-trivial (>8KB) so an unguarded write would
    # span multiple syscalls and could interleave.
    state.issues = [IssueState(number=i, title="x" * 200) for i in range(50)]
    guard = ConcurrentStateGuard(state, state_path, concurrent=True)

    errors: list = []

    def writer():
        for _ in range(50):
            guard.add_tokens(1, 0)
            guard.save()
            # Read at a guard-mediated serialization point: never a partial write.
            with guard.lock:
                try:
                    json.loads(state_path.read_text())
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"state file was corrupted by interleaved writes: {errors[:3]}"
    # Final file is valid JSON and reflects every increment (8 threads × 50).
    on_disk = json.loads(state_path.read_text())
    assert on_disk["total_tokens_in"] == 8 * 50


# ── AC3: lock only acquired in concurrent mode ───────────────────────────────


def test_ac3_single_slot_mode_does_not_acquire_the_lock(tmp_path):
    """With concurrent=False the guard performs no lock acquisition."""
    state_path = tmp_path / "state.json"
    state = _new_state()
    guard = ConcurrentStateGuard(state, state_path, concurrent=False)
    tracker = _TrackingLock()
    guard._lock = tracker

    guard.add_tokens(7, 3)
    guard.save()

    assert tracker.enter_count == 0, "single-slot mode must not acquire the lock"
    # The mutation/save still happen — just without locking.
    assert state.total_tokens_in == 7
    assert state.total_tokens_out == 3
    assert json.loads(state_path.read_text())["total_tokens_in"] == 7


def test_ac3_concurrent_flag_reflects_mode():
    """The guard records whether it is in concurrent mode."""
    assert ConcurrentStateGuard(_new_state(), None, concurrent=True).concurrent is True
    assert ConcurrentStateGuard(_new_state(), None, concurrent=False).concurrent is False


# ── AC4: total equals arithmetic sum of all worker token values ──────────────


def test_ac4_concurrent_run_total_equals_sum_of_worker_tokens(tmp_path):
    """Through a real run_concurrent_level run, the accumulated total is exact.

    Each ticket's code stage adds a distinct, known token count via the guard.
    After the level drains, total_tokens_in must equal the arithmetic sum with
    no lost-write races.
    """
    state_path = tmp_path / "state.json"
    state = _new_state()
    state.max_coder_slots = 4
    guard = ConcurrentStateGuard(state, state_path, concurrent=True)

    tickets = list(range(1, 13))
    per_ticket_in = {t: t * 100 for t in tickets}      # distinct known values
    per_ticket_out = {t: t * 10 for t in tickets}

    def code(t, attempt):
        guard.add_tokens(per_ticket_in[t], per_ticket_out[t])
        guard.save()
        return StageResult.PASS

    def test(t, attempt):
        return StageResult.PASS

    res = run_concurrent_level(
        tickets, code, test,
        max_coder_slots=4,
        file_map={t: {f"f{t}.py"} for t in tickets},  # all disjoint → max concurrency
    )

    assert sorted(res.merged) == tickets
    assert state.total_tokens_in == sum(per_ticket_in.values())
    assert state.total_tokens_out == sum(per_ticket_out.values())


# ── AC5: final state file is valid, uncorrupted JSON ─────────────────────────


def test_ac5_final_state_file_is_valid_json_after_concurrent_run(tmp_path):
    """After a concurrent run that saves from each worker, the file parses cleanly
    and reflects the correct accumulated total."""
    state_path = tmp_path / "state.json"
    state = _new_state()
    state.max_coder_slots = 4
    guard = ConcurrentStateGuard(state, state_path, concurrent=True)

    tickets = list(range(1, 9))

    def code(t, attempt):
        guard.add_tokens(50, 25)
        guard.save()
        return StageResult.PASS

    def test(t, attempt):
        guard.add_tokens(5, 2)
        guard.save()
        return StageResult.PASS

    run_concurrent_level(
        tickets, code, test,
        max_coder_slots=4,
        file_map={t: {f"f{t}.py"} for t in tickets},
    )

    # Final on-disk state parses and equals the in-memory total.
    on_disk = json.loads(state_path.read_text())
    assert on_disk["total_tokens_in"] == state.total_tokens_in
    assert state.total_tokens_in == len(tickets) * (50 + 5)
    assert on_disk["total_tokens_out"] == state.total_tokens_out


# ── Integration: pipeline routes worker-thread mutations through a guard ──────


def test_pipeline_dispatch_uses_concurrent_state_guard():
    """pipeline._run_pipeline_dispatch wires worker token mutations + saves through
    ConcurrentStateGuard so the concurrent path is actually protected (AC1+AC2)."""
    import inspect

    from services.sprint_manager import pipeline

    src = inspect.getsource(pipeline._run_pipeline_dispatch)
    assert "ConcurrentStateGuard" in src, (
        "_run_pipeline_dispatch must construct a ConcurrentStateGuard to protect "
        "shared SprintState mutations in concurrent worker threads"
    )
    # The raw unguarded counter RMW must no longer appear in the dispatch body.
    assert "state.total_tokens_in +=" not in src, (
        "raw unguarded 'state.total_tokens_in +=' RMW must be routed through the guard"
    )
    assert "state.total_tokens_out +=" not in src
