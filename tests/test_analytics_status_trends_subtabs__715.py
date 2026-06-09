"""Tests for issue #715: Add Status and Trends sub-tabs to Analytics (runs against UAT)

This is a frontend-only ticket (apps/dashboard/static/project.html). Almost every
acceptance criterion is DOM/JS behavior — tab switching, lazy-load network counts,
Chart.js rendering, console-error checks, mobile layout — none of which is reachable
over the HTTP API. Those criteria are marked manual via pytest.skip.

The one server-backed dependency is the Trends data source, `/api/metrics/sprints`,
which is exercised for real below.
"""
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

def test_715__subtab_bar_order_calibration_metrics_status_trends():
    # AC: Analytics sub-tab bar reads exactly Calibration | Metrics | Status | Trends
    pytest.skip("manual — DOM order of sub-tab buttons, cannot be HTTP-tested")


def test_715__status_renders_in_anl_card_panel():
    # AC: anlShowTab('status') renders pane-status/statusInit inside an anl-card panel
    pytest.skip("manual — DOM render / class application, cannot be HTTP-tested")


def test_715__status_removed_from_top_nav_dropdown():
    # AC: Status entry removed from the Analytics dropdown group in the top nav
    pytest.skip("manual — DOM nav contents, cannot be HTTP-tested")


def test_715__trends_renders_three_charts():
    # AC: anlShowTab('trends') renders Velocity / Failure / Duration charts in anl-card wrappers
    pytest.skip("manual — Chart.js canvas render, cannot be HTTP-tested")


def test_715__trends_data_source_metrics_sprints(client):
    # AC: Trends charts fetch data from /api/metrics/sprints (lazy on first open).
    # The lazy-load timing is DOM/JS (manual); the data source itself is HTTP-testable.
    r = client.get("/api/metrics/sprints")
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    data = r.json()
    assert isinstance(data, list), "metrics/sprints must return a JSON list of sprints"
    if data:
        row = data[0]
        # Fields the three Trends charts consume: velocity (done), failure rate,
        # and dispatch duration.
        assert "ticket_outcomes_breakdown" in row, "velocity/failure chart needs ticket_outcomes_breakdown"
        assert "duration_minutes" in row, "duration chart needs duration_minutes"
        assert "agent_dispatch_counts" in row, "duration chart needs agent_dispatch_counts"


def test_715__status_lazy_load_first_open_only():
    # AC: Status sub-tab calls statusInit() on first open only (subsequent switches no re-fetch)
    pytest.skip("manual — lazy-load network count over tab switches, cannot be HTTP-tested")


def test_715__four_subtabs_visually_consistent():
    # AC: All four sub-tabs share card chrome, spacing, heading style
    pytest.skip("manual — visual consistency, cannot be HTTP-tested")


def test_715__switching_subtabs_no_reload_or_reset():
    # AC: Switching between sub-tabs does not reload the page or reset loaded tabs
    pytest.skip("manual — SPA tab-switch behavior, cannot be HTTP-tested")


def test_715__no_console_errors_on_tab_switch():
    # AC: No console errors on tab switch in Chrome/Firefox
    pytest.skip("manual — browser console, cannot be HTTP-tested")


def test_715__calibration_and_metrics_unaffected():
    # AC: Existing Calibration and Metrics sub-tabs are unaffected
    pytest.skip("manual — DOM/JS behavior of existing panels, cannot be HTTP-tested")
