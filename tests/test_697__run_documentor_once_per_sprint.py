"""Tests for issue #697: Run documentor once per sprint, not per ticket.

AC items verified:
  AC-1  _run_documentor() is removed from inside handle_post_tester()
  AC-2  _run_documentor() called exactly once after dispatch loop finishes
  AC-3  Full list of completed ticket numbers passed to _run_documentor()
  AC-4  Sprint label passed to _run_documentor()
  AC-5  _run_documentor called exactly once (not N times) per sprint run
  AC-6  Exactly one documentor log appears after all tickets complete
  AC-7  No documentor log appears mid-loop during per-ticket processing
  AC-8  Zero-passing-tickets: documentor skipped gracefully
"""
from __future__ import annotations

import sys
import types
from contextlib import ExitStack
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(documentor_enabled: bool = True, tmp_path: Path = None):
    """Return a minimal SprintConfig-like stub."""
    cfg = MagicMock()
    cfg.documentor_enabled = documentor_enabled
    cfg.repo_name = "owner/repo"
    cfg.api_url = None
    cfg.logs_dir = tmp_path / "logs" if tmp_path else Path("/tmp/logs")
    cfg.worktree_coder = tmp_path / "coder" if tmp_path else Path("/tmp/coder")
    cfg.worktree_tester = tmp_path / "tester" if tmp_path else Path("/tmp/tester")
    cfg.worktree_tester_app = cfg.worktree_tester / "apps" / "dashboard"
    cfg.sprints_dir = tmp_path / "sprints" if tmp_path else Path("/tmp/sprints")
    cfg.sprint_branch_prefix = "sprint"
    cfg.app_default_port = None
    cfg.documenter_prompt_template = None
    return cfg


def _run_sprint_with_mocks(
    issue_numbers: list[int],
    documentor_enabled: bool,
    all_merge: bool,
    tmp_path: Path,
    captured_documentor_calls: list,
):
    """Run run_sprint() with heavy machinery mocked.

    Records all calls to _run_documentor in captured_documentor_calls.
    Returns (summary, state).
    """
    cfg = _make_cfg(documentor_enabled=documentor_enabled, tmp_path=tmp_path)

    raw_issues = [{"number": n, "title": f"Issue {n}"} for n in issue_numbers]

    def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                            repo_name=None, cfg=None, chosen_port=None,
                            rate_limit_events=None, on_running=None,
                            sprint_label=None, prior_failures=None):
        if on_running:
            on_running()
        return True, None

    def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                             repo_name=None, cfg=None, chosen_port=None,
                             rate_limit_events=None, on_running=None,
                             sprint_label=None):
        if on_running:
            on_running()
        return 0, None

    def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                gate_pytest, gate_lint, gate_merge_preview,
                                target_branch="develop", **kwargs):
        if all_merge:
            return True, f"Issue #{issue_num}: merged", None
        return False, f"Issue #{issue_num}: gate failed (pytest)", sm.FailureCategory.PYTEST_FAIL

    def fake_run_documentor(issue_nums, sprint_label, repo_name, cfg=None):
        captured_documentor_calls.append({
            "issue_nums": list(issue_nums),
            "sprint_label": sprint_label,
            "repo_name": repo_name,
        })

    patches = [
        patch.object(sm, "list_backlog_issues", return_value=raw_issues),
        patch.object(sm, "_dispatch_coder", side_effect=fake_dispatch_coder),
        patch.object(sm, "_dispatch_tester", side_effect=fake_dispatch_tester),
        patch.object(sm, "handle_post_tester", side_effect=fake_handle_post_tester),
        patch.object(sm, "_run_documentor", side_effect=fake_run_documentor),
        patch.object(sm, "_post_sprint_status"),
        patch.object(sm, "_create_sprint_branch"),
        patch.object(sm, "_setup_pid_file"),
        patch.object(sm, "_warn_file_conflicts"),
        patch.object(sm, "_neon_sprint_init"),
        patch.object(sm, "_neon_sprint_status"),
        patch.object(sm, "_neon_ticket_status"),
        patch.object(sm, "_load_sprint_plan", return_value=None),
        patch.object(sm, "_build_sprint_dag_layers", return_value=None),
        patch.object(sm, "_compute_dispatch_levels", side_effect=lambda issues, *a, **kw: [issues]),
        patch.object(sm, "_emit_sprint_lifecycle_event"),
        patch.object(sm, "structured_log"),
        patch.object(sm, "_transition_safe"),
        patch.object(sm, "_load_estimate", return_value=None),
        patch.object(sm, "_plan_json_set_state_sm"),
        patch.object(sm, "_delete_failure_sidecar"),
        patch.object(sm, "_find_feature_branch", side_effect=lambda n: f"feature/{n}-slug"),
        patch.object(sm, "_post_agent_event"),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        summary, state = sm.run_sprint(
            label="sprint-1",
            skip_gates=True,
            gate_pytest=False,
            gate_lint=False,
            gate_merge_preview=False,
            cfg=cfg,
        )

    return summary, state


# ---------------------------------------------------------------------------
# AC-1 / AC-7: handle_post_tester must NOT call _run_documentor
# ---------------------------------------------------------------------------

class TestAC1DocumentorRemovedFromHandlePostTester:
    """AC-1/7: _run_documentor() must not be called from within handle_post_tester()."""

    def test_handle_post_tester_skip_gates_does_not_call_documentor(self, tmp_path):
        """When skip_gates=True and documentor_enabled=True, _run_documentor is NOT called."""
        cfg_stub = _make_cfg(documentor_enabled=True, tmp_path=tmp_path)

        mock_doc = MagicMock()
        patches_hpt = [
            patch.object(sm, "_find_feature_branch", return_value="feature/1-slug"),
            patch.object(sm, "_is_branch_merged_into", return_value=False),
            patch.object(sm, "_was_feature_merged_via_log", return_value=True),
            patch.object(sm, "_call_finish_feature", return_value=True),
            patch.object(sm, "_run_quality_gates", return_value=[
                sm.GateResult(gate="pytest", passed=True, skipped=True),
            ]),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_success_comment"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
            patch.object(sm, "_run_documentor", mock_doc),
        ]
        with ExitStack() as stack:
            for p in patches_hpt:
                stack.enter_context(p)
            ok, msg, cat = sm.handle_post_tester(
                issue_num=1,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                gate_frontend_lint=False,
                documentor_enabled=True,
                cfg=cfg_stub,
                target_branch="sprint/sprint-1",
            )

        mock_doc.assert_not_called(), \
            "handle_post_tester must NOT call _run_documentor (AC-1)"

    def test_handle_post_tester_gates_pass_does_not_call_documentor(self, tmp_path):
        """When gates pass and documentor_enabled=True, _run_documentor is NOT called."""
        cfg_stub = _make_cfg(documentor_enabled=True, tmp_path=tmp_path)

        mock_doc2 = MagicMock()
        patches_hpt2 = [
            patch.object(sm, "_find_feature_branch", return_value="feature/1-slug"),
            patch.object(sm, "_is_branch_merged_into", return_value=False),
            patch.object(sm, "_was_feature_merged_via_log", return_value=True),
            patch.object(sm, "_call_finish_feature", return_value=True),
            patch.object(sm, "_run_quality_gates", return_value=[
                sm.GateResult(gate="pytest", passed=True, skipped=False),
                sm.GateResult(gate="lint",   passed=True, skipped=False),
            ]),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_success_comment"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
            patch.object(sm, "_run_documentor", mock_doc2),
        ]
        with ExitStack() as stack:
            for p in patches_hpt2:
                stack.enter_context(p)
            ok, msg, cat = sm.handle_post_tester(
                issue_num=1,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                gate_frontend_lint=False,
                documentor_enabled=True,
                cfg=cfg_stub,
                target_branch="sprint/sprint-1",
            )

        mock_doc2.assert_not_called(), \
            "handle_post_tester must NOT call _run_documentor even when gates pass (AC-1)"


# ---------------------------------------------------------------------------
# AC-2 / AC-5 / AC-6: run_sprint calls _run_documentor exactly once
# ---------------------------------------------------------------------------

class TestAC2DocumentorCalledOnceAfterLoop:
    """AC-2/5/6: _run_documentor called exactly once after all tickets finish."""

    def test_three_passing_tickets_calls_documentor_once(self, tmp_path):
        """With 3 passing tickets and documentor_enabled=True, _run_documentor called once."""
        calls = []
        _run_sprint_with_mocks(
            issue_numbers=[10, 20, 30],
            documentor_enabled=True,
            all_merge=True,
            tmp_path=tmp_path,
            captured_documentor_calls=calls,
        )

        assert len(calls) == 1, (
            f"_run_documentor must be called exactly once per sprint, got {len(calls)} call(s)"
        )

    def test_documentor_disabled_means_not_called(self, tmp_path):
        """With documentor_enabled=False, _run_documentor is never called."""
        calls = []
        _run_sprint_with_mocks(
            issue_numbers=[10, 20],
            documentor_enabled=False,
            all_merge=True,
            tmp_path=tmp_path,
            captured_documentor_calls=calls,
        )

        assert len(calls) == 0, (
            f"_run_documentor must not be called when documentor_enabled=False, got {len(calls)} call(s)"
        )


# ---------------------------------------------------------------------------
# AC-3: full list of merged ticket numbers passed
# ---------------------------------------------------------------------------

class TestAC3MergedTicketsPassed:
    """AC-3: full list of completed ticket numbers passed to _run_documentor()."""

    def test_all_merged_issue_nums_in_call(self, tmp_path):
        """All merged issue numbers must appear in the _run_documentor call."""
        calls = []
        _run_sprint_with_mocks(
            issue_numbers=[101, 102, 103],
            documentor_enabled=True,
            all_merge=True,
            tmp_path=tmp_path,
            captured_documentor_calls=calls,
        )

        assert len(calls) == 1
        passed_nums = sorted(calls[0]["issue_nums"])
        assert passed_nums == [101, 102, 103], (
            f"Expected [101, 102, 103] in _run_documentor call, got {passed_nums}"
        )

    def test_only_merged_tickets_passed_not_failed(self, tmp_path):
        """Only merged (passing) ticket numbers are passed, not failed ones."""
        calls = []

        cfg = _make_cfg(documentor_enabled=True, tmp_path=tmp_path)
        raw_issues = [{"number": n, "title": f"Issue {n}"} for n in [201, 202, 203]]

        merge_results = {201: True, 202: False, 203: True}

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            if merge_results[issue_num]:
                return True, f"Issue #{issue_num}: merged", None
            return False, f"Issue #{issue_num}: gate failed (pytest)", sm.FailureCategory.PYTEST_FAIL

        def fake_run_documentor(issue_nums, sprint_label, repo_name, cfg=None):
            calls.append({"issue_nums": list(issue_nums), "sprint_label": sprint_label})

        patches2 = [
            patch.object(sm, "list_backlog_issues", return_value=raw_issues),
            patch.object(sm, "_dispatch_coder", return_value=(True, None)),
            patch.object(sm, "_dispatch_tester", return_value=(0, None)),
            patch.object(sm, "handle_post_tester", side_effect=fake_handle_post_tester),
            patch.object(sm, "_run_documentor", side_effect=fake_run_documentor),
            patch.object(sm, "_post_sprint_status"),
            patch.object(sm, "_create_sprint_branch"),
            patch.object(sm, "_setup_pid_file"),
            patch.object(sm, "_warn_file_conflicts"),
            patch.object(sm, "_neon_sprint_init"),
            patch.object(sm, "_neon_sprint_status"),
            patch.object(sm, "_neon_ticket_status"),
            patch.object(sm, "_load_sprint_plan", return_value=None),
            patch.object(sm, "_build_sprint_dag_layers", return_value=None),
            patch.object(sm, "_compute_dispatch_levels", side_effect=lambda issues, *a, **kw: [issues]),
            patch.object(sm, "_emit_sprint_lifecycle_event"),
            patch.object(sm, "structured_log"),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_load_estimate", return_value=None),
            patch.object(sm, "_plan_json_set_state_sm"),
            patch.object(sm, "_delete_failure_sidecar"),
            patch.object(sm, "_find_feature_branch", side_effect=lambda n: f"feature/{n}-slug"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "record_failure"),
        ]
        with ExitStack() as stack:
            for p in patches2:
                stack.enter_context(p)
            sm.run_sprint(
                label="sprint-1",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                cfg=cfg,
            )

        assert len(calls) == 1
        passed_nums = sorted(calls[0]["issue_nums"])
        assert passed_nums == [201, 203], (
            f"Only merged tickets [201, 203] must be passed, got {passed_nums}"
        )


# ---------------------------------------------------------------------------
# AC-4: sprint label passed to _run_documentor
# ---------------------------------------------------------------------------

class TestAC4SprintLabelPassed:
    """AC-4: sprint label passed as context to _run_documentor()."""

    def test_sprint_label_in_call(self, tmp_path):
        """Sprint label must be passed to _run_documentor."""
        calls = []
        _run_sprint_with_mocks(
            issue_numbers=[1],
            documentor_enabled=True,
            all_merge=True,
            tmp_path=tmp_path,
            captured_documentor_calls=calls,
        )

        assert len(calls) == 1
        assert calls[0]["sprint_label"] == "sprint-1", (
            f"Sprint label 'sprint-1' must be passed to _run_documentor, got {calls[0]['sprint_label']!r}"
        )


# ---------------------------------------------------------------------------
# AC-8: Zero passing tickets — documentor skipped gracefully
# ---------------------------------------------------------------------------

class TestAC8ZeroPassingTickets:
    """AC-8: documentor not called when no tickets pass."""

    def test_no_passing_tickets_skips_documentor(self, tmp_path):
        """When all tickets fail gates, _run_documentor is never called."""
        calls = []
        _run_sprint_with_mocks(
            issue_numbers=[1, 2],
            documentor_enabled=True,
            all_merge=False,
            tmp_path=tmp_path,
            captured_documentor_calls=calls,
        )

        assert len(calls) == 0, (
            f"_run_documentor must not be called when no tickets pass, got {len(calls)} call(s)"
        )

    def test_empty_sprint_skips_documentor(self, tmp_path):
        """When sprint has no issues at all, _run_documentor is never called."""
        calls = []
        cfg = _make_cfg(documentor_enabled=True, tmp_path=tmp_path)

        with (
            patch.object(sm, "list_backlog_issues", return_value=[]),
            patch.object(sm, "_run_documentor", side_effect=lambda *a, **kw: calls.append(a)),
            patch.object(sm, "_setup_pid_file"),
            patch.object(sm, "_warn_file_conflicts"),
            patch.object(sm, "_neon_sprint_init"),
            patch.object(sm, "structured_log"),
            patch.object(sm, "_plan_json_set_state_sm"),
        ):
            sm.run_sprint(
                label="sprint-1",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                cfg=cfg,
            )

        assert len(calls) == 0, (
            "Empty sprint must not call _run_documentor"
        )


# ---------------------------------------------------------------------------
# AC signature: _run_documentor accepts issue_nums list + sprint_label
# ---------------------------------------------------------------------------

class TestRunDocumentorSignature:
    """Verify _run_documentor() has the new batch signature."""

    def test_signature_accepts_list_and_sprint_label(self):
        """_run_documentor must accept (issue_nums: list[int], sprint_label: str, ...) signature."""
        import inspect
        sig = inspect.signature(sm._run_documentor)
        params = list(sig.parameters.keys())

        assert "issue_nums" in params, \
            f"_run_documentor must have 'issue_nums' parameter, got {params}"
        assert "sprint_label" in params, \
            f"_run_documentor must have 'sprint_label' parameter, got {params}"

        # issue_nums must come before sprint_label
        assert params.index("issue_nums") < params.index("sprint_label"), \
            "issue_nums must be first positional param, sprint_label second"
