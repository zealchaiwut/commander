"""Integration tests for issue #1648: Running tab snapshot consolidation.

Fills gaps not covered by test_running_snapshot__1645.py and
test_1647__frontend_running_first_paint.py:

  Gap 1 — Comprehensive github_client spy: the existing AC-2 test in #1645
           only patches two specific functions.  This suite patches ALL public
           callables in github_client with a side_effect that raises
           AssertionError, so the test fails immediately if *any* github API
           function is reached during the /api/running response — even ones
           added in the future.

  Gap 2 — Fetch-count for the flag-ON first-paint path: assert that
           _smgmtRunningFirstPaint() issues exactly ONE fetch() call (for
           /api/running), not a fan-out of per-ticket requests.

  Gap 3 — Poll continuation: assert that setInterval for the 2-second live
           poll is set up before (and therefore independently of) the flag
           branch, confirming the poll keeps running after first-paint.

Tests run without live GitHub credentials or network access.
"""
from __future__ import annotations

import contextlib
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — make dashboard modules importable
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "apps" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

_STATIC = _DASHBOARD / "static"
_PROJECT_HTML = _STATIC / "project.html"

PROJECT_HTML = _PROJECT_HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared fixture data
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
# Gap 1: Comprehensive github_client spy
# ---------------------------------------------------------------------------

class TestComprehensiveNoGitHubCalls:
    """ALL public github_client callables are spied on — any call fails the test.

    This is more thorough than the two-function patch in test_running_snapshot__1645.py
    (TestAC2NoGitHubAPICall) because it catches functions added in the future as well.
    """

    def _all_public_github_functions(self):
        """Return the names of all public functions defined in github_client."""
        import github_client
        return [
            name
            for name, obj in inspect.getmembers(github_client, inspect.isfunction)
            if not name.startswith("_")
        ]

    def test_no_public_github_client_function_called(self, tmp_path):
        """build_running_snapshot() must not invoke any public github_client function."""
        import github_client
        from routers.running_service import build_running_snapshot

        project = "owner/repo"
        sprint_label = "sprint-103"

        public_names = self._all_public_github_functions()
        assert public_names, "Expected at least one public function in github_client"

        def _make_spy(fn_name: str):
            def _spy(*args, **kwargs):
                raise AssertionError(
                    f"github_client.{fn_name}() was invoked during "
                    "/api/running response — snapshot must be GitHub-free"
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
                # If any github_client function fires, AssertionError propagates here
                snapshot = build_running_snapshot(project)

        assert snapshot is not None, "Expected a non-None snapshot from build_running_snapshot()"
        assert snapshot["sprint_label"] == sprint_label

    def test_spy_covers_all_public_functions_in_module(self):
        """Sanity-check: the spy list includes the two functions tested in #1645 AC-2."""
        public_names = self._all_public_github_functions()
        assert "cached_open_issues_with_body" in public_names
        assert "list_open_issues_with_body" in public_names

    def test_spy_count_is_substantial(self):
        """There should be at least 20 public functions in github_client (regression guard)."""
        public_names = self._all_public_github_functions()
        assert len(public_names) >= 20, (
            f"Expected at least 20 public github_client functions, got {len(public_names)}"
        )


# ---------------------------------------------------------------------------
# Gap 2: Flag-ON first-paint fetch count
# ---------------------------------------------------------------------------

class TestFlagOnFirstPaintFetchCount:
    """_smgmtRunningFirstPaint() must issue exactly ONE fetch() call, not a fan-out."""

    def _extract_first_paint_fn(self) -> str:
        """Extract the body of _smgmtRunningFirstPaint() from project.html."""
        m = re.search(
            r"async function _smgmtRunningFirstPaint\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert m is not None, "_smgmtRunningFirstPaint() not found in project.html"
        return m.group(0)

    def test_exactly_one_fetch_call_in_first_paint(self):
        """The function body must contain exactly one fetch( call."""
        fn_body = self._extract_first_paint_fn()
        # Count all fetch( occurrences (avoid false positives like fetchSomething()
        # by requiring a space, (, or newline before 'fetch').
        fetch_calls = re.findall(r"(?<!\w)fetch\s*\(", fn_body)
        assert len(fetch_calls) == 1, (
            f"Expected exactly 1 fetch() call in _smgmtRunningFirstPaint(), "
            f"found {len(fetch_calls)}: {fetch_calls}"
        )

    def test_single_fetch_targets_api_running(self):
        """The one fetch call must target the /api/running endpoint."""
        fn_body = self._extract_first_paint_fn()
        # The URL must include /api/running and the project query param
        assert "/api/running" in fn_body, (
            "_smgmtRunningFirstPaint() fetch call does not target /api/running"
        )
        assert "project=" in fn_body, (
            "_smgmtRunningFirstPaint() fetch call is missing the project= param"
        )

    def test_no_per_ticket_status_fan_out_in_first_paint(self):
        """The function must NOT contain calls that fan out per ticket (e.g. /api/issues/*)."""
        fn_body = self._extract_first_paint_fn()
        # These patterns would indicate a fan-out rather than a single snapshot call
        assert "/api/issues/" not in fn_body, (
            "_smgmtRunningFirstPaint() appears to fan-out per-issue requests"
        )
        assert "/api/sprints/" not in fn_body, (
            "_smgmtRunningFirstPaint() appears to call /api/sprints/* — unexpected fan-out"
        )

    def test_flag_check_gates_first_paint_call(self):
        """The first-paint path is only taken when running_aggregate feature flag is ON."""
        # The flag branch lives in _smgmtLivePollRestart, not inside _smgmtRunningFirstPaint
        # itself — confirm the flag check and the first-paint call are both present in the HTML
        assert "running_aggregate" in PROJECT_HTML
        assert "_smgmtRunningFirstPaint()" in PROJECT_HTML
        # They should appear in the same function body (_smgmtLivePollRestart)
        restart_match = re.search(
            r"function _smgmtLivePollRestart\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert restart_match is not None, "_smgmtLivePollRestart not found in project.html"
        restart_body = restart_match.group(1)
        assert "running_aggregate" in restart_body, (
            "Flag check 'running_aggregate' not found inside _smgmtLivePollRestart()"
        )
        assert "_smgmtRunningFirstPaint()" in restart_body, (
            "_smgmtRunningFirstPaint() call not found inside _smgmtLivePollRestart()"
        )


# ---------------------------------------------------------------------------
# Gap 3: Poll continuation — setInterval is not gated by the flag
# ---------------------------------------------------------------------------

class TestPollContinuesIndependentOfFlag:
    """The 2-second live poll must be established regardless of the feature flag.

    _smgmtLivePollRestart() must wire setInterval BEFORE the flag branch so
    the poll keeps ticking even when _smgmtRunningFirstPaint() handles first paint.
    (This is the corrected behaviour per the triage note: the Running tab uses
    a 2-second poll, NOT an SSE connection.)
    """

    def test_setinterval_precedes_flag_branch(self):
        """setInterval(_smgmtLivePollTick, 2000) must appear before the flag check."""
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
            "setInterval must be set BEFORE the flag branch so the poll is always active "
            f"(interval_pos={interval_pos}, flag_pos={flag_pos})"
        )

    def test_live_poll_tick_still_wired_when_flag_on(self):
        """Even with the flag ON, _smgmtLivePollTick must remain in the interval."""
        # setInterval always wires _smgmtLivePollTick; first-paint is a one-shot
        # initial call, not a replacement for the ongoing poll.
        restart_match = re.search(
            r"function _smgmtLivePollRestart\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert restart_match is not None
        body = restart_match.group(1)
        # Both the setInterval and the conditional first-paint call must coexist
        assert "setInterval(_smgmtLivePollTick, 2000)" in body
        assert "_smgmtRunningFirstPaint()" in body

    def test_no_sse_eventsource_in_running_tab_path(self):
        """The Running tab uses a 2-second poll, not an EventSource/SSE connection.

        Per triage note on #1647: the Running tab has NO EventSource — asserting
        that _smgmtRunningFirstPaint() does not open an EventSource is correct
        behaviour documentation.
        """
        fn_body_match = re.search(
            r"async function _smgmtRunningFirstPaint\(\)(.*?)^\}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert fn_body_match is not None
        fn_body = fn_body_match.group(0)
        assert "EventSource" not in fn_body, (
            "_smgmtRunningFirstPaint() must not open an EventSource — "
            "the Running tab uses a 2-second poll, not SSE"
        )
        assert "new EventSource" not in fn_body
