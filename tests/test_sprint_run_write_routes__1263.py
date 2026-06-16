"""Tests for issue #1263 — Extract Sprint Run Write Routes from server.py.

AC1: POST /api/sprints/run moved to routers/sprint_run.py; business/subprocess
     logic in sprint_run_service.py
AC2: POST /api/sprints/{label}/rerun moved to routers/sprint_run.py
AC3: DELETE /api/sprints/run/{label} moved to routers/sprint_run.py
AC4: All subprocess glue lives exclusively in sprint_run_service.py
AC5: routers/sprint_run.py router registered in server.py via app.include_router()
AC6: None of the three endpoints remain directly defined in server.py
AC7: py_compile passes for server.py, sprint_run_service.py, routers/sprint_run.py
AC8: No existing endpoint paths, HTTP methods, or response contracts change
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
SPRINT_RUN_SERVICE_PATH = DASHBOARD_DIR / "routers" / "sprint_run_service.py"
SERVER_PATH = DASHBOARD_DIR / "server.py"

# Handler names that must now live in sprint_run.py
WRITE_HANDLER_NAMES = [
    "run_sprint_managed",
    "kill_sprint",
    "rerun_sprint",
]

# URL slugs for the three write routes
WRITE_SLUGS = [
    "/api/sprints/run",
    "/api/sprints/{sprint_label}/rerun",
    "/api/sprints/run/{sprint_label}",
]

# Subprocess function names that should be in sprint_run_service.py
SUBPROCESS_FN_NAMES = [
    "spawn_sprint_process",
    "kill_sprint_process",
]


# ── AC1 ──────────────────────────────────────────────────────────────────────

def test_ac1_sprint_run_service_exists():
    """AC1: sprint_run_service.py exists alongside sprint_run.py."""
    assert SPRINT_RUN_SERVICE_PATH.exists(), (
        "routers/sprint_run_service.py not found — subprocess/business logic "
        "must live here per AC1"
    )


def test_ac1_run_handler_in_router():
    """AC1: run_sprint_managed is defined in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "def run_sprint_managed" in source, (
        "run_sprint_managed handler missing from routers/sprint_run.py"
    )


def test_ac1_router_post_run():
    """AC1: @router.post('/api/sprints/run') is declared in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "/api/sprints/run" in source, (
        "POST /api/sprints/run route not found in sprint_run.py"
    )
    assert "@router.post" in source, (
        "sprint_run.py must use @router.post, not @app.post"
    )


def test_ac1_service_has_subprocess_logic():
    """AC1: sprint_run_service.py contains subprocess spawn logic."""
    source = SPRINT_RUN_SERVICE_PATH.read_text(encoding="utf-8")
    assert "subprocess" in source or "Popen" in source, (
        "sprint_run_service.py must contain subprocess/Popen logic (AC1 — "
        "subprocess glue lives in the service module)"
    )


# ── AC2 ──────────────────────────────────────────────────────────────────────

def test_ac2_rerun_handler_in_router():
    """AC2: rerun_sprint is defined in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "def rerun_sprint" in source, (
        "rerun_sprint handler missing from routers/sprint_run.py"
    )


def test_ac2_rerun_route_in_router():
    """AC2: POST /api/sprints/{sprint_label}/rerun is in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "rerun" in source and "@router.post" in source, (
        "POST rerun route not found in sprint_run.py"
    )


# ── AC3 ──────────────────────────────────────────────────────────────────────

def test_ac3_kill_handler_in_router():
    """AC3: kill_sprint is defined in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "def kill_sprint" in source, (
        "kill_sprint handler missing from routers/sprint_run.py"
    )


def test_ac3_delete_route_in_router():
    """AC3: DELETE /api/sprints/run/{sprint_label} is in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "@router.delete" in source, (
        "sprint_run.py must use @router.delete for DELETE /api/sprints/run/{sprint_label}"
    )


# ── AC4 ──────────────────────────────────────────────────────────────────────

def test_ac4_spawn_function_in_service():
    """AC4: sprint_run_service.py has a subprocess spawn function."""
    source = SPRINT_RUN_SERVICE_PATH.read_text(encoding="utf-8")
    has_spawn = any(fn in source for fn in SUBPROCESS_FN_NAMES)
    assert has_spawn, (
        f"sprint_run_service.py must contain one of: {SUBPROCESS_FN_NAMES} "
        "(subprocess spawn logic per AC4)"
    )


def test_ac4_kill_function_in_service():
    """AC4: sprint_run_service.py has a process kill function."""
    source = SPRINT_RUN_SERVICE_PATH.read_text(encoding="utf-8")
    assert "kill" in source.lower() or "SIGTERM" in source or "SIGKILL" in source, (
        "sprint_run_service.py must contain kill/signal logic (AC4)"
    )


def test_ac4_popen_not_in_router():
    """AC4: routers/sprint_run.py does not call Popen directly (logic in service)."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "subprocess.Popen" not in source, (
        "routers/sprint_run.py must not call subprocess.Popen directly — "
        "subprocess glue belongs in sprint_run_service.py (AC4)"
    )


# ── AC5 ──────────────────────────────────────────────────────────────────────

def test_ac5_server_includes_router():
    """AC5: server.py registers sprint_run_router via app.include_router."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    assert "sprint_run_router" in source, (
        "server.py must import sprint_run_router"
    )
    assert "app.include_router(sprint_run_router)" in source, (
        "server.py must call app.include_router(sprint_run_router)"
    )


def test_ac5_init_exports_router():
    """AC5: routers/__init__.py exports sprint_run_router."""
    init_path = DASHBOARD_DIR / "routers" / "__init__.py"
    source = init_path.read_text(encoding="utf-8")
    assert "sprint_run_router" in source, (
        "routers/__init__.py must export sprint_run_router"
    )


# ── AC6 ──────────────────────────────────────────────────────────────────────

def test_ac6_post_run_not_in_server():
    """AC6: @app.post('/api/sprints/run') is no longer in server.py."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()
    violations = [
        ln.strip() for ln in lines
        if "@app.post" in ln and "/api/sprints/run" in ln
        and "rerun" not in ln  # exclude rerun route check (separate AC)
    ]
    assert not violations, (
        f"server.py still defines @app.post for /api/sprints/run: {violations}"
    )


def test_ac6_rerun_not_in_server():
    """AC6: @app.post('.../rerun') is no longer in server.py."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()
    violations = [
        ln.strip() for ln in lines
        if "@app.post" in ln and "rerun" in ln
    ]
    assert not violations, (
        f"server.py still defines @app.post rerun route: {violations}"
    )


def test_ac6_delete_run_not_in_server():
    """AC6: @app.delete('/api/sprints/run/...') is no longer in server.py."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()
    violations = [
        ln.strip() for ln in lines
        if "@app.delete" in ln and "sprints" in ln and "run" in ln
    ]
    assert not violations, (
        f"server.py still defines @app.delete sprint run route: {violations}"
    )


# ── AC7 ──────────────────────────────────────────────────────────────────────

def test_ac7_server_compiles():
    """AC7: server.py has no syntax errors."""
    try:
        py_compile.compile(str(SERVER_PATH), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"server.py has syntax errors: {e}")


def test_ac7_sprint_run_service_compiles():
    """AC7: sprint_run_service.py has no syntax errors."""
    assert SPRINT_RUN_SERVICE_PATH.exists(), "sprint_run_service.py does not exist"
    try:
        py_compile.compile(str(SPRINT_RUN_SERVICE_PATH), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"sprint_run_service.py has syntax errors: {e}")


def test_ac7_sprint_run_router_compiles():
    """AC7: routers/sprint_run.py has no syntax errors."""
    try:
        py_compile.compile(str(SPRINT_RUN_PATH), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"routers/sprint_run.py has syntax errors: {e}")


# ── AC8 ──────────────────────────────────────────────────────────────────────

def test_ac8_run_route_path_unchanged():
    """AC8: POST /api/sprints/run path is unchanged in sprint_run.py."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert '"/api/sprints/run"' in source, (
        "POST /api/sprints/run path must be preserved exactly in sprint_run.py"
    )


def test_ac8_delete_route_path_unchanged():
    """AC8: DELETE /api/sprints/run/{sprint_label} path is unchanged."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert '"/api/sprints/run/{sprint_label}"' in source, (
        "DELETE /api/sprints/run/{sprint_label} path must be preserved exactly"
    )


def test_ac8_rerun_route_path_unchanged():
    """AC8: POST /api/sprints/{sprint_label}/rerun path is unchanged."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert '"/api/sprints/{sprint_label}/rerun"' in source, (
        "POST /api/sprints/{sprint_label}/rerun path must be preserved exactly"
    )


def test_ac8_run_returns_202():
    """AC8: run_sprint_managed route uses status_code=202."""
    source = SPRINT_RUN_PATH.read_text(encoding="utf-8")
    assert "status_code=202" in source, (
        "POST /api/sprints/run must keep status_code=202 response contract"
    )
