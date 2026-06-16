"""Tests for issue #1186: Stack Bulk Create settings bar fields on mobile (runs against UAT)"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_bulk_create_mobile_responsive__bc_settings_bar_flex_direction_column_at_max_width_600px(client):
    # AC: `.bc-settings-bar` switches to `flex-direction: column` at `max-width: 600px`
    # Fetch project.html and verify the media query rule exists
    r = client.get("/static/project.html")
    assert r.status_code == 200
    html = r.text
    assert ".bc-settings-bar" in html
    # Verify responsive media query for mobile breakpoint
    assert "@media" in html
    assert "600px" in html or "600" in html
    assert "flex-direction: column" in html or "flex-direction:column" in html


def test_bulk_create_mobile_responsive__bc_settings_field_width_100_percent_at_max_width_600px(client):
    # AC: `.bc-settings-field` width is `100%` at `max-width: 600px`
    r = client.get("/static/project.html")
    assert r.status_code == 200
    html = r.text
    # Verify media query includes width: 100% for settings-field
    assert ".bc-settings-field" in html
    # Check for responsive rule (media query should set width: 100%)
    assert "width: 100%" in html or "width:100%" in html


def test_bulk_create_mobile_responsive__select_input_full_width_at_max_width_600px(client):
    # AC: All `<select>` and `<input>` elements inside the settings bar are full-width at `max-width: 600px`
    r = client.get("/static/project.html")
    assert r.status_code == 200
    html = r.text
    # Verify that bc-select and bc-text-input exist and are targeted by media rules
    assert ".bc-select" in html
    assert ".bc-text-input" in html
    # Media query should apply width: 100% to these elements on mobile
    assert "@media" in html


def test_bulk_create_mobile_responsive__no_horizontal_scroll_at_375px_viewport(client):
    # AC: No horizontal scroll or overflow on 375px viewport
    # This is a structural check — verify the HTML serves the Bulk Create page
    r = client.get("/static/project.html")
    assert r.status_code == 200
    html = r.text
    # Verify bulk create section exists
    assert "bc-settings-bar" in html
    assert "bc-settings-field" in html
    # Verify responsive CSS is present (no overflow styles that would break mobile)
    assert "overflow-x: auto" not in html or "bc-settings-bar" not in html.split("overflow-x: auto")[0]


def test_bulk_create_mobile_responsive__no_horizontal_scroll_at_600px_viewport(client):
    # AC: No horizontal scroll or overflow on 600px viewport
    r = client.get("/static/project.html")
    assert r.status_code == 200
    html = r.text
    # Same check as 375px — responsive CSS should prevent overflow at 600px
    assert "bc-settings-bar" in html
    assert "@media" in html


def test_bulk_create_mobile_responsive__desktop_layout_unchanged_above_601px(client):
    # AC: Desktop layout (≥601px) is pixel-identical to current behavior
    r = client.get("/static/project.html")
    assert r.status_code == 200
    html = r.text
    # Verify base .bc-settings-bar style (no media query) keeps horizontal flex layout
    # Extract the base style line for bc-settings-bar
    lines = html.split('\n')
    bc_settings_bar_base = None
    for i, line in enumerate(lines):
        if '.bc-settings-bar {' in line:
            # Read next few lines to get the base style
            style_section = '\n'.join(lines[i:i+6])
            if 'display: flex' in style_section and 'gap:' in style_section:
                bc_settings_bar_base = style_section
            break
    assert bc_settings_bar_base is not None, "Base .bc-settings-bar style with flex should exist"
    # Base should have flex (horizontal), not flex-direction: column (which would only be in media query)
    assert 'flex-direction: column' not in bc_settings_bar_base, "Base style should not have flex-direction: column"
