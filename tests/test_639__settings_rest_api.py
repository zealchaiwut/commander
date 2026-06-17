"""Tests for issue #639 — Settings REST API with effective read and override write.

AC coverage:
  AC1  — GET /api/settings returns 200 with non-secret fields
  AC2  — GET /api/settings represents secret fields as boolean presence flags
  AC3  — PUT /api/settings persists global override; only supplied keys written
  AC4  — PUT /api/settings rejects unknown key with 400 + descriptive error
  AC5  — PUT /api/settings rejects raw secret value with 422
  AC6  — GET /api/projects/{slug}/settings returns effective settings (project wins)
  AC7  — GET /api/projects/{slug}/settings applies same secret-as-boolean rule
  AC8  — PUT /api/projects/{slug}/settings persists only supplied fields as project override
  AC9  — PUT /api/projects/{slug}/settings rejects unknown keys with 400
  AC10 — PUT /api/projects/{slug}/settings rejects raw secret values with 422
  AC11 — Project-scoped endpoints return 404 when slug doesn't exist
  AC12 — Response schema is consistent (non-secret fields present, secret_set flags present)
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


def _make_engine(db_path: Path | None = None):
    if db_path is None:
        url = "sqlite:///:memory:"
        connect_args = {"check_same_thread": False}
        poolclass = StaticPool
    else:
        url = f"sqlite:///{db_path}"
        connect_args = {"check_same_thread": False}
        poolclass = None
    engine = create_engine(
        url,
        connect_args=connect_args,
        poolclass=poolclass,
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


@pytest.fixture()
def client_ctx(tmp_path, monkeypatch):
    """Yield (client, srv, settings_repo, engine) with in-memory DB and projects patched."""
    engine = _make_engine(tmp_path / "settings.db")

    for mod in (
        "server",
        "settings_repo",
        "settings_schema",
        "services.sprint_manager.settings_repo",
        "services.sprint_manager.settings_schema",
        "routers.settings_service",
        "routers.settings",
    ):
        sys.modules.pop(mod, None)

    import settings_repo as settings_repo

    # Alias before importing server so server + settings_service share one module.
    sys.modules["services.sprint_manager.settings_repo"] = settings_repo
    monkeypatch.setattr(
        settings_repo,
        "_fallback_store_path",
        lambda: tmp_path / "settings_store.json",
    )

    import server as srv

    SessionLocal = sessionmaker(bind=engine)
    settings_repo._session_factory = SessionLocal

    import routers.settings_service as settings_service

    settings_service._settings_repo = settings_repo

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=[
        {"repo": "owner/test-proj"},
        {"repo": "owner/override-proj"},
        {"repo": "owner/fallback-proj"},
    ]):
        with patch.object(srv, "_settings_repo", settings_repo):
            client = TestClient(srv.app, raise_server_exceptions=False)
            yield client, srv, settings_repo, engine


# ── AC1: GET /api/settings returns 200 with non-secret fields ────────────────

def test_get_global_settings_returns_200(client_ctx):
    """GET /api/settings must return 200."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/settings")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_get_global_settings_has_non_secret_fields(client_ctx):
    """GET /api/settings must include non-secret fields like default_model."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/settings")
    data = resp.json()
    assert "default_model" in data, f"Expected 'default_model' in response; got: {list(data.keys())}"
    assert "estimation_default_points" in data, f"Expected 'estimation_default_points'; got: {list(data.keys())}"


# ── AC2: GET /api/settings secrets as boolean presence flags ─────────────────

def test_get_global_settings_secrets_as_boolean_flags(client_ctx):
    """GET /api/settings must not include raw secret values; secrets appear as _set flags."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/settings")
    data = resp.json()
    assert "github_token" not in data, "Raw 'github_token' must NOT appear in response"
    assert "github_token_set" in data, "Expected 'github_token_set' presence flag in response"
    assert isinstance(data["github_token_set"], bool), "github_token_set must be a boolean"


def test_get_global_settings_secret_flag_false_when_unset(client_ctx):
    """Secret flag is False when no value stored."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/settings")
    data = resp.json()
    assert data["github_token_set"] is False, "Unset secret must show False flag"


# ── AC3: PUT /api/settings persists global override ──────────────────────────

def test_put_global_settings_returns_200(client_ctx):
    """PUT /api/settings with valid body must return 200."""
    client, srv, repo, engine = client_ctx
    resp = client.put("/api/settings", json={"default_model": "claude-3-5-sonnet"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_put_global_settings_persists_supplied_key(client_ctx):
    """PUT /api/settings value visible in subsequent GET."""
    client, srv, repo, engine = client_ctx
    client.put("/api/settings", json={"default_model": "claude-test-model"})
    resp = client.get("/api/settings")
    data = resp.json()
    assert data["default_model"] == "claude-test-model", (
        f"Expected 'claude-test-model', got {data.get('default_model')}"
    )


def test_put_global_settings_only_updates_supplied_keys(client_ctx):
    """PUT /api/settings must not overwrite keys not in request body."""
    client, srv, repo, engine = client_ctx
    client.put("/api/settings", json={"default_model": "model-A"})
    client.put("/api/settings", json={"estimation_default_points": 5})
    resp = client.get("/api/settings")
    data = resp.json()
    assert data["default_model"] == "model-A", "Previously written key must be preserved"
    assert data["estimation_default_points"] == 5, "Newly written key must be present"


# ── AC4: PUT /api/settings rejects unknown key with 400 ──────────────────────

def test_put_global_settings_rejects_unknown_key(client_ctx):
    """PUT /api/settings with an unknown key must return 400."""
    client, srv, repo, engine = client_ctx
    resp = client.put("/api/settings", json={"nonexistent_key": "value"})
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_put_global_settings_400_names_offending_key(client_ctx):
    """400 response body must name the offending unknown key."""
    client, srv, repo, engine = client_ctx
    resp = client.put("/api/settings", json={"nonexistent_key": "value"})
    body = resp.text
    assert "nonexistent_key" in body, f"Error body must name the key; got: {body}"


# ── AC5: PUT /api/settings rejects raw secret value with 422 ─────────────────

def test_put_global_settings_rejects_raw_secret(client_ctx):
    """PUT /api/settings with a secret field must return 422."""
    client, srv, repo, engine = client_ctx
    resp = client.put("/api/settings", json={"github_token": "ghp_abc123"})
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


def test_put_global_settings_422_mentions_secrets(client_ctx):
    """422 response body must indicate secrets cannot be written via this endpoint."""
    client, srv, repo, engine = client_ctx
    resp = client.put("/api/settings", json={"github_token": "ghp_abc123"})
    body = resp.text
    assert "secret" in body.lower() or "github_token" in body, (
        f"422 body must mention secret or key name; got: {body}"
    )


# ── AC6: GET /api/projects/{slug}/settings returns effective merged settings ──

def test_get_project_settings_returns_200(client_ctx):
    """GET /api/projects/{slug}/settings for existing project returns 200."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_get_project_settings_project_override_wins(client_ctx):
    """Project override must take precedence over global value."""
    client, srv, repo, engine = client_ctx
    client.put("/api/settings", json={"default_model": "global-model"})
    client.put("/api/projects/override-proj/settings", json={"default_model": "project-model"})
    resp = client.get("/api/projects/override-proj/settings")
    data = resp.json()
    assert data["default_model"] == "project-model", (
        f"Project override must win; got {data.get('default_model')}"
    )


def test_get_project_settings_falls_back_to_global(client_ctx):
    """Non-overridden fields must reflect global value in project settings."""
    client, srv, repo, engine = client_ctx
    client.put("/api/settings", json={"default_model": "global-model"})
    resp = client.get("/api/projects/fallback-proj/settings")
    data = resp.json()
    assert data["default_model"] == "global-model", (
        f"Non-overridden field must reflect global; got {data.get('default_model')}"
    )


# ── AC7: GET /api/projects/{slug}/settings applies same secret rule ───────────

def test_get_project_settings_secrets_as_boolean_flags(client_ctx):
    """Project settings must not expose raw secret values."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    data = resp.json()
    assert "github_token" not in data, "Raw secret must not appear in project settings"
    assert "github_token_set" in data, "Secret boolean flag must appear in project settings"


# ── AC8: PUT /api/projects/{slug}/settings persists project override ──────────

def test_put_project_settings_returns_200(client_ctx):
    """PUT /api/projects/{slug}/settings with valid body returns 200."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/test-proj/settings",
        json={"estimation_default_points": 3},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_put_project_settings_persists_and_visible_in_get(client_ctx):
    """PUT project override visible in subsequent GET /api/projects/{slug}/settings."""
    client, srv, repo, engine = client_ctx
    client.put(
        "/api/projects/test-proj/settings",
        json={"estimation_default_points": 7},
    )
    resp = client.get("/api/projects/test-proj/settings")
    data = resp.json()
    assert data["estimation_default_points"] == 7, (
        f"Expected 7, got {data.get('estimation_default_points')}"
    )


def test_put_project_settings_does_not_affect_global(client_ctx):
    """PUT /api/projects/{slug}/settings must not change global settings."""
    client, srv, repo, engine = client_ctx
    client.put("/api/settings", json={"estimation_default_points": 3})
    client.put(
        "/api/projects/test-proj/settings",
        json={"estimation_default_points": 99},
    )
    resp = client.get("/api/settings")
    data = resp.json()
    assert data["estimation_default_points"] == 3, (
        f"Global must be unaffected; got {data.get('estimation_default_points')}"
    )


# ── AC9: PUT /api/projects/{slug}/settings rejects unknown keys with 400 ──────

def test_put_project_settings_rejects_unknown_key(client_ctx):
    """PUT /api/projects/{slug}/settings with unknown key returns 400."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/test-proj/settings",
        json={"made_up_field": True},
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_put_project_settings_400_names_offending_key(client_ctx):
    """400 error body must name the offending unknown key."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/test-proj/settings",
        json={"made_up_field": True},
    )
    body = resp.text
    assert "made_up_field" in body, f"Error body must name the key; got: {body}"


# ── AC10: PUT /api/projects/{slug}/settings rejects raw secrets with 422 ──────

def test_put_project_settings_rejects_raw_secret(client_ctx):
    """PUT /api/projects/{slug}/settings with a secret field returns 422."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/test-proj/settings",
        json={"github_token": "ghp_xyz"},
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


# ── AC11: Project-scoped endpoints return 404 when slug doesn't exist ─────────

def test_get_project_settings_404_for_unknown_slug(client_ctx):
    """GET /api/projects/{slug}/settings returns 404 when slug not found."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/does-not-exist/settings")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


def test_put_project_settings_404_for_unknown_slug(client_ctx):
    """PUT /api/projects/{slug}/settings returns 404 when slug not found."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/does-not-exist/settings",
        json={"default_model": "x"},
    )
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


# ── AC12: Response schema consistency ─────────────────────────────────────────

def test_global_and_project_settings_have_same_schema(client_ctx):
    """GET /api/settings and GET /api/projects/{slug}/settings return same set of keys."""
    client, srv, repo, engine = client_ctx
    global_resp = client.get("/api/settings")
    proj_resp = client.get("/api/projects/test-proj/settings")
    assert global_resp.status_code == 200
    assert proj_resp.status_code == 200
    global_keys = set(global_resp.json().keys())
    proj_keys = set(proj_resp.json().keys())
    assert global_keys == proj_keys, (
        f"Global and project settings must have same keys.\n"
        f"Global only: {global_keys - proj_keys}\n"
        f"Project only: {proj_keys - global_keys}"
    )
