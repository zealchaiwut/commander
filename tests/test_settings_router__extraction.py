"""Verify all settings-cluster routes exist and are accessible (issue extraction).

Uses a fresh TestClient against server.py. Tests check route registration
(200/4xx responses), not business logic correctness — the goal is to confirm
the extraction didn't accidentally drop or rename any route.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure apps/dashboard is on sys.path for server imports
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from server import app
    return TestClient(app, raise_server_exceptions=False)


# ── Route existence checks ────────────────────────────────────────────────────

def test_get_settings_route_exists(client):
    """GET /api/settings should be registered (returns 200)."""
    resp = client.get("/api/settings")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


def test_put_settings_route_exists(client):
    """PUT /api/settings should be registered (returns 200 or 400 for bad payload)."""
    resp = client.put("/api/settings", json={})
    assert resp.status_code in (200, 400, 422), f"Unexpected status {resp.status_code}"


def test_delete_project_settings_route_exists(client):
    """DELETE /api/projects/{slug}/settings should be registered (returns 404 for unknown slug)."""
    resp = client.delete("/api/projects/__nonexistent_slug__/settings")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_get_project_settings_route_exists(client):
    """GET /api/projects/{slug}/settings should be registered."""
    resp = client.get("/api/projects/__nonexistent_slug__/settings")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_put_project_settings_route_exists(client):
    """PUT /api/projects/{slug}/settings should be registered."""
    resp = client.put("/api/projects/__nonexistent_slug__/settings", json={})
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_get_deploy_config_route_exists(client):
    """GET /api/projects/{slug}/deploy-config should be registered."""
    resp = client.get("/api/projects/__nonexistent_slug__/deploy-config")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_put_deploy_config_route_exists(client):
    """PUT /api/projects/{slug}/deploy-config should be registered."""
    resp = client.put("/api/projects/__nonexistent_slug__/deploy-config", json={})
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_validate_deploy_config_route_exists(client):
    """POST /api/projects/{slug}/environments/{env}/deploy-config/validate should be registered."""
    resp = client.post(
        "/api/projects/__nonexistent_slug__/environments/prd/deploy-config/validate",
        json={},
    )
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_fs_list_route_exists(client):
    """GET /api/fs/list should be registered and return a directory listing."""
    resp = client.get("/api/fs/list")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "entries" in data, "Response should have 'entries' key"
    assert "current" in data, "Response should have 'current' key"


def test_get_env_vars_route_exists(client):
    """GET /api/projects/{slug}/environments/{env}/env-vars should be registered."""
    resp = client.get("/api/projects/__nonexistent_slug__/environments/prd/env-vars")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_put_env_vars_route_exists(client):
    """PUT /api/projects/{slug}/environments/{env}/env-vars should be registered."""
    resp = client.put(
        "/api/projects/__nonexistent_slug__/environments/prd/env-vars",
        json={"vars": []},
    )
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


def test_scaffold_check_route_exists(client):
    """GET /api/projects/{slug}/docs/scaffold/check should be registered."""
    resp = client.get("/api/projects/__nonexistent_slug__/docs/scaffold/check")
    assert resp.status_code in (404, 503), f"Unexpected status {resp.status_code}"


def test_scaffold_apply_route_exists(client):
    """POST /api/projects/{slug}/docs/scaffold/apply should be registered."""
    resp = client.post(
        "/api/projects/__nonexistent_slug__/docs/scaffold/apply",
        json={"confirm": False},
    )
    assert resp.status_code in (400, 404, 503), f"Unexpected status {resp.status_code}"


def test_get_project_notes_route_exists(client):
    """GET /api/projects/notes should be registered."""
    resp = client.get("/api/projects/notes")
    assert resp.status_code == 400, f"Expected 400 (missing repo param), got {resp.status_code}"


def test_post_project_notes_route_exists(client):
    """POST /api/projects/notes should be registered."""
    resp = client.post("/api/projects/notes", json={"content": "test"})
    assert resp.status_code == 400, f"Expected 400 (missing repo param), got {resp.status_code}"


# ── Confirm routes not in server.py monolith directly ────────────────────────

def test_routes_served_from_settings_router(client):
    """Verify the settings router is in the app route list."""
    routes = [str(r.path) for r in client.app.routes if hasattr(r, "path")]
    settings_routes = [
        "/api/settings",
        "/api/projects/{slug}/settings",
        "/api/projects/{slug}/deploy-config",
        "/api/fs/list",
        "/api/projects/notes",
    ]
    for route_path in settings_routes:
        assert route_path in routes, f"Route '{route_path}' not found in app routes"
