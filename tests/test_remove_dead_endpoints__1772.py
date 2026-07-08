"""Tests for issue #1772 — Remove three dead API endpoints and orphaned helpers.

AC1 - DELETE /api/alerts/{idx} is removed and returns 404
AC2 - POST /api/projects/{owner}/{repo_name}/backlog/cleanup is removed and returns 404
AC3 - POST /api/reports/daily is removed and returns 404
AC4 - Private helpers exclusively owned by removed handlers are deleted
AC5 - reports.py is deleted and its include_router line is removed from server.py
AC6 - test_slim_server_py__1267.py and test_sprint_run_router__1262.py no longer
      reference the removed routes
AC9 - No remaining references to the three removed routes exist in any source file
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
os.environ.setdefault("DB_PATH", "/tmp/commander-test-1772.db")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import server as srv
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# AC1 — DELETE /api/alerts/{idx} is gone (returns 404)
# ---------------------------------------------------------------------------

class TestDeleteAlertRouteRemoved:
    """AC1 — DELETE /api/alerts/{idx} must return 404 (route deleted)."""

    def test_delete_alert_returns_404(self, client):
        r = client.delete("/api/alerts/0")
        assert r.status_code == 404, (
            f"DELETE /api/alerts/{{idx}} should return 404 after removal, "
            f"got {r.status_code}"
        )

    def test_get_alerts_still_returns_200(self, client):
        """GET /api/alerts is unaffected — must still work."""
        r = client.get("/api/alerts")
        assert r.status_code == 200

    def test_post_alerts_still_returns_201(self, client):
        """POST /api/alerts is unaffected — must still work."""
        r = client.post("/api/alerts", json={"title": "test-1772", "body": "body"})
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# AC2 — POST /api/projects/{owner}/{repo_name}/backlog/cleanup is gone (returns 404)
# ---------------------------------------------------------------------------

class TestBacklogCleanupRouteRemoved:
    """AC2 — POST .../backlog/cleanup must return 404 (route deleted)."""

    def test_backlog_cleanup_returns_404(self, client):
        r = client.post(
            "/api/projects/owner/repo/backlog/cleanup",
            json={"confirmed": True, "issue_numbers": []},
        )
        assert r.status_code == 404, (
            f"POST /api/projects/.../backlog/cleanup should return 404 after removal, "
            f"got {r.status_code}"
        )

    def test_backlog_cleanup_preview_still_registered(self, client):
        """cleanup-preview is a related but separate active route — must not be removed."""
        r = client.post("/api/projects/owner/repo/backlog/cleanup-preview")
        assert r.status_code != 404, (
            "cleanup-preview was unintentionally removed"
        )

    def test_backlog_triage_still_registered(self, client):
        """triage is an active route — must not be removed."""
        r = client.post("/api/projects/owner/repo/backlog/triage")
        assert r.status_code != 404, (
            "backlog/triage was unintentionally removed"
        )

    def test_backlog_triage_apply_still_registered(self, client):
        """triage-apply is an active route — must not be removed."""
        r = client.post(
            "/api/projects/owner/repo/backlog/triage-apply",
            json={"confirmed": True, "issue_numbers": []},
        )
        assert r.status_code != 404, (
            "backlog/triage-apply was unintentionally removed"
        )


# ---------------------------------------------------------------------------
# AC3 — POST /api/reports/daily is gone (returns 404)
# ---------------------------------------------------------------------------

class TestReportsDailyRouteRemoved:
    """AC3 — POST /api/reports/daily must return 404 (route deleted)."""

    def test_reports_daily_returns_404(self, client):
        r = client.post("/api/reports/daily", json={})
        assert r.status_code == 404, (
            f"POST /api/reports/daily should return 404 after removal, "
            f"got {r.status_code}"
        )


# ---------------------------------------------------------------------------
# AC4 — No orphaned private helpers
# ---------------------------------------------------------------------------

class TestNoOrphanedHelpers:
    """AC4 — handlers removed; no private helpers exclusive to them remain."""

    def test_dismiss_alert_handler_removed_from_system_misc(self):
        import routers.system_misc as sm
        assert not hasattr(sm, "dismiss_alert"), (
            "dismiss_alert function should be deleted from system_misc.py"
        )

    def test_backlog_cleanup_handler_removed_from_tickets(self):
        import routers.tickets as t
        assert not hasattr(t, "backlog_cleanup"), (
            "backlog_cleanup function should be deleted from tickets.py"
        )

    def test_backlog_cleanup_service_import_retained_in_tickets(self):
        """backlog_cleanup_service must remain — still used by cleanup-preview and triage-apply."""
        import routers.tickets as t
        assert hasattr(t, "backlog_cleanup_service"), (
            "backlog_cleanup_service was incorrectly removed from tickets.py; "
            "it is still needed by backlog_cleanup_preview and backlog_triage_apply"
        )


# ---------------------------------------------------------------------------
# AC5 — reports.py deleted; include_router removed from server.py and __init__.py
# ---------------------------------------------------------------------------

class TestReportsModuleDeleted:
    """AC5 — reports.py deleted; all registration lines removed."""

    def test_reports_py_does_not_exist(self):
        reports_py = ROUTERS_DIR / "reports.py"
        assert not reports_py.exists(), (
            "routers/reports.py must be deleted "
            "(it became empty after POST /api/reports/daily was removed)"
        )

    def test_reports_router_absent_from_server_py(self):
        source = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
        assert "reports_router" not in source, (
            "server.py still imports/mounts reports_router — remove that import and "
            "app.include_router(reports_router) call"
        )

    def test_reports_router_absent_from_init_py(self):
        source = (ROUTERS_DIR / "__init__.py").read_text(encoding="utf-8")
        assert "reports_router" not in source, (
            "routers/__init__.py still exports reports_router — remove the import and "
            "__all__ entry"
        )


# ---------------------------------------------------------------------------
# AC6 — test_slim_server_py__1267.py and test_sprint_run_router__1262.py clean
# ---------------------------------------------------------------------------

class TestFilesNoLongerReferenceRemovedRoutes:
    """AC6 — the two named test files must not reference the three removed routes."""

    def test_1267_no_longer_references_reports_daily(self):
        source = (REPO_ROOT / "tests" / "test_slim_server_py__1267.py").read_text(
            encoding="utf-8"
        )
        assert '"/api/reports/daily"' not in source, (
            "test_slim_server_py__1267.py still has /api/reports/daily in "
            "FORMERLY_SERVER_ROUTES — remove that tuple"
        )

    def test_1262_no_longer_references_reports_daily(self):
        source = (REPO_ROOT / "tests" / "test_sprint_run_router__1262.py").read_text(
            encoding="utf-8"
        )
        assert "/api/reports/daily" not in source, (
            "test_sprint_run_router__1262.py still references /api/reports/daily"
        )


# ---------------------------------------------------------------------------
# AC9 — grep-clean: no remaining source references to the three removed routes
# ---------------------------------------------------------------------------

class TestGrepClean:
    """AC9 — source files must not reference the removed route paths."""

    SOURCE_DIRS = [
        DASHBOARD_DIR / "routers",
        DASHBOARD_DIR,
        REPO_ROOT / "scripts",
        REPO_ROOT / "services",
        REPO_ROOT / "hooks",
    ]

    def _grep(self, pattern: str) -> list[str]:
        hits: list[str] = []
        for base in self.SOURCE_DIRS:
            if not base.exists():
                continue
            for path in base.rglob("*.py"):
                if "test_" in path.name:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if pattern in text:
                    hits.append(str(path.relative_to(REPO_ROOT)))
        return hits

    def test_dismiss_alert_not_in_any_source(self):
        hits = self._grep('"/api/alerts/{idx}"')
        assert not hits, (
            f"DELETE /api/alerts/{{idx}} path still referenced in: {hits}"
        )

    def test_backlog_cleanup_route_not_in_any_source(self):
        hits = self._grep("/backlog/cleanup")
        # cleanup-preview is allowed — filter it out
        hits = [h for h in hits if "cleanup-preview" not in open(  # noqa: SIM115
            REPO_ROOT / h
        ).read()]
        # Actually filter by checking direct string presence
        real_hits: list[str] = []
        for rel in hits:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            # backlog/cleanup-preview is allowed; bare backlog/cleanup route is not
            import re
            if re.search(r'["\'].*backlog/cleanup[^-]', text) or \
               re.search(r'backlog/cleanup"', text):
                real_hits.append(rel)
        assert not real_hits, (
            f"POST /backlog/cleanup route path still referenced in: {real_hits}"
        )

    def test_reports_daily_not_in_any_source(self):
        hits = self._grep("/api/reports/daily")
        assert not hits, (
            f"/api/reports/daily still referenced in: {hits}"
        )
