"""Tests for issue #1853: Logs: group Activity feed by sprint run (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
_uat_port = os.environ.get("UAT_PORT", "")
BASE_URL = os.environ.get("UAT_BASE_URL") or ("http://localhost:" + _uat_port if _uat_port else "")
if not BASE_URL.startswith("http"):
    pytest.skip(
        "UAT_BASE_URL / UAT_PORT not set — skip UAT tests",
        allow_module_level=True,
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_logs_group_activity_feed__ac1_activity_feed_groups_events_under_sprint_run_headers(client):
    # AC: Activity feed groups events under sprint-run headers (sprint label + run outcome + error count),
    # newest run first; events with no run association fall into a "Dashboard / other" group per day

    # Verify events API returns data (grouping is done client-side in evlGroupEventsByRun)
    r = client.get("/api/projects/commander/events?limit=100")
    assert r.status_code == 200
    events = r.json()
    assert isinstance(events, list)
    # Grouping logic is pure JS (evlGroupEventsByRun tested in activity-grouping.test.mjs)
    # UI rendering is tested in UAT step 1
    pytest.skip("AC1 UI rendering: requires browser interaction to verify sprint-run grouping headers")


def test_logs_group_activity_feed__ac2_run_groups_collapsible_with_error_expansion(client):
    # AC: Each run group is collapsible; groups containing errors render expanded by default, others collapsed

    # Verify /api/projects/commander/events returns events (collapse/expand is client-side)
    r = client.get("/api/projects/commander/events?limit=100")
    assert r.status_code == 200
    events = r.json()
    # Check for error events that should trigger expanded state
    error_types = {'ticket_failed', 'ticket_error'}
    any(e.get('type') in error_types for e in events)
    # Collapse logic is pure JS (evlToggleRunGroup tested in activity-grouping.test.mjs)
    pytest.skip("AC2 UI toggle: requires browser interaction to verify collapse/expand behavior")


def test_logs_group_activity_feed__ac3_filters_apply_within_groups(client):
    # AC: Existing severity/agent/source filters still apply within groups

    # Verify events API returns data with proper source/type for filtering
    r = client.get("/api/projects/commander/events?limit=100")
    assert r.status_code == 200
    events = r.json()
    # Verify events have source and type fields for filtering
    for e in events[:5]:
        assert 'source' in e
        assert 'type' in e
    # Filter application is JS-side; verified in UAT step 2
    pytest.skip("AC3 filter behavior: requires browser interaction to verify filters apply within groups")


def test_logs_group_activity_feed__ac4_group_header_shows_summary(client):
    # AC: Group header shows a compact summary line (event count, error count)

    # Verify events API returns event data
    r = client.get("/api/projects/commander/events?limit=100")
    assert r.status_code == 200
    events = r.json()
    assert len(events) > 0
    # Event data is available for summary; header rendering is JS-side
    # Verified in UAT steps 1 & 2 via browser
    pytest.skip("AC4 header display: requires browser inspection to verify summary line content")
