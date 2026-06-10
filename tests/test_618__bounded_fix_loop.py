"""Tests for #618: bounded fix-loop for logic failures in sprint_manager.

AC items verified:
  AC-1  sprint_manager.py per-issue loop wraps coder→gates in for attempt in range(K)
        where K = COMMANDER_MAX_FIX_ROUNDS (default 3)
  AC-2  On logic failure: record_failure called and _dispatch_coder re-invoked with
        accumulated failure context from all prior attempts
  AC-3  Gates re-run after each re-dispatch; gate-pass exits loop immediately
  AC-4  Infra failures (CRASH, HANG, RETRY_EXHAUSTED) bypass fix-loop unchanged
  AC-5  Two consecutive identical failure signatures → early abort before K
  AC-6  After exhausting K rounds (or early-abort): needs-rework with
        FailureCategory.RETRY_EXHAUSTED and full per-attempt failure history
  AC-7  COMMANDER_MAX_FIX_ROUNDS env var respected at runtime; changing requires no
        code change
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_cfg(tmp_path: Path) -> MagicMock:
    """Minimal SprintConfig stub."""
    cfg = MagicMock()
    cfg.worktree_coder = tmp_path
    cfg.worktree_tester = tmp_path
    cfg.worktree_tester_app = tmp_path
    cfg.repo_name = None
    cfg.api_url = None
    cfg.coder_prompt_template = None
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg.sprints_dir = tmp_path / "sprints"
    cfg.sprints_dir.mkdir(parents=True, exist_ok=True)
    cfg.app_default_port = None
    cfg.documentor_enabled = False
    return cfg


def _run_sprint_with_mocks(
    tmp_path: Path,
    *,
    coder_sequence: list[tuple[bool, str | None]],
    gate_sequence: list[tuple[bool, str, str | None]],
    tester_rc: int = 0,
    env_overrides: dict | None = None,
) -> tuple[sm.SprintSummary, list, list]:
    """Run run_sprint for issue #1 with controlled coder/gate sequences.

    coder_sequence: list of (ok, category) returned by _dispatch_coder per call.
    gate_sequence:  list of (merged, summary_line, gate_category) returned by
                    handle_post_tester per call.

    Returns (summary, transition_calls, record_failure_calls).
    """
    coder_iter    = iter(coder_sequence)
    gate_iter     = iter(gate_sequence)
    dispatch_calls: list = []          # (attempt_num, prior_failures)
    transition_calls: list = []
    record_calls: list = []

    def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                   repo_name=None, cfg=None, chosen_port=None,
                   rate_limit_events=None, on_running=None, sprint_label=None,
                   prior_failures=None):
        if on_running:
            on_running()
        dispatch_calls.append(prior_failures)
        return next(coder_iter)

    def fake_tester(issue_num, alert_modes, sprint_branch="develop",
                    repo_name=None, cfg=None, chosen_port=None,
                    rate_limit_events=None, on_running=None, sprint_label=None,
                    pre_dispatch_risk=None):
        if on_running:
            on_running()
        return tester_rc, None

    def fake_gates(**kwargs):
        return next(gate_iter)

    def fake_record(issue_num, failure_class, detail, repo_root=None,
                    summary=None, files_to_inspect=None):
        record_calls.append({"issue": issue_num, "class": failure_class, "detail": detail})
        return None

    def fake_transition(issue_num, target, actor=None, repo_name=None):
        transition_calls.append((issue_num, target))

    env = {k: v for k, v in os.environ.items()}
    env.pop("COMMANDER_MAX_FIX_ROUNDS", None)
    if env_overrides:
        env.update(env_overrides)

    with (
        patch.object(sm, "_create_sprint_branch", lambda b: None),
        patch.object(sm, "list_backlog_issues",
                     lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
        patch.object(sm, "_dispatch_coder", fake_coder),
        patch.object(sm, "_dispatch_tester", fake_tester),
        patch.object(sm, "handle_post_tester", fake_gates),
        patch.object(sm, "_post_sprint_status", lambda *a, **kw: None),
        patch.object(sm, "_neon_ticket_status", lambda *a, **kw: None),
        patch.object(sm, "_warn_file_conflicts", lambda i: None),
        patch.object(sm, "_setup_pid_file", lambda n: None),
        patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"),
        patch.object(sm, "_transition_safe", fake_transition),
        patch.object(sm, "record_failure", fake_record),
        patch.object(sm, "dispatch_alerts", lambda *a, **kw: None),
        patch.object(sm, "_design_docs_guard", lambda p: None),
        patch.dict(os.environ, env, clear=True),
    ):
        summary, state = sm.run_sprint(
            label="sprint-99",
            skip_gates=False,
            gate_pytest=False,
            gate_lint=False,
            gate_merge_preview=False,
            gate_typecheck=False,
            gate_design=False,
        )

    return summary, transition_calls, record_calls, dispatch_calls


# ---------------------------------------------------------------------------
# AC-1: fix-loop runs up to K attempts and respects default K=3
# ---------------------------------------------------------------------------

class TestAC1FixLoopBoundary:
    def test_coder_dispatched_up_to_k_times_on_logic_failure(self, tmp_path):
        """With K=3 and alternating logic failures (no dup), coder dispatched 3 times."""
        # Alternate categories so consecutive-dup guard never fires
        seq = [
            (False, sm.FailureCategory.PYTEST_FAIL),
            (False, sm.FailureCategory.LINT_FAIL),
            (False, sm.FailureCategory.PYTEST_FAIL),
        ]
        summary, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=seq,
            gate_sequence=[],
        )
        assert len(dispatch_calls) == 3, (
            f"Expected 3 coder dispatch calls (K=3 alternating), got {len(dispatch_calls)}"
        )

    def test_fix_loop_exits_after_gate_pass_before_k(self, tmp_path):
        """If gate passes on attempt 1, coder dispatched only once (not K times)."""
        summary, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[(True, None)],
            gate_sequence=[(True, "merged", None)],
        )
        assert len(dispatch_calls) == 1, (
            f"Gate passed on first attempt; coder should run once, got {len(dispatch_calls)}"
        )
        assert summary.merged, "Issue should be in merged list on gate-pass"

    def test_default_k_is_3_without_env(self, tmp_path):
        """Default K=3 when COMMANDER_MAX_FIX_ROUNDS not set (alternating to avoid dup abort)."""
        seq = [
            (False, sm.FailureCategory.LINT_FAIL),
            (False, sm.FailureCategory.PYTEST_FAIL),
            (False, sm.FailureCategory.LINT_FAIL),
        ]
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=seq,
            gate_sequence=[],
        )
        assert len(dispatch_calls) == 3


# ---------------------------------------------------------------------------
# AC-2: logic failure → record_failure + re-dispatch with accumulated context
# ---------------------------------------------------------------------------

class TestAC2LogicFailureRetry:
    def test_record_failure_called_on_logic_failure_before_retry(self, tmp_path):
        """record_failure must be called on each logic failure before re-dispatch."""
        logic_fail = (False, sm.FailureCategory.PYTEST_FAIL)
        _, _, record_calls, _ = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[logic_fail, logic_fail, logic_fail],
            gate_sequence=[],
        )
        assert len(record_calls) >= 1, "record_failure must be called on logic failure"
        for rc in record_calls:
            assert rc["class"] in sm._LOGIC_FAILURE_CATEGORIES | {sm.FailureCategory.RETRY_EXHAUSTED}

    def test_second_dispatch_receives_prior_failure_context(self, tmp_path):
        """On re-dispatch (attempt 2+), prior_failures must be non-empty."""
        logic_fail = (False, sm.FailureCategory.PYTEST_FAIL)
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[logic_fail, (True, None)],
            gate_sequence=[(True, "merged", None)],
        )
        assert len(dispatch_calls) == 2
        assert dispatch_calls[0] is None or dispatch_calls[0] == [], (
            "First attempt should have no prior_failures"
        )
        assert dispatch_calls[1], (
            "Second attempt must receive non-empty accumulated failure context"
        )

    def test_gate_logic_failure_triggers_coder_redispatch(self, tmp_path):
        """Gate logic failure (e.g., PYTEST_FAIL from handle_post_tester) triggers re-dispatch."""
        gate_logic_fail = (False, "gate failed: pytest", sm.FailureCategory.PYTEST_FAIL)
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[(True, None), (True, None)],
            gate_sequence=[gate_logic_fail, (True, "merged", None)],
        )
        assert len(dispatch_calls) == 2, (
            f"Gate logic failure must trigger coder re-dispatch; got {len(dispatch_calls)} dispatches"
        )

    def test_all_logic_categories_trigger_retry(self, tmp_path):
        """Every category in _LOGIC_FAILURE_CATEGORIES causes retry, not immediate skip."""
        for cat in sm._LOGIC_FAILURE_CATEGORIES:
            _, _, _, dispatch_calls = _run_sprint_with_mocks(
                tmp_path,
                coder_sequence=[(False, cat), (True, None)],
                gate_sequence=[(True, "merged", None)],
            )
            assert len(dispatch_calls) >= 2, (
                f"Logic failure category {cat} must trigger retry; only {len(dispatch_calls)} dispatch(es)"
            )


# ---------------------------------------------------------------------------
# AC-3: gate-pass exits loop immediately
# ---------------------------------------------------------------------------

class TestAC3GatePassExitsLoop:
    def test_gate_pass_on_attempt_2_exits_without_attempt_3(self, tmp_path):
        """Gate passes on attempt 2 → loop exits; attempt 3 never happens."""
        logic_fail = (False, sm.FailureCategory.LINT_FAIL)
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[logic_fail, (True, None)],
            gate_sequence=[(True, "merged", None)],
        )
        assert len(dispatch_calls) == 2, (
            "Coder dispatched twice (fail then succeed); gate passes → no third dispatch"
        )

    def test_issue_in_merged_after_gate_pass(self, tmp_path):
        """Issue appears in summary.merged when gate passes."""
        logic_fail = (False, sm.FailureCategory.TESTER_REJECTED)
        summary, _, _, _ = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[logic_fail, (True, None)],
            gate_sequence=[(True, "merged", None)],
        )
        assert "#1" in summary.merged or any("1" in m for m in summary.merged)


# ---------------------------------------------------------------------------
# AC-4: infra failures bypass fix-loop
# ---------------------------------------------------------------------------

class TestAC4InfraFailureBypass:
    def test_crash_failure_skips_to_next_issue_immediately(self, tmp_path):
        """CRASH (infra) category: coder dispatched once, no retry."""
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[(False, sm.FailureCategory.CRASH)],
            gate_sequence=[],
        )
        assert len(dispatch_calls) == 1, (
            f"CRASH (infra) must not retry; expected 1 dispatch, got {len(dispatch_calls)}"
        )

    def test_crash_failure_does_not_tag_needs_rework(self, tmp_path):
        """CRASH is infra — must NOT trigger needs-rework transition."""
        _, transition_calls, _, _ = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[(False, sm.FailureCategory.CRASH)],
            gate_sequence=[],
        )
        needs_rework_calls = [
            (n, t) for n, t in transition_calls
            if t is not None and hasattr(t, "value") and "needs" in t.value.lower()
        ]
        assert not needs_rework_calls, (
            "CRASH (infra) must not trigger needs-rework transition"
        )

    def test_hang_tester_bypasses_fix_loop(self, tmp_path):
        """Tester HANG (infra): coder dispatched once, fix-loop not re-entered."""
        coder_iter = iter([(True, None)])
        dispatch_calls2 = []

        def fake_coder2(issue_num, alert_modes, sprint_branch="develop",
                        repo_name=None, cfg=None, chosen_port=None,
                        rate_limit_events=None, on_running=None, sprint_label=None,
                        prior_failures=None):
            if on_running:
                on_running()
            dispatch_calls2.append(prior_failures)
            return next(coder_iter)

        def fake_tester_hang(issue_num, alert_modes, sprint_branch="develop",
                             repo_name=None, cfg=None, chosen_port=None,
                             rate_limit_events=None, on_running=None, sprint_label=None,
                             pre_dispatch_risk=None):
            if on_running:
                on_running()
            return 1, sm.FailureCategory.HANG

        with (
            patch.object(sm, "_create_sprint_branch", lambda b: None),
            patch.object(sm, "list_backlog_issues",
                         lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
            patch.object(sm, "_dispatch_coder", fake_coder2),
            patch.object(sm, "_dispatch_tester", fake_tester_hang),
            patch.object(sm, "_post_sprint_status", lambda *a, **kw: None),
            patch.object(sm, "_neon_ticket_status", lambda *a, **kw: None),
            patch.object(sm, "_warn_file_conflicts", lambda i: None),
            patch.object(sm, "_setup_pid_file", lambda n: None),
            patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"),
            patch.object(sm, "_transition_safe", lambda *a, **kw: None),
            patch.object(sm, "record_failure", lambda *a, **kw: None),
            patch.object(sm, "dispatch_alerts", lambda *a, **kw: None),
            patch.object(sm, "_design_docs_guard", lambda p: None),
        ):
            sm.run_sprint(
                label="sprint-99",
                skip_gates=False,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
            )

        assert len(dispatch_calls2) == 1, (
            f"Tester HANG must not trigger coder re-dispatch; got {len(dispatch_calls2)}"
        )


# ---------------------------------------------------------------------------
# AC-5: consecutive identical failure signature → early abort
# ---------------------------------------------------------------------------

class TestAC5ConsecutiveDupAbort:
    def test_early_abort_on_consecutive_identical_category(self, tmp_path):
        """Two consecutive PYTEST_FAIL from coder → loop aborts after 2, not K=3."""
        logic_fail = (False, sm.FailureCategory.PYTEST_FAIL)
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            # Provide 3 failures; loop should abort after 2 consecutive identical ones
            coder_sequence=[logic_fail, logic_fail, logic_fail],
            gate_sequence=[],
        )
        assert len(dispatch_calls) == 2, (
            f"Consecutive identical failure must abort early after 2 attempts, "
            f"got {len(dispatch_calls)}"
        )

    def test_different_categories_do_not_trigger_early_abort(self, tmp_path):
        """Alternating failure categories do NOT trigger early abort."""
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[
                (False, sm.FailureCategory.PYTEST_FAIL),
                (False, sm.FailureCategory.LINT_FAIL),
                (False, sm.FailureCategory.PYTEST_FAIL),
            ],
            gate_sequence=[],
        )
        assert len(dispatch_calls) == 3, (
            f"Alternating failure categories must exhaust K=3; got {len(dispatch_calls)}"
        )

    def test_early_abort_after_gate_consecutive_same_failure(self, tmp_path):
        """Two consecutive identical gate failures trigger early abort."""
        gate_fail = (False, "gate failed", sm.FailureCategory.PYTEST_FAIL)
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[(True, None), (True, None), (True, None)],
            gate_sequence=[gate_fail, gate_fail, gate_fail],
        )
        assert len(dispatch_calls) == 2, (
            f"Consecutive identical gate failures must abort early at 2; "
            f"got {len(dispatch_calls)}"
        )


# ---------------------------------------------------------------------------
# AC-6: exhausted → needs-rework with RETRY_EXHAUSTED + failure history
# ---------------------------------------------------------------------------

class TestAC6RetryExhausted:
    def test_needs_rework_transition_after_exhaustion(self, tmp_path):
        """After K rounds without gate-pass, _transition_safe called with NEEDS_REWORK."""
        logic_fail = (False, sm.FailureCategory.PYTEST_FAIL)
        _, transition_calls, _, _ = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[logic_fail, logic_fail, logic_fail],
            gate_sequence=[],
        )
        # Check that NEEDS_REWORK was called (after early-abort due to consecutive dups)
        needs_rework = [
            (n, t) for n, t in transition_calls
            if t is not None and hasattr(t, "value")
            and "needs" in str(t.value).lower()
        ]
        assert needs_rework, (
            "NEEDS_REWORK transition must fire after fix-loop exhaustion/early-abort"
        )

    def test_needs_rework_not_called_on_gate_pass(self, tmp_path):
        """No NEEDS_REWORK transition when gate passes."""
        _, transition_calls, _, _ = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[(True, None)],
            gate_sequence=[(True, "merged", None)],
        )
        needs_rework = [
            (n, t) for n, t in transition_calls
            if t is not None and hasattr(t, "value")
            and "needs" in str(t.value).lower()
        ]
        assert not needs_rework, "NEEDS_REWORK must NOT fire when gate passes"

    def test_issue_in_skipped_after_exhaustion(self, tmp_path):
        """After exhaustion, issue appears in summary.skipped, not summary.merged."""
        logic_fail = (False, sm.FailureCategory.LINT_FAIL)
        summary, _, _, _ = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[logic_fail, logic_fail, logic_fail],
            gate_sequence=[],
        )
        assert not summary.merged, "Exhausted issue must NOT appear in merged"
        assert any("1" in s for s in summary.skipped), (
            "Exhausted issue must appear in skipped"
        )

    def test_early_abort_also_tags_needs_rework(self, tmp_path):
        """Early-abort (consecutive dup) also triggers NEEDS_REWORK."""
        logic_fail = (False, sm.FailureCategory.PYTEST_FAIL)
        _, transition_calls, _, _ = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[logic_fail, logic_fail],
            gate_sequence=[],
        )
        needs_rework = [
            (n, t) for n, t in transition_calls
            if t is not None and hasattr(t, "value")
            and "needs" in str(t.value).lower()
        ]
        assert needs_rework, (
            "Early-abort (consecutive dup) must also trigger NEEDS_REWORK"
        )


# ---------------------------------------------------------------------------
# AC-7: COMMANDER_MAX_FIX_ROUNDS env var
# ---------------------------------------------------------------------------

class TestAC7EnvVar:
    def test_k1_limits_to_single_attempt(self, tmp_path):
        """COMMANDER_MAX_FIX_ROUNDS=1 → only 1 coder dispatch even on logic failure."""
        logic_fail = (False, sm.FailureCategory.PYTEST_FAIL)
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=[logic_fail, logic_fail, logic_fail],
            gate_sequence=[],
            env_overrides={"COMMANDER_MAX_FIX_ROUNDS": "1"},
        )
        assert len(dispatch_calls) == 1, (
            f"COMMANDER_MAX_FIX_ROUNDS=1 must limit to 1 attempt; got {len(dispatch_calls)}"
        )

    def test_k2_allows_two_attempts(self, tmp_path):
        """COMMANDER_MAX_FIX_ROUNDS=2 → at most 2 dispatches."""
        logic_fail = (False, sm.FailureCategory.LINT_FAIL)
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            # 3 ready to serve but K=2 so only 2 consumed
            coder_sequence=[logic_fail, logic_fail, logic_fail],
            gate_sequence=[],
            env_overrides={"COMMANDER_MAX_FIX_ROUNDS": "2"},
        )
        assert len(dispatch_calls) == 2, (
            f"COMMANDER_MAX_FIX_ROUNDS=2 must limit to 2 attempts; got {len(dispatch_calls)}"
        )

    def test_k5_allows_five_attempts_on_alternating_failures(self, tmp_path):
        """COMMANDER_MAX_FIX_ROUNDS=5 → up to 5 dispatches (no early abort when cats alternate)."""
        # Alternate categories to prevent early abort
        seq = [
            (False, sm.FailureCategory.PYTEST_FAIL),
            (False, sm.FailureCategory.LINT_FAIL),
            (False, sm.FailureCategory.PYTEST_FAIL),
            (False, sm.FailureCategory.LINT_FAIL),
            (False, sm.FailureCategory.PYTEST_FAIL),
        ]
        _, _, _, dispatch_calls = _run_sprint_with_mocks(
            tmp_path,
            coder_sequence=seq,
            gate_sequence=[],
            env_overrides={"COMMANDER_MAX_FIX_ROUNDS": "5"},
        )
        assert len(dispatch_calls) == 5, (
            f"COMMANDER_MAX_FIX_ROUNDS=5 must allow 5 attempts; got {len(dispatch_calls)}"
        )
