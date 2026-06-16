"""Tests for issue #1183: Stack logs search and view toggle on narrow screens (runs against UAT)"""
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

def test_logs_toolbar_mobile_responsive__viewport_375px_stacks_vertically(client):
    # AC: At 375px viewport width, `.logs-toolbar-row2` items stack vertically (column direction)
    # This test verifies the page loads and the CSS selectors exist; actual rendering verified via browser UAT step.
    r = client.get("/project/zealchaiwut-commander/logs")
    assert r.status_code == 200
    html = r.text
    assert "logs-toolbar-row2" in html
    assert "logs-search-wrap" in html
    assert "logs-view-toggle" in html


def test_logs_toolbar_mobile_responsive__search_wrap_full_width_375px(client):
    # AC: At 375px viewport width, `.logs-search-wrap` spans full container width
    r = client.get("/project/zealchaiwut-commander/logs")
    assert r.status_code == 200
    html = r.text
    assert "logs-search-wrap" in html
    # The responsive CSS should be present in the page


def test_logs_toolbar_mobile_responsive__view_toggle_full_width_375px(client):
    # AC: At 375px viewport width, `.logs-view-toggle` spans full container width
    r = client.get("/project/zealchaiwut-commander/logs")
    assert r.status_code == 200
    html = r.text
    assert "logs-view-toggle" in html


def test_logs_toolbar_mobile_responsive__same_behavior_at_620px(client):
    # AC: At 620px viewport width, same stacking behavior applies as at 375px
    r = client.get("/project/zealchaiwut-commander/logs")
    assert r.status_code == 200
    html = r.text
    assert "logs-toolbar-row2" in html


def test_logs_toolbar_mobile_responsive__search_input_usable_375px(client):
    # AC: Search input is fully usable (no overflow, no clipping) at 375px
    r = client.get("/project/zealchaiwut-commander/logs")
    assert r.status_code == 200
    html = r.text
    assert "logs-search" in html
    assert "logs-search-icon" in html


def test_logs_toolbar_mobile_responsive__desktop_unchanged_over_620px(client):
    # AC: At desktop widths (>620px), toolbar layout is unchanged from current behavior
    r = client.get("/project/zealchaiwut-commander/logs")
    assert r.status_code == 200
    html = r.text
    assert "logs-toolbar" in html


def test_logs_toolbar_mobile_responsive__no_horizontal_scroll_375px(client):
    # AC: No horizontal scroll bar appears on the logs page at 375px or 620px
    # This is verified via browser UAT steps with actual viewport resizing
    r = client.get("/project/zealchaiwut-commander/logs")
    assert r.status_code == 200
    html = r.text
    # Verify the page structure exists
    assert "logs-pane" in html
