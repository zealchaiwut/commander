"""Tests for issue #1840: manifest write error handling in auto-run rerun.

When the manifest write raises OSError (disk full, permission error, missing
sprints dir), the rerun endpoint must NOT silently succeed with stranded tickets.

AC coverage:
- AC-1: When the manifest write raises OSError, the endpoint returns HTTP 500.
- AC-2: The 500 response detail names the moved-but-undispatched ticket numbers.
- AC-3: When manifest write fails, sprint_manager is NOT spawned.
- AC-4: When manifest write fails, plan/DB state is rolled back to needs_rework.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import server as srv  # noqa: E402

_FAKE_REPO = "owner/test-proj"


def _issue(number: int, *label_names: str) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "",
        "labels": [{"name": n} for n in label_names],
    }


def _post_rerun_with_manifest_fail(tmp_path, sprint_label, issues):
    """Post an auto-run rerun where the manifest write raises OSError.

    Returns (response, spawn_calls, plan_json_mock, db_state_mock).
    """
    spawn_calls = []

    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)

    def fake_popen(cmd, **kwargs):
        spawn_calls.append(list(cmd))
        return mock_proc

    sm_path = tmp_path / "sprint_manager.py"
    sm_path.touch()

    all_nums = {i["number"] for i in issues}
    plan_json_mock = MagicMock(return_value=None)
    db_state_mock = MagicMock(return_value=None)

    original_write_text = Path.write_text

    def failing_write(self, data, *args, **kwargs):
        if "rerun-manifest" in self.name:
            raise OSError("Disk full: no space left on device")
        return original_write_text(self, data, *args, **kwargs)

    with (
        patch.object(srv, "_is_sprint_running", return_value=False),
        patch.object(srv, "_project_root_path", return_value=tmp_path),
        patch.object(srv, "_coder_clone_path", return_value=tmp_path),
        patch.object(srv, "SPRINT_MANAGER_PATH", sm_path),
        patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues),
        patch.object(srv.github_client, "list_labels", return_value=[]),
        patch.object(srv.github_client, "get_label_color", return_value="0075ca"),
        patch.object(srv.github_client, "create_label", return_value=None),
        patch.object(srv.github_client, "update_labels", return_value=None),
        patch.object(srv.github_client, "invalidate", return_value=None),
        patch.object(srv, "_await_rerun_relabel", return_value=all_nums),
        patch.object(srv, "_emit_dashboard_event", return_value=None),
        patch.object(srv, "_write_plan_json", return_value=None),
        patch.object(srv, "_plan_json_set_state", plan_json_mock),
        patch.object(srv, "_sprint_db_set_state", db_state_mock),
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(Path, "write_text", new=failing_write),
    ):
        client = TestClient(srv.app)
        resp = client.post(
            f"/api/sprints/{sprint_label}/rerun",
            params={"project": _FAKE_REPO},
            json={"auto_run": True},
        )

    return resp, spawn_calls, plan_json_mock, db_state_mock


class TestManifestWriteErrorReturns500:
    """AC-1: Manifest write failure must result in HTTP 500."""

    def test_returns_500_on_osError(self, tmp_path):
        """OSError during manifest write → HTTP 500 (not 200)."""
        issues = [
            _issue(49, "sprint-4", "needs-rework"),
            _issue(50, "sprint-4", "needs-rework"),
        ]
        resp, _, _, _ = _post_rerun_with_manifest_fail(tmp_path, "sprint-4", issues)
        assert resp.status_code == 500, (
            f"Expected HTTP 500 when manifest write fails, got {resp.status_code}. "
            f"Response body: {resp.text!r}"
        )

    def test_500_detail_is_string(self, tmp_path):
        """The 500 response must have a non-empty detail field."""
        issues = [_issue(49, "sprint-4", "needs-rework")]
        resp, _, _, _ = _post_rerun_with_manifest_fail(tmp_path, "sprint-4", issues)
        assert resp.status_code == 500
        detail = resp.json().get("detail", "")
        assert detail, (
            "HTTP 500 response must include a non-empty 'detail' message. "
            f"Got: {resp.json()!r}"
        )


class TestManifestWriteErrorNamesTickets:
    """AC-2: The 500 detail must name the moved-but-undispatched ticket numbers."""

    def test_detail_names_single_moved_ticket(self, tmp_path):
        """500 detail includes the single moved ticket number."""
        issues = [_issue(99, "sprint-7", "needs-rework")]
        resp, _, _, _ = _post_rerun_with_manifest_fail(tmp_path, "sprint-7", issues)
        assert resp.status_code == 500
        detail = resp.json().get("detail", "")
        assert "#99" in detail, (
            f"500 detail must name moved ticket #99. Got detail: {detail!r}"
        )

    def test_detail_names_all_moved_tickets(self, tmp_path):
        """500 detail includes every moved ticket when multiple tickets are moved."""
        issues = [
            _issue(49, "sprint-4", "needs-rework"),
            _issue(50, "sprint-4", "needs-rework"),
        ]
        resp, _, _, _ = _post_rerun_with_manifest_fail(tmp_path, "sprint-4", issues)
        assert resp.status_code == 500
        detail = resp.json().get("detail", "")
        assert "#49" in detail and "#50" in detail, (
            f"500 detail must name both moved tickets #49 and #50. Got: {detail!r}"
        )


class TestManifestWriteErrorNoSpawn:
    """AC-3: sprint_manager must NOT be spawned when manifest write fails."""

    def test_spawn_not_called_on_manifest_fail(self, tmp_path):
        """subprocess.Popen must not be called when manifest write raises."""
        issues = [
            _issue(49, "sprint-4", "needs-rework"),
            _issue(50, "sprint-4", "needs-rework"),
        ]
        resp, spawn_calls, _, _ = _post_rerun_with_manifest_fail(tmp_path, "sprint-4", issues)
        assert resp.status_code == 500
        assert not spawn_calls, (
            "sprint_manager must NOT be spawned when manifest write fails — "
            "spawning without a manifest would bypass the label-lag fix (#1827). "
            f"Got spawn calls: {spawn_calls}"
        )


class TestManifestWriteErrorRollsBackState:
    """AC-4: plan.json and DB must be set to needs_rework when manifest write fails."""

    def test_plan_json_set_to_needs_rework(self, tmp_path):
        """_plan_json_set_state is called with 'needs_rework' on manifest failure."""
        issues = [_issue(49, "sprint-4", "needs-rework")]
        resp, _, plan_json_mock, _ = _post_rerun_with_manifest_fail(
            tmp_path, "sprint-4", issues
        )
        assert resp.status_code == 500

        needs_rework_calls = [
            c for c in plan_json_mock.call_args_list
            if len(c.args) >= 3 and c.args[2] == "needs_rework"
        ]
        assert needs_rework_calls, (
            "_plan_json_set_state must be called with state='needs_rework' to roll "
            "back from the premature 'running' state set before the manifest write. "
            f"All calls: {plan_json_mock.call_args_list}"
        )

    def test_plan_json_rollback_includes_end_reason(self, tmp_path):
        """The needs_rework rollback call includes an end_reason keyword arg."""
        issues = [_issue(49, "sprint-4", "needs-rework")]
        resp, _, plan_json_mock, _ = _post_rerun_with_manifest_fail(
            tmp_path, "sprint-4", issues
        )
        assert resp.status_code == 500

        needs_rework_calls = [
            c for c in plan_json_mock.call_args_list
            if len(c.args) >= 3 and c.args[2] == "needs_rework"
        ]
        assert needs_rework_calls, "needs_rework call must exist (see above test)"
        call = needs_rework_calls[-1]
        end_reason = call.kwargs.get("end_reason", "")
        assert end_reason, (
            "_plan_json_set_state rollback must include end_reason so the dashboard "
            f"can show why the sprint entered needs_rework. kwargs: {call.kwargs}"
        )

    def test_db_state_set_to_needs_rework(self, tmp_path):
        """_sprint_db_set_state is called with 'needs_rework' on manifest failure."""
        issues = [_issue(49, "sprint-4", "needs-rework")]
        resp, _, _, db_state_mock = _post_rerun_with_manifest_fail(
            tmp_path, "sprint-4", issues
        )
        assert resp.status_code == 500

        db_needs_rework_calls = [
            c for c in db_state_mock.call_args_list
            if len(c.args) >= 3 and c.args[2] == "needs_rework"
        ]
        assert db_needs_rework_calls, (
            "_sprint_db_set_state must be called with state='needs_rework' on "
            "manifest write failure so the DB matches plan.json. "
            f"All calls: {db_state_mock.call_args_list}"
        )
