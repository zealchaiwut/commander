"""Tests for issue #2258 — Delete the unused changelog API.

AC coverage:
  AC1 — routers/changelog.py and routers/changelog_service.py deleted;
         route removed from server.py; GET /api/projects/{slug}/changelog → 404
  AC2 — docs/changelog/ files retained (Brain corpus unaffected)
  AC3 — No frontend or lookout breakage (no static/ callers, endpoint 404)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── AC1: modules deleted ─────────────────────────────────────────────────────

class TestModulesDeleted:
    """AC1 — Both module files must not exist after deletion."""

    def test_changelog_router_module_deleted(self):
        changelog_py = DASHBOARD_DIR / "routers" / "changelog.py"
        assert not changelog_py.exists(), (
            f"routers/changelog.py still exists at {changelog_py} — delete it"
        )

    def test_changelog_service_module_deleted(self):
        service_py = DASHBOARD_DIR / "routers" / "changelog_service.py"
        assert not service_py.exists(), (
            f"routers/changelog_service.py still exists at {service_py} — delete it"
        )

    def test_changelog_router_not_in_routers_init(self):
        init_py = (DASHBOARD_DIR / "routers" / "__init__.py").read_text(encoding="utf-8")
        assert "changelog_router" not in init_py, (
            "changelog_router import still present in routers/__init__.py"
        )

    def test_changelog_not_imported_in_server(self):
        server_py = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
        assert "changelog_router" not in server_py, (
            "changelog_router reference still present in server.py"
        )


# ── AC1: endpoint returns 404 ────────────────────────────────────────────────

class TestEndpointGone:
    """AC1 — GET /api/projects/{slug}/changelog must return 404."""

    @pytest.fixture(scope="class")
    def client(self):
        """Build a minimal FastAPI app from only the changelog router package
        to assert the route is absent — avoids pulling in all of server.py."""
        import importlib
        import fastapi

        app = fastapi.FastAPI()
        # Import routers package; the changelog router must not be registered
        import routers as _routers
        for name in _routers.__all__:
            router = getattr(_routers, name)
            app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    def test_changelog_endpoint_not_200(self, client):
        # Route deleted → never 200. Path may match a DELETE projects route
        # (405) or nothing (404); both confirm the GET handler is gone.
        r = client.get("/api/projects/testproject/changelog")
        assert r.status_code in (404, 405), (
            f"Expected 404 or 405 (GET route deleted), got {r.status_code}"
        )

    def test_changelog_endpoint_with_env_param_not_200(self, client):
        r = client.get("/api/projects/testproject/changelog?env=uat")
        assert r.status_code in (404, 405), (
            f"Expected 404 or 405 (GET route deleted), got {r.status_code}"
        )


# ── AC2: docs/changelog/ retained ───────────────────────────────────────────

class TestChangelogFilesRetained:
    """AC2 — docs/changelog/ directory and its .md files must still exist."""

    def test_changelog_dir_exists(self):
        changelog_dir = REPO_ROOT / "docs" / "changelog"
        assert changelog_dir.is_dir(), (
            f"docs/changelog/ directory missing at {changelog_dir}"
        )

    def test_changelog_dir_has_markdown_files(self):
        changelog_dir = REPO_ROOT / "docs" / "changelog"
        md_files = list(changelog_dir.rglob("*.md"))
        assert len(md_files) > 0, (
            "docs/changelog/ has no .md files — Brain corpus was accidentally deleted"
        )

    def test_uat_subdir_exists(self):
        uat_dir = REPO_ROOT / "docs" / "changelog" / "uat"
        assert uat_dir.is_dir(), (
            "docs/changelog/uat/ directory missing"
        )

    def test_prd_subdir_exists(self):
        prd_dir = REPO_ROOT / "docs" / "changelog" / "prd"
        assert prd_dir.is_dir(), (
            "docs/changelog/prd/ directory missing"
        )


# ── AC3: no static/ callers ──────────────────────────────────────────────────

class TestNoFrontendCallers:
    """AC3 — No frontend code calls the changelog endpoint."""

    def test_no_static_caller_of_changelog_endpoint(self):
        static_dir = DASHBOARD_DIR / "static"
        hits = []
        for f in static_dir.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".js", ".mjs", ".ts"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                if "/changelog" in text and "api/projects" in text:
                    hits.append(str(f.relative_to(REPO_ROOT)))
        assert not hits, (
            f"Found static files calling the changelog endpoint (must be zero): {hits}"
        )
