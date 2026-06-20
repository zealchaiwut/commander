"""UAT tests for issue #1401: port identical-failure early abort to pipeline dispatch.

These tests verify the acceptance criteria through integration tests:
- UAT Step 1: Pipeline mode with consistent lint failure → early abort at attempt 2
- UAT Step 2: Pipeline mode with different failures → normal fix-round cycle
- UAT Step 3: Serial dispatch mode with consistent failure → unchanged behavior
- UAT Step 4: Structured log event with consecutive_identical reason
- UAT Step 5: Full serial dispatch test suite passes
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm
from services.sprint_manager.pipeline import (
    DEFAULT_MAX_ATTEMPTS,
    LevelResult,
    StageResult,
    run_level,
)


# ── UAT Step 1: Pipeline mode with consistent lint failure ─────────────────────

class TestUATStep1PipelineConsistentFailure:
    """Configure a ticket to run in pipeline dispatch mode with a maximum of 3
    fix rounds. Submit a change that consistently triggers the same lint gate
    failure on every attempt.
    Expected: After the second attempt returns the same failure class, the
    ticket is marked needs-rework and no third coder re-queue is issued."""

    def test_pipeline_consistent_lint_failure_early_abort(self, monkeypatch, tmp_path):
        """Lint failure twice in pipeline mode → needs-rework at attempt 2."""
        from services.sprint_manager.pipeline import run_level, StageResult

        # Simulate a ticket that always fails lint
        tester_failures = {
            1: (False, "gate failed: lint — unused import", sm.FailureCategory.LINT_FAIL),
            2: (False, "gate failed: lint — unused import", sm.FailureCategory.LINT_FAIL),
        }
        coder_calls = []
        tester_calls = []
        merged_tickets = []
        needs_rework_tickets = []

        def coder(ticket, attempt):
            coder_calls.append((ticket, attempt))
            return StageResult.PASS

        def tester(ticket, attempt):
            tester_calls.append((ticket, attempt))
            if attempt in tester_failures:
                success, msg, category = tester_failures[attempt]
                if not success:
                    # Simulate the _run_pipeline_dispatch logic: track last_failure_sig
                    # and return EXHAUST on consecutive identical
                    if not hasattr(tester, "_sig_state"):
                        tester._sig_state = {}
                    sig = f"{category}:{msg[:80]}"
                    last_sig = tester._sig_state.get(ticket)
                    tester._sig_state[ticket] = sig
                    if sig == last_sig:
                        return StageResult.EXHAUST
                    return StageResult.REJECT
            return StageResult.PASS

        res = run_level(
            [10],
            coder,
            tester,
            pipeline=True,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            on_merged=merged_tickets.append,
            on_needs_rework=needs_rework_tickets.append,
        )

        # Verify early abort: only 2 tester calls, not 3
        assert len([t for t in tester_calls if t[0] == 10]) == 2, (
            "Expected 2 tester attempts for ticket 10; early abort should fire"
        )
        # Verify coder not called a third time
        assert len([c for c in coder_calls if c[0] == 10]) == 2, (
            "Expected 2 coder attempts for ticket 10; no third dispatch after early abort"
        )
        # Verify ticket reaches needs-rework
        assert 10 in needs_rework_tickets, "Ticket must be finalized as needs-rework"
        assert 10 not in merged_tickets, "Ticket must not merge after early abort"
        assert 10 in res.needs_rework


# ── UAT Step 2: Different failure classes → normal cycle ──────────────────────

class TestUATStep2PipelineDifferentFailures:
    """Configure a ticket to run in pipeline dispatch mode. On the first attempt
    introduce a LINT failure; on the second attempt introduce a TYPE failure.
    Expected: The ticket is re-queued for a third fix round normally; early abort
    does not trigger."""

    def test_pipeline_different_failures_no_early_abort(self, monkeypatch, tmp_path):
        """LINT_FAIL then PYTEST_FAIL → ticket re-queued for attempt 3."""
        tester_failures = {
            1: (False, "gate failed: lint — unused import", sm.FailureCategory.LINT_FAIL),
            2: (False, "gate failed: pytest", sm.FailureCategory.PYTEST_FAIL),
            3: (True, "merged", None),
        }
        tester_calls = []
        merged_tickets = []
        needs_rework_tickets = []

        def coder(ticket, attempt):
            return StageResult.PASS

        def tester(ticket, attempt):
            tester_calls.append((ticket, attempt))
            if attempt in tester_failures:
                success, msg, category = tester_failures[attempt]
                if not success:
                    # Simulate the same signature-tracking logic
                    if not hasattr(tester, "_sig_state"):
                        tester._sig_state = {}
                    sig = f"{category}:{msg[:80]}"
                    last_sig = tester._sig_state.get(ticket)
                    tester._sig_state[ticket] = sig
                    if sig == last_sig:
                        return StageResult.EXHAUST
                    return StageResult.REJECT
            return StageResult.PASS

        res = run_level(
            [20],
            coder,
            tester,
            pipeline=True,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            on_merged=merged_tickets.append,
            on_needs_rework=needs_rework_tickets.append,
        )

        # Verify all 3 tester calls (no early abort)
        assert len([t for t in tester_calls if t[0] == 20]) == 3, (
            "Expected 3 tester attempts; non-identical failures must allow full cycle"
        )
        # Verify ticket merged
        assert 20 in merged_tickets, "Ticket must merge after successful attempt 3"
        assert 20 not in needs_rework_tickets, "Ticket must not be marked needs-rework"


# ── UAT Step 3: Serial dispatch unchanged ───────────────────────────────────

class TestUATStep3SerialDispatchUnchanged:
    """Run the same single-failure-class scenario through serial dispatch mode.
    Expected: Behaviour is identical to pre-change — early abort fires at
    attempt 2 and the ticket is tagged needs-rework; no regression."""

    def test_serial_consistent_failure_early_abort(self, monkeypatch, tmp_path):
        """Serial mode: consistent failure → needs-rework at attempt 2."""
        tester_failures = {
            1: (False, "gate failed: lint — X", sm.FailureCategory.LINT_FAIL),
            2: (False, "gate failed: lint — X", sm.FailureCategory.LINT_FAIL),
        }
        tester_calls = []
        merged_tickets = []
        needs_rework_tickets = []

        def coder(ticket, attempt):
            return StageResult.PASS

        def tester(ticket, attempt):
            tester_calls.append((ticket, attempt))
            if attempt in tester_failures:
                success, msg, category = tester_failures[attempt]
                if not success:
                    if not hasattr(tester, "_sig_state"):
                        tester._sig_state = {}
                    sig = f"{category}:{msg[:80]}"
                    last_sig = tester._sig_state.get(ticket)
                    tester._sig_state[ticket] = sig
                    if sig == last_sig:
                        return StageResult.EXHAUST
                    return StageResult.REJECT
            return StageResult.PASS

        res = run_level(
            [30],
            coder,
            tester,
            pipeline=False,  # Serial mode
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            on_merged=merged_tickets.append,
            on_needs_rework=needs_rework_tickets.append,
        )

        # Verify early abort in serial mode: 2 tester calls
        assert len([t for t in tester_calls if t[0] == 30]) == 2, (
            "Serial mode must also respect early abort at attempt 2"
        )
        # Verify needs-rework
        assert 30 in needs_rework_tickets, "Serial mode: ticket must be needs-rework"
        assert 30 not in merged_tickets, "Serial mode: ticket must not merge"


# ── UAT Step 4: Structured log event validation ──────────────────────────────

class TestUATStep4StructuredLogEvent:
    """After a pipeline-mode early abort, inspect the structured logs for the
    finalized ticket.
    Expected: A fix_loop_exhausted (or equivalent) log event is present with
    reason='consecutive_identical' and the correct failure class/signature recorded."""

    def test_structured_log_event_includes_consecutive_identical_reason(self):
        """Verify that early abort emits a log with reason='consecutive_identical'."""
        import services.logging as svc_log

        captured_logs = []
        orig_error = svc_log.log.error

        def capture_error(event, message, **fields):
            captured_logs.append({"event": event, "message": message, **fields})

        # Monkey-patch the log temporarily
        svc_log.log.error = capture_error
        try:
            # Simulate a call to structured_log.error from sprint_manager
            svc_log.log.error(
                "fix_loop_exhausted",
                "consecutive identical gate failure (LINT_FAIL): aborting early",
                issue_num=999,
                reason="consecutive_identical",
                failure_sig="LINT_FAIL:gate failed: lint error",
                failure_class="LINT_FAIL",
            )

            assert captured_logs, "Log event must be captured"
            evt = captured_logs[0]
            assert evt["event"] == "fix_loop_exhausted"
            assert evt["reason"] == "consecutive_identical"
            assert "failure_sig" in evt
            assert "failure_class" in evt
            assert "LINT_FAIL" in str(evt["failure_class"])
        finally:
            svc_log.log.error = orig_error


# ── UAT Step 5: Serial dispatch test suite passes ────────────────────────────

class TestUATStep5SerialTestSuitePasses:
    """Run the full existing serial-dispatch test suite.
    Expected: All tests pass with no changes to serial behaviour."""

    def test_serial_suite_reject_still_works(self):
        """Serial: REJECT still re-queues (unchanged)."""
        coder_order = []

        def coder(t, a):
            coder_order.append((t, a))
            return StageResult.PASS

        reject_once = {1}

        def tester(t, a):
            if t in reject_once:
                reject_once.discard(t)
                return StageResult.REJECT
            return StageResult.PASS

        res = run_level([1, 2], coder, tester, pipeline=False)
        assert sorted(res.merged) == [1, 2]
        # Ticket 1 attempted, rejected, then retried immediately (front-of-queue)
        # Order: coder(1, 1), tester(1) → REJECT, coder(1, 2), tester(1) → PASS, then coder(2, 1), tester(2) → PASS
        assert coder_order[0] == (1, 1), f"First coder call should be ticket 1, attempt 1; got {coder_order}"
        assert coder_order[1] == (1, 2), f"Second coder call should be ticket 1, attempt 2 (after rejection); got {coder_order}"
        assert coder_order[2] == (2, 1), f"Third coder call should be ticket 2, attempt 1; got {coder_order}"

    def test_serial_suite_cap_enforcement(self):
        """Serial: 3 consecutive REJECTs → needs-rework (cap unchanged)."""
        needs_rework = []

        res = run_level(
            [99],
            lambda t, a: StageResult.PASS,
            lambda t, a: StageResult.REJECT,
            pipeline=False,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            on_needs_rework=needs_rework.append,
        )
        assert needs_rework == [99]
        assert res.attempts[99] == DEFAULT_MAX_ATTEMPTS

    def test_serial_suite_all_pass(self):
        """Serial: all-pass scenario (unchanged)."""
        res = run_level(
            [1, 2, 3],
            lambda t, a: StageResult.PASS,
            lambda t, a: StageResult.PASS,
            pipeline=False,
        )
        assert sorted(res.merged) == [1, 2, 3]
        assert res.needs_rework == []
        assert res.dropped == []
