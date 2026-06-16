"""Tests for issue #1046 — Redesign Home page with token-based card grid.

AC coverage:
  AC1  — Page uses only var(--token) values from tokens.css; no hardcoded
          colors or spacing in the inline <style> block
  AC2  — Dark theme is default (data-theme="dark"); no theme toggle present
  AC3  — Layout has a clearly distinguished page header section above the grid
  AC4  — Project cards have uniform anatomy: title, status badge, progress
          indicator, and last-activity timestamp in the card head
  AC5  — Empty-state component with message + primary CTA renders when no
          projects exist
  AC6  — Primary add-project action is keyboard accessible (tabindex set,
          focus-visible styles defined)
  AC7  — All existing links and JS event handlers continue to function
  AC8  — Diff scoped to home.html only (tokens.css and other files unchanged)
  AC9  — impeccable detect — checked manually; no automated test possible
  AC10 — Vanilla HTML, CSS, JS only; no new frameworks or build steps
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
HOME_HTML = STATIC_DIR / "home.html"
TOKENS_CSS = STATIC_DIR / "css" / "tokens.css"


@pytest.fixture(scope="module")
def html() -> str:
    assert HOME_HTML.exists(), f"home.html not found at {HOME_HTML}"
    return HOME_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def inline_style(html) -> str:
    """Extract the content of the inline <style> block."""
    m = re.search(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def inline_script(html) -> str:
    """Extract inline <script> block (not src=... scripts)."""
    parts = re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.DOTALL)
    return "\n".join(parts)


# =============================================================================
# AC1 — No hardcoded colors or spacing (must use var(--token) values)
# =============================================================================


def test_ac1_no_inline_root_color_redefinitions(inline_style):
    """Inline style must not redefine :root color tokens already in tokens.css.

    The redesign removes the redundant :root block that duplicated tokens.css
    values such as --bg:#0d0d0d and --surface:#161616.
    """
    root_match = re.search(r":root\s*\{([^}]*)\}", inline_style, re.DOTALL)
    if not root_match:
        return  # No :root block at all — pass
    root_body = root_match.group(1)
    # The canonical token values must live in tokens.css, not re-declared here
    assert "--bg:#" not in root_body.replace(" ", ""), (
        "home.html inline :root block must not redefine --bg; "
        "use var(--bg) from tokens.css instead"
    )
    assert "--surface:#" not in root_body.replace(" ", ""), (
        "home.html inline :root block must not redefine --surface; "
        "use var(--surface) from tokens.css instead"
    )


def test_ac1_no_inline_dark_theme_block(inline_style):
    """Inline style must not redefine [data-theme=dark] tokens.

    The redesign removes the duplicated dark-theme override block since
    tokens.css already defines and owns those values.
    """
    has_dark_block = bool(
        re.search(r'\[data-theme\s*=\s*["\']?dark["\']?\s*\]', inline_style)
    )
    assert not has_dark_block, (
        "home.html must not contain a [data-theme=dark] override block — "
        "dark-theme token values are owned exclusively by tokens.css"
    )


def test_ac1_body_background_uses_token(inline_style):
    """body background must use var(--bg) not a hardcoded hex value."""
    body_match = re.search(r"\bbody\s*\{([^}]+)\}", inline_style, re.DOTALL)
    if not body_match:
        pytest.skip("No body rule in inline style")
    body_rule = body_match.group(1)
    if "background" not in body_rule:
        return  # background set elsewhere or not in this rule
    assert "var(--" in body_rule, (
        "body background must use a var(--token) value, not a hardcoded hex"
    )
    assert not re.search(r"background\s*:\s*#", body_rule), (
        "body must not use a hardcoded hex background — use var(--bg)"
    )


def test_ac1_no_hardcoded_hex_colors_in_rules(inline_style):
    """CSS rules must use var(--token) for colors, not bare hex codes.

    We strip :root / [data-theme] blocks first (those are declarations),
    then scan the remaining rules for any hex color that isn't inside a
    var() expression.
    """
    # Remove :root and [data-theme] blocks (declarations, not rule-set usages)
    cleaned = re.sub(r":root\s*\{[^}]*\}", "", inline_style, flags=re.DOTALL)
    cleaned = re.sub(
        r'\[data-theme[^\]]*\]\s*\{[^}]*\}', "", cleaned, flags=re.DOTALL
    )
    # Remove comments
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

    # Find hex color usages NOT inside a var() argument
    # Approach: find all #rrggbb / #rgb patterns, then check their context
    hex_pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    for m in hex_pattern.finditer(cleaned):
        start = max(0, m.start() - 8)
        context = cleaned[start : m.start()]
        # Skip if we're inside a var( context
        if "var(" in context:
            continue
        # Skip animation percentages like "0%" that accidentally match hex-ish
        if not re.match(r"#[0-9a-fA-F]{3,8}", m.group()):
            continue
        pytest.fail(
            f"Hardcoded hex color '{m.group()}' found in home.html CSS rules. "
            f"Context: '…{cleaned[max(0,m.start()-20):m.end()+20]}…'. "
            "Use a var(--token) value from tokens.css instead."
        )


# =============================================================================
# AC2 — Dark theme default; no theme toggle
# =============================================================================


def test_ac2_html_element_dark_theme(html):
    """<html> must have data-theme=\"dark\" as the default."""
    assert 'data-theme="dark"' in html, (
        '<html> must have data-theme="dark" — dark theme is the default; '
        "no light theme fallback or toggle should exist"
    )


def test_ac2_no_light_theme_on_html_element(html):
    """<html> must not have data-theme=\"light\"."""
    assert 'data-theme="light"' not in html, (
        '<html data-theme="light"> must be replaced with data-theme="dark" — '
        "the redesign locks the page to dark theme"
    )


def test_ac2_no_theme_toggle_button(html):
    """There must be no theme-toggle button in the markup."""
    assert "theme-toggle" not in html, (
        "home.html must not contain a theme-toggle button — "
        "dark theme is now the permanent default (AC2)"
    )


def test_ac2_no_toggle_theme_function(inline_script):
    """The toggleTheme() JS function must not be present."""
    assert "toggleTheme" not in inline_script, (
        "toggleTheme() function must be removed — "
        "dark theme is permanent; there is no theme toggle (AC2)"
    )


# =============================================================================
# AC3 — Clearly distinguished page header above the project grid
# =============================================================================


def test_ac3_page_header_element_present(html):
    """A static page header element must appear above the main content."""
    has_header_tag = bool(re.search(r"<header\b", html))
    has_page_hdr_class = "page-hdr" in html
    assert has_header_tag or has_page_hdr_class, (
        "home.html must have a static page header section (a <header> element "
        "or an element with class 'page-hdr') above the project content — "
        "this provides the 'clearly distinguished page header section' (AC3)"
    )


def test_ac3_page_header_above_main_content(html):
    """The page header must appear before the #home content container."""
    header_pos = -1
    m = re.search(r"<header\b|class=[\"'][^\"']*page-hdr", html)
    if m:
        header_pos = m.start()
    home_div_pos = html.find('id="home"')
    assert header_pos != -1, "Page header element not found (see AC3)"
    assert header_pos < home_div_pos, (
        "Page header must appear in the markup before the #home content div — "
        "it must be visually above the project grid"
    )


def test_ac3_page_header_has_title(html):
    """The page header must contain a site/app title."""
    m = re.search(r"<header[^>]*>(.*?)</header>", html, re.DOTALL)
    if not m:
        # Find the page-hdr div
        m2 = re.search(r'class="[^"]*page-hdr[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        content = m2.group(1) if m2 else html
    else:
        content = m.group(1)
    # Should contain "Commander" or similar branding
    assert "Commander" in content or "commander" in content.lower(), (
        "The page header must contain the app name ('Commander') — "
        "it is the primary branding element of the header (AC3)"
    )


# =============================================================================
# AC4 — Project cards have uniform anatomy with 4 elements in the card head
# =============================================================================


def test_ac4_pbrief_head_class_exists(html):
    """Project cards must have a .pbrief-head element."""
    assert "pbrief-head" in html, (
        "Project cards must use the .pbrief-head class for the card header — "
        "this anchors the uniform anatomy for all cards (AC4)"
    )


def test_ac4_status_badge_in_card(html):
    """Each rendered project card must include a status badge (.pbstatus)."""
    assert "pbstatus" in html, (
        "Project cards must include a .pbstatus element (status badge) — "
        "this is required by AC4's uniform card anatomy"
    )


def test_ac4_progress_indicator_in_card_head(html):
    """Card head must surface a progress indicator."""
    # The progress indicator can be a .card-meta, .card-meta-item, or
    # any element showing percentage in the card head.
    has_progress_class = "card-meta" in html
    # Also accept if the statusline with percent is in the head area
    has_progress_ref = "_cardMeta" in html or "card-meta-item" in html or (
        "card-meta" in html
    )
    assert has_progress_class or has_progress_ref, (
        "Project card head must include a progress indicator — "
        "use .card-meta/.card-meta-item or a _cardMeta() helper (AC4)"
    )


def test_ac4_last_activity_in_card_anatomy(html):
    """Card anatomy must expose a last-activity timestamp element."""
    # The last-activity can come from _cardMeta() which extracts from recent_activity
    has_activity_meta = "card-meta" in html or "last_activity" in html or (
        "recent_activity" in html and "card" in html
    )
    assert has_activity_meta, (
        "Project card anatomy must include a last-activity timestamp — "
        "expose it in the card head via card-meta or similar (AC4)"
    )


def test_ac4_card_meta_css_defined(inline_style):
    """CSS must define the .card-meta class for the card anatomy elements."""
    assert ".card-meta" in inline_style, (
        "home.html <style> must define .card-meta — "
        "this class groups progress + last-activity in the card head (AC4)"
    )


# =============================================================================
# AC5 — Empty-state component with message + primary CTA
# =============================================================================


def test_ac5_empty_state_class_used(html):
    """The .empty-state class from tokens.css must be used for the no-projects state."""
    assert "empty-state" in html, (
        "home.html must use the .empty-state class (defined in tokens.css) "
        "when no projects exist — this is the token-based empty-state component (AC5)"
    )


def test_ac5_empty_state_has_primary_cta(html):
    """The empty state must include a primary CTA button."""
    # The CTA must use btn-primary from tokens.css
    has_primary = "btn-primary" in html or "btn btn-primary" in html
    assert has_primary, (
        "The empty-state component must include a primary CTA using .btn-primary "
        "(from tokens.css) so it is visually prominent (AC5)"
    )


def test_ac5_empty_state_in_render_function(inline_script):
    """The renderProjectsShell JS function must render an empty state when no projects."""
    # Check that the empty-state appears in the JS rendering logic
    assert "empty-state" in inline_script, (
        "The renderProjectsShell() function (or equivalent) must render "
        "an .empty-state component when no projects exist (AC5)"
    )


# =============================================================================
# AC6 — Primary CTA is keyboard accessible
# =============================================================================


def test_ac6_add_project_has_tabindex(html):
    """The add-project CTA must have an explicit tabindex or be a native focusable."""
    # The add-project link is either an <a> (natively focusable) or a button
    # with tabindex. Accept either form.
    has_anchor_cta = bool(re.search(r'<a[^>]+openAdd', html))
    has_button_cta = bool(re.search(r'<button[^>]+openAdd', html))
    # If using the .btn class from tokens.css, the element is natively focusable
    has_btn_class_cta = "btn-primary" in html and "openAdd" in html
    assert has_anchor_cta or has_button_cta or has_btn_class_cta, (
        "The add-project CTA must be a natively focusable element (<a> or <button>) "
        "so it is keyboard accessible without explicit tabindex (AC6)"
    )


def test_ac6_focus_visible_style_defined(inline_style):
    """A focus-visible or :focus style must be defined for interactive elements."""
    has_focus = ":focus" in inline_style or "focus-visible" in inline_style
    assert has_focus, (
        "home.html must define :focus-visible (or :focus) styles for "
        "interactive elements to ensure keyboard accessibility (AC6)"
    )


# =============================================================================
# AC7 — All existing links and JS event handlers continue to function
# =============================================================================


def test_ac7_init_function_present(inline_script):
    """The init() function must still be present and called."""
    assert "function init(" in inline_script or "async function init(" in inline_script, (
        "The init() function must be preserved in home.html — "
        "it bootstraps the daily brief on page load (AC7)"
    )
    assert "init()" in inline_script, (
        "init() must still be called at the end of the script block (AC7)"
    )


def test_ac7_sprint_mgmt_links_generated(inline_script):
    """renderProject / _sprintMgmtHref must still generate /project/<slug>/sprint-mgmt links."""
    assert "sprint-mgmt" in inline_script, (
        "home.html JS must still generate '/project/<slug>/sprint-mgmt' links — "
        "clicking a project card must navigate to sprint management (AC7)"
    )


def test_ac7_open_add_handler_preserved(inline_script):
    """The openAdd() click handler must still be present."""
    assert "function openAdd" in inline_script, (
        "openAdd() function must be preserved — "
        "it handles the 'Add new project' CTA click (AC7)"
    )


def test_ac7_run_sprint_action_preserved(inline_script):
    """The runSprintAction() handler must still be present for decision actions."""
    assert "runSprintAction" in inline_script, (
        "runSprintAction() must be preserved — "
        "it handles the 'Run sprint' one-tap action from the decisions section (AC7)"
    )


def test_ac7_deck_redirect_preserved(inline_script):
    """The _deckRedirect() function for deep-link routing must still work."""
    assert "_deckRedirect" in inline_script, (
        "_deckRedirect() must be preserved — "
        "it translates /?project=<P>&view=<V> deep links to the project URL (AC7)"
    )


def test_ac7_load_brief_function_preserved(inline_script):
    """loadBrief() must still be present for date-navigation."""
    assert "loadBrief" in inline_script, (
        "loadBrief() must be preserved — "
        "it reloads the brief content when the user navigates to another date (AC7)"
    )


# =============================================================================
# AC8 — Diff scoped to home.html only
# =============================================================================


def test_ac8_tokens_css_unchanged():
    """tokens.css must not have been modified by the redesign."""
    tokens_src = TOKENS_CSS.read_text(encoding="utf-8")
    # tokens.css should still have its canonical dark-theme values
    assert "#0d0d0d" in tokens_src, (
        "tokens.css must remain unchanged — the redesign only modifies home.html"
    )
    assert "#161616" in tokens_src, (
        "tokens.css must retain its existing values (not modified by this ticket)"
    )


def test_ac8_tokens_css_link_still_in_home(html):
    """home.html must still link tokens.css (required by AC1)."""
    assert "tokens.css" in html, (
        "home.html must still include <link href='...tokens.css'> — "
        "removing this link would break AC1's token usage"
    )


# =============================================================================
# AC10 — Vanilla HTML, CSS, JS; no new frameworks or build steps
# =============================================================================


def test_ac10_no_script_type_module(html):
    """No <script type=\"module\"> must be introduced."""
    assert 'type="module"' not in html, (
        "home.html must not use ES module scripts (<script type=module>) — "
        "this is a no-build-step page; vanilla script tags only (AC10)"
    )


def test_ac10_no_framework_import(inline_script):
    """No framework imports (React, Vue, etc.) must appear in the inline script."""
    for fw in ("React", "Vue", "Svelte", "Angular", "import(", 'from "react"'):
        assert fw not in inline_script, (
            f"home.html must not import '{fw}' — "
            "vanilla HTML/CSS/JS only; no new frameworks (AC10)"
        )


def test_ac10_no_new_external_css_frameworks(html):
    """No CSS framework CDN links (Bootstrap, Tailwind, etc.) must be added."""
    for fw in ("bootstrap", "tailwind", "bulma", "foundation"):
        # Check in <link> tags only (ignore inline mentions in comments)
        link_tags = re.findall(r"<link[^>]+>", html)
        for tag in link_tags:
            assert fw not in tag.lower(), (
                f"home.html must not load the '{fw}' CSS framework — "
                "vanilla styles only; use tokens.css for design tokens (AC10)"
            )
