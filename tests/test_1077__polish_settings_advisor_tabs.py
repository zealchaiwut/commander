"""
Tests for issue #1077 — Polish Settings and Advisor tabs UI/UX.

All tests parse the static HTML file; no server required.
"""
import re
from pathlib import Path

HTML_PATH = Path(__file__).parent.parent / 'apps/dashboard/static/project.html'


def _html():
    return HTML_PATH.read_text()


# ── AC1: All form inputs and controls in Settings tab have visible focus rings ──

def test_ac1_ps_btn_has_focus_visible():
    """ps-btn must have a :focus-visible CSS rule."""
    html = _html()
    assert '.ps-btn:focus-visible' in html, \
        ".ps-btn:focus-visible rule missing — buttons in Settings tab have no keyboard focus ring"


def test_ac1_ps_toggle_has_focus_visible():
    """ps-toggle (toggle switch) must have a :focus-visible CSS rule."""
    html = _html()
    assert '.ps-toggle:focus-visible' in html, \
        ".ps-toggle:focus-visible rule missing — toggle switch has no keyboard focus ring"


def test_ac1_ps_inp_has_focus_ring():
    """ps-inp must have a :focus or :focus-visible CSS rule with visible indicator."""
    html = _html()
    assert re.search(r'\.ps-inp:(focus|focus-visible)\s*\{', html), \
        "ps-inp is missing a :focus/:focus-visible rule"


# ── AC2: Hover states on inputs/controls are visually distinct ──

def test_ac2_ps_inp_has_hover_state():
    """ps-inp must have a :hover CSS rule distinct from the default state."""
    html = _html()
    assert '.ps-inp:hover' in html, \
        ".ps-inp:hover rule missing — input fields have no hover state"


# ── AC3: Validation states clearly styled; communicate without color alone ──

def test_ac3_invalid_css_class_exists():
    """.ps-inp.is-invalid CSS class must be defined."""
    html = _html()
    assert '.ps-inp.is-invalid' in html, \
        ".ps-inp.is-invalid CSS missing — invalid inputs have no visual error state"


def test_ac3_aria_invalid_used():
    """aria-invalid must be set programmatically for invalid inputs."""
    html = _html()
    assert 'aria-invalid' in html, \
        "aria-invalid not present — screen readers won't announce field errors"


def test_ac3_field_error_message_element_exists():
    """Error messages must be associated via aria-describedby or ps-field-error elements."""
    html = _html()
    has_describedby = 'aria-describedby' in html
    has_error_class = 'ps-field-error' in html
    assert has_describedby or has_error_class, \
        "No aria-describedby or ps-field-error found — errors aren't associated with inputs"


# ── AC4: Save action triggers a visible confirmation; skipped for prefers-reduced-motion ──

def test_ac4_save_confirmation_animation_keyframe():
    """A @keyframes animation for save confirmation must be defined."""
    html = _html()
    # Look for the specific keyframe we'll add for the save button pulse
    assert '@keyframes psSavePulse' in html, \
        "@keyframes psSavePulse missing — save button has no confirmation animation"


def test_ac4_prefers_reduced_motion_suppresses_save_animation():
    """prefers-reduced-motion must suppress the save button animation."""
    html = _html()
    # Find all prefers-reduced-motion blocks and check one covers the save animation
    blocks = re.findall(
        r'@media\s*\(prefers-reduced-motion[^)]*\)\s*\{[^}]*\}',
        html, re.DOTALL,
    )
    covers_save = any('ps-btn-saved' in b for b in blocks)
    assert covers_save, \
        "No prefers-reduced-motion block suppressing .ps-btn-saved animation"


# ── AC5: Advisor tab renders a loading state while data fetches ──

def test_ac5_advisor_fetch_shows_loading_state():
    """_advFetch must set a loading indicator on the root element before the fetch resolves."""
    html = _html()
    # Extract _advFetch function body
    match = re.search(r'function _advFetch\(\)\s*\{(.*?)(?=\nfunction |\n// ──)', html, re.DOTALL)
    assert match, "_advFetch function not found"
    body = match.group(1)
    assert 'adv-loading' in body, \
        "_advFetch does not insert an adv-loading element before the fetch call"


# ── AC6: Advisor tab renders a friendly empty state when no data is available ──

def test_ac6_advisor_empty_css_defined():
    """.adv-empty CSS must be present for the empty state container."""
    html = _html()
    assert '.adv-empty' in html, ".adv-empty CSS class missing"


def test_ac6_advisor_empty_html_content():
    """Empty state must include a title and sub-message."""
    html = _html()
    assert 'adv-empty-title' in html, "adv-empty-title missing"
    assert 'adv-empty-sub' in html, "adv-empty-sub missing"
    assert 'No suggestions yet' in html, "Empty state message 'No suggestions yet' missing"


# ── AC7: All controls have aria-label or associated <label> elements ──

def test_ac7_estimation_inputs_have_aria_label():
    """S/M/L/XL estimation number inputs must each have an aria-label."""
    html = _html()
    for field_id in ['ps-est-s', 'ps-est-m', 'ps-est-l', 'ps-est-xl']:
        lines = [ln for ln in html.splitlines() if f'id="{field_id}"' in ln]
        assert lines, f"Input #{field_id} not found in HTML"
        assert any('aria-label' in ln for ln in lines), \
            f"#{field_id} is missing aria-label"


def test_ac7_buffer_inputs_have_aria_label():
    """Buffer percentage inputs must each have an aria-label."""
    html = _html()
    for field_id in ['ps-buf', 'ps-thin-buf']:
        lines = [ln for ln in html.splitlines() if f'id="{field_id}"' in ln]
        assert lines, f"Input #{field_id} not found in HTML"
        assert any('aria-label' in ln for ln in lines), \
            f"#{field_id} is missing aria-label"


def test_ac7_text_inputs_have_aria_label():
    """Branch and text inputs must each have an aria-label."""
    html = _html()
    for field_id in [
        'ps-display-name', 'ps-default-branch', 'ps-default-branch-uat',
        'ps-default-branch-prd', 'ps-sprint-budget', 'ps-tester-repo',
    ]:
        lines = [ln for ln in html.splitlines() if f'id="{field_id}"' in ln]
        assert lines, f"Input #{field_id} not found in HTML"
        assert any('aria-label' in ln for ln in lines), \
            f"#{field_id} is missing aria-label"


# ── AC8: Full keyboard navigation — no unexpected tabindex=-1 on main actions ──

def test_ac8_save_button_is_keyboard_reachable():
    """The Save changes button must not carry tabindex='-1'."""
    html = _html()
    # Find the save button line(s)
    save_lines = [ln for ln in html.splitlines()
                  if 'projSettingsSave' in ln and '<button' in ln]
    assert save_lines, "Save changes button not found"
    for ln in save_lines:
        assert 'tabindex="-1"' not in ln, \
            f"Save button has tabindex=-1, blocking keyboard access: {ln.strip()}"


# ── AC9: Implementation uses existing tokens; no new --custom-property names ──

def test_ac9_no_new_css_custom_properties():
    """No new --variable definitions may be introduced (only use existing tokens)."""
    html = _html()
    # Collect tokens defined in :root
    root_block = re.search(r':root\s*\{([^}]+)\}', html)
    assert root_block, ":root block not found"
    root_tokens = set(re.findall(r'--([\w-]+)\s*:', root_block.group(1)))

    # Collect tokens defined in [data-theme="dark"]
    dark_block = re.search(r'\[data-theme="dark"\]\s*\{([^}]+)\}', html)
    dark_tokens = set(re.findall(r'--([\w-]+)\s*:', dark_block.group(1))) if dark_block else set()

    all_defined = set(re.findall(r'--([\w-]+)\s*:', html))
    known = root_tokens | dark_tokens
    new_tokens = all_defined - known

    # Only flag tokens with prefixes that would indicate new definitions for this feature
    suspicious = {t for t in new_tokens if t.startswith('ps-') or t.startswith('adv-')}
    assert not suspicious, \
        f"New CSS custom property names introduced (not in :root): {suspicious}"


# ── AC10: Dark theme applied consistently ──

def test_ac10_settings_css_uses_design_tokens():
    """Settings tab CSS must use foundation design tokens (var(--...))."""
    html = _html()
    start = html.find('Project Settings tab')
    end = html.find('Advisor tab')
    settings_region = html[start:end] if end > start else html[start:start + 5000]
    assert 'var(--surface)' in settings_region or 'var(--bg)' in settings_region, \
        "Settings CSS doesn't reference --surface or --bg token"
    assert 'var(--border)' in settings_region, \
        "Settings CSS doesn't reference --border token"


def test_ac10_advisor_css_uses_design_tokens():
    """Advisor tab CSS must use foundation design tokens."""
    html = _html()
    start = html.find('Advisor tab')
    region = html[start:start + 4000]
    assert 'var(--' in region, "Advisor CSS region uses no design tokens"


# ── AC11: Vanilla JS/CSS only — no new framework dependencies ──

def test_ac11_no_new_framework_script_tags():
    """No React, Vue, Svelte, Angular, or jQuery script imports may be added."""
    html = _html()
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    frameworks = ('react', 'vue', 'svelte', 'angular', 'jquery')
    for src in srcs:
        for fw in frameworks:
            assert fw not in src.lower(), \
                f"Framework '{fw}' found in <script src>: {src}"
