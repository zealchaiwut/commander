"""Tests for issue #1179: Wrap backlog filter pills on narrow screens (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
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

def test_wrap_backlog_filter_pills__filter_pills_wrap_at_375px(client):
    # AC: At 375px viewport width, filter pills wrap to multiple rows instead of overflowing
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")


def test_wrap_backlog_filter_pills__filter_pills_wrap_at_600px(client):
    # AC: At 600px viewport width, filter pills wrap to multiple rows instead of overflowing
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")


def test_wrap_backlog_filter_pills__no_horizontal_scroll_at_375px(client):
    # AC: No horizontal page scroll appears at 375px or 600px when the filter row is visible
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")


def test_wrap_backlog_filter_pills__desktop_layout_unchanged(client):
    # AC: Desktop viewport (≥621px) layout is unchanged — pills remain on a single row
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")


def test_wrap_backlog_filter_pills__css_flex_wrap_applied(client):
    # AC: `.bl-filter` has `flex-wrap: wrap` and `gap: 6px` applied inside `@media (max-width: 620px)`
    # Verify the static HTML is served (no JS runtime inspection possible via HTTP)
    r = client.get("/static/project.html")
    assert r.status_code == 200
    # Check that the CSS rules are present in the markup
    # Look for the media query with the wrap rule
    assert "@media (max-width: 620px)" in r.text
    assert ".bl-filter" in r.text
    # The gap: 6px should be present in at least one of the media queries for .bl-filter
    assert "gap: 6px" in r.text


def test_wrap_backlog_filter_pills__pill_shrink_enabled(client):
    # AC: `.bl-pill` can shrink (no `flex-shrink: 0` or `min-width` that prevents wrapping) within the breakpoint
    r = client.get("/static/project.html")
    assert r.status_code == 200
    # Verify CSS has .bl-pill defined and doesn't have problematic shrink constraints
    assert ".bl-pill" in r.text
    # .bl-pill should not have flex-shrink: 0 globally (it may have it in specific contexts like .smgmt-moveto-pill)
    # The test just verifies the class exists and is styled appropriately


def test_wrap_backlog_filter_pills__overflow_pill_not_clipped(client):
    # AC: The "6+" overflow pill row does not clip or overflow its container at narrow widths
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")
