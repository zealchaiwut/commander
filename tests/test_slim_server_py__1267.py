"""Tests for issue #1267: Slim server.py to thin app factory (under 400 lines, no route handlers).

AC coverage:
  AC1 - server.py is under 400 lines (blank lines and comments included)
  AC2 - server.py contains no route handler implementations (@app.get/@app.post etc.)
  AC3 - All code deleted from server.py has been moved into router/module files
        (verified indirectly: all previously-registered routes still respond non-404)
  AC4 - /api route inventory diff is zero: same paths, methods, operation IDs
  AC5 - COMMANDER_GATE_MONOLITH CI gate passes green (server.py under 400 lines)
  AC6 - Full smoke test suite passes with no regressions
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

# Routes that were in server.py before the slim-down and must still be accessible.
# These are tested by checking the app's route table (not by HTTP calls, to keep
# tests fast and network-free).
FORMERLY_SERVER_ROUTES = [
    # sprint nav
    ("GET", "/api/sprint-nav-status"),
    ("GET", "/api/sprint-progress"),
    ("GET", "/api/sprint-nav-summary"),
    # issues
    ("GET", "/api/issues"),
    ("POST", "/api/issues/{issue_id}/approve"),
    ("POST", "/api/issues/{issue_id}/reject"),
    ("POST", "/api/issues/{issue_id}/close"),
    ("GET", "/api/issues/{issue_id}/test-report"),
    ("POST", "/api/issues/{issue_id}/sprint-label"),
    # projects
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("DELETE", "/api/projects/{owner}/{repo_name}"),
    ("GET", "/api/projects/{project}/running-sprint"),
    ("POST", "/api/projects/{owner}/{repo_name}/approve-batch"),
    ("POST", "/api/projects/init"),
    ("GET", "/api/project-details"),
    ("POST", "/api/projects/{owner}/{repo_name}/sprint-branch-merge"),
    # environments
    ("POST", "/api/projects/{slug}/environments/{env}/deploy"),
    ("POST", "/api/projects/{slug}/environments/{env}/restart"),
    ("POST", "/api/projects/{slug}/environments/{env}/stop"),
    ("POST", "/api/projects/{slug}/environments/{env}/start"),
    ("GET", "/api/projects/{slug}/environments/{env}/run-state"),
    ("GET", "/api/projects/{slug}/environments/{env}/deploy-status"),
    ("GET", "/api/projects/{slug}/environments"),
    ("PUT", "/api/projects/{slug}/environments"),
    # settings sync
    ("GET", "/api/settings/sync/status"),
    ("POST", "/api/settings/sync/diff"),
    ("POST", "/api/settings/sync/commit"),
    # maintenance
    ("POST", "/api/maintenance/tests/cleanup"),
    # sprint planning
    ("GET", "/api/sprint-planning/issues"),
    ("POST", "/api/sprint-planning/assign"),
    # sprint labels / open issues
    ("GET", "/api/open-issues"),
    ("POST", "/api/sprints/batch-labels"),
    # sprint run / management
    ("POST", "/api/sprint-run"),
    ("GET", "/api/sprint-management/issues"),
    # estimates
    ("GET", "/api/sprints/{sprint_label}/estimate-summary"),
    ("GET", "/api/sprints/{sprint_label}/estimate"),
    ("GET", "/api/estimates/batch"),
    ("GET", "/api/sprints/{sprint_label}/outcome"),
    ("GET", "/api/sprints/{sprint_label}/estimate-vs-actual"),
    # calibration
    ("GET", "/api/calibration"),
    # metrics
    ("GET", "/api/metrics/sprints"),
    # analytics
    ("GET", "/api/projects/{slug}/analytics/metrics"),
    ("GET", "/api/projects/{slug}/analytics/calibration"),
    # reports
    ("POST", "/api/reports/daily"),
    # finish card
    ("GET", "/api/sprints/{sprint_label}/finish-card"),
    # tickets (draft/create)
    ("POST", "/api/tickets/draft"),
    ("POST", "/api/tickets/create"),
    # bulk tickets
    ("POST", "/api/tickets/bulk"),
    ("POST", "/api/tickets/bulk/{job_id}/estimate-draft"),
    ("POST", "/api/tickets/bulk/{job_id}/skip"),
    ("POST", "/api/tickets/bulk/{job_id}/retry"),
    ("POST", "/api/tickets/bulk/{job_id}/redraft"),
    ("POST", "/api/tickets/bulk/{job_id}/post-selected"),
    ("POST", "/api/tickets/bulk/{job_id}/retry-with-body"),
    ("POST", "/api/tickets/bulk/{job_id}/retry-with-image"),
    ("POST", "/api/tickets/bulk/{job_id}/retry-all"),
    ("POST", "/api/tickets/bulk/{job_id}/size-remedy-comment"),
    ("POST", "/api/tickets/bulk/{job_id}/size-remedy-images"),
    # deploy
    ("POST", "/api/deploy/promote"),
    # mis-sizing
    ("GET", "/api/sprints/{sprint_label}/mis-sizing-flags"),
    ("POST", "/api/sprints/{sprint_label}/mis-sizing-flags/generate"),
    ("POST", "/api/sprints/{sprint_label}/mis-sizing-flags/{issue_id}/action"),
    ("GET", "/api/mis-sizing/history"),
    ("POST", "/api/mis-sizing/rebuild"),
    ("GET", "/api/mis-sizing/config"),
    ("POST", "/api/mis-sizing/config"),
]


# ---------------------------------------------------------------------------
# AC1 + AC5: server.py is under 400 lines
# ---------------------------------------------------------------------------

class TestServerPyLineCount:
    """AC1 / AC5 — server.py must be under 400 lines (the COMMANDER_GATE_MONOLITH threshold)."""

    def test_server_py_under_400_lines(self):
        """server.py must have fewer than 400 lines total (including blanks and comments)."""
        lines = SERVER_PY.read_text(encoding="utf-8").splitlines()
        count = len(lines)
        assert count < 400, (
            f"server.py has {count} lines — must be under 400 to pass COMMANDER_GATE_MONOLITH. "
            f"Move remaining route handlers to routers/."
        )

    def test_server_py_exists(self):
        """server.py must still exist (it is the app entry point)."""
        assert SERVER_PY.exists(), "server.py must exist"


# ---------------------------------------------------------------------------
# AC2: server.py contains no route handler implementations
# ---------------------------------------------------------------------------

class TestNoRouteHandlersInServer:
    """AC2 — server.py must not define any @app.get/@app.post etc. route handlers."""

    ROUTE_DECORATOR_PATTERN = re.compile(
        r"@app\.(get|post|put|delete|patch|websocket)\s*\(",
        re.IGNORECASE,
    )

    def test_no_app_get_decorators(self):
        """server.py must contain no @app.get(...) route decorators."""
        source = SERVER_PY.read_text(encoding="utf-8")
        matches = [
            f"  Line {i}: {line.strip()}"
            for i, line in enumerate(source.splitlines(), 1)
            if re.search(r"@app\.get\s*\(", line)
        ]
        assert not matches, (
            "server.py still contains @app.get route decorators:\n" + "\n".join(matches)
        )

    def test_no_app_post_decorators(self):
        """server.py must contain no @app.post(...) route decorators."""
        source = SERVER_PY.read_text(encoding="utf-8")
        matches = [
            f"  Line {i}: {line.strip()}"
            for i, line in enumerate(source.splitlines(), 1)
            if re.search(r"@app\.post\s*\(", line)
        ]
        assert not matches, (
            "server.py still contains @app.post route decorators:\n" + "\n".join(matches)
        )

    def test_no_app_put_delete_patch_decorators(self):
        """server.py must contain no @app.put/@app.delete/@app.patch route decorators."""
        source = SERVER_PY.read_text(encoding="utf-8")
        matches = [
            f"  Line {i}: {line.strip()}"
            for i, line in enumerate(source.splitlines(), 1)
            if re.search(r"@app\.(put|delete|patch)\s*\(", line)
        ]
        assert not matches, (
            "server.py still contains @app.put/@app.delete/@app.patch route decorators:\n"
            + "\n".join(matches)
        )


# ---------------------------------------------------------------------------
# AC3 + AC4: All previously-registered routes are still reachable
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app_routes():
    """Return the set of (method, path) tuples registered on the FastAPI app."""
    import os
    os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
    os.environ.setdefault("DB_PATH", "/tmp/commander-test-1267.db")
    import server
    route_set = set()
    for route in server.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:
            for m in methods:
                route_set.add((m.upper(), path))
    return route_set


class TestRouteInventoryPreserved:
    """AC3 / AC4 — every route that was previously in server.py must still be in the app."""

    @pytest.mark.parametrize("method,path", FORMERLY_SERVER_ROUTES)
    def test_route_is_registered(self, app_routes, method, path):
        """Route must still be registered on the FastAPI app after the slim-down."""
        assert (method, path) in app_routes, (
            f"{method} {path} is no longer registered on the app — "
            f"make sure the router that owns it is imported and included via app.include_router()"
        )


# ---------------------------------------------------------------------------
# AC2 extra: server.py only has app factory components
# ---------------------------------------------------------------------------

class TestServerPyIsAppFactory:
    """AC2 extra — server.py structure should only contain app factory components."""

    def test_server_py_has_app_object(self):
        """server.py must define (or import) a FastAPI 'app' object."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "app = FastAPI(" in source or "from fastapi import" in source, (
            "server.py must create or import the FastAPI app"
        )

    def test_server_py_has_include_router(self):
        """server.py must call app.include_router(...)."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "app.include_router(" in source, (
            "server.py must include at least one router via app.include_router()"
        )

    def test_server_py_has_lifespan(self):
        """server.py must define or reference the lifespan context."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "lifespan" in source, (
            "server.py must define or reference the FastAPI lifespan context"
        )

    def test_server_py_compiles(self):
        """server.py must have no syntax errors."""
        import py_compile
        py_compile.compile(str(SERVER_PY), doraise=True)


# ---------------------------------------------------------------------------
# AC6: Key routers are registered and functional
# ---------------------------------------------------------------------------

class TestRoutersExist:
    """AC6 — new router files created for this task must exist and be importable."""

    EXPECTED_ROUTERS = [
        "sprint_nav",
        "issues",
        "projects",
        "environments",
        "settings_sync",
        "maintenance",
        "sprint_planning",
        "sprint_labels",
        "sprint_dispatch",
        "estimates",
        "calibration",
        "metrics",
        "reports",
        "finish_card",
        "deploy",
        "mis_sizing",
    ]

    @pytest.mark.parametrize("router_name", EXPECTED_ROUTERS)
    def test_router_module_exists(self, router_name):
        """Router module must exist as a .py file under routers/."""
        router_file = ROUTERS_DIR / f"{router_name}.py"
        assert router_file.exists(), (
            f"routers/{router_name}.py does not exist — "
            f"create it and move the corresponding routes from server.py"
        )

    @pytest.mark.parametrize("router_name", EXPECTED_ROUTERS)
    def test_router_module_imported_in_init(self, router_name):
        """Router module must be imported in routers/__init__.py."""
        init_py = ROUTERS_DIR / "__init__.py"
        source = init_py.read_text(encoding="utf-8")
        assert f"from .{router_name} import router as {router_name}_router" in source or \
               f"{router_name}_router" in source, (
            f"routers/__init__.py must export {router_name}_router"
        )

    @pytest.mark.parametrize("router_name", EXPECTED_ROUTERS)
    def test_router_included_in_server(self, router_name):
        """server.py must include each new router via app.include_router(...)."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert f"{router_name}_router" in source, (
            f"server.py must import and include {router_name}_router"
        )
