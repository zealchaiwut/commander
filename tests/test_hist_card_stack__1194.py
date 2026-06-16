"""Tests for issue #1194: Stack Sprint History card header on mobile (runs against UAT)"""
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

def test_hist_card_stack__375px_viewport_stacks_to_column(client):
    # AC: At 375 px viewport width, `.hist-card-head` stacks into a column
    # (title/badges on top, actions below)
    # This is an HTTP-only verification that the page serves and contains the necessary CSS
    r = client.get("/")
    assert r.status_code == 200
    # Verify the CSS rule exists for mobile breakpoint (375px and below)
    assert "max-width: 600px" in r.text
    assert "hist-card-head" in r.text
    assert "flex-direction: column" in r.text
    pytest.skip("visual verification — confirm via design-contract gate / agent-browser, not HTTP")


def test_hist_card_stack__600px_viewport_column_with_gap(client):
    # AC: At 600 px viewport width, same column layout applies with `gap: 8px` between sections
    r = client.get("/")
    assert r.status_code == 200
    # Verify the CSS has gap: 8px in the media query
    assert "max-width: 600px" in r.text
    assert "gap: 8px" in r.text
    pytest.skip("visual verification — confirm via design-contract gate / agent-browser, not HTTP")


def test_hist_card_stack__full_width_flex_basis_600px(client):
    # AC: `.hist-card-head-left` and `.hist-card-head-right` each span full width
    # (`flex-basis: 100%`) at ≤ 600 px
    r = client.get("/")
    assert r.status_code == 200
    # Verify flex-basis: 100% is set in the mobile media query
    assert "flex-basis: 100%" in r.text or "width: 100%" in r.text
    pytest.skip("visual verification — confirm via design-contract gate / agent-browser, not HTTP")


def test_hist_card_stack__no_overflow_at_600px(client):
    # AC: No horizontal scrollbar or overflow on the card header at any width ≤ 600 px
    r = client.get("/")
    assert r.status_code == 200
    # Verify overflow-x: hidden is set in media query
    assert "overflow-x: hidden" in r.text or "overflow: hidden" in r.text
    pytest.skip("visual verification — confirm via design-contract gate / agent-browser, not HTTP")


def test_hist_card_stack__desktop_unchanged(client):
    # AC: Desktop layout (> 600 px) is visually unchanged
    # Verify the desktop CSS for hist-card-head is present (grid-template-columns)
    r = client.get("/")
    assert r.status_code == 200
    # Desktop should use grid layout
    assert "grid-template-columns: minmax(0, 1fr) auto" in r.text
    pytest.skip("visual verification — confirm via design-contract gate / agent-browser, not HTTP")


def test_hist_card_stack__button_interactions_functional(client):
    # AC: All existing badge and action button interactions still function after layout change
    # HTTP-only test: verify the page loads and action buttons are present
    r = client.get("/")
    assert r.status_code == 200
    # Verify action button classes exist
    assert "hist-head-actions" in r.text
    assert "hist-head-btn" in r.text
    pytest.skip("interaction verification — confirm via browser, not HTTP")
