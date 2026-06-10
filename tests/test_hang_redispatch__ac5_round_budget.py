"""Tests for #787 AC5: hang_continue + fix_round dispatches combined ≤ K+1 per ticket."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


def _fake_cfg(tmp_path: Path) -> MagicMock:
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


def _run_sprint_collect_dispatches(
    tmp_path: Path,
    coder_returns: list,
    *,
    max_fix_rounds: int = 3,
    hang_redispatch_env: str = "1",
) -> list[dict]:
    """Run sprint and return list of dispatch info dicts."""
    dispatch_calls: list = []
    coder_iter = iter(coder_returns)

    def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                   repo_name=None, cfg=None, chosen_port=None,
                   rate_limit_events=None, on_running=None, sprint_label=None,
                   prior_failures=None, hang_continuation=None, attempt_kind=None):
        if on_running:
            on_running()
        dispatch_calls.append({
            "attempt_kind": attempt_kind,
            "hang_continuation": hang_continuation,
        })
        try:
            return next(coder_iter)
        except StopIteration:
            return (True, None)

    env = {k: v for k, v in os.environ.items()}
    env["COMMANDER_HANG_REDISPATCH"] = hang_redispatch_env
    env["COMMANDER_MAX_FIX_ROUNDS"] = str(max_fix_rounds)

    with (
        patch.object(sm, "_create_sprint_branch", lambda b: None),
        patch.object(sm, "list_backlog_issues",
                     lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
        patch.object(sm, "_dispatch_coder", fake_coder),
        patch.object(sm, "_dispatch_tester", lambda *a, **kw: (0, None)),
        patch.object(sm, "handle_post_tester",
                     lambda *a, **kw: (True, "merged", None)),
        patch.object(sm, "_post_sprint_status", lambda *a, **kw: None),
        patch.object(sm, "_neon_ticket_status", lambda *a, **kw: None),
        patch.object(sm, "_warn_file_conflicts", lambda i: None),
        patch.object(sm, "_setup_pid_file", lambda n: None),
        patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"),
        patch.object(sm, "_transition_safe", lambda *a, **kw: None),
        patch.object(sm, "record_failure", lambda *a, **kw: None),
        patch.object(sm, "dispatch_alerts", lambda *a, **kw: None),
        patch.object(sm, "_design_docs_guard", lambda p: None),
        patch.object(sm, "_db_agent_start_sm", lambda *a, **kw: None),
        patch.object(sm, "_db_agent_finish_sm", lambda *a, **kw: None),
        patch.object(sm, "_emit_sprint_lifecycle_event", lambda *a, **kw: None),
        patch.object(sm, "_add_blocked_label", lambda *a, **kw: None),
        patch.dict(os.environ, env, clear=True),
    ):
        sm.run_sprint(
            label="sprint-99",
            skip_gates=True,
            gate_pytest=False,
            gate_lint=False,
            gate_merge_preview=False,
            gate_typecheck=False,
            gate_design=False,
            cfg=_fake_cfg(tmp_path),
        )  # returns (summary, state) — ignore both
    return dispatch_calls


class TestAC5RoundBudget:
    def test_hang_continue_counts_against_fix_round_budget(self, tmp_path):
        """hang_continue + fix_round combined must not exceed K+1 (K=3 default)."""
        K = 3
        # Worst case: hang on initial, then K fix_round failures
        # Expect total ≤ K+1 = 4, but since hang_continue takes one slot, ≤ K
        calls = _run_sprint_collect_dispatches(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),        # initial → hang → redispatch
                (False, sm.FailureCategory.PYTEST_FAIL), # hang_continue → logic fail
                (False, sm.FailureCategory.PYTEST_FAIL), # fix_round → logic fail
                (False, sm.FailureCategory.PYTEST_FAIL), # fix_round → logic fail
                (False, sm.FailureCategory.PYTEST_FAIL), # should NOT be reached
            ],
            max_fix_rounds=K,
        )
        # Count non-initial dispatches
        non_initial = [c for c in calls if c["attempt_kind"] != "initial"]
        assert len(non_initial) <= K + 1, (
            f"hang_continue + fix_round combined must not exceed K+1={K+1}; "
            f"got {len(non_initial)} non-initial dispatches: {non_initial}"
        )

    def test_total_dispatches_bounded_with_hang_continue(self, tmp_path):
        """Total coder dispatches (including initial) must be ≤ K+1 with 1 hang."""
        K = 3
        calls = _run_sprint_collect_dispatches(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (False, sm.FailureCategory.PYTEST_FAIL),
                (False, sm.FailureCategory.PYTEST_FAIL),
                (False, sm.FailureCategory.PYTEST_FAIL),
                (False, sm.FailureCategory.PYTEST_FAIL),  # must NOT run
            ],
            max_fix_rounds=K,
        )
        assert len(calls) <= K + 1, (
            f"Total dispatches must be ≤ K+1={K+1}; got {len(calls)}: {calls}"
        )

    def test_hang_continue_followed_by_fix_rounds_exhausted(self, tmp_path):
        """After hang_continue + K-1 fix_rounds exhausted, loop should stop."""
        K = 3
        # Expected sequence: initial(hang) → hang_continue(logic_fail) → fix_round(logic_fail) → fix_round(logic_fail) → STOP
        calls = _run_sprint_collect_dispatches(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (False, sm.FailureCategory.LINT_FAIL),
                (False, sm.FailureCategory.PYTEST_FAIL),
                (False, sm.FailureCategory.LINT_FAIL),
                (True, None),  # should not be reached
            ],
            max_fix_rounds=K,
        )
        # With K=3: initial + hang_continue + 2 fix_rounds = 4 dispatches = K+1
        assert len(calls) <= K + 1, (
            f"With K={K}, dispatches must be ≤ {K+1}; got {len(calls)}"
        )

    def test_k1_with_hang_limits_to_two_dispatches(self, tmp_path):
        """With K=1, a hang on initial gives: initial + hang_continue = 2 dispatches."""
        calls = _run_sprint_collect_dispatches(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (True, None),  # hang_continue succeeds
                (True, None),  # must NOT run
            ],
            max_fix_rounds=1,
        )
        assert len(calls) == 2, (
            f"K=1 + 1 hang → 2 dispatches; got {len(calls)}"
        )

    def test_two_hangs_with_k3_results_in_two_dispatches(self, tmp_path):
        """Two hangs: initial(hang) → hang_continue(hang) → escalate. Only 2 dispatches."""
        calls = _run_sprint_collect_dispatches(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (False, sm.FailureCategory.HANG),
                (True, None),  # must NOT run
            ],
            max_fix_rounds=3,
        )
        assert len(calls) == 2, (
            f"Two hangs must result in exactly 2 dispatches; got {len(calls)}"
        )
