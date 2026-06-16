"""Tests for issue #1248: Extract page-serving handlers from server.py to routers/pages.py.

Acceptance Criteria verified here:
  AC1: routers/pages.py exists and contains all moved route handlers (GET /, /home, /overview, /project/*)
  AC2: Version-hash injection helper is moved to routers/pages.py (not duplicated)
  AC3: server.py registers routers/pages.py router and contains no inline page-serving handler definitions
  AC4: Total route count across the app is unchanged before and after the refactor
  AC5: py_compile passes on both server.py and routers/pages.py with no errors
  AC6: No new routes are introduced by this change
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
PAGES_PY = DASHBOARD_DIR / "routers" / "pages.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── AC1: routers/pages.py exists and contains all moved route handlers ────────

class TestAC1PagesRouterHasAllRoutes:
    """AC1: routers/pages.py exists and registers all moved page routes via APIRouter."""

    def test_pages_py_exists(self):
        assert PAGES_PY.exists(), "routers/pages.py must exist"

    def test_pages_py_has_api_router(self):
        content = PAGES_PY.read_text()
        assert "APIRouter" in content, "routers/pages.py must use APIRouter"
        assert "router = APIRouter" in content, \
            "routers/pages.py must declare router = APIRouter(...)"

    def test_root_route_in_pages_py(self):
        content = PAGES_PY.read_text()
        assert '"/")' in content or '"/",' in content or "('/')" in content, \
            "GET / must be registered in routers/pages.py"

    def test_home_route_in_pages_py(self):
        content = PAGES_PY.read_text()
        assert '"/home"' in content, \
            "GET /home must be registered in routers/pages.py"

    def test_overview_route_in_pages_py(self):
        content = PAGES_PY.read_text()
        assert '"/overview"' in content, \
            "GET /overview must be registered in routers/pages.py"

    def test_project_slug_route_in_pages_py(self):
        content = PAGES_PY.read_text()
        assert '"/project/{slug}"' in content, \
            "GET /project/{slug} must be registered in routers/pages.py"

    def test_project_slug_tab_route_in_pages_py(self):
        content = PAGES_PY.read_text()
        assert '"/project/{slug}/{tab}"' in content, \
            "GET /project/{slug}/{tab} must be registered in routers/pages.py"

    def test_projects_redirect_route_in_pages_py(self):
        content = PAGES_PY.read_text()
        assert '"/projects/{path:path}"' in content, \
            "GET /projects/{path:path} must be registered in routers/pages.py"


# ── AC2: Version-hash injection helper is in pages.py (not duplicated) ────────

class TestAC2VersionHashHelperMoved:
    """AC2: _inject_version_into_html is in routers/pages.py and NOT in server.py."""

    def test_inject_version_helper_in_pages_py(self):
        content = PAGES_PY.read_text()
        assert "_inject_version_into_html" in content, \
            "routers/pages.py must define _inject_version_into_html"

    def test_inject_version_helper_not_in_server_py(self):
        content = SERVER_PY.read_text()
        assert "def _inject_version_into_html" not in content, \
            "server.py must not define _inject_version_into_html — it was moved to routers/pages.py"

    def test_serve_html_helper_in_pages_py(self):
        content = PAGES_PY.read_text()
        assert "_serve_html" in content, \
            "routers/pages.py must define _serve_html helper"

    def test_serve_html_helper_not_in_server_py(self):
        content = SERVER_PY.read_text()
        assert "def _serve_html" not in content, \
            "server.py must not define _serve_html — it was moved to routers/pages.py"


# ── AC3: server.py registers pages_router and has no inline page handlers ─────

class TestAC3ServerPyHasNoInlineHandlers:
    """AC3: server.py includes pages_router and has zero inline page-serving handlers."""

    def _inline_app_get_handlers(self) -> set[str]:
        """Return set of paths registered with @app.get(...) in server.py."""
        tree = ast.parse(SERVER_PY.read_text())
        paths: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                    continue
                if func.value.id == "app" and func.attr == "get":
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        paths.add(decorator.args[0].value)
        return paths

    def test_no_root_inline_handler(self):
        h = self._inline_app_get_handlers()
        assert "/" not in h, \
            "server.py must not have @app.get('/') — moved to routers/pages.py"

    def test_no_home_inline_handler(self):
        h = self._inline_app_get_handlers()
        assert "/home" not in h, \
            "server.py must not have @app.get('/home') — moved to routers/pages.py"

    def test_no_overview_inline_handler(self):
        h = self._inline_app_get_handlers()
        assert "/overview" not in h, \
            "server.py must not have @app.get('/overview') — moved to routers/pages.py"

    def test_no_projects_path_inline_handler(self):
        h = self._inline_app_get_handlers()
        assert "/projects/{path:path}" not in h, \
            "server.py must not have @app.get('/projects/{path:path}') — moved to routers/pages.py"

    def test_no_project_slug_inline_handler(self):
        h = self._inline_app_get_handlers()
        assert "/project/{slug}" not in h, \
            "server.py must not have @app.get('/project/{slug}') — moved to routers/pages.py"

    def test_no_project_slug_tab_inline_handler(self):
        h = self._inline_app_get_handlers()
        assert "/project/{slug}/{tab}" not in h, \
            "server.py must not have @app.get('/project/{slug}/{tab}') — moved to routers/pages.py"

    def test_server_includes_pages_router(self):
        content = SERVER_PY.read_text()
        assert "app.include_router(pages_router)" in content, \
            "server.py must call app.include_router(pages_router)"


# ── AC4: Total route count is unchanged ──────────────────────────────────────

@pytest.fixture(scope="module")
def test_client():
    if "server" in sys.modules:
        del sys.modules["server"]
    with patch("services.logging.log"):
        import server as srv
        from fastapi.testclient import TestClient
        yield TestClient(srv.app, raise_server_exceptions=False)


class TestAC4RouteCountUnchanged:
    """AC4: Total registered route count must equal the pre-extraction baseline (243)."""

    BASELINE = 243

    def test_route_count_equals_baseline(self, test_client):
        import server as srv
        routes = [r for r in srv.app.routes if hasattr(r, "path")]
        assert len(routes) == self.BASELINE, (
            f"Expected {self.BASELINE} routes (pre-#1248 baseline), got {len(routes)}. "
            "Moving routes must not change the total registered count."
        )


# ── AC5: py_compile exits 0 ───────────────────────────────────────────────────

class TestAC5PyCompile:
    """AC5: python -m py_compile passes on both server.py and routers/pages.py."""

    def test_py_compile_pages_py(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(PAGES_PY)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, \
            f"py_compile routers/pages.py failed:\n{result.stderr}"
        assert result.stderr == "", \
            f"py_compile routers/pages.py produced stderr: {result.stderr}"

    def test_py_compile_server_py(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SERVER_PY)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, \
            f"py_compile server.py failed:\n{result.stderr}"
        assert result.stderr == "", \
            f"py_compile server.py produced stderr: {result.stderr}"


# ── AC6: Routes respond correctly over HTTP ──────────────────────────────────

class TestAC6MovedRoutesWork:
    """AC6: All moved page routes respond correctly (HTTP-level)."""

    def test_root_returns_200(self, test_client):
        resp = test_client.get("/")
        assert resp.status_code == 200, f"GET / returned {resp.status_code}"
        assert "html" in resp.headers.get("content-type", "").lower(), \
            "GET / must return HTML content"

    def test_home_redirect(self, test_client):
        resp = test_client.get("/home", follow_redirects=False)
        assert resp.status_code in (301, 302), \
            f"GET /home must redirect, got {resp.status_code}"

    def test_overview_redirect(self, test_client):
        resp = test_client.get("/overview", follow_redirects=False)
        assert resp.status_code in (301, 302), \
            f"GET /overview must redirect, got {resp.status_code}"

    def test_project_slug_redirects_to_tab(self, test_client):
        resp = test_client.get("/project/commander", follow_redirects=False)
        assert resp.status_code in (301, 302), \
            f"GET /project/commander must redirect, got {resp.status_code}"
        assert "/sprint-mgmt" in resp.headers.get("location", ""), \
            "GET /project/<slug> must redirect to .../sprint-mgmt"

    def test_project_slug_valid_tab_returns_html(self, test_client):
        resp = test_client.get("/project/commander/sprint-mgmt")
        assert resp.status_code == 200, \
            f"GET /project/commander/sprint-mgmt returned {resp.status_code}"
        assert "html" in resp.headers.get("content-type", "").lower(), \
            "GET /project/slug/valid-tab must return HTML"

    def test_project_slug_invalid_tab_redirects(self, test_client):
        resp = test_client.get("/project/commander/nonexistent-tab", follow_redirects=False)
        assert resp.status_code in (301, 302), \
            f"GET /project/commander/nonexistent-tab must redirect, got {resp.status_code}"

    def test_projects_old_path_redirects(self, test_client):
        resp = test_client.get("/projects/commander/sprint-mgmt", follow_redirects=False)
        assert resp.status_code in (301, 302), \
            f"GET /projects/commander/sprint-mgmt must redirect, got {resp.status_code}"
