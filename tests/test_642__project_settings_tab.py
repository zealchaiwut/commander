"""Tests for issue #642 — Build Project Settings tab under More.

AC coverage:
  AC1  — Settings item appears under More in the sub-tab bar (HTML structure)
  AC2  — Estimation section renders with "Inherits global defaults" notice when no override
  AC3  — Editing estimation field and saving stores project-level override (global unchanged)
  AC4  — DELETE /api/projects/{slug}/settings clears project override; GET reverts to global
  AC5  — Suggested from sprint history column is disabled/read-only (HTML structure)
  AC6  — Project section: display_name, icon, color, tracked fields exist in schema
  AC7  — Overrides section: tester_test_repo field exists in schema
  AC8  — Agent models row is read-only (no schema field to PUT)
  AC9  — All editable fields persist via PUT /api/projects/{slug}/settings
  AC10 — Project with no overrides returns global values (no save-as-override on load)
  AC11 — Settings tab pane exists in project.html HTML
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
STATIC_DIR = DASHBOARD_DIR / "static"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── Fixtures ─────────────────────────────────────────────────────────────────

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
    """Yield (client, srv, settings_repo, engine) with in-memory DB and projects patched."""
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


# ── AC1: Settings item in More dropdown (HTML structure) ─────────────────────

def test_settings_tab_button_in_more_dropdown():
    """stab-settings button must appear inside the More dropdown in project.html."""
    html = (STATIC_DIR / "project.html").read_text()
    assert 'id="stab-settings"' in html, (
        "project.html must contain a stab-settings button in the More dropdown"
    )
    # The button must be inside the More dropdown div
    more_idx = html.index('id="stab-dropdown-more"')
    settings_btn_idx = html.index('id="stab-settings"')
    assert settings_btn_idx > more_idx, (
        "stab-settings button must appear after the More dropdown opening"
    )


def test_settings_tab_pane_exists():
    """pane-settings div must exist in project.html."""
    html = (STATIC_DIR / "project.html").read_text()
    assert 'id="pane-settings"' in html, (
        "project.html must contain a pane-settings tab pane"
    )


# ── AC2: Estimation section shows global defaults when no override ─────────────

def test_get_project_settings_returns_global_estimation_defaults_when_no_override(client_ctx):
    """GET /api/projects/{slug}/settings with no override returns global estimation defaults."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["estimation_s_minutes"] == 5
    assert data["estimation_m_minutes"] == 15
    assert data["estimation_l_minutes"] == 30
    assert data["estimation_xl_minutes"] == 60
    assert data["estimation_buffer_pct"] == 20
    assert data["estimation_thin_ac_buffer_pct"] == 30


def test_project_settings_has_no_stored_override_after_fresh_get(client_ctx):
    """GET /api/projects/{slug}/settings alone must not create a project-level override row."""
    client, srv, repo, engine = client_ctx
    client.get("/api/projects/test-proj/settings")
    # The scoped project row must not exist — only global defaults served
    override = srv._settings_repo.get_setting_scoped(
        "project", "app_config", project="owner/test-proj"
    )
    assert override == {}, (
        "GET alone must not persist any project override; got: %s" % override
    )


# ── AC3: Editing estimation stores project override; global unchanged ──────────

def test_put_estimation_override_persists(client_ctx):
    """PUT /api/projects/{slug}/settings with estimation field stores project override."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/test-proj/settings",
        json={"estimation_m_minutes": 20},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    get_resp = client.get("/api/projects/test-proj/settings")
    assert get_resp.json()["estimation_m_minutes"] == 20


def test_put_estimation_override_does_not_affect_global(client_ctx):
    """PUT project estimation override must not mutate global settings."""
    client, srv, repo, engine = client_ctx
    client.put("/api/settings", json={"estimation_m_minutes": 15})
    client.put("/api/projects/test-proj/settings", json={"estimation_m_minutes": 25})
    global_resp = client.get("/api/settings")
    assert global_resp.json()["estimation_m_minutes"] == 15, (
        "Global must be unchanged after project override"
    )


# ── AC4: DELETE /api/projects/{slug}/settings clears project override ──────────

def test_delete_project_settings_returns_200(client_ctx):
    """DELETE /api/projects/{slug}/settings must return 200."""
    client, srv, repo, engine = client_ctx
    client.put("/api/projects/test-proj/settings", json={"estimation_m_minutes": 99})
    resp = client.delete("/api/projects/test-proj/settings")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_delete_project_settings_reverts_to_global(client_ctx):
    """After DELETE, GET /api/projects/{slug}/settings must return global values."""
    client, srv, repo, engine = client_ctx
    client.put("/api/settings", json={"estimation_m_minutes": 15})
    client.put("/api/projects/test-proj/settings", json={"estimation_m_minutes": 99})
    client.delete("/api/projects/test-proj/settings")
    resp = client.get("/api/projects/test-proj/settings")
    assert resp.json()["estimation_m_minutes"] == 15, (
        "After DELETE, project must revert to global value"
    )


def test_delete_project_settings_404_unknown_slug(client_ctx):
    """DELETE /api/projects/{slug}/settings returns 404 for unknown slug."""
    client, srv, repo, engine = client_ctx
    resp = client.delete("/api/projects/no-such-project/settings")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


# ── AC5: Suggested from sprint history uses calibration API ───────────────────

def test_suggested_column_wired_to_calibration():
    """Settings estimation table must load suggestions from analytics calibration."""
    html = (STATIC_DIR / "project.html").read_text()
    pane_start = html.index('id="pane-settings"')
    pane_section = html[pane_start:pane_start + 20000]
    assert 'ps-est-suggest-S' in pane_section, "Estimation table must have per-size suggestion cells"
    assert '_psLoadEstSuggestions' in html, "project.html must load calibration suggestions for Settings"
    assert 'applyEstCalibration' in html, "Settings must reuse applyEstCalibration from Analytics"
    assert 'ps-soon' not in pane_section, "SOON placeholder must be removed from Settings estimation table"


# ── AC6: Project section fields exist in schema ───────────────────────────────

def test_display_name_field_in_settings_schema(client_ctx):
    """display_name field must be readable and writable via project settings API."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    data = resp.json()
    assert "display_name" in data, (
        "display_name must appear in GET /api/projects/{slug}/settings response"
    )


def test_icon_field_in_settings_schema(client_ctx):
    """icon field must be readable via project settings API."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    assert "icon" in resp.json(), "icon must appear in settings response"


def test_color_field_in_settings_schema(client_ctx):
    """color field must be readable via project settings API."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    assert "color" in resp.json(), "color must appear in settings response"


def test_tracked_field_in_settings_schema(client_ctx):
    """tracked field must be readable via project settings API."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    assert "tracked" in resp.json(), "tracked must appear in settings response"


# ── AC7: Overrides section: tester_test_repo field exists ──────────────────────

def test_tester_test_repo_field_in_settings_schema(client_ctx):
    """tester_test_repo field must appear in GET /api/projects/{slug}/settings."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    assert "tester_test_repo" in resp.json(), (
        "tester_test_repo must appear in project settings response"
    )


def test_tester_test_repo_can_be_put(client_ctx):
    """tester_test_repo must be writable via PUT /api/projects/{slug}/settings."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/test-proj/settings",
        json={"tester_test_repo": "owner/test-issues"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = client.get("/api/projects/test-proj/settings").json()
    assert data["tester_test_repo"] == "owner/test-issues"


# ── AC8: Agent models row is read-only (schema has no agent_models field) ──────

def test_agent_models_not_a_writable_field(client_ctx):
    """agent_models must NOT be an accepted field (it's a read-only pointer to global)."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/test-proj/settings",
        json={"agent_models": "anything"},
    )
    assert resp.status_code == 400, (
        "agent_models must not be a writable field; expected 400"
    )


def test_global_settings_link_in_settings_pane():
    """Settings pane in project.html must contain a link to Global Settings."""
    html = (STATIC_DIR / "project.html").read_text()
    pane_start = html.index('id="pane-settings"')
    pane_section = html[pane_start:pane_start + 20000]
    # Either 'Global settings' or 'Global Settings' text should be present
    assert 'global' in pane_section.lower() and 'settings' in pane_section.lower(), (
        "Settings pane must contain a link or reference to Global Settings"
    )


# ── AC9: All editable fields persist via PUT ──────────────────────────────────

def test_put_all_project_section_fields(client_ctx):
    """PUT /api/projects/{slug}/settings with display_name, icon, color, tracked — all persist."""
    client, srv, repo, engine = client_ctx
    payload = {
        "display_name": "My Project",
        "icon": "ti-rocket",
        "color": "green",
        "tracked": False,
    }
    resp = client.put("/api/projects/test-proj/settings", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = client.get("/api/projects/test-proj/settings").json()
    assert data["display_name"] == "My Project"
    assert data["icon"] == "ti-rocket"
    assert data["color"] == "green"
    assert data["tracked"] is False


def test_put_overrides_section_fields(client_ctx):
    """PUT /api/projects/{slug}/settings with branch overrides — persist."""
    client, srv, repo, engine = client_ctx
    payload = {
        "default_branch": "main",
        "default_branch_uat": "develop",
        "default_branch_prd": "master",
        "tester_test_repo": "zealchaiwut/test-issues",
    }
    resp = client.put("/api/projects/test-proj/settings", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = client.get("/api/projects/test-proj/settings").json()
    assert data["default_branch"] == "main"
    assert data["default_branch_uat"] == "develop"
    assert data["default_branch_prd"] == "master"
    assert data["tester_test_repo"] == "zealchaiwut/test-issues"


# ── AC10: Project with no overrides shows global values ───────────────────────

def test_project_no_overrides_shows_global_sprint_duration(client_ctx):
    """Project with no override shows global sprint_duration_days default."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    data = resp.json()
    assert data["sprint_duration_days"] == 14, (
        f"Expected global default 14, got {data.get('sprint_duration_days')}"
    )


def test_project_no_overrides_shows_global_default_branch(client_ctx):
    """Project with no override shows global default_branch default."""
    client, srv, repo, engine = client_ctx
    resp = client.get("/api/projects/test-proj/settings")
    data = resp.json()
    assert data["default_branch"] == "develop", (
        f"Expected global default 'develop', got {data.get('default_branch')}"
    )
    assert data["default_branch_uat"] == "develop"
    assert data["default_branch_prd"] == "master"


# ── AC11: Settings tab pane exists (HTML) ─────────────────────────────────────

def test_settings_pane_contains_estimation_section():
    """pane-settings must contain an Estimation section."""
    html = (STATIC_DIR / "project.html").read_text()
    pane_start = html.index('id="pane-settings"')
    pane_section = html[pane_start:pane_start + 20000]
    assert 'estimation' in pane_section.lower() or 'Estimation' in pane_section, (
        "Settings pane must contain an Estimation section"
    )


def test_settings_pane_contains_overrides_section():
    """pane-settings must contain an Overrides section."""
    html = (STATIC_DIR / "project.html").read_text()
    pane_start = html.index('id="pane-settings"')
    pane_section = html[pane_start:pane_start + 20000]
    assert 'overrides' in pane_section.lower() or 'Overrides' in pane_section, (
        "Settings pane must contain an Overrides section"
    )


def test_settings_pane_contains_project_section():
    """pane-settings must contain a Project section."""
    html = (STATIC_DIR / "project.html").read_text()
    pane_start = html.index('id="pane-settings"')
    pane_section = html[pane_start:pane_start + 20000]
    assert 'project' in pane_section.lower(), (
        "Settings pane must contain a Project section"
    )
