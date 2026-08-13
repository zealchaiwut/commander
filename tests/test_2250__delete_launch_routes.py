"""Tests for issue #2250: Delete the launch routes and subprocess spawn paths.

AC1: sprint_run.py and sprint_run_service.py deleted; routes removed from server.py
AC2: Spawn and orphan-sweep code removed from startup.py
AC3: GET /api/board and GET /api/sprint-management/issues still respond 200
AC4: GET /api/sprints/{label}/state* still responds
AC5: The Finish wizard and the Deploy tab work end to end
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
APPS_DASHBOARD = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(APPS_DASHBOARD))
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="module")
def client():
    """In-process TestClient (issue #1746).

    The endpoints under test are asserted against the app object directly rather
    than a live server, so the test passes in any environment. An earlier version
    issued live httpx requests to UAT_BASE_URL and failed wherever no dashboard
    was listening.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415
    import server as srv  # noqa: PLC0415

    return TestClient(srv.app)


# ── AC1: Files deleted ────────────────────────────────────────────────────────

def test_ac1_sprint_run_py_deleted():
    """sprint_run.py must not exist."""
    assert not (APPS_DASHBOARD / "routers" / "sprint_run.py").exists(), \
        "sprint_run.py still exists — must be deleted"


def test_ac1_sprint_run_service_py_deleted():
    """sprint_run_service.py must not exist."""
    assert not (APPS_DASHBOARD / "routers" / "sprint_run_service.py").exists(), \
        "sprint_run_service.py still exists — must be deleted"


def test_ac1_sprint_run_router_not_in_server_py():
    """server.py must not import or include sprint_run_router."""
    server_text = (APPS_DASHBOARD / "server.py").read_text(encoding="utf-8")
    assert "sprint_run_router" not in server_text, \
        "server.py still references sprint_run_router"


def test_ac1_sprint_run_router_not_in_routers_init():
    """routers/__init__.py must not import or export sprint_run_router."""
    init_text = (APPS_DASHBOARD / "routers" / "__init__.py").read_text(encoding="utf-8")
    assert "sprint_run_router" not in init_text, \
        "routers/__init__.py still references sprint_run_router"
    assert "sprint_run" not in init_text, \
        "routers/__init__.py still imports from sprint_run"


# ── AC2: Spawn and orphan-sweep code removed from startup.py ─────────────────

def test_ac2_sprint_manager_path_removed():
    """SPRINT_MANAGER_PATH constant must be removed from startup.py."""
    startup_text = (APPS_DASHBOARD / "startup.py").read_text(encoding="utf-8")
    assert "SPRINT_MANAGER_PATH" not in startup_text, \
        "startup.py still defines SPRINT_MANAGER_PATH"


def test_ac2_sprint_manager_argv_removed():
    """_sprint_manager_argv function must be removed from startup.py."""
    startup_text = (APPS_DASHBOARD / "startup.py").read_text(encoding="utf-8")
    assert "_sprint_manager_argv" not in startup_text, \
        "startup.py still defines _sprint_manager_argv"


def test_ac2_sweep_orphan_pid_files_removed():
    """_sweep_orphan_pid_files function must be removed from startup.py."""
    startup_text = (APPS_DASHBOARD / "startup.py").read_text(encoding="utf-8")
    assert "_sweep_orphan_pid_files" not in startup_text, \
        "startup.py still defines _sweep_orphan_pid_files"


# ── AC3: Board and sprint-management endpoints still work ─────────────────────

def test_ac3_board_endpoint_responds_200(client):
    """GET /api/board must respond with HTTP 200."""
    r = client.get("/api/board", params={"project": "commander"})
    assert r.status_code == 200, \
        f"GET /api/board returned {r.status_code}, expected 200. Response: {r.text[:200]}"


def test_ac3_sprint_management_issues_endpoint_responds_200(client):
    """GET /api/sprint-management/issues must respond with HTTP 200."""
    r = client.get("/api/sprint-management/issues", params={"repo": "zealchaiwut/commander"})
    assert r.status_code == 200, \
        f"GET /api/sprint-management/issues returned {r.status_code}, expected 200. Response: {r.text[:200]}"


# ── AC4 & AC5: State endpoints and Finish wizard still work ────────────────────

def test_ac4_state_endpoint_responds_200(client):
    """GET /api/sprints/{label}/state must respond with HTTP 200 (use test sprint)."""
    r = client.get("/api/sprints/sprint-1023/state", params={"project": "commander"})
    assert r.status_code == 200, \
        f"GET /api/sprints/sprint-1023/state returned {r.status_code}, expected 200. Response: {r.text[:200]}"


def test_ac5_finish_bg_endpoint_exists():
    """POST /api/sprints/{label}/finish-bg endpoint must still exist (structural check)."""
    from routers.finish_progress import router
    methods_paths = [(list(r.methods or []), r.path) for r in router.routes]
    post_paths = [p for methods, p in methods_paths if "POST" in methods]
    assert any("finish-bg" in p for p in post_paths), \
        f"POST finish-bg endpoint missing — Finish wizard broken. POST paths: {post_paths}"


def test_ac5_finish_stream_endpoint_exists():
    """GET /api/sprints/{label}/finish-stream endpoint must still exist (structural check)."""
    from routers.finish_progress import router
    methods_paths = [(list(r.methods or []), r.path) for r in router.routes]
    get_paths = [p for methods, p in methods_paths if "GET" in methods]
    assert any("finish-stream" in p for p in get_paths), \
        f"GET finish-stream endpoint missing — Finish wizard broken. GET paths: {get_paths}"
