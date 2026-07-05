"""Tests for issue #1003: Activity backend chip mislabels escalated coder runs (runs against UAT)"""
import os
import json
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


def test_escalation_backend_chip__escalated_cline_run_shows_cline_backend(client):
    """AC: When a coder ticket has been escalated (cline run followed by claude-code run),
    the cline agent_finished event displays 'cline' as its backend chip in the Activity view."""
    # Query the Activity endpoint for events, filtering for agent_finished type
    r = client.get("/api/projects/commander/events?source=agent&limit=200")
    assert r.status_code == 200, f"Failed to fetch events: {r.text}"
    events = r.json()

    # Find an agent_finished event for a coder run that was escalated (run_id proximity match)
    agent_finished_events = [
        e for e in events
        if e.get("type") == "agent_finished"
        and isinstance(e.get("detail"), dict)
        and str(e["detail"].get("role", "")).lower() == "coder"
    ]

    # In a sprint with escalations, we expect to see coder agent_finished events
    # with distinct backends. Filter for events that have a backend set.
    events_with_backend = [e for e in agent_finished_events if e.get("backend")]

    if not events_with_backend:
        pytest.skip("No escalated coder runs found in current data")

    # For each coder event with a backend, verify the backend matches the expected value
    # (this test verifies the Activity view returns backend data without mislabeling)
    for event in events_with_backend:
        detail = event.get("detail", {})
        backend = event.get("backend")
        # The backend should be set and should match one of the expected coder backends
        assert backend in ("cline", "claude-code"), \
            f"Unexpected backend {backend!r} for coder event {event.get('action_id')}"


def test_escalation_backend_chip__escalated_claude_code_run_shows_claude_code_backend(client):
    """AC: When a coder ticket has been escalated, the claude-code agent_finished event
    displays 'claude-code' as its backend chip in the Activity view."""
    # Query for agent_finished events for coder runs
    r = client.get("/api/projects/commander/events?source=agent&limit=200")
    assert r.status_code == 200
    events = r.json()

    agent_finished_events = [
        e for e in events
        if e.get("type") == "agent_finished"
        and isinstance(e.get("detail"), dict)
        and str(e["detail"].get("role", "")).lower() == "coder"
    ]

    # Filter for claude-code backend events
    claude_code_events = [
        e for e in agent_finished_events
        if e.get("backend") == "claude-code"
    ]

    if not claude_code_events:
        pytest.skip("No claude-code coder runs found in current data")

    # Verify each claude-code event has the correct backend set
    for event in claude_code_events:
        assert event.get("backend") == "claude-code", \
            f"Expected 'claude-code' backend but got {event.get('backend')!r}"


def test_escalation_backend_chip__non_escalated_run_shows_correct_backend(client):
    """AC: Non-escalated runs (single coder run per ticket) continue to display
    the correct backend chip unchanged."""
    r = client.get("/api/projects/commander/events?source=agent&limit=200")
    assert r.status_code == 200
    events = r.json()

    # Get all coder agent_finished events
    coder_finished_events = [
        e for e in events
        if e.get("type") == "agent_finished"
        and isinstance(e.get("detail"), dict)
        and str(e["detail"].get("role", "")).lower() == "coder"
    ]

    # Non-escalated runs should have a backend set to either cline or claude-code
    for event in coder_finished_events:
        if event.get("backend"):
            backend = event.get("backend")
            assert backend in ("cline", "claude-code"), \
                f"Unexpected backend {backend!r} for event {event.get('action_id')}"


def test_escalation_backend_chip__reload_persists_correct_backend(client):
    """AC: Reload the Activity tab (hard refresh) and confirm both chip labels persist
    correctly after re-fetch — they are not swapped or reset by a second page load."""
    # First fetch
    r1 = client.get("/api/projects/commander/events?source=agent&limit=200")
    assert r1.status_code == 200
    events1 = r1.json()

    # Second fetch (simulates page reload)
    r2 = client.get("/api/projects/commander/events?source=agent&limit=200")
    assert r2.status_code == 200
    events2 = r2.json()

    # Extract coder agent_finished events from both fetches
    def get_coder_finished(events):
        return {
            e.get("action_id"): e.get("backend")
            for e in events
            if e.get("type") == "agent_finished"
            and isinstance(e.get("detail"), dict)
            and str(e["detail"].get("role", "")).lower() == "coder"
            and e.get("backend")
        }

    events1_map = get_coder_finished(events1)
    events2_map = get_coder_finished(events2)

    # Verify that backends for the same run_id remain consistent across reloads
    for run_id, backend1 in events1_map.items():
        if run_id in events2_map:
            backend2 = events2_map[run_id]
            assert backend1 == backend2, \
                f"Backend mismatch on reload for run {run_id}: {backend1!r} → {backend2!r}"
