"""Tests for issue #641 — Build global settings screen behind header gear icon.

AC coverage:
  AC1  — Gear icon in header navigates to global settings screen (HTML structure)
  AC2  — Screen loads effective global settings from GET /api/settings on mount
  AC3  — Agent Models section: dropdowns for BA, Coder, Tester, Estimator
  AC4  — Saving a model calls PUT /api/settings and persists
  AC5  — Defaults section: sprint_duration_days, default_branch, bulk_create_concurrency, log_level
  AC6  — Saving a default field calls PUT /api/settings and persists
  AC7  — Estimation Defaults section: estimation_*_minutes + buffer fields
  AC8  — Saving estimation defaults persists via PUT /api/settings
  AC9  — Secrets section: presence indicators for github_token and database_url
  AC10 — Secret values never rendered; fields never editable (no raw secrets in GET response)
  AC11 — Project settings panel shows 'configured globally' link instead of model dropdowns
"""

import os
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


@pytest.fixture()
def client_ctx():
    """Yield (client, srv, settings_repo, engine) with in-memory DB."""
    engine = _make_engine()

    for mod in ("server", "services.sprint_manager.settings_repo", "services.sprint_manager.settings_schema"):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    SessionLocal = sessionmaker(bind=engine)
    settings_repo._session_factory = SessionLocal

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=[{"repo": "owner/test-proj"}]):
        with patch.object(srv, "_settings_repo", settings_repo):
            client = TestClient(srv.app, raise_server_exceptions=False)
            yield client, srv, settings_repo, engine


# ── AC2: GET /api/settings returns agent model fields ────────────────────────

def test_get_settings_has_default_model_for_ba(client_ctx):
    """GET /api/settings must include default_model (BA agent model)."""
    client, *_ = client_ctx
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "default_model" in data, f"Expected 'default_model' in response; got: {list(data.keys())}"


def test_get_settings_has_coder_model(client_ctx):
    """GET /api/settings must include coder_model."""
    client, *_ = client_ctx
    resp = client.get("/api/settings")
    data = resp.json()
    assert "coder_model" in data, f"Expected 'coder_model'; got: {list(data.keys())}"


def test_get_settings_has_tester_model(client_ctx):
    """GET /api/settings must include tester_model."""
    client, *_ = client_ctx
    resp = client.get("/api/settings")
    data = resp.json()
    assert "tester_model" in data, f"Expected 'tester_model'; got: {list(data.keys())}"


def test_get_settings_has_estimator_model(client_ctx):
    """GET /api/settings must include estimator_model."""
    client, *_ = client_ctx
    resp = client.get("/api/settings")
    data = resp.json()
    assert "estimator_model" in data, f"Expected 'estimator_model'; got: {list(data.keys())}"


# ── AC3+AC4: Agent model save persists via PUT ────────────────────────────────

def test_put_settings_persists_coder_model(client_ctx):
    """PUT /api/settings with coder_model must persist for subsequent GET."""
    client, *_ = client_ctx
    resp = client.put("/api/settings", json={"coder_model": "claude-opus-4-8"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = client.get("/api/settings").json()
    assert data["coder_model"] == "claude-opus-4-8"


def test_put_settings_persists_tester_model(client_ctx):
    """PUT /api/settings with tester_model must persist."""
    client, *_ = client_ctx
    client.put("/api/settings", json={"tester_model": "claude-haiku-4-5"})
    data = client.get("/api/settings").json()
    assert data["tester_model"] == "claude-haiku-4-5"


# ── AC5: Defaults section fields in GET /api/settings ────────────────────────

def test_get_settings_has_sprint_duration_days(client_ctx):
    """GET /api/settings must include sprint_duration_days with numeric default."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "sprint_duration_days" in data, (
        f"Expected 'sprint_duration_days'; got: {list(data.keys())}"
    )
    assert isinstance(data["sprint_duration_days"], int), (
        f"sprint_duration_days must be int; got {type(data['sprint_duration_days'])}"
    )


def test_get_settings_has_default_branch(client_ctx):
    """GET /api/settings must include default_branch with string default."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "default_branch" in data, f"Expected 'default_branch'; got: {list(data.keys())}"
    assert isinstance(data["default_branch"], str), (
        f"default_branch must be str; got {type(data['default_branch'])}"
    )


def test_get_settings_has_default_branch_uat_and_prd(client_ctx):
    """GET /api/settings must include UAT and PRD default branch fields."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert data.get("default_branch_uat") == "develop"
    assert data.get("default_branch_prd") == "master"


def test_get_settings_has_bulk_create_concurrency(client_ctx):
    """GET /api/settings must include bulk_create_concurrency."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "bulk_create_concurrency" in data, (
        f"Expected 'bulk_create_concurrency'; got: {list(data.keys())}"
    )


def test_get_settings_has_log_level(client_ctx):
    """GET /api/settings must include log_level."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "log_level" in data, f"Expected 'log_level'; got: {list(data.keys())}"


# ── AC6: Saving defaults persists via PUT ────────────────────────────────────

def test_put_settings_persists_sprint_duration_days(client_ctx):
    """PUT /api/settings with sprint_duration_days must persist."""
    client, *_ = client_ctx
    resp = client.put("/api/settings", json={"sprint_duration_days": 14})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = client.get("/api/settings").json()
    assert data["sprint_duration_days"] == 14


def test_put_settings_persists_default_branch(client_ctx):
    """PUT /api/settings with default_branch must persist."""
    client, *_ = client_ctx
    client.put("/api/settings", json={"default_branch": "develop"})
    data = client.get("/api/settings").json()
    assert data["default_branch"] == "develop"


def test_put_settings_persists_default_branch_uat_and_prd(client_ctx):
    """PUT /api/settings with UAT/PRD branch fields must persist."""
    client, *_ = client_ctx
    client.put("/api/settings", json={"default_branch_uat": "feature/uat", "default_branch_prd": "main"})
    data = client.get("/api/settings").json()
    assert data["default_branch_uat"] == "feature/uat"
    assert data["default_branch_prd"] == "main"


def test_put_settings_persists_bulk_create_concurrency(client_ctx):
    """PUT /api/settings with bulk_create_concurrency must persist."""
    client, *_ = client_ctx
    client.put("/api/settings", json={"bulk_create_concurrency": 5})
    data = client.get("/api/settings").json()
    assert data["bulk_create_concurrency"] == 5


def test_put_settings_persists_log_level(client_ctx):
    """PUT /api/settings with log_level must persist."""
    client, *_ = client_ctx
    client.put("/api/settings", json={"log_level": "DEBUG"})
    data = client.get("/api/settings").json()
    assert data["log_level"] == "DEBUG"


# ── AC7: Estimation defaults fields in GET /api/settings ─────────────────────

def test_get_settings_has_estimation_s_minutes(client_ctx):
    """GET /api/settings must include estimation_s_minutes."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "estimation_s_minutes" in data, (
        f"Expected 'estimation_s_minutes'; got: {list(data.keys())}"
    )


def test_get_settings_has_estimation_m_minutes(client_ctx):
    """GET /api/settings must include estimation_m_minutes."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "estimation_m_minutes" in data, (
        f"Expected 'estimation_m_minutes'; got: {list(data.keys())}"
    )


def test_get_settings_has_estimation_l_minutes(client_ctx):
    """GET /api/settings must include estimation_l_minutes."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "estimation_l_minutes" in data, (
        f"Expected 'estimation_l_minutes'; got: {list(data.keys())}"
    )


def test_get_settings_has_estimation_xl_minutes(client_ctx):
    """GET /api/settings must include estimation_xl_minutes."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "estimation_xl_minutes" in data, (
        f"Expected 'estimation_xl_minutes'; got: {list(data.keys())}"
    )


def test_get_settings_has_estimation_buffer_pct(client_ctx):
    """GET /api/settings must include estimation_buffer_pct."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "estimation_buffer_pct" in data, (
        f"Expected 'estimation_buffer_pct'; got: {list(data.keys())}"
    )


# ── AC8: Saving estimation defaults persists via PUT ─────────────────────────

def test_put_settings_persists_estimation_s_minutes(client_ctx):
    """PUT /api/settings with estimation_s_minutes must persist."""
    client, *_ = client_ctx
    resp = client.put("/api/settings", json={"estimation_s_minutes": 7})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = client.get("/api/settings").json()
    assert data["estimation_s_minutes"] == 7


def test_put_settings_persists_estimation_m_minutes(client_ctx):
    """PUT /api/settings with estimation_m_minutes must persist."""
    client, *_ = client_ctx
    client.put("/api/settings", json={"estimation_m_minutes": 120})
    data = client.get("/api/settings").json()
    assert data["estimation_m_minutes"] == 120


def test_put_settings_persists_estimation_buffer_pct(client_ctx):
    """PUT /api/settings with estimation_buffer_pct must persist."""
    client, *_ = client_ctx
    client.put("/api/settings", json={"estimation_buffer_pct": 20})
    data = client.get("/api/settings").json()
    assert data["estimation_buffer_pct"] == 20


# ── AC9: Secrets section presence indicators ─────────────────────────────────

def test_get_settings_has_github_token_set_flag(client_ctx):
    """GET /api/settings must include github_token_set boolean flag."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "github_token_set" in data, (
        f"Expected 'github_token_set'; got: {list(data.keys())}"
    )
    assert isinstance(data["github_token_set"], bool), "github_token_set must be bool"


def test_get_settings_has_database_url_set_flag(client_ctx):
    """GET /api/settings must include database_url_set boolean flag."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "database_url_set" in data, (
        f"Expected 'database_url_set'; got: {list(data.keys())}"
    )
    assert isinstance(data["database_url_set"], bool), "database_url_set must be bool"


def test_database_url_set_reflects_env_var(client_ctx):
    """database_url_set flag must be True when DATABASE_URL env var is set."""
    client, *_ = client_ctx
    with patch.dict(os.environ, {"DATABASE_URL": "postgres://localhost/test"}):
        for mod in ("server", "services.sprint_manager.settings_repo", "services.sprint_manager.settings_schema"):
            sys.modules.pop(mod, None)
        import services.sprint_manager.settings_schema as schema
        stored = {}
        result = schema.build_effective_response(stored)
        assert result["database_url_set"] is True, (
            "database_url_set must be True when DATABASE_URL env var is present"
        )


def test_database_url_set_false_when_env_var_absent(client_ctx):
    """database_url_set flag must be False when DATABASE_URL env var is not set."""
    for mod in ("services.sprint_manager.settings_schema",):
        sys.modules.pop(mod, None)
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    with patch.dict(os.environ, env, clear=True):
        import services.sprint_manager.settings_schema as schema
        stored = {}
        result = schema.build_effective_response(stored)
        assert result["database_url_set"] is False, (
            "database_url_set must be False when DATABASE_URL env var is absent"
        )


# ── AC10: Secret values never in response ────────────────────────────────────

def test_get_settings_no_raw_github_token(client_ctx):
    """GET /api/settings must NOT include raw github_token value."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "github_token" not in data, "Raw 'github_token' must NOT appear in response"


def test_get_settings_no_raw_database_url(client_ctx):
    """GET /api/settings must NOT include raw database_url value."""
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert "database_url" not in data, "Raw 'database_url' must NOT appear in response"


# ── AC11: project.html has 'configured globally' link for agent models ────────

def test_project_settings_panel_has_global_settings_link():
    """Project settings panel must reference global settings for agent models."""
    html_path = DASHBOARD_DIR / "static" / "project.html"
    content = html_path.read_text(encoding="utf-8")
    assert "configured globally" in content or "Global settings" in content, (
        "project.html must mention 'configured globally' or 'Global settings' in project settings panel"
    )


def test_project_html_settings_panel_no_model_dropdowns():
    """Project settings tab must not offer its own agent model dropdowns.

    Model selection lives in the global settings page.
    """
    html_path = DASHBOARD_DIR / "static" / "project.html"
    content = html_path.read_text(encoding="utf-8")
    start = content.find('id="pane-settings"')
    end = content.find('<!-- ── Ticket detail panel', start)
    if start == -1 or end == -1:
        pytest.skip("pane-settings boundaries not found in project.html")
    panel_html = content[start:end]
    assert "sm-model-select" not in panel_html, (
        "project settings tab must not contain sm-model-select (model dropdowns belong in global settings page)"
    )


# ── AC1: Sidebar link opens global settings page ─────────────────────────────

def test_sidebar_has_global_settings_link():
    """Sidebar must have a Global settings link that opens the full page."""
    html_path = DASHBOARD_DIR / "static" / "project.html"
    content = html_path.read_text(encoding="utf-8")
    assert 'id="sb-global-settings-link"' in content, (
        "project.html must have sb-global-settings-link in the sidebar"
    )
    assert "openGlobalSettingsPage" in content, (
        "project.html must wire openGlobalSettingsPage for global settings navigation"
    )
    assert "ti-settings" in content, (
        "project.html must have ti-settings icon for global settings"
    )


def test_global_settings_is_full_page_not_modal():
    """Global settings must be a tab pane page, not a modal overlay."""
    html_path = DASHBOARD_DIR / "static" / "project.html"
    content = html_path.read_text(encoding="utf-8")
    assert 'id="pane-global-settings"' in content, "Global settings must use pane-global-settings tab pane"
    assert 'id="gss-page-body"' in content, "Global settings API cards must render in gss-page-body"
    assert 'id="sm-modal"' not in content, "Global settings modal (sm-modal) must be removed"
    assert 'id="gnotes-panel"' not in content, "Global notes panel must be removed"
    assert 'id="sb-notes-link"' not in content, "Global notes sidebar link must be removed"
    assert 'openNotesPanel' not in content, "Global notes panel handler must be removed"
    assert 'id="settings-panel"' not in content, "Legacy settings-panel overlay must be removed"


def test_global_settings_screen_has_required_sections():
    """Global settings page must contain Agent Models, Defaults, and Secrets sections."""
    html_path = DASHBOARD_DIR / "static" / "project.html"
    content = html_path.read_text(encoding="utf-8")
    assert "Agent Models" in content, "Global settings must have Agent Models section"
    assert "UAT branch" in content, "Global settings Defaults must include UAT branch"
    assert "PRD branch" in content, "Global settings Defaults must include PRD branch"
    assert "Secrets" in content, "Global settings must have Secrets section"
    assert "Settings Sync" in content, "Global settings must have Settings Sync section"
    assert "Sprint duration" not in content.split('gss-card-title">Defaults')[1].split('gss-card-title')[0], (
        "Sprint duration must not appear in global Defaults card"
    )


def test_global_settings_screen_has_save_button():
    """Global settings screen must have a save button that calls the API."""
    html_path = DASHBOARD_DIR / "static" / "project.html"
    content = html_path.read_text(encoding="utf-8")
    assert "gssSave" in content or "_gsSave" in content or "saveGlobalSettings" in content, (
        "Global settings screen must have a save function"
    )


def test_global_settings_loads_from_api():
    """Global settings JS must call GET /api/settings when the page opens."""
    html_path = DASHBOARD_DIR / "static" / "project.html"
    content = html_path.read_text(encoding="utf-8")
    assert "globalSettingsLoad" in content, (
        "project.html must define globalSettingsLoad for the global settings page"
    )
    assert "/api/settings" in content, (
        "project.html must reference /api/settings for the global settings screen"
    )
