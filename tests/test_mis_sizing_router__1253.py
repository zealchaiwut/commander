"""Tests for issue #1253 — extract mis-sizing routes from server.py to routers/mis_sizing.py.

One test function per acceptance criterion.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

# Ensure dashboard dir is first in sys.path so `from config import ...` resolves
# to apps/dashboard/config.py, not services/sprint_manager/config.py.
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from fastapi import APIRouter  # noqa: E402


# Route table expected after extraction
MIS_SIZING_ROUTES = {
    "/api/sprints/{sprint_label}/mis-sizing-flags": {"GET"},
    "/api/sprints/{sprint_label}/mis-sizing-flags/generate": {"POST"},
    "/api/sprints/{sprint_label}/mis-sizing-flags/{issue_id}/action": {"POST"},
    "/api/mis-sizing/history": {"GET"},
    "/api/mis-sizing/rebuild": {"POST"},
    "/api/mis-sizing/config": {"GET", "POST"},
}


def _route_methods(routes) -> dict[str, set[str]]:
    """Build {path: {methods}} dropping HEAD/OPTIONS noise."""
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


# ── AC1: routers/mis_sizing.py exists with all expected routes ────────────────

def test_ac1_mis_sizing_router_file_exists():
    assert (ROUTERS_DIR / "mis_sizing.py").exists(), (
        "routers/mis_sizing.py does not exist"
    )


def test_ac1_mis_sizing_router_is_apirouter():
    mod = importlib.import_module("routers.mis_sizing")
    assert isinstance(mod.router, APIRouter), (
        "routers.mis_sizing.router is not an APIRouter instance"
    )


def test_ac1_mis_sizing_router_has_all_routes():
    mod = importlib.import_module("routers.mis_sizing")
    got = _route_methods(mod.router.routes)
    for path, methods in MIS_SIZING_ROUTES.items():
        assert path in got, f"{path} missing from mis_sizing router"
        assert methods <= got[path], (
            f"{path}: expected methods {methods}, got {got[path]}"
        )


# ── AC2: routes delegate to _mis_sizing module, no logic duplication ──────────

def test_ac2_router_imports_mis_sizing_module():
    """The router file references the mis_sizing service module."""
    src = (ROUTERS_DIR / "mis_sizing.py").read_text()
    assert "mis_sizing" in src, (
        "routers/mis_sizing.py does not reference the mis_sizing module"
    )
    # Must not contain duplicated business logic (e.g. raw flag computation)
    assert "generate_and_save_flags" not in src.replace(
        "_mis_sizing.generate_and_save_flags", ""
    ).replace(
        "mis_sizing.generate_and_save_flags", ""
    ), (
        "generate_and_save_flags appears to be duplicated rather than delegated"
    )


# ── AC3: server.py includes the router, no inline mis-sizing route definitions ─

def test_ac3_server_includes_mis_sizing_router():
    src = SERVER_PY.read_text()
    assert "mis_sizing_router" in src, (
        "server.py does not reference mis_sizing_router"
    )
    assert "app.include_router(mis_sizing_router)" in src, (
        "server.py missing app.include_router(mis_sizing_router)"
    )


def test_ac3_no_inline_mis_sizing_routes_in_server():
    src = SERVER_PY.read_text()
    inline_paths = [
        '"/api/sprints/{sprint_label}/mis-sizing-flags"',
        '"/api/sprints/{sprint_label}/mis-sizing-flags/generate"',
        '"/api/sprints/{sprint_label}/mis-sizing-flags/{issue_id}/action"',
        '"/api/mis-sizing/history"',
        '"/api/mis-sizing/rebuild"',
        '"/api/mis-sizing/config"',
    ]
    for path_literal in inline_paths:
        assert f"@app.get({path_literal})" not in src, (
            f"server.py still has inline @app.get({path_literal})"
        )
        assert f"@app.post({path_literal})" not in src, (
            f"server.py still has inline @app.post({path_literal})"
        )


# ── AC4: py_compile exits 0 ───────────────────────────────────────────────────

def test_ac4_py_compile_clean():
    result = subprocess.run(
        [
            sys.executable,
            "-m", "py_compile",
            str(ROUTERS_DIR / "mis_sizing.py"),
            str(SERVER_PY),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout == "", f"Unexpected py_compile output: {result.stdout}"
    assert result.stderr == "", f"Unexpected py_compile errors: {result.stderr}"


# ── AC5: endpoint paths and HTTP methods identical on the app ─────────────────

def test_ac5_all_routes_registered_on_app_with_correct_methods():
    import server as srv
    on_app = _route_methods(srv.app.routes)
    for path, methods in MIS_SIZING_ROUTES.items():
        assert path in on_app, (
            f"Route {path} is missing from the FastAPI app after extraction"
        )
        assert methods <= on_app[path], (
            f"{path}: app has methods {on_app[path]}, expected {methods}"
        )


def test_ac5_routes_served_by_extracted_router_not_server_inline():
    """Routes must be served by the mis_sizing router, not inline server handlers."""
    import routers.mis_sizing as mis_mod
    router_paths = _route_methods(mis_mod.router.routes)
    for path in MIS_SIZING_ROUTES:
        assert path in router_paths, (
            f"{path} is not in routers.mis_sizing — still served inline?"
        )
