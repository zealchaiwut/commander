"""Tests for #787 AC3: Second hang on the same ticket escalates via infra-failure path."""
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


def _run_sprint_controlled(
    tmp_path: Path,
    coder_returns: list,
    *,
    hang_redispatch_env: str = "1",
) -> tuple[sm.SprintSummary, list, list]:
    dispatch_calls: list = []
    transition_calls: list = []

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
        return next(coder_iter)

    def fake_transition(issue_num, target, actor=None, repo_name=None, note=None):
        transition_calls.append((issue_num, target))

    env = {k: v for k, v in os.environ.items()}
    env["COMMANDER_HANG_REDISPATCH"] = hang_redispatch_env
    env.pop("COMMANDER_MAX_FIX_ROUNDS", None)

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
        patch.object(sm, "_transition_safe", fake_transition),
        patch.object(sm, "record_failure", lambda *a, **kw: None),
        patch.object(sm, "dispatch_alerts", lambda *a, **kw: None),
        patch.object(sm, "_design_docs_guard", lambda p: None),
        patch.object(sm, "_db_agent_start_sm", lambda *a, **kw: None),
        patch.object(sm, "_db_agent_finish_sm", lambda *a, **kw: None),
        patch.object(sm, "_emit_sprint_lifecycle_event", lambda *a, **kw: None),
        patch.object(sm, "_add_blocked_label", lambda *a, **kw: None),
        patch.dict(os.environ, env, clear=True),
    ):
        summary, _ = sm.run_sprint(
            label="sprint-99",
            skip_gates=True,
            gate_pytest=False,
            gate_lint=False,
            gate_merge_preview=False,
            gate_typecheck=False,
            gate_design=False,
            cfg=_fake_cfg(tmp_path),
        )
    return summary, dispatch_calls, transition_calls


class TestAC3SecondHangEscalates:
    def test_second_hang_stops_dispatching(self, tmp_path):
        """Two consecutive hangs must result in exactly 2 coder dispatches (no third)."""
        summary, calls, _ = _run_sprint_controlled(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (False, sm.FailureCategory.HANG),
                (True, None),  # this third call must NOT happen
            ],
            hang_redispatch_env="1",
        )
        assert len(calls) == 2, (
            f"Second hang must stop redispatch: expected 2 calls, got {len(calls)}"
        )

    def test_second_hang_marks_ticket_skipped(self, tmp_path):
        """After second hang, the ticket must appear in summary.skipped."""
        summary, calls, _ = _run_sprint_controlled(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (False, sm.FailureCategory.HANG),
            ],
            hang_redispatch_env="1",
        )
        assert any("1" in s for s in summary.skipped), (
            "Ticket must appear in summary.skipped after second hang"
        )
        assert not summary.merged, "Ticket must NOT appear in summary.merged after double hang"

    def test_second_hang_does_not_call_dispatch_tester(self, tmp_path):
        """After second hang, tester must NOT be dispatched."""
        tester_calls = []

        def spy_tester(*a, **kw):
            tester_calls.append(True)
            return 0, None

        env = {k: v for k, v in os.environ.items()}
        env["COMMANDER_HANG_REDISPATCH"] = "1"
        env.pop("COMMANDER_MAX_FIX_ROUNDS", None)

        coder_iter = iter([
            (False, sm.FailureCategory.HANG),
            (False, sm.FailureCategory.HANG),
        ])

        with (
            patch.object(sm, "_create_sprint_branch", lambda b: None),
            patch.object(sm, "list_backlog_issues",
                         lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
            patch.object(sm, "_dispatch_coder",
                         lambda *a, **kw: (kw.get("on_running") and kw["on_running"]() or None)
                         or next(coder_iter)),
            patch.object(sm, "_dispatch_tester", spy_tester),
            patch.object(sm, "handle_post_tester", lambda *a, **kw: (True, "merged", None)),
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
            )

        assert not tester_calls, "Tester must NOT be dispatched after second hang"

    def test_first_dispatch_is_initial_second_is_hang_continue(self, tmp_path):
        """attempt_kind sequence must be: initial, hang_continue."""
        summary, calls, _ = _run_sprint_controlled(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (False, sm.FailureCategory.HANG),
            ],
            hang_redispatch_env="1",
        )
        assert len(calls) == 2
        assert calls[0]["attempt_kind"] == "initial", (
            f"First dispatch must be 'initial', got {calls[0]['attempt_kind']!r}"
        )
        assert calls[1]["attempt_kind"] == "hang_continue", (
            f"Second dispatch must be 'hang_continue', got {calls[1]['attempt_kind']!r}"
        )
