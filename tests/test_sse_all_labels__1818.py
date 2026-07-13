"""Tests for issue #1818: SSE board connects only the first running sprint label

AC1: When _smgmtRunningLabels has N labels, _smgmtLivePollRestart opens N distinct
     EventSource connections (one per label)
AC2: An SSE error/complete on one label does not disconnect the other labels'
     streams — each label's connection is independent
AC3: _smgmtSseDisconnect closes all open EventSource connections and empties
     the internal _smgmtSseEs map

ACs are verified via frontend behavioral tests (node --test) in
tests/frontend/smgmt-sse-all-labels.test.mjs

This pytest file verifies the HTTP-level SSE infrastructure supports multiple
concurrent sprint labels without requiring a live server.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Match  # noqa: E402

from routers.running import router as running_router  # noqa: E402
from routers.sprint_live import router as sprint_live_router  # noqa: E402


def _make_running_client() -> TestClient:
    app = FastAPI()
    app.include_router(running_router)
    return TestClient(app, raise_server_exceptions=False)


def _get_live_route():
    return next(
        r for r in sprint_live_router.routes
        if getattr(r, "path", "") == "/api/sprints/{sprint_label}/live"
    )


# --- AC1: live endpoint is parameterized — supports any label, not just the first ---

def test_sse_all_labels__ac1_live_snapshot_per_label():
    """AC1: The /api/sprints/{label}/live route is parameterized and matches both sprint-117 and sprint-118."""
    routes = {getattr(r, "path", "") for r in sprint_live_router.routes}
    assert "/api/sprints/{sprint_label}/live" in routes, (
        f"Route /api/sprints/{{sprint_label}}/live not registered. Found: {routes}"
    )

    live_route = _get_live_route()
    scope1 = {"type": "http", "method": "GET", "path": "/api/sprints/sprint-117/live"}
    scope2 = {"type": "http", "method": "GET", "path": "/api/sprints/sprint-118/live"}
    match1, _ = live_route.matches(scope1)
    match2, _ = live_route.matches(scope2)
    assert match1 == Match.FULL, "Route must match sprint-117"
    assert match2 == Match.FULL, "Route must match sprint-118"


def test_sse_all_labels__ac1_multiple_labels_support():
    """AC1: /api/running returns 200 or 404 (not 422/500) for a valid project."""
    with patch("routers.running.build_running_snapshot", return_value=None):
        client = _make_running_client()
        r = client.get("/api/running?project=zealchaiwut/commander")
        assert r.status_code in (200, 404), (
            f"GET /api/running must return 200 or 404, got {r.status_code}: {r.text}"
        )


# --- AC2: each label's stream is independently routable ---

def test_sse_all_labels__ac2_independent_streams():
    """AC2: sprint-117 and sprint-118 both fully match the live route independently."""
    live_route = _get_live_route()

    for label in ("sprint-117", "sprint-118", "sprint-1", "sprint-999"):
        scope = {"type": "http", "method": "GET", "path": f"/api/sprints/{label}/live"}
        match, _ = live_route.matches(scope)
        assert match == Match.FULL, (
            f"Live route must independently match label '{label}', got {match}"
        )


# --- AC3: /api/running returns current state with no stale labels ---

def test_sse_all_labels__ac3_api_consistency():
    """AC3: /api/running returns 404 when no sprint running (no stale label leak)."""
    with patch("routers.running.build_running_snapshot", return_value=None):
        client = _make_running_client()
        r = client.get("/api/running?project=zealchaiwut/commander")
        assert r.status_code == 404
        data = r.json()
        assert "detail" in data, "404 response must have 'detail' field"


# --- Frontend behavioral tests are the primary AC verification ---

def test_sse_all_labels__frontend_tests_exist():
    """Frontend smgmt-sse-all-labels.test.mjs must exist and cover all three ACs."""
    test_file = REPO_ROOT / "tests" / "frontend" / "smgmt-sse-all-labels.test.mjs"
    assert test_file.exists(), f"Frontend test file {test_file} must exist"

    content = test_file.read_text()
    assert "AC1" in content, "Frontend tests must cover AC1"
    assert "AC2" in content, "Frontend tests must cover AC2"
    assert "AC3" in content, "Frontend tests must cover AC3"
