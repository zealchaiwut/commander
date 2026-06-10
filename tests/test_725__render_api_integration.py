"""Tests for issue #725 — integrate the Render API for deploy and restart.

AC coverage:
  AC1 — POST .../deploy triggers POST /v1/services/{id}/deploys with Bearer auth
         when host=render
  AC2 — POST .../restart triggers the Render restart endpoint when host=render
  AC3 — render_api_key is never in any response body / forwarded to the frontend
  AC4 — status endpoint calls GET .../deploys?limit=1 and returns a normalized
         status of queued|building|live|failed
  AC5 — frontend polls the status endpoint to reflect building → live
  AC6 — a 401 from Render → HTTP 502 "Invalid Render API key"
  AC7 — a 404 from Render → HTTP 502 "Render service not found — check render_service_id"
  AC8 — missing render_service_id/render_api_key → HTTP 400 before any Render call
  AC9 — existing deploy/restart behavior for non-render hosts is unaffected
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


# ── unit: pure builders / validators / normalizers ───────────────────────────


def test_deploy_url_targets_render_deploys_endpoint():
    """AC1: deploy URL is the Render create-deploy endpoint."""
    assert ra.deploy_url("srv-abc") == (
        "https://api.render.com/v1/services/srv-abc/deploys"
    )


def test_restart_url_targets_render_restart_endpoint():
    """AC2: restart URL is the Render restart endpoint."""
    assert ra.restart_url("srv-abc") == (
        "https://api.render.com/v1/services/srv-abc/restart"
    )


def test_status_url_limits_to_latest_deploy():
    """AC4: status URL fetches the latest deploy only."""
    assert ra.status_url("srv-abc") == (
        "https://api.render.com/v1/services/srv-abc/deploys?limit=1"
    )


def test_auth_headers_use_bearer_token():
    """AC1: requests carry Authorization: Bearer <key>."""
    headers = ra.auth_headers("rnd_secret")
    assert headers["Authorization"] == "Bearer rnd_secret"


def test_require_render_target_returns_service_and_key():
    sid, key = ra.require_render_target(
        {"host": "render", "render_service_id": "srv-1", "render_api_key": "rnd_k"}
    )
    assert sid == "srv-1"
    assert key == "rnd_k"


def test_require_render_target_missing_service_id_raises():
    """AC8: absent render_service_id is rejected before any call."""
    with pytest.raises(ra.RenderActionError):
        ra.require_render_target(
            {"host": "render", "render_api_key": "rnd_k"}
        )


def test_require_render_target_missing_api_key_raises():
    """AC8: absent render_api_key is rejected before any call."""
    with pytest.raises(ra.RenderActionError):
        ra.require_render_target(
            {"host": "render", "render_service_id": "srv-1"}
        )


@pytest.mark.parametrize("raw,norm", [
    ("created", "queued"),
    ("queued", "queued"),
    ("build_in_progress", "building"),
    ("update_in_progress", "building"),
    ("pre_deploy_in_progress", "building"),
    ("live", "live"),
    ("build_failed", "failed"),
    ("update_failed", "failed"),
    ("canceled", "failed"),
    ("deactivated", "failed"),
])
def test_normalize_status_maps_to_four_states(raw, norm):
    """AC4: every Render status maps into queued|building|live|failed."""
    assert ra.normalize_status(raw) == norm


def test_normalize_status_always_in_allowed_set():
    """AC4: normalized output is always one of the four allowed states."""
    allowed = {"queued", "building", "live", "failed"}
    for s in ("created", "live", "build_failed", "weird_unknown", None):
        assert ra.normalize_status(s) in allowed


def test_latest_status_from_payload_reads_first_deploy():
    """AC4: latest status pulled from the deploys?limit=1 payload."""
    payload = [{"deploy": {"id": "dep-1", "status": "build_in_progress"}}]
    assert ra.latest_status_from_payload(payload) == "building"


def test_map_api_error_401_is_invalid_key():
    """AC6: 401 maps to 502 with the invalid-key message."""
    status, detail = ra.map_api_error(401)
    assert status == 502
    assert detail == "Invalid Render API key"


def test_map_api_error_404_is_service_not_found():
    """AC7: 404 maps to 502 with the service-not-found message."""
    status, detail = ra.map_api_error(404)
    assert status == 502
    assert detail == "Render service not found — check render_service_id"


# ── endpoint tests ───────────────────────────────────────────────────────────


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
def client_ctx():
    """Yield (client, srv, settings_repo) with in-memory DB + commander/perf-coach."""
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

    SessionLocal = sessionmaker(bind=engine)
    settings_repo._session_factory = SessionLocal

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=_PROJECTS):
        with patch.object(srv, "_settings_repo", settings_repo):
            with patch.object(srv.projects_module, "get_project_environments", return_value={}):
                with patch.object(srv, "_derive_project_environments", return_value={}):
                    client = TestClient(srv.app, raise_server_exceptions=False)
                    yield client, srv, settings_repo


def _save_render_config(settings_repo, env_overrides, project="owner/perf-coach"):
    """Persist a project-scoped deploy config override."""
    from services.sprint_manager.deploy_config_schema import DEPLOY_CONFIG_KEY
    settings_repo.set_setting(
        "project", DEPLOY_CONFIG_KEY, env_overrides, project=project
    )


def test_status_route_registered(client_ctx):
    """AC4: a deploy-status endpoint exists on the app."""
    client, srv, repo = client_ctx
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    assert "/api/projects/{slug}/environments/{env}/deploy-status" in paths


def test_render_deploy_calls_render_api_with_bearer(client_ctx):
    """AC1: host=render deploy POSTs to the Render deploys URL with Bearer auth."""
    client, srv, settings_repo = client_ctx
    _save_render_config(settings_repo, {
        "prd": {"host": "render", "render_service_id": "srv-xyz",
                "render_api_key": "rnd_topsecret"},
    })

    calls = []

    def fake_call(method, url, api_key, **kw):
        calls.append((method, url, api_key))
        return 201, {"id": "dep-1", "status": "queued"}

    with patch.object(srv._render_actions, "call_render", side_effect=fake_call):
        resp = client.post("/api/projects/perf-coach/environments/prd/deploy")

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    method, url, api_key = calls[0]
    assert method == "POST"
    assert url == "https://api.render.com/v1/services/srv-xyz/deploys"
    assert api_key == "rnd_topsecret"


def test_render_deploy_never_leaks_api_key(client_ctx):
    """AC3: render_api_key never appears in the response body."""
    client, srv, settings_repo = client_ctx
    _save_render_config(settings_repo, {
        "prd": {"host": "render", "render_service_id": "srv-xyz",
                "render_api_key": "rnd_topsecret"},
    })

    with patch.object(srv._render_actions, "call_render",
                      return_value=(201, {"id": "dep-1", "status": "queued"})):
        resp = client.post("/api/projects/perf-coach/environments/prd/deploy")

    assert resp.status_code == 200
    assert "rnd_topsecret" not in resp.text


def test_render_restart_calls_render_restart_endpoint(client_ctx):
    """AC2: host=render restart POSTs to the Render restart URL."""
    client, srv, settings_repo = client_ctx
    _save_render_config(settings_repo, {
        "prd": {"host": "render", "render_service_id": "srv-xyz",
                "render_api_key": "rnd_topsecret"},
    })

    calls = []

    def fake_call(method, url, api_key, **kw):
        calls.append((method, url))
        return 200, {}

    with patch.object(srv._render_actions, "call_render", side_effect=fake_call):
        resp = client.post("/api/projects/perf-coach/environments/prd/restart")

    assert resp.status_code == 200, resp.text
    assert calls == [("POST", "https://api.render.com/v1/services/srv-xyz/restart")]
    assert "rnd_topsecret" not in resp.text


def test_render_status_endpoint_returns_normalized_status(client_ctx):
    """AC4: status endpoint calls deploys?limit=1 and normalizes the status."""
    client, srv, settings_repo = client_ctx
    _save_render_config(settings_repo, {
        "prd": {"host": "render", "render_service_id": "srv-xyz",
                "render_api_key": "rnd_topsecret"},
    })

    calls = []

    def fake_call(method, url, api_key, **kw):
        calls.append((method, url))
        return 200, [{"deploy": {"id": "dep-1", "status": "build_in_progress"}}]

    with patch.object(srv._render_actions, "call_render", side_effect=fake_call):
        resp = client.get("/api/projects/perf-coach/environments/prd/deploy-status")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "building"
    assert data["status"] in {"queued", "building", "live", "failed"}
    assert calls == [("GET", "https://api.render.com/v1/services/srv-xyz/deploys?limit=1")]
    assert "rnd_topsecret" not in resp.text


def test_render_401_returns_502_invalid_key(client_ctx):
    """AC6: a 401 from Render → HTTP 502 with the invalid-key message."""
    client, srv, settings_repo = client_ctx
    _save_render_config(settings_repo, {
        "prd": {"host": "render", "render_service_id": "srv-xyz",
                "render_api_key": "bad_key"},
    })

    def fake_call(method, url, api_key, **kw):
        raise srv._render_actions.RenderApiError(502, "Invalid Render API key")

    with patch.object(srv._render_actions, "call_render", side_effect=fake_call):
        resp = client.post("/api/projects/perf-coach/environments/prd/deploy")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Invalid Render API key"


def test_render_404_returns_502_service_not_found(client_ctx):
    """AC7: a 404 from Render → HTTP 502 with the service-not-found message."""
    client, srv, settings_repo = client_ctx
    _save_render_config(settings_repo, {
        "prd": {"host": "render", "render_service_id": "srv-bad",
                "render_api_key": "rnd_k"},
    })

    def fake_call(method, url, api_key, **kw):
        raise srv._render_actions.RenderApiError(
            502, "Render service not found — check render_service_id"
        )

    with patch.object(srv._render_actions, "call_render", side_effect=fake_call):
        resp = client.post("/api/projects/perf-coach/environments/prd/deploy")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Render service not found — check render_service_id"


def test_render_deploy_missing_service_id_is_400_before_any_call(client_ctx):
    """AC8: missing render_service_id → 400 and no Render API call."""
    client, srv, settings_repo = client_ctx
    _save_render_config(settings_repo, {
        "prd": {"host": "render", "render_api_key": "rnd_k"},
    })

    call_mock = MagicMock()
    with patch.object(srv._render_actions, "call_render", call_mock):
        resp = client.post("/api/projects/perf-coach/environments/prd/deploy")

    assert resp.status_code == 400
    call_mock.assert_not_called()


def test_render_deploy_missing_api_key_is_400_before_any_call(client_ctx):
    """AC8: missing render_api_key → 400 and no Render API call."""
    client, srv, settings_repo = client_ctx
    _save_render_config(settings_repo, {
        "prd": {"host": "render", "render_service_id": "srv-xyz"},
    })

    call_mock = MagicMock()
    with patch.object(srv._render_actions, "call_render", call_mock):
        resp = client.post("/api/projects/perf-coach/environments/prd/deploy")

    assert resp.status_code == 400
    call_mock.assert_not_called()


def test_non_render_host_deploy_unaffected(client_ctx):
    """AC9: a host=local env still runs the git-pull deploy, no Render call."""
    client, srv, settings_repo = client_ctx
    import subprocess
    _save_render_config(settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/perf-uat",
                "branch": "develop", "launchd_label": "com.perfcoach.uat"},
    })

    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["git", "pull"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="Fast-forward\n", stderr="")
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    render_call = MagicMock()
    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        with patch.object(srv._render_actions, "call_render", render_call):
            resp = client.post("/api/projects/perf-coach/environments/uat/deploy")

    assert resp.status_code == 200, resp.text
    assert "Fast-forward" in resp.json()["pull_output"]
    render_call.assert_not_called()


def test_frontend_polls_status_endpoint():
    """AC5: project.html polls the deploy-status endpoint after a deploy."""
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "deploy-status" in html
    # there must be polling logic (setTimeout/setInterval) tied to status
    assert "_renderDeployPoll" in html or "deployStatusPoll" in html
