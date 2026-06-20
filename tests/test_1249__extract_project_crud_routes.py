"""Tests for issue #1249 — extract project CRUD routes from server.py to routers/projects.py.

One function per acceptance criterion.
"""
from __future__ import annotations

import importlib
import os
import py_compile
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"

sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

from fastapi import APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PROJECT_ROUTES = {
    "/api/projects": {"GET", "POST"},
    "/api/projects/{owner}/{repo_name}": {"DELETE"},
}


def _route_methods(routes) -> dict[str, set[str]]:
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


# ── AC1: routers/projects.py contains the three route handlers ───────────────

def test_ac1_projects_router_file_exists():
    assert (DASHBOARD_DIR / "routers" / "projects.py").exists()


def test_ac1_projects_router_exposes_all_three_routes():
    mod = importlib.import_module("routers.projects")
    assert isinstance(mod.router, APIRouter)
    got = _route_methods(mod.router.routes)

    assert "/api/projects" in got, "GET /api/projects missing from projects router"
    assert "GET" in got["/api/projects"]
    assert "POST" in got["/api/projects"]

    assert "/api/projects/{owner}/{repo_name}" in got, (
        "DELETE /api/projects/{owner}/{repo_name} missing from projects router"
    )
    assert "DELETE" in got["/api/projects/{owner}/{repo_name}"]


# ── AC2: routes delegate to existing projects module — no logic added/removed ─

def test_ac2_get_projects_delegates_to_projects_module():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)

    fake_result = {"projects": [], "total": 0}
    with patch.object(srv.projects_module, "get_all_projects", return_value=fake_result) as mock_get:
        resp = client.get("/api/projects")
    assert resp.status_code == 200
    mock_get.assert_called_once()


def test_ac2_post_projects_delegates_to_projects_module():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)

    fake_proj = {"repo": "owner/new-repo", "icon": "ti-folder", "color": "gray"}
    with patch.object(srv.projects_module, "add_project", return_value=fake_proj) as mock_add:
        resp = client.post("/api/projects", json={"repo_url": "https://github.com/owner/new-repo"})
    assert resp.status_code == 201
    mock_add.assert_called_once()


def test_ac2_delete_projects_delegates_to_projects_module():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)

    fake_projects = [{"repo": "owner/repo"}]
    with patch.object(srv.projects_module, "load_projects", return_value=fake_projects), \
         patch.object(srv.projects_module, "remove_project", return_value=["some/path"]) as mock_rm:
        resp = client.request(
            "DELETE",
            "/api/projects/owner/repo",
            json={"delete_local_folders": False, "delete_github_repo": False},
        )
    assert resp.status_code == 200
    mock_rm.assert_called_once_with("owner/repo")


# ── AC3: server.py no longer defines any of these three routes directly ───────

def test_ac3_get_projects_route_absent_from_server():
    src = SERVER_PY.read_text()
    assert '@app.get("/api/projects")' not in src, (
        "GET /api/projects still declared with @app.get in server.py"
    )


def test_ac3_post_projects_route_absent_from_server():
    src = SERVER_PY.read_text()
    assert '@app.post("/api/projects"' not in src, (
        "POST /api/projects still declared with @app.post in server.py"
    )


def test_ac3_delete_projects_route_absent_from_server():
    src = SERVER_PY.read_text()
    assert '@app.delete("/api/projects/{owner}/{repo_name}")' not in src, (
        "DELETE /api/projects/{owner}/{repo_name} still declared in server.py"
    )


# ── AC4: server.py mounts routers/projects.py router ─────────────────────────

def test_ac4_server_imports_projects_router():
    src = SERVER_PY.read_text()
    assert "projects_router" in src, "projects_router not referenced in server.py"


def test_ac4_server_mounts_projects_router():
    src = SERVER_PY.read_text()
    assert "app.include_router(projects_router)" in src, (
        "app.include_router(projects_router) not found in server.py"
    )


def test_ac4_projects_router_registered_on_app():
    import server as srv
    got = _route_methods(srv.app.routes)
    assert "GET" in got.get("/api/projects", set()), "GET /api/projects not on app"
    assert "POST" in got.get("/api/projects", set()), "POST /api/projects not on app"
    assert "DELETE" in got.get("/api/projects/{owner}/{repo_name}", set()), (
        "DELETE /api/projects/{owner}/{repo_name} not on app"
    )


# ── AC5: py_compile passes on both files ─────────────────────────────────────

def test_ac5_py_compile_server():
    py_compile.compile(str(SERVER_PY), doraise=True)


def test_ac5_py_compile_projects_router():
    projects_router_py = DASHBOARD_DIR / "routers" / "projects.py"
    py_compile.compile(str(projects_router_py), doraise=True)


# ── AC6: same request/response contracts, status codes, error handling ────────

def test_ac6_get_projects_returns_200():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)
    fake_result = {"projects": [], "total": 0}
    with patch.object(srv.projects_module, "get_all_projects", return_value=fake_result):
        resp = client.get("/api/projects")
    assert resp.status_code == 200


def test_ac6_post_projects_returns_201():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)
    fake_proj = {"repo": "owner/my-repo", "icon": "ti-folder", "color": "gray"}
    with patch.object(srv.projects_module, "add_project", return_value=fake_proj):
        resp = client.post("/api/projects", json={"repo_url": "https://github.com/owner/my-repo"})
    assert resp.status_code == 201


def test_ac6_post_projects_409_on_duplicate():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)
    with patch.object(srv.projects_module, "add_project", side_effect=FileExistsError("already exists")):
        resp = client.post("/api/projects", json={"repo_url": "https://github.com/owner/dupe"})
    assert resp.status_code == 409


def test_ac6_delete_projects_404_on_unknown_repo():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)
    with patch.object(srv.projects_module, "load_projects", return_value=[]):
        resp = client.request(
            "DELETE",
            "/api/projects/nobody/norepo",
            json={"delete_local_folders": False, "delete_github_repo": False},
        )
    assert resp.status_code == 404


def test_ac6_delete_projects_returns_ok_and_removed():
    import server as srv
    client = TestClient(srv.app, raise_server_exceptions=False)
    fake_projects = [{"repo": "owner/repo"}]
    with patch.object(srv.projects_module, "load_projects", return_value=fake_projects), \
         patch.object(srv.projects_module, "remove_project", return_value=["path/removed"]):
        resp = client.request(
            "DELETE",
            "/api/projects/owner/repo",
            json={"delete_local_folders": False, "delete_github_repo": False},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["removed"], list)
