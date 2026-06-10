"""Tests for #787 AC6: COMMANDER_HANG_REDISPATCH=0 disables redispatch; first hang escalates."""
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


def _run_sprint_with_hang(
    tmp_path: Path,
    *,
    hang_redispatch_env: str,
) -> tuple[sm.SprintSummary, list]:
    """Run sprint where coder always returns HANG. Returns (summary, dispatch_calls)."""
    dispatch_calls: list = []

    def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                   repo_name=None, cfg=None, chosen_port=None,
                   rate_limit_events=None, on_running=None, sprint_label=None,
                   prior_failures=None, hang_continuation=None, attempt_kind=None):
        if on_running:
            on_running()
        dispatch_calls.append(attempt_kind)
        return False, sm.FailureCategory.HANG

    env = {k: v for k, v in os.environ.items()}
    env["COMMANDER_HANG_REDISPATCH"] = hang_redispatch_env
    env.pop("COMMANDER_MAX_FIX_ROUNDS", None)

    with (
        patch.object(sm, "_create_sprint_branch", lambda b: None),
        patch.object(sm, "list_backlog_issues",
                     lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
        patch.object(sm, "_dispatch_coder", fake_coder),
        patch.object(sm, "_dispatch_tester", lambda *a, **kw: (0, None)),
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
    return summary, dispatch_calls


class TestAC6DisableEnvVar:
    def test_redispatch_disabled_with_env_zero(self, tmp_path):
        """COMMANDER_HANG_REDISPATCH=0 → only 1 coder dispatch (no redispatch)."""
        summary, calls = _run_sprint_with_hang(tmp_path, hang_redispatch_env="0")
        assert len(calls) == 1, (
            f"COMMANDER_HANG_REDISPATCH=0 must prevent redispatch: expected 1 call, got {len(calls)}"
        )

    def test_redispatch_enabled_with_env_one(self, tmp_path):
        """COMMANDER_HANG_REDISPATCH=1 (default) → 2 coder dispatches on first hang."""
        # Override: second hang also fails, so we need different returns per call
        dispatch_calls: list = []
        returns = iter([
            (False, sm.FailureCategory.HANG),  # initial
            (False, sm.FailureCategory.HANG),  # hang_continue → escalate
        ])

        def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                       repo_name=None, cfg=None, chosen_port=None,
                       rate_limit_events=None, on_running=None, sprint_label=None,
                       prior_failures=None, hang_continuation=None, attempt_kind=None):
            if on_running:
                on_running()
            dispatch_calls.append(attempt_kind)
            return next(returns)

        env = {k: v for k, v in os.environ.items()}
        env["COMMANDER_HANG_REDISPATCH"] = "1"

        with (
            patch.object(sm, "_create_sprint_branch", lambda b: None),
            patch.object(sm, "list_backlog_issues",
                         lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
            patch.object(sm, "_dispatch_coder", fake_coder),
            patch.object(sm, "_dispatch_tester", lambda *a, **kw: (0, None)),
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
        assert len(dispatch_calls) == 2, (
            f"COMMANDER_HANG_REDISPATCH=1 must allow 1 redispatch; got {len(dispatch_calls)} calls"
        )

    def test_disabled_hang_escalates_immediately(self, tmp_path):
        """With COMMANDER_HANG_REDISPATCH=0, first hang must put ticket in skipped."""
        summary, calls = _run_sprint_with_hang(tmp_path, hang_redispatch_env="0")
        assert any("1" in s for s in summary.skipped), (
            "Ticket must be skipped when COMMANDER_HANG_REDISPATCH=0 and hang occurs"
        )

    def test_redispatch_env_default_is_on(self, tmp_path):
        """When COMMANDER_HANG_REDISPATCH is unset, redispatch must be enabled by default."""
        dispatch_calls: list = []
        returns = iter([
            (False, sm.FailureCategory.HANG),
            (True, None),  # hang_continue succeeds
        ])

        def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                       repo_name=None, cfg=None, chosen_port=None,
                       rate_limit_events=None, on_running=None, sprint_label=None,
                       prior_failures=None, hang_continuation=None, attempt_kind=None):
            if on_running:
                on_running()
            dispatch_calls.append(attempt_kind)
            return next(returns)

        env = {k: v for k, v in os.environ.items()}
        env.pop("COMMANDER_HANG_REDISPATCH", None)  # unset — must default to enabled

        with (
            patch.object(sm, "_create_sprint_branch", lambda b: None),
            patch.object(sm, "list_backlog_issues",
                         lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
            patch.object(sm, "_dispatch_coder", fake_coder),
            patch.object(sm, "_dispatch_tester", lambda *a, **kw: (0, None)),
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

        assert len(dispatch_calls) == 2, (
            f"Default (unset) COMMANDER_HANG_REDISPATCH must enable redispatch; "
            f"got {len(dispatch_calls)} calls"
        )
