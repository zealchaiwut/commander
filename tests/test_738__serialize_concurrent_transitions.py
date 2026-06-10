"""Test suite for issue #738: serialize concurrent agent label transitions and
develop-branch merges.

Each test is anchored to a specific acceptance criterion (AC) from the issue:

AC1  During pipeline mode, at most one ticket carries `in-progress` at any
     moment — always the ticket the coder is actively building.
AC2  At most one ticket carries `SIT` at any moment — always the ticket the
     tester is actively validating.
AC3  The `SIT` → `UAT` transition fires only for the ticket the tester is
     currently handling; no other ticket's label is affected.
AC4  Writes to `develop` are serialized: a second concurrent merge blocks until
     the first completes — never two concurrent merges.
AC5  After a simulated agent crash mid-label-write, the ticket is reconciled back
     to a valid state with zero ghost labels.
AC6  No regression: single-agent (serial) pipeline-mode label transitions behave
     as before.
"""
import threading
import time

import pytest

from services.sprint_manager.pipeline import StageResult, run_level
from services.sprint_manager.serialization import (
    IN_PROGRESS_LABEL,
    SIT_LABEL,
    develop_merge_guard,
    ghost_status_labels,
    label_transition_guard,
    reconcile_status_labels,
)


# ── shared fake label store ───────────────────────────────────────────────────

class _FakeLabels:
    """In-memory stand-in for GitHub labels, guarded by the production lock.

    Records every observed state so a test can assert an invariant held at *every*
    moment, not just at the end.
    """

    def __init__(self):
        self._by_issue: dict[int, set[str]] = {}
        self.observations: list[dict[int, set[str]]] = []
        self._obs_lock = threading.Lock()

    def transition(self, issue: int, status: str | None) -> None:
        """Atomically set the single status label for an issue (None clears it).

        Wrapped in the production label_transition_guard so concurrent writers are
        serialized exactly as the real code path is.
        """
        with label_transition_guard():
            # Simulate the real read-modify-write window (remove then add) with a
            # tiny gap so an unguarded version would interleave and be caught.
            cur = self._by_issue.get(issue, set())
            new = {l for l in cur if l not in (IN_PROGRESS_LABEL, SIT_LABEL)}
            time.sleep(0.001)
            if status is not None:
                new.add(status)
            self._by_issue[issue] = new
            self._snapshot()

    def _snapshot(self) -> None:
        with self._obs_lock:
            self.observations.append(
                {i: set(l) for i, l in self._by_issue.items()}
            )

    def count_with(self, label: str) -> int:
        return sum(1 for l in self._by_issue.values() if label in l)

    def max_concurrent(self, label: str) -> int:
        return max(
            (sum(1 for l in obs.values() if label in l) for obs in self.observations),
            default=0,
        )


# ── AC1: at most one in-progress at any moment ────────────────────────────────

def test_ac1_at_most_one_in_progress_during_pipeline():
    """Across a full concurrent pipeline run, never more than one `in-progress`."""
    labels = _FakeLabels()
    tickets = [1, 2, 3, 4, 5]

    def code_fn(ticket, attempt):
        labels.transition(ticket, IN_PROGRESS_LABEL)  # coder claims it
        time.sleep(0.002)
        labels.transition(ticket, None)  # coder hands off — clears in-progress
        return StageResult.PASS

    def test_fn(ticket, attempt):
        labels.transition(ticket, SIT_LABEL)
        time.sleep(0.002)
        labels.transition(ticket, "UAT")
        return StageResult.PASS

    run_level(tickets, code_fn, test_fn, pipeline=True)

    assert labels.max_concurrent(IN_PROGRESS_LABEL) <= 1
    # End state: every ticket reached UAT, none stuck in-progress.
    assert labels.count_with(IN_PROGRESS_LABEL) == 0


def test_ac1_reconcile_keeps_single_in_progress():
    """Reconcile strips a stale `in-progress` ghost from non-coder tickets."""
    observed = {
        10: {IN_PROGRESS_LABEL, "enhancement"},  # ghost — coder no longer here
        11: {IN_PROGRESS_LABEL},                  # coder's real active ticket
        12: {"UAT"},
    }
    fixed = reconcile_status_labels(observed, coder_active=11, tester_active=None)
    in_prog = [i for i, l in fixed.items() if IN_PROGRESS_LABEL in l]
    assert in_prog == [11]
    assert fixed[10] == {"enhancement"}  # non-status label preserved


# ── AC2: at most one SIT at any moment ────────────────────────────────────────

def test_ac2_at_most_one_sit_during_pipeline():
    labels = _FakeLabels()
    tickets = [1, 2, 3, 4, 5]

    def code_fn(ticket, attempt):
        labels.transition(ticket, IN_PROGRESS_LABEL)
        labels.transition(ticket, None)
        return StageResult.PASS

    def test_fn(ticket, attempt):
        labels.transition(ticket, SIT_LABEL)
        time.sleep(0.003)
        labels.transition(ticket, "UAT")
        return StageResult.PASS

    run_level(tickets, code_fn, test_fn, pipeline=True)

    assert labels.max_concurrent(SIT_LABEL) <= 1
    assert labels.count_with(SIT_LABEL) == 0


def test_ac2_reconcile_keeps_single_sit():
    observed = {
        20: {SIT_LABEL},               # ghost — tester moved on
        21: {SIT_LABEL, "sprint-56"},  # tester's real active ticket
    }
    fixed = reconcile_status_labels(observed, coder_active=None, tester_active=21)
    sit = [i for i, l in fixed.items() if SIT_LABEL in l]
    assert sit == [21]
    assert fixed[20] == set()  # ghost SIT stripped, nothing else to keep


# ── AC3: SIT → UAT affects only the tester's current ticket ───────────────────

def test_ac3_sit_to_uat_only_touches_tester_ticket():
    """Reconciling around a SIT→UAT transition leaves unrelated labels untouched."""
    observed = {
        30: {"UAT"},                     # just promoted by tester — keep
        31: {SIT_LABEL},                 # tester's NEW active ticket — keep SIT
        32: {IN_PROGRESS_LABEL},         # coder's active ticket — untouched
        33: {"needs-rework"},            # unrelated terminal — untouched
    }
    fixed = reconcile_status_labels(observed, coder_active=32, tester_active=31)
    assert fixed[30] == {"UAT"}          # AC3: promotion not disturbed
    assert fixed[31] == {SIT_LABEL}
    assert fixed[32] == {IN_PROGRESS_LABEL}
    assert fixed[33] == {"needs-rework"}
    # Exactly one SIT, exactly one in-progress.
    assert sum(1 for l in fixed.values() if SIT_LABEL in l) == 1
    assert sum(1 for l in fixed.values() if IN_PROGRESS_LABEL in l) == 1


# ── AC4: develop merges are serialized ────────────────────────────────────────

def test_ac4_develop_merges_never_overlap():
    """Two concurrent merge attempts through develop_merge_guard never overlap."""
    inside = 0
    max_inside = 0
    state_lock = threading.Lock()

    def do_merge():
        nonlocal inside, max_inside
        with develop_merge_guard():
            with state_lock:
                inside += 1
                max_inside = max(max_inside, inside)
            time.sleep(0.02)  # hold the critical section
            with state_lock:
                inside -= 1

    threads = [threading.Thread(target=do_merge) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_inside == 1  # never two merges in flight at once


def test_ac4_second_merge_blocks_until_first_completes():
    """A second merge attempt blocks until the first releases the lock."""
    order: list[str] = []
    first_in = threading.Event()
    release_first = threading.Event()

    def first():
        with develop_merge_guard():
            order.append("first-enter")
            first_in.set()
            release_first.wait(timeout=2)
            order.append("first-exit")

    def second():
        first_in.wait(timeout=2)  # ensure first holds the lock
        with develop_merge_guard():
            order.append("second-enter")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    first_in.wait(timeout=2)
    t2.start()
    time.sleep(0.05)  # give second a chance to (wrongly) enter
    assert "second-enter" not in order  # blocked while first holds
    release_first.set()
    t1.join()
    t2.join()
    assert order == ["first-enter", "first-exit", "second-enter"]


# ── AC5: crash mid-label-write reconciled to a valid state ────────────────────

def test_ac5_limbo_ticket_reconciled_to_in_progress():
    """A ticket left label-less (crash between remove and add) regains its label."""
    observed = {
        40: set(),               # limbo: crashed mid-write, no status label
        41: {SIT_LABEL},
    }
    fixed = reconcile_status_labels(observed, coder_active=40, tester_active=41)
    assert fixed[40] == {IN_PROGRESS_LABEL}  # reconciled out of limbo
    assert fixed[41] == {SIT_LABEL}


def test_ac5_limbo_ticket_absent_from_snapshot_restored():
    """An active ticket missing entirely from the snapshot is restored."""
    fixed = reconcile_status_labels({}, coder_active=50, tester_active=51)
    assert fixed[50] == {IN_PROGRESS_LABEL}
    assert fixed[51] == {SIT_LABEL}


def test_ac5_ghost_helper_reports_only_stale_transient_labels():
    observed = {
        60: {IN_PROGRESS_LABEL, "enhancement"},  # ghost in-progress
        61: {SIT_LABEL},                          # ghost SIT
        62: {IN_PROGRESS_LABEL},                  # legit coder ticket
        63: {"UAT"},                              # no transient ghost
    }
    ghosts = ghost_status_labels(observed, coder_active=62, tester_active=None)
    assert ghosts == {60: {IN_PROGRESS_LABEL}, 61: {SIT_LABEL}}


# ── AC6: serial mode behaves as before ────────────────────────────────────────

def test_ac6_serial_mode_transitions_unchanged():
    """Serial pipeline mode still drives each ticket through in-progress→SIT→UAT
    with no concurrency and no ghost labels."""
    labels = _FakeLabels()
    tickets = [1, 2, 3]
    order: list[tuple] = []

    def code_fn(ticket, attempt):
        labels.transition(ticket, IN_PROGRESS_LABEL)
        order.append((ticket, "coder"))
        labels.transition(ticket, None)
        return StageResult.PASS

    def test_fn(ticket, attempt):
        labels.transition(ticket, SIT_LABEL)
        order.append((ticket, "tester"))
        labels.transition(ticket, "UAT")
        return StageResult.PASS

    result = run_level(tickets, code_fn, test_fn, pipeline=False)

    # Strict serial order: each ticket fully processed before the next starts.
    assert order == [
        (1, "coder"), (1, "tester"),
        (2, "coder"), (2, "tester"),
        (3, "coder"), (3, "tester"),
    ]
    assert result.merged == [1, 2, 3]
    assert labels.count_with(IN_PROGRESS_LABEL) == 0
    assert labels.count_with(SIT_LABEL) == 0
    # Never any concurrency in serial mode.
    assert labels.max_concurrent(IN_PROGRESS_LABEL) <= 1
    assert labels.max_concurrent(SIT_LABEL) <= 1


def test_ac6_label_transition_guard_is_reentrant():
    """The label guard is reentrant so a guarded write nested in another guarded
    write on the same thread does not deadlock (serial code path safety)."""
    with label_transition_guard():
        with label_transition_guard():
            assert True  # reached without deadlock
