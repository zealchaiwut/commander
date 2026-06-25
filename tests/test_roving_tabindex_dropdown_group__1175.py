"""Tests for issue #1175: Roving tabindex leaves no focusable tab when a dropdown-group tab is active (runs against UAT)"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_roving_tabindex_top_level_tab_active(client):
    # AC: When the active tab is a top-level tab (e.g. Board, Sprint), exactly one tab in the strip has tabIndex="0" and all others have tabIndex="-1"
    # This is a DOM/browser interaction test; cannot be fully verified via HTTP.
    # The feature affects DOM state after page load; browser automation would be needed.
    pytest.skip("manual — browser interaction required for DOM tabIndex inspection and keyboard focus verification")


def test_roving_tabindex_manage_dropdown_active(client):
    # AC: When the active tab is a child of the manage dropdown group (Logs, Deploy), the manage group trigger element has tabIndex="0" and all other top-level tab elements have tabIndex="-1"
    # This is a DOM/browser interaction test; cannot be fully verified via HTTP.
    pytest.skip("manual — browser interaction required for DOM tabIndex inspection")


def test_roving_tabindex_planning_dropdown_active(client):
    # AC: When the active tab is a child of the planning dropdown group (Analytics, Roadmap, Advisor), the planning group trigger element has tabIndex="0" and all other top-level tab elements have tabIndex="-1"
    # This is a DOM/browser interaction test; cannot be fully verified via HTTP.
    pytest.skip("manual — browser interaction required for DOM tabIndex inspection")


def test_roving_tabindex_exactly_one_zero_element(client):
    # AC: At no point does the tab strip contain zero elements with tabIndex="0" — the tablist always has exactly one Tab-reachable entry regardless of which tab is active
    # This is a DOM/browser interaction test; cannot be fully verified via HTTP.
    pytest.skip("manual — browser interaction required for DOM tabIndex verification across multiple tab switches")


def test_roving_tabindex_tab_focus_from_outside(client):
    # AC: Pressing Tab from outside the tab strip moves focus to the single tabIndex="0" element, whether that is a top-level tab or a group trigger
    # This is a DOM/browser interaction test; keyboard focus cannot be verified via HTTP.
    pytest.skip("manual — browser interaction required for keyboard focus and Tab-key navigation verification")


def test_roving_tabindex_arrow_key_navigation(client):
    # AC: Navigating between tabs with arrow keys continues to work correctly after this fix — roving tabindex updates properly when switching from a group-child tab to a top-level tab and vice versa
    # This is a DOM/browser interaction test; keyboard navigation cannot be verified via HTTP.
    pytest.skip("manual — browser interaction required for arrow-key navigation and tabindex state verification")
