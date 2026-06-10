"""Tester verification for issue #725 — Render API deploy/restart integration.

Independent tester-owned suite (one test per acceptance criterion). The feature
talks to the external Render API (https://api.render.com), which no UAT server
can reach with valid creds, so the endpoints are exercised in-process via
FastAPI TestClient with `render_actions.call_render` mocked — the only faithful,
deterministic way to verify the 401/404 → 502 mapping, the 400-before-call
guard, and the "key never leaks" contract. Pure helpers are asserted directly.

AC1 — POST .../deploy → POST /v1/services/{id}/deploys w/ Bearer when host=render
AC2 — POST .../restart → Render restart endpoint when host=render
AC3 — render_api_key never appears in any response body
AC4 — status endpoint GETs deploys?limit=1 → normalized queued|building|live|failed
AC5 — frontend polls the status endpoint to reflect building → live
AC6 — Render 401 → HTTP 502 "Invalid Render API key"
AC7 — Render 404 → HTTP 502 "Render service not found — check render_service_id"
AC8 — missing render_service_id/render_api_key → HTTP 400 before any Render call
AC9 — non-render hosts deploy/restart unaffected
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from services.sprint_manager import render_actions as ra  # noqa: E402


# ── in-process app harness (Render API mocked) ───────────────────────────────


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                project TEXT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(scope, project, key)
            )
        """))
        conn.commit()
    return engine


_PROJECTS = [
    {"repo": "zealchaiwut/commander"},
    {"repo": "owner/perf-coach"},
]


@pytest.fixture()
def app_ctx():
    engine = _make_engine()
    for mod in (
        "server",
        "services.sprint_manager.settings_repo",
        "services.sprint_manager.deploy_config_schema",
        "services.sprint_manager.deploy_actions",
        "services.sprint_manager.render_actions",
    ):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    settings_repo._session_factory = sessionmaker(bind=engine)

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=_PROJECTS), \
         patch.object(srv, "_settings_repo", settings_repo), \
         patch.object(srv.projects_module, "get_project_environments", return_value={}), \
         patch.object(srv, "_derive_project_environments", return_value={}):
        client = TestClient(srv.app, raise_server_exceptions=False)
        yield client, srv, settings_repo


def _save_cfg(settings_repo, env_overrides, project="owner/perf-coach"):
    from services.sprint_manager.deploy_config_schema import DEPLOY_CONFIG_KEY
    settings_repo.set_setting("project", DEPLOY_CONFIG_KEY, env_overrides, project=project)


def _render_env(**extra):
    base = {"host": "render", "render_service_id": "srv-xyz", "render_api_key": "rnd_topsecret"}
    base.update(extra)
    return base


# ── AC1 — deploy hits Render deploys endpoint with Bearer auth ────────────────


def test_render_deploy_restart__deploy_posts_to_render_with_bearer(app_ctx):
    client, srv, repo = app_ctx
    _save_cfg(repo, {"prd": _render_env()})
    calls = []

    def fake(method, url, api_key, **kw):
        calls.append((method, url, api_key))
        return 201, {"id": "dep-1", "status": "queued"}

    with patch.object(srv._render_actions, "call_render", side_effect=fake):
        r = client.post("/api/projects/perf-coach/environments/prd/deploy")
    assert r.status_code == 200, r.text
    assert calls == [("POST", "https://api.render.com/v1/services/srv-xyz/deploys", "rnd_topsecret")]
    # and the pure builders the endpoint relies on are correct
    assert ra.deploy_url("srv-xyz") == "https://api.render.com/v1/services/srv-xyz/deploys"
    assert ra.auth_headers("rnd_topsecret")["Authorization"] == "Bearer rnd_topsecret"


# ── AC2 — restart hits Render restart endpoint ───────────────────────────────


def test_render_deploy_restart__restart_posts_to_render_restart(app_ctx):
    client, srv, repo = app_ctx
    _save_cfg(repo, {"prd": _render_env()})
    calls = []

    def fake(method, url, api_key, **kw):
        calls.append((method, url))
        return 200, {}

    with patch.object(srv._render_actions, "call_render", side_effect=fake):
        r = client.post("/api/projects/perf-coach/environments/prd/restart")
    assert r.status_code == 200, r.text
    assert calls == [("POST", "https://api.render.com/v1/services/srv-xyz/restart")]


# ── AC3 — api key never leaks into a response body ───────────────────────────


def test_render_deploy_restart__api_key_never_in_response(app_ctx):
    client, srv, repo = app_ctx
    _save_cfg(repo, {"prd": _render_env()})
    with patch.object(srv._render_actions, "call_render",
                      return_value=(201, {"id": "dep-1", "status": "live"})):
        deploy = client.post("/api/projects/perf-coach/environments/prd/deploy")
        status = client.get("/api/projects/perf-coach/environments/prd/deploy-status")
    with patch.object(srv._render_actions, "call_render", return_value=(200, {})):
        restart = client.post("/api/projects/perf-coach/environments/prd/restart")
    for resp in (deploy, status, restart):
        assert "rnd_topsecret" not in resp.text


# ── AC4 — status endpoint normalizes to the four allowed states ──────────────


def test_render_deploy_restart__status_endpoint_normalizes(app_ctx):
    client, srv, repo = app_ctx
    _save_cfg(repo, {"prd": _render_env()})
    calls = []

    def fake(method, url, api_key, **kw):
        calls.append((method, url))
        return 200, [{"deploy": {"id": "dep-1", "status": "build_in_progress"}}]

    with patch.object(srv._render_actions, "call_render", side_effect=fake):
        r = client.get("/api/projects/perf-coach/environments/prd/deploy-status")
    assert r.status_code == 200, r.text
    assert calls == [("GET", "https://api.render.com/v1/services/srv-xyz/deploys?limit=1")]
    assert r.json()["status"] == "building"
    # full map stays inside the allowed set, incl. unknown fallback
    allowed = {"queued", "building", "live", "failed"}
    for raw in ("created", "queued", "build_in_progress", "live", "build_failed",
                "canceled", "deactivated", "??unknown??", None):
        assert ra.normalize_status(raw) in allowed


# ── AC5 — frontend polls the status endpoint ─────────────────────────────────


def test_render_deploy_restart__frontend_polls_status():
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "deploy-status" in html
    assert ("setTimeout" in html or "setInterval" in html)


# ── AC6 — 401 → 502 invalid key ──────────────────────────────────────────────


def test_render_deploy_restart__401_maps_to_502(app_ctx):
    client, srv, repo = app_ctx
    _save_cfg(repo, {"prd": _render_env(render_api_key="bad")})

    def fake(method, url, api_key, **kw):
        raise srv._render_actions.RenderApiError(*ra.map_api_error(401))

    with patch.object(srv._render_actions, "call_render", side_effect=fake):
        r = client.post("/api/projects/perf-coach/environments/prd/deploy")
    assert r.status_code == 502
    assert r.json()["detail"] == "Invalid Render API key"
    assert ra.map_api_error(401) == (502, "Invalid Render API key")


# ── AC7 — 404 → 502 service not found ────────────────────────────────────────


def test_render_deploy_restart__404_maps_to_502(app_ctx):
    client, srv, repo = app_ctx
    _save_cfg(repo, {"prd": _render_env(render_service_id="srv-bad")})

    def fake(method, url, api_key, **kw):
        raise srv._render_actions.RenderApiError(*ra.map_api_error(404))

    with patch.object(srv._render_actions, "call_render", side_effect=fake):
        r = client.post("/api/projects/perf-coach/environments/prd/deploy")
    assert r.status_code == 502
    assert r.json()["detail"] == "Render service not found — check render_service_id"


# ── AC8 — missing config → 400 before any Render call ────────────────────────


def test_render_deploy_restart__missing_config_is_400_before_call(app_ctx):
    client, srv, repo = app_ctx
    # missing service id
    _save_cfg(repo, {"prd": {"host": "render", "render_api_key": "rnd_k"}})
    m1 = MagicMock()
    with patch.object(srv._render_actions, "call_render", m1):
        r1 = client.post("/api/projects/perf-coach/environments/prd/deploy")
    assert r1.status_code == 400
    m1.assert_not_called()

    # missing api key
    _save_cfg(repo, {"prd": {"host": "render", "render_service_id": "srv-xyz"}})
    m2 = MagicMock()
    with patch.object(srv._render_actions, "call_render", m2):
        r2 = client.post("/api/projects/perf-coach/environments/prd/deploy")
    assert r2.status_code == 400
    m2.assert_not_called()


# ── AC9 — non-render host unaffected ─────────────────────────────────────────


def test_render_deploy_restart__non_render_host_unaffected(app_ctx):
    client, srv, repo = app_ctx
    import subprocess
    _save_cfg(repo, {"uat": {"host": "local", "working_dir": "/srv/perf-uat",
                             "branch": "develop", "launchd_label": "com.perfcoach.uat"}})

    def fake_run(cmd, *a, **kw):
        out = "Fast-forward\n" if cmd[:2] == ["git", "pull"] else "abc123\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    render_call = MagicMock()
    with patch.object(srv.subprocess, "run", side_effect=fake_run), \
         patch.object(srv._render_actions, "call_render", render_call):
        r = client.post("/api/projects/perf-coach/environments/uat/deploy")
    assert r.status_code == 200, r.text
    assert "Fast-forward" in r.json()["pull_output"]
    render_call.assert_not_called()
    # is_render_host correctly rejects a local entry
    assert ra.is_render_host({"host": "local"}) is False
