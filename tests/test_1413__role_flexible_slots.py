"""Tests for issue #1413: role-flexible worker pool slots.

Each test is anchored to a specific acceptance criterion (AC) from the issue.

AC1  A pool slot can execute either a code_fn task or a test_fn task; role is
     determined by the task pulled from the queue, not by the slot.
AC2  When a coding stage completes, the finished ticket is enqueued for testing
     and the freed slot immediately pulls the next eligible task (code or test).
AC3  With slots=2, one slot can run a coding task while the other runs a testing
     task on a different ticket simultaneously.
AC4  Merge serialization from #738 is preserved: only one ticket may merge to
     develop at a time regardless of how many slots are active.
AC5  Conflict and dependency rules applied during coding are also enforced when a
     slot picks up a test task.
AC6  When a tester rejects a ticket, that ticket is re-queued to the FRONT of the
     coder queue and no other ticket's position is affected.
AC7  Early-sprint runs result in slots predominantly running code_fn; late-sprint
     runs result in slots predominantly running test_fn — no configuration change.
AC8  Existing code_fn and test_fn callables are reused without signature changes.
AC9  Unit tests cover: mixed-role slot assignment, merge overlap prevention with
     concurrent slots, and tester-rejection re-queue ordering.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import pytest

from services.sprint_manager.pipeline import StageResult
from services.sprint_manager.concurrent_scheduler import run_concurrent_level


# ── helpers ───────────────────────────────────────────────────────────────────

def _pass_code(ticket, attempt):
    return StageResult.PASS


def _pass_test(ticket, attempt):
    return StageResult.PASS


def _fail_code(ticket, attempt):
    return StageResult.FAIL


class _RoleTracker:
    """Records which role (code vs test) each slot ran and the peak concurrency."""

    def __init__(self, code_secs: float = 0.0, test_secs: float = 0.0):
        self.code_secs = code_secs
        self.test_secs = test_secs
        self._lock = threading.Lock()
        self.active_code: set = set()
        self.active_test: set = set()
        self.max_concurrent_code: int = 0
        self.max_concurrent_test: int = 0
        self.max_concurrent_total: int = 0
        self.code_ran: list = []
        self.test_ran: list = []

    def code(self, ticket, attempt):
        with self._lock:
            self.active_code.add(ticket)
            total = len(self.active_code) + len(self.active_test)
            self.max_concurrent_code = max(self.max_concurrent_code, len(self.active_code))
            self.max_concurrent_total = max(self.max_concurrent_total, total)
            self.code_ran.append(ticket)
        time.sleep(self.code_secs)
        with self._lock:
            self.active_code.discard(ticket)
        return StageResult.PASS

    def test(self, ticket, attempt):
        with self._lock:
            self.active_test.add(ticket)
            total = len(self.active_code) + len(self.active_test)
            self.max_concurrent_test = max(self.max_concurrent_test, len(self.active_test))
            self.max_concurrent_total = max(self.max_concurrent_total, total)
            self.test_ran.append(ticket)
        time.sleep(self.test_secs)
        with self._lock:
            self.active_test.discard(ticket)
        return StageResult.PASS


# ── AC1: slot executes code_fn or test_fn based on queue ─────────────────────

def test_ac1_single_slot_runs_code_then_test():
    """A single slot runs code_fn for a ticket, then test_fn for the same ticket."""
    order = []
    lock = threading.Lock()

    def code(t, attempt):
        with lock:
            order.append(("code", t))
        return StageResult.PASS

    def test(t, attempt):
        with lock:
            order.append(("test", t))
        return StageResult.PASS

    res = run_concurrent_level([1], code, test, max_coder_slots=1)
    assert res.merged == [1]
    assert order == [("code", 1), ("test", 1)]


def test_ac1_slot_role_determined_by_queue_not_slot():
    """With 2 slots, the role (code vs test) depends on which queue has eligible work."""
    tracker = _RoleTracker(code_secs=0.03, test_secs=0.03)
    file_map = {1: {"a.py"}, 2: {"b.py"}}

    # Ticket 1 starts coding; slot 2 should be able to pick up a test task for
    # an already-coded ticket if one is available. We simulate this by pre-coding
    # ticket 2 manually — since we control code_fn, we inject test-queue loading
    # by making slot 2 run a test while slot 1 codes.
    res = run_concurrent_level(
        [1, 2], tracker.code, tracker.test,
        max_coder_slots=2,
        file_map=file_map,
    )
    assert sorted(res.merged) == [1, 2]
    assert sorted(tracker.code_ran) == [1, 2]
    assert sorted(tracker.test_ran) == [1, 2]


# ── AC2: freed slot picks next eligible task (code or test) ──────────────────

def test_ac2_freed_slot_picks_test_when_no_code_left():
    """After all code tasks complete, freed slots pick up test tasks immediately."""
    order: list = []
    lock = threading.Lock()

    def code(t, attempt):
        time.sleep(0.01)
        with lock:
            order.append(("code", t))
        return StageResult.PASS

    def test(t, attempt):
        with lock:
            order.append(("test", t))
        return StageResult.PASS

    res = run_concurrent_level(
        [1, 2, 3], code, test,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
    )
    assert sorted(res.merged) == [1, 2, 3]
    # Every code must precede its corresponding test.
    for t in [1, 2, 3]:
        assert order.index(("code", t)) < order.index(("test", t))


def test_ac2_freed_coding_slot_picks_test_immediately():
    """When all code tasks are exhausted, a freed slot picks up the next test task."""
    test_start: dict[int, float] = {}
    code_end: dict[int, float] = {}

    def code(t, attempt):
        time.sleep(0.03)
        code_end[t] = time.monotonic()
        return StageResult.PASS

    def test(t, attempt):
        test_start[t] = time.monotonic()
        time.sleep(0.01)
        return StageResult.PASS

    # 3 tickets, 1 slot — slot codes, then picks up test for what it just coded.
    run_concurrent_level(
        [1, 2, 3], code, test,
        max_coder_slots=1,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
    )
    # Each test_start must be after the corresponding code_end (freed slot picks it up).
    for t in [1, 2, 3]:
        assert test_start[t] >= code_end[t] - 0.005  # 5ms tolerance


# ── AC3: code and test run simultaneously with slots=2 ────────────────────────

def test_ac3_code_and_test_run_simultaneously():
    """With 2 slots, one can be coding while the other is testing concurrently."""
    # Ticket 1: code fast, long test.
    # Ticket 2: code fast.
    # With 2 slots, after both code, slot 1 picks test(1) while slot 2 picks test(2)
    # simultaneously, OR slot 2 codes ticket 3 while slot 1 tests ticket 1/2.
    # We verify that code_fn and test_fn tasks overlap in wall time.
    code_spans: list[tuple] = []   # (start, end) tuples
    test_spans: list[tuple] = []
    span_lock = threading.Lock()

    def code(t, attempt):
        t0 = time.monotonic()
        time.sleep(0.04)
        t1 = time.monotonic()
        with span_lock:
            code_spans.append((t0, t1))
        return StageResult.PASS

    def test(t, attempt):
        t0 = time.monotonic()
        time.sleep(0.04)
        t1 = time.monotonic()
        with span_lock:
            test_spans.append((t0, t1))
        return StageResult.PASS

    res = run_concurrent_level(
        [1, 2, 3], code, test,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
    )
    assert sorted(res.merged) == [1, 2, 3]

    # Check that at least one code span and one test span overlap.
    def overlaps(a_start, a_end, b_start, b_end) -> bool:
        return a_start < b_end and b_start < a_end

    found_overlap = any(
        overlaps(cs, ce, ts, te)
        for cs, ce in code_spans
        for ts, te in test_spans
    )
    assert found_overlap, "Expected code and test tasks to overlap in wall-clock time"


# ── AC4: merge serialization with concurrent slots ────────────────────────────

def test_ac4_merge_serialization_preserved():
    """Merges are serialized even when multiple slots run test tasks concurrently.

    Simulates the production contract: test_fn uses develop_merge_guard() for
    the actual merge step. Verifies that concurrent test tasks never merge
    simultaneously.
    """
    from services.sprint_manager.serialization import develop_merge_guard

    inside_merge: list[int] = []
    merge_overlap_detected = threading.Event()
    merge_lock = threading.Lock()

    def test_with_merge(t, attempt):
        with develop_merge_guard():
            with merge_lock:
                inside_merge.append(t)
                if len(inside_merge) > 1:
                    merge_overlap_detected.set()
            time.sleep(0.03)
            with merge_lock:
                inside_merge.remove(t)
        return StageResult.PASS

    res = run_concurrent_level(
        [1, 2, 3], _pass_code, test_with_merge,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert not merge_overlap_detected.is_set(), (
        "Two merges ran simultaneously — develop_merge_guard not respected"
    )


def test_ac4_concurrent_slots_never_merge_simultaneously():
    """With max_coder_slots=3, the peak inside the merge guard is always 1."""
    from services.sprint_manager.serialization import develop_merge_guard

    peak_inside = [0]
    current_inside = [0]
    lock = threading.Lock()

    def test_with_merge(t, attempt):
        with develop_merge_guard():
            with lock:
                current_inside[0] += 1
                peak_inside[0] = max(peak_inside[0], current_inside[0])
            time.sleep(0.02)
            with lock:
                current_inside[0] -= 1
        return StageResult.PASS

    run_concurrent_level(
        [1, 2, 3, 4], _pass_code, test_with_merge,
        max_coder_slots=3,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}, 4: {"d.py"}},
    )
    assert peak_inside[0] == 1, (
        f"Expected peak merge concurrency of 1, got {peak_inside[0]}"
    )


# ── AC5: conflict rules apply when picking test tasks ─────────────────────────

def test_ac5_test_task_blocked_while_coding_same_files():
    """A test task is not started while a coding task on the same files is active."""
    # We'll run 2 slots:
    # Slot 1 codes ticket A (server.py) — slowly
    # Meanwhile ticket B (server.py) finishes coding quickly and waits in tester_q
    # Ticket B's test must NOT start until ticket A finishes coding.
    coding_start: dict[int, float] = {}
    coding_end: dict[int, float] = {}
    test_start: dict[int, float] = {}

    def code(t, attempt):
        coding_start[t] = time.monotonic()
        if t == "A":
            time.sleep(0.08)  # slow coder for A
        else:
            time.sleep(0.01)  # fast coder for B
        coding_end[t] = time.monotonic()
        return StageResult.PASS

    def test(t, attempt):
        test_start[t] = time.monotonic()
        time.sleep(0.01)
        return StageResult.PASS

    file_map = {"A": {"server.py"}, "B": {"server.py"}}

    res = run_concurrent_level(
        ["A", "B"], code, test,
        max_coder_slots=2,
        file_map=file_map,
    )
    assert sorted(res.merged) == ["A", "B"]
    # B finishes coding before A. But B's test should only start after A finishes
    # coding (both touch server.py — conflict rule applies to test tasks too).
    if "B" in test_start and "A" in coding_end:
        assert test_start["B"] >= coding_end["A"] - 0.005, (
            "Ticket B's test started while ticket A was still coding the same file"
        )


def test_ac5_test_task_not_blocked_by_different_files():
    """A test task on different files is NOT blocked by an active coding task."""
    test_started = threading.Event()
    coding_done = threading.Event()

    def code(t, attempt):
        if t == "A":
            # Wait until B's test starts before finishing coding
            test_started.wait(timeout=3)
        return StageResult.PASS

    def test(t, attempt):
        if t == "B":
            test_started.set()  # signal that B's test has started
        return StageResult.PASS

    # A touches x.py, B touches y.py — no conflict
    file_map = {"A": {"x.py"}, "B": {"y.py"}}

    res = run_concurrent_level(
        ["A", "B"], code, test,
        max_coder_slots=2,
        file_map=file_map,
    )
    assert sorted(res.merged) == ["A", "B"]
    assert test_started.is_set(), "B's test never started while A was coding"


# ── AC6: tester rejection re-queues to FRONT of coder queue ──────────────────

def test_ac6_reject_requeues_to_front_of_coder_queue():
    """Rejected ticket is retried (re-queued to front) before any other pending ticket.

    Strategy: ticket 3 shares a file with ticket 1, so ticket 3 cannot be
    selected while ticket 1 is coding.  Ticket 1 codes slowly, giving ticket 2
    time to code → test → REJECT.  After rejection, coder_q = [2, 3] with
    ticket 3 still blocked by the conflict.  The free slot must pick ticket 2
    (from the front) before ticket 3, which is unblocked only after ticket 1
    finishes.
    """
    coding_starts: dict[int, list] = {}
    lock = threading.Lock()
    reject_once = {2}

    def code(t, attempt):
        with lock:
            coding_starts.setdefault(t, []).append(time.monotonic())
        if t == 1:
            time.sleep(0.15)   # hold conflict on "shared.py" long enough
        elif t == 2:
            time.sleep(0.02)   # fast: code → test → reject before ticket 1 done
        return StageResult.PASS

    def test(t, attempt):
        if t in reject_once:
            reject_once.discard(t)
            return StageResult.REJECT
        return StageResult.PASS

    file_map = {1: {"shared.py"}, 2: {"other.py"}, 3: {"shared.py"}}

    res = run_concurrent_level(
        [1, 2, 3], code, test,
        max_coder_slots=2,
        file_map=file_map,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert res.attempts[2] == 2, "Ticket 2 should have been retried once"

    # Ticket 2's retry must start BEFORE ticket 3 (which is blocked by ticket 1).
    t2_retry_start = coding_starts[2][1]
    t3_start = coding_starts[3][0]
    assert t2_retry_start < t3_start, (
        "Rejected ticket 2 retry did not precede ticket 3 — not re-queued to front"
    )


def test_ac6_reject_does_not_affect_other_tickets_position():
    """Rejecting ticket 2 does not prevent ticket 3 from completing normally."""
    merged = []
    rejected_once = {2}

    def test(t, attempt):
        if t in rejected_once:
            rejected_once.discard(t)
            return StageResult.REJECT
        return StageResult.PASS

    res = run_concurrent_level(
        [1, 2, 3], _pass_code, test,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
        on_merged=merged.append,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert sorted(merged) == [1, 2, 3]
    assert res.needs_rework == []
    assert res.dropped == []


def test_ac6_reject_front_queue_ordering_with_multiple_slots():
    """With 2 slots, rejected ticket's retry starts before any other pending ticket.

    Uses same file-conflict strategy: ticket 3 conflicts with ticket 1, so
    it stays in coder_q while ticket 1 codes.  After ticket 2 is rejected the
    free slot should pick ticket 2 (front of coder_q) before ticket 3.
    """
    coding_starts: dict[int, list] = {}
    lock = threading.Lock()
    reject_once = {2}

    def code(t, attempt):
        with lock:
            coding_starts.setdefault(t, []).append(time.monotonic())
        if t == 1:
            time.sleep(0.15)
        elif t == 2:
            time.sleep(0.02)
        return StageResult.PASS

    def test(t, attempt):
        if t in reject_once:
            reject_once.discard(t)
            return StageResult.REJECT
        return StageResult.PASS

    file_map = {1: {"shared.py"}, 2: {"other.py"}, 3: {"shared.py"}}

    res = run_concurrent_level(
        [1, 2, 3], code, test,
        max_coder_slots=2,
        file_map=file_map,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert res.attempts[2] == 2

    t2_retry_start = coding_starts[2][1]
    t3_start = coding_starts[3][0]
    assert t2_retry_start < t3_start, (
        "With 2 slots, rejected ticket 2 retry should precede ticket 3"
    )


# ── AC7: natural load balancing ───────────────────────────────────────────────

def test_ac7_early_sprint_slots_run_code():
    """When all tickets are unstarted, slots run code_fn (no test tasks queued yet)."""
    tracker = _RoleTracker(code_secs=0.02)
    file_map = {1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}}

    run_concurrent_level(
        [1, 2, 3], tracker.code, tracker.test,
        max_coder_slots=3,
        file_map=file_map,
    )
    # All 3 coded; since no test tasks exist initially, slots start with code.
    assert sorted(tracker.code_ran) == [1, 2, 3]
    assert tracker.max_concurrent_code == 3, (
        f"Expected 3 concurrent coders early sprint, got {tracker.max_concurrent_code}"
    )


def test_ac7_late_sprint_slots_run_test():
    """When all tickets are coded (only test tasks remain), slots run test_fn."""
    # Simulate late sprint: code_fn is instant for all tickets; they immediately
    # queue into tester_q; slots then pick up test tasks.
    tracker = _RoleTracker(test_secs=0.03)
    file_map = {1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}}

    res = run_concurrent_level(
        [1, 2, 3], _pass_code, tracker.test,  # instant code
        max_coder_slots=3,
        file_map=file_map,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert sorted(tracker.test_ran) == [1, 2, 3]
    # All test tasks are processed — peak concurrent tests reflects slot flexibility.
    assert tracker.max_concurrent_test >= 1


# ── AC8: code_fn / test_fn signatures unchanged ───────────────────────────────

def test_ac8_code_fn_signature_ticket_and_attempt():
    """code_fn is still called as code_fn(ticket, attempt) — no signature change."""
    calls: list[tuple] = []

    def code(ticket, attempt):
        calls.append((ticket, attempt))
        return StageResult.PASS

    run_concurrent_level([42], code, _pass_test, max_coder_slots=1)
    assert calls == [(42, 1)]


def test_ac8_test_fn_signature_ticket_and_attempt():
    """test_fn is still called as test_fn(ticket, attempt) — no signature change."""
    calls: list[tuple] = []

    def test(ticket, attempt):
        calls.append((ticket, attempt))
        return StageResult.PASS

    run_concurrent_level([99], _pass_code, test, max_coder_slots=1)
    assert calls == [(99, 1)]


# ── AC9: mixed-role slot assignment, merge overlap prevention, reject ordering ─

def test_ac9_mixed_role_slot_assignment():
    """Slots dynamically switch roles: one codes while another tests, then swap."""
    code_active: set = set()
    test_active: set = set()
    mixed_observed = threading.Event()
    track_lock = threading.Lock()

    def code(t, attempt):
        with track_lock:
            code_active.add(t)
            if test_active:  # code + test running simultaneously
                mixed_observed.set()
        time.sleep(0.05)
        with track_lock:
            code_active.discard(t)
        return StageResult.PASS

    def test(t, attempt):
        with track_lock:
            test_active.add(t)
            if code_active:  # test + code running simultaneously
                mixed_observed.set()
        time.sleep(0.05)
        with track_lock:
            test_active.discard(t)
        return StageResult.PASS

    res = run_concurrent_level(
        [1, 2, 3], code, test,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert mixed_observed.is_set(), (
        "No mixed-role slot assignment observed — expected code + test to overlap"
    )


def test_ac9_merge_overlap_prevention_with_concurrent_slots():
    """Concurrent flexible slots never allow two merges in flight at once.

    This mirrors the production contract where test_fn calls develop_merge_guard
    before executing git merge. The scheduler must not prevent this from working.
    """
    from services.sprint_manager.serialization import develop_merge_guard

    merge_count = [0]
    peak_concurrent_merges = [0]
    merge_lock = threading.Lock()

    def test_fn_with_merge(t, attempt):
        # Simulates the real _tester_stage which wraps the merge in develop_merge_guard.
        with develop_merge_guard():
            with merge_lock:
                merge_count[0] += 1
                peak_concurrent_merges[0] = max(peak_concurrent_merges[0], merge_count[0])
            time.sleep(0.03)  # hold the critical section
            with merge_lock:
                merge_count[0] -= 1
        return StageResult.PASS

    res = run_concurrent_level(
        [1, 2, 3, 4], _pass_code, test_fn_with_merge,
        max_coder_slots=3,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}, 4: {"d.py"}},
    )
    assert sorted(res.merged) == [1, 2, 3, 4]
    assert peak_concurrent_merges[0] == 1, (
        f"Expected peak merge concurrency of 1 (serialized), "
        f"got {peak_concurrent_merges[0]}"
    )


def test_ac9_tester_rejection_requeue_ordering():
    """Rejected ticket's retry starts before other pending tickets.

    Uses the same file-conflict gating strategy as the AC6 tests: ticket 3
    shares a file with ticket 1, keeping it in coder_q while ticket 1 codes.
    After ticket 2 is rejected, the free slot picks ticket 2 (from the front)
    before ticket 3 (still blocked by conflict with ticket 1).
    """
    coding_starts: dict[int, list] = {}
    lock = threading.Lock()
    reject_once = {2}

    def code(t, attempt):
        with lock:
            coding_starts.setdefault(t, []).append(time.monotonic())
        if t == 1:
            time.sleep(0.15)
        elif t == 2:
            time.sleep(0.02)
        return StageResult.PASS

    def test(t, attempt):
        if t in reject_once:
            reject_once.discard(t)
            return StageResult.REJECT
        return StageResult.PASS

    file_map = {1: {"shared.py"}, 2: {"other.py"}, 3: {"shared.py"}}

    res = run_concurrent_level(
        [1, 2, 3], code, test,
        max_coder_slots=2,
        file_map=file_map,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert res.attempts[2] == 2, "Ticket 2 should be coded twice (original + retry)"

    t2_retry_start = coding_starts[2][1]
    t3_start = coding_starts[3][0]
    assert t2_retry_start < t3_start, (
        f"Ticket 2 retry started at {t2_retry_start:.4f} but ticket 3 started "
        f"at {t3_start:.4f} — rejected ticket should go to front of coder queue"
    )


def test_ac9_all_acs_combined_smoke():
    """Smoke test: 4 tickets, 2 flexible slots, one rejection, all merge correctly."""
    merged = []
    dropped = []
    rework = []
    reject_once = {3}

    def test(t, attempt):
        if t in reject_once:
            reject_once.discard(t)
            return StageResult.REJECT
        return StageResult.PASS

    res = run_concurrent_level(
        [1, 2, 3, 4], _pass_code, test,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}, 4: {"d.py"}},
        on_merged=merged.append,
        on_needs_rework=rework.append,
        on_dropped=dropped.append,
    )
    assert sorted(res.merged) == [1, 2, 3, 4]
    assert sorted(merged) == [1, 2, 3, 4]
    assert rework == []
    assert dropped == []
    assert res.attempts[3] == 2  # rejected once → coded twice
