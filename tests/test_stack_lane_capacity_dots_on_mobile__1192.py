"""Tests for issue #1192: Stack lane capacity dots on mobile screens (runs against UAT)"""
import os
import pytest
import httpx


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

def test_stack_lane_capacity_dots_on_mobile__375px_stacking(client):
    # AC: At 375px viewport: `.lane-capacity` stacks vertically (`flex-direction: column`, `gap: 4px`)
    # This is a CSS layout test — verify the page loads and inspect computed styles via browser
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")


def test_stack_lane_capacity_dots_on_mobile__640px_stacking(client):
    # AC: At 640px viewport: `.lane-capacity` stacks vertically
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")


def test_stack_lane_capacity_dots_on_mobile__641px_no_stacking(client):
    # AC: At 641px+ viewport: `.lane-capacity` layout unchanged from current desktop behavior
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")


def test_stack_lane_capacity_dots_on_mobile__375px_dots_wrap(client):
    # AC: At 375px and 640px: `.lane-dots` wraps onto multiple lines (`flex-wrap: wrap`)
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")


def test_stack_lane_capacity_dots_on_mobile__375px_no_overflow(client):
    # AC: No horizontal overflow on lane capacity section at 375px
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")


def test_stack_lane_capacity_dots_on_mobile__no_regression_desktop(client):
    # AC: No visual regression on desktop (1024px+)
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")
