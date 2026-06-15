"""Smoke tests for the logs-ingestion router extraction.

Verifies that the four endpoints previously in server.py:
  POST   /api/agent-event
  POST   /api/token-usage
  DELETE /api/events/test
  GET    /events  (SSE stream)
are now served by ``routers/logs.py`` + ``routers/logs_service.py`` and are
still reachable on the app at the same paths with the same HTTP methods.

One test per acceptance criterion to keep the report readable.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))
if str(REPO_ROOT / "services" / "sprint_manager") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

import os
os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

from fastapi import APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


LOGS_ROUTES = {
    "/api/agent-event": {"POST"},
    "/api/token-usage":  {"POST"},
    "/api/events/test":  {"DELETE"},
    "/events":           {"GET"},
}


def _route_methods(routes) -> dict[str, set[str]]:
    """Build {path: {methods}} dropping auto HEAD/OPTIONS noise."""
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


# ── AC1: routers/logs.py and routers/logs_service.py exist ───────────────────

def test_ac1_logs_router_file_exists():
    assert (DASHBOARD_DIR / "routers" / "logs.py").exists()
    assert (DASHBOARD_DIR / "routers" / "logs_service.py").exists()


def test_ac1_logs_router_is_api_router():
    mod = importlib.import_module("routers.logs")
    assert isinstance(mod.router, APIRouter)


def test_ac1_logs_service_has_required_symbols():
    svc = importlib.import_module("routers.logs_service")
    assert hasattr(svc, "AgentEvent")
    assert hasattr(svc, "TokenUsageEvent")
    assert hasattr(svc, "broadcast")
    assert hasattr(svc, "_subscribers")
    assert hasattr(svc, "receive_agent_event")
    assert hasattr(svc, "receive_token_usage")
    assert hasattr(svc, "clear_test_events")


# ── AC2: router exposes all four endpoints ────────────────────────────────────

def test_ac2_logs_router_exposes_all_routes():
    mod = importlib.import_module("routers.logs")
    got = _route_methods(mod.router.routes)
    for path, methods in LOGS_ROUTES.items():
        assert path in got, f"{path} missing from logs router"
        assert methods <= got[path], (
            f"{path} methods {got[path]} != expected {methods}"
        )


# ── AC3: all routes registered on the app ────────────────────────────────────

def test_ac3_app_registers_logs_routes():
    import server as srv
    got = _route_methods(srv.app.routes)
    for path, methods in LOGS_ROUTES.items():
        assert path in got, f"{path} missing from app route table"
        assert methods <= got[path], (
            f"App {path} methods {got[path]} != expected {methods}"
        )


# ── AC4: endpoints return expected HTTP status codes ─────────────────────────

@pytest.fixture(scope="module")
def live_client():
    """TestClient with the app and DB tables pre-created."""
    import db as _db
    _db.init_db()
    import server as srv
    return TestClient(srv.app, raise_server_exceptions=True)


def test_ac4_post_agent_event_returns_200(live_client):
    resp = live_client.post("/api/agent-event", json={
        "event_type": "tool_use",
        "working_dir": "/tmp",
        "status": "working",
    })
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_ac4_post_token_usage_returns_200(live_client):
    resp = live_client.post("/api/token-usage", json={
        "input_tokens": 10,
        "output_tokens": 5,
        "working_dir": "/tmp",
    })
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_ac4_post_token_usage_noop_returns_200(live_client):
    """Zero-token events return {ok: true} without DB writes."""
    resp = live_client.post("/api/token-usage", json={
        "input_tokens": 0,
        "output_tokens": 0,
    })
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_ac4_delete_events_test_returns_200(live_client):
    resp = live_client.delete("/api/events/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert "events_deleted" in body
    assert "agents_deleted" in body
    assert "alerts_cleared" in body


def test_ac4_get_events_sse_stream_registered():
    """GET /events is registered on the router with the correct method."""
    mod = importlib.import_module("routers.logs")
    got = _route_methods(mod.router.routes)
    assert "/events" in got
    assert "GET" in got["/events"]


# ── AC5: extracted handlers absent from server.py ────────────────────────────

def test_ac5_handlers_absent_from_server_py():
    src = SERVER_PY.read_text()
    assert '@app.post("/api/agent-event")' not in src, (
        "POST /api/agent-event still declared with @app.post in server.py"
    )
    assert '@app.post("/api/token-usage")' not in src, (
        "POST /api/token-usage still declared with @app.post in server.py"
    )
    assert '@app.delete("/api/events/test")' not in src, (
        "DELETE /api/events/test still declared with @app.delete in server.py"
    )
    assert '@app.get("/events")' not in src, (
        "GET /events still declared with @app.get in server.py"
    )


# ── AC6: shared state (_alerts, broadcast) visible from server.py ────────────

def test_ac6_alerts_shared_between_server_and_logs_service():
    """Mutations to _alerts in logs_service are visible in server.py and vice versa."""
    import server as srv
    import routers.logs_service as svc

    # Both modules must reference the same list object
    assert srv._alerts is svc._alerts, (
        "_alerts in server.py and logs_service are different objects — "
        "test-alert purge will be silently broken"
    )


def test_ac6_broadcast_is_imported_from_logs_service():
    """server.py's broadcast must be the same callable as logs_service.broadcast."""
    import server as srv
    import routers.logs_service as svc
    assert srv.broadcast is svc.broadcast
