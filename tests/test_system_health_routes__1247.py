"""Tests for issue #1247: Extract system/health routes from server.py to routers/system.py.

Acceptance Criteria verified here:
  AC1: routers/system.py exists and registers all moved routes via an APIRouter
  AC2: server.py includes the new router and contains zero inline handler functions for moved routes
  AC3: Moved routes respond correctly over HTTP
  AC4: Total registered route count in the running app is unchanged (243)
  AC5: python -m py_compile routers/system.py server.py exits 0 with no output
  AC6: No new routes introduced; no existing routes removed or renamed (covered by AC3+AC4)
  AC7: All moved handlers are thin wrappers (no business logic inline)
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"
SYSTEM_PY = DASHBOARD_DIR / "routers" / "system.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── AC1: routers/system.py exists and registers all moved routes ──────────────

class TestAC1SystemRouterHasAllRoutes:
    """AC1: routers/system.py exists and registers all moved routes via APIRouter."""

    def test_system_py_exists(self):
        assert SYSTEM_PY.exists(), "routers/system.py must exist"

    def test_system_py_has_api_router(self):
        content = SYSTEM_PY.read_text()
        assert "APIRouter" in content, "routers/system.py must use APIRouter"
        assert "router = APIRouter" in content, \
            "routers/system.py must declare router = APIRouter(...)"

    def test_health_route_in_system_py(self):
        content = SYSTEM_PY.read_text()
        assert "/api/health" in content, \
            "GET /api/health must be registered in routers/system.py"

    def test_environment_route_in_system_py(self):
        content = SYSTEM_PY.read_text()
        assert "/api/environment" in content, \
            "GET /api/environment must be registered in routers/system.py"

    def test_repo_config_route_in_system_py(self):
        content = SYSTEM_PY.read_text()
        assert "/api/repo/config" in content, \
            "GET /api/repo/config must be registered in routers/system.py"

    def test_github_labels_route_in_system_py(self):
        content = SYSTEM_PY.read_text()
        assert "/api/github/labels" in content, \
            "GET /api/github/labels must be registered in routers/system.py"

    def test_version_route_in_system_py(self):
        content = SYSTEM_PY.read_text()
        assert "/api/version" in content, \
            "GET /api/version must be registered in routers/system.py"

    def test_gh_auth_status_route_in_system_py(self):
        content = SYSTEM_PY.read_text()
        assert "/api/gh-auth-status" in content, \
            "GET /api/gh-auth-status must be registered in routers/system.py"


# ── AC2: server.py has zero inline handlers for moved routes ──────────────────

class TestAC2ServerPyHasNoInlineHandlers:
    """AC2: server.py contains zero inline @app.get/post handlers for moved routes."""

    def _inline_handlers(self) -> dict[str, set[str]]:
        """Return mapping method→set-of-paths for @app.<method>(path) decorators."""
        tree = ast.parse(SERVER_PY.read_text())
        handlers: dict[str, set[str]] = {"get": set(), "post": set()}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                    continue
                if func.value.id == "app" and func.attr in handlers:
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        handlers[func.attr].add(decorator.args[0].value)
        return handlers

    def test_no_health_inline_handler(self):
        h = self._inline_handlers()
        assert "/api/health" not in h["get"], \
            "server.py must not have @app.get('/api/health') — moved to routers/system.py"

    def test_no_environment_inline_handler(self):
        h = self._inline_handlers()
        assert "/api/environment" not in h["get"], \
            "server.py must not have @app.get('/api/environment') — moved to routers/system.py"

    def test_no_repo_config_inline_handler(self):
        h = self._inline_handlers()
        assert "/api/repo/config" not in h["get"], \
            "server.py must not have @app.get('/api/repo/config') — moved to routers/system.py"

    def test_no_github_labels_inline_get_handler(self):
        h = self._inline_handlers()
        assert "/api/github/labels" not in h["get"], \
            "server.py must not have @app.get('/api/github/labels') — moved to routers/system.py"

    def test_no_github_labels_inline_post_handler(self):
        h = self._inline_handlers()
        assert "/api/github/labels" not in h["post"], \
            "server.py must not have @app.post('/api/github/labels') — moved to routers/system.py"

    def test_server_includes_system_router(self):
        content = SERVER_PY.read_text()
        assert "app.include_router(system_router)" in content, \
            "server.py must call app.include_router(system_router)"


# ── AC3: Moved routes respond correctly over HTTP ─────────────────────────────

@pytest.fixture(scope="module")
def test_client():
    if "server" in sys.modules:
        del sys.modules["server"]
    with patch("services.logging.log"):
        import server as srv
        from fastapi.testclient import TestClient
        yield TestClient(srv.app, raise_server_exceptions=False)


class TestAC3MovedRoutesWork:
    """AC3: All moved routes respond correctly (HTTP-level)."""

    def test_get_health_returns_200(self, test_client):
        resp = test_client.get("/api/health")
        assert resp.status_code == 200, f"GET /api/health returned {resp.status_code}"
        data = resp.json()
        assert isinstance(data, dict), "GET /api/health must return a JSON object"
        assert "status" in data, "GET /api/health response must have 'status' field"

    def test_get_environment_returns_200(self, test_client):
        resp = test_client.get("/api/environment")
        assert resp.status_code == 200, f"GET /api/environment returned {resp.status_code}"
        data = resp.json()
        assert "environment" in data, "GET /api/environment must return {'environment': ...}"

    def test_get_version_returns_200(self, test_client):
        resp = test_client.get("/api/version")
        assert resp.status_code == 200, f"GET /api/version returned {resp.status_code}"
        assert isinstance(resp.json(), dict), "GET /api/version must return a JSON object"

    def test_get_gh_auth_status_returns_200(self, test_client):
        resp = test_client.get("/api/gh-auth-status")
        assert resp.status_code == 200, f"GET /api/gh-auth-status returned {resp.status_code}"
        assert isinstance(resp.json(), dict), "GET /api/gh-auth-status must return a JSON object"

    def test_get_repo_config_responds(self, test_client):
        resp = test_client.get("/api/repo/config")
        assert resp.status_code in (200, 400), \
            f"GET /api/repo/config returned unexpected status {resp.status_code}"

    def test_get_github_labels_responds(self, test_client):
        resp = test_client.get("/api/github/labels")
        assert resp.status_code in (200, 400, 422, 429, 502), \
            f"GET /api/github/labels returned unexpected status {resp.status_code}"


# ── AC4: Total route count is unchanged ──────────────────────────────────────

class TestAC4RouteCountUnchanged:
    """AC4: Total registered route count must equal the pre-extraction baseline."""

    BASELINE = 243  # route count confirmed before issue #1247 extraction

    def test_route_count_equals_baseline(self, test_client):
        import server as srv
        routes = [r for r in srv.app.routes if hasattr(r, "path")]
        assert len(routes) == self.BASELINE, (
            f"Expected {self.BASELINE} routes (pre-#1247 baseline), got {len(routes)}. "
            "Moving routes must not change the total registered count."
        )


# ── AC5: py_compile exits 0 ───────────────────────────────────────────────────

class TestAC5PyCompile:
    """AC5: python -m py_compile routers/system.py server.py exits 0 with no output."""

    def test_py_compile_system_py(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SYSTEM_PY)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, \
            f"py_compile routers/system.py failed:\n{result.stderr}"
        assert result.stderr == "", \
            f"py_compile routers/system.py produced stderr: {result.stderr}"

    def test_py_compile_server_py(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SERVER_PY)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, \
            f"py_compile server.py failed:\n{result.stderr}"
        assert result.stderr == "", \
            f"py_compile server.py produced stderr: {result.stderr}"


# ── AC7: All moved handlers are thin wrappers ────────────────────────────────

class TestAC7ThinHandlers:
    """AC7: Moved handlers in system.py are thin wrappers — no inline business logic."""

    def _handler_line_count(self, route_path: str) -> int:
        """Return the body line count of the handler for route_path in system.py."""
        tree = ast.parse(SYSTEM_PY.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    if dec.args[0].value == route_path:
                        return (node.end_lineno or node.lineno) - node.lineno + 1
        return -1

    def test_health_handler_is_thin(self):
        n = self._handler_line_count("/api/health")
        assert n != -1, "/api/health handler not found in system.py"
        assert n <= 10, (
            f"/api/health handler is {n} lines in system.py — "
            "must be <=10 (thin wrapper; logic stays in system_service.py / server.py)"
        )

    def test_environment_handler_is_thin(self):
        n = self._handler_line_count("/api/environment")
        assert n != -1, "/api/environment handler not found in system.py"
        assert n <= 5, f"/api/environment handler is {n} lines — must be <=5"

    def test_repo_config_handler_is_thin(self):
        n = self._handler_line_count("/api/repo/config")
        assert n != -1, "/api/repo/config handler not found in system.py"
        assert n <= 5, f"/api/repo/config handler is {n} lines — must be <=5"

    def test_github_labels_handler_is_thin(self):
        n = self._handler_line_count("/api/github/labels")
        assert n != -1, "/api/github/labels handler not found in system.py"
        assert n <= 5, f"/api/github/labels handler is {n} lines — must be <=5"
