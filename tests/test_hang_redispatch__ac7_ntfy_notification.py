"""Tests for #787 AC7: ntfy emits a distinct notification for hang-redispatch events."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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


class TestAC7NtfyNotification:
    def test_hang_redispatch_emits_distinct_alert(self, tmp_path):
        """When a hang triggers a redispatch, dispatch_alerts must be called
        with a category/title distinct from normal warn/kill notifications."""
        alert_calls: list = []

        def capture_alert(alert_modes, title="", body="", issue_num=None,
                          category=None, cfg=None, repo=None):
            alert_calls.append({
                "title": title,
                "body": body,
                "category": category,
            })

        coder_iter = iter([
            (False, sm.FailureCategory.HANG),
            (True, None),
        ])

        def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                       repo_name=None, cfg=None, chosen_port=None,
                       rate_limit_events=None, on_running=None, sprint_label=None,
                       prior_failures=None, hang_continuation=None, attempt_kind=None):
            if on_running:
                on_running()
            return next(coder_iter)

        env = {k: v for k, v in os.environ.items()}
        env["COMMANDER_HANG_REDISPATCH"] = "1"
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
            patch.object(sm, "dispatch_alerts", side_effect=capture_alert),
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

        # There must be at least one alert call for the hang-redispatch event
        assert alert_calls, "dispatch_alerts must be called when hang triggers redispatch"

        # Check that at least one alert has a distinct category for hang-redispatch
        hang_redispatch_alerts = [
            a for a in alert_calls
            if "redispatch" in (a.get("category") or "").lower()
            or "hang" in (a.get("category") or "").lower()
            or "redispatch" in (a.get("title") or "").lower()
            or "hang" in (a.get("title") or "").lower()
        ]
        assert hang_redispatch_alerts, (
            f"At least one alert must reference 'hang' or 'redispatch'; "
            f"got alerts: {alert_calls}"
        )

    def test_hang_redispatch_alert_has_unique_category(self, tmp_path):
        """The hang-redispatch alert category must differ from 'warn' and 'kill' categories."""
        alert_calls: list = []

        def capture_alert(alert_modes, title="", body="", issue_num=None,
                          category=None, cfg=None, repo=None):
            alert_calls.append({"category": category, "title": title})

        coder_iter = iter([
            (False, sm.FailureCategory.HANG),
            (True, None),
        ])

        def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                       repo_name=None, cfg=None, chosen_port=None,
                       rate_limit_events=None, on_running=None, sprint_label=None,
                       prior_failures=None, hang_continuation=None, attempt_kind=None):
            if on_running:
                on_running()
            return next(coder_iter)

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
            patch.object(sm, "dispatch_alerts", side_effect=capture_alert),
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

        categories = [a.get("category") for a in alert_calls]
        # Must not ONLY have "warn" or "kill" categories — there must be a new one
        non_generic = [c for c in categories if c not in (None, "warn", "kill", "failure")]
        hang_specific = [
            c for c in categories
            if c and ("redispatch" in c.lower() or "hang" in c.lower())
        ]
        assert hang_specific or any(
            "redispatch" in (a.get("title") or "").lower() for a in alert_calls
        ), (
            f"At least one alert must use a hang-redispatch-specific category or title; "
            f"categories: {categories}, titles: {[a.get('title') for a in alert_calls]}"
        )

    def test_no_redispatch_alert_when_env_disabled(self, tmp_path):
        """With COMMANDER_HANG_REDISPATCH=0, no hang-redispatch alert is emitted."""
        alert_calls: list = []

        def capture_alert(alert_modes, title="", body="", issue_num=None,
                          category=None, cfg=None, repo=None):
            alert_calls.append({"category": category, "title": title})

        def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                       repo_name=None, cfg=None, chosen_port=None,
                       rate_limit_events=None, on_running=None, sprint_label=None,
                       prior_failures=None, hang_continuation=None, attempt_kind=None):
            if on_running:
                on_running()
            return False, sm.FailureCategory.HANG

        env = {k: v for k, v in os.environ.items()}
        env["COMMANDER_HANG_REDISPATCH"] = "0"

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
            patch.object(sm, "dispatch_alerts", side_effect=capture_alert),
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

        hang_redispatch_alerts = [
            a for a in alert_calls
            if "redispatch" in (a.get("category") or "").lower()
            or "redispatch" in (a.get("title") or "").lower()
        ]
        assert not hang_redispatch_alerts, (
            f"No redispatch alert must be emitted when COMMANDER_HANG_REDISPATCH=0; "
            f"got: {hang_redispatch_alerts}"
        )
