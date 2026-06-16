"""Tests for issue #1264: Extract bulk job read routes to routers/bulk_tickets.py.

AC coverage:
  AC1 - GET /api/tickets/bulk/{job_id} returns identical response shape and status codes
  AC2 - GET /api/tickets/bulk/{job_id}* sub-path variants registered exclusively in routers/bulk_tickets.py
  AC3 - _get_bulk_job loader lives in routers/bulk_tickets.py; no duplicate in server.py
  AC4 - server.py contains zero new route definitions for /api/tickets/bulk/* GET paths
  AC5 - py_compile passes on both server.py and routers/bulk_tickets.py
"""
from __future__ import annotations

import ast
import json
import py_compile
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
BULK_TICKETS_PY = ROUTERS_DIR / "bulk_tickets.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(job_id: str, status: str = "drafts_ready") -> dict:
    return {
        "job_id": job_id,
        "status": status,
        "repo": "zealchaiwut/commander",
        "default_labels": ["enhancement"],
        "concurrency": 3,
        "has_attachments": False,
        "image_url_map": {},
        "created_at": "2024-01-01T00:00:00+00:00",
        "tickets": [
            {
                "index": 0,
                "prompt": "test",
                "state": "draft_ready",
                "title": "Ticket 0",
                "body": "body",
                "body_preview": "body",
                "issue_num": None,
                "issue_url": None,
                "error": None,
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

    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# AC1: GET /api/tickets/bulk/{job_id} response shape and status codes
# ---------------------------------------------------------------------------

class TestGetBulkJobEndpoint:
    """AC1 — GET /api/tickets/bulk/{job_id} returns identical response shape."""

    def test_returns_200_with_correct_shape(self, client):
        """Known job returns 200 with job_id, repo, status, concurrency, default_labels, tickets."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id)
        server._bulk_jobs[job_id] = job

        try:
            resp = client.get(f"/api/tickets/bulk/{job_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["job_id"] == job_id
            assert body["repo"] == job["repo"]
            assert body["status"] == job["status"]
            assert body["concurrency"] == job["concurrency"]
            assert body["default_labels"] == job["default_labels"]
            assert isinstance(body["tickets"], list)
            assert len(body["tickets"]) == 1
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_returns_404_for_unknown_job(self, client):
        """Non-existent job_id returns 404."""
        resp = client.get("/api/tickets/bulk/nonexistent-job-id-xyz")
        assert resp.status_code == 404

    def test_strips_internal_fields_from_tickets(self, client):
        """Tickets with underscore-prefixed internal fields have them stripped."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id)
        job["tickets"][0]["_internal_flag"] = True
        server._bulk_jobs[job_id] = job

        try:
            resp = client.get(f"/api/tickets/bulk/{job_id}")
            assert resp.status_code == 200
            ticket = resp.json()["tickets"][0]
            assert "_internal_flag" not in ticket
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_stream_endpoint_returns_200_for_known_job(self, client):
        """GET /api/tickets/bulk/{job_id}/stream returns 200 for a known job."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id, status="drafts_ready")
        server._bulk_jobs[job_id] = job

        try:
            with client.stream("GET", f"/api/tickets/bulk/{job_id}/stream") as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_stream_endpoint_returns_404_for_unknown_job(self, client):
        """GET /api/tickets/bulk/{job_id}/stream returns 404 for unknown job_id."""
        resp = client.get("/api/tickets/bulk/no-such-job/stream")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC2: GET routes registered exclusively in routers/bulk_tickets.py
# ---------------------------------------------------------------------------

class TestGetRoutesInBulkTicketsRouter:
    """AC2 — GET /api/tickets/bulk/* routes live in routers/bulk_tickets.py."""

    def test_bulk_tickets_module_exists(self):
        """routers/bulk_tickets.py must exist."""
        assert BULK_TICKETS_PY.exists(), "routers/bulk_tickets.py does not exist"

    def test_get_job_route_in_router(self):
        """routers/bulk_tickets.py must contain the GET {job_id} route."""
        source = BULK_TICKETS_PY.read_text()
        assert "/api/tickets/bulk/{job_id}" in source or 'bulk/{job_id}"' in source, (
            "GET /api/tickets/bulk/{job_id} route must be in routers/bulk_tickets.py"
        )

    def test_get_stream_route_in_router(self):
        """routers/bulk_tickets.py must contain the GET {job_id}/stream route."""
        source = BULK_TICKETS_PY.read_text()
        assert "stream" in source, (
            "GET /api/tickets/bulk/{job_id}/stream route must be in routers/bulk_tickets.py"
        )

    def test_router_importable(self):
        """routers.bulk_tickets must export a FastAPI router."""
        from routers.bulk_tickets import router
        from fastapi import APIRouter
        assert isinstance(router, APIRouter)

    def test_get_routes_on_router(self):
        """The router must have at least two GET routes registered."""
        from routers.bulk_tickets import router
        get_routes = [r for r in router.routes if "GET" in getattr(r, "methods", set())]
        assert len(get_routes) >= 2, (
            f"Expected >=2 GET routes on bulk_tickets router, found {len(get_routes)}"
        )


# ---------------------------------------------------------------------------
# AC3: _get_bulk_job lives in routers/bulk_tickets.py, no duplicate in server.py
# ---------------------------------------------------------------------------

class TestGetBulkJobLocation:
    """AC3 — _get_bulk_job is defined in routers/bulk_tickets.py, not in server.py."""

    def test_importable_from_routers(self):
        """_get_bulk_job must be importable from routers.bulk_tickets."""
        from routers.bulk_tickets import _get_bulk_job
        assert callable(_get_bulk_job)

    def test_not_defined_in_server_py_source(self):
        """server.py must not contain a function definition for _get_bulk_job."""
        source = SERVER_PY.read_text()
        tree = ast.parse(source)
        fn_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert "_get_bulk_job" not in fn_names, (
            "_get_bulk_job function definition must not exist in server.py"
        )

    def test_server_module_exposes_get_bulk_job(self):
        """server._get_bulk_job must still resolve (imported from routers)."""
        import server
        assert hasattr(server, "_get_bulk_job"), (
            "server._get_bulk_job must be importable (re-exported from routers.bulk_tickets)"
        )
        assert callable(server._get_bulk_job)


# ---------------------------------------------------------------------------
# AC4: server.py has no GET route decorators for /api/tickets/bulk/* paths
# ---------------------------------------------------------------------------

class TestNoDirectBulkGetRoutesInServerPy:
    """AC4 — server.py contains zero GET route definitions for /api/tickets/bulk/*."""

    def test_no_app_get_bulk_decorator_in_server(self):
        """server.py must not have @app.get decorator for /api/tickets/bulk/ paths."""
        source = SERVER_PY.read_text()
        # Find any @app.get( decorators that reference /api/tickets/bulk/
        lines = source.splitlines()
        violations = []
        for i, line in enumerate(lines, start=1):
            if '@app.get(' in line and '/api/tickets/bulk/' in line:
                violations.append(f"Line {i}: {line.strip()}")
        assert not violations, (
            f"server.py must not define GET routes for /api/tickets/bulk/*:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# AC5: py_compile passes on both files
# ---------------------------------------------------------------------------

class TestSyntaxClean:
    """AC5 — py_compile passes on server.py and routers/bulk_tickets.py."""

    def test_server_py_compiles(self):
        """server.py has no syntax errors."""
        py_compile.compile(str(SERVER_PY), doraise=True)

    def test_bulk_tickets_py_compiles(self):
        """routers/bulk_tickets.py has no syntax errors."""
        py_compile.compile(str(BULK_TICKETS_PY), doraise=True)
