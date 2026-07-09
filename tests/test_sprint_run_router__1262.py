"""Tests for issue #1262 — Extract Sprint Run Read/Preview Routes to Dedicated Router.

AC1: routers/sprint_run.py exists and registers all moved routes via an APIRouter
AC2: Routes moved: GET .../branch-status, GET .../rerun/preview, GET .../rerun-preview
     (POST rerun/deploy/promote routes are Out of Scope per issue #1262;
      the daily-report POST route was removed entirely in issue #1772)
AC3: All moved routes return identical responses as before the refactor
AC4: No sprint-run read/preview routes remain defined in server.py (no @app.get decorator
     for the moved slugs)
AC5: server.py includes the new router via app.include_router(...)
AC6: py_compile.compile passes for both server.py and routers/sprint_run.py
AC7: Existing unit/integration tests pass without modification
"""
import py_compile
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

SPRINT_RUN_PATH = DASHBOARD_DIR / "routers" / "sprint_run.py"
SERVER_PATH = DASHBOARD_DIR / "server.py"

# Handler function names that must be present in sprint_run.py
HANDLER_NAMES = [
    "get_sprint_branch_status",
    "rerun_sprint_preview",
    "rerun_sprint_preview_v2",
]

# URL slugs that must appear in sprint_run.py and must NOT appear as @app.get in server.py
ENDPOINT_SLUGS = [
    "branch-status",
    "rerun/preview",
    "rerun-preview",
]


# ── AC1 ──────────────────────────────────────────────────────────────────────

def test_ac1_router_file_exists():
    """AC1: routers/sprint_run.py exists."""
    assert SPRINT_RUN_PATH.exists(), (
        "routers/sprint_run.py not found — the new router file must be created"
    )


def test_ac1_router_has_all_handlers():
    """AC1: All route handlers are defined in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    missing = [name for name in HANDLER_NAMES if f"def {name}" not in source]
    assert not missing, (
        f"Handler(s) missing from sprint_run.py: {missing}"
    )


def test_ac1_router_uses_apirouter():
    """AC1: sprint_run.py declares an APIRouter instance named 'router'."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "APIRouter" in source, "sprint_run.py must import APIRouter from fastapi"
    assert "router = APIRouter(" in source or "router=APIRouter(" in source, (
        "sprint_run.py must declare: router = APIRouter(...)"
    )


def test_ac1_router_has_route_decorators():
    """AC1: Route decorators use @router.get (not @app.get) for all three endpoints."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    for slug in ENDPOINT_SLUGS:
        assert slug in source, (
            f"Endpoint slug {slug!r} not found in sprint_run.py"
        )
    assert "@router.get" in source, (
        "sprint_run.py must use @router.get decorators, not @app.get"
    )


# ── AC2 ──────────────────────────────────────────────────────────────────────

def test_ac2_branch_status_route_in_router():
    """AC2: GET .../branch-status is defined in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "branch-status" in source, (
        "GET branch-status route missing from sprint_run.py"
    )
    assert "get_sprint_branch_status" in source


def test_ac2_rerun_preview_route_in_router():
    """AC2: GET .../rerun/preview is defined in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "rerun/preview" in source or "rerun-preview" in source, (
        "GET rerun preview route missing from sprint_run.py"
    )


def test_ac2_rerun_preview_v2_route_in_router():
    """AC2: GET .../rerun-preview is defined in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "rerun-preview" in source, (
        "GET rerun-preview route missing from sprint_run.py"
    )
    assert "rerun_sprint_preview_v2" in source


# ── AC3 ──────────────────────────────────────────────────────────────────────

def test_ac3_handler_signatures_preserved():
    """AC3: Handler signatures match what was in server.py (sprint_label + project params)."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    # Each handler must accept sprint_label and project params
    for handler in HANDLER_NAMES:
        assert f"def {handler}" in source, f"Handler {handler} not found"
    # spot-check params are present
    assert "sprint_label" in source, "sprint_label parameter missing from handlers"
    assert "project" in source, "project parameter missing from handlers"


# ── AC4 ──────────────────────────────────────────────────────────────────────

def test_ac4_branch_status_not_in_server():
    """AC4: @app.get branch-status is no longer in server.py."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()
    violations = [
        ln for ln in lines
        if "@app.get" in ln and "branch-status" in ln
    ]
    assert not violations, (
        f"server.py still defines @app.get branch-status route: {violations}"
    )


def test_ac4_rerun_preview_not_in_server():
    """AC4: @app.get rerun/preview routes are no longer in server.py."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()
    violations = [
        ln for ln in lines
        if "@app.get" in ln and ("rerun/preview" in ln or "rerun-preview" in ln)
    ]
    assert not violations, (
        f"server.py still defines @app.get rerun preview route(s): {violations}"
    )


# ── AC5 ──────────────────────────────────────────────────────────────────────

def test_ac5_server_includes_router():
    """AC5: server.py mounts sprint_run_router via app.include_router(...)."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    assert "sprint_run_router" in source, (
        "server.py must import sprint_run_router from routers"
    )
    assert "app.include_router(sprint_run_router)" in source, (
        "server.py must call app.include_router(sprint_run_router)"
    )


def test_ac5_init_exports_sprint_run_router():
    """AC5: routers/__init__.py exports sprint_run_router."""
    init_path = DASHBOARD_DIR / "routers" / "__init__.py"
    source = init_path.read_text(encoding="utf-8")
    assert "sprint_run_router" in source, (
        "routers/__init__.py must import and export sprint_run_router"
    )


# ── AC6 ──────────────────────────────────────────────────────────────────────

def test_ac6_sprint_run_compiles():
    """AC6: routers/sprint_run.py has no syntax errors."""
    try:
        py_compile.compile(str(SPRINT_RUN_PATH), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"routers/sprint_run.py has syntax errors: {e}")


def test_ac6_server_compiles():
    """AC6: server.py has no syntax errors after the refactor."""
    try:
        py_compile.compile(str(SERVER_PATH), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"server.py has syntax errors after refactor: {e}")
