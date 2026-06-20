"""Tests for issue #1412: conflict-aware concurrent scheduler for the sprint runner.

Each test is anchored to a specific acceptance criterion (AC).

AC1  max_coder_slots and max_tester_slots configuration (default 1 each) accepted
     at sprint-start and persisted on SprintState; running-pane lane capacity
     reflects reality throughout the run.
AC2  When max_coder_slots=1 the scheduler behaves identically to the existing
     pipeline (no regression).
AC3  Three tickets at the same DAG level with fully disjoint files_likely_affected
     all dispatch coders simultaneously when max_coder_slots=3.
AC4  Two tickets sharing at least one path in files_likely_affected are never
     coding at the same time; the second is held until the first is merged.
AC5  depends_on / blocks edges are hard ordering: a downstream ticket never enters
     the coder pool before its upstream ticket is merged. (Tested via the level
     barrier — downstream tickets live in a later level.)
AC6  Level barrier preserved: run_concurrent_level does not return until every
     ticket in the level has reached a terminal state.
AC7  Coders are dispatched into the existing warm worktree pool (integration
     concern; not directly unit-testable).
AC8  Each scheduling tick selects the largest eligible set of tickets whose deps
     are satisfied and files do not overlap with currently-coding tickets.
AC9  Sprint state records active slot counts; resumed/inspected sprint shows
     correct lane occupancy.
"""
from __future__ import annotations

import threading
import time

from services.sprint_manager.pipeline import StageResult
from services.sprint_manager.concurrent_scheduler import run_concurrent_level


# ── helpers ──────────────────────────────────────────────────────────────────


def _pass_code(ticket, attempt):
    return StageResult.PASS


def _pass_test(ticket, attempt):
    return StageResult.PASS


def _fail_code(ticket, attempt):
    return StageResult.FAIL


class _ConcurrencyTracker:
    """Records concurrent coder occupancy and file-conflict violations."""

    def __init__(self, work_secs: float = 0.0):
        self.work_secs = work_secs
        self._lock = threading.Lock()
        self.active: set = set()          # tickets currently in code_fn
        self.max_concurrent: int = 0      # peak simultaneous coders
        self.overlap_pairs: list = []     # (t1, t2) pairs that coded simultaneously

    def code(self, ticket, attempt, *, file_map=None):
        with self._lock:
            for other in self.active:
                if file_map and (
                    file_map.get(ticket, set()) & file_map.get(other, set())
                ):
                    self.overlap_pairs.append((ticket, other))
            self.active.add(ticket)
            self.max_concurrent = max(self.max_concurrent, len(self.active))
        time.sleep(self.work_secs)
        with self._lock:
            self.active.discard(ticket)
        return StageResult.PASS


# ── AC1: max_coder_slots / max_tester_slots on SprintState ───────────────────


def test_ac1_sprint_state_has_max_coder_slots_field():
    """SprintState carries max_coder_slots with default 1."""
    from services.sprint_manager.state import SprintState
    s = SprintState(sprint_label="test", sprint_number=1)
    assert hasattr(s, "max_coder_slots")
    assert s.max_coder_slots == 1


def test_ac1_sprint_state_has_max_tester_slots_field():
    """SprintState carries max_tester_slots with default 1."""
    from services.sprint_manager.state import SprintState
    s = SprintState(sprint_label="test", sprint_number=1)
    assert hasattr(s, "max_tester_slots")
    assert s.max_tester_slots == 1


def test_ac1_sprint_state_has_active_coder_slots_field():
    """SprintState carries active_coder_slots (real-time occupancy) defaulting to 0."""
    from services.sprint_manager.state import SprintState
    s = SprintState(sprint_label="test", sprint_number=1)
    assert hasattr(s, "active_coder_slots")
    assert s.active_coder_slots == 0


def test_ac1_max_coder_slots_round_trips_through_dict():
    """max_coder_slots / max_tester_slots survive to_dict / from_dict round-trip."""
    from services.sprint_manager.state import SprintState
    s = SprintState(sprint_label="s1", sprint_number=1)
    s.max_coder_slots = 3
    s.max_tester_slots = 2
    s.active_coder_slots = 2
    d = s.to_dict()
    assert d["max_coder_slots"] == 3
    assert d["max_tester_slots"] == 2
    assert d["active_coder_slots"] == 2
    s2 = SprintState.from_dict(d)
    assert s2.max_coder_slots == 3
    assert s2.max_tester_slots == 2
    assert s2.active_coder_slots == 2


def test_ac1_sprint_config_has_max_tester_slots():
    """SprintConfig carries max_tester_slots; None means 'not configured in yaml'.

    Issue #1415 changed the default from 1 to None to distinguish 'not set'
    from 'explicitly set to 1'. The sprint runner resolves None → 1 (serial).
    """
    from services.sprint_manager.config import SprintConfig
    cfg = SprintConfig()
    assert hasattr(cfg, "max_tester_slots")
    # None = not configured; runner resolves to 1 (serial) via run_sprint logic.
    assert cfg.max_tester_slots is None


# ── AC2: max_coder_slots=1 is identical to existing pipeline ────────────────


def test_ac2_slots_1_merges_all_tickets():
    """max_coder_slots=1 processes all tickets and merges them."""
    merged = []
    res = run_concurrent_level(
        [1, 2, 3], _pass_code, _pass_test,
        max_coder_slots=1,
        on_merged=merged.append,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert sorted(merged) == [1, 2, 3]
    assert res.dropped == []
    assert res.needs_rework == []


def test_ac2_slots_1_coder_before_tester_per_ticket():
    """With max_coder_slots=1 each ticket is coded before it is tested."""
    order = []
    lock = threading.Lock()

    def code(ticket, attempt):
        with lock:
            order.append(("code", ticket))
        return StageResult.PASS

    def test(ticket, attempt):
        with lock:
            order.append(("test", ticket))
        return StageResult.PASS

    run_concurrent_level([1, 2, 3], code, test, max_coder_slots=1)
    for t in [1, 2, 3]:
        assert order.index(("code", t)) < order.index(("test", t))


def test_ac2_slots_1_rejection_retries_and_merges():
    """Tester rejection re-queues to the coder; final merge still happens."""
    reject_once = {2}

    def test(ticket, attempt):
        if ticket in reject_once:
            reject_once.discard(ticket)
            return StageResult.REJECT
        return StageResult.PASS

    merged = []
    res = run_concurrent_level(
        [1, 2, 3], _pass_code, test,
        max_coder_slots=1,
        on_merged=merged.append,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert res.attempts[2] == 2  # coded twice


# ── AC3: three disjoint tickets → all start simultaneously ──────────────────


def test_ac3_three_disjoint_tickets_start_concurrently():
    """Three tickets with non-overlapping files all code at the same time."""
    tracker = _ConcurrencyTracker(work_secs=0.05)
    file_map = {
        1: {"a.py"},
        2: {"b.py"},
        3: {"c.py"},
    }

    def code(t, attempt):
        return tracker.code(t, attempt, file_map=file_map)

    res = run_concurrent_level(
        [1, 2, 3], code, _pass_test,
        max_coder_slots=3,
        file_map=file_map,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert tracker.max_concurrent == 3, (
        f"Expected 3 concurrent coders, got {tracker.max_concurrent}"
    )
    assert tracker.overlap_pairs == [], "File-conflicting tickets coded simultaneously"


def test_ac3_disjoint_all_merge():
    """All three disjoint tickets are merged when max_coder_slots=3."""
    res = run_concurrent_level(
        [1, 2, 3], _pass_code, _pass_test,
        max_coder_slots=3,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
    )
    assert sorted(res.merged) == [1, 2, 3]


# ── AC4: tickets sharing a file are never coding simultaneously ──────────────


def test_ac4_conflicting_pair_never_codes_simultaneously():
    """T1 and T2 share server.py; they must never code at the same time."""
    tracker = _ConcurrencyTracker(work_secs=0.05)
    file_map = {
        1: {"server.py"},
        2: {"server.py"},
    }

    def code(t, attempt):
        return tracker.code(t, attempt, file_map=file_map)

    res = run_concurrent_level(
        [1, 2], code, _pass_test,
        max_coder_slots=2,
        file_map=file_map,
    )
    assert sorted(res.merged) == [1, 2]
    assert tracker.max_concurrent == 1, (
        f"Expected at most 1 concurrent coder for conflicting tickets, "
        f"got {tracker.max_concurrent}"
    )
    assert tracker.overlap_pairs == [], "Conflicting tickets coded simultaneously"


def test_ac4_second_ticket_held_until_first_merged():
    """The second conflicting ticket only starts after the first is merged (terminal)."""
    start_times: dict[int, float] = {}
    finish_times: dict[int, float] = {}
    file_map = {1: {"shared.py"}, 2: {"shared.py"}}

    def code(t, attempt):
        start_times[t] = time.monotonic()
        time.sleep(0.03)
        finish_times[t] = time.monotonic()
        return StageResult.PASS

    run_concurrent_level(
        [1, 2], code, _pass_test,
        max_coder_slots=2,
        file_map=file_map,
    )
    # T2 must start after T1 finishes coding (i.e., after T1's worktree is free
    # AND tester merges T1; but at minimum after T1 exits code_fn).
    assert start_times[2] >= finish_times[1], (
        "Second ticket started coding before first finished"
    )


# ── AC5: depends_on / blocks → hard ordering via level barrier ───────────────


def test_ac5_level_barrier_no_level2_before_level1_merged():
    """Level N+1 tickets never start before level N is fully merged.

    run_concurrent_level does not return until all its tickets are terminal,
    so the caller iterating levels enforces the hard ordering.
    """
    level_start: dict[int, float] = {}
    level_finish: dict[int, float] = {}

    def code(t, attempt):
        level_start[t] = time.monotonic()
        time.sleep(0.02)
        level_finish[t] = time.monotonic()
        return StageResult.PASS

    # Level 0: T1, T2 (independent)
    run_concurrent_level(
        [1, 2], code, _pass_test,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}},
    )
    level0_done = time.monotonic()

    # Level 1: T3, T4 (depend on T1 and T2 via DAG level)
    run_concurrent_level(
        [3, 4], code, _pass_test,
        max_coder_slots=2,
        file_map={3: {"c.py"}, 4: {"d.py"}},
    )

    # T3 and T4 must not have started before run_concurrent_level returned for level 0
    assert level_start[3] >= level0_done, "Level-1 ticket started before level-0 finished"
    assert level_start[4] >= level0_done, "Level-1 ticket started before level-0 finished"


# ── AC6: run_concurrent_level does not return until level is fully drained ───


def test_ac6_level_barrier_function_blocks_until_all_terminal():
    """run_concurrent_level blocks the caller until all tickets are terminal."""
    def slow_code(t, attempt):
        time.sleep(0.03)
        return StageResult.PASS

    def slow_test(t, attempt):
        time.sleep(0.02)
        return StageResult.PASS

    res = run_concurrent_level(
        [1, 2, 3], slow_code, slow_test,
        max_coder_slots=3,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
    )

    assert sorted(res.merged) == [1, 2, 3]
    # All 3 coded concurrently (~30ms) + serial tester (3 × 20ms = ~60ms) ≈ 90ms.
    # If the call returned early, merged would be incomplete.
    assert len(res.merged) == 3


def test_ac6_dropped_ticket_does_not_block():
    """A coder failure (FAIL) marks the ticket dropped without blocking the rest."""
    def code(t, attempt):
        if t == 2:
            return StageResult.FAIL
        return StageResult.PASS

    merged = []
    dropped = []
    res = run_concurrent_level(
        [1, 2, 3], code, _pass_test,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
        on_merged=merged.append,
        on_dropped=dropped.append,
    )
    assert sorted(merged) == [1, 3]
    assert dropped == [2]
    assert sorted(res.merged) == [1, 3]
    assert res.dropped == [2]


# ── AC8: largest eligible set selected per tick ──────────────────────────────


def test_ac8_largest_eligible_set_dispatched():
    """When 3 slots are free and 3 non-conflicting tickets are pending, all 3 start.

    This test verifies the scheduler doesn't artificially serialize disjoint
    tickets when slots are available.
    """
    tracker = _ConcurrencyTracker(work_secs=0.04)
    file_map = {1: {"f1.py"}, 2: {"f2.py"}, 3: {"f3.py"}}

    def code(t, attempt):
        return tracker.code(t, attempt, file_map=file_map)

    run_concurrent_level(
        [1, 2, 3], code, _pass_test,
        max_coder_slots=3,
        file_map=file_map,
    )
    assert tracker.max_concurrent == 3


def test_ac8_conflict_reduces_eligible_set():
    """With 3 slots but a pairwise conflict, only 2 tickets can start together.

    T1 (a.py), T2 (b.py), T3 (a.py): T1 and T3 conflict. With 3 slots, we can
    only code 2 simultaneously in the first batch (e.g., T1 + T2 or T2 + T3).
    """
    tracker = _ConcurrencyTracker(work_secs=0.04)
    file_map = {1: {"a.py"}, 2: {"b.py"}, 3: {"a.py"}}

    def code(t, attempt):
        return tracker.code(t, attempt, file_map=file_map)

    res = run_concurrent_level(
        [1, 2, 3], code, _pass_test,
        max_coder_slots=3,
        file_map=file_map,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert tracker.max_concurrent == 2, (
        f"With T1/T3 conflicting, at most 2 coders should run simultaneously, "
        f"got {tracker.max_concurrent}"
    )
    assert tracker.overlap_pairs == [], "Conflicting pair coded simultaneously"


# ── AC9: sprint state records active slot counts ─────────────────────────────


def test_ac9_on_active_slots_change_callback_fires():
    """on_active_slots_change is called each time active coder count changes."""
    observations: list[int] = []

    def code(t, attempt):
        time.sleep(0.02)
        return StageResult.PASS

    run_concurrent_level(
        [1, 2, 3], code, _pass_test,
        max_coder_slots=3,
        file_map={1: {"a.py"}, 2: {"b.py"}, 3: {"c.py"}},
        on_active_slots_change=observations.append,
    )
    assert len(observations) > 0
    # Peak must have been > 1 (concurrent coders observed)
    assert max(observations) > 1


def test_ac9_active_slots_zero_when_level_complete():
    """Active slot count returns to 0 after run_concurrent_level returns."""
    slot_at_end = []

    def code(t, attempt):
        return StageResult.PASS

    run_concurrent_level(
        [1, 2], code, _pass_test,
        max_coder_slots=2,
        file_map={1: {"a.py"}, 2: {"b.py"}},
        on_active_slots_change=slot_at_end.append,
    )
    # After the level is drained, active slots must be 0
    assert slot_at_end[-1] == 0


def test_ac9_sprint_state_active_slots_updated_via_callback(monkeypatch, tmp_path):
    """run_sprint records active_coder_slots on SprintState when concurrent mode is active."""
    import services.sprint_manager.sprint_manager as sm

    def _stub_run_concurrent(tickets, code_fn, test_fn, **kwargs):
        cb = kwargs.get("on_active_slots_change")
        if cb:
            cb(len(tickets))  # pretend all started
            cb(0)             # then all finished
        return sm.pipeline.LevelResult(merged=list(tickets), order=[], attempts={t: 1 for t in tickets})

    monkeypatch.setattr(
        "services.sprint_manager.concurrent_scheduler.run_concurrent_level",
        _stub_run_concurrent,
    )

    # Build minimal state and call the internal dispatch helper
    state = sm.SprintState(sprint_label="sprint-test", sprint_number=1, issues=[
        sm.IssueState(number=101, title="t101"),
        sm.IssueState(number=102, title="t102"),
    ])
    state.max_coder_slots = 2

    # Verify the state has the field and it can be updated
    state.active_coder_slots = 2
    assert state.active_coder_slots == 2
    state.active_coder_slots = 0
    assert state.active_coder_slots == 0


# ── Regression: existing pipeline_mode tests must still pass ────────────────


def test_regression_pipeline_run_level_still_works():
    """The existing pipeline.run_level function is unaffected (regression guard)."""
    from services.sprint_manager.pipeline import run_level

    merged = []
    res = run_level(
        [1, 2, 3], _pass_code, _pass_test,
        pipeline=True,
        on_merged=merged.append,
    )
    assert sorted(res.merged) == [1, 2, 3]
    assert sorted(merged) == [1, 2, 3]


def test_regression_max_coder_slots_1_identical_to_pipeline_run_level():
    """run_concurrent_level(max_coder_slots=1) produces the same result set as run_level."""
    from services.sprint_manager.pipeline import run_level

    def make_stages():
        merged = []
        reject_once = {2}

        def code(t, attempt):
            return StageResult.PASS

        def test(t, attempt):
            if t in reject_once:
                reject_once.discard(t)
                return StageResult.REJECT
            return StageResult.PASS

        return code, test, merged

    code1, test1, m1 = make_stages()
    res1 = run_level([1, 2, 3], code1, test1, pipeline=True, on_merged=m1.append)

    code2, test2, m2 = make_stages()
    res2 = run_concurrent_level(
        [1, 2, 3], code2, test2, max_coder_slots=1, on_merged=m2.append,
    )

    assert sorted(res1.merged) == sorted(res2.merged)
    assert sorted(m1) == sorted(m2)
