"""Tester AC verification for issue #774 — Project Settings: Add Icon & Color Pickers.

Independent tester-authored checks (separate from the coder's
test_774__icon_color_pickers.py). The feature is a frontend-only change to
apps/dashboard/static/project.html plus the existing project-settings API for
persistence. Because the dashboard serves static HTML from disk per request and
this feature lives on a branch not yet deployed to the UAT server, the picker UI
is verified as an on-disk static-content contract and the persistence criteria
are exercised through the in-process settings API (the #773 reference test uses
the same TestClient pattern).

One function per acceptance criterion (Risk: MEDIUM → up to 2 tests per AC).

AC coverage:
  AC1 — Icon field is a rendered-icon grid/dropdown picker (not raw text input)
  AC2 — Color field is a swatch / curated-palette picker (not free-text input)
  AC3 — Selected icon & color are previewed live in the settings panel
  AC4 — Saved icon/color persist to project settings JSON and reload correctly
  AC5 — Legacy value matching a valid option is accepted; invalid values warn
  AC6 — Picker is keyboard-accessible and works on light & dark themes
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
    """Markup of the 'Icon & color' settings row only, so picker assertions do
    not accidentally match unrelated icon/color markup in this very large file."""
    start = html.index("Icon &amp; color")
    return html[start:start + 2500]


def _fn_body(html, signature, span=1400):
    idx = html.index(signature)
    return html[idx:idx + span]


# ── In-process settings API fixture (mirrors the #773 / project-settings harness) ──

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
def client():
    engine = _make_engine()
    for mod in ("server", "services.sprint_manager.settings_repo",
                "services.sprint_manager.settings_schema"):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    settings_repo._session_factory = sessionmaker(bind=engine)

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects",
                      return_value=[{"repo": "owner/test-proj"}]):
        with patch.object(srv, "_settings_repo", settings_repo):
            yield TestClient(srv.app, raise_server_exceptions=False)


# ── AC1: Icon field is a rendered-icon grid picker, not a text input ──────────

def test_icon_color_pickers__ac1_icon_is_rendered_icon_grid_picker():
    html = _html()
    region = _identity_region(html)
    # Picker container present, legacy free-text input gone.
    assert 'id="ps-icon-grid"' in region, "icon grid picker container must exist"
    assert 'id="ps-icon" type="text"' not in html, "legacy free-text icon input must be removed"
    # Options render as real Tabler icons, not raw strings.
    body = _fn_body(html, "function _psRenderIconPicker", span=900)
    assert "_PS_ICON_OPTIONS" in html, "a curated icon option list must be defined"
    assert 'class="ti ' in body, "icon options must render as <i class='ti ...'> icons"


# ── AC2: Color field is a swatch / curated-palette picker, not free text ──────

def test_icon_color_pickers__ac2_color_is_curated_swatch_picker():
    html = _html()
    region = _identity_region(html)
    assert 'id="ps-color-grid"' in region, "color swatch picker container must exist"
    assert 'id="ps-color" type="text"' not in html, "legacy free-text color input must be removed"
    body = _fn_body(html, "function _psRenderColorPicker", span=900)
    assert "_PS_COLOR_MAP" in body, "color swatches must be built from the curated _PS_COLOR_MAP palette"


# ── AC3: Live preview of selected icon & color ────────────────────────────────

def test_icon_color_pickers__ac3_live_preview_updates_on_selection():
    html = _html()
    region = _identity_region(html)
    assert 'id="ps-identity-avatar"' in region, "live preview avatar must exist"
    assert 'id="ps-preview-icon"' in region, "preview must contain an icon element"
    assert "function _psUpdateIdentityPreview" in html, "a live preview updater must exist"
    # Both selection handlers must refresh the preview immediately.
    for fn in ("function _psSelectIcon", "function _psSelectColor"):
        assert "_psUpdateIdentityPreview" in _fn_body(html, fn, span=400), \
            f"{fn} must refresh the live preview on selection"


# ── AC4: Saved icon/color persist and reload ──────────────────────────────────

def test_icon_color_pickers__ac4_icon_color_persist_and_reload(client):
    r = client.put("/api/projects/test-proj/settings",
                   json={"icon": "ti-rocket", "color": "blue"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = client.get("/api/projects/test-proj/settings").json()
    assert data["icon"] == "ti-rocket", "icon must persist and reload"
    assert data["color"] == "blue", "color must persist and reload"


def test_icon_color_pickers__ac4_home_payload_reflects_settings_identity(client):
    """GET /api/home must expose icon/color from project settings overrides."""
    client.put("/api/projects/test-proj/settings",
               json={"icon": "ti-terminal-2", "color": "green", "display_name": "Commander X"})
    home = client.get("/api/home").json()
    proj = next(p for p in home["projects"] if p["slug"] == "test-proj")
    assert proj["icon"] == "ti-terminal-2"
    assert proj["color"] == "green"
    assert proj["name"] == "Commander X"


def test_icon_color_pickers__ac4_save_serializes_picker_state():
    # The form must save the picker's selected ids, not stale text-input reads.
    body = _fn_body(html := _html(), "async function projSettingsSave", span=1200)
    assert "icon: _psSelectedIcon" in body, "save must send the selected icon id"
    assert "color: _psSelectedColor" in body, "save must send the selected color id"
    assert "_psSyncIdentityToChrome" in html, "save must refresh header/sidebar identity immediately"
    load = _fn_body(html, "async function projSettingsLoad", span=2000)
    assert "_psApplyIdentity" in load, "load must apply stored icon/color back into the pickers"


# ── AC5: Legacy value accepted if valid; invalid values warn ──────────────────

def test_icon_color_pickers__ac5_valid_legacy_value_roundtrips(client):
    # A legacy named color that is a valid option must persist unchanged (no rejection).
    r = client.put("/api/projects/test-proj/settings", json={"color": "gray"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    assert client.get("/api/projects/test-proj/settings").json()["color"] == "gray"


def test_icon_color_pickers__ac5_invalid_value_shows_validation_message():
    html = _html()
    assert 'id="ps-identity-error"' in _identity_region(html), "validation message element must exist"
    body = _fn_body(html, "function _psApplyIdentity", span=1400)
    assert "_PS_ICON_OPTIONS" in body, "icon value must be validated against the curated options"
    assert "_PS_COLOR_MAP" in body, "color value must be validated against the curated palette"
    assert "ps-identity-error" in body, "invalid legacy values must surface the validation message"


# ── AC6: Keyboard accessible + works on light & dark themes ────────────────────

def test_icon_color_pickers__ac6_keyboard_accessible_radiogroup():
    html = _html()
    region = _identity_region(html)
    # Native radiogroup semantics on the picker containers.
    assert 'role="radiogroup"' in region, "pickers must expose radiogroup semantics"
    for fn in ("function _psRenderIconPicker", "function _psRenderColorPicker"):
        body = _fn_body(html, fn, span=900)
        assert "<button" in body, f"{fn} options must be focusable <button> elements"
        assert "aria-checked" in body, f"{fn} options must expose aria-checked state"
        assert "tabindex" in body, f"{fn} options must manage roving tabindex"


def test_icon_color_pickers__ac6_theme_variables_and_default_state():
    html = _html()
    # Picker styles use theme CSS variables → render correctly in light & dark.
    assert "var(--" in _fn_body(html, ".ps-icon-opt", span=700), \
        "icon picker styles must use theme CSS variables"
    # Sensible default for a project with no prior icon/color (UAT step 6).
    assert "_PS_DEFAULT_ICON" in html and "_PS_DEFAULT_COLOR" in html, \
        "defaults must exist so an unset project shows a sensible placeholder, not a broken state"
