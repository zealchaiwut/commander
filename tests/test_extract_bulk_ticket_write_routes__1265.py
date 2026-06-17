"""Tests for issue #1265: Extract bulk-ticket draft/create POST routes to routers/bulk_tickets.py.

AC coverage:
  AC1 - routers/bulk_tickets.py contains all four POST routes
  AC2 - server.py no longer defines any of the four routes directly
  AC3 - server.py includes routers/bulk_tickets.router (bulk_tickets_router)
  AC4 - No route paths or HTTP methods change
  AC5 - Request/response behavior (status codes, payloads, error handling) identical
  AC6 - py_compile passes on both server.py and routers/bulk_tickets.py
  AC7 - No other files import handler functions directly from server.py
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
# Helpers
# ---------------------------------------------------------------------------

FOUR_POST_ROUTES = [
    "/api/tickets/draft",
    "/api/tickets/create",
    "/api/tickets/bulk",
    "post-selected",  # /api/tickets/bulk/{job_id}/post-selected
]


def _make_job(job_id: str, status: str = "drafts_ready") -> dict:
    return {
        "job_id": job_id,
        "status": status,
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
                "prompt": "test",
                "state": "draft_ready",
                "title": "Ticket 0",
                "body": "body",
                "body_preview": "body",
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

    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# AC1: routers/bulk_tickets.py contains all four POST routes
# ---------------------------------------------------------------------------

class TestRoutesPresentInRouter:
    """AC1 — routers/bulk_tickets.py contains all four POST routes."""

    def test_bulk_tickets_module_exists(self):
        """routers/bulk_tickets.py must exist."""
        assert BULK_TICKETS_PY.exists(), "routers/bulk_tickets.py does not exist"

    def test_post_draft_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for /api/tickets/draft."""
        source = BULK_TICKETS_PY.read_text()
        assert '@router.post("/api/tickets/draft")' in source, (
            "POST /api/tickets/draft must be in routers/bulk_tickets.py"
        )

    def test_post_create_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for /api/tickets/create."""
        source = BULK_TICKETS_PY.read_text()
        assert '/api/tickets/create' in source and '@router.post' in source, (
            "POST /api/tickets/create must be in routers/bulk_tickets.py"
        )

    def test_post_bulk_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for /api/tickets/bulk."""
        source = BULK_TICKETS_PY.read_text()
        # Look for the exact route that starts the bulk job (not a sub-path)
        assert '@router.post("/api/tickets/bulk"' in source, (
            "POST /api/tickets/bulk must be in routers/bulk_tickets.py"
        )

    def test_post_post_selected_route_in_router(self):
        """routers/bulk_tickets.py must declare @router.post for post-selected."""
        source = BULK_TICKETS_PY.read_text()
        assert 'post-selected' in source and '@router.post' in source, (
            "POST /api/tickets/bulk/{job_id}/post-selected must be in routers/bulk_tickets.py"
        )

    def test_router_has_four_post_routes(self):
        """The bulk_tickets router must expose exactly four POST routes (was two GET)."""
        from routers.bulk_tickets import router
        from fastapi import APIRouter
        assert isinstance(router, APIRouter)
        post_routes = [r for r in router.routes if "POST" in getattr(r, "methods", set())]
        assert len(post_routes) >= 4, (
            f"Expected >=4 POST routes on bulk_tickets router, found {len(post_routes)}: "
            + str([getattr(r, 'path', '') for r in post_routes])
        )


# ---------------------------------------------------------------------------
# AC2: server.py no longer defines any of the four routes directly
# ---------------------------------------------------------------------------

class TestRoutesRemovedFromServer:
    """AC2 — server.py must not have @app.post for the four extracted routes."""

    def test_no_app_post_draft_in_server(self):
        """server.py must not contain @app.post for /api/tickets/draft."""
        source = SERVER_PY.read_text()
        lines = source.splitlines()
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if "@app.post" in line and "/api/tickets/draft" in line
        ]
        assert not violations, (
            "server.py still defines POST /api/tickets/draft:\n" + "\n".join(violations)
        )

    def test_no_app_post_create_in_server(self):
        """server.py must not contain @app.post for /api/tickets/create."""
        source = SERVER_PY.read_text()
        lines = source.splitlines()
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if "@app.post" in line and '"/api/tickets/create"' in line
        ]
        assert not violations, (
            "server.py still defines POST /api/tickets/create:\n" + "\n".join(violations)
        )

    def test_no_app_post_bulk_start_in_server(self):
        """server.py must not contain @app.post for /api/tickets/bulk (bulk start)."""
        source = SERVER_PY.read_text()
        lines = source.splitlines()
        # Match only the exact /api/tickets/bulk route (not sub-paths with {job_id})
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if "@app.post" in line and '"/api/tickets/bulk"' in line
        ]
        assert not violations, (
            "server.py still defines POST /api/tickets/bulk:\n" + "\n".join(violations)
        )

    def test_no_app_post_post_selected_in_server(self):
        """server.py must not contain @app.post for post-selected."""
        source = SERVER_PY.read_text()
        lines = source.splitlines()
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if "@app.post" in line and "post-selected" in line
        ]
        assert not violations, (
            "server.py still defines POST post-selected route:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# AC3: server.py includes bulk_tickets_router
# ---------------------------------------------------------------------------

class TestRouterIncludedInServer:
    """AC3 — server.py includes bulk_tickets_router."""

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


# ---------------------------------------------------------------------------
# AC4: No route paths or HTTP methods change
# ---------------------------------------------------------------------------

class TestRoutePathsUnchanged:
    """AC4 — route paths remain mounted at the same paths after the move."""

    def test_draft_path_registered_on_app(self, client):
        """POST /api/tickets/draft path is registered and accessible."""
        # Sending invalid (empty) data should return 422 (validation error),
        # not 404 (route not found).
        resp = client.post("/api/tickets/draft", data={})
        assert resp.status_code != 404, (
            "POST /api/tickets/draft returned 404 — route is not registered"
        )

    def test_create_path_registered_on_app(self, client):
        """POST /api/tickets/create path is registered and accessible."""
        resp = client.post("/api/tickets/create", data={})
        assert resp.status_code != 404, (
            "POST /api/tickets/create returned 404 — route is not registered"
        )

    def test_bulk_start_path_registered_on_app(self, client):
        """POST /api/tickets/bulk path is registered and accessible."""
        resp = client.post("/api/tickets/bulk", data={})
        assert resp.status_code != 404, (
            "POST /api/tickets/bulk returned 404 — route is not registered"
        )

    def test_post_selected_path_registered_on_app(self, client):
        """POST /api/tickets/bulk/{job_id}/post-selected path is registered.

        Route-not-found returns {"detail": "Not Found"} (FastAPI default).
        A registered route with an unknown job_id returns {"detail": "Job not found"}.
        """
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/tickets/bulk/{fake_id}/post-selected", json={"tickets": [{"index": 0, "labels": []}]})
        # Distinguish FastAPI "Not Found" (unregistered route) from handler 404 ("Job not found")
        if resp.status_code == 404:
            detail = resp.json().get("detail", "")
            assert detail != "Not Found", (
                "POST /api/tickets/bulk/{job_id}/post-selected returned FastAPI route-not-found "
                "404 — route is not registered on the app"
            )


# ---------------------------------------------------------------------------
# AC5: Request/response behavior identical
# ---------------------------------------------------------------------------

class TestRequestResponseBehavior:
    """AC5 — status codes and error handling are identical after the move."""

    def test_draft_missing_description_returns_400(self, client):
        """POST /api/tickets/draft with empty description must return 400."""
        resp = client.post("/api/tickets/draft", data={"description": ""})
        assert resp.status_code == 400, (
            f"Expected 400 for empty description, got {resp.status_code}"
        )
        body = resp.json()
        assert "Description is required" in body.get("detail", ""), (
            f"Expected 'Description is required' in detail, got: {body}"
        )

    def test_create_missing_title_returns_400(self, client):
        """POST /api/tickets/create with empty title must return 400."""
        resp = client.post("/api/tickets/create", data={"title": ""})
        assert resp.status_code == 400, (
            f"Expected 400 for empty title, got {resp.status_code}"
        )
        body = resp.json()
        assert "Title is required" in body.get("detail", ""), (
            f"Expected 'Title is required' in detail, got: {body}"
        )

    def test_bulk_start_bad_concurrency_returns_422(self, client):
        """POST /api/tickets/bulk with invalid concurrency returns 422."""
        resp = client.post("/api/tickets/bulk", data={
            "repo": "zealchaiwut/commander",
            "prompts": '["test"]',
            "concurrency": "7",  # invalid value
        })
        assert resp.status_code == 422, (
            f"Expected 422 for bad concurrency, got {resp.status_code}"
        )

    def test_bulk_start_bad_prompts_returns_422(self, client):
        """POST /api/tickets/bulk with non-JSON prompts returns 422."""
        resp = client.post("/api/tickets/bulk", data={
            "repo": "zealchaiwut/commander",
            "prompts": "not-json",
        })
        assert resp.status_code == 422, (
            f"Expected 422 for non-JSON prompts, got {resp.status_code}"
        )

    def test_bulk_start_unknown_repo_returns_422(self, client):
        """POST /api/tickets/bulk with unknown repo returns 422."""
        resp = client.post("/api/tickets/bulk", data={
            "repo": "unknown/repo",
            "prompts": '["test"]',
        })
        assert resp.status_code == 422, (
            f"Expected 422 for unknown repo, got {resp.status_code}"
        )

    def test_bulk_start_empty_prompts_returns_422(self, client):
        """POST /api/tickets/bulk with empty prompts array returns 422."""
        resp = client.post("/api/tickets/bulk", data={
            "repo": "zealchaiwut/commander",
            "prompts": "[]",
        })
        assert resp.status_code == 422, (
            f"Expected 422 for empty prompts, got {resp.status_code}"
        )

    def test_post_selected_unknown_job_returns_404(self, client):
        """POST /api/tickets/bulk/{job_id}/post-selected with unknown job returns 404."""
        fake_id = str(uuid.uuid4())
        resp = client.post(
            f"/api/tickets/bulk/{fake_id}/post-selected",
            json={"tickets": [{"index": 0, "labels": []}]},
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown job, got {resp.status_code}"
        )

    def test_post_selected_no_tickets_returns_422(self, client):
        """POST /api/tickets/bulk/{job_id}/post-selected with empty tickets returns 422."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id)
        server._bulk_jobs[job_id] = job
        try:
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/post-selected",
                json={"tickets": []},
            )
            assert resp.status_code == 422, (
                f"Expected 422 for empty tickets list, got {resp.status_code}"
            )
        finally:
            server._bulk_jobs.pop(job_id, None)

    def test_bulk_start_valid_request_returns_202(self, client, monkeypatch):
        """POST /api/tickets/bulk with valid payload returns 202 with job_id."""
        import server
        # Stub out _run_bulk_job to avoid actually running BA
        async def _noop_run(job_id: str):
            pass

        monkeypatch.setattr(server, "_run_bulk_job", _noop_run)

        resp = client.post("/api/tickets/bulk", data={
            "repo": "zealchaiwut/commander",
            "prompts": '["test ticket"]',
            "concurrency": "3",
        })
        assert resp.status_code == 202, (
            f"Expected 202 for valid bulk start, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "job_id" in body, f"Response must include job_id, got: {body}"
        # Clean up
        server._bulk_jobs.pop(body.get("job_id", ""), None)


# ---------------------------------------------------------------------------
# AC6: py_compile passes on both files
# ---------------------------------------------------------------------------

class TestSyntaxClean:
    """AC6 — py_compile passes on server.py and routers/bulk_tickets.py."""

    def test_server_py_compiles(self):
        """server.py has no syntax errors."""
        py_compile.compile(str(SERVER_PY), doraise=True)

    def test_bulk_tickets_py_compiles(self):
        """routers/bulk_tickets.py has no syntax errors."""
        py_compile.compile(str(BULK_TICKETS_PY), doraise=True)


# ---------------------------------------------------------------------------
# AC7: No other files import handler functions directly from server.py
# ---------------------------------------------------------------------------

class TestNoDirectHandlerImports:
    """AC7 — no file imports the moved handler functions from server.py."""

    MOVED_HANDLERS = [
        "create_ticket_draft",
        "create_ticket_from_draft",
        "bulk_create_start",
        "bulk_post_selected",
    ]

    def test_no_external_import_of_moved_handlers(self):
        """No .py file outside server.py imports the moved handlers from server."""
        import re
        for py_file in REPO_ROOT.rglob("*.py"):
            if py_file == SERVER_PY:
                continue
            try:
                text = py_file.read_text(errors="ignore")
            except OSError:
                continue
            for name in self.MOVED_HANDLERS:
                pattern = rf"from\s+server\s+import[^#\n]*\b{name}\b"
                match = re.search(pattern, text)
                assert not match, (
                    f"{py_file.relative_to(REPO_ROOT)} imports '{name}' from server — "
                    "update the import to routers.bulk_tickets"
                )
