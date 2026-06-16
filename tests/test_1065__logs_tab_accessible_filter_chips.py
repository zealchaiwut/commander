"""Tests for issue #1065: Polish Logs Tab UI with Accessible Filter Chips

TDD tests written before implementation. Each test is anchored to a specific AC.
"""
import os
import re
import pytest
import httpx

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
def logs_html(client):
    r = client.get("/project/commander/logs")
    assert r.status_code == 200
    return r.text


# AC1 — Filter chips have aria-pressed and focus ring
def test_filter_chips_have_aria_pressed(logs_html):
    """AC1: Filter chips have aria-pressed attribute on source filter buttons."""
    assert 'aria-pressed' in logs_html, (
        "Source filter chips must have aria-pressed attribute for screen-reader state"
    )


def test_filter_chips_focus_ring_css(logs_html):
    """AC1: Filter chips have a visible focus ring via :focus-visible CSS."""
    # Check for focus-visible rule targeting logs-chip
    assert 'logs-chip:focus-visible' in logs_html or '.logs-chip:focus' in logs_html, (
        "logs-chip must have a :focus-visible rule for keyboard navigation"
    )
    # And it should reference an outline (the focus ring itself)
    focus_section = re.search(
        r'\.logs-chip:focus[^}]+\}', logs_html, re.DOTALL
    )
    assert focus_section, "logs-chip focus rule not found in CSS"
    focus_css = focus_section.group(0)
    assert 'outline' in focus_css, "Focus ring (outline) missing from logs-chip:focus-visible rule"


# AC2 — Row hover/focus states use foundation tokens
def test_row_hover_uses_foundation_tokens(logs_html):
    """AC2: Row hover states reference CSS token variables, not hardcoded colors."""
    # logs-issue-row:hover already exists; confirm it uses var(--surface-hover)
    assert 'logs-issue-row:hover' in logs_html, "logs-issue-row:hover rule missing"
    row_hover = re.search(r'\.logs-issue-row:hover\s*\{([^}]+)\}', logs_html, re.DOTALL)
    assert row_hover, "Could not parse logs-issue-row:hover rule"
    assert 'var(--' in row_hover.group(1), (
        "logs-issue-row:hover must use a CSS token variable (var(--...)), not hardcoded color"
    )


def test_row_focus_state_present(logs_html):
    """AC2: Interactive row elements have a focus state style."""
    # evl-bundle-header is a clickable row — needs focus ring
    assert 'evl-bundle-header:focus' in logs_html or 'logs-issue-row:focus' in logs_html, (
        "Log rows must have explicit :focus or :focus-visible CSS rules"
    )


# AC3 — Tab switch animation with prefers-reduced-motion
def test_tab_switch_transition_css(logs_html):
    """AC3: View toggle buttons or body panels have a CSS transition for smooth switching."""
    # Should have a transition on the logs-view-btn or logs-body panels
    assert ('logs-view-btn' in logs_html and 'transition' in logs_html), (
        "logs-view-btn should have a CSS transition for smooth tab switching"
    )


def test_prefers_reduced_motion_suppresses_tab_transitions(logs_html):
    """AC3: prefers-reduced-motion media query suppresses tab-switch transitions."""
    assert 'prefers-reduced-motion' in logs_html, (
        "CSS must include @media (prefers-reduced-motion: reduce) to suppress animations"
    )


# AC4 — List filter transition with prefers-reduced-motion
def test_list_filter_transition_css(logs_html):
    """AC4: The activity list (evl-list) or individual rows have a transition for filtering."""
    # The evl-list or evl-bundle-row should have a CSS transition
    has_transition = (
        'evl-bundle-row' in logs_html and 'transition' in logs_html
    )
    assert has_transition, "evl-bundle-row should have a CSS transition for filter animations"


# AC5 — Loading spinner or skeleton
def test_loading_skeleton_or_spinner_present(logs_html):
    """AC5: A loading indicator (spinner or skeleton) is present in the DOM for the events list."""
    has_spinner = (
        'logs-skeleton' in logs_html
        or 'logs-spinner' in logs_html
        or 'evl-skeleton' in logs_html
        or 'evl-spinner' in logs_html
        or ('skeleton' in logs_html and 'logs' in logs_html)
    )
    assert has_spinner, (
        "A loading skeleton or spinner element must exist in the Logs/Activity view"
    )


def test_loading_indicator_css_present(logs_html):
    """AC5: The loading skeleton/spinner has corresponding CSS."""
    has_css = (
        'logs-skeleton' in logs_html
        or 'evl-skeleton' in logs_html
        or 'logs-spinner' in logs_html
    )
    assert has_css, "Loading skeleton/spinner must have CSS class definition"


# AC6 — Empty state with "No events" text
def test_empty_state_no_events_text(logs_html):
    """AC6: The activity view renders 'No events' text when event list is empty."""
    assert 'No events' in logs_html, (
        "Logs view must contain 'No events' as the empty-state text for the activity list"
    )


def test_empty_state_in_evl_list(logs_html):
    """AC6: 'No events' empty state is inside or referenced by the activity list area."""
    # The evl-list area should either contain or be able to render "No events"
    evl_section = re.search(
        r'id=["\']evl-list["\'][^<]*>.*?</div>', logs_html, re.DOTALL
    )
    if evl_section:
        # If the evl-list exists, check that the JS evlRender function contains "No events"
        assert 'No events' in logs_html, "evlRender should produce 'No events' empty state text"
    else:
        # Fallback: the empty state text exists somewhere in the logs pane
        pane_section = re.search(
            r'id=["\']pane-logs["\'].*?</div>', logs_html, re.DOTALL
        )
        assert pane_section and 'No events' in pane_section.group(0), (
            "'No events' empty state must be visible in the logs pane"
        )


# AC7 — Dark theme via foundation tokens
def test_dark_theme_tokens_in_logs_css(logs_html):
    """AC7: Logs CSS uses token variables (var(--...)) for dark theme consistency."""
    # Count var(-- references in the logs CSS section
    logs_css_start = logs_html.find('Logs tab (issue #420)')
    evl_css_start = logs_html.find('Events Activity Log')
    if logs_css_start == -1:
        logs_css_start = logs_html.find('.logs-pane')
    logs_css_end = logs_html.find('/* ──', evl_css_start + 100) if evl_css_start > 0 else -1
    if logs_css_end == -1:
        logs_css_end = logs_css_start + 20000

    logs_css = logs_html[logs_css_start:logs_css_end]
    token_count = logs_css.count('var(--')
    assert token_count >= 20, (
        f"Logs CSS should use at least 20 CSS token references (found {token_count}); "
        "dark theme must reference shared foundation tokens"
    )


def test_no_hardcoded_hex_in_logs_section(logs_html):
    """AC7: No hardcoded hex colors in the logs-specific CSS block (outside comments and var() fallbacks)."""
    # Find logs CSS section from "Logs tab" comment to next major section (Toast notifications)
    logs_css_start = logs_html.find('Logs tab (issue')
    if logs_css_start == -1:
        logs_css_start = logs_html.find('.logs-pane')
    # End at next standalone CSS section header or </style> — whichever is closer
    next_section = logs_html.find('Toast notifications', logs_css_start)
    style_end = logs_html.find('</style>', logs_css_start)
    logs_css_end = next_section if (next_section > 0 and next_section < style_end) else style_end
    logs_css = logs_html[logs_css_start:logs_css_end] if logs_css_end > 0 else logs_html[logs_css_start:]

    # Strip CSS block comments to avoid matching issue numbers like /* issue #420 */
    logs_css_no_comments = re.sub(r'/\*.*?\*/', '', logs_css, flags=re.DOTALL)

    # Strip var(--token, #fallback) patterns — fallback hex inside var() is acceptable
    logs_css_no_var_fallback = re.sub(r'var\([^)]+\)', '', logs_css_no_comments)

    # Now find any remaining bare hex color literals (6 or 8 char only)
    hex_matches = re.findall(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b', logs_css_no_var_fallback)
    assert len(hex_matches) <= 3, (
        f"Found {len(hex_matches)} hardcoded hex colors in logs CSS (outside comments/var fallbacks); "
        f"use token vars instead: {hex_matches[:5]}"
    )


# AC8 — Scoped changes (code review criterion — skip in automated tests)
def test_changes_scoped_to_logs_view():
    """AC8: All JS/markup changes are scoped to the logs view src/ modules (code-review gate)."""
    pytest.skip("manual — verified by git diff showing only logs-view-scoped changes")


# AC9 — Bundle rebuilt
def test_bundle_is_served_and_current(client):
    """AC9: static/dist/bundle.js is served and contains the expected log module exports."""
    r = client.get("/static/dist/bundle.js")
    assert r.status_code == 200, "bundle.js must be served at /static/dist/bundle.js"
    bundle = r.text
    assert len(bundle) > 5000, "bundle.js appears empty or minimal — run npm run build"
    # The bundle should expose logpanel exports on window
    assert 'colorizeLogLine' in bundle or 'escapeLogHtml' in bundle or 'window.' in bundle, (
        "bundle.js should contain logpanel module exports (colorizeLogLine, escapeLogHtml)"
    )


# AC10 — impeccable detect passes (manual)
def test_impeccable_detect_passes():
    """AC10: npx impeccable detect passes on the logs view with no regressions."""
    pytest.skip("manual — run 'npx impeccable detect apps/dashboard/static/project.html' locally")
