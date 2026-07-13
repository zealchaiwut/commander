"""Tests for issue #1861 — GET /api/agent-guide endpoint.

AC coverage:
  AC1 — GET /api/agent-guide returns {content, version} where content is the
         markdown of docs/agent-guide.md
  AC2 — docs/agent-guide.md covers the 5 recipe areas with exact
         method+path+key params for every step
  AC3 — Endpoint 404s cleanly with a helpful message if the file is missing
  AC4 — Behavioral test: TestClient GET asserts 200 and documented paths in
         the guide exist in app.routes (guards against endpoint drift)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routers.agent_guide_service as agent_guide_service  # noqa: E402
from routers.agent_guide import router as agent_guide_router  # noqa: E402

GUIDE_FILE = REPO_ROOT / "docs" / "agent-guide.md"


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_app(*extra_routers):
    app = FastAPI()
    app.include_router(agent_guide_router)
    for r in extra_routers:
        app.include_router(r)
    return app


def _normalize_path(path: str) -> str:
    """Normalize path-param names so {job_id} and {sprint_label} compare equal."""
    return re.sub(r"\{[^}]+\}", "{param}", path)


def _collect_route_tuples(app) -> set[tuple[str, str]]:
    """Return (METHOD, normalized-path) pairs from every route in the FastAPI app.

    FastAPI 0.139+ stores included routers as _IncludedRouter wrappers; actual
    APIRoute objects live in _IncludedRouter.original_router.routes.
    """
    from fastapi.routing import APIRoute

    result: set[tuple[str, str]] = set()

    def _walk(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                norm = _normalize_path(route.path)
                for method in route.methods or []:
                    result.add((method.upper(), norm))
            # _IncludedRouter (FastAPI 0.115+) wraps an APIRouter
            if hasattr(route, "original_router") and hasattr(
                route.original_router, "routes"
            ):
                _walk(route.original_router.routes)
            # Legacy/nested router with .routes attribute
            elif hasattr(route, "routes"):
                _walk(route.routes)

    _walk(app.routes)
    return result


# ── AC1: endpoint returns {content, version} ──────────────────────────────────

class TestGetAgentGuide:
    """AC1 — GET /api/agent-guide returns {content, version}."""

    def test_returns_200(self):
        client = TestClient(_make_app())
        r = client.get("/api/agent-guide")
        assert r.status_code == 200

    def test_response_has_content_field(self):
        client = TestClient(_make_app())
        data = client.get("/api/agent-guide").json()
        assert "content" in data

    def test_response_has_version_field(self):
        client = TestClient(_make_app())
        data = client.get("/api/agent-guide").json()
        assert "version" in data

    def test_content_is_non_empty_string(self):
        client = TestClient(_make_app())
        data = client.get("/api/agent-guide").json()
        assert isinstance(data["content"], str)
        assert len(data["content"]) > 0

    def test_version_is_non_empty_string(self):
        client = TestClient(_make_app())
        data = client.get("/api/agent-guide").json()
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    def test_content_matches_guide_file_on_disk(self):
        """AC1 — content equals the raw text of docs/agent-guide.md."""
        if not GUIDE_FILE.exists():
            pytest.skip("docs/agent-guide.md not yet created")
        expected = GUIDE_FILE.read_text(encoding="utf-8")
        client = TestClient(_make_app())
        data = client.get("/api/agent-guide").json()
        assert data["content"] == expected

    def test_version_changes_when_content_changes(self, monkeypatch, tmp_path):
        """Version is a content fingerprint — same content yields same version."""
        guide_v1 = tmp_path / "agent-guide-v1.md"
        guide_v1.write_text("# Guide v1\n", encoding="utf-8")
        guide_v2 = tmp_path / "agent-guide-v2.md"
        guide_v2.write_text("# Guide v2\n", encoding="utf-8")

        monkeypatch.setattr(agent_guide_service, "GUIDE_PATH", guide_v1)
        client = TestClient(_make_app())
        v1 = client.get("/api/agent-guide").json()["version"]

        monkeypatch.setattr(agent_guide_service, "GUIDE_PATH", guide_v2)
        v2 = client.get("/api/agent-guide").json()["version"]

        assert v1 != v2

    def test_version_stable_across_reads(self, monkeypatch, tmp_path):
        guide = tmp_path / "agent-guide.md"
        guide.write_text("# Stable\n", encoding="utf-8")
        monkeypatch.setattr(agent_guide_service, "GUIDE_PATH", guide)
        client = TestClient(_make_app())
        v1 = client.get("/api/agent-guide").json()["version"]
        v2 = client.get("/api/agent-guide").json()["version"]
        assert v1 == v2


# ── AC2: guide covers the 5 recipe areas ─────────────────────────────────────

class TestGuideCoversRecipeAreas:
    """AC2 — docs/agent-guide.md covers all 5 recipe areas."""

    @pytest.fixture(autouse=True)
    def _require_guide(self):
        if not GUIDE_FILE.exists():
            pytest.skip("docs/agent-guide.md not yet created")
        self.content = GUIDE_FILE.read_text(encoding="utf-8")

    def test_recipe_1_bulk_create_tickets(self):
        assert "POST /api/tickets/bulk" in self.content, (
            "Recipe 1 must document POST /api/tickets/bulk"
        )
        assert "GET /api/tickets/bulk/{job_id}" in self.content, (
            "Recipe 1 must document GET /api/tickets/bulk/{job_id}"
        )
        assert "/api/tickets/bulk/{job_id}/post-selected" in self.content, (
            "Recipe 1 must document POST /api/tickets/bulk/{job_id}/post-selected"
        )

    def test_recipe_2_create_and_run_sprint(self):
        assert "POST /api/sprints/create" in self.content, (
            "Recipe 2 must document POST /api/sprints/create"
        )
        assert "POST /api/sprints/batch-labels" in self.content, (
            "Recipe 2 must document POST /api/sprints/batch-labels"
        )
        assert "POST /api/sprints/run" in self.content, (
            "Recipe 2 must document POST /api/sprints/run"
        )
        assert "/api/sprints/" in self.content and "/live" in self.content, (
            "Recipe 2 must document the live poll/SSE endpoint"
        )
        assert "GET /api/sprints/history" in self.content, (
            "Recipe 2 must document GET /api/sprints/history"
        )

    def test_recipe_3_estimate_issue(self):
        assert "/api/issues/" in self.content and "/estimate" in self.content, (
            "Recipe 3 must document the POST /api/issues/{id}/estimate endpoint"
        )

    def test_recipe_4_read_docs(self):
        assert "/api/projects/" in self.content and "/docs" in self.content, (
            "Recipe 4 must document the project docs endpoints"
        )

    def test_recipe_5_error_conventions(self):
        assert "409" in self.content, "Recipe 5 must document the 409 sprint-already-running error"
        assert "422" in self.content, "Recipe 5 must document 422 validation errors"


# ── AC3: 404 when file is missing ─────────────────────────────────────────────

class TestMissingGuideFails:
    """AC3 — endpoint returns 404 with a helpful message when file is absent."""

    def test_returns_404(self, monkeypatch, tmp_path):
        missing = tmp_path / "nonexistent-agent-guide.md"
        monkeypatch.setattr(agent_guide_service, "GUIDE_PATH", missing)
        client = TestClient(_make_app())
        r = client.get("/api/agent-guide")
        assert r.status_code == 404

    def test_error_detail_mentions_file(self, monkeypatch, tmp_path):
        missing = tmp_path / "nonexistent-agent-guide.md"
        monkeypatch.setattr(agent_guide_service, "GUIDE_PATH", missing)
        client = TestClient(_make_app())
        r = client.get("/api/agent-guide")
        body = r.json()
        detail = body.get("detail", "")
        assert len(detail) > 0, "404 response must include a detail message"
        assert any(
            kw in detail.lower() for kw in ("agent", "guide", "docs", "missing", "not found")
        ), f"404 detail should mention the guide file; got: {detail!r}"


# ── AC4: documented paths exist in app.routes ─────────────────────────────────

class TestDocumentedPathsDriftGuard:
    """AC4 — Documented API paths exist in the live route table (drift guard)."""

    @pytest.fixture(autouse=True)
    def _require_guide(self):
        if not GUIDE_FILE.exists():
            pytest.skip("docs/agent-guide.md not yet created")
        self.content = GUIDE_FILE.read_text(encoding="utf-8")

    def _build_app_with_all_guide_routers(self):
        """Build an app with all routers that the guide documents."""
        from routers.bulk_tickets import router as bulk_tickets_router
        from routers.sprint_crud import router as sprint_crud_router
        from routers.sprint_labels import router as sprint_labels_router
        from routers.sprint_run import router as sprint_run_router
        from routers.sprint_live import router as sprint_live_router
        from routers.sprint_history import router as sprint_history_router
        from routers.system_misc import router as system_misc_router
        from routers.docs import router as docs_router

        return _make_app(
            bulk_tickets_router,
            sprint_crud_router,
            sprint_labels_router,
            sprint_run_router,
            sprint_live_router,
            sprint_history_router,
            system_misc_router,
            docs_router,
        )

    def test_get_agent_guide_returns_200(self):
        """The endpoint itself returns 200 with the guide (AC4 prerequisite)."""
        client = TestClient(_make_app())
        r = client.get("/api/agent-guide")
        assert r.status_code == 200

    def test_documented_paths_exist_in_routes(self):
        """Every METHOD /api/path documented in the guide is a live route."""
        app = self._build_app_with_all_guide_routers()
        client = TestClient(app)

        # Read guide via the endpoint
        r = client.get("/api/agent-guide")
        assert r.status_code == 200
        content = r.json()["content"]

        # Extract documented METHOD /api/path patterns
        documented = re.findall(
            r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/api/[^\s\)`\n\"\']+)",
            content,
        )
        assert documented, "Guide must document at least one API path"

        # Collect all route paths from the app (walks _IncludedRouter wrappers)
        route_tuples = _collect_route_tuples(app)

        # Check each documented path/method pair exists
        missing: list[str] = []
        for method, raw_path in documented:
            path_clean = raw_path.split("?")[0].rstrip("/")
            norm_doc = _normalize_path(path_clean)
            if (method, norm_doc) not in route_tuples:
                missing.append(f"{method} {raw_path}")

        assert not missing, (
            "Documented paths not found in app.routes (endpoint drift detected):\n"
            + "\n".join(f"  {p}" for p in missing)
        )
