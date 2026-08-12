"""Tests for issue #2238 — Remove the overnight sprint scheduler.

Acceptance criteria:
  AC1 — All three modules deleted and their include_router calls removed
  AC2 — The scheduled-run toggle removed from the board UI; bundle rebuilt
  AC3 — No route under /api/scheduler remains
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_BUNDLE = _ROOT / "apps" / "dashboard" / "static" / "dist" / "bundle.js"
_SCHED_RUN_SRC = _ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "scheduled-run.js"


# ── AC1 — module files deleted ────────────────────────────────────────────────

def test_ac1_scheduler_router_module_deleted():
    """AC1: apps.dashboard.routers.scheduler must not exist."""
    for key in list(sys.modules.keys()):
        if "scheduler" in key and "concurrent" not in key:
            del sys.modules[key]
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("apps.dashboard.routers.scheduler")


def test_ac1_scheduler_service_module_deleted():
    """AC1: apps.dashboard.routers.scheduler_service must not exist."""
    for key in list(sys.modules.keys()):
        if "scheduler_service" in key:
            del sys.modules[key]
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("apps.dashboard.routers.scheduler_service")


def test_ac1_sprint_scheduler_module_deleted():
    """AC1: services.sprint_manager.sprint_scheduler must not exist."""
    for key in list(sys.modules.keys()):
        if "sprint_scheduler" in key:
            del sys.modules[key]
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("services.sprint_manager.sprint_scheduler")


# ── AC2 — scheduled-run.js removed from source ───────────────────────────────

def test_ac2_scheduled_run_js_deleted():
    """AC2: The scheduled-run.js source file must not exist."""
    assert not _SCHED_RUN_SRC.exists(), (
        f"scheduled-run.js still present at {_SCHED_RUN_SRC}"
    )


def test_ac2_bundle_excludes_sched_toggle():
    """AC2: Rebuilt bundle must not contain the Run-on-schedule toggle function."""
    if not _BUNDLE.exists():
        pytest.skip("bundle.js not found; run npm run build first")
    text = _BUNDLE.read_text(encoding="utf-8")
    assert "smgmtToggleRunOnSchedule" not in text, (
        "bundle.js still exports smgmtToggleRunOnSchedule"
    )
    assert "smgmtSchedToggleHtml" not in text, (
        "bundle.js still exports _smgmtSchedToggleHtml"
    )
    assert "_smgmtHydrateSchedToggles" not in text, (
        "bundle.js still exports _smgmtHydrateSchedToggles"
    )


# ── AC3 — no /api/scheduler routes remain ────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from apps.dashboard.server import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_ac3_scheduler_config_get_returns_404(client):
    """AC3: GET /api/scheduler/config must return 404."""
    resp = client.get("/api/scheduler/config", params={"project": "test/repo"})
    assert resp.status_code == 404, (
        f"Expected 404 but got {resp.status_code} — scheduler route still exists"
    )


def test_ac3_scheduler_config_put_returns_404(client):
    """AC3: PUT /api/scheduler/config must return 404."""
    resp = client.put(
        "/api/scheduler/config",
        json={"project": "test/repo", "scheduled_run_time": "02:00"},
    )
    assert resp.status_code == 404, (
        f"Expected 404 but got {resp.status_code} — scheduler PUT route still exists"
    )


def test_ac3_scheduler_sprints_get_returns_404(client):
    """AC3: GET /api/scheduler/sprints must return 404."""
    resp = client.get("/api/scheduler/sprints", params={"project": "test/repo"})
    assert resp.status_code == 404, (
        f"Expected 404 but got {resp.status_code} — scheduler sprints route still exists"
    )
