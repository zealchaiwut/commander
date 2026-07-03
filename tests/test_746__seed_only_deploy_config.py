"""Tests for issue #746 — seed-only-project fallback for GET/PUT deploy-config.

AC coverage:
  AC1 — GET /api/projects/{slug}/deploy-config returns 200 for a seed-only project
  AC2 — PUT /api/projects/{slug}/deploy-config returns 200 for a seed-only project
  AC3 — seed-only slugs resolve repo as zealchaiwut/{slug}
  AC4 — both endpoints resolve normally for slugs present in projects.json
  AC5 — a slug neither in projects.json nor a known seed still returns 404
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

# Only commander is in projects.json — perf-coach is seed-only in this setup.
_PROJECTS = [{"repo": "zealchaiwut/commander"}]


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE settings ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " scope TEXT NOT NULL, project TEXT, key TEXT NOT NULL,"
            " value TEXT NOT NULL,"
            " updated_at TEXT NOT NULL DEFAULT (datetime('now')),"
            " UNIQUE(scope, project, key))"
        ))
        conn.commit()
    return engine


@pytest.fixture()
def client():
    engine = _make_engine()
    for mod in (
        "server",
        "services.sprint_manager.settings_repo",
        "services.sprint_manager.deploy_config_schema",
        "apps.dashboard.routers.settings_service",
        "apps.dashboard.routers.settings",
    ):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    settings_repo._session_factory = sessionmaker(bind=engine)
    from fastapi.testclient import TestClient
    # patch at the module level so settings_service sees the same mock
    import apps.dashboard.routers.settings_service as ss  # noqa: PLC0415
    with patch.object(srv.projects_module, "load_projects", return_value=_PROJECTS):
        with patch.object(ss.projects_module, "load_projects", return_value=_PROJECTS):
            with patch.object(ss, "_settings_repo", settings_repo):
                with patch.object(ss.projects_module, "get_project_environments", return_value={}):
                    with patch.object(ss, "_derive_project_environments", return_value={}):
                        yield TestClient(srv.app, raise_server_exceptions=False)


# AC1 — GET returns 200 for a seed-only project (not in projects.json)
def test_get_deploy_config__seed_only_project_returns_200(client):
    resp = client.get("/api/projects/perf-coach/deploy-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict), "expected a dict deploy-config body"


# AC2 — PUT returns 200 for a seed-only project (not in projects.json)
def test_put_deploy_config__seed_only_project_returns_200(client):
    resp = client.put(
        "/api/projects/perf-coach/deploy-config",
        json={"prd": {"host": "render", "render_service_id": "svc-test-123"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict), "expected a dict deploy-config body"


# AC3 — seed-only slug resolves repo as zealchaiwut/{slug}
def test_get_deploy_config__seed_only_project_resolves_correct_repo(client):
    # PUT a config and verify it's stored/returned correctly (proves resolution happened)
    client.put(
        "/api/projects/perf-coach/deploy-config",
        json={"prd": {"host": "render", "render_service_id": "svc-abc"}},
    )
    resp = client.get("/api/projects/perf-coach/deploy-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Seed for perf-coach has prd.host=render and uat.host=local
    assert body.get("prd", {}).get("host") == "render"
    assert body.get("uat", {}).get("host") == "local"


# AC4 — GET and PUT resolve normally for slugs present in projects.json
def test_get_deploy_config__projects_json_slug_resolves_normally(client):
    resp = client.get("/api/projects/commander/deploy-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # commander seed has prd.host=local
    assert body.get("prd", {}).get("host") == "local"


def test_put_deploy_config__projects_json_slug_resolves_normally(client):
    resp = client.put(
        "/api/projects/commander/deploy-config",
        json={"prd": {"host": "local", "branch": "master"}},
    )
    assert resp.status_code == 200, resp.text


# AC5 — a slug not in projects.json and not a known seed returns 404
def test_get_deploy_config__unknown_slug_returns_404(client):
    resp = client.get("/api/projects/nonexistent-project-xyz/deploy-config")
    assert resp.status_code == 404, resp.text


def test_put_deploy_config__unknown_slug_returns_404(client):
    resp = client.put(
        "/api/projects/nonexistent-project-xyz/deploy-config",
        json={"prd": {"host": "local"}},
    )
    assert resp.status_code == 404, resp.text
