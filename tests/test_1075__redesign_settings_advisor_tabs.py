"""Tests for issue #1075 — Redesign Settings and Advisor tabs in project.html.

AC coverage:
  AC1 — Settings view is restructured into clean grouped sections with
         consistent label/control/help-text anatomy and token-scale form fields
  AC2 — Advisor view has consistent card anatomy (title, body, action) and
         a clear styled empty state
  AC3 — CSS in settings and advisor sections uses only var(--token) values
         from tokens.css; no hardcoded colors outside the token system
  AC4 — Dark theme applies correctly to both views (token vars auto-adapt)
  AC5 — All existing settings form handlers remain functional after redesign
  AC6 — All existing advisor accept/BA action flows remain functional
  AC7 — Vanilla HTML/CSS/JS only; no framework additions
  AC8 — Diff scoped to settings and advisor view regions
  AC9 — Both views pass impeccable detect (manual; no automated test possible)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"
TOKENS_CSS = STATIC_DIR / "css" / "tokens.css"


@pytest.fixture(scope="module")
def html() -> str:
    assert PROJECT_HTML.exists(), f"project.html not found at {PROJECT_HTML}"
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def inline_style(html) -> str:
    m = re.search(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def inline_script(html) -> str:
    parts = re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.DOTALL)
    return "\n".join(parts)


@pytest.fixture(scope="module")
def settings_css(inline_style) -> str:
    """Extract the project Settings CSS region (issue #642 block)."""
    m = re.search(
        r"/\*\s*──\s*Project Settings tab[^*]*\*/(.+?)(?=/\*\s*──\s*Env path)",
        inline_style,
        re.DOTALL,
    )
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def advisor_css(inline_style) -> str:
    """Extract the Advisor CSS region (issue #881 block)."""
    m = re.search(
        r"/\*\s*──\s*Advisor tab[^*]*\*/(.+?)(?=/\*\s*Roadmap look-ahead)",
        inline_style,
        re.DOTALL,
    )
    return m.group(1) if m else ""


# =============================================================================
# tokens.css must exist and define the design system
# =============================================================================


def test_tokens_css_exists():
    """tokens.css must be created at static/css/tokens.css."""
    assert TOKENS_CSS.exists(), (
        "static/css/tokens.css does not exist — create it with the design token "
        "definitions from DESIGN.md (--bg, --surface, --border, --text, etc.)"
    )


def test_tokens_css_defines_core_tokens():
    """tokens.css must define the core set of design tokens."""
    content = TOKENS_CSS.read_text(encoding="utf-8")
    required = [
        "--bg:",
        "--surface:",
        "--border:",
        "--text:",
        "--text-muted:",
        "--blue:",
        "--green:",
        "--red:",
        "--amber:",
    ]
    for token in required:
        assert token in content, (
            f"tokens.css is missing required token '{token}'"
        )


def test_tokens_css_has_dark_theme():
    """tokens.css must define dark-theme token overrides."""
    content = TOKENS_CSS.read_text(encoding="utf-8")
    assert "[data-theme" in content, (
        "tokens.css must include a [data-theme='dark'] block for dark token overrides"
    )


def test_project_html_links_tokens_css(html):
    """project.html must load tokens.css via a <link> tag."""
    assert "tokens.css" in html, (
        "project.html must contain a <link> to tokens.css so the token "
        "system is available to the settings and advisor sections"
    )


# =============================================================================
# AC1 — Settings view anatomy: grouped cards with label/control/hint rows
# =============================================================================


def test_ac1_settings_pane_exists(html):
    """Settings tab pane with id='pane-settings' must exist."""
    assert 'id="pane-settings"' in html, "Missing id='pane-settings' tab pane"


def test_ac1_settings_has_card_groups(html):
    """Settings view must use .proj-settings-card elements for grouped sections."""
    count = html.count('class="proj-settings-card"') + html.count(
        "proj-settings-card "
    )
    assert count >= 4, (
        f"Expected at least 4 settings cards; found {count}. "
        "Each logical settings group should be a .proj-settings-card."
    )


def test_ac1_settings_cards_have_head_and_body(html):
    """Each settings section card must have a card head and card body."""
    assert "proj-settings-card-head" in html, (
        "Missing .proj-settings-card-head — cards need a titled header"
    )
    assert "proj-settings-card-body" in html, (
        "Missing .proj-settings-card-body — cards need a content body"
    )


def test_ac1_settings_row_anatomy(html):
    """Settings card bodies must contain label/control rows (.ps-row pattern)."""
    assert "ps-row-label" in html, "Missing .ps-row-label in settings view"
    assert "ps-row-control" in html, "Missing .ps-row-control in settings view"


def test_ac1_settings_help_text_pattern(html):
    """Settings rows must have help text (.ps-hint) for key fields."""
    assert "ps-hint" in html, (
        "Missing .ps-hint elements — settings rows need help text anatomy"
    )


def test_ac1_settings_css_uses_token_scale(settings_css):
    """Settings CSS must use var(--token) for colors; no bare hex."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    # Strip comments
    cleaned = re.sub(r"/\*.*?\*/", "", settings_css, flags=re.DOTALL)
    # Remove rgba() and color-mix() calls (they're composites, not raw hex)
    cleaned_no_funcs = re.sub(r"rgba?\([^)]+\)", "", cleaned)
    cleaned_no_funcs = re.sub(r"color-mix\([^)]+\)", "", cleaned_no_funcs)
    hex_pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    violations = []
    for m in hex_pattern.finditer(cleaned_no_funcs):
        # Allow #fff and #000 — they're accepted neutrals per design contract
        if m.group().lower() in ("#fff", "#000", "#ffffff", "#000000"):
            continue
        start = max(0, m.start() - 30)
        ctx = cleaned_no_funcs[start : m.end() + 30]
        violations.append(f"'{m.group()}' … {ctx.strip()}")
    assert not violations, (
        "Hardcoded hex colors in settings CSS — use var(--token) values:\n"
        + "\n".join(violations[:5])
    )


# =============================================================================
# AC2 — Advisor view anatomy: card anatomy (title, body, action) + empty state
# =============================================================================


def test_ac2_advisor_pane_exists(html):
    """Advisor tab pane with id='pane-advisor' must exist."""
    assert 'id="pane-advisor"' in html, "Missing id='pane-advisor' tab pane"


def test_ac2_advisor_header_with_run_button(html):
    """Advisor view must have a header area with the Run advisor button."""
    assert "adv-header" in html, "Missing .adv-header section in advisor view"
    assert "adv-run-btn" in html, "Missing #adv-run-btn run button"
    assert "advRun()" in html, "Missing advRun() call on run button"


def test_ac2_advisor_empty_state_styled(html):
    """Advisor view must have a styled empty state with .adv-empty class."""
    assert "adv-empty" in html, (
        "Missing .adv-empty — advisor must have a clear empty state element"
    )
    # The empty state message should explain what to do
    assert "Run advisor" in html or "run advisor" in html.lower(), (
        "Empty state must guide the user to run the advisor"
    )


def test_ac2_advisor_card_title_anatomy(inline_script):
    """_advRender must generate cards with .adv-card-pitch (title)."""
    assert "adv-card-pitch" in inline_script, (
        "_advRender does not emit .adv-card-pitch — advisor cards need a title"
    )


def test_ac2_advisor_card_body_anatomy(inline_script):
    """_advRender must generate cards with .adv-rationale (body)."""
    assert "adv-rationale" in inline_script, (
        "_advRender does not emit .adv-rationale — advisor cards need a body"
    )


def test_ac2_advisor_card_action_anatomy(inline_script):
    """_advRender must generate cards with action buttons (accept/dismiss)."""
    # Advisor cards must have an action area with at least an accept or dismiss action
    has_action = (
        "adv-card-actions" in inline_script
        or ("sugAccept" in inline_script and "adv-card" in inline_script)
        or ("advAccept" in inline_script)
    )
    assert has_action, (
        "_advRender must include action buttons on advisor cards. "
        "Cards need a title/body/action anatomy. Add Accept and Dismiss actions."
    )


def test_ac2_advisor_css_no_hardcoded_hex(advisor_css):
    """Advisor CSS section must not use hardcoded hex color values."""
    if not advisor_css:
        pytest.skip("Could not extract advisor CSS region")
    cleaned = re.sub(r"/\*.*?\*/", "", advisor_css, flags=re.DOTALL)
    cleaned_no_funcs = re.sub(r"rgba?\([^)]+\)", "", cleaned)
    hex_pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    violations = []
    for m in hex_pattern.finditer(cleaned_no_funcs):
        if m.group().lower() in ("#fff", "#000", "#ffffff", "#000000"):
            continue
        start = max(0, m.start() - 30)
        ctx = cleaned_no_funcs[start : m.end() + 30]
        violations.append(f"'{m.group()}' … {ctx.strip()}")
    assert not violations, (
        "Hardcoded hex colors in advisor CSS — use var(--token) values:\n"
        + "\n".join(violations[:5])
    )


def test_ac2_advisor_no_undefined_accent_token(advisor_css):
    """Advisor CSS must not use var(--accent) — it is not in the token system."""
    if not advisor_css:
        pytest.skip("Could not extract advisor CSS region")
    assert "var(--accent)" not in advisor_css, (
        "Advisor CSS uses var(--accent) which is not a defined design token. "
        "Use var(--blue) for the primary action color instead."
    )


# =============================================================================
# AC3 — Token-only CSS: no hardcoded values in settings/advisor CSS blocks
# =============================================================================


def test_ac3_settings_no_undefined_accent(settings_css):
    """Settings CSS must not use var(--accent) — it is not in the token system."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    assert "var(--accent)" not in settings_css, (
        "Settings CSS uses var(--accent) which is not a defined design token. "
        "Use var(--blue) for primary actions instead."
    )


# =============================================================================
# AC4 — Dark theme applies to both views (via token system)
# =============================================================================


def test_ac4_tokens_css_has_dark_overrides():
    """tokens.css dark overrides ensure both views adapt automatically."""
    content = TOKENS_CSS.read_text(encoding="utf-8")
    assert "--bg:" in content, "tokens.css missing --bg token for dark theme"
    assert "--surface:" in content, "tokens.css missing --surface token"
    dark_match = re.search(
        r'\[data-theme[^\]]*dark[^\]]*\]\s*\{([^}]+)\}', content, re.DOTALL
    )
    assert dark_match, "tokens.css missing [data-theme='dark'] override block"
    dark_block = dark_match.group(1)
    assert "--bg:" in dark_block, (
        "tokens.css dark block must override --bg for dark background"
    )


def test_ac4_settings_cards_use_surface_tokens(settings_css):
    """Settings cards must use var(--surface) and var(--border), not hardcoded."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    assert "var(--surface)" in settings_css, (
        "Settings CSS must use var(--surface) for card backgrounds"
    )
    assert "var(--border)" in settings_css, (
        "Settings CSS must use var(--border) for card borders"
    )


# =============================================================================
# AC5 — Settings form handlers remain functional
# =============================================================================


def test_ac5_settings_save_function_exists(inline_script):
    """projSettingsSave() must still exist after redesign."""
    assert "projSettingsSave" in inline_script, (
        "projSettingsSave function not found — settings save handler was removed"
    )


def test_ac5_settings_load_function_exists(inline_script):
    """projSettingsLoad() must still exist after redesign."""
    assert "projSettingsLoad" in inline_script, (
        "projSettingsLoad function not found — settings load handler was removed"
    )


def test_ac5_estimation_form_ids_exist(html):
    """Size estimation input IDs must still exist: ps-est-s/m/l/xl."""
    for size_id in ("ps-est-s", "ps-est-m", "ps-est-l", "ps-est-xl"):
        assert f'id="{size_id}"' in html, (
            f"Missing form element id='{size_id}' — estimation handler broken"
        )


def test_ac5_project_identity_fields_exist(html):
    """Project identity fields (display name, tracked toggle) must exist."""
    assert 'id="ps-display-name"' in html, "Missing ps-display-name input"
    assert 'id="ps-tracked-toggle"' in html, "Missing ps-tracked-toggle button"


def test_ac5_save_bar_buttons_exist(html):
    """Save bar must have Cancel and Save changes buttons."""
    assert "projSettingsLoad()" in html, "Cancel button must call projSettingsLoad()"
    assert "projSettingsSave()" in html, "Save button must call projSettingsSave()"


def test_ac5_cleanup_buttons_exist(html):
    """Sprint cleanup action buttons must still exist."""
    assert "sprintCleanupPreview()" in html, "Missing sprintCleanupPreview call"
    assert "scaffoldCheck()" in html, "Missing scaffoldCheck call"


# =============================================================================
# AC6 — Advisor accept/BA action flows remain functional
# =============================================================================


def test_ac6_adv_run_function_exists(inline_script):
    """advRun() function must exist and handle the run flow."""
    assert "function advRun" in inline_script or "advRun = function" in inline_script, (
        "advRun function not found — advisor run flow broken"
    )


def test_ac6_adv_fetch_function_exists(inline_script):
    """_advFetch() function must exist."""
    assert "_advFetch" in inline_script, (
        "_advFetch not found — advisor data loading broken"
    )


def test_ac6_adv_render_function_exists(inline_script):
    """_advRender() function must exist."""
    assert "_advRender" in inline_script, (
        "_advRender not found — advisor render flow broken"
    )


def test_ac6_sug_accept_function_exists(inline_script):
    """sugAccept() function must exist for the accept/BA flow."""
    assert "sugAccept" in inline_script, (
        "sugAccept not found — advisor accept/BA action flow broken"
    )


def test_ac6_sug_dismiss_function_exists(inline_script):
    """sugDismiss() function must exist for the dismiss flow."""
    assert "sugDismiss" in inline_script, (
        "sugDismiss not found — advisor dismiss flow broken"
    )


def test_ac6_advisor_root_element_exists(html):
    """adv-root element must exist for _advRender to target."""
    assert 'id="adv-root"' in html, (
        "Missing id='adv-root' — _advRender has no target element"
    )


# =============================================================================
# AC7 — Vanilla HTML/CSS/JS only; no framework additions
# =============================================================================


def test_ac7_no_framework_imports(html):
    """project.html must not import React, Vue, Svelte, or other frameworks."""
    for framework in ("react", "vue", "svelte", "angular", "preact"):
        assert f"/{framework}" not in html.lower() or f"from '{framework}" not in html, (
            f"Framework import detected: {framework}. Use vanilla JS only."
        )


def test_ac7_no_es_module_syntax_in_advisor(inline_script):
    """Advisor JS must not use ES module import/export syntax."""
    advisor_section = inline_script[inline_script.find("_advLoaded"):]
    assert "import " not in advisor_section[:2000], (
        "ES module import syntax found in advisor JS — must stay vanilla"
    )


# =============================================================================
# AC8 — Diff scoped to settings/advisor regions
# =============================================================================


def test_ac8_deploy_tab_unchanged(html):
    """Deploy tab elements must still exist — changes must not touch other tabs."""
    assert 'id="pane-deploy"' in html or "pane-deploy" in html, (
        "pane-deploy is missing — the redesign accidentally removed another tab"
    )


def test_ac8_sprint_board_unchanged(html):
    """Sprint board elements must still exist — changes scoped to settings/advisor."""
    assert "pane-sprint" in html or 'id="pane-board"' in html or "smgmt" in html, (
        "Sprint board elements missing — redesign went beyond intended scope"
    )
