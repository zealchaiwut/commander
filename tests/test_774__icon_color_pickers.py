"""Tests for issue #774 — Project Settings: Add Icon & Color Pickers.

Replaces the free-text icon and color inputs in the project settings form with
a visual icon grid picker and a curated color-swatch palette, a live preview of
the chosen identity, and legacy-value validation.

AC coverage:
  AC1 — Icon field is a dropdown/grid picker rendering icons (not a text input)
  AC2 — Color field is a swatch/curated-palette picker (not a free-text input)
  AC3 — Selected icon & color are previewed live in the settings panel
  AC4 — Saved icon/color persist to project settings JSON and reload correctly
  AC5 — Legacy text matching a valid option is accepted; invalid values warn
  AC6 — Picker is keyboard-accessible and theme-agnostic (light & dark)
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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _html():
    return (STATIC_DIR / "project.html").read_text()


def _identity_region(html):
    """Return the markup of the 'Icon & color' settings row only.

    Anchored on the row label so assertions about the picker do not accidentally
    match unrelated icon/color markup elsewhere in the very large file.
    """
    start = html.index("Icon &amp; color")
    return html[start:start + 2500]


# ── Backend fixture (mirrors test_642 project-settings harness) ───────────────

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


# ── AC1: Icon field is a rendered-icon grid picker, not a text input ──────────

def test_icon_field_is_a_grid_picker():
    """The Icon & color row must contain an icon grid picker container."""
    region = _identity_region(_html())
    assert 'id="ps-icon-grid"' in region, (
        "Settings form must render an icon grid picker (#ps-icon-grid)"
    )


def test_icon_field_is_not_a_free_text_input():
    """The legacy free-text icon input must be gone."""
    html = _html()
    assert 'id="ps-icon" type="text"' not in html, (
        "icon must no longer be a free-text input"
    )


def test_icon_options_render_as_icons():
    """The icon picker JS must emit rendered <i class='ti ...'> elements, not raw strings."""
    html = _html()
    assert "_PS_ICON_OPTIONS" in html, "an icon options list must be defined"
    assert "function _psRenderIconPicker" in html, "icon picker render fn must exist"
    render_idx = html.index("function _psRenderIconPicker")
    body = html[render_idx:render_idx + 900]
    assert 'class="ti ' in body, "icon options must be rendered as Tabler <i> icons"


# ── AC2: Color field is a swatch / curated-palette picker, not free text ──────

def test_color_field_is_a_swatch_picker():
    region = _identity_region(_html())
    assert 'id="ps-color-grid"' in region, (
        "Settings form must render a color swatch picker (#ps-color-grid)"
    )


def test_color_field_is_not_a_free_text_input():
    html = _html()
    assert 'id="ps-color" type="text"' not in html, (
        "color must no longer be a free-text input"
    )


def test_color_picker_uses_curated_palette():
    """Color options must come from the curated _PS_COLOR_MAP palette."""
    html = _html()
    assert "function _psRenderColorPicker" in html, "color picker render fn must exist"
    render_idx = html.index("function _psRenderColorPicker")
    body = html[render_idx:render_idx + 900]
    assert "_PS_COLOR_MAP" in body, "color picker must build swatches from _PS_COLOR_MAP"


# ── AC3: Live preview of selected icon & color ────────────────────────────────

def test_live_preview_element_exists():
    region = _identity_region(_html())
    assert 'id="ps-identity-avatar"' in region, "a live identity preview avatar must exist"
    assert 'id="ps-preview-icon"' in region, "preview must contain an icon element"


def test_preview_updates_on_selection():
    """Selecting an icon or color must call the preview updater."""
    html = _html()
    assert "function _psUpdateIdentityPreview" in html, "preview updater fn must exist"
    for fn in ("function _psSelectIcon", "function _psSelectColor"):
        idx = html.index(fn)
        body = html[idx:idx + 400]
        assert "_psUpdateIdentityPreview" in body, (
            f"{fn} must refresh the live preview on selection"
        )


# ── AC4: Saved icon/color persist and reload ──────────────────────────────────

def test_icon_and_color_persist_via_put(client_ctx):
    """PUT icon/color then GET must return the stored values."""
    client, srv, repo, engine = client_ctx
    resp = client.put(
        "/api/projects/test-proj/settings",
        json={"icon": "ti-rocket", "color": "blue"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = client.get("/api/projects/test-proj/settings").json()
    assert data["icon"] == "ti-rocket", "icon must persist and reload"
    assert data["color"] == "blue", "color must persist and reload"


def test_save_sends_selected_picker_values():
    """projSettingsSave must serialize the picker selections, not text-input reads."""
    html = _html()
    idx = html.index("async function projSettingsSave")
    body = html[idx:idx + 1200]
    assert "icon: _psSelectedIcon" in body, "save must use the selected icon state"
    assert "color: _psSelectedColor" in body, "save must use the selected color state"


def test_load_applies_stored_identity():
    """projSettingsLoad must hand the stored icon/color to the identity applier."""
    html = _html()
    idx = html.index("async function projSettingsLoad")
    body = html[idx:idx + 2000]
    assert "_psApplyIdentity" in body, "load must apply the stored icon/color into the pickers"


# ── AC5: Legacy value validation ──────────────────────────────────────────────

def test_validation_message_element_exists():
    region = _identity_region(_html())
    assert 'id="ps-identity-error"' in region, "a validation message element must exist"


def test_legacy_valid_value_accepted_invalid_warns():
    """_psApplyIdentity must accept known options and flag unknown ones with a message."""
    html = _html()
    assert "function _psApplyIdentity" in html, "identity validator fn must exist"
    idx = html.index("function _psApplyIdentity")
    body = html[idx:idx + 1400]
    # Validates against the curated option sets
    assert "_PS_ICON_OPTIONS" in body, "icon value must be validated against the option list"
    assert "_PS_COLOR_MAP" in body, "color value must be validated against the palette"
    # Surfaces a validation message for invalid values
    assert "ps-identity-error" in body, "invalid values must surface the validation message"


def test_named_color_persists_unchanged(client_ctx):
    """A legacy named color that is a valid option round-trips unchanged (no rejection)."""
    client, srv, repo, engine = client_ctx
    resp = client.put("/api/projects/test-proj/settings", json={"color": "gray"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert client.get("/api/projects/test-proj/settings").json()["color"] == "gray"


# ── AC6: Keyboard accessibility + theme-agnostic ──────────────────────────────

def test_picker_options_are_keyboard_focusable_buttons():
    """Icon and color options must be <button> elements (natively keyboard-operable)."""
    html = _html()
    for fn in ("function _psRenderIconPicker", "function _psRenderColorPicker"):
        idx = html.index(fn)
        body = html[idx:idx + 900]
        assert "<button" in body, f"{fn} options must be <button> elements for keyboard access"
        assert "aria-checked" in body, f"{fn} options must expose aria-checked state"


def test_picker_styles_use_theme_variables():
    """Picker option styles must use CSS theme variables so they work in light & dark."""
    html = _html()
    idx = html.index(".ps-icon-opt")
    body = html[idx:idx + 700]
    assert "var(--" in body, "icon picker styles must use theme CSS variables"


# ── AC6 (default / empty state): no crash when no icon/color set ──────────────

def test_default_icon_and_color_defined():
    """A sensible default must exist for projects with no prior icon/color."""
    html = _html()
    assert "_PS_DEFAULT_ICON" in html, "a default icon must be defined"
    assert "_PS_DEFAULT_COLOR" in html, "a default color must be defined"
