"""Tests for issue #1853: Logs: group Activity feed by sprint run (runs against UAT)"""
import os
import subprocess
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL", "http://localhost:8001")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_logs_group_activity_feed__ac_grouping_logic(client):
    """
    AC1-AC4: Grouping logic is implemented in evlGroupEventsByRun() and tested
    via Node.js behavioral tests in activity-grouping.test.mjs.
    This test verifies the Node.js suite passes.
    """
    import pathlib
    repo_root = pathlib.Path(__file__).parent.parent
    result = subprocess.run(
        ["node", "--test", "tests/frontend/activity-grouping.test.mjs"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Node.js tests failed. Output:\n{result.stdout}\n{result.stderr}"
    )


def test_logs_group_activity_feed__api_events_accessible(client):
    """AC1-AC4: Events API is accessible and returns properly structured data."""
    r = client.get("/api/projects/commander/events?limit=100")
    assert r.status_code == 200
    events = r.json()
    assert isinstance(events, list)
    if len(events) > 0:
        e = events[0]
        assert "timestamp" in e
        assert "source" in e
        assert "type" in e
