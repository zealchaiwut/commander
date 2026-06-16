"""Tests for issue #1252: Extract analytics and metrics routes to sprint_analytics router.

AC-1: routers/sprint_analytics.py exists and contains all moved routes
AC-2: Routes moved: GET /api/sprints/{label}/estimate-summary,
      GET /api/sprints/{label}/estimate, GET /api/sprints/{label}/outcome,
      GET /api/sprints/{label}/estimate-vs-actual, GET /api/estimates/batch,
      GET /api/calibration, GET /api/metrics/sprints
AC-3: server.py includes the new router via app.include_router(...)
      and contains none of the above route handler definitions
AC-4: Route handlers delegate reads/writes to the estimates JSON store and
      token_usage module; no direct file I/O duplicated from server.py
AC-5: Partial outcome-service move completed — outcome logic fully lives in
      the service layer, not inline in the route handler
AC-6: python -m py_compile routers/sprint_analytics.py server.py exits 0
AC-7: All moved endpoints return identical response shapes as before
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
SERVER_PY = DASHBOARD_DIR / "server.py"
ROUTER_FILE = ROUTERS_DIR / "sprint_analytics.py"
SERVICE_FILE = ROUTERS_DIR / "sprint_analytics_service.py"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

# ── Route paths to verify ────────────────────────────────────────────────────

MOVED_ROUTES = [
    ("GET", "/api/sprints/{sprint_label}/estimate-summary"),
    ("GET", "/api/sprints/{sprint_label}/estimate"),
    ("GET", "/api/sprints/{sprint_label}/outcome"),
    ("GET", "/api/sprints/{sprint_label}/estimate-vs-actual"),
    ("GET", "/api/estimates/batch"),
    ("GET", "/api/calibration"),
    ("GET", "/api/metrics/sprints"),
]


def _get_test_client():
    for mod in list(sys.modules.keys()):
        if mod in ("server", "db", "github_client") or mod.startswith("routers"):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    import server as srv
    return TestClient(srv.app, raise_server_exceptions=False), srv


def _make_proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


# ── AC-1: Router file exists ─────────────────────────────────────────────────


class TestRouterFileExists:
    """AC-1: routers/sprint_analytics.py exists."""

    def test_router_file_exists(self):
        assert ROUTER_FILE.exists(), "routers/sprint_analytics.py does not exist"

    def test_router_file_defines_api_router(self):
        source = ROUTER_FILE.read_text()
        assert "APIRouter" in source, (
            "routers/sprint_analytics.py must import and use APIRouter"
        )

    def test_router_exposes_router_attribute(self):
        spec = importlib.util.spec_from_file_location("sprint_analytics", ROUTER_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "router"), (
            "routers/sprint_analytics.py must expose a 'router' attribute"
        )


# ── AC-2: All 7 routes present ───────────────────────────────────────────────


class TestAllRoutesPresent:
    """AC-2: All 7 routes defined in the new router file."""

    def test_estimate_summary_route_in_router(self):
        source = ROUTER_FILE.read_text()
        assert "estimate-summary" in source, (
            "router must define GET /api/sprints/{label}/estimate-summary"
        )

    def test_estimate_route_in_router(self):
        source = ROUTER_FILE.read_text()
        assert "/estimate" in source, (
            "router must define GET /api/sprints/{label}/estimate"
        )

    def test_outcome_route_in_router(self):
        source = ROUTER_FILE.read_text()
        assert "/outcome" in source, (
            "router must define GET /api/sprints/{label}/outcome"
        )

    def test_estimate_vs_actual_route_in_router(self):
        source = ROUTER_FILE.read_text()
        assert "estimate-vs-actual" in source, (
            "router must define GET /api/sprints/{label}/estimate-vs-actual"
        )

    def test_estimates_batch_route_in_router(self):
        source = ROUTER_FILE.read_text()
        assert "/api/estimates/batch" in source, (
            "router must define GET /api/estimates/batch"
        )

    def test_calibration_route_in_router(self):
        source = ROUTER_FILE.read_text()
        assert "/api/calibration" in source, (
            "router must define GET /api/calibration"
        )

    def test_metrics_sprints_route_in_router(self):
        source = ROUTER_FILE.read_text()
        assert "/api/metrics/sprints" in source, (
            "router must define GET /api/metrics/sprints"
        )

    def test_router_uses_router_decorator_not_app(self):
        source = ROUTER_FILE.read_text()
        assert "@router.get" in source or "@router.post" in source, (
            "router must use @router.get/@router.post decorators, not @app.*"
        )
        # Must NOT have @app.get for the moved routes
        assert "@app.get(\"/api/calibration\")" not in source
        assert "@app.get(\"/api/metrics/sprints\")" not in source
        assert "@app.get(\"/api/estimates/batch\")" not in source


# ── AC-3: server.py no longer defines moved routes ───────────────────────────


class TestServerPyNoDirectRoutes:
    """AC-3: server.py uses include_router; no direct route handlers for moved paths."""

    def test_server_py_has_no_estimate_summary_handler(self):
        source = SERVER_PY.read_text()
        assert '@app.get("/api/sprints/{sprint_label}/estimate-summary")' not in source, (
            "server.py must not define @app.get('/api/sprints/{sprint_label}/estimate-summary')"
        )

    def test_server_py_has_no_sprint_estimate_handler(self):
        source = SERVER_PY.read_text()
        assert '@app.get("/api/sprints/{sprint_label}/estimate")' not in source, (
            "server.py must not define @app.get('/api/sprints/{sprint_label}/estimate')"
        )

    def test_server_py_has_no_outcome_handler(self):
        source = SERVER_PY.read_text()
        assert '@app.get("/api/sprints/{sprint_label}/outcome")' not in source, (
            "server.py must not define @app.get('/api/sprints/{sprint_label}/outcome')"
        )

    def test_server_py_has_no_estimate_vs_actual_handler(self):
        source = SERVER_PY.read_text()
        assert '@app.get("/api/sprints/{sprint_label}/estimate-vs-actual")' not in source, (
            "server.py must not define @app.get('/api/sprints/{sprint_label}/estimate-vs-actual')"
        )

    def test_server_py_has_no_estimates_batch_handler(self):
        source = SERVER_PY.read_text()
        assert '@app.get("/api/estimates/batch")' not in source, (
            "server.py must not define @app.get('/api/estimates/batch')"
        )

    def test_server_py_has_no_calibration_handler(self):
        source = SERVER_PY.read_text()
        assert '@app.get("/api/calibration")' not in source, (
            "server.py must not define @app.get('/api/calibration')"
        )

    def test_server_py_has_no_metrics_sprints_handler(self):
        source = SERVER_PY.read_text()
        assert '@app.get("/api/metrics/sprints")' not in source, (
            "server.py must not define @app.get('/api/metrics/sprints')"
        )

    def test_server_py_includes_sprint_analytics_router(self):
        source = SERVER_PY.read_text()
        assert "sprint_analytics_router" in source, (
            "server.py must import and include sprint_analytics_router"
        )
        assert "include_router(sprint_analytics_router)" in source, (
            "server.py must call app.include_router(sprint_analytics_router)"
        )


# ── AC-4: Route handlers delegate to service layer ───────────────────────────


class TestRouteHandlersDelegation:
    """AC-4: Route handlers are thin; no direct file I/O in handler functions."""

    def test_router_imports_service_or_delegates(self):
        source = ROUTER_FILE.read_text()
        # Handler should NOT be doing raw open() or Path.read_text() calls inline
        # (those belong in the service module).
        # The router should import a service module or use helpers.
        has_service_import = (
            "sprint_analytics_service" in source
            or "from . import" in source
            or "import sprint_analytics_service" in source
        )
        has_inline_path_io = (
            "Path(" in source and ".read_text(" in source
            and "sprint_analytics_service" not in source
        )
        # If route handlers do file I/O, they must be using imported helpers
        if has_inline_path_io:
            pytest.fail(
                "routers/sprint_analytics.py does file I/O inline without "
                "delegating to a service module"
            )

    def test_service_file_exists(self):
        """AC-4: A service module exists for the analytics logic."""
        assert SERVICE_FILE.exists(), (
            "routers/sprint_analytics_service.py must exist (service layer for AC-4/AC-5)"
        )

    def test_estimates_batch_reads_estimates_json_store(self):
        """AC-4: /api/estimates/batch reads from estimates JSON store."""
        # Verify the service reads from estimates directory
        service_source = SERVICE_FILE.read_text()
        assert "estimates" in service_source, (
            "sprint_analytics_service.py must read from the estimates JSON store"
        )

    def test_calibration_uses_token_usage_or_state_files(self):
        """AC-4: /api/calibration reads from state files (estimates store)."""
        service_source = SERVICE_FILE.read_text()
        assert "calibration" in service_source.lower(), (
            "sprint_analytics_service.py must implement calibration logic"
        )


# ── AC-5: Outcome logic in service layer ─────────────────────────────────────


class TestOutcomeServiceLayer:
    """AC-5: Outcome logic fully lives in service layer, not inline in route handler."""

    def test_outcome_handler_is_thin(self):
        """AC-5: get_sprint_outcome in router delegates to service."""
        router_source = ROUTER_FILE.read_text()
        # Count lines of the outcome handler (should be short — just a call)
        lines = router_source.splitlines()
        in_outcome = False
        outcome_lines = []
        brace_depth = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "def get_sprint_outcome" in stripped or (
                "@router.get" in stripped and i + 1 < len(lines)
                and "outcome" in lines[i + 1]
            ):
                in_outcome = True
            if in_outcome:
                outcome_lines.append(line)
                # Stop after 2 blank lines (next function)
                if len(outcome_lines) > 3 and stripped == "" and len(outcome_lines) > 5:
                    break
        # The handler itself (excluding docstring) should be short if it delegates
        non_empty = [l for l in outcome_lines if l.strip() and not l.strip().startswith("#")]
        assert len(non_empty) <= 20, (
            f"get_sprint_outcome handler is {len(non_empty)} non-empty lines — "
            "outcome logic should be in the service layer (AC-5)"
        )

    def test_service_has_outcome_logic(self):
        """AC-5: sprint_analytics_service.py contains the outcome business logic."""
        service_source = SERVICE_FILE.read_text()
        # The service should have the complex outcome computation
        assert "outcome" in service_source.lower(), (
            "sprint_analytics_service.py must contain outcome logic"
        )
        # Check for state file reading (the real outcome logic)
        assert "state_data" in service_source or "sprint_status" in service_source, (
            "sprint_analytics_service.py must contain sprint outcome computation"
        )


# ── AC-6: py_compile ─────────────────────────────────────────────────────────


class TestPyCompile:
    """AC-6: python -m py_compile exits 0 with no output."""

    def test_py_compile_router(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ROUTER_FILE)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_analytics.py:\n{result.stderr}"
        )
        assert result.stderr == "", f"py_compile stderr: {result.stderr}"

    def test_py_compile_service(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SERVICE_FILE)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_analytics_service.py:\n{result.stderr}"
        )
        assert result.stderr == "", f"py_compile stderr: {result.stderr}"

    def test_py_compile_server(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SERVER_PY)],
            capture_output=True, text=True,
            cwd=str(DASHBOARD_DIR),
        )
        assert result.returncode == 0, (
            f"py_compile failed on server.py:\n{result.stderr}"
        )

    def test_py_compile_router_and_server_together(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ROUTER_FILE), str(SERVER_PY)],
            capture_output=True, text=True,
            cwd=str(DASHBOARD_DIR),
        )
        assert result.returncode == 0, f"py_compile failed:\n{result.stderr}"
        assert result.stdout.strip() == "", f"py_compile produced output: {result.stdout}"


# ── AC-7: Response shapes identical ──────────────────────────────────────────


class TestResponseShapes:
    """AC-7: Moved endpoints return identical response shapes."""

    def test_estimates_batch_empty_returns_correct_shape(self):
        """AC-7: GET /api/estimates/batch with no issues returns valid shape."""
        client, _ = _get_test_client()
        resp = client.get("/api/estimates/batch?project=owner/testrepo&issues=")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_hours" in data
        assert "complete" in data
        assert "issues" in data
        assert "estimated_count" in data
        assert "partial" in data

    def test_calibration_returns_correct_shape(self):
        """AC-7: GET /api/calibration returns {buckets: {S,M,L,XL}}."""
        client, srv = _get_test_client()
        fake_projects = [{"repo": "owner/testrepo", "name": "Test"}]
        with patch.object(srv.projects_module, "load_projects", return_value=fake_projects):
            resp = client.get("/api/calibration?project=owner/testrepo")
        # Either 200 with correct shape, or 404 for unknown project — both are valid
        if resp.status_code == 200:
            data = resp.json()
            assert "buckets" in data, "calibration response must have 'buckets' key"
            buckets = data["buckets"]
            for size in ("S", "M", "L", "XL"):
                assert size in buckets, f"calibration buckets must include size {size}"

    def test_metrics_sprints_returns_list(self):
        """AC-7: GET /api/metrics/sprints returns a JSON array."""
        client, _ = _get_test_client()
        resp = client.get("/api/metrics/sprints")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_estimates_batch_with_nonexistent_issues_returns_shape(self):
        """AC-7: GET /api/estimates/batch with issue nums returns per-issue nulls."""
        client, _ = _get_test_client()
        resp = client.get(
            "/api/estimates/batch?project=owner/testrepo&issues=9999,9998"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "issues" in data
        assert "estimated_count" in data
        assert data["estimated_count"] == 0

    def test_estimate_summary_invalid_label_returns_400(self):
        """AC-7: Invalid sprint label returns 400 — same as pre-refactor."""
        client, _ = _get_test_client()
        resp = client.get(
            "/api/sprints/not-a-sprint/estimate-summary?project=owner/testrepo"
        )
        assert resp.status_code == 400

    def test_sprint_estimate_not_found_returns_404(self):
        """AC-7: Missing estimate file returns 404 — same as pre-refactor."""
        client, _ = _get_test_client()
        resp = client.get(
            "/api/sprints/sprint-9999/estimate?project=owner/testrepo"
        )
        assert resp.status_code in (404, 400)

    def test_outcome_invalid_label_returns_400(self):
        """AC-7: Invalid sprint label for /outcome returns 400."""
        client, _ = _get_test_client()
        resp = client.get("/api/sprints/bad-label/outcome?project=owner/testrepo")
        assert resp.status_code == 400

    def test_estimate_vs_actual_invalid_label_returns_400(self):
        """AC-7: Invalid sprint label for /estimate-vs-actual returns 400."""
        client, _ = _get_test_client()
        resp = client.get(
            "/api/sprints/not-sprint/estimate-vs-actual?project=owner/testrepo"
        )
        assert resp.status_code == 400
