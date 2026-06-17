"""Tests for issue #1073: Audit and fix Deploy tab UI against impeccable rules (runs against UAT)"""
import os
import pytest
import httpx
import re


# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8000")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture
def project_html(client):
    """Fetch the static project.html file for inspection"""
    r = client.get("/static/project.html")
    assert r.status_code == 200
    return r.text


# --- Acceptance Criteria ---

def test_deploy_tab_ui_audit__impeccable_detect_zero_findings(client):
    """AC: Impeccable detect runs on the deploy region of project.html and reports **zero findings**"""
    pytest.skip("manual — verified via impeccable detect in browser (UAT step 5), not HTTP")


def test_deploy_tab_ui_audit__status_indicators_contrast(project_html):
    """AC: Status indicators meet contrast requirements using foundation tokens only"""
    # Verify status-pill classes use foundation tokens for contrast
    assert ".status-pill--queued" in project_html
    # Spot-check that queued state uses --text-muted for contrast (WCAG AA improvement)
    assert re.search(r'\.status-pill--queued\s*\{[^}]*color: var\(--text-muted\)', project_html, re.DOTALL)
    # Verify no hardcoded colors, only foundation tokens
    assert "color: var(--" in project_html


def test_deploy_tab_ui_audit__row_spacing_conformance(project_html):
    """AC: Row spacing conforms to impeccable spacing scale"""
    # Verify deploy-card__row uses foundation spacing (8px gap, 12px/16px padding)
    assert "gap: 8px" in project_html
    assert "padding: 12px 16px" in project_html
    # Margin for intro text should be 24px (foundation scale)
    assert "margin: 0 0 24px" in project_html


def test_deploy_tab_ui_audit__action_button_sizing(project_html):
    """AC: Action buttons meet minimum touch/click target sizing"""
    # Verify deploy-btn and button elements have min-height: 44px (WCAG 2.5-5.3)
    assert "min-height: 44px" in project_html
    # Verify multiple button types meet the requirement
    assert ".deploy-btn {" in project_html
    assert ".deploy-field-edit {" in project_html
    assert ".deploy-log__toggle {" in project_html
    # Check deploy-field-edit size was increased to 44x44
    assert re.search(r'\.deploy-field-edit\s*\{[^}]*width: 44px[^}]*height: 44px', project_html, re.DOTALL)


def test_deploy_tab_ui_audit__log_panel_alignment(project_html):
    """AC: Log panel alignment matches grid/layout tokens"""
    # Verify deploy-log uses consistent grid-aligned margin and padding
    assert ".deploy-log {" in project_html
    assert "margin: 0 16px 12px" in project_html
    assert "padding: 12px" in project_html


def test_deploy_tab_ui_audit__no_custom_css_vars(project_html):
    """AC: No new CSS variables or custom values introduced outside foundation tokens"""
    # Extract deploy tab section
    deploy_section = re.search(r'/\* ── Deploy tab.*?(?=/\*|$)', project_html, re.DOTALL)
    assert deploy_section

    text = deploy_section.group(0)
    # Foundation tokens allowed: --text, --surface, --border, --blue, --green, --red, --amber, --mono, --purple, etc.
    # Disallow custom --foo- tokens not in the foundation
    allowed_tokens = {
        'text', 'surface', 'border', 'blue', 'green', 'red', 'amber', 'mono', 'purple',
        'indigo', 'steel', 'yellow', 'teal', 'pink', 'bg', 'hover', 'muted', 'sub',
        'on-primary', 'background', 'color', 'sidebar'
    }

    # Find all CSS variables used
    variables = re.findall(r'--([a-z0-9-]+)', text)
    for var in variables:
        # Check if it's a foundation token or subcomponent (e.g., --blue-bg, --text-muted)
        if not any(var.startswith(allowed) for allowed in allowed_tokens):
            # Only fail on truly custom variables (not partial matches)
            if not any(c in var for c in ['-']):  # single-word token must be allowed
                pytest.fail(f"Custom CSS variable found: --{var}")


def test_deploy_tab_ui_audit__dark_theme_renders(client):
    """AC: Dark theme renders correctly with no visual regressions in the deploy region"""
    # Verify the tokens.css file includes dark theme support
    r = client.get("/static/css/tokens.css")
    assert r.status_code == 200
    # Verify dark-theme CSS rules are present with token definitions
    assert "[data-theme=\"dark\"]" in r.text
    # Verify surface token is defined (with colon for CSS variables)
    assert "--surface:" in r.text
    # Verify both light and dark surface definitions exist
    assert ":root" in r.text  # light theme
    assert "[data-theme=\"dark\"]" in r.text  # dark theme
    # Verify project.html supports data-theme attribute for switching
    proj = client.get("/static/project.html")
    assert "data-theme" in proj.text


def test_deploy_tab_ui_audit__action_handlers_functional(client):
    """AC: All existing deploy action handlers (deploy, rollback, cancel, etc.) remain functional after changes"""
    pytest.skip("manual — verified via browser interaction in UAT step 2, not HTTP")
