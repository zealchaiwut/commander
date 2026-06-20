"""Tests for issue #1401: port identical-failure early abort to pipeline dispatch.

Each test is anchored to a specific acceptance criterion (AC).

AC1  _run_pipeline_dispatch tracks the previous failure signature per ticket
     across fix rounds, analogous to _last_failure_sig in the serial loop.
AC2  On tester REJECT / gate logic failure, the new failure signature is
     compared to the stored previous signature for that ticket.
AC3  When signatures match (consecutive identical failure), the ticket is NOT
     re-queued and is finalized as needs-rework immediately.
AC4  A structured log event (fix_loop_exhausted) is emitted with
     reason: "consecutive_identical" and includes the failure class/signature.
AC5  A ticket that fails the lint gate twice in a row with the same failure
     class reaches needs-rework after two attempts, not three, in pipeline mode.
AC6  Serial dispatch behaviour is unchanged — existing _last_failure_sig logic
     and tests continue to pass.
AC7  Non-identical consecutive failures continue through the normal fix-round
     cycle in pipeline mode.
AC8  Unit/integration test covers the pipeline identical-failure path and
     asserts early termination at attempt 2.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import importlib.util as _iutil
import os as _os
import unittest.mock as _umock

import services.sprint_manager.sprint_manager as sm
from services.sprint_manager.pipeline import (
    DEFAULT_MAX_ATTEMPTS,
    LevelResult,
    StageResult,
    run_level,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _logic_cat():
    """Return any logic failure category."""
    return next(iter(sm._LOGIC_FAILURE_CATEGORIES))


def _build_dispatch(monkeypatch, *, tester_behavior):
    """Stub all agent/IO calls and return the patched sm module."""
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


def _make_state(sm_mod, nums):
    issues = [sm_mod.IssueState(number=n, title=f"t{n}") for n in nums]
    state = sm_mod.SprintState(
        sprint_label="sprint-test", sprint_number=999, issues=issues
    )
    summary = sm_mod.SprintSummary()
    return state, summary


def _run_driver(sm_mod, state, summary, levels):
    dispatch_levels = [[state.issues[i] for i in lvl] for lvl in levels]
    level_nums = [[state.issues[i].number for i in lvl] for lvl in levels]
    sm_mod._run_pipeline_dispatch(
        state=state,
        state_path="/tmp/ignored.json",
        summary=summary,
        dispatch_levels=dispatch_levels,
        level_nums_by_idx=level_nums,
        label="sprint-test",
        sprint_num=None,
        eff_repo="o/r",
        api_url=None,
        target_branch="sprint/sprint-test",
        sprint_branch="sprint/sprint-test",
        alert_modes=[],
        cfg=None,
        run_id="run-x",
        eff_sprints_dir=None,
        rerun_decisions={},
        skip_gates=False,
        gate_pytest=True,
        gate_lint=True,
        gate_merge_preview=True,
        gate_typecheck=True,
        gate_design=True,
        gate_frontend_lint=True,
        gate_monolith=True,
        gate_scope="changed",
        resume=False,
        retry_failed=False,
    )


# ── AC8: pipeline.StageResult.EXHAUST unit test ───────────────────────────────

class TestAC8PipelineExhaustUnitTest:
    """Unit tests for pipeline.StageResult.EXHAUST — the new signal that causes
    immediate needs-rework without checking the attempt cap."""

    def test_exhaust_variant_exists(self):
        """StageResult has an EXHAUST variant (added for early-abort path)."""
        assert hasattr(StageResult, "EXHAUST"), (
            "StageResult must have EXHAUST variant for pipeline early-abort"
        )

    def test_exhaust_serial_goes_to_needs_rework_immediately(self):
        """Serial mode: EXHAUST from test_fn → needs_rework even on attempt 1."""
        rework = []
        calls = []

        def test_fn(ticket, attempt):
            calls.append(attempt)
            return StageResult.EXHAUST

        res = run_level(
            [42], lambda t, a: StageResult.PASS, test_fn, pipeline=False,
            on_needs_rework=rework.append,
        )
        assert rework == [42], "EXHAUST must immediately finalize as needs-rework"
        assert 42 in res.needs_rework
        assert 42 not in res.merged
        assert 42 not in res.dropped
        assert calls == [1], "test_fn called exactly once — EXHAUST does not re-queue"

    def test_exhaust_pipeline_goes_to_needs_rework_immediately(self):
        """Pipeline mode: EXHAUST from test_fn → needs_rework, ticket does not
        re-enter the coder queue for a third attempt."""
        rework = []
        coder_calls = []

        def code_fn(ticket, attempt):
            coder_calls.append((ticket, attempt))
            return StageResult.PASS

        def test_fn(ticket, attempt):
            return StageResult.EXHAUST

        res = run_level(
            [10], code_fn, test_fn, pipeline=True,
            on_needs_rework=rework.append,
        )
        assert rework == [10], "EXHAUST must finalize as needs-rework in pipeline mode"
        assert 10 in res.needs_rework
        assert 10 not in res.merged
        # Coder ran only once — EXHAUST must not trigger a second coder dispatch.
        assert len([c for c in coder_calls if c[0] == 10]) == 1, (
            "EXHAUST must not re-queue ticket to coder"
        )

    def test_exhaust_pipeline_other_tickets_still_process(self):
        """EXHAUST on one ticket must not block processing of other tickets."""
        rework = []
        merged = []
        exhaust_ticket = {1}

        def test_fn(ticket, attempt):
            if ticket in exhaust_ticket:
                return StageResult.EXHAUST
            return StageResult.PASS

        res = run_level(
            [1, 2, 3], lambda t, a: StageResult.PASS, test_fn, pipeline=True,
            on_merged=merged.append, on_needs_rework=rework.append,
        )
        assert rework == [1]
        assert sorted(merged) == [2, 3]
        assert sorted(res.merged) == [2, 3]

    def test_exhaust_not_counted_against_attempt_cap(self):
        """EXHAUST fires after fewer than max_attempts — before the cap."""
        rework = []
        test_calls = []

        def test_fn(ticket, attempt):
            test_calls.append(attempt)
            return StageResult.EXHAUST  # abort on first test

        run_level(
            [7], lambda t, a: StageResult.PASS, test_fn, pipeline=True,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            on_needs_rework=rework.append,
        )
        assert rework == [7]
        # Only 1 test call, not 3 (cap not reached).
        assert len(test_calls) == 1, (
            f"EXHAUST must abort after 1 test call, not max_attempts; "
            f"got {len(test_calls)}"
        )


# ── AC1/AC2/AC3: integration — early abort on consecutive identical failure ───

class TestAC1AC2AC3ConsecutiveIdenticalAbort:
    """Verify that _run_pipeline_dispatch detects consecutive identical failures
    and finalises the ticket as needs-rework without a third coder dispatch."""

    def test_consecutive_identical_gate_failure_aborts_early(self, monkeypatch, tmp_path):
        """Ticket rejected twice with the same category+summary → needs-rework
        after 2 tester attempts, not 3 (pipeline mode)."""
        LOGIC = _logic_cat()
        tester_calls = []

        def tester(*, issue_num, **k):
            tester_calls.append(issue_num)
            return (False, "gate failed: lint error X", LOGIC)

        sm_mod = _build_dispatch(monkeypatch, tester_behavior=tester)
        state, summary = _make_state(sm_mod, [501])
        _run_driver(sm_mod, state, summary, [[0]])

        issue = state.issues[0]
        assert issue.status == "skipped", "ticket must be skipped (needs-rework)"
        assert any("#501" in s for s in summary.skipped), "ticket must appear in skipped"
        assert "#501" not in summary.merged
        # Exactly 2 tester calls — early abort fires after 2, not 3.
        assert tester_calls.count(501) == 2, (
            f"Expected 2 tester attempts (early abort), got {tester_calls.count(501)}"
        )

    def test_ticket_not_requeued_after_identical_failure(self, monkeypatch, tmp_path):
        """When early abort fires, no third coder dispatch is issued."""
        LOGIC = _logic_cat()
        coder_calls = []

        real_coder = sm._dispatch_coder

        def counting_coder(num, *a, **k):
            coder_calls.append(num)
            return (True, None)

        monkeypatch.setattr(sm, "_dispatch_coder", counting_coder)

        sm_mod = _build_dispatch(monkeypatch,
                                 tester_behavior=lambda *, issue_num, **k:
                                 (False, "gate failed: same error", _logic_cat()))
        # Re-patch coder (build_dispatch overwrites it)
        monkeypatch.setattr(sm_mod, "_dispatch_coder", counting_coder)

        state, summary = _make_state(sm_mod, [502])
        _run_driver(sm_mod, state, summary, [[0]])

        # Coder dispatched twice (attempt 1 + fix round 2); no third dispatch.
        assert coder_calls.count(502) == 2, (
            f"Expected 2 coder dispatches (no third after early abort), "
            f"got {coder_calls.count(502)}"
        )

    def test_non_logic_failure_is_not_early_aborted(self, monkeypatch, tmp_path):
        """A non-logic gate failure (infra) is dropped immediately; early-abort
        dup detection does not interfere."""
        INFRA = sm.FailureCategory.CRASH
        tester_calls = []

        def tester(*, issue_num, **k):
            tester_calls.append(issue_num)
            return (False, "infra crash", INFRA)

        sm_mod = _build_dispatch(monkeypatch, tester_behavior=tester)
        state, summary = _make_state(sm_mod, [503])
        _run_driver(sm_mod, state, summary, [[0]])

        # Non-logic failure must drop immediately (1 tester call, no rework loop).
        assert tester_calls.count(503) == 1, (
            "Non-logic infra failure must drop immediately, not enter the dup-abort loop"
        )


# ── AC4: structured log event with reason="consecutive_identical" ─────────────

class TestAC4StructuredLogEvent:
    """Verify the fix_loop_exhausted log event is emitted with the right fields
    when the consecutive-identical early abort fires."""

    def test_fix_loop_exhausted_emitted_with_consecutive_identical(
        self, monkeypatch, tmp_path
    ):
        """When two identical gate failures fire, fix_loop_exhausted is logged
        with reason='consecutive_identical' and includes the failure class."""
        LOGIC = _logic_cat()
        log_events = []

        import services.logging as svc_log
        orig_error = svc_log.log.error

        def capture_error(event, message, **fields):
            log_events.append({"event": event, "message": message, **fields})

        monkeypatch.setattr(svc_log.log, "error", capture_error)

        sm_mod = _build_dispatch(
            monkeypatch,
            tester_behavior=lambda *, issue_num, **k:
            (False, "gate failed: lint X", LOGIC),
        )
        state, summary = _make_state(sm_mod, [601])
        _run_driver(sm_mod, state, summary, [[0]])

        exhausted_events = [
            e for e in log_events if e.get("event") == "fix_loop_exhausted"
        ]
        assert exhausted_events, (
            "fix_loop_exhausted log event must be emitted on consecutive identical failure"
        )
        evt = exhausted_events[0]
        assert evt.get("reason") == "consecutive_identical", (
            f"Expected reason='consecutive_identical', got {evt.get('reason')!r}"
        )
        assert "failure_sig" in evt, (
            "fix_loop_exhausted event must include failure_sig field"
        )
        assert "failure_class" in evt, (
            "fix_loop_exhausted event must include failure_class field"
        )
        assert str(LOGIC) in str(evt.get("failure_class", "")), (
            f"failure_class must reference the failure category; got {evt.get('failure_class')!r}"
        )


# ── AC5: lint gate failure twice → needs-rework after two attempts ────────────

class TestAC5LintGateTwiceAbort:
    """A ticket failing the lint gate twice in pipeline mode reaches needs-rework
    after exactly two attempts."""

    def test_lint_fail_twice_pipeline_needs_rework_at_attempt_2(
        self, monkeypatch, tmp_path
    ):
        """LINT_FAIL + same summary twice → needs-rework after attempt 2, not 3."""
        LINT = sm.FailureCategory.LINT_FAIL
        tester_calls = []

        def tester(*, issue_num, **k):
            tester_calls.append(issue_num)
            return (False, "gate failed: lint — unused import", LINT)

        sm_mod = _build_dispatch(monkeypatch, tester_behavior=tester)
        state, summary = _make_state(sm_mod, [701])
        _run_driver(sm_mod, state, summary, [[0]])

        issue = state.issues[0]
        assert issue.status == "skipped", "ticket must reach needs-rework state"
        assert tester_calls.count(701) == 2, (
            f"LINT_FAIL twice must abort after 2 attempts; got {tester_calls.count(701)}"
        )
        assert "#701" not in summary.merged

    def test_lint_fail_twice_other_tickets_still_merge(self, monkeypatch, tmp_path):
        """Early abort on one ticket must not prevent other tickets from merging."""
        LINT = sm.FailureCategory.LINT_FAIL

        def tester(*, issue_num, **k):
            if issue_num == 702:
                return (False, "gate failed: lint error", LINT)
            return (True, "merged", None)

        sm_mod = _build_dispatch(monkeypatch, tester_behavior=tester)
        state, summary = _make_state(sm_mod, [702, 703, 704])
        _run_driver(sm_mod, state, summary, [[0, 1, 2]])

        assert sorted(summary.merged) == ["#703", "#704"]
        assert any("#702" in s for s in summary.skipped)


# ── AC6: serial dispatch behaviour unchanged ──────────────────────────────────

class TestAC6SerialUnchanged:
    """Serial dispatch via pipeline.run_level(pipeline=False) must behave exactly
    as before — the REJECT/needs-rework cap logic is untouched by this change.
    (sprint_manager.py's serial loop was not modified.)"""

    def test_serial_reject_requeues_to_front(self):
        """Serial mode: REJECT still re-queues the ticket before other tickets."""
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
        assert sorted(res.merged) == [1, 2, 3]
        # Ticket 2 rejected once → coded again before ticket 3 (front-of-queue).
        assert coder_order == [1, 2, 2, 3]

    def test_serial_reject_cap_still_triggers_needs_rework(self):
        """Serial mode: 3 consecutive REJECTs → needs-rework (cap unchanged)."""
        rework = []

        res = run_level(
            [99], lambda t, a: StageResult.PASS,
            lambda t, a: StageResult.REJECT, pipeline=False,
            on_needs_rework=rework.append,
        )
        assert rework == [99]
        assert 99 in res.needs_rework
        assert res.attempts[99] == DEFAULT_MAX_ATTEMPTS

    def test_serial_pass_not_affected(self):
        """Serial mode: all-pass scenario is untouched."""
        merged = []
        res = run_level(
            [1, 2, 3], lambda t, a: StageResult.PASS, lambda t, a: StageResult.PASS,
            pipeline=False, on_merged=merged.append,
        )
        assert sorted(merged) == [1, 2, 3]
        assert res.dropped == []
        assert res.needs_rework == []

    def test_serial_exhaust_goes_to_needs_rework_not_drop(self):
        """Serial mode: EXHAUST still results in needs_rework (not dropped),
        confirming the serial path handles the new variant correctly."""
        rework = []
        dropped = []

        res = run_level(
            [5], lambda t, a: StageResult.PASS,
            lambda t, a: StageResult.EXHAUST, pipeline=False,
            on_needs_rework=rework.append, on_dropped=dropped.append,
        )
        assert rework == [5], "EXHAUST must go to needs_rework even in serial mode"
        assert dropped == [], "EXHAUST must NOT go to dropped"


# ── AC7: non-identical consecutive failures continue through fix-round cycle ──

class TestAC7NonIdenticalContinues:
    """Different failure categories on consecutive attempts must NOT trigger the
    early abort in pipeline mode."""

    def test_different_categories_pipeline_no_early_abort(self, monkeypatch, tmp_path):
        """LINT_FAIL then PYTEST_FAIL → ticket re-queued for attempt 3 normally."""
        categories = [
            sm.FailureCategory.LINT_FAIL,
            sm.FailureCategory.PYTEST_FAIL,
        ]
        call_index = {"n": 0}
        tester_calls = []

        def tester(*, issue_num, **k):
            tester_calls.append(issue_num)
            idx = call_index["n"]
            call_index["n"] += 1
            if idx < len(categories):
                return (False, f"gate failed: error {idx}", categories[idx])
            return (True, "merged", None)

        sm_mod = _build_dispatch(monkeypatch, tester_behavior=tester)
        state, summary = _make_state(sm_mod, [801])
        _run_driver(sm_mod, state, summary, [[0]])

        # Ticket eventually merges after 3 tester calls (no early abort).
        assert "#801" in summary.merged, (
            "Non-identical failures must not trigger early abort; ticket should merge"
        )
        assert tester_calls.count(801) == 3, (
            f"Expected 3 tester calls (no early abort); got {tester_calls.count(801)}"
        )

    def test_same_category_different_summary_no_early_abort(self, monkeypatch, tmp_path):
        """Same category but different summary_line must NOT trigger early abort
        — the signature includes the summary excerpt."""
        LINT = sm.FailureCategory.LINT_FAIL
        summaries = ["gate failed: lint — file A", "gate failed: lint — file B"]
        call_index = {"n": 0}
        tester_calls = []

        def tester(*, issue_num, **k):
            tester_calls.append(issue_num)
            idx = call_index["n"]
            call_index["n"] += 1
            if idx < len(summaries):
                return (False, summaries[idx], LINT)
            return (True, "merged", None)

        sm_mod = _build_dispatch(monkeypatch, tester_behavior=tester)
        state, summary = _make_state(sm_mod, [802])
        _run_driver(sm_mod, state, summary, [[0]])

        assert "#802" in summary.merged, (
            "Different summary lines with same category must not early-abort"
        )
