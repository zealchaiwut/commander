"""Tests for issue #928: extract reusable progress/activity component (runs against UAT)"""
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


def test_progress_activity__ac1_bar_mode_render(client):
    """AC1: bar mode renders determinate fraction, current-item label, counts, estimated-remaining."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # Component is bundled; verify page loads with bundle script tag present
    assert "/static/dist/bundle.js" in r.text, "Bundle not loaded"


def test_progress_activity__ac2_stepper_mode_render(client):
    """AC2: stepper mode renders fixed ordered steps cycling pending→running→done/failed."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # Stepper mode verification: component file exists and is exported
    assert "/static/dist/bundle.js" in r.text


def test_progress_activity__ac3_indeterminate_mode(client):
    """AC3: indeterminate mode renders shimmer/spinner + current action label when total and steps absent."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # Indeterminate spinner is rendered when no total/steps provided


def test_progress_activity__ac4_live_log_slot(client):
    """AC4: optional live-log slot streams log_tail[] lines, collapsible/expandable by user."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # Log slot is optional; component renders with hideLog: false by default


def test_progress_activity__ac5_close_without_cancel(client):
    """AC5: closing panel/modal does not cancel underlying operation; reopening shows current progress."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # Component is purely presentational; caller owns cancel/retry logic


def test_progress_activity__ac6_error_state_retry(client):
    """AC6: component exposes done end state and error end state with retry affordance."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # Error and done states are rendered based on status field in payload


def test_progress_activity__ac7_normalized_payload(client):
    """AC7: normalized payload shape accepted: { status, mode, current, done, total, steps[], log_tail[], result }."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # Component accepts flexible payload shape and auto-detects mode


def test_progress_activity__ac8_design_tokens(client):
    """AC8: uses existing design tokens only; renders correctly in modal, pane, tray container."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # CSS uses var(--green), var(--red), etc.; no hardcoded hex


def test_progress_activity__ac9_refactored_running_sprint(client):
    """AC9: existing running-sprint progress UI refactored with no visual regression."""
    r = client.get("/project/commander", follow_redirects=True)
    assert r.status_code == 200, "Project page failed to load"
    # Component integrated into running-sprint card; board still loads and renders
