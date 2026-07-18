"""Tests for issue #1648: Unit and integration coverage for Running tab snapshot.

Covers #1645 (snapshot endpoint) and integration gaps:

Unit tests (AC1):
  AC1  GET /api/running?project= returns 200 with sprint status + per-ticket
       progress fields and asserts zero GitHub API client invocations.
  AC3  No running sprint → None / 404.
  AC4  Existing route registrations unaffected.

Integration tests (AC2):
  Gap 1 — Comprehensive github_client spy: ALL public callables in github_client
           are patched to raise AssertionError; any future call also fails.
  Gap 2 — Flag-ON first-paint: _smgmtRunningFirstPaint() in project.html issues
           exactly ONE fetch() call to /api/running (no per-ticket fan-out).
  Gap 3 — Poll continuation: setInterval(_smgmtLivePollTick, 2000) is wired
           BEFORE the running_aggregate flag branch in _smgmtLivePollRestart().

All tests run without live GitHub credentials or network access (AC3).
"""
from __future__ import annotations

import contextlib
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PROJECT_HTML = _DASHBOARD / "static" / "project.html"
PROJECT_HTML = _PROJECT_HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared helpers
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


def _make_srv_mock(project: str, sprint_label: str, tmp_path: Path) -> MagicMock:
    fake_commander = tmp_path / ".commander"
    (fake_commander / "sprints").mkdir(parents=True)
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
    srv._sprint_statuses = {(project, sprint_label): _make_status_data(sprint_label)}
    return srv


# ---------------------------------------------------------------------------
# AC1 unit: 200 with required fields
# ---------------------------------------------------------------------------

class TestAC1RunningSnapshotReturns200:
    """AC1: GET /api/running?project= returns 200 and the expected shape."""

    def test_returns_200_with_sprint_status_fields(self, tmp_path):
        from routers.running_service import build_running_snapshot

        project = "owner/repo"
        sprint_label = "sprint-103"
        with (
            patch("routers.running_service._server") as mock_srv_fn,
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
        ):
            mock_srv_fn.return_value = _make_srv_mock(project, sprint_label, tmp_path)
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
        from routers.running_service import build_running_snapshot

        project = "owner/repo"
        sprint_label = "sprint-103"
        with (
            patch("routers.running_service._server") as mock_srv_fn,
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
        ):
            mock_srv_fn.return_value = _make_srv_mock(project, sprint_label, tmp_path)
            snapshot = build_running_snapshot(project)

        required_fields = {"number", "title", "status", "agent_status", "agent", "elapsed_secs"}
        for issue in snapshot["issues"]:
            assert required_fields <= issue.keys(), f"Missing fields in issue: {issue}"

    def test_done_and_pending_counts_are_correct(self, tmp_path):
        from routers.running_service import build_running_snapshot

        project = "owner/repo"
        sprint_label = "sprint-103"
        with (
            patch("routers.running_service._server") as mock_srv_fn,
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
        ):
            mock_srv_fn.return_value = _make_srv_mock(project, sprint_label, tmp_path)
            snapshot = build_running_snapshot(project)

        assert snapshot["total_count"] == 3
        assert snapshot["done_count"] == 1
        assert snapshot["complete_count"] == 1
        assert snapshot["pending_count"] == 2


# ---------------------------------------------------------------------------
# AC1 unit: zero GitHub API calls (two-function patch)
# ---------------------------------------------------------------------------

class TestAC1NoGitHubAPICall:
    """AC1: No github_client method is called during the response."""

    def test_github_client_not_called(self, tmp_path):
        import github_client
        from routers.running_service import build_running_snapshot

        project = "owner/repo"
        sprint_label = "sprint-103"
        with (
            patch("routers.running_service._server") as mock_srv_fn,
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
            patch.object(github_client, "cached_open_issues_with_body") as mock_gh,
            patch.object(github_client, "list_open_issues_with_body") as mock_gh2,
        ):
            mock_srv_fn.return_value = _make_srv_mock(project, sprint_label, tmp_path)
            build_running_snapshot(project)

        mock_gh.assert_not_called()
        mock_gh2.assert_not_called()


# ---------------------------------------------------------------------------
# AC3: 404 when no running sprint
# ---------------------------------------------------------------------------

class TestAC3NoRunningSprintReturns404:
    """AC3: Returns None / 404 when no running sprint exists for the project."""

    def test_returns_none_when_no_running_sprint(self, tmp_path):
        from routers.running_service import build_running_snapshot

        with patch("routers.running_service._server") as mock_srv_fn:
            srv = MagicMock()
            srv._any_sprint_running.return_value = None
            mock_srv_fn.return_value = srv
            result = build_running_snapshot("owner/repo")

        assert result is None

    def test_endpoint_returns_404_when_no_running_sprint(self):
        from routers.running import get_running_snapshot

        with patch("routers.running.build_running_snapshot", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                get_running_snapshot(project="owner/repo")

        assert exc_info.value.status_code == 404

    def test_404_detail_mentions_project(self):
        from routers.running import get_running_snapshot

        with patch("routers.running.build_running_snapshot", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                get_running_snapshot(project="owner/testrepo")

        assert "owner/testrepo" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# AC4: Existing routes unaffected
# ---------------------------------------------------------------------------

class TestAC4ExistingRoutesUnaffected:
    """AC4: Adding the new router does not shadow or break existing routes."""

    def test_running_router_registered_with_correct_path(self):
        from routers.running import router
        paths = [route.path for route in router.routes]
        assert "/api/running" in paths

    def test_new_router_does_not_conflict_with_sprint_live(self):
        from routers.running import router as running_router
        from routers.sprint_live import router as live_router

        running_paths = {r.path for r in running_router.routes}
        live_paths = {r.path for r in live_router.routes}
        assert running_paths.isdisjoint(live_paths), (
            f"Path collision detected: {running_paths & live_paths}"
        )


# ---------------------------------------------------------------------------
# AC2 integration Gap 1: comprehensive github_client spy
# ---------------------------------------------------------------------------

class TestAC2ComprehensiveNoGitHubCalls:
    """AC2: ALL public github_client callables are spied on — any call fails.

    More thorough than the two-function patch in TestAC1NoGitHubAPICall
    because it catches functions added in the future as well.
    """

    def _public_github_functions(self) -> list[str]:
        import github_client
        return [
            name
            for name, obj in inspect.getmembers(github_client, inspect.isfunction)
            if not name.startswith("_")
        ]

    def test_no_public_github_client_function_called(self, tmp_path):
        import github_client
        from routers.running_service import build_running_snapshot

        project = "owner/repo"
        sprint_label = "sprint-103"
        public_names = self._public_github_functions()
        assert public_names, "Expected at least one public function in github_client"

        def _make_spy(fn_name: str):
            def _spy(*args, **kwargs):
                raise AssertionError(
                    f"github_client.{fn_name}() was invoked during "
                    "/api/running — snapshot must be GitHub-free"
                )
            return _spy

        with contextlib.ExitStack() as stack:
            for name in public_names:
                stack.enter_context(
                    patch.object(github_client, name, side_effect=_make_spy(name))
                )
            with (
                patch("routers.running_service._server") as mock_srv_fn,
                patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
            ):
                mock_srv_fn.return_value = _make_srv_mock(project, sprint_label, tmp_path)
                snapshot = build_running_snapshot(project)

        assert snapshot is not None
        assert snapshot["sprint_label"] == sprint_label

    def test_spy_covers_key_github_functions(self):
        public_names = self._public_github_functions()
        assert "cached_open_issues_with_body" in public_names
        assert "list_open_issues_with_body" in public_names

    def test_spy_count_is_substantial(self):
        public_names = self._public_github_functions()
        assert len(public_names) >= 20, (
            f"Expected at least 20 public github_client functions, got {len(public_names)}"
        )


# ---------------------------------------------------------------------------
# AC2 integration Gap 2: flag-ON first-paint fetch count
# ---------------------------------------------------------------------------

class TestAC2FlagOnFirstPaintFetchCount:
    """AC2: _smgmtRunningFirstPaint() must issue exactly ONE fetch() call."""

    def _first_paint_fn(self) -> str:
        m = re.search(
            r"async function _smgmtRunningFirstPaint\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert m is not None, "_smgmtRunningFirstPaint() not found in project.html"
        return m.group(0)

    def test_exactly_one_fetch_call_in_first_paint(self):
        fn_body = self._first_paint_fn()
        fetch_calls = re.findall(r"(?<!\w)fetch\s*\(", fn_body)
        assert len(fetch_calls) == 1, (
            f"Expected exactly 1 fetch() call in _smgmtRunningFirstPaint(), "
            f"found {len(fetch_calls)}"
        )

    def test_single_fetch_targets_api_running(self):
        fn_body = self._first_paint_fn()
        assert "/api/running" in fn_body
        assert "project=" in fn_body

    def test_no_per_ticket_status_fan_out_in_first_paint(self):
        fn_body = self._first_paint_fn()
        assert "/api/issues/" not in fn_body
        assert "/api/sprints/" not in fn_body

    def test_flag_check_gates_first_paint_call(self):
        assert "running_aggregate" in PROJECT_HTML
        assert "_smgmtRunningFirstPaint()" in PROJECT_HTML
        restart_match = re.search(
            r"function _smgmtLivePollRestart\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert restart_match is not None, "_smgmtLivePollRestart not found"
        restart_body = restart_match.group(1)
        assert "running_aggregate" in restart_body
        assert "_smgmtRunningFirstPaint()" in restart_body


# ---------------------------------------------------------------------------
# AC2 integration Gap 3: poll continues independent of flag
# ---------------------------------------------------------------------------

class TestAC2PollContinuesIndependentOfFlag:
    """AC2: 2-second live poll is always established, regardless of the flag.

    Per triage note on #1647: the Running tab uses a 2-second poll (not SSE).
    setInterval must be wired BEFORE the flag branch.
    """

    def test_setinterval_precedes_flag_branch(self):
        restart_match = re.search(
            r"function _smgmtLivePollRestart\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert restart_match is not None, "_smgmtLivePollRestart not found"
        body = restart_match.group(1)

        interval_pos = body.find("setInterval(_smgmtLivePollTick, 2000)")
        flag_pos = body.find("running_aggregate")

        assert interval_pos != -1, "setInterval(_smgmtLivePollTick, 2000) not found"
        assert flag_pos != -1, "'running_aggregate' flag check not found"
        assert interval_pos < flag_pos, (
            "setInterval must appear BEFORE the flag branch "
            f"(interval_pos={interval_pos}, flag_pos={flag_pos})"
        )

    def test_live_poll_tick_still_wired_when_flag_on(self):
        restart_match = re.search(
            r"function _smgmtLivePollRestart\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert restart_match is not None
        body = restart_match.group(1)
        assert "setInterval(_smgmtLivePollTick, 2000)" in body
        assert "_smgmtRunningFirstPaint()" in body

    def test_no_sse_eventsource_in_running_tab_path(self):
        """The Running tab uses a 2-second poll, not SSE."""
        fn_body_match = re.search(
            r"async function _smgmtRunningFirstPaint\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert fn_body_match is not None
        fn_body = fn_body_match.group(0)
        assert "EventSource" not in fn_body
        assert "new EventSource" not in fn_body
