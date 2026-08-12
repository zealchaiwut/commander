"""Tests for issue #2250: Delete the launch routes and subprocess spawn paths.

AC1: sprint_run.py and sprint_run_service.py deleted; routes removed from server.py
AC2: Spawn and orphan-sweep code removed from startup.py
AC3: GET /api/board and GET /api/sprint-management/issues still respond 200
AC4: GET /api/sprints/{label}/state* still responds
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
APPS_DASHBOARD = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(APPS_DASHBOARD))
sys.path.insert(0, str(REPO_ROOT))


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

def test_ac3_board_endpoint_exists():
    """GET /api/board route must still be registered in sprint_dispatch router."""
    from routers.sprint_dispatch import router
    paths = [route.path for route in router.routes]
    assert "/api/board" in paths, \
        "GET /api/board is missing from sprint_dispatch router"


def test_ac3_sprint_management_issues_endpoint_exists():
    """GET /api/sprint-management/issues route must still be registered."""
    from routers.sprint_dispatch import router
    paths = [route.path for route in router.routes]
    assert "/api/sprint-management/issues" in paths, \
        "GET /api/sprint-management/issues is missing from sprint_dispatch router"


# ── AC4: State endpoints still exist ─────────────────────────────────────────

def test_ac4_state_endpoints_exist():
    """GET /api/sprints/{sprint_label}/state* must still be registered in sprint_live."""
    from routers.sprint_live import router
    paths = [route.path for route in router.routes]
    state_routes = [p for p in paths if "state" in p and "sprint_label" in p]
    assert state_routes, \
        f"No state routes found in sprint_live router. Routes: {paths}"
    assert "/api/sprints/{sprint_label}/state" in paths, \
        "GET /api/sprints/{sprint_label}/state is missing"


# ── AC4: finish_progress routes still registered (Finish wizard) ──────────────

def test_ac4_finish_progress_router_present():
    """finish_progress router must still be imported in routers/__init__.py."""
    init_text = (APPS_DASHBOARD / "routers" / "__init__.py").read_text(encoding="utf-8")
    assert "finish_progress_router" in init_text, \
        "finish_progress_router was removed — Finish wizard will break"


def test_ac4_finish_bg_endpoint_exists():
    """POST /api/sprints/{sprint_label}/finish-bg must still be registered."""
    from routers.finish_progress import router
    methods_paths = [(list(r.methods or []), r.path) for r in router.routes]
    post_paths = [p for methods, p in methods_paths if "POST" in methods]
    assert any("finish-bg" in p for p in post_paths), \
        f"POST finish-bg endpoint missing. POST paths: {post_paths}"


def test_ac4_finish_stream_endpoint_exists():
    """GET /api/sprints/{sprint_label}/finish-stream must still be registered."""
    from routers.finish_progress import router
    methods_paths = [(list(r.methods or []), r.path) for r in router.routes]
    get_paths = [p for methods, p in methods_paths if "GET" in methods]
    assert any("finish-stream" in p for p in get_paths), \
        f"GET finish-stream endpoint missing. GET paths: {get_paths}"
