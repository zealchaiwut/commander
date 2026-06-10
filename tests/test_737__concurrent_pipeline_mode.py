"""Test suite for issue #737: opt-in concurrent pipeline mode for sprint manager.

Each test is anchored to a specific acceptance criterion (AC) from the issue.

AC1  pipeline_mode setting at per-sprint and per-project scope, defaults False.
AC2  kill-switch (env var) forces serial regardless of pipeline_mode value.
AC3  exactly one coder + one tester worker run concurrently — never more.
AC4  coder worker pulls a coder queue; tester worker pulls a tester queue fed
     by coder completions, producing a SIT branch per ticket.
AC5  10 independent tickets: pipeline wall-clock ~= half of serial.
AC6  hard level barrier — next level never starts until current level merged.
AC7  tester rejection pushes the ticket to the FRONT of the coder queue.
AC8  retry cap of 3 — exceeding the cap labels needs-rework and drops the
     ticket from both queues without blocking the pipeline.
AC9  per-ticket side effects identical in both modes (same calls, same order).
AC10 pipeline_mode False / kill-switch restores exact serial behavior.
"""
import threading
import time

import pytest

from services.sprint_manager.pipeline import (
    DEFAULT_MAX_ATTEMPTS,
    PIPELINE_KILL_SWITCH_ENV,
    LevelResult,
    StageResult,
    pipeline_mode_enabled,
    run_level,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _always_pass_code(ticket, attempt):
    return StageResult.PASS


def _always_pass_test(ticket, attempt):
    return StageResult.PASS


class _ConcurrencyTracker:
    """Records concurrent stage occupancy and cross-stage overlap."""

    def __init__(self, work_secs=0.0):
        self.work_secs = work_secs
        self.lock = threading.Lock()
        self.coder_active = 0
        self.tester_active = 0
        self.max_coder = 0
        self.max_tester = 0
        self.saw_overlap = False  # a coder and tester active simultaneously

    def code(self, ticket, attempt):
        with self.lock:
            self.coder_active += 1
            self.max_coder = max(self.max_coder, self.coder_active)
            if self.tester_active > 0:
                self.saw_overlap = True
        time.sleep(self.work_secs)
        with self.lock:
            self.coder_active -= 1
        return StageResult.PASS

    def test(self, ticket, attempt):
        with self.lock:
            self.tester_active += 1
            self.max_tester = max(self.max_tester, self.tester_active)
            if self.coder_active > 0:
                self.saw_overlap = True
        time.sleep(self.work_secs)
        with self.lock:
            self.tester_active -= 1
        return StageResult.PASS


# ── AC1: setting exists, defaults False ──────────────────────────────────────

def test_ac1_default_is_serial():
    """No sprint/project setting and no env → serial (False)."""
    assert pipeline_mode_enabled(env={}) is False


def test_ac1_per_sprint_scope_enables():
    """A per-sprint setting of True enables pipeline mode."""
    assert pipeline_mode_enabled(sprint_setting=True, env={}) is True


def test_ac1_per_project_scope_enables():
    """A per-project setting of True enables pipeline mode when no sprint setting."""
    assert pipeline_mode_enabled(project_setting=True, env={}) is True


def test_ac1_sprint_overrides_project():
    """Per-sprint scope takes precedence over per-project scope."""
    assert pipeline_mode_enabled(sprint_setting=False, project_setting=True, env={}) is False
    assert pipeline_mode_enabled(sprint_setting=True, project_setting=False, env={}) is True


def test_ac1_schema_field_present_and_default_false():
    """pipeline_mode is a known non-secret setting defaulting to False."""
    from services.sprint_manager.settings_schema import (
        KNOWN_FIELDS,
        NON_SECRET_FIELDS,
        build_effective_response,
    )
    assert "pipeline_mode" in KNOWN_FIELDS
    assert KNOWN_FIELDS["pipeline_mode"]["default"] is False
    assert "pipeline_mode" in NON_SECRET_FIELDS
    eff = build_effective_response({})
    assert eff["pipeline_mode"] is False


def test_ac1_sprint_config_field_default_false():
    """SprintConfig carries a pipeline_mode field defaulting to False."""
    from services.sprint_manager.sprint_manager import SprintConfig
    assert SprintConfig().pipeline_mode is False


# ── AC2: kill-switch forces serial ───────────────────────────────────────────

def test_ac2_kill_switch_forces_serial_over_sprint():
    env = {PIPELINE_KILL_SWITCH_ENV: "1"}
    assert pipeline_mode_enabled(sprint_setting=True, env=env) is False


def test_ac2_kill_switch_forces_serial_over_project():
    env = {PIPELINE_KILL_SWITCH_ENV: "true"}
    assert pipeline_mode_enabled(project_setting=True, env=env) is False


def test_ac2_kill_switch_falsey_does_not_force():
    env = {PIPELINE_KILL_SWITCH_ENV: "0"}
    assert pipeline_mode_enabled(sprint_setting=True, env=env) is True


# ── AC3: exactly one coder + one tester concurrent, never more ───────────────

def test_ac3_at_most_one_coder_and_one_tester():
    tracker = _ConcurrencyTracker(work_secs=0.01)
    run_level(
        list(range(10)), tracker.code, tracker.test, pipeline=True,
    )
    assert tracker.max_coder == 1, "more than one coder ran concurrently"
    assert tracker.max_tester == 1, "more than one tester ran concurrently"


# ── AC4 / AC1 overlap: coder works ahead while tester validates ──────────────

def test_ac4_coder_and_tester_overlap_in_pipeline():
    """In pipeline mode the coder advances while the tester validates."""
    tracker = _ConcurrencyTracker(work_secs=0.02)
    run_level(list(range(6)), tracker.code, tracker.test, pipeline=True)
    assert tracker.saw_overlap is True


def test_ac4_serial_never_overlaps():
    tracker = _ConcurrencyTracker(work_secs=0.01)
    run_level(list(range(6)), tracker.code, tracker.test, pipeline=False)
    assert tracker.saw_overlap is False


def test_ac4_tester_queue_fed_by_coder_completions():
    """Every merged ticket was coded before it was tested (coder feeds tester)."""
    order = []
    lock = threading.Lock()

    def code(ticket, attempt):
        with lock:
            order.append(("coder", ticket))
        return StageResult.PASS

    def test(ticket, attempt):
        with lock:
            order.append(("tester", ticket))
        return StageResult.PASS

    res = run_level([1, 2, 3], code, test, pipeline=True)
    assert sorted(res.merged) == [1, 2, 3]
    # For each ticket the coder entry precedes the tester entry.
    for t in [1, 2, 3]:
        ci = order.index(("coder", t))
        ti = order.index(("tester", t))
        assert ci < ti


# ── AC5: pipeline wall-clock ~ half of serial ────────────────────────────────

def test_ac5_pipeline_roughly_halves_wallclock():
    n = 10
    stage = 0.02

    def code(ticket, attempt):
        time.sleep(stage)
        return StageResult.PASS

    def test(ticket, attempt):
        time.sleep(stage)
        return StageResult.PASS

    t0 = time.monotonic()
    run_level(list(range(n)), code, test, pipeline=False)
    serial = time.monotonic() - t0

    t0 = time.monotonic()
    run_level(list(range(n)), code, test, pipeline=True)
    parallel = time.monotonic() - t0

    # Serial ~= n*2*stage; pipeline ~= (n+1)*stage. Allow generous slack for
    # scheduling jitter but still prove a clear, roughly-half speedup.
    assert parallel < serial * 0.7, f"serial={serial:.3f} parallel={parallel:.3f}"


# ── AC6: hard level barrier ──────────────────────────────────────────────────

def test_ac6_level_barrier_blocks_until_level_complete():
    """run_level does not return until every ticket in the level is terminal,
    so a caller iterating levels cannot start level N+1 early."""
    levels = [[1, 2, 3], [4, 5, 6]]
    started = []
    lock = threading.Lock()

    def code(ticket, attempt):
        with lock:
            started.append(ticket)
        return StageResult.PASS

    for lvl in levels:
        before = set(started)
        run_level(lvl, code, _always_pass_test, pipeline=True)
        # When level N returns, no ticket from a later level has started yet
        # for the duration of this level — verified by the fact that only this
        # level's tickets were added during its run.
        added = [t for t in started if t not in before]
        assert set(added) == set(lvl)
    assert started.count != 0
    # All level-1 tickets appear before any level-2 ticket.
    first_level2 = min(started.index(t) for t in levels[1])
    last_level1 = max(started.index(t) for t in levels[0])
    assert last_level1 < first_level2


# ── AC7: rejection pushes ticket to FRONT of coder queue ─────────────────────

def test_ac7_rejection_requeues_to_front_serial():
    """A rejected ticket is retried before any unstarted ticket."""
    reject_once = {2}
    coder_order = []

    def code(ticket, attempt):
        coder_order.append(ticket)
        return StageResult.PASS

    def test(ticket, attempt):
        if ticket in reject_once:
            reject_once.discard(ticket)
            return StageResult.REJECT
        return StageResult.PASS

    res = run_level([1, 2, 3], code, test, pipeline=False)
    # Sequence: code 1, test 1 pass, code 2, test 2 reject -> front,
    # so 2 is coded again before 3.
    assert coder_order == [1, 2, 2, 3]
    assert sorted(res.merged) == [1, 2, 3]


def test_ac7_rejected_ticket_retried_before_unstarted_pipeline():
    """In pipeline mode the rejected ticket is re-coded (front of queue)."""
    reject_once = {1}
    lock = threading.Lock()
    coder_runs = []

    def code(ticket, attempt):
        with lock:
            coder_runs.append((ticket, attempt))
        return StageResult.PASS

    def test(ticket, attempt):
        with lock:
            do_reject = ticket in reject_once
            if do_reject:
                reject_once.discard(ticket)
        if do_reject:
            return StageResult.REJECT
        return StageResult.PASS

    res = run_level([1, 2], code, test, pipeline=True)
    assert sorted(res.merged) == [1, 2]
    # Ticket 1 was coded twice (initial + the fix attempt after rejection).
    assert sum(1 for t, _ in coder_runs if t == 1) == 2


# ── AC8: retry cap → needs-rework, dropped, pipeline keeps flowing ───────────

def test_ac8_three_rejections_marks_needs_rework():
    rework = []

    def test_reject(ticket, attempt):
        return StageResult.REJECT  # always reject

    res = run_level(
        [99], _always_pass_code, test_reject, pipeline=False,
        on_needs_rework=rework.append,
    )
    assert res.needs_rework == [99]
    assert rework == [99]
    assert res.attempts[99] == DEFAULT_MAX_ATTEMPTS  # exactly 3 coder attempts


def test_ac8_capped_ticket_dropped_without_blocking_others():
    """A ticket that exhausts retries is dropped; remaining tickets still merge."""
    rework = []

    def test_fn(ticket, attempt):
        if ticket == 1:
            return StageResult.REJECT  # ticket 1 never passes
        return StageResult.PASS

    res = run_level(
        [1, 2, 3], _always_pass_code, test_fn, pipeline=True,
        on_needs_rework=rework.append,
    )
    assert res.needs_rework == [1]
    assert sorted(res.merged) == [2, 3]
    assert res.attempts[1] == DEFAULT_MAX_ATTEMPTS


# ── AC9 / AC10: identical per-ticket side effects + order across modes ────────

def _record_run(pipeline):
    """Run a deterministic scenario and return the per-ticket call order."""
    calls = []
    lock = threading.Lock()
    reject_once = {2}

    def code(ticket, attempt):
        with lock:
            calls.append(("code", ticket, attempt))
        return StageResult.PASS

    def test(ticket, attempt):
        with lock:
            do_reject = ticket in reject_once
            if do_reject:
                reject_once.discard(ticket)
        with lock:
            calls.append(("test", ticket, attempt))
        return StageResult.REJECT if do_reject else StageResult.PASS

    merged = []
    res = run_level(
        [1, 2, 3], code, test, pipeline=pipeline,
        on_merged=merged.append,
    )
    return calls, sorted(res.merged), sorted(merged)


def test_ac9_per_ticket_side_effects_identical_across_modes():
    serial_calls, serial_merged, serial_cb = _record_run(pipeline=False)
    # Per-ticket call multiset is identical regardless of interleaving.
    pipe_calls, pipe_merged, pipe_cb = _record_run(pipeline=True)
    assert sorted(serial_calls) == sorted(pipe_calls)
    assert serial_merged == pipe_merged == [1, 2, 3]
    assert serial_cb == pipe_cb == [1, 2, 3]
    # Each ticket: its coder attempt precedes its tester attempt (same order).
    for calls in (serial_calls, pipe_calls):
        for t in [1, 2, 3]:
            for a in range(1, 3):
                if ("code", t, a) in calls and ("test", t, a) in calls:
                    assert calls.index(("code", t, a)) < calls.index(("test", t, a))


# ── Integration: the real _run_pipeline_dispatch driver in sprint_manager ────

def _build_dispatch(monkeypatch, tmp_path, *, tester_behavior):
    """Stub all agent/IO calls and return (sm, state, summary, run_driver).

    `tester_behavior(num, attempt)` returns (merged, summary_line, gate_category).
    """
    import services.sprint_manager.sprint_manager as sm

    # Stub every external side effect to a no-op / deterministic value.
    monkeypatch.setattr(sm, "_dispatch_coder", lambda num, *a, **k: (True, None))
    monkeypatch.setattr(sm, "_find_feature_branch", lambda num: f"feature/{num}-x")
    monkeypatch.setattr(sm, "_dispatch_tester", lambda num, *a, **k: (0, None))
    monkeypatch.setattr(sm, "_transition_safe", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_neon_ticket_status", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_post_sprint_status", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_emit_sprint_lifecycle_event", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_load_estimate", lambda num: None)
    monkeypatch.setattr(sm, "dispatch_alerts", lambda *a, **k: None)
    monkeypatch.setattr(sm, "record_failure", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_publish_gate_failure_analyses", lambda *a, **k: None)
    monkeypatch.setattr(sm.SprintState, "save", lambda self, p: None)

    monkeypatch.setattr(sm, "handle_post_tester", tester_behavior)

    return sm


def _make_state(sm, nums):
    issues = [sm.IssueState(number=n, title=f"t{n}") for n in nums]
    state = sm.SprintState(sprint_label="sprint-test", sprint_number=999, issues=issues)
    summary = sm.SprintSummary()
    return state, summary


def _run_driver(sm, state, summary, levels):
    dispatch_levels = [[state.issues[i] for i in lvl] for lvl in levels]
    level_nums = [[state.issues[i].number for i in lvl] for lvl in levels]
    sm._run_pipeline_dispatch(
        state=state, state_path="/tmp/ignored.json", summary=summary,
        dispatch_levels=dispatch_levels, level_nums_by_idx=level_nums,
        label="sprint-test", sprint_num=None, eff_repo="o/r", api_url=None,
        target_branch="sprint/sprint-test", sprint_branch="sprint/sprint-test",
        alert_modes=[], cfg=None, run_id="run-x",
        eff_sprints_dir=None, rerun_decisions={},
        skip_gates=False, gate_pytest=True, gate_lint=True, gate_merge_preview=True,
        gate_typecheck=True, gate_design=True, gate_frontend_lint=True, gate_scope="changed",
        resume=False, retry_failed=False,
    )


def test_ac4_driver_all_pass_merges_all(monkeypatch, tmp_path):
    """The real driver merges every ticket and marks them done (AC4)."""
    sm = _build_dispatch(monkeypatch, tmp_path,
                         tester_behavior=lambda **k: (True, "merged", None))
    state, summary = _make_state(sm, [101, 102, 103, 104])
    _run_driver(sm, state, summary, [[0, 1, 2, 3]])
    assert sorted(summary.merged) == ["#101", "#102", "#103", "#104"]
    assert all(i.status == "done" for i in state.issues)


def test_ac8_driver_needs_rework_after_three_rejections(monkeypatch, tmp_path):
    """A ticket rejected on every attempt is dropped as needs-rework while the
    rest of the level still merges (AC8)."""
    LOGIC = next(iter(sm_logic_categories()))

    def tester(*, issue_num, **k):
        if issue_num == 201:
            return (False, "gate failed: logic", LOGIC)
        return (True, "merged", None)

    sm = _build_dispatch(monkeypatch, tmp_path, tester_behavior=tester)
    state, summary = _make_state(sm, [201, 202, 203])
    _run_driver(sm, state, summary, [[0, 1, 2]])

    assert sorted(summary.merged) == ["#202", "#203"]
    bad = next(i for i in state.issues if i.number == 201)
    assert bad.status == "skipped"
    assert any("#201" in s for s in summary.skipped)
    # 201 saw exactly 3 tester attempts (the retry cap).
    assert bad.tester_attempt_count == 3


def test_ac6_driver_level_barrier(monkeypatch, tmp_path):
    """Level N+1 tickets only start after level N fully merges (AC6)."""
    coder_order = []

    sm = _build_dispatch(monkeypatch, tmp_path,
                         tester_behavior=lambda **k: (True, "merged", None))

    def coder(num, *a, **k):
        coder_order.append(num)
        return (True, None)

    monkeypatch.setattr(sm, "_dispatch_coder", coder)
    state, summary = _make_state(sm, [301, 302, 303, 304])
    _run_driver(sm, state, summary, [[0, 1], [2, 3]])

    last_l1 = max(coder_order.index(n) for n in (301, 302))
    first_l2 = min(coder_order.index(n) for n in (303, 304))
    assert last_l1 < first_l2
    assert sorted(summary.merged) == ["#301", "#302", "#303", "#304"]


def sm_logic_categories():
    import services.sprint_manager.sprint_manager as sm
    return sm._LOGIC_FAILURE_CATEGORIES


def test_ac10_serial_is_strictly_ordered_and_sequential():
    """pipeline=False processes tickets strictly one at a time, in order."""
    calls, merged, _ = _record_run(pipeline=False)
    # Strict serial order: code1,test1, code2,test2(reject), code2,test2, code3,test3
    expected = [
        ("code", 1, 1), ("test", 1, 1),
        ("code", 2, 1), ("test", 2, 1),
        ("code", 2, 2), ("test", 2, 2),
        ("code", 3, 1), ("test", 3, 1),
    ]
    assert calls == expected
    assert merged == [1, 2, 3]
