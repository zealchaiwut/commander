"""Tests for issue #1266: Extract bulk-ticket per-job action routes to routers/bulk_tickets.py.

AC coverage:
  AC1 - All per-job action routes (skip, retry, redraft, estimate-draft, retry-with-body,
        retry-with-image, retry-all, size-remedy-comment, size-remedy-images) are defined
        in routers/bulk_tickets.py and removed from server.py
  AC2 - server.py registers bulk_tickets_router; no new bulk-ticket route definitions in server.py
  AC3 - All moved routes preserve their HTTP method, path, and response contract
  AC4 - py_compile passes on both server.py and routers/bulk_tickets.py
  AC5 - Existing automated tests for per-job actions pass without modification
  AC6 - No routes are duplicated across server.py and routers/bulk_tickets.py
"""
from __future__ import annotations

import py_compile
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
BULK_TICKETS_PY = ROUTERS_DIR / "bulk_tickets.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PER_JOB_ACTION_PATHS = [
    "/api/tickets/bulk/{job_id}/skip",
    "/api/tickets/bulk/{job_id}/retry",
    "/api/tickets/bulk/{job_id}/redraft",
    "/api/tickets/bulk/{job_id}/estimate-draft",
    "/api/tickets/bulk/{job_id}/retry-with-body",
    "/api/tickets/bulk/{job_id}/retry-with-image",
    "/api/tickets/bulk/{job_id}/retry-all",
    "/api/tickets/bulk/{job_id}/size-remedy-comment",
    "/api/tickets/bulk/{job_id}/size-remedy-images",
]

# The path segment that uniquely identifies each route in source code
PER_JOB_ACTION_SEGMENTS = [
    "skip",
    "retry",
    "redraft",
    "estimate-draft",
    "retry-with-body",
    "retry-with-image",
    "retry-all",
    "size-remedy-comment",
    "size-remedy-images",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(job_id: str, ticket_state: str = "draft_ready") -> dict:
    return {
        "job_id": job_id,
        "status": "drafts_ready",
        "repo": "zealchaiwut/commander",
        "sprint_label": "",
        "default_labels": ["enhancement"],
        "concurrency": 3,
        "has_attachments": False,
        "image_url_map": {},
        "attachment_filenames": [],
        "image_assignments": [],
        "attachment_error": None,
        "stop_requested": False,
        "allowed_labels": ["enhancement"],
        "created_at": "2024-01-01T00:00:00+00:00",
        "tickets": [
            {
                "index": 0,
                "prompt": "test ticket prompt",
                "state": ticket_state,
                "title": "Test Ticket",
                "body": "This is the ticket body.",
                "body_preview": "This is the ticket body.",
                "issue_num": None,
                "issue_url": None,
                "label_pills": None,
                "suggested_labels": None,
                "error": None,
                "attachment_warning": None,
                "started_at": None,
                "finished_at": None,
                "retry_count": 0,
                "last_error": None,
            }
        ],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))
    monkeypatch.setenv("COMMANDER_DISABLE_NEON", "1")

    import server
    import projects as projects_module
    import github_client as gc

    fake_project = {"repo": "zealchaiwut/commander", "name": "Commander"}
    monkeypatch.setattr(projects_module, "load_projects", lambda: [fake_project])
    monkeypatch.setattr("server.projects_module.load_projects", lambda: [fake_project])
    monkeypatch.setattr(
        gc, "list_labels",
        lambda repo_name=None: [{"name": "enhancement", "color": "84b6eb"}],
    )

    yield TestClient(server.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# AC1: All per-job action routes defined in routers/bulk_tickets.py
# ---------------------------------------------------------------------------

class TestRoutesInBulkTicketsRouter:
    """AC1 — all nine per-job action routes are declared in routers/bulk_tickets.py."""

    def test_bulk_tickets_module_exists(self):
        """routers/bulk_tickets.py must exist."""
        assert BULK_TICKETS_PY.exists(), "routers/bulk_tickets.py does not exist"

    def test_skip_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the skip route."""
        source = BULK_TICKETS_PY.read_text()
        assert '@router.post("/api/tickets/bulk/{job_id}/skip")' in source, (
            "skip route must be declared in routers/bulk_tickets.py"
        )

    def test_retry_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the retry route."""
        source = BULK_TICKETS_PY.read_text()
        assert '@router.post("/api/tickets/bulk/{job_id}/retry")' in source, (
            "retry route must be declared in routers/bulk_tickets.py"
        )

    def test_redraft_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the redraft route."""
        source = BULK_TICKETS_PY.read_text()
        assert '@router.post("/api/tickets/bulk/{job_id}/redraft")' in source, (
            "redraft route must be declared in routers/bulk_tickets.py"
        )

    def test_estimate_draft_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the estimate-draft route."""
        source = BULK_TICKETS_PY.read_text()
        assert "estimate-draft" in source and "@router.post" in source, (
            "estimate-draft route must be declared in routers/bulk_tickets.py"
        )

    def test_retry_with_body_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the retry-with-body route."""
        source = BULK_TICKETS_PY.read_text()
        assert "retry-with-body" in source and "@router.post" in source, (
            "retry-with-body route must be declared in routers/bulk_tickets.py"
        )

    def test_retry_with_image_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the retry-with-image route."""
        source = BULK_TICKETS_PY.read_text()
        assert "retry-with-image" in source and "@router.post" in source, (
            "retry-with-image route must be declared in routers/bulk_tickets.py"
        )

    def test_retry_all_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the retry-all route."""
        source = BULK_TICKETS_PY.read_text()
        assert "retry-all" in source and "@router.post" in source, (
            "retry-all route must be declared in routers/bulk_tickets.py"
        )

    def test_size_remedy_comment_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the size-remedy-comment route."""
        source = BULK_TICKETS_PY.read_text()
        assert "size-remedy-comment" in source and "@router.post" in source, (
            "size-remedy-comment route must be declared in routers/bulk_tickets.py"
        )

    def test_size_remedy_images_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for the size-remedy-images route."""
        source = BULK_TICKETS_PY.read_text()
        assert "size-remedy-images" in source and "@router.post" in source, (
            "size-remedy-images route must be declared in routers/bulk_tickets.py"
        )

    def test_router_has_all_nine_per_job_post_routes(self):
        """The bulk_tickets router must expose at least nine POST routes on {job_id}/* paths."""
        from routers.bulk_tickets import router
        from fastapi import APIRouter
        assert isinstance(router, APIRouter)
        job_post_routes = [
            r for r in router.routes
            if "POST" in getattr(r, "methods", set())
            and "{job_id}/" in getattr(r, "path", "")
        ]
        # estimate-draft, skip, retry, redraft, post-selected,
        # retry-with-body, retry-with-image, retry-all, size-remedy-comment, size-remedy-images
        assert len(job_post_routes) >= 9, (
            f"Expected >=9 per-job POST routes on bulk_tickets router, found {len(job_post_routes)}: "
            + str([getattr(r, "path", "") for r in job_post_routes])
        )


# ---------------------------------------------------------------------------
# AC1 (removal): per-job action routes removed from server.py
# ---------------------------------------------------------------------------

class TestRoutesRemovedFromServer:
    """AC1 — server.py must not define any per-job action routes via @app.post."""

    @pytest.mark.parametrize("segment", PER_JOB_ACTION_SEGMENTS)
    def test_no_app_post_per_job_route_in_server(self, segment):
        """server.py must not contain @app.post for any per-job action path."""
        source = SERVER_PY.read_text()
        lines = source.splitlines()
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if "@app.post" in line and segment in line
        ]
        assert not violations, (
            f"server.py still defines @app.post route for '{segment}':\n"
            + "\n".join(violations)
        )

    def test_no_app_post_bulk_job_id_any_action_in_server(self):
        """server.py must not have any @app.post decorator for /api/tickets/bulk/{job_id}/* paths."""
        source = SERVER_PY.read_text()
        lines = source.splitlines()
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if "@app.post" in line and "job_id" in line and "bulk" in line
        ]
        assert not violations, (
            "server.py defines @app.post routes for /api/tickets/bulk/{job_id}/* — must be in router:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# AC2: server.py registers bulk_tickets_router; no new route definitions
# ---------------------------------------------------------------------------

class TestRouterRegisteredInServer:
    """AC2 — server.py registers bulk_tickets_router and has no new route defs."""

    def test_bulk_tickets_router_imported_in_server(self):
        """server.py must import bulk_tickets_router from routers."""
        source = SERVER_PY.read_text()
        assert "bulk_tickets_router" in source, (
            "server.py must import bulk_tickets_router from routers"
        )

    def test_app_include_router_bulk_tickets_in_server(self):
        """server.py must call app.include_router(bulk_tickets_router)."""
        source = SERVER_PY.read_text()
        assert "app.include_router(bulk_tickets_router)" in source, (
            "server.py must call app.include_router(bulk_tickets_router)"
        )

    def test_server_py_no_new_bulk_ticket_route_definitions(self):
        """server.py must not define any new @app.post/@app.get for /api/tickets/* paths."""
        source = SERVER_PY.read_text()
        lines = source.splitlines()
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if ("@app.post" in line or "@app.get" in line) and "/api/tickets/" in line
        ]
        assert not violations, (
            "server.py defines route decorators for /api/tickets/* — move to router:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# AC3: Routes preserve HTTP method, path, and response contract
# ---------------------------------------------------------------------------

class TestRoutePathsAndResponseContract:
    """AC3 — all per-job action routes respond correctly (not 404 when registered)."""

    def test_skip_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/skip with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/skip",
            json={"index": 0},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_retry_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/retry with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/retry",
            json={"index": 0},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_redraft_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/redraft with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/redraft",
            json={"index": 0},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_estimate_draft_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/estimate-draft with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/estimate-draft",
            json={"index": 0},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_retry_with_body_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/retry-with-body with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/retry-with-body",
            json={"index": 0, "body": "updated body text"},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_retry_with_image_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/retry-with-image with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/retry-with-image",
            data={"index": "0", "body_text": ""},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_retry_all_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/retry-all with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/retry-all",
            json={"bodies": {}},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_size_remedy_comment_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/size-remedy-comment with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/size-remedy-comment",
            json={"index": 0},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_size_remedy_images_returns_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/size-remedy-images with unknown job returns 404."""
        resp = client.post(
            f"/api/tickets/bulk/{uuid.uuid4()}/size-remedy-images",
            json={"index": 0},
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Job not found"

    def test_skip_returns_ok_for_known_job(self, client):
        """POST /api/tickets/bulk/{job_id}/skip with a known job returns 200 with ok key."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id, ticket_state="pending")
        server._bulk_jobs[job_id] = job
        try:
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/skip",
                json={"index": 0},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body.get("ok") is True
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_retry_returns_ok_for_known_job(self, client):
        """POST /api/tickets/bulk/{job_id}/retry with a known job returns 200 with ok key."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id, ticket_state="failed")
        server._bulk_jobs[job_id] = job
        try:
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/retry",
                json={"index": 0},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body.get("ok") is True
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_redraft_returns_ok_for_known_job(self, client):
        """POST /api/tickets/bulk/{job_id}/redraft with a known job returns 200 with ok key."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id, ticket_state="draft_ready")
        server._bulk_jobs[job_id] = job
        try:
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/redraft",
                json={"index": 0},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body.get("ok") is True
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_skip_returns_422_for_invalid_index(self, client):
        """POST /api/tickets/bulk/{job_id}/skip with out-of-range index returns 422."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id)
        server._bulk_jobs[job_id] = job
        try:
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/skip",
                json={"index": 999},
            )
            assert resp.status_code == 422
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_retry_returns_422_for_invalid_index(self, client):
        """POST /api/tickets/bulk/{job_id}/retry with out-of-range index returns 422."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id)
        server._bulk_jobs[job_id] = job
        try:
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/retry",
                json={"index": 999},
            )
            assert resp.status_code == 422
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_retry_all_returns_ok_for_known_job_with_empty_bodies(self, client):
        """POST /api/tickets/bulk/{job_id}/retry-all with empty bodies returns 200."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id)
        server._bulk_jobs[job_id] = job
        try:
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/retry-all",
                json={"bodies": {}},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body.get("ok") is True
            assert body.get("retried") == 0
        finally:
            server._bulk_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC4: py_compile passes on both files
# ---------------------------------------------------------------------------

class TestSyntaxClean:
    """AC4 — py_compile passes on server.py and routers/bulk_tickets.py."""

    def test_server_py_compiles(self):
        """server.py has no syntax errors."""
        py_compile.compile(str(SERVER_PY), doraise=True)

    def test_bulk_tickets_py_compiles(self):
        """routers/bulk_tickets.py has no syntax errors."""
        py_compile.compile(str(BULK_TICKETS_PY), doraise=True)


# ---------------------------------------------------------------------------
# AC6: No routes duplicated across server.py and routers/bulk_tickets.py
# ---------------------------------------------------------------------------

class TestNoDuplicateRoutes:
    """AC6 — no route path defined in both server.py and routers/bulk_tickets.py."""

    def test_no_duplicate_per_job_action_routes(self):
        """No per-job action path segment appears in both server.py @app.post and bulk_tickets.py."""
        server_source = SERVER_PY.read_text()
        router_source = BULK_TICKETS_PY.read_text()

        # Find segments referenced by @app.post in server.py
        server_lines = server_source.splitlines()
        server_post_segments = set()
        for line in server_lines:
            if "@app.post" in line:
                for seg in PER_JOB_ACTION_SEGMENTS:
                    if seg in line:
                        server_post_segments.add(seg)

        # Find segments referenced by @router.post in bulk_tickets.py
        router_lines = router_source.splitlines()
        router_post_segments = set()
        for line in router_lines:
            if "@router.post" in line:
                for seg in PER_JOB_ACTION_SEGMENTS:
                    if seg in line:
                        router_post_segments.add(seg)

        duplicates = server_post_segments & router_post_segments
        assert not duplicates, (
            f"Routes duplicated in both server.py and routers/bulk_tickets.py: {duplicates}"
        )

    def test_router_importable_and_has_per_job_routes(self):
        """routers.bulk_tickets must export an APIRouter with per-job POST routes."""
        from routers.bulk_tickets import router
        from fastapi import APIRouter
        assert isinstance(router, APIRouter)

        # All per-job action paths must be registered on the router
        router_paths = {getattr(r, "path", "") for r in router.routes}
        for path in PER_JOB_ACTION_PATHS:
            assert path in router_paths, (
                f"Route {path} not found on bulk_tickets router. "
                f"Registered paths: {sorted(router_paths)}"
            )
