"""Tests for issue #2234: delete advisor.py orphan and six zero-caller endpoints.

AC1 — services/sprint_manager/advisor.py deleted
AC2 — Six endpoints removed:
        sprint_preflight: cycle-check, conflicts, dep-order
        sprint_finish: conflict-status (nested + flat)
        sprint_dispatch: POST /api/sprint-run
        deploy: POST /api/deploy/promote (whole file deleted)
AC3 — routers/deploy.py deleted and include_router removed from server.py
AC4 — GET /api/board and GET /api/sprint-management/issues still respond 200
AC5 — GET /api/sprints/{label}/preflight still returns cycle, conflict and dep-order data
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
os.environ.setdefault("COMMANDER_DISABLE_AUTO_RECONCILE", "1")


# ── AC1: advisor.py deleted ───────────────────────────────────────────────────

class TestAdvisorDeleted:
    """services/sprint_manager/advisor.py must not exist (AC1)."""

    def test_advisor_py_does_not_exist(self):
        advisor = REPO_ROOT / "services" / "sprint_manager" / "advisor.py"
        assert not advisor.exists(), (
            "advisor.py is an orphan with zero references; it must be deleted"
        )


# ── AC2: Six dead endpoints removed ──────────────────────────────────────────

def _route_paths(router) -> set[str]:
    """Extract path strings from a FastAPI router's route list."""
    paths = set()
    for route in router.routes:
        if hasattr(route, "path"):
            paths.add(route.path)
        if hasattr(route, "methods"):
            for method in (route.methods or []):
                paths.add(f"{method}:{route.path}")
    return paths


class TestDeadEndpointsRemoved:
    """All six zero-caller endpoints must be removed (AC2)."""

    def test_cycle_check_removed(self):
        from apps.dashboard.routers.sprint_preflight import router
        paths = _route_paths(router)
        assert not any("cycle-check" in p for p in paths), (
            "GET cycle-check has zero frontend callers and must be removed"
        )

    def test_conflicts_removed(self):
        from apps.dashboard.routers.sprint_preflight import router
        paths = _route_paths(router)
        assert not any(
            p.endswith("/conflicts") or "/conflicts" in p
            for p in paths
            if "preflight" not in p
        ), "GET .../conflicts standalone endpoint must be removed"

    def test_dep_order_removed(self):
        from apps.dashboard.routers.sprint_preflight import router
        paths = _route_paths(router)
        assert not any("dep-order" in p for p in paths), (
            "GET dep-order has zero frontend callers and must be removed"
        )

    def test_conflict_status_removed_nested(self):
        from apps.dashboard.routers.sprint_finish import router
        paths = _route_paths(router)
        assert not any(
            "conflict-status" in p and "projects" in p for p in paths
        ), "Nested GET .../conflict-status must be removed"

    def test_conflict_status_removed_flat(self):
        from apps.dashboard.routers.sprint_finish import router
        paths = _route_paths(router)
        assert not any(
            "conflict-status" in p and "projects" not in p for p in paths
        ), "Flat GET /api/sprints/{label}/conflict-status must be removed"

    def test_post_sprint_run_removed(self):
        from apps.dashboard.routers.sprint_dispatch import router
        routes_with_methods = set()
        for route in router.routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    routes_with_methods.add(f"{method}:{route.path}")
        assert "POST:/api/sprint-run" not in routes_with_methods, (
            "POST /api/sprint-run is superseded by POST /api/sprints/run and must be removed"
        )


# ── AC3: deploy.py deleted and include_router removed ────────────────────────

class TestDeployRouterDeleted:
    """routers/deploy.py must be deleted and its router unmounted (AC3)."""

    def test_deploy_py_does_not_exist(self):
        deploy_py = ROUTERS_DIR / "deploy.py"
        assert not deploy_py.exists(), (
            "deploy.py (POST /api/deploy/promote) has zero callers; file must be deleted"
        )

    def test_deploy_promote_route_not_in_app(self):
        """The /api/deploy/promote route must not be mounted on the FastAPI app."""
        import server as srv
        app_paths = {
            getattr(route, "path", "") for route in srv.app.routes
        }
        assert "/api/deploy/promote" not in app_paths, (
            "app.include_router(deploy_router) must be removed from server.py"
        )


# ── AC4: Surviving board and sprint-management routes still registered ────────

class TestSurvivingRoutesExist:
    """GET /api/board and GET /api/sprint-management/issues must still respond 200 (AC4)."""

    def test_board_route_still_registered(self):
        from apps.dashboard.routers.sprint_dispatch import router
        routes_with_methods = set()
        for route in router.routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    routes_with_methods.add(f"{method}:{route.path}")
        assert "GET:/api/board" in routes_with_methods, (
            "GET /api/board must remain registered — it lives in sprint_dispatch.py"
        )

    def test_sprint_management_issues_route_still_registered(self):
        from apps.dashboard.routers.sprint_dispatch import router
        routes_with_methods = set()
        for route in router.routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    routes_with_methods.add(f"{method}:{route.path}")
        assert "GET:/api/sprint-management/issues" in routes_with_methods, (
            "GET /api/sprint-management/issues must remain registered"
        )


# ── AC5: Aggregate preflight still returns cycle/conflict/dep-order data ──────

class TestPreflightStillReturnsAggregateData:
    """GET /api/sprints/{label}/preflight must still return dag, warnings, and cycle fields (AC5)."""

    def test_preflight_route_still_registered(self):
        from apps.dashboard.routers.sprint_preflight import router
        routes_with_methods = set()
        for route in router.routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    routes_with_methods.add(f"{method}:{route.path}")
        assert "GET:/api/sprints/{sprint_label}/preflight" in routes_with_methods, (
            "GET /api/sprints/{label}/preflight must remain — it returns cycle/conflict/dep-order inline"
        )

    def test_preflight_response_contains_dag_warnings_cycle(self):
        """Preflight handler must return a dict with dag, warnings, and cycle keys."""
        from apps.dashboard.routers.sprint_preflight import get_sprint_preflight

        mock_srv = MagicMock()
        mock_srv._SPRINT_LABEL_RE.match.return_value = True
        mock_srv._get_sprint_issues.return_value = []
        mock_srv._MIS_SIZING_AVAILABLE = False
        mock_srv._project_root_path.return_value = Path("/tmp/fake-project")
        mock_srv._commander_dir.return_value = Path("/tmp/fake-project/.commander")
        mock_srv._effective_agent_models.return_value = {}
        mock_srv._PF_NON_WORK = {"sprint-summary", "docs"}

        with patch("apps.dashboard.routers.sprint_preflight._server", return_value=mock_srv):
            result = get_sprint_preflight(sprint_label="sprint-1", project="owner/repo")

        assert "dag" in result, "preflight response must include 'dag' (inline DAG/cycle data)"
        assert "warnings" in result, "preflight response must include 'warnings' (conflict/stale data)"
        assert "cycle" in result, "preflight response must include 'cycle' (cycle path or None)"
        assert result["ok"] is True

    def test_preflight_preflight_fix_route_still_registered(self):
        from apps.dashboard.routers.sprint_preflight import router
        routes_with_methods = set()
        for route in router.routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    routes_with_methods.add(f"{method}:{route.path}")
        assert "POST:/api/sprints/{sprint_label}/preflight-fix" in routes_with_methods, (
            "POST preflight-fix must remain — it is still used"
        )
