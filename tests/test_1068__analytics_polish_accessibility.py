"""Issue #1068 — Polish Analytics page UI and accessibility.

Each test is anchored to a specific AC item. Tests fail before the fix
and pass after the implementation satisfies the criterion.
"""
import os
import re
import shutil
import subprocess

import pytest

ANALYTICS_HTML = os.path.join(
    os.path.dirname(__file__), "..", "apps", "dashboard", "static", "analytics.html"
)
ROOT = os.path.join(os.path.dirname(__file__), "..")


def _html():
    with open(ANALYTICS_HTML, encoding="utf-8") as f:
        return f.read()


def _style():
    m = re.search(r"<style>(.*?)</style>", _html(), re.DOTALL)
    assert m, "<style> block not found in analytics.html"
    return m.group(1)


def _script():
    m = re.search(r"<script>(.*?)</script>", _html(), re.DOTALL | re.IGNORECASE)
    # May have multiple script tags; collect the main inline one
    scripts = re.findall(r"<script>(.*?)</script>", _html(), re.DOTALL | re.IGNORECASE)
    return "\n".join(scripts)


def _block(selector, css):
    """Return body of the first CSS rule matching selector string exactly."""
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    m = re.search(pattern, css, re.DOTALL)
    return m.group(1) if m else None


# ── AC1: sub-tab hover/focus states use foundation tokens ─────────────────────

def test_ac1_tab_hover_uses_token_not_hex():
    """Hover state on anl-tab-btn must use CSS tokens — no hardcoded hex."""
    css = _style()
    hover_block = _block(".anl-tab-btn:hover:not(.active)", css)
    assert hover_block is not None, ".anl-tab-btn:hover:not(.active) rule not found"
    hex_re = r"#[0-9a-fA-F]{3,8}\b"
    matches = re.findall(hex_re, hover_block)
    assert not matches, (
        f"Hardcoded hex in hover rule — use var(--): {matches}"
    )


def test_ac1_tab_hover_has_background_token():
    """Hover state must include a background using a foundation token."""
    css = _style()
    hover_block = _block(".anl-tab-btn:hover:not(.active)", css)
    assert hover_block is not None, ".anl-tab-btn:hover:not(.active) rule not found"
    assert "background" in hover_block and "var(--" in hover_block, (
        ".anl-tab-btn:hover:not(.active) must set background using a foundation token "
        "(e.g. var(--surface-2)) to give a visible hover affordance"
    )


def test_ac1_tab_focus_visible_uses_token_not_hex():
    """Focus-visible ring on tab must use CSS tokens, no hardcoded hex."""
    css = _style()
    focus_block = _block(".anl-tab-btn:focus-visible", css)
    assert focus_block is not None, ".anl-tab-btn:focus-visible rule not found"
    hex_re = r"#[0-9a-fA-F]{3,8}\b"
    matches = re.findall(hex_re, focus_block)
    assert not matches, (
        f"Hardcoded hex in focus-visible rule — use var(--): {matches}"
    )


def test_ac1_tab_focus_visible_outline_offset_positive():
    """Focus ring must be outside the element (outline-offset >= 0) for visibility."""
    css = _style()
    focus_block = _block(".anl-tab-btn:focus-visible", css)
    assert focus_block is not None, ".anl-tab-btn:focus-visible rule not found"
    m = re.search(r"outline-offset:\s*(-?\d+)px", focus_block)
    if m:
        offset = int(m.group(1))
        assert offset >= 0, (
            f"outline-offset is {offset}px (inside element). Must be >= 0 "
            "so the focus ring is visible outside the button boundary."
        )


# ── AC2: sub-tab content transitions, prefers-reduced-motion ─────────────────

def test_ac2_panel_transition_or_animation_defined():
    """The panel show/hide must use CSS transition or animation for smooth switching."""
    css = _style()
    # Check for transition or @keyframes or animation on panel-related rules
    has_transition = bool(
        re.search(r"\.anl-panel[^\{]*\{[^}]*transition", css, re.DOTALL)
        or re.search(r"\.anl-panel\.show[^\{]*\{[^}]*(?:transition|animation)", css, re.DOTALL)
        or re.search(r"@keyframes\s+anl", css)
    )
    assert has_transition, (
        "No CSS transition or animation found for .anl-panel / .anl-panel.show. "
        "AC2 requires smooth content transitions."
    )


def test_ac2_prefers_reduced_motion_media_query_present():
    """A @media (prefers-reduced-motion: reduce) block must exist and disable animations."""
    css = _style()
    assert "prefers-reduced-motion" in css, (
        "@media (prefers-reduced-motion: reduce) block is missing. "
        "AC2 requires disabling animation for users who prefer it."
    )
    # Confirm the block actually contains animation/transition reset
    rm_block_m = re.search(
        r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
        css, re.DOTALL
    )
    assert rm_block_m, "@media (prefers-reduced-motion: reduce) block not parseable"
    rm_block = rm_block_m.group(1)
    assert "transition" in rm_block or "animation" in rm_block, (
        "The prefers-reduced-motion block must reset transition or animation to none"
    )


# ── AC3: loading skeleton/spinner while data fetches ─────────────────────────

def test_ac3_loading_skeleton_css_class_defined():
    """A CSS class for loading state (skeleton or spinner) must be defined."""
    css = _style()
    has_skeleton = (
        "anl-skeleton" in css
        or "skeleton" in css
        or "anl-spinner" in css
        or "anl-loading" in css
    )
    assert has_skeleton, (
        "No skeleton or spinner CSS class found. "
        "AC3 requires charts/tables show a loading state while data fetches."
    )


def test_ac3_loading_state_shown_before_fetch():
    """fetchCost/fetchMetrics/fetchCalibration must inject a loading indicator
    before the async fetch completes."""
    script = _script()
    # Look for loading indicator being set before fetch() calls
    has_loading_before_fetch = (
        re.search(r"(anl-skeleton|anl-spinner|anl-loading|loading|Loading)", script)
        is not None
        and re.search(r"innerHTML\s*=.*?(skeleton|loading|spinner)", script, re.IGNORECASE | re.DOTALL)
        is not None
    )
    assert has_loading_before_fetch, (
        "fetch functions must set innerHTML to a loading/skeleton indicator "
        "before the async fetch resolves (AC3)."
    )


# ── AC4: intentional empty states ────────────────────────────────────────────

def test_ac4_trends_panel_empty_state_has_icon_and_message():
    """Trends panel empty state must contain an icon and descriptive message."""
    html = _html()
    empty_block_m = re.search(
        r'id="anl-trends-empty"[^>]*>(.*?)</div>',
        html, re.DOTALL
    )
    assert empty_block_m, "anl-trends-empty element not found"
    empty_block = empty_block_m.group(1)
    assert "<i " in empty_block or "<svg" in empty_block, (
        "Trends empty state must contain an icon (<i> or <svg>)"
    )
    assert re.search(r"<p[^>]*>|<span[^>]*>", empty_block), (
        "Trends empty state must contain a text message (<p> or <span>)"
    )


def test_ac4_calibration_panel_empty_state_has_icon_and_message():
    """Calibration panel empty state must contain an icon and message."""
    html = _html()
    empty_block_m = re.search(
        r'id="cal-empty"[^>]*>(.*?)</div>',
        html, re.DOTALL
    )
    assert empty_block_m, "cal-empty element not found"
    empty_block = empty_block_m.group(1)
    assert "<i " in empty_block or "<svg" in empty_block, (
        "Calibration empty state must contain an icon"
    )
    assert re.search(r"<p[^>]*>|<span[^>]*>", empty_block), (
        "Calibration empty state must contain a text message"
    )


def test_ac4_status_panel_empty_state_present():
    """Status panel must have an empty state element."""
    html = _html()
    assert 'id="anl-status-empty"' in html, (
        "Status panel is missing anl-status-empty empty-state element"
    )


def test_ac4_metrics_panel_empty_state_or_empty_cards():
    """Metrics panel must have an empty state (panel-level or card-level)."""
    html = _html()
    # Metrics cards already use .is-empty + .metric-empty but verify panel-level OR card-level
    has_panel_empty = 'id="anl-metrics-empty"' in html
    has_card_empty = "metric-empty" in html
    assert has_panel_empty or has_card_empty, (
        "Metrics panel must have an empty state: either a panel-level "
        "anl-metrics-empty element or metric-card.is-empty / .metric-empty pattern."
    )


# ── AC5: keyboard arrow-key navigation ───────────────────────────────────────

def test_ac5_arrow_key_handler_on_tablist():
    """Arrow key (ArrowLeft/ArrowRight) handler must be wired for keyboard tab cycling."""
    script = _script()
    has_arrow = (
        "ArrowLeft" in script or "arrowleft" in script.lower()
        or "ArrowRight" in script or "arrowright" in script.lower()
    )
    assert has_arrow, (
        "No ArrowLeft/ArrowRight key handling found. "
        "AC5 requires keyboard arrow key cycling across sub-tabs."
    )


def test_ac5_keydown_event_listener_on_tablist():
    """A keydown event listener must be attached to the tab bar or tab buttons."""
    script = _script()
    has_keydown = "keydown" in script
    assert has_keydown, (
        "No 'keydown' event listener found. "
        "AC5 requires attaching a keydown handler to enable arrow key navigation."
    )


def test_ac5_tablist_role_on_tab_bar():
    """The tab container must have role=tablist for screen-reader grouping."""
    html = _html()
    assert 'role="tablist"' in html, (
        'The sub-tab container must have role="tablist" for keyboard navigation context.'
    )


# ── AC6: aria-selected reflects active state ─────────────────────────────────

def test_ac6_aria_selected_on_default_active_tab():
    """The initially active tab (Trends) must have aria-selected='true' in HTML."""
    html = _html()
    # The Trends tab should be active by default
    trends_tab_m = re.search(
        r'id="anl-tab-trends"[^>]*aria-selected="([^"]*)"',
        html
    )
    if not trends_tab_m:
        trends_tab_m = re.search(
            r'aria-selected="([^"]*)"[^>]*id="anl-tab-trends"',
            html
        )
    assert trends_tab_m, 'anl-tab-trends must have aria-selected attribute'
    assert trends_tab_m.group(1) == "true", (
        f"Default active tab (Trends) must have aria-selected='true', "
        f"got: '{trends_tab_m.group(1)}'"
    )


def test_ac6_aria_selected_set_in_anlShowTab():
    """anlShowTab must update aria-selected on tab switch."""
    script = _script()
    assert "aria-selected" in script, (
        "anlShowTab must call setAttribute('aria-selected', ...) when switching tabs"
    )


def test_ac6_inactive_tabs_aria_selected_false():
    """Non-default tabs must have aria-selected='false' in HTML."""
    html = _html()
    for tab in ("status", "metrics", "calibration"):
        m = re.search(
            r'id="anl-tab-' + tab + r'"[^>]*aria-selected="([^"]*)"',
            html
        )
        if not m:
            m = re.search(
                r'aria-selected="([^"]*)"[^>]*id="anl-tab-' + tab + r'"',
                html
            )
        assert m, f'anl-tab-{tab} must have aria-selected attribute'
        assert m.group(1) == "false", (
            f"Inactive tab '{tab}' must have aria-selected='false', "
            f"got: '{m.group(1)}'"
        )


# ── AC7: focus ring meets WCAG 2.1 AA ────────────────────────────────────────

def test_ac7_focus_ring_present_on_tab_btn():
    """:focus-visible rule for .anl-tab-btn must exist with outline."""
    css = _style()
    focus_block = _block(".anl-tab-btn:focus-visible", css)
    assert focus_block is not None, ".anl-tab-btn:focus-visible CSS rule not found"
    assert "outline" in focus_block, (
        ".anl-tab-btn:focus-visible must define an outline for visible focus ring (WCAG 2.1 AA)"
    )


def test_ac7_focus_ring_uses_blue_token():
    """Focus ring must use var(--blue) for the accent (meets 3:1+ contrast on both themes)."""
    css = _style()
    focus_block = _block(".anl-tab-btn:focus-visible", css)
    assert focus_block is not None, ".anl-tab-btn:focus-visible rule not found"
    assert "var(--blue)" in focus_block, (
        ".anl-tab-btn:focus-visible must use var(--blue) for the focus ring — "
        "meets WCAG 2.1 AA 3:1 contrast against the tab bar background in both themes"
    )


# ── AC8: vanilla JS/CSS only ──────────────────────────────────────────────────

def test_ac8_no_new_external_script_dependencies():
    """No new external script src URLs beyond the Tabler icons CDN."""
    html = _html()
    external_scripts = re.findall(r'<script[^>]+src="(https?://[^"]+)"', html)
    for url in external_scripts:
        assert False, (
            f"Unexpected external <script src> introduced: {url}. "
            "AC8: use vanilla JS only, no new framework dependencies."
        )


def test_ac8_no_import_statements():
    """No ES module import statements (no module framework introduced)."""
    script = _script()
    assert "import " not in script or "// import" in script, (
        "ES module 'import' found. AC8 requires vanilla JS only — no module bundler."
    )


# ── AC9: dark theme consistent across analytics sub-surfaces ─────────────────

def test_ac9_dark_theme_tokens_in_style():
    """Dark theme override block must be present with core tokens."""
    css = _style()
    assert '[data-theme="dark"]' in css, (
        'Dark theme override [data-theme="dark"] block is missing from analytics.html'
    )
    dark_m = re.search(r'\[data-theme="dark"\]\s*\{([^}]*)\}', css, re.DOTALL)
    assert dark_m, "Dark theme override block not parseable"
    dark_block = dark_m.group(1)
    for token in ("--bg", "--surface", "--text", "--border", "--blue"):
        assert token in dark_block, (
            f"Dark theme block missing required token: {token}"
        )


def test_ac9_new_classes_use_tokens_not_hardcoded_colors():
    """Any skeleton/spinner/loading CSS must use tokens, not hardcoded colors."""
    css = _style()
    # Strip :root and dark-theme blocks (they legitimately define colors)
    stripped = re.sub(r":root\s*\{[^}]*\}", "", css, flags=re.DOTALL)
    stripped = re.sub(r'\[data-theme="dark"\]\s*\{[^}]*\}', "", stripped, flags=re.DOTALL)
    # Strip known media queries that may contain vendor values
    stripped = re.sub(r"@media[^{]*\{[^}]*(?:\{[^}]*\}[^}]*)*\}", "", stripped, flags=re.DOTALL)
    hex_matches = re.findall(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b", stripped)
    assert not hex_matches, (
        f"Hardcoded hex colors found in component CSS rules: {hex_matches}. "
        "Use CSS variables (var(--)) so dark theme stays consistent."
    )


# ── AC10: detect lint passes ─────────────────────────────────────────────────

def test_ac10_impeccable_detect_passes():
    """npx impeccable detect analytics.html must exit 0 (no new anti-patterns)."""
    if shutil.which("npx") is None:
        pytest.skip("npx not available in this environment; gate runs in tester/CI")
    result = subprocess.run(
        ["npx", "impeccable", "detect", ANALYTICS_HTML],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Impeccable found violations:\n{result.stdout}\n{result.stderr}"
    )


# ── AC11: changes scoped to analytics, no regressions ───────────────────────

def test_ac11_analytics_tab_ids_intact():
    """All four sub-tab IDs and panel IDs must remain intact."""
    html = _html()
    for tab in ("trends", "status", "metrics", "calibration"):
        assert f'id="anl-tab-{tab}"' in html, f"Tab button ID missing: anl-tab-{tab}"
        assert f'id="anl-panel-{tab}"' in html, f"Panel ID missing: anl-panel-{tab}"


def test_ac11_fetch_functions_still_exposed():
    """Core data-fetch functions must remain exposed on window."""
    html = _html()
    for fn in ("fetchCost", "fetchMetrics", "fetchCalibration"):
        assert f"window.{fn}" in html, (
            f"Data-fetch function no longer on window: {fn}. "
            "Scope changes must not break existing fetch wiring."
        )


def test_ac11_top_nav_and_brand_intact():
    """Shared nav components must remain unchanged."""
    html = _html()
    assert 'class="top-nav"' in html, "top-nav class removed — regression in shared nav"
    assert 'class="brand-mark"' in html, "brand-mark removed — shared nav regression"
    assert 'class="btn-icon"' in html, "btn-icon removed — shared nav regression"
