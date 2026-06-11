"""Tests for issue #794 — extract system/health and activity/logs routers.

First strangler-fig extraction wave: move the movable system endpoints
(``/diagnostics``, ``/api/version``, ``/api/gh-auth-status``) into
``routers/system.py`` and the movable activity endpoints (``/api/agents``,
``/api/events``, ``/api/projects/{slug}/events``) into ``routers/activity.py``,
each with a sibling service module.

Scope note — pre-existing tests pin four endpoints to server.py source and the
AC forbids modifying tests, so those stay in the monolith and are out of this
extraction wave:
  * /api/health         — AST-pinned by test_418 (_slog route.entry on server.py)
  * /api/environment     — string-pinned by test_prd_uat_split__8
  * /api/logs/runs       — string-pinned by test_419
  * /api/logs/sync-github — string-pinned by test_630

One function per acceptance criterion.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import os
os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

from fastapi import APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ── Cluster contracts: path -> set of HTTP methods (the "after" route table) ──

SYSTEM_ROUTES = {
    "/diagnostics": {"GET"},
    "/api/version": {"GET"},
    "/api/gh-auth-status": {"GET"},
}

ACTIVITY_ROUTES = {
    "/api/agents": {"GET"},
    "/api/events": {"GET"},
    "/api/projects/{slug}/events": {"GET"},
}


def _route_methods(routes) -> dict[str, set[str]]:
    """Build {path: {methods}} dropping the auto HEAD/OPTIONS noise."""
    out: dict[str, set[str]] = {}
    for r in routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if path is None or methods is None:
            continue
        keep = {m for m in methods if m not in ("HEAD", "OPTIONS")}
        if keep:
            out.setdefault(path, set()).update(keep)
    return out


# ── AC1: routers/system.py created with the system endpoints ─────────────────

def test_ac1_system_router_exposes_system_endpoints():
    mod = importlib.import_module("routers.system")
    assert isinstance(mod.router, APIRouter)
    got = _route_methods(mod.router.routes)
    for path, methods in SYSTEM_ROUTES.items():
        assert path in got, f"{path} missing from system router"
        assert methods <= got[path], f"{path} methods {got[path]} != {methods}"


def test_ac1_system_endpoints_behave_identically():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)

    # /api/version — build metadata shape + no-cache header
    v = client.get("/api/version")
    assert v.status_code == 200
    body = v.json()
    assert set(body.keys()) == {"git_sha", "branch", "build_timestamp"}
    assert "no-cache" in v.headers.get("cache-control", "")

    # /api/gh-auth-status — startup preflight dict + no-cache header
    g = client.get("/api/gh-auth-status")
    assert g.status_code == 200
    assert isinstance(g.json(), dict)
    assert "no-cache" in g.headers.get("cache-control", "")

    # /diagnostics — serves the diagnostics HTML page
    d = client.get("/diagnostics")
    assert d.status_code == 200
    assert "text/html" in d.headers.get("content-type", "")


# ── AC2: routers/activity.py created with the activity endpoints ─────────────

def test_ac2_activity_router_exposes_activity_endpoints():
    mod = importlib.import_module("routers.activity")
    assert isinstance(mod.router, APIRouter)
    got = _route_methods(mod.router.routes)
    for path, methods in ACTIVITY_ROUTES.items():
        assert path in got, f"{path} missing from activity router"
        assert methods <= got[path], f"{path} methods {got[path]} != {methods}"


def test_ac2_activity_endpoints_behave_identically():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)

    # /api/agents and /api/events return JSON lists
    assert isinstance(client.get("/api/agents").json(), list)
    assert isinstance(client.get("/api/events").json(), list)

    # /api/projects/{slug}/events — 404 unknown slug, 400 bad source, 200 list
    fake = [{"repo": "owner/known"}]
    with patch.object(srv.projects_module, "load_projects", return_value=fake):
        assert client.get("/api/projects/nope/events").status_code == 404
        assert client.get("/api/projects/known/events?source=bogus").status_code == 400
        ok = client.get("/api/projects/known/events")
        assert ok.status_code == 200
        assert isinstance(ok.json(), list)


# ── AC3: sibling service modules created alongside each router ────────────────

def test_ac3_service_modules_exist_alongside_routers():
    assert (DASHBOARD_DIR / "routers" / "system.py").exists()
    assert (DASHBOARD_DIR / "routers" / "system_service.py").exists()
    assert (DASHBOARD_DIR / "routers" / "activity.py").exists()
    assert (DASHBOARD_DIR / "routers" / "activity_service.py").exists()

    sys_svc = importlib.import_module("routers.system_service")
    act_svc = importlib.import_module("routers.activity_service")
    assert hasattr(sys_svc, "get_version")
    assert hasattr(sys_svc, "get_gh_auth_status")
    assert hasattr(act_svc, "list_agents")
    assert hasattr(act_svc, "list_events")
    assert hasattr(act_svc, "get_project_events")


# ── AC4: server.py registers both routers and removes the moved code ─────────

def test_ac4_server_registers_both_routers():
    src = SERVER_PY.read_text()
    assert "system_router" in src and "activity_router" in src
    assert "app.include_router(system_router)" in src
    assert "app.include_router(activity_router)" in src


def test_ac4_moved_routes_removed_from_monolith():
    src = SERVER_PY.read_text()
    for path in list(SYSTEM_ROUTES) + list(ACTIVITY_ROUTES):
        assert f'@app.get("{path}")' not in src, (
            f"{path} still declared with @app.get in server.py — not extracted"
        )


# ── AC5: server.py net line count strictly lower (moved code removed) ────────
# The monolith gate compares HEAD vs base line count; the observable proxy here
# is that the moved handler bodies no longer live in server.py.

def test_ac5_moved_handlers_absent_from_server():
    src = SERVER_PY.read_text()
    for fn in ("def get_version(", "def get_gh_auth_status(",
               "def list_agents(", "def list_events("):
        assert fn not in src, f"{fn} body still present in server.py"


# ── AC7: one regression test per cluster — route-table parity ────────────────
# "path+method set identical before and after extraction": every cluster route
# is still registered on the app, with the same methods, and is now served by
# the extracted router module (not by an inline server.py handler).

def _app_routes_for(paths):
    import server as srv
    got = _route_methods(srv.app.routes)
    return {p: got.get(p, set()) for p in paths}


def test_ac7_system_cluster_route_parity():
    import routers.system as system_mod
    on_app = _app_routes_for(SYSTEM_ROUTES)
    for path, methods in SYSTEM_ROUTES.items():
        assert methods <= on_app[path], (
            f"system route {path} lost on app: {on_app[path]} (expected {methods})"
        )
    # served by the extracted module, not an inline server handler
    router_paths = _route_methods(system_mod.router.routes)
    for path in SYSTEM_ROUTES:
        assert path in router_paths, f"{path} not served by routers.system"


def test_ac7_activity_cluster_route_parity():
    import routers.activity as activity_mod
    on_app = _app_routes_for(ACTIVITY_ROUTES)
    for path, methods in ACTIVITY_ROUTES.items():
        assert methods <= on_app[path], (
            f"activity route {path} lost on app: {on_app[path]} (expected {methods})"
        )
    router_paths = _route_methods(activity_mod.router.routes)
    for path in ACTIVITY_ROUTES:
        assert path in router_paths, f"{path} not served by routers.activity"
