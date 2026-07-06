"""Tests for issue #1645: GET /api/running?project= snapshot endpoint.

AC-1: GET /api/running?project=<id> responds HTTP 200 with running sprint
      status and per-ticket progress fields.
AC-2: No GitHub API client method is invoked during the response.
AC-3: Endpoint returns HTTP 404 when no running sprint exists for the project.
AC-4: Existing routes and behaviour are unaffected.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure dashboard modules are importable.
_DASHBOARD = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))


# ---------------------------------------------------------------------------
# Helpers — minimal fake status_data matching what server._sprint_statuses holds
# ---------------------------------------------------------------------------

def _make_status_data(sprint_label: str = "sprint-103") -> dict:
    return {
        "sprint_label": sprint_label,
        "start_timestamp": "2026-07-06T00:00:00",
        "pipeline_mode": False,
        "max_coder_slots": 1,
        "max_tester_slots": 1,
        "issues": [
            {
                "number": 10,
                "title": "First ticket",
                "status": "done",
                "agent_status": None,
                "dispatch_level": 0,
            },
            {
                "number": 11,
                "title": "Second ticket",
                "status": "in-progress",
                "agent_status": "coder_running",
                "coder_started_at": "2026-07-06T00:05:00",
                "dispatch_level": 0,
            },
            {
                "number": 12,
                "title": "Third ticket",
                "status": "pending",
                "agent_status": None,
                "dispatch_level": 0,
            },
        ],
        "estimates": {},
    }


# ---------------------------------------------------------------------------
# AC-1: 200 with required fields when sprint is running
# ---------------------------------------------------------------------------

class TestAC1RunningSnapshotReturns200:
    """AC-1: GET /api/running?project=<id> returns 200 and the expected shape."""

    def test_returns_200_with_sprint_status_fields(self, tmp_path):
        project = "owner/repo"
        sprint_label = "sprint-103"
        status_data = _make_status_data(sprint_label)

        fake_commander = tmp_path / ".commander"
        (fake_commander / "sprints").mkdir(parents=True)

        def fake_project_root(p):
            return tmp_path

        def fake_commander_dir(root):
            return fake_commander

        def fake_read_plan(root, label):
            return {"state": "running"}

        def fake_pid_alive(root, label):
            return True

        fake_statuses = {(project, sprint_label): status_data}

        from routers.running_service import build_running_snapshot

        with (
            patch("routers.running_service._server") as mock_srv_fn,
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
        ):
            srv = MagicMock()
            srv._any_sprint_running.return_value = {
                "project": project,
                "sprint_label": sprint_label,
                "pid": 12345,
            }
            srv._project_root_path.side_effect = fake_project_root
            srv._commander_dir.side_effect = fake_commander_dir
            srv._read_plan_json.side_effect = fake_read_plan
            srv._sprint_pid_alive.side_effect = fake_pid_alive
            srv._sprint_statuses = fake_statuses
            mock_srv_fn.return_value = srv

            snapshot = build_running_snapshot(project)

        assert snapshot is not None
        assert snapshot["sprint_label"] == sprint_label
        assert snapshot["project"] == project
        assert "time_spent_sec" in snapshot
        assert "started_at" in snapshot
        assert "issues" in snapshot
        assert "done_count" in snapshot
        assert "pending_count" in snapshot
        assert "total_count" in snapshot

    def test_issues_contain_per_ticket_progress_fields(self, tmp_path):
        project = "owner/repo"
        sprint_label = "sprint-103"
        status_data = _make_status_data(sprint_label)

        fake_commander = tmp_path / ".commander"
        (fake_commander / "sprints").mkdir(parents=True)

        fake_statuses = {(project, sprint_label): status_data}

        from routers.running_service import build_running_snapshot

        with (
            patch("routers.running_service._server") as mock_srv_fn,
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
        ):
            srv = MagicMock()
            srv._any_sprint_running.return_value = {
                "project": project,
                "sprint_label": sprint_label,
                "pid": 12345,
            }
            srv._project_root_path.return_value = tmp_path
            srv._commander_dir.return_value = fake_commander
            srv._read_plan_json.return_value = {"state": "running"}
            srv._sprint_pid_alive.return_value = True
            srv._sprint_statuses = fake_statuses
            mock_srv_fn.return_value = srv

            snapshot = build_running_snapshot(project)

        issues = snapshot["issues"]
        assert len(issues) == 3
        required_fields = {"number", "title", "status", "agent_status", "agent", "elapsed_secs"}
        for issue in issues:
            assert required_fields <= issue.keys(), f"Missing fields in issue: {issue}"

    def test_done_and_pending_counts_are_correct(self, tmp_path):
        project = "owner/repo"
        sprint_label = "sprint-103"
        status_data = _make_status_data(sprint_label)

        fake_commander = tmp_path / ".commander"
        (fake_commander / "sprints").mkdir(parents=True)
        fake_statuses = {(project, sprint_label): status_data}

        from routers.running_service import build_running_snapshot

        with (
            patch("routers.running_service._server") as mock_srv_fn,
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
        ):
            srv = MagicMock()
            srv._any_sprint_running.return_value = {
                "project": project,
                "sprint_label": sprint_label,
                "pid": 1,
            }
            srv._project_root_path.return_value = tmp_path
            srv._commander_dir.return_value = fake_commander
            srv._read_plan_json.return_value = {"state": "running"}
            srv._sprint_pid_alive.return_value = True
            srv._sprint_statuses = fake_statuses
            mock_srv_fn.return_value = srv

            snapshot = build_running_snapshot(project)

        # 1 done, 1 in-progress (coder_running), 1 pending → total=3
        # complete_count = done + failed + skipped = 1; pending = total - complete = 2
        assert snapshot["total_count"] == 3
        assert snapshot["done_count"] == 1
        assert snapshot["complete_count"] == 1
        assert snapshot["pending_count"] == 2  # in-progress + pending both count as non-terminal


# ---------------------------------------------------------------------------
# AC-2: No GitHub API client method invoked
# ---------------------------------------------------------------------------

class TestAC2NoGitHubAPICall:
    """AC-2: No github_client method is called during the response."""

    def test_github_client_not_called(self, tmp_path):
        project = "owner/repo"
        sprint_label = "sprint-103"
        status_data = _make_status_data(sprint_label)

        fake_commander = tmp_path / ".commander"
        (fake_commander / "sprints").mkdir(parents=True)
        fake_statuses = {(project, sprint_label): status_data}

        from routers.running_service import build_running_snapshot
        import github_client

        with (
            patch("routers.running_service._server") as mock_srv_fn,
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
            patch.object(github_client, "cached_open_issues_with_body") as mock_gh,
            patch.object(github_client, "list_open_issues_with_body") as mock_gh2,
        ):
            srv = MagicMock()
            srv._any_sprint_running.return_value = {
                "project": project,
                "sprint_label": sprint_label,
                "pid": 1,
            }
            srv._project_root_path.return_value = tmp_path
            srv._commander_dir.return_value = fake_commander
            srv._read_plan_json.return_value = {"state": "running"}
            srv._sprint_pid_alive.return_value = True
            srv._sprint_statuses = fake_statuses
            mock_srv_fn.return_value = srv

            build_running_snapshot(project)

        mock_gh.assert_not_called()
        mock_gh2.assert_not_called()


# ---------------------------------------------------------------------------
# AC-3: 404 when no running sprint
# ---------------------------------------------------------------------------

class TestAC3NoRunningSprintReturns404:
    """AC-3: Returns None (→ 404) when no running sprint exists for the project."""

    def test_returns_none_when_no_running_sprint(self, tmp_path):
        from routers.running_service import build_running_snapshot

        with patch("routers.running_service._server") as mock_srv_fn:
            srv = MagicMock()
            srv._any_sprint_running.return_value = None
            mock_srv_fn.return_value = srv

            result = build_running_snapshot("owner/repo")

        assert result is None

    def test_endpoint_returns_404_when_no_running_sprint(self):
        """The FastAPI endpoint raises HTTPException(404)."""
        from fastapi import HTTPException
        from routers.running import get_running_snapshot

        with patch("routers.running.build_running_snapshot", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                get_running_snapshot(project="owner/repo")

        assert exc_info.value.status_code == 404

    def test_404_detail_mentions_project(self):
        """The 404 response body names the project so the caller can debug."""
        from fastapi import HTTPException
        from routers.running import get_running_snapshot

        with patch("routers.running.build_running_snapshot", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                get_running_snapshot(project="owner/testrepo")

        assert "owner/testrepo" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# AC-4: Existing routes unaffected — smoke-test via route registration
# ---------------------------------------------------------------------------

class TestAC4ExistingRoutesUnaffected:
    """AC-4: Adding the new router does not shadow or break existing routes."""

    def test_running_router_registered_with_correct_path(self):
        from routers.running import router
        paths = [route.path for route in router.routes]
        assert "/api/running" in paths

    def test_new_router_does_not_conflict_with_sprint_live(self):
        """The new /api/running path must not collide with /api/sprints/{label}/live."""
        from routers.running import router as running_router
        from routers.sprint_live import router as live_router

        running_paths = {r.path for r in running_router.routes}
        live_paths = {r.path for r in live_router.routes}
        assert running_paths.isdisjoint(live_paths), (
            f"Path collision detected: {running_paths & live_paths}"
        )
