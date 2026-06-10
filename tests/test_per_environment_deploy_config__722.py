"""Tester verification for issue #722 — per-environment deploy config.

Independent (tester-authored) checks against the real ``server.app`` driving the
security-critical acceptance criteria: render_api_key is never returned in
cleartext (masked + render_api_key_set), an omitted key is preserved on PUT, and
unsupported envs/hosts are rejected with 400.

Runs in-process via FastAPI TestClient with an isolated in-memory settings store,
because no separate UAT HTTP server is provisioned for this clone (its .env
declares ENVIRONMENT=prd and the dashboard is not running). The deploy-config
endpoints are pure GitHub/SQLite/JSON — no external state — so in-process drive
exercises the same code path a live server would.
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

_PROJECTS = [{"repo": "zealchaiwut/commander"}, {"repo": "owner/perf-coach"}]


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
    ):
        sys.modules.pop(mod, None)
    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    settings_repo._session_factory = sessionmaker(bind=engine)
    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=_PROJECTS):
        with patch.object(srv, "_settings_repo", settings_repo):
            with patch.object(srv.projects_module, "get_project_environments", return_value={}):
                with patch.object(srv, "_derive_project_environments", return_value={}):
                    yield TestClient(srv.app, raise_server_exceptions=False)


# AC6 / AC7 — PUT a render key, GET must mask it and never leak cleartext.
def test_deploy_config__render_key_never_cleartext(client):
    secret = "rnd_live_supersecret_value"
    put = client.put(
        "/api/projects/perf-coach/deploy-config",
        json={"prd": {"host": "render", "render_service_id": "srv-abc123",
                      "render_api_key": secret}},
    )
    assert put.status_code == 200, put.text
    get = client.get("/api/projects/perf-coach/deploy-config")
    assert get.status_code == 200, get.text
    assert secret not in get.text          # raw secret must not appear anywhere
    assert secret not in put.text
    body = get.json()
    assert body["prd"]["render_api_key_set"] is True
    assert "render_api_key" not in body["prd"]
    assert body["prd"]["render_api_key_masked"].startswith("rnd_")
    assert body["prd"]["render_api_key_masked"].endswith("alue")


# AC10 — PUT omitting render_api_key preserves the stored secret.
def test_deploy_config__omitted_key_preserved(client):
    client.put(
        "/api/projects/perf-coach/deploy-config",
        json={"prd": {"host": "render", "render_api_key": "rnd_keep_me_around"}},
    )
    client.put(
        "/api/projects/perf-coach/deploy-config",
        json={"prd": {"host": "render", "render_service_id": "srv-xyz999"}},
    )
    body = client.get("/api/projects/perf-coach/deploy-config").json()
    assert body["prd"]["render_api_key_set"] is True
    assert body["prd"]["render_service_id"] == "srv-xyz999"


# AC8 — commander seed defaults surface on GET with no stored override.
def test_deploy_config__commander_seed_defaults(client):
    body = client.get("/api/projects/commander/deploy-config").json()
    assert body["prd"]["host"] == "local"
    assert body["prd"]["launchd_label"] == "com.commander.dashboard"
    assert body["prd"]["branch"] == "master"
    assert body["uat"]["host"] == "local"
    assert body["uat"]["branch"] == "develop"


# AC1 / AC2 — validation rejects unsupported env and host.
def test_deploy_config__validation_rejects_bad_env_and_host(client):
    assert client.put(
        "/api/projects/commander/deploy-config",
        json={"staging": {"host": "local"}},
    ).status_code == 400
    assert client.put(
        "/api/projects/commander/deploy-config",
        json={"prd": {"host": "aws"}},
    ).status_code == 400
