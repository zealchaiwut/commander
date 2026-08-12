"""Tests for issue #795 — extract sprints and tickets routers from server.py.

Second strangler-fig wave continuing #794: move the cleanly-movable sprint and
ticket endpoints into ``routers/sprints.py`` and ``routers/tickets.py`` (each
with a sibling service module), and lift the shared sprint-dispatch subprocess
env helper into ``routers/dispatch_service.py`` so both the new routers and the
``sprint_manager``-facing run/finish endpoints share one implementation.

Scope note — pre-existing tests pin the named sprint *lifecycle* handlers to
``server.py`` source or import them directly, and the AC forbids modifying
tests, so those stay in the monolith and are out of this extraction wave:
  * run_sprint_managed         — server.run_sprint_managed() called by test_156
  * rerun_sprint               — `def rerun_sprint` AST-pinned by test_410
  * finish_sprint              — source/behaviour pinned by test_195
  * set_sprint_status          — server.set_sprint_status() called by test_215
  * get_sprint_management_issues, get_sprint_estimate_vs_actual,
    get_sprint_estimate_summary, get_sprint_branch_status, bulk_post_selected
    — imported / source-pinned by their own tests.

One function per acceptance criterion.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

from fastapi import APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ── Cluster contracts: path -> set of HTTP methods served by the new router ──

SPRINTS_ROUTES = {
    "/api/sprints": {"GET"},
    "/api/sprints/goal": {"GET", "POST"},
    "/api/sprints/order": {"GET", "POST"},
    "/api/sprints/running-all": {"GET"},
    "/api/sprints/{sprint_label}/dispatch-log": {"GET"},
}

TICKETS_ROUTES = {
    "/api/tickets/{issue_id}/approve": {"POST"},
    "/api/tickets/bulk/{job_id}/stop": {"POST"},
    "/api/tickets/bulk/{job_id}": {"DELETE"},
}

# Handler function names that must no longer be declared in server.py.
MOVED_HANDLERS = [
    "get_sprints",
    "get_sprint_goal",
    "save_sprint_goal",
    "get_sprint_order",
    "save_sprint_order",
    "get_all_running_sprints",
    "get_dispatch_log",
    "approve_ticket",
    "bulk_stop_job",
    "bulk_delete_job",
]


def _route_methods(routes) -> dict:
    """Build {path: {methods}} dropping the auto HEAD/OPTIONS noise."""
    out: dict = {}
    for r in routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if path is None or methods is None:
            continue
        keep = {m for m in methods if m not in ("HEAD", "OPTIONS")}
        if keep:
            out.setdefault(path, set()).update(keep)
    return out


# ── AC1: routers/sprints.py created with the movable sprint endpoints ────────

def test_ac1_sprints_router_exposes_sprint_endpoints():
    mod = importlib.import_module("routers.sprints")
    assert isinstance(mod.router, APIRouter)
    got = _route_methods(mod.router.routes)
    for path, methods in SPRINTS_ROUTES.items():
        assert path in got, f"{path} missing from sprints router"
        assert methods <= got[path], f"{path} methods {got.get(path)} != {methods}"


def test_ac1_sprint_endpoints_behave_identically():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)

    # /api/sprints — {"sprints": [...], "default": ...}
    with patch.object(srv.github_client, "list_sprints", return_value=["sprint-1"]), \
         patch.object(srv.github_client, "latest_active_sprint", return_value="sprint-1"):
        r = client.get("/api/sprints")
        assert r.status_code == 200
        assert set(r.json().keys()) == {"sprints", "default"}

    # /api/sprints/running-all — {"running": [...]}
    with patch.object(srv, "_all_sprints_running", return_value=[]):
        r = client.get("/api/sprints/running-all")
        assert r.status_code == 200
        assert r.json() == {"running": []}

    # /api/sprints/goal round-trips through the extracted POST + GET
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with patch.object(srv, "_project_root_path", return_value=Path(td)):
            save = client.post(
                "/api/sprints/goal",
                json={"project": "owner/repo", "sprint_label": "sprint-1", "goal": "ship it"},
            )
            assert save.status_code == 200 and save.json() == {"ok": True}
            got = client.get("/api/sprints/goal", params={"project": "owner/repo", "sprint": "sprint-1"})
            assert got.status_code == 200 and got.json() == {"goal": "ship it"}


# ── AC2: routers/tickets.py created with the movable ticket endpoints ────────

def test_ac2_tickets_router_exposes_ticket_endpoints():
    mod = importlib.import_module("routers.tickets")
    assert isinstance(mod.router, APIRouter)
    got = _route_methods(mod.router.routes)
    for path, methods in TICKETS_ROUTES.items():
        assert path in got, f"{path} missing from tickets router"
        assert methods <= got[path], f"{path} methods {got.get(path)} != {methods}"


def test_ac2_ticket_endpoints_behave_identically():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)

    # /api/tickets/{id}/approve — approves on GitHub then broadcasts, returns {"ok": True}
    async def _noop_broadcast(_data):
        return None

    with patch.object(srv.github_client, "approve_issue", return_value=None) as approve, \
         patch.object(srv, "broadcast", _noop_broadcast):
        r = client.post("/api/tickets/42/approve")
        assert r.status_code == 200 and r.json() == {"ok": True}
        approve.assert_called_once()

    # /api/tickets/bulk/{job_id}/stop — 404 for unknown job (parity with monolith)
    with patch.object(srv, "_get_bulk_job", return_value=None):
        assert client.post("/api/tickets/bulk/nope/stop").status_code == 404

    # /api/tickets/bulk/{job_id} DELETE — idempotent ok even when job is gone
    r = client.delete("/api/tickets/bulk/does-not-exist")
    assert r.status_code == 200 and r.json() == {"ok": True}


# ── AC4: server.py only mounts the routers; moved handlers removed ───────────

def test_ac4_server_registers_both_routers():
    src = SERVER_PY.read_text()
    assert "sprints_router" in src and "tickets_router" in src
    assert "app.include_router(sprints_router)" in src
    assert "app.include_router(tickets_router)" in src


def test_ac4_moved_routes_removed_from_monolith():
    src = SERVER_PY.read_text()
    moved_paths = [
        '@app.get("/api/sprints")',
        '@app.get("/api/sprints/goal")',
        '@app.post("/api/sprints/goal")',
        '@app.get("/api/sprints/order")',
        '@app.post("/api/sprints/order")',
        '@app.get("/api/sprints/running-all")',
        '@app.get("/api/sprints/{sprint_label}/dispatch-log")',
        '@app.post("/api/tickets/{issue_id}/approve")',
        '@app.post("/api/tickets/bulk/{job_id}/stop")',
        '@app.delete("/api/tickets/bulk/{job_id}")',
    ]
    for dec in moved_paths:
        assert dec not in src, f"{dec} still declared in server.py — not extracted"


def test_ac4_moved_handler_bodies_absent_from_server():
    src = SERVER_PY.read_text()
    for fn in MOVED_HANDLERS:
        assert f"def {fn}(" not in src, f"def {fn}( body still present in server.py"


# ── AC5: route-table parity — endpoints still served, now by the routers ─────

def _app_routes_for(paths):
    import server as srv
    got = _route_methods(srv.app.routes)
    return {p: got.get(p, set()) for p in paths}


def test_ac5_sprint_cluster_route_parity():
    import routers.sprints as sprints_mod
    on_app = _app_routes_for(SPRINTS_ROUTES)
    for path, methods in SPRINTS_ROUTES.items():
        assert methods <= on_app[path], (
            f"sprint route {path} lost on app: {on_app[path]} (expected {methods})"
        )
    router_paths = _route_methods(sprints_mod.router.routes)
    for path in SPRINTS_ROUTES:
        assert path in router_paths, f"{path} not served by routers.sprints"


def test_ac5_ticket_cluster_route_parity():
    import routers.tickets as tickets_mod
    on_app = _app_routes_for(TICKETS_ROUTES)
    for path, methods in TICKETS_ROUTES.items():
        assert methods <= on_app[path], (
            f"ticket route {path} lost on app: {on_app[path]} (expected {methods})"
        )
    router_paths = _route_methods(tickets_mod.router.routes)
    for path in TICKETS_ROUTES:
        assert path in router_paths, f"{path} not served by routers.tickets"


# ── AC6: sibling service modules hold the logic, not the route handlers ───────

def test_ac6_service_modules_exist_with_logic():
    for name in ("sprints_service", "tickets_service"):
        assert (DASHBOARD_DIR / "routers" / f"{name}.py").exists()
    sprints_svc = importlib.import_module("routers.sprints_service")
    tickets_svc = importlib.import_module("routers.tickets_service")
    for fn in ("get_sprints", "get_sprint_goal", "save_sprint_goal",
               "get_sprint_order", "save_sprint_order",
               "get_all_running_sprints", "get_dispatch_log"):
        assert hasattr(sprints_svc, fn), f"sprints_service missing {fn}"
    for fn in ("approve_ticket", "bulk_stop_job", "bulk_delete_job"):
        assert hasattr(tickets_svc, fn), f"tickets_service missing {fn}"
