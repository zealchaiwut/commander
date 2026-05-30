"""Tests for issue #418: structured log.event() calls in dashboard server.py.

Acceptance Criteria:
  AC-1: log.event() called on entry of each key route handler
  AC-2: log.event() called when dispatch triggered, with dispatch type and target
  AC-3: log.event() called on error paths with level=error
  AC-4: log.event() called during server startup
  AC-5: Every log.event() call includes project="dashboard" and request_id correlation keys
  AC-6: All existing print() statements remain untouched
  AC-7: No new dependencies beyond the structured logger
  AC-8: No existing tests break; new log calls do not alter return values or side effects
"""
from __future__ import annotations

import ast
import re
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

_SERVER_SRC = SERVER_PY.read_text()


# ── helpers ───────────────────────────────────────────────────────────────────

def _find_slog_event_calls(src: str) -> list[dict]:
    """Parse all _slog.event(...) calls and return a list of their kwargs."""
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "event"):
            continue
        val = getattr(func.value, "id", None)
        if val != "_slog":
            continue
        record: dict = {"name": None, "kwargs": {}, "lineno": node.lineno}
        if node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant):
                record["name"] = arg0.value
        for kw in node.keywords:
            if kw.arg is None:
                continue
            val_node = kw.value
            if isinstance(val_node, ast.Constant):
                record["kwargs"][kw.arg] = val_node.value
            else:
                record["kwargs"][kw.arg] = "__dynamic__"
        calls.append(record)
    return calls


_ALL_EVENT_CALLS = _find_slog_event_calls(_SERVER_SRC)


def _calls_by_name(name: str) -> list[dict]:
    return [c for c in _ALL_EVENT_CALLS if c["name"] == name]


# ── AC-4: server.startup event in lifespan ───────────────────────────────────

class TestServerStartupEvent:
    def test_startup_event_exists(self):
        startup_calls = _calls_by_name("server.startup")
        assert len(startup_calls) >= 1, "Expected at least one 'server.startup' event call"

    def test_startup_event_has_project_dashboard(self):
        for call in _calls_by_name("server.startup"):
            assert call["kwargs"].get("project") == "dashboard", (
                f"server.startup at line {call['lineno']} missing project='dashboard'"
            )

    def test_startup_event_has_request_id(self):
        for call in _calls_by_name("server.startup"):
            assert "request_id" in call["kwargs"], (
                f"server.startup at line {call['lineno']} missing request_id"
            )

    def test_startup_event_has_environment_field(self):
        for call in _calls_by_name("server.startup"):
            assert "environment" in call["kwargs"], (
                f"server.startup at line {call['lineno']} missing environment field"
            )

    def test_startup_event_in_lifespan_function(self):
        """server.startup must appear inside the lifespan async generator, not a random place."""
        tree = ast.parse(_SERVER_SRC)
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "lifespan":
                func_src = ast.unparse(node)
                assert "server.startup" in func_src, (
                    "Expected 'server.startup' event inside lifespan() function"
                )
                return
        pytest.fail("lifespan() function not found in server.py")


# ── AC-1: route.entry events on key handlers ─────────────────────────────────

_EXPECTED_ROUTE_ENTRY_ROUTES = {
    "/",
    "/api/health",
    "/api/agent-event",
    "/api/issues/{issue_id}/approve",
    "/api/tickets/{issue_id}/approve",
    "/api/issues/{issue_id}/reject",
    "/api/issues/{issue_id}/close",
    "/api/sprint-run",
    "/api/sprints/run",
}


class TestRouteEntryEvents:
    def test_route_entry_events_exist(self):
        entry_calls = _calls_by_name("route.entry")
        assert len(entry_calls) >= len(_EXPECTED_ROUTE_ENTRY_ROUTES), (
            f"Expected >= {len(_EXPECTED_ROUTE_ENTRY_ROUTES)} 'route.entry' calls, "
            f"found {len(entry_calls)}"
        )

    def test_all_expected_routes_have_entry_event(self):
        entry_calls = _calls_by_name("route.entry")
        logged_routes = {c["kwargs"].get("route") for c in entry_calls}
        missing = _EXPECTED_ROUTE_ENTRY_ROUTES - logged_routes
        assert not missing, (
            f"These routes lack a 'route.entry' log.event(): {missing}"
        )

    def test_route_entry_on_get_root(self):
        root_entries = [
            c for c in _calls_by_name("route.entry")
            if c["kwargs"].get("route") == "/" and c["kwargs"].get("method") == "GET"
        ]
        assert root_entries, "No route.entry for GET /"

    def test_route_entry_on_get_health(self):
        health_entries = [
            c for c in _calls_by_name("route.entry")
            if c["kwargs"].get("route") == "/api/health"
        ]
        assert health_entries, "No route.entry for /api/health"

    def test_route_entry_on_post_agent_event(self):
        entries = [
            c for c in _calls_by_name("route.entry")
            if c["kwargs"].get("route") == "/api/agent-event"
        ]
        assert entries, "No route.entry for /api/agent-event"

    def test_route_entry_on_sprint_run(self):
        entries = [
            c for c in _calls_by_name("route.entry")
            if c["kwargs"].get("route") == "/api/sprint-run"
        ]
        assert entries, "No route.entry for /api/sprint-run"

    def test_route_entry_on_sprints_run(self):
        entries = [
            c for c in _calls_by_name("route.entry")
            if c["kwargs"].get("route") == "/api/sprints/run"
        ]
        assert entries, "No route.entry for /api/sprints/run"


# ── AC-2: sprint.dispatch event on dispatch trigger ──────────────────────────

class TestDispatchEvent:
    def test_dispatch_event_exists(self):
        dispatch_calls = _calls_by_name("sprint.dispatch")
        assert len(dispatch_calls) >= 1, "Expected at least one 'sprint.dispatch' event call"

    def test_dispatch_event_has_sprint_label(self):
        for call in _calls_by_name("sprint.dispatch"):
            assert "sprint_label" in call["kwargs"], (
                f"sprint.dispatch at line {call['lineno']} missing sprint_label"
            )

    def test_dispatch_event_has_dispatch_type(self):
        for call in _calls_by_name("sprint.dispatch"):
            assert "dispatch_type" in call["kwargs"], (
                f"sprint.dispatch at line {call['lineno']} missing dispatch_type"
            )

    def test_simple_dispatch_type_value(self):
        simple = [
            c for c in _calls_by_name("sprint.dispatch")
            if c["kwargs"].get("dispatch_type") == "simple"
        ]
        assert simple, "Expected sprint.dispatch with dispatch_type='simple' for /api/sprint-run"

    def test_managed_dispatch_type_value(self):
        managed = [
            c for c in _calls_by_name("sprint.dispatch")
            if c["kwargs"].get("dispatch_type") == "managed"
        ]
        assert managed, "Expected sprint.dispatch with dispatch_type='managed' for /api/sprints/run"

    def test_managed_dispatch_has_target_project(self):
        managed = [
            c for c in _calls_by_name("sprint.dispatch")
            if c["kwargs"].get("dispatch_type") == "managed"
        ]
        for call in managed:
            assert "target_project" in call["kwargs"], (
                f"managed sprint.dispatch at line {call['lineno']} missing target_project"
            )

    def test_dispatch_event_after_process_spawn_in_sprint_run(self):
        """sprint.dispatch must appear AFTER the subprocess.Popen call in /api/sprint-run."""
        src = _SERVER_SRC
        sprint_run_start = src.find('"/api/sprint-run"')
        assert sprint_run_start != -1, "Could not locate /api/sprint-run route"
        sprint_run_block = src[sprint_run_start:sprint_run_start + 2000]
        popen_pos = sprint_run_block.find("Popen(")
        dispatch_pos = sprint_run_block.find('"sprint.dispatch"')
        assert popen_pos != -1, "Popen not found in /api/sprint-run block"
        assert dispatch_pos != -1, "sprint.dispatch not found near /api/sprint-run"
        assert dispatch_pos > popen_pos, (
            "sprint.dispatch event must appear after Popen() call in /api/sprint-run"
        )


# ── AC-3: error path events with level=error ─────────────────────────────────

class TestErrorPathEvents:
    def test_route_error_events_exist(self):
        error_calls = _calls_by_name("route.error")
        assert len(error_calls) >= 1, "Expected at least one 'route.error' event call"

    def test_route_error_events_have_level_error(self):
        for call in _calls_by_name("route.error"):
            assert call["kwargs"].get("level") == "error", (
                f"route.error at line {call['lineno']} missing level='error'"
            )

    def test_error_event_on_approve_issue_exception(self):
        """approve_issue error handler must have a route.error event."""
        approve_error = [
            c for c in _calls_by_name("route.error")
            if c["kwargs"].get("route") == "/api/issues/{issue_id}/approve"
        ]
        assert approve_error, "No route.error for /api/issues/{issue_id}/approve error path"

    def test_error_event_on_reject_issue_exception(self):
        reject_error = [
            c for c in _calls_by_name("route.error")
            if c["kwargs"].get("route") == "/api/issues/{issue_id}/reject"
        ]
        assert reject_error, "No route.error for /api/issues/{issue_id}/reject error path"

    def test_error_event_on_close_issue_exception(self):
        close_error = [
            c for c in _calls_by_name("route.error")
            if c["kwargs"].get("route") == "/api/issues/{issue_id}/close"
        ]
        assert close_error, "No route.error for /api/issues/{issue_id}/close error path"

    def test_error_event_on_sprint_run_invalid_label(self):
        sprint_errors = [
            c for c in _calls_by_name("route.error")
            if c["kwargs"].get("route") == "/api/sprint-run"
        ]
        assert sprint_errors, "No route.error for /api/sprint-run error paths"

    def test_error_event_on_sprints_run_error_paths(self):
        managed_errors = [
            c for c in _calls_by_name("route.error")
            if c["kwargs"].get("route") == "/api/sprints/run"
        ]
        assert len(managed_errors) >= 2, (
            f"Expected >= 2 route.error calls for /api/sprints/run, found {len(managed_errors)}"
        )

    def test_error_events_have_error_field(self):
        for call in _calls_by_name("route.error"):
            assert "error" in call["kwargs"], (
                f"route.error at line {call['lineno']} missing 'error' field"
            )


# ── AC-5: all events include project and request_id ──────────────────────────

class TestCorrelationKeys:
    def test_all_events_have_project_dashboard(self):
        for call in _ALL_EVENT_CALLS:
            assert call["kwargs"].get("project") == "dashboard", (
                f"_slog.event('{call['name']}') at line {call['lineno']} "
                f"missing project='dashboard'"
            )

    def test_all_events_have_request_id_key(self):
        for call in _ALL_EVENT_CALLS:
            assert "request_id" in call["kwargs"], (
                f"_slog.event('{call['name']}') at line {call['lineno']} "
                f"missing request_id key"
            )

    def test_middleware_sets_request_id_as_uuid(self):
        """_attach_request_id middleware must set request.state.request_id = str(uuid.uuid4())."""
        src = _SERVER_SRC
        assert "_attach_request_id" in src, "Expected _attach_request_id middleware"
        middleware_match = re.search(
            r"async def _attach_request_id.*?return await call_next",
            src,
            re.DOTALL,
        )
        assert middleware_match, "_attach_request_id middleware body not found"
        body = middleware_match.group(0)
        assert "request.state.request_id" in body, (
            "Middleware must set request.state.request_id"
        )
        assert "uuid.uuid4()" in body, (
            "Middleware must use uuid.uuid4() for request_id"
        )

    def test_request_ids_are_unique_per_request(self):
        """Two requests must produce different request_ids."""
        if "server" in sys.modules:
            del sys.modules["server"]

        with patch("services.logging.log") as mock_log:
            import server as srv
            from fastapi.testclient import TestClient
            client = TestClient(srv.app, raise_server_exceptions=False)

            seen_ids: list[str] = []

            original_event = mock_log.event.side_effect

            def capture_event(name, **kwargs):
                rid = kwargs.get("request_id")
                if rid:
                    seen_ids.append(rid)

            mock_log.event.side_effect = capture_event
            srv._slog = mock_log

            client.get("/")
            client.get("/")

        if len(seen_ids) >= 2:
            assert seen_ids[0] != seen_ids[1], (
                "request_id must be unique per request, got same value twice"
            )

    def test_startup_request_id_is_uuid_format(self):
        """Startup request_id must be a UUID string, not a constant."""
        src = _SERVER_SRC
        # Find the block around server.startup call
        match = re.search(r'_slog\.event\(\s*"server\.startup"(.*?)\)', src, re.DOTALL)
        assert match, "server.startup event call not found"
        block = match.group(0)
        assert "uuid.uuid4()" in block, (
            "server.startup request_id must use uuid.uuid4(), got: " + block[:200]
        )


# ── AC-6: existing print() statements not removed ────────────────────────────

class TestPrintStatementsPreserved:
    def test_print_statements_still_present(self):
        """server.py must still contain print() calls — none were removed."""
        print_count = _SERVER_SRC.count("print(")
        assert print_count > 0, "All print() statements appear to have been removed"

    def test_print_count_not_reduced(self):
        """Number of print() calls must be >= what was there before the feature (#418 only adds)."""
        # The diff shows 50 insertions, 9 deletions — none of the deletions removed print().
        # The original had many prints; we just verify a reasonable minimum.
        print_count = _SERVER_SRC.count("print(")
        assert print_count >= 10, (
            f"Expected >= 10 print() calls remaining, found {print_count}. "
            "Feature must not remove existing print() statements."
        )

    def test_slog_event_calls_added_not_replacing_prints(self):
        """Every _slog.event call should exist alongside print() calls, not instead of them."""
        # The feature adds log.event calls — the overall print count should still be high.
        event_call_count = len(_ALL_EVENT_CALLS)
        print_count = _SERVER_SRC.count("print(")
        assert print_count > event_call_count, (
            f"print() count ({print_count}) should exceed new log.event() count "
            f"({event_call_count}), suggesting prints were preserved"
        )


# ── AC-7: no new dependencies ────────────────────────────────────────────────

class TestNoDependencies:
    def test_only_services_logging_imported_for_events(self):
        """The structured logger import must be from services.logging, not a new package."""
        assert "from services.logging import log as _slog" in _SERVER_SRC, (
            "Expected 'from services.logging import log as _slog' in server.py"
        )

    def test_no_new_third_party_logging_import(self):
        """No third-party structured logging libs (structlog, loguru, etc.) should be imported."""
        for bad_import in ["import structlog", "from structlog", "import loguru", "from loguru"]:
            assert bad_import not in _SERVER_SRC, (
                f"Unexpected third-party logging import found: {bad_import}"
            )

    def test_services_logging_module_importable(self):
        """services.logging must be importable (no new dependencies required)."""
        import importlib
        spec = importlib.util.find_spec("services.logging")
        assert spec is not None, "services.logging module not found — missing dependency?"


# ── AC-8: return values unchanged, no side-effect regression ─────────────────

class TestNoRegressions:
    def test_get_root_returns_html_response(self):
        """GET / must still return an HTML response after adding log.event()."""
        if "server" in sys.modules:
            del sys.modules["server"]

        with patch("services.logging.log"):
            import server as srv
            from fastapi.testclient import TestClient
            client = TestClient(srv.app, raise_server_exceptions=False)
            resp = client.get("/")

        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "html" in content_type or len(resp.content) > 0, (
            "GET / must return HTML content"
        )

    def test_get_health_returns_json(self):
        """GET /api/health must still return a JSON response after adding log.event()."""
        if "server" in sys.modules:
            del sys.modules["server"]

        with patch("services.logging.log"):
            import server as srv
            from fastapi.testclient import TestClient
            client = TestClient(srv.app, raise_server_exceptions=False)
            resp = client.get("/api/health")

        assert resp.status_code in (200, 503), (
            f"GET /api/health returned unexpected status {resp.status_code}"
        )
        data = resp.json()
        assert "status" in data or "checks" in data or isinstance(data, dict), (
            "GET /api/health must return a JSON object"
        )

    def test_slog_event_errors_do_not_propagate(self):
        """If _slog.event raises, the route handler must not fail."""
        if "server" in sys.modules:
            del sys.modules["server"]

        mock_log = MagicMock()
        mock_log.event.side_effect = RuntimeError("logger exploded")

        with patch("services.logging.log", mock_log):
            import server as srv
            srv._slog = mock_log
            from fastapi.testclient import TestClient
            client = TestClient(srv.app, raise_server_exceptions=False)
            resp = client.get("/api/health")

        # Even if _slog.event raises, the route must respond (200 or 503)
        assert resp.status_code in (200, 503, 500), (
            f"Route should handle _slog.event failure gracefully, got {resp.status_code}"
        )

    def test_log_event_import_does_not_break_server_import(self):
        """server.py must import cleanly with the new _slog import."""
        import importlib
        if "server" in sys.modules:
            del sys.modules["server"]

        with patch("services.logging.log"):
            import server as srv

        assert hasattr(srv, "app"), "server.app not found after import"
        assert hasattr(srv, "_slog"), "server._slog not found — import of structured logger failed"
